"""STEP C client transport (deploy/SAFEBOX-BROKER-REMEDIATION-PLAN.md).

Pins the runtime-plane client for the safebox broker: the rollout predicate, the request shaping for
the two identity shapes (product inline-mint vs operator pre-minted token), fail-closed behaviour, and
the transport timeout/unreachable mapping. No raw key is ever fetched here — the client only POSTs to
/v1/providers/* and returns the key-free result.
"""
import urllib.error

import pytest

from plugins.takyon import safebox


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch):
    for var in (
        "TAKYON_PROVIDER_BROKER",
        "TAKYON_SAFEBOX_URL",
        "TAKYON_HOST_ROLE",
        "TAKYON_SAFEBOX_TOKEN",
    ):
        monkeypatch.delenv(var, raising=False)


def test_provider_broker_enabled_requires_flag_remote_and_not_safebox_host(monkeypatch):
    # flag off -> never enabled
    monkeypatch.setenv("TAKYON_SAFEBOX_URL", "http://10.0.0.2:8000")
    assert safebox.provider_broker_enabled() is False
    # flag on but no remote configured -> not enabled (can't reach a broker)
    monkeypatch.setenv("TAKYON_PROVIDER_BROKER", "1")
    monkeypatch.delenv("TAKYON_SAFEBOX_URL", raising=False)
    assert safebox.provider_broker_enabled() is False
    # flag on + remote + safebox host itself -> NOT enabled (it resolves locally; it IS the authority)
    monkeypatch.setenv("TAKYON_SAFEBOX_URL", "http://10.0.0.2:8000")
    monkeypatch.setenv("TAKYON_HOST_ROLE", "safebox")
    assert safebox.provider_broker_enabled() is False
    # flag on + remote + runtime plane (operator) -> enabled
    monkeypatch.setenv("TAKYON_HOST_ROLE", "operator")
    assert safebox.provider_broker_enabled() is True


def test_broker_provider_call_product_inline_shape(monkeypatch):
    monkeypatch.setenv("TAKYON_SAFEBOX_URL", "http://10.0.0.2:8000")
    seen = {}

    def _fake_remote_json(method, path, payload=None, *, timeout=10.0):
        seen.update(method=method, path=path, payload=payload, timeout=timeout)
        return {"content": [{"type": "text", "text": "hi"}], "usage": {"input_tokens": 3}}

    monkeypatch.setattr(safebox, "_remote_json", _fake_remote_json)
    out = safebox.broker_provider_call(
        "anthropic",
        "messages",
        {"model": "claude", "messages": []},
        estimate_microusd=4000,
        business="climblog",
        action="anthropic.messages",
        session_token="sess_abc",
    )
    assert out["usage"]["input_tokens"] == 3
    assert seen["method"] == "POST"
    assert seen["path"] == "/v1/providers/anthropic/messages"
    assert seen["timeout"] >= 60  # provider-latency room, not the 10s env-read timeout
    body = seen["payload"]
    assert body["session_token"] == "sess_abc"
    assert body["business"] == "climblog"
    assert body["action"] == "anthropic.messages"
    assert body["estimate_microusd"] == 4000
    assert body["payload"] == {"model": "claude", "messages": []}
    assert "token" not in body  # inline-mint shape carries NO pre-minted token


def test_broker_provider_call_operator_token_shape(monkeypatch):
    monkeypatch.setenv("TAKYON_SAFEBOX_URL", "http://10.0.0.2:8000")
    seen = {}

    def _fake_remote_json(method, path, payload=None, *, timeout=10.0):
        seen.update(path=path, payload=payload)
        return {"ok": True}

    monkeypatch.setattr(safebox, "_remote_json", _fake_remote_json)
    safebox.broker_provider_call(
        "tavily", "search", {"query": "x"}, estimate_microusd=900, token="cap.tok.sig"
    )
    assert seen["path"] == "/v1/providers/tavily/search"
    body = seen["payload"]
    assert body["token"] == "cap.tok.sig"
    assert "session_token" not in body and "business" not in body


def test_broker_provider_call_unknown_route_raises(monkeypatch):
    monkeypatch.setenv("TAKYON_SAFEBOX_URL", "http://10.0.0.2:8000")
    with pytest.raises(ValueError, match="no safebox broker route"):
        safebox.broker_provider_call("openai", "chat", {}, estimate_microusd=1)


def test_broker_provider_call_without_remote_fails_closed(monkeypatch):
    # No TAKYON_SAFEBOX_URL -> must NOT silently resolve a local raw key; refuse instead.
    with pytest.raises(safebox.SafeboxAuthorityUnavailable):
        safebox.broker_provider_call(
            "anthropic", "messages", {}, estimate_microusd=1, session_token="s", business="b"
        )


def test_remote_json_maps_unreachable_to_504(monkeypatch):
    monkeypatch.setenv("TAKYON_SAFEBOX_URL", "http://10.0.0.2:8000")

    def _boom(req, timeout=None):
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr(safebox.urllib.request, "urlopen", _boom)
    with pytest.raises(safebox.RemoteSafeboxError) as ei:
        safebox._remote_json("POST", "/v1/providers/anthropic/messages", {"x": 1}, timeout=5)
    assert ei.value.status_code == 504
    assert ei.value.payload.get("detail") == "safebox_unreachable"
