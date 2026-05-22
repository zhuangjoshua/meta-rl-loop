"""Tests for the Takyon CEO operator plugin."""

from __future__ import annotations

import json
import sys
import types
from pathlib import Path

import pytest

from plugins.takyon.core import (
    TAKYON_TOOL_DEFINITIONS,
    TakyonError,
    TakyonStore,
    handle_business_list_businesses,
    handle_business_registry,
    handle_business_upsert_business,
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
        "ad-creative",
        "build-product",
        "business-learning",
        "ceo",
        "conversion-review",
        "distribution-campaign",
        "failure-recovery",
        "market-research",
        "outreach",
        "pricing-strategy",
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


def test_registry_tool_filters_by_category_and_priority():
    result = json.loads(
        handle_business_registry(
            {"kind": "tools", "category": "queue", "priority_band": "p2_growth"}
        )
    )
    assert result["success"] is True
    assert [tool["name"] for tool in result["tools"]] == ["business_enqueue_job"]
    assert "skills" not in result


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
            "business:latexflow/workspace:campaigns/finals",
            [{"action": "workspace.upsert", "path": "campaigns/finals"}],
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
