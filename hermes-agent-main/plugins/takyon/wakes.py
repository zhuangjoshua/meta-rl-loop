"""Scheduled CEO wakes, Postgres-native (``wake_schedules`` table + ``dispatch_due_wakes()``,
migration 0010).

A wake is NOT a separate mechanism: it is a due row enqueued into the SAME ``jobs`` queue the worker
already drains. This leaf owns the two non-queue pieces:

  * the **schedule** — one ``wake_schedules`` row per business (CRUD here). The table is the truth;
    nothing watches ``.takyon/cron/jobs.json`` or a ``.tick.lock``.
  * the **dispatch** — :func:`dispatch_due_wakes` calls the in-DB ``dispatch_due_wakes()`` function,
    which enqueues one job per due schedule (exactly-once on the fired minute-window) and advances
    ``next_run_at`` with a bounded catch-up — atomically, in the source of truth. The Python side is a
    thin caller so the enqueue+advance can never be split by a crash between two statements.

The third piece, **drain**, is not here: the worker pulls ``kind='ceo_wake'`` jobs through
``jobs.run_one`` like any other job. This leaf only schedules and dispatches.

Who calls :func:`dispatch_due_wakes`? Either Supabase ``pg_cron`` (an in-DB scheduled call, no external
process to keep alive) or a single ``CRON_SECRET``-gated endpoint hit on an interval — both run the
identical SQL. Pure leaf: the caller owns the connection; each mutation opens its own transaction.

This coexists with the legacy file cron (``cron/scheduler.py``); it does not touch or retire it. That
retirement rides with the SQLite path in Phase 8.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any

_COLS = (
    "business_slug, kind, enabled, interval_seconds, next_run_at, "
    "last_enqueued_at, payload, created_at, updated_at"
)


@dataclass(frozen=True)
class WakeSchedule:
    business_slug: str
    kind: str
    enabled: bool
    interval_seconds: int
    next_run_at: Any
    last_enqueued_at: Any
    payload: dict[str, Any]
    created_at: Any
    updated_at: Any


def _row_to_schedule(row: tuple) -> WakeSchedule:
    return WakeSchedule(
        business_slug=row[0],
        kind=row[1],
        enabled=bool(row[2]),
        interval_seconds=int(row[3]),
        next_run_at=row[4],
        last_enqueued_at=row[5],
        payload=row[6] or {},
        created_at=row[7],
        updated_at=row[8],
    )


def upsert_wake_schedule(
    conn,
    business_slug: str,
    *,
    interval_seconds: int,
    next_run_at: datetime | None = None,
    kind: str = "ceo_wake",
    enabled: bool = True,
    payload: dict[str, Any] | None = None,
) -> WakeSchedule:
    """Create or update a business's wake schedule (one row per business, PK'd on business_slug).

    ``next_run_at`` semantics are deliberate: on CREATE it defaults to ``now()`` (the first dispatch
    fires immediately); on UPDATE, passing ``None`` PRESERVES the dispatcher's current cursor — you can
    change the cadence or pause/resume without disturbing when the next wake fires. Pass an explicit
    timestamp only to deliberately move the next fire."""
    if interval_seconds <= 0:
        raise ValueError("interval_seconds must be > 0")
    params = {
        "slug": business_slug,
        "kind": kind,
        "enabled": enabled,
        "interval": interval_seconds,
        # Same bound param is read in both clauses: INSERT coalesces it to now(); the conflict UPDATE
        # coalesces it to the existing next_run_at — so None means "don't move the cursor".
        "next": next_run_at,
        "payload": json.dumps(payload or {}),
    }
    with conn.transaction():
        row = conn.execute(
            "insert into wake_schedules "
            "(business_slug, kind, enabled, interval_seconds, next_run_at, payload) "
            "values (%(slug)s, %(kind)s, %(enabled)s, %(interval)s, "
            "        coalesce(%(next)s::timestamptz, now()), %(payload)s::jsonb) "
            "on conflict (business_slug) do update set "
            "  kind = excluded.kind, "
            "  enabled = excluded.enabled, "
            "  interval_seconds = excluded.interval_seconds, "
            "  payload = excluded.payload, "
            "  next_run_at = coalesce(%(next)s::timestamptz, wake_schedules.next_run_at), "
            "  updated_at = now() "
            f"returning {_COLS}",
            params,
        ).fetchone()
    return _row_to_schedule(row)


def get_wake_schedule(conn, business_slug: str) -> WakeSchedule | None:
    row = conn.execute(
        f"select {_COLS} from wake_schedules where business_slug = %s", (business_slug,)
    ).fetchone()
    return _row_to_schedule(row) if row else None


def list_wake_schedules(conn, *, enabled_only: bool = False) -> list[WakeSchedule]:
    sql = f"select {_COLS} from wake_schedules"
    if enabled_only:
        sql += " where enabled"
    sql += " order by business_slug"
    return [_row_to_schedule(r) for r in conn.execute(sql).fetchall()]


def set_enabled(conn, business_slug: str, enabled: bool) -> None:
    """Pause (False) or resume (True) a schedule without deleting it. A paused schedule is skipped by
    dispatch; resuming does NOT reset next_run_at (the cursor is preserved)."""
    with conn.transaction():
        conn.execute(
            "update wake_schedules set enabled = %s, updated_at = now() where business_slug = %s",
            (enabled, business_slug),
        )


def dispatch_due_wakes(conn) -> int:
    """Run one dispatch tick: enqueue a job for every due, enabled schedule and advance each cursor —
    atomically, inside the in-DB ``dispatch_due_wakes()`` function. Returns the number of jobs newly
    enqueued (0 when nothing is due). Idempotent within a minute-window: a replay or an overlapping
    tick collapses to one job per business per window, and a host that was down fires ONE catch-up
    enqueue rather than a backlog. This is exactly what pg_cron — or a CRON_SECRET endpoint — calls."""
    row = conn.execute("select dispatch_due_wakes()").fetchone()
    return int(row[0]) if row else 0
