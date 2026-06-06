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
    assert (workspace_root / "src" / "app" / "app" / "layout.js").exists()
    assert (workspace_root / "src" / "app" / "app" / "page.js").exists()
    assert (workspace_root / "src" / "app" / "app" / "(product)" / "layout.js").exists()
    assert (workspace_root / "src" / "app" / "app" / "profile" / "page.js").exists()
    assert (workspace_root / "src" / "components" / "starter-pages.js").exists()
    assert (workspace_root / "src" / "components" / "starter-server.js").exists()
    starter_context = (workspace_root / "src" / "components" / "starter-context.js").read_text()
    next_config = (workspace_root / "next.config.js").read_text()
    assert 'export const starterDefaultPlanKey =' in starter_context
    assert "export const starterConfiguredPlans =" in starter_context
    assert "export const starterDefaultMonthlyPlan =" in starter_context
    assert "export async function starterRequestAuth" in starter_context
    assert "export async function starterSession" in starter_context
    assert "export async function starterAccount" in starter_context
    assert "export async function starterProfile" in starter_context
    assert "export async function starterUpdateProfile" in starter_context
    assert "export async function starterCheckout" in starter_context
    assert "export async function starterGenerate" in starter_context
    assert "export function starterIsAuthenticated" in starter_context
    assert "export function starterIsEntitled" in starter_context
    assert "export function starterSubscriptionState" in starter_context
    assert "export function starterCanUseApp" in starter_context
    assert "export function starterCanCheckout" in starter_context
    assert "export function starterCanGenerate" in starter_context
    assert "export function starterViewerState" in starter_context
    assert "export function starterAppState" in starter_context
    assert "export async function starterLoadViewer" in starter_context
    assert "export async function starterLoadAppState" in starter_context
    assert 'send_email: true' not in starter_context
    starter_pages = (workspace_root / "src" / "components" / "starter-pages.js").read_text()
    assert "function StarterLandingPage" in starter_pages
    assert "function landingState(appState = null)" in starter_pages
    assert "function StarterProductAccessGate" in starter_pages
    assert "function StarterProfilePageInner" in starter_pages
    assert 'new URLSearchParams(window.location.search)' in starter_pages
    assert 'useSearchParams' not in starter_pages
    assert "Route-level access lives here." not in starter_pages
    assert "Use the rails. Redesign everything else." not in starter_pages
    assert "This shell is already behind the real app gate." not in starter_pages
    assert "Anonymous visitors can see the landing page" not in starter_pages
    assert "Open /app" not in starter_pages
    assert "Open profile" not in starter_pages
    assert "Start in AppKit" not in starter_pages
    assert "This action is not available yet." not in starter_pages
    assert "viewer state" not in starter_pages
    assert "Connect a real plan before launch." not in starter_pages
    assert "Subscription details appear here once a real plan is configured." not in starter_pages
    assert "Land in a real product shell" not in starter_pages
    assert "We received your checkout." in starter_pages
    assert "Sign in to view your account." in starter_pages
    assert "Use your email to manage your subscription and profile." in starter_pages
    starter_server = (workspace_root / "src" / "components" / "starter-server.js").read_text()
    assert "function requestHasSessionCookie()" in starter_server
    assert 'const target = "takyon_app_session="' in starter_server
    assert 'if (!requestHasSessionCookie()) {' in starter_server
    assert "return starterAppState({}, null, { errors });" in starter_server
    app_page = (workspace_root / "src" / "app" / "app" / "page.js").read_text()
    product_layout = (workspace_root / "src" / "app" / "app" / "(product)" / "layout.js").read_text()
    assert "loadServerAppState" in app_page
    assert "StarterProductAccessGate initialAppState={initialAppState}" in app_page
    assert 'if (initialAppState?.access?.state !== "ready")' in product_layout
    assert 'redirect("/app")' in product_layout
    assert '"priceCents": 1900' in (workspace_root / takyon_core.SUBUSER_KIT_DIRNAME / "surface-context.js").read_text()
    assert '"includedAiBudgetMicrousd": 5000000' in (workspace_root / takyon_core.SUBUSER_KIT_DIRNAME / "surface-context.js").read_text()
    assert '"auth": "declared"' in (workspace_root / takyon_core.SUBUSER_KIT_DIRNAME / "surface-context.js").read_text()
    assert '"checkout": "declared"' in (workspace_root / takyon_core.SUBUSER_KIT_DIRNAME / "surface-context.js").read_text()
    assert 'TAKYON_LOCAL_RUNTIME_PROXY_ORIGIN' in next_config
    assert 'source: "/api/takyon/:path*"' in next_config


def test_appkit_contract_block_preserves_canonical_rail_helpers():
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

    assert "AppKit-owned rail helpers are canonical behavior, not inspiration." in block
    assert "starterRequestAuth(...)" in block
    assert "starterIsEntitled(...)" in block
    assert "starterSubscriptionState(...)" in block
    assert "starterCanUseApp(...)" in block
    assert "starterViewerState(...)" in block
    assert "starterAppState(...)" in block
    assert "starterLoadViewer()" in block
    assert "starterLoadAppState()" in block
    assert "canonical starter shells for `/`, `/app`, and `/app/profile`" in block
    assert "Keep customer-facing copy free of developer framing." in block


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

    assert "keep that rail behavior authoritative" in block
    assert "do not fake browser-only sessions" in block
    assert "Do not pre-disable auth UI" in block
    assert "signed-up/account-holder" in block
    assert "subscribed/unsubscribed" in block
    assert "src/app/app/(product)/" in block
    assert "first monthly bootstrap" in block
    assert "Do not ship customer-facing copy" in block


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
