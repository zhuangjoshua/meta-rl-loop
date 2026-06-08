from __future__ import annotations

import json
import types
from pathlib import Path

from plugins.takyon import core as takyon_core
from plugins.takyon.core import handle_business_claude_agent_task


class _FakeConn:
    def __enter__(self):
        return object()

    def __exit__(self, exc_type, exc, tb):
        return False


class _FakeStore:
    def __init__(self, root: Path):
        self.root = root

    def _connect(self):
        return _FakeConn()

    def _ensure_business(self, conn, business: str):
        return {"owner_user_id": "user-123", "work_focus": "all", "slug": business}

    def _active_operator_user_id(self):
        return "user-123"

    def read(self, *, scope, query, include=None, limit=None):
        if query == "summary":
            return {
                "app": {
                    "surface_contract": {
                        "source_path": "product/site",
                        "publish_policy": "publish_after_refresh",
                    }
                }
            }
        return {}

    def _business_root(self, business: str):
        return self.root / "businesses" / business

    def _resolve_business_file(self, business: str, rel: str, **_kwargs):
        return self._business_root(business) / rel

    def _sync_business_workspace_remote(self, business: str):
        return None

    def commit(self, **_kwargs):
        return {"success": True}


def test_claude_agent_task_uses_broader_defaults_for_product_site_work(tmp_path, monkeypatch):
    monkeypatch.setenv("TAKYON_HOME", str(tmp_path))
    captured: dict[str, object] = {}

    def fake_process(*, payload: dict[str, object], **kwargs):
        captured["payload"] = payload
        Path(str(payload["cwd"]), "index.html").write_text("<h1>Latexflow</h1>\n", encoding="utf-8")
        return types.SimpleNamespace(returncode=0, stdout=json.dumps({"success": True, "summary": "ok"}), stderr="")

    monkeypatch.setattr(takyon_core, "_store", lambda: _FakeStore(tmp_path))
    monkeypatch.setattr(takyon_core, "_session_business_slug", lambda: "latexflow")
    monkeypatch.setattr(takyon_core, "_require_api_access", lambda *args, **kwargs: None)
    monkeypatch.setattr(takyon_core, "_should_run_claude_agent_in_docker", lambda _workspace_rel: False)
    monkeypatch.setattr(takyon_core, "_workspace_needs_runtime_ui_contract", lambda _workspace_rel: False)
    monkeypatch.setattr(takyon_core, "_resolve_runtime_executable", lambda name: "/usr/bin/node" if name == "node" else None)
    monkeypatch.setattr(takyon_core, "_ensure_repo_node_dependencies", lambda packages: {"success": True})
    monkeypatch.setattr(takyon_core, "_reserve_operator_task_budget", lambda **_kwargs: {"reservation_key": "r1", "reserved_cents": 800})
    monkeypatch.setattr(
        takyon_core,
        "_finalize_operator_task_budget",
        lambda **_kwargs: {"reservation_key": "r1", "reserved_cents": 800, "status": "charged"},
    )
    monkeypatch.setattr(takyon_core, "_record_claude_agent_runtime_event", lambda **_kwargs: None)
    monkeypatch.setattr(takyon_core, "_run_claude_agent_task_process", fake_process)

    result = json.loads(
        handle_business_claude_agent_task(
            {
                "business": "latexflow",
                "workspace": "product/site",
                "instruction": "Build the first honest product surface.",
                "idempotency_key": "workspace-faster-defaults",
                "install": False,
            }
        )
    )

    payload = captured["payload"]
    assert result["success"] is True
    assert payload["maxTurns"] == 60
    assert payload["timeoutMs"] == 1200000
    assert payload["maxBudgetUsd"] == 8.0
    assert payload["effort"] == "medium"
    assert payload["model"] == "claude-sonnet-4-6"


def test_claude_agent_task_clamps_explicit_product_site_turn_budget(tmp_path, monkeypatch):
    monkeypatch.setenv("TAKYON_HOME", str(tmp_path))
    captured: dict[str, object] = {}

    def fake_process(*, payload: dict[str, object], **kwargs):
        captured["payload"] = payload
        Path(str(payload["cwd"]), "index.html").write_text("<h1>Latexflow</h1>\n", encoding="utf-8")
        return types.SimpleNamespace(returncode=0, stdout=json.dumps({"success": True, "summary": "ok"}), stderr="")

    monkeypatch.setattr(takyon_core, "_store", lambda: _FakeStore(tmp_path))
    monkeypatch.setattr(takyon_core, "_session_business_slug", lambda: "latexflow")
    monkeypatch.setattr(takyon_core, "_require_api_access", lambda *args, **kwargs: None)
    monkeypatch.setattr(takyon_core, "_should_run_claude_agent_in_docker", lambda _workspace_rel: False)
    monkeypatch.setattr(takyon_core, "_workspace_needs_runtime_ui_contract", lambda _workspace_rel: False)
    monkeypatch.setattr(takyon_core, "_resolve_runtime_executable", lambda name: "/usr/bin/node" if name == "node" else None)
    monkeypatch.setattr(takyon_core, "_ensure_repo_node_dependencies", lambda packages: {"success": True})
    monkeypatch.setattr(takyon_core, "_reserve_operator_task_budget", lambda **_kwargs: {"reservation_key": "r1", "reserved_cents": 800})
    monkeypatch.setattr(
        takyon_core,
        "_finalize_operator_task_budget",
        lambda **_kwargs: {"reservation_key": "r1", "reserved_cents": 800, "status": "charged"},
    )
    monkeypatch.setattr(takyon_core, "_record_claude_agent_runtime_event", lambda **_kwargs: None)
    monkeypatch.setattr(takyon_core, "_run_claude_agent_task_process", fake_process)

    result = json.loads(
        handle_business_claude_agent_task(
            {
                "business": "latexflow",
                "workspace": "product/site",
                "instruction": "Build the first honest product surface.",
                "idempotency_key": "workspace-clamped-turn-budget",
                "max_turns": 500,
                "install": False,
            }
        )
    )

    payload = captured["payload"]
    assert result["success"] is True
    assert payload["maxTurns"] == 90
