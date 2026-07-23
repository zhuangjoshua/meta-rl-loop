from __future__ import annotations

import pytest

from sim.market_v2_adapter import (
    MarketV2AdapterError,
    MarketV2Backend,
    _ensure_timeline,
    _policy_from_spec,
)
from sim.population_market_v2 import FUNNEL, MODEL_PATH, evaluate_policies
from sim.tier_b_experiment import _design_schema, _normalize_design
from sim.tier_b_market import _load_world
from sim.test_population_market_v2 import LANDING, CohortJudge

WORLD = 71
PUBLIC_RECEIPT_KEYS = {
    "schema", "world", "periods", "rows", "funnel", "spend", "revenue", "roas",
    "periods_independent", "revenue_definition",
}


def loop_spec(*, with_timeline: bool = False) -> dict:
    ads = []
    for index in range(1, 4):
        ad = {
            "id": f"ad-{index}",
            "headline": f"See missing answers resolve {index}",
            "message": "Watch one incomplete intake trigger a reminder and become a ready client record.",
            "visual": "Screen recording of a reminder resolving an incomplete client intake.",
            "call_to_action": "START_TRIAL",
            "proof": "benefit",
            "named_story": False,
            "demo": True,
        }
        if with_timeline:
            ad["duration_seconds"] = 12
            ad["scenes"] = [
                {"start_second": 0, "end_second": 6, "content": "Large text: Missing client answer; incomplete record visible."},
                {"start_second": 6, "end_second": 12, "content": "Status changes from Waiting on Client to Ready; offer appears."},
            ]
        ads.append(ad)
    return {
        "iteration": 1,
        "policy": "v0",
        "landing_page": LANDING,
        "ads": ads,
        "campaigns": [{
            "id": "sales-broad",
            "objective": "sales",
            "mode": "fixed",
            "audience": "broad",
            "budget": 200.0,
            "ad_ids": [ad["id"] for ad in ads],
        }],
    }


def backend(judge=None) -> MarketV2Backend:
    return MarketV2Backend(
        world_number=WORLD,
        judge=judge or CohortJudge(),
        budget=200.0,
        periods=4,
        concurrency=1,
        model_path=MODEL_PATH,
    )


def test_adapter_receipt_matches_direct_engine_call():
    market = backend()
    _, platform = _load_world(WORLD)
    validated = market.validate(loop_spec(with_timeline=True), platform)
    receipt = market.simulate(seed=7, raw_spec=validated, expected=True)

    direct = evaluate_policies(
        world_number=WORLD,
        raw_policies=[_policy_from_spec(validated)],
        judge=CohortJudge(),
        periods=4,
        concurrency=1,
        expected_budget=200.0,
        model_path=MODEL_PATH,
    )[0]
    assert receipt["funnel"] == direct["funnel"]
    assert receipt["spend"] == direct["spend"]
    assert receipt["revenue"] == direct["first_payment_revenue"]
    assert receipt["roas"] == direct["first_payment_roas"]
    assert receipt["rows"] == direct["rows"]


def test_adapter_synthesizes_timeline_only_when_missing():
    market = backend()
    _, platform = _load_world(WORLD)
    validated = market.validate(loop_spec(with_timeline=False), platform)
    for ad in validated["ads"]:
        assert ad["timeline_synthesized"] is True
        assert ad["duration_seconds"] > 0
        assert ad["scenes"][0]["content"].startswith("Static image ad")
        assert ad["visual"] in ad["scenes"][0]["content"]

    authored = market.validate(loop_spec(with_timeline=True), platform)
    for ad in authored["ads"]:
        assert "timeline_synthesized" not in ad
        assert len(ad["scenes"]) == 2


def test_ensure_timeline_passthrough_is_identity():
    ad = loop_spec(with_timeline=True)["ads"][0]
    assert _ensure_timeline(dict(ad)) == ad


def test_receipts_contain_no_hidden_fields():
    market = backend()
    _, platform = _load_world(WORLD)
    validated = market.validate(loop_spec(with_timeline=True), platform)
    for receipt in (
        market.simulate(seed=7, raw_spec=validated),
        market.simulate(seed=7, raw_spec=validated, expected=True),
    ):
        assert set(receipt) <= PUBLIC_RECEIPT_KEYS
        assert receipt["periods_independent"] is True
        assert receipt["revenue_definition"] == "first_payment"
        for row in receipt["rows"]:
            assert set(row) <= {"ad", "spend", *FUNNEL}


def test_sampled_receipt_uses_engine_sampling_shape():
    market = backend()
    _, platform = _load_world(WORLD)
    validated = market.validate(loop_spec(with_timeline=True), platform)
    receipt = market.simulate(seed=11, raw_spec=validated)
    assert receipt["spend"] == 200.0 * 4
    for name in FUNNEL:
        assert name in receipt["funnel"]
    # Sampled counts are integers, monotone down the funnel per ad.
    for row in receipt["rows"]:
        counts = [row[name] for name in FUNNEL]
        assert all(float(value).is_integer() for value in counts)
        assert all(a >= b for a, b in zip(counts, counts[1:]))


def test_validate_rejects_wrong_budget_total():
    market = backend()
    _, platform = _load_world(WORLD)
    spec = loop_spec(with_timeline=True)
    spec["campaigns"][0]["budget"] = 150.0
    with pytest.raises(MarketV2AdapterError):
        market.validate(spec, platform)


def test_design_schema_requires_timeline_only_in_v2_mode():
    legacy = _design_schema()
    v2 = _design_schema(include_timeline=True)
    legacy_ad = legacy["properties"]["ads"]["items"]
    v2_ad = v2["properties"]["ads"]["items"]
    assert "duration_seconds" not in legacy_ad["properties"]
    assert "scenes" not in legacy_ad["properties"]
    assert "duration_seconds" in v2_ad["required"]
    assert "scenes" in v2_ad["required"]


def test_normalize_design_default_still_uses_legacy_validator():
    _, platform = _load_world(WORLD)
    payload = {
        "design_thesis": "test",
        "ads": loop_spec(with_timeline=False)["ads"],
        "campaigns": loop_spec(with_timeline=False)["campaigns"],
    }
    _, spec = _normalize_design(
        payload, iteration=1, policy_version=0, landing_page=LANDING,
        platform=platform, budget=200.0,
    )
    # The legacy validator marks each ad with its judge-visible "actual" text.
    assert all("actual" in ad for ad in spec["ads"])
    assert all("timeline_synthesized" not in ad for ad in spec["ads"])


def test_purge_invalid_judge_cache_removes_only_violations(tmp_path):
    from sim.market_v2_adapter import _purge_invalid_judge_cache
    from sim.population_market_v2 import MODEL_VERSION
    import json as _json

    allowed = {"profile_a", "profile_b"}
    good = {"ad_assessments": [
        {"helped_microprofiles": ["profile_a"], "rejected_microprofiles": ["profile_b"]},
    ]}
    overlapping = {"ad_assessments": [
        {"helped_microprofiles": ["profile_a"], "rejected_microprofiles": ["profile_a"]},
    ]}
    unknown = {"ad_assessments": [
        {"helped_microprofiles": ["mystery"], "rejected_microprofiles": []},
    ]}
    unrelated = {"something_else": True}
    (tmp_path / f"{MODEL_VERSION}-p1-aaa.json").write_text(_json.dumps(good))
    (tmp_path / f"{MODEL_VERSION}-p2-bbb.json").write_text(_json.dumps(overlapping))
    (tmp_path / f"{MODEL_VERSION}-p3-ccc.json").write_text(_json.dumps(unknown))
    (tmp_path / "other-namespace-ddd.json").write_text(_json.dumps(overlapping))
    (tmp_path / f"{MODEL_VERSION}-p4-eee.json").write_text(_json.dumps(unrelated))

    removed = _purge_invalid_judge_cache(tmp_path, allowed)
    assert sorted(removed) == [f"{MODEL_VERSION}-p2-bbb.json", f"{MODEL_VERSION}-p3-ccc.json"]
    assert (tmp_path / f"{MODEL_VERSION}-p1-aaa.json").exists()
    assert (tmp_path / "other-namespace-ddd.json").exists()
    assert (tmp_path / f"{MODEL_VERSION}-p4-eee.json").exists()
