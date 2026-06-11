from __future__ import annotations

import json
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
        lambda skills: (list(skills), "[Hermes guidance skill: default-product-site]"),
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
            }
        )
    )

    instruction = str(captured["payload"]["instruction"])
    assert result["success"] is True
    assert result["guidance_skills"] == ["claude-design", "claude-design-openai"]
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
        lambda skills: (list(skills), "[Hermes guidance skill: default-product-site]"),
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
            }
        )
    )

    instruction = str(captured["payload"]["instruction"])
    assert result["success"] is True
    assert "Public landing composition contract:" in instruction
    assert "small centered island" in instruction
    assert "1320px" in instruction
    assert "520px" in instruction
    assert "mid-`500px` widths" in instruction
    assert "1600px" in instruction
    assert "90vw" in instruction
    assert "92vw" in instruction
    assert "1680px" in instruction
    assert "94vw" in instruction
    assert "1760px" in instruction
    assert "masthead/navigation lane" in instruction
    assert "58/42" in instruction


def test_claude_agent_task_chooses_vibrant_guidance_for_bold_consumer_brief(tmp_path, monkeypatch):
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
        lambda skills: (list(skills), "[Hermes guidance skill: inferred-product-site]"),
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
            }
        )
    )

    instruction = str(captured["payload"]["instruction"])
    assert result["success"] is True
    assert result["guidance_skills"] == ["claude-design", "claude-design-vibrant"]
    assert "claude-design-vibrant" in str(result["guidance_selection_reason"])
    assert "[Hermes guidance skill: inferred-product-site]" in instruction


def test_style_selector_prefers_openai_for_calm_editorial_ai_pet_brief():
    skill, reason = takyon_core._select_default_product_site_style_skill(
        surface={
            "notes": "AI coach for anxious first-time dog owners. Calm, trustworthy, editorial tone.",
            "customer_experience_shape": {
                "surface_goal": "Give serious, gentle guidance to worried new pet parents."
            },
        },
        instruction=(
            "Create a warm, calm, editorial landing page for an AI dog-parent coach. "
            "Avoid playful gimmicks and keep it trustworthy."
        ),
    )

    assert skill == "claude-design-openai"
    assert "claude-design-openai" in reason


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

    def fail_if_mounted(*args, **kwargs):
        raise AssertionError("mounted_business_workspace should not be used when a session workspace root is active")

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
    monkeypatch.setattr(takyon_core, "_record_claude_agent_runtime_event", lambda **_kwargs: None)
    monkeypatch.setattr(takyon_core, "_run_claude_agent_task_in_docker", fake_docker_runner)
    monkeypatch.setattr(takyon_core, "_run_claude_agent_task_process", fake_process)
    monkeypatch.setattr(takyon_storage, "mounted_business_workspace", fail_if_mounted)

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
