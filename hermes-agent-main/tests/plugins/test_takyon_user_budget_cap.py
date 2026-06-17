"""Per-user monthly AI cap (ai_gateway._user_monthly_budget_microusd) — the gate that stops ONE
subuser from draining the whole business pool.

Regression for the red-team CRITICAL: the free/default tier returned 0, and since `app_user_id` is
always set, the per-user gate (`committed + estimate > 0`) fired on the FIRST call → every default
customer 402'd. The sharp edge: "fixing" it by returning None disables the per-user gate entirely,
letting one credited user drain the whole business budget. So the cap must always be positive,
never None — these tests pin that.
"""

from types import SimpleNamespace

from plugins.takyon import ai_gateway as g


def _plan(tier, included):
    return SimpleNamespace(tier=tier, included_ai_budget_microusd=included)


def test_free_tier_with_configured_allowance_is_honored():
    assert g._user_monthly_budget_microusd(_plan("free", 1_000_000)) == 1_000_000


def test_free_tier_at_zero_falls_back_to_floor_not_blocked():
    # the bug: free tier returned 0 -> 0 + estimate > 0 always -> 402 on the first request
    assert g._user_monthly_budget_microusd(_plan("free", 0)) > 0


def test_paid_tier_honors_its_configured_cap_exactly():
    assert g._user_monthly_budget_microusd(_plan("pro", 250_000)) == 250_000


def test_no_plan_returns_positive_floor():
    v = g._user_monthly_budget_microusd(None)
    assert v == g._DEFAULT_USER_MONTHLY_BUDGET_MICROUSD and v > 0


def test_never_returns_none_or_zero_for_default_tier():
    # None would disable the per-user gate (one user drains the whole pool); 0 blocks everyone.
    for plan in (None, _plan("free", 0), _plan("free", 5_000_000)):
        v = g._user_monthly_budget_microusd(plan)
        assert v is not None and v > 0
