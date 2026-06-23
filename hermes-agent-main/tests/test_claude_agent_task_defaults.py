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
        self._workspace_revision_cache: dict[str, int] = {}
        self._workspace_base_revision: dict[str, int] = {}
        self._workspace_storage_backend_override = None
        self._head_revision_by_slug: dict[str, int] = {}
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

    def _business_head_revision(self, business: str):
        return int(self._head_revision_by_slug.get(business, 0) or 0)

    def _canonical_workspace_revision(self, business: str):
        if self._workspace_root_override is not None:
            return int(self._workspace_base_revision.get(business, 0) or 0)
        return int(self._workspace_revision_cache.get(business, self._business_head_revision(business)) or 0)

    def _sync_business_workspace_remote(self, business: str):
        workspace = self._business_root(business)
        if not workspace.exists():
            return "skipped_missing_workspace"
        backend = self._workspace_storage_backend()
        current_head = self._business_head_revision(business)
        next_revision = current_head + 1
        current_files = {}
        if current_head > 0:
            current_files = (
                takyon_storage.read_workspace_manifest(backend, business, current_head).get("files") or {}
            )
        candidate_files = takyon_storage.workspace_source_digests(workspace)
        if current_files == candidate_files:
            self._workspace_revision_cache[business] = current_head
            if self._workspace_root_override is not None:
                self._workspace_base_revision[business] = current_head
            return "synced"
        takyon_storage.write_workspace_revision(backend, business, next_revision, workspace)
        self._head_revision_by_slug[business] = next_revision
        self._workspace_revision_cache[business] = next_revision
        if self._workspace_root_override is not None:
            self._workspace_base_revision[business] = next_revision
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
    monkeypatch.setattr(
        takyon_core,
        "_claude_agent_non_docker_worker_env",
        lambda business, operator_user_id: {"CLAUDE_AGENT_SDK_CLIENT_APP": "takyon-business-agent"},
    )

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
    monkeypatch.setattr(
        takyon_core,
        "_claude_agent_non_docker_worker_env",
        lambda business, operator_user_id: {"CLAUDE_AGENT_SDK_CLIENT_APP": "takyon-business-agent"},
    )

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
    monkeypatch.setattr(
        takyon_core,
        "_claude_agent_non_docker_worker_env",
        lambda business, operator_user_id: {"CLAUDE_AGENT_SDK_CLIENT_APP": "takyon-business-agent"},
    )

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
    # Backstop default when the caller omits guidance_skills: base method + one generic style,
    # NOT all six. The bootstrap CEO normally passes the single fitting pack explicitly; this lean
    # fallback keeps an omitting caller from carrying all six packs through the worker loop.
    assert result["guidance_skills"] == [
        "claude-design",
        "claude-design-openai",
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
    monkeypatch.setattr(
        takyon_core,
        "_claude_agent_non_docker_worker_env",
        lambda business, operator_user_id: {"CLAUDE_AGENT_SDK_CLIENT_APP": "takyon-business-agent"},
    )

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
    monkeypatch.setattr(
        takyon_core,
        "_claude_agent_non_docker_worker_env",
        lambda business, operator_user_id: {"CLAUDE_AGENT_SDK_CLIENT_APP": "takyon-business-agent"},
    )

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
    # The lean backstop default is base method + one generic style — NOT a per-business pick and NOT
    # all six. The CEO selects the fitting pack explicitly in the bootstrap path; this fallback only
    # fires when guidance_skills is omitted entirely.
    assert result["guidance_skills"] == [
        "claude-design",
        "claude-design-openai",
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
    monkeypatch.setattr(
        takyon_core,
        "_claude_agent_non_docker_worker_env",
        lambda business, operator_user_id: {"CLAUDE_AGENT_SDK_CLIENT_APP": "takyon-business-agent"},
    )

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
    monkeypatch.setattr(
        takyon_core,
        "_claude_agent_non_docker_worker_env",
        lambda business, operator_user_id: {"CLAUDE_AGENT_SDK_CLIENT_APP": "takyon-business-agent"},
    )

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
    store = _FakeStore(outer_home)
    store._workspace_root_override = outer_home

    def fake_docker_runner(*, payload: dict[str, object], workspace_path: Path, timeout_ms: int, business: str = "", operator_user_id: str = ""):
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
    monkeypatch.setattr(
        takyon_core,
        "_claude_agent_non_docker_worker_env",
        lambda business, operator_user_id: {"CLAUDE_AGENT_SDK_CLIENT_APP": "takyon-business-agent"},
    )

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


def test_commit_business_workspace_revision_is_noop_when_source_tree_is_unchanged(tmp_path, monkeypatch, pg_store_dsn):
    monkeypatch.setenv("TAKYON_HOME", str(tmp_path / ".takyon-home"))
    monkeypatch.setenv("DATABASE_URL", pg_store_dsn)
    store = takyon_core.TakyonStore(root=tmp_path / "outer-home", operator_user_id="user-123", database_url=pg_store_dsn)
    monkeypatch.setenv("TAKYON_STORAGE_BACKEND", "local")
    monkeypatch.setenv("TAKYON_STORAGE_LOCAL_DIR", str(tmp_path / "storage"))

    with store._connect() as conn:
        with conn:
            conn.execute(
                "INSERT INTO businesses (slug, name, mode, goal, status, work_focus, budget_json, created_at, updated_at) VALUES (?, ?, 'live', '', 'active', 'all', ?, ?, ?)",
                ("ledgerleaf", "Ledgerleaf", "{}", takyon_core._now(), takyon_core._now()),
            )

    root = store._business_root("ledgerleaf", sync=False)
    landing = root / "product" / "site" / "src" / "screens" / "landing.tsx"
    landing.parent.mkdir(parents=True, exist_ok=True)
    landing.write_text("export function LandingScreen(){return <main>one</main>;}\n", encoding="utf-8")

    assert store._sync_business_workspace_remote("ledgerleaf") == "synced"
    first_revision = store._business_head_revision("ledgerleaf")
    assert first_revision == 1

    assert store._sync_business_workspace_remote("ledgerleaf") == "synced"
    assert store._business_head_revision("ledgerleaf") == first_revision


def test_claude_agent_task_blocks_docker_product_site_when_canonical_readback_diverges(tmp_path, monkeypatch):
    outer_home = tmp_path / "outer-home"
    workspace = outer_home / "businesses" / "latexflow" / "product" / "site"
    screens = workspace / "src" / "screens"
    screens.mkdir(parents=True, exist_ok=True)
    store = _FakeStore(outer_home)
    store._workspace_root_override = outer_home

    def fake_sync(_business: str):
        status = _FakeStore._sync_business_workspace_remote(store, _business)
        backend = store._workspace_storage_backend()
        head_revision = store._business_head_revision("latexflow")
        mounted = tmp_path / "mounted-canonical"
        takyon_storage.materialize_workspace_revision(backend, "latexflow", head_revision, mounted, delete_local=True)
        canonical_landing = mounted / "product" / "site" / "src" / "screens" / "landing.tsx"
        canonical_landing.write_text(
            "export function LandingScreen() {\n  return <main aria-hidden=\"true\" data-takyon-scaffold=\"landing\" />;\n}\n",
            encoding="utf-8",
        )
        takyon_storage.write_workspace_revision(backend, "latexflow", head_revision + 1, mounted)
        store._head_revision_by_slug["latexflow"] = head_revision + 1
        return status

    store._sync_business_workspace_remote = fake_sync

    def fake_docker_runner(*, payload: dict[str, object], workspace_path: Path, timeout_ms: int, business: str = "", operator_user_id: str = ""):
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
    monkeypatch.setattr(
        takyon_core,
        "_claude_agent_non_docker_worker_env",
        lambda business, operator_user_id: {"CLAUDE_AGENT_SDK_CLIENT_APP": "takyon-business-agent"},
    )

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

    def fake_docker_runner(*, payload: dict[str, object], workspace_path: Path, timeout_ms: int, business: str = "", operator_user_id: str = ""):
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
    monkeypatch.setattr(
        takyon_core,
        "_claude_agent_non_docker_worker_env",
        lambda business, operator_user_id: {"CLAUDE_AGENT_SDK_CLIENT_APP": "takyon-business-agent"},
    )

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
    # The worker is key-free via the safebox proxy (the only path); stand up the proxy so the host-user
    # / tmpfs / security-arg assembly under test runs.
    monkeypatch.setenv("TAKYON_CLAUDE_AGENT_BROKER", "1")
    monkeypatch.setenv("TAKYON_CLAUDE_AGENT_BROKER_URL", "http://10.116.0.2:8000")
    monkeypatch.setattr(
        takyon_core,
        "_mint_claude_agent_operator_session_token",
        lambda business, operator_user_id: "cap-host-user",
    )

    run_cmd, payload, worker_cwd, worker_env = takyon_core._run_claude_agent_task_in_docker(
        payload={
            "business": "latexflow",
            "workspace": "product/site",
            "instruction": "Build the product shell.",
        },
        workspace_path=workspace,
        timeout_ms=30_000,
        business="latexflow",
        operator_user_id="user-123",
    )

    joined = " ".join(run_cmd)
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
    # No raw provider key from _runtime_env leaks into the container even though one was present.
    assert "ANTHROPIC_API_KEY=test-key" not in joined
    assert "ANTHROPIC_API_KEY=cap-host-user" in joined


def test_run_claude_agent_task_in_docker_authenticates_to_proxy_with_capability(tmp_path, monkeypatch):
    """The worker reaches the safebox PROXY key-free: ANTHROPIC_BASE_URL is the safebox ROOT (the SDK
    appends /v1/messages) and ANTHROPIC_API_KEY is the minted operator capability — no raw provider key
    from _runtime_env / safebox ever enters the container."""
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True)
    repo_root = tmp_path / "repo"
    repo_root.mkdir(parents=True)

    from tools.environments import docker as docker_env

    monkeypatch.setattr(docker_env, "find_docker", lambda: "/usr/bin/docker")
    monkeypatch.setattr(docker_env, "_resolve_host_user_spec", lambda: None)
    monkeypatch.setattr(docker_env, "_host_user_identity_mount_args", lambda user_spec: [])
    monkeypatch.setattr(docker_env, "_build_security_args", lambda run_as_host_user=False: [])
    monkeypatch.setattr(takyon_core, "_repo_root", lambda: repo_root)
    monkeypatch.setattr(takyon_core, "_docker_claude_worker_binary_mounts", lambda **kwargs: ([], {}))
    # _runtime_env would otherwise surface a raw provider secret; ensure even then it never reaches the
    # container under the proxy path.
    monkeypatch.setattr(
        takyon_core,
        "_runtime_env",
        lambda extra=None: {"ANTHROPIC_API_KEY": "raw-should-not-leak", **(extra or {})},
    )

    # Lockdown defaults ON because a remote safebox is configured (no explicit broker flag).
    monkeypatch.delenv("TAKYON_CLAUDE_AGENT_BROKER", raising=False)
    monkeypatch.setenv("TAKYON_SAFEBOX_URL", "http://10.116.0.2:8000")
    monkeypatch.delenv("TAKYON_CLAUDE_AGENT_BROKER_URL", raising=False)
    monkeypatch.delenv("TAKYON_CLAUDE_AGENT_BROKER_NETWORK", raising=False)
    monkeypatch.setattr(
        takyon_core,
        "_mint_claude_agent_operator_session_token",
        lambda business, operator_user_id: "cap-anthropic-msgs",
    )

    run_cmd, _payload, _worker_cwd, _worker_env = takyon_core._run_claude_agent_task_in_docker(
        payload={
            "business": "latexflow",
            "workspace": "product/site",
            "instruction": "Build the product shell.",
        },
        workspace_path=workspace,
        timeout_ms=30_000,
        business="latexflow",
        operator_user_id="user-123",
    )

    joined = " ".join(run_cmd)
    # Base URL is the safebox ROOT (no /v1/messages suffix — the SDK appends it).
    assert "ANTHROPIC_BASE_URL=http://10.116.0.2:8000" in joined
    assert "/v1/messages" not in joined
    # Auth is the minted operator.session token, never a raw key.
    assert "ANTHROPIC_API_KEY=cap-anthropic-msgs" in joined
    assert "raw-should-not-leak" not in joined
    assert "ANTHROPIC_TOKEN=" not in joined
    # No confined network configured → default bridge (still key-free via host NAT).
    assert "--network" not in run_cmd


def test_claude_agent_operator_session_audience_accepted_by_proxy(monkeypatch):
    """The worker mints an operator.session token (via /v1/operator/session-token); the proxy's operator
    authorizer accepts that audience on every Anthropic call, so the minted token is a credential the
    proxy authorizes."""
    from plugins.takyon import safebox_app

    # The session-token mint binds the operator.session audience, and the proxy authorizer always folds
    # that audience into its accepted set (safebox_provider_proxy._authorize_operator_proxy).
    assert safebox_app._OPERATOR_SESSION_AUDIENCE == "operator.session"
    assert (
        safebox_app._ACTION_AUDIENCE_DEFAULTS[safebox_app._OPERATOR_SESSION_AUDIENCE]
        == safebox_app._OPERATOR_SESSION_AUDIENCE
    )


def test_run_claude_agent_task_in_docker_resolves_anthropic_env_from_safebox(tmp_path, monkeypatch):
    """Even when a raw provider key sits in the host env, the proxy path keeps it OUT of the container:
    the SDK is keyed only by the minted operator.session token against the proxy root, and the raw-key
    resolver no longer exists on the worker plane."""
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True)
    repo_root = tmp_path / "repo"
    repo_root.mkdir(parents=True)

    from tools.environments import docker as docker_env

    monkeypatch.setenv("ANTHROPIC_API_KEY", "raw-api-key-should-not-leak")
    monkeypatch.setenv("ANTHROPIC_TOKEN", "raw-oauth-token-should-not-leak")
    monkeypatch.setattr(docker_env, "find_docker", lambda: "/usr/bin/docker")
    monkeypatch.setattr(docker_env, "_resolve_host_user_spec", lambda: None)
    monkeypatch.setattr(docker_env, "_host_user_identity_mount_args", lambda user_spec: [])
    monkeypatch.setattr(docker_env, "_build_security_args", lambda run_as_host_user=False: [])
    monkeypatch.setattr(takyon_core, "_repo_root", lambda: repo_root)
    monkeypatch.setattr(takyon_core, "_docker_claude_worker_binary_mounts", lambda **kwargs: ([], {}))

    monkeypatch.setenv("TAKYON_CLAUDE_AGENT_BROKER", "1")
    monkeypatch.setenv("TAKYON_CLAUDE_AGENT_BROKER_URL", "http://10.116.0.2:8000")
    monkeypatch.setattr(
        takyon_core,
        "_mint_claude_agent_operator_session_token",
        lambda business, operator_user_id: "cap-safebox",
    )

    run_cmd, _payload, _worker_cwd, _worker_env = takyon_core._run_claude_agent_task_in_docker(
        payload={
            "business": "latexflow",
            "workspace": "product/site",
            "instruction": "Build the product shell.",
        },
        workspace_path=workspace,
        timeout_ms=30_000,
        business="latexflow",
        operator_user_id="user-123",
    )

    joined = " ".join(run_cmd)
    # The raw key sitting in the host env NEVER enters the container; only the session token does.
    assert "raw-api-key-should-not-leak" not in joined
    assert "raw-oauth-token-should-not-leak" not in joined
    assert "ANTHROPIC_TOKEN=" not in joined
    assert "ANTHROPIC_API_KEY=cap-safebox" in joined
    assert "ANTHROPIC_BASE_URL=http://10.116.0.2:8000" in joined


def _patch_docker_for_lockdown(tmp_path, monkeypatch):
    """Shared docker stubs so the lockdown tests exercise the real env/network assembly."""
    from tools.environments import docker as docker_env

    monkeypatch.setattr(docker_env, "find_docker", lambda: "/usr/bin/docker")
    monkeypatch.setattr(docker_env, "_resolve_host_user_spec", lambda: None)
    monkeypatch.setattr(docker_env, "_host_user_identity_mount_args", lambda user_spec: [])
    monkeypatch.setattr(docker_env, "_build_security_args", lambda run_as_host_user=False: [])
    repo_root = tmp_path / "repo"
    repo_root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(takyon_core, "_repo_root", lambda: repo_root)
    monkeypatch.setattr(takyon_core, "_docker_claude_worker_binary_mounts", lambda **kwargs: ([], {}))
    monkeypatch.setattr(takyon_core, "_runtime_env", lambda extra=None: dict(extra or {}))


def test_run_claude_agent_task_in_docker_lockdown_drops_raw_key_and_confines_network(tmp_path, monkeypatch):
    """STEP D: with the broker lockdown ON the container gets NO raw provider key, is pointed at the
    safebox broker with a minted capability token, and is --network-confined to the safebox only."""
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True)
    _patch_docker_for_lockdown(tmp_path, monkeypatch)

    monkeypatch.setenv("ANTHROPIC_API_KEY", "raw-provider-key-should-not-leak")
    monkeypatch.setenv("ANTHROPIC_TOKEN", "raw-provider-token-should-not-leak")
    monkeypatch.setenv("TAKYON_CLAUDE_AGENT_BROKER", "1")
    monkeypatch.setenv("TAKYON_CLAUDE_AGENT_BROKER_URL", "https://safebox.internal")
    monkeypatch.setenv("TAKYON_CLAUDE_AGENT_BROKER_NETWORK", "takyon-safebox-only")
    monkeypatch.setattr(
        takyon_core,
        "_mint_claude_agent_operator_session_token",
        lambda business, operator_user_id: "cap-token-xyz",
    )

    run_cmd, _payload, _worker_cwd, _worker_env = takyon_core._run_claude_agent_task_in_docker(
        payload={
            "business": "latexflow",
            "workspace": "product/site",
            "instruction": "Build the product shell.",
        },
        workspace_path=workspace,
        timeout_ms=30_000,
        business="latexflow",
        operator_user_id="user-123",
    )

    joined = " ".join(run_cmd)
    # No raw provider key/token anywhere in the container invocation.
    assert "raw-provider-key-should-not-leak" not in joined
    assert "raw-provider-token-should-not-leak" not in joined
    assert "ANTHROPIC_TOKEN=" not in joined
    # SDK pointed at the broker, authenticated with the minted capability token.
    assert "ANTHROPIC_BASE_URL=https://safebox.internal" in joined
    assert "ANTHROPIC_API_KEY=cap-token-xyz" in joined
    # Network confined to the safebox-only network (no default-bridge egress).
    assert "--network" in run_cmd
    assert run_cmd[run_cmd.index("--network") + 1] == "takyon-safebox-only"
    # Other sandbox flags preserved.
    assert "--read-only" in run_cmd
    assert "HOME=/tmp" in joined


def test_run_claude_agent_task_in_docker_lockdown_fails_closed_without_broker_url(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True)
    _patch_docker_for_lockdown(tmp_path, monkeypatch)

    monkeypatch.setenv("TAKYON_CLAUDE_AGENT_BROKER", "1")
    monkeypatch.delenv("TAKYON_CLAUDE_AGENT_BROKER_URL", raising=False)
    monkeypatch.delenv("TAKYON_SAFEBOX_URL", raising=False)

    import pytest

    with pytest.raises(takyon_core.TakyonError):
        takyon_core._run_claude_agent_task_in_docker(
            payload={"business": "latexflow", "workspace": "product/site", "instruction": "x"},
            workspace_path=workspace,
            timeout_ms=30_000,
            business="latexflow",
            operator_user_id="user-123",
        )


def test_run_claude_agent_task_in_docker_lockdown_fails_closed_without_token(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True)
    _patch_docker_for_lockdown(tmp_path, monkeypatch)

    monkeypatch.setenv("TAKYON_CLAUDE_AGENT_BROKER", "1")
    monkeypatch.setenv("TAKYON_CLAUDE_AGENT_BROKER_URL", "https://safebox.internal")
    monkeypatch.setenv("TAKYON_CLAUDE_AGENT_BROKER_NETWORK", "takyon-safebox-only")
    monkeypatch.setattr(
        takyon_core,
        "_mint_claude_agent_operator_session_token",
        lambda business, operator_user_id: "",
    )

    import pytest

    with pytest.raises(takyon_core.TakyonError):
        takyon_core._run_claude_agent_task_in_docker(
            payload={"business": "latexflow", "workspace": "product/site", "instruction": "x"},
            workspace_path=workspace,
            timeout_ms=30_000,
            business="latexflow",
            operator_user_id="user-123",
        )


def test_run_claude_agent_task_in_docker_keyless_without_network_uses_default_bridge(tmp_path, monkeypatch):
    """A confined network is OPTIONAL hardening: with NO network configured the worker is STILL key-free
    (proxy reachable via host NAT on the default bridge). Missing network must NOT fail closed."""
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True)
    _patch_docker_for_lockdown(tmp_path, monkeypatch)

    monkeypatch.setenv("ANTHROPIC_API_KEY", "raw-provider-key-should-not-leak")
    monkeypatch.setenv("TAKYON_CLAUDE_AGENT_BROKER", "1")
    monkeypatch.setenv("TAKYON_CLAUDE_AGENT_BROKER_URL", "http://10.116.0.2:8000")
    monkeypatch.delenv("TAKYON_CLAUDE_AGENT_BROKER_NETWORK", raising=False)
    monkeypatch.setattr(
        takyon_core,
        "_mint_claude_agent_operator_session_token",
        lambda business, operator_user_id: "cap-token-xyz",
    )

    run_cmd, _payload, _worker_cwd, _worker_env = takyon_core._run_claude_agent_task_in_docker(
        payload={"business": "latexflow", "workspace": "product/site", "instruction": "x"},
        workspace_path=workspace,
        timeout_ms=30_000,
        business="latexflow",
        operator_user_id="user-123",
    )

    joined = " ".join(run_cmd)
    # Still key-free against the proxy.
    assert "raw-provider-key-should-not-leak" not in joined
    assert "ANTHROPIC_BASE_URL=http://10.116.0.2:8000" in joined
    assert "ANTHROPIC_API_KEY=cap-token-xyz" in joined
    # No confined network → default bridge (no --network override).
    assert "--network" not in run_cmd


def test_run_claude_agent_task_in_docker_defaults_to_proxy_when_remote_safebox_configured(tmp_path, monkeypatch):
    """No explicit broker flag, but a remote safebox IS configured → lockdown defaults ON (key-free via
    the proxy). The raw-key worker path is gone, so this is the ONLY way the worker runs on a deployed
    plane."""
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True)
    _patch_docker_for_lockdown(tmp_path, monkeypatch)

    monkeypatch.delenv("TAKYON_CLAUDE_AGENT_BROKER", raising=False)
    monkeypatch.delenv("TAKYON_CLAUDE_AGENT_BROKER_URL", raising=False)
    monkeypatch.delenv("TAKYON_CLAUDE_AGENT_BROKER_NETWORK", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "raw-provider-key-should-not-leak")
    # The broker URL DEFAULTS to the configured safebox remote URL (root), and lockdown DEFAULTS ON
    # because a remote safebox is configured.
    monkeypatch.setenv("TAKYON_SAFEBOX_URL", "http://10.116.0.2:8000")
    monkeypatch.setattr(
        takyon_core,
        "_mint_claude_agent_operator_session_token",
        lambda business, operator_user_id: "cap-default-on",
    )

    run_cmd, _payload, _worker_cwd, _worker_env = takyon_core._run_claude_agent_task_in_docker(
        payload={"business": "latexflow", "workspace": "product/site", "instruction": "x"},
        workspace_path=workspace,
        timeout_ms=30_000,
        business="latexflow",
        operator_user_id="user-123",
    )

    joined = " ".join(run_cmd)
    assert "raw-provider-key-should-not-leak" not in joined
    # Base URL resolved from the safebox remote URL (root, no /v1/messages suffix).
    assert "ANTHROPIC_BASE_URL=http://10.116.0.2:8000" in joined
    assert "ANTHROPIC_API_KEY=cap-default-on" in joined


def test_run_claude_agent_task_in_docker_no_raw_key_path_when_lockdown_disabled(tmp_path, monkeypatch):
    """The legacy raw-key path is DELETED: explicitly disabling the broker with no remote safebox does
    NOT fall back to a raw key — it fails closed."""
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True)
    _patch_docker_for_lockdown(tmp_path, monkeypatch)

    monkeypatch.setenv("TAKYON_CLAUDE_AGENT_BROKER", "off")
    monkeypatch.delenv("TAKYON_SAFEBOX_URL", raising=False)
    monkeypatch.delenv("TAKYON_CLAUDE_AGENT_BROKER_URL", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "raw-key")

    import pytest

    with pytest.raises(takyon_core.TakyonError):
        takyon_core._run_claude_agent_task_in_docker(
            payload={"business": "latexflow", "workspace": "product/site", "instruction": "x"},
            workspace_path=workspace,
            timeout_ms=30_000,
            business="latexflow",
            operator_user_id="user-123",
        )


def test_non_docker_worker_env_uses_proxy_and_session_token_no_raw_key(monkeypatch):
    """The NON-docker (host-subprocess) Claude worker is key-free too: ANTHROPIC_BASE_URL = the safebox
    ROOT + ANTHROPIC_API_KEY = a minted operator.session token (real owner), NO raw provider key — closing
    the audit's open hole at the non-docker fallback."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "raw-key-should-not-leak")
    monkeypatch.setenv("ANTHROPIC_TOKEN", "raw-token-should-not-leak")
    monkeypatch.setenv("TAKYON_CLAUDE_AGENT_BROKER", "1")
    monkeypatch.setenv("TAKYON_CLAUDE_AGENT_BROKER_URL", "http://10.116.0.2:8000")

    minted: dict[str, object] = {}

    def fake_mint(business, operator_user_id):
        minted["business"] = business
        minted["operator_user_id"] = operator_user_id
        return "operator-session-token-xyz"

    monkeypatch.setattr(takyon_core, "_mint_claude_agent_operator_session_token", fake_mint)

    env = takyon_core._claude_agent_non_docker_worker_env("latexflow", "owner-1")

    # Pointed at the safebox ROOT (no /v1/messages suffix — the SDK appends it).
    assert env["ANTHROPIC_BASE_URL"] == "http://10.116.0.2:8000"
    # Credential is the minted session token, never the raw key sitting in the host env.
    assert env["ANTHROPIC_API_KEY"] == "operator-session-token-xyz"
    assert env["ANTHROPIC_API_KEY"] != "raw-key-should-not-leak"
    # The raw ANTHROPIC_TOKEN is never injected for the worker.
    assert "ANTHROPIC_TOKEN" not in env or env.get("ANTHROPIC_TOKEN") != "raw-token-should-not-leak"
    # Minted for the REAL owner + business.
    assert minted == {"business": "latexflow", "operator_user_id": "owner-1"}


def test_non_docker_worker_env_fails_closed_when_lockdown_disabled(monkeypatch):
    monkeypatch.setenv("TAKYON_CLAUDE_AGENT_BROKER", "off")
    monkeypatch.delenv("TAKYON_SAFEBOX_URL", raising=False)
    monkeypatch.delenv("TAKYON_CLAUDE_AGENT_BROKER_URL", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "raw-key")

    import pytest

    with pytest.raises(takyon_core.TakyonError):
        takyon_core._claude_agent_non_docker_worker_env("latexflow", "owner-1")


def test_non_docker_worker_env_fails_closed_when_mint_refused(monkeypatch):
    monkeypatch.setenv("TAKYON_CLAUDE_AGENT_BROKER", "1")
    monkeypatch.setenv("TAKYON_CLAUDE_AGENT_BROKER_URL", "http://10.116.0.2:8000")
    monkeypatch.setattr(
        takyon_core,
        "_mint_claude_agent_operator_session_token",
        lambda business, operator_user_id: "",
    )

    import pytest

    with pytest.raises(takyon_core.TakyonError):
        takyon_core._claude_agent_non_docker_worker_env("latexflow", "owner-1")


def test_mint_claude_agent_operator_session_token_passes_owner_to_safebox(monkeypatch):
    """The worker's session-token minter forwards the EXPLICIT resolved business owner to the safebox
    (not a session global), so the durable worker process mints for the real owner."""
    captured: dict[str, object] = {}

    def fake_mint(business, operator_user_id, *, max_cost_microusd):
        captured["business"] = business
        captured["operator_user_id"] = operator_user_id
        captured["max_cost_microusd"] = max_cost_microusd
        return "operator-session-token-xyz"

    monkeypatch.setattr(takyon_core.safebox, "mint_operator_session_token", fake_mint)

    token = takyon_core._mint_claude_agent_operator_session_token("latexflow", "owner-1")

    assert token == "operator-session-token-xyz"
    assert captured["business"] == "latexflow"
    assert captured["operator_user_id"] == "owner-1"
    assert captured["max_cost_microusd"] == takyon_core._CLAUDE_AGENT_BROKER_MAX_COST_MICROUSD


def test_mint_claude_agent_operator_session_token_fails_closed_on_error(monkeypatch):
    def boom(*a, **k):
        raise takyon_core.safebox.RemoteSafeboxError(
            "not_business_owner", status_code=403, payload={"detail": "not_business_owner"}
        )

    monkeypatch.setattr(takyon_core.safebox, "mint_operator_session_token", boom)
    # Any mint failure → "" so the caller refuses the run (no raw-key fallback).
    assert takyon_core._mint_claude_agent_operator_session_token("latexflow", "owner-1") == ""
    # Missing identity → "" without even calling the safebox.
    assert takyon_core._mint_claude_agent_operator_session_token("", "owner-1") == ""
    assert takyon_core._mint_claude_agent_operator_session_token("latexflow", "") == ""


def test_missing_env_for_requirement_accepts_safebox_backed_anthropic_token(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_TOKEN", raising=False)
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)

    def fake_read_env_backed_value(name: str) -> str:
        return "remote-oauth-token" if name == "ANTHROPIC_TOKEN" else ""

    monkeypatch.setattr(takyon_core.safebox, "read_env_backed_value", fake_read_env_backed_value)

    assert takyon_core._missing_env_for_requirement("anthropic") == []


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
    monkeypatch.setattr(
        takyon_core,
        "_claude_agent_non_docker_worker_env",
        lambda business, operator_user_id: {"CLAUDE_AGENT_SDK_CLIENT_APP": "takyon-business-agent"},
    )

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
    monkeypatch.setattr(
        takyon_core,
        "_claude_agent_non_docker_worker_env",
        lambda business, operator_user_id: {"CLAUDE_AGENT_SDK_CLIENT_APP": "takyon-business-agent"},
    )

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
    monkeypatch.setattr(
        takyon_core,
        "_claude_agent_non_docker_worker_env",
        lambda business, operator_user_id: {"CLAUDE_AGENT_SDK_CLIENT_APP": "takyon-business-agent"},
    )

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
    monkeypatch.setattr(
        takyon_core,
        "_claude_agent_non_docker_worker_env",
        lambda business, operator_user_id: {"CLAUDE_AGENT_SDK_CLIENT_APP": "takyon-business-agent"},
    )

    result = json.loads(
        handle_business_claude_agent_task(
            {
                "business": "latexflow",
                "workspace": "product/site",
                "instruction": "Run npm install and npm run build.",
                "idempotency_key": "workspace-product-turn-cap-retry",
                "install": False,
                "max_turns": 20,
                "refresh_surface": False,
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
    monkeypatch.setattr(
        takyon_core,
        "_claude_agent_non_docker_worker_env",
        lambda business, operator_user_id: {"CLAUDE_AGENT_SDK_CLIENT_APP": "takyon-business-agent"},
    )

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
