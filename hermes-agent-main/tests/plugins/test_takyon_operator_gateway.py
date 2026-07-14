from __future__ import annotations

import uuid

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
        model="claude-opus-4-8",
        operator_user_id=operator_user_id,
        business_slug=business,
    )


def test_primary_facade_manual_compaction_uses_exact_resumed_sdk_session(
    monkeypatch, tmp_path
):
    from plugins.takyon import claude_sdk_runtime, claude_sdk_sessions

    owner = str(uuid.uuid4())
    session_id = str(uuid.uuid4())
    captured = {}

    class Store:
        project_key = "project"

        def __init__(self, **kwargs):
            captured["store_scope"] = kwargs

        def load(self, key):
            captured["load_key"] = key
            return [{"type": "user", "uuid": "existing"}]

    def run(**kwargs):
        captured["run"] = kwargs
        return {
            "operation": "compact",
            "compact_receipt": {
                "trigger": "manual",
                "pre_tokens": 1200,
                "post_tokens": 300,
            },
            "usage": {"input_tokens": 40, "output_tokens": 10},
            "total_cost_usd": 0.02,
        }

    monkeypatch.setattr(
        claude_sdk_sessions, "PostgresClaudeSdkSessionStore", Store
    )
    monkeypatch.setattr(claude_sdk_runtime, "run_primary_sdk_subprocess", run)
    monkeypatch.setattr(og, "compose_primary_agent_system_prompt", lambda *_args: "policy")
    monkeypatch.setenv("TAKYON_PRIMARY_AGENT_MAX_BUDGET_USD", "2")

    agent = og.PrimaryAgentFacade(
        operator_user_id=owner,
        business_slug="acme",
        workspace_root=str(tmp_path),
    )
    result = agent.compact_session(
        session_id=session_id,
        focus_topic="  preserve   launch decisions  ",
    )

    assert result["compact_receipt"]["post_tokens"] == 300
    assert captured["store_scope"] == {
        "operator_user_id": owner,
        "business_slug": "acme",
    }
    assert captured["load_key"]["sessionId"] == session_id
    assert captured["run"]["session_id"] == session_id
    assert captured["run"]["resume_session"] is True
    assert captured["run"]["operation"] == "compact"
    assert captured["run"]["user_prompt"] == (
        "/compact preserve launch decisions"
    )
    assert agent.session_total_tokens == 50
    assert agent.session_estimated_cost_usd == 0.02


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


def test_anthropic_broker_runtime_prefers_host_gateway_url_over_docker_worker_url(monkeypatch):
    monkeypatch.setattr(og, "_operator_anthropic_broker_lockdown", lambda: True)
    monkeypatch.setattr(og, "_resolve_operator_owner_user_id", lambda context: "owner-1")
    monkeypatch.setenv("TAKYON_OPERATOR_GATEWAY_BROKER_URL", "http://127.0.0.1:8765")
    monkeypatch.setenv("TAKYON_CLAUDE_AGENT_BROKER_URL", "http://host.docker.internal:8765")
    from plugins.takyon import safebox

    monkeypatch.setattr(safebox, "mint_operator_session_token", lambda *a, **k: "operator-session-token-xyz")

    runtime = _resolve_runtime_for_request(_anthropic_context(), {"model": "claude-opus-4-8"})

    assert runtime["base_url"] == "http://127.0.0.1:8765"
    assert runtime["api_key"] == "operator-session-token-xyz"


def test_root_scope_anthropic_broker_runtime_mints_without_dashboard_session(monkeypatch):
    monkeypatch.setattr(og, "_operator_anthropic_broker_lockdown", lambda: True)
    monkeypatch.setattr(og, "_resolve_operator_owner_user_id", lambda context: "owner-1")
    from plugins.takyon import safebox

    monkeypatch.setenv("TAKYON_OPERATOR_GATEWAY_BROKER_URL", "http://127.0.0.1:8765")
    minted: dict[str, object] = {}

    def fake_mint(business, operator_user_id, **kwargs):
        minted["business"] = business
        minted["operator_user_id"] = operator_user_id
        return "operator-session-token-xyz"

    monkeypatch.setattr(safebox, "mint_operator_session_token", fake_mint)

    runtime = _resolve_runtime_for_request(_anthropic_context(business=""), {"model": "claude-opus-4-8"})

    assert runtime["base_url"] == "http://127.0.0.1:8765"
    assert runtime["api_key"] == "operator-session-token-xyz"
    assert minted == {
        "business": "",
        "operator_user_id": "owner-1",
    }


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
        model="gpt-4o",
        operator_user_id="owner-1",
        business_slug="latexflow",
    )
    runtime = _resolve_runtime_for_request(context, {"model": "gpt-4o"})
    assert runtime["api_key"] == "local-key"


def test_operator_gateway_refuses_model_switch_before_provider_resolution(monkeypatch):
    context = OperatorGatewayContext(
        provider="openai",
        requested_provider="openai",
        api_mode="codex_responses",
        upstream_base_url="https://api.openai.com/v1",
        model="gpt-5.5",
    )

    with pytest.raises(RuntimeError, match="model switch refused"):
        _resolve_runtime_for_request(context, {"model": "deepseek-v4-pro"})


def test_strict_ceo_role_requires_pinned_openai_responses_runtime(monkeypatch):
    monkeypatch.setenv("TAKYON_STRICT_MODEL_ROLES", "1")
    monkeypatch.setenv("TAKYON_MODEL", "gpt-5.5")

    og._require_strict_ceo_role(
        {
            "api_mode": "codex_responses",
            "base_url": "https://api.openai.com/v1",
        },
        "gpt-5.5",
    )
    with pytest.raises(RuntimeError, match="requires OpenAI Responses"):
        og._require_strict_ceo_role(
            {
                "api_mode": "anthropic_messages",
                "base_url": "https://api.anthropic.com",
            },
            "gpt-5.5",
        )


def test_enable_operator_gateway_deletes_generic_fallback_state(monkeypatch):
    class Compressor:
        pass

    class Agent:
        model = "gpt-5.5"
        _fallback_chain = [{"provider": "anthropic", "model": "claude-sonnet-5"}]
        _fallback_model = _fallback_chain[0]
        _fallback_index = 0
        _credential_pool = object()
        context_compressor = Compressor()

    monkeypatch.setattr(
        og,
        "_operator_gateway_dispatch_for",
        lambda _mode: {"base_url": "https://operator-gateway.local/v1", "replace_fn": lambda agent, context: None},
    )
    agent = Agent()

    og.enable_operator_gateway(
        agent,
        {
            "provider": "custom",
            "requested_provider": "custom",
            "api_mode": "codex_responses",
            "base_url": "https://api.openai.com/v1",
        },
    )

    assert agent._fallback_chain == []
    assert agent._fallback_model is None
    assert agent._credential_pool is None
    assert agent._takyon_strict_model_pin == "gpt-5.5"
    assert agent.context_compressor._takyon_operator_gateway_context.model == "gpt-5.5"


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
