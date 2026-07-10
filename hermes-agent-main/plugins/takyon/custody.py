"""Custody ledger — flow B (sub-users → user, held by the platform), the payout side
of the money model in mediationplan.md.

A business's customers (sub-users) pay on the shared platform Stripe. The platform
keeps an application fee (`STRIPE_CONNECT_APPLICATION_FEE_BPS`, default 2000 bps =
20%) and accrues the rest as money OWED to the business owner. That owed balance is
a ledger fact from day one — accrual does NOT depend on the owner having connected
Stripe Connect yet. When the owner connects and withdraws, `payout` drains the owed
balance. This is the user's money in custody; it is NEVER netted against the billing
ledger (flow A).

Same robustness contract as billing.py: every mutating op takes
`SELECT … FOR UPDATE` on the single custody_accounts row before checking for a prior
effect or writing, so concurrent accruals can't lose updates and a replayed
idempotency key can't double-credit. `net_cents` on each entry is the SIGNED effect
on owed_balance, so owed_balance == Σ net_cents exactly; `reconcile_custody` proves it.

Backend-agnostic: takes a psycopg connection, imports no psycopg.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass

from .ledger_gate import gate_fetchone

_DEFAULT_APP_FEE_BPS = 2000  # 20%, matches secrets/.env default


def _remote_safebox_enabled() -> bool:
    """True on runtime planes that must not perform custody mint/payout operations locally."""
    try:
        from . import safebox

        return safebox._remote_enabled() and not safebox._local_authority_enabled()
    except Exception:
        return False


def _cell(row, index: int):
    """Read a gate-result column by position, tolerating both tuple and dict row factories."""
    if isinstance(row, Mapping):
        return list(row.values())[index]
    return row[index]


class CustodyError(Exception):
    """Base for custody-ledger errors."""


class NoCustodyAccount(CustodyError):
    """No custody_accounts row for this user (provisioning should have opened one)."""


class InsufficientCustody(CustodyError):
    """Payout requested more than the owed balance."""

    def __init__(self, *, requested_cents: int, owed_cents: int) -> None:
        self.requested_cents = requested_cents
        self.owed_cents = owed_cents
        super().__init__(
            f"insufficient_custody: requested {requested_cents}, owed {owed_cents}"
        )


class CustodyClawbackPending(CustodyError):
    """Payouts are blocked until refunded/disputed customer money is recovered."""

    def __init__(self, *, pending_cents: int) -> None:
        self.pending_cents = pending_cents
        super().__init__(f"custody_clawback_pending: {pending_cents} cents")


class CustodyClawbackNotFound(CustodyError):
    """The requested release does not identify this owner's business clawback."""


@dataclass(frozen=True)
class CustodyBalances:
    user_id: str
    owed_balance_cents: int
    paid_out_cents: int
    currency: str


def app_fee_bps() -> int:
    """The platform application fee in basis points, from
    STRIPE_CONNECT_APPLICATION_FEE_BPS. Defaults to 2000 (20%); clamped to [0, 10000]
    so a misconfigured value can never invert a payment or exceed the gross."""
    raw = os.environ.get("STRIPE_CONNECT_APPLICATION_FEE_BPS")
    if raw is None or raw.strip() == "":
        return _DEFAULT_APP_FEE_BPS
    try:
        bps = int(raw)
    except ValueError:
        return _DEFAULT_APP_FEE_BPS
    return max(0, min(10000, bps))


# The mutating ops route their writes through the migration-0038 SECURITY DEFINER functions
# (safebox_custody_*), the only sanctioned writers of the custody ledger from live request paths. The
# shared ledger gate requires a Safebox authority DB login; runtime planes do not demote into a
# money-writing role. The function still executes its row ops with the owner's privileges, so the money
# math is unchanged. Result columns:
#   0 refusal   1 fig_requested_cents   2 fig_owed_cents   3 new_owed


def _raise_for_custody_refusal(row, *, user_id: str | None = None) -> None:
    """If the gate result carries a refusal, raise the matching typed exception; else return."""
    refusal = _cell(row, 0)
    if refusal is None:
        return
    if refusal == "no_custody_account":
        raise NoCustodyAccount(user_id)
    if refusal == "insufficient_custody":
        raise InsufficientCustody(
            requested_cents=int(_cell(row, 1)),
            owed_cents=int(_cell(row, 2)),
        )
    if refusal == "custody_clawback_pending":
        raise CustodyClawbackPending(pending_cents=int(_cell(row, 2)))
    raise CustodyError(f"unexpected gate refusal: {refusal!r}")  # pragma: no cover - defensive


def open_custody_account(conn, user_id: str, *, currency: str = "usd") -> None:
    """Open the user's single custody account. Idempotent and transaction-free so it
    composes inside the provisioning transaction."""
    if _remote_safebox_enabled():
        from . import safebox

        safebox.open_custody_account(conn, user_id, currency=currency)
        return
    conn.execute("select safebox_custody_open_account(%s, %s)", (user_id, currency))


def accrue(
    conn,
    user_id: str,
    business_slug: str,
    gross_cents: int,
    idempotency_key: str,
    *,
    stripe_ref: str | None = None,
    fee_bps: int | None = None,
    withheld_cents: int = 0,
    metadata: dict | None = None,
) -> int:
    """Record a sub-user payment: take the app fee off `gross_cents` and accrue the
    net to the owner's owed balance. Works regardless of Connect status. Idempotent on
    `idempotency_key` (a Stripe event id → replayed webhook accrues once). Returns the
    new owed balance.

    Fee is floored, so net = gross − floor(gross · bps / 10000) ≥ 0 and the platform
    never over-takes a sub-cent rounding.
    """
    if gross_cents <= 0:
        raise ValueError("gross_cents must be > 0")
    if _remote_safebox_enabled():
        from . import safebox

        raise safebox.SafeboxAuthorityUnavailable(
            "custody.accrue is a mint operation; process the signed app-payment webhook "
            "on the safebox instead of passing caller-supplied amounts"
        )
    bps = app_fee_bps() if fee_bps is None else max(0, min(10000, fee_bps))
    fee = (gross_cents * bps) // 10000
    withheld = max(0, int(withheld_cents or 0))
    if withheld > max(0, gross_cents - fee):
        raise ValueError("withheld_cents must not exceed gross minus platform fee")
    net = gross_cents - fee - withheld
    entry_metadata = dict(metadata or {})
    if withheld:
        entry_metadata["withheld_cents"] = withheld
    # Row ops in the migration-0038 SECURITY DEFINER function safebox_custody_accrue (verbatim port):
    # lock the account, NoCustodyAccount when absent, idempotent on idempotency_key (replay returns
    # owed), else accrue net to owed + write the 'accrual' entry. Fee/net policy math stays above.
    row = conn.execute(
        "select * from safebox_custody_accrue(%s, %s, %s, %s, %s, %s, %s, %s::jsonb)",
        (
            user_id,
            business_slug,
            gross_cents,
            fee,
            net,
            idempotency_key,
            stripe_ref,
            json.dumps(entry_metadata or {}, ensure_ascii=False, sort_keys=True),
        ),
    ).fetchone()
    _raise_for_custody_refusal(row, user_id=user_id)
    return int(_cell(row, 3))  # new_owed


def payout(
    conn,
    user_id: str,
    amount_cents: int,
    idempotency_key: str,
    *,
    stripe_ref: str | None = None,
) -> int:
    """Withdraw `amount_cents` from the owed balance (the Connect transfer the owner
    pulls). Raises InsufficientCustody if it exceeds owed. Idempotent on
    `idempotency_key`. Returns the new owed balance. The Connect-connected gate lives
    at the API layer; the ledger only guards owed ≥ amount."""
    if amount_cents <= 0:
        raise ValueError("amount_cents must be > 0")
    if _remote_safebox_enabled():
        from . import safebox

        raise safebox.SafeboxAuthorityUnavailable(
            "custody.payout is an operator-authorized safebox operation; use the payout route "
            "instead of a caller-supplied amount"
        )
    # Row ops in the migration-0038 SECURITY DEFINER function safebox_custody_payout (verbatim port):
    # lock the account, NoCustodyAccount when absent, idempotent on idempotency_key (replay returns
    # owed), InsufficientCustody when amount>owed (nothing written), else drain owed + bump paid_out.
    row = conn.execute(
        "select * from safebox_custody_payout(%s, %s, %s, %s)",
        (user_id, amount_cents, idempotency_key, stripe_ref),
    ).fetchone()
    _raise_for_custody_refusal(row, user_id=user_id)
    return int(_cell(row, 3))  # new_owed


def clawback(
    conn,
    user_id: str,
    business_slug: str,
    amount_cents: int,
    idempotency_key: str,
    *,
    stripe_ref: str | None = None,
    metadata: dict | None = None,
) -> dict[str, int | bool]:
    """Debit refunded/disputed customer money and durably track any unpaid shortfall."""
    if amount_cents <= 0:
        raise ValueError("amount_cents must be > 0")
    if _remote_safebox_enabled():
        from . import safebox

        raise safebox.SafeboxAuthorityUnavailable(
            "custody clawback must be derived from a signed app-payment webhook on the safebox"
        )
    row = conn.execute(
        "select * from safebox_custody_clawback(%s, %s, %s, %s, %s, %s::jsonb)",
        (
            user_id,
            business_slug,
            int(amount_cents),
            idempotency_key,
            stripe_ref,
            json.dumps(metadata or {}, ensure_ascii=False, sort_keys=True),
        ),
    ).fetchone()
    _raise_for_custody_refusal(row, user_id=user_id)
    return {
        "applied_cents": int(_cell(row, 1) or 0),
        "shortfall_cents": int(_cell(row, 2) or 0),
        "owed_balance_cents": int(_cell(row, 3) or 0),
        "replayed": bool(_cell(row, 4)),
    }


def release_clawback(
    conn,
    user_id: str,
    business_slug: str,
    clawback_idempotency_key: str,
    release_idempotency_key: str,
    *,
    stripe_ref: str | None = None,
    metadata: dict | None = None,
) -> dict[str, int | bool]:
    """Release one won-dispute clawback without restoring its unfunded shortfall."""
    if not str(clawback_idempotency_key or "").strip():
        raise ValueError("clawback_idempotency_key is required")
    if not str(release_idempotency_key or "").strip():
        raise ValueError("release_idempotency_key is required")
    if _remote_safebox_enabled():
        from . import safebox

        raise safebox.SafeboxAuthorityUnavailable(
            "custody clawback release must be derived from a signed dispute webhook on the safebox"
        )
    row = conn.execute(
        "select * from safebox_custody_release_clawback(%s, %s, %s, %s, %s, %s::jsonb)",
        (
            user_id,
            business_slug,
            clawback_idempotency_key,
            release_idempotency_key,
            stripe_ref,
            json.dumps(metadata or {}, ensure_ascii=False, sort_keys=True),
        ),
    ).fetchone()
    refusal = _cell(row, 0)
    if refusal == "no_custody_account":
        raise NoCustodyAccount(user_id)
    if refusal == "custody_clawback_not_found":
        raise CustodyClawbackNotFound(clawback_idempotency_key)
    if refusal is not None:
        raise CustodyError(f"unexpected gate refusal: {refusal!r}")
    return {
        "credited_cents": int(_cell(row, 1) or 0),
        "owed_balance_cents": int(_cell(row, 2) or 0),
        "replayed": bool(_cell(row, 3)),
    }


def get_custody_balances(conn, user_id: str) -> CustodyBalances:
    acct = conn.execute(
        "select owed_balance_cents, paid_out_cents, currency from custody_accounts "
        "where user_id = %s",
        (user_id,),
    ).fetchone()
    if acct is None:
        raise NoCustodyAccount(user_id)
    return CustodyBalances(
        user_id=user_id,
        owed_balance_cents=int(acct[0]),
        paid_out_cents=int(acct[1]),
        currency=acct[2],
    )


def reconcile_custody(conn, user_id: str) -> dict:
    """Prove owed_balance == Σ net_cents and paid_out == Σ payout gross, with neither
    negative. Returns a dict with `ok` and any drift."""
    acct = conn.execute(
        "select owed_balance_cents, paid_out_cents from custody_accounts "
        "where user_id = %s",
        (user_id,),
    ).fetchone()
    if acct is None:
        raise NoCustodyAccount(user_id)
    owed, paid_out = int(acct[0]), int(acct[1])
    sums = conn.execute(
        "select coalesce(sum(net_cents), 0), "
        "coalesce(sum(gross_cents) filter (where kind = 'payout'), 0) "
        "from custody_entries where user_id = %s",
        (user_id,),
    ).fetchone()
    calc_owed, calc_paid = int(sums[0]), int(sums[1])
    drift = {}
    if calc_owed != owed:
        drift["owed_balance"] = {"cached": owed, "ledger": calc_owed}
    if calc_paid != paid_out:
        drift["paid_out"] = {"cached": paid_out, "ledger": calc_paid}
    if owed < 0:
        drift["owed_negative"] = owed
    if paid_out < 0:
        drift["paid_out_negative"] = paid_out
    return {"ok": not drift, "drift": drift}

# The former in-Python idempotency helper (_entry_exists) moved into the migration-0038 SECURITY
# DEFINER functions (safebox_custody_*), now the only sanctioned writers of the custody ledger.
