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
import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

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

    assert result is None
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


@pytest.mark.parametrize("requirement", ("x", "meta", "reddit", "reddit_ads"))
def test_composio_requirement_reports_missing_api_key(monkeypatch, requirement):
    monkeypatch.delenv("COMPOSIO_API_KEY", raising=False)
    monkeypatch.setattr(core.safebox, "read_env_backed_value", lambda _key: "")
    assert core._missing_env_for_requirement(requirement) == ["COMPOSIO_API_KEY"]


def test_ceo_wake_handler_reports_true_cost_in_cents(monkeypatch):
    # The handler converts the turn's true USD cost to integer cents for settlement and packages the
    # response. $0.0734 → 7 cents.
    captured: dict = {}

    def _fake_turn(*, slug, system_prompt, user_prompt, toolsets, max_turns, inactivity_limit, **_kw):
        captured.update(slug=slug, toolsets=toolsets, max_turns=max_turns)
        return "the CEO did things", 0.0734, "exact", True

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
    storage.sync_up(backend, "acme", seed)

    monkeypatch.setenv("TAKYON_HOME", str(tmp_path))
    monkeypatch.setenv("TAKYON_STORAGE_BACKEND", "local")
    monkeypatch.setenv("TAKYON_STORAGE_LOCAL_DIR", str(tmp_path / "bucket"))
    monkeypatch.setattr(core, "_store", lambda: core.TakyonStore(tmp_path, operator_user_id=""))

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
    storage.sync_up(backend, "acme", seed)

    monkeypatch.setenv("TAKYON_HOME", str(tmp_path))
    monkeypatch.setenv("TAKYON_STORAGE_BACKEND", "local")
    monkeypatch.setenv("TAKYON_STORAGE_LOCAL_DIR", str(tmp_path / "bucket"))
    monkeypatch.setattr(core, "_store", lambda: core.TakyonStore(tmp_path, operator_user_id=""))

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


def test_ceo_wake_handler_honors_payload_max_turns(monkeypatch):
    captured: dict = {}

    def _fake_turn(*, max_turns, **_kw):
        captured["max_turns"] = max_turns
        return "", 0.0, "none", True

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
        return "ok", 0.0, "none", True

    monkeypatch.setattr(worker, "_run_ceo_turn", _fake_turn)
    result = worker.ceo_wake_handler(SimpleNamespace(business_slug="acme", payload={}))

    assert result.result["business_slug"] == "acme"
    assert seen["user_id"] == "user-123"
    assert seen["workspace_root"]
    assert not Path(seen["workspace_root"]).exists()

    resumed = tmp_path / "resumed"
    storage.sync_down(backend, "acme", resumed)
    assert (resumed / "metrics" / "summary.md").read_text() == "fresh\n"


def test_ceo_wake_handler_syncs_partial_workspace_on_failed_turn(monkeypatch, tmp_path):
    backend = storage.LocalStorageBackend(tmp_path / "bucket")
    seed = tmp_path / "seed"
    (seed / "research").mkdir(parents=True, exist_ok=True)
    (seed / "research" / "strategy.md").write_text("seed\n")
    storage.sync_up(backend, "acme", seed)

    monkeypatch.setenv("TAKYON_STORAGE_BACKEND", "local")
    monkeypatch.setenv("TAKYON_STORAGE_LOCAL_DIR", str(tmp_path / "bucket"))
    monkeypatch.setattr(worker, "_business_owner_user_id", lambda _slug: "user-123")

    def _fake_turn(*, slug, **_kw):
        workspace_root = get_session_env("TAKYON_SESSION_WORKSPACE_ROOT")
        workspace = Path(workspace_root) / "businesses" / slug
        (workspace / "product").mkdir(parents=True, exist_ok=True)
        (workspace / "product" / "surface.md").write_text("partial surface\n")
        raise RuntimeError("turn interrupted")

    monkeypatch.setattr(worker, "_run_ceo_turn", _fake_turn)

    with pytest.raises(RuntimeError, match="turn interrupted"):
        worker.ceo_wake_handler(SimpleNamespace(business_slug="acme", payload={}))

    resumed = tmp_path / "resumed"
    storage.sync_down(backend, "acme", resumed)
    assert (resumed / "product" / "surface.md").read_text() == "partial surface\n"


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


def test_zero_cost_turn_reports_zero_cents(monkeypatch):
    monkeypatch.setattr(worker, "_business_owner_user_id", lambda _slug: "user-123")
    monkeypatch.setattr(worker, "_run_ceo_turn", lambda **_kw: ("", 0.0, "none", True))
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
        lambda _database_url=None: "postgresql://fake",
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
