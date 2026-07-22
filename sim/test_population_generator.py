from __future__ import annotations

import json

import pytest

from sim.llm_client import LLMConfig, LLMStats
from sim.population_generator import (
    RELATIONSHIPS,
    PopulationGeneratorError,
    generate_population,
    load_business_spec,
    load_platform,
    normalize_and_validate_generation,
)
from sim.population_market_v2 import load_population_model, load_variation_model
from sim.tier_b_market import SIM_ROOT


BUSINESS_PATH = SIM_ROOT / "business-spec-formflow.json"
PLATFORM_PATH = SIM_ROOT / "world-71" / "platform.json"


def generation() -> dict:
    populations = []
    for index, relationship in enumerate(RELATIONSHIPS):
        if relationship in {"out_of_market", "category_relevant_other"}:
            eligibility = "none"
        elif relationship in {"core_direct_buyer", "secondary_direct_buyer"}:
            eligibility = "direct"
        else:
            eligibility = "conditional"
        if eligibility == "none":
            primary_states = {
                "current_need": "none", "product_fit": "none", "authority": "none",
                "budget": "constrained", "switching_cost": "high",
                "implementation_capacity": "constrained", "product_experience": "negative",
            }
            secondary_states = {
                "current_need": "occasional", "product_fit": "weak", "authority": "none",
                "budget": "available", "switching_cost": "moderate",
                "implementation_capacity": "available", "product_experience": "limited",
            }
        elif eligibility == "direct":
            primary_states = {
                "current_need": "acute", "product_fit": "strong", "authority": "direct",
                "budget": "available", "switching_cost": "low",
                "implementation_capacity": "available", "product_experience": "strong",
            }
            secondary_states = {
                "current_need": "recurring", "product_fit": "partial", "authority": "shared",
                "budget": "constrained", "switching_cost": "moderate",
                "implementation_capacity": "constrained", "product_experience": "useful",
            }
        else:
            primary_states = {
                "current_need": "recurring", "product_fit": "partial", "authority": "shared",
                "budget": "constrained", "switching_cost": "high",
                "implementation_capacity": "constrained", "product_experience": "useful",
            }
            secondary_states = {
                "current_need": "occasional", "product_fit": "weak", "authority": "influence",
                "budget": "unavailable", "switching_cost": "prohibitive",
                "implementation_capacity": "blocked", "product_experience": "limited",
            }
        populations.append({
            "id": relationship,
            "label": relationship.replace("_", " ").title(),
            "market_relationship": relationship,
            "share": index + 1,
            "parent_constitution": (
                "A concrete category relationship that fixes current need, alternative, "
                "authority, timing, and the conditions under which evaluation can proceed."
            ),
            "delivery": {
                "audience_presence": {
                    "broad": 1.0,
                    "interest_biztools": 0.4 + index / 100,
                    "interest_niche": 0.2 + index / 100,
                },
                "objective_affinity": {
                    "clicks": 1.0,
                    "pageviews": 0.9,
                    "leads": 0.8,
                    "sales": 0.7,
                },
            },
            "children": [
                {
                    "id": f"{relationship}_{suffix}",
                    "label": f"{relationship} {suffix}",
                    "share": child_share,
                    "purchase_eligibility": eligibility,
                    "situation": "Handles a concrete recurring work situation with a known current alternative.",
                    "current_alternative": "Uses email, a generic form, or an established workflow with known tradeoffs.",
                    "attention_context": "Sees cold feed content on a phone while interrupted by ordinary client work.",
                    "purchase_process": "Checks fit and evidence, then follows the authority and payment process for this role.",
                    "relevant_priorities": ["workflow fit", "credible evidence"],
                    "required_evidence": ["a visible, factual mechanism"],
                    "rejection_reasons": ["missing required capability", "poor timing"],
                    "positive_matches": ["a concrete recurring intake problem"],
                    "decision_strata": [
                        {
                            "id": f"{relationship}_{suffix}_primary",
                            "share": 0.6,
                            **primary_states,
                            "rationale": "The primary state reflects the more qualified part of this exact role.",
                        },
                        {
                            "id": f"{relationship}_{suffix}_secondary",
                            "share": 0.4,
                            **secondary_states,
                            "rationale": "The secondary state reflects a concrete blocker within this exact role.",
                        },
                    ],
                }
                for suffix, child_share in (("a", 2.0), ("b", 1.0))
            ],
        })
    return {
        "generation_thesis": "A mixed cold market organized by actual relationship to the product.",
        "populations": populations,
    }


class FakeLLM:
    def __init__(self, response):
        self.response = response
        self.config = LLMConfig(provider="codex", model="test-model")
        self.stats = LLMStats()
        self.prompts = []

    def complete(self, *, prompt, schema, cache_namespace):
        self.stats.calls += 1
        self.prompts.append(prompt)
        return self.response


def test_generation_normalizes_shares_and_covers_all_market_relationships():
    business = load_business_spec(BUSINESS_PATH)
    platform = load_platform(PLATFORM_PATH)
    model, normalization, report = normalize_and_validate_generation(
        generation(), business=business, platform=platform, seed=7,
    )
    assert model["schema"] == "takyon.consumer-population.v5-adaptive-statistical"
    assert sum(parent["share"] for parent in model["populations"]) == pytest.approx(1)
    assert all(
        sum(child["share"] for child in parent["children"]) == pytest.approx(1)
        for parent in model["populations"]
    )
    assert set(report["relationship_coverage"]) == set(RELATIONSHIPS)
    assert report["eligibility_counts"]["none"] > 0
    assert report["eligibility_counts"]["conditional"] > 0
    assert report["eligibility_counts"]["direct"] > 0
    assert report["decision_stratum_count"] == 40
    assert report["contains_ads"] is False
    assert any(row["normalized"] for row in normalization)


def test_generation_fails_when_out_of_market_people_can_purchase():
    raw = generation()
    raw["populations"][0]["children"][0]["purchase_eligibility"] = "direct"
    with pytest.raises(PopulationGeneratorError, match="out_of_market"):
        normalize_and_validate_generation(
            raw,
            business=load_business_spec(BUSINESS_PATH),
            platform=load_platform(PLATFORM_PATH),
            seed=1,
        )


def test_structural_nonbuyer_may_have_no_purchase_process():
    raw = generation()
    raw["populations"][0]["children"][0]["purchase_process"] = "None."
    raw["populations"][0]["children"][0]["relevant_priorities"] = []
    model, _, _ = normalize_and_validate_generation(
        raw,
        business=load_business_spec(BUSINESS_PATH),
        platform=load_platform(PLATFORM_PATH),
        seed=1,
    )
    assert model["populations"][0]["children"][0]["purchase_eligibility"] == "none"


def test_generate_population_writes_reproducibility_and_audit_log(tmp_path):
    raw = generation()
    architect = FakeLLM(raw)
    auditor = FakeLLM({
        "verdict": "pass",
        "audit_summary": "The market is mixed, coherent, and product-relevant.",
        "issues": [],
        "revised_generation": raw,
    })
    output = tmp_path / "adaptive-market"
    manifest = generate_population(
        business=load_business_spec(BUSINESS_PATH),
        platform=load_platform(PLATFORM_PATH),
        seed=12,
        architect=architect,
        auditor=auditor,
        output_dir=output,
    )
    expected = {
        "business-spec.snapshot.json",
        "platform.snapshot.json",
        "architect-prompt.txt",
        "draft-generation.json",
        "draft-population.normalized.json",
        "draft-validation.json",
        "auditor-prompt.txt",
        "audit.json",
        "population-model.json",
        "normalization.json",
        "validation.json",
        "audit-summary.json",
        "manifest.json",
        "events.jsonl",
    }
    assert expected <= {path.name for path in output.iterdir()}
    assert manifest["seed"] == 12
    assert manifest["architect"]["stats"]["calls"] == 1
    assert manifest["auditor"]["stats"]["calls"] == 1
    assert len(load_population_model(output / "population-model.json")["populations"]) == 10
    events = [json.loads(line)["event"] for line in (output / "events.jsonl").read_text().splitlines()]
    assert events == ["run_started", "architect_completed", "auditor_completed", "run_completed"]
    assert "Landing page leads with generic-benefit copy" not in architect.prompts[0]
    assert "Formflow" in architect.prompts[0]


def test_product_independent_human_variation_has_no_role_preferences():
    variation = load_variation_model(SIM_ROOT / "human-variation-core-v1.json")
    assert variation is not None
    assert len(variation["human_microprofiles"]) == 10
    assert sum(profile["share"] for profile in variation["human_microprofiles"]) == pytest.approx(1)
    assert "role_factual_preferences" not in variation
