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
    ) -> None:
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
        """The interactive shell's inline single-claim lane (size=1, no wake dispatch)."""
        return cls(
            worker_id=worker_id or f"cli-wake-{os.getpid()}",
            size=1,
            dispatch=False,
            kinds=kinds,
            handlers=handlers,
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
            dispatch=self.dispatch if dispatch is None else dispatch,
            stop=stop,
            max_jobs=self.max_jobs if max_jobs is None else max_jobs,
            heartbeat_conn_factory=heartbeat_conn_factory,
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

        stop = threading.Event()

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

        def _run_loop(*, thread_worker_id: str, allow_dispatch: bool) -> int:
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
                        kinds=self.kinds,
                        owner_user_id=self.owner_user_id,
                        dispatch=allow_dispatch,
                        stop=stop,
                        max_jobs=max_jobs,
                        heartbeat_conn_factory=_heartbeat_conn_factory,
                    )
                    total_drained += counts["drained"]
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
        if concurrency == 1:
            return _run_loop(thread_worker_id=worker_id, allow_dispatch=self.dispatch)

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
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

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
