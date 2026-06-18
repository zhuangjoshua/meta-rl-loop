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

from dataclasses import dataclass


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


def open_billing_account(conn, user_id: str, *, allowance_included_cents: int = 0) -> None:
    """Open the user's single billing account. Idempotent and transaction-free so it
    composes inside the provisioning transaction. Default allowance is 0 — the free /
    plan grant amount is a policy decision applied later via `grant_allowance`, not a
    number invented here."""
    conn.execute(
        "insert into billing_accounts (user_id, allowance_included_cents) "
        "values (%s, %s) on conflict (user_id) do nothing",
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
    with conn.transaction():
        acct = conn.execute(
            "select allowance_included_cents from billing_accounts "
            "where user_id = %s for update",
            (user_id,),
        ).fetchone()
        if acct is None:
            raise NoBillingAccount(user_id)
        if _entry_exists(conn, idempotency_key):
            return int(acct[0])
        conn.execute(
            "update billing_accounts set allowance_included_cents = %s, "
            "allowance_used_cents = 0, allowance_period_start = coalesce(%s, now()), "
            "allowance_resets_at = %s, updated_at = now() where user_id = %s",
            (included_cents, period_start, resets_at, user_id),
        )
        conn.execute(
            "insert into billing_entries (user_id, bucket, kind, amount_cents, "
            "balance_after_cents, idempotency_key) "
            "values (%s, 'allowance', 'grant', %s, 0, %s)",
            (user_id, included_cents, idempotency_key),
        )
    return included_cents


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
    with conn.transaction():
        acct = conn.execute(
            "select allowance_included_cents, allowance_used_cents "
            "from billing_accounts where user_id = %s for update",
            (user_id,),
        ).fetchone()
        if acct is None:
            raise NoBillingAccount(user_id)
        included, used = int(acct[0]), int(acct[1])
        existing = conn.execute(
            "select bucket, amount_cents from billing_entries "
            "where reservation_key = %s and kind = 'reserve'",
            (rk,),
        ).fetchall()
        if existing:
            a = sum(int(r[1]) for r in existing if r[0] == "allowance")
            return Reservation(key=rk, allowance_cents=a)
        avail_allow = max(0, included - used)
        if estimate_cents > avail_allow:
            raise InsufficientBalance(
                estimate_cents=estimate_cents,
                allowance_available_cents=avail_allow,
            )
        new_used = used + estimate_cents
        conn.execute(
            "update billing_accounts set allowance_used_cents = %s, "
            "updated_at = now() where user_id = %s",
            (new_used, user_id),
        )
        # Allowance entry: always written (even as a zero anchor for a zero-estimate
        # reservation) so the reservation_key exists and replays idempotently.
        _insert_entry(
            conn, user_id, business_slug, "allowance", "reserve",
            estimate_cents, new_used, rk, job_id, f"{rk}:reserve:allowance",
        )
        return Reservation(key=rk, allowance_cents=estimate_cents)


def settle(conn, reservation_key: str, actual_cents: int) -> None:
    """Finalize a reservation at `actual_cents` (≤ reserved): record the real spend
    and release the unused remainder. Idempotent — a second settle/refund on the same
    reservation is a no-op (first finalizer wins)."""
    if actual_cents < 0:
        raise ValueError("actual_cents must be >= 0")
    rk = reservation_key
    with conn.transaction():
        resv = conn.execute(
            "select user_id, bucket, amount_cents from billing_entries "
            "where reservation_key = %s and kind = 'reserve'",
            (rk,),
        ).fetchall()
        if not resv:
            raise UnknownReservation(rk)
        user_id = str(resv[0][0])
        a_resv = sum(int(r[2]) for r in resv if r[1] == "allowance")
        if actual_cents > a_resv:
            raise ValueError(f"actual {actual_cents} exceeds reserved {a_resv}")
        acct = conn.execute(
            "select allowance_used_cents from billing_accounts "
            "where user_id = %s for update",
            (user_id,),
        ).fetchone()
        if _finalized(conn, rk):
            return
        used = int(acct[0])
        # Record the actual spend; release the unused remainder of the reservation.
        s_alloc = actual_cents
        r_alloc = a_resv - s_alloc
        new_used = used - r_alloc
        conn.execute(
            "update billing_accounts set allowance_used_cents = %s, "
            "updated_at = now() where user_id = %s",
            (new_used, user_id),
        )
        # settle does not move balances (it reclassifies held → spent), so its
        # balance_after is the pre-release value; refund carries the released value.
        if s_alloc > 0:
            _insert_entry(conn, user_id, None, "allowance", "settle",
                          s_alloc, used, rk, None, f"{rk}:settle:allowance")
        if r_alloc > 0:
            _insert_entry(conn, user_id, None, "allowance", "refund",
                          r_alloc, new_used, rk, None, f"{rk}:refund:allowance")


def refund(conn, reservation_key: str) -> None:
    """Release a whole reservation (the failure path). Idempotent — a no-op if the
    reservation was already settled or refunded."""
    rk = reservation_key
    with conn.transaction():
        resv = conn.execute(
            "select user_id, bucket, amount_cents from billing_entries "
            "where reservation_key = %s and kind = 'reserve'",
            (rk,),
        ).fetchall()
        if not resv:
            raise UnknownReservation(rk)
        user_id = str(resv[0][0])
        a_resv = sum(int(r[2]) for r in resv if r[1] == "allowance")
        acct = conn.execute(
            "select allowance_used_cents from billing_accounts "
            "where user_id = %s for update",
            (user_id,),
        ).fetchone()
        if _finalized(conn, rk):
            return
        used = int(acct[0])
        new_used = used - a_resv
        conn.execute(
            "update billing_accounts set allowance_used_cents = %s, "
            "updated_at = now() where user_id = %s",
            (new_used, user_id),
        )
        if a_resv > 0:
            _insert_entry(conn, user_id, None, "allowance", "refund",
                          a_resv, new_used, rk, None, f"{rk}:refund:allowance")


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


def _entry_exists(conn, idempotency_key: str) -> bool:
    return (
        conn.execute(
            "select 1 from billing_entries where idempotency_key = %s",
            (idempotency_key,),
        ).fetchone()
        is not None
    )


def _finalized(conn, reservation_key: str) -> bool:
    return (
        conn.execute(
            "select 1 from billing_entries where reservation_key = %s "
            "and kind in ('settle', 'refund') limit 1",
            (reservation_key,),
        ).fetchone()
        is not None
    )


def _insert_entry(
    conn, user_id, business_slug, bucket, kind, amount, balance_after,
    reservation_key, job_id, idempotency_key,
) -> None:
    conn.execute(
        "insert into billing_entries (user_id, business_slug, bucket, kind, "
        "amount_cents, balance_after_cents, reservation_key, job_id, idempotency_key) "
        "values (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
        (user_id, business_slug, bucket, kind, amount, balance_after,
         reservation_key, job_id, idempotency_key),
    )
