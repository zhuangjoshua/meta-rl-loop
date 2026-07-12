"""WorkerPool — the one worker constructor (modularization plan Stage 1, §2.4).

Every compute lane that drains the Postgres job queue converges here instead of
open-coding its own loop:

  1. ``takyon worker`` (``takyon_cli/main.py cmd_worker``)      -> ``WorkerPool.local_threads(...)``
  2. dashboard embedded drain (``takyon_cli/web_server.py``)    -> ``WorkerPool.embedded(...)``
  3. interactive shell inline wake (``plugins/takyon/cli.py``)  -> ``WorkerPool.inline(...)``
  4. the dashboard operator turn (``tui_gateway.isolated_turn_worker`` detached subprocess)
     is the fourth, session-owned lane: it executes ONE turn, not a queue drain, so it does
     not construct a pool. Stage 3 threads RuntimeContext through its spawn payload (plan R4).

Size, dispatcher role, and identity are constructor arguments (they were an env read, a
thread-index trick, and a formatted hostname+pid string). Handlers are injected — the
module-level ``worker.HANDLERS`` dict is just the default map the composition root passes in.
The drain/claim/execute semantics are UNCHANGED: ``run()`` is ``worker.run_worker_loop``'s
body lifted verbatim, and every tick still goes through ``worker.drain_tick`` /
``jobs.run_one`` (budget reserve→settle→release untouched, per the plan's escape-hatch rule).

Stage 2 adds ``ClaimScope`` here (durable session ownership of jobs); it is deliberately NOT
a constructor argument yet — nothing lands unwired.
"""

from __future__ import annotations

import logging
import os
import socket
import threading
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from . import jobs

_log = logging.getLogger("takyon.worker_pool")

# Handler registry type: kind -> handler. The default is worker.HANDLERS (resolved lazily to
# keep this module a leaf — worker.py imports us for the run_worker_loop delegate).
HandlerRegistry = Mapping[str, "jobs.Handler"]


def default_handlers() -> HandlerRegistry:
    from . import worker

    return worker.HANDLERS


class WorkerPool:
    """One drainable pool of worker threads over the shared Postgres job queue.

    Construct via the lane factories (``local_threads`` / ``embedded`` / ``inline``) so each
    launch path's topology is explicit and greppable.
    """

    def __init__(
        self,
        *,
        worker_id: str | None = None,
        size: int | None = None,
        dispatch: bool = True,
        kinds: Sequence[str] | None = None,
        owner_user_id: str | None = None,
        poll_interval: float | None = None,
        once: bool = False,
        max_jobs: int | None = None,
        database_url: str | None = None,
        handlers: HandlerRegistry | None = None,
        pool_id: str | None = None,
        exclusive: bool = False,
        register: bool = True,
    ) -> None:
        from . import claim_scope as _cs

        self.worker_id = worker_id or f"worker-{socket.gethostname()}-{os.getpid()}"
        self._explicit_size = size
        self.dispatch = dispatch
        self.kinds = [str(k).strip() for k in (kinds or []) if str(k).strip()] or None
        self.owner_user_id = owner_user_id
        self.poll_interval = poll_interval
        self.once = once
        self.max_jobs = max_jobs
        self.database_url = database_url
        self._handlers = handlers
        # Pool identity (Stage 2, UC1): the registry row this pool heartbeats and the identity
        # its claims present to the reservation predicate. Precedence: explicit pool_id arg,
        # then an EXPLICIT worker_id (a lane that names its worker owns that identity — e.g.
        # `worker --once` inside a console session must NOT adopt, register as, and later
        # decommission the session's pool row), then the session env the operator-prod script
        # exports, then this pool's own worker id.
        env_pool = str(os.getenv(_cs.POOL_ID_ENV) or "").strip()
        explicit_worker = str(worker_id or "").strip()
        self.pool_id = (
            str(pool_id or "").strip() or explicit_worker or env_pool or self.worker_id
        )
        env_exclusive = str(os.getenv(_cs.POOL_EXCLUSIVE_ENV) or "").strip().lower() in {
            "1", "true", "yes", "on",
        }
        # Env-declared exclusivity binds only the pool the env actually names — a differently
        # named lane in the same shell (worker --once) must not become an exclusive pool with
        # an empty reservation set (it would claim nothing at all).
        self.exclusive = bool(exclusive) or (env_exclusive and self.pool_id == env_pool and bool(env_pool))
        self.register = register
        self.release_sha = _cs.runtime_release_sha()
        self.pool_lease_seconds = max(
            120.0, float(os.getenv("TAKYON_WORKER_POOL_LEASE_SECONDS") or 300.0)
        )

    # ── lane factories (the four compute paths, plan A.4) ─────────────────────────────

    @classmethod
    def local_threads(
        cls,
        *,
        worker_id: str | None = None,
        poll_interval: float | None = None,
        dispatch: bool = True,
        kinds: Sequence[str] | None = None,
        owner_user_id: str | None = None,
        once: bool = False,
        max_jobs: int | None = None,
        database_url: str | None = None,
        handlers: HandlerRegistry | None = None,
    ) -> "WorkerPool":
        """The ``takyon worker`` process lane (VPS service, operator-prod Mac workers)."""
        return cls(
            worker_id=worker_id,
            poll_interval=poll_interval,
            dispatch=dispatch,
            kinds=kinds,
            owner_user_id=owner_user_id,
            once=once,
            max_jobs=max_jobs,
            database_url=database_url,
            handlers=handlers,
        )

    @classmethod
    def embedded(
        cls,
        *,
        worker_id: str | None = None,
        poll_interval: float | None = None,
        handlers: HandlerRegistry | None = None,
    ) -> "WorkerPool":
        """The dashboard's opt-in in-process drain (``TAKYON_DASHBOARD_EMBEDDED_WORKER=1``)."""
        return cls(
            worker_id=worker_id or f"dashboard-worker-{os.getpid()}",
            poll_interval=poll_interval,
            dispatch=True,
            handlers=handlers,
        )

    @classmethod
    def inline(
        cls,
        *,
        worker_id: str | None = None,
        kinds: Sequence[str] | None = None,
        handlers: HandlerRegistry | None = None,
    ) -> "WorkerPool":
        """The interactive shell's inline single-claim lane (size=1, no wake dispatch). It
        presents the SESSION pool identity (when one is declared) so it can claim the
        session-reserved wakes the shell itself enqueues; it never registers a pool row
        (the session's worker pool owns that lease)."""
        from . import claim_scope as _cs

        session_pool = str(os.getenv(_cs.POOL_ID_ENV) or "").strip() or None
        return cls(
            worker_id=worker_id or f"cli-wake-{os.getpid()}",
            size=1,
            dispatch=False,
            kinds=kinds,
            handlers=handlers,
            pool_id=session_pool,
            register=False,
        )

    # ── topology ───────────────────────────────────────────────────────────────────────

    @property
    def handlers(self) -> HandlerRegistry:
        return self._handlers if self._handlers is not None else default_handlers()

    @property
    def size(self) -> int:
        """Thread count. Constructor arg wins; the env read is the compat default (Stage 3
        replaces it with RuntimeContext.worker)."""
        if self.once or self.max_jobs is not None:
            return 1
        if self._explicit_size is not None:
            return max(1, int(self._explicit_size))
        return max(1, _env_int("TAKYON_WORKER_CONCURRENCY", 2))

    def thread_worker_id(self, index: int) -> str:
        """Stable per-thread identity: thread 0 of a size-1 pool keeps the bare id (commit
        6bc61762's base-prefix contract); multi-thread pools suffix ``-N``."""
        if self.size == 1:
            return self.worker_id
        return f"{self.worker_id}-{index + 1}"

    # ── drain primitives (delegate to the tested worker/jobs engine, verbatim) ─────────

    def drain_once(
        self,
        conn,
        *,
        stop: threading.Event | None = None,
        max_jobs: int | None = None,
        dispatch: bool | None = None,
        heartbeat_conn_factory: Callable[[], Any] | None = None,
    ) -> dict[str, int]:
        """One drain tick on a caller-owned connection (== ``worker.drain_tick``)."""
        from . import worker

        return worker.drain_tick(
            conn,
            worker_id=self.worker_id,
            handlers=self.handlers,
            kinds=self.kinds,
            owner_user_id=self.owner_user_id,
            claim_pool_id=self.pool_id,
            exclusive_pool=self.exclusive,
            dispatch=self.dispatch if dispatch is None else dispatch,
            stop=stop,
            max_jobs=self.max_jobs if max_jobs is None else max_jobs,
            heartbeat_conn_factory=heartbeat_conn_factory,
            worker_release_sha=self.release_sha,
        )

    def run_one_inline(self, conn) -> "jobs.JobOutcome | None":
        """Claim-and-run a single job on a caller-owned connection (the shell's bounded
        targeted loop — it polls a SPECIFIC job to completion, so it must not drain the
        whole queue the way ``drain_once`` does)."""
        return jobs.run_one(
            conn,
            worker_id=self.worker_id,
            handlers=self.handlers,
            kinds=self.kinds,
            owner_user_id=self.owner_user_id,
            claim_pool_id=self.pool_id,
            exclusive_pool=False,
            worker_release_sha=self.release_sha,
        )

    # ── the process shell (body lifted verbatim from worker.run_worker_loop) ───────────

    def run(self) -> int:
        """Run the pool until SIGTERM/SIGINT (or ``once``/``max_jobs``). Opens a fresh
        per-tick psycopg connection (autocommit, ``prepare_threshold=None`` — the SAME
        pgbouncer-safe settings as ``runtime_app``) so a dropped connection only costs one
        tick; reconnects next tick. A SIGTERM stops pulling NEW jobs between jobs and exits
        cleanly — a job killed mid-turn is left 'running' and reclaimed by ``requeue_stale``
        on the next worker (its reservation refunded). Returns total jobs drained."""
        import psycopg

        from . import claim_scope as _cs
        from .core import load_takyon_env
        from .runtime_app import (
            assert_takyon_pg_role,
            configure_takyon_pg_session,
            resolve_database_url,
        )

        load_takyon_env()
        # Mark this process as the worker plane: core's worker-deferral dispatcher must run
        # tools INLINE here (the surrounding job is already durable; deferring again would
        # starve the drain threads waiting on their own sub-jobs). Stage 3 replaces this env
        # write with RuntimeContext.is_worker_process (plan A.1).
        os.environ["TAKYON_WORKER_PROCESS"] = "1"
        database_url = self.database_url
        resolved_url = resolve_database_url(
            database_url,
            plane=None if database_url else "operator",
        )  # invariant #8: raises if unconfigured
        worker_id = self.worker_id
        interval = (
            self.poll_interval
            if self.poll_interval is not None
            else _env_float("TAKYON_WORKER_POLL_SECONDS", _default_poll_seconds())
        )
        concurrency = self.size
        once = self.once
        max_jobs = self.max_jobs
        ready_file = str(os.getenv("TAKYON_WORKER_READY_FILE") or "").strip()
        if ready_file and not self.register:
            raise RuntimeError(
                "TAKYON_WORKER_READY_FILE requires a registered worker pool"
            )

        stop = threading.Event()

        def _pool_conn():
            conn = psycopg.connect(resolved_url, autocommit=True, prepare_threshold=None)
            if not database_url:
                assert_takyon_pg_role(conn, "operator")
                configure_takyon_pg_session(conn, bypass=True)
            return conn

        release_identity_failed = threading.Event()

        def _stop_invalid_release(exc: _cs.LocalReleaseIdentityError) -> None:
            if release_identity_failed.is_set():
                return
            release_identity_failed.set()
            stop.set()
            _log.error(
                "worker[%s]: release identity invalid (%s); draining without new claims",
                worker_id,
                exc,
            )
            if ready_file:
                try:
                    Path(ready_file).unlink(missing_ok=True)
                except OSError:
                    pass
            if self.register:
                try:
                    conn = _pool_conn()
                    try:
                        _cs.begin_drain(conn, self.pool_id)
                    finally:
                        conn.close()
                except Exception as drain_exc:  # noqa: BLE001
                    _log.warning("worker[%s]: pool drain mark failed: %s", worker_id, drain_exc)

        # ── pool registry lifecycle (Stage 2, UC1) ───────────────────────────────────
        # Register this pool's heartbeated lease row; a daemon thread renews it on its own
        # connection so a long-running handler never lets the lease lapse (a lapsed lease
        # spills the pool's STRICT reservations to other workers — correct when the pool is
        # dead, wrong when it is merely busy). A normal daemon may self-heal an initial
        # registration failure through the heartbeat loop. A console preflight is stricter:
        # it must not open an operator shell until this exact worker owns a live registry row.
        registered = False
        if self.register:
            try:
                conn = _pool_conn()
                try:
                    _cs.register_pool(
                        conn,
                        pool_id=self.pool_id,
                        owner_user_id=self.owner_user_id,
                        exclusive=self.exclusive,
                        concurrency=concurrency,
                        lease_seconds=self.pool_lease_seconds,
                        release_sha=self.release_sha,
                    )
                    registered = True
                finally:
                    conn.close()
            except _cs.LocalReleaseIdentityError as exc:
                _stop_invalid_release(exc)
                if ready_file:
                    raise
            except Exception as exc:  # noqa: BLE001 — daemon self-heals; preflight fails closed
                _log.error(
                    "worker[%s]: pool registration failed (%s); strict reservations for pool "
                    "%s will spill as if the pool were down until the registry row exists "
                    "(heartbeat loop keeps retrying registration)",
                    worker_id,
                    exc,
                    self.pool_id,
                )
                if ready_file:
                    raise RuntimeError(
                        f"worker pool {self.pool_id} did not register; refusing readiness"
                    ) from exc

        if ready_file:
            if not registered:
                raise RuntimeError(
                    f"worker pool {self.pool_id} is not registered; refusing readiness"
                )
            marker = Path(ready_file)
            marker.parent.mkdir(parents=True, exist_ok=True)
            temporary = marker.with_name(
                f".{marker.name}.{os.getpid()}.{threading.get_ident()}.tmp"
            )
            temporary.write_text(
                f"worker_id={worker_id}\npool_id={self.pool_id}\n",
                encoding="utf-8",
            )
            os.replace(temporary, marker)

        def _pool_heartbeat_loop() -> None:
            interval = max(15.0, self.pool_lease_seconds / 4.0)
            drained_marked = False
            while not stop.wait(interval):
                try:
                    _cs.require_local_release_sha(self.release_sha, field="release_sha")
                    conn = _pool_conn()
                    try:
                        if not _cs.heartbeat_pool(
                            conn, self.pool_id, lease_seconds=self.pool_lease_seconds
                        ) and not stop.is_set():
                            _cs.register_pool(
                                conn,
                                pool_id=self.pool_id,
                                owner_user_id=self.owner_user_id,
                                exclusive=self.exclusive,
                                concurrency=concurrency,
                                lease_seconds=self.pool_lease_seconds,
                                release_sha=self.release_sha,
                            )
                    finally:
                        conn.close()
                except _cs.LocalReleaseIdentityError as exc:
                    _stop_invalid_release(exc)
                    break
                except Exception as exc:  # noqa: BLE001 — transient DB outage must not kill the pool
                    _log.warning("worker[%s]: pool heartbeat failed: %s", worker_id, exc)
            # Stop requested: mark draining (in-flight work finishes; reservations held).
            if not drained_marked:
                try:
                    conn = _pool_conn()
                    try:
                        from . import claim_scope as _cs2

                        _cs2.begin_drain(conn, self.pool_id)
                    finally:
                        conn.close()
                except Exception:  # noqa: BLE001
                    pass

        pool_heartbeat_thread: threading.Thread | None = None
        if self.register:
            pool_heartbeat_thread = threading.Thread(
                target=_pool_heartbeat_loop, name="takyon-pool-heartbeat", daemon=True
            )
            pool_heartbeat_thread.start()

        def _request_stop(signum, _frame):
            _log.info(
                "worker[%s]: signal %s received; finishing current job then stopping",
                worker_id,
                signum,
            )
            stop.set()

        import signal

        for _sig in (signal.SIGTERM, signal.SIGINT):
            try:
                signal.signal(_sig, _request_stop)
            except (ValueError, OSError):
                # Not on the main thread (e.g. under a test harness) — skip signal install.
                pass

        def _run_loop(*, thread_worker_id: str, allow_dispatch: bool, kinds_override: Sequence[str] | None = None, min_queue_age_override: float | None = None) -> int:
            import psycopg

            def _heartbeat_conn_factory():
                hb_conn = psycopg.connect(resolved_url, autocommit=True, prepare_threshold=None)
                if not database_url:
                    assert_takyon_pg_role(hb_conn, "operator")
                    configure_takyon_pg_session(hb_conn, bypass=True)
                return hb_conn

            total_drained = 0
            while not stop.is_set():
                conn = None
                try:
                    conn = psycopg.connect(resolved_url, autocommit=True, prepare_threshold=None)
                    if not database_url:
                        assert_takyon_pg_role(conn, "operator")
                        configure_takyon_pg_session(conn, bypass=True)
                except Exception as exc:  # noqa: BLE001 — transient DB outage must not crash the daemon
                    if conn is not None:
                        try:
                            conn.close()
                        except Exception:
                            pass
                    _log.warning(
                        "worker[%s]: DB connect failed (%s); retrying in %.0fs",
                        thread_worker_id,
                        exc,
                        interval,
                    )
                    stop.wait(interval)
                    continue
                try:
                    from . import worker

                    counts = worker.drain_tick(
                        conn,
                        worker_id=thread_worker_id,
                        handlers=self.handlers,
                        kinds=kinds_override if kinds_override is not None else self.kinds,
                        min_queue_age_seconds=min_queue_age_override,
                        owner_user_id=self.owner_user_id,
                        claim_pool_id=self.pool_id,
                        exclusive_pool=self.exclusive,
                        dispatch=allow_dispatch,
                        stop=stop,
                        max_jobs=max_jobs,
                        heartbeat_conn_factory=_heartbeat_conn_factory,
                        worker_release_sha=self.release_sha,
                    )
                    total_drained += counts["drained"]
                except _cs.LocalReleaseIdentityError as exc:
                    _stop_invalid_release(exc)
                except Exception as exc:  # noqa: BLE001 — a tick failure must not crash the daemon
                    _log.exception("worker[%s]: tick failed: %s", thread_worker_id, exc)
                finally:
                    conn.close()

                if once or (max_jobs is not None and total_drained >= max_jobs):
                    break
                stop.wait(interval)
            _log.info(
                "worker[%s]: stopped (drained %d job(s) this run)", thread_worker_id, total_drained
            )
            return total_drained

        _log.info(
            "worker[%s]: starting (dispatch=%s poll=%.0fs concurrency=%d owner=%s)",
            worker_id,
            self.dispatch,
            interval,
            concurrency,
            str(self.owner_user_id or "").strip() or "*",
        )
        def _decommission() -> None:
            if not self.register:
                return
            try:
                conn = _pool_conn()
                try:
                    from . import claim_scope as _cs

                    _cs.decommission_pool(conn, self.pool_id)
                finally:
                    conn.close()
            except Exception as exc:  # noqa: BLE001
                _log.warning("worker[%s]: pool decommission failed: %s", worker_id, exc)

        # Dedicated operator-task lane (fire-and-continue enabler): a CEO turn occupies its drain
        # thread for the WHOLE bootstrap/wake, so a claude.agent_task / product.surface_refresh job
        # it fires with wait_ms:0 would otherwise queue behind the turn itself on a small pool —
        # a livelock, not overlap. One extra kinds-scoped daemon thread claims exactly those job
        # kinds; it spends its life blocked on the brokered docker build + provider calls, so it
        # adds no meaningful CPU. Full-service pools only (never --once/--max-jobs/kinds-scoped
        # pools), TAKYON_WORKER_OPERATOR_TASK_LANE=0 disables.
        operator_task_lane = (
            self.kinds is None
            and not once
            and max_jobs is None
            and str(os.getenv("TAKYON_WORKER_OPERATOR_TASK_LANE", "1")).strip().lower()
            not in {"0", "false", "no", "off"}
        )

        operator_task_lane_thread: threading.Thread | None = None

        def _spawn_operator_task_lane() -> threading.Thread | None:
            nonlocal operator_task_lane_thread
            if not operator_task_lane:
                return None
            if operator_task_lane_thread is not None:
                return operator_task_lane_thread
            lane = threading.Thread(
                target=lambda: _run_loop(
                    thread_worker_id=f"{worker_id}-optask",
                    allow_dispatch=False,
                    # Every worker-executed sub-job kind a CEO turn can fire-and-wait on. X
                    # publishes are worker-backed too: without this lane they queue behind the
                    # CEO's own turn on a concurrency-1 pool, so every launch-post call hits its
                    # wait bound and the post publishes only after the turn ends (observed live).
                    kinds_override=("claude.agent_task", "product.surface_refresh", "x.publish_outreach"),
                    # Sub-jobs are fired BY a turn already running here; the Mac-first queue-age
                    # delay only stalls that turn's own wait (measured: every launch post aged
                    # 180s before the lane could claim it). Claim immediately.
                    min_queue_age_override=0.0,
                ),
                name="takyon-worker-optask",
                daemon=True,
            )
            lane.start()
            operator_task_lane_thread = lane
            return lane

        def _join_operator_task_lane() -> None:
            # The lane owns the long product/Taste jobs most likely to still be running when a
            # deploy asks the worker to drain. It may be a daemon for crash semantics, but graceful
            # process shutdown must join it so systemd never sees the main loop exit and kills the
            # child task while it is still committing its exact attempt.
            if operator_task_lane_thread is not None:
                operator_task_lane_thread.join()

        if concurrency == 1:
            try:
                _spawn_operator_task_lane()
                return _run_loop(thread_worker_id=worker_id, allow_dispatch=self.dispatch)
            finally:
                stop.set()
                _join_operator_task_lane()
                _decommission()

        totals = [0 for _ in range(concurrency)]
        errors: list[BaseException] = []

        def _thread_main(index: int) -> None:
            thread_worker_id = self.thread_worker_id(index)
            try:
                totals[index] = _run_loop(
                    thread_worker_id=thread_worker_id,
                    # Dispatcher role is explicit pool topology: exactly one dispatching
                    # thread (was the `index == 0` trick).
                    allow_dispatch=self.dispatch and index == 0,
                )
            except BaseException as exc:  # pragma: no cover - defensive last resort
                errors.append(exc)
                stop.set()

        threads = [
            threading.Thread(
                target=_thread_main,
                args=(index,),
                name=f"takyon-worker-{index + 1}",
                daemon=True,
            )
            for index in range(concurrency)
        ]
        _spawn_operator_task_lane()
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        _join_operator_task_lane()

        _decommission()
        if errors:
            raise errors[0]
        return sum(totals)


def _default_poll_seconds() -> float:
    from . import worker

    return worker._DEFAULT_POLL_SECONDS


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except (TypeError, ValueError):
        _log.warning("worker_pool: invalid %s=%r; using default %.0f", name, raw, default)
        return default


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except (TypeError, ValueError):
        _log.warning("worker_pool: invalid %s=%r; using default %d", name, raw, default)
        return default
