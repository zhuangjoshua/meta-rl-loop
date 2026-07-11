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


def test_saas_worker_contract_keeps_app_graph_fixed_and_landing_composition_fluid():
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
    assert 'guidance_skills: pass exactly THREE — "taste-frontend" first, "claude-design" second' in prompt
    assert "Taste sits above and adapts the selected system; it never replaces it." in prompt
    assert "without prescribing a section count or layout family" in prompt
    assert "route graph and required public/auth behavior are immutable" in prompt
    assert "business_generate_site_image" in prompt
    assert "Persist every created/generated customer artifact" in prompt
    assert "Upgrade landing proof from the verified research" in prompt


def test_navigation_component_is_force_refreshed_with_appkit_rails():
    assert "src/components/site-navigation.tsx" in takyon_core._STARTER_OWNED_REFRESH_FILES
    assert "src/components/social-proof-marquee.tsx" in takyon_core._STARTER_OWNED_REFRESH_FILES
    assert "src/lib/interaction-sounds.ts" in takyon_core._STARTER_OWNED_REFRESH_FILES
    assert "src/screens/support.tsx" in takyon_core._STARTER_OWNED_REFRESH_FILES


def test_landing_has_truthful_coscale_social_proof_and_default_interaction_sounds():
    proof = read("src/components/social-proof-marquee.tsx")
    main = read("src/main.tsx")
    sounds = read("src/lib/interaction-sounds.ts")
    assert "SocialProofMarquee" in main
    assert "PublicLandingRoute" in main
    assert '<Route path="/" element={<PublicLandingRoute />} />' in main
    assert "Used by professionals building with Coscale" in proof
    assert "animate-proof-marquee" in proof
    assert "installInteractionSounds" in main
    assert 'closest("button")' in sounds


def test_pricing_uses_only_published_plan_price_and_limits():
    support = read("src/screens/support.tsx")
    takyon = read("src/lib/takyon.ts")
    assert "defaultPlanPriceLabel" in support
    assert "defaultPlanLimitLabels" in support
    assert "Plan limits" in support
    assert "includedActionQuota" in takyon
    assert "includedAiBudgetMicrousd" in takyon
    contract = takyon_core._subuser_app_kit_contract_block(None)
    assert "never invent a promotion" in contract
    assert "defaultPlanLimitLabels()" in contract


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


def test_surface_contract_display_name_outranks_slug_and_strategy(tmp_path):
    business_root = tmp_path / "businesses" / "qa-proposal-0711"
    workspace = business_root / "product" / "site"
    strategy = business_root / "research" / "strategy.md"
    workspace.mkdir(parents=True)
    strategy.parent.mkdir(parents=True)
    strategy.write_text("# Wrong Legacy Name strategy\n", encoding="utf-8")
    surface = {"metadata": {"product_display_name": "Draftwell"}}

    payload = takyon_core._subuser_surface_context_payload(
        surface, slug="qa-proposal-0711", workspace_root=workspace
    )
    metadata = takyon_core._subuser_app_starter_strings(
        surface, slug="qa-proposal-0711", workspace_root=workspace
    )

    assert payload["businessName"] == "Draftwell"
    assert 'aria-label="Draftwell"' in payload["brandMarkSvg"]
    assert ">DR</text>" in payload["brandMarkSvg"]
    assert metadata["title"] == "Draftwell"
    assert "qa-proposal-0711" not in payload["businessName"].lower()
    assert "surfaceContext.business" not in read("src/lib/branding.ts")
    worker_contract = takyon_core._subuser_app_worker_contract_block(
        {
            "metadata": {
                "product_display_name": "Draftwell",
                "workflow_completion_required": True,
            }
        },
        plans_configured=True,
    )
    assert "Canonical customer-visible product name: Draftwell" in worker_contract
    assert "FINAL WORKFLOW GATE IS ACTIVE" in worker_contract

    prompt = turn_runtime._business_bootstrap_instruction(
        "qa-proposal-0711",
        "Generate and save proposals, then reopen and revise them",
        "live",
        archetype="saas",
    )
    assert "display_name: the ONE human product display name" in prompt
    assert "workflow_completion_required: true" in prompt


def test_surface_context_reads_unbolded_product_name_from_identity_list(tmp_path):
    business_root = tmp_path / "businesses" / "internal-slug-0711"
    workspace = business_root / "product" / "site"
    strategy = business_root / "research" / "strategy.md"
    workspace.mkdir(parents=True)
    strategy.parent.mkdir(parents=True)
    strategy.write_text(
        "# Briefly initial landing brief — idea-only fast pass\n\n"
        "## Canonical identity\n\n"
        "- Business record name: internal-slug-0711\n"
        "- Product name: Briefly\n",
        encoding="utf-8",
    )
    payload = takyon_core._subuser_surface_context_payload(
        None, slug="internal-slug-0711", workspace_root=workspace
    )
    assert payload["businessName"] == "Briefly"
