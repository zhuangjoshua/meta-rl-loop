from __future__ import annotations

import copy
import json

import pytest

from sim.llm_client import LLMConfig, LLMStats
from sim.population_market_v2 import (
    AD_SIGNALS,
    CHOICE_CALIBRATION_PATH,
    FUNNEL,
    MODEL_PATH,
    PRODUCT_STATES,
    PopulationMarketError,
    evaluate_policies,
    load_choice_calibration,
    load_population_model,
    load_variation_model,
    load_subscription_economics,
    subscription_roas_scenarios,
    public_receipt,
    validate_policy,
)
from sim.tier_b_market import SIM_ROOT, _load_world


LANDING = (SIM_ROOT / "formflow-landing-page.md").read_text(encoding="utf-8")


def policy():
    ads = []
    for index in range(1, 4):
        ads.append({
            "id": f"ad-{index}",
            "headline": f"See missing answers resolve {index}",
            "primary_text": "Watch one incomplete intake trigger a reminder and become a ready client record.",
            "call_to_action": "START_TRIAL",
            "duration_seconds": 12,
            "scenes": [
                {"start_second": 0, "end_second": 3, "content": "Large text: Missing client answer; incomplete record visible."},
                {"start_second": 3, "end_second": 8, "content": "Formflow reminder sends and the client fills the secure link."},
                {"start_second": 8, "end_second": 12, "content": "Status changes from Waiting on Client to Ready; offer appears."},
            ],
        })
    return {
        "id": "test-policy",
        "landing_page": LANDING,
        "ads": ads,
        "campaigns": [{
            "id": "sales-broad",
            "objective": "sales",
            "mode": "fixed",
            "audience": "broad",
            "budget": 200,
            "ad_ids": [ad["id"] for ad in ads],
            "ad_weights": {"ad-1": 0.5, "ad-2": 0.3, "ad-3": 0.2},
        }],
    }


class CohortJudge:
    def __init__(self, *, invalid_signal=False):
        self.config = LLMConfig(provider="codex", model="test-consumer")
        self.stats = LLMStats()
        self.calls = 0
        self.invalid_signal = invalid_signal

    def complete(self, *, prompt, schema, cache_namespace):
        self.calls += 1
        block = prompt.split("<policy_cohorts_json>\n", 1)[1].split("\n</policy_cohorts_json>", 1)[0]
        policies = json.loads(block)
        parent_block = prompt.split("<parent_population_json>\n", 1)[1].split("\n</parent_population_json>", 1)[0]
        parent = json.loads(parent_block)
        products = []
        ads = []
        frozen_product_states = (
            schema["properties"]["product_assessments"].get("maxItems") == 0
        )
        if not frozen_product_states:
            for child in parent["children"]:
                ineligible = child.get("purchase_eligibility") == "none"
                products.append({
                    "child_id": child["id"],
                    "current_need": "none" if ineligible else "recurring",
                    "product_fit": "none" if ineligible else "strong",
                    "authority": "none" if ineligible else "direct",
                    "budget": "unavailable" if ineligible else "available",
                    "switching_cost": "prohibitive" if ineligible else "moderate",
                    "implementation_capacity": "blocked" if ineligible else "available",
                    "product_experience": "negative" if ineligible else "useful",
                    "reason": "Fixed product-person state.",
                })
        for current in policies:
            for ad in current["ads"]:
                for child in parent["children"]:
                    values = {
                        "attention": "engaging",
                        "comprehension": "clear",
                        "relevance": "direct",
                        "credibility": "demonstrated",
                        "expectation_match": "aligned",
                        "trial_motivation": "consider",
                    }
                    if self.invalid_signal:
                        values["attention"] = "eleven_out_of_ten"
                    ads.append({
                        "policy_id": current["id"],
                        "child_id": child["id"],
                        "ad_id": ad["id"],
                        **values,
                        "helped_microprofiles": [],
                        "rejected_microprofiles": [],
                        "content_seen": "Opening and demonstrated state change.",
                        "primary_blocker": "Offer friction.",
                    })
        return {
            "product_assessments": products,
            "ad_assessments": ads,
            "population_summary": "Test population.",
        }


def test_model_has_exact_hierarchy_and_shares():
    model = load_population_model(MODEL_PATH)
    assert len(model["populations"]) == 10
    assert sum(parent["share"] for parent in model["populations"]) == pytest.approx(1)
    assert all(sum(child["share"] for child in parent["children"]) == pytest.approx(1)
               for parent in model["populations"])
    assert len({child["id"] for parent in model["populations"] for child in parent["children"]}) == 39


def test_timeline_is_required():
    _, platform = _load_world(71)
    broken = policy()
    del broken["ads"][0]["scenes"]
    with pytest.raises(PopulationMarketError, match="timeline"):
        validate_policy(broken, platform, expected_budget=200)


def test_exactly_ten_parent_evaluations_and_hidden_public_receipt():
    judge = CohortJudge()
    result = evaluate_policies(
        world_number=71,
        raw_policies=[policy()],
        judge=judge,
        periods=2,
        concurrency=4,
        expected_budget=200,
    )[0]
    assert judge.calls == 10
    assert result["population_evaluations"] == 10
    assert result["funnel"]["purchases"] > 0
    assert len(result["hidden_audit"]["product_assessments"]) == 39
    assert all("policy_id" not in row for row in result["hidden_audit"]["product_assessments"])
    assert "calibration_adjustments" not in result
    receipt = public_receipt(result)
    assert "hidden_audit" not in receipt
    assert "population_summaries" not in json.dumps(receipt)
    assert all(row["purchases"] <= row["activations"] <= row["signups"] for row in receipt["rows"])


def test_invalid_semantic_signal_fails_closed():
    with pytest.raises(PopulationMarketError, match="invalid attention"):
        evaluate_policies(
            world_number=71,
            raw_policies=[policy()],
            judge=CohortJudge(invalid_signal=True),
            periods=1,
            concurrency=2,
            expected_budget=200,
        )


def test_market_role_model_contains_real_nonbuyers_and_delivery():
    model = load_population_model(SIM_ROOT / "population-model-v3.json")
    assert model["schema"] == "takyon.consumer-population.v3-market-roles"
    assert model["populations"][0]["id"] == "out_of_market_general_adults"
    assert model["populations"][0]["share"] == pytest.approx(0.71)
    assert all(parent.get("delivery") for parent in model["populations"])
    assert any(
        child.get("purchase_eligibility") == "none"
        for parent in model["populations"]
        for child in parent["children"]
    )


def test_ineligible_children_never_purchase():
    result = evaluate_policies(
        world_number=71,
        raw_policies=[policy()],
        judge=CohortJudge(),
        periods=2,
        concurrency=2,
        expected_budget=200,
    )[0]
    ineligible = {
        child["id"]
        for parent in load_population_model()["populations"]
        for child in parent["children"]
        if child.get("purchase_eligibility") == "none"
    }
    assert all(
        row["purchases"] == 0
        for row in result["hidden_audit"]["cohort_rows"]
        if row["child_id"] in ineligible
    )


def test_choice_calibration_has_no_rate_caps_or_lift_bounds():
    calibration = load_choice_calibration(CHOICE_CALIBRATION_PATH)
    serialized = json.dumps(calibration)
    assert "conditional_rate_caps" not in serialized
    assert "lift_bound" not in serialized
    assert set(calibration["stage_intercepts"]) == set(FUNNEL[1:])
    assert set(calibration["product_state_values"]) == set(PRODUCT_STATES)
    assert set(AD_SIGNALS) <= set(calibration["ad_signal_values"])


def test_persistent_people_cannot_purchase_twice_across_ads():
    result = evaluate_policies(
        world_number=71,
        raw_policies=[policy()],
        judge=CohortJudge(),
        periods=8,
        concurrency=2,
        expected_budget=200,
    )[0]
    rows = result["hidden_audit"]["cohort_rows"]
    for group in result["hidden_audit"]["persistence_groups"]:
        purchases = sum(
            row["purchases"]
            for row in rows
            if row["parent_id"] == group["parent_id"]
            and row["child_id"] == group["child_id"]
            and row["ad_id"] in group["impressions_by_ad"]
        )
        assert purchases <= group["persistent_pool_size"] + 1e-6
    assert all(row["persistent_unique_reach"] <= row["exposed"] + 1e-6 for row in rows)


def test_statistical_subpopulation_states_replace_judge_product_guess(tmp_path):
    model = copy.deepcopy(load_population_model(SIM_ROOT / "population-model-v3.json"))
    model["schema"] = "takyon.consumer-population.v5-adaptive-statistical"
    for parent in model["populations"]:
        for child in parent["children"]:
            eligible = child.get("purchase_eligibility") != "none"
            child["decision_strata"] = [
                {
                    "id": f"{child['id']}_ready",
                    "share": 0.35,
                    "current_need": "recurring" if eligible else "none",
                    "product_fit": "strong" if eligible else "none",
                    "authority": "direct" if eligible else "none",
                    "budget": "available" if eligible else "unavailable",
                    "switching_cost": "low" if eligible else "prohibitive",
                    "implementation_capacity": "available" if eligible else "blocked",
                    "product_experience": "strong" if eligible else "negative",
                    "rationale": "The ready statistical stratum has every fixed prerequisite represented.",
                },
                {
                    "id": f"{child['id']}_blocked",
                    "share": 0.65,
                    "current_need": "occasional" if eligible else "none",
                    "product_fit": "partial" if eligible else "none",
                    "authority": "influence" if eligible else "none",
                    "budget": "constrained" if eligible else "unavailable",
                    "switching_cost": "high",
                    "implementation_capacity": "constrained" if eligible else "blocked",
                    "product_experience": "limited",
                    "rationale": "The blocked statistical stratum lacks enough authority or practical readiness.",
                },
            ]
    model_path = tmp_path / "statistical-population.json"
    model_path.write_text(json.dumps(model), encoding="utf-8")
    judge = CohortJudge()
    result = evaluate_policies(
        world_number=71,
        raw_policies=[policy()],
        judge=judge,
        periods=2,
        concurrency=2,
        expected_budget=200,
        model_path=model_path,
    )[0]
    assert judge.calls == 10
    assert result["funnel"]["purchases"] > 0
    assert all(
        row["source"] == "frozen_subpopulation_distribution"
        for row in result["hidden_audit"]["product_assessments"]
    )
    assert all(
        row["choice_inputs"]["product"]["source"]
        == "frozen_subpopulation_distribution"
        for row in result["hidden_audit"]["cohort_rows"]
    )


def test_human_variation_is_cross_cutting_and_role_specific():
    variation = load_variation_model(SIM_ROOT / "human-variation-v4.json")
    assert variation is not None
    assert len(variation["human_microprofiles"]) == 12
    assert sum(value["share"] for value in variation["human_microprofiles"]) == pytest.approx(1)
    assert "anti_ai_copy_realist" in {
        value["id"] for value in variation["human_microprofiles"]
    }
    assert set(variation["role_factual_preferences"]) == {
        parent["id"]
        for parent in load_population_model(SIM_ROOT / "population-model-v3.json")["populations"]
    }


def test_subscription_roas_uses_retained_cohort_revenue():
    economics = load_subscription_economics(SIM_ROOT / "subscription-economics-v1.json")
    result = subscription_roas_scenarios(
        purchases=100,
        spend=1000,
        price=29,
        economics=economics,
    )
    assert result is not None
    reference = result["scenarios"]["reference_10pct"]
    assert reference["horizons"]["month_1"]["cohort_revenue_roas"] == pytest.approx(2.9)
    assert reference["horizons"]["month_3"]["cohort_revenue_roas"] == pytest.approx(
        2.9 * (1 + 0.9 + 0.81)
    )
    assert reference["modeled_lifetime_revenue_roas"] == pytest.approx(29.0)
