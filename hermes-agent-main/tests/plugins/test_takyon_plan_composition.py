"""UC4 compositional-pricing engine + plan-write wiring (modularization plan §2.7, Stage 5).

Prices/budgets are DERIVED from priced components, never freehand. The engine tests are
rig-independent (no DB, no safebox) and monkeypatch `usage_pricing` to pin a known model price so
the "metered ceiling is priced from usage_pricing" claim is asserted exactly. The upsert-wiring
tests use the plain PG rig (`pg_conn`) — same posture as `test_takyon_monthly_only_pg.py`: direct
inserts + the entitlements leaf, no safebox path.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest

from agent import usage_pricing
from plugins.takyon import plan_composition as pc


# ── fixtures / helpers ─────────────────────────────────────────────────────────────


def _metered(model="claude-opus-4-8", **kw):
    kw.setdefault("provider", "anthropic")
    kw.setdefault("input_tokens", 1_000_000)
    kw.setdefault("output_tokens", 1_000_000)
    return pc.PricedComponent(
        kind="ai_allowance",
        key="ai_allowance",
        cost_basis=pc.CostBasis.metered(pc.MeteredAllowance(model=model, **kw)),
        grants={"model_allowlist": [model], "features": {"ai_chat": True}},
    )


def _fixed(key, fee_microusd, **grants):
    return pc.PricedComponent(
        kind="external_fee",
        key=key,
        cost_basis=pc.CostBasis.fixed(fee_microusd),
        grants=grants,
    )


# ── engine: derivation determinism ─────────────────────────────────────────────────


def test_compose_is_deterministic():
    comp = pc.PlanComposition(
        components=(_metered(), _fixed("shopify_store", 9_000_000, rail="shopify")),
        margin_policy=pc.MarginPolicy(margin_floor=0.30, rounding="dollar"),
    )
    a = pc.compose_plan(comp)
    b = pc.compose_plan(comp)
    assert a == b  # frozen dataclasses compare structurally; receipt included


# ── engine: metered ceiling is priced from usage_pricing (the ONE SSOT) ─────────────


def test_metered_ceiling_priced_from_usage_pricing(monkeypatch):
    """The metered COGS ceiling must come from usage_pricing, not a hardcoded number. Pin a known
    per-call cost and assert the derived ceiling equals it (µUSD), and the AI-budget grant equals
    that ceiling marked up via usage_pricing.billed_cost."""
    # A metered allowance whose realized provider cost we pin to exactly $2.00.
    def fake_estimate(model, usage, *, provider=None, base_url=None):
        assert model == "pinned-model"  # the composer routes the model through here
        return usage_pricing.CostResult(
            amount_usd=Decimal("2.00"), status="estimated", source="official_docs_snapshot", label="~$2.00"
        )

    monkeypatch.setattr(usage_pricing, "estimate_usage_cost", fake_estimate)
    monkeypatch.setattr(usage_pricing, "usage_markup_bps", lambda: 2500)  # 25%

    comp = pc.PlanComposition(
        components=(_metered(model="pinned-model", provider="custom"),),
        margin_policy=pc.MarginPolicy(margin_floor=0.0, rounding="none"),
    )
    plan = pc.compose_plan(comp)
    assert plan.total_cogs_microusd_month == 2_000_000  # $2.00 → 2_000_000 µUSD, from usage_pricing
    # budget = billed_cost(2_000_000) at 25% = 2_500_000
    assert plan.included_ai_budget_microusd == 2_500_000
    # at floor 0 the derived price equals total COGS
    assert plan.price_microusd == 2_000_000
    assert plan.price_cents == 200


def test_metered_price_reflects_real_usage_pricing_table():
    """No monkeypatch: 1M input + 1M output on opus-4-8 ($5/$25 per M) = exactly $30 COGS,
    proving the composer reads the real official-docs pricing table."""
    plan = pc.compose_plan(
        pc.PlanComposition(
            components=(_metered(),),
            margin_policy=pc.MarginPolicy(margin_floor=0.0, rounding="none"),
        )
    )
    assert plan.total_cogs_microusd_month == 30_000_000  # $30
    assert plan.included_ai_budget_microusd == usage_pricing.billed_cost(30_000_000)


def test_unpriced_model_fails_closed():
    with pytest.raises(pc.UnpricedAllowance, match="no-such-model"):
        pc.compose_plan(
            pc.PlanComposition(
                components=(_metered(model="no-such-model", provider="nope"),),
            )
        )


# ── engine: margin invariant refuses WITH figures (never clamps) ────────────────────


def test_margin_refusal_carries_exact_figures():
    """A composed price is derived to clear the floor, so `compose_plan` itself doesn't clamp.
    The refusal surface is the shared invariant `assert_price_meets_margin` (also the freehand
    gate): a fixed price that can't cover COGS at the floor REFUSES with the exact numbers."""
    # price $10 (10_000_000 µUSD), billed budget 20M → realized 16M; floor 0.4 → allowed 6M.
    with pytest.raises(pc.MarginFloorViolation) as exc:
        pc.assert_price_meets_margin(1000, 20_000_000, margin_floor=0.4)
    msg = str(exc.value)
    assert "16000000" in msg  # realized COGS
    assert "10000000" in msg  # price µUSD
    assert "0.4" in msg
    assert "never silently clamped" in msg


def test_composed_price_clears_floor_exactly():
    """Derived price meets the floor: 30M metered + 9M fixed = 39M COGS, floor 0.30, dollar
    rounding → min 39M/0.70 = 55_714_286, rounded up to $56 = 56M, realized margin >= 0.30."""
    plan = pc.compose_plan(
        pc.PlanComposition(
            components=(_metered(), _fixed("shopify_store", 9_000_000)),
            margin_policy=pc.MarginPolicy(margin_floor=0.30, rounding="dollar"),
        )
    )
    assert plan.total_cogs_microusd_month == 39_000_000
    assert plan.price_microusd == 56_000_000
    assert plan.price_cents == 5600
    assert plan.receipt["realized_margin"] >= 0.30


def test_impossible_margin_policy_rejected():
    with pytest.raises(pc.InvalidMarginPolicy):
        pc.MarginPolicy(margin_floor=1.0)
    with pytest.raises(pc.InvalidMarginPolicy):
        pc.MarginPolicy(margin_floor=-0.1)


# ── engine: fixed + per_unit composition ────────────────────────────────────────────


def test_fixed_plus_per_unit_composition():
    per_unit = pc.PricedComponent(
        kind="quota",
        key="api_calls",
        cost_basis=pc.CostBasis.per_unit(unit_cost_microusd=100, included_units=10_000),
        grants={},
    )
    fixed = _fixed("seat", 2_000_000)
    plan = pc.compose_plan(
        pc.PlanComposition(
            components=(per_unit, fixed),
            margin_policy=pc.MarginPolicy(margin_floor=0.0, rounding="cent"),
        )
    )
    # per_unit COGS = 100 * 10_000 = 1_000_000; fixed = 2_000_000; total = 3_000_000
    assert plan.total_cogs_microusd_month == 3_000_000
    assert plan.price_cents == 300
    # no metered component → no AI budget grant
    assert plan.included_ai_budget_microusd == 0


def test_floor_price_dominates_when_higher():
    """The operator can choose a price point above the margin floor; the higher wins."""
    plan = pc.compose_plan(
        pc.PlanComposition(
            components=(_fixed("seat", 1_000_000),),
            margin_policy=pc.MarginPolicy(margin_floor=0.0, rounding="none"),
            floor_price_microusd=50_000_000,  # $50, way above the $1 COGS floor
        )
    )
    assert plan.price_microusd == 50_000_000


# ── engine: receipt contents ────────────────────────────────────────────────────────


def test_receipt_carries_component_derivation():
    plan = pc.compose_plan(
        pc.PlanComposition(
            components=(_metered(), _fixed("shopify_store", 9_000_000, rail="shopify")),
            margin_policy=pc.MarginPolicy(margin_floor=0.30, rounding="dollar"),
        )
    )
    r = plan.receipt
    assert r["engine"] == "plan_composition.compose_plan"
    assert r["monthly_only"] is True
    keys = {c["key"] for c in r["components"]}
    assert keys == {"ai_allowance", "shopify_store"}
    # the shopify component's COGS appears in the derivation and total
    shopify = next(c for c in r["components"] if c["key"] == "shopify_store")
    assert shopify["cogs_microusd_month"] == 9_000_000
    assert r["total_cogs_microusd_month"] == 39_000_000
    assert r["price_cents"] == 5600
    assert any("shopify_store" in line for line in r["derivation_lines"])
    assert any("usage_pricing" in line for line in r["derivation_lines"])


def test_grants_union_across_components():
    plan = pc.compose_plan(
        pc.PlanComposition(
            components=(
                _metered(),  # grants ai_chat + opus in allowlist
                pc.PricedComponent(
                    kind="feature_rail",
                    key="analytics",
                    cost_basis=pc.CostBasis.fixed(500_000),
                    grants={"features": ["analytics"], "rail": "umami", "credits": {"ad_image": 5}},
                ),
            ),
            margin_policy=pc.MarginPolicy(margin_floor=0.0),
        )
    )
    assert plan.features == {"ai_chat": True, "analytics": True}
    assert plan.model_allowlist == ("claude-opus-4-8",)
    assert plan.rails == ("umami",)
    assert plan.credits == {"ad_image": 5}


# ── engine: input validation ────────────────────────────────────────────────────────


def test_metered_basis_rejects_stray_fixed_fields():
    with pytest.raises(pc.InvalidComponent):
        pc.CostBasis(kind="metered", allowance=pc.MeteredAllowance(model="x", input_tokens=1), fee_microusd_month=10)


def test_unknown_cost_basis_kind_rejected():
    with pytest.raises(pc.InvalidComponent):
        pc.CostBasis(kind="percentage")


def test_empty_composition_rejected():
    with pytest.raises(pc.InvalidComponent):
        pc.PlanComposition(components=())


# ── PG wiring: upsert_plan_from_composition persists DERIVED economics ───────────────

psycopg = pytest.importorskip("psycopg")

from plugins.takyon import app_entitlements as ents  # noqa: E402


def _mk_business(conn) -> str:
    uid = str(uuid.uuid4())
    conn.execute(
        "insert into users (id, auth0_sub) values (%s, %s)", (uid, f"auth0|{uuid.uuid4().hex}")
    )
    slug = f"pc-{uuid.uuid4().hex[:8]}"
    conn.execute(
        "insert into businesses (slug, name, goal, status, mode, owner_user_id) "
        "values (%s, %s, 'g', 'active', 'test', %s)",
        (slug, slug, uid),
    )
    return slug


def _composition():
    return pc.PlanComposition(
        components=(_metered(), _fixed("shopify_store", 9_000_000, rail="shopify")),
        margin_policy=pc.MarginPolicy(margin_floor=0.30, rounding="dollar"),
    )


def test_upsert_from_composition_persists_derived_numbers(pg_conn):
    slug = _mk_business(pg_conn)
    plan = ents.upsert_plan_from_composition(pg_conn, slug, "starter", _composition(), tier="paid")
    # DERIVED, not typed:
    assert plan.price_cents == 5600
    assert plan.billing_interval == "month"
    assert plan.included_ai_budget_microusd == usage_pricing.billed_cost(30_000_000)
    # derived gateway grants folded into metadata
    assert plan.metadata["features"] == {"ai_chat": True}
    assert plan.metadata["model_allowlist"] == ["claude-opus-4-8"]
    assert plan.metadata["rails"] == ["shopify"]
    # composition receipt stored under the additive metadata key
    receipt = plan.metadata[ents._COMPOSITION_METADATA_KEY]["receipt"]
    assert receipt["total_cogs_microusd_month"] == 39_000_000
    assert receipt["price_cents"] == 5600
    # round-trip read sees the same
    reread = ents.get_plan_policy(pg_conn, slug, "starter")
    assert reread.price_cents == 5600
    assert reread.metadata[ents._COMPOSITION_METADATA_KEY]["margin_floor"] == 0.30


def test_composed_write_is_monthly_only(pg_conn):
    """The composer is monthly-only by construction; the persisted row is always 'month'."""
    slug = _mk_business(pg_conn)
    plan = ents.upsert_plan_from_composition(pg_conn, slug, "starter", _composition(), tier="paid")
    assert plan.billing_interval == "month"


def test_transitional_freehand_write_still_margin_gated(pg_conn):
    """The raw price/budget input path stays, but STILL validates the shared margin invariant —
    one path, no silent second rail. A budget above 100% of price refuses fail-loud (not clamped)."""
    slug = _mk_business(pg_conn)
    with pytest.raises(ents.InvalidPlan) as exc:
        ents.upsert_plan_policy(
            pg_conn, slug, "over", tier="paid", price_cents=500,
            included_ai_budget_microusd=9_000_000,  # > cap 5_000_000
        )
    assert "9000000" in str(exc.value) and "5000000" in str(exc.value)
    assert ents.get_plan_policy(pg_conn, slug, "over") is None  # nothing written


def test_freehand_within_margin_still_writes(pg_conn):
    slug = _mk_business(pg_conn)
    plan = ents.upsert_plan_policy(
        pg_conn, slug, "ok", tier="paid", price_cents=1000, included_ai_budget_microusd=5_000_000
    )
    assert plan.price_cents == 1000
    assert plan.included_ai_budget_microusd == 5_000_000


def test_composed_reprice_of_live_plan_mints_new_version_not_mutation(pg_conn):
    """Grandfather rail preserved: a composed RE-PRICE of a plan_key with an active subscriber is
    refused in place — the caller mints a NEW plan_key version. The live plan is never mutated."""
    slug = _mk_business(pg_conn)
    # v1 plan
    v1 = ents.upsert_plan_from_composition(pg_conn, slug, "starter", _composition(), tier="paid")
    # give it an active (stripe-backed) subscriber so its economics freeze
    ent, tier = ents.grant_entitlement(
        pg_conn, slug, email="sub@example.com", tier="paid", status="active",
        plan_key="starter", stripe_subscription_id="sub_live_1",
    )
    assert tier == "paid"
    # a composed RE-PRICE of the SAME plan_key (add a second store → higher COGS → higher price)
    reprice = pc.PlanComposition(
        components=(
            _metered(),
            _fixed("shopify_store", 9_000_000, rail="shopify"),
            _fixed("shopify_store_2", 9_000_000),
        ),
        margin_policy=pc.MarginPolicy(margin_floor=0.30, rounding="dollar"),
    )
    with pytest.raises(ents.GrandfatheredPlanFrozen):
        ents.upsert_plan_from_composition(pg_conn, slug, "starter", reprice, tier="paid")
    # v1 economics untouched
    still = ents.get_plan_policy(pg_conn, slug, "starter")
    assert still.price_cents == v1.price_cents
    # minting a NEW plan_key version with the reprice succeeds (grandfathering intact)
    v2 = ents.upsert_plan_from_composition(pg_conn, slug, "starter-v2", reprice, tier="paid")
    assert v2.plan_key == "starter-v2"
    assert v2.price_cents > v1.price_cents  # extra store → higher derived price
    # v1 subscriber stays on the frozen v1 row
    assert ents.get_plan_policy(pg_conn, slug, "starter").price_cents == v1.price_cents
