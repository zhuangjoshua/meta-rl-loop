from __future__ import annotations

from pathlib import Path

from plugins.takyon import core as takyon_core
from plugins.takyon import turn_runtime


SCAFFOLD = Path(takyon_core.__file__).resolve().parent / "subuser_app_kit" / "scaffold"


def read(rel: str) -> str:
    return (SCAFFOLD / rel).read_text(encoding="utf-8")


def test_public_shell_separates_login_signup_and_redirects_signed_in_viewers():
    auth = read("src/lib/product-auth.tsx")
    landing = read("src/screens/landing.tsx")
    navigation = read("src/components/site-navigation.tsx")

    assert "signUpWithGoogle" in auth
    assert "setSubscribeAfterAuth(true)" not in auth  # the boolean comes from startGoogleAuth's argument
    assert "startGoogleAuth(true)" in auth
    assert "shouldSubscribeAfterAuth" in read("src/lib/hooks.ts")
    assert 'if (access.loading) return <LandingLoading />' in landing
    assert '<Navigate to="/app" replace />' in landing
    assert "PublicSiteHeader" in landing
    assert navigation.count("Log in") >= 1
    assert navigation.count("Sign up") >= 1
    assert "border-b border-border" in navigation
    assert "max-w-7xl" not in navigation
    assert 'className="flex w-full' in navigation


def test_nested_public_pages_have_stable_header_and_no_conversion_ctas():
    support = read("src/screens/support.tsx")
    assert "PublicSiteHeader" in support
    assert "BackButton" in support
    assert "Open app" not in support
    assert "Subscribe" not in support
    assert "export function PricingScreen" in support
    assert '<Route path="/pricing"' in read("src/main.tsx")


def test_app_layout_is_canonical_direct_full_width_gate():
    layout = read("src/screens/app-layout.tsx")
    home = read("src/screens/app-home.tsx")
    assert 'data-takyon-scaffold="app-layout"' in layout
    assert "!access.authenticated" in layout
    assert "!access.entitled" in layout
    assert "<Outlet />" in layout
    assert 'className="w-full px-4 py-6 sm:px-6 lg:px-8"' in layout
    assert "max-w-6xl" not in layout
    assert "BackButton" in layout
    assert "Open app" not in home


def test_record_lists_keep_last_success_across_tabs_and_refresh_failures():
    hooks = read("src/lib/hooks.ts")
    block = hooks.split("export function useRecords", 1)[1].split(
        "export interface UseActionRunnerResult", 1
    )[0]
    assert "const recordsCache = new Map" in hooks
    assert "recordsCache.get(type)" in block
    assert "recordsCache.set(type, list)" in block
    catch_block = block.split("} catch (err) {", 1)[1].split("} finally", 1)[0]
    assert "setRecords([])" not in catch_block


def test_saas_worker_contract_requires_product_visuals_proof_and_durable_full_page_workflow():
    contract = takyon_core._subuser_app_kit_contract_block(None)
    assert "PublicSiteHeader" in contract
    assert "distinct Log in and Sign up" in contract
    assert "full-width application workspace" in contract
    assert "persist it through `saveRecord(...)`" in contract
    assert "product-specific UI visuals" in contract
    assert "Quantified outcome claims" in contract

    prompt = turn_runtime._business_bootstrap_instruction(
        "saas-appkit-test", "Build a SaaS workflow", "live", archetype="web_saas"
    )
    assert "Preserve and render the canonical `PublicSiteHeader`" in prompt
    assert "at least two polished, product-specific representations" in prompt
    assert "Persist every created/generated customer artifact" in prompt
    assert "Upgrade landing proof from the verified research" in prompt


def test_navigation_component_is_force_refreshed_with_appkit_rails():
    assert "src/components/site-navigation.tsx" in takyon_core._STARTER_OWNED_REFRESH_FILES


def test_surface_context_carries_strategy_product_name_instead_of_internal_slug(tmp_path):
    business_root = tmp_path / "businesses" / "internal-slug-0711"
    workspace = business_root / "product" / "site"
    strategy = business_root / "research" / "strategy.md"
    workspace.mkdir(parents=True)
    strategy.parent.mkdir(parents=True)
    strategy.write_text(
        "# Threadline strategy brief\n\n## Business Name\nThreadline\n",
        encoding="utf-8",
    )
    payload = takyon_core._subuser_surface_context_payload(
        None, slug="internal-slug-0711", workspace_root=workspace
    )
    assert payload["business"] == "internal-slug-0711"
    assert payload["businessName"] == "Threadline"
    branding = read("src/lib/branding.ts")
    assert "businessName" in branding
