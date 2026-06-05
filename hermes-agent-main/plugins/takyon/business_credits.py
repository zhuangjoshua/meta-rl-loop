"""Business creative-credit ledger — a fixed-credit rail for future paid creative/ad actions.

This is deliberately distinct from:
  * ``billing.py`` — the Takyon user's own money with the platform, and
  * ``app_usage.py`` — per-business product AI usage metered in microUSD.

Creative credits are a BUSINESS-scoped operator-facing product layer: grant packs, reserve credits
before a spendful creative action, commit on success, release on failure. The account row caches
the currently available balance; outstanding reservations are derived from the append-only ledger.
Concurrency rests on the same house invariant as the other ledgers: every mutating operation locks
the single account row ``for update`` before checking prior effects or writing entries.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass


class BusinessCreditsError(Exception):
    """Base for business creative-credit ledger errors."""


class InsufficientCreativeCredits(BusinessCreditsError):
    """A reserve would overdraw the business credit balance."""

    def __init__(self, *, requested_credits: int, available_credits: int) -> None:
        self.requested_credits = requested_credits
        self.available_credits = available_credits
        super().__init__(
            f"insufficient_creative_credits: need {requested_credits}, have {available_credits}"
        )


class UnknownCreativeCreditReservation(BusinessCreditsError):
    """commit/release referenced a reservation key that does not exist."""


@dataclass(frozen=True)
class CreativeCreditBalances:
    business_slug: str
    balance_credits: int
    reserved_credits: int


@dataclass(frozen=True)
class CreativeCreditReservation:
    key: str
    reserved_credits: int


@dataclass(frozen=True)
class CreativeCreditEntry:
    id: int
    business_slug: str
    kind: str
    amount_credits: int
    balance_after_credits: int
    reservation_key: str | None
    idempotency_key: str
    metadata: dict
    stripe_ref: str | None
    created_at: object


_ACCOUNT_COLUMNS = "business_slug, balance_credits"
_ENTRY_COLUMNS = (
    "id, business_slug, kind, amount_credits, balance_after_credits, reservation_key, "
    "idempotency_key, metadata, stripe_ref, created_at"
)


def _json_dumps(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _row_get(row, key: str, index: int):
    if isinstance(row, Mapping):
        return row[key]
    return row[index]


def _entry_from_row(row) -> CreativeCreditEntry:
    return CreativeCreditEntry(
        id=int(_row_get(row, "id", 0)),
        business_slug=str(_row_get(row, "business_slug", 1)),
        kind=str(_row_get(row, "kind", 2)),
        amount_credits=int(_row_get(row, "amount_credits", 3)),
        balance_after_credits=int(_row_get(row, "balance_after_credits", 4)),
        reservation_key=(
            None
            if _row_get(row, "reservation_key", 5) is None
            else str(_row_get(row, "reservation_key", 5))
        ),
        idempotency_key=str(_row_get(row, "idempotency_key", 6)),
        metadata=_row_get(row, "metadata", 7) if isinstance(_row_get(row, "metadata", 7), dict) else {},
        stripe_ref=(
            None
            if _row_get(row, "stripe_ref", 8) is None
            else str(_row_get(row, "stripe_ref", 8))
        ),
        created_at=_row_get(row, "created_at", 9),
    )


def _reserved_credits(conn, business_slug: str) -> int:
    row = conn.execute(
        """
        select coalesce(sum(r.amount_credits), 0) as reserved_credits
        from business_creative_credit_entries r
        left join business_creative_credit_entries f
          on f.reservation_key = r.reservation_key
         and f.kind in ('commit', 'release')
        where r.business_slug = %s
          and r.kind = 'reserve'
          and f.id is null
        """,
        (business_slug,),
    ).fetchone()
    return int(_row_get(row, "reserved_credits", 0) or 0)


def _ensure_account_locked(conn, business_slug: str) -> tuple[str, int]:
    conn.execute(
        """
        insert into business_creative_credit_accounts (business_slug)
        values (%s)
        on conflict (business_slug) do nothing
        """,
        (business_slug,),
    )
    row = conn.execute(
        f"""
        select {_ACCOUNT_COLUMNS}
        from business_creative_credit_accounts
        where business_slug = %s
        for update
        """,
        (business_slug,),
    ).fetchone()
    return str(_row_get(row, "business_slug", 0)), int(_row_get(row, "balance_credits", 1))


def open_business_credit_account(conn, business_slug: str) -> None:
    """Idempotently open the business creative-credit account at zero."""
    conn.execute(
        """
        insert into business_creative_credit_accounts (business_slug)
        values (%s)
        on conflict (business_slug) do nothing
        """,
        (business_slug,),
    )


def get_business_credit_balances(conn, business_slug: str) -> CreativeCreditBalances:
    """Read the current available/reserved credit balances. Missing account -> zero balance."""
    row = conn.execute(
        f"""
        select {_ACCOUNT_COLUMNS}
        from business_creative_credit_accounts
        where business_slug = %s
        """,
        (business_slug,),
    ).fetchone()
    if row is None:
        return CreativeCreditBalances(
            business_slug=business_slug,
            balance_credits=0,
            reserved_credits=0,
        )
    return CreativeCreditBalances(
        business_slug=str(_row_get(row, "business_slug", 0)),
        balance_credits=int(_row_get(row, "balance_credits", 1)),
        reserved_credits=_reserved_credits(conn, business_slug),
    )


def grant_credits(
    conn,
    business_slug: str,
    credits: int,
    idempotency_key: str,
    *,
    metadata: dict | None = None,
    stripe_ref: str | None = None,
) -> CreativeCreditBalances:
    """Credit a purchased/granted pack to the business. Idempotent on ``idempotency_key``."""
    if credits <= 0:
        raise ValueError("credits must be > 0")
    key = str(idempotency_key or "").strip()
    if not key:
        raise ValueError("idempotency_key is required")
    with conn.transaction():
        business_slug, balance = _ensure_account_locked(conn, business_slug)
        prior = conn.execute(
            "select 1 from business_creative_credit_entries where idempotency_key = %s",
            (key,),
        ).fetchone()
        if prior is not None:
            return CreativeCreditBalances(
                business_slug=business_slug,
                balance_credits=balance,
                reserved_credits=_reserved_credits(conn, business_slug),
            )
        new_balance = balance + credits
        conn.execute(
            """
            update business_creative_credit_accounts
            set balance_credits = %s, updated_at = now()
            where business_slug = %s
            """,
            (new_balance, business_slug),
        )
        conn.execute(
            """
            insert into business_creative_credit_entries (
              business_slug, kind, amount_credits, balance_after_credits,
              idempotency_key, metadata, stripe_ref
            )
            values (%s, 'grant', %s, %s, %s, %s::jsonb, %s)
            """,
            (
                business_slug,
                credits,
                new_balance,
                key,
                _json_dumps(metadata or {}),
                stripe_ref,
            ),
        )
        return CreativeCreditBalances(
            business_slug=business_slug,
            balance_credits=new_balance,
            reserved_credits=_reserved_credits(conn, business_slug),
        )


def reserve_credits(
    conn,
    business_slug: str,
    credits: int,
    reservation_key: str,
    *,
    metadata: dict | None = None,
) -> CreativeCreditReservation:
    """Hold credits for a future creative action. Idempotent on ``reservation_key``."""
    if credits <= 0:
        raise ValueError("credits must be > 0")
    key = str(reservation_key or "").strip()
    if not key:
        raise ValueError("reservation_key is required")
    with conn.transaction():
        business_slug, balance = _ensure_account_locked(conn, business_slug)
        existing = conn.execute(
            f"""
            select {_ENTRY_COLUMNS}
            from business_creative_credit_entries
            where reservation_key = %s
              and kind = 'reserve'
            """,
            (key,),
        ).fetchone()
        if existing is not None:
            entry = _entry_from_row(existing)
            return CreativeCreditReservation(key=key, reserved_credits=entry.amount_credits)
        if credits > balance:
            raise InsufficientCreativeCredits(
                requested_credits=credits,
                available_credits=balance,
            )
        new_balance = balance - credits
        conn.execute(
            """
            update business_creative_credit_accounts
            set balance_credits = %s, updated_at = now()
            where business_slug = %s
            """,
            (new_balance, business_slug),
        )
        conn.execute(
            """
            insert into business_creative_credit_entries (
              business_slug, kind, amount_credits, balance_after_credits,
              reservation_key, idempotency_key, metadata
            )
            values (%s, 'reserve', %s, %s, %s, %s, %s::jsonb)
            """,
            (
                business_slug,
                credits,
                new_balance,
                key,
                key,
                _json_dumps(metadata or {}),
            ),
        )
        return CreativeCreditReservation(key=key, reserved_credits=credits)


def commit_credits(
    conn,
    reservation_key: str,
    *,
    actual_credits: int | None = None,
    metadata: dict | None = None,
) -> CreativeCreditBalances:
    """Finalize a reservation, optionally refunding ``reserved - actual`` back to balance."""
    key = str(reservation_key or "").strip()
    if not key:
        raise ValueError("reservation_key is required")
    with conn.transaction():
        reserve_row = conn.execute(
            f"""
            select {_ENTRY_COLUMNS}
            from business_creative_credit_entries
            where reservation_key = %s
              and kind = 'reserve'
            for update
            """,
            (key,),
        ).fetchone()
        if reserve_row is None:
            raise UnknownCreativeCreditReservation(key)
        reserve = _entry_from_row(reserve_row)
        business_slug, balance = _ensure_account_locked(conn, reserve.business_slug)
        prior = conn.execute(
            f"""
            select {_ENTRY_COLUMNS}
            from business_creative_credit_entries
            where reservation_key = %s
              and kind in ('commit', 'release')
            order by id asc
            limit 1
            """,
            (key,),
        ).fetchone()
        if prior is not None:
            return CreativeCreditBalances(
                business_slug=business_slug,
                balance_credits=balance,
                reserved_credits=_reserved_credits(conn, business_slug),
            )
        spent = reserve.amount_credits if actual_credits is None else int(actual_credits)
        if spent < 0:
            raise ValueError("actual_credits must be >= 0")
        if spent > reserve.amount_credits:
            raise ValueError(
                f"actual credits {spent} exceed reserved {reserve.amount_credits}"
            )
        refund = reserve.amount_credits - spent
        new_balance = balance + refund
        conn.execute(
            """
            update business_creative_credit_accounts
            set balance_credits = %s, updated_at = now()
            where business_slug = %s
            """,
            (new_balance, business_slug),
        )
        conn.execute(
            """
            insert into business_creative_credit_entries (
              business_slug, kind, amount_credits, balance_after_credits,
              reservation_key, idempotency_key, metadata
            )
            values (%s, 'commit', %s, %s, %s, %s, %s::jsonb)
            """,
            (
                business_slug,
                spent,
                new_balance,
                key,
                f"{key}:commit",
                _json_dumps(metadata or {}),
            ),
        )
        return CreativeCreditBalances(
            business_slug=business_slug,
            balance_credits=new_balance,
            reserved_credits=_reserved_credits(conn, business_slug),
        )


def release_credits(
    conn,
    reservation_key: str,
    *,
    metadata: dict | None = None,
) -> CreativeCreditBalances:
    """Free a reservation without spending its credits."""
    key = str(reservation_key or "").strip()
    if not key:
        raise ValueError("reservation_key is required")
    with conn.transaction():
        reserve_row = conn.execute(
            f"""
            select {_ENTRY_COLUMNS}
            from business_creative_credit_entries
            where reservation_key = %s
              and kind = 'reserve'
            for update
            """,
            (key,),
        ).fetchone()
        if reserve_row is None:
            raise UnknownCreativeCreditReservation(key)
        reserve = _entry_from_row(reserve_row)
        business_slug, balance = _ensure_account_locked(conn, reserve.business_slug)
        prior = conn.execute(
            f"""
            select {_ENTRY_COLUMNS}
            from business_creative_credit_entries
            where reservation_key = %s
              and kind in ('commit', 'release')
            order by id asc
            limit 1
            """,
            (key,),
        ).fetchone()
        if prior is not None:
            return CreativeCreditBalances(
                business_slug=business_slug,
                balance_credits=balance,
                reserved_credits=_reserved_credits(conn, business_slug),
            )
        new_balance = balance + reserve.amount_credits
        conn.execute(
            """
            update business_creative_credit_accounts
            set balance_credits = %s, updated_at = now()
            where business_slug = %s
            """,
            (new_balance, business_slug),
        )
        conn.execute(
            """
            insert into business_creative_credit_entries (
              business_slug, kind, amount_credits, balance_after_credits,
              reservation_key, idempotency_key, metadata
            )
            values (%s, 'release', %s, %s, %s, %s, %s::jsonb)
            """,
            (
                business_slug,
                reserve.amount_credits,
                new_balance,
                key,
                f"{key}:release",
                _json_dumps(metadata or {}),
            ),
        )
        return CreativeCreditBalances(
            business_slug=business_slug,
            balance_credits=new_balance,
            reserved_credits=_reserved_credits(conn, business_slug),
        )


def list_credit_entries(conn, business_slug: str) -> list[CreativeCreditEntry]:
    rows = conn.execute(
        f"""
        select {_ENTRY_COLUMNS}
        from business_creative_credit_entries
        where business_slug = %s
        order by id asc
        """,
        (business_slug,),
    ).fetchall()
    return [_entry_from_row(row) for row in rows]
