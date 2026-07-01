"""Operator inference gateway for Takyon-owned agent turns.

This keeps the Hermes/CEO tool loop outside the authority boundary while routing
the actual provider HTTP requests through a local proxy transport. The proxy
holds the real provider/base-URL resolution server-side; the outer agent only
sees a placeholder key and a local gateway endpoint.

This is intentionally a minimum-delta transport cutover:
  * the agent loop, tool execution, and local filesystem writes stay where they are
  * the model/provider request hop is redirected through a local gateway transport
  * raw provider credentials are not injected into the outer ``AIAgent`` instance

The gateway currently supports the three wire protocols used by operator runs:
``chat_completions``, ``codex_responses``, and ``anthropic_messages``.
Unsupported modes fail closed with a clear error instead of silently bypassing
the gateway.
"""

from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass
from typing import Any, Iterable
from urllib.parse import parse_qsl, urlparse

import httpx

logger = logging.getLogger(__name__)

_UPSTREAM_CLIENT_LOCK = threading.Lock()
_UPSTREAM_CLIENT: httpx.Client | None = None


def _get_upstream_client() -> httpx.Client:
    """Return a process-wide pooled httpx.Client for upstream forwarding.

    Reusing a single client preserves connection pooling / keep-alive across
    proxied operator turns instead of paying a fresh TCP+TLS handshake per
    request. The client is created lazily and never closed for the life of the
    process; its connection pool is shared across all forwarded requests.
    """
    global _UPSTREAM_CLIENT
    client = _UPSTREAM_CLIENT
    if client is not None and not client.is_closed:
        return client
    with _UPSTREAM_CLIENT_LOCK:
        client = _UPSTREAM_CLIENT
        if client is None or client.is_closed:
            client = httpx.Client(
                timeout=httpx.Timeout(900.0, connect=10.0),
                follow_redirects=False,
            )
            _UPSTREAM_CLIENT = client
        return client

_PLACEHOLDER_API_KEY = "takyon-operator-gateway"
_OPENAI_GATEWAY_BASE_URL = "https://operator-gateway.local/v1"
_ANTHROPIC_GATEWAY_BASE_URL = "https://operator-gateway.local"
_OPERATOR_GATEWAY_BROKER_URL_ENV = "TAKYON_OPERATOR_GATEWAY_BROKER_URL"
_SUPPORTED_API_MODES = {"chat_completions", "codex_responses", "anthropic_messages"}
_HOP_BY_HOP_HEADERS = {
    "authorization",
    "connection",
    "content-length",
    "host",
    "transfer-encoding",
}


@dataclass(frozen=True)
class OperatorGatewayContext:
    provider: str
    requested_provider: str
    api_mode: str
    upstream_base_url: str
    operator_user_id: str = ""
    business_slug: str = ""
    workspace_root: str = ""


def operator_gateway_supported(runtime: dict[str, Any]) -> bool:
    return str(runtime.get("api_mode") or "").strip().lower() in _SUPPORTED_API_MODES


def operator_gateway_placeholder_api_key() -> str:
    return _PLACEHOLDER_API_KEY


def _operator_gateway_dispatch() -> dict[str, dict[str, Any]]:
    """Single source of truth mapping each supported ``api_mode`` to its local
    gateway base URL and the client-replacement function used to swap the outer
    agent's transport. Anything not listed here falls back to the OpenAI-family
    defaults. The replace functions are referenced here (rather than at import
    time) because they are defined later in this module."""
    return {
        "anthropic_messages": {
            "base_url": _ANTHROPIC_GATEWAY_BASE_URL,
            "replace_fn": _replace_anthropic_gateway_client,
        },
    }


def _operator_gateway_default_dispatch() -> dict[str, Any]:
    return {
        "base_url": _OPENAI_GATEWAY_BASE_URL,
        "replace_fn": _replace_openai_gateway_client,
    }


def _operator_gateway_dispatch_for(api_mode: str) -> dict[str, Any]:
    key = str(api_mode or "").strip().lower()
    return _operator_gateway_dispatch().get(key, _operator_gateway_default_dispatch())


def operator_gateway_client_base_url(api_mode: str) -> str:
    return _operator_gateway_dispatch_for(api_mode)["base_url"]


def build_operator_gateway_context(
    runtime: dict[str, Any],
    *,
    operator_user_id: str | None = None,
    business_slug: str | None = None,
    workspace_root: str | None = None,
) -> OperatorGatewayContext:
    provider = str(runtime.get("provider") or "").strip().lower()
    requested = str(runtime.get("requested_provider") or provider).strip().lower()
    api_mode = str(runtime.get("api_mode") or "").strip().lower()
    base_url = str(runtime.get("base_url") or "").strip()
    return OperatorGatewayContext(
        provider=provider,
        requested_provider=requested or provider,
        api_mode=api_mode,
        upstream_base_url=base_url.rstrip("/"),
        operator_user_id=str(operator_user_id or "").strip(),
        business_slug=str(business_slug or "").strip(),
        workspace_root=str(workspace_root or "").strip(),
    )


def build_operator_gateway_http_client(context: OperatorGatewayContext) -> httpx.Client:
    transport = httpx.MockTransport(_OperatorGatewayHandler(context).handle)
    return httpx.Client(
        transport=transport,
        base_url=operator_gateway_client_base_url(context.api_mode),
        timeout=httpx.Timeout(900.0, connect=10.0),
    )


def enable_operator_gateway(
    agent: Any,
    runtime: dict[str, Any],
    *,
    operator_user_id: str | None = None,
    business_slug: str | None = None,
    workspace_root: str | None = None,
) -> Any:
    if not operator_gateway_supported(runtime):
        raise RuntimeError(
            "operator gateway does not yet support "
            f"api_mode={runtime.get('api_mode')!r}"
        )

    context = build_operator_gateway_context(
        runtime,
        operator_user_id=operator_user_id,
        business_slug=business_slug,
        workspace_root=workspace_root,
    )
    agent._takyon_operator_gateway = True
    agent._takyon_operator_gateway_context = context
    # Disable direct credential rotation / pool failover paths: the outer
    # agent should not try to re-resolve raw provider credentials.
    agent._credential_pool = None

    _operator_gateway_dispatch_for(context.api_mode)["replace_fn"](agent, context)
    return agent


def rebuild_operator_gateway_transport(agent: Any) -> None:
    context = getattr(agent, "_takyon_operator_gateway_context", None)
    if not isinstance(context, OperatorGatewayContext):
        raise RuntimeError("operator gateway context is missing")
    runtime = {
        "provider": context.provider,
        "requested_provider": context.requested_provider,
        "api_mode": context.api_mode,
        "base_url": context.upstream_base_url,
    }
    enable_operator_gateway(
        agent,
        runtime,
        operator_user_id=context.operator_user_id,
        business_slug=context.business_slug,
        workspace_root=context.workspace_root,
    )


def build_operator_gateway_agent(
    *,
    runtime: dict[str, Any],
    model: str,
    operator_user_id: str | None = None,
    business_slug: str | None = None,
    workspace_root: str | None = None,
    agent_kwargs: dict[str, Any] | None = None,
):
    from run_agent import AIAgent

    if not operator_gateway_supported(runtime):
        raise RuntimeError(
            "operator gateway does not yet support "
            f"api_mode={runtime.get('api_mode')!r}"
        )

    agent_kwargs = dict(agent_kwargs or {})
    gateway_base = operator_gateway_client_base_url(runtime.get("api_mode") or "")
    agent = AIAgent(
        model=model,
        provider=runtime.get("provider"),
        base_url=gateway_base,
        api_key=_PLACEHOLDER_API_KEY,
        api_mode=runtime.get("api_mode"),
        **agent_kwargs,
    )
    return enable_operator_gateway(
        agent,
        runtime,
        operator_user_id=operator_user_id,
        business_slug=business_slug,
        workspace_root=workspace_root,
    )


def _replace_openai_gateway_client(agent: Any, context: OperatorGatewayContext) -> None:
    try:
        if getattr(agent, "client", None) is not None:
            agent._close_openai_client(agent.client, reason="operator_gateway_swap", shared=True)
    except Exception:
        pass

    http_client = build_operator_gateway_http_client(context)
    agent.api_key = _PLACEHOLDER_API_KEY
    # Keep the semantic base URL on the agent so provider-specific request-shape
    # logic still sees the true upstream backend family, while the SDK client
    # itself talks only to the local gateway transport.
    agent.base_url = context.upstream_base_url or agent.base_url
    client_kwargs = {
        "api_key": _PLACEHOLDER_API_KEY,
        "base_url": operator_gateway_client_base_url(context.api_mode),
        "http_client": http_client,
    }
    timeout = getattr(agent, "_client_kwargs", {}).get("timeout")
    if timeout is not None:
        client_kwargs["timeout"] = timeout
    agent._client_kwargs = client_kwargs
    agent.client = agent._create_openai_client(
        client_kwargs,
        reason="operator_gateway",
        shared=True,
    )


def _replace_anthropic_gateway_client(agent: Any, context: OperatorGatewayContext) -> None:
    from agent.anthropic_adapter import build_anthropic_client
    from takyon_cli.timeouts import get_provider_request_timeout

    try:
        if getattr(agent, "_anthropic_client", None) is not None:
            agent._anthropic_client.close()
    except Exception:
        pass

    timeout = get_provider_request_timeout(agent.provider, agent.model)
    http_client = build_operator_gateway_http_client(context)
    agent._anthropic_client = build_anthropic_client(
        _PLACEHOLDER_API_KEY,
        operator_gateway_client_base_url(context.api_mode),
        timeout=timeout,
        http_client=http_client,
    )
    agent._anthropic_api_key = _PLACEHOLDER_API_KEY
    agent._anthropic_base_url = context.upstream_base_url
    agent._is_anthropic_oauth = False
    agent.api_key = _PLACEHOLDER_API_KEY
    agent.base_url = context.upstream_base_url or agent.base_url


class _ProxyByteStream(httpx.SyncByteStream):
    def __init__(self, upstream_response: httpx.Response):
        self._response = upstream_response
        self._closed = False

    def __iter__(self) -> Iterable[bytes]:
        try:
            for chunk in self._response.iter_bytes():
                if chunk:
                    yield chunk
        finally:
            self.close()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        # Only release the streamed response back to the shared pool; the
        # pooled upstream client is process-scoped and must stay open.
        self._response.close()


class _OperatorGatewayHandler:
    def __init__(self, context: OperatorGatewayContext):
        self._context = context

    def handle(self, request: httpx.Request) -> httpx.Response:
        body_bytes = request.content or b""
        body = _json_body_or_empty(body_bytes)
        runtime = _resolve_runtime_for_request(self._context, body)
        api_mode = str(runtime.get("api_mode") or "").strip().lower()
        path = request.url.path or "/"

        if path == "/v1/chat/completions":
            _require_mode(api_mode, "chat_completions")
        elif path == "/v1/responses":
            _require_mode(api_mode, "codex_responses")
        elif path == "/v1/messages":
            _require_mode(api_mode, "anthropic_messages")
        else:
            return _json_error(404, f"unsupported operator gateway path: {path}")

        stream_requested = bool(body.get("stream"))
        try:
            return _proxy_upstream_request(
                runtime=runtime,
                path=path,
                body_bytes=body_bytes,
                incoming_headers=request.headers,
                stream=stream_requested,
            )
        except Exception as exc:
            logger.exception("operator gateway upstream request failed")
            return _json_error(502, f"operator gateway upstream request failed: {exc}")


def _operator_anthropic_broker_lockdown() -> bool:
    """Whether the operator CEO loop must route Anthropic through the safebox PROXY (key-free) rather
    than resolving a raw provider key locally. Defaults ON whenever a remote safebox is configured (same
    contract as the coding worker's ``core._claude_agent_broker_lockdown_enabled``)."""
    from plugins.takyon import core as takyon_core

    return bool(takyon_core._claude_agent_broker_lockdown_enabled())


def _operator_anthropic_broker_url() -> str:
    """Safebox proxy root for host-side operator gateway calls.

    The coding worker may need a Docker-reachable URL such as ``host.docker.internal``, while the
    interactive CLI gateway runs on the Mac host and needs the localhost tunnel. Keep that host override
    separate so local operator chat does not inherit a container-only name.
    """
    import os

    override = str(os.getenv(_OPERATOR_GATEWAY_BROKER_URL_ENV, "") or "").strip().rstrip("/")
    if override:
        return override
    from plugins.takyon import core as takyon_core

    return str(takyon_core._claude_agent_broker_url() or "").strip().rstrip("/")


def _resolve_anthropic_broker_runtime(
    context: OperatorGatewayContext,
    body: dict[str, Any],
) -> dict[str, Any]:
    """Build the key-free anthropic runtime for a CEO turn: point upstream at the safebox PROXY ROOT and
    authenticate with a minted ``operator.session`` token carrying the REAL operator.

    The raw provider key is NEVER resolved on this plane — the safebox holds it and meters each proxied
    call against THAT owner's control-plane billing allowance. Fails CLOSED (raises) when the proxy URL or
    the owner cannot be resolved or the mint is refused, so the CEO turn refuses rather than falling back
    to a raw key."""
    from plugins.takyon import safebox

    broker_base_url = _operator_anthropic_broker_url()
    if not broker_base_url:
        raise RuntimeError(
            "operator anthropic broker lockdown is on but no safebox proxy URL is configured "
            f"(set {_OPERATOR_GATEWAY_BROKER_URL_ENV}, TAKYON_CLAUDE_AGENT_BROKER_URL, or "
            "TAKYON_SAFEBOX_URL to the safebox ROOT)"
        )
    business_slug = str(context.business_slug or "").strip()
    operator_user_id = _resolve_operator_owner_user_id(context)
    if not operator_user_id:
        raise RuntimeError(
            "operator anthropic broker lockdown requires a resolved operator owner to mint an "
            "operator.session token; it is missing for this CEO turn"
        )
    session_token = str(
        safebox.mint_operator_session_token(
            business_slug,
            operator_user_id,
        )
        or ""
    ).strip()
    if not session_token:
        raise RuntimeError(
            "the safebox refused to mint an operator.session token for this CEO turn (the operator does "
            "not own the business / root scope session, or /v1/operator/session-token is unavailable)"
        )
    return {
        "provider": context.provider or "anthropic",
        "requested_provider": context.requested_provider or context.provider or "anthropic",
        "api_mode": "anthropic_messages",
        # The safebox proxy serves the stock /v1/messages path the SDK appends, so the upstream base is
        # the safebox ROOT and the session token is the credential. No raw provider key is resolved.
        "base_url": broker_base_url,
        "api_key": session_token,
    }


def _resolve_operator_owner_user_id(context: OperatorGatewayContext) -> str:
    """Resolve the REAL business-owner user id for the CEO turn's business. Prefers the authoritative
    ``businesses.owner_user_id`` read; falls back to the operator id the launch path already injected on
    the context. Returns "" when neither is available (the caller fails closed)."""
    business_slug = str(context.business_slug or "").strip()
    if business_slug:
        try:
            from plugins.takyon.core import TakyonStore

            store = TakyonStore()
            with store._connect() as conn:
                business = store._ensure_business(conn, business_slug)
            owner = str((business or {}).get("owner_user_id") or "").strip()
            if owner:
                return owner
        except Exception:
            logger.debug("operator gateway: owner_user_id read failed", exc_info=True)
    return str(context.operator_user_id or "").strip()


def _resolve_runtime_for_request(
    context: OperatorGatewayContext,
    body: dict[str, Any],
) -> dict[str, Any]:
    # Anthropic CEO turns route THROUGH the safebox proxy (key-free, operator.session) under broker
    # lockdown — the raw provider key is never resolved on this plane. Other api_modes still resolve a
    # runtime locally (those providers have no safebox proxy route yet).
    if str(context.api_mode or "").strip().lower() == "anthropic_messages" and _operator_anthropic_broker_lockdown():
        return _resolve_anthropic_broker_runtime(context, body)

    from takyon_cli.runtime_provider import resolve_runtime_provider

    target_model = str(body.get("model") or "").strip() or None
    kwargs: dict[str, Any] = {"requested": context.requested_provider or None}
    if target_model:
        kwargs["target_model"] = target_model
    if context.upstream_base_url:
        kwargs["explicit_base_url"] = context.upstream_base_url
    runtime = resolve_runtime_provider(**kwargs)
    if not isinstance(runtime.get("api_key"), str) or not str(runtime.get("api_key")).strip():
        raise RuntimeError("no provider API key resolved for operator gateway request")
    return runtime


def _require_mode(actual: str, expected: str) -> None:
    if actual != expected:
        raise RuntimeError(
            f"operator gateway route requires api_mode={expected}, got {actual or 'unknown'}"
        )


def _json_body_or_empty(body_bytes: bytes) -> dict[str, Any]:
    if not body_bytes:
        return {}
    try:
        data = json.loads(body_bytes.decode("utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _proxy_upstream_request(
    *,
    runtime: dict[str, Any],
    path: str,
    body_bytes: bytes,
    incoming_headers: httpx.Headers,
    stream: bool,
) -> httpx.Response:
    target_url, query_params = _upstream_url(runtime, path)
    headers = _upstream_headers(runtime, incoming_headers)
    client = _get_upstream_client()
    request = client.build_request(
        "POST",
        target_url,
        params=query_params or None,
        headers=headers,
        content=body_bytes,
    )

    if not stream:
        response = client.send(request, stream=False)
        content = response.content
        out = httpx.Response(
            response.status_code,
            headers=_response_headers(response.headers, streaming=False),
            content=content,
            request=None,
        )
        response.close()
        return out

    response = client.send(request, stream=True)
    return httpx.Response(
        response.status_code,
        headers=_response_headers(response.headers, streaming=True),
        stream=_ProxyByteStream(response),
        request=None,
    )


def _upstream_url(runtime: dict[str, Any], path: str) -> tuple[str, list[tuple[str, str]]]:
    raw = str(runtime.get("base_url") or "").strip()
    if not raw:
        raise RuntimeError("resolved runtime did not provide a base_url")
    parsed = urlparse(raw)
    base = parsed._replace(query="", fragment="")
    query_params = list(parse_qsl(parsed.query, keep_blank_values=True))
    base_text = base.geturl().rstrip("/")
    if path == "/v1/chat/completions":
        suffix = "/chat/completions"
    elif path == "/v1/responses":
        suffix = "/responses"
    else:
        suffix = "/v1/messages"
    return f"{base_text}{suffix}", query_params


def _upstream_headers(runtime: dict[str, Any], incoming_headers: httpx.Headers) -> dict[str, str]:
    api_mode = str(runtime.get("api_mode") or "").strip().lower()
    upstream_headers: dict[str, str] = {}

    for key, value in incoming_headers.items():
        lowered = key.lower()
        if lowered in _HOP_BY_HOP_HEADERS:
            continue
        upstream_headers[key] = value

    if api_mode == "anthropic_messages":
        upstream_headers.update(_anthropic_auth_headers(runtime))
    else:
        upstream_headers.update(_openai_like_auth_headers(runtime))

    return upstream_headers


def _openai_like_auth_headers(runtime: dict[str, Any]) -> dict[str, str]:
    from agent.auxiliary_client import build_nvidia_nim_headers, build_or_headers
    from providers import get_provider_profile
    from takyon_cli.models import copilot_default_headers
    from utils import base_url_host_matches
    import run_agent

    base_url = str(runtime.get("base_url") or "")
    provider = str(runtime.get("provider") or "").strip().lower()
    api_key = str(runtime.get("api_key") or "").strip()
    headers: dict[str, str] = {"Authorization": f"Bearer {api_key}"}

    if base_url_host_matches(base_url, "openrouter.ai"):
        headers.update(build_or_headers())
    elif base_url_host_matches(base_url, "integrate.api.nvidia.com"):
        headers.update(build_nvidia_nim_headers(base_url))
    elif base_url_host_matches(base_url, "api.routermint.com"):
        headers.update(run_agent._routermint_headers())
    elif base_url_host_matches(base_url, "api.githubcopilot.com"):
        headers.update(copilot_default_headers())
    elif base_url_host_matches(base_url, "api.kimi.com"):
        headers["User-Agent"] = "claude-code/0.1.0"
    elif base_url_host_matches(base_url, "portal.qwen.ai"):
        headers.update(run_agent._qwen_portal_headers())
    elif base_url_host_matches(base_url, "chatgpt.com"):
        from agent.auxiliary_client import _codex_cloudflare_headers

        headers.update(_codex_cloudflare_headers(api_key))
    else:
        try:
            profile = get_provider_profile(provider)
            if profile and profile.default_headers:
                headers.update({str(k): str(v) for k, v in profile.default_headers.items()})
        except Exception:
            pass

    return headers


def _anthropic_auth_headers(runtime: dict[str, Any]) -> dict[str, str]:
    from agent.anthropic_adapter import (
        _common_betas_for_base_url,
        _get_claude_code_version,
        _is_kimi_coding_endpoint,
        _is_oauth_token,
        _is_third_party_anthropic_endpoint,
        _normalize_base_url_text,
        _requires_bearer_auth,
        _is_azure_anthropic_endpoint,
    )

    base_url = _normalize_base_url_text(runtime.get("base_url"))
    api_key = str(runtime.get("api_key") or "").strip()
    headers: dict[str, str] = {}
    common_betas = _common_betas_for_base_url(base_url)
    if common_betas:
        headers["anthropic-beta"] = ",".join(common_betas)

    if _is_kimi_coding_endpoint(base_url):
        headers["User-Agent"] = "claude-code/0.1.0"
        headers["x-api-key"] = api_key
    elif _requires_bearer_auth(base_url):
        headers["Authorization"] = f"Bearer {api_key}"
    elif _is_third_party_anthropic_endpoint(base_url):
        headers["x-api-key"] = api_key
    elif _is_oauth_token(api_key):
        oauth_betas = common_betas + ["oauth-2025-04-20"]
        headers["Authorization"] = f"Bearer {api_key}"
        headers["anthropic-beta"] = ",".join(oauth_betas)
        headers["user-agent"] = f"claude-cli/{_get_claude_code_version()} (external, cli)"
        headers["x-app"] = "cli"
    else:
        headers["x-api-key"] = api_key

    if _is_azure_anthropic_endpoint(base_url):
        # Azure-hosted Anthropic endpoints still use the same body and path,
        # but they require an API version query parameter. The query param is
        # derived separately in `_upstream_url`; keep the headers here only.
        headers.setdefault("accept", "application/json")
    return headers


def _response_headers(headers: httpx.Headers, *, streaming: bool) -> dict[str, str]:
    out: dict[str, str] = {}
    for key, value in headers.items():
        lowered = key.lower()
        if lowered in {
            "connection",
            "content-encoding",
            "content-length",
            "transfer-encoding",
        }:
            continue
        out[key] = value
    if streaming:
        out.pop("Content-Length", None)
        out.pop("content-length", None)
    return out


def _json_error(status_code: int, detail: str) -> httpx.Response:
    payload = {"error": {"message": detail}}
    return httpx.Response(
        status_code,
        headers={"content-type": "application/json"},
        content=json.dumps(payload).encode("utf-8"),
    )
