"""Postgres-native worker-drain plane — the process that turns queued ``jobs`` rows into real work.

This is the DRAIN half of the Phase-6 worker plane; the enqueue/schedule halves live in ``jobs.py``
(the at-least-once queue + budget-gated ``run_one`` cycle) and ``wakes.py`` (the ``wake_schedules``
cursor + in-DB ``dispatch_due_wakes()``). One long-lived process per deployment ties them together,
each loop tick:

  1. self-dispatches due CEO wakes (``wakes.dispatch_due_wakes``) — so pg_cron is OPTIONAL: a host
     running this worker needs no external scheduler to fire recurring wakes;
  2. reclaims stale claims left by a crashed prior worker (``jobs.requeue_stale``);
  3. drains the queue one job at a time through ``jobs.run_one`` — which keeps the FULL contract:
     ``FOR UPDATE SKIP LOCKED`` claim → flow-A reserve → handler → settle/release, at-least-once, and
     never a fake ``completed`` (a partial/failed turn is ``blocked``/``failed``, invariant #8);
  4. dispatches each job KIND to its handler. Today the active handlers are ``ceo_wake`` — a
     scheduled CEO turn, the Postgres-native replacement for the legacy file-cron
     ``takyon-ceo:<slug>`` job — and ``ceo_bootstrap`` for durable create-time business bootstrap.

Money: ``run_one`` only reserves/settles when the job payload carries ``estimate_cents`` (>0), which
rides onto the job from ``wake_schedules.payload``. The ``ceo_wake`` handler therefore ALWAYS reports
the turn's true model cost (from the agent's own usage accounting) as ``actual_cost_cents`` so the
settle is correct whenever an estimate was reserved; with no estimate the turn runs unmetered and the
reported cost is simply ignored by ``run_one``. No second money path — this reuses flow-A unchanged.

Invariant #8 (no silent fallback): starting the loop with no operator-plane database URL configured
raises loudly via ``resolve_database_url`` — it never half-starts against a phantom queue, and never
quietly falls back to SQLite (there is no SQLite worker; jobs/wakes are Postgres-only).

INERT until deliberately run: importing this module starts nothing, and the tracked
``deploy/argon-alpha-14/takyon-worker.service`` exists but is NOT enabled on the VPS. Recurring wake
execution stays on the legacy file-cron until a host runs ``takyon-cli worker`` (or the unit is
enabled). Activation is a separate, operator-gated step.
"""

from __future__ import annotations

import contextvars
import hashlib
import json
import logging
import os
import re
import socket
import threading
import time
from contextlib import nullcontext
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Mapping, Sequence

from . import app_usage, billing, composio_distribution, jobs, wakes
from .jobs import Job, JobOutcome, JobRunResult

if TYPE_CHECKING:
    from .channel_registry import ChannelPublisher

_log = logging.getLogger("takyon.worker")

# Default tool-iteration ceiling for a single wake turn when the schedule payload does not pin one.
_DEFAULT_MAX_TURNS = 30
# Inactivity (not wall-clock) timeout for one CEO turn: a turn may run for a long time while it is
# actively calling tools / streaming, but a hung API call or stuck tool with NO activity for this
# many seconds is interrupted and the job fails (then retries / requeues). 0 disables the guard.
_DEFAULT_TURN_TIMEOUT = 600.0
_DEFAULT_WAKE_WALL_TIMEOUT = 1800.0
# Idle headroom for the mobile_app bootstrap marathon (see ceo_bootstrap_handler): app build +
# store-signed publish push a single turn well past the web default without being stuck.
_MOBILE_BOOTSTRAP_TURN_TIMEOUT = 1800.0
# A bootstrap is one bounded launch transaction, not an unbounded background agent session.  The
# coding workers inside it retain their own tighter per-call ceilings; these bounds cap the outer
# CEO choreography even while it remains active.  The short completion grace may start only after
# the required product has a durable live outcome,
# never at the earlier landing-only milestone; an absolute ceiling catches runs that never reach
# a terminal product outcome.
_DEFAULT_BOOTSTRAP_WALL_TIMEOUT = 3000.0
_MOBILE_BOOTSTRAP_WALL_TIMEOUT = 3300.0
_DEFAULT_BOOTSTRAP_POST_PUBLISH_GRACE = 60.0
_BOOTSTRAP_COMPLETION_PROBE_INTERVAL = 15.0
# Default queue poll cadence when a tick drains nothing. Drain itself is tight (run_one in a loop).
_DEFAULT_POLL_SECONDS = 15.0
# Reclaim claims older than this from a crashed worker. Keep the worker-loop default aligned with
# jobs.requeue_stale so dead local workers do not strand create/bootstrap jobs for hours.
_STALE_SECONDS = 900
# Fresh /create bootstrap is the operator critical path. A brief upstream model overload should be
# absorbed inside the SAME CEO turn instead of burning the whole bootstrap job's only retry and
# failing the business after a few seconds of bad luck.
_BOOTSTRAP_API_RETRY_FLOOR = 6
# Release product-AI usage holds whose provider call crashed before settle/release.
_APP_USAGE_HOLD_TTL_SECONDS = 3600
_X_POST_CHAR_LIMIT = 280
_SDK_SESSION_RETENTION_SWEEP_INTERVAL_SECONDS = 300.0
_SDK_SESSION_RETENTION_SWEEP_LOCK = threading.Lock()
_SDK_SESSION_RETENTION_NEXT_SWEEP_AT = 0.0

_LAST_SDK_TURN_RECEIPT: contextvars.ContextVar[dict[str, Any] | None] = (
    contextvars.ContextVar("takyon_last_sdk_turn_receipt", default=None)
)


def _consume_sdk_turn_receipt() -> dict[str, Any] | None:
    receipt = _LAST_SDK_TURN_RECEIPT.get()
    _LAST_SDK_TURN_RECEIPT.set(None)
    return dict(receipt) if isinstance(receipt, Mapping) else None


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _truncate_worker_text(value: str, limit: int = 400) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "..."


def _normalize_worker_progress_text(value: Any, *, limit: int = 220) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if not text or text in {"(empty)", "_thinking"}:
        return ""
    return _truncate_worker_text(text, limit=limit)


def _is_ceo_inactivity_timeout(exc: BaseException) -> bool:
    if not isinstance(exc, TimeoutError):
        return False
    return "inactivity limit" in str(exc).lower()


def _open_operator_lifecycle_conn():
    import psycopg

    from .core import load_takyon_env
    from .runtime_app import assert_takyon_pg_role, configure_takyon_pg_session, resolve_database_url

    load_takyon_env()
    resolved_url = resolve_database_url(None, plane="operator")
    conn = psycopg.connect(resolved_url, autocommit=True, prepare_threshold=None)
    assert_takyon_pg_role(conn, "operator")
    configure_takyon_pg_session(conn, bypass=True)
    return conn


def _best_effort_terminalize_owned_timeout(job: Job, *, error: str) -> str | None:
    """Fail/requeue an inactivity-timed-out CEO job immediately if this worker still owns it.

    Normal handler failures are finalized by ``jobs.run_one`` after the handler raises. The inactivity
    watchdog is special: if the worker unwinds badly after timing out a live CEO turn, relying only on
    the outer failure path can leave the durable row ``running`` until the 15-minute stale-claim
    sweeper. This helper mirrors that failure finalization early, under the current claim, so the row
    releases immediately when possible.
    """
    worker_id = str(getattr(job, "locked_by", "") or "").strip()
    job_id = str(getattr(job, "id", "") or "").strip()
    if not worker_id or not job_id:
        return None

    estimate_cents = int(((job.payload or {}).get("estimate_cents", 0) or 0))
    reservation_key = f"job:{job_id}:{int(getattr(job, 'attempts', 0) or 0)}"
    conn = None
    try:
        conn = _open_operator_lifecycle_conn()
        if estimate_cents > 0:
            try:
                billing.release_reservation(conn, reservation_key)
            except Exception as release_exc:  # noqa: BLE001 - row finalization outranks release hiccups
                _log.warning(
                    "worker: reservation release failed during timeout finalization for job %s "
                    "(non-fatal): %s",
                    job_id,
                    release_exc,
                )
        return jobs.fail_if_still_owned(
            conn,
            job_id,
            worker_id=worker_id,
            attempt=int(getattr(job, "attempts", 0) or 0),
            error=error,
            retryable=True,
        )
    except Exception as finalizer_exc:  # noqa: BLE001 - outer run_one path still exists as fallback
        _log.warning(
            "worker: timeout finalizer could not terminalize job %s (non-fatal fallback to run_one): %s",
            job_id,
            finalizer_exc,
        )
        return None
    finally:
        if conn is not None:
            conn.close()


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


def _compose_x_link_reply(destination_url: str, *, label: str = "") -> str:
    """The link reply that ships under an X thread. The takyon-x skill keeps links out of the
    tweet body (X de-boosts body links), so the product URL goes in a reply — this composes it."""
    url = str(destination_url or "").strip()
    label = str(label or "").strip()
    text = f"{label}: {url}" if label else url
    if len(text) > _X_POST_CHAR_LIMIT:
        text = url[:_X_POST_CHAR_LIMIT]
    return text


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
    operator_task = (
        dict(metadata.get("operator_task"))
        if isinstance(metadata.get("operator_task"), Mapping)
        else {}
    )
    if operator_task:
        receipt_payload["operator_task"] = operator_task
    if media:
        receipt_payload["media"] = [dict(item) for item in media]

    receipt_content = json.dumps(receipt_payload, indent=2, sort_keys=True) + "\n"
    receipt_sha256 = hashlib.sha256(receipt_content.encode("utf-8")).hexdigest()
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
            "content": receipt_content,
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
    task_kind = str(operator_task.get("task_kind") or "").strip().lower()
    bootstrap_run_id = str(operator_task.get("run_id") or "").strip()
    try:
        bootstrap_attempt = int(operator_task.get("attempt") or 0)
    except (TypeError, ValueError):
        bootstrap_attempt = 0
    if task_kind == "ceo_bootstrap" and bootstrap_run_id and bootstrap_attempt > 0:
        operations.append(
            {
                "action": "event.record",
                "business": slug,
                "scope": f"business:{slug}",
                "event_type": "bootstrap.x_launch.outcome",
                "payload": {
                    "status": "published",
                    "operator_task": {
                        "task_kind": task_kind,
                        "run_id": bootstrap_run_id,
                        "attempt": bootstrap_attempt,
                    },
                    "post_id": post_id,
                    "post_url": post_url,
                    "receipt_path": receipt_rel,
                    "receipt_sha256": receipt_sha256,
                    "receipt_sent": True,
                    "external_side_effects": "sent",
                    "x_worker_job_id": str(job_id),
                    "source": "x_publish_receipt",
                    "recorded_at": published_at,
                },
            }
        )
    # Record the receipt + conversation durably. Under the 2-thread worker, a concurrent commit
    # for another job re-materializes this business's local cache mirror with delete_local=True,
    # which can wipe the workspace dir mid-apply and surface as a transient FileNotFoundError. The
    # control-plane DB write is idempotent (keyed by the idempotency_key below), so retrying the
    # whole commit on that ENOENT is safe: the next attempt re-materializes and re-writes the
    # artifacts. This makes the X-publish receipt/conversation persist even when a peer thread
    # wipes the mirror concurrently. (The mirror flock that used to serialize this deadlocked the
    # worker and was removed; see core._business_mirror_lock.)
    idem = f"x-publish-artifact:{job_id}:{post_id}"
    last_enoent: FileNotFoundError | None = None
    for _attempt in range(5):
        store = TakyonStore()
        try:
            store.commit(
                scope=f"business:{slug}",
                operations=operations,
                idempotency_key=idem,
                reason="worker recorded live X publish receipt",
                actor="worker",
            )
            return {"artifact": artifact_rel, "receipt": receipt_rel}
        except FileNotFoundError as exc:
            last_enoent = exc
            time.sleep(0.1 * (_attempt + 1))
            continue
    if last_enoent is not None:
        raise last_enoent
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
    extra: Mapping[str, Any] | None = None,
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
    if isinstance(extra, Mapping) and extra:
        payload.update(
            {
                str(key): value
                for key, value in extra.items()
                if value not in (None, [], {})
            }
        )
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


def _record_ceo_turn_chat(slug: str, text: str) -> None:
    """Record ONE model response (assistant message) as a business.ceo_turn chat bubble.

    The chat IS the turn, streamed: this is called once per model response — each
    mid-loop assistant message via the interim tap as it completes, plus the final
    response at turn end — so a long bootstrap reads like a live agent conversation
    (a message per step), not one end-of-turn summary.

    Pure read of text the agent ALREADY produced and appended to its own messages —
    it never mutates the agent's persisted context, so a bubble can never affect a
    future Hermes turn. Stored on the business scope (not /runtime) so the
    overview/boot builders pick it up; the read-side ``_takyon_ceo_chat_stream``
    lightly cleans it. Best-effort: a failure here must NOT fail the turn (the caller
    is on the billing/settlement path)."""
    from .core import TakyonStore

    body = str(text or "").strip()[:4000]
    if not body:
        return
    try:
        store = TakyonStore()
        # Dedup: the per-message interim tap and the post-turn final call can both
        # present the same last text. Skip a write whose text equals the most recent
        # turn bubble so an identical final response isn't double-posted.
        try:
            recent = store.read_ceo_turn_events(slug, limit=1)
            if recent:
                last_payload = recent[0].get("payload")
                if isinstance(last_payload, str):
                    last_payload = json.loads(last_payload)
                if isinstance(last_payload, dict) and str(last_payload.get("text") or "").strip() == body:
                    return
        except Exception:
            pass
        with store._connect() as conn:
            store._record_event(
                conn,
                scope=f"business:{slug}",
                business_slug=slug,
                event_type="business.ceo_turn",
                payload={"text": body},
            )
    except Exception as exc:  # pragma: no cover - best-effort chat mirror only
        _log.debug("failed to record CEO turn chat event for %s: %s", slug, exc)


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
        return {
            "status": "passed",
            "source_path": source_path,
            "publish": {
                "status": "published",
                "public_url": str(
                    publish_state.get("public_url")
                    or surface.get("public_url")
                    or f"https://{slug}.coscale.app/"
                ).strip(),
                "publish_target": str(
                    publish_state.get("publish_target")
                    or surface.get("publish_target")
                    or f"{slug}.coscale.app"
                ).strip(),
                "published_at": published_at,
            },
            "note": "already_published_no_source_changes",
        }

    tokens: list[object] = []
    try:
        if str(operator_user_id or "").strip():
            from gateway.session_context import clear_session_vars, set_session_vars

            tokens = set_session_vars(
                user_id=str(operator_user_id or "").strip(),
                task_kind="ceo_bootstrap",
            )
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
    finally:
        if tokens:
            clear_session_vars(tokens)
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
        self._last_nested_activity = ""
        self._last_tool_generating = ""
        self._stream_buffer = ""
        self._stream_open = False
        self._stream_last_emit = 0.0
        self._reasoning_buf = ""
        self._last_activity_monotonic = time.monotonic()
        self._active_tool_calls: set[str] = set()
        self._active_tool_lock = threading.Lock()

    def _touch_activity(self) -> None:
        self._last_activity_monotonic = time.monotonic()

    def seconds_since_activity(self) -> float:
        return max(0.0, time.monotonic() - self._last_activity_monotonic)

    def has_active_tool(self) -> bool:
        with self._active_tool_lock:
            return bool(self._active_tool_calls)

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
        self._touch_activity()

    def emit(self, line: str) -> None:
        text = str(line or "").strip()
        if not text:
            return
        self._touch_activity()
        self.finish_stream()
        _record_runtime_event(
            self.slug,
            kind=self.kind,
            status="output",
            detail=text,
            line=text,
            command=self.command,
        )
        self._touch_activity()

    def stream_delta(self, delta: Any) -> None:
        if delta is None:
            self.finish_stream()
            return
        text = str(delta or "")
        if not text:
            return
        self._touch_activity()
        self._stream_open = True
        self._stream_buffer += text
        # CEO model deltas are transport detail, not durable event boundaries. Keep the current
        # assistant message buffered until a tool starts or the response completes; otherwise a
        # sparse upstream stream becomes dozens of broken chat fragments interleaved with transport
        # heartbeats. The overall /create stream remains live through curated updates and tool/task
        # milestones, while each assistant message is emitted exactly once and intact.

    def _flush_stream_buffer(self) -> None:
        if not self._stream_buffer:
            return
        chunk = self._stream_buffer
        self._stream_buffer = ""
        self._stream_last_emit = time.monotonic()
        _record_runtime_event(
            self.slug,
            kind=self.kind,
            status="output",
            detail=chunk,
            line=chunk,
            command=self.command,
            extra={"stream": "message_delta"},
        )

    def finish_stream(self) -> None:
        if not self._stream_open:
            return
        self._flush_stream_buffer()
        _record_runtime_event(
            self.slug,
            kind=self.kind,
            status="output",
            detail="",
            line="",
            command=self.command,
            extra={"stream": "message_flush"},
        )
        self._stream_open = False

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
        with self._active_tool_lock:
            self._active_tool_calls.add(str(tool_call_id or tool_name))
        self._touch_activity()
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
        if (
            text == "receiving stream response"
            or text == "waiting for non-streaming API response"
            or text.startswith("waiting for non-streaming response (")
        ):
            self._touch_activity()
            return
        if not text or text == self._last_activity:
            return
        self._touch_activity()
        self._last_activity = text
        self.emit(f"agent -> {text}")

    def nested_activity(self, line: str) -> None:
        text = _normalize_worker_progress_text(line)
        self._touch_activity()
        if not text or text == self._last_nested_activity:
            return
        self._last_nested_activity = text
        self.emit(f"worker -> {text}")

    def _flush_reasoning(self) -> None:
        self._reasoning_buf = ""

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
        self._touch_activity()
        if event_type == "tool.started":
            self._flush_reasoning()
            self._last_tool_generating = ""
            suffix = f" · {preview}" if preview else ""
            self.emit(f"tool started -> {name}{suffix}")
        elif event_type == "tool.completed":
            self._flush_reasoning()
            duration = kwargs.get("duration")
            suffix = f" · {duration:.1f}s" if isinstance(duration, (int, float)) else ""
            self.emit(f"tool completed -> {name}{suffix}")
        elif event_type in {"reasoning.available", "_thinking"}:
            # Never persist raw reasoning to the durable events plane. Curated CEO updates are the
            # only customer-visible planning rail.
            return

    def tool_completed(
        self,
        tool_id: str,
        name: str,
        args: dict[str, object],
        result: object,
    ) -> None:
        from .turn_runtime import _tool_progress_lines

        with self._active_tool_lock:
            self._active_tool_calls.discard(str(tool_id or name))
        self._touch_activity()
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


def _ceo_turn_bound_reason(
    *,
    now: float,
    started_at: float,
    wall_clock_limit: float,
    completion_observed_at: float | None,
    completion_grace_seconds: float,
    active_external_work: bool = False,
) -> str:
    absolute_limit = max(0.0, float(wall_clock_limit or 0.0))
    if absolute_limit and now - started_at >= absolute_limit:
        return f"reached {int(absolute_limit)}s bootstrap wall-clock limit"
    if active_external_work:
        return ""
    completion_grace = max(0.0, float(completion_grace_seconds or 0.0))
    if (
        completion_observed_at is not None
        and completion_grace
        and now - completion_observed_at >= completion_grace
    ):
        return (
            "durable bootstrap product outcome remained complete for "
            f"{int(completion_grace)}s grace window"
        )
    return ""


_WORKER_AGENT_RUNTIME_ENV = "TAKYON_WORKER_AGENT_RUNTIME"
_WORKER_AGENT_RUNTIME_HERMES = "hermes"
_WORKER_AGENT_RUNTIME_SDK = "claude-agent-sdk"


def _selected_worker_agent_runtime() -> str:
    """Return the explicit canary runtime; never fall back after an SDK error."""

    selected = str(
        os.getenv(_WORKER_AGENT_RUNTIME_ENV) or _WORKER_AGENT_RUNTIME_HERMES
    ).strip().lower()
    if selected not in {
        _WORKER_AGENT_RUNTIME_HERMES,
        _WORKER_AGENT_RUNTIME_SDK,
    }:
        raise RuntimeError(
            f"unsupported {_WORKER_AGENT_RUNTIME_ENV}={selected!r}; expected "
            f"{_WORKER_AGENT_RUNTIME_HERMES!r} or {_WORKER_AGENT_RUNTIME_SDK!r}"
        )
    return selected


def _job_billing_mode(job: Job, handler: jobs.Handler) -> str:
    """Bind queue billing to the selected handler runtime before any hold."""

    if handler in {ceo_bootstrap_handler, ceo_wake_handler}:
        if _selected_worker_agent_runtime() == _WORKER_AGENT_RUNTIME_SDK:
            return jobs.BILLING_MODE_PROVIDER_BROKER
    return jobs.BILLING_MODE_JOB_RESERVATION


def _sdk_turn_budget_usd(
    *, turn_config: Mapping[str, Any], payload: Mapping[str, Any]
) -> float:
    """Resolve an explicit high-level turn budget; there is no SDK default."""

    raw = (
        turn_config.get("max_budget_usd")
        or payload.get("max_budget_usd")
        or os.getenv("TAKYON_PRIMARY_AGENT_MAX_BUDGET_USD")
    )
    try:
        value = float(raw or 0)
    except (TypeError, ValueError):
        value = 0.0
    if value <= 0:
        raise RuntimeError(
            "primary SDK turn requires explicit max_budget_usd in its turn policy"
        )
    return value


def _run_hermes_ceo_turn(
    *,
    slug: str,
    system_prompt: str,
    user_prompt: str,
    toolsets: list[str],
    max_turns: int,
    inactivity_limit: float,
    wall_clock_limit: float = 0.0,
    completion_probe: Callable[[], bool] | None = None,
    completion_grace_seconds: float = 0.0,
    external_activity_probe: Callable[[], bool] | None = None,
    terminal_review_probe: Callable[[], bool] | None = None,
    hard_stop_callback: Callable[[str], None] | None = None,
    api_retry_floor: int = 0,
    progress: _RuntimeProgress | None = None,
    record_final_chat: bool = True,
) -> tuple[str, float, str, bool]:
    """Run ONE CEO wake turn for ``business:<slug>`` and return ``(final_response, cost_usd,
    cost_status, turn_completed)``.

    Built to be the SAME CEO the interactive shell runs (``cli._run_agent``): the stable
    ``prompts/ceo.md`` as the ephemeral system prompt, the per-business wake instructions
    (``core._ceo_cron_prompt``) as the user turn, the wake toolsets (``core._ceo_cron_toolsets``),
    and the model/provider resolved the same way (``cli._require_agent_model_config`` — which raises
    loudly if unconfigured, invariant #8). The difference vs. the shell path is purely operational:
    no interactive operator-envelope wrapping, a daemon-grade inactivity timeout (mirrors
    ``cron/scheduler.py``), and the turn's true cost extracted for billing settlement.

    Raises on a failed/aborted turn (so ``jobs.run_one`` releases the reservation and fails/requeues
    rather than recording a fake completion)."""
    import concurrent.futures
    import contextvars

    from takyon_cli.runtime_provider import resolve_runtime_provider

    from .turn_runtime import (
        _read_model_config,
        _reasoning_progress_callback,
        _require_agent_model_config,
        _takyon_reasoning_config,
    )
    from .core import (
        TakyonStore,
        _active_operator_task_receipt_context,
        _bound_claude_worker_activity,
        _claude_worker_activity_run_identity,
        load_takyon_env,
    )
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
            "reasoning_config": _takyon_reasoning_config(),
            "reasoning_callback": _reasoning_progress_callback(progress) if progress is not None else None,
            "tool_progress_callback": progress.tool_progress if progress is not None else None,
            "tool_start_callback": progress.tool_started if progress is not None else None,
            "tool_gen_callback": progress.tool_generating if progress is not None else None,
            "tool_complete_callback": progress.tool_completed if progress is not None else None,
        },
    )
    agent._memory_nudge_interval = 0
    agent._skill_nudge_interval = 0
    agent.suppress_status_output = True
    # Worker CEO turns (bootstrap/wake) block for many minutes on child tool calls —
    # business_claude_agent_task builds run 5-30 minutes — so the default 5m prompt-cache TTL
    # expires between iterations and every post-build API call re-reads the whole prefix cold.
    # The 1h TTL (GA, plain cache_control {"ttl": "1h"} in the request body — no beta header,
    # so it passes through the safebox proxy untouched) keeps the prefix warm across those
    # gaps. Economics: 1h writes cost 2x vs 1.25x, but a bootstrap re-reads the prefix dozens
    # of times, so it pays for itself within the first build gap. Env-overridable escape hatch.
    if getattr(agent, "_use_prompt_caching", False):
        _worker_cache_ttl = str(os.getenv("TAKYON_WORKER_CACHE_TTL", "") or "").strip() or "1h"
        if _worker_cache_ttl in {"5m", "1h"}:
            agent._cache_ttl = _worker_cache_ttl
    agent.activity_callback = progress.activity if progress is not None else None
    if api_retry_floor > 0:
        try:
            agent._api_max_retries = max(
                int(getattr(agent, "_api_max_retries", 0) or 0),
                int(api_retry_floor),
            )
        except (TypeError, ValueError):
            agent._api_max_retries = int(api_retry_floor)
    # Stream each model response (mid-loop assistant message) to the business chat as
    # its own bubble, the instant it completes — so a long bootstrap/wake reads like a
    # live agent conversation (a message per step), not a single end-of-turn summary.
    # Display-only by construction: _emit_interim_assistant_message (run_agent.py) only
    # forwards text the loop ALREADY appended to messages; it never mutates the agent's
    # context, so this cannot affect a future Hermes turn. The final no-tool-call
    # response is still recorded once more after the turn returns (deduped).
    agent.interim_assistant_callback = (
        (lambda text, already_streamed=False: _record_ceo_turn_chat(slug, text))
        if record_final_chat
        else None
    )

    # Run on a worker thread and watch the agent's own activity tracker, so a hung turn is caught
    # without killing a healthy long-running one. (Mirrors cron/scheduler.py's inactivity guard.)
    limit = inactivity_limit if inactivity_limit and inactivity_limit > 0 else None
    def _claude_worker_activity(line: str) -> None:
        if progress is None:
            return
        nested = getattr(progress, "nested_activity", None)
        if callable(nested):
            nested(line)

    turn_started = time.time()
    turn_started_monotonic = time.monotonic()
    watchdog_activity_run_id, watchdog_activity_attempt = (
        _claude_worker_activity_run_identity()
    )

    def _emit_turn_event(
        status: str,
        *,
        error: str | None = None,
        completed: bool | None = None,
        response_head: str | None = None,
    ) -> None:
        """Turn-level slice of the cost/log ledger (operator_cost_events, migration 0070).

        Fires on EVERY outcome — success, failure, timeout — because a failed turn still burned
        real tokens and that partial spend is exactly what post-hoc debugging needs. Carries the
        agent's reply head so a task is debuggable from the ledger alone (the full transcript
        stays in ``events``/``business.ceo_turn``). Best-effort by construction."""
        try:
            from . import cost_events

            payload: dict[str, Any] = {
                "api_calls": int(getattr(agent, "session_api_calls", 0) or 0),
            }
            if completed is not None:
                payload["turn_completed"] = completed
            if response_head:
                payload["response_head"] = response_head[:500]
            ctx_vars = cost_events.operator_context()
            cost_now = float(getattr(agent, "session_estimated_cost_usd", 0.0) or 0.0)
            cost_events.record_operator_event_autoconn(
                event_kind=cost_events.KIND_TURN,
                business_slug=slug,
                user_id=ctx_vars.get("user_id") or None,
                job_id=ctx_vars.get("run_id") or None,
                run_id=ctx_vars.get("run_id") or None,
                session_id=str(getattr(agent, "session_id", "") or "") or None,
                task_kind=ctx_vars.get("task_kind") or None,
                name=ctx_vars.get("task_kind") or "ceo_turn",
                status=status,
                provider=str(getattr(agent, "provider", "") or "") or None,
                model=str(getattr(agent, "model", "") or "") or None,
                input_tokens=int(getattr(agent, "session_input_tokens", 0) or 0),
                output_tokens=int(getattr(agent, "session_output_tokens", 0) or 0),
                cache_read_tokens=int(getattr(agent, "session_cache_read_tokens", 0) or 0),
                cache_write_tokens=int(getattr(agent, "session_cache_write_tokens", 0) or 0),
                reasoning_tokens=int(getattr(agent, "session_reasoning_tokens", 0) or 0),
                cost_microusd=int(round(cost_now * 1_000_000)),
                cost_status=str(getattr(agent, "session_cost_status", "unknown") or "unknown"),
                duration_ms=int((time.time() - turn_started) * 1000),
                error=error,
                payload=payload,
            )
        except Exception:  # noqa: BLE001 — observability must never break the turn
            pass

    worker_activity_binding = (
        _bound_claude_worker_activity(_claude_worker_activity)
        if progress is not None
        else nullcontext()
    )
    # The plugin registry loads Takyon handlers under ``takyon_plugins.takyon.core``, a twin
    # module with separate ContextVars. Discovery has completed by the time the agent is built;
    # mirror the exact canonical parent identity before copying context into the agent thread.
    registered_tool_context_binding = nullcontext()
    operator_task_context = _active_operator_task_receipt_context()
    if operator_task_context:
        import sys

        registered_core = sys.modules.get("takyon_plugins.takyon.core")
        registered_binder = getattr(
            registered_core, "_bound_operator_task_context", None
        )
        if callable(registered_binder):
            registered_tool_context_binding = registered_binder(
                **operator_task_context
            )
    with worker_activity_binding, registered_tool_context_binding:
        pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        ctx = contextvars.copy_context()
        run_kwargs = (
            {"stream_callback": progress.stream_delta}
            if progress is not None and record_final_chat
            else {}
        )
        future = pool.submit(ctx.run, agent.run_conversation, user_prompt, **run_kwargs)
        timed_out = False
        ownership_lost_reason = ""
        bounded_stop_reason = ""
        completion_observed_at: float | None = None
        next_completion_probe_at = turn_started_monotonic
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
                    active_claim = jobs.current_job_claim()
                    if active_claim is not None and active_claim.lost:
                        ownership_lost_reason = active_claim.reason
                        break
                    idle = 0.0
                    if hasattr(agent, "get_activity_summary"):
                        try:
                            idle = float(agent.get_activity_summary().get("seconds_since_activity", 0.0))
                        except Exception:
                            idle = 0.0
                    if progress is not None:
                        try:
                            idle = min(idle, float(progress.seconds_since_activity()))
                        except Exception:
                            pass
                    # Context-free worker clock: the Claude-worker stderr reader stamps the exact
                    # (business, durable run, attempt) directly, so a lost activity-sink binding
                    # cannot false-kill a healthy run. Never fold in "any worker" activity: the Mac
                    # and VPS pools run several businesses concurrently, and B must not keep A alive
                    # or suppress A's wall-clock bound.
                    claude_worker_idle = float("inf")
                    try:
                        from .core import claude_worker_seconds_since_activity

                        claude_worker_idle = float(
                            claude_worker_seconds_since_activity(
                                slug,
                                run_id=watchdog_activity_run_id,
                                attempt=watchdog_activity_attempt,
                            )
                        )
                        idle = min(idle, claude_worker_idle)
                    except Exception:
                        pass
                    now_monotonic = time.monotonic()
                    if callable(terminal_review_probe):
                        try:
                            if bool(terminal_review_probe()):
                                bounded_stop_reason = (
                                    "durable bootstrap blocker requires human review"
                                )
                                break
                        except Exception as exc:
                            # Unknown proof state cannot authorize another model/tool iteration.
                            # Interrupt and join this turn; the outer handler pins the parent claim
                            # while retrying the durable read before any continuation can start.
                            bounded_stop_reason = (
                                f"human-review proof read unavailable: {exc}"
                            )
                            break
                    active_tool_probe = getattr(progress, "has_active_tool", None)
                    active_tool = bool(
                        callable(active_tool_probe) and active_tool_probe()
                    )
                    active_external_work = claude_worker_idle < 60.0 or active_tool
                    if active_tool:
                        idle = 0.0
                    # A delegated child job has its own durable claim heartbeat.  Consult it when
                    # the in-process clocks look quiet or the outer wall limit is due.  This covers
                    # a child running in another worker process: the parent CEO claim must remain
                    # live until that bounded child terminates, never requeue alongside it.
                    if not active_external_work and callable(external_activity_probe) and (
                        idle >= min(30.0, float(limit) / 4.0)
                        or (
                            wall_clock_limit
                            and now_monotonic - turn_started_monotonic
                            >= float(wall_clock_limit)
                        )
                    ):
                        try:
                            active_external_work = bool(external_activity_probe())
                        except Exception:
                            # Fail closed against duplicate builds: if canonical child-liveness
                            # state cannot be read, do not expire/requeue the parent on that poll.
                            # The child subprocess keeps its own hard timeout and the next poll
                            # retries the durable claim read.
                            active_external_work = True
                        if active_external_work:
                            idle = 0.0
                    if callable(completion_probe) and now_monotonic >= next_completion_probe_at:
                        next_completion_probe_at = (
                            now_monotonic + _BOOTSTRAP_COMPLETION_PROBE_INTERVAL
                        )
                        try:
                            if bool(completion_probe()) and completion_observed_at is None:
                                completion_observed_at = now_monotonic
                        except Exception:
                            # A transient read failure must not stop a live launch.  The absolute
                            # ceiling remains, and the next bounded probe retries canonical state.
                            pass
                    if active_external_work and completion_observed_at is not None:
                        # The post-publish grace is for CEO launch bookkeeping, not time spent
                        # blocked on a still-running delegated child.  Pause it until that child's
                        # durable claim ends; the absolute outer ceiling is applied immediately
                        # afterward if the child itself ran beyond it.
                        completion_observed_at = now_monotonic
                    bounded_stop_reason = _ceo_turn_bound_reason(
                        now=now_monotonic,
                        started_at=turn_started_monotonic,
                        wall_clock_limit=wall_clock_limit,
                        completion_observed_at=completion_observed_at,
                        completion_grace_seconds=completion_grace_seconds,
                        active_external_work=active_external_work,
                    )
                    if bounded_stop_reason:
                        if (
                            bounded_stop_reason.startswith("reached ")
                            and callable(hard_stop_callback)
                        ):
                            try:
                                hard_stop_callback(bounded_stop_reason)
                            except Exception as exc:
                                # Unknown durable stop state cannot authorize returning/requeueing.
                                # Keep the parent claim pinned and retry the stop request next poll.
                                _log.warning(
                                    "worker: bootstrap hard-stop request failed for business:%s; "
                                    "pinning the parent claim: %s",
                                    slug,
                                    exc,
                                )
                                bounded_stop_reason = ""
                                continue
                        break
                    if idle >= limit:
                        timed_out = True
                        break
        finally:
            # ``Future.cancel`` cannot stop a running thread.  Interrupt the agent first, then JOIN
            # the thread before this handler is allowed to raise/return.  The old wait=False path
            # requeued the durable bootstrap while its agent/tool thread was still editing the same
            # business (BriefVault/AppKitProof), creating overlapping revisions and stale-base
            # conflicts.  run_one continues heartbeating the parent claim while this join waits.
            if timed_out or bounded_stop_reason or ownership_lost_reason:
                if hasattr(agent, "interrupt"):
                    if ownership_lost_reason:
                        agent.interrupt("CEO worker claim lost")
                    elif timed_out:
                        agent.interrupt("CEO wake timed out (inactivity)")
                    else:
                        agent.interrupt(f"CEO bootstrap stopped: {bounded_stop_reason}")
                interrupt_started = time.monotonic()
                next_pinned_log = interrupt_started + 30.0
                while not future.done():
                    concurrent.futures.wait({future}, timeout=2.0)
                    if future.done():
                        break
                    if time.monotonic() >= next_pinned_log:
                        # Python cannot safely kill a live thread.  This is an explicit fail-closed
                        # posture, not a silent wrapper hang: retain/heartbeat the current claim and
                        # forbid requeue until the agent/tool thread acknowledges interrupt.  Its
                        # subprocess/container has a separately enforced hard deadline + process-
                        # group reap, and every durable write is claim-generation fenced.
                        _log.error(
                            "worker: interrupted CEO thread for business:%s still alive after %.0fs; "
                            "pinning claim and refusing requeue until it exits",
                            slug,
                            time.monotonic() - interrupt_started,
                        )
                        next_pinned_log = time.monotonic() + 30.0
                try:
                    future.result()
                except Exception:
                    # The requested interrupt normally surfaces as an exception from the agent
                    # thread.  Joining it is the invariant; its interrupt exception is represented
                    # by the explicit timeout/bounded/lost-claim outcome below.
                    pass
            if progress is not None:
                progress.finish_stream()
            pool.shutdown(wait=True, cancel_futures=True)

    if ownership_lost_reason:
        raise jobs.JobClaimLost(
            f"CEO turn for business:{slug} lost its exact worker claim: {ownership_lost_reason}"
        )

    if timed_out:
        _emit_turn_event(
            "timeout",
            error=f"idle past {int(limit)}s inactivity limit",
        )
        raise TimeoutError(
            f"CEO wake for business:{slug} idle past {int(limit)}s inactivity limit"
        )

    if bounded_stop_reason:
        final_response = f"Launch work stopped at its bounded runtime: {bounded_stop_reason}."
        if record_final_chat:
            _record_ceo_turn_chat(slug, final_response)
        cost_usd = float(getattr(agent, "session_estimated_cost_usd", 0.0) or 0.0)
        cost_status = str(getattr(agent, "session_cost_status", "unknown") or "unknown")
        _emit_turn_event(
            "bounded",
            error=bounded_stop_reason,
            completed=False,
            response_head=final_response,
        )
        # The bootstrap handler resolves this incomplete turn against durable publish/actions
        # truth.  A complete live product settles once; a pre-publish cap fails/requeues normally.
        return final_response, cost_usd, cost_status, False

    if not isinstance(result, dict):
        raise RuntimeError(
            f"agent.run_conversation returned {type(result).__name__} instead of dict for "
            f"business:{slug}"
        )
    # A turn that reported failure must NOT be billed or marked completed — raise so run_one
    # releases and fails/requeues (invariant #8). BUT exhausting the iteration budget is NOT a
    # failure: at the cap the loop force-summarizes (turn_exit_reason='max_iterations_reached'),
    # so completed=False there means "ran out of calls", not "the work failed". Surface that via
    # the returned `turn_completed` so a done-gated caller (bootstrap) can judge real success by
    # durable state (did the surface publish?) instead of the raw iteration count.
    hit_iteration_cap = str(result.get("turn_exit_reason") or "").startswith("max_iterations")
    if result.get("failed") is True or (result.get("completed") is False and not hit_iteration_cap):
        turn_error = str(
            result.get("error") or (result.get("final_response") or "").strip() or "CEO wake reported failure"
        )
        _emit_turn_event("error", error=turn_error, completed=False)
        raise RuntimeError(turn_error)

    final_response = str(result.get("final_response") or "")
    # The chat IS the turn: record this wake/bootstrap turn's own reply as one chat
    # bubble (business.ceo_turn). Display-only mirror of final_response — never feeds
    # back into the agent's context.
    if record_final_chat:
        _record_ceo_turn_chat(slug, final_response)
    cost_usd = float(getattr(agent, "session_estimated_cost_usd", 0.0) or 0.0)
    cost_status = str(getattr(agent, "session_cost_status", "unknown") or "unknown")
    # turn_completed is False when the loop hit the iteration cap (a clean finish under the cap
    # sets it True). Callers use it to tell "finished" from "ran out of budget"; the bootstrap
    # handler resolves the latter against the product's publish (done-gate) state.
    turn_completed = bool(result.get("completed"))
    _emit_turn_event("ok", completed=turn_completed, response_head=final_response)
    return final_response, cost_usd, cost_status, turn_completed


def _run_claude_sdk_ceo_turn(
    *,
    slug: str,
    system_prompt: str,
    user_prompt: str,
    toolsets: list[str],
    max_turns: int,
    max_budget_usd: float,
    effort: str,
    inactivity_limit: float,
    sdk_session_id: str,
    sdk_resume_session: bool,
    sdk_epoch: str,
    wall_clock_limit: float = 0.0,
    completion_probe: Callable[[], bool] | None = None,
    completion_grace_seconds: float = 0.0,
    external_activity_probe: Callable[[], bool] | None = None,
    terminal_review_probe: Callable[[], bool] | None = None,
    hard_stop_callback: Callable[[str], None] | None = None,
    progress: _RuntimeProgress | None = None,
    sdk_allowed_tools: frozenset[str] | None = None,
    sdk_tool_receipt_callback: Callable[[str, Mapping[str, Any], str], None] | None = None,
    record_final_chat: bool = True,
) -> tuple[str, float, str, bool]:
    """Run one SDK turn with the same Python-owned orchestration gates."""

    from gateway.session_context import get_session_env

    from . import cost_events
    from .claude_sdk_runtime import (
        ClaudeSdkProcessStopped,
        run_primary_sdk_subprocess,
        stable_sdk_session_id,
    )
    from .claude_sdk_sessions import PostgresClaudeSdkSessionStore
    from .core import _active_operator_task_receipt_context

    owner_user_id = _business_owner_user_id(slug)
    stable_session = stable_sdk_session_id(sdk_session_id)
    workspace_root = str(
        get_session_env("TAKYON_SESSION_WORKSPACE_ROOT", "") or ""
    ).strip()
    if not workspace_root:
        raise RuntimeError(
            "primary SDK CEO turn requires the bound business workspace"
        )
    task_context = _active_operator_task_receipt_context() or {}
    task_kind = str(task_context.get("task_kind") or "ceo_wake").strip().lower()
    try:
        invocation_mode = {
            "ceo_bootstrap": "bootstrap",
            "ceo_wake": "wake",
        }[task_kind]
    except KeyError as exc:
        raise RuntimeError(
            f"primary SDK CEO turn has unsupported task kind {task_kind!r}"
        ) from exc
    task_id = str(task_context.get("run_id") or sdk_session_id or "").strip()
    session_store = PostgresClaudeSdkSessionStore(
        operator_user_id=owner_user_id,
        business_slug=slug,
    )
    started_at = time.time()
    started_monotonic = time.monotonic()
    completion_observed_at: float | None = None
    next_completion_probe_at = started_monotonic
    bounded_stop_reason = ""
    _LAST_SDK_TURN_RECEIPT.set(None)

    def active_work() -> bool:
        active_tool_probe = getattr(progress, "has_active_tool", None)
        if callable(active_tool_probe) and bool(active_tool_probe()):
            return True
        if callable(external_activity_probe):
            return bool(external_activity_probe())
        return False

    def stop_probe(_elapsed: float, _idle: float) -> str | None:
        nonlocal completion_observed_at, next_completion_probe_at
        active_claim = jobs.current_job_claim()
        if active_claim is not None and active_claim.lost:
            return f"claim_lost:{active_claim.reason}"
        if callable(terminal_review_probe):
            try:
                if bool(terminal_review_probe()):
                    return "durable bootstrap blocker requires human review"
            except Exception as exc:
                return f"human-review proof read unavailable: {exc}"
        now = time.monotonic()
        if callable(completion_probe) and now >= next_completion_probe_at:
            next_completion_probe_at = now + _BOOTSTRAP_COMPLETION_PROBE_INTERVAL
            try:
                if bool(completion_probe()) and completion_observed_at is None:
                    completion_observed_at = now
            except Exception:
                pass
        external_work = active_work()
        if external_work and completion_observed_at is not None:
            completion_observed_at = now
        reason = _ceo_turn_bound_reason(
            now=now,
            started_at=started_monotonic,
            wall_clock_limit=wall_clock_limit,
            completion_observed_at=completion_observed_at,
            completion_grace_seconds=completion_grace_seconds,
            active_external_work=external_work,
        )
        if reason and reason.startswith("reached ") and callable(hard_stop_callback):
            try:
                hard_stop_callback(reason)
            except Exception as exc:
                _log.warning(
                    "worker: SDK bootstrap hard-stop request failed for business:%s; "
                    "pinning the parent claim: %s",
                    slug,
                    exc,
                )
                return None
        return reason or None

    def on_progress(event: Mapping[str, Any]) -> None:
        if progress is None:
            return
        kind = str(event.get("kind") or "runtime").strip()
        status = str(event.get("status") or "running").strip()
        detail = _normalize_worker_progress_text(event.get("detail"), limit=4000)
        trace = event.get("trace") if isinstance(event.get("trace"), Mapping) else {}
        if kind == "assistant" and status == "output":
            # Match the prior Hermes live-chat contract: each completed
            # tool-continuing assistant message is visible immediately, while a
            # phase-internal final summary stays private when record_final_chat
            # is disabled. Wake/interactive turns expose their final response;
            # the post-turn write below deduplicates that same last message.
            message_role = str(trace.get("message_role") or "").strip()
            if detail and (record_final_chat or message_role == "interim"):
                _record_ceo_turn_chat(slug, detail)
            return
        if kind == "tool":
            # Parent bridge callbacks produce the canonical scoped tool trace.
            return
        if kind == "skill":
            skill_name = str(trace.get("skill_name") or "").strip()
            entry_key = str(trace.get("entry_key") or f"skill:{skill_name}").strip()
            progress._record_trace(
                entry_kind="skill",
                entry_key=entry_key,
                label=skill_name or "Skill",
                detail=detail or f"{skill_name or 'Skill'} {status}.",
                status=("running" if status == "started" else status),
                tool_name="Skill",
                skill_name=skill_name,
                summary=detail if status in {"completed", "failed"} else "",
            )
            return
        if detail and kind in {"session", "provider", "turn"}:
            progress.emit(f"{kind} -> {detail}")

    def tool_completed(
        _tool_use_id: str,
        name: str,
        args: Mapping[str, Any],
        result: str,
    ) -> None:
        if progress is not None:
            progress.tool_completed(_tool_use_id, name, args, result)
        if callable(sdk_tool_receipt_callback):
            sdk_tool_receipt_callback(name, args, result)

    disabled_toolsets = [
        "cronjob",
        "messaging",
        "clarify",
        "memory",
        "session_search",
        "terminal",
        "file",
        "browser",
        "code_execution",
    ]
    registered_tool_context_binding = nullcontext()
    if task_context:
        import sys

        registered_core = sys.modules.get("takyon_plugins.takyon.core")
        registered_binder = getattr(
            registered_core, "_bound_operator_task_context", None
        )
        if callable(registered_binder):
            registered_tool_context_binding = registered_binder(**task_context)
    try:
        with registered_tool_context_binding:
            result = run_primary_sdk_subprocess(
                business=slug,
                operator_user_id=owner_user_id,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                # HANDOFF's reviewed runtime baseline includes the bounded
                # todo tool in every mode. Bootstrap's legacy Hermes config did
                # not list that toolset, so add it before the exact policy
                # filter; the compiled allowlist still prevents any widening.
                enabled_toolsets=list(dict.fromkeys([*toolsets, "todo"])),
                disabled_toolsets=disabled_toolsets,
                invocation_allowed_tools=sdk_allowed_tools,
                workspace_root=workspace_root,
                session_id=stable_session,
                resume_session=sdk_resume_session,
                session_store=session_store,
                task_id=task_id,
                mode=invocation_mode,
                epoch=str(sdk_epoch or task_context.get("task_kind") or "ceo_wake"),
                max_turns=max_turns,
                max_budget_usd=max_budget_usd,
                effort=effort,
                inactivity_limit=inactivity_limit,
                stop_probe=stop_probe,
                active_work_probe=active_work,
                progress_callback=on_progress,
                on_tool_start=(progress.tool_started if progress is not None else None),
                on_tool_complete=tool_completed,
            )
    except ClaudeSdkProcessStopped as exc:
        if exc.reason.startswith("claim_lost:"):
            raise jobs.JobClaimLost(
                f"CEO turn for business:{slug} lost its exact worker claim: "
                f"{exc.reason.split(':', 1)[1]}"
            ) from exc
        if exc.inactivity_timeout:
            raise TimeoutError(
                f"CEO wake for business:{slug} idle past "
                f"{int(inactivity_limit)}s inactivity limit"
            ) from exc
        bounded_stop_reason = exc.reason
        _LAST_SDK_TURN_RECEIPT.set(
            {
                "session_id": stable_session,
                "resumed": bool(sdk_resume_session),
                "mode": str(task_context.get("task_kind") or "ceo_turn"),
                "epoch": str(sdk_epoch or task_context.get("task_kind") or "ceo_turn"),
                "status": "stopped",
                "stop_reason": bounded_stop_reason,
            }
        )
        final_response = (
            f"Launch work stopped at its bounded runtime: {bounded_stop_reason}."
        )
        if record_final_chat:
            _record_ceo_turn_chat(slug, final_response)
        return final_response, 0.0, "unknown", False

    final_response = str(result.get("summary") or "").strip()
    if not final_response:
        raise RuntimeError(
            f"primary SDK returned no final response for business:{slug}"
        )
    if record_final_chat:
        _record_ceo_turn_chat(slug, final_response)
    raw_cost = result.get("total_cost_usd")
    cost_usd = float(raw_cost) if isinstance(raw_cost, (int, float)) else 0.0
    cost_status = "actual" if isinstance(raw_cost, (int, float)) else "unknown"
    usage = result.get("usage") if isinstance(result.get("usage"), Mapping) else {}
    _LAST_SDK_TURN_RECEIPT.set(
        {
            "session_id": stable_session,
            "resumed": bool(sdk_resume_session),
            "mode": str(task_context.get("task_kind") or "ceo_turn"),
            "epoch": str(sdk_epoch or task_context.get("task_kind") or "ceo_turn"),
            "status": "completed",
            "model": str(result.get("model") or ""),
            "actual_models": list(result.get("actual_models") or []),
            "usage": dict(usage),
            "total_cost_usd": cost_usd,
            "skill_receipt": result.get("skill_receipt"),
            "invocation_id": str(result.get("invocation_id") or ""),
            "invocation_total_ceiling_microusd": int(
                result.get("invocation_total_ceiling_microusd") or 0
            ),
            "invocation_per_call_ceiling_microusd": int(
                result.get("invocation_per_call_ceiling_microusd") or 0
            ),
        }
    )
    try:
        cost_events.record_operator_event_autoconn(
            event_kind=cost_events.KIND_TURN,
            business_slug=slug,
            user_id=owner_user_id,
            job_id=task_id or None,
            run_id=task_id or None,
            session_id=stable_session,
            task_kind=str(task_context.get("task_kind") or "ceo_turn"),
            name=str(task_context.get("task_kind") or "ceo_turn"),
            status="ok",
            provider="safebox",
            model=str(result.get("model") or "") or None,
            input_tokens=int(usage.get("input_tokens") or 0),
            output_tokens=int(usage.get("output_tokens") or 0),
            cache_read_tokens=int(usage.get("cache_read_input_tokens") or 0),
            cache_write_tokens=int(usage.get("cache_creation_input_tokens") or 0),
            cost_microusd=int(round(cost_usd * 1_000_000)),
            cost_status=cost_status,
            duration_ms=int((time.time() - started_at) * 1000),
            payload={
                "turn_completed": True,
                "billing_mode": jobs.BILLING_MODE_PROVIDER_BROKER,
                "skill_receipt": result.get("skill_receipt"),
                "response_head": final_response[:500],
            },
        )
    except Exception:
        pass
    return final_response, cost_usd, cost_status, True


def _run_ceo_turn(
    *,
    slug: str,
    system_prompt: str,
    user_prompt: str,
    toolsets: list[str],
    max_turns: int,
    inactivity_limit: float,
    wall_clock_limit: float = 0.0,
    completion_probe: Callable[[], bool] | None = None,
    completion_grace_seconds: float = 0.0,
    external_activity_probe: Callable[[], bool] | None = None,
    terminal_review_probe: Callable[[], bool] | None = None,
    hard_stop_callback: Callable[[str], None] | None = None,
    api_retry_floor: int = 0,
    progress: _RuntimeProgress | None = None,
    agent_runtime: str = _WORKER_AGENT_RUNTIME_HERMES,
    sdk_session_id: str = "",
    sdk_resume_session: bool = False,
    sdk_max_budget_usd: float = 0.0,
    sdk_effort: str = "high",
    sdk_epoch: str = "",
    sdk_allowed_tools: frozenset[str] | None = None,
    sdk_tool_receipt_callback: Callable[[str, Mapping[str, Any], str], None] | None = None,
    record_final_chat: bool = True,
) -> tuple[str, float, str, bool]:
    selected = str(agent_runtime or "").strip().lower()
    if selected == _WORKER_AGENT_RUNTIME_HERMES:
        _LAST_SDK_TURN_RECEIPT.set(None)
        return _run_hermes_ceo_turn(
            slug=slug,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            toolsets=toolsets,
            max_turns=max_turns,
            inactivity_limit=inactivity_limit,
            wall_clock_limit=wall_clock_limit,
            completion_probe=completion_probe,
            completion_grace_seconds=completion_grace_seconds,
            external_activity_probe=external_activity_probe,
            terminal_review_probe=terminal_review_probe,
            hard_stop_callback=hard_stop_callback,
            api_retry_floor=api_retry_floor,
            progress=progress,
            record_final_chat=record_final_chat,
        )
    if selected != _WORKER_AGENT_RUNTIME_SDK:
        raise RuntimeError(f"unsupported CEO agent runtime {selected!r}")
    if not sdk_session_id:
        raise RuntimeError("primary SDK CEO turn requires a stable session ID")
    if sdk_max_budget_usd <= 0:
        raise RuntimeError("primary SDK CEO turn requires an explicit budget")
    return _run_claude_sdk_ceo_turn(
        slug=slug,
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        toolsets=toolsets,
        max_turns=max_turns,
        max_budget_usd=sdk_max_budget_usd,
        effort=sdk_effort,
        inactivity_limit=inactivity_limit,
        sdk_session_id=sdk_session_id,
        sdk_resume_session=sdk_resume_session,
        sdk_epoch=sdk_epoch,
        sdk_allowed_tools=sdk_allowed_tools,
        sdk_tool_receipt_callback=sdk_tool_receipt_callback,
        record_final_chat=record_final_chat,
        wall_clock_limit=wall_clock_limit,
        completion_probe=completion_probe,
        completion_grace_seconds=completion_grace_seconds,
        external_activity_probe=external_activity_probe,
        terminal_review_probe=terminal_review_probe,
        hard_stop_callback=hard_stop_callback,
        progress=progress,
    )


def _business_owner_user_id(slug: str) -> str:
    """Resolve the durable owner of ``business:<slug>`` for a worker job that must bind the operator
    identity of the user who created it.

    Every worker handler (ceo_bootstrap, ceo_wake, deferred operator tools) binds the session to this
    user id; an EMPTY or unreadable value here is the upstream cause of the two create-time failures
    we have to keep out: a build session that binds an empty user then raises
    "operator identity required: no operator user is bound to this session", and a session that binds
    a non-empty user against a row that is not yet visible then raises "business:<slug> does not
    exist". Both reduce to: the businesses row must be durably visible AND carry a real owner before
    the job binds identity.

    Dashboard create commits the businesses row (with owner = the Auth0 principal) and reads it back
    BEFORE it enqueues the bootstrap job, so by the time a worker claims the job the row is committed.
    But create and the worker run on separate Postgres connections, and a job can be claimed within
    milliseconds of the enqueue commit. To make this robust against that brief read-after-write lag,
    poll a fresh short-lived connection a few times before giving up — then FAIL LOUDLY with the exact
    slug rather than returning "" (which would silently unbind the whole build session). A loud raise
    here turns into a retryable job failure (jobs.run_one) instead of a confusing tool-level identity
    error mid-build."""
    from .core import TakyonError, TakyonStore

    last_exc: Exception | None = None
    owner = ""
    # Bounded read-after-write retry: ~0.1s + 0.2s + 0.4s + 0.8s ≈ 1.5s total before failing, which
    # comfortably covers cross-connection commit visibility without stalling a healthy job.
    for attempt in range(5):
        try:
            store = TakyonStore()
            with store._connect() as conn:
                business = store._ensure_business(conn, slug)
            owner = str(business.get("owner_user_id") or "").strip()
            if owner:
                return owner
            # Row exists but has no owner — never bind an empty operator (that is exactly the
            # "operator identity required" failure). Treat as a transient miss and retry; if it is
            # still empty after the window, fall through to the loud raise below.
            last_exc = TakyonError(f"business:{slug} has no owner_user_id yet")
        except TakyonError as exc:
            # _ensure_business raises "business not found: <slug>" when the row is not yet visible to
            # this fresh connection. That is the durability race — retry within the window.
            last_exc = exc
        if attempt < 4:
            time.sleep(0.1 * (2**attempt))

    raise TakyonError(
        f"cannot bind operator identity for business:{slug}: owner_user_id is unresolved after "
        f"read-after-write retries ({last_exc})"
    )


def _bootstrap_real_http_actions(store: Any, slug: str) -> set[str]:
    """Real HTTP action files present for a bootstrap-built product surface."""
    try:
        from . import app_actions as takyon_app_actions
        from .core import _surface_product_workflow_shape
    except Exception:
        from plugins.takyon import app_actions as takyon_app_actions
        from plugins.takyon.core import _surface_product_workflow_shape

    surface: dict[str, Any] = {}
    source_path = "product/site"
    try:
        if hasattr(store, "_connect") and hasattr(store, "_app_surface_contract"):
            with store._connect() as conn:
                loaded_surface = store._app_surface_contract(conn, slug)
            if isinstance(loaded_surface, dict):
                surface = loaded_surface
                source_path = str(surface.get("source_path") or "").strip() or source_path
    except Exception:
        surface = {}
    try:
        site_root = store._business_root(slug) / source_path
    except Exception:
        return set()
    surface_with_workflow = {
        **surface,
        "product_workflow": _surface_product_workflow_shape(surface),
    }
    try:
        return takyon_app_actions.site_http_action_names(site_root, surface_with_workflow)
    except Exception:
        return set()


def _bootstrap_live_action_execution_verification(store: Any, slug: str) -> dict[str, Any]:
    """Read current-build signed-in action proof; this is not full browser-workflow proof."""
    try:
        from .core import _requested_live_action_execution_verification_state
    except Exception:
        from plugins.takyon.core import _requested_live_action_execution_verification_state

    try:
        with store._connect() as conn:
            surface = store._app_surface_contract(conn, slug)
            source_path = str((surface or {}).get("source_path") or "product/site").strip()
            root = store._business_root(slug) / source_path
            return _requested_live_action_execution_verification_state(
                conn,
                business=slug,
                root=root,
                surface=surface if isinstance(surface, dict) else {},
            )
    except Exception as exc:
        return {
            "action_execution_required": True,
            "status": "pending",
            "live_build_id": "",
            "actions": [],
            "verified_action": "",
            "verified_at": "",
            "receipt_path": "",
            "blocker": f"live action execution verification evidence could not be read: {exc}",
        }


def _bootstrap_has_durable_live_product(
    store: Any,
    slug: str,
    *,
    workflow_requested: bool,
) -> bool:
    """Whether the required product milestone is durably live.

    For workflow-required web businesses this is also the early completion predicate because the
    action gate proves the final product pass published. Landing-only businesses finish naturally.
    """
    try:
        with store._connect() as conn:
            surface = store._app_surface_contract(conn, slug)
    except Exception:
        return False
    if not isinstance(surface, Mapping):
        return False
    metadata = surface.get("metadata") if isinstance(surface.get("metadata"), Mapping) else {}
    publish = metadata.get("takyon_publish") if isinstance(metadata.get("takyon_publish"), Mapping) else {}
    publish_status = str(
        publish.get("status") or surface.get("publish_status") or ""
    ).strip().lower()
    if publish_status != "published":
        return False
    if not bool(metadata.get("bootstrap_final_product_pass_required")):
        return False
    baseline_build_id = str(
        metadata.get("bootstrap_final_product_baseline_build_id") or ""
    ).strip().lower()
    live_build_id = str(surface.get("live_build_id") or "").strip().lower()
    if not baseline_build_id or not live_build_id or live_build_id == baseline_build_id:
        return False
    if workflow_requested and not _bootstrap_real_http_actions(store, slug):
        return False
    return True


def _bootstrap_phase_authoritative_evidence(
    store: Any,
    run: Any,
    phase: str,
    *,
    workflow_requested: bool,
    archetype: str,
) -> Any | None:
    """Validate one phase from durable runtime truth; assistant prose is ignored."""

    from .bootstrap_phases import AuthoritativePhaseEvidence, PHASE_REQUIRED_SKILLS

    slug = str(run.business_slug)
    owner = str(run.owner_user_id)
    receipts = list(run.phase_receipts.get(phase) or [])
    if phase == "preflight":
        with store._connect() as conn:
            business = store._ensure_business(conn, slug)
        if str((business or {}).get("owner_user_id") or "") != owner:
            raise RuntimeError("bootstrap preflight business ownership changed")
        return AuthoritativePhaseEvidence(
            "business-row", {"business_slug": slug, "owner_user_id": owner}
        )

    required_skills = PHASE_REQUIRED_SKILLS.get(phase, frozenset())
    if phase == "mobile" and str(archetype or "").strip().lower() != "mobile_app":
        required_skills = frozenset()
    invoked_skills: set[str] = set()
    for receipt in receipts:
        if (
            not isinstance(receipt, Mapping)
            or receipt.get("tool") != "__primary_agent_runtime__"
            or receipt.get("status") != "completed"
        ):
            continue
        raw_invoked = receipt.get("skills_invoked")
        if not isinstance(raw_invoked, Sequence) or isinstance(
            raw_invoked, (str, bytes, bytearray)
        ):
            continue
        invoked_skills.update(
            str(skill).strip().split(":", 1)[-1]
            for skill in raw_invoked
            if str(skill or "").strip()
        )
    if not required_skills <= invoked_skills:
        return None

    root = store._business_root(slug)
    if phase == "brief":
        path = root / "research" / "strategy.md"
        try:
            body = path.read_text(encoding="utf-8").strip()
        except OSError:
            return None
        if len(body) < 80:
            return None
        # Business creation seeds this exact two-line skeleton before the
        # bootstrap starts. A long operator goal can make that placeholder
        # exceed the byte threshold, but it is not the Taste-authored offer,
        # audience, positioning, and tone brief required by this phase.
        meaningful_lines = [line.strip() for line in body.splitlines() if line.strip()]
        if (
            len(meaningful_lines) == 2
            and meaningful_lines[0].startswith("# ")
            and meaningful_lines[1].lower().startswith("goal:")
        ):
            return None
        return AuthoritativePhaseEvidence(
            "workspace-artifact",
            {
                "path": "research/strategy.md",
                "sha256": hashlib.sha256(body.encode()).hexdigest(),
                "bytes": len(body.encode()),
            },
        )

    if phase in {"surface", "landing_build_publish", "final_workflow_build_publish"}:
        try:
            with store._connect() as conn:
                surface = store._app_surface_contract(conn, slug)
        except Exception:
            return None
        if not isinstance(surface, Mapping):
            return None
        if phase == "surface":
            runtime_features = {
                str(item).strip().lower() for item in surface.get("runtime_features") or []
            }
            routes = {
                str(item.get("path") if isinstance(item, Mapping) else item).strip()
                for item in surface.get("routes") or []
            }
            if str(surface.get("source_path") or "").strip("/") != "product/site":
                return None
            if not {"auth", "account", "profile", "checkout"} <= runtime_features:
                return None
            if not {"/", "/app", "/app/profile"} <= routes:
                return None
            return AuthoritativePhaseEvidence(
                "app-surface-contract",
                {
                    "source_path": "product/site",
                    "runtime_features": sorted(runtime_features),
                    "routes": sorted(routes),
                },
            )
        if phase == "landing_build_publish":
            metadata = surface.get("metadata") if isinstance(surface.get("metadata"), Mapping) else {}
            publish = metadata.get("takyon_publish") if isinstance(metadata.get("takyon_publish"), Mapping) else {}
            status = str(publish.get("status") or surface.get("publish_status") or "").lower()
            build_id = str(surface.get("live_build_id") or "").strip()
            public_url = str(publish.get("public_url") or surface.get("public_url") or "").strip()
            if status != "published" or not build_id or not public_url:
                return None
            return AuthoritativePhaseEvidence(
                "live-product-publication",
                {"build_id": build_id, "public_url": public_url, "status": status},
            )
        if not _bootstrap_has_durable_live_product(
            store, slug, workflow_requested=workflow_requested
        ):
            return None
        return AuthoritativePhaseEvidence(
            "final-product-done-predicate",
            {
                "workflow_requested": workflow_requested,
                "real_http_actions": sorted(_bootstrap_real_http_actions(store, slug)),
                "live_build_id": str(surface.get("live_build_id") or ""),
                "required_skills_invoked": sorted(required_skills),
            },
        )

    if phase in {"search", "logo"}:
        rel = (
            f"product/seo/search-console/{slug}/receipt.json"
            if phase == "search"
            else f"product/brand/logos/{slug}/receipt.json"
        )
        expected_key = str(
            run.phase_idempotency[phase]["register" if phase == "search" else "generate"]
        )
        receipt: dict[str, Any] = {}
        try:
            loaded = json.loads((root / rel).read_text(encoding="utf-8"))
            receipt = dict(loaded) if isinstance(loaded, Mapping) else {}
        except (OSError, ValueError):
            receipt = {}
        if receipt and str(receipt.get("idempotency_key") or "") == expected_key:
            status = str(receipt.get("status") or "").lower()
            success = bool(receipt.get("success"))
            allowed_blocker = status.startswith("blocked") if phase == "search" else (
                "insufficient" in status or "unconfigured" in status
            )
            if success or allowed_blocker:
                return AuthoritativePhaseEvidence(
                    "tool-receipt-artifact",
                    {"path": rel, "status": status, "success": success},
                )
        for observed in reversed(receipts):
            status = str(observed.get("status") or "").lower()
            error = str(observed.get("error") or "").lower()
            allowed_blocker = (
                status.startswith("blocked")
                if phase == "search"
                else ("insufficient" in status + error or "unconfigured" in status + error)
            )
            if bool(observed.get("success")) or allowed_blocker:
                return AuthoritativePhaseEvidence(
                    "parent-tool-bridge-receipt", dict(observed)
                )
        return None

    if phase == "mobile":
        if str(archetype or "").strip().lower() != "mobile_app":
            return AuthoritativePhaseEvidence("archetype-policy", {"skipped": True})
        from .core import _creative_credit_reservation_outcome

        for key in dict(run.phase_idempotency.get("mobile") or {}).values():
            outcome = _creative_credit_reservation_outcome(
                store, slug, f"mobile-release:{slug}:preview:{key}"
            )
            metadata = outcome.get("metadata") if isinstance(outcome.get("metadata"), Mapping) else {}
            if outcome.get("state") == "committed" and str(metadata.get("build_id") or ""):
                return AuthoritativePhaseEvidence(
                    "creative-credit-ledger",
                    {"idempotency_key": key, "build_id": str(metadata.get("build_id"))},
                )
        for observed in reversed(receipts):
            if bool(observed.get("success")) and str(observed.get("build_id") or ""):
                return AuthoritativePhaseEvidence("parent-tool-bridge-receipt", dict(observed))
            blocker = (
                str(observed.get("status") or "") + " " + str(observed.get("error") or "")
            ).lower()
            if any(
                token in blocker
                for token in (
                    "greenlight_preflight_failed",
                    "compliance",
                    "insufficient",
                    "credit",
                    "eas_builder_unconfigured",
                )
            ):
                return AuthoritativePhaseEvidence("parent-tool-bridge-receipt", dict(observed))
        return None

    if phase == "finalize":
        if not _bootstrap_has_durable_live_product(
            store, slug, workflow_requested=workflow_requested
        ):
            return None
        if not any(
            receipt.get("tool") == "business_post_operator_update"
            and bool(receipt.get("success"))
            for receipt in receipts
        ):
            return None
        if not any(
            receipt.get("tool") == "__primary_agent_runtime__"
            and receipt.get("status") == "completed"
            for receipt in receipts
        ):
            return None
        return AuthoritativePhaseEvidence(
            "final-done-gate-and-update-receipt",
            {
                "product_complete": True,
                "operator_update_recorded": True,
                "sdk_turn_completed": True,
            },
        )
    return None


def _post_bootstrap_phase_operator_update(
    phase_store: Any,
    run: Any,
    phase: str,
    *,
    completed: bool = False,
) -> None:
    """Post one deterministic, deduplicated customer milestone per phase."""

    from .core import handle_business_post_operator_update

    copy = {
        "brief": (
            "Defining your offer",
            "I’m turning your idea into a clear offer, audience, and product direction.",
        ),
        "surface": (
            "Shaping the product",
            "I’m setting the customer journey and the core access and account experience.",
        ),
        "landing_build_publish": (
            "Designing your public launch",
            "I’m crafting the branded public experience and putting the first polished version online.",
        ),
        "search": (
            "Connecting search visibility",
            "The public experience is live; I’m connecting it to search discovery now.",
        ),
        "logo": (
            "Creating the brand mark",
            "I’m creating the visual identity that will carry through the public and customer experience.",
        ),
        "final_workflow_build_publish": (
            "Building the customer experience",
            "I’m finishing the signed-in product experience and the real customer workflow.",
        ),
        "mobile": (
            "Building the iOS app",
            "I’m shaping and packaging the real mobile product for its first signed build.",
        ),
        "finalize": (
            "Finishing launch checks",
            "The product work is in place; I’m checking the final business and launch outcomes.",
        ),
    }
    if phase not in copy:
        raise RuntimeError(f"bootstrap phase {phase!r} has no customer update")
    phase_index = {
        name: index for index, name in enumerate(
            (
                "brief",
                "surface",
                "landing_build_publish",
                "search",
                "logo",
                "final_workflow_build_publish",
                "mobile",
                "finalize",
            )
        )
    }
    current_index = phase_index[phase] + (1 if completed else 0)
    mobile = str(run.immutable_inputs.get("archetype") or "").lower() == "mobile_app"
    milestones = [
        ("Define the offer", "RESEARCH", 0),
        ("Design and publish the brand", "PRODUCT", 4),
        ("Build the customer experience", "PRODUCT", 5),
        *(([("Build the iOS app", "PRODUCT", 6)]) if mobile else []),
        ("Complete launch checks", "LAUNCH", 7),
    ]
    milestone_payload = []
    for title, category, completion_index in milestones:
        if current_index > completion_index:
            status = "completed"
        elif current_index <= completion_index and (
            (title == "Define the offer" and current_index == 0)
            or (title == "Design and publish the brand" and 1 <= current_index <= 4)
            or (title == "Build the customer experience" and current_index == 5)
            or (title == "Build the iOS app" and current_index == 6)
            or (title == "Complete launch checks" and current_index == 7)
        ):
            status = "running"
        else:
            status = "queued"
        milestone_payload.append(
            {"title": title, "category": category, "status": status}
        )
    if completed:
        headline = "Your launch is ready"
        summary = (
            "The core launch work is complete, the product is published, and the final checks are finished."
        )
    else:
        headline, summary = copy[phase]
    update_key_name = "operator_update_completed" if completed else "operator_update"
    args = {
        "business": run.business_slug,
        "headline": headline,
        "summary": summary,
        "milestones": milestone_payload,
        "idempotency_key": str(run.phase_idempotency[phase][update_key_name]),
        "reason": "fresh business launch milestone",
        "actor": "worker",
    }
    raw = handle_business_post_operator_update(args)
    phase_store.record_operator_update_receipt(
        run.job_id,
        phase,
        args=args,
        result=raw,
    )


def _bootstrap_x_launch_outcome(
    store: Any,
    slug: str,
    *,
    bootstrap_job_id: str,
    bootstrap_attempt: int,
) -> dict[str, Any]:
    """Exact-attempt durable truth for the mandatory bootstrap X phase.

    Evidence from another business run or an earlier attempt is never adopted implicitly. A
    successful publish writes ``bootstrap.x_launch.outcome`` atomically with its X receipt; a
    current attempt that intentionally dedupes an existing receipt writes a fresh scoped adoption
    event. A blocked provider/credit gate writes the same scoped event, while a CEO-declared blocker
    is accepted only from an operator update carrying this exact ``(job_id, attempt)`` context.
    """
    run_id = str(bootstrap_job_id or "").strip()
    try:
        attempt = int(bootstrap_attempt)
    except (TypeError, ValueError):
        attempt = 0
    pending: dict[str, Any] = {
        "status": "pending",
        "bootstrap_job_id": run_id,
        "bootstrap_attempt": attempt,
        "post_id": "",
        "post_url": "",
        "receipt_path": "",
        "receipt_sha256": "",
        "receipt_sent": False,
        "blocker": "",
        "source": "",
        "review_required": False,
    }
    if not run_id or attempt < 1:
        return {**pending, "blocker": "bootstrap job/attempt identity is unavailable"}
    try:
        with store._connect() as conn:
            rows = conn.execute(
                """
                SELECT event_type, payload_json
                FROM events
                WHERE business_slug = ?
                  AND event_type IN (
                    'bootstrap.x_launch.outcome',
                    'business.operator_update'
                  )
                ORDER BY created_at DESC
                LIMIT 100
                """,
                (slug,),
            ).fetchall()
    except Exception as exc:
        raise RuntimeError(f"X launch outcome evidence could not be read: {exc}") from exc

    receipt_candidates: list[dict[str, Any]] | None = None

    def _published_receipt_valid(payload: Mapping[str, Any]) -> bool:
        nonlocal receipt_candidates
        if receipt_candidates is None:
            try:
                from .core import _x_outreach_receipt_candidates

                receipt_candidates = list(_x_outreach_receipt_candidates(store, slug))
            except Exception:
                receipt_candidates = []
        expected_path = str(payload.get("receipt_path") or "").strip()
        expected_post_id = str(payload.get("post_id") or "").strip()
        expected_post_url = str(payload.get("post_url") or "").strip()
        for candidate in receipt_candidates:
            if expected_path and str(candidate.get("receipt_rel") or "").strip() != expected_path:
                continue
            receipt = (
                candidate.get("receipt")
                if isinstance(candidate.get("receipt"), Mapping)
                else {}
            )
            receipt_task = (
                receipt.get("operator_task")
                if isinstance(receipt.get("operator_task"), Mapping)
                else (
                    (receipt.get("metadata") or {}).get("operator_task")
                    if isinstance(receipt.get("metadata"), Mapping)
                    and isinstance((receipt.get("metadata") or {}).get("operator_task"), Mapping)
                    else {}
                )
            )
            try:
                receipt_attempt = int(receipt_task.get("attempt") or 0)
            except (TypeError, ValueError):
                continue
            if (
                str(receipt_task.get("task_kind") or "").strip().lower()
                != "ceo_bootstrap"
                or str(receipt_task.get("run_id") or "").strip() != run_id
                or not (0 < receipt_attempt <= attempt)
                or not bool(receipt.get("sent"))
                or str(receipt.get("external_side_effects") or "").strip().lower()
                != "sent"
            ):
                continue
            if expected_post_id and str(candidate.get("post_id") or "").strip() != expected_post_id:
                continue
            if expected_post_url and str(candidate.get("post_url") or "").strip() != expected_post_url:
                continue
            return True
        # The parent CEO may be mounted at the workspace revision from before an X child committed,
        # so its local receipt mirror can legitimately lag the DB event. New runtime-authored events
        # carry a digest of the exact receipt bytes and the sent-side-effect facts from the same
        # atomic commit. The bootstrap.* namespace is runtime-reserved, making this a durable proof
        # commitment rather than a model-authored status label.
        receipt_digest = str(payload.get("receipt_sha256") or "").strip().lower()
        return bool(
            expected_path
            and expected_post_id
            and re.fullmatch(r"[0-9a-f]{64}", receipt_digest)
            and bool(payload.get("receipt_sent"))
            and str(payload.get("external_side_effects") or "").strip().lower() == "sent"
        )

    for row in rows or []:
        if isinstance(row, Mapping):
            event_type = str(row.get("event_type") or "")
            raw_payload: Any = row.get("payload_json")
        else:
            try:
                event_type = str(row[0] or "")
                raw_payload = row[1]
            except Exception:
                continue
        if isinstance(raw_payload, Mapping):
            payload = dict(raw_payload)
        else:
            try:
                payload = json.loads(str(raw_payload or "{}"))
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
        operator_task = (
            payload.get("operator_task")
            if isinstance(payload.get("operator_task"), Mapping)
            else {}
        )
        task_run_id = str(operator_task.get("run_id") or "").strip()
        try:
            task_attempt = int(operator_task.get("attempt") or 0)
        except (TypeError, ValueError):
            task_attempt = 0
        if (
            str(operator_task.get("task_kind") or "").strip().lower() != "ceo_bootstrap"
            or task_run_id != run_id
            or task_attempt != attempt
        ):
            continue

        if event_type == "bootstrap.x_launch.outcome":
            status = str(payload.get("status") or "").strip().lower()
            if status not in {"published", "blocked"}:
                continue
            if status == "published" and not _published_receipt_valid(payload):
                # Payload shape is not provider authority. A published launch outcome must resolve
                # to the immutable sent receipt from this bootstrap run (or a prior attempt that the
                # current attempt explicitly adopted).
                continue
            return {
                **pending,
                "status": status,
                "post_id": str(payload.get("post_id") or "").strip(),
                "post_url": str(payload.get("post_url") or "").strip(),
                "receipt_path": str(payload.get("receipt_path") or "").strip(),
                "receipt_sha256": str(payload.get("receipt_sha256") or "").strip().lower(),
                "receipt_sent": bool(payload.get("receipt_sent")),
                "blocker": str(payload.get("blocker") or "").strip(),
                "source": str(payload.get("source") or event_type).strip(),
                "review_required": bool(payload.get("review_required")),
            }

        milestones = payload.get("milestones")
        if not isinstance(milestones, list):
            continue
        launch_blocked = next(
            (
                item
                for item in milestones
                if isinstance(item, Mapping)
                and str(item.get("category") or "").strip().upper() == "LAUNCH"
                and str(item.get("status") or "").strip().lower() == "blocked"
            ),
            None,
        )
        if launch_blocked is not None:
            blocker = str(
                launch_blocked.get("description")
                or payload.get("summary")
                or "X launch was explicitly blocked"
            ).strip()
            return {
                **pending,
                "status": "blocked",
                "blocker": blocker,
                "source": "business.operator_update",
                "review_required": True,
            }
    return pending


def _bootstrap_human_review_blocker(
    store: Any,
    slug: str,
    *,
    bootstrap_job_id: str,
    bootstrap_attempt: int,
) -> dict[str, Any]:
    """Durable same-job blocker that forbids every later autonomous bootstrap attempt."""
    run_id = str(bootstrap_job_id or "").strip()
    try:
        attempt = max(1, int(bootstrap_attempt))
    except (TypeError, ValueError):
        return {}
    with store._connect() as conn:
        rows = conn.execute(
            """
            SELECT event_type, payload_json
            FROM events
            WHERE business_slug = ?
              AND event_type = 'bootstrap.human_review_required'
            ORDER BY created_at DESC
            LIMIT 100
            """,
            (slug,),
        ).fetchall()
    for row in rows or []:
        if isinstance(row, Mapping):
            event_type = str(row.get("event_type") or "bootstrap.human_review_required")
            raw_payload = row.get("payload_json")
        else:
            event_type = str(row[0] or "bootstrap.human_review_required") if len(row) > 1 else "bootstrap.human_review_required"
            raw_payload = row[1] if len(row) > 1 else row[0]
        if isinstance(raw_payload, Mapping):
            payload = dict(raw_payload)
        else:
            try:
                payload = json.loads(str(raw_payload or "{}"))
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
        operator_task = (
            payload.get("operator_task")
            if isinstance(payload.get("operator_task"), Mapping)
            else {}
        )
        try:
            event_attempt = int(operator_task.get("attempt") or 0)
        except (TypeError, ValueError):
            continue
        same_job_prior_attempt = bool(
            str(operator_task.get("task_kind") or "").strip().lower() == "ceo_bootstrap"
            and str(operator_task.get("run_id") or "").strip() == run_id
            and 0 < event_attempt <= attempt
        )
        if not same_job_prior_attempt:
            continue
        review_required = bool(payload.get("review_required"))
        blocker = str(payload.get("blocker") or "").strip()
        source = str(payload.get("source") or event_type).strip()
        if review_required:
            return {
                "review_required": True,
                "blocker": blocker or "human review required",
                "source": source or "runtime",
                "workspace": str(payload.get("workspace") or "").strip(),
                "operator_task": dict(operator_task),
            }
    return {}


def _product_publish_blocker_after(store: Any, slug: str, cursor: str) -> tuple[str, str]:
    """Return a new validation-passed/platform-publish blocker after ``cursor``."""
    with store._connect() as conn:
        row = conn.execute(
            "SELECT id, payload_json FROM events WHERE business_slug = ? "
            "AND event_type = 'product.surface.refresh' "
            "ORDER BY created_at DESC, id DESC LIMIT 1",
            (slug,),
        ).fetchone()
    if row is None:
        return "", ""
    event_id = str(row.get("id") if isinstance(row, Mapping) else row[0])
    raw = row.get("payload_json") if isinstance(row, Mapping) else row[1]
    payload = dict(raw) if isinstance(raw, Mapping) else json.loads(str(raw or "{}"))
    publish = payload.get("publish") if isinstance(payload.get("publish"), Mapping) else {}
    blocker = str(
        publish.get("blocker") or payload.get("blocker") or payload.get("error") or ""
    ).strip()
    if (
        event_id != str(cursor or "")
        and str(payload.get("status") or "").strip().lower() == "passed"
        and str(publish.get("status") or "").strip().lower() == "blocked"
    ):
        return event_id, blocker or "product validation passed but platform publication failed"
    return event_id, ""


def _read_bootstrap_human_review_blocker_pinned(
    store: Any,
    slug: str,
    *,
    bootstrap_job_id: str,
    bootstrap_attempt: int,
) -> dict[str, Any]:
    """Read terminal proof without ever turning unknown database state into permission to continue."""
    while True:
        active_claim = jobs.current_job_claim()
        if active_claim is not None:
            active_claim.assert_owned("reading bootstrap human-review proof")
        try:
            return _bootstrap_human_review_blocker(
                store,
                slug,
                bootstrap_job_id=bootstrap_job_id,
                bootstrap_attempt=bootstrap_attempt,
            )
        except Exception as exc:
            _log.warning(
                "worker: human-review proof read failed for bootstrap %s attempt %s; "
                "pinning the parent claim: %s",
                bootstrap_job_id,
                bootstrap_attempt,
                exc,
            )
            time.sleep(2.0)


def _bootstrap_ready_for_completion_grace(
    store: Any,
    slug: str,
    *,
    workflow_requested: bool,
    archetype: str,
    bootstrap_job_id: str,
    bootstrap_attempt: int,
) -> bool:
    """Whether it is safe for the outer CEO completion grace to begin.

    Web bootstraps are terminal after the required product is durably live. Mobile bootstraps have
    the store-signed app phase after the web product, so they intentionally rely on their absolute
    ceiling and natural turn completion rather than arming this earlier web completion probe.
    """
    if str(archetype or "").strip().lower() == "mobile_app":
        return False
    product_complete = _bootstrap_has_durable_live_product(
        store,
        slug,
        workflow_requested=workflow_requested,
    )
    return product_complete


def _bootstrap_delegated_children(
    store: Any,
    slug: str,
    *,
    bootstrap_job_id: str,
    bootstrap_attempt: int,
    statuses: tuple[str, ...] = ("queued", "running"),
) -> list[dict[str, Any]]:
    """Return every unfinished child owned by this exact parent bootstrap generation."""
    parent_run_id = str(bootstrap_job_id or "").strip()
    try:
        parent_attempt = max(1, int(bootstrap_attempt))
    except (TypeError, ValueError):
        return []
    if not parent_run_id:
        return []
    parent_identity = json.dumps(
        {
            "task_kind": "ceo_bootstrap",
            "run_id": parent_run_id,
            "attempt": parent_attempt,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    with store._connect() as conn:
        rows = conn.execute(
            """
            SELECT id, status, locked_by, attempts, payload
            FROM jobs
            WHERE business_slug = ?
              AND kind IN ('claude.agent_task', 'product.surface_refresh')
              AND status = ANY(?)
              AND COALESCE(payload -> 'parent_operator_task', '{}'::jsonb) @> ?::jsonb
            ORDER BY created_at, id
            """,
            (slug, list(statuses), parent_identity),
        ).fetchall()
    return [dict(row) if isinstance(row, Mapping) else {
        "id": row[0],
        "status": row[1],
        "locked_by": row[2],
        "attempts": row[3],
        "payload": row[4],
    } for row in (rows or [])]


def _bootstrap_has_live_delegated_child(
    store: Any,
    slug: str,
    *,
    bootstrap_job_id: str,
    bootstrap_attempt: int,
    freshness_seconds: float = 60.0,
) -> bool:
    """Whether this exact bootstrap attempt still owns queued or running delegated work."""
    del freshness_seconds  # unfinished ownership, not recent activity, is the safety predicate
    return bool(
        _bootstrap_delegated_children(
            store,
            slug,
            bootstrap_job_id=bootstrap_job_id,
            bootstrap_attempt=bootstrap_attempt,
        )
    )


def _record_bootstrap_human_review_required(
    store: Any,
    slug: str,
    *,
    bootstrap_job_id: str,
    bootstrap_attempt: int,
    blocker: str,
    source: str,
) -> dict[str, Any]:
    from .core import _assert_active_worker_claim

    _assert_active_worker_claim(store, "recording bootstrap human-review stop")
    payload = {
        "operator_task": {
            "task_kind": "ceo_bootstrap",
            "run_id": str(bootstrap_job_id),
            "attempt": int(bootstrap_attempt),
        },
        "source": str(source or "runtime"),
        "blocker": str(blocker or "human review required"),
        "review_required": True,
    }
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:24]
    store.commit(
        scope=f"business:{slug}",
        operations=[
            {
                "action": "event.record",
                "business": slug,
                "scope": f"business:{slug}",
                "event_type": "bootstrap.human_review_required",
                "payload": payload,
            }
        ],
        idempotency_key=(
            f"bootstrap-human-review:{bootstrap_job_id}:{bootstrap_attempt}:{digest}"
        ),
        reason="stop bootstrap automation for human review",
        actor="runtime",
    )
    return payload


def _cancel_bootstrap_delegated_children(
    store: Any,
    slug: str,
    *,
    bootstrap_job_id: str,
    bootstrap_attempt: int,
    reason: str,
) -> int:
    """Block queued children and request cooperative cancellation of running children."""
    from .core import _assert_active_worker_claim

    _assert_active_worker_claim(store, "cancelling delegated bootstrap children")
    parent_identity = json.dumps(
        {
            "task_kind": "ceo_bootstrap",
            "run_id": str(bootstrap_job_id),
            "attempt": int(bootstrap_attempt),
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    cancellation = json.dumps(
        {
            "cancel_requested": True,
            "cancel_reason": str(reason or "parent bootstrap stopped"),
            "cancel_requested_at": _utc_now_iso(),
        }
    )
    error = json.dumps(
        {"reason": "parent_bootstrap_cancelled", "error": str(reason or "parent stopped")}
    )
    with store._connect() as conn:
        with conn.transaction():
            queued = conn.execute(
                "UPDATE jobs SET status = 'blocked', error = ?::jsonb, "
                "payload = COALESCE(payload, '{}'::jsonb) || ?::jsonb, "
                "locked_by = NULL, locked_at = NULL, updated_at = now() "
                "WHERE business_slug = ? "
                "AND kind IN ('claude.agent_task', 'product.surface_refresh') "
                "AND status = 'queued' "
                "AND COALESCE(payload -> 'parent_operator_task', '{}'::jsonb) @> ?::jsonb",
                (error, cancellation, slug, parent_identity),
            ).rowcount
            running = conn.execute(
                "UPDATE jobs SET payload = COALESCE(payload, '{}'::jsonb) || ?::jsonb, "
                "updated_at = now() "
                "WHERE business_slug = ? "
                "AND kind IN ('claude.agent_task', 'product.surface_refresh') "
                "AND status = 'running' "
                "AND COALESCE(payload -> 'parent_operator_task', '{}'::jsonb) @> ?::jsonb",
                (cancellation, slug, parent_identity),
            ).rowcount
    return int(queued or 0) + int(running or 0)


def _wait_for_bootstrap_delegated_children(
    store: Any,
    slug: str,
    *,
    bootstrap_job_id: str,
    bootstrap_attempt: int,
    deadline_monotonic: float,
    cancel_immediately: bool = False,
    cancel_reason: str = "parent bootstrap stopped",
    human_review_on_deadline: bool = True,
) -> dict[str, Any]:
    """Pin the parent claim until every exact child is terminal; never requeue alongside it."""
    cancel_requested = False
    review_payload: dict[str, Any] = {}
    while True:
        active_claim = jobs.current_job_claim()
        if active_claim is not None:
            active_claim.assert_owned("draining delegated bootstrap children")
        try:
            children = _bootstrap_delegated_children(
                store,
                slug,
                bootstrap_job_id=bootstrap_job_id,
                bootstrap_attempt=bootstrap_attempt,
            )
        except Exception as exc:
            _log.warning(
                "worker: could not read delegated children for bootstrap %s attempt %s; "
                "pinning the parent claim: %s",
                bootstrap_job_id,
                bootstrap_attempt,
                exc,
            )
            time.sleep(2.0)
            continue
        if not children:
            return review_payload
        deadline_reached = time.monotonic() >= float(deadline_monotonic)
        should_cancel = bool(cancel_immediately or deadline_reached)
        if should_cancel and not cancel_requested:
            reason = (
                f"bootstrap reached its hard deadline with {len(children)} delegated child job(s) unfinished"
                if deadline_reached and not cancel_immediately
                else cancel_reason
            )
            try:
                _cancel_bootstrap_delegated_children(
                    store,
                    slug,
                    bootstrap_job_id=bootstrap_job_id,
                    bootstrap_attempt=bootstrap_attempt,
                    reason=reason,
                )
                if deadline_reached and human_review_on_deadline:
                    review_payload = _record_bootstrap_human_review_required(
                        store,
                        slug,
                        bootstrap_job_id=bootstrap_job_id,
                        bootstrap_attempt=bootstrap_attempt,
                        blocker=reason,
                        source="bootstrap_hard_deadline",
                    )
                cancel_requested = True
            except Exception as exc:
                _log.warning(
                    "worker: delegated-child cancellation failed for bootstrap %s attempt %s; "
                    "pinning the parent claim: %s",
                    bootstrap_job_id,
                    bootstrap_attempt,
                    exc,
                )
                time.sleep(2.0)
                continue
        time.sleep(1.0)


def _request_bootstrap_hard_stop(
    store: Any,
    slug: str,
    *,
    bootstrap_job_id: str,
    bootstrap_attempt: int,
    reason: str,
) -> dict[str, Any]:
    """Persist a stop intent and request cooperative cancellation of every exact child."""
    _cancel_bootstrap_delegated_children(
        store,
        slug,
        bootstrap_job_id=bootstrap_job_id,
        bootstrap_attempt=bootstrap_attempt,
        reason=reason,
    )
    return _record_bootstrap_human_review_required(
        store,
        slug,
        bootstrap_job_id=bootstrap_job_id,
        bootstrap_attempt=bootstrap_attempt,
        blocker=reason,
        source="bootstrap_hard_deadline",
    )


def _request_bootstrap_hard_stop_pinned(
    store: Any,
    slug: str,
    *,
    bootstrap_job_id: str,
    bootstrap_attempt: int,
    reason: str,
) -> dict[str, Any]:
    while True:
        try:
            return _request_bootstrap_hard_stop(
                store,
                slug,
                bootstrap_job_id=bootstrap_job_id,
                bootstrap_attempt=bootstrap_attempt,
                reason=reason,
            )
        except Exception as exc:
            _log.warning(
                "worker: hard-stop persistence failed for bootstrap %s attempt %s; "
                "pinning the parent claim: %s",
                bootstrap_job_id,
                bootstrap_attempt,
                exc,
            )
            time.sleep(2.0)


def ceo_wake_handler(job: Job) -> JobRunResult:
    """Handle a ``ceo_wake`` job: run the scheduled CEO turn for ``job.business_slug`` and report its
    true model cost as ``actual_cost_cents`` for flow-A settlement.

    The wake prompt and toolsets come from the canonical source (``core._ceo_cron_prompt`` /
    ``_ceo_cron_toolsets``) so this never drifts from the legacy/cron wake instructions; the system
    prompt is the stable ``prompts/ceo.md`` via ``cli._load_ceo_prompt``."""
    from gateway.session_context import clear_session_vars, set_session_vars

    from .turn_runtime import _business_workspace_execution_context, _load_ceo_prompt
    from .core import TakyonStore, _bound_operator_task_context, _refresh_stale_live_ad_campaigns

    slug = job.business_slug
    owner_user_id = _business_owner_user_id(slug)
    store = TakyonStore(operator_user_id=owner_user_id)
    toolsets = store._ceo_cron_toolsets()
    system_prompt = _load_ceo_prompt()
    progress = _RuntimeProgress(slug=slug, kind="ceo_wake", command=f"/wake {slug}")

    payload = job.payload or {}
    agent_runtime = _selected_worker_agent_runtime()
    sdk_max_budget_usd = (
        _sdk_turn_budget_usd(turn_config={}, payload=payload)
        if agent_runtime == _WORKER_AGENT_RUNTIME_SDK
        else 0.0
    )
    sdk_effort = str(
        payload.get("effort")
        or os.getenv("TAKYON_PRIMARY_AGENT_EFFORT")
        or "high"
    ).strip().lower()
    try:
        wake_attempt = max(1, int(getattr(job, "attempts", 1) or 1))
    except (TypeError, ValueError):
        wake_attempt = 1
    try:
        max_turns = int(payload.get("max_turns") or _DEFAULT_MAX_TURNS)
    except (TypeError, ValueError):
        max_turns = _DEFAULT_MAX_TURNS
    inactivity_limit = _env_float("TAKYON_WORKER_TURN_TIMEOUT", _DEFAULT_TURN_TIMEOUT)
    wake_wall_clock_limit = max(
        60.0,
        _env_float("TAKYON_WORKER_WAKE_WALL_TIMEOUT", _DEFAULT_WAKE_WALL_TIMEOUT),
    )
    sdk_receipts: list[dict[str, Any]] = []

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
                task_kind="ceo_wake",
            )
            # Mark the steady-state wake turn so product/destructive tool handlers fail closed
            # (_refuse_on_autonomous_wake). Bootstrap sets "ceo_bootstrap" and chat sets nothing,
            # so neither is refused. The marker is read in this turn, before any worker job is
            # enqueued, so a wake-spawned edit is refused at source. run_id carries the durable
            # job id so every cost/log event inside the turn correlates to this job.
            with _bound_operator_task_context(run_id=str(job.id), task_kind="ceo_wake"):
                # Pre-wake ad refresh: pull fresh delivery insights for LIVE + STALE ad campaigns so
                # the pulse the CEO reads this turn is current, instead of relying on it to remember
                # to sync. Runs INSIDE the bound wake operator-task context so the auto-fired
                # insights-sync is attributed to this wake and shares its guard lane — the sync tool
                # itself is test-mode-aware and never refuses on wake, and its cap-settlement / D9
                # ad-group pause is a deterministic safety rail (routes through the creative gateway,
                # not the wake-guarded control handler). Best-effort — a failed/slow refresh must
                # never break the wake.
                try:
                    _ad_refresh = _refresh_stale_live_ad_campaigns(slug)
                    if int(_ad_refresh.get("refreshed") or 0):
                        _record_runtime_event(
                            slug,
                            kind="ceo_wake",
                            status="running",
                            detail=f"Pre-wake refreshed insights for {_ad_refresh['refreshed']} live ad campaign(s).",
                            command=f"/wake {slug}",
                        )
                except Exception:
                    pass
                # Pre-wake deterministic distillation: evaluate matured episodes' measured
                # metric deltas and keep the significant ones as [measured] lessons (RL rail
                # R8, fixed slice — code, not model judgment). Runs AFTER the insights refresh
                # so the "after" snapshot reads fresh delivery numbers, and BEFORE the prompt
                # build so this wake's appended learnings already include them. Best-effort —
                # a failed distill must never break the wake.
                try:
                    _distilled = store.distill_episode_lessons(slug)
                    if int(_distilled.get("distilled") or 0):
                        _record_runtime_event(
                            slug,
                            kind="ceo_wake",
                            status="running",
                            detail=(
                                f"Pre-wake distilled {_distilled['distilled']} measured lesson(s) "
                                "from matured episodes."
                            ),
                            command=f"/wake {slug}",
                        )
                except Exception:
                    pass
                # Pre-wake ROAS run-history assembly: append any NEW insights-sync results as
                # run entries to metrics/roas/<channel>.md — the per-business track record the
                # channel skill reads before its next launch ("do more of what worked"). Runs
                # AFTER the insights refresh (fresh receipts) and BEFORE the prompt build.
                # Best-effort — a failed append must never break the wake.
                try:
                    _roas_hist = store.assemble_roas_run_history(slug)
                    if int(_roas_hist.get("appended") or 0):
                        _record_runtime_event(
                            slug,
                            kind="ceo_wake",
                            status="running",
                            detail=(
                                f"Pre-wake appended {_roas_hist['appended']} run(s) to the "
                                "metrics/roas/ channel history."
                            ),
                            command=f"/wake {slug}",
                        )
                except Exception:
                    pass
                # Build the wake prompt AFTER the pre-wake refresh + distillation (it was
                # previously built before them, so the injected memory and appended learnings
                # could not see this wake's own refresh/distill work — a stale-prompt bug).
                user_prompt = store._ceo_cron_prompt(slug)
                final_response, cost_usd, cost_status, _turn_completed = _run_ceo_turn(
                    slug=slug,
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                    toolsets=toolsets,
                    max_turns=max_turns,
                    inactivity_limit=inactivity_limit,
                    wall_clock_limit=wake_wall_clock_limit,
                    progress=progress,
                    agent_runtime=agent_runtime,
                    sdk_session_id=str(job.id),
                    sdk_resume_session=wake_attempt > 1,
                    sdk_max_budget_usd=sdk_max_budget_usd,
                    sdk_effort=sdk_effort,
                    sdk_epoch="wake",
                )
                if agent_runtime == _WORKER_AGENT_RUNTIME_SDK:
                    if receipt := _consume_sdk_turn_receipt():
                        sdk_receipts.append(receipt)
    except Exception as exc:
        if _is_ceo_inactivity_timeout(exc):
            status = _best_effort_terminalize_owned_timeout(job, error=str(exc))
            if status:
                _log.warning(
                    "worker: ceo_wake inactivity timeout terminalized durable job %s as %s before bubbling",
                    getattr(job, "id", ""),
                    status,
                )
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
            "agent_runtime": agent_runtime,
            "sdk_receipts": sdk_receipts,
        },
        actual_cost_cents=cents,
        billing_mode=(
            jobs.BILLING_MODE_PROVIDER_BROKER
            if agent_runtime == _WORKER_AGENT_RUNTIME_SDK
            else jobs.BILLING_MODE_JOB_RESERVATION
        ),
    )


def ceo_bootstrap_handler(job: Job) -> JobRunResult:
    from gateway.session_context import clear_session_vars, set_session_vars

    from .turn_runtime import (
        _bootstrap_goal_requests_product_workflow,
        _bootstrap_public_site_url,
        _business_workspace_execution_context,
        _ceo_bootstrap_phase_runtime_config,
    )
    from .bootstrap_phases import (
        PHASE_ALLOWED_TOOLS,
        PHASE_MAX_TURNS,
        PostgresBootstrapPhaseStore,
        phase_prompt,
    )
    from .core import TakyonStore, _bound_operator_task_context

    slug = job.business_slug
    bootstrap_job_id = str(job.id)
    try:
        bootstrap_attempt = max(1, int(getattr(job, "attempts", 1) or 1))
    except (TypeError, ValueError):
        bootstrap_attempt = 1
    owner_user_id = _business_owner_user_id(slug)
    store = TakyonStore(operator_user_id=owner_user_id)
    summary = store.read(scope=f"business:{slug}", query="summary")
    business = summary.get("business") if isinstance(summary.get("business"), dict) else {}
    active_mode = "live"
    goal = str((job.payload or {}).get("goal") or (business or {}).get("goal") or "").strip()
    workflow_requested = _bootstrap_goal_requests_product_workflow(goal)
    business_name = str((business or {}).get("name") or "").strip()
    # Opt-in landing-hero animations (--animation at create). Persisted on the business metadata;
    # read it robustly whether the summary exposes a parsed `metadata` dict or a raw metadata_json
    # string, and honor a job-payload override. Absent → False → bootstrap prose is unchanged.
    def _business_metadata(rec: dict) -> dict:
        meta = rec.get("metadata")
        if isinstance(meta, dict):
            return meta
        raw = rec.get("metadata_json")
        if isinstance(raw, str) and raw.strip():
            try:
                import json as _json
                parsed = _json.loads(raw)
                return parsed if isinstance(parsed, dict) else {}
            except Exception:
                return {}
        return {}

    animations = bool(
        (job.payload or {}).get("landing_animations")
        or _business_metadata(business or {}).get("landing_animations")
    )
    # Archetype-aware bootstrap: prefer the summary's business row, fall back to a direct row read
    # (the archetype column is canonical on businesses). Absent/error → "" → web prose unchanged.
    archetype = str((business or {}).get("archetype") or "").strip().lower()
    if not archetype:
        try:
            with store._connect() as conn:
                row = store._ensure_business(conn, slug)
            archetype = str((row or {}).get("archetype") or "").strip().lower()
        except Exception:
            archetype = ""
    bootstrap_turn = _ceo_bootstrap_phase_runtime_config(
        goal,
        archetype=archetype,
    )
    system_prompt = str(bootstrap_turn.get("ephemeral_system_prompt") or "")
    toolsets = list(bootstrap_turn.get("enabled_toolsets") or ["takyon", "takyon-authority", "skills"])
    payload = job.payload or {}
    agent_runtime = _selected_worker_agent_runtime()
    sdk_max_budget_usd = (
        _sdk_turn_budget_usd(turn_config=bootstrap_turn, payload=payload)
        if agent_runtime == _WORKER_AGENT_RUNTIME_SDK
        else 0.0
    )
    sdk_effort = str(
        bootstrap_turn.get("effort")
        or payload.get("effort")
        or os.getenv("TAKYON_PRIMARY_AGENT_EFFORT")
        or "high"
    ).strip().lower()
    try:
        max_turns = int(payload.get("max_turns") or _DEFAULT_MAX_TURNS)
    except (TypeError, ValueError):
        max_turns = _DEFAULT_MAX_TURNS
    inactivity_limit = _env_float("TAKYON_WORKER_TURN_TIMEOUT", _DEFAULT_TURN_TIMEOUT)
    # Mobile bootstrap is a marathon turn — landing + logo + an iOS app build (a
    # 10-20min docker worker) + a store-signed publish. Its long model sub-steps and build waits
    # exceed the 600s default and were FALSE-killing the turn mid-build (sipstreak, 2026-07-09,
    # both attempts). Give it 30min of idle headroom: streaming tokens and the app-build heartbeat
    # reset the clock continuously, so only a genuine ~30min stall (a real hang) still trips it.
    if str(archetype or "").strip().lower() == "mobile_app":
        inactivity_limit = max(inactivity_limit, _MOBILE_BOOTSTRAP_TURN_TIMEOUT)
    wall_ceiling = (
        _MOBILE_BOOTSTRAP_WALL_TIMEOUT
        if str(archetype or "").strip().lower() == "mobile_app"
        else _DEFAULT_BOOTSTRAP_WALL_TIMEOUT
    )
    wall_clock_limit = min(
        wall_ceiling,
        max(
            60.0,
            _env_float("TAKYON_WORKER_BOOTSTRAP_WALL_TIMEOUT", wall_ceiling),
        ),
    )
    completion_grace_seconds = min(
        900.0,
        max(
            60.0,
            _env_float(
                "TAKYON_WORKER_BOOTSTRAP_POST_PUBLISH_GRACE",
                _DEFAULT_BOOTSTRAP_POST_PUBLISH_GRACE,
            ),
        ),
    )
    schedule = str(payload.get("schedule") or "").strip()
    command = f"/create {slug}"
    progress = _RuntimeProgress(slug=slug, kind="ceo_bootstrap", command=command)
    sdk_receipts: list[dict[str, Any]] = []
    bootstrap_started_monotonic = time.monotonic()
    bootstrap_deadline_monotonic = bootstrap_started_monotonic + wall_clock_limit
    bootstrap_deadline_at = time.time() + wall_clock_limit
    human_review_blocker: dict[str, Any] = {}
    platform_publish_blocker = ""
    phase_store = PostgresBootstrapPhaseStore(
        operator_user_id=owner_user_id,
        business_slug=slug,
    )
    phase_run = phase_store.initialize_or_load(
        job_id=bootstrap_job_id,
        sdk_session_id=bootstrap_job_id,
        owner_user_id=owner_user_id,
        business_slug=slug,
        immutable_inputs={
            "goal": goal,
            "business_name": business_name,
            "active_mode": active_mode,
            "animations": animations,
            "archetype": archetype,
            "workflow_requested": workflow_requested,
            "schedule": schedule,
            "job_payload": dict(payload),
        },
        job_attempt=bootstrap_attempt,
    )

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
            # run_id carries the durable job id so every cost/log event inside the turn
            # correlates to this job (operator_cost_events, migration 0070).
            with _bound_operator_task_context(
                run_id=str(job.id),
                task_kind="ceo_bootstrap",
                attempt=bootstrap_attempt,
                deadline_at=bootstrap_deadline_at,
            ):
                final_response = ""
                cost_usd = 0.0
                cost_status = "unknown"
                turn_completed = False
                sdk_query_count = 0
                phase_calls_this_attempt: dict[str, int] = {}

                def verify_phase(run: Any, phase: str) -> Any | None:
                    return _bootstrap_phase_authoritative_evidence(
                        store,
                        run,
                        phase,
                        workflow_requested=workflow_requested,
                        archetype=archetype,
                    )

                while True:
                    # The first incomplete phase is revalidated before any model call. Effects that
                    # committed before a crash are checkpointed here and never paid/executed twice.
                    phase_run = phase_store.reconcile_first_incomplete(
                        bootstrap_job_id, verify_phase
                    )
                    phase = phase_run.current_phase
                    if phase is None:
                        break
                    human_review_blocker = _read_bootstrap_human_review_blocker_pinned(
                        store,
                        slug,
                        bootstrap_job_id=bootstrap_job_id,
                        bootstrap_attempt=bootstrap_attempt,
                    )
                    if human_review_blocker:
                        _wait_for_bootstrap_delegated_children(
                            store,
                            slug,
                            bootstrap_job_id=bootstrap_job_id,
                            bootstrap_attempt=bootstrap_attempt,
                            deadline_monotonic=bootstrap_deadline_monotonic,
                            cancel_immediately=True,
                            cancel_reason=str(
                                human_review_blocker.get("blocker")
                                or "bootstrap requires human review"
                            ),
                            human_review_on_deadline=False,
                        )
                        break
                    boundary_review = _wait_for_bootstrap_delegated_children(
                        store,
                        slug,
                        bootstrap_job_id=bootstrap_job_id,
                        bootstrap_attempt=bootstrap_attempt,
                        deadline_monotonic=bootstrap_deadline_monotonic,
                    )
                    if boundary_review:
                        human_review_blocker = boundary_review
                        break
                    elapsed = time.monotonic() - bootstrap_started_monotonic
                    if elapsed >= wall_clock_limit:
                        human_review_blocker = _request_bootstrap_hard_stop_pinned(
                            store,
                            slug,
                            bootstrap_job_id=bootstrap_job_id,
                            bootstrap_attempt=bootstrap_attempt,
                            reason=(
                                "bootstrap reached its hard end-to-end deadline before a bounded "
                                "continuation could start"
                            ),
                        )
                        break
                    phase_calls_this_attempt[phase] = phase_calls_this_attempt.get(phase, 0) + 1
                    if phase_calls_this_attempt[phase] > 2:
                        raise RuntimeError(
                            f"bootstrap phase {phase} did not produce its authoritative done predicate"
                        )
                    phase_store.start_phase(
                        bootstrap_job_id, phase, job_attempt=bootstrap_attempt
                    )
                    _post_bootstrap_phase_operator_update(
                        phase_store,
                        phase_run,
                        phase,
                    )
                    next_prompt = phase_prompt(
                        phase_run,
                        phase,
                        public_site_url=_bootstrap_public_site_url(slug),
                        animations=animations,
                    )
                    refresh_cursor, _ = _product_publish_blocker_after(store, slug, "")
                    sdk_query_count += 1
                    configured_phase_turns = PHASE_MAX_TURNS.get(phase, _DEFAULT_MAX_TURNS)
                    if payload.get("max_turns") is not None:
                        configured_phase_turns = min(configured_phase_turns, max_turns)
                    response, turn_cost_usd, turn_cost_status, phase_turn_completed = _run_ceo_turn(
                        slug=slug,
                        system_prompt=system_prompt,
                        user_prompt=next_prompt,
                        toolsets=toolsets,
                        max_turns=max(1, int(configured_phase_turns)),
                        inactivity_limit=inactivity_limit,
                        wall_clock_limit=max(60.0, wall_clock_limit - elapsed),
                        completion_probe=lambda: _bootstrap_ready_for_completion_grace(
                            store,
                            slug,
                            workflow_requested=workflow_requested,
                            archetype=archetype,
                            bootstrap_job_id=bootstrap_job_id,
                            bootstrap_attempt=bootstrap_attempt,
                        ),
                        completion_grace_seconds=completion_grace_seconds,
                        external_activity_probe=lambda: _bootstrap_has_live_delegated_child(
                            store,
                            slug,
                            bootstrap_job_id=bootstrap_job_id,
                            bootstrap_attempt=bootstrap_attempt,
                        ),
                        terminal_review_probe=lambda: bool(
                            _bootstrap_human_review_blocker(
                                store,
                                slug,
                                bootstrap_job_id=bootstrap_job_id,
                                bootstrap_attempt=bootstrap_attempt,
                            )
                        ),
                        hard_stop_callback=lambda reason: _request_bootstrap_hard_stop(
                            store,
                            slug,
                            bootstrap_job_id=bootstrap_job_id,
                            bootstrap_attempt=bootstrap_attempt,
                            reason=(
                                f"bootstrap hard deadline reached: {reason}; automation stopped "
                                "and every delegated child was cancelled"
                            ),
                        ),
                        api_retry_floor=_BOOTSTRAP_API_RETRY_FLOOR,
                        progress=progress,
                        agent_runtime=agent_runtime,
                        sdk_session_id=bootstrap_job_id,
                        sdk_resume_session=(
                            bootstrap_attempt > 1 or sdk_query_count > 1
                        ),
                        sdk_max_budget_usd=sdk_max_budget_usd,
                        sdk_effort=sdk_effort,
                        # Every continuation and retry belongs to this one
                        # job-level cumulative Safebox spend envelope.
                        sdk_epoch="bootstrap",
                        sdk_allowed_tools=PHASE_ALLOWED_TOOLS[phase],
                        sdk_tool_receipt_callback=lambda name, args, result, active_phase=phase: (
                            phase_store.record_tool_receipt(
                                bootstrap_job_id,
                                active_phase,
                                tool_name=name,
                                args=args,
                                result=result,
                            )
                        ),
                        # Phase summaries are internal orchestration output. Customer-visible
                        # progress is emitted only through the deterministic operator-update rail.
                        record_final_chat=False,
                    )
                    sdk_turn_receipt: dict[str, Any] | None = None
                    if agent_runtime == _WORKER_AGENT_RUNTIME_SDK:
                        if receipt := _consume_sdk_turn_receipt():
                            sdk_turn_receipt = receipt
                            sdk_receipts.append(receipt)
                    if phase_turn_completed:
                        phase_store.record_runtime_completion(
                            bootstrap_job_id,
                            phase,
                            runtime_receipt=sdk_turn_receipt,
                        )
                    final_response = response
                    cost_usd += turn_cost_usd
                    cost_status = turn_cost_status
                    turn_completed = phase_turn_completed
                    if time.monotonic() >= bootstrap_deadline_monotonic:
                        human_review_blocker = _request_bootstrap_hard_stop_pinned(
                            store,
                            slug,
                            bootstrap_job_id=bootstrap_job_id,
                            bootstrap_attempt=bootstrap_attempt,
                            reason=(
                                "bootstrap reached its hard end-to-end deadline; automation and "
                                "every delegated child were stopped for human review"
                            ),
                        )
                    boundary_review = _wait_for_bootstrap_delegated_children(
                        store,
                        slug,
                        bootstrap_job_id=bootstrap_job_id,
                        bootstrap_attempt=bootstrap_attempt,
                        deadline_monotonic=bootstrap_deadline_monotonic,
                    )
                    if boundary_review:
                        human_review_blocker = boundary_review
                    durable_review_blocker = _read_bootstrap_human_review_blocker_pinned(
                        store,
                        slug,
                        bootstrap_job_id=bootstrap_job_id,
                        bootstrap_attempt=bootstrap_attempt,
                    )
                    if durable_review_blocker:
                        human_review_blocker = durable_review_blocker
                    if human_review_blocker:
                        break
                    _, platform_publish_blocker = _product_publish_blocker_after(
                        store, slug, refresh_cursor
                    )
                    if platform_publish_blocker:
                        break
                    # Only a runtime validator may advance. A natural model stop with no durable
                    # artifact/receipt remains on this phase for one bounded repair query.
                    phase_run = phase_store.reconcile_first_incomplete(
                        bootstrap_job_id, verify_phase
                    )
                completed_phase_run = phase_store.load(bootstrap_job_id)
                if completed_phase_run.status == "completed":
                    _post_bootstrap_phase_operator_update(
                        phase_store,
                        completed_phase_run,
                        "finalize",
                        completed=True,
                    )
                finalize_evidence = completed_phase_run.phase_evidence.get("finalize")
                finalize_details = (
                    finalize_evidence.get("details")
                    if isinstance(finalize_evidence, Mapping)
                    and isinstance(finalize_evidence.get("details"), Mapping)
                    else {}
                )
                turn_completed = bool(
                    turn_completed or finalize_details.get("sdk_turn_completed")
                )
    except Exception as exc:
        # Every exceptional parent path—not only inactivity—must cancel and drain its exact queued/
        # running children before run_one is allowed to requeue the parent. Otherwise an API error or
        # lost claim can start attempt 2 while attempt 1's detached child is still editing.
        boundary_review = _wait_for_bootstrap_delegated_children(
            store,
            slug,
            bootstrap_job_id=bootstrap_job_id,
            bootstrap_attempt=bootstrap_attempt,
            deadline_monotonic=bootstrap_deadline_monotonic,
            cancel_immediately=True,
            cancel_reason=f"parent bootstrap failed: {exc}",
            human_review_on_deadline=True,
        )
        if boundary_review:
            human_review_blocker = boundary_review
        durable_review_blocker = _read_bootstrap_human_review_blocker_pinned(
            store,
            slug,
            bootstrap_job_id=bootstrap_job_id,
            bootstrap_attempt=bootstrap_attempt,
        )
        if durable_review_blocker:
            human_review_blocker = durable_review_blocker
        if human_review_blocker:
            final_response = str(exc)
        else:
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

    if time.monotonic() >= bootstrap_deadline_monotonic and not human_review_blocker:
        human_review_blocker = _request_bootstrap_hard_stop_pinned(
            store,
            slug,
            bootstrap_job_id=bootstrap_job_id,
            bootstrap_attempt=bootstrap_attempt,
            reason=(
                "bootstrap reached its hard end-to-end deadline before authoritative settlement"
            ),
        )
    final_boundary_review = _wait_for_bootstrap_delegated_children(
        store,
        slug,
        bootstrap_job_id=bootstrap_job_id,
        bootstrap_attempt=bootstrap_attempt,
        deadline_monotonic=bootstrap_deadline_monotonic,
        cancel_immediately=bool(human_review_blocker),
        cancel_reason=str(
            human_review_blocker.get("blocker") or "bootstrap is stopping"
        ),
        human_review_on_deadline=True,
    )
    if final_boundary_review:
        human_review_blocker = final_boundary_review
    durable_review_blocker = _read_bootstrap_human_review_blocker_pinned(
        store,
        slug,
        bootstrap_job_id=bootstrap_job_id,
        bootstrap_attempt=bootstrap_attempt,
    )
    if durable_review_blocker:
        human_review_blocker = durable_review_blocker

    if human_review_blocker:
        blocker_text = str(
            human_review_blocker.get("blocker") or "human review is required"
        ).strip()
        final_response = (
            final_response.rstrip()
            + ("\n\n" if final_response.strip() else "")
            + f"Bootstrap stopped for human review: {blocker_text}"
        )
        wake_result: dict[str, object] = {
            "status": "suppressed",
            "enabled": False,
            "reason": "bootstrap_human_review_required",
            "requested_schedule": schedule,
        }
        cents = max(0, int(round(cost_usd * 100)))
        _record_runtime_event(
            slug,
            kind="ceo_bootstrap",
            status="blocked",
            detail=f"CEO bootstrap stopped for human review: {blocker_text}",
            command=command,
            trace={
                "kind": "turn",
                "entry_key": "turn:ceo_bootstrap",
                "label": "CEO bootstrap",
                "detail": blocker_text,
                "status": "blocked",
            },
        )
        return JobRunResult(
            result={
                "business_slug": slug,
                "final_response": final_response[:4000],
                "cost_usd": round(cost_usd, 6),
                "cost_status": cost_status,
                "bootstrap_completion_status": "needs_human_review",
                "review_required": True,
                "review_blocker": blocker_text,
                "review_source": str(human_review_blocker.get("source") or "runtime"),
                "wake": wake_result,
                "agent_runtime": agent_runtime,
                "sdk_receipts": sdk_receipts,
            },
            actual_cost_cents=cents,
            terminal_status="blocked",
            terminal_reason="bootstrap_human_review_required",
            billing_mode=(
                jobs.BILLING_MODE_PROVIDER_BROKER
                if agent_runtime == _WORKER_AGENT_RUNTIME_SDK
                else jobs.BILLING_MODE_JOB_RESERVATION
            ),
        )

    if platform_publish_blocker:
        cents = max(0, int(round(cost_usd * 100)))
        _record_runtime_event(
            slug,
            kind="ceo_bootstrap",
            status="blocked",
            detail=f"CEO bootstrap stopped at the platform publish rail: {platform_publish_blocker}",
            command=command,
        )
        return JobRunResult(
            result={
                "business_slug": slug,
                "final_response": final_response[:4000],
                "cost_usd": round(cost_usd, 6),
                "cost_status": cost_status,
                "bootstrap_completion_status": "platform_blocked",
                "review_required": False,
                "review_blocker": platform_publish_blocker,
                "wake": {
                    "status": "suppressed",
                    "enabled": False,
                    "reason": "platform_publish_blocked",
                    "requested_schedule": schedule,
                },
                "agent_runtime": agent_runtime,
                "sdk_receipts": sdk_receipts,
            },
            actual_cost_cents=cents,
            terminal_status="blocked",
            terminal_reason="platform_publish_blocked",
            billing_mode=(
                jobs.BILLING_MODE_PROVIDER_BROKER
                if agent_runtime == _WORKER_AGENT_RUNTIME_SDK
                else jobs.BILLING_MODE_JOB_RESERVATION
            ),
        )

    # ── Post-turn finalization (NON-FATAL by contract) ──────────────────────────────────────────
    # The CEO bootstrap turn has already returned. Everything below is bookkeeping: a final trusted
    # product-surface refresh, the wake-cron schedule, and receipt events. NONE of it may raise out
    # of the handler, because run_one re-runs the ENTIRE 5-minute build on any handler exception
    # (reason=handler_error → fail()→requeue). A fully-built, published bootstrap that hiccups here
    # (a transient DB blip, a lost job claim raising JobNotRunning, a surface re-refresh wobble) must
    # NOT be thrown back on the queue to rebuild from scratch and starve the single build lane.
    #
    # Root cause this guards: a clean turn (finish_reason=stop, under the iteration cap) built the
    # whole product and PUBLISHED the site, then a post-turn step raised and run_one requeued it —
    # observed on business "simple": turn ended 04:31:42, requeued 04:31:49 (reason=handler_error,
    # error == the bare job id == JobNotRunning), attempt 2 re-ran the full 287s Docker build and
    # blocked a fresh business behind it. The done-gate below still trusts canonical product state
    # when this bookkeeping refresh wobbles.
    surface_refresh: dict[str, Any] | None = None
    publish_status = "unknown"
    try:
        surface_refresh = _refresh_business_surface_after_bootstrap(
            slug,
            job_id=str(job.id),
            operator_user_id=owner_user_id,
        )
    except Exception as exc:  # noqa: BLE001 - post-turn refresh must never requeue a finished build
        _log.warning(
            "worker: bootstrap post-turn surface refresh failed for business:%s (non-fatal): %s",
            slug,
            exc,
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

    # The done-gate. A web bootstrap is terminal when the product requirement holds. Research and
    # distribution run later on the existing wake rail. Mobile additionally requires the CEO turn
    # to finish naturally, because its store-signed app phase follows the web product and must not be cut off by the web completion
    # probe. This prevents an interrupted/capped post-product turn from settling as fake "done".
    real_http_actions = _bootstrap_real_http_actions(store, slug) if workflow_requested else set()
    try:
        durable_product_complete = _bootstrap_has_durable_live_product(
            store,
            slug,
            workflow_requested=workflow_requested,
        )
    except Exception:
        durable_product_complete = False
    product_complete = durable_product_complete and (
        not workflow_requested or bool(real_http_actions)
    )
    mobile_bootstrap = str(archetype or "").strip().lower() == "mobile_app"
    bootstrap_done = product_complete and (bool(turn_completed) or not mobile_bootstrap)
    if not bootstrap_done:
        if workflow_requested and publish_status == "published" and not real_http_actions:
            raise RuntimeError(
                f"bootstrap for business:{slug} published the access shell but never materialized "
                "a real /app workflow action"
            )
        if product_complete and mobile_bootstrap and not turn_completed:
            raise RuntimeError(
                f"bootstrap for business:{slug} stopped before its mobile release phase completed"
            )
        if product_complete and not turn_completed:
            raise RuntimeError(
                f"bootstrap for business:{slug} stopped before its final product pass completed"
            )
        raise RuntimeError(
            f"bootstrap for business:{slug} exhausted its iteration budget before publishing "
            f"(surface status={publish_status})"
        )

    bootstrap_completion_status = "completed"

    live_action_verification: dict[str, Any] = {
        "action_execution_required": False,
        "status": "not_required",
        "live_build_id": "",
        "actions": [],
        "verified_action": "",
        "verified_at": "",
        "receipt_path": "",
        "blocker": "",
    }
    if workflow_requested:
        live_action_verification = _bootstrap_live_action_execution_verification(store, slug)
    action_execution_verified = (
        not workflow_requested
        or str(live_action_verification.get("status") or "").strip().lower() == "action_verified"
    )
    live_action_execution_status = (
        "action_verified"
        if action_execution_verified
        else "pending"
    )
    if workflow_requested and not action_execution_verified:
        verification_blocker = str(
            live_action_verification.get("blocker")
            or "no successful signed-in live action execution receipt exists for the current live build"
        ).strip()
        final_response = (
            final_response.rstrip()
            + ("\n\n" if final_response.strip() else "")
            + "Signed-in live action execution verification is pending: "
            + verification_blocker
            + ". Full browser workflow E2E remains required for the requested product workflow: "
            + "save and exact-ref reopen, plus each requested revise, copy, export, or delete step."
        )
    elif workflow_requested:
        final_response = (
            final_response.rstrip()
            + ("\n\n" if final_response.strip() else "")
            + "Signed-in live action execution is action-verified for the current build. "
            + "Full browser workflow E2E remains required for the requested product workflow: "
            + "save and exact-ref reopen, plus each requested revise, copy, export, or delete step."
        )

    wake_result: dict[str, object] | None = None
    if schedule:
        try:
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
            wake_state = "enabled"
            try:
                wake_items = wake_result.get("results") if isinstance(wake_result, dict) else []
                wake_item = next(
                    (
                        item
                        for item in (wake_items or [])
                        if isinstance(item, dict) and item.get("action") == "cron.ensure_ceo_wakeup"
                    ),
                    {},
                )
                wake_state = "enabled" if wake_item.get("enabled") else "paused"
            except Exception:
                wake_state = "unknown"
            _record_runtime_event(
                slug,
                kind="ceo_bootstrap",
                status="output",
                detail=f"wake schedule {wake_state} -> business:{slug} {schedule}",
                line=f"wake schedule {wake_state} -> business:{slug} {schedule}",
                command=command,
            )
        except Exception as exc:  # noqa: BLE001 - wake scheduling must never requeue a finished build
            _log.warning(
                "worker: bootstrap wake-cron schedule failed for business:%s (non-fatal): %s",
                slug,
                exc,
            )

    cents = max(0, int(round(cost_usd * 100)))
    completion_detail = (
        "CEO bootstrap build completed; signed-in live action execution is action-verified, but full browser workflow E2E remains unverified."
        if action_execution_verified and workflow_requested
        else (
            "CEO bootstrap build completed; signed-in live action execution verification is pending, and full browser workflow E2E remains unverified."
            if workflow_requested
            else "CEO bootstrap completed."
        )
    )
    runtime_completion_status = "completed"
    _record_runtime_event(
        slug,
        kind="ceo_bootstrap",
        status=runtime_completion_status,
        detail=completion_detail,
        command=command,
        trace={
            "kind": "turn",
            "entry_key": "turn:ceo_bootstrap",
            "label": "CEO bootstrap",
            "detail": final_response[:280].strip() or "CEO bootstrap completed.",
            "status": runtime_completion_status,
        },
    )
    return JobRunResult(
        result={
            "business_slug": slug,
            "final_response": final_response[:4000],
            "cost_usd": round(cost_usd, 6),
            "cost_status": cost_status,
            "surface_refresh": surface_refresh,
            "bootstrap_completion_status": bootstrap_completion_status,
            "live_action_execution_status": live_action_execution_status,
            "live_action_execution_verification": live_action_verification,
            "wake": wake_result,
            "agent_runtime": agent_runtime,
            "sdk_receipts": sdk_receipts,
        },
        actual_cost_cents=cents,
        billing_mode=(
            jobs.BILLING_MODE_PROVIDER_BROKER
            if agent_runtime == _WORKER_AGENT_RUNTIME_SDK
            else jobs.BILLING_MODE_JOB_RESERVATION
        ),
    )


def _x_posted_marker_path(slug: str, job_id: str) -> Path:
    # Durable per-job "already tweeted" marker. Lives OUTSIDE the re-materializable
    # cache/businesses/<slug> mirror (sibling ``.x-posted`` dir) so a concurrent
    # delete_local materialize can never prune it. This is the idempotency rail that
    # stops a retry of a 'failed-after-publish' X job from re-tweeting/re-charging.
    from .core import _slugify, _store

    store = _store()
    base = store._business_workspace_base().parent / ".x-posted"
    safe_slug = _slugify(str(slug)) or "global"
    safe_job = re.sub(r"[^A-Za-z0-9._-]+", "-", str(job_id)).strip("-") or "job"
    return base / safe_slug / f"{safe_job}.json"


def _read_x_posted_marker(slug: str, job_id: str) -> dict[str, Any] | None:
    path = _x_posted_marker_path(slug, job_id)
    try:
        if not path.is_file():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _write_x_posted_marker(slug: str, job_id: str, marker: Mapping[str, Any]) -> None:
    path = _x_posted_marker_path(slug, job_id)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(dict(marker), ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        # Best-effort durability rail; failure to persist the marker must not abort a
        # successful publish. Worst case is the pre-existing retry hazard, not a new one.
        pass


def channel_publish_outreach_handler(job: Job, channel: "ChannelPublisher") -> JobRunResult:
    """The ONE generic outreach-publish money envelope, shared by every ``ChannelPublisher``.

    The creative-credit reserve → publish → commit/release skeleton is byte-identical across X and
    Reddit (verified against the pre-extraction handlers); only the ``action`` / ``budget_bucket``
    scalars, the per-channel reservation/commit/release metadata, and the publish body itself differ,
    and those live behind ``channel``'s descriptor callables (see ``channel_registry.py``). Adding a
    channel is one ``ChannelPublisher`` — this envelope is never forked.

    Money-safety invariant (unchanged from the two originals): reserve once; on success commit; on a
    failure after a real side effect commit-partial (never release a shipped post's hold); on a failure with
    no side effect release; a finalization failure re-raises with both errors. Provider/receipt/marker
    calls resolve through this ``worker`` module + ``core`` so existing monkeypatches still apply."""
    from . import business_credits as takyon_business_credits
    from . import core as takyon_core
    from .channel_registry import PublishContext

    payload = job.payload or {}
    slug = job.business_slug
    body = str(payload.get("body") or "").strip()
    # Per-channel required-input guards (X: body; Reddit: subreddit-or-thread) run in publish()/here.
    if channel.slug == "x" and not body:
        raise RuntimeError("x publish job is missing a body")
    if channel.slug == "reddit":
        subreddit = str(payload.get("subreddit") or "").strip()
        thread_external_id = str(payload.get("thread_external_id") or "").strip()
        if not thread_external_id and not subreddit:
            raise RuntimeError("reddit publish job is missing a subreddit or thread_external_id")

    work_request_id = str(payload.get("work_request_id") or "").strip()
    reservation_key = f"{channel.slug}-publish:{job.id}"
    action = channel.credit_action
    bucket = channel.budget_bucket
    reservation: dict[str, Any] | None = None
    credit_result: dict[str, Any] | None = None
    finalized = False

    ctx = PublishContext(
        job=job,
        slug=slug,
        payload=payload,
        body=body,
        work_request_id=work_request_id,
        reservation_key=reservation_key,
    )

    if work_request_id:
        _update_work_request(
            slug,
            work_request_id,
            status="running",
            payload_updates={"worker_job_id": str(job.id)},
        )

    # Idempotency guard (money-gate integrity): if this exact job already shipped AND committed on a
    # prior attempt, re-derive + re-write the receipt without re-posting/re-charging. X's durable
    # posted-marker drives this; Reddit's hook is a no-op (nothing to replay).
    replay_result = channel.replay_if_complete(ctx)
    if replay_result is not None:
        return replay_result

    try:
        reservation = takyon_core._reserve_creative_credits(
            slug,
            action=action,
            reservation_key=reservation_key,
            budget_bucket=bucket,
            metadata=channel.reservation_metadata(ctx),
        )
    except takyon_business_credits.InsufficientCreativeCredits as exc:
        if work_request_id:
            _update_work_request(
                slug,
                work_request_id,
                status="failed",
                payload_updates={"worker_error": str(exc), "budget_bucket": bucket},
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

    ctx.reservation = reservation
    outcome = None
    try:
        outcome = channel.publish(ctx)
        credit_result = takyon_core._commit_creative_credits(
            reservation_key,
            action=action,
            budget_bucket=bucket,
            metadata=channel.commit_metadata(ctx, outcome),
        )
        finalized = True
        outcome.extra["credit_result"] = credit_result
        post_id = outcome.post_id
        post_url = channel.finalize_post_url(ctx, outcome)
        artifacts = channel.record_result(
            ctx,
            outcome,
            job_id=str(job.id),
            payload=payload,
            post_id=post_id,
            post_url=post_url,
            provider_response=outcome.provider_response,
            media=outcome.media,
            credits_charged=int(
                (credit_result or {}).get("actual_credits")
                or (reservation or {}).get("requested_credits")
                or 0
            ),
            budget_bucket=str(
                (credit_result or {}).get("budget_bucket")
                or (reservation or {}).get("budget_bucket")
                or bucket
            ).strip()
            or bucket,
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
                        or bucket
                    ).strip()
                    or bucket,
                    "channel_budget": (credit_result or {}).get("channel_budget", {}),
                },
            )
        return JobRunResult(
            result={
                "business_slug": slug,
                "provider": channel.slug,
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
                    or bucket
                ).strip()
                or bucket,
                "channel_budget": (credit_result or {}).get("channel_budget", {}),
            },
            actual_cost_cents=0,
        )
    except Exception as exc:
        finalization_error: Exception | None = None
        # Recover partial progress: on a publish failure ``outcome`` is None but the body may have
        # shipped side effects (X thread segments), tracked on ``ctx.partial`` — so commit-partial
        # (never release a shipped post's hold), exactly as the originals' ``if thread_posts:`` did.
        effective_outcome = outcome if outcome is not None else ctx.partial
        posted = bool(effective_outcome is not None and effective_outcome.posted)
        if reservation is not None and not finalized:
            try:
                if posted:
                    credit_result = takyon_core._commit_creative_credits(
                        reservation_key,
                        action=action,
                        budget_bucket=bucket,
                        metadata=channel.partial_failed_metadata(ctx, effective_outcome, exc),
                    )
                else:
                    credit_result = takyon_core._release_creative_credits(
                        reservation_key,
                        action=action,
                        budget_bucket=bucket,
                        metadata=channel.release_metadata(ctx, exc),
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
                    "post_id": (effective_outcome.post_id if effective_outcome is not None else "") or None,
                    "credits_charged": int(
                        (credit_result or {}).get("actual_credits")
                        or ((reservation or {}).get("requested_credits") if posted else 0)
                        or 0
                    ),
                    "budget_bucket": str(
                        (credit_result or {}).get("budget_bucket")
                        or (reservation or {}).get("budget_bucket")
                        or bucket
                    ).strip()
                    or bucket,
                    "channel_budget": (credit_result or {}).get("channel_budget", {}),
                },
            )
        if finalization_error is not None:
            raise RuntimeError(
                f"{exc} (credit finalization also failed: {finalization_error})"
            ) from exc
        raise


def x_publish_outreach_handler(job: Job) -> JobRunResult:
    """X outreach publish — the generic envelope dispatched with the X ``ChannelPublisher``."""
    from .channel_registry import X_CHANNEL

    return channel_publish_outreach_handler(job, X_CHANNEL)


def reddit_publish_outreach_handler(job: Job) -> JobRunResult:
    """Reddit outreach publish — the generic envelope dispatched with the Reddit ``ChannelPublisher``."""
    from .channel_registry import REDDIT_CHANNEL

    return channel_publish_outreach_handler(job, REDDIT_CHANNEL)


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

        from .turn_runtime import _business_workspace_execution_context
        from .core import (
            TakyonStore,
            _assert_active_parent_operator_task,
            _bound_operator_task_context,
        )

        parent_operator_task = (
            payload.get("parent_operator_task")
            if isinstance(payload.get("parent_operator_task"), Mapping)
            else {}
        )
        parent_run_id = str(parent_operator_task.get("run_id") or "").strip()
        parent_task_kind = str(parent_operator_task.get("task_kind") or "").strip().lower()
        try:
            parent_attempt = int(parent_operator_task.get("attempt") or 0)
        except (TypeError, ValueError):
            parent_attempt = 0
        try:
            parent_deadline_at = max(
                0.0, float(parent_operator_task.get("deadline_at") or 0.0)
            )
        except (TypeError, ValueError):
            parent_deadline_at = 0.0
        if parent_task_kind != "ceo_bootstrap" or not parent_run_id or parent_attempt < 1:
            parent_run_id = ""
            parent_task_kind = ""
            parent_attempt = 0
            parent_deadline_at = 0.0

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
            # A deferred bootstrap child executes under its runtime-authored parent identity, not
            # the child work-request id. This lets bounded rails such as Taste persist an exact
            # parent-attempt blocker that the outer CEO watchdog can observe before another call.
            bound_context: dict[str, Any] = {
                "run_id": parent_run_id or work_request_id,
                "task_kind": parent_task_kind or tool_name,
            }
            if parent_attempt > 0:
                bound_context["attempt"] = parent_attempt
            if parent_deadline_at > 0.0:
                bound_context["deadline_at"] = parent_deadline_at
            with _bound_operator_task_context(**bound_context):
                if parent_run_id:
                    _assert_active_parent_operator_task(
                        TakyonStore(operator_user_id=owner_user_id),
                        f"starting delegated {tool_name}",
                    )
                raw = handler_fn(dict(args))
                active_claim = jobs.current_job_claim()
                if active_claim is not None:
                    active_claim.assert_owned(f"recording {tool_name} result")
                if parent_run_id:
                    _assert_active_parent_operator_task(
                        TakyonStore(operator_user_id=owner_user_id),
                        f"recording delegated {tool_name} result",
                    )
        result = _parse_jsonish_output(str(raw or ""))
        if not isinstance(result, dict) or not result:
            result = {"success": False, "error": f"{tool_name} returned no parseable result"}
    except jobs.JobClaimLost:
        # The newer attempt owns both the job row and work-request reconciliation.  A stale wrapper
        # must not overwrite that run with its late result/error.
        raise
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


def store_mobile_release_handler(job: Job) -> JobRunResult:
    """Execute a deferred ``store.build`` (business_publish_mobile_release) on the worker plane.

    Reuses the deferred-operator-tool machinery exactly like claude.agent_task: the tool re-runs
    INLINE here (TAKYON_WORKER_PROCESS suppresses re-deferral) and owns its own money flow on the
    creative-credit rail (reserve→settle-at-trigger→release), so this wrapper carries no estimate and
    run_one never double-settles. Fail-closed until the real EAS builder lands: the tool returns an
    ``eas_builder_unconfigured`` result, which becomes a recorded blocked/failed run — never an
    uncharged build."""
    from .core import handle_business_publish_mobile_release

    return _operator_tool_task_handler(
        job, tool_name="business_publish_mobile_release", handler_fn=handle_business_publish_mobile_release
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


def openmeter_sync_handler(job: Job) -> JobRunResult:
    """Run an ``openmeter.sync`` job FAIL-SOFT: the OpenMeter access/customer/plan sync is a downstream
    usage MIRROR, never a runtime gate and never a CEO-recoverable blocker. A degraded mirror (provider
    404/timeout/transport error, misconfigured ``OPENMETER_URL``, missing target, anything) must NOT
    turn into a ``failed``/``blocked`` job — that becomes a "Resolve Openmeter sync failure" task that
    burns CEO recovery cycles and counts toward "N blockers need CEO recovery" on a fresh business.

    So this handler swallows every error and returns a ``completed`` JobRunResult carrying a truthful
    ``{ok: False, degraded: True, error}`` summary instead of raising. The local ``app_usage_events``
    rail is the source of truth for usage/entitlement; OpenMeter is a best-effort analytics mirror, so a
    completed-but-degraded job is the honest receipt — the mirror stays stale (visible in the result)
    until the operator fixes ``OPENMETER_URL``/token, but no business task goes FAILED."""
    from .core import _run_openmeter_sync_job, _store

    payload = job.payload if isinstance(job.payload, Mapping) else {}
    scope = str(payload.get("scope") or "").strip().lower()
    try:
        result = _run_openmeter_sync_job(_store(), job.business_slug, payload)
        summary = result if isinstance(result, Mapping) else {}
        if summary.get("configured") and not summary.get("ok"):
            # The sync ran but the mirror is degraded (e.g. provider 404). Log-and-skip silently; the
            # job COMPLETES with the truthful degraded summary so it never becomes a recoverable blocker.
            _log.info(
                "openmeter sync mirror degraded (business=%s scope=%s); non-fatal, job completes: %s",
                job.business_slug,
                scope or "unknown",
                summary.get("error"),
            )
            return JobRunResult(
                result={**dict(summary), "degraded": True, "non_fatal": True}, actual_cost_cents=0
            )
        return JobRunResult(result=result, actual_cost_cents=0)
    except Exception as exc:  # fail-soft: a degraded mirror is NEVER a CEO-recoverable blocker
        _log.warning(
            "openmeter sync job degraded (business=%s scope=%s); non-fatal mirror, job completes: %s",
            job.business_slug,
            scope or "unknown",
            exc,
        )
        return JobRunResult(
            result={
                "configured": True,
                "ok": False,
                "degraded": True,
                "non_fatal": True,
                "scope": scope or None,
                "error": str(exc),
            },
            actual_cost_cents=0,
        )


# The kind→handler registry the drain consults. New job kinds register here.
HANDLERS: dict[str, jobs.Handler] = {
    "ceo_bootstrap": ceo_bootstrap_handler,
    "ceo_wake": ceo_wake_handler,
    "x.publish_outreach": x_publish_outreach_handler,
    "reddit.publish_outreach": reddit_publish_outreach_handler,
    "claude.agent_task": claude_agent_task_handler,
    "product.surface_refresh": product_surface_refresh_handler,
    "store.build": store_mobile_release_handler,
    "product_action": product_action_handler,
    "openmeter.sync": openmeter_sync_handler,
}


# ── the drain loop ──────────────────────────────────────────────────────────────────────────────


def _conn_is_safebox_authority(conn) -> bool:
    """True only when ``conn`` is logged in as the Safebox DB authority role."""
    raw = getattr(conn, "_pg", conn)
    try:
        from .runtime_app import assert_takyon_pg_role
    except ImportError:  # pragma: no cover - alternate load path
        from plugins.takyon.runtime_app import assert_takyon_pg_role

    try:
        assert_takyon_pg_role(raw, "safebox")
    except Exception:
        return False
    return True


def _maybe_prune_expired_sdk_sessions(conn) -> int:
    """Run the bounded cross-tenant retention sweep only on an operator worker.

    The process-local cadence prevents every empty queue poll from rescanning
    the transcript table. Forced RLS and the operator-only table grant remain
    the database-side authority boundary.
    """

    global _SDK_SESSION_RETENTION_NEXT_SWEEP_AT
    observed = time.monotonic()
    with _SDK_SESSION_RETENTION_SWEEP_LOCK:
        if observed < _SDK_SESSION_RETENTION_NEXT_SWEEP_AT:
            return 0
        _SDK_SESSION_RETENTION_NEXT_SWEEP_AT = (
            observed + _SDK_SESSION_RETENTION_SWEEP_INTERVAL_SECONDS
        )
    from .claude_sdk_sessions import prune_expired_sdk_sessions_global
    from .runtime_app import assert_takyon_pg_role

    raw = getattr(conn, "_pg", conn)
    assert_takyon_pg_role(raw, "operator")
    return prune_expired_sdk_sessions_global(conn)


def drain_tick(
    conn,
    *,
    worker_id: str,
    handlers: Mapping[str, jobs.Handler] | None = None,
    kinds: list[str] | tuple[str, ...] | None = None,
    owner_user_id: str | None = None,
    claim_pool_id: str | None = None,
    exclusive_pool: bool = False,
    dispatch: bool = True,
    stop: threading.Event | None = None,
    max_jobs: int | None = None,
    heartbeat_conn_factory=None,
    min_queue_age_seconds: float | None = None,
    worker_release_sha: str | None = None,
) -> dict[str, int]:
    """One drain tick on an open autocommit connection: optionally dispatch due wakes, reclaim stale
    claims, then drain queued jobs through ``jobs.run_one`` until the queue is empty (or ``stop`` is
    set, or ``max_jobs`` reached). Returns counts including dispatch, stale-job reclaim, orphaned
    product-usage hold release, and job outcomes. Pure of process concerns (signals, sleeping,
    reconnect) so it is directly testable against a real Postgres connection."""
    handlers = HANDLERS if handlers is None else handlers
    counts = {
        "dispatched": 0,
        "requeued": 0,
        "usage_holds_released": 0,
        "drained": 0,
        "completed": 0,
        "blocked": 0,
        "failed": 0,
    }

    if dispatch:
        enabled_kinds = {str(kind).strip() for kind in (kinds or []) if str(kind).strip()}
        if not enabled_kinds or "ceo_wake" in enabled_kinds:
            counts["dispatched"] += wakes.dispatch_due_wakes(conn)
        if not enabled_kinds or "product_action" in enabled_kinds:
            counts["dispatched"] += _dispatch_due_action_jobs(conn)
    counts["requeued"] = jobs.requeue_stale(
        conn,
        older_than_seconds=_env_int("TAKYON_WORKER_STALE_SECONDS", _STALE_SECONDS),
        worker_id=worker_id,
    )
    if _conn_is_safebox_authority(conn):
        counts["usage_holds_released"] = app_usage.reconcile_held_usage(
            conn,
            older_than_seconds=_env_int(
                "TAKYON_APP_USAGE_HOLD_TTL_SECONDS",
                _APP_USAGE_HOLD_TTL_SECONDS,
            ),
        )
    else:
        try:
            pruned_sessions = _maybe_prune_expired_sdk_sessions(conn)
            if pruned_sessions:
                _log.info(
                    "worker[%s]: pruned %d expired Claude SDK sessions",
                    worker_id,
                    pruned_sessions,
                )
        except Exception as exc:  # noqa: BLE001 - a failed sweep must not stop queue work
            _log.warning(
                "worker[%s]: Claude SDK session retention sweep failed: %s",
                worker_id,
                exc,
            )

    while stop is None or not stop.is_set():
        outcome: JobOutcome | None = jobs.run_one(
            conn,
            worker_id=worker_id,
            handlers=handlers,
            kinds=kinds,
            owner_user_id=owner_user_id,
            claim_pool_id=claim_pool_id,
            exclusive_pool=exclusive_pool,
            heartbeat_conn_factory=heartbeat_conn_factory,
            min_queue_age_seconds=min_queue_age_seconds,
            worker_release_sha=worker_release_sha,
            billing_mode_resolver=_job_billing_mode,
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
        if heartbeat_conn_factory is not None:
            # Long handlers keep the claim connection idle while heartbeats use a separate short-lived
            # connection. Do not reuse that stale claim socket for the next claim; the outer worker loop
            # opens a fresh connection on the next tick.
            break
        if max_jobs is not None and counts["drained"] >= max_jobs:
            break

    # Retry at most one ambiguous terminal settlement after useful queue work. The blocked parent is
    # never rerun; Safebox's reservation finalizer is idempotent, and owner scoping matches claims.
    reconcile_conn = conn
    close_reconcile_conn = False
    if heartbeat_conn_factory is not None and counts["drained"] > 0:
        try:
            reconcile_conn = heartbeat_conn_factory()
            close_reconcile_conn = True
        except Exception as exc:  # noqa: BLE001 - leave the durable marker for the next tick
            reconcile_conn = None
            _log.warning(
                "worker[%s]: could not open a fresh pending-settlement connection: %s",
                worker_id,
                exc,
            )
    try:
        if reconcile_conn is not None and callable(getattr(reconcile_conn, "transaction", None)):
            try:
                jobs.reconcile_pending_terminal_settlements(
                    reconcile_conn,
                    owner_user_id=owner_user_id,
                    retry_after_seconds=_env_int("TAKYON_JOB_SETTLEMENT_RETRY_SECONDS", 30),
                    limit=1,
                )
            except Exception as exc:  # noqa: BLE001 - durable marker remains the retry source
                _log.warning(
                    "worker[%s]: pending billing settlement reconciliation failed: %s",
                    worker_id,
                    exc,
                )
    finally:
        if close_reconcile_conn and reconcile_conn is not None:
            try:
                reconcile_conn.close()
            except Exception:
                pass

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
    owner_user_id: str | None = None,
    once: bool = False,
    max_jobs: int | None = None,
    database_url: str | None = None,
) -> int:
    """Back-compat entrypoint: construct the one worker constructor and run it.

    The process loop moved verbatim to ``worker_pool.WorkerPool.run()`` (modularization
    Stage 1) — size/dispatcher/identity are constructor topology there. Callers that
    already hold this signature (scripts, tests) keep working; new call sites should
    construct a ``WorkerPool`` lane factory directly."""
    from .worker_pool import WorkerPool

    return WorkerPool.local_threads(
        worker_id=worker_id,
        poll_interval=poll_interval,
        dispatch=dispatch,
        kinds=kinds,
        owner_user_id=owner_user_id,
        once=once,
        max_jobs=max_jobs,
        database_url=database_url,
    ).run()


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
