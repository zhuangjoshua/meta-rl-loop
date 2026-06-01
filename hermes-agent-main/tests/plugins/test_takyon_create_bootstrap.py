from __future__ import annotations

from plugins.takyon import cli as takyon_cli


def test_create_schedules_recurring_wake_after_bootstrap(monkeypatch):
    monkeypatch.setattr(takyon_cli, "_require_agent_model_config", lambda *args, **kwargs: None)

    observed = {"scheduled_before_agent": None, "scheduled_after_return": False}
    state = {"business": {"slug": "latexflow", "mode": "test"}}

    class FakeStore:
        def __init__(self, *args, **kwargs):
            pass

        def commit(self, *, scope, operations, **kwargs):
            if any((op or {}).get("action") == "cron.ensure_ceo_wakeup" for op in operations or []):
                observed["scheduled_after_return"] = True
                return {"results": [{"action": "cron.ensure_ceo_wakeup"}]}
            return {"results": [{"action": "business.upsert"}]}

        def read(self, *, scope, query, **kwargs):
            assert scope == "business:latexflow"
            assert query == "summary"
            return state

    def fake_run_agent(*args, **kwargs):
        assert kwargs.get("current_business") == "latexflow"
        assert kwargs.get("operator_user_id") in {"", None}
        observed["scheduled_before_agent"] = observed["scheduled_after_return"]
        return "bootstrapped"

    monkeypatch.setattr(takyon_cli, "TakyonStore", FakeStore)
    monkeypatch.setattr(takyon_cli, "_read_model_config", lambda store: {})
    monkeypatch.setattr(takyon_cli, "_run_agent", fake_run_agent)

    result = takyon_cli.run_takyon_command(
        ["create", "--test", "--schedule", "every 6h", "latexflow", "overleaf competitor"],
        model="",
        max_turns=1,
    )

    assert observed["scheduled_before_agent"] is False
    assert observed["scheduled_after_return"] is True
    assert result["success"] is True
    assert result["agent_response"] == "bootstrapped"
