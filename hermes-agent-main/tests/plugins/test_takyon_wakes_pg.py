"""Postgres integration test for scheduled CEO wakes (``plugins/takyon/wakes.py`` +
``dispatch_due_wakes()``, migration 0010) — recurring wakes as due-rows enqueued into the SAME jobs
queue, with the schedule living in ``wake_schedules`` (no file cron, no ticker, no ``.tick.lock``).

Proves the dispatch contract end-to-end on real Postgres:
  * upsert creates one schedule per business, due now by default; on update it PRESERVES the
    dispatcher's cursor (cadence/pause changes don't disturb the next fire) unless an explicit
    ``next_run_at`` is passed;
  * ``set_enabled`` pauses dispatch and resumes it WITHOUT resetting the cursor;
  * ``dispatch_due_wakes`` enqueues exactly one job per due schedule (keyed on the scheduled
    minute-window) and advances the cursor; a disabled schedule is skipped;
  * window idempotency — a replay in the same minute-window collapses to ONE job (the
    ``jobs.idempotency_key`` conflict), and a host that was down fires ONE bounded catch-up that
    realigns to now, never an N-deep backlog;
  * the enqueued wake then drains through the ordinary worker (``jobs.run_one`` filtered to
    ``kind='ceo_wake'``) — dispatch and drain are the same queue, end to end.

Skips unless psycopg is importable and TAKYON_TEST_PG_DSN is set.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest

psycopg = pytest.importorskip("psycopg")

from plugins.takyon import jobs, wakes  # noqa: E402
from plugins.takyon.control_plane import provision_user_on_first_login  # noqa: E402


def _business(conn) -> str:
    """Provision a user + a business they own; return the business slug (wake_schedules FKs to it)."""
    uid, _created, _raw = provision_user_on_first_login(conn, f"auth0|{uuid.uuid4().hex}")
    slug = f"biz-{uuid.uuid4().hex[:8]}"
    conn.execute(
        "insert into businesses (slug, name, owner_user_id) values (%s, %s, %s)",
        (slug, "Acme", uid),
    )
    return slug


def _completing_handler(result):
    """A ceo_wake handler stub that records its calls and completes with a fixed result."""

    calls: list[str] = []

    def _h(job: jobs.Job) -> jobs.JobRunResult:
        calls.append(job.id)
        return jobs.JobRunResult(result=result)

    _h.calls = calls  # type: ignore[attr-defined]
    return _h


# ── schedule CRUD ──────────────────────────────────────────────────────────────────────────────────


def test_upsert_creates_schedule_due_now_by_default(pg_conn):
    slug = _business(pg_conn)
    sched = wakes.upsert_wake_schedule(pg_conn, slug, interval_seconds=3600)
    assert sched.business_slug == slug
    assert sched.kind == "ceo_wake"
    assert sched.enabled is True
    assert sched.interval_seconds == 3600
    assert sched.payload == {}
    assert sched.next_run_at is not None
    # The documented default: next_run_at = now(), so the first wake is immediately due and fires.
    assert wakes.dispatch_due_wakes(pg_conn) == 1


def test_upsert_update_preserves_cursor_unless_explicit(pg_conn):
    slug = _business(pg_conn)
    created = wakes.upsert_wake_schedule(pg_conn, slug, interval_seconds=3600)
    cursor = created.next_run_at

    # Change cadence WITHOUT passing next_run_at: the dispatcher's cursor is preserved.
    updated = wakes.upsert_wake_schedule(pg_conn, slug, interval_seconds=60)
    assert updated.interval_seconds == 60
    assert updated.next_run_at == cursor

    # Passing an explicit next_run_at DOES move the cursor.
    target = datetime(2030, 1, 1, tzinfo=timezone.utc)
    moved = wakes.upsert_wake_schedule(pg_conn, slug, interval_seconds=60, next_run_at=target)
    assert moved.next_run_at == target


def test_upsert_rejects_nonpositive_interval(pg_conn):
    slug = _business(pg_conn)
    with pytest.raises(ValueError):
        wakes.upsert_wake_schedule(pg_conn, slug, interval_seconds=0)


def test_set_enabled_pauses_dispatch_and_preserves_cursor(pg_conn):
    slug = _business(pg_conn)
    created = wakes.upsert_wake_schedule(pg_conn, slug, interval_seconds=3600)

    wakes.set_enabled(pg_conn, slug, False)
    paused = wakes.get_wake_schedule(pg_conn, slug)
    assert paused.enabled is False
    assert paused.next_run_at == created.next_run_at  # pausing does not move the cursor
    # A paused, due schedule is skipped by dispatch — nothing enqueued.
    assert wakes.dispatch_due_wakes(pg_conn) == 0

    wakes.set_enabled(pg_conn, slug, True)
    resumed = wakes.get_wake_schedule(pg_conn, slug)
    assert resumed.next_run_at == created.next_run_at  # resuming does not reset it either
    assert wakes.dispatch_due_wakes(pg_conn) == 1


# ── dispatch ─────────────────────────────────────────────────────────────────────────────────────


def test_dispatch_enqueues_one_job_and_advances_cursor(pg_conn):
    slug = _business(pg_conn)
    past = datetime.now(timezone.utc) - timedelta(seconds=10)
    created = wakes.upsert_wake_schedule(pg_conn, slug, interval_seconds=3600, next_run_at=past)

    assert wakes.dispatch_due_wakes(pg_conn) == 1
    job_list = jobs.list_jobs(pg_conn, slug)
    assert len(job_list) == 1
    job = job_list[0]
    assert job.kind == "ceo_wake"
    assert job.status == "queued"
    # Keyed on the scheduled minute-window: wake:<slug>:<YYYYMMDDHH24MI>.
    assert job.idempotency_key.startswith(f"wake:{slug}:")
    minute = job.idempotency_key.rsplit(":", 1)[1]
    assert len(minute) == 12 and minute.isdigit()

    # The cursor advanced (fire recorded) and the schedule is no longer due.
    sched = wakes.get_wake_schedule(pg_conn, slug)
    assert sched.last_enqueued_at is not None
    assert sched.next_run_at > created.next_run_at
    assert wakes.dispatch_due_wakes(pg_conn) == 0


def test_dispatch_collapses_replay_in_same_window(pg_conn):
    # Window idempotency: re-firing the same scheduled minute collapses to ONE job via the
    # jobs.idempotency_key conflict (a replay or an overlapping tick never doubles the wake).
    slug = _business(pg_conn)
    past = datetime.now(timezone.utc) - timedelta(seconds=10)
    wakes.upsert_wake_schedule(pg_conn, slug, interval_seconds=3600, next_run_at=past)
    assert wakes.dispatch_due_wakes(pg_conn) == 1

    # Force the cursor back to the SAME instant (same minute-window) and re-dispatch.
    pg_conn.execute(
        "update wake_schedules set next_run_at = %s where business_slug = %s", (past, slug)
    )
    assert wakes.dispatch_due_wakes(pg_conn) == 0  # on-conflict-do-nothing ⇒ zero new jobs
    assert len(jobs.list_jobs(pg_conn, slug)) == 1  # still exactly one


def test_dispatch_does_one_catchup_after_outage_not_a_backlog(pg_conn):
    # Bounded catch-up: a host down for ~10 intervals fires ONE enqueue and realigns to now, never a
    # 10-deep backlog (the greatest(now, next_run_at) bound in dispatch_due_wakes).
    slug = _business(pg_conn)
    interval = 3600
    behind = datetime.now(timezone.utc) - timedelta(seconds=10 * interval)
    wakes.upsert_wake_schedule(pg_conn, slug, interval_seconds=interval, next_run_at=behind)

    assert wakes.dispatch_due_wakes(pg_conn) == 1  # ONE catch-up, not ten
    assert len(jobs.list_jobs(pg_conn, slug)) == 1

    # Realigned to ~now + interval (NOT behind + interval, which would still be in the past).
    sched = wakes.get_wake_schedule(pg_conn, slug)
    now = datetime.now(timezone.utc)
    assert sched.next_run_at > now
    assert sched.next_run_at < now + timedelta(seconds=interval + 60)
    assert wakes.dispatch_due_wakes(pg_conn) == 0  # not immediately due again


def test_dispatched_wake_is_claimable_and_drains(pg_conn):
    # End to end: the schedule enqueues into the same jobs queue, and the ordinary worker drains it,
    # filtered to the wake kind. Schedule → dispatch → claim → run → complete, one path.
    slug = _business(pg_conn)
    past = datetime.now(timezone.utc) - timedelta(seconds=10)
    wakes.upsert_wake_schedule(pg_conn, slug, interval_seconds=3600, next_run_at=past)
    assert wakes.dispatch_due_wakes(pg_conn) == 1

    handler = _completing_handler({"woke": True})
    outcome = jobs.run_one(
        pg_conn, worker_id="w1", handlers={"ceo_wake": handler}, kinds=["ceo_wake"]
    )
    assert outcome is not None
    assert outcome.kind == "ceo_wake"
    assert outcome.status == "completed"
    assert len(handler.calls) == 1

    job = jobs.get_job(pg_conn, outcome.job_id)
    assert job.status == "completed"
    assert job.result == {"woke": True}


def test_dispatch_fires_each_independent_window(pg_conn):
    # Across genuinely different minute-windows the same business wakes again (the key changes with
    # the scheduled minute), so dispatch is idempotent-per-window, not once-ever.
    slug = _business(pg_conn)
    first = datetime.now(timezone.utc) - timedelta(minutes=5)
    wakes.upsert_wake_schedule(pg_conn, slug, interval_seconds=60, next_run_at=first)
    assert wakes.dispatch_due_wakes(pg_conn) == 1

    # Put the cursor in a DIFFERENT, still-past minute-window; it fires a second, distinct job.
    second = datetime.now(timezone.utc) - timedelta(minutes=2)
    pg_conn.execute(
        "update wake_schedules set next_run_at = %s where business_slug = %s", (second, slug)
    )
    assert wakes.dispatch_due_wakes(pg_conn) == 1
    keys = {j.idempotency_key for j in jobs.list_jobs(pg_conn, slug)}
    assert len(keys) == 2  # two distinct windows ⇒ two distinct wake jobs
