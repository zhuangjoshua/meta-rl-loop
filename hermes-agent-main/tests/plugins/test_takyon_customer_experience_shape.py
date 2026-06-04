from __future__ import annotations

from pathlib import Path

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
    assert "experienceNotes" not in customer


def test_materialized_subuser_kit_writes_js_context_only(tmp_path: Path):
    workspace_root = tmp_path / "product" / "site"
    workspace_root.mkdir(parents=True)

    takyon_core._materialize_subuser_app_kit(  # type: ignore[attr-defined]
        workspace_root,
        slug="plannerly",
        surface={"runtime_features": ["auth", "account"], "routes": [{"path": "/"}, {"path": "/app"}]},
    )

    kit_root = workspace_root / takyon_core.SUBUSER_KIT_DIRNAME
    assert (kit_root / "surface-context.js").exists()
    assert not (kit_root / "surface-context.md").exists()


def test_materialized_subuser_kit_seeds_monthly_app_starter_for_app_shells(tmp_path: Path):
    workspace_root = tmp_path / "product" / "site"
    workspace_root.mkdir(parents=True)

    takyon_core._materialize_subuser_app_kit(  # type: ignore[attr-defined]
        workspace_root,
        slug="plannerly",
        surface={
            "runtime_features": ["auth", "checkout"],
            "routes": [{"path": "/"}, {"path": "/app"}],
            "metadata": {
                "subuser_app": {
                    "app_mode": "standard_saas",
                    "subscription_style": "monthly",
                },
                "customer_experience": {
                    "required_routes": ["/", "/app"],
                    "required_app_tabs": ["Planner", "Account"],
                },
            },
        },
    )

    assert (workspace_root / "package.json").exists()
    assert (workspace_root / "next.config.js").exists()
    assert (workspace_root / "src" / "app" / "layout.js").exists()
    assert (workspace_root / "src" / "app" / "globals.css").exists()
    assert (workspace_root / "src" / "app" / "app" / "page.js").exists()
    assert (workspace_root / "src" / "components" / "StarterAuthForm.js").exists()


def test_default_surface_contract_omits_design_brief(tmp_path: Path):
    store = takyon_core.TakyonStore(tmp_path)

    class _FakeCursor:
        def fetchone(self):
            return None

    class _FakeConn:
        def execute(self, *_args, **_kwargs):
            return _FakeCursor()

    surface = store._stored_app_surface_contract(_FakeConn(), "plannerly")  # type: ignore[attr-defined]

    assert "design_brief_path" not in surface
