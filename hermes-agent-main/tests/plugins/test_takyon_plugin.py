"""Tests for the Takyon CEO operator plugin."""

from __future__ import annotations

import json
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
    handle_business_list_businesses,
    handle_business_publish_outreach,
    handle_business_request_app_magic_link,
    handle_business_ugc_ad_write,
    handle_business_claude_agent_task,
    handle_business_set_work_focus,
    handle_business_list_conversation_messages,
    handle_business_read_conversation_thread,
    handle_business_upsert_app_surface_contract,
    handle_business_upsert_business,
    handle_business_verify_product_surface,
)


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


def test_plugin_registers_tools_and_commands():
    import plugins.takyon as takyon

    ctx = _FakePluginContext()
    takyon.register(ctx)
    assert sorted(ctx.tools) == sorted(tool["name"] for tool in TAKYON_TOOL_DEFINITIONS)
    assert "business_list_conversation_messages" in ctx.tools
    assert "business_read_conversation_thread" in ctx.tools
    assert "business_conversation_agent_task" not in ctx.tools
    assert ctx.skills == []
    assert ctx.commands == ["takyon"]
    assert set(ctx.slash_commands) == {"takyon"}


def test_bundled_takyon_skills_exist():
    skills_root = Path(__file__).resolve().parents[2] / "skills" / "takyon"
    skill_files = {path.parent.name: path for path in skills_root.glob("*/SKILL.md")}
    assert set(skill_files) == {
        "ugc-video-ad",
        "takyon-app-runtime",
        "takyon-build-product",
        "takyon-business-metrics",
        "takyon-claude-agent-sdk",
        "takyon-conversation-followup",
        "takyon-distribution",
        "takyon-market-research",
        "takyon-reddit",
        "takyon-x",
    }
    for path in skill_files.values():
        text = path.read_text(encoding="utf-8")
        assert text.startswith("---\nname:")


def test_bootstrap_prompt_requires_distribution_campaign_batch():
    from plugins.takyon.cli import _business_bootstrap_instruction

    prompt = _business_bootstrap_instruction("demo", "find users", "test")

    assert "distribution/campaign/" in prompt
    assert "3 evidence-backed lanes" in prompt
    assert "6 total" in prompt
    assert "business_publish_outreach" in prompt
    assert "not a forever recurring funnel" in prompt


def test_bootstrap_prompt_requires_real_product_source_before_runtime_mirrors():
    from plugins.takyon.cli import _business_bootstrap_instruction

    prompt = _business_bootstrap_instruction("demo", "find users", "test")

    assert "product/site/" in prompt
    assert "default bootstrap surface mode is app_shell" in prompt
    assert "product/surface.md records that source_path truthfully" in prompt
    assert "Do not expand product/runtime.md" in prompt


def test_bootstrap_prompt_orders_surface_before_distribution_before_runtime():
    from plugins.takyon.cli import _business_bootstrap_instruction

    prompt = _business_bootstrap_instruction("demo", "find users", "test")

    surface_index = prompt.index("Then normally use takyon-build-product")
    distribution_index = prompt.index("then create or continue distribution/campaign/")
    runtime_index = prompt.index("Do not expand product/runtime.md")

    assert surface_index < distribution_index < runtime_index


def test_runtime_mirror_files_wait_for_real_public_surface(tmp_path, monkeypatch):
    monkeypatch.setenv("TAKYON_HOME", str(tmp_path))
    store = TakyonStore(tmp_path)
    _commit(
        store,
        "business:latexflow",
        [{"action": "business.upsert", "business": "latexflow", "name": "Latexflow"}],
        "init-runtime-guard",
    )

    _commit(
        store,
        "business:latexflow",
        [{"action": "app.budget.set", "business": "latexflow", "hard_limit_microusd": 7_500_000}],
        "budget-before-surface",
    )

    product_root = tmp_path / "businesses" / "latexflow" / "product"
    assert (product_root / "surface.md").exists()
    assert not (product_root / "runtime.md").exists()
    assert not (product_root / "plans.md").exists()
    assert not (product_root / "customers.md").exists()
    assert not (product_root / "billing.md").exists()
    assert not (product_root / "usage.md").exists()


def test_runtime_mirror_files_resume_once_real_public_surface_exists(tmp_path, monkeypatch):
    monkeypatch.setenv("TAKYON_HOME", str(tmp_path))
    store = TakyonStore(tmp_path)
    _commit(
        store,
        "business:latexflow",
        [{"action": "business.upsert", "business": "latexflow", "name": "Latexflow"}],
        "init-runtime-after-surface",
    )
    site = tmp_path / "businesses" / "latexflow" / "product" / "site"
    site.mkdir(parents=True)
    (site / "index.html").write_text("<h1>Latexflow</h1>\n", encoding="utf-8")
    _commit(
        store,
        "business:latexflow",
        [{"action": "app.surface.upsert", "business": "latexflow", "source_path": "product/site"}],
        "surface-with-source",
    )

    _commit(
        store,
        "business:latexflow",
        [{"action": "app.budget.set", "business": "latexflow", "hard_limit_microusd": 7_500_000}],
        "budget-after-surface",
    )

    product_root = tmp_path / "businesses" / "latexflow" / "product"
    assert (product_root / "runtime.md").exists()
    assert (product_root / "plans.md").exists()
    assert (product_root / "customers.md").exists()
    assert (product_root / "billing.md").exists()
    assert (product_root / "usage.md").exists()


def test_ceo_wake_prompt_includes_outreach_lifecycle(tmp_path):
    store = TakyonStore(tmp_path)
    prompt = store._ceo_cron_prompt("demo")

    assert "Start with business_calculate_pulse" in prompt
    assert "takyon-business-metrics" in prompt
    assert "takyon-conversation-followup" in prompt
    assert "business_conversation_agent_task" not in prompt
    assert store._ceo_cron_toolsets() == ["takyon", "web", "skills", "todo"]


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


def test_takyon_skills_index_command_is_removed():
    from plugins.takyon.cli import run_takyon_command, takyon_slash_command

    message = "takyon skills-index was removed. Start a fresh ./takyon run or relaunch the shell to sync bundled skills automatically."

    with pytest.raises(SystemExit, match="skills-index was removed"):
        run_takyon_command(["skills-index"])

    assert takyon_slash_command("skills-index") == message


def test_plugin_cli_main_syncs_bundled_skills_on_startup(monkeypatch):
    import plugins.takyon.cli as takyon_cli
    import tools.skills_sync as skills_sync

    calls: list[tuple[str, object]] = []

    class _Parser:
        def parse_args(self, argv):
            calls.append(("parse_args", list(argv or [])))
            return types.SimpleNamespace(example=True)

    monkeypatch.setattr(takyon_cli, "build_parser", lambda: _Parser())
    monkeypatch.setattr(
        takyon_cli,
        "takyon_command",
        lambda args: calls.append(("takyon_command", args)),
    )
    monkeypatch.setattr(
        skills_sync,
        "sync_skills",
        lambda quiet=True: calls.append(("sync_skills", quiet)),
    )

    takyon_cli.main(["commands"])

    assert ("parse_args", ["commands"]) in calls
    assert ("sync_skills", True) in calls
    assert any(name == "takyon_command" for name, _ in calls)


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


def test_takyon_slash_can_proxy_takyon_skills(monkeypatch):
    import agent.skill_commands as skill_commands
    import plugins.takyon as takyon

    monkeypatch.setattr(
        skill_commands,
        "resolve_skill_command_key",
        lambda name: "/takyon-market-research" if name in {"market-research", "takyon-market-research"} else None,
    )
    monkeypatch.setattr(
        skill_commands,
        "build_skill_invocation_message",
        lambda cmd_key, user_instruction="", **_: f"skill={cmd_key}; instruction={user_instruction}",
    )

    ctx = _FakePluginContext()
    takyon.register(ctx)
    result = ctx.slash_commands["takyon"]["handler"]("market-research find channels")

    assert result == "Queued Takyon skill /takyon-market-research."
    assert ctx.injected == [("user", "skill=/takyon-market-research; instruction=find channels")]


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

    result = store.read(scope="business:latexflow", query="read_file", path="research/pricing.md")
    assert result["content"] == "# Pricing\n"

    with pytest.raises(TakyonError):
        store.read(scope="business:other", query="read_file", path="research/pricing.md")


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


def test_business_upsert_seeds_only_canonical_roots(tmp_path):
    store = TakyonStore(tmp_path)
    _commit(
        store,
        "business:latexflow",
        [{"action": "business.upsert", "business": "latexflow", "name": "Latexflow", "goal": "Build PDFs"}],
        "seed-canonical-roots",
    )

    root = tmp_path / "businesses" / "latexflow"
    assert (root / "product").is_dir()
    assert (root / "distribution").is_dir()
    assert (root / "research").is_dir()
    assert (root / "metrics").is_dir()
    assert (root / "research" / "strategy.md").exists()
    assert not (root / "research" / "market.md").exists()
    assert not (root / "metrics" / "summary.md").exists()
    assert not (root / "product" / "design-brief.md").exists()
    assert not (root / "distribution" / "campaign").exists()


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
        [{"action": "artifact.write", "business": "latexflow", "path": "metrics/summary.md", "content": "# Summary\n\nBaseline.\n"}],
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


def test_app_like_surface_claiming_app_route_without_real_source_is_blocked(tmp_path, monkeypatch):
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
                "idempotency_key": "verify-app-like-claim-only",
            }
        )
    )["verification"]

    assert verification["status"] == "blocked"
    assert verification["done_gate_status"] == "blocked"
    assert verification["publish"]["status"] == "blocked"
    assert "generated source does not include a working app subroute" in verification["blocker"]
    assert not (tmp_path / "published-sites" / "briefpilot" / "index.html").exists()


def test_static_app_surface_with_real_app_route_passes(tmp_path, monkeypatch):
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
        """,
        encoding="utf-8",
    )
    app_dir = site / "app"
    app_dir.mkdir()
    (app_dir / "index.html").write_text(
        """
        <h1>BriefPilot App</h1>
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
    assert inventory["routes"] == ["/", "/app"]
    assert inventory["declared_routes"] == ["/app"]
    assert {"auth", "generate", "session"}.issubset(set(inventory["runtime_integrations"]))
    assert {"form", "input", "runtime_fetch"}.issubset(set(inventory["workflow_markers"]))
    assert (tmp_path / "published-sites" / "briefpilot" / "index.html").exists()
    assert (tmp_path / "published-sites" / "briefpilot" / "app" / "index.html").exists()


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
    assert "@takyon_app_runtime path /api/*" in caddyfile
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


def test_business_verify_product_surface_uses_longer_default_timeout(monkeypatch):
    captured: dict[str, object] = {}

    class _FakeStore:
        def read(self, **_: object) -> dict[str, object]:
            return {
                "app": {
                    "surface": {
                        "source_path": "product/site",
                        "publish_target": "https://latexflow.fourmanifold.com/",
                    }
                }
            }

        def commit(self, **_: object) -> dict[str, object]:
            return {"success": True, "results": []}

    def fake_finalize(**kwargs: object) -> dict[str, object]:
        captured["timeout_seconds"] = kwargs["timeout_seconds"]
        return {
            "status": "passed",
            "done_gate_status": "passed",
            "publish": {
                "status": "published",
                "public_url": "https://latexflow.fourmanifold.com/",
                "blocker": "",
            },
            "receipt_path": "metrics/receipts/product-surface/test.json",
            "inventory": {},
        }

    monkeypatch.setattr(takyon_core, "_store", lambda: _FakeStore())
    monkeypatch.setattr(takyon_core, "_finalize_product_surface_verification", fake_finalize)
    monkeypatch.setattr(
        takyon_core,
        "_product_surface_verification_operations",
        lambda **_: [{"action": "event.record", "business": "latexflow", "scope": "business:latexflow", "event_type": "test", "payload": {}}],
    )

    result = json.loads(
        handle_business_verify_product_surface(
            {
                "business": "latexflow",
                "idempotency_key": "verify-default-timeout",
            }
        )
    )

    assert result["success"] is True
    assert captured["timeout_seconds"] == 300


def test_bundled_claude_design_guidance_skills_exist():
    skills_root = Path(__file__).resolve().parents[2] / "skills" / "creative"
    expected = {
        "claude-design",
        "claude-design-openai",
        "claude-design-stripe",
        "claude-design-superhuman",
        "claude-design-vibrant",
        "claude-design-doodle",
    }
    found = {path.parent.name for path in skills_root.glob("*/SKILL.md")}
    assert expected.issubset(found)


def test_business_verify_product_surface_treats_null_install_as_default_true(monkeypatch):
    captured: dict[str, object] = {}

    class _FakeStore:
        def read(self, **_: object) -> dict[str, object]:
            return {
                "app": {
                    "surface": {
                        "source_path": "product/site",
                        "publish_target": "https://latexflow.fourmanifold.com/",
                    }
                }
            }

        def commit(self, **_: object) -> dict[str, object]:
            return {"success": True, "results": []}

    def fake_finalize(**kwargs: object) -> dict[str, object]:
        captured["install"] = kwargs["install"]
        return {
            "status": "passed",
            "done_gate_status": "passed",
            "publish": {
                "status": "published",
                "public_url": "https://latexflow.fourmanifold.com/",
                "blocker": "",
            },
            "receipt_path": "metrics/receipts/product-surface/test.json",
            "inventory": {},
        }

    monkeypatch.setattr(takyon_core, "_store", lambda: _FakeStore())
    monkeypatch.setattr(takyon_core, "_finalize_product_surface_verification", fake_finalize)
    monkeypatch.setattr(
        takyon_core,
        "_product_surface_verification_operations",
        lambda **_: [{"action": "event.record", "business": "latexflow", "scope": "business:latexflow", "event_type": "test", "payload": {}}],
    )

    result = json.loads(
        handle_business_verify_product_surface(
            {
                "business": "latexflow",
                "install": None,
                "idempotency_key": "verify-null-install-default",
            }
        )
    )

    assert result["success"] is True
    assert captured["install"] is True


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


def test_claude_agent_task_injects_customer_facing_ai_copy_contract_for_product_work(tmp_path, monkeypatch):
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
                "instruction": "Refresh the landing page copy.",
                "idempotency_key": "workspace-ai-copy-contract",
                "install": False,
            }
        )
    )

    instruction = captured["payload"]["instruction"]
    assert result["success"] is True
    assert "Customer-facing AI product copy contract" in instruction
    assert "Claude Opus 4.7, Claude Sonnet 4.6, and Claude Haiku 4.5" in instruction
    assert "Do not describe Claude-backed behavior with GPT names" in instruction


def test_app_surface_contract_records_runtime_features(tmp_path, monkeypatch):
    monkeypatch.setenv("TAKYON_HOME", str(tmp_path))
    store = TakyonStore(tmp_path)
    _commit(
        store,
        "business:latexflow",
        [{"action": "business.upsert", "business": "latexflow", "name": "Latexflow", "budget": {"amount": 25}}],
        "init",
    )

    result = json.loads(
        handle_business_upsert_app_surface_contract(
            {
                "business": "latexflow",
                "source_path": "product/site",
                "runtime_features": ["auth", "checkout", "generate"],
                "idempotency_key": "surface-runtime-features",
            }
        )
    )

    assert result["success"] is True
    app = store.read(scope="business:latexflow", query="summary", include=["app"])["app"]
    assert app["surface_contract"]["runtime_features"] == ["auth", "checkout", "generate"]
    surface_md = (tmp_path / "businesses" / "latexflow" / "product" / "surface.md").read_text(encoding="utf-8")
    assert "Runtime features: auth, checkout, generate" in surface_md


def test_app_surface_contract_rejects_unknown_runtime_features(tmp_path, monkeypatch):
    monkeypatch.setenv("TAKYON_HOME", str(tmp_path))
    store = TakyonStore(tmp_path)
    _commit(
        store,
        "business:latexflow",
        [{"action": "business.upsert", "business": "latexflow", "name": "Latexflow", "budget": {"amount": 25}}],
        "init",
    )

    result = json.loads(
        handle_business_upsert_app_surface_contract(
            {
                "business": "latexflow",
                "source_path": "product/site",
                "runtime_features": ["auth", "made_up_rail"],
                "idempotency_key": "surface-runtime-features-invalid",
            }
        )
    )

    assert result["success"] is False
    assert "unknown runtime_features" in result["error"]


def test_claude_agent_task_injects_runtime_ui_contract_for_product_work(tmp_path, monkeypatch):
    monkeypatch.setenv("TAKYON_HOME", str(tmp_path))
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
                "action": "app.surface.upsert",
                "business": "latexflow",
                "source_path": "product/site",
                "runtime_features": ["auth", "checkout", "generate"],
            }
        ],
        "surface-contract-runtime-features",
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
                "instruction": "Build the product shell.",
                "idempotency_key": "workspace-runtime-contract",
                "install": False,
            }
        )
    )

    instruction = captured["payload"]["instruction"]
    assert result["success"] is True
    assert "Hermes runtime UI contract" in instruction
    assert "Declared runtime-backed features: auth, checkout, generate" in instruction
    assert "Runtime API base: /api/takyon/apps/latexflow" in instruction
    assert "checkout (owner: takyon-app-runtime)" in instruction
    assert "Canonical tools: business_create_app_checkout, business_record_stripe_webhook" in instruction
    assert "Exact runtime endpoints: POST /api/takyon/apps/latexflow/auth/request, GET /api/takyon/apps/latexflow/auth/verify, GET /api/takyon/apps/latexflow/session" in instruction
    assert "Exact runtime endpoints: POST /api/takyon/apps/latexflow/checkout" in instruction
    assert "Exact runtime endpoints: POST /api/takyon/apps/latexflow/generate" in instruction


def test_runtime_md_lists_selected_and_owned_runtime_rails(tmp_path, monkeypatch):
    monkeypatch.setenv("TAKYON_HOME", str(tmp_path))
    store = TakyonStore(tmp_path)
    _commit(
        store,
        "business:latexflow",
        [{"action": "business.upsert", "business": "latexflow", "name": "Latexflow", "budget": {"amount": 25}}],
        "init",
    )
    site = tmp_path / "businesses" / "latexflow" / "product" / "site"
    site.mkdir(parents=True)
    (site / "index.html").write_text("<h1>Latexflow</h1>\n", encoding="utf-8")
    _commit(
        store,
        "business:latexflow",
        [
            {
                "action": "app.surface.upsert",
                "business": "latexflow",
                "source_path": "product/site",
                "runtime_features": ["auth", "checkout"],
            }
        ],
        "surface-runtime-md-rails",
    )

    runtime_md = (tmp_path / "businesses" / "latexflow" / "product" / "runtime.md").read_text(encoding="utf-8")
    assert "## Selected Runtime Rails" in runtime_md
    assert "- auth — owner: takyon-app-runtime" in runtime_md
    assert "- checkout — owner: takyon-app-runtime" in runtime_md
    assert "## Rails By Owner" in runtime_md
    assert "### takyon-app-runtime" in runtime_md
    assert "Tools: business_create_app_checkout, business_record_stripe_webhook" in runtime_md


def test_claude_agent_task_distills_guidance_skill_into_worker_instruction(tmp_path, monkeypatch):
    monkeypatch.setenv("TAKYON_HOME", str(tmp_path))
    store = TakyonStore(tmp_path)
    _commit(
        store,
        "business:latexflow",
        [{"action": "business.upsert", "business": "latexflow", "name": "Latexflow", "budget": {"amount": 25}}],
        "init",
    )

    skills_dir = tmp_path / "skills"
    skill_file = skills_dir / "creative" / "claude-design" / "SKILL.md"
    skill_file.parent.mkdir(parents=True, exist_ok=True)
    skill_file.write_text(
        """---
name: claude-design
---

# Claude Design for CLI/API Agents

Use this skill for strong product UI work.

## When To Use

- landing pages
- dashboard concepts

## Workflow

1. Read the product context.
2. Build the artifact with intentional hierarchy.

## Artifact Format Rules

- Prefer local files.
""",
        encoding="utf-8",
    )

    captured: dict[str, object] = {}

    def fake_run(command, *, input=None, **kwargs):
        if len(command) > 1 and str(command[1]).endswith("takyon-claude-agent-task.mjs"):
            payload = json.loads(input or "{}")
            captured["payload"] = payload
            Path(payload["cwd"], "index.html").write_text("<h1>Latexflow</h1>\n", encoding="utf-8")
            return types.SimpleNamespace(returncode=0, stdout=json.dumps({"success": True, "summary": "ok"}), stderr="")
        return types.SimpleNamespace(returncode=0, stdout="v99.0.0\n", stderr="")

    monkeypatch.setattr(takyon_core, "get_all_skills_dirs", lambda: [skills_dir])
    monkeypatch.setattr(takyon_core, "_require_api_access", lambda *args, **kwargs: None)
    monkeypatch.setattr(takyon_core, "_resolve_runtime_executable", lambda name: "/usr/bin/node" if name == "node" else None)
    monkeypatch.setattr(takyon_core, "_ensure_repo_node_dependencies", lambda packages: {"success": True})
    monkeypatch.setattr(takyon_core.subprocess, "run", fake_run)

    result = json.loads(
        handle_business_claude_agent_task(
            {
                "business": "latexflow",
                "workspace": "product/site",
                "instruction": "Build a more intentional product surface.",
                "guidance_skills": ["claude-design"],
                "idempotency_key": "workspace-guidance",
                "install": False,
            }
        )
    )

    instruction = captured["payload"]["instruction"]
    assert result["success"] is True
    assert result["guidance_skills"] == ["claude-design"]
    assert "[Hermes guidance skill: claude-design]" in instruction
    assert "## Workflow" in instruction
    assert "Build the artifact with intentional hierarchy." in instruction
    assert "current working directory is already the requested business workspace: product/site" in instruction


def test_claude_agent_task_distills_method_and_style_guidance_skills(tmp_path, monkeypatch):
    monkeypatch.setenv("TAKYON_HOME", str(tmp_path))
    store = TakyonStore(tmp_path)
    _commit(
        store,
        "business:dogsnap",
        [{"action": "business.upsert", "business": "dogsnap", "name": "Dogsnap", "budget": {"amount": 25}}],
        "init",
    )

    skills_dir = tmp_path / "skills"
    method_file = skills_dir / "creative" / "claude-design" / "SKILL.md"
    style_file = skills_dir / "creative" / "claude-design-doodle" / "SKILL.md"
    method_file.parent.mkdir(parents=True, exist_ok=True)
    style_file.parent.mkdir(parents=True, exist_ok=True)

    method_file.write_text(
        """---
name: claude-design
---

# Claude Design

Use this skill for outward-facing product/site work.

## Shared Style Selection

- `claude-design-doodle`: whimsical playful consumer

## Workflow

1. Pick one coherent style skill.
2. Build the surface with intentional hierarchy.
""",
        encoding="utf-8",
    )
    style_file.write_text(
        """---
name: claude-design-doodle
---

# Claude Design Doodle

Playful shared design system.

## Visual Direction

- whimsical
- friendly

## Hard Rules

- playful still has to ship
""",
        encoding="utf-8",
    )

    captured: dict[str, object] = {}

    def fake_run(command, *, input=None, **kwargs):
        if len(command) > 1 and str(command[1]).endswith("takyon-claude-agent-task.mjs"):
            payload = json.loads(input or "{}")
            captured["payload"] = payload
            Path(payload["cwd"], "index.html").write_text("<h1>Dogsnap</h1>\n", encoding="utf-8")
            return types.SimpleNamespace(returncode=0, stdout=json.dumps({"success": True, "summary": "ok"}), stderr="")
        return types.SimpleNamespace(returncode=0, stdout="v99.0.0\n", stderr="")

    monkeypatch.setattr(takyon_core, "get_all_skills_dirs", lambda: [skills_dir])
    monkeypatch.setattr(takyon_core, "_require_api_access", lambda *args, **kwargs: None)
    monkeypatch.setattr(takyon_core, "_resolve_runtime_executable", lambda name: "/usr/bin/node" if name == "node" else None)
    monkeypatch.setattr(takyon_core, "_ensure_repo_node_dependencies", lambda packages: {"success": True})
    monkeypatch.setattr(takyon_core.subprocess, "run", fake_run)

    result = json.loads(
        handle_business_claude_agent_task(
            {
                "business": "dogsnap",
                "workspace": "product/site",
                "instruction": "Build a playful dog photo-sharing homepage.",
                "guidance_skills": ["claude-design", "claude-design-doodle"],
                "idempotency_key": "workspace-guidance-pair",
                "install": False,
            }
        )
    )

    instruction = captured["payload"]["instruction"]
    assert result["success"] is True
    assert result["guidance_skills"] == ["claude-design", "claude-design-doodle"]
    assert "[Hermes guidance skill: claude-design]" in instruction
    assert "[Hermes guidance skill: claude-design-doodle]" in instruction
    assert "Pick one coherent style skill." in instruction
    assert "playful still has to ship" in instruction


def test_claude_agent_task_publishes_verified_product_surface(tmp_path, monkeypatch):
    monkeypatch.setenv("TAKYON_HOME", str(tmp_path))
    monkeypatch.setenv("TAKYON_PRODUCT_SITE_ROOT", str(tmp_path / "published-sites"))
    store = TakyonStore(tmp_path)
    _commit(
        store,
        "business:latexflow",
        [{"action": "business.upsert", "business": "latexflow", "name": "Latexflow", "budget": {"amount": 25}}],
        "init",
    )

    def fake_run(command, *, input=None, **kwargs):
        if len(command) > 1 and str(command[1]).endswith("takyon-claude-agent-task.mjs"):
            payload = json.loads(input or "{}")
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
                "idempotency_key": "workspace-publish",
                "install": False,
            }
        )
    )

    assert result["success"] is True
    assert result["verification"]["status"] == "passed"
    assert result["verification"]["publish"]["status"] == "published"
    assert (tmp_path / "published-sites" / "latexflow" / "index.html").exists()
    app = store.read(scope="business:latexflow", query="summary", include=["app"])["app"]
    assert app["surface_contract"]["status"] == "active"
    assert app["surface_contract"]["publish_status"] == "published"
    assert app["surface_contract"]["public_url"] == "https://latexflow.fourmanifold.com/"


def test_claude_agent_task_treats_null_install_as_default_true_for_verification(tmp_path, monkeypatch):
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
            Path(payload["cwd"], "index.html").write_text("<h1>Latexflow</h1>\n", encoding="utf-8")
            return types.SimpleNamespace(returncode=0, stdout=json.dumps({"success": True, "summary": "ok"}), stderr="")
        return types.SimpleNamespace(returncode=0, stdout="v99.0.0\n", stderr="")

    def fake_finalize(**kwargs: object) -> dict[str, object]:
        captured["install"] = kwargs["install"]
        return {
            "status": "passed",
            "done_gate_status": "passed",
            "publish": {
                "status": "published",
                "public_url": "https://latexflow.fourmanifold.com/",
                "blocker": "",
            },
            "receipt_path": "metrics/receipts/product-surface/test.json",
            "inventory": {},
        }

    monkeypatch.setattr(takyon_core, "_require_api_access", lambda *args, **kwargs: None)
    monkeypatch.setattr(takyon_core, "_resolve_runtime_executable", lambda name: "/usr/bin/node" if name == "node" else None)
    monkeypatch.setattr(takyon_core, "_ensure_repo_node_dependencies", lambda packages: {"success": True})
    monkeypatch.setattr(takyon_core.subprocess, "run", fake_run)
    monkeypatch.setattr(takyon_core, "_finalize_product_surface_verification", fake_finalize)

    result = json.loads(
        handle_business_claude_agent_task(
            {
                "business": "latexflow",
                "workspace": "product/site",
                "instruction": "Build the product surface under product/site.",
                "idempotency_key": "workspace-null-install-default",
                "install": None,
            }
        )
    )

    assert result["success"] is True
    assert captured["install"] is True


def test_product_verification_defaults_publish_root_to_takyon_home_product_sites(tmp_path, monkeypatch):
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
    assert verification["verification"]["done_gate_status"] == "passed"
    assert verification["verification"]["publish"]["status"] == "published"
    assert verification["verification"]["publish"]["publish_root"] == str(tmp_path / "product-sites" / "latexflow")
    app = store.read(scope="business:latexflow", query="summary", include=["app"])["app"]
    assert app["surface_contract"]["status"] == "active"
    assert app["surface_contract"]["publish_status"] == "published"
    assert (tmp_path / "product-sites" / "latexflow" / "index.html").exists()


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
    caddyfile = (tmp_path / "Caddyfile").read_text(encoding="utf-8")
    assert "latexflow.fourmanifold.com" in caddyfile
    assert "@takyon_app_runtime path /api/*" in caddyfile
    assert "reverse_proxy 127.0.0.1:9119" in caddyfile


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


def test_verify_next_product_with_static_export_still_runs_build(tmp_path, monkeypatch):
    business_root = tmp_path / "businesses" / "feedbackpilot"
    site = business_root / "product" / "site"
    site.mkdir(parents=True)
    (site / "out").mkdir()
    (site / "out" / "index.html").write_text("<h1>Static export exists</h1>\n", encoding="utf-8")
    (site / "next.config.js").write_text("module.exports = {};\n", encoding="utf-8")
    (site / "package.json").write_text(
        json.dumps(
            {
                "name": "feedbackpilot-site",
                "private": True,
                "scripts": {"build": "next build", "start": "next start"},
                "dependencies": {"next": "^15.0.0", "react": "^19.0.0", "react-dom": "^19.0.0"},
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(
        takyon_core,
        "_javascript_package_manager_command",
        lambda name: {"available": True, "name": "npm", "command": ["/usr/bin/npm"], "source": "test"},
    )
    monkeypatch.setattr(
        takyon_core,
        "_run_verification_command",
        lambda command, **kwargs: {"command": command, "status": "passed"},
    )

    verification = _verify_product_surface_path(business_root, "product/site", install=True)

    assert verification["status"] == "passed"
    assert verification["kind"] == "node_build"
    assert [check["command"] for check in verification["checks"]] == [
        ["/usr/bin/npm", "install", "--ignore-scripts"],
        ["/usr/bin/npm", "run", "build"],
    ]


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


def test_legacy_cap_usd_budget_alias_is_honored(tmp_path):
    store = TakyonStore(tmp_path)
    _commit(
        store,
        "business:latexflow",
        [
            {
                "action": "business.upsert",
                "business": "latexflow",
                "name": "Latexflow",
                "budget": {"cap_usd": 10, "currency": "usd"},
            }
        ],
        "init-legacy-budget",
    )

    _commit(
        store,
        "business:latexflow",
        [{"action": "ledger.allocate", "amount": 7, "purpose": "test"}],
        "alloc-7-legacy",
    )

    with pytest.raises(TakyonError, match="exceed budget"):
        _commit(
            store,
            "business:latexflow",
            [{"action": "ledger.allocate", "amount": 4, "purpose": "too much"}],
            "alloc-4-legacy",
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
    receipt_files = store.read(scope="business:longer", query="list_files", path="metrics/receipts")["files"]
    assert "metrics/receipts/outreach" in {item["path"] for item in receipt_files}


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

    assert result["results"][0]["path"] == "research/index.md"


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
    monkeypatch.setenv("TAKYON_PRODUCT_SITE_ROOT", str(tmp_path / "published-sites"))
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
    published = tmp_path / "published-sites" / "latexflow"
    published.mkdir(parents=True)
    (published / "index.html").write_text("<h1>Latexflow</h1>\n", encoding="utf-8")
    cron_jobs.create_job(
        prompt="CEO wakeup for business:latexflow.",
        schedule="every 1h",
        name="takyon-ceo:latexflow",
        skills=[],
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
    assert deletion["published_site"]["removed"] is True
    assert not (tmp_path / "businesses" / "latexflow").exists()
    assert not published.exists()
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
    corpus = tmp_path / "businesses" / "latexflow" / "metrics" / "conversations" / "corpus" / "messages.jsonl"
    events = tmp_path / "businesses" / "latexflow" / "metrics" / "conversations" / "corpus" / "events.jsonl"
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


def test_conversation_read_tools_return_filtered_backlog_and_thread(tmp_path, monkeypatch):
    monkeypatch.setenv("TAKYON_HOME", str(tmp_path))
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
                "source": "reddit",
                "thread_external_id": "post-9",
                "thread_title": "Launch feedback",
                "external_id": "comment-9",
                "direction": "inbound",
                "author_label": "founder",
                "body": "This looks promising but I need pricing.",
            },
            {
                "action": "conversation.message.record",
                "business": "latexflow",
                "source": "reddit",
                "thread_external_id": "post-9",
                "thread_title": "Launch feedback",
                "external_id": "reply-9",
                "direction": "outbound",
                "author_label": "Takyon",
                "body": "Thanks for the note.",
                "status": "responded",
            },
        ],
        "conversation",
    )

    backlog = json.loads(handle_business_list_conversation_messages({"business": "latexflow"}))
    assert backlog["success"] is True
    assert len(backlog["messages"]) == 1
    assert backlog["messages"][0]["direction"] == "inbound"
    assert backlog["messages"][0]["thread_file"].startswith("metrics/conversations/reddit/")

    thread = json.loads(
        handle_business_read_conversation_thread(
            {"business": "latexflow", "thread_id": backlog["messages"][0]["thread_id"]}
        )
    )
    assert thread["success"] is True
    assert thread["file"].startswith("metrics/conversations/reddit/")
    assert [message["direction"] for message in thread["messages"]] == ["inbound", "outbound"]


def test_business_ugc_ad_write_records_existing_publication(tmp_path, monkeypatch):
    monkeypatch.setenv("TAKYON_HOME", str(tmp_path))
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
    publication_dir = tmp_path / "businesses" / "clipbook" / "product" / "ugc-ads" / "clipbook-demo"
    publication_dir.mkdir(parents=True, exist_ok=True)
    (publication_dir / "ad.mp4").write_bytes(b"fake mp4 bytes")
    (publication_dir / "reference.png").write_bytes(b"fake png bytes")
    script = {"dialogue_action": [{"dialogue": "UGC test", "action": "holding the product"}]}
    (publication_dir / "script.json").write_text(json.dumps(script), encoding="utf-8")

    result = json.loads(
        handle_business_ugc_ad_write(
            {
                "business": "clipbook",
                "value": {
                    "slug": "clipbook-demo",
                    "path": "product/ugc-ads/clipbook-demo/ad.mp4",
                    "seconds": 12.4,
                    "n_clips": 2,
                    "script": script,
                },
                "idempotency_key": "clipbook-ugc-ad-write",
            }
        )
    )

    assert result["success"] is True
    assert result["path"] == "product/ugc-ads/clipbook-demo/ad.mp4"
    assert result["publication_dir"] == "product/ugc-ads/clipbook-demo"
    assert result["files"] == [
        "product/ugc-ads/clipbook-demo/ad.mp4",
        "product/ugc-ads/clipbook-demo/script.json",
        "product/ugc-ads/clipbook-demo/reference.png",
    ]
    with store._connect() as conn:
        row = conn.execute(
            "SELECT event_type, payload_json FROM events WHERE business_slug = ? ORDER BY created_at DESC LIMIT 1",
            ("clipbook",),
        ).fetchone()
    assert row["event_type"] == "ugc_ad.write"
    payload = json.loads(row["payload_json"])
    assert payload["path"] == "product/ugc-ads/clipbook-demo/ad.mp4"
    assert payload["script"] == script


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
