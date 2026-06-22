"""Product AI-spend budget cap + usage ledger — Phase 5 (increment c) of mediationplan.md.

Builds on `app_identity` (the sub-user spine). One concern, scoped by `business_slug`: cap
what a business's PRODUCT is allowed to spend on AI on behalf of its sub-users, and record
every spend. This is distinct from the Takyon operator's own money in `billing.py` (0002):
that is the user→platform ledger; this is the per-business product COMPUTE budget.

THE ONE GATE — reserve-then-settle. The SQLite trunk gated product spend on TWO
uncoordinated paths, and both are wrong under load:
  1. an estimate PRE-CHECK on the old SQLite app runtime that read a rendered budget mirror and compared
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

# Invariant 9 (GOAL_RULES §3): there is NO flat per-business pool cap. Budget is derived from
# the active PAID subscription's per-subuser ``included_ai_budget_microusd`` (the per-user gate),
# never a per-business arbitrary pool. ``app_budgets.hard_limit_microusd`` is therefore a
# SENTINEL column: NULL/None means "no per-business pool cap" (the per-subuser gate is the only
# budget gate) and a non-null integer is an explicit, enforced per-business cap that an operator
# may still set as a fail-closed ceiling (e.g. 0 = refuse all product spend for an unentitled
# business). New budgets open with NO pool cap (None), not the old $5 default.
_NO_POOL_CAP = None


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


class AppUserBudgetExceeded(AppUsageError):
    """A reserve would push one sub-user past their current monthly plan-funded AI budget."""

    def __init__(
        self,
        *,
        app_user_id: str,
        user_monthly_limit_microusd: int,
        committed_microusd: int,
        requested_microusd: int,
    ) -> None:
        self.app_user_id = app_user_id
        self.user_monthly_limit_microusd = user_monthly_limit_microusd
        self.committed_microusd = committed_microusd
        self.requested_microusd = requested_microusd
        self.remaining_microusd = max(0, user_monthly_limit_microusd - committed_microusd)
        super().__init__(
            f"app user usage would exceed monthly plan cap: need {requested_microusd}, "
            f"committed {committed_microusd} of {user_monthly_limit_microusd}"
        )


class UnknownReservation(AppUsageError):
    """settle/release referenced a reservation_key that was never reserved in this business."""


class AppUserNotFound(AppUsageError):
    """The referenced sub-user does not exist in this business."""


@dataclass(frozen=True)
class AppBudget:
    """One business's product AI-spend pool row for the current metering period.

    ``hard_limit_microusd`` is a SENTINEL (invariant 9): None means NO per-business pool cap
    (the per-subuser subscription gate is the only budget gate); a non-null integer is an
    explicit, enforced per-business ceiling."""

    business_slug: str
    status: str
    hard_limit_microusd: int | None
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
        hard_limit_microusd=None if row[2] is None else int(row[2]),
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


# The reserve/settle/release row ops live in the migration-0037 SECURITY DEFINER functions
# (safebox_reserve_usage / safebox_settle_usage / safebox_release_usage), which are the ONLY
# sanctioned writers of app_usage_events — the gate's privilege boundary. Each returns a
# `safebox_usage_gate_result` composite: a leading `refusal` discriminator, an `is_noop` flag, and
# figure columns, then the 19 event columns (in _EVENT_COLUMNS order). _GATE_RESULT_PREFIX is the
# count of those leading non-event columns, so the event sub-row is `row[_GATE_RESULT_PREFIX:]` and
# feeds _event_from_row unchanged. Refusals are mapped back to the SAME typed exceptions (with the
# SAME fields) the in-Python gate raised, so callers and tests see identical behavior.
#   row[0] refusal   row[1] is_noop   row[2] fig_status   row[3] fig_hard_limit_microusd
#   row[4] fig_user_limit_microusd   row[5] fig_committed_microusd   row[6] fig_requested_microusd
_GATE_RESULT_PREFIX = 7


def _raise_for_gate_refusal(
    row,
    *,
    business_slug: str,
    app_user_id: str | None = None,
    reservation_key: str | None = None,
) -> None:
    """If the gate result carries a refusal, raise the matching typed exception with exact figures;
    otherwise return (the caller then builds the UsageEvent from the event sub-row)."""
    refusal = row[0]
    if refusal is None:
        return
    if refusal == "budget_inactive":
        raise AppBudgetInactive(business_slug, str(row[2]))  # fig_status
    if refusal == "app_user_not_found":
        raise AppUserNotFound(app_user_id)
    if refusal == "app_user_budget_exceeded":
        raise AppUserBudgetExceeded(
            app_user_id=app_user_id,
            user_monthly_limit_microusd=int(row[4]),  # fig_user_limit_microusd
            committed_microusd=int(row[5]),  # fig_committed_microusd
            requested_microusd=int(row[6]),  # fig_requested_microusd
        )
    if refusal == "budget_exceeded":
        raise AppBudgetExceeded(
            hard_limit_microusd=int(row[3]),  # fig_hard_limit_microusd
            committed_microusd=int(row[5]),  # fig_committed_microusd
            requested_microusd=int(row[6]),  # fig_requested_microusd
        )
    if refusal == "unknown_reservation":
        raise UnknownReservation(reservation_key)
    raise AppUsageError(f"unexpected gate refusal: {refusal!r}")  # pragma: no cover - defensive


def _gate_row_is_noop(row) -> bool:
    """True when the gate call was an idempotent no-op on an already-finalized row (row[1])."""
    return bool(row[1])


def _event_from_gate_row(row) -> UsageEvent:
    """Build the UsageEvent from a non-refusal gate result (the event sub-row after the prefix)."""
    return _event_from_row(row[_GATE_RESULT_PREFIX:])


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
    """Open the business budget with NO per-business pool cap if absent (invariant 9:
    hard_limit_microusd defaults to NULL → the per-subuser subscription gate is the only budget
    gate), then lock its row `for update`. Must be called inside a transaction. The lock
    serializes all concurrent reserves for the business so the committed-spend aggregate is
    consistent. Unknown business → ForeignKeyViolation (fail loud).

    Weekly rollover (migration 0035): the row's period is the ISO-week window. Migration 0035 set
    the column DEFAULTS to date_trunc('week', now()), but NEW-row defaults alone never advance an
    already-open row — without a runtime roll the window would pin forever and committed spend would
    never reset next week. So, still under the row lock, if the selected period has elapsed
    (current_period_end <= now()), atomically advance it to the current ISO week before returning.
    Because _committed_microusd aggregates events with created_at >= current_period_start, advancing
    the window starts the customer's weekly allowance fresh — the intended "resets weekly" behavior.
    """
    conn.execute(
        "insert into app_budgets (business_slug) values (%s) on conflict (business_slug) do nothing",
        (business_slug,),
    )
    row = conn.execute(
        f"select {_BUDGET_COLUMNS} from app_budgets where business_slug = %s for update",
        (business_slug,),
    ).fetchone()
    rolled = conn.execute(
        "update app_budgets set "
        " current_period_start = date_trunc('week', now()), "
        " current_period_end = date_trunc('week', now()) + interval '1 week', "
        " updated_at = now() "
        f"where business_slug = %s and current_period_end <= now() returning {_BUDGET_COLUMNS}",
        (business_slug,),
    ).fetchone()
    return _budget_from_row(rolled if rolled is not None else row)


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


def _app_user_committed_microusd(conn, business_slug: str, app_user_id: str, period_start) -> int:
    """Committed spend for one sub-user within the period: held estimates of `reserved` rows plus
    recorded actuals of `completed` rows. Caller must already hold the business budget row lock so
    the aggregate is read from a stable view."""
    row = conn.execute(
        "select coalesce(sum(case "
        " when status = 'reserved' then estimated_cost_microusd "
        " when status = 'completed' then actual_cost_microusd "
        " else 0 end), 0) "
        "from app_usage_events where business_slug = %s and app_user_id = %s and created_at >= %s",
        (business_slug, app_user_id, period_start),
    ).fetchone()
    return int(row[0])


# ── budget catalog ───────────────────────────────────────────────────────────────


def ensure_app_budget(conn, business_slug: str) -> AppBudget:
    """Open the business budget with NO per-business pool cap if absent (idempotent), returning
    the row in effect. Period is the ISO week (migration 0035): the row opens on the current
    date_trunc('week', now()) window and _ensure_budget_locked rolls it forward each week so
    committed spend resets weekly."""
    with conn.transaction():
        return _ensure_budget_locked(conn, business_slug)


def set_app_budget(
    conn, business_slug: str, *, hard_limit_microusd: int | None, status: str = "active"
) -> AppBudget:
    """Set the per-business pool sentinel (and optionally status) for a business, opening the row
    first if needed. ``hard_limit_microusd=None`` clears the pool cap (the per-subuser
    subscription gate is then the only budget gate); a non-negative integer sets an explicit,
    enforced ceiling (0 = refuse all product spend). Unknown business → ForeignKeyViolation.

    Note (invariant 9): this is no longer reachable from an operator tool — the
    ``business_configure_app_budget`` cap tool was removed. It remains as the shared write
    primitive used by tests and internal reconciliation, never as a user-facing pool override."""
    if hard_limit_microusd is not None and hard_limit_microusd < 0:
        raise ValueError("hard_limit_microusd must be >= 0 or None")
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


def _microusd_to_usd(value: int | None) -> float | None:
    """Present a microUSD integer as a 2-decimal USD float for the UI. None passes through (an
    absent figure must stay absent, not render as $0.00). The microUSD integer remains the
    authoritative number; this is a display projection only."""
    if value is None:
        return None
    return round(int(value) / 1_000_000, 2)


def get_usage_summary(conn, business_slug: str) -> dict:
    """Authoritative budget/remaining read for the current period: the figures a pre-flight UI
    or check should use INSTEAD of the old SQLite rendered-mirror read that the broken estimate
    pre-check relied on. Pure read; the real gate is reserve_usage. Returns
    status/hard_limit/committed/remaining/period plus a $-denominated projection for the UI.

    The unit the customer sees is dollars over a WEEKLY period (the period is the ISO-week window
    fixed in the app_budgets row — see migration 0035): ``period_unit='week'`` and the ``*_usd``
    fields are the dollar projection of the authoritative microUSD figures, so the Plans & Billing
    surface renders a "$ weekly allocation" without each caller re-deriving the conversion or the
    period unit. The microUSD integers remain the source of truth; the dollar fields are display
    only.

    Invariant 9: there is NO free per-business pool. A never-opened budget reports status
    'missing' with NO pool cap (hard_limit_microusd=None) and remaining=None — it does NOT hand
    back a flat default allowance. When a pool cap is set (a non-null integer), remaining is the
    cap minus committed; with no pool cap (None), remaining is None (the per-subuser subscription
    gate, not a pool, governs spend)."""
    budget = get_app_budget(conn, business_slug)
    if budget is None:
        return {
            "status": "missing",
            "hard_limit_microusd": None,
            "committed_microusd": 0,
            "remaining_microusd": None,
            "current_period_start": None,
            "current_period_end": None,
            # $-denominated weekly projection for the UI (microUSD figures above stay authoritative).
            "period_unit": "week",
            "hard_limit_usd": None,
            "committed_usd": 0.0,
            "remaining_usd": None,
        }
    committed = _committed_microusd(conn, business_slug, budget.current_period_start)
    remaining = (
        None
        if budget.hard_limit_microusd is None
        else max(0, budget.hard_limit_microusd - committed)
    )
    return {
        "status": budget.status,
        "hard_limit_microusd": budget.hard_limit_microusd,
        "committed_microusd": committed,
        "remaining_microusd": remaining,
        "current_period_start": budget.current_period_start,
        "current_period_end": budget.current_period_end,
        # $-denominated weekly projection for the UI (microUSD figures above stay authoritative).
        "period_unit": "week",
        "hard_limit_usd": _microusd_to_usd(budget.hard_limit_microusd),
        "committed_usd": _microusd_to_usd(committed),
        "remaining_usd": _microusd_to_usd(remaining),
    }


# ── the gate: reserve → settle | release ───────────────────────────────────────────


def reserve_usage(
    conn,
    business_slug: str,
    *,
    estimated_cost_microusd: int,
    reservation_key: str,
    app_user_id: str | None = None,
    user_monthly_limit_microusd: int | None = None,
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
    if user_monthly_limit_microusd is not None and user_monthly_limit_microusd < 0:
        raise ValueError("user_monthly_limit_microusd must be >= 0")
    key = str(reservation_key or "").strip()
    if not key:
        raise ValueError("reservation_key is required")
    if user_monthly_limit_microusd is not None and app_user_id is None:
        raise ValueError("app_user_id is required when user_monthly_limit_microusd is set")
    # The atomic-under-the-budget-row-lock body now lives in the migration-0037 SECURITY DEFINER
    # function safebox_reserve_usage (the gate is the ONLY sanctioned writer of app_usage_events).
    # Its row ops are a verbatim port of the former in-Python body: open+lock+weekly-roll the budget,
    # inactive→refuse, idempotent reservation_key short circuit, sub-user existence + per-subuser
    # gate, per-business pool gate, then insert the 'reserved' row. Refusals come back as a
    # discriminator and are re-raised here as the identical typed exceptions.
    row = conn.execute(
        "select * from safebox_reserve_usage(%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)",
        (
            business_slug, estimated_cost_microusd, key, app_user_id,
            user_monthly_limit_microusd, app_user_tier, purpose, route,
            provider, model, _json_dumps(metadata or {}),
        ),
    ).fetchone()
    _raise_for_gate_refusal(row, business_slug=business_slug, app_user_id=app_user_id)
    return _event_from_gate_row(row)


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
    # Row ops in the migration-0037 SECURITY DEFINER function safebox_settle_usage (verbatim port):
    # lock the event row, unknown→refuse, finalized→no-op return, else reserved→completed recording
    # the actual with the same COALESCE-preserve + metadata-merge and NO cap re-check (money-truth).
    row = conn.execute(
        "select * from safebox_settle_usage(%s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)",
        (
            business_slug, key, actual_cost_microusd, input_tokens, output_tokens,
            provider_request_id, provider, model,
            None if metadata is None else _json_dumps(metadata),
        ),
    ).fetchone()
    _raise_for_gate_refusal(row, business_slug=business_slug, reservation_key=key)
    settled = _event_from_gate_row(row)
    # An idempotent no-op on an already-finalized row returns BEFORE the mirror — exactly as the
    # former in-Python `return event` short-circuit did (it returned from inside the transaction,
    # never reaching this mirror call). Only a fresh settle mirrors.
    if _gate_row_is_noop(row):
        return settled
    # Post-commit: mirror the settled cost into OpenMeter for exact-cost aggregation
    # (GOAL_RULES §4 buy-not-build). Fire-and-forget and FAIL-SAFE — the authoritative ledger
    # already committed above, so a mirror failure (or OpenMeter being disabled) must never
    # raise into the caller. Idempotent on the reservation key.
    _mirror_settled_usage_to_openmeter(settled)
    return settled


def _mirror_settled_usage_to_openmeter(event: "UsageEvent") -> None:
    """Best-effort OpenMeter usage-event mirror for one settled event. Never raises."""
    if int(getattr(event, "actual_cost_microusd", 0) or 0) <= 0:
        return
    try:
        from . import openmeter_backend
    except ImportError:  # pragma: no cover - import-shape fallback
        try:
            from plugins.takyon import openmeter_backend  # type: ignore
        except Exception:
            return
    try:
        if not openmeter_backend.enabled():
            return
        openmeter_backend.ingest_usage_event(
            business_slug=event.business_slug,
            reservation_key=event.reservation_key,
            actual_cost_microusd=int(event.actual_cost_microusd or 0),
            route=getattr(event, "route", None),
            provider=getattr(event, "provider", None),
            model=getattr(event, "model", None),
            app_user_id=getattr(event, "app_user_id", None),
            occurred_at=(
                event.completed_at.isoformat()
                if getattr(event, "completed_at", None) is not None
                and hasattr(event.completed_at, "isoformat")
                else None
            ),
        )
    except Exception:  # noqa: BLE001 - a cost-mirror failure must never break a settle
        return


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
    # Row ops in the migration-0037 SECURITY DEFINER function safebox_release_usage (verbatim port):
    # lock the event row, unknown→refuse, finalized→no-op return, else reserved→failed (error given)
    # | released (clean cancel), actual cost zeroed, error coalesced, metadata merged. The 'failed'
    # vs 'released' split on `error` is decided inside the function (error not null → failed).
    row = conn.execute(
        "select * from safebox_release_usage(%s, %s, %s, %s::jsonb)",
        (
            business_slug, key, error,
            None if metadata is None else _json_dumps(metadata),
        ),
    ).fetchone()
    _raise_for_gate_refusal(row, business_slug=business_slug, reservation_key=key)
    return _event_from_gate_row(row)


def record_completed_usage(
    conn,
    business_slug: str,
    *,
    actual_cost_microusd: int,
    reservation_key: str,
    estimated_cost_microusd: int | None = None,
    app_user_id: str | None = None,
    user_monthly_limit_microusd: int | None = None,
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
    self-report path from the old SQLite app runtime, where the cost was already known and there
    was no provider round-trip to straddle. Goes through the SAME gate as reserve (committed +
    this amount must fit the cap), then writes a `completed` row directly. Idempotent on
    `reservation_key`. The gate amount is `estimated_cost_microusd` if given, else the actual."""
    if actual_cost_microusd < 0:
        raise ValueError("actual_cost_microusd must be >= 0")
    if estimated_cost_microusd is not None and estimated_cost_microusd < 0:
        raise ValueError("estimated_cost_microusd must be >= 0")
    if user_monthly_limit_microusd is not None and user_monthly_limit_microusd < 0:
        raise ValueError("user_monthly_limit_microusd must be >= 0")
    key = str(reservation_key or "").strip()
    if not key:
        raise ValueError("reservation_key is required")
    if user_monthly_limit_microusd is not None and app_user_id is None:
        raise ValueError("app_user_id is required when user_monthly_limit_microusd is set")
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
        if app_user_id is not None and user_monthly_limit_microusd is not None:
            user_committed = _app_user_committed_microusd(
                conn, business_slug, app_user_id, budget.current_period_start
            )
            if user_committed + gate_amount > user_monthly_limit_microusd:
                raise AppUserBudgetExceeded(
                    app_user_id=app_user_id,
                    user_monthly_limit_microusd=user_monthly_limit_microusd,
                    committed_microusd=user_committed,
                    requested_microusd=gate_amount,
                )
        # Per-business pool gate: ONLY when an explicit cap is set (sentinel None = no pool cap,
        # invariant 9 — the per-subuser subscription gate above is then the sole budget gate).
        if budget.hard_limit_microusd is not None:
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
