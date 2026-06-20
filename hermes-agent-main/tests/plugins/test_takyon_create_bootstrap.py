from __future__ import annotations

from datetime import datetime, timedelta, timezone

import cron.jobs as cron_jobs

from plugins.takyon import cli as takyon_cli
from plugins.takyon.core import TakyonStore


def test_create_enqueues_bootstrap_after_business_persists(monkeypatch):
    monkeypatch.setattr(takyon_cli, "_require_agent_model_config", lambda *args, **kwargs: None)

    observed = {"scheduled_inline": False, "enqueued": None}
    state = {"business": {"slug": "latexflow", "mode": "live"}}

    class FakeStore:
        def __init__(self, *args, **kwargs):
            pass

        def commit(self, *, scope, operations, **kwargs):
            if any((op or {}).get("action") == "cron.ensure_ceo_wakeup" for op in operations or []):
                observed["scheduled_inline"] = True
            return {"results": [{"action": "business.upsert"}]}

        def read(self, *, scope, query, **kwargs):
            assert scope == "business:latexflow"
            assert query == "summary"
            return state

    def fake_enqueue(store, slug, *, goal, mode, schedule, max_turns):
        observed["enqueued"] = {
            "slug": slug,
            "goal": goal,
            "mode": mode,
            "schedule": schedule,
            "max_turns": max_turns,
        }
        return {
            "action": "ceo_bootstrap.enqueue",
            "business": slug,
            "job_id": "job-123",
            "status": "queued",
            "created": True,
            "schedule": schedule or "",
        }

    monkeypatch.setattr(takyon_cli, "TakyonStore", FakeStore)
    monkeypatch.setattr(takyon_cli, "_read_model_config", lambda store: {})
    monkeypatch.setattr(takyon_cli, "_enqueue_pg_ceo_bootstrap", fake_enqueue)
    # This test exercises the enqueue contract, not slug-collision handling. The FakeStore reports the
    # business on read, which the create chokepoint's pre-create _business_exists probe would treat as an
    # existing slug and auto-increment to `latexflow-2`; stub it so the slug under test stays `latexflow`.
    monkeypatch.setattr(takyon_cli, "_business_exists", lambda *_a, **_k: False)
    # The chokepoint also runs the operator balance preflight + seeds free credits against the real
    # control-plane DB (each covered by its own PG-gated suite); stub them so this enqueue unit is hermetic.
    monkeypatch.setattr(takyon_cli, "_operator_create_balance_preflight", lambda *_a, **_k: None)
    monkeypatch.setattr(takyon_cli, "_seed_business_free_credits", lambda *_a, **_k: None)

    result = takyon_cli.run_takyon_command(
        ["create", "--live", "--schedule", "every 6h", "latexflow", "overleaf competitor"],
        model="",
        max_turns=7,
    )

    assert observed["scheduled_inline"] is False
    assert observed["enqueued"] == {
        "slug": "latexflow",
        "goal": "overleaf competitor",
        "mode": "live",
        "schedule": "every 6h",
        "max_turns": 7,
    }
    assert result["success"] is True
    assert result["bootstrap_job"]["job_id"] == "job-123"


def test_create_caps_bootstrap_turn_budget(monkeypatch):
    monkeypatch.setattr(takyon_cli, "_require_agent_model_config", lambda *args, **kwargs: None)

    observed = {"enqueued": None}
    state = {"business": {"slug": "latexflow", "mode": "live"}}

    class FakeStore:
        def __init__(self, *args, **kwargs):
            pass

        def commit(self, *, scope, operations, **kwargs):
            return {"results": [{"action": "business.upsert"}]}

        def read(self, *, scope, query, **kwargs):
            assert scope == "business:latexflow"
            assert query == "summary"
            return state

    def fake_enqueue(store, slug, *, goal, mode, schedule, max_turns):
        observed["enqueued"] = {
            "slug": slug,
            "goal": goal,
            "mode": mode,
            "schedule": schedule,
            "max_turns": max_turns,
        }
        return {
            "action": "ceo_bootstrap.enqueue",
            "business": slug,
            "job_id": "job-123",
            "status": "queued",
            "created": True,
            "schedule": schedule or "",
        }

    monkeypatch.setattr(takyon_cli, "TakyonStore", FakeStore)
    monkeypatch.setattr(takyon_cli, "_read_model_config", lambda store: {})
    monkeypatch.setattr(takyon_cli, "_enqueue_pg_ceo_bootstrap", fake_enqueue)
    # This test exercises the enqueue contract, not slug-collision handling. The FakeStore reports the
    # business on read, which the create chokepoint's pre-create _business_exists probe would treat as an
    # existing slug and auto-increment to `latexflow-2`; stub it so the slug under test stays `latexflow`.
    monkeypatch.setattr(takyon_cli, "_business_exists", lambda *_a, **_k: False)
    # The chokepoint also runs the operator balance preflight + seeds free credits against the real
    # control-plane DB (each covered by its own PG-gated suite); stub them so this enqueue unit is hermetic.
    monkeypatch.setattr(takyon_cli, "_operator_create_balance_preflight", lambda *_a, **_k: None)
    monkeypatch.setattr(takyon_cli, "_seed_business_free_credits", lambda *_a, **_k: None)

    # Request a turn budget well ABOVE the cap so this asserts the capping relationship, not a literal:
    # the enqueued budget must be clamped down to _DEFAULT_BOOTSTRAP_MAX_TURNS (a change-detector pinned
    # to the old literal 20 would silently break every time that cap is retuned).
    takyon_cli.run_takyon_command(
        ["create", "--live", "latexflow", "overleaf competitor"],
        model="",
        max_turns=takyon_cli._DEFAULT_BOOTSTRAP_MAX_TURNS + 50,
    )

    assert observed["enqueued"] == {
        "slug": "latexflow",
        "goal": "overleaf competitor",
        "mode": "live",
        "schedule": "every 6h",
        "max_turns": takyon_cli._DEFAULT_BOOTSTRAP_MAX_TURNS,
    }


def test_ensure_ceo_wakeup_can_defer_first_run(monkeypatch, tmp_path):
    store = TakyonStore(tmp_path)

    class _FakeConn:
        def __enter__(self):
            return object()

        def __exit__(self, exc_type, exc, tb):
            return False

    created: dict[str, object] = {}

    def _fake_create_job(**kwargs):
        created["create_kwargs"] = dict(kwargs)
        return {
            "id": "job-123",
            "schedule_display": "every 360m",
            "next_run_at": "",
        }

    def _fake_update_job(job_id, updates):
        created["update_job_id"] = job_id
        created["update_payload"] = dict(updates)
        return {
            "id": job_id,
            "schedule_display": "every 360m",
            "next_run_at": str(updates.get("next_run_at") or ""),
        }

    monkeypatch.setattr(store, "_connect", lambda: _FakeConn())
    monkeypatch.setattr(store, "_control_blocker", lambda conn, scope: None)
    monkeypatch.setattr("plugins.takyon.core._db_backend", lambda: "sqlite")
    monkeypatch.setattr(cron_jobs, "list_jobs", lambda include_disabled=True: [])
    monkeypatch.setattr(cron_jobs, "create_job", _fake_create_job)
    monkeypatch.setattr(cron_jobs, "update_job", _fake_update_job)

    before = datetime.now(timezone.utc)
    result = store._ensure_ceo_cron(
        "crm",
        schedule="every 6h",
        reason="bootstrap completed and enabled CEO wake loop",
        defer_first_run=True,
    )

    deferred = datetime.fromisoformat(str(result["next_run_at"]))
    assert deferred >= before + timedelta(hours=5, minutes=59)
    assert deferred <= before + timedelta(hours=6, minutes=1)
    assert created["update_job_id"] == "job-123"
    assert created["update_payload"] == {"next_run_at": str(result["next_run_at"])}
    assert result["defer_first_run"] is True
