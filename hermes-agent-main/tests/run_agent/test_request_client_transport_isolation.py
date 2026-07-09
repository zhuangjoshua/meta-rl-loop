"""Regression guardrail: per-request OpenAI clients must never wrap (and then
close) an ``http_client`` transport shared with the primary client.

Bug reproduced on the prod operator rail 2026-07-08 (business qaloop0708a,
gpt-5.5 CEO via the Takyon operator gateway, api_mode=codex_responses):

``_replace_openai_gateway_client`` stores the gateway transport in
``agent._client_kwargs["http_client"]``. ``_create_request_openai_client``
copied those kwargs, so every per-request client wrapped the SAME shared
httpx transport — and ``interruptible_api_call``'s ``request_complete``
close closed it after the FIRST request (the OpenAI SDK ``close()`` closes
whatever ``http_client`` it wraps, owned or not). From then on every call:
"Detected closed shared OpenAI client; recreating before use" → rebuild from
kwargs still carrying the CLOSED transport → APIConnectionError
('Connection error.') forever. Same class as #10933.

The fix publishes ``agent._request_http_client_factory`` from the gateway
swap; ``_create_request_openai_client`` mints a fresh transport per request
client (or pops a shared one), and ``_replace_primary_openai_client`` mints
a fresh transport on rebuild.
"""
from unittest.mock import patch

from run_agent import AIAgent


def _make_agent():
    return AIAgent(
        api_key="test-key",
        base_url="https://api.example.com/v1",
        model="test/model",
        quiet_mode=True,
        skip_context_files=True,
        skip_memory=True,
    )


class _FakeTransport:
    def __init__(self):
        self.is_closed = False

    def close(self):
        self.is_closed = True


def _make_fake_openai_factory(constructed):
    class _FakeOpenAI:
        def __init__(self, **kwargs):
            self._kwargs = kwargs
            self._http_client = kwargs.get("http_client")
            # Mirror the real SDK: expose the wrapped transport as ._client
            # so _is_openai_client_closed sees its closed-ness.
            self._client = kwargs.get("http_client")
            self._closed = False
            constructed.append(self)

        def close(self):
            self._closed = True
            hc = self._http_client
            if hc is not None and hasattr(hc, "close"):
                try:
                    hc.close()
                except Exception:
                    pass

    return _FakeOpenAI


def test_request_client_never_reuses_shared_http_client_without_factory():
    """No factory published: a shared ``http_client`` in ``_client_kwargs``
    must be POPPED for per-request clients, never passed through."""
    agent = _make_agent()
    constructed: list = []
    fake_openai = _make_fake_openai_factory(constructed)
    shared_transport = _FakeTransport()

    agent._client_kwargs = {
        "api_key": "k",
        "base_url": "https://api.example.com/v1",
        "http_client": shared_transport,
    }

    with patch("run_agent.OpenAI", fake_openai):
        agent.client = agent._create_openai_client(
            agent._client_kwargs, reason="seed", shared=True
        )
        request_client = agent._create_request_openai_client(reason="req")

    assert request_client._http_client is not shared_transport, (
        "per-request client wraps the primary's shared http_client; closing "
        "it on request_complete would close the primary's transport and every "
        "later call fails APIConnectionError (#10933 class / qaloop0708a)"
    )
    # Closing the request client must leave the primary's transport open.
    request_client.close()
    assert not shared_transport.is_closed, (
        "request_complete close killed the shared primary transport"
    )


def test_request_client_uses_factory_minted_transport():
    """Factory published (operator gateway): each request client gets its own
    factory-minted transport; closing it leaves the primary's transport open."""
    agent = _make_agent()
    constructed: list = []
    fake_openai = _make_fake_openai_factory(constructed)
    minted: list = []

    def factory():
        t = _FakeTransport()
        minted.append(t)
        return t

    primary_transport = factory()
    agent._client_kwargs = {
        "api_key": "takyon-operator-gateway",
        "base_url": "https://operator-gateway.local/v1",
        "http_client": primary_transport,
    }
    agent._request_http_client_factory = factory

    with patch("run_agent.OpenAI", fake_openai):
        agent.client = agent._create_openai_client(
            agent._client_kwargs, reason="operator_gateway", shared=True
        )
        req_a = agent._create_request_openai_client(reason="codex_stream_request")
        req_a.close()  # request_complete
        req_b = agent._create_request_openai_client(reason="codex_stream_request")

    assert req_a._http_client is not primary_transport
    assert req_b._http_client is not primary_transport
    assert req_a._http_client is not req_b._http_client, (
        "request clients share a transport; the second wraps a closed one"
    )
    assert not primary_transport.is_closed, (
        "request_complete close killed the shared gateway transport — the "
        "qaloop0708a failure loop"
    )
    assert req_b._http_client is not None and not req_b._http_client.is_closed


def test_replace_primary_mints_fresh_transport_via_factory():
    """Primary rebuild with a factory must wrap a FRESH transport (not the old
    closed one) and store it back into ``_client_kwargs``."""
    agent = _make_agent()
    constructed: list = []
    fake_openai = _make_fake_openai_factory(constructed)
    minted: list = []

    def factory():
        t = _FakeTransport()
        minted.append(t)
        return t

    old_transport = factory()
    old_transport.close()  # simulate: something closed the gateway transport
    agent._client_kwargs = {
        "api_key": "takyon-operator-gateway",
        "base_url": "https://operator-gateway.local/v1",
        "http_client": old_transport,
    }
    agent._request_http_client_factory = factory

    with patch("run_agent.OpenAI", fake_openai):
        agent.client = agent._create_openai_client(
            agent._client_kwargs, reason="seed", shared=True
        )
        ok = agent._replace_primary_openai_client(reason="recreate_closed:test")

    assert ok
    rebuilt = agent.client
    assert rebuilt._http_client is not old_transport, (
        "rebuild re-wrapped the closed transport; the rebuilt client is dead "
        "on arrival and the recovery loop never converges"
    )
    assert not rebuilt._http_client.is_closed
    assert agent._client_kwargs.get("http_client") is rebuilt._http_client, (
        "rebuild must store the fresh transport so later ensures/rebuilds see it"
    )


def test_gateway_swap_publishes_request_transport_factory():
    """The operator-gateway client swap must publish the per-request transport
    factory — that is the seam the core fix relies on."""
    from plugins.takyon import operator_gateway as og

    class _Agent:
        provider = "custom"
        base_url = "https://api.openai.com/v1"
        client = None
        _client_kwargs: dict = {}

        def _client_log_context(self):
            return ""

        def _close_openai_client(self, client, *, reason, shared):
            pass

        def _create_openai_client(self, kwargs, *, reason, shared):
            return object()

    agent = _Agent()
    context = og.OperatorGatewayContext(
        provider="custom",
        requested_provider="custom",
        api_mode="codex_responses",
        upstream_base_url="https://api.openai.com/v1",
        model="gpt-5.5",
    )
    with patch.object(og, "build_operator_gateway_http_client") as build:
        build.side_effect = lambda ctx: _FakeTransport()
        og._replace_openai_gateway_client(agent, context)
        factory = getattr(agent, "_request_http_client_factory", None)
        assert callable(factory), (
            "gateway swap no longer publishes _request_http_client_factory; "
            "per-request clients will wrap and close the shared gateway "
            "transport (qaloop0708a failure loop)"
        )
        t1, t2 = factory(), factory()
    assert t1 is not t2, "factory must mint a fresh transport per call"
