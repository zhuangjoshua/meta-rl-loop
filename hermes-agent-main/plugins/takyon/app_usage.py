"""Product AI-spend budget cap + usage ledger — Phase 5 (increment c) of mediationplan.md.

Builds on `app_identity` (the sub-user spine). One concern, scoped by `business_slug`: cap
what a business's PRODUCT is allowed to spend on AI on behalf of its sub-users, and record
every spend. This is distinct from the Takyon operator's own money in `billing.py` (0002):
that is the user→platform ledger; this is the per-business product COMPUTE budget.

THE ONE GATE — reserve-then-settle. The SQLite trunk gated product spend on TWO
uncoordinated paths, and both are wrong under load:
  1. an estimate PRE-CHECK (app_api.py:379) that read a rendered budget mirror and compared
     estimate>remaining but RESERVED NOTHING — pure read-then-act, so N concurrent /generate
     calls all saw the same remaining and all proceeded (overspend); and
  2. an actuals RE-SUM at insert time (core.py:5362) that summed actual_cost only and raised
     if it would exceed the cap — but it fired AFTER the provider was already called and paid,
     so tripping it meant refusing to RECORD spend that already happened (the ledger then
     under-counts real cost — a money-truth violation, mediationplan invariant #8).
This module collapses both into billing.py's Phase-3 pattern applied to the product budget:
  * reserve(estimate) holds the estimate atomically under the budget row lock — the ONE gate
    that can refuse spend (AppBudgetExceeded). Committed spend = Σ(estimate of still-`reserved`
    rows) + Σ(actual of `completed` rows) within the period; failed/released rows count zero.
  * settle(actual) records the real provider spend and NEVER re-checks the cap — once money is
    spent, recording the truth is mandatory.
  * release() frees the hold on the failure path (no spend recorded).
Deliberate divergence from billing.py: settle records the true actual even if it slightly
exceeds the reserved estimate. billing.py asserts actual≤reserved because it is custody of the
user's real money; here the estimate is only a pre-flight gate and the provider's actual is the
truth — capping it would reintroduce the very under-count this increment removes. The cap is
enforced at reserve.

Postgres port of the SQLite trunk's app_budgets / app_usage_events (core.py:3026-3034,
3203-3224, gate at 5349-5398); the SQLite product path is the predecessor, retired in Phase 8.

House style (matches billing.py / custody.py / policy.py / app_identity.py / app_entitlements):
pure leaf, takes a psycopg connection, imports no psycopg, opens its own `conn.transaction()`
per mutating op, and raises typed errors on broken preconditions. An unknown business fails loud
through the FK to businesses(slug). Concurrency rests on ONE invariant mirroring billing.py:
every reserve takes `select … for update` on the single app_budgets row BEFORE computing
committed spend or inserting, so parallel reserves can never oversell and a replayed
reservation_key can never double-charge.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

_DEFAULT_HARD_LIMIT_MICROUSD = 5_000_000


class AppUsageError(Exception):
    """Base for product usage/budget errors."""


class AppBudgetInactive(AppUsageError):
    """The business budget exists but its status is not 'active', so spend is refused."""

    def __init__(self, business_slug: str, status: str) -> None:
        self.business_slug = business_slug
        self.status = status
        super().__init__(f"app budget for {business_slug} is {status!r}, not active")


class AppBudgetExceeded(AppUsageError):
    """A reserve would push committed spend past the hard cap. Carries the exact figures
    so the caller can build a precise 402 without leaking anything else."""

    def __init__(
        self, *, hard_limit_microusd: int, committed_microusd: int, requested_microusd: int
    ) -> None:
        self.hard_limit_microusd = hard_limit_microusd
        self.committed_microusd = committed_microusd
        self.requested_microusd = requested_microusd
        self.remaining_microusd = max(0, hard_limit_microusd - committed_microusd)
        super().__init__(
            f"app usage would exceed budget cap: need {requested_microusd}, "
            f"committed {committed_microusd} of {hard_limit_microusd}"
        )


class UnknownReservation(AppUsageError):
    """settle/release referenced a reservation_key that was never reserved in this business."""


class AppUserNotFound(AppUsageError):
    """The referenced sub-user does not exist in this business."""


@dataclass(frozen=True)
class AppBudget:
    """One business's product AI-spend cap for the current metering period."""

    business_slug: str
    status: str
    hard_limit_microusd: int
    current_period_start: object
    current_period_end: object


@dataclass(frozen=True)
class UsageEvent:
    """One spend record. status lifecycle: reserved → completed | failed | released. While
    `reserved` the estimate is held; once `completed` the actual is the recorded spend."""

    id: str
    business_slug: str
    app_user_id: str | None
    app_user_tier: str | None
    reservation_key: str
    purpose: str
    route: str
    status: str
    estimated_cost_microusd: int
    actual_cost_microusd: int
    input_tokens: int | None
    output_tokens: int | None
    provider_request_id: str | None
    provider: str | None
    model: str | None
    error: str | None
    metadata: dict
    created_at: object
    completed_at: object


_FINALIZED_STATUSES = ("completed", "failed", "released")

_BUDGET_COLUMNS = (
    "business_slug, status, hard_limit_microusd, current_period_start, current_period_end"
)
_EVENT_COLUMNS = (
    "id, business_slug, app_user_id, app_user_tier, reservation_key, purpose, route, "
    "status, estimated_cost_microusd, actual_cost_microusd, input_tokens, output_tokens, "
    "provider_request_id, provider, model, error, metadata, created_at, completed_at"
)


def _json_dumps(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _budget_from_row(row) -> AppBudget:
    return AppBudget(
        business_slug=str(row[0]),
        status=str(row[1]),
        hard_limit_microusd=int(row[2]),
        current_period_start=row[3],
        current_period_end=row[4],
    )


def _event_from_row(row) -> UsageEvent:
    return UsageEvent(
        id=str(row[0]),
        business_slug=str(row[1]),
        app_user_id=None if row[2] is None else str(row[2]),
        app_user_tier=None if row[3] is None else str(row[3]),
        reservation_key=str(row[4]),
        purpose=str(row[5]),
        route=str(row[6]),
        status=str(row[7]),
        estimated_cost_microusd=int(row[8]),
        actual_cost_microusd=int(row[9]),
        input_tokens=None if row[10] is None else int(row[10]),
        output_tokens=None if row[11] is None else int(row[11]),
        provider_request_id=None if row[12] is None else str(row[12]),
        provider=None if row[13] is None else str(row[13]),
        model=None if row[14] is None else str(row[14]),
        error=None if row[15] is None else str(row[15]),
        metadata=row[16] if isinstance(row[16], dict) else {},
        created_at=row[17],
        completed_at=row[18],
    )


def _require_app_user(conn, business_slug: str, app_user_id: str) -> None:
    """Verify the sub-user belongs to THIS business (the FK alone would allow a cross-business
    id). Mirrors the SQLite guard at core.py:5351."""
    row = conn.execute(
        "select 1 from app_users where business_slug = %s and id = %s",
        (business_slug, app_user_id),
    ).fetchone()
    if row is None:
        raise AppUserNotFound(app_user_id)


def _ensure_budget_locked(conn, business_slug: str) -> AppBudget:
    """Open the business budget at the default cap if absent, then lock its row `for update`.
    Must be called inside a transaction. The lock serializes all concurrent reserves for the
    business so the committed-spend aggregate is consistent. Unknown business →
    ForeignKeyViolation (fail loud)."""
    conn.execute(
        "insert into app_budgets (business_slug) values (%s) on conflict (business_slug) do nothing",
        (business_slug,),
    )
    row = conn.execute(
        f"select {_BUDGET_COLUMNS} from app_budgets where business_slug = %s for update",
        (business_slug,),
    ).fetchone()
    return _budget_from_row(row)


def _committed_microusd(conn, business_slug: str, period_start) -> int:
    """Committed spend within the period: held estimates of `reserved` rows + recorded actuals
    of `completed` rows. failed/released rows count zero. Caller must already hold the budget
    row lock so this reads a stable view."""
    row = conn.execute(
        "select coalesce(sum(case "
        " when status = 'reserved' then estimated_cost_microusd "
        " when status = 'completed' then actual_cost_microusd "
        " else 0 end), 0) "
        "from app_usage_events where business_slug = %s and created_at >= %s",
        (business_slug, period_start),
    ).fetchone()
    return int(row[0])


# ── budget catalog ───────────────────────────────────────────────────────────────


def ensure_app_budget(conn, business_slug: str) -> AppBudget:
    """Open the business budget at the default cap if absent (idempotent), returning the row in
    effect. Period is calendar-month UTC, fixed at creation (faithful to the SQLite trunk)."""
    with conn.transaction():
        return _ensure_budget_locked(conn, business_slug)


def set_app_budget(
    conn, business_slug: str, *, hard_limit_microusd: int, status: str = "active"
) -> AppBudget:
    """Set the hard cap (and optionally status) for a business, opening the row first if needed.
    Mirrors the SQLite `app.budget.set` op (core.py:5010). Unknown business →
    ForeignKeyViolation."""
    if hard_limit_microusd < 0:
        raise ValueError("hard_limit_microusd must be >= 0")
    if not str(status or "").strip():
        raise ValueError("status must be a non-empty string")
    with conn.transaction():
        _ensure_budget_locked(conn, business_slug)
        row = conn.execute(
            "update app_budgets set hard_limit_microusd = %s, status = %s, updated_at = now() "
            f"where business_slug = %s returning {_BUDGET_COLUMNS}",
            (hard_limit_microusd, status, business_slug),
        ).fetchone()
    return _budget_from_row(row)


def get_app_budget(conn, business_slug: str) -> AppBudget | None:
    """Read a business budget, or None if never opened. Pure read."""
    row = conn.execute(
        f"select {_BUDGET_COLUMNS} from app_budgets where business_slug = %s",
        (business_slug,),
    ).fetchone()
    return None if row is None else _budget_from_row(row)


def get_usage_summary(conn, business_slug: str) -> dict:
    """Authoritative budget/remaining read for the current period: the figures a pre-flight UI
    or check should use INSTEAD of the SQLite rendered-mirror read (`_app_budget_remaining_microusd`,
    app_api.py:176) that the broken estimate pre-check relied on. Pure read; the real gate is
    reserve_usage. Returns status/hard_limit/committed/remaining/period. If the budget was never
    opened, status is 'missing' and the cap is the default (what reserve would open it at)."""
    budget = get_app_budget(conn, business_slug)
    if budget is None:
        return {
            "status": "missing",
            "hard_limit_microusd": _DEFAULT_HARD_LIMIT_MICROUSD,
            "committed_microusd": 0,
            "remaining_microusd": _DEFAULT_HARD_LIMIT_MICROUSD,
            "current_period_start": None,
            "current_period_end": None,
        }
    committed = _committed_microusd(conn, business_slug, budget.current_period_start)
    return {
        "status": budget.status,
        "hard_limit_microusd": budget.hard_limit_microusd,
        "committed_microusd": committed,
        "remaining_microusd": max(0, budget.hard_limit_microusd - committed),
        "current_period_start": budget.current_period_start,
        "current_period_end": budget.current_period_end,
    }


# ── the gate: reserve → settle | release ───────────────────────────────────────────


def reserve_usage(
    conn,
    business_slug: str,
    *,
    estimated_cost_microusd: int,
    reservation_key: str,
    app_user_id: str | None = None,
    app_user_tier: str | None = None,
    purpose: str = "product_usage",
    route: str = "app",
    provider: str | None = None,
    model: str | None = None,
    metadata: dict | None = None,
) -> UsageEvent:
    """Hold `estimated_cost_microusd` against the business budget — THE gate. Atomic under the
    budget row lock: opens the budget if absent, refuses if status≠active (AppBudgetInactive) or
    if committed+estimate would exceed the cap (AppBudgetExceeded, nothing written). Idempotent on
    `reservation_key` (UNIQUE per business): a replay returns the SAME reserved row without
    holding twice. Returns the `reserved` UsageEvent; thread its reservation_key into settle/
    release."""
    if estimated_cost_microusd < 0:
        raise ValueError("estimated_cost_microusd must be >= 0")
    key = str(reservation_key or "").strip()
    if not key:
        raise ValueError("reservation_key is required")
    with conn.transaction():
        budget = _ensure_budget_locked(conn, business_slug)
        if budget.status != "active":
            raise AppBudgetInactive(business_slug, budget.status)
        existing = conn.execute(
            f"select {_EVENT_COLUMNS} from app_usage_events "
            "where business_slug = %s and reservation_key = %s",
            (business_slug, key),
        ).fetchone()
        if existing is not None:
            return _event_from_row(existing)
        if app_user_id is not None:
            _require_app_user(conn, business_slug, app_user_id)
        committed = _committed_microusd(conn, business_slug, budget.current_period_start)
        if committed + estimated_cost_microusd > budget.hard_limit_microusd:
            raise AppBudgetExceeded(
                hard_limit_microusd=budget.hard_limit_microusd,
                committed_microusd=committed,
                requested_microusd=estimated_cost_microusd,
            )
        row = conn.execute(
            "insert into app_usage_events "
            "(business_slug, app_user_id, app_user_tier, reservation_key, purpose, route, "
            " status, estimated_cost_microusd, provider, model, metadata) "
            "values (%s, %s, %s, %s, %s, %s, 'reserved', %s, %s, %s, %s::jsonb) "
            f"returning {_EVENT_COLUMNS}",
            (
                business_slug, app_user_id, app_user_tier, key, purpose, route,
                estimated_cost_microusd, provider, model, _json_dumps(metadata or {}),
            ),
        ).fetchone()
    return _event_from_row(row)


def settle_usage(
    conn,
    business_slug: str,
    reservation_key: str,
    *,
    actual_cost_microusd: int,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    provider_request_id: str | None = None,
    provider: str | None = None,
    model: str | None = None,
    metadata: dict | None = None,
) -> UsageEvent:
    """Finalize a reservation at the real provider spend: reserved → completed. Records
    `actual_cost_microusd` and NEVER re-checks the cap (money is already spent — truth is
    mandatory). Locks the event row, so two concurrent settles serialize and the first wins;
    a settle/release after finalization is a no-op returning the existing row (idempotent).
    Unknown reservation_key → UnknownReservation. provider/model/provider_request_id are
    COALESCE-preserved (a value set at reserve survives a None here); metadata is merged."""
    if actual_cost_microusd < 0:
        raise ValueError("actual_cost_microusd must be >= 0")
    key = str(reservation_key or "").strip()
    if not key:
        raise ValueError("reservation_key is required")
    with conn.transaction():
        existing = conn.execute(
            f"select {_EVENT_COLUMNS} from app_usage_events "
            "where business_slug = %s and reservation_key = %s for update",
            (business_slug, key),
        ).fetchone()
        if existing is None:
            raise UnknownReservation(key)
        event = _event_from_row(existing)
        if event.status in _FINALIZED_STATUSES:
            return event
        row = conn.execute(
            "update app_usage_events set "
            " status = 'completed', "
            " actual_cost_microusd = %s, "
            " input_tokens = coalesce(%s, input_tokens), "
            " output_tokens = coalesce(%s, output_tokens), "
            " provider_request_id = coalesce(%s, provider_request_id), "
            " provider = coalesce(%s, provider), "
            " model = coalesce(%s, model), "
            " metadata = metadata || coalesce(%s::jsonb, '{}'::jsonb), "
            " completed_at = now(), "
            " updated_at = now() "
            f"where business_slug = %s and reservation_key = %s returning {_EVENT_COLUMNS}",
            (
                actual_cost_microusd, input_tokens, output_tokens, provider_request_id,
                provider, model,
                None if metadata is None else _json_dumps(metadata),
                business_slug, key,
            ),
        ).fetchone()
    return _event_from_row(row)


def release_usage(
    conn,
    business_slug: str,
    reservation_key: str,
    *,
    error: str | None = None,
    metadata: dict | None = None,
) -> UsageEvent:
    """Free a reservation without recording spend (the failure path): reserved → failed (when an
    `error` is given) or released (a clean cancel). actual stays 0 so committed drops by the held
    estimate. Idempotent — a no-op returning the existing row if already finalized. Unknown
    reservation_key → UnknownReservation."""
    key = str(reservation_key or "").strip()
    if not key:
        raise ValueError("reservation_key is required")
    with conn.transaction():
        existing = conn.execute(
            f"select {_EVENT_COLUMNS} from app_usage_events "
            "where business_slug = %s and reservation_key = %s for update",
            (business_slug, key),
        ).fetchone()
        if existing is None:
            raise UnknownReservation(key)
        event = _event_from_row(existing)
        if event.status in _FINALIZED_STATUSES:
            return event
        new_status = "failed" if error else "released"
        row = conn.execute(
            "update app_usage_events set "
            " status = %s, "
            " actual_cost_microusd = 0, "
            " error = coalesce(%s, error), "
            " metadata = metadata || coalesce(%s::jsonb, '{}'::jsonb), "
            " completed_at = now(), "
            " updated_at = now() "
            f"where business_slug = %s and reservation_key = %s returning {_EVENT_COLUMNS}",
            (
                new_status, error,
                None if metadata is None else _json_dumps(metadata),
                business_slug, key,
            ),
        ).fetchone()
    return _event_from_row(row)


def record_completed_usage(
    conn,
    business_slug: str,
    *,
    actual_cost_microusd: int,
    reservation_key: str,
    estimated_cost_microusd: int | None = None,
    app_user_id: str | None = None,
    app_user_tier: str | None = None,
    purpose: str = "product_usage",
    route: str = "app",
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    provider_request_id: str | None = None,
    provider: str | None = None,
    model: str | None = None,
    metadata: dict | None = None,
) -> UsageEvent:
    """Record an already-completed spend in one shot — reserve+settle fused for the synchronous
    self-report path (the SQLite `/usage` route, app_api.py:339, where the cost is known and there
    is no provider round-trip to straddle). Goes through the SAME gate as reserve (committed +
    this amount must fit the cap), then writes a `completed` row directly. Idempotent on
    `reservation_key`. The gate amount is `estimated_cost_microusd` if given, else the actual."""
    if actual_cost_microusd < 0:
        raise ValueError("actual_cost_microusd must be >= 0")
    if estimated_cost_microusd is not None and estimated_cost_microusd < 0:
        raise ValueError("estimated_cost_microusd must be >= 0")
    key = str(reservation_key or "").strip()
    if not key:
        raise ValueError("reservation_key is required")
    estimate = actual_cost_microusd if estimated_cost_microusd is None else estimated_cost_microusd
    gate_amount = max(estimate, actual_cost_microusd)
    with conn.transaction():
        budget = _ensure_budget_locked(conn, business_slug)
        if budget.status != "active":
            raise AppBudgetInactive(business_slug, budget.status)
        existing = conn.execute(
            f"select {_EVENT_COLUMNS} from app_usage_events "
            "where business_slug = %s and reservation_key = %s",
            (business_slug, key),
        ).fetchone()
        if existing is not None:
            return _event_from_row(existing)
        if app_user_id is not None:
            _require_app_user(conn, business_slug, app_user_id)
        committed = _committed_microusd(conn, business_slug, budget.current_period_start)
        if committed + gate_amount > budget.hard_limit_microusd:
            raise AppBudgetExceeded(
                hard_limit_microusd=budget.hard_limit_microusd,
                committed_microusd=committed,
                requested_microusd=gate_amount,
            )
        row = conn.execute(
            "insert into app_usage_events "
            "(business_slug, app_user_id, app_user_tier, reservation_key, purpose, route, "
            " status, estimated_cost_microusd, actual_cost_microusd, input_tokens, output_tokens, "
            " provider_request_id, provider, model, metadata, completed_at) "
            "values (%s, %s, %s, %s, %s, %s, 'completed', %s, %s, %s, %s, %s, %s, %s, %s::jsonb, now()) "
            f"returning {_EVENT_COLUMNS}",
            (
                business_slug, app_user_id, app_user_tier, key, purpose, route,
                estimate, actual_cost_microusd, input_tokens, output_tokens,
                provider_request_id, provider, model, _json_dumps(metadata or {}),
            ),
        ).fetchone()
    return _event_from_row(row)


def list_usage_events(
    conn, business_slug: str, *, app_user_id: str | None = None, limit: int = 100
) -> list[UsageEvent]:
    """List a business's usage events newest-first, optionally filtered to one sub-user. Pure
    read."""
    if app_user_id is None:
        rows = conn.execute(
            f"select {_EVENT_COLUMNS} from app_usage_events where business_slug = %s "
            "order by created_at desc limit %s",
            (business_slug, limit),
        ).fetchall()
    else:
        rows = conn.execute(
            f"select {_EVENT_COLUMNS} from app_usage_events "
            "where business_slug = %s and app_user_id = %s "
            "order by created_at desc limit %s",
            (business_slug, app_user_id, limit),
        ).fetchall()
    return [_event_from_row(row) for row in rows]
