"""Tests for the Takyon CEO operator plugin."""

from __future__ import annotations

import json
import sys
import types
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from plugins.takyon import core as takyon_core
from plugins.takyon.core import (
    TAKYON_TOOL_DEFINITIONS,
    TakyonError,
    TakyonStore,
    _API_ENV_ALIASES,
    _canonicalize_business_product_links,
    _product_publish_target,
    _scan_for_pretend_product_state,
    _verify_product_surface_path,
    handle_business_check_runtime_capabilities,
    handle_business_delete_business,
    handle_business_generate_creative_asset,
    handle_business_list_businesses,
    handle_business_publish_outreach,
    handle_business_registry,
    handle_business_request_app_magic_link,
    handle_business_claude_agent_task,
    handle_business_set_work_focus,
    handle_business_upsert_business,
    handle_business_verify_product_surface,
)
from plugins.takyon.registry import TAKYON_CATEGORIES, TAKYON_PRIORITY_BANDS, TAKYON_REGISTRY


class _FakePluginContext:
    def __init__(self):
        self.tools = []
        self.skills = []
        self.commands = []
        self.slash_commands = {}
        self.injected = []

    def register_tool(self, **kwargs):
        self.tools.append(kwargs["name"])

    def register_skill(self, **kwargs):
        self.skills.append(kwargs["name"])

    def register_cli_command(self, **kwargs):
        self.commands.append(kwargs["name"])

    def register_command(self, name, handler, **kwargs):
        self.slash_commands[name] = {"handler": handler, **kwargs}

    def inject_message(self, content, role="user"):
        self.injected.append((role, content))
        return True


def _commit(store: TakyonStore, scope: str, operations: list[dict], key: str):
    return store.commit(scope=scope, operations=operations, idempotency_key=key, reason="test", actor="test")


def test_plugin_registers_skill_pack():
    import plugins.takyon as takyon

    ctx = _FakePluginContext()
    takyon.register(ctx)
    assert sorted(ctx.tools) == sorted(tool["name"] for tool in TAKYON_TOOL_DEFINITIONS)
    assert set(ctx.skills) == {
        "build-product",
        "business-pulse",
        "ceo",
        "app-runtime",
        "claude-agent-sdk",
        "market-research",
    }
    assert ctx.commands == ["takyon"]
    assert set(ctx.slash_commands) == {"takyon"}


def test_registry_covers_tools_and_skills():
    registered_tools = {tool["name"] for tool in TAKYON_TOOL_DEFINITIONS}
    registry_tools = {tool["name"] for tool in TAKYON_REGISTRY["tools"]}
    assert registry_tools == registered_tools

    skills_root = Path(__file__).resolve().parents[2] / "plugins" / "takyon" / "skills"
    skill_dirs = {path.parent.name for path in skills_root.glob("*/SKILL.md")}
    registry_skills = {skill["name"] for skill in TAKYON_REGISTRY["skills"]}
    assert registry_skills == skill_dirs

    for collection in (TAKYON_REGISTRY["tools"], TAKYON_REGISTRY["skills"]):
        for item in collection:
            assert item["category"] in TAKYON_CATEGORIES
            assert item["priority_bands"]
            assert set(item["priority_bands"]).issubset(TAKYON_PRIORITY_BANDS)


def test_bootstrap_prompt_requires_phase_one_outreach_batch():
    from plugins.takyon.cli import _business_bootstrap_instruction

    prompt = _business_bootstrap_instruction("demo", "find users", "test")

    assert "distribution/phase-1-outreach/" in prompt
    assert "3 evidence-backed lanes" in prompt
    assert "6 total" in prompt
    assert "business_publish_outreach" in prompt
    assert "not a forever recurring funnel" in prompt


def test_ceo_wake_prompt_includes_outreach_lifecycle(tmp_path):
    prompt = TakyonStore(tmp_path)._ceo_cron_prompt("demo")

    assert "Advance the outreach lifecycle" in prompt
    assert "distribution/phase-1-outreach/" in prompt
    assert "if Phase 1 is incomplete" in prompt
    assert "if complete but unreviewed" in prompt


def test_registry_keeps_trimmed_core_skill_metadata():
    skills = {skill["name"]: skill for skill in TAKYON_REGISTRY["skills"]}

    assert set(skills) == {
        "app-runtime",
        "build-product",
        "business-pulse",
        "ceo",
        "claude-agent-sdk",
        "market-research",
    }

    assert "product/site surface" in skills["build-product"]["use_when"]
    assert "research first" in skills["market-research"]["use_when"]


def test_registry_tool_filters_by_category_and_priority():
    result = json.loads(
        handle_business_registry(
            {"kind": "tools", "category": "queue", "priority_band": "p2_growth"}
        )
    )
    assert result["success"] is True
    assert [tool["name"] for tool in result["tools"]] == ["business_enqueue_job"]
    assert "skills" not in result


def test_runtime_capability_check_reports_requested_commands():
    result = json.loads(
        handle_business_check_runtime_capabilities(
            {"capabilities": ["python", "definitely_missing_takyon_test_binary"]}
        )
    )

    assert result["success"] is True
    assert result["capabilities"]["python"]["available"] is True
    assert result["capabilities"]["definitely_missing_takyon_test_binary"]["available"] is False
    assert "definitely_missing_takyon_test_binary" in result["missing_capabilities"]


def test_product_publish_target_defaults_to_business_subdomain():
    assert _product_publish_target("latexflow") == "https://latexflow.fourmanifold.com/"


def test_takyon_slash_runs_local_registry_command():
    import plugins.takyon as takyon

    ctx = _FakePluginContext()
    takyon.register(ctx)
    result = ctx.slash_commands["takyon"]["handler"]("registry tools queue p2_growth")
    assert "business_enqueue_job" in result
    assert '"skills"' not in result


def test_takyon_slash_can_proxy_installed_skills(monkeypatch):
    import agent.skill_commands as skill_commands
    import plugins.takyon as takyon

    monkeypatch.setattr(
        skill_commands,
        "get_skill_commands",
        lambda: {"/demo-skill": {"name": "Demo Skill", "skill_dir": "/tmp/demo"}},
    )
    monkeypatch.setattr(
        skill_commands,
        "build_skill_invocation_message",
        lambda cmd_key, user_instruction="", **_: f"skill={cmd_key}; instruction={user_instruction}",
    )

    ctx = _FakePluginContext()
    takyon.register(ctx)
    result = ctx.slash_commands["takyon"]["handler"]("demo-skill do useful work")

    assert result == "Queued Takyon skill /demo-skill."
    assert ctx.injected == [("user", "skill=/demo-skill; instruction=do useful work")]


def test_takyon_slash_can_proxy_takyon_plugin_skills(monkeypatch):
    import plugins.takyon as takyon

    fake_skills_tool = types.ModuleType("tools.skills_tool")
    fake_skills_tool.skill_view = lambda name: json.dumps(
        {
            "success": True,
            "name": name,
            "content": "# Market Research\n\nUse this skill.",
        }
    )
    monkeypatch.setitem(sys.modules, "tools.skills_tool", fake_skills_tool)

    ctx = _FakePluginContext()
    takyon.register(ctx)
    result = ctx.slash_commands["takyon"]["handler"]("market-research find channels")

    assert result == "Queued Takyon skill takyon:market-research."
    assert ctx.injected[0][0] == "user"
    assert 'name="takyon:market-research"' in ctx.injected[0][1]
    assert "find channels" in ctx.injected[0][1]


def test_business_memory_is_business_scoped(tmp_path):
    store = TakyonStore(tmp_path)
    _commit(
        store,
        "business:latexflow",
        [{"action": "business.upsert", "business": "latexflow", "name": "Latexflow"}],
        "init-latexflow",
    )
    _commit(
        store,
        "business:latexflow",
        [{"action": "memory.write", "path": "pricing.md", "content": "# Pricing\n"}],
        "write-pricing",
    )

    result = store.read(scope="business:latexflow", query="read_file", path="brain/pricing.md")
    assert result["content"] == "# Pricing\n"

    with pytest.raises(TakyonError):
        store.read(scope="business:other", query="read_file", path="brain/pricing.md")


def test_business_pulse_is_read_only_baseline(tmp_path):
    store = TakyonStore(tmp_path)
    _commit(
        store,
        "business:latexflow",
        [{"action": "business.upsert", "business": "latexflow", "name": "Latexflow", "goal": "Build PDFs"}],
        "init-latexflow",
    )

    pulse = store.calculate_pulse("latexflow")

    assert pulse["success"] is True
    assert pulse["is_first_pulse"] is True
    assert pulse["summary"]["users"] == 0
    assert pulse["deltas_from_previous_pulse"]["status"] == "baseline"
    with store._connect() as conn:
        recorded = conn.execute(
            "SELECT COUNT(*) AS count FROM events WHERE business_slug = 'latexflow' AND event_type = 'business.pulse.snapshot'"
        ).fetchone()
    assert recorded["count"] == 0


def test_pulse_file_write_records_snapshot_event(tmp_path):
    store = TakyonStore(tmp_path)
    _commit(
        store,
        "business:latexflow",
        [{"action": "business.upsert", "business": "latexflow", "name": "Latexflow", "goal": "Build PDFs"}],
        "init-latexflow",
    )

    _commit(
        store,
        "business:latexflow",
        [{"action": "artifact.write", "business": "latexflow", "path": "brain/pulse.md", "content": "# Pulse\n\nBaseline.\n"}],
        "write-pulse",
    )

    with store._connect() as conn:
        recorded = conn.execute(
            "SELECT COUNT(*) AS count FROM events WHERE business_slug = 'latexflow' AND event_type = 'business.pulse.snapshot'"
        ).fetchone()
    assert recorded["count"] == 1


def test_app_plan_normalizes_interval_and_records_validation_warnings(tmp_path):
    store = TakyonStore(tmp_path)
    _commit(
        store,
        "business:latexflow",
        [{"action": "business.upsert", "business": "latexflow", "name": "Latexflow", "budget": {"amount": 25}}],
        "init",
    )

    _commit(
        store,
        "business:latexflow",
        [
            {
                "action": "app.plan.upsert",
                "business": "latexflow",
                "plan_key": "studio",
                "tier": "pro",
                "price_cents": 1200,
                "billing_interval": "monthly",
                "included_action_quota": 10,
                "metadata": {"core_actions_per_month": "unlimited"},
            }
        ],
        "plan",
    )

    app = store.read(scope="business:latexflow", query="summary", include=["app"])["app"]
    plan = app["plans"][0]
    assert plan["billing_interval"] == "month"
    assert plan["metadata"]["takyon_plan_validation"]["status"] == "warning"


def test_active_surface_requires_product_verification_receipt(tmp_path, monkeypatch):
    monkeypatch.setenv("TAKYON_HOME", str(tmp_path))
    monkeypatch.setenv("TAKYON_PRODUCT_SITE_ROOT", str(tmp_path / "published-sites"))
    store = TakyonStore(tmp_path)
    _commit(
        store,
        "business:latexflow",
        [{"action": "business.upsert", "business": "latexflow", "name": "Latexflow", "budget": {"amount": 25}}],
        "init",
    )

    _commit(
        store,
        "business:latexflow",
        [{"action": "app.surface.upsert", "business": "latexflow", "status": "active", "source_path": "product/site", "routes": ["/"]}],
        "surface-before-source",
    )
    app = store.read(scope="business:latexflow", query="summary", include=["app"])["app"]
    assert app["surface_contract"]["status"] == "unverified"

    site = tmp_path / "businesses" / "latexflow" / "product" / "site"
    site.mkdir(parents=True)
    (site / "index.html").write_text("<h1>Latexflow</h1>\n", encoding="utf-8")
    (site / "index.html").chmod(0o600)
    verification = json.loads(
        handle_business_verify_product_surface(
            {
                "business": "latexflow",
                "source_path": "product/site",
                "install": False,
                "idempotency_key": "verify-static-site",
            }
        )
    )

    assert verification["success"] is True
    assert verification["verification"]["status"] == "passed"
    assert verification["verification"]["inventory"]["status"] == "collected"
    assert verification["verification"]["inventory"]["routes"] == ["/"]
    app = store.read(scope="business:latexflow", query="summary", include=["app"])["app"]
    assert app["surface_contract"]["status"] == "active"
    assert app["surface_contract"]["publish_status"] == "published"
    assert app["surface_contract"]["public_url"] == "https://latexflow.fourmanifold.com/"
    assert app["product_inventory"]["routes"] == ["/"]
    assert app["product_surface"]["local_continuable_work"] == []
    assert (tmp_path / "published-sites" / "latexflow" / "index.html").exists()
    assert app["surface_contract"]["metadata"]["takyon_surface_validation"]["status"] == "passed"
    assert app["surface_contract"]["routes"] == ["/"]
    pulse = store.calculate_pulse("latexflow")
    assert pulse["current_state"]["product_surface"]["inventory_status"] == "collected"
    assert pulse["summary"]["local_continuable_product_work"] == 0


def test_app_like_surface_cannot_pass_as_landing_page_only(tmp_path, monkeypatch):
    monkeypatch.setenv("TAKYON_HOME", str(tmp_path))
    monkeypatch.setenv("TAKYON_PRODUCT_SITE_ROOT", str(tmp_path / "published-sites"))
    store = TakyonStore(tmp_path)
    _commit(
        store,
        "business:briefpilot",
        [{"action": "business.upsert", "business": "briefpilot", "name": "BriefPilot"}],
        "init-app-like-landing",
    )
    _commit(
        store,
        "business:briefpilot",
        [
            {
                "action": "app.surface.upsert",
                "business": "briefpilot",
                "status": "draft",
                "source_path": "product/site",
                "routes": [
                    {"path": "/", "name": "Homepage"},
                    {"path": "/app", "name": "App", "description": "AI brief builder workflow"},
                ],
                "notes": "AI brief builder app.",
            }
        ],
        "surface-app-like-landing",
    )
    site = tmp_path / "businesses" / "briefpilot" / "product" / "site"
    site.mkdir(parents=True)
    (site / "index.html").write_text("<h1>BriefPilot</h1><p>Join the waitlist.</p>\n", encoding="utf-8")

    verification = json.loads(
        handle_business_verify_product_surface(
            {
                "business": "briefpilot",
                "source_path": "product/site",
                "install": False,
                "idempotency_key": "verify-app-like-landing",
            }
        )
    )["verification"]

    assert verification["status"] == "failed"
    assert verification["done_gate_status"] == "blocked"
    assert "app subroute" in verification["error"]
    assert verification["publish"]["status"] == "blocked"


def test_static_spa_app_surface_with_shared_runtime_routes_passes(tmp_path, monkeypatch):
    monkeypatch.setenv("TAKYON_HOME", str(tmp_path))
    monkeypatch.setenv("TAKYON_PRODUCT_SITE_ROOT", str(tmp_path / "published-sites"))
    store = TakyonStore(tmp_path)
    _commit(
        store,
        "business:briefpilot",
        [{"action": "business.upsert", "business": "briefpilot", "name": "BriefPilot"}],
        "init-static-spa-app",
    )
    _commit(
        store,
        "business:briefpilot",
        [
            {
                "action": "app.surface.upsert",
                "business": "briefpilot",
                "status": "draft",
                "source_path": "product/site",
                "runtime_api_base": "/api/takyon/apps/briefpilot",
                "routes": [
                    {"path": "/", "name": "Homepage"},
                    {"path": "/app", "name": "App", "description": "AI brief builder workflow"},
                ],
                "notes": "AI brief builder app.",
            }
        ],
        "surface-static-spa-app",
    )
    site = tmp_path / "businesses" / "briefpilot" / "product" / "site"
    site.mkdir(parents=True)
    (site / "index.html").write_text(
        """
        <h1>BriefPilot</h1>
        <a href="/app">Open app</a>
        <form id="signin"><input name="email" type="email"></form>
        <form id="workspace"><textarea name="brief"></textarea><button>Generate</button></form>
        <script>
        fetch('/api/takyon/apps/briefpilot/session');
        document.getElementById('signin').addEventListener('submit', function (event) {
          event.preventDefault();
          fetch('/api/takyon/apps/briefpilot/auth/request', { method: 'POST' });
        });
        document.getElementById('workspace').addEventListener('submit', function (event) {
          event.preventDefault();
          fetch('/api/takyon/apps/briefpilot/generate', { method: 'POST' });
        });
        </script>
        """,
        encoding="utf-8",
    )

    verification = json.loads(
        handle_business_verify_product_surface(
            {
                "business": "briefpilot",
                "source_path": "product/site",
                "install": False,
                "idempotency_key": "verify-static-spa-app",
            }
        )
    )["verification"]

    assert verification["status"] == "passed"
    assert verification["done_gate_status"] == "passed"
    inventory = verification["inventory"]
    assert inventory["routes"] == ["/"]
    assert inventory["declared_routes"] == ["/app"]
    assert {"auth", "generate", "session"}.issubset(set(inventory["runtime_integrations"]))
    assert {"form", "input", "runtime_fetch"}.issubset(set(inventory["workflow_markers"]))
    assert (tmp_path / "published-sites" / "briefpilot" / "index.html").exists()


def test_legacy_shared_renderer_policy_requires_real_product_source_files(tmp_path, monkeypatch):
    monkeypatch.setenv("TAKYON_HOME", str(tmp_path))
    monkeypatch.setenv("TAKYON_PRODUCT_LOCAL_BASE_URL", "http://127.0.0.1:9127/site")
    store = TakyonStore(tmp_path)
    _commit(
        store,
        "business:latexflow",
        [{"action": "business.upsert", "business": "latexflow", "name": "Latexflow", "goal": "Overleaf competitor"}],
        "init-legacy-shared-renderer",
    )
    _commit(
        store,
        "business:latexflow",
        [
            {
                "action": "app.surface.upsert",
                "business": "latexflow",
                "status": "draft",
                "publish_policy": "shared_renderer",
                "routes": ["/"],
                "metadata": {
                    "headline": "Write LaTeX without tickets",
                    "shared_product_blocks": [
                        {
                            "type": "waitlist",
                            "label": "Early access",
                            "status": "available",
                            "description": "Collect workflow feedback.",
                        }
                    ],
                },
            }
        ],
        "surface-legacy-shared-renderer",
    )

    verification = json.loads(
        handle_business_verify_product_surface(
            {
                "business": "latexflow",
                "install": False,
                "idempotency_key": "verify-legacy-shared-renderer",
            }
        )
    )

    assert verification["success"] is True
    receipt = verification["verification"]
    assert receipt["status"] == "missing"
    assert receipt["effective_publish_policy"] == "publish_after_verify"
    assert receipt["requested_publish_policy"] == "shared_renderer"
    assert receipt["done_gate_status"] == "blocked"
    assert receipt["publish"]["status"] == "blocked"
    assert receipt["publish"]["public_url"] == ""
    assert "source path does not exist" in receipt["blocker"].lower()
    app = store.read(scope="business:latexflow", query="summary", include=["app"])["app"]
    assert app["surface_contract"]["status"] == "draft"
    assert app["surface_contract"]["publish_policy"] == "shared_renderer"
    assert app["surface_contract"]["publish_status"] == "not_published"
    assert app["surface_contract"]["public_url"] in {"", None}
    assert app["product_surface"]["has_source_files"] is False
    assert app["product_surface"]["local_continuable_work"]
    pulse = store.calculate_pulse("latexflow")
    assert pulse["summary"]["local_continuable_product_work"] > 0


def test_static_product_publish_writes_caddy_route_when_configured(tmp_path, monkeypatch):
    monkeypatch.setenv("TAKYON_HOME", str(tmp_path))
    monkeypatch.setenv("TAKYON_PRODUCT_SITE_ROOT", str(tmp_path / "published-sites"))
    monkeypatch.setenv("TAKYON_PRODUCT_CADDYFILE", str(tmp_path / "Caddyfile"))
    monkeypatch.setenv("TAKYON_PRODUCT_DEPLOY_DRY_RUN", "1")
    (tmp_path / "Caddyfile").write_text("app.fourmanifold.com {\n    reverse_proxy 127.0.0.1:9119\n}\n", encoding="utf-8")
    store = TakyonStore(tmp_path)
    _commit(
        store,
        "business:latexflow",
        [{"action": "business.upsert", "business": "latexflow", "name": "Latexflow"}],
        "init",
    )
    site = tmp_path / "businesses" / "latexflow" / "product" / "site"
    site.mkdir(parents=True)
    (site / "index.html").write_text("<h1>Latexflow</h1>\n", encoding="utf-8")

    verification = json.loads(
        handle_business_verify_product_surface(
            {
                "business": "latexflow",
                "source_path": "product/site",
                "install": False,
                "idempotency_key": "verify-static-site-caddy",
            }
        )
    )["verification"]

    caddyfile = (tmp_path / "Caddyfile").read_text(encoding="utf-8")
    assert verification["done_gate_status"] == "passed"
    assert verification["publish"]["status"] == "published"
    assert verification["publish"]["caddyfile"] == str(tmp_path / "Caddyfile")
    assert "latexflow.fourmanifold.com" in caddyfile
    assert "@takyon_app_runtime path /api/takyon/apps/* /api/generated-apps/* /api/webhooks/stripe" in caddyfile
    assert "reverse_proxy 127.0.0.1:9119" in caddyfile
    assert f"root * {tmp_path / 'published-sites' / 'latexflow'}" in caddyfile
    assert "try_files {path} {path}/ /index.html" in caddyfile
    assert "file_server" in caddyfile
    assert ((tmp_path / "published-sites" / "latexflow" / "index.html").stat().st_mode & 0o777) == 0o644


def test_product_verification_detects_nested_workspace_prefix(tmp_path, monkeypatch):
    monkeypatch.setenv("TAKYON_HOME", str(tmp_path))
    store = TakyonStore(tmp_path)
    _commit(
        store,
        "business:latexflow",
        [{"action": "business.upsert", "business": "latexflow", "name": "Latexflow"}],
        "init",
    )
    nested = tmp_path / "businesses" / "latexflow" / "product" / "site" / "product" / "site"
    nested.mkdir(parents=True)
    (nested / "index.html").write_text("<h1>Nested</h1>\n", encoding="utf-8")

    verification = json.loads(
        handle_business_verify_product_surface(
            {
                "business": "latexflow",
                "source_path": "product/site",
                "install": False,
                "idempotency_key": "verify-nested-site",
            }
        )
    )

    assert verification["success"] is True
    assert verification["verification"]["status"] == "failed"
    assert "duplicate workspace prefix" in verification["verification"]["error"]


def test_claude_agent_task_injects_workspace_relative_contract(tmp_path, monkeypatch):
    monkeypatch.setenv("TAKYON_HOME", str(tmp_path))
    store = TakyonStore(tmp_path)
    _commit(
        store,
        "business:latexflow",
        [{"action": "business.upsert", "business": "latexflow", "name": "Latexflow", "budget": {"amount": 25}}],
        "init",
    )

    captured: dict[str, object] = {}

    def fake_run(command, *, input=None, **kwargs):
        if len(command) > 1 and str(command[1]).endswith("takyon-claude-agent-task.mjs"):
            payload = json.loads(input or "{}")
            captured["payload"] = payload
            Path(payload["cwd"], "index.html").write_text("<h1>Latexflow</h1>\n", encoding="utf-8")
            return types.SimpleNamespace(returncode=0, stdout=json.dumps({"success": True, "summary": "ok"}), stderr="")
        return types.SimpleNamespace(returncode=0, stdout="v99.0.0\n", stderr="")

    monkeypatch.setattr(takyon_core, "_require_api_access", lambda *args, **kwargs: None)
    monkeypatch.setattr(takyon_core, "_resolve_runtime_executable", lambda name: "/usr/bin/node" if name == "node" else None)
    monkeypatch.setattr(takyon_core, "_ensure_repo_node_dependencies", lambda packages: {"success": True})
    monkeypatch.setattr(takyon_core.subprocess, "run", fake_run)

    result = json.loads(
        handle_business_claude_agent_task(
            {
                "business": "latexflow",
                "workspace": "product/site",
                "instruction": "Build the product surface under product/site.",
                "idempotency_key": "workspace-contract",
                "install": False,
            }
        )
    )

    instruction = captured["payload"]["instruction"]
    assert result["success"] is True
    assert "current working directory is already the requested business workspace: product/site" in instruction
    assert "not `product/site/index.html`" in instruction
    assert (tmp_path / "businesses" / "latexflow" / "product" / "site" / "index.html").exists()
    assert not (tmp_path / "businesses" / "latexflow" / "product" / "site" / "product" / "site").exists()


def test_product_verification_records_publish_blocker_when_hosting_root_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("TAKYON_HOME", str(tmp_path))
    monkeypatch.setenv("TAKYON_PRODUCT_SITE_ROOT", "")
    monkeypatch.setenv("PUBLIC_COMPANY_SITE_ROOT", "")
    monkeypatch.setenv("TAKYON_STATIC_SITE_ROOT", "")
    store = TakyonStore(tmp_path)
    _commit(
        store,
        "business:latexflow",
        [{"action": "business.upsert", "business": "latexflow", "name": "Latexflow"}],
        "init",
    )
    site = tmp_path / "businesses" / "latexflow" / "product" / "site"
    site.mkdir(parents=True)
    (site / "index.html").write_text("<h1>Latexflow</h1>\n", encoding="utf-8")

    verification = json.loads(
        handle_business_verify_product_surface(
            {
                "business": "latexflow",
                "source_path": "product/site",
                "install": False,
                "idempotency_key": "verify-static-site-no-host-root",
            }
        )
    )

    assert verification["success"] is True
    assert verification["verification"]["status"] == "passed"
    assert verification["verification"]["done_gate_status"] == "blocked"
    assert "TAKYON_PRODUCT_SITE_ROOT" in verification["verification"]["blocker"]
    app = store.read(scope="business:latexflow", query="summary", include=["app"])["app"]
    assert app["surface_contract"]["status"] == "publish_blocked"
    assert app["surface_contract"]["publish_status"] == "blocked"


def test_product_inventory_is_nonfatal_for_unreadable_source_file(tmp_path, monkeypatch):
    monkeypatch.setenv("TAKYON_HOME", str(tmp_path))
    monkeypatch.setenv("TAKYON_PRODUCT_SITE_ROOT", str(tmp_path / "published-sites"))
    business_root = tmp_path / "businesses" / "latexflow"
    site = business_root / "product" / "site"
    site.mkdir(parents=True)
    (site / "index.html").write_text("<h1>Latexflow</h1>\n", encoding="utf-8")
    (site / "bad.js").write_bytes(b"\xff\xfe\x00")

    verification = _verify_product_surface_path(business_root, "product/site", install=False)

    assert verification["status"] == "passed"
    assert verification["inventory"]["status"] == "collected"
    assert verification["inventory"]["files_skipped"] >= 1


def test_next_product_publish_uses_service_rail_without_static_index(tmp_path, monkeypatch):
    business_root = tmp_path / "businesses" / "latexflow"
    site = business_root / "product" / "site"
    site.mkdir(parents=True)
    (site / ".next").mkdir()
    (site / ".next" / "BUILD_ID").write_text("build-1\n", encoding="utf-8")
    (site / "out").mkdir()
    (site / "out" / "index.html").write_text("<h1>Static export also exists</h1>\n", encoding="utf-8")
    (site / "package.json").write_text(
        json.dumps(
            {
                "name": "latexflow-site",
                "private": True,
                "scripts": {"build": "next build", "start": "next start"},
                "dependencies": {"next": "^15.0.0", "react": "^19.0.0", "react-dom": "^19.0.0"},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("TAKYON_PRODUCT_DEPLOY_DRY_RUN", "1")
    monkeypatch.setenv("TAKYON_PRODUCT_SKIP_PUBLIC_PROBE", "1")
    monkeypatch.setenv("TAKYON_PRODUCT_SYSTEMD_DIR", str(tmp_path / "systemd"))
    monkeypatch.setenv("TAKYON_PRODUCT_CADDYFILE", str(tmp_path / "Caddyfile"))
    monkeypatch.setattr(
        takyon_core,
        "_javascript_package_manager_command",
        lambda name: {"available": True, "name": "npm", "command": ["/usr/bin/npm"], "source": "test"},
    )

    result = takyon_core._publish_product_surface_path(
        business_root=business_root,
        slug="latexflow",
        source_path="product/site",
        publish_target="https://latexflow.fourmanifold.com/",
    )

    assert result["status"] == "published"
    assert result["deploy_kind"] == "next_systemd_caddy"
    assert result["public_url"] == "https://latexflow.fourmanifold.com/"
    assert result["publish_source_path"] == "product/site"
    service = tmp_path / "systemd" / "takyon-product-latexflow.service"
    assert "npm run start -- -H 127.0.0.1 -p" in service.read_text(encoding="utf-8")
    assert "latexflow.fourmanifold.com" in (tmp_path / "Caddyfile").read_text(encoding="utf-8")


def test_static_site_with_noop_package_manifest_does_not_require_npm(tmp_path, monkeypatch):
    business_root = tmp_path / "businesses" / "inboxpilot"
    site = business_root / "product" / "site"
    site.mkdir(parents=True)
    (site / "index.html").write_text("<h1>InboxPilot</h1>\n<script src=\"app.js\"></script>\n", encoding="utf-8")
    (site / "app.js").write_text("console.log('ready')\n", encoding="utf-8")
    (site / "style.css").write_text("body { font-family: sans-serif; }\n", encoding="utf-8")
    (site / "package.json").write_text(
        json.dumps(
            {
                "name": "inboxpilot-site",
                "private": True,
                "scripts": {"build": "echo 'Static site - no build step required.'"},
                "devDependencies": {"serve": "^14.2.0"},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr("plugins.takyon.core._resolve_runtime_executable", lambda name: None)

    verification = _verify_product_surface_path(business_root, "product/site", install=True)

    assert verification["status"] == "passed"
    assert verification["kind"] == "static_source_present"
    assert verification["checks"] == []


def test_path_escape_is_rejected(tmp_path):
    store = TakyonStore(tmp_path)
    _commit(
        store,
        "business:latexflow",
        [{"action": "business.upsert", "business": "latexflow", "name": "Latexflow"}],
        "init",
    )

    with pytest.raises(TakyonError):
        _commit(
            store,
            "business:latexflow",
            [{"action": "artifact.write", "path": "../outside.md", "content": "no"}],
            "escape",
        )


def test_idempotency_replays_same_result_and_rejects_drift(tmp_path):
    store = TakyonStore(tmp_path)
    ops = [{"action": "business.upsert", "business": "latexflow", "name": "Latexflow"}]

    first = _commit(store, "business:latexflow", ops, "same-key")
    second = _commit(store, "business:latexflow", ops, "same-key")
    assert first == second

    with pytest.raises(TakyonError):
        _commit(
            store,
            "business:latexflow",
            [{"action": "business.upsert", "business": "latexflow", "name": "Different"}],
            "same-key",
        )


def test_kill_switch_blocks_child_writes(tmp_path):
    store = TakyonStore(tmp_path)
    _commit(
        store,
        "business:latexflow",
        [{"action": "business.upsert", "business": "latexflow", "name": "Latexflow"}],
        "init",
    )
    _commit(
        store,
        "business:latexflow",
        [{"action": "control.set", "scope": "business:latexflow", "state": "killed", "reason": "stop"}],
        "kill",
    )

    with pytest.raises(TakyonError, match="killed"):
        _commit(
            store,
            "business:latexflow/workspace:distribution/finals",
            [{"action": "workspace.upsert", "path": "distribution/finals"}],
            "blocked",
        )


def test_budget_cap_is_enforced(tmp_path):
    store = TakyonStore(tmp_path)
    _commit(
        store,
        "business:latexflow",
        [
            {
                "action": "business.upsert",
                "business": "latexflow",
                "name": "Latexflow",
                "budget": {"amount": 10, "currency": "USD"},
            }
        ],
        "init",
    )
    _commit(
        store,
        "business:latexflow",
        [{"action": "ledger.allocate", "amount": 7, "purpose": "test"}],
        "alloc-7",
    )

    with pytest.raises(TakyonError, match="exceed budget"):
        _commit(
            store,
            "business:latexflow",
            [{"action": "ledger.allocate", "amount": 4, "purpose": "too much"}],
            "alloc-4",
        )


def test_required_env_must_exist(tmp_path, monkeypatch):
    monkeypatch.delenv("TAKYON_TEST_MISSING_API_KEY", raising=False)
    store = TakyonStore(tmp_path)
    _commit(
        store,
        "business:latexflow",
        [{"action": "business.upsert", "business": "latexflow", "name": "Latexflow"}],
        "init",
    )

    with pytest.raises(TakyonError, match="missing API/env"):
        _commit(
            store,
            "business:latexflow",
            [
                {
                    "action": "job.enqueue",
                    "kind": "external-call",
                    "requires_env": ["TAKYON_TEST_MISSING_API_KEY"],
                }
            ],
            "missing-api",
        )


def test_product_deploy_job_is_vercel_gated_in_test_mode(tmp_path, monkeypatch):
    monkeypatch.setitem(_API_ENV_ALIASES, "vercel", ("TAKYON_TEST_VERCEL_TOKEN",))
    monkeypatch.delenv("TAKYON_TEST_VERCEL_TOKEN", raising=False)
    store = TakyonStore(tmp_path)
    _commit(
        store,
        "business:longer",
        [{"action": "business.upsert", "business": "longer", "name": "Longer", "mode": "test"}],
        "init",
    )

    result = _commit(
        store,
        "business:longer",
        [{"action": "job.enqueue", "kind": "product.deploy", "payload": {"source_path": "product/site"}}],
        "deploy",
    )["results"][0]

    assert result["external_side_effects"] == "suppressed"
    assert result["missing_credentials_suppressed"] == ["TAKYON_TEST_VERCEL_TOKEN"]
    job = store.read(scope="business:longer", query="summary")["jobs"][0]
    assert job["payload"]["missing_credentials_suppressed"] == ["TAKYON_TEST_VERCEL_TOKEN"]


def test_business_work_focus_persists_and_blocks_cross_lane_writes(tmp_path):
    store = TakyonStore(tmp_path)
    _commit(
        store,
        "business:latexflow",
        [{"action": "business.upsert", "business": "latexflow", "name": "Latexflow"}],
        "init-focus",
    )

    result = _commit(
        store,
        "business:latexflow",
        [{"action": "business.focus.set", "business": "latexflow", "work_focus": "marketing"}],
        "focus-marketing",
    )

    assert result["results"][0]["work_focus"] == "marketing"
    assert store.read(scope="business:latexflow", query="summary")["business"]["work_focus"] == "marketing"

    with pytest.raises(TakyonError, match="marketing-only"):
        _commit(
            store,
            "business:latexflow",
            [{"action": "app.surface.upsert", "business": "latexflow", "source_path": "product/site"}],
            "blocked-product-surface",
        )

    allowed = _commit(
        store,
        "business:latexflow",
        [{"action": "artifact.write", "path": "outreach/test.md", "content": "marketing\n"}],
        "marketing-file",
    )
    assert allowed["results"][0]["path"] == "outreach/test.md"

    _commit(
        store,
        "business:latexflow",
        [{"action": "business.focus.set", "business": "latexflow", "work_focus": "product"}],
        "focus-product",
    )
    with pytest.raises(TakyonError, match="product-only"):
        _commit(
            store,
            "business:latexflow",
            [{"action": "outreach.local_publish", "channel": "x", "target": "@example", "subject": "Nope", "body": "Nope"}],
            "blocked-outreach",
        )


def test_business_set_work_focus_handler_returns_json(tmp_path, monkeypatch):
    monkeypatch.setenv("TAKYON_HOME", str(tmp_path))
    _commit(
        TakyonStore(tmp_path),
        "business:latexflow",
        [{"action": "business.upsert", "business": "latexflow", "name": "Latexflow"}],
        "init-handler-focus",
    )

    result = json.loads(
        handle_business_set_work_focus(
            {
                "business": "latexflow",
                "work_focus": "product-only",
                "idempotency_key": "handler-focus-product",
            }
        )
    )

    assert result["success"] is True
    assert result["results"][0]["work_focus"] == "product"


def test_focus_command_uses_current_shell_business(tmp_path, monkeypatch):
    monkeypatch.setenv("TAKYON_HOME", str(tmp_path))
    store = TakyonStore(tmp_path)
    _commit(
        store,
        "business:latexflow",
        [{"action": "business.upsert", "business": "latexflow", "name": "Latexflow"}],
        "init-shell-focus",
    )

    from plugins.takyon.cli import _handle_shell_line

    output, current = _handle_shell_line(
        "/focus marketing",
        current_business="latexflow",
        store=store,
        model="",
        max_turns=1,
    )

    assert current == "latexflow"
    assert "work focus -> marketing" in output
    assert store.read(scope="business:latexflow", query="summary")["business"]["work_focus"] == "marketing"


def test_wake_command_triggers_current_business_cron_immediately(tmp_path, monkeypatch):
    import cron.jobs as cron_jobs
    import cron.scheduler as cron_scheduler

    monkeypatch.setenv("TAKYON_HOME", str(tmp_path))
    cron_dir = tmp_path / "cron"
    monkeypatch.setattr(cron_jobs, "CRON_DIR", cron_dir)
    monkeypatch.setattr(cron_jobs, "JOBS_FILE", cron_dir / "jobs.json")
    monkeypatch.setattr(cron_jobs, "OUTPUT_DIR", cron_dir / "output")

    store = TakyonStore(tmp_path)
    _commit(
        store,
        "business:latexflow",
        [{"action": "business.upsert", "business": "latexflow", "name": "Latexflow"}],
        "init-shell-wake",
    )
    tick_calls: list[bool] = []

    def fake_tick(*, verbose: bool = True, adapters=None, loop=None) -> int:
        tick_calls.append(verbose)
        return 1

    monkeypatch.setattr(cron_scheduler, "tick", fake_tick)

    from plugins.takyon.cli import _handle_shell_line

    output, current = _handle_shell_line(
        "/wake",
        current_business="latexflow",
        store=store,
        model="",
        max_turns=1,
    )

    assert current == "latexflow"
    assert "recurring wake schedule" in output
    assert "triggered now" in output
    assert tick_calls == [False]

    jobs = cron_jobs.list_jobs(include_disabled=True)
    assert [job["name"] for job in jobs] == ["takyon-ceo:latexflow"]
    next_run_at = datetime.fromisoformat(str(jobs[0]["next_run_at"]))
    now = datetime.now(next_run_at.tzinfo) if next_run_at.tzinfo else datetime.now()
    assert next_run_at <= now + timedelta(seconds=2)


def test_focus_change_refreshes_existing_ceo_cron_prompt(tmp_path, monkeypatch):
    import cron.jobs as cron_jobs

    cron_dir = tmp_path / "cron"
    monkeypatch.setattr(cron_jobs, "CRON_DIR", cron_dir)
    monkeypatch.setattr(cron_jobs, "JOBS_FILE", cron_dir / "jobs.json")
    monkeypatch.setattr(cron_jobs, "OUTPUT_DIR", cron_dir / "output")

    store = TakyonStore(tmp_path)
    _commit(
        store,
        "business:latexflow",
        [{"action": "business.upsert", "business": "latexflow", "name": "Latexflow"}],
        "init-cron-focus",
    )
    _commit(
        store,
        "business:latexflow",
        [{"action": "cron.ensure_ceo_wakeup", "business": "latexflow", "schedule": "every 6h"}],
        "cron-focus",
    )
    job = cron_jobs.list_jobs(include_disabled=True)[0]
    cron_jobs.update_job(job["id"], {"prompt": "old prompt"})

    result = _commit(
        store,
        "business:latexflow",
        [{"action": "business.focus.set", "business": "latexflow", "work_focus": "product"}],
        "focus-refresh-cron",
    )

    refreshed = cron_jobs.list_jobs(include_disabled=True)[0]
    assert result["results"][0]["cron"]["updated"] is True
    assert "work_focus field" in refreshed["prompt"]
    assert "product means choose only product" in refreshed["prompt"]


def test_work_focus_blocks_direct_product_app_handlers(tmp_path, monkeypatch):
    monkeypatch.setenv("TAKYON_HOME", str(tmp_path))
    store = TakyonStore(tmp_path)
    _commit(
        store,
        "business:latexflow",
        [{"action": "business.upsert", "business": "latexflow", "name": "Latexflow"}],
        "init-direct-focus",
    )
    _commit(
        store,
        "business:latexflow",
        [{"action": "business.focus.set", "business": "latexflow", "work_focus": "marketing"}],
        "direct-focus-marketing",
    )

    result = json.loads(
        handle_business_request_app_magic_link(
            {
                "business": "latexflow",
                "email": "customer@example.com",
                "origin": "https://example.com",
            }
        )
    )

    assert result["success"] is False
    assert "marketing-only" in result["error"]


def test_test_outreach_local_publish_does_not_require_provider_credentials(tmp_path, monkeypatch):
    monkeypatch.setitem(_API_ENV_ALIASES, "reddit", ("TAKYON_TEST_REDDIT_TOKEN",))
    monkeypatch.delenv("TAKYON_TEST_REDDIT_TOKEN", raising=False)
    store = TakyonStore(tmp_path)
    _commit(
        store,
        "business:longer",
        [{"action": "business.upsert", "business": "longer", "name": "Longer", "mode": "test"}],
        "init",
    )

    result = _commit(
        store,
        "business:longer",
        [
            {
                "action": "outreach.local_publish",
                "business": "longer",
                "provider": "reddit",
                "channel": "reddit",
                "target": "r/example",
                "subject": "Local test",
                "body": "Suppressed local body",
            }
        ],
        "local-outreach",
    )["results"][0]

    assert result["external_side_effects"] == "suppressed"
    assert result["sent"] is False
    assert result["artifact"].startswith("distribution/local-published/")
    assert (tmp_path / "businesses" / "longer" / result["artifact"]).exists()
    assert not (tmp_path / "businesses" / "longer" / result["artifact"].replace("distribution/", "outreach/", 1)).exists()
    assert (tmp_path / "businesses" / "longer" / result["receipt"]).exists()
    root_files = store.read(scope="business:longer", query="list_files", path=".")["files"]
    assert "distribution" in {item["path"] for item in root_files}
    assert "receipts" not in {item["path"] for item in root_files}
    receipt_files = store.read(scope="business:longer", query="list_files", path="receipts")["files"]
    assert "receipts/outreach" in {item["path"] for item in receipt_files}


def test_manual_paid_entitlement_requires_billing_evidence(tmp_path):
    store = TakyonStore(tmp_path)
    _commit(
        store,
        "business:longer",
        [{"action": "business.upsert", "business": "longer", "name": "Longer"}],
        "init",
    )
    _commit(
        store,
        "business:longer",
        [{"action": "app.customer.upsert", "email": "customer@example.com"}],
        "customer",
    )

    with pytest.raises(TakyonError, match="fake billing state"):
        _commit(
            store,
            "business:longer",
            [{"action": "app.entitlement.upsert", "email": "customer@example.com", "tier": "pro"}],
            "manual-pro",
        )


def test_product_source_scanner_blocks_fake_auth_but_allows_local_product_logs(tmp_path):
    site = tmp_path / "product" / "site"
    site.mkdir(parents=True)
    (site / "app.js").write_text(
        "localStorage.setItem('longer_logs', JSON.stringify([]));\n"
        "localStorage.setItem('longer_session', JSON.stringify({email: 'demo@example.com'}));\n",
        encoding="utf-8",
    )

    findings = _scan_for_pretend_product_state(site)

    assert [finding["issue"] for finding in findings] == [
        "browser-local auth/session/account state",
    ]


def test_product_source_scanner_blocks_demo_checkout_and_unbacked_loading(tmp_path):
    site = tmp_path / "product" / "site"
    site.mkdir(parents=True)
    (site / "account.html").write_text(
        "<div id=\"account-email\">Loading...</div>\n"
        "<a href=\"/demo-checkout\">Checkout</a>\n"
        "<script>new URLSearchParams(location.search).has('demo')</script>\n",
        encoding="utf-8",
    )
    docs = site / "docs"
    docs.mkdir()
    (docs / "fixture.html").write_text(
        "<script>new URLSearchParams(location.search).has('demo')</script>\n",
        encoding="utf-8",
    )

    findings = _scan_for_pretend_product_state(site)

    assert sorted(finding["issue"] for finding in findings) == sorted([
        "fake payment or checkout",
        "demo login or demo session",
        "unbacked account/billing loading widget",
    ])


def test_brain_index_completion_gate_requires_feature_evidence(tmp_path):
    store = TakyonStore(tmp_path)
    _commit(
        store,
        "business:longer",
        [{"action": "business.upsert", "business": "longer", "name": "Longer"}],
        "init",
    )

    with pytest.raises(TakyonError, match="feature evidence ledger"):
        _commit(
            store,
            "business:longer",
            [
                {
                    "action": "memory.write",
                    "business": "longer",
                    "path": "index.md",
                    "content": "# Longer\n\nBootstrap: COMPLETE\n",
                }
            ],
            "bad-index",
        )

    result = _commit(
        store,
        "business:longer",
        [
            {
                "action": "memory.write",
                "business": "longer",
                "path": "index.md",
                "content": (
                    "# Longer\n\n"
                    "Bootstrap: COMPLETE\n\n"
                    "| Feature | Source files | Runtime/tool endpoint used | Audit/test record | Remaining blocker |\n"
                    "|---|---|---|---|---|\n"
                    "| Account | product/site/account.html | /api/takyon/apps/longer/account | agent record abc | blocked until browser endpoint is wired |\n"
                ),
            }
        ],
        "good-index",
    )

    assert result["results"][0]["path"] == "brain/index.md"


def test_gc_is_dry_run_by_default_and_keeps_protected_rows(tmp_path):
    store = TakyonStore(tmp_path)
    _commit(
        store,
        "business:latexflow",
        [{"action": "business.upsert", "business": "latexflow", "name": "Latexflow"}],
        "init",
    )
    _commit(
        store,
        "business:latexflow",
        [{"action": "event.record", "event_type": "note", "payload": {"x": 1}}],
        "event",
    )

    result = _commit(
        store,
        "global",
        [{"action": "maintenance.gc", "older_than_days": 7}],
        "gc-dry-run",
    )
    gc = result["results"][0]
    assert gc["dry_run"] is True
    assert "ledger_entries" in gc["protected"]
    assert store.read(scope="global", query="list_businesses")["businesses"][0]["slug"] == "latexflow"


def test_delete_business_dry_run_keeps_state_and_files(tmp_path):
    store = TakyonStore(tmp_path)
    _commit(
        store,
        "business:latexflow",
        [{"action": "business.upsert", "business": "latexflow", "name": "Latexflow"}],
        "init-delete-preview",
    )
    _commit(
        store,
        "business:latexflow",
        [{"action": "artifact.write", "path": "product/spec.md", "content": "# Spec\n"}],
        "write-delete-preview",
    )

    result = _commit(
        store,
        "business:latexflow",
        [{"action": "business.delete", "business": "latexflow", "delete_domains": False}],
        "delete-preview",
    )
    deletion = result["results"][0]

    assert deletion["dry_run"] is True
    assert deletion["filesystem"]["files"] >= 1
    assert (tmp_path / "businesses" / "latexflow" / "product" / "spec.md").exists()
    assert store.read(scope="global", query="list_businesses")["businesses"][0]["slug"] == "latexflow"


def test_delete_business_removes_files_rows_and_cron(tmp_path, monkeypatch):
    import cron.jobs as cron_jobs

    cron_dir = tmp_path / "cron"
    monkeypatch.setattr(cron_jobs, "CRON_DIR", cron_dir)
    monkeypatch.setattr(cron_jobs, "JOBS_FILE", cron_dir / "jobs.json")
    monkeypatch.setattr(cron_jobs, "OUTPUT_DIR", cron_dir / "output")

    store = TakyonStore(tmp_path)
    _commit(
        store,
        "business:latexflow",
        [{"action": "business.upsert", "business": "latexflow", "name": "Latexflow"}],
        "init-delete-confirm",
    )
    _commit(
        store,
        "business:latexflow",
        [{"action": "artifact.write", "path": "product/spec.md", "content": "# Spec\n"}],
        "write-delete-confirm",
    )
    cron_jobs.create_job(
        prompt="CEO wakeup for business:latexflow.",
        schedule="every 1h",
        name="takyon-ceo:latexflow",
        skills=["takyon:ceo"],
    )

    result = _commit(
        store,
        "business:latexflow",
        [
            {
                "action": "business.delete",
                "business": "latexflow",
                "confirm": True,
                "delete_domains": False,
            }
        ],
        "delete-confirm",
    )
    deletion = result["results"][0]

    assert deletion["dry_run"] is False
    assert deletion["filesystem"]["removed"] is True
    assert not (tmp_path / "businesses" / "latexflow").exists()
    assert store.read(scope="global", query="list_businesses")["businesses"] == []
    assert cron_jobs.list_jobs(include_disabled=True) == []


def test_tool_handlers_return_json(tmp_path, monkeypatch):
    monkeypatch.setenv("TAKYON_HOME", str(tmp_path))
    create = json.loads(
        handle_business_upsert_business(
            {
                "business": "latexflow",
                "name": "Latexflow",
                "idempotency_key": "handler-init",
            }
        )
    )
    assert create["success"] is True

    read = json.loads(handle_business_list_businesses({}))
    assert read["success"] is True
    assert read["businesses"][0]["slug"] == "latexflow"

    preview = json.loads(
        handle_business_delete_business(
            {
                "business": "latexflow",
                "delete_domains": False,
                "idempotency_key": "handler-delete-preview",
            }
        )
    )
    assert preview["success"] is True
    assert preview["results"][0]["dry_run"] is True


def test_conversation_messages_append_permanent_corpus(tmp_path):
    store = TakyonStore(tmp_path)
    _commit(
        store,
        "business:latexflow",
        [{"action": "business.upsert", "business": "latexflow", "name": "Latexflow"}],
        "init",
    )
    result = _commit(
        store,
        "business:latexflow",
        [
            {
                "action": "conversation.message.record",
                "business": "latexflow",
                "source": "reddit",
                "thread_external_id": "post-1",
                "thread_title": "Launch reply thread",
                "external_id": "comment-1",
                "direction": "inbound",
                "author_label": "grad-student",
                "body": "Does it support git sync?",
            }
        ],
        "reply",
    )

    message_result = result["results"][0]
    assert message_result["status"] == "needs_response"
    corpus = tmp_path / "businesses" / "latexflow" / "conversations" / "corpus" / "messages.jsonl"
    events = tmp_path / "businesses" / "latexflow" / "conversations" / "corpus" / "events.jsonl"
    lines = corpus.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    row = json.loads(lines[0])
    assert row["business"] == "latexflow"
    assert row["thread_external_id"] == "post-1"
    assert row["body"] == "Does it support git sync?"
    assert "conversation.message.record" in events.read_text(encoding="utf-8")


def test_conversation_message_status_update_rewrites_thread(tmp_path):
    store = TakyonStore(tmp_path)
    _commit(
        store,
        "business:latexflow",
        [{"action": "business.upsert", "business": "latexflow", "name": "Latexflow"}],
        "init",
    )
    _commit(
        store,
        "business:latexflow",
        [
            {
                "action": "conversation.message.record",
                "business": "latexflow",
                "source": "x",
                "thread_external_id": "post-2",
                "thread_title": "Pricing replies",
                "external_id": "reply-1",
                "direction": "inbound",
                "body": "Too expensive",
            }
        ],
        "reply",
    )
    result = _commit(
        store,
        "business:latexflow",
        [
            {
                "action": "conversation.message.status.set",
                "business": "latexflow",
                "source": "x",
                "external_id": "reply-1",
                "status": "ignored",
            }
        ],
        "ignore",
    )

    assert result["results"][0]["status"] == "ignored"
    thread_path = tmp_path / "businesses" / "latexflow" / result["results"][0]["file"]
    assert "Status: ignored" in thread_path.read_text(encoding="utf-8")


def test_business_generate_creative_asset_writes_local_video_and_receipt(tmp_path, monkeypatch):
    monkeypatch.setenv("TAKYON_HOME", str(tmp_path))
    monkeypatch.setenv("FAL_KEY", "test-fal-key")
    store = TakyonStore(tmp_path)
    _commit(
        store,
        "business:clipbook",
        [
            {
                "action": "business.upsert",
                "business": "clipbook",
                "name": "Clipbook",
                "budget": {"amount": 5},
            }
        ],
        "init-clipbook",
    )
    source = tmp_path / "source.mp4"
    source.write_bytes(b"fake mp4 bytes")

    import tools.video_generation_tool as video_tool

    monkeypatch.setattr(
        video_tool,
        "_handle_video_generate",
        lambda _args: json.dumps(
            {
                "success": True,
                "video": str(source),
                "provider": "fal",
                "model": "test-video-model",
                "prompt": "UGC test",
            }
        ),
    )

    result = json.loads(
        handle_business_generate_creative_asset(
            {
                "business": "clipbook",
                "kind": "video",
                "channel": "meta",
                "format": "ugc",
                "campaign": "launch",
                "prompt": "UGC test",
                "provider": "fal",
                "budget_usd": 0.25,
                "idempotency_key": "clipbook-meta-ugc-video",
            }
        )
    )

    assert result["success"] is True
    assert result["path"].startswith("distribution/launch/creatives/meta-ugc/")
    asset_path = tmp_path / "businesses" / "clipbook" / result["path"]
    receipt_path = tmp_path / "businesses" / "clipbook" / result["receipt"]
    assert asset_path.read_bytes() == b"fake mp4 bytes"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert receipt["external_side_effects"] == "local_asset_only"
    assert receipt["posted"] is False
    assert receipt["provider"] == "fal"


def test_business_publish_outreach_uses_test_mode_local_receipt(tmp_path, monkeypatch):
    monkeypatch.setenv("TAKYON_HOME", str(tmp_path))
    store = TakyonStore(tmp_path)
    _commit(
        store,
        "business:jobtailor",
        [
            {
                "action": "business.upsert",
                "business": "jobtailor",
                "name": "JobTailor",
                "mode": "test",
            }
        ],
        "init-jobtailor",
    )

    result = json.loads(
        handle_business_publish_outreach(
            {
                "business": "jobtailor",
                "channel": "meta",
                "target": "local-meta-test",
                "subject": "Try JobTailor",
                "body": "Try JobTailor\\n\\nPaste a job description and see what your resume is missing.",
                "idempotency_key": "jobtailor-meta-local-publish",
            }
        )
    )

    assert result["success"] is True
    publish = result["results"][0]
    assert publish["external_side_effects"] == "suppressed"
    assert publish["artifact"].startswith("distribution/local-published/")
    artifact = tmp_path / "businesses" / "jobtailor" / publish["artifact"]
    receipt = tmp_path / "businesses" / "jobtailor" / publish["receipt"]
    assert artifact.is_file()
    assert receipt.is_file()
    assert not (tmp_path / "businesses" / "jobtailor" / publish["artifact"].replace("distribution/", "outreach/", 1)).exists()
    artifact_text = artifact.read_text(encoding="utf-8")
    assert artifact_text == "# Try JobTailor\n\nPaste a job description and see what your resume is missing.\n"
    assert "\\n" not in artifact_text
    assert "External side effects" not in artifact_text
    assert "Local publish id" not in artifact_text
    assert "Provider" not in artifact_text
    receipt_payload = json.loads(receipt.read_text(encoding="utf-8"))
    assert receipt_payload["sent"] is False
    assert receipt_payload["external_side_effects"] == "suppressed"
    assert receipt_payload["provider"] == "meta"
    assert receipt_payload["artifact_path"] == publish["artifact"]


def test_business_publish_outreach_canonicalizes_product_url(tmp_path, monkeypatch):
    monkeypatch.setenv("TAKYON_HOME", str(tmp_path))
    store = TakyonStore(tmp_path)
    _commit(
        store,
        "business:latexflow",
        [
            {
                "action": "business.upsert",
                "business": "latexflow",
                "name": "LatexFlow",
                "mode": "test",
            },
        ],
        "init-latexflow-outreach",
    )
    _commit(
        store,
        "business:latexflow",
        [
            {
                "action": "app.surface.upsert",
                "business": "latexflow",
                "status": "draft",
                "source_path": "product/site",
                "publish_target": "https://latexflow.fourmanifold.com/",
            },
        ],
        "surface-latexflow-outreach",
    )

    preview, replacements = _canonicalize_business_product_links(
        "https://latexflow.io (coming soon)",
        business="latexflow",
        canonical_url="https://latexflow.fourmanifold.com/",
    )
    assert preview == "https://latexflow.fourmanifold.com/ (coming soon)"
    assert replacements == [{"from": "https://latexflow.io", "to": "https://latexflow.fourmanifold.com/"}]

    result = json.loads(
        handle_business_publish_outreach(
            {
                "business": "latexflow",
                "channel": "reddit",
                "target": "r/latex",
                "subject": "Try LatexFlow",
                "body": "Git sync and magic links.\n\nhttps://latexflow.io (coming soon)",
                "idempotency_key": "latexflow-canonical-outreach",
            }
        )
    )

    assert result["success"] is True
    publish = result["results"][0]
    artifact = tmp_path / "businesses" / "latexflow" / publish["artifact"]
    receipt = tmp_path / "businesses" / "latexflow" / publish["receipt"]
    assert "https://latexflow.fourmanifold.com/ (coming soon)" in artifact.read_text(encoding="utf-8")
    assert "latexflow.io" not in artifact.read_text(encoding="utf-8")
    receipt_payload = json.loads(receipt.read_text(encoding="utf-8"))
    assert receipt_payload["metadata"]["canonical_product_url"] == "https://latexflow.fourmanifold.com/"
    assert receipt_payload["metadata"]["canonicalized_product_links"]


def test_business_publish_outreach_records_intended_destination(tmp_path, monkeypatch):
    monkeypatch.setenv("TAKYON_HOME", str(tmp_path))
    store = TakyonStore(tmp_path)
    _commit(
        store,
        "business:domainpulse",
        [
            {
                "action": "business.upsert",
                "business": "domainpulse",
                "name": "DomainPulse",
                "mode": "test",
            }
        ],
        "init-domainpulse",
    )

    result = json.loads(
        handle_business_publish_outreach(
            {
                "business": "domainpulse",
                "channel": "show_hn",
                "provider": "hacker_news",
                "target": "news.ycombinator.com",
                "subject": "Show HN: DomainPulse",
                "body": "I built this to compare registrar renewal prices.",
                "idempotency_key": "domainpulse-show-hn-local-publish",
            }
        )
    )

    assert result["success"] is True
    publish = result["results"][0]
    assert publish["destination_url"] == "https://news.ycombinator.com/submit"
    artifact = tmp_path / "businesses" / "domainpulse" / publish["artifact"]
    receipt = tmp_path / "businesses" / "domainpulse" / publish["receipt"]
    artifact_text = artifact.read_text(encoding="utf-8")
    assert "Destination: https://news.ycombinator.com/submit" in artifact_text
    receipt_payload = json.loads(receipt.read_text(encoding="utf-8"))
    assert receipt_payload["target"] == "news.ycombinator.com"
    assert receipt_payload["destination_url"] == "https://news.ycombinator.com/submit"
    assert receipt_payload["external_side_effects"] == "suppressed"
    assert receipt_payload["sent"] is False


def test_business_publish_outreach_live_requires_provider_gate(tmp_path, monkeypatch):
    monkeypatch.setenv("TAKYON_HOME", str(tmp_path))
    monkeypatch.delenv("META_ACCESS_TOKEN", raising=False)
    monkeypatch.delenv("FACEBOOK_ACCESS_TOKEN", raising=False)
    store = TakyonStore(tmp_path)
    _commit(
        store,
        "business:jobtailor",
        [
            {
                "action": "business.upsert",
                "business": "jobtailor",
                "name": "JobTailor",
                "mode": "live",
            }
        ],
        "init-jobtailor-live",
    )

    result = json.loads(
        handle_business_publish_outreach(
                {
                    "business": "jobtailor",
                    "channel": "meta",
                    "provider": "MISSING_OUTREACH_PROVIDER",
                    "subject": "Try JobTailor",
                    "body": "Paste a job description and see what your resume is missing.",
                    "idempotency_key": "jobtailor-meta-live-publish",
                }
        )
    )

    assert result["success"] is False
    assert "requires missing API/env credential" in result["error"]
