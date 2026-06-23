from __future__ import annotations

import httpx
import pytest

from plugins.takyon import operator_gateway as og
from plugins.takyon.operator_gateway import (
    OperatorGatewayContext,
    _anthropic_auth_headers,
    _resolve_anthropic_broker_runtime,
    _resolve_runtime_for_request,
    _response_headers,
    _upstream_url,
)


def test_upstream_url_rewrites_openai_style_paths_without_double_v1():
    target_url, query_params = _upstream_url(
        {"base_url": "https://api.openai.com/v1"},
        "/v1/chat/completions",
    )

    assert target_url == "https://api.openai.com/v1/chat/completions"
    assert query_params == []


def test_upstream_url_preserves_query_params_for_anthropic_style_routes():
    target_url, query_params = _upstream_url(
        {"base_url": "https://example.invalid/anthropic?api-version=2025-05-01"},
        "/v1/messages",
    )

    assert target_url == "https://example.invalid/anthropic/v1/messages"
    assert query_params == [("api-version", "2025-05-01")]


def test_response_headers_strip_content_encoding_for_decoded_body():
    headers = httpx.Headers(
        {
            "content-encoding": "gzip",
            "content-length": "123",
            "content-type": "application/json",
            "x-request-id": "req_123",
        }
    )

    result = _response_headers(headers, streaming=False)

    assert "content-encoding" not in {key.lower() for key in result}
    assert "content-length" not in {key.lower() for key in result}
    assert result["content-type"] == "application/json"
    assert result["x-request-id"] == "req_123"


def test_response_headers_strip_content_encoding_for_streamed_body():
    headers = httpx.Headers(
        {
            "Content-Encoding": "gzip",
            "Transfer-Encoding": "chunked",
            "Content-Type": "text/event-stream",
        }
    )

    result = _response_headers(headers, streaming=True)

    lowered = {key.lower() for key in result}
    assert "content-encoding" not in lowered
    assert "transfer-encoding" not in lowered
    assert result["content-type"] == "text/event-stream"


# ── CEO operator turn: Anthropic routes through the safebox proxy key-free (operator.session) ─────────
#
# Under broker lockdown the CEO loop must call Anthropic THROUGH the safebox proxy with a minted
# operator.session token carrying the REAL business owner — NEVER resolving a raw provider key on the
# runtime plane. These tests pin: (1) base_url = safebox ROOT + api_key = session token, no raw key;
# (2) the session token is minted for the resolved owner; (3) owner-missing / mint-failure fails closed;
# (4) the assembled upstream Anthropic auth header carries the session token (x-api-key) to the safebox.


def _anthropic_context(business="latexflow", operator_user_id="owner-1"):
    return OperatorGatewayContext(
        provider="anthropic",
        requested_provider="anthropic",
        api_mode="anthropic_messages",
        upstream_base_url="https://api.anthropic.com",
        operator_user_id=operator_user_id,
        business_slug=business,
    )


def test_anthropic_broker_runtime_uses_safebox_root_and_session_token(monkeypatch):
    monkeypatch.setattr(og, "_operator_anthropic_broker_lockdown", lambda: True)
    monkeypatch.setattr(og, "_resolve_operator_owner_user_id", lambda context: "owner-1")
    from plugins.takyon import core as takyon_core
    from plugins.takyon import safebox

    monkeypatch.setattr(takyon_core, "_claude_agent_broker_url", lambda: "http://10.116.0.2:8000")

    minted: dict[str, object] = {}

    def fake_mint(business, operator_user_id, **kwargs):
        minted["business"] = business
        minted["operator_user_id"] = operator_user_id
        return "operator-session-token-xyz"

    monkeypatch.setattr(safebox, "mint_operator_session_token", fake_mint)

    runtime = _resolve_runtime_for_request(_anthropic_context(), {"model": "claude-opus-4-8"})

    # Base URL is the safebox ROOT (the SDK appends /v1/messages, which the proxy serves).
    assert runtime["base_url"] == "http://10.116.0.2:8000"
    assert runtime["base_url"].endswith("/v1/messages") is False
    # The credential is the minted operator.session token — NEVER a raw provider key.
    assert runtime["api_key"] == "operator-session-token-xyz"
    assert "sk-ant" not in str(runtime["api_key"])
    # Minted for the REAL resolved owner + business.
    assert minted == {"business": "latexflow", "operator_user_id": "owner-1"}


def test_anthropic_broker_runtime_fails_closed_when_owner_missing(monkeypatch):
    monkeypatch.setattr(og, "_operator_anthropic_broker_lockdown", lambda: True)
    monkeypatch.setattr(og, "_resolve_operator_owner_user_id", lambda context: "")
    from plugins.takyon import core as takyon_core

    monkeypatch.setattr(takyon_core, "_claude_agent_broker_url", lambda: "http://10.116.0.2:8000")

    with pytest.raises(RuntimeError):
        _resolve_anthropic_broker_runtime(_anthropic_context(operator_user_id=""), {})


def test_anthropic_broker_runtime_fails_closed_when_mint_refused(monkeypatch):
    monkeypatch.setattr(og, "_operator_anthropic_broker_lockdown", lambda: True)
    monkeypatch.setattr(og, "_resolve_operator_owner_user_id", lambda context: "owner-1")
    from plugins.takyon import core as takyon_core
    from plugins.takyon import safebox

    monkeypatch.setattr(takyon_core, "_claude_agent_broker_url", lambda: "http://10.116.0.2:8000")
    # The safebox refuses (e.g. operator does not own the business) → empty token.
    monkeypatch.setattr(safebox, "mint_operator_session_token", lambda *a, **k: "")

    with pytest.raises(RuntimeError):
        _resolve_anthropic_broker_runtime(_anthropic_context(), {})


def test_anthropic_broker_runtime_fails_closed_without_proxy_url(monkeypatch):
    monkeypatch.setattr(og, "_operator_anthropic_broker_lockdown", lambda: True)
    from plugins.takyon import core as takyon_core

    monkeypatch.setattr(takyon_core, "_claude_agent_broker_url", lambda: "")

    with pytest.raises(RuntimeError):
        _resolve_anthropic_broker_runtime(_anthropic_context(), {})


def test_anthropic_auth_header_sends_session_token_as_x_api_key_to_safebox():
    # The safebox root is a third-party Anthropic endpoint (no anthropic.com host), so the adapter sends
    # the session token via x-api-key — exactly what the proxy's operator authorizer accepts.
    headers = _anthropic_auth_headers(
        {"base_url": "http://10.116.0.2:8000", "api_key": "operator-session-token-xyz"}
    )
    assert headers.get("x-api-key") == "operator-session-token-xyz"
    assert "Authorization" not in headers


def test_non_anthropic_modes_do_not_use_the_broker_runtime(monkeypatch):
    # Broker lockdown is anthropic-only: a chat_completions CEO turn still resolves a local runtime
    # (those providers have no safebox proxy route), so the anthropic broker branch must not fire.
    monkeypatch.setattr(og, "_operator_anthropic_broker_lockdown", lambda: True)

    def boom(*a, **k):
        raise AssertionError("anthropic broker runtime must not be used for non-anthropic modes")

    monkeypatch.setattr(og, "_resolve_anthropic_broker_runtime", boom)

    captured: dict[str, object] = {}

    def fake_resolve(**kwargs):
        captured.update(kwargs)
        return {"api_key": "local-key", "base_url": "https://api.openai.com/v1", "api_mode": "chat_completions"}

    import takyon_cli.runtime_provider as rp

    monkeypatch.setattr(rp, "resolve_runtime_provider", fake_resolve)

    context = OperatorGatewayContext(
        provider="openai",
        requested_provider="openai",
        api_mode="chat_completions",
        upstream_base_url="https://api.openai.com/v1",
        operator_user_id="owner-1",
        business_slug="latexflow",
    )
    runtime = _resolve_runtime_for_request(context, {"model": "gpt-4o"})
    assert runtime["api_key"] == "local-key"


def test_runtime_plane_resolves_anthropic_keyfree_without_probing_v1_env(monkeypatch):
    """GOAL_RULES §1 step 4a: on a RUNTIME plane (operator/sub-user, remote safebox configured),
    resolve_runtime_provider must build a CONSTRUCTIBLE anthropic runtime with NO raw provider key —
    and must NOT issue the boot-time GET /v1/env/ANTHROPIC_* probe. The operator gateway discards this
    api_key for its placeholder and re-resolves each call key-free, so a key here is pure leak."""
    import takyon_cli.runtime_provider as rp
    from plugins.takyon import core as takyon_core
    from plugins.takyon import safebox

    # Runtime plane: a remote safebox is configured and this host is NOT the safebox itself, so the
    # broker lockdown defaults ON.
    monkeypatch.setattr(safebox, "_local_authority_enabled", lambda: False)
    monkeypatch.setattr(takyon_core, "_claude_agent_broker_lockdown_enabled", lambda: True)

    # Make a config that selects native anthropic at api.anthropic.com.
    monkeypatch.setattr(rp, "_get_model_config", lambda: {"provider": "anthropic"})
    monkeypatch.setattr(rp, "resolve_provider", lambda *a, **k: "anthropic")
    monkeypatch.setattr(rp, "resolve_requested_provider", lambda requested: "anthropic")
    monkeypatch.setattr(rp, "_resolve_named_custom_runtime", lambda **k: None)
    monkeypatch.setattr(rp, "_resolve_explicit_runtime", lambda **k: None)
    monkeypatch.setattr(rp, "load_pool", lambda provider: None)

    # Any raw-key resolution would be a /v1/env probe — fail the test if it is reached.
    def _no_probe(*a, **k):
        raise AssertionError("resolve_runtime_provider must not resolve a raw anthropic key on a runtime plane")

    monkeypatch.setattr(rp, "get_env_value", _no_probe)
    import agent.anthropic_adapter as aad
    monkeypatch.setattr(aad, "resolve_anthropic_token", _no_probe)

    runtime = rp.resolve_runtime_provider(requested="anthropic")

    assert runtime["provider"] == "anthropic"
    assert runtime["api_mode"] == "anthropic_messages"
    assert runtime["api_key"] == ""  # key-free: nothing resolved on the plane
    assert runtime["source"] == "safebox-broker-keyfree"


def test_safebox_host_still_resolves_anthropic_key_locally(monkeypatch):
    """The key-free guard must NOT fire on the safebox host (role=safebox): the safebox resolves its
    own provider keys locally for the proxy/broker."""
    import takyon_cli.runtime_provider as rp
    from plugins.takyon import safebox

    # On the safebox host the local authority is enabled — the keyfree-plane guard returns False.
    monkeypatch.setattr(safebox, "_local_authority_enabled", lambda: True)
    assert rp._anthropic_broker_keyfree_plane() is False
