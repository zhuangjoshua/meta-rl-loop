"""Billing ledger — flow A (user → platform), the compute-spend side of the money
model in mediationplan.md.

One bucket per user — allowance: the operator's plan-funded usage budget, an opaque
metering unit. Stored in cents for accounting only; it is NEVER money and must never
be surfaced as dollars. (The à-la-carte "topup" overflow bucket was removed 2026-06-18,
operator decision: operator funding comes solely from the subscription allowance.)

Costly work is wrapped in reserve → settle/refund:
  reserve(estimate) holds allowance; on success settle(actual) records the real spend
  and releases (estimate − actual); on failure refund() releases the whole reservation.
  Allowance not covering the estimate raises InsufficientBalance (the caller maps that
  to a 402 / blocked job).

Concurrency + idempotency rest on ONE invariant: every mutating op takes
`SELECT … FOR UPDATE` on the single billing_accounts row BEFORE it checks for a
prior effect or writes anything. That row lock serializes all of a user's ledger
writes, so parallel reserves can never oversell and a replayed idempotency key can
never double-charge. Cached balances are always re-derivable from the append-only
billing_entries; `reconcile_billing` proves it.

Backend-agnostic: takes a psycopg connection, opens its own `conn.transaction()`
per op (works whether the connection is autocommit or not), imports no psycopg.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from .ledger_gate import gate_fetchone


def _cell(row, index: int):
    """Read a gate-result column by position, tolerating both tuple and dict row factories (the
    store lends a tuple_row connection to leaves, but some callers run under dict_row)."""
    if isinstance(row, Mapping):
        return list(row.values())[index]
    return row[index]


class BillingError(Exception):
    """Base for billing-ledger errors."""


class NoBillingAccount(BillingError):
    """No billing_accounts row for this user (provisioning should have opened one)."""


class UnknownReservation(BillingError):
    """settle/refund referenced a reservation_key that was never reserved."""


class InsufficientBalance(BillingError):
    """The allowance cannot cover the estimate. Carries the exact figures so the caller
    can build a precise 402 without leaking anything else."""

    def __init__(
        self,
        *,
        estimate_cents: int,
        allowance_available_cents: int,
    ) -> None:
        self.estimate_cents = estimate_cents
        self.allowance_available_cents = allowance_available_cents
        super().__init__(
            f"insufficient_balance: need {estimate_cents}, "
            f"allowance {allowance_available_cents}"
        )


@dataclass(frozen=True)
class Reservation:
    """The outcome of a reserve: the allowance held. `key` is the reservation_key
    threaded into settle/refund."""

    key: str
    allowance_cents: int

    @property
    def total_cents(self) -> int:
        return self.allowance_cents


@dataclass(frozen=True)
class BillingBalances:
    user_id: str
    allowance_included_cents: int
    allowance_used_cents: int
    allowance_remaining_cents: int
    reserved_cents: int
    allowance_period_start: object | None = None
    allowance_resets_at: object | None = None


# The mutating ops route their writes through the migration-0038 SECURITY DEFINER functions
# (safebox_billing_*), which are the ONLY sanctioned writers of the billing ledger once the runtime
# runs as the demoted `takyon_runtime` role. Each runs under the restricted role (ledger_gate) so a
# forged direct write is DENIED at the DB; the function still executes its row ops with the owner's
# privileges, so the money math (FOR UPDATE locks, idempotency, balance arithmetic) is unchanged.
# The result composite columns, in order:
#   0 refusal   1 fig_estimate_cents   2 fig_allowance_available_cents   3 allowance_cents
#   4 included_cents


def _raise_for_billing_refusal(row, *, user_id: str | None = None, reservation_key: str | None = None):
    """If the gate result carries a refusal, raise the matching typed exception; else return."""
    refusal = _cell(row, 0)
    if refusal is None:
        return
    if refusal == "no_billing_account":
        raise NoBillingAccount(user_id)
    if refusal == "unknown_reservation":
        raise UnknownReservation(reservation_key)
    if refusal == "insufficient_balance":
        raise InsufficientBalance(
            estimate_cents=int(_cell(row, 1)),
            allowance_available_cents=int(_cell(row, 2)),
        )
    raise BillingError(f"unexpected gate refusal: {refusal!r}")  # pragma: no cover - defensive


def open_billing_account(conn, user_id: str, *, allowance_included_cents: int = 0) -> None:
    """Open the user's single billing account. Idempotent and transaction-free so it
    composes inside the provisioning transaction. Default allowance is 0 — the free /
    plan grant amount is a policy decision applied later via `grant_allowance`, not a
    number invented here."""
    gate_fetchone(
        conn,
        "select safebox_billing_open_account(%s, %s)",
        (user_id, allowance_included_cents),
    )


def grant_allowance(
    conn,
    user_id: str,
    included_cents: int,
    idempotency_key: str,
    *,
    period_start=None,
    resets_at=None,
) -> int:
    """Set the period's included allowance and reset used to 0 (a fresh metering
    period). Assumes no allowance reservations are outstanding across the reset.
    Idempotent on `idempotency_key`. Returns the included amount in effect."""
    if included_cents < 0:
        raise ValueError("included_cents must be >= 0")
    # Row ops in the migration-0038 SECURITY DEFINER function safebox_billing_grant_allowance
    # (verbatim port): lock the account row, NoBillingAccount when absent, idempotent on
    # idempotency_key (replay returns the current included amount), else set the period included +
    # reset used to 0 and write the 'grant' entry.
    row = gate_fetchone(
        conn,
        "select * from safebox_billing_grant_allowance(%s, %s, %s, %s, %s)",
        (user_id, included_cents, idempotency_key, period_start, resets_at),
    )
    _raise_for_billing_refusal(row, user_id=user_id)
    return int(_cell(row, 4))  # included_cents in effect


def reserve(
    conn,
    user_id: str,
    estimate_cents: int,
    idempotency_key: str,
    *,
    business_slug: str | None = None,
    job_id: str | None = None,
) -> Reservation:
    """Hold `estimate_cents` against the user's allowance. `idempotency_key` becomes the
    reservation_key; a replay returns the SAME reservation without moving balances. Raises
    InsufficientBalance if the allowance cannot cover the estimate (no entries written)."""
    if estimate_cents < 0:
        raise ValueError("estimate_cents must be >= 0")
    rk = idempotency_key
    # Row ops in the migration-0038 SECURITY DEFINER function safebox_billing_reserve (verbatim
    # port): lock the account row, NoBillingAccount when absent, idempotent reservation_key short
    # circuit (replay returns the same held allowance), InsufficientBalance refusal (nothing
    # written), else hold the estimate and write the 'reserve' entry (always, even a zero anchor).
    row = gate_fetchone(
        conn,
        "select * from safebox_billing_reserve(%s, %s, %s, %s, %s)",
        (user_id, estimate_cents, rk, business_slug, job_id),
    )
    _raise_for_billing_refusal(row, user_id=user_id)
    return Reservation(key=rk, allowance_cents=int(_cell(row, 3)))  # allowance_cents held


def settle(conn, reservation_key: str, actual_cents: int) -> None:
    """Finalize a reservation at `actual_cents` (≤ reserved): record the real spend
    and release the unused remainder. Idempotent — a second settle/refund on the same
    reservation is a no-op (first finalizer wins)."""
    if actual_cents < 0:
        raise ValueError("actual_cents must be >= 0")
    rk = reservation_key
    # Pre-check the actual<=reserved invariant in Python (a ValueError, not a ledger refusal) so the
    # custody-of-real-money guarantee is enforced BEFORE the gate writes — exactly as before. The
    # reserved-amount read is a pure SELECT (still permitted under the demoted role).
    resv = conn.execute(
        "select bucket, amount_cents from billing_entries "
        "where reservation_key = %s and kind = 'reserve'",
        (rk,),
    ).fetchall()
    if not resv:
        raise UnknownReservation(rk)
    a_resv = sum(int(_cell(r, 1)) for r in resv if _cell(r, 0) == "allowance")
    if actual_cents > a_resv:
        raise ValueError(f"actual {actual_cents} exceeds reserved {a_resv}")
    # Row ops in the migration-0038 SECURITY DEFINER function safebox_billing_settle (verbatim port):
    # re-look up the reserve, lock the account row, already-finalized → no-op (first finalizer wins),
    # else record the actual ('settle') and release the unused remainder ('refund').
    row = gate_fetchone(
        conn,
        "select * from safebox_billing_settle(%s, %s)",
        (rk, actual_cents),
    )
    _raise_for_billing_refusal(row, reservation_key=rk)


def refund(conn, reservation_key: str) -> None:
    """Release a whole reservation (the failure path). Idempotent — a no-op if the
    reservation was already settled or refunded."""
    rk = reservation_key
    # Row ops in the migration-0038 SECURITY DEFINER function safebox_billing_refund (verbatim port):
    # look up the reserve (UnknownReservation when absent), lock the account row, already-finalized →
    # no-op, else release the whole held reservation back to the allowance.
    row = gate_fetchone(
        conn,
        "select * from safebox_billing_refund(%s)",
        (rk,),
    )
    _raise_for_billing_refusal(row, reservation_key=rk)


def get_billing_balances(conn, user_id: str) -> BillingBalances:
    """Read the cached balances plus derived allowance remaining and outstanding
    reserved (Σreserve − Σsettle − Σrefund). Allowance figures are metering units —
    callers must not render them as money."""
    acct = conn.execute(
        "select allowance_included_cents, allowance_used_cents, "
        "allowance_period_start, allowance_resets_at "
        "from billing_accounts where user_id = %s",
        (user_id,),
    ).fetchone()
    if acct is None:
        raise NoBillingAccount(user_id)
    included, used, period_start, resets_at = (
        int(acct[0]), int(acct[1]), acct[2], acct[3],
    )
    reserved = conn.execute(
        "select coalesce(sum(amount_cents) filter (where kind = 'reserve'), 0) "
        "- coalesce(sum(amount_cents) filter (where kind = 'settle'), 0) "
        "- coalesce(sum(amount_cents) filter (where kind = 'refund'), 0) "
        "from billing_entries where user_id = %s",
        (user_id,),
    ).fetchone()[0]
    return BillingBalances(
        user_id=user_id,
        allowance_included_cents=included,
        allowance_used_cents=used,
        allowance_remaining_cents=included - used,
        reserved_cents=int(reserved),
        allowance_period_start=period_start,
        allowance_resets_at=resets_at,
    )


def reconcile_billing(conn, user_id: str) -> dict:
    """Prove the cached balances equal the append-only ledger and no reservation went
    negative. Returns a dict with `ok` and, on drift, the mismatched figures. Allowance
    is reconciled within the current period (since allowance_period_start)."""
    acct = conn.execute(
        "select allowance_included_cents, allowance_used_cents, "
        "allowance_period_start from billing_accounts where user_id = %s",
        (user_id,),
    ).fetchone()
    if acct is None:
        raise NoBillingAccount(user_id)
    included, used, period_start = (
        int(acct[0]), int(acct[1]), acct[2],
    )
    sums = conn.execute(
        "select "
        " coalesce(sum(amount_cents) filter (where bucket='allowance' and kind='reserve' and (%(ps)s::timestamptz is null or created_at >= %(ps)s::timestamptz)), 0),"
        " coalesce(sum(amount_cents) filter (where bucket='allowance' and kind='settle'  and (%(ps)s::timestamptz is null or created_at >= %(ps)s::timestamptz)), 0),"
        " coalesce(sum(amount_cents) filter (where bucket='allowance' and kind='refund'  and (%(ps)s::timestamptz is null or created_at >= %(ps)s::timestamptz)), 0)"
        " from billing_entries where user_id = %(uid)s",
        {"ps": period_start, "uid": user_id},
    ).fetchone()
    a_res, a_set, a_ref = (int(x) for x in sums)
    calc_used = a_res - a_ref
    reserved_allowance = a_res - a_set - a_ref
    drift = {}
    if calc_used != used:
        drift["allowance_used"] = {"cached": used, "ledger": calc_used}
    if reserved_allowance < 0:
        drift["reserved_allowance_negative"] = reserved_allowance
    if used > included:
        drift["allowance_oversold"] = {"used": used, "included": included}
    return {
        "ok": not drift,
        "drift": drift,
        "reserved_allowance_cents": reserved_allowance,
        "reserved_cents": reserved_allowance,
    }

# The former in-Python row-op helpers (_entry_exists / _finalized / _insert_entry) moved into the
# migration-0038 SECURITY DEFINER functions (safebox_billing_*), which are now the only sanctioned
# writers of the billing ledger. The Python ops above invoke those functions under the restricted
# runtime role (ledger_gate); they no longer issue direct INSERT/UPDATE on billing_accounts /
# billing_entries.
