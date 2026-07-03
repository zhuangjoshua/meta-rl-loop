from __future__ import annotations

import sys
from pathlib import Path


SCRIPTS_DIR = (
    Path(__file__).resolve().parents[2]
    / "skills"
    / "takyon"
    / "static-ad-creative-generator"
    / "scripts"
)
sys.path.insert(0, str(SCRIPTS_DIR))

import validate_spec  # noqa: E402


def _canonical_reddit_spec() -> dict:
    return {
        "creative_id": "reddit-nutrient-gap-01",
        "platform": "reddit",
        "placement": "feed",
        "aspect_ratio": "1:1",
        "goal": "clicks",
        "audience": {
            "persona": "nutrition trackers",
            "pain": "they know calories but not nutrient gaps",
            "desire": "food-first fixes for missing nutrients",
            "awareness_level": "problem_aware",
        },
        "strategy": {
            "angle": "pain_agitation",
            "boldness": "hard",
            "disruption_target": "calorie-only food tracking",
            "hook_tactic": "warning",
            "psychological_trigger": "pain_agitation",
            "creative_mechanic": "reframe",
            "hook": "Stop tracking calories only",
            "claim_support": "demo of nutrient bars closing from food choices",
            "promise": "see the foods that close each nutrient gap",
            "objection_addressed": "I already use a food tracker",
            "proof_type": "demo",
        },
        "visual": {
            "template": "hero_product",
            "style": "clean native nutrition app ad",
            "scene": "fresh foods beside a simple nutrient-gap meter",
            "subject": "whole foods and a nutrient tracker motif",
            "composition": "square feed card with clear top headline space",
            "focal_point": "the nutrient gap closing",
            "props": ["leafy greens", "pumpkin seeds", "lentils"],
            "background": "soft off-white surface",
            "lighting": "bright natural light",
        },
        "product": {
            "representation": "implied by nutrient tracker motif",
            "must_show": ["Nutrientx name", "green accent"],
            "must_not_show": ["fabricated third-party logos", "invented endorsements"],
        },
        "copy": {
            "overlay_text": "Stop guessing nutrients",
            "headline": "Find your nutrient gaps",
            "primary_text": "Nutrientx shows what you are missing and the foods that fix it.",
            "cta": "Learn More",
        },
        "layout": {
            "overlay_position": "top",
            "typography": "bold readable sans-serif",
            "safe_zones": "keep text at least 5% from every edge",
            "logo_position": "bottom corner",
        },
        "prompting": {
            "final_image_prompt": "",
            "negative_constraints": ["garbled text", "fake logos", "clutter"],
            "reference_images": [],
        },
        "qa": {
            "readability_check": "short overlay reads in feed",
            "native_platform_check": "looks like a promoted Reddit feed card",
            "policy_risks": [],
            "iteration_notes": "test a comparison variant next",
        },
    }


def test_reddit_feed_spec_is_canonical_static_ad_schema():
    errors, warnings = validate_spec.validate(_canonical_reddit_spec())

    assert errors == []
    assert not any("not valid for platform 'reddit'" in message for _level, message in warnings)
