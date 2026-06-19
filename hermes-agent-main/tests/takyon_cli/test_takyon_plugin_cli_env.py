from __future__ import annotations

import os


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
