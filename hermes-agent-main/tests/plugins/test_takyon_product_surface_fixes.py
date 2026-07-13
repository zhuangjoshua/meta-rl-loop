from __future__ import annotations

from pathlib import Path

from plugins.takyon import core as takyon_core

_CORE_SRC = Path(takyon_core.__file__).read_text(encoding="utf-8")
_SCAFFOLD = Path(takyon_core.__file__).resolve().parent / "subuser_app_kit" / "scaffold"


def test_appkit_owned_src_rematerialized_on_rebuild():
    # The scaffold-owned metadata floor + AppKit rail wrappers are seeded into a business once at
    # bootstrap. They must ALSO be force-refreshed from the scaffold on every rebuild, or a
    # canonical metadata/rail fix only ever reaches newly-bootstrapped businesses and never the
    # existing ones. Guard the mechanism.
    assert "def _rematerialize_starter_owned_files(" in _CORE_SRC
    # ...and that it is actually invoked from the kit materializer (runs on every refresh build).
    assert "_rematerialize_starter_owned_files(workspace_root" in _CORE_SRC
    for rail in (
        "index.html",
        "public/llms.txt",
        "public/robots.txt",
        "public/sitemap.xml",
        "src/main.tsx",
        "src/lib/hooks.ts",
        "src/lib/takyon.ts",
        "src/screens/app-layout.tsx",
    ):
        assert rail in _CORE_SRC, f"{rail} missing from _STARTER_OWNED_REFRESH_FILES"


def test_worker_contract_forbids_free_tier():
    # Takyon app products support exactly one paid entitlement; there is no free plan runtime-side.
    # The product-build worker contract must say so, or the worker invents fake "Free · N/month"
    # copy that advertises access the product cannot actually grant (the unentitled gate blocks it).
    assert "do NOT support a free plan" in _CORE_SRC


def test_subscribe_intent_is_reactive_to_the_intent_param():
    # The "/app?intent=subscribe" CTA is a client-side <Link>; the checkout effect MUST re-run when
    # the intent query param changes (so it is passed in and lives in the deps array). Reading only
    # window.location.search would fire solely on a full reload — the bug that made the button click
    # do nothing. Guard against that regression.
    hooks = (_SCAFFOLD / "src" / "lib" / "hooks.ts").read_text(encoding="utf-8")
    assert "export function useSubscribeIntent(" in hooks
    assert "intent: string | null" in hooks
    assert "autoStart = false" in hooks
    assert "autoStart || intent === \"subscribe\"" in hooks
    assert "[autoStart, intent, access.authenticated, access.entitled, access.loading]" in hooks


def test_login_and_signup_both_continue_directly_to_checkout():
    auth = (_SCAFFOLD / "src" / "lib" / "product-auth.tsx").read_text(encoding="utf-8")
    layout = (_SCAFFOLD / "src" / "screens" / "app-layout.tsx").read_text(encoding="utf-8")
    assert auth.count("startGoogleAuth(true)") == 2
    assert "const autoCheckout" in layout
    assert "Complete your subscription" not in layout
    assert "Checkout didn&apos;t open" in layout


def test_appkit_access_gate_uses_entitlements_not_legacy_account_flags():
    # Sub-user access authority is the app_entitlements projection returned by /account. A signed-in
    # app_users.status="active", stale user.tier, or legacy plan/account boolean must never make the
    # shared UI skip checkout or show paid access.
    hooks = (_SCAFFOLD / "src" / "lib" / "hooks.ts").read_text(encoding="utf-8")
    entitled_block = hooks.split("export function isAccountEntitled", 1)[1].split(
        "export function subscriptionStateFromAccount", 1
    )[0]
    subscription_block = hooks.split("export function subscriptionStateFromAccount", 1)[1].split(
        "export function resolveViewerCta", 1
    )[0]

    assert "accountEntitlements(payload).some" in entitled_block
    assert "payload.entitled" not in entitled_block
    assert "payload.plan" not in entitled_block
    assert "user?.tier" not in entitled_block
    assert "user?.tier" not in subscription_block
    assert "has_active_subscription" not in hooks
    assert "subscription.status" not in hooks


def test_scaffold_owned_product_writes_are_refused_with_routable_repair():
    # Every file in _STARTER_OWNED_REFRESH_FILES is force-rewritten from the scaffold on every kit
    # materialize — including inside a durable write's OWN commit (the surface-projection refresh),
    # so a CEO edit there self-reverts and then fails its sha postcondition identically forever
    # (observed live: two full CEO turns burned in the loop). The gate must refuse up front and
    # name the viable repair, and it must NOT block worker-owned product source or non-product paths.
    import pytest

    for rel in takyon_core._STARTER_OWNED_REFRESH_FILES:
        with pytest.raises(takyon_core.TakyonError, match="scaffold-owned"):
            takyon_core._refuse_starter_owned_product_write(f"product/site/{rel}")
    # Worker-owned screens and non-product paths stay writable.
    takyon_core._refuse_starter_owned_product_write("product/site/src/screens/app-home.tsx")
    takyon_core._refuse_starter_owned_product_write("product/site/src/lib/branding-extras.ts")
    takyon_core._refuse_starter_owned_product_write("research/strategy.md")
    # Both durable-mutation handlers actually call the gate (source-level wiring guard).
    write_src = _CORE_SRC.split("def handle_business_write_file(", 1)[1].split("\ndef ", 1)[0]
    patch_src = _CORE_SRC.split("def handle_business_patch_file(", 1)[1].split("\ndef ", 1)[0]
    assert "_refuse_starter_owned_product_write(rel)" in write_src
    assert "_refuse_starter_owned_product_write(rel)" in patch_src


def test_worker_contract_names_the_force_rewritten_scaffold_files():
    # The kit contract is the worker's only discovery surface for file ownership. If it does not
    # name the force-rewritten set, workers burn build retries "fixing" a file the platform
    # silently reverts before every build (the unwinnable formatSubscriptionState loop).
    contract = takyon_core._subuser_app_kit_contract_block(None)
    assert "force-rewritten" in contract
    for rel in takyon_core._STARTER_OWNED_REFRESH_FILES:
        assert f"`{rel}`" in contract, f"{rel} missing from the contract's scaffold-owned list"


def test_terminal_worker_failure_names_the_new_key_affordance():
    # Once a worker run is terminal, a same-key re-call replays the stored result verbatim (attach-
    # or-replay is intended design). The failure payload must SAY so, or the caller loops the
    # identical replay to budget exhaustion; the side-effect lane must additionally warn that a
    # fresh key re-executes the action.
    fields = takyon_core._terminal_worker_retry_fields(
        "business_claude_agent_task", "run-1", "failed", side_effect=False
    )
    assert fields["terminal"] is True
    assert "NEW idempotency_key" in fields["retry_guidance"]
    assert "replays this stored result" in fields["retry_guidance"]
    spendful = takyon_core._terminal_worker_retry_fields(
        "business_x_publish_outreach", "run-2", "failed", side_effect=True
    )
    assert "WILL re-execute the side effect" in spendful["retry_guidance"]
    # The stale-base raise routes to the same affordance instead of the actionless "re-hydrate".
    assert "re-delegating with a NEW idempotency_key" in _CORE_SRC
