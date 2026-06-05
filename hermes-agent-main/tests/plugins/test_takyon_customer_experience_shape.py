from __future__ import annotations

from pathlib import Path
from urllib.error import URLError

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
        plans=[
            {
                "plan_key": "monthly",
                "tier": "paid",
                "price_cents": 1900,
                "currency": "usd",
                "billing_interval": "month",
                "included_ai_budget_microusd": 5_000_000,
                "included_action_quota": 0,
                "allow_overage": False,
            }
        ],
    )

    assert (workspace_root / "package.json").exists()
    assert (workspace_root / "next.config.js").exists()
    assert (workspace_root / "src" / "app" / "layout.js").exists()
    assert (workspace_root / "src" / "app" / "globals.css").exists()
    assert (workspace_root / "src" / "app" / "app" / "page.js").exists()
    assert (workspace_root / "src" / "components" / "StarterAuthForm.js").exists()
    assert (workspace_root / "src" / "components" / "StarterCheckoutForm.js").exists()
    assert (workspace_root / "src" / "components" / "StarterGenerateForm.js").exists()
    starter_context = (workspace_root / "src" / "components" / "starter-context.js").read_text()
    starter_checkout = (workspace_root / "src" / "components" / "StarterCheckoutForm.js").read_text()
    starter_auth = (workspace_root / "src" / "components" / "StarterAuthForm.js").read_text()
    starter_landing = (workspace_root / "src" / "components" / "StarterLanding.js").read_text()
    next_config = (workspace_root / "next.config.js").read_text()
    starter_workspace = (workspace_root / "src" / "components" / "StarterWorkspace.js").read_text()
    assert 'export const starterDefaultPlanKey =' in starter_context
    assert "export const starterConfiguredPlans =" in starter_context
    assert "export const starterDefaultMonthlyPlan =" in starter_context
    assert '"priceCents": 1900' in (workspace_root / takyon_core.SUBUSER_KIT_DIRNAME / "surface-context.js").read_text()
    assert '"includedAiBudgetMicrousd": 5000000' in (workspace_root / takyon_core.SUBUSER_KIT_DIRNAME / "surface-context.js").read_text()
    assert 'useState(starterDefaultPlanKey)' in starter_checkout
    assert 'fixedMonthlyPlan ? "Subscribe" : "Start checkout"' in starter_checkout
    assert 'origin: typeof window !== "undefined" ? window.location.origin : ""' in starter_auth
    assert 'TAKYON_LOCAL_RUNTIME_PROXY_ORIGIN' in next_config
    assert 'source: "/api/takyon/:path*"' in next_config
    assert "See pricing" not in starter_landing
    assert "Monthly access" not in starter_landing
    assert "Continue with your account" not in starter_workspace
    assert "const hasPaidAccess =" in starter_workspace
    assert "const showGenerate =" in starter_workspace


def test_appkit_contract_block_preserves_canonical_rail_components():
    block = takyon_core._subuser_app_kit_contract_block(  # type: ignore[attr-defined]
        {
            "runtime_features": ["auth", "account", "checkout", "generate"],
            "routes": ["/", "/app"],
            "metadata": {
                "subuser_app": {
                    "app_mode": "standard_saas",
                    "subscription_style": "monthly",
                }
            },
        }
    )

    assert "AppKit-owned rail components and helpers are canonical behavior, not inspiration." in block
    assert "preserve that behavior and only wrap, restyle, reposition, or compose around it" in block


def test_worker_contract_block_keeps_appkit_auth_behavior_authoritative():
    block = takyon_core._subuser_app_worker_contract_block(  # type: ignore[attr-defined]
        {
            "runtime_features": ["auth", "account", "checkout"],
            "routes": ["/", "/app"],
            "metadata": {
                "subuser_app": {
                    "app_mode": "standard_saas",
                    "subscription_style": "monthly",
                },
                "customer_experience": {
                    "required_routes": ["/", "/app"],
                },
            },
        },
        plans_configured=True,
    )

    assert "keep its sign-in behavior authoritative" in block
    assert "do not fake browser-only sessions" in block


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


def test_probe_product_public_url_retries_transient_tls_bootstrap(monkeypatch):
    sleeps: list[int] = []
    attempts = {"count": 0}

    class _Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    def _fake_urlopen(_request, timeout=0):
        attempts["count"] += 1
        if attempts["count"] < 3:
            raise URLError("[SSL: TLSV1_ALERT_INTERNAL_ERROR] tlsv1 alert internal error (_ssl.c:1000)")
        return _Response()

    monkeypatch.setattr(takyon_core, "_product_deploy_dry_run", lambda: False)
    monkeypatch.setattr(takyon_core, "_product_public_probe_enabled", lambda: True)
    monkeypatch.setattr(takyon_core.urllib.request, "urlopen", _fake_urlopen)
    monkeypatch.setattr(takyon_core.time, "sleep", sleeps.append)

    ok, blocker = takyon_core._probe_product_public_url("https://example.fourmanifold.com/")  # type: ignore[attr-defined]

    assert ok is True
    assert blocker == ""
    assert attempts["count"] == 3
    assert sleeps == [2, 4]
