"""The worker plane: a Postgres-backed, at-least-once job queue (``jobs`` table, migration 0010) plus
the one budget-gated execution contract every job runs under.

This is a pure leaf — the caller owns the connection (one per-request autocommit psycopg connection,
exactly as ``runtime_app`` opens it) and each mutating operation opens its own ``with
conn.transaction():``. There is no global state, no pool, no thread; a worker is just a process that
calls :func:`run_one` in a loop.

The contract (mediationplan.md > Worker Plane):
  * ONE job, ONE worker — :func:`claim_one` uses ``SELECT … FOR UPDATE SKIP LOCKED`` so two workers
    never pick the same row.
  * At-least-once + idempotent — :func:`enqueue` is ``on conflict (idempotency_key) do nothing``; a
    replay returns the SAME job, never a second effect.
  * Budget-gated — before running, :func:`run_one` reserves the job's estimate on the OWNER's flow-A
    billing account (``billing.reserve`` — the same engine top-ups and inline spend use). On success
    it settles the true cost and releases the remainder; on failure it refunds the whole hold. A
    reserve that the buckets cannot cover ⇒ the job is **blocked** with a reason and NOTHING runs
    (invariant #8: partial = blocked/failed, never a fabricated completion).
  * Retries re-check budget — each attempt reserves under a fresh key, so an exhausted budget blocks
    instead of retrying forever. A crash mid-job is reconciled by releasing the stale hold before the
    next attempt reserves again, so a held-but-never-settled reservation can never leak.
  * The job row is its own receipt — ``status`` + ``result``/``error`` are written atomically with the
    terminal transition, so the durable record is the source of truth for what happened.

The actual work is a SEAM: :func:`run_one` takes a ``handlers`` mapping ``kind -> Handler``. The host
wires real handlers (a ``ceo_wake`` handler that runs the CEO turn, a build handler, …); tests pass
deterministic stubs. This mirrors the AI gateway's ``get_provider_caller`` seam: the engine — claim,
budget, lifecycle — is real and tested on real Postgres; only the leaf side effect is injected.
"""

from __future__ import annotations

import json
import concurrent.futures
import contextvars
from dataclasses import dataclass
from typing import Any, Callable, Mapping

from . import billing

# A handler runs one job's actual work and returns its result + the TRUE cost to settle. A handler
# that spends nothing returns actual_cost_cents=0 (then no settle/refund moves money). Raising signals
# failure: run_one refunds the hold and fails/retries the job.
Handler = Callable[["Job"], "JobRunResult"]


class JobError(RuntimeError):
    """Base class for worker-plane errors."""


class UnknownJob(JobError):
    """No job matches the given id."""


class JobNotRunning(JobError):
    """A lifecycle transition (complete/block/fail) referenced a job that is not in 'running' — a
    double-finalize or a lost claim. Raised loud rather than silently overwriting a terminal row."""


class BusinessOwnerMissing(JobError):
    """A job's business has no resolvable owner_user_id (0001 makes it NOT NULL), so its spend could
    not be reserved against anyone — an integrity violation, surfaced rather than charged to no one."""


_TERMINAL = ("completed", "blocked", "failed", "cancelled")

# Lanes: the per-business concurrency gate in claim_one is PER LANE, not per business-total. CEO
# turns (ceo_bootstrap/ceo_wake) share one 'ceo' lane so a business never runs two CEO turns at
# once; every other kind is its own lane so a long-running job cannot starve wakes — and a CEO turn
# that enqueues another kind and waits on it cannot deadlock behind its own business gate. The lane
# is derived from kind in SQL (no schema change, dispatch_due_wakes untouched).
_LANE_SQL = "(case when {a}.kind in ('ceo_bootstrap', 'ceo_wake') then 'ceo' else {a}.kind end)"

# The columns of a jobs row, in one place so every SELECT projects the same Job.
_COLS = (
    "id, business_slug, kind, status, idempotency_key, payload, result, error, "
    "reserved_billing_entry_id, attempts, max_attempts, locked_by, locked_at, created_at, updated_at"
)


@dataclass(frozen=True)
class Job:
    id: str
    business_slug: str
    kind: str
    status: str
    idempotency_key: str
    payload: dict[str, Any]
    result: dict[str, Any] | None
    error: dict[str, Any] | None
    reserved_billing_entry_id: str | None
    attempts: int
    max_attempts: int
    locked_by: str | None
    locked_at: Any
    created_at: Any
    updated_at: Any


@dataclass(frozen=True)
class JobRunResult:
    """What a handler returns: the result payload to persist and the TRUE cost (microcents → cents,
    the handler's call) to settle. ``actual_cost_cents`` is clamped to ≤ the reserved estimate by
    run_one (settle never exceeds the hold)."""

    result: dict[str, Any] | None = None
    actual_cost_cents: int = 0


@dataclass(frozen=True)
class JobOutcome:
    """The result of one run_one cycle, for the worker loop / tests to observe without re-querying."""

    job_id: str
    kind: str
    status: str  # 'completed' | 'blocked' | 'failed' | 'requeued'
    reserved_cents: int = 0
    actual_cents: int = 0
    reason: str | None = None


def _row_to_job(row: tuple) -> Job:
    return Job(
        id=str(row[0]),
        business_slug=row[1],
        kind=row[2],
        status=row[3],
        idempotency_key=row[4],
        payload=row[5] or {},
        result=row[6],
        error=row[7],
        reserved_billing_entry_id=row[8],
        attempts=int(row[9]),
        max_attempts=int(row[10]),
        locked_by=row[11],
        locked_at=row[12],
        created_at=row[13],
        updated_at=row[14],
    )


def _resolve_owner_user_id(conn, business_slug: str) -> str:
    row = conn.execute(
        "select owner_user_id from businesses where slug = %s", (business_slug,)
    ).fetchone()
    if row is None or row[0] is None:
        raise BusinessOwnerMissing(business_slug)
    return str(row[0])


# ── enqueue / read ─────────────────────────────────────────────────────────────────────────────────


def enqueue(
    conn,
    business_slug: str,
    kind: str,
    *,
    idempotency_key: str,
    payload: dict[str, Any] | None = None,
    max_attempts: int = 5,
) -> Job:
    """Place a job on the queue. Idempotent on ``idempotency_key``: a replay returns the EXISTING job
    unchanged (one effect), never a second row. ``payload`` may carry ``estimate_cents`` (the budget
    run_one reserves before running) and any handler input."""
    if max_attempts < 1:
        raise ValueError("max_attempts must be >= 1")
    body = json.dumps(payload or {})
    with conn.transaction():
        row = conn.execute(
            "insert into jobs (business_slug, kind, idempotency_key, payload, max_attempts) "
            "values (%s, %s, %s, %s::jsonb, %s) "
            "on conflict (idempotency_key) do nothing "
            f"returning {_COLS}",
            (business_slug, kind, idempotency_key, body, max_attempts),
        ).fetchone()
        if row is not None:
            return _row_to_job(row)
        # Conflict: the job already exists (a replay). Return it as-is.
        existing = conn.execute(
            f"select {_COLS} from jobs where idempotency_key = %s", (idempotency_key,)
        ).fetchone()
    return _row_to_job(existing)


def get_job(conn, job_id: str) -> Job | None:
    row = conn.execute(f"select {_COLS} from jobs where id = %s", (job_id,)).fetchone()
    return _row_to_job(row) if row else None


def list_jobs(conn, business_slug: str, *, limit: int = 50) -> list[Job]:
    rows = conn.execute(
        f"select {_COLS} from jobs where business_slug = %s order by created_at desc limit %s",
        (business_slug, limit),
    ).fetchall()
    return [_row_to_job(r) for r in rows]


# ── claim / lifecycle ────────────────────────────────────────────────────────────────────────────


def claim_one(conn, *, worker_id: str, kinds: list[str] | tuple[str, ...] | None = None) -> Job | None:
    """Atomically claim the next queued job (optionally restricted to ``kinds``): prefer
    ``ceo_bootstrap`` over ordinary queued work, then fall back to FIFO within that priority class.
    A business runs at most ONE job per lane at a time (see ``_LANE_SQL``): CEO turns serialize
    against each other, while other kinds run in their own lane alongside them. Lock one row with
    ``FOR UPDATE SKIP LOCKED`` so a second worker skips it, then flip it to 'running', stamp
    locked_by/locked_at, and increment attempts. Returns the claimed job, or None if the queue is
    empty. The whole claim is one transaction; the row is committed 'running' before this returns."""
    lane_gate = (
        "and not exists ("
        "  select 1 from jobs r "
        "  where r.business_slug = j.business_slug and r.status = 'running' "
        f"  and {_LANE_SQL.format(a='r')} = {_LANE_SQL.format(a='j')}"
        ") "
    )
    with conn.transaction():
        if kinds:
            picked = conn.execute(
                "select j.id from jobs j "
                "where j.status = 'queued' and j.kind = any(%s) "
                + lane_gate
                + "order by case when j.kind = 'ceo_bootstrap' then 0 else 1 end, j.created_at "
                "for update skip locked limit 1",
                (list(kinds),),
            ).fetchone()
        else:
            picked = conn.execute(
                "select j.id from jobs j "
                "where j.status = 'queued' "
                + lane_gate
                + "order by case when j.kind = 'ceo_bootstrap' then 0 else 1 end, j.created_at "
                "for update skip locked limit 1"
            ).fetchone()
        if picked is None:
            return None
        row = conn.execute(
            "update jobs set status = 'running', locked_by = %s, locked_at = now(), "
            "attempts = attempts + 1, updated_at = now() "
            f"where id = %s returning {_COLS}",
            (worker_id, picked[0]),
        ).fetchone()
    return _row_to_job(row)


def heartbeat(conn, job_id: str, *, worker_id: str) -> None:
    """Refresh a running job's claim so other workers can distinguish live work from a stale claim."""
    with conn.transaction():
        updated = conn.execute(
            "update jobs set locked_at = now(), updated_at = now() "
            "where id = %s and status = 'running' and locked_by = %s",
            (job_id, worker_id),
        ).rowcount
    if updated == 0:
        raise JobNotRunning(job_id)


def complete(conn, job_id: str, *, result: dict[str, Any] | None = None) -> None:
    """Terminal success. Only a 'running' job may complete — the lifecycle is single-writer (the
    claimer holds it), so a non-'running' row means a bug, and we raise rather than overwrite."""
    body = json.dumps(result) if result is not None else None
    with conn.transaction():
        updated = conn.execute(
            "update jobs set status = 'completed', result = %s::jsonb, error = null, "
            "locked_by = null, locked_at = null, updated_at = now() "
            "where id = %s and status = 'running'",
            (body, job_id),
        ).rowcount
    if updated == 0:
        raise JobNotRunning(job_id)


def block(conn, job_id: str, *, reason: str, detail: dict[str, Any] | None = None) -> None:
    """Terminal block (invariant #8): the work could not run for a NAMED reason (budget exhausted, no
    handler, missing config). Distinct from 'failed' (the work was attempted and errored)."""
    err = {"reason": reason}
    if detail:
        err["detail"] = detail
    with conn.transaction():
        updated = conn.execute(
            "update jobs set status = 'blocked', error = %s::jsonb, "
            "locked_by = null, locked_at = null, updated_at = now() "
            "where id = %s and status = 'running'",
            (json.dumps(err), job_id),
        ).rowcount
    if updated == 0:
        raise JobNotRunning(job_id)


def fail(conn, job_id: str, *, error: str, retryable: bool = True) -> str:
    """The work was attempted and raised. If retryable and attempts remain, re-queue (back to
    'queued', lock released) for another claim; else mark 'failed'. Returns 'requeued' or 'failed'.
    The hold is released by run_one BEFORE this is called, and the stale-hold reconciliation in
    run_one releases it again (idempotent) on the next attempt, so no reservation leaks on requeue."""
    err = json.dumps({"reason": "handler_error", "error": error})
    with conn.transaction():
        row = conn.execute(
            "select attempts, max_attempts from jobs where id = %s and status = 'running' for update",
            (job_id,),
        ).fetchone()
        if row is None:
            raise JobNotRunning(job_id)
        attempts, max_attempts = int(row[0]), int(row[1])
        if retryable and attempts < max_attempts:
            conn.execute(
                "update jobs set status = 'queued', error = %s::jsonb, "
                "locked_by = null, locked_at = null, updated_at = now() where id = %s",
                (err, job_id),
            )
            return "requeued"
        conn.execute(
            "update jobs set status = 'failed', error = %s::jsonb, "
            "locked_by = null, locked_at = null, updated_at = now() where id = %s",
            (err, job_id),
        )
    return "failed"


def requeue_stale(conn, *, older_than_seconds: int = 900, worker_id: str = "reaper") -> int:
    """Crash recovery (mediationplan: worker crash mid-job → safe retry). A worker that dies leaves its
    job 'running' with a stale locked_at; claim_one only picks 'queued', so without this the job is
    stuck forever. Re-queue 'running' jobs whose lock is older than the threshold and that have
    attempts left; those at max_attempts are blocked with a reason (never retried forever). The next
    claim's run_one releases any stale billing hold before reserving again. Returns rows touched."""
    with conn.transaction():
        requeued = conn.execute(
            "update jobs set status = 'queued', locked_by = null, locked_at = null, updated_at = now() "
            "where status = 'running' and locked_at < now() - make_interval(secs => %s) "
            "and attempts < max_attempts",
            (older_than_seconds,),
        ).rowcount
        blocked = conn.execute(
            "update jobs set status = 'blocked', "
            "error = %s::jsonb, locked_by = null, locked_at = null, updated_at = now() "
            "where status = 'running' and locked_at < now() - make_interval(secs => %s) "
            "and attempts >= max_attempts",
            (json.dumps({"reason": "stalled_max_attempts"}), older_than_seconds),
        ).rowcount
    return int(requeued) + int(blocked)


# ── the one budget-gated execution cycle ───────────────────────────────────────────────────────────


def run_one(
    conn,
    *,
    worker_id: str,
    handlers: Mapping[str, Handler],
    kinds: list[str] | tuple[str, ...] | None = None,
    heartbeat_interval_seconds: float = 15.0,
) -> JobOutcome | None:
    """Claim one job and run it under the full contract; returns its outcome, or None if the queue is
    empty. The pipeline, each step its own transaction on the autocommit conn:

      1. claim          — FOR UPDATE SKIP LOCKED → 'running' (attempts++).
      2. handler lookup — no handler for the kind ⇒ blocked('no_handler'); nothing reserved.
      3. reserve        — release any stale hold from a crashed prior attempt (idempotent refund),
                          then reserve estimate_cents on the OWNER's flow-A account under a per-attempt
                          key. InsufficientBalance ⇒ blocked('budget_exhausted'); nothing runs.
      4. run            — handler(job). Raises ⇒ refund the hold, then fail/requeue.
      5. settle         — clamp actual ≤ reserved, settle (releases the remainder), complete.
    """
    job = claim_one(conn, worker_id=worker_id, kinds=kinds)
    if job is None:
        return None

    handler = handlers.get(job.kind)
    if handler is None:
        block(conn, job.id, reason="no_handler", detail={"kind": job.kind})
        return JobOutcome(job.id, job.kind, "blocked", reason="no_handler")

    estimate_cents = int((job.payload or {}).get("estimate_cents", 0) or 0)
    reservation_key = f"job:{job.id}:{job.attempts}"
    reserved = 0

    if estimate_cents > 0:
        owner_user_id = _resolve_owner_user_id(conn, job.business_slug)
        # Reconcile a crashed prior attempt: release whatever it held (idempotent — a no-op if it was
        # already settled/refunded) so a held-but-never-settled reservation never leaks across retries.
        if job.reserved_billing_entry_id and job.reserved_billing_entry_id != reservation_key:
            billing.refund(conn, job.reserved_billing_entry_id)
        _set_reserved_key(conn, job.id, reservation_key)
        try:
            res = billing.reserve(
                conn,
                owner_user_id,
                estimate_cents,
                reservation_key,
                business_slug=job.business_slug,
                job_id=str(job.id),
            )
            reserved = res.allowance_cents
        except billing.InsufficientBalance as exc:
            block(
                conn,
                job.id,
                reason="budget_exhausted",
                detail={"estimate_cents": estimate_cents, "error": str(exc)},
            )
            return JobOutcome(job.id, job.kind, "blocked", reason="budget_exhausted")

    try:
        wait_timeout = (
            heartbeat_interval_seconds
            if heartbeat_interval_seconds and heartbeat_interval_seconds > 0
            else None
        )
        run_result: JobRunResult | None = None
        ctx = contextvars.copy_context()
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(ctx.run, handler, job)
            while True:
                if wait_timeout is None:
                    run_result = future.result()
                    break
                done, _ = concurrent.futures.wait({future}, timeout=wait_timeout)
                if done:
                    run_result = future.result()
                    break
                heartbeat(conn, job.id, worker_id=worker_id)
        assert run_result is not None
    except Exception as exc:  # handler failed: release the hold, then fail/requeue
        if estimate_cents > 0:
            billing.refund(conn, reservation_key)
        status = fail(conn, job.id, error=str(exc), retryable=True)
        return JobOutcome(
            job.id, job.kind, status, reserved_cents=reserved, reason="handler_error"
        )

    actual = 0
    if estimate_cents > 0:
        actual = max(0, min(int(run_result.actual_cost_cents or 0), reserved))
        billing.settle(conn, reservation_key, actual)
    complete(conn, job.id, result=run_result.result)
    return JobOutcome(
        job.id, job.kind, "completed", reserved_cents=reserved, actual_cents=actual
    )


def _set_reserved_key(conn, job_id: str, reservation_key: str) -> None:
    with conn.transaction():
        conn.execute(
            "update jobs set reserved_billing_entry_id = %s, updated_at = now() where id = %s",
            (reservation_key, job_id),
        )
