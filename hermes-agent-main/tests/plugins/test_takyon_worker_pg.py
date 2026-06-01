"""Tests for the worker-drain plane (``plugins/takyon/worker.py``) — the process that ties the
Phase-6 queue (jobs.py) and schedule (wakes.py) together: self-dispatch due wakes, reclaim stale
claims, drain through the budget-gated ``run_one`` cycle, and route each kind to its handler.

Two layers:
  * PG (need TAKYON_TEST_PG_DSN): ``drain_tick`` on real Postgres with a STUB handler injected — the
    engine (dispatch, claim, reserve, settle, lifecycle, counts) is the real thing; only the leaf CEO
    turn is stubbed, exactly as jobs_pg stubs the work seam. Proves a due wake is enqueued-then-drained
    in one tick, the cursor advances so a second tick is a no-op, true cost settles the ledger, an
    exhausted budget blocks without running, and --no-dispatch drains without enqueuing.
  * Unit (need only psycopg importable): the ``ceo_wake`` handler converts the turn's true USD cost to
    cents for settlement and plumbs payload knobs through the run seam; and starting the loop with no
    DATABASE_URL raises loudly (invariant #8 — never a silent half-start).
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

psycopg = pytest.importorskip("psycopg")

from plugins.takyon import billing, core, jobs, wakes, worker  # noqa: E402
from plugins.takyon.control_plane import provision_user_on_first_login  # noqa: E402
from plugins.takyon.runtime_app import RuntimeNotConfigured  # noqa: E402
from plugins.takyon import storage  # noqa: E402
from gateway.session_context import get_session_env  # noqa: E402


def _provision_business(conn, *, allowance_cents: int = 0) -> tuple[str, str]:
    """Provision a user + a business they own, optionally granting the owner a flow-A allowance so
    ``billing.reserve`` can succeed. Returns ``(business_slug, owner_user_id)``."""
    uid, _created, _raw = provision_user_on_first_login(conn, f"auth0|{uuid.uuid4().hex}")
    slug = f"biz-{uuid.uuid4().hex[:8]}"
    conn.execute(
        "insert into businesses (slug, name, owner_user_id) values (%s, %s, %s)",
        (slug, "Acme", uid),
    )
    if allowance_cents > 0:
        billing.grant_allowance(conn, uid, allowance_cents, f"grant:{uuid.uuid4().hex}")
    return slug, uid


class _StubWakeHandler:
    """Stands in for the real (model-calling) ceo_wake handler: records every job it ran and returns a
    fixed result + true cost, so a tick test asserts the work ran without touching a provider."""

    def __init__(self, *, cost_cents: int = 0, raises: Exception | None = None):
        self.cost_cents = cost_cents
        self.raises = raises
        self.calls: list[str] = []

    def __call__(self, job: jobs.Job) -> jobs.JobRunResult:
        self.calls.append(job.id)
        if self.raises is not None:
            raise self.raises
        return jobs.JobRunResult(result={"ran": job.business_slug}, actual_cost_cents=self.cost_cents)


def _due_now() -> datetime:
    """A timestamp safely in the past so the schedule is immediately due on the next dispatch."""
    return datetime.now(timezone.utc) - timedelta(minutes=5)


# ── PG: drain_tick end-to-end ──────────────────────────────────────────────────────────────────────


def test_drain_tick_dispatches_due_wake_then_drains_it(pg_conn):
    # The headline path: one tick turns a due schedule into a queued job AND drains it to completion.
    slug, _uid = _provision_business(pg_conn)
    wakes.upsert_wake_schedule(pg_conn, slug, interval_seconds=3600, next_run_at=_due_now())
    handler = _StubWakeHandler(cost_cents=0)

    counts = worker.drain_tick(pg_conn, worker_id="w1", handlers={"ceo_wake": handler})
    assert counts["dispatched"] == 1
    assert counts["drained"] == 1
    assert counts["completed"] == 1
    assert len(handler.calls) == 1

    enqueued = jobs.list_jobs(pg_conn, slug)
    assert len(enqueued) == 1
    assert enqueued[0].kind == "ceo_wake"
    assert enqueued[0].status == "completed"


def test_second_tick_is_noop_after_cursor_advances(pg_conn):
    # The drain loop must not re-fire the same wake every tick: dispatch advances next_run_at past
    # now(), so the immediate next tick finds nothing due and an empty queue.
    slug, _uid = _provision_business(pg_conn)
    wakes.upsert_wake_schedule(pg_conn, slug, interval_seconds=3600, next_run_at=_due_now())
    handler = _StubWakeHandler()

    first = worker.drain_tick(pg_conn, worker_id="w1", handlers={"ceo_wake": handler})
    assert first["dispatched"] == 1 and first["drained"] == 1

    second = worker.drain_tick(pg_conn, worker_id="w1", handlers={"ceo_wake": handler})
    assert second["dispatched"] == 0
    assert second["drained"] == 0
    assert len(handler.calls) == 1  # the wake ran exactly once across both ticks


def test_drain_tick_settles_true_cost_through_run_one(pg_conn):
    # Money flows the real flow-A path: the schedule payload carries estimate_cents, run_one reserves
    # it on the owner, the handler reports the TRUE cost, and the ledger settles to that true cost.
    slug, uid = _provision_business(pg_conn, allowance_cents=100_000)
    wakes.upsert_wake_schedule(
        pg_conn, slug, interval_seconds=3600, next_run_at=_due_now(),
        payload={"estimate_cents": 500},
    )
    handler = _StubWakeHandler(cost_cents=300)

    counts = worker.drain_tick(pg_conn, worker_id="w1", handlers={"ceo_wake": handler})
    assert counts["completed"] == 1

    bal = billing.get_billing_balances(pg_conn, uid)
    assert bal.allowance_used_cents == 300  # settled at true cost, not the 500 estimate
    assert bal.reserved_cents == 0  # the remainder of the hold was released


def test_drain_tick_counts_blocked_on_exhausted_budget(pg_conn):
    # Invariant #8 surfaced through the tick: a wake whose estimate the owner cannot cover is BLOCKED,
    # the handler never runs, and the tick counts it as blocked (not completed).
    slug, _uid = _provision_business(pg_conn)  # zero allowance
    wakes.upsert_wake_schedule(
        pg_conn, slug, interval_seconds=3600, next_run_at=_due_now(),
        payload={"estimate_cents": 500},
    )
    handler = _StubWakeHandler(cost_cents=300)

    counts = worker.drain_tick(pg_conn, worker_id="w1", handlers={"ceo_wake": handler})
    assert counts["blocked"] == 1
    assert counts["completed"] == 0
    assert handler.calls == []  # the work never ran


def test_drain_tick_no_dispatch_drains_without_enqueuing(pg_conn):
    # --no-dispatch: drain the queue but do NOT enqueue due wakes (for when pg_cron owns dispatch).
    slug, _uid = _provision_business(pg_conn)
    wakes.upsert_wake_schedule(pg_conn, slug, interval_seconds=3600, next_run_at=_due_now())
    jobs.enqueue(pg_conn, slug, "ceo_wake", idempotency_key="pre", payload={})
    handler = _StubWakeHandler()

    counts = worker.drain_tick(pg_conn, worker_id="w1", handlers={"ceo_wake": handler}, dispatch=False)
    assert counts["dispatched"] == 0
    assert counts["drained"] == 1  # only the pre-enqueued job
    # The due schedule produced no job, because dispatch was off.
    assert wakes.get_wake_schedule(pg_conn, slug).last_enqueued_at is None


def test_drain_tick_empty_queue_is_noop(pg_conn):
    counts = worker.drain_tick(pg_conn, worker_id="w1", handlers={}, dispatch=False)
    assert counts == {
        "dispatched": 0, "requeued": 0, "drained": 0, "completed": 0, "blocked": 0, "failed": 0,
    }


def test_drain_tick_uses_real_registry_for_ceo_wake(pg_conn, monkeypatch):
    # With no explicit handlers, the tick consults worker.HANDLERS — proving ceo_wake is wired. We
    # stub the model turn at the run seam so no provider is called.
    slug, _uid = _provision_business(pg_conn)
    wakes.upsert_wake_schedule(pg_conn, slug, interval_seconds=3600, next_run_at=_due_now())
    seen: dict[str, str] = {}

    def _fake_turn(*, slug, **_kw):  # noqa: A002 - mirror the real kw name
        seen["slug"] = slug
        return "done", 0.0, "exact"

    monkeypatch.setattr(worker, "_run_ceo_turn", _fake_turn)
    counts = worker.drain_tick(pg_conn, worker_id="w1")  # handlers defaults to HANDLERS
    assert counts["completed"] == 1
    assert seen["slug"] == slug


# ── unit: handler cost conversion + invariant #8 ─────────────────────────────────────────────────────


def test_handlers_registry_maps_ceo_wake():
    assert worker.HANDLERS["ceo_wake"] is worker.ceo_wake_handler


def test_handlers_registry_maps_ceo_bootstrap():
    assert worker.HANDLERS["ceo_bootstrap"] is worker.ceo_bootstrap_handler


def test_ceo_wake_handler_reports_true_cost_in_cents(monkeypatch):
    # The handler converts the turn's true USD cost to integer cents for settlement and packages the
    # response. $0.0734 → 7 cents.
    captured: dict = {}

    def _fake_turn(*, slug, system_prompt, user_prompt, toolsets, max_turns, inactivity_limit):
        captured.update(slug=slug, toolsets=toolsets, max_turns=max_turns)
        return "the CEO did things", 0.0734, "exact"

    monkeypatch.setattr(worker, "_business_owner_user_id", lambda _slug: "user-123")
    monkeypatch.setattr(worker, "_run_ceo_turn", _fake_turn)
    job = SimpleNamespace(business_slug="acme", payload={})
    result = worker.ceo_wake_handler(job)

    assert result.actual_cost_cents == 7
    assert result.result["business_slug"] == "acme"
    assert result.result["final_response"] == "the CEO did things"
    assert result.result["cost_status"] == "exact"
    # The handler sourced the canonical wake toolsets (not an invented list).
    assert captured["toolsets"] == ["takyon", "web", "skills", "todo"]
    assert captured["max_turns"] == worker._DEFAULT_MAX_TURNS


def test_ceo_wake_handler_honors_payload_max_turns(monkeypatch):
    captured: dict = {}

    def _fake_turn(*, max_turns, **_kw):
        captured["max_turns"] = max_turns
        return "", 0.0, "none"

    monkeypatch.setattr(worker, "_business_owner_user_id", lambda _slug: "user-123")
    monkeypatch.setattr(worker, "_run_ceo_turn", _fake_turn)
    worker.ceo_wake_handler(SimpleNamespace(business_slug="acme", payload={"max_turns": 7}))
    assert captured["max_turns"] == 7


def test_ceo_wake_handler_runs_in_isolated_workspace(monkeypatch, tmp_path):
    backend = storage.LocalStorageBackend(tmp_path / "bucket")
    seed = tmp_path / "seed"
    (seed / "research").mkdir(parents=True, exist_ok=True)
    (seed / "research" / "strategy.md").write_text("seed\n")
    storage.sync_up(backend, "acme", seed)

    monkeypatch.setenv("TAKYON_STORAGE_BACKEND", "local")
    monkeypatch.setenv("TAKYON_STORAGE_LOCAL_DIR", str(tmp_path / "bucket"))
    monkeypatch.setattr(worker, "_business_owner_user_id", lambda _slug: "user-123")

    seen: dict[str, str] = {}

    def _fake_turn(*, slug, **_kw):
        workspace_root = get_session_env("TAKYON_SESSION_WORKSPACE_ROOT")
        seen["workspace_root"] = workspace_root
        seen["user_id"] = get_session_env("TAKYON_SESSION_USER_ID")
        workspace = Path(workspace_root) / "businesses" / slug
        assert (workspace / "research" / "strategy.md").read_text() == "seed\n"
        (workspace / "metrics").mkdir(parents=True, exist_ok=True)
        (workspace / "metrics" / "summary.md").write_text("fresh\n")
        return "ok", 0.0, "none"

    monkeypatch.setattr(worker, "_run_ceo_turn", _fake_turn)
    result = worker.ceo_wake_handler(SimpleNamespace(business_slug="acme", payload={}))

    assert result.result["business_slug"] == "acme"
    assert seen["user_id"] == "user-123"
    assert seen["workspace_root"]
    assert not Path(seen["workspace_root"]).exists()

    resumed = tmp_path / "resumed"
    storage.sync_down(backend, "acme", resumed)
    assert (resumed / "metrics" / "summary.md").read_text() == "fresh\n"


def test_zero_cost_turn_reports_zero_cents(monkeypatch):
    monkeypatch.setattr(worker, "_business_owner_user_id", lambda _slug: "user-123")
    monkeypatch.setattr(worker, "_run_ceo_turn", lambda **_kw: ("", 0.0, "none"))
    result = worker.ceo_wake_handler(SimpleNamespace(business_slug="acme", payload={}))
    assert result.actual_cost_cents == 0


def test_run_worker_loop_blocks_without_database_url(monkeypatch):
    # Invariant #8: no DATABASE_URL → loud RuntimeNotConfigured before any loop/connection, never a
    # silent half-start. The loop legitimately calls load_takyon_env() first (that is how it reads
    # DATABASE_URL from $TAKYON_HOME/.env in production), so we neutralize that env-file load here —
    # otherwise a configured dev machine's on-disk .env would repopulate DATABASE_URL and mask the
    # invariant. With the load no-op'd and the env vars cleared, the resolve seam must raise.
    monkeypatch.setattr(core, "load_takyon_env", lambda *a, **k: None)
    for name in ("DATABASE_URL", "POSTGRES_URL", "POSTGRES_PRISMA_URL"):
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(RuntimeNotConfigured):
        worker.run_worker_loop(database_url=None, once=True)
