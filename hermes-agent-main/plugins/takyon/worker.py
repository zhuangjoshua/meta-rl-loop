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

import json
import logging
import os
import re
import socket
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from . import composio_distribution, jobs, wakes
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
_X_POST_CHAR_LIMIT = 280


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _truncate_worker_text(value: str, limit: int = 400) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "..."


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


def _split_x_fragment(text: str, *, limit: int = _X_POST_CHAR_LIMIT) -> list[str]:
    remaining = str(text or "").strip()
    parts: list[str] = []
    while remaining:
        if len(remaining) <= limit:
            parts.append(remaining)
            break
        window = remaining[: limit + 1]
        split_at = 0
        for marker, keep in (("\n\n", 0), (". ", 1), ("! ", 1), ("? ", 1), ("; ", 1), (": ", 1), (", ", 1), (" ", 0)):
            idx = window.rfind(marker)
            if idx >= max(limit // 2, 32):
                split_at = idx + keep
                break
        if split_at <= 0:
            split_at = limit
        part = remaining[:split_at].strip()
        if not part:
            part = remaining[:limit].strip()
            split_at = len(part)
        parts.append(part)
        remaining = remaining[split_at:].strip()
    return parts


def _split_x_thread_segments(body: str, *, limit: int = _X_POST_CHAR_LIMIT) -> list[str]:
    text = str(body or "").strip()
    if not text:
        return []
    paragraphs = [paragraph.strip() for paragraph in re.split(r"\n{2,}", text) if paragraph.strip()]
    if not paragraphs:
        return _split_x_fragment(text, limit=limit)
    segments: list[str] = []
    current = ""
    for paragraph in paragraphs:
        for piece in _split_x_fragment(paragraph, limit=limit):
            if not current:
                current = piece
                continue
            candidate = f"{current}\n\n{piece}"
            if len(candidate) <= limit:
                current = candidate
            else:
                segments.append(current)
                current = piece
    if current:
        segments.append(current)
    return segments


def _x_tool_data(payload: Mapping[str, Any] | None) -> Mapping[str, Any]:
    current: Any = payload if isinstance(payload, Mapping) else {}
    for _ in range(4):
        if isinstance(current, Mapping) and isinstance(current.get("data"), Mapping):
            current = current.get("data")
            continue
        break
    return current if isinstance(current, Mapping) else {}


def _extract_x_post_id(payload: Mapping[str, Any] | None) -> str:
    data = _x_tool_data(payload)
    for key in ("id", "rest_id", "post_id", "tweet_id"):
        value = str(data.get(key) or "").strip() if isinstance(data, Mapping) else ""
        if value:
            return value
    return ""


def _extract_x_media_id(payload: Mapping[str, Any] | None) -> str:
    data = _x_tool_data(payload)
    nested_media = data.get("media") if isinstance(data.get("media"), Mapping) else {}
    for source in (data, nested_media):
        for key in ("media_id_string", "media_id", "id"):
            value = str(source.get(key) or "").strip() if isinstance(source, Mapping) else ""
            if value:
                return value
    return ""


def _extract_x_username(payload: Mapping[str, Any] | None) -> str:
    data = _x_tool_data(payload)
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


def _update_work_request(
    slug: str,
    work_request_id: str,
    *,
    status: str,
    payload_updates: Mapping[str, Any] | None = None,
    rewrite_distribution: bool = True,
) -> None:
    """Flip the canonical run row (business_work_requests) for one worker-executed job and emit the
    status event. ``rewrite_distribution`` stays on for outreach kinds (their run state feeds the
    distribution files); worker-executed tool runs (claude.agent_task, product.surface_refresh)
    pass False."""
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
        if rewrite_distribution:
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
    media: list[dict[str, Any]] | None = None,
    credits_charged: int = 0,
    budget_bucket: str = "",
    channel_budget: Mapping[str, Any] | None = None,
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
        "credits_charged": max(0, int(credits_charged or 0)),
        "budget_bucket": str(budget_bucket or payload.get("channel") or "x").strip() or "x",
        "channel_budget": dict(channel_budget or {}),
        "published_at": published_at,
        "sent": True,
        "external_side_effects": "sent",
        "artifact_path": artifact_rel,
        "metadata": dict(metadata),
        "provider_response": dict(provider_response or {}),
    }
    if media:
        receipt_payload["media"] = [dict(item) for item in media]

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


def _extract_reddit_publish_ref(payload: Mapping[str, Any] | None) -> dict[str, str]:
    def _mapping(value: Any) -> Mapping[str, Any]:
        return value if isinstance(value, Mapping) else {}

    current: Any = payload if isinstance(payload, Mapping) else {}
    for _ in range(4):
        if isinstance(current, Mapping) and current.get("data") is not None:
            next_value = current.get("data")
            if next_value is current:
                break
            current = next_value
            continue
        break
    data = _mapping(current)
    json_payload = _mapping(data.get("json"))
    json_data = _mapping(json_payload.get("data"))

    post_id = ""
    for source in (data, json_data):
        for key in ("name", "id"):
            value = str(source.get(key) or "").strip() if isinstance(source, Mapping) else ""
            if value:
                post_id = value
                break
        if post_id:
            break

    post_url = ""
    for source in (data, json_data):
        for key in ("url", "permalink"):
            value = str(source.get(key) or "").strip() if isinstance(source, Mapping) else ""
            if value:
                post_url = value
                break
        if post_url:
            break
    if post_url.startswith("/"):
        post_url = f"https://www.reddit.com{post_url}"
    return {
        "post_id": post_id,
        "post_url": post_url,
    }


def _record_reddit_publish_result(
    slug: str,
    *,
    job_id: str,
    payload: Mapping[str, Any],
    post_id: str,
    post_url: str,
    provider_response: Mapping[str, Any] | None,
    credits_charged: int = 0,
    budget_bucket: str = "",
    channel_budget: Mapping[str, Any] | None = None,
) -> dict[str, str]:
    from .core import TakyonStore

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    safe_post_id = re.sub(r"[^a-zA-Z0-9_-]+", "-", str(post_id or "").strip()).strip("-")
    stem = safe_post_id or str(job_id)
    artifact_rel = f"distribution/local-published/reddit/{timestamp}-{stem}.md"
    receipt_rel = f"metrics/receipts/outreach/{timestamp}-reddit-{stem}.json"
    body = str(payload.get("body") or "").strip()
    subject = str(payload.get("subject") or payload.get("title") or "").strip()
    subreddit = str(payload.get("subreddit") or "").strip()
    post_kind = str(payload.get("post_kind") or "").strip()
    metadata = payload.get("metadata") if isinstance(payload.get("metadata"), Mapping) else {}
    published_at = _utc_now_iso()
    title = _publish_artifact_title(subject, body or subject or subreddit or "Reddit")
    thread_external_id = str(payload.get("thread_external_id") or post_id).strip() or post_id
    artifact_lines = [f"# {title}"]
    if subreddit:
        artifact_lines.extend(["", f"Subreddit: r/{subreddit}"])
    if body:
        artifact_lines.extend(["", body])
    if post_url:
        artifact_lines.extend(["", f"Published: {post_url}"])
    receipt_payload = {
        "provider": "reddit",
        "channel": str(payload.get("channel") or "reddit"),
        "target": payload.get("target"),
        "recipient": payload.get("recipient"),
        "subject": subject,
        "body": body,
        "url": str(payload.get("url") or "").strip(),
        "subreddit": subreddit,
        "post_kind": post_kind,
        "destination_url": payload.get("destination_url"),
        "destination_label": payload.get("destination_label"),
        "thread_external_id": thread_external_id,
        "post_id": post_id,
        "post_url": post_url,
        "credits_charged": max(0, int(credits_charged or 0)),
        "budget_bucket": str(budget_bucket or payload.get("channel") or "reddit").strip() or "reddit",
        "channel_budget": dict(channel_budget or {}),
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
            "source": "reddit",
            "external_id": thread_external_id,
            "title": title,
            "url": post_url,
            "status": "active",
        },
        {
            "action": "conversation.message.record",
            "business": slug,
            "source": "reddit",
            "external_id": post_id,
            "thread_external_id": thread_external_id,
            "thread_title": title,
            "direction": "outbound",
            "author_label": "business",
            "body": body or subject,
            "status": "responded",
            "received_at": published_at,
        },
    ]
    store = TakyonStore()
    store.commit(
        scope=f"business:{slug}",
        operations=operations,
        idempotency_key=f"reddit-publish-artifact:{job_id}:{post_id}",
        reason="worker recorded live Reddit publish receipt",
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


def _refresh_business_surface_after_bootstrap(
    slug: str,
    *,
    job_id: str,
    operator_user_id: str | None = None,
) -> dict[str, Any] | None:
    """After scratch sync-up, refresh the durable product surface if this bootstrap declared one.

    This closes the gap where a bootstrap turn writes final `product/site/*` files late in the turn
    (for example via `business_write_file`) after an earlier worker refresh already ran against an
    incomplete scratch workspace. The durable business root is the source of truth for product-host
    publication, so do one final trusted refresh against that durable state before marking bootstrap
    complete — but only when durable `product/site/*` artifact writes actually happened after the
    most recent successful publish.
    """

    from .core import TakyonStore, handle_business_refresh_product_surface

    def _path_targets_source(candidate: Any, expected_source: str) -> bool:
        normalized_candidate = str(candidate or "").strip().strip("/")
        normalized_source = str(expected_source or "").strip().strip("/")
        if not normalized_candidate or not normalized_source:
            return False
        return normalized_candidate == normalized_source or normalized_candidate.startswith(f"{normalized_source}/")

    def _source_changed_after_publish(
        store: Any,
        *,
        business_slug: str,
        expected_source: str,
        published_at: str,
    ) -> bool:
        timestamp = str(published_at or "").strip()
        if not timestamp:
            return True
        try:
            with store._connect() as conn:
                rows = conn.execute(
                    """
                    SELECT payload_json
                    FROM events
                    WHERE business_slug = ?
                      AND created_at > ?
                      AND event_type IN (?, ?)
                    ORDER BY created_at ASC
                    """,
                    (business_slug, timestamp, "artifact.write", "artifact.patch"),
                ).fetchall()
        except Exception:
            return True
        for row in rows or []:
            raw_payload = ""
            try:
                raw_payload = str(row["payload_json"] or "")
            except Exception:
                raw_payload = ""
            payload = _parse_jsonish_output(raw_payload)
            candidate = payload.get("path") or payload.get("source_path") or payload.get("workspace")
            if _path_targets_source(candidate, expected_source):
                return True
        return False

    store = TakyonStore(operator_user_id=str(operator_user_id or "").strip() or None)
    summary = store.read(scope=f"business:{slug}", query="summary", include=["app"], limit=1)
    app = summary.get("app") if isinstance(summary.get("app"), dict) else {}
    surface = app.get("surface") or app.get("surface_contract") or {}
    if not isinstance(surface, Mapping):
        return None
    source_path = str(surface.get("source_path") or "").strip()
    if not source_path:
        return None
    metadata = surface.get("metadata") if isinstance(surface.get("metadata"), Mapping) else {}
    publish_state = metadata.get("takyon_publish") if isinstance(metadata.get("takyon_publish"), Mapping) else {}
    publish_status = str(
        publish_state.get("status") or surface.get("publish_status") or ""
    ).strip().lower()
    publish_source_path = str(publish_state.get("publish_source_path") or "").strip() or source_path
    published_at = str(
        publish_state.get("published_at") or surface.get("published_at") or ""
    ).strip()
    if (
        publish_status == "published"
        and _path_targets_source(publish_source_path, source_path)
        and not _source_changed_after_publish(
            store,
            business_slug=slug,
            expected_source=source_path,
            published_at=published_at,
        )
    ):
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
        _business_workspace_execution_context,
        _ceo_bootstrap_turn_config,
    )
    from .core import TakyonStore, _bound_operator_task_context

    slug = job.business_slug
    store = TakyonStore()
    summary = store.read(scope=f"business:{slug}", query="summary")
    business = summary.get("business") if isinstance(summary.get("business"), dict) else {}
    active_mode = "live"
    goal = str((job.payload or {}).get("goal") or (business or {}).get("goal") or "").strip()
    business_name = str((business or {}).get("name") or "").strip()
    bootstrap_turn = _ceo_bootstrap_turn_config(
        slug,
        goal,
        active_mode,
        business_name=business_name,
    )
    user_prompt = str(bootstrap_turn.get("user_prompt") or "")
    system_prompt = str(bootstrap_turn.get("ephemeral_system_prompt") or "")
    toolsets = list(bootstrap_turn.get("enabled_toolsets") or ["takyon", "web", "skills"])
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
                task_kind="ceo_bootstrap",
            )
            with _bound_operator_task_context(task_kind="ceo_bootstrap"):
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

    surface_refresh = _refresh_business_surface_after_bootstrap(
        slug,
        job_id=str(job.id),
        operator_user_id=owner_user_id,
    )
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
            operations=[
                {
                    "action": "cron.ensure_ceo_wakeup",
                    "business": slug,
                    "schedule": schedule,
                    "defer_first_run": True,
                }
            ],
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
    from . import business_credits as takyon_business_credits
    from . import core as takyon_core

    payload = job.payload or {}
    slug = job.business_slug
    body = str(payload.get("body") or "").strip()
    if not body:
        raise RuntimeError("x publish job is missing a body")

    work_request_id = str(payload.get("work_request_id") or "").strip()
    reply_to = str(payload.get("thread_external_id") or "").strip()
    reservation_key = f"x-publish:{job.id}"
    reservation: dict[str, Any] | None = None
    credit_result: dict[str, Any] | None = None
    finalized = False
    if work_request_id:
        _update_work_request(
            slug,
            work_request_id,
            status="running",
            payload_updates={"worker_job_id": str(job.id)},
        )

    try:
        reservation = takyon_core._reserve_creative_credits(
            slug,
            action="x_publish_outreach",
            reservation_key=reservation_key,
            budget_bucket="x",
            metadata={
                "business": slug,
                "action": "x_publish_outreach",
                "job_id": str(job.id),
                "work_request_id": work_request_id or None,
                "channel": "x",
                "provider": "x",
                "thread_external_id": reply_to or None,
            },
        )
    except takyon_business_credits.InsufficientCreativeCredits as exc:
        if work_request_id:
            _update_work_request(
                slug,
                work_request_id,
                status="failed",
                payload_updates={"worker_error": str(exc), "budget_bucket": "x"},
            )
        raise RuntimeError(str(exc)) from exc
    except takyon_core.CreativeCreditBudgetExceeded as exc:
        if work_request_id:
            _update_work_request(
                slug,
                work_request_id,
                status="failed",
                payload_updates={
                    "worker_error": str(exc),
                    "budget_bucket": exc.bucket,
                    "channel_budget": exc.channel_budget,
                },
            )
        raise RuntimeError(str(exc)) from exc

    post_id = ""
    thread_posts: list[dict[str, Any]] = []
    try:
        segments = _split_x_thread_segments(body)
        if not segments:
            raise RuntimeError("x publish job is missing a body")
        provider_response: dict[str, Any] = {}
        media_ids: list[str] = []
        media_records: list[dict[str, Any]] = []
        for raw_rel in payload.get("media_paths") or []:
            rel = takyon_core._safe_relpath(str(raw_rel or ""), field="media_paths").as_posix()
            abs_path = takyon_core._store()._resolve_business_file(slug, rel)
            if not abs_path.is_file():
                raise RuntimeError(f"media file not found: {rel}")
            descriptor = composio_distribution.upload_file_descriptor(
                toolkit_slug="twitter",
                tool_slug="TWITTER_UPLOAD_MEDIA",
                file_path=abs_path,
                timeout=180.0,
            )
            response = composio_distribution.twitter_execute_tool(
                "TWITTER_UPLOAD_MEDIA",
                arguments={
                    # [composio-schema] Confirm TWITTER_UPLOAD_MEDIA uses the media file argument name "media".
                    "media": descriptor,
                },
                timeout=180.0,
            )
            media_id = _extract_x_media_id(response)
            if not media_id:
                raise RuntimeError(f"X media upload returned no media id for {rel}")
            media_ids.append(media_id)
            media_records.append({"path": rel, "media_id": media_id})
        current_reply_to = reply_to
        for index, segment in enumerate(segments):
            arguments: dict[str, Any] = {"text": segment}
            if current_reply_to:
                arguments["reply_in_reply_to_tweet_id"] = current_reply_to
            if index == 0 and media_ids:
                # [composio-schema] Confirm TWITTER_CREATION_OF_A_POST uses the flattened media_media_ids argument.
                arguments["media_media_ids"] = list(media_ids)
            response = composio_distribution.twitter_execute_tool(
                "TWITTER_CREATION_OF_A_POST",
                arguments=arguments,
                timeout=120.0,
            )
            current_post_id = _extract_x_post_id(response) or (post_id if post_id else str(job.id))
            if not post_id:
                post_id = current_post_id
                provider_response = dict(response)
            thread_posts.append(
                {
                    "index": index,
                    "post_id": current_post_id,
                    "body": segment,
                    "reply_to": current_reply_to,
                    "media": list(media_records) if index == 0 and media_records else [],
                    "provider_response": dict(response),
                }
            )
            current_reply_to = current_post_id
        if len(thread_posts) > 1:
            provider_response["thread_posts"] = thread_posts
        elif media_records:
            provider_response["media"] = media_records
        credit_result = takyon_core._commit_creative_credits(
            reservation_key,
            action="x_publish_outreach",
            budget_bucket="x",
            metadata={
                "business": slug,
                "action": "x_publish_outreach",
                "job_id": str(job.id),
                "work_request_id": work_request_id or None,
                "channel": "x",
                "provider": "x",
                "post_id": post_id,
                "thread_post_count": len(thread_posts),
            },
        )
        finalized = True
        whoami = composio_distribution.twitter_execute_tool(
            "TWITTER_USER_LOOKUP_ME",
            arguments={"user_fields": ["username"]},
            timeout=30.0,
        )
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
            media=media_records,
            credits_charged=int(
                (credit_result or {}).get("actual_credits")
                or (reservation or {}).get("requested_credits")
                or 0
            ),
            budget_bucket=str(
                (credit_result or {}).get("budget_bucket")
                or (reservation or {}).get("budget_bucket")
                or "x"
            ).strip()
            or "x",
            channel_budget=(credit_result or {}).get("channel_budget"),
        )
        if work_request_id:
            _update_work_request(
                slug,
                work_request_id,
                status="completed",
                payload_updates={
                    "artifact_path": artifacts["artifact"],
                    "receipt_path": artifacts["receipt"],
                    "post_id": post_id,
                    "post_url": post_url,
                    "credits_charged": int(
                        (credit_result or {}).get("actual_credits")
                        or (reservation or {}).get("requested_credits")
                        or 0
                    ),
                    "budget_bucket": str(
                        (credit_result or {}).get("budget_bucket")
                        or (reservation or {}).get("budget_bucket")
                        or "x"
                    ).strip()
                    or "x",
                    "channel_budget": (credit_result or {}).get("channel_budget", {}),
                },
            )
        return JobRunResult(
            result={
                "business_slug": slug,
                "provider": "x",
                "post_id": post_id,
                "post_url": post_url,
                "artifact_path": artifacts["artifact"],
                "receipt_path": artifacts["receipt"],
                "credits_charged": int(
                    (credit_result or {}).get("actual_credits")
                    or (reservation or {}).get("requested_credits")
                    or 0
                ),
                "balance_credits": (credit_result or {}).get("balance_credits"),
                "reserved_credits": (credit_result or {}).get("reserved_credits"),
                "budget_bucket": str(
                    (credit_result or {}).get("budget_bucket")
                    or (reservation or {}).get("budget_bucket")
                    or "x"
                ).strip()
                or "x",
                "channel_budget": (credit_result or {}).get("channel_budget", {}),
            },
            actual_cost_cents=0,
        )
    except Exception as exc:
        finalization_error: Exception | None = None
        if reservation is not None and not finalized:
            try:
                if thread_posts:
                    credit_result = takyon_core._commit_creative_credits(
                        reservation_key,
                        action="x_publish_outreach",
                        budget_bucket="x",
                        metadata={
                            "business": slug,
                            "action": "x_publish_outreach",
                            "status": "partial_failed",
                            "job_id": str(job.id),
                            "work_request_id": work_request_id or None,
                            "channel": "x",
                            "provider": "x",
                            "post_id": post_id or None,
                            "thread_post_count": len(thread_posts),
                            "thread_posts": thread_posts,
                            "error": str(exc),
                        },
                    )
                else:
                    credit_result = takyon_core._release_creative_credits(
                        reservation_key,
                        action="x_publish_outreach",
                        budget_bucket="x",
                        metadata={
                            "business": slug,
                            "action": "x_publish_outreach",
                            "status": "failed",
                            "job_id": str(job.id),
                            "work_request_id": work_request_id or None,
                            "channel": "x",
                            "provider": "x",
                            "error": str(exc),
                        },
                    )
                finalized = True
            except Exception as release_exc:
                finalization_error = release_exc
        if work_request_id:
            _update_work_request(
                slug,
                work_request_id,
                status="failed",
                payload_updates={
                    "worker_error": str(exc),
                    "post_id": post_id or None,
                    "credits_charged": int(
                        (credit_result or {}).get("actual_credits")
                        or ((reservation or {}).get("requested_credits") if thread_posts else 0)
                        or 0
                    ),
                    "budget_bucket": str(
                        (credit_result or {}).get("budget_bucket")
                        or (reservation or {}).get("budget_bucket")
                        or "x"
                    ).strip()
                    or "x",
                    "channel_budget": (credit_result or {}).get("channel_budget", {}),
                },
            )
        if finalization_error is not None:
            raise RuntimeError(
                f"{exc} (credit finalization also failed: {finalization_error})"
            ) from exc
        raise


def reddit_publish_outreach_handler(job: Job) -> JobRunResult:
    from . import business_credits as takyon_business_credits
    from . import core as takyon_core

    payload = job.payload or {}
    slug = job.business_slug
    body = str(payload.get("body") or "").strip()
    title = str(payload.get("title") or payload.get("subject") or "").strip()
    post_kind = str(payload.get("post_kind") or "").strip() or "self"
    subreddit = str(payload.get("subreddit") or "").strip()
    url = str(payload.get("url") or "").strip()
    thread_external_id = str(payload.get("thread_external_id") or "").strip()
    if not thread_external_id and not subreddit:
        raise RuntimeError("reddit publish job is missing a subreddit or thread_external_id")

    work_request_id = str(payload.get("work_request_id") or "").strip()
    reservation_key = f"reddit-publish:{job.id}"
    reservation: dict[str, Any] | None = None
    credit_result: dict[str, Any] | None = None
    finalized = False
    if work_request_id:
        _update_work_request(
            slug,
            work_request_id,
            status="running",
            payload_updates={"worker_job_id": str(job.id)},
        )

    try:
        reservation = takyon_core._reserve_creative_credits(
            slug,
            action="reddit_publish_outreach",
            reservation_key=reservation_key,
            budget_bucket="reddit",
            metadata={
                "business": slug,
                "action": "reddit_publish_outreach",
                "job_id": str(job.id),
                "work_request_id": work_request_id or None,
                "channel": "reddit",
                "provider": "reddit",
                "thread_external_id": thread_external_id or None,
                "subreddit": subreddit or None,
            },
        )
    except takyon_business_credits.InsufficientCreativeCredits as exc:
        if work_request_id:
            _update_work_request(
                slug,
                work_request_id,
                status="failed",
                payload_updates={"worker_error": str(exc), "budget_bucket": "reddit"},
            )
        raise RuntimeError(str(exc)) from exc
    except takyon_core.CreativeCreditBudgetExceeded as exc:
        if work_request_id:
            _update_work_request(
                slug,
                work_request_id,
                status="failed",
                payload_updates={
                    "worker_error": str(exc),
                    "budget_bucket": exc.bucket,
                    "channel_budget": exc.channel_budget,
                },
            )
        raise RuntimeError(str(exc)) from exc

    provider_response: dict[str, Any] = {}
    post_id = ""
    post_url = ""
    try:
        if thread_external_id:
            provider_response = composio_distribution.reddit_execute_tool(
                "REDDIT_POST_REDDIT_COMMENT",
                arguments={
                    # [composio-schema] Confirm REDDIT_POST_REDDIT_COMMENT uses thing_id and text.
                    "thing_id": thread_external_id,
                    "text": body,
                },
                timeout=120.0,
            )
        else:
            arguments: dict[str, Any] = {
                "subreddit": subreddit,
                "title": title,
                # [composio-schema] Confirm REDDIT_CREATE_REDDIT_POST uses kind with values self/link.
                "kind": "self" if post_kind == "self" else "link",
            }
            if post_kind == "self":
                # [composio-schema] Confirm REDDIT_CREATE_REDDIT_POST uses text for self-post bodies.
                arguments["text"] = body
            else:
                arguments["url"] = url
            provider_response = composio_distribution.reddit_execute_tool(
                "REDDIT_CREATE_REDDIT_POST",
                arguments=arguments,
                timeout=120.0,
            )
        publish_ref = _extract_reddit_publish_ref(provider_response)
        post_id = str(publish_ref.get("post_id") or "").strip()
        post_url = str(publish_ref.get("post_url") or "").strip()
        if not post_id:
            raise RuntimeError("Reddit publish returned no post id")

        credit_result = takyon_core._commit_creative_credits(
            reservation_key,
            action="reddit_publish_outreach",
            budget_bucket="reddit",
            metadata={
                "business": slug,
                "action": "reddit_publish_outreach",
                "job_id": str(job.id),
                "work_request_id": work_request_id or None,
                "channel": "reddit",
                "provider": "reddit",
                "post_id": post_id,
                "subreddit": subreddit or None,
                "post_kind": post_kind,
            },
        )
        finalized = True
        artifacts = _record_reddit_publish_result(
            slug,
            job_id=str(job.id),
            payload=payload,
            post_id=post_id,
            post_url=post_url,
            provider_response=provider_response,
            credits_charged=int(
                (credit_result or {}).get("actual_credits")
                or (reservation or {}).get("requested_credits")
                or 0
            ),
            budget_bucket=str(
                (credit_result or {}).get("budget_bucket")
                or (reservation or {}).get("budget_bucket")
                or "reddit"
            ).strip()
            or "reddit",
            channel_budget=(credit_result or {}).get("channel_budget"),
        )
        if work_request_id:
            _update_work_request(
                slug,
                work_request_id,
                status="completed",
                payload_updates={
                    "artifact_path": artifacts["artifact"],
                    "receipt_path": artifacts["receipt"],
                    "post_id": post_id,
                    "post_url": post_url,
                    "credits_charged": int(
                        (credit_result or {}).get("actual_credits")
                        or (reservation or {}).get("requested_credits")
                        or 0
                    ),
                    "budget_bucket": str(
                        (credit_result or {}).get("budget_bucket")
                        or (reservation or {}).get("budget_bucket")
                        or "reddit"
                    ).strip()
                    or "reddit",
                    "channel_budget": (credit_result or {}).get("channel_budget", {}),
                },
            )
        return JobRunResult(
            result={
                "business_slug": slug,
                "provider": "reddit",
                "post_id": post_id,
                "post_url": post_url,
                "artifact_path": artifacts["artifact"],
                "receipt_path": artifacts["receipt"],
                "credits_charged": int(
                    (credit_result or {}).get("actual_credits")
                    or (reservation or {}).get("requested_credits")
                    or 0
                ),
                "balance_credits": (credit_result or {}).get("balance_credits"),
                "reserved_credits": (credit_result or {}).get("reserved_credits"),
                "budget_bucket": str(
                    (credit_result or {}).get("budget_bucket")
                    or (reservation or {}).get("budget_bucket")
                    or "reddit"
                ).strip()
                or "reddit",
                "channel_budget": (credit_result or {}).get("channel_budget", {}),
            },
            actual_cost_cents=0,
        )
    except Exception as exc:
        finalization_error: Exception | None = None
        if reservation is not None and not finalized:
            try:
                if post_id:
                    credit_result = takyon_core._commit_creative_credits(
                        reservation_key,
                        action="reddit_publish_outreach",
                        budget_bucket="reddit",
                        metadata={
                            "business": slug,
                            "action": "reddit_publish_outreach",
                            "status": "partial_failed",
                            "job_id": str(job.id),
                            "work_request_id": work_request_id or None,
                            "channel": "reddit",
                            "provider": "reddit",
                            "post_id": post_id,
                            "post_url": post_url or None,
                            "subreddit": subreddit or None,
                            "post_kind": post_kind,
                            "error": str(exc),
                        },
                    )
                else:
                    credit_result = takyon_core._release_creative_credits(
                        reservation_key,
                        action="reddit_publish_outreach",
                        budget_bucket="reddit",
                        metadata={
                            "business": slug,
                            "action": "reddit_publish_outreach",
                            "status": "failed",
                            "job_id": str(job.id),
                            "work_request_id": work_request_id or None,
                            "channel": "reddit",
                            "provider": "reddit",
                            "subreddit": subreddit or None,
                            "post_kind": post_kind,
                            "error": str(exc),
                        },
                    )
                finalized = True
            except Exception as release_exc:
                finalization_error = release_exc
        if work_request_id:
            _update_work_request(
                slug,
                work_request_id,
                status="failed",
                payload_updates={
                    "worker_error": str(exc),
                    "post_id": post_id or None,
                    "credits_charged": int(
                        (credit_result or {}).get("actual_credits")
                        or ((reservation or {}).get("requested_credits") if post_id else 0)
                        or 0
                    ),
                    "budget_bucket": str(
                        (credit_result or {}).get("budget_bucket")
                        or (reservation or {}).get("budget_bucket")
                        or "reddit"
                    ).strip()
                    or "reddit",
                    "channel_budget": (credit_result or {}).get("channel_budget", {}),
                },
            )
        if finalization_error is not None:
            raise RuntimeError(
                f"{exc} (credit finalization also failed: {finalization_error})"
            ) from exc
        raise


def _operator_tool_task_handler(job: Job, *, tool_name: str, handler_fn) -> JobRunResult:
    """Execute one worker-deferred operator tool run (core._run_operator_task_on_worker enqueued it)
    by calling the EXISTING tool function — no copied logic, the inline path and the worker path are
    one implementation. The work-request row is the canonical run object: flip it running →
    completed/blocked/failed and store the full tool result in its payload (the waiting tool-side
    dispatcher polls that row; the dashboard events stream gets the status event from
    ``_update_work_request``).

    The JOB lifecycle reports on the wrapper execution, not the task outcome: a tool that ran to a
    recorded failed/blocked result still COMPLETES the job (the run row + agent_runs carry the
    truth); only an unrecorded crash raises (job 'failed', max_attempts=1 ⇒ terminal, never an
    expensive double-run). Budget is reserved/settled INSIDE the tool on the operator-budget rail —
    the job payload carries no estimate_cents, so returning actual_cost_cents=0 keeps run_one from
    double-settling."""
    payload = dict(job.payload or {})
    args = payload.get("args") if isinstance(payload.get("args"), Mapping) else {}
    work_request_id = str(payload.get("work_request_id") or "").strip()
    slug = job.business_slug
    if work_request_id:
        _update_work_request(slug, work_request_id, status="running", rewrite_distribution=False)
    owner_user_id = _business_owner_user_id(slug)
    tokens: list[object] = []
    try:
        from gateway.session_context import clear_session_vars, set_session_vars

        from .cli import _business_workspace_execution_context
        from .core import _bound_operator_task_context

        with _business_workspace_execution_context(
            slug,
            operator_user_id=owner_user_id,
            sync_on_exception=True,
        ) as workspace_home:
            tokens = set_session_vars(
                user_id=owner_user_id,
                workspace_root=str(workspace_home or ""),
                business_slug=slug,
                task_kind=tool_name,
            )
            with _bound_operator_task_context(run_id=work_request_id, task_kind=tool_name):
                raw = handler_fn(dict(args))
        result = _parse_jsonish_output(str(raw or ""))
        if not isinstance(result, dict) or not result:
            result = {"success": False, "error": f"{tool_name} returned no parseable result"}
    except Exception as exc:
        if work_request_id:
            _update_work_request(
                slug,
                work_request_id,
                status="failed",
                payload_updates={"result": {"success": False, "error": str(exc)}},
                rewrite_distribution=False,
            )
        raise
    finally:
        if tokens:
            clear_session_vars(tokens)
    status = "completed" if result.get("success") else ("blocked" if result.get("blocked") else "failed")
    if work_request_id:
        _update_work_request(
            slug,
            work_request_id,
            status=status,
            payload_updates={"result": result},
            rewrite_distribution=False,
        )
    return JobRunResult(result={"status": status, "work_request_id": work_request_id or None}, actual_cost_cents=0)


def claude_agent_task_handler(job: Job) -> JobRunResult:
    from .core import handle_business_claude_agent_task

    return _operator_tool_task_handler(
        job, tool_name="business_claude_agent_task", handler_fn=handle_business_claude_agent_task
    )


def product_surface_refresh_handler(job: Job) -> JobRunResult:
    from .core import handle_business_refresh_product_surface

    return _operator_tool_task_handler(
        job, tool_name="business_refresh_product_surface", handler_fn=handle_business_refresh_product_surface
    )


def product_action_handler(job: Job) -> JobRunResult:
    from . import app_actions as takyon_app_actions
    from .core import _store

    payload = job.payload if isinstance(job.payload, Mapping) else {}
    action_name = str(payload.get("action") or "").strip().lower()
    if not action_name:
        raise RuntimeError("product_action job missing action")
    window_key = str(payload.get("window_key") or job.idempotency_key or "").strip()
    if not window_key:
        raise RuntimeError("product_action job missing window key")
    result = takyon_app_actions.execute_scheduled_action(
        _store(),
        business_slug=job.business_slug,
        action_name=action_name,
        window_key=window_key,
    )
    return JobRunResult(result=result, actual_cost_cents=0)


# The kind→handler registry the drain consults. New job kinds register here.
HANDLERS: dict[str, jobs.Handler] = {
    "ceo_bootstrap": ceo_bootstrap_handler,
    "ceo_wake": ceo_wake_handler,
    "x.publish_outreach": x_publish_outreach_handler,
    "reddit.publish_outreach": reddit_publish_outreach_handler,
    "claude.agent_task": claude_agent_task_handler,
    "product.surface_refresh": product_surface_refresh_handler,
    "product_action": product_action_handler,
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
        counts["dispatched"] = wakes.dispatch_due_wakes(conn) + _dispatch_due_action_jobs(conn)
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


def _dispatch_due_action_jobs(conn) -> int:
    from . import app_actions as takyon_app_actions
    from .core import _store

    now = datetime.now(timezone.utc)

    def _enqueue(item: Mapping[str, Any]) -> None:
        jobs.enqueue(
            conn,
            str(item.get("business_slug") or ""),
            "product_action",
            idempotency_key=str(item.get("window_key") or ""),
            payload={
                "action": str(item.get("action_name") or ""),
                "window_key": str(item.get("window_key") or ""),
            },
        )

    return takyon_app_actions.dispatch_due_action_schedules(_store(), now, _enqueue)


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
    # Mark this process as the worker plane: core's worker-deferral dispatcher must run tools INLINE
    # here (the surrounding job is already durable; deferring again would starve the drain threads
    # waiting on their own sub-jobs).
    os.environ["TAKYON_WORKER_PROCESS"] = "1"
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
