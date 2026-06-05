"""Tests for the Takyon CEO operator plugin."""

from __future__ import annotations

import json
import os
import types
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from gateway.session_context import clear_session_vars, set_session_vars
from plugins.takyon import business_credits as takyon_business_credits
from plugins.takyon import core as takyon_core
from plugins.takyon import storage
from plugins.takyon.stripe_util import build_signature_header
from plugins.takyon.core import (
    TAKYON_TOOL_DEFINITIONS,
    TakyonError,
    TakyonStore,
    _API_ENV_ALIASES,
    _bounded_product_inventory,
    _canonical_runtime_features_for_surface_shape,
    _canonicalize_business_product_links,
    _product_publish_target,
    _scan_for_pretend_product_state,
    _refresh_product_surface_path,
    _test_app_checkout_url,
    _surface_subuser_app_shape,
    _surface_customer_experience_shape,
    _meta_load_launch_receipt,
    _validate_product_surface_contract,
    handle_business_check_runtime_capabilities,
    handle_business_create_app_checkout,
    handle_business_delete_business,
    handle_business_list_businesses,
    handle_business_meta_ad_control,
    handle_business_meta_ad_insights_sync,
    handle_business_meta_ad_bind_manual_launch,
    handle_business_write_file,
    handle_business_meta_ad_launch,
    handle_business_reddit_ad_control,
    handle_business_reddit_ad_insights_sync,
    handle_business_reddit_ad_launch,
    handle_business_publish_outreach,
    handle_business_read_app_account,
    handle_business_request_app_magic_link,
    handle_business_record_stripe_webhook,
    handle_business_verify_app_magic_link,
    handle_business_static_ad_generate,
    handle_business_ugc_ad_generate,
    handle_business_ugc_ad_write,
    handle_business_claude_agent_task,
    handle_business_set_work_focus,
    handle_business_list_conversation_messages,
    handle_business_read_conversation_thread,
    handle_business_upsert_app_surface_contract,
    handle_business_upsert_business,
    takyon_toolset_name,
    handle_business_refresh_product_surface,
)


class _FakePluginContext:
    def __init__(self):
        self.tools = []
        self.tool_defs = []
        self.skills = []
        self.commands = []
        self.slash_commands = {}
        self.injected = []

    def register_tool(self, **kwargs):
        self.tools.append(kwargs["name"])
        self.tool_defs.append(dict(kwargs))

    def register_skill(self, **kwargs):
        self.skills.append(kwargs["name"])

    def register_cli_command(self, **kwargs):
        self.commands.append(kwargs["name"])

    def register_command(self, name, handler, **kwargs):
        self.slash_commands[name] = {"handler": handler, **kwargs}

    def inject_message(self, content, role="user"):
        self.injected.append((role, content))
        return True


@pytest.fixture(autouse=True)
def _isolated_takyon_pg_env(monkeypatch, tmp_path, pg_store_dsn):
    monkeypatch.setenv("DATABASE_URL", pg_store_dsn)
    monkeypatch.setenv("TAKYON_PLATFORM_OWNER_SUB", "auth0|takyon-plugin-tests")
    user_id, _ = TakyonStore(root=tmp_path, database_url=pg_store_dsn).seed_platform_owner()
    import psycopg

    from plugins.takyon import billing

    with psycopg.connect(pg_store_dsn, autocommit=True) as conn:
        billing.topup(conn, user_id, 50_000, "takyon-plugin-tests-topup")


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


def test_plugin_registers_authority_tools_on_separate_toolset():
    import plugins.takyon as takyon

    ctx = _FakePluginContext()
    takyon.register(ctx)
    toolsets = {item["name"]: item["toolset"] for item in ctx.tool_defs}

    assert toolsets["business_write_file"] == "takyon"
    assert toolsets["business_publish_outreach"] == "takyon"
    assert toolsets["business_ugc_ad_generate"] == "takyon-authority"
    assert toolsets["business_static_ad_generate"] == "takyon-authority"
    assert toolsets["business_meta_ad_launch"] == "takyon-authority"
    assert toolsets["business_meta_ad_bind_manual_launch"] == "takyon-authority"
    assert toolsets["business_meta_ad_control"] == "takyon-authority"
    assert toolsets["business_meta_ad_insights_sync"] == "takyon-authority"
    assert toolsets["business_reddit_ad_launch"] == "takyon-authority"
    assert toolsets["business_reddit_ad_control"] == "takyon-authority"
    assert toolsets["business_reddit_ad_insights_sync"] == "takyon-authority"
    assert toolsets["business_refresh_product_surface"] == "takyon-authority"
    assert toolsets["business_check_runtime_capabilities"] == "takyon-authority"
    assert takyon_toolset_name("business_gc") == "takyon-authority"
    assert takyon_toolset_name("business_record_event") == "takyon"


def test_bundled_takyon_skills_exist():
    skills_root = Path(__file__).resolve().parents[2] / "skills" / "takyon"
    skill_files = {path.parent.name: path for path in skills_root.glob("*/SKILL.md")}
    assert set(skill_files) == {
        "autonomous-seo-geo-operator",
        "static-ad-creative-generator",
        "ugc-video-ad",
        "takyon-app-runtime",
        "takyon-build-product",
        "takyon-business-metrics",
        "takyon-claude-agent-sdk",
        "takyon-conversation-followup",
        "takyon-distribution",
        "takyon-market-research",
        "takyon-meta-ads",
        "takyon-reddit-ads",
        "takyon-x",
    }
    for path in skill_files.values():
        text = path.read_text(encoding="utf-8")
        assert text.startswith("---\nname:")


def test_bootstrap_prompt_expands_strategy_then_builds_then_posts_on_x():
    from plugins.takyon.cli import _business_bootstrap_instruction

    prompt = _business_bootstrap_instruction("demo", "find users", "test")

    assert "Read research/strategy.md and add to it" in prompt
    assert "Build the first real product/site page or app page people can actually see or use." in prompt
    assert "Draft or publish one top-level X post." in prompt


def test_bootstrap_prompt_uses_business_publish_outreach_for_the_x_move_in_test_mode():
    from plugins.takyon.cli import _business_bootstrap_instruction

    prompt = _business_bootstrap_instruction("demo", "find users", "test")

    assert "For the X move, call business_publish_outreach." in prompt
    assert "distribution/local-published/" in prompt
    assert "3 evidence-backed lanes" not in prompt
    assert "6 total" not in prompt


def test_bootstrap_prompt_treats_fresh_create_as_greenfield_and_research_bounded():
    from plugins.takyon.cli import _business_bootstrap_instruction

    prompt = _business_bootstrap_instruction("demo", "find users", "test")

    assert "assume metrics and most business workspaces may be empty" in prompt
    assert "If relevant durable assets already exist, advance them instead of recreating them." in prompt
    assert "Do only enough research to keep strategy, product, and X claims truthful." in prompt


def test_bootstrap_prompt_orders_strategy_before_build_before_x():
    from plugins.takyon.cli import _business_bootstrap_instruction

    prompt = _business_bootstrap_instruction("demo", "find users", "test")

    strategy_index = prompt.index("1. Read research/strategy.md and add to it so it becomes a short working strategy, not just the seeded goal.")
    build_index = prompt.index("2. Build the first real product/site page or app page people can actually see or use.")
    x_index = prompt.index("3. Draft or publish one top-level X post.")

    assert strategy_index < build_index < x_index


def test_bootstrap_prompt_keeps_truth_and_blocker_language():
    from plugins.takyon.cli import _business_bootstrap_instruction

    prompt = _business_bootstrap_instruction("demo", "find users", "test")

    assert "Keep work business-scoped and truthful." in prompt
    assert "If something is blocked, record the blocker and continue with local/test artifacts that do not require that provider." in prompt


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


def test_runtime_mirror_files_stay_absent_by_default_even_after_real_public_surface_exists(tmp_path, monkeypatch):
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
    assert (product_root / "surface.md").exists()
    assert not (product_root / "runtime.md").exists()
    assert not (product_root / "plans.md").exists()
    assert not (product_root / "customers.md").exists()
    assert not (product_root / "billing.md").exists()
    assert not (product_root / "usage.md").exists()


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


def test_business_read_file_truth_metadata_marks_commentary_vs_authoritative(tmp_path):
    store = TakyonStore(tmp_path)
    _commit(
        store,
        "business:probe",
        [{"action": "business.upsert", "business": "probe", "name": "Probe"}],
        "init-probe-read-meta",
    )
    business_root = tmp_path / "businesses" / "probe"
    (business_root / "product").mkdir(parents=True, exist_ok=True)
    (business_root / "product" / "surface.md").write_text("# Surface\n", encoding="utf-8")
    (business_root / "product" / "site").mkdir(parents=True, exist_ok=True)
    (business_root / "product" / "site" / "index.html").write_text("<h1>Probe</h1>\n", encoding="utf-8")
    (business_root / "metrics" / "receipts").mkdir(parents=True, exist_ok=True)
    (business_root / "metrics" / "receipts" / "publish.json").write_text("{\"ok\":true}\n", encoding="utf-8")

    summary = store.read(scope="business:probe", query="read_file", path="product/surface.md")
    source = store.read(scope="business:probe", query="read_file", path="product/site/index.html")
    receipt = store.read(scope="business:probe", query="read_file", path="metrics/receipts/publish.json")

    assert summary["document_role"] == "summary"
    assert summary["proof_level"] == "commentary"
    assert "Do not use this file by itself as proof" in summary["proof_guidance"]

    assert source["document_role"] == "implementation_source"
    assert source["proof_level"] == "authoritative"
    assert "judge current website or app behavior and wiring" in source["proof_guidance"]

    assert receipt["document_role"] == "receipt"
    assert receipt["proof_level"] == "authoritative"
    assert "Machine-generated receipt" in receipt["proof_guidance"]


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


def test_active_surface_requires_product_refresh_receipt(tmp_path, monkeypatch):
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
        handle_business_refresh_product_surface(
            {
                "business": "latexflow",
                "source_path": "product/site",
                "install": False,
                "idempotency_key": "verify-static-site",
            }
        )
    )

    assert verification["success"] is True
    assert verification["surface_refresh"]["status"] == "passed"
    assert verification["surface_refresh"]["inventory"]["status"] == "collected"
    assert verification["surface_refresh"]["inventory"]["routes"] == ["/"]
    app = store.read(scope="business:latexflow", query="summary", include=["app"])["app"]
    assert app["surface_contract"]["status"] == "active"
    assert app["surface_contract"]["publish_status"] == "published"
    assert app["surface_contract"]["public_url"] == "https://latexflow.fourmanifold.com/"
    assert app["product_inventory"]["routes"] == ["/"]
    assert app["product_surface"]["local_continuable_work"] == []
    assert (tmp_path / "published-sites" / "latexflow" / "index.html").exists()
    assert app["surface_contract"]["publish_receipt_path"]
    assert app["surface_contract"]["routes"] == ["/"]
    pulse = store.calculate_pulse("latexflow")
    assert pulse["current_state"]["product_surface"]["inventory_status"] == "collected"
    assert pulse["summary"]["local_continuable_product_work"] == 0


def test_product_surface_projection_turns_stale_when_source_changes_after_publish(tmp_path, monkeypatch):
    monkeypatch.setenv("TAKYON_HOME", str(tmp_path))
    monkeypatch.setenv("TAKYON_PRODUCT_SITE_ROOT", str(tmp_path / "published-sites"))
    store = TakyonStore(tmp_path)
    _commit(
        store,
        "business:latexflow",
        [{"action": "business.upsert", "business": "latexflow", "name": "Latexflow", "budget": {"amount": 25}}],
        "init-stale-surface",
    )
    _commit(
        store,
        "business:latexflow",
        [{"action": "app.surface.upsert", "business": "latexflow", "status": "active", "source_path": "product/site", "routes": ["/"]}],
        "surface-stale-surface",
    )
    site = tmp_path / "businesses" / "latexflow" / "product" / "site"
    site.mkdir(parents=True)
    index = site / "index.html"
    index.write_text("<h1>Latexflow</h1>\n", encoding="utf-8")
    handle_business_refresh_product_surface(
        {
            "business": "latexflow",
            "source_path": "product/site",
            "install": False,
            "idempotency_key": "verify-stale-surface",
        }
    )

    # Simulate a later source edit that bypassed the canonical verify/publish turn.
    index.write_text("<h1>Latexflow v2</h1>\n", encoding="utf-8")
    future_ts = datetime.now().timestamp() + 5
    os.utime(index, (future_ts, future_ts))

    app = store.read(scope="business:latexflow", query="summary", include=["app"])["app"]
    assert app["surface_contract"]["status"] == "unverified"
    assert app["surface_contract"]["publish_status"] == "published"
    assert any(
        "product source has not been published" not in item
        for item in app["product_surface"]["local_continuable_work"]
    )


def test_artifact_write_refreshes_surface_contract_mirror_when_product_source_changes(tmp_path, monkeypatch):
    monkeypatch.setenv("TAKYON_HOME", str(tmp_path))
    monkeypatch.setenv("TAKYON_PRODUCT_SITE_ROOT", str(tmp_path / "published-sites"))
    store = TakyonStore(tmp_path)
    _commit(
        store,
        "business:latexflow",
        [{"action": "business.upsert", "business": "latexflow", "name": "Latexflow", "budget": {"amount": 25}}],
        "init-refresh-surface-md",
    )
    _commit(
        store,
        "business:latexflow",
        [{"action": "app.surface.upsert", "business": "latexflow", "status": "active", "source_path": "product/site", "routes": ["/"]}],
        "surface-refresh-surface-md",
    )
    site = tmp_path / "businesses" / "latexflow" / "product" / "site"
    site.mkdir(parents=True)
    (site / "index.html").write_text("<h1>Latexflow</h1>\n", encoding="utf-8")
    handle_business_refresh_product_surface(
        {
            "business": "latexflow",
            "source_path": "product/site",
            "install": False,
            "idempotency_key": "verify-refresh-surface-md",
        }
    )

    _commit(
        store,
        "business:latexflow",
        [{"action": "artifact.write", "business": "latexflow", "path": "product/site/index.html", "content": "<h1>Latexflow v2</h1>\n"}],
        "mutate-product-source",
    )

    app = store.read(scope="business:latexflow", query="summary", include=["app"])["app"]
    surface_md = (tmp_path / "businesses" / "latexflow" / "product" / "surface.md").read_text(encoding="utf-8")
    assert "- Status: unverified" in surface_md
    assert "- Publish status: published" in surface_md
    assert app["surface_contract"]["status"] == "unverified"
    assert app["surface_contract"]["publish_status"] == "published"
    assert (tmp_path / "published-sites" / "latexflow" / "index.html").read_text(encoding="utf-8") == "<h1>Latexflow</h1>\n"


def test_artifact_write_leaves_new_product_site_unverified_until_explicit_refresh(tmp_path, monkeypatch):
    monkeypatch.setenv("TAKYON_HOME", str(tmp_path))
    monkeypatch.setenv("TAKYON_PRODUCT_SITE_ROOT", str(tmp_path / "published-sites"))
    store = TakyonStore(tmp_path)
    _commit(
        store,
        "business:sitecheck",
        [{"action": "business.upsert", "business": "sitecheck", "name": "SiteCheck", "budget": {"amount": 25}}],
        "init-auto-verify-site",
    )
    _commit(
        store,
        "business:sitecheck",
        [{"action": "app.surface.upsert", "business": "sitecheck", "status": "active", "source_path": "product/site", "routes": ["/"]}],
        "surface-auto-verify-site",
    )

    _commit(
        store,
        "business:sitecheck",
        [{"action": "artifact.write", "business": "sitecheck", "path": "product/site/index.html", "content": "<h1>SiteCheck</h1>\n"}],
        "write-site-index",
    )

    app = store.read(scope="business:sitecheck", query="summary", include=["app"])["app"]
    assert app["surface_contract"]["status"] == "unverified"
    assert app["surface_contract"]["publish_status"] == "not_published"
    assert app["surface_contract"]["public_url"] == ""
    assert "product source has not been published" in app["product_surface"]["local_continuable_work"]
    assert not (tmp_path / "published-sites" / "sitecheck" / "index.html").exists()
    receipt_dir = tmp_path / "businesses" / "sitecheck" / "metrics" / "receipts" / "product-surface"
    assert not receipt_dir.exists()


def test_surface_upsert_leaves_existing_product_site_unverified_until_explicit_refresh(tmp_path, monkeypatch):
    monkeypatch.setenv("TAKYON_HOME", str(tmp_path))
    monkeypatch.setenv("TAKYON_PRODUCT_SITE_ROOT", str(tmp_path / "published-sites"))
    store = TakyonStore(tmp_path)
    _commit(
        store,
        "business:siteafterwrite",
        [{"action": "business.upsert", "business": "siteafterwrite", "name": "SiteAfterWrite", "budget": {"amount": 25}}],
        "init-site-after-write",
    )
    _commit(
        store,
        "business:siteafterwrite",
        [{"action": "artifact.write", "business": "siteafterwrite", "path": "product/site/index.html", "content": "<h1>SiteAfterWrite</h1>\n"}],
        "write-site-before-surface",
    )
    _commit(
        store,
        "business:siteafterwrite",
        [{"action": "app.surface.upsert", "business": "siteafterwrite", "status": "active", "source_path": "product/site/index.html", "routes": ["/"]}],
        "surface-after-existing-site",
    )

    app = store.read(scope="business:siteafterwrite", query="summary", include=["app"])["app"]
    assert app["surface_contract"]["source_path"] == "product/site"
    assert app["surface_contract"]["status"] == "unverified"
    assert app["surface_contract"]["publish_status"] == "not_published"
    assert app["surface_contract"]["public_url"] == ""
    assert "product source has not been published" in app["product_surface"]["local_continuable_work"]
    assert not (tmp_path / "published-sites" / "siteafterwrite" / "index.html").exists()
    receipt_dir = tmp_path / "businesses" / "siteafterwrite" / "metrics" / "receipts" / "product-surface"
    assert not receipt_dir.exists()


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
        handle_business_refresh_product_surface(
            {
                "business": "briefpilot",
                "source_path": "product/site",
                "install": False,
                "idempotency_key": "verify-app-like-claim-only",
            }
        )
    )["surface_refresh"]

    assert verification["status"] == "blocked"
    assert verification["publish"]["status"] == "published"
    assert verification["publish"]["public_url"] == "https://briefpilot.fourmanifold.com/"
    assert "generated source does not include a working app subroute" in verification["blocker"]
    assert (tmp_path / "published-sites" / "briefpilot" / "index.html").exists()
    app = store.read(scope="business:briefpilot", query="summary", include=["app"])["app"]
    assert app["surface_contract"]["status"] == "active"
    assert app["surface_contract"]["publish_status"] == "published"
    assert app["surface_contract"]["public_url"] == "https://briefpilot.fourmanifold.com/"
    assert app["surface_contract"]["publish_blocker"]


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
        handle_business_refresh_product_surface(
            {
                "business": "briefpilot",
                "source_path": "product/site",
                "install": False,
                "idempotency_key": "verify-static-spa-app",
            }
        )
    )["surface_refresh"]

    assert verification["status"] == "passed"
    inventory = verification["inventory"]
    assert inventory["routes"] == ["/", "/app"]
    assert inventory["declared_routes"] == ["/app"]
    assert {"auth", "generate", "session"}.issubset(set(inventory["runtime_integrations"]))
    assert {"form", "input", "runtime_fetch"}.issubset(set(inventory["workflow_markers"]))
    assert (tmp_path / "published-sites" / "briefpilot" / "index.html").exists()
    assert (tmp_path / "published-sites" / "briefpilot" / "app" / "index.html").exists()


def test_next_app_surface_with_src_app_routes_passes(tmp_path, monkeypatch):
    monkeypatch.setenv("TAKYON_HOME", str(tmp_path))
    monkeypatch.setenv("TAKYON_PRODUCT_SITE_ROOT", str(tmp_path / "published-sites"))
    store = TakyonStore(tmp_path)
    _commit(
        store,
        "business:noteleaf",
        [{"action": "business.upsert", "business": "noteleaf", "name": "NoteLeaf"}],
        "init-next-src-app",
    )
    _commit(
        store,
        "business:noteleaf",
        [
            {
                "action": "app.surface.upsert",
                "business": "noteleaf",
                "status": "draft",
                "source_path": "product/site",
                "runtime_api_base": "/api/takyon/apps/noteleaf",
                "runtime_features": ["auth", "account", "checkout", "generate"],
                "routes": [
                    {"path": "/", "name": "Homepage"},
                    {"path": "/app", "name": "Workspace", "description": "Monthly compile workflow"},
                ],
                "metadata": {
                    "subuser_app": {
                        "app_mode": "ai_tool",
                        "subscription_style": "monthly",
                    }
                },
                "notes": "Monthly app shell.",
            }
        ],
        "surface-next-src-app",
    )
    site = tmp_path / "businesses" / "noteleaf" / "product" / "site"
    site.mkdir(parents=True)
    (site / "package.json").write_text(
        json.dumps(
            {
                "name": "noteleaf",
                "private": True,
                "dependencies": {"next": "14.2.3", "react": "18.3.1", "react-dom": "18.3.1"},
                "scripts": {"build": "next build"},
            }
        ),
        encoding="utf-8",
    )
    (site / "src" / "app" / "page.js").parent.mkdir(parents=True, exist_ok=True)
    (site / "src" / "app" / "page.js").write_text(
        """
        export default function HomePage() {
          return <a href="/app">Open workspace</a>;
        }
        """,
        encoding="utf-8",
    )
    (site / "src" / "app" / "app" / "page.js").parent.mkdir(parents=True, exist_ok=True)
    (site / "src" / "app" / "app" / "page.js").write_text(
        """
        export default function WorkspacePage() {
          return (
            <form>
              <input name="email" type="email" />
              <textarea name="prompt" />
            </form>
          );
        }
        """,
        encoding="utf-8",
    )
    (site / "src" / "app" / "layout.js").write_text(
        """
        export const metadata = {
          title: "NoteLeaf",
        };

        export default function RootLayout({ children }) {
          return (
            <html lang="en">
              <body>{children}</body>
            </html>
          );
        }
        """,
        encoding="utf-8",
    )
    (site / "src" / "app" / "globals.css").write_text("body { font-family: sans-serif; }\n", encoding="utf-8")
    (site / "src" / "lib").mkdir(parents=True, exist_ok=True)
    (site / "src" / "lib" / "runtime.js").write_text(
        """
        export async function pingRuntime() {
          await fetch('/api/takyon/apps/noteleaf/session');
          await fetch('/api/takyon/apps/noteleaf/auth/request', { method: 'POST' });
          await fetch('/api/takyon/apps/noteleaf/generate', { method: 'POST' });
        }
        """,
        encoding="utf-8",
    )

    verification = json.loads(
        handle_business_refresh_product_surface(
            {
                "business": "noteleaf",
                "source_path": "product/site",
                "install": False,
                "idempotency_key": "verify-next-src-app",
            }
        )
    )["surface_refresh"]

    assert verification["status"] == "passed"
    inventory = verification["inventory"]
    assert inventory["routes"] == ["/", "/app"]
    assert inventory["declared_routes"] == ["/app"]
    assert {"auth", "generate", "session"}.issubset(set(inventory["runtime_integrations"]))
    assert {"form", "input", "runtime_fetch"}.issubset(set(inventory["workflow_markers"]))


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
        handle_business_refresh_product_surface(
            {
                "business": "latexflow",
                "install": False,
                "idempotency_key": "verify-legacy-shared-renderer",
            }
        )
    )

    assert verification["success"] is True
    receipt = verification["surface_refresh"]
    assert receipt["status"] == "missing"
    assert receipt["effective_publish_policy"] == "publish_after_refresh"
    assert receipt["requested_publish_policy"] == "shared_renderer"
    assert receipt["publish"]["status"] == "blocked"
    assert receipt["publish"]["public_url"] == ""
    assert receipt["blocker"]
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
        handle_business_refresh_product_surface(
            {
                "business": "latexflow",
                "source_path": "product/site",
                "install": False,
                "idempotency_key": "verify-static-site-caddy",
            }
        )
    )["surface_refresh"]

    caddyfile = (tmp_path / "Caddyfile").read_text(encoding="utf-8")
    assert verification["publish"]["status"] == "published"
    assert verification["publish"]["caddyfile"] == str(tmp_path / "Caddyfile")
    assert "latexflow.fourmanifold.com" in caddyfile
    assert "@takyon_app_runtime path /api/*" in caddyfile
    assert "reverse_proxy 127.0.0.1:9119" in caddyfile
    assert f"root * {tmp_path / 'published-sites' / 'latexflow'}" in caddyfile
    assert "try_files {path} {path}/ /index.html" in caddyfile
    assert "file_server" in caddyfile
    assert ((tmp_path / "published-sites" / "latexflow" / "index.html").stat().st_mode & 0o777) == 0o644


def test_product_surface_refresh_detects_nested_workspace_prefix(tmp_path, monkeypatch):
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
        handle_business_refresh_product_surface(
            {
                "business": "latexflow",
                "source_path": "product/site",
                "install": False,
                "idempotency_key": "verify-nested-site",
            }
        )
    )

    assert verification["success"] is True
    assert verification["surface_refresh"]["status"] == "failed"
    assert "duplicate workspace prefix" in verification["surface_refresh"]["error"]


def test_business_refresh_product_surface_uses_longer_default_timeout(monkeypatch):
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
            "publish": {
                "status": "published",
                "public_url": "https://latexflow.fourmanifold.com/",
                "blocker": "",
            },
            "receipt_path": "metrics/receipts/product-surface/test.json",
            "inventory": {},
        }

    monkeypatch.setattr(takyon_core, "_store", lambda: _FakeStore())
    monkeypatch.setattr(takyon_core, "_finalize_product_surface_refresh", fake_finalize)
    monkeypatch.setattr(
        takyon_core,
        "_product_surface_refresh_operations",
        lambda **_: [{"action": "event.record", "business": "latexflow", "scope": "business:latexflow", "event_type": "test", "payload": {}}],
    )

    result = json.loads(
        handle_business_refresh_product_surface(
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


def test_business_refresh_product_surface_treats_null_install_as_default_true(monkeypatch):
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
            "publish": {
                "status": "published",
                "public_url": "https://latexflow.fourmanifold.com/",
                "blocker": "",
            },
            "receipt_path": "metrics/receipts/product-surface/test.json",
            "inventory": {},
        }

    monkeypatch.setattr(takyon_core, "_store", lambda: _FakeStore())
    monkeypatch.setattr(takyon_core, "_finalize_product_surface_refresh", fake_finalize)
    monkeypatch.setattr(
        takyon_core,
        "_product_surface_refresh_operations",
        lambda **_: [{"action": "event.record", "business": "latexflow", "scope": "business:latexflow", "event_type": "test", "payload": {}}],
    )

    result = json.loads(
        handle_business_refresh_product_surface(
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

    calls: list[dict[str, object]] = []

    def fake_run(command, *, input=None, **kwargs):
        calls.append({"command": list(command), "input": input})
        if len(command) > 1 and str(command[1]).endswith("takyon-claude-agent-task.mjs"):
            payload = json.loads(input or "{}")
            Path(payload["cwd"], "index.html").write_text("<h1>Latexflow</h1>\n", encoding="utf-8")
            return types.SimpleNamespace(returncode=0, stdout=json.dumps({"success": True, "summary": "ok"}), stderr="")
        return types.SimpleNamespace(returncode=0, stdout="v99.0.0\n", stderr="")

    monkeypatch.setattr(takyon_core, "_require_api_access", lambda *args, **kwargs: None)
    monkeypatch.setattr(takyon_core, "_should_run_claude_agent_in_docker", lambda _workspace_rel: False)
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

    payload = next(
        json.loads(str(call.get("input") or "{}"))
        for call in calls
        if len(call.get("command") or []) > 1
        and str((call.get("command") or [None, ""])[1]).endswith("takyon-claude-agent-task.mjs")
        and call.get("input")
    )
    instruction = payload["instruction"]
    assert result["success"] is True
    assert "current working directory is already the requested business workspace: product/site" in instruction
    assert "not `product/site/index.html`" in instruction
    assert captured["payload"]["root"] == captured["payload"]["cwd"]
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
    monkeypatch.setattr(takyon_core, "_should_run_claude_agent_in_docker", lambda _workspace_rel: False)
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
    assert "Supported Takyon build shapes: plain static source, Vite static app, Next static export, and Next service app." in instruction
    assert "If you use Next config, emit `next.config.js` or `next.config.mjs`, never `next.config.ts`." in instruction
    assert "This surface is app-like and must ship a real `/app` route in source." in instruction


def test_app_like_surface_defaults_required_routes_to_root_and_app():
    surface = {
        "metadata": {
            "subuser_app": {"app_mode": "ai_tool"},
            "customer_experience": {
                "required_sections": ["hero", "pricing"],
                "required_app_tabs": ["Translate", "History"],
            },
        }
    }

    shape = _surface_customer_experience_shape(surface)

    assert shape["required_routes"] == ["/", "/app"]
    assert shape["required_app_tabs"] == ["Translate", "History"]


def test_surface_subuser_app_shape_defaults_subscription_style_to_monthly():
    surface = {
        "runtime_features": ["generate"],
        "metadata": {
            "subuser_app": {"app_mode": "ai_tool"},
        },
    }

    shape = _surface_subuser_app_shape(surface)

    assert shape["subscription_style"] == "monthly"


def test_monthly_ai_tool_shape_canonicalizes_required_runtime_features():
    runtime_features = _canonical_runtime_features_for_surface_shape(
        ["generate"],
        app_mode="ai_tool",
        subscription_style="free_only",
        api_mode="none",
    )

    assert runtime_features == ["auth", "account", "checkout", "generate"]


def test_landing_only_surface_does_not_force_app_route():
    surface = {
        "landing_page_only": True,
        "metadata": {
            "subuser_app": {"app_mode": "ai_tool"},
            "customer_experience": {
                "required_routes": ["/"],
                "required_app_tabs": ["Translate"],
            },
        },
    }

    shape = _surface_customer_experience_shape(surface)

    assert shape["required_routes"] == ["/"]


def test_bootstrap_app_surface_seed_canonicalizes_minimal_monthly_site_shape(tmp_path, monkeypatch):
    monkeypatch.setenv("TAKYON_HOME", str(tmp_path))
    store = TakyonStore(tmp_path)
    _commit(
        store,
        "business:latexflow",
        [{"action": "business.upsert", "business": "latexflow", "name": "Latexflow", "budget": {"amount": 25}}],
        "init-bootstrap-shape",
    )

    result = json.loads(
        handle_business_upsert_app_surface_contract(
            {
                "business": "latexflow",
                "source_path": "product/app",
                "app_mode": "ai_tool",
                "subscription_style": "monthly",
                "runtime_features": ["generate"],
                "conversion_model": "self-serve signup -> free tier (5 docs) -> $9/mo paid plan",
                "required_routes": ["/", "/editor", "/documents", "/pricing", "/app"],
                "required_app_tabs": ["Documents", "Editor", "Share", "Account"],
                "notes": "Auth, checkout, and entitlements show as DEBUG/blocked states when Hermes rails are not wired.",
                "idempotency_key": "bootstrap-shape",
            }
        )
    )

    assert result["success"] is True
    app = store.read(scope="business:latexflow", query="summary", include=["app"])["app"]
    surface = app["surface_contract"]
    shape = _surface_customer_experience_shape(surface)

    assert surface["source_path"] == "product/site"
    assert shape["conversion_model"] == "monthly subscription"
    assert shape["required_routes"] == ["/", "/app"]
    assert shape["required_app_tabs"] == []
    assert surface["routes"] == [{"path": "/"}, {"path": "/app"}]
    assert "DEBUG/blocked" not in str(surface.get("notes") or "")
    assert app["plans"] == [
        {
            "plan_key": "monthly",
            "tier": "paid",
            "price_cents": 0,
            "currency": "usd",
            "billing_interval": "month",
            "included_ai_budget_microusd": 0,
            "included_action_quota": 25,
            "allow_overage": False,
            "stripe_product_id": None,
            "stripe_price_id": None,
            "metadata": {
                "takyon_seed": {
                    "kind": "monthly_app_shell",
                    "price_status": "unset",
                }
            },
        }
    ]


def test_ai_surface_without_auth_runtime_features_does_not_require_session_rails(tmp_path):
    site = tmp_path / "product" / "site"
    app = site / "app"
    app.mkdir(parents=True)
    (site / "index.html").write_text(
        "<main><a href=\"/app\">Open app</a></main>\n",
        encoding="utf-8",
    )
    (app / "index.html").write_text(
        """
        <form id="translate-form">
          <textarea name="prompt"></textarea>
          <button type="submit">Translate</button>
        </form>
        <script>
          async function run(prompt) {
            return fetch('/generate', {
              method: 'POST',
              headers: { 'content-type': 'application/json' },
              body: JSON.stringify({ prompt })
            });
          }
        </script>
        """,
        encoding="utf-8",
    )
    surface = {
        "runtime_features": ["generate"],
        "metadata": {
            "subuser_app": {"app_mode": "ai_tool"},
            "customer_experience": {
                "required_routes": ["/", "/app"],
                "required_app_tabs": ["Translate", "Preview"],
            },
        },
        "routes": [{"path": "/"}, {"path": "/app"}],
        "notes": "AI writing workspace with no saved accounts yet.",
    }

    inventory = _bounded_product_inventory(tmp_path, "product/site", surface=surface)
    ok, blocker = _validate_product_surface_contract(inventory, surface)

    assert ok is True
    assert blocker == ""


def test_runtime_generate_route_does_not_count_as_working_app_subroute(tmp_path):
    site = tmp_path / "product" / "site"
    site.mkdir(parents=True)
    (site / "index.html").write_text(
        """
        <main>
          <form>
            <textarea></textarea>
            <button>Generate</button>
          </form>
        </main>
        <script>
          fetch('/generate', { method: 'POST' });
        </script>
        """,
        encoding="utf-8",
    )
    surface = {
        "runtime_features": ["generate"],
        "metadata": {
            "subuser_app": {"app_mode": "ai_tool"},
            "customer_experience": {
                "required_routes": ["/", "/app"],
            },
        },
        "routes": [{"path": "/"}, {"path": "/app"}],
        "notes": "AI workspace.",
    }

    inventory = _bounded_product_inventory(tmp_path, "product/site", surface=surface)
    ok, blocker = _validate_product_surface_contract(inventory, surface)

    assert ok is False
    assert "/app" in blocker
    assert "/generate" not in blocker


def test_claude_agent_task_uses_broader_defaults_for_product_site_work(tmp_path, monkeypatch):
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
    monkeypatch.setattr(takyon_core, "_should_run_claude_agent_in_docker", lambda _workspace_rel: False)
    monkeypatch.setattr(takyon_core, "_resolve_runtime_executable", lambda name: "/usr/bin/node" if name == "node" else None)
    monkeypatch.setattr(takyon_core, "_ensure_repo_node_dependencies", lambda packages: {"success": True})
    monkeypatch.setattr(takyon_core.subprocess, "run", fake_run)

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
    assert payload["maxTurns"] == 16
    assert payload["timeoutMs"] == 300000
    assert payload["effort"] == "medium"


def test_claude_agent_task_no_longer_requires_legacy_business_budget(tmp_path, monkeypatch):
    monkeypatch.setenv("TAKYON_HOME", str(tmp_path))
    store = TakyonStore(tmp_path)
    _commit(
        store,
        "business:latexflow",
        [{"action": "business.upsert", "business": "latexflow", "name": "Latexflow"}],
        "init-no-legacy-budget",
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
                "idempotency_key": "workspace-no-legacy-budget",
                "install": False,
            }
        )
    )

    assert result["success"] is True
    assert result["operator_budget"]["source"] == "operator_billing"
    assert result["operator_budget"]["status"] == "settled_estimate"
    assert result["operator_budget"]["charged_cents"] == 200


def test_claude_agent_task_syncs_isolated_workspace_outputs_before_return(tmp_path, monkeypatch):
    monkeypatch.setenv("TAKYON_HOME", str(tmp_path))
    store = TakyonStore(tmp_path)
    _commit(
        store,
        "business:latexflow",
        [{"action": "business.upsert", "business": "latexflow", "name": "Latexflow"}],
        "init-isolated-sync",
    )

    scratch_home = tmp_path / "worker-home"
    tokens = set_session_vars(
        business_slug="latexflow",
        workspace_root=str(scratch_home),
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

    try:
        result = json.loads(
            handle_business_claude_agent_task(
                {
                    "business": "latexflow",
                    "workspace": "product/site",
                    "instruction": "Build the product surface under product/site.",
                    "idempotency_key": "workspace-isolated-sync",
                    "install": False,
                }
            )
        )
    finally:
        clear_session_vars(tokens)

    assert result["success"] is True
    assert result["operator_budget"]["status"] == "covered_by_session_budget"
    assert (tmp_path / "storage" / "latexflow" / "product" / "site" / "index.html").exists()
    content = TakyonStore(tmp_path).read(
        scope="business:latexflow",
        query="read_file",
        path="product/site/index.html",
    )["content"]
    assert "<h1>Latexflow</h1>" in content


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
    assert app["surface_contract"]["runtime_features"] == ["auth", "account", "checkout", "generate"]
    surface_md = (tmp_path / "businesses" / "latexflow" / "product" / "surface.md").read_text(encoding="utf-8")
    assert "Runtime features: auth, account, checkout, generate" in surface_md


def test_app_surface_contract_normalizes_legacy_billing_to_account_and_checkout(tmp_path, monkeypatch):
    monkeypatch.setenv("TAKYON_HOME", str(tmp_path))
    store = TakyonStore(tmp_path)
    _commit(
        store,
        "business:latexflow",
        [{"action": "business.upsert", "business": "latexflow", "name": "Latexflow", "budget": {"amount": 25}}],
        "init-billing-alias",
    )

    result = json.loads(
        handle_business_upsert_app_surface_contract(
            {
                "business": "latexflow",
                "source_path": "product/site",
                "runtime_features": ["billing"],
                "rail_state": {"billing": "blocked"},
                "idempotency_key": "surface-runtime-features-billing-alias",
            }
        )
    )

    assert result["success"] is True
    app = store.read(scope="business:latexflow", query="summary", include=["app"])["app"]
    assert app["surface_contract"]["runtime_features"] == ["auth", "account", "checkout"]
    shape = _surface_subuser_app_shape(app["surface_contract"])
    assert shape["rail_state"] == {"auth": "unknown", "account": "blocked", "checkout": "blocked"}


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
    monkeypatch.setattr(takyon_core, "_should_run_claude_agent_in_docker", lambda _workspace_rel: False)
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
    assert "Hermes sub-user app plane contract" in instruction
    assert "Declared runtime-backed features: auth, account, checkout, generate" in instruction
    assert "Runtime API base fallback: /api/takyon/apps/latexflow" in instruction
    assert "account (owner: takyon-app-runtime)" in instruction
    assert "checkout (owner: takyon-app-runtime)" in instruction
    assert "Canonical tools: business_read_app_account" in instruction
    assert "Canonical tools: business_create_app_checkout, business_record_stripe_webhook" in instruction
    assert "Reachable runtime endpoints: POST /auth/request on product hosts or POST /api/takyon/apps/latexflow/auth/request off-host" in instruction
    assert "Reachable runtime endpoints: GET /account on product hosts or GET /api/takyon/apps/latexflow/account off-host" in instruction
    assert "Reachable runtime endpoints: POST /checkout on product hosts or POST /api/takyon/apps/latexflow/checkout off-host" in instruction
    assert "Reachable runtime endpoints: POST /generate on product hosts or POST /api/takyon/apps/latexflow/generate off-host" in instruction
    assert "Treat POST /generate on product hosts or POST <runtime_api_base>/generate off-host as the public product contract for AI generation" in instruction
    assert "product code should not call providers or internal authority endpoints directly" in instruction
    assert "`tk_` top-level operator tokens never belong in product code" in instruction
    assert "`tkg_` is the app/business AI mediation boundary, not a customer login or session token" in instruction
    assert "Frontend-local, non-authoritative features that do not persist account/business truth and do not call provider or authority endpoints may be implemented without declaring a runtime rail." in instruction
    assert "Frontend-local, non-authoritative behavior may look live when it runs entirely in the browser" in instruction
    assert "`account` is the canonical paid-state read rail." in instruction
    assert "No app plans are configured yet. Do not render pricing cards" in instruction


def test_claude_agent_task_uses_docker_lane_for_product_site_when_terminal_env_is_docker(tmp_path, monkeypatch):
    monkeypatch.setenv("TAKYON_HOME", str(tmp_path))
    store = TakyonStore(tmp_path)
    _commit(
        store,
        "business:latexflow",
        [{"action": "business.upsert", "business": "latexflow", "name": "Latexflow", "budget": {"amount": 25}}],
        "init",
    )

    captured: dict[str, object] = {}

    def fake_docker_runner(*, payload, workspace_path, timeout_ms):
        captured["payload"] = payload
        captured["workspace_path"] = str(workspace_path)
        Path(workspace_path, "index.html").write_text("<h1>Latexflow</h1>\n", encoding="utf-8")
        return types.SimpleNamespace(returncode=0, stdout=json.dumps({"success": True, "summary": "ok"}), stderr="")

    monkeypatch.setattr(takyon_core, "_require_api_access", lambda *args, **kwargs: None)
    monkeypatch.setattr(takyon_core, "_ensure_repo_node_dependencies", lambda packages: {"success": True})
    monkeypatch.setattr(takyon_core, "_run_claude_agent_task_in_docker", fake_docker_runner)

    result = json.loads(
        handle_business_claude_agent_task(
            {
                "business": "latexflow",
                "workspace": "product/site",
                "instruction": "Build the product shell.",
                "idempotency_key": "workspace-docker-lane",
                "install": False,
            }
        )
    )

    assert result["success"] is True
    assert captured["payload"]["cwd"] == str(tmp_path / "businesses" / "latexflow" / "product" / "site")
    assert captured["payload"]["allowBash"] is True
    assert captured["workspace_path"].endswith("product/site")


def test_run_claude_agent_task_in_docker_passes_stdin_into_container(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True)
    repo_root = tmp_path / "repo"
    repo_root.mkdir(parents=True)

    captured: dict[str, object] = {}

    def fake_run(command, **kwargs):
        captured["command"] = list(command)
        captured["input"] = kwargs.get("input")
        return types.SimpleNamespace(returncode=0, stdout=json.dumps({"success": True, "summary": "ok"}), stderr="")

    from tools.environments import docker as docker_env

    monkeypatch.setattr(docker_env, "find_docker", lambda: "/usr/bin/docker")
    monkeypatch.setattr(docker_env, "_build_security_args", lambda run_as_host_user=False: ["--security-opt=test"])
    monkeypatch.setattr(takyon_core, "_repo_root", lambda: repo_root)
    monkeypatch.setattr(takyon_core, "_runtime_env", lambda extra=None: {"ANTHROPIC_API_KEY": "test-key", **(extra or {})})
    monkeypatch.setattr(takyon_core.subprocess, "run", fake_run)

    result = takyon_core._run_claude_agent_task_in_docker(
        payload={
            "business": "latexflow",
            "workspace": "product/site",
            "instruction": "Build the product shell.",
        },
        workspace_path=workspace,
        timeout_ms=30_000,
    )

    assert result.returncode == 0
    assert "-i" in captured["command"]
    payload = json.loads(str(captured["input"]))
    assert payload["instruction"] == "Build the product shell."
    assert payload["cwd"] == "/workspace"
    assert payload["root"] == "/workspace"


def test_surface_md_lists_selected_and_owned_runtime_rails(tmp_path, monkeypatch):
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
        "surface-runtime-rails",
    )

    surface_md = (tmp_path / "businesses" / "latexflow" / "product" / "surface.md").read_text(encoding="utf-8")
    assert "## Runtime Rails" in surface_md
    assert "- auth — owner: takyon-app-runtime" in surface_md
    assert "- account — owner: takyon-app-runtime" in surface_md
    assert "- checkout — owner: takyon-app-runtime" in surface_md
    assert "Canonical tools: business_read_app_account" in surface_md
    assert "Canonical tools: business_create_app_checkout, business_record_stripe_webhook" in surface_md


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
    assert result["surface_refresh"]["status"] == "passed"
    assert result["surface_refresh"]["publish"]["status"] == "published"
    assert (tmp_path / "published-sites" / "latexflow" / "index.html").exists()
    app = store.read(scope="business:latexflow", query="summary", include=["app"])["app"]
    assert app["surface_contract"]["status"] == "active"
    assert app["surface_contract"]["publish_status"] == "published"
    assert app["surface_contract"]["public_url"] == "https://latexflow.fourmanifold.com/"


def test_claude_agent_task_treats_null_install_as_default_true_for_surface_refresh(tmp_path, monkeypatch):
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
    monkeypatch.setattr(takyon_core, "_finalize_product_surface_refresh", fake_finalize)

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


def test_claude_agent_task_retries_once_on_local_surface_refresh_blocker(tmp_path, monkeypatch):
    monkeypatch.setenv("TAKYON_HOME", str(tmp_path))
    store = TakyonStore(tmp_path)
    _commit(
        store,
        "business:latexflow",
        [{"action": "business.upsert", "business": "latexflow", "name": "Latexflow", "budget": {"amount": 25}}],
        "init-repair-retry",
    )
    _commit(
        store,
        "business:latexflow",
        [
            {
                "action": "app.surface.upsert",
                "business": "latexflow",
                "source_path": "product/site",
                "app_mode": "standard_saas",
                "subscription_style": "monthly",
                "runtime_features": ["auth", "checkout"],
                "required_routes": ["/", "/app"],
            }
        ],
        "surface-repair-retry",
    )

    payloads: list[dict[str, object]] = []
    refresh_calls: list[str] = []

    def fake_run(command, *, input=None, **kwargs):
        if len(command) > 1 and str(command[1]).endswith("takyon-claude-agent-task.mjs"):
            payload = json.loads(input or "{}")
            payloads.append(payload)
            return types.SimpleNamespace(returncode=0, stdout=json.dumps({"success": True, "summary": "ok"}), stderr="")
        return types.SimpleNamespace(returncode=0, stdout="v99.0.0\n", stderr="")

    def fake_finalize(**kwargs: object) -> dict[str, object]:
        refresh_calls.append(str(kwargs["receipt_path"]))
        if len(refresh_calls) == 1:
            blocker = "npm run build failed: Module not found: Can't resolve './globals.css'"
            return {
                "status": "failed",
                "source_path": "product/site",
                "checks": [
                    {
                        "status": "failed",
                        "command": ["npm", "run", "build"],
                        "stderr": "Module not found: Can't resolve './globals.css'",
                    }
                ],
                "publish": {
                    "status": "blocked",
                    "publish_target": "https://latexflow.fourmanifold.com/",
                    "publish_source_path": "product/site",
                    "blocker": blocker,
                },
                "inventory": {},
                "receipt_path": str(kwargs["receipt_path"]),
                "blocker": blocker,
            }
        return {
            "status": "passed",
            "source_path": "product/site",
            "checks": [],
            "publish": {
                "status": "published",
                "public_url": "https://latexflow.fourmanifold.com/",
                "publish_target": "https://latexflow.fourmanifold.com/",
                "publish_source_path": "product/site",
                "published_at": "2026-06-04T23:59:00+00:00",
                "blocker": "",
            },
            "inventory": {},
            "receipt_path": str(kwargs["receipt_path"]),
            "blocker": "",
        }

    monkeypatch.setattr(takyon_core, "_require_api_access", lambda *args, **kwargs: None)
    monkeypatch.setattr(takyon_core, "_should_run_claude_agent_in_docker", lambda _workspace_rel: False)
    monkeypatch.setattr(takyon_core, "_resolve_runtime_executable", lambda name: "/usr/bin/node" if name == "node" else None)
    monkeypatch.setattr(takyon_core, "_ensure_repo_node_dependencies", lambda packages: {"success": True})
    monkeypatch.setattr(takyon_core.subprocess, "run", fake_run)
    monkeypatch.setattr(takyon_core, "_finalize_product_surface_refresh", fake_finalize)

    result = json.loads(
        handle_business_claude_agent_task(
            {
                "business": "latexflow",
                "workspace": "product/site",
                "instruction": "Build the product surface under product/site.",
                "idempotency_key": "workspace-local-repair-retry",
            }
        )
    )

    assert result["success"] is True
    assert result["worker_attempts"] == 2
    assert len(result["local_repair_retries"]) == 1
    assert len(payloads) == 2
    assert len(refresh_calls) == 2
    assert "Hermes automatic local repair retry (2 of 2)" in str(payloads[1]["instruction"])
    assert "globals.css" in str(payloads[1]["instruction"])
    assert result["surface_refresh"]["publish"]["status"] == "published"


def test_product_surface_refresh_defaults_publish_root_to_takyon_home_product_sites(tmp_path, monkeypatch):
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
        handle_business_refresh_product_surface(
            {
                "business": "latexflow",
                "source_path": "product/site",
                "install": False,
                "idempotency_key": "verify-static-site-no-host-root",
            }
        )
    )

    assert verification["success"] is True
    assert verification["surface_refresh"]["status"] == "passed"
    assert verification["surface_refresh"]["publish"]["status"] == "published"
    assert verification["surface_refresh"]["publish"]["publish_root"] == str(tmp_path / "product-sites" / "latexflow")
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

    verification = _refresh_product_surface_path(business_root, "product/site", install=False)

    assert verification["status"] == "passed"
    assert verification["inventory"]["status"] == "collected"
    assert verification["inventory"]["files_skipped"] >= 1


def test_next_product_publish_uses_service_rail_without_static_index(tmp_path, monkeypatch):
    business_root = tmp_path / "businesses" / "latexflow"
    site = business_root / "product" / "site"
    site.mkdir(parents=True)
    (site / ".next").mkdir()
    (site / ".next" / "BUILD_ID").write_text("build-1\n", encoding="utf-8")
    next_bin = site / "node_modules" / "next" / "dist" / "bin"
    next_bin.mkdir(parents=True)
    (next_bin / "next").write_text("#!/usr/bin/env node\nconsole.log('next');\n", encoding="utf-8")
    (next_bin / "next").chmod(0o755)
    (site / "node_modules" / ".bin").mkdir(parents=True)
    (site / "node_modules" / ".bin" / "next").symlink_to("../next/dist/bin/next")
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
    monkeypatch.setenv("TAKYON_PRODUCT_SERVICE_ROOT", str(tmp_path / "product-services"))
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
    service_root = tmp_path / "product-services" / "latexflow"
    assert result["publish_root"] == str(service_root)
    assert (service_root / ".next" / "BUILD_ID").read_text(encoding="utf-8").strip() == "build-1"
    assert (service_root / "package.json").is_file()
    assert os.access(service_root / "node_modules" / ".bin" / "next", os.X_OK)
    assert (service_root / "node_modules" / ".bin" / "next").is_symlink()
    service = tmp_path / "systemd" / "takyon-product-latexflow.service"
    service_text = service.read_text(encoding="utf-8")
    assert "ExecStart=/usr/bin/npm run start -- -H 127.0.0.1 -p" in service_text
    assert "/bin/bash -lc" not in service_text
    assert str(service_root) in service_text
    caddyfile = (tmp_path / "Caddyfile").read_text(encoding="utf-8")
    assert "latexflow.fourmanifold.com" in caddyfile
    assert "@takyon_app_runtime path /api/* /auth/request /auth/verify /session /account /profile /checkout /usage /generate" in caddyfile
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

    verification = _refresh_product_surface_path(business_root, "product/site", install=True)

    assert verification["status"] == "passed"
    assert verification["kind"] == "static_source_present"
    assert verification["checks"] == []


def test_refresh_next_product_with_static_export_still_runs_build(tmp_path, monkeypatch):
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
        "_run_surface_command",
        lambda command, **kwargs: {"command": command, "status": "passed"},
    )

    verification = _refresh_product_surface_path(business_root, "product/site", install=True)

    assert verification["status"] == "passed"
    assert verification["kind"] == "node_build"
    assert [check["command"] for check in verification["checks"]] == [
        ["/usr/bin/npm", "install", "--ignore-scripts"],
        ["/usr/bin/npm", "run", "build"],
    ]


def test_refresh_normalizes_supported_next_config_typescript(tmp_path, monkeypatch):
    business_root = tmp_path / "businesses" / "latexflow"
    site = business_root / "product" / "site"
    site.mkdir(parents=True)
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
    (site / "next.config.ts").write_text(
        'import type { NextConfig } from "next";\nconst nextConfig: NextConfig = { reactStrictMode: true };\nexport default nextConfig;\n',
        encoding="utf-8",
    )

    def fake_run(command, **kwargs):
        if command[:2] == ["/usr/bin/node", "--check"]:
            return types.SimpleNamespace(returncode=0, stdout="", stderr="")
        raise AssertionError(f"unexpected subprocess.run command: {command}")

    monkeypatch.setattr(takyon_core, "_resolve_runtime_executable", lambda name: "/usr/bin/node" if name == "node" else None)
    monkeypatch.setattr(
        takyon_core,
        "_javascript_package_manager_command",
        lambda name: {"available": True, "name": "npm", "command": ["/usr/bin/npm"], "source": "test"},
    )
    monkeypatch.setattr(
        takyon_core,
        "_run_surface_command",
        lambda command, **kwargs: {"command": command, "status": "passed"},
    )
    monkeypatch.setattr(takyon_core.subprocess, "run", fake_run)

    verification = _refresh_product_surface_path(business_root, "product/site", install=True)

    assert verification["status"] == "passed"
    assert verification["repairs"]
    assert verification["repairs"][0]["to"] == "next.config.mjs"
    assert (site / "next.config.mjs").exists()
    assert not (site / "next.config.ts").exists()
    assert [check["command"] for check in verification["checks"]] == [
        ["/usr/bin/npm", "install", "--ignore-scripts"],
        ["/usr/bin/npm", "run", "build"],
    ]


def test_refresh_failure_surfaces_exact_build_blocker_without_publish_shadow(tmp_path, monkeypatch):
    monkeypatch.setenv("TAKYON_HOME", str(tmp_path))
    store = TakyonStore(tmp_path)
    _commit(
        store,
        "business:latexflow",
        [{"action": "business.upsert", "business": "latexflow", "name": "Latexflow"}],
        "init-build-blocker",
    )
    _commit(
        store,
        "business:latexflow",
        [{"action": "app.surface.upsert", "business": "latexflow", "status": "draft", "source_path": "product/site", "routes": ["/"]}],
        "surface-build-blocker",
    )
    site = tmp_path / "businesses" / "latexflow" / "product" / "site"
    site.mkdir(parents=True)
    (site / "next.config.js").write_text("module.exports = {};\n", encoding="utf-8")
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

    def fake_run_surface_command(command, **kwargs):
        if command == ["/usr/bin/npm", "install", "--ignore-scripts"]:
            return {"command": command, "status": "passed", "stdout": "", "stderr": ""}
        if command == ["/usr/bin/npm", "run", "build"]:
            return {
                "command": command,
                "status": "failed",
                "stdout": "",
                "stderr": "Configuring Next.js via 'next.config.ts' is not supported.",
            }
        raise AssertionError(f"unexpected surface command: {command}")

    monkeypatch.setattr(
        takyon_core,
        "_javascript_package_manager_command",
        lambda name: {"available": True, "name": "npm", "command": ["/usr/bin/npm"], "source": "test"},
    )
    monkeypatch.setattr(takyon_core, "_run_surface_command", fake_run_surface_command)

    verification = json.loads(
        handle_business_refresh_product_surface(
            {
                "business": "latexflow",
                "source_path": "product/site",
                "install": True,
                "idempotency_key": "verify-build-blocker",
            }
        )
    )["surface_refresh"]

    assert verification["status"] == "failed"
    assert verification["publish"]["status"] == "blocked"
    assert "npm run build failed: Configuring Next.js via 'next.config.ts' is not supported." in verification["blocker"]
    assert "static publish directory" not in verification["blocker"]


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


def test_pg_magic_link_verify_creates_session_and_free_entitlement(tmp_path, monkeypatch):
    monkeypatch.setenv("TAKYON_HOME", str(tmp_path))
    store = TakyonStore(tmp_path)
    _commit(
        store,
        "business:authrail",
        [{"action": "business.upsert", "business": "authrail", "name": "Authrail", "mode": "test"}],
        "init-authrail",
    )

    request = json.loads(
        handle_business_request_app_magic_link(
            {
                "business": "authrail",
                "email": "tester@example.com",
                "name": "Test User",
                "origin": "https://authrail.example.com",
                "send_email": False,
            }
        )
    )
    verify = json.loads(
        handle_business_verify_app_magic_link(
            {
                "business": "authrail",
                "token": request["token"],
            }
        )
    )
    account = json.loads(
        handle_business_read_app_account(
            {
                "business": "authrail",
                "session_token": verify["session_token"],
            }
        )
    )

    assert verify["success"] is True
    assert verify["session_token"]
    assert account["success"] is True
    assert account["user"]["email"] == "tester@example.com"
    assert any(ent["tier"] == "free" and ent["status"] == "active" for ent in account["entitlements"])


def test_pg_test_mode_checkout_creates_intent_on_postgres(tmp_path, monkeypatch):
    monkeypatch.setenv("TAKYON_HOME", str(tmp_path))
    store = TakyonStore(tmp_path)
    _commit(
        store,
        "business:checkoutrail",
        [{"action": "business.upsert", "business": "checkoutrail", "name": "Checkoutrail", "mode": "test"}],
        "init-checkoutrail",
    )
    _commit(
        store,
        "business:checkoutrail",
        [
            {
                "action": "app.plan.upsert",
                "business": "checkoutrail",
                "plan_key": "pro_monthly",
                "tier": "pro",
                "price_cents": 1900,
                "currency": "usd",
                "billing_interval": "month",
            }
        ],
        "init-checkoutrail-plan",
    )

    checkout = json.loads(
        handle_business_create_app_checkout(
            {
                "business": "checkoutrail",
                "plan_key": "pro_monthly",
                "customer_email": "tester@example.com",
                "success_url": "https://example.test/success",
                "cancel_url": "https://example.test/cancel",
            }
        )
    )

    assert checkout["success"] is True
    assert checkout["mode"] == "test"
    assert checkout["checkout_url"].startswith("local://takyon/checkout/checkoutrail/")


def test_test_app_checkout_url_prefers_same_origin_http_url():
    assert _test_app_checkout_url(
        business="checkoutrailweb",
        intent_id="abc123",
        origin="https://checkoutrailweb.example.com",
    ) == (
        "https://checkoutrailweb.example.com/api/takyon/apps/checkoutrailweb/checkout"
        "?checkout_intent_id=abc123&mode=test"
    )
    assert _test_app_checkout_url(
        business="checkoutrailweb",
        intent_id="abc123",
        origin="not-a-url",
    ) == "local://takyon/checkout/checkoutrailweb/abc123"


def test_pg_test_mode_checkout_uses_same_origin_receipt_url_when_origin_present(tmp_path, monkeypatch):
    monkeypatch.setenv("TAKYON_HOME", str(tmp_path))
    store = TakyonStore(tmp_path)
    _commit(
        store,
        "business:checkoutrailweb",
        [{"action": "business.upsert", "business": "checkoutrailweb", "name": "Checkoutrailweb", "mode": "test"}],
        "init-checkoutrailweb",
    )
    _commit(
        store,
        "business:checkoutrailweb",
        [
            {
                "action": "app.plan.upsert",
                "business": "checkoutrailweb",
                "plan_key": "monthly",
                "tier": "pro",
                "price_cents": 1400,
                "currency": "usd",
                "billing_interval": "month",
            }
        ],
        "init-checkoutrailweb-plan",
    )

    checkout = json.loads(
        handle_business_create_app_checkout(
            {
                "business": "checkoutrailweb",
                "plan_key": "monthly",
                "customer_email": "tester@example.com",
                "success_url": "https://checkoutrailweb.example.com/app?checkout=success",
                "cancel_url": "https://checkoutrailweb.example.com/pricing",
                "origin": "https://checkoutrailweb.example.com",
            }
        )
    )

    assert checkout["success"] is True
    assert checkout["mode"] == "test"
    assert checkout["checkout_url"] == (
        "https://checkoutrailweb.example.com/api/takyon/apps/checkoutrailweb/checkout"
        f"?checkout_intent_id={checkout['checkout_intent_id']}&mode=test"
    )


def test_pg_checkout_webhook_updates_account_to_paid(tmp_path, monkeypatch):
    monkeypatch.setenv("TAKYON_HOME", str(tmp_path))
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_test")
    store = TakyonStore(tmp_path)
    _commit(
        store,
        "business:paidacct",
        [{"action": "business.upsert", "business": "paidacct", "name": "Paidacct", "mode": "test"}],
        "init-paidacct",
    )
    _commit(
        store,
        "business:paidacct",
        [
            {
                "action": "app.plan.upsert",
                "business": "paidacct",
                "plan_key": "pro_yearly",
                "tier": "pro",
                "price_cents": 14900,
                "currency": "usd",
                "billing_interval": "year",
            }
        ],
        "init-paidacct-plan",
    )

    request = json.loads(
        handle_business_request_app_magic_link(
            {
                "business": "paidacct",
                "email": "paid@example.com",
                "name": "Paid User",
                "origin": "https://paidacct.example.com",
                "send_email": False,
            }
        )
    )
    verify = json.loads(
        handle_business_verify_app_magic_link(
            {
                "business": "paidacct",
                "token": request["token"],
            }
        )
    )
    account_before = json.loads(
        handle_business_read_app_account(
            {
                "business": "paidacct",
                "session_token": verify["session_token"],
            }
        )
    )
    checkout = json.loads(
        handle_business_create_app_checkout(
            {
                "business": "paidacct",
                "plan_key": "pro_yearly",
                "app_user_id": account_before["user"]["id"],
                "customer_email": "paid@example.com",
                "success_url": "https://example.test/success",
                "cancel_url": "https://example.test/cancel",
            }
        )
    )

    event = {
        "id": "evt_paidacct",
        "type": "checkout.session.completed",
        "created": 1_700_000_000,
        "data": {
            "object": {
                "id": "cs_paidacct",
                "object": "checkout.session",
                "mode": "subscription",
                "payment_status": "paid",
                "status": "complete",
                "currency": "usd",
                "amount_subtotal": 14900,
                "amount_total": 14900,
                "customer": "cus_paidacct",
                "subscription": "sub_paidacct",
                "customer_details": {"email": "paid@example.com"},
                "customer_email": "paid@example.com",
                "client_reference_id": checkout["client_reference_id"],
                "metadata": {"checkout_intent_id": checkout["checkout_intent_id"]},
            }
        },
    }
    raw_event = json.dumps(event)
    webhook = json.loads(
        handle_business_record_stripe_webhook(
            {
                "raw_body": raw_event,
                "stripe_signature": build_signature_header(raw_event, "whsec_test"),
            }
        )
    )
    account_after = json.loads(
        handle_business_read_app_account(
            {
                "business": "paidacct",
                "session_token": verify["session_token"],
            }
        )
    )

    assert webhook["success"] is True
    assert webhook["type"] == "checkout.session.completed"
    assert webhook["processed"]["recorded"] is True
    assert account_after["user"]["tier"] == "paid"
    assert account_after["revenue"]["amount_paid_cents"] == 14900
    assert any(
        ent["tier"] == "paid"
        and ent["status"] == "active"
        and ent["plan_key"] == "pro_yearly"
        and ent["stripe_subscription_id"] == "sub_paidacct"
        for ent in account_after["entitlements"]
    )


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


def test_delete_business_removes_remote_workspace_copy(tmp_path):
    store = TakyonStore(tmp_path)
    _commit(
        store,
        "business:latexflow",
        [{"action": "business.upsert", "business": "latexflow", "name": "Latexflow"}],
        "init-delete-remote",
    )
    _commit(
        store,
        "business:latexflow",
        [{"action": "artifact.write", "path": "research/spec.md", "content": "# Spec\n"}],
        "write-delete-remote",
    )

    backend = storage.LocalStorageBackend(tmp_path / "storage")
    resumed = tmp_path / "resumed-before-delete"
    storage.sync_down(backend, "latexflow", resumed)
    assert (resumed / "research" / "spec.md").read_text() == "# Spec\n"

    _commit(
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
        "delete-remote",
    )

    emptied = tmp_path / "resumed-after-delete"
    report = storage.sync_down(backend, "latexflow", emptied)
    assert report.downloaded == ()
    assert not (emptied / "research" / "spec.md").exists()


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


def test_business_upsert_respects_configured_reserved_public_subdomains(tmp_path, monkeypatch):
    monkeypatch.setenv("TAKYON_HOME", str(tmp_path))
    (tmp_path / "config.yaml").write_text(
        "dashboard:\n  reserved_public_subdomains:\n    - sai\n",
        encoding="utf-8",
    )

    blocked = json.loads(
        handle_business_upsert_business(
            {
                "business": "sai",
                "name": "SAI",
                "idempotency_key": "handler-reserved-sai",
            }
        )
    )
    assert blocked["success"] is False
    assert "reserved for Four Manifold infrastructure" in blocked["error"]

    allowed = json.loads(
        handle_business_upsert_business(
            {
                "business": "sai-lab",
                "name": "SAI",
                "idempotency_key": "handler-allowed-sai-name",
            }
        )
    )
    assert allowed["success"] is True
    assert allowed["slug"] == "sai-lab"


def test_business_session_binding_scopes_file_writes(tmp_path, monkeypatch):
    monkeypatch.setenv("TAKYON_HOME", str(tmp_path))
    store = TakyonStore(tmp_path)
    _commit(
        store,
        "business:alpha",
        [{"action": "business.upsert", "business": "alpha", "name": "Alpha"}],
        "init-alpha",
    )
    _commit(
        store,
        "business:beta",
        [{"action": "business.upsert", "business": "beta", "name": "Beta"}],
        "init-beta",
    )

    tokens = set_session_vars(business_slug="alpha")
    try:
        wrote = json.loads(
            handle_business_write_file(
                {
                    "path": "research/session-note.md",
                    "content": "alpha-only\n",
                    "idempotency_key": "write-alpha",
                }
            )
        )
        assert wrote["success"] is True
        assert (store._business_root("alpha") / "research" / "session-note.md").read_text(encoding="utf-8") == "alpha-only\n"

        blocked = json.loads(
            handle_business_write_file(
                {
                    "business": "beta",
                    "path": "research/session-note.md",
                    "content": "beta\n",
                    "idempotency_key": "write-beta",
                }
            )
        )
        assert blocked["success"] is False
        assert "bound to the current session" in str(blocked.get("error") or "")
        assert not (store._business_root("beta") / "research" / "session-note.md").exists()
    finally:
        clear_session_vars(tokens)


def test_store_rejects_noncanonical_business_output_paths(tmp_path):
    store = TakyonStore(tmp_path)
    _commit(
        store,
        "business:alpha",
        [{"action": "business.upsert", "business": "alpha", "name": "Alpha"}],
        "init-alpha",
    )

    with pytest.raises(TakyonError, match="must stay under one of"):
        _commit(
            store,
            "business:alpha",
            [{"action": "workspace.upsert", "path": "scratch"}],
            "bad-workspace",
        )

    with pytest.raises(TakyonError, match="must stay under one of"):
        _commit(
            store,
            "business:alpha",
            [{"action": "artifact.write", "path": "scratch/note.md", "content": "nope"}],
            "bad-artifact",
        )


def test_claude_agent_task_rejects_noncanonical_workspace(tmp_path, monkeypatch):
    monkeypatch.setenv("TAKYON_HOME", str(tmp_path))
    store = TakyonStore(tmp_path)
    _commit(
        store,
        "business:alpha",
        [{"action": "business.upsert", "business": "alpha", "name": "Alpha", "budget": {"amount": 25}}],
        "init-alpha",
    )

    result = json.loads(
        handle_business_claude_agent_task(
            {
                "business": "alpha",
                "workspace": "scratch",
                "instruction": "Write a file.",
                "idempotency_key": "bad-workspace",
            }
        )
    )
    assert result["success"] is False
    assert "must stay under one of" in str(result.get("error") or "")


def test_business_session_allows_claude_agent_surface_refresh(tmp_path, monkeypatch):
    monkeypatch.setenv("TAKYON_HOME", str(tmp_path))
    store = TakyonStore(tmp_path)
    _commit(
        store,
        "business:alpha",
        [{"action": "business.upsert", "business": "alpha", "name": "Alpha"}],
        "init-alpha",
    )

    def fake_run(command, *, input=None, **kwargs):
        if len(command) > 1 and str(command[1]).endswith("takyon-claude-agent-task.mjs"):
            payload = json.loads(input or "{}")
            Path(payload["cwd"], "index.html").write_text("<h1>Alpha</h1>\n", encoding="utf-8")
            return types.SimpleNamespace(returncode=0, stdout=json.dumps({"success": True, "summary": "ok"}), stderr="")
        return types.SimpleNamespace(returncode=0, stdout="v99.0.0\n", stderr="")

    captured: dict[str, object] = {}

    def fake_finalize(**kwargs: object) -> dict[str, object]:
        captured["source_path"] = kwargs["source_path"]
        return {
            "status": "passed",
            "publish": {
                "status": "published",
                "public_url": "https://alpha.fourmanifold.com/",
                "blocker": "",
            },
            "receipt_path": "metrics/receipts/product-surface/test.json",
            "inventory": {},
        }

    monkeypatch.setattr(takyon_core, "_require_api_access", lambda *args, **kwargs: None)
    monkeypatch.setattr(takyon_core, "_resolve_runtime_executable", lambda name: "/usr/bin/node" if name == "node" else None)
    monkeypatch.setattr(takyon_core, "_ensure_repo_node_dependencies", lambda packages: {"success": True})
    monkeypatch.setattr(takyon_core.subprocess, "run", fake_run)
    monkeypatch.setattr(takyon_core, "_finalize_product_surface_refresh", fake_finalize)

    tokens = set_session_vars(business_slug="alpha")
    try:
        result = json.loads(
            handle_business_claude_agent_task(
                {
                    "business": "alpha",
                    "workspace": "product/site",
                    "instruction": "Update the product surface.",
                    "refresh_surface": True,
                    "idempotency_key": "claude-refresh-surface",
                }
            )
        )
        assert result["success"] is True
        assert result["surface_refresh"]["status"] == "passed"
        assert result["surface_refresh"]["publish"]["status"] == "published"
        assert "verification" not in result
        assert captured["source_path"] == "product/site"
    finally:
        clear_session_vars(tokens)


def test_claude_agent_task_records_failed_worker_run_on_prelaunch_gate(tmp_path, monkeypatch):
    monkeypatch.setenv("TAKYON_HOME", str(tmp_path))
    store = TakyonStore(tmp_path)
    _commit(
        store,
        "business:alpha",
        [{"action": "business.upsert", "business": "alpha", "name": "Alpha"}],
        "init-alpha-prelaunch-failure",
    )

    def fail_api_gate(*args, **kwargs):
        raise TakyonError("anthropic access missing")

    monkeypatch.setattr(takyon_core, "_require_api_access", fail_api_gate)

    result = json.loads(
        handle_business_claude_agent_task(
            {
                "business": "alpha",
                "workspace": "product/site",
                "instruction": "Build the first product surface.",
                "idempotency_key": "claude-prelaunch-failure",
            }
        )
    )

    assert result["success"] is False
    assert "anthropic access missing" in str(result.get("error") or "")

    with store._connect() as conn:
        row = conn.execute(
            "SELECT status, result_json, scope FROM agent_runs WHERE scope = ? ORDER BY updated_at DESC LIMIT 1",
            ("business:alpha/workspace:product/site",),
        ).fetchone()
    assert row is not None
    assert str(row[0]) == "failed"
    payload = json.loads(str(row[1] or "{}"))
    assert payload["source"] == "claude-agent-sdk"
    assert payload["workspace"] == "product/site"
    assert payload["error"] == "anthropic access missing"
    assert payload["surface_refresh"] is None
    assert "verification" not in payload


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


def test_business_static_ad_generate_test_mode_writes_mock_bundle(tmp_path, monkeypatch):
    monkeypatch.setenv("TAKYON_HOME", str(tmp_path))
    store = TakyonStore(tmp_path)
    _commit(
        store,
        "business:frameforge",
        [{"action": "business.upsert", "business": "frameforge", "name": "Frameforge", "mode": "test"}],
        "init-frameforge",
    )
    example_spec = (
        Path(__file__).resolve().parents[2]
        / "skills"
        / "takyon"
        / "static-ad-creative-generator"
        / "examples"
        / "example-spec.json"
    ).read_text(encoding="utf-8")
    spec_path = tmp_path / "businesses" / "frameforge" / "research" / "example-spec.json"
    spec_path.parent.mkdir(parents=True, exist_ok=True)
    spec_path.write_text(example_spec, encoding="utf-8")

    result = json.loads(
        handle_business_static_ad_generate(
            {
                "business": "frameforge",
                "input_path": "research/example-spec.json",
                "slug": "frameforge-static",
                "idempotency_key": "frameforge-static-test-v1",
            }
        )
    )

    assert result["success"] is True
    assert result["status"] == "suppressed_test_mode"
    manifest = tmp_path / "businesses" / "frameforge" / "product" / "static-ads" / "frameforge-static" / "manifest.json"
    receipt = tmp_path / "businesses" / "frameforge" / result["receipt"]
    assert manifest.is_file()
    assert receipt.is_file()


def test_business_static_ad_generate_live_charges_credits(tmp_path, monkeypatch):
    monkeypatch.setenv("TAKYON_HOME", str(tmp_path))
    store = TakyonStore(tmp_path)
    _commit(
        store,
        "business:frameforge",
        [{"action": "business.upsert", "business": "frameforge", "name": "Frameforge", "mode": "live"}],
        "init-frameforge-live",
    )
    _grant_creative_credits(store, "frameforge", 10, "frameforge-grant")
    spec_path = tmp_path / "businesses" / "frameforge" / "research" / "example-spec.json"
    spec_path.parent.mkdir(parents=True, exist_ok=True)
    spec_path.write_text(
        (
            Path(__file__).resolve().parents[2]
            / "skills"
            / "takyon"
            / "static-ad-creative-generator"
            / "examples"
            / "example-spec.json"
        ).read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        takyon_core,
        "_call_creative_runtime_gateway",
        lambda endpoint, payload: {
            "success": True,
            "status": "created",
            "manifest": "product/static-ads/frameforge-static-live/manifest.json",
            "succeeded": 1,
            "failed": 0,
            "credits_charged": 2,
            "balance_credits": 8,
            "reserved_credits": 0,
        },
    )

    result = json.loads(
        handle_business_static_ad_generate(
            {
                "business": "frameforge",
                "input_path": "research/example-spec.json",
                "slug": "frameforge-static-live",
                "idempotency_key": "frameforge-static-live-v1",
            }
        )
    )

    assert result["success"] is True
    assert result["status"] == "created"
    assert result["balance_credits"] == 8
    receipt = json.loads(
        (
            tmp_path
            / "businesses"
            / "frameforge"
            / "product"
            / "static-ads"
            / "frameforge-static-live"
            / "receipt.json"
        ).read_text(encoding="utf-8")
    )
    assert receipt["credits_charged"] == 2


def test_business_ugc_ad_generate_blocks_without_credits(tmp_path, monkeypatch):
    monkeypatch.setenv("TAKYON_HOME", str(tmp_path))
    store = TakyonStore(tmp_path)
    _commit(
        store,
        "business:clipbook",
        [{"action": "business.upsert", "business": "clipbook", "name": "Clipbook", "mode": "live"}],
        "init-clipbook-live",
    )
    brief_path = tmp_path / "businesses" / "clipbook" / "research" / "brief.json"
    brief_path.parent.mkdir(parents=True, exist_ok=True)
    brief_path.write_text(json.dumps({"business": "clipbook", "product": "demo"}), encoding="utf-8")
    monkeypatch.setattr(
        takyon_core,
        "_call_creative_runtime_gateway",
        lambda endpoint, payload: {
            "success": False,
            "status": "blocked_insufficient_creative_credits",
            "requested_credits": 8,
            "available_credits": 0,
            "balance_credits": 0,
            "reserved_credits": 0,
            "error": "insufficient_creative_credits",
        },
    )

    result = json.loads(
        handle_business_ugc_ad_generate(
            {
                "business": "clipbook",
                "brief_path": "research/brief.json",
                "slug": "clipbook-demo",
                "idempotency_key": "clipbook-ugc-live-v1",
            }
        )
    )

    assert result["success"] is False
    assert result["status"] == "blocked_insufficient_creative_credits"
    assert "insufficient_creative_credits" in result["error"]


def test_business_ugc_ad_generate_live_charges_credits_and_records_asset(tmp_path, monkeypatch):
    monkeypatch.setenv("TAKYON_HOME", str(tmp_path))
    store = TakyonStore(tmp_path)
    _commit(
        store,
        "business:clipbook",
        [{"action": "business.upsert", "business": "clipbook", "name": "Clipbook", "mode": "live"}],
        "init-clipbook-live-success",
    )
    _grant_creative_credits(store, "clipbook", 20, "clipbook-grant")
    brief_path = tmp_path / "businesses" / "clipbook" / "research" / "brief.json"
    script_path = tmp_path / "businesses" / "clipbook" / "research" / "script.json"
    brief_path.parent.mkdir(parents=True, exist_ok=True)
    brief_path.write_text(json.dumps({"business": "clipbook", "product": "demo"}), encoding="utf-8")
    script = {"dialogue_action": [{"dialogue": "Try Clipbook", "action": "holding the product"}]}
    script_path.write_text(json.dumps(script), encoding="utf-8")

    def fake_gateway(endpoint, payload):
        publication_dir = tmp_path / "businesses" / "clipbook" / "product" / "ugc-ads" / "clipbook-demo"
        publication_dir.mkdir(parents=True, exist_ok=True)
        (publication_dir / "ad.mp4").write_bytes(b"fake mp4 bytes")
        (publication_dir / "reference.png").write_bytes(b"fake png bytes")
        (publication_dir / "script.json").write_text(json.dumps(script), encoding="utf-8")
        return {
            "success": True,
            "status": "created",
            "write_payload": {
                "value": {
                    "slug": "clipbook-demo",
                    "path": "product/ugc-ads/clipbook-demo/ad.mp4",
                    "seconds": 12.0,
                    "n_clips": 2,
                    "script": script,
                }
            },
            "credits_charged": 8,
            "balance_credits": 12,
            "reserved_credits": 0,
        }

    monkeypatch.setattr(takyon_core, "_call_creative_runtime_gateway", fake_gateway)

    result = json.loads(
        handle_business_ugc_ad_generate(
            {
                "business": "clipbook",
                "brief_path": "research/brief.json",
                "script_path": "research/script.json",
                "slug": "clipbook-demo",
                "idempotency_key": "clipbook-ugc-live-success-v1",
            }
        )
    )

    assert result["success"] is True
    assert result["status"] == "created"
    assert result["path"] == "product/ugc-ads/clipbook-demo/ad.mp4"
    assert result["balance_credits"] == 12


def _meta_test_business(tmp_path, monkeypatch, *, slug="clipbook", mode="test"):
    """Set up a temp TAKYON_HOME + a business for the Meta ad launch tests."""
    monkeypatch.setenv("TAKYON_HOME", str(tmp_path))
    store = TakyonStore(tmp_path)
    upsert = {"action": "business.upsert", "business": slug, "name": slug.title()}
    if mode:
        upsert["mode"] = mode
    _commit(store, f"business:{slug}", [upsert], f"init-{slug}")
    return store


def _grant_creative_credits(store: TakyonStore, business: str, credits: int, key: str) -> None:
    with store._connect() as conn:
        takyon_business_credits.grant_credits(conn, business, credits, idempotency_key=key)


def _meta_launch_args(**overrides):
    args = {
        "business": "clipbook",
        "mode": "launch",
        "ad_video_path": "product/ugc-ads/demo-meta/ad.mp4",
        "slug": "demo-meta",
        "campaign": {"objective": "OUTCOME_TRAFFIC"},
        "adset": {"daily_budget_usd": 5.0, "optimization_goal": "LINK_CLICKS"},
        "ad": {
            "message": "Try Clipbook",
            "link": "https://example.com/clipbook",
            "call_to_action": "LEARN_MORE",
        },
        "idempotency_key": "clipbook-meta-demo-v1",
    }
    args.update(overrides)
    return args


def _write_meta_launch_receipt(tmp_path, *, business="clipbook", slug="demo-meta", mode="live"):
    receipt_path = (
        tmp_path
        / "businesses"
        / business
        / "distribution"
        / "meta-ads"
        / slug
        / "receipt.json"
    )
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    receipt = {
        "idempotency_key": f"{slug}-launch-v1",
        "business": business,
        "slug": slug,
        "success": True,
        "mode": mode,
        "status": "created_paused" if mode == "live" else "suppressed_test_mode",
        "paused": True,
        "ad_video_path": f"product/ugc-ads/{slug}/ad.mp4",
        "ids": {
            "creative_id": "creative-1",
            "campaign_id": "campaign-1",
            "adset_id": "adset-1",
            "ad_id": "ad-1",
        },
    }
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
    return receipt_path


def test_meta_load_launch_receipt_recovers_from_event_payload_when_file_missing(tmp_path, monkeypatch):
    store = _meta_test_business(tmp_path, monkeypatch, mode="live")
    publication_dir = (
        tmp_path
        / "businesses"
        / "clipbook"
        / "distribution"
        / "meta-ads"
        / "demo-meta"
    )
    publication_dir.mkdir(parents=True, exist_ok=True)
    receipt_abs = publication_dir / "receipt.json"
    plan_abs = publication_dir / "plan.json"
    assert not receipt_abs.exists()
    recovered_receipt = {
        "idempotency_key": "clipbook-meta-demo-v1",
        "business": "clipbook",
        "slug": "demo-meta",
        "success": True,
        "mode": "live",
        "status": "created_paused",
        "paused": True,
        "ad_image_path": "product/static-ads/demo-image/creative.png",
        "plan_path": "distribution/meta-ads/demo-meta/plan.json",
        "ids": {
            "creative_id": "creative-1",
            "campaign_id": "campaign-1",
            "adset_id": "adset-1",
            "ad_id": "ad-1",
        },
        "campaign_plan": {
            "slug": "demo-meta",
            "campaign": {"name": "Demo Campaign", "objective": "OUTCOME_TRAFFIC"},
            "adset": {"daily_budget_usd": 1.0},
            "ad": {"link": "https://example.com/clipbook"},
        },
    }
    _commit(
        store,
        "business:clipbook/distribution:meta-ads/demo-meta",
        [{
            "action": "event.record",
            "business": "clipbook",
            "event_type": "meta_ad.launch",
            "payload": {**recovered_receipt, "publication_dir": "distribution/meta-ads/demo-meta"},
        }],
        "clipbook-meta-recover-v1",
    )

    loaded = _meta_load_launch_receipt(store, "clipbook", {"slug": "demo-meta"})

    assert loaded["receipt"]["status"] == "created_paused"
    assert loaded["receipt"]["ids"]["campaign_id"] == "campaign-1"
    assert receipt_abs.is_file()
    assert plan_abs.is_file()


def test_business_meta_ad_launch_test_mode_suppresses_and_is_idempotent(tmp_path, monkeypatch):
    store = _meta_test_business(tmp_path, monkeypatch)
    video_dir = tmp_path / "businesses" / "clipbook" / "product" / "ugc-ads" / "demo-meta"
    video_dir.mkdir(parents=True, exist_ok=True)
    (video_dir / "ad.mp4").write_bytes(b"fake mp4 bytes")

    result = json.loads(handle_business_meta_ad_launch(_meta_launch_args()))

    assert result["success"] is True
    assert result["status"] == "suppressed_test_mode"
    assert result["external_side_effects"] == "suppressed"
    assert result["paused"] is True
    assert result["slug"] == "demo-meta"
    assert result["receipt"] == "distribution/meta-ads/demo-meta/receipt.json"
    assert "ids" not in result  # no Meta objects created in test mode

    receipt_abs = tmp_path / "businesses" / "clipbook" / result["receipt"]
    assert receipt_abs.is_file()
    receipt = json.loads(receipt_abs.read_text(encoding="utf-8"))
    assert receipt["status"] == "suppressed_test_mode"
    assert receipt["mode"] == "test"
    assert receipt["paused"] is True
    assert receipt["idempotency_key"] == "clipbook-meta-demo-v1"

    with store._connect() as conn:
        event = conn.execute(
            "SELECT event_type FROM events WHERE business_slug = ? ORDER BY created_at DESC LIMIT 1",
            ("clipbook",),
        ).fetchone()
    assert event["event_type"] == "meta_ad.launch"

    # Re-running with the same idempotency key returns the existing receipt, not a duplicate.
    repeat = json.loads(handle_business_meta_ad_launch(_meta_launch_args()))
    assert repeat["success"] is True
    assert repeat["idempotent"] is True
    assert repeat["status"] == "suppressed_test_mode"
    assert repeat["paused"] is True


def test_business_meta_ad_launch_rejects_over_cap_budget(tmp_path, monkeypatch):
    _meta_test_business(tmp_path, monkeypatch)
    video_dir = tmp_path / "businesses" / "clipbook" / "product" / "ugc-ads" / "demo-meta"
    video_dir.mkdir(parents=True, exist_ok=True)
    (video_dir / "ad.mp4").write_bytes(b"fake mp4 bytes")

    result = json.loads(
        handle_business_meta_ad_launch(_meta_launch_args(adset={"daily_budget_usd": 999.0}))
    )

    assert result["success"] is False
    assert "exceeds the safety cap" in result["error"]
    assert not (tmp_path / "businesses" / "clipbook" / "distribution" / "meta-ads").exists()


def test_business_meta_ad_launch_refuses_activation(tmp_path, monkeypatch):
    _meta_test_business(tmp_path, monkeypatch)

    result = json.loads(handle_business_meta_ad_launch(_meta_launch_args(activate=True)))

    assert result["success"] is False
    assert "PAUSED" in result["error"]
    assert "activation" in result["error"].lower()


def test_business_meta_ad_launch_blocks_missing_video(tmp_path, monkeypatch):
    _meta_test_business(tmp_path, monkeypatch)
    # No ad.mp4 written: the plan validates but the video file is absent.

    result = json.loads(handle_business_meta_ad_launch(_meta_launch_args()))

    assert result["success"] is False
    assert "ad video not found" in result["error"]
    assert "ugc-video-ad" in result["error"]
    assert not (tmp_path / "businesses" / "clipbook" / "distribution" / "meta-ads").exists()


def test_business_meta_ad_launch_preflight_surfaces_authority_error(tmp_path, monkeypatch):
    for var in ("META_SYSTEM_USER_ACCESS_TOKEN", "META_ACCESS_TOKEN", "FACEBOOK_ACCESS_TOKEN"):
        monkeypatch.delenv(var, raising=False)
    _meta_test_business(tmp_path, monkeypatch, mode="live")
    monkeypatch.setattr(
        takyon_core,
        "_call_creative_runtime_gateway",
        lambda endpoint, payload: (_ for _ in ()).throw(
            TakyonError("Meta action requires META_SYSTEM_USER_ACCESS_TOKEN or META_ACCESS_TOKEN")
        ),
    )

    result = json.loads(
        handle_business_meta_ad_launch(
            {"business": "clipbook", "mode": "preflight", "idempotency_key": "clipbook-meta-preflight"}
        )
    )

    assert result["success"] is False
    assert "META_ACCESS_TOKEN" in result["error"]


def test_business_meta_ad_launch_test_mode_supports_image_asset(tmp_path, monkeypatch):
    _meta_test_business(tmp_path, monkeypatch)
    image_dir = tmp_path / "businesses" / "clipbook" / "product" / "static-ads" / "demo-image"
    image_dir.mkdir(parents=True, exist_ok=True)
    (image_dir / "creative.png").write_bytes(b"fake png bytes")

    result = json.loads(
        handle_business_meta_ad_launch(
            _meta_launch_args(
                asset_kind="image",
                ad_video_path="",
                ad_image_path="product/static-ads/demo-image/creative.png",
                slug="demo-image",
            )
        )
    )

    assert result["success"] is True
    assert result["status"] == "suppressed_test_mode"
    receipt = json.loads(
        (
            tmp_path
            / "businesses"
            / "clipbook"
            / "distribution"
            / "meta-ads"
            / "demo-image"
            / "receipt.json"
        ).read_text(encoding="utf-8")
    )
    assert receipt["asset_kind"] == "image"
    assert receipt["ad_image_path"] == "product/static-ads/demo-image/creative.png"


def test_business_meta_ad_launch_manual_handoff_writes_packet_without_meta_call(tmp_path, monkeypatch):
    store = _meta_test_business(tmp_path, monkeypatch, mode="live")
    video_dir = tmp_path / "businesses" / "clipbook" / "product" / "ugc-ads" / "demo-meta"
    video_dir.mkdir(parents=True, exist_ok=True)
    (video_dir / "ad.mp4").write_bytes(b"fake mp4 bytes")

    result = json.loads(
        handle_business_meta_ad_launch(
            _meta_launch_args(mode="manual_handoff", idempotency_key="clipbook-meta-manual-v1")
        )
    )

    assert result["success"] is True
    assert result["status"] == "ready_for_manual_launch"
    assert result["launch_mode"] == "manual_handoff"
    assert result["plan_path"] == "distribution/meta-ads/demo-meta/plan.json"

    plan_abs = tmp_path / "businesses" / "clipbook" / result["plan_path"]
    receipt_abs = tmp_path / "businesses" / "clipbook" / result["receipt"]
    assert plan_abs.is_file()
    assert receipt_abs.is_file()

    plan = json.loads(plan_abs.read_text(encoding="utf-8"))
    receipt = json.loads(receipt_abs.read_text(encoding="utf-8"))
    assert plan["launch_mode"] == "manual_handoff"
    assert "utm_campaign=demo-meta" in plan["ad"]["tracked_link"]
    assert receipt["status"] == "ready_for_manual_launch"
    assert receipt["launch_mode"] == "manual_handoff"

    with store._connect() as conn:
        row = conn.execute(
            "SELECT event_type FROM events WHERE business_slug = ? ORDER BY created_at DESC LIMIT 1",
            ("clipbook",),
        ).fetchone()
    assert row["event_type"] == "meta_ad.launch"


def test_business_meta_ad_launch_live_image_blocks_without_credits(tmp_path, monkeypatch):
    monkeypatch.setenv("META_ACCESS_TOKEN", "meta-test-token")
    monkeypatch.setenv("META_AD_ACCOUNT_ID", "123456")
    monkeypatch.setenv("META_PAGE_ID", "654321")
    _meta_test_business(tmp_path, monkeypatch, mode="live")
    image_dir = tmp_path / "businesses" / "clipbook" / "product" / "static-ads" / "demo-image"
    image_dir.mkdir(parents=True, exist_ok=True)
    (image_dir / "creative.png").write_bytes(b"fake png bytes")
    monkeypatch.setattr(
        takyon_core,
        "_call_creative_runtime_gateway",
        lambda endpoint, payload: {
            "success": False,
            "status": "blocked_insufficient_creative_credits",
            "requested_credits": 1,
            "available_credits": 0,
            "balance_credits": 0,
            "reserved_credits": 0,
            "error": "insufficient_creative_credits",
        },
    )

    result = json.loads(
        handle_business_meta_ad_launch(
            _meta_launch_args(
                asset_kind="image",
                ad_video_path="",
                ad_image_path="product/static-ads/demo-image/creative.png",
                slug="demo-image-live",
            )
        )
    )

    assert result["success"] is False
    assert result["status"] == "blocked_insufficient_creative_credits"
    assert "insufficient_creative_credits" in result["error"]


def test_business_meta_ad_launch_live_image_charges_credits(tmp_path, monkeypatch):
    monkeypatch.setenv("META_ACCESS_TOKEN", "meta-test-token")
    monkeypatch.setenv("META_AD_ACCOUNT_ID", "123456")
    monkeypatch.setenv("META_PAGE_ID", "654321")
    store = _meta_test_business(tmp_path, monkeypatch, mode="live")
    _grant_creative_credits(store, "clipbook", 5, "clipbook-meta-grant")
    image_dir = tmp_path / "businesses" / "clipbook" / "product" / "static-ads" / "demo-image"
    image_dir.mkdir(parents=True, exist_ok=True)
    (image_dir / "creative.png").write_bytes(b"fake png bytes")
    monkeypatch.setattr(
        takyon_core,
        "_call_creative_runtime_gateway",
        lambda endpoint, payload: {
            "success": True,
            "status": "created_paused",
            "ad_account_id": "act_123456",
            "graph_version": "v23.0",
            "ids": {
                "image_hash": "hash123",
                "creative_id": "creative-1",
                "campaign_id": "campaign-1",
                "adset_id": "adset-1",
                "ad_id": "ad-1",
            },
            "thumbnail_url": "https://example.com/image.png",
            "credits_charged": 1,
            "balance_credits": 4,
            "reserved_credits": 0,
        },
    )

    result = json.loads(
        handle_business_meta_ad_launch(
            _meta_launch_args(
                asset_kind="image",
                ad_video_path="",
                ad_image_path="product/static-ads/demo-image/creative.png",
                slug="demo-image-live",
            )
        )
    )

    assert result["success"] is True
    assert result["status"] == "created_paused"
    assert result["balance_credits"] == 4


def test_business_meta_ad_bind_manual_launch_updates_receipt_and_records_event(tmp_path, monkeypatch):
    store = _meta_test_business(tmp_path, monkeypatch, mode="live")
    video_dir = tmp_path / "businesses" / "clipbook" / "product" / "ugc-ads" / "demo-meta"
    video_dir.mkdir(parents=True, exist_ok=True)
    (video_dir / "ad.mp4").write_bytes(b"fake mp4 bytes")
    json.loads(
        handle_business_meta_ad_launch(
            _meta_launch_args(mode="manual_handoff", idempotency_key="clipbook-meta-manual-bind-launch")
        )
    )

    result = json.loads(
        handle_business_meta_ad_bind_manual_launch(
            {
                "business": "clipbook",
                "slug": "demo-meta",
                "campaign_id": "campaign-manual-1",
                "adset_id": "adset-manual-1",
                "ad_id": "ad-manual-1",
                "creative_id": "creative-manual-1",
                "launched_at": "2026-06-03T12:00:00Z",
                "actual_daily_budget_usd": 7.5,
                "idempotency_key": "clipbook-meta-manual-bind-v1",
            }
        )
    )

    assert result["success"] is True
    assert result["status"] == "bound_manual_launch"
    receipt_abs = tmp_path / "businesses" / "clipbook" / "distribution" / "meta-ads" / "demo-meta" / "receipt.json"
    receipt = json.loads(receipt_abs.read_text(encoding="utf-8"))
    assert receipt["status"] == "externally_launched"
    assert receipt["ids"]["campaign_id"] == "campaign-manual-1"
    assert receipt["actual_daily_budget_usd"] == 7.5

    with store._connect() as conn:
        row = conn.execute(
            "SELECT event_type FROM events WHERE business_slug = ? ORDER BY created_at DESC LIMIT 1",
            ("clipbook",),
        ).fetchone()
    assert row["event_type"] == "meta_ad.manual_bind"


def test_business_meta_ad_control_test_mode_suppresses_and_is_idempotent(tmp_path, monkeypatch):
    store = _meta_test_business(tmp_path, monkeypatch, mode="test")
    _write_meta_launch_receipt(tmp_path, mode="test")

    result = json.loads(
        handle_business_meta_ad_control(
            {
                "business": "clipbook",
                "slug": "demo-meta",
                "operation": "activate",
                "idempotency_key": "clipbook-meta-activate-test-v1",
            }
        )
    )

    assert result["success"] is True
    assert result["status"] == "suppressed_test_mode"
    assert result["operation"] == "activate"
    receipt_abs = tmp_path / "businesses" / "clipbook" / result["receipt"]
    assert receipt_abs.is_file()
    receipt = json.loads(receipt_abs.read_text(encoding="utf-8"))
    assert receipt["mode"] == "test"

    with store._connect() as conn:
        event = conn.execute(
            "SELECT event_type FROM events WHERE business_slug = ? ORDER BY created_at DESC LIMIT 1",
            ("clipbook",),
        ).fetchone()
    assert event["event_type"] == "meta_ad.activate"

    repeat = json.loads(
        handle_business_meta_ad_control(
            {
                "business": "clipbook",
                "slug": "demo-meta",
                "operation": "activate",
                "idempotency_key": "clipbook-meta-activate-test-v1",
            }
        )
    )
    assert repeat["success"] is True
    assert repeat["idempotent"] is True


def test_business_meta_ad_control_live_activate_records_event(tmp_path, monkeypatch):
    monkeypatch.setenv("META_ACCESS_TOKEN", "meta-test-token")
    store = _meta_test_business(tmp_path, monkeypatch, mode="live")
    _write_meta_launch_receipt(tmp_path)
    calls: list[tuple[str, dict]] = []

    def fake_gateway(endpoint, payload):
        calls.append((endpoint, payload))
        return {
            "success": True,
            "status": "activated",
            "graph_version": "v23.0",
            "applied": [
                {"object": "campaign", "id": "campaign-1", "status": "ACTIVE"},
                {"object": "adset", "id": "adset-1", "status": "ACTIVE"},
                {"object": "ad", "id": "ad-1", "status": "ACTIVE"},
            ],
        }

    monkeypatch.setattr(takyon_core, "_call_creative_runtime_gateway", fake_gateway)

    result = json.loads(
        handle_business_meta_ad_control(
            {
                "business": "clipbook",
                "slug": "demo-meta",
                "operation": "activate",
                "idempotency_key": "clipbook-meta-activate-live-v1",
            }
        )
    )

    assert result["success"] is True
    assert result["status"] == "activated"
    assert calls[0][0] == "meta-control"
    assert calls[0][1]["campaign_id"] == "campaign-1"

    with store._connect() as conn:
        row = conn.execute(
            "SELECT event_type, payload_json FROM events WHERE business_slug = ? ORDER BY created_at DESC LIMIT 1",
            ("clipbook",),
        ).fetchone()
    assert row["event_type"] == "meta_ad.activate"
    payload = json.loads(row["payload_json"])
    assert payload["receipt"].startswith("distribution/meta-ads/demo-meta/actions/")


def test_business_meta_ad_control_rejects_over_cap_budget(tmp_path, monkeypatch):
    monkeypatch.setenv("TAKYON_META_MAX_DAILY_BUDGET_USD", "10")
    _meta_test_business(tmp_path, monkeypatch, mode="live")
    _write_meta_launch_receipt(tmp_path)

    result = json.loads(
        handle_business_meta_ad_control(
            {
                "business": "clipbook",
                "slug": "demo-meta",
                "operation": "set_budget",
                "daily_budget_usd": 999,
                "idempotency_key": "clipbook-meta-budget-cap-v1",
            }
        )
    )

    assert result["success"] is False
    assert "exceeds the safety cap" in result["error"]


def test_business_meta_ad_insights_sync_live_writes_snapshot(tmp_path, monkeypatch):
    monkeypatch.setenv("META_ACCESS_TOKEN", "meta-test-token")
    store = _meta_test_business(tmp_path, monkeypatch, mode="live")
    _write_meta_launch_receipt(tmp_path)

    monkeypatch.setattr(
        takyon_core,
        "_call_creative_runtime_gateway",
        lambda endpoint, payload: {
            "success": True,
            "status": "synced",
            "graph_version": "v23.0",
            "rows": [
                {
                    "account_currency": "USD",
                    "campaign_id": "campaign-1",
                    "campaign_name": "Demo Campaign",
                    "date_start": "2026-06-01",
                    "date_stop": "2026-06-01",
                    "impressions": "1000",
                    "reach": "700",
                    "clicks": "25",
                    "spend": "12.34",
                    "ctr": "2.5",
                    "cpc": "0.4936",
                    "cpm": "12.34",
                }
            ],
        },
    )

    result = json.loads(
        handle_business_meta_ad_insights_sync(
            {
                "business": "clipbook",
                "slug": "demo-meta",
                "level": "campaign",
                "date_preset": "last_7d",
                "idempotency_key": "clipbook-meta-insights-v1",
            }
        )
    )

    assert result["success"] is True
    assert result["status"] == "synced"
    assert result["totals"]["spend_cents"] == 1234
    assert result["totals"]["clicks"] == 25

    metrics_abs = tmp_path / "businesses" / "clipbook" / result["metrics_path"]
    assert metrics_abs.is_file()
    lines = [json.loads(line) for line in metrics_abs.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert lines[-1]["totals"]["spend_cents"] == 1234

    with store._connect() as conn:
        row = conn.execute(
            "SELECT event_type, payload_json FROM events WHERE business_slug = ? ORDER BY created_at DESC LIMIT 1",
            ("clipbook",),
        ).fetchone()
    assert row["event_type"] == "meta_ad.insights_sync"
    payload = json.loads(row["payload_json"])
    assert payload["receipt"].startswith("metrics/meta-ads/demo-meta/syncs/")


def test_business_meta_ad_insights_sync_manual_writes_snapshot(tmp_path, monkeypatch):
    store = _meta_test_business(tmp_path, monkeypatch, mode="live")
    _write_meta_launch_receipt(tmp_path)

    result = json.loads(
        handle_business_meta_ad_insights_sync(
            {
                "business": "clipbook",
                "slug": "demo-meta",
                "source": "manual",
                "level": "campaign",
                "time_range": {"since": "2026-06-01", "until": "2026-06-01"},
                "spend_usd": 12.34,
                "impressions": 1000,
                "clicks": 25,
                "idempotency_key": "clipbook-meta-manual-insights-v1",
            }
        )
    )

    assert result["success"] is True
    assert result["status"] == "synced_manual"
    assert result["totals"]["spend_cents"] == 1234
    assert result["totals"]["ctr"] == 2.5
    assert result["totals"]["cpc"] == 0.4936
    assert result["totals"]["cpm"] == 12.34

    metrics_abs = tmp_path / "businesses" / "clipbook" / result["metrics_path"]
    assert metrics_abs.is_file()
    lines = [json.loads(line) for line in metrics_abs.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert lines[-1]["source"] == "manual"

    with store._connect() as conn:
        row = conn.execute(
            "SELECT event_type FROM events WHERE business_slug = ? ORDER BY created_at DESC LIMIT 1",
            ("clipbook",),
        ).fetchone()
    assert row["event_type"] == "meta_ad.insights_sync"


def _reddit_launch_args(**overrides):
    args = {
        "business": "clipbook",
        "mode": "launch",
        "asset_kind": "image",
        "slug": "demo-reddit",
        "campaign": {"objective": "CLICKS"},
        "ad_group": {"daily_budget_usd": 5.0, "optimization_goal": "CLICKS"},
        "post": {
            "headline": "Try Clipbook",
            "destination_url": "https://example.com/clipbook",
            "media_url": "https://cdn.example.com/clipbook.png",
            "thumbnail_url": "https://cdn.example.com/clipbook-thumb.png",
            "allow_comments": False,
        },
        "ad": {"name": "Clipbook Reddit Ad", "click_url": "https://example.com/clipbook"},
        "idempotency_key": "clipbook-reddit-demo-v1",
    }
    args.update(overrides)
    return args


def _stub_reddit_ads_config(monkeypatch):
    monkeypatch.setattr(
        takyon_core,
        "_reddit_ads_config",
        lambda require_auth=True: {
            "client_id": "reddit-client",
            "client_secret": "reddit-secret",
            "access_token": "reddit-access-token",
            "refresh_token": "reddit-refresh-token",
            "user_agent": "takyon-tests/1.0",
            "business_id": "business-1",
            "ad_account_id": "a2_demo",
            "profile_id": "t2_profile",
            "funding_instrument_id": "fi_1",
            "pixel_id": "pixel_1",
            "api_base": "https://ads-api.reddit.com/api/v3",
            "state": {},
            "state_path": None,
            "expires_at": 0,
        },
    )


def _write_reddit_launch_receipt(tmp_path, *, business="clipbook", slug="demo-reddit", mode="live"):
    receipt_path = (
        tmp_path
        / "businesses"
        / business
        / "distribution"
        / "reddit-ads"
        / slug
        / "receipt.json"
    )
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    receipt = {
        "idempotency_key": f"{slug}-launch-v1",
        "business": business,
        "slug": slug,
        "success": True,
        "mode": mode,
        "status": "created_paused" if mode == "live" else "suppressed_test_mode",
        "paused": True,
        "asset_kind": "existing_post",
        "objective": "CLICKS",
        "daily_budget_usd": 5.0,
        "budget_scope": "ad_group",
        "ad_account_id": "a2_demo",
        "ids": {
            "campaign_id": "campaign-1",
            "ad_group_id": "adgroup-1",
            "ad_id": "ad-1",
            "post_id": "t3_demo123",
        },
    }
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
    return receipt_path


def test_business_reddit_ad_launch_test_mode_suppresses_and_is_idempotent(tmp_path, monkeypatch):
    _stub_reddit_ads_config(monkeypatch)
    store = _meta_test_business(tmp_path, monkeypatch)

    result = json.loads(handle_business_reddit_ad_launch(_reddit_launch_args()))

    assert result["success"] is True
    assert result["status"] == "suppressed_test_mode"
    assert result["external_side_effects"] == "suppressed"
    assert result["paused"] is True
    assert result["slug"] == "demo-reddit"
    assert result["plan_path"] == "distribution/reddit-ads/demo-reddit/plan.json"
    assert result["receipt"] == "distribution/reddit-ads/demo-reddit/receipt.json"
    assert "ids" not in result

    plan_abs = tmp_path / "businesses" / "clipbook" / result["plan_path"]
    assert plan_abs.is_file()
    plan = json.loads(plan_abs.read_text(encoding="utf-8"))
    assert plan["asset_kind"] == "image"
    assert plan["post"]["media_url"] == "https://cdn.example.com/clipbook.png"

    receipt_abs = tmp_path / "businesses" / "clipbook" / result["receipt"]
    assert receipt_abs.is_file()
    receipt = json.loads(receipt_abs.read_text(encoding="utf-8"))
    assert receipt["status"] == "suppressed_test_mode"
    assert receipt["mode"] == "test"
    assert receipt["paused"] is True
    assert receipt["idempotency_key"] == "clipbook-reddit-demo-v1"
    assert receipt["asset_kind"] == "image"
    assert receipt["plan_path"] == result["plan_path"]

    with store._connect() as conn:
        event = conn.execute(
            "SELECT event_type FROM events WHERE business_slug = ? ORDER BY created_at DESC LIMIT 1",
            ("clipbook",),
        ).fetchone()
    assert event["event_type"] == "reddit_ad.launch"

    repeat = json.loads(handle_business_reddit_ad_launch(_reddit_launch_args()))
    assert repeat["success"] is True
    assert repeat["idempotent"] is True
    assert repeat["status"] == "suppressed_test_mode"


def test_business_reddit_ad_launch_defaults_destination_to_canonical_product_url(tmp_path, monkeypatch):
    _stub_reddit_ads_config(monkeypatch)
    _meta_test_business(tmp_path, monkeypatch)

    result = json.loads(
        handle_business_reddit_ad_launch(
            _reddit_launch_args(
                post={
                    "headline": "Try Clipbook",
                    "media_url": "https://cdn.example.com/clipbook.png",
                    "allow_comments": False,
                },
                ad={"name": "Clipbook Reddit Ad"},
            )
        )
    )

    assert result["success"] is True
    plan_abs = tmp_path / "businesses" / "clipbook" / result["plan_path"]
    plan = json.loads(plan_abs.read_text(encoding="utf-8"))
    assert plan["post"]["destination_url"] == "https://clipbook.fourmanifold.com/"
    assert plan["ad"]["click_url"] == "https://clipbook.fourmanifold.com/"

    receipt_abs = tmp_path / "businesses" / "clipbook" / result["receipt"]
    receipt = json.loads(receipt_abs.read_text(encoding="utf-8"))
    assert receipt["destination_url"] == "https://clipbook.fourmanifold.com/"
    assert receipt["click_url"] == "https://clipbook.fourmanifold.com/"


def test_business_reddit_ad_launch_preserves_copy_fields_in_plan_and_receipt(tmp_path, monkeypatch):
    _stub_reddit_ads_config(monkeypatch)
    _meta_test_business(tmp_path, monkeypatch)

    result = json.loads(
        handle_business_reddit_ad_launch(
            _reddit_launch_args(
                post={
                    "headline": "Try Clipbook",
                    "destination_url": "https://clipbook.fourmanifold.com/",
                    "media_url": "https://cdn.example.com/clipbook.png",
                    "allow_comments": False,
                    "display_url": "https://clipbook.fourmanifold.com/",
                    "call_to_action": "Learn More",
                    "supplementary_text": "No ticket queue. No templated sludge.",
                    "body": "Optional long-form copy for Reddit post flows.",
                },
                ad={"name": "Clipbook Reddit Ad"},
            )
        )
    )

    assert result["success"] is True
    plan_abs = tmp_path / "businesses" / "clipbook" / result["plan_path"]
    plan = json.loads(plan_abs.read_text(encoding="utf-8"))
    assert plan["post"]["display_url"] == "https://clipbook.fourmanifold.com/"
    assert plan["post"]["call_to_action"] == "Learn More"
    assert plan["post"]["supplementary_text"] == "No ticket queue. No templated sludge."
    assert plan["post"]["body"] == "Optional long-form copy for Reddit post flows."

    receipt_abs = tmp_path / "businesses" / "clipbook" / result["receipt"]
    receipt = json.loads(receipt_abs.read_text(encoding="utf-8"))
    assert receipt["display_url"] == "https://clipbook.fourmanifold.com/"
    assert receipt["call_to_action"] == "Learn More"
    assert receipt["supplementary_text"] == "No ticket queue. No templated sludge."
    assert receipt["body"] == "Optional long-form copy for Reddit post flows."


def test_business_reddit_ad_launch_test_mode_stages_local_image_asset(tmp_path, monkeypatch):
    _stub_reddit_ads_config(monkeypatch)
    _meta_test_business(tmp_path, monkeypatch)
    image_rel = "product/static-ads/demo-reddit/banner.png"
    image_abs = tmp_path / "businesses" / "clipbook" / image_rel
    image_abs.parent.mkdir(parents=True, exist_ok=True)
    image_abs.write_bytes(b"fake png bytes")

    result = json.loads(
        handle_business_reddit_ad_launch(
            _reddit_launch_args(
                post={
                    "headline": "Try Clipbook",
                    "destination_url": "https://example.com/clipbook",
                    "image_path": image_rel,
                    "allow_comments": False,
                }
            )
        )
    )

    assert result["success"] is True
    assert result["status"] == "suppressed_test_mode"
    plan_abs = tmp_path / "businesses" / "clipbook" / result["plan_path"]
    plan = json.loads(plan_abs.read_text(encoding="utf-8"))
    assert plan["post"]["media_url"].endswith("/_takyon/assets/demo-reddit-image/banner.png")
    assert plan["post"]["thumbnail_url"] == plan["post"]["media_url"]
    assert len(plan["public_assets"]) == 1
    asset_receipt_rel = plan["public_assets"][0]["receipt_path"]
    asset_receipt_abs = tmp_path / "businesses" / "clipbook" / asset_receipt_rel
    assert asset_receipt_abs.is_file()
    asset_receipt = json.loads(asset_receipt_abs.read_text(encoding="utf-8"))
    assert asset_receipt["source_path"] == image_rel
    assert asset_receipt["status"] == "staged_unverified"
    assert asset_receipt["public_url_verified"] is False


def test_business_reddit_ad_launch_test_mode_stages_local_video_and_reference_thumbnail(tmp_path, monkeypatch):
    _stub_reddit_ads_config(monkeypatch)
    _meta_test_business(tmp_path, monkeypatch)
    publication_rel = "product/ugc-ads/demo-reddit-video"
    publication_dir = tmp_path / "businesses" / "clipbook" / publication_rel
    publication_dir.mkdir(parents=True, exist_ok=True)
    (publication_dir / "ad.mp4").write_bytes(b"fake mp4 bytes")
    (publication_dir / "reference.png").write_bytes(b"fake png bytes")

    result = json.loads(
        handle_business_reddit_ad_launch(
            _reddit_launch_args(
                asset_kind="video",
                post={
                    "headline": "Watch Clipbook",
                    "destination_url": "https://example.com/clipbook",
                    "video_path": f"{publication_rel}/ad.mp4",
                    "allow_comments": False,
                },
            )
        )
    )

    assert result["success"] is True
    plan_abs = tmp_path / "businesses" / "clipbook" / result["plan_path"]
    plan = json.loads(plan_abs.read_text(encoding="utf-8"))
    assert plan["post"]["media_url"].endswith("/_takyon/assets/demo-reddit-video/ad.mp4")
    assert plan["post"]["thumbnail_url"].endswith("/_takyon/assets/demo-reddit-thumbnail/reference.png")
    assert len(plan["public_assets"]) == 2


def test_business_reddit_ad_launch_live_local_asset_failure_writes_blocked_public_asset_receipt(tmp_path, monkeypatch):
    _stub_reddit_ads_config(monkeypatch)
    _meta_test_business(tmp_path, monkeypatch, mode="live")
    image_rel = "product/static-ads/demo-reddit/banner.png"
    image_abs = tmp_path / "businesses" / "clipbook" / image_rel
    image_abs.parent.mkdir(parents=True, exist_ok=True)
    image_abs.write_bytes(b"fake png bytes")
    monkeypatch.setattr(takyon_core, "_probe_public_asset_url", lambda url: (False, "dns-miss"))

    result = json.loads(
        handle_business_reddit_ad_launch(
            _reddit_launch_args(
                post={
                    "headline": "Try Clipbook",
                    "destination_url": "https://example.com/clipbook",
                    "image_path": image_rel,
                    "allow_comments": False,
                }
            )
        )
    )

    assert result["success"] is False
    assert "staged public asset is not reachable yet" in result["error"]
    asset_receipt_abs = (
        tmp_path
        / "businesses"
        / "clipbook"
        / "product"
        / "public-assets"
        / "demo-reddit-image"
        / "receipt.json"
    )
    assert asset_receipt_abs.is_file()
    asset_receipt = json.loads(asset_receipt_abs.read_text(encoding="utf-8"))
    assert asset_receipt["status"] == "blocked_public_url_unreachable"
    assert asset_receipt["blocker"] == "dns-miss"


def test_business_reddit_ad_launch_rejects_over_cap_budget(tmp_path, monkeypatch):
    _stub_reddit_ads_config(monkeypatch)
    _meta_test_business(tmp_path, monkeypatch)

    result = json.loads(
        handle_business_reddit_ad_launch(_reddit_launch_args(ad_group={"daily_budget_usd": 999.0}))
    )

    assert result["success"] is False
    assert "exceeds the safety cap" in result["error"]
    assert not (tmp_path / "businesses" / "clipbook" / "distribution" / "reddit-ads").exists()


def test_business_reddit_ad_launch_refuses_activation(tmp_path, monkeypatch):
    _stub_reddit_ads_config(monkeypatch)
    _meta_test_business(tmp_path, monkeypatch)

    result = json.loads(handle_business_reddit_ad_launch(_reddit_launch_args(activate=True)))

    assert result["success"] is False
    assert "PAUSED" in result["error"]
    assert "activation" in result["error"].lower()


def test_business_reddit_ad_launch_live_blocks_without_credits(tmp_path, monkeypatch):
    _stub_reddit_ads_config(monkeypatch)
    _meta_test_business(tmp_path, monkeypatch, mode="live")
    monkeypatch.setattr(
        takyon_core,
        "_call_creative_runtime_gateway",
        lambda endpoint, payload: {
            "success": False,
            "status": "blocked_insufficient_creative_credits",
            "requested_credits": 1,
            "available_credits": 0,
            "balance_credits": 0,
            "reserved_credits": 0,
            "error": "insufficient_creative_credits",
        },
    )

    result = json.loads(
        handle_business_reddit_ad_launch(
            _reddit_launch_args(
                asset_kind="existing_post",
                post_id="t3_demo123",
                post={},
                ad={"name": "Clipbook Reddit Ad", "click_url": "https://example.com/clipbook"},
                slug="demo-reddit-live",
            )
        )
    )

    assert result["success"] is False
    assert result["status"] == "blocked_insufficient_creative_credits"
    assert "insufficient_creative_credits" in result["error"]


def test_business_reddit_ad_launch_live_charges_credits(tmp_path, monkeypatch):
    _stub_reddit_ads_config(monkeypatch)
    store = _meta_test_business(tmp_path, monkeypatch, mode="live")
    _grant_creative_credits(store, "clipbook", 5, "clipbook-reddit-grant")
    monkeypatch.setattr(
        takyon_core,
        "_call_creative_runtime_gateway",
        lambda endpoint, payload: {
            "success": True,
            "status": "created_paused",
            "business_id": "business-1",
            "ad_account_id": "a2_demo",
            "profile_id": "t2_profile",
            "funding_instrument_id": "fi_1",
            "pixel_id": "pixel_1",
            "ids": {
                "campaign_id": "campaign-1",
                "ad_group_id": "adgroup-1",
                "ad_id": "ad-1",
                "post_id": "t3_demo123",
            },
            "preview_url": "https://www.reddit.com/?ad=preview",
            "preview_expiry": "2026-06-04T00:00:00Z",
            "post_url": "https://www.reddit.com/comments/demo",
            "credits_charged": 1,
            "balance_credits": 4,
            "reserved_credits": 0,
        },
    )

    result = json.loads(
        handle_business_reddit_ad_launch(
            _reddit_launch_args(
                asset_kind="existing_post",
                post_id="t3_demo123",
                post={},
                ad={"name": "Clipbook Reddit Ad", "click_url": "https://example.com/clipbook"},
                slug="demo-reddit-live",
            )
        )
    )

    assert result["success"] is True
    assert result["status"] == "created_paused"
    assert result["balance_credits"] == 4


def test_reddit_launch_plan_passes_structured_post_payload(tmp_path, monkeypatch):
    store = _meta_test_business(tmp_path, monkeypatch, mode="live")
    staged_args, staged_assets = takyon_core._reddit_stage_launch_args(
        store,
        "clipbook",
        _reddit_launch_args(
            post={
                "headline": "Try Clipbook",
                "media_url": "https://cdn.example.com/clipbook.png",
                "allow_comments": False,
                "call_to_action": "Learn More",
                "supplementary_text": "No ticket queue. No templated sludge.",
            },
            ad={"name": "Clipbook Reddit Ad"},
            slug="demo-reddit-structured",
            idempotency_key="clipbook-reddit-structured-v1",
        ),
        publish_target=_product_publish_target("clipbook"),
        verify_public_url=False,
    )

    assert staged_assets == []
    plan = takyon_core._reddit_launch_plan(staged_args, {})
    assert plan["structured_post_payload"]["data"]["creative"]["destination"]["url"] == "https://clipbook.fourmanifold.com/"
    assert plan["structured_post_payload"]["data"]["creative"]["destination"]["call_to_action"] == "Learn More"
    assert plan["structured_post_payload"]["data"]["creative"]["supplementary_text"] == "No ticket queue. No templated sludge."
    assert plan["legacy_post_payload"]["data"]["content"][0]["destination_url"] == "https://clipbook.fourmanifold.com/"
    assert plan["legacy_post_payload"]["data"]["content"][0]["call_to_action"] == "Learn More"


def test_reddit_launch_plan_uses_body_as_structured_copy_fallback(tmp_path, monkeypatch):
    store = _meta_test_business(tmp_path, monkeypatch, mode="live")
    staged_args, staged_assets = takyon_core._reddit_stage_launch_args(
        store,
        "clipbook",
        _reddit_launch_args(
            post={
                "headline": "Try Clipbook",
                "media_url": "https://cdn.example.com/clipbook.png",
                "body": "Optional long-form copy for Reddit post flows.",
                "allow_comments": False,
            },
            ad={"name": "Clipbook Reddit Ad"},
            slug="demo-reddit-body-copy",
            idempotency_key="clipbook-reddit-body-copy-v1",
        ),
        publish_target=_product_publish_target("clipbook"),
        verify_public_url=False,
    )

    assert staged_assets == []
    plan = takyon_core._reddit_launch_plan(staged_args, {})
    assert plan["structured_post_payload"]["data"]["creative"]["supplementary_text"] == "Optional long-form copy for Reddit post flows."
    assert plan["legacy_post_payload"]["data"]["body"] == "Optional long-form copy for Reddit post flows."


def test_business_reddit_ad_control_test_mode_suppresses_and_is_idempotent(tmp_path, monkeypatch):
    store = _meta_test_business(tmp_path, monkeypatch, mode="test")
    _write_reddit_launch_receipt(tmp_path, mode="test")

    result = json.loads(
        handle_business_reddit_ad_control(
            {
                "business": "clipbook",
                "slug": "demo-reddit",
                "operation": "activate",
                "idempotency_key": "clipbook-reddit-activate-test-v1",
            }
        )
    )

    assert result["success"] is True
    assert result["status"] == "suppressed_test_mode"
    assert result["operation"] == "activate"
    receipt_abs = tmp_path / "businesses" / "clipbook" / result["receipt"]
    assert receipt_abs.is_file()
    receipt = json.loads(receipt_abs.read_text(encoding="utf-8"))
    assert receipt["mode"] == "test"

    with store._connect() as conn:
        event = conn.execute(
            "SELECT event_type FROM events WHERE business_slug = ? ORDER BY created_at DESC LIMIT 1",
            ("clipbook",),
        ).fetchone()
    assert event["event_type"] == "reddit_ad.activate"

    repeat = json.loads(
        handle_business_reddit_ad_control(
            {
                "business": "clipbook",
                "slug": "demo-reddit",
                "operation": "activate",
                "idempotency_key": "clipbook-reddit-activate-test-v1",
            }
        )
    )
    assert repeat["success"] is True
    assert repeat["idempotent"] is True


def test_business_reddit_ad_control_live_activate_records_event(tmp_path, monkeypatch):
    store = _meta_test_business(tmp_path, monkeypatch, mode="live")
    _write_reddit_launch_receipt(tmp_path)
    calls: list[tuple[str, dict]] = []

    def fake_gateway(endpoint, payload):
        calls.append((endpoint, payload))
        return {
            "success": True,
            "status": "activated",
            "applied": [
                {"object": "campaign", "id": "campaign-1", "configured_status": "ACTIVE"},
                {"object": "ad_group", "id": "adgroup-1", "configured_status": "ACTIVE"},
                {"object": "ad", "id": "ad-1", "configured_status": "ACTIVE"},
            ],
        }

    monkeypatch.setattr(takyon_core, "_call_creative_runtime_gateway", fake_gateway)

    result = json.loads(
        handle_business_reddit_ad_control(
            {
                "business": "clipbook",
                "slug": "demo-reddit",
                "operation": "activate",
                "idempotency_key": "clipbook-reddit-activate-live-v1",
            }
        )
    )

    assert result["success"] is True
    assert result["status"] == "activated"
    assert calls[0][0] == "reddit-control"
    assert calls[0][1]["campaign_id"] == "campaign-1"

    with store._connect() as conn:
        row = conn.execute(
            "SELECT event_type, payload_json FROM events WHERE business_slug = ? ORDER BY created_at DESC LIMIT 1",
            ("clipbook",),
        ).fetchone()
    assert row["event_type"] == "reddit_ad.activate"
    payload = json.loads(row["payload_json"])
    assert payload["receipt"].startswith("distribution/reddit-ads/demo-reddit/actions/")


def test_business_reddit_ad_control_rejects_over_cap_budget(tmp_path, monkeypatch):
    monkeypatch.setenv("TAKYON_REDDIT_MAX_DAILY_BUDGET_USD", "10")
    _meta_test_business(tmp_path, monkeypatch, mode="live")
    _write_reddit_launch_receipt(tmp_path)

    result = json.loads(
        handle_business_reddit_ad_control(
            {
                "business": "clipbook",
                "slug": "demo-reddit",
                "operation": "set_budget",
                "daily_budget_usd": 999,
                "idempotency_key": "clipbook-reddit-budget-cap-v1",
            }
        )
    )

    assert result["success"] is False
    assert "exceeds the safety cap" in result["error"]


def test_business_reddit_ad_insights_sync_live_writes_snapshot(tmp_path, monkeypatch):
    store = _meta_test_business(tmp_path, monkeypatch, mode="live")
    _write_reddit_launch_receipt(tmp_path)

    monkeypatch.setattr(
        takyon_core,
        "_call_creative_runtime_gateway",
        lambda endpoint, payload: {
            "success": True,
            "status": "synced",
            "rows": [
                {
                    "date": "2026-06-01",
                    "impressions": 1000,
                    "clicks": 25,
                    "spend": 12340000,
                    "ctr": 2.5,
                    "cpc": 493600,
                    "cpm": 12340000,
                }
            ],
        },
    )

    result = json.loads(
        handle_business_reddit_ad_insights_sync(
            {
                "business": "clipbook",
                "slug": "demo-reddit",
                "level": "campaign",
                "starts_at": "2026-06-01T00:00:00Z",
                "ends_at": "2026-06-02T00:00:00Z",
                "idempotency_key": "clipbook-reddit-insights-v1",
            }
        )
    )

    assert result["success"] is True
    assert result["status"] == "synced"
    assert result["totals"]["spend_micros"] == 12340000
    assert result["totals"]["clicks"] == 25

    metrics_abs = tmp_path / "businesses" / "clipbook" / result["metrics_path"]
    assert metrics_abs.is_file()
    lines = [json.loads(line) for line in metrics_abs.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert lines[-1]["totals"]["spend_micros"] == 12340000

    with store._connect() as conn:
        row = conn.execute(
            "SELECT event_type, payload_json FROM events WHERE business_slug = ? ORDER BY created_at DESC LIMIT 1",
            ("clipbook",),
        ).fetchone()
    assert row["event_type"] == "reddit_ad.insights_sync"
    payload = json.loads(row["payload_json"])
    assert payload["receipt"].startswith("metrics/reddit-ads/demo-reddit/syncs/")


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
