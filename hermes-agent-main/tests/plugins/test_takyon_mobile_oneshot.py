"""Mobile one-shot lane (2026-07-08 god-mode enablement) — pure/rig-free pins.

Covers the four seams that turned `create --archetype app` into a real app pipeline:
  * archetype-aware bootstrap (step 5 + turn-cap bump, web prose byte-unchanged),
  * the host-independent builder lane (safebox-minted credentials resolver + builder_mode),
  * settle-at-trigger receipts carrying the expo.dev logs_url,
  * the operator creative-gate bypass flag parsing (client + authority halves).

The money-order invariants themselves live in test_takyon_store_build.py; the live proof is the
fresh-business E2E (create → bootstrap → signed preview-lane build).
"""

from __future__ import annotations

import pytest

from plugins.takyon import store_build, store_builder
from plugins.takyon.turn_runtime import (
    _MOBILE_BOOTSTRAP_EXTRA_TURNS,
    _bootstrap_turn_cap_for_goal,
    _business_bootstrap_instruction,
)


# ── archetype-aware bootstrap ─────────────────────────────────────────────────────────────


def test_bootstrap_turn_cap_bumps_only_for_mobile():
    web = _bootstrap_turn_cap_for_goal("build a saas")
    assert _bootstrap_turn_cap_for_goal("build a saas", archetype="mobile_app") == web + _MOBILE_BOOTSTRAP_EXTRA_TURNS
    assert _bootstrap_turn_cap_for_goal("build a saas", archetype="web_saas") == web
    assert _bootstrap_turn_cap_for_goal("build a saas", archetype="") == web


def test_bootstrap_instruction_web_prose_unchanged_without_archetype():
    base = _business_bootstrap_instruction("acme", "goal", "live", business_name="Acme")
    explicit_web = _business_bootstrap_instruction(
        "acme", "goal", "live", business_name="Acme", archetype="web_saas"
    )
    assert base == explicit_web
    assert "### 5. iOS app" not in base
    assert "takyon-mobile-app" not in base


def test_bootstrap_instruction_mobile_gains_step_5():
    mobile = _business_bootstrap_instruction(
        "acme", "goal", "live", business_name="Acme", archetype="mobile_app"
    )
    assert "### 5. iOS app build + first store-signed build" in mobile
    assert "takyon-mobile-app at step 5" in mobile
    assert "business_publish_mobile_release with lane preview" in mobile
    assert "FRESH idempotency_key" in mobile
    # The web steps stay intact ahead of it.
    assert "### 4. X post" in mobile
    assert mobile.index("### 4. X post") < mobile.index("### 5. iOS app")


# ── host-independent builder lane ─────────────────────────────────────────────────────────


def _full_mint_payload() -> dict:
    return {
        "business": "acme",
        "bundle_identifier": "com.coscale.acme",
        "team_id": "TEAM123456",
        "expo_owner": "coscale",
        "expo_token": "tok_" + "x" * 20,
        "dist_cert_id": "CERTID",
        "dist_p12_b64": "cDEyYnl0ZXM=",
        "dist_p12_password": "pw",
        "profile_b64": "cHJvZmlsZQ==",
        "minted_at": 1,
    }


def test_safebox_resolver_returns_preminted_creds(monkeypatch):
    from plugins.takyon import safebox as sb

    monkeypatch.setattr(sb, "store_eas_build_credentials", lambda business, capabilities=None: _full_mint_payload())
    creds = store_builder.resolve_safebox_store_credentials("acme", capabilities=["ASSOCIATED_DOMAINS"])
    # ASC key fields stay EMPTY — the .p8 never reaches this host.
    assert creds.key_id == "" and creds.issuer_id == "" and creds.private_key_pem == ""
    assert creds.preminted_profile_b64 == "cHJvZmlsZQ=="
    assert creds.dist_p12_b64 == "cDEyYnl0ZXM=" and creds.dist_p12_path == ""
    assert creds.expo_token.startswith("tok_")
    # repr never leaks secret material.
    assert "tok_" not in repr(creds) and "pw" not in repr(creds)


@pytest.mark.parametrize("missing", ["expo_token", "dist_p12_b64", "profile_b64", "dist_p12_password"])
def test_safebox_resolver_fails_closed_on_incomplete_bundle(monkeypatch, missing):
    from plugins.takyon import safebox as sb

    payload = _full_mint_payload()
    payload[missing] = ""
    monkeypatch.setattr(sb, "store_eas_build_credentials", lambda business, capabilities=None: payload)
    with pytest.raises(store_builder.StoreBuilderUnconfigured):
        store_builder.resolve_safebox_store_credentials("acme")


def test_safebox_resolver_fails_closed_on_transport_error(monkeypatch):
    from plugins.takyon import safebox as sb

    def _boom(business, capabilities=None):
        raise RuntimeError("safebox unreachable")

    monkeypatch.setattr(sb, "store_eas_build_credentials", _boom)
    with pytest.raises(store_builder.StoreBuilderUnconfigured):
        store_builder.resolve_safebox_store_credentials("acme")


def test_builder_mode_local_wins(monkeypatch):
    monkeypatch.setattr(store_builder, "is_configured", lambda secrets_dir=None: True)
    assert store_builder.builder_mode() == "local"


def test_builder_mode_safebox_when_toolchain_and_remote(monkeypatch):
    from plugins.takyon import safebox as sb

    monkeypatch.setattr(store_builder, "is_configured", lambda secrets_dir=None: False)
    monkeypatch.setattr(store_builder.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(sb, "_use_remote_authority", lambda: True)
    assert store_builder.builder_mode() == "safebox"


def test_builder_mode_empty_without_toolchain(monkeypatch):
    monkeypatch.setattr(store_builder, "is_configured", lambda secrets_dir=None: False)
    monkeypatch.setattr(store_builder.shutil, "which", lambda name: None)
    assert store_builder.builder_mode() == ""


def test_builder_mode_empty_without_remote_safebox(monkeypatch):
    from plugins.takyon import safebox as sb

    monkeypatch.setattr(store_builder, "is_configured", lambda secrets_dir=None: False)
    monkeypatch.setattr(store_builder.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(sb, "_use_remote_authority", lambda: False)
    assert store_builder.builder_mode() == ""


# ── settle metadata carries the logs receipt ──────────────────────────────────────────────


def test_run_build_settle_metadata_carries_logs_url():
    settled: dict = {}

    def reserve(business, credits, key):
        return {"key": key}

    def settle(key, actual, meta):
        settled.update(meta)

    def release(key, meta):  # pragma: no cover - not hit on success
        raise AssertionError("release must not run on a successful trigger")

    result = store_build.run_build(
        business_slug="acme",
        lane="preview",
        credits=4,
        reservation_key="rk-1",
        reserve=reserve,
        settle=settle,
        release=release,
        invoke_eas=lambda: store_build.BuildResult(
            build_id="b-1", lane="preview", logs_url="https://expo.dev/accounts/coscale/projects/acme/builds/b-1"
        ),
    )
    assert result.logs_url.endswith("/builds/b-1")
    assert settled["build_id"] == "b-1"
    assert settled["logs_url"].endswith("/builds/b-1")


def test_run_build_without_logs_url_omits_the_key():
    settled: dict = {}
    store_build.run_build(
        business_slug="acme",
        lane="preview",
        credits=4,
        reservation_key="rk-2",
        reserve=lambda b, c, k: None,
        settle=lambda k, a, m: settled.update(m),
        release=lambda k, m: None,
        invoke_eas=lambda: store_build.BuildResult(build_id="b-2", lane="preview"),
    )
    assert "logs_url" not in settled


# ── operator creative-gate bypass flag (both halves parse identically) ────────────────────


@pytest.mark.parametrize("raw,expected", [("1", True), ("true", True), ("YES", True), ("on", True), ("", False), ("0", False), ("off", False)])
def test_operator_creative_gate_flag_parsing(monkeypatch, raw, expected):
    from plugins.takyon import core, safebox_app

    if raw:
        monkeypatch.setenv("TAKYON_OPERATOR_CREATIVE_GATE_DISABLED", raw)
    else:
        monkeypatch.delenv("TAKYON_OPERATOR_CREATIVE_GATE_DISABLED", raising=False)
    assert core._operator_creative_gate_disabled() is expected
    assert safebox_app._operator_creative_gate_disabled() is expected


# ── regression pin: the mobile_release audience must ride the safebox creative gate ───────
# (c87547a0 was clobbered once by a stale-base core.py push; a missing entry silently
# downgrades the reserve to the local branch, which refuses on remote planes.)


def test_mobile_release_audience_registered_on_both_halves():
    from plugins.takyon import core, safebox_app

    assert core._creative_credit_action_audience("mobile_release") == "creative.mobile_release"
    assert safebox_app._CREATIVE_AUDIENCE_CREDIT_ACTION.get("creative.mobile_release") == "mobile_release"
