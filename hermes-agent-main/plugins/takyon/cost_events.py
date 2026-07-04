"""Granular cost/log observability ledgers (migration 0070) — the debugging record of what an
agent DID and what it COST, per task and per business, at every granularity: one row per LLM call,
tool call, CEO turn, worker job, and app-gateway provider call.

These writers are OBSERVABILITY, not money authority. The money rails (billing.py, app_usage.py,
business_credits.py) stay the only gates; a cost event row correlates to its money row via
reservation_key / job_id. Every write here is best-effort: it runs in its own (sub)transaction so
a failure can never poison the caller's transaction, and every public function swallows its own
errors — a broken event write must never block a turn, a job, or a customer request.

Plane split (two tables, per the operator ask):
  * ``operator_cost_events`` — the Takyon operator/CEO plane. Direct INSERT under the operator /
    safebox / runtime roles (append-only grant). The app plane has no access of any kind.
  * ``app_cost_events`` — the product/subuser plane. ALL writers go through the SECURITY DEFINER
    port ``takyon_app_record_cost_event`` (the app runtime role can only execute the port, never
    touch the table; it cannot read events back).

Kill switch: ``TAKYON_COST_EVENTS_DISABLED=1`` disables all recording (incident valve).
"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Any, Mapping

logger = logging.getLogger(__name__)

# Event kinds written today. The column is deliberately an open CHECK(length>0) text, not an enum:
# new kinds must not need a migration. Keep this list in sync as call sites grow.
KIND_LLM_CALL = "llm_call"
KIND_TOOL_CALL = "tool_call"
KIND_PROVIDER_CALL = "provider_call"
KIND_TURN = "turn"
KIND_JOB = "job"
KIND_LOG = "log"
KIND_METRICS = "metrics"

_MAX_TEXT = 2000
_MAX_LABEL = 200

# Operability: the recorder must stay silent-by-design on the hot path, but a COMPLETELY broken
# ledger (bad grant, missing table, dead pool) should be visible in the service log. First failure
# per process logs at WARNING; the rest stay at DEBUG.
_failure_warned = False


def _log_write_failure(context: str, exc: Exception) -> None:
    global _failure_warned
    if not _failure_warned:
        _failure_warned = True
        logger.warning("cost event write failed (%s) — further failures logged at DEBUG: %s", context, exc)
    else:
        logger.debug("cost event write failed (%s): %s", context, exc)


def _disabled() -> bool:
    return str(os.environ.get("TAKYON_COST_EVENTS_DISABLED") or "").strip() == "1"


def _clip(value: Any, limit: int = _MAX_LABEL) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    return text[:limit]


def _int_or_none(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return None


def _payload_json(payload: Mapping[str, Any] | None) -> str:
    if not payload:
        return "{}"
    try:
        text = json.dumps(payload, ensure_ascii=False, default=str)
    except Exception:
        return "{}"
    if len(text) > 16_000:  # keep rows bounded; the correlated receipts hold the full story
        return json.dumps({"truncated": True, "head": text[:8_000]}, ensure_ascii=False)
    return text


def _own_transaction(conn):
    """The caller's conn, in a subtransaction that cannot poison the caller's work.

    psycopg3 ``transaction()`` on the RAW connection is exactly the right primitive: on an
    autocommit conn it opens/commits its own transaction; inside a caller's open transaction it
    becomes a SAVEPOINT, so a failed event INSERT rolls back only itself.
    """
    raw = getattr(conn, "_pg", conn)
    return raw, raw.transaction()


_OPERATOR_INSERT_SQL = """
insert into operator_cost_events (
    business_slug, user_id, job_id, run_id, session_id, task_kind,
    event_kind, name, status,
    provider, model,
    input_tokens, output_tokens, cache_read_tokens, cache_write_tokens, reasoning_tokens,
    cost_microusd, cost_status, reservation_key, duration_ms, error, payload, started_at
) values (
    %s, %s, %s, %s, %s, %s,
    %s, %s, %s,
    %s, %s,
    %s, %s, %s, %s, %s,
    %s, %s, %s, %s, %s, %s::jsonb, %s
)
returning id
"""


def record_operator_cost_event(
    conn,
    *,
    event_kind: str,
    business_slug: str | None = None,
    user_id: str | None = None,
    job_id: str | None = None,
    run_id: str | None = None,
    session_id: str | None = None,
    task_kind: str | None = None,
    name: str | None = None,
    status: str = "ok",
    provider: str | None = None,
    model: str | None = None,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    cache_read_tokens: int | None = None,
    cache_write_tokens: int | None = None,
    reasoning_tokens: int | None = None,
    cost_microusd: int | None = None,
    cost_status: str | None = None,
    reservation_key: str | None = None,
    duration_ms: int | None = None,
    error: str | None = None,
    payload: Mapping[str, Any] | None = None,
    started_at=None,
) -> str | None:
    """Append one operator-plane cost/log event. Best-effort: returns the row id or None."""
    if _disabled():
        return None
    try:
        kind = _clip(event_kind, 60)
        if not kind:
            return None
        uid = str(user_id or "").strip() or None
        raw, txn = _own_transaction(conn)
        with txn:
            row = raw.execute(
                _OPERATOR_INSERT_SQL,
                (
                    _clip(business_slug), uid, _clip(job_id), _clip(run_id),
                    _clip(session_id), _clip(task_kind, 100),
                    kind, _clip(name), _clip(status, 40) or "ok",
                    _clip(provider, 100), _clip(model),
                    _int_or_none(input_tokens), _int_or_none(output_tokens),
                    _int_or_none(cache_read_tokens), _int_or_none(cache_write_tokens),
                    _int_or_none(reasoning_tokens),
                    _int_or_none(cost_microusd), _clip(cost_status, 40),
                    _clip(reservation_key), _int_or_none(duration_ms),
                    _clip(error, _MAX_TEXT), _payload_json(payload), started_at,
                ),
            ).fetchone()
        return str(row[0]) if row else None
    except Exception as exc:  # noqa: BLE001 — observability must never break the caller
        _log_write_failure(f"operator/{event_kind}", exc)
        return None


_APP_PORT_SQL = """
select takyon_app_record_cost_event(
    %s, %s, %s, %s, %s, %s, %s, %s,
    %s, %s, %s, %s, %s, %s, %s, %s,
    %s, %s, %s, %s, %s, %s
)
"""


def record_app_cost_event(
    conn,
    *,
    business_slug: str,
    event_kind: str,
    name: str | None = None,
    status: str = "ok",
    route: str | None = None,
    purpose: str | None = None,
    provider: str | None = None,
    model: str | None = None,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    cache_read_tokens: int | None = None,
    cache_write_tokens: int | None = None,
    cost_microusd: int | None = None,
    cost_status: str | None = None,
    reservation_key: str | None = None,
    provider_request_id: str | None = None,
    app_user_id: str | None = None,
    app_user_tier: str | None = None,
    duration_ms: int | None = None,
    error: str | None = None,
    payload: Mapping[str, Any] | None = None,
    started_at=None,
) -> str | None:
    """Append one subuser-plane cost/log event through the SECURITY DEFINER port.

    Works from every plane (the port is granted to app + authority roles) so app_cost_events has
    exactly one writer shape. Best-effort: returns the row id or None.
    """
    if _disabled():
        return None
    try:
        slug = _clip(business_slug)
        kind = _clip(event_kind, 60)
        if not slug or not kind:
            return None
        app_uid = str(app_user_id or "").strip() or None
        raw, txn = _own_transaction(conn)
        with txn:
            row = raw.execute(
                _APP_PORT_SQL,
                (
                    slug, kind, _clip(name), _clip(status, 40) or "ok",
                    _clip(route), _clip(purpose), _clip(provider, 100), _clip(model),
                    _int_or_none(input_tokens), _int_or_none(output_tokens),
                    _int_or_none(cache_read_tokens), _int_or_none(cache_write_tokens),
                    _int_or_none(cost_microusd), _clip(cost_status, 40),
                    _clip(reservation_key), _clip(provider_request_id),
                    app_uid, _clip(app_user_tier, 100),
                    _int_or_none(duration_ms), _clip(error, _MAX_TEXT),
                    _payload_json(payload), started_at,
                ),
            ).fetchone()
        return str(row[0]) if row else None
    except Exception as exc:  # noqa: BLE001 — observability must never break the caller
        _log_write_failure(f"app/{business_slug}/{event_kind}", exc)
        return None


# ---------------------------------------------------------------------------
# Operator-plane glue: context resolution + the two Takyon-owned choke points
# (per-LLM-call plugin hook, per-tool-call registration wrapper).
# ---------------------------------------------------------------------------


def operator_context() -> dict[str, str]:
    """Resolve who/where from the ambient Takyon session context (contextvars).

    All worker lanes (ceo_wake / ceo_bootstrap / deferred operator tools) bind these via
    ``gateway.session_context.set_session_vars`` + ``core._bound_operator_task_context`` before the
    agent runs, and both propagate onto the agent thread through ``contextvars.copy_context()``.
    """
    ctx = {"business_slug": "", "user_id": "", "task_kind": "", "run_id": "", "platform": ""}
    try:
        from gateway.session_context import get_session_env

        ctx["business_slug"] = str(get_session_env("TAKYON_SESSION_BUSINESS_SLUG", "") or "")
        ctx["user_id"] = str(get_session_env("TAKYON_SESSION_USER_ID", "") or "")
        ctx["task_kind"] = str(get_session_env("TAKYON_SESSION_TASK_KIND", "") or "")
        ctx["platform"] = str(get_session_env("TAKYON_SESSION_PLATFORM", "") or "")
    except Exception:
        pass
    try:
        from .core import _ACTIVE_OPERATOR_TASK_KIND, _ACTIVE_OPERATOR_TASK_RUN_ID

        ctx["run_id"] = str(_ACTIVE_OPERATOR_TASK_RUN_ID.get() or "")
        ctx["task_kind"] = ctx["task_kind"] or str(_ACTIVE_OPERATOR_TASK_KIND.get() or "")
    except Exception:
        pass
    return ctx


def record_operator_event_autoconn(**fields) -> None:
    """Open a pooled operator store connection and record; for hook sites that hold no conn."""
    try:
        from .core import TakyonStore

        store = TakyonStore()
        with store._connect() as conn:
            record_operator_cost_event(conn, **fields)
    except Exception as exc:  # noqa: BLE001
        _log_write_failure("autoconn", exc)


def post_api_request_hook(**kwargs) -> None:
    """Plugin hook (``post_api_request``): one row per LLM API call made by a Takyon agent.

    Registered by ``plugins/takyon/__init__.register``. Fires inside the generic agent loop, which
    passes the canonical usage buckets; cost is resolved fail-closed from ``agent/usage_pricing``
    (unpriced ⇒ cost NULL + cost_status 'unknown', never a guess).
    """
    if _disabled():
        return
    try:
        ctx = operator_context()
        # Only record Takyon-plane agent activity; a generic (non-Takyon) CLI session on the same
        # install has no business/task context and stays out of the business ledger.
        if not (ctx["business_slug"] or ctx["task_kind"] or ctx["platform"] == "takyon"
                or str(kwargs.get("platform") or "") == "takyon"):
            return
        usage = kwargs.get("usage") if isinstance(kwargs.get("usage"), Mapping) else {}
        model = str(kwargs.get("model") or "")
        provider = str(kwargs.get("provider") or "")

        cost_microusd = None
        cost_status = None
        try:
            from agent.usage_pricing import CanonicalUsage, estimate_usage_cost

            cu = CanonicalUsage(
                input_tokens=int(usage.get("input_tokens") or 0),
                output_tokens=int(usage.get("output_tokens") or 0),
                cache_read_tokens=int(usage.get("cache_read_tokens") or 0),
                cache_write_tokens=int(usage.get("cache_write_tokens") or 0),
                reasoning_tokens=int(usage.get("reasoning_tokens") or 0),
            )
            cost = estimate_usage_cost(
                model, cu,
                provider=provider or None,
                base_url=str(kwargs.get("base_url") or "") or None,
            )
            if cost.amount_usd is not None:
                cost_microusd = int(round(float(cost.amount_usd) * 1_000_000))
            cost_status = cost.status
        except Exception:
            cost_status = "unknown"

        duration_ms = None
        try:
            duration_ms = int(float(kwargs.get("api_duration") or 0) * 1000) or None
        except Exception:
            pass

        record_operator_event_autoconn(
            event_kind=KIND_LLM_CALL,
            business_slug=ctx["business_slug"] or None,
            user_id=ctx["user_id"] or None,
            job_id=ctx["run_id"] or None,
            run_id=ctx["run_id"] or None,
            session_id=str(kwargs.get("session_id") or "") or None,
            task_kind=ctx["task_kind"] or None,
            name=model or None,
            status="ok" if str(kwargs.get("finish_reason") or "") != "error" else "error",
            provider=provider or None,
            model=model or None,
            input_tokens=usage.get("input_tokens"),
            output_tokens=usage.get("output_tokens"),
            cache_read_tokens=usage.get("cache_read_tokens"),
            cache_write_tokens=usage.get("cache_write_tokens"),
            reasoning_tokens=usage.get("reasoning_tokens"),
            cost_microusd=cost_microusd,
            cost_status=cost_status,
            duration_ms=duration_ms,
            payload={
                "api_call_count": kwargs.get("api_call_count"),
                "finish_reason": kwargs.get("finish_reason"),
                "message_count": kwargs.get("message_count"),
                "assistant_content_chars": kwargs.get("assistant_content_chars"),
                "assistant_tool_call_count": kwargs.get("assistant_tool_call_count"),
            },
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug("post_api_request cost event failed: %s", exc)


def record_metrics_observation(
    *,
    provider: str,
    name: str,
    metrics: Mapping[str, Any] | None,
    business_slug: str | None = None,
    rows: list | None = None,
    identifiers: Mapping[str, Any] | None = None,
    status: str = "ok",
) -> None:
    """One row per provider METRICS READBACK — ad delivery insights (impressions/clicks/CTR/CPM/
    spend/…), post metrics, search-console/keyword data, web analytics, and any future readback.

    Deliberately NON-PRESCRIPTIVE: whatever metric object the provider returned is stored verbatim
    under payload["metrics"], so a new metric never needs schema work — it just shows up. Rows are
    capped to the first 50 to keep each event bounded (payload["rows_total"] keeps the true count;
    the channel's own receipt files hold the full row set). Best-effort by construction."""
    if _disabled():
        return
    try:
        ctx = operator_context()
        payload: dict[str, Any] = {"metrics": dict(metrics or {})}
        if rows is not None:
            payload["rows_total"] = len(rows)
            payload["rows"] = list(rows[:50])
        if identifiers:
            payload["identifiers"] = {
                str(k): v for k, v in dict(identifiers).items() if v not in (None, "")
            }
        record_operator_event_autoconn(
            event_kind=KIND_METRICS,
            business_slug=business_slug or (ctx.get("business_slug") or None),
            user_id=ctx.get("user_id") or None,
            job_id=ctx.get("run_id") or None,
            run_id=ctx.get("run_id") or None,
            task_kind=ctx.get("task_kind") or None,
            name=name,
            status=status,
            provider=provider,
            payload=payload,
        )
    except Exception as exc:  # noqa: BLE001 — observability must never break the readback
        _log_write_failure(f"metrics/{provider}", exc)


def _tool_call_business_slug(args: Mapping[str, Any] | None, ctx: Mapping[str, str]) -> str | None:
    if isinstance(args, Mapping):
        for key in ("business", "business_slug", "slug"):
            value = str(args.get(key) or "").strip()
            if value:
                return value
    return str(ctx.get("business_slug") or "").strip() or None


def wrap_business_tool_handler(tool_name: str, handler):
    """Wrap one registered Takyon tool handler so every call appends a tool_call cost event.

    Applied once at plugin registration (``plugins/takyon/__init__.register``) — the single
    Takyon-owned choke point every ``business_*`` tool call passes through, whatever surface
    invoked it (CEO turn, worker job, shell, dashboard). Records name, scope, duration, and
    outcome; argument VALUES are never persisted (only the key names), so no secrets/PII land
    in the ledger.
    """

    def wrapped(args, **kwargs):
        started = time.time()
        status = "ok"
        error_text = None
        result = None
        try:
            result = handler(args, **kwargs)
            return result
        except BaseException as exc:
            status = "error"
            error_text = str(exc)
            raise
        finally:
            try:
                if not _disabled():
                    ctx = operator_context()
                    payload: dict[str, Any] = {
                        "arg_keys": sorted(str(k) for k in args)[:40] if isinstance(args, Mapping) else [],
                    }
                    if status == "ok" and isinstance(result, str):
                        payload["result_chars"] = len(result)
                    record_operator_event_autoconn(
                        event_kind=KIND_TOOL_CALL,
                        business_slug=_tool_call_business_slug(args, ctx),
                        user_id=ctx["user_id"] or None,
                        job_id=ctx["run_id"] or None,
                        run_id=ctx["run_id"] or None,
                        task_kind=ctx["task_kind"] or None,
                        name=tool_name,
                        status=status,
                        duration_ms=int((time.time() - started) * 1000),
                        error=error_text,
                        payload=payload,
                    )
            except Exception as exc:  # noqa: BLE001
                logger.debug("tool cost event failed (%s): %s", tool_name, exc)

    return wrapped
