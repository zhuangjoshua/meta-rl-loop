"""Fresh per-turn worker for interactive TUI operator turns.

The parent process keeps session ownership, budgets, and transcript writes.
This child only executes ``agent.run_conversation(...)`` in a fresh process,
streaming structured events back over stdout as JSON lines.
"""

from __future__ import annotations

import contextlib
import json
import os
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from takyon_constants import get_takyon_home
from takyon_cli.env_loader import load_takyon_dotenv

_takyon_home = get_takyon_home()
load_takyon_dotenv(
    takyon_home=_takyon_home, project_env=Path(__file__).parent.parent / ".env"
)

_PROTOCOL_STDOUT = sys.stdout
sys.stdout = sys.stderr
_WRITE_LOCK = threading.Lock()


def _send(obj: dict[str, Any]) -> None:
    line = json.dumps(obj, ensure_ascii=False)
    with _WRITE_LOCK:
        _PROTOCOL_STDOUT.write(line + "\n")
        _PROTOCOL_STDOUT.flush()


def _request(event: str, payload: dict[str, Any], timeout: int = 300) -> str:
    request_id = uuid.uuid4().hex[:8]
    _send(
        {
            "type": "request",
            "event": event,
            "request_id": request_id,
            "payload": payload,
            "timeout": timeout,
        }
    )
    while True:
        raw = sys.stdin.readline()
        if not raw:
            return ""
        try:
            msg = json.loads(raw)
        except Exception:
            continue
        if (
            isinstance(msg, dict)
            and msg.get("type") == "response"
            and msg.get("request_id") == request_id
        ):
            value = msg.get("value", "")
            return value if isinstance(value, str) else str(value or "")


def _emit_event(event: str, payload: dict[str, Any]) -> None:
    _send({"type": "event", "event": event, "payload": payload})


def _tool_ctx(name: str, args: dict[str, Any]) -> str:
    try:
        from agent.display import build_tool_preview

        return build_tool_preview(name, args, max_len=80) or ""
    except Exception:
        return ""


def _fmt_tool_duration(seconds: float | None) -> str:
    if seconds is None:
        return ""
    if seconds < 10:
        return f"{seconds:.1f}s"
    if seconds < 60:
        return f"{round(seconds)}s"
    mins, secs = divmod(int(round(seconds)), 60)
    return f"{mins}m {secs}s" if secs else f"{mins}m"


def _count_list(obj: object, *path: str) -> int | None:
    cur = obj
    for key in path:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(key)
    return len(cur) if isinstance(cur, list) else None


def _tool_summary(name: str, result: str, duration_s: float | None) -> str | None:
    try:
        data = json.loads(result)
    except Exception:
        data = None

    dur = _fmt_tool_duration(duration_s)
    suffix = f" in {dur}" if dur else ""
    text = None

    if name == "web_search" and isinstance(data, dict):
        n = _count_list(data, "data", "web")
        if n is not None:
            text = f"Did {n} {'search' if n == 1 else 'searches'}"
    elif name == "web_extract" and isinstance(data, dict):
        n = _count_list(data, "results") or _count_list(data, "data", "results")
        if n is not None:
            text = f"Extracted {n} {'page' if n == 1 else 'pages'}"

    if isinstance(data, dict) and data.get("fallback_warning"):
        warning = str(data.get("fallback_warning") or "").strip()
        if warning:
            return f"{warning}{suffix}"

    return f"{text}{suffix}" if text else None


def _usage_snapshot(agent) -> dict[str, Any]:
    keys = [
        "session_input_tokens",
        "session_prompt_tokens",
        "session_output_tokens",
        "session_completion_tokens",
        "session_cache_read_tokens",
        "session_cache_write_tokens",
        "session_reasoning_tokens",
        "session_total_tokens",
        "session_api_calls",
        "session_estimated_cost_usd",
    ]
    return {key: getattr(agent, key, 0) for key in keys}


def _usage_payload(agent) -> dict[str, Any]:
    g = lambda k, fb=None: getattr(agent, k, 0) or (getattr(agent, fb, 0) if fb else 0)
    usage = {
        "model": getattr(agent, "model", "") or "",
        "input": g("session_input_tokens", "session_prompt_tokens"),
        "output": g("session_output_tokens", "session_completion_tokens"),
        "cache_read": g("session_cache_read_tokens"),
        "cache_write": g("session_cache_write_tokens"),
        "reasoning": g("session_reasoning_tokens"),
        "prompt": g("session_prompt_tokens"),
        "completion": g("session_completion_tokens"),
        "total": g("session_total_tokens"),
        "calls": g("session_api_calls"),
    }
    comp = getattr(agent, "context_compressor", None)
    if comp:
        ctx_used = getattr(comp, "last_prompt_tokens", 0) or usage["total"] or 0
        ctx_max = getattr(comp, "context_length", 0) or 0
        if ctx_max:
            usage["context_used"] = ctx_used
            usage["context_max"] = ctx_max
            usage["context_percent"] = max(0, min(100, round(ctx_used / ctx_max * 100)))
        usage["compressions"] = getattr(comp, "compression_count", 0) or 0
    try:
        from agent.usage_pricing import CanonicalUsage, estimate_usage_cost

        cost = estimate_usage_cost(
            usage["model"],
            CanonicalUsage(
                input_tokens=usage["input"],
                output_tokens=usage["output"],
                cache_read_tokens=usage["cache_read"],
                cache_write_tokens=usage["cache_write"],
            ),
            provider=getattr(agent, "provider", None),
            base_url=getattr(agent, "base_url", None),
        )
        usage["cost_status"] = cost.status
        if cost.amount_usd is not None:
            usage["cost_usd"] = float(cost.amount_usd)
    except Exception:
        pass
    return usage


def _maybe_session_db():
    try:
        from takyon_state import SessionDB

        return SessionDB()
    except Exception:
        return None


def _build_agent(payload: dict[str, Any], workspace_root: str):
    from plugins.takyon.operator_gateway import build_operator_gateway_agent

    runtime = dict(payload.get("runtime") or {})
    agent_cfg = dict(payload.get("agent_config") or {})
    agent_kwargs = {
        "max_iterations": int(agent_cfg.get("max_iterations") or 90),
        "quiet_mode": True,
        "verbose_logging": bool(agent_cfg.get("verbose_logging")),
        "reasoning_config": agent_cfg.get("reasoning_config"),
        "service_tier": agent_cfg.get("service_tier"),
        "enabled_toolsets": list(agent_cfg.get("enabled_toolsets") or []),
        "disabled_toolsets": list(agent_cfg.get("disabled_toolsets") or []),
        "platform": "tui",
        "session_id": str(payload.get("session_key") or ""),
        "session_db": _maybe_session_db(),
        "ephemeral_system_prompt": agent_cfg.get("ephemeral_system_prompt") or None,
        "providers_allowed": agent_cfg.get("providers_allowed"),
        "providers_ignored": agent_cfg.get("providers_ignored"),
        "providers_order": agent_cfg.get("providers_order"),
        "provider_sort": agent_cfg.get("provider_sort"),
        "provider_require_parameters": bool(
            agent_cfg.get("provider_require_parameters")
        ),
        "provider_data_collection": agent_cfg.get("provider_data_collection"),
        "openrouter_min_coding_score": agent_cfg.get("openrouter_min_coding_score"),
        "request_overrides": dict(agent_cfg.get("request_overrides") or {}),
        "fallback_model": agent_cfg.get("fallback_model"),
        "checkpoints_enabled": bool(agent_cfg.get("checkpoints_enabled")),
        "pass_session_id": bool(agent_cfg.get("pass_session_id")),
        "skip_context_files": bool(agent_cfg.get("skip_context_files")),
        "load_soul_identity": bool(agent_cfg.get("load_soul_identity")),
        "skip_memory": bool(agent_cfg.get("skip_memory")),
    }

    tool_started_at: dict[str, float] = {}
    edit_snapshots: dict[str, Any] = {}

    def _tool_start(tool_call_id: str, name: str, args: dict[str, Any]) -> None:
        try:
            from agent.display import capture_local_edit_snapshot

            snapshot = capture_local_edit_snapshot(name, args)
            if snapshot is not None:
                edit_snapshots[tool_call_id] = snapshot
        except Exception:
            pass
        tool_started_at[tool_call_id] = time.time()
        _emit_event(
            "tool.start",
            {"tool_id": tool_call_id, "name": name, "context": _tool_ctx(name, args)},
        )

    def _tool_complete(
        tool_call_id: str,
        name: str,
        args: dict[str, Any],
        result: str,
    ) -> None:
        payload: dict[str, Any] = {"tool_id": tool_call_id, "name": name}
        started_at = tool_started_at.pop(tool_call_id, None)
        snapshot = edit_snapshots.pop(tool_call_id, None)
        duration_s = time.time() - started_at if started_at else None
        if duration_s is not None:
            payload["duration_s"] = duration_s
        summary = _tool_summary(name, result, duration_s)
        if summary:
            payload["summary"] = summary
        if name == "todo":
            try:
                data = json.loads(result)
                if isinstance(data, dict) and isinstance(data.get("todos"), list):
                    payload["todos"] = data.get("todos")
            except Exception:
                pass
        try:
            from agent.display import render_edit_diff_with_delta

            rendered: list[str] = []
            if render_edit_diff_with_delta(
                name,
                result,
                function_args=args,
                snapshot=snapshot,
                print_fn=rendered.append,
            ):
                payload["inline_diff"] = "\n".join(rendered)
        except Exception:
            pass
        _emit_event("tool.complete", payload)

    def _tool_progress(
        event_type: str,
        name: str | None = None,
        preview: str | None = None,
        args: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> None:
        payload: dict[str, Any] = {}
        if name:
            payload["name"] = str(name)
        if preview:
            payload["preview"] = str(preview)
        if args:
            payload["args"] = args
        for key in (
            "goal",
            "task_count",
            "task_index",
            "subagent_id",
            "parent_id",
            "depth",
            "model",
            "tool_count",
            "toolsets",
            "status",
            "summary",
            "duration_seconds",
            "input_tokens",
            "output_tokens",
            "reasoning_tokens",
            "api_calls",
            "cost_usd",
            "files_read",
            "files_written",
            "output_tail",
        ):
            if kwargs.get(key) is not None:
                payload[key] = kwargs.get(key)
        _emit_event("tool.progress", {"event_type": event_type, **payload})

    agent_kwargs.update(
        {
            "tool_start_callback": _tool_start,
            "tool_complete_callback": _tool_complete,
            "tool_progress_callback": _tool_progress,
            "tool_gen_callback": lambda name: _emit_event(
                "tool.generating", {"name": name}
            ),
            "thinking_callback": lambda text: _emit_event(
                "thinking.delta", {"text": text}
            ),
            "reasoning_callback": lambda text: _emit_event(
                "reasoning.delta", {"text": text}
            ),
            "status_callback": lambda kind, text=None: _emit_event(
                "status.update",
                {"kind": str(kind), "text": None if text is None else str(text)},
            ),
            "clarify_callback": lambda q, c: _request(
                "clarify.request", {"question": q, "choices": c}
            ),
        }
    )

    agent = build_operator_gateway_agent(
        runtime=runtime,
        model=str(payload.get("model") or ""),
        operator_user_id=str(payload.get("operator_user_id") or ""),
        business_slug=str(payload.get("business_slug") or ""),
        workspace_root=workspace_root,
        agent_kwargs=agent_kwargs,
    )
    try:
        agent.background_review_callback = lambda message: _emit_event(
            "review.summary", {"text": str(message)}
        )
    except Exception:
        pass
    return agent


def _bind_prompt_callbacks() -> None:
    from tools.skills_tool import set_secret_capture_callback
    from tools.terminal_tool import set_sudo_password_callback

    set_sudo_password_callback(lambda: _request("sudo.request", {}, timeout=120))

    def secret_cb(env_var: str, prompt: str, metadata: dict[str, Any] | None = None):
        payload = {"prompt": prompt, "env_var": env_var}
        if metadata:
            payload["metadata"] = metadata
        value = _request("secret.request", payload)
        if not value:
            return {
                "success": True,
                "stored_as": env_var,
                "validated": False,
                "skipped": True,
                "message": "skipped",
            }
        from takyon_cli.config import save_env_value_secure

        return {
            **save_env_value_secure(env_var, value),
            "skipped": False,
            "message": "ok",
        }

    set_secret_capture_callback(secret_cb)


def _set_session_envs(
    *,
    session_key: str,
    operator_user_id: str,
    workspace_root: str,
    business_slug: str,
) -> list[Any]:
    from gateway.session_context import set_session_vars
    from tools.approval import set_current_session_key

    os.environ["TAKYON_GATEWAY_SESSION"] = "1"
    os.environ["TAKYON_EXEC_ASK"] = "1"
    os.environ["TAKYON_INTERACTIVE"] = "1"
    os.environ["TAKYON_SESSION_KEY"] = session_key or ""
    os.environ["TAKYON_SESSION_USER_ID"] = operator_user_id or ""
    os.environ["TAKYON_SESSION_WORKSPACE_ROOT"] = workspace_root or ""
    os.environ["TAKYON_SESSION_BUSINESS_SLUG"] = business_slug or ""
    tokens = set_session_vars(
        session_key=session_key or "",
        user_id=operator_user_id or "",
        workspace_root=workspace_root or "",
        business_slug=business_slug or "",
    )
    approval_token = set_current_session_key(session_key or "")
    return [tokens, approval_token]


def _clear_session_envs(tokens: list[Any]) -> None:
    from gateway.session_context import clear_session_vars
    from tools.approval import reset_current_session_key

    session_tokens, approval_token = tokens
    clear_session_vars(session_tokens)
    reset_current_session_key(approval_token)


def main() -> int:
    raw = sys.stdin.readline()
    if not raw:
        return 2
    try:
        payload = json.loads(raw)
    except Exception as exc:
        _send({"type": "error", "message": f"invalid payload: {exc}"})
        return 2

    session_key = str(payload.get("session_key") or "")
    operator_user_id = str(payload.get("operator_user_id") or "")
    business_slug = str(payload.get("business_slug") or "")

    try:
        from plugins.takyon.cli import _business_workspace_execution_context

        workspace_context = (
            _business_workspace_execution_context(
                business_slug, operator_user_id=operator_user_id
            )
            if business_slug
            else contextlib.nullcontext(None)
        )
        with workspace_context as workspace_home:
            workspace_root = str(workspace_home or "")
            session_tokens = _set_session_envs(
                session_key=session_key,
                operator_user_id=operator_user_id,
                workspace_root=workspace_root,
                business_slug=business_slug,
            )
            try:
                _bind_prompt_callbacks()
                agent = _build_agent(payload, workspace_root)

                def _stream(delta: str) -> None:
                    _emit_event("message.delta", {"text": delta})

                run_kwargs = {
                    "conversation_history": list(payload.get("history") or []),
                    "stream_callback": _stream,
                }
                system_message = payload.get("system_message") or None
                if system_message:
                    run_kwargs["system_message"] = system_message
                result = agent.run_conversation(
                    payload.get("run_message"),
                    **run_kwargs,
                )
                _send(
                    {
                        "type": "result",
                        "result": result,
                        "usage": _usage_payload(agent),
                        "usage_snapshot": _usage_snapshot(agent),
                        "session_id": getattr(agent, "session_id", "") or "",
                        "session_estimated_cost_usd": float(
                            getattr(agent, "session_estimated_cost_usd", 0.0) or 0.0
                        ),
                    }
                )
            finally:
                _clear_session_envs(session_tokens)
    except Exception as exc:
        _send({"type": "error", "message": str(exc)})
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
