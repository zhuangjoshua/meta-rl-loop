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

import os
from dataclasses import dataclass

_DEFAULT_APP_FEE_BPS = 2000  # 20%, matches secrets/.env default


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


def open_custody_account(conn, user_id: str, *, currency: str = "usd") -> None:
    """Open the user's single custody account. Idempotent and transaction-free so it
    composes inside the provisioning transaction."""
    conn.execute(
        "insert into custody_accounts (user_id, currency) values (%s, %s) "
        "on conflict (user_id) do nothing",
        (user_id, currency),
    )


def accrue(
    conn,
    user_id: str,
    business_slug: str,
    gross_cents: int,
    idempotency_key: str,
    *,
    stripe_ref: str | None = None,
    fee_bps: int | None = None,
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
    bps = app_fee_bps() if fee_bps is None else max(0, min(10000, fee_bps))
    fee = (gross_cents * bps) // 10000
    net = gross_cents - fee
    with conn.transaction():
        acct = conn.execute(
            "select owed_balance_cents from custody_accounts "
            "where user_id = %s for update",
            (user_id,),
        ).fetchone()
        if acct is None:
            raise NoCustodyAccount(user_id)
        if _entry_exists(conn, idempotency_key):
            return int(acct[0])
        new_owed = int(acct[0]) + net
        conn.execute(
            "update custody_accounts set owed_balance_cents = %s, updated_at = now() "
            "where user_id = %s",
            (new_owed, user_id),
        )
        conn.execute(
            "insert into custody_entries (user_id, business_slug, kind, gross_cents, "
            "fee_cents, net_cents, stripe_ref, idempotency_key) "
            "values (%s, %s, 'accrual', %s, %s, %s, %s, %s)",
            (user_id, business_slug, gross_cents, fee, net, stripe_ref, idempotency_key),
        )
    return new_owed


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
    with conn.transaction():
        acct = conn.execute(
            "select owed_balance_cents, paid_out_cents from custody_accounts "
            "where user_id = %s for update",
            (user_id,),
        ).fetchone()
        if acct is None:
            raise NoCustodyAccount(user_id)
        owed, paid_out = int(acct[0]), int(acct[1])
        if _entry_exists(conn, idempotency_key):
            return owed
        if amount_cents > owed:
            raise InsufficientCustody(requested_cents=amount_cents, owed_cents=owed)
        new_owed = owed - amount_cents
        new_paid = paid_out + amount_cents
        conn.execute(
            "update custody_accounts set owed_balance_cents = %s, paid_out_cents = %s, "
            "updated_at = now() where user_id = %s",
            (new_owed, new_paid, user_id),
        )
        conn.execute(
            "insert into custody_entries (user_id, kind, gross_cents, fee_cents, "
            "net_cents, stripe_ref, idempotency_key) "
            "values (%s, 'payout', %s, 0, %s, %s, %s)",
            (user_id, amount_cents, -amount_cents, stripe_ref, idempotency_key),
        )
    return new_owed


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


def _entry_exists(conn, idempotency_key: str) -> bool:
    return (
        conn.execute(
            "select 1 from custody_entries where idempotency_key = %s",
            (idempotency_key,),
        ).fetchone()
        is not None
    )
