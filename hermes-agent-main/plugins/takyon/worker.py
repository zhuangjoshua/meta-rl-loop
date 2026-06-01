"""Postgres-native worker-drain plane — the process that turns queued ``jobs`` rows into real work.

This is the DRAIN half of the Phase-6 worker plane; the enqueue/schedule halves live in ``jobs.py``
(the at-least-once queue + budget-gated ``run_one`` cycle) and ``wakes.py`` (the ``wake_schedules``
cursor + in-DB ``dispatch_due_wakes()``). One long-lived process per deployment ties them together,
each loop tick:

  1. self-dispatches due CEO wakes (``wakes.dispatch_due_wakes``) — so pg_cron is OPTIONAL: a host
     running this worker needs no external scheduler to fire recurring wakes;
  2. reclaims stale claims left by a crashed prior worker (``jobs.requeue_stale``);
  3. drains the queue one job at a time through ``jobs.run_one`` — which keeps the FULL contract:
     ``FOR UPDATE SKIP LOCKED`` claim → flow-A reserve → handler → settle/refund, at-least-once, and
     never a fake ``completed`` (a partial/failed turn is ``blocked``/``failed``, invariant #8);
  4. dispatches each job KIND to its handler. Today the only handler is ``ceo_wake`` — a scheduled
     CEO turn, the Postgres-native replacement for the legacy file-cron ``takyon-ceo:<slug>`` job.

Money: ``run_one`` only reserves/settles when the job payload carries ``estimate_cents`` (>0), which
rides onto the job from ``wake_schedules.payload``. The ``ceo_wake`` handler therefore ALWAYS reports
the turn's true model cost (from the agent's own usage accounting) as ``actual_cost_cents`` so the
settle is correct whenever an estimate was reserved; with no estimate the turn runs unmetered and the
reported cost is simply ignored by ``run_one``. No second money path — this reuses flow-A unchanged.

Invariant #8 (no silent fallback): starting the loop with no DATABASE_URL configured raises loudly
via ``resolve_database_url`` — it never half-starts against a phantom queue, and never quietly falls
back to SQLite (there is no SQLite worker; jobs/wakes are Postgres-only).

INERT until deliberately run: importing this module starts nothing, and the tracked
``deploy/argon-alpha-14/takyon-worker.service`` exists but is NOT enabled on the VPS. Recurring wake
execution stays on the legacy file-cron until a host runs ``takyon-cli worker`` (or the unit is
enabled). Activation is a separate, operator-gated step.
"""

from __future__ import annotations

import logging
import os
import socket
import threading
from typing import Mapping

from . import jobs, wakes
from .jobs import Job, JobOutcome, JobRunResult

_log = logging.getLogger("takyon.worker")

# Default tool-iteration ceiling for a single wake turn when the schedule payload does not pin one.
_DEFAULT_MAX_TURNS = 30
# Inactivity (not wall-clock) timeout for one CEO turn: a turn may run for a long time while it is
# actively calling tools / streaming, but a hung API call or stuck tool with NO activity for this
# many seconds is interrupted and the job fails (then retries / requeues). 0 disables the guard.
_DEFAULT_TURN_TIMEOUT = 600.0
# Default queue poll cadence when a tick drains nothing. Drain itself is tight (run_one in a loop).
_DEFAULT_POLL_SECONDS = 15.0
# Reclaim claims older than this from a crashed worker (matches jobs.requeue_stale's own default).
_STALE_SECONDS = 900


# ── the ceo_wake handler ────────────────────────────────────────────────────────────────────────


def _run_ceo_turn(
    *,
    slug: str,
    system_prompt: str,
    user_prompt: str,
    toolsets: list[str],
    max_turns: int,
    inactivity_limit: float,
) -> tuple[str, float, str]:
    """Run ONE CEO wake turn for ``business:<slug>`` and return ``(final_response, cost_usd,
    cost_status)``.

    Built to be the SAME CEO the interactive shell runs (``cli._run_agent``): the stable
    ``prompts/ceo.md`` as the ephemeral system prompt, the per-business wake instructions
    (``core._ceo_cron_prompt``) as the user turn, the wake toolsets (``core._ceo_cron_toolsets``),
    and the model/provider resolved the same way (``cli._require_agent_model_config`` — which raises
    loudly if unconfigured, invariant #8). The difference vs. the shell path is purely operational:
    no interactive operator-envelope wrapping, a daemon-grade inactivity timeout (mirrors
    ``cron/scheduler.py``), and the turn's true cost extracted for billing settlement.

    Raises on a failed/aborted turn (so ``jobs.run_one`` refunds the reservation and fails/requeues
    rather than recording a fake completion)."""
    import concurrent.futures
    import contextvars

    from takyon_cli.runtime_provider import resolve_runtime_provider

    from .cli import _read_model_config, _require_agent_model_config
    from .core import TakyonStore, load_takyon_env
    from .operator_gateway import build_operator_gateway_agent

    load_takyon_env()
    model_config = _read_model_config(TakyonStore())
    resolved_model = _require_agent_model_config(model_config)  # raises TakyonError if missing
    provider = model_config.get("provider", "")
    runtime = resolve_runtime_provider(
        requested=provider or None,
        target_model=resolved_model,
    )
    agent = build_operator_gateway_agent(
        runtime=runtime,
        model=resolved_model,
        operator_user_id=_business_owner_user_id(slug),
        business_slug=slug,
        agent_kwargs={
            "max_iterations": max_turns,
            "enabled_toolsets": list(toolsets),
            # Same suppressions as the interactive CEO turn: no cron/messaging/clarify side channels,
            # no memory writes (a wake must not corrupt user representations), no shell-only toolsets.
            "disabled_toolsets": [
                "cronjob",
                "messaging",
                "clarify",
                "memory",
                "session_search",
                "terminal",
                "file",
                "browser",
                "code_execution",
            ],
            "ephemeral_system_prompt": system_prompt,
            "load_soul_identity": False,
            "skip_memory": True,
            "skip_context_files": True,
            "platform": "takyon",
            "quiet_mode": True,
        },
    )
    agent._memory_nudge_interval = 0
    agent._skill_nudge_interval = 0
    agent.suppress_status_output = True

    # Run on a worker thread and watch the agent's own activity tracker, so a hung turn is caught
    # without killing a healthy long-running one. (Mirrors cron/scheduler.py's inactivity guard.)
    limit = inactivity_limit if inactivity_limit and inactivity_limit > 0 else None
    pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    ctx = contextvars.copy_context()
    future = pool.submit(ctx.run, agent.run_conversation, user_prompt)
    timed_out = False
    try:
        if limit is None:
            result = future.result()
        else:
            result = None
            while True:
                done, _ = concurrent.futures.wait({future}, timeout=5.0)
                if done:
                    result = future.result()
                    break
                idle = 0.0
                if hasattr(agent, "get_activity_summary"):
                    try:
                        idle = float(agent.get_activity_summary().get("seconds_since_activity", 0.0))
                    except Exception:
                        idle = 0.0
                if idle >= limit:
                    timed_out = True
                    break
    finally:
        pool.shutdown(wait=False, cancel_futures=True)

    if timed_out:
        if hasattr(agent, "interrupt"):
            agent.interrupt("CEO wake timed out (inactivity)")
        raise TimeoutError(
            f"CEO wake for business:{slug} idle past {int(limit)}s inactivity limit"
        )

    if not isinstance(result, dict):
        raise RuntimeError(
            f"agent.run_conversation returned {type(result).__name__} instead of dict for "
            f"business:{slug}"
        )
    # A turn that reported failure must NOT be billed or marked completed — raise so run_one
    # refunds and fails/requeues (invariant #8).
    if result.get("failed") is True or result.get("completed") is False:
        raise RuntimeError(
            str(result.get("error") or (result.get("final_response") or "").strip() or "CEO wake reported failure")
        )

    final_response = str(result.get("final_response") or "")
    cost_usd = float(getattr(agent, "session_estimated_cost_usd", 0.0) or 0.0)
    cost_status = str(getattr(agent, "session_cost_status", "unknown") or "unknown")
    return final_response, cost_usd, cost_status


def _business_owner_user_id(slug: str) -> str:
    from .core import TakyonStore

    store = TakyonStore()
    with store._connect() as conn:
        business = store._ensure_business(conn, slug)
    return str(business.get("owner_user_id") or "").strip()


def ceo_wake_handler(job: Job) -> JobRunResult:
    """Handle a ``ceo_wake`` job: run the scheduled CEO turn for ``job.business_slug`` and report its
    true model cost as ``actual_cost_cents`` for flow-A settlement.

    The wake prompt and toolsets come from the canonical source (``core._ceo_cron_prompt`` /
    ``_ceo_cron_toolsets``) so this never drifts from the legacy/cron wake instructions; the system
    prompt is the stable ``prompts/ceo.md`` via ``cli._load_ceo_prompt``."""
    from gateway.session_context import clear_session_vars, set_session_vars

    from .cli import _business_workspace_execution_context, _load_ceo_prompt
    from .core import TakyonStore

    slug = job.business_slug
    store = TakyonStore()
    user_prompt = store._ceo_cron_prompt(slug)
    toolsets = store._ceo_cron_toolsets()
    system_prompt = _load_ceo_prompt()
    owner_user_id = _business_owner_user_id(slug)

    payload = job.payload or {}
    try:
        max_turns = int(payload.get("max_turns") or _DEFAULT_MAX_TURNS)
    except (TypeError, ValueError):
        max_turns = _DEFAULT_MAX_TURNS
    inactivity_limit = _env_float("TAKYON_WORKER_TURN_TIMEOUT", _DEFAULT_TURN_TIMEOUT)

    tokens: list[object] = []
    try:
        with _business_workspace_execution_context(slug, operator_user_id=owner_user_id) as workspace_home:
            tokens = set_session_vars(
                user_id=owner_user_id,
                workspace_root=str(workspace_home or ""),
                business_slug=slug,
            )
            final_response, cost_usd, cost_status = _run_ceo_turn(
                slug=slug,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                toolsets=toolsets,
                max_turns=max_turns,
                inactivity_limit=inactivity_limit,
            )
    finally:
        if tokens:
            clear_session_vars(tokens)
    cents = max(0, int(round(cost_usd * 100)))
    return JobRunResult(
        result={
            "business_slug": slug,
            "final_response": final_response[:4000],
            "cost_usd": round(cost_usd, 6),
            "cost_status": cost_status,
        },
        actual_cost_cents=cents,
    )


# The kind→handler registry the drain consults. New job kinds register here.
HANDLERS: dict[str, jobs.Handler] = {
    "ceo_wake": ceo_wake_handler,
}


# ── the drain loop ──────────────────────────────────────────────────────────────────────────────


def drain_tick(
    conn,
    *,
    worker_id: str,
    handlers: Mapping[str, jobs.Handler] | None = None,
    kinds: list[str] | tuple[str, ...] | None = None,
    dispatch: bool = True,
    stop: threading.Event | None = None,
    max_jobs: int | None = None,
) -> dict[str, int]:
    """One drain tick on an open autocommit connection: optionally dispatch due wakes, reclaim stale
    claims, then drain queued jobs through ``jobs.run_one`` until the queue is empty (or ``stop`` is
    set, or ``max_jobs`` reached). Returns counts ``{dispatched, requeued, drained, completed,
    blocked, failed}``. Pure of process concerns (signals, sleeping, reconnect) so it is directly
    testable against a real Postgres connection."""
    handlers = HANDLERS if handlers is None else handlers
    counts = {"dispatched": 0, "requeued": 0, "drained": 0, "completed": 0, "blocked": 0, "failed": 0}

    if dispatch:
        counts["dispatched"] = wakes.dispatch_due_wakes(conn)
    counts["requeued"] = jobs.requeue_stale(conn, older_than_seconds=_STALE_SECONDS, worker_id=worker_id)

    while stop is None or not stop.is_set():
        outcome: JobOutcome | None = jobs.run_one(
            conn, worker_id=worker_id, handlers=handlers, kinds=kinds
        )
        if outcome is None:
            break
        counts["drained"] += 1
        if outcome.status == "completed":
            counts["completed"] += 1
        elif outcome.status == "blocked":
            counts["blocked"] += 1
        elif outcome.status in ("failed", "queued"):  # fail() may requeue (→ 'queued') or give up (→ 'failed')
            counts["failed"] += 1
        _log.info(
            "worker[%s]: job %s kind=%s -> %s (reserved=%dc actual=%dc%s)",
            worker_id,
            outcome.job_id,
            outcome.kind,
            outcome.status,
            outcome.reserved_cents,
            outcome.actual_cents,
            f" reason={outcome.reason}" if outcome.reason else "",
        )
        if max_jobs is not None and counts["drained"] >= max_jobs:
            break

    return counts


def run_worker_loop(
    *,
    worker_id: str | None = None,
    poll_interval: float | None = None,
    dispatch: bool = True,
    kinds: list[str] | tuple[str, ...] | None = None,
    once: bool = False,
    max_jobs: int | None = None,
    database_url: str | None = None,
) -> int:
    """Run the worker process loop until SIGTERM/SIGINT (or ``once``/``max_jobs``). Opens a fresh
    per-tick psycopg connection (autocommit, ``prepare_threshold=None`` — the SAME pgbouncer-safe
    settings as ``runtime_app``) so a dropped connection only costs one tick; reconnects next tick.
    A SIGTERM stops pulling NEW jobs between jobs and exits cleanly — a job killed mid-turn is left
    'running' and reclaimed by ``requeue_stale`` on the next worker (its reservation refunded), so an
    interrupted wake is safe. Returns the total number of jobs drained."""
    import psycopg

    from .core import load_takyon_env
    from .runtime_app import resolve_database_url

    load_takyon_env()
    resolved_url = resolve_database_url(database_url)  # invariant #8: raises if unconfigured
    worker_id = worker_id or f"worker-{socket.gethostname()}-{os.getpid()}"
    interval = poll_interval if poll_interval is not None else _env_float(
        "TAKYON_WORKER_POLL_SECONDS", _DEFAULT_POLL_SECONDS
    )

    stop = threading.Event()

    def _request_stop(signum, _frame):
        _log.info("worker[%s]: signal %s received; finishing current job then stopping", worker_id, signum)
        stop.set()

    import signal

    for _sig in (signal.SIGTERM, signal.SIGINT):
        try:
            signal.signal(_sig, _request_stop)
        except (ValueError, OSError):
            # Not on the main thread (e.g. under a test harness) — skip signal install.
            pass

    _log.info("worker[%s]: starting (dispatch=%s poll=%.0fs)", worker_id, dispatch, interval)
    total_drained = 0
    while not stop.is_set():
        try:
            conn = psycopg.connect(resolved_url, autocommit=True, prepare_threshold=None)
        except Exception as exc:  # noqa: BLE001 — transient DB outage must not crash the daemon
            _log.warning("worker[%s]: DB connect failed (%s); retrying in %.0fs", worker_id, exc, interval)
            stop.wait(interval)
            continue
        try:
            counts = drain_tick(
                conn,
                worker_id=worker_id,
                kinds=kinds,
                dispatch=dispatch,
                stop=stop,
                max_jobs=max_jobs,
            )
            total_drained += counts["drained"]
        except Exception as exc:  # noqa: BLE001 — a tick failure must not crash the daemon
            _log.exception("worker[%s]: tick failed: %s", worker_id, exc)
        finally:
            conn.close()

        if once or (max_jobs is not None and total_drained >= max_jobs):
            break
        stop.wait(interval)

    _log.info("worker[%s]: stopped (drained %d job(s) this run)", worker_id, total_drained)
    return total_drained


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except (TypeError, ValueError):
        _log.warning("worker: invalid %s=%r; using default %.0f", name, raw, default)
        return default
