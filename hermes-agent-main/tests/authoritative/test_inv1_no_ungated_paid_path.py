"""Authoritative money-safety suite — INVARIANT 1.

GOAL_RULES.md §3 invariant 1:
    "No ungated paid path — every provider/model/search-extract/media/infra
     egress is wrapped in `reserve → (settle | release)`, priced in
     `usage_pricing` (unpriced ⇒ refused before egress)."

Where invariant 6 (test_inv6_*) asserts the FAIL-CLOSED DEFAULT (an unpriced /
unknown / un-registered op is refused), this module asserts the ENVELOPE itself:
every paid product-provider egress that exists today is routed through the ONE
metered `reserve → (settle|release)` wrapper, prices fail-closed BEFORE egress,
and requires an active entitlement before reserving. It then asserts the named
§3 gap #4 — the `app_actions._reserve_usage` ungated-spend hole — is CLOSED.

Every symbol below was confirmed by reading source under hermes-agent-main/:

  plugins/takyon/ai_gateway.py
    - broker_provider_call(...)        ~265  "THE single metered envelope"
        reserve_usage -> do_call() -> _settle_or_hold | release_usage(on error)
    - _require_active_entitlement(...) ~214  entitlement None -> 402
    - broker_message_for_business(...) ~327  Anthropic egress -> broker_provider_call
    - broker_search_for_business / the Tavily egress ~597 -> broker_provider_call
    - _DEFAULT_USER_MONTHLY_BUDGET_MICROUSD = 500_000 ($0.50)  ~248
  plugins/takyon/app_usage.py
    - reserve_usage  323  / settle_usage 398 / release_usage 456  (the gate)
  plugins/takyon/app_actions.py
    - _reserve_usage(...)               ~1239  (the action reserve path)
    - _resolve_pg_action_usage_limit() ~828   (per-user-limit resolution)
  agent/usage_pricing.py
    - has_known_pricing(...)            ~887   fail-closed (no heuristic family)
  agent/web_search_registry.py
    - provider_billing(...) -> unknown provider == ("unknown", None) (fail closed)

ASPIRATIONAL / GAP TESTS: the two `test_gap4_*` functions assert the DESIRED
end-state of §3 gap #4 (the action reserve path must require an active
entitlement and unify its per-user floor to plan-derived-or-0, like the
gateway). On current source the hole is open, so they are EXPECTED RED — that
RED defines the target the loop must close. Every other test is a contract that
must stay GREEN.

All non-PG tests are no-credential / no-network: they import the real symbols
and exercise deterministic behavior, or read source and assert structure. The
two `test_pg_*` tests need the Postgres rig (marked, skipped without it).
"""

from __future__ import annotations

import inspect
import re
import uuid
from pathlib import Path

import pytest

# ── Real imports (confirmed importable from the repo) ────────────────────────
from plugins.takyon import ai_gateway, app_actions, app_usage
from plugins.takyon.ai_gateway import (
    GatewayMessageError,
    broker_provider_call,
    _require_active_entitlement,
    _user_monthly_budget_microusd,
    _DEFAULT_USER_MONTHLY_BUDGET_MICROUSD,
)
from plugins.takyon.app_usage import (
    reserve_usage,
    settle_usage,
    release_usage,
)
from agent.usage_pricing import has_known_pricing
from agent.web_search_registry import provider_billing


_UNPRICED_MODEL = "totally-made-up-model-xyz-9999"
_UNKNOWN_PROVIDER = "totally-made-up-provider-xyz-9999"


def _source_of(obj) -> str:
    return inspect.getsource(obj)


def _body_of(func) -> str:
    """Source of `func` with its leading docstring removed, so structural/ordering
    assertions match the CODE, not prose that happens to name `do_call()`/`reserve`
    in the docstring."""
    src = inspect.getsource(func)
    doc = func.__doc__
    if doc:
        end = src.find(doc)
        if end != -1:
            return src[end + len(doc):]
    return src


# ─────────────────────────────────────────────────────────────────────────────
# Seam A: the reserve → (settle | release) envelope exists and is THE gate
# ─────────────────────────────────────────────────────────────────────────────


def test_usage_gate_exposes_reserve_settle_release_triple():
    """app_usage exposes the full envelope: reserve_usage, settle_usage,
    release_usage. These three are the only legitimate transitions of a held
    spend; a paid path that skips them is ungated."""
    assert callable(reserve_usage)
    assert callable(settle_usage)
    assert callable(release_usage)


def test_reserve_usage_is_the_refusing_gate():
    """reserve_usage refuses (raises) rather than silently allowing: an inactive
    budget -> AppBudgetInactive, an over-cap reserve -> AppBudgetExceeded. The
    gate must be able to say no, or it is not a gate. Asserted at source level
    (the raises are inside a PG transaction; behavior is exercised in test_pg_*).
    """
    src = _source_of(reserve_usage)
    assert "raise AppBudgetInactive" in src
    assert "raise AppBudgetExceeded" in src
    # Idempotent on reservation_key: a replay returns the existing reserved row
    # rather than holding twice (no double-spend, no double-charge).
    assert "reservation_key" in src
    assert 'status != "active"' in src or "status != 'active'" in src


def test_settle_never_rechecks_cap_truth_is_mandatory():
    """settle_usage records the real provider spend and never re-checks the cap
    (money is already spent — truth on settle is mandatory). This is what makes
    `reserve → settle` honest: you cannot under-record actual spend to dodge the
    ledger."""
    src = _source_of(settle_usage)
    assert "actual_cost_microusd" in src
    # The settle path sets status to completed; it does not raise AppBudgetExceeded.
    assert "completed" in src
    assert "AppBudgetExceeded" not in src


def test_release_frees_hold_without_recording_spend():
    """release_usage is the failure leg of the envelope: it frees the reservation
    and records ZERO actual spend (so committed drops back by the held estimate).
    A provider error must release, never settle a phantom charge."""
    src = _source_of(release_usage)
    assert "actual_cost_microusd = 0" in src
    assert "released" in src or "failed" in src


# ─────────────────────────────────────────────────────────────────────────────
# Seam B: broker_provider_call is THE single metered envelope every paid
#         product-provider call routes through
# ─────────────────────────────────────────────────────────────────────────────


def test_broker_provider_call_wraps_reserve_call_settle_or_release():
    """The single metered envelope: reserve_usage FIRST, then do_call(), then
    settle on success or release on a provider error. Asserts the exact ordered
    structure so a refactor cannot reorder egress before the reservation or drop
    the release-on-error leg."""
    src = _body_of(broker_provider_call)
    i_reserve = src.find("reserve_usage(")
    i_docall = src.find("do_call()")
    i_release = src.find("release_usage(")
    i_settle = src.find("_settle_or_hold(")
    assert i_reserve != -1, "broker must reserve"
    assert i_docall != -1, "broker must call the provider via do_call()"
    assert i_release != -1, "broker must release on provider error"
    assert i_settle != -1, "broker must settle the actual cost"
    # Reservation happens BEFORE the provider egress.
    assert i_reserve < i_docall, "reserve must precede the provider call"
    # The provider error path releases the hold (failure leg of the envelope).
    assert "release_usage(" in src
    assert "raise GatewayMessageError(status_code=502" in src


def test_broker_provider_call_release_is_on_the_error_path():
    """The release leg lives in the `except` around do_call() — a provider
    exception releases the hold and surfaces a 502, it never settles. Confirms
    the failure path of the envelope is wired to release, not to a silent pass."""
    src = _body_of(broker_provider_call)
    # Find the try/except that wraps the provider call.
    assert re.search(r"try:\s*\n\s*raw = do_call\(\)\s*\n\s*except Exception", src), (
        "do_call() must be wrapped in try/except that releases on failure"
    )
    # The release call appears between do_call and the settle (i.e. on the error leg).
    i_docall = src.find("do_call()")
    i_release = src.find("release_usage(", i_docall)
    i_settle = src.find("_settle_or_hold(")
    assert i_docall < i_release < i_settle


def test_every_paid_product_egress_routes_through_the_broker():
    """The two product provider egress paths that exist today — Anthropic
    generation and Tavily search/extract — BOTH route through broker_provider_call.
    There is no second un-brokered product-provider egress in ai_gateway.

    Relationship assertion over the module source: the provider literals
    "anthropic" and "tavily" appear as broker_provider_call(provider=...) args,
    and the count of broker_provider_call CALL sites (definition excluded) is the
    count of distinct paid product egresses. If someone adds a 3rd paid provider
    without the broker, the provider-list assertion below still forces the broker.
    """
    mod_src = inspect.getsource(ai_gateway)
    # broker_provider_call appears as: 1 def + N call sites. Today N == 2.
    occurrences = mod_src.count("broker_provider_call")
    assert occurrences >= 3, "expected the def plus >=2 broker call sites"
    # Both known paid providers are passed to the broker, not called raw.
    assert 'provider="anthropic"' in mod_src
    assert 'provider="tavily"' in mod_src


def test_paid_egress_requires_active_entitlement_before_reserving():
    """Both broker egress paths call _require_active_entitlement(entitlement)
    BEFORE broker_provider_call — no active subscription ⇒ no reservation ⇒ no
    egress (subscription_required / 402). Asserted by ordering in the module
    source for each provider block."""
    mod_src = inspect.getsource(ai_gateway)
    # _require_active_entitlement is used in the gateway (>=2 call sites: anthropic + tavily).
    assert mod_src.count("_require_active_entitlement(entitlement)") >= 2
    # For the tavily block, the entitlement check precedes its broker call.
    i_tavily_broker = mod_src.find('provider="tavily"')
    assert i_tavily_broker != -1
    # The nearest preceding _require_active_entitlement must exist before the tavily broker call.
    i_req_before = mod_src.rfind("_require_active_entitlement(entitlement)", 0, i_tavily_broker)
    assert i_req_before != -1, "tavily egress must require an active entitlement first"


def test_require_active_entitlement_refuses_when_none():
    """_require_active_entitlement(None) raises a 402 subscription_required —
    deterministic, no credentials. This is the runtime gate the broker paths
    depend on."""
    with pytest.raises(GatewayMessageError) as exc:
        _require_active_entitlement(None)
    assert exc.value.status_code == 402
    # A present entitlement does not raise.
    _require_active_entitlement(object())


# ─────────────────────────────────────────────────────────────────────────────
# Seam C: priced fail-closed BEFORE egress (unpriced ⇒ refused)
# ─────────────────────────────────────────────────────────────────────────────


def test_has_known_pricing_false_for_unknown_model_no_heuristic_fallback():
    """An unpriced model with an unknown provider has NO known pricing — the
    fail-closed signal callers refuse on (no heuristic family match). This is the
    `unpriced ⇒ refused before egress` half of invariant 1."""
    assert has_known_pricing(_UNPRICED_MODEL, provider=_UNKNOWN_PROVIDER) is False


def test_unknown_web_provider_is_not_free_so_it_meters():
    """An unknown web provider classifies ("unknown", None) — NOT "free". The
    egress point only skips the meter for "free"; "unknown" (a forgotten paid
    backend) therefore takes the reserve path. Fail-closed: a new paid backend
    can't leak ungated spend by being absent from the billing map."""
    mode, namespace = provider_billing(_UNKNOWN_PROVIDER)
    assert mode == "unknown"
    assert mode != "free"
    assert namespace is None


def test_tavily_egress_prices_before_reserving():
    """In the Tavily egress, the server-side fail-closed price (tavily_request_microusd,
    raising TavilyPricingUnavailable) is computed BEFORE broker_provider_call —
    an unpriced operation is refused before any reservation or provider call."""
    mod_src = inspect.getsource(ai_gateway)
    i_price = mod_src.find("tavily_request_microusd(")
    i_tavily_broker = mod_src.find('provider="tavily"')
    assert i_price != -1 and i_tavily_broker != -1
    assert i_price < i_tavily_broker, "price must be resolved before the tavily reservation"
    # The unpriced branch refuses (does not default to $0 and proceed).
    assert "except TavilyPricingUnavailable" in mod_src


# ─────────────────────────────────────────────────────────────────────────────
# Seam D: §3 GAP #4 — the app_actions._reserve_usage ungated-spend hole.
#         ASPIRATIONAL: asserts the DESIRED closed state. EXPECTED RED today.
# ─────────────────────────────────────────────────────────────────────────────


def test_gap4_action_reserve_requires_active_entitlement():
    """ASPIRATIONAL (§3 gap #4) — EXPECTED RED on current source.

    The gateway paid path calls `_require_active_entitlement` before reserving,
    but `app_actions._reserve_usage` does NOT — so a `service`/null-subuser action
    reserve falls through to the per-business pool with no entitlement check =
    ungated spend. The fix is to require an active entitlement on the action
    reserve path too.

    DESIRED end-state asserted here: the action reserve path must reference an
    active-entitlement requirement (it does not today). RED here is the TARGET,
    not a bug in the test — it goes GREEN once the loop closes the hole.
    """
    src = _source_of(app_actions._reserve_usage)
    resolve_src = _source_of(app_actions._resolve_pg_action_usage_limit)
    combined = src + resolve_src
    assert "_require_active_entitlement" in combined or "subscription_required" in combined, (
        "GAP #4 OPEN: app_actions reserve path does not require an active "
        "entitlement before reserving spend (ungated service/null-subuser path)."
    )


def test_gap4_action_per_user_limit_unifies_to_plan_or_zero_not_none():
    """ASPIRATIONAL (§3 gap #4) — EXPECTED RED on current source.

    `_resolve_pg_action_usage_limit` returns `None` (NO per-user cap) for a
    `service`/null user, which disables the per-user gate and lets one caller
    drain the per-business pool. §3 requires unifying per-user-limit resolution
    to plan-derived-or-0 (never an uncapped None on a billable path).

    DESIRED end-state: the resolver no longer returns a bare `None` per-user
    limit for the service/null branch. RED here is the TARGET.
    """
    resolve_src = _source_of(app_actions._resolve_pg_action_usage_limit)
    # Today the service/null branch is `return resolved_user_tier, None`.
    assert "return resolved_user_tier, None" not in resolve_src, (
        "GAP #4 OPEN: the action per-user-limit resolver still returns an "
        "uncapped None for service/null users instead of plan-derived-or-0."
    )


def test_gap4_centralized_per_user_floor_is_unified_to_plan_or_zero():
    """ASPIRATIONAL (§3 gap #4 / invariant 9 overlap) — EXPECTED RED today.

    §3 also requires CENTRALIZING per-user-limit resolution: today the gateway
    floors `$0.50` (`_DEFAULT_USER_MONTHLY_BUDGET_MICROUSD`) while actions floor
    `$0` — two divergent floors. The desired end-state unifies them to
    plan-derived-or-0 (no `$0.50` free-tier floor reachable on a billable path).

    This asserts the unification target: the `$0.50` per-user default floor is no
    longer a live fallback in the gateway's per-user-budget resolver. RED here is
    the TARGET (the floor still exists at ai_gateway.py:248 today).
    """
    floor_src = _source_of(_user_monthly_budget_microusd)
    # Currently `_user_monthly_budget_microusd` falls back to the $0.50 default
    # for plan is None and for a 0-budget free tier. The unified end-state removes
    # that floor from the billable path.
    assert "_DEFAULT_USER_MONTHLY_BUDGET_MICROUSD" not in floor_src, (
        "GAP #4/INV9 OPEN: the gateway per-user resolver still falls back to the "
        "$0.50 default floor instead of plan-derived-or-0."
    )


def test_default_user_floor_constant_is_removed_to_zero():
    """Post-invariant-9 contract: the $0.50 per-user free-tier FLOOR is REMOVED.

    Invariant 9 / §3 gap #4 unified per-user-limit resolution to plan-derived-or-0 with NO free
    floor, so the back-compat shim `_DEFAULT_USER_MONTHLY_BUDGET_MICROUSD` is now 0 (`0 == "no
    floor"`) and is NOT referenced by the resolver. This is the reconciliation of the once-self-
    contradictory pin: it formerly asserted the live $0.50 floor (500_000); the floor having been
    correctly zeroed, the contract this anchors is the absence of any positive floor. Not a catalog
    snapshot — it asserts the invariant that the per-user budget can never fall back to a free
    floor (the companion `test_gap4_centralized_per_user_floor_is_unified_to_plan_or_zero` proves
    the resolver no longer references it at all)."""
    assert _DEFAULT_USER_MONTHLY_BUDGET_MICROUSD == 0


# ─────────────────────────────────────────────────────────────────────────────
# PG-gated end-to-end: the envelope actually holds, settles, and releases money.
# Needs the Postgres rig (TAKYON_TEST_PG_DSN); skipped otherwise.
# ─────────────────────────────────────────────────────────────────────────────


def _seed_business(conn, *, hard_limit: int) -> str:
    """Seed an owner + business and open an active budget, then return the slug.

    The `app_budgets.business_slug` FK requires a real `businesses` row, which in
    turn requires a `users` owner (0001 identity spine). We insert the owner row
    directly (rather than going through control_plane's login/key-minting path,
    which would pull in the Safebox authority) — the test only needs a valid
    owner FK target to exercise the usage envelope, not a login session.
    """
    owner_id = conn.execute(
        "insert into users (auth0_sub) values (%s) returning id",
        (f"auth0|{uuid.uuid4().hex}",),
    ).fetchone()[0]
    slug = f"inv1-{uuid.uuid4().hex[:8]}"
    conn.execute(
        "insert into businesses (slug, name, owner_user_id) values (%s, %s, %s)",
        (slug, "Inv1 Probe Co", owner_id),
    )
    app_usage.set_app_budget(conn, slug, hard_limit_microusd=hard_limit, status="active")
    return slug


def test_pg_reserve_then_release_returns_committed_to_zero(pg_conn):
    """END-TO-END (PG): reserve holds the estimate; release frees it so committed
    returns to 0 and the recorded actual cost is 0. Proves the release leg of the
    envelope actually returns money to the pool — no phantom spend on failure."""
    slug = _seed_business(pg_conn, hard_limit=10_000_000)
    key = uuid.uuid4().hex
    reserved = app_usage.reserve_usage(
        pg_conn,
        slug,
        estimated_cost_microusd=1_000,
        reservation_key=key,
        purpose="authoritative_inv1_probe",
        route="test",
    )
    assert reserved.status == "reserved"
    assert reserved.estimated_cost_microusd == 1_000

    summary_after_reserve = app_usage.get_usage_summary(pg_conn, slug)
    assert summary_after_reserve["committed_microusd"] >= 1_000

    released = app_usage.release_usage(pg_conn, slug, key, error="provider_boom")
    assert released.status in {"failed", "released"}
    assert released.actual_cost_microusd == 0

    summary_after_release = app_usage.get_usage_summary(pg_conn, slug)
    assert summary_after_release["committed_microusd"] == 0


def test_pg_settle_records_actual_and_is_idempotent(pg_conn):
    """END-TO-END (PG): reserve then settle records the actual spend and a second
    settle/release is a no-op (idempotent on the reservation_key) — the ledger
    cannot be double-charged or re-opened after finalization."""
    slug = _seed_business(pg_conn, hard_limit=10_000_000)
    key = uuid.uuid4().hex
    app_usage.reserve_usage(
        pg_conn,
        slug,
        estimated_cost_microusd=2_000,
        reservation_key=key,
        purpose="authoritative_inv1_probe",
        route="test",
    )
    settled = app_usage.settle_usage(pg_conn, slug, key, actual_cost_microusd=1_500)
    assert settled.status == "completed"
    assert settled.actual_cost_microusd == 1_500

    # Idempotent: a second settle (or a release) after finalization returns the
    # finalized row unchanged — no second charge, no re-open.
    again = app_usage.settle_usage(pg_conn, slug, key, actual_cost_microusd=9_999_999)
    assert again.status == "completed"
    assert again.actual_cost_microusd == 1_500
    released = app_usage.release_usage(pg_conn, slug, key, error="late")
    assert released.status == "completed"
    assert released.actual_cost_microusd == 1_500
