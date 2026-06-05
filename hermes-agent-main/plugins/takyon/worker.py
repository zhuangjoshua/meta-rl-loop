"""Postgres-native worker-drain plane — the process that turns queued ``jobs`` rows into real work.

This is the DRAIN half of the Phase-6 worker plane; the enqueue/schedule halves live in ``jobs.py``
(the at-least-once queue + budget-gated ``run_one`` cycle) and ``wakes.py`` (the ``wake_schedules``
cursor + in-DB ``dispatch_due_wakes()``). One long-lived process per deployment ties them together,
each loop tick:

  1. self-dispatches due CEO wakes (``wakes.dispatch_due_wakes``) — so pg_cron is OPTIONAL: a host
     running this worker needs no external scheduler to fire recurring wakes;
  2. reclaims stale claims left by a crashed prior worker (``jobs.requeue_stale``);
  3. drains the queue one job at a time through ``jobs.run_one`` — which keeps the FULL contract:
     ``FOR UPDATE SKIP LOCKED`` claim → flow-A reserve → handler → settle/refund, at-least-once, and
     never a fake ``completed`` (a partial/failed turn is ``blocked``/``failed``, invariant #8);
  4. dispatches each job KIND to its handler. Today the active handlers are ``ceo_wake`` — a
     scheduled CEO turn, the Postgres-native replacement for the legacy file-cron
     ``takyon-ceo:<slug>`` job — and ``ceo_bootstrap`` for durable create-time business bootstrap.

Money: ``run_one`` only reserves/settles when the job payload carries ``estimate_cents`` (>0), which
rides onto the job from ``wake_schedules.payload``. The ``ceo_wake`` handler therefore ALWAYS reports
the turn's true model cost (from the agent's own usage accounting) as ``actual_cost_cents`` so the
settle is correct whenever an estimate was reserved; with no estimate the turn runs unmetered and the
reported cost is simply ignored by ``run_one``. No second money path — this reuses flow-A unchanged.

Invariant #8 (no silent fallback): starting the loop with no DATABASE_URL configured raises loudly
via ``resolve_database_url`` — it never half-starts against a phantom queue, and never quietly falls
back to SQLite (there is no SQLite worker; jobs/wakes are Postgres-only).

INERT until deliberately run: importing this module starts nothing, and the tracked
``deploy/argon-alpha-14/takyon-worker.service`` exists but is NOT enabled on the VPS. Recurring wake
execution stays on the legacy file-cron until a host runs ``takyon-cli worker`` (or the unit is
enabled). Activation is a separate, operator-gated step.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import re
import socket
import subprocess
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from . import jobs, safebox, wakes
from .jobs import Job, JobOutcome, JobRunResult

_log = logging.getLogger("takyon.worker")

# Default tool-iteration ceiling for a single wake turn when the schedule payload does not pin one.
_DEFAULT_MAX_TURNS = 30
# Inactivity (not wall-clock) timeout for one CEO turn: a turn may run for a long time while it is
# actively calling tools / streaming, but a hung API call or stuck tool with NO activity for this
# many seconds is interrupted and the job fails (then retries / requeues). 0 disables the guard.
_DEFAULT_TURN_TIMEOUT = 600.0
# Default queue poll cadence when a tick drains nothing. Drain itself is tight (run_one in a loop).
_DEFAULT_POLL_SECONDS = 15.0
# Reclaim claims older than this from a crashed worker (matches jobs.requeue_stale's own default).
_STALE_SECONDS = 900


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _truncate_worker_text(value: str, limit: int = 400) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "..."


def _xurl_home() -> Path:
    return Path(os.environ.get("HOME") or str(Path.home())).expanduser()


def _xurl_env(home: Path) -> dict[str, str]:
    from .core import _runtime_env

    return _runtime_env({"HOME": str(home)})


def _parse_jsonish_output(text: str) -> dict[str, Any]:
    raw = str(text or "").strip()
    if not raw:
        return {}
    candidates = [raw, *reversed([line for line in raw.splitlines() if line.strip()])]
    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
        if isinstance(parsed, list):
            return {"items": parsed}
    return {"raw": raw}


def _run_xurl_json_command(command: list[str], *, home: Path, timeout: int = 90) -> dict[str, Any]:
    proc = subprocess.run(
        command,
        text=True,
        capture_output=True,
        timeout=timeout,
        env=_xurl_env(home),
    )
    if proc.returncode != 0:
        detail = _truncate_worker_text(proc.stderr or proc.stdout or "xurl command failed")
        raise RuntimeError(detail)
    return _parse_jsonish_output(proc.stdout or proc.stderr)


def _try_run_xurl_json_command(command: list[str], *, home: Path, timeout: int = 30) -> dict[str, Any]:
    try:
        return _run_xurl_json_command(command, home=home, timeout=timeout)
    except Exception:
        return {}


def _extract_x_post_id(payload: Mapping[str, Any] | None) -> str:
    data = payload.get("data") if isinstance(payload.get("data"), Mapping) else payload if isinstance(payload, Mapping) else {}
    for key in ("id", "rest_id", "post_id", "tweet_id"):
        value = str(data.get(key) or "").strip() if isinstance(data, Mapping) else ""
        if value:
            return value
    return ""


def _extract_x_username(payload: Mapping[str, Any] | None) -> str:
    if not isinstance(payload, Mapping):
        return ""
    data = payload.get("data") if isinstance(payload.get("data"), Mapping) else payload
    for key in ("username", "screen_name", "handle"):
        value = str((data or {}).get(key) or "").strip().lstrip("@")
        if value:
            return value
    return ""


def _publish_artifact_title(subject: str, body: str) -> str:
    title = str(subject or "").strip()
    if title:
        return title
    for line in str(body or "").splitlines():
        cleaned = line.strip()
        if cleaned:
            return cleaned[:80]
    return "X post"


def _safe_x_artifact_stem(post_id: str, *, fallback: str) -> str:
    stem = re.sub(r"[^A-Za-z0-9._-]+", "-", str(post_id or "").strip()).strip("-")
    return stem or fallback


def _persist_xurl_shared_auth_best_effort(home: Path) -> None:
    auth_path = home / ".xurl"
    if not auth_path.exists():
        return
    try:
        encoded = base64.b64encode(auth_path.read_bytes()).decode("ascii")
        safebox.save_env_backed_value("XURL_SHARED_AUTH_B64_SECRET", encoded)
    except Exception as exc:  # pragma: no cover - backup persistence should not block posting
        _log.debug("failed to persist shared xurl auth: %s", exc)


def _ensure_local_xurl_auth() -> tuple[str, Path]:
    from .core import (
        _decode_xurl_shared_auth_blob,
        _read_xurl_shared_auth_secret,
        _resolve_xurl_executable,
        _xurl_auth_path,
        _xurl_auth_status_ok,
    )

    home = _xurl_home()
    xurl = _resolve_xurl_executable()
    if not xurl:
        raise RuntimeError("xurl is not installed on the worker host")
    auth_path = _xurl_auth_path(home=str(home))
    if _xurl_auth_status_ok(home=str(home)):
        return xurl, auth_path

    key, value = _read_xurl_shared_auth_secret()
    auth_text = _decode_xurl_shared_auth_blob(key, value) if key and value else ""
    if not auth_text.strip():
        raise RuntimeError(
            "shared xurl auth is missing; seed /root/.xurl or configure XURL_SHARED_AUTH_B64_SECRET"
        )

    auth_path.parent.mkdir(parents=True, exist_ok=True)
    auth_path.write_text(auth_text, encoding="utf-8")
    os.chmod(auth_path, 0o600)
    if not _xurl_auth_status_ok(home=str(home)):
        raise RuntimeError("shared xurl auth is present but xurl auth status failed")
    _persist_xurl_shared_auth_best_effort(home)
    return xurl, auth_path


def _update_outreach_work_request(
    slug: str,
    work_request_id: str,
    *,
    status: str,
    payload_updates: Mapping[str, Any] | None = None,
) -> None:
    if not work_request_id:
        return
    from .core import TakyonStore

    store = TakyonStore()
    with store._connect() as conn:
        row = conn.execute(
            f"SELECT scope, kind, payload_json FROM {store._work_requests_table()} WHERE id = ?",
            (work_request_id,),
        ).fetchone()
        if row is None:
            return
        payload = _parse_jsonish_output(str(row["payload_json"] or ""))
        if isinstance(payload_updates, Mapping):
            payload.update(
                {
                    str(key): value
                    for key, value in payload_updates.items()
                    if value not in (None, "", [], {})
                }
            )
        conn.execute(
            f"UPDATE {store._work_requests_table()} SET status = ?, payload_json = ?, updated_at = ? WHERE id = ?",
            (status, json.dumps(payload), _utc_now_iso(), work_request_id),
        )
        store._rewrite_distribution_files(conn, slug)
        store._record_event(
            conn,
            scope=str(row["scope"] or f"business:{slug}"),
            business_slug=slug,
            event_type="job.enqueue.status",
            payload={
                "job_id": work_request_id,
                "kind": str(row["kind"] or ""),
                "status": status,
            },
        )


def _record_x_publish_result(
    slug: str,
    *,
    job_id: str,
    payload: Mapping[str, Any],
    post_id: str,
    post_url: str,
    provider_response: Mapping[str, Any] | None,
) -> dict[str, str]:
    from .core import TakyonStore

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    stem = _safe_x_artifact_stem(post_id, fallback=str(job_id))
    artifact_rel = f"distribution/local-published/x/{timestamp}-{stem}.md"
    receipt_rel = f"metrics/receipts/outreach/{timestamp}-x-{stem}.json"
    body = str(payload.get("body") or "").strip()
    subject = str(payload.get("subject") or "").strip()
    metadata = payload.get("metadata") if isinstance(payload.get("metadata"), Mapping) else {}
    published_at = _utc_now_iso()
    title = _publish_artifact_title(subject, body)
    thread_external_id = str(payload.get("thread_external_id") or post_id).strip() or post_id
    artifact_lines = [f"# {title}", "", body, "", f"Published: {post_url}"]
    receipt_payload = {
        "provider": "x",
        "channel": str(payload.get("channel") or "x"),
        "target": payload.get("target"),
        "recipient": payload.get("recipient"),
        "subject": subject,
        "body": body,
        "destination_url": payload.get("destination_url"),
        "destination_label": payload.get("destination_label"),
        "thread_external_id": thread_external_id,
        "post_id": post_id,
        "post_url": post_url,
        "published_at": published_at,
        "sent": True,
        "external_side_effects": "sent",
        "artifact_path": artifact_rel,
        "metadata": dict(metadata),
        "provider_response": dict(provider_response or {}),
    }

    operations: list[dict[str, Any]] = [
        {
            "action": "artifact.write",
            "business": slug,
            "path": artifact_rel,
            "content": "\n".join(artifact_lines).rstrip() + "\n",
        },
        {
            "action": "artifact.write",
            "business": slug,
            "path": receipt_rel,
            "content": json.dumps(receipt_payload, indent=2, sort_keys=True) + "\n",
        },
        {
            "action": "conversation.thread.upsert",
            "business": slug,
            "source": "x",
            "external_id": thread_external_id,
            "title": title,
            "url": post_url,
            "status": "active",
        },
        {
            "action": "conversation.message.record",
            "business": slug,
            "source": "x",
            "external_id": post_id,
            "thread_external_id": thread_external_id,
            "thread_title": title,
            "direction": "outbound",
            "author_label": "business",
            "body": body,
            "status": "responded",
            "received_at": published_at,
        },
    ]
    store = TakyonStore()
    store.commit(
        scope=f"business:{slug}",
        operations=operations,
        idempotency_key=f"x-publish-artifact:{job_id}:{post_id}",
        reason="worker recorded live X publish receipt",
        actor="worker",
    )
    return {"artifact": artifact_rel, "receipt": receipt_rel}


# ── the ceo_wake handler ────────────────────────────────────────────────────────────────────────


def _record_runtime_event(
    slug: str,
    *,
    kind: str,
    status: str,
    detail: str = "",
    line: str = "",
    command: str = "",
    trace: Mapping[str, Any] | None = None,
) -> None:
    from .core import TakyonStore

    payload = {
        "kind": kind,
        "status": status,
        "detail": detail,
        "line": line,
        "command": command,
    }
    if isinstance(trace, Mapping) and trace:
        payload["trace"] = {
            str(key): value
            for key, value in trace.items()
            if value not in (None, "", [], {})
        }
    try:
        store = TakyonStore()
        with store._connect() as conn:
            store._record_event(
                conn,
                scope=f"business:{slug}/runtime",
                business_slug=slug,
                event_type=f"dashboard.run.{status}",
                payload=payload,
            )
    except Exception as exc:  # pragma: no cover - best-effort trace only
        _log.debug("failed to record worker runtime event for %s: %s", slug, exc)


def _refresh_business_surface_after_bootstrap(slug: str, *, job_id: str) -> dict[str, Any] | None:
    """After scratch sync-up, refresh the durable product surface if this bootstrap declared one.

    This closes the gap where a bootstrap turn writes final `product/site/*` files late in the turn
    (for example via `business_write_file`) after an earlier worker refresh already ran against an
    incomplete scratch workspace. The durable business root is the source of truth for product-host
    publication, so do one final trusted refresh against that durable state before marking bootstrap
    complete.
    """

    from .core import TakyonStore, handle_business_refresh_product_surface

    store = TakyonStore()
    summary = store.read(scope=f"business:{slug}", query="summary", include=["app"], limit=1)
    app = summary.get("app") if isinstance(summary.get("app"), dict) else {}
    surface = app.get("surface") or app.get("surface_contract") or {}
    if not isinstance(surface, Mapping):
        return None
    source_path = str(surface.get("source_path") or "").strip()
    if not source_path:
        return None

    raw = handle_business_refresh_product_surface(
        {
            "business": slug,
            "source_path": source_path,
            "publish_target": surface.get("publish_target"),
            "publish_policy": surface.get("publish_policy"),
            "activate_on_success": True,
            "install": True,
            "idempotency_key": f"{job_id}:bootstrap-final-surface-refresh",
            "reason": "bootstrap final product surface refresh",
            "actor": "worker",
        }
    )
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return {"status": "failed", "error": f"invalid product surface refresh payload: {raw[:400]}"}
    if not isinstance(payload, dict):
        return {"status": "failed", "error": "invalid product surface refresh payload"}
    refresh = payload.get("surface_refresh")
    if isinstance(refresh, dict):
        return refresh
    if not payload.get("success", False):
        return {"status": "failed", "error": str(payload.get("error") or "product surface refresh failed")}
    return None


def _humanize_trace_label(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return "Activity"
    text = re.sub(r"[._-]+", " ", text)
    return " ".join(part.capitalize() for part in text.split())


def _tool_trace_shape(name: str, args: Mapping[str, Any] | None = None) -> tuple[str, str, str]:
    tool_name = str(name or "").strip()
    arguments = args if isinstance(args, Mapping) else {}
    if tool_name == "skill_view":
        skill_name = str(arguments.get("name") or "").strip()
        label = skill_name or "Skill"
        detail = f"Loaded skill {skill_name}." if skill_name else "Loaded a skill."
        return "skill", label, detail
    if tool_name == "todo":
        todos = arguments.get("todos")
        count = len(todos) if isinstance(todos, list) else 0
        return "tool", "Todo", (f"Updated {count} task{'s' if count != 1 else ''}." if count else "Updated task list.")
    if tool_name == "business_claude_agent_task":
        workspace = str(arguments.get("workspace") or arguments.get("source_path") or "").strip()
        return "tool", "Delegated worker", workspace or "Delegated workspace task."
    return "tool", _humanize_trace_label(tool_name), ""


class _RuntimeProgress:
    def __init__(self, *, slug: str, kind: str, command: str):
        self.slug = slug
        self.kind = kind
        self.command = command
        self._last_activity = ""
        self._last_tool_generating = ""

    def _record_trace(
        self,
        *,
        entry_kind: str,
        entry_key: str,
        label: str,
        detail: str,
        status: str,
        tool_name: str = "",
        skill_name: str = "",
        preview: str = "",
        summary: str = "",
    ) -> None:
        _record_runtime_event(
            self.slug,
            kind=self.kind,
            status="trace",
            detail=detail or label,
            command=self.command,
            trace={
                "kind": entry_kind,
                "entry_key": entry_key,
                "label": label,
                "detail": detail,
                "status": status,
                "tool_name": tool_name,
                "skill_name": skill_name,
                "preview": preview,
                "summary": summary,
            },
        )

    def emit(self, line: str) -> None:
        text = str(line or "").strip()
        if not text:
            return
        _record_runtime_event(
            self.slug,
            kind=self.kind,
            status="output",
            detail=text,
            line=text,
            command=self.command,
        )

    def tool_generating(self, name: str) -> None:
        if not name or name == self._last_tool_generating:
            return
        self._last_tool_generating = name
        self.emit(f"preparing tool -> {name}")

    def tool_started(
        self,
        tool_call_id: str,
        name: str,
        args: dict[str, object],
    ) -> None:
        from agent.display import build_tool_preview

        tool_name = str(name or "").strip()
        if not tool_name:
            return
        preview = build_tool_preview(tool_name, args if isinstance(args, dict) else {}, max_len=120) or ""
        entry_kind, label, default_detail = _tool_trace_shape(tool_name, args)
        skill_name = str((args or {}).get("name") or "").strip() if tool_name == "skill_view" else ""
        self._record_trace(
            entry_kind=entry_kind,
            entry_key=f"tool:{tool_call_id or tool_name}",
            label=label,
            detail=preview or default_detail or f"{label} started.",
            status="running",
            tool_name=tool_name,
            skill_name=skill_name,
            preview=preview,
        )

    def activity(self, desc: str) -> None:
        text = str(desc or "").strip()
        if not text or text == self._last_activity:
            return
        self._last_activity = text
        self.emit(f"agent -> {text}")

    def tool_progress(
        self,
        event_type: str,
        name: str | None = None,
        preview: str | None = None,
        args: dict[str, object] | None = None,
        **kwargs: object,
    ) -> None:
        if not name:
            return
        if event_type == "tool.started":
            self._last_tool_generating = ""
            suffix = f" · {preview}" if preview else ""
            self.emit(f"tool started -> {name}{suffix}")
        elif event_type == "tool.completed":
            duration = kwargs.get("duration")
            suffix = f" · {duration:.1f}s" if isinstance(duration, (int, float)) else ""
            self.emit(f"tool completed -> {name}{suffix}")

    def tool_completed(
        self,
        tool_id: str,
        name: str,
        args: dict[str, object],
        result: object,
    ) -> None:
        from .cli import _tool_progress_lines

        lines = _tool_progress_lines(name, args if isinstance(args, dict) else {}, result)
        for line in lines[:2]:
            self.emit(line)
        entry_kind, label, default_detail = _tool_trace_shape(name, args)
        skill_name = str((args or {}).get("name") or "").strip() if name == "skill_view" else ""
        detail = next((str(line).strip() for line in lines if str(line).strip()), "")
        self._record_trace(
            entry_kind=entry_kind,
            entry_key=f"tool:{tool_id or name}",
            label=label,
            detail=detail or default_detail or f"{label} completed.",
            status="completed",
            tool_name=str(name or "").strip(),
            skill_name=skill_name,
            summary=detail or default_detail or f"{label} completed.",
        )


def _run_ceo_turn(
    *,
    slug: str,
    system_prompt: str,
    user_prompt: str,
    toolsets: list[str],
    max_turns: int,
    inactivity_limit: float,
    progress: _RuntimeProgress | None = None,
) -> tuple[str, float, str]:
    """Run ONE CEO wake turn for ``business:<slug>`` and return ``(final_response, cost_usd,
    cost_status)``.

    Built to be the SAME CEO the interactive shell runs (``cli._run_agent``): the stable
    ``prompts/ceo.md`` as the ephemeral system prompt, the per-business wake instructions
    (``core._ceo_cron_prompt``) as the user turn, the wake toolsets (``core._ceo_cron_toolsets``),
    and the model/provider resolved the same way (``cli._require_agent_model_config`` — which raises
    loudly if unconfigured, invariant #8). The difference vs. the shell path is purely operational:
    no interactive operator-envelope wrapping, a daemon-grade inactivity timeout (mirrors
    ``cron/scheduler.py``), and the turn's true cost extracted for billing settlement.

    Raises on a failed/aborted turn (so ``jobs.run_one`` refunds the reservation and fails/requeues
    rather than recording a fake completion)."""
    import concurrent.futures
    import contextvars

    from takyon_cli.runtime_provider import resolve_runtime_provider

    from .cli import _read_model_config, _require_agent_model_config
    from .core import TakyonStore, load_takyon_env
    from .operator_gateway import build_operator_gateway_agent

    load_takyon_env()
    model_config = _read_model_config(TakyonStore())
    resolved_model = _require_agent_model_config(model_config)  # raises TakyonError if missing
    provider = model_config.get("provider", "")
    runtime = resolve_runtime_provider(
        requested=provider or None,
        target_model=resolved_model,
    )
    agent = build_operator_gateway_agent(
        runtime=runtime,
        model=resolved_model,
        operator_user_id=_business_owner_user_id(slug),
        business_slug=slug,
        agent_kwargs={
            "max_iterations": max_turns,
            "enabled_toolsets": list(toolsets),
            # Same suppressions as the interactive CEO turn: no cron/messaging/clarify side channels,
            # no memory writes (a wake must not corrupt user representations), no shell-only toolsets.
            "disabled_toolsets": [
                "cronjob",
                "messaging",
                "clarify",
                "memory",
                "session_search",
                "terminal",
                "file",
                "browser",
                "code_execution",
            ],
            "ephemeral_system_prompt": system_prompt,
            "load_soul_identity": False,
            "skip_memory": True,
            "skip_context_files": True,
            "platform": "takyon",
            "quiet_mode": True,
            "tool_progress_callback": progress.tool_progress if progress is not None else None,
            "tool_start_callback": progress.tool_started if progress is not None else None,
            "tool_gen_callback": progress.tool_generating if progress is not None else None,
            "tool_complete_callback": progress.tool_completed if progress is not None else None,
        },
    )
    agent._memory_nudge_interval = 0
    agent._skill_nudge_interval = 0
    agent.suppress_status_output = True
    agent.activity_callback = progress.activity if progress is not None else None

    # Run on a worker thread and watch the agent's own activity tracker, so a hung turn is caught
    # without killing a healthy long-running one. (Mirrors cron/scheduler.py's inactivity guard.)
    limit = inactivity_limit if inactivity_limit and inactivity_limit > 0 else None
    pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    ctx = contextvars.copy_context()
    future = pool.submit(ctx.run, agent.run_conversation, user_prompt)
    timed_out = False
    try:
        if limit is None:
            result = future.result()
        else:
            result = None
            while True:
                done, _ = concurrent.futures.wait({future}, timeout=5.0)
                if done:
                    result = future.result()
                    break
                idle = 0.0
                if hasattr(agent, "get_activity_summary"):
                    try:
                        idle = float(agent.get_activity_summary().get("seconds_since_activity", 0.0))
                    except Exception:
                        idle = 0.0
                if idle >= limit:
                    timed_out = True
                    break
    finally:
        pool.shutdown(wait=False, cancel_futures=True)

    if timed_out:
        if hasattr(agent, "interrupt"):
            agent.interrupt("CEO wake timed out (inactivity)")
        raise TimeoutError(
            f"CEO wake for business:{slug} idle past {int(limit)}s inactivity limit"
        )

    if not isinstance(result, dict):
        raise RuntimeError(
            f"agent.run_conversation returned {type(result).__name__} instead of dict for "
            f"business:{slug}"
        )
    # A turn that reported failure must NOT be billed or marked completed — raise so run_one
    # refunds and fails/requeues (invariant #8).
    if result.get("failed") is True or result.get("completed") is False:
        raise RuntimeError(
            str(result.get("error") or (result.get("final_response") or "").strip() or "CEO wake reported failure")
        )

    final_response = str(result.get("final_response") or "")
    cost_usd = float(getattr(agent, "session_estimated_cost_usd", 0.0) or 0.0)
    cost_status = str(getattr(agent, "session_cost_status", "unknown") or "unknown")
    return final_response, cost_usd, cost_status


def _business_owner_user_id(slug: str) -> str:
    from .core import TakyonStore

    store = TakyonStore()
    with store._connect() as conn:
        business = store._ensure_business(conn, slug)
    return str(business.get("owner_user_id") or "").strip()


def ceo_wake_handler(job: Job) -> JobRunResult:
    """Handle a ``ceo_wake`` job: run the scheduled CEO turn for ``job.business_slug`` and report its
    true model cost as ``actual_cost_cents`` for flow-A settlement.

    The wake prompt and toolsets come from the canonical source (``core._ceo_cron_prompt`` /
    ``_ceo_cron_toolsets``) so this never drifts from the legacy/cron wake instructions; the system
    prompt is the stable ``prompts/ceo.md`` via ``cli._load_ceo_prompt``."""
    from gateway.session_context import clear_session_vars, set_session_vars

    from .cli import _business_workspace_execution_context, _load_ceo_prompt
    from .core import TakyonStore

    slug = job.business_slug
    store = TakyonStore()
    user_prompt = store._ceo_cron_prompt(slug)
    toolsets = store._ceo_cron_toolsets()
    system_prompt = _load_ceo_prompt()
    owner_user_id = _business_owner_user_id(slug)
    progress = _RuntimeProgress(slug=slug, kind="ceo_wake", command=f"/wake {slug}")

    payload = job.payload or {}
    try:
        max_turns = int(payload.get("max_turns") or _DEFAULT_MAX_TURNS)
    except (TypeError, ValueError):
        max_turns = _DEFAULT_MAX_TURNS
    inactivity_limit = _env_float("TAKYON_WORKER_TURN_TIMEOUT", _DEFAULT_TURN_TIMEOUT)

    tokens: list[object] = []
    try:
        _record_runtime_event(
            slug,
            kind="ceo_wake",
            status="started",
            detail="CEO wake is running.",
            command=f"/wake {slug}",
            trace={
                "kind": "turn",
                "entry_key": "turn:ceo_wake",
                "label": "CEO wake",
                "detail": "CEO wake is running.",
                "status": "running",
            },
        )
        with _business_workspace_execution_context(
            slug,
            operator_user_id=owner_user_id,
            sync_on_exception=True,
        ) as workspace_home:
            tokens = set_session_vars(
                user_id=owner_user_id,
                workspace_root=str(workspace_home or ""),
                business_slug=slug,
            )
            final_response, cost_usd, cost_status = _run_ceo_turn(
                slug=slug,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                toolsets=toolsets,
                max_turns=max_turns,
                inactivity_limit=inactivity_limit,
                progress=progress,
            )
    except Exception as exc:
        _record_runtime_event(
            slug,
            kind="ceo_wake",
            status="failed",
            detail=str(exc),
            command=f"/wake {slug}",
            trace={
                "kind": "turn",
                "entry_key": "turn:ceo_wake",
                "label": "CEO wake",
                "detail": str(exc),
                "status": "failed",
            },
        )
        raise
    finally:
        if tokens:
            clear_session_vars(tokens)
    cents = max(0, int(round(cost_usd * 100)))
    _record_runtime_event(
        slug,
        kind="ceo_wake",
        status="completed",
        detail="CEO wake completed.",
        command=f"/wake {slug}",
        trace={
            "kind": "turn",
            "entry_key": "turn:ceo_wake",
            "label": "CEO wake",
            "detail": final_response[:280].strip() or "CEO wake completed.",
            "status": "completed",
        },
    )
    return JobRunResult(
        result={
            "business_slug": slug,
            "final_response": final_response[:4000],
            "cost_usd": round(cost_usd, 6),
            "cost_status": cost_status,
        },
        actual_cost_cents=cents,
    )


def ceo_bootstrap_handler(job: Job) -> JobRunResult:
    from gateway.session_context import clear_session_vars, set_session_vars

    from .cli import (
        _business_bootstrap_instruction,
        _business_workspace_execution_context,
        _load_ceo_prompt,
    )
    from .core import TakyonStore

    slug = job.business_slug
    store = TakyonStore()
    summary = store.read(scope=f"business:{slug}", query="summary")
    business = summary.get("business") if isinstance(summary.get("business"), dict) else {}
    active_mode = "live"
    goal = str((job.payload or {}).get("goal") or (business or {}).get("goal") or "").strip()
    business_name = str((business or {}).get("name") or "").strip()
    user_prompt = _business_bootstrap_instruction(
        slug,
        goal,
        active_mode,
        business_name=business_name,
    )
    system_prompt = _load_ceo_prompt()
    owner_user_id = _business_owner_user_id(slug)
    payload = job.payload or {}
    try:
        max_turns = int(payload.get("max_turns") or _DEFAULT_MAX_TURNS)
    except (TypeError, ValueError):
        max_turns = _DEFAULT_MAX_TURNS
    inactivity_limit = _env_float("TAKYON_WORKER_TURN_TIMEOUT", _DEFAULT_TURN_TIMEOUT)
    schedule = str(payload.get("schedule") or "").strip()
    command = f"/create {slug}"
    progress = _RuntimeProgress(slug=slug, kind="ceo_bootstrap", command=command)

    tokens: list[object] = []
    try:
        _record_runtime_event(
            slug,
            kind="ceo_bootstrap",
            status="started",
            detail="CEO bootstrap is running.",
            command=command,
            trace={
                "kind": "turn",
                "entry_key": "turn:ceo_bootstrap",
                "label": "CEO bootstrap",
                "detail": "CEO bootstrap is running.",
                "status": "running",
            },
        )
        with _business_workspace_execution_context(
            slug,
            operator_user_id=owner_user_id,
            sync_on_exception=True,
        ) as workspace_home:
            tokens = set_session_vars(
                user_id=owner_user_id,
                workspace_root=str(workspace_home or ""),
                business_slug=slug,
            )
            final_response, cost_usd, cost_status = _run_ceo_turn(
                slug=slug,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                toolsets=["takyon", "web", "skills", "todo"],
                max_turns=max_turns,
                inactivity_limit=inactivity_limit,
                progress=progress,
            )
    except Exception as exc:
        _record_runtime_event(
            slug,
            kind="ceo_bootstrap",
            status="failed",
            detail=str(exc),
            command=command,
            trace={
                "kind": "turn",
                "entry_key": "turn:ceo_bootstrap",
                "label": "CEO bootstrap",
                "detail": str(exc),
                "status": "failed",
            },
        )
        raise
    finally:
        if tokens:
            clear_session_vars(tokens)

    surface_refresh = _refresh_business_surface_after_bootstrap(slug, job_id=str(job.id))
    if surface_refresh:
        publish = surface_refresh.get("publish") if isinstance(surface_refresh.get("publish"), dict) else {}
        publish_status = str(publish.get("status") or surface_refresh.get("status") or "").strip() or "unknown"
        publish_target = str(publish.get("public_url") or publish.get("publish_target") or "").strip()
        publish_blocker = str(
            publish.get("blocker")
            or surface_refresh.get("blocker")
            or surface_refresh.get("error")
            or ""
        ).strip()
        if publish_status == "published":
            detail = f"product surface -> published {publish_target or slug}"
        elif publish_blocker:
            detail = f"product surface -> {publish_status}: {publish_blocker}"
        else:
            detail = f"product surface -> {publish_status}"
        _record_runtime_event(
            slug,
            kind="ceo_bootstrap",
            status="output",
            detail=detail,
            line=detail,
            command=command,
        )

    wake_result: dict[str, object] | None = None
    if schedule:
        wake_result = store.commit(
            scope=f"business:{slug}",
            operations=[{"action": "cron.ensure_ceo_wakeup", "business": slug, "schedule": schedule}],
            idempotency_key=f"{job.id}:bootstrap-wake:{schedule}",
            reason="bootstrap completed and enabled CEO wake loop",
            actor="worker",
        )
        _record_runtime_event(
            slug,
            kind="ceo_bootstrap",
            status="output",
            detail=f"wake schedule -> business:{slug} {schedule}",
            line=f"wake schedule -> business:{slug} {schedule}",
            command=command,
        )

    cents = max(0, int(round(cost_usd * 100)))
    _record_runtime_event(
        slug,
        kind="ceo_bootstrap",
        status="completed",
        detail="CEO bootstrap completed.",
        command=command,
        trace={
            "kind": "turn",
            "entry_key": "turn:ceo_bootstrap",
            "label": "CEO bootstrap",
            "detail": final_response[:280].strip() or "CEO bootstrap completed.",
            "status": "completed",
        },
    )
    return JobRunResult(
        result={
            "business_slug": slug,
            "final_response": final_response[:4000],
            "cost_usd": round(cost_usd, 6),
            "cost_status": cost_status,
            "surface_refresh": surface_refresh,
            "wake": wake_result,
        },
        actual_cost_cents=cents,
    )


def x_publish_outreach_handler(job: Job) -> JobRunResult:
    payload = job.payload or {}
    slug = job.business_slug
    body = str(payload.get("body") or "").strip()
    if not body:
        raise RuntimeError("x publish job is missing a body")

    work_request_id = str(payload.get("work_request_id") or "").strip()
    reply_to = str(payload.get("thread_external_id") or "").strip()
    home = _xurl_home()
    if work_request_id:
        _update_outreach_work_request(
            slug,
            work_request_id,
            status="running",
            payload_updates={"worker_job_id": str(job.id)},
        )

    try:
        xurl, _auth_path = _ensure_local_xurl_auth()
        command = [xurl, "reply", reply_to, body] if reply_to else [xurl, "post", body]
        provider_response = _run_xurl_json_command(command, home=home, timeout=90)
        post_id = _extract_x_post_id(provider_response) or str(job.id)
        whoami = _try_run_xurl_json_command([xurl, "whoami"], home=home, timeout=20)
        username = _extract_x_username(whoami)
        post_url = (
            f"https://x.com/{username}/status/{post_id}"
            if username
            else f"https://x.com/i/web/status/{post_id}"
        )
        artifacts = _record_x_publish_result(
            slug,
            job_id=str(job.id),
            payload=payload,
            post_id=post_id,
            post_url=post_url,
            provider_response=provider_response,
        )
        if work_request_id:
            _update_outreach_work_request(
                slug,
                work_request_id,
                status="completed",
                payload_updates={
                    "artifact_path": artifacts["artifact"],
                    "receipt_path": artifacts["receipt"],
                    "post_id": post_id,
                    "post_url": post_url,
                },
            )
        _persist_xurl_shared_auth_best_effort(home)
        return JobRunResult(
            result={
                "business_slug": slug,
                "provider": "x",
                "post_id": post_id,
                "post_url": post_url,
                "artifact_path": artifacts["artifact"],
                "receipt_path": artifacts["receipt"],
            },
            actual_cost_cents=0,
        )
    except Exception as exc:
        if work_request_id:
            _update_outreach_work_request(
                slug,
                work_request_id,
                status="failed",
                payload_updates={"worker_error": str(exc)},
            )
        raise


# The kind→handler registry the drain consults. New job kinds register here.
HANDLERS: dict[str, jobs.Handler] = {
    "ceo_bootstrap": ceo_bootstrap_handler,
    "ceo_wake": ceo_wake_handler,
    "x.publish_outreach": x_publish_outreach_handler,
}


# ── the drain loop ──────────────────────────────────────────────────────────────────────────────


def drain_tick(
    conn,
    *,
    worker_id: str,
    handlers: Mapping[str, jobs.Handler] | None = None,
    kinds: list[str] | tuple[str, ...] | None = None,
    dispatch: bool = True,
    stop: threading.Event | None = None,
    max_jobs: int | None = None,
) -> dict[str, int]:
    """One drain tick on an open autocommit connection: optionally dispatch due wakes, reclaim stale
    claims, then drain queued jobs through ``jobs.run_one`` until the queue is empty (or ``stop`` is
    set, or ``max_jobs`` reached). Returns counts ``{dispatched, requeued, drained, completed,
    blocked, failed}``. Pure of process concerns (signals, sleeping, reconnect) so it is directly
    testable against a real Postgres connection."""
    handlers = HANDLERS if handlers is None else handlers
    counts = {"dispatched": 0, "requeued": 0, "drained": 0, "completed": 0, "blocked": 0, "failed": 0}

    if dispatch:
        counts["dispatched"] = wakes.dispatch_due_wakes(conn)
    counts["requeued"] = jobs.requeue_stale(conn, older_than_seconds=_STALE_SECONDS, worker_id=worker_id)

    while stop is None or not stop.is_set():
        outcome: JobOutcome | None = jobs.run_one(
            conn, worker_id=worker_id, handlers=handlers, kinds=kinds
        )
        if outcome is None:
            break
        counts["drained"] += 1
        if outcome.status == "completed":
            counts["completed"] += 1
        elif outcome.status == "blocked":
            counts["blocked"] += 1
        elif outcome.status in ("failed", "queued"):  # fail() may requeue (→ 'queued') or give up (→ 'failed')
            counts["failed"] += 1
        _log.info(
            "worker[%s]: job %s kind=%s -> %s (reserved=%dc actual=%dc%s)",
            worker_id,
            outcome.job_id,
            outcome.kind,
            outcome.status,
            outcome.reserved_cents,
            outcome.actual_cents,
            f" reason={outcome.reason}" if outcome.reason else "",
        )
        if max_jobs is not None and counts["drained"] >= max_jobs:
            break

    return counts


def run_worker_loop(
    *,
    worker_id: str | None = None,
    poll_interval: float | None = None,
    dispatch: bool = True,
    kinds: list[str] | tuple[str, ...] | None = None,
    once: bool = False,
    max_jobs: int | None = None,
    database_url: str | None = None,
) -> int:
    """Run the worker process loop until SIGTERM/SIGINT (or ``once``/``max_jobs``). Opens a fresh
    per-tick psycopg connection (autocommit, ``prepare_threshold=None`` — the SAME pgbouncer-safe
    settings as ``runtime_app``) so a dropped connection only costs one tick; reconnects next tick.
    A SIGTERM stops pulling NEW jobs between jobs and exits cleanly — a job killed mid-turn is left
    'running' and reclaimed by ``requeue_stale`` on the next worker (its reservation refunded), so an
    interrupted wake is safe. Returns the total number of jobs drained."""
    import psycopg

    from .core import load_takyon_env
    from .runtime_app import resolve_database_url

    load_takyon_env()
    resolved_url = resolve_database_url(database_url)  # invariant #8: raises if unconfigured
    worker_id = worker_id or f"worker-{socket.gethostname()}-{os.getpid()}"
    interval = poll_interval if poll_interval is not None else _env_float(
        "TAKYON_WORKER_POLL_SECONDS", _DEFAULT_POLL_SECONDS
    )
    concurrency = 1 if once or max_jobs is not None else max(1, _env_int("TAKYON_WORKER_CONCURRENCY", 2))

    stop = threading.Event()

    def _request_stop(signum, _frame):
        _log.info("worker[%s]: signal %s received; finishing current job then stopping", worker_id, signum)
        stop.set()

    import signal

    for _sig in (signal.SIGTERM, signal.SIGINT):
        try:
            signal.signal(_sig, _request_stop)
        except (ValueError, OSError):
            # Not on the main thread (e.g. under a test harness) — skip signal install.
            pass

    def _run_loop(*, thread_worker_id: str, allow_dispatch: bool) -> int:
        import psycopg

        total_drained = 0
        while not stop.is_set():
            try:
                conn = psycopg.connect(resolved_url, autocommit=True, prepare_threshold=None)
            except Exception as exc:  # noqa: BLE001 — transient DB outage must not crash the daemon
                _log.warning(
                    "worker[%s]: DB connect failed (%s); retrying in %.0fs",
                    thread_worker_id,
                    exc,
                    interval,
                )
                stop.wait(interval)
                continue
            try:
                counts = drain_tick(
                    conn,
                    worker_id=thread_worker_id,
                    kinds=kinds,
                    dispatch=allow_dispatch,
                    stop=stop,
                    max_jobs=max_jobs,
                )
                total_drained += counts["drained"]
            except Exception as exc:  # noqa: BLE001 — a tick failure must not crash the daemon
                _log.exception("worker[%s]: tick failed: %s", thread_worker_id, exc)
            finally:
                conn.close()

            if once or (max_jobs is not None and total_drained >= max_jobs):
                break
            stop.wait(interval)
        _log.info("worker[%s]: stopped (drained %d job(s) this run)", thread_worker_id, total_drained)
        return total_drained

    _log.info(
        "worker[%s]: starting (dispatch=%s poll=%.0fs concurrency=%d)",
        worker_id,
        dispatch,
        interval,
        concurrency,
    )
    if concurrency == 1:
        return _run_loop(thread_worker_id=worker_id, allow_dispatch=dispatch)

    totals = [0 for _ in range(concurrency)]
    errors: list[BaseException] = []

    def _thread_main(index: int) -> None:
        thread_worker_id = f"{worker_id}-{index + 1}"
        try:
            totals[index] = _run_loop(
                thread_worker_id=thread_worker_id,
                allow_dispatch=dispatch and index == 0,
            )
        except BaseException as exc:  # pragma: no cover - defensive last resort
            errors.append(exc)
            stop.set()

    threads = [
        threading.Thread(
            target=_thread_main,
            args=(index,),
            name=f"takyon-worker-{index + 1}",
            daemon=True,
        )
        for index in range(concurrency)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    if errors:
        raise errors[0]
    return sum(totals)


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except (TypeError, ValueError):
        _log.warning("worker: invalid %s=%r; using default %.0f", name, raw, default)
        return default


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except (TypeError, ValueError):
        _log.warning("worker: invalid %s=%r; using default %d", name, raw, default)
        return default
