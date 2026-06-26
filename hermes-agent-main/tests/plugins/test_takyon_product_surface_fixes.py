from __future__ import annotations

from pathlib import Path

from plugins.takyon import core as takyon_core

_CORE_SRC = Path(takyon_core.__file__).read_text(encoding="utf-8")
_SCAFFOLD = Path(takyon_core.__file__).resolve().parent / "subuser_app_kit" / "scaffold"


def test_appkit_owned_src_rematerialized_on_rebuild():
    # The AppKit-owned rail wrappers (lib/hooks.ts, lib/takyon.ts, screens/app-layout.tsx, ...) are
    # seeded into a business once at bootstrap. They must ALSO be force-refreshed from the scaffold
    # on every rebuild, or a canonical rail fix (e.g. wiring the subscribe CTA to checkout) only
    # ever reaches newly-bootstrapped businesses and never the existing ones. Guard the mechanism.
    assert "def _rematerialize_appkit_owned_src(" in _CORE_SRC
    # ...and that it is actually invoked from the kit materializer (runs on every refresh build).
    assert "_rematerialize_appkit_owned_src(workspace_root" in _CORE_SRC
    for rail in ("src/main.tsx", "src/lib/hooks.ts", "src/lib/takyon.ts", "src/screens/app-layout.tsx"):
        assert rail in _CORE_SRC, f"{rail} missing from _APPKIT_OWNED_SRC_FILES"


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
    assert "[intent, access.authenticated, access.entitled, access.loading]" in hooks


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
