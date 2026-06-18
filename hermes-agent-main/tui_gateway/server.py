import atexit
import base64
import concurrent.futures
import contextlib
import contextvars
import copy
import importlib
import json
import logging
import mimetypes
import os
import queue
import re
import shlex
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Optional
from urllib.parse import urlparse

from agent.skill_utils import parse_frontmatter
from takyon_constants import get_takyon_home
from takyon_cli.env_loader import load_takyon_dotenv
from utils import is_truthy_value
from tui_gateway.transport import (
    StdioTransport,
    Transport,
    bind_transport,
    current_transport,
    reset_transport,
)

logger = logging.getLogger(__name__)

_INSUFFICIENT_OPERATOR_BALANCE_CLS: Optional[type] = None


def _insufficient_operator_balance_cls() -> type:
    """Lazily resolve plugins.takyon.cli.InsufficientOperatorBalance (the §3 gap #2
    company-creation balance-block exception) for the create handler's ``except`` clause, without
    importing the heavy cli module at gateway load. Cached after first resolution; falls back to a
    never-matching sentinel only if cli is somehow unavailable so the generic handler still runs."""
    global _INSUFFICIENT_OPERATOR_BALANCE_CLS
    if _INSUFFICIENT_OPERATOR_BALANCE_CLS is None:
        try:
            from plugins.takyon.cli import InsufficientOperatorBalance

            _INSUFFICIENT_OPERATOR_BALANCE_CLS = InsufficientOperatorBalance
        except Exception:  # pragma: no cover - cli is always importable on the create path
            class _NeverInsufficientBalance(Exception):
                ...

            _INSUFFICIENT_OPERATOR_BALANCE_CLS = _NeverInsufficientBalance
    return _INSUFFICIENT_OPERATOR_BALANCE_CLS


_TAKYON_AGENT_TOOLSETS = ["takyon", "web", "skills", "todo"]
_TAKYON_DISABLED_TOOLSETS = [
    "browser",
    "code_execution",
    "cronjob",
    "file",
    "memory",
    "messaging",
    "session_search",
    "takyon-authority",
    "terminal",
]

_takyon_home = get_takyon_home()
load_takyon_dotenv(
    takyon_home=_takyon_home, project_env=Path(__file__).parent.parent / ".env"
)


# ── Panic logger ─────────────────────────────────────────────────────
# Gateway crashes in a TUI session leave no forensics: stdout is the
# JSON-RPC pipe (TUI side parses it, doesn't log raw), the root logger
# only catches handled warnings, and the subprocess exits before stderr
# flushes through the stderr->gateway.stderr event pump. This hook
# appends every unhandled exception to ~/.takyon/logs/tui_gateway_crash.log
# AND re-emits a one-line summary to stderr so the TUI can surface it in
# Activity — exactly what was missing when the voice-mode turns started
# exiting the gateway mid-TTS.
_CRASH_LOG = os.path.join(_takyon_home, "logs", "tui_gateway_crash.log")


def _panic_hook(exc_type, exc_value, exc_tb):
    import traceback

    trace = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
    try:
        os.makedirs(os.path.dirname(_CRASH_LOG), exist_ok=True)
        with open(_CRASH_LOG, "a", encoding="utf-8") as f:
            f.write(
                f"\n=== unhandled exception · {time.strftime('%Y-%m-%d %H:%M:%S')} ===\n"
            )
            f.write(trace)
    except Exception:
        pass
    # Stderr goes through to the TUI as a gateway.stderr Activity line —
    # the first line here is what the user will see without opening any
    # log files.  Rest of the stack is still in the log for full context.
    first = (
        str(exc_value).strip().splitlines()[0]
        if str(exc_value).strip()
        else exc_type.__name__
    )
    print(f"[gateway-crash] {exc_type.__name__}: {first}", file=sys.stderr, flush=True)
    # Chain to the default hook so the process still terminates normally.
    sys.__excepthook__(exc_type, exc_value, exc_tb)


sys.excepthook = _panic_hook


def _thread_panic_hook(args):
    # threading.excepthook signature: SimpleNamespace(exc_type, exc_value, exc_traceback, thread)
    import traceback

    trace = "".join(
        traceback.format_exception(args.exc_type, args.exc_value, args.exc_traceback)
    )
    try:
        os.makedirs(os.path.dirname(_CRASH_LOG), exist_ok=True)
        with open(_CRASH_LOG, "a", encoding="utf-8") as f:
            f.write(
                f"\n=== thread exception · {time.strftime('%Y-%m-%d %H:%M:%S')} "
                f"· thread={args.thread.name} ===\n"
            )
            f.write(trace)
    except Exception:
        pass
    first_line = (
        str(args.exc_value).strip().splitlines()[0]
        if str(args.exc_value).strip()
        else args.exc_type.__name__
    )
    print(
        f"[gateway-crash] thread {args.thread.name} raised {args.exc_type.__name__}: {first_line}",
        file=sys.stderr,
        flush=True,
    )


threading.excepthook = _thread_panic_hook

try:
    from takyon_cli.banner import prefetch_update_check

    prefetch_update_check()
except Exception:
    pass

from tui_gateway.render import make_stream_renderer, render_diff, render_message

_sessions: dict[str, dict] = {}
_methods: dict[str, callable] = {}
_pending: dict[str, tuple[str, threading.Event]] = {}
_answers: dict[str, str] = {}
_db = None
_db_error: str | None = None
_stdout_lock = threading.Lock()
_cfg_lock = threading.Lock()
_cfg_cache: dict | None = None
_cfg_mtime: float | None = None
_cfg_path = None
try:
    _slash_timeout = float(os.environ.get("TAKYON_TUI_SLASH_TIMEOUT_S") or "45")
except (ValueError, TypeError):
    _slash_timeout = 45.0
_SLASH_WORKER_TIMEOUT_S = max(5.0, _slash_timeout)
_DETAIL_SECTION_NAMES = ("thinking", "tools", "subagents", "activity")
_DETAIL_MODES = frozenset({"hidden", "collapsed", "expanded"})

# ── Async RPC dispatch (#12546) ──────────────────────────────────────
# A handful of handlers block the dispatcher loop in entry.py for seconds
# to minutes (slash.exec, cli.exec, shell.exec, session.resume,
# session.branch, session.compress, skills.manage).  While they're running, inbound RPCs —
# notably approval.respond and session.interrupt — sit unread in the
# stdin pipe.  We route only those slow handlers onto a small thread pool;
# everything else stays on the main thread so ordering stays sane for the
# fast path.  write_json is already _stdout_lock-guarded, so concurrent
# response writes are safe.
_LONG_HANDLERS = frozenset(
    {
        "browser.manage",
        "cli.exec",
        "session.branch",
        "session.compress",
        "session.resume",
        "shell.exec",
        "skills.manage",
        "slash.exec",
        "takyon.dashboard.create",
        "takyon.dashboard.state",
        "takyon.dashboard.workspace",
        "takyon.file.media",
        "takyon.file.read",
        "takyon.files.list",
        "takyon.outputs.list",
        "takyon.shell.exec",
        "takyon.site.preview",
        "takyon.scope.get",
    }
)

try:
    _rpc_pool_workers = max(
        2, int(os.environ.get("TAKYON_TUI_RPC_POOL_WORKERS") or "4")
    )
except (ValueError, TypeError):
    _rpc_pool_workers = 4
_pool = concurrent.futures.ThreadPoolExecutor(
    max_workers=_rpc_pool_workers,
    thread_name_prefix="tui-rpc",
)
atexit.register(lambda: _pool.shutdown(wait=False, cancel_futures=True))

_TAKYON_BACKGROUND_RUNS: dict[str, dict[str, Any]] = {}
_TAKYON_BACKGROUND_RUNS_LOCK = threading.Lock()

# Reserve real stdout for JSON-RPC only; redirect Python's stdout to stderr
# so stray print() from libraries/tools becomes harmless gateway.stderr instead
# of corrupting the JSON protocol.
_real_stdout = sys.stdout
sys.stdout = sys.stderr

# Module-level stdio transport — fallback sink when no transport is bound via
# contextvar or session. Stream resolved through a lambda so runtime monkey-
# patches of `_real_stdout` (used extensively in tests) still land correctly.
_stdio_transport = StdioTransport(lambda: _real_stdout, _stdout_lock)


class _SlashWorker:
    """Persistent TakyonCLI subprocess for slash commands."""

    def __init__(self, session_key: str, model: str, operator_user_id: str = ""):
        self._lock = threading.Lock()
        self._seq = 0
        self.stderr_tail: list[str] = []
        self.stdout_queue: queue.Queue[dict | None] = queue.Queue()

        argv = [
            sys.executable,
            "-m",
            "tui_gateway.slash_worker",
            "--session-key",
            session_key,
        ]
        if model:
            argv += ["--model", model]

        # Per-session identity propagation: the slash worker acts as THIS session's operator, so
        # it gets the session principal via TAKYON_SESSION_USER_ID (per-session env in a
        # per-session child process) — never the process-global operator var, which per-session
        # planes ignore (core.operator_identity_mode).
        env = os.environ.copy()
        principal = str(operator_user_id or "").strip()
        if principal:
            env["TAKYON_SESSION_USER_ID"] = principal

        self.proc = subprocess.Popen(
            argv,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            cwd=os.getcwd(),
            env=env,
        )
        threading.Thread(target=self._drain_stdout, daemon=True).start()
        threading.Thread(target=self._drain_stderr, daemon=True).start()

    def _drain_stdout(self):
        for line in self.proc.stdout or []:
            try:
                self.stdout_queue.put(json.loads(line))
            except json.JSONDecodeError:
                continue
        self.stdout_queue.put(None)

    def _drain_stderr(self):
        for line in self.proc.stderr or []:
            if text := line.rstrip("\n"):
                self.stderr_tail = (self.stderr_tail + [text])[-80:]

    def run(self, command: str) -> str:
        if self.proc.poll() is not None:
            raise RuntimeError("slash worker exited")

        with self._lock:
            self._seq += 1
            rid = self._seq
            self.proc.stdin.write(json.dumps({"id": rid, "command": command}) + "\n")
            self.proc.stdin.flush()

            while True:
                try:
                    msg = self.stdout_queue.get(timeout=_SLASH_WORKER_TIMEOUT_S)
                except queue.Empty:
                    raise RuntimeError("slash worker timed out")
                if msg is None:
                    break
                if msg.get("id") != rid:
                    continue
                if not msg.get("ok"):
                    raise RuntimeError(msg.get("error", "slash worker failed"))
                return str(msg.get("output", "")).rstrip()

            raise RuntimeError(
                f"slash worker closed pipe{': ' + chr(10).join(self.stderr_tail[-8:]) if self.stderr_tail else ''}"
            )

    def close(self):
        try:
            if self.proc.poll() is None:
                self.proc.terminate()
                self.proc.wait(timeout=1)
        except Exception:
            try:
                self.proc.kill()
            except Exception:
                pass


def _load_busy_input_mode() -> str:
    display = _load_cfg().get("display")
    if not isinstance(display, dict):
        display = {}
    raw = str(display.get("busy_input_mode", "") or "").strip().lower()
    return raw if raw in {"queue", "steer", "interrupt"} else "interrupt"


def _notify_session_boundary(event_type: str, session_id: str | None) -> None:
    """Fire session lifecycle hooks with CLI parity."""
    try:
        from takyon_cli.plugins import invoke_hook as _invoke_hook

        _invoke_hook(event_type, session_id=session_id, platform="tui")
    except Exception:
        pass


def _finalize_session(session: dict | None, end_reason: str = "tui_close") -> None:
    """Best-effort finalize hook + memory commit for a session."""
    if not session or session.get("_finalized"):
        return
    session["_finalized"] = True
    stop_event = session.get("_notif_stop")
    if stop_event is not None:
        stop_event.set()

    agent = session.get("agent")
    lock = session.get("history_lock")
    if lock is not None:
        with lock:
            history = list(session.get("history", []))
    else:
        history = list(session.get("history", []))
    if agent is not None and history and hasattr(agent, "commit_memory_session"):
        try:
            agent.commit_memory_session(history)
        except Exception:
            pass

    session_key = session.get("session_key")
    session_id = getattr(agent, "session_id", None) or session_key
    _notify_session_boundary("on_session_finalize", session_id)

    # Mark session ended in DB so it doesn't linger as a ghost row in /resume.
    # Use session_id (from agent.session_id) not session_key — after compression,
    # session_key may be stale (the ended parent) while session_id is the live
    # continuation. Fix for #20001.
    if session_id:
        try:
            db = _get_db()
            if db is not None:
                db.end_session(session_id, end_reason)
        except Exception:
            pass


def _shutdown_sessions() -> None:
    for session in list(_sessions.values()):
        _finalize_session(session, end_reason="tui_shutdown")
        try:
            _terminate_isolated_turn_proc(session.get("takyon_turn_proc"))
        except Exception:
            pass
        try:
            worker = session.get("slash_worker")
            if worker:
                worker.close()
        except Exception:
            pass


atexit.register(_shutdown_sessions)


# ── Plumbing ──────────────────────────────────────────────────────────


def _get_db():
    global _db, _db_error
    if _db is None:
        from takyon_state import SessionDB

        try:
            _db = SessionDB()
            _db_error = None
        except Exception as exc:
            _db_error = str(exc)
            logger.warning(
                "TUI session store unavailable — continuing without state.db features: %s",
                exc,
            )
            return None
    return _db


def _db_unavailable_error(rid, *, code: int):
    detail = _db_error or "state.db unavailable"
    return _err(rid, code, f"state.db unavailable: {detail}")


def write_json(obj: dict) -> bool:
    """Emit one JSON frame. Routes via the most-specific transport available.

    Precedence:

    1. Event frames with a session id → the transport stored on that session,
       so async events land with the client that owns the session even if
       the emitting thread has no contextvar binding.
    2. Otherwise the transport bound on the current context (set by
       :func:`dispatch` for the lifetime of a request).
    3. Otherwise the module-level stdio transport, matching the historical
       behaviour and keeping tests that monkey-patch ``_real_stdout`` green.
    """
    if obj.get("method") == "event":
        sid = ((obj.get("params") or {}).get("session_id")) or ""
        if sid and (t := (_sessions.get(sid) or {}).get("transport")) is not None:
            return t.write(obj)

    return (current_transport() or _stdio_transport).write(obj)


def _emit(event: str, sid: str, payload: dict | None = None):
    params = {"type": event, "session_id": sid}
    if payload is not None:
        params["payload"] = payload
    write_json({"jsonrpc": "2.0", "method": "event", "params": params})


def _status_update(sid: str, kind: str, text: str | None = None):
    body = (text if text is not None else kind).strip()
    if not body:
        return
    _emit(
        "status.update",
        sid,
        {"kind": kind if text is not None else "status", "text": body},
    )


_PROGRESS_PHASES = frozenset({
    "thinking", "planning", "editing", "running",
    "fixing", "finalizing", "done",
})


def _emit_progress(
    sid: str,
    phase: str,
    message: str,
    target: str | None = None,
    percent: int | None = None,
):
    if phase not in _PROGRESS_PHASES:
        phase = "running"
    payload: dict = {"phase": phase, "message": str(message).strip()}
    if target is not None:
        payload["target"] = str(target).strip()
    if percent is not None and isinstance(percent, (int, float)):
        payload["percent"] = max(0, min(100, int(percent)))
    _emit("progress", sid, payload)


def _estimate_image_tokens(width: int, height: int) -> int:
    """Very rough UI estimate for image prompt cost.

    Uses 512px tiles at ~85 tokens/tile as a lightweight cross-provider hint.
    This is intentionally approximate and only used for attachment display.
    """
    if width <= 0 or height <= 0:
        return 0
    return max(1, (width + 511) // 512) * max(1, (height + 511) // 512) * 85


def _image_meta(path: Path) -> dict:
    meta = {"name": path.name}
    try:
        from PIL import Image

        with Image.open(path) as img:
            width, height = img.size
        meta["width"] = int(width)
        meta["height"] = int(height)
        meta["token_estimate"] = _estimate_image_tokens(int(width), int(height))
    except Exception:
        pass
    return meta


def _ok(rid, result: dict) -> dict:
    return {"jsonrpc": "2.0", "id": rid, "result": result}


def _err(rid, code: int, msg: str) -> dict:
    return {"jsonrpc": "2.0", "id": rid, "error": {"code": code, "message": msg}}


def method(name: str):
    def dec(fn):
        _methods[name] = fn
        return fn

    return dec


def _normalize_request(req: Any) -> tuple[Any, str, dict] | dict:
    """Validate a JSON-RPC request enough for safe local dispatch."""
    if not isinstance(req, dict):
        return _err(None, -32600, "invalid request: expected an object")

    rid = req.get("id")
    method = req.get("method")
    if not isinstance(method, str) or not method:
        return _err(rid, -32600, "invalid request: method must be a non-empty string")

    params = req.get("params", {})
    if params is None:
        params = {}
    elif not isinstance(params, dict):
        return _err(rid, -32602, "invalid params: expected an object")

    return rid, method, params


def handle_request(req: dict) -> dict | None:
    normalized = _normalize_request(req)
    if isinstance(normalized, dict):
        return normalized

    rid, method, params = normalized
    fn = _methods.get(method)
    if not fn:
        return _err(rid, -32601, f"unknown method: {method}")
    return fn(rid, params)


def dispatch(req: dict, transport: Optional[Transport] = None) -> dict | None:
    """Route inbound RPCs — long handlers to the pool, everything else inline.

    Returns a response dict when handled inline. Returns None when the
    handler was scheduled on the pool; the worker writes its own response
    via the bound transport when done.

    *transport* (optional): pins every write produced by this request —
    including any events emitted by the handler — to the given transport.
    Omitting it falls back to the module-level stdio transport, preserving
    the original behaviour for ``tui_gateway.entry``.
    """
    t = transport or _stdio_transport
    token = bind_transport(t)
    try:
        normalized = _normalize_request(req)
        if isinstance(normalized, dict):
            return normalized

        _rid, method, _params = normalized
        if method not in _LONG_HANDLERS:
            return handle_request(req)

        # Snapshot the context so the pool worker sees the bound transport.
        ctx = contextvars.copy_context()

        def run():
            try:
                resp = handle_request(req)
            except Exception as exc:
                resp = _err(req.get("id"), -32000, f"handler error: {exc}")
            if resp is not None:
                t.write(resp)

        _pool.submit(lambda: ctx.run(run))

        return None
    finally:
        reset_transport(token)


def _wait_agent(session: dict, rid: str, timeout: float = 30.0) -> dict | None:
    ready = session.get("agent_ready")
    if ready is not None and not ready.wait(timeout=timeout):
        return _err(rid, 5032, "agent initialization timed out")
    err = session.get("agent_error")
    return _err(rid, 5032, err) if err else None


def _start_agent_build(sid: str, session: dict) -> None:
    """Start building the real AIAgent for a TUI session, once.

    Classic `takyon` shows the prompt before constructing AIAgent; the TUI used
    to eagerly build it during session.create, making startup feel blocked on
    tool discovery/model metadata even though the composer was visible.  Keep
    the shell responsive by deferring this work until the first prompt (or any
    command that actually needs the agent), while retaining the same ready/error
    event contract for the frontend.
    """
    ready = session.get("agent_ready")
    if ready is None:
        return
    lock = session.setdefault("agent_build_lock", threading.Lock())
    with lock:
        if ready.is_set() or session.get("agent_build_started"):
            return
        session["agent_build_started"] = True
    key = session["session_key"]

    def _build() -> None:
        current = _sessions.get(sid)
        if current is None:
            ready.set()
            return

        worker = None
        notify_registered = False
        try:
            # Bind the session principal during agent construction — set_session_vars with an
            # empty user_id would otherwise EXPLICITLY clear the contextvar and mask the
            # per-session identity this session resolved at create time.
            tokens = _set_session_context(
                key, operator_user_id=_takyon_operator_user_id(current)
            )
            try:
                agent = _make_agent(sid, key)
            finally:
                _clear_session_context(tokens)

            # Session DB row deferred to first run_conversation() call.
            # pending_title applied post-first-message (see cli.exec handler).
            current["agent"] = agent

            try:
                worker = _SlashWorker(
                    key,
                    getattr(agent, "model", _resolve_model()),
                    operator_user_id=_takyon_operator_user_id(current),
                )
                current["slash_worker"] = worker
            except Exception:
                pass

            try:
                from tools.approval import (
                    register_gateway_notify,
                    load_permanent_allowlist,
                )

                register_gateway_notify(
                    key, lambda data: _emit("approval.request", sid, data)
                )
                notify_registered = True
                load_permanent_allowlist()
            except Exception:
                pass

            _wire_callbacks(sid)
            _sessions[sid]["_notif_stop"] = _start_notification_poller(sid, _sessions[sid])
            _notify_session_boundary("on_session_reset", key)

            info = _session_info(agent)
            warn = _probe_credentials(agent)
            if warn:
                info["credential_warning"] = warn
            cfg_warn = _probe_config_health(_load_cfg())
            if cfg_warn:
                info["config_warning"] = cfg_warn
                logger.warning(cfg_warn)
            _emit("session.info", sid, info)
        except Exception as e:
            current["agent_error"] = str(e)
            _emit("error", sid, {"message": f"agent init failed: {e}"})
        finally:
            if _sessions.get(sid) is not current:
                if worker is not None:
                    try:
                        worker.close()
                    except Exception:
                        pass
                if notify_registered:
                    try:
                        from tools.approval import unregister_gateway_notify

                        unregister_gateway_notify(key)
                    except Exception:
                        pass
            ready.set()

    threading.Thread(target=_build, daemon=True).start()


def _sess_nowait(params, rid):
    s = _sessions.get(params.get("session_id") or "")
    if s:
        transport = current_transport()
        if transport is not None:
            s["transport"] = transport
    return (s, None) if s else (None, _err(rid, 4001, "session not found"))


def _sess(params, rid):
    s, err = _sess_nowait(params, rid)
    if err:
        return (None, err)
    _start_agent_build(params.get("session_id") or "", s)
    return (s, _wait_agent(s, rid))


def _normalize_completion_path(path_part: str) -> str:
    expanded = os.path.expanduser(path_part)
    if os.name != "nt":
        normalized = expanded.replace("\\", "/")
        if (
            len(normalized) >= 3
            and normalized[1] == ":"
            and normalized[2] == "/"
            and normalized[0].isalpha()
        ):
            return f"/mnt/{normalized[0].lower()}/{normalized[3:]}"
    return expanded


# ── Config I/O ────────────────────────────────────────────────────────


# Keep aligned with `INDICATOR_STYLES` / `DEFAULT_INDICATOR_STYLE` in
# ``ui-tui/src/app/interfaces.ts`` — both ends validate against the
# same shape so `config.get indicator` and the live TUI render agree.
_INDICATOR_STYLES: tuple[str, ...] = ("ascii", "emoji", "kaomoji", "unicode")
_INDICATOR_DEFAULT = "kaomoji"


def _load_cfg() -> dict:
    global _cfg_cache, _cfg_mtime, _cfg_path
    try:
        import yaml

        p = _takyon_home / "config.yaml"
        mtime = p.stat().st_mtime if p.exists() else None
        with _cfg_lock:
            if _cfg_cache is not None and _cfg_mtime == mtime and _cfg_path == p:
                return copy.deepcopy(_cfg_cache)
        if p.exists():
            with open(p, encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
        else:
            data = {}
        with _cfg_lock:
            _cfg_cache = copy.deepcopy(data)
            _cfg_mtime = mtime
            _cfg_path = p
        return data
    except Exception:
        pass
    return {}


def _save_cfg(cfg: dict):
    global _cfg_cache, _cfg_mtime, _cfg_path
    import yaml

    path = _takyon_home / "config.yaml"
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(cfg, f)
    with _cfg_lock:
        _cfg_cache = copy.deepcopy(cfg)
        _cfg_path = path
        try:
            _cfg_mtime = path.stat().st_mtime
        except Exception:
            _cfg_mtime = None


def _set_session_context(
    session_key: str,
    *,
    operator_user_id: str = "",
    workspace_root: str = "",
    business_slug: str = "",
) -> list:
    try:
        from gateway.session_context import set_session_vars

        return set_session_vars(
            session_key=session_key,
            user_id=operator_user_id or "",
            workspace_root=workspace_root or "",
            business_slug=business_slug or "",
        )
    except Exception:
        return []


def _clear_session_context(tokens: list) -> None:
    if not tokens:
        return
    try:
        from gateway.session_context import clear_session_vars

        clear_session_vars(tokens)
    except Exception:
        pass


def _enable_gateway_prompts() -> None:
    """Route approvals through gateway callbacks instead of CLI input()."""
    os.environ["TAKYON_GATEWAY_SESSION"] = "1"
    os.environ["TAKYON_EXEC_ASK"] = "1"
    os.environ["TAKYON_INTERACTIVE"] = "1"


# ── Blocking prompt factory ──────────────────────────────────────────


def _block(event: str, sid: str, payload: dict, timeout: int = 300) -> str:
    rid = uuid.uuid4().hex[:8]
    ev = threading.Event()
    _pending[rid] = (sid, ev)
    payload["request_id"] = rid
    _emit(event, sid, payload)
    ev.wait(timeout=timeout)
    _pending.pop(rid, None)
    return _answers.pop(rid, "")


def _clear_pending(sid: str | None = None) -> None:
    """Release pending prompts with an empty answer.

    When *sid* is provided, only prompts owned by that session are
    released — critical for session.interrupt, which must not
    collaterally cancel clarify/sudo/secret prompts on unrelated
    sessions sharing the same tui_gateway process.  When *sid* is
    None, every pending prompt is released (used during shutdown).
    """
    for rid, (owner_sid, ev) in list(_pending.items()):
        if sid is None or owner_sid == sid:
            _answers[rid] = ""
            ev.set()


# ── Agent factory ────────────────────────────────────────────────────


def resolve_skin() -> dict:
    try:
        from takyon_cli.skin_engine import init_skin_from_config, get_active_skin

        init_skin_from_config(_load_cfg())
        skin = get_active_skin()
        return {
            "name": skin.name,
            "colors": skin.colors,
            "branding": skin.branding,
            "banner_logo": skin.banner_logo,
            "banner_hero": skin.banner_hero,
            "tool_prefix": skin.tool_prefix,
            "help_header": (skin.branding or {}).get("help_header", ""),
        }
    except Exception:
        return {}


def _resolve_model() -> str:
    env = (
        os.environ.get("TAKYON_MODEL", "")
        or os.environ.get("TAKYON_INFERENCE_MODEL", "")
    ).strip()
    if env:
        return env
    m = _load_cfg().get("model", "")
    if isinstance(m, dict):
        return str(m.get("default", "") or "").strip()
    if isinstance(m, str) and m:
        return m.strip()
    return "anthropic/claude-sonnet-4"


def _resolve_startup_runtime() -> tuple[str, str | None]:
    model = _resolve_model()
    explicit_provider = os.environ.get("TAKYON_TUI_PROVIDER", "").strip()
    if explicit_provider:
        return model, explicit_provider

    explicit_model = (
        os.environ.get("TAKYON_MODEL", "")
        or os.environ.get("TAKYON_INFERENCE_MODEL", "")
    ).strip()
    if not explicit_model:
        return model, None

    try:
        from takyon_cli.models import detect_static_provider_for_model

        cfg = _load_cfg().get("model") or {}
        current_provider = (
            (
                str(cfg.get("provider") or "").strip().lower()
                if isinstance(cfg, dict)
                else ""
            )
            or os.environ.get("TAKYON_INFERENCE_PROVIDER", "").strip().lower()
            or "auto"
        )
        detected = detect_static_provider_for_model(explicit_model, current_provider)
        if detected:
            provider, detected_model = detected
            return detected_model, provider
    except Exception:
        pass
    return model, None


def _write_config_key(key_path: str, value):
    cfg = _load_cfg()
    current = cfg
    keys = key_path.split(".")
    for key in keys[:-1]:
        if key not in current or not isinstance(current.get(key), dict):
            current[key] = {}
        current = current[key]
    current[keys[-1]] = value
    _save_cfg(cfg)


_STATUSBAR_MODES = frozenset({"off", "top", "bottom"})


def _coerce_statusbar(raw) -> str:
    if raw is False:
        return "off"
    if isinstance(raw, str) and (s := raw.strip().lower()) in _STATUSBAR_MODES:
        return s
    return "top"


def _display_mouse_tracking(display: dict) -> bool:
    """Return canonical display.mouse_tracking with legacy tui_mouse fallback."""
    if not isinstance(display, dict):
        return True
    if "mouse_tracking" in display:
        raw = display.get("mouse_tracking")
    else:
        raw = display.get("tui_mouse", True)
    if raw is False or raw == 0:
        return False
    if isinstance(raw, str):
        return raw.strip().lower() not in {"0", "false", "no", "off"}
    return True


def _load_reasoning_config() -> dict | None:
    from takyon_constants import parse_reasoning_effort

    effort = str(
        (_load_cfg().get("agent") or {}).get("reasoning_effort", "") or ""
    ).strip()
    return parse_reasoning_effort(effort)


def _load_service_tier() -> str | None:
    raw = (
        str((_load_cfg().get("agent") or {}).get("service_tier", "") or "")
        .strip()
        .lower()
    )
    if not raw or raw in {"normal", "default", "standard", "off", "none"}:
        return None
    if raw in {"fast", "priority", "on"}:
        return "priority"
    return None


def _load_show_reasoning() -> bool:
    return bool((_load_cfg().get("display") or {}).get("show_reasoning", False))


def _load_tool_progress_mode() -> str:
    env = os.environ.get("TAKYON_TUI_TOOL_PROGRESS", "").strip().lower()
    if env in {"off", "new", "all", "verbose"}:
        return env
    raw = (_load_cfg().get("display") or {}).get("tool_progress", "all")
    if raw is False:
        return "off"
    if raw is True:
        return "all"
    mode = str(raw or "all").strip().lower()
    return mode if mode in {"off", "new", "all", "verbose"} else "all"


def _load_enabled_toolsets() -> list[str] | None:
    explicit = [
        item.strip()
        for item in os.environ.get("TAKYON_TUI_TOOLSETS", "").split(",")
        if item.strip()
    ]
    cfg = None
    fallback_notice = None

    try:
        from toolsets import validate_toolset
    except Exception:
        validate_toolset = None

    if explicit and validate_toolset is not None:
        built_in = [name for name in explicit if validate_toolset(name)]
        unresolved = [name for name in explicit if name not in built_in]

        if unresolved:
            try:
                from takyon_cli.plugins import discover_plugins

                discover_plugins()
                plugin_valid = [name for name in unresolved if validate_toolset(name)]
            except Exception:
                plugin_valid = []

            if plugin_valid:
                built_in.extend(plugin_valid)
                unresolved = [name for name in unresolved if name not in plugin_valid]

        if any(name in {"all", "*"} for name in built_in):
            ignored = [name for name in explicit if name not in {"all", "*"}]
            if ignored:
                print(
                    "[tui] TAKYON_TUI_TOOLSETS=all enables every toolset; "
                    f"ignoring additional entries: {', '.join(ignored)}",
                    file=sys.stderr,
                    flush=True,
                )
            return None

        if not unresolved:
            return built_in

        mcp_names: set[str] = set()
        mcp_disabled: set[str] = set()
        try:
            from takyon_cli.config import read_raw_config
            from takyon_cli.tools_config import _parse_enabled_flag

            raw_cfg = read_raw_config()
            mcp_servers = (
                raw_cfg.get("mcp_servers")
                if isinstance(raw_cfg.get("mcp_servers"), dict)
                else {}
            )
            for name, server_cfg in mcp_servers.items():
                if not isinstance(server_cfg, dict):
                    continue
                if _parse_enabled_flag(server_cfg.get("enabled", True), default=True):
                    mcp_names.add(str(name))
                else:
                    mcp_disabled.add(str(name))
        except Exception:
            mcp_names = set()
            mcp_disabled = set()

        mcp_valid = [name for name in unresolved if name in mcp_names]
        disabled = [name for name in unresolved if name in mcp_disabled]
        unknown = [
            name
            for name in unresolved
            if name not in mcp_names and name not in mcp_disabled
        ]
        valid = built_in + mcp_valid

        if unknown:
            print(
                f"[tui] ignoring unknown TAKYON_TUI_TOOLSETS entries: {', '.join(unknown)}",
                file=sys.stderr,
                flush=True,
            )
        if disabled:
            print(
                "[tui] ignoring disabled MCP servers in TAKYON_TUI_TOOLSETS "
                "(set enabled: true in config.yaml to use): "
                f"{', '.join(disabled)}",
                file=sys.stderr,
                flush=True,
            )

        if valid:
            return valid

        fallback_notice = (
            "[tui] no valid TAKYON_TUI_TOOLSETS entries; using configured CLI toolsets"
        )

    try:
        from takyon_cli.config import load_config
        from takyon_cli.tools_config import _get_platform_tools

        cfg = cfg if cfg is not None else load_config()

        # Runtime toolset resolution must include default MCP servers so the
        # agent can actually call them. Passing ``False`` here is the
        # config-editing variant — used when we need to persist a toolset
        # list without baking in implicit MCP defaults. Using the wrong
        # variant at agent creation time makes MCP tools silently missing
        # from the TUI. See PR #3252 for the original design split.
        enabled = sorted(
            _get_platform_tools(cfg, "cli", include_default_mcp_servers=True)
        )
        if fallback_notice is not None:
            print(fallback_notice, file=sys.stderr, flush=True)
        return enabled or None
    except Exception:
        if fallback_notice is not None:
            print(
                "[tui] no valid TAKYON_TUI_TOOLSETS entries and configured CLI toolsets could not be loaded; enabling all toolsets",
                file=sys.stderr,
                flush=True,
            )
        return None


def _session_tool_progress_mode(sid: str) -> str:
    return str(_sessions.get(sid, {}).get("tool_progress_mode", "all") or "all")


def _tool_progress_enabled(sid: str) -> bool:
    return _session_tool_progress_mode(sid) != "off"


def _restart_slash_worker(session: dict):
    worker = session.get("slash_worker")
    if worker:
        try:
            worker.close()
        except Exception:
            pass
    try:
        session["slash_worker"] = _SlashWorker(
            session["session_key"],
            getattr(session.get("agent"), "model", _resolve_model()),
            operator_user_id=_takyon_operator_user_id(session),
        )
    except Exception:
        session["slash_worker"] = None


def _persist_model_switch(result) -> None:
    from takyon_cli.config import save_config

    cfg = _load_cfg()
    model_cfg = cfg.get("model")
    if not isinstance(model_cfg, dict):
        model_cfg = {}
        cfg["model"] = model_cfg

    model_cfg["default"] = result.new_model
    model_cfg["provider"] = result.target_provider
    if result.base_url:
        model_cfg["base_url"] = result.base_url
    else:
        model_cfg.pop("base_url", None)
    save_config(cfg)


def _apply_model_switch(sid: str, session: dict, raw_input: str) -> dict:
    from takyon_cli.model_switch import parse_model_flags, switch_model
    from takyon_cli.runtime_provider import resolve_runtime_provider

    model_input, explicit_provider, persist_global = parse_model_flags(raw_input)
    if not model_input:
        raise ValueError("model value required")

    agent = session.get("agent")
    if agent:
        current_provider = getattr(agent, "provider", "") or ""
        current_model = getattr(agent, "model", "") or ""
        current_base_url = getattr(agent, "base_url", "") or ""
        current_api_key = getattr(agent, "api_key", "") or ""
    else:
        runtime = resolve_runtime_provider(requested=None)
        current_provider = str(runtime.get("provider", "") or "")
        current_model = _resolve_model()
        current_base_url = str(runtime.get("base_url", "") or "")
        # Preserve a callable api_key (Azure Foundry Entra ID bearer
        # provider) unchanged — ``str(...)`` would produce
        # ``"<function ...>"`` and poison downstream switch_model
        # validation. Match the agent-present branch's behavior at the
        # top of this block.
        _runtime_key = runtime.get("api_key", "")
        if callable(_runtime_key) and not isinstance(_runtime_key, str):
            current_api_key = _runtime_key
        else:
            current_api_key = str(_runtime_key or "")

    # Load user-defined providers so switch_model can resolve named custom
    # endpoints (e.g. "ollama-launch") and validate against saved model lists.
    user_provs = None
    custom_provs = None
    try:
        from takyon_cli.config import get_compatible_custom_providers, load_config

        cfg = load_config()
        user_provs = cfg.get("providers")
        custom_provs = get_compatible_custom_providers(cfg)
    except Exception:
        pass

    result = switch_model(
        raw_input=model_input,
        current_provider=current_provider,
        current_model=current_model,
        current_base_url=current_base_url,
        current_api_key=current_api_key,
        is_global=persist_global,
        explicit_provider=explicit_provider,
        user_providers=user_provs,
        custom_providers=custom_provs,
    )
    if not result.success:
        raise ValueError(result.error_message or "model switch failed")

    if agent:
        agent.switch_model(
            new_model=result.new_model,
            new_provider=result.target_provider,
            api_key=result.api_key,
            base_url=result.base_url,
            api_mode=result.api_mode,
        )
        if getattr(agent, "_takyon_operator_gateway", False):
            from plugins.takyon.operator_gateway import enable_operator_gateway

            runtime = resolve_runtime_provider(
                requested=result.target_provider,
                target_model=result.new_model,
                explicit_base_url=result.base_url or None,
            )
            context = getattr(agent, "_takyon_operator_gateway_context", None)
            enable_operator_gateway(
                agent,
                runtime,
                operator_user_id=getattr(context, "operator_user_id", ""),
                business_slug=getattr(context, "business_slug", ""),
                workspace_root=getattr(context, "workspace_root", ""),
            )
        _restart_slash_worker(session)
        _emit("session.info", sid, _session_info(agent))

    os.environ["TAKYON_MODEL"] = result.new_model
    os.environ["TAKYON_INFERENCE_MODEL"] = result.new_model
    # Keep the process-level provider env vars in sync with the user's
    # explicit choice so any ambient re-resolution (credential pool refresh,
    # compressor rebuild, aux clients) and startup re-resolution on /new
    # both pick up the new provider instead of the original one persisted
    # in config or env.
    #
    # TAKYON_TUI_PROVIDER is the canonical "explicit-this-process" carrier
    # consumed by _resolve_startup_runtime() — set it unconditionally on
    # /model so /new can't fall through to static-catalog detection and
    # pick a coincidentally-matching native provider (fixes #16857).
    if result.target_provider:
        os.environ["TAKYON_INFERENCE_PROVIDER"] = result.target_provider
        os.environ["TAKYON_TUI_PROVIDER"] = result.target_provider
    if persist_global:
        _persist_model_switch(result)
    return {"value": result.new_model, "warning": result.warning_message or ""}


def _compress_session_history(
    session: dict,
    focus_topic: str | None = None,
    approx_tokens: int | None = None,
    before_messages: list | None = None,
    history_version: int | None = None,
) -> tuple[int, dict]:
    from agent.model_metadata import estimate_request_tokens_rough

    agent = session["agent"]
    # Snapshot history under the lock so the LLM-bound compression call
    # below does NOT hold history_lock for the duration of the request —
    # otherwise other handlers acquiring the lock (prompt.submit etc.)
    # block on the dispatcher loop while compaction runs.
    if before_messages is None or history_version is None:
        with session["history_lock"]:
            before_messages = list(session.get("history", []))
            history_version = int(session.get("history_version", 0))
    history = before_messages
    if len(history) < 4:
        usage = _get_usage(agent)
        return 0, usage
    if approx_tokens is None:
        # Include system prompt + tool schemas so the figure reflects real
        # request pressure, not a transcript-only underestimate (#6217).
        _sys_prompt = getattr(agent, "_cached_system_prompt", "") or ""
        _tools = getattr(agent, "tools", None) or None
        approx_tokens = estimate_request_tokens_rough(
            history, system_prompt=_sys_prompt, tools=_tools
        )
    # Pass system_message=None so AIAgent._compress_context rebuilds the
    # system prompt cleanly via _build_system_prompt(None). Passing the
    # cached prompt (which already contains the agent identity block)
    # makes the rebuild append the identity a second time. Mirrors the
    # CLI's _manual_compress fix for issue #15281.
    compressed, _ = agent._compress_context(
        history,
        None,
        approx_tokens=approx_tokens,
        focus_topic=focus_topic or None,
    )
    with session["history_lock"]:
        if int(session.get("history_version", 0)) != history_version:
            # External mutation during compaction — drop the compressed
            # result so we don't clobber concurrent edits.
            usage = _get_usage(agent)
            return 0, usage
        session["history"] = compressed
        session["history_version"] = history_version + 1
    usage = _get_usage(agent)
    return len(history) - len(compressed), usage


def _sync_session_key_after_compress(
    sid: str,
    session: dict,
    *,
    clear_pending_title: bool = True,
    restart_slash_worker: bool = True,
) -> None:
    """Re-anchor session_key when AIAgent._compress_context rotates session_id.

    AIAgent._compress_context ends the current SessionDB session and creates
    a new continuation session, rotating ``agent.session_id``.  The TUI
    gateway keeps the gateway-side ``session_key`` separate (used for
    approval routing, slash worker init, DB title/history lookups, yolo
    state).  Without this sync, those operations would target the ended
    parent session while the agent writes to the new continuation session.

    Policy flags:
        clear_pending_title: True for manual /compress (title belongs to old
            session). False for post-turn auto-compression (preserve user
            intent so pending_title can be applied to the continuation).
        restart_slash_worker: True for manual /compress and post-turn
            auto-compression (worker holds stale session key). False only
            if the caller manages the worker lifecycle separately.
    """
    agent = session.get("agent")
    new_session_id = getattr(agent, "session_id", None) or ""
    old_key = session.get("session_key", "") or ""
    if not new_session_id or new_session_id == old_key:
        return

    try:
        from tools.approval import (
            disable_session_yolo,
            enable_session_yolo,
            is_session_yolo_enabled,
            register_gateway_notify,
            unregister_gateway_notify,
        )

        try:
            unregister_gateway_notify(old_key)
        except Exception:
            pass
        session["session_key"] = new_session_id
        try:
            yolo_was_on = is_session_yolo_enabled(old_key)
        except Exception:
            yolo_was_on = False
        if yolo_was_on:
            try:
                enable_session_yolo(new_session_id)
                disable_session_yolo(old_key)
            except Exception:
                pass
        try:
            register_gateway_notify(
                new_session_id,
                lambda data: _emit("approval.request", sid, data),
            )
        except Exception:
            pass
    except Exception:
        # Even if the approval module fails to import, still anchor the
        # session_key on the new continuation id so downstream lookups
        # don't keep targeting the ended row.
        session["session_key"] = new_session_id

    if clear_pending_title:
        session["pending_title"] = None
    if restart_slash_worker:
        try:
            _restart_slash_worker(session)
        except Exception:
            pass


def _get_usage(agent) -> dict:
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


def _probe_credentials(agent) -> str:
    """Light credential check at session creation — returns warning or ''."""
    try:
        key = getattr(agent, "api_key", "") or ""
        provider = getattr(agent, "provider", "") or ""
        if not key or key == "no-key-required":
            return f"No API key configured for provider '{provider}'. First message will fail."
    except Exception:
        pass
    return ""


def _probe_config_health(cfg: dict) -> str:
    """Flag bare YAML keys (`agent:` with no value → None) that silently
    drop nested settings. Returns warning or ''."""
    if not isinstance(cfg, dict):
        return ""
    warnings: list[str] = []
    null_keys = sorted(k for k, v in cfg.items() if v is None)
    if not null_keys:
        pass
    else:
        keys = ", ".join(f"`{k}`" for k in null_keys)
        warnings.append(
            f"config.yaml has empty section(s): {keys}. "
            f"Remove the line(s) or set them to `{{}}` — "
            f"empty sections silently drop nested settings."
        )
    display_cfg = cfg.get("display")
    agent_cfg = cfg.get("agent")
    if isinstance(display_cfg, dict):
        personality = str(display_cfg.get("personality", "") or "").strip().lower()
        if (
            personality
            and personality not in {"default", "none", "neutral"}
            and isinstance(agent_cfg, dict)
            and agent_cfg.get("personalities") is None
        ):
            warnings.append(
                "`display.personality` is set but `agent.personalities` is empty/null; "
                "personality overlay will be skipped."
            )
    return " ".join(warnings).strip()


def _current_profile_name() -> str:
    try:
        from takyon_cli.profiles import get_active_profile_name

        return get_active_profile_name() or "default"
    except Exception:
        return "default"


def _session_info(agent) -> dict:
    reasoning_config = getattr(agent, "reasoning_config", None)
    reasoning_effort = ""
    if (
        isinstance(reasoning_config, dict)
        and reasoning_config.get("enabled") is not False
    ):
        reasoning_effort = str(reasoning_config.get("effort", "") or "")
    service_tier = getattr(agent, "service_tier", None) or ""
    info: dict = {
        "model": getattr(agent, "model", ""),
        "reasoning_effort": reasoning_effort,
        "service_tier": service_tier,
        "fast": service_tier == "priority",
        "tools": {},
        "skills": {},
        "cwd": os.getenv("TERMINAL_CWD", os.getcwd()),
        "version": "",
        "release_date": "",
        "update_behind": None,
        "update_command": "",
        "usage": _get_usage(agent),
        "profile_name": _current_profile_name(),
    }
    try:
        from takyon_cli import __version__, __release_date__

        info["version"] = __version__
        info["release_date"] = __release_date__
    except Exception:
        pass
    try:
        from model_tools import get_toolset_for_tool

        for t in getattr(agent, "tools", []) or []:
            name = t["function"]["name"]
            info["tools"].setdefault(get_toolset_for_tool(name) or "other", []).append(
                name
            )
    except Exception:
        pass
    try:
        from takyon_cli.banner import get_available_skills

        info["skills"] = get_available_skills()
    except Exception:
        pass
    try:
        from tools.mcp_tool import get_mcp_status

        info["mcp_servers"] = get_mcp_status()
    except Exception:
        info["mcp_servers"] = []
    try:
        info["system_prompt"] = getattr(agent, "_cached_system_prompt", "") or ""
    except Exception:
        pass
    try:
        from takyon_cli.banner import get_update_result
        from takyon_cli.config import recommended_update_command

        info["update_behind"] = get_update_result(timeout=0.5)
        info["update_command"] = recommended_update_command()
    except Exception:
        pass
    return info


def _tool_ctx(name: str, args: dict) -> str:
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


def _append_turn_file_activity(session: dict | None, items: list[dict[str, str]]) -> None:
    if not isinstance(session, dict) or not items:
        return
    bucket = session.setdefault("takyon_turn_file_activity", [])
    if not isinstance(bucket, list):
        bucket = []
        session["takyon_turn_file_activity"] = bucket
    seen = {
        (str(item.get("action") or "").strip(), str(item.get("path") or "").strip())
        for item in bucket
        if isinstance(item, dict)
    }
    for item in items:
        if not isinstance(item, dict):
            continue
        action = str(item.get("action") or "").strip()
        path = str(item.get("path") or "").strip()
        if not action or not path:
            continue
        key = (action, path)
        if key in seen:
            continue
        seen.add(key)
        bucket.append({"action": action, "path": path})


def _turn_file_activity_targets_product_surface(file_activity: list[dict[str, str]]) -> bool:
    if not isinstance(file_activity, list) or not file_activity:
        return False
    try:
        from plugins.takyon.core import _workspace_needs_runtime_ui_contract
    except Exception:
        return False
    for item in file_activity:
        if not isinstance(item, dict):
            continue
        path = str(item.get("path") or "").strip()
        if path and _workspace_needs_runtime_ui_contract(path):
            return True
    return False


def _finalize_product_surface_after_turn(
    session: dict | None,
    *,
    sid: str,
    business_slug: str,
    operator_user_id: str,
) -> str:
    if not isinstance(session, dict):
        return ""
    file_activity = list(session.get("takyon_turn_file_activity") or [])
    if not _turn_file_activity_targets_product_surface(file_activity):
        return ""
    from plugins.takyon.worker import _refresh_business_surface_after_bootstrap

    refresh = _refresh_business_surface_after_bootstrap(
        business_slug,
        job_id=f"session:{session.get('session_key') or sid}:{session.get('takyon_active_turn_key') or 'turn'}",
        operator_user_id=operator_user_id or None,
    )
    if not isinstance(refresh, dict):
        return ""
    publish = refresh.get("publish") if isinstance(refresh.get("publish"), dict) else {}
    publish_status = str(publish.get("status") or refresh.get("status") or "").strip()
    publish_blocker = str(
        publish.get("blocker")
        or refresh.get("blocker")
        or refresh.get("error")
        or ""
    ).strip()
    if publish_status and publish_status != "published" and publish_blocker:
        return f"Product surface: {publish_status} - {publish_blocker}"
    return ""


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


def _takyon_trace_label(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return "Activity"
    text = re.sub(r"[._-]+", " ", text)
    return " ".join(part.capitalize() for part in text.split())


def _takyon_trace_tool_shape(
    name: str,
    args: dict | None = None,
    context: str = "",
) -> tuple[str, str, str, str]:
    tool_name = str(name or "").strip()
    tool_args = args if isinstance(args, dict) else {}
    preview = str(context or "").strip()
    if tool_name == "skill_view":
        skill_name = str(tool_args.get("name") or preview).strip()
        label = skill_name or "Skill"
        detail = preview or (f"Loaded skill {skill_name}." if skill_name else "Loaded a skill.")
        return "skill", label, detail, skill_name
    if tool_name == "todo":
        todos = tool_args.get("todos")
        count = len(todos) if isinstance(todos, list) else 0
        detail = f"Updated {count} task{'s' if count != 1 else ''}." if count else "Updated task list."
        return "tool", "Todo", detail, ""
    if tool_name == "business_claude_agent_task":
        workspace = str(tool_args.get("workspace") or tool_args.get("source_path") or preview).strip()
        return "tool", "Delegated worker", workspace or "Delegated workspace task.", ""
    return "tool", _takyon_trace_label(tool_name), preview, ""


def _takyon_record_session_runtime_event(
    session: dict | None,
    *,
    kind: str,
    status: str,
    detail: str = "",
    line: str = "",
    command: str = "operator turn",
    trace: dict[str, Any] | None = None,
) -> None:
    if not isinstance(session, dict):
        return
    slug = str(session.get("takyon_current_business") or "").strip()
    if not slug:
        return
    payload: dict[str, Any] = {
        "kind": kind,
        "status": status,
        "detail": detail,
        "line": line,
        "command": command,
    }
    if isinstance(trace, dict) and trace:
        payload["trace"] = {
            str(key): value
            for key, value in trace.items()
            if value not in (None, "", [], {})
        }
    try:
        store = _takyon_store(session)
        with store._connect() as conn:
            store._record_event(
                conn,
                scope=f"business:{slug}/runtime",
                business_slug=slug,
                event_type=f"dashboard.run.{status}",
                payload=payload,
            )
    except Exception as exc:
        logger.debug("failed to record gateway runtime event for %s: %s", slug, exc)


def _on_tool_start(sid: str, tool_call_id: str, name: str, args: dict):
    session = _sessions.get(sid)
    if session is not None:
        try:
            from agent.display import capture_local_edit_snapshot

            snapshot = capture_local_edit_snapshot(name, args)
            if snapshot is not None:
                session.setdefault("edit_snapshots", {})[tool_call_id] = snapshot
        except Exception:
            pass
        session.setdefault("tool_started_at", {})[tool_call_id] = time.time()
        context = _tool_ctx(name, args)
        entry_kind, label, detail, skill_name = _takyon_trace_tool_shape(name, args, context)
        _takyon_record_session_runtime_event(
            session,
            kind="ceo_turn",
            status="trace",
            detail=detail or label,
            trace={
                "kind": entry_kind,
                "entry_key": f"tool:{tool_call_id}",
                "label": label,
                "detail": detail or label,
                "status": "running",
                "tool_name": str(name or "").strip(),
                "skill_name": skill_name,
                "preview": context,
                "turn_key": str(session.get("takyon_active_turn_key") or "").strip(),
            },
        )
    if _tool_progress_enabled(sid):
        _emit(
            "tool.start",
            sid,
            {"tool_id": tool_call_id, "name": name, "context": _tool_ctx(name, args)},
        )
        progress_message = str(detail or label or f"Running {name}").strip()
        progress_target = str(_tool_ctx(name, args) or "").strip()
        if progress_target and progress_target == progress_message:
            progress_target = ""
        _emit_progress(sid, "running", progress_message, target=progress_target or None)


def _on_tool_complete(sid: str, tool_call_id: str, name: str, args: dict, result: str):
    payload = {"tool_id": tool_call_id, "name": name}
    session = _sessions.get(sid)
    snapshot = None
    started_at = None
    if session is not None:
        snapshot = session.setdefault("edit_snapshots", {}).pop(tool_call_id, None)
        started_at = session.setdefault("tool_started_at", {}).pop(tool_call_id, None)
    duration_s = time.time() - started_at if started_at else None
    if duration_s is not None:
        payload["duration_s"] = duration_s
    summary = _tool_summary(name, result, duration_s)
    if summary:
        payload["summary"] = summary
    file_activity = _tool_file_activity(result)
    if file_activity:
        _append_turn_file_activity(session, file_activity)
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
    if session is not None:
        context = _tool_ctx(name, args)
        entry_kind, label, detail, skill_name = _takyon_trace_tool_shape(name, args, context)
        _takyon_record_session_runtime_event(
            session,
            kind="ceo_turn",
            status="trace",
            detail=str(payload.get("summary") or detail or label).strip(),
            trace={
                "kind": entry_kind,
                "entry_key": f"tool:{tool_call_id}",
                "label": label,
                "detail": str(payload.get("summary") or detail or label).strip(),
                "status": "completed",
                "tool_name": str(name or "").strip(),
                "skill_name": skill_name,
                "preview": context,
                "summary": str(payload.get("summary") or "").strip(),
                "turn_key": str(session.get("takyon_active_turn_key") or "").strip(),
            },
        )
    if _tool_progress_enabled(sid) or payload.get("inline_diff"):
        _emit("tool.complete", sid, payload)


def _on_tool_progress(
    sid: str,
    event_type: str,
    name: str | None = None,
    preview: str | None = None,
    _args: dict | None = None,
    **_kwargs,
):
    if not _tool_progress_enabled(sid):
        return
    if event_type == "tool.started" and name:
        _emit("tool.progress", sid, {"name": name, "preview": preview or ""})
        return
    if event_type == "reasoning.available" and preview:
        _emit("reasoning.available", sid, {"text": str(preview)})
        return
    if event_type.startswith("subagent."):
        payload = {
            "goal": str(_kwargs.get("goal") or ""),
            "task_count": int(_kwargs.get("task_count") or 1),
            "task_index": int(_kwargs.get("task_index") or 0),
        }
        # Identity fields for the TUI spawn tree.  All optional — older
        # emitters that omit them fall back to flat rendering client-side.
        if _kwargs.get("subagent_id"):
            payload["subagent_id"] = str(_kwargs["subagent_id"])
        if _kwargs.get("parent_id"):
            payload["parent_id"] = str(_kwargs["parent_id"])
        if _kwargs.get("depth") is not None:
            payload["depth"] = int(_kwargs["depth"])
        if _kwargs.get("model"):
            payload["model"] = str(_kwargs["model"])
        if _kwargs.get("tool_count") is not None:
            payload["tool_count"] = int(_kwargs["tool_count"])
        if _kwargs.get("toolsets"):
            payload["toolsets"] = [str(t) for t in _kwargs["toolsets"]]
        # Per-branch rollups emitted on subagent.complete (features 1+2+4).
        for int_key in (
            "input_tokens",
            "output_tokens",
            "reasoning_tokens",
            "api_calls",
        ):
            val = _kwargs.get(int_key)
            if val is not None:
                try:
                    payload[int_key] = int(val)
                except (TypeError, ValueError):
                    pass
        if _kwargs.get("cost_usd") is not None:
            try:
                payload["cost_usd"] = float(_kwargs["cost_usd"])
            except (TypeError, ValueError):
                pass
        if _kwargs.get("files_read"):
            payload["files_read"] = [str(p) for p in _kwargs["files_read"]]
        if _kwargs.get("files_written"):
            payload["files_written"] = [str(p) for p in _kwargs["files_written"]]
        if _kwargs.get("output_tail"):
            payload["output_tail"] = list(_kwargs["output_tail"])  # list of dicts
        if name:
            payload["tool_name"] = str(name)
        if preview:
            payload["text"] = str(preview)
        if _kwargs.get("status"):
            payload["status"] = str(_kwargs["status"])
        if _kwargs.get("summary"):
            payload["summary"] = str(_kwargs["summary"])
        if _kwargs.get("duration_seconds") is not None:
            payload["duration_seconds"] = float(_kwargs["duration_seconds"])
        if preview and event_type == "subagent.tool":
            payload["tool_preview"] = str(preview)
            payload["text"] = str(preview)
        _emit(event_type, sid, payload)


def _agent_cbs(sid: str) -> dict:
    return {
        "tool_start_callback": lambda tc_id, name, args: _on_tool_start(
            sid, tc_id, name, args
        ),
        "tool_complete_callback": lambda tc_id, name, args, result: _on_tool_complete(
            sid, tc_id, name, args, result
        ),
        "tool_progress_callback": lambda event_type, name=None, preview=None, args=None, **kwargs: _on_tool_progress(
            sid, event_type, name, preview, args, **kwargs
        ),
        "tool_gen_callback": lambda name: _tool_progress_enabled(sid)
        and _emit("tool.generating", sid, {"name": name}),
        "thinking_callback": lambda text: _emit("thinking.delta", sid, {"text": text}),
        "reasoning_callback": lambda text: _emit("reasoning.delta", sid, {"text": text}),
        "status_callback": lambda kind, text=None: _status_update(
            sid, str(kind), None if text is None else str(text)
        ),
        "clarify_callback": lambda q, c: _block(
            "clarify.request", sid, {"question": q, "choices": c}
        ),
    }


def _wire_callbacks(sid: str):
    from tools.terminal_tool import set_sudo_password_callback
    from tools.skills_tool import set_secret_capture_callback

    set_sudo_password_callback(lambda: _block("sudo.request", sid, {}, timeout=120))

    def secret_cb(env_var, prompt, metadata=None):
        pl = {"prompt": prompt, "env_var": env_var}
        if metadata:
            pl["metadata"] = metadata
        val = _block("secret.request", sid, pl)
        if not val:
            return {
                "success": True,
                "stored_as": env_var,
                "validated": False,
                "skipped": True,
                "message": "skipped",
            }
        from takyon_cli.config import save_env_value_secure

        return {
            **save_env_value_secure(env_var, val),
            "skipped": False,
            "message": "ok",
        }

    set_secret_capture_callback(secret_cb)


def _apply_usage_snapshot(
    agent,
    usage_snapshot: dict[str, Any] | None,
    *,
    session_id: str = "",
) -> None:
    if agent is None:
        return
    if isinstance(usage_snapshot, dict):
        for key, value in usage_snapshot.items():
            try:
                setattr(agent, key, value)
            except Exception:
                pass
    if session_id:
        try:
            agent.session_id = session_id
        except Exception:
            pass


def _terminate_isolated_turn_proc(proc) -> None:
    if proc is None:
        return
    try:
        if proc.poll() is not None:
            return
    except Exception:
        return
    try:
        proc.terminate()
    except Exception:
        return
    try:
        proc.wait(timeout=1.5)
        return
    except Exception:
        pass
    try:
        proc.kill()
        proc.wait(timeout=1.5)
    except Exception:
        pass


def _build_isolated_turn_payload(
    session: dict,
    agent,
    run_message: Any,
    history: list[dict[str, Any]],
    *,
    operator_user_id: str,
    business_slug: str,
    system_message: str | None = None,
    max_iterations_override: int | None = None,
    agent_config_overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    gateway_ctx = getattr(agent, "_takyon_operator_gateway_context", None)
    requested_provider = str(
        getattr(gateway_ctx, "requested_provider", "") or getattr(agent, "provider", "") or ""
    ).strip()
    upstream_base_url = str(
        getattr(gateway_ctx, "upstream_base_url", "") or getattr(agent, "base_url", "") or ""
    ).strip()
    payload = {
        "session_key": str(session.get("session_key") or ""),
        "operator_user_id": operator_user_id,
        "business_slug": business_slug,
        "run_message": run_message,
        "history": history,
        "model": str(getattr(agent, "model", "") or ""),
        "runtime": {
            "provider": str(getattr(agent, "provider", "") or ""),
            "requested_provider": requested_provider or str(getattr(agent, "provider", "") or ""),
            "api_mode": str(getattr(agent, "api_mode", "") or ""),
            "base_url": upstream_base_url,
        },
        "agent_config": {
            "max_iterations": int(
                max_iterations_override
                or getattr(agent, "max_iterations", 90)
                or 90
            ),
            "verbose_logging": bool(session.get("tool_progress_mode") == "verbose"),
            "reasoning_config": getattr(agent, "reasoning_config", None),
            "service_tier": getattr(agent, "service_tier", None),
            "enabled_toolsets": list(getattr(agent, "enabled_toolsets", None) or []),
            "disabled_toolsets": list(getattr(agent, "disabled_toolsets", None) or []),
            "ephemeral_system_prompt": getattr(agent, "ephemeral_system_prompt", None),
            "providers_allowed": getattr(agent, "providers_allowed", None),
            "providers_ignored": getattr(agent, "providers_ignored", None),
            "providers_order": getattr(agent, "providers_order", None),
            "provider_sort": getattr(agent, "provider_sort", None),
            "provider_require_parameters": bool(
                getattr(agent, "provider_require_parameters", False)
            ),
            "provider_data_collection": getattr(agent, "provider_data_collection", None),
            "openrouter_min_coding_score": getattr(
                agent, "openrouter_min_coding_score", None
            ),
            "request_overrides": dict(getattr(agent, "request_overrides", {}) or {}),
            "fallback_model": getattr(agent, "_fallback_model", None),
            "checkpoints_enabled": bool(getattr(agent, "_checkpoint_mgr", None)),
            "pass_session_id": bool(getattr(agent, "pass_session_id", False)),
            "skip_context_files": bool(getattr(agent, "skip_context_files", False)),
            "skip_memory": bool(getattr(agent, "skip_memory", False)),
        },
        "system_message": system_message or None,
    }
    overrides = (
        dict(agent_config_overrides)
        if isinstance(agent_config_overrides, dict)
        else {}
    )
    if overrides:
        payload["agent_config"].update(overrides)
    return payload


def _forward_isolated_turn_event(
    sid: str,
    session: dict,
    event: str,
    payload: dict[str, Any],
    streamer,
) -> None:
    if event == "message.delta":
        text = str(payload.get("text") or "")
        delta_payload = {"text": text}
        if streamer and (r := streamer.feed(text)) is not None:
            delta_payload["rendered"] = r
        _emit("message.delta", sid, delta_payload)
        return
    if event == "tool.start":
        name = str(payload.get("name") or "").strip()
        context = str(payload.get("context") or "").strip()
        entry_kind, label, detail, skill_name = _takyon_trace_tool_shape(name, None, context)
        _takyon_record_session_runtime_event(
            session,
            kind="ceo_turn",
            status="trace",
            detail=detail or label,
            trace={
                "kind": entry_kind,
                "entry_key": f"tool:{payload.get('tool_id') or name}",
                "label": label,
                "detail": detail or label,
                "status": "running",
                "tool_name": name,
                "skill_name": skill_name,
                "preview": context,
                "turn_key": str(session.get("takyon_active_turn_key") or "").strip(),
            },
        )
        _emit("tool.start", sid, payload)
        return
    if event == "tool.complete":
        name = str(payload.get("name") or "").strip()
        detail = str(payload.get("summary") or name or "Tool completed.").strip()
        _takyon_record_session_runtime_event(
            session,
            kind="ceo_turn",
            status="trace",
            detail=detail,
            trace={
                "kind": "tool",
                "entry_key": f"tool:{payload.get('tool_id') or name}",
                "label": _takyon_trace_label(name),
                "detail": detail,
                "status": "completed",
                "tool_name": name,
                "summary": str(payload.get("summary") or "").strip(),
                "turn_key": str(session.get("takyon_active_turn_key") or "").strip(),
            },
        )
        _emit("tool.complete", sid, payload)
        return
    if event == "tool.progress":
        _emit("tool.progress", sid, payload)
        return
    if event in {
        "tool.generating",
        "thinking.delta",
        "reasoning.delta",
        "reasoning.available",
        "status.update",
        "review.summary",
        "progress",
    }:
        _emit(event, sid, payload)


def _run_isolated_gateway_turn(
    sid: str,
    session: dict,
    agent,
    run_message: Any,
    history: list[dict[str, Any]],
    *,
    operator_user_id: str,
    business_slug: str,
    streamer,
    system_message_override: str | None = None,
    max_iterations_override: int | None = None,
    agent_config_overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = _build_isolated_turn_payload(
        session,
        agent,
        run_message,
        history,
        operator_user_id=operator_user_id,
        business_slug=business_slug,
        system_message=system_message_override,
        max_iterations_override=max_iterations_override,
        agent_config_overrides=agent_config_overrides,
    )
    proc = subprocess.Popen(
        [sys.executable, "-m", "tui_gateway.isolated_turn_worker"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
        cwd=os.getcwd(),
        env=os.environ.copy(),
    )
    session["takyon_turn_proc"] = proc
    session.pop("takyon_turn_interrupted", None)
    stderr_tail: list[str] = []

    def _drain_stderr() -> None:
        assert proc.stderr is not None
        for line in proc.stderr:
            text = line.rstrip()
            if not text:
                continue
            stderr_tail.append(text)
            if len(stderr_tail) > 40:
                del stderr_tail[:-40]
            print(text, file=sys.stderr)

    threading.Thread(target=_drain_stderr, daemon=True).start()
    final: dict[str, Any] | None = None
    try:
        assert proc.stdin is not None
        proc.stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
        proc.stdin.flush()
        assert proc.stdout is not None
        for raw in proc.stdout:
            line = raw.strip()
            if not line:
                continue
            msg = json.loads(line)
            kind = str(msg.get("type") or "")
            if kind == "event":
                _forward_isolated_turn_event(
                    sid,
                    session,
                    str(msg.get("event") or ""),
                    dict(msg.get("payload") or {}),
                    streamer,
                )
                continue
            if kind == "request":
                answer = _block(
                    str(msg.get("event") or ""),
                    sid,
                    dict(msg.get("payload") or {}),
                    timeout=int(msg.get("timeout") or 300),
                )
                proc.stdin.write(
                    json.dumps(
                        {
                            "type": "response",
                            "request_id": msg.get("request_id"),
                            "value": answer,
                        },
                        ensure_ascii=False,
                    )
                    + "\n"
                )
                proc.stdin.flush()
                continue
            if kind == "result":
                final = msg
                break
            if kind == "error":
                raise RuntimeError(str(msg.get("message") or "isolated turn failed"))
        if final is None:
            rc = proc.wait(timeout=5.0)
            if session.pop("takyon_turn_interrupted", False):
                return {
                    "result": {"final_response": "", "messages": history, "interrupted": True},
                    "usage": {},
                    "usage_snapshot": {},
                    "session_id": str(getattr(agent, "session_id", "") or ""),
                    "session_estimated_cost_usd": float(
                        getattr(agent, "session_estimated_cost_usd", 0.0) or 0.0
                    ),
                }
            stderr = "\n".join(stderr_tail[-8:]).strip()
            if stderr:
                detail = stderr
            else:
                from plugins.takyon.core import _format_process_exit_detail

                detail = _format_process_exit_detail(rc, process_label="isolated turn")
            raise RuntimeError(detail)
        return final
    finally:
        session.pop("takyon_turn_proc", None)
        session.pop("takyon_turn_interrupted", None)
        try:
            if proc.stdin is not None:
                proc.stdin.close()
        except Exception:
            pass
        _terminate_isolated_turn_proc(proc)


def _render_personality_prompt(value) -> str:
    if isinstance(value, dict):
        parts = [value.get("system_prompt", "")]
        if value.get("tone"):
            parts.append(f'Tone: {value["tone"]}')
        if value.get("style"):
            parts.append(f'Style: {value["style"]}')
        return "\n".join(p for p in parts if p)
    return str(value)


def _available_personalities(cfg: dict | None = None) -> dict:
    try:
        from cli import load_cli_config

        return (load_cli_config().get("agent") or {}).get("personalities", {}) or {}
    except Exception:
        try:
            from takyon_cli.config import load_config as _load_full_cfg

            return (_load_full_cfg().get("agent") or {}).get("personalities", {}) or {}
        except Exception:
            cfg = cfg or _load_cfg()
            return (cfg.get("agent") or {}).get("personalities", {}) or {}


def _validate_personality(value: str, cfg: dict | None = None) -> tuple[str, str]:
    raw = str(value or "").strip()
    name = raw.lower()
    if not name or name in {"none", "default", "neutral"}:
        return "", ""

    personalities = _available_personalities(cfg)
    if name not in personalities:
        names = sorted(personalities)
        available = ", ".join(f"`{n}`" for n in names)
        base = f"Unknown personality: `{raw}`."
        if available:
            base += f"\n\nAvailable: `none`, {available}"
        else:
            base += "\n\nNo personalities configured."
        raise ValueError(base)

    return name, _render_personality_prompt(personalities[name])


def _apply_personality_to_session(
    sid: str, session: dict, new_prompt: str
) -> tuple[bool, dict | None]:
    """Apply a personality change to an existing session without resetting history.

    Updates the agent's ephemeral system prompt in-place so the new personality
    takes effect on the next turn.  The cached base system prompt is left intact
    (ephemeral_system_prompt is appended at API-call time, not baked into the
    cache), which preserves prompt-cache hits.

    Also injects a system-role marker into the conversation history so the model
    knows to pivot its style from this point forward (without this, LLMs tend to
    continue the tone established by earlier messages in the transcript).

    Returns (history_reset, info) — history_reset is always False since we
    preserve the conversation.
    """
    if not session:
        return False, None

    agent = session.get("agent")
    if agent:
        agent.ephemeral_system_prompt = new_prompt or None
        # Inject a pivot marker into history so the model sees the change point.
        # This prevents it from pattern-matching its prior style.
        if new_prompt:
            marker = (
                "[System: The user has changed the assistant's personality. "
                "From this point forward, adopt the following persona and respond "
                f"accordingly: {new_prompt}]"
            )
        else:
            marker = (
                "[System: The user has cleared the personality overlay. "
                "From this point forward, respond in your normal default style.]"
            )
        with session["history_lock"]:
            session["history"].append({"role": "user", "content": marker})
            session["history_version"] = int(session.get("history_version", 0)) + 1
        info = _session_info(agent)
        _emit("session.info", sid, info)
        return False, info
    return False, None


def _cfg_max_turns(cfg: dict, default: int) -> int:
    try:
        env_max = int(os.environ.get("TAKYON_TUI_MAX_TURNS", "") or 0)
        if env_max > 0:
            return env_max
    except (TypeError, ValueError):
        pass
    agent_cfg = cfg.get("agent") or {}
    return int(agent_cfg.get("max_turns") or cfg.get("max_turns") or default)


def _parse_tui_skills_env() -> list[str]:
    raw = os.environ.get("TAKYON_TUI_SKILLS", "")
    skills: list[str] = []
    seen: set[str] = set()
    for part in raw.replace("\n", ",").split(","):
        item = part.strip()
        if item and item not in seen:
            seen.add(item)
            skills.append(item)
    return skills


def _normalize_string_list(value: Any) -> list[str]:
    if isinstance(value, str):
        raw_items = re.split(r"[\n,]", value)
    elif isinstance(value, (list, tuple, set)):
        raw_items = list(value)
    else:
        raw_items = []
    items: list[str] = []
    seen: set[str] = set()
    for raw in raw_items:
        item = str(raw or "").strip()
        if not item or item in seen:
            continue
        seen.add(item)
        items.append(item)
    return items


def _coerce_bool(value: Any, *, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value).strip().lower()
    if not text:
        return default
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    return default


def _normalize_request_hostname(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    try:
        parsed = urlparse(raw if "://" in raw else f"//{raw}")
    except Exception:
        parsed = None
    hostname = str(getattr(parsed, "hostname", "") or "").strip().lower()
    if hostname:
        return hostname
    lowered = raw.lower()
    if lowered.startswith("[") and "]" in lowered:
        return lowered[1:].split("]", 1)[0].strip()
    return lowered.split(":", 1)[0].strip()


def _takyon_request_hostname(
    params: dict[str, Any] | None = None,
    *,
    session: dict[str, Any] | None = None,
    transport: Any | None = None,
) -> str:
    if isinstance(session, dict):
        cached = _normalize_request_hostname(session.get("takyon_request_host"))
        if cached:
            return cached
    bound = transport or current_transport() or (session or {}).get("transport")
    for candidate in (
        getattr(bound, "request_host", "") if bound is not None else "",
        getattr(bound, "request_origin", "") if bound is not None else "",
        (params or {}).get("_takyon_request_host"),
        (params or {}).get("_takyon_request_origin"),
    ):
        normalized = _normalize_request_hostname(candidate)
        if normalized:
            return normalized
    return ""


def _takyon_is_skill_lab_host(
    params: dict[str, Any] | None = None,
    *,
    session: dict[str, Any] | None = None,
    transport: Any | None = None,
) -> bool:
    return _takyon_request_hostname(
        params,
        session=session,
        transport=transport,
    ) == "skills.fourmanifold.com"


def _takyon_skill_lab_catalog() -> list[dict[str, Any]]:
    skills_root = Path(__file__).resolve().parents[1] / "skills" / "takyon"
    catalog: list[dict[str, Any]] = []
    for skill_file in sorted(skills_root.glob("*/SKILL.md")):
        try:
            text = skill_file.read_text(encoding="utf-8")
            meta = parse_frontmatter(text)[0]
        except Exception:
            meta = {}
        skill_name = str(meta.get("name") or skill_file.parent.name).strip()
        if not skill_name:
            continue
        hermes_meta = ((meta.get("metadata") or {}).get("hermes") or {})
        routing_meta = hermes_meta.get("routing") or {}
        catalog.append(
            {
                "name": skill_name,
                "slug": skill_file.parent.name,
                "description": str(meta.get("description") or "").strip(),
                "category": str(hermes_meta.get("category") or "").strip(),
                "owns": str(routing_meta.get("owns") or "").strip(),
                "path": str(skill_file),
            }
        )
    return catalog


def _build_takyon_skill_lab_prompt(
    skill_identifiers: list[str],
    *,
    session_id: str | None = None,
) -> tuple[str, list[str], list[str]]:
    from agent.skill_commands import build_preloaded_skills_prompt

    selected = _normalize_string_list(skill_identifiers)
    if not selected:
        return "", [], []
    catalog = _takyon_skill_lab_catalog()
    by_identifier: dict[str, str] = {}
    for item in catalog:
        name = str(item.get("name") or "").strip()
        slug = str(item.get("slug") or "").strip()
        path = str(item.get("path") or "").strip()
        if not path:
            continue
        if name:
            by_identifier.setdefault(name, path)
        if slug:
            by_identifier.setdefault(slug, path)
    resolved_identifiers = [by_identifier.get(item, item) for item in selected]

    skills_prompt, loaded_skills, missing_skills = build_preloaded_skills_prompt(
        resolved_identifiers,
        task_id=session_id,
    )
    session_note = (
        "You are in Takyon Skill Lab, a development test session for selected Takyon skills.\n"
        "If a dev business is already in scope, use the normal Takyon business rails for it.\n"
        "If no business is in scope, do not auto-bootstrap one just because a selected skill is normally business-scoped.\n"
        "If the active skill needs business state, credentials, receipts, or another authority gate, "
        "name the exact missing prerequisite and stop rather than inventing it.\n"
        "Keep the session chat-like and truthful: use real tools when useful, stream normally, and let the "
        "tool/activity feed reflect what actually happened."
    )
    parts = [session_note.strip()]
    if skills_prompt.strip():
        parts.append(skills_prompt.strip())
    return "\n\n".join(parts).strip(), loaded_skills, missing_skills


def _background_agent_kwargs(agent, task_id: str) -> dict:
    cfg = _load_cfg()

    return {
        "base_url": getattr(agent, "base_url", None) or None,
        "api_key": getattr(agent, "api_key", None) or None,
        "provider": getattr(agent, "provider", None) or None,
        "api_mode": getattr(agent, "api_mode", None) or None,
        "acp_command": getattr(agent, "acp_command", None) or None,
        "acp_args": getattr(agent, "acp_args", None) or None,
        "model": getattr(agent, "model", None) or _resolve_model(),
        "max_iterations": _cfg_max_turns(cfg, 25),
        "enabled_toolsets": getattr(agent, "enabled_toolsets", None)
        or _load_enabled_toolsets(),
        "quiet_mode": True,
        "verbose_logging": False,
        "ephemeral_system_prompt": getattr(agent, "ephemeral_system_prompt", None)
        or None,
        "providers_allowed": getattr(agent, "providers_allowed", None),
        "providers_ignored": getattr(agent, "providers_ignored", None),
        "providers_order": getattr(agent, "providers_order", None),
        "provider_sort": getattr(agent, "provider_sort", None),
        "provider_require_parameters": getattr(
            agent, "provider_require_parameters", False
        ),
        "provider_data_collection": getattr(agent, "provider_data_collection", None),
        "openrouter_min_coding_score": getattr(agent, "openrouter_min_coding_score", None),
        "session_id": task_id,
        "reasoning_config": getattr(agent, "reasoning_config", None)
        or _load_reasoning_config(),
        "service_tier": getattr(agent, "service_tier", None) or _load_service_tier(),
        "request_overrides": dict(getattr(agent, "request_overrides", {}) or {}),
        "platform": "tui",
        "session_db": _get_db(),
        "fallback_model": getattr(agent, "_fallback_model", None),
    }


def _reset_session_agent(sid: str, session: dict) -> dict:
    tokens = _set_session_context(
        session["session_key"], operator_user_id=_takyon_operator_user_id(session)
    )
    try:
        new_agent = _make_agent(
            sid, session["session_key"], session_id=session["session_key"]
        )
    finally:
        _clear_session_context(tokens)
    session["agent"] = new_agent
    session["attached_images"] = []
    session["edit_snapshots"] = {}
    session["image_counter"] = 0
    session["running"] = False
    session["show_reasoning"] = _load_show_reasoning()
    session["tool_progress_mode"] = _load_tool_progress_mode()
    session["tool_started_at"] = {}
    with session["history_lock"]:
        session["history"] = []
        session["history_version"] = int(session.get("history_version", 0)) + 1
    info = _session_info(new_agent)
    _emit("session.info", sid, info)
    _restart_slash_worker(session)
    return info


def _make_agent(sid: str, key: str, session_id: str | None = None):
    from plugins.takyon.operator_gateway import build_operator_gateway_agent
    from takyon_cli.runtime_provider import resolve_runtime_provider

    cfg = _load_cfg()
    agent_cfg = cfg.get("agent") or {}
    system_prompt = (agent_cfg.get("system_prompt", "") or "").strip()
    startup_skills = _parse_tui_skills_env()
    if startup_skills:
        from agent.skill_commands import build_preloaded_skills_prompt

        skills_prompt, _loaded_skills, missing_skills = build_preloaded_skills_prompt(
            startup_skills,
            task_id=session_id or key,
        )
        if missing_skills:
            raise ValueError(f"Unknown skill(s): {', '.join(missing_skills)}")
        if skills_prompt:
            system_prompt = "\n\n".join(
                part for part in (system_prompt, skills_prompt) if part
            ).strip()
    model, requested_provider = _resolve_startup_runtime()
    runtime = resolve_runtime_provider(
        requested=requested_provider,
        target_model=model or None,
    )
    return build_operator_gateway_agent(
        runtime=runtime,
        model=model,
        operator_user_id="",
        business_slug="",
        agent_kwargs={
            "max_iterations": _cfg_max_turns(cfg, 90),
            "quiet_mode": True,
            "verbose_logging": _load_tool_progress_mode() == "verbose",
            "reasoning_config": _load_reasoning_config(),
            "service_tier": _load_service_tier(),
            "enabled_toolsets": list(_TAKYON_AGENT_TOOLSETS),
            "disabled_toolsets": list(_TAKYON_DISABLED_TOOLSETS),
            "platform": "tui",
            "session_id": session_id or key,
            "session_db": _get_db(),
            "ephemeral_system_prompt": system_prompt or None,
            "checkpoints_enabled": is_truthy_value(os.environ.get("TAKYON_TUI_CHECKPOINTS")),
            "pass_session_id": is_truthy_value(os.environ.get("TAKYON_TUI_PASS_SESSION_ID")),
            "skip_context_files": is_truthy_value(os.environ.get("TAKYON_IGNORE_RULES")),
            "skip_memory": is_truthy_value(os.environ.get("TAKYON_IGNORE_RULES")),
            **_agent_cbs(sid),
        },
    )


def _init_session(
    sid: str, key: str, agent, history: list, cols: int = 80, operator_user_id: str = ""
):
    _sessions[sid] = {
        "agent": agent,
        "session_key": key,
        # The session principal — resumed/branched sessions must carry it exactly like freshly
        # created ones, never re-derive it from process-global env (per-session identity planes
        # ignore TAKYON_OPERATOR_USER_ID; see core.operator_identity_mode).
        "takyon_operator_user_id": str(operator_user_id or "").strip(),
        "history": history,
        "history_lock": threading.Lock(),
        "history_version": 0,
        "running": False,
        "attached_images": [],
        "image_counter": 0,
        "cols": cols,
        "slash_worker": None,
        "show_reasoning": _load_show_reasoning(),
        "tool_progress_mode": _load_tool_progress_mode(),
        "edit_snapshots": {},
        "tool_started_at": {},
        # Pin async event emissions to whichever transport created the
        # session (stdio for Ink, JSON-RPC WS for the dashboard sidebar).
        "transport": current_transport() or _stdio_transport,
    }
    try:
        _sessions[sid]["slash_worker"] = _SlashWorker(
            key,
            getattr(agent, "model", _resolve_model()),
            operator_user_id=str(operator_user_id or "").strip(),
        )
    except Exception:
        # Defer hard-failure to slash.exec; chat still works without slash worker.
        _sessions[sid]["slash_worker"] = None
    try:
        from tools.approval import register_gateway_notify, load_permanent_allowlist

        register_gateway_notify(key, lambda data: _emit("approval.request", sid, data))
        load_permanent_allowlist()
    except Exception:
        pass
    # Surface the self-improvement background review's "💾 …" summary as a
    # review.summary event so Ink can render it as a persistent system line
    # in the transcript. In the CLI path this message is printed via
    # prompt_toolkit; the TUI has no equivalent print surface, so without
    # this callback the review would write the skill/memory change silently.
    try:
        agent.background_review_callback = lambda message, _sid=sid: _emit(
            "review.summary", _sid, {"text": str(message)}
        )
    except Exception:
        # Bare AIAgents that don't expose the attribute (unlikely, but keep
        # session startup resilient).
        pass
    _wire_callbacks(sid)
    _sessions[sid]["_notif_stop"] = _start_notification_poller(sid, _sessions[sid])
    _notify_session_boundary("on_session_reset", key)
    _emit("session.info", sid, _session_info(agent))


def _new_session_key() -> str:
    return f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"


def _with_checkpoints(session, fn):
    return fn(session["agent"]._checkpoint_mgr, os.getenv("TERMINAL_CWD", os.getcwd()))


def _resolve_checkpoint_hash(mgr, cwd: str, ref: str) -> str:
    try:
        checkpoints = mgr.list_checkpoints(cwd)
        idx = int(ref) - 1
    except ValueError:
        return ref
    if 0 <= idx < len(checkpoints):
        return checkpoints[idx].get("hash", ref)
    raise ValueError(f"Invalid checkpoint number. Use 1-{len(checkpoints)}.")


def _enrich_with_attached_images(user_text: str, image_paths: list[str]) -> str:
    """Pre-analyze attached images via vision and prepend descriptions to user text."""
    import asyncio, json as _json
    from tools.vision_tools import vision_analyze_tool

    prompt = (
        "Describe everything visible in this image in thorough detail. "
        "Include any text, code, data, objects, people, layout, colors, "
        "and any other notable visual information."
    )

    parts: list[str] = []
    for path in image_paths:
        p = Path(path)
        if not p.exists():
            continue
        hint = f"[You can examine it with vision_analyze using image_url: {p}]"
        try:
            r = _json.loads(
                asyncio.run(vision_analyze_tool(image_url=str(p), user_prompt=prompt))
            )
            desc = r.get("analysis", "") if r.get("success") else None
            parts.append(
                f"[The user attached an image:\n{desc}]\n{hint}"
                if desc
                else f"[The user attached an image but analysis failed.]\n{hint}"
            )
        except Exception:
            parts.append(f"[The user attached an image but analysis failed.]\n{hint}")

    text = user_text or ""
    prefix = "\n\n".join(parts)
    if prefix:
        return f"{prefix}\n\n{text}" if text else prefix
    return text or "What do you see in this image?"


def _content_display_text(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, (int, float)):
        return str(content)
    if isinstance(content, list):
        parts = []
        for part in content:
            text = _content_display_text(part).strip()
            if text:
                parts.append(text)
        return "\n".join(parts)
    if isinstance(content, dict):
        kind = content.get("type")
        if kind in {"text", "input_text", "output_text"}:
            return str(content.get("text") or content.get("content") or "")
        if kind in {"image_url", "input_image", "image"}:
            return "[image]"
        if kind in {"input_audio", "audio"}:
            return "[audio]"
        if kind:
            return f"[{kind}]"
        if "text" in content:
            return str(content.get("text") or "")
        return "[structured content]"
    return str(content)


_TAKYON_LEGACY_CREATE_TEST_MODE_PREFIX = (
    "Operator UI preference: create any new business in test mode unless the operator explicitly asks for live mode."
)
_TAKYON_BUDGET_GUARD_PREFIX = (
    "Budget guard: the operator appears to be asking for a new business but did not state a budget."
)
_TAKYON_SCOPED_OPERATOR_SUFFIX = (
    "\n\nFirst read this business state with Takyon business tools. Honor the business work_focus field "
    "if it is marketing-only or product-only. Keep all durable writes business-scoped."
)
_TAKYON_GLOBAL_OPERATOR_SUFFIX = (
    "\n\nUse global reads for businesses, credentials, policy, skills, and budgets. "
    "For any business/product/customer state change, create or select the business and use concrete business_* tools."
)


def _sanitize_user_history_display_text(text: str) -> str:
    value = str(text or "")
    changed = True
    while changed:
        changed = False
        if value.startswith(_TAKYON_LEGACY_CREATE_TEST_MODE_PREFIX + "\n\n"):
            value = value[len(_TAKYON_LEGACY_CREATE_TEST_MODE_PREFIX + "\n\n") :]
            changed = True
            continue
        if value.startswith(_TAKYON_BUDGET_GUARD_PREFIX):
            parts = value.split("\n\n", 1)
            if len(parts) == 2:
                value = parts[1]
                changed = True
                continue

    marker = "\n\nOperator request:\n"
    if value.startswith("Scope: business:") and marker in value and value.endswith(
        _TAKYON_SCOPED_OPERATOR_SUFFIX
    ):
        head, request = value.split(marker, 1)
        if "\nCEO role: scoped business operator." in head:
            return request[: -len(_TAKYON_SCOPED_OPERATOR_SUFFIX)]
    if value.startswith("Scope: global") and marker in value and value.endswith(
        _TAKYON_GLOBAL_OPERATOR_SUFFIX
    ):
        head, request = value.split(marker, 1)
        if "\nCEO role: account/root-scope operator." in head:
            return request[: -len(_TAKYON_GLOBAL_OPERATOR_SUFFIX)]
    return value


def _history_to_messages(history: list[dict]) -> list[dict]:
    messages = []
    tool_call_args = {}
    previous_user_text = ""
    previous_user_was_wrapped = False

    for m in history:
        if not isinstance(m, dict):
            continue
        role = m.get("role")
        if role not in {"user", "assistant", "tool", "system"}:
            continue
        if role == "user" and isinstance(m.get("display_text"), str) and m.get("display_text"):
            content_text = str(m.get("display_text"))
        else:
            content_text = _content_display_text(m.get("content"))
        if role == "user":
            raw_user_text = content_text
            content_text = _sanitize_user_history_display_text(content_text)
            user_was_wrapped = content_text != raw_user_text
            if (
                content_text.strip()
                and previous_user_text == content_text
                and (user_was_wrapped or previous_user_was_wrapped)
            ):
                previous_user_was_wrapped = previous_user_was_wrapped or user_was_wrapped
                continue
        if role == "assistant" and m.get("tool_calls"):
            for tc in m["tool_calls"]:
                fn = tc.get("function", {})
                tc_id = tc.get("id", "")
                if tc_id and fn.get("name"):
                    try:
                        args = json.loads(fn.get("arguments", "{}"))
                    except (json.JSONDecodeError, TypeError):
                        args = {}
                    tool_call_args[tc_id] = (fn["name"], args)
            if not content_text.strip():
                continue
        if role == "tool":
            tc_id = m.get("tool_call_id", "")
            tc_info = tool_call_args.get(tc_id) if tc_id else None
            name = (tc_info[0] if tc_info else None) or m.get("tool_name") or "tool"
            args = (tc_info[1] if tc_info else None) or {}
            messages.append(
                {"role": "tool", "name": name, "context": _tool_ctx(name, args)}
            )
            continue
        if not content_text.strip():
            continue
        messages.append({"role": role, "text": content_text})
        if role == "user":
            previous_user_text = content_text
            previous_user_was_wrapped = user_was_wrapped
        else:
            previous_user_text = ""
            previous_user_was_wrapped = False

    return messages


# ── Methods: session ─────────────────────────────────────────────────


def _resolve_session_operator_user_id(params: dict | None, transport) -> str:
    """Resolve the operator principal for a new TUI session, most-authenticated source first:

    1. the transport's authenticated principal (the /api/ws upgrade resolved it from the dashboard
       auth session) — a client-supplied param can NEVER override it;
    2. the ``_takyon_operator_user_id`` param — only reaches here on transports without a principal:
       the dashboard HTTP-RPC ingress strips any client value and injects the authenticated
       principal server-side, and a local stdio client is the same-user trusted shell;
    3. per-session TAKYON_SESSION_USER_ID (contextvar with env fallback — the per-session PTY child
       carries it from the dashboard's authenticated spawn);
    4. legacy process-global TAKYON_OPERATOR_USER_ID, ONLY on planes that have not declared
       per-session identity (core.operator_identity_mode() == "") — a process-wide env value must
       never satisfy a per-session dashboard principal."""
    principal = getattr(transport, "operator_principal", None)
    principal_user = str(getattr(principal, "user_id", "") or "").strip()
    if principal_user:
        return principal_user
    param_user = str((params or {}).get("_takyon_operator_user_id") or "").strip()
    if param_user:
        return param_user
    try:
        from gateway.session_context import get_session_env

        session_user = str(get_session_env("TAKYON_SESSION_USER_ID", "") or "").strip()
    except Exception:
        session_user = ""
    if session_user:
        return session_user
    from plugins.takyon.core import operator_identity_mode

    if operator_identity_mode():
        return ""
    return str(os.getenv("TAKYON_OPERATOR_USER_ID") or "").strip()


@method("session.create")
def _(rid, params: dict) -> dict:
    sid = uuid.uuid4().hex[:8]
    key = _new_session_key()
    cols = int(params.get("cols", 80))
    boot_business = str(params.get("_takyon_boot_business") or "").strip()
    skill_lab_skills = _normalize_string_list(params.get("_takyon_skill_lab_skills"))
    skill_lab_prompt = ""
    loaded_skill_lab_skills: list[str] = []
    _enable_gateway_prompts()
    transport = current_transport() or _stdio_transport
    request_host = _takyon_request_hostname(params, transport=transport)
    operator_user_id = _resolve_session_operator_user_id(params, transport)
    if not operator_user_id:
        logger.warning("takyon session.create without operator_user_id")
    if skill_lab_skills:
        if not _takyon_is_skill_lab_host(params, transport=transport):
            return _err(
                rid,
                4047,
                "Skill Lab is available only on skills.fourmanifold.com",
            )
        (
            skill_lab_prompt,
            loaded_skill_lab_skills,
            missing_skill_lab_skills,
        ) = _build_takyon_skill_lab_prompt(skill_lab_skills, session_id=key)
        if missing_skill_lab_skills:
            return _err(
                rid,
                4046,
                f"Unknown skill(s): {', '.join(missing_skill_lab_skills)}",
            )

    ready = threading.Event()

    session = {
        "agent": None,
        "agent_error": None,
        "agent_ready": ready,
        "attached_images": [],
        "cols": cols,
        "edit_snapshots": {},
        "history": [],
        "history_lock": threading.Lock(),
        "history_version": 0,
        "image_counter": 0,
        "pending_title": None,
        "running": False,
        "session_key": key,
        "show_reasoning": _load_show_reasoning(),
        "slash_worker": None,
        "takyon_operator_user_id": operator_user_id,
        "takyon_request_host": request_host,
        "tool_progress_mode": _load_tool_progress_mode(),
        "tool_started_at": {},
        "transport": transport,
    }
    if skill_lab_prompt:
        session["takyon_skill_lab"] = {
            "requested_skills": skill_lab_skills,
            "skills": loaded_skill_lab_skills,
            "prompt": skill_lab_prompt,
        }
    _sessions[sid] = session
    boot_result = {
        "requested_business": "",
        "accepted": False,
        "reason": "",
    }
    if boot_business:
        try:
            from plugins.takyon.cli import _slugify

            slug = _slugify(boot_business)
            logger.warning(
                "takyon session.create boot requested business=%s operator_user_id=%s",
                slug,
                operator_user_id,
            )
            boot_result["requested_business"] = slug
            if _takyon_can_access_business(session, slug):
                session["takyon_current_business"] = slug
                boot_result["accepted"] = True
            else:
                visible_businesses = [
                    str(item.get("slug") or "").strip()
                    for item in _takyon_businesses_for_session(session)
                    if isinstance(item, dict)
                ]
                boot_result["reason"] = (
                    f"Could not open business:{slug}. "
                    + (
                        "No businesses are visible for this account."
                        if not visible_businesses
                        else "That business is not available to this account."
                    )
                )
                logger.warning(
                    "takyon session.create boot denied business=%s operator_user_id=%s visible_businesses=%s",
                    slug,
                    operator_user_id,
                    visible_businesses,
                )
        except Exception as exc:
            boot_result["reason"] = str(exc)
            logger.warning(
                "takyon session.create boot failed business=%s operator_user_id=%s error=%s",
                boot_business,
                operator_user_id,
                exc,
            )

    # Return the lightweight session immediately so Ink can paint the composer
    # + skeleton panel, then build the real AIAgent just after this response is
    # flushed.  This keeps startup responsive while still hydrating tools/skills
    # without requiring the user to submit a first prompt.
    def _deferred_build() -> None:
        session = _sessions.get(sid)
        if session is not None:
            _start_agent_build(sid, session)

    build_timer = threading.Timer(0.05, _deferred_build)
    build_timer.daemon = True
    build_timer.start()

    return _ok(
        rid,
        {
            "session_id": sid,
            "takyon_boot": boot_result,
            "takyon_skill_lab": {
                "enabled": bool(skill_lab_prompt),
                "skills": loaded_skill_lab_skills,
            },
            "info": {
                "model": _resolve_model(),
                "tools": {},
                "skills": {},
                "cwd": os.getenv("TERMINAL_CWD", os.getcwd()),
                "lazy": True,
                "profile_name": _current_profile_name(),
            },
        },
    )


@method("session.list")
def _(rid, params: dict) -> dict:
    db = _get_db()
    if db is None:
        return _db_unavailable_error(rid, code=5006)
    try:
        # Resume picker should surface human conversation sessions from every
        # user-facing surface — CLI, TUI, all gateway platforms (including new
        # ones not enumerated here), ACP adapter clients, webhook sessions,
        # custom `TAKYON_SESSION_SOURCE` values, and older installs with
        # different source labels. We deny-list only the noisy internal
        # sources (``tool`` sub-agent runs) rather than allow-listing a
        # fixed set of platform names that goes stale whenever a new
        # platform is added or a user names their own source.
        deny = frozenset({"tool"})

        limit = int(params.get("limit", 200) or 200)
        # Over-fetch modestly so per-source filtering doesn't leave us
        # short; the compression-tip projection in ``list_sessions_rich``
        # can also merge rows.
        fetch_limit = max(limit * 2, 200)
        rows = [
            s
            for s in db.list_sessions_rich(source=None, limit=fetch_limit)
            if (s.get("source") or "").strip().lower() not in deny
        ][:limit]
        return _ok(
            rid,
            {
                "sessions": [
                    {
                        "id": s["id"],
                        "title": s.get("title") or "",
                        "preview": s.get("preview") or "",
                        "started_at": s.get("started_at") or 0,
                        "message_count": s.get("message_count") or 0,
                        "source": s.get("source") or "",
                    }
                    for s in rows
                ]
            },
        )
    except Exception as e:
        return _err(rid, 5006, str(e))


@method("session.most_recent")
def _(rid, params: dict) -> dict:
    """Return the most recent human-facing session id, or ``None``.

    Mirrors ``session.list``'s deny-list behaviour (drops ``tool``
    sub-agent rows).  Used by TUI auto-resume when
    ``display.tui_auto_resume_recent`` is on; the field is also handy
    for any CLI tooling that wants "latest session" without paginating
    the full list.

    Contract: a ``{"session_id": null}`` result means "no eligible
    session found right now".  Errors are also folded into that
    null-result shape (and logged) so callers don't have to special-
    case JSON-RPC error envelopes for what is a normal "no answer".
    """
    db = _get_db()
    if db is None:
        return _ok(rid, {"session_id": None})
    try:
        deny = frozenset({"tool"})
        # Over-fetch by a generous bounded amount so heavy sub-agent
        # users (lots of recent ``tool`` rows) don't get a false
        # "no eligible session" answer.  ``session.list`` uses a
        # similar over-fetch strategy.
        rows = db.list_sessions_rich(source=None, limit=200)
        for row in rows:
            src = (row.get("source") or "").strip().lower()
            if src in deny:
                continue
            return _ok(
                rid,
                {
                    "session_id": row.get("id"),
                    "title": row.get("title") or "",
                    "started_at": row.get("started_at") or 0,
                    "source": row.get("source") or "",
                },
            )
        return _ok(rid, {"session_id": None})
    except Exception:
        logger.exception("session.most_recent failed")
        return _ok(rid, {"session_id": None})


@method("session.resume")
def _(rid, params: dict) -> dict:
    target = params.get("session_id", "")
    boot_business = str(params.get("_takyon_boot_business") or "").strip()
    if not target:
        return _err(rid, 4006, "session_id required")
    db = _get_db()
    if db is None:
        return _db_unavailable_error(rid, code=5000)
    found = db.get_session(target)
    if not found:
        found = db.get_session_by_title(target)
        if found:
            target = found["id"]
        else:
            return _err(rid, 4007, "session not found")
    sid = uuid.uuid4().hex[:8]
    _enable_gateway_prompts()
    # Resume resolves the principal exactly like session.create — from the authenticated
    # transport/server-injected param/session env — so a resumed dashboard session is never an
    # identity-less session that only worked through a process-global env backdoor.
    operator_user_id = _resolve_session_operator_user_id(
        params, current_transport() or _stdio_transport
    )
    try:
        db.reopen_session(target)
        history = db.get_messages_as_conversation(target)
        display_history = db.get_messages_as_conversation(
            target, include_ancestors=True
        )
        messages = _history_to_messages(display_history)
        tokens = _set_session_context(target, operator_user_id=operator_user_id)
        try:
            agent = _make_agent(sid, target, session_id=target)
        finally:
            _clear_session_context(tokens)
        _init_session(
            sid,
            target,
            agent,
            history,
            cols=int(params.get("cols", 80)),
            operator_user_id=operator_user_id,
        )
        if boot_business:
            try:
                from plugins.takyon.cli import _slugify

                slug = _slugify(boot_business)
                if _takyon_can_access_business(_sessions.get(sid), slug):
                    _sessions[sid]["takyon_current_business"] = slug
            except Exception:
                pass
    except Exception as e:
        return _err(rid, 5000, f"resume failed: {e}")
    return _ok(
        rid,
        {
            "session_id": sid,
            "resumed": target,
            "message_count": len(messages),
            "messages": messages,
            "info": _session_info(agent),
        },
    )


@method("session.delete")
def _(rid, params: dict) -> dict:
    """Delete a stored session and its on-disk transcript files.

    Used by the TUI resume picker (``d`` key) so users can prune old
    sessions without dropping to the CLI.  Refuses to delete a session
    that is currently active in this gateway process — those rows are
    still being written to and removing them out from under the live
    agent corrupts message ordering and trips FK constraints when the
    next message append flushes.
    """
    target = params.get("session_id", "")
    if not target:
        return _err(rid, 4006, "session_id required")
    db = _get_db()
    if db is None:
        return _db_unavailable_error(rid, code=5036)
    # Block deletion of any session currently bound to a live TUI session
    # in this process.  The picker hides the active session anyway, but a
    # racing caller could still target it.  Snapshot via ``list(...)``
    # because ``_sessions`` is mutated by concurrent RPCs on the thread
    # pool — iterating the dict directly can raise ``RuntimeError:
    # dictionary changed size during iteration``.  If even the snapshot
    # raises, fail closed (refuse the delete) rather than fail open.
    try:
        snapshot = list(_sessions.values())
    except Exception as e:
        return _err(rid, 5036, f"could not enumerate active sessions: {e}")
    active = {s.get("session_key") for s in snapshot if s.get("session_key")}
    if target in active:
        return _err(rid, 4023, "cannot delete an active session")
    sessions_dir = get_takyon_home() / "sessions"
    try:
        deleted = db.delete_session(target, sessions_dir=sessions_dir)
    except Exception as e:
        return _err(rid, 5036, f"delete failed: {e}")
    if not deleted:
        return _err(rid, 4007, "session not found")
    return _ok(rid, {"deleted": target})


@method("session.title")
def _(rid, params: dict) -> dict:
    session, err = _sess_nowait(params, rid)
    if err:
        return err
    db = _get_db()
    if db is None:
        return _db_unavailable_error(rid, code=5007)
    key = session["session_key"]
    if "title" not in params:
        fallback = session.get("pending_title") or ""
        try:
            resolved_title = db.get_session_title(key) or ""
            if fallback:
                if db.set_session_title(key, fallback):
                    session["pending_title"] = None
                    resolved_title = fallback
                else:
                    existing_row = db.get_session(key)
                    existing_title = ((existing_row or {}).get("title") or "").strip()
                    if existing_title == fallback:
                        session["pending_title"] = None
                        resolved_title = fallback
                    elif not resolved_title:
                        resolved_title = fallback
            elif resolved_title:
                session["pending_title"] = None
        except Exception:
            resolved_title = fallback
        return _ok(
            rid,
            {
                "title": resolved_title,
                "session_key": key,
            },
        )
    title = (params.get("title", "") or "").strip()
    if not title:
        return _err(rid, 4021, "title required")
    try:
        if db.set_session_title(key, title):
            session["pending_title"] = None
            return _ok(rid, {"pending": False, "title": title})
        # rowcount == 0 can mean "same value" as well as "missing row".
        # Queue only when the session row truly does not exist yet.
        existing_row = db.get_session(key)
        if existing_row:
            session["pending_title"] = None
            return _ok(
                rid,
                {
                    "pending": False,
                    "title": (existing_row.get("title") or title),
                },
            )
        session["pending_title"] = title
        return _ok(rid, {"pending": True, "title": title})
    except ValueError as e:
        return _err(rid, 4022, str(e))
    except Exception as e:
        return _err(rid, 5007, str(e))


@method("session.usage")
def _(rid, params: dict) -> dict:
    session, err = _sess_nowait(params, rid)
    if err:
        return err
    agent = session.get("agent")
    return _ok(
        rid,
        (
            _get_usage(agent)
            if agent is not None
            else {"calls": 0, "input": 0, "output": 0, "total": 0}
        ),
    )


@method("session.status")
def _(rid, params: dict) -> dict:
    session, err = _sess_nowait(params, rid)
    if err:
        return err

    from takyon_constants import display_takyon_home

    key = session.get("session_key") or params.get("session_id") or ""
    agent = session.get("agent")
    meta = {}
    db = _get_db()
    if db and key:
        try:
            meta = db.get_session(key) or {}
        except Exception:
            meta = {}

    def _dt(value, fallback: datetime | None = None) -> datetime:
        if value:
            try:
                return datetime.fromtimestamp(float(value))
            except Exception:
                pass
        return fallback or datetime.now()

    created = _dt(meta.get("started_at"))
    updated = created
    for field in ("updated_at", "last_updated_at", "last_activity_at"):
        if meta.get(field):
            updated = _dt(meta.get(field), created)
            break

    usage = _get_usage(agent) if agent is not None else {}
    provider = getattr(agent, "provider", None) or "unknown"
    model = getattr(agent, "model", None) or "(unknown)"
    lines = [
        "Takyon TUI Status",
        "",
        f"Session ID: {key}",
        f"Path: {display_takyon_home()}",
    ]
    title = (meta.get("title") or "").strip()
    if title:
        lines.append(f"Title: {title}")
    lines.extend(
        [
            f"Model: {model} ({provider})",
            f"Created: {created.strftime('%Y-%m-%d %H:%M')}",
            f"Last Activity: {updated.strftime('%Y-%m-%d %H:%M')}",
            f"Tokens: {int(usage.get('total') or 0):,}",
            f"Agent Running: {'Yes' if session.get('running') else 'No'}",
        ]
    )
    return _ok(rid, {"output": "\n".join(lines)})


@method("session.history")
def _(rid, params: dict) -> dict:
    session, err = _sess_nowait(params, rid)
    if err:
        return err
    history = list(session.get("history", []))
    history_memory_only = bool(session.get("history_memory_only"))
    db = _get_db()
    if not history_memory_only and db is not None and session.get("session_key"):
        try:
            history = db.get_messages_as_conversation(
                session["session_key"], include_ancestors=True
            )
        except Exception:
            pass
    return _ok(
        rid,
        {
            "count": len(history),
            "messages": _history_to_messages(history),
            "running": bool(session.get("running")),
        },
    )


@method("session.undo")
def _(rid, params: dict) -> dict:
    session, err = _sess(params, rid)
    if err:
        return err
    # Reject during an in-flight turn.  If we mutated history while
    # the agent thread is running, prompt.submit's post-run history
    # write would either clobber the undo (version matches) or
    # silently drop the agent's output (version mismatch, see below).
    # Neither is what the user wants — make them /interrupt first.
    if session.get("running"):
        return _err(
            rid, 4009, "session busy — /interrupt the current turn before /undo"
        )
    removed = 0
    with session["history_lock"]:
        history = session.get("history", [])
        while history and history[-1].get("role") in {"assistant", "tool"}:
            history.pop()
            removed += 1
        if history and history[-1].get("role") == "user":
            history.pop()
            removed += 1
        if removed:
            session["history_version"] = int(session.get("history_version", 0)) + 1
    return _ok(rid, {"removed": removed})


@method("session.compress")
def _(rid, params: dict) -> dict:
    session, err = _sess(params, rid)
    if err:
        return err
    if session.get("running"):
        return _err(
            rid, 4009, "session busy — /interrupt the current turn before /compress"
        )
    sid = params.get("session_id", "")
    focus_topic = str(params.get("focus_topic", "") or "").strip()
    try:
        from agent.manual_compression_feedback import summarize_manual_compression
        from agent.model_metadata import estimate_request_tokens_rough

        with session["history_lock"]:
            before_messages = list(session.get("history", []))
            history_version = int(session.get("history_version", 0))
        before_count = len(before_messages)
        _agent = session["agent"]
        _sys_prompt = getattr(_agent, "_cached_system_prompt", "") or ""
        _tools = getattr(_agent, "tools", None) or None
        before_tokens = (
            estimate_request_tokens_rough(
                before_messages, system_prompt=_sys_prompt, tools=_tools
            )
            if before_count
            else 0
        )

        if before_count >= 4:
            focus_suffix = f', focus: "{focus_topic}"' if focus_topic else ""
            _status_update(
                sid,
                "compressing",
                f"⠋ compressing {before_count} messages "
                f"(~{before_tokens:,} tok){focus_suffix}…",
            )

        try:
            removed, usage = _compress_session_history(
                session,
                focus_topic,
                approx_tokens=before_tokens,
                before_messages=before_messages,
                history_version=history_version,
            )
            with session["history_lock"]:
                messages = list(session.get("history", []))
            after_count = len(messages)
            # Re-read system prompt + tools after compression — _compress_context
            # may have rebuilt the system prompt (_cached_system_prompt=None).
            _sys_prompt_after = (
                getattr(_agent, "_cached_system_prompt", "") or _sys_prompt
            )
            _tools_after = getattr(_agent, "tools", None) or _tools
            after_tokens = (
                estimate_request_tokens_rough(
                    messages,
                    system_prompt=_sys_prompt_after,
                    tools=_tools_after,
                )
                if after_count
                else 0
            )
            agent = session["agent"]
            _sync_session_key_after_compress(sid, session)
            summary = summarize_manual_compression(
                before_messages, messages, before_tokens, after_tokens
            )
            info = _session_info(agent)
            _emit("session.info", sid, info)
            return _ok(
                rid,
                {
                    "status": "compressed",
                    "removed": removed,
                    "before_messages": before_count,
                    "after_messages": after_count,
                    "before_tokens": before_tokens,
                    "after_tokens": after_tokens,
                    "summary": summary,
                    "usage": usage,
                    "info": info,
                    "messages": messages,
                },
            )
        finally:
            # Always clear the pinned compressing status so the bar
            # reverts to neutral whether compaction succeeded, was a
            # no-op, or raised.
            _status_update(sid, "ready")
    except Exception as e:
        return _err(rid, 5005, str(e))


@method("session.save")
def _(rid, params: dict) -> dict:
    session, err = _sess(params, rid)
    if err:
        return err
    import time as _time

    filename = os.path.abspath(
        f"takyon_conversation_{_time.strftime('%Y%m%d_%H%M%S')}.json"
    )
    try:
        with open(filename, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "model": getattr(session["agent"], "model", ""),
                    "messages": session.get("history", []),
                },
                f,
                indent=2,
                ensure_ascii=False,
            )
        return _ok(rid, {"file": filename})
    except Exception as e:
        return _err(rid, 5011, str(e))


@method("session.close")
def _(rid, params: dict) -> dict:
    sid = params.get("session_id", "")
    session = _sessions.pop(sid, None)
    if not session:
        return _ok(rid, {"closed": False})
    _finalize_session(session)
    try:
        _terminate_isolated_turn_proc(session.get("takyon_turn_proc"))
    except Exception:
        pass
    try:
        from tools.approval import unregister_gateway_notify

        unregister_gateway_notify(session["session_key"])
    except Exception:
        pass
    try:
        agent = session.get("agent")
        if agent and hasattr(agent, "close"):
            agent.close()
    except Exception:
        pass
    try:
        worker = session.get("slash_worker")
        if worker:
            worker.close()
    except Exception:
        pass
    return _ok(rid, {"closed": True})


@method("session.branch")
def _(rid, params: dict) -> dict:
    session, err = _sess(params, rid)
    if err:
        return err
    db = _get_db()
    if db is None:
        return _db_unavailable_error(rid, code=5008)
    old_key = session["session_key"]
    with session["history_lock"]:
        history = [dict(msg) for msg in session.get("history", [])]
    if not history:
        return _err(rid, 4008, "nothing to branch — send a message first")
    new_key = _new_session_key()
    branch_name = params.get("name", "")
    try:
        if branch_name:
            title = branch_name
        else:
            current = db.get_session_title(old_key) or "branch"
            title = (
                db.get_next_title_in_lineage(current)
                if hasattr(db, "get_next_title_in_lineage")
                else f"{current} (branch)"
            )
        db.create_session(
            new_key, source="tui", model=_resolve_model(), parent_session_id=old_key
        )
        for msg in history:
            db.append_message(
                session_id=new_key,
                role=msg.get("role", "user"),
                content=msg.get("content"),
            )
        db.set_session_title(new_key, title)
    except Exception as e:
        return _err(rid, 5008, f"branch failed: {e}")
    new_sid = uuid.uuid4().hex[:8]
    try:
        # A branch inherits the parent session's principal — same operator, same authority.
        branch_operator_user_id = _takyon_operator_user_id(session)
        tokens = _set_session_context(new_key, operator_user_id=branch_operator_user_id)
        try:
            agent = _make_agent(new_sid, new_key, session_id=new_key)
        finally:
            _clear_session_context(tokens)
        _init_session(
            new_sid,
            new_key,
            agent,
            list(history),
            cols=session.get("cols", 80),
            operator_user_id=branch_operator_user_id,
        )
    except Exception as e:
        return _err(rid, 5000, f"agent init failed on branch: {e}")
    return _ok(rid, {"session_id": new_sid, "title": title, "parent": old_key})


@method("session.interrupt")
def _(rid, params: dict) -> dict:
    session, err = _sess(params, rid)
    if err:
        return err
    session["takyon_turn_interrupted"] = True
    try:
        _terminate_isolated_turn_proc(session.get("takyon_turn_proc"))
    except Exception:
        pass
    if hasattr(session["agent"], "interrupt"):
        session["agent"].interrupt()
    # Scope the pending-prompt release to THIS session.  A global
    # _clear_pending() would collaterally cancel clarify/sudo/secret
    # prompts on unrelated sessions sharing the same tui_gateway
    # process, silently resolving them to empty strings.
    _clear_pending(params.get("session_id", ""))
    try:
        from tools.approval import resolve_gateway_approval

        resolve_gateway_approval(session["session_key"], "deny", resolve_all=True)
    except Exception:
        pass
    return _ok(rid, {"status": "interrupted"})


# ── Delegation: subagent tree observability + controls ───────────────
# Powers the TUI's /agents overlay (see ui-tui/src/components/agentsOverlay).
# The registry lives in tools/delegate_tool — these handlers are thin
# translators between JSON-RPC and the Python API.


@method("delegation.status")
def _(rid, params: dict) -> dict:
    from tools.delegate_tool import (
        is_spawn_paused,
        list_active_subagents,
        _get_max_concurrent_children,
        _get_max_spawn_depth,
    )

    return _ok(
        rid,
        {
            "active": list_active_subagents(),
            "paused": is_spawn_paused(),
            "max_spawn_depth": _get_max_spawn_depth(),
            "max_concurrent_children": _get_max_concurrent_children(),
        },
    )


@method("delegation.pause")
def _(rid, params: dict) -> dict:
    from tools.delegate_tool import set_spawn_paused

    paused = bool(params.get("paused", True))
    return _ok(rid, {"paused": set_spawn_paused(paused)})


@method("subagent.interrupt")
def _(rid, params: dict) -> dict:
    from tools.delegate_tool import interrupt_subagent

    subagent_id = str(params.get("subagent_id") or "").strip()
    if not subagent_id:
        return _err(rid, 4000, "subagent_id required")
    ok = interrupt_subagent(subagent_id)
    return _ok(rid, {"found": ok, "subagent_id": subagent_id})


# ── Spawn-tree snapshots: TUI-written, disk-persisted ────────────────
# The TUI is the source of truth for subagent state (it assembles payloads
# from the event stream).  On turn-complete it posts the final tree here;
# /replay and /replay-diff fetch past snapshots by session_id + filename.
#
# Layout:  $TAKYON_HOME/spawn-trees/<session_id>/<timestamp>.json
# Each file contains { session_id, started_at, finished_at, subagents: [...] }.


def _spawn_trees_root():
    from pathlib import Path as _P
    from takyon_constants import get_takyon_home

    root = get_takyon_home() / "spawn-trees"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _spawn_tree_session_dir(session_id: str):
    safe = (
        "".join(c if c.isalnum() or c in "-_" else "_" for c in session_id) or "unknown"
    )
    d = _spawn_trees_root() / safe
    d.mkdir(parents=True, exist_ok=True)
    return d


# Per-session append-only index of lightweight snapshot metadata.  Read by
# `spawn_tree.list` so scanning doesn't require reading every full snapshot
# file (Copilot review on #14045).  One JSON object per line.
_SPAWN_TREE_INDEX = "_index.jsonl"


def _append_spawn_tree_index(session_dir, entry: dict) -> None:
    try:
        with (session_dir / _SPAWN_TREE_INDEX).open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError as exc:
        # Index is a cache — losing a line just means list() falls back
        # to a directory scan for that entry.  Never block the save.
        logger.debug("spawn_tree index append failed: %s", exc)


def _read_spawn_tree_index(session_dir) -> list[dict]:
    index_path = session_dir / _SPAWN_TREE_INDEX
    if not index_path.exists():
        return []
    out: list[dict] = []
    try:
        with index_path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except OSError:
        return []
    return out


@method("spawn_tree.save")
def _(rid, params: dict) -> dict:
    session_id = str(params.get("session_id") or "").strip()
    subagents = params.get("subagents") or []
    if not isinstance(subagents, list) or not subagents:
        return _err(rid, 4000, "subagents list required")

    from datetime import datetime

    started_at = params.get("started_at")
    finished_at = params.get("finished_at") or time.time()
    label = str(params.get("label") or "")
    ts = datetime.utcfromtimestamp(float(finished_at)).strftime("%Y%m%dT%H%M%S")
    fname = f"{ts}.json"
    d = _spawn_tree_session_dir(session_id or "default")
    path = d / fname
    try:
        payload = {
            "session_id": session_id,
            "started_at": float(started_at) if started_at else None,
            "finished_at": float(finished_at),
            "label": label,
            "subagents": subagents,
        }
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    except OSError as exc:
        return _err(rid, 5000, f"spawn_tree.save failed: {exc}")

    _append_spawn_tree_index(
        d,
        {
            "path": str(path),
            "session_id": session_id,
            "started_at": payload["started_at"],
            "finished_at": payload["finished_at"],
            "label": label,
            "count": len(subagents),
        },
    )

    return _ok(rid, {"path": str(path), "session_id": session_id})


@method("spawn_tree.list")
def _(rid, params: dict) -> dict:
    session_id = str(params.get("session_id") or "").strip()
    limit = int(params.get("limit") or 50)
    cross_session = bool(params.get("cross_session"))

    if cross_session:
        root = _spawn_trees_root()
        roots = [p for p in root.iterdir() if p.is_dir()]
    else:
        roots = [_spawn_tree_session_dir(session_id or "default")]

    entries: list[dict] = []
    for d in roots:
        indexed = _read_spawn_tree_index(d)
        if indexed:
            # Skip index entries whose snapshot file was manually deleted.
            entries.extend(
                e for e in indexed if (p := e.get("path")) and Path(p).exists()
            )
            continue

        # Fallback for legacy (pre-index) sessions: full scan.  O(N) reads
        # but only runs once per session until the next save writes the index.
        for p in d.glob("*.json"):
            if p.name == _SPAWN_TREE_INDEX:
                continue
            try:
                stat = p.stat()
                try:
                    raw = json.loads(p.read_text(encoding="utf-8"))
                except Exception:
                    raw = {}
                subagents = raw.get("subagents") or []
                entries.append(
                    {
                        "path": str(p),
                        "session_id": raw.get("session_id") or d.name,
                        "finished_at": raw.get("finished_at") or stat.st_mtime,
                        "started_at": raw.get("started_at"),
                        "label": raw.get("label") or "",
                        "count": len(subagents) if isinstance(subagents, list) else 0,
                    }
                )
            except OSError:
                continue

    entries.sort(key=lambda e: e.get("finished_at") or 0, reverse=True)
    return _ok(rid, {"entries": entries[:limit]})


@method("spawn_tree.load")
def _(rid, params: dict) -> dict:
    from pathlib import Path

    raw_path = str(params.get("path") or "").strip()
    if not raw_path:
        return _err(rid, 4000, "path required")

    # Reject paths escaping the spawn-trees root.
    root = _spawn_trees_root().resolve()
    try:
        resolved = Path(raw_path).resolve()
        resolved.relative_to(root)
    except (ValueError, OSError) as exc:
        return _err(rid, 4030, f"path outside spawn-trees root: {exc}")

    try:
        payload = json.loads(resolved.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return _err(rid, 5000, f"spawn_tree.load failed: {exc}")

    return _ok(rid, payload)


@method("session.steer")
def _(rid, params: dict) -> dict:
    """Inject a user message into the next tool result without interrupting.

    Mirrors AIAgent.steer(). Safe to call while a turn is running — the text
    lands on the last tool result of the next tool batch and the model sees
    it on its next iteration. No interrupt, no new user turn, no role
    alternation violation.
    """
    text = (params.get("text") or "").strip()
    if not text:
        return _err(rid, 4002, "text is required")
    session, err = _sess_nowait(params, rid)
    if err:
        return err
    agent = session.get("agent")
    if agent is None or not hasattr(agent, "steer"):
        return _err(rid, 4010, "agent does not support steer")
    try:
        accepted = agent.steer(text)
    except Exception as exc:
        return _err(rid, 5000, f"steer failed: {exc}")
    return _ok(rid, {"status": "queued" if accepted else "rejected", "text": text})


@method("terminal.resize")
def _(rid, params: dict) -> dict:
    session, err = _sess_nowait(params, rid)
    if err:
        return err
    session["cols"] = int(params.get("cols", 80))
    return _ok(rid, {"cols": session["cols"]})


# ── Methods: prompt ──────────────────────────────────────────────────


@method("prompt.submit")
def _(rid, params: dict) -> dict:
    sid, text = params.get("session_id", ""), params.get("text", "")
    create_in_test_mode = bool(params.get("create_in_test_mode"))
    session, err = _sess_nowait(params, rid)
    if err:
        return err
    with session["history_lock"]:
        if session.get("running"):
            return _err(rid, 4009, "session busy")
        session["running"] = True
    skill_lab = session.get("takyon_skill_lab") if isinstance(session, dict) else None
    skill_lab_prompt = ""
    skill_lab_agent_overrides: dict[str, Any] | None = None
    if isinstance(skill_lab, dict):
        skill_lab_prompt = str(skill_lab.get("prompt") or "").strip()
        if skill_lab_prompt:
            current_ephemeral = str(
                getattr(session.get("agent"), "ephemeral_system_prompt", "") or ""
            ).strip()
            combined_prompt = "\n\n".join(
                part for part in (current_ephemeral, skill_lab_prompt) if part
            ).strip()
            skill_lab_agent_overrides = {
                "ephemeral_system_prompt": combined_prompt or skill_lab_prompt
            }
    current_business = str(session.get("takyon_current_business") or "").strip()
    _append_user_message_to_session_db(sid, session, text)

    _start_streaming_session_turn(
        rid,
        sid,
        session,
        text,
        display_text=text,
        contextualize_takyon=bool(current_business) or not bool(skill_lab_prompt),
        create_in_test_mode=create_in_test_mode,
        agent_config_overrides=skill_lab_agent_overrides,
        persist_user_message_to_db=False,
    )
    return _ok(rid, {"status": "streaming"})


def _notification_poller_loop(
    stop_event: threading.Event, sid: str, session: dict
) -> None:
    """Poll completion_queue and dispatch notifications autonomously.

    Runs in a daemon thread started by _init_session(). Emits a
    status.update (kind=process) for user visibility, then chains an
    agent turn via _run_prompt_submit if the session is idle.

    NOTE: The completion_queue is global (one per process). If multiple
    TUI sessions coexist, whichever poller wakes first grabs the event,
    even if the process was started by a different session. This matches
    CLI/gateway behavior (single session per process).
    """
    from tools.process_registry import process_registry, format_process_notification

    while not stop_event.is_set() and not session.get("_finalized"):
        try:
            evt = process_registry.completion_queue.get(timeout=0.5)
        except Exception:
            continue

        _evt_sid = evt.get("session_id", "")
        if evt.get("type") == "completion" and process_registry.is_completion_consumed(_evt_sid):
            continue

        text = format_process_notification(evt)
        if not text:
            continue

        _emit("status.update", sid, {"kind": "process", "text": text})

        with session["history_lock"]:
            if session.get("running"):
                process_registry.completion_queue.put(evt)
                continue
            session["running"] = True

        rid = f"__notif__{int(time.time() * 1000)}"
        try:
            _emit("message.start", sid)
            _run_prompt_submit(rid, sid, session, text)
        except Exception as exc:
            print(
                f"[tui_gateway] notification poller dispatch failed: "
                f"{type(exc).__name__}: {exc}",
                file=sys.stderr,
            )
            with session["history_lock"]:
                session["running"] = False

    # Drain any remaining events after stop signal (process all pending
    # before exiting so nothing is lost on shutdown).
    while not process_registry.completion_queue.empty():
        try:
            evt = process_registry.completion_queue.get_nowait()
        except Exception:
            break
        _evt_sid = evt.get("session_id", "")
        if evt.get("type") == "completion" and process_registry.is_completion_consumed(_evt_sid):
            continue
        text = format_process_notification(evt)
        if not text:
            continue

        _emit("status.update", sid, {"kind": "process", "text": text})

        with session["history_lock"]:
            if session.get("running"):
                process_registry.completion_queue.put(evt)
                break
            session["running"] = True

        rid = f"__notif__{int(time.time() * 1000)}"
        try:
            _emit("message.start", sid)
            _run_prompt_submit(rid, sid, session, text)
        except Exception as exc:
            print(
                f"[tui_gateway] notification poller dispatch failed: "
                f"{type(exc).__name__}: {exc}",
                file=sys.stderr,
            )
            with session["history_lock"]:
                session["running"] = False


def _start_notification_poller(sid: str, session: dict) -> threading.Event:
    """Start the background notification poller for a TUI session."""
    stop = threading.Event()
    t = threading.Thread(
        target=_notification_poller_loop,
        args=(stop, sid, session),
        daemon=True,
    )
    t.start()
    return stop


def _history_without_latest_user_message(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    trimmed = list(messages)
    for index in range(len(trimmed) - 1, -1, -1):
        entry = trimmed[index]
        if isinstance(entry, dict) and entry.get("role") == "user":
            del trimmed[index]
            break
    return trimmed


def _reset_session_history_for_post_bootstrap_chat(sid: str, session: dict) -> None:
    with session["history_lock"]:
        session["history"] = []
        session["history_version"] = int(session.get("history_version", 0)) + 1
        session.pop("history_memory_only", None)
    try:
        agent = session.get("agent")
        session_db = getattr(agent, "_session_db", None) or _get_db()
        session_key = str(
            getattr(agent, "session_id", "") or session.get("session_key") or sid
        ).strip()
        if session_db and session_key:
            session_db.replace_messages(session_key, [])
    except Exception as exc:
        print(
            f"[tui_gateway] bootstrap history reset persist failed: {exc}",
            file=sys.stderr,
        )


def _append_user_message_to_session_db(sid: str, session: dict, content: Any) -> None:
    try:
        agent = session.get("agent")
        session_db = getattr(agent, "_session_db", None) or _get_db()
        session_key = str(
            getattr(agent, "session_id", "") or session.get("session_key") or sid
        ).strip()
        if session_db and session_key:
            session_db.append_message(
                session_id=session_key,
                role="user",
                content=content,
            )
    except Exception as exc:
        print(
            f"[tui_gateway] in-flight user history persist failed: {exc}",
            file=sys.stderr,
        )


def _persist_inflight_user_message(
    sid: str,
    session: dict,
    history: list[dict[str, Any]],
    history_version: int,
    content: Any,
    *,
    display_text: str | None = None,
    persist_db: bool = True,
) -> int:
    pending_message: dict[str, Any] = {"role": "user", "content": content}
    if isinstance(display_text, str) and display_text.strip():
        pending_message["display_text"] = display_text

    with session["history_lock"]:
        current_version = int(session.get("history_version", 0))
        base_history = (
            history if current_version == history_version else list(session.get("history", []))
        )
        session["history"] = [*base_history, pending_message]
        next_version = current_version + 1
        session["history_version"] = next_version

    if persist_db:
        _append_user_message_to_session_db(sid, session, content)

    return next_version


def _start_streaming_session_turn(
    rid,
    sid: str,
    session: dict,
    text: Any,
    *,
    display_text: str | None = None,
    contextualize_takyon: bool = False,
    create_in_test_mode: bool = False,
    record_user_history: bool = True,
    system_message_override: str | None = None,
    max_iterations_override: int | None = None,
    agent_config_overrides: dict[str, Any] | None = None,
    post_complete_callback: Callable[[], str | None] | None = None,
    start_delay_ms: int = 0,
    persist_user_message_to_db: bool = True,
) -> None:
    _start_agent_build(sid, session)

    def run_after_agent_ready() -> None:
        err = _wait_agent(session, rid)
        if err:
            _emit(
                "error",
                sid,
                {
                    "message": err.get("error", {}).get(
                        "message", "agent initialization failed"
                    )
                },
            )
            with session["history_lock"]:
                session["running"] = False
            return
        if start_delay_ms > 0:
            time.sleep(start_delay_ms / 1000.0)
        _run_prompt_submit(
            rid,
            sid,
            session,
            text,
            display_text=display_text,
            contextualize_takyon=contextualize_takyon,
            create_in_test_mode=create_in_test_mode,
            record_user_history=record_user_history,
            system_message_override=system_message_override,
            max_iterations_override=max_iterations_override,
            agent_config_overrides=agent_config_overrides,
            post_complete_callback=post_complete_callback,
            persist_user_message_to_db=persist_user_message_to_db,
        )

    threading.Thread(target=run_after_agent_ready, daemon=True).start()


def _run_prompt_submit(
    rid,
    sid: str,
    session: dict,
    text: Any,
    *,
    display_text: str | None = None,
    contextualize_takyon: bool = False,
    create_in_test_mode: bool = False,
    record_user_history: bool = True,
    system_message_override: str | None = None,
    max_iterations_override: int | None = None,
    agent_config_overrides: dict[str, Any] | None = None,
    post_complete_callback: Callable[[], str | None] | None = None,
    persist_user_message_to_db: bool = True,
) -> None:
    with session["history_lock"]:
        history = list(session["history"])
        history_version = int(session.get("history_version", 0))
        images = list(session.get("attached_images", []))
        session["attached_images"] = []
        if not record_user_history:
            session["history_memory_only"] = True
    agent = session["agent"]
    _emit("message.start", sid)

    def run():
        approval_token = None
        session_tokens = []
        goal_followup = None  # set by the post-turn goal hook below
        reservation_key = ""
        reserved_cents = 0
        turn_cost_before_usd = 0.0
        billing_warning = ""
        resolved_operator_user_id = ""
        try:
            session["takyon_turn_file_activity"] = []
            from tools.approval import (
                reset_current_session_key,
                set_current_session_key,
            )
            from plugins.takyon.cli import (
                _business_workspace_execution_context,
                _operator_budget_finalize,
                _operator_budget_reserve,
                _resolved_operator_user_id,
            )

            approval_token = set_current_session_key(session["session_key"])
            cols = session.get("cols", 80)
            streamer = make_stream_renderer(cols)
            prompt = text
            if contextualize_takyon and isinstance(prompt, str):
                prompt = _build_takyon_prompt_text(
                    session,
                    prompt,
                    create_in_test_mode=create_in_test_mode,
                )

            if isinstance(prompt, str) and "@" in prompt:
                from agent.context_references import preprocess_context_references
                from agent.model_metadata import get_model_context_length

                ctx_len = get_model_context_length(
                    getattr(agent, "model", "") or _resolve_model(),
                    base_url=getattr(agent, "base_url", "") or "",
                    api_key=getattr(agent, "api_key", "") or "",
                    provider=getattr(agent, "provider", "") or "",
                    config_context_length=getattr(
                        agent, "_config_context_length", None
                    ),
                )
                ctx = preprocess_context_references(
                    prompt,
                    cwd=os.environ.get("TERMINAL_CWD", os.getcwd()),
                    allowed_root=os.environ.get("TERMINAL_CWD", os.getcwd()),
                    context_length=ctx_len,
                )
                if ctx.blocked:
                    _emit(
                        "error",
                        sid,
                        {
                            "message": "\n".join(ctx.warnings)
                            or "Context injection refused."
                        },
                    )
                    return
                prompt = ctx.message

            # Decide image routing per-turn based on active provider/model.
            # "native" → pass pixels to the main model as OpenAI-style content
            # parts (adapters translate for Anthropic/Gemini/Bedrock/etc.).
            # "text"   → pre-analyze with vision_analyze and prepend the text.
            # See agent/image_routing.py for the full decision table.
            run_message: Any = prompt
            if images:
                try:
                    from agent.image_routing import (
                        decide_image_input_mode,
                        build_native_content_parts,
                    )
                    from agent.auxiliary_client import (
                        _read_main_model,
                        _read_main_provider,
                    )
                    from takyon_cli.config import load_config as _tui_load_config

                    _cfg = _tui_load_config()
                    _mode = decide_image_input_mode(
                        _read_main_provider(),
                        _read_main_model(),
                        _cfg,
                    )
                except Exception as _img_exc:
                    print(
                        f"[tui_gateway] image_routing decision failed, defaulting to text: {_img_exc}",
                        file=sys.stderr,
                    )
                    _mode = "text"

                if _mode == "native":
                    try:
                        _parts, _skipped = build_native_content_parts(
                            prompt,
                            images,
                        )
                        if _skipped:
                            print(
                                f"[tui_gateway] native image attachment skipped {len(_skipped)} unreadable path(s)",
                                file=sys.stderr,
                            )
                        if any(p.get("type") == "image_url" for p in _parts):
                            run_message = _parts
                        else:
                            run_message = _enrich_with_attached_images(prompt, images)
                    except Exception as _img_exc:
                        print(
                            f"[tui_gateway] native attach failed, falling back to text: {_img_exc}",
                            file=sys.stderr,
                        )
                        run_message = _enrich_with_attached_images(prompt, images)
            else:
                run_message = _enrich_with_attached_images(prompt, images)

            persisted_history_version = history_version
            if record_user_history:
                persisted_history_version = _persist_inflight_user_message(
                    sid,
                    session,
                    history,
                    history_version,
                    run_message,
                    display_text=display_text,
                    persist_db=persist_user_message_to_db,
                )

            resolved_operator_user_id = _resolved_operator_user_id(
                _takyon_operator_user_id(session)
            )
            current_business = str(session.get("takyon_current_business") or "").strip()
            turn_key = ""
            if current_business:
                turn_key = f"turn:session:{sid}:{uuid.uuid4().hex[:10]}"
                session["takyon_active_turn_key"] = turn_key
                _takyon_record_session_runtime_event(
                    session,
                    kind="ceo_turn",
                    status="started",
                    detail="CEO turn is running.",
                    trace={
                        "kind": "turn",
                        "entry_key": turn_key,
                        "label": "CEO turn",
                        "detail": "CEO turn is running.",
                        "status": "running",
                    },
                )
            if resolved_operator_user_id:
                reservation_key, reserved_cents = _operator_budget_reserve(
                    operator_user_id=resolved_operator_user_id,
                    business_slug=current_business or None,
                    reservation_key=f"tui-turn:{sid}:{uuid.uuid4().hex}",
                )
                turn_cost_before_usd = float(
                    getattr(agent, "session_estimated_cost_usd", 0.0) or 0.0
                )

            worker_usage = None
            turn_cost_after_usd = turn_cost_before_usd
            original_agent_max_iterations = getattr(agent, "max_iterations", None)
            if getattr(agent, "_takyon_operator_gateway", False):
                worker_kwargs = {
                    "operator_user_id": resolved_operator_user_id,
                    "business_slug": current_business,
                    "streamer": streamer,
                }
                if system_message_override:
                    worker_kwargs["system_message_override"] = system_message_override
                if max_iterations_override is not None:
                    worker_kwargs["max_iterations_override"] = max_iterations_override
                if isinstance(agent_config_overrides, dict) and agent_config_overrides:
                    worker_kwargs["agent_config_overrides"] = dict(agent_config_overrides)
                worker_result = _run_isolated_gateway_turn(
                    sid,
                    session,
                    agent,
                    run_message,
                    list(history),
                    **worker_kwargs,
                )
                result = worker_result.get("result")
                worker_usage = (
                    dict(worker_result.get("usage") or {})
                    if isinstance(worker_result, dict)
                    else None
                )
                _apply_usage_snapshot(
                    agent,
                    worker_result.get("usage_snapshot")
                    if isinstance(worker_result, dict)
                    else None,
                    session_id=str(
                        (worker_result.get("session_id") or "")
                        if isinstance(worker_result, dict)
                        else ""
                    ),
                )
                try:
                    turn_cost_after_usd = float(
                        (worker_result.get("session_estimated_cost_usd"))
                        if isinstance(worker_result, dict)
                        else turn_cost_before_usd
                    )
                except (TypeError, ValueError):
                    turn_cost_after_usd = turn_cost_before_usd
            else:
                def _stream(delta):
                    payload = {"text": delta}
                    if streamer and (r := streamer.feed(delta)) is not None:
                        payload["rendered"] = r
                    _emit("message.delta", sid, payload)

                workspace_context = (
                    _business_workspace_execution_context(
                        current_business,
                        operator_user_id=resolved_operator_user_id,
                    )
                    if current_business
                    else contextlib.nullcontext(None)
                )
                with workspace_context as workspace_home:
                    try:
                        if max_iterations_override is not None:
                            agent.max_iterations = int(max_iterations_override)
                        session_tokens = _set_session_context(
                            session["session_key"],
                            operator_user_id=_takyon_operator_user_id(session),
                            workspace_root=str(workspace_home or ""),
                            business_slug=current_business,
                        )
                        run_kwargs = {
                            "conversation_history": list(history),
                            "stream_callback": _stream,
                        }
                        if system_message_override:
                            run_kwargs["system_message"] = system_message_override
                        result = agent.run_conversation(run_message, **run_kwargs)
                    finally:
                        if (
                            max_iterations_override is not None
                            and original_agent_max_iterations is not None
                        ):
                            agent.max_iterations = original_agent_max_iterations

            last_reasoning = None
            status_note = None
            if isinstance(result, dict):
                if isinstance(result.get("messages"), list):
                    if not record_user_history:
                        result["messages"] = _history_without_latest_user_message(
                            result["messages"]
                        )
                    elif isinstance(display_text, str) and display_text:
                        for message in reversed(result["messages"]):
                            if isinstance(message, dict) and message.get("role") == "user":
                                message["display_text"] = display_text
                                break
                    with session["history_lock"]:
                        current_version = int(session.get("history_version", 0))
                        if current_version == persisted_history_version:
                            session["history"] = result["messages"]
                            session["history_version"] = persisted_history_version + 1
                        else:
                            # History mutated externally during the turn
                            # (undo/compress/retry/rollback now guard on
                            # session.running, but this is the defensive
                            # backstop for any path that slips past).
                            # Surface the desync rather than silently
                            # dropping the agent's output — the UI can
                            # show the response and warn that it was
                            # not persisted.
                            print(
                                f"[tui_gateway] prompt.submit: history_version mismatch "
                                f"(expected={persisted_history_version} current={current_version}) — "
                                f"agent output NOT written to session history",
                                file=sys.stderr,
                            )
                            status_note = (
                                "History changed during this turn — the response above is visible "
                                "but was not saved to session history."
                            )

                # If auto-compression fired inside run_conversation(), agent.session_id
                # may have rotated. Sync session_key before downstream title/goal/finalize
                # handling uses it. Preserve pending_title (user intent) so it can be
                # applied to the continuation. Restart slash worker so subsequent
                # worker-backed commands (/title etc.) target the live session.
                # Fix for #20001.
                _sync_session_key_after_compress(
                    sid, session, clear_pending_title=False, restart_slash_worker=True,
                )
                if (
                    not record_user_history
                    and isinstance(result.get("messages"), list)
                ):
                    try:
                        session_db = getattr(agent, "_session_db", None) or _get_db()
                        session_key = str(
                            getattr(agent, "session_id", "")
                            or session.get("session_key")
                            or sid
                        ).strip()
                        if session_db and session_key:
                            session_db.replace_messages(session_key, result["messages"])
                    except Exception as exc:
                        print(
                            f"[tui_gateway] hidden-history rewrite failed: {exc}",
                            file=sys.stderr,
                        )

                raw = result.get("final_response", "")
                status = (
                    "interrupted"
                    if result.get("interrupted")
                    else "error" if result.get("error") else "complete"
                )
                # When the backend produced no visible response AND reported a
                # real error (e.g. invalid model slug → provider 4xx), surface
                # that error as the visible text instead of shipping an empty
                # turn to Ink. Mirrors classic CLI behavior at cli.py where
                # (failed|partial) + no final_response → "Error: <detail>".
                # Leaves the None-with-no-error path untouched: an empty
                # successful turn still renders as empty, and the existing
                # "(empty)" sentinel handling stays in its own lane.
                if (not raw) and result.get("error") and (
                    result.get("failed") or result.get("partial")
                ):
                    raw = f"Error: {result.get('error')}"
                lr = result.get("last_reasoning")
                if isinstance(lr, str) and lr.strip():
                    last_reasoning = lr.strip()
            else:
                raw = str(result)
                status = "complete"

            if status == "complete" and callable(post_complete_callback):
                try:
                    post_warning = str(post_complete_callback() or "").strip()
                    if post_warning:
                        if status_note:
                            status_note = f"{status_note}\n{post_warning}"
                        else:
                            status_note = post_warning
                except Exception as exc:
                    logger.exception("post-turn finalization failed")
                    warning = f"Post-turn finalization failed: {exc}"
                    if status_note:
                        status_note = f"{status_note}\n{warning}"
                    else:
                        status_note = warning
            if status == "complete" and current_business:
                try:
                    post_warning = _finalize_product_surface_after_turn(
                        session,
                        sid=sid,
                        business_slug=current_business,
                        operator_user_id=resolved_operator_user_id,
                    )
                    if post_warning:
                        if status_note:
                            status_note = f"{status_note}\n{post_warning}"
                        else:
                            status_note = post_warning
                except Exception as exc:
                    logger.exception("post-turn product surface finalization failed")
                    warning = f"Post-turn product surface finalization failed: {exc}"
                    if status_note:
                        status_note = f"{status_note}\n{warning}"
                    else:
                        status_note = warning

            usage_payload = worker_usage if isinstance(worker_usage, dict) else _get_usage(agent)
            payload = {"text": raw, "usage": usage_payload, "status": status}
            if last_reasoning:
                payload["reasoning"] = last_reasoning
            if status_note:
                payload["warning"] = status_note
            rendered = render_message(raw, cols)
            if rendered:
                payload["rendered"] = rendered
            _emit("message.complete", sid, payload)
            _emit_progress(sid, "done", "Turn complete.")
            if current_business:
                trace_status_value = "completed" if status == "complete" else "failed"
                trace_detail = (raw or "").strip()[:280]
                if not trace_detail:
                    trace_detail = "CEO turn completed." if trace_status_value == "completed" else "CEO turn ended with an error."
                _takyon_record_session_runtime_event(
                    session,
                    kind="ceo_turn",
                    status=trace_status_value,
                    detail=trace_detail,
                    trace={
                        "kind": "turn",
                        "entry_key": turn_key or f"turn:session:{sid}",
                        "label": "CEO turn",
                        "detail": trace_detail,
                        "status": trace_status_value,
                    },
                )

            # ── /goal continuation (Ralph-style loop) ─────────────────
            # After every TUI turn, if a /goal is active, ask the judge
            # whether the goal is done and — if not and we're still under
            # budget — queue a continuation prompt to run after this
            # thread releases session["running"]. The verdict message
            # ("✓ Goal achieved" / "⏸ budget exhausted") is surfaced as
            # a system line so the user sees progress regardless of
            # outcome. Mirrors gateway/run._post_turn_goal_continuation.
            if status == "complete" and isinstance(raw, str) and raw.strip():
                try:
                    from takyon_cli.goals import GoalManager

                    sid_key = session.get("session_key") or ""
                    if sid_key:
                        try:
                            goals_cfg = _load_cfg().get("goals") or {}
                            goal_max_turns = int(goals_cfg.get("max_turns", 20) or 20)
                        except Exception:
                            goal_max_turns = 20
                        goal_mgr = GoalManager(
                            session_id=sid_key,
                            default_max_turns=goal_max_turns,
                        )
                        if goal_mgr.is_active():
                            decision = goal_mgr.evaluate_after_turn(
                                raw,
                                user_initiated=True,
                            )
                            verdict_msg = decision.get("message") or ""
                            if verdict_msg:
                                _emit(
                                    "status.update",
                                    sid,
                                    {"kind": "goal", "text": verdict_msg},
                                )
                            if decision.get("should_continue"):
                                cont_prompt = decision.get("continuation_prompt") or ""
                                if cont_prompt:
                                    goal_followup = cont_prompt
                except Exception as _goal_exc:
                    print(
                        f"[tui_gateway] goal continuation hook failed: "
                        f"{type(_goal_exc).__name__}: {_goal_exc}",
                        file=sys.stderr,
                    )

            # Apply pending_title now that the DB row exists.
            _pending = session.get("pending_title")
            if _pending and status == "complete":
                _pdb = _get_db()
                if _pdb:
                    _session_key = session.get("session_key") or sid
                    try:
                        if _pdb.set_session_title(_session_key, _pending):
                            session["pending_title"] = None
                    except ValueError as exc:
                        # Invalid/duplicate title — non-retryable, drop it.
                        # Auto-title will take over. Fix for #19029.
                        session["pending_title"] = None
                        logger.info(
                            "Dropping pending title for session %s: %s",
                            _session_key, exc,
                        )
                    except Exception:
                        # Transient DB failure — keep pending_title for retry.
                        pass

            if (
                status == "complete"
                and isinstance(raw, str)
                and raw.strip()
                and isinstance(text, str)
                and text.strip()
                and record_user_history
            ):
                try:
                    from agent.title_generator import maybe_auto_title

                    maybe_auto_title(
                        _get_db(),
                        session.get("session_key") or sid,
                        text,
                        raw,
                        session.get("history", []),
                    )
                except Exception:
                    pass

            # CLI parity: when voice-mode TTS is on, speak the agent reply
            # (cli.py:_voice_speak_response).  Only the final text — tool
            # calls / reasoning already stream separately and would be
            # noisy to read aloud.
            if (
                status == "complete"
                and isinstance(raw, str)
                and raw.strip()
                and _voice_tts_enabled()
            ):
                try:
                    from takyon_cli.voice import speak_text

                    spoken = raw
                    threading.Thread(
                        target=speak_text, args=(spoken,), daemon=True
                    ).start()
                except ImportError:
                    logger.warning("voice TTS skipped: takyon_cli.voice unavailable")
                except Exception as e:
                    logger.warning("voice TTS dispatch failed: %s", e)
        except Exception as e:
            import traceback

            trace = traceback.format_exc()
            try:
                os.makedirs(os.path.dirname(_CRASH_LOG), exist_ok=True)
                with open(_CRASH_LOG, "a", encoding="utf-8") as f:
                    f.write(
                        f"\n=== turn-dispatcher exception · "
                        f"{time.strftime('%Y-%m-%d %H:%M:%S')} · sid={sid} ===\n"
                    )
                    f.write(trace)
            except Exception:
                pass
            print(
                f"[gateway-turn] {type(e).__name__}: {e}", file=sys.stderr, flush=True
            )
            _takyon_record_session_runtime_event(
                session,
                kind="ceo_turn",
                status="failed",
                detail=str(e),
                trace={
                    "kind": "turn",
                    "entry_key": str(session.get("takyon_active_turn_key") or f"turn:session:{sid}"),
                    "label": "CEO turn",
                    "detail": str(e),
                    "status": "failed",
                },
            )
            _emit("error", sid, {"message": str(e)})
        finally:
            turn_actual_cents = max(
                0,
                int(
                    round(
                        (
                            float(turn_cost_after_usd or 0.0)
                            - float(turn_cost_before_usd or 0.0)
                        )
                        * 100
                    )
                ),
            )
            if reservation_key:
                try:
                    billing_warning = _operator_budget_finalize(
                        operator_user_id=resolved_operator_user_id,
                        business_slug=str(session.get("takyon_current_business") or "").strip() or None,
                        reservation_key=reservation_key,
                        reserved_cents=reserved_cents,
                        actual_cents=turn_actual_cents,
                    )
                except Exception as exc:
                    _emit("error", sid, {"message": f"budget settlement failed: {exc}"})
                if billing_warning:
                    _emit("status.update", sid, {"kind": "budget", "text": billing_warning})
                _emit(
                    "takyon.operator.account",
                    sid,
                    {
                        "actual_cents": turn_actual_cents,
                        "reserved_cents": reserved_cents,
                    },
                )
            try:
                if approval_token is not None:
                    reset_current_session_key(approval_token)
            except Exception:
                pass
            _clear_session_context(session_tokens)
            session["takyon_turn_file_activity"] = []
            session.pop("takyon_active_turn_key", None)
            with session["history_lock"]:
                if not record_user_history:
                    session.pop("history_memory_only", None)
                session["running"] = False

        # Chain a goal-continuation turn if the judge said so. We do
        # this AFTER the finally releases session["running"], so the
        # nested _run_prompt_submit doesn't deadlock on the busy
        # guard. A real user prompt that races us wins because
        # prompt.submit sets running=True under the history_lock and
        # we check that guard before re-firing.
        if goal_followup:
            with session["history_lock"]:
                if session.get("running"):
                    # User already sent something — their turn wins,
                    # the judge will re-run on the next turn anyway.
                    return
                session["running"] = True
            try:
                _emit("message.start", sid)
                _run_prompt_submit(rid, sid, session, goal_followup)
            except Exception as _cont_exc:
                print(
                    f"[tui_gateway] goal continuation dispatch failed: "
                    f"{type(_cont_exc).__name__}: {_cont_exc}",
                    file=sys.stderr,
                )
                with session["history_lock"]:
                    session["running"] = False

        # Drain completion notifications that arrived during this turn.
        # The background poller handles between-turn delivery; this is
        # the safety net for events that arrived mid-turn.
        try:
            from tools.process_registry import process_registry

            for _evt, synth in process_registry.drain_notifications():
                with session["history_lock"]:
                    if session.get("running"):
                        process_registry.completion_queue.put(_evt)
                        break
                    session["running"] = True
                try:
                    _emit("message.start", sid)
                    _run_prompt_submit(rid, sid, session, synth)
                except Exception as _n_exc:
                    print(
                        f"[tui_gateway] completion notification dispatch failed: "
                        f"{type(_n_exc).__name__}: {_n_exc}",
                        file=sys.stderr,
                    )
                    with session["history_lock"]:
                        session["running"] = False
        except Exception as _drain_exc:
            print(
                f"[tui_gateway] completion queue drain failed: "
                f"{type(_drain_exc).__name__}: {_drain_exc}",
                file=sys.stderr,
            )

    threading.Thread(target=run, daemon=True).start()


@method("clipboard.paste")
def _(rid, params: dict) -> dict:
    session, err = _sess(params, rid)
    if err:
        return err
    try:
        from takyon_cli.clipboard import has_clipboard_image, save_clipboard_image
    except Exception as e:
        return _err(rid, 5027, f"clipboard unavailable: {e}")

    session["image_counter"] = session.get("image_counter", 0) + 1
    img_dir = _takyon_home / "images"
    img_dir.mkdir(parents=True, exist_ok=True)
    img_path = (
        img_dir
        / f"clip_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{session['image_counter']}.png"
    )

    # Save-first: mirrors CLI keybinding path; more robust than has_image() precheck
    if not save_clipboard_image(img_path):
        session["image_counter"] = max(0, session["image_counter"] - 1)
        msg = (
            "Clipboard has image but extraction failed"
            if has_clipboard_image()
            else "No image found in clipboard"
        )
        return _ok(rid, {"attached": False, "message": msg})

    session.setdefault("attached_images", []).append(str(img_path))
    return _ok(
        rid,
        {
            "attached": True,
            "path": str(img_path),
            "count": len(session["attached_images"]),
            **_image_meta(img_path),
        },
    )


@method("image.attach")
def _(rid, params: dict) -> dict:
    session, err = _sess(params, rid)
    if err:
        return err
    raw = str(params.get("path", "") or "").strip()
    if not raw:
        return _err(rid, 4015, "path required")
    try:
        from cli import (
            _IMAGE_EXTENSIONS,
            _detect_file_drop,
            _resolve_attachment_path,
            _split_path_input,
        )

        dropped = _detect_file_drop(raw)
        if dropped:
            image_path = dropped["path"]
            remainder = dropped["remainder"]
        else:
            path_token, remainder = _split_path_input(raw)
            image_path = _resolve_attachment_path(path_token)
            if image_path is None:
                return _err(rid, 4016, f"image not found: {path_token}")
        if image_path.suffix.lower() not in _IMAGE_EXTENSIONS:
            return _err(rid, 4016, f"unsupported image: {image_path.name}")
        session.setdefault("attached_images", []).append(str(image_path))
        return _ok(
            rid,
            {
                "attached": True,
                "path": str(image_path),
                "count": len(session["attached_images"]),
                "remainder": remainder,
                "text": remainder or f"[User attached image: {image_path.name}]",
                **_image_meta(image_path),
            },
        )
    except Exception as e:
        return _err(rid, 5027, str(e))


@method("input.detect_drop")
def _(rid, params: dict) -> dict:
    session, err = _sess_nowait(params, rid)
    if err:
        return err
    try:
        from cli import _detect_file_drop

        raw = str(params.get("text", "") or "")
        dropped = _detect_file_drop(raw)
        if not dropped:
            return _ok(rid, {"matched": False})

        drop_path = dropped["path"]
        remainder = dropped["remainder"]
        if dropped["is_image"]:
            session.setdefault("attached_images", []).append(str(drop_path))
            text = remainder or f"[User attached image: {drop_path.name}]"
            return _ok(
                rid,
                {
                    "matched": True,
                    "is_image": True,
                    "path": str(drop_path),
                    "count": len(session["attached_images"]),
                    "text": text,
                    **_image_meta(drop_path),
                },
            )

        text = f"[User attached file: {drop_path}]" + (
            f"\n{remainder}" if remainder else ""
        )
        return _ok(
            rid,
            {
                "matched": True,
                "is_image": False,
                "path": str(drop_path),
                "name": drop_path.name,
                "text": text,
            },
        )
    except Exception as e:
        return _err(rid, 5027, str(e))


@method("prompt.background")
def _(rid, params: dict) -> dict:
    session, err = _sess(params, rid)
    if err:
        return err
    text, parent = params.get("text", ""), params.get("session_id", "")
    if not text:
        return _err(rid, 4012, "text required")
    task_id = f"bg_{uuid.uuid4().hex[:6]}"

    def run():
        # The background task acts as the parent session's operator — bind its principal so the
        # cleared-contextvar state can't mask per-session identity during the detached turn.
        session_tokens = _set_session_context(
            task_id, operator_user_id=_takyon_operator_user_id(session)
        )
        try:
            from run_agent import AIAgent

            bg_agent = AIAgent(**_background_agent_kwargs(session["agent"], task_id))
            parent_agent = session["agent"]
            if getattr(parent_agent, "_takyon_operator_gateway", False):
                from plugins.takyon.operator_gateway import enable_operator_gateway
                from takyon_cli.runtime_provider import resolve_runtime_provider

                context = getattr(parent_agent, "_takyon_operator_gateway_context", None)
                runtime = resolve_runtime_provider(
                    requested=getattr(context, "requested_provider", None),
                    target_model=getattr(bg_agent, "model", None),
                    explicit_base_url=getattr(context, "upstream_base_url", None) or None,
                )
                enable_operator_gateway(
                    bg_agent,
                    runtime,
                    operator_user_id=getattr(context, "operator_user_id", ""),
                    business_slug=getattr(context, "business_slug", ""),
                    workspace_root=getattr(context, "workspace_root", ""),
                )

            result = bg_agent.run_conversation(
                user_message=text,
                task_id=task_id,
            )
            _emit(
                "background.complete",
                parent,
                {
                    "task_id": task_id,
                    "text": (
                        result.get("final_response", str(result))
                        if isinstance(result, dict)
                        else str(result)
                    ),
                },
            )
        except Exception as e:
            _emit(
                "background.complete",
                parent,
                {"task_id": task_id, "text": f"error: {e}"},
            )
        finally:
            _clear_session_context(session_tokens)

    threading.Thread(target=run, daemon=True).start()
    return _ok(rid, {"task_id": task_id})


# ── Methods: respond ─────────────────────────────────────────────────


def _respond(rid, params, key):
    r = params.get("request_id", "")
    entry = _pending.get(r)
    if not entry:
        return _err(rid, 4009, f"no pending {key} request")
    _, ev = entry
    _answers[r] = params.get(key, "")
    ev.set()
    return _ok(rid, {"status": "ok"})


@method("clarify.respond")
def _(rid, params: dict) -> dict:
    return _respond(rid, params, "answer")


@method("sudo.respond")
def _(rid, params: dict) -> dict:
    return _respond(rid, params, "password")


@method("secret.respond")
def _(rid, params: dict) -> dict:
    return _respond(rid, params, "value")


@method("approval.respond")
def _(rid, params: dict) -> dict:
    session, err = _sess(params, rid)
    if err:
        return err
    try:
        from tools.approval import resolve_gateway_approval

        return _ok(
            rid,
            {
                "resolved": resolve_gateway_approval(
                    session["session_key"],
                    params.get("choice", "deny"),
                    resolve_all=params.get("all", False),
                )
            },
        )
    except Exception as e:
        return _err(rid, 5004, str(e))


# ── Methods: config ──────────────────────────────────────────────────


@method("config.set")
def _(rid, params: dict) -> dict:
    key, value = params.get("key", ""), params.get("value", "")
    session = _sessions.get(params.get("session_id", ""))

    if key == "model":
        try:
            if not value:
                return _err(rid, 4002, "model value required")
            if session:
                # Reject during an in-flight turn.  agent.switch_model()
                # mutates self.model / self.provider / self.base_url /
                # self.client in place; the worker thread running
                # agent.run_conversation is reading those on every
                # iteration.  A mid-turn swap can send an HTTP request
                # with the new base_url but old model (or vice versa),
                # producing 400/404s the user never asked for.  Parity
                # with the gateway's running-agent /model guard.
                if session.get("running"):
                    return _err(
                        rid,
                        4009,
                        "session busy — /interrupt the current turn before switching models",
                    )
                result = _apply_model_switch(
                    params.get("session_id", ""), session, value
                )
            else:
                result = _apply_model_switch("", {"agent": None}, value)
            return _ok(
                rid,
                {"key": key, "value": result["value"], "warning": result["warning"]},
            )
        except Exception as e:
            return _err(rid, 5001, str(e))

    if key == "fast":
        raw = str(value or "").strip().lower()
        agent = session.get("agent") if session else None
        if agent is not None:
            current_fast = getattr(agent, "service_tier", None) == "priority"
        else:
            current_fast = _load_service_tier() == "priority"

        if raw in {"status"}:
            return _ok(
                rid,
                {"key": key, "value": "fast" if current_fast else "normal"},
            )

        if raw in {"", "toggle"}:
            nv = "normal" if current_fast else "fast"
        elif raw in {"fast", "on"}:
            nv = "fast"
        elif raw in {"normal", "off"}:
            nv = "normal"
        else:
            return _err(rid, 4002, f"unknown fast mode: {value}")

        overrides = None
        if nv == "fast":
            from takyon_cli.models import resolve_fast_mode_overrides

            target_model = (
                getattr(agent, "model", None) if agent is not None else _resolve_model()
            )
            if not target_model:
                return _err(
                    rid,
                    4002,
                    "fast mode is not available without a selected model",
                )
            overrides = resolve_fast_mode_overrides(target_model)
            if overrides is None:
                return _err(
                    rid,
                    4002,
                    "fast mode is not available for this model",
                )

        _write_config_key("agent.service_tier", nv)
        if agent is not None:
            agent.service_tier = "priority" if nv == "fast" else None
            current_overrides = dict(getattr(agent, "request_overrides", {}) or {})
            current_overrides.pop("service_tier", None)
            current_overrides.pop("speed", None)
            if nv == "fast":
                current_overrides.update(overrides)
            agent.request_overrides = current_overrides
            _emit(
                "session.info",
                params.get("session_id", ""),
                _session_info(agent),
            )
        return _ok(rid, {"key": key, "value": nv})

    if key == "busy":
        raw = str(value or "").strip().lower()
        if raw in {"", "status"}:
            return _ok(rid, {"key": key, "value": _load_busy_input_mode()})
        if raw not in {"queue", "steer", "interrupt"}:
            return _err(rid, 4002, f"unknown busy mode: {value}")
        _write_config_key("display.busy_input_mode", raw)
        return _ok(rid, {"key": key, "value": raw})

    if key == "verbose":
        cycle = ["off", "new", "all", "verbose"]
        cur = (
            session.get("tool_progress_mode", _load_tool_progress_mode())
            if session
            else _load_tool_progress_mode()
        )
        if value and value != "cycle":
            nv = str(value).strip().lower()
            if nv not in cycle:
                return _err(rid, 4002, f"unknown verbose mode: {value}")
        else:
            try:
                idx = cycle.index(cur)
            except ValueError:
                idx = 2
            nv = cycle[(idx + 1) % len(cycle)]
        _write_config_key("display.tool_progress", nv)
        if session:
            session["tool_progress_mode"] = nv
            agent = session.get("agent")
            if agent is not None:
                agent.verbose_logging = nv == "verbose"
        return _ok(rid, {"key": key, "value": nv})

    if key == "yolo":
        try:
            if session:
                from tools.approval import (
                    disable_session_yolo,
                    enable_session_yolo,
                    is_session_yolo_enabled,
                )

                current = is_session_yolo_enabled(session["session_key"])
                if current:
                    disable_session_yolo(session["session_key"])
                    nv = "0"
                else:
                    enable_session_yolo(session["session_key"])
                    nv = "1"
            else:
                current = is_truthy_value(os.environ.get("TAKYON_YOLO_MODE"))
                if current:
                    os.environ.pop("TAKYON_YOLO_MODE", None)
                    nv = "0"
                else:
                    os.environ["TAKYON_YOLO_MODE"] = "1"
                    nv = "1"
            return _ok(rid, {"key": key, "value": nv})
        except Exception as e:
            return _err(rid, 5001, str(e))

    if key == "reasoning":
        try:
            from takyon_constants import parse_reasoning_effort

            arg = str(value or "").strip().lower()
            if arg in {"show", "on"}:
                cfg = _load_cfg()
                display = (
                    cfg.get("display") if isinstance(cfg.get("display"), dict) else {}
                )
                sections = (
                    display.get("sections")
                    if isinstance(display.get("sections"), dict)
                    else {}
                )
                display["show_reasoning"] = True
                sections["thinking"] = "expanded"
                display["sections"] = sections
                cfg["display"] = display
                _save_cfg(cfg)
                if session:
                    session["show_reasoning"] = True
                return _ok(rid, {"key": key, "value": "show"})
            if arg in {"hide", "off"}:
                cfg = _load_cfg()
                display = (
                    cfg.get("display") if isinstance(cfg.get("display"), dict) else {}
                )
                sections = (
                    display.get("sections")
                    if isinstance(display.get("sections"), dict)
                    else {}
                )
                display["show_reasoning"] = False
                sections["thinking"] = "hidden"
                display["sections"] = sections
                cfg["display"] = display
                _save_cfg(cfg)
                if session:
                    session["show_reasoning"] = False
                return _ok(rid, {"key": key, "value": "hide"})

            parsed = parse_reasoning_effort(arg)
            if parsed is None:
                return _err(rid, 4002, f"unknown reasoning value: {value}")
            _write_config_key("agent.reasoning_effort", arg)
            if session and session.get("agent") is not None:
                session["agent"].reasoning_config = parsed
            return _ok(rid, {"key": key, "value": arg})
        except Exception as e:
            return _err(rid, 5001, str(e))

    if key == "details_mode":
        nv = str(value or "").strip().lower()
        if nv not in _DETAIL_MODES:
            return _err(rid, 4002, f"unknown details_mode: {value}")
        cfg = _load_cfg()
        display = cfg.get("display") if isinstance(cfg.get("display"), dict) else {}
        sections = (
            display.get("sections") if isinstance(display.get("sections"), dict) else {}
        )
        display["details_mode"] = nv
        for section in _DETAIL_SECTION_NAMES:
            sections[section] = nv
        display["sections"] = sections
        cfg["display"] = display
        _save_cfg(cfg)
        return _ok(rid, {"key": key, "value": nv})

    if key.startswith("details_mode."):
        # Per-section override: `details_mode.<section>` writes to
        # `display.sections.<section>`. Empty value clears the explicit
        # override and lets frontend resolution apply built-in section defaults
        # before the global details_mode.
        section = key.split(".", 1)[1]
        if section not in _DETAIL_SECTION_NAMES:
            return _err(rid, 4002, f"unknown section: {section}")

        cfg = _load_cfg()
        display = cfg.get("display") if isinstance(cfg.get("display"), dict) else {}
        sections_cfg = (
            display.get("sections") if isinstance(display.get("sections"), dict) else {}
        )

        nv = str(value or "").strip().lower()
        if not nv:
            sections_cfg.pop(section, None)
            display["sections"] = sections_cfg
            cfg["display"] = display
            _save_cfg(cfg)
            return _ok(rid, {"key": key, "value": ""})

        if nv not in _DETAIL_MODES:
            return _err(rid, 4002, f"unknown details_mode: {value}")

        sections_cfg[section] = nv
        display["sections"] = sections_cfg
        cfg["display"] = display
        _save_cfg(cfg)
        return _ok(rid, {"key": key, "value": nv})

    if key == "thinking_mode":
        nv = str(value or "").strip().lower()
        allowed_tm = frozenset({"collapsed", "truncated", "full"})
        if nv not in allowed_tm:
            return _err(rid, 4002, f"unknown thinking_mode: {value}")
        _write_config_key("display.thinking_mode", nv)
        # Backward compatibility bridge: keep details_mode aligned.
        _write_config_key(
            "display.details_mode", "expanded" if nv == "full" else "collapsed"
        )
        return _ok(rid, {"key": key, "value": nv})

    if key == "compact":
        raw = str(value or "").strip().lower()
        cfg0 = _load_cfg()
        d0 = cfg0.get("display") if isinstance(cfg0.get("display"), dict) else {}
        cur_b = bool(d0.get("tui_compact", False))
        if raw in {"", "toggle"}:
            nv_b = not cur_b
        elif raw == "on":
            nv_b = True
        elif raw == "off":
            nv_b = False
        else:
            return _err(rid, 4002, f"unknown compact value: {value}")
        _write_config_key("display.tui_compact", nv_b)
        return _ok(rid, {"key": key, "value": "on" if nv_b else "off"})

    if key == "statusbar":
        raw = str(value or "").strip().lower()
        display = _load_cfg().get("display")
        d0 = display if isinstance(display, dict) else {}
        current = _coerce_statusbar(d0.get("tui_statusbar", "top"))

        if raw in {"", "toggle"}:
            nv = "top" if current == "off" else "off"
        elif raw == "on":
            nv = "top"
        elif raw in _STATUSBAR_MODES:
            nv = raw
        else:
            return _err(rid, 4002, f"unknown statusbar value: {value}")

        _write_config_key("display.tui_statusbar", nv)
        return _ok(rid, {"key": key, "value": nv})

    if key == "mouse":
        raw = str(value or "").strip().lower()
        cfg = _load_cfg()
        display = cfg.get("display") if isinstance(cfg.get("display"), dict) else {}
        current = _display_mouse_tracking(display)

        if raw in {"", "toggle"}:
            nv = not current
        elif raw == "on":
            nv = True
        elif raw == "off":
            nv = False
        else:
            return _err(rid, 4002, f"unknown mouse value: {value}")

        _write_config_key("display.mouse_tracking", nv)
        return _ok(rid, {"key": key, "value": "on" if nv else "off"})

    if key == "indicator":
        # Use an explicit None check rather than `value or ""` so falsy
        # non-string inputs (0, False, []) still surface as themselves
        # in the error message instead of looking like a blank value.
        raw = ("" if value is None else str(value)).strip().lower()
        if raw not in _INDICATOR_STYLES:
            return _err(
                rid,
                4002,
                f"unknown indicator: {raw!r}; pick one of {'|'.join(_INDICATOR_STYLES)}",
            )
        _write_config_key("display.tui_status_indicator", raw)
        return _ok(rid, {"key": key, "value": raw})

    if key in {"prompt", "personality", "skin"}:
        try:
            cfg = _load_cfg()
            if key == "prompt":
                if value == "clear":
                    cfg.pop("custom_prompt", None)
                    nv = ""
                else:
                    cfg["custom_prompt"] = value
                    nv = value
                _save_cfg(cfg)
            elif key == "personality":
                sid_key = params.get("session_id", "")
                pname, new_prompt = _validate_personality(str(value or ""), cfg)
                _write_config_key("display.personality", pname)
                _write_config_key("agent.system_prompt", new_prompt)
                nv = str(value or "default")
                history_reset, info = _apply_personality_to_session(
                    sid_key, session, new_prompt
                )
            else:
                _write_config_key(f"display.{key}", value)
                nv = value
                if key == "skin":
                    _emit("skin.changed", "", resolve_skin())
            resp = {"key": key, "value": nv}
            if key == "personality":
                resp["history_reset"] = history_reset
                if info is not None:
                    resp["info"] = info
            return _ok(rid, resp)
        except Exception as e:
            return _err(rid, 5001, str(e))

    return _err(rid, 4002, f"unknown config key: {key}")


@method("config.get")
def _(rid, params: dict) -> dict:
    key = params.get("key", "")
    if key == "provider":
        try:
            from takyon_cli.models import list_available_providers, normalize_provider

            model = _resolve_model()
            parts = model.split("/", 1)
            return _ok(
                rid,
                {
                    "model": model,
                    "provider": (
                        normalize_provider(parts[0]) if len(parts) > 1 else "unknown"
                    ),
                    "providers": list_available_providers(),
                },
            )
        except Exception as e:
            return _err(rid, 5013, str(e))
    if key == "profile":
        from takyon_constants import display_takyon_home

        return _ok(rid, {"home": str(_takyon_home), "display": display_takyon_home()})
    if key == "full":
        return _ok(rid, {"config": _load_cfg()})
    if key == "prompt":
        return _ok(rid, {"prompt": _load_cfg().get("custom_prompt", "")})
    if key == "skin":
        return _ok(
            rid, {"value": (_load_cfg().get("display") or {}).get("skin", "default")}
        )
    if key == "indicator":
        # Normalize so a hand-edited config.yaml with stray casing or
        # an unknown value reads back the SAME value the TUI actually
        # rendered (frontend's `normalizeIndicatorStyle` falls back to
        # `_INDICATOR_DEFAULT` for the same inputs).  Otherwise
        # `/indicator` would print one thing while the UI shows another.
        raw = (_load_cfg().get("display") or {}).get("tui_status_indicator", "")
        norm = str(raw).strip().lower()
        return _ok(
            rid,
            {"value": norm if norm in _INDICATOR_STYLES else _INDICATOR_DEFAULT},
        )
    if key == "personality":
        return _ok(
            rid,
            {"value": (_load_cfg().get("display") or {}).get("personality", "default")},
        )
    if key == "reasoning":
        cfg = _load_cfg()
        effort = str(
            (cfg.get("agent") or {}).get("reasoning_effort", "medium") or "medium"
        )
        display = (
            "show"
            if bool((cfg.get("display") or {}).get("show_reasoning", False))
            else "hide"
        )
        return _ok(rid, {"value": effort, "display": display})
    if key == "fast":
        return _ok(
            rid,
            {
                "value": (
                    "fast"
                    if (session := _sessions.get(params.get("session_id", "")))
                    and getattr(session.get("agent"), "service_tier", None)
                    == "priority"
                    else ("fast" if _load_service_tier() == "priority" else "normal")
                ),
            },
        )
    if key == "busy":
        return _ok(rid, {"value": _load_busy_input_mode()})
    if key == "details_mode":
        allowed_dm = frozenset({"hidden", "collapsed", "expanded"})
        raw = (
            str(
                (_load_cfg().get("display") or {}).get("details_mode", "collapsed")
                or "collapsed"
            )
            .strip()
            .lower()
        )
        nv = raw if raw in allowed_dm else "collapsed"
        return _ok(rid, {"value": nv})
    if key == "thinking_mode":
        allowed_tm = frozenset({"collapsed", "truncated", "full"})
        cfg = _load_cfg()
        raw = (
            str((cfg.get("display") or {}).get("thinking_mode", "") or "")
            .strip()
            .lower()
        )
        if raw in allowed_tm:
            nv = raw
        else:
            dm = (
                str(
                    (cfg.get("display") or {}).get("details_mode", "collapsed")
                    or "collapsed"
                )
                .strip()
                .lower()
            )
            nv = "full" if dm == "expanded" else "collapsed"
        return _ok(rid, {"value": nv})
    if key == "compact":
        on = bool((_load_cfg().get("display") or {}).get("tui_compact", False))
        return _ok(rid, {"value": "on" if on else "off"})
    if key == "statusbar":
        display = _load_cfg().get("display")
        raw = (
            display.get("tui_statusbar", "top") if isinstance(display, dict) else "top"
        )
        return _ok(rid, {"value": _coerce_statusbar(raw)})
    if key == "mouse":
        display = _load_cfg().get("display")
        on = _display_mouse_tracking(display)
        return _ok(rid, {"value": "on" if on else "off"})
    if key == "mtime":
        cfg_path = _takyon_home / "config.yaml"
        try:
            return _ok(
                rid, {"mtime": cfg_path.stat().st_mtime if cfg_path.exists() else 0}
            )
        except Exception:
            return _ok(rid, {"mtime": 0})
    return _err(rid, 4002, f"unknown config key: {key}")


@method("setup.status")
def _(rid, params: dict) -> dict:
    try:
        from takyon_cli.main import _has_any_provider_configured

        return _ok(rid, {"provider_configured": bool(_has_any_provider_configured())})
    except Exception as e:
        return _err(rid, 5016, str(e))


# ── Methods: tools & system ──────────────────────────────────────────


@method("process.stop")
def _(rid, params: dict) -> dict:
    try:
        from tools.process_registry import process_registry

        return _ok(rid, {"killed": process_registry.kill_all()})
    except Exception as e:
        return _err(rid, 5010, str(e))


@method("reload.mcp")
def _(rid, params: dict) -> dict:
    session = _sessions.get(params.get("session_id", ""))
    try:
        # Gate: /reload-mcp invalidates the prompt cache for this session.
        # Respect the ``approvals.mcp_reload_confirm`` config toggle — if
        # set (default true) AND the caller did not pass ``confirm=true``
        # in params, surface a warning to the transcript instead of just
        # reloading silently.  Users pass confirm=true either by
        # re-invoking after reading the warning, or by setting the
        # config key to false permanently.
        user_confirm = bool(params.get("confirm", False))
        if not user_confirm:
            try:
                from takyon_cli.config import load_config as _load_config

                _cfg = _load_config()
                _approvals = _cfg.get("approvals") if isinstance(_cfg, dict) else None
                _confirm_required = True
                if isinstance(_approvals, dict):
                    _confirm_required = bool(_approvals.get("mcp_reload_confirm", True))
            except Exception:
                _confirm_required = True
            if _confirm_required:
                # Return a structured response the Ink client can surface
                # as a warning/confirmation without actually reloading yet.
                # Ink's ops.ts reads ``status`` and prints ``message`` to
                # the transcript; a follow-up invocation with confirm=true
                # (or an `always` choice that flips the config) proceeds.
                return _ok(
                    rid,
                    {
                        "status": "confirm_required",
                        "message": (
                            "⚠️  /reload-mcp invalidates the prompt cache (next "
                            "message re-sends full input tokens). Reply `/reload-mcp "
                            "now` to proceed, or `/reload-mcp always` to proceed and "
                            "silence this prompt permanently."
                        ),
                    },
                )

        from tools.mcp_tool import shutdown_mcp_servers, discover_mcp_tools

        shutdown_mcp_servers()
        discover_mcp_tools()
        if session:
            agent = session["agent"]
            if hasattr(agent, "refresh_tools"):
                agent.refresh_tools()
            _emit("session.info", params.get("session_id", ""), _session_info(agent))

        # Honor `always=true` by persisting the opt-out to config.
        if bool(params.get("always", False)):
            try:
                from cli import save_config_value as _save_cfg

                _save_cfg("approvals.mcp_reload_confirm", False)
            except Exception as _exc:
                logger.warning("Failed to persist mcp_reload_confirm=false: %s", _exc)

        return _ok(rid, {"status": "reloaded"})
    except Exception as e:
        return _err(rid, 5015, str(e))


@method("reload.env")
def _(rid, params: dict) -> dict:
    """Re-read ``~/.takyon/.env`` into the gateway process via
    ``takyon_cli.config.reload_env``, matching classic CLI's ``/reload``
    handler.  Newly added API keys take effect on the next agent call
    without restarting the TUI.

    The credential pool / provider routing for any *already-constructed*
    agent does not auto-rebuild — that's the same behaviour as classic
    CLI's ``/reload``.  Users who want a brand-new credential resolution
    should follow with ``/new``.
    """
    try:
        from takyon_cli.config import reload_env

        count = reload_env()
        return _ok(rid, {"updated": int(count)})
    except Exception as e:
        return _err(rid, 5015, str(e))


_TUI_HIDDEN: frozenset[str] = frozenset(
    {
        "sethome",
        "set-home",
        "commands",
        "approve",
        "deny",
    }
)

_TUI_EXTRA: list[tuple[str, str, str]] = [
    ("/compact", "Toggle compact display mode", "TUI"),
    ("/logs", "Show recent gateway log lines", "TUI"),
    ("/mouse", "Toggle mouse/wheel tracking [on|off|toggle]", "TUI"),
]

# Commands that queue messages onto _pending_input in the CLI.
# In the TUI the slash worker subprocess has no reader for that queue,
# so slash.exec rejects them → TUI falls through to command.dispatch.
_PENDING_INPUT_COMMANDS: frozenset[str] = frozenset(
    {
        "retry",
        "queue",
        "q",
        "steer",
        "plan",
        "goal",
    }
)

_WORKER_BLOCKED_COMMANDS: frozenset[str] = frozenset({"snapshot", "snap"})


@method("commands.catalog")
def _(rid, params: dict) -> dict:
    """Registry-backed slash metadata for the TUI — categorized, no aliases."""
    try:
        from takyon_cli.commands import (
            COMMAND_REGISTRY,
            SUBCOMMANDS,
            _build_description,
        )

        all_pairs: list[list[str]] = []
        canon: dict[str, str] = {}
        categories: list[dict] = []
        cat_map: dict[str, list[list[str]]] = {}
        cat_order: list[str] = []

        for cmd in COMMAND_REGISTRY:
            if cmd.name in _TUI_HIDDEN or cmd.gateway_only:
                continue

            c = f"/{cmd.name}"
            canon[c.lower()] = c
            for a in cmd.aliases:
                canon[f"/{a}".lower()] = c

            desc = _build_description(cmd)
            all_pairs.append([c, desc])

            cat = cmd.category
            if cat not in cat_map:
                cat_map[cat] = []
                cat_order.append(cat)
            cat_map[cat].append([c, desc])

        for name, desc, cat in _TUI_EXTRA:
            all_pairs.append([name, desc])
            if cat not in cat_map:
                cat_map[cat] = []
                cat_order.append(cat)
            cat_map[cat].append([name, desc])

        warning = ""
        try:
            qcmds = _load_cfg().get("quick_commands", {}) or {}
            if isinstance(qcmds, dict) and qcmds:
                bucket = "User commands"
                if bucket not in cat_map:
                    cat_map[bucket] = []
                    cat_order.append(bucket)
                for qname, qc in sorted(qcmds.items()):
                    if not isinstance(qc, dict):
                        continue
                    key = f"/{qname}"
                    canon[key.lower()] = key
                    qtype = qc.get("type", "")
                    if qtype == "exec":
                        default_desc = f"exec: {qc.get('command', '')}"
                    elif qtype == "alias":
                        default_desc = f"alias → {qc.get('target', '')}"
                    else:
                        default_desc = qtype or "quick command"
                    qdesc = str(qc.get("description") or default_desc)
                    qdesc = qdesc[:120] + ("…" if len(qdesc) > 120 else "")
                    all_pairs.append([key, qdesc])
                    cat_map[bucket].append([key, qdesc])
        except Exception as e:
            if not warning:
                warning = f"quick_commands discovery unavailable: {e}"

        skill_count = 0
        try:
            from agent.skill_commands import scan_skill_commands

            for k, info in sorted(scan_skill_commands().items()):
                d = str(info.get("description", "Skill"))
                all_pairs.append([k, d[:120] + ("…" if len(d) > 120 else "")])
                skill_count += 1
        except Exception as e:
            warning = f"skill discovery unavailable: {e}"

        for cat in cat_order:
            categories.append({"name": cat, "pairs": cat_map[cat]})

        sub = {k: v[:] for k, v in SUBCOMMANDS.items()}
        return _ok(
            rid,
            {
                "pairs": all_pairs,
                "sub": sub,
                "canon": canon,
                "categories": categories,
                "skill_count": skill_count,
                "warning": warning,
            },
        )
    except Exception as e:
        return _err(rid, 5020, str(e))


def _cli_exec_blocked(argv: list[str]) -> str | None:
    """Return user hint if this argv must not run headless in the gateway process."""
    if not argv:
        return "bare `takyon` is interactive — use `/takyon chat -q …` or run `takyon` in another terminal"
    a0 = argv[0].lower()
    if a0 == "setup":
        return "`takyon setup` needs a full terminal — run it outside the TUI"
    if a0 == "gateway":
        return "`takyon gateway` is long-running — run it in another terminal"
    if a0 == "sessions" and len(argv) > 1 and argv[1].lower() == "browse":
        return "`takyon sessions browse` is interactive — use /resume here, or run browse in another terminal"
    if a0 == "config" and len(argv) > 1 and argv[1].lower() == "edit":
        return "`takyon config edit` needs $EDITOR in a real terminal"
    return None


@method("cli.exec")
def _(rid, params: dict) -> dict:
    """Run `python -m takyon_cli.main` with argv; capture stdout/stderr (non-interactive only)."""
    argv = params.get("argv", [])
    if not isinstance(argv, list) or not all(isinstance(x, str) for x in argv):
        return _err(rid, 4003, "argv must be list[str]")
    hint = _cli_exec_blocked(argv)
    if hint:
        return _ok(rid, {"blocked": True, "hint": hint, "code": -1, "output": ""})
    try:
        r = subprocess.run(
            [sys.executable, "-m", "takyon_cli.main", *argv],
            capture_output=True,
            text=True,
            timeout=min(int(params.get("timeout", 240)), 600),
            cwd=os.getcwd(),
            env=os.environ.copy(),
        )
        parts = [r.stdout or "", r.stderr or ""]
        out = "\n".join(p for p in parts if p).strip() or "(no output)"
        return _ok(
            rid, {"blocked": False, "code": r.returncode, "output": out[:48_000]}
        )
    except subprocess.TimeoutExpired:
        return _err(rid, 5016, "cli.exec: timeout")
    except Exception as e:
        return _err(rid, 5017, str(e))


def _takyon_business_payload_from_summary(summary: Any) -> dict[str, Any] | None:
    business = summary.get("business") if isinstance(summary, dict) else None
    return business if isinstance(business, dict) else None


def _takyon_business_payload(store: Any, slug: str) -> dict[str, Any] | None:
    try:
        data = store.read(scope=f"business:{slug}", query="summary")
        return _takyon_business_payload_from_summary(data)
    except Exception:
        return None


def _takyon_business_home_snapshot(
    store: Any,
    slug: str,
    *,
    sync_files: bool = True,
) -> dict[str, Any]:
    business_slug = str(slug or "").strip().lower()
    if not business_slug:
        return {"current": {}, "overview": {}}

    def as_dict(value: Any) -> dict[str, Any]:
        return value if isinstance(value, dict) else {}

    def as_list(value: Any) -> list[Any]:
        return value if isinstance(value, list) else []

    def as_int(value: Any) -> int:
        try:
            return int(value or 0)
        except Exception:
            return 0

    def as_text(value: Any) -> str:
        return str(value or "").strip()

    def openable_url(value: Any) -> str:
        text = as_text(value)
        if not text:
            return ""
        if re.match(r"^(https?://|data:)", text, re.I):
            return text
        if re.match(r"^[a-z0-9][a-z0-9.-]*\.[a-z]{2,}(?:/.*)?$", text, re.I):
            return text
        return ""

    def headline(kind: str, status: str) -> str:
        label = {
            "ceo_bootstrap": "CEO bootstrap",
            "ceo_wake": "CEO wake",
            "ceo_turn": "CEO turn",
            "product.deploy": "Product publish",
            "product.build": "Product build",
        }.get(kind, "Work request")
        status_text = as_text(status).lower() or "recorded"
        if status_text == "running":
            return f"{label} is running."
        if status_text == "queued":
            return f"{label} is queued."
        if status_text in {"completed", "done", "succeeded", "success"}:
            return f"{label} completed."
        if status_text in {"failed", "blocked", "error"}:
            return f"{label} needs attention."
        return f"{label} is {status_text}."

    def tone(status: str) -> str:
        status_text = as_text(status).lower()
        if re.search(r"blocked|fail|error|missing|attention", status_text):
            return "blocked"
        if re.search(r"queued|scheduled|waiting|pending", status_text):
            return "waiting"
        if re.search(r"done|complete|completed|success|passed", status_text):
            return "done"
        if re.search(r"running|active|working", status_text):
            return "active"
        return "neutral"

    def trace_status(status: str) -> str:
        status_text = as_text(status).lower()
        if status_text in {"started", "output", "heartbeat"}:
            return "running"
        if re.search(r"blocked|fail|error", status_text):
            return "blocked"
        if re.search(r"queued|scheduled|waiting|pending", status_text):
            return "scheduled"
        if re.search(r"done|complete|completed|success|passed", status_text):
            return "done"
        if re.search(r"running|active", status_text):
            return "running"
        return status_text or "recorded"

    def job_label(kind: str) -> str:
        key = as_text(kind).lower()
        mapping = {
            "ceo_bootstrap": "CEO bootstrap",
            "ceo_wake": "CEO wake",
            "ceo_turn": "CEO turn",
            "product.deploy": "Product publish",
            "product.build": "Product build",
        }
        if key in mapping:
            return mapping[key]
        key = re.sub(r"[._-]+", " ", key).strip()
        return " ".join(part.capitalize() for part in key.split()) if key else "Work request"

    from plugins.takyon.core import (
        _product_surface_operational_facts,
        _read_product_surface_receipt,
        _summarize_operator_work_item,
    )

    with store._connect() as conn:
        store._enforce_operator_business_access(conn, business_slug)
        business = store._ensure_business(conn, business_slug)
        budget = store._ensure_app_budget(conn, business_slug)
        surface = store._app_surface_contract(conn, business_slug)

        users = conn.execute(
            "SELECT COUNT(*) AS count FROM app_users WHERE business_slug = ?",
            (business_slug,),
        ).fetchone()
        paid_customers = conn.execute(
            """
            SELECT COUNT(DISTINCT app_user_id) AS count
            FROM app_entitlements
            WHERE business_slug = ? AND status IN ('active', 'trialing') AND tier IN ('paid', 'pro', 'team', 'owner')
            """,
            (business_slug,),
        ).fetchone()
        mrr = conn.execute(
            """
            SELECT COALESCE(SUM(
                CASE
                  WHEN p.billing_interval = 'year' THEN p.price_cents / 12.0
                  WHEN p.billing_interval = 'month' THEN p.price_cents
                  ELSE 0
                END
            ), 0) AS mrr_cents
            FROM app_entitlements e
            JOIN app_plan_policies p
              ON p.business_slug = e.business_slug AND p.plan_key = e.plan_key
            WHERE e.business_slug = ? AND e.status IN ('active', 'trialing')
            """,
            (business_slug,),
        ).fetchone()
        revenue = conn.execute(
            "SELECT COALESCE(SUM(amount_paid_cents), 0) AS cents FROM app_revenue_events WHERE business_slug = ?",
            (business_slug,),
        ).fetchone()
        checkout_intents = conn.execute(
            "SELECT COUNT(*) AS count FROM app_checkout_intents WHERE business_slug = ?",
            (business_slug,),
        ).fetchone()
        usage = conn.execute(
            """
            SELECT COALESCE(SUM(actual_cost_microusd), 0) AS actual,
                   COALESCE(SUM(estimated_cost_microusd), 0) AS estimated
            FROM app_usage_events
            WHERE business_slug = ? AND created_at >= ?
            """,
            (business_slug, budget["current_period_start"]),
        ).fetchone()
        queued_jobs = conn.execute(
            f"SELECT COUNT(*) AS count FROM {store._work_requests_table()} WHERE business_slug = ? AND status = 'queued'",
            (business_slug,),
        ).fetchone()
        latest_jobs: list[dict[str, Any]] = []
        latest_job: dict[str, Any] = {}
        for table_name, source_name in (
            (store._work_requests_table(), "job"),
            ("jobs", "worker"),
        ):
            try:
                rows = conn.execute(
                    f"""
                    SELECT id, kind, status, payload_json, created_at, updated_at
                    FROM {table_name}
                    WHERE business_slug = ?
                    ORDER BY updated_at DESC, created_at DESC
                    LIMIT 6
                    """,
                    (business_slug,),
                ).fetchall()
            except Exception:
                continue
            for row in rows:
                item = store._row_to_dict(row)
                if not item:
                    continue
                item["source"] = source_name
                latest_jobs.append(item)
        latest_jobs.sort(
            key=lambda item: (
                as_text(item.get("updated_at") or item.get("created_at")),
                as_text(item.get("id")),
            ),
            reverse=True,
        )
        latest_jobs = latest_jobs[:8]
        latest_job_views: list[dict[str, Any]] = []
        for item in latest_jobs:
            summary = _summarize_operator_work_item(item)
            view = dict(item)
            view["status"] = summary.get("status") or item.get("status") or "idle"
            view["detail"] = summary.get("detail") or item.get("detail") or ""
            view["error"] = summary.get("error") or ""
            latest_job_views.append(view)
        latest_jobs = latest_job_views
        if latest_jobs:
            latest_job = latest_jobs[0]

        trace_entries: list[dict[str, Any]] = []
        seen_trace_keys: set[str] = set()
        try:
            worker_queued = conn.execute(
                "SELECT COUNT(*) AS count FROM jobs WHERE business_slug = ? AND status = 'queued'",
                (business_slug,),
            ).fetchone()
            if worker_queued is not None:
                queued_jobs = worker_queued
        except Exception:
            pass
        try:
            event_rows = conn.execute(
                """
                SELECT * FROM events
                WHERE business_slug = ? AND event_type LIKE 'dashboard.run.%'
                ORDER BY created_at DESC
                LIMIT 24
                """,
                (business_slug,),
            ).fetchall()
            for row in event_rows:
                event = store._row_to_dict(row)
                if not event:
                    continue
                payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
                event_kind = as_text(payload.get("kind"))
                status_value = as_text(payload.get("status") or event.get("event_type")).replace("dashboard.run.", "")
                detail = as_text(payload.get("line") or payload.get("detail") or payload.get("command"))
                updated_at = as_text(event.get("created_at"))
                trace_payload = payload.get("trace") if isinstance(payload.get("trace"), dict) else {}
                trace_entry = {
                    "id": as_text(event.get("id")),
                    "entry_key": as_text(trace_payload.get("entry_key") or event.get("id")),
                    "source": "runtime",
                    "kind": as_text(trace_payload.get("kind") or "note"),
                    "label": as_text(trace_payload.get("label")) or job_label(event_kind or "note"),
                    "detail": as_text(trace_payload.get("detail") or detail or trace_payload.get("summary")),
                    "status": trace_status(as_text(trace_payload.get("status") or status_value)),
                    "tone": tone(as_text(trace_payload.get("status") or status_value)),
                    "updated_at": updated_at,
                    "tool_name": as_text(trace_payload.get("tool_name")),
                    "skill_name": as_text(trace_payload.get("skill_name")),
                    "summary": as_text(trace_payload.get("summary")),
                }
                trace_key = as_text(trace_entry.get("entry_key") or trace_entry.get("id"))
                if trace_key and trace_key not in seen_trace_keys:
                    seen_trace_keys.add(trace_key)
                    trace_entries.append(trace_entry)
        except Exception:
            trace_entries = []

    publish_status = as_text(surface.get("publish_status")).lower()
    public_url = as_text(surface.get("public_url"))
    source_path = as_text(surface.get("source_path"))
    spent_microusd = as_int((usage["actual"] if usage else 0) or (usage["estimated"] if usage else 0))
    try:
        business_root = store._business_root(business_slug, sync=sync_files)
    except TypeError:
        business_root = store._business_root(business_slug)
    receipt = _read_product_surface_receipt(
        business_root,
        as_text(surface.get("publish_receipt_path")),
    )
    receipt_inventory = receipt.get("inventory") if isinstance(receipt.get("inventory"), dict) else {}
    product_facts = _product_surface_operational_facts(
        surface=surface,
        receipt=receipt,
        inventory=receipt_inventory,
    )
    product_blocker = as_text(product_facts.get("blocker") or surface.get("publish_blocker"))
    local_research_outputs: list[dict[str, Any]] = []
    try:
        by_path: dict[str, dict[str, Any]] = {}
        for rel_root in ("research", "brain"):
            root = business_root / rel_root
            if not root.is_dir():
                continue
            for path in root.rglob("*"):
                if not path.is_file() or path.name.startswith("."):
                    continue
                rel = str(path.relative_to(business_root))
                if rel in {"research/index.md", "metrics/summary.md", "metrics/wake-history.md"}:
                    continue
                stat = path.stat()
                by_path[rel] = {
                    "path": rel,
                    "updated_at": int(stat.st_mtime * 1000),
                    "size": int(stat.st_size),
                    "source": rel_root,
                }
        local_research_outputs = list(by_path.values())
        local_research_outputs.sort(key=lambda item: int(item.get("updated_at") or 0), reverse=True)
        local_research_outputs = local_research_outputs[:80]
    except Exception:
        local_research_outputs = []
    latest_job_label = job_label(as_text(latest_job.get("kind")))
    latest_job_status = trace_status(as_text(latest_job.get("status")))
    latest_job_detail = as_text(latest_job.get("detail")) or headline(
        as_text(latest_job.get("kind")),
        as_text(latest_job.get("status")),
    )

    try:
        summary = as_dict(store.read(scope=f"business:{business_slug}", query="summary", limit=12))
    except Exception:
        summary = {}
    conversations = as_dict(summary.get("conversations"))
    unresolved_by_thread: dict[str, int] = {}
    for message in as_list(conversations.get("unresolved")):
        message_dict = as_dict(message)
        thread_id = as_text(message_dict.get("thread_id"))
        if thread_id:
            unresolved_by_thread[thread_id] = unresolved_by_thread.get(thread_id, 0) + 1
    posts: list[dict[str, Any]] = []
    for thread in as_list(conversations.get("threads")):
        thread_dict = as_dict(thread)
        source = as_text(thread_dict.get("source"))
        raw_url = as_text(thread_dict.get("url"))
        source_l = source.lower()
        postish = (
            source_l.startswith("test-")
            or bool(raw_url)
            or source_l == "x"
            or source_l.startswith("x-")
            or any(
                marker in source_l
                for marker in (
                    "post",
                    "outreach",
                    "reddit",
                    "hacker",
                    "twitter",
                    "linkedin",
                    "forum",
                    "social",
                )
            )
        )
        if not postish:
            continue
        url = openable_url(raw_url)
        artifact_path = ""
        if not url and raw_url:
            try:
                artifact_candidate = store._resolve_business_file(business_slug, raw_url, sync=False)
                if artifact_candidate.exists() and artifact_candidate.is_file():
                    artifact_path = raw_url
            except Exception:
                artifact_path = ""
        try:
            conversation_file = as_text(store._conversation_thread_relpath(thread_dict))
        except Exception:
            conversation_file = ""
        mode = "test" if source_l.startswith("test-") or artifact_path.startswith(("distribution/local-published/", "outreach/local-published/")) else "live"
        thread_id = as_text(thread_dict.get("id"))
        posts.append(
            {
                "id": thread_id,
                "title": as_text(thread_dict.get("title") or thread_dict.get("external_id") or source),
                "source": source,
                "status": as_text(thread_dict.get("status")),
                "mode": mode,
                "url": url,
                "artifact_path": artifact_path,
                "conversation_file": conversation_file,
                "created_at": as_text(thread_dict.get("created_at")),
                "updated_at": as_text(thread_dict.get("updated_at")),
                "unresolved_messages": unresolved_by_thread.get(thread_id, 0),
            }
        )

    def latest_channel_job(*needles: str) -> dict[str, Any] | None:
        names = {str(item or "").strip().lower() for item in needles if str(item or "").strip()}
        for job in latest_jobs:
            kind = as_text(job.get("kind")).lower()
            payload = job.get("payload") if isinstance(job.get("payload"), dict) else {}
            channels = {
                as_text(payload.get("channel")).lower(),
                as_text(payload.get("provider")).lower(),
            }
            matched = any(
                name
                and (
                    name in channels
                    or kind.startswith(f"{name}.")
                    or kind.startswith(f"{name}_")
                    or f".{name}" in kind
                    or name in kind
                )
                for name in names
            )
            if not matched:
                continue
            return {
                "id": as_text(job.get("id")),
                "kind": as_text(job.get("kind")),
                "status": as_text(job.get("status")),
                "label": job_label(as_text(job.get("kind"))),
                "detail": as_text(job.get("detail")) or headline(as_text(job.get("kind")), as_text(job.get("status"))),
                "updated_at": as_text(job.get("updated_at") or job.get("created_at")),
                "created_at": as_text(job.get("created_at")),
            }
        return None

    x_items = [
        post
        for post in posts
        if (
            as_text(post.get("source")).lower().replace("test-", "", 1) == "x"
            or as_text(post.get("source")).lower().startswith("x-")
            or "twitter" in as_text(post.get("source")).lower()
        )
    ]
    x_job = latest_channel_job("x", "twitter")
    outreach_channels = {
        "x": {
            "channel": "x",
            "label": "X",
            "status": (
                "published_local"
                if any(as_text(item.get("mode")).lower() == "test" for item in x_items)
                else "published"
                if x_items
                else as_text((x_job or {}).get("status")) or "missing"
            ),
            "updated_at": as_text((x_items[0] if x_items else {}).get("updated_at") or (x_job or {}).get("updated_at")),
            "draft_path": "",
            "items": x_items[:8],
            "latest_job": x_job,
            "published_count": len(x_items),
        },
        "reddit": {
            "channel": "reddit",
            "label": "Reddit",
            "status": "missing",
            "updated_at": "",
            "campaigns": [],
            "latest_job": latest_channel_job("reddit"),
            "campaign_count": 0,
            "metrics_count": 0,
        },
        "meta": {
            "channel": "meta",
            "label": "Meta",
            "status": "missing",
            "updated_at": "",
            "campaigns": [],
            "latest_job": latest_channel_job("meta", "facebook", "instagram"),
            "campaign_count": 0,
            "metrics_count": 0,
        },
    }

    task_cards: list[dict[str, Any]] = []
    for entry in trace_entries[:8]:
        task_cards.append(
            {
                "id": f"runtime:{entry.get('entry_key') or entry.get('id')}",
                "source": "runtime",
                "label": entry.get("label") or "Runtime event",
                "status": entry.get("status") or "recorded",
                "detail": entry.get("detail") or "",
                "tone": entry.get("tone") or tone(entry.get("status") or ""),
                "updated_at": entry.get("updated_at") or "",
            }
        )
    for job in latest_jobs[:6]:
        task_cards.append(
            {
                "id": f"{as_text(job.get('source') or 'job')}:{as_text(job.get('id'))}",
                "source": as_text(job.get("source") or "job"),
                "label": job_label(as_text(job.get("kind"))),
                "status": trace_status(as_text(job.get("status"))),
                "detail": latest_job_detail if job is latest_job else (as_text(job.get("detail")) or headline(as_text(job.get("kind")), as_text(job.get("status")))),
                "tone": tone(as_text(job.get("status"))),
                "updated_at": as_text(job.get("updated_at") or job.get("created_at")),
            }
        )
    if product_blocker:
        task_cards.insert(
            0,
            {
                "id": "product:blocker",
                "source": "product",
                "label": "Product publish blocker",
                "status": "blocked",
                "detail": product_blocker,
                "tone": "blocked",
                "updated_at": as_text(surface.get("published_at") or surface.get("updated_at")),
            },
        )

    current_action = {
        "source": "idle",
        "label": "Business is synced.",
        "status": "idle",
        "detail": "",
        "blocker": product_blocker,
    }
    for preferred_status in ("running", "scheduled", "blocked"):
        for task in task_cards:
            status_value = trace_status(as_text(task.get("status")))
            if status_value != preferred_status:
                continue
            current_action = {
                "source": as_text(task.get("source")),
                "label": as_text(task.get("label")) or "Current work",
                "status": status_value,
                "detail": as_text(task.get("detail")),
                "blocker": product_blocker,
            }
            break
        if current_action["status"] != "idle":
            break
    if current_action["status"] == "idle" and latest_job:
        current_action = {
            "source": as_text(latest_job.get("source") or "job"),
            "label": latest_job_label,
            "status": latest_job_status or "recorded",
            "detail": latest_job_detail,
            "blocker": product_blocker,
        }

    website_status = (
        "published"
        if publish_status == "published" and public_url
        else "publish_blocked"
        if product_blocker
        else "local_source"
        if source_path
        else "missing"
    )

    return {
        "current": {
            "name": as_text(business.get("name")) or business_slug,
            "goal": as_text(business.get("goal")),
            "mode": "live"
            if as_text(business.get("mode") or business.get("status") or business.get("state")).strip().lower() != "live"
            else "live",
        },
        "overview": {
            "product": {
                "status": as_text(surface.get("status")) or "missing",
                "source_path": source_path,
                "runtime_api_base": as_text(surface.get("runtime_api_base")),
                "publish_target": as_text(surface.get("publish_target")),
                "publish_policy": as_text(surface.get("publish_policy")),
                "publish_status": as_text(surface.get("publish_status")),
                "public_url": public_url,
                "published_at": as_text(surface.get("published_at")),
                "publish_receipt_path": as_text(surface.get("publish_receipt_path")),
                "publish_blocker": product_blocker,
                "publish_mode": as_text(product_facts.get("publish_mode")),
                "publish_source_path": as_text(product_facts.get("publish_source_path")),
                "inventory_status": as_text(product_facts.get("inventory_status")),
                "detected_frameworks": list(product_facts.get("detected_frameworks") or []),
                "detected_package_manager": as_text(product_facts.get("detected_package_manager")),
                "runtime_integrations": list(product_facts.get("runtime_integrations") or []),
                "workflow_markers": list(product_facts.get("workflow_markers") or []),
                "latest_check_status": as_text(product_facts.get("latest_check_status")),
                "latest_check_command": as_text(product_facts.get("latest_check_command")),
                "latest_check_error": as_text(product_facts.get("latest_check_error")),
                "notes": as_text(surface.get("notes")),
            },
            "metrics": {
                "users": as_int(users["count"] if users else 0),
                "paid_customers": as_int(paid_customers["count"] if paid_customers else 0),
                "mrr_cents": as_int(round(float((mrr["mrr_cents"] if mrr else 0) or 0))),
                "revenue_cents": as_int(revenue["cents"] if revenue else 0),
                "checkout_intents": as_int(checkout_intents["count"] if checkout_intents else 0),
                "usage_events": 0,
                "unresolved_inbound": 0,
                "queued_jobs": as_int(queued_jobs["count"] if queued_jobs else 0),
            },
            "budget": {
                "business_amount": None,
                "business_status": "",
                "app_status": as_text(budget.get("status")),
                "app_limit_microusd": as_int(budget.get("hard_limit_microusd")),
                "app_spent_microusd": spent_microusd,
                "app_remaining_microusd": as_int(budget.get("hard_limit_microusd")) - spent_microusd,
            },
            "posts": posts[:12],
            "cron": [],
            "files": [],
            "jobs": latest_jobs[:6],
            "agent_runs": [],
            "workers": [],
            "trace": trace_entries[:12],
            "tasks": task_cards[:16],
            "status_cards": [
                {
                    "label": "Current action",
                    "status": current_action["status"],
                    "detail": current_action["label"],
                    "tone": tone(current_action["status"]),
                },
                {
                    "label": "Product publish",
                    "status": website_status,
                    "detail": product_blocker or as_text(surface.get("publish_status")) or "Not published yet.",
                    "tone": "blocked" if product_blocker else ("done" if website_status == "published" else "waiting"),
                },
            ],
            "current_action": current_action,
            "ceo_loop": {
                "status": current_action["status"],
                "headline": current_action["label"],
                "detail": current_action["detail"],
                "next_action": current_action["detail"] or current_action["label"],
            },
            "wake_health": {},
            "research": {
                "status": "visible" if local_research_outputs else "needed",
                "latest_path": as_text((local_research_outputs[0] if local_research_outputs else {}).get("path")),
                "count": len(local_research_outputs),
                "outputs": local_research_outputs[:24],
            },
            "research_outputs": local_research_outputs,
            "artifacts": {
                "website": {
                    "status": website_status,
                    "path": "",
                    "updated_at": "",
                    "deploy_status": "",
                    "source_path": source_path,
                    "public_url": public_url,
                    "publish_target": as_text(surface.get("publish_target")),
                    "publish_policy": as_text(surface.get("publish_policy")),
                    "publish_status": as_text(surface.get("publish_status")),
                    "publish_blocker": product_blocker,
                    "publish_receipt_path": as_text(surface.get("publish_receipt_path")),
                    "publish_mode": as_text(product_facts.get("publish_mode")),
                    "publish_source_path": as_text(product_facts.get("publish_source_path")),
                },
                "outreach": {
                    "status": as_text(outreach_channels["x"].get("status")) or "missing",
                    "path": "",
                    "receipt": "",
                    "updated_at": as_text(outreach_channels["x"].get("updated_at")),
                    "published_count": as_int(outreach_channels["x"].get("published_count")),
                    "items": [],
                    "receipts": [],
                    "channels": outreach_channels,
                },
                "creative_assets": {
                    "status": "missing",
                    "path": "",
                    "receipt": "",
                    "updated_at": "",
                    "count": 0,
                },
            },
            "conversations": {
                "active_threads": len(as_list(conversations.get("threads"))),
                "unresolved_messages": sum(unresolved_by_thread.values()),
                "latest_message_at": "",
            },
            "generated_at": "",
            "pulse_warning": "",
        },
    }


def _takyon_business_slugs(businesses: Any) -> set[str]:
    if not isinstance(businesses, list):
        return set()
    return {
        str(item.get("slug") or "").strip()
        for item in businesses
        if isinstance(item, dict) and str(item.get("slug") or "").strip()
    }


_TAKYON_CREATE_BUSINESS_PATTERNS = (
    re.compile(r"\b(?:create|build|make|start|launch|bootstrap|set\s+up)\b.{0,80}\b(?:business|micro\s*saas|saas|startup|company)\b", re.I | re.S),
    re.compile(r"\b(?:new|another)\b.{0,40}\b(?:business|micro\s*saas|saas|startup|company)\b", re.I | re.S),
)
_TAKYON_EXISTING_BUSINESS_TARGET_PATTERNS = (
    re.compile(r"\bfor\s+(?:this|the)\s+(?:business|company|startup|saas|micro\s*saas|app|product)\b", re.I),
    re.compile(r"\bthis\s+(?:business|company|startup|saas|micro\s*saas|app|product)\b", re.I),
)


def _takyon_prompt_may_create_business(text: str) -> bool:
    compact = " ".join(str(text or "").strip().split())
    if not compact:
        return False
    if any(pattern.search(compact) for pattern in _TAKYON_EXISTING_BUSINESS_TARGET_PATTERNS):
        return False
    return any(pattern.search(compact) for pattern in _TAKYON_CREATE_BUSINESS_PATTERNS)


def _takyon_prompt_mentions_budget(text: str) -> bool:
    compact = " ".join(str(text or "").strip().split()).lower()
    if not compact:
        return False
    if re.search(r"\b(?:budget|cap|spend limit|spend cap|runway|limit)\b", compact):
        return True
    return bool(re.search(r"(?:\$|usd\s*)\d+(?:[,.]\d+)?|\d+(?:[,.]\d+)?\s*(?:usd|dollars?)\b", compact))


def _build_takyon_prompt_text(
    session: dict,
    text: str,
    *,
    create_in_test_mode: bool = False,
) -> str:
    from plugins.takyon.cli import _operator_context_message

    current_business = str(session.get("takyon_current_business") or "") or None
    prompt_text = _operator_context_message(text, current_business)
    if _takyon_prompt_may_create_business(text):
        try:
            data = _takyon_store(session).read(scope="global", query="list_businesses", limit=200)
            session["takyon_businesses_before_prompt"] = sorted(_takyon_business_slugs(data.get("businesses")))
            session["takyon_pending_business_create"] = True
            session["takyon_pending_business_create_at"] = time.time()
        except Exception:
            session["takyon_businesses_before_prompt"] = list(session.get("takyon_known_businesses") or [])
            session["takyon_pending_business_create"] = True
            session["takyon_pending_business_create_at"] = time.time()
        if not _takyon_prompt_mentions_budget(text):
            prompt_text = (
                "Budget guard: the operator appears to be asking for a new business but did not state a budget. "
                "Before live spending, paid provider calls, customer-facing AI usage, or app usage-budget commitments, "
                "ask one concise budget question or set an explicit budget only if the operator/configured creation path provides one. "
                "If the product has AI-backed customer usage, that usage budget is funded by the active paid subscription's included AI budget (set plan pricing with business_upsert_app_plan); there is no separate operator usage-cap tool.\n\n"
                + prompt_text
            )
    return prompt_text


def _takyon_maybe_auto_enter_created_business(
    session: dict | None,
    businesses: list[Any],
) -> tuple[str | None, str | None]:
    if session is None:
        return None, None

    known = _takyon_business_slugs(businesses)
    pending = bool(session.get("takyon_pending_business_create"))
    pending_at = float(session.get("takyon_pending_business_create_at") or 0)
    current = str(session.get("takyon_current_business") or "").strip()

    auto_slug: str | None = None
    warning: str | None = None
    if pending and (time.time() - pending_at) <= 900:
        before = set(session.get("takyon_businesses_before_prompt") or [])
        created = sorted(known - before)
        if not current and len(created) == 1:
            auto_slug = created[0]
            session["takyon_current_business"] = auto_slug
            session.pop("takyon_pending_business_create", None)
            session.pop("takyon_businesses_before_prompt", None)
            session.pop("takyon_pending_business_create_at", None)
        elif len(created) > 1:
            warning = "Multiple new businesses were created; choose one from the scope menu."
            session.pop("takyon_pending_business_create", None)
            session.pop("takyon_businesses_before_prompt", None)
            session.pop("takyon_pending_business_create_at", None)
    elif pending:
        session.pop("takyon_pending_business_create", None)
        session.pop("takyon_businesses_before_prompt", None)
        session.pop("takyon_pending_business_create_at", None)

    session["takyon_known_businesses"] = sorted(known)
    return auto_slug, warning


def _takyon_registry_display_payload() -> dict[str, Any]:
    try:
        from plugins.takyon.core import TAKYON_TOOL_DEFINITIONS
    except Exception as exc:
        return {"version": None, "tools": {}, "skills": {}, "warning": str(exc)}

    detail_keys = {
        "business_read_business": ["business"],
        "business_calculate_pulse": ["business"],
        "business_upsert_app_surface_contract": ["source_path", "publish_target", "business"],
        "business_x_publish_outreach": ["channel", "destination_label", "destination_url", "target", "business"],
        "business_x_search": ["query", "business"],
        "business_publish_test_outreach": ["channel", "destination_label", "destination_url", "target", "business"],
        "business_reddit_publish_outreach": ["subreddit", "thread_external_id", "destination_label", "business"],
        "business_claude_agent_task": ["workspace", "source_path", "business"],
        "business_conversation_agent_task": ["goal", "task", "context", "business"],
        "business_record_agent": ["scope", "status", "business"],
    }
    tools: dict[str, dict[str, Any]] = {}
    for item in TAKYON_TOOL_DEFINITIONS:
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        tools[name] = {
            "display_name": name.replace("business_", "").replace("_", " ").strip().title(),
            "activity_verb": "",
            "detail_hint": "",
            "detail_keys": detail_keys.get(name, []),
            "implementation_status": "implemented",
            "category": "",
            "effect": "",
        }

    skills_root = Path(__file__).resolve().parents[1] / "skills" / "takyon"
    skills: dict[str, dict[str, Any]] = {}
    for skill_file in sorted(skills_root.glob("*/SKILL.md")):
        try:
            meta = parse_frontmatter(skill_file.read_text(encoding="utf-8"))[0]
        except Exception:
            meta = {}
        skill_name = str(meta.get("name") or skill_file.parent.name).strip()
        if not skill_name:
            continue
        skills[skill_name] = {
            "display_name": skill_name,
            "activity_verb": "",
            "detail_hint": "",
            "detail_keys": [],
            "implementation_status": "implemented",
            "category": str(((meta.get("metadata") or {}).get("hermes") or {}).get("category") or "").strip(),
            "effect": "",
        }
    return {"version": "takyon-hermes-skills", "tools": tools, "skills": skills}


def _takyon_business_overview_payload(
    store: Any,
    slug: str,
    *,
    summary_data: Any | None = None,
) -> dict[str, Any]:
    def as_dict(value: Any) -> dict[str, Any]:
        return value if isinstance(value, dict) else {}

    def as_list(value: Any) -> list[Any]:
        return value if isinstance(value, list) else []

    def as_int(value: Any) -> int:
        try:
            return int(value or 0)
        except Exception:
            return 0

    def brief_text(value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, (str, int, float, bool)):
            return str(value)
        return ""

    def openable_url(value: Any) -> str:
        text = brief_text(value).strip()
        if not text:
            return ""
        if re.match(r"^(https?://|data:)", text, re.I):
            return text
        if re.match(r"^[a-z0-9][a-z0-9.-]*\.[a-z]{2,}(?:/.*)?$", text, re.I):
            return text
        return ""

    def parse_ts(value: Any) -> float | None:
        text = brief_text(value).strip()
        if not text:
            return None
        try:
            return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
        except Exception:
            return None

    def human_kind(value: Any) -> str:
        text = brief_text(value).strip() or "work request"
        text = re.sub(r"[._-]+", " ", text)
        return " ".join(part.capitalize() for part in text.split())

    def status_tone(value: Any) -> str:
        text = brief_text(value).lower()
        if re.search(r"blocked|fail|error|stale|attention|missing", text):
            return "blocked"
        if re.search(r"pending|queued|scheduled|waiting", text):
            return "waiting"
        if re.search(r"done|complete|completed|success|succeeded|passed", text):
            return "done"
        if re.search(r"running|active|watch", text):
            return "active"
        return "neutral"

    def trace_status(value: Any) -> str:
        text = brief_text(value).strip().lower()
        if text in {"started", "output", "heartbeat"}:
            return "running"
        return text or "recorded"

    def upsert_trace_entry(
        entries_by_key: dict[str, dict[str, Any]],
        order: list[str],
        entry: dict[str, Any],
    ) -> None:
        key = brief_text(entry.get("entry_key") or entry.get("id"))
        if not key:
            return
        current = entries_by_key.get(key)
        if current is None:
            entries_by_key[key] = entry
            order.append(key)
            return
        merged = dict(current)
        for field in (
            "id",
            "source",
            "kind",
            "label",
            "detail",
            "status",
            "tone",
            "updated_at",
            "tool_name",
            "skill_name",
            "summary",
        ):
            value = brief_text(entry.get(field)) if field not in {"id"} else entry.get(field)
            if value not in (None, "", [], {}):
                merged[field] = value
        entries_by_key[key] = merged

    def legacy_trace_entry(
        *,
        event_id: str,
        event_kind: str,
        event_status: str,
        detail: str,
        updated_at: str,
    ) -> dict[str, Any] | None:
        text = detail.strip()
        status = trace_status(event_status)
        tone = status_tone(status)
        if event_kind in {"ceo_bootstrap", "ceo_wake", "ceo_turn"} and event_status in {"started", "completed", "failed"}:
            return {
                "id": event_id,
                "entry_key": f"turn:{event_kind}",
                "source": "runtime",
                "kind": "turn",
                "label": job_label(event_kind if event_kind != "ceo_turn" else "ceo_turn").replace("Ceo", "CEO"),
                "detail": text or ("CEO turn completed." if status == "completed" else "CEO turn is running."),
                "status": status,
                "tone": tone,
                "updated_at": updated_at,
                "tool_name": "",
                "skill_name": "",
                "summary": "",
            }
        tool_started = re.match(r"^tool started -> ([^·]+?)(?: · (.+))?$", text, re.I)
        if tool_started:
            tool_name = brief_text(tool_started.group(1)).strip()
            preview = brief_text(tool_started.group(2)).strip()
            return {
                "id": event_id,
                "entry_key": f"legacy-tool:{tool_name}:{preview}",
                "source": "runtime",
                "kind": "tool",
                "label": human_kind(tool_name),
                "detail": preview or text,
                "status": "running",
                "tone": "active",
                "updated_at": updated_at,
                "tool_name": tool_name,
                "skill_name": "",
                "summary": "",
            }
        tool_completed = re.match(r"^tool completed -> ([^·]+?)(?: · (.+))?$", text, re.I)
        if tool_completed:
            tool_name = brief_text(tool_completed.group(1)).strip()
            summary = brief_text(tool_completed.group(2)).strip()
            return {
                "id": event_id,
                "entry_key": f"legacy-tool:{tool_name}",
                "source": "runtime",
                "kind": "tool",
                "label": human_kind(tool_name),
                "detail": summary or text,
                "status": "completed",
                "tone": "done",
                "updated_at": updated_at,
                "tool_name": tool_name,
                "skill_name": "",
                "summary": summary,
            }
        if text.startswith("agent -> "):
            return {
                "id": event_id,
                "entry_key": f"note:{event_id}",
                "source": "runtime",
                "kind": "note",
                "label": "Agent",
                "detail": text.replace("agent -> ", "", 1).strip(),
                "status": status,
                "tone": tone,
                "updated_at": updated_at,
                "tool_name": "",
                "skill_name": "",
                "summary": "",
            }
        return None

    def job_label(kind: Any) -> str:
        value = brief_text(kind)
        if value == "ceo_bootstrap":
            return "CEO bootstrap"
        if value == "ceo_wake":
            return "CEO wake"
        if value == "ceo_turn":
            return "CEO turn"
        if value == "product.deploy":
            return "Publish product site"
        if value == "product.build":
            return "Build product surface"
        if value.startswith("distribution.") or value.startswith("outreach."):
            return "Publish or test outreach"
        if value.startswith("vendor.stripe"):
            return "Prepare Stripe approval"
        if "creative" in value or "ad" in value:
            return "Generate ad creative"
        return human_kind(value)

    def job_detail(job: dict[str, Any]) -> str:
        kind = brief_text(job.get("kind"))
        status = brief_text(job.get("status") or "recorded")
        payload = as_dict(job.get("payload"))
        blockers = [brief_text(item) for item in as_list(payload.get("blockers")) if brief_text(item)]
        missing = [
            brief_text(item)
            for item in as_list(payload.get("missing_credentials_suppressed") or payload.get("requires_env"))
            if brief_text(item)
        ]
        blocked_reason = brief_text(payload.get("blocked_reason") or payload.get("error") or payload.get("note"))
        if kind == "ceo_bootstrap":
            if status == "queued":
                return "Bootstrap is queued and waiting for the worker."
            if status == "running":
                return "Bootstrap is running and will sync durable results back when complete."
            return blocked_reason or f"CEO bootstrap is {status}."
        if kind == "ceo_wake":
            if status == "queued":
                return "CEO wake is queued."
            if status == "running":
                return "CEO wake is running."
            return blocked_reason or f"CEO wake is {status}."
        if kind == "product.deploy":
            if blockers:
                return "Website is local; deploy waits on " + ", ".join(blockers[:3]) + "."
            return "Website deploy is recorded as gated follow-up work."
        if kind == "product.build" and blocked_reason:
            return blocked_reason
        if kind.startswith("distribution.") or kind.startswith("outreach."):
            if payload.get("external_side_effects") == "suppressed":
                return "Test-mode outreach is local only; external posting is suppressed."
            if missing:
                return "External posting waits on " + ", ".join(missing[:3]) + "."
        if kind.startswith("vendor.stripe"):
            return blocked_reason or "Checkout cannot go live until Stripe/provider approval is complete."
        return blocked_reason or f"{job_label(kind)} is {status}."

    summary = as_dict(summary_data) if isinstance(summary_data, dict) else {}
    if not summary:
        summary = as_dict(store.read(scope=f"business:{slug}", query="summary", limit=12))
    business = as_dict(summary.get("business"))
    app = as_dict(summary.get("app"))
    surface = as_dict(app.get("surface_contract") or app.get("surface"))
    product_surface_evidence = as_dict(app.get("product_surface"))
    product_inventory = as_dict(app.get("product_inventory"))
    source_path = brief_text(surface.get("source_path"))

    # Curated CEO update (business_post_operator_update). This is the ONLY
    # customer-facing channel: the latest business.operator_update event carries a
    # warm headline + 1-2 sentence summary and a milestone plan. The raw assistant
    # message stream (chain-of-thought / planning) is NEVER surfaced to the
    # customer; the UI renders only this curated card + the milestone rollup.
    operator_update: dict[str, Any] = {}
    for event in as_list(summary.get("events")):
        event_dict = as_dict(event)
        if brief_text(event_dict.get("event_type")) != "business.operator_update":
            continue
        payload = event_dict.get("payload")
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except Exception:
                payload = {}
        operator_update = as_dict(payload)
        operator_update["updated_at"] = brief_text(
            event_dict.get("created_at") or event_dict.get("updated_at")
        )
        break
    operator_update_milestones = [
        m for m in as_list(operator_update.get("milestones")) if isinstance(m, dict)
    ]

    try:
        pulse = as_dict(store.calculate_pulse(slug, limit=5))
    except Exception as exc:
        pulse = {"warning": str(exc)}

    current_state = as_dict(pulse.get("current_state"))
    app_budget = as_dict(current_state.get("app_budget"))
    pulse_summary = as_dict(pulse.get("summary"))
    app_revenue = as_dict(app.get("revenue"))
    app_usage = as_dict(app.get("usage_this_period"))
    conversations = as_dict(summary.get("conversations"))
    unresolved_by_thread: dict[str, int] = {}
    for message in as_list(conversations.get("unresolved")):
        message_dict = as_dict(message)
        thread_id = brief_text(message_dict.get("thread_id"))
        if thread_id:
            unresolved_by_thread[thread_id] = unresolved_by_thread.get(thread_id, 0) + 1
    posts: list[dict[str, Any]] = []
    for thread in as_list(conversations.get("threads")):
        thread_dict = as_dict(thread)
        source = brief_text(thread_dict.get("source"))
        raw_url = brief_text(thread_dict.get("url"))
        url = openable_url(raw_url)
        source_l = source.lower()
        postish = (
            source_l.startswith("test-")
            or bool(raw_url)
            or source_l == "x"
            or source_l.startswith("x-")
            or any(
                marker in source_l
                for marker in (
                    "post",
                    "outreach",
                    "reddit",
                    "hacker",
                    "twitter",
                    "linkedin",
                    "forum",
                    "social",
                )
            )
        )
        if not postish:
            continue
        artifact_path = ""
        if not url and raw_url:
            try:
                artifact_candidate = store._resolve_business_file(slug, raw_url, sync=False)
                if artifact_candidate.exists() and artifact_candidate.is_file():
                    artifact_path = raw_url
            except Exception:
                artifact_path = ""
        try:
            conversation_file = brief_text(store._conversation_thread_relpath(thread_dict))
        except Exception:
            conversation_file = ""
        mode = "test" if source_l.startswith("test-") or artifact_path.startswith(("distribution/local-published/", "outreach/local-published/")) else "live"
        thread_id = brief_text(thread_dict.get("id"))
        posts.append(
            {
                "id": thread_id,
                "title": brief_text(thread_dict.get("title") or thread_dict.get("external_id") or source),
                "source": source,
                "status": brief_text(thread_dict.get("status")),
                "mode": mode,
                "url": url,
                "artifact_path": artifact_path,
                "conversation_file": conversation_file,
                "created_at": brief_text(thread_dict.get("created_at")),
                "updated_at": brief_text(thread_dict.get("updated_at")),
                "unresolved_messages": unresolved_by_thread.get(thread_id, 0),
            }
        )

    try:
        cron_jobs_raw = as_list(store._business_cron_jobs(slug))
    except Exception:
        cron_jobs_raw = []
    cron_jobs = []
    for job in cron_jobs_raw[:8]:
        job_dict = as_dict(job)
        cron_jobs.append(
            {
                "id": brief_text(job_dict.get("id")),
                "name": brief_text(job_dict.get("name")),
                "enabled": bool(job_dict.get("enabled", True)),
                "state": brief_text(job_dict.get("state") or job_dict.get("status")),
                "schedule": brief_text(job_dict.get("schedule_display") or job_dict.get("schedule")),
                "next_run": brief_text(job_dict.get("next_run_at") or job_dict.get("next_run")),
                "last_run": brief_text(job_dict.get("last_run_at") or job_dict.get("last_run")),
            }
        )

    try:
        files_data = as_dict(store.read(scope=f"business:{slug}", query="list_files", path=".", limit=18))
        files_raw = as_list(files_data.get("files"))
    except Exception:
        files_raw = []
    files = [
        {"path": brief_text(as_dict(item).get("path")), "type": brief_text(as_dict(item).get("type"))}
        for item in files_raw
        if brief_text(as_dict(item).get("path"))
    ]

    jobs = []
    for job in as_list(summary.get("jobs"))[:8]:
        job_dict = as_dict(job)
        payload = as_dict(job_dict.get("payload"))
        # The CEO may author an operator-facing milestone intent on the job
        # payload (title/description/category) plus a work_request_id linking
        # raw runtime/tool events back to this milestone. Carry those through so
        # the Tasks panel can show milestone cards instead of raw tool calls.
        jobs.append(
            {
                "id": brief_text(job_dict.get("id")),
                "kind": brief_text(job_dict.get("kind") or job_dict.get("type") or job_dict.get("name")),
                "status": brief_text(job_dict.get("status") or job_dict.get("state")),
                "updated_at": brief_text(job_dict.get("updated_at")),
                "created_at": brief_text(job_dict.get("created_at")),
                "label": job_label(job_dict.get("kind") or job_dict.get("type") or job_dict.get("name")),
                "detail": job_detail({**job_dict, "payload": payload}),
                "tone": status_tone(job_dict.get("status") or job_dict.get("state")),
                "title": brief_text(payload.get("title") or payload.get("summary")),
                "description": brief_text(payload.get("description") or payload.get("why_now")),
                "category": brief_text(payload.get("category")),
                "work_request_id": brief_text(payload.get("work_request_id")),
            }
        )

    agent_runs: list[dict[str, Any]] = []
    workers: list[dict[str, Any]] = []
    runtime_events: list[dict[str, Any]] = []
    trace_entries: list[dict[str, Any]] = []
    conn = None
    try:
        conn = store._connect()
        rows = conn.execute(
            "SELECT * FROM agent_runs WHERE scope = ? OR scope LIKE ? ORDER BY updated_at DESC LIMIT ?",
            (f"business:{slug}", f"business:{slug}/%", 8),
        ).fetchall()
        for row in rows:
            run = as_dict(store._row_to_dict(row))
            result = as_dict(run.get("result"))
            completed = len(as_list(result.get("completed")))
            blockers = len(as_list(result.get("blockers")))
            source = brief_text(result.get("source"))
            workspace = brief_text(result.get("workspace"))
            summary_text = brief_text(result.get("summary"))
            error_text = brief_text(result.get("error"))
            surface_refresh = as_dict(result.get("surface_refresh"))
            detail_parts = []
            if completed:
                detail_parts.append(f"{completed} completed")
            if blockers:
                detail_parts.append(f"{blockers} blocker{'s' if blockers != 1 else ''}")
            if surface_refresh:
                refresh_status = brief_text(surface_refresh.get("status"))
                refresh_path = brief_text(surface_refresh.get("receipt_path"))
                if refresh_status:
                    detail_parts.append(f"surface refresh {refresh_status}")
                if refresh_path:
                    detail_parts.append(refresh_path)
            agent_runs.append(
                {
                    "id": brief_text(run.get("id")),
                    "status": brief_text(run.get("status")),
                    "updated_at": brief_text(run.get("updated_at") or run.get("created_at")),
                    "label": "CEO or worker run",
                    "detail": ", ".join(detail_parts) or summary_text or error_text or brief_text(run.get("prompt"))[:160] or "Run recorded in audit trail.",
                    "tone": status_tone(run.get("status")),
                    # Worker runs record the milestone work_request_id they served;
                    # carry it so the card can nest under that milestone row.
                    "work_request_id": brief_text(result.get("work_request_id")),
                }
            )
            if source or workspace or brief_text(run.get("scope")).startswith(f"business:{slug}/workspace:"):
                worker_tool = "business_claude_agent_task" if source == "claude-agent-sdk" else "business_record_agent"
                purpose = workspace or brief_text(run.get("scope")).replace(f"business:{slug}/workspace:", "").strip()
                workers.append(
                    {
                        "id": brief_text(run.get("id")),
                        "tool_name": worker_tool,
                        "name": "Delegated worker" if worker_tool == "business_claude_agent_task" else "Agent run",
                        "purpose": purpose or summary_text or brief_text(run.get("prompt"))[:120],
                        "status": brief_text(run.get("status")) or "recorded",
                        "updated_at": brief_text(run.get("updated_at") or run.get("created_at")),
                        "latest_detail": error_text or summary_text or ", ".join(detail_parts) or brief_text(run.get("prompt"))[:180],
                        "tone": status_tone(run.get("status")),
                    }
                )
        event_rows = conn.execute(
            "SELECT * FROM events WHERE business_slug = ? AND event_type LIKE 'dashboard.run.%' ORDER BY created_at DESC LIMIT ?",
            (slug, 48),
        ).fetchall()
        seen_runtime_details: set[str] = set()
        trace_by_key: dict[str, dict[str, Any]] = {}
        trace_order: list[str] = []
        for row in event_rows:
            event = as_dict(store._row_to_dict(row))
            payload = as_dict(event.get("payload"))
            event_kind = brief_text(payload.get("kind"))
            status = brief_text(payload.get("status") or event.get("event_type")).replace("dashboard.run.", "")
            if status == "heartbeat":
                continue
            detail = brief_text(payload.get("line") or payload.get("detail"))
            event_id = brief_text(event.get("id"))
            updated_at = brief_text(event.get("created_at"))
            trace_payload = as_dict(payload.get("trace"))
            if trace_payload:
                trace_kind = brief_text(trace_payload.get("kind") or "note")
                trace_entry = {
                    "id": event_id,
                    "entry_key": brief_text(trace_payload.get("entry_key") or event_id),
                    "source": "runtime",
                    "kind": trace_kind,
                    "label": brief_text(trace_payload.get("label")) or job_label(event_kind or trace_kind),
                    "detail": brief_text(trace_payload.get("detail") or detail or trace_payload.get("summary")),
                    "status": trace_status(trace_payload.get("status") or status),
                    "tone": status_tone(trace_status(trace_payload.get("status") or status)),
                    "updated_at": updated_at,
                    "tool_name": brief_text(trace_payload.get("tool_name")),
                    "skill_name": brief_text(trace_payload.get("skill_name")),
                    "summary": brief_text(trace_payload.get("summary")),
                }
                upsert_trace_entry(trace_by_key, trace_order, trace_entry)
            else:
                fallback_entry = legacy_trace_entry(
                    event_id=event_id,
                    event_kind=event_kind,
                    event_status=status,
                    detail=detail,
                    updated_at=updated_at,
                )
                if fallback_entry is not None:
                    upsert_trace_entry(trace_by_key, trace_order, fallback_entry)
            if detail in seen_runtime_details:
                continue
            seen_runtime_details.add(detail)
            label = "CEO live trace"
            if event_kind == "ceo_bootstrap":
                label = "CEO bootstrap"
            elif event_kind == "ceo_wake":
                label = "CEO wake"
            elif event_kind == "ceo_turn":
                label = "CEO turn"
            if trace_payload:
                label = brief_text(trace_payload.get("label")) or label
            lower_detail = detail.lower()
            if label == "CEO live trace" and lower_detail.startswith("agent ->"):
                label = "Agent"
            elif label == "CEO live trace" and "preparing tool ->" in lower_detail:
                label = "Preparing tool"
            elif label == "CEO live trace" and "tool started ->" in lower_detail:
                label = "Tool started"
            elif label == "CEO live trace" and "tool completed ->" in lower_detail:
                label = "Tool completed"
            elif label == "CEO live trace" and lower_detail.startswith("product surface refresh"):
                label = "Product surface refresh"
            runtime_events.append(
                {
                    "id": event_id,
                    "status": trace_status(trace_payload.get("status") if trace_payload else status),
                    "updated_at": updated_at,
                    "label": label,
                    "detail": brief_text(trace_payload.get("detail") if trace_payload else detail) or brief_text(payload.get("command")) or "Runtime event recorded.",
                    "tone": status_tone(trace_payload.get("status") if trace_payload else trace_status(status)),
                    # Trace events emitted inside a worker task carry the active
                    # run_id (the milestone work_request_id); carry it so the raw
                    # tool card nests under that milestone instead of floating.
                    "work_request_id": brief_text(payload.get("run_id") or trace_payload.get("run_id")),
                }
            )
        trace_entries = [trace_by_key[key] for key in reversed(trace_order)]
    except Exception:
        agent_runs = []
        runtime_events = []
        trace_entries = []
    finally:
        if conn is not None:
            conn.close()

    def file_card(rel: str) -> dict[str, Any] | None:
        try:
            path = store._business_root(slug) / rel
            if not path.is_file():
                return None
            stat = path.stat()
            return {"path": rel, "updated_at": int(stat.st_mtime * 1000)}
        except Exception:
            return None

    def latest_under(rel_root: str, suffixes: set[str] | None = None) -> dict[str, Any] | None:
        try:
            root = store._business_root(slug) / rel_root
            if not root.is_dir():
                return None
            matches = [
                path for path in root.rglob("*")
                if path.is_file() and (suffixes is None or path.suffix.lower() in suffixes)
            ]
            if not matches:
                return None
            latest = max(matches, key=lambda path: path.stat().st_mtime)
            return {
                "path": str(latest.relative_to(store._business_root(slug))),
                "updated_at": int(latest.stat().st_mtime * 1000),
                "count": len(matches),
            }
        except Exception:
            return None

    def all_under(rel_root: str, suffixes: set[str] | None = None) -> list[dict[str, Any]]:
        try:
            root = store._business_root(slug) / rel_root
            if not root.is_dir():
                return []
            matches = [
                path for path in root.rglob("*")
                if path.is_file() and (suffixes is None or path.suffix.lower() in suffixes)
            ]
            matches.sort(key=lambda path: path.stat().st_mtime, reverse=True)
            return [
                {
                    "path": str(path.relative_to(store._business_root(slug))),
                    "updated_at": int(path.stat().st_mtime * 1000),
                }
                for path in matches
            ]
        except Exception:
            return []

    def business_file_index(rel_roots: list[str], *, limit: int = 80) -> list[dict[str, Any]]:
        try:
            business_root = store._business_root(slug)
        except Exception:
            return []
        by_path: dict[str, dict[str, Any]] = {}
        for rel_root in rel_roots:
            try:
                root = business_root / rel_root
                if not root.is_dir():
                    continue
                for path in root.rglob("*"):
                    if not path.is_file() or path.name.startswith("."):
                        continue
                    rel = str(path.relative_to(business_root))
                    if rel in {"research/index.md", "metrics/summary.md", "metrics/wake-history.md"}:
                        continue
                    stat = path.stat()
                    by_path[rel] = {
                        "path": rel,
                        "updated_at": int(stat.st_mtime * 1000),
                        "size": int(stat.st_size),
                        "source": rel_root.strip("/"),
                    }
            except Exception:
                continue
        indexed = list(by_path.values())
        indexed.sort(key=lambda item: int(item.get("updated_at") or 0), reverse=True)
        return indexed[: max(1, min(int(limit or 80), 200))]

    def read_json_object(rel_path: str) -> dict[str, Any]:
        try:
            path = store._business_root(slug) / rel_path
            if not path.is_file():
                return {}
            data = json.loads(path.read_text(encoding="utf-8"))
            return as_dict(data)
        except Exception:
            return {}

    def read_last_jsonl_object(rel_path: str) -> tuple[dict[str, Any], str]:
        try:
            path = store._business_root(slug) / rel_path
            if not path.is_file():
                return {}, ""
            for raw_line in reversed(path.read_text(encoding="utf-8", errors="replace").splitlines()):
                line = raw_line.strip()
                if not line:
                    continue
                data = json.loads(line)
                if isinstance(data, dict):
                    return as_dict(data), rel_path
            return {}, rel_path
        except Exception:
            return {}, ""

    def latest_channel_job(*needles: str) -> dict[str, Any] | None:
        wanted = [str(needle or "").strip().lower() for needle in needles if str(needle or "").strip()]
        if not wanted:
            return None
        for job in jobs:
            job_dict = as_dict(job)
            payload = as_dict(job_dict.get("payload"))
            kind = brief_text(job_dict.get("kind")).lower()
            payload_channel = brief_text(payload.get("channel")).lower()
            requested_skill = brief_text(payload.get("requested_skill")).lower()
            if payload_channel in wanted or any(token in kind or token in requested_skill for token in wanted):
                return {
                    "id": brief_text(job_dict.get("id")),
                    "kind": brief_text(job_dict.get("kind")),
                    "status": brief_text(job_dict.get("status") or "queued"),
                    "label": brief_text(job_dict.get("label") or payload.get("summary") or human_kind(job_dict.get("kind"))),
                    "detail": brief_text(job_dict.get("detail") or payload.get("summary")),
                    "updated_at": brief_text(job_dict.get("updated_at") or job_dict.get("created_at")),
                    "created_at": brief_text(job_dict.get("created_at")),
                    "requested_credits": payload.get("requested_credits"),
                    "credits_charged": payload.get("credits_charged"),
                    "reserved_credits": payload.get("reserved_credits"),
                }
        return None

    def sum_credit_field(items: list[dict[str, Any]], field: str) -> int | None:
        total = 0
        seen = False
        for item in items:
            value = item.get(field)
            if value in (None, ""):
                continue
            try:
                total += int(value)
                seen = True
            except Exception:
                continue
        return total if seen else None

    def collect_paid_campaigns(
        publication_root: str,
        metrics_root: str,
        *,
        plan_secondary_key: str,
    ) -> list[dict[str, Any]]:
        try:
            business_root = store._business_root(slug)
        except Exception:
            return []
        root = business_root / publication_root
        if not root.is_dir():
            return []
        campaigns: list[dict[str, Any]] = []
        for receipt_abs in root.glob("*/receipt.json"):
            receipt_rel = str(receipt_abs.relative_to(business_root))
            receipt = read_json_object(receipt_rel)
            if not receipt:
                continue
            slug_name = receipt_abs.parent.name
            plan_rel = f"{publication_root}/{slug_name}/plan.json"
            plan_abs = business_root / plan_rel
            plan = read_json_object(plan_rel)
            campaign_block = as_dict(plan.get("campaign"))
            secondary_block = as_dict(plan.get(plan_secondary_key))
            ad_block = as_dict(plan.get("ad"))
            metrics_rel = f"{metrics_root}/{slug_name}/insights.jsonl"
            latest_metrics, metrics_path = read_last_jsonl_object(metrics_rel)
            asset_path = brief_text(
                receipt.get("ad_video_path")
                or receipt.get("ad_image_path")
                or plan.get("ad_video_path")
                or plan.get("ad_image_path")
            )
            campaigns.append(
                {
                    "slug": slug_name,
                    "status": brief_text(receipt.get("status")),
                    "launch_mode": brief_text(receipt.get("launch_mode") or plan.get("launch_mode")),
                    "asset_kind": brief_text(receipt.get("asset_kind") or plan.get("asset_kind")),
                    "asset_path": asset_path,
                    "plan_path": plan_rel if plan_abs.is_file() else "",
                    "receipt_path": receipt_rel,
                    "metrics_path": metrics_path,
                    "created_at": brief_text(receipt.get("created_at")),
                    "updated_at": brief_text(
                        receipt.get("updated_at")
                        or receipt.get("externally_launched_at")
                        or receipt.get("created_at")
                    ),
                    "objective": brief_text(receipt.get("objective") or campaign_block.get("objective")),
                    "campaign_name": brief_text(receipt.get("campaign_name") or campaign_block.get("name")),
                    "secondary_name": brief_text(
                        receipt.get("adset_name")
                        or receipt.get("ad_group_name")
                        or secondary_block.get("name")
                    ),
                    "ad_name": brief_text(receipt.get("ad_name") or ad_block.get("name")),
                    "daily_budget_usd": receipt.get("daily_budget_usd") or secondary_block.get("daily_budget_usd"),
                    "actual_daily_budget_usd": receipt.get("actual_daily_budget_usd"),
                    "message": brief_text(receipt.get("message") or ad_block.get("message")),
                    "link": brief_text(receipt.get("link") or ad_block.get("link")),
                    "tracked_link": brief_text(receipt.get("tracked_link") or ad_block.get("tracked_link")),
                    "call_to_action": brief_text(receipt.get("call_to_action") or ad_block.get("call_to_action")),
                    "ids": as_dict(receipt.get("ids")),
                    "requested_credits": receipt.get("requested_credits"),
                    "credits_charged": receipt.get("credits_charged"),
                    "balance_credits": receipt.get("balance_credits"),
                    "reserved_credits": receipt.get("reserved_credits"),
                    "latest_metrics": latest_metrics,
                    "open_url": openable_url(
                        brief_text(receipt.get("preview_url") or receipt.get("post_url") or receipt.get("link"))
                    ),
                }
            )
        campaigns.sort(
            key=lambda item: brief_text(item.get("updated_at") or item.get("created_at")),
            reverse=True,
        )
        return campaigns

    source_root = source_path.strip().strip("/")
    website_candidates = [
        f"{source_root}/index.html" if source_root else "",
        "product/site/index.html",
    ]
    website = next((file_card(rel) for rel in website_candidates if rel), None)
    deploy_pending = any(
        "deploy" in str(job.get("kind") or "").lower()
        and str(job.get("status") or "").lower() not in {"done", "complete", "completed", "success"}
        for job in jobs
    )
    outreach_published = [
        *all_under("distribution/local-published", {".md", ".txt"}),
        *all_under("outreach/local-published", {".md", ".txt"}),
    ]
    outreach_receipts = all_under("receipts/outreach", {".json"})
    outreach_latest = outreach_published[0] if outreach_published else None
    outreach_receipt = outreach_receipts[0] if outreach_receipts else None
    outreach_draft = file_card("distribution/outreach-drafts.md") or latest_under("outreach", {".md", ".txt"})
    receipt_by_stem = {
        Path(str(receipt.get("path") or "")).stem.lower(): receipt
        for receipt in outreach_receipts
        if receipt.get("path")
    }
    outreach_items = []
    for item in outreach_published:
        item_path = str(item.get("path") or "")
        item_stem = Path(item_path).stem.lower()
        item_hash_match = re.search(r"-([0-9a-f]{6,64})$", item_stem)
        item_hash = item_hash_match.group(1) if item_hash_match else ""
        matched_receipt = None
        if item_hash:
            matched_receipt = next(
                (receipt for stem, receipt in receipt_by_stem.items() if stem.startswith(item_hash)),
                None,
            )
        outreach_items.append({
            "path": item_path,
            "updated_at": item.get("updated_at"),
            "receipt": (matched_receipt or {}).get("path", ""),
            "status": "published_local",
        })
    if not outreach_items and outreach_draft:
        outreach_items.append({
            "path": outreach_draft.get("path", ""),
            "updated_at": outreach_draft.get("updated_at"),
            "receipt": "",
            "status": "draft_only",
        })
    x_items = [
        {
            "id": brief_text(post.get("id")),
            "title": brief_text(post.get("title")),
            "source": brief_text(post.get("source")),
            "status": brief_text(post.get("status")),
            "mode": brief_text(post.get("mode")),
            "url": brief_text(post.get("url")),
            "artifact_path": brief_text(post.get("artifact_path")),
            "conversation_file": brief_text(post.get("conversation_file")),
            "created_at": brief_text(post.get("created_at")),
            "updated_at": brief_text(post.get("updated_at")),
            "unresolved_messages": as_int(post.get("unresolved_messages")),
        }
        for post in posts
        if (
            (brief_text(post.get("source")).lower().replace("test-", "", 1) == "x")
            or brief_text(post.get("source")).lower().startswith("x-")
            or "twitter" in brief_text(post.get("source")).lower()
        )
    ]
    x_job = latest_channel_job("x", "twitter")
    meta_campaigns = collect_paid_campaigns(
        "distribution/meta-ads",
        "metrics/meta-ads",
        plan_secondary_key="adset",
    )
    reddit_campaigns = collect_paid_campaigns(
        "distribution/reddit-ads",
        "metrics/reddit-ads",
        plan_secondary_key="ad_group",
    )
    meta_job = latest_channel_job("meta")
    reddit_job = latest_channel_job("reddit")
    outreach_channels = {
        "x": {
            "channel": "x",
            "label": "X",
            "status": (
                "published_local"
                if any(item.get("mode") == "test" for item in x_items)
                else "published"
                if x_items
                else "draft_only"
                if outreach_draft
                else brief_text((x_job or {}).get("status")) or "missing"
            ),
            "updated_at": brief_text(
                (x_items[0] if x_items else {}).get("updated_at")
                or (outreach_draft or {}).get("updated_at")
                or (x_job or {}).get("updated_at")
            ),
            "draft_path": brief_text((outreach_draft or {}).get("path")),
            "items": x_items[:8],
            "latest_job": x_job,
            "published_count": len(x_items),
        },
        "reddit": {
            "channel": "reddit",
            "label": "Reddit",
            "status": brief_text((reddit_campaigns[0] if reddit_campaigns else {}).get("status") or (reddit_job or {}).get("status") or "missing"),
            "updated_at": brief_text(
                (reddit_campaigns[0] if reddit_campaigns else {}).get("updated_at")
                or (reddit_job or {}).get("updated_at")
            ),
            "campaigns": reddit_campaigns[:8],
            "latest_job": reddit_job,
            "campaign_count": len(reddit_campaigns),
            "metrics_count": sum(1 for item in reddit_campaigns if as_dict(item.get("latest_metrics"))),
            "allocated_credits": sum_credit_field(reddit_campaigns, "requested_credits") or sum_credit_field(reddit_campaigns, "credits_charged") or 0,
            "requested_credits": sum_credit_field(reddit_campaigns, "requested_credits"),
            "credits_charged": sum_credit_field(reddit_campaigns, "credits_charged"),
            "reserved_credits": sum_credit_field(reddit_campaigns, "reserved_credits"),
            "balance_credits": (reddit_campaigns[0] if reddit_campaigns else {}).get("balance_credits"),
        },
        "meta": {
            "channel": "meta",
            "label": "Meta",
            "status": brief_text((meta_campaigns[0] if meta_campaigns else {}).get("status") or (meta_job or {}).get("status") or "missing"),
            "updated_at": brief_text(
                (meta_campaigns[0] if meta_campaigns else {}).get("updated_at")
                or (meta_job or {}).get("updated_at")
            ),
            "campaigns": meta_campaigns[:8],
            "latest_job": meta_job,
            "campaign_count": len(meta_campaigns),
            "metrics_count": sum(1 for item in meta_campaigns if as_dict(item.get("latest_metrics"))),
            "allocated_credits": sum_credit_field(meta_campaigns, "requested_credits") or sum_credit_field(meta_campaigns, "credits_charged") or 0,
            "requested_credits": sum_credit_field(meta_campaigns, "requested_credits"),
            "credits_charged": sum_credit_field(meta_campaigns, "credits_charged"),
            "reserved_credits": sum_credit_field(meta_campaigns, "reserved_credits"),
            "balance_credits": (meta_campaigns[0] if meta_campaigns else {}).get("balance_credits"),
        },
    }
    creative_latest = (
        latest_under("distribution", _TAKYON_MEDIA_SUFFIXES)
        or latest_under("campaigns", _TAKYON_MEDIA_SUFFIXES)
        or latest_under("creatives", _TAKYON_MEDIA_SUFFIXES)
    )
    creative_receipt = latest_under("receipts/creative-assets", {".json"})
    research_outputs = business_file_index(["research", "brain"], limit=80)
    research_latest = research_outputs[0] if research_outputs else None

    now_ts = time.time()
    active_cron = [job for job in cron_jobs if job.get("enabled", True)]
    overdue_cron = [
        job for job in active_cron
        if parse_ts(job.get("next_run")) is not None and (parse_ts(job.get("next_run")) or now_ts) < now_ts - 60
    ]
    never_ran_cron = [job for job in active_cron if not job.get("last_run")]
    if active_cron and overdue_cron:
        wake_health = {
            "status": "needs_attention",
            "headline": "CEO wakeups are scheduled but appear overdue.",
            "detail": "Run /cron tick or start the Takyon gateway so scheduled CEO checks can fire.",
        }
    elif active_cron and never_ran_cron:
        wake_health = {
            "status": "watching",
            "headline": "CEO wakeups are scheduled and waiting for their first run.",
            "detail": "The next wake should review evidence, blockers, replies, and the next highest-impact move.",
        }
    elif active_cron:
        wake_health = {
            "status": "watching",
            "headline": "CEO wake loop is active.",
            "detail": "Scheduled wakes keep checking customer signal, blocked work, and the next move.",
        }
    else:
        wake_health = {
            "status": "quiet",
            "headline": "No CEO wake loop is visible.",
            "detail": "Use /wake for one run or /create --schedule when creating the next business.",
        }

    task_cards: list[dict[str, Any]] = []
    # The CEO's curated milestones (business_post_operator_update) are the PRIMARY
    # milestone rows when present: a few intent cards (title + description +
    # category pill + status) instead of flat low-level worker labels. Raw
    # worker/runtime events nest under the running milestone via the
    # current_task_id grouping in _takyon_live_state_payload.
    operator_milestone_cards: list[dict[str, Any]] = []
    for index, milestone in enumerate(operator_update_milestones[:8]):
        m_dict = as_dict(milestone)
        m_title = brief_text(m_dict.get("title"))
        if not m_title:
            continue
        operator_milestone_cards.append(
            {
                "id": f"milestone:{index}",
                "source": "operator_update",
                "label": m_title,
                "title": m_title,
                "description": brief_text(m_dict.get("description")),
                "category": brief_text(m_dict.get("category")),
                "status": brief_text(m_dict.get("status")) or "running",
                "detail": brief_text(m_dict.get("description")) or m_title,
                "tone": status_tone(m_dict.get("status")),
                "updated_at": brief_text(operator_update.get("updated_at")),
            }
        )
    task_cards.extend(operator_milestone_cards)
    # Work-request/job cards are PRIMARY milestone rows too. Build the set of
    # known job ids first so a raw runtime/agent card carrying a work_request_id
    # can be re-parented onto its milestone card (id format "job:<id>") and the
    # existing nesting in _takyon_live_state_payload groups the raw tool calls
    # underneath. If the work_request_id is not resolvable, leave the raw card
    # as a standalone row (current behavior).
    known_job_ids = {brief_text(job.get("id")) for job in jobs[:8] if brief_text(job.get("id"))}

    def _milestone_parent_id(work_request_id: str) -> str:
        wr = brief_text(work_request_id)
        return f"job:{wr}" if wr and wr in known_job_ids else ""

    for event in runtime_events[:6]:
        event_card = {
            "id": f"runtime:{event.get('id')}",
            "source": "runtime",
            "label": event.get("label") or "CEO live trace",
            "status": event.get("status") or "recorded",
            "detail": event.get("detail") or "",
            "tone": event.get("tone") or status_tone(event.get("status")),
            "updated_at": event.get("updated_at") or "",
        }
        parent_id = _milestone_parent_id(event.get("work_request_id"))
        if parent_id:
            event_card["task_id"] = parent_id
        task_cards.append(event_card)
    for job in jobs[:8]:
        # Prefer the CEO-authored milestone intent (title/description/category)
        # over the static job_label/job_detail; fall back to the old values when
        # absent. The explicit title/description/category are passed through so
        # the canonical task surfaces them verbatim as a milestone card.
        job_card = {
            "id": f"job:{job.get('id')}",
            "source": "job",
            "label": job.get("title") or job.get("label") or human_kind(job.get("kind")),
            "status": job.get("status") or "recorded",
            "detail": job.get("description") or job.get("detail") or "",
            "tone": job.get("tone") or status_tone(job.get("status")),
            "updated_at": job.get("updated_at") or job.get("created_at") or "",
            "category": job.get("category") or "",
        }
        if job.get("title"):
            job_card["title"] = job.get("title")
        if job.get("description"):
            job_card["description"] = job.get("description")
        task_cards.append(job_card)
    for run in agent_runs[:4]:
        run_card = {
            "id": f"agent:{run.get('id')}",
            "source": "agent",
            "label": run.get("label") or "CEO run",
            "status": run.get("status") or "recorded",
            "detail": run.get("detail") or "Run recorded in audit trail.",
            "tone": run.get("tone") or status_tone(run.get("status")),
            "updated_at": run.get("updated_at") or "",
        }
        parent_id = _milestone_parent_id(run.get("work_request_id"))
        if parent_id:
            run_card["task_id"] = parent_id
        task_cards.append(run_card)
    for job in cron_jobs[:4]:
        label = "CEO wake loop" if re.search(r"takyon-ceo|ceo", brief_text(job.get("name")), re.I) else brief_text(job.get("name") or "Scheduled work")
        task_cards.append({
            "id": f"cron:{job.get('id')}",
            "source": "cron",
            "label": label,
            "status": "overdue" if job in overdue_cron else brief_text(job.get("state") or "scheduled"),
            "detail": wake_health["detail"] if job in overdue_cron else (
                f"Next wake {job.get('next_run')}" if job.get("next_run") else "Scheduled CEO check."
            ),
            "tone": "blocked" if job in overdue_cron else "waiting",
            "updated_at": job.get("last_run") or job.get("next_run") or "",
        })

    # Side-channel milestone rollup. The Tasks-panel PRIMARY rows are produced by
    # a SEPARATE auxiliary LLM call (tui_gateway.task_rollup) over the raw trace
    # assembled above — NOT by a CEO tool call, so it never enters the CEO's
    # conversation context or breaks prompt caching ("like a message delta").
    # It is cached by a hash of the trace with a short TTL (only re-summarizes on
    # change) and fails open (returns [] → deterministic labels below stand).
    # The CEO's own curated operator_update milestones, when present, are an
    # explicit override and take precedence over the auto rollup.
    if not operator_milestone_cards:
        try:
            from tui_gateway.task_rollup import summarize_task_milestones

            rollup_cards = summarize_task_milestones(slug, task_cards)
        except Exception:
            rollup_cards = []
        if rollup_cards:
            for card in rollup_cards:
                card["tone"] = card.get("tone") or status_tone(card.get("status"))
            # Prepend so the milestone rows are the primary intent anchors; the
            # raw job/runtime/agent rows nest under them in
            # _takyon_live_state_payload.
            task_cards = [*rollup_cards, *task_cards]

    blocked_count = sum(1 for task in task_cards if task.get("tone") == "blocked")
    product_visible = bool(website)
    research_visible = bool(research_outputs)
    operator_update_headline = brief_text(operator_update.get("headline"))
    operator_update_summary = brief_text(operator_update.get("summary"))
    if operator_update_headline and not blocked_count and wake_health["status"] != "needs_attention":
        # The CEO's curated update is the authoritative customer-facing CEO-loop
        # copy when there is no harder blocker/wake signal to surface first.
        ceo_loop = {
            "status": "working",
            "headline": operator_update_headline,
            "detail": operator_update_summary or operator_update_headline,
            "next_action": operator_update_summary or operator_update_headline,
        }
    elif wake_health["status"] == "needs_attention":
        ceo_loop = {
            "status": "needs_attention",
            "headline": wake_health["headline"],
            "detail": wake_health["detail"],
            "next_action": "Run /cron tick or wake the CEO now.",
        }
    elif blocked_count:
        ceo_loop = {
            "status": "recovering",
            "headline": f"{blocked_count} blocker{'s' if blocked_count != 1 else ''} need CEO recovery.",
            "detail": "Blocked work is preserved as tasks instead of disappearing into logs.",
            "next_action": "Open Tasks, resolve a gate, or wake the CEO to choose a recovery move.",
        }
    elif not research_visible:
        ceo_loop = {
            "status": "research_first",
            "headline": "Research is the next visible company-building move.",
            "detail": "No research files are visible in the business workspace yet.",
            "next_action": "Create durable research notes, then decide the next business move from evidence.",
        }
    elif product_visible:
        ceo_loop = {
            "status": "working",
            "headline": "Product preview is available.",
            "detail": "A customer-facing surface exists; keep checking it against the visible research files.",
            "next_action": "Open the preview or continue research.",
        }
    else:
        ceo_loop = {
            "status": "working",
            "headline": "The company has strategy context and is ready for the next move.",
            "detail": "Use research, outreach, creative, or product work based on evidence.",
            "next_action": "Wake the CEO or pick a focused task.",
        }

    status_cards = [
        {
            "label": "Current focus",
            "status": ceo_loop["status"],
            "detail": ceo_loop["headline"],
            "tone": status_tone(ceo_loop["status"]),
        },
        {
            "label": "Research",
            "status": "visible" if research_visible else "needed",
            "detail": (research_latest or {}).get("path", "No research files are visible yet."),
            "tone": "done" if research_visible else "waiting",
        },
        {
            "label": "Product",
            "status": "previewable" if product_visible else "not built yet",
            "detail": (website or {}).get("path", "No local product surface is visible yet."),
            "tone": "done" if product_visible else "waiting",
        },
        {
            "label": "Scheduled checks",
            "status": wake_health["status"],
            "detail": wake_health["headline"],
            "tone": status_tone(wake_health["status"]),
        },
    ]

    routes = surface.get("routes") if isinstance(surface.get("routes"), list) else []
    business_budget = as_dict(current_state.get("business_budget"))
    return {
        "goal": brief_text(business.get("goal")),
        "mode": "live"
        if brief_text(business.get("mode") or business.get("status") or business.get("state")).strip().lower() != "live"
        else "live",
        "product": {
            "status": brief_text(surface.get("status") or "missing"),
            "source_path": source_path,
            "runtime_api_base": brief_text(surface.get("runtime_api_base")),
            "publish_target": brief_text(surface.get("publish_target")),
            "publish_policy": brief_text(surface.get("publish_policy")),
            "publish_status": brief_text(surface.get("publish_status")),
            "public_url": brief_text(surface.get("public_url")),
            "published_at": brief_text(surface.get("published_at")),
            "publish_receipt_path": brief_text(surface.get("publish_receipt_path")),
            "publish_blocker": brief_text(surface.get("publish_blocker")),
            "routes_count": len(routes),
            "surface_status": brief_text(surface.get("status") or product_surface_evidence.get("surface_status") or "missing"),
            "surface_receipt": brief_text(surface.get("publish_receipt_path") or product_surface_evidence.get("latest_receipt_path")),
            "inventory_status": brief_text(product_inventory.get("status")),
            "risk_marker_count": len(as_list(product_inventory.get("risk_markers"))),
            "claim_snippet_count": len(as_list(product_inventory.get("claim_snippets"))),
            "pretend_finding_count": len(as_list(product_inventory.get("pretend_findings"))),
            "local_continuable_work": as_list(product_surface_evidence.get("local_continuable_work"))[:8],
            "filesystem_index": brief_text(app.get("filesystem_index") or "product/surface.md"),
            "notes": brief_text(surface.get("notes")),
        },
        "metrics": {
            "users": as_int(pulse_summary.get("users")),
            "paid_customers": as_int(pulse_summary.get("paid_customers")),
            "mrr_cents": as_int(pulse_summary.get("mrr_cents")),
            "revenue_cents": as_int(pulse_summary.get("revenue_cents") or app_revenue.get("amount_paid_cents")),
            "checkout_intents": as_int(pulse_summary.get("checkout_intents")),
            "usage_events": as_int(pulse_summary.get("usage_events") or app_usage.get("events")),
            "unresolved_inbound": as_int(pulse_summary.get("unresolved_inbound") or conversations.get("unresolved_messages")),
            "queued_jobs": as_int(pulse_summary.get("queued_jobs")),
        },
        "budget": {
            "business_amount": business_budget.get("amount"),
            "business_status": brief_text(business_budget.get("status")),
            "app_status": brief_text(app_budget.get("status")),
            "app_limit_microusd": as_int(app_budget.get("hard_limit_microusd")),
            "app_spent_microusd": as_int(app_budget.get("spent_microusd")),
            "app_remaining_microusd": as_int(app_budget.get("remaining_microusd")),
        },
        "cron": cron_jobs,
        "files": files,
        "jobs": jobs,
        "agent_runs": agent_runs,
        "workers": workers[:8],
        "registry": _takyon_registry_display_payload(),
        "trace": trace_entries[:32],
        "tasks": task_cards[:16],
        "status_cards": status_cards,
        "ceo_loop": ceo_loop,
        "wake_health": wake_health,
        "research": {
            "status": "visible" if research_visible else "needed",
            "latest_path": (research_latest or {}).get("path", ""),
            "count": len(research_outputs),
            "outputs": research_outputs[:24],
        },
        "research_outputs": research_outputs,
        "posts": posts[:12],
        "artifacts": {
            "website": {
                "status": (
                    "published"
                    if brief_text(surface.get("publish_status")) == "published" and brief_text(surface.get("public_url"))
                    else "publish_blocked"
                    if brief_text(surface.get("publish_status")) == "blocked"
                    else "local_source" if website else "missing"
                ),
                "path": (website or {}).get("path", ""),
                "updated_at": (website or {}).get("updated_at"),
                "deploy_status": "pending" if deploy_pending else "",
                "source_path": source_path,
                "public_url": brief_text(surface.get("public_url")),
                "publish_target": brief_text(surface.get("publish_target")),
                "publish_policy": brief_text(surface.get("publish_policy")),
                "publish_status": brief_text(surface.get("publish_status")),
                "publish_blocker": brief_text(surface.get("publish_blocker")),
                "publish_receipt_path": brief_text(surface.get("publish_receipt_path")),
            },
            "outreach": {
                "status": "published_local" if outreach_latest else ("draft_only" if outreach_draft else "missing"),
                "path": (outreach_latest or outreach_draft or {}).get("path", ""),
                "receipt": (outreach_receipt or {}).get("path", ""),
                "updated_at": (outreach_latest or outreach_draft or {}).get("updated_at"),
                "published_count": len(outreach_published),
                "items": outreach_items,
                "receipts": [str(receipt.get("path") or "") for receipt in outreach_receipts if receipt.get("path")],
                "channels": outreach_channels,
            },
            "creative_assets": {
                "status": "generated" if creative_latest else "missing",
                "path": (creative_latest or {}).get("path", ""),
                "receipt": (creative_receipt or {}).get("path", ""),
                "updated_at": (creative_latest or {}).get("updated_at"),
                "count": (creative_latest or {}).get("count", 0),
            },
        },
        "conversations": {
            "active_threads": as_int(conversations.get("active_threads")),
            "unresolved_messages": as_int(conversations.get("unresolved_messages")),
            "latest_message_at": brief_text(conversations.get("latest_message_at")),
        },
        "generated_at": brief_text(pulse.get("generated_at")),
        "pulse_warning": brief_text(pulse.get("warning")),
    }


def _takyon_output_detail(path: str) -> tuple[str, str]:
    suffix = Path(path).suffix.lower()
    if suffix in _TAKYON_VIDEO_SUFFIXES:
        return "video", "Generated video asset"
    if suffix in _TAKYON_IMAGE_SUFFIXES:
        return "image", "Generated image asset"
    parts = path.split("/")
    top = parts[0] if parts else ""
    if path == "product/site/index.html":
        return "file", "Website surface (local source)"
    if path.startswith("product/site/"):
        return "file", "Website source asset"
    if path.startswith("metrics/receipts/outreach/"):
        return "receipt", "Outreach publish receipt"
    if path.startswith("metrics/receipts/creative-assets/"):
        return "receipt", "Creative asset receipt"
    if path.startswith("distribution/outreach-drafts"):
        return "file", "Outreach draft only"
    if "ugc" in path.lower() and suffix in {".md", ".txt"}:
        return "file", "Creative brief draft only"
    if path.startswith("metrics/receipts/") or top == "receipts":
        return "receipt", "Business receipt"
    if top in {"reports", "outputs"}:
        return "report", "Historical output"
    if path.startswith(("distribution/local-published/", "outreach/local-published/")):
        return "file", "Local published outreach"
    if top == "app":
        return "file", "App runtime artifact"
    if top == "brain":
        return "file", "Business brain artifact"
    if top == "product":
        return "file", "Product artifact"
    if top == "distribution":
        return "file", "Distribution artifact"
    return "file", "Business artifact"


_TAKYON_VIDEO_SUFFIXES = {".mp4", ".mov", ".webm", ".m4v"}
_TAKYON_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
_TAKYON_MEDIA_SUFFIXES = _TAKYON_VIDEO_SUFFIXES | _TAKYON_IMAGE_SUFFIXES
_TAKYON_TEXT_OUTPUT_SUFFIXES = {
    ".md",
    ".txt",
    ".json",
    ".js",
    ".css",
    ".html",
    ".ts",
    ".tsx",
    ".jsx",
    ".yml",
    ".yaml",
}
_TAKYON_HIDDEN_OUTPUT_SUFFIXES = {
    ".js",
    ".jsx",
    ".ts",
    ".tsx",
}
_TAKYON_MAX_MEDIA_BYTES = 64 * 1024 * 1024
_TAKYON_MAX_FILE_READ_BYTES = 512 * 1024
_TAKYON_MAX_SITE_PREVIEW_BYTES = 8 * 1024 * 1024
_TAKYON_INLINE_OUTPUT_PREVIEW_BYTES = 24 * 1024
_TAKYON_INLINE_OUTPUT_PREVIEW_LIMIT = 8


# Internal toolchain / config files the CEO writes incidentally while building.
# They are NOT operator-facing deliverables and must be hidden from the
# Documents / Deliverables panels (card "Dont show all raw documents"). The
# files remain reachable through an explicit file read; they are only excluded
# from the promoted deliverable lists.
_TAKYON_HIDDEN_OUTPUT_BASENAMES = {
    "skill.md",
    "config.yaml",
    "config.yml",
    "package.json",
    "package-lock.json",
    "yarn.lock",
    "pnpm-lock.yaml",
    "uv.lock",
    "poetry.lock",
    "requirements.txt",
    "pyproject.toml",
    "tsconfig.json",
    "vite.config.ts",
    "vite.config.js",
    ".gitignore",
    ".env",
    "agents.md",
    "claude.md",
}
# Any path segment matching one of these is an internal toolchain dir.
_TAKYON_HIDDEN_OUTPUT_DIR_SEGMENTS = {
    "node_modules",
    ".next",
    ".cache",
    "__pycache__",
    ".git",
    "dist",
    "build",
    ".turbo",
    ".vite",
}


def _takyon_hide_operator_output(path: Any) -> bool:
    rel = str(path or "").strip()
    if not rel:
        return False
    p = Path(rel)
    # Raw source modules remain available through explicit file reads, but they
    # should not be promoted as operator-facing deliverables/documents.
    if p.suffix.lower() in _TAKYON_HIDDEN_OUTPUT_SUFFIXES:
        return True
    if p.name.lower() in _TAKYON_HIDDEN_OUTPUT_BASENAMES:
        return True
    # Lock files (*.lock) are internal toolchain noise.
    if p.suffix.lower() == ".lock":
        return True
    if any(seg.lower() in _TAKYON_HIDDEN_OUTPUT_DIR_SEGMENTS for seg in p.parts):
        return True
    return False


def _takyon_site_asset_data_url(index_path: Path, raw_url: str, *, site_root: Path | None = None) -> str | None:
    url = str(raw_url or "").strip()
    if (
        not url
        or url.startswith("#")
        or url.startswith("data:")
        or re.match(r"^[a-z][a-z0-9+.-]*:", url, re.I)
    ):
        return None
    clean = url.split("#", 1)[0].split("?", 1)[0]
    if not clean:
        return None
    root = (site_root or index_path.parent).resolve()
    base = root if url.startswith("/") and site_root else index_path.parent.resolve()
    candidate = (base / clean.lstrip("/")).resolve()
    if not candidate.is_file() or root not in (candidate, *candidate.parents):
        return None
    try:
        if candidate.stat().st_size > _TAKYON_MAX_SITE_PREVIEW_BYTES:
            return None
        mime = mimetypes.guess_type(str(candidate))[0] or "application/octet-stream"
        encoded = base64.b64encode(candidate.read_bytes()).decode("ascii")
        suffix = ""
        if "#" in url:
            suffix = "#" + url.split("#", 1)[1]
        return f"data:{mime};base64,{encoded}{suffix}"
    except Exception:
        return None


def _takyon_inline_static_site(index_path: Path, *, site_root: Path | None = None) -> str:
    html_text = index_path.read_text(encoding="utf-8", errors="replace")

    def replace_attr(match: re.Match[str]) -> str:
        prefix = match.group("prefix")
        url = match.group("url")
        suffix = match.group("suffix")
        data_url = _takyon_site_asset_data_url(index_path, url, site_root=site_root)
        if not data_url:
            return match.group(0)
        return f"{prefix}{data_url}{suffix}"

    return re.sub(
        r"(?P<prefix>\b(?:src|href)\s*=\s*[\"'])(?P<url>[^\"']+)(?P<suffix>[\"'])",
        replace_attr,
        html_text,
        flags=re.I,
    )


def _takyon_historical_outputs_payload(store: Any, slug: str, *, limit: int = 40) -> list[dict[str, Any]]:
    try:
        root = store._business_root(slug)
    except Exception:
        return []
    if not root.exists() or not root.is_dir():
        return []

    candidates: set[Path] = set()
    exact_paths = {
        "product/surface.md",
        "distribution/surface.md",
        "research/index.md",
        "metrics/summary.md",
        "metrics/wake-history.md",
        "product/mvp-spec.md",
        "product/site/index.html",
    }
    for rel in exact_paths:
        path = root / rel
        if path.is_file():
            candidates.add(path)

    recursive_roots = [
        "outputs",
        "reports",
        "research",
        "brain",
        "campaigns",
        "distribution",
        "outreach/local-published",
        "product/site",
    ]
    allowed_suffixes = {*_TAKYON_TEXT_OUTPUT_SUFFIXES, *_TAKYON_MEDIA_SUFFIXES}
    for rel_root in recursive_roots:
        directory = root / rel_root
        if not directory.exists() or not directory.is_dir():
            continue
        for path in directory.rglob("*"):
            if not path.is_file():
                continue
            if path.suffix.lower() not in allowed_suffixes:
                continue
            try:
                rel = str(path.relative_to(root))
            except Exception:
                continue
            if _takyon_hide_operator_output(rel):
                continue
            candidates.add(path)

    outputs: list[dict[str, Any]] = []
    for path in candidates:
        try:
            stat = path.stat()
            rel = str(path.relative_to(root))
        except Exception:
            continue
        kind, detail = _takyon_output_detail(rel)
        outputs.append(
            {
                "id": f"historical:{slug}:{rel}",
                "title": path.name,
                "detail": detail,
                "path": rel,
                "kind": kind,
                "at": int(stat.st_mtime * 1000),
            }
        )

    outputs.sort(key=lambda item: int(item.get("at") or 0), reverse=True)
    preview_budget = _TAKYON_INLINE_OUTPUT_PREVIEW_LIMIT
    for item in outputs:
        if preview_budget <= 0:
            break
        rel = str(item.get("path") or "").strip()
        if not rel:
            continue
        if Path(rel).suffix.lower() not in _TAKYON_TEXT_OUTPUT_SUFFIXES:
            continue
        path = root / rel
        try:
            size = path.stat().st_size
            with path.open("rb") as fh:
                raw = fh.read(min(size, _TAKYON_INLINE_OUTPUT_PREVIEW_BYTES))
        except Exception:
            continue
        item["preview_content"] = raw.decode("utf-8", errors="replace")
        item["preview_truncated"] = size > _TAKYON_INLINE_OUTPUT_PREVIEW_BYTES
        item["preview_size"] = size
        preview_budget -= 1
    return outputs[: max(1, min(int(limit or 40), 100))]


def _takyon_preview_path(path: Any) -> str:
    text = str(path or "").strip().strip("/")
    if not text:
        return "product/site"
    if text == "product/site/index.html":
        return "product/site"
    if text.startswith("product/site/"):
        return "product/site"
    return text


def _takyon_workspace_deliverables_payload(
    overview: dict[str, Any] | None,
    outputs: list[dict[str, Any]] | None,
    *,
    limit: int = 60,
) -> list[dict[str, Any]]:
    def as_dict(value: Any) -> dict[str, Any]:
        return value if isinstance(value, dict) else {}

    def as_list(value: Any) -> list[Any]:
        return value if isinstance(value, list) else []

    def as_text(value: Any) -> str:
        return str(value or "").strip()

    def read_at(value: Any) -> int:
        try:
            return int(value or 0)
        except Exception:
            return 0

    def normalize(output: dict[str, Any]) -> dict[str, Any] | None:
        path = as_text(output.get("path"))
        if _takyon_hide_operator_output(path):
            return None
        title = as_text(output.get("title")) or (Path(path).name if path else "Output")
        source = as_text(output.get("source")).lower()
        detail = as_text(output.get("detail"))
        if not detail:
            if source == "research":
                detail = "Research artifact"
            elif source == "brain":
                detail = "Business brain artifact"
            else:
                detail = "Business artifact"
        item = {
            "id": as_text(output.get("id")) or f"deliverable:{path or title}",
            "title": title,
            "detail": detail,
            "path": path,
            "kind": as_text(output.get("kind")) or "file",
            "source": source or "workspace",
            "at": read_at(output.get("at") or output.get("updated_at") or output.get("created_at")),
        }
        for key in ("preview_content", "preview_truncated", "preview_size", "size", "updated_at", "created_at"):
            if key in output and output.get(key) not in (None, ""):
                item[key] = output.get(key)
        return item

    def merge_item(by_key: dict[str, dict[str, Any]], output: dict[str, Any]) -> None:
        item = normalize(output)
        if not item:
            return
        key = as_text(item.get("path")) or as_text(item.get("id"))
        if not key:
            return
        current = by_key.get(key)
        if current is None:
            by_key[key] = item
            return
        current_has_preview = isinstance(current.get("preview_content"), str)
        next_has_preview = isinstance(item.get("preview_content"), str)
        if not current_has_preview and next_has_preview:
            by_key[key] = {**current, **item}
            return
        if read_at(item.get("at")) >= read_at(current.get("at")):
            by_key[key] = {**current, **item}

    merged: dict[str, dict[str, Any]] = {}
    for output in outputs or []:
        if isinstance(output, dict):
            merge_item(merged, output)

    overview_dict = as_dict(overview)
    research = as_dict(overview_dict.get("research"))
    research_outputs = research.get("outputs")
    if not isinstance(research_outputs, list) or not research_outputs:
        research_outputs = overview_dict.get("research_outputs")
    for output in as_list(research_outputs):
        if isinstance(output, dict):
            merge_item(merged, output)

    deliverables = list(merged.values())
    deliverables.sort(key=lambda item: read_at(item.get("at")), reverse=True)
    return deliverables[: max(1, min(int(limit or 60), 120))]


def _takyon_workspace_preview_payload(
    overview: dict[str, Any] | None,
    outputs: list[dict[str, Any]] | None,
) -> dict[str, Any]:
    def as_dict(value: Any) -> dict[str, Any]:
        return value if isinstance(value, dict) else {}

    def as_text(value: Any) -> str:
        return str(value or "").strip()

    overview_dict = as_dict(overview)
    product = as_dict(overview_dict.get("product"))
    artifacts = as_dict(overview_dict.get("artifacts"))
    website = as_dict(artifacts.get("website"))
    public_url = as_text(product.get("public_url") or website.get("public_url"))
    local_paths = [
        as_text(website.get("path")),
        *[
            as_text(item.get("path"))
            for item in (outputs or [])
            if isinstance(item, dict)
        ],
    ]
    local_preview_path = next(
        (
            _takyon_preview_path(path)
            for path in local_paths
            if path.startswith("product/site")
        ),
        "product/site",
    )
    has_local_preview = any(path.startswith("product/site") for path in local_paths)
    preview_status = (
        "published"
        if public_url
        else "ready"
        if has_local_preview
        else as_text(product.get("publish_status") or website.get("publish_status") or product.get("status") or website.get("status"))
        or "missing"
    )
    return {
        "preview_path": local_preview_path,
        "preview_available": bool(public_url or has_local_preview),
        "preview_status": preview_status,
    }


def _takyon_session(params: dict) -> dict | None:
    return _sessions.get(str(params.get("session_id") or ""))


_TAKYON_CREATE_VALUE_FLAGS = {"--schedule", "--name"}


def _takyon_create_business_arg_index(tokens: list[str]) -> int | None:
    index = 1
    while index < len(tokens):
        token = tokens[index]
        if token in _TAKYON_CREATE_VALUE_FLAGS:
            index += 2
            continue
        if any(token.startswith(f"{flag}=") for flag in _TAKYON_CREATE_VALUE_FLAGS):
            index += 1
            continue
        if token.startswith("-"):
            index += 1
            continue
        return index
    return None


def _takyon_unique_business_slug(store: Any, base_slug: str) -> str:
    try:
        data = store.read(scope="global", query="list_businesses", limit=500)
        existing = {
            str(item.get("slug") or "")
            for item in (data.get("businesses") if isinstance(data, dict) else []) or []
            if isinstance(item, dict)
        }
    except Exception:
        existing = set()
    if base_slug not in existing:
        return base_slug
    stem = re.sub(r"-+", "-", base_slug).strip("-") or "business"
    timestamp = time.strftime("%m%d%H%M%S", time.localtime())
    candidate = f"{stem}-{timestamp}"
    if candidate not in existing:
        return candidate
    suffix = 2
    while True:
        candidate = f"{stem}-{timestamp}-{suffix}"
        if candidate not in existing:
            return candidate
        suffix += 1


def _takyon_require_durable_business(
    store: Any,
    slug: str,
    *,
    context: str,
    command_result: Any = None,
) -> dict[str, Any]:
    if isinstance(command_result, dict) and command_result.get("success") is False:
        detail = (
            str(command_result.get("error") or "").strip()
            or str(command_result.get("output") or "").strip()
            or json.dumps(command_result, ensure_ascii=False)
        )
        raise RuntimeError(f"{context} failed for business:{slug}: {detail}")
    try:
        summary = store.read(scope=f"business:{slug}", query="summary", limit=1)
    except Exception as exc:
        raise RuntimeError(
            f"{context} did not persist business:{slug}: {exc}"
        ) from exc
    business = summary.get("business") if isinstance(summary, dict) else {}
    if str((business or {}).get("slug") or "").strip() != slug:
        raise RuntimeError(f"{context} did not persist business:{slug}")
    return summary if isinstance(summary, dict) else {}


def _takyon_detached_shell_target(line: str, current_business: str | None) -> tuple[str, str, str] | None:
    raw = str(line or "").strip().lstrip("/")
    if not raw:
        return None
    try:
        tokens = shlex.split(raw)
    except ValueError:
        return None
    if not tokens:
        return None

    command = tokens[0].lower()
    if command == "wake":
        business = str(current_business or "").strip()
        return ("wake", business, "/" + shlex.join(["wake", business, *tokens[1:]])) if business else None

    if command in {"create", "build", "init"}:
        try:
            from plugins.takyon.cli import _parse_business_start_args

            # Match the interactive shell path, which normalizes create/build/init
            # through the canonical /create parser before running the business start.
            slug, _raw_name, _goal, _mode, _schedule, auto_start, no_auto = _parse_business_start_args(
                ["create", *tokens[1:]],
                    usage='usage: /create [--live] [--no-auto] [--schedule "every 6h"] <business> [goal]',
                auto_default=True,
            )
        except Exception:
            return None
        if auto_start and not no_auto and slug:
            return None

    return None


def _takyon_pending_scope_overview(
    kind: str,
    business: str,
    status: str,
    detail: str = "",
    started_at: float = 0,
) -> dict[str, Any]:
    label = "CEO bootstrap" if kind == "create" else "CEO wake"
    status_text = "running" if status == "running" else status
    visible_detail = detail or "Takyon is running this outside the dashboard process."
    if status_text == "running" and started_at:
        elapsed = max(0, int(time.time() - started_at))
        visible_detail = f"{visible_detail} · running {elapsed}s"
    return {
        "tasks": [
            {
                "id": f"runtime:{kind}:{business}",
                "source": "runtime",
                "label": label,
                "status": status_text,
                "detail": visible_detail,
                "tone": "active" if status == "running" else ("blocked" if status == "error" else "done"),
            }
        ],
        "ceo_loop": {
            "status": "working" if status == "running" else status_text,
            "headline": f"{label} is {status_text}.",
            "detail": visible_detail,
        },
    }


def _takyon_record_runtime_event(
    business: str,
    *,
    kind: str,
    status: str,
    detail: str = "",
    line: str = "",
    command: str = "",
) -> None:
    slug = str(business or "").strip()
    if not slug:
        return
    payload = {
        "kind": kind,
        "status": status,
        "detail": detail,
        "line": line,
        "command": command,
        "recorded_at": datetime.utcnow().isoformat() + "Z",
    }
    try:
        from plugins.takyon.cli import TakyonStore

        store = TakyonStore()
        with store._connect() as conn:
            store._record_event(
                conn,
                scope=f"business:{slug}/runtime",
                business_slug=slug,
                event_type=f"dashboard.run.{status}",
                payload=payload,
            )
    except Exception as exc:
        logger.debug("failed to record Takyon runtime event for %s: %s", slug, exc)


def _takyon_clean_runtime_line(line: str) -> str:
    text = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", str(line or "")).strip()
    text = re.sub(r"^(?:->|→)\s*", "", text)
    text = re.sub(r"\s+", " ", text)
    return text[:360]


def _takyon_set_background_run(business: str, run: dict[str, Any]) -> None:
    slug = str(business or "").strip()
    if not slug:
        return
    with _TAKYON_BACKGROUND_RUNS_LOCK:
        _TAKYON_BACKGROUND_RUNS[slug] = {**run, "business": slug}


def _takyon_get_background_run(business: str) -> dict[str, Any] | None:
    slug = str(business or "").strip()
    if not slug:
        return None
    with _TAKYON_BACKGROUND_RUNS_LOCK:
        run = _TAKYON_BACKGROUND_RUNS.get(slug)
        if not isinstance(run, dict):
            return None
        started_at = float(run.get("started_at") or 0)
        finished_at = float(run.get("finished_at") or 0)
        if finished_at and finished_at < time.time() - 7200:
            _TAKYON_BACKGROUND_RUNS.pop(slug, None)
            return None
        if not finished_at and started_at and started_at < time.time() - 7200:
            return None
        return dict(run)


def _takyon_reconcile_background_run(
    business: str,
    run: dict[str, Any] | None,
    overview: dict[str, Any] | None,
) -> dict[str, Any] | None:
    def _parse_ts(value: Any) -> float:
        text = str(value or "").strip()
        if not text:
            return 0.0
        try:
            return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
        except Exception:
            return 0.0

    def _job_matches(candidate: dict[str, Any], expected_kind: str, expected_job_id: str) -> bool:
        if expected_job_id and str(candidate.get("id") or "").strip() == expected_job_id:
            return True
        candidate_kind = str(candidate.get("kind") or "").strip().lower()
        if expected_kind == "create":
            return candidate_kind == "ceo_bootstrap"
        if expected_kind == "wake":
            return candidate_kind == "ceo_wake"
        return False

    current = dict(run) if isinstance(run, dict) else None
    jobs = (overview or {}).get("jobs") if isinstance(overview, dict) else None
    if not isinstance(jobs, list) or not jobs:
        return current

    current_kind = str((current or {}).get("kind") or "").strip().lower()
    current_job_id = str((current or {}).get("job_id") or "").strip()
    matched: dict[str, Any] | None = None
    matched_ts = 0.0
    for item in jobs:
        if not isinstance(item, dict):
            continue
        if current and not _job_matches(item, current_kind, current_job_id):
            continue
        item_ts = _parse_ts(item.get("updated_at") or item.get("created_at"))
        if matched is None or item_ts >= matched_ts:
            matched = item
            matched_ts = item_ts

    if matched is None:
        return current

    job_status = str(matched.get("status") or "").strip().lower()
    if not job_status:
        return current

    matched_kind = str(matched.get("kind") or "").strip().lower()
    reconciled = current or {
        "kind": "create" if matched_kind == "ceo_bootstrap" else matched_kind,
        "business": str(business or "").strip(),
        "started_at": matched_ts or time.time(),
    }
    reconciled["job_id"] = str(matched.get("id") or reconciled.get("job_id") or "").strip()
    reconciled["status"] = job_status
    detail = str(matched.get("detail") or "").strip()
    if detail:
        reconciled["detail"] = detail
    if job_status in {"blocked", "failed", "completed"} and matched_ts:
        reconciled["finished_at"] = matched_ts
    return reconciled


# --- Task rollup taxonomy ---------------------------------------------------
# OPERATOR-APPROVED (GOAL_RULES §5/§7, locked 2026-06-17): the category set, the
# status-pill labels, and the intent-first title phrasing below are the final
# shipped operator copy for the Tasks rollup. Presentation-only — this layer does
# NOT touch the Hermes runtime or its intra-turn context (§7); it only relabels
# what the rollup renders.
#
# Status pills (internal canonical status -> operator label):
#   queued -> PLANNED, running -> RUNNING, blocked -> BLOCKED,
#   needs_review -> NEEDS REVIEW, completed -> DONE, failed -> FAILED.
# Category taxonomy (one pill per task): RESEARCH / PRODUCT / LAUNCH / GROWTH / OPS.
_TAKYON_TASK_CATEGORIES = ("RESEARCH", "PRODUCT", "LAUNCH", "GROWTH", "OPS")

# Spec criterion #3: status pills must use these canonical values, never raw
# runtime statuses like "recorded", "live", or "queued"-from-source.
_TAKYON_TASK_STATUS_LABELS = {
    "queued": "PLANNED",
    "running": "RUNNING",
    "blocked": "BLOCKED",
    "needs_review": "NEEDS REVIEW",
    "completed": "DONE",
    "failed": "FAILED",
    "idle": "Idle",
}

# Ordered (keyword -> category) hints. First match wins. Keyword is matched
# against the lowercased raw label + detail + source.
_TAKYON_TASK_CATEGORY_HINTS: tuple[tuple[tuple[str, ...], str], ...] = (
    (("research", "market", "competitor", "audience", "icp", "interview", "discovery", "brain"), "RESEARCH"),
    (("ops", "billing", "invoice", "payout", "credential", "secret", "reconcile", "maintenance", "monitor", "infra"), "OPS"),
    (("growth", "outreach", "campaign", "post", "tweet", "reddit", "email", "ad", "ads", "funnel", "acquisition", "conversion", "seo", "social"), "GROWTH"),
    (("publish", "deploy", "launch", "release", "ship", "go-live", "go live", "distribution", "wake", "cron", "schedul"), "LAUNCH"),
    (("product", "build", "site", "spec", "offer", "feature", "app", "checkout", "pricing", "logo", "design"), "PRODUCT"),
)


def _takyon_task_category(label: str, detail: str, source: str) -> str:
    """Best-effort intent category for a task (operator-approved taxonomy).

    Returns one of RESEARCH / PRODUCT / LAUNCH / GROWTH / OPS.
    """
    haystack = " ".join(part for part in (label, detail, source) if part).lower()
    for keywords, category in _TAKYON_TASK_CATEGORY_HINTS:
        if any(token in haystack for token in keywords):
            return category
    # Pure scheduling/background work is go-to-market cadence (wakes drive the
    # outreach/launch loop); CEO/bootstrap company-building defaults to PRODUCT.
    if source in {"cron", "background"}:
        return "LAUNCH"
    return "PRODUCT"


# Verb-led, outcome-first title style (operator-approved): <=8 words, never a raw
# tool/op name. Used to recast obvious tool-identifier labels into an outcome.
_TAKYON_TASK_TITLE_MAX_WORDS = 8


def _takyon_task_intent_title(label: str, detail: str, source: str) -> str:
    """Verb-led, outcome-first title — never a raw tool-call string (<=8 words).

    Operator-approved style: outcome-first, capped at 8 words. Strips obvious
    tool/identifier noise so a card never shows e.g. "business_write_file" or
    "takyon:tool:foo"; does not invent specific business copy.
    """
    text = str(label or "").strip()
    if not text:
        text = "Recorded work"
    # Strip raw tool/skill identifier shapes so a card never shows e.g.
    # "business_write_file" or "takyon:tool:foo".
    if re.search(r"[a-z]+_[a-z]+|^takyon:|^tool:|::", text):
        cleaned = re.sub(r"^(?:takyon:|tool:)", "", text)
        cleaned = cleaned.split("::")[-1]
        cleaned = re.sub(r"[._:-]+", " ", cleaned).strip()
        if cleaned:
            text = " ".join(part.capitalize() for part in cleaned.split())
    # Outcome-first style caps the title at 8 words; the full label remains
    # available in the expanded "raw:" detail row.
    words = text.split()
    if len(words) > _TAKYON_TASK_TITLE_MAX_WORDS:
        text = " ".join(words[:_TAKYON_TASK_TITLE_MAX_WORDS]) + "…"
    return text[:120]


def _takyon_task_description(label: str, detail: str, category: str) -> str:
    """One-sentence description for the card (spec criterion #5: non-empty)."""
    base = str(detail or "").strip()
    if base:
        # Keep it to a single sentence-ish line.
        first = re.split(r"(?<=[.!?])\s+", base, maxsplit=1)[0].strip()
        if first:
            return first[:240]
    # Fall back to an intent-shaped sentence from the title + category.
    title = str(label or "this work").strip() or "this work"
    lane = {
        "RESEARCH": "Research toward the next company move",
        "PRODUCT": "Building the product",
        "LAUNCH": "Taking the company to market",
        "GROWTH": "Growing demand and distribution",
        "OPS": "Keeping the company running",
    }.get(category, "Company work")
    return f"{lane}: {title}."[:240]


def _takyon_attach_operator_update_copy(
    live_state: Any, overview: dict[str, Any] | None
) -> None:
    """Surface the CEO's curated headline/summary on the live_state.

    business_post_operator_update mirrors its warm headline + 1-2 sentence
    summary onto overview.ceo_loop. We copy that onto the live_state so the chat
    "CEO update" card shows the curated operator copy — never the raw assistant
    reasoning stream. Presentation-only; does not touch the agent's turn context.
    """
    if not isinstance(live_state, dict) or not isinstance(overview, dict):
        return
    ceo_loop = overview.get("ceo_loop")
    if not isinstance(ceo_loop, dict):
        return
    headline = str(ceo_loop.get("headline") or "").strip()
    detail = str(ceo_loop.get("detail") or "").strip()
    if headline:
        live_state["headline"] = headline
    if detail:
        live_state["summary"] = detail


def _takyon_live_state_payload(
    overview: dict[str, Any] | None,
    background_run: dict[str, Any] | None,
) -> dict[str, Any]:
    def as_dict(value: Any) -> dict[str, Any]:
        return value if isinstance(value, dict) else {}

    def as_list(value: Any) -> list[Any]:
        return value if isinstance(value, list) else []

    def as_text(value: Any) -> str:
        return str(value or "").strip()

    def run_label(kind: str) -> str:
        key = as_text(kind).lower()
        if key == "create":
            return "CEO bootstrap"
        if key == "wake":
            return "CEO wake"
        if key == "turn":
            return "CEO turn"
        if key:
            words = re.sub(r"[._-]+", " ", key)
            return " ".join(part.capitalize() for part in words.split())
        return "Background run"

    def canonical_task_status(value: Any) -> str:
        status = as_text(value).lower()
        if not status:
            return "idle"
        # FAILED (red) is a hard error; BLOCKED (amber) and NEEDS REVIEW (purple)
        # are distinct operator-approved lifecycle states checked before failure.
        if any(token in status for token in ("needs_review", "needs review", "review", "approval", "awaiting")):
            return "needs_review"
        if any(token in status for token in ("blocked", "stuck", "paused")):
            return "blocked"
        if any(token in status for token in ("failed", "error", "recovering", "needs_attention", "overdue", "stale")):
            return "failed"
        if any(token in status for token in ("queued", "scheduled", "pending", "planned", "waiting")):
            return "queued"
        if any(token in status for token in ("done", "complete", "completed", "success", "succeeded", "published", "visible", "previewable")):
            return "completed"
        if any(token in status for token in ("running", "active", "live")):
            return "running"
        if status in {"working", "watching", "research_first", "idle", "quiet", "missing"}:
            return "idle"
        return "idle"

    def canonical_hint_status(value: Any) -> str:
        status = as_text(value).lower()
        if not status:
            return "idle"
        if any(token in status for token in ("failed", "error", "blocked", "recovering", "needs_attention", "overdue", "stale")):
            return "failed"
        if any(token in status for token in ("queued", "scheduled", "pending", "waiting")):
            return "queued"
        if any(token in status for token in ("running", "active", "live")):
            return "running"
        return "idle"

    def canonical_task(task: dict[str, Any]) -> dict[str, Any]:
        label = as_text(task.get("label")) or "Recorded work"
        status = canonical_task_status(task.get("status"))
        detail = as_text(task.get("detail")) or "Tracked in the workspace overview."
        updated_at = as_text(task.get("updated_at") or task.get("created_at"))
        source = as_text(task.get("source")) or "overview"
        task_id = as_text(task.get("id")) or f"task:{label}:{status}"
        # Prefer an explicit CEO-authored milestone category/title/description
        # when the card carries one (e.g. a work-request job whose payload set
        # title/description/category); the heuristics below are fallbacks only.
        explicit_category = as_text(task.get("category")).upper()
        category = (
            explicit_category
            if explicit_category in _TAKYON_TASK_CATEGORIES
            else _takyon_task_category(label, detail, source)
        )
        title = as_text(task.get("title")) or _takyon_task_intent_title(label, detail, source)
        description = as_text(task.get("description")) or _takyon_task_description(label, detail, category)
        # Spec criterion #6: raw low-level events carry a parent task_id so the
        # frontend can nest them under an intent-level task instead of flat rows.
        parent_task_id = as_text(task.get("task_id") or task.get("parent_task_id"))
        steps = [step for step in as_list(task.get("steps")) if isinstance(step, dict)]
        outputs = [out for out in as_list(task.get("outputs")) if out not in (None, "")]
        return {
            "id": task_id,
            "task_id": parent_task_id or task_id,
            "source": source,
            "label": label,
            "title": title,
            "description": description,
            "category": category,
            "status": status,
            "status_label": _TAKYON_TASK_STATUS_LABELS.get(status, status.capitalize()),
            "detail": detail,
            "steps": steps,
            "outputs": outputs,
            "updated_at": updated_at,
        }

    overview_dict = as_dict(overview)
    current_action = as_dict(overview_dict.get("current_action"))
    ceo_loop = as_dict(overview_dict.get("ceo_loop"))
    raw_tasks = [canonical_task(task) for task in as_list(overview_dict.get("tasks")) if isinstance(task, dict)]

    run = as_dict(background_run)
    run_status = canonical_task_status(run.get("status"))
    run_label_text = run_label(as_text(run.get("kind")))
    run_detail = as_text(run.get("detail")) or (
        "CEO bootstrap is queued." if run_status == "queued"
        else "CEO bootstrap is running." if run_status == "running" and run_label_text == "CEO bootstrap"
        else "Background work is queued." if run_status == "queued"
        else "Background work is running." if run_status == "running"
        else ""
    )
    run_updated_at = as_text(run.get("updated_at") or run.get("finished_at") or run.get("started_at"))
    run_task = canonical_task({
        "id": as_text(run.get("job_id")) or f"background:{run_label_text.lower().replace(' ', '-')}",
        "source": "background",
        "label": run_label_text,
        "status": run_status,
        "detail": run_detail,
        "updated_at": run_updated_at,
    }) if run_status in {"running", "queued", "failed"} else None

    live_tasks = list(raw_tasks)
    if run_task and not any(
        task.get("label") == run_task["label"]
        and task.get("status") == run_task["status"]
        and task.get("detail") == run_task["detail"]
        for task in live_tasks
    ):
        live_tasks.insert(0, run_task)

    hint_candidates = [
        canonical_hint_status(current_action.get("status")),
        canonical_hint_status(ceo_loop.get("status")),
    ]
    if "failed" in hint_candidates:
        hint_status = "failed"
    elif "running" in hint_candidates:
        hint_status = "running"
    elif "queued" in hint_candidates:
        hint_status = "queued"
    else:
        hint_status = "idle"
    has_active_run = bool(run_task and run_status in {"running", "queued"})
    if hint_status == "failed" and not has_active_run:
        live_tasks = [task for task in live_tasks if task.get("status") != "running"]

    # Spec criterion #6: low-level runtime/tool events must attach to a parent
    # intent task via task_id rather than float as standalone top-level rows.
    # The "current" intent task is the first running task, else the first queued,
    # else the first task. Raw runtime/trace events that did not already declare
    # a parent get grouped under it.
    def _is_intent_task(task: dict[str, Any]) -> bool:
        return as_text(task.get("source")) not in {"runtime", "trace"}

    # The side-channel rollup milestones (source "task_rollup") and the CEO's
    # curated operator_update milestones are the PRIMARY intent rows. When such
    # milestone rows are present, prefer them as the anchor and nest the raw
    # work rows (jobs/agent/runtime/trace/background/cron) underneath, so the
    # panel shows milestone cards on top with low-level worker events grouped
    # under them rather than as flat sibling rows.
    _MILESTONE_SOURCES = {"task_rollup", "operator_update"}

    def _is_milestone_task(task: dict[str, Any]) -> bool:
        return as_text(task.get("source")) in _MILESTONE_SOURCES

    has_milestone = any(_is_milestone_task(t) for t in live_tasks)

    def _pick_anchor(predicate) -> dict[str, Any] | None:
        return (
            next((t for t in live_tasks if t.get("status") == "running" and predicate(t)), None)
            or next((t for t in live_tasks if t.get("status") == "queued" and predicate(t)), None)
            or next((t for t in live_tasks if predicate(t)), None)
        )

    if has_milestone:
        intent_anchor = _pick_anchor(_is_milestone_task) or _pick_anchor(_is_intent_task)
    else:
        intent_anchor = _pick_anchor(_is_intent_task)
    current_task_id = as_text((intent_anchor or {}).get("id"))
    if current_task_id:
        # When a milestone anchor leads, raw work rows nest under it. Otherwise
        # only the low-level runtime/trace events nest (legacy behavior — job and
        # operator_update milestone rows stay as their own top-level intents).
        nestable_sources = (
            {"runtime", "trace", "job", "agent", "background", "cron"}
            if has_milestone
            else {"runtime", "trace"}
        )
        for task in live_tasks:
            if task.get("id") == current_task_id:
                continue
            # Never nest one milestone under another; keep every milestone row
            # as a top-level primary card.
            if _is_milestone_task(task):
                continue
            # Only re-parent rows that did not already carry an explicit parent
            # (task_id defaults to its own id in canonical_task).
            if as_text(task.get("source")) in nestable_sources and as_text(
                task.get("task_id")
            ) == as_text(task.get("id")):
                task["task_id"] = current_task_id

    def first_task(status: str) -> dict[str, Any] | None:
        return next((task for task in live_tasks if task.get("status") == status), None)

    active_task = first_task("running")
    queued_task = first_task("queued")
    failed_task = first_task("failed")
    blocked_task = first_task("blocked")
    review_task = first_task("needs_review")
    completed_task = first_task("completed")

    if active_task:
        return {
            "status": "running",
            "label": as_text(active_task.get("label")) or "Working…",
            "detail": as_text(active_task.get("detail")) or "Working…",
            "updated_at": as_text(active_task.get("updated_at")),
            "tasks": live_tasks[:16],
            "current_task_id": current_task_id,
        }
    if queued_task:
        return {
            "status": "queued",
            "label": as_text(queued_task.get("label")) or "Queued",
            "detail": as_text(queued_task.get("detail")) or "Queued work is waiting to run.",
            "updated_at": as_text(queued_task.get("updated_at")),
            "tasks": live_tasks[:16],
            "current_task_id": current_task_id,
        }
    if failed_task:
        return {
            "status": "failed",
            "label": as_text(failed_task.get("label")) or "Needs attention",
            "detail": as_text(failed_task.get("detail")) or "Recorded work needs attention.",
            "updated_at": as_text(failed_task.get("updated_at")),
            "tasks": live_tasks[:16],
            "current_task_id": current_task_id,
        }
    if blocked_task:
        return {
            "status": "blocked",
            "label": as_text(blocked_task.get("label")) or "Blocked",
            "detail": as_text(blocked_task.get("detail")) or "Work is blocked and waiting on a dependency.",
            "updated_at": as_text(blocked_task.get("updated_at")),
            "tasks": live_tasks[:16],
            "current_task_id": current_task_id,
        }
    if review_task:
        return {
            "status": "needs_review",
            "label": as_text(review_task.get("label")) or "Needs review",
            "detail": as_text(review_task.get("detail")) or "Work is awaiting operator review.",
            "updated_at": as_text(review_task.get("updated_at")),
            "tasks": live_tasks[:16],
            "current_task_id": current_task_id,
        }
    if hint_status == "failed":
        label = as_text(current_action.get("label")) or as_text(ceo_loop.get("headline")) or "Needs attention"
        detail = as_text(current_action.get("detail")) or as_text(ceo_loop.get("detail")) or "Recorded work needs attention."
        synthetic_task = canonical_task({
            "id": "live-state:failed",
            "source": as_text(current_action.get("source")) or "overview",
            "label": label,
            "status": "failed",
            "detail": detail,
            "updated_at": "",
        })
        return {
            "status": "failed",
            "label": label,
            "detail": detail,
            "updated_at": "",
            "tasks": [synthetic_task, *live_tasks][:16],
            "current_task_id": as_text(synthetic_task.get("id")) or current_task_id,
        }
    if completed_task:
        return {
            "status": "completed",
            "label": as_text(completed_task.get("label")) or "Completed",
            "detail": as_text(completed_task.get("detail")) or "Recent work completed.",
            "updated_at": as_text(completed_task.get("updated_at")),
            "tasks": live_tasks[:16],
            "current_task_id": current_task_id,
        }
    return {
        "status": "idle",
        "label": "Idle",
        "detail": "",
        "updated_at": "",
        "tasks": live_tasks[:16],
            "current_task_id": current_task_id,
    }


def _takyon_operator_user_id(session: dict | None) -> str:
    return str((session or {}).get("takyon_operator_user_id") or "").strip()


def _takyon_store(session: dict | None):
    from plugins.takyon.cli import TakyonStore
    operator_user_id = _takyon_operator_user_id(session) or None
    if not isinstance(session, dict):
        return TakyonStore(operator_user_id=operator_user_id)
    cached = session.get("takyon_store")
    if isinstance(cached, TakyonStore):
        cached_user_id = str(getattr(cached, "_operator_user_id", "") or "").strip() or None
        if cached_user_id == operator_user_id:
            return cached
    store = TakyonStore(operator_user_id=operator_user_id)
    session["takyon_store"] = store
    return store


def _takyon_runtime_event_cursor(created_at: Any, event_id: Any) -> str:
    ts = str(created_at or "").strip()
    eid = str(event_id or "").strip()
    if not ts and not eid:
        return ""
    return f"{ts}::{eid}"


def _takyon_parse_runtime_event_cursor(cursor: Any) -> tuple[str, str]:
    text = str(cursor or "").strip()
    if not text:
        return "", ""
    if "::" not in text:
        return text, ""
    created_at, event_id = text.split("::", 1)
    return created_at.strip(), event_id.strip()


_TAKYON_BUSINESSES_CACHE_TTL_SECONDS = max(
    0.25,
    float(os.getenv("TAKYON_BUSINESSES_CACHE_TTL_SECONDS", "1.5") or 1.5),
)


def _takyon_invalidate_businesses_cache(session: dict | None) -> None:
    if not isinstance(session, dict):
        return
    session.pop("takyon_businesses_cache", None)


def _takyon_businesses_for_session(
    session: dict | None,
    store=None,
) -> list[dict[str, Any]]:
    if isinstance(session, dict):
        cached = session.get("takyon_businesses_cache")
        cached_at = 0.0
        if isinstance(cached, dict):
            cached_at = float(cached.get("at") or 0.0)
            items = cached.get("items")
            if (
                isinstance(items, list)
                and items
                and (time.monotonic() - cached_at) <= _TAKYON_BUSINESSES_CACHE_TTL_SECONDS
            ):
                return [item for item in items if isinstance(item, dict)]
            if items == [] and (time.monotonic() - cached_at) <= _TAKYON_BUSINESSES_CACHE_TTL_SECONDS:
                return []
    active_store = store or _takyon_store(session)
    data = active_store.read(scope="global", query="list_businesses", limit=200)
    businesses = data.get("businesses") if isinstance(data, dict) else []
    items = [item for item in businesses if isinstance(item, dict)] if isinstance(businesses, list) else []
    if isinstance(session, dict):
        session["takyon_businesses_cache"] = {
            "at": time.monotonic(),
            "items": items,
        }
    if not isinstance(businesses, list):
        return []
    return items


def _takyon_can_access_business(
    session: dict | None,
    business: str,
    *,
    businesses: list[dict[str, Any]] | None = None,
) -> bool:
    slug = str(business or "").strip()
    if not slug:
        return False
    return any(
        str(item.get("slug") or "").strip() == slug
        for item in (businesses if businesses is not None else _takyon_businesses_for_session(session))
    )


def _takyon_require_business_access(
    session: dict | None,
    business: str,
    *,
    businesses: list[dict[str, Any]] | None = None,
) -> str | None:
    slug = str(business or "").strip()
    if not slug:
        return "business scope required"
    if _takyon_can_access_business(session, slug, businesses=businesses):
        return None
    return f"access denied for business:{slug}"


def _takyon_requested_business(
    session: dict | None,
    params: dict | None = None,
) -> str:
    raw = ""
    if isinstance(params, dict):
        raw = str(
            params.get("business_slug")
            or params.get("business")
            or ""
        ).strip()
    if not raw and isinstance(session, dict):
        raw = str(session.get("takyon_current_business") or "").strip()
    if raw.lower() in {"global", "root", "none"}:
        return ""
    if not raw:
        return ""
    try:
        from plugins.takyon.cli import _slugify

        return _slugify(raw)
    except Exception:
        return raw


def _takyon_workspace_boot_payload(
    session: dict | None,
    business: str,
) -> dict[str, Any]:
    slug = str(business or "").strip()
    if not slug:
        return {
            "business_slug": "",
            "current": {},
            "overview": {},
            "outputs": [],
            "deliverables": [],
            "background_run": None,
            "live_state": {
                "status": "idle",
                "label": "Idle",
                "detail": "",
                "updated_at": "",
                "tasks": [],
            },
        }
    store = _takyon_store(session)
    try:
        snapshot = _takyon_business_home_snapshot(store, slug, sync_files=False)
    except Exception:
        snapshot = {}
    current = snapshot.get("current") if isinstance(snapshot, dict) else {}
    overview = snapshot.get("overview") if isinstance(snapshot, dict) else {}
    background_run = _takyon_reconcile_background_run(
        slug,
        _takyon_get_background_run(slug),
        overview if isinstance(overview, dict) else {},
    )
    deliverables = _takyon_workspace_deliverables_payload(
        overview if isinstance(overview, dict) else {},
        [],
    )
    live_state = _takyon_live_state_payload(
        overview if isinstance(overview, dict) else {},
        background_run,
    )
    _takyon_attach_operator_update_copy(
        live_state, overview if isinstance(overview, dict) else {}
    )
    overview_payload = dict(overview) if isinstance(overview, dict) else {}
    product_payload = dict(overview_payload.get("product") or {})
    product_payload.update(
        _takyon_workspace_preview_payload(
            overview_payload,
            [],
        )
    )
    overview_payload["product"] = product_payload
    return {
        "business_slug": slug,
        "current": current if isinstance(current, dict) else {},
        "overview": overview_payload,
        "outputs": [],
        "deliverables": deliverables,
        "background_run": background_run,
        "live_state": live_state,
    }


def _takyon_workspace_payload(
    session: dict | None,
    business: str,
    *,
    output_limit: int = 50,
    view: str = "full",
) -> dict[str, Any]:
    slug = str(business or "").strip()
    if not slug:
        return {
            "business_slug": "",
            "current": {},
            "overview": {},
            "outputs": [],
            "deliverables": [],
            "background_run": None,
            "live_state": {
                "status": "idle",
                "label": "Idle",
                "detail": "",
                "updated_at": "",
                "tasks": [],
            },
        }
    if str(view or "").strip().lower() == "boot":
        return _takyon_workspace_boot_payload(session, slug)
    store = _takyon_store(session)
    try:
        summary = store.read(scope=f"business:{slug}", query="summary", limit=12)
    except Exception:
        summary = {}
    current = _takyon_business_payload_from_summary(summary) or {}
    overview = _takyon_business_overview_payload(store, slug, summary_data=summary)
    outputs = _takyon_historical_outputs_payload(
        store,
        slug,
        limit=max(1, min(int(output_limit or 50), 100)),
    )
    deliverables = _takyon_workspace_deliverables_payload(
        overview if isinstance(overview, dict) else {},
        outputs if isinstance(outputs, list) else [],
    )
    background_run = _takyon_reconcile_background_run(
        slug,
        _takyon_get_background_run(slug),
        overview if isinstance(overview, dict) else {},
    )
    live_state = _takyon_live_state_payload(
        overview if isinstance(overview, dict) else {},
        background_run,
    )
    _takyon_attach_operator_update_copy(
        live_state, overview if isinstance(overview, dict) else {}
    )
    overview_payload = dict(overview) if isinstance(overview, dict) else {}
    product_payload = dict(overview_payload.get("product") or {})
    product_payload.update(
        _takyon_workspace_preview_payload(
            overview_payload,
            outputs if isinstance(outputs, list) else [],
        )
    )
    overview_payload["product"] = product_payload
    return {
        "business_slug": slug,
        "current": current,
        "overview": overview_payload,
        "outputs": outputs if isinstance(outputs, list) else [],
        "deliverables": deliverables,
        "background_run": background_run,
        "live_state": live_state,
    }


def _takyon_dashboard_state_payload(
    session: dict | None,
    *,
    explicit_business: bool = False,
    business: str = "",
    output_limit: int = 50,
    view: str = "full",
) -> dict[str, Any]:
    store = _takyon_store(session)
    businesses = _takyon_businesses_for_session(session, store=store)
    auto_slug, auto_warning = _takyon_maybe_auto_enter_created_business(session, businesses)

    if explicit_business:
        current_business = str(business or "").strip()
        if isinstance(session, dict):
            session["takyon_current_business"] = current_business
    else:
        current_business = _takyon_requested_business(session)
        if auto_slug:
            current_business = auto_slug

    if current_business:
        access_error = _takyon_require_business_access(
            session,
            current_business,
            businesses=businesses,
        )
        if access_error:
            raise PermissionError(access_error)

    workspace = _takyon_workspace_payload(
        session,
        current_business,
        output_limit=output_limit,
        view=view,
    )
    return {
        "scope": f"business:{current_business}" if current_business else "global",
        "business": current_business,
        "business_slug": current_business,
        "businesses": businesses,
        "current": workspace.get("current") or {},
        "overview": workspace.get("overview") or {},
        "outputs": workspace.get("outputs") or [],
        "deliverables": workspace.get("deliverables") or [],
        "background_run": workspace.get("background_run"),
        "live_state": workspace.get("live_state") or {},
        "auto_switched_business": auto_slug or "",
        "auto_scope_warning": auto_warning or "",
    }


def _takyon_scope_payload(session: dict | None) -> dict[str, Any]:
    try:
        from plugins.takyon.cli import _slugify

        store = _takyon_store(session)
        businesses = _takyon_businesses_for_session(session, store=store)
        auto_slug, auto_warning = _takyon_maybe_auto_enter_created_business(session, businesses)
        raw_business = str((session or {}).get("takyon_current_business") or "").strip()
        current_business = _slugify(raw_business) if raw_business else ""
        exists = any(
            isinstance(item, dict) and str(item.get("slug") or "") == current_business
            for item in businesses
        )
        pending = (session or {}).get("takyon_background_run") if isinstance(session, dict) else None
        if (not isinstance(pending, dict) or str(pending.get("business") or "").strip() != current_business) and current_business:
            pending = _takyon_get_background_run(current_business)
        pending_business = str((pending or {}).get("business") or "").strip() if isinstance(pending, dict) else ""
        pending_recent = bool(
            pending_business
            and pending_business == current_business
            and float((pending or {}).get("started_at") or 0) > time.time() - 7200
        )
        pending_status = str((pending or {}).get("status") or "") if isinstance(pending, dict) else ""
        if current_business and not exists and pending_recent and pending_status == "running":
            current = {
                "slug": current_business,
                "name": current_business,
                "status": "creating" if pending.get("kind") == "create" else "working",
                "state": "working",
                "reason": "Takyon is running in the background.",
            }
            businesses = [current, *businesses]
            overview = _takyon_pending_scope_overview(
                str(pending.get("kind") or "run"),
                current_business,
                str(pending.get("status") or "running"),
                str(pending.get("detail") or ""),
                float(pending.get("started_at") or 0),
            )
        elif current_business and not exists:
            current_business = ""
            if session is not None:
                session["takyon_current_business"] = ""
            current = None
            overview = {}
        else:
            current = _takyon_business_payload(store, current_business) if current_business else None
            overview = (
                _takyon_business_overview_payload(store, current_business)
                if current_business
                else {}
            )
            if current_business and pending_recent and isinstance(overview, dict):
                if pending_status == "running":
                    pending_overview = _takyon_pending_scope_overview(
                        str(pending.get("kind") or "run"),
                        current_business,
                        pending_status,
                        str(pending.get("detail") or ""),
                        float(pending.get("started_at") or 0),
                    )
                    overview = {
                        **overview,
                        "tasks": [
                            *(pending_overview.get("tasks") or []),
                            *((overview.get("tasks") if isinstance(overview.get("tasks"), list) else []) or []),
                        ],
                        "ceo_loop": pending_overview.get("ceo_loop") or overview.get("ceo_loop"),
                    }
        if isinstance(overview, dict):
            overview.setdefault("registry", _takyon_registry_display_payload())
        return {
            "scope": f"business:{current_business}" if current_business else "global",
            "business": current_business,
            "current": current or {},
            "businesses": businesses,
            "overview": overview,
            "auto_switched_business": auto_slug or "",
            "auto_scope_warning": auto_warning or "",
        }
    except Exception as e:
        return {
            "scope": "global",
            "business": "",
            "current": {},
            "businesses": [],
            "overview": {},
            "warning": str(e),
        }


@method("takyon.scope.get")
def _(rid, params: dict) -> dict:
    return _ok(rid, _takyon_scope_payload(_takyon_session(params)))


@method("takyon.scope.set")
def _(rid, params: dict) -> dict:
    session = _takyon_session(params)
    if session is None:
        return _err(rid, 4001, "session not found")

    business = str(params.get("business") or "").strip()
    if business.lower() in {"", "global", "root", "none"}:
        session["takyon_current_business"] = ""
        return _ok(rid, _takyon_scope_payload(session))

    try:
        from plugins.takyon.cli import _slugify

        slug = _slugify(business)
        logger.warning(
            "takyon scope.set requested business=%s operator_user_id=%s",
            slug,
            _takyon_operator_user_id(session) or "",
        )
        exists = _takyon_can_access_business(session, slug)
        if not exists:
            businesses = [
                str(item.get("slug") or "").strip()
                for item in _takyon_businesses_for_session(session)
                if isinstance(item, dict)
            ]
            message = (
                f"Could not open business:{slug}. No businesses are visible for this account."
                if not businesses
                else f"Could not open business:{slug}. That business is not available to this account."
            )
            logger.warning(
                "takyon scope.set denied business=%s operator_user_id=%s visible_businesses=%s",
                slug,
                _takyon_operator_user_id(session) or "",
                businesses,
            )
            return _err(rid, 4041, message)
        session["takyon_current_business"] = slug
        return _ok(rid, _takyon_scope_payload(session))
    except Exception as e:
        return _err(rid, 5041, str(e))


@method("takyon.wake.schedule")
def _(rid, params: dict) -> dict:
    session = _takyon_session(params)
    if session is None:
        return _err(rid, 4001, "session not found")

    business = str(session.get("takyon_current_business") or "").strip()
    access_error = _takyon_require_business_access(session, business)
    if access_error:
        return _err(rid, 4004, access_error)

    schedule = str(params.get("schedule") or "").strip() or "every 6h"
    try:
        _takyon_store(session).commit(
            scope=f"business:{business}",
            operations=[
                {
                    "action": "cron.ensure_ceo_wakeup",
                    "business": business,
                    "schedule": schedule,
                }
            ],
            idempotency_key=f"dashboard-wake-schedule:{business}:{uuid.uuid4().hex}",
            reason="dashboard updated CEO wake schedule",
            actor="operator",
        )
        return _ok(
            rid,
            {
                "output": f"CEO wake schedule set to {schedule} for business:{business}.",
                **_takyon_scope_payload(session),
            },
        )
    except Exception as e:
        return _err(rid, 5043, str(e))


@method("takyon.slash.complete")
def _(rid, params: dict) -> dict:
    text = str(params.get("text") or "")
    session = _takyon_session(params)
    current_business = str((session or {}).get("takyon_current_business") or "").strip()
    if not text.startswith("/"):
        return _ok(rid, {"items": [], "replace_from": 0})
    try:
        from plugins.takyon.cli import _slash_entries, _slash_matches

        items: list[dict[str, Any]] = []
        for entry in _slash_matches(_slash_entries(), text, current_business)[:30]:
            name = str(entry.get("name") or "").strip().lstrip("/")
            if not name:
                continue
            scope = "business" if entry.get("requires_business") else "global"
            kind = "skill" if entry.get("kind") == "skill" else "control"
            priority = str(entry.get("priority_band") or "").strip()
            description = str(entry.get("description") or "").strip()
            meta_parts = [kind, scope]
            if priority:
                meta_parts.append(priority)
            items.append(
                {
                    "text": f"/{name}",
                    "display": f"/{name}",
                    "meta": " ".join(meta_parts),
                    "description": description,
                    "requires_business": bool(entry.get("requires_business")),
                    "kind": kind,
                }
            )
        return _ok(rid, {"items": items, "replace_from": 0})
    except Exception as e:
        return _err(rid, 5042, str(e))


@method("takyon.files.list")
def _(rid, params: dict) -> dict:
    session = _takyon_session(params)
    if session is None:
        return _err(rid, 4001, "session not found")
    business = _takyon_requested_business(session, params)
    access_error = _takyon_require_business_access(session, business)
    if access_error:
        return _err(rid, 4004, access_error)
    path = str(params.get("path") or ".").strip() or "."
    try:
        data = _takyon_store(session).read(
            scope=f"business:{business}",
            query="list_files",
            path=path,
            limit=100,
        )
        return _ok(rid, {**data, **_takyon_scope_payload(session)})
    except Exception as e:
        return _err(rid, 5045, str(e))


@method("takyon.file.read")
def _(rid, params: dict) -> dict:
    session = _takyon_session(params)
    if session is None:
        return _err(rid, 4001, "session not found")
    business = _takyon_requested_business(session, params)
    access_error = _takyon_require_business_access(session, business)
    if access_error:
        return _err(rid, 4004, access_error)
    path = str(params.get("path") or "").strip()
    if not path:
        return _err(rid, 4004, "path required")
    try:
        store = _takyon_store(session)
        file_path = store._resolve_business_file(business, path)
        if not file_path.exists() or not file_path.is_file():
            return _err(rid, 4044, f"file not found: {path}")
        size = file_path.stat().st_size
        with file_path.open("rb") as fh:
            raw = fh.read(min(size, _TAKYON_MAX_FILE_READ_BYTES))
        rel = str(file_path.relative_to(store._business_root(business)))
        return _ok(
            rid,
            {
                "path": rel,
                "size": size,
                "content": raw.decode("utf-8", errors="replace"),
                "truncated": size > _TAKYON_MAX_FILE_READ_BYTES,
            },
        )
    except Exception as e:
        return _err(rid, 5049, str(e))


@method("takyon.file.media")
def _(rid, params: dict) -> dict:
    session = _takyon_session(params)
    if session is None:
        return _err(rid, 4001, "session not found")
    business = _takyon_requested_business(session, params)
    access_error = _takyon_require_business_access(session, business)
    if access_error:
        return _err(rid, 4004, access_error)
    path = str(params.get("path") or "").strip()
    if not path:
        return _err(rid, 4004, "path required")
    try:
        store = _takyon_store(session)
        file_path = store._resolve_business_file(business, path)
        if not file_path.exists() or not file_path.is_file():
            return _err(rid, 4044, f"file not found: {path}")
        suffix = file_path.suffix.lower()
        if suffix not in _TAKYON_MEDIA_SUFFIXES:
            return _err(rid, 4004, "file is not a supported media asset")
        size = file_path.stat().st_size
        if size > _TAKYON_MAX_MEDIA_BYTES:
            return _err(rid, 4130, f"media asset is too large to preview inline: {size} bytes")
        mime = mimetypes.guess_type(str(file_path))[0] or ("video/mp4" if suffix in _TAKYON_VIDEO_SUFFIXES else "image/png")
        encoded = base64.b64encode(file_path.read_bytes()).decode("ascii")
        return _ok(
            rid,
            {
                "path": path,
                "media_type": mime,
                "size": size,
                "url": f"data:{mime};base64,{encoded}",
            },
        )
    except Exception as e:
        return _err(rid, 5047, str(e))


@method("takyon.site.preview")
def _(rid, params: dict) -> dict:
    session = _takyon_session(params)
    if session is None:
        return _err(rid, 4001, "session not found")
    business = _takyon_requested_business(session, params)
    access_error = _takyon_require_business_access(session, business)
    if access_error:
        return _err(rid, 4004, access_error)
    requested_path = str(params.get("path") or "").strip()
    try:
        store = _takyon_store(session)
        if not requested_path:
            overview = _takyon_business_overview_payload(store, business)
            artifacts = overview.get("artifacts") if isinstance(overview, dict) else {}
            website = artifacts.get("website") if isinstance(artifacts, dict) else {}
            product = overview.get("product") if isinstance(overview, dict) else {}
            requested_path = (
                (website or {}).get("path")
                or (product or {}).get("source_path")
                or "product/site"
            )
        candidate = store._resolve_business_file(business, requested_path)
        if candidate.is_dir() or not candidate.suffix:
            candidate = candidate / "index.html"
        if not candidate.exists() or not candidate.is_file():
            return _err(rid, 4044, f"site preview not found: {requested_path}")
        if candidate.name != "index.html" and candidate.suffix.lower() != ".html":
            return _err(rid, 4004, "site preview requires an HTML file or site directory")
        size = candidate.stat().st_size
        if size > _TAKYON_MAX_SITE_PREVIEW_BYTES:
            return _err(rid, 4130, f"site preview is too large: {size} bytes")
        business_root = store._business_root(business)
        source_root = (business_root / "product/site").resolve()
        candidate_resolved = candidate.resolve()
        html_text = _takyon_inline_static_site(
            candidate,
            site_root=source_root if source_root in (candidate_resolved, *candidate_resolved.parents) else None,
        )
        encoded = base64.b64encode(html_text.encode("utf-8")).decode("ascii")
        rel = str(candidate.relative_to(store._business_root(business)))
        return _ok(
            rid,
            {
                "path": rel,
                "size": len(html_text.encode("utf-8")),
                "url": f"data:text/html;charset=utf-8;base64,{encoded}",
            },
        )
    except Exception as e:
        return _err(rid, 5048, str(e))


@method("takyon.outputs.list")
def _(rid, params: dict) -> dict:
    session = _takyon_session(params)
    if session is None:
        return _err(rid, 4001, "session not found")
    business = _takyon_requested_business(session, params)
    if not business:
        return _ok(rid, {"outputs": [], **_takyon_scope_payload(session)})
    access_error = _takyon_require_business_access(session, business)
    if access_error:
        return _err(rid, 4004, access_error)
    try:
        outputs = _takyon_historical_outputs_payload(
            _takyon_store(session),
            business,
            limit=int(params.get("limit") or 40),
        )
        return _ok(rid, {"outputs": outputs, **_takyon_scope_payload(session)})
    except Exception as e:
        return _err(rid, 5046, str(e))


@method("takyon.dashboard.workspace")
def _(rid, params: dict) -> dict:
    session = _takyon_session(params)
    if session is None:
        return _err(rid, 4001, "session not found")
    business = _takyon_requested_business(session, params)
    view = str(params.get("view") or "full").strip().lower()
    if not business:
        return _ok(
            rid,
            {
                "business_slug": "",
                "current": {},
                "overview": {},
                "outputs": [],
                "background_run": None,
            },
        )
    access_error = _takyon_require_business_access(session, business)
    if access_error:
        return _err(rid, 4004, access_error)
    try:
        return _ok(
            rid,
            _takyon_workspace_payload(
                session,
                business,
                output_limit=int(params.get("limit") or 50),
                view=view,
            ),
        )
    except Exception as e:
        return _err(rid, 5050, str(e))


@method("takyon.dashboard.runtime")
def _(rid, params: dict) -> dict:
    session = _takyon_session(params)
    if session is None:
        return _err(rid, 4001, "session not found")
    business = _takyon_requested_business(session, params)
    if not business:
        return _ok(rid, {"business_slug": "", "events": [], "cursor": "", "after": ""})
    access_error = _takyon_require_business_access(session, business)
    if access_error:
        return _err(rid, 4004, access_error)
    requested_after = str(params.get("after") or "").strip()
    after_created_at, after_event_id = _takyon_parse_runtime_event_cursor(requested_after)
    try:
        limit = max(1, min(int(params.get("limit") or 24), 100))
    except (TypeError, ValueError):
        limit = 24
    try:
        store = _takyon_store(session)
        with store._connect() as conn:
            if after_created_at or after_event_id:
                rows = conn.execute(
                    """
                    SELECT * FROM events
                    WHERE business_slug = ? AND event_type LIKE 'dashboard.run.%'
                      AND (
                        created_at > ?
                        OR (created_at = ? AND id > ?)
                      )
                    ORDER BY created_at ASC, id ASC
                    LIMIT ?
                    """,
                    (business, after_created_at, after_created_at, after_event_id, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT * FROM (
                      SELECT * FROM events
                      WHERE business_slug = ? AND event_type LIKE 'dashboard.run.%'
                      ORDER BY created_at DESC, id DESC
                      LIMIT ?
                    ) recent
                    ORDER BY created_at ASC, id ASC
                    """,
                    (business, limit),
                ).fetchall()
        events: list[dict[str, Any]] = []
        cursor = requested_after
        for row in rows:
            event = store._row_to_dict(row)
            if not isinstance(event, dict):
                continue
            payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
            trace = payload.get("trace") if isinstance(payload.get("trace"), dict) else {}
            created_at = str(event.get("created_at") or "").strip()
            event_id = str(event.get("id") or "").strip()
            cursor_value = _takyon_runtime_event_cursor(created_at, event_id)
            if cursor_value:
                cursor = cursor_value
            events.append(
                {
                    "id": event_id,
                    "created_at": created_at,
                    "kind": str(payload.get("kind") or "").strip(),
                    "status": str(payload.get("status") or event.get("event_type") or "")
                    .replace("dashboard.run.", "")
                    .strip(),
                    "detail": str(payload.get("detail") or "").strip(),
                    "line": str(payload.get("line") or "").strip(),
                    "command": str(payload.get("command") or "").strip(),
                    "trace": (
                        {
                            "entry_key": str(trace.get("entry_key") or "").strip(),
                            "kind": str(trace.get("kind") or "").strip(),
                            "label": str(trace.get("label") or "").strip(),
                            "detail": str(trace.get("detail") or "").strip(),
                            "status": str(trace.get("status") or "").strip(),
                            "tool_name": str(trace.get("tool_name") or "").strip(),
                            "skill_name": str(trace.get("skill_name") or "").strip(),
                            "preview": str(trace.get("preview") or "").strip(),
                            "summary": str(trace.get("summary") or "").strip(),
                        }
                        if trace
                        else None
                    ),
                }
            )
        return _ok(
            rid,
            {
                "business_slug": business,
                "events": events,
                "cursor": cursor,
                "after": requested_after,
            },
        )
    except Exception as e:
        return _err(rid, 5053, str(e))


@method("takyon.dashboard.state")
def _(rid, params: dict) -> dict:
    session = _takyon_session(params)
    if session is None:
        return _err(rid, 4001, "session not found")
    explicit_business = "business_slug" in params or "business" in params
    business = _takyon_requested_business(session, params)
    view = str(params.get("view") or "full").strip().lower()
    try:
        return _ok(
            rid,
            _takyon_dashboard_state_payload(
                session,
                explicit_business=explicit_business,
                business=business,
                output_limit=int(params.get("limit") or 50),
                view=view,
            ),
        )
    except PermissionError as e:
        return _err(rid, 4041, str(e))
    except Exception as e:
        return _err(rid, 5052, str(e))


@method("takyon.dashboard.create")
def _(rid, params: dict) -> dict:
    session = _takyon_session(params)
    if session is None:
        return _err(rid, 4001, "session not found")
    started_stream = False
    try:
        takyon_cli = importlib.import_module("plugins.takyon.cli")

        sid = str(params.get("session_id") or "").strip()
        requested_name = str(params.get("name") or params.get("business_name") or "").strip()
        requested_goal = str(params.get("goal") or "").strip()
        requested_mode = str(params.get("mode") or "live").strip().lower()
        bootstrap_enabled = _coerce_bool(params.get("bootstrap"), default=True)
        if requested_mode == "test":
            return _err(rid, 4004, "test mode is disabled; all businesses run live")
        if requested_mode != "live":
            return _err(rid, 4004, "mode must be live")
        if not bootstrap_enabled and not _takyon_is_skill_lab_host(params, session=session):
            return _err(
                rid,
                4048,
                "bootstrap=false is available only on skills.fourmanifold.com",
            )
        can_stream_bootstrap = bool(
            session.get("agent") is not None or session.get("agent_ready") is not None
        )
        history_lock = session.get("history_lock")
        if can_stream_bootstrap:
            if history_lock is None:
                history_lock = threading.Lock()
                session["history_lock"] = history_lock
            with history_lock:
                if session.get("running"):
                    return _err(rid, 4009, "session busy")
                session["running"] = True
        TakyonStore = takyon_cli.TakyonStore
        resolve_dashboard_create_identity = takyon_cli._resolve_dashboard_create_identity
        run_takyon_command = takyon_cli.run_takyon_command
        read_model_config = getattr(takyon_cli, "_read_model_config", lambda _store: {})
        build_bootstrap_turn = getattr(takyon_cli, "_ceo_bootstrap_turn_config", None)
        operator_user_id = _takyon_operator_user_id(session) or None
        # GOAL_RULES §3 gap #2: zero-balance company-creation preflight. Bootstrapping a company
        # spends real operator money, so refuse BEFORE any business row, identity resolution, or
        # bootstrap work when the operator has no spendable balance. Raises
        # InsufficientOperatorBalance (mapped to 4030 below); fail-open only for identity-less/dev.
        takyon_cli._operator_create_balance_preflight(operator_user_id)
        resolved_name, slug = resolve_dashboard_create_identity(
            requested_name,
            requested_goal,
            str(params.get("slug") or params.get("business") or "").strip(),
            operator_user_id=operator_user_id,
        )

        store = TakyonStore(operator_user_id=operator_user_id)
        command_argv = ["create", "--live"]
        if can_stream_bootstrap or not bootstrap_enabled:
            command_argv.append("--no-auto")
        if resolved_name:
            command_argv.extend(["--name", resolved_name])
        command_argv.append(slug)
        if requested_goal:
            command_argv.append(requested_goal)
        command_result = run_takyon_command(
            command_argv,
            model=os.getenv("TAKYON_MODEL", ""),
            max_turns=int(os.getenv("TAKYON_MAX_TURNS", "30") or 30),
            show_activity=False,
            show_indicator=True,
            shell_history=None,
            operator_user_id=operator_user_id,
        )
        create_summary = _takyon_require_durable_business(
            store,
            slug,
            context="dashboard create",
            command_result=command_result,
        )
        active_mode = "live"
        config = read_model_config(store)
        schedule = str(config.get("default_ceo_schedule") or "every 6h").strip() or "every 6h"
        _takyon_invalidate_businesses_cache(session)
        session["takyon_current_business"] = slug
        workspace = _takyon_workspace_payload(
            session,
            slug,
            output_limit=int(params.get("limit") or 50),
            view="boot",
        )
        current = dict(workspace.get("current") or {})
        if not current and isinstance(create_summary, dict):
            current = _takyon_business_payload_from_summary(create_summary) or {}
        if resolved_name and not str(current.get("name") or "").strip():
            current["name"] = resolved_name
        if slug and not str(current.get("slug") or "").strip():
            current["slug"] = slug
        if active_mode and not str(current.get("mode") or "").strip():
            current["mode"] = active_mode

        if not bootstrap_enabled:
            session.pop("takyon_pending_business_create", None)
            session.pop("takyon_pending_business_create_at", None)
            session.pop("takyon_background_run", None)
            return _ok(
                rid,
                {
                    "business_slug": slug,
                    "business_name": resolved_name,
                    "goal": requested_goal,
                    "mode": active_mode,
                    "job_id": "",
                    "job_kind": "",
                    "job_status": "",
                    "lifecycle_state": "ready",
                    "scope": f"business:{slug}",
                    "current": current,
                    "overview": workspace.get("overview") or {},
                    "outputs": workspace.get("outputs") or [],
                    "deliverables": workspace.get("deliverables") or [],
                    "background_run": None,
                    "live_state": workspace.get("live_state") or {},
                    "businesses": _takyon_businesses_for_session(session, store=_takyon_store(session)),
                    "dev_mode": True,
                },
            )

        session["takyon_pending_business_create"] = True
        session["takyon_pending_business_create_at"] = time.time()

        if not can_stream_bootstrap:
            bootstrap_job = (
                command_result.get("bootstrap_job") or {}
                if isinstance(command_result, dict)
                else {}
            )
            if bootstrap_job:
                session["takyon_background_run"] = {
                    "kind": "create",
                    "business": slug,
                    "status": str(bootstrap_job.get("status") or "queued"),
                    "started_at": time.time(),
                    "detail": "Queued CEO bootstrap job.",
                    "job_id": str(bootstrap_job.get("job_id") or ""),
                }
                _takyon_set_background_run(slug, session["takyon_background_run"])
            return _ok(
                rid,
                {
                    "business_slug": slug,
                    "business_name": resolved_name,
                    "goal": requested_goal,
                    "mode": active_mode,
                    "job_id": str(bootstrap_job.get("job_id") or ""),
                    "job_kind": str(bootstrap_job.get("kind") or "ceo_bootstrap"),
                    "job_status": str(bootstrap_job.get("status") or "queued"),
                    "lifecycle_state": "queued" if bootstrap_job else "ready",
                    "scope": f"business:{slug}",
                    "current": current,
                    "overview": workspace.get("overview") or {},
                    "outputs": workspace.get("outputs") or [],
                    "deliverables": workspace.get("deliverables") or [],
                    "background_run": workspace.get("background_run") or session.get("takyon_background_run"),
                    "live_state": workspace.get("live_state") or {},
                    "businesses": _takyon_businesses_for_session(session, store=_takyon_store(session)),
                },
            )

        session.pop("takyon_background_run", None)
        if callable(build_bootstrap_turn):
            bootstrap_turn = build_bootstrap_turn(
                slug,
                requested_goal,
                active_mode,
                business_name=resolved_name,
            )
        else:
            bootstrap_turn = {
                "user_prompt": requested_goal or f"Bootstrap business:{slug} now.",
                "ephemeral_system_prompt": "",
                "enabled_toolsets": ["takyon", "web", "skills"],
                "disabled_toolsets": [],
                "load_soul_identity": False,
                "skip_memory": True,
                "skip_context_files": True,
                "max_turns": 20,
            }
        bootstrap_user_prompt = str(bootstrap_turn.get("user_prompt") or "")
        bootstrap_agent_config = {
            "enabled_toolsets": list(bootstrap_turn.get("enabled_toolsets") or []),
            "disabled_toolsets": list(bootstrap_turn.get("disabled_toolsets") or []),
            "ephemeral_system_prompt": str(
                bootstrap_turn.get("ephemeral_system_prompt") or ""
            )
            or None,
            "load_soul_identity": bool(bootstrap_turn.get("load_soul_identity")),
            "skip_memory": bool(bootstrap_turn.get("skip_memory")),
            "skip_context_files": bool(bootstrap_turn.get("skip_context_files")),
        }
        try:
            bootstrap_max_turns = int(bootstrap_turn.get("max_turns") or 20)
        except (TypeError, ValueError):
            bootstrap_max_turns = 20

        def _finalize_bootstrap() -> str | None:
            from plugins.takyon.worker import _refresh_business_surface_after_bootstrap

            warning_parts: list[str] = []
            try:
                try:
                    _takyon_require_durable_business(
                        store,
                        slug,
                        context="bootstrap finalization",
                    )
                except Exception as exc:
                    logger.warning(
                        "bootstrap finalization skipped for business:%s: %s",
                        slug,
                        exc,
                    )
                    return str(exc)
                surface_refresh = _refresh_business_surface_after_bootstrap(
                    slug,
                    job_id=f"session:{session.get('session_key') or sid or slug}",
                    operator_user_id=operator_user_id,
                )
                if isinstance(surface_refresh, dict):
                    publish = (
                        surface_refresh.get("publish")
                        if isinstance(surface_refresh.get("publish"), dict)
                        else {}
                    )
                    publish_status = str(
                        publish.get("status") or surface_refresh.get("status") or ""
                    ).strip()
                    publish_blocker = str(
                        publish.get("blocker")
                        or surface_refresh.get("blocker")
                        or surface_refresh.get("error")
                        or ""
                    ).strip()
                    if publish_status and publish_status != "published" and publish_blocker:
                        warning_parts.append(
                            f"Product surface: {publish_status} - {publish_blocker}"
                        )
                if schedule:
                    store.commit(
                        scope=f"business:{slug}",
                        operations=[
                            {
                                "action": "cron.ensure_ceo_wakeup",
                                "business": slug,
                                "schedule": schedule,
                                "defer_first_run": True,
                            }
                        ],
                        idempotency_key=(
                            f"session-bootstrap-wake:{session.get('session_key') or sid or slug}:"
                            f"{slug}:{schedule}"
                        ),
                        reason="bootstrap completed and enabled CEO wake loop",
                        actor="worker",
                    )
                return "\n".join(part for part in warning_parts if part)
            finally:
                _reset_session_history_for_post_bootstrap_chat(sid, session)

        _start_streaming_session_turn(
            rid,
            sid,
            session,
            bootstrap_user_prompt,
            record_user_history=False,
            max_iterations_override=max(1, bootstrap_max_turns),
            agent_config_overrides=bootstrap_agent_config,
            post_complete_callback=_finalize_bootstrap,
            start_delay_ms=125,
        )
        started_stream = True
        return _ok(
            rid,
            {
                "business_slug": slug,
                "business_name": resolved_name,
                "goal": requested_goal,
                "mode": active_mode,
                "job_id": "",
                "job_kind": "ceo_bootstrap",
                "job_status": "streaming",
                "lifecycle_state": "streaming",
                "streaming": True,
                "output": f"Create started for business:{slug}",
                "scope": f"business:{slug}",
                "current": current,
                "overview": workspace.get("overview") or {},
                "outputs": workspace.get("outputs") or [],
                "deliverables": workspace.get("deliverables") or [],
                "background_run": workspace.get("background_run"),
                "live_state": workspace.get("live_state") or {},
                "businesses": _takyon_businesses_for_session(session, store=_takyon_store(session)),
            },
        )
    except SystemExit as e:
        if not started_stream:
            history_lock = session.get("history_lock")
            if history_lock is not None:
                with history_lock:
                    session["running"] = False
        return _err(rid, 4004, str(e))
    except _insufficient_operator_balance_cls() as e:
        # GOAL_RULES §3 gap #2: clean balance-block. Map to 4030 (insufficient operator balance) so
        # the dashboard nudges the user to add credits instead of showing a generic create failure.
        # No business was created (the preflight runs before any write).
        if not started_stream:
            history_lock = session.get("history_lock")
            if history_lock is not None:
                with history_lock:
                    session["running"] = False
        return _err(rid, 4030, str(e))
    except Exception as e:
        if not started_stream:
            history_lock = session.get("history_lock")
            if history_lock is not None:
                with history_lock:
                    session["running"] = False
        return _err(rid, 5051, str(e))


@method("takyon.shell.exec")
def _(rid, params: dict) -> dict:
    session = _takyon_session(params)
    if session is None:
        return _err(rid, 4001, "session not found")
    sid = str(params.get("session_id") or "")
    line = str(params.get("line") or "").strip()
    if not line:
        return _err(rid, 4004, "empty command")

    try:
        from plugins.takyon.cli import (
            TakyonStore,
            _handle_shell_line,
            _record_shell_turn,
        )

        history = session.setdefault("takyon_shell_history", [])
        current_business = str(session.get("takyon_current_business") or "") or None
        operator_user_id = _takyon_operator_user_id(session) or None
        detached_target = _takyon_detached_shell_target(line, current_business)
        if detached_target:
            detached_kind, target_business, detached_line = detached_target
            if detached_kind == "create" and target_business:
                _takyon_invalidate_businesses_cache(session)
                create_store = TakyonStore(operator_user_id=operator_user_id)
                try:
                    existing = create_store.read(scope="global", query="list_businesses", limit=200)
                    session["takyon_businesses_before_prompt"] = sorted(
                        _takyon_business_slugs(existing.get("businesses"))
                    )
                except Exception:
                    session["takyon_businesses_before_prompt"] = list(
                        session.get("takyon_known_businesses") or []
                    )
                session["takyon_pending_business_create"] = True
                session["takyon_pending_business_create_at"] = time.time()
            if target_business and detached_kind != "create":
                access_error = _takyon_require_business_access(session, target_business)
                if access_error:
                    return _err(rid, 4041, access_error)
            background_run = {
                "kind": detached_kind,
                "business": target_business,
                "status": "running",
                "started_at": time.time(),
                "detail": f"Running {detached_line}",
            }
            session["takyon_background_run"] = background_run
            _takyon_set_background_run(target_business, background_run)
            if sid:
                _emit(
                    "status.update",
                    sid,
                    {
                        "kind": "takyon",
                        "text": f"Started {detached_line} for business:{target_business}.",
                    },
                )

            def run_detached() -> None:
                output = ""
                output_lines: list[str] = []
                command_text = detached_line
                started_at = float(session.get("takyon_background_run", {}).get("started_at") or time.time())
                _takyon_record_runtime_event(
                    target_business,
                    kind=detached_kind,
                    status="started",
                    detail=f"Started {detached_line}",
                    command=command_text,
                )
                try:
                    raw = detached_line.strip().lstrip("/")
                    proc = subprocess.Popen(
                        [
                            sys.executable,
                            "-c",
                            "from plugins.takyon.cli import main; main()",
                            *shlex.split(raw),
                        ],
                        cwd=str(Path(__file__).resolve().parents[1]),
                        env={
                            **os.environ.copy(),
                            **(
                                # Per-session identity propagation into the detached child — the
                                # session var, never the legacy process-global operator var (which
                                # per-session planes ignore; see core.operator_identity_mode).
                                {"TAKYON_SESSION_USER_ID": operator_user_id}
                                if operator_user_id
                                else {}
                            ),
                        },
                        text=True,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT,
                        bufsize=1,
                    )
                    line_queue: queue.Queue[str | None] = queue.Queue()

                    def read_stdout() -> None:
                        try:
                            assert proc.stdout is not None
                            for raw_line in proc.stdout:
                                line_queue.put(raw_line)
                        finally:
                            line_queue.put(None)

                    threading.Thread(
                        target=read_stdout,
                        name=f"takyon-{detached_kind}-{target_business or 'global'}-stdout",
                        daemon=True,
                    ).start()
                    timeout_seconds = int(os.getenv("TAKYON_BACKGROUND_TIMEOUT_S", "3600") or 3600)
                    deadline = time.time() + timeout_seconds
                    stdout_done = False
                    heartbeat_seconds = max(2, int(os.getenv("TAKYON_BACKGROUND_HEARTBEAT_S", "5") or 5))
                    last_heartbeat = 0.0
                    last_output_event = started_at
                    while not stdout_done or proc.poll() is None:
                        if time.time() > deadline:
                            proc.kill()
                            raise TimeoutError(f"background {detached_kind} exceeded {timeout_seconds}s")
                        try:
                            item = line_queue.get(timeout=2.0)
                        except queue.Empty:
                            if time.time() - last_heartbeat >= heartbeat_seconds:
                                elapsed = max(0, int(time.time() - started_at))
                                heartbeat = f"Running {detached_line} · {elapsed}s"
                                if time.time() - last_output_event >= 20:
                                    _takyon_record_runtime_event(
                                        target_business,
                                        kind=detached_kind,
                                        status="heartbeat",
                                        detail=heartbeat,
                                        command=command_text,
                                    )
                                if sid:
                                    _emit(
                                        "status.update",
                                        sid,
                                        {"kind": "takyon", "text": heartbeat},
                                    )
                                last_heartbeat = time.time()
                            continue
                        if item is None:
                            stdout_done = True
                            continue
                        output_lines.append(item.rstrip("\n"))
                        clean_line = _takyon_clean_runtime_line(item)
                        if not clean_line:
                            continue
                        last_output_event = time.time()
                        background_run = {
                            "kind": detached_kind,
                            "business": target_business,
                            "status": "running",
                            "started_at": started_at,
                            "detail": clean_line,
                        }
                        session["takyon_background_run"] = background_run
                        _takyon_set_background_run(target_business, background_run)
                        _takyon_record_runtime_event(
                            target_business,
                            kind=detached_kind,
                            status="output",
                            detail=clean_line,
                            line=clean_line,
                            command=command_text,
                        )
                        if sid:
                            _emit(
                                "status.update",
                                sid,
                                {"kind": "takyon", "text": clean_line},
                            )
                    returncode = proc.wait(timeout=5)
                    output = "\n".join(output_lines).strip()
                    status = "done" if returncode == 0 else "error"
                    detail = "Completed." if returncode == 0 else f"Exited {returncode}."
                    if returncode != 0 and output:
                        detail = output.splitlines()[-1][:240]
                    elif output:
                        detail = output.splitlines()[-1][:240]
                    next_business = target_business
                except Exception as exc:
                    output = f"Takyon background {detached_kind} failed: {exc}"
                    status = "error"
                    detail = str(exc)
                    next_business = target_business or current_business
                _takyon_record_runtime_event(
                    next_business or target_business or "",
                    kind=detached_kind,
                    status="completed" if status == "done" else "failed",
                    detail=detail,
                    command=command_text,
                )
                if sid:
                    _emit(
                        "status.update",
                        sid,
                        {
                            "kind": "takyon",
                            "text": f"{detached_kind.capitalize()} for business:{next_business or target_business} {status}. {detail}",
                        },
                    )
                session["takyon_current_business"] = next_business or ""
                background_result = {
                    "kind": detached_kind,
                    "business": next_business or target_business or "",
                    "status": status,
                    "started_at": session.get("takyon_background_run", {}).get("started_at", time.time()),
                    "finished_at": time.time(),
                    "detail": detail,
                }
                session["takyon_background_run"] = background_result
                _takyon_set_background_run(next_business or target_business or "", background_result)
                if isinstance(history, list):
                    _record_shell_turn(history, detached_line, output)

            threading.Thread(
                target=run_detached,
                name=f"takyon-{detached_kind}-{target_business or current_business or 'global'}",
                daemon=True,
            ).start()
            message = (
                (
                    f"Create started for business:{target_business}. Refresh status or open the business after a moment "
                    "to see files, blockers, and deliverables."
                )
                if detached_kind == "create"
                else (
                    f"Wake started for business:{target_business}. Refresh status or open the business after a moment "
                    "to see files, blockers, and deliverables."
                )
            )
            result: dict[str, Any] = {
                "output": message,
                **_takyon_scope_payload(session),
            }
            return _ok(
                rid,
                result,
            )
        output, next_business = _handle_shell_line(
            line,
            current_business=current_business,
            store=TakyonStore(operator_user_id=operator_user_id),
            model=os.getenv("TAKYON_MODEL", ""),
            max_turns=int(os.getenv("TAKYON_MAX_TURNS", "30") or 30),
            shell_history=history if isinstance(history, list) else None,
            operator_user_id=operator_user_id,
        )
        session["takyon_current_business"] = next_business or ""
        if isinstance(history, list):
            _record_shell_turn(history, line, output)
        return _ok(
            rid,
            {
                "output": output,
                **_takyon_scope_payload(session),
            },
        )
    except SystemExit as e:
        return _ok(rid, {"output": str(e), **_takyon_scope_payload(session)})
    except Exception as e:
        return _err(rid, 5043, str(e))


@method("takyon.prompt.context")
def _(rid, params: dict) -> dict:
    session = _takyon_session(params)
    if session is None:
        return _err(rid, 4001, "session not found")
    text = str(params.get("text") or "")
    try:
        prompt_text = _build_takyon_prompt_text(
            session,
            text,
            create_in_test_mode=bool(params.get("create_in_test_mode")),
        )
        return _ok(
            rid,
            {"text": prompt_text},
        )
    except Exception as e:
        return _err(rid, 5044, str(e))


@method("takyon.skill_lab.catalog")
def _(rid, params: dict) -> dict:
    if not _takyon_is_skill_lab_host(params):
        return _err(rid, 4047, "Skill Lab is available only on skills.fourmanifold.com")
    try:
        return _ok(rid, {"skills": _takyon_skill_lab_catalog()})
    except Exception as e:
        return _err(rid, 5044, str(e))


@method("command.resolve")
def _(rid, params: dict) -> dict:
    try:
        from takyon_cli.commands import resolve_command

        r = resolve_command(params.get("name", ""))
        if r:
            return _ok(
                rid,
                {
                    "canonical": r.name,
                    "description": r.description,
                    "category": r.category,
                },
            )
        return _err(rid, 4011, f"unknown command: {params.get('name')}")
    except Exception as e:
        return _err(rid, 5012, str(e))


def _resolve_name(name: str) -> str:
    try:
        from takyon_cli.commands import resolve_command

        r = resolve_command(name)
        return r.name if r else name
    except Exception:
        return name


@method("command.dispatch")
def _(rid, params: dict) -> dict:
    name, arg = params.get("name", "").lstrip("/"), params.get("arg", "")
    resolved = _resolve_name(name)
    if resolved != name:
        name = resolved
    session = _sessions.get(params.get("session_id", ""))

    qcmds = _load_cfg().get("quick_commands", {})
    if name in qcmds:
        qc = qcmds[name]
        if qc.get("type") == "exec":
            r = subprocess.run(
                qc.get("command", ""),
                shell=True,
                capture_output=True,
                text=True,
                timeout=30,
            )
            output = (
                (r.stdout or "")
                + ("\n" if r.stdout and r.stderr else "")
                + (r.stderr or "")
            ).strip()[:4000]
            if r.returncode != 0:
                return _err(
                    rid,
                    4018,
                    output or f"quick command failed with exit code {r.returncode}",
                )
            return _ok(rid, {"type": "exec", "output": output})
        if qc.get("type") == "alias":
            return _ok(rid, {"type": "alias", "target": qc.get("target", "")})

    try:
        from takyon_cli.plugins import (
            get_plugin_command_handler,
            resolve_plugin_command_result,
        )

        handler = get_plugin_command_handler(name)
        if handler:
            result = resolve_plugin_command_result(handler(arg))
            return _ok(rid, {"type": "plugin", "output": str(result or "")})
    except Exception:
        pass

    try:
        from agent.skill_commands import (
            scan_skill_commands,
            build_skill_invocation_message,
        )

        cmds = scan_skill_commands()
        key = f"/{name}"
        if key in cmds:
            msg = build_skill_invocation_message(
                key, arg, task_id=session.get("session_key", "") if session else ""
            )
            if msg:
                return _ok(
                    rid,
                    {
                        "type": "skill",
                        "message": msg,
                        "name": cmds[key].get("name", name),
                    },
                )
    except Exception:
        pass

    # ── Commands that queue messages onto _pending_input in the CLI ───
    # In the TUI the slash worker subprocess has no reader for that queue,
    # so we handle them here and return a structured payload.

    if name in {"queue", "q"}:
        if not arg:
            return _err(rid, 4004, "usage: /queue <prompt>")
        return _ok(rid, {"type": "send", "message": arg})

    if name == "retry":
        if not session:
            return _err(rid, 4001, "no active session to retry")
        if session.get("running"):
            return _err(
                rid, 4009, "session busy — /interrupt the current turn before /retry"
            )
        history = session.get("history", [])
        if not history:
            return _err(rid, 4018, "no previous user message to retry")
        # Walk backwards to find the last user message
        last_user_idx = None
        for i in range(len(history) - 1, -1, -1):
            if history[i].get("role") == "user":
                last_user_idx = i
                break
        if last_user_idx is None:
            return _err(rid, 4018, "no previous user message to retry")
        content = history[last_user_idx].get("content", "")
        if isinstance(content, list):
            content = " ".join(
                p.get("text", "")
                for p in content
                if isinstance(p, dict) and p.get("type") == "text"
            )
        if not content:
            return _err(rid, 4018, "last user message is empty")
        # Truncate history: remove everything from the last user message onward
        # (mirrors CLI retry_last() which strips the failed exchange)
        with session["history_lock"]:
            session["history"] = history[:last_user_idx]
            session["history_version"] = int(session.get("history_version", 0)) + 1
        return _ok(rid, {"type": "send", "message": content})

    if name == "steer":
        if not arg:
            return _err(rid, 4004, "usage: /steer <prompt>")
        agent = session.get("agent") if session else None
        if agent and hasattr(agent, "steer"):
            try:
                accepted = agent.steer(arg)
                if accepted:
                    return _ok(
                        rid,
                        {
                            "type": "exec",
                            "output": f"⏩ Steer queued — arrives after the next tool call: {arg[:80]}{'...' if len(arg) > 80 else ''}",
                        },
                    )
            except Exception:
                pass
        # Fallback: no active run, treat as next-turn message
        return _ok(rid, {"type": "send", "message": arg})

    if name == "goal":
        if not session:
            return _err(rid, 4001, "no active session")
        try:
            from takyon_cli.goals import GoalManager
        except Exception as exc:
            return _err(rid, 5030, f"goals unavailable: {exc}")

        sid_key = session.get("session_key") or ""
        if not sid_key:
            return _err(rid, 4001, "no session key")

        try:
            goals_cfg = _load_cfg().get("goals") or {}
            max_turns = int(goals_cfg.get("max_turns", 20) or 20)
        except Exception:
            max_turns = 20
        mgr = GoalManager(session_id=sid_key, default_max_turns=max_turns)

        lower = arg.strip().lower()
        if not arg.strip() or lower == "status":
            return _ok(rid, {"type": "exec", "output": mgr.status_line()})
        if lower == "pause":
            state = mgr.pause(reason="user-paused")
            out = "No goal set." if state is None else f"⏸ Goal paused: {state.goal}"
            return _ok(rid, {"type": "exec", "output": out})
        if lower == "resume":
            state = mgr.resume()
            if state is None:
                return _ok(rid, {"type": "exec", "output": "No goal to resume."})
            return _ok(
                rid,
                {
                    "type": "exec",
                    "output": (
                        f"▶ Goal resumed: {state.goal}\n"
                        "Send any message to continue, or wait — I'll take the next step on the next turn."
                    ),
                },
            )
        if lower in {"clear", "stop", "done"}:
            had = mgr.has_goal()
            mgr.clear()
            return _ok(
                rid,
                {
                    "type": "exec",
                    "output": "✓ Goal cleared." if had else "No active goal.",
                },
            )

        # Otherwise — treat the remaining text as the new goal.
        try:
            state = mgr.set(arg)
        except ValueError as exc:
            return _err(rid, 4004, f"invalid goal: {exc}")

        notice = (
            f"⊙ Goal set ({state.max_turns}-turn budget): {state.goal}\n"
            "I'll keep working until the goal is done, you pause/clear it, or the budget is exhausted.\n"
            "Controls: /goal status · /goal pause · /goal resume · /goal clear"
        )
        # Send the goal text as the kickoff prompt. The TUI client sees
        # {type: send, notice, message} → renders `notice` as a sys line,
        # then submits `message` as a user turn. The post-turn judge
        # wired in _run_prompt_submit takes over from there.
        return _ok(
            rid,
            {"type": "send", "notice": notice, "message": state.goal},
        )

    if name in {"snapshot", "snap"}:
        subcommand = arg.split(maxsplit=1)[0].lower() if arg else ""
        if subcommand in {"restore", "rewind"}:
            return _ok(
                rid,
                {
                    "type": "exec",
                    "output": (
                        "/snapshot restore is blocked in the TUI because it changes "
                        "config/state on disk while the live agent has cached settings. "
                        "Run it in the classic CLI, then restart the TUI."
                    ),
                },
            )

    return _err(rid, 4018, f"not a quick/plugin/skill command: {name}")


# ── Methods: paste ────────────────────────────────────────────────────

_paste_counter = 0


@method("paste.collapse")
def _(rid, params: dict) -> dict:
    global _paste_counter
    text = params.get("text", "")
    if not text:
        return _err(rid, 4004, "empty paste")

    _paste_counter += 1
    line_count = text.count("\n") + 1
    paste_dir = _takyon_home / "pastes"
    paste_dir.mkdir(parents=True, exist_ok=True)

    from datetime import datetime

    paste_file = (
        paste_dir / f"paste_{_paste_counter}_{datetime.now().strftime('%H%M%S')}.txt"
    )
    paste_file.write_text(text, encoding="utf-8")

    placeholder = (
        f"[Pasted text #{_paste_counter}: {line_count} lines \u2192 {paste_file}]"
    )
    return _ok(
        rid, {"placeholder": placeholder, "path": str(paste_file), "lines": line_count}
    )


# ── Methods: complete ─────────────────────────────────────────────────

_FUZZY_CACHE_TTL_S = 5.0
_FUZZY_CACHE_MAX_FILES = 20000
_FUZZY_FALLBACK_EXCLUDES = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        ".next",
        ".cache",
        ".venv",
        "venv",
        "node_modules",
        "__pycache__",
        "dist",
        "build",
        "target",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
    }
)
_fuzzy_cache_lock = threading.Lock()
_fuzzy_cache: dict[str, tuple[float, list[str]]] = {}


def _list_repo_files(root: str) -> list[str]:
    """Return file paths relative to ``root``.

    Uses ``git ls-files`` from the repo top (resolved via
    ``rev-parse --show-toplevel``) so the listing covers tracked + untracked
    files anywhere in the repo, then converts each path back to be relative
    to ``root``. Files outside ``root`` (parent directories of cwd, sibling
    subtrees) are excluded so the picker stays scoped to what's reachable
    from the gateway's cwd. Falls back to a bounded ``os.walk(root)`` when
    ``root`` isn't inside a git repo. Result cached per-root for
    ``_FUZZY_CACHE_TTL_S`` so rapid keystrokes don't respawn git processes.
    """
    now = time.monotonic()
    with _fuzzy_cache_lock:
        cached = _fuzzy_cache.get(root)
        if cached and now - cached[0] < _FUZZY_CACHE_TTL_S:
            return cached[1]

    files: list[str] = []
    try:
        top_result = subprocess.run(
            ["git", "-C", root, "rev-parse", "--show-toplevel"],
            capture_output=True,
            timeout=2.0,
            check=False,
        )
        if top_result.returncode == 0:
            top = top_result.stdout.decode("utf-8", "replace").strip()
            list_result = subprocess.run(
                [
                    "git",
                    "-C",
                    top,
                    "ls-files",
                    "-z",
                    "--cached",
                    "--others",
                    "--exclude-standard",
                ],
                capture_output=True,
                timeout=2.0,
                check=False,
            )
            if list_result.returncode == 0:
                for p in list_result.stdout.decode("utf-8", "replace").split("\0"):
                    if not p:
                        continue
                    rel = os.path.relpath(os.path.join(top, p), root).replace(
                        os.sep, "/"
                    )
                    # Skip parents/siblings of cwd — keep the picker scoped
                    # to root-and-below, matching Cmd-P workspace semantics.
                    if rel.startswith("../"):
                        continue
                    files.append(rel)
                    if len(files) >= _FUZZY_CACHE_MAX_FILES:
                        break
    except (OSError, subprocess.TimeoutExpired):
        pass

    if not files:
        # Fallback walk: skip vendor/build dirs + dot-dirs so the walk stays
        # tractable. Dotfiles themselves survive — the ranker decides based
        # on whether the query starts with `.`.
        try:
            for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
                dirnames[:] = [
                    d
                    for d in dirnames
                    if d not in _FUZZY_FALLBACK_EXCLUDES and not d.startswith(".")
                ]
                rel_dir = os.path.relpath(dirpath, root)
                for f in filenames:
                    rel = f if rel_dir == "." else f"{rel_dir}/{f}"
                    files.append(rel.replace(os.sep, "/"))
                    if len(files) >= _FUZZY_CACHE_MAX_FILES:
                        break
                if len(files) >= _FUZZY_CACHE_MAX_FILES:
                    break
        except OSError:
            pass

    with _fuzzy_cache_lock:
        _fuzzy_cache[root] = (now, files)

    return files


def _fuzzy_basename_rank(name: str, query: str) -> tuple[int, int] | None:
    """Rank ``name`` against ``query``; lower is better. Returns None to reject.

    Tiers (kind):
      0 — exact basename
      1 — basename prefix (e.g. `app` → `appChrome.tsx`)
      2 — word-boundary / camelCase hit (e.g. `chrome` → `appChrome.tsx`)
      3 — substring anywhere in basename
      4 — subsequence match (every query char appears in order)

    Secondary key is `len(name)` so shorter names win ties.
    """
    if not query:
        return (3, len(name))

    nl = name.lower()
    ql = query.lower()

    if nl == ql:
        return (0, len(name))

    if nl.startswith(ql):
        return (1, len(name))

    # Word-boundary split: `foo-bar_baz.qux` → ["foo","bar","baz","qux"].
    # camelCase split: `appChrome` → ["app","Chrome"]. Cheap approximation;
    # falls through to substring/subsequence if it misses.
    parts: list[str] = []
    buf = ""
    for ch in name:
        if ch in "-_." or (ch.isupper() and buf and not buf[-1].isupper()):
            if buf:
                parts.append(buf)
            buf = ch if ch not in "-_." else ""
        else:
            buf += ch
    if buf:
        parts.append(buf)
    for p in parts:
        if p.lower().startswith(ql):
            return (2, len(name))

    if ql in nl:
        return (3, len(name))

    i = 0
    for ch in nl:
        if ch == ql[i]:
            i += 1
            if i == len(ql):
                return (4, len(name))

    return None


@method("complete.path")
def _(rid, params: dict) -> dict:
    word = params.get("word", "")
    if not word:
        return _ok(rid, {"items": []})

    items: list[dict] = []
    try:
        is_context = word.startswith("@")
        query = word[1:] if is_context else word

        if is_context and not query:
            items = [
                {"text": "@diff", "display": "@diff", "meta": "git diff"},
                {"text": "@staged", "display": "@staged", "meta": "staged diff"},
                {"text": "@file:", "display": "@file:", "meta": "attach file"},
                {"text": "@folder:", "display": "@folder:", "meta": "attach folder"},
                {"text": "@url:", "display": "@url:", "meta": "fetch url"},
                {"text": "@git:", "display": "@git:", "meta": "git log"},
            ]
            return _ok(rid, {"items": items})

        # Accept both `@folder:path` and the bare `@folder` form so the user
        # sees directory listings as soon as they finish typing the keyword,
        # without first accepting the static `@folder:` hint.
        if is_context and query in {"file", "folder"}:
            prefix_tag, path_part = query, ""
        elif is_context and query.startswith(("file:", "folder:")):
            prefix_tag, _, tail = query.partition(":")
            path_part = tail
        else:
            prefix_tag = ""
            path_part = query if is_context else query

        # Fuzzy basename search across the repo when the user types a bare
        # name with no path separator — `@appChrome` surfaces every file
        # whose basename matches, regardless of directory depth. Matches what
        # editors like Cursor / VS Code do for Cmd-P. Path-ish queries (with
        # `/`, `./`, `~/`, `/abs`) fall through to the directory-listing
        # path so explicit navigation intent is preserved.
        if is_context and path_part and "/" not in path_part and prefix_tag != "folder":
            root = os.getcwd()
            ranked: list[tuple[tuple[int, int], str, str]] = []
            for rel in _list_repo_files(root):
                basename = os.path.basename(rel)
                if basename.startswith(".") and not path_part.startswith("."):
                    continue
                rank = _fuzzy_basename_rank(basename, path_part)
                if rank is None:
                    continue
                ranked.append((rank, rel, basename))

            ranked.sort(key=lambda r: (r[0], len(r[1]), r[1]))
            tag = prefix_tag or "file"
            for _, rel, basename in ranked[:30]:
                items.append(
                    {
                        "text": f"@{tag}:{rel}",
                        "display": basename,
                        "meta": os.path.dirname(rel),
                    }
                )

            return _ok(rid, {"items": items})

        expanded = _normalize_completion_path(path_part) if path_part else "."
        if expanded == "." or not expanded:
            search_dir, match = ".", ""
        elif expanded.endswith("/"):
            search_dir, match = expanded, ""
        else:
            search_dir = os.path.dirname(expanded) or "."
            match = os.path.basename(expanded)

        if not os.path.isdir(search_dir):
            return _ok(rid, {"items": []})

        want_dir = prefix_tag == "folder"
        match_lower = match.lower()
        for entry in sorted(os.listdir(search_dir)):
            if match and not entry.lower().startswith(match_lower):
                continue
            if is_context and not prefix_tag and entry.startswith("."):
                continue
            full = os.path.join(search_dir, entry)
            is_dir = os.path.isdir(full)
            # Explicit `@folder:` / `@file:` — honour the user's filter.  Skip
            # the opposite kind instead of auto-rewriting the completion tag,
            # which used to defeat the prefix and let `@folder:` list files.
            if prefix_tag and want_dir != is_dir:
                continue
            rel = os.path.relpath(full)
            suffix = "/" if is_dir else ""

            if is_context and prefix_tag:
                text = f"@{prefix_tag}:{rel}{suffix}"
            elif is_context:
                kind = "folder" if is_dir else "file"
                text = f"@{kind}:{rel}{suffix}"
            elif word.startswith("~"):
                text = "~/" + os.path.relpath(full, os.path.expanduser("~")) + suffix
            elif word.startswith("./"):
                text = "./" + rel + suffix
            else:
                text = rel + suffix

            items.append(
                {
                    "text": text,
                    "display": entry + suffix,
                    "meta": "dir" if is_dir else "",
                }
            )
            if len(items) >= 30:
                break
    except Exception as e:
        return _err(rid, 5021, str(e))

    return _ok(rid, {"items": items})


def _details_completion_item(value: str, meta: str = "") -> dict:
    return {"text": value, "display": value, "meta": meta}


def _details_root_completion_item(
    value: str, meta: str, needs_leading_space: bool
) -> dict:
    return _details_completion_item(
        f" {value}" if needs_leading_space else value,
        meta,
    )


def _details_completions(text: str) -> list[dict] | None:
    if not text.lower().startswith("/details"):
        return None

    stripped = text.strip()
    if stripped and not "/details".startswith(stripped.lower().split()[0]):
        return None

    body = text[len("/details") :]
    if body.startswith(" "):
        body = body[1:]
    parts = body.split()
    has_trailing_space = text.endswith(" ")
    sections = ("thinking", "tools", "subagents", "activity")
    modes = ("hidden", "collapsed", "expanded")

    if not body or (len(parts) == 0 and has_trailing_space):
        return [
            *[
                _details_root_completion_item(
                    mode, "global mode", not has_trailing_space
                )
                for mode in modes
            ],
            _details_root_completion_item(
                "cycle", "cycle global mode", not has_trailing_space
            ),
            *[
                _details_root_completion_item(
                    section, "section override", not has_trailing_space
                )
                for section in sections
            ],
        ]

    if len(parts) == 1 and not has_trailing_space:
        prefix = parts[0].lower()
        candidates = [*modes, "cycle", *sections]
        return [
            _details_completion_item(
                candidate,
                (
                    "section override"
                    if candidate in sections
                    else "cycle global mode" if candidate == "cycle" else "global mode"
                ),
            )
            for candidate in candidates
            if candidate.startswith(prefix) and candidate != prefix
        ]

    if len(parts) == 1 and has_trailing_space and parts[0].lower() in sections:
        return [
            *[
                _details_completion_item(mode, f"set {parts[0].lower()}")
                for mode in modes
            ],
            _details_completion_item("reset", f"clear {parts[0].lower()} override"),
        ]

    if len(parts) == 2 and not has_trailing_space and parts[0].lower() in sections:
        prefix = parts[1].lower()
        return [
            _details_completion_item(
                candidate,
                (
                    f"clear {parts[0].lower()} override"
                    if candidate == "reset"
                    else f"set {parts[0].lower()}"
                ),
            )
            for candidate in (*modes, "reset")
            if candidate.startswith(prefix) and candidate != prefix
        ]

    return []


@method("complete.slash")
def _(rid, params: dict) -> dict:
    text = params.get("text", "")
    if not text.startswith("/"):
        return _ok(rid, {"items": []})

    try:
        from takyon_cli.commands import SlashCommandCompleter
        from prompt_toolkit.document import Document
        from prompt_toolkit.formatted_text import to_plain_text

        from agent.skill_commands import get_skill_commands
        from agent.skill_bundles import get_skill_bundles

        completer = SlashCommandCompleter(
            skill_commands_provider=lambda: get_skill_commands(),
            skill_bundles_provider=lambda: get_skill_bundles(),
        )
        doc = Document(text, len(text))
        items = [
            {
                "text": c.text,
                "display": c.display or c.text,
                "meta": to_plain_text(c.display_meta) if c.display_meta else "",
            }
            for c in completer.get_completions(doc, None)
        ][:30]
        text_lower = text.lower()
        extras = [
            {
                "text": "/compact",
                "display": "/compact",
                "meta": "Toggle compact display mode",
            },
            {
                "text": "/details",
                "display": "/details",
                "meta": "Control agent detail visibility",
            },
            {
                "text": "/logs",
                "display": "/logs",
                "meta": "Show recent gateway log lines",
            },
            {
                "text": "/mouse",
                "display": "/mouse",
                "meta": "Toggle mouse/wheel tracking [on|off|toggle]",
            },
        ]
        for extra in extras:
            if extra["text"].startswith(text_lower) and not any(
                item["text"] == extra["text"] for item in items
            ):
                items.append(extra)

        details_items = _details_completions(text)
        if details_items is not None:
            return _ok(
                rid,
                {
                    "items": details_items,
                    "replace_from": text.rfind(" ") + 1 if " " in text else len(text),
                },
            )

        return _ok(
            rid,
            {"items": items, "replace_from": text.rfind(" ") + 1 if " " in text else 1},
        )
    except Exception as e:
        return _err(rid, 5020, str(e))


@method("model.options")
def _(rid, params: dict) -> dict:
    try:
        from takyon_cli.inventory import build_models_payload, load_picker_context

        session = _sessions.get(params.get("session_id", ""))
        agent = session.get("agent") if session else None
        # Layer agent-session state on top of disk config — once an agent
        # is spawned, IT owns the live provider/model/base_url. Empty
        # agent attributes must NOT clobber disk config (with_overrides
        # is truthy-only).
        ctx = load_picker_context().with_overrides(
            current_provider=getattr(agent, "provider", "") if agent else "",
            current_model=(
                (getattr(agent, "model", "") if agent else "") or _resolve_model()
            ),
            current_base_url=getattr(agent, "base_url", "") if agent else "",
        )
        # picker_hints + canonical_order produce the TUI's required shape:
        # `authenticated`/`auth_type`/`key_env`/`warning` per row, in
        # CANONICAL_PROVIDERS declaration order. include_unconfigured=True
        # so the picker can show the full provider universe (with the
        # setup-hint warning attached) instead of only authed rows.
        # Curated model lists are preserved — list_authenticated_providers
        # populates `models` from the curated catalog, not provider_model_ids
        # (which would pull non-agentic models like TTS/embeddings/etc.).
        payload = build_models_payload(
            ctx,
            include_unconfigured=True,
            picker_hints=True,
            canonical_order=True,
            max_models=50,
        )
        return _ok(rid, payload)
    except Exception as e:
        return _err(rid, 5033, str(e))


@method("model.save_key")
def _(rid, params: dict) -> dict:
    """Save an API key for a provider, then return its refreshed model list.

    Params:
        slug: provider slug (e.g. "deepseek", "xai")
        api_key: the key value to save

    Returns the provider dict with models populated (same shape as
    model.options entries) on success.
    """
    try:
        from takyon_cli.auth import PROVIDER_REGISTRY
        from takyon_cli.config import is_managed, save_env_value
        from takyon_cli.inventory import build_models_payload, load_picker_context

        slug = (params.get("slug") or "").strip()
        api_key = (params.get("api_key") or "").strip()
        if not slug or not api_key:
            return _err(rid, 4001, "slug and api_key are required")

        if is_managed():
            return _err(rid, 4006, "managed install — credentials are read-only")

        pconfig = PROVIDER_REGISTRY.get(slug)
        if not pconfig:
            return _err(rid, 4002, f"unknown provider: {slug}")
        if pconfig.auth_type != "api_key":
            return _err(
                rid,
                4003,
                f"{pconfig.name} uses {pconfig.auth_type} auth — "
                f"run `takyon model` to configure",
            )
        if not pconfig.api_key_env_vars:
            return _err(rid, 4004, f"no env var defined for {pconfig.name}")

        # Save the key to ~/.takyon/.env
        env_var = pconfig.api_key_env_vars[0]
        save_env_value(env_var, api_key)
        # Also set in current process so the refreshed inventory sees it.
        import os

        os.environ[env_var] = api_key

        # Refresh provider data via the shared inventory builder so this
        # surface stays in lock-step with model.options + dashboard
        # /api/model/options. picker_hints=True ensures the returned row
        # carries `authenticated` for the TUI frontend.
        session = _sessions.get(params.get("session_id", ""))
        agent = session.get("agent") if session else None
        ctx = load_picker_context().with_overrides(
            current_provider=getattr(agent, "provider", "") if agent else "",
            current_model=(
                (getattr(agent, "model", "") if agent else "") or _resolve_model()
            ),
            current_base_url=getattr(agent, "base_url", "") if agent else "",
        )
        payload = build_models_payload(
            ctx, picker_hints=True, max_models=50,
        )
        provider_data = next(
            (p for p in payload["providers"] if p["slug"] == slug), None
        )
        if provider_data is None:
            # Key was saved but provider didn't appear — still return success.
            provider_data = {
                "slug": slug,
                "name": pconfig.name,
                "is_current": False,
                "models": [],
                "total_models": 0,
                "authenticated": True,
            }
        # picker_hints sets `authenticated` from the row state, but the
        # synthetic fallback above doesn't go through that path.
        provider_data["authenticated"] = True
        return _ok(rid, {"provider": provider_data})
    except Exception as e:
        return _err(rid, 5034, str(e))


@method("model.disconnect")
def _(rid, params: dict) -> dict:
    """Remove credentials for a provider.

    Params:
        slug: provider slug (e.g. "deepseek", "xai")

    Returns success status and the provider's slug.
    """
    try:
        from takyon_cli.auth import PROVIDER_REGISTRY, clear_provider_auth
        from takyon_cli.config import remove_env_value

        slug = (params.get("slug") or "").strip()
        if not slug:
            return _err(rid, 4001, "slug is required")

        pconfig = PROVIDER_REGISTRY.get(slug)
        cleared_env = False
        cleared_auth = False

        # Remove API key env vars from .env and process
        if pconfig and pconfig.api_key_env_vars:
            for ev in pconfig.api_key_env_vars:
                if remove_env_value(ev):
                    cleared_env = True

        # Clear OAuth / credential pool state
        cleared_auth = clear_provider_auth(slug)

        if not cleared_env and not cleared_auth:
            return _err(rid, 4005, f"no credentials found for {slug}")

        provider_name = pconfig.name if pconfig else slug
        return _ok(
            rid,
            {
                "slug": slug,
                "name": provider_name,
                "disconnected": True,
            },
        )
    except Exception as e:
        return _err(rid, 5035, str(e))


# ── Methods: slash.exec ──────────────────────────────────────────────


def _mirror_slash_side_effects(sid: str, session: dict, command: str) -> str:
    """Apply side effects that must also hit the gateway's live agent."""
    parts = command.lstrip("/").split(None, 1)
    if not parts:
        return ""
    name, arg, agent = (
        parts[0],
        (parts[1].strip() if len(parts) > 1 else ""),
        session.get("agent"),
    )

    # Reject agent-mutating commands during an in-flight turn.  These
    # all do read-then-mutate on live agent/session state that the
    # worker thread running agent.run_conversation is using.  Parity
    # with the session.compress / session.undo guards and the gateway
    # runner's running-agent /model guard.
    _MUTATES_WHILE_RUNNING = {"model", "personality", "prompt", "compress"}
    if name in _MUTATES_WHILE_RUNNING and session.get("running"):
        return f"session busy — /interrupt the current turn before running /{name}"

    try:
        if name == "model" and arg and agent:
            result = _apply_model_switch(sid, session, arg)
            return result.get("warning", "")
        elif name == "personality" and arg and agent:
            _, new_prompt = _validate_personality(arg, _load_cfg())
            _apply_personality_to_session(sid, session, new_prompt)
        elif name == "prompt" and agent:
            cfg = _load_cfg()
            new_prompt = (cfg.get("agent") or {}).get("system_prompt", "") or ""
            agent.ephemeral_system_prompt = new_prompt or None
            agent._cached_system_prompt = None
        elif name == "compress" and agent:
            _compress_session_history(session, arg)
            _sync_session_key_after_compress(sid, session)
            _emit("session.info", sid, _session_info(agent))
        elif name == "fast" and agent:
            mode = arg.lower()
            if mode in {"fast", "on"}:
                agent.service_tier = "priority"
            elif mode in {"normal", "off"}:
                agent.service_tier = None
            _emit("session.info", sid, _session_info(agent))
        elif name == "reload-mcp" and agent and hasattr(agent, "reload_mcp_tools"):
            agent.reload_mcp_tools()
        elif name == "stop":
            from tools.process_registry import process_registry

            process_registry.kill_all()
    except Exception as e:
        return f"live session sync failed: {e}"
    return ""


@method("slash.exec")
def _(rid, params: dict) -> dict:
    session, err = _sess(params, rid)
    if err:
        return err

    cmd = params.get("command", "").strip()
    if not cmd:
        return _err(rid, 4004, "empty command")

    # Skill slash commands and _pending_input commands must NOT go through the
    # slash worker — see _PENDING_INPUT_COMMANDS definition above. Plugin
    # commands must also avoid the worker, but unlike skills/pending-input they
    # still return normal slash.exec output so the TUI keeps the pager path.
    _cmd_text = cmd.lstrip("/") if cmd.startswith("/") else cmd
    _cmd_parts = _cmd_text.split(maxsplit=1)
    _cmd_base = (_cmd_parts[0] if _cmd_parts else "").lower()
    _cmd_arg = _cmd_parts[1] if len(_cmd_parts) > 1 else ""

    if _cmd_base in _PENDING_INPUT_COMMANDS:
        return _err(
            rid, 4018, f"pending-input command: use command.dispatch for /{_cmd_base}"
        )

    if _cmd_base in _WORKER_BLOCKED_COMMANDS:
        subcommand = _cmd_arg.split(maxsplit=1)[0].lower() if _cmd_arg else ""
        if subcommand in {"restore", "rewind"}:
            return _err(
                rid,
                4018,
                "snapshot restore mutates live config/state; use command.dispatch for /snapshot restore",
            )

    try:
        from agent.skill_commands import get_skill_commands

        _cmd_key = f"/{_cmd_base}"
        if _cmd_key in get_skill_commands():
            return _err(
                rid, 4018, f"skill command: use command.dispatch for {_cmd_key}"
            )
    except Exception:
        pass

    plugin_handler = None
    resolve_plugin_command_result = None
    if _cmd_base:
        try:
            from takyon_cli.plugins import (
                get_plugin_command_handler,
                resolve_plugin_command_result,
            )

            plugin_handler = get_plugin_command_handler(_cmd_base)
        except Exception:
            plugin_handler = None
            resolve_plugin_command_result = None

    if plugin_handler and resolve_plugin_command_result:
        try:
            result = resolve_plugin_command_result(plugin_handler(_cmd_arg))
            return _ok(rid, {"output": str(result or "(no output)")})
        except Exception as e:
            return _ok(rid, {"output": f"Plugin command error: {e}"})

    worker = session.get("slash_worker")
    if not worker:
        try:
            worker = _SlashWorker(
                session["session_key"],
                getattr(session.get("agent"), "model", _resolve_model()),
                operator_user_id=_takyon_operator_user_id(session),
            )
            session["slash_worker"] = worker
        except Exception as e:
            return _err(rid, 5030, f"slash worker start failed: {e}")

    try:
        output = worker.run(cmd)
        warning = _mirror_slash_side_effects(params.get("session_id", ""), session, cmd)
        payload = {"output": output or "(no output)"}
        if warning:
            payload["warning"] = warning
        return _ok(rid, payload)
    except Exception as e:
        try:
            worker.close()
        except Exception:
            pass
        session["slash_worker"] = None
        return _err(rid, 5030, str(e))


# ── Methods: voice ───────────────────────────────────────────────────


_voice_sid_lock = threading.Lock()
_voice_event_sid: str = ""


def _voice_emit(event: str, payload: dict | None = None) -> None:
    """Emit a voice event toward the session that most recently turned the
    mode on. Voice is process-global (one microphone), so there's only ever
    one sid to target; the TUI handler treats an empty sid as "active
    session". Kept separate from _emit to make the lack of per-call sid
    argument explicit."""
    with _voice_sid_lock:
        sid = _voice_event_sid
    _emit(event, sid, payload)


def _voice_mode_enabled() -> bool:
    """Current voice-mode flag (runtime-only, CLI parity).

    cli.py initialises ``_voice_mode = False`` at startup and only flips
    it via ``/voice on``; it never reads a persisted enable bit from
    config.yaml.  We match that: no config lookup, env var only.  This
    avoids the TUI auto-starting in REC the next time the user opens it
    just because they happened to enable voice in a prior session.
    """
    return os.environ.get("TAKYON_VOICE", "").strip() == "1"


def _voice_tts_enabled() -> bool:
    """Whether agent replies should be spoken back via TTS (runtime only)."""
    return os.environ.get("TAKYON_VOICE_TTS", "").strip() == "1"


def _voice_cfg_dict() -> dict:
    """Shape-safe accessor for the ``voice:`` block in config.yaml.

    ``_load_cfg()`` returns raw ``yaml.safe_load()`` output, so both the
    root AND ``voice`` may be any YAML scalar / list / None. A hand-edit
    like ``voice: true`` or a malformed top-level config that parses to
    a scalar would otherwise break ``.get("…")`` and take every
    ``voice.*`` branch down with it (Copilot round-3..7 review on
    #19835). Coerce through ``isinstance`` at every level so malformed
    config falls back to an empty dict instead of crashing /voice.
    """
    cfg = _load_cfg()
    voice_cfg = cfg.get("voice") if isinstance(cfg, dict) else None

    return voice_cfg if isinstance(voice_cfg, dict) else {}


def _voice_record_key() -> str:
    """Current ``voice.record_key`` value, documented default on error."""
    record_key = _voice_cfg_dict().get("record_key")

    return str(record_key) if isinstance(record_key, str) and record_key else "ctrl+b"


@method("voice.toggle")
def _(rid, params: dict) -> dict:
    """CLI parity for the ``/voice`` slash command.

    Subcommands:

    * ``status`` — report mode + TTS flags (default when action is unknown).
    * ``on`` / ``off`` — flip voice *mode* (the umbrella bit). Turning it
      off also tears down any active continuous recording loop. Does NOT
      start recording on its own; recording is driven by ``voice.record``
      (Ctrl+B) after mode is on, matching cli.py's enable/Ctrl+B split.
    * ``tts`` — toggle speech-output of agent replies. Requires mode on
      (mirrors CLI's _toggle_voice_tts guard).
    """
    action = params.get("action", "status")

    if action == "status":
        # Mirror CLI's _show_voice_status: include STT/TTS provider
        # availability so the user can tell at a glance *why* voice mode
        # isn't working ("STT provider: MISSING ..." is the common case).
        # ``record_key`` mirrors the configured ``voice.record_key`` so the
        # TUI can both bind it (frontend ``isVoiceToggleKey``) and display
        # it in /voice status — previously the TUI hardcoded Ctrl+B and
        # ignored the config (#18994).
        payload: dict = {
            "enabled": _voice_mode_enabled(),
            "record_key": _voice_record_key(),
            "tts": _voice_tts_enabled(),
        }
        try:
            from tools.voice_mode import check_voice_requirements

            reqs = check_voice_requirements()
            payload["available"] = bool(reqs.get("available"))
            payload["audio_available"] = bool(reqs.get("audio_available"))
            payload["stt_available"] = bool(reqs.get("stt_available"))
            payload["details"] = reqs.get("details") or ""
        except Exception as e:
            # check_voice_requirements pulls optional transcription deps —
            # swallow so /voice status always returns something useful.
            logger.warning("voice.toggle status: requirements probe failed: %s", e)

        return _ok(rid, payload)

    if action in {"on", "off"}:
        enabled = action == "on"
        # Runtime-only flag (CLI parity) — no _write_config_key, so the
        # next TUI launch starts with voice OFF instead of auto-REC from a
        # persisted stale toggle.
        os.environ["TAKYON_VOICE"] = "1" if enabled else "0"

        if not enabled:
            # Disabling the mode must tear the continuous loop down; the
            # loop holds the microphone and would otherwise keep running.
            try:
                from takyon_cli.voice import stop_continuous

                stop_continuous()
            except ImportError:
                pass
            except Exception as e:
                logger.warning("voice: stop_continuous failed during toggle off: %s", e)

        return _ok(
            rid,
            {
                "enabled": enabled,
                "record_key": _voice_record_key(),
                "tts": _voice_tts_enabled(),
            },
        )

    if action == "tts":
        if not _voice_mode_enabled():
            return _err(rid, 4014, "enable voice mode first: /voice on")
        new_value = not _voice_tts_enabled()
        # Runtime-only flag (CLI parity) — see voice.toggle on/off above.
        os.environ["TAKYON_VOICE_TTS"] = "1" if new_value else "0"
        # Include ``record_key`` on every branch so a /voice tts toggle
        # doesn't reset the TUI's cached shortcut to the default when a
        # user has a custom binding configured (Copilot review, round 2
        # on #19835). Keeps parity with the status/on/off branches above.
        return _ok(
            rid,
            {
                "enabled": True,
                "record_key": _voice_record_key(),
                "tts": new_value,
            },
        )

    return _err(rid, 4013, f"unknown voice action: {action}")


@method("voice.record")
def _(rid, params: dict) -> dict:
    """VAD-bounded push-to-talk capture, CLI-parity.

    ``start`` begins one VAD-bounded capture and emits ``voice.transcript``
    after silence stops the recorder. ``stop`` forces transcription of the
    active buffer, matching classic CLI push-to-talk. The voice wrapper retains
    no-speech counts across single-shot starts, so three consecutive silent
    captures emit ``voice.transcript`` with ``no_speech_limit=True``.
    """
    action = params.get("action", "start")

    if action not in {"start", "stop"}:
        return _err(rid, 4019, f"unknown voice action: {action}")

    try:
        if action == "start":
            if not _voice_mode_enabled():
                return _err(rid, 4015, "voice mode is off — enable with /voice on")

            with _voice_sid_lock:
                global _voice_event_sid
                _voice_event_sid = params.get("session_id") or _voice_event_sid

            from takyon_cli.voice import start_continuous

            # Shape-safe lookups: malformed ``voice:`` YAML (bool/scalar/list)
            # must not crash /voice with a 5025 — fall back to VAD defaults.
            #
            # Exclude ``bool`` from the numeric check since Python's bool is
            # a subclass of int — a hand-edit like ``silence_threshold: true``
            # would otherwise forward as ``1`` instead of falling back to
            # the documented 200 / 3.0 defaults (Copilot round-12 on #19835).
            voice_cfg = _voice_cfg_dict()
            threshold = voice_cfg.get("silence_threshold")
            duration = voice_cfg.get("silence_duration")
            safe_threshold = (
                threshold
                if isinstance(threshold, (int, float))
                and not isinstance(threshold, bool)
                else 200
            )
            safe_duration = (
                duration
                if isinstance(duration, (int, float)) and not isinstance(duration, bool)
                else 3.0
            )
            started = start_continuous(
                on_transcript=lambda t: _voice_emit("voice.transcript", {"text": t}),
                on_status=lambda s: _voice_emit("voice.status", {"state": s}),
                on_silent_limit=lambda: _voice_emit(
                    "voice.transcript", {"no_speech_limit": True}
                ),
                silence_threshold=safe_threshold,
                silence_duration=safe_duration,
                auto_restart=False,
            )
            if started is False:
                return _ok(rid, {"status": "busy"})
            return _ok(rid, {"status": "recording"})

        # action == "stop"
        with _voice_sid_lock:
            _voice_event_sid = params.get("session_id") or _voice_event_sid

        from takyon_cli.voice import stop_continuous

        stop_continuous(force_transcribe=True)
        return _ok(rid, {"status": "stopped"})
    except ImportError:
        return _err(
            rid, 5025, "voice module not available — install audio dependencies"
        )
    except Exception as e:
        return _err(rid, 5025, str(e))


@method("voice.tts")
def _(rid, params: dict) -> dict:
    text = params.get("text", "")
    if not text:
        return _err(rid, 4020, "text required")
    try:
        from takyon_cli.voice import speak_text

        threading.Thread(target=speak_text, args=(text,), daemon=True).start()
        return _ok(rid, {"status": "speaking"})
    except ImportError:
        return _err(rid, 5026, "voice module not available")
    except Exception as e:
        return _err(rid, 5026, str(e))


# ── Methods: insights ────────────────────────────────────────────────


@method("insights.get")
def _(rid, params: dict) -> dict:
    days = params.get("days", 30)
    db = _get_db()
    if db is None:
        return _db_unavailable_error(rid, code=5017)
    try:
        cutoff = time.time() - days * 86400
        rows = [
            s
            for s in db.list_sessions_rich(limit=500)
            if (s.get("started_at") or 0) >= cutoff
        ]
        return _ok(
            rid,
            {
                "days": days,
                "sessions": len(rows),
                "messages": sum(s.get("message_count", 0) for s in rows),
            },
        )
    except Exception as e:
        return _err(rid, 5017, str(e))


# ── Methods: rollback ────────────────────────────────────────────────


@method("rollback.list")
def _(rid, params: dict) -> dict:
    session, err = _sess(params, rid)
    if err:
        return err
    try:

        def go(mgr, cwd):
            if not mgr.enabled:
                return _ok(rid, {"enabled": False, "checkpoints": []})
            return _ok(
                rid,
                {
                    "enabled": True,
                    "checkpoints": [
                        {
                            "hash": c.get("hash", ""),
                            "timestamp": c.get("timestamp", ""),
                            "message": c.get("message", ""),
                        }
                        for c in mgr.list_checkpoints(cwd)
                    ],
                },
            )

        return _with_checkpoints(session, go)
    except Exception as e:
        return _err(rid, 5020, str(e))


@method("rollback.restore")
def _(rid, params: dict) -> dict:
    session, err = _sess(params, rid)
    if err:
        return err
    target = params.get("hash", "")
    file_path = params.get("file_path", "")
    if not target:
        return _err(rid, 4014, "hash required")
    # Full-history rollback mutates session history.  Rejecting during
    # an in-flight turn prevents prompt.submit from silently dropping
    # the agent's output (version mismatch path) or clobbering the
    # rollback (version-matches path).  A file-scoped rollback only
    # touches disk, so we allow it.
    if not file_path and session.get("running"):
        return _err(
            rid,
            4009,
            "session busy — /interrupt the current turn before full rollback.restore",
        )
    try:

        def go(mgr, cwd):
            resolved = _resolve_checkpoint_hash(mgr, cwd, target)
            result = mgr.restore(cwd, resolved, file_path=file_path or None)
            if result.get("success") and not file_path:
                removed = 0
                with session["history_lock"]:
                    history = session.get("history", [])
                    while history and history[-1].get("role") in {"assistant", "tool"}:
                        history.pop()
                        removed += 1
                    if history and history[-1].get("role") == "user":
                        history.pop()
                        removed += 1
                    if removed:
                        session["history_version"] = (
                            int(session.get("history_version", 0)) + 1
                        )
                result["history_removed"] = removed
            return result

        return _ok(rid, _with_checkpoints(session, go))
    except Exception as e:
        return _err(rid, 5021, str(e))


@method("rollback.diff")
def _(rid, params: dict) -> dict:
    session, err = _sess(params, rid)
    if err:
        return err
    target = params.get("hash", "")
    if not target:
        return _err(rid, 4014, "hash required")
    try:
        r = _with_checkpoints(
            session,
            lambda mgr, cwd: mgr.diff(cwd, _resolve_checkpoint_hash(mgr, cwd, target)),
        )
        raw = r.get("diff", "")[:4000]
        payload = {"stat": r.get("stat", ""), "diff": raw}
        rendered = render_diff(raw, session.get("cols", 80))
        if rendered:
            payload["rendered"] = rendered
        return _ok(rid, payload)
    except Exception as e:
        return _err(rid, 5022, str(e))


# ── Methods: browser / plugins / cron / skills ───────────────────────


def _resolve_browser_cdp_url() -> str:
    """Return the configured browser CDP override without network I/O.

    ``/browser status`` must be fast — calling
    ``tools.browser_tool._get_cdp_override`` would invoke
    ``_resolve_cdp_override``, which performs an HTTP probe to
    ``.../json/version`` for discovery-style URLs.  That probe has
    a multi-second timeout and would block the TUI on a slow or
    unreachable host even though status only needs to report whether
    an override is set.

    Mirrors the env/config precedence of ``_get_cdp_override`` (env
    var first, then ``browser.cdp_url`` from config.yaml) without the
    websocket-resolution step, so the answer reflects user intent
    even when the configured host is not currently reachable.  The
    actual WS normalization happens in ``browser_navigate`` on the
    next tool call.
    """
    env_url = os.environ.get("BROWSER_CDP_URL", "").strip()
    if env_url:
        return env_url
    try:
        from takyon_cli.config import read_raw_config

        cfg = read_raw_config()
        browser_cfg = cfg.get("browser", {}) if isinstance(cfg, dict) else {}
        if isinstance(browser_cfg, dict):
            return str(browser_cfg.get("cdp_url", "") or "").strip()
    except Exception:
        pass
    return ""


def _is_default_local_cdp(parsed) -> bool:
    """Match the discovery-style local default; never the concrete WS form.

    A user-supplied ``ws://127.0.0.1:9222/devtools/browser/<id>`` is a
    real, connectable endpoint — collapsing it to bare ``http://...:9222``
    would strip the path and break the connect.
    """
    try:
        port = parsed.port or 80
    except ValueError:
        return False

    discovery_path = parsed.path in {"", "/", "/json", "/json/version"}
    return (
        parsed.scheme in {"http", "ws"}
        and parsed.hostname in {"127.0.0.1", "localhost"}
        and port == 9222
        and discovery_path
    )


def _http_ok(url: str, timeout: float) -> bool:
    import urllib.request

    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return 200 <= getattr(resp, "status", 200) < 300
    except Exception:
        return False


def _probe_urls(parsed) -> list[str]:
    scheme = {"ws": "http", "wss": "https"}.get(parsed.scheme, parsed.scheme)
    root = f"{scheme}://{parsed.netloc}".rstrip("/")
    return [f"{root}/json/version", f"{root}/json"]


def _normalize_cdp_url(parsed) -> str:
    # Concrete ``/devtools/browser/<id>`` endpoints (Browserbase et al.)
    # are connectable as-is. Discovery-style inputs collapse to bare
    # ``scheme://host:port`` so ``_resolve_cdp_override`` can append
    # ``/json/version`` later without doubling the path.
    if parsed.path.startswith("/devtools/browser/"):
        return parsed.geturl()
    return parsed._replace(path="", params="", query="", fragment="").geturl()


def _failure_messages(url: str, port: int, system: str) -> list[str]:
    from takyon_cli.browser_connect import manual_chrome_debug_command

    command = manual_chrome_debug_command(port, system)
    hint = (
        ["Start a Chromium-family browser with remote debugging, then retry /browser connect:", command]
        if command
        else [
            "No supported Chromium-family browser executable was found in this environment.",
            f"Install one or start a Chromium-family browser with --remote-debugging-port={port}, then retry /browser connect.",
        ]
    )
    return [
        f"Browser CDP is not reachable at {url}.",
        *hint,
        "Browser not connected — start a Chromium-family browser with remote debugging and retry /browser connect",
    ]


@method("browser.manage")
def _(rid, params: dict) -> dict:
    action = params.get("action", "status")

    if action == "status":
        url = _resolve_browser_cdp_url()
        return _ok(rid, {"connected": bool(url), "url": url})

    if action == "disconnect":
        return _browser_disconnect(rid)

    if action != "connect":
        return _err(rid, 4015, f"unknown action: {action}")

    return _browser_connect(rid, params)


def _browser_connect(rid, params: dict) -> dict:
    import platform

    from takyon_cli.browser_connect import DEFAULT_BROWSER_CDP_URL
    from tools.browser_tool import cleanup_all_browsers
    from urllib.parse import urlparse

    raw_url = params.get("url")
    if raw_url is not None and not isinstance(raw_url, str):
        return _err(
            rid, 4015, f"browser url must be a string, got {type(raw_url).__name__}"
        )
    url = (raw_url or "").strip() or DEFAULT_BROWSER_CDP_URL

    sid = params.get("session_id") or ""
    system = platform.system()
    messages: list[str] = []

    def announce(message: str, *, level: str = "info") -> None:
        messages.append(message)
        # Without a session id the TUI prints `messages` from the
        # response; emitting an event would double-render. Only stream
        # progress when there's a real session to scope it to.
        if sid:
            _emit("browser.progress", sid, {"message": message, "level": level})

    parsed = urlparse(url if "://" in url else f"http://{url}")
    if parsed.scheme not in {"http", "https", "ws", "wss"}:
        return _err(rid, 4015, f"unsupported browser url: {url}")
    if not parsed.hostname:
        return _err(rid, 4015, f"missing host in browser url: {url}")
    try:
        port = parsed.port or (443 if parsed.scheme in {"https", "wss"} else 80)
    except ValueError:
        return _err(rid, 4015, f"invalid port in browser url: {url}")

    # Always normalize default-local to 127.0.0.1:9222 so downstream
    # comparisons + messaging match what we'll actually persist.
    if _is_default_local_cdp(parsed):
        url = DEFAULT_BROWSER_CDP_URL
        parsed = urlparse(url)
        port = parsed.port or 9222

    try:
        # ws[s]://.../devtools/browser/<id> endpoints (hosted CDP
        # providers) don't serve the HTTP discovery path; just check
        # TCP-level reachability and let browser_navigate handshake.
        if parsed.scheme in {"ws", "wss"} and parsed.path.startswith(
            "/devtools/browser/"
        ):
            import socket

            try:
                with socket.create_connection((parsed.hostname, port), timeout=2.0):
                    pass
            except OSError as e:
                return _err(rid, 5031, f"could not reach browser CDP at {url}: {e}")
        else:
            probes = _probe_urls(parsed)
            ok = any(_http_ok(p, timeout=2.0) for p in probes)

            if not ok and _is_default_local_cdp(parsed):
                from takyon_cli.browser_connect import try_launch_chrome_debug

                announce(
                    "Chromium-family browser isn't running with remote debugging — attempting to launch..."
                )

                if try_launch_chrome_debug(port, system):
                    for _ in range(20):
                        time.sleep(0.5)
                        if any(_http_ok(p, timeout=1.0) for p in probes):
                            ok = True
                            break

                if ok:
                    announce(f"Chromium-family browser launched and listening on port {port}")
                else:
                    for line in _failure_messages(url, port, system)[1:]:
                        announce(line, level="error")
                    return _ok(
                        rid, {"connected": False, "url": url, "messages": messages}
                    )
            elif not ok:
                return _err(rid, 5031, f"could not reach browser CDP at {url}")
            elif _is_default_local_cdp(parsed):
                announce(f"Chromium-family browser is already listening on port {port}")

        normalized = _normalize_cdp_url(parsed)

        # Order matters: reap sessions BEFORE publishing the new env
        # so an in-flight tool call sees the old supervisor closed,
        # then again AFTER so the default task's cached supervisor
        # is drained against the new URL.
        cleanup_all_browsers()
        os.environ["BROWSER_CDP_URL"] = normalized
        cleanup_all_browsers()
    except Exception as e:
        return _err(rid, 5031, str(e))

    payload: dict[str, object] = {"connected": True, "url": normalized}
    if messages:
        payload["messages"] = messages
    return _ok(rid, payload)


def _browser_disconnect(rid) -> dict:
    # Reap, drop the env override, reap again — closes the same swap
    # window covered by ``_browser_connect``.
    def reap() -> None:
        try:
            from tools.browser_tool import cleanup_all_browsers

            cleanup_all_browsers()
        except Exception:
            pass

    reap()
    os.environ.pop("BROWSER_CDP_URL", None)
    reap()
    return _ok(rid, {"connected": False})


@method("plugins.list")
def _(rid, params: dict) -> dict:
    try:
        from takyon_cli.plugins import get_plugin_manager

        return _ok(
            rid,
            {
                "plugins": [
                    {
                        "name": n,
                        "version": getattr(i, "version", "?"),
                        "enabled": getattr(i, "enabled", True),
                    }
                    for n, i in get_plugin_manager()._plugins.items()
                ]
            },
        )
    except Exception as e:
        return _err(rid, 5032, str(e))


@method("config.show")
def _(rid, params: dict) -> dict:
    try:
        cfg = _load_cfg()
        model = _resolve_model()
        api_key = os.environ.get("TAKYON_API_KEY", "") or cfg.get("api_key", "")
        masked = f"****{api_key[-4:]}" if len(api_key) > 4 else "(not set)"
        base_url = os.environ.get("TAKYON_BASE_URL", "") or cfg.get("base_url", "")

        sections = [
            {
                "title": "Model",
                "rows": [
                    ["Model", model],
                    ["Base URL", base_url or "(default)"],
                    ["API Key", masked],
                ],
            },
            {
                "title": "Agent",
                "rows": [
                    ["Max Turns", str(_cfg_max_turns(cfg, 90))],
                    ["Toolsets", ", ".join(cfg.get("enabled_toolsets", [])) or "all"],
                    ["Verbose", str(cfg.get("verbose", False))],
                ],
            },
            {
                "title": "Environment",
                "rows": [
                    ["Working Dir", os.getcwd()],
                    ["Config File", str(_takyon_home / "config.yaml")],
                ],
            },
        ]
        return _ok(rid, {"sections": sections})
    except Exception as e:
        return _err(rid, 5030, str(e))


@method("tools.list")
def _(rid, params: dict) -> dict:
    try:
        from toolsets import get_all_toolsets, get_toolset_info

        session = _sessions.get(params.get("session_id", ""))
        enabled = (
            set(getattr(session["agent"], "enabled_toolsets", []) or [])
            if session
            else set(_load_enabled_toolsets() or [])
        )

        items = []
        for name in sorted(get_all_toolsets().keys()):
            info = get_toolset_info(name)
            if not info:
                continue
            items.append(
                {
                    "name": name,
                    "description": info["description"],
                    "tool_count": info["tool_count"],
                    "enabled": name in enabled if enabled else True,
                    "tools": info["resolved_tools"],
                }
            )
        return _ok(rid, {"toolsets": items})
    except Exception as e:
        return _err(rid, 5031, str(e))


@method("tools.show")
def _(rid, params: dict) -> dict:
    try:
        from model_tools import get_toolset_for_tool, get_tool_definitions

        session = _sessions.get(params.get("session_id", ""))
        enabled = (
            getattr(session["agent"], "enabled_toolsets", None)
            if session
            else _load_enabled_toolsets()
        )
        tools = get_tool_definitions(enabled_toolsets=enabled, quiet_mode=True)
        sections = {}

        for tool in sorted(tools, key=lambda t: t["function"]["name"]):
            name = tool["function"]["name"]
            desc = str(tool["function"].get("description", "") or "").split("\n")[0]
            if ". " in desc:
                desc = desc[: desc.index(". ") + 1]
            sections.setdefault(get_toolset_for_tool(name) or "unknown", []).append(
                {
                    "name": name,
                    "description": desc,
                }
            )

        return _ok(
            rid,
            {
                "sections": [
                    {"name": name, "tools": rows}
                    for name, rows in sorted(sections.items())
                ],
                "total": len(tools),
            },
        )
    except Exception as e:
        return _err(rid, 5034, str(e))


@method("tools.configure")
def _(rid, params: dict) -> dict:
    action = str(params.get("action", "") or "").strip().lower()
    targets = [
        str(name).strip() for name in params.get("names", []) or [] if str(name).strip()
    ]
    if action not in {"disable", "enable"}:
        return _err(rid, 4017, f"unknown tools action: {action}")
    if not targets:
        return _err(rid, 4018, "names required")

    try:
        from takyon_cli.config import load_config, save_config
        from takyon_cli.tools_config import (
            CONFIGURABLE_TOOLSETS,
            _apply_mcp_change,
            _apply_toolset_change,
            _get_platform_tools,
            _get_plugin_toolset_keys,
        )

        cfg = load_config()
        valid_toolsets = {
            ts_key for ts_key, _, _ in CONFIGURABLE_TOOLSETS
        } | _get_plugin_toolset_keys()
        toolset_targets = [name for name in targets if ":" not in name]
        mcp_targets = [name for name in targets if ":" in name]
        unknown = [name for name in toolset_targets if name not in valid_toolsets]
        toolset_targets = [name for name in toolset_targets if name in valid_toolsets]

        if toolset_targets:
            _apply_toolset_change(cfg, "cli", toolset_targets, action)

        missing_servers = (
            _apply_mcp_change(cfg, mcp_targets, action) if mcp_targets else set()
        )
        save_config(cfg)

        session = _sessions.get(params.get("session_id", ""))
        info = (
            _reset_session_agent(params.get("session_id", ""), session)
            if session
            else None
        )
        enabled = sorted(
            _get_platform_tools(load_config(), "cli", include_default_mcp_servers=False)
        )
        changed = [
            name
            for name in targets
            if name not in unknown
            and (":" not in name or name.split(":", 1)[0] not in missing_servers)
        ]

        return _ok(
            rid,
            {
                "changed": changed,
                "enabled_toolsets": enabled,
                "info": info,
                "missing_servers": sorted(missing_servers),
                "reset": bool(session),
                "unknown": unknown,
            },
        )
    except Exception as e:
        return _err(rid, 5035, str(e))


@method("toolsets.list")
def _(rid, params: dict) -> dict:
    try:
        from toolsets import get_all_toolsets, get_toolset_info

        session = _sessions.get(params.get("session_id", ""))
        enabled = (
            set(getattr(session["agent"], "enabled_toolsets", []) or [])
            if session
            else set(_load_enabled_toolsets() or [])
        )

        items = []
        for name in sorted(get_all_toolsets().keys()):
            info = get_toolset_info(name)
            if not info:
                continue
            items.append(
                {
                    "name": name,
                    "description": info["description"],
                    "tool_count": info["tool_count"],
                    "enabled": name in enabled if enabled else True,
                }
            )
        return _ok(rid, {"toolsets": items})
    except Exception as e:
        return _err(rid, 5032, str(e))


@method("agents.list")
def _(rid, params: dict) -> dict:
    try:
        from tools.process_registry import process_registry

        procs = process_registry.list_sessions()
        return _ok(
            rid,
            {
                "processes": [
                    {
                        "session_id": p["session_id"],
                        "command": p["command"][:80],
                        "status": p["status"],
                        "uptime": p["uptime_seconds"],
                    }
                    for p in procs
                ]
            },
        )
    except Exception as e:
        return _err(rid, 5033, str(e))


@method("cron.manage")
def _(rid, params: dict) -> dict:
    action, jid = params.get("action", "list"), params.get("name", "")
    try:
        from tools.cronjob_tools import cronjob

        if action == "list":
            return _ok(rid, json.loads(cronjob(action="list")))
        if action == "add":
            return _ok(
                rid,
                json.loads(
                    cronjob(
                        action="create",
                        name=jid,
                        schedule=params.get("schedule", ""),
                        prompt=params.get("prompt", ""),
                    )
                ),
            )
        if action in {"remove", "pause", "resume"}:
            return _ok(rid, json.loads(cronjob(action=action, job_id=jid)))
        return _err(rid, 4016, f"unknown cron action: {action}")
    except Exception as e:
        return _err(rid, 5023, str(e))


@method("skills.manage")
def _(rid, params: dict) -> dict:
    action, query = params.get("action", "list"), params.get("query", "")
    try:
        if action == "list":
            from takyon_cli.banner import get_available_skills

            return _ok(rid, {"skills": get_available_skills()})
        if action == "search":
            from tools.skills_hub import (
                GitHubAuth,
                create_source_router,
                unified_search,
            )

            raw = (
                unified_search(
                    query,
                    create_source_router(GitHubAuth()),
                    source_filter="all",
                    limit=20,
                )
                or []
            )
            return _ok(
                rid,
                {
                    "results": [
                        {"name": r.name, "description": r.description} for r in raw
                    ]
                },
            )
        if action == "install":
            from takyon_cli.skills_hub import do_install

            class _Q:
                def print(self, *a, **k):
                    pass

            do_install(query, skip_confirm=True, console=_Q())
            return _ok(rid, {"installed": True, "name": query})
        if action == "browse":
            from takyon_cli.skills_hub import browse_skills

            pg = int(params.get("page", 0) or 0) or (
                int(query) if query.isdigit() else 1
            )
            return _ok(
                rid, browse_skills(page=pg, page_size=int(params.get("page_size", 20)))
            )
        if action == "inspect":
            from takyon_cli.skills_hub import inspect_skill

            return _ok(rid, {"info": inspect_skill(query) or {}})
        return _err(rid, 4017, f"unknown skills action: {action}")
    except Exception as e:
        return _err(rid, 5024, str(e))


@method("skills.reload")
def _(rid, params: dict) -> dict:
    try:
        from agent.skill_commands import reload_skills

        result = reload_skills()
        added = result.get("added") or []
        removed = result.get("removed") or []
        total = int(result.get("total") or 0)

        lines = ["Reloading skills..."]
        if not added and not removed:
            lines.append("No new skills detected.")
        if added:
            lines.append("Added skills:")
            lines.extend(f"  - {item.get('name', '')}" for item in added)
        if removed:
            lines.append("Removed skills:")
            lines.extend(f"  - {item.get('name', '')}" for item in removed)
        lines.append(f"{total} skill(s) available")
        return _ok(rid, {"output": "\n".join(lines), "result": result})
    except Exception as e:
        return _err(rid, 5025, str(e))


# ── Methods: shell ───────────────────────────────────────────────────


@method("shell.exec")
def _(rid, params: dict) -> dict:
    cmd = params.get("command", "")
    if not cmd:
        return _err(rid, 4004, "empty command")
    try:
        from tools.approval import detect_dangerous_command

        is_dangerous, _, desc = detect_dangerous_command(cmd)
        if is_dangerous:
            return _err(
                rid, 4005, f"blocked: {desc}. Use the agent for dangerous commands."
            )
    except ImportError:
        pass
    try:
        r = subprocess.run(
            cmd, shell=True, capture_output=True, text=True, timeout=30, cwd=os.getcwd()
        )
        return _ok(
            rid,
            {
                "stdout": r.stdout[-4000:],
                "stderr": r.stderr[-2000:],
                "code": r.returncode,
            },
        )
    except subprocess.TimeoutExpired:
        return _err(rid, 5002, "command timed out (30s)")
    except Exception as e:
        return _err(rid, 5003, str(e))
