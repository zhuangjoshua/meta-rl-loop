"""Decoupled side-LLM summarizer for the business Tasks panel.

This is the operator's design: the Tasks-panel milestone rollup is produced by a
SEPARATE auxiliary LLM call whose output never re-enters the CEO's conversation
context (so it does not pollute prompt caching or the agent loop) — "like a
message delta" rather than a CEO tool call.

It takes the raw runtime trace the gateway has already assembled for a business
(jobs + runtime/agent events + work-requests, i.e. ``overview["tasks"]``) and
returns a handful of MILESTONE cards:

    {title, description, category, status}

with ``category`` in :data:`ROLLUP_CATEGORIES` and ``status`` in
:data:`ROLLUP_STATUSES`. The Tasks panel renders these as the PRIMARY rows and
nests the raw worker/runtime events underneath via the existing
``current_task_id`` grouping in ``_takyon_live_state_payload``.

Key properties (mirrors the title-generation side-call in
``agent/title_generator.py``):

* Decoupled — uses ``agent.auxiliary_client.call_llm(task="task_rollup", ...)``,
  its own cheap auto-resolved model, separate from the CEO's main conversation
  and prompt cache. Nothing here is ever appended to the agent's message list.
* Cached — keyed by a hash of the business slug + the materially-relevant fields
  of the trace, with a short TTL. The LLM is only called when the trace changes;
  every poll in between reuses the cached milestones, so it never runs unbounded.
* Fail-open — any failure (no provider, timeout, bad JSON) returns ``[]`` so the
  caller falls straight back to the existing deterministic task labels. The panel
  never blocks on this call.
* Cheap — a single small auxiliary completion gated by the cache; it is an LLM
  call but bounded to "only on change" with a hard token/timeout cap.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import threading
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Operator-approved taxonomy. Kept in lockstep with
# tui_gateway.server._TAKYON_TASK_CATEGORIES and core.OPERATOR_UPDATE_CATEGORIES.
ROLLUP_CATEGORIES = ("RESEARCH", "PRODUCT", "LAUNCH", "GROWTH", "OPS")
# Milestone lifecycle states the side-LLM may assign. PLANNED/RUNNING/BLOCKED/
# DONE/FAILED map onto the canonical Tasks-panel status pills downstream.
ROLLUP_STATUSES = ("PLANNED", "RUNNING", "BLOCKED", "DONE", "FAILED")

# Lowercased status -> canonical raw status the gateway's canonical_task_status()
# understands (it maps these onto the PLANNED/RUNNING/BLOCKED/DONE/FAILED pills).
_STATUS_TO_RAW = {
    "planned": "queued",
    "running": "running",
    "blocked": "blocked",
    "done": "completed",
    "failed": "failed",
}

# Bounds: keep the side-call small and cheap.
_MAX_TRACE_ROWS = 24
_MAX_MILESTONES = 6
_DEFAULT_TTL_SECONDS = max(
    5.0, float(os.getenv("TAKYON_TASK_ROLLUP_TTL_SECONDS", "45") or 45)
)
_DEFAULT_TIMEOUT_SECONDS = max(
    3.0, float(os.getenv("TAKYON_TASK_ROLLUP_TIMEOUT_SECONDS", "20") or 20)
)
_MAX_TOKENS = 700

# Process-global cache: slug -> {"hash": str, "at": float, "milestones": list}.
# Guarded by a lock because the gateway can serve concurrent workspace polls.
_CACHE: Dict[str, Dict[str, Any]] = {}
_CACHE_LOCK = threading.Lock()
# Per-slug in-flight guard so concurrent polls don't fan out duplicate LLM calls
# for the same changed trace.
_INFLIGHT: Dict[str, float] = {}

_SYSTEM_PROMPT = (
    "You summarize a startup's autonomous-CEO runtime activity into a short list "
    "of operator-facing MILESTONE cards. You are given raw, low-level work rows "
    "(tool runs, jobs, agent traces). Roll them up into the handful of higher-"
    "level intents the CEO is actually pursuing — e.g. 'Build the plant-identify "
    "workflow', 'Research the ghostwriting market', 'Launch the offer page'.\n\n"
    "Return STRICT JSON: an object {\"milestones\": [...]} with at most "
    f"{_MAX_MILESTONES} items. Each milestone is an object with:\n"
    "  title: the intent, <=8 words, verb-led, never a raw tool name.\n"
    "  description: one short sentence.\n"
    f"  category: one of {', '.join(ROLLUP_CATEGORIES)}.\n"
    f"  status: one of {', '.join(ROLLUP_STATUSES)}.\n"
    "Group related low-level rows under one milestone. Prefer fewer, clearer "
    "milestones. Order most-active first. Output ONLY the JSON object."
)


def _trace_rows(tasks: List[Any]) -> List[Dict[str, str]]:
    """Pick the materially-relevant fields from each raw task row.

    Only the fields that actually describe the work (label/detail/status/source)
    feed both the cache hash and the LLM, so cosmetic churn (timestamps, ids)
    never re-triggers a summarization.
    """
    rows: List[Dict[str, str]] = []
    for task in tasks:
        if not isinstance(task, dict):
            continue
        label = str(task.get("label") or task.get("title") or "").strip()
        detail = str(task.get("detail") or task.get("description") or "").strip()
        status = str(task.get("status") or "").strip()
        source = str(task.get("source") or "").strip()
        if not (label or detail):
            continue
        rows.append(
            {
                "label": label[:160],
                "detail": detail[:240],
                "status": status[:40],
                "source": source[:40],
            }
        )
        if len(rows) >= _MAX_TRACE_ROWS:
            break
    return rows


def _trace_hash(slug: str, rows: List[Dict[str, str]]) -> str:
    payload = json.dumps([slug, rows], sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _cached(slug: str, trace_hash: str) -> Optional[List[Dict[str, Any]]]:
    with _CACHE_LOCK:
        entry = _CACHE.get(slug)
        if not isinstance(entry, dict):
            return None
        if entry.get("hash") != trace_hash:
            return None
        if (time.monotonic() - float(entry.get("at") or 0.0)) > _DEFAULT_TTL_SECONDS:
            return None
        milestones = entry.get("milestones")
        return list(milestones) if isinstance(milestones, list) else None


def _store(slug: str, trace_hash: str, milestones: List[Dict[str, Any]]) -> None:
    with _CACHE_LOCK:
        _CACHE[slug] = {
            "hash": trace_hash,
            "at": time.monotonic(),
            "milestones": list(milestones),
        }


def _claim_inflight(slug: str) -> bool:
    """Return True if this caller should run the LLM (no other call is in-flight).

    A stale in-flight marker older than the timeout is reclaimed so a crashed
    call never wedges the slug.
    """
    now = time.monotonic()
    with _CACHE_LOCK:
        started = _INFLIGHT.get(slug)
        if started is not None and (now - started) < _DEFAULT_TIMEOUT_SECONDS:
            return False
        _INFLIGHT[slug] = now
        return True


def _release_inflight(slug: str) -> None:
    with _CACHE_LOCK:
        _INFLIGHT.pop(slug, None)


def _coerce_milestone(raw: Any, index: int) -> Optional[Dict[str, Any]]:
    if not isinstance(raw, dict):
        return None
    title = str(raw.get("title") or "").strip()
    if not title:
        return None
    category = str(raw.get("category") or "").strip().upper()
    if category not in ROLLUP_CATEGORIES:
        category = "PRODUCT"
    status_token = str(raw.get("status") or "").strip().lower()
    raw_status = _STATUS_TO_RAW.get(status_token, "running")
    description = str(raw.get("description") or "").strip()
    return {
        "id": f"rollup:{index}",
        "source": "task_rollup",
        "label": title[:160],
        "title": title[:160],
        "description": description[:280],
        "category": category,
        # canonical_task_status() in the gateway maps this onto the pill label.
        "status": raw_status,
        "detail": (description or title)[:280],
    }


def _parse_milestones(content: str) -> List[Dict[str, Any]]:
    text = (content or "").strip()
    if not text:
        return []
    # Tolerate fenced code blocks / leading prose around the JSON object.
    if "```" in text:
        match = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
        if match:
            text = match.group(1).strip()
    if not text.startswith("{"):
        brace = text.find("{")
        if brace != -1:
            text = text[brace:]
    try:
        data = json.loads(text)
    except Exception:
        return []
    items = data.get("milestones") if isinstance(data, dict) else None
    if not isinstance(items, list):
        return []
    milestones: List[Dict[str, Any]] = []
    for raw in items:
        coerced = _coerce_milestone(raw, len(milestones))
        if coerced is not None:
            milestones.append(coerced)
        if len(milestones) >= _MAX_MILESTONES:
            break
    return milestones


def _run_side_llm(rows: List[Dict[str, str]]) -> List[Dict[str, Any]]:
    """Single decoupled auxiliary completion. Returns [] on any failure."""
    # Import lazily so the gateway payload path has no hard dependency on the
    # auxiliary client (keeps the fail-open contract honest if it can't import).
    from agent.auxiliary_client import call_llm

    user_payload = json.dumps({"trace": rows}, ensure_ascii=False)
    messages = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": f"Runtime trace rows:\n{user_payload}"},
    ]
    response = call_llm(
        task="task_rollup",
        messages=messages,
        max_tokens=_MAX_TOKENS,
        temperature=0.2,
        timeout=_DEFAULT_TIMEOUT_SECONDS,
    )
    content = (response.choices[0].message.content or "") if response else ""
    return _parse_milestones(content)


def summarize_task_milestones(
    slug: str,
    tasks: List[Any],
) -> List[Dict[str, Any]]:
    """Return side-LLM milestone cards for ``slug`` from the raw task trace.

    Cached by a hash of the trace with a short TTL: the LLM is called only when
    the trace materially changes. Fail-open — returns ``[]`` (caller falls back
    to deterministic labels) on missing provider, timeout, or unparseable output.

    The returned cards are shaped like the gateway's other primary task rows
    (id/source/label/title/description/category/status/detail) so
    ``_takyon_live_state_payload`` canonicalizes and nests under them with no
    special-casing.
    """
    slug = str(slug or "").strip()
    if not slug:
        return []
    # Allow operators to hard-disable the side-call (pure deterministic fallback).
    if os.getenv("TAKYON_TASK_ROLLUP_DISABLED", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }:
        return []

    rows = _trace_rows(tasks if isinstance(tasks, list) else [])
    if not rows:
        return []

    trace_hash = _trace_hash(slug, rows)
    cached = _cached(slug, trace_hash)
    if cached is not None:
        return cached

    # Only one in-flight summarization per slug at a time. If another poll is
    # already summarizing this changed trace, serve the previous (stale-but-
    # valid) milestones rather than fanning out a second LLM call.
    if not _claim_inflight(slug):
        with _CACHE_LOCK:
            entry = _CACHE.get(slug)
        prev = entry.get("milestones") if isinstance(entry, dict) else None
        return list(prev) if isinstance(prev, list) else []

    try:
        milestones = _run_side_llm(rows)
    except Exception as exc:
        logger.warning("Task-rollup side-LLM failed for %s: %s", slug, exc)
        logger.debug("Task-rollup traceback", exc_info=True)
        # Fail-open: cache an empty result against this hash so a persistently
        # failing provider does not retry the LLM on every poll until the trace
        # changes. The caller falls back to deterministic labels.
        _store(slug, trace_hash, [])
        return []
    finally:
        _release_inflight(slug)

    _store(slug, trace_hash, milestones)
    return milestones


def reset_cache() -> None:
    """Clear the rollup cache (test hook)."""
    with _CACHE_LOCK:
        _CACHE.clear()
        _INFLIGHT.clear()
