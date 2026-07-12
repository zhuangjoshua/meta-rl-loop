"""The worker plane: a Postgres-backed, at-least-once job queue (``jobs`` table, migration 0010) plus
the one budget-gated execution contract every job runs under.

This is a pure leaf — the caller owns the connection (one per-request autocommit psycopg connection,
exactly as ``runtime_app`` opens it) and each mutating operation opens its own ``with
conn.transaction():``. There is no internal worker pool: a worker is a process that calls
:func:`run_one` in a loop; one run owns its bounded handler and lease-watchdog threads.

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
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
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


class JobClaimLost(JobNotRunning):
    """The handler's exact ``(job id, worker id, attempt)`` generation is no longer authoritative.

    A job id alone is not an ownership token: a stale-job requeue can give the same row back to the
    same worker process with ``attempts + 1``.  Raising this distinct error lets long-running child
    work terminate without retrying or committing through the newer attempt's claim.
    """


class BusinessOwnerMissing(JobError):
    """A job's business has no resolvable owner_user_id (0001 makes it NOT NULL), so its spend could
    not be reserved against anyone — an integrity violation, surfaced rather than charged to no one."""


class NonRetryableJobError(JobError):
    """Raise from a job handler when the failure is DETERMINISTIC — e.g. an upstream provider
    rejected the request with an invalid-request-class 4xx (400/401/403/404), where a re-run with
    identical inputs fails identically. ``run_one`` then finalizes the job through the EXISTING
    terminal path (``fail(..., retryable=False)`` → status 'failed') on the first claim instead of
    requeueing it for another identical burn (observed: a workspace-usage-limit 400 burned
    2 claims x 6 API retries before failing generic 'handler_error')."""


_TERMINAL = ("completed", "blocked", "failed", "cancelled")

# Lanes: CEO turns serialize with CEO turns but remain separate from the canonical product-writer
# lane so a CEO can await its delegated build without deadlocking.  All jobs capable of advancing
# product source/build/live pointers share ONE lane; otherwise a refresh or store build can overlap a
# Claude writer for the same business even though same-kind attempts are generation-fenced.
_PRODUCT_WRITER_KINDS = ("claude.agent_task", "product.surface_refresh", "store.build")
_LANE_SQL = (
    "(case "
    "when {a}.kind in ('ceo_bootstrap', 'ceo_wake') then 'ceo' "
    "when {a}.kind in ('claude.agent_task', 'product.surface_refresh', 'store.build') then 'product' "
    "else {a}.kind end)"
)


def job_lane(kind: str) -> str:
    """Python mirror of ``_LANE_SQL`` for diagnostics and deterministic regressions."""
    normalized = str(kind or "").strip()
    if normalized in {"ceo_bootstrap", "ceo_wake"}:
        return "ceo"
    if normalized in _PRODUCT_WRITER_KINDS:
        return "product"
    return normalized

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
    "reserved_billing_entry_id, attempts, max_attempts, locked_by, locked_at, created_at, updated_at, "
    "reserved_pool_id, required_release_sha, claimed_release_sha, claimed_pool_id"
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
    reserved_pool_id: str | None = None
    required_release_sha: str = ""
    claimed_release_sha: str | None = None
    claimed_pool_id: str | None = None


@dataclass(frozen=True)
class JobRunResult:
    """What a handler returns: the result payload to persist and the TRUE cost (microcents → cents,
    the handler's call) to settle. ``actual_cost_cents`` is clamped to ≤ the reserved estimate by
    run_one (settle never exceeds the hold)."""

    result: dict[str, Any] | None = None
    actual_cost_cents: int = 0


@dataclass
class JobClaimGuard:
    """Process-local cancellation handle for one exact durable job claim generation."""

    job_id: str
    worker_id: str
    attempt: int
    _lost: threading.Event = field(default_factory=threading.Event, repr=False)
    _reason: str = field(default="", repr=False)

    @property
    def lost(self) -> bool:
        return self._lost.is_set()

    @property
    def reason(self) -> str:
        return self._reason or "durable job claim is no longer owned"

    def mark_lost(self, reason: str = "") -> None:
        if reason and not self._reason:
            self._reason = str(reason)
        self._lost.set()

    def assert_owned(self, operation: str = "continue") -> None:
        if self.lost:
            raise JobClaimLost(
                f"worker claim lost before {operation}: job={self.job_id} "
                f"worker={self.worker_id} attempt={self.attempt}: {self.reason}"
            )


@dataclass
class ProductWriterLeaseGuard:
    """Live proof that one exact DB transaction still owns a business writer lease.

    Transaction advisory locks disappear as soon as their connection/backend disappears.  A plain
    context manager cannot notice that while a long child handler is running, so the replacement
    writer could acquire the key while the old child kept editing.  This guard fingerprints the
    SAME backend transaction that acquired the lock and is probed on that same connection.  Any
    connection error or backend/xid change fails closed and wakes the existing job/child abort rail.
    """

    key: str
    backend_pid: int
    transaction_id: str
    _conn: Any = field(repr=False)
    _on_lost: Callable[[str], None] | None = field(default=None, repr=False)
    _lost: threading.Event = field(default_factory=threading.Event, repr=False)
    _reason: str = field(default="", repr=False)
    _reason_lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    @classmethod
    def capture(
        cls,
        conn: Any,
        *,
        key: str,
        on_lost: Callable[[str], None] | None = None,
    ) -> "ProductWriterLeaseGuard":
        backend_pid, transaction_id = _product_writer_lease_identity(conn, key=key)
        return cls(
            key=str(key),
            backend_pid=backend_pid,
            transaction_id=transaction_id,
            _conn=conn,
            _on_lost=on_lost,
        )

    @property
    def lost(self) -> bool:
        return self._lost.is_set()

    @property
    def reason(self) -> str:
        return self._reason or "product-writer lease is no longer owned"

    def mark_lost(self, reason: str) -> None:
        normalized = str(reason or "product-writer lease is no longer owned")
        first = False
        with self._reason_lock:
            if not self._lost.is_set():
                self._reason = normalized
                self._lost.set()
                first = True
        if first and self._on_lost is not None:
            try:
                self._on_lost(normalized)
            except Exception:
                pass

    def assert_owned(self, operation: str = "continue") -> None:
        if self.lost:
            raise JobClaimLost(
                f"product-writer lease lost before {operation}: key={self.key}: {self.reason}"
            )

    def probe_same_transaction(self) -> None:
        """Prove that the connection still represents the acquiring backend transaction."""
        self.assert_owned("lease probe")
        try:
            backend_pid, transaction_id = _product_writer_lease_identity(
                self._conn,
                key=self.key,
            )
        except Exception as exc:
            self.mark_lost(f"same-connection lease probe failed: {exc}")
            self.assert_owned("lease probe")
            return
        if backend_pid != self.backend_pid or transaction_id != self.transaction_id:
            self.mark_lost(
                "same-connection transaction identity changed "
                f"from {self.backend_pid}/{self.transaction_id} "
                f"to {backend_pid}/{transaction_id}"
            )
        self.assert_owned("lease probe")


_ACTIVE_JOB_CLAIM: contextvars.ContextVar[JobClaimGuard | None] = contextvars.ContextVar(
    "takyon_active_job_claim", default=None
)
_ACTIVE_EXECUTION_LEASE_KEY: contextvars.ContextVar[str] = contextvars.ContextVar(
    "takyon_active_execution_lease_key", default=""
)
_ACTIVE_EXECUTION_LEASE_GUARD: contextvars.ContextVar[ProductWriterLeaseGuard | None] = (
    contextvars.ContextVar("takyon_active_execution_lease_guard", default=None)
)
_LIVE_LOCAL_HANDLER_CLAIMS: set[tuple[str, int]] = set()
_LIVE_LOCAL_HANDLER_CLAIMS_LOCK = threading.Lock()

_PRODUCT_WRITER_LEASE_PROBE_SECONDS = 1.0


def current_job_claim() -> JobClaimGuard | None:
    """Return the exact claim guard bound to the current handler context, if any."""
    return _ACTIVE_JOB_CLAIM.get()


def product_writer_lease_key(business_slug: str) -> str:
    """Cross-process single-writer key; business-scoped, never process/global scoped."""
    return f"takyon-product-writer:{str(business_slug or '').strip().lower()}:product"


def current_execution_lease_key() -> str:
    return str(_ACTIVE_EXECUTION_LEASE_KEY.get() or "")


def current_execution_lease_guard() -> ProductWriterLeaseGuard | None:
    return _ACTIVE_EXECUTION_LEASE_GUARD.get()


def _product_writer_lease_identity(conn: Any, *, key: str) -> tuple[int, str]:
    """Fingerprint the backend transaction and prove it still owns this exact bigint lock."""
    row = conn.execute(
        "with lease_key as (select hashtextextended(%s, 0) as value) "
        "select pg_backend_pid() as backend_pid, "
        "pg_current_xact_id()::text as transaction_id, "
        "exists ("
        " select 1 from pg_locks held cross join lease_key "
        " where held.locktype = 'advisory' and held.pid = pg_backend_pid() "
        " and held.granted and held.mode = 'ExclusiveLock' and held.objsubid = 1 "
        " and held.classid::bigint = ((lease_key.value >> 32) & 4294967295) "
        " and held.objid::bigint = (lease_key.value & 4294967295)"
        ") as owns_lock",
        (key,),
    ).fetchone()
    if row is None:
        raise JobClaimLost("product-writer lease identity query returned no row")
    if isinstance(row, Mapping):
        backend_pid = row.get("backend_pid")
        transaction_id = row.get("transaction_id")
        owns_lock = row.get("owns_lock")
    else:
        backend_pid, transaction_id, owns_lock = row[0], row[1], row[2]
    if backend_pid is None or not str(transaction_id or "").strip():
        raise JobClaimLost("product-writer lease identity query returned incomplete state")
    if not bool(owns_lock):
        raise JobClaimLost("same backend transaction no longer owns the advisory key")
    return int(backend_pid), str(transaction_id)


@contextmanager
def _monitor_product_writer_lease(
    conn: Any,
    *,
    key: str,
    on_lost: Callable[[str], None] | None = None,
):
    """Bind and continuously verify one already-acquired transaction advisory lock."""
    guard = ProductWriterLeaseGuard.capture(conn, key=key, on_lost=on_lost)
    key_token = _ACTIVE_EXECUTION_LEASE_KEY.set(key)
    guard_token = _ACTIVE_EXECUTION_LEASE_GUARD.set(guard)
    stop = threading.Event()

    def _watch() -> None:
        while not stop.wait(_PRODUCT_WRITER_LEASE_PROBE_SECONDS):
            try:
                guard.probe_same_transaction()
            except JobClaimLost:
                return

    watcher = threading.Thread(
        target=_watch,
        name="takyon-product-writer-lease",
        daemon=True,
    )
    watcher.start()
    try:
        yield guard
    finally:
        stop.set()
        watcher.join(timeout=max(2.0, _PRODUCT_WRITER_LEASE_PROBE_SECONDS * 2.0))
        if watcher.is_alive():
            guard.mark_lost("same-connection lease watchdog did not stop")
        _ACTIVE_EXECUTION_LEASE_GUARD.reset(guard_token)
        _ACTIVE_EXECUTION_LEASE_KEY.reset(key_token)
    # A final same-transaction proof closes the interval between the last periodic probe and the
    # handler return.  A broken connection may make the surrounding transaction exit raise too;
    # callers normalize that to this fail-closed lease-loss error.
    guard.probe_same_transaction()


@contextmanager
def _hold_product_writer_lease(
    job: Job,
    *,
    fallback_conn: Any,
    conn_factory: Callable[[], Any] | None,
    waiting_heartbeat: Callable[[], None] | None = None,
    claim_guard: JobClaimGuard | None = None,
):
    """Hold one DB-backed writer lease until the handler and every child have joined.

    A stale reaper may supersede the job row while its old handler is still alive. The replacement
    claim cannot enter the same business's product lane until the old handler unwinds and releases
    this transaction-scoped advisory lock. Different business keys remain fully parallel.
    """
    if job_lane(job.kind) != "product":
        yield
        return
    key = product_writer_lease_key(job.business_slug)
    lease_conn = conn_factory() if conn_factory is not None else fallback_conn
    close_lease_conn = conn_factory is not None
    lease_guard: ProductWriterLeaseGuard | None = None
    try:
        try:
            with lease_conn.transaction():
                next_heartbeat = 0.0
                while True:
                    row = lease_conn.execute(
                        "select pg_try_advisory_xact_lock(hashtextextended(%s, 0))",
                        (key,),
                    ).fetchone()
                    acquired = bool(
                        row
                        and (
                            row[0]
                            if not isinstance(row, Mapping)
                            else next(iter(row.values()))
                        )
                    )
                    if acquired:
                        break
                    now = time.monotonic()
                    if waiting_heartbeat is not None and now >= next_heartbeat:
                        waiting_heartbeat()
                        next_heartbeat = now + 5.0
                    time.sleep(0.25)
                if waiting_heartbeat is not None:
                    waiting_heartbeat()
                with _monitor_product_writer_lease(
                    lease_conn,
                    key=key,
                    on_lost=claim_guard.mark_lost if claim_guard is not None else None,
                ) as lease_guard:
                    yield lease_guard
        except Exception as exc:
            if lease_guard is not None and lease_guard.lost:
                raise JobClaimLost(
                    f"product-writer lease lost for {job.business_slug}: {lease_guard.reason}"
                ) from exc
            raise
    finally:
        if close_lease_conn:
            try:
                lease_conn.close()
            except Exception:
                pass


@contextmanager
def _bound_job_claim(guard: JobClaimGuard):
    token = _ACTIVE_JOB_CLAIM.set(guard)
    claim_key = (guard.job_id, guard.attempt)
    with _LIVE_LOCAL_HANDLER_CLAIMS_LOCK:
        _LIVE_LOCAL_HANDLER_CLAIMS.add(claim_key)
    try:
        yield guard
    finally:
        with _LIVE_LOCAL_HANDLER_CLAIMS_LOCK:
            _LIVE_LOCAL_HANDLER_CLAIMS.discard(claim_key)
        _ACTIVE_JOB_CLAIM.reset(token)


def _live_local_job_ids() -> list[str]:
    with _LIVE_LOCAL_HANDLER_CLAIMS_LOCK:
        return sorted({job_id for job_id, _attempt in _LIVE_LOCAL_HANDLER_CLAIMS})


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
        reserved_pool_id=row[15],
        required_release_sha=str(row[16] or ""),
        claimed_release_sha=str(row[17]) if row[17] else None,
        claimed_pool_id=str(row[18]) if row[18] else None,
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
    required_release_sha: str | None = None,
) -> Job:
    """Place a job on the queue. Idempotent on ``idempotency_key``: a replay returns the EXISTING job
    unchanged (one effect — a replay never re-stamps the reservation), never a second row. ``payload``
    may carry ``estimate_cents`` (the budget run_one reserves before running) and any handler input.
    ``claim_scope`` reserves the job for a worker pool (see claim_scope.py for the policies)."""
    if max_attempts < 1:
        raise ValueError("max_attempts must be >= 1")
    from .claim_scope import require_local_release_sha

    exact_release = require_local_release_sha(
        required_release_sha,
        field="required_release_sha",
    )
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
            "insert into jobs (business_slug, kind, idempotency_key, payload, max_attempts, required_release_sha, "
            " reserved_pool_id, reservation_policy, reservation_lease_seconds, reservation_expires_at) "
            "values (%s, %s, %s, %s::jsonb, %s, %s, %s, %s, %s, "
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
                exact_release,
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
    worker_release_sha: str | None = None,
) -> Job | None:
    """Atomically claim the next queued job (optionally restricted to ``kinds``): prefer
    ``ceo_bootstrap`` over ordinary queued work, then fall back to FIFO within that priority class.
    A business runs at most ONE job per lane at a time (see ``_LANE_SQL``): CEO turns serialize
    against each other, canonical product writers serialize together, and unrelated kinds retain
    independent lanes. Lock one row with ``FOR UPDATE SKIP LOCKED`` so a second worker skips it,
    then flip it to 'running', stamp locked_by/locked_at, and increment attempts. Returns the
    claimed job, or None if the queue is
    empty. The whole claim is one transaction; the row is committed 'running' before this returns.

    ``claim_pool_id`` is the claiming pool's registry identity: reserved jobs are honored via the
    indexed reservation predicate (own reservations first, then unreserved, then expired/orphaned
    spills — see claim_scope.py). ``exclusive_pool=True`` claims ONLY jobs reserved for this pool
    (UC1: a session-owned pool does nobody else's work)."""
    _refresh_job_lifecycle_session(conn)
    from .claim_scope import require_local_release_sha

    exact_release = require_local_release_sha(worker_release_sha, field="worker_release_sha")
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
        params.append(exact_release)
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
            + "and j.required_release_sha = %s "
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
            "attempts = attempts + 1, updated_at = now(), claimed_release_sha = %s, "
            "claimed_pool_id = nullif(%s, '') "
            f"where id = %s returning {_COLS}",
            (worker_id, exact_release, pool_filter, picked[0]),
        ).fetchone()
    return _row_to_job(row)


def heartbeat(conn, job_id: str, *, worker_id: str, attempt: int | None = None) -> None:
    """Refresh one exact running claim generation.

    ``worker_id`` alone is insufficient because the same process can reclaim the same row after a
    stale requeue.  Callers that own a claimed :class:`Job` pass ``attempt`` so an old handler can
    never heartbeat the newer generation back to life.
    """
    _refresh_job_lifecycle_session(conn)
    attempt_gate = " and attempts = %s" if attempt is not None else ""
    params: tuple[Any, ...] = (
        (job_id, worker_id, int(attempt)) if attempt is not None else (job_id, worker_id)
    )
    with conn.transaction():
        updated = conn.execute(
            "update jobs set locked_at = now(), updated_at = now() "
            "where id = %s and status = 'running' and locked_by = %s "
            "and coalesce((payload ->> 'cancel_requested')::boolean, false) = false"
            + attempt_gate,
            params,
        ).rowcount
    if updated == 0:
        row = conn.execute(
            "select status, locked_by, attempts, "
            "coalesce((payload ->> 'cancel_requested')::boolean, false) "
            "from jobs where id = %s",
            (job_id,),
        ).fetchone()
        if (
            row is not None
            and str(row[0]) == "running"
            and str(row[1] or "") == worker_id
            and (attempt is None or int(row[2]) == int(attempt))
            and not bool(row[3])
        ):
            return
        raise JobClaimLost(
            f"heartbeat rejected stale claim generation: job={job_id} "
            f"worker={worker_id} attempt={attempt}"
        )


def complete(
    conn,
    job_id: str,
    *,
    result: dict[str, Any] | None = None,
    worker_id: str | None = None,
    attempt: int | None = None,
) -> None:
    """Terminal success. Only a 'running' job may complete — the lifecycle is single-writer (the
    claimer holds it), so a non-'running' row means a bug, and we raise rather than overwrite."""
    _refresh_job_lifecycle_session(conn)
    body = json.dumps(result) if result is not None else None
    claim_gate = ""
    params: list[Any] = [body, job_id]
    if worker_id is not None:
        claim_gate += " and locked_by = %s"
        params.append(str(worker_id))
    if attempt is not None:
        claim_gate += " and attempts = %s"
        params.append(int(attempt))
    with conn.transaction():
        updated = conn.execute(
            "update jobs set status = 'completed', result = %s::jsonb, error = null, "
            "locked_by = null, locked_at = null, claimed_release_sha = null, "
            "claimed_pool_id = null, updated_at = now() "
            "where id = %s and status = 'running'" + claim_gate,
            tuple(params),
        ).rowcount
    if updated == 0:
        raise JobClaimLost(f"complete rejected stale claim generation for job={job_id}")


def block(
    conn,
    job_id: str,
    *,
    reason: str,
    detail: dict[str, Any] | None = None,
    worker_id: str | None = None,
    attempt: int | None = None,
) -> None:
    """Terminal block (invariant #8): the work could not run for a NAMED reason (budget exhausted, no
    handler, missing config). Distinct from 'failed' (the work was attempted and errored)."""
    err = {"reason": reason}
    if detail:
        err["detail"] = detail
    _refresh_job_lifecycle_session(conn)
    claim_gate = ""
    params: list[Any] = [json.dumps(err), job_id]
    if worker_id is not None:
        claim_gate += " and locked_by = %s"
        params.append(str(worker_id))
    if attempt is not None:
        claim_gate += " and attempts = %s"
        params.append(int(attempt))
    with conn.transaction():
        updated = conn.execute(
            "update jobs set status = 'blocked', error = %s::jsonb, "
            "locked_by = null, locked_at = null, claimed_release_sha = null, "
            "claimed_pool_id = null, updated_at = now() "
            "where id = %s and status = 'running'" + claim_gate,
            tuple(params),
        ).rowcount
    if updated == 0:
        raise JobClaimLost(f"block rejected stale claim generation for job={job_id}")


def fail(
    conn,
    job_id: str,
    *,
    error: str,
    retryable: bool = True,
    worker_id: str | None = None,
    attempt: int | None = None,
) -> str:
    """The work was attempted and raised. If retryable and attempts remain, re-queue (back to
    'queued', lock released) for another claim; else mark 'failed'. Returns 'requeued' or 'failed'.
    The hold is released by run_one BEFORE this is called, and the stale-hold reconciliation in
    run_one releases it again (idempotent) on the next attempt, so no reservation leaks on requeue."""
    err = json.dumps({"reason": "handler_error", "error": error})
    _refresh_job_lifecycle_session(conn)
    claim_gate = ""
    params: list[Any] = [job_id]
    if worker_id is not None:
        claim_gate += " and locked_by = %s"
        params.append(str(worker_id))
    if attempt is not None:
        claim_gate += " and attempts = %s"
        params.append(int(attempt))
    with conn.transaction():
        row = conn.execute(
            "select attempts, max_attempts from jobs where id = %s and status = 'running'"
            + claim_gate
            + " for update",
            tuple(params),
        ).fetchone()
        if row is None:
            raise JobClaimLost(f"fail rejected stale claim generation for job={job_id}")
        attempts, max_attempts = int(row[0]), int(row[1])
        if retryable and attempts < max_attempts:
            conn.execute(
                "update jobs set status = 'queued', error = %s::jsonb, "
                "locked_by = null, locked_at = null, claimed_release_sha = null, "
                "claimed_pool_id = null, updated_at = now(), "
                f"{_RENEW_AFTER_LEASE_SQL} where id = %s",
                (err, job_id),
            )
            return "requeued"
        conn.execute(
            "update jobs set status = 'failed', error = %s::jsonb, "
            "locked_by = null, locked_at = null, claimed_release_sha = null, "
            "claimed_pool_id = null, updated_at = now() where id = %s",
            (err, job_id),
        )
    return "failed"


def fail_if_still_owned(
    conn,
    job_id: str,
    *,
    worker_id: str,
    attempt: int | None = None,
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
        attempt_gate = " and attempts = %s" if attempt is not None else ""
        params: tuple[Any, ...] = (
            (job_id, worker_id, int(attempt)) if attempt is not None else (job_id, worker_id)
        )
        row = conn.execute(
            "select attempts, max_attempts from jobs "
            "where id = %s and status = 'running' and locked_by = %s"
            + attempt_gate
            + " for update",
            params,
        ).fetchone()
        if row is None:
            return None
        attempts, max_attempts = int(row[0]), int(row[1])
        if retryable and attempts < max_attempts:
            conn.execute(
                "update jobs set status = 'queued', error = %s::jsonb, "
                "locked_by = null, locked_at = null, claimed_release_sha = null, "
                "claimed_pool_id = null, updated_at = now(), "
                f"{_RENEW_AFTER_LEASE_SQL} where id = %s",
                (err, job_id),
            )
            return "queued"
        conn.execute(
            "update jobs set status = 'failed', error = %s::jsonb, "
            "locked_by = null, locked_at = null, claimed_release_sha = null, "
            "claimed_pool_id = null, updated_at = now() where id = %s",
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
    local_job_ids = _live_local_job_ids()
    local_guard = " and not (id = any(%s))" if local_job_ids else ""
    local_params: tuple[Any, ...] = (local_job_ids,) if local_job_ids else ()
    # A bootstrap may be quiet while its exact delegated build owns a claim on another worker
    # thread/process. Never reclaim that parent alongside its child. The relationship is scoped to
    # parent job + attempt; a different child for the same business must not pin this generation.
    # If the child itself is stale, this sweep reclaims it first and the next sweep may recover the
    # parent safely.
    delegated_child_guard = (
        " and not (kind = 'ceo_bootstrap' and exists ("
        "select 1 from jobs child where child.business_slug = jobs.business_slug "
        "and child.id <> jobs.id "
        "and child.kind in ('claude.agent_task', 'product.surface_refresh') "
        "and child.status in ('queued', 'running') "
        "and coalesce(child.payload -> 'parent_operator_task', '{}'::jsonb) @> "
        "jsonb_build_object('task_kind', 'ceo_bootstrap', 'run_id', jobs.id, "
        "'attempt', jobs.attempts)))"
    )
    with conn.transaction():
        requeued = conn.execute(
            "update jobs set status = 'queued', locked_by = null, locked_at = null, "
            "claimed_release_sha = null, claimed_pool_id = null, updated_at = now(), "
            f"{_RENEW_AFTER_LEASE_SQL} "
            "where status = 'running' and locked_at < now() - make_interval(secs => %s) "
            "and attempts < max_attempts"
            + local_guard
            + delegated_child_guard,
            (older_than_seconds, *local_params),
        ).rowcount
        blocked = conn.execute(
            "update jobs set status = 'blocked', "
            "error = %s::jsonb, locked_by = null, locked_at = null, "
            "claimed_release_sha = null, claimed_pool_id = null, updated_at = now() "
            "where status = 'running' and locked_at < now() - make_interval(secs => %s) "
            "and attempts >= max_attempts"
            + local_guard
            + delegated_child_guard,
            (json.dumps({"reason": "stalled_max_attempts"}), older_than_seconds, *local_params),
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
    worker_release_sha: str | None = None,
) -> JobOutcome | None:
    """Claim one job and run it under the full contract; returns its outcome, or None if the queue is
    empty. The pipeline, each step its own transaction on the autocommit conn:

      1. claim          — FOR UPDATE SKIP LOCKED → 'running' (attempts++).
      2. handler lookup — no handler for the kind ⇒ blocked('no_handler'); nothing reserved.
      3. writer lease   — product jobs take only their business's DB advisory lane; other businesses
                          and non-product jobs remain parallel.
      4. reserve        — release any stale hold from a crashed prior attempt (idempotent refund),
                          then reserve estimate_cents on the OWNER's flow-A account under a per-attempt
                          key. InsufficientBalance ⇒ blocked('budget_exhausted'); nothing runs.
      5. run            — handler(job). Raises ⇒ refund the hold, then fail/requeue.
      6. settle         — clamp actual ≤ reserved, settle (releases the remainder), complete.
    """
    job = claim_one(
        conn,
        worker_id=worker_id,
        kinds=kinds,
        owner_user_id=owner_user_id,
        claim_pool_id=claim_pool_id,
        exclusive_pool=exclusive_pool,
        min_queue_age_seconds=min_queue_age_seconds,
        worker_release_sha=worker_release_sha,
    )
    if job is None:
        return None

    claimed_at = time.time()
    estimate_cents = int((job.payload or {}).get("estimate_cents", 0) or 0)
    reservation_key = f"job:{job.id}:{job.attempts}"

    def _emit_job_event(
        status: str,
        *,
        actual_cents: int = 0,
        error: str | None = None,
        extra: Mapping[str, Any] | None = None,
    ) -> None:
        """Job-level slice of the cost/log ledger (operator_cost_events, migration 0070).

        One row per terminal transition — completed / failed / blocked — with the settled cost so
        every task is queryable per business at job granularity. Best-effort by construction."""
        try:
            from . import cost_events

            payload: dict[str, Any] = {"attempts": job.attempts, "worker_id": worker_id}
            if extra:
                payload.update(dict(extra))
            cost_events.record_operator_cost_event(
                conn,
                event_kind=cost_events.KIND_JOB,
                business_slug=job.business_slug or None,
                job_id=str(job.id),
                task_kind=job.kind,
                name=job.kind,
                status=status,
                cost_microusd=max(0, int(actual_cents)) * 10_000,
                cost_status="actual" if actual_cents else None,
                reservation_key=reservation_key if estimate_cents > 0 else None,
                duration_ms=int((time.time() - claimed_at) * 1000),
                error=error,
                payload=payload,
            )
        except Exception:  # noqa: BLE001 — observability must never break the job contract
            pass

    handler = handlers.get(job.kind)
    if handler is None:
        block(
            conn,
            job.id,
            reason="no_handler",
            detail={"kind": job.kind},
            worker_id=worker_id,
            attempt=job.attempts,
        )
        _emit_job_event("blocked", error="no_handler")
        return JobOutcome(job.id, job.kind, "blocked", reason="no_handler")

    reserved = 0

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

    claim_guard = JobClaimGuard(
        job_id=str(job.id),
        worker_id=str(worker_id),
        attempt=int(job.attempts),
    )

    def _heartbeat_while_waiting_for_writer_lease() -> None:
        claim_guard.assert_owned("product-writer lease acquisition")
        hb_conn, _close_hb = _lifecycle_conn()
        heartbeat(
            hb_conn,
            job.id,
            worker_id=worker_id,
            attempt=job.attempts,
        )

    def _claim_is_recent(lifecycle_conn) -> bool:
        stale_seconds = max(60.0, float(os.getenv("TAKYON_WORKER_STALE_SECONDS") or 14_400))
        warning_window_seconds = max(300.0, float(heartbeat_interval_seconds or 0) * 20.0)
        window_seconds = min(warning_window_seconds, stale_seconds / 2.0)
        row = lifecycle_conn.execute(
            "select status, locked_by, attempts, "
            "locked_at > now() - (%s::double precision * interval '1 second') "
            ", coalesce((payload ->> 'cancel_requested')::boolean, false) "
            "from jobs where id = %s",
            (window_seconds, job.id),
        ).fetchone()
        return bool(
            row is not None
            and str(row[0]) == "running"
            and str(row[1] or "") == worker_id
            and int(row[2]) == int(job.attempts)
            and row[3]
            and not bool(row[4])
        )

    try:
        wait_timeout = (
            heartbeat_interval_seconds
            if heartbeat_interval_seconds and heartbeat_interval_seconds > 0
            else None
        )
        run_result: JobRunResult | None = None
        def _run_claimed_handler() -> JobRunResult:
            with _bound_job_claim(claim_guard):
                return handler(job)

        with _hold_product_writer_lease(
            job,
            fallback_conn=conn,
            conn_factory=heartbeat_conn_factory,
            waiting_heartbeat=_heartbeat_while_waiting_for_writer_lease,
            claim_guard=claim_guard,
        ):
            # The business-scoped writer lease precedes money reservation. A replacement attempt
            # waiting behind a still-draining predecessor therefore cannot hold budget or begin any
            # side effect. Non-product jobs pass through this context without serialization.
            if estimate_cents > 0:
                owner_user_id = _resolve_owner_user_id(conn, job.business_slug)
                if (
                    job.reserved_billing_entry_id
                    and job.reserved_billing_entry_id != reservation_key
                ):
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
                        worker_id=worker_id,
                        attempt=job.attempts,
                    )
                    _emit_job_event(
                        "blocked",
                        error=f"budget_exhausted: {exc}",
                        extra={"estimate_cents": estimate_cents},
                    )
                    _reset_lifecycle_conn()
                    return JobOutcome(
                        job.id,
                        job.kind,
                        "blocked",
                        reason="budget_exhausted",
                    )
            # Copy only after the lease ContextVar is bound; otherwise the handler thread sees an
            # empty key and the core inline guard tries to reacquire this same DB advisory lock.
            ctx = contextvars.copy_context()
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(ctx.run, _run_claimed_handler)
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
                        heartbeat(
                            hb_conn,
                            job.id,
                            worker_id=worker_id,
                            attempt=job.attempts,
                        )
                    except Exception as hb_exc:  # noqa: BLE001 — lost claim / DB blip must not requeue live work
                        if heartbeat_conn_factory is not None:
                            _reset_lifecycle_conn()
                            try:
                                hb_conn, _close_hb = _lifecycle_conn()
                                heartbeat(
                                    hb_conn,
                                    job.id,
                                    worker_id=worker_id,
                                    attempt=job.attempts,
                                )
                                continue
                            except Exception:
                                pass
                        try:
                            _reset_lifecycle_conn()
                            probe_conn, _close_probe = _lifecycle_conn()
                            if _claim_is_recent(probe_conn):
                                continue
                            claim_guard.mark_lost(
                                "heartbeat probe confirmed that a newer/terminal claim owns the row"
                            )
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
                status = fail(
                    lifecycle_conn,
                    job.id,
                    error=str(exc),
                    retryable=not isinstance(exc, JobClaimLost),
                    worker_id=worker_id,
                    attempt=job.attempts,
                )
            except JobNotRunning:
                claim_guard.mark_lost("terminal failure transition rejected stale claim")
                repaired_status = fail_if_still_owned(
                    lifecycle_conn,
                    job.id,
                    worker_id=worker_id,
                    attempt=job.attempts,
                    error=str(exc),
                    retryable=not isinstance(exc, JobClaimLost),
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
        _emit_job_event(status, error=str(exc), extra={"reserved_cents": reserved})
        return JobOutcome(
            job.id, job.kind, status, reserved_cents=reserved, reason="handler_error"
        )

    # The handler returned successfully, but success is authoritative only while THIS exact attempt
    # still owns the claim. Refresh/verify before money settlement so an obsolete attempt cannot charge
    # and cannot report completion through a sibling's newer generation.
    actual = 0
    lifecycle_conn, close_lifecycle = _lifecycle_conn()
    try:
        claim_guard.assert_owned("job settlement")
        heartbeat(
            lifecycle_conn,
            job.id,
            worker_id=worker_id,
            attempt=job.attempts,
        )
        if estimate_cents > 0:
            actual = max(0, min(int(run_result.actual_cost_cents or 0), reserved))
            billing.settle(lifecycle_conn, reservation_key, actual)
        complete(
            lifecycle_conn,
            job.id,
            result=run_result.result,
            worker_id=worker_id,
            attempt=job.attempts,
        )
    except JobNotRunning:
        # Our generation was superseded before authoritative settlement/finalization. Never mutate
        # the newer row and never claim completion; release this attempt's hold idempotently.
        if estimate_cents > 0:
            try:
                billing.refund(lifecycle_conn, reservation_key)
            except Exception:  # noqa: BLE001 — best-effort; the sibling attempt reconciles the hold too
                pass
        claim_guard.mark_lost("terminal completion transition rejected stale claim")
        _log.warning(
            "jobs: job %s (kind=%s) handler returned after losing attempt %s; refusing to "
            "finalize or report completion",
            job.id,
            job.kind,
            job.attempts,
        )
        _emit_job_event(
            "failed",
            error="lost exact job claim before finalize",
            extra={"reserved_cents": reserved, "lost_claim": True},
        )
        return JobOutcome(
            job.id,
            job.kind,
            "failed",
            reserved_cents=reserved,
            actual_cents=0,
            reason="lost_claim",
        )
    finally:
        _close_lifecycle_conn(lifecycle_conn, close_lifecycle)
    _emit_job_event(
        "completed",
        actual_cents=actual,
        extra={"reserved_cents": reserved},
    )
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
