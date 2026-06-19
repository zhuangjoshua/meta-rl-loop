"""Execution-policy engine — Phase 4 of mediationplan.md.

Decides HOW a unit of the CEO's own work runs against the user's flow-A budget:
run it inline, downgrade to a cheaper model tier, push it to a background job, or
block it — so features degrade gracefully under budget pressure instead of
hard-failing. The decision is ADVISORY: it reads balances and the per-business
routing knobs and returns a recommendation. The atomic money gate is still
`billing.reserve` (it takes the row lock and can raise InsufficientBalance); this
engine never moves balances and opens no transaction.

Inputs it reads:
  * `app_execution_policies` (migration 0004) — per-business knobs: preferred tier,
    inline runtime/output ceilings, whether worker escalation and expensive branches
    are allowed, and an OPTIONAL monthly sub-cap (`monthly_app_budget_cents`). An
    absent row means conservative documented defaults (we do NOT auto-insert on read).
  * flow-A balances via `billing.get_billing_balances` — allowance remaining (a
    metering unit, never money), net of outstanding reservations.
  * a caller-supplied cost estimate (optionally priced per tier).

The monthly sub-cap is a guardrail, NOT a second wallet: the real balance is always
the flow-A ledger, and the cap only ever tightens the effective budget. Per-business
spend is netted via reservation_key because billing's settle/refund entries carry
business_slug=NULL (only the reserve is tagged) — see `_business_period_spend_cents`.

House style (matches billing.py / custody.py): pure leaf, takes a psycopg
connection, imports no psycopg, reads config straight from os.environ. Imports
`billing` for balance reads only (billing imports nothing here → no cycle).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timezone

from . import billing

# Conservative defaults for a business with no explicit policy row. Mirror the DDL
# defaults in db/migrations/0004_execution_policies.sql exactly.
_DEFAULTS = {
    "preferred_model_tier": "standard",
    "max_runtime_seconds": 300,
    "max_output_bytes": 5_000_000,
    "allow_worker_escalation": True,
    "allow_expensive_branches": True,
    "quality_mode": "balanced",
    "retry_depth": 1,
    "monthly_app_budget_cents": None,
}

# Columns a caller may set through upsert_execution_policy (everything but the
# primary key and the managed timestamps).
_MUTABLE_FIELDS = frozenset(_DEFAULTS)

_DEFAULT_EXPENSIVE_THRESHOLD_CENTS = 100  # a per-unit estimate over $1.00 is "expensive"

# Plan-gated minimum CEO wake cadence (seconds). The operator's wake schedule may not be set
# FASTER (smaller interval) than the minimum their subscription plan allows — faster wakes burn
# more inference money, so cadence is an entitlement, not a free knob. This is the single source of
# truth for plan -> floor; the wake-schedule write boundary (`core._ensure_ceo_cron`) reads it and
# refuses a sub-floor interval. `wakes.upsert_wake_schedule` stays a pure leaf with NO plan gate.
#
# Keys are normalized (uppercased, stripped) plan names as they arrive from the operator's Stripe
# subscription metadata (`takyon_plan_name`) / `TAKYON_OPERATOR_DEFAULT_PLAN_NAME`. An unknown or
# absent plan (no active/trialing subscription) falls to the most-restrictive default floor, so a
# plan DOWNGRADE that drops the active subscription tightens the floor rather than loosening it.
_DEFAULT_WAKE_MIN_INTERVAL_SECONDS = 21_600  # 6h — the floor for an unknown / no-subscription plan
_PLAN_WAKE_MIN_INTERVAL_SECONDS: dict[str, int] = {
    "DEV": 60,  # internal/dev plan: allow tight cadences for testing
    "SCALE": 3_600,  # 1h
    "GROWTH": 7_200,  # 2h
    "PRO": 10_800,  # 3h
    "STARTER": 21_600,  # 6h
    "FREE": 86_400,  # 24h — slowest paid-floor cadence (free has no AI budget anyway, see inv #9)
}


def _normalize_plan_name(plan_name: str | None) -> str:
    return str(plan_name or "").strip().upper()


def plan_min_wake_interval_seconds(plan_name: str | None) -> int:
    """Minimum allowed CEO wake interval (seconds) for ``plan_name``. Single source of truth for
    wake-cadence plan gating. Unknown / absent plan -> the conservative default floor (never faster
    than 6h) so an EVIL caller cannot speed up wakes by hiding their plan. A specific plan's floor
    can be overridden per-deployment with ``TAKYON_WAKE_MIN_INTERVAL_SECONDS__<PLAN>`` (e.g.
    ``TAKYON_WAKE_MIN_INTERVAL_SECONDS__PRO=14400``); the global default floor is overridable with
    ``TAKYON_WAKE_MIN_INTERVAL_SECONDS_DEFAULT``. All values clamp to >= 60 so the gate can never
    invert to "no floor"."""
    normalized = _normalize_plan_name(plan_name)
    default_floor = _env_min_interval("TAKYON_WAKE_MIN_INTERVAL_SECONDS_DEFAULT", _DEFAULT_WAKE_MIN_INTERVAL_SECONDS)
    if not normalized:
        return default_floor
    env_override = _env_min_interval(f"TAKYON_WAKE_MIN_INTERVAL_SECONDS__{normalized}", None)
    if env_override is not None:
        return env_override
    base = _PLAN_WAKE_MIN_INTERVAL_SECONDS.get(normalized)
    if base is None:
        return default_floor
    return max(60, int(base))


def _env_min_interval(var: str, fallback: int | None) -> int | None:
    raw = os.environ.get(var)
    if raw is None or raw.strip() == "":
        return fallback
    try:
        value = int(raw)
    except ValueError:
        return fallback
    return max(60, value)


class PolicyError(Exception):
    """Base for execution-policy errors."""


class NoBusiness(PolicyError):
    """No businesses row for this slug — cannot resolve an owner to bill."""


@dataclass(frozen=True)
class ExecutionPolicy:
    """One business's execution knobs. Mirrors a row of app_execution_policies; a
    business with no row gets this populated from `_DEFAULTS`."""

    business_slug: str
    preferred_model_tier: str
    max_runtime_seconds: int
    max_output_bytes: int
    allow_worker_escalation: bool
    allow_expensive_branches: bool
    quality_mode: str
    retry_depth: int
    monthly_app_budget_cents: int | None


@dataclass(frozen=True)
class PolicyDecision:
    """An advisory routing decision. `outcome` is the disposition; `model_tier` is the
    tier to run at (may differ from the requested tier on a downgrade; None when
    blocked); `reason` is a stable machine-readable code; `detail` carries the figures
    behind the decision for receipts/debugging."""

    outcome: str  # 'inline' | 'cheaper' | 'job' | 'blocked'
    reason: str
    model_tier: str | None
    estimate_cents: int
    detail: dict


def expensive_threshold_cents() -> int:
    """The per-unit estimate above which a branch is "expensive", from
    TAKYON_EXECUTION_EXPENSIVE_THRESHOLD_CENTS. Defaults to 100 (= $1.00) and clamps a
    misconfigured value to >= 0 so the gate can never invert (matches custody's
    app_fee_bps idiom)."""
    raw = os.environ.get("TAKYON_EXECUTION_EXPENSIVE_THRESHOLD_CENTS")
    if raw is None or raw.strip() == "":
        return _DEFAULT_EXPENSIVE_THRESHOLD_CENTS
    try:
        value = int(raw)
    except ValueError:
        return _DEFAULT_EXPENSIVE_THRESHOLD_CENTS
    return max(0, value)


_SELECT_COLUMNS = (
    "business_slug, preferred_model_tier, max_runtime_seconds, max_output_bytes, "
    "allow_worker_escalation, allow_expensive_branches, quality_mode, retry_depth, "
    "monthly_app_budget_cents"
)


def get_execution_policy(conn, business_slug: str) -> ExecutionPolicy:
    """Read a business's execution policy, or return conservative defaults if it has no
    row. Pure read — it does NOT insert a row on a miss, so reading a policy never
    mutates state."""
    row = conn.execute(
        f"select {_SELECT_COLUMNS} from app_execution_policies where business_slug = %s",
        (business_slug,),
    ).fetchone()
    if row is None:
        return ExecutionPolicy(business_slug=business_slug, **_DEFAULTS)
    return ExecutionPolicy(
        business_slug=str(row[0]),
        preferred_model_tier=str(row[1]),
        max_runtime_seconds=int(row[2]),
        max_output_bytes=int(row[3]),
        allow_worker_escalation=bool(row[4]),
        allow_expensive_branches=bool(row[5]),
        quality_mode=str(row[6]),
        retry_depth=int(row[7]),
        monthly_app_budget_cents=None if row[8] is None else int(row[8]),
    )


def upsert_execution_policy(conn, business_slug: str, **changes) -> ExecutionPolicy:
    """Create or update a business's policy, preserving fields not named in `changes`.
    Read-merge-write under a row lock so concurrent upserts can't clobber each other;
    the FK to businesses(slug) makes an unknown business fail loud. Returns the policy
    in effect after the write."""
    _validate_changes(changes)
    with conn.transaction():
        existing = conn.execute(
            f"select {_SELECT_COLUMNS} from app_execution_policies "
            "where business_slug = %s for update",
            (business_slug,),
        ).fetchone()
        base = (
            dict(_DEFAULTS)
            if existing is None
            else {
                "preferred_model_tier": existing[1],
                "max_runtime_seconds": existing[2],
                "max_output_bytes": existing[3],
                "allow_worker_escalation": existing[4],
                "allow_expensive_branches": existing[5],
                "quality_mode": existing[6],
                "retry_depth": existing[7],
                "monthly_app_budget_cents": existing[8],
            }
        )
        merged = {**base, **changes}
        conn.execute(
            "insert into app_execution_policies ("
            " business_slug, preferred_model_tier, max_runtime_seconds, max_output_bytes,"
            " allow_worker_escalation, allow_expensive_branches, quality_mode, retry_depth,"
            " monthly_app_budget_cents"
            ") values ("
            " %(business_slug)s, %(preferred_model_tier)s, %(max_runtime_seconds)s,"
            " %(max_output_bytes)s, %(allow_worker_escalation)s, %(allow_expensive_branches)s,"
            " %(quality_mode)s, %(retry_depth)s, %(monthly_app_budget_cents)s"
            ") on conflict (business_slug) do update set"
            " preferred_model_tier     = excluded.preferred_model_tier,"
            " max_runtime_seconds      = excluded.max_runtime_seconds,"
            " max_output_bytes         = excluded.max_output_bytes,"
            " allow_worker_escalation  = excluded.allow_worker_escalation,"
            " allow_expensive_branches = excluded.allow_expensive_branches,"
            " quality_mode             = excluded.quality_mode,"
            " retry_depth              = excluded.retry_depth,"
            " monthly_app_budget_cents = excluded.monthly_app_budget_cents,"
            " updated_at               = now()",
            {"business_slug": business_slug, **merged},
        )
    return get_execution_policy(conn, business_slug)


def decide_execution(
    conn,
    *,
    business_slug: str,
    estimate_cents: int,
    requested_tier: str | None = None,
    estimated_runtime_seconds: int = 0,
    estimated_output_bytes: int = 0,
    tier_estimates: dict[str, int] | None = None,
) -> PolicyDecision:
    """Recommend how to run a unit of the business's own work.

    Resolves the owner from `business_slug` (single source of truth — the caller can't
    pass a mismatched user), reads its policy and the owner's flow-A balances, and
    returns one of four outcomes:

      * 'inline'  — run now at the requested tier, within the inline runtime/output
                    ceilings.
      * 'cheaper' — run now (inline) but at a downgraded affordable tier, because the
                    requested tier is unaffordable or an disallowed expensive branch.
      * 'job'     — must run as a background job (estimated runtime/output exceeds the
                    inline ceiling) and escalation is allowed; `model_tier` is whatever
                    was chosen (possibly downgraded — see detail['downgraded']).
      * 'blocked' — cannot run: nothing affordable/allowed, or it exceeds the inline
                    ceiling with escalation disabled. `reason` is precise.

    A zero estimate is always affordable, so free actions are never blocked on budget
    (they can still route to a job if they're long/large). Advisory only — `billing.reserve`
    is the atomic gate that actually holds funds. Raises on broken preconditions (bad
    inputs, unknown business, missing billing account) rather than laundering them into
    a budget 'blocked'."""
    if estimate_cents < 0:
        raise ValueError("estimate_cents must be >= 0")
    if estimated_runtime_seconds < 0:
        raise ValueError("estimated_runtime_seconds must be >= 0")
    if estimated_output_bytes < 0:
        raise ValueError("estimated_output_bytes must be >= 0")
    if requested_tier is not None and not requested_tier:
        raise ValueError("requested_tier, if given, must be a non-empty string")

    owner = conn.execute(
        "select owner_user_id from businesses where slug = %s", (business_slug,)
    ).fetchone()
    if owner is None:
        raise NoBusiness(business_slug)
    user_id = str(owner[0])

    policy = get_execution_policy(conn, business_slug)
    effective_tier = requested_tier or policy.preferred_model_tier

    # Candidate tier → estimate map. A caller that priced several tiers passes them;
    # otherwise the single estimate is attributed to the effective tier.
    if tier_estimates:
        candidates = {str(t): int(e) for t, e in tier_estimates.items()}
        for tier, est in candidates.items():
            if est < 0:
                raise ValueError(f"tier_estimates[{tier!r}] must be >= 0")
        candidates.setdefault(effective_tier, estimate_cents)
    else:
        candidates = {effective_tier: estimate_cents}
    requested_est = candidates[effective_tier]

    balances = billing.get_billing_balances(conn, user_id)
    flow_a_available = max(0, balances.allowance_remaining_cents)

    cap = policy.monthly_app_budget_cents
    if cap is not None:
        spent = _business_period_spend_cents(conn, business_slug)
        cap_headroom = max(0, cap - spent)
        effective_budget = min(flow_a_available, cap_headroom)
    else:
        spent = None
        cap_headroom = None
        effective_budget = flow_a_available

    threshold = expensive_threshold_cents()

    def _allowed(est: int) -> bool:
        return policy.allow_expensive_branches or est <= threshold

    # affordable_budget ignores the expensive gate (so we can tell a budget block apart
    # from an expensive-branch block); affordable_allowed also applies the gate.
    affordable_budget = {t: e for t, e in candidates.items() if e <= effective_budget}
    affordable_allowed = {t: e for t, e in affordable_budget.items() if _allowed(e)}

    detail: dict = {
        "requested_tier": effective_tier,
        "requested_estimate_cents": requested_est,
        "flow_a_available_cents": flow_a_available,
        "effective_budget_cents": effective_budget,
        "expensive_threshold_cents": threshold,
        "candidates": dict(candidates),
    }
    if cap is not None:
        detail["monthly_app_budget_cents"] = cap
        detail["business_period_spend_cents"] = spent
        detail["business_cap_headroom_cents"] = cap_headroom

    # ---- choose a tier ---------------------------------------------------------
    if effective_tier in affordable_allowed:
        chosen_tier: str = effective_tier
        chosen_est: int = requested_est
        downgraded = False
    elif affordable_allowed:
        # Closest affordable quality: the most expensive tier we can still run.
        chosen_tier, chosen_est = max(
            affordable_allowed.items(), key=lambda kv: (kv[1], kv[0])
        )
        downgraded = True
    else:
        cheapest_est = min(candidates.values())
        if cheapest_est > flow_a_available:
            reason = "insufficient_balance"
        elif cap is not None and cheapest_est > cap_headroom:
            reason = "business_cap_exhausted"
        else:
            reason = "expensive_branch_disallowed"
        detail["downgraded"] = False
        return PolicyDecision(
            outcome="blocked",
            reason=reason,
            model_tier=None,
            estimate_cents=0,
            detail=detail,
        )

    detail["downgraded"] = downgraded

    # ---- inline vs background job (tier-independent: about the work's size/time) ----
    exceeds_runtime = estimated_runtime_seconds > policy.max_runtime_seconds
    exceeds_output = estimated_output_bytes > policy.max_output_bytes
    detail["exceeds_runtime"] = exceeds_runtime
    detail["exceeds_output"] = exceeds_output
    if exceeds_runtime or exceeds_output:
        if policy.allow_worker_escalation:
            return PolicyDecision(
                outcome="job",
                reason="exceeds_inline_limits",
                model_tier=chosen_tier,
                estimate_cents=chosen_est,
                detail=detail,
            )
        return PolicyDecision(
            outcome="blocked",
            reason="exceeds_inline_limits_and_escalation_disabled",
            model_tier=None,
            estimate_cents=0,
            detail=detail,
        )

    if downgraded:
        return PolicyDecision(
            outcome="cheaper",
            reason="downgraded_to_affordable_tier",
            model_tier=chosen_tier,
            estimate_cents=chosen_est,
            detail=detail,
        )
    return PolicyDecision(
        outcome="inline",
        reason="ok",
        model_tier=chosen_tier,
        estimate_cents=chosen_est,
        detail=detail,
    )


def _validate_changes(changes: dict) -> None:
    """Reject unknown fields and obviously-bad values up front, so a caller gets a clean
    ValueError instead of an opaque IntegrityError + aborted transaction. The DDL CHECK
    constraints remain the hard backstop."""
    unknown = set(changes) - _MUTABLE_FIELDS
    if unknown:
        raise ValueError(f"unknown execution-policy fields: {sorted(unknown)}")
    for key in ("preferred_model_tier", "quality_mode"):
        if key in changes and (not isinstance(changes[key], str) or not changes[key]):
            raise ValueError(f"{key} must be a non-empty string")
    for key in ("max_runtime_seconds", "max_output_bytes"):
        if key in changes and (not isinstance(changes[key], int) or changes[key] <= 0):
            raise ValueError(f"{key} must be a positive integer")
    if "retry_depth" in changes and (
        not isinstance(changes["retry_depth"], int) or changes["retry_depth"] < 0
    ):
        raise ValueError("retry_depth must be a non-negative integer")
    cap = changes.get("monthly_app_budget_cents")
    if "monthly_app_budget_cents" in changes and cap is not None:
        if not isinstance(cap, int) or cap < 0:
            raise ValueError("monthly_app_budget_cents must be None or a non-negative integer")


def _current_period_start() -> datetime:
    """First instant of the current month in UTC (the monthly sub-cap window)."""
    now = datetime.now(timezone.utc)
    return datetime(now.year, now.month, 1, tzinfo=timezone.utc)


def _business_period_spend_cents(conn, business_slug: str) -> int:
    """Net cents this business has committed in the current monthly period: the sum of
    its reservations less everything later released (Σreserve − Σrefund over the
    business's reservation_keys). billing's settle/refund entries carry
    business_slug=NULL — only the reserve is tagged — so we net via the reservation_key
    set rather than filtering every kind by business_slug. The result is outstanding
    holds + settled actuals, which is exactly what the monthly sub-cap should measure."""
    period_start = _current_period_start()
    row = conn.execute(
        "select"
        " coalesce(sum(amount_cents) filter (where kind = 'reserve'), 0)"
        " - coalesce(sum(amount_cents) filter (where kind = 'refund'), 0)"
        " from billing_entries where reservation_key in ("
        "   select distinct reservation_key from billing_entries"
        "   where business_slug = %(slug)s and kind = 'reserve'"
        "     and reservation_key is not null and created_at >= %(ps)s"
        " )",
        {"slug": business_slug, "ps": period_start},
    ).fetchone()
    return int(row[0])
