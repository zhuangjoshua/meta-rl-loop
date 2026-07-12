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

import json
import os
import shutil
import tempfile
import threading
import uuid
import contextlib
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

psycopg = pytest.importorskip("psycopg")

from plugins.takyon import app_usage, billing, core, jobs, safebox, wakes, worker  # noqa: E402
from plugins.takyon import turn_runtime
from plugins.takyon.control_plane import provision_user_on_first_login  # noqa: E402
from plugins.takyon.runtime_app import RuntimeNotConfigured  # noqa: E402
from plugins.takyon import storage  # noqa: E402
from gateway.session_context import get_session_env  # noqa: E402


@pytest.fixture(autouse=True)
def _local_safebox_authority(monkeypatch):
    """Route money/provisioning authority through the LOCAL safebox path for this suite.

    Post authority-split (f0e2ae2a), ``provision_user_on_first_login`` / ``billing.*`` mint+open
    operations delegate to the REMOTE safebox whenever ``TAKYON_SAFEBOX_URL`` is set — but the shared
    test-rig safebox writes to its OWN control plane (a different database) and its operator route
    token is scrubbed by the hermetic env, so those setup calls can never land rows in the per-test
    throwaway DB. The local-authority path runs the same SECURITY DEFINER ledger SQL on the SAME
    connection the test lent, which is exactly what these tests exercise (queue/ledger engine, not
    the plane split — that boundary has its own suites)."""
    monkeypatch.setattr(safebox, "_local_authority_enabled", lambda: True)
    # The throwaway test databases connect as the superuser; accept the tracked legacy-role opt-in
    # (the same cutover switch the rig safebox itself runs with) so the Stage-4a ledger role gates
    # (`assert_takyon_pg_role`) admit that session. Plane-role enforcement has its own suites.
    monkeypatch.setenv("TAKYON_ALLOW_LEGACY_DB_ROLES", "1")
    # Neutralize the on-disk env-file load for the whole suite. Several worker paths (the workspace
    # execution context, run_worker_loop, the lifecycle conn) legitimately call load_takyon_env()
    # first — but on a configured dev workspace that loads ../secrets/.env INTO THE PROCESS ENV
    # (PUBLIC_COMPANY_BASE_DOMAIN, prod DATABASE_URL, ...), permanently poisoning every later test in
    # the same xdist worker (observed: the app-plane routing suite's product-host mapping 400s once a
    # worker_pg test has run). Tests that need env values set them explicitly via monkeypatch.
    monkeypatch.setattr(core, "load_takyon_env", lambda *a, **k: [])
    monkeypatch.setattr(turn_runtime, "load_takyon_env", lambda *a, **k: [])


def _credentialed_dsn(pg_conn) -> str:
    """A conninfo for THIS test's throwaway DB that can actually RECONNECT. psycopg's
    ``conn.info.dsn`` deliberately strips the password, which happens to work on trust-auth
    Postgres but can never open a second connection on password-auth servers (e.g. a dockerized
    local test PG: ``fe_sendauth: no password supplied``). Rebuild from the operator-supplied
    TAKYON_TEST_PG_DSN + this test's throwaway dbname — the same recipe as the pg_store_dsn
    fixture in conftest."""
    from psycopg.conninfo import make_conninfo

    base = str(os.environ.get("TAKYON_TEST_PG_DSN") or "").strip()
    if base:
        return make_conninfo(base, dbname=pg_conn.info.dbname)
    return pg_conn.info.dsn


@pytest.fixture
def operator_plane_store(pg_conn, monkeypatch):
    """Bind the operator-plane store seam to THIS test's throwaway database.

    Stage 4a made the Postgres store open its own per-request pooled connections from the plane DSN
    (``resolve_database_url(plane="operator")`` → ``TAKYON_OPERATOR_DATABASE_URL``) and assert the
    operator DB role on each one. The throwaway rig DB has no plane DSN in env and connects as the
    superuser, so hand every store the EXPLICIT test DSN instead — the tracked test/maintenance path
    that skips the plane-role assert, exactly like the ``pg_store`` fixtures. Patching the
    ``core.TakyonStore`` name covers both ``core._store()`` (drain-tick action dispatch) and the
    call-time ``from .core import TakyonStore`` sites (ceo_wake_handler, turn_runtime's workspace
    context). The store's workspace half is pinned to the local backend so no remote sync gate runs.
    """
    dsn = _credentialed_dsn(pg_conn)
    tmp_root = Path(tempfile.mkdtemp(prefix="takyon-worker-pg-store-"))
    monkeypatch.setenv("TAKYON_STORAGE_BACKEND", "local")
    monkeypatch.setenv("TAKYON_STORAGE_LOCAL_DIR", str(tmp_root / "bucket"))
    orig_store_cls = core.TakyonStore

    def _make_store(*args, **kwargs):
        kwargs.setdefault("database_url", dsn)
        return orig_store_cls(*args, **kwargs)

    monkeypatch.setattr(core, "TakyonStore", _make_store)
    try:
        yield _make_store
    finally:
        shutil.rmtree(tmp_root, ignore_errors=True)


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


def test_drain_tick_dispatches_due_wake_then_drains_it(pg_conn, operator_plane_store):
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


def test_second_tick_is_noop_after_cursor_advances(pg_conn, operator_plane_store):
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


def test_drain_tick_settles_true_cost_through_run_one(pg_conn, operator_plane_store):
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


def test_drain_tick_counts_blocked_on_exhausted_budget(pg_conn, operator_plane_store):
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
        "dispatched": 0,
        "requeued": 0,
        "usage_holds_released": 0,
        "drained": 0,
        "completed": 0,
        "blocked": 0,
        "failed": 0,
    }


def test_operator_drain_tick_skips_safebox_only_usage_reconciler(monkeypatch):
    class _RoleConn:
        def execute(self, sql, params=()):
            if "session_user::text" in sql and "current_user::text" in sql:
                return self
            raise AssertionError(f"unexpected SQL on fake operator conn: {sql}")

        def fetchone(self):
            return {
                "session_user": "takyon_operator_runtime",
                "current_user": "takyon_operator_runtime",
            }

    def _usage_reconciler_must_not_run(*_a, **_kw):
        raise AssertionError("operator worker must not run the safebox usage-hold reconciler")

    monkeypatch.setattr(worker.jobs, "requeue_stale", lambda *_a, **_kw: 0)
    monkeypatch.setattr(worker.jobs, "run_one", lambda *_a, **_kw: None)
    monkeypatch.setattr(worker.app_usage, "reconcile_held_usage", _usage_reconciler_must_not_run)

    counts = worker.drain_tick(_RoleConn(), worker_id="operator-worker", handlers={}, dispatch=False)

    assert counts == {
        "dispatched": 0,
        "requeued": 0,
        "usage_holds_released": 0,
        "drained": 0,
        "completed": 0,
        "blocked": 0,
        "failed": 0,
    }


def test_drain_tick_defaults_stale_reclaim_to_15_minutes(monkeypatch):
    class _RoleConn:
        def execute(self, sql, params=()):
            if "session_user::text" in sql and "current_user::text" in sql:
                return self
            raise AssertionError(f"unexpected SQL on fake operator conn: {sql}")

        def fetchone(self):
            return {
                "session_user": "takyon_operator_runtime",
                "current_user": "takyon_operator_runtime",
            }

    seen: dict[str, object] = {}

    def _capture_requeue(_conn, *, older_than_seconds, worker_id):
        seen["older_than_seconds"] = older_than_seconds
        seen["worker_id"] = worker_id
        return 0

    monkeypatch.delenv("TAKYON_WORKER_STALE_SECONDS", raising=False)
    monkeypatch.setattr(worker.jobs, "requeue_stale", _capture_requeue)
    monkeypatch.setattr(worker.jobs, "run_one", lambda *_a, **_kw: None)

    counts = worker.drain_tick(_RoleConn(), worker_id="operator-worker", handlers={}, dispatch=False)

    assert counts == {
        "dispatched": 0,
        "requeued": 0,
        "usage_holds_released": 0,
        "drained": 0,
        "completed": 0,
        "blocked": 0,
        "failed": 0,
    }
    assert seen == {
        "older_than_seconds": 900,
        "worker_id": "operator-worker",
    }


def test_drain_tick_passes_owner_user_id_to_run_one(monkeypatch):
    class _RoleConn:
        def execute(self, sql, params=()):
            if "session_user::text" in sql and "current_user::text" in sql:
                return self
            raise AssertionError(f"unexpected SQL on fake operator conn: {sql}")

        def fetchone(self):
            return {
                "session_user": "takyon_operator_runtime",
                "current_user": "takyon_operator_runtime",
            }

    seen: dict[str, object] = {}

    monkeypatch.setattr(worker.jobs, "requeue_stale", lambda *_a, **_kw: 0)

    def _capture_run_one(_conn, **kwargs):
        seen["owner_user_id"] = kwargs.get("owner_user_id")
        return None

    monkeypatch.setattr(worker.jobs, "run_one", _capture_run_one)

    counts = worker.drain_tick(
        _RoleConn(),
        worker_id="operator-worker",
        handlers={},
        dispatch=False,
        owner_user_id="user-123",
    )

    assert counts == {
        "dispatched": 0,
        "requeued": 0,
        "usage_holds_released": 0,
        "drained": 0,
        "completed": 0,
        "blocked": 0,
        "failed": 0,
    }
    assert seen == {"owner_user_id": "user-123"}


def test_drain_tick_reconciles_orphaned_usage_holds(pg_conn, monkeypatch):
    slug, _uid = _provision_business(pg_conn)
    app_usage.set_app_budget(pg_conn, slug, hard_limit_microusd=1_000)
    app_usage.reserve_usage(pg_conn, slug, estimated_cost_microusd=400, reservation_key="held")
    monkeypatch.setenv("TAKYON_APP_USAGE_HOLD_TTL_SECONDS", "0")

    counts = worker.drain_tick(pg_conn, worker_id="w1", handlers={}, dispatch=False)

    assert counts["usage_holds_released"] == 1
    event = app_usage.list_usage_events(pg_conn, slug)[0]
    assert event.status == "released"
    assert app_usage.get_usage_summary(pg_conn, slug)["committed_microusd"] == 0


def test_drain_tick_uses_real_registry_for_ceo_wake(pg_conn, operator_plane_store, monkeypatch):
    # With no explicit handlers, the tick consults worker.HANDLERS — proving ceo_wake is wired. We
    # stub the model turn at the run seam so no provider is called.
    slug, _uid = _provision_business(pg_conn)
    wakes.upsert_wake_schedule(pg_conn, slug, interval_seconds=3600, next_run_at=_due_now())
    seen: dict[str, str] = {}

    def _fake_turn(*, slug, **_kw):  # noqa: A002 - mirror the real kw name
        seen["slug"] = slug
        return "done", 0.0, "exact", True

    monkeypatch.setattr(worker, "_run_ceo_turn", _fake_turn)
    counts = worker.drain_tick(pg_conn, worker_id="w1")  # handlers defaults to HANDLERS
    assert counts["completed"] == 1
    assert seen["slug"] == slug


# ── unit: handler cost conversion + invariant #8 ─────────────────────────────────────────────────────


def test_handlers_registry_maps_ceo_wake():
    assert worker.HANDLERS["ceo_wake"] is worker.ceo_wake_handler


def test_handlers_registry_maps_ceo_bootstrap():
    assert worker.HANDLERS["ceo_bootstrap"] is worker.ceo_bootstrap_handler


def test_handlers_registry_maps_x_publish_outreach():
    assert worker.HANDLERS["x.publish_outreach"] is worker.x_publish_outreach_handler


def test_handlers_registry_maps_reddit_publish_outreach():
    assert worker.HANDLERS["reddit.publish_outreach"] is worker.reddit_publish_outreach_handler


def test_bootstrap_final_surface_refresh_skips_redundant_republish(monkeypatch):
    called = {"count": 0}

    def _unexpected_refresh(*args, **kwargs):
        called["count"] += 1
        return json.dumps({"success": True, "surface_refresh": {"status": "passed", "publish": {"status": "published"}}})

    class _FakeCursor:
        def __init__(self, rows):
            self._rows = rows

        def fetchall(self):
            return list(self._rows)

    class _FakeConn:
        def __init__(self, rows):
            self._rows = rows

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def execute(self, *_args, **_kwargs):
            return _FakeCursor(self._rows)

    class _FakeStore:
        def read(self, *, scope, query, include=None, limit=None):
            assert scope == "business:permitkit"
            assert query == "summary"
            return {
                "app": {
                    "surface_contract": {
                        "source_path": "product/site",
                        "publish_status": "published",
                        "published_at": "2026-06-08T15:48:31+00:00",
                        "metadata": {
                            "takyon_publish": {
                                "status": "published",
                                "published_at": "2026-06-08T15:48:31+00:00",
                                "publish_source_path": "product/site",
                            }
                        },
                    }
                }
            }

        def _connect(self):
            return _FakeConn([])

    monkeypatch.setattr(core, "TakyonStore", lambda operator_user_id=None: _FakeStore())
    monkeypatch.setattr(core, "handle_business_refresh_product_surface", _unexpected_refresh)

    result = worker._refresh_business_surface_after_bootstrap(
        "permitkit",
        job_id="job-1",
    )

    # The contract under test: NO redundant republish runs. Since 0c693c65 ("Fix local operator
    # bootstrap parity") the skip returns a structured already-published receipt (instead of None)
    # so callers can see WHY nothing ran — the refresh handler still must never be invoked.
    assert isinstance(result, dict)
    assert result["note"] == "already_published_no_source_changes"
    assert result["publish"]["status"] == "published"
    assert called["count"] == 0


def test_bootstrap_final_surface_refresh_runs_after_late_product_write(monkeypatch):
    called: list[dict[str, Any]] = []

    def _refresh(args, **kwargs):
        called.append(dict(args))
        return json.dumps(
            {
                "success": True,
                "surface_refresh": {
                    "status": "passed",
                    "source_path": "product/site",
                    "publish": {
                        "status": "published",
                        "public_url": "https://permitkit.fourmanifold.com/",
                    },
                },
            }
        )

    class _FakeCursor:
        def __init__(self, rows):
            self._rows = rows

        def fetchall(self):
            return list(self._rows)

    class _FakeConn:
        def __init__(self, rows):
            self._rows = rows

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def execute(self, *_args, **_kwargs):
            return _FakeCursor(self._rows)

    class _FakeStore:
        def read(self, *, scope, query, include=None, limit=None):
            assert scope == "business:permitkit"
            assert query == "summary"
            return {
                "app": {
                    "surface_contract": {
                        "source_path": "product/site",
                        "publish_status": "published",
                        "published_at": "2026-06-08T15:48:31+00:00",
                        "metadata": {
                            "takyon_publish": {
                                "status": "published",
                                "published_at": "2026-06-08T15:48:31+00:00",
                                "publish_source_path": "product/site",
                            }
                        },
                    }
                }
            }

        def _connect(self):
            return _FakeConn(
                [
                    {
                        "payload_json": json.dumps(
                            {"path": "product/site/src/app/page.js"}
                        )
                    }
                ]
            )

    monkeypatch.setattr(core, "TakyonStore", lambda operator_user_id=None: _FakeStore())
    monkeypatch.setattr(core, "handle_business_refresh_product_surface", _refresh)

    result = worker._refresh_business_surface_after_bootstrap(
        "permitkit",
        job_id="job-2",
    )

    assert called and called[0]["source_path"] == "product/site"
    assert isinstance(result, dict)
    assert result["publish"]["status"] == "published"


@pytest.mark.parametrize(
    "requirement",
    ("composio", "x", "twitter", "x_social", "meta", "metaads", "reddit", "reddit_ads"),
)
def test_composio_requirement_accepts_env_key(monkeypatch, requirement):
    monkeypatch.setenv("COMPOSIO_API_KEY", "composio-test-key")
    assert core._missing_env_for_requirement(requirement) == []


def test_composio_requirement_accepts_safebox_backed_key(monkeypatch):
    monkeypatch.delenv("COMPOSIO_API_KEY", raising=False)
    monkeypatch.setattr(
        core.safebox,
        "read_env_backed_value",
        lambda key: "composio-test-key" if key == "COMPOSIO_API_KEY" else "",
    )
    assert core._missing_env_for_requirement("x") == []


@pytest.mark.parametrize("requirement", ("x", "reddit", "reddit_ads"))
def test_composio_requirement_reports_missing_api_key(monkeypatch, requirement):
    monkeypatch.delenv("COMPOSIO_API_KEY", raising=False)
    monkeypatch.setattr(core.safebox, "read_env_backed_value", lambda _key: "")
    assert core._missing_env_for_requirement(requirement) == ["COMPOSIO_API_KEY"]


def test_meta_requirement_reports_missing_key_aliases(monkeypatch):
    # The Meta ads plane deliberately resolves its own Graph/MCP token aliases FIRST and only falls
    # back to Composio (core._API_ENV_ALIASES["meta"], commit 9566afc4 "Replace Meta Ads v2 with
    # rebuilt Graph+MCP path"). The missing-credential report is one slash-joined alias entry that
    # must name both the Meta token route and the Composio fallback.
    monkeypatch.delenv("COMPOSIO_API_KEY", raising=False)
    monkeypatch.setattr(core.safebox, "read_env_backed_value", lambda _key: "")
    missing = core._missing_env_for_requirement("meta")
    assert len(missing) == 1
    aliases = missing[0].split("/")
    assert "META_MCP_OAUTH_TOKEN" in aliases
    assert "COMPOSIO_API_KEY" in aliases


def test_ceo_wake_handler_reports_true_cost_in_cents(monkeypatch, tmp_path):
    # The handler converts the turn's true USD cost to integer cents for settlement and packages the
    # response. $0.0734 → 7 cents.
    import contextlib

    captured: dict = {}

    def _fake_turn(*, slug, system_prompt, user_prompt, toolsets, max_turns, inactivity_limit, **_kw):
        captured.update(slug=slug, toolsets=toolsets, max_turns=max_turns)
        return "the CEO did things", 0.0734, "exact", True

    # Stage 4a: mounting the real canonical workspace needs the operator-plane DB (head-revision
    # read). This unit test pins the handler's cost/plumbing contract, not the mount — fake the
    # workspace context (the established in-file pattern; the mount has its own PG coverage above).
    @contextlib.contextmanager
    def _fake_workspace(*_a, **_k):
        yield str(tmp_path)

    monkeypatch.setattr(turn_runtime, "_business_workspace_execution_context", _fake_workspace)
    monkeypatch.setattr(worker, "_business_owner_user_id", lambda _slug: "user-123")
    monkeypatch.setattr(worker, "_run_ceo_turn", _fake_turn)
    job = SimpleNamespace(id="job-cost-1", business_slug="acme", payload={})
    result = worker.ceo_wake_handler(job)

    assert result.actual_cost_cents == 7
    assert result.result["business_slug"] == "acme"
    assert result.result["final_response"] == "the CEO did things"
    assert result.result["cost_status"] == "exact"
    # The handler sourced the canonical wake toolsets (not an invented list). The CEO wake
    # deliberately carries the quarantined spendful toolset too (1826f007 "Fix keystone
    # toolset-gating: give CEO the takyon-authority toolset").
    assert captured["toolsets"] == ["takyon", core.TAKYON_AUTHORITY_TOOLSET, "web", "skills", "todo"]
    assert captured["max_turns"] == worker._DEFAULT_MAX_TURNS


def test_x_publish_outreach_handler_posts_and_records_receipt(monkeypatch, tmp_path):
    captured: dict[str, Any] = {"calls": []}
    statuses: list[tuple[str, str, dict[str, Any]]] = []
    recorded: dict[str, Any] = {}

    monkeypatch.setattr(
        core,
        "_reserve_creative_credits",
        lambda *args, **kwargs: {
            "requested_credits": 1,
            "budget_bucket": "x",
            "channel_budget": {"allocated_credits": 1, "used_credits": 0, "reserved_credits": 1, "remaining_credits": 0},
        },
    )
    monkeypatch.setattr(
        core,
        "_commit_creative_credits",
        lambda *args, **kwargs: {
            "actual_credits": 1,
            "balance_credits": 9,
            "reserved_credits": 0,
            "budget_bucket": "x",
            "channel_budget": {"allocated_credits": 1, "used_credits": 1, "reserved_credits": 0, "remaining_credits": 0},
        },
    )
    monkeypatch.setattr(
        core,
        "_release_creative_credits",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("release should not run on successful publish")),
    )

    def _fake_twitter_execute(tool_slug, *, arguments=None, timeout=0.0, **_kwargs):
        captured["calls"].append({"tool_slug": tool_slug, "arguments": dict(arguments or {}), "timeout": timeout})
        if tool_slug == "TWITTER_CREATION_OF_A_POST":
            return {"data": {"id": "tweet-123"}}
        if tool_slug == "TWITTER_USER_LOOKUP_ME":
            return {"data": {"username": "sharedacct"}}
        raise AssertionError(f"unexpected tool {tool_slug}")

    monkeypatch.setattr(worker.composio_distribution, "twitter_execute_tool", _fake_twitter_execute)
    monkeypatch.setattr(
        worker,
        "_record_x_publish_result",
        lambda slug, **kwargs: recorded.update(kwargs) or {"artifact": "distribution/local-published/x/proof.md", "receipt": "metrics/receipts/outreach/proof.json"},
    )
    monkeypatch.setattr(
        worker,
        "_update_work_request",
        lambda slug, work_request_id, *, status, payload_updates=None: statuses.append(
            (work_request_id, status, dict(payload_updates or {}))
        ),
    )

    job = SimpleNamespace(
        id="job-123",
        business_slug="acme",
        payload={
            "body": "Ship it",
            "channel": "x",
            "provider": "x",
            "subject": "Ship it",
            "work_request_id": "wr-123",
        },
    )
    result = worker.x_publish_outreach_handler(job)

    assert captured["calls"][0]["tool_slug"] == "TWITTER_CREATION_OF_A_POST"
    assert captured["calls"][0]["arguments"] == {"text": "Ship it"}
    assert captured["calls"][1]["tool_slug"] == "TWITTER_USER_LOOKUP_ME"
    assert result.actual_cost_cents == 0
    assert result.result["post_id"] == "tweet-123"
    assert result.result["post_url"] == "https://x.com/sharedacct/status/tweet-123"
    assert result.result["credits_charged"] == 1
    assert result.result["budget_bucket"] == "x"
    assert statuses[0][1] == "running"
    assert statuses[-1][1] == "completed"
    assert statuses[-1][2]["post_id"] == "tweet-123"
    assert recorded["credits_charged"] == 1


def test_x_publish_outreach_handler_marks_failed_work_request(monkeypatch, tmp_path):
    statuses: list[tuple[str, str, dict[str, Any]]] = []
    release_calls: list[dict[str, Any]] = []

    monkeypatch.setattr(
        core,
        "_reserve_creative_credits",
        lambda *args, **kwargs: {
            "requested_credits": 1,
            "budget_bucket": "x",
            "channel_budget": {"allocated_credits": 1, "used_credits": 0, "reserved_credits": 1, "remaining_credits": 0},
        },
    )
    monkeypatch.setattr(
        core,
        "_commit_creative_credits",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("commit should not run when nothing was published")),
    )
    monkeypatch.setattr(
        core,
        "_release_creative_credits",
        lambda *args, **kwargs: release_calls.append(dict(kwargs)) or {
            "balance_credits": 10,
            "reserved_credits": 0,
            "budget_bucket": "x",
            "channel_budget": {"allocated_credits": 1, "used_credits": 0, "reserved_credits": 0, "remaining_credits": 1},
        },
    )
    monkeypatch.setattr(
        worker.composio_distribution,
        "twitter_execute_tool",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("auth failed")),
    )
    monkeypatch.setattr(
        worker,
        "_update_work_request",
        lambda slug, work_request_id, *, status, payload_updates=None: statuses.append(
            (work_request_id, status, dict(payload_updates or {}))
        ),
    )

    with pytest.raises(RuntimeError, match="auth failed"):
        worker.x_publish_outreach_handler(
            SimpleNamespace(
                id="job-456",
                business_slug="acme",
                payload={"body": "Ship it", "provider": "x", "work_request_id": "wr-456"},
            )
        )

    assert statuses[0][1] == "running"
    assert statuses[-1][1] == "failed"
    assert release_calls


def test_x_publish_outreach_handler_threads_overlength_body(monkeypatch, tmp_path):
    calls: list[dict[str, Any]] = []
    captured_result: dict[str, Any] = {}

    monkeypatch.setattr(
        core,
        "_reserve_creative_credits",
        lambda *args, **kwargs: {
            "requested_credits": 1,
            "budget_bucket": "x",
            "channel_budget": {"allocated_credits": 1, "used_credits": 0, "reserved_credits": 1, "remaining_credits": 0},
        },
    )
    monkeypatch.setattr(
        core,
        "_commit_creative_credits",
        lambda *args, **kwargs: {
            "actual_credits": 1,
            "balance_credits": 9,
            "reserved_credits": 0,
            "budget_bucket": "x",
            "channel_budget": {"allocated_credits": 1, "used_credits": 1, "reserved_credits": 0, "remaining_credits": 0},
        },
    )
    monkeypatch.setattr(
        core,
        "_release_creative_credits",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("release should not run on successful thread publish")),
    )

    responses = iter(
        (
            {"data": {"id": "tweet-1"}},
            {"data": {"id": "tweet-2"}},
            {"data": {"username": "sharedacct"}},
        )
    )

    def _fake_twitter_execute(tool_slug, *, arguments=None, timeout=0.0, **_kwargs):
        calls.append({"tool_slug": tool_slug, "arguments": dict(arguments or {}), "timeout": timeout})
        return next(responses)

    monkeypatch.setattr(worker.composio_distribution, "twitter_execute_tool", _fake_twitter_execute)

    def _fake_record(slug, **kwargs):
        captured_result["payload"] = kwargs
        return {
            "artifact": "distribution/local-published/x/thread.md",
            "receipt": "metrics/receipts/outreach/thread.json",
        }

    monkeypatch.setattr(
        worker,
        "_record_x_publish_result",
        _fake_record,
    )
    monkeypatch.setattr(worker, "_update_work_request", lambda *args, **kwargs: None)

    body = "A" * 200 + "\n\n" + "B" * 160
    result = worker.x_publish_outreach_handler(
        SimpleNamespace(
            id="job-thread",
            business_slug="acme",
            payload={"body": body, "provider": "x"},
        )
    )

    assert [call["tool_slug"] for call in calls] == [
        "TWITTER_CREATION_OF_A_POST",
        "TWITTER_CREATION_OF_A_POST",
        "TWITTER_USER_LOOKUP_ME",
    ]
    assert calls[0]["arguments"]["text"] == "A" * 200
    assert calls[1]["arguments"]["reply_in_reply_to_tweet_id"] == "tweet-1"
    thread_posts = captured_result["payload"]["provider_response"]["thread_posts"]
    assert [item["post_id"] for item in thread_posts] == ["tweet-1", "tweet-2"]
    assert result.result["post_id"] == "tweet-1"
    assert result.result["post_url"] == "https://x.com/sharedacct/status/tweet-1"


def test_x_publish_outreach_handler_uploads_media_once_and_attaches_to_first_post(monkeypatch, tmp_path):
    captured: dict[str, Any] = {"uploads": [], "calls": []}
    recorded: dict[str, Any] = {}
    backend = storage.LocalStorageBackend(tmp_path / "bucket")
    seed = tmp_path / "seed"
    media_path = seed / "product" / "ads" / "hero.png"
    media_path.parent.mkdir(parents=True, exist_ok=True)
    media_path.write_bytes(b"png")
    # Canonicalization spine: the store's business mirror materializes from an IMMUTABLE keyed
    # workspace revision whose head pointer lives in the operator control plane. Seed revision 1
    # and pin the head-revision read (the one DB touch on this path) so the media file resolves
    # through the real mirror without a database.
    storage.write_workspace_revision(backend, "acme", 1, seed)

    class _RevisionPinnedStore(core.TakyonStore):
        def _business_head_revision(self, slug: str) -> int:
            return 1

    monkeypatch.setenv("TAKYON_HOME", str(tmp_path))
    monkeypatch.setenv("TAKYON_STORAGE_BACKEND", "local")
    monkeypatch.setenv("TAKYON_STORAGE_LOCAL_DIR", str(tmp_path / "bucket"))
    monkeypatch.setattr(core, "_store", lambda: _RevisionPinnedStore(tmp_path, operator_user_id=""))

    monkeypatch.setattr(
        core,
        "_reserve_creative_credits",
        lambda *args, **kwargs: {
            "requested_credits": 1,
            "budget_bucket": "x",
            "channel_budget": {"allocated_credits": 1, "used_credits": 0, "reserved_credits": 1, "remaining_credits": 0},
        },
    )
    monkeypatch.setattr(
        core,
        "_commit_creative_credits",
        lambda *args, **kwargs: {
            "actual_credits": 1,
            "balance_credits": 9,
            "reserved_credits": 0,
            "budget_bucket": "x",
            "channel_budget": {"allocated_credits": 1, "used_credits": 1, "reserved_credits": 0, "remaining_credits": 0},
        },
    )
    monkeypatch.setattr(
        core,
        "_release_creative_credits",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("release should not run on successful publish")),
    )

    monkeypatch.setattr(
        worker.composio_distribution,
        "upload_file_descriptor",
        lambda *, toolkit_slug, tool_slug, file_path, timeout=0.0: captured["uploads"].append(
            {
                "toolkit_slug": toolkit_slug,
                "tool_slug": tool_slug,
                "file_path": str(file_path),
                "timeout": timeout,
            }
        ) or {"name": "hero.png", "mimetype": "image/png", "s3key": "s3://hero"},
    )

    def _fake_twitter_execute(tool_slug, *, arguments=None, timeout=0.0, **_kwargs):
        captured["calls"].append({"tool_slug": tool_slug, "arguments": dict(arguments or {})})
        if tool_slug == "TWITTER_UPLOAD_MEDIA":
            return {"data": {"media_id_string": "media-1"}}
        if tool_slug == "TWITTER_CREATION_OF_A_POST":
            return {"data": {"id": "tweet-123"}}
        if tool_slug == "TWITTER_USER_LOOKUP_ME":
            return {"data": {"username": "sharedacct"}}
        raise AssertionError(f"unexpected tool {tool_slug}")

    monkeypatch.setattr(worker.composio_distribution, "twitter_execute_tool", _fake_twitter_execute)
    monkeypatch.setattr(
        worker,
        "_record_x_publish_result",
        lambda slug, **kwargs: recorded.update(kwargs) or {
            "artifact": "distribution/local-published/x/proof.md",
            "receipt": "metrics/receipts/outreach/proof.json",
        },
    )
    monkeypatch.setattr(worker, "_update_work_request", lambda *args, **kwargs: None)

    result = worker.x_publish_outreach_handler(
        SimpleNamespace(
            id="job-media",
            business_slug="acme",
            payload={"body": "Ship it", "provider": "x", "media_paths": ["product/ads/hero.png"]},
        )
    )

    assert captured["uploads"][0]["tool_slug"] == "TWITTER_UPLOAD_MEDIA"
    assert captured["calls"][0]["tool_slug"] == "TWITTER_UPLOAD_MEDIA"
    assert captured["calls"][1]["tool_slug"] == "TWITTER_CREATION_OF_A_POST"
    assert captured["calls"][1]["arguments"]["media_media_ids"] == ["media-1"]
    assert recorded["media"] == [{"path": "product/ads/hero.png", "media_id": "media-1"}]
    assert result.result["post_id"] == "tweet-123"


def test_x_publish_outreach_handler_releases_credits_when_media_upload_fails(monkeypatch, tmp_path):
    release_calls: list[dict[str, Any]] = []
    backend = storage.LocalStorageBackend(tmp_path / "bucket")
    seed = tmp_path / "seed"
    media_path = seed / "product" / "ads" / "hero.png"
    media_path.parent.mkdir(parents=True, exist_ok=True)
    media_path.write_bytes(b"png")
    # See the media-attach test above: seed an immutable workspace revision and pin the store's
    # head-revision read so the media file resolves through the real mirror without a database.
    storage.write_workspace_revision(backend, "acme", 1, seed)

    class _RevisionPinnedStore(core.TakyonStore):
        def _business_head_revision(self, slug: str) -> int:
            return 1

    monkeypatch.setenv("TAKYON_HOME", str(tmp_path))
    monkeypatch.setenv("TAKYON_STORAGE_BACKEND", "local")
    monkeypatch.setenv("TAKYON_STORAGE_LOCAL_DIR", str(tmp_path / "bucket"))
    monkeypatch.setattr(core, "_store", lambda: _RevisionPinnedStore(tmp_path, operator_user_id=""))

    monkeypatch.setattr(
        core,
        "_reserve_creative_credits",
        lambda *args, **kwargs: {
            "requested_credits": 1,
            "budget_bucket": "x",
            "channel_budget": {"allocated_credits": 1, "used_credits": 0, "reserved_credits": 1, "remaining_credits": 0},
        },
    )
    monkeypatch.setattr(
        core,
        "_commit_creative_credits",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("commit should not run when upload fails")),
    )
    monkeypatch.setattr(
        core,
        "_release_creative_credits",
        lambda *args, **kwargs: release_calls.append(dict(kwargs)) or {
            "balance_credits": 10,
            "reserved_credits": 0,
            "budget_bucket": "x",
            "channel_budget": {"allocated_credits": 1, "used_credits": 0, "reserved_credits": 0, "remaining_credits": 1},
        },
    )

    monkeypatch.setattr(
        worker.composio_distribution,
        "upload_file_descriptor",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("upload failed")),
    )
    monkeypatch.setattr(
        worker.composio_distribution,
        "twitter_execute_tool",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("post call should not run when upload fails")),
    )
    monkeypatch.setattr(worker, "_update_work_request", lambda *args, **kwargs: None)

    with pytest.raises(RuntimeError, match="upload failed"):
        worker.x_publish_outreach_handler(
            SimpleNamespace(
                id="job-media-fail",
                business_slug="acme",
                payload={"body": "Ship it", "provider": "x", "media_paths": ["product/ads/hero.png"]},
            )
        )

    assert release_calls


def test_reddit_publish_outreach_handler_posts_and_records_receipt(monkeypatch):
    statuses: list[tuple[str, str, dict[str, Any]]] = []
    recorded: dict[str, Any] = {}

    monkeypatch.setattr(
        core,
        "_reserve_creative_credits",
        lambda *args, **kwargs: {
            "requested_credits": 1,
            "budget_bucket": "reddit",
            "channel_budget": {"allocated_credits": 1, "used_credits": 0, "reserved_credits": 1, "remaining_credits": 0},
        },
    )
    monkeypatch.setattr(
        core,
        "_commit_creative_credits",
        lambda *args, **kwargs: {
            "actual_credits": 1,
            "balance_credits": 9,
            "reserved_credits": 0,
            "budget_bucket": "reddit",
            "channel_budget": {"allocated_credits": 1, "used_credits": 1, "reserved_credits": 0, "remaining_credits": 0},
        },
    )
    monkeypatch.setattr(
        core,
        "_release_creative_credits",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("release should not run on successful publish")),
    )
    monkeypatch.setattr(
        worker.composio_distribution,
        "reddit_execute_tool",
        lambda tool_slug, *, arguments=None, timeout=0.0, **_kwargs: {
            "data": {"json": {"data": {"name": "t3_post123", "permalink": "/r/freelance/comments/post123/title/"}}}
        },
    )
    monkeypatch.setattr(
        worker,
        "_record_reddit_publish_result",
        lambda slug, **kwargs: recorded.update(kwargs) or {
            "artifact": "distribution/local-published/reddit/proof.md",
            "receipt": "metrics/receipts/outreach/reddit-proof.json",
        },
    )
    monkeypatch.setattr(
        worker,
        "_update_work_request",
        lambda slug, work_request_id, *, status, payload_updates=None: statuses.append(
            (work_request_id, status, dict(payload_updates or {}))
        ),
    )

    result = worker.reddit_publish_outreach_handler(
        SimpleNamespace(
            id="job-reddit",
            business_slug="acme",
            payload={
                "title": "How freelancers stop scope creep",
                "body": "Short checklist that keeps projects from ballooning.",
                "subreddit": "freelance",
                "post_kind": "self",
                "provider": "reddit",
                "work_request_id": "wr-reddit",
            },
        )
    )

    assert result.result["post_id"] == "t3_post123"
    assert result.result["post_url"] == "https://www.reddit.com/r/freelance/comments/post123/title/"
    assert statuses[0][1] == "running"
    assert statuses[-1][1] == "completed"
    assert recorded["credits_charged"] == 1


def test_ceo_wake_handler_honors_payload_max_turns(monkeypatch, tmp_path):
    import contextlib

    captured: dict = {}

    def _fake_turn(*, max_turns, **_kw):
        captured["max_turns"] = max_turns
        return "", 0.0, "none", True

    # Stage 4a: the real workspace mount reads the operator-plane DB; this test pins only the
    # max_turns payload plumbing, so fake the workspace context (in-file pattern).
    @contextlib.contextmanager
    def _fake_workspace(*_a, **_k):
        yield str(tmp_path)

    monkeypatch.setattr(turn_runtime, "_business_workspace_execution_context", _fake_workspace)
    monkeypatch.setattr(worker, "_business_owner_user_id", lambda _slug: "user-123")
    monkeypatch.setattr(worker, "_run_ceo_turn", _fake_turn)
    worker.ceo_wake_handler(SimpleNamespace(id="job-turns-1", business_slug="acme", payload={"max_turns": 7}))
    assert captured["max_turns"] == 7


def test_ceo_wake_handler_binds_owner_before_loading_prompt(monkeypatch):
    import contextlib

    from plugins.takyon import cli as takyon_cli
    import gateway.session_context as session_context

    seen: dict[str, Any] = {}

    class _FakeStore:
        def __init__(self, *args, **kwargs):
            self.operator_user_id = kwargs.get("operator_user_id")
            # setdefault: the contract under test is that the FIRST store (the one the wake prompt
            # is read from) binds the owner. Later incidental stores in the same handler (e.g. the
            # pre-wake ad-insights refresh building core._store()) must not clobber the recording —
            # same pattern as test_refresh_business_surface_after_bootstrap_binds_operator_identity.
            seen.setdefault("store_operator_user_id", self.operator_user_id)

        def _ceo_cron_prompt(self, slug: str) -> str:
            assert slug == "acme"
            if self.operator_user_id != "user-123":
                raise AssertionError("wake prompt read before owner binding")
            return "wake prompt"

        def _ceo_cron_toolsets(self) -> list[str]:
            if self.operator_user_id != "user-123":
                raise AssertionError("wake toolsets read before owner binding")
            return ["takyon", "web", "skills", "todo"]

    @contextlib.contextmanager
    def _fake_workspace(*_a, **_k):
        yield "/tmp/fake-workspace"

    monkeypatch.setattr(core, "TakyonStore", _FakeStore)
    monkeypatch.setattr(worker, "_business_owner_user_id", lambda _slug: "user-123")
    monkeypatch.setattr(turn_runtime, "_business_workspace_execution_context", _fake_workspace)
    monkeypatch.setattr(session_context, "set_session_vars", lambda **_k: [])
    monkeypatch.setattr(session_context, "clear_session_vars", lambda *_a, **_k: None)
    monkeypatch.setattr(worker, "_record_runtime_event", lambda *_a, **_k: None)
    monkeypatch.setattr(worker, "_run_ceo_turn", lambda **_kw: ("ok", 0.0, "none", True))

    result = worker.ceo_wake_handler(SimpleNamespace(id="job-bind-1", business_slug="acme", payload={}))

    assert result.result["business_slug"] == "acme"
    assert seen["store_operator_user_id"] == "user-123"


def test_ceo_wake_handler_orders_refresh_then_distill_then_prompt(monkeypatch):
    # The wake prompt must be built AFTER the pre-wake insights refresh and the deterministic
    # episode distillation, so this wake's injected memory + appended learnings already reflect
    # this wake's own refresh/distill work (regression pin for the stale-prompt ordering bug).
    import contextlib

    import gateway.session_context as session_context

    order: list[str] = []

    class _FakeStore:
        def __init__(self, *args, **kwargs):
            pass

        def distill_episode_lessons(self, slug):
            assert slug == "acme"
            order.append("distill")
            return {"distilled": 0}

        def _ceo_cron_prompt(self, slug):
            order.append("prompt")
            return "wake prompt"

        def _ceo_cron_toolsets(self):
            return ["takyon", "web", "skills", "todo"]

    @contextlib.contextmanager
    def _fake_workspace(*_a, **_k):
        yield "/tmp/fake-workspace"

    monkeypatch.setattr(core, "TakyonStore", _FakeStore)
    monkeypatch.setattr(
        core, "_refresh_stale_live_ad_campaigns",
        lambda slug: order.append("refresh") or {"refreshed": 0},
    )
    monkeypatch.setattr(worker, "_business_owner_user_id", lambda _slug: "user-123")
    monkeypatch.setattr(turn_runtime, "_business_workspace_execution_context", _fake_workspace)
    monkeypatch.setattr(session_context, "set_session_vars", lambda **_k: [])
    monkeypatch.setattr(session_context, "clear_session_vars", lambda *_a, **_k: None)
    monkeypatch.setattr(worker, "_record_runtime_event", lambda *_a, **_k: None)
    monkeypatch.setattr(worker, "_run_ceo_turn", lambda **_kw: ("ok", 0.0, "none", True))

    result = worker.ceo_wake_handler(SimpleNamespace(id="job-order-1", business_slug="acme", payload={}))

    assert result.result["business_slug"] == "acme"
    assert order == ["refresh", "distill", "prompt"]


def test_ceo_wake_handler_runs_in_isolated_workspace(monkeypatch, tmp_path):
    seed = tmp_path / "seed"
    (seed / "research").mkdir(parents=True, exist_ok=True)
    (seed / "research" / "strategy.md").write_text("seed\n")
    monkeypatch.setattr(worker, "_business_owner_user_id", lambda _slug: "user-123")

    seen: dict[str, str] = {}

    @contextlib.contextmanager
    def _isolated_workspace(slug, **_kwargs):
        home = Path(tempfile.mkdtemp(prefix="wake-unit-", dir=str(tmp_path)))
        workspace = home / "businesses" / slug
        shutil.copytree(seed, workspace)
        seen["mounted_home"] = str(home)
        try:
            yield home
        finally:
            shutil.rmtree(home, ignore_errors=True)

    monkeypatch.setattr(turn_runtime, "_business_workspace_execution_context", _isolated_workspace)

    def _fake_turn(*, slug, **_kw):
        workspace_root = get_session_env("TAKYON_SESSION_WORKSPACE_ROOT")
        seen["workspace_root"] = workspace_root
        seen["user_id"] = get_session_env("TAKYON_SESSION_USER_ID")
        workspace = Path(workspace_root) / "businesses" / slug
        assert (workspace / "research" / "strategy.md").read_text() == "seed\n"
        (workspace / "metrics").mkdir(parents=True, exist_ok=True)
        (workspace / "metrics" / "summary.md").write_text("fresh\n")
        return "ok", 0.0, "none", True

    monkeypatch.setattr(worker, "_run_ceo_turn", _fake_turn)
    result = worker.ceo_wake_handler(SimpleNamespace(id="job-iso-1", business_slug="acme", payload={}))

    assert result.result["business_slug"] == "acme"
    assert seen["user_id"] == "user-123"
    assert seen["workspace_root"]
    assert not Path(seen["workspace_root"]).exists()


def test_ceo_wake_handler_propagates_failed_turn_and_cleans_workspace(monkeypatch, tmp_path):
    seed = tmp_path / "seed"
    (seed / "research").mkdir(parents=True, exist_ok=True)
    (seed / "research" / "strategy.md").write_text("seed\n")
    monkeypatch.setattr(worker, "_business_owner_user_id", lambda _slug: "user-123")
    seen: dict[str, str] = {}

    @contextlib.contextmanager
    def _isolated_workspace(slug, **_kwargs):
        home = Path(tempfile.mkdtemp(prefix="wake-failed-unit-", dir=str(tmp_path)))
        workspace = home / "businesses" / slug
        shutil.copytree(seed, workspace)
        seen["mounted_home"] = str(home)
        try:
            yield home
        finally:
            partial = workspace / "product" / "surface.md"
            seen["partial"] = partial.read_text() if partial.exists() else ""
            shutil.rmtree(home, ignore_errors=True)

    monkeypatch.setattr(turn_runtime, "_business_workspace_execution_context", _isolated_workspace)

    def _fake_turn(*, slug, **_kw):
        workspace_root = get_session_env("TAKYON_SESSION_WORKSPACE_ROOT")
        workspace = Path(workspace_root) / "businesses" / slug
        (workspace / "product").mkdir(parents=True, exist_ok=True)
        (workspace / "product" / "surface.md").write_text("partial surface\n")
        raise RuntimeError("turn interrupted")

    monkeypatch.setattr(worker, "_run_ceo_turn", _fake_turn)

    with pytest.raises(RuntimeError, match="turn interrupted"):
        worker.ceo_wake_handler(SimpleNamespace(id="job-sync-1", business_slug="acme", payload={}))

    assert seen["partial"] == "partial surface\n"
    assert not Path(seen["mounted_home"]).exists()


def test_ceo_wake_timeout_calls_owned_timeout_finalizer(monkeypatch):
    import contextlib

    from plugins.takyon import cli as takyon_cli
    import gateway.session_context as session_context

    events: list[tuple[str, str]] = []
    finalized: dict[str, Any] = {}

    @contextlib.contextmanager
    def _fake_workspace(*_a, **_k):
        yield "/tmp/fake-workspace"

    @contextlib.contextmanager
    def _fake_bound_op(*_a, **_k):
        yield

    class _FakeStore:
        def __init__(self, *args, **kwargs):
            pass

        def _ceo_cron_prompt(self, _slug):
            return "Wake business now."

        def _ceo_cron_toolsets(self):
            return ["takyon", "web", "skills"]

    monkeypatch.setattr(core, "TakyonStore", _FakeStore)
    monkeypatch.setattr(core, "_bound_operator_task_context", _fake_bound_op)
    monkeypatch.setattr(worker, "_business_owner_user_id", lambda _slug: "user-123")
    monkeypatch.setattr(turn_runtime, "_business_workspace_execution_context", _fake_workspace)
    monkeypatch.setattr(turn_runtime, "_load_ceo_prompt", lambda: "CEO prompt")
    monkeypatch.setattr(session_context, "set_session_vars", lambda **_k: [])
    monkeypatch.setattr(session_context, "clear_session_vars", lambda *_a, **_k: None)
    monkeypatch.setattr(
        worker,
        "_record_runtime_event",
        lambda _slug, *, kind, status, **_kw: events.append((status, kind)),
    )
    monkeypatch.setattr(
        worker,
        "_best_effort_terminalize_owned_timeout",
        lambda job, *, error: finalized.update(job_id=str(job.id), error=error) or "queued",
    )
    monkeypatch.setattr(
        worker,
        "_run_ceo_turn",
        lambda **_kw: (_ for _ in ()).throw(
            TimeoutError("CEO wake for business:acme idle past 600s inactivity limit")
        ),
    )

    with pytest.raises(TimeoutError):
        worker.ceo_wake_handler(SimpleNamespace(id="job-timeout", business_slug="acme", payload={}, locked_by="w1"))

    assert finalized["job_id"] == "job-timeout"
    assert ("failed", "ceo_wake") in events


def test_best_effort_terminalize_owned_timeout_requeues_running_job(pg_conn, monkeypatch):
    # Stage 4a: the timeout finalizer opens its OWN operator-plane lifecycle connection from the
    # plane DSN. Point that seam at this test's throwaway database (the job/ledger rows live there);
    # the finalizer's SQL and the requeue/refund contract under test stay fully real.
    def _test_lifecycle_conn():
        from plugins.takyon.runtime_app import configure_takyon_pg_session

        conn = psycopg.connect(_credentialed_dsn(pg_conn), autocommit=True, prepare_threshold=None)
        configure_takyon_pg_session(conn, bypass=True)
        return conn

    monkeypatch.setattr(worker, "_open_operator_lifecycle_conn", _test_lifecycle_conn)

    slug, uid = _provision_business(pg_conn, allowance_cents=100_000)
    queued = jobs.enqueue(
        pg_conn,
        slug,
        "ceo_bootstrap",
        idempotency_key="timeout-job",
        payload={"estimate_cents": 500},
        max_attempts=2,
    )
    claimed = jobs.claim_one(pg_conn, worker_id="w-timeout")
    assert claimed is not None and claimed.id == queued.id

    reservation_key = f"job:{claimed.id}:{claimed.attempts}"
    jobs._set_reserved_key(pg_conn, claimed.id, reservation_key)
    billing.reserve(
        pg_conn,
        uid,
        500,
        reservation_key,
        business_slug=slug,
        job_id=str(claimed.id),
    )

    status = worker._best_effort_terminalize_owned_timeout(
        claimed,
        error="CEO wake for business wedge idle past 600s inactivity limit",
    )

    assert status == "queued"
    job = jobs.get_job(pg_conn, claimed.id)
    assert job is not None
    assert job.status == "queued"
    assert job.locked_by is None
    assert job.error["reason"] == "handler_error"
    bal = billing.get_billing_balances(pg_conn, uid)
    assert bal.reserved_cents == 0
    assert bal.allowance_used_cents == 0


def test_best_effort_terminalize_owned_timeout_refunds_with_estimate(monkeypatch):
    calls: list[tuple[str, str]] = []

    class _FakeConn:
        def close(self) -> None:
            calls.append(("close", "conn"))

    def _fake_refund(conn, reservation_key):
        assert isinstance(conn, _FakeConn)
        calls.append(("refund", reservation_key))

    def _fake_fail_if_still_owned(conn, job_id, *, worker_id, attempt, error, retryable):
        assert isinstance(conn, _FakeConn)
        calls.append(("fail", job_id))
        assert worker_id == "w-timeout"
        assert attempt == 2
        assert error == "idle timeout"
        assert retryable is True
        return "queued"

    monkeypatch.setattr(worker, "_open_operator_lifecycle_conn", lambda: _FakeConn())
    monkeypatch.setattr(worker.billing, "refund", _fake_refund)
    monkeypatch.setattr(worker.jobs, "fail_if_still_owned", _fake_fail_if_still_owned)

    claimed = SimpleNamespace(
        id="job-timeout",
        attempts=2,
        locked_by="w-timeout",
        payload={"estimate_cents": 500},
    )

    status = worker._best_effort_terminalize_owned_timeout(claimed, error="idle timeout")

    assert status == "queued"
    assert calls == [
        ("refund", "job:job-timeout:2"),
        ("fail", "job-timeout"),
        ("close", "conn"),
    ]


def test_refresh_business_surface_after_bootstrap_uses_declared_surface(monkeypatch):
    seen: dict[str, object] = {}

    class _FakeStore:
        def read(self, *, scope, query, include=None, limit=None):
            assert scope == "business:acme"
            assert query == "summary"
            return {
                "app": {
                    "surface_contract": {
                        "source_path": "product/site",
                        "publish_target": "https://acme.fourmanifold.com/",
                        "publish_policy": "publish_after_refresh",
                    }
                }
            }

    def _fake_refresh(args, **_kw):
        seen.update(args)
        return json.dumps(
            {
                "success": True,
                "surface_refresh": {
                    "status": "passed",
                    "publish": {
                        "status": "published",
                        "public_url": "https://acme.fourmanifold.com/",
                    },
                },
            }
        )

    monkeypatch.setattr(core, "TakyonStore", lambda *a, **k: _FakeStore())
    monkeypatch.setattr(core, "handle_business_refresh_product_surface", _fake_refresh)

    refreshed = worker._refresh_business_surface_after_bootstrap("acme", job_id="job-1")

    assert refreshed["publish"]["status"] == "published"
    assert seen["business"] == "acme"
    assert seen["source_path"] == "product/site"
    assert seen["publish_target"] == "https://acme.fourmanifold.com/"
    assert seen["publish_policy"] == "publish_after_refresh"
    assert seen["idempotency_key"] == "job-1:bootstrap-final-surface-refresh"


def test_refresh_business_surface_after_bootstrap_binds_operator_identity(monkeypatch):
    seen: dict[str, object] = {}

    class _FakeStore:
        def __init__(self, *args, **kwargs):
            seen.setdefault("store_operator_user_id", kwargs.get("operator_user_id"))

        def read(self, *, scope, query, include=None, limit=None):
            assert scope == "business:acme"
            assert query == "summary"
            return {
                "app": {
                    "surface_contract": {
                        "source_path": "product/site",
                        "publish_target": "https://acme.fourmanifold.com/",
                        "publish_policy": "publish_after_refresh",
                    }
                }
            }

    def _fake_refresh(args, **_kw):
        seen.update(args)
        seen["session_user_id"] = get_session_env("TAKYON_SESSION_USER_ID")
        seen["session_business_slug"] = get_session_env("TAKYON_SESSION_BUSINESS_SLUG")
        return json.dumps(
            {
                "success": True,
                "surface_refresh": {
                    "status": "passed",
                    "publish": {
                        "status": "published",
                        "public_url": "https://acme.fourmanifold.com/",
                    },
                },
            }
        )

    monkeypatch.setattr(core, "TakyonStore", _FakeStore)
    monkeypatch.setattr(core, "handle_business_refresh_product_surface", _fake_refresh)

    refreshed = worker._refresh_business_surface_after_bootstrap(
        "acme",
        job_id="job-1",
        operator_user_id="owner-123",
    )

    assert refreshed["publish"]["status"] == "published"
    assert seen["store_operator_user_id"] == "owner-123"
    assert seen["session_user_id"] == "owner-123"
    assert seen["session_business_slug"] == ""


def test_refresh_business_surface_after_bootstrap_skips_missing_source_path(monkeypatch):
    class _FakeStore:
        def read(self, *, scope, query, include=None, limit=None):
            return {"app": {"surface_contract": {"source_path": ""}}}

    monkeypatch.setattr(core, "TakyonStore", lambda *a, **k: _FakeStore())
    monkeypatch.setattr(
        core,
        "handle_business_refresh_product_surface",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("refresh should not be called")),
    )

    assert worker._refresh_business_surface_after_bootstrap("acme", job_id="job-1") is None


def test_zero_cost_turn_reports_zero_cents(monkeypatch, tmp_path):
    import contextlib

    # Stage 4a: the real workspace mount reads the operator-plane DB; this test pins only the
    # zero-cost conversion, so fake the workspace context (in-file pattern).
    @contextlib.contextmanager
    def _fake_workspace(*_a, **_k):
        yield str(tmp_path)

    monkeypatch.setattr(turn_runtime, "_business_workspace_execution_context", _fake_workspace)
    monkeypatch.setattr(worker, "_business_owner_user_id", lambda _slug: "user-123")
    monkeypatch.setattr(worker, "_run_ceo_turn", lambda **_kw: ("", 0.0, "none", True))
    result = worker.ceo_wake_handler(SimpleNamespace(id="job-zero-1", business_slug="acme", payload={}))
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


def test_run_worker_loop_configures_operator_pg_session_before_draining(monkeypatch):
    import psycopg as _psycopg
    import plugins.takyon.runtime_app as runtime_app

    seen: list[str] = []

    class _FakeConn:
        def close(self):
            seen.append("close")

    monkeypatch.setattr(core, "load_takyon_env", lambda *a, **k: None)
    monkeypatch.setattr(runtime_app, "resolve_database_url", lambda *a, **k: "postgresql://fake")
    monkeypatch.setattr(_psycopg, "connect", lambda *a, **k: _FakeConn())
    monkeypatch.setattr(runtime_app, "assert_takyon_pg_role", lambda _conn, plane: seen.append(f"assert:{plane}"))
    monkeypatch.setattr(
        runtime_app,
        "configure_takyon_pg_session",
        lambda _conn, *, bypass: seen.append(f"configure:{bypass}"),
    )

    def _fake_drain_tick(_conn, *, stop, **_kw):
        seen.append("drain")
        stop.set()
        return {
            "dispatched": 0,
            "requeued": 0,
            "usage_holds_released": 0,
            "drained": 0,
            "completed": 0,
            "blocked": 0,
            "failed": 0,
        }

    monkeypatch.setattr(worker, "drain_tick", _fake_drain_tick)

    drained = worker.run_worker_loop(worker_id="w1", once=True)

    assert drained == 0
    # Stage 2: WorkerPool.run() opens a short-lived pool-registration connection first (also
    # role-asserted + configured). The pinned invariant is unchanged: the DRAIN connection is
    # asserted into the operator role and session-configured immediately before drain_tick.
    assert "drain" in seen
    drain_at = seen.index("drain")
    assert seen[drain_at - 2 : drain_at] == ["assert:operator", "configure:True"]


def test_run_worker_loop_uses_multiple_threads_when_configured(monkeypatch):
    seen: list[tuple[str, bool]] = []
    seen_lock = threading.Lock()

    class _FakeConn:
        def close(self):
            return None

    import psycopg as _psycopg

    monkeypatch.setattr(core, "load_takyon_env", lambda *a, **k: None)
    monkeypatch.setattr(worker, "_env_int", lambda _name, _default: 2)
    monkeypatch.setattr(
        __import__("plugins.takyon.runtime_app", fromlist=["resolve_database_url"]),
        "resolve_database_url",
        # Stage 3: resolve_database_url gained the plane= kwarg — the stub must accept it.
        lambda *a, **k: "postgresql://fake",
    )
    monkeypatch.setattr(_psycopg, "connect", lambda *a, **k: _FakeConn())

    def _fake_drain_tick(conn, *, worker_id, dispatch, stop, **_kw):
        del conn
        with seen_lock:
            seen.append((worker_id, dispatch))
            if len(seen) >= 2:
                stop.set()
        return {
            "dispatched": 0,
            "requeued": 0,
            "drained": 0,
            "completed": 0,
            "blocked": 0,
            "failed": 0,
        }

    monkeypatch.setattr(worker, "drain_tick", _fake_drain_tick)

    drained = worker.run_worker_loop(database_url="postgresql://fake")
    assert drained == 0
    assert len({item[0] for item in seen}) == 2
    assert sum(1 for _worker_id, dispatch in seen if dispatch) == 1


# ---------------------------------------------------------------------------
# _business_owner_user_id — the single chokepoint every worker handler uses to
# bind operator identity for a create-time business. It must (1) return the
# durable owner, (2) tolerate brief cross-connection read-after-write lag with a
# bounded retry, and (3) FAIL LOUDLY (never return "") when the owner cannot be
# resolved — an empty bind is the upstream cause of the build-time
# "operator identity required" / "business:<slug> does not exist" failures.
# Pure unit (no PG): the store is faked.
# ---------------------------------------------------------------------------


def _install_fake_owner_store(monkeypatch, attempts: list):
    """attempts: list of callables; each call to _ensure_business pops the next.
    A callable returns the business dict or raises."""

    class _FakeConn:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    class _FakeStore:
        def _connect(self):
            return _FakeConn()

        def _ensure_business(self, conn, slug):
            return attempts.pop(0)(slug)

    monkeypatch.setattr(core, "TakyonStore", lambda *a, **k: _FakeStore())
    # Never actually sleep in the retry window.
    monkeypatch.setattr(worker.time, "sleep", lambda *_a, **_k: None)


def test_business_owner_user_id_returns_durable_owner(monkeypatch):
    _install_fake_owner_store(
        monkeypatch,
        [lambda slug: {"owner_user_id": "user-123"}],
    )
    assert worker._business_owner_user_id("acme") == "user-123"


def test_business_owner_user_id_retries_past_read_after_write_lag(monkeypatch):
    # First read: row not yet visible (the durability race). Second: owner present.
    def _missing(slug):
        raise core.TakyonError(f"business not found: {slug}")

    _install_fake_owner_store(
        monkeypatch,
        [_missing, lambda slug: {"owner_user_id": "user-xyz"}],
    )
    assert worker._business_owner_user_id("acme") == "user-xyz"


def test_business_owner_user_id_fails_loud_on_empty_owner(monkeypatch):
    # Row exists every attempt but carries no owner — must raise, never bind "".
    _install_fake_owner_store(
        monkeypatch,
        [lambda slug: {"owner_user_id": ""} for _ in range(5)],
    )
    with pytest.raises(core.TakyonError) as exc:
        worker._business_owner_user_id("acme")
    assert "acme" in str(exc.value)
    assert "operator identity" in str(exc.value).lower() or "owner_user_id" in str(exc.value)


def test_business_owner_user_id_fails_loud_when_row_never_appears(monkeypatch):
    def _missing(slug):
        raise core.TakyonError(f"business not found: {slug}")

    _install_fake_owner_store(monkeypatch, [_missing for _ in range(5)])
    with pytest.raises(core.TakyonError) as exc:
        worker._business_owner_user_id("acme")
    assert "acme" in str(exc.value)


# ── ceo_bootstrap_handler done-gate: a completed/published bootstrap must NOT requeue ──────────────
#
# Regression guard for the build-loop bug: a CEO bootstrap turn that finished cleanly and published
# the product site was requeued by a raising POST-TURN step (observed on business "simple": the turn
# ended at finish_reason=stop, then the handler raised JobNotRunning and run_one re-ran the whole
# 5-minute Docker build, starving the single build lane). The handler must treat "turn completed OR
# site published" as DONE and make every post-turn step (surface refresh, wake-cron commit, receipt
# events) non-fatal, so a finished build settles+completes instead of looping.


class _BootstrapStubStore:
    """Minimal TakyonStore stand-in for ceo_bootstrap_handler unit tests."""

    def __init__(self, *, goal: str = "do the thing") -> None:
        self.goal = goal
        self.commits: list[dict[str, Any]] = []

    def read(self, *_, **__) -> dict[str, Any]:
        return {"business": {"name": "Acme", "goal": self.goal}}

    def commit(self, **kwargs) -> dict[str, Any]:
        self.commits.append(kwargs)
        return {"ok": True}


def _install_bootstrap_handler_stubs(
    monkeypatch,
    *,
    turn_completed: bool,
    surface_refresh: Any,
    run_turn=None,
    goal: str = "do the thing",
):
    """Patch every heavy collaborator of ceo_bootstrap_handler so it runs in-process without a DB,
    workspace, agent, or network. Returns the dict capturing what the handler did."""
    import contextlib

    from plugins.takyon import cli as takyon_cli
    import gateway.session_context as session_context

    captured: dict[str, Any] = {"events": [], "refresh_calls": 0}
    store = _BootstrapStubStore(goal=goal)

    monkeypatch.setattr(core, "TakyonStore", lambda *a, **k: store)

    @contextlib.contextmanager
    def _fake_bound_op(*_a, **_k):
        yield

    monkeypatch.setattr(core, "_bound_operator_task_context", _fake_bound_op)

    @contextlib.contextmanager
    def _fake_workspace(*_a, **_k):
        yield "/tmp/fake-workspace"

    monkeypatch.setattr(turn_runtime, "_business_workspace_execution_context", _fake_workspace)
    monkeypatch.setattr(
        takyon_cli,
        "_ceo_bootstrap_turn_config",
        lambda *a, **k: {
            "user_prompt": "Bootstrap business now.",
            "ephemeral_system_prompt": "CEO prompt",
            "enabled_toolsets": ["takyon", "web", "skills"],
        },
    )
    monkeypatch.setattr(session_context, "set_session_vars", lambda **_k: [])
    monkeypatch.setattr(session_context, "clear_session_vars", lambda *_a, **_k: None)
    monkeypatch.setattr(worker, "_business_owner_user_id", lambda _slug: "user-123")

    def _default_turn(*, slug, **_kw):
        return "Bootstrap complete. Live at https://acme.coscale.app/", 1.0, "exact", turn_completed

    monkeypatch.setattr(worker, "_run_ceo_turn", run_turn or _default_turn)

    def _fake_refresh(slug, *, job_id, operator_user_id=None):
        captured["refresh_calls"] += 1
        if isinstance(surface_refresh, BaseException):
            raise surface_refresh
        if callable(surface_refresh):
            return surface_refresh()
        return surface_refresh

    monkeypatch.setattr(worker, "_refresh_business_surface_after_bootstrap", _fake_refresh)

    def _capture_event(slug, *, kind, status, **_kw):
        captured["events"].append((status, kind))

    monkeypatch.setattr(worker, "_record_runtime_event", _capture_event)
    captured["store"] = store
    return captured


def test_ceo_bootstrap_handler_binds_owner_before_loading_summary(monkeypatch):
    import contextlib

    from plugins.takyon import cli as takyon_cli
    import gateway.session_context as session_context

    seen: dict[str, Any] = {}

    class _FakeStore:
        def __init__(self, *args, **kwargs):
            self.operator_user_id = kwargs.get("operator_user_id")
            seen["store_operator_user_id"] = self.operator_user_id

        def read(self, *, scope, query, include=None, limit=None):
            assert scope == "business:acme"
            assert query == "summary"
            if self.operator_user_id != "user-123":
                raise AssertionError("summary read before owner binding")
            return {"business": {"name": "Acme", "goal": "do the thing"}}

        def commit(self, **_kwargs) -> dict[str, Any]:
            return {"ok": True}

    @contextlib.contextmanager
    def _fake_workspace(*_a, **_k):
        yield "/tmp/fake-workspace"

    @contextlib.contextmanager
    def _fake_bound_op(*_a, **_k):
        yield

    monkeypatch.setattr(core, "TakyonStore", _FakeStore)
    monkeypatch.setattr(core, "_bound_operator_task_context", _fake_bound_op)
    monkeypatch.setattr(worker, "_business_owner_user_id", lambda _slug: "user-123")
    monkeypatch.setattr(turn_runtime, "_business_workspace_execution_context", _fake_workspace)
    monkeypatch.setattr(
        takyon_cli,
        "_ceo_bootstrap_turn_config",
        lambda *a, **k: {
            "user_prompt": "Bootstrap business now.",
            "ephemeral_system_prompt": "CEO prompt",
            "enabled_toolsets": ["takyon", "web", "skills"],
        },
    )
    monkeypatch.setattr(session_context, "set_session_vars", lambda **_k: [])
    monkeypatch.setattr(session_context, "clear_session_vars", lambda *_a, **_k: None)
    monkeypatch.setattr(worker, "_record_runtime_event", lambda *_a, **_k: None)
    monkeypatch.setattr(worker, "_run_ceo_turn", lambda **_kw: ("ok", 0.0, "none", True))
    monkeypatch.setattr(
        worker,
        "_refresh_business_surface_after_bootstrap",
        lambda *_a, **_k: {"publish": {"status": "published"}},
    )

    result = worker.ceo_bootstrap_handler(
        SimpleNamespace(id="job-1", business_slug="acme", payload={})
    )

    assert result.result["business_slug"] == "acme"
    assert seen["store_operator_user_id"] == "user-123"


def test_bootstrap_completed_and_published_job_completes_not_requeued(monkeypatch):
    # Clean turn (turn_completed=True) + a published surface refresh: the handler returns a
    # JobRunResult (run_one will settle+complete it). It must NOT raise, so the build never requeues.
    captured = _install_bootstrap_handler_stubs(
        monkeypatch,
        turn_completed=True,
        surface_refresh={"publish": {"status": "published", "public_url": "https://acme.coscale.app/"}},
    )
    job = SimpleNamespace(
        id="job-abc-123",
        business_slug="acme",
        payload={"schedule": "every 6h"},
    )

    result = worker.ceo_bootstrap_handler(job)

    assert isinstance(result, jobs.JobRunResult)
    assert result.result["business_slug"] == "acme"
    assert result.actual_cost_cents == 100  # $1.00 → 100c
    # Wake schedule was committed and the final "completed" receipt event fired.
    assert captured["store"].commits, "wake-cron schedule should have been committed"
    assert ("completed", "ceo_bootstrap") in captured["events"]


def test_bootstrap_published_but_turn_capped_completes_not_requeued(monkeypatch):
    # Turn hit the iteration cap (turn_completed=False) but the site PUBLISHED → done-gate is met by
    # publication. Must complete, not requeue (this is the capped-but-live case).
    _install_bootstrap_handler_stubs(
        monkeypatch,
        turn_completed=False,
        surface_refresh={"publish": {"status": "published", "public_url": "https://acme.coscale.app/"}},
    )
    job = SimpleNamespace(id="job-abc-456", business_slug="acme", payload={})

    result = worker.ceo_bootstrap_handler(job)
    assert isinstance(result, jobs.JobRunResult)


def test_bootstrap_incomplete_workflow_continues_in_same_job_and_preserves_identity(monkeypatch):
    prompts: list[str] = []
    completion_checks = iter([False, True])

    def _turn(**kwargs):
        prompts.append(kwargs["user_prompt"])
        return "continue", 0.10, "exact", True

    _install_bootstrap_handler_stubs(
        monkeypatch,
        turn_completed=True,
        run_turn=_turn,
        goal="Build a proposal SaaS where customers generate, save, revise, and delete proposals.",
        surface_refresh={"publish": {"status": "published", "public_url": "https://acme.coscale.app/"}},
    )
    monkeypatch.setattr(
        worker,
        "_bootstrap_has_durable_live_product",
        lambda *_a, **_k: next(completion_checks),
    )
    monkeypatch.setattr(worker, "_bootstrap_real_http_actions", lambda *_a, **_k: {"proposal"})

    result = worker.ceo_bootstrap_handler(
        SimpleNamespace(id="job-same-run", business_slug="acme", payload={})
    )

    assert isinstance(result, jobs.JobRunResult)
    assert len(prompts) == 2
    assert "SAME in-progress bootstrap" in prompts[1]
    assert "Preserve the canonical business/product name" in prompts[1]
    assert "do not restart, rebrand, or redo completed work" in prompts[1]


def test_bootstrap_passes_bounded_runtime_and_durable_publish_probe(monkeypatch):
    captured_turn: dict[str, Any] = {}

    def _bounded_turn(**kwargs):
        captured_turn.update(kwargs)
        return "bounded after publish", 0.25, "exact", False

    _install_bootstrap_handler_stubs(
        monkeypatch,
        turn_completed=False,
        surface_refresh={"publish": {"status": "published", "public_url": "https://acme.coscale.app/"}},
        run_turn=_bounded_turn,
    )
    monkeypatch.setattr(worker, "_bootstrap_has_durable_live_product", lambda *_a, **_k: True)
    monkeypatch.setattr(worker, "_bootstrap_has_live_delegated_child", lambda *_a, **_k: True)

    result = worker.ceo_bootstrap_handler(
        SimpleNamespace(id="job-bounded-live", business_slug="acme", payload={})
    )

    assert isinstance(result, jobs.JobRunResult)
    assert captured_turn["wall_clock_limit"] == worker._DEFAULT_BOOTSTRAP_WALL_TIMEOUT
    assert captured_turn["completion_grace_seconds"] == worker._DEFAULT_BOOTSTRAP_POST_PUBLISH_GRACE
    assert captured_turn["completion_probe"]() is True
    assert captured_turn["external_activity_probe"]() is True


def test_bootstrap_child_liveness_uses_fresh_durable_job_claim():
    import contextlib

    seen: dict[str, Any] = {}

    class _Conn:
        def execute(self, sql, params):
            seen["sql"] = sql
            seen["params"] = params
            return self

        def fetchone(self):
            return (1,)

    class _Store:
        @contextlib.contextmanager
        def _connect(self):
            yield _Conn()

    assert worker._bootstrap_has_live_delegated_child(_Store(), "acme") is True
    assert "claude.agent_task" in seen["sql"]
    assert "locked_at" in seen["sql"]
    assert seen["params"] == ("acme", 60.0)


def test_bootstrap_post_turn_surface_refresh_exception_does_not_requeue(monkeypatch):
    # The post-turn surface refresh RAISES (e.g. a lost job claim → JobNotRunning, or a transient DB
    # blip). The turn completed cleanly, so the build is done: the handler must swallow the post-turn
    # exception and still return a JobRunResult. A raise here would re-run the whole build.
    captured = _install_bootstrap_handler_stubs(
        monkeypatch,
        turn_completed=True,
        surface_refresh=jobs.JobNotRunning("job-abc-789"),  # str(exc) == the bare job id, as observed
    )
    job = SimpleNamespace(id="job-abc-789", business_slug="acme", payload={"schedule": "every 6h"})

    result = worker.ceo_bootstrap_handler(job)

    assert isinstance(result, jobs.JobRunResult)
    assert captured["refresh_calls"] == 1
    assert ("completed", "ceo_bootstrap") in captured["events"]


def test_bootstrap_post_turn_wake_commit_exception_does_not_requeue(monkeypatch):
    # The wake-cron store.commit RAISES after a clean, published turn. Non-fatal: the handler must
    # still complete (wake scheduling is bookkeeping; a finished published build is done).
    captured = _install_bootstrap_handler_stubs(
        monkeypatch,
        turn_completed=True,
        surface_refresh={"publish": {"status": "published"}},
    )

    def _boom(**_kw):
        raise RuntimeError("transient wake-cron commit failure")

    captured["store"].commit = _boom  # type: ignore[method-assign]
    job = SimpleNamespace(id="job-abc-999", business_slug="acme", payload={"schedule": "every 6h"})

    result = worker.ceo_bootstrap_handler(job)

    assert isinstance(result, jobs.JobRunResult)
    assert ("completed", "ceo_bootstrap") in captured["events"]


def test_bootstrap_capped_before_publish_still_requeues(monkeypatch):
    # The ONLY genuine requeue: the turn capped out (turn_completed=False) AND the site never
    # published. That is a real incomplete — the handler must raise so run_one requeues for
    # continuation. (Guards against the done-gate going too far and never retrying real failures.)
    _install_bootstrap_handler_stubs(
        monkeypatch,
        turn_completed=False,
        surface_refresh={"publish": {"status": "build_failed", "blocker": "vite build error"}},
    )
    job = SimpleNamespace(id="job-abc-000", business_slug="acme", payload={})

    with pytest.raises(RuntimeError) as exc:
        worker.ceo_bootstrap_handler(job)
    assert "iteration budget" in str(exc.value)


def test_bootstrap_timeout_waits_for_delegated_child_before_outer_requeue(monkeypatch):
    captured = _install_bootstrap_handler_stubs(
        monkeypatch,
        turn_completed=False,
        surface_refresh={"publish": {"status": "unknown"}},
        run_turn=lambda **_kw: (_ for _ in ()).throw(
            TimeoutError("CEO wake for business:acme idle past 600s inactivity limit")
        ),
    )
    child_checks: list[bool] = []
    child_states = iter((True, False))
    monkeypatch.setattr(
        worker,
        "_best_effort_terminalize_owned_timeout",
        lambda *_args, **_kwargs: pytest.fail("handler must not requeue itself before it exits"),
    )
    monkeypatch.setattr(
        worker,
        "_bootstrap_has_live_delegated_child",
        lambda *_args, **_kwargs: child_checks.append(state := next(child_states)) or state,
    )
    sleeps: list[float] = []
    monkeypatch.setattr(worker.time, "sleep", lambda seconds: sleeps.append(float(seconds)))
    job = SimpleNamespace(id="job-timeout", business_slug="acme", payload={}, locked_by="w1")

    with pytest.raises(TimeoutError):
        worker.ceo_bootstrap_handler(job)

    assert child_checks == [True, False]
    assert sleeps == [5.0]
    assert ("failed", "ceo_bootstrap") in captured["events"]


def test_bootstrap_workflow_goal_published_without_real_action_requeues(monkeypatch):
    _install_bootstrap_handler_stubs(
        monkeypatch,
        turn_completed=False,
        surface_refresh={"publish": {"status": "published", "public_url": "https://acme.coscale.app/"}},
        goal=(
            "Build a service that helps customers upload messy contractor notes and receive a "
            "quote-ready scope of work in the signed-in app."
        ),
    )
    monkeypatch.setattr(worker, "_bootstrap_real_http_actions", lambda *_a, **_k: set())
    job = SimpleNamespace(id="job-workflow-missing", business_slug="acme", payload={})

    with pytest.raises(RuntimeError) as exc:
        worker.ceo_bootstrap_handler(job)
    assert "never materialized a real /app workflow action" in str(exc.value)


def test_bootstrap_workflow_goal_published_with_real_action_completes(monkeypatch):
    _install_bootstrap_handler_stubs(
        monkeypatch,
        turn_completed=False,
        surface_refresh={"publish": {"status": "published", "public_url": "https://acme.coscale.app/"}},
        goal=(
            "Build a service that helps customers upload messy contractor notes and receive a "
            "quote-ready scope of work in the signed-in app."
        ),
    )
    monkeypatch.setattr(worker, "_bootstrap_real_http_actions", lambda *_a, **_k: {"generate-quote"})
    job = SimpleNamespace(id="job-workflow-ok", business_slug="acme", payload={})

    result = worker.ceo_bootstrap_handler(job)
    assert isinstance(result, jobs.JobRunResult)


def test_channel_tracked_link_tags_without_clobbering_and_meta_delegates():
    tagged = core._channel_tracked_link(
        "https://acme.example.com/welcome",
        source="x",
        medium="social",
        campaign_key="acme",
    )
    assert "utm_source=x" in tagged
    assert "utm_medium=social" in tagged
    assert "utm_campaign=acme" in tagged
    # Caller-set UTM params are not clobbered.
    preset = core._channel_tracked_link(
        "https://acme.example.com/?utm_source=newsletter",
        source="x",
        medium="social",
        campaign_key="acme",
    )
    assert "utm_source=newsletter" in preset
    assert "utm_source=x" not in preset


def test_compose_x_link_reply_formats_and_truncates():
    assert worker._compose_x_link_reply("https://acme.example.com/") == "https://acme.example.com/"
    assert (
        worker._compose_x_link_reply("https://acme.example.com/", label="Try it")
        == "Try it: https://acme.example.com/"
    )
    long_url = "https://acme.example.com/" + "a" * 400
    assert len(worker._compose_x_link_reply(long_url, label="Try it")) <= worker._X_POST_CHAR_LIMIT


def _stub_x_credits(monkeypatch):
    monkeypatch.setattr(
        core,
        "_reserve_creative_credits",
        lambda *args, **kwargs: {
            "requested_credits": 1,
            "budget_bucket": "x",
            "channel_budget": {"allocated_credits": 1, "used_credits": 0, "reserved_credits": 1, "remaining_credits": 0},
        },
    )
    monkeypatch.setattr(
        core,
        "_commit_creative_credits",
        lambda *args, **kwargs: {
            "actual_credits": 1,
            "balance_credits": 9,
            "reserved_credits": 0,
            "budget_bucket": "x",
            "channel_budget": {"allocated_credits": 1, "used_credits": 1, "reserved_credits": 0, "remaining_credits": 0},
        },
    )
    monkeypatch.setattr(
        core,
        "_release_creative_credits",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("release should not run on successful publish")),
    )


def test_x_publish_outreach_handler_posts_destination_link_as_reply(monkeypatch, tmp_path):
    _stub_x_credits(monkeypatch)
    calls: list[dict[str, Any]] = []
    responses = iter(
        (
            {"data": {"id": "tweet-root"}},
            {"data": {"id": "tweet-link"}},
            {"data": {"username": "sharedacct"}},
        )
    )

    def _fake_twitter_execute(tool_slug, *, arguments=None, timeout=0.0, **_kwargs):
        calls.append({"tool_slug": tool_slug, "arguments": dict(arguments or {}), "timeout": timeout})
        return next(responses)

    monkeypatch.setattr(worker.composio_distribution, "twitter_execute_tool", _fake_twitter_execute)
    monkeypatch.setattr(
        worker,
        "_record_x_publish_result",
        lambda slug, **kwargs: {"artifact": "a.md", "receipt": "r.json"},
    )
    monkeypatch.setattr(worker, "_update_work_request", lambda *args, **kwargs: None)

    dest = "https://acme.example.com/?utm_source=x&utm_medium=social&utm_campaign=acme"
    result = worker.x_publish_outreach_handler(
        SimpleNamespace(
            id="job-link",
            business_slug="acme",
            payload={"body": "Ship it", "provider": "x", "destination_url": dest},
        )
    )

    assert [c["tool_slug"] for c in calls] == [
        "TWITTER_CREATION_OF_A_POST",
        "TWITTER_CREATION_OF_A_POST",
        "TWITTER_USER_LOOKUP_ME",
    ]
    # First post is the body; second post is the link reply threaded under the root tweet.
    assert calls[0]["arguments"] == {"text": "Ship it"}
    assert calls[1]["arguments"]["text"] == dest
    assert calls[1]["arguments"]["reply_in_reply_to_tweet_id"] == "tweet-root"
    # The recorded root post stays the body tweet, not the link reply.
    assert result.result["post_id"] == "tweet-root"


def test_x_publish_outreach_handler_skips_link_reply_when_link_already_in_body(monkeypatch, tmp_path):
    _stub_x_credits(monkeypatch)
    calls: list[dict[str, Any]] = []
    responses = iter(
        (
            {"data": {"id": "tweet-root"}},
            {"data": {"username": "sharedacct"}},
        )
    )

    def _fake_twitter_execute(tool_slug, *, arguments=None, timeout=0.0, **_kwargs):
        calls.append({"tool_slug": tool_slug, "arguments": dict(arguments or {}), "timeout": timeout})
        return next(responses)

    monkeypatch.setattr(worker.composio_distribution, "twitter_execute_tool", _fake_twitter_execute)
    monkeypatch.setattr(
        worker,
        "_record_x_publish_result",
        lambda slug, **kwargs: {"artifact": "a.md", "receipt": "r.json"},
    )
    monkeypatch.setattr(worker, "_update_work_request", lambda *args, **kwargs: None)

    worker.x_publish_outreach_handler(
        SimpleNamespace(
            id="job-inbody",
            business_slug="acme",
            payload={
                "body": "Check it out https://acme.example.com",
                "provider": "x",
                "destination_url": "https://acme.example.com/?utm_source=x&utm_medium=social&utm_campaign=acme",
            },
        )
    )

    # Only the body post + the username lookup — no separate link reply, since the link is in body.
    assert [c["tool_slug"] for c in calls] == [
        "TWITTER_CREATION_OF_A_POST",
        "TWITTER_USER_LOOKUP_ME",
    ]


def test_x_publish_outreach_handler_posts_link_reply_when_body_only_contains_destination_prefix(monkeypatch, tmp_path):
    _stub_x_credits(monkeypatch)
    calls: list[dict[str, Any]] = []
    responses = iter(
        (
            {"data": {"id": "tweet-root"}},
            {"data": {"id": "tweet-link"}},
            {"data": {"username": "sharedacct"}},
        )
    )

    def _fake_twitter_execute(tool_slug, *, arguments=None, timeout=0.0, **_kwargs):
        calls.append({"tool_slug": tool_slug, "arguments": dict(arguments or {}), "timeout": timeout})
        return next(responses)

    monkeypatch.setattr(worker.composio_distribution, "twitter_execute_tool", _fake_twitter_execute)
    monkeypatch.setattr(
        worker,
        "_record_x_publish_result",
        lambda slug, **kwargs: {"artifact": "a.md", "receipt": "r.json"},
    )
    monkeypatch.setattr(worker, "_update_work_request", lambda *args, **kwargs: None)

    worker.x_publish_outreach_handler(
        SimpleNamespace(
            id="job-prefix-only",
            business_slug="acme",
            payload={
                "body": "Check it out https://acme.example.com/offer",
                "provider": "x",
                "destination_url": "https://acme.example.com/?utm_source=x&utm_medium=social&utm_campaign=acme",
            },
        )
    )

    assert [c["tool_slug"] for c in calls] == [
        "TWITTER_CREATION_OF_A_POST",
        "TWITTER_CREATION_OF_A_POST",
        "TWITTER_USER_LOOKUP_ME",
    ]
    assert calls[1]["arguments"]["text"] == "https://acme.example.com/?utm_source=x&utm_medium=social&utm_campaign=acme"
    assert calls[1]["arguments"]["reply_in_reply_to_tweet_id"] == "tweet-root"


def test_worker_pool_run_passes_pool_identity_to_drain(monkeypatch):
    """Stage 2 regression pin: the run() drain loop must present the pool's claim identity —
    without it an exclusive session pool can never claim its own strictly-reserved jobs
    (stranding them while the pool's lease is alive)."""
    import psycopg as _psycopg

    from plugins.takyon import core, runtime_app
    from plugins.takyon import worker_pool as wp

    seen: list[dict] = []

    class _FakeConn:
        def close(self):
            pass

        def execute(self, *_a, **_k):  # pool registration touches worker_pools
            class _Cur:
                def fetchone(self):
                    return ("x",)

            return _Cur()

    monkeypatch.setattr(core, "load_takyon_env", lambda *a, **k: None)
    monkeypatch.setattr(runtime_app, "resolve_database_url", lambda *a, **k: "postgresql://fake")
    monkeypatch.setattr(_psycopg, "connect", lambda *a, **k: _FakeConn())
    monkeypatch.setattr(runtime_app, "assert_takyon_pg_role", lambda *_a, **_k: None)
    monkeypatch.setattr(runtime_app, "configure_takyon_pg_session", lambda *_a, **_k: None)

    def _fake_drain_tick(_conn, *, stop, **kw):
        seen.append(kw)
        stop.set()
        return {"dispatched": 0, "requeued": 0, "usage_holds_released": 0, "drained": 0,
                "completed": 0, "blocked": 0, "failed": 0}

    monkeypatch.setattr(worker, "drain_tick", _fake_drain_tick)

    pool = wp.WorkerPool(worker_id="w-ident", pool_id="pool-ident", exclusive=True, once=True)
    pool.run()

    assert seen, "drain_tick never called"
    assert seen[0]["claim_pool_id"] == "pool-ident"
    assert seen[0]["exclusive_pool"] is True


def test_worker_pool_heartbeat_thread_starts_even_if_initial_registration_fails(monkeypatch):
    """Stage 2 regression pin (found live: RLS denied the first registration and the pool then
    NEVER registered): the heartbeat loop is the registration retry path, so it must start
    whenever registration is wanted — not only after a successful first attempt."""
    import psycopg as _psycopg

    from plugins.takyon import core, runtime_app
    from plugins.takyon import worker_pool as wp

    class _FailingConn:
        def close(self):
            pass

        def execute(self, *_a, **_k):
            raise RuntimeError("rls denied")

    monkeypatch.setattr(core, "load_takyon_env", lambda *a, **k: None)
    monkeypatch.setattr(runtime_app, "resolve_database_url", lambda *a, **k: "postgresql://fake")
    monkeypatch.setattr(_psycopg, "connect", lambda *a, **k: _FailingConn())
    monkeypatch.setattr(runtime_app, "assert_takyon_pg_role", lambda *_a, **_k: None)
    monkeypatch.setattr(runtime_app, "configure_takyon_pg_session", lambda *_a, **_k: None)

    started: list[str] = []
    real_thread = threading.Thread

    class _SpyThread(real_thread):
        def start(self):
            started.append(self.name)
            if self.name == "takyon-pool-heartbeat":
                return  # don't actually run the loop in the test
            return super().start()

    monkeypatch.setattr(threading, "Thread", _SpyThread)

    def _fake_drain_tick(_conn, *, stop, **_kw):
        stop.set()
        return {"dispatched": 0, "requeued": 0, "usage_holds_released": 0, "drained": 0,
                "completed": 0, "blocked": 0, "failed": 0}

    monkeypatch.setattr(worker, "drain_tick", _fake_drain_tick)

    pool = wp.WorkerPool(worker_id="w-heal", pool_id="pool-heal", once=True)
    pool.run()

    assert "takyon-pool-heartbeat" in started, (
        "heartbeat/self-heal thread must start even when initial registration fails"
    )
