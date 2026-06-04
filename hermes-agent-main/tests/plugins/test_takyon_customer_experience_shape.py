from __future__ import annotations

from plugins.takyon import core as takyon_core


def test_surface_customer_experience_shape_defaults_strategy_source():
    shape = takyon_core._surface_customer_experience_shape(  # type: ignore[attr-defined]
        {
            "metadata": {},
        }
    )

    assert shape["research_sources"] == ["research/strategy.md"]
    assert shape["surface_goal"] == ""
    assert shape["required_sections"] == []


def test_merge_customer_experience_metadata_normalizes_worker_contract_fields():
    metadata = takyon_core._merge_customer_experience_metadata(  # type: ignore[attr-defined]
        {
            "customer_experience": {
                "required_sections": ["hero"],
            }
        },
        surface_goal="Parents trust the app enough to sign up and start planning",
        conversion_model="self_serve_signup",
        required_routes=["/", "/pricing", "/app", "/pricing"],
        required_sections=["hero", "sample plan", "pricing"],
        required_app_tabs=["Planner", "Progress"],
        research_sources=["research/strategy.md", "research/market.md"],
        experience_notes="Show the locked upgrade path honestly even before billing is live.",
    )
    payload = takyon_core._subuser_surface_context_payload(  # type: ignore[attr-defined]
        {
            "metadata": metadata,
            "runtime_features": ["auth", "generate"],
        },
        slug="plannerly",
    )

    customer = payload["customerExperience"]
    assert customer["surfaceGoal"] == "Parents trust the app enough to sign up and start planning"
    assert customer["conversionModel"] == "self_serve_signup"
    assert customer["requiredRoutes"] == ["/", "/pricing", "/app"]
    assert customer["requiredSections"] == ["hero", "sample plan", "pricing"]
    assert customer["requiredAppTabs"] == ["Planner", "Progress"]
    assert customer["researchSources"] == ["research/strategy.md", "research/market.md"]
    assert "billing is live" in customer["experienceNotes"]
