"""Per-user monthly AI cap (ai_gateway._user_monthly_budget_microusd) — the per-subuser gate and,
post-invariant-9, the ONLY budget gate (the per-business pool cap is removed).

GOAL_RULES §3 / invariant 9 rewrote this contract. The OLD behavior floored a free/None plan to a
$0.50 free allowance so default customers were never blocked. That free-tier floor is REMOVED:
budget comes ONLY from the active PAID subscription's ``included_ai_budget_microusd`` (plan-derived-
or-0). A free / unentitled / absent plan now resolves to 0 → the reserve refuses → the gateway
surfaces 402. The resolver is still PLAN-DERIVED-OR-0 (an int, never None): None would disable the
per-user gate entirely and let one caller drain the business, so the value is always a concrete int.
This is the canonical resolver ``app_actions._plan_derived_user_limit_microusd`` delegates to.

Subuser-billing-plan WS1 (migration 0063): the resolver returns the FULL monthly allowance — the
0035-era ×7/30 weekly pro-rate is retired. The usage gate itself anchors the matching monthly
window to the customer's entitlement period, so window and allowance can never diverge per call
site (a stale pro-rated caller underspends; it cannot overspend).
"""

from types import SimpleNamespace

from plugins.takyon import ai_gateway as g


def _plan(tier, included):
    return SimpleNamespace(tier=tier, included_ai_budget_microusd=included)


def test_paid_tier_honors_its_configured_cap_full_monthly():
    assert g._user_monthly_budget_microusd(_plan("pro", 250_000)) == 250_000


def test_paid_tier_budget_is_plan_included_no_prorate_no_markup():
    # The per-user budget is the plan's y-term, verbatim — no markup, no floor top-up, and no
    # window pro-rate: the gate (migration 0063) anchors the monthly window itself, so the full
    # monthly figure is the spendable amount for the customer's own billing month.
    assert g._user_monthly_budget_microusd(_plan("pro", 4_200_000)) == 4_200_000


def test_prorate_constants_are_gone():
    # The ×7/30 weekly pro-rate is retired with the entitlement-anchored monthly window. If these
    # constants reappear, a caller-side window/limit mismatch becomes expressible again.
    assert not hasattr(g, "_USAGE_WINDOW_DAYS")
    assert not hasattr(g, "_PLAN_FUNDING_PERIOD_DAYS")
    assert not hasattr(g, "_user_weekly_budget_microusd")


def test_free_tier_grants_zero_budget_no_floor():
    # Invariant 9: a free tier funds nothing — budget comes ONLY from a PAID subscription. Even a
    # free tier carrying a configured allowance resolves to 0 (no free-tier funding of AI spend).
    assert g._user_monthly_budget_microusd(_plan("free", 0)) == 0
    assert g._user_monthly_budget_microusd(_plan("free", 1_000_000)) == 0


def test_no_plan_returns_zero_not_a_floor():
    # No active paid plan ⇒ 0 budget (reserve refuses → 402), never a positive free floor. The
    # legacy $0.50 floor constant is neutralized to 0.
    assert g._user_monthly_budget_microusd(None) == 0
    # The 0-valued shim was deleted in the monthly-only purge; absent-or-zero both mean "no
    # floor" — only a positive value would violate invariant 9.
    assert getattr(g, "_DEFAULT_USER_MONTHLY_BUDGET_MICROUSD", 0) == 0


def test_resolver_never_returns_none_only_a_concrete_int():
    # None would disable the per-user gate (one user could drain the business); the resolver must
    # always return a concrete int (0 for unentitled, the plan value for paid).
    for plan in (None, _plan("free", 0), _plan("free", 5_000_000), _plan("pro", 250_000)):
        v = g._user_monthly_budget_microusd(plan)
        assert v is not None and isinstance(v, int)
