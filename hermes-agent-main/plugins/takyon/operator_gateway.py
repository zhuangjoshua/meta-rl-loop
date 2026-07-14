"""Takyon primary Agent SDK facade and provider transport helpers.

New operator turns use :class:`PrimaryAgentFacade`, which delegates the model
loop to the shared Claude Agent SDK subprocess and retains only the metadata
surface needed by the existing CLI/dashboard RPC layer.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import uuid
from types import SimpleNamespace
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence
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
    model: str = ""
    operator_user_id: str = ""
    business_slug: str = ""
    workspace_root: str = ""


def operator_gateway_supported(runtime: dict[str, Any]) -> bool:
    return str(runtime.get("api_mode") or "").strip().lower() in _SUPPORTED_API_MODES


def operator_gateway_placeholder_api_key() -> str:
    return _PLACEHOLDER_API_KEY


def _require_strict_ceo_role(runtime: dict[str, Any], model: str) -> None:
    strict = str(os.getenv("TAKYON_STRICT_MODEL_ROLES") or "").strip().lower()
    if strict not in {"1", "true", "yes", "on"}:
        return
    pinned = str(os.getenv("TAKYON_MODEL") or "").strip()
    mode = str(runtime.get("api_mode") or "").strip().lower()
    host = (urlparse(str(runtime.get("base_url") or "")).hostname or "").lower()
    if pinned != "gpt-5.5" or model != pinned:
        raise RuntimeError("strict CEO role requires TAKYON_MODEL='gpt-5.5'")
    if mode != "codex_responses" or host != "api.openai.com":
        raise RuntimeError(
            "strict CEO role requires OpenAI Responses at https://api.openai.com/v1"
        )


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
    model: str,
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
        model=str(model or "").strip(),
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
    pinned_model: str | None = None,
    operator_user_id: str | None = None,
    business_slug: str | None = None,
    workspace_root: str | None = None,
) -> Any:
    if not operator_gateway_supported(runtime):
        raise RuntimeError(
            "operator gateway does not yet support "
            f"api_mode={runtime.get('api_mode')!r}"
        )

    model = str(pinned_model or getattr(agent, "model", "") or "").strip()
    if not model:
        raise RuntimeError("operator gateway requires an explicit model pin")
    if str(getattr(agent, "model", "") or "").strip() != model:
        raise RuntimeError(
            "operator gateway model changed after pin: "
            f"expected {model!r}, got {getattr(agent, 'model', '')!r}"
        )
    _require_strict_ceo_role(runtime, model)
    context = build_operator_gateway_context(
        runtime,
        model=model,
        operator_user_id=operator_user_id,
        business_slug=business_slug,
        workspace_root=workspace_root,
    )
    agent._takyon_operator_gateway = True
    agent._takyon_operator_gateway_context = context
    agent._takyon_strict_model_pin = model
    compressor = getattr(agent, "context_compressor", None)
    if compressor is not None:
        compressor._takyon_operator_gateway_context = context
    # Takyon CEO turns never switch provider/model. Clear every fallback-chain state even if a
    # generic caller tried to populate it, and disable credential-pool failover.
    agent._fallback_chain = []
    agent._fallback_model = None
    agent._fallback_index = 0
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
        pinned_model=context.model,
        operator_user_id=context.operator_user_id,
        business_slug=context.business_slug,
        workspace_root=context.workspace_root,
    )


_PRIMARY_AGENT_MODEL = "deepseek-v4-pro"
_PRIMARY_INTERACTIVE_TOOLSETS = (
    "takyon",
    "takyon-authority",
    "web",
    "skills",
    "todo",
)
_PRIMARY_INTERACTIVE_DISABLED_TOOLSETS = (
    "cronjob",
    "messaging",
    "memory",
    "session_search",
    "terminal",
    "file",
    "browser",
    "code_execution",
)


def primary_interactive_budget_usd() -> float:
    """Return the explicit per-turn SDK ceiling; absence is a deploy error."""

    raw = str(os.getenv("TAKYON_PRIMARY_AGENT_MAX_BUDGET_USD") or "").strip()
    try:
        value = float(raw or 0)
    except (TypeError, ValueError):
        value = 0.0
    if value <= 0 or value > 100:
        raise RuntimeError(
            "interactive Claude Agent SDK turns require "
            "TAKYON_PRIMARY_AGENT_MAX_BUDGET_USD between 0 and 100"
        )
    return value


def primary_interactive_epoch() -> str:
    """Return a fresh Safebox invocation-envelope epoch for one SDK turn."""

    return f"interactive:{uuid.uuid4()}"


def compose_primary_agent_system_prompt(*parts: object) -> str:
    """Compose stable CEO policy with optional UI/session overlays."""

    try:
        from plugins.takyon.turn_runtime import _load_ceo_prompt

        ceo_prompt = str(_load_ceo_prompt() or "").strip()
    except Exception:
        ceo_prompt = ""
    values: list[str] = []
    for candidate in (ceo_prompt, *parts):
        text = str(candidate or "").strip()
        if text and text not in values:
            values.append(text)
    values.append(
        "Use the approved native skills through the Skill tool when their "
        "descriptions match the request. Skill availability, semantic capability "
        "bindings, tool exposure, and write paths are enforced by the immutable "
        "published HANDOFF policy; never infer broader authority from a skill body."
    )
    return "\n\n".join(values).strip()


def _primary_message_text(value: object) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        text_parts: list[str] = []
        for item in value:
            if not isinstance(item, Mapping):
                continue
            if str(item.get("type") or "") == "text":
                text = str(item.get("text") or "").strip()
                if text:
                    text_parts.append(text)
        if text_parts:
            return "\n\n".join(text_parts)
    raise RuntimeError(
        "primary Claude Agent SDK turns require text input; image attachments "
        "must be pre-analyzed by the existing vision rail"
    )


class PrimaryAgentFacade:
    """Metadata-compatible shell around the shared primary SDK subprocess.

    The dashboard keeps its established RPC/session/history structures while
    model state lives in the durable SDK SessionStore.  This object holds no
    provider credential and exposes no nested-agent primitive.
    """

    def __init__(
        self,
        *,
        operator_user_id: str = "",
        business_slug: str = "",
        workspace_root: str = "",
        agent_kwargs: Mapping[str, Any] | None = None,
    ) -> None:
        kwargs = dict(agent_kwargs or {})
        self.model = _PRIMARY_AGENT_MODEL
        self.provider = "safebox"
        self.api_mode = "anthropic_messages"
        self.base_url = ""
        self.api_key = "scoped-at-call"
        self.max_iterations = int(kwargs.get("max_iterations") or 90)
        self.enabled_toolsets = list(
            kwargs.get("enabled_toolsets") or _PRIMARY_INTERACTIVE_TOOLSETS
        )
        self.disabled_toolsets = list(
            kwargs.get("disabled_toolsets")
            or _PRIMARY_INTERACTIVE_DISABLED_TOOLSETS
        )
        self.ephemeral_system_prompt = kwargs.get("ephemeral_system_prompt") or None
        self.reasoning_config = kwargs.get("reasoning_config")
        self.service_tier = kwargs.get("service_tier")
        self.request_overrides = dict(kwargs.get("request_overrides") or {})
        self.providers_allowed = None
        self.providers_ignored = None
        self.providers_order = None
        self.provider_sort = None
        self.provider_require_parameters = False
        self.provider_data_collection = None
        self.openrouter_min_coding_score = None
        self.pass_session_id = bool(kwargs.get("pass_session_id"))
        self.skip_context_files = True
        self.skip_memory = True
        self.verbose_logging = bool(kwargs.get("verbose_logging"))
        self.session_id = str(kwargs.get("session_id") or "")
        self._session_db = kwargs.get("session_db")
        self._checkpoint_mgr = None
        self._fallback_model = None
        self._cached_system_prompt = compose_primary_agent_system_prompt(
            self.ephemeral_system_prompt
        )
        self.tools: list[dict[str, Any]] = []
        self.context_compressor = SimpleNamespace(
            last_prompt_tokens=0,
            context_length=0,
            compression_count=0,
        )
        self.session_input_tokens = 0
        self.session_prompt_tokens = 0
        self.session_output_tokens = 0
        self.session_completion_tokens = 0
        self.session_cache_read_tokens = 0
        self.session_cache_write_tokens = 0
        self.session_reasoning_tokens = 0
        self.session_total_tokens = 0
        self.session_api_calls = 0
        self.session_estimated_cost_usd = 0.0
        self.session_cost_status = "unknown"
        self._takyon_primary_sdk = True
        self._takyon_operator_gateway = True
        self._takyon_operator_gateway_context = OperatorGatewayContext(
            provider="safebox",
            requested_provider="safebox",
            api_mode="anthropic_messages",
            upstream_base_url="",
            model=_PRIMARY_AGENT_MODEL,
            operator_user_id=str(operator_user_id or "").strip(),
            business_slug=str(business_slug or "").strip(),
            workspace_root=str(workspace_root or "").strip(),
        )
        self.tool_start_callback = kwargs.get("tool_start_callback")
        self.tool_complete_callback = kwargs.get("tool_complete_callback")
        self.tool_progress_callback = kwargs.get("tool_progress_callback")
        self.status_callback = kwargs.get("status_callback")
        self.background_review_callback = None
        self.interim_assistant_callback = None
        self._interrupted = threading.Event()

    def _scope(self) -> tuple[str, str, str]:
        from gateway.session_context import get_session_env

        context = self._takyon_operator_gateway_context
        owner = str(
            context.operator_user_id
            or get_session_env("TAKYON_SESSION_USER_ID", "")
            or ""
        ).strip()
        business = str(
            context.business_slug
            or get_session_env("TAKYON_SESSION_BUSINESS_SLUG", "")
            or ""
        ).strip()
        workspace = str(
            context.workspace_root
            or get_session_env("TAKYON_SESSION_WORKSPACE_ROOT", "")
            or ""
        ).strip()
        if not owner or not business or not workspace:
            raise RuntimeError(
                "primary Claude Agent SDK operator turns require exact operator, "
                "business, and workspace scope; global model chat is disabled"
            )
        return owner, business, workspace

    def _apply_usage(self, receipt: Mapping[str, Any]) -> None:
        usage = receipt.get("usage") if isinstance(receipt.get("usage"), Mapping) else {}
        self.session_input_tokens = int(
            usage.get("input_tokens") or usage.get("input") or 0
        )
        self.session_prompt_tokens = self.session_input_tokens
        self.session_output_tokens = int(
            usage.get("output_tokens") or usage.get("output") or 0
        )
        self.session_completion_tokens = self.session_output_tokens
        self.session_cache_read_tokens = int(
            usage.get("cache_read_input_tokens") or usage.get("cache_read") or 0
        )
        self.session_cache_write_tokens = int(
            usage.get("cache_creation_input_tokens") or usage.get("cache_write") or 0
        )
        self.session_total_tokens = self.session_input_tokens + self.session_output_tokens
        self.session_api_calls = 1
        raw_cost = receipt.get("total_cost_usd")
        if isinstance(raw_cost, (int, float)):
            self.session_estimated_cost_usd = float(raw_cost)
            self.session_cost_status = "actual"

    def run_conversation(
        self,
        user_message: object,
        *,
        conversation_history: Sequence[Mapping[str, Any]] | None = None,
        system_message: str | None = None,
        task_id: str | None = None,
        stream_callback: Any = None,
        **_kwargs: Any,
    ) -> dict[str, Any]:
        from plugins.takyon.claude_sdk_runtime import (
            primary_sdk_session_project_key,
            run_primary_sdk_subprocess,
            stable_sdk_session_id,
        )
        from plugins.takyon.claude_sdk_sessions import PostgresClaudeSdkSessionStore

        owner, business, workspace = self._scope()
        stable_session = stable_sdk_session_id(self.session_id or task_id)
        store = PostgresClaudeSdkSessionStore(
            operator_user_id=owner,
            business_slug=business,
        )
        key = {
            "projectKey": primary_sdk_session_project_key(
                operator_user_id=owner,
                business=business,
            ),
            "sessionId": stable_session,
        }
        resume = store.load(key) is not None

        def progress(event: Mapping[str, Any]) -> None:
            kind = str(event.get("kind") or "runtime")
            status = str(event.get("status") or "running")
            detail = str(event.get("detail") or "")
            trace = event.get("trace") if isinstance(event.get("trace"), Mapping) else {}
            if kind == "skill" and callable(self.tool_progress_callback):
                self.tool_progress_callback(
                    f"skill.{status}",
                    name=str(trace.get("skill_name") or "Skill"),
                    preview=detail,
                    status=status,
                )
            elif kind in {"session", "provider", "turn"} and callable(
                self.status_callback
            ):
                self.status_callback(kind, detail)

        result = run_primary_sdk_subprocess(
            business=business,
            operator_user_id=owner,
            system_prompt=compose_primary_agent_system_prompt(
                self.ephemeral_system_prompt,
                system_message,
            ),
            user_prompt=_primary_message_text(user_message),
            enabled_toolsets=self.enabled_toolsets,
            disabled_toolsets=self.disabled_toolsets,
            workspace_root=workspace,
            session_id=stable_session,
            resume_session=resume,
            session_store=store,
            task_id=str(task_id or self.session_id or ""),
            mode="interactive",
            epoch=primary_interactive_epoch(),
            max_turns=self.max_iterations,
            max_budget_usd=primary_interactive_budget_usd(),
            effort="high",
            stop_probe=lambda _elapsed, _idle: (
                "operator interrupted the SDK turn" if self._interrupted.is_set() else None
            ),
            progress_callback=progress,
            on_tool_start=self.tool_start_callback,
            on_tool_complete=self.tool_complete_callback,
        )
        self._apply_usage(result)
        final = str(result.get("summary") or "").strip()
        if not final:
            raise RuntimeError("primary Claude Agent SDK returned no final response")
        if callable(stream_callback):
            stream_callback(final)
        messages = [dict(item) for item in (conversation_history or ())]
        messages.extend(
            [
                {"role": "user", "content": user_message},
                {"role": "assistant", "content": final},
            ]
        )
        return {
            "final_response": final,
            "messages": messages,
            "completed": True,
            "sdk_receipt": dict(result),
        }

    def interrupt(self) -> None:
        self._interrupted.set()

    def steer(self, _text: str) -> bool:
        return False

    def close(self) -> None:
        self._interrupted.set()

    def compact_session(
        self,
        *,
        session_id: str,
        focus_topic: str | None = None,
        operator_user_id: str | None = None,
        business_slug: str | None = None,
        workspace_root: str | None = None,
    ) -> dict[str, Any]:
        """Run the SDK's native manual compaction on one exact durable session."""

        from plugins.takyon.claude_sdk_runtime import (
            primary_sdk_session_project_key,
            run_primary_sdk_subprocess,
            stable_sdk_session_id,
        )
        from plugins.takyon.claude_sdk_sessions import PostgresClaudeSdkSessionStore

        requested_session = str(session_id or self.session_id or "").strip()
        if not requested_session:
            raise RuntimeError("manual SDK compaction requires an exact session ID")
        focus = " ".join(str(focus_topic or "").split())
        if "\x00" in focus or len(focus) > 500:
            raise RuntimeError("manual SDK compaction focus must be at most 500 characters")
        explicit_scope = any(
            value is not None
            for value in (operator_user_id, business_slug, workspace_root)
        )
        if explicit_scope:
            if operator_user_id is None or business_slug is None or workspace_root is None:
                raise RuntimeError(
                    "manual SDK compaction requires complete operator, business, and workspace scope"
                )
            owner = str(operator_user_id or "").strip()
            business = str(business_slug or "").strip()
            workspace = str(workspace_root or "").strip()
            if not owner or not workspace:
                raise RuntimeError(
                    "manual SDK compaction requires exact operator and workspace scope"
                )
        else:
            owner, business, workspace = self._scope()
        stable_session = stable_sdk_session_id(requested_session)
        store = PostgresClaudeSdkSessionStore(
            operator_user_id=owner,
            business_slug=business,
        )
        key = {
            "projectKey": primary_sdk_session_project_key(
                operator_user_id=owner,
                business=business,
            ),
            "sessionId": stable_session,
        }
        if store.load(key) is None:
            raise RuntimeError(
                "manual SDK compaction requires an existing durable session"
            )

        def progress(event: Mapping[str, Any]) -> None:
            if not callable(self.status_callback):
                return
            if (
                str(event.get("kind") or "") == "session"
                and str(event.get("status") or "") == "compacted"
            ):
                self.status_callback(
                    "session", str(event.get("detail") or "Context compacted.")
                )

        result = run_primary_sdk_subprocess(
            business=business,
            operator_user_id=owner,
            system_prompt=compose_primary_agent_system_prompt(
                self.ephemeral_system_prompt
            ),
            user_prompt=f"/compact{f' {focus}' if focus else ''}",
            enabled_toolsets=self.enabled_toolsets,
            disabled_toolsets=self.disabled_toolsets,
            workspace_root=workspace,
            session_id=stable_session,
            resume_session=True,
            session_store=store,
            task_id=f"compact:{stable_session}",
            mode="interactive",
            epoch=primary_interactive_epoch(),
            operation="compact",
            max_turns=1,
            max_budget_usd=primary_interactive_budget_usd(),
            effort="high",
            inactivity_limit=600.0,
            progress_callback=progress,
        )
        receipt = result.get("compact_receipt")
        if not isinstance(receipt, Mapping) or receipt.get("trigger") != "manual":
            raise RuntimeError(
                "manual SDK compaction completed without a durable boundary receipt"
            )
        self._apply_usage(result)
        return dict(result)

    def refresh_tools(self) -> list[dict[str, Any]]:
        raise RuntimeError(
            "primary SDK tool inventory is immutable for the published runtime release"
        )

    def reload_mcp_tools(self) -> list[dict[str, Any]]:
        raise RuntimeError(
            "primary SDK MCP inventory is immutable for the published runtime release"
        )

    def commit_memory_session(self, _history: object) -> None:
        return None

    def switch_model(self, *, new_model: str, **_kwargs: Any) -> None:
        if str(new_model or "").strip() != _PRIMARY_AGENT_MODEL:
            raise RuntimeError(
                f"primary operator model is pinned to {_PRIMARY_AGENT_MODEL}"
            )

    def _compress_context(
        self, history: Sequence[Mapping[str, Any]], *_args: Any, **_kwargs: Any
    ) -> tuple[list[dict[str, Any]], None]:
        del history
        raise RuntimeError(
            "manual UI compression must use native SDK compact_session; generic "
            "history compression cannot rewrite its durable session"
        )


def build_primary_agent_facade(
    *,
    runtime: dict[str, Any] | None = None,
    model: str = "",
    operator_user_id: str | None = None,
    business_slug: str | None = None,
    workspace_root: str | None = None,
    agent_kwargs: dict[str, Any] | None = None,
) -> PrimaryAgentFacade:
    del runtime, model
    return PrimaryAgentFacade(
        operator_user_id=str(operator_user_id or ""),
        business_slug=str(business_slug or ""),
        workspace_root=str(workspace_root or ""),
        agent_kwargs=agent_kwargs,
    )


def build_operator_gateway_agent(**kwargs: Any) -> PrimaryAgentFacade:
    """Compatibility alias; live callers receive the primary SDK facade."""

    return build_primary_agent_facade(**kwargs)


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
    # Per-request wire clients (``interruptible_api_call``) are closed after
    # every request, and the OpenAI SDK ``close()`` also closes whatever
    # ``http_client`` it wraps — including the shared gateway transport stored
    # in ``_client_kwargs`` above. Publish a factory so the agent mints a
    # FRESH gateway transport per request client / primary rebuild instead of
    # wrapping (and then closing) the primary client's transport. Without
    # this, the first ``request_complete`` close kills the shared transport
    # and every later call dies with APIConnectionError (#10933 class).
    agent._request_http_client_factory = (
        lambda: build_operator_gateway_http_client(context)
    )
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
    contract as the primary SDK's ``core._claude_agent_broker_lockdown_enabled``)."""
    from plugins.takyon import core as takyon_core

    return bool(takyon_core._claude_agent_broker_lockdown_enabled())


def _operator_anthropic_broker_url() -> str:
    """Safebox proxy root for host-side operator gateway calls.

    A sandboxed SDK process may need a Docker-reachable URL such as ``host.docker.internal``, while the
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


def _resolve_openai_broker_runtime(
    context: OperatorGatewayContext,
    body: dict[str, Any],
) -> dict[str, Any]:
    """The OpenAI (codex_responses) mirror of ``_resolve_anthropic_broker_runtime``: point upstream at
    ``<safebox>/v1`` (the gateway appends ``/responses``, hitting the safebox's stock-path proxy) and
    authenticate with a minted ``operator.session`` token. NO raw OpenAI key is ever resolved on this
    plane — the safebox injects it server-side and meters reserve/settle against the real operator.
    Fails CLOSED, same contract as the anthropic lane (collaborator machines have no local key at all:
    turputaru/climuru 401'd with the placeholder key on Sai's machine, 2026-07-08)."""
    from plugins.takyon import safebox

    broker_base_url = _operator_anthropic_broker_url()
    if not broker_base_url:
        raise RuntimeError(
            "operator broker lockdown is on but no safebox proxy URL is configured "
            f"(set {_OPERATOR_GATEWAY_BROKER_URL_ENV}, TAKYON_CLAUDE_AGENT_BROKER_URL, or "
            "TAKYON_SAFEBOX_URL to the safebox ROOT)"
        )
    business_slug = str(context.business_slug or "").strip()
    operator_user_id = _resolve_operator_owner_user_id(context)
    if not operator_user_id:
        raise RuntimeError(
            "operator broker lockdown requires a resolved operator owner to mint an "
            "operator.session token; it is missing for this CEO turn"
        )
    session_token = str(
        safebox.mint_operator_session_token(business_slug, operator_user_id) or ""
    ).strip()
    if not session_token:
        raise RuntimeError(
            "the safebox refused to mint an operator.session token for this CEO turn (the operator "
            "does not own the business / root scope session, or /v1/operator/session-token is "
            "unavailable)"
        )
    return {
        "provider": context.provider or "custom",
        "requested_provider": context.requested_provider or context.provider or "custom",
        "api_mode": "codex_responses",
        # _upstream_url appends "/responses" for the codex path, so the base is <safebox>/v1 and the
        # request lands on the safebox's stock-path /v1/responses proxy route.
        "base_url": f"{broker_base_url}/v1",
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
    pinned_model = str(context.model or "").strip()
    requested_model = str(body.get("model") or "").strip()
    if not pinned_model:
        raise RuntimeError("operator gateway context has no model pin")
    if not requested_model:
        raise RuntimeError("operator gateway request omitted its pinned model")
    if requested_model != pinned_model:
        raise RuntimeError(
            "operator gateway model switch refused: "
            f"requested {requested_model!r}, pinned {pinned_model!r}"
        )
    # Anthropic AND OpenAI CEO turns route THROUGH the safebox proxy (key-free, operator.session)
    # under broker lockdown — the raw provider key is never resolved on this plane. chat_completions
    # remains local-resolve (no safebox proxy route yet).
    mode = str(context.api_mode or "").strip().lower()
    if mode == "anthropic_messages" and _operator_anthropic_broker_lockdown():
        return _resolve_anthropic_broker_runtime(context, body)
    if mode == "codex_responses" and _operator_anthropic_broker_lockdown():
        return _resolve_openai_broker_runtime(context, body)

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

    base_url = str(runtime.get("base_url") or "")
    provider = str(runtime.get("provider") or "").strip().lower()
    api_key = str(runtime.get("api_key") or "").strip()
    headers: dict[str, str] = {"Authorization": f"Bearer {api_key}"}

    if base_url_host_matches(base_url, "openrouter.ai"):
        headers.update(build_or_headers())
    elif base_url_host_matches(base_url, "integrate.api.nvidia.com"):
        headers.update(build_nvidia_nim_headers(base_url))
    elif base_url_host_matches(base_url, "api.routermint.com"):
        from takyon_cli import __version__ as takyon_version

        headers["User-Agent"] = f"TakyonAgent/{takyon_version}"
    elif base_url_host_matches(base_url, "api.githubcopilot.com"):
        headers.update(copilot_default_headers())
    elif base_url_host_matches(base_url, "api.kimi.com"):
        headers["User-Agent"] = "claude-code/0.1.0"
    elif base_url_host_matches(base_url, "portal.qwen.ai"):
        import platform

        user_agent = f"QwenCode/0.14.1 ({platform.system().lower()}; {platform.machine()})"
        headers.update(
            {
                "User-Agent": user_agent,
                "X-DashScope-CacheControl": "enable",
                "X-DashScope-UserAgent": user_agent,
                "X-DashScope-AuthType": "qwen-oauth",
            }
        )
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
