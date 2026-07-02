"""Tests for the CreativeProviderSpec registry (modularization plan §6b item 2).

These pin the STRUCTURAL money-gate invariant the operator made priority-one — the
registry must be STRICTLY STRONGER than today's hand-written reserve, never weaker:

  (1) a spec WITHOUT a money_gate is UNCONSTRUCTABLE (type-level "no ungated paid
      capability");
  (2) ``gated_creative_call`` RESERVES before the key is resolved, and RELEASES on any
      provider failure — the reserve->call->commit/release money envelope;
  (3) key resolution flows through the safebox alias route (``first_env_backed_value``),
      never ``os.environ`` — the secret boundary holds;
  (4) receipt-model == priced-model == spec.model (the logo truthfulness bug is fixed by
      construction);
  (5) an unpriced model FAILS CLOSED before any reserve.

stdlib + pytest + monkeypatch only; no network, no DB.
"""
from __future__ import annotations

import pytest


def _reg():
    from plugins.takyon import creative_provider_registry as reg

    return reg


# ─── (1) the structural invariant: no money_gate == unconstructable ───────────────


def test_spec_without_money_gate_raises():
    reg = _reg()
    with pytest.raises(reg.MissingMoneyGate):
        reg.CreativeProviderSpec(
            canonical_id="image:ungated",
            capability="image",
            provider="gemini",
            model="gemini-3.1-flash-image",
            pricing_key=("google", "gemini-3.1-flash-image"),
            key_aliases=("GEMINI_API_KEY",),
            safebox_route=("gemini", "logo"),
            # money_gate omitted -> UNCONSTRUCTABLE
        )


def test_spec_with_explicit_none_money_gate_raises():
    reg = _reg()
    with pytest.raises(reg.MissingMoneyGate):
        reg.CreativeProviderSpec(
            canonical_id="image:ungated2",
            capability="image",
            provider="gemini",
            model="gemini-3.1-flash-image",
            pricing_key=("google", "gemini-3.1-flash-image"),
            key_aliases=("GEMINI_API_KEY",),
            safebox_route=("gemini", "logo"),
            money_gate=None,
        )


def test_credit_action_gate_requires_action():
    reg = _reg()
    with pytest.raises(reg.MissingMoneyGate):
        reg.CreditActionGate(credit_action="")


def test_usage_rail_gate_requires_op():
    reg = _reg()
    with pytest.raises(reg.MissingMoneyGate):
        reg.UsageRailGate(meter_op="")


def test_registry_every_spec_has_a_money_gate():
    """No spec in the shipped registry is ungated — enforced structurally, asserted here
    as a guard against a future ungated addition slipping past review."""
    reg = _reg()
    assert reg.CREATIVE_PROVIDER_REGISTRY  # non-empty
    for spec in reg.CREATIVE_PROVIDER_REGISTRY.values():
        assert isinstance(spec.money_gate, (reg.CreditActionGate, reg.UsageRailGate))


def test_spec_exposes_no_raw_generate():
    """The spec has no raw generate() — the only invocation path is gated_creative_call."""
    reg = _reg()
    spec = reg.get_creative_provider_spec("image:gemini-logo")
    assert not hasattr(spec, "generate")


# ─── (4) receipt-model == priced-model == rendered-model ──────────────────────────


def test_gemini_logo_spec_render_priced_receipt_models_agree():
    """The logo truthfulness bug is fixed by construction: the spec's model (what the
    safebox renders) equals the priced model equals what core stamps on the receipt."""
    reg = _reg()
    from plugins.takyon import core, creative_gateway

    spec = reg.get_creative_provider_spec("image:gemini-logo")
    # spec.model == the model the safebox actually renders
    assert spec.model == creative_gateway._GEMINI_IMAGE_MODEL == "gemini-3.1-flash-image"
    # priced model (pricing_key[1]) == spec.model
    assert spec.priced_model == spec.model
    # core's receipt-stamped model == spec.model (was 'gemini-2.5-flash-image' — wrong)
    assert core._LOGO_IMAGE_MODEL == spec.model
    # core's receipt provider == the usage_pricing vendor used to price it
    assert core._LOGO_IMAGE_PROVIDER == spec.pricing_provider == "google"


def test_gemini_logo_priced_from_usage_pricing_ssot():
    """The provider cost is resolved from usage_pricing via the spec's pricing_key —
    the ONE price SSOT — not a literal. core's logo cost derives from the SAME spec."""
    reg = _reg()
    from plugins.takyon import core

    spec = reg.get_creative_provider_spec("image:gemini-logo")
    cost = reg.resolve_priced_model_cost_usd(spec)
    assert cost == pytest.approx(0.039)
    assert core._logo_provider_cost_usd() == pytest.approx(cost)


# ─── (5) an unpriced model fails closed ───────────────────────────────────────────


def test_unpriced_model_fails_closed():
    reg = _reg()
    unpriced = reg.CreativeProviderSpec(
        canonical_id="image:unpriced",
        capability="image",
        provider="gemini",
        model="gemini-nonexistent-image",
        pricing_key=("google", "gemini-nonexistent-image"),
        key_aliases=("GEMINI_API_KEY",),
        safebox_route=("gemini", "logo"),
        money_gate=reg.CreditActionGate(credit_action="logo_generate"),
    )
    with pytest.raises(reg.CreativeProviderUnpriced):
        reg.resolve_priced_model_cost_usd(unpriced)


def test_gated_call_refuses_unpriced_before_any_reserve(monkeypatch):
    """An unpriced model refuses BEFORE any safebox reserve — no reserve, no key, no call."""
    reg = _reg()
    from plugins.takyon import safebox

    def _no_reserve(*a, **k):
        raise AssertionError("reserve must not run for an unpriced model")

    monkeypatch.setattr(safebox, "creative_reserve", _no_reserve)

    unpriced = reg.CreativeProviderSpec(
        canonical_id="image:unpriced2",
        capability="image",
        provider="gemini",
        model="gemini-nonexistent-image",
        pricing_key=("google", "gemini-nonexistent-image"),
        key_aliases=("GEMINI_API_KEY",),
        safebox_route=("gemini", "logo"),
        money_gate=reg.CreditActionGate(credit_action="logo_generate"),
    )
    with pytest.raises(reg.CreativeProviderUnpriced):
        reg.gated_creative_call(
            unpriced,
            business="acme",
            operator_user_id="op-1",
            reservation_key="k:1",
            payload={"prompt": "x"},
        )


# ─── (2) reserve BEFORE key, commit on success, release on failure ────────────────


class _Balances:
    def __init__(self, balance=98, reserved=2):
        self.balance_credits = balance
        self.reserved_credits = reserved


def _order_recorder(monkeypatch, *, provider_call, commit_ok=True):
    """Wire the safebox creative gate with an ordered event log so a test can assert the
    reserve happens BEFORE the (server-side) key resolution inside the provider call."""
    reg = _reg()
    from plugins.takyon import safebox

    events: list[str] = []

    def _reserve(*, business, operator_user_id, action, reservation_key, units=1, metadata=None):
        events.append("reserve")
        return {"token": "cap-token", "reservation_key": reservation_key, "reserved_credits": 2}

    def _provider_call(provider, path, payload, *, token, **kw):
        events.append(f"provider_call:{provider}/{path}:{token}")
        return provider_call(provider, path, payload, token, events)

    def _commit(*, reservation_key, actual_credits=None, metadata=None):
        events.append("commit")
        if not commit_ok:
            raise AssertionError("commit called on a failed call")
        return _Balances()

    def _release(*, reservation_key, metadata=None):
        events.append("release")
        return _Balances(balance=100, reserved=0)

    monkeypatch.setattr(safebox, "creative_reserve", _reserve)
    monkeypatch.setattr(safebox, "creative_provider_call", _provider_call)
    monkeypatch.setattr(safebox, "creative_commit", _commit)
    monkeypatch.setattr(safebox, "creative_release", _release)
    return events


def test_gated_call_reserves_before_key_and_commits_on_success(monkeypatch):
    reg = _reg()
    spec = reg.get_creative_provider_spec("image:gemini-logo")

    def _provider_call(provider, path, payload, token, events):
        # The key is resolved SERVER-SIDE here (inside the gated route). Record it so the
        # test can prove reserve happened first.
        events.append("key_resolved_server_side")
        return {"image_base64": "AAAA", "format": "raw"}

    events = _order_recorder(monkeypatch, provider_call=_provider_call)

    out = reg.gated_creative_call(
        spec,
        business="acme",
        operator_user_id="op-1",
        reservation_key="k:success",
        payload={"prompt": "logo"},
    )
    assert out["success"] is True
    assert out["status"] == "created"
    assert out["model"] == spec.model == "gemini-3.1-flash-image"
    assert out["provider_cost_usd"] == pytest.approx(0.039)
    assert out["credits_charged"] == 2  # logo_generate == 2 credits
    # ORDER: reserve strictly before the key is resolved server-side, then commit; no release.
    assert events.index("reserve") < events.index("key_resolved_server_side")
    assert "commit" in events and "release" not in events
    assert events[-1] == "commit"


def test_gated_call_releases_on_provider_failure(monkeypatch):
    reg = _reg()
    spec = reg.get_creative_provider_spec("image:gemini-logo")

    class _ProviderBoom(RuntimeError):
        pass

    def _provider_call(provider, path, payload, token, events):
        events.append("provider_failed")
        raise _ProviderBoom("gemini_unconfigured")  # e.g. missing key fails closed server-side

    events = _order_recorder(monkeypatch, provider_call=_provider_call)

    with pytest.raises(_ProviderBoom):
        reg.gated_creative_call(
            spec,
            business="acme",
            operator_user_id="op-1",
            reservation_key="k:fail",
            payload={"prompt": "logo"},
        )
    # RESERVED first, then RELEASED on the failure; NEVER committed (no charge for a failed call).
    assert events.index("reserve") < events.index("provider_failed")
    assert "release" in events and "commit" not in events
    assert events[-1] == "release"


# ─── (3) key resolution goes through safebox aliases, never os.environ ────────────


def test_key_resolution_uses_safebox_aliases_not_os_environ(monkeypatch):
    """The provider key is resolved SERVER-SIDE via safebox.first_env_backed_value over
    the spec's aliases — never read from os.environ on the business plane.

    We monkeypatch first_env_backed_value to be THE source and forbid os.environ reads of
    any alias name. The provider caller in the safebox app resolves the key via the same
    alias route (creative_gateway._resolve_gemini_image_key -> safebox.first_env_backed_value)."""
    reg = _reg()
    from plugins.takyon import creative_gateway, safebox

    spec = reg.get_creative_provider_spec("image:gemini-logo")

    resolved_via: list[tuple[str, ...]] = []

    def _first_env_backed_value(*keys):
        resolved_via.append(tuple(keys))
        return "SAFEBOX-RESOLVED-KEY"

    monkeypatch.setattr(safebox, "first_env_backed_value", _first_env_backed_value)

    # Forbid reading any alias from os.environ for the key.
    import os as _os

    real_environ_get = _os.environ.get
    forbidden = set(spec.key_aliases)

    def _guarded_get(name, default=None):
        assert name not in forbidden, f"os.environ read of provider key alias {name!r} is forbidden"
        return real_environ_get(name, default)

    monkeypatch.setattr(_os.environ, "get", _guarded_get)

    # The gateway's key resolver (the SAME one the safebox provider caller uses) must
    # source the key from the alias route, not os.environ.
    key = creative_gateway._resolve_gemini_image_key()
    assert key == "SAFEBOX-RESOLVED-KEY"
    # It asked first_env_backed_value for exactly the spec's aliases.
    assert resolved_via == [spec.key_aliases]


def test_gateway_aliases_match_spec():
    """The gateway's key aliases (what the safebox resolver uses) equal the spec's — one
    source of truth for the safebox key boundary."""
    reg = _reg()
    from plugins.takyon import creative_gateway

    spec = reg.get_creative_provider_spec("image:gemini-logo")
    assert tuple(creative_gateway._GEMINI_KEY_ALIASES) == spec.key_aliases


# ─── registry derivations: aliases + denylist build FROM the spec ─────────────────


def test_api_env_aliases_derive_from_registry():
    reg = _reg()
    from plugins.takyon import core

    for provider, aliases in reg.creative_provider_alias_rows().items():
        core_row = core._API_ENV_ALIASES.get(provider)
        assert core_row is not None
        for alias in aliases:
            assert alias in core_row, f"{alias} missing from core._API_ENV_ALIASES[{provider!r}]"


def test_denylist_derives_from_registry():
    reg = _reg()
    from plugins.takyon import core

    deny = core.provider_key_denylist()
    for name in reg.creative_provider_denylist_names():
        assert name in deny, f"{name} should be denied for /v1/env egress"
