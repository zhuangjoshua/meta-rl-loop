from __future__ import annotations

import json
import shutil
import subprocess
import types
from contextlib import nullcontext
from pathlib import Path

import pytest

from plugins.takyon import core as takyon_core
from plugins.takyon import storage as takyon_storage
from plugins.takyon.core import handle_business_claude_agent_task


def test_docker_bind_retry_is_limited_to_prestart_mount_failure(tmp_path):
    workspace = tmp_path / "product" / "app"
    workspace.mkdir(parents=True)
    mount_failure = subprocess.CompletedProcess(
        ["docker", "run"],
        125,
        stdout="",
        stderr=(
            "docker: Error response from daemon: invalid mount config for type \"bind\": "
            f"bind source path does not exist: {workspace}"
        ),
    )
    worker_failure = subprocess.CompletedProcess(
        ["docker", "run"],
        1,
        stdout="",
        stderr="npm run build failed",
    )

    assert takyon_core._retryable_docker_bind_mount_failure(
        mount_failure,
        workspace_path=workspace,
    )
    assert not takyon_core._retryable_docker_bind_mount_failure(
        worker_failure,
        workspace_path=workspace,
    )
    workspace.rmdir()
    assert not takyon_core._retryable_docker_bind_mount_failure(
        mount_failure,
        workspace_path=workspace,
    )


@pytest.fixture(autouse=True)
def _pin_test_coding_worker_model(monkeypatch):
    monkeypatch.setenv("TAKYON_CLAUDE_AGENT_MODEL", "deepseek-v4-pro")
    monkeypatch.setattr(
        takyon_core,
        "_hold_business_product_writer_lease",
        lambda *_args, **_kwargs: nullcontext(),
    )
    monkeypatch.setattr(takyon_core, "_product_worker_runtime_snapshot", lambda root: root)


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

    def enforce_operator_business_access(self, *_args, **_kwargs):
        return None

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


def test_claude_agent_task_uses_broader_defaults_and_pinned_model_for_product_site_work(tmp_path, monkeypatch):
    monkeypatch.setenv("TAKYON_HOME", str(tmp_path))
    captured: dict[str, object] = {}
    runtime_events: list[dict[str, object]] = []

    def fake_process(*, payload: dict[str, object], **kwargs):
        captured["payload"] = payload
        Path(str(payload["cwd"]), "index.html").write_text("<h1>Latexflow</h1>\n", encoding="utf-8")
        return types.SimpleNamespace(
            returncode=0,
            stdout=json.dumps(
                {
                    "success": True,
                    "summary": "Now I have full context. Reading this as: an editorial landing. Dial: variance=5.",
                }
            ),
            stderr="",
        )

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
    monkeypatch.setattr(
        takyon_core,
        "_record_claude_agent_runtime_event",
        lambda **kwargs: runtime_events.append(kwargs),
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
                "idempotency_key": "workspace-faster-defaults",
                "install": False,
                "refresh_surface": False,
            }
        )
    )

    payload = captured["payload"]
    assert result["success"] is True
    assert payload["maxTurns"] == 60
    completed = [event for event in runtime_events if event.get("status") == "completed"]
    assert completed[-1]["detail"] == "Claude worker completed for product/site."
    assert not any("Reading this as" in str(event) for event in runtime_events)
    assert payload["timeoutMs"] == 900000
    assert payload["maxBudgetUsd"] == 8.0
    assert payload["effort"] == "medium"
    assert payload["model"] == "deepseek-v4-pro"


def test_claude_agent_model_pin_refuses_per_call_override(monkeypatch):
    monkeypatch.setenv("TAKYON_CLAUDE_AGENT_MODEL", "deepseek-v4-pro")

    with pytest.raises(takyon_core.TakyonError, match="model override refused"):
        takyon_core._resolve_claude_agent_model("claude-sonnet-5")


def test_claude_agent_model_has_no_implicit_fallback(monkeypatch):
    monkeypatch.delenv("TAKYON_CLAUDE_AGENT_MODEL", raising=False)
    monkeypatch.setattr(takyon_core, "_model_from_config", lambda *keys: "")

    with pytest.raises(takyon_core.TakyonError, match="no fallback model is available"):
        takyon_core._resolve_claude_agent_model()


def test_claude_agent_model_does_not_inherit_ceo_default(tmp_path, monkeypatch):
    monkeypatch.setenv("TAKYON_HOME", str(tmp_path))
    monkeypatch.delenv("TAKYON_CLAUDE_AGENT_MODEL", raising=False)
    (tmp_path / "config.yaml").write_text(
        "model:\n  provider: custom\n  default: gpt-5.5\n",
        encoding="utf-8",
    )

    with pytest.raises(takyon_core.TakyonError, match="no fallback model is available"):
        takyon_core._resolve_claude_agent_model()


def test_strict_worker_role_requires_deepseek_pin(monkeypatch):
    monkeypatch.setenv("TAKYON_STRICT_MODEL_ROLES", "1")
    monkeypatch.setenv("TAKYON_CLAUDE_AGENT_MODEL", "claude-sonnet-5")

    with pytest.raises(takyon_core.TakyonError, match="requires TAKYON_CLAUDE_AGENT_MODEL"):
        takyon_core._resolve_claude_agent_model()


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


def test_claude_agent_task_budget_bounds_and_model_are_env_overridable(tmp_path, monkeypatch):
    monkeypatch.setenv("TAKYON_HOME", str(tmp_path))
    monkeypatch.setenv("TAKYON_CLAUDE_AGENT_BUDGET_DEFAULT_USD", "25")
    monkeypatch.setenv("TAKYON_CLAUDE_AGENT_BUDGET_MAX_USD", "60")
    monkeypatch.setenv("TAKYON_CLAUDE_AGENT_MODEL", "claude-fable-5")
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

    # Omitted budget rides the env default; explicit budget above the stock 25 cap is allowed
    # when the env max raises it; the model env flips the worker without a per-call arg.
    result = json.loads(
        handle_business_claude_agent_task(
            {
                "business": "latexflow",
                "workspace": "product/site",
                "instruction": "Build the first honest product surface.",
                "idempotency_key": "workspace-env-budget-default",
                "install": False,
                "refresh_surface": False,
            }
        )
    )
    payload = captured["payload"]
    assert result["success"] is True
    assert payload["maxBudgetUsd"] == 25.0
    assert payload["model"] == "claude-fable-5"

    result = json.loads(
        handle_business_claude_agent_task(
            {
                "business": "latexflow",
                "workspace": "product/site",
                "instruction": "Build the first honest product surface.",
                "idempotency_key": "workspace-env-budget-raised-cap",
                "budget_usd": 40.0,
                "install": False,
                "refresh_surface": False,
            }
        )
    )
    payload = captured["payload"]
    assert result["success"] is True
    assert payload["maxBudgetUsd"] == 40.0


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
    assert result["guidance_skills"] == ["claude-design"]
    assert result["guidance_selection_reason"] == "defaulted to dense-product-safe Claude Design guidance"
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


def test_claude_agent_task_includes_visual_craft_contract_for_product_site(tmp_path, monkeypatch):
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
                "idempotency_key": "workspace-visual-craft-contract",
                "install": False,
                "refresh_surface": False,
            }
        )
    )

    instruction = str(captured["payload"]["instruction"])
    assert result["success"] is True
    assert "Product visual craft contract" in instruction
    assert "Never use emoji as UI iconography" in instruction
    assert "lucide-react" in instruction
    assert "framer-motion" in instruction
    assert "prefers-reduced-motion" in instruction

    # Every npm package the visual contract names MUST be pinned in the scaffold's own
    # package.json: worker-added dependencies are force-restored away on every surface
    # refresh, so a contract that names an unpinned dep instructs the worker into a
    # guaranteed build failure.
    scaffold_pkg = json.loads(
        (Path(takyon_core.__file__).parent / "subuser_app_kit" / "scaffold" / "package.json").read_text(encoding="utf-8")
    )
    pinned = set(scaffold_pkg.get("dependencies") or {})
    for named_dep in ("lucide-react", "framer-motion"):
        assert named_dep in pinned


def test_mobile_worker_contract_bans_emoji_iconography():
    assert "Never use emoji as UI iconography" in takyon_core.MOBILE_APP_WORKER_CONTRACT
    # Mobile stays on the pinned managed Expo SDK: the contract may only point at icon/motion
    # capability already inside that dependency closure, never a new package.
    assert "@expo/vector-icons" in takyon_core.MOBILE_APP_WORKER_CONTRACT
    assert "Animated" in takyon_core.MOBILE_APP_WORKER_CONTRACT
    assert "isReduceMotionEnabled" in takyon_core.MOBILE_APP_WORKER_CONTRACT
    # `react-native-svg` is outside the pinned closure; the contract must never point the
    # worker at plain "inline SVG" on mobile — that import breaks the managed build.
    mobile_pkg = json.loads(
        (Path(takyon_core.__file__).parent / "mobile_app_kit" / "scaffold" / "package.json").read_text(encoding="utf-8")
    )
    assert "react-native-svg" not in (mobile_pkg.get("dependencies") or {})
    assert "or inline SVG" not in takyon_core.MOBILE_APP_WORKER_CONTRACT


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
    assert result["guidance_skills"] == ["claude-design"]
    assert result["guidance_selection_reason"] == "defaulted to dense-product-safe Claude Design guidance"
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
    assert result["guidance_selection_reason"] == "used explicit customer-facing guidance exactly as requested"


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


def test_mobile_worker_materializes_product_app_before_constructing_docker_mount(tmp_path, monkeypatch):
    outer_home = tmp_path / "outer-home"
    workspace = outer_home / "businesses" / "sipstreak" / "product" / "app"
    captured: dict[str, object] = {}
    store = _FakeStore(outer_home)
    store._workspace_root_override = outer_home
    store._ensure_business = lambda _conn, business: {
        "owner_user_id": "user-123",
        "work_focus": "all",
        "slug": business,
        "name": "Sipstreak",
        "goal": "A hydration habit coach",
        "archetype": "mobile_app",
    }

    def fake_docker_runner(
        *,
        payload: dict[str, object],
        workspace_path: Path,
        timeout_ms: int,
        business: str = "",
        operator_user_id: str = "",
    ):
        # This is the exact seam that used to hand Docker a nonexistent product/app bind source.
        # Both the seed-completion marker and platform-owned runtime boundary must exist before the
        # docker command is even constructed.
        assert workspace_path == workspace
        assert workspace_path.is_dir()
        assert (workspace_path / "app.json").is_file()
        assert (workspace_path / "_takyon" / "runtime-client.ts").is_file()
        captured["docker_workspace_path"] = workspace_path
        captured["docker_timeout_ms"] = timeout_ms
        return ["docker", "run"], payload, str(tmp_path), {}

    def fake_process(*, payload: dict[str, object], **_kwargs):
        Path(str(payload["cwd"]), "worker-ran.txt").write_text("yes\n", encoding="utf-8")
        return types.SimpleNamespace(
            returncode=0,
            stdout=json.dumps({"success": True, "summary": "mobile source updated"}),
            stderr="",
        )

    monkeypatch.setenv("TAKYON_HOME", str(tmp_path))
    monkeypatch.setattr(takyon_core, "_store", lambda: store)
    monkeypatch.setattr(takyon_core, "_session_business_slug", lambda: "sipstreak")
    monkeypatch.setattr(takyon_core, "_require_api_access", lambda *args, **kwargs: None)
    monkeypatch.setattr(takyon_core, "_should_run_claude_agent_in_docker", lambda _workspace_rel: True)
    monkeypatch.setattr(takyon_core, "_workspace_needs_runtime_ui_contract", lambda _workspace_rel: False)
    monkeypatch.setattr(takyon_core, "_ensure_repo_node_dependencies", lambda packages: {"success": True})
    monkeypatch.setattr(
        takyon_core,
        "_reserve_operator_task_budget",
        lambda **_kwargs: {"reservation_key": "r-mobile", "reserved_cents": 800},
    )
    monkeypatch.setattr(
        takyon_core,
        "_finalize_operator_task_budget",
        lambda **_kwargs: {
            "reservation_key": "r-mobile",
            "reserved_cents": 800,
            "status": "charged",
        },
    )
    monkeypatch.setattr(takyon_core, "_record_claude_agent_runtime_event", lambda **_kwargs: None)
    monkeypatch.setattr(takyon_core, "_run_claude_agent_task_in_docker", fake_docker_runner)
    monkeypatch.setattr(takyon_core, "_run_claude_agent_task_process", fake_process)

    result = json.loads(
        handle_business_claude_agent_task(
            {
                "business": "sipstreak",
                "workspace": "product/app",
                "instruction": "Build the mobile app.",
                "idempotency_key": "mobile-materialize-before-mount",
                "refresh_surface": False,
                "install": False,
            }
        )
    )

    assert result["success"] is True
    assert captured["docker_workspace_path"] == workspace
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
    assert "TAKYON_CLAUDE_AGENT_IN_DOCKER=1" in joined
    assert "CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS=1" in joined
    # No confined network configured → default bridge (still key-free via host NAT).
    assert "--network" not in run_cmd


def test_claude_agent_operator_session_audience_accepted_by_proxy(monkeypatch):
    """The worker mints an operator.session token (via /v1/operator/session-token); the proxy's operator
    authorizer accepts that audience on every Anthropic call, so the minted token is a credential the
    proxy authorizes."""
    import time

    from plugins.takyon import safebox_app, safebox_provider_proxy
    from plugins.takyon.safebox_capability import CapabilityScope, mint_capability

    # The session-token mint binds the operator.session audience, and the proxy authorizer always folds
    # that audience into its accepted set (safebox_provider_proxy._authorize_operator_proxy).
    assert safebox_app._OPERATOR_SESSION_AUDIENCE == "operator.session"
    # It must NOT be admitted by the product/sub-user single-use action map: operator sessions are
    # minted only by the dedicated ownership-proven endpoint.
    assert safebox_app._OPERATOR_SESSION_AUDIENCE not in safebox_app._ACTION_AUDIENCE_DEFAULTS

    signing_key = b"operator-session-test-signing-key"
    monkeypatch.setenv("TAKYON_CAP_SIGNING_KEY", signing_key.decode())
    now = int(time.time())
    token = mint_capability(
        CapabilityScope(
            takyon_user_id="operator-1",
            business_slug="example-business",
            app_user_id=None,
            action=safebox_app._OPERATOR_SESSION_AUDIENCE,
            max_cost_microusd=100_000,
        ),
        signing_key=signing_key,
        audience=safebox_app._OPERATOR_SESSION_AUDIENCE,
        nonce="session-1",
        issued_at=now,
        ttl_seconds=300,
    )
    authorized = safebox_provider_proxy._authorize_operator_proxy(
        f"Bearer {token}",
        None,
        capability_audiences=frozenset({"anthropic.messages"}),
    )
    assert authorized.scope.takyon_user_id == "operator-1"
    assert authorized.via == "capability:operator.session"


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
    assert "CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS=1" in joined


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
    assert "CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS=1" in joined
    for key in (
        "TAKYON_CLAUDE_AGENT_MODEL",
        "ANTHROPIC_MODEL",
        "ANTHROPIC_DEFAULT_OPUS_MODEL",
        "ANTHROPIC_DEFAULT_SONNET_MODEL",
        "ANTHROPIC_DEFAULT_HAIKU_MODEL",
        "CLAUDE_CODE_SUBAGENT_MODEL",
    ):
        assert f"{key}=deepseek-v4-pro" in joined
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
    assert env["CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS"] == "1"
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
    monotonic_ticks = iter((100.0, 101.0, 102.0, 103.0))
    monkeypatch.setattr(takyon_core.time, "monotonic", lambda: next(monotonic_ticks))

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
                "timeout_ms": 900_000,
                "refresh_surface": False,
            }
        )
    )

    assert result["success"] is True
    assert [payload["maxTurns"] for payload in captured_payloads] == [20, 60]
    assert captured_payloads[0]["timeoutMs"] == 900_000
    assert 0 < captured_payloads[1]["timeoutMs"] < captured_payloads[0]["timeoutMs"]
    assert result["worker_attempts"] == 2
    assert result["turn_cap_retries"] == [{"from": 20, "to": 60}]
    operations = store.commits[-1]["operations"]
    agent_record = next(op for op in operations if op.get("action") == "agent.record")
    assert agent_record["result"]["turn_cap_retries"] == [{"from": 20, "to": 60}]


def test_taste_task_never_restarts_fresh_session_after_turn_cap(tmp_path, monkeypatch):
    monkeypatch.setenv("TAKYON_HOME", str(tmp_path))
    store = _FakeStore(tmp_path)
    process_calls: list[dict[str, object]] = []

    def fake_process(*, payload: dict[str, object], **_kwargs):
        process_calls.append(dict(payload))
        return types.SimpleNamespace(
            returncode=1,
            stdout=json.dumps(
                {
                    "success": False,
                    "error": "Claude Code returned an error result: Reached maximum number of turns (60)",
                }
            ),
            stderr="",
        )

    _patch_non_docker_product_site(monkeypatch, store)
    monkeypatch.setattr(
        takyon_core,
        "_reserve_operator_task_budget",
        lambda **_kwargs: {"reservation_key": "r1", "reserved_cents": 800},
    )
    monkeypatch.setattr(
        takyon_core,
        "_finalize_operator_task_budget",
        lambda **_kwargs: {"reservation_key": "r1", "reserved_cents": 800, "status": "charged"},
    )
    monkeypatch.setattr(takyon_core, "_run_claude_agent_task_process", fake_process)

    result = json.loads(
        handle_business_claude_agent_task(
            {
                "business": "latexflow",
                "workspace": "product/site",
                "instruction": "Implement and preflight the landing.",
                "guidance_skills": ["taste-frontend"],
                "idempotency_key": "taste-single-session-turn-cap",
                "install": False,
                "max_turns": 60,
                "timeout_ms": 900_000,
                "refresh_surface": False,
            }
        )
    )

    assert result["success"] is False
    assert result["worker_attempts"] == 1
    assert result["turn_cap_retries"] == []
    assert len(process_calls) == 1


def test_taste_task_never_restarts_fresh_session_after_build_blocker(tmp_path, monkeypatch):
    monkeypatch.setenv("TAKYON_HOME", str(tmp_path))
    store = _FakeStore(tmp_path)
    process_calls: list[dict[str, object]] = []

    def fake_process(*, payload: dict[str, object], **_kwargs):
        process_calls.append(dict(payload))
        Path(str(payload["cwd"]), "index.html").write_text("<h1>Latexflow</h1>\n", encoding="utf-8")
        Path(str(payload["cwd"]), "DESIGN.md").write_text(
            "# Design Read\nEditorial precision for proposal teams.\n\n"
            "DESIGN_VARIANCE: 6\nMOTION_INTENSITY: 4\nVISUAL_DENSITY: 5\n",
            encoding="utf-8",
        )
        return types.SimpleNamespace(
            returncode=0,
            stdout=json.dumps({"success": True, "summary": "implemented"}),
            stderr="",
        )

    _patch_non_docker_product_site(monkeypatch, store)
    monkeypatch.setattr(
        takyon_core,
        "_workspace_needs_runtime_ui_contract",
        lambda workspace_rel: workspace_rel == "product/site",
    )
    monkeypatch.setattr(takyon_core, "_materialize_subuser_app_kit", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        takyon_core,
        "_reserve_operator_task_budget",
        lambda **_kwargs: {"reservation_key": "r1", "reserved_cents": 800},
    )
    monkeypatch.setattr(
        takyon_core,
        "_finalize_operator_task_budget",
        lambda **_kwargs: {"reservation_key": "r1", "reserved_cents": 800, "status": "charged"},
    )
    monkeypatch.setattr(takyon_core, "_run_claude_agent_task_process", fake_process)
    monkeypatch.setattr(
        takyon_core,
        "_finalize_product_surface_refresh",
        lambda **_kwargs: {
            "status": "failed",
            "source_path": "product/site",
            "receipt_path": "metrics/receipts/product-surface/taste-build-blocker.json",
            "runtime_features": [],
            "inventory": {},
            "error": "typecheck failed: unused variable",
            "blocker": "typecheck failed: unused variable",
            "publish": {"status": "blocked", "blocker": "typecheck failed: unused variable"},
        },
    )

    result = json.loads(
        handle_business_claude_agent_task(
            {
                "business": "latexflow",
                "workspace": "product/site",
                "instruction": "Implement and preflight the landing.",
                "guidance_skills": ["taste-frontend"],
                "idempotency_key": "taste-single-session-build-blocker",
                "install": False,
                "max_turns": 60,
                "timeout_ms": 900_000,
                "refresh_surface": True,
            }
        )
    )

    assert result["success"] is False
    assert result["blocked"] is True
    assert result["worker_attempts"] == 1
    assert len(process_calls) == 1


def test_successful_taste_worker_with_policy_publish_blocker_is_not_human_review(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("TAKYON_HOME", str(tmp_path))

    class _CapturingStore(_FakeStore):
        def __init__(self, root):
            super().__init__(root)
            self.commits: list[dict[str, object]] = []

        def commit(self, **kwargs):
            self.commits.append(dict(kwargs))
            return {"success": True}

    store = _CapturingStore(tmp_path)

    def fake_process(*, payload: dict[str, object], **_kwargs):
        root = Path(str(payload["cwd"]))
        root.joinpath("index.html").write_text("<h1>ProposalProof</h1>\n", encoding="utf-8")
        root.joinpath("DESIGN.md").write_text(
            "# Design Read\nEditorial precision.\n\n"
            "DESIGN_VARIANCE: 4\nMOTION_INTENSITY: 2\nVISUAL_DENSITY: 3\n",
            encoding="utf-8",
        )
        return types.SimpleNamespace(
            returncode=0,
            stdout=json.dumps({"success": True, "summary": "implemented"}),
            stderr="",
        )

    _patch_non_docker_product_site(monkeypatch, store, session_slug="proposalproof")
    monkeypatch.setattr(
        takyon_core,
        "_workspace_needs_runtime_ui_contract",
        lambda workspace_rel: workspace_rel == "product/site",
    )
    monkeypatch.setattr(takyon_core, "_materialize_subuser_app_kit", lambda *_a, **_k: None)
    monkeypatch.setattr(
        takyon_core,
        "_reserve_operator_task_budget",
        lambda **_kwargs: {"reservation_key": "r1", "reserved_cents": 800},
    )
    monkeypatch.setattr(
        takyon_core,
        "_finalize_operator_task_budget",
        lambda **_kwargs: {"reservation_key": "r1", "reserved_cents": 800, "status": "charged"},
    )
    monkeypatch.setattr(takyon_core, "_run_claude_agent_task_process", fake_process)
    monkeypatch.setattr(
        takyon_core,
        "_finalize_product_surface_refresh",
        lambda **_kwargs: {
            "status": "passed",
            "source_path": "product/site",
            "receipt_path": "metrics/receipts/product-surface/policy-blocked.json",
            "runtime_features": [],
            "inventory": {},
            "blocker": "subscription policy copy is worker-authored",
            "publish": {
                "status": "blocked",
                "blocker": "subscription policy copy is worker-authored",
            },
        },
    )
    monkeypatch.setattr(takyon_core, "_product_surface_refresh_operations", lambda **_kwargs: [])

    with takyon_core._bound_operator_task_context(
        run_id="bootstrap-job", task_kind="ceo_bootstrap", attempt=1
    ):
        result = json.loads(
            handle_business_claude_agent_task(
                {
                    "business": "proposalproof",
                    "workspace": "product/site",
                    "instruction": "Implement and preflight the landing.",
                    "guidance_skills": ["taste-frontend"],
                    "idempotency_key": "taste-policy-publish-blocker",
                    "install": False,
                    "max_turns": 60,
                    "timeout_ms": 900_000,
                    "refresh_surface": True,
                }
            )
        )

    assert result["blocked"] is True
    assert result["review_required"] is False
    operations = store.commits[-1]["operations"]
    assert not any(
        op.get("event_type") == "bootstrap.human_review_required" for op in operations
    )
    agent_record = next(op for op in operations if op.get("action") == "agent.record")
    assert agent_record["result"]["review_required"] is False


def test_taste_success_without_durable_design_contract_blocks_before_publish(tmp_path, monkeypatch):
    monkeypatch.setenv("TAKYON_HOME", str(tmp_path))
    store = _FakeStore(tmp_path)
    process_calls: list[dict[str, object]] = []

    def fake_process(*, payload: dict[str, object], **_kwargs):
        process_calls.append(dict(payload))
        Path(str(payload["cwd"]), "index.html").write_text(
            "<h1>Latexflow</h1>\n", encoding="utf-8"
        )
        return types.SimpleNamespace(
            returncode=0,
            stdout=json.dumps({"success": True, "summary": "implemented"}),
            stderr="",
        )

    _patch_non_docker_product_site(monkeypatch, store)
    monkeypatch.setattr(
        takyon_core,
        "_workspace_needs_runtime_ui_contract",
        lambda workspace_rel: workspace_rel == "product/site",
    )
    monkeypatch.setattr(takyon_core, "_materialize_subuser_app_kit", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        takyon_core,
        "_reserve_operator_task_budget",
        lambda **_kwargs: {"reservation_key": "r1", "reserved_cents": 800},
    )
    monkeypatch.setattr(
        takyon_core,
        "_finalize_operator_task_budget",
        lambda **_kwargs: {"reservation_key": "r1", "reserved_cents": 800, "status": "charged"},
    )
    monkeypatch.setattr(takyon_core, "_run_claude_agent_task_process", fake_process)
    monkeypatch.setattr(
        takyon_core,
        "_finalize_product_surface_refresh",
        lambda **_kwargs: pytest.fail("missing Taste contract must block before publish"),
    )

    result = json.loads(
        handle_business_claude_agent_task(
            {
                "business": "latexflow",
                "workspace": "product/site",
                "instruction": "Implement and preflight the landing.",
                "guidance_skills": ["design-taste-frontend"],
                "idempotency_key": "taste-missing-design-contract",
                "install": False,
                "max_turns": 60,
                "timeout_ms": 900_000,
                "refresh_surface": True,
            }
        )
    )

    assert result["success"] is False
    assert result["blocked"] is True
    assert result["worker_attempts"] == 1
    assert result["taste_design_contract"] is None
    assert "Taste design contract missing" in result["error"]
    assert len(process_calls) == 1


def test_taste_timeout_without_design_contract_stops_parent_for_human_review(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv("TAKYON_HOME", str(tmp_path))

    class _CapturingStore(_FakeStore):
        def __init__(self, root):
            super().__init__(root)
            self.commits: list[dict[str, object]] = []

        def commit(self, **kwargs):
            self.commits.append(dict(kwargs))
            return {"success": True}

    store = _CapturingStore(tmp_path)
    process_calls: list[dict[str, object]] = []

    def fake_process(*, payload: dict[str, object], **_kwargs):
        process_calls.append(dict(payload))
        Path(str(payload["cwd"]), "index.html").write_text(
            "<h1>unfinished Taste source</h1>\n",
            encoding="utf-8",
        )
        raise subprocess.TimeoutExpired(cmd=["node"], timeout=1.0)

    _patch_non_docker_product_site(monkeypatch, store)
    monkeypatch.setattr(
        takyon_core,
        "_workspace_needs_runtime_ui_contract",
        lambda workspace_rel: workspace_rel == "product/site",
    )
    monkeypatch.setattr(takyon_core, "_materialize_subuser_app_kit", lambda *_a, **_k: None)
    monkeypatch.setattr(
        takyon_core,
        "_reserve_operator_task_budget",
        lambda **_kwargs: {"reservation_key": "r1", "reserved_cents": 800},
    )
    monkeypatch.setattr(
        takyon_core,
        "_finalize_operator_task_budget",
        lambda **_kwargs: {
            "reservation_key": "r1",
            "reserved_cents": 800,
            "status": "settled_estimate",
        },
    )
    monkeypatch.setattr(takyon_core, "_run_claude_agent_task_process", fake_process)
    monkeypatch.setattr(
        takyon_core,
        "_finalize_product_surface_refresh",
        lambda **_kwargs: pytest.fail("invalid timed-out Taste contract must block before publish"),
    )

    with takyon_core._bound_operator_task_context(
        run_id="bootstrap-job",
        task_kind="ceo_bootstrap",
        attempt=2,
    ):
        result = json.loads(
            handle_business_claude_agent_task(
                {
                    "business": "latexflow",
                    "workspace": "product/site",
                    "instruction": "Implement and preflight the landing.",
                    "guidance_skills": ["taste-frontend"],
                    "idempotency_key": "taste-timeout-missing-design-contract",
                    "install": False,
                    "max_turns": 60,
                    "timeout_ms": 900_000,
                    "refresh_surface": True,
                }
            )
        )

    assert result["success"] is False
    assert result["blocked"] is True
    assert result["timed_out"] is True
    assert result["review_required"] is True
    assert "Taste design contract missing" in result["review_blocker"]
    assert len(process_calls) == 1
    operations = store.commits[-1]["operations"]
    human_review_event = next(
        op
        for op in operations
        if op.get("event_type") == "bootstrap.human_review_required"
    )
    assert human_review_event["payload"]["operator_task"] == {
        "run_id": "bootstrap-job",
        "task_kind": "ceo_bootstrap",
        "attempt": 2,
    }
    assert human_review_event["payload"]["timed_out"] is True
    assert any(op.get("action") == "agent.record" for op in operations)


def test_taste_timeout_published_partial_still_requires_human_review(tmp_path, monkeypatch):
    monkeypatch.setenv("TAKYON_HOME", str(tmp_path))

    class _CapturingStore(_FakeStore):
        def __init__(self, root):
            super().__init__(root)
            self.commits: list[dict[str, object]] = []

        def commit(self, **kwargs):
            self.commits.append(dict(kwargs))
            return {"success": True}

    store = _CapturingStore(tmp_path)

    def fake_process(*, payload: dict[str, object], **_kwargs):
        root = Path(str(payload["cwd"]))
        (root / "index.html").write_text("<h1>publishable partial</h1>\n", encoding="utf-8")
        (root / "DESIGN.md").write_text(
            "# Design Read\nEditorial precision for proposal teams.\n\n"
            "DESIGN_VARIANCE: 6\nMOTION_INTENSITY: 4\nVISUAL_DENSITY: 5\n",
            encoding="utf-8",
        )
        raise subprocess.TimeoutExpired(cmd=["node"], timeout=1.0)

    _patch_non_docker_product_site(monkeypatch, store)
    monkeypatch.setattr(
        takyon_core,
        "_workspace_needs_runtime_ui_contract",
        lambda workspace_rel: workspace_rel == "product/site",
    )
    monkeypatch.setattr(takyon_core, "_materialize_subuser_app_kit", lambda *_a, **_k: None)
    monkeypatch.setattr(
        takyon_core,
        "_reserve_operator_task_budget",
        lambda **_kwargs: {"reservation_key": "r1", "reserved_cents": 800},
    )
    monkeypatch.setattr(
        takyon_core,
        "_finalize_operator_task_budget",
        lambda **_kwargs: {
            "reservation_key": "r1",
            "reserved_cents": 800,
            "status": "settled_estimate",
        },
    )
    monkeypatch.setattr(takyon_core, "_run_claude_agent_task_process", fake_process)
    monkeypatch.setattr(
        takyon_core,
        "_finalize_product_surface_refresh",
        lambda **_kwargs: {
            "status": "passed",
            "source_path": "product/site",
            "receipt_path": "metrics/receipts/product-surface/taste-timeout-partial.json",
            "runtime_features": [],
            "inventory": {},
            "publish": {
                "status": "published",
                "public_url": "https://latexflow.coscale.app/",
            },
            "blocker": "",
        },
    )

    with takyon_core._bound_operator_task_context(
        run_id="bootstrap-job",
        task_kind="ceo_bootstrap",
        attempt=3,
    ):
        result = json.loads(
            handle_business_claude_agent_task(
                {
                    "business": "latexflow",
                    "workspace": "product/site",
                    "instruction": "Implement and preflight the landing.",
                    "guidance_skills": ["taste-frontend"],
                    "idempotency_key": "taste-timeout-published-partial",
                    "install": False,
                    "max_turns": 60,
                    "timeout_ms": 900_000,
                    "refresh_surface": True,
                }
            )
        )

    assert result["timed_out"] is True
    assert result["surface_refresh"]["publish"]["status"] == "published"
    assert result["review_required"] is True
    operations = store.commits[-1]["operations"]
    event = next(
        op for op in operations if op.get("event_type") == "bootstrap.human_review_required"
    )
    assert event["payload"]["timed_out"] is True
    assert event["payload"]["operator_task"]["attempt"] == 3


def test_claude_agent_task_bash_wrapper_uses_absolute_env_and_bash_paths():
    script = Path(__file__).resolve().parents[1] / "scripts" / "takyon-claude-agent-task.mjs"
    text = script.read_text(encoding="utf-8")
    assert 'const SANDBOX_PATH = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin";' in text
    assert "return `/usr/bin/env -i PATH=${SANDBOX_PATH} HOME=/tmp /bin/bash -lc ${JSON.stringify(script)}`;" in text


def test_claude_agent_task_script_passes_safe_host_path_to_child_env():
    script = Path(__file__).resolve().parents[1] / "scripts" / "takyon-claude-agent-task.mjs"
    text = script.read_text(encoding="utf-8")
    assert "function buildClaudeSessionEnv({" in text
    assert 'PATH: String(process.env.PATH || SANDBOX_PATH).trim() || SANDBOX_PATH,' in text
    assert 'HOME: String(process.env.HOME || "/tmp").trim() || "/tmp",' in text
    assert 'for (const key of ["LANG", "LC_ALL", "SHELL", "TERM", "TMPDIR", "TMP", "TEMP", "USER"]) {' in text


def test_claude_agent_task_script_has_no_model_fallback(tmp_path):
    node = shutil.which("node")
    if not node:
        pytest.skip("node is unavailable")
    script = Path(__file__).resolve().parents[1] / "scripts" / "takyon-claude-agent-task.mjs"
    proc = subprocess.run(
        [node, str(script)],
        input=json.dumps({"cwd": str(tmp_path), "root": str(tmp_path)}),
        text=True,
        capture_output=True,
        env={"PATH": str(Path(node).parent), "HOME": str(tmp_path), "ANTHROPIC_API_KEY": "cap_test"},
        timeout=10,
        check=False,
    )

    assert proc.returncode == 1
    assert "no fallback model is available" in json.loads(proc.stdout)["error"]


def test_claude_agent_task_script_rejects_mismatched_internal_model_alias(tmp_path):
    node = shutil.which("node")
    if not node:
        pytest.skip("node is unavailable")
    script = Path(__file__).resolve().parents[1] / "scripts" / "takyon-claude-agent-task.mjs"
    proc = subprocess.run(
        [node, str(script)],
        input=json.dumps(
            {
                "cwd": str(tmp_path),
                "root": str(tmp_path),
                "model": "deepseek-v4-pro",
            }
        ),
        text=True,
        capture_output=True,
        env={
            "PATH": str(Path(node).parent),
            "HOME": str(tmp_path),
            "ANTHROPIC_API_KEY": "cap_test",
            "TAKYON_CLAUDE_AGENT_MODEL": "deepseek-v4-pro",
            "ANTHROPIC_MODEL": "claude-sonnet-5",
        },
        timeout=10,
        check=False,
    )

    assert proc.returncode == 1
    assert "conflicts with pinned model" in json.loads(proc.stdout)["error"]


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


def test_claude_agent_task_script_passes_beta_disable_to_child_env():
    script = Path(__file__).resolve().parents[1] / "scripts" / "takyon-claude-agent-task.mjs"
    text = script.read_text(encoding="utf-8")
    assert "process.env.CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS" in text
    assert "CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS: disableExperimentalBetas" in text


def test_claude_agent_task_script_forces_local_terminal_work_inside_outer_docker_worker():
    script = Path(__file__).resolve().parents[1] / "scripts" / "takyon-claude-agent-task.mjs"
    text = script.read_text(encoding="utf-8")
    assert 'const inDockerWorker = String(process.env.TAKYON_CLAUDE_AGENT_IN_DOCKER || "").trim() === "1";' in text
    assert 'env.TERMINAL_ENV = "local";' in text
    assert "env.TERMINAL_CWD = cwd;" in text
    assert 'env.TERMINAL_DOCKER_MOUNT_CWD_TO_WORKSPACE = "0";' in text


def test_claude_agent_task_script_keeps_partial_messages_but_suppresses_private_reasoning():
    script = Path(__file__).resolve().parents[1] / "scripts" / "takyon-claude-agent-task.mjs"
    text = script.read_text(encoding="utf-8")
    assert "includePartialMessages: true" in text
    assert 'thinking: { type: "adaptive", display: "summarized" }' in text
    assert 'delta.type !== "thinking_delta"' in text
    assert "Thinking deltas are private model reasoning" in text
    assert 'entry_key: "claude-planning"' in text


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


def _patch_non_docker_product_site(monkeypatch, store, *, session_slug="latexflow"):
    """Shared monkeypatch set for a non-docker product/site worker run."""
    monkeypatch.setattr(takyon_core, "_store", lambda: store)
    monkeypatch.setattr(takyon_core, "_session_business_slug", lambda: session_slug)
    monkeypatch.setattr(takyon_core, "_require_api_access", lambda *args, **kwargs: None)
    monkeypatch.setattr(takyon_core, "_should_run_claude_agent_in_docker", lambda _workspace_rel: False)
    monkeypatch.setattr(takyon_core, "_workspace_needs_runtime_ui_contract", lambda _workspace_rel: False)
    monkeypatch.setattr(takyon_core, "_resolve_runtime_executable", lambda name: "/usr/bin/node" if name == "node" else None)
    monkeypatch.setattr(takyon_core, "_ensure_repo_node_dependencies", lambda packages: {"success": True})
    monkeypatch.setattr(takyon_core, "_record_claude_agent_runtime_event", lambda **_kwargs: None)
    monkeypatch.setattr(
        takyon_core,
        "_claude_agent_non_docker_worker_env",
        lambda business, operator_user_id: {"CLAUDE_AGENT_SDK_CLIENT_APP": "takyon-business-agent"},
    )


def test_claude_agent_task_timeout_preserves_partial_and_blocks(tmp_path, monkeypatch):
    """A wall-clock TimeoutExpired from the worker subprocess must NOT escape and discard the scratch:
    the partial edits are synced to canonical and the result is blocked+timed_out (rides the existing
    anti-re-delegation guard) instead of a cold failure."""
    monkeypatch.setenv("TAKYON_HOME", str(tmp_path))

    def fake_process(*, payload: dict[str, object], **kwargs):
        # The worker wrote a partial edit before wedging past the wall-clock ceiling.
        Path(str(payload["cwd"]), "index.html").write_text("<h1>partial</h1>\n", encoding="utf-8")
        raise subprocess.TimeoutExpired(cmd=["node"], timeout=1.0)

    _patch_non_docker_product_site(monkeypatch, _FakeStore(tmp_path))
    monkeypatch.setattr(takyon_core, "_reserve_operator_task_budget", lambda **_kwargs: {"reservation_key": "r1", "reserved_cents": 800})
    monkeypatch.setattr(
        takyon_core,
        "_finalize_operator_task_budget",
        lambda **_kwargs: {"reservation_key": "r1", "reserved_cents": 800, "status": "settled_estimate"},
    )
    monkeypatch.setattr(takyon_core, "_run_claude_agent_task_process", fake_process)

    result = json.loads(
        handle_business_claude_agent_task(
            {
                "business": "latexflow",
                "workspace": "product/site",
                "instruction": "Build the first honest product surface.",
                "idempotency_key": "workspace-timeout-preserve",
                "install": False,
                "refresh_surface": False,
            }
        )
    )

    assert result["success"] is False
    assert result["blocked"] is True
    assert result["timed_out"] is True
    assert result["partial_workspace_sync_status"] == "synced"
    assert "timed out" in (result["error"] or "")
    # The partial edit survives in the synced canonical workspace (not discarded).
    assert (tmp_path / "businesses" / "latexflow" / "product" / "site" / "index.html").exists()


def test_claude_agent_task_timeout_settles_estimate_not_double_charge(tmp_path, monkeypatch):
    """On timeout the operator budget is finalized exactly once at the reserved estimate (actual_cents
    is None — the worker spend is unmeasurable), never a fabricated double charge."""
    monkeypatch.setenv("TAKYON_HOME", str(tmp_path))
    finalize_calls: list[dict[str, object]] = []

    def fake_process(*, payload: dict[str, object], **kwargs):
        raise subprocess.TimeoutExpired(cmd=["node"], timeout=1.0)

    def fake_finalize(**kwargs):
        finalize_calls.append(dict(kwargs))
        return {"reservation_key": "r1", "reserved_cents": 800, "status": "settled_estimate"}

    # Global (non-business) session => the operator-budget rail is live (not the in-session no-op), and
    # no session-binding guard fires. The mocked reserve returns a real reservation so finalize runs.
    _patch_non_docker_product_site(monkeypatch, _FakeStore(tmp_path), session_slug=None)
    monkeypatch.setattr(takyon_core, "_reserve_operator_task_budget", lambda **_kwargs: {"reservation_key": "r1", "reserved_cents": 800})
    monkeypatch.setattr(takyon_core, "_finalize_operator_task_budget", fake_finalize)
    monkeypatch.setattr(takyon_core, "_run_claude_agent_task_process", fake_process)

    result = json.loads(
        handle_business_claude_agent_task(
            {
                "business": "latexflow",
                "workspace": "product/site",
                "instruction": "Build the first honest product surface.",
                "idempotency_key": "workspace-timeout-billing",
                "install": False,
                "refresh_surface": False,
            }
        )
    )

    assert result["success"] is False
    assert result["timed_out"] is True
    assert len(finalize_calls) == 1
    assert finalize_calls[-1]["consume_reserved"] is True
    assert finalize_calls[-1]["actual_cents"] is None


def test_claude_agent_task_timeout_runs_publish_gates_and_blocks_incomplete_partial(tmp_path, monkeypatch):
    """A timed-out partial runs the canonical refresh/completeness gate, but an incomplete source
    remains blocked instead of being promoted merely because the worker wrote files."""
    monkeypatch.setenv("TAKYON_HOME", str(tmp_path))

    def fake_process(*, payload: dict[str, object], **kwargs):
        Path(str(payload["cwd"]), "index.html").write_text("<h1>partial</h1>\n", encoding="utf-8")
        raise subprocess.TimeoutExpired(cmd=["node"], timeout=1.0)

    refresh_calls: list[dict[str, object]] = []

    def blocked_refresh(**kwargs):
        refresh_calls.append(dict(kwargs))
        return {
            "status": "failed",
            "kind": "vite",
            "source_path": "product/site",
            "receipt_path": "metrics/receipts/product-surface/timeout-partial.json",
            "inventory": {},
            "error": "requested SaaS workflow is incomplete",
            "blocker": "requested SaaS workflow is incomplete",
            "publish": {
                "status": "blocked",
                "blocker": "requested SaaS workflow is incomplete",
            },
        }

    _patch_non_docker_product_site(monkeypatch, _FakeStore(tmp_path))
    monkeypatch.setattr(takyon_core, "_reserve_operator_task_budget", lambda **_kwargs: {"reservation_key": "r1", "reserved_cents": 800})
    monkeypatch.setattr(
        takyon_core,
        "_finalize_operator_task_budget",
        lambda **_kwargs: {"reservation_key": "r1", "reserved_cents": 800, "status": "settled_estimate"},
    )
    monkeypatch.setattr(takyon_core, "_finalize_product_surface_refresh", blocked_refresh)
    monkeypatch.setattr(takyon_core, "_run_claude_agent_task_process", fake_process)

    result = json.loads(
        handle_business_claude_agent_task(
            {
                "business": "latexflow",
                "workspace": "product/site",
                "instruction": "Build the first honest product surface.",
                "idempotency_key": "workspace-timeout-no-publish",
                "install": False,
                "refresh_surface": True,
            }
        )
    )

    assert result["success"] is False
    assert result["timed_out"] is True
    assert refresh_calls
    assert result["surface_refresh"]["publish"]["status"] == "blocked"
    assert "incomplete" in result["surface_refresh"]["blocker"]
