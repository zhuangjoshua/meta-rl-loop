from __future__ import annotations

import hashlib
import inspect
import shutil
from pathlib import Path

import pytest

from plugins.takyon import core as takyon_core
from plugins.takyon import turn_runtime


SCAFFOLD = Path(takyon_core.__file__).resolve().parent / "subuser_app_kit" / "scaffold"
MOBILE_SCAFFOLD = Path(takyon_core.__file__).resolve().parent / "mobile_app_kit" / "scaffold"
HERMES_ROOT = Path(takyon_core.__file__).resolve().parents[2]
TASTE_SKILL = HERMES_ROOT / "skills" / "creative" / "taste-frontend" / "SKILL.md"
TASTE_UPSTREAM = HERMES_ROOT / "skills" / "creative" / "taste-frontend" / "UPSTREAM.md"
PRODUCT_SKILL = HERMES_ROOT / "skills" / "takyon" / "takyon-product" / "SKILL.md"
DESIGN_COMMAND = HERMES_ROOT / "plugins" / "takyon" / "harness" / "commands" / "design.md"


def read(rel: str) -> str:
    return (SCAFFOLD / rel).read_text(encoding="utf-8")


def test_public_shell_separates_login_signup_and_redirects_signed_in_viewers():
    auth = read("src/lib/product-auth.tsx")
    landing = read("src/screens/landing.tsx")
    navigation = read("src/components/site-navigation.tsx")

    assert "signUpWithGoogle" in auth
    assert "setSubscribeAfterAuth(true)" not in auth  # the boolean comes from startGoogleAuth's argument
    assert auth.count("startGoogleAuth(true)") == 2
    assert "shouldSubscribeAfterAuth" in read("src/lib/hooks.ts")
    assert 'aria-busy={access.loading || undefined}' in landing
    assert '<Navigate to="/app" replace />' in landing
    assert "PublicSiteHeader" in landing
    assert navigation.count("Log in") >= 1
    assert navigation.count("Sign up") >= 1
    assert "border-b border-border" in navigation
    assert "max-w-7xl" not in navigation
    assert 'className="flex w-full' in navigation


def test_landing_seed_is_composition_neutral_and_non_shippable(tmp_path):
    landing = read("src/screens/landing.tsx")

    assert 'data-takyon-scaffold="landing"' in landing
    assert "PublicSiteHeader" in landing
    assert '<Navigate to="/app" replace />' in landing
    for forced_composition in (
        "<section",
        "grid-cols",
        "StoreSection",
        "Sign up",
        "Your workspace",
        "Outcome-focused",
        "Real product visuals",
        "Evidence over hype",
    ):
        assert forced_composition not in landing

    product_root = tmp_path / "site"
    seeded_landing = product_root / "src" / "screens" / "landing.tsx"
    seeded_landing.parent.mkdir(parents=True)
    seeded_landing.write_text(landing, encoding="utf-8")
    markers = takyon_core._scaffold_visible_shell_markers(product_root)
    assert markers == [
        {
            "path": "src/screens/landing.tsx",
            "issue": "scaffold_visible_shell",
            "snippet": "screen still carries the scaffold sentinel marker and renders the blank shell",
        }
    ]
    blocker = takyon_core._scaffold_visible_shell_unfinished_blocker(
        {"inventory": {"risk_markers": markers}}
    )
    assert "product still ships the blank scaffold screen" in blocker
    assert "src/screens/landing.tsx" in blocker


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
    assert "!access.entitled && !accountRoute" in layout
    assert "const autoCheckout" in layout
    assert "useSubscribeIntent(access, searchParams.get(\"intent\"), autoCheckout)" in layout
    assert "Opening secure checkout" in layout
    assert "Complete your subscription" not in layout
    assert 'location.pathname.replace(/\\/+$/, "") === "/app/profile"' in layout
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


def test_action_result_schema_drift_becomes_a_visible_runner_error():
    hooks = read("src/lib/hooks.ts")
    announcer = read("src/components/action-error-announcer.tsx")
    main = read("src/main.tsx")
    contract = takyon_core._subuser_app_kit_contract_block(None)
    prompt = turn_runtime._business_bootstrap_instruction(
        "schema-drift-test",
        "Build a product workflow that generates and saves service reports",
        "live",
        archetype="web_saas",
    )

    assert "export type ActionResultDecoder<T>" in hooks
    assert "export type DecodedActionResult<T>" in hooks
    assert "export type StrictActionResultDecoder<T>" in hooks
    assert "decode?: ActionResultDecoder<T>" in hooks
    assert "export function decodeActionResult<T>" in hooks
    assert "export function useDecodedActionRunner<T>" in hooks
    assert 'APP_ACTION_ERROR_EVENT = "takyon:app-action-error"' in hooks
    assert "announceActionError(name, actionError)" in hooks
    assert "checkoutUrl: error.checkoutUrl" in hooks
    assert "decoded === null || decoded === undefined" in hooks
    assert 'error.kind = "invalid_result"' in hooks
    assert 'role="alert"' in announcer
    assert 'data-takyon-appkit="action-error"' in announcer
    assert 'notice.kind === "budget" && notice.checkoutUrl' in announcer
    assert "Upgrade plan" in announcer
    assert "<ActionErrorAnnouncer />" in main
    assert "src/components/action-error-announcer.tsx" in takyon_core._STARTER_OWNED_REFRESH_FILES
    assert "generation prompt, action-boundary validator/normalizer" in contract
    assert "identical field types" in contract
    assert "`useDecodedActionRunner(name, taggedDecoder)`" in contract
    assert "named `DecodedActionResult<T>` decoder" in contract
    assert "global `invalid_result` alert" in contract
    assert "one explicit normalized JSON result schema" in prompt
    assert "`useDecodedActionRunner(name, taggedDecoder)`" in prompt
    assert "`saveRecord({ data })` payload" in contract
    assert "`record.data` reopen decoder/renderer" in contract
    assert "Raw casts and unconditional `ok: true` returns are invalid" in contract


def test_saas_worker_contract_keeps_app_graph_fixed_and_landing_composition_fluid():
    contract = takyon_core._subuser_app_kit_contract_block(None)
    assert "PublicSiteHeader" in contract
    assert "distinct Log in and Sign up" in contract
    assert "full-width application workspace" in contract
    assert "persist it through `saveRecord(...)`" in contract
    assert "one effective action per state" in contract
    assert "Every delete requires an explicit customer confirmation" in contract
    assert "Never cast `saveRecord`, `getRecord`, or `deleteRecord`" in contract
    assert "as many as your workflow needs" in contract
    assert "worker-owned landing decides whether and how a proof section fits the brief" in contract
    assert "generic platform portfolio proof" in contract
    assert "Quantified outcome claims" in contract

    prompt = turn_runtime._business_bootstrap_instruction(
        "saas-appkit-test", "Build a SaaS workflow", "live", archetype="web_saas"
    )
    assert "Preserve and render the canonical `PublicSiteHeader`" in prompt
    landing_pass = prompt.split("#### 2a. Build and publish the landing page", 1)[1].split(
        "#### 2a.1. Register Search Console", 1
    )[0]
    assert "guidance_skills" not in landing_pass
    assert "native `design-taste-frontend` skill" in landing_pass
    assert "Safebox-gated image generation" in landing_pass
    assert "images, DESIGN.md, screenshots, and visual audits are optional" in landing_pass
    assert "never publication conditions" in landing_pass
    assert "max_turns: 60" in landing_pass
    assert "effort: medium" in landing_pass
    assert "timeout_ms: 900000" in landing_pass
    assert "first API retry or any unchanged deterministic failure" in landing_pass
    assert "Persist every created/generated customer artifact" in prompt
    assert "bootstrap_final_product_pass: true" in prompt
    assert "Upgrade landing proof from the verified research" not in prompt
    assert "### 4. X post" not in prompt
    assert "scheduled CEO wake rail" in prompt


def test_bootstrap_supplies_content_inputs_without_mandating_landing_sections():
    prompt = turn_runtime._business_bootstrap_instruction(
        "composition-neutral-test",
        "Build a paid micro-SaaS for turning client notes into reusable briefs",
        "live",
        archetype="web_saas",
    )
    landing_guidance = prompt.split("For /:", 1)[1].split("For /app:", 1)[0]

    assert (
        "customer, problem, value, offer or pricing, and real conversion path when relevant"
        in landing_guidance
    )
    assert "content inputs, not a required section list" in landing_guidance
    assert "native Taste skill decides whether, where, and how" in landing_guidance
    assert "may omit anything irrelevant" in landing_guidance
    assert "hero, problem, features, pricing, and CTA must" not in prompt
    assert "left-and-right" not in landing_guidance
    assert "split hero" not in landing_guidance


def test_bootstrap_uses_native_taste_without_prompt_body_guidance_injection():
    prompt = turn_runtime._business_bootstrap_instruction(
        "taste-once-test",
        "Build a SaaS where customers generate and save proposals",
        "live",
        archetype="web_saas",
    )
    product_pass = prompt.split("Then finish the access shell and account page", 1)[1].split(
        "#### 2c. Workflow verification gate", 1
    )[0]
    assert "guidance_skills" not in prompt
    assert "native `design-taste-frontend` skill" in product_pass
    assert "inspect the existing product before editing" in product_pass
    assert "Honor the skill's own scope boundary" in product_pass
    assert "do not apply marketing-page layout rules to the multi-step `/app` product UI" in product_pass
    assert "preserve useful landing-page direction" in product_pass
    assert "available image tool is optional" in product_pass
    assert "`effort: high`, `max_turns: 90`, `budget_usd: 25.0`, and `timeout_ms: 1800000`" in product_pass
    assert "intentionally separate from the Taste landing's medium/60/900 bounds" in product_pass
    assert "#### 3a. Upgrade landing proof" not in prompt


def test_new_landing_offers_optional_assets_and_keeps_logo_after_publish():
    prompt = turn_runtime._business_bootstrap_instruction(
        "taste-assets-test",
        "Build a SaaS where customers generate and save account briefs",
        "live",
        archetype="web_saas",
    )
    landing_heading = "#### 2a. Build and publish the landing page"
    logo_heading = "#### 2b. Add the real logo, then finish the /app access shell + profile"
    landing_pass = prompt.split(landing_heading, 1)[1].split(
        "#### 2a.1. Register Search Console", 1
    )[0]
    landing_lower = landing_pass.lower()

    assert "available when original imagery improves the product" in landing_lower
    assert "safebox-gated image generation" in landing_lower
    assert "images" in landing_lower and "never publication conditions" in landing_lower

    # Optional site imagery stays inside the Taste-owned 2a call. The distinct logo workflow
    # remains after the first landing has published and does not make site images mandatory.
    assert prompt.index(landing_heading) < prompt.index(logo_heading)
    logo_pass = prompt.split(logo_heading, 1)[1].split(
        "#### 2c. Workflow verification gate", 1
    )[0]
    assert "Once the landing page has published in 2a" in logo_pass
    assert "business_generate_logo" in logo_pass


def test_opted_in_animation_remains_guidance_without_requiring_generated_imagery():
    prompt = turn_runtime._business_bootstrap_instruction(
        "taste-animation-test",
        "Build an animated analytics SaaS",
        "live",
        animations=True,
        archetype="web_saas",
    )
    landing_pass = prompt.split("#### 2a. Build and publish the landing page", 1)[1].split(
        "#### 2a.1. Register Search Console", 1
    )[0]
    assert "explicitly requested continuous landing animation" in landing_pass
    assert "reduced-motion-safe" in landing_pass
    assert "required generated assets" not in landing_pass


def test_bootstrap_keeps_named_cancellation_policy_out_of_worker_authored_copy():
    prompt = turn_runtime._business_bootstrap_instruction(
        "policy-copy-test",
        "Offer a $9.99 monthly plan with immediate self-service cancellation and no refund option.",
        "live",
        archetype="web_saas",
    )

    assert "treat those as AppKit/backend constraints only" in prompt
    assert "even when the business goal names that policy" in prompt
    assert "account.product_runtime_contract.subscription.cancellation" in prompt
    assert "cancellation ends access immediately with no grace period" not in prompt


def test_taste_skill_is_byte_exact_pinned_upstream_implementation():
    content = TASTE_SKILL.read_bytes()
    assert hashlib.sha256(content).hexdigest() == (
        "aa194351b246b8b4799099d4ed7b033d29eab6e6e3d58d8d2172978be7b3ec89"
    )
    text = content.decode("utf-8")
    assert "name: design-taste-frontend" in text
    assert "Not dashboards, not data tables, not multi-step product UI." in text
    assert 'Output a one-line "Design Read" before generating' in text
    assert "## 14. FINAL PRE-FLIGHT CHECK" in text
    provenance = TASTE_SKILL.with_name("UPSTREAM.md").read_text(encoding="utf-8")
    assert "b17742737e796305d829b3ad39eda3add0d79060" in provenance
    assert "byte-identical" in provenance


def test_taste_landing_worker_has_optional_images_without_browser_precondition():
    source = Path(takyon_core.__file__).read_text(encoding="utf-8")
    prompt = turn_runtime._business_bootstrap_instruction(
        "native-taste-preflight", "Build a polished SaaS", "live", archetype="web_saas"
    )
    assert "TASTE_LANDING_RENDER_PREFLIGHT_CONTRACT" not in source
    assert "native `design-taste-frontend` skill" in prompt
    assert "Safebox-gated image generation is available" in prompt
    assert "images, DESIGN.md, screenshots, and visual audits are optional" in prompt
    assert "bounded rendered-viewport preflight" not in prompt
    dockerfile = (
        HERMES_ROOT.parent / "deploy" / "argon-alpha-14" / "takyon-claude-worker.Dockerfile"
    ).read_text(encoding="utf-8")
    assert "apt-get install" not in dockerfile
    assert "agent-browser@0.26.0" not in dockerfile


def test_taste_render_scratch_cleanup_never_follows_symlinks(tmp_path):
    workspace = tmp_path / "site"
    workspace.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    marker = outside / "keep.txt"
    marker.write_text("keep", encoding="utf-8")
    (workspace / ".takyon-preflight").symlink_to(outside, target_is_directory=True)

    takyon_core._remove_taste_preflight_artifacts(workspace)

    assert not (workspace / ".takyon-preflight").exists()
    assert marker.read_text(encoding="utf-8") == "keep"


def test_product_owner_and_design_command_use_native_taste_for_every_worker():
    product = PRODUCT_SKILL.read_text(encoding="utf-8")
    command = DESIGN_COMMAND.read_text(encoding="utf-8")

    for text in (product, command):
        assert "design-taste-frontend" in text
        assert "native Claude Code skill" in text
        assert "every Agent SDK session" in text
        assert "guidance_skills" in text
        assert "DESIGN.md" in text
        assert "not publication conditions" in text
        assert "1440x900" not in text
        assert "390x844" not in text

    assert 'guidance_skills: ["taste-frontend"]' not in product
    assert "guidance_skills: []" not in product
    assert "`effort: high`, `max_turns: 90`, `budget_usd: 25.0`, and `timeout_ms: 1800000`" in product
    assert "For every `product/site` design pass" not in command

    upstream = TASTE_UPSTREAM.read_text(encoding="utf-8")
    assert (
        'npx skills add https://github.com/Leonxlnx/taste-skill --skill '
        '"design-taste-frontend"'
    ) in upstream


def test_open_design_templates_and_dependencies_are_removed():
    creative = HERMES_ROOT / "skills" / "creative"
    assert not any(path.is_file() for path in (creative / "claude-design").rglob("*"))
    for name in ("stripe", "openai", "doodle", "brutalist", "superhuman"):
        assert not any(
            path.is_file() for path in (creative / f"claude-design-{name}").rglob("*")
        )
    source = Path(takyon_core.__file__).read_text(encoding="utf-8")
    assert '"/tmp:rw,exec,size=384m"' in source
    assert '"claude-design-openai"' not in source
    assert '"claude-design-stripe"' not in source
    assert '"claude-design-superhuman"' not in source
    assert '"claude-design-doodle"' not in source
    assert '"claude-design-brutalist"' not in source


def test_navigation_component_is_force_refreshed_with_appkit_rails():
    assert "src/components/action-error-announcer.tsx" in takyon_core._STARTER_OWNED_REFRESH_FILES
    assert "src/components/site-navigation.tsx" in takyon_core._STARTER_OWNED_REFRESH_FILES
    assert "src/components/social-proof-marquee.tsx" not in takyon_core._STARTER_OWNED_REFRESH_FILES
    assert "src/components/subscription-cancellation.tsx" in takyon_core._STARTER_OWNED_REFRESH_FILES
    assert "src/lib/interaction-sounds.ts" in takyon_core._STARTER_OWNED_REFRESH_FILES
    assert "src/screens/support.tsx" in takyon_core._STARTER_OWNED_REFRESH_FILES


def test_subscription_cancel_control_is_starter_owned_and_backend_truthful():
    main = read("src/main.tsx")
    component = read("src/components/subscription-cancellation.tsx")
    hooks = read("src/lib/hooks.ts")
    runtime = (SCAFFOLD.parent / "runtime-client.js").read_text(encoding="utf-8")

    assert "SubscriptionCancellation" in main
    assert '<Route path="profile" element={<AccountRoute />} />' in main
    assert "hasNonterminalStripeSubscription(access.account)" in component
    assert "client.cancelSubscription()" in component
    assert "Cancel subscription now" in component
    assert "product_runtime_contract" in component
    assert 'policy?.effective_timing === "immediate"' in component
    assert 'policy?.refund_policy === "none"' in component
    assert "SubscriptionCancellationResult" in component
    assert "window.confirm" in component
    assert "setSuccessMessage(cancellationResultCopy(outcome))" in component
    assert "cancellation already succeeded" in component
    assert 'data-takyon-appkit="subscription-cancellation-success"' in component
    assert "export function hasNonterminalStripeSubscription" in hooks
    assert "sandbox_retired" in hooks
    assert 'source === "stripe"' in hooks
    active_helper = hooks.split("export function hasActiveStripeSubscription", 1)[1]
    assert "activePaidEntitlement(entitlement)" in active_helper
    cancel_method = runtime.split("async cancelSubscription()", 1)[1].split("async deleteAccount", 1)[0]
    assert 'JSON.stringify({ action: "cancel_subscription" })' in cancel_method
    assert "payload" not in cancel_method

    contract = takyon_core._subuser_app_kit_contract_block(None)
    assert "Self-service subscription cancellation is a non-removable AppKit invariant" in contract
    assert "machine-readable backend contract and exact action result" in contract
    assert '"effective_timing": "immediate"' in contract
    assert '"refund_policy": "none"' in contract
    assert "never add a refund action or control" in contract


def test_backend_cancellation_policy_is_one_machine_source_of_truth():
    assert takyon_core.subscription_cancellation_policy() == {
        "version": 1,
        "effective_timing": "immediate",
        "refund_policy": "none",
    }
    account_handler = inspect.getsource(takyon_core.handle_business_read_app_account)
    assert '"subscription_cancellation_policy": subscription_cancellation_policy()' in account_handler
    assert '"product_runtime_contract": product_runtime_contract()' in account_handler


def test_subscription_cancel_conformance_blocks_support_mediation(tmp_path):
    root = tmp_path / "site"
    for rel in ("src/main.tsx", "src/components/subscription-cancellation.tsx"):
        destination = root / rel
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(read(rel), encoding="utf-8")
    profile = root / "src" / "screens" / "profile.tsx"
    profile.parent.mkdir(parents=True, exist_ok=True)
    profile.write_text(
        'export const Profile = () => <p>To cancel or change billing, contact support.</p>;\n',
        encoding="utf-8",
    )

    markers = takyon_core._appkit_subscription_cancellation_markers(root)
    assert [marker["issue"] for marker in markers] == ["support_mediated_subscription_cancel"]
    blocker = takyon_core._appkit_subscription_cancellation_unfinished_blocker(
        {"inventory": {"risk_markers": markers}}
    )
    assert "support-mediated" in blocker
    assert "product cancellation does not expose refunds" in blocker

    profile.write_text(
        'export const Profile = () => <p>Cancel any active plan instantly in Account. Contact support for help understanding an invoice.</p>;\n',
        encoding="utf-8",
    )
    assert takyon_core._appkit_subscription_cancellation_markers(root) == []


@pytest.mark.parametrize(
    "source,issue",
    [
        (
            'export const Profile = () => <p>Your subscription ends August 12, 2026.</p>;\n',
            "unverified_subscription_cancellation_timing",
        ),
        (
            'export const Profile = () => <p>Cancellation is scheduled for the end of the current billing period.</p>;\n',
            "unverified_subscription_cancellation_timing",
        ),
        (
            'export const Refund = () => <button onClick={requestRefund}>Request refund</button>;\n',
            "product_subscription_refund_option",
        ),
    ],
)
def test_subscription_conformance_blocks_worker_timing_and_refund_options(
    tmp_path, source, issue
):
    root = tmp_path / "site"
    for rel in ("src/main.tsx", "src/components/subscription-cancellation.tsx"):
        destination = root / rel
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(read(rel), encoding="utf-8")
    profile = root / "src" / "screens" / "profile.tsx"
    profile.parent.mkdir(parents=True, exist_ok=True)
    profile.write_text(source, encoding="utf-8")

    markers = takyon_core._appkit_subscription_cancellation_markers(root)

    assert issue in [marker["issue"] for marker in markers]


def test_subscription_conformance_does_not_cross_ternary_copy_branches(tmp_path):
    root = tmp_path / "site"
    for rel in ("src/main.tsx", "src/components/subscription-cancellation.tsx"):
        destination = root / rel
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(read(rel), encoding="utf-8")
    account = root / "src" / "screens" / "account.tsx"
    account.parent.mkdir(parents=True, exist_ok=True)
    account.write_text(
        "const label = canceled ? 'Subscription canceled' : 'Subscribe to continue';\n",
        encoding="utf-8",
    )

    assert takyon_core._appkit_subscription_cancellation_markers(root) == []


def test_subscription_conformance_scans_product_actions_for_refund_options(tmp_path):
    root = tmp_path / "site"
    for rel in ("src/main.tsx", "src/components/subscription-cancellation.tsx"):
        destination = root / rel
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(read(rel), encoding="utf-8")
    action = root / "actions" / "refund-subscription.ts"
    action.parent.mkdir(parents=True, exist_ok=True)
    action.write_text(
        "export default async function requestRefund() { return { refund: true }; }\n",
        encoding="utf-8",
    )

    markers = takyon_core._appkit_subscription_cancellation_markers(root)

    assert any(marker["issue"] == "product_subscription_refund_option" for marker in markers)


def test_subscription_cancel_conformance_scans_beyond_300_files_and_prunes_generated_dirs(
    tmp_path,
):
    root = tmp_path / "site"
    for rel in ("src/main.tsx", "src/components/subscription-cancellation.tsx"):
        destination = root / rel
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(read(rel), encoding="utf-8")
    for index in range(350):
        path = root / "src" / "screens" / f"screen-{index:03d}.tsx"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"export const copy{index} = 'ordinary copy';\n", encoding="utf-8")
    (root / "src" / "screens" / "screen-349.tsx").write_text(
        "export const billing = 'To cancel or manage billing, contact support.';\n",
        encoding="utf-8",
    )
    ignored = root / "src" / "node_modules" / "generated.ts"
    ignored.parent.mkdir(parents=True, exist_ok=True)
    ignored.write_text("export const x = 'contact support to cancel';\n", encoding="utf-8")

    markers = takyon_core._appkit_subscription_cancellation_markers(root)

    support_markers = [
        marker for marker in markers if marker["issue"] == "support_mediated_subscription_cancel"
    ]
    assert [marker["path"] for marker in support_markers] == [
        "src/screens/screen-349.tsx"
    ]
    assert not any(marker["issue"] == "appkit_subscription_scan_incomplete" for marker in markers)


def test_mobile_profile_exposes_the_same_backend_truthful_self_service_cancel():
    profile = (MOBILE_SCAFFOLD / "src" / "screens" / "profile.tsx").read_text(
        encoding="utf-8"
    )
    cancellation = (
        MOBILE_SCAFFOLD / "src" / "components" / "subscription-cancellation.tsx"
    ).read_text(encoding="utf-8")
    runtime = (MOBILE_SCAFFOLD / "_takyon" / "runtime-client.ts").read_text(
        encoding="utf-8"
    )
    auth = (MOBILE_SCAFFOLD / "src" / "lib" / "product-auth.tsx").read_text(
        encoding="utf-8"
    )
    assert "SubscriptionCancellation" in profile
    assert "<SubscriptionCancellation />" in profile
    assert "hasNonterminalStripeSubscription(auth.account)" in cancellation
    assert "account?.entitlements" in auth
    assert 'source === "stripe"' in auth
    assert "await client.cancelSubscription()" in cancellation
    assert "Cancel subscription now" in cancellation
    assert "product_runtime_contract" in cancellation
    assert 'policy?.effective_timing === "immediate"' in cancellation
    assert 'policy?.refund_policy === "none"' in cancellation
    assert "SubscriptionCancellationResult" in cancellation
    assert "setCanceledLocally(true)" in cancellation
    assert "Stripe cancellation is complete" in cancellation
    assert "cancelSubscription(): Promise<SubscriptionCancellationResult>" in runtime
    cancel_method = runtime.split("async cancelSubscription()", 1)[1].split("async profile", 1)[0]
    assert 'JSON.stringify({ action: "cancel_subscription" })' in cancel_method
    assert "payload" not in cancel_method


def test_mobile_subscription_boundary_is_force_owned_and_conformance_checked(tmp_path):
    app_root = tmp_path / "product" / "app"
    app_root.mkdir(parents=True)
    (app_root / "app.json").write_text("{}\n", encoding="utf-8")
    profile = app_root / "src" / "screens" / "profile.tsx"
    profile.parent.mkdir(parents=True, exist_ok=True)
    custom_profile = (
        'import { SubscriptionCancellation } from "../components/subscription-cancellation";\n'
        "export default function Profile() { return <><h1>Custom account</h1>"
        "<SubscriptionCancellation /></>; }\n"
    )
    profile.write_text(custom_profile, encoding="utf-8")
    app_home = app_root / "src" / "screens" / "app-home.tsx"
    app_home.write_text(
        'export const Home = ({ router }: any) => <Button onPress={() => router.push("/profile")} />;\n',
        encoding="utf-8",
    )
    for rel in takyon_core._MOBILE_STARTER_OWNED_REFRESH_FILES:
        path = app_root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("// worker removed cancellation\n", encoding="utf-8")

    takyon_core._materialize_mobile_app_workspace(
        app_root,
        slug="future-mobile",
        business_name="Future Mobile",
        description="A mobile product",
        surface={},
    )

    assert takyon_core._mobile_appkit_subscription_cancellation_markers(app_root) == []
    assert profile.read_text(encoding="utf-8") == custom_profile
    for rel in takyon_core._MOBILE_STARTER_OWNED_REFRESH_FILES:
        assert (app_root / rel).read_bytes() == (MOBILE_SCAFFOLD / rel).read_bytes()
    with pytest.raises(takyon_core.TakyonError, match="scaffold-owned"):
        takyon_core._refuse_starter_owned_product_write(
            "product/app/src/components/subscription-cancellation.tsx"
        )
    takyon_core._refuse_starter_owned_product_write(
        "product/app/src/screens/profile.tsx"
    )


def test_mobile_subscription_conformance_blocks_support_mediation(tmp_path):
    app_root = tmp_path / "app"
    for rel in takyon_core._MOBILE_STARTER_OWNED_REFRESH_FILES:
        destination = app_root / rel
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(MOBILE_SCAFFOLD / rel, destination)
    profile = app_root / "src" / "screens" / "profile.tsx"
    profile.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(MOBILE_SCAFFOLD / "src/screens/profile.tsx", profile)
    app_home = app_root / "src" / "screens" / "app-home.tsx"
    shutil.copy2(MOBILE_SCAFFOLD / "src/screens/app-home.tsx", app_home)
    support_copy = app_root / "src" / "screens" / "billing-help.tsx"
    support_copy.parent.mkdir(parents=True, exist_ok=True)
    support_copy.write_text(
        "export const help = 'Contact support to cancel your subscription.';\n",
        encoding="utf-8",
    )

    markers = takyon_core._mobile_appkit_subscription_cancellation_markers(app_root)

    assert any(marker["issue"] == "support_mediated_subscription_cancel" for marker in markers)


def test_mobile_subscription_conformance_requires_discoverable_profile_navigation(tmp_path):
    app_root = tmp_path / "app"
    for rel in takyon_core._MOBILE_STARTER_OWNED_REFRESH_FILES:
        destination = app_root / rel
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(MOBILE_SCAFFOLD / rel, destination)
    profile = app_root / "src" / "screens" / "profile.tsx"
    profile.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(MOBILE_SCAFFOLD / "src/screens/profile.tsx", profile)
    home = app_root / "src" / "screens" / "app-home.tsx"
    home.write_text("export default function Home() { return <Text>Workspace</Text>; }\n")

    markers = takyon_core._mobile_appkit_subscription_cancellation_markers(app_root)

    assert any(
        marker["issue"] == "mobile_appkit_subscription_cancel_undiscoverable"
        for marker in markers
    )


def test_landing_has_no_forced_visual_module_and_keeps_default_interaction_sounds():
    main = read("src/main.tsx")
    landing = read("src/screens/landing.tsx")
    navigation = read("src/components/site-navigation.tsx")
    store = read("src/screens/store.tsx")
    sounds = read("src/lib/interaction-sounds.ts")
    assert "SocialProofMarquee" not in main
    assert "PublicLandingRoute" not in main
    assert '<Route path="/" element={<LandingScreen />} />' in main
    assert '<Route path="/store" element={<StoreScreen />} />' in main
    assert "StoreSection" not in landing
    assert "export function StoreScreen" in store
    assert "<StoreSection" in store
    for path, label in (
        ('to="/"', "Home"),
        ('to="/pricing"', "Pricing"),
        ('to="/faq"', "FAQ"),
        ('to="/privacy"', "Privacy"),
        ('to="/terms"', "Terms"),
    ):
        assert path in navigation
        assert label in navigation
    assert not (SCAFFOLD / "src/components/social-proof-marquee.tsx").exists()
    assert "animate-proof-marquee" not in read("src/index.css")
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

    assert takyon_core.DEFAULT_BOOTSTRAP_MONTHLY_PLAN_PRICE_CENTS == 1_900
    assert takyon_core.DEFAULT_BOOTSTRAP_MONTHLY_PLAN_INCLUDED_AI_BUDGET_MICROUSD == 5_000_000

    prompt = turn_runtime._business_bootstrap_instruction(
        "pricing-preservation-test",
        "Build a paid micro-SaaS with a $29 monthly plan",
        "live",
        archetype="web_saas",
    )
    assert "Use an explicitly requested monthly price when one is already known." in prompt
    assert "If pricing is not settled yet, keep the canonical starter monthly plan" in prompt
    assert "$19" not in prompt


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


def test_surface_context_reads_identity_name_label(tmp_path):
    business_root = tmp_path / "businesses" / "notewave"
    workspace = business_root / "product" / "site"
    strategy = business_root / "research" / "strategy.md"
    workspace.mkdir(parents=True)
    strategy.parent.mkdir(parents=True)
    strategy.write_text(
        "# notewave — Landing Brief\n\n## Identity\n- **Name:** NoteWave\n",
        encoding="utf-8",
    )

    payload = takyon_core._subuser_surface_context_payload(
        None, slug="notewave", workspace_root=workspace
    )

    assert payload["businessName"] == "NoteWave"


def test_subscription_gate_uses_canonical_product_name():
    layout = read("src/screens/app-layout.tsx")
    assert "continue to {productName}." in layout
    assert "unlocks the complete product" not in layout


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
