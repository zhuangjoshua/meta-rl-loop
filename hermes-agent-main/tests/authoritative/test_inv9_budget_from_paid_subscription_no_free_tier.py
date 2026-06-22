"""Authoritative INVARIANT 9 — budget strictly from a paid subscription.

GOAL_RULES.md §3, invariant 9 (verbatim target):

    "Budget strictly from a paid subscription — no free tier, no arbitrary cap.
    Per-business / per-subapp AI budget comes ONLY from the active **paid**
    subscription's `included_ai_budget_microusd` (the `y` term of x+y+z),
    re-derived on subscription change. **Remove** the flat
    `_DEFAULT_HARD_LIMIT_MICROUSD` ($5) per-business pool cap **and** the `$0.50`
    per-user free-tier floor (`ai_gateway.py:248`); no active entitlement ⇒ 0
    budget ⇒ all product AI spend refused (HTTP 402). Bill at **exact provider
    cost — no markup** (margin is structural = `z`). Delete or repurpose the
    now-dead `business_configure_app_budget` operator cap tool."

ASPIRATIONAL — this test asserts the DESIRED END STATE and is EXPECTED to be RED
on the current code, which still ships:
  * `app_usage._DEFAULT_HARD_LIMIT_MICROUSD = 5_000_000`  (the $5 per-business cap)
  * `ai_gateway._DEFAULT_USER_MONTHLY_BUDGET_MICROUSD = 500_000`  (the $0.50 floor)
  * `ai_gateway._user_monthly_budget_microusd(plan)` floors a free/None plan to $0.50
  * `app_actions._reserve_usage` / `_resolve_pg_action_usage_limit` do NOT call any
    `_require_active_entitlement` — a service/null-subuser reserve falls through to the
    per-business pool = unbounded ungated spend.

RED here = the target the optimization loop must reach. GREEN means the upstream
holes have actually been closed in the canonical source.

Every symbol referenced below was confirmed by reading the real source in
`/Users/Zygote/Downloads/takyon/hermes-agent-main` before this file was written.
The assertions are source/unit-level and need NO credential or network. The
genuinely Postgres-gated end-to-end refusal is marked separately with the repo's
pg fixtures.
"""

from __future__ import annotations

import inspect

import pytest

from plugins.takyon import ai_gateway
from plugins.takyon import app_usage
from plugins.takyon.app_entitlements import PlanPolicy


# ── helpers ────────────────────────────────────────────────────────────────────


def _plan(*, tier: str, included_ai_budget_microusd: int, plan_key: str = "pk") -> PlanPolicy:
    """Build a real PlanPolicy with the fields invariant 9 actually reads.

    Field order/names confirmed against app_entitlements.PlanPolicy (frozen
    dataclass, app_entitlements.py:90-107)."""
    return PlanPolicy(
        id="plan-id",
        business_slug="acme",
        plan_key=plan_key,
        tier=tier,
        price_cents=0 if tier == "free" else 2000,
        currency="usd",
        billing_interval="month",
        included_ai_budget_microusd=included_ai_budget_microusd,
        included_action_quota=0,
        stripe_product_id=None,
        stripe_price_id=None,
        source="test",
        notes="",
        metadata={},
    )


# ── 1. No arbitrary per-business hard-cap default ───────────────────────────────


def test_no_default_per_business_hard_cap_constant():
    """Invariant 9: the flat `$5` per-business pool cap must be REMOVED.

    The budget is the plan's `included_ai_budget_microusd`, not a flat pool. A
    nonzero `_DEFAULT_HARD_LIMIT_MICROUSD` is exactly the "arbitrary cap" the
    invariant forbids — it both caps paid budget below the plan and hands a free
    pool to the unentitled.

    RED on current code: app_usage.py:47 defines it = 5_000_000.
    """
    default = getattr(app_usage, "_DEFAULT_HARD_LIMIT_MICROUSD", None)
    assert default in (None, 0), (
        "Invariant 9 requires NO arbitrary per-business hard-cap default. "
        f"Found app_usage._DEFAULT_HARD_LIMIT_MICROUSD={default!r}. The per-business "
        "budget must derive from the active paid subscription's "
        "included_ai_budget_microusd, never a flat pool."
    )


def test_unopened_budget_summary_is_zero_not_default_pool():
    """`get_usage_summary` for a never-opened budget must NOT hand back a free pool.

    Today (app_usage.py:299-308) a missing budget reports
    hard_limit==remaining==_DEFAULT_HARD_LIMIT_MICROUSD ($5) — a free per-business
    allowance with no subscription behind it. Under invariant 9 a business with no
    active paid entitlement has 0 budget.

    RED on current code: the missing-budget branch returns the $5 default.
    """
    src = inspect.getsource(app_usage.get_usage_summary)
    assert "_DEFAULT_HARD_LIMIT_MICROUSD" not in src, (
        "get_usage_summary still falls back to the flat _DEFAULT_HARD_LIMIT_MICROUSD "
        "pool for an unopened budget. Invariant 9: no free per-business pool; an "
        "unentitled business has 0 budget."
    )


# ── 2. No $0.50 per-user free-tier floor; budget == plan.included_ai_budget ─────


def test_no_user_monthly_free_floor_constant():
    """Invariant 9 explicitly names `ai_gateway.py:248` — the `$0.50` floor — for removal.

    RED on current code: ai_gateway._DEFAULT_USER_MONTHLY_BUDGET_MICROUSD = 500_000.
    """
    floor = getattr(ai_gateway, "_DEFAULT_USER_MONTHLY_BUDGET_MICROUSD", None)
    assert floor in (None, 0), (
        "Invariant 9 requires the $0.50 per-user free-tier floor to be removed. "
        f"Found ai_gateway._DEFAULT_USER_MONTHLY_BUDGET_MICROUSD={floor!r}."
    )


def test_no_entitlement_means_zero_budget_not_floor():
    """No active paid plan ⇒ 0 budget — NOT a $0.50 (or any positive) free floor.

    `_user_weekly_budget_microusd(None)` is the "no plan policy" path. Under
    invariant 9 it must resolve to 0 so that, with the per-user gate active, the
    reserve refuses (the gateway then surfaces 402).
    """
    assert ai_gateway._user_weekly_budget_microusd(None) == 0, (
        "No plan ⇒ budget must be 0 (refuse → 402), not a free floor."
    )


def test_free_tier_plan_grants_zero_budget():
    """A `free` tier plan left at 0 must stay 0 — no floor top-up.

    Invariant 9: budget comes ONLY from a PAID subscription; a free tier funds 0.
    """
    free_plan = _plan(tier="free", included_ai_budget_microusd=0)
    assert ai_gateway._user_weekly_budget_microusd(free_plan) == 0, (
        "A free-tier plan must grant exactly its configured (0) budget, not the floor."
    )


def test_paid_plan_budget_is_the_included_ai_budget_prorated_to_the_window():
    """A paid plan's per-user budget == its `included_ai_budget_microusd` pro-rated to
    the usage window, with NO markup/scaling beyond that documented pro-ration.

    The plan's `included_ai_budget_microusd` is a MONTHLY allowance; the usage window is
    the ISO week, so the per-window budget is that allowance × window_days /
    funding_period_days (so a full ~30-day month totals the monthly allowance — the fix
    for the ~4.3× weekly overspend a raw monthly allowance over a weekly window caused).
    This is the regression guard that "remove the free floor" did not also break paid
    budgets, and that the ONLY transform applied to the y-term is the window pro-ration
    (no markup). Asserted against the module's own window constants, not a literal, so it
    stays a contract about the relationship rather than a snapshot of the number.
    """
    monthly = 4_200_000
    paid_plan = _plan(tier="pro", included_ai_budget_microusd=monthly)
    expected = monthly * ai_gateway._USAGE_WINDOW_DAYS // ai_gateway._PLAN_FUNDING_PERIOD_DAYS
    assert ai_gateway._user_weekly_budget_microusd(paid_plan) == expected


# ── 3. Unentitled ⇒ refused on BOTH the gateway AND the actions paths ───────────


def test_gateway_message_path_requires_active_entitlement():
    """The /messages broker must 402 when there is no active entitlement.

    Confirms `_require_active_entitlement` exists, is invoked by
    `broker_message_for_business`, and 402s on None. This half already holds —
    it is the contract the actions path must be brought up to.
    """
    # The guard 402s on None.
    with pytest.raises(ai_gateway.GatewayMessageError) as exc:
        ai_gateway._require_active_entitlement(None)
    assert exc.value.status_code == 402
    assert exc.value.detail == {"error": "subscription_required"}
    # And the message broker actually calls it.
    assert "_require_active_entitlement" in inspect.getsource(
        ai_gateway.broker_message_for_business
    )


def test_gateway_search_path_requires_active_entitlement():
    """The /search broker must also gate on an active entitlement (regression guard)."""
    assert "_require_active_entitlement" in inspect.getsource(
        ai_gateway.broker_search_for_business
    )


def test_app_actions_reserve_path_requires_active_entitlement():
    """THE NAMED HOLE (GOAL_RULES §3 gap #4): `app_actions._reserve_usage` does not
    require an active entitlement, so a service/null-subuser action reserve falls
    through to the per-business pool = unbounded ungated spend.

    Invariant 9 / gap #4: the action reserve path MUST enforce the active-entitlement
    requirement (the same gate the gateway uses) so an unentitled action is refused,
    never silently funded by the removed per-business pool.

    RED on current code: neither `_reserve_usage` nor `_resolve_pg_action_usage_limit`
    references any active-entitlement *requirement* — they only read the entitlement
    to derive a per-user limit, and for a `service`/null tier return None (no per-user
    gate at all).
    """
    from plugins.takyon import app_actions

    reserve_src = inspect.getsource(app_actions._reserve_usage)
    limit_src = inspect.getsource(app_actions._resolve_pg_action_usage_limit)
    combined = reserve_src + "\n" + limit_src
    assert "_require_active_entitlement" in combined or "subscription_required" in combined, (
        "app_actions reserve path does not enforce an active-entitlement requirement "
        "(GOAL_RULES §3 gap #4). A service/null-subuser action reserve currently falls "
        "through to the per-business pool = ungated spend. It must refuse (402-equivalent) "
        "when there is no active paid entitlement, like the gateway path does."
    )


def test_service_or_null_subuser_action_limit_is_not_unbounded():
    """A service / null-subuser action reserve must NOT resolve to an unbounded
    (None) per-user limit that falls through to the per-business pool.

    `_resolve_pg_action_usage_limit` is the centralization point GOAL_RULES §3 gap #4
    asks for ("centralize per-user-limit resolution ... unify to plan-derived-or-0").
    Invariant 9: with no active paid entitlement the resolved limit is 0 (refuse),
    never None (no gate).

    RED on current code: app_actions.py:837-838 returns (tier, None) for a `service`
    tier or missing app_user_id — i.e. NO per-user cap, falling through to the pool.
    """
    from plugins.takyon import app_actions

    src = inspect.getsource(app_actions._resolve_pg_action_usage_limit)
    # The unbounded escape hatches that defeat the per-user gate must be gone:
    # an early `return ..., None` for service/null callers, or a default of None.
    returns_none = "return resolved_user_tier, None" in src
    assert not returns_none, (
        "_resolve_pg_action_usage_limit still returns an UNBOUNDED (None) per-user "
        "limit for service/null-subuser callers, which falls through to the per-business "
        "pool = ungated spend. Invariant 9 / gap #4: unify to plan-derived-or-0 — an "
        "unentitled caller resolves to 0 and is refused."
    )


# ── 4. Exact provider cost — no markup ─────────────────────────────────────────


def test_usage_pricing_has_no_markup_multiplier():
    """Bill at EXACT provider cost — margin is structural (z), never a markup on y.

    Asserts the canonical pricing/cost code applies no markup/margin multiplier:
    cost = tokens * rate / 1e6 (+ request_cost), nothing else. This holds on current
    code and is a regression guard against a future markup creeping into the budget.
    """
    from agent import usage_pricing
    from plugins.takyon import ai_provider

    cost_src = inspect.getsource(usage_pricing.estimate_usage_cost)
    provider_src = (
        inspect.getsource(ai_provider.microusd_cost)
        + "\n"
        + inspect.getsource(ai_provider.anthropic_rates_microusd_per_token)
    )
    for token in ("markup", "margin", "surcharge"):
        assert token not in cost_src.lower(), f"markup-like term {token!r} in estimate_usage_cost"
        assert token not in provider_src.lower(), f"markup-like term {token!r} in provider cost"


def test_microusd_cost_is_exact_rate_times_tokens_no_markup():
    """`microusd_cost` under the explicit per-token override must equal rate*tokens
    exactly (ceil to microUSD), proving no markup factor is folded into the bill.

    Uses the documented env override (ai_provider.py:116-129) so the assertion needs
    NO pricing table, credential, or network — it isolates the arithmetic. 1000 input
    tokens at $3/MTok = 3000 microUSD; 500 output at $15/MTok = 7500 microUSD.
    """
    import os
    from plugins.takyon import ai_provider

    # 3 microUSD/token in == $3/MTok; 15 microUSD/token out == $15/MTok.
    os.environ["TAKYON_APP_ANTHROPIC_INPUT_MICROUSD_PER_TOKEN"] = "3"
    os.environ["TAKYON_APP_ANTHROPIC_OUTPUT_MICROUSD_PER_TOKEN"] = "15"
    try:
        cost = ai_provider.microusd_cost("claude-x", input_tokens=1000, output_tokens=500)
    finally:
        del os.environ["TAKYON_APP_ANTHROPIC_INPUT_MICROUSD_PER_TOKEN"]
        del os.environ["TAKYON_APP_ANTHROPIC_OUTPUT_MICROUSD_PER_TOKEN"]
    assert cost == 1000 * 3 + 500 * 15, (
        f"microusd_cost must be exact rate*tokens with no markup; got {cost}, "
        f"expected {1000 * 3 + 500 * 15}."
    )


# ── 5. Dead operator-cap tool deleted/repurposed ───────────────────────────────


def test_business_configure_app_budget_operator_cap_tool_removed():
    """Invariant 9: "Delete or repurpose the now-dead `business_configure_app_budget`
    operator cap tool." Once the per-business pool cap is gone, an operator tool that
    sets a flat `hard_limit_microusd` cap is dead and contradicts plan-derived budget.

    RED on current code: core.py registers `business_configure_app_budget` with a
    handler that issues an `app.budget.set` op carrying `hard_limit_microusd`.
    """
    from plugins.takyon import core

    handler = getattr(core, "handle_business_configure_app_budget", None)
    if handler is None:
        # Tool fully deleted — invariant satisfied.
        return
    src = inspect.getsource(handler)
    assert "app.budget.set" not in src and "hard_limit_microusd" not in src, (
        "business_configure_app_budget still sets a flat per-business hard_limit cap "
        "(app.budget.set / hard_limit_microusd). Invariant 9 requires this operator "
        "cap tool to be deleted or repurposed once budget is plan-derived."
    )


# ── 6. Postgres-gated end-to-end refusal (needs the pg rig) ─────────────────────


@pytest.mark.usefixtures("pg_conn")
def test_unentitled_business_reserve_refused_at_zero_budget(pg_conn):
    """End-to-end at the DB layer: a business whose budget is 0 (no paid subscription
    backing it) must REFUSE any product AI reserve — the gate that becomes the 402.

    Under invariant 9 an unentitled business carries 0 budget. With the per-business
    hard cap set to 0, `reserve_usage` must raise `AppBudgetExceeded` for ANY positive
    spend (committed 0 + cost > 0). This is the concrete refusal the gateway maps to
    HTTP 402.

    Needs Postgres: exercises the real reserve-under-row-lock against `app_budgets` /
    `app_usage_events`. Marked via the repo's `pg_conn` fixture (skips when
    TAKYON_TEST_PG_DSN is unset).

    NOTE: this asserts the DESIRED behavior of a 0-budget (unentitled) business. It is
    independent of whether the $5 DEFAULT constant still exists — here we explicitly set
    the cap to 0 to model "no active paid subscription".
    """
    # An unknown business would fail the FK; create the minimal business row the
    # app_budgets FK points at.
    # businesses.owner_user_id is NOT NULL (0001 identity spine); seed an owner row
    # directly (same pattern as inv1/inv2 — no login/key-minting needed for an FK target).
    owner_id = pg_conn.execute(
        "insert into users (auth0_sub) values (%s) returning id",
        ("auth0|inv9-owner",),
    ).fetchone()[0]
    pg_conn.execute(
        "insert into businesses (slug, name, owner_user_id, mode) values (%s, %s, %s, %s) on conflict (slug) do nothing",
        ("inv9biz", "inv9biz", owner_id, "live"),
    )
    # Model "no paid subscription" as an explicit 0 hard cap.
    app_usage.set_app_budget(pg_conn, "inv9biz", hard_limit_microusd=0, status="active")

    with pytest.raises(app_usage.AppBudgetExceeded) as exc:
        app_usage.reserve_usage(
            pg_conn,
            "inv9biz",
            estimated_cost_microusd=1,  # any positive product AI spend
            reservation_key="inv9-rk-1",
            purpose="ai_generate",
            route="internal_ai_gateway",
        )
    assert exc.value.hard_limit_microusd == 0
    assert exc.value.remaining_microusd == 0
