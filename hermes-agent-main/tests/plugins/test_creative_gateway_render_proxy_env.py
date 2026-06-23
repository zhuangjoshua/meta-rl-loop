"""Unit tests for the creative-render subprocess env (safebox creative-credit gate cutover).

Covers ``plugins.takyon.creative_gateway._creative_subprocess_env`` — the env the gateway hands to the
UGC / static-ad render subprocesses. The creative-credit money gate is AUTHORITATIVE on the safebox:
the gateway has already reserved the action's fixed credits and minted a creative CAPABILITY, which it
passes to the subprocess so the subprocess can hit the GATED provider routes
(``/v1/providers/{openai/images,fal/{path}}``) key-free.

On a RUNTIME plane it injects the safebox coordinates + ``TAKYON_CREATIVE_CAPABILITY_TOKEN`` + the gate
flag and STRIPS any raw provider key. On the safebox/local plane the host is its own authority and the
subprocess keeps its local raw-key path (no gate flag injected). Fail closed (503) when the capability
is missing, or when remote authority is on but the gate coordinates are incomplete.

Hermetic: stdlib + pytest + monkeypatch, no network.
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException


def _gw():
    from plugins.takyon import creative_gateway as gw

    return gw


_CAP = "cap-token-abc"


# ─── runtime plane → gate env injected, raw key stripped ──────────────────────


def test_runtime_plane_injects_gate_env_and_strips_raw_keys(monkeypatch):
    gw = _gw()
    monkeypatch.setattr(gw, "_use_remote_authority", lambda: True)
    monkeypatch.setattr(gw.safebox, "provider_proxy_base_url", lambda: "http://10.116.0.2:8000")
    monkeypatch.setenv(gw.safebox._SAFEBOX_REMOTE_TOKEN_ENV, "internal-token")
    # A raw provider key in the runtime-plane process env must NOT survive into the subprocess.
    monkeypatch.setenv("OPENAI_API_KEY", "sk-RAW-must-not-pass")
    monkeypatch.setenv("FAL_KEY", "fal-RAW-must-not-pass")

    env = gw._creative_subprocess_env(_CAP)

    assert env["TAKYON_CREATIVE_VIA_PROXY"] == "1"
    assert env["TAKYON_CREATIVE_CAPABILITY_TOKEN"] == _CAP
    assert env[gw.safebox._SAFEBOX_REMOTE_URL_ENV] == "http://10.116.0.2:8000"
    assert env[gw.safebox._SAFEBOX_REMOTE_TOKEN_ENV] == "internal-token"
    # The raw provider keys are stripped — the gated route is the only sanctioned path.
    assert "OPENAI_API_KEY" not in env
    assert "FAL_KEY" not in env
    assert "FAL_API_KEY" not in env


def test_runtime_plane_missing_capability_fails_closed(monkeypatch):
    """No creative capability means the reserve did not happen → 503, never a raw-key subprocess."""
    gw = _gw()
    monkeypatch.setattr(gw, "_use_remote_authority", lambda: True)
    monkeypatch.setattr(gw.safebox, "provider_proxy_base_url", lambda: "http://10.116.0.2:8000")

    with pytest.raises(HTTPException) as exc:
        gw._creative_subprocess_env("")
    assert exc.value.status_code == 503
    assert exc.value.detail == "creative_capability_unavailable"


def test_runtime_plane_missing_base_url_fails_closed(monkeypatch):
    """Remote authority on, but the safebox base URL for the gated routes is missing → 503, never a
    raw-key subprocess on a runtime plane."""
    gw = _gw()
    monkeypatch.setattr(gw, "_use_remote_authority", lambda: True)
    monkeypatch.setattr(gw.safebox, "provider_proxy_base_url", lambda: "")

    with pytest.raises(HTTPException) as exc:
        gw._creative_subprocess_env(_CAP)
    assert exc.value.status_code == 503
    assert exc.value.detail == "creative_gate_unconfigured"


def test_runtime_plane_optional_internal_token(monkeypatch):
    """The internal token is optional (the safebox may run tokenless on a local rig): its absence must
    NOT fail the env build, but the gate flag + capability + base URL must still be injected."""
    gw = _gw()
    monkeypatch.setattr(gw, "_use_remote_authority", lambda: True)
    monkeypatch.setattr(gw.safebox, "provider_proxy_base_url", lambda: "http://10.116.0.2:8000")
    monkeypatch.delenv(gw.safebox._SAFEBOX_REMOTE_TOKEN_ENV, raising=False)

    env = gw._creative_subprocess_env(_CAP)
    assert env["TAKYON_CREATIVE_VIA_PROXY"] == "1"
    assert env["TAKYON_CREATIVE_CAPABILITY_TOKEN"] == _CAP
    assert env[gw.safebox._SAFEBOX_REMOTE_URL_ENV] == "http://10.116.0.2:8000"


# ─── safebox / local plane → inherited env, no gate injection ─────────────────


def test_local_plane_returns_inherited_env_without_gate_flag(monkeypatch):
    gw = _gw()
    monkeypatch.setattr(gw, "_use_remote_authority", lambda: False)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-local-dev")

    env = gw._creative_subprocess_env(_CAP)

    # No gate injection on the local/safebox plane (the host is its own authority).
    assert "TAKYON_CREATIVE_VIA_PROXY" not in env
    assert "TAKYON_CREATIVE_CAPABILITY_TOKEN" not in env
    # The local key is left intact so the local-dev subprocess path works unchanged.
    assert env["OPENAI_API_KEY"] == "sk-local-dev"


def test_local_plane_still_requires_capability(monkeypatch):
    """Even on the local plane a missing capability is a fail-closed 503 — the in-process reserve must
    have produced one before the subprocess runs."""
    gw = _gw()
    monkeypatch.setattr(gw, "_use_remote_authority", lambda: False)

    with pytest.raises(HTTPException) as exc:
        gw._creative_subprocess_env("")
    assert exc.value.status_code == 503
    assert exc.value.detail == "creative_capability_unavailable"
