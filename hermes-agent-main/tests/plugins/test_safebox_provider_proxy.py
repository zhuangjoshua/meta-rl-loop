"""Operator/platform PROVIDER PROXY routes on the safebox service app.

This is the TRUSTED operator/platform counterpart to the metered ``/v1/providers/*`` business broker:
internal-token only (no capability, no per-call metering), it resolves the real provider key LOCALLY on
the safebox and forwards, so operator/platform/worker code never holds a raw key.

These tests are hermetic — NO network, NO live providers, NO live DB. ``httpx`` and the per-provider
key resolvers are stubbed via monkeypatch. For every route we pin the four hard invariants:

  (a) a wrong/absent internal token -> 401 (before any upstream work),
  (b) an unconfigured key -> 503 BEFORE any upstream call is attempted,
  (c) on success the real key NEVER appears in the response body or headers, and
  (d) the anthropic route forwards ``stream:true`` as a ``text/event-stream`` SSE passthrough.
"""

from __future__ import annotations

import json

import pytest
from starlette.testclient import TestClient

from plugins.takyon import safebox_app, safebox_provider_proxy

_TOKEN = "secret-internal-token"
# A canary that must NEVER be observed in any response surfaced to the caller.
_REAL_KEY = "sk-REAL-PROVIDER-KEY-CANARY-do-not-leak"


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv(safebox_app._SAFEBOX_TOKEN_ENV, _TOKEN)
    # The proxy needs no capability signing key, but set it for parity with a real safebox host.
    monkeypatch.setenv(safebox_app._CAP_SIGNING_KEY_ENV, "unused-by-the-proxy")
    return TestClient(safebox_app.build_safebox_app())


def _auth():
    return {"Authorization": f"Bearer {_TOKEN}"}


# ── Fake httpx transport (records the outbound request, returns a canned response) ────────────────
class _FakeResponse:
    def __init__(self, status_code, payload=None, *, text=None):
        self.status_code = int(status_code)
        if text is not None:
            self.text = text
        else:
            self.text = json.dumps(payload if payload is not None else {})

    def read(self):
        return self.text.encode("utf-8")

    def decode(self, *_a, **_k):  # not used directly; iter_raw yields bytes
        return self.text


class _FakeStream:
    """Context manager returned by httpx.Client.stream — yields raw SSE bytes verbatim."""

    def __init__(self, status_code, chunks):
        self.status_code = int(status_code)
        self._chunks = list(chunks)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self):
        return b"".join(self._chunks)

    def iter_raw(self):
        for chunk in self._chunks:
            yield chunk


class _FakeClient:
    """Stand-in for httpx.Client. Records every outbound (url, headers, json) so a test can assert the
    real key WAS injected into the request (proving local resolution) yet NEVER leaks into the response."""

    sent = []  # class-level capture across the request lifetime

    def __init__(self, *a, **k):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def close(self):
        pass

    # Non-streaming POST.
    response = None  # set per-test

    def post(self, url, *, headers=None, json=None):
        _FakeClient.sent.append({"url": url, "headers": dict(headers or {}), "json": json})
        return _FakeClient.response

    # Streaming POST.
    stream_response = None  # set per-test

    def stream(self, method, url, *, headers=None, json=None):
        _FakeClient.sent.append(
            {"url": url, "headers": dict(headers or {}), "json": json, "stream": True}
        )
        return _FakeClient.stream_response


@pytest.fixture(autouse=True)
def _reset_fake_client():
    _FakeClient.sent = []
    _FakeClient.response = None
    _FakeClient.stream_response = None
    yield


def _patch_httpx(monkeypatch):
    monkeypatch.setattr(safebox_provider_proxy.httpx, "Client", _FakeClient)


def _assert_no_key(resp):
    """The real key canary must appear nowhere the caller can see."""
    assert _REAL_KEY not in resp.text
    for value in resp.headers.values():
        assert _REAL_KEY not in value


# ── Anthropic (streaming-capable) ────────────────────────────────────────────────────────────────
def test_anthropic_wrong_token_is_401(client, monkeypatch):
    monkeypatch.setattr(safebox_provider_proxy, "_anthropic_key", lambda: _REAL_KEY)
    resp = client.post(
        "/v1/proxy/anthropic/messages",
        headers={"Authorization": "Bearer wrong"},
        json={"model": "claude-sonnet-4-6", "messages": [{"role": "user", "content": "hi"}]},
    )
    assert resp.status_code == 401


def test_anthropic_missing_token_is_401(client, monkeypatch):
    monkeypatch.setattr(safebox_provider_proxy, "_anthropic_key", lambda: _REAL_KEY)
    resp = client.post(
        "/v1/proxy/anthropic/messages",
        json={"model": "claude-sonnet-4-6", "messages": [{"role": "user", "content": "hi"}]},
    )
    assert resp.status_code == 401


def test_anthropic_unconfigured_is_503_before_upstream(client, monkeypatch):
    monkeypatch.setattr(safebox_provider_proxy, "_anthropic_key", lambda: "")
    _patch_httpx(monkeypatch)
    resp = client.post(
        "/v1/proxy/anthropic/messages",
        headers=_auth(),
        json={"model": "claude-sonnet-4-6", "messages": [{"role": "user", "content": "hi"}]},
    )
    assert resp.status_code == 503
    assert resp.json()["detail"] == "anthropic_unconfigured"
    assert _FakeClient.sent == []  # never reached upstream


def test_anthropic_non_stream_success_is_key_free_and_injects_key_upstream(client, monkeypatch):
    monkeypatch.setattr(safebox_provider_proxy, "_anthropic_key", lambda: _REAL_KEY)
    _patch_httpx(monkeypatch)
    _FakeClient.response = _FakeResponse(200, {"id": "msg_1", "content": [{"type": "text", "text": "ok"}]})
    resp = client.post(
        "/v1/proxy/anthropic/messages",
        headers=_auth(),
        json={"model": "claude-sonnet-4-6", "messages": [{"role": "user", "content": "hi"}]},
    )
    assert resp.status_code == 200
    assert resp.json()["id"] == "msg_1"
    _assert_no_key(resp)
    # The key WAS resolved locally and injected into the OUTBOUND request (x-api-key) — proving the
    # safebox forwards with the real key while the response stays key-free.
    sent = _FakeClient.sent[-1]
    assert sent["url"] == safebox_provider_proxy._ANTHROPIC_MESSAGES_URL
    assert sent["headers"]["x-api-key"] == _REAL_KEY
    assert sent["headers"]["anthropic-version"] == "2023-06-01"


def test_anthropic_upstream_error_is_sanitized_no_key(client, monkeypatch):
    monkeypatch.setattr(safebox_provider_proxy, "_anthropic_key", lambda: _REAL_KEY)
    _patch_httpx(monkeypatch)
    _FakeClient.response = _FakeResponse(400, text='{"error":"bad request"}')
    resp = client.post(
        "/v1/proxy/anthropic/messages",
        headers=_auth(),
        json={"model": "claude-sonnet-4-6", "messages": [{"role": "user", "content": "hi"}]},
    )
    assert resp.status_code == 400
    _assert_no_key(resp)


def test_anthropic_stream_true_is_text_event_stream(client, monkeypatch):
    monkeypatch.setattr(safebox_provider_proxy, "_anthropic_key", lambda: _REAL_KEY)
    _patch_httpx(monkeypatch)
    _FakeClient.stream_response = _FakeStream(
        200,
        [
            b"event: message_start\ndata: {\"type\":\"message_start\"}\n\n",
            b"event: message_stop\ndata: {\"type\":\"message_stop\"}\n\n",
        ],
    )
    resp = client.post(
        "/v1/proxy/anthropic/messages",
        headers=_auth(),
        json={
            "model": "claude-sonnet-4-6",
            "messages": [{"role": "user", "content": "hi"}],
            "stream": True,
        },
    )
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")
    # The upstream SSE bytes are proxied through verbatim.
    assert "message_start" in resp.text
    assert "message_stop" in resp.text
    _assert_no_key(resp)
    # The streaming request also went out with the local key injected (and as a stream).
    sent = _FakeClient.sent[-1]
    assert sent.get("stream") is True
    assert sent["headers"]["x-api-key"] == _REAL_KEY


def test_anthropic_also_mounted_at_v1_messages_for_sdk(client, monkeypatch):
    # The stock Anthropic SDK path: ANTHROPIC_BASE_URL=<safebox root> hits /v1/messages.
    monkeypatch.setattr(safebox_provider_proxy, "_anthropic_key", lambda: _REAL_KEY)
    _patch_httpx(monkeypatch)
    _FakeClient.response = _FakeResponse(200, {"id": "msg_sdk"})
    resp = client.post(
        "/v1/messages",
        headers=_auth(),
        json={"model": "claude-sonnet-4-6", "messages": [{"role": "user", "content": "hi"}]},
    )
    assert resp.status_code == 200
    assert resp.json()["id"] == "msg_sdk"
    _assert_no_key(resp)


# ── Tavily ───────────────────────────────────────────────────────────────────────────────────────
def test_tavily_wrong_token_is_401(client, monkeypatch):
    monkeypatch.setattr(safebox_provider_proxy, "_tavily_key", lambda: _REAL_KEY)
    resp = client.post(
        "/v1/proxy/tavily/search", headers={"Authorization": "Bearer wrong"}, json={"query": "x"}
    )
    assert resp.status_code == 401


def test_tavily_unconfigured_is_503_before_upstream(client, monkeypatch):
    monkeypatch.setattr(safebox_provider_proxy, "_tavily_key", lambda: "")
    called = []
    monkeypatch.setattr(
        safebox_provider_proxy, "_as_json_object", lambda b: called.append(b) or (b or {})
    )
    from plugins.takyon import ai_provider

    monkeypatch.setattr(
        ai_provider, "call_tavily", lambda *a, **k: pytest.fail("upstream must not be called")
    )
    resp = client.post("/v1/proxy/tavily/search", headers=_auth(), json={"query": "x"})
    assert resp.status_code == 503
    assert resp.json()["detail"] == "tavily_unconfigured"


def test_tavily_success_is_key_free(client, monkeypatch):
    monkeypatch.setattr(safebox_provider_proxy, "_tavily_key", lambda: _REAL_KEY)
    captured = {}
    from plugins.takyon import ai_provider

    def _fake_call(operation, body, key):
        captured["operation"] = operation
        captured["key"] = key
        # Tavily injects the key into the request body; the RETURNED JSON is key-free.
        return {"results": [{"title": "t"}], "operation": operation}

    monkeypatch.setattr(ai_provider, "call_tavily", _fake_call)
    resp = client.post("/v1/proxy/tavily/search", headers=_auth(), json={"query": "x"})
    assert resp.status_code == 200
    assert resp.json()["operation"] == "search"
    assert captured["key"] == _REAL_KEY  # resolved locally + passed to the leaf
    _assert_no_key(resp)


def test_tavily_unsupported_operation_is_400(client, monkeypatch):
    monkeypatch.setattr(safebox_provider_proxy, "_tavily_key", lambda: _REAL_KEY)
    resp = client.post("/v1/proxy/tavily/crawl", headers=_auth(), json={"url": "x"})
    assert resp.status_code == 400
    assert resp.json()["detail"] == "unsupported_tavily_operation"


# ── Gemini image ─────────────────────────────────────────────────────────────────────────────────
def test_gemini_wrong_token_is_401(client, monkeypatch):
    monkeypatch.setattr(safebox_provider_proxy, "_gemini_image_key", lambda: _REAL_KEY)
    resp = client.post(
        "/v1/proxy/gemini/image", headers={"Authorization": "Bearer wrong"}, json={"prompt": "logo"}
    )
    assert resp.status_code == 401


def test_gemini_unconfigured_is_503_before_upstream(client, monkeypatch):
    monkeypatch.setattr(safebox_provider_proxy, "_gemini_image_key", lambda: "")
    from plugins.takyon import creative_gateway

    monkeypatch.setattr(
        creative_gateway,
        "_gemini_generate_logo_png",
        lambda **k: pytest.fail("provider must not be called"),
    )
    resp = client.post("/v1/proxy/gemini/image", headers=_auth(), json={"prompt": "logo"})
    assert resp.status_code == 503
    assert resp.json()["detail"] == "gemini_unconfigured"


def test_gemini_success_is_key_free(client, monkeypatch):
    monkeypatch.setattr(safebox_provider_proxy, "_gemini_image_key", lambda: _REAL_KEY)
    captured = {}
    from plugins.takyon import creative_gateway

    def _fake_gen(*, api_key, prompt):
        captured["api_key"] = api_key
        captured["prompt"] = prompt
        return b"\x89PNG\r\n\x1a\nFAKEPNGBYTES"

    monkeypatch.setattr(creative_gateway, "_gemini_generate_logo_png", _fake_gen)
    resp = client.post("/v1/proxy/gemini/image", headers=_auth(), json={"prompt": "a logo"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["format"] == "png"
    assert data["image_base64"]  # base64 of the fake png
    assert captured["api_key"] == _REAL_KEY  # resolved locally
    assert captured["prompt"] == "a logo"
    _assert_no_key(resp)


def test_gemini_missing_prompt_is_400(client, monkeypatch):
    monkeypatch.setattr(safebox_provider_proxy, "_gemini_image_key", lambda: _REAL_KEY)
    resp = client.post("/v1/proxy/gemini/image", headers=_auth(), json={})
    assert resp.status_code == 400
    assert resp.json()["detail"] == "missing_prompt"


# ── OpenAI image ─────────────────────────────────────────────────────────────────────────────────
def test_openai_wrong_token_is_401(client, monkeypatch):
    monkeypatch.setattr(safebox_provider_proxy, "_openai_key", lambda: _REAL_KEY)
    resp = client.post(
        "/v1/proxy/openai/images", headers={"Authorization": "Bearer wrong"}, json={"prompt": "x"}
    )
    assert resp.status_code == 401


def test_openai_unconfigured_is_503_before_upstream(client, monkeypatch):
    monkeypatch.setattr(safebox_provider_proxy, "_openai_key", lambda: "")
    _patch_httpx(monkeypatch)
    resp = client.post("/v1/proxy/openai/images", headers=_auth(), json={"prompt": "x"})
    assert resp.status_code == 503
    assert resp.json()["detail"] == "openai_unconfigured"
    assert _FakeClient.sent == []


def test_openai_success_is_key_free_with_bearer_injected(client, monkeypatch):
    monkeypatch.setattr(safebox_provider_proxy, "_openai_key", lambda: _REAL_KEY)
    _patch_httpx(monkeypatch)
    _FakeClient.response = _FakeResponse(200, {"data": [{"b64_json": "AAAA"}]})
    resp = client.post(
        "/v1/proxy/openai/images", headers=_auth(), json={"prompt": "x", "model": "gpt-image-1"}
    )
    assert resp.status_code == 200
    assert resp.json()["data"][0]["b64_json"] == "AAAA"
    _assert_no_key(resp)
    sent = _FakeClient.sent[-1]
    assert sent["url"] == safebox_provider_proxy._OPENAI_IMAGES_URL
    assert sent["headers"]["Authorization"] == f"Bearer {_REAL_KEY}"


# ── FAL ──────────────────────────────────────────────────────────────────────────────────────────
def test_fal_wrong_token_is_401(client, monkeypatch):
    monkeypatch.setattr(safebox_provider_proxy, "_fal_key", lambda: _REAL_KEY)
    resp = client.post(
        "/v1/proxy/fal/fal-ai/kling-video", headers={"Authorization": "Bearer wrong"}, json={"x": 1}
    )
    assert resp.status_code == 401


def test_fal_unconfigured_is_503_before_upstream(client, monkeypatch):
    monkeypatch.setattr(safebox_provider_proxy, "_fal_key", lambda: "")
    _patch_httpx(monkeypatch)
    resp = client.post("/v1/proxy/fal/fal-ai/kling-video", headers=_auth(), json={"x": 1})
    assert resp.status_code == 503
    assert resp.json()["detail"] == "fal_unconfigured"
    assert _FakeClient.sent == []


def test_fal_success_is_key_free_with_key_header_injected(client, monkeypatch):
    monkeypatch.setattr(safebox_provider_proxy, "_fal_key", lambda: _REAL_KEY)
    _patch_httpx(monkeypatch)
    _FakeClient.response = _FakeResponse(200, {"video": {"url": "https://x/v.mp4"}})
    resp = client.post(
        "/v1/proxy/fal/fal-ai/kling-video/v3/pro/image-to-video", headers=_auth(), json={"x": 1}
    )
    assert resp.status_code == 200
    assert resp.json()["video"]["url"] == "https://x/v.mp4"
    _assert_no_key(resp)
    sent = _FakeClient.sent[-1]
    # FAL forwards to https://fal.run/<path> with an "Authorization: Key <key>" header.
    assert sent["url"] == "https://fal.run/fal-ai/kling-video/v3/pro/image-to-video"
    assert sent["headers"]["Authorization"] == f"Key {_REAL_KEY}"


# ── Route wiring smoke ───────────────────────────────────────────────────────────────────────────
def test_proxy_routes_are_registered():
    app = safebox_app.build_safebox_app()
    # Proxy routes are attached directly to the app (no _IncludedRouter wrapper), so app.routes is flat.
    paths = {getattr(route, "path", None) for route in app.routes}
    assert "/v1/proxy/anthropic/messages" in paths
    assert "/v1/messages" in paths
    assert "/v1/proxy/tavily/{operation}" in paths
    assert "/v1/proxy/gemini/image" in paths
    assert "/v1/proxy/openai/images" in paths
    assert "/v1/proxy/fal/{path:path}" in paths
    # The proxy is ADDITIVE: the metered business broker route is still mounted.
    assert "/v1/providers/anthropic/messages" in paths
