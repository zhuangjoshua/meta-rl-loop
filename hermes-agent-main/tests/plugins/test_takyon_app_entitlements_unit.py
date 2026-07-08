from __future__ import annotations

from plugins.takyon import app_entitlements


def _policy(metadata: dict) -> app_entitlements.PlanPolicy:
    return app_entitlements.PlanPolicy(
        id="plan_1",
        business_slug="biz",
        plan_key="monthly",
        tier="paid",
        price_cents=900,
        currency="usd",
        billing_interval="month",
        included_ai_budget_microusd=5_000_000,
        included_action_quota=0,
        stripe_product_id=None,
        stripe_price_id=None,
        source="takyon",
        notes="",
        metadata=metadata,
    )


def test_plan_metadata_update_preserves_existing_gateway_allowlists_when_omitted():
    existing = _policy(
        {
            "features": {"ai_generate": True, "web_search": True},
            "model_allowlist": ["claude-sonnet-4-6"],
            "custom": "old",
        }
    )

    merged = app_entitlements._preserve_gateway_allowlist_metadata({"custom": "new"}, existing)

    assert merged["custom"] == "new"
    assert merged["features"] == {"ai_generate": True, "web_search": True}
    assert merged["model_allowlist"] == ["claude-sonnet-4-6"]


def test_plan_metadata_update_allows_explicit_gateway_allowlist_change():
    existing = _policy(
        {
            "features": {"ai_generate": True, "web_search": True},
            "model_allowlist": ["claude-sonnet-4-6"],
        }
    )

    merged = app_entitlements._preserve_gateway_allowlist_metadata(
        {"features": {}, "model_allowlist": ["claude-other"]},
        existing,
    )

    assert merged["features"] == {}
    assert merged["model_allowlist"] == ["claude-other"]


def test_baseline_gateway_features_injected_when_features_absent():
    merged = app_entitlements._ensure_baseline_gateway_features({"custom": "x"})
    assert merged["features"] == {"ai_generate": True}
    assert merged["custom"] == "x"


def test_baseline_gateway_features_merged_into_product_domain_dict():
    # The 07-04 strand shape: CEO-seeded product-domain names only (magicslides).
    merged = app_entitlements._ensure_baseline_gateway_features(
        {"features": {"deck_chat": True, "deck_generate": True, "speaker_notes": True}}
    )
    assert merged["features"]["ai_generate"] is True
    assert merged["features"]["deck_generate"] is True


def test_baseline_gateway_features_respects_explicit_opt_out():
    merged = app_entitlements._ensure_baseline_gateway_features(
        {"features": {"ai_generate": False, "exports": True}}
    )
    assert merged["features"]["ai_generate"] is False


def test_baseline_gateway_features_appended_to_legacy_list():
    merged = app_entitlements._ensure_baseline_gateway_features(
        {"features": ["prompt_scoring", "mention_tracking"]}
    )
    assert merged["features"] == ["prompt_scoring", "mention_tracking", "ai_generate"]
