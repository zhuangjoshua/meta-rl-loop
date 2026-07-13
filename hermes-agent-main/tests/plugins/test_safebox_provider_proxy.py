"""Operator/platform PROVIDER PROXY routes on the safebox service app — AUTHORITATIVELY MONEY-GATED.

These are the operator/platform counterpart to the metered ``/v1/providers/*`` business broker: keyless
egress (the safebox resolves the real provider key LOCALLY and forwards) AND an authoritative money gate
(every call reserves -> settles the OPERATOR's control-plane budget keyed on the verified operator scope,
BEFORE/AFTER resolving the key). There is NO ungated path, and the shared Safebox transport token is not
spend authority. After the creative-credit cutover, ONLY the Anthropic (streaming) + Tavily proxy routes live here;
the two route-wiring tests pin that the ungated creative proxy routes are gone.

These tests are hermetic — NO network, NO live providers, NO live DB. ``httpx`` and the per-provider key
resolvers are stubbed via monkeypatch, and the operator budget rail (``_OperatorBudgetAdapter``) is
replaced with an in-memory fake that records reserve/settle/release and can refuse on demand. For every
remaining route we pin the hard invariants:

  (a) a wrong/absent credential -> 401 (before any upstream work / before any reserve),
  (b) an out-of-budget operator -> 402 BEFORE any provider key is resolved or any upstream call is made,
  (c) reserve -> settle happens exactly once per call on the safebox (release on failure),
  (d) an unconfigured key -> 503 (after the reserve, which is released), BEFORE any upstream call,
  (e) on success the real key NEVER appears in the response body or headers,
  (f) the anthropic route forwards ``stream:true`` as a ``text/event-stream`` SSE passthrough and settles
      the ACTUAL cost parsed from the usage event,
  (g) a REUSABLE session capability works across >1 call (no single-use rejection).
"""

from __future__ import annotations

import asyncio
import json
import time

import pytest
from starlette.testclient import TestClient
from starlette.requests import ClientDisconnect

from plugins.takyon import safebox_app, safebox_provider_proxy
from plugins.takyon.safebox_capability import CapabilityScope, mint_capability

_TOKEN = "secret-internal-token"
_OPERATOR_TOKEN = "operator-route-token-not-on-subuser"
_SIGNING_KEY = b"safebox-only-signing-key-not-on-clients"
_OPERATOR = "operator_user_A"
# A canary that must NEVER be observed in any response surfaced to the caller.
_REAL_KEY = "sk-REAL-PROVIDER-KEY-CANARY-do-not-leak"


# ── Fake operator budget rail (records reserve/settle/release; refuses on demand) ─────────────────
class _FakeBudget:
    """In-memory stand-in for the operator control-plane money rail. Records every reserve/settle/release
    so a test can assert the gate fired exactly once per call. ``refuse`` makes reserve raise the
    out-of-budget error BEFORE any provider work."""

    events: list = []
    refuse = False

    def reserve(self, scope, estimate_microusd):
        from plugins.takyon.safebox_app import OperatorBudgetExceeded

        if _FakeBudget.refuse:
            raise OperatorBudgetExceeded(estimate_cents=99, allowance_available_cents=0)
        rid = f"r{len(_FakeBudget.events)}"
        _FakeBudget.events.append(("reserve", scope.takyon_user_id, int(estimate_microusd), rid))
        return {"id": rid, "operator_user_id": scope.takyon_user_id, "estimate": int(estimate_microusd)}

    def settle(self, reservation, actual_microusd):
        _FakeBudget.events.append(("settle", reservation["id"], int(actual_microusd)))

    def release(self, reservation):
        _FakeBudget.events.append(("release", reservation["id"]))


@pytest.fixture(autouse=True)
def _patch_budget(monkeypatch):
    _FakeBudget.events = []
    _FakeBudget.refuse = False
    monkeypatch.setattr(safebox_app, "_OperatorBudgetAdapter", _FakeBudget)
    yield


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv(safebox_app._SAFEBOX_TOKEN_ENV, _TOKEN)
    monkeypatch.setenv(safebox_app._CAP_SIGNING_KEY_ENV, _SIGNING_KEY.decode())
    monkeypatch.setenv(safebox_app._OPERATOR_TOKEN_ENV, _OPERATOR_TOKEN)
    monkeypatch.setenv(safebox_app._OPERATOR_CLIENTS_ENV, "testclient")
    # Kept set to prove the old platform-operator fallback does not revive shared-token spend.
    monkeypatch.setenv("TAKYON_PLATFORM_OPERATOR_USER_ID", _OPERATOR)
    return TestClient(safebox_app.build_safebox_app())


def _auth():
    return {"Authorization": f"Bearer {_TOKEN}", "X-Takyon-Operator-Token": _OPERATOR_TOKEN}


def _session_cap(*, max_cost_microusd=5_000_000, ttl=3600, nonce="sess-1"):
    """A SESSION-scoped operator capability (audience = operator.session). Reusable across calls."""
    scope = CapabilityScope(
        takyon_user_id=_OPERATOR,
        business_slug="acme",
        app_user_id=None,
        action=safebox_app._OPERATOR_SESSION_AUDIENCE,
        max_cost_microusd=max_cost_microusd,
    )
    return mint_capability(
        scope,
        signing_key=_SIGNING_KEY,
        audience=safebox_app._OPERATOR_SESSION_AUDIENCE,
        nonce=nonce,
        issued_at=int(time.time()),
        ttl_seconds=ttl,
    )


def _cap_headers(cap):
    # The SDK presents the capability as x-api-key.
    return {"x-api-key": cap}


# ── Fake httpx transport (records the outbound request, returns a canned response) ────────────────
class _FakeResponse:
    def __init__(self, status_code, payload=None, *, text=None):
        self.status_code = int(status_code)
        self.text = text if text is not None else json.dumps(payload if payload is not None else {})

    def read(self):
        return self.text.encode("utf-8")


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

    def iter_bytes(self):
        for chunk in self._chunks:
            yield chunk


class _FailingStream(_FakeStream):
    def __init__(self, exc, *, open_failure=False, chunks=()):
        super().__init__(200, chunks)
        self.exc = exc
        self.open_failure = open_failure

    def __enter__(self):
        if self.open_failure:
            raise self.exc
        return self

    def iter_bytes(self):
        yield from super().iter_bytes()
        raise self.exc


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

    response = None  # set per-test

    def post(self, url, *, headers=None, json=None):
        _FakeClient.sent.append({"url": url, "headers": dict(headers or {}), "json": json})
        return _FakeClient.response

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
    assert _REAL_KEY not in resp.text
    for value in resp.headers.values():
        assert _REAL_KEY not in value


def _reserves():
    return [e for e in _FakeBudget.events if e[0] == "reserve"]


def _settles():
    return [e for e in _FakeBudget.events if e[0] == "settle"]


def _releases():
    return [e for e in _FakeBudget.events if e[0] == "release"]


# ══ Anthropic (streaming-capable, money-gated) ════════════════════════════════════════════════════
def test_anthropic_wrong_token_is_401_before_any_reserve(client, monkeypatch):
    monkeypatch.setattr(safebox_provider_proxy, "_anthropic_key", lambda: _REAL_KEY)
    resp = client.post(
        "/v1/proxy/anthropic/messages",
        headers={"Authorization": "Bearer wrong"},
        json={"model": "claude-sonnet-4-6", "messages": [{"role": "user", "content": "hi"}]},
    )
    assert resp.status_code == 401
    assert _reserves() == []  # never reached the money gate


def test_anthropic_missing_token_is_401(client, monkeypatch):
    monkeypatch.setattr(safebox_provider_proxy, "_anthropic_key", lambda: _REAL_KEY)
    resp = client.post(
        "/v1/proxy/anthropic/messages",
        json={"model": "claude-sonnet-4-6", "messages": [{"role": "user", "content": "hi"}]},
    )
    assert resp.status_code == 401


def test_anthropic_out_of_budget_is_402_before_key_or_upstream(client, monkeypatch):
    # The operator budget refuses -> 402 BEFORE any provider key is resolved or any upstream call made.
    _FakeBudget.refuse = True
    key_calls = []
    monkeypatch.setattr(
        safebox_provider_proxy, "_anthropic_key", lambda: key_calls.append(1) or _REAL_KEY
    )
    monkeypatch.setattr(
        safebox_provider_proxy.httpx,
        "Client",
        lambda *a, **k: pytest.fail("upstream must not be reached when out of budget"),
    )
    resp = client.post(
        "/v1/messages",
        headers=_cap_headers(_session_cap()),
        json={"model": "claude-sonnet-4-6", "messages": [{"role": "user", "content": "hi"}]},
    )
    assert resp.status_code == 402
    assert resp.json()["detail"]["error"] == "operator_budget_exceeded"
    assert key_calls == []  # key never resolved
    assert _settles() == [] and _releases() == []  # nothing held to settle/release


def test_anthropic_over_ceiling_is_402_before_upstream(client, monkeypatch):
    # A capability's signed per-call ceiling is a HARD cap: an estimate above it is refused before any
    # reserve / key / upstream call.
    monkeypatch.setattr(safebox_provider_proxy, "_anthropic_estimate_microusd", lambda p: 9_000_000)
    monkeypatch.setattr(
        safebox_provider_proxy.httpx,
        "Client",
        lambda *a, **k: pytest.fail("upstream must not be reached over ceiling"),
    )
    resp = client.post(
        "/v1/messages",
        headers=_cap_headers(_session_cap(max_cost_microusd=1_000_000)),
        json={"model": "claude-sonnet-4-6", "messages": [{"role": "user", "content": "hi"}]},
    )
    assert resp.status_code == 402
    assert resp.json()["detail"] == "estimate_exceeds_ceiling"
    assert _reserves() == []  # never reserved


def test_anthropic_unconfigured_key_is_503_and_releases_hold(client, monkeypatch):
    # Key unconfigured -> 503 AFTER the reserve (which is released), BEFORE any upstream call.
    monkeypatch.setattr(safebox_provider_proxy, "_anthropic_estimate_microusd", lambda p: 1000)
    monkeypatch.setattr(safebox_provider_proxy, "_anthropic_key", lambda: "")
    monkeypatch.setattr(
        safebox_provider_proxy.httpx,
        "Client",
        lambda *a, **k: pytest.fail("upstream must not be reached without a key"),
    )
    resp = client.post(
        "/v1/messages",
        headers=_cap_headers(_session_cap()),
        json={"model": "claude-sonnet-4-6", "messages": [{"role": "user", "content": "hi"}]},
    )
    assert resp.status_code == 503
    assert resp.json()["detail"] == "anthropic_unconfigured"
    assert len(_reserves()) == 1 and len(_releases()) == 1 and _settles() == []


def test_anthropic_non_stream_reserves_settles_once_and_is_key_free(client, monkeypatch):
    monkeypatch.setattr(safebox_provider_proxy, "_anthropic_estimate_microusd", lambda p: 5000)
    monkeypatch.setattr(
        safebox_provider_proxy, "_anthropic_actual_microusd_from_response", lambda p, r: 1800
    )
    monkeypatch.setattr(safebox_provider_proxy, "_anthropic_key", lambda: _REAL_KEY)
    _patch_httpx(monkeypatch)
    _FakeClient.response = _FakeResponse(200, {"id": "msg_1", "usage": {"output_tokens": 7}})
    resp = client.post(
        "/v1/messages",
        headers=_cap_headers(_session_cap()),
        json={"model": "claude-sonnet-4-6", "messages": [{"role": "user", "content": "hi"}]},
    )
    assert resp.status_code == 200
    assert resp.json()["id"] == "msg_1"
    _assert_no_key(resp)
    # Reserve at the estimate, settle the ACTUAL from the response usage — exactly once each.
    assert [e for e in _reserves()] == [("reserve", _OPERATOR, 5000, "r0")]
    assert _settles() == [("settle", "r0", 1800)]
    assert _releases() == []
    # The key WAS resolved locally + injected into the OUTBOUND request, never the response.
    sent = _FakeClient.sent[-1]
    assert sent["headers"]["x-api-key"] == _REAL_KEY
    assert sent["headers"]["anthropic-version"] == "2023-06-01"


def test_anthropic_non_stream_accepts_claude_sonnet_5_catalog_pricing(client, monkeypatch):
    monkeypatch.setattr(
        safebox_provider_proxy, "_anthropic_actual_microusd_from_response", lambda p, r: 400
    )
    monkeypatch.setattr(safebox_provider_proxy, "_anthropic_key", lambda: _REAL_KEY)
    _patch_httpx(monkeypatch)
    _FakeClient.response = _FakeResponse(200, {"id": "msg_sonnet_5", "usage": {"output_tokens": 20}})
    resp = client.post(
        "/v1/messages",
        headers=_cap_headers(_session_cap()),
        json={"model": "claude-sonnet-5", "messages": [{"role": "user", "content": "hi"}]},
    )
    assert resp.status_code == 200
    assert resp.json()["id"] == "msg_sonnet_5"
    assert _reserves()
    assert _settles() == [("settle", "r0", 400)]
    assert _releases() == []
    _assert_no_key(resp)


def test_deepseek_model_uses_deepseek_endpoint_and_key(client, monkeypatch):
    monkeypatch.setattr(safebox_provider_proxy, "_anthropic_estimate_microusd", lambda p: 5000)
    monkeypatch.setattr(
        safebox_provider_proxy, "_anthropic_actual_microusd_from_response", lambda p, r: 900
    )
    monkeypatch.setattr(safebox_provider_proxy, "_deepseek_key", lambda: _REAL_KEY)
    monkeypatch.setattr(
        safebox_provider_proxy,
        "_anthropic_key",
        lambda: pytest.fail("anthropic key must not be used for deepseek models"),
    )
    _patch_httpx(monkeypatch)
    _FakeClient.response = _FakeResponse(200, {"id": "msg_deepseek", "usage": {"output_tokens": 7}})
    resp = client.post(
        "/v1/messages",
        headers=_cap_headers(_session_cap()),
        json={"model": "deepseek-v4-pro", "messages": [{"role": "user", "content": "hi"}]},
    )
    assert resp.status_code == 200
    sent = _FakeClient.sent[-1]
    assert sent["url"] == safebox_provider_proxy._DEEPSEEK_MESSAGES_URL
    assert sent["headers"]["x-api-key"] == _REAL_KEY


def test_model_rewrite_env_cannot_substitute_the_requested_model(client, monkeypatch):
    monkeypatch.setenv("TAKYON_ANTHROPIC_MODEL_REWRITE", "deepseek-v4-pro")
    monkeypatch.setattr(safebox_provider_proxy, "_anthropic_estimate_microusd", lambda p: 5000)
    monkeypatch.setattr(
        safebox_provider_proxy,
        "_anthropic_actual_microusd_from_response",
        lambda p, r: 900,
    )
    monkeypatch.setattr(safebox_provider_proxy, "_anthropic_key", lambda: _REAL_KEY)
    monkeypatch.setattr(
        safebox_provider_proxy,
        "_deepseek_key",
        lambda: pytest.fail("a Claude request must never be rewritten to DeepSeek"),
    )
    _patch_httpx(monkeypatch)
    _FakeClient.response = _FakeResponse(200, {"id": "msg_exact", "usage": {"output_tokens": 7}})
    resp = client.post(
        "/v1/messages",
        headers=_cap_headers(_session_cap()),
        json={"model": "claude-sonnet-5", "messages": [{"role": "user", "content": "hi"}]},
    )
    assert resp.status_code == 200
    sent = _FakeClient.sent[-1]
    assert sent["url"] == safebox_provider_proxy._ANTHROPIC_MESSAGES_URL
    assert sent["json"]["model"] == "claude-sonnet-5"


def test_anthropic_upstream_error_releases_hold_and_is_sanitized(client, monkeypatch):
    monkeypatch.setattr(safebox_provider_proxy, "_anthropic_estimate_microusd", lambda p: 5000)
    monkeypatch.setattr(safebox_provider_proxy, "_anthropic_key", lambda: _REAL_KEY)
    _patch_httpx(monkeypatch)
    _FakeClient.response = _FakeResponse(400, text='{"error":"bad request"}')
    resp = client.post(
        "/v1/messages",
        headers=_cap_headers(_session_cap()),
        json={"model": "claude-sonnet-4-6", "messages": [{"role": "user", "content": "hi"}]},
    )
    assert resp.status_code == 400
    _assert_no_key(resp)
    # Upstream rejected -> no realized spend -> the hold is RELEASED, never settled.
    assert len(_reserves()) == 1 and len(_releases()) == 1 and _settles() == []


def test_anthropic_stream_is_event_stream_and_settles_actual_from_usage(client, monkeypatch):
    monkeypatch.setattr(safebox_provider_proxy, "_anthropic_estimate_microusd", lambda p: 6000)
    monkeypatch.setattr(safebox_provider_proxy, "_anthropic_key", lambda: _REAL_KEY)
    # Drive the realized usage parse to a known billed amount.
    from plugins.takyon import ai_provider

    monkeypatch.setattr(ai_provider, "billed_microusd_cost", lambda *a, **k: (1234, 2222))
    monkeypatch.setattr(ai_provider, "anthropic_payload", lambda b: ({}, "claude-sonnet-4-6", 10))
    _patch_httpx(monkeypatch)
    _FakeClient.stream_response = _FakeStream(
        200,
        [
            b'event: message_start\ndata: {"type":"message_start","message":{"usage":{"input_tokens":10}}}\n\n',
            b'event: message_delta\ndata: {"type":"message_delta","usage":{"output_tokens":42}}\n\n',
            b'event: message_stop\ndata: {"type":"message_stop"}\n\n',
        ],
    )
    resp = client.post(
        "/v1/messages",
        headers=_cap_headers(_session_cap()),
        json={
            "model": "claude-sonnet-4-6",
            "messages": [{"role": "user", "content": "hi"}],
            "stream": True,
        },
    )
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")
    assert "message_start" in resp.text and "message_stop" in resp.text
    _assert_no_key(resp)
    # Reserved the estimate up front, settled the ACTUAL billed cost parsed from the usage events.
    assert _reserves() == [("reserve", _OPERATOR, 6000, "r0")]
    assert _settles() == [("settle", "r0", 2222)]
    assert _releases() == []
    sent = _FakeClient.sent[-1]
    assert sent.get("stream") is True
    assert sent["headers"]["x-api-key"] == _REAL_KEY


def test_anthropic_stream_upstream_error_releases_hold(client, monkeypatch):
    monkeypatch.setattr(safebox_provider_proxy, "_anthropic_estimate_microusd", lambda p: 6000)
    monkeypatch.setattr(safebox_provider_proxy, "_anthropic_key", lambda: _REAL_KEY)
    from plugins.takyon import ai_provider

    monkeypatch.setattr(ai_provider, "anthropic_payload", lambda b: ({}, "claude-sonnet-4-6", 10))
    _patch_httpx(monkeypatch)
    _FakeClient.stream_response = _FakeStream(429, [b'{"error":"rate_limited"}'])
    resp = client.post(
        "/v1/messages",
        headers=_cap_headers(_session_cap()),
        json={
            "model": "claude-sonnet-4-6",
            "messages": [{"role": "user", "content": "hi"}],
            "stream": True,
        },
    )
    assert resp.status_code == 429
    assert resp.json()["detail"] == {"error": "provider_error", "upstream_status": 429}
    _assert_no_key(resp)
    # Upstream rejected -> the hold is RELEASED, never settled.
    assert len(_reserves()) == 1 and len(_releases()) == 1 and _settles() == []


def test_anthropic_worker_fail_fast_header_disables_sdk_retry(client, monkeypatch):
    monkeypatch.setattr(safebox_provider_proxy, "_anthropic_estimate_microusd", lambda p: 6000)
    monkeypatch.setattr(safebox_provider_proxy, "_deepseek_key", lambda: _REAL_KEY)
    from plugins.takyon import ai_provider

    monkeypatch.setattr(ai_provider, "anthropic_payload", lambda b: ({}, "deepseek-v4-pro", 10))
    _patch_httpx(monkeypatch)
    _FakeClient.stream_response = _FakeStream(529, [b'{"error":"overloaded"}'])
    headers = {
        **_cap_headers(_session_cap()),
        "x-takyon-fail-on-api-retry": "1",
    }
    resp = client.post(
        "/v1/messages",
        headers=headers,
        json={
            "model": "deepseek-v4-pro",
            "messages": [{"role": "user", "content": "hi"}],
            "stream": True,
        },
    )
    assert resp.status_code == 529
    assert resp.headers["x-should-retry"] == "false"
    assert len(_reserves()) == 1 and len(_releases()) == 1 and _settles() == []


def test_anthropic_stream_open_transport_failure_is_502_and_releases(client, monkeypatch, caplog):
    monkeypatch.setattr(safebox_provider_proxy, "_anthropic_estimate_microusd", lambda p: 6000)
    monkeypatch.setattr(safebox_provider_proxy, "_anthropic_key", lambda: _REAL_KEY)
    from plugins.takyon import ai_provider

    monkeypatch.setattr(ai_provider, "anthropic_payload", lambda b: ({}, "claude-sonnet-4-6", 10))
    _patch_httpx(monkeypatch)
    request = safebox_provider_proxy.httpx.Request(
        "POST", safebox_provider_proxy._ANTHROPIC_MESSAGES_URL
    )
    _FakeClient.stream_response = _FailingStream(
        safebox_provider_proxy.httpx.ConnectError(f"connect failed {_REAL_KEY}", request=request),
        open_failure=True,
    )
    with caplog.at_level("WARNING", logger="takyon.safebox_provider_proxy"):
        resp = client.post(
            "/v1/messages",
            headers=_cap_headers(_session_cap()),
            json={
                "model": "claude-sonnet-4-6",
                "messages": [{"role": "user", "content": "hi"}],
                "stream": True,
            },
        )
    assert resp.status_code == 502
    assert resp.json()["detail"] == "provider_unreachable"
    assert len(_reserves()) == 1 and len(_releases()) == 1 and _settles() == []
    assert "ConnectError" in caplog.text and "phase=open" in caplog.text
    assert _REAL_KEY not in caplog.text and _REAL_KEY not in resp.text


def test_anthropic_midstream_failure_releases_and_logs_sanitized(client, monkeypatch, caplog):
    monkeypatch.setattr(safebox_provider_proxy, "_anthropic_estimate_microusd", lambda p: 6000)
    monkeypatch.setattr(safebox_provider_proxy, "_anthropic_key", lambda: _REAL_KEY)
    from plugins.takyon import ai_provider

    monkeypatch.setattr(ai_provider, "anthropic_payload", lambda b: ({}, "claude-sonnet-4-6", 10))
    _patch_httpx(monkeypatch)
    request = safebox_provider_proxy.httpx.Request(
        "POST", safebox_provider_proxy._ANTHROPIC_MESSAGES_URL
    )
    _FakeClient.stream_response = _FailingStream(
        safebox_provider_proxy.httpx.ReadError(
            f"socket reset {_REAL_KEY} body={{\"private\":\"customer-data\"}}",
            request=request,
        ),
        chunks=(b'event: message_start\ndata: {"type":"message_start"}\n\n',),
    )
    local_client = TestClient(safebox_app.build_safebox_app(), raise_server_exceptions=False)
    with caplog.at_level("WARNING", logger="takyon.safebox_provider_proxy"):
        resp = local_client.post(
            "/v1/messages",
            headers=_cap_headers(_session_cap()),
            json={
                "model": "claude-sonnet-4-6",
                "messages": [{"role": "user", "content": "hi"}],
                "stream": True,
            },
        )
    assert resp.status_code == 200  # headers were validly committed after upstream opened
    assert "event: error" in resp.text
    assert '"type":"api_error"' in resp.text
    assert "Upstream provider stream interrupted" in resp.text
    assert len(_reserves()) == 1 and _releases() == []
    assert _settles() == [("settle", "r0", 6000)]
    assert "ReadError" in caplog.text and "phase=body" in caplog.text
    assert _REAL_KEY not in caplog.text
    assert "customer-data" not in caplog.text


class _OwnedTestContext:
    def __init__(self):
        self.exits = 0

    def __exit__(self, *args):
        self.exits += 1


class _OwnedTestClient:
    def __init__(self):
        self.closes = 0

    def close(self):
        self.closes += 1


def _owned_response(chunks):
    context = _OwnedTestContext()
    http_client = _OwnedTestClient()
    owner = safebox_provider_proxy._OpenedProviderStream(
        client=http_client,
        stream_context=context,
        ledger=_FakeBudget(),
        reservation={"id": "r0"},
        usage=pytest.fail,
        estimate_microusd=6000,
        provider="anthropic",
        secret=_REAL_KEY,
    )
    return (
        safebox_provider_proxy._OwnedStreamingResponse(
            chunks, media_type="text/event-stream", owner=owner
        ),
        context,
        http_client,
    )


def test_opened_stream_client_disconnect_settles_estimate_once():
    def chunks():
        yield b"first"
        yield b"second"

    response, context, http_client = _owned_response(chunks())

    async def send(message):
        if message["type"] == "http.response.body":
            raise OSError("client disconnected")

    async def receive():
        return {"type": "http.request"}

    with pytest.raises(ClientDisconnect):
        asyncio.run(
            response(
                {"type": "http", "asgi": {"spec_version": "2.4"}}, receive, send
            )
        )
    assert _settles() == [("settle", "r0", 6000)] and _releases() == []
    assert context.exits == 1 and http_client.closes == 1


def test_opened_stream_close_before_first_next_still_closes_and_settles():
    iterated = []

    def chunks():
        iterated.append(True)
        yield b"never"

    response, context, http_client = _owned_response(chunks())

    async def send(_message):
        raise OSError("closed before response body")

    async def receive():
        return {"type": "http.request"}

    with pytest.raises(ClientDisconnect):
        asyncio.run(
            response(
                {"type": "http", "asgi": {"spec_version": "2.4"}}, receive, send
            )
        )
    assert iterated == []
    assert _settles() == [("settle", "r0", 6000)] and _releases() == []
    assert context.exits == 1 and http_client.closes == 1


def test_session_capability_is_reusable_across_more_than_one_call(client, monkeypatch):
    # A SESSION capability is NOT single-use: the SAME token drives several calls without a replay
    # rejection. Each call independently reserves + settles on the operator rail.
    monkeypatch.setattr(safebox_provider_proxy, "_anthropic_estimate_microusd", lambda p: 1000)
    monkeypatch.setattr(
        safebox_provider_proxy, "_anthropic_actual_microusd_from_response", lambda p, r: 900
    )
    monkeypatch.setattr(safebox_provider_proxy, "_anthropic_key", lambda: _REAL_KEY)
    _patch_httpx(monkeypatch)
    _FakeClient.response = _FakeResponse(200, {"id": "msg"})
    cap = _session_cap()
    for _ in range(3):
        resp = client.post(
            "/v1/messages",
            headers=_cap_headers(cap),  # SAME token every time
            json={"model": "claude-sonnet-4-6", "messages": [{"role": "user", "content": "hi"}]},
        )
        assert resp.status_code == 200
    assert len(_reserves()) == 3 and len(_settles()) == 3 and _releases() == []


def test_per_action_anthropic_capability_is_accepted_and_metered(client, monkeypatch):
    # The coding worker mints a per-action `anthropic.messages` capability (not operator.session). The
    # proxy still accepts it (worker key-free contract) AND meters it on the operator rail.
    monkeypatch.setattr(safebox_provider_proxy, "_anthropic_estimate_microusd", lambda p: 2000)
    monkeypatch.setattr(
        safebox_provider_proxy, "_anthropic_actual_microusd_from_response", lambda p, r: 1500
    )
    monkeypatch.setattr(safebox_provider_proxy, "_anthropic_key", lambda: _REAL_KEY)
    _patch_httpx(monkeypatch)
    _FakeClient.response = _FakeResponse(200, {"id": "msg_worker"})
    scope = CapabilityScope(
        takyon_user_id=_OPERATOR,
        business_slug="acme",
        app_user_id=None,
        action=safebox_app._ANTHROPIC_AUDIENCE,
        max_cost_microusd=2_000_000,
    )
    cap = mint_capability(
        scope,
        signing_key=_SIGNING_KEY,
        audience=safebox_app._ANTHROPIC_AUDIENCE,
        nonce="worker-n1",
        issued_at=int(time.time()),
        ttl_seconds=300,
    )
    resp = client.post("/v1/messages", headers=_cap_headers(cap), json={
        "model": "claude-sonnet-4-6", "messages": [{"role": "user", "content": "hi"}]
    })
    assert resp.status_code == 200
    assert resp.json()["id"] == "msg_worker"
    _assert_no_key(resp)
    assert _reserves() == [("reserve", _OPERATOR, 2000, "r0")]
    assert _settles() == [("settle", "r0", 1500)]


def test_bare_internal_token_is_refused_no_spend(client, monkeypatch):
    # Authority principle / G2: the shared internal token is transport REACHABILITY, not spend authority
    # (every plane holds it). A /v1/messages call with the bare token and NO capability is refused 401
    # BEFORE any reserve and without ever reaching the upstream provider — no ungated, no token-spend path.
    monkeypatch.setattr(safebox_provider_proxy, "_anthropic_key", lambda: _REAL_KEY)
    monkeypatch.setattr(
        safebox_provider_proxy.httpx,
        "Client",
        lambda *a, **k: pytest.fail("upstream must not be reached for a bare-token call"),
    )
    resp = client.post(
        "/v1/messages",
        headers=_auth(),  # bare internal token, NOT a capability
        json={"model": "claude-sonnet-4-6", "messages": [{"role": "user", "content": "hi"}]},
    )
    assert resp.status_code == 401
    assert _reserves() == []  # refused before any money reserve


def test_bare_internal_token_refused_even_with_platform_operator(monkeypatch):
    # Even with a platform-operator id configured, the bare internal token buys NOTHING (G2): the old
    # transitional platform-operator scope is gone, so a bare-token /v1/messages is 401, never reserved.
    monkeypatch.setenv(safebox_app._SAFEBOX_TOKEN_ENV, _TOKEN)
    monkeypatch.setenv(safebox_app._CAP_SIGNING_KEY_ENV, _SIGNING_KEY.decode())
    monkeypatch.setenv("TAKYON_PLATFORM_OPERATOR_USER_ID", "op-platform")
    monkeypatch.setattr(safebox_provider_proxy, "_anthropic_key", lambda: _REAL_KEY)
    monkeypatch.setattr(
        safebox_provider_proxy.httpx,
        "Client",
        lambda *a, **k: pytest.fail("upstream must not be reached for a bare-token call"),
    )
    local_client = TestClient(safebox_app.build_safebox_app())
    resp = local_client.post(
        "/v1/messages",
        headers=_auth(),
        json={"model": "claude-sonnet-4-6", "messages": [{"role": "user", "content": "hi"}]},
    )
    assert resp.status_code == 401


# ══ Tavily (money-gated) ══════════════════════════════════════════════════════════════════════════
def test_tavily_wrong_token_is_401(client, monkeypatch):
    monkeypatch.setattr(safebox_provider_proxy, "_tavily_key", lambda: _REAL_KEY)
    resp = client.post(
        "/v1/proxy/tavily/search", headers={"Authorization": "Bearer wrong"}, json={"query": "x"}
    )
    assert resp.status_code == 401
    assert _reserves() == []


def test_tavily_out_of_budget_is_402_before_key(client, monkeypatch):
    _FakeBudget.refuse = True
    monkeypatch.setattr(safebox_provider_proxy, "_tavily_price_microusd", lambda op, p: 2500)
    key_calls = []
    monkeypatch.setattr(
        safebox_provider_proxy, "_tavily_key", lambda: key_calls.append(1) or _REAL_KEY
    )
    from plugins.takyon import ai_provider

    monkeypatch.setattr(
        ai_provider, "call_tavily", lambda *a, **k: pytest.fail("upstream must not be called")
    )
    resp = client.post(
        "/v1/proxy/tavily/search", headers=_cap_headers(_session_cap()), json={"query": "x"}
    )
    assert resp.status_code == 402
    assert key_calls == []


def test_tavily_unconfigured_is_503_and_releases(client, monkeypatch):
    monkeypatch.setattr(safebox_provider_proxy, "_tavily_price_microusd", lambda op, p: 2500)
    monkeypatch.setattr(safebox_provider_proxy, "_tavily_key", lambda: "")
    from plugins.takyon import ai_provider

    monkeypatch.setattr(
        ai_provider, "call_tavily", lambda *a, **k: pytest.fail("upstream must not be called")
    )
    resp = client.post(
        "/v1/proxy/tavily/search", headers=_cap_headers(_session_cap()), json={"query": "x"}
    )
    assert resp.status_code == 503
    assert resp.json()["detail"] == "tavily_unconfigured"
    assert len(_reserves()) == 1 and len(_releases()) == 1 and _settles() == []


def test_tavily_success_reserves_settles_once_and_is_key_free(client, monkeypatch):
    monkeypatch.setattr(safebox_provider_proxy, "_tavily_price_microusd", lambda op, p: 2500)
    monkeypatch.setattr(safebox_provider_proxy, "_tavily_key", lambda: _REAL_KEY)
    captured = {}
    from plugins.takyon import ai_provider

    def _fake_call(operation, body, key):
        captured["operation"] = operation
        captured["key"] = key
        return {"results": [{"title": "t"}], "operation": operation}

    monkeypatch.setattr(ai_provider, "call_tavily", _fake_call)
    resp = client.post(
        "/v1/proxy/tavily/search", headers=_cap_headers(_session_cap()), json={"query": "x"}
    )
    assert resp.status_code == 200
    assert resp.json()["operation"] == "search"
    assert captured["key"] == _REAL_KEY  # resolved locally + passed to the leaf
    _assert_no_key(resp)
    # Per-request provider: actual == reserved price; reserve + settle exactly once.
    assert _reserves() == [("reserve", _OPERATOR, 2500, "r0")]
    assert _settles() == [("settle", "r0", 2500)]
    assert _releases() == []


def test_tavily_upstream_error_releases_hold(client, monkeypatch):
    monkeypatch.setattr(safebox_provider_proxy, "_tavily_price_microusd", lambda op, p: 2500)
    monkeypatch.setattr(safebox_provider_proxy, "_tavily_key", lambda: _REAL_KEY)
    from plugins.takyon import ai_provider

    def _boom(*a, **k):
        raise RuntimeError("Tavily API returned 500: oops")

    monkeypatch.setattr(ai_provider, "call_tavily", _boom)
    resp = client.post(
        "/v1/proxy/tavily/search", headers=_cap_headers(_session_cap()), json={"query": "x"}
    )
    assert resp.status_code == 502
    _assert_no_key(resp)
    assert len(_reserves()) == 1 and len(_releases()) == 1 and _settles() == []


def test_tavily_unsupported_operation_is_400(client, monkeypatch):
    monkeypatch.setattr(safebox_provider_proxy, "_tavily_key", lambda: _REAL_KEY)
    resp = client.post(
        "/v1/proxy/tavily/crawl", headers=_cap_headers(_session_cap()), json={"url": "x"}
    )
    assert resp.status_code == 400
    assert resp.json()["detail"] == "unsupported_tavily_operation"
    assert _reserves() == []  # refused before any reserve


# ══ Session-token mint route ══════════════════════════════════════════════════════════════════════
def test_operator_session_token_mint_roundtrips(client, monkeypatch):
    # The mint validates operator ownership (boundary 1) and returns a reusable, ceiling-bound capability.
    import contextlib

    class _OwnerConn:
        def execute(self, sql, params=None):
            class _C:
                def fetchone(self_inner):
                    return {"owner_user_id": _OPERATOR}

            return _C()

    @contextlib.contextmanager
    def _fake_conn():
        yield _OwnerConn()

    monkeypatch.setattr(safebox_app, "_safebox_db_conn", _fake_conn)
    resp = client.post(
        "/v1/operator/session-token",
        headers=_auth(),
        json={"business": "acme", "operator_user_id": _OPERATOR, "max_cost_microusd": 2_000_000},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["audience"] == safebox_app._OPERATOR_SESSION_AUDIENCE
    from plugins.takyon.safebox_capability import verify_capability

    scope, _nonce, exp = verify_capability(
        data["token"],
        signing_key=_SIGNING_KEY,
        expected_audience=safebox_app._OPERATOR_SESSION_AUDIENCE,
        now=0,
    )
    assert scope.takyon_user_id == _OPERATOR
    assert scope.business_slug == "acme"
    assert scope.app_user_id is None  # operator/platform call has no product sub-user
    assert scope.max_cost_microusd == 2_000_000
    assert exp > 0


def test_operator_session_token_requires_internal_token(client):
    resp = client.post(
        "/v1/operator/session-token",
        json={"business": "acme", "operator_user_id": _OPERATOR, "max_cost_microusd": 1000},
    )
    assert resp.status_code == 401


# ══ Route wiring smoke ════════════════════════════════════════════════════════════════════════════
def test_proxy_routes_are_registered():
    app = safebox_app.build_safebox_app()
    paths = {getattr(route, "path", None) for route in app.routes}
    assert "/v1/proxy/anthropic/messages" in paths
    assert "/v1/messages" in paths
    assert "/v1/proxy/tavily/{operation}" in paths
    assert "/v1/operator/session-token" in paths
    # The metered business broker route is still mounted.
    assert "/v1/providers/anthropic/messages" in paths


def test_ungated_creative_proxy_routes_are_deleted():
    app = safebox_app.build_safebox_app()
    paths = {getattr(route, "path", None) for route in app.routes}
    assert "/v1/proxy/gemini/image" not in paths
    assert "/v1/proxy/openai/images" not in paths
    assert "/v1/proxy/fal/{path:path}" not in paths
    # The gated replacements ARE mounted.
    assert "/v1/providers/gemini/logo" in paths
    assert "/v1/providers/openai/images" in paths
    assert "/v1/providers/fal/kling-image-to-video" in paths
    assert "/v1/providers/fal/{fal_path:path}" not in paths


def test_deleted_ungated_creative_proxy_routes_are_unreachable(client):
    for route in ("/v1/proxy/gemini/image", "/v1/proxy/openai/images", "/v1/proxy/fal/fal-ai/x"):
        resp = client.post(route, headers=_auth(), json={"prompt": "x"})
        assert resp.status_code == 404, (route, resp.status_code)
