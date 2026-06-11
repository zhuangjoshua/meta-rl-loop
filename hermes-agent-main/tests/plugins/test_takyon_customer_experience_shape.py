from __future__ import annotations

from pathlib import Path
from urllib.error import URLError

import pytest

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


def test_merge_subuser_app_metadata_preserves_existing_rail_truth_when_declaring_new_rail():
    metadata = takyon_core._merge_subuser_app_metadata(  # type: ignore[attr-defined]
        {
            "subuser_app": {
                "app_mode": "standard_saas",
                "subscription_style": "monthly",
                "api_mode": "shared_runtime",
                "rail_state": {
                    "auth": "live",
                    "account": "live",
                },
            }
        },
        runtime_features=["auth", "account", "records"],
        previous_runtime_features=["auth", "account"],
        app_mode="standard_saas",
        subscription_style="monthly",
        api_mode="shared_runtime",
    )
    assert metadata["subuser_app"]["rail_state"] == {
        "auth": "live",
        "account": "live",
        "records": "declared",
    }


def test_product_workflow_rejects_no_sharing_when_directory_or_connections_are_selected():
    with pytest.raises(takyon_core.TakyonError, match="no_sharing cannot stay true"):
        takyon_core._validate_product_workflow_contract(  # type: ignore[attr-defined]
            surface={
                "runtime_features": ["directory"],
                "metadata": {
                    "product_workflow": {
                        "scope_rules": {
                            "no_sharing": True,
                        }
                    }
                },
            },
            runtime_features=["auth", "account", "profile", "directory"],
        )


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
    assert (workspace_root / "src" / "app" / "opengraph-image.js").exists()
    assert (workspace_root / "src" / "app" / "privacy" / "page.js").exists()
    assert (workspace_root / "src" / "app" / "robots.js").exists()
    assert (workspace_root / "src" / "app" / "sitemap.js").exists()
    assert (workspace_root / "src" / "app" / "terms" / "page.js").exists()
    assert (workspace_root / "src" / "app" / "twitter-image.js").exists()
    assert (workspace_root / "src" / "app" / "app" / "layout.js").exists()
    assert (workspace_root / "src" / "app" / "app" / "page.js").exists()
    assert (workspace_root / "src" / "app" / "app" / "(product)" / "layout.js").exists()
    assert (workspace_root / "src" / "app" / "app" / "profile" / "page.js").exists()
    assert (workspace_root / "src" / "components" / "starter-primitives.js").exists()
    assert (workspace_root / "src" / "components" / "starter-access-page.js").exists()
    assert (workspace_root / "src" / "components" / "starter-account-page.js").exists()
    assert (workspace_root / "src" / "components" / "starter-metadata.js").exists()
    assert (workspace_root / "src" / "components" / "starter-server.js").exists()
    starter_context = (workspace_root / "src" / "components" / "starter-context.js").read_text()
    starter_metadata = (workspace_root / "src" / "components" / "starter-metadata.js").read_text()
    globals_css = (workspace_root / "src" / "app" / "globals.css").read_text()
    next_config = (workspace_root / "next.config.js").read_text()
    assert 'export const starterDefaultPlanKey =' in starter_context
    assert "export const starterConfiguredPlans =" in starter_context
    assert "export const starterDefaultMonthlyPlan =" in starter_context
    assert "export async function starterRequestAuth" in starter_context
    assert "export async function starterSession" in starter_context
    assert "export async function starterAccount" in starter_context
    assert "export async function starterCancelSubscription" in starter_context
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
    starter_primitives = (workspace_root / "src" / "components" / "starter-primitives.js").read_text()
    starter_access_page = (workspace_root / "src" / "components" / "starter-access-page.js").read_text()
    starter_account_page = (workspace_root / "src" / "components" / "starter-account-page.js").read_text()
    assert "export function StarterAuthCard" in starter_primitives
    assert "export function StarterSubscriptionCard" in starter_primitives
    assert "export function StarterBlockedCard" in starter_primitives
    assert 'new URLSearchParams(window.location.search)' in starter_access_page
    assert 'useSearchParams' not in starter_access_page
    assert 'title="Access ready."' in starter_access_page
    assert "Open account" in starter_access_page
    assert "Use Account to review your plan, billing state, and profile details." in starter_access_page
    assert "Plan details will appear here." in starter_access_page
    assert "Sign in to open your account." in starter_account_page
    assert "Use your email to manage your subscription and profile." in starter_account_page
    assert "Cancellation scheduled." in starter_account_page
    assert "Cancel subscription" in starter_account_page
    assert "--starter-max: 1120px;" in globals_css
    assert "width: min(calc(100% - clamp(24px, 4vw, 64px)), var(--starter-max));" in globals_css
    starter_server = (workspace_root / "src" / "components" / "starter-server.js").read_text()
    assert "function requestHasSessionCookie()" in starter_server
    assert 'const target = "takyon_app_session="' in starter_server
    assert 'if (!requestHasSessionCookie()) {' in starter_server
    assert "return starterAppState({}, null, { errors });" in starter_server
    assert "starterRootMetadata" in starter_metadata
    assert "starterAppRobotsMetadata" in starter_metadata
    assert "starterDefaultPublicRoutes" in starter_metadata
    assert "starterPageMetadata" in starter_metadata
    assert "starterPublicRoutes()" in starter_metadata
    assert 'card: "summary_large_image"' in starter_metadata
    assert 'starterDefaultPublicRoutes = ["/", "/privacy", "/terms"]' in starter_metadata
    assert 'normalized === "/app"' in starter_metadata
    assert 'starterAbsoluteUrl("/sitemap.xml")' in starter_metadata
    home_page = (workspace_root / "src" / "app" / "page.js").read_text()
    privacy_page = (workspace_root / "src" / "app" / "privacy" / "page.js").read_text()
    root_layout = (workspace_root / "src" / "app" / "layout.js").read_text()
    app_layout = (workspace_root / "src" / "app" / "app" / "layout.js").read_text()
    app_page = (workspace_root / "src" / "app" / "app" / "page.js").read_text()
    product_layout = (workspace_root / "src" / "app" / "app" / "(product)" / "layout.js").read_text()
    sitemap_page = (workspace_root / "src" / "app" / "sitemap.js").read_text()
    terms_page = (workspace_root / "src" / "app" / "terms" / "page.js").read_text()
    twitter_image = (workspace_root / "src" / "app" / "twitter-image.js").read_text()
    robots_page = (workspace_root / "src" / "app" / "robots.js").read_text()
    opengraph_image = (workspace_root / "src" / "app" / "opengraph-image.js").read_text()
    assert "starterDefaultMonthlyPlan" not in home_page
    assert "Keep the monthly subscription visible before anyone starts checkout." not in home_page
    assert 'redirect("/app")' in home_page
    assert 'href="/privacy"' not in home_page
    assert '"/pricing"' not in home_page
    assert not (workspace_root / "src" / "app" / "pricing" / "page.js").exists()
    assert "Privacy policy" in privacy_page
    assert "Back to home" not in privacy_page
    assert "starter-card starter-section-card" not in privacy_page
    assert 'href="/terms"' in privacy_page
    assert "StarterPrivacyPage" not in privacy_page
    assert 'path: "/privacy"' in privacy_page
    assert "export const metadata = starterRootMetadata;" in root_layout
    assert "metadataBase: starterMetadataBase" in starter_metadata
    assert "openGraph:" in starter_metadata
    assert "twitter:" in starter_metadata
    assert "export const metadata = starterAppRobotsMetadata;" in app_layout
    assert 'href="/privacy"' in app_layout
    assert 'href="/terms"' in app_layout
    assert "loadServerAppState" in app_page
    assert "StarterAccessPage initialAppState={initialAppState}" in app_page
    assert 'if (initialAppState?.access?.state !== "ready")' in product_layout
    assert 'redirect("/app")' in product_layout
    assert "starterSitemapEntries" in sitemap_page
    assert "Terms of service" in terms_page
    assert "Back to home" not in terms_page
    assert "starter-card starter-section-card" not in terms_page
    assert 'href="/privacy"' in terms_page
    assert "StarterTermsPage" not in terms_page
    assert 'path: "/terms"' in terms_page
    assert "starterRobotsConfig" in robots_page
    assert "ImageResponse" in opengraph_image
    assert "starterSiteName" in opengraph_image
    assert 'export { alt, contentType, default, size } from "./opengraph-image.js";' in twitter_image
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
    assert "starterCancelSubscription(...)" in block
    assert "starterSubscriptionState(...)" in block
    assert "starterCanUseApp(...)" in block
    assert "starterViewerState(...)" in block
    assert "starterAppState(...)" in block
    assert "starterLoadViewer()" in block
    assert "starterLoadAppState()" in block
    assert "preset support pages at `/privacy` and `/terms`" in block
    assert "Do not spend normal bootstrap/design time reading, redesigning, or polishing them" in block
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
    assert "Treat `src/app/privacy/page.js` and `src/app/terms/page.js` as preset support pages" in block
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
