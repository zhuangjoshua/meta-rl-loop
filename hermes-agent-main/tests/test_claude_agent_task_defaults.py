from __future__ import annotations

import json
import shutil
import types
from pathlib import Path

from plugins.takyon import core as takyon_core
from plugins.takyon import storage as takyon_storage
from plugins.takyon.core import handle_business_claude_agent_task


class _FakeConn:
    def __enter__(self):
        return object()

    def __exit__(self, exc_type, exc, tb):
        return False


class _FakeStore:
    def __init__(self, root: Path, *, surface_contract: dict[str, object] | None = None):
        self.root = root
        self._workspace_root_override = None
        self._workspace_sync_cache: set[str] = set()
        self._workspace_storage_backend_override = None
        self._surface_contract = {
            "source_path": "product/site",
            "publish_policy": "publish_after_refresh",
        }
        if isinstance(surface_contract, dict):
            self._surface_contract.update(surface_contract)

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
                    "surface_contract": dict(self._surface_contract)
                }
            }
        return {}

    def _business_root(self, business: str):
        return self.root / "businesses" / business

    def _resolve_business_file(self, business: str, rel: str, **_kwargs):
        return self._business_root(business) / rel

    def _workspace_storage_backend(self):
        if self._workspace_storage_backend_override is not None:
            return self._workspace_storage_backend_override
        return takyon_storage.LocalStorageBackend(self.root / "storage")

    def _sync_business_workspace_remote(self, business: str):
        workspace = self._business_root(business)
        if not workspace.exists():
            return "skipped_missing_workspace"
        backend = self._workspace_storage_backend()
        target = Path(getattr(backend, "root")) / takyon_storage.object_prefix(business)
        if target.exists():
            shutil.rmtree(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(workspace, target)
        return "synced"

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
                "refresh_surface": False,
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
                "refresh_surface": False,
            }
        )
    )

    payload = captured["payload"]
    assert result["success"] is True
    assert payload["maxTurns"] == 90


def test_claude_agent_task_defaults_product_site_guidance_when_omitted(tmp_path, monkeypatch):
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
    monkeypatch.setattr(takyon_core, "_workspace_needs_runtime_ui_contract", lambda workspace_rel: workspace_rel == "product/site")
    monkeypatch.setattr(takyon_core, "_runtime_ui_contract_block", lambda _surface: "")
    monkeypatch.setattr(takyon_core, "_subuser_app_worker_contract_block", lambda _surface, *, plans_configured=False: "")
    monkeypatch.setattr(takyon_core, "_subuser_app_kit_contract_block", lambda _surface: "")
    monkeypatch.setattr(takyon_core, "_materialize_subuser_app_kit", lambda *args, **kwargs: None)
    monkeypatch.setattr(takyon_core, "_resolve_runtime_executable", lambda name: "/usr/bin/node" if name == "node" else None)
    monkeypatch.setattr(takyon_core, "_ensure_repo_node_dependencies", lambda packages: {"success": True})
    monkeypatch.setattr(takyon_core, "_reserve_operator_task_budget", lambda **_kwargs: {"reservation_key": "r1", "reserved_cents": 800})
    monkeypatch.setattr(
        takyon_core,
        "_finalize_operator_task_budget",
        lambda **_kwargs: {"reservation_key": "r1", "reserved_cents": 800, "status": "charged"},
    )
    monkeypatch.setattr(takyon_core, "_record_claude_agent_runtime_event", lambda **_kwargs: None)
    monkeypatch.setattr(
        takyon_core,
        "_compose_worker_guidance_block",
        lambda skills: (list(skills), "[Hermes guidance skill: default-product-site]" if skills else ""),
    )
    monkeypatch.setattr(takyon_core, "_run_claude_agent_task_process", fake_process)

    result = json.loads(
        handle_business_claude_agent_task(
            {
                "business": "latexflow",
                "workspace": "product/site",
                "instruction": "Build the first honest product surface.",
                "idempotency_key": "workspace-default-guidance",
                "install": False,
                "refresh_surface": False,
            }
        )
    )

    instruction = str(captured["payload"]["instruction"])
    assert result["success"] is True
    # Customer-facing product surfaces now default to the full design-pack set so the worker
    # always builds with a coherent visual direction instead of bare layout rules.
    assert result["guidance_skills"] == [
        "claude-design",
        "claude-design-openai",
        "claude-design-stripe",
        "claude-design-superhuman",
        "claude-design-vibrant",
        "claude-design-doodle",
    ]
    assert result["guidance_selection_reason"] == "auto-selected design packs for customer-facing product surface"
    assert "[Hermes guidance skill: default-product-site]" in instruction


def test_claude_agent_task_includes_public_landing_composition_contract_for_product_site(tmp_path, monkeypatch):
    monkeypatch.setenv("TAKYON_HOME", str(tmp_path))
    captured: dict[str, object] = {}

    def fake_process(*, payload: dict[str, object], **kwargs):
        captured["payload"] = payload
        Path(str(payload["cwd"]), "index.html").write_text("<h1>PupCoach</h1>\n", encoding="utf-8")
        return types.SimpleNamespace(returncode=0, stdout=json.dumps({"success": True, "summary": "ok"}), stderr="")

    monkeypatch.setattr(takyon_core, "_store", lambda: _FakeStore(tmp_path))
    monkeypatch.setattr(takyon_core, "_session_business_slug", lambda: "pupcoach")
    monkeypatch.setattr(takyon_core, "_require_api_access", lambda *args, **kwargs: None)
    monkeypatch.setattr(takyon_core, "_should_run_claude_agent_in_docker", lambda _workspace_rel: False)
    monkeypatch.setattr(takyon_core, "_workspace_needs_runtime_ui_contract", lambda workspace_rel: workspace_rel == "product/site")
    monkeypatch.setattr(takyon_core, "_runtime_ui_contract_block", lambda _surface: "")
    monkeypatch.setattr(takyon_core, "_subuser_app_worker_contract_block", lambda _surface, *, plans_configured=False: "")
    monkeypatch.setattr(takyon_core, "_subuser_app_kit_contract_block", lambda _surface: "")
    monkeypatch.setattr(takyon_core, "_materialize_subuser_app_kit", lambda *args, **kwargs: None)
    monkeypatch.setattr(takyon_core, "_resolve_runtime_executable", lambda name: "/usr/bin/node" if name == "node" else None)
    monkeypatch.setattr(takyon_core, "_ensure_repo_node_dependencies", lambda packages: {"success": True})
    monkeypatch.setattr(takyon_core, "_reserve_operator_task_budget", lambda **_kwargs: {"reservation_key": "r1", "reserved_cents": 800})
    monkeypatch.setattr(
        takyon_core,
        "_finalize_operator_task_budget",
        lambda **_kwargs: {"reservation_key": "r1", "reserved_cents": 800, "status": "charged"},
    )
    monkeypatch.setattr(takyon_core, "_record_claude_agent_runtime_event", lambda **_kwargs: None)
    monkeypatch.setattr(
        takyon_core,
        "_compose_worker_guidance_block",
        lambda skills: (list(skills), "[Hermes guidance skill: default-product-site]" if skills else ""),
    )
    monkeypatch.setattr(takyon_core, "_run_claude_agent_task_process", fake_process)

    result = json.loads(
        handle_business_claude_agent_task(
            {
                "business": "pupcoach",
                "workspace": "product/site",
                "instruction": "Build the first honest product surface.",
                "idempotency_key": "workspace-public-landing-composition",
                "install": False,
                "refresh_surface": False,
            }
        )
    )

    instruction = str(captured["payload"]["instruction"])
    assert result["success"] is True
    assert "Public landing composition floor:" in instruction
    assert "small centered island" in instruction
    assert "page-scale" in instruction
    assert "selected design direction" in instruction
    # Exact pixel/width prescriptions now live in the design packs, not this floor.
    assert "1680px" not in instruction
    assert "92vw" not in instruction
    assert "58/42" not in instruction


def test_claude_agent_task_defaults_full_pack_set_not_keyword_inferred(tmp_path, monkeypatch):
    monkeypatch.setenv("TAKYON_HOME", str(tmp_path))
    captured: dict[str, object] = {}
    store = _FakeStore(
        tmp_path,
        surface_contract={
            "notes": (
                "ICP: men and women 18-35 who want to be socially cooler. "
                "Landing page must be bold, specific, anti-generic."
            ),
            "customer_experience_shape": {
                "surface_goal": "Convert young consumer users for a social self-improvement app."
            },
        },
    )

    def fake_process(*, payload: dict[str, object], **kwargs):
        captured["payload"] = payload
        Path(str(payload["cwd"]), "index.html").write_text("<h1>Coolman</h1>\n", encoding="utf-8")
        return types.SimpleNamespace(returncode=0, stdout=json.dumps({"success": True, "summary": "ok"}), stderr="")

    monkeypatch.setattr(takyon_core, "_store", lambda: store)
    monkeypatch.setattr(takyon_core, "_session_business_slug", lambda: "coolman")
    monkeypatch.setattr(takyon_core, "_require_api_access", lambda *args, **kwargs: None)
    monkeypatch.setattr(takyon_core, "_should_run_claude_agent_in_docker", lambda _workspace_rel: False)
    monkeypatch.setattr(takyon_core, "_workspace_needs_runtime_ui_contract", lambda workspace_rel: workspace_rel == "product/site")
    monkeypatch.setattr(takyon_core, "_runtime_ui_contract_block", lambda _surface: "")
    monkeypatch.setattr(takyon_core, "_subuser_app_worker_contract_block", lambda _surface, *, plans_configured=False: "")
    monkeypatch.setattr(takyon_core, "_subuser_app_kit_contract_block", lambda _surface: "")
    monkeypatch.setattr(takyon_core, "_materialize_subuser_app_kit", lambda *args, **kwargs: None)
    monkeypatch.setattr(takyon_core, "_resolve_runtime_executable", lambda name: "/usr/bin/node" if name == "node" else None)
    monkeypatch.setattr(takyon_core, "_ensure_repo_node_dependencies", lambda packages: {"success": True})
    monkeypatch.setattr(takyon_core, "_reserve_operator_task_budget", lambda **_kwargs: {"reservation_key": "r1", "reserved_cents": 800})
    monkeypatch.setattr(
        takyon_core,
        "_finalize_operator_task_budget",
        lambda **_kwargs: {"reservation_key": "r1", "reserved_cents": 800, "status": "charged"},
    )
    monkeypatch.setattr(takyon_core, "_record_claude_agent_runtime_event", lambda **_kwargs: None)
    monkeypatch.setattr(
        takyon_core,
        "_compose_worker_guidance_block",
        lambda skills: (list(skills), "[Hermes guidance skill: inferred-product-site]" if skills else ""),
    )
    monkeypatch.setattr(takyon_core, "_run_claude_agent_task_process", fake_process)

    result = json.loads(
        handle_business_claude_agent_task(
            {
                "business": "coolman",
                "workspace": "product/site",
                "instruction": "Build the first honest product surface.",
                "idempotency_key": "workspace-vibrant-guidance",
                "install": False,
                "refresh_surface": False,
            }
        )
    )

    instruction = str(captured["payload"]["instruction"])
    assert result["success"] is True
    # The default is the FULL pack set (the worker chooses one coherent direction), not a single
    # pack inferred from brief keywords like "bold consumer".
    assert result["guidance_skills"] == [
        "claude-design",
        "claude-design-openai",
        "claude-design-stripe",
        "claude-design-superhuman",
        "claude-design-vibrant",
        "claude-design-doodle",
    ]
    assert result["guidance_selection_reason"] == "auto-selected design packs for customer-facing product surface"
    assert "[Hermes guidance skill: inferred-product-site]" in instruction


def test_claude_agent_task_settles_reported_actual_cost(tmp_path, monkeypatch):
    monkeypatch.setenv("TAKYON_HOME", str(tmp_path))
    finalize_calls: list[dict[str, object]] = []

    def fake_process(*, payload: dict[str, object], **kwargs):
        Path(str(payload["cwd"]), "index.html").write_text("<h1>Latexflow</h1>\n", encoding="utf-8")
        return types.SimpleNamespace(
            returncode=0,
            stdout=json.dumps(
                {
                    "success": True,
                    "summary": "ok",
                    "actual_cost_cents": 137,
                }
            ),
            stderr="",
        )

    def fake_finalize(**kwargs):
        finalize_calls.append(dict(kwargs))
        return {
            "reservation_key": "r1",
            "reserved_cents": 800,
            "charged_cents": 137,
            "status": "settled_actual",
        }

    monkeypatch.setattr(takyon_core, "_store", lambda: _FakeStore(tmp_path))
    monkeypatch.setattr(takyon_core, "_session_business_slug", lambda: "latexflow")
    monkeypatch.setattr(takyon_core, "_require_api_access", lambda *args, **kwargs: None)
    monkeypatch.setattr(takyon_core, "_should_run_claude_agent_in_docker", lambda _workspace_rel: False)
    monkeypatch.setattr(takyon_core, "_workspace_needs_runtime_ui_contract", lambda _workspace_rel: False)
    monkeypatch.setattr(takyon_core, "_resolve_runtime_executable", lambda name: "/usr/bin/node" if name == "node" else None)
    monkeypatch.setattr(takyon_core, "_ensure_repo_node_dependencies", lambda packages: {"success": True})
    monkeypatch.setattr(takyon_core, "_reserve_operator_task_budget", lambda **_kwargs: {"reservation_key": "r1", "reserved_cents": 800})
    monkeypatch.setattr(takyon_core, "_finalize_operator_task_budget", fake_finalize)
    monkeypatch.setattr(takyon_core, "_record_claude_agent_runtime_event", lambda **_kwargs: None)
    monkeypatch.setattr(takyon_core, "_run_claude_agent_task_process", fake_process)

    result = json.loads(
        handle_business_claude_agent_task(
            {
                "business": "latexflow",
                "workspace": "product/site",
                "instruction": "Build the first honest product surface.",
                "idempotency_key": "workspace-actual-cost",
                "install": False,
                "refresh_surface": False,
            }
        )
    )

    assert result["success"] is True
    assert result["actual_cost_cents"] == 137
    assert result["operator_budget"]["charged_cents"] == 137
    assert finalize_calls[-1]["actual_cents"] == 137


def test_claude_agent_task_respects_explicit_empty_guidance_for_product_site(tmp_path, monkeypatch):
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
    monkeypatch.setattr(takyon_core, "_workspace_needs_runtime_ui_contract", lambda workspace_rel: workspace_rel == "product/site")
    monkeypatch.setattr(takyon_core, "_runtime_ui_contract_block", lambda _surface: "")
    monkeypatch.setattr(takyon_core, "_subuser_app_worker_contract_block", lambda _surface, *, plans_configured=False: "")
    monkeypatch.setattr(takyon_core, "_subuser_app_kit_contract_block", lambda _surface: "")
    monkeypatch.setattr(takyon_core, "_materialize_subuser_app_kit", lambda *args, **kwargs: None)
    monkeypatch.setattr(takyon_core, "_resolve_runtime_executable", lambda name: "/usr/bin/node" if name == "node" else None)
    monkeypatch.setattr(takyon_core, "_ensure_repo_node_dependencies", lambda packages: {"success": True})
    monkeypatch.setattr(takyon_core, "_reserve_operator_task_budget", lambda **_kwargs: {"reservation_key": "r1", "reserved_cents": 800})
    monkeypatch.setattr(
        takyon_core,
        "_finalize_operator_task_budget",
        lambda **_kwargs: {"reservation_key": "r1", "reserved_cents": 800, "status": "charged"},
    )
    monkeypatch.setattr(takyon_core, "_record_claude_agent_runtime_event", lambda **_kwargs: None)
    monkeypatch.setattr(takyon_core, "_compose_worker_guidance_block", lambda skills: (list(skills), ""))
    monkeypatch.setattr(takyon_core, "_run_claude_agent_task_process", fake_process)

    result = json.loads(
        handle_business_claude_agent_task(
            {
                "business": "latexflow",
                "workspace": "product/site",
                "instruction": "Build the first honest product surface.",
                "guidance_skills": [],
                "idempotency_key": "workspace-explicit-empty-guidance",
                "install": False,
                "refresh_surface": False,
            }
        )
    )

    assert result["success"] is True
    assert result["guidance_skills"] == []


def test_claude_agent_task_reuses_session_workspace_for_docker_product_work(tmp_path, monkeypatch):
    outer_home = tmp_path / "outer-home"
    workspace = outer_home / "businesses" / "latexflow" / "product" / "site"
    workspace.mkdir(parents=True, exist_ok=True)
    captured: dict[str, object] = {}
    mounted_calls: list[dict[str, object]] = []
    store = _FakeStore(outer_home)
    store._workspace_root_override = outer_home

    real_mounted_business_workspace = takyon_storage.mounted_business_workspace

    def record_mount(*args, **kwargs):
        mounted_calls.append({"args": args, "kwargs": kwargs})
        return real_mounted_business_workspace(*args, **kwargs)

    def fake_docker_runner(*, payload: dict[str, object], workspace_path: Path, timeout_ms: int):
        captured["docker_workspace_path"] = workspace_path
        captured["docker_timeout_ms"] = timeout_ms
        return ["docker", "run"], payload, str(tmp_path), {}

    def fake_process(*, payload: dict[str, object], cwd: str, **kwargs):
        captured["payload"] = payload
        captured["cwd"] = cwd
        Path(str(payload["cwd"]), "index.html").write_text("<h1>Latexflow</h1>\n", encoding="utf-8")
        return types.SimpleNamespace(returncode=0, stdout=json.dumps({"success": True, "summary": "ok"}), stderr="")

    monkeypatch.setenv("TAKYON_HOME", str(tmp_path))
    monkeypatch.setattr(takyon_core, "_store", lambda: store)
    monkeypatch.setattr(takyon_core, "_session_business_slug", lambda: "latexflow")
    monkeypatch.setattr(takyon_core, "_require_api_access", lambda *args, **kwargs: None)
    monkeypatch.setattr(takyon_core, "_should_run_claude_agent_in_docker", lambda _workspace_rel: True)
    monkeypatch.setattr(takyon_core, "_workspace_needs_runtime_ui_contract", lambda _workspace_rel: False)
    monkeypatch.setattr(takyon_core, "_ensure_repo_node_dependencies", lambda packages: {"success": True})
    monkeypatch.setattr(takyon_core, "_reserve_operator_task_budget", lambda **_kwargs: {"reservation_key": "r1", "reserved_cents": 800})
    monkeypatch.setattr(
        takyon_core,
        "_finalize_operator_task_budget",
        lambda **_kwargs: {"reservation_key": "r1", "reserved_cents": 800, "status": "charged"},
    )
    monkeypatch.setattr(
        takyon_core,
        "_finalize_product_surface_refresh",
        lambda **_kwargs: {
            "status": "passed",
            "source_path": "product/site",
            "kind": "node_build",
            "checks": [],
            "inventory": {},
            "publish": {"status": "published", "public_url": "https://latexflow.fourmanifold.com/"},
            "receipt_path": "metrics/receipts/product-surface/test.json",
        },
    )
    monkeypatch.setattr(takyon_core, "_product_surface_refresh_operations", lambda **_kwargs: [])
    monkeypatch.setattr(takyon_core, "_record_claude_agent_runtime_event", lambda **_kwargs: None)
    monkeypatch.setattr(takyon_core, "_run_claude_agent_task_in_docker", fake_docker_runner)
    monkeypatch.setattr(takyon_core, "_run_claude_agent_task_process", fake_process)
    monkeypatch.setattr(takyon_storage, "mounted_business_workspace", record_mount)

    result = json.loads(
        handle_business_claude_agent_task(
            {
                "business": "latexflow",
                "workspace": "product/site",
                "instruction": "Build the first honest product surface.",
                "idempotency_key": "workspace-reuse-outer-scratch",
                "install": False,
            }
        )
    )

    assert result["success"] is True
    assert captured["docker_workspace_path"] == workspace
    assert captured["payload"]["cwd"] == str(workspace)
    assert result["workspace_sync_status"] == "synced"
    assert result["workspace_durability"]["matched"] is True
    assert len(mounted_calls) == 1


def test_scoped_workspace_store_uses_workspace_root_override_for_mounted_takyon_homes(tmp_path, monkeypatch):
    monkeypatch.setenv("TAKYON_HOME", str(tmp_path / ".takyon-home"))
    prototype = takyon_core.TakyonStore(root=tmp_path / "outer-home", operator_user_id="user-123")
    mounted_home = (tmp_path / "mounted-home").resolve()
    scoped = takyon_core._scoped_workspace_store(
        prototype,
        root=mounted_home,
        operator_user_id="user-123",
    )

    assert getattr(scoped, "_workspace_root_override", None) == mounted_home
    assert scoped._business_workspace_base() == mounted_home / "businesses"
    assert scoped._business_root("ledgerleaf", sync=False) == mounted_home / "businesses" / "ledgerleaf"


def test_replace_business_workspace_cache_overwrites_stale_operator_cache_with_verified_tree(tmp_path, monkeypatch):
    monkeypatch.setenv("TAKYON_HOME", str(tmp_path / ".takyon-home"))
    store = takyon_core.TakyonStore(root=tmp_path / "outer-home", operator_user_id="user-123")
    monkeypatch.setattr(store, "_workspace_storage_backend_kind", lambda: "supabase_s3")

    stale_root = store._business_root("ledgerleaf", sync=False)
    stale_landing = stale_root / "product" / "site" / "src" / "screens" / "landing.tsx"
    stale_landing.parent.mkdir(parents=True, exist_ok=True)
    stale_landing.write_text(
        "export function LandingScreen() {\n  return <main aria-hidden=\"true\" data-takyon-scaffold=\"landing\" />;\n}\n",
        encoding="utf-8",
    )

    verified_root = tmp_path / "verified-tree"
    verified_landing = verified_root / "product" / "site" / "src" / "screens" / "landing.tsx"
    verified_landing.parent.mkdir(parents=True, exist_ok=True)
    verified_landing.write_text(
        "export function LandingScreen() {\n  return <main>verified worker landing</main>;\n}\n",
        encoding="utf-8",
    )

    store._replace_business_workspace_cache("ledgerleaf", verified_root)

    assert stale_landing.read_text(encoding="utf-8") == verified_landing.read_text(encoding="utf-8")


def test_claude_agent_task_blocks_docker_product_site_when_canonical_readback_diverges(tmp_path, monkeypatch):
    outer_home = tmp_path / "outer-home"
    workspace = outer_home / "businesses" / "latexflow" / "product" / "site"
    screens = workspace / "src" / "screens"
    screens.mkdir(parents=True, exist_ok=True)
    store = _FakeStore(outer_home)
    store._workspace_root_override = outer_home

    def fake_sync(_business: str):
        backend = store._workspace_storage_backend()
        target = Path(getattr(backend, "root")) / takyon_storage.object_prefix("latexflow")
        if target.exists():
            shutil.rmtree(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(outer_home / "businesses" / "latexflow", target)
        canonical_landing = target / "product" / "site" / "src" / "screens" / "landing.tsx"
        canonical_landing.write_text(
            "export function LandingScreen() {\n  return <main aria-hidden=\"true\" data-takyon-scaffold=\"landing\" />;\n}\n",
            encoding="utf-8",
        )
        return "synced"

    store._sync_business_workspace_remote = fake_sync

    def fake_docker_runner(*, payload: dict[str, object], workspace_path: Path, timeout_ms: int):
        return ["docker", "run"], payload, str(tmp_path), {}

    def fake_process(*, payload: dict[str, object], cwd: str, **kwargs):
        landing = Path(str(payload["cwd"])) / "src" / "screens" / "landing.tsx"
        landing.parent.mkdir(parents=True, exist_ok=True)
        landing.write_text(
            "export function LandingScreen() {\n  return <main>worker landing</main>;\n}\n",
            encoding="utf-8",
        )
        return types.SimpleNamespace(returncode=0, stdout=json.dumps({"success": True, "summary": "ok"}), stderr="")

    monkeypatch.setenv("TAKYON_HOME", str(tmp_path))
    monkeypatch.setattr(takyon_core, "_store", lambda: store)
    monkeypatch.setattr(takyon_core, "_session_business_slug", lambda: "latexflow")
    monkeypatch.setattr(takyon_core, "_require_api_access", lambda *args, **kwargs: None)
    monkeypatch.setattr(takyon_core, "_should_run_claude_agent_in_docker", lambda _workspace_rel: True)
    monkeypatch.setattr(takyon_core, "_workspace_needs_runtime_ui_contract", lambda _workspace_rel: False)
    monkeypatch.setattr(takyon_core, "_ensure_repo_node_dependencies", lambda packages: {"success": True})
    monkeypatch.setattr(takyon_core, "_reserve_operator_task_budget", lambda **_kwargs: {"reservation_key": "r1", "reserved_cents": 800})
    monkeypatch.setattr(
        takyon_core,
        "_finalize_operator_task_budget",
        lambda **_kwargs: {"reservation_key": "r1", "reserved_cents": 800, "status": "charged"},
    )
    monkeypatch.setattr(
        takyon_core,
        "_finalize_product_surface_refresh",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("refresh should not run after a durability mismatch")),
    )
    monkeypatch.setattr(takyon_core, "_record_claude_agent_runtime_event", lambda **_kwargs: None)
    monkeypatch.setattr(takyon_core, "_run_claude_agent_task_in_docker", fake_docker_runner)
    monkeypatch.setattr(takyon_core, "_run_claude_agent_task_process", fake_process)

    result = json.loads(
        handle_business_claude_agent_task(
            {
                "business": "latexflow",
                "workspace": "product/site",
                "instruction": "Build the first honest product surface.",
                "idempotency_key": "workspace-durable-readback-mismatch",
                "install": False,
            }
        )
    )

    assert result["success"] is False
    assert result["workspace_sync_status"] == "synced"
    assert result["workspace_durability"]["matched"] is False
    assert "worker_source_not_durable" in (result["error"] or "")
    durability_paths = " ".join(
        list(result["workspace_durability"]["changed"])
        + list(result["workspace_durability"]["worker_only"])
        + list(result["workspace_durability"]["canonical_only"])
    )
    assert "src/screens/landing.tsx" in durability_paths


def test_claude_agent_task_refreshes_docker_product_site_from_canonical_readback(tmp_path, monkeypatch):
    outer_home = tmp_path / "outer-home"
    workspace = outer_home / "businesses" / "latexflow" / "product" / "site"
    workspace.mkdir(parents=True, exist_ok=True)
    store = _FakeStore(outer_home)
    store._workspace_root_override = outer_home
    refresh_calls: list[dict[str, object]] = []

    def fake_docker_runner(*, payload: dict[str, object], workspace_path: Path, timeout_ms: int):
        return ["docker", "run"], payload, str(tmp_path), {}

    def fake_process(*, payload: dict[str, object], cwd: str, **kwargs):
        landing = Path(str(payload["cwd"])) / "src" / "screens" / "landing.tsx"
        landing.parent.mkdir(parents=True, exist_ok=True)
        landing.write_text(
            "export function LandingScreen() {\n  return <main>canonical worker landing</main>;\n}\n",
            encoding="utf-8",
        )
        return types.SimpleNamespace(returncode=0, stdout=json.dumps({"success": True, "summary": "ok"}), stderr="")

    def fake_refresh(*, store, business: str, source_path: str, **kwargs):
        source_root = store._resolve_business_file(
            business,
            source_path,
            require_output_root=True,
            field="workspace",
        )
        refresh_calls.append(
            {
                "store_root": str(store.root),
                "source_root": str(source_root),
                "landing": source_root.joinpath("src", "screens", "landing.tsx").read_text(encoding="utf-8"),
            }
        )
        return {
            "status": "passed",
            "source_path": source_path,
            "kind": "node_build",
            "checks": [],
            "inventory": {},
            "publish": {"status": "published", "public_url": "https://latexflow.fourmanifold.com/"},
            "receipt_path": "metrics/receipts/product-surface/test.json",
        }

    monkeypatch.setenv("TAKYON_HOME", str(tmp_path))
    monkeypatch.setattr(takyon_core, "_store", lambda: store)
    monkeypatch.setattr(takyon_core, "_session_business_slug", lambda: "latexflow")
    monkeypatch.setattr(takyon_core, "_require_api_access", lambda *args, **kwargs: None)
    monkeypatch.setattr(takyon_core, "_should_run_claude_agent_in_docker", lambda _workspace_rel: True)
    monkeypatch.setattr(takyon_core, "_workspace_needs_runtime_ui_contract", lambda _workspace_rel: False)
    monkeypatch.setattr(takyon_core, "_ensure_repo_node_dependencies", lambda packages: {"success": True})
    monkeypatch.setattr(takyon_core, "_reserve_operator_task_budget", lambda **_kwargs: {"reservation_key": "r1", "reserved_cents": 800})
    monkeypatch.setattr(
        takyon_core,
        "_finalize_operator_task_budget",
        lambda **_kwargs: {"reservation_key": "r1", "reserved_cents": 800, "status": "charged"},
    )
    monkeypatch.setattr(takyon_core, "_finalize_product_surface_refresh", fake_refresh)
    monkeypatch.setattr(takyon_core, "_product_surface_refresh_operations", lambda **_kwargs: [])
    monkeypatch.setattr(takyon_core, "_record_claude_agent_runtime_event", lambda **_kwargs: None)
    monkeypatch.setattr(takyon_core, "_run_claude_agent_task_in_docker", fake_docker_runner)
    monkeypatch.setattr(takyon_core, "_run_claude_agent_task_process", fake_process)

    result = json.loads(
        handle_business_claude_agent_task(
            {
                "business": "latexflow",
                "workspace": "product/site",
                "instruction": "Build the first honest product surface.",
                "idempotency_key": "workspace-durable-readback-refresh",
                "install": False,
            }
        )
    )

    assert result["success"] is True
    assert result["workspace_sync_status"] == "synced"
    assert result["workspace_durability"]["matched"] is True
    assert len(refresh_calls) == 1
    assert refresh_calls[0]["store_root"] != str(outer_home)
    assert "canonical worker landing" in refresh_calls[0]["landing"]


def test_run_claude_agent_task_in_docker_uses_host_user_and_container_only_tmp_home(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True)
    repo_root = tmp_path / "repo"
    repo_root.mkdir(parents=True)

    from tools.environments import docker as docker_env

    monkeypatch.setattr(docker_env, "find_docker", lambda: "/usr/bin/docker")
    monkeypatch.setattr(docker_env, "_resolve_host_user_spec", lambda: "995:987")
    monkeypatch.setattr(
        docker_env,
        "_host_user_identity_mount_args",
        lambda user_spec: [
            "--mount",
            f"type=bind,src={tmp_path / 'passwd'},dst=/etc/passwd,readonly",
            "--mount",
            f"type=bind,src={tmp_path / 'group'},dst=/etc/group,readonly",
        ] if user_spec == "995:987" else [],
    )
    monkeypatch.setattr(
        docker_env,
        "_build_security_args",
        lambda run_as_host_user=False: [f"--security-opt=test-{str(bool(run_as_host_user)).lower()}"],
    )
    monkeypatch.setattr(takyon_core, "_repo_root", lambda: repo_root)
    monkeypatch.setattr(takyon_core, "_runtime_env", lambda extra=None: {"ANTHROPIC_API_KEY": "test-key", **(extra or {})})

    run_cmd, payload, worker_cwd, worker_env = takyon_core._run_claude_agent_task_in_docker(
        payload={
            "business": "latexflow",
            "workspace": "product/site",
            "instruction": "Build the product shell.",
        },
        workspace_path=workspace,
        timeout_ms=30_000,
    )

    assert "-i" in run_cmd
    assert "--user" in run_cmd
    user_index = run_cmd.index("--user")
    assert run_cmd[user_index + 1] == "995:987"
    assert "--security-opt=test-true" in run_cmd
    assert payload["instruction"] == "Build the product shell."
    assert payload["cwd"] == "/workspace"
    assert payload["root"] == "/workspace"
    assert worker_cwd == str(repo_root)
    assert worker_env.get("HOME") != "/tmp"
    assert "HOME=/tmp" in run_cmd
    assert "/etc/passwd,readonly" in " ".join(run_cmd)
    assert "/etc/group,readonly" in " ".join(run_cmd)


def test_claude_agent_task_returns_worker_failure_diagnostics(tmp_path, monkeypatch):
    monkeypatch.setenv("TAKYON_HOME", str(tmp_path))

    class _CapturingStore(_FakeStore):
        def __init__(self, root: Path):
            super().__init__(root)
            self.commits: list[dict[str, object]] = []

        def commit(self, **kwargs):
            self.commits.append(dict(kwargs))
            return {"success": True}

    store = _CapturingStore(tmp_path)

    def fake_process(*, payload: dict[str, object], **kwargs):
        Path(str(payload["cwd"])).mkdir(parents=True, exist_ok=True)
        return types.SimpleNamespace(returncode=1, stdout="plain worker failure output", stderr="fatal: missing claude auth")

    monkeypatch.setattr(takyon_core, "_store", lambda: store)
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
                "idempotency_key": "workspace-worker-failure-diagnostics",
                "install": False,
            }
        )
    )

    assert result["success"] is False
    assert result["error"] == "fatal: missing claude auth"
    assert result["worker_returncode"] == 1
    assert result["worker_stderr"] == "fatal: missing claude auth"
    assert result["raw_stdout"] == "plain worker failure output"
    operations = store.commits[-1]["operations"]
    agent_record = next(op for op in operations if op.get("action") == "agent.record")
    assert agent_record["result"]["worker_returncode"] == 1
    assert agent_record["result"]["worker_stderr"] == "fatal: missing claude auth"
    assert agent_record["result"]["raw_stdout"] == "plain worker failure output"


def test_claude_agent_task_preserves_worker_stderr_from_sdk_stdout(tmp_path, monkeypatch):
    monkeypatch.setenv("TAKYON_HOME", str(tmp_path))

    store = _FakeStore(tmp_path)

    def fake_process(*, payload: dict[str, object], **kwargs):
        Path(str(payload["cwd"])).mkdir(parents=True, exist_ok=True)
        return types.SimpleNamespace(
            returncode=1,
            stdout=json.dumps(
                {
                    "success": False,
                    "error": "Error: Claude Code process exited with code 1",
                    "worker_stderr": "ENOENT: no such file or directory, uv_os_homedir",
                }
            ),
            stderr="",
        )

    monkeypatch.setattr(takyon_core, "_store", lambda: store)
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
                "idempotency_key": "workspace-worker-json-stderr",
                "install": False,
            }
        )
    )

    assert result["success"] is False
    assert result["worker_returncode"] == 1
    assert result["worker_stderr"] == "ENOENT: no such file or directory, uv_os_homedir"


def test_claude_agent_task_formats_signal_terminated_worker_error(tmp_path, monkeypatch):
    monkeypatch.setenv("TAKYON_HOME", str(tmp_path))

    store = _FakeStore(tmp_path)

    def fake_process(*, payload: dict[str, object], **kwargs):
        Path(str(payload["cwd"])).mkdir(parents=True, exist_ok=True)
        return types.SimpleNamespace(returncode=-15, stdout="", stderr="")

    monkeypatch.setattr(takyon_core, "_store", lambda: store)
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
                "idempotency_key": "workspace-worker-sigterm",
                "install": False,
            }
        )
    )

    assert result["success"] is False
    assert result["worker_returncode"] == -15
    assert result["error"] == "Claude worker was interrupted by SIGTERM before completion"


def test_claude_agent_task_retries_product_turn_cap_once_with_higher_budget(tmp_path, monkeypatch):
    monkeypatch.setenv("TAKYON_HOME", str(tmp_path))

    class _CapturingStore(_FakeStore):
        def __init__(self, root: Path):
            super().__init__(root)
            self.commits: list[dict[str, object]] = []

        def commit(self, **kwargs):
            self.commits.append(dict(kwargs))
            return {"success": True}

    store = _CapturingStore(tmp_path)
    captured_payloads: list[dict[str, object]] = []

    def fake_process(*, payload: dict[str, object], **kwargs):
        captured_payloads.append(dict(payload))
        Path(str(payload["cwd"])).mkdir(parents=True, exist_ok=True)
        if len(captured_payloads) == 1:
            return types.SimpleNamespace(
                returncode=1,
                stdout=json.dumps(
                    {
                        "success": False,
                        "error": "Error: Claude Code returned an error result: Reached maximum number of turns (20)",
                    }
                ),
                stderr="",
            )
        Path(str(payload["cwd"]), "index.html").write_text("<h1>Latexflow</h1>\n", encoding="utf-8")
        return types.SimpleNamespace(returncode=0, stdout=json.dumps({"success": True, "summary": "ok"}), stderr="")

    monkeypatch.setattr(takyon_core, "_store", lambda: store)
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
                "instruction": "Run npm install and npm run build.",
                "idempotency_key": "workspace-product-turn-cap-retry",
                "install": False,
                "max_turns": 20,
            }
        )
    )

    assert result["success"] is True
    assert [payload["maxTurns"] for payload in captured_payloads] == [20, 60]
    assert result["worker_attempts"] == 2
    assert result["turn_cap_retries"] == [{"from": 20, "to": 60}]
    operations = store.commits[-1]["operations"]
    agent_record = next(op for op in operations if op.get("action") == "agent.record")
    assert agent_record["result"]["turn_cap_retries"] == [{"from": 20, "to": 60}]


def test_claude_agent_task_bash_wrapper_uses_absolute_env_and_bash_paths():
    script = Path(__file__).resolve().parents[1] / "scripts" / "takyon-claude-agent-task.mjs"
    text = script.read_text(encoding="utf-8")
    assert "/usr/bin/env -i PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin HOME=/tmp /bin/bash -lc" in text


def test_docker_claude_worker_binary_mounts_uses_repo_binary_when_present(tmp_path, monkeypatch):
    repo_root = tmp_path / "repo"
    binary = repo_root / "node_modules" / "@anthropic-ai" / "claude-agent-sdk-linux-arm64" / "claude"
    binary.parent.mkdir(parents=True, exist_ok=True)
    binary.write_text("#!/bin/sh\n", encoding="utf-8")

    monkeypatch.setattr(takyon_core, "_docker_server_arch", lambda docker_exe: "arm64")

    mounts, env_map = takyon_core._docker_claude_worker_binary_mounts(
        docker_exe="/usr/bin/docker",
        repo_root=repo_root,
    )

    assert mounts == []
    assert env_map == {
        "TAKYON_CLAUDE_CODE_EXECUTABLE": "/repo/node_modules/@anthropic-ai/claude-agent-sdk-linux-arm64/claude"
    }


def test_docker_claude_worker_binary_mounts_installs_cached_binary_when_repo_binary_missing(tmp_path, monkeypatch):
    repo_root = tmp_path / "repo"
    package_json = repo_root / "node_modules" / "@anthropic-ai" / "claude-agent-sdk" / "package.json"
    package_json.parent.mkdir(parents=True, exist_ok=True)
    package_json.write_text(json.dumps({"version": "0.3.148"}), encoding="utf-8")

    fake_root = tmp_path / "takyon-root"
    monkeypatch.setattr(takyon_core, "_docker_server_arch", lambda docker_exe: "arm64")
    monkeypatch.setattr(takyon_core, "_resolve_runtime_executable", lambda name: "/usr/bin/npm" if name == "npm" else None)
    monkeypatch.setattr(takyon_core, "get_default_takyon_root", lambda: fake_root)
    monkeypatch.setattr(takyon_core, "_runtime_env", lambda extra=None: dict(extra or {}))

    def fake_run(cmd, **kwargs):
        binary = Path(kwargs["cwd"]) / "node_modules" / "@anthropic-ai" / "claude-agent-sdk-linux-arm64" / "claude"
        binary.parent.mkdir(parents=True, exist_ok=True)
        binary.write_text("#!/bin/sh\n", encoding="utf-8")
        return types.SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(takyon_core.subprocess, "run", fake_run)

    mounts, env_map = takyon_core._docker_claude_worker_binary_mounts(
        docker_exe="/usr/bin/docker",
        repo_root=repo_root,
    )

    expected_root = fake_root / "cache" / "claude-agent-sdk" / "linux-arm64-0.3.148"
    assert mounts == [
        "--mount",
        f"type=bind,src={expected_root},dst=/opt/takyon-claude-sdk,readonly",
    ]
    assert env_map == {
        "TAKYON_CLAUDE_CODE_EXECUTABLE": "/opt/takyon-claude-sdk/node_modules/@anthropic-ai/claude-agent-sdk-linux-arm64/claude"
    }
    assert expected_root.joinpath("node_modules", "@anthropic-ai", "claude-agent-sdk-linux-arm64", "claude").exists()


def test_claude_agent_task_script_honors_explicit_claude_executable_env():
    script = Path(__file__).resolve().parents[1] / "scripts" / "takyon-claude-agent-task.mjs"
    text = script.read_text(encoding="utf-8")
    assert "TAKYON_CLAUDE_CODE_EXECUTABLE" in text
    assert "pathToClaudeCodeExecutable" in text


def test_claude_agent_task_ignores_worker_surface_contract_patch_and_refreshes_once(tmp_path, monkeypatch):
    monkeypatch.setenv("TAKYON_HOME", str(tmp_path))
    captured_payloads: list[dict[str, object]] = []
    refresh_calls: list[dict[str, object]] = []

    class _SurfaceUpdatingStore(_FakeStore):
        def __init__(self, root: Path):
            super().__init__(
                root,
                surface_contract={
                    "source_path": "product/site",
                    "runtime_features": ["auth", "account", "checkout"],
                    "routes": ["/", "/app"],
                    "metadata": {
                        "customer_experience": {
                            "required_routes": ["/", "/app"],
                        }
                    },
                },
            )
            self.commits: list[dict[str, object]] = []

        def commit(self, **kwargs):
            self.commits.append(dict(kwargs))
            for op in kwargs.get("operations", []):
                if op.get("action") != "app.surface.upsert":
                    continue
                updated = dict(self._surface_contract)
                metadata = dict(updated.get("metadata") or {})
                customer_experience = (
                    dict(metadata.get("customer_experience") or {})
                    if isinstance(metadata.get("customer_experience"), dict)
                    else {}
                )
                for key in ("surface_goal", "conversion_model", "required_routes", "required_sections", "required_app_tabs", "research_sources"):
                    if key in op:
                        customer_experience[key] = op.get(key)
                if customer_experience:
                    metadata["customer_experience"] = customer_experience
                if "product_workflow" in op:
                    metadata["product_workflow"] = op.get("product_workflow")
                if metadata:
                    updated["metadata"] = metadata
                if "runtime_features" in op:
                    updated["runtime_features"] = op.get("runtime_features")
                self._surface_contract = updated
            return {"success": True, "results": kwargs.get("operations", [])}

    store = _SurfaceUpdatingStore(tmp_path)

    def fake_process(*, payload: dict[str, object], **kwargs):
        captured_payloads.append(dict(payload))
        workspace = Path(str(payload["cwd"]))
        workspace.mkdir(parents=True, exist_ok=True)
        workspace.joinpath("index.html").write_text("<h1>Plannerly</h1>\n", encoding="utf-8")
        if len(captured_payloads) == 1:
            patch_path = workspace / "_takyon" / "worker-surface-contract.json"
            patch_path.parent.mkdir(parents=True, exist_ok=True)
            patch_path.write_text(
                json.dumps(
                    {
                        "requested": True,
                        "why": "The build needed a real action rail and named action.",
                        "patch": {
                            "runtime_features": ["auth", "account", "actions", "checkout"],
                            "product_workflow": {
                                "actions": [{"name": "plan-workflow", "trigger": "http"}],
                                "outbound_hosts": ["api.example.com"],
                            },
                        },
                    }
                )
                + "\n",
                encoding="utf-8",
            )
        return types.SimpleNamespace(returncode=0, stdout=json.dumps({"success": True, "summary": "ok"}), stderr="")

    def fake_refresh(*, surface: dict[str, object], **kwargs):
        refresh_calls.append(dict(surface))
        return {"status": "completed"}

    monkeypatch.setattr(takyon_core, "_store", lambda: store)
    monkeypatch.setattr(takyon_core, "_session_business_slug", lambda: "plannerly")
    monkeypatch.setattr(takyon_core, "_require_api_access", lambda *args, **kwargs: None)
    monkeypatch.setattr(takyon_core, "_should_run_claude_agent_in_docker", lambda _workspace_rel: False)
    monkeypatch.setattr(takyon_core, "_workspace_needs_runtime_ui_contract", lambda workspace_rel: workspace_rel == "product/site")
    monkeypatch.setattr(takyon_core, "_materialize_subuser_app_kit", lambda *args, **kwargs: None)
    monkeypatch.setattr(takyon_core, "_finalize_product_surface_refresh", fake_refresh)
    monkeypatch.setattr(takyon_core, "_product_surface_refresh_operations", lambda **_kwargs: [])
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
                "business": "plannerly",
                "workspace": "product/site",
                "instruction": "Build the first honest product surface.",
                "idempotency_key": "workspace-same-run-surface-contract-update",
                "install": False,
            }
        )
    )

    assert result["success"] is True
    assert result["worker_attempts"] == 1
    assert "surface_contract_retries" not in result
    assert "surface_contract_update" not in result
    assert len(refresh_calls) == 1
    assert refresh_calls[0]["runtime_features"] == ["auth", "account", "checkout"]
    assert len(captured_payloads) == 1
    assert "Declared runtime-backed features for this app: auth, account, checkout" in str(captured_payloads[0]["instruction"])
    operations = store.commits[-1]["operations"]
    agent_record = next(op for op in operations if op.get("action") == "agent.record")
    assert "surface_contract_retries" not in agent_record["result"]
    assert "surface_contract_update" not in agent_record["result"]
