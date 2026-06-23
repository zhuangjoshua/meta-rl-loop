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

from .ledger_gate import gate_fetchone


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


def _gate_cell(row, index: int):
    """Read a SECURITY DEFINER gate-result column by position, tolerating tuple and dict row
    factories (``select * from func(...)`` expands the composite columns positionally)."""
    if isinstance(row, Mapping):
        return list(row.values())[index]
    return row[index]


# The migration-0038 SECURITY DEFINER functions (safebox_credits_*) are the only sanctioned writers
# of the creative-credit ledger once the runtime runs as the demoted `takyon_runtime` role. The
# mutating ops below invoke those functions under the restricted role (ledger_gate); the function
# bodies are a verbatim port of the former in-Python row ops (FOR UPDATE locks, idempotency,
# balance math). Result composite columns, in order:
#   0 refusal   1 fig_requested_credits   2 fig_available_credits   3 business_slug
#   4 balance_credits   5 reserved_credits   6 reserved_credits_out


def _raise_for_credits_refusal(row, *, reservation_key: str | None = None) -> None:
    """If the gate result carries a refusal, raise the matching typed exception; else return."""
    refusal = _gate_cell(row, 0)
    if refusal is None:
        return
    if refusal == "insufficient_credits":
        raise InsufficientCreativeCredits(
            requested_credits=int(_gate_cell(row, 1)),
            available_credits=int(_gate_cell(row, 2)),
        )
    if refusal == "unknown_reservation":
        raise UnknownCreativeCreditReservation(reservation_key)
    raise BusinessCreditsError(f"unexpected gate refusal: {refusal!r}")  # pragma: no cover - defensive


def _balances_from_gate(row) -> "CreativeCreditBalances":
    """Build CreativeCreditBalances from a non-refusal gate result (cols 3/4/5)."""
    return CreativeCreditBalances(
        business_slug=str(_gate_cell(row, 3)),
        balance_credits=int(_gate_cell(row, 4)),
        reserved_credits=int(_gate_cell(row, 5)),
    )


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


# The former in-Python account open+lock helper (_ensure_account_locked) moved into the migration-0038
# SECURITY DEFINER functions (safebox_credits_*); the mutating ops now reach the ledger only through
# those functions under the restricted runtime role.


def open_business_credit_account(conn, business_slug: str) -> None:
    """Idempotently open the business creative-credit account at zero."""
    gate_fetchone(
        conn,
        "select safebox_credits_open_account(%s)",
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
    stripe_reference = str(stripe_ref or "").strip() or None
    # Row ops in the migration-0038 SECURITY DEFINER function safebox_credits_grant (verbatim port):
    # open+lock the account, idempotent on stripe_ref (per-business 'grant') AND idempotency_key
    # (replay returns current balances), else credit the pack and write the 'grant' entry.
    row = gate_fetchone(
        conn,
        "select * from safebox_credits_grant(%s, %s, %s, %s::jsonb, %s)",
        (business_slug, credits, key, _json_dumps(metadata or {}), stripe_reference),
    )
    return _balances_from_gate(row)


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
    # Row ops in the migration-0038 SECURITY DEFINER function safebox_credits_reserve (verbatim
    # port): open+lock the account, idempotent reservation_key short circuit (replay returns the
    # same held amount), InsufficientCreativeCredits refusal (nothing written), else hold the credits.
    row = gate_fetchone(
        conn,
        "select * from safebox_credits_reserve(%s, %s, %s, %s::jsonb)",
        (business_slug, credits, key, _json_dumps(metadata or {})),
    )
    _raise_for_credits_refusal(row, reservation_key=key)
    return CreativeCreditReservation(key=key, reserved_credits=int(_gate_cell(row, 6)))


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
    # Pre-check the actual-credits preconditions in Python (ValueErrors, not ledger refusals) so the
    # spend>=0 + spend<=reserved invariants are enforced BEFORE the gate writes — exactly as before.
    # The reserve-amount read is a pure SELECT (still permitted under the demoted role). Unknown
    # reservation is decided inside the gate function, but checking here keeps the ValueError ordering
    # for a present reservation with a bad actual_credits.
    if actual_credits is not None:
        reserve_row = conn.execute(
            "select amount_credits from business_creative_credit_entries "
            "where reservation_key = %s and kind = 'reserve'",
            (key,),
        ).fetchone()
        if reserve_row is not None:
            reserved = int(_row_get(reserve_row, "amount_credits", 0))
            spent = int(actual_credits)
            if spent < 0:
                raise ValueError("actual_credits must be >= 0")
            if spent > reserved:
                raise ValueError(f"actual credits {spent} exceed reserved {reserved}")
    # Row ops in the migration-0038 SECURITY DEFINER function safebox_credits_commit (verbatim port):
    # lock the reserve + account rows, UnknownCreativeCreditReservation when absent, prior
    # commit/release → no-op returning current balances, else refund reserved−actual back to balance.
    row = gate_fetchone(
        conn,
        "select * from safebox_credits_commit(%s, %s, %s::jsonb)",
        (key, actual_credits, _json_dumps(metadata or {})),
    )
    _raise_for_credits_refusal(row, reservation_key=key)
    return _balances_from_gate(row)


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
    # Row ops in the migration-0038 SECURITY DEFINER function safebox_credits_release (verbatim
    # port): lock the reserve + account rows, UnknownCreativeCreditReservation when absent, prior
    # commit/release → no-op, else return the full reservation to balance.
    row = gate_fetchone(
        conn,
        "select * from safebox_credits_release(%s, %s::jsonb)",
        (key, _json_dumps(metadata or {})),
    )
    _raise_for_credits_refusal(row, reservation_key=key)
    return _balances_from_gate(row)


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
