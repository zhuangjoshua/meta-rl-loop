from __future__ import annotations

from plugins.takyon import cli as takyon_cli


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

    takyon_cli.run_takyon_command(
        ["create", "--live", "latexflow", "overleaf competitor"],
        model="",
        max_turns=30,
    )

    assert observed["enqueued"] == {
        "slug": "latexflow",
        "goal": "overleaf competitor",
        "mode": "live",
        "schedule": "every 6h",
        "max_turns": 20,
    }
