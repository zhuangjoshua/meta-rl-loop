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
import logging
import concurrent.futures
import contextvars
import os
import time
from dataclasses import dataclass
from typing import Any, Callable, Mapping

from . import billing

_log = logging.getLogger("takyon.jobs")

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

# Session ownership (modularization Stage 2, UC1): a job may be RESERVED for a worker pool via
# the indexed reservation columns stamped at enqueue from a ClaimScope (claim_scope.py). The old
# payload-hint affinity (payload->>'preferred_worker_id_prefix' + grace-window LIKE matching) is
# GONE — 'after_lease' reproduces exactly that first-claim-then-spill behavior as a config value,
# and 'strict' pins the job to the owning pool while that pool's registry lease is alive (spilling,
# not stranding, when the pool dies). Requeues renew the after_lease window off updated_at so a
# healthy local retry does not immediately spill to a sibling machine (commit f899da41's contract).
_RESERVATION_GATE_SQL = (
    "and ("
    "  j.reserved_pool_id is null "
    "  or j.reserved_pool_id = %s "
    "  or (j.reservation_policy = 'after_lease' "
    "      and j.reservation_expires_at is not null "
    "      and j.reservation_expires_at <= now()) "
    "  or (j.reservation_policy = 'strict' and not exists ("
    "        select 1 from worker_pools p "
    "        where p.pool_id = j.reserved_pool_id "
    "          and p.status in ('joining', 'active', 'draining') "
    "          and p.lease_expires_at > now())) "
    ") "
)
_RESERVED_FOR_ME_ORDER_SQL = "case when j.reserved_pool_id = %s then 0 else 1 end, "
_RENEW_AFTER_LEASE_SQL = (
    "reservation_expires_at = case "
    "when reservation_policy = 'after_lease' and reservation_lease_seconds is not null "
    "then now() + (reservation_lease_seconds * interval '1 second') "
    "else reservation_expires_at end"
)

# The columns of a jobs row, in one place so every SELECT projects the same Job.
_COLS = (
    "id, business_slug, kind, status, idempotency_key, payload, result, error, "
    "reserved_billing_entry_id, attempts, max_attempts, locked_by, locked_at, created_at, updated_at"
)


def _operator_lifecycle_session_required() -> bool:
    return str(os.getenv("TAKYON_HOST_ROLE") or "").strip().lower() in {
        "operator",
        "dashboard",
        "worker",
    }


def _refresh_job_lifecycle_session(conn) -> None:
    """Reassert control-plane RLS state before touching the jobs queue."""
    required = _operator_lifecycle_session_required()
    try:
        try:
            conn.execute("reset role")
        except Exception:
            if required:
                raise
        try:
            from .runtime_app import assert_takyon_pg_role, configure_takyon_pg_session
        except ImportError:  # pragma: no cover - alternate load path
            from plugins.takyon.runtime_app import assert_takyon_pg_role, configure_takyon_pg_session

        configure_takyon_pg_session(conn, bypass=True)
        if required:
            assert_takyon_pg_role(conn, "operator")
    except Exception:
        if required:
            raise


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
    last_row = None
    for attempt in range(10):
        row = conn.execute(
            "select owner_user_id from businesses where slug = %s", (business_slug,)
        ).fetchone()
        last_row = row
        if row is not None and row[0] is not None:
            return str(row[0])
        if attempt < 9:
            time.sleep(0.5)
    if last_row is None or last_row[0] is None:
        raise BusinessOwnerMissing(business_slug)
    return str(last_row[0])


# ── enqueue / read ─────────────────────────────────────────────────────────────────────────────────


def enqueue(
    conn,
    business_slug: str,
    kind: str,
    *,
    idempotency_key: str,
    payload: dict[str, Any] | None = None,
    max_attempts: int = 5,
    claim_scope: "ClaimScope | None" = None,
) -> Job:
    """Place a job on the queue. Idempotent on ``idempotency_key``: a replay returns the EXISTING job
    unchanged (one effect — a replay never re-stamps the reservation), never a second row. ``payload``
    may carry ``estimate_cents`` (the budget run_one reserves before running) and any handler input.
    ``claim_scope`` reserves the job for a worker pool (see claim_scope.py for the policies)."""
    if max_attempts < 1:
        raise ValueError("max_attempts must be >= 1")
    reserved_pool_id = None
    reservation_policy = "any"
    lease_seconds = None
    if claim_scope is not None and claim_scope.fallback != "any":
        reserved_pool_id = str(claim_scope.pool_id or "").strip() or None
        if reserved_pool_id is not None:
            reservation_policy = claim_scope.fallback
            if claim_scope.fallback == "after_lease":
                lease_seconds = max(0.0, float(claim_scope.lease_seconds or 0.0)) or None
    _refresh_job_lifecycle_session(conn)
    body = json.dumps(payload or {})
    with conn.transaction():
        row = conn.execute(
            "insert into jobs (business_slug, kind, idempotency_key, payload, max_attempts, "
            " reserved_pool_id, reservation_policy, reservation_lease_seconds, reservation_expires_at) "
            "values (%s, %s, %s, %s::jsonb, %s, %s, %s, %s, "
            " case when %s::double precision is not null "
            "  then now() + (%s::double precision * interval '1 second') else null end) "
            "on conflict (idempotency_key) do nothing "
            f"returning {_COLS}",
            (
                business_slug,
                kind,
                idempotency_key,
                body,
                max_attempts,
                reserved_pool_id,
                reservation_policy,
                lease_seconds,
                lease_seconds,
                lease_seconds,
            ),
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


def claim_one(
    conn,
    *,
    worker_id: str,
    kinds: list[str] | tuple[str, ...] | None = None,
    owner_user_id: str | None = None,
    claim_pool_id: str | None = None,
    exclusive_pool: bool = False,
    min_queue_age_seconds: float | None = None,
) -> Job | None:
    """Atomically claim the next queued job (optionally restricted to ``kinds``): prefer
    ``ceo_bootstrap`` over ordinary queued work, then fall back to FIFO within that priority class.
    A business runs at most ONE job per lane at a time (see ``_LANE_SQL``): CEO turns serialize
    against each other, while other kinds run in their own lane alongside them. Lock one row with
    ``FOR UPDATE SKIP LOCKED`` so a second worker skips it, then flip it to 'running', stamp
    locked_by/locked_at, and increment attempts. Returns the claimed job, or None if the queue is
    empty. The whole claim is one transaction; the row is committed 'running' before this returns.

    ``claim_pool_id`` is the claiming pool's registry identity: reserved jobs are honored via the
    indexed reservation predicate (own reservations first, then unreserved, then expired/orphaned
    spills — see claim_scope.py). ``exclusive_pool=True`` claims ONLY jobs reserved for this pool
    (UC1: a session-owned pool does nobody else's work)."""
    _refresh_job_lifecycle_session(conn)
    lane_gate = (
        "and not exists ("
        "  select 1 from jobs r "
        "  where r.business_slug = j.business_slug and r.status = 'running' "
        f"  and {_LANE_SQL.format(a='r')} = {_LANE_SQL.format(a='j')}"
        ") "
    )
    # None => the process-wide env policy (Mac-first pickup delay for root jobs). An explicit
    # value lets the operator-task lane claim turn-fired sub-jobs immediately: those jobs are
    # created BY a turn already running on this worker, so aging them only stalls the turn.
    if min_queue_age_seconds is None:
        min_queue_age_seconds = max(0.0, float(os.getenv("TAKYON_WORKER_MIN_QUEUE_AGE_SECONDS") or 0.0))
    min_queue_age_seconds = max(0.0, float(min_queue_age_seconds))
    owner_filter = str(owner_user_id or "").strip()
    pool_filter = str(claim_pool_id or "").strip()
    if exclusive_pool and not pool_filter:
        raise ValueError("exclusive_pool=True requires claim_pool_id")
    age_gate = ""
    if min_queue_age_seconds > 0:
        age_gate = "and j.created_at <= (now() - (%s::double precision * interval '1 second')) "
    owner_gate = ""
    if owner_filter:
        owner_gate = (
            "and exists ("
            "  select 1 from businesses b "
            "  where b.slug = j.business_slug and b.owner_user_id = %s"
            ") "
        )
    reservation_gate = _RESERVATION_GATE_SQL
    exclusive_gate = ""
    if exclusive_pool:
        exclusive_gate = "and j.reserved_pool_id = %s "

    with conn.transaction():
        params: list[Any] = []
        kind_gate = ""
        if kinds:
            kind_gate = "and j.kind = any(%s) "
            params.append(list(kinds))
        if min_queue_age_seconds > 0:
            params.append(min_queue_age_seconds)
        if owner_filter:
            params.append(owner_filter)
        params.append(pool_filter)  # reservation gate
        if exclusive_pool:
            params.append(pool_filter)  # exclusive gate
        params.append(pool_filter)  # reserved-for-me ordering
        picked = conn.execute(
            "select j.id from jobs j "
            "where j.status = 'queued' "
            + kind_gate
            + age_gate
            + owner_gate
            + reservation_gate
            + exclusive_gate
            + lane_gate
            + "order by "
            + _RESERVED_FOR_ME_ORDER_SQL
            + "case when j.kind = 'ceo_bootstrap' then 0 else 1 end, j.created_at "
            "for update skip locked limit 1",
            tuple(params),
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
    _refresh_job_lifecycle_session(conn)
    with conn.transaction():
        updated = conn.execute(
            "update jobs set locked_at = now(), updated_at = now() "
            "where id = %s and status = 'running' and locked_by = %s",
            (job_id, worker_id),
        ).rowcount
    if updated == 0:
        row = conn.execute("select status, locked_by from jobs where id = %s", (job_id,)).fetchone()
        if (
            row is not None
            and str(row[0]) == "running"
            and str(row[1] or "") == worker_id
        ):
            return
        raise JobNotRunning(job_id)


def complete(conn, job_id: str, *, result: dict[str, Any] | None = None) -> None:
    """Terminal success. Only a 'running' job may complete — the lifecycle is single-writer (the
    claimer holds it), so a non-'running' row means a bug, and we raise rather than overwrite."""
    _refresh_job_lifecycle_session(conn)
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
    _refresh_job_lifecycle_session(conn)
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
    _refresh_job_lifecycle_session(conn)
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
                "locked_by = null, locked_at = null, updated_at = now(), "
                f"{_RENEW_AFTER_LEASE_SQL} where id = %s",
                (err, job_id),
            )
            return "requeued"
        conn.execute(
            "update jobs set status = 'failed', error = %s::jsonb, "
            "locked_by = null, locked_at = null, updated_at = now() where id = %s",
            (err, job_id),
        )
    return "failed"


def fail_if_still_owned(
    conn,
    job_id: str,
    *,
    worker_id: str,
    error: str,
    retryable: bool = True,
) -> str | None:
    """Best-effort failure finalizer for a stale lifecycle connection.

    Only touches the row if it is still running under the same worker claim. If a sibling worker has
    reclaimed it, leave that live attempt alone.
    """
    err = json.dumps({"reason": "handler_error", "error": error})
    _refresh_job_lifecycle_session(conn)
    with conn.transaction():
        row = conn.execute(
            "select attempts, max_attempts from jobs "
            "where id = %s and status = 'running' and locked_by = %s for update",
            (job_id, worker_id),
        ).fetchone()
        if row is None:
            return None
        attempts, max_attempts = int(row[0]), int(row[1])
        if retryable and attempts < max_attempts:
            conn.execute(
                "update jobs set status = 'queued', error = %s::jsonb, "
                "locked_by = null, locked_at = null, updated_at = now(), "
                f"{_RENEW_AFTER_LEASE_SQL} where id = %s",
                (err, job_id),
            )
            return "queued"
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
    _refresh_job_lifecycle_session(conn)
    with conn.transaction():
        requeued = conn.execute(
            "update jobs set status = 'queued', locked_by = null, locked_at = null, updated_at = now(), "
            f"{_RENEW_AFTER_LEASE_SQL} "
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
    owner_user_id: str | None = None,
    claim_pool_id: str | None = None,
    exclusive_pool: bool = False,
    heartbeat_interval_seconds: float = 15.0,
    heartbeat_conn_factory: Callable[[], Any] | None = None,
    min_queue_age_seconds: float | None = None,
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
    job = claim_one(
        conn,
        worker_id=worker_id,
        kinds=kinds,
        owner_user_id=owner_user_id,
        claim_pool_id=claim_pool_id,
        exclusive_pool=exclusive_pool,
        min_queue_age_seconds=min_queue_age_seconds,
    )
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

    job_lifecycle_conn = None

    def _lifecycle_conn():
        nonlocal job_lifecycle_conn
        if heartbeat_conn_factory is None:
            return conn, False
        if job_lifecycle_conn is None:
            job_lifecycle_conn = heartbeat_conn_factory()
        return job_lifecycle_conn, False

    def _close_lifecycle_conn(lifecycle_conn, should_close: bool) -> None:
        nonlocal job_lifecycle_conn
        if should_close:
            lifecycle_conn.close()
            return
        if lifecycle_conn is not None and lifecycle_conn is job_lifecycle_conn:
            lifecycle_conn.close()
            job_lifecycle_conn = None

    def _reset_lifecycle_conn() -> None:
        nonlocal job_lifecycle_conn
        if job_lifecycle_conn is None:
            return
        try:
            job_lifecycle_conn.close()
        except Exception:
            pass
        job_lifecycle_conn = None

    def _claim_is_recent(lifecycle_conn) -> bool:
        stale_seconds = max(60.0, float(os.getenv("TAKYON_WORKER_STALE_SECONDS") or 14_400))
        warning_window_seconds = max(300.0, float(heartbeat_interval_seconds or 0) * 20.0)
        window_seconds = min(warning_window_seconds, stale_seconds / 2.0)
        row = lifecycle_conn.execute(
            "select status, locked_by, locked_at > now() - (%s::double precision * interval '1 second') "
            "from jobs where id = %s",
            (window_seconds, job.id),
        ).fetchone()
        return bool(
            row is not None
            and str(row[0]) == "running"
            and str(row[1] or "") == worker_id
            and row[2]
        )

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
                # The heartbeat is a LIVENESS signal, never a correctness gate. A long build
                # (bootstrap Docker→R2 + claude-agent-task) can run past the stale threshold and lose
                # its claim to requeue_stale + a sibling re-claim; a transient DB blip can likewise
                # make one refresh fail. NEITHER means the work failed — the handler thread is still
                # running and its durable side effects (published site, receipts) are landing. If we
                # let a heartbeat exception escape here it is caught below as a "handler error" and the
                # ENTIRE 5-minute build is requeued and re-run from scratch, starving the single build
                # lane (observed on businesses "simple"/"simple-meal-planning": JobNotRunning at
                # jobs.py heartbeat → fail() → requeue → attempt 2 rebuilds everything). So swallow a
                # heartbeat failure: keep waiting for the handler, and let the TERMINAL transition be
                # the single authority on the outcome.
                try:
                    hb_conn, _close_hb = _lifecycle_conn()
                    heartbeat(hb_conn, job.id, worker_id=worker_id)
                except Exception as hb_exc:  # noqa: BLE001 — lost claim / DB blip must not requeue live work
                    if heartbeat_conn_factory is not None:
                        _reset_lifecycle_conn()
                        try:
                            hb_conn, _close_hb = _lifecycle_conn()
                            heartbeat(hb_conn, job.id, worker_id=worker_id)
                            continue
                        except Exception:
                            pass
                    try:
                        _reset_lifecycle_conn()
                        probe_conn, _close_probe = _lifecycle_conn()
                        if _claim_is_recent(probe_conn):
                            continue
                    except Exception:
                        pass
                    _log.warning(
                        "jobs: heartbeat could not refresh claim for job %s (kind=%s, non-fatal; "
                        "handler still running): %s",
                        job.id,
                        job.kind,
                        hb_exc,
                    )
        assert run_result is not None
    except Exception as exc:  # handler failed: release the hold, then fail/requeue
        lifecycle_conn, close_lifecycle = _lifecycle_conn()
        refund_exc: Exception | None = None
        try:
            if estimate_cents > 0:
                try:
                    billing.refund(lifecycle_conn, reservation_key)
                except Exception as refund_err:  # noqa: BLE001 - terminal job transition outranks refund hiccups
                    refund_exc = refund_err
                    _log.warning(
                        "jobs: refund failed after handler error for job %s (kind=%s); "
                        "continuing to fail/requeue the job so it does not stay running: %s",
                        job.id,
                        job.kind,
                        refund_err,
                    )
                    if heartbeat_conn_factory is not None:
                        _reset_lifecycle_conn()
                        lifecycle_conn, close_lifecycle = _lifecycle_conn()
            try:
                status = fail(lifecycle_conn, job.id, error=str(exc), retryable=True)
            except JobNotRunning:
                repaired_status = fail_if_still_owned(
                    lifecycle_conn,
                    job.id,
                    worker_id=worker_id,
                    error=str(exc),
                    retryable=True,
                )
                _log.warning(
                    "jobs: handler failed after job %s (kind=%s) lost its running claim; "
                    "continuing drain without wedging the worker%s",
                    job.id,
                    job.kind,
                    f" ({repaired_status})" if repaired_status else "",
                )
                status = repaired_status or "failed"
            if refund_exc is not None and estimate_cents > 0:
                try:
                    billing.refund(lifecycle_conn, reservation_key)
                except Exception as retry_exc:  # noqa: BLE001 - row is terminal; avoid wedging on cleanup
                    _log.warning(
                        "jobs: refund retry still failed after terminalizing job %s (kind=%s): %s",
                        job.id,
                        job.kind,
                        retry_exc,
                    )
        finally:
            _close_lifecycle_conn(lifecycle_conn, close_lifecycle)
        return JobOutcome(
            job.id, job.kind, status, reserved_cents=reserved, reason="handler_error"
        )

    # The handler SUCCEEDED. Settle + complete are the terminal authority. But the claim may have been
    # lost while the long build ran (requeue_stale + sibling re-claim) — then this row is no longer
    # 'running'/ours, so settle()/complete() raise JobNotRunning. That is NOT a failure to surface: the
    # work is done and its side effects already landed; re-running it would only re-build a published
    # product. Treat a lost claim on a successful finish as a benign "already finalized elsewhere":
    # release our own hold so no reservation leaks, log it, and report completion. A genuine bug (double
    # complete of a truly-terminal row) is the same harmless idempotent no-op here.
    actual = 0
    lifecycle_conn, close_lifecycle = _lifecycle_conn()
    try:
        if estimate_cents > 0:
            actual = max(0, min(int(run_result.actual_cost_cents or 0), reserved))
            billing.settle(lifecycle_conn, reservation_key, actual)
        complete(lifecycle_conn, job.id, result=run_result.result)
    except JobNotRunning:
        # Our claim was reclaimed mid-build (the build outran the stale window). Don't requeue a
        # finished, side-effect-complete job. Release our hold idempotently so the refund isn't lost.
        if estimate_cents > 0:
            try:
                billing.refund(lifecycle_conn, reservation_key)
            except Exception:  # noqa: BLE001 — best-effort; the sibling attempt reconciles the hold too
                pass
        _log.warning(
            "jobs: job %s (kind=%s) finished but its claim was lost before finalize; reporting "
            "completion without requeue (side effects already landed)",
            job.id,
            job.kind,
        )
        return JobOutcome(
            job.id, job.kind, "completed", reserved_cents=reserved, actual_cents=0
        )
    finally:
        _close_lifecycle_conn(lifecycle_conn, close_lifecycle)
    return JobOutcome(
        job.id, job.kind, "completed", reserved_cents=reserved, actual_cents=actual
    )


def _set_reserved_key(conn, job_id: str, reservation_key: str) -> None:
    _refresh_job_lifecycle_session(conn)
    with conn.transaction():
        conn.execute(
            "update jobs set reserved_billing_entry_id = %s, updated_at = now() where id = %s",
            (reservation_key, job_id),
        )
