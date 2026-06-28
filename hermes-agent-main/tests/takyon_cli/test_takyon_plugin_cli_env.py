from __future__ import annotations

import os
import sys


def test_run_takyon_command_loads_env_before_store(monkeypatch):
    import plugins.takyon.cli as takyon_cli

    order: list[str] = []

    class _Store:
        def __init__(self, *args, **kwargs):
            order.append(f"store:{bool(os.environ.get('DATABASE_URL'))}")

        def read(self, *, scope, query, **kwargs):
            return {"success": True, "scope": scope, "businesses": []}

    monkeypatch.delenv("DATABASE_URL", raising=False)

    def _fake_load() -> list[str]:
        order.append("env")
        os.environ["DATABASE_URL"] = "postgresql://example.invalid:6543/takyon"
        return ["/tmp/.takyon/.env"]

    monkeypatch.setattr(takyon_cli, "load_takyon_env", _fake_load)
    monkeypatch.setattr(takyon_cli, "TakyonStore", _Store)

    result = takyon_cli.run_takyon_command(["businesses"])

    assert result["success"] is True
    assert order == ["env", "store:True"]


def test_log_follow_flags_are_stripped_from_command_args():
    import plugins.takyon.cli as takyon_cli

    clean, follow_logs = takyon_cli._strip_log_follow_flags(
        ["shell", "homework-solver", "--logs"],
        default=False,
    )
    assert clean == ["shell", "homework-solver"]
    assert follow_logs is True

    clean, follow_logs = takyon_cli._strip_log_follow_flags(
        ["run", "homework-solver", "--follow-logs", "inspect state", "--no-logs"],
        default=True,
    )
    assert clean == ["run", "homework-solver", "inspect state"]
    assert follow_logs is False


def test_shell_command_accepts_trailing_logs_flag(monkeypatch):
    import plugins.takyon.cli as takyon_cli

    captured: dict[str, object] = {}

    class _Store:
        def __init__(self, *args, **kwargs):
            pass

    def fake_shell(**kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(takyon_cli, "load_takyon_env", lambda: [])
    monkeypatch.setattr(takyon_cli, "TakyonStore", _Store)
    monkeypatch.setattr(takyon_cli, "_interactive_shell", fake_shell)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)

    result = takyon_cli.run_takyon_command(["shell", "homework-solver", "--logs"])

    assert result is None
    assert captured["initial_business"] == "homework-solver"
    assert captured["follow_logs"] is True
