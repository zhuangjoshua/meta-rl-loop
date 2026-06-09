"""Postgres integration test for the worker plane (``plugins/takyon/jobs.py``, migration 0010) — the
at-least-once job queue and the ONE budget-gated execution contract every job runs under.

Proves the contract end-to-end on real Postgres (mediationplan.md > Worker Plane):
  * enqueue is idempotent on ``idempotency_key`` (a replay is one row, one effect);
  * ``claim_one`` prioritizes ``ceo_bootstrap`` ahead of ordinary queued work, is FIFO within a
    priority class, flips a job to 'running', and never hands the same row to two workers —
    including the real ``FOR UPDATE SKIP LOCKED`` proof: a row locked by another live transaction is
    skipped, then claimable once that transaction releases;
  * ``run_one`` reserves the estimate on the OWNER's flow-A billing account, runs the handler behind a
    seam, settles the TRUE cost and completes — the ledger actually moves;
  * an exhausted budget BLOCKS with a reason and runs nothing (invariant #8: never a fabricated
    completion), and nothing is held;
  * a handler error refunds the whole hold and fails/retries; retries are BOUNDED by max_attempts
    (an exhausted budget or a permanently-failing job stops, never loops), and every attempt's hold is
    released so no reservation leaks;
  * a crashed worker's 'running' job is recovered by ``requeue_stale`` (or blocked at max attempts),
    and the next attempt releases the crashed attempt's stale hold before reserving again.

The work itself is injected through the ``handlers`` seam (deterministic stubs here), exactly as the
AI gateway injects ``get_provider_caller``: the engine — claim, budget, lifecycle — is the real thing
on real Postgres; only the leaf side effect is stubbed. Skips unless psycopg is importable and
TAKYON_TEST_PG_DSN is set.
"""

from __future__ import annotations

import os
import threading
import time
import uuid

import pytest

psycopg = pytest.importorskip("psycopg")

from psycopg.conninfo import make_conninfo  # noqa: E402

from plugins.takyon import billing, jobs  # noqa: E402
from plugins.takyon.control_plane import provision_user_on_first_login  # noqa: E402


def _provision_business(conn, *, allowance_cents: int = 0) -> tuple[str, str]:
    """Provision a user + a business they own. Optionally grant the owner a flow-A allowance so
    ``billing.reserve`` can succeed (provisioning opens a ZERO-allowance account). Returns
    ``(business_slug, owner_user_id)``."""
    uid, _created, _raw = provision_user_on_first_login(conn, f"auth0|{uuid.uuid4().hex}")
    slug = f"biz-{uuid.uuid4().hex[:8]}"
    conn.execute(
        "insert into businesses (slug, name, owner_user_id) values (%s, %s, %s)",
        (slug, "Acme", uid),
    )
    if allowance_cents > 0:
        billing.grant_allowance(conn, uid, allowance_cents, f"grant:{uuid.uuid4().hex}")
    return slug, uid


class _RecordingHandler:
    """A job-handler stub: records every job it ran (so a test can assert the work ran exactly N
    times — or never) and returns a fixed result + true cost. Pass ``raises`` to exercise the
    handler-error path."""

    def __init__(self, *, cost_cents: int = 0, result=None, raises: Exception | None = None):
        self.cost_cents = cost_cents
        self.result = result if result is not None else {"ok": True}
        self.raises = raises
        self.calls: list[str] = []

    def __call__(self, job: jobs.Job) -> jobs.JobRunResult:
        self.calls.append(job.id)
        if self.raises is not None:
            raise self.raises
        return jobs.JobRunResult(result=self.result, actual_cost_cents=self.cost_cents)


# ── enqueue / claim ──────────────────────────────────────────────────────────────────────────────


def test_enqueue_is_idempotent(pg_conn):
    slug, _uid = _provision_business(pg_conn)
    j1 = jobs.enqueue(pg_conn, slug, "ceo_wake", idempotency_key="k1", payload={"a": 1})
    j2 = jobs.enqueue(pg_conn, slug, "ceo_wake", idempotency_key="k1", payload={"a": 2})
    # A replay returns the SAME row unchanged — one effect, and the original payload is untouched.
    assert j1.id == j2.id
    assert j2.payload == {"a": 1}
    assert len(jobs.list_jobs(pg_conn, slug)) == 1


def test_claim_one_is_fifo_and_never_double_claims(pg_conn):
    slug, _uid = _provision_business(pg_conn)
    a = jobs.enqueue(pg_conn, slug, "k", idempotency_key="a")
    b = jobs.enqueue(pg_conn, slug, "k", idempotency_key="b")
    first = jobs.claim_one(pg_conn, worker_id="w1")
    second = jobs.claim_one(pg_conn, worker_id="w2")
    # Both queued jobs were claimed, as distinct rows, oldest first; a 'running' job is never re-served.
    assert first is not None and second is not None
    assert {first.id, second.id} == {a.id, b.id}
    assert first.id == a.id
    assert first.status == "running" and first.attempts == 1 and first.locked_by == "w1"
    assert jobs.claim_one(pg_conn, worker_id="w3") is None


def test_claim_one_filters_by_kind(pg_conn):
    slug, _uid = _provision_business(pg_conn)
    jobs.enqueue(pg_conn, slug, "build", idempotency_key="bld")
    wake = jobs.enqueue(pg_conn, slug, "ceo_wake", idempotency_key="wk")
    # A worker restricted to ceo_wake skips the older build job and claims the wake.
    claimed = jobs.claim_one(pg_conn, worker_id="w1", kinds=["ceo_wake"])
    assert claimed is not None and claimed.id == wake.id


def test_claim_one_prioritizes_bootstrap_over_older_wake(pg_conn):
    first_slug, _uid = _provision_business(pg_conn)
    second_slug, _uid2 = _provision_business(pg_conn)
    wake = jobs.enqueue(pg_conn, first_slug, "ceo_wake", idempotency_key="wake")
    bootstrap = jobs.enqueue(pg_conn, second_slug, "ceo_bootstrap", idempotency_key="bootstrap")

    claimed = jobs.claim_one(pg_conn, worker_id="w1")

    assert claimed is not None
    assert claimed.id == bootstrap.id
    assert claimed.id != wake.id


def test_claim_one_serializes_jobs_per_business(pg_conn):
    slug, _uid = _provision_business(pg_conn)
    first = jobs.enqueue(pg_conn, slug, "ceo_wake", idempotency_key="a")
    second = jobs.enqueue(pg_conn, slug, "ceo_bootstrap", idempotency_key="b")
    claimed = jobs.claim_one(pg_conn, worker_id="w1")
    assert claimed is not None and claimed.id == second.id
    # A second queued job for the same business must wait until the running one finishes.
    assert jobs.claim_one(pg_conn, worker_id="w2") is None
    jobs.complete(pg_conn, claimed.id, result={"ok": True})
    next_job = jobs.claim_one(pg_conn, worker_id="w2")
    assert next_job is not None and next_job.id == first.id


def test_claim_one_serializes_per_lane_not_per_business(pg_conn):
    """The per-business gate is PER LANE: a running CEO turn must not starve a different-kind job
    for the same business (and a CEO turn that enqueues-and-waits on another kind must not deadlock
    behind its own business gate), while two jobs in the SAME lane still serialize."""
    slug, _uid = _provision_business(pg_conn)
    wake = jobs.enqueue(pg_conn, slug, "ceo_wake", idempotency_key="lane-wake")
    publish = jobs.enqueue(pg_conn, slug, "x.publish_outreach", idempotency_key="lane-pub-1")
    second_publish = jobs.enqueue(pg_conn, slug, "x.publish_outreach", idempotency_key="lane-pub-2")

    first = jobs.claim_one(pg_conn, worker_id="w1")
    assert first is not None and first.id == wake.id
    # Different lane (x.publish_outreach vs the running ceo wake): claimable concurrently.
    cross_lane = jobs.claim_one(pg_conn, worker_id="w2")
    assert cross_lane is not None and cross_lane.id == publish.id
    # Same lane as the running publish job: must wait.
    assert jobs.claim_one(pg_conn, worker_id="w3") is None
    jobs.complete(pg_conn, cross_lane.id, result={"ok": True})
    drained = jobs.claim_one(pg_conn, worker_id="w3")
    assert drained is not None and drained.id == second_publish.id


def test_claim_one_skips_rows_locked_by_another_worker(pg_conn):
    # The real FOR UPDATE SKIP LOCKED guarantee: a row another transaction already holds is skipped,
    # not blocked on, so two workers never contend for one job. A second live connection holds the
    # row's lock (autocommit off → the lock survives until we roll back).
    slug, _uid = _provision_business(pg_conn)
    job = jobs.enqueue(pg_conn, slug, "k", idempotency_key="only")
    url = make_conninfo(os.environ["TAKYON_TEST_PG_DSN"], dbname=pg_conn.info.dbname)
    other = psycopg.connect(url)
    try:
        locked = other.execute(
            "select id from jobs where status = 'queued' for update skip locked limit 1"
        ).fetchone()
        assert locked is not None and str(locked[0]) == job.id
        # The only queued row is locked elsewhere → our worker finds nothing rather than blocking.
        assert jobs.claim_one(pg_conn, worker_id="w1") is None
        other.rollback()  # release the lock
        # Now the same row is claimable.
        claimed = jobs.claim_one(pg_conn, worker_id="w1")
        assert claimed is not None and claimed.id == job.id
    finally:
        other.close()


# ── the budget-gated execution cycle ───────────────────────────────────────────────────────────────


def test_run_one_returns_none_on_empty_queue(pg_conn):
    assert jobs.run_one(pg_conn, worker_id="w1", handlers={}) is None


def test_run_one_reserves_settles_completes_and_moves_ledger(pg_conn):
    slug, uid = _provision_business(pg_conn, allowance_cents=100_000)
    jobs.enqueue(pg_conn, slug, "ceo_wake", idempotency_key="j", payload={"estimate_cents": 500})
    handler = _RecordingHandler(cost_cents=300, result={"did": "work"})

    outcome = jobs.run_one(pg_conn, worker_id="w1", handlers={"ceo_wake": handler})
    assert outcome is not None
    assert outcome.status == "completed"
    assert outcome.reserved_cents == 500  # the estimate was held
    assert outcome.actual_cents == 300  # settled at the TRUE cost
    assert len(handler.calls) == 1  # the work ran exactly once

    job = jobs.get_job(pg_conn, outcome.job_id)
    assert job.status == "completed"
    assert job.result == {"did": "work"}
    assert job.error is None
    assert job.locked_by is None and job.locked_at is None

    # The ledger truly moved: allowance spent == the true cost, and nothing is left reserved.
    bal = billing.get_billing_balances(pg_conn, uid)
    assert bal.allowance_used_cents == 300
    assert bal.reserved_cents == 0


def test_run_one_blocks_when_budget_exhausted_without_running(pg_conn):
    # Invariant #8: a reserve the buckets cannot cover BLOCKS with a reason and runs nothing — never a
    # fabricated completion, never a charge to no one.
    slug, uid = _provision_business(pg_conn)  # zero allowance, zero topup
    jobs.enqueue(pg_conn, slug, "ceo_wake", idempotency_key="j", payload={"estimate_cents": 500})
    handler = _RecordingHandler(cost_cents=300)

    outcome = jobs.run_one(pg_conn, worker_id="w1", handlers={"ceo_wake": handler})
    assert outcome.status == "blocked"
    assert outcome.reason == "budget_exhausted"
    assert handler.calls == []  # the work never ran

    job = jobs.get_job(pg_conn, outcome.job_id)
    assert job.status == "blocked"
    assert job.error["reason"] == "budget_exhausted"
    bal = billing.get_billing_balances(pg_conn, uid)
    assert bal.reserved_cents == 0  # nothing was held
    assert bal.allowance_used_cents == 0


def test_run_one_blocks_unknown_kind(pg_conn):
    # No handler for the kind ⇒ blocked('no_handler'); the gate is never even reached, so nothing is
    # reserved (an allowance is present precisely to prove it stays untouched).
    slug, uid = _provision_business(pg_conn, allowance_cents=100_000)
    jobs.enqueue(pg_conn, slug, "mystery", idempotency_key="j", payload={"estimate_cents": 500})

    outcome = jobs.run_one(pg_conn, worker_id="w1", handlers={"ceo_wake": _RecordingHandler()})
    assert outcome.status == "blocked"
    assert outcome.reason == "no_handler"

    job = jobs.get_job(pg_conn, outcome.job_id)
    assert job.status == "blocked"
    assert job.error == {"reason": "no_handler", "detail": {"kind": "mystery"}}
    assert billing.get_billing_balances(pg_conn, uid).reserved_cents == 0


def test_handler_error_refunds_hold_and_fails(pg_conn):
    # The work was attempted and raised: the hold is released and (no attempts left) the job fails.
    slug, uid = _provision_business(pg_conn, allowance_cents=100_000)
    jobs.enqueue(
        pg_conn, slug, "ceo_wake", idempotency_key="j",
        payload={"estimate_cents": 500}, max_attempts=1,
    )
    handler = _RecordingHandler(raises=RuntimeError("boom"))

    outcome = jobs.run_one(pg_conn, worker_id="w1", handlers={"ceo_wake": handler})
    assert outcome.status == "failed"  # attempts(1) == max(1): no retry
    assert len(handler.calls) == 1

    job = jobs.get_job(pg_conn, outcome.job_id)
    assert job.status == "failed"
    assert job.error["reason"] == "handler_error"
    assert "boom" in job.error["error"]

    # The failure released the hold — no allowance is permanently consumed by a failed call.
    bal = billing.get_billing_balances(pg_conn, uid)
    assert bal.reserved_cents == 0
    assert bal.allowance_used_cents == 0


def test_failing_job_retries_to_a_bound_then_fails_with_no_leak(pg_conn):
    # Retries are BOUNDED (mediationplan: exhausted budget/permanent failure stops, never loops), and
    # each attempt's hold is released, so a job that always fails leaks nothing.
    slug, uid = _provision_business(pg_conn, allowance_cents=100_000)
    jobs.enqueue(
        pg_conn, slug, "ceo_wake", idempotency_key="j",
        payload={"estimate_cents": 500}, max_attempts=2,
    )
    handler = _RecordingHandler(raises=RuntimeError("always"))

    o1 = jobs.run_one(pg_conn, worker_id="w1", handlers={"ceo_wake": handler})
    assert o1.status == "requeued"  # attempts(1) < max(2): back to the queue
    o2 = jobs.run_one(pg_conn, worker_id="w1", handlers={"ceo_wake": handler})
    assert o2.status == "failed"  # attempts(2) == max(2): bounded, terminal
    assert o1.job_id == o2.job_id
    assert len(handler.calls) == 2  # ran exactly twice, not forever

    # A failed job is terminal — never re-claimed.
    assert jobs.run_one(pg_conn, worker_id="w1", handlers={"ceo_wake": handler}) is None
    bal = billing.get_billing_balances(pg_conn, uid)
    assert bal.reserved_cents == 0
    assert bal.allowance_used_cents == 0


def test_zero_estimate_job_completes_without_touching_billing(pg_conn):
    # A job with no estimate (the common ceo_wake) skips the gate entirely: no owner resolve, no
    # reserve/settle — it just runs and completes.
    slug, uid = _provision_business(pg_conn)  # no allowance at all
    jobs.enqueue(pg_conn, slug, "ceo_wake", idempotency_key="j")  # payload {} ⇒ estimate 0
    handler = _RecordingHandler(result={"woke": True})

    outcome = jobs.run_one(pg_conn, worker_id="w1", handlers={"ceo_wake": handler})
    assert outcome.status == "completed"
    assert outcome.reserved_cents == 0 and outcome.actual_cents == 0
    assert jobs.get_job(pg_conn, outcome.job_id).result == {"woke": True}
    assert billing.get_billing_balances(pg_conn, uid).reserved_cents == 0


def test_run_one_heartbeats_while_handler_is_running(pg_conn, monkeypatch):
    slug, _uid = _provision_business(pg_conn)
    jobs.enqueue(pg_conn, slug, "ceo_wake", idempotency_key="j")
    release = threading.Event()
    heartbeat_calls: list[tuple[str, str]] = []
    original_heartbeat = jobs.heartbeat

    def _wrapped_heartbeat(conn, job_id: str, *, worker_id: str) -> None:
        heartbeat_calls.append((job_id, worker_id))
        original_heartbeat(conn, job_id, worker_id=worker_id)

    monkeypatch.setattr(jobs, "heartbeat", _wrapped_heartbeat)

    def _release_after_heartbeat() -> None:
        deadline = time.time() + 1.0
        while time.time() < deadline:
            if heartbeat_calls:
                release.set()
                return
            time.sleep(0.01)
        release.set()

    threading.Thread(target=_release_after_heartbeat, daemon=True).start()

    class _WaitingHandler:
        def __call__(self, job: jobs.Job) -> jobs.JobRunResult:
            release.wait(1.0)
            return jobs.JobRunResult(result={"ok": job.business_slug}, actual_cost_cents=0)

    outcome = jobs.run_one(
        pg_conn,
        worker_id="w1",
        handlers={"ceo_wake": _WaitingHandler()},
        heartbeat_interval_seconds=0.05,
    )
    assert outcome is not None and outcome.status == "completed"
    assert heartbeat_calls and heartbeat_calls[0][1] == "w1"


# ── crash recovery ─────────────────────────────────────────────────────────────────────────────────


def _go_stale(conn, job_id: str, *, seconds: int = 3600) -> None:
    """Backdate a 'running' job's lock so the reaper considers the worker dead."""
    conn.execute(
        "update jobs set locked_at = now() - make_interval(secs => %s) where id = %s",
        (seconds, job_id),
    )


def test_requeue_stale_returns_crashed_job_to_queue(pg_conn):
    slug, _uid = _provision_business(pg_conn)
    job = jobs.enqueue(pg_conn, slug, "ceo_wake", idempotency_key="j")
    claimed = jobs.claim_one(pg_conn, worker_id="dead-worker")
    assert claimed.status == "running"  # the worker took it, then "died"
    _go_stale(pg_conn, job.id)

    assert jobs.requeue_stale(pg_conn, older_than_seconds=900) == 1
    back = jobs.get_job(pg_conn, job.id)
    assert back.status == "queued"
    assert back.locked_by is None and back.locked_at is None
    # It is claimable again (this is the whole point — a crash must not strand the job).
    assert jobs.claim_one(pg_conn, worker_id="w2").id == job.id


def test_requeue_stale_blocks_job_out_of_attempts(pg_conn):
    # A job whose worker keeps dying is not retried forever: at max_attempts the reaper blocks it.
    slug, _uid = _provision_business(pg_conn)
    job = jobs.enqueue(pg_conn, slug, "ceo_wake", idempotency_key="j", max_attempts=1)
    jobs.claim_one(pg_conn, worker_id="dead")  # attempts -> 1 == max
    _go_stale(pg_conn, job.id)

    assert jobs.requeue_stale(pg_conn, older_than_seconds=900) == 1
    blocked = jobs.get_job(pg_conn, job.id)
    assert blocked.status == "blocked"
    assert blocked.error == {"reason": "stalled_max_attempts"}


def test_crashed_attempts_stale_hold_is_released_before_next_reserve(pg_conn):
    # The reservation-leak guard: a worker that reserves then crashes before settling leaves an
    # outstanding hold. The next attempt must release that stale hold before reserving again, so a
    # held-but-never-settled reservation can never accumulate across retries.
    slug, uid = _provision_business(pg_conn, allowance_cents=100_000)
    job = jobs.enqueue(
        pg_conn, slug, "ceo_wake", idempotency_key="j",
        payload={"estimate_cents": 500}, max_attempts=5,
    )
    # Attempt 1: claim, reserve, then "crash" before settling.
    jobs.claim_one(pg_conn, worker_id="dead")  # attempts -> 1
    stale_key = f"job:{job.id}:1"
    billing.reserve(pg_conn, uid, 500, stale_key, business_slug=slug, job_id=job.id)
    pg_conn.execute(
        "update jobs set reserved_billing_entry_id = %s, "
        "locked_at = now() - make_interval(secs => 3600) where id = %s",
        (stale_key, job.id),
    )
    assert billing.get_billing_balances(pg_conn, uid).reserved_cents == 500  # hold outstanding

    # Reaper returns it to the queue; the next run_one releases the stale hold, reserves fresh, runs.
    jobs.requeue_stale(pg_conn, older_than_seconds=900)
    handler = _RecordingHandler(cost_cents=200)
    outcome = jobs.run_one(pg_conn, worker_id="w2", handlers={"ceo_wake": handler})
    assert outcome.status == "completed"
    assert outcome.actual_cents == 200

    bal = billing.get_billing_balances(pg_conn, uid)
    assert bal.reserved_cents == 0  # stale hold released AND the new hold settled — no leak
    assert bal.allowance_used_cents == 200  # only the true cost remains charged
