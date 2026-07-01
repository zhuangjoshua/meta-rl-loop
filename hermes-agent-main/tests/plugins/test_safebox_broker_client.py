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
        "TAKYON_SAFEBOX_OPERATOR_TOKEN",
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


def test_remote_json_adds_operator_token_only_for_operator_routes(monkeypatch):
    monkeypatch.setenv("TAKYON_SAFEBOX_URL", "http://10.0.0.2:8000")
    monkeypatch.setenv("TAKYON_SAFEBOX_TOKEN", "shared-transport-token")
    monkeypatch.setenv("TAKYON_SAFEBOX_OPERATOR_TOKEN", "operator-route-token")
    seen: list[tuple[str, str | None]] = []

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return b"{}"

    def _capture(req, timeout=None):
        seen.append((req.full_url, req.get_header("X-takyon-operator-token")))
        return _Resp()

    monkeypatch.setattr(safebox.urllib.request, "urlopen", _capture)

    safebox._remote_json("POST", "/v1/operator/session-token", {"x": 1}, timeout=5)
    safebox._remote_json("POST", "/v1/postmark/send", {"x": 1}, timeout=5)
    safebox._remote_json("POST", "/v1/storage/put", {"x": 1}, timeout=5)
    safebox._remote_json("POST", "/v1/app-media/put", {"x": 1}, timeout=5)
    safebox._remote_json("POST", "/v1/providers/anthropic/messages", {"x": 1}, timeout=5)

    assert seen[0] == ("http://10.0.0.2:8000/v1/operator/session-token", "operator-route-token")
    assert seen[1] == ("http://10.0.0.2:8000/v1/postmark/send", "operator-route-token")
    assert seen[2] == ("http://10.0.0.2:8000/v1/storage/put", "operator-route-token")
    assert seen[3] == ("http://10.0.0.2:8000/v1/app-media/put", None)
    assert seen[4] == ("http://10.0.0.2:8000/v1/providers/anthropic/messages", None)


def test_remote_json_operator_route_requires_operator_token(monkeypatch):
    monkeypatch.setenv("TAKYON_SAFEBOX_URL", "http://10.0.0.2:8000")
    monkeypatch.setenv("TAKYON_SAFEBOX_TOKEN", "shared-transport-token")
    monkeypatch.setattr(safebox.urllib.request, "urlopen", lambda *a, **k: pytest.fail("opened socket"))

    with pytest.raises(safebox.SafeboxAuthorityUnavailable):
        safebox._remote_json("POST", "/v1/operator/session-token", {"x": 1}, timeout=5)


def test_meta_graph_forward_rejects_caller_chosen_host_before_socket(monkeypatch):
    monkeypatch.setenv("TAKYON_SAFEBOX_URL", "http://10.0.0.2:8000")
    monkeypatch.setenv("TAKYON_SAFEBOX_TOKEN", "shared-transport-token")
    monkeypatch.setenv("TAKYON_SAFEBOX_OPERATOR_TOKEN", "operator-route-token")
    monkeypatch.setattr(safebox.urllib.request, "urlopen", lambda *a, **k: pytest.fail("opened socket"))

    with pytest.raises(ValueError, match="meta_graph_host_not_allowed"):
        safebox.meta_graph_forward(
            method="GET",
            path="/me",
            params={"fields": "id"},
            host="169.254.169.254",
        )


def test_remote_env_mutation_disabled_before_socket(monkeypatch):
    monkeypatch.setenv("TAKYON_SAFEBOX_URL", "http://10.0.0.2:8000")
    monkeypatch.setenv("TAKYON_SAFEBOX_TOKEN", "shared-transport-token")
    monkeypatch.setenv("TAKYON_SAFEBOX_OPERATOR_TOKEN", "operator-route-token")
    monkeypatch.setattr(safebox.urllib.request, "urlopen", lambda *a, **k: pytest.fail("opened socket"))

    with pytest.raises(safebox.SafeboxAuthorityUnavailable, match="remote env mutation is disabled"):
        safebox.save_env_backed_value("OPENAI_API_KEY", "sk-nope")
    with pytest.raises(safebox.SafeboxAuthorityUnavailable, match="remote env mutation is disabled"):
        safebox.remove_env_backed_value("OPENAI_API_KEY")


def test_proxy_request_requires_operator_capability_before_socket(monkeypatch):
    monkeypatch.setenv("TAKYON_SAFEBOX_URL", "http://10.0.0.2:8000")
    monkeypatch.setenv("TAKYON_SAFEBOX_TOKEN", "shared-transport-token")
    monkeypatch.setattr(safebox.urllib.request, "urlopen", lambda *a, **k: pytest.fail("opened socket"))

    with pytest.raises(safebox.SafeboxAuthorityUnavailable, match="signed operator capability"):
        safebox.proxy_request("tavily", "search", {"query": "x"})


def test_proxy_request_sends_operator_capability_as_x_api_key(monkeypatch):
    monkeypatch.setenv("TAKYON_SAFEBOX_URL", "http://10.0.0.2:8000")
    monkeypatch.setenv("TAKYON_SAFEBOX_TOKEN", "shared-transport-token")
    seen = {}

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return b'{"ok": true}'

    def _capture(req, timeout=None):
        seen["url"] = req.full_url
        seen["authorization"] = req.get_header("Authorization")
        seen["x_api_key"] = req.get_header("X-api-key")
        seen["body"] = req.data
        return _Resp()

    monkeypatch.setattr(safebox.urllib.request, "urlopen", _capture)

    out = safebox.proxy_request(
        "tavily",
        "search",
        {"query": "x"},
        token="operator-session-capability",
        timeout=5,
    )

    assert out == {"ok": True}
    assert seen["url"] == "http://10.0.0.2:8000/v1/proxy/tavily/search"
    assert seen["authorization"] == "Bearer shared-transport-token"
    assert seen["x_api_key"] == "operator-session-capability"
    assert b'"query": "x"' in seen["body"]


def test_stripe_catalog_remote_request_uses_operator_token_but_checkout_does_not(monkeypatch):
    monkeypatch.setenv("TAKYON_SAFEBOX_URL", "http://10.0.0.2:8000")
    monkeypatch.setenv("TAKYON_SAFEBOX_TOKEN", "shared-transport-token")
    monkeypatch.setenv("TAKYON_SAFEBOX_OPERATOR_TOKEN", "operator-route-token")
    seen: list[tuple[str, str | None]] = []

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return b"{}"

    def _capture(req, timeout=None):
        seen.append((req.full_url, req.get_header("X-takyon-operator-token")))
        return _Resp()

    monkeypatch.setattr(safebox.urllib.request, "urlopen", _capture)

    safebox.stripe_request("products", {"metadata[business]": "climblog"}, method="POST")
    safebox.stripe_request("checkout/sessions", {"metadata[business]": "climblog"}, method="POST")

    assert seen == [
        ("http://10.0.0.2:8000/v1/stripe/request", "operator-route-token"),
        ("http://10.0.0.2:8000/v1/stripe/request", None),
    ]


def test_stripe_catalog_remote_request_requires_operator_token(monkeypatch):
    monkeypatch.setenv("TAKYON_SAFEBOX_URL", "http://10.0.0.2:8000")
    monkeypatch.setenv("TAKYON_SAFEBOX_TOKEN", "shared-transport-token")
    monkeypatch.setattr(safebox.urllib.request, "urlopen", lambda *a, **k: pytest.fail("opened socket"))

    with pytest.raises(safebox.SafeboxAuthorityUnavailable):
        safebox.stripe_request("prices", {"metadata[business]": "climblog"}, method="POST")


# ── Operator session-token mint (POST /v1/operator/session-token) ────────────────────────────────────
#
# The operator CEO loop + coding worker present this token (audience operator.session) on every Anthropic
# proxy call instead of a raw key. The client just POSTs the (business, owner, ceiling) and returns the
# token, fail-closed.


def test_mint_operator_session_token_posts_owner_and_returns_token(monkeypatch):
    monkeypatch.setenv("TAKYON_SAFEBOX_URL", "http://10.0.0.2:8000")
    captured: dict[str, object] = {}

    def fake_remote_json(method, path, payload=None, *, timeout=10.0):
        captured["method"] = method
        captured["path"] = path
        captured["payload"] = payload
        return {"token": "operator-session-token-xyz", "audience": "operator.session"}

    monkeypatch.setattr(safebox, "_remote_json", fake_remote_json)

    token = safebox.mint_operator_session_token("latexflow", "owner-1", max_cost_microusd=2_000_000)

    assert token == "operator-session-token-xyz"
    assert captured["method"] == "POST"
    assert captured["path"] == "/v1/operator/session-token"
    assert captured["payload"] == {
        "business": "latexflow",
        "operator_user_id": "owner-1",
        "max_cost_microusd": 2_000_000,
    }


def test_mint_operator_session_token_includes_ttl_when_given(monkeypatch):
    monkeypatch.setenv("TAKYON_SAFEBOX_URL", "http://10.0.0.2:8000")
    captured: dict[str, object] = {}

    def fake_remote_json(method, path, payload=None, *, timeout=10.0):
        captured["payload"] = payload
        return {"token": "tok"}

    monkeypatch.setattr(safebox, "_remote_json", fake_remote_json)

    safebox.mint_operator_session_token("latexflow", "owner-1", ttl_seconds=1800)
    assert captured["payload"]["ttl_seconds"] == 1800


def test_mint_operator_session_token_posts_root_scope_dashboard_session(monkeypatch):
    monkeypatch.setenv("TAKYON_SAFEBOX_URL", "http://10.0.0.2:8000")
    captured: dict[str, object] = {}

    def fake_remote_json(method, path, payload=None, *, timeout=10.0):
        captured["payload"] = payload
        return {"token": "tok"}

    monkeypatch.setattr(safebox, "_remote_json", fake_remote_json)

    safebox.mint_operator_session_token(
        "",
        "owner-1",
        session_token="dashboard-session-token",
    )
    assert captured["payload"] == {
        "business": "",
        "operator_user_id": "owner-1",
        "max_cost_microusd": 2_000_000,
        "session_token": "dashboard-session-token",
    }


def test_mint_operator_session_token_allows_root_scope_without_dashboard_session(monkeypatch):
    monkeypatch.setenv("TAKYON_SAFEBOX_URL", "http://10.0.0.2:8000")
    captured: dict[str, object] = {}

    def fake_remote_json(method, path, payload=None, *, timeout=10.0):
        captured["payload"] = payload
        return {"token": "tok"}

    monkeypatch.setattr(safebox, "_remote_json", fake_remote_json)

    safebox.mint_operator_session_token("", "owner-1")
    assert captured["payload"] == {
        "business": "",
        "operator_user_id": "owner-1",
        "max_cost_microusd": 2_000_000,
    }
    with pytest.raises(safebox.RemoteSafeboxError):
        safebox.mint_operator_session_token("latexflow", "")


def test_mint_operator_session_token_requires_remote_safebox(monkeypatch):
    monkeypatch.delenv("TAKYON_SAFEBOX_URL", raising=False)
    with pytest.raises(safebox.SafeboxAuthorityUnavailable):
        safebox.mint_operator_session_token("latexflow", "owner-1")


def test_mint_operator_session_token_fails_closed_on_empty_token(monkeypatch):
    monkeypatch.setenv("TAKYON_SAFEBOX_URL", "http://10.0.0.2:8000")
    monkeypatch.setattr(safebox, "_remote_json", lambda *a, **k: {"token": ""})
    with pytest.raises(safebox.RemoteSafeboxError):
        safebox.mint_operator_session_token("latexflow", "owner-1")


def test_mint_operator_session_token_propagates_safebox_refusal(monkeypatch):
    monkeypatch.setenv("TAKYON_SAFEBOX_URL", "http://10.0.0.2:8000")

    def fake_remote_json(*a, **k):
        raise safebox.RemoteSafeboxError(
            "not_business_owner", status_code=403, payload={"detail": "not_business_owner"}
        )

    monkeypatch.setattr(safebox, "_remote_json", fake_remote_json)
    with pytest.raises(safebox.RemoteSafeboxError):
        safebox.mint_operator_session_token("latexflow", "wrong-owner")
