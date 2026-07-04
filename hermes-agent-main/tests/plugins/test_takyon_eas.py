"""EAS build-lane credential gate (plugins/takyon/eas.py) — pure tests.

Pins the fail-closed secret-boundary + money-gate contract: the Expo token resolves ONLY via the
injected safebox route; absent → EasUnconfigured with the exact `eas_unconfigured` gate token
(before any spend); lanes map to priced actions; unknown lane refused. No DB, no network, no real
token.
"""

from __future__ import annotations

import pytest

from plugins.takyon import eas


def test_resolve_returns_token_from_safebox_route():
    seen = {}

    def fake_first_env(*keys):
        seen["keys"] = keys
        return "  expo-robot-tok  "

    assert eas.resolve_expo_token(fake_first_env) == "expo-robot-tok"
    # It asks the safebox for exactly the EXPO_TOKEN aliases (never os.environ).
    assert seen["keys"] == eas.EXPO_TOKEN_ALIASES


def test_resolve_empty_when_absent_or_raising():
    assert eas.resolve_expo_token(lambda *k: "") == ""
    assert eas.resolve_expo_token(lambda *k: None) == ""

    def boom(*k):
        raise RuntimeError("safebox down")

    assert eas.resolve_expo_token(boom) == ""  # never propagates → caller fails closed cleanly


def test_assert_configured_fails_closed_with_gate_token():
    with pytest.raises(eas.EasUnconfigured) as exc:
        eas.assert_configured(lambda *k: "")
    assert "eas_unconfigured" in str(exc.value)


def test_assert_configured_returns_token_when_present():
    assert eas.assert_configured(lambda *k: "tok") == "tok"


def test_lane_pricing_maps_to_priced_actions():
    assert eas.pricing_key_for_lane("build_ios") == ("eas", "build_ios")
    assert eas.pricing_key_for_lane("update") == ("eas", "update")
    assert eas.pricing_key_for_lane("maestro_ios") == ("eas", "maestro_ios")
    with pytest.raises(eas.EasError):
        eas.pricing_key_for_lane("build_android")  # Apple-only: no android lane


def test_lane_pricing_keys_exist_in_usage_pricing_ssot():
    # Every declared lane must resolve to a real priced entry (fail closed otherwise).
    from agent.usage_pricing import has_known_pricing

    for lane, (provider, op) in eas.LANE_PRICING.items():
        assert has_known_pricing(op, provider=provider), f"{lane} -> {provider}/{op} unpriced"


def test_expo_alias_denied_over_v1_env():
    # The secret-boundary win: registering "expo"/"app_store_connect" in _API_ENV_ALIASES must put
    # EXPO_TOKEN + the ASC private key on the provider denylist (never vended over /v1/env).
    from plugins.takyon import core

    denylist = core.provider_key_denylist()
    assert "EXPO_TOKEN" in denylist
    assert "APP_STORE_CONNECT_PRIVATE_KEY" in denylist
