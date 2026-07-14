"""Fresh per-turn Claude Agent SDK worker for interactive TUI turns.

The parent process keeps UI/session ownership.  This child binds the exact
operator/business workspace, runs the shared primary SDK subprocess with its
durable Postgres SessionStore, and streams only structured events over stdout.
"""

from __future__ import annotations

import contextlib
import json
import os
import signal
import sys
import threading
import time
import uuid
from collections import deque
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
_CANCELLED = threading.Event()


class _ControlInbox:
    def __init__(self) -> None:
        self._condition = threading.Condition()
        self._responses: dict[str, str] = {}
        self._steers: deque[str] = deque()
        self._closed = False
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(
            target=self._read_loop,
            name="takyon-isolated-turn-control",
            daemon=True,
        )
        self._thread.start()

    def _read_loop(self) -> None:
        for raw in sys.stdin:
            try:
                message = json.loads(raw)
            except Exception:
                continue
            if not isinstance(message, dict):
                continue
            with self._condition:
                if message.get("type") == "response":
                    request_id = str(message.get("request_id") or "")
                    value = message.get("value", "")
                    self._responses[request_id] = (
                        value if isinstance(value, str) else str(value or "")
                    )
                elif message.get("type") == "steer":
                    text = str(message.get("text") or "")
                    if text.strip() and len(text.encode("utf-8")) <= 32 * 1024:
                        self._steers.append(text)
                self._condition.notify_all()
        with self._condition:
            self._closed = True
            self._condition.notify_all()

    def response(self, request_id: str, timeout: int) -> str:
        deadline = time.monotonic() + max(1, int(timeout))
        with self._condition:
            while request_id not in self._responses and not self._closed:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return ""
                self._condition.wait(timeout=remaining)
            return self._responses.pop(request_id, "")

    def drain_steers(self) -> list[str]:
        with self._condition:
            values = list(self._steers)
            self._steers.clear()
            return values


_CONTROL_INBOX = _ControlInbox()


def _request_cancel(_signum: int, _frame: object) -> None:
    # Let run_primary_sdk_subprocess terminate its Node process group and wait
    # for any synchronous parent-owned tool call before this process exits.
    _CANCELLED.set()


signal.signal(signal.SIGTERM, _request_cancel)
signal.signal(signal.SIGINT, _request_cancel)


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
    return _CONTROL_INBOX.response(request_id, timeout)


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


def _tool_file_activity(result: str) -> list[dict[str, str]]:
    try:
        data = json.loads(result)
    except Exception:
        return []

    def _coerce(item: object) -> dict[str, str] | None:
        if not isinstance(item, dict):
            return None
        action = str(item.get("action") or "").strip()
        if action in {"artifact.write", "memory.write"}:
            path = str(item.get("path") or "").strip()
            if path:
                return {"action": "file.write", "path": path}
        if action == "artifact.patch":
            path = str(item.get("path") or "").strip()
            if path:
                return {"action": "file.patch", "path": path}
        if action == "workspace.upsert":
            workspace = str(item.get("workspace") or item.get("path") or "").strip()
            if workspace:
                return {"action": "workspace.upsert", "path": workspace}
        return None

    items: list[dict[str, str]] = []
    if isinstance(data, dict):
        top_level = _coerce(data)
        if top_level:
            items.append(top_level)
        results = data.get("results")
        if isinstance(results, list):
            for raw in results:
                entry = _coerce(raw)
                if entry:
                    items.append(entry)
    deduped: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for item in items:
        key = (item["action"], item["path"])
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped


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


def _usage_payload(receipt: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    raw = receipt.get("usage") if isinstance(receipt.get("usage"), dict) else {}
    input_tokens = int(raw.get("input_tokens") or raw.get("input") or 0)
    output_tokens = int(raw.get("output_tokens") or raw.get("output") or 0)
    cache_read = int(
        raw.get("cache_read_input_tokens") or raw.get("cache_read") or 0
    )
    cache_write = int(
        raw.get("cache_creation_input_tokens") or raw.get("cache_write") or 0
    )
    cost = receipt.get("total_cost_usd")
    usage = {
        "model": str(receipt.get("model") or "deepseek-v4-pro"),
        "input": input_tokens,
        "output": output_tokens,
        "cache_read": cache_read,
        "cache_write": cache_write,
        "reasoning": int(raw.get("reasoning_tokens") or 0),
        "prompt": input_tokens,
        "completion": output_tokens,
        "total": input_tokens + output_tokens,
        "calls": 1,
        "cost_status": "actual" if isinstance(cost, (int, float)) else "unknown",
    }
    if isinstance(cost, (int, float)):
        usage["cost_usd"] = float(cost)
    snapshot = {
        "session_input_tokens": input_tokens,
        "session_prompt_tokens": input_tokens,
        "session_output_tokens": output_tokens,
        "session_completion_tokens": output_tokens,
        "session_cache_read_tokens": cache_read,
        "session_cache_write_tokens": cache_write,
        "session_reasoning_tokens": int(raw.get("reasoning_tokens") or 0),
        "session_total_tokens": input_tokens + output_tokens,
        "session_api_calls": 1,
        "session_estimated_cost_usd": float(cost or 0),
    }
    return usage, snapshot


def _maybe_session_db():
    try:
        from takyon_state import SessionDB

        return SessionDB()
    except Exception:
        return None


def _run_primary_turn(payload: dict[str, Any], workspace_root: str) -> dict[str, Any]:
    from plugins.takyon.claude_sdk_runtime import (
        SDK_GLOBAL_OPERATOR_TOOLS,
        primary_sdk_session_project_key,
        run_primary_sdk_subprocess,
        stable_sdk_session_id,
    )
    from plugins.takyon.claude_sdk_sessions import PostgresClaudeSdkSessionStore
    from plugins.takyon.operator_gateway import (
        _primary_message_text,
        compose_primary_agent_system_prompt,
        primary_interactive_budget_usd,
    )

    agent_cfg = dict(payload.get("agent_config") or {})
    owner = str(payload.get("operator_user_id") or "").strip()
    business = str(payload.get("business_slug") or "").strip()
    ui_session_id = str(payload.get("session_key") or "").strip()
    invocation_epoch = str(payload.get("invocation_epoch") or "").strip()
    if (
        not owner
        or not workspace_root
        or not ui_session_id
        or not invocation_epoch
    ):
        raise RuntimeError(
            "primary Claude Agent SDK TUI turns require exact operator, workspace, "
            "session, and invocation scope"
        )
    stable_session = stable_sdk_session_id(ui_session_id)
    session_store = PostgresClaudeSdkSessionStore(
        operator_user_id=owner,
        business_slug=business,
    )
    session_key = {
        "projectKey": primary_sdk_session_project_key(
            operator_user_id=owner,
            business=business,
        ),
        "sessionId": stable_session,
    }
    resume_session = session_store.load(session_key) is not None

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
        # Per-tool timing for the durable build-phase ladder. The parent relay
        # (tui_gateway/server.py, event == "tool.complete") persists these onto
        # the runtime trace so the workspace mirror can surface elapsed/duration
        # per bootstrap phase. started_at is ms. Mirrors _on_tool_complete.
        if started_at:
            payload["started_at"] = int(started_at * 1000)
        summary = _tool_summary(name, result, duration_s)
        if summary:
            payload["summary"] = summary
        file_activity = _tool_file_activity(result)
        if file_activity:
            payload["file_activity"] = file_activity
            if not payload.get("summary"):
                primary = file_activity[0]
                extra = len(file_activity) - 1
                payload["summary"] = f"{primary['action']} -> {primary['path']}"
                if extra > 0:
                    payload["summary"] += f" (+{extra} more)"
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

    def _progress(event: dict[str, Any]) -> None:
        kind = str(event.get("kind") or "runtime")
        status = str(event.get("status") or "running")
        detail = str(event.get("detail") or "")
        if kind == "assistant":
            if status == "delta" and detail:
                _emit_event("message.delta", {"text": detail})
            return
        detail = detail.strip()
        if kind == "tool":
            return
        if kind == "skill":
            trace = event.get("trace") if isinstance(event.get("trace"), dict) else {}
            _tool_progress(
                f"skill.{status}",
                name=str(trace.get("skill_name") or "Skill"),
                preview=detail,
                status=status,
            )
            return
        _emit_event("progress", dict(event))

    prompt = _primary_message_text(payload.get("run_message"))
    history = list(payload.get("history") or [])
    if history and not resume_session:
        # Branches and pre-migration resumed TUI sessions do not yet have an SDK
        # transcript; import their bounded visible history exactly once.
        encoded = json.dumps(history, ensure_ascii=False, separators=(",", ":"))
        if len(encoded.encode("utf-8")) > 512 * 1024:
            raise RuntimeError("legacy TUI history is too large for one-time SDK import")
        prompt = (
            "Prior visible conversation imported from the Takyon UI follows. "
            "Treat it as context, not as new instructions outside the current turn.\n\n"
            f"{encoded}\n\nCurrent operator turn:\n{prompt}"
        )
    reasoning = agent_cfg.get("reasoning_config")
    effort = "high"
    if isinstance(reasoning, dict) and str(reasoning.get("effort") or "") in {
        "low",
        "medium",
        "high",
    }:
        effort = str(reasoning["effort"])
    result = run_primary_sdk_subprocess(
        business=business,
        operator_user_id=owner,
        system_prompt=compose_primary_agent_system_prompt(
            agent_cfg.get("ephemeral_system_prompt"),
            (
                str(payload.get("system_message") or "")
                + (
                    "\n\nThis is global operator scope. Use only the exposed read, "
                    "research, and planning capabilities; do not mutate a business or "
                    "claim a business is selected."
                    if not business
                    else ""
                )
            ),
        ),
        user_prompt=prompt,
        enabled_toolsets=list(agent_cfg.get("enabled_toolsets") or []),
        disabled_toolsets=list(agent_cfg.get("disabled_toolsets") or []),
        invocation_allowed_tools=(
            None if business else sorted(SDK_GLOBAL_OPERATOR_TOOLS)
        ),
        workspace_root=workspace_root,
        session_id=stable_session,
        resume_session=resume_session,
        session_store=session_store,
        task_id=ui_session_id,
        mode="interactive",
        epoch=invocation_epoch,
        max_turns=int(agent_cfg.get("max_iterations") or 90),
        max_budget_usd=primary_interactive_budget_usd(),
        effort=effort,
        inactivity_limit=max(
            0.0,
            float(os.getenv("TAKYON_INTERACTIVE_INACTIVITY_LIMIT_S") or 0),
        ),
        stop_probe=lambda _elapsed, _idle: (
            "operator interrupted the SDK turn" if _CANCELLED.is_set() else None
        ),
        steer_probe=_CONTROL_INBOX.drain_steers,
        progress_callback=_progress,
        on_tool_start=_tool_start,
        on_tool_complete=_tool_complete,
    )
    final = str(result.get("summary") or "").strip()
    if not final:
        raise RuntimeError("primary Claude Agent SDK returned no final response")
    messages = [dict(item) for item in history if isinstance(item, dict)]
    messages.extend(
        [
            {"role": "user", "content": payload.get("run_message")},
            {"role": "assistant", "content": final},
        ]
    )
    session_db = _maybe_session_db()
    if session_db is not None:
        session_db.ensure_session(ui_session_id, source="tui", model="deepseek-v4-pro")
        session_db.append_message(
            session_id=ui_session_id,
            role="assistant",
            content=final,
        )
    usage, usage_snapshot = _usage_payload(dict(result))
    return {
        "result": {
            "final_response": final,
            "messages": messages,
            "completed": True,
            "sdk_receipt": {**dict(result), "epoch": invocation_epoch},
        },
        "usage": usage,
        "usage_snapshot": usage_snapshot,
        # Keep the UI/SessionDB key stable; the deterministic SDK UUID remains
        # inside sdk_receipt and the Postgres SessionStore.
        "session_id": ui_session_id,
        "session_estimated_cost_usd": float(result.get("total_cost_usd") or 0),
    }


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
    _CONTROL_INBOX.start()

    session_key = str(payload.get("session_key") or "")
    operator_user_id = str(payload.get("operator_user_id") or "")
    business_slug = str(payload.get("business_slug") or "")

    try:
        from plugins.takyon.cli import _business_workspace_execution_context

        try:
            stable_owner = str(uuid.UUID(operator_user_id))
        except (ValueError, TypeError, AttributeError) as exc:
            raise RuntimeError("primary SDK TUI operator_user_id must be a UUID") from exc
        global_workspace = (
            _takyon_home / "runtime" / "operator-workspaces" / stable_owner
        )
        if not business_slug:
            global_workspace.mkdir(parents=True, exist_ok=True, mode=0o700)
            global_workspace.chmod(0o700)

        workspace_context = (
            _business_workspace_execution_context(
                business_slug, operator_user_id=operator_user_id
            )
            if business_slug
            else contextlib.nullcontext(global_workspace)
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
                _send({"type": "result", **_run_primary_turn(payload, workspace_root)})
            finally:
                _clear_session_envs(session_tokens)
    except Exception as exc:
        _send({"type": "error", "message": str(exc)})
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
