"""Tests for the Takyon CEO operator plugin."""

from __future__ import annotations

import json
import os
import stat
import tempfile
import types
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

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
    _surface_product_workflow_shape,
    _subuser_surface_context_payload,
    _validate_product_surface_contract,
    handle_business_check_runtime_capabilities,
    handle_business_create_app_checkout,
    handle_business_delete_business,
    handle_business_list_businesses,
    handle_business_write_file,
    handle_business_reddit_ad_control,
    handle_business_reddit_ad_insights_sync,
    handle_business_reddit_ad_launch,
    handle_business_reddit_publish_outreach,
    handle_business_publish_test_outreach,
    handle_business_x_publish_outreach,
    handle_business_x_search,
    handle_business_x_metrics_sync,
    handle_business_read_app_account,
    handle_business_read_app_record,
    handle_business_list_app_records,
    handle_business_upsert_app_customer,
    handle_business_record_stripe_webhook,
    handle_business_delete_app_record,
    handle_business_static_ad_generate,
    handle_business_generate_logo,
    handle_business_ugc_ad_generate,
    handle_business_ugc_ad_write,
    handle_business_read_channel_credit_budgets,
    handle_business_claude_agent_task,
    handle_business_set_work_focus,
    handle_business_set_channel_credit_budgets,
    handle_business_list_conversation_messages,
    handle_business_read_conversation_thread,
    handle_business_upsert_app_surface_contract,
    handle_business_upsert_app_record,
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
    # Force LOCAL safebox authority so env-resolution (DATABASE_URL etc.) reads this test
    # process's os.environ — under the rig TAKYON_SAFEBOX_URL is set, which would otherwise
    # route resolution to the out-of-process safebox stub that never sees the monkeypatched
    # DATABASE_URL. Canonical PG-test pattern (see test_takyon_operator_tiers_pg.py).
    monkeypatch.setenv("TAKYON_HOST_ROLE", "safebox")
    monkeypatch.setenv("DATABASE_URL", pg_store_dsn)
    monkeypatch.setenv("TAKYON_PLATFORM_OWNER_SUB", "auth0|takyon-plugin-tests")
    # The DB-URL resolver memoises process-wide; each test gets a fresh throwaway DB, so clear the
    # memo at setup (and teardown) so a prior test's now-dropped DSN can't leak into this test.
    from plugins.takyon.runtime_app import reset_database_url_cache

    reset_database_url_cache()
    user_id, _ = TakyonStore(root=tmp_path, database_url=pg_store_dsn).seed_platform_owner()
    import psycopg

    from plugins.takyon import billing

    with psycopg.connect(pg_store_dsn, autocommit=True) as conn:
        billing.grant_allowance(conn, user_id, 50_000, "takyon-plugin-tests-allowance")
    try:
        yield
    finally:
        reset_database_url_cache()


def _commit(store: TakyonStore, scope: str, operations: list[dict], key: str):
    return store.commit(scope=scope, operations=operations, idempotency_key=key, reason="test", actor="test")


def _sqlite_app_session(store: TakyonStore, business: str, email: str, *, name: str = "Test User") -> tuple[dict[str, Any], str]:
    """Test setup for local SQLite stores: Supabase login is PG-only, so seed a real app session row."""
    now = datetime.now(timezone.utc).isoformat()
    user_id = uuid.uuid4().hex
    token = takyon_core._random_token()
    session_id = uuid.uuid4().hex
    with store._connect() as conn:
        conn.execute(
            "INSERT INTO app_users (id, business_slug, email, name, status, tier, metadata_json, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, 'active', 'unentitled', ?, ?, ?) "
            "ON CONFLICT(business_slug, email) DO UPDATE SET "
            "name = COALESCE(excluded.name, app_users.name), updated_at = excluded.updated_at",
            (user_id, business, email, name, json.dumps({"source": "test_session"}), now, now),
        )
        user = dict(
            conn.execute(
                "SELECT * FROM app_users WHERE business_slug = ? AND email = ?",
                (business, email),
            ).fetchone()
        )
        conn.execute(
            "INSERT INTO app_sessions (id, business_slug, app_user_id, token_hash, expires_at, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                session_id,
                business,
                user["id"],
                takyon_core._hash_token(token),
                (datetime.now(timezone.utc) + timedelta(days=30)).isoformat(),
                now,
            ),
        )
        store._sync_user_tier(conn, business, str(user["id"]))
        store._rewrite_app_files(conn, business)
    return user, token


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
    assert toolsets["business_x_publish_outreach"] == "takyon"
    assert toolsets["business_publish_test_outreach"] == "takyon"
    assert toolsets["business_read_channel_credit_budgets"] == "takyon-authority"
    assert toolsets["business_ugc_ad_generate"] == "takyon-authority"
    assert toolsets["business_static_ad_generate"] == "takyon-authority"
    assert toolsets["business_meta_ad_launch"] == "takyon-authority"
    assert toolsets["business_meta_ad_control"] == "takyon-authority"
    assert toolsets["business_meta_ad_insights_sync"] == "takyon-authority"
    assert toolsets["business_meta_ad_evaluate"] == "takyon-authority"
    assert toolsets["business_meta_pixel_ensure"] == "takyon-authority"
    assert toolsets["business_meta_pixel_verify"] == "takyon-authority"
    assert toolsets["business_x_search"] == "takyon-authority"
    assert toolsets["business_x_metrics_sync"] == "takyon-authority"
    assert toolsets["business_reddit_publish_outreach"] == "takyon"
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
        "takyon-brand-logo",
        "takyon-product",
        "takyon-business-metrics",
        "takyon-conversation-followup",
        "takyon-distribution",
        "takyon-lightreel-seedance-fal-ugc",
        "takyon-market-research",
        "takyon-meta-ads-v2",
        "takyon-reddit",
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


def test_bootstrap_prompt_routes_the_x_move_to_takyon_x():
    from plugins.takyon.cli import _business_bootstrap_instruction

    prompt = _business_bootstrap_instruction("demo", "find users", "test")

    assert "Load takyon-x (skill_view) and execute its procedure to draft and publish one X post about this business." in prompt
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

    # (Invariant 9: the former `app.budget.set` operator-cap write was removed; a business with no
    # real public surface still must not get runtime mirror files. The init write above is enough to
    # prove the mirror files stay absent until a real surface exists.)

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

    # (Invariant 9: the former `app.budget.set` operator-cap write was removed. The real surface
    # upsert above is the meaningful product write; runtime mirror files must still stay absent by
    # default even after it.)

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
    assert store._ceo_cron_toolsets() == ["takyon", "takyon-authority", "web", "skills", "todo"]


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


def test_runtime_capabilities_probe_user_scoped_systemd_run(monkeypatch):
    monkeypatch.setattr(
        takyon_core,
        "_resolve_runtime_executable",
        lambda name: "/usr/bin/systemd-run" if name == "systemd-run" else None,
    )
    monkeypatch.setattr(takyon_core, "_command_version", lambda command: "255")
    monkeypatch.setattr(takyon_core.platform, "system", lambda: "Linux")

    calls: list[tuple[list[str], dict[str, Any]]] = []

    def _fake_run(command, **kwargs):
        calls.append((list(command), dict(kwargs)))
        return types.SimpleNamespace(returncode=1, stdout="", stderr="Failed to connect to bus")

    monkeypatch.setattr(takyon_core.subprocess, "run", _fake_run)

    capabilities = takyon_core._runtime_capabilities(("systemd-run",))

    assert capabilities["systemd-run"]["available"] is False
    assert capabilities["systemd-run"]["error"] == "Failed to connect to bus"
    assert calls[0][0][:3] == ["/usr/bin/systemd-run", "--user", "--scope"]


def test_action_runtime_capability_check_requires_working_user_scope_on_operator(monkeypatch):
    monkeypatch.setenv("TAKYON_HOST_ROLE", "operator")
    monkeypatch.setattr(
        takyon_core,
        "_runtime_capabilities",
        lambda requested: {
            "deno": {"available": True, "path": "/usr/bin/deno", "version": "2.8.3"},
            "systemd-run": {
                "available": False,
                "path": "/usr/bin/systemd-run",
                "version": "255",
                "error": "Failed to connect to bus",
            },
        },
    )

    result = json.loads(
        handle_business_check_runtime_capabilities(
            {"ecosystems": ["actions"], "capabilities": ["deno", "systemd-run"]}
        )
    )

    ensure = result["ensure"][0]
    assert ensure["success"] is False
    assert "requires a working user-scoped systemd-run sandbox" in ensure["error"]


def test_product_publish_target_defaults_to_business_subdomain():
    assert _product_publish_target("latexflow") == "https://latexflow.coscale.app/"


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
    assert summary["truth_surface"] == "canonical"
    assert "committed canonical workspace" in summary["truth_guidance"]

    assert source["document_role"] == "implementation_source"
    assert source["proof_level"] == "mixed"
    assert "hydrated local workspace cache" in source["proof_guidance"]
    assert source["truth_surface"] == "canonical"

    assert receipt["document_role"] == "receipt"
    assert receipt["proof_level"] == "authoritative"
    assert "Machine-generated receipt" in receipt["proof_guidance"]
    assert receipt["truth_surface"] == "canonical"


def test_business_read_file_labels_active_session_workspace_as_working(tmp_path):
    store = TakyonStore(tmp_path)
    _commit(
        store,
        "business:probe",
        [{"action": "business.upsert", "business": "probe", "name": "Probe"}],
        "init-probe-working-read-meta",
    )
    store._workspace_root_override = tmp_path / "session-home"
    business_root = store._business_root("probe", sync=False)
    (business_root / "product" / "site").mkdir(parents=True, exist_ok=True)
    (business_root / "product" / "site" / "index.html").write_text("<h1>Probe</h1>\n", encoding="utf-8")

    source = store.read(scope="business:probe", query="read_file", path="product/site/index.html")

    assert source["truth_surface"] == "working"
    assert source["proof_level"] == "authoritative"
    assert "active session workspace" in source["proof_guidance"]


def test_business_summary_and_app_surface_label_workspace_vs_recorded_live_truth(tmp_path, monkeypatch):
    monkeypatch.setenv("TAKYON_HOME", str(tmp_path))
    monkeypatch.setenv("TAKYON_PRODUCT_SITE_ROOT", str(tmp_path / "published-sites"))
    store = TakyonStore(tmp_path)
    _commit(
        store,
        "business:latexflow",
        [{"action": "business.upsert", "business": "latexflow", "name": "Latexflow", "budget": {"amount": 25}}],
        "init-summary-truth-labels",
    )
    _commit(
        store,
        "business:latexflow",
        [{"action": "app.surface.upsert", "business": "latexflow", "status": "active", "source_path": "product/site", "routes": ["/"]}],
        "surface-summary-truth-labels",
    )
    site = tmp_path / "businesses" / "latexflow" / "product" / "site"
    site.mkdir(parents=True)
    (site / "index.html").write_text("<h1>Latexflow</h1>\n", encoding="utf-8")
    handle_business_refresh_product_surface(
        {
            "business": "latexflow",
            "source_path": "product/site",
            "install": False,
            "idempotency_key": "verify-summary-truth-labels",
        }
    )

    summary = store.read(scope="business:latexflow", query="summary", include=["app"])

    assert summary["truth"]["source"]["surface"] == "canonical"
    assert summary["truth"]["live"]["surface"] == "recorded_live"
    assert summary["app"]["truth"]["source"]["surface"] == "canonical"
    assert summary["app"]["truth"]["live"]["publish_status"] == "published"
    assert summary["app"]["product_surface"]["source_truth"]["surface"] == "canonical"
    assert summary["app"]["product_surface"]["live_truth"]["surface"] == "recorded_live"


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


def test_business_upsert_rejects_dirty_fresh_workspace_root(tmp_path):
    store = TakyonStore(tmp_path)
    root = tmp_path / "businesses" / "latexflow"
    root.mkdir(parents=True)
    (root / "legacy.txt").write_text("stale workspace\n", encoding="utf-8")

    with pytest.raises(TakyonError, match=r"workspace root already exists and is non-empty"):
        _commit(
            store,
            "business:latexflow",
            [{"action": "business.upsert", "business": "latexflow", "name": "Latexflow", "goal": "Build PDFs"}],
            "reject-dirty-root",
        )


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
    assert app["surface_contract"]["public_url"] == "https://latexflow.coscale.app/"
    assert app["product_inventory"]["routes"] == ["/"]
    assert app["product_surface"]["local_continuable_work"] == []
    assert (tmp_path / "published-sites" / "latexflow" / "index.html").exists()
    assert app["surface_contract"]["publish_receipt_path"]
    assert app["surface_contract"]["routes"] == ["/"]
    pulse = store.calculate_pulse("latexflow")
    assert pulse["current_state"]["product_surface"]["inventory_status"] == "collected"
    assert pulse["summary"]["local_continuable_product_work"] == 0


def test_product_surface_refresh_keeps_http_actions_out_of_stored_runtime_features(tmp_path, monkeypatch):
    monkeypatch.setenv("TAKYON_HOME", str(tmp_path))
    monkeypatch.setenv("TAKYON_PRODUCT_SITE_ROOT", str(tmp_path / "published-sites"))
    from plugins.takyon import app_actions as takyon_app_actions

    monkeypatch.setattr(takyon_app_actions.shutil, "which", lambda name: "/usr/bin/deno")

    def fake_refresh(_business_root, _source_path, *, surface, plans, install, timeout_seconds):
        return {
            "status": "passed",
            "kind": "vite_react_ts",
            "source_path": "product/site",
            "inventory": {
                "status": "collected",
                "routes": ["/", "/app", "/app/profile"],
            },
            "warnings": [],
            "error": "",
            "local_continuable_work": [],
        }

    def fake_publish(*, business_root, slug, source_path, publish_target, source_revision):
        return {
            "status": "published",
            "public_url": publish_target,
            "publish_target": publish_target,
            "publish_source_path": source_path,
            "published_at": datetime.now(timezone.utc).isoformat(),
            "live_build_id": "test-build",
            "live_probe_status": "ok",
            "live_probe_detail": "",
            "artifact_prefix": "",
            "source_revision": source_revision,
        }

    monkeypatch.setattr(takyon_core, "_refresh_product_surface_path", fake_refresh)
    monkeypatch.setattr(takyon_core, "_publish_product_surface_path", fake_publish)

    store = TakyonStore(tmp_path)
    _commit(
        store,
        "business:latexflow",
        [{"action": "business.upsert", "business": "latexflow", "name": "Latexflow", "budget": {"amount": 25}}],
        "init-refresh-action-promotion",
    )
    _commit(
        store,
        "business:latexflow",
        [{"action": "app.surface.upsert", "business": "latexflow", "status": "active", "source_path": "product/site", "routes": ["/", "/app", "/app/profile"]}],
        "surface-refresh-action-promotion",
    )
    site = tmp_path / "businesses" / "latexflow" / "product" / "site"
    (site / "src" / "screens").mkdir(parents=True)
    (site / "actions").mkdir(parents=True)
    (site / "src" / "screens" / "app-home.tsx").write_text(
        'const { run } = useActionRunner("coach-chat");\n',
        encoding="utf-8",
    )
    (site / "actions" / "coach-chat.ts").write_text(
        "export default async (payload, ctx) => ({ reply: 'locked in' });\n",
        encoding="utf-8",
    )

    result = json.loads(
        handle_business_refresh_product_surface(
            {
                "business": "latexflow",
                "source_path": "product/site",
                "install": False,
                "idempotency_key": "refresh-action-promotion",
            }
        )
    )

    assert result["success"] is True
    assert "actions" not in result["surface_refresh"]["runtime_features"]
    app = store.read(scope="business:latexflow", query="summary", include=["app"])["app"]
    assert "actions" not in app["surface_contract"]["runtime_features"]
    context_payload = _subuser_surface_context_payload(app["surface_contract"], slug="latexflow")
    assert "actions" not in context_payload["runtimeFeatures"]


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


def test_publish_result_preserves_existing_live_state_on_blocked_republish(tmp_path, monkeypatch):
    monkeypatch.setenv("TAKYON_HOME", str(tmp_path))
    store = TakyonStore(tmp_path)
    publish_target = "https://latexflow.coscale.app/"
    _commit(
        store,
        "business:latexflow",
        [{"action": "business.upsert", "business": "latexflow", "name": "Latexflow", "budget": {"amount": 25}}],
        "init-preserve-live-publish-state",
    )
    _commit(
        store,
        "business:latexflow",
        [
            {
                "action": "app.surface.upsert",
                "business": "latexflow",
                "status": "active",
                "source_path": "product/site",
                "routes": ["/"],
                "publish_target": publish_target,
            }
        ],
        "surface-preserve-live-publish-state",
    )
    _commit(
        store,
        "business:latexflow",
        [
            {
                "action": "app.surface.publish_result",
                "business": "latexflow",
                "publish_status": "published",
                "publish_target": publish_target,
                "public_url": publish_target,
                "published_at": "2026-06-06T15:54:19+00:00",
                "receipt_path": "metrics/receipts/product-surface/published.json",
                "publish_source_path": "product/site",
                "blocker": "",
            }
        ],
        "publish-live-state",
    )
    _commit(
        store,
        "business:latexflow",
        [
            {
                "action": "app.surface.publish_result",
                "business": "latexflow",
                "publish_status": "blocked",
                "publish_target": publish_target,
                "public_url": "",
                "published_at": "",
                "receipt_path": "metrics/receipts/product-surface/blocked.json",
                "publish_source_path": "product/site",
                "blocker": "npm run build failed: next: not found",
            }
        ],
        "publish-blocked-state",
    )

    app = store.read(scope="business:latexflow", query="summary", include=["app"])["app"]
    surface = app["surface_contract"]

    assert surface["publish_status"] == "published"
    assert surface["public_url"] == publish_target
    assert surface["published_at"] == "2026-06-06T15:54:19+00:00"
    assert surface["publish_blocker"] == "npm run build failed: next: not found"
    assert surface["publish_receipt_path"] == "metrics/receipts/product-surface/blocked.json"
    assert surface["metadata"]["takyon_publish"]["preserved_live_state"] is True
    assert surface["metadata"]["takyon_publish_last_attempt"]["status"] == "blocked"


def test_product_surface_reads_live_pointer_over_stale_publish_status(tmp_path, monkeypatch):
    monkeypatch.setenv("TAKYON_HOME", str(tmp_path))
    monkeypatch.setenv("TAKYON_PRODUCT_SITE_ROOT", str(tmp_path / "published-sites"))
    store = TakyonStore(tmp_path)
    publish_target = "https://latexflow.coscale.app/"
    _commit(
        store,
        "business:latexflow",
        [{"action": "business.upsert", "business": "latexflow", "name": "Latexflow", "budget": {"amount": 25}}],
        "init-live-pointer-over-stale-status",
    )
    _commit(
        store,
        "business:latexflow",
        [
            {
                "action": "app.surface.upsert",
                "business": "latexflow",
                "status": "active",
                "source_path": "product/site",
                "routes": ["/"],
                "publish_target": publish_target,
            }
        ],
        "surface-live-pointer-over-stale-status",
    )
    site = tmp_path / "businesses" / "latexflow" / "product" / "site"
    site.mkdir(parents=True)
    (site / "index.html").write_text("<h1>Latexflow</h1>\n", encoding="utf-8")
    _commit(
        store,
        "business:latexflow",
        [
            {
                "action": "app.surface.publish_result",
                "business": "latexflow",
                "publish_status": "blocked",
                "publish_target": publish_target,
                "public_url": "",
                "published_at": "",
                "live_build_id": "build-123",
                "receipt_path": "metrics/receipts/product-surface/blocked.json",
                "publish_source_path": "product/site",
                "blocker": "stale status should not outrank live pointer",
            }
        ],
        "publish-blocked-with-live-pointer",
    )

    app = store.read(scope="business:latexflow", query="summary", include=["app"])["app"]
    surface_md = (tmp_path / "businesses" / "latexflow" / "product" / "surface.md").read_text(encoding="utf-8")

    assert app["surface_contract"]["publish_status"] == "published"
    assert app["surface_contract"]["public_url"] == publish_target
    assert app["surface_contract"]["live_build_id"] == "build-123"
    assert app["truth"]["live"]["publish_status"] == "published"
    assert app["product_surface"]["publish_status"] == "published"
    assert "- Publish status: published" in surface_md
    assert "- Live build id: build-123" in surface_md


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
    assert verification["publish"]["public_url"] == "https://briefpilot.coscale.app/"
    assert "generated source does not include a working app subroute" in verification["blocker"]
    assert (tmp_path / "published-sites" / "briefpilot" / "index.html").exists()
    app = store.read(scope="business:briefpilot", query="summary", include=["app"])["app"]
    assert app["surface_contract"]["status"] == "active"
    assert app["surface_contract"]["publish_status"] == "published"
    assert app["surface_contract"]["public_url"] == "https://briefpilot.coscale.app/"
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
          fetch('/api/takyon/apps/briefpilot/auth/session', { method: 'POST' });
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
          await fetch('/api/takyon/apps/noteleaf/auth/session', { method: 'POST' });
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
    assert "latexflow.coscale.app" in caddyfile
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
                        "publish_target": "https://latexflow.coscale.app/",
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
                "public_url": "https://latexflow.coscale.app/",
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
                        "publish_target": "https://latexflow.coscale.app/",
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
                "public_url": "https://latexflow.coscale.app/",
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


def test_business_refresh_product_surface_rejects_source_path_drift(monkeypatch):
    finalize_called = False

    class _FakeStore:
        def read(self, **_: object) -> dict[str, object]:
            return {
                "app": {
                    "surface": {
                        "source_path": "product/site",
                        "publish_target": "https://latexflow.coscale.app/",
                    }
                }
            }

    def fake_finalize(**kwargs: object) -> dict[str, object]:
        nonlocal finalize_called
        finalize_called = True
        return {"status": "passed"}

    monkeypatch.setattr(takyon_core, "_store", lambda: _FakeStore())
    monkeypatch.setattr(takyon_core, "_finalize_product_surface_refresh", fake_finalize)

    result = json.loads(
        handle_business_refresh_product_surface(
            {
                "business": "latexflow",
                "source_path": "product/longer",
                "idempotency_key": "verify-source-path-drift",
            }
        )
    )

    assert result["success"] is False
    assert "anchored to 'product/site'" in str(result.get("error") or "")
    assert finalize_called is False


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


def test_generate_rail_canonicalizes_auth_account_dependency():
    # `generate` still implies auth/account; the app_mode/subscription_style/api_mode
    # driven auto-includes were deleted with the app-shape taxonomy (§22/§23).
    runtime_features = _canonical_runtime_features_for_surface_shape(["generate"])

    assert runtime_features == ["auth", "account", "generate"]


def test_landing_only_surface_does_not_force_app_route():
    surface = {
        "landing_page_only": True,
        "metadata": {
            "subuser_app": {},
            "customer_experience": {
                "required_routes": ["/"],
                "required_app_tabs": ["Translate"],
            },
        },
    }

    shape = _surface_customer_experience_shape(surface)

    assert shape["required_routes"] == ["/"]


def test_bootstrap_app_surface_seed_ignores_burned_shape_args(tmp_path, monkeypatch):
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
    assert shape["conversion_model"] == ""
    assert shape["required_routes"] == ["/", "/app"]
    assert shape["required_app_tabs"] == []
    assert surface["runtime_features"] == ["auth", "account", "profile", "checkout"]
    assert "customer_experience" not in (surface.get("metadata") or {})


def test_bootstrap_app_surface_seed_ignores_burned_workflow_args(tmp_path, monkeypatch):
    monkeypatch.setenv("TAKYON_HOME", str(tmp_path))
    store = TakyonStore(tmp_path)
    _commit(
        store,
        "business:latexflow",
        [{"action": "business.upsert", "business": "latexflow", "name": "Latexflow", "budget": {"amount": 25}}],
        "init-bootstrap-ai-shell",
    )

    result = json.loads(
        handle_business_upsert_app_surface_contract(
            {
                "business": "latexflow",
                "source_path": "product/site",
                "runtime_features": ["actions"],
                "surface_goal": "Convert any plain English equation description into compilable LaTeX.",
                "product_workflow": {
                    "primary_user": "Student, researcher, or engineer who knows math but not LaTeX",
                    "primary_job": "Get compilable LaTeX from a plain English equation description",
                    "success_moment": "User pastes the output into Overleaf and it compiles.",
                },
                "idempotency_key": "bootstrap-ai-shell-no-actions-spec",
            }
        )
    )

    assert result["success"] is True
    app = store.read(scope="business:latexflow", query="summary", include=["app"])["app"]
    surface = app["surface_contract"]
    workflow = _surface_product_workflow_shape(surface)

    # Fresh bootstrap shells persist the fixed auth/account/profile/checkout shell
    # directly onto the contract so the published /app shell is truthful immediately.
    assert _surface_runtime_features(surface) == ["auth", "account", "profile", "checkout"]
    assert takyon_core._surface_effective_runtime_features(surface) == [  # type: ignore[attr-defined]
        "auth",
        "account",
        "profile",
        "checkout",
    ]
    assert _surface_is_bootstrap_access_shell(surface) is True
    assert workflow == {}
    assert surface["routes"] == [{"path": route} for route in takyon_core.DEFAULT_SUBUSER_APP_ROUTES]  # type: ignore[attr-defined]
    assert surface["runtime_features"] == ["auth", "account", "profile", "checkout"]
    assert "DEBUG/blocked" not in str(surface.get("notes") or "")
    assert len(app["plans"]) == 1
    plan = app["plans"][0]
    assert plan["plan_key"] == "monthly"
    assert plan["tier"] == "paid"
    assert plan["price_cents"] == 1_900
    assert plan["currency"] == "usd"
    assert plan["billing_interval"] == "month"
    assert plan["included_ai_budget_microusd"] == 5_000_000
    assert plan["included_action_quota"] == 0
    assert plan["stripe_product_id"] is None
    assert plan["stripe_price_id"] is None
    assert plan["source"] == "takyon_starter"
    assert plan["notes"] == ""
    assert plan["metadata"]["takyon_seed"] == {
        "kind": "monthly_access_shell",
        "price_status": "unset",
    }


def test_existing_source_can_select_actions_before_named_actions_exist(tmp_path, monkeypatch):
    monkeypatch.setenv("TAKYON_HOME", str(tmp_path))
    store = TakyonStore(tmp_path)
    _commit(
        store,
        "business:longer",
        [{"action": "business.upsert", "business": "longer", "name": "Longer", "budget": {"amount": 25}}],
        "init-existing-source-actions-shell",
    )

    site = tmp_path / "businesses" / "longer" / "product" / "site" / "src" / "screens"
    site.mkdir(parents=True)
    (site / "app-home.tsx").write_text(
        "export default function AppHome() { return <main>Longer</main>; }\n",
        encoding="utf-8",
    )

    result = json.loads(
        handle_business_upsert_app_surface_contract(
            {
                "business": "longer",
                "source_path": "product/site",
                "runtime_features": ["auth", "account", "actions"],
                "idempotency_key": "existing-source-actions-no-specs",
            }
        )
    )

    assert result["success"] is True
    app = store.read(scope="business:longer", query="summary", include=["app"])["app"]
    surface = app["surface_contract"]

    assert _surface_runtime_features(surface) == ["auth", "account"]


def test_surface_upsert_backfills_monthly_plan_for_existing_app_shell_source(tmp_path, monkeypatch):
    monkeypatch.setenv("TAKYON_HOME", str(tmp_path))
    store = TakyonStore(tmp_path)
    _commit(
        store,
        "business:longer",
        [{"action": "business.upsert", "business": "longer", "name": "Longer", "budget": {"amount": 25}}],
        "init-existing-source-shape",
    )

    site = tmp_path / "businesses" / "longer" / "product" / "site" / "src" / "app" / "app" / "(product)"
    site.mkdir(parents=True)
    (site / "root.js").write_text("export default function ProductRoot() { return null; }\n", encoding="utf-8")

    result = json.loads(
        handle_business_upsert_app_surface_contract(
            {
                "business": "longer",
                "source_path": "product/site",
                "runtime_features": ["auth"],
                "idempotency_key": "existing-source-shape",
            }
        )
    )

    assert result["success"] is True
    app = store.read(scope="business:longer", query="summary", include=["app"])["app"]
    assert len(app["plans"]) == 1
    plan = app["plans"][0]
    assert plan["plan_key"] == "monthly"
    assert plan["source"] == "takyon_starter"
    assert plan["metadata"]["takyon_seed"] == {
        "kind": "monthly_access_shell",
        "price_status": "unset",
    }


def test_surface_upsert_rejects_source_path_change_after_anchor(tmp_path, monkeypatch):
    monkeypatch.setenv("TAKYON_HOME", str(tmp_path))
    store = TakyonStore(tmp_path)
    _commit(
        store,
        "business:latexflow",
        [{"action": "business.upsert", "business": "latexflow", "name": "Latexflow"}],
        "init-anchor",
    )
    _commit(
        store,
        "business:latexflow",
        [{"action": "app.surface.upsert", "business": "latexflow", "source_path": "product/site"}],
        "surface-anchor",
    )

    with pytest.raises(TakyonError, match="anchored to 'product/site' and cannot switch to 'product/longer'"):
        _commit(
            store,
            "business:latexflow",
            [{"action": "app.surface.upsert", "business": "latexflow", "source_path": "product/longer"}],
            "surface-source-drift",
        )


def test_bootstrap_access_shell_surface_passes_without_generate_workflow(tmp_path):
    site = tmp_path / "product" / "site"
    app_dir = site / "app"
    profile_dir = app_dir / "profile"
    profile_dir.mkdir(parents=True)
    (site / "index.html").write_text(
        """
        <main>
          <a href="/app">Enter</a>
          <a href="/app/profile">Profile</a>
        </main>
        """,
        encoding="utf-8",
    )
    (app_dir / "index.html").write_text(
        """
        <section>
          <form id="signin">
            <input name="email" type="email" />
            <button type="submit">Sign in</button>
          </form>
          <form id="subscribe">
            <input name="plan" value="monthly" />
            <button type="submit">Subscribe</button>
          </form>
        </section>
        <script>
          fetch('/api/takyon/apps/coachyard/session');
          fetch('/api/takyon/apps/coachyard/account');
          document.getElementById('signin').addEventListener('submit', function (event) {
            event.preventDefault();
            fetch('/api/takyon/apps/coachyard/auth/session', { method: 'POST' });
          });
          document.getElementById('subscribe').addEventListener('submit', function (event) {
            event.preventDefault();
            fetch('/api/takyon/apps/coachyard/checkout', { method: 'POST' });
          });
        </script>
        """,
        encoding="utf-8",
    )
    (profile_dir / "index.html").write_text(
        """
        <section>
          <h1>Membership</h1>
          <form id="profile">
            <input name="display_name" type="text" />
            <button type="submit">Save</button>
          </form>
        </section>
        <script>
          fetch('/api/takyon/apps/coachyard/profile');
        </script>
        """,
        encoding="utf-8",
    )
    surface = {
        "runtime_features": ["auth", "account", "profile", "checkout"],
        "metadata": {
            "subuser_app": {"app_mode": "ai_tool", "subscription_style": "monthly"},
            "customer_experience": {
                "required_routes": ["/", "/app"],
                "required_app_tabs": [],
            },
        },
        "routes": [{"path": "/"}, {"path": "/app"}, {"path": "/app/profile"}],
        "notes": "Private coaching membership.",
    }

    inventory = _bounded_product_inventory(tmp_path, "product/site", surface=surface)
    ok, blocker = _validate_product_surface_contract(inventory, surface)

    assert ok is True
    assert blocker == ""


def test_checkout_cta_without_runtime_checkout_blocks_surface(tmp_path):
    site = tmp_path / "product" / "site"
    app_dir = site / "app"
    app_dir.mkdir(parents=True)
    (site / "index.html").write_text(
        "<main><a href=\"/app\">Open app</a></main>\n",
        encoding="utf-8",
    )
    (app_dir / "index.html").write_text(
        """
        <section>
          <p>You're signed in</p>
          <h1>Start studying smarter</h1>
          <p>LearnForge Pro</p>
          <p>$9/month</p>
          <a href="/app">Subscribe - $9/month</a>
        </section>
        <script>
          fetch('/api/takyon/apps/learnforge/session');
          fetch('/api/takyon/apps/learnforge/account');
        </script>
        """,
        encoding="utf-8",
    )
    surface = {
        "runtime_features": ["auth", "account", "profile", "checkout"],
        "metadata": {
            "subuser_app": {"app_mode": "ai_tool", "subscription_style": "monthly"},
            "customer_experience": {"required_routes": ["/", "/app"]},
        },
        "routes": [{"path": "/"}, {"path": "/app"}],
        "notes": "Private study app with a monthly subscription.",
    }

    inventory = _bounded_product_inventory(tmp_path, "product/site", surface=surface)
    ok, blocker = _validate_product_surface_contract(inventory, surface)

    assert ok is False
    assert "runtime checkout rail" in blocker
    assert "/app" in blocker


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

    def fake_process(*, payload: dict[str, object], **kwargs):
        Path(str(payload["cwd"]), "index.html").write_text("<h1>Latexflow</h1>\n", encoding="utf-8")
        return types.SimpleNamespace(returncode=0, stdout=json.dumps({"success": True, "summary": "ok"}), stderr="")

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

    bootstrap = json.loads(
        handle_business_upsert_app_surface_contract(
            {
                "business": "latexflow",
                "source_path": "product/site",
                "routes": [{"path": "/"}],
                "idempotency_key": "surface-bootstrap",
            }
        )
    )

    assert bootstrap["success"] is True
    site = tmp_path / "businesses" / "latexflow" / "product" / "site"
    site.mkdir(parents=True, exist_ok=True)
    (site / "index.html").write_text("<h1>Latexflow</h1>\n", encoding="utf-8")

    result = json.loads(
        handle_business_upsert_app_surface_contract(
            {
                "business": "latexflow",
                "source_path": "product/site",
                "runtime_features": ["auth", "checkout", "records"],
                "routes": [{"path": "/"}, {"path": "/app"}],
                "idempotency_key": "surface-runtime-features",
            }
        )
    )

    assert result["success"] is True
    app = store.read(scope="business:latexflow", query="summary", include=["app"])["app"]
    assert app["surface_contract"]["runtime_features"] == ["auth", "account", "records", "checkout"]
    surface_md = (tmp_path / "businesses" / "latexflow" / "product" / "surface.md").read_text(encoding="utf-8")
    assert "Runtime rails: auth, account, records, checkout" in surface_md


def test_vite_ai_product_contract_rejects_generate_runtime_feature(tmp_path, monkeypatch):
    monkeypatch.setenv("TAKYON_HOME", str(tmp_path))
    store = TakyonStore(tmp_path)
    _commit(
        store,
        "business:latexflow",
        [{"action": "business.upsert", "business": "latexflow", "name": "Latexflow", "budget": {"amount": 25}}],
        "init-vite-generate-reject",
    )

    result = json.loads(
        handle_business_upsert_app_surface_contract(
            {
                "business": "latexflow",
                "source_path": "product/site",
                "runtime_features": ["generate"],
                "idempotency_key": "vite-ai-generate-reject",
            }
        )
    )

    assert result["success"] is False
    assert "generate is not a declarable rail on the pinned Vite scaffold" in result["error"]


def test_app_surface_contract_omits_burned_surface_theory_from_context_and_projection(tmp_path, monkeypatch):
    monkeypatch.setenv("TAKYON_HOME", str(tmp_path))
    store = TakyonStore(tmp_path)
    _commit(
        store,
        "business:latexflow",
        [{"action": "business.upsert", "business": "latexflow", "name": "Latexflow", "budget": {"amount": 25}}],
        "init-product-workflow",
    )

    result = json.loads(
        handle_business_upsert_app_surface_contract(
            {
                "business": "latexflow",
                "source_path": "product/site",
                "runtime_features": ["records"],
                "required_routes": ["/", "/app", "/workspace"],
                "surface_goal": "Turn rough notes into a saved investor update.",
                "product_workflow": {
                    "primary_job": "A founder opens the app to turn rough notes into a saved investor update.",
                },
                "idempotency_key": "surface-product-workflow",
            }
        )
    )

    assert result["success"] is True
    app = store.read(scope="business:latexflow", query="summary", include=["app"])["app"]
    context_payload = _subuser_surface_context_payload(app["surface_contract"], slug="latexflow")
    assert "customerExperience" not in context_payload
    assert "productWorkflow" not in context_payload
    assert context_payload["runtimeFeatures"] == ["records"]

    surface_md = (tmp_path / "businesses" / "latexflow" / "product" / "surface.md").read_text(encoding="utf-8")
    assert surface_md.startswith("# Product Surface")
    assert "## Shell Record" in surface_md
    assert "## Product Workflow" not in surface_md
    assert "## Theme Source" not in surface_md
    assert "## Constraints" not in surface_md


def test_app_records_rail_persists_lists_reads_and_deletes_records(tmp_path, monkeypatch):
    monkeypatch.setenv("TAKYON_HOME", str(tmp_path))
    store = TakyonStore(tmp_path)
    _commit(
        store,
        "business:latexflow",
        [{"action": "business.upsert", "business": "latexflow", "name": "Latexflow", "budget": {"amount": 25}}],
        "init-app-records",
    )
    customer_commit = _commit(
        store,
        "business:latexflow",
        [
            {
                "action": "app.customer.upsert",
                "business": "latexflow",
                "email": "founder@example.com",
                "name": "Founder",
            }
        ],
        "seed-app-customer",
    )
    app_user_id = customer_commit["results"][0]["app_user_id"]

    saved = json.loads(
        handle_business_upsert_app_record(
            {
                "business": "latexflow",
                "app_user_id": app_user_id,
                "record_type": "draft",
                "title": "Investor update",
                "data": {"body": "First saved note", "status": "ready"},
                "metadata": {"source": "test"},
                "idempotency_key": "record-save-1",
            }
        )
    )
    assert saved["success"] is True
    record_id = saved["record"]["id"]
    assert saved["record"]["type"] == "draft"
    assert saved["record"]["data"]["status"] == "ready"

    listed = json.loads(
        handle_business_list_app_records(
            {
                "business": "latexflow",
                "app_user_id": app_user_id,
                "record_type": "draft",
            }
        )
    )
    assert listed["success"] is True
    assert listed["count"] == 1
    assert listed["records"][0]["id"] == record_id

    read_back = json.loads(
        handle_business_read_app_record(
            {
                "business": "latexflow",
                "app_user_id": app_user_id,
                "record_type": "draft",
                "record_id": record_id,
            }
        )
    )
    assert read_back["success"] is True
    assert read_back["record"]["title"] == "Investor update"
    assert read_back["record"]["data"]["body"] == "First saved note"

    deleted = json.loads(
        handle_business_delete_app_record(
            {
                "business": "latexflow",
                "app_user_id": app_user_id,
                "record_type": "draft",
                "record_id": record_id,
                "idempotency_key": "record-delete-1",
            }
        )
    )
    assert deleted["success"] is True
    assert deleted["deleted"] is True

    listed_after_delete = json.loads(
        handle_business_list_app_records(
            {
                "business": "latexflow",
                "app_user_id": app_user_id,
                "record_type": "draft",
            }
        )
    )
    assert listed_after_delete["success"] is True
    assert listed_after_delete["count"] == 0


def test_app_surface_contract_ignores_burned_product_workflow_input(tmp_path, monkeypatch):
    monkeypatch.setenv("TAKYON_HOME", str(tmp_path))
    store = TakyonStore(tmp_path)
    _commit(
        store,
        "business:latexflow",
        [{"action": "business.upsert", "business": "latexflow", "name": "Latexflow", "budget": {"amount": 25}}],
        "init-product-workflow-rail-check",
    )

    result = json.loads(
        handle_business_upsert_app_surface_contract(
            {
                "business": "latexflow",
                "source_path": "product/site",
                "routes": ["/"],
                "runtime_features": [],
                "product_workflow": {
                    "primary_job": "Turn notes into a saved update.",
                    "persistence_rules": {
                        "requires_server_state": True,
                        "persistence_rail": "records",
                    },
                },
                "idempotency_key": "surface-product-workflow-derive-records",
            }
        )
    )

    assert result["success"] is True
    app = store.read(scope="business:latexflow", query="summary", include=["app"])["app"]
    assert app["surface_contract"]["runtime_features"] == []
    assert _surface_product_workflow_shape(app["surface_contract"]) == {}
    assert "productWorkflow" not in _subuser_surface_context_payload(app["surface_contract"], slug="latexflow")


def test_app_surface_contract_ignores_burned_constraints_and_workflow_input(tmp_path, monkeypatch):
    monkeypatch.setenv("TAKYON_HOME", str(tmp_path))
    store = TakyonStore(tmp_path)
    _commit(
        store,
        "business:latexflow",
        [{"action": "business.upsert", "business": "latexflow", "name": "Latexflow", "budget": {"amount": 25}}],
        "init-product-workflow-landing-check",
    )

    result = json.loads(
        handle_business_upsert_app_surface_contract(
            {
                "business": "latexflow",
                "source_path": "product/site",
                "routes": ["/"],
                "runtime_features": [],
                "constraints": {"landing_page_only": True},
                "product_workflow": {
                    "primary_job": "Save customer work.",
                    "persistence_rules": {"persistence_rail": "records"},
                },
                "idempotency_key": "surface-product-workflow-landing-only",
            }
        )
    )

    assert result["success"] is True
    app = store.read(scope="business:latexflow", query="summary", include=["app"])["app"]
    assert "constraints" not in app["surface_contract"]
    assert _surface_product_workflow_shape(app["surface_contract"]) == {}


def test_app_surface_contract_ignores_burned_customer_tabs_and_workflow_budget_input(tmp_path, monkeypatch):
    monkeypatch.setenv("TAKYON_HOME", str(tmp_path))
    store = TakyonStore(tmp_path)
    _commit(
        store,
        "business:latexflow",
        [{"action": "business.upsert", "business": "latexflow", "name": "Latexflow", "budget": {"amount": 25}}],
        "init-product-workflow-screen-budget",
    )
    result = json.loads(
        handle_business_upsert_app_surface_contract(
            {
                "business": "latexflow",
                "source_path": "product/site",
                "routes": ["/"],
                "runtime_features": [],
                "required_app_tabs": ["workspace", "history", "detail"],
                "product_workflow": {
                    "primary_job": "Save customer work.",
                    "persistence_rules": {"persistence_rail": "records"},
                    "product_budget": {"screens": {"min": 2, "max": 2}},
                },
                "idempotency_key": "surface-product-workflow-screen-budget-fail",
            }
        )
    )

    assert result["success"] is True
    app = store.read(scope="business:latexflow", query="summary", include=["app"])["app"]
    metadata = app["surface_contract"].get("metadata") or {}
    assert "customer_experience" not in metadata
    assert "product_workflow" not in metadata


def test_app_surface_contract_ignores_burned_product_workflow_range_input(tmp_path, monkeypatch):
    monkeypatch.setenv("TAKYON_HOME", str(tmp_path))
    store = TakyonStore(tmp_path)
    _commit(
        store,
        "business:latexflow",
        [{"action": "business.upsert", "business": "latexflow", "name": "Latexflow", "budget": {"amount": 25}}],
        "init-product-workflow-range-check",
    )

    result = json.loads(
        handle_business_upsert_app_surface_contract(
            {
                "business": "latexflow",
                "source_path": "product/site",
                "routes": ["/"],
                "runtime_features": [],
                "product_workflow": {
                    "primary_job": "Save customer work.",
                    "persistence_rules": {"persistence_rail": "records"},
                    "product_budget": {"screens": {"min": 5, "max": 2}},
                },
                "idempotency_key": "surface-product-workflow-range-fail",
            }
        )
    )

    assert result["success"] is True
    app = store.read(scope="business:latexflow", query="summary", include=["app"])["app"]
    assert _surface_product_workflow_shape(app["surface_contract"]) == {}


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
    assert shape["rail_state"] == {"auth": "declared", "account": "blocked", "checkout": "blocked"}


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
                "runtime_features": ["auth", "checkout", "records", "generate"],
                "product_workflow": {
                    "primary_user": "signed-in product subuser",
                    "workspace_model": "one user = one workspace",
                    "primary_job": "A founder opens the app to turn rough notes into a saved investor update.",
                    "core_loop": {
                        "input": "paste rough company notes",
                        "action": "generate a cleaned investor update draft",
                        "result": "show the finished update on screen",
                        "save_record": True,
                        "return_to_record_later": True,
                    },
                    "persistence_rules": {
                        "requires_server_state": True,
                        "persistence_rail": "records",
                        "survives_sign_out": True,
                        "truthful_empty_state": True,
                        "reopenable_history": True,
                        "no_local_only_state": True,
                    },
                    "product_budget": {
                        "screens": {"min": 2, "max": 5},
                        "entity_types": {"min": 3, "max": 6},
                        "backend_actions": {"min": 5, "max": 10},
                        "ai_flows": {"min": 0, "max": 2},
                    },
                    "first_run": {
                        "strategy": "guided_first_run",
                        "empty_state_required": True,
                        "pending_state_required": True,
                        "error_state_required": True,
                    },
                    "acceptance_tests": [
                        "completed loop creates a saved record",
                        "sign out and back in preserves the record",
                    ],
                    "not_now": ["team collaboration", "sharing"],
                },
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
    assert "The surface contract records an MVP-complete product workflow for the gated app." in instruction
    assert "Primary user: signed-in product subuser" in instruction
    assert "Workspace model: one user = one workspace" in instruction
    assert "Closed-loop requirement: paste rough company notes -> generate a cleaned investor update draft -> show the finished update on screen -> save a real record -> return to it later" in instruction
    assert "Persistence requirements: use the `records` persistence rail, server-side state, survive sign-out/sign-in, truthful empty state, reopenable history, no local-only pretend state" in instruction
    assert "Complexity target: screens 2-5; entity types 3-6; backend actions 5-10; AI flows 0-2" in instruction
    assert "First-run requirements: strategy `guided_first_run`, truthful empty state, pending states, error states" in instruction
    assert "Acceptance tests that must read back true before you call this done:" in instruction
    assert "completed loop creates a saved record" in instruction
    assert "Explicitly out of scope for this MVP: team collaboration, sharing" in instruction
    assert "Declared runtime-backed features: auth, account, records, checkout, generate" in instruction
    assert "Runtime API base fallback: /api/takyon/apps/latexflow" in instruction
    assert "account (owner: takyon-app-runtime)" in instruction
    assert "checkout (owner: takyon-app-runtime)" in instruction
    assert "api.openai.com" in instruction
    assert "`OPENAI_API_KEY`" in instruction
    assert "Client code must not call `/generate` directly" in instruction
    assert "Canonical tools: business_read_app_account" in instruction
    assert "Canonical tools: business_list_app_records, business_read_app_record, business_upsert_app_record, business_delete_app_record" in instruction
    assert "Canonical tools: business_create_app_checkout, business_record_stripe_webhook" in instruction
    assert "Reachable runtime endpoints: POST /auth/session on product hosts or POST /api/takyon/apps/latexflow/auth/session off-host" in instruction
    assert "Reachable runtime endpoints: GET /account on product hosts or GET /api/takyon/apps/latexflow/account off-host" in instruction
    assert "Reachable runtime endpoints: GET /records on product hosts or GET /api/takyon/apps/latexflow/records off-host; POST /records on product hosts or POST /api/takyon/apps/latexflow/records off-host; GET /records/<type>/<id> on product hosts or GET /api/takyon/apps/latexflow/records/<type>/<id> off-host; POST /records/<type>/<id> on product hosts or POST /api/takyon/apps/latexflow/records/<type>/<id> off-host; DELETE /records/<type>/<id> on product hosts or DELETE /api/takyon/apps/latexflow/records/<type>/<id> off-host" in instruction
    assert "Reachable runtime endpoints: POST /checkout on product hosts or POST /api/takyon/apps/latexflow/checkout off-host" in instruction
    assert "Reachable runtime endpoints: POST /generate on product hosts or POST /api/takyon/apps/latexflow/generate off-host" in instruction
    assert "Treat POST /generate on product hosts or POST <runtime_api_base>/generate off-host as the public product contract for AI generation" in instruction
    assert "product code should not call providers or internal authority endpoints directly" in instruction
    assert "never put operator/admin routes or `tk_`/`tkg_` operator tokens in product code" in instruction
    assert "Customer identity and all account/session/entitlement/checkout/usage truth come only from the declared app runtime rails" in instruction
    assert "Frontend-local, non-authoritative features that do not persist account/business truth and do not call provider or authority endpoints may be implemented without declaring a runtime rail." in instruction
    # The minimized worker contract carries the positive obligation; the §25.4 fear and
    # free-tier prose is gone — integrity now lives in the rails, validators, and refresh gate.
    assert "Your overriding obligation is that the product's primary job works for real." in instruction
    assert "Use the declared shared rails and named actions for backend behavior." in instruction
    assert "do not fake browser-only sessions" not in instruction
    assert "Do not use localStorage" not in instruction
    assert "Do not write or imply `Free`" not in instruction


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
    style_design_file = skills_dir / "creative" / "claude-design-doodle" / "DESIGN.md"
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
    style_design_file.write_text(
        """# Design System Inspired by Doodle

## 4. Spacing & Grid

- use a broad desktop grid instead of a narrow centered island

## 5. Layout & Composition

- the right rail should feel visually substantial

## 9. Anti-patterns

- do not turn the page into long paragraph blocks
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
    assert "[Hermes design reference: claude-design-doodle / DESIGN.md]" in instruction
    assert "required design contract" in instruction
    assert "Pick one coherent style skill." in instruction
    assert "playful still has to ship" in instruction
    assert "right rail should feel visually substantial" in instruction


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

    def fake_process(*, payload: dict[str, object], **kwargs):
        Path(str(payload["cwd"]), "index.html").write_text("<h1>Latexflow</h1>\n", encoding="utf-8")
        return types.SimpleNamespace(returncode=0, stdout=json.dumps({"success": True, "summary": "ok"}), stderr="")

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
    assert app["surface_contract"]["public_url"] == "https://latexflow.coscale.app/"
    receipt_path = tmp_path / "businesses" / "latexflow" / str(result["surface_refresh"]["receipt_path"])
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert receipt["guidance_skills"] == []
    assert receipt["guidance_selection_reason"] == ""


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

    def fake_process(*, payload: dict[str, object], **kwargs):
        Path(str(payload["cwd"]), "index.html").write_text("<h1>Latexflow</h1>\n", encoding="utf-8")
        return types.SimpleNamespace(returncode=0, stdout=json.dumps({"success": True, "summary": "ok"}), stderr="")

    def fake_finalize(**kwargs: object) -> dict[str, object]:
        captured["install"] = kwargs["install"]
        return {
            "status": "passed",
            "publish": {
                "status": "published",
                "public_url": "https://latexflow.coscale.app/",
                "blocker": "",
            },
            "receipt_path": "metrics/receipts/product-surface/test.json",
            "inventory": {},
        }

    monkeypatch.setattr(takyon_core, "_require_api_access", lambda *args, **kwargs: None)
    monkeypatch.setattr(takyon_core, "_should_run_claude_agent_in_docker", lambda _workspace_rel: False)
    monkeypatch.setattr(takyon_core, "_resolve_runtime_executable", lambda name: "/usr/bin/node" if name == "node" else None)
    monkeypatch.setattr(takyon_core, "_ensure_repo_node_dependencies", lambda packages: {"success": True})
    monkeypatch.setattr(takyon_core, "_run_claude_agent_task_process", fake_process)
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


def test_claude_agent_task_prefixes_forbidden_surface_blockers_with_blocked(tmp_path, monkeypatch):
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
                "runtime_features": ["auth", "checkout"],
                "required_routes": ["/", "/app"],
            }
        ],
        "surface-repair-retry",
    )

    payloads: list[dict[str, object]] = []

    def fake_run(command, *, input=None, **kwargs):
        if len(command) > 1 and str(command[1]).endswith("takyon-claude-agent-task.mjs"):
            payload = json.loads(input or "{}")
            payloads.append(payload)
            return types.SimpleNamespace(returncode=0, stdout=json.dumps({"success": True, "summary": "ok"}), stderr="")
        return types.SimpleNamespace(returncode=0, stdout="v99.0.0\n", stderr="")

    def fake_finalize(**kwargs: object) -> dict[str, object]:
        blocker = (
            "product source violates runtime authority boundaries:\n"
            "- product source imports or constructs an AI provider SDK directly at "
            "product/site/actions/coach.ts:133; issue: provider sdk import; snippet: "
            "import OpenAI from 'openai';; remove the SDK import and call your declared action "
            "over `ctx.base_url` + `ctx.session_token`, which brokers the generate rail server-side"
        )
        return {
            "status": "blocked",
            "source_path": "product/site",
            "checks": [],
            "publish": {
                "status": "blocked",
                "public_url": "",
                "publish_target": "https://latexflow.coscale.app/",
                "publish_source_path": "product/site",
                "blocker": blocker,
            },
            "inventory": {},
            "blockers": [
                "product source imports or constructs an AI provider SDK directly at "
                "product/site/actions/coach.ts:133; issue: provider sdk import; snippet: "
                "import OpenAI from 'openai';; remove the SDK import and call your declared action "
                "over `ctx.base_url` + `ctx.session_token`, which brokers the generate rail server-side"
            ],
            "receipt_path": str(kwargs["receipt_path"]),
            "blocker": blocker,
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

    assert result["success"] is False
    assert result["worker_attempts"] == 1
    assert len(payloads) == 1
    assert result["blocked"] is True
    assert str(result["error"]).startswith("BLOCKED: product source violates runtime authority boundaries:")
    assert str(result["summary"]).startswith("BLOCKED: product source violates runtime authority boundaries:")
    assert "provider sdk import" in str(result["error"])


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


def test_next_product_publish_is_blocked_without_vite_dist_output(tmp_path, monkeypatch):
    business_root = tmp_path / "businesses" / "latexflow"
    site = business_root / "product" / "site"
    site.mkdir(parents=True)
    (site / ".next").mkdir()
    (site / ".next" / "BUILD_ID").write_text("build-1\n", encoding="utf-8")
    (site / ".next" / "build-manifest.json").write_text("{}\n", encoding="utf-8")
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
    result = takyon_core._publish_product_surface_path(
        business_root=business_root,
        slug="latexflow",
        source_path="product/site",
        publish_target="https://latexflow.coscale.app/",
    )

    assert result["status"] == "blocked"
    assert "dist/index.html" in result["blocker"]


def test_next_product_publish_does_not_handoff_to_activation_host(tmp_path, monkeypatch):
    business_root = tmp_path / "businesses" / "latexflow"
    site = business_root / "product" / "site"
    site.mkdir(parents=True)
    (site / ".next").mkdir()
    (site / ".next" / "BUILD_ID").write_text("build-1\n", encoding="utf-8")
    (site / ".next" / "build-manifest.json").write_text("{}\n", encoding="utf-8")
    next_bin = site / "node_modules" / "next" / "dist" / "bin"
    next_bin.mkdir(parents=True)
    (next_bin / "next").write_text("#!/usr/bin/env node\nconsole.log('next');\n", encoding="utf-8")
    (next_bin / "next").chmod(0o755)
    (site / "node_modules" / ".bin").mkdir(parents=True)
    (site / "node_modules" / ".bin" / "next").symlink_to("../next/dist/bin/next")
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
    result = takyon_core._publish_product_surface_path(
        business_root=business_root,
        slug="latexflow",
        source_path="product/site",
        publish_target="https://latexflow.coscale.app/",
    )

    assert result["status"] == "blocked"
    assert "dist/index.html" in result["blocker"]
    assert (site / ".next").exists()
    assert (site / "node_modules").exists()

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

    assert verification["status"] == "blocked"
    assert "pinned Vite scaffold" in verification["error"]


def test_refresh_next_product_is_blocked(tmp_path, monkeypatch):
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

    verification = _refresh_product_surface_path(business_root, "product/site", install=True)

    assert verification["status"] == "blocked"
    assert "Next/AppKit product trees are unsupported" in verification["error"]
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


def test_anthropic_requirement_allows_keyfree_safebox_broker(monkeypatch):
    for key in ("ANTHROPIC_API_KEY", "ANTHROPIC_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("TAKYON_CLAUDE_AGENT_BROKER", "1")
    monkeypatch.setenv("TAKYON_CLAUDE_AGENT_BROKER_URL", "http://10.116.0.2:8000")

    result = takyon_core._require_api_access(
        {"action": "agent.record", "business": "latexflow", "requires_api": ["anthropic"]}
    )

    assert result["missing_credentials_suppressed"] == []


def test_anthropic_requirement_still_fails_without_raw_key_or_broker(monkeypatch):
    for key in (
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_TOKEN",
        "CLAUDE_CODE_OAUTH_TOKEN",
        "TAKYON_CLAUDE_AGENT_BROKER",
        "TAKYON_CLAUDE_AGENT_BROKER_URL",
        "TAKYON_SAFEBOX_URL",
    ):
        monkeypatch.delenv(key, raising=False)

    with pytest.raises(TakyonError, match="missing API/env"):
        takyon_core._require_api_access(
            {"action": "agent.record", "business": "latexflow", "requires_api": ["anthropic"]}
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


def test_create_schedule_can_defer_first_ceo_wake(tmp_path, pg_store_dsn):
    from plugins.takyon import wakes

    store = TakyonStore(tmp_path, database_url=pg_store_dsn)
    _commit(
        store,
        "business:crm",
        [{"action": "business.upsert", "business": "crm", "name": "CRM"}],
        "init-deferred-wake-business",
    )
    before = datetime.now(timezone.utc)
    result = _commit(
        store,
        "business:crm",
        [
            {
                "action": "cron.ensure_ceo_wakeup",
                "business": "crm",
                "schedule": "every 6h",
                "defer_first_run": True,
            }
        ],
        "init-deferred-wake-schedule",
    )

    with store._connect() as conn:
        with store._leaf_conn(conn) as leaf:
            sched = wakes.get_wake_schedule(leaf, "crm")
            assert sched is not None
            assert wakes.dispatch_due_wakes(leaf) == 0

    assert sched.next_run_at >= before + timedelta(hours=5, minutes=59)
    assert sched.next_run_at <= before + timedelta(hours=6, minutes=1)
    assert result["results"][0]["defer_first_run"] is True


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
        handle_business_upsert_app_customer(
            {
                "business": "latexflow",
                "email": "customer@example.com",
            }
        )
    )

    assert result["success"] is False
    assert "marketing-only" in result["error"]


def test_sqlite_seeded_app_session_reads_unentitled_account(tmp_path, monkeypatch):
    monkeypatch.setenv("TAKYON_HOME", str(tmp_path))
    store = TakyonStore(tmp_path)
    _commit(
        store,
        "business:authrail",
        [{"action": "business.upsert", "business": "authrail", "name": "Authrail", "mode": "test"}],
        "init-authrail",
    )

    _user, session_token = _sqlite_app_session(store, "authrail", "tester@example.com")
    account = json.loads(
        handle_business_read_app_account(
            {
                "business": "authrail",
                "session_token": session_token,
            }
        )
    )

    assert account["success"] is True
    assert account["user"]["email"] == "tester@example.com"
    assert account["user"]["tier"] == "unentitled"
    assert account["entitlements"] == []


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
    assert checkout["url"] == checkout["checkout_url"]


def test_monthly_plan_upsert_rejects_included_ai_budget_above_price(tmp_path, monkeypatch):
    monkeypatch.setenv("TAKYON_HOME", str(tmp_path))
    store = TakyonStore(tmp_path)
    _commit(
        store,
        "business:budgetcap",
        [{"action": "business.upsert", "business": "budgetcap", "name": "Budgetcap", "mode": "test"}],
        "init-budgetcap",
    )

    result = _commit(
        store,
        "business:budgetcap",
        [
            {
                "action": "app.plan.upsert",
                "business": "budgetcap",
                "plan_key": "monthly",
                "tier": "paid",
                "price_cents": 1900,
                "currency": "usd",
                "billing_interval": "month",
                "included_ai_budget_microusd": 19_000_001,
            }
        ],
        "budgetcap-plan",
    )["results"][0]

    assert result["success"] is False
    assert "included_ai_budget_microusd must be between 0 and the monthly plan price" in result["error"]


def test_monthly_plan_price_drop_requires_existing_budget_to_fit_new_price(tmp_path, monkeypatch):
    monkeypatch.setenv("TAKYON_HOME", str(tmp_path))
    store = TakyonStore(tmp_path)
    _commit(
        store,
        "business:budgetsync",
        [{"action": "business.upsert", "business": "budgetsync", "name": "Budgetsync", "mode": "test"}],
        "init-budgetsync",
    )
    seed = _commit(
        store,
        "business:budgetsync",
        [
            {
                "action": "app.plan.upsert",
                "business": "budgetsync",
                "plan_key": "monthly",
                "tier": "paid",
                "price_cents": 1900,
                "currency": "usd",
                "billing_interval": "month",
                "included_ai_budget_microusd": 5_000_000,
            }
        ],
        "budgetsync-seed-plan",
    )["results"][0]
    assert seed["success"] is True

    result = _commit(
        store,
        "business:budgetsync",
        [
            {
                "action": "app.plan.upsert",
                "business": "budgetsync",
                "plan_key": "monthly",
                "tier": "paid",
                "price_cents": 300,
                "currency": "usd",
                "billing_interval": "month",
            }
        ],
        "budgetsync-price-drop",
    )["results"][0]

    assert result["success"] is False
    assert "included_ai_budget_microusd must be between 0 and the monthly plan price" in result["error"]


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
    assert checkout["url"] == checkout["checkout_url"]


def test_pg_checkout_webhook_updates_account_to_paid(tmp_path, monkeypatch):
    monkeypatch.setenv("TAKYON_HOME", str(tmp_path))
    # The sub-user app webhook is now verified server-side on the safebox; run this handler test as
    # the safebox host so verify_stripe_app_webhook reads STRIPE_WEBHOOK_SECRET locally (no remote).
    monkeypatch.setenv("TAKYON_HOST_ROLE", "safebox")
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

    _user, session_token = _sqlite_app_session(store, "paidacct", "paid@example.com", name="Paid User")
    account_before = json.loads(
        handle_business_read_app_account(
            {
                "business": "paidacct",
                "session_token": session_token,
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
                "session_token": session_token,
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


def test_pg_account_read_recovers_paid_checkout_without_webhook(tmp_path, monkeypatch):
    from plugins.takyon import stripe_util

    monkeypatch.setenv("TAKYON_HOME", str(tmp_path))
    store = TakyonStore(tmp_path)
    _commit(
        store,
        "business:recoveracct",
        [{"action": "business.upsert", "business": "recoveracct", "name": "Recoveracct", "mode": "live"}],
        "init-recoveracct",
    )

    _user, session_token = _sqlite_app_session(store, "recoveracct", "recover@example.com", name="Recover User")
    account_before = json.loads(
        handle_business_read_app_account(
            {
                "business": "recoveracct",
                "session_token": session_token,
            }
        )
    )

    leaves = store._app_leaves()
    with store._connect() as conn:
        with store._leaf_conn(conn) as leaf:
            intent = leaves["payments"].create_checkout_intent(
                leaf,
                "recoveracct",
                plan_key="monthly",
                client_reference_id="recoveracct-ref",
                app_user_id=account_before["user"]["id"],
                customer_email="recover@example.com",
                status="pending",
            )
            leaves["payments"].attach_checkout_session(
                leaf,
                intent_id=intent.id,
                stripe_checkout_session_id="cs_recoveracct",
                checkout_url="https://stripe.test/cs_recoveracct",
                status="pending",
            )

    def fake_stripe_request(path, params, *, method="POST"):
        assert method == "GET"
        if path == "checkout/sessions/cs_recoveracct":
            return {
                "id": "cs_recoveracct",
                "object": "checkout.session",
                "mode": "subscription",
                "status": "complete",
                "payment_status": "paid",
                "currency": "usd",
                "amount_subtotal": 1900,
                "amount_total": 1900,
                "customer": "cus_recoveracct",
                "subscription": "sub_recoveracct",
                "customer_details": {"email": "recover@example.com"},
                "customer_email": "recover@example.com",
                "client_reference_id": "recoveracct-ref",
                "metadata": {"checkout_intent_id": intent.id},
                "created": 1_700_000_123,
            }
        if path == "subscriptions/sub_recoveracct":
            return {
                "id": "sub_recoveracct",
                "object": "subscription",
                "status": "active",
                "customer": "cus_recoveracct",
                "current_period_end": 1_700_600_000,
                "cancel_at_period_end": False,
            }
        raise AssertionError(f"unexpected Stripe request: {path}")

    monkeypatch.setattr(stripe_util, "stripe_request", fake_stripe_request)

    account_after = json.loads(
        handle_business_read_app_account(
            {
                "business": "recoveracct",
                "session_token": session_token,
            }
        )
    )

    assert account_after["success"] is True
    assert account_after["user"]["tier"] == "paid"
    assert account_after["revenue"]["amount_paid_cents"] == 1900
    assert any(
        ent["tier"] == "paid"
        and ent["status"] == "active"
        and ent["plan_key"] == "monthly"
        and ent["stripe_subscription_id"] == "sub_recoveracct"
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
    assert deletion["subuser_product_site"] == {
        "target": "root@134.209.123.8",
        "path": "/opt/takyon/.takyon/product-sites/latexflow",
    }
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
    subuser_delete_calls: list[str] = []
    monkeypatch.setattr(
        takyon_core,
        "_delete_subuser_product_site",
        lambda slug: (
            subuser_delete_calls.append(slug)
            or {
                "target": "root@134.209.123.8",
                "path": f"/opt/takyon/.takyon/product-sites/{slug}",
                "removed": True,
                "status": "removed",
            }
        ),
    )
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
    assert deletion["subuser_product_site"]["removed"] is True
    assert subuser_delete_calls == ["latexflow"]
    assert not (tmp_path / "businesses" / "latexflow").exists()
    assert not published.exists()
    assert store.read(scope="global", query="list_businesses")["businesses"] == []
    assert cron_jobs.list_jobs(include_disabled=True) == []


def test_delete_business_removes_product_service_runtime(tmp_path, monkeypatch):
    monkeypatch.setenv("TAKYON_PRODUCT_DEPLOY_DRY_RUN", "1")
    monkeypatch.setenv("TAKYON_PRODUCT_SERVICE_ROOT", str(tmp_path / "product-services"))
    monkeypatch.setenv("TAKYON_PRODUCT_SYSTEMD_DIR", str(tmp_path / "systemd"))
    monkeypatch.setenv("TAKYON_PRODUCT_CADDYFILE", str(tmp_path / "Caddyfile"))
    monkeypatch.setattr(
        takyon_core,
        "_delete_subuser_product_site",
        lambda slug: {
            "target": "root@134.209.123.8",
            "path": f"/opt/takyon/.takyon/product-sites/{slug}",
            "removed": True,
            "status": "removed",
        },
    )

    store = TakyonStore(tmp_path)
    _commit(
        store,
        "business:latexflow",
        [{"action": "business.upsert", "business": "latexflow", "name": "Latexflow"}],
        "init-delete-product-service",
    )
    _commit(
        store,
        "business:latexflow",
        [{"action": "artifact.write", "path": "product/spec.md", "content": "# Spec\n"}],
        "write-delete-product-service",
    )

    service_root = tmp_path / "product-services" / "latexflow"
    service_root.mkdir(parents=True)
    (service_root / "server.js").write_text("console.log('live');\n", encoding="utf-8")

    systemd_dir = tmp_path / "systemd"
    systemd_dir.mkdir(parents=True)
    service_file = systemd_dir / "takyon-product-latexflow.service"
    service_file.write_text("[Unit]\nDescription=Takyon Product - latexflow\n", encoding="utf-8")

    caddyfile = tmp_path / "Caddyfile"
    caddyfile.write_text(
        "latexflow.coscale.app {\n"
        "    handle {\n"
        "        reverse_proxy 127.0.0.1:4010\n"
        "    }\n"
        "}\n",
        encoding="utf-8",
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
        "delete-product-service",
    )
    deletion = result["results"][0]

    assert deletion["product_service"]["service_root"]["removed"] is True
    assert deletion["product_service"]["service_file"]["removed"] is True
    assert deletion["product_service"]["caddy_route"]["removed"] is True
    assert deletion["subuser_product_site"]["removed"] is True
    assert not service_root.exists()
    assert not service_file.exists()
    assert "latexflow.coscale.app" not in caddyfile.read_text(encoding="utf-8")


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


def test_sync_business_workspace_remote_skips_supabase_push_on_local_mac(tmp_path, monkeypatch):
    monkeypatch.setenv("TAKYON_HOME", str(tmp_path))
    monkeypatch.setenv("TAKYON_STORAGE_BACKEND", "supabase_s3")
    monkeypatch.delenv("TAKYON_ALLOW_REMOTE_STORAGE_SYNC_OUTSIDE_VPS", raising=False)
    monkeypatch.delenv("TAKYON_HOST_ROLE", raising=False)
    monkeypatch.setattr(takyon_core.platform, "system", lambda: "Darwin")

    store = TakyonStore(tmp_path)
    workspace = store._business_root("latexflow", sync=False)
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "research").mkdir(parents=True, exist_ok=True)
    (workspace / "research" / "spec.md").write_text("# Spec\n", encoding="utf-8")

    sync_calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []
    monkeypatch.setattr(
        TakyonStore,
        "_workspace_storage_backend",
        lambda self: types.SimpleNamespace(name="supabase_s3"),
    )
    monkeypatch.setattr(
        storage,
        "sync_up",
        lambda *args, **kwargs: sync_calls.append((args, kwargs)),
    )

    store._sync_business_workspace_remote("latexflow")

    assert sync_calls == []


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


def test_claude_agent_task_rejects_surface_source_path_drift_before_worker_launch(tmp_path, monkeypatch):
    monkeypatch.setenv("TAKYON_HOME", str(tmp_path))
    store = TakyonStore(tmp_path)
    _commit(
        store,
        "business:alpha",
        [{"action": "business.upsert", "business": "alpha", "name": "Alpha"}],
        "init-alpha-source-path-drift",
    )
    _commit(
        store,
        "business:alpha",
        [{"action": "app.surface.upsert", "business": "alpha", "status": "active", "source_path": "product/site", "routes": ["/"]}],
        "surface-alpha-source-path-drift",
    )

    launched = False

    def unexpected_run(*args, **kwargs):
        nonlocal launched
        launched = True
        raise AssertionError("worker should not launch when the surface source path drifts")

    monkeypatch.setattr(takyon_core, "_require_api_access", lambda *args, **kwargs: None)
    monkeypatch.setattr(takyon_core.subprocess, "run", unexpected_run)

    result = json.loads(
        handle_business_claude_agent_task(
            {
                "business": "alpha",
                "workspace": "product/longer",
                "instruction": "Build the product surface.",
                "idempotency_key": "claude-source-path-drift",
            }
        )
    )

    assert result["success"] is False
    assert "anchored to 'product/site'" in str(result.get("error") or "")
    assert launched is False


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
                "public_url": "https://alpha.coscale.app/",
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


def test_business_static_ad_generate_rejects_test_mode(tmp_path, monkeypatch):
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

    assert result["success"] is False
    assert "requires a live business" in result["error"]


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
    synced: list[str] = []
    monkeypatch.setattr(
        TakyonStore,
        "_sync_business_workspace_remote",
        lambda self, slug: synced.append(slug),
    )

    result = json.loads(
        handle_business_static_ad_generate(
            {
                "business": "frameforge",
                "input_path": "research/example-spec.json",
                "slug": "frameforge-static-live",
                "budget_bucket": "meta",
                "idempotency_key": "frameforge-static-live-v1",
            }
        )
    )

    assert result["success"] is True
    assert result["status"] == "created"
    assert result["balance_credits"] == 8
    assert synced == ["frameforge"]
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


def test_business_generate_logo_rejects_test_mode(tmp_path, monkeypatch):
    monkeypatch.setenv("TAKYON_HOME", str(tmp_path))
    store = TakyonStore(tmp_path)
    _commit(
        store,
        "business:lumen",
        [{"action": "business.upsert", "business": "lumen", "name": "Lumen", "mode": "test"}],
        "init-lumen-test",
    )

    result = json.loads(
        handle_business_generate_logo(
            {
                "business": "lumen",
                "idempotency_key": "lumen-logo-test-v1",
            }
        )
    )

    assert result["success"] is False
    assert "requires a live business" in result["error"]


def test_business_generate_logo_zero_credits_does_not_call_provider(tmp_path, monkeypatch):
    monkeypatch.setenv("TAKYON_HOME", str(tmp_path))
    store = TakyonStore(tmp_path)
    _commit(
        store,
        "business:lumen",
        [{"action": "business.upsert", "business": "lumen", "name": "Lumen", "mode": "live"}],
        "init-lumen-live",
    )
    # No creative credits granted: the preflight gate must block before the
    # authority gateway (and thus the provider) is ever reached.
    called: list[str] = []
    monkeypatch.setattr(
        takyon_core,
        "_call_creative_runtime_gateway",
        lambda endpoint, payload: called.append(endpoint) or {"success": True},
    )

    result = json.loads(
        handle_business_generate_logo(
            {
                "business": "lumen",
                "idempotency_key": "lumen-logo-zero-v1",
            }
        )
    )

    assert result["success"] is False
    assert result["status"] == "blocked_insufficient_creative_credits"
    assert called == []  # provider/authority route never invoked


def test_business_generate_logo_live_charges_credits_and_writes_receipt(tmp_path, monkeypatch):
    monkeypatch.setenv("TAKYON_HOME", str(tmp_path))
    store = TakyonStore(tmp_path)
    _commit(
        store,
        "business:lumen",
        [{"action": "business.upsert", "business": "lumen", "name": "Lumen", "mode": "live"}],
        "init-lumen-live2",
    )
    _grant_creative_credits(store, "lumen", 10, "lumen-grant")
    captured: dict[str, Any] = {}

    def _fake_gateway(endpoint, payload):
        captured["endpoint"] = endpoint
        captured["payload"] = payload
        return {
            "success": True,
            "status": "created",
            "asset_path": "product/brand/logos/lumen/logo.png",
            "prompt": "icon prompt",
            "provider": "google",
            "model": "gemini-2.5-flash-image",
            "provider_cost_usd": 0.039,
            "credits_charged": 2,
            "balance_credits": 8,
            "reserved_credits": 0,
        }

    monkeypatch.setattr(takyon_core, "_call_creative_runtime_gateway", _fake_gateway)
    synced: list[str] = []
    monkeypatch.setattr(
        TakyonStore,
        "_sync_business_workspace_remote",
        lambda self, slug: synced.append(slug),
    )

    result = json.loads(
        handle_business_generate_logo(
            {
                "business": "lumen",
                "idempotency_key": "lumen-logo-live-v1",
                "business_context": {"category": "design tools", "tone": "playful"},
            }
        )
    )

    assert result["success"] is True
    assert result["status"] == "created"
    assert result["asset_path"] == "product/brand/logos/lumen/logo.png"
    assert result["provider_cost_usd"] == 0.039
    assert captured["endpoint"] == "logo-render"
    # brand context is passed through to the authority route
    assert captured["payload"]["business_context"]["category"] == "design tools"
    assert synced == ["lumen"]
    receipt = json.loads(
        (
            tmp_path
            / "businesses"
            / "lumen"
            / "product"
            / "brand"
            / "logos"
            / "lumen"
            / "receipt.json"
        ).read_text(encoding="utf-8")
    )
    assert receipt["credits_charged"] == 2
    assert receipt["provider_cost_usd"] == 0.039
    assert receipt["model"] == "gemini-2.5-flash-image"


def test_business_generate_logo_unconfigured_key_surfaces_blocked(tmp_path, monkeypatch):
    monkeypatch.setenv("TAKYON_HOME", str(tmp_path))
    store = TakyonStore(tmp_path)
    _commit(
        store,
        "business:lumen",
        [{"action": "business.upsert", "business": "lumen", "name": "Lumen", "mode": "live"}],
        "init-lumen-live3",
    )
    _grant_creative_credits(store, "lumen", 10, "lumen-grant3")

    def _fake_gateway(endpoint, payload):
        # The authority route returns 503 gemini_image_unconfigured; the
        # transport helper raises a TakyonError with that detail.
        raise takyon_core.TakyonError(
            "creative authority runtime failed (503): gemini_image_unconfigured"
        )

    monkeypatch.setattr(takyon_core, "_call_creative_runtime_gateway", _fake_gateway)

    result = json.loads(
        handle_business_generate_logo(
            {
                "business": "lumen",
                "idempotency_key": "lumen-logo-503-v1",
            }
        )
    )

    assert result["success"] is False
    assert result["status"] == "blocked_authority_runtime_unavailable"
    assert "gemini_image_unconfigured" in result["error"]


def test_logo_provider_cost_is_priced_in_usage_pricing():
    from agent import usage_pricing

    entry = usage_pricing._OFFICIAL_DOCS_PRICING.get(("google", "gemini-2.5-flash-image"))
    assert entry is not None
    assert entry.request_cost is not None
    # core resolves the exact priced cost (no second hardcoded table)
    assert takyon_core._logo_provider_cost_usd() == float(entry.request_cost)


def test_gemini_alias_resolves_logo_key_requirement():
    # business_generate_logo declares requires_api=["gemini"]; the alias must
    # map to the Safebox-backed key names so credential gating can resolve it.
    assert takyon_core._API_ENV_ALIASES["gemini"] == (
        "TAKYON_GEMINI_API_KEY",
        "GEMINI_API_KEY",
        "GOOGLE_API_KEY",
    )


def test_business_generate_logo_registered_as_authority_tool():
    assert "business_generate_logo" in takyon_core.TAKYON_AUTHORITY_TOOL_NAMES
    names = [d["name"] for d in takyon_core.TAKYON_TOOL_DEFINITIONS]
    assert "business_generate_logo" in names


def test_business_seo_add_property_registered_as_authority_tool():
    assert "business_seo_add_property" in takyon_core.TAKYON_AUTHORITY_TOOL_NAMES
    names = [d["name"] for d in takyon_core.TAKYON_TOOL_DEFINITIONS]
    assert "business_seo_add_property" in names


class _FakeGscSites:
    """Duck-typed Search Console sites() resource backed by an in-memory list."""

    def __init__(self, sites: list[dict[str, Any]], added: list[str]):
        self._sites = sites
        self._added = added

    def list(self):
        outer = self

        class _Exec:
            def execute(self):
                return {"siteEntry": list(outer._sites)}

        return _Exec()

    def add(self, siteUrl: str):
        outer = self

        class _Exec:
            def execute(self):
                outer._added.append(siteUrl)
                outer._sites.append(
                    {"siteUrl": siteUrl, "permissionLevel": "siteUnverifiedUser"}
                )
                return {}

        return _Exec()


class _FakeGscService:
    def __init__(self, sites: list[dict[str, Any]]):
        self._sites = sites
        self.added: list[str] = []

    def sites(self):
        return _FakeGscSites(self._sites, self.added)


def test_seo_add_gsc_property_registers_subdomain_under_owner_parent():
    service = _FakeGscService(
        [{"siteUrl": "sc-domain:coscale.app", "permissionLevel": "siteOwner"}]
    )
    result = takyon_core._seo_add_gsc_property(service, "https://acme.coscale.app")
    assert result == {
        "success": True,
        "site_url": "https://acme.coscale.app/",
        "parent": "sc-domain:coscale.app",
        "already_existed": False,
    }
    assert service.added == ["https://acme.coscale.app/"]


def test_seo_add_gsc_property_is_idempotent_when_already_present():
    service = _FakeGscService(
        [
            {"siteUrl": "sc-domain:coscale.app", "permissionLevel": "siteOwner"},
            {"siteUrl": "https://acme.coscale.app/", "permissionLevel": "siteOwner"},
        ]
    )
    result = takyon_core._seo_add_gsc_property(service, "https://acme.coscale.app/")
    assert result["already_existed"] is True
    assert service.added == []


def test_seo_add_gsc_property_rejects_url_outside_owner_verified_parent():
    service = _FakeGscService(
        [{"siteUrl": "sc-domain:coscale.app", "permissionLevel": "siteOwner"}]
    )
    with pytest.raises(TakyonError):
        takyon_core._seo_add_gsc_property(service, "https://acme.example.com/")


def test_seo_add_gsc_property_requires_http_scheme():
    service = _FakeGscService(
        [{"siteUrl": "sc-domain:coscale.app", "permissionLevel": "siteOwner"}]
    )
    with pytest.raises(TakyonError):
        takyon_core._seo_add_gsc_property(service, "ftp://acme.coscale.app/")


def test_handle_business_seo_add_property_requires_site_url():
    out = json.loads(takyon_core.handle_business_seo_add_property({}))
    assert out["success"] is False
    assert "site_url" in out["error"].lower()


def test_seo_build_credentials_reads_service_account_from_safebox(monkeypatch):
    # The credential source is the same canonical Safebox alias used by
    # business_register_search_console, not the retired GSC_SERVICE_ACCOUNT_KEY
    # alias and not a key file on disk. With no secret configured it fails
    # closed before any Google client import, so this holds regardless of
    # google-auth presence.
    seen: list[tuple[str, ...]] = []

    def _fake_first(*keys: str) -> str:
        seen.append(tuple(keys))
        return ""

    monkeypatch.setattr(takyon_core.safebox, "first_env_backed_value", _fake_first)
    with pytest.raises(TakyonError) as excinfo:
        takyon_core._seo_build_credentials(["https://www.googleapis.com/auth/webmasters"])
    assert seen == [("TAKYON_GSC_SERVICE_ACCOUNT_KEY",)]
    assert "TAKYON_GSC_SERVICE_ACCOUNT_KEY" in str(excinfo.value)


def test_business_seo_query_data_registered_in_plain_takyon_toolset():
    # Read-only data tool: belongs in the general "takyon" toolset alongside the
    # other read tools the SEO skill requires (requires_toolsets: [takyon]),
    # NOT the side-effecting "takyon-authority" toolset.
    names = [d["name"] for d in takyon_core.TAKYON_TOOL_DEFINITIONS]
    assert "business_seo_query_data" in names
    assert "business_seo_query_data" not in takyon_core.TAKYON_AUTHORITY_TOOL_NAMES
    assert takyon_toolset_name("business_seo_query_data") == "takyon"


def test_handle_business_seo_query_data_requires_mode():
    out = json.loads(takyon_core.handle_business_seo_query_data({}))
    assert out["success"] is False
    assert "mode" in out["error"].lower()


def test_handle_business_seo_query_data_rejects_unknown_mode():
    out = json.loads(takyon_core.handle_business_seo_query_data({"mode": "nope"}))
    assert out["success"] is False
    assert "mode must be one of" in out["error"].lower()


def test_handle_business_seo_query_data_gsc_query_requires_site_url_and_dates():
    out = json.loads(takyon_core.handle_business_seo_query_data({"mode": "gsc-query"}))
    assert out["success"] is False
    assert "site_url" in out["error"].lower()

    out = json.loads(
        takyon_core.handle_business_seo_query_data(
            {"mode": "gsc-query", "site_url": "sc-domain:acme.coscale.app"}
        )
    )
    assert out["success"] is False
    assert "start_date" in out["error"].lower() or "end_date" in out["error"].lower()


def test_handle_business_seo_query_data_keyword_modes_validate_inputs(monkeypatch):
    # DataForSEO backend: a business scope (so the paid-spend scope guard passes) but
    # forced "no creds" so the paid path fails closed deterministically (the keyword modes
    # replaced the old free Google Ads Keyword Planner backend).
    monkeypatch.setattr(takyon_core, "_session_business_slug", lambda: "testco")
    monkeypatch.setattr(takyon_core, "_seo_resolve_sensitive_env", lambda name: "")

    # keyword-historical validates the keyword list before any paid call
    out = json.loads(
        takyon_core.handle_business_seo_query_data({"mode": "keyword-historical"})
    )
    assert out["success"] is False
    assert "keywords" in out["error"].lower()

    # with keywords but no DataForSEO creds → fails closed (no spend, no fabrication)
    out = json.loads(
        takyon_core.handle_business_seo_query_data(
            {"mode": "keyword-historical", "keywords": ["running shoes"]}
        )
    )
    assert out["success"] is False
    assert "dataforseo_unconfigured" in out["error"]

    # keyword-ideas requires a keyword or page_url seed
    out = json.loads(takyon_core.handle_business_seo_query_data({"mode": "keyword-ideas"}))
    assert out["success"] is False
    assert "keywords" in out["error"].lower() or "page_url" in out["error"].lower()

    # with a seed but no creds → fails closed
    out = json.loads(
        takyon_core.handle_business_seo_query_data(
            {"mode": "keyword-ideas", "keywords": ["running shoes"]}
        )
    )
    assert out["success"] is False
    assert "dataforseo_unconfigured" in out["error"]


def test_seo_resolve_sensitive_env_is_strict_safebox_gated(monkeypatch):
    # Strict gate: every secret resolves through the safebox authority, even when
    # the same name is also present in the process env — no os.getenv side door.
    from plugins.takyon import safebox as takyon_safebox

    monkeypatch.setenv("GOOGLE_ADS_CLIENT_SECRET", "local-env-should-be-ignored")
    seen: list[str] = []

    def _read(key):
        seen.append(key)
        return "gated-secret"

    monkeypatch.setattr(takyon_safebox, "read_env_backed_value", _read)
    assert (
        takyon_core._seo_resolve_sensitive_env("GOOGLE_ADS_CLIENT_SECRET")
        == "gated-secret"
    )
    assert seen == ["GOOGLE_ADS_CLIENT_SECRET"]


def test_seo_resolve_sensitive_env_empty_when_safebox_unavailable(monkeypatch):
    # If the safebox has no value or is unavailable, resolve returns "" so callers
    # can raise one uniform error (never a silent os.getenv fallback).
    from plugins.takyon import safebox as takyon_safebox

    def _unavailable(_key):
        raise takyon_safebox.SafeboxAuthorityUnavailable("no authority configured")

    monkeypatch.setattr(takyon_safebox, "read_env_backed_value", _unavailable)
    assert takyon_core._seo_resolve_sensitive_env("GOOGLE_ADS_REFRESH_TOKEN") == ""


def test_google_ads_client_id_is_safebox_sensitive():
    # client_id has no sensitive suffix, so it is explicitly allowlisted; otherwise
    # the safebox gate would refuse to serve it.
    from plugins.takyon import safebox as takyon_safebox

    assert takyon_safebox.is_sensitive_env_key("GOOGLE_ADS_CLIENT_ID")


def test_gemini_logo_prompt_encodes_brand_brief_and_context():
    from plugins.takyon import creative_gateway as gw

    prompt = gw._gemini_logo_prompt(
        {"name": "Lumen", "category": "design tools", "tone": "playful"}
    ).lower()
    # operator-owned brand brief: flat vector, transparent, icon-only, no text
    assert "flat vector" in prompt
    assert "transparent" in prompt
    assert "no text" in prompt
    # business context steers the icon concept
    assert "lumen" in prompt
    assert "design tools" in prompt
    assert "playful" in prompt


def test_gemini_logo_prompt_defaults_without_context():
    from plugins.takyon import creative_gateway as gw

    prompt = gw._gemini_logo_prompt({"slug": "acme"}).lower()
    assert "no text" in prompt and "transparent" in prompt
    assert "acme" in prompt  # name falls back to slug


def test_resolve_gemini_image_key_empty_when_unconfigured(monkeypatch):
    from plugins.takyon import creative_gateway as gw

    monkeypatch.setattr(gw.safebox, "first_env_backed_value", lambda *names: "")
    assert gw._resolve_gemini_image_key() == ""


def test_resolve_gemini_image_key_reads_alias(monkeypatch):
    from plugins.takyon import creative_gateway as gw

    seen = {}

    def _fake(*names):
        seen["names"] = names
        return "  sk-gemini-xyz  "

    monkeypatch.setattr(gw.safebox, "first_env_backed_value", _fake)
    assert gw._resolve_gemini_image_key() == "sk-gemini-xyz"
    assert seen["names"] == ("TAKYON_GEMINI_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY")


def test_business_set_channel_credit_budgets_persists_snapshot(tmp_path, monkeypatch):
    monkeypatch.setenv("TAKYON_HOME", str(tmp_path))
    store = TakyonStore(tmp_path)
    _commit(
        store,
        "business:frameforge",
        [{"action": "business.upsert", "business": "frameforge", "name": "Frameforge", "mode": "live"}],
        "init-frameforge-budgets",
    )
    _grant_creative_credits(store, "frameforge", 5, "frameforge-budget-grant")

    result = _set_channel_credit_budgets(
        "frameforge",
        {"x": 1, "meta": 3, "reddit": 1},
        key="frameforge-channel-budgets-v1",
    )

    assert result["success"] is True
    assert result["value"]["channels"]["x"]["allocated_credits"] == 1
    assert result["value"]["channels"]["meta"]["allocated_credits"] == 3
    assert result["value"]["channels"]["reddit"]["allocated_credits"] == 1
    assert result["value"]["budget_capacity_credits"] == 5
    assert (
        tmp_path
        / "businesses"
        / "frameforge"
        / "metrics"
        / "channel-credit-budgets.json"
    ).is_file()


def test_business_read_channel_credit_budgets_returns_snapshot_and_action_costs(tmp_path, monkeypatch):
    monkeypatch.setenv("TAKYON_HOME", str(tmp_path))
    store = TakyonStore(tmp_path)
    _commit(
        store,
        "business:frameforge",
        [{"action": "business.upsert", "business": "frameforge", "name": "Frameforge", "mode": "live"}],
        "init-frameforge-read-budgets",
    )
    _grant_creative_credits(store, "frameforge", 5, "frameforge-budget-grant-read")
    _set_channel_credit_budgets(
        "frameforge",
        {"x": 1, "meta": 3, "reddit": 1},
        key="frameforge-channel-budgets-read-v1",
    )

    result = json.loads(
        handle_business_read_channel_credit_budgets(
            {
                "business": "frameforge",
            }
        )
    )

    assert result["success"] is True
    assert result["path"] == "metrics/channel-credit-budgets.json"
    assert result["value"]["channels"]["x"]["allocated_credits"] == 1
    assert result["value"]["channels"]["meta"]["allocated_credits"] == 3
    assert result["value"]["action_costs"]["x_publish_outreach"]["default_bucket"] == "x"
    assert result["value"]["action_costs"]["reddit_publish_outreach"]["default_bucket"] == "reddit"
    assert result["value"]["action_costs"]["meta_ad_launch"]["credits"] >= 1


def test_business_set_channel_credit_budgets_persists_credits_only(tmp_path, monkeypatch):
    monkeypatch.setenv("TAKYON_HOME", str(tmp_path))
    store = TakyonStore(tmp_path)
    _commit(
        store,
        "business:frameforge",
        [{"action": "business.upsert", "business": "frameforge", "name": "Frameforge", "mode": "live"}],
        "init-frameforge-launch-defaults",
    )
    _grant_creative_credits(store, "frameforge", 43, "frameforge-launch-defaults-grant")

    result = _set_channel_credit_budgets(
        "frameforge",
        {"x": 1, "meta": 12, "reddit": 30},
        key="frameforge-channel-budgets-defaults-v1",
    )

    assert result["success"] is True
    assert result["value"]["channels"]["x"] == {
        "allocated_credits": 1,
        "used_credits": 0,
        "reserved_credits": 0,
        "remaining_credits": 1,
    }
    assert "launch_defaults" not in result["value"]["channels"]["meta"]
    assert "launch_defaults" not in result["value"]["channels"]["reddit"]


def test_business_set_channel_credit_budgets_rejects_drop_below_reserved_live_spend(tmp_path, monkeypatch):
    monkeypatch.setenv("TAKYON_HOME", str(tmp_path))
    store = TakyonStore(tmp_path)
    _commit(
        store,
        "business:frameforge",
        [{"action": "business.upsert", "business": "frameforge", "name": "Frameforge", "mode": "live"}],
        "init-frameforge-live-reserved-budget",
    )
    _grant_creative_credits(store, "frameforge", 100, "frameforge-live-reserved-budget-grant")
    _set_channel_credit_budgets(
        "frameforge",
        {"x": 0, "meta": 100, "reddit": 0},
        key="frameforge-live-reserved-budget-initial",
    )

    reservation = takyon_core._reserve_channel_spend_credits(
        "frameforge",
        channel="meta",
        requested_credits=60,
        reservation_key="frameforge-meta-media-spend-v1",
        metadata={"slug": "demo"},
    )

    result = json.loads(
        handle_business_set_channel_credit_budgets(
            {
                "business": "frameforge",
                "allocations": {"x": 0, "meta": 50, "reddit": 0},
                "idempotency_key": "frameforge-live-reserved-budget-lower",
            }
        )
    )

    assert reservation["requested_credits"] == 60
    assert result["success"] is False
    assert "meta budget cannot drop below 60 credits" in result["error"]


def test_business_ugc_ad_generate_live_requires_channel_budget_context(tmp_path, monkeypatch):
    monkeypatch.setenv("TAKYON_HOME", str(tmp_path))
    store = TakyonStore(tmp_path)
    _commit(
        store,
        "business:clipbook",
        [{"action": "business.upsert", "business": "clipbook", "name": "Clipbook", "mode": "live"}],
        "init-clipbook-live-budget-context",
    )
    brief_path = tmp_path / "businesses" / "clipbook" / "research" / "brief.json"
    brief_path.parent.mkdir(parents=True, exist_ok=True)
    brief_path.write_text(json.dumps({"business": "clipbook", "product": "demo"}), encoding="utf-8")

    result = json.loads(
        handle_business_ugc_ad_generate(
            {
                "business": "clipbook",
                "brief_path": "research/brief.json",
                "slug": "clipbook-demo",
                "idempotency_key": "clipbook-ugc-live-missing-budget-context-v1",
            }
        )
    )

    assert result["success"] is False
    assert "budget_bucket or ad_metadata.channel" in result["error"]


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
                "budget_bucket": "meta",
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
    synced: list[str] = []
    monkeypatch.setattr(
        TakyonStore,
        "_sync_business_workspace_remote",
        lambda self, slug: synced.append(slug),
    )

    result = json.loads(
        handle_business_ugc_ad_generate(
            {
                "business": "clipbook",
                "brief_path": "research/brief.json",
                "script_path": "research/script.json",
                "slug": "clipbook-demo",
                "ad_metadata": {"channel": "meta", "campaign_slug": "clipbook-demo"},
                "idempotency_key": "clipbook-ugc-live-success-v1",
            }
        )
    )

    assert result["success"] is True
    assert result["status"] == "created"
    assert result["path"] == "product/ugc-ads/clipbook-demo/ad.mp4"
    assert result["balance_credits"] == 12
    assert synced == ["clipbook", "clipbook"]


def _meta_test_business(tmp_path, monkeypatch, *, slug="clipbook", mode="test"):
    """Set up a temp TAKYON_HOME + a business for the Meta ad launch tests."""
    monkeypatch.setenv("TAKYON_HOME", str(tmp_path))
    store = TakyonStore(tmp_path)
    upsert = {"action": "business.upsert", "business": slug, "name": slug.title()}
    if mode:
        upsert["mode"] = mode
    _commit(store, f"business:{slug}", [upsert], f"init-{slug}")
    return store


def _write_x_outreach_receipt(
    tmp_path,
    *,
    business="clipbook",
    filename="20260607T120000Z-x-demo.json",
    post_id="1234567890",
    post_url="https://x.com/vaalapp/status/1234567890",
    published_at="2026-06-07T12:00:00+00:00",
):
    receipt_path = (
        tmp_path
        / "businesses"
        / business
        / "metrics"
        / "receipts"
        / "outreach"
        / filename
    )
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    receipt = {
        "provider": "x",
        "channel": "x",
        "post_id": post_id,
        "post_url": post_url,
        "published_at": published_at,
        "sent": True,
        "external_side_effects": "sent",
        "artifact_path": f"distribution/local-published/x/{post_id}.md",
    }
    receipt_path.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    return receipt_path


def _grant_creative_credits(store: TakyonStore, business: str, credits: int, key: str) -> None:
    with store._connect() as conn:
        takyon_business_credits.grant_credits(conn, business, credits, idempotency_key=key)


def _set_channel_credit_budgets(
    business: str,
    allocations: dict[str, Any],
    *,
    key: str,
) -> dict[str, Any]:
    return json.loads(
        handle_business_set_channel_credit_budgets(
            {
                "business": business,
                "allocations": allocations,
                "idempotency_key": key,
            }
        )
    )


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


def _set_meta_mcp_env(monkeypatch) -> None:
    monkeypatch.setenv("META_MCP_OAUTH_TOKEN", "official-meta-mcp-test-token")


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


def _seed_live_ad_spend_policy(
    *,
    business: str,
    channel: str,
    slug: str,
    reserved_credits: int = 5_000,
    daily_budget_cents: int = 500,
    total_budget_cents: int = 5_000,
    provider_account_id: str = "account-1",
    provider_campaign_id: str = "campaign-1",
    provider_group_id: str = "group-1",
    provider_ad_id: str = "ad-1",
    provider_post_id: str | None = None,
    metadata: dict[str, Any] | None = None,
):
    start_at = datetime.now(timezone.utc) + timedelta(minutes=5)
    end_at = start_at + timedelta(days=10)
    return takyon_core._upsert_ad_spend_policy(
        business,
        channel=channel,
        slug=slug,
        reservation_key=f"{business}-{channel}-{slug}-reservation",
        reserved_credits=reserved_credits,
        daily_budget_cents=daily_budget_cents,
        total_budget_cents=total_budget_cents,
        start_at=start_at,
        end_at=end_at,
        provider_account_id=provider_account_id,
        provider_campaign_id=provider_campaign_id,
        provider_group_id=provider_group_id,
        provider_ad_id=provider_ad_id,
        provider_post_id=provider_post_id,
        status="created_paused",
        metadata=metadata or {},
    )


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
    assert plan["post"]["destination_url"] == "https://clipbook.coscale.app/"
    assert plan["ad"]["click_url"] == "https://clipbook.coscale.app/"

    receipt_abs = tmp_path / "businesses" / "clipbook" / result["receipt"]
    receipt = json.loads(receipt_abs.read_text(encoding="utf-8"))
    assert receipt["destination_url"] == "https://clipbook.coscale.app/"
    assert receipt["click_url"] == "https://clipbook.coscale.app/"


def test_business_reddit_ad_launch_preserves_copy_fields_in_plan_and_receipt(tmp_path, monkeypatch):
    _stub_reddit_ads_config(monkeypatch)
    _meta_test_business(tmp_path, monkeypatch)

    result = json.loads(
        handle_business_reddit_ad_launch(
            _reddit_launch_args(
                post={
                    "headline": "Try Clipbook",
                    "destination_url": "https://clipbook.coscale.app/",
                    "media_url": "https://cdn.example.com/clipbook.png",
                    "allow_comments": False,
                    "display_url": "https://clipbook.coscale.app/",
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
    assert plan["post"]["display_url"] == "https://clipbook.coscale.app/"
    assert plan["post"]["call_to_action"] == "Learn More"
    assert plan["post"]["supplementary_text"] == "No ticket queue. No templated sludge."
    assert plan["post"]["body"] == "Optional long-form copy for Reddit post flows."

    receipt_abs = tmp_path / "businesses" / "clipbook" / result["receipt"]
    receipt = json.loads(receipt_abs.read_text(encoding="utf-8"))
    assert receipt["display_url"] == "https://clipbook.coscale.app/"
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


def test_stage_business_public_asset_mirrors_into_static_publish_root_only(tmp_path, monkeypatch):
    store = _meta_test_business(tmp_path, monkeypatch, mode="live")
    publish_target = "https://clipbook.coscale.app/"
    publish_receipt_rel = "metrics/receipts/product-surface/test.json"
    publish_root = tmp_path / "product-sites" / "clipbook"
    receipt_abs = tmp_path / "businesses" / "clipbook" / publish_receipt_rel
    receipt_abs.parent.mkdir(parents=True, exist_ok=True)
    receipt_abs.write_text(
        json.dumps(
            {
                "publish": {
                    "status": "published",
                    "publish_mode": "local_static",
                    "publish_root": str(publish_root),
                    "public_url": publish_target,
                }
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    _commit(
        store,
        "business:clipbook",
        [
            {
                "action": "app.surface.upsert",
                "business": "clipbook",
                "status": "active",
                "source_path": "product/site",
                "routes": ["/"],
                "publish_target": publish_target,
            },
            {
                "action": "app.surface.publish_result",
                "business": "clipbook",
                "publish_status": "published",
                "publish_target": publish_target,
                "public_url": publish_target,
                "published_at": "2026-06-05T16:00:00+00:00",
                "receipt_path": publish_receipt_rel,
                "publish_source_path": "product/site",
                "blocker": "",
            },
        ],
        "clipbook-surface-publish-state",
    )
    image_rel = "product/static-ads/demo-reddit/banner.png"
    image_abs = tmp_path / "businesses" / "clipbook" / image_rel
    image_abs.parent.mkdir(parents=True, exist_ok=True)
    image_abs.write_bytes(b"fake png bytes")

    staged = takyon_core._stage_business_public_asset(
        store,
        "clipbook",
        source_path=image_rel,
        asset_slug="demo-reddit-image",
        verify_public_url=False,
    )

    shared_asset = tmp_path / "product-sites" / "clipbook" / "_takyon" / "assets" / "demo-reddit-image" / "banner.png"
    assert shared_asset.is_file()
    assert staged["publish_roots"] == [str(publish_root)]


def test_make_product_publish_path_traversable_opens_takyon_home_for_asset_serving(tmp_path, monkeypatch):
    takyon_home = tmp_path / ".takyon"
    service_root = takyon_home / "product-services" / "clipbook"
    service_root.mkdir(parents=True, exist_ok=True)
    product_services = service_root.parent
    takyon_home.chmod(0o700)
    product_services.chmod(0o700)
    service_root.chmod(0o700)
    monkeypatch.setenv("TAKYON_HOME", str(takyon_home))

    takyon_core._make_product_publish_path_traversable(service_root)

    assert stat.S_IMODE(takyon_home.stat().st_mode) == 0o711
    assert stat.S_IMODE(product_services.stat().st_mode) == 0o755
    assert stat.S_IMODE(service_root.stat().st_mode) == 0o755


def test_business_reddit_ad_launch_rejects_over_cap_budget(tmp_path, monkeypatch):
    _stub_reddit_ads_config(monkeypatch)
    _meta_test_business(tmp_path, monkeypatch)

    result = json.loads(
        handle_business_reddit_ad_launch(_reddit_launch_args(ad_group={"daily_budget_usd": 999.0}))
    )

    assert result["success"] is False
    assert "exceeds the safety cap" in result["error"]
    assert not (tmp_path / "businesses" / "clipbook" / "distribution" / "reddit-ads").exists()


def test_business_reddit_ad_launch_defaults_to_activation_when_live(tmp_path, monkeypatch):
    _stub_reddit_ads_config(monkeypatch)
    store = _meta_test_business(tmp_path, monkeypatch, mode="live")
    _grant_creative_credits(store, "clipbook", 3000, "clipbook-reddit-activate-default-grant")
    _set_channel_credit_budgets(
        "clipbook",
        {"x": 0, "meta": 0, "reddit": 3000},
        key="clipbook-reddit-activate-default-budget",
    )

    def fake_gateway(endpoint, payload):
        if endpoint == "reddit-launch":
            return {
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
                "balance_credits": 2999,
                "reserved_credits": 2999,
            }
        assert endpoint == "reddit-control"
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

    result = json.loads(handle_business_reddit_ad_launch(_reddit_launch_args()))

    assert result["success"] is True
    assert result["status"] == "activated"
    assert result["paused"] is False
    assert result["value"]["reserved_credits"] == 2999
    assert result["value"]["total_budget_usd"] == 29.99

    repeat = json.loads(handle_business_reddit_ad_launch(_reddit_launch_args()))
    assert repeat["success"] is True
    assert repeat["idempotent"] is True
    assert repeat["status"] == "activated"
    assert repeat["paused"] is False


def test_business_reddit_ad_launch_live_blocks_without_credits(tmp_path, monkeypatch):
    _stub_reddit_ads_config(monkeypatch)
    _meta_test_business(tmp_path, monkeypatch, mode="live")

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
    assert result["status"] == "blocked_channel_budget_exhausted"
    assert "allocate more Reddit credits" in result["error"]


def test_business_reddit_ad_launch_live_charges_credits(tmp_path, monkeypatch):
    _stub_reddit_ads_config(monkeypatch)
    store = _meta_test_business(tmp_path, monkeypatch, mode="live")
    _grant_creative_credits(store, "clipbook", 3000, "clipbook-reddit-grant")
    _set_channel_credit_budgets(
        "clipbook",
        {"x": 0, "meta": 0, "reddit": 3000},
        key="clipbook-reddit-budget-v1",
    )
    monkeypatch.setattr(
        takyon_core,
        "_call_creative_runtime_gateway",
        lambda endpoint, payload: (
            {
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
                "balance_credits": 2999,
                "reserved_credits": 2999,
            }
            if endpoint == "reddit-launch"
            else {
                "success": True,
                "status": "activated",
                "applied": [
                    {"object": "campaign", "id": "campaign-1", "configured_status": "ACTIVE"},
                    {"object": "ad_group", "id": "adgroup-1", "configured_status": "ACTIVE"},
                    {"object": "ad", "id": "ad-1", "configured_status": "ACTIVE"},
                ],
            }
        ),
    )
    synced: list[str] = []
    monkeypatch.setattr(
        TakyonStore,
        "_sync_business_workspace_remote",
        lambda self, slug: synced.append(slug),
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
    assert result["status"] == "activated"
    assert result["balance_credits"] == 2999
    assert synced == ["clipbook"]


def test_business_reddit_ad_launch_derives_bounded_budget_when_omitted(tmp_path, monkeypatch):
    _stub_reddit_ads_config(monkeypatch)
    store = _meta_test_business(tmp_path, monkeypatch, mode="live")
    _grant_creative_credits(store, "clipbook", 3000, "clipbook-reddit-default-budget-grant")
    _set_channel_credit_budgets(
        "clipbook",
        {"x": 0, "meta": 0, "reddit": 3000},
        key="clipbook-reddit-default-budget-v1",
    )
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
            "balance_credits": 2999,
            "reserved_credits": 2999,
        },
    )

    result = json.loads(
        handle_business_reddit_ad_launch(
            _reddit_launch_args(
                ad_group={"optimization_goal": "CLICKS"},
                slug="demo-reddit-default-budget",
                activate=False,
                idempotency_key="clipbook-reddit-default-budget-launch-v1",
            )
        )
    )

    assert result["success"] is True
    assert result["status"] == "created_paused"
    assert result["value"]["daily_budget_usd"] == 29.99
    assert result["value"]["total_budget_usd"] == 29.99
    plan_abs = tmp_path / "businesses" / "clipbook" / result["value"]["plan_path"]
    plan = json.loads(plan_abs.read_text(encoding="utf-8"))
    assert plan["ad_group"]["daily_budget_usd"] == 29.99


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
    assert plan["structured_post_payload"]["data"]["creative"]["destination"]["url"] == "https://clipbook.coscale.app/"
    assert plan["structured_post_payload"]["data"]["creative"]["destination"]["call_to_action"] == "Learn More"
    assert plan["structured_post_payload"]["data"]["creative"]["supplementary_text"] == "No ticket queue. No templated sludge."
    assert plan["legacy_post_payload"]["data"]["content"][0]["destination_url"] == "https://clipbook.coscale.app/"
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


def test_reddit_launch_plan_defaults_ad_group_start_time_only(tmp_path, monkeypatch):
    store = _meta_test_business(tmp_path, monkeypatch, mode="live")
    staged_args, _staged_assets = takyon_core._reddit_stage_launch_args(
        store,
        "clipbook",
        _reddit_launch_args(
            slug="demo-reddit-default-start",
            idempotency_key="clipbook-reddit-default-start-v1",
        ),
        publish_target=_product_publish_target("clipbook"),
        verify_public_url=False,
    )

    plan = takyon_core._reddit_launch_plan(staged_args, {})
    assert "start_time" not in plan["campaign_payload"]["data"]
    ad_group_start = plan["ad_group_payload"]["data"]["start_time"]
    assert ad_group_start.endswith("Z")

    parsed = takyon_core._parse_iso_datetime(ad_group_start)
    assert parsed is not None
    assert parsed.minute == 0
    assert parsed.second == 0
    assert parsed.microsecond == 0


def test_reddit_launch_plan_defaults_cpc_bid_value_for_non_cbo_launches(tmp_path, monkeypatch):
    store = _meta_test_business(tmp_path, monkeypatch, mode="live")
    staged_args, _staged_assets = takyon_core._reddit_stage_launch_args(
        store,
        "clipbook",
        _reddit_launch_args(
            slug="demo-reddit-default-bid",
            idempotency_key="clipbook-reddit-default-bid-v1",
        ),
        publish_target=_product_publish_target("clipbook"),
        verify_public_url=False,
    )

    plan = takyon_core._reddit_launch_plan(staged_args, {})
    assert plan["ad_group_payload"]["data"]["bid_type"] == "CPC"
    assert plan["ad_group_payload"]["data"]["bid_value"] == 1_000_000
    assert plan["ad_group_payload"]["data"]["bid_value"] < plan["ad_group_payload"]["data"]["goal_value"]


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
    _seed_live_ad_spend_policy(
        business="clipbook",
        channel="reddit",
        slug="demo-reddit",
        provider_account_id="account-policy",
        provider_campaign_id="campaign-policy",
        provider_group_id="adgroup-policy",
        provider_ad_id="ad-policy",
        provider_post_id="post-policy",
        metadata={"budget_scope": "ad_group"},
    )
    receipt_abs = tmp_path / "businesses" / "clipbook" / "distribution" / "reddit-ads" / "demo-reddit" / "receipt.json"
    tampered = json.loads(receipt_abs.read_text(encoding="utf-8"))
    tampered["ids"]["campaign_id"] = "campaign-tampered"
    tampered["ids"]["ad_group_id"] = "adgroup-tampered"
    tampered["ids"]["ad_id"] = "ad-tampered"
    receipt_abs.write_text(json.dumps(tampered), encoding="utf-8")
    calls: list[tuple[str, dict]] = []
    synced: list[str] = []

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
    monkeypatch.setattr(
        TakyonStore,
        "_sync_business_workspace_remote",
        lambda self, slug: synced.append(slug),
    )

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
    assert calls[0][1]["campaign_id"] == "campaign-policy"
    assert calls[0][1]["ad_group_id"] == "adgroup-policy"
    assert calls[0][1]["ad_id"] == "ad-policy"
    assert synced == ["clipbook"]

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
    _seed_live_ad_spend_policy(
        business="clipbook",
        channel="reddit",
        slug="demo-reddit",
        provider_campaign_id="campaign-1",
        provider_group_id="adgroup-1",
        provider_ad_id="ad-1",
        metadata={"budget_scope": "ad_group"},
    )

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
    _seed_live_ad_spend_policy(
        business="clipbook",
        channel="reddit",
        slug="demo-reddit",
        provider_account_id="account-policy",
        provider_campaign_id="campaign-policy",
        provider_group_id="adgroup-policy",
        provider_ad_id="ad-policy",
        provider_post_id="post-policy",
        total_budget_cents=5000,
        metadata={"budget_scope": "ad_group"},
    )
    receipt_abs = tmp_path / "businesses" / "clipbook" / "distribution" / "reddit-ads" / "demo-reddit" / "receipt.json"
    tampered = json.loads(receipt_abs.read_text(encoding="utf-8"))
    tampered["ad_account_id"] = "account-tampered"
    tampered["ids"]["campaign_id"] = "campaign-tampered"
    receipt_abs.write_text(json.dumps(tampered), encoding="utf-8")
    calls: list[tuple[str, dict[str, Any]]] = []
    synced: list[str] = []

    def fake_gateway(endpoint, payload):
        calls.append((endpoint, payload))
        return {
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
        }

    monkeypatch.setattr(takyon_core, "_call_creative_runtime_gateway", fake_gateway)
    monkeypatch.setattr(
        TakyonStore,
        "_sync_business_workspace_remote",
        lambda self, slug: synced.append(slug),
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
    assert calls[0][0] == "reddit-insights"
    assert calls[0][1]["campaign_id"] == "campaign-policy"
    assert calls[0][1]["ad_account_id"] == "account-policy"
    assert calls[0][1]["fields"] == ["SPEND", "IMPRESSIONS", "CLICKS", "CTR", "CPC", "ECPM"]
    assert synced == ["clipbook"]

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


def test_business_publish_test_outreach_uses_local_receipt(tmp_path, monkeypatch):
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
        handle_business_publish_test_outreach(
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


def test_business_x_publish_outreach_sets_x_defaults_in_test_mode(tmp_path, monkeypatch):
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
        "init-jobtailor-x",
    )

    result = json.loads(
        handle_business_x_publish_outreach(
            {
                "business": "jobtailor",
                "body": "Scope creep keeps eating freelancer margins.",
                "idempotency_key": "jobtailor-x-local-publish",
            }
        )
    )

    assert result["success"] is True
    publish = result["results"][0]
    receipt = tmp_path / "businesses" / "jobtailor" / publish["receipt"]
    receipt_payload = json.loads(receipt.read_text(encoding="utf-8"))
    assert receipt_payload["channel"] == "x"
    assert receipt_payload["provider"] == "x"


def test_business_x_publish_outreach_test_mode_records_media_paths_without_provider_calls(tmp_path, monkeypatch):
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
        "init-jobtailor-x-media",
    )
    image_one = tmp_path / "businesses" / "jobtailor" / "product" / "ads" / "hero.png"
    image_two = tmp_path / "businesses" / "jobtailor" / "product" / "ads" / "chart.png"
    image_one.parent.mkdir(parents=True, exist_ok=True)
    image_one.write_bytes(b"png-one")
    image_two.write_bytes(b"png-two")

    monkeypatch.setattr(
        takyon_core.composio_distribution,
        "twitter_execute_tool",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("provider call should not run in test mode")),
    )
    monkeypatch.setattr(
        takyon_core.composio_distribution,
        "upload_file_descriptor",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("upload should not run in test mode")),
    )

    result = json.loads(
        handle_business_x_publish_outreach(
            {
                "business": "jobtailor",
                "body": "Scope creep keeps eating freelancer margins.",
                "media_paths": ["product/ads/hero.png", "product/ads/chart.png"],
                "idempotency_key": "jobtailor-x-local-publish-media",
            }
        )
    )

    assert result["success"] is True
    publish = result["results"][0]
    receipt = tmp_path / "businesses" / "jobtailor" / publish["receipt"]
    receipt_payload = json.loads(receipt.read_text(encoding="utf-8"))
    assert receipt_payload["metadata"]["media_paths"] == [
        "product/ads/hero.png",
        "product/ads/chart.png",
    ]


@pytest.mark.parametrize(
    ("media_paths", "expected"),
    [
        (["../outside.png"], "must stay within the business workspace"),
        (["product/ads/missing.png"], "media file not found"),
        (
            [
                "product/ads/1.png",
                "product/ads/2.png",
                "product/ads/3.png",
                "product/ads/4.png",
                "product/ads/5.png",
            ],
            "at most 4 images",
        ),
        (["product/ads/hero.png", "product/ads/clip.mp4"], "cannot mix images and video"),
    ],
)
def test_business_x_publish_outreach_rejects_invalid_media_paths(tmp_path, monkeypatch, media_paths, expected):
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
        "init-jobtailor-x-invalid-media",
    )
    media_root = tmp_path / "businesses" / "jobtailor" / "product" / "ads"
    media_root.mkdir(parents=True, exist_ok=True)
    for name in ("1.png", "2.png", "3.png", "4.png", "5.png", "hero.png"):
        (media_root / name).write_bytes(b"png")
    (media_root / "clip.mp4").write_bytes(b"mp4")

    result = json.loads(
        handle_business_x_publish_outreach(
            {
                "business": "jobtailor",
                "body": "Scope creep keeps eating freelancer margins.",
                "media_paths": media_paths,
                "idempotency_key": "jobtailor-x-invalid-media",
            }
        )
    )

    assert result["success"] is False
    assert expected in result["error"]


def test_business_publish_test_outreach_canonicalizes_product_url(tmp_path, monkeypatch):
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
                "publish_target": "https://latexflow.coscale.app/",
            },
        ],
        "surface-latexflow-outreach",
    )

    preview, replacements = _canonicalize_business_product_links(
        "https://latexflow.io (coming soon)",
        business="latexflow",
        canonical_url="https://latexflow.coscale.app/",
    )
    assert preview == "https://latexflow.coscale.app/ (coming soon)"
    assert replacements == [{"from": "https://latexflow.io", "to": "https://latexflow.coscale.app/"}]

    result = json.loads(
        handle_business_publish_test_outreach(
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
    assert "https://latexflow.coscale.app/ (coming soon)" in artifact.read_text(encoding="utf-8")
    assert "latexflow.io" not in artifact.read_text(encoding="utf-8")
    receipt_payload = json.loads(receipt.read_text(encoding="utf-8"))
    assert receipt_payload["metadata"]["canonical_product_url"] == "https://latexflow.coscale.app/"
    assert receipt_payload["metadata"]["canonicalized_product_links"]


def test_business_publish_test_outreach_records_intended_destination(tmp_path, monkeypatch):
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
        handle_business_publish_test_outreach(
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


def test_business_x_publish_outreach_live_x_blocks_before_enqueue_when_credits_are_missing(tmp_path, monkeypatch):
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
                "mode": "live",
            }
        ],
        "init-jobtailor-live-x-preflight",
    )

    result = json.loads(
        handle_business_x_publish_outreach(
            {
                "business": "jobtailor",
                "channel": "x",
                "provider": "x",
                "body": "Scope creep keeps eating freelancer margins.",
                "requires_api": ["x"],
                "idempotency_key": "jobtailor-x-live-blocked-v1",
            }
        )
    )

    assert result["success"] is False
    assert result["blocked"] is True
    assert result["status"] == "blocked_insufficient_creative_credits"
    assert "insufficient_creative_credits" in result["error"]


def test_business_reddit_publish_outreach_sets_reddit_defaults_in_test_mode(tmp_path, monkeypatch):
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
        "init-jobtailor-reddit",
    )

    result = json.loads(
        handle_business_reddit_publish_outreach(
            {
                "business": "jobtailor",
                "subreddit": "freelance",
                "title": "How freelancers stop scope creep",
                "body": "Short checklist that keeps projects from ballooning.",
                "idempotency_key": "jobtailor-reddit-local-publish",
            }
        )
    )

    assert result["success"] is True
    publish = result["results"][0]
    receipt = tmp_path / "businesses" / "jobtailor" / publish["receipt"]
    receipt_payload = json.loads(receipt.read_text(encoding="utf-8"))
    assert receipt_payload["channel"] == "reddit"
    assert receipt_payload["provider"] == "reddit"
    assert receipt_payload["metadata"]["subreddit"] == "freelance"


def test_business_reddit_publish_outreach_live_blocks_before_enqueue_when_credits_are_missing(tmp_path, monkeypatch):
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
                "mode": "live",
            }
        ],
        "init-jobtailor-live-reddit-preflight",
    )

    result = json.loads(
        handle_business_reddit_publish_outreach(
            {
                "business": "jobtailor",
                "subreddit": "freelance",
                "title": "How freelancers stop scope creep",
                "body": "Short checklist that keeps projects from ballooning.",
                "idempotency_key": "jobtailor-reddit-live-blocked-v1",
            }
        )
    )

    assert result["success"] is False
    assert result["blocked"] is True
    assert result["status"] == "blocked_insufficient_creative_credits"
    assert "insufficient_creative_credits" in result["error"]


def test_business_x_search_live_writes_snapshot(tmp_path, monkeypatch):
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
                "mode": "live",
            }
        ],
        "init-clipbook-x-search",
    )
    monkeypatch.setattr(
        takyon_core.composio_distribution,
        "twitter_execute_tool",
        lambda *args, **kwargs: {
            "data": {
                "data": [
                    {"id": "1", "text": "search result one", "author_id": "a1"},
                    {"id": "2", "text": "search result two", "author_id": "a2"},
                ],
                "meta": {"result_count": 2, "newest_id": "2", "oldest_id": "1"},
            }
        },
    )

    result = json.loads(
        handle_business_x_search(
            {
                "business": "clipbook",
                "query": "launch copy",
                "max_results": 12,
                "idempotency_key": "clipbook-x-search-v1",
            }
        )
    )

    assert result["success"] is True
    assert result["result_count"] == 2
    assert result["newest_id"] == "2"
    snapshot_abs = tmp_path / "businesses" / "clipbook" / result["snapshot_path"]
    assert snapshot_abs.is_file()
    snapshot_payload = json.loads(snapshot_abs.read_text(encoding="utf-8"))
    assert snapshot_payload["query"] == "launch copy"
    assert snapshot_payload["requested_max_results"] == 12
    assert snapshot_payload["meta"]["result_count"] == 2
    assert snapshot_payload["tweets"][0]["id"] == "1"

    with store._connect() as conn:
        row = conn.execute(
            "SELECT event_type, payload_json FROM events WHERE business_slug = ? ORDER BY created_at DESC LIMIT 1",
            ("clipbook",),
        ).fetchone()
    assert row["event_type"] == "x.search"
    payload = json.loads(row["payload_json"])
    assert payload["snapshot_path"] == result["snapshot_path"]


def test_business_x_metrics_sync_live_writes_snapshot(tmp_path, monkeypatch):
    store = _meta_test_business(tmp_path, monkeypatch, mode="live")
    _write_x_outreach_receipt(
        tmp_path,
        post_id="2063697342092509383",
        post_url="https://x.com/vaalapp/status/2063697342092509383",
        published_at="2026-06-07T18:59:22+00:00",
    )

    monkeypatch.setattr(
        takyon_core,
        "_x_metrics_lookup",
        lambda post_id, **_kw: {
            "id": post_id,
            "created_at": "2026-06-07T18:59:22.000Z",
            "public_metrics": {
                "reply_count": 1,
                "retweet_count": 2,
                "like_count": 3,
                "quote_count": 4,
                "bookmark_count": 5,
                "impression_count": 6,
            },
            "non_public_metrics": {
                "user_profile_clicks": 7,
                "impression_count": 8,
                "engagements": 9,
            },
            "organic_metrics": {
                "reply_count": 10,
                "retweet_count": 11,
                "impression_count": 12,
                "like_count": 13,
                "user_profile_clicks": 14,
            },
        },
    )

    result = json.loads(
        handle_business_x_metrics_sync(
            {
                "business": "clipbook",
                "post_id": "2063697342092509383",
                "idempotency_key": "clipbook-x-metrics-v1",
            }
        )
    )

    assert result["success"] is True
    assert result["status"] == "synced"
    assert result["totals"]["public_metrics"]["like_count"] == 3
    assert result["totals"]["non_public_metrics"]["engagements"] == 9
    assert result["totals"]["organic_metrics"]["user_profile_clicks"] == 14

    post_abs = tmp_path / "businesses" / "clipbook" / result["post_metrics_path"]
    assert post_abs.is_file()
    post_payload = json.loads(post_abs.read_text(encoding="utf-8"))
    assert post_payload["post_id"] == "2063697342092509383"
    assert post_payload["public_metrics"]["bookmark_count"] == 5

    metrics_abs = tmp_path / "businesses" / "clipbook" / result["metrics_path"]
    assert metrics_abs.is_file()
    lines = [json.loads(line) for line in metrics_abs.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert lines[-1]["post_metrics_path"] == result["post_metrics_path"]

    summary_abs = tmp_path / "businesses" / "clipbook" / result["summary_path"]
    assert summary_abs.is_file()
    summary_payload = json.loads(summary_abs.read_text(encoding="utf-8"))
    assert summary_payload["posts_synced"] == 1
    assert summary_payload["totals"]["public_metrics"]["impression_count"] == 6

    with store._connect() as conn:
        row = conn.execute(
            "SELECT event_type, payload_json FROM events WHERE business_slug = ? ORDER BY created_at DESC LIMIT 1",
            ("clipbook",),
        ).fetchone()
    assert row["event_type"] == "x.metrics_sync"
    payload = json.loads(row["payload_json"])
    assert payload["receipt"].startswith("metrics/x/syncs/")


def test_business_x_metrics_sync_defaults_to_latest_x_receipt(tmp_path, monkeypatch):
    _meta_test_business(tmp_path, monkeypatch, mode="live")
    _write_x_outreach_receipt(
        tmp_path,
        filename="20260607T120000Z-x-old.json",
        post_id="111",
        post_url="https://x.com/vaalapp/status/111",
        published_at="2026-06-07T12:00:00+00:00",
    )
    latest_path = _write_x_outreach_receipt(
        tmp_path,
        filename="20260607T130000Z-x-new.json",
        post_id="222",
        post_url="https://x.com/vaalapp/status/222",
        published_at="2026-06-07T13:00:00+00:00",
    )
    os.utime(latest_path, (latest_path.stat().st_atime, latest_path.stat().st_mtime + 10))

    seen: dict[str, str] = {}

    def _fake_lookup(post_id, **_kw):
        seen["post_id"] = post_id
        return {
            "id": post_id,
            "created_at": "2026-06-07T13:00:00.000Z",
            "public_metrics": {"like_count": 1},
        }

    monkeypatch.setattr(takyon_core, "_x_metrics_lookup", _fake_lookup)

    result = json.loads(
        handle_business_x_metrics_sync(
            {
                "business": "clipbook",
                "idempotency_key": "clipbook-x-metrics-latest-v1",
            }
        )
    )

    assert result["success"] is True
    assert seen["post_id"] == "222"
    assert result["post_id"] == "222"


def test_enforce_operator_business_access_fails_closed_without_principal(monkeypatch):
    """The staged identity gate (bind -> observe -> enforce): '1'/'enforce' refuses a principal-less
    session BEFORE any business read — never widened to all-business access; 'warn' allows but
    records one operator.identity.unbound event per business; unset keeps local single-operator
    behavior unchanged."""

    class _UnboundStore:
        _warn_unbound_operator_access = takyon_core.TakyonStore._warn_unbound_operator_access
        _system_plane = ""

        def __init__(self):
            self.events = []

        def _active_operator_user_id(self) -> str:
            return ""

        def _record_event(self, conn, *, scope, business_slug, event_type, payload):
            self.events.append((scope, business_slug, event_type))

    store = _UnboundStore()

    monkeypatch.setenv("TAKYON_REQUIRE_OPERATOR_IDENTITY", "1")
    with pytest.raises(takyon_core.TakyonError, match="operator identity required"):
        # conn=None proves the refusal happens before any DB access.
        takyon_core.TakyonStore._enforce_operator_business_access(store, None, "acme")
    monkeypatch.setenv("TAKYON_REQUIRE_OPERATOR_IDENTITY", "enforce")
    with pytest.raises(takyon_core.TakyonError, match="operator identity required"):
        takyon_core.TakyonStore._enforce_operator_business_access(store, None, "acme")

    # Observe stage: allowed, evidence recorded once per business per store instance.
    monkeypatch.setenv("TAKYON_REQUIRE_OPERATOR_IDENTITY", "warn")
    assert takyon_core.TakyonStore._enforce_operator_business_access(store, None, "acme") is None
    assert takyon_core.TakyonStore._enforce_operator_business_access(store, None, "acme") is None
    assert takyon_core.TakyonStore._enforce_operator_business_access(store, None, "zeta") is None
    assert store.events == [
        ("business:acme", "acme", "operator.identity.unbound"),
        ("business:zeta", "zeta", "operator.identity.unbound"),
    ]

    monkeypatch.delenv("TAKYON_REQUIRE_OPERATOR_IDENTITY", raising=False)
    assert (
        takyon_core.TakyonStore._enforce_operator_business_access(store, None, "acme") is None
    )


def test_operator_task_worker_deferral_is_opt_in(monkeypatch):
    """Worker-plane execution of long operator tools is an explicit deployment declaration
    (TAKYON_OPERATOR_TASKS_VIA_WORKER=1) and never triggers inside the worker process itself
    (the surrounding job is already durable; re-deferring could starve the drain threads)."""
    monkeypatch.delenv("TAKYON_OPERATOR_TASKS_VIA_WORKER", raising=False)
    monkeypatch.delenv("TAKYON_WORKER_PROCESS", raising=False)
    assert takyon_core._defer_claude_agent_task_to_worker({"business": "acme"}) is None
    assert takyon_core._defer_product_surface_refresh_to_worker({"business": "acme"}) is None

    monkeypatch.setenv("TAKYON_OPERATOR_TASKS_VIA_WORKER", "1")
    monkeypatch.setenv("TAKYON_WORKER_PROCESS", "1")
    assert takyon_core._defer_claude_agent_task_to_worker({"business": "acme"}) is None
    assert takyon_core._defer_product_surface_refresh_to_worker({"business": "acme"}) is None


class _DeferralStoreStub:
    _workspace_root_override = None

    def __init__(self):
        self.commits = []

    def commit(self, **kwargs):
        self.commits.append(kwargs)
        return {
            "success": True,
            "results": [{"action": "job.enqueue", "job": "run-1", "worker_job": "wj-1"}],
        }


def test_defer_claude_agent_task_attaches_to_worker_run(monkeypatch):
    """The tool enqueues ONE canonical run (work request mirrored to a worker job), waits on the
    run row, and returns the recorded tool result verbatim — caller-context defaults (like
    refresh_surface for product workspaces) are frozen into the deferred args."""
    monkeypatch.setenv("TAKYON_OPERATOR_TASKS_VIA_WORKER", "1")
    monkeypatch.delenv("TAKYON_WORKER_PROCESS", raising=False)
    store = _DeferralStoreStub()
    monkeypatch.setattr(takyon_core, "_store", lambda: store)
    monkeypatch.setattr(takyon_core, "_require_api_access", lambda op, **kw: {})
    statuses = iter(
        [
            ("queued", {}),
            ("running", {}),
            ("completed", {"result": {"success": True, "summary": "done"}}),
        ]
    )
    monkeypatch.setattr(takyon_core, "_read_work_request_run", lambda _store, _run_id: next(statuses))
    monkeypatch.setattr(takyon_core, "_WORKER_DEFERRAL_POLL_SECONDS", 0.0)

    raw = takyon_core._defer_claude_agent_task_to_worker(
        {
            "business": "acme",
            "instruction": "build the site",
            "idempotency_key": "task-1",
            "workspace": "product/site",
        }
    )
    result = json.loads(raw)
    assert result["success"] is True
    assert result["summary"] == "done"
    assert result["run_id"] == "run-1"
    assert result["worker_job"] == "wj-1"

    assert len(store.commits) == 1
    op = store.commits[0]["operations"][0]
    assert op["action"] == "job.enqueue"
    assert op["kind"] == "claude.agent_task"
    assert op["worker_queue"] is True
    assert op["payload"]["tool"] == "business_claude_agent_task"
    # No session binding here, product workspace: refresh default frozen at the caller.
    assert op["payload"]["args"]["refresh_surface"] is True
    assert store.commits[0]["idempotency_key"] == "task-1:claude-sdk-worker-job"


def test_defer_claude_agent_task_detaches_when_wait_budget_expires(monkeypatch):
    """If the caller's wait budget runs out, the tool returns a detached 'running' handle instead
    of killing the run — re-calling with the same args + idempotency_key re-attaches."""
    monkeypatch.setenv("TAKYON_OPERATOR_TASKS_VIA_WORKER", "1")
    monkeypatch.delenv("TAKYON_WORKER_PROCESS", raising=False)
    store = _DeferralStoreStub()
    monkeypatch.setattr(takyon_core, "_store", lambda: store)
    monkeypatch.setattr(takyon_core, "_require_api_access", lambda op, **kw: {})
    monkeypatch.setattr(takyon_core, "_read_work_request_run", lambda _store, _run_id: ("running", {}))

    class _FakeTime:
        def __init__(self):
            self.now = 0.0

        def monotonic(self):
            return self.now

        def sleep(self, seconds):
            self.now += max(float(seconds), 1.0) * 100_000.0

    monkeypatch.setattr(takyon_core, "time", _FakeTime())

    raw = takyon_core._defer_claude_agent_task_to_worker(
        {
            "business": "acme",
            "instruction": "build",
            "idempotency_key": "task-2",
            "workspace": "research",
        }
    )
    result = json.loads(raw)
    assert result["success"] is False
    assert result["status"] == "running"
    assert result["detached"] is True
    assert result["run_id"] == "run-1"
    assert "idempotency_key" in result["note"]


def test_defer_claude_agent_task_allows_business_session_canonical_workspace(monkeypatch):
    monkeypatch.setenv("TAKYON_OPERATOR_TASKS_VIA_WORKER", "1")
    monkeypatch.delenv("TAKYON_WORKER_PROCESS", raising=False)

    class _SessionStoreStub(_DeferralStoreStub):
        _workspace_root_override = "/tmp/takyon-session-workspace"

    store = _SessionStoreStub()
    monkeypatch.setattr(takyon_core, "_store", lambda: store)
    monkeypatch.setattr(takyon_core, "_session_business_slug", lambda: "acme")
    monkeypatch.setattr(takyon_core, "_require_api_access", lambda op, **kw: {})
    monkeypatch.setattr(
        takyon_core,
        "_read_work_request_run",
        lambda _store, _run_id: ("completed", {"result": {"success": True, "summary": "done"}}),
    )
    monkeypatch.setattr(takyon_core, "_WORKER_DEFERRAL_POLL_SECONDS", 0.0)

    raw = takyon_core._defer_claude_agent_task_to_worker(
        {
            "business": "acme",
            "instruction": "build",
            "idempotency_key": "task-override",
            "workspace": "product/site",
        }
    )
    result = json.loads(raw)
    assert result["success"] is True
    assert result["summary"] == "done"
    assert store.commits[0]["operations"][0]["payload"]["args"]["workspace"] == "product/site"
    assert store.commits[0]["operations"][0]["payload"]["args"]["refresh_surface"] is True


def test_defer_product_surface_refresh_rejects_session_bound_call(monkeypatch):
    monkeypatch.setenv("TAKYON_OPERATOR_TASKS_VIA_WORKER", "1")
    monkeypatch.delenv("TAKYON_WORKER_PROCESS", raising=False)
    monkeypatch.setattr(takyon_core, "_session_business_slug", lambda: "acme")

    with pytest.raises(TakyonError, match="authority tool surface"):
        takyon_core._defer_product_surface_refresh_to_worker(
            {
                "business": "acme",
                "idempotency_key": "surface-1",
            }
        )


def test_defer_claude_agent_task_fails_if_worker_never_picks_up(monkeypatch):
    monkeypatch.setenv("TAKYON_OPERATOR_TASKS_VIA_WORKER", "1")
    monkeypatch.delenv("TAKYON_WORKER_PROCESS", raising=False)
    store = _DeferralStoreStub()
    monkeypatch.setattr(takyon_core, "_store", lambda: store)
    monkeypatch.setattr(takyon_core, "_require_api_access", lambda op, **kw: {})
    monkeypatch.setattr(takyon_core, "_read_work_request_run", lambda _store, _run_id: ("queued", {}))
    monkeypatch.setattr(takyon_core, "_WORKER_DEFERRAL_POLL_SECONDS", 0.0)
    monkeypatch.setattr(takyon_core, "_WORKER_PICKUP_TIMEOUT_SECONDS", 0.0)

    class _FakeTime:
        def __init__(self):
            self.now = 0.0

        def monotonic(self):
            return self.now

        def sleep(self, seconds):
            self.now += max(float(seconds), 1.0) * 100_000.0

    monkeypatch.setattr(takyon_core, "time", _FakeTime())

    raw = takyon_core._defer_claude_agent_task_to_worker(
        {
            "business": "acme",
            "instruction": "build",
            "idempotency_key": "task-queued",
            "workspace": "research",
        }
    )
    result = json.loads(raw)
    assert result["success"] is False
    assert "pickup deadline" in result["error"]
    assert result["status"] == "queued"
    assert result["run_id"] == "run-1"
    assert result["worker_job"] == "wj-1"


def test_record_claude_agent_runtime_event_includes_bound_run_id(monkeypatch):
    captured: list[dict[str, Any]] = []

    class _Conn:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    class _Store:
        def _connect(self):
            return _Conn()

        def _record_event(self, conn, *, scope, business_slug, event_type, payload):
            captured.append(
                {
                    "scope": scope,
                    "business_slug": business_slug,
                    "event_type": event_type,
                    "payload": dict(payload),
                }
            )

    monkeypatch.setattr(takyon_core, "_store", lambda: _Store())
    with takyon_core._bound_operator_task_run("run-123"):
        takyon_core._record_claude_agent_runtime_event(
            business="acme",
            workspace_rel="product/site",
            status="running",
            detail="still working",
        )

    assert captured
    assert captured[-1]["event_type"] == "dashboard.run.running"
    assert captured[-1]["payload"]["run_id"] == "run-123"


def test_operator_tool_task_handler_records_run_lifecycle(monkeypatch):
    """The worker handler calls the EXISTING tool function and owns the canonical run row:
    running -> terminal status mapped from the tool result, full result stored on the row, and
    actual_cost_cents=0 so run_one never double-settles the tool's internal budget rail."""
    from plugins.takyon import worker as takyon_worker

    calls = []

    def _record(slug, work_request_id, *, status, payload_updates=None, rewrite_distribution=True):
        calls.append((slug, work_request_id, status, payload_updates, rewrite_distribution))

    monkeypatch.setattr(takyon_worker, "_update_work_request", _record)
    monkeypatch.setattr(
        takyon_core,
        "handle_business_claude_agent_task",
        lambda args, **kw: json.dumps({"success": True, "summary": "ok", "actual_cost_cents": 42}),
    )

    def _job(payload):
        return takyon_worker.Job(
            id="job-1",
            business_slug="acme",
            kind="claude.agent_task",
            status="running",
            idempotency_key="ik-1",
            payload=payload,
            result=None,
            error=None,
            reserved_billing_entry_id=None,
            attempts=1,
            max_attempts=1,
            locked_by="w1",
            locked_at=None,
            created_at=None,
            updated_at=None,
        )

    outcome = takyon_worker.claude_agent_task_handler(
        _job({"args": {"business": "acme"}, "work_request_id": "wr-1"})
    )
    assert [(c[2], c[4]) for c in calls] == [("running", False), ("completed", False)]
    assert calls[-1][3]["result"]["summary"] == "ok"
    assert outcome.actual_cost_cents == 0
    assert outcome.result == {"status": "completed", "work_request_id": "wr-1"}

    # A recorded tool failure maps to a failed RUN but still a clean handler return (the run row
    # and agent_runs carry the truth; the job must not retry an expensive task).
    calls.clear()
    monkeypatch.setattr(
        takyon_core,
        "handle_business_claude_agent_task",
        lambda args, **kw: json.dumps({"success": False, "error": "boom"}),
    )
    outcome = takyon_worker.claude_agent_task_handler(
        _job({"args": {"business": "acme"}, "work_request_id": "wr-2"})
    )
    assert [c[2] for c in calls] == ["running", "failed"]
    assert outcome.result["status"] == "failed"


def test_operator_tool_task_handler_binds_owner_session_context(monkeypatch):
    from plugins.takyon import worker as takyon_worker

    observed = {}

    @contextmanager
    def _workspace(slug, *, operator_user_id=None, sync_on_exception=False):
        observed["workspace"] = {
            "slug": slug,
            "operator_user_id": operator_user_id,
            "sync_on_exception": sync_on_exception,
        }
        yield "/tmp/takyon-worker-test-home"

    def _set_session_vars(**kwargs):
        observed["set_session_vars"] = dict(kwargs)
        return ["token"]

    def _clear_session_vars(tokens):
        observed["clear_session_vars"] = list(tokens)

    @contextmanager
    def _bound_run(run_id):
        observed["run_id"] = run_id
        yield

    monkeypatch.setattr(takyon_worker, "_business_owner_user_id", lambda slug: "user-123")
    monkeypatch.setattr(takyon_worker, "_update_work_request", lambda *args, **kwargs: None)
    monkeypatch.setattr("plugins.takyon.cli._business_workspace_execution_context", _workspace)
    monkeypatch.setattr("gateway.session_context.set_session_vars", _set_session_vars)
    monkeypatch.setattr("gateway.session_context.clear_session_vars", _clear_session_vars)
    monkeypatch.setattr(takyon_core, "_bound_operator_task_run", _bound_run)
    monkeypatch.setattr(
        takyon_core,
        "handle_business_claude_agent_task",
        lambda args, **kw: json.dumps({"success": True, "summary": "ok"}),
    )

    job = takyon_worker.Job(
        id="job-ctx",
        business_slug="acme",
        kind="claude.agent_task",
        status="running",
        idempotency_key="ik-ctx",
        payload={"args": {"business": "acme"}, "work_request_id": "wr-ctx"},
        result=None,
        error=None,
        reserved_billing_entry_id=None,
        attempts=1,
        max_attempts=1,
        locked_by="w1",
        locked_at=None,
        created_at=None,
        updated_at=None,
    )
    outcome = takyon_worker.claude_agent_task_handler(job)

    assert observed["workspace"] == {
        "slug": "acme",
        "operator_user_id": "user-123",
        "sync_on_exception": True,
    }
    assert observed["set_session_vars"] == {
        "user_id": "user-123",
        "workspace_root": "/tmp/takyon-worker-test-home",
        "business_slug": "acme",
    }
    assert observed["clear_session_vars"] == ["token"]
    assert observed["run_id"] == "wr-ctx"
    assert outcome.result == {"status": "completed", "work_request_id": "wr-ctx"}

    # An unrecorded crash writes the failed run row, then re-raises so the JOB fails loudly.
    calls.clear()

    def _crash(args, **kw):
        raise RuntimeError("worker exploded")

    monkeypatch.setattr(takyon_core, "handle_business_claude_agent_task", _crash)
    with pytest.raises(RuntimeError, match="worker exploded"):
        takyon_worker.claude_agent_task_handler(
            _job({"args": {"business": "acme"}, "work_request_id": "wr-3"})
        )
    assert [c[2] for c in calls] == ["running", "failed"]
