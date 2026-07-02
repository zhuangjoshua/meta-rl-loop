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
    # Two businesses so the per-business-per-lane gate stays out of the way: this test pins FIFO
    # ordering across the queue and that a claimed ('running') row is never re-served. Same-business
    # serialization has its own tests below.
    first_slug, _uid = _provision_business(pg_conn)
    second_slug, _uid2 = _provision_business(pg_conn)
    a = jobs.enqueue(pg_conn, first_slug, "k", idempotency_key="a")
    b = jobs.enqueue(pg_conn, second_slug, "k", idempotency_key="b")
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


def test_claim_one_filters_by_owner_user_id(pg_conn):
    first_slug, _first_uid = _provision_business(pg_conn)
    second_slug, second_uid = _provision_business(pg_conn)
    jobs.enqueue(pg_conn, first_slug, "ceo_wake", idempotency_key="first")
    second = jobs.enqueue(pg_conn, second_slug, "ceo_wake", idempotency_key="second")

    claimed = jobs.claim_one(pg_conn, worker_id="w1", owner_user_id=second_uid)

    assert claimed is not None
    assert claimed.id == second.id
    assert jobs.claim_one(pg_conn, worker_id="w2", owner_user_id=second_uid) is None


def test_claim_one_prefers_matching_worker_prefix_during_grace_window(pg_conn):
    first_slug, _uid = _provision_business(pg_conn)
    second_slug, _uid2 = _provision_business(pg_conn)
    jobs.enqueue(pg_conn, first_slug, "ceo_bootstrap", idempotency_key="older-bootstrap")
    preferred = jobs.enqueue(
        pg_conn,
        second_slug,
        "ceo_bootstrap",
        idempotency_key="preferred-bootstrap",
        payload={
            "preferred_worker_id_prefix": "mac-operator-Local-",
            "preferred_worker_claim_seconds": 120,
        },
    )

    claimed = jobs.claim_one(pg_conn, worker_id="mac-operator-Local-123")

    assert claimed is not None
    assert claimed.id == preferred.id
    assert claimed.locked_by == "mac-operator-Local-123"


def test_claim_one_skips_nonmatching_worker_prefix_until_grace_window_expires(pg_conn):
    first_slug, _uid = _provision_business(pg_conn)
    second_slug, _uid2 = _provision_business(pg_conn)
    fallback = jobs.enqueue(pg_conn, first_slug, "ceo_bootstrap", idempotency_key="fallback-bootstrap")
    preferred = jobs.enqueue(
        pg_conn,
        second_slug,
        "ceo_bootstrap",
        idempotency_key="preferred-bootstrap",
        payload={
            "preferred_worker_id_prefix": "mac-operator-Local-",
            "preferred_worker_claim_seconds": 120,
        },
    )

    claimed = jobs.claim_one(pg_conn, worker_id="mac-operator-Other-123")

    assert claimed is not None
    assert claimed.id == fallback.id
    assert jobs.get_job(pg_conn, preferred.id).status == "queued"


def test_claim_one_allows_nonmatching_worker_prefix_after_grace_window_expires(pg_conn):
    slug, _uid = _provision_business(pg_conn)
    preferred = jobs.enqueue(
        pg_conn,
        slug,
        "ceo_bootstrap",
        idempotency_key="preferred-bootstrap",
        payload={
            "preferred_worker_id_prefix": "mac-operator-Local-",
            "preferred_worker_claim_seconds": 120,
        },
    )
    pg_conn.execute(
        "update jobs set created_at = now() - interval '5 minutes', "
        "updated_at = now() - interval '5 minutes' where id = %s",
        (preferred.id,),
    )

    claimed = jobs.claim_one(pg_conn, worker_id="mac-operator-Other-123")

    assert claimed is not None
    assert claimed.id == preferred.id
    assert claimed.locked_by == "mac-operator-Other-123"


def test_claim_one_renews_preferred_worker_window_after_requeue(pg_conn):
    slug, _uid = _provision_business(pg_conn)
    preferred = jobs.enqueue(
        pg_conn,
        slug,
        "ceo_bootstrap",
        idempotency_key="preferred-bootstrap",
        payload={
            "preferred_worker_id_prefix": "mac-operator-Local-",
            "preferred_worker_claim_seconds": 120,
        },
    )
    pg_conn.execute(
        "update jobs set created_at = now() - interval '10 minutes', updated_at = now() where id = %s",
        (preferred.id,),
    )

    assert jobs.claim_one(pg_conn, worker_id="mac-operator-Other-123") is None

    claimed = jobs.claim_one(pg_conn, worker_id="mac-operator-Local-456")
    assert claimed is not None
    assert claimed.id == preferred.id
    assert claimed.locked_by == "mac-operator-Local-456"


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
    slug, uid = _provision_business(pg_conn)  # zero allowance
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


def test_bootstrap_timeout_requeues_and_releases_running_claim(pg_conn):
    # Regression for moveoutpacket0701: a ceo_bootstrap inactivity timeout must not leave the durable
    # job wedged in 'running'. It should release its claim, refund the hold, and return to the queue
    # when attempts remain.
    slug, uid = _provision_business(pg_conn, allowance_cents=100_000)
    jobs.enqueue(
        pg_conn, slug, "ceo_bootstrap", idempotency_key="j",
        payload={"estimate_cents": 500}, max_attempts=2,
    )
    handler = _RecordingHandler(raises=TimeoutError("CEO wake for business wedge idle past 600s inactivity limit"))

    outcome = jobs.run_one(pg_conn, worker_id="w1", handlers={"ceo_bootstrap": handler})

    assert outcome.status == "requeued"
    assert outcome.reason == "handler_error"
    assert len(handler.calls) == 1

    job = jobs.get_job(pg_conn, outcome.job_id)
    assert job.status == "queued"
    assert job.locked_by is None
    assert job.locked_at is None
    assert job.error["reason"] == "handler_error"
    assert "idle past 600s inactivity limit" in job.error["error"]
    assert jobs.claim_one(pg_conn, worker_id="w2").id == job.id

    bal = billing.get_billing_balances(pg_conn, uid)
    assert bal.reserved_cents == 0
    assert bal.allowance_used_cents == 0


def test_handler_error_still_terminalizes_job_when_first_refund_raises(pg_conn, monkeypatch):
    slug, uid = _provision_business(pg_conn, allowance_cents=100_000)
    jobs.enqueue(
        pg_conn, slug, "ceo_wake", idempotency_key="j",
        payload={"estimate_cents": 500}, max_attempts=1,
    )
    handler = _RecordingHandler(raises=RuntimeError("boom"))
    refund_calls: list[str] = []
    original_refund = billing.refund

    def _flaky_refund(conn, reservation_key: str) -> None:
        refund_calls.append(reservation_key)
        if len(refund_calls) == 1:
            raise RuntimeError("safebox timeout")
        original_refund(conn, reservation_key)

    monkeypatch.setattr(billing, "refund", _flaky_refund)

    outcome = jobs.run_one(pg_conn, worker_id="w1", handlers={"ceo_wake": handler})

    assert outcome.status == "failed"
    assert len(handler.calls) == 1
    assert len(refund_calls) == 2

    job = jobs.get_job(pg_conn, outcome.job_id)
    assert job.status == "failed"
    assert job.error["reason"] == "handler_error"
    assert "boom" in job.error["error"]

    bal = billing.get_billing_balances(pg_conn, uid)
    assert bal.reserved_cents == 0
    assert bal.allowance_used_cents == 0


def test_handler_error_lost_claim_does_not_wedge_worker(pg_conn, monkeypatch):
    # A stale/lost claim can make the terminal fail() transition raise JobNotRunning after the
    # handler already raised. That must not escape the tick and wedge the daemon; the worker should
    # keep draining later jobs.
    slug, uid = _provision_business(pg_conn, allowance_cents=100_000)
    jobs.enqueue(
        pg_conn, slug, "ceo_wake", idempotency_key="j",
        payload={"estimate_cents": 500}, max_attempts=2,
    )
    handler = _RecordingHandler(raises=RuntimeError("workspace missing"))

    def _lost_claim_fail(conn, job_id: str, *, error: str, retryable: bool = True) -> str:
        raise jobs.JobNotRunning(job_id)

    monkeypatch.setattr(jobs, "fail", _lost_claim_fail)

    outcome = jobs.run_one(pg_conn, worker_id="w1", handlers={"ceo_wake": handler})

    assert outcome is not None
    assert outcome.status == "queued"
    assert outcome.reason == "handler_error"
    assert len(handler.calls) == 1
    assert jobs.get_job(pg_conn, outcome.job_id).status == "queued"
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


def test_run_one_uses_isolated_heartbeat_connection(pg_conn, monkeypatch):
    slug, _uid = _provision_business(pg_conn)
    jobs.enqueue(pg_conn, slug, "ceo_wake", idempotency_key="j")
    release = threading.Event()
    heartbeat_calls: list[str] = []
    closed: list[bool] = []

    class _HeartbeatConn:
        def __init__(self, inner):
            self._inner = inner

        def execute(self, *args, **kwargs):
            return self._inner.execute(*args, **kwargs)

        def transaction(self, *args, **kwargs):
            return self._inner.transaction(*args, **kwargs)

        def close(self) -> None:
            closed.append(True)

    def _factory():
        return _HeartbeatConn(pg_conn)

    original_heartbeat = jobs.heartbeat

    def _wrapped_heartbeat(conn, job_id: str, *, worker_id: str) -> None:
        assert isinstance(conn, _HeartbeatConn)
        heartbeat_calls.append(job_id)
        release.set()
        original_heartbeat(conn, job_id, worker_id=worker_id)

    monkeypatch.setattr(jobs, "heartbeat", _wrapped_heartbeat)

    class _WaitingHandler:
        def __call__(self, job: jobs.Job) -> jobs.JobRunResult:
            release.wait(1.0)
            return jobs.JobRunResult(result={"ok": job.business_slug}, actual_cost_cents=0)

    outcome = jobs.run_one(
        pg_conn,
        worker_id="w1",
        handlers={"ceo_wake": _WaitingHandler()},
        heartbeat_interval_seconds=0.05,
        heartbeat_conn_factory=_factory,
    )

    assert outcome is not None and outcome.status == "completed"
    assert heartbeat_calls
    assert closed


def test_run_one_uses_isolated_lifecycle_connection_for_completion(pg_conn, monkeypatch):
    slug, _uid = _provision_business(pg_conn)
    jobs.enqueue(pg_conn, slug, "ceo_wake", idempotency_key="j")
    closed: list[bool] = []
    completion_conn_used: list[bool] = []

    class _LifecycleConn:
        def __init__(self, inner):
            self._inner = inner

        def execute(self, *args, **kwargs):
            return self._inner.execute(*args, **kwargs)

        def transaction(self, *args, **kwargs):
            return self._inner.transaction(*args, **kwargs)

        def close(self) -> None:
            closed.append(True)

    def _factory():
        return _LifecycleConn(pg_conn)

    original_complete = jobs.complete

    def _wrapped_complete(conn, job_id: str, *, result=None) -> None:
        assert isinstance(conn, _LifecycleConn)
        completion_conn_used.append(True)
        original_complete(conn, job_id, result=result)

    monkeypatch.setattr(jobs, "complete", _wrapped_complete)

    outcome = jobs.run_one(
        pg_conn,
        worker_id="w1",
        handlers={"ceo_wake": _RecordingHandler(result={"ok": True})},
        heartbeat_conn_factory=_factory,
    )

    assert outcome is not None and outcome.status == "completed"
    assert completion_conn_used
    assert closed


def test_run_one_refreshes_lifecycle_session_after_handler_mutates_guc(pg_conn):
    slug, _uid = _provision_business(pg_conn)
    jobs.enqueue(pg_conn, slug, "ceo_wake", idempotency_key="j")

    class _ClearingHandler:
        def __call__(self, job: jobs.Job) -> jobs.JobRunResult:
            pg_conn.execute("select set_config('takyon.rls_bypass', '0', false)")
            return jobs.JobRunResult(result={"ok": job.business_slug}, actual_cost_cents=0)

    outcome = jobs.run_one(
        pg_conn,
        worker_id="w1",
        handlers={"ceo_wake": _ClearingHandler()},
    )

    assert outcome is not None and outcome.status == "completed"
    assert jobs.get_job(pg_conn, outcome.job_id).status == "completed"
    row = pg_conn.execute("select current_setting('takyon.rls_bypass', true)").fetchone()
    assert str(row[0]) == "1"


def test_run_one_heartbeat_failure_does_not_requeue_a_finished_job(pg_conn, monkeypatch):
    """The regression: a long build (bootstrap Docker→R2) outruns the stale window, the heartbeat
    raises JobNotRunning mid-handler, and the WHOLE finished build was requeued + re-run from scratch
    (handler_error). A heartbeat failure is a liveness signal, never a correctness gate: the handler
    is still running and its side effects are landing, so the job must settle + complete, NOT requeue.
    """
    slug, uid = _provision_business(pg_conn, allowance_cents=100_000)
    jobs.enqueue(
        pg_conn, slug, "ceo_bootstrap", idempotency_key="j",
        payload={"estimate_cents": 500}, max_attempts=2,
    )
    release = threading.Event()
    heartbeat_calls: list[str] = []

    def _failing_heartbeat(conn, job_id: str, *, worker_id: str) -> None:
        # First heartbeat fails like a lost claim; let the handler finish so we exercise the
        # finished-but-heartbeat-failed path, not the handler-error path.
        heartbeat_calls.append(job_id)
        release.set()
        raise jobs.JobNotRunning(job_id)

    monkeypatch.setattr(jobs, "heartbeat", _failing_heartbeat)

    class _SlowHandler:
        def __call__(self, job: jobs.Job) -> jobs.JobRunResult:
            release.wait(2.0)  # outlive at least one heartbeat tick so the failing heartbeat fires
            return jobs.JobRunResult(result={"built": job.business_slug}, actual_cost_cents=300)

    outcome = jobs.run_one(
        pg_conn,
        worker_id="w1",
        handlers={"ceo_bootstrap": _SlowHandler()},
        heartbeat_interval_seconds=0.05,
    )
    assert heartbeat_calls, "the failing heartbeat must have fired mid-handler"
    # The finished build COMPLETES — it is NOT requeued/failed by a heartbeat hiccup.
    assert outcome is not None and outcome.status == "completed"
    job = jobs.get_job(pg_conn, outcome.job_id)
    assert job.status == "completed"
    assert job.attempts == 1  # ran exactly once; no re-run of the full build
    assert job.result == {"built": slug}
    # The true cost settled; no reservation leaks.
    bal = billing.get_billing_balances(pg_conn, uid)
    assert bal.reserved_cents == 0
    assert bal.allowance_used_cents == 300


def test_run_one_lost_claim_on_successful_finish_completes_without_requeue(pg_conn):
    """If the claim is lost WHILE a successful build runs (requeue_stale + a sibling re-claim that then
    finalizes the row first), the terminal complete() sees a non-'running' row and raises JobNotRunning.
    That is NOT a failure to surface: the work is done and its side effects already landed — re-running
    it would only rebuild a published product. run_one reports completion and never requeues."""
    slug, uid = _provision_business(pg_conn, allowance_cents=100_000)
    jobs.enqueue(
        pg_conn, slug, "ceo_bootstrap", idempotency_key="j",
        payload={"estimate_cents": 500}, max_attempts=2,
    )

    class _ReclaimingHandler:
        """Simulate the lost-claim race: while 'running', the reaper requeues this job, a sibling
        re-claims AND finalizes it, so by the time run_one finalizes the original attempt the row is
        already terminal (no longer 'running')."""

        def __call__(self, job: jobs.Job) -> jobs.JobRunResult:
            # Make the claim look stale, reaper requeues it, sibling re-claims (attempts -> 2) and
            # completes the row before this (original) attempt returns.
            _go_stale(pg_conn, job.id)
            assert jobs.requeue_stale(pg_conn, older_than_seconds=900) == 1
            reclaimed = jobs.claim_one(pg_conn, worker_id="sibling")
            assert reclaimed is not None and reclaimed.id == job.id
            jobs.complete(pg_conn, job.id, result={"by": "sibling"})
            return jobs.JobRunResult(result={"built": job.business_slug}, actual_cost_cents=300)

    outcome = jobs.run_one(
        pg_conn,
        worker_id="w1",
        handlers={"ceo_bootstrap": _ReclaimingHandler()},
        heartbeat_interval_seconds=0,  # no heartbeat loop: isolate the terminal lost-claim path
    )
    # The original attempt finished its build; it reports completion rather than requeuing/crashing.
    assert outcome is not None and outcome.status == "completed"
    # The row is terminal (completed by whichever attempt finalized first) — NOT requeued. The original
    # attempt's own hold was released so nothing leaks from it.
    job = jobs.get_job(pg_conn, outcome.job_id)
    assert job.status == "completed"
    bal = billing.get_billing_balances(pg_conn, uid)
    assert bal.reserved_cents == 0  # original attempt's hold refunded on the lost-claim finalize


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
