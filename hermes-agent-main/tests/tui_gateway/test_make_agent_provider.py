"""Regression tests for the TUI primary Claude Agent SDK facade."""

import os
import inspect
import threading
import types
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import pytest


def test_make_agent_builds_key_free_deepseek_sdk_facade():
    fake_cfg = {
        "model": {"default": "legacy-model", "provider": "legacy-provider"},
        "agent": {"system_prompt": "test overlay"},
    }
    with (
        patch("tui_gateway.server._load_cfg", return_value=fake_cfg),
        patch("tui_gateway.server._get_db", return_value=MagicMock()),
        patch("tui_gateway.server._load_tool_progress_mode", return_value="compact"),
        patch("tui_gateway.server._load_reasoning_config", return_value=None),
        patch("tui_gateway.server._load_service_tier", return_value=None),
    ):
        from tui_gateway.server import _make_agent

        agent = _make_agent("sid-1", "key-1")

    assert agent._takyon_primary_sdk is True
    assert agent.model == "deepseek-v4-pro"
    assert agent.provider == "safebox"
    assert agent.api_key == "scoped-at-call"
    assert "test overlay" in agent._cached_system_prompt


def test_make_agent_ignores_display_personality_without_system_prompt():
    fake_cfg = {
        "agent": {
            "system_prompt": "",
            "personalities": {"kawaii": "sparkle system prompt"},
        },
        "display": {"personality": "kawaii"},
    }
    with (
        patch("tui_gateway.server._load_cfg", return_value=fake_cfg),
        patch("tui_gateway.server._get_db", return_value=MagicMock()),
    ):
        from tui_gateway.server import _make_agent

        agent = _make_agent("sid-default-personality", "key-default-personality")

    assert agent.ephemeral_system_prompt is None
    assert "sparkle system prompt" not in agent._cached_system_prompt


def test_make_agent_honors_safe_tui_launch_env_flags():
    fake_cfg = {"agent": {"system_prompt": ""}}
    with (
        patch.dict(
            os.environ,
            {
                "TAKYON_TUI_MAX_TURNS": "7",
                "TAKYON_TUI_CHECKPOINTS": "1",
                "TAKYON_TUI_PASS_SESSION_ID": "1",
                "TAKYON_IGNORE_RULES": "1",
            },
        ),
        patch("tui_gateway.server._load_cfg", return_value=fake_cfg),
        patch("tui_gateway.server._get_db", return_value=MagicMock()),
    ):
        from tui_gateway.server import _make_agent

        agent = _make_agent("sid-env", "key-env")

    assert agent.max_iterations == 7
    assert agent.pass_session_id is True
    assert agent.skip_context_files is True
    assert agent.skip_memory is True
    # The primary SDK exposes no local file/git tools, so legacy checkpoints
    # remain unavailable even if an old TUI flag is still present.
    assert agent._checkpoint_mgr is None


def test_probe_config_health_flags_null_sections():
    from tui_gateway.server import _probe_config_health

    assert _probe_config_health({"agent": {"x": 1}}) == ""
    assert _probe_config_health({}) == ""
    msg = _probe_config_health({"agent": None, "display": None, "model": {}})
    assert "agent" in msg and "display" in msg
    assert "model" not in msg


def test_probe_config_health_flags_null_personalities_with_active_personality():
    from tui_gateway.server import _probe_config_health

    msg = _probe_config_health(
        {
            "agent": {"personalities": None},
            "display": {"personality": "kawaii"},
            "model": {},
        }
    )
    assert "display.personality" in msg
    assert "agent.personalities" in msg


def test_make_agent_tolerates_null_config_sections():
    null_cfg = {"agent": None, "display": None, "model": {"default": "legacy"}}
    with (
        patch("tui_gateway.server._load_cfg", return_value=null_cfg),
        patch("tui_gateway.server._get_db", return_value=MagicMock()),
    ):
        from tui_gateway.server import _make_agent

        agent = _make_agent("sid-null", "key-null")

    assert agent._takyon_primary_sdk is True
    assert agent.ephemeral_system_prompt is None


def test_make_agent_tolerates_null_personalities_with_active_personality():
    cfg = {
        "agent": {"personalities": None},
        "display": {"personality": "kawaii"},
    }
    with (
        patch("tui_gateway.server._load_cfg", return_value=cfg),
        patch("tui_gateway.server._get_db", return_value=MagicMock()),
    ):
        from tui_gateway.server import _make_agent

        agent = _make_agent("sid-null-personality", "key-null-personality")

    assert agent._takyon_primary_sdk is True
    assert agent.ephemeral_system_prompt is None


def test_isolated_turn_payload_uses_fresh_invocation_epoch_per_turn():
    from plugins.takyon.operator_gateway import PrimaryAgentFacade
    from tui_gateway.server import _build_isolated_turn_payload

    session = {"session_key": "ui-session-1"}
    agent = PrimaryAgentFacade(
        operator_user_id="11111111-1111-1111-1111-111111111111",
        business_slug="demo",
        workspace_root="/tmp/demo",
    )
    kwargs = {
        "operator_user_id": "11111111-1111-1111-1111-111111111111",
        "business_slug": "demo",
    }

    first = _build_isolated_turn_payload(session, agent, "one", [], **kwargs)
    second = _build_isolated_turn_payload(session, agent, "two", [], **kwargs)

    assert first["session_key"] == second["session_key"] == "ui-session-1"
    assert first["invocation_epoch"].startswith("interactive:")
    assert second["invocation_epoch"].startswith("interactive:")
    assert first["invocation_epoch"] != second["invocation_epoch"]


def test_primary_facade_fails_closed_without_business_scope(monkeypatch):
    from plugins.takyon.operator_gateway import PrimaryAgentFacade

    monkeypatch.setattr("gateway.session_context.get_session_env", lambda *_args: "")
    agent = PrimaryAgentFacade(
        operator_user_id="11111111-1111-1111-1111-111111111111",
        business_slug="",
        workspace_root="/tmp/demo",
    )

    with pytest.raises(RuntimeError, match="global model chat is disabled"):
        agent._scope()


def test_prompt_submit_allows_global_model_chat_before_start(monkeypatch):
    from tui_gateway import server

    sid = "global-sdk-session"
    session = {
        "session_key": sid,
        "history": [],
        "history_lock": threading.Lock(),
        "running": False,
        "takyon_current_business": "",
        "agent": types.SimpleNamespace(_takyon_primary_sdk=True),
    }
    server._sessions[sid] = session
    monkeypatch.setattr(server, "_bind_takyon_operator_user_id", lambda *_args: "owner")
    started = {}
    monkeypatch.setattr(
        server,
        "_start_streaming_session_turn",
        lambda rid, session_id, bound_session, text, **kwargs: started.update(
            rid=rid,
            session_id=session_id,
            session=bound_session,
            text=text,
            kwargs=kwargs,
        ),
    )
    try:
        response = server.handle_request(
            {
                "id": "req-1",
                "method": "prompt.submit",
                "params": {"session_id": sid, "text": "hello"},
            }
        )
    finally:
        server._sessions.pop(sid, None)

    assert response["result"]["status"] == "streaming"
    assert started["session_id"] == sid
    assert started["text"] == "hello"
    assert started["kwargs"]["contextualize_takyon"] is True
    assert session["running"] is True


def test_cli_and_dashboard_live_turn_paths_have_no_hermes_agent_fallback():
    from plugins.takyon import cli
    from tui_gateway import server

    cli_turn = inspect.getsource(cli._run_agent_with_meta)
    dashboard_turn = inspect.getsource(server._run_prompt_submit)
    facade_builder = inspect.getsource(server._make_agent)

    assert "run_primary_sdk_subprocess" in cli_turn
    assert "build_operator_gateway_agent" not in cli_turn
    assert "run_conversation(" not in cli_turn
    assert "run_conversation(" not in dashboard_turn
    assert "_run_isolated_gateway_turn(" in dashboard_turn
    assert "PrimaryAgentFacade" not in facade_builder
    assert "build_primary_agent_facade" in facade_builder


def test_dashboard_surfaces_every_published_skill_from_approved_manifest(monkeypatch):
    from plugins.takyon.operator_gateway import PrimaryAgentFacade
    from tui_gateway import server

    monkeypatch.delenv("TAKYON_CLAUDE_SKILLS_MANIFEST", raising=False)
    manifest = server._load_approved_skills_manifest()
    expected = {
        item["name"]
        for item in manifest["skills"]
        if isinstance(item, dict) and item.get("name")
    }
    agent = PrimaryAgentFacade()

    surfaced = set(server._session_info(agent)["skills"]["takyon"])
    catalogued = {item["name"] for item in server._takyon_skill_lab_catalog()}

    assert expected
    assert surfaced == expected
    assert catalogued == expected


def test_sdk_facade_explicitly_retires_mutable_tool_and_compact_operations():
    from plugins.takyon.operator_gateway import PrimaryAgentFacade

    agent = PrimaryAgentFacade()
    with pytest.raises(RuntimeError, match="immutable"):
        agent.refresh_tools()
    with pytest.raises(RuntimeError, match="immutable"):
        agent.reload_mcp_tools()
    with pytest.raises(RuntimeError, match="manual UI compression"):
        agent._compress_context([])


def test_sdk_session_rejects_idle_steer_and_keeps_reload_immutable(monkeypatch):
    from plugins.takyon.operator_gateway import PrimaryAgentFacade
    from tui_gateway import server

    sid = "sdk-contract-status"
    owner = "00000000-0000-4000-8000-000000000001"
    agent = PrimaryAgentFacade()
    compact_calls = []
    agent.compact_session = lambda **kwargs: (
        compact_calls.append(kwargs)
        or {
            "compact_receipt": {
                "trigger": "manual",
                "pre_tokens": 1200,
                "post_tokens": 300,
            }
        }
    )
    session = {
        "session_key": sid,
        "history": [],
        "history_lock": threading.Lock(),
        "running": False,
        "agent": agent,
        "takyon_operator_user_id": owner,
    }
    server._sessions[sid] = session
    monkeypatch.setattr(server, "_start_agent_build", lambda *_args: None)
    monkeypatch.setattr(server, "_wait_agent", lambda *_args: None)
    try:
        steer = server.handle_request(
            {
                "id": "steer",
                "method": "session.steer",
                "params": {"session_id": sid, "text": "change direction"},
            }
        )
        reload_result = server.handle_request(
            {
                "id": "reload",
                "method": "reload.mcp",
                "params": {"session_id": sid, "confirm": True},
            }
        )
        compact = server.handle_request(
            {
                "id": "compact",
                "method": "session.compress",
                "params": {"session_id": sid},
            }
        )
    finally:
        server._sessions.pop(sid, None)

    assert steer["result"]["status"] == "rejected"
    assert reload_result["result"]["status"] == "immutable"
    assert compact["result"]["status"] == "compacted"
    assert compact["result"]["compact_receipt"]["trigger"] == "manual"
    assert compact["result"]["summary"]["headline"] == "SDK context compacted"
    assert len(compact_calls) == 1
    assert compact_calls[0]["session_id"] == sid
    assert compact_calls[0]["focus_topic"] is None
    assert compact_calls[0]["operator_user_id"] == owner
    assert compact_calls[0]["business_slug"] == ""
    assert compact_calls[0]["workspace_root"].endswith(
        f"runtime/operator-workspaces/{owner}"
    )


def test_sdk_compress_uses_authenticated_business_scope_and_durable_key(
    monkeypatch, tmp_path
):
    from plugins.takyon import turn_runtime
    from plugins.takyon.operator_gateway import PrimaryAgentFacade
    from tui_gateway import server
    from tui_gateway.transport import bind_transport, reset_transport

    owner = "00000000-0000-4000-8000-000000000003"
    gateway_sid = "ephemeral-gateway-id"
    durable_key = "durable-ui-session-key"
    workspace = tmp_path / "mounted-business"
    workspace.mkdir()
    captured = {}

    @contextmanager
    def mounted(slug, *, operator_user_id=None, **_kwargs):
        captured["mount"] = (slug, operator_user_id)
        yield workspace

    agent = PrimaryAgentFacade()

    def compact(**kwargs):
        captured["compact"] = kwargs
        return {
            "compact_receipt": {
                "trigger": "manual",
                "pre_tokens": 800,
                "post_tokens": 200,
            }
        }

    agent.compact_session = compact
    transport = types.SimpleNamespace(
        operator_principal=types.SimpleNamespace(user_id=owner)
    )
    server._sessions[gateway_sid] = {
        "session_key": durable_key,
        "history": [],
        "history_lock": threading.Lock(),
        "running": False,
        "agent": agent,
        "transport": transport,
        "takyon_operator_user_id": owner,
        "takyon_current_business": "acme",
    }
    monkeypatch.setattr(
        turn_runtime, "_business_workspace_execution_context", mounted
    )
    token = bind_transport(transport)
    try:
        response = server.handle_request(
            {
                "id": "compact",
                "method": "session.compress",
                "params": {"session_id": gateway_sid},
            }
        )
    finally:
        reset_transport(token)
        server._sessions.pop(gateway_sid, None)

    assert response["result"]["status"] == "compacted"
    assert captured["mount"] == ("acme", owner)
    assert captured["compact"] == {
        "session_id": durable_key,
        "focus_topic": None,
        "operator_user_id": owner,
        "business_slug": "acme",
        "workspace_root": str(workspace.resolve()),
    }


def test_sdk_compact_and_reload_slashes_never_enter_legacy_worker(monkeypatch):
    from plugins.takyon.operator_gateway import PrimaryAgentFacade
    from tui_gateway import server

    sid = "sdk-control-slashes"
    owner = "00000000-0000-4000-8000-000000000002"
    agent = PrimaryAgentFacade()
    compact_calls = []
    agent.compact_session = lambda **kwargs: (
        compact_calls.append(kwargs)
        or {
            "compact_receipt": {
                "trigger": "manual",
                "pre_tokens": 900,
                "post_tokens": 200,
            }
        }
    )
    server._sessions[sid] = {
        "session_key": sid,
        "history": [],
        "history_lock": threading.Lock(),
        "running": False,
        "agent": agent,
        "takyon_operator_user_id": owner,
    }
    monkeypatch.setattr(
        server,
        "_SlashWorker",
        lambda *_args, **_kwargs: pytest.fail("legacy slash worker must not start"),
    )
    try:
        compact = server.handle_request(
            {
                "id": "compact",
                "method": "slash.exec",
                "params": {
                    "session_id": sid,
                    "command": "compress preserve decisions",
                },
            }
        )
        reload_result = server.handle_request(
            {
                "id": "reload",
                "method": "slash.exec",
                "params": {"session_id": sid, "command": "reload-mcp"},
            }
        )
    finally:
        server._sessions.pop(sid, None)

    assert "900 → 200 tokens" in compact["result"]["output"]
    assert "immutable" in reload_result["result"]["output"]
    assert len(compact_calls) == 1
    assert compact_calls[0]["session_id"] == sid
    assert compact_calls[0]["focus_topic"] == "preserve decisions"
    assert compact_calls[0]["operator_user_id"] == owner
    assert compact_calls[0]["business_slug"] == ""
