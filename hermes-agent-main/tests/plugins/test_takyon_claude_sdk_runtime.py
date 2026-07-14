from __future__ import annotations

import json
import os
import signal
import socket
import hashlib
import io
import subprocess
import threading
import time
import uuid

import pytest

from plugins.takyon.claude_sdk_runtime import (
    ClaudeSdkRuntimeError,
    InMemorySessionStoreBackend,
    ScopedToolBridge,
    ToolBridgeScope,
    _build_skill_resource_reader,
    _skill_resource_tool_definition,
    _primary_sdk_node_runtime,
    build_primary_sdk_env,
    enforce_sdk_mode_tool_policy,
    sdk_tool_definitions,
    stable_sdk_session_id,
)


def test_stable_sdk_session_id_preserves_uuid_and_maps_other_ids() -> None:
    existing = str(uuid.uuid4())
    assert stable_sdk_session_id(existing) == existing
    assert stable_sdk_session_id("job-123") == stable_sdk_session_id("job-123")
    assert stable_sdk_session_id("job-123") != stable_sdk_session_id("job-124")


def test_sdk_tool_definitions_remove_legacy_delegation(monkeypatch) -> None:
    definitions = [
        {
            "type": "function",
            "function": {
                "name": "business_read_business",
                "description": "read",
                "parameters": {"type": "object", "properties": {}},
            },
        },
        {
            "type": "function",
            "function": {
                "name": "business_claude_agent_task",
                "description": "delegate",
                "parameters": {"type": "object", "properties": {}},
            },
        },
        {
            "type": "function",
            "function": {
                "name": "skill_view",
                "description": "legacy skill loader",
                "parameters": {"type": "object", "properties": {}},
            },
        },
    ]
    monkeypatch.setattr("model_tools.get_tool_definitions", lambda **_kwargs: definitions)

    selected = sdk_tool_definitions(enabled_toolsets=["takyon", "skills"])

    assert [item["name"] for item in selected] == ["business_read_business"]


def test_sdk_tool_definitions_register_web_handlers_for_standalone_cli(monkeypatch) -> None:
    imports: list[str] = []
    selections: list[dict] = []
    monkeypatch.setattr(
        "plugins.takyon.claude_sdk_runtime.importlib.import_module",
        lambda name: imports.append(name),
    )
    monkeypatch.setattr(
        "model_tools.get_tool_definitions",
        lambda **kwargs: selections.append(kwargs) or [],
    )

    sdk_tool_definitions(
        enabled_toolsets=["takyon", "web"],
        disabled_toolsets=["browser", "terminal"],
    )

    assert imports == ["tools.web_tools"]
    assert selections == [
        {
            "enabled_toolsets": ["takyon", "web"],
            "disabled_toolsets": ["terminal"],
            "quiet_mode": True,
        }
    ]


def test_primary_sdk_env_is_key_free_and_authorized_by_scoped_capability(
    monkeypatch, tmp_path
) -> None:
    from plugins.takyon import core, safebox
    import takyon_constants

    monkeypatch.setenv("ANTHROPIC_API_KEY", "raw-anthropic-key")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "raw-deepseek-key")
    monkeypatch.setenv("DATABASE_URL", "postgres://operator-authority")
    monkeypatch.setenv("TAKYON_OPERATOR_DATABASE_URL", "postgres://operator-authority")
    monkeypatch.setenv("TAKYON_SAFEBOX_TOKEN", "transport-secret")
    monkeypatch.setattr(takyon_constants, "get_takyon_home", lambda: tmp_path)
    monkeypatch.setattr(
        safebox,
        "provider_proxy_base_url",
        lambda: "http://10.116.0.2:8000",
    )
    minted: list[tuple[str | None, str, dict]] = []

    def mint(business, owner, **kwargs):
        minted.append((business, owner, kwargs))
        return "safebox-operator-session-capability"

    monkeypatch.setattr(safebox, "mint_operator_session_token", mint)
    monkeypatch.setattr(core, "_resolve_claude_agent_model", lambda _model: "deepseek-v4-pro")
    monkeypatch.setattr(
        core,
        "_claude_agent_model_aliases",
        lambda model: {
            "TAKYON_CLAUDE_AGENT_MODEL": model,
            "ANTHROPIC_MODEL": model,
            "ANTHROPIC_DEFAULT_OPUS_MODEL": model,
            "ANTHROPIC_DEFAULT_SONNET_MODEL": model,
            "ANTHROPIC_DEFAULT_HAIKU_MODEL": model,
            "CLAUDE_CODE_SUBAGENT_MODEL": model,
        },
    )

    env = build_primary_sdk_env(
        business="acme",
        operator_user_id="operator-1",
        invocation_id=str(uuid.uuid4()),
        max_total_cost_microusd=2_000_000,
        max_cost_microusd=500_000,
    )

    assert minted[0][:2] == ("acme", "operator-1")
    assert minted[0][2]["max_total_cost_microusd"] == 2_000_000
    assert minted[0][2]["max_cost_microusd"] == 500_000
    assert env["ANTHROPIC_API_KEY"] == "safebox-operator-session-capability"
    assert env["ANTHROPIC_BASE_URL"] == "http://10.116.0.2:8000"
    assert env["TAKYON_CLAUDE_AGENT_MODEL"] == "deepseek-v4-pro"
    for forbidden in (
        "DEEPSEEK_API_KEY",
        "DATABASE_URL",
        "TAKYON_OPERATOR_DATABASE_URL",
        "TAKYON_SAFEBOX_TOKEN",
    ):
        assert forbidden not in env


def _bridge_client(bridge: ScopedToolBridge):
    duplicated = os.dup(bridge.child_fd)
    client = socket.socket(fileno=duplicated)
    return client, client.makefile("r", encoding="utf-8"), client.makefile(
        "w", encoding="utf-8"
    )


def test_scoped_tool_bridge_injects_business_and_dispatches() -> None:
    calls = []

    def dispatch(name, args, **kwargs):
        calls.append((name, args, kwargs))
        return json.dumps({"success": True, "business": args["business"]})

    bridge = ScopedToolBridge(
        tool_definitions=[
            {
                "name": "business_read_business",
                "description": "read",
                "inputSchema": {"type": "object", "properties": {}},
            }
        ],
        scope=ToolBridgeScope(
            operator_user_id="user-1",
            business="acme",
            session_id="session-1",
            task_id="task-1",
        ),
        dispatcher=dispatch,
    ).start()
    client, reader, writer = _bridge_client(bridge)
    try:
        writer.write(
            json.dumps(
                {
                    "id": "tool-1",
                    "type": "tool",
                    "name": "business_read_business",
                    "args": {},
                }
            )
            + "\n"
        )
        writer.flush()
        response = json.loads(reader.readline())
    finally:
        writer.close()
        reader.close()
        client.close()
        bridge.close()

    assert response["ok"] is True
    assert calls[0][1]["business"] == "acme"
    assert calls[0][2]["enabled_tools"] == ["business_read_business"]


def test_scoped_tool_bridge_refuses_cross_business_call() -> None:
    bridge = ScopedToolBridge(
        tool_definitions=[
            {
                "name": "business_write_file",
                "description": "write",
                "inputSchema": {"type": "object", "properties": {}},
            }
        ],
        scope=ToolBridgeScope(operator_user_id="user-1", business="acme"),
        dispatcher=lambda *_args, **_kwargs: pytest.fail("dispatcher must not run"),
    ).start()
    client, reader, writer = _bridge_client(bridge)
    try:
        writer.write(
            json.dumps(
                {
                    "id": "tool-2",
                    "type": "tool",
                    "name": "business_write_file",
                    "args": {"business": "other", "path": "x", "content": "x"},
                }
            )
            + "\n"
        )
        writer.flush()
        response = json.loads(reader.readline())
    finally:
        writer.close()
        reader.close()
        client.close()
        bridge.close()

    assert response["ok"] is False
    assert "cross-business" in response["error"]


def test_scoped_tool_bridge_refuses_unlisted_tool() -> None:
    bridge = ScopedToolBridge(
        tool_definitions=[],
        scope=ToolBridgeScope(operator_user_id="user-1"),
        dispatcher=lambda *_args, **_kwargs: pytest.fail("dispatcher must not run"),
    ).start()
    client, reader, writer = _bridge_client(bridge)
    try:
        writer.write(
            json.dumps(
                {
                    "id": "tool-3",
                    "type": "tool",
                    "name": "business_delete_business",
                    "args": {},
                }
            )
            + "\n"
        )
        writer.flush()
        response = json.loads(reader.readline())
    finally:
        writer.close()
        reader.close()
        client.close()
        bridge.close()

    assert response["ok"] is False
    assert "not allowed" in response["error"]


def _policy_manifest(tmp_path, *, allowed_tools=None):
    inventory = ["dangerous_tool", "safe_tool"]
    digest = "sha256:" + hashlib.sha256(
        "\0".join(inventory).encode("utf-8")
    ).hexdigest()
    manifest = {
        "plugin": {"name": "test-approved-skills", "version": "1.0.0"},
        "capability_bindings": {
            "business.safe.read": {
                "adapter": "mcp",
                "tools": ["safe_tool"],
                "scope": "current_business",
                "authority": "operator_session",
            },
            "business.danger.control": {
                "adapter": "mcp",
                "tools": ["dangerous_tool"],
                "scope": "current_business",
                "authority": "operator_session",
            },
        },
        "capability_tools": {
            "business.safe.read": ["safe_tool"],
            "business.danger.control": ["dangerous_tool"],
        },
        "model_tool_inventory": inventory,
        "model_tool_inventory_digest": digest,
        "handoff_guidance": "Use the compiled capability bindings.",
        "mode_tool_policy": {
            "bootstrap": {
                "allowed_skills": ["safe-skill"],
                "baseline_tools": ["safe_tool"],
                "allowed_tools": list(allowed_tools or ["safe_tool"]),
                "denied_capabilities": ["business.danger.control"],
                "denied_tools": ["dangerous_tool"],
                "denied_write_paths": ["product/site"],
            }
        },
        "skills": [
            {
                "name": "safe-skill",
                "allowed_modes": ["bootstrap"],
                "plugin_path": "skills/safe-skill",
                "publish_files": [
                    "skills/safe-skill/SKILL.md",
                    "skills/safe-skill/references/guide.md",
                ],
            },
            {
                "name": "wake-only-skill",
                "allowed_modes": ["wake"],
                "plugin_path": "skills/wake-only-skill",
                "publish_files": ["skills/wake-only-skill/SKILL.md"],
            },
        ],
    }
    path = tmp_path / "approved-skills.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    return path


def test_mode_policy_is_exact_allowlist_plus_parent_resource_tool(tmp_path) -> None:
    manifest = _policy_manifest(tmp_path)
    selected, policy = enforce_sdk_mode_tool_policy(
        manifest_path=manifest,
        mode="ceo_bootstrap",
        tool_definitions=[
            {"name": "safe_tool", "inputSchema": {"type": "object"}},
            {"name": "dangerous_tool", "inputSchema": {"type": "object"}},
            _skill_resource_tool_definition(),
        ],
    )

    assert [item["name"] for item in selected] == [
        "safe_tool",
        "skill_read_resource",
    ]
    assert policy.allowed_tools == ("safe_tool",)


def test_mode_policy_refuses_unknown_allowed_tool(tmp_path) -> None:
    manifest = _policy_manifest(tmp_path, allowed_tools=["unknown_tool"])
    with pytest.raises(ClaudeSdkRuntimeError, match="allowed_tools"):
        enforce_sdk_mode_tool_policy(
            manifest_path=manifest,
            mode="bootstrap",
            tool_definitions=[_skill_resource_tool_definition()],
        )


def test_mode_policy_refuses_allowed_skill_mode_drift(tmp_path) -> None:
    manifest_path = _policy_manifest(tmp_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["skills"][0]["allowed_modes"] = ["interactive"]
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(ClaudeSdkRuntimeError, match="allowed_skills drifted"):
        enforce_sdk_mode_tool_policy(
            manifest_path=manifest_path,
            mode="bootstrap",
            tool_definitions=[
                {"name": "safe_tool", "inputSchema": {"type": "object"}},
                _skill_resource_tool_definition(),
            ],
        )


def test_mode_policy_refuses_capability_binding_policy_drift(tmp_path) -> None:
    manifest_path = _policy_manifest(tmp_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["capability_bindings"]["business.safe.read"]["scope"] = "any_business"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(ClaudeSdkRuntimeError, match="invalid or drifted binding"):
        enforce_sdk_mode_tool_policy(
            manifest_path=manifest_path,
            mode="bootstrap",
            tool_definitions=[
                {"name": "safe_tool", "inputSchema": {"type": "object"}},
                _skill_resource_tool_definition(),
            ],
        )


def test_skill_resource_reader_is_manifest_exact_and_blocks_escape(tmp_path) -> None:
    plugin = tmp_path / "plugin"
    skill = plugin / "skills" / "safe-skill"
    (skill / "references").mkdir(parents=True)
    (skill / "SKILL.md").write_text("skill", encoding="utf-8")
    (skill / "references" / "guide.md").write_text("guide", encoding="utf-8")
    wake_only = plugin / "skills" / "wake-only-skill"
    wake_only.mkdir(parents=True)
    (wake_only / "SKILL.md").write_text("wake only", encoding="utf-8")
    (plugin / "secret.txt").write_text("secret", encoding="utf-8")
    manifest = _policy_manifest(plugin)
    reader = _build_skill_resource_reader(
        plugin_root=plugin,
        manifest_path=manifest,
        allowed_skills=("safe-skill",),
    )

    assert reader({"skill": "safe-skill", "path": "references//./guide.md"}) == "guide"
    assert reader(
        {
            "skill": "test-approved-skills:safe-skill",
            "path": "references/guide.md",
        }
    ) == "guide"
    assert reader({"skill": "safe-skill", "path": "SKILL.md"}) == "skill"
    with pytest.raises(ClaudeSdkRuntimeError, match="manifest-approved"):
        reader({"skill": "wake-only-skill", "path": "SKILL.md"})
    with pytest.raises(ClaudeSdkRuntimeError, match="manifest-approved"):
        reader({"skill": "other-plugin:safe-skill", "path": "SKILL.md"})
    with pytest.raises(ClaudeSdkRuntimeError, match="manifest-approved"):
        reader(
            {
                "skill": "test-approved-skills:safe-skill:extra",
                "path": "SKILL.md",
            }
        )
    with pytest.raises(ClaudeSdkRuntimeError):
        reader({"skill": "safe-skill", "path": "../../secret.txt"})
    with pytest.raises(ClaudeSdkRuntimeError):
        reader(
            {
                "skill": "test-approved-skills:safe-skill",
                "path": "../../secret.txt",
            }
        )
    with pytest.raises(ClaudeSdkRuntimeError):
        reader({"skill": "safe-skill", "path": "unpublished.md"})


@pytest.mark.parametrize(
    "path",
    ["product/site/index.tsx", "product\\site//./index.tsx", "../product/site"],
)
def test_scoped_bridge_canonicalizes_and_refuses_mode_write_paths(path) -> None:
    bridge = ScopedToolBridge(
        tool_definitions=[
            {
                "name": "business_write_file",
                "description": "write",
                "inputSchema": {"type": "object"},
            }
        ],
        scope=ToolBridgeScope(operator_user_id="user-1", business="acme"),
        dispatcher=lambda *_args, **_kwargs: pytest.fail("dispatcher must not run"),
        denied_write_paths=["product/site"],
    ).start()
    client, reader, writer = _bridge_client(bridge)
    try:
        writer.write(
            json.dumps(
                {
                    "id": "write-1",
                    "type": "tool",
                    "name": "business_write_file",
                    "args": {"path": path, "content": "x"},
                }
            )
            + "\n"
        )
        writer.flush()
        response = json.loads(reader.readline())
    finally:
        writer.close()
        reader.close()
        client.close()
        bridge.close()
    assert response["ok"] is False


def test_bridge_close_pins_until_synchronous_side_effect_returns() -> None:
    entered = threading.Event()
    release = threading.Event()

    def dispatch(*_args, **_kwargs):
        entered.set()
        assert release.wait(5)
        return "done"

    bridge = ScopedToolBridge(
        tool_definitions=[{"name": "safe_tool", "inputSchema": {"type": "object"}}],
        scope=ToolBridgeScope(operator_user_id="user-1"),
        dispatcher=dispatch,
    ).start()
    client, _reader, writer = _bridge_client(bridge)
    writer.write(
        json.dumps({"id": "slow", "type": "tool", "name": "safe_tool", "args": {}})
        + "\n"
    )
    writer.flush()
    assert entered.wait(2)
    closing = threading.Thread(target=bridge.close)
    closing.start()
    time.sleep(0.05)
    assert closing.is_alive()
    release.set()
    closing.join(2)
    writer.close()
    client.close()
    assert not closing.is_alive()


def test_session_bridge_close_pins_until_inflight_append_returns() -> None:
    entered = threading.Event()
    release = threading.Event()
    session_id = str(uuid.uuid4())
    project_key = "takyon:test:session-close"

    class BlockingSessionStore(InMemorySessionStoreBackend):
        def append(self, key, entries) -> None:
            entered.set()
            assert release.wait(5)
            super().append(key, entries)

    bridge = ScopedToolBridge(
        tool_definitions=[],
        scope=ToolBridgeScope(
            operator_user_id="user-1",
            session_id=session_id,
            session_project_key=project_key,
        ),
        session_store=BlockingSessionStore(),
    ).start()
    client, _reader, writer = _bridge_client(bridge)
    writer.write(
        json.dumps(
            {
                "id": "append",
                "type": "session_append",
                "key": {"projectKey": project_key, "sessionId": session_id},
                "entries": [{"type": "assistant", "uuid": "entry-1"}],
            }
        )
        + "\n"
    )
    writer.flush()
    assert entered.wait(2)
    closing = threading.Thread(target=bridge.close)
    closing.start()
    time.sleep(0.05)
    assert closing.is_alive()
    release.set()
    closing.join(2)
    writer.close()
    client.close()
    assert not closing.is_alive()


def test_session_bridge_lane_acks_while_tool_lane_is_blocked() -> None:
    entered = threading.Event()
    release = threading.Event()
    simulated_append_budget = 0.05
    session_id = str(uuid.uuid4())
    project_key = "takyon:test:separate-session-lane"
    scope = ToolBridgeScope(
        operator_user_id="user-1",
        session_id=session_id,
        session_project_key=project_key,
    )
    store = InMemorySessionStoreBackend()

    def dispatch(*_args, **_kwargs):
        entered.set()
        assert release.wait(5)
        return "done"

    tool_bridge = ScopedToolBridge(
        tool_definitions=[{"name": "slow_tool", "inputSchema": {"type": "object"}}],
        scope=scope,
        dispatcher=dispatch,
    ).start()
    session_bridge = ScopedToolBridge(
        tool_definitions=[],
        scope=scope,
        session_store=store,
    ).start()
    tool_client, tool_reader, tool_writer = _bridge_client(tool_bridge)
    session_client, session_reader, session_writer = _bridge_client(session_bridge)
    try:
        tool_writer.write(
            json.dumps(
                {"id": "slow", "type": "tool", "name": "slow_tool", "args": {}}
            )
            + "\n"
        )
        tool_writer.flush()
        assert entered.wait(1)
        time.sleep(simulated_append_budget * 1.5)

        session_client.settimeout(1)
        append_started = time.monotonic()
        session_writer.write(
            json.dumps(
                {
                    "id": "append",
                    "type": "session_append",
                    "key": {"projectKey": project_key, "sessionId": session_id},
                    "entries": [{"type": "assistant", "uuid": "entry-1"}],
                }
            )
            + "\n"
        )
        session_writer.flush()
        append_response = json.loads(session_reader.readline())
        append_elapsed = time.monotonic() - append_started

        assert append_response["ok"] is True
        assert append_elapsed < 1
        assert release.is_set() is False
        assert store.load(
            {"projectKey": project_key, "sessionId": session_id}
        ) == [{"type": "assistant", "uuid": "entry-1"}]

        release.set()
        tool_client.settimeout(1)
        assert json.loads(tool_reader.readline())["ok"] is True
    finally:
        release.set()
        for stream in (session_writer, session_reader, tool_writer, tool_reader):
            stream.close()
        session_client.close()
        tool_client.close()
        session_bridge.close()
        tool_bridge.close()


def _node_runtime(tmp_path, *, sdk_version="0.3.148", zod_version="4.4.3"):
    root = tmp_path / "node-runtime"
    sdk = root / "node_modules" / "@anthropic-ai" / "claude-agent-sdk"
    zod = root / "node_modules" / "zod"
    sdk.mkdir(parents=True)
    zod.mkdir(parents=True)
    (sdk / "sdk.mjs").write_text("export const query = 1;", encoding="utf-8")
    (sdk / "package.json").write_text(
        json.dumps({"version": sdk_version}), encoding="utf-8"
    )
    (zod / "index.js").write_text("export const z = {};", encoding="utf-8")
    (zod / "package.json").write_text(
        json.dumps({"version": zod_version}), encoding="utf-8"
    )
    return root, sdk / "sdk.mjs", zod / "index.js"


def test_node_runtime_requires_exact_locked_dependencies_and_node20(
    monkeypatch, tmp_path
) -> None:
    root, sdk_module, zod_module = _node_runtime(tmp_path)
    monkeypatch.setenv("TAKYON_CLAUDE_NODE_RUNTIME", str(root))
    monkeypatch.delenv("TAKYON_CLAUDE_AGENT_SDK_MODULE", raising=False)
    monkeypatch.delenv("TAKYON_CLAUDE_ZOD_MODULE", raising=False)
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess(
            ["node", "--version"], 0, stdout="v20.19.0\n", stderr=""
        ),
    )

    node, sdk, zod = _primary_sdk_node_runtime(child_path=os.environ["PATH"])

    assert node
    assert sdk == sdk_module.resolve()
    assert zod == zod_module.resolve()


def test_node_runtime_rejects_version_and_writable_module(monkeypatch, tmp_path) -> None:
    root, sdk_module, _zod_module = _node_runtime(tmp_path, sdk_version="0.3.147")
    monkeypatch.setenv("TAKYON_CLAUDE_NODE_RUNTIME", str(root))
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess(
            ["node", "--version"], 0, stdout="v20.0.0\n", stderr=""
        ),
    )
    with pytest.raises(ClaudeSdkRuntimeError, match="0.3.148"):
        _primary_sdk_node_runtime(child_path=os.environ["PATH"])

    (sdk_module.parent / "package.json").write_text(
        json.dumps({"version": "0.3.148"}), encoding="utf-8"
    )
    sdk_module.chmod(0o666)
    with pytest.raises(ClaudeSdkRuntimeError, match="group/world writable"):
        _primary_sdk_node_runtime(child_path=os.environ["PATH"])


def test_node_runtime_repo_fallback_is_explicit_and_never_production(
    monkeypatch,
) -> None:
    monkeypatch.delenv("TAKYON_CLAUDE_NODE_RUNTIME", raising=False)
    monkeypatch.delenv("TAKYON_CLAUDE_SDK_ALLOW_REPO_NODE_MODULES", raising=False)
    with pytest.raises(ClaudeSdkRuntimeError, match="explicit dev fallback"):
        _primary_sdk_node_runtime(child_path=os.environ["PATH"])
    monkeypatch.setenv("TAKYON_CLAUDE_SDK_ALLOW_REPO_NODE_MODULES", "1")
    monkeypatch.setenv("TAKYON_ENV", "prod")
    with pytest.raises(ClaudeSdkRuntimeError, match="explicit dev fallback"):
        _primary_sdk_node_runtime(child_path=os.environ["PATH"])


def test_second_bridge_constructor_failure_closes_first_bridge(
    monkeypatch, tmp_path
) -> None:
    from plugins.takyon import claude_sdk_runtime as runtime

    policy = runtime.SdkModeToolPolicy(
        mode="interactive",
        allowed_skills=("safe-skill",),
        baseline_tools=("safe_tool",),
        allowed_tools=("safe_tool",),
        denied_capabilities=(),
        denied_tools=(),
        denied_write_paths=(),
        handoff_guidance="Use the scoped tool bridge.",
    )
    definitions = [{"name": "safe_tool", "inputSchema": {"type": "object"}}]
    monkeypatch.setattr(
        runtime,
        "_primary_sdk_install_paths",
        lambda: (
            tmp_path / "entrypoint.mjs",
            tmp_path / "plugin",
            tmp_path / "manifest.json",
        ),
    )
    monkeypatch.setattr(
        runtime, "sdk_tool_definitions", lambda **_kwargs: list(definitions)
    )
    monkeypatch.setattr(
        runtime,
        "enforce_sdk_mode_tool_policy",
        lambda **_kwargs: (list(definitions), policy),
    )
    monkeypatch.setattr(
        runtime,
        "_build_skill_resource_reader",
        lambda **_kwargs: lambda _args: "",
    )
    monkeypatch.setattr(
        runtime,
        "build_primary_sdk_env",
        lambda **_kwargs: {
            "PATH": os.environ.get("PATH", ""),
            "ANTHROPIC_API_KEY": "scoped-capability",
            "CLAUDE_CONFIG_DIR": str(tmp_path / "config"),
        },
    )
    monkeypatch.setattr(
        runtime,
        "_primary_sdk_node_runtime",
        lambda **_kwargs: ("node", tmp_path / "sdk.mjs", tmp_path / "zod.mjs"),
    )
    original_bridge = runtime.ScopedToolBridge
    first_bridge = None
    bridge_count = 0

    def construct_bridge(**kwargs):
        nonlocal bridge_count, first_bridge
        bridge_count += 1
        if bridge_count == 2:
            raise OSError("forced SessionStore bridge constructor failure")
        first_bridge = original_bridge(**kwargs)
        return first_bridge

    monkeypatch.setattr(runtime, "ScopedToolBridge", construct_bridge)

    with pytest.raises(OSError, match="forced SessionStore"):
        runtime.run_primary_sdk_subprocess(
            business="acme",
            operator_user_id="operator-1",
            system_prompt="system",
            user_prompt="turn",
            enabled_toolsets=["takyon"],
            workspace_root=tmp_path,
            session_id=str(uuid.uuid4()),
            resume_session=False,
            session_store=runtime.InMemorySessionStoreBackend(),
            mode="interactive",
            epoch="interactive:test",
            max_turns=2,
            max_budget_usd=1,
        )

    assert first_bridge is not None
    assert first_bridge.child_fd == -1
    assert first_bridge._parent_socket.fileno() == -1


@pytest.mark.parametrize(
    ("handoff_mode", "wire_mode"),
    [("bootstrap", "ceo_bootstrap"), ("wake", "ceo_wake")],
)
def test_primary_sdk_subprocess_serializes_task_kind_wire_mode(
    monkeypatch, tmp_path, handoff_mode, wire_mode
) -> None:
    from plugins.takyon import claude_sdk_runtime as runtime

    session_id = str(uuid.uuid4())

    class RecordingStdin(io.StringIO):
        def __init__(self) -> None:
            super().__init__()
            self.writes: list[str] = []

        def write(self, value: str) -> int:
            self.writes.append(value)
            return super().write(value)

    class FakeBridge:
        def __init__(self, child_fd) -> None:
            self.child_fd = child_fd

        def start(self):
            return self

        def close_child_in_parent(self) -> None:
            pass

        def close(self) -> None:
            pass

    class FakeProcess:
        def __init__(self) -> None:
            self.stdin = RecordingStdin()
            self.stdout = io.StringIO(
                json.dumps(
                    {
                        "ok": True,
                        "result": {
                            "session_id": session_id,
                            "operation": "turn",
                            "summary": "done",
                        },
                    }
                )
            )
            self.stderr = io.StringIO()
            self.returncode = 0

        def poll(self):
            return 0

        def wait(self, timeout=None):
            return 0

    process = FakeProcess()
    bridges = iter((FakeBridge(91), FakeBridge(92)))
    popen_kwargs = {}
    policy = runtime.SdkModeToolPolicy(
        mode=handoff_mode,
        allowed_skills=("safe-skill",),
        baseline_tools=("safe_tool",),
        allowed_tools=("safe_tool",),
        denied_capabilities=(),
        denied_tools=(),
        denied_write_paths=(),
        handoff_guidance="Use the scoped tool bridge.",
    )
    definitions = [{"name": "safe_tool", "inputSchema": {"type": "object"}}]

    monkeypatch.setattr(
        runtime,
        "_primary_sdk_install_paths",
        lambda: (
            tmp_path / "entrypoint.mjs",
            tmp_path / "plugin",
            tmp_path / "manifest.json",
        ),
    )
    monkeypatch.setattr(
        runtime, "sdk_tool_definitions", lambda **_kwargs: list(definitions)
    )

    def enforce_mode(**kwargs):
        assert kwargs["mode"] == handoff_mode
        return (list(definitions), policy)

    monkeypatch.setattr(runtime, "enforce_sdk_mode_tool_policy", enforce_mode)
    monkeypatch.setattr(
        runtime,
        "_build_skill_resource_reader",
        lambda **_kwargs: lambda _args: "",
    )
    monkeypatch.setattr(
        runtime,
        "build_primary_sdk_env",
        lambda **_kwargs: {
            "PATH": os.environ.get("PATH", ""),
            "ANTHROPIC_API_KEY": "scoped-capability",
            "CLAUDE_CONFIG_DIR": str(tmp_path / "config"),
        },
    )
    monkeypatch.setattr(
        runtime,
        "_primary_sdk_node_runtime",
        lambda **_kwargs: ("node", tmp_path / "sdk.mjs", tmp_path / "zod.mjs"),
    )
    monkeypatch.setattr(runtime, "ScopedToolBridge", lambda **_kwargs: next(bridges))

    def popen(*_args, **kwargs):
        popen_kwargs.update(kwargs)
        return process

    monkeypatch.setattr(runtime.subprocess, "Popen", popen)

    result = runtime.run_primary_sdk_subprocess(
        business="acme",
        operator_user_id="operator-1",
        system_prompt="system",
        user_prompt="turn",
        enabled_toolsets=["takyon"],
        workspace_root=tmp_path,
        session_id=session_id,
        resume_session=False,
        session_store=runtime.InMemorySessionStoreBackend(),
        mode=handoff_mode,
        epoch=f"{handoff_mode}:phase-1",
        max_turns=2,
        max_budget_usd=1,
    )

    request = json.loads(process.stdin.writes[0])
    assert request["mode"] == wire_mode
    assert request["epoch"] == f"{handoff_mode}:phase-1"
    assert popen_kwargs["pass_fds"] == (91, 92)
    assert popen_kwargs["env"][runtime.SDK_TOOL_BRIDGE_FD_ENV] == "91"
    assert popen_kwargs["env"][runtime.SDK_SESSION_BRIDGE_FD_ENV] == "92"
    assert result["summary"] == "done"


def test_keyboard_interrupt_terminates_sdk_process_before_bridge_cleanup(
    monkeypatch, tmp_path
) -> None:
    from plugins.takyon import claude_sdk_runtime as runtime

    events: list[str] = []

    class FakeBridge:
        def __init__(self, name, child_fd) -> None:
            self.name = name
            self.child_fd = child_fd

        def start(self):
            return self

        def close_child_in_parent(self) -> None:
            events.append(f"{self.name}_child_closed")

        def close(self) -> None:
            events.append(f"{self.name}_closed")

    class FakeProcess:
        def __init__(self) -> None:
            self.stdin = io.StringIO()
            self.stdout = io.StringIO()
            self.stderr = io.StringIO()
            self.returncode = None
            self.terminated = False

        def poll(self):
            return -15 if self.terminated else None

    process = FakeProcess()
    bridges = iter((FakeBridge("tool", 91), FakeBridge("session", 92)))
    policy = runtime.SdkModeToolPolicy(
        mode="interactive",
        allowed_skills=("safe-skill",),
        baseline_tools=("safe_tool",),
        allowed_tools=("safe_tool",),
        denied_capabilities=(),
        denied_tools=(),
        denied_write_paths=(),
        handoff_guidance="Use the scoped tool bridge.",
    )
    definitions = [{"name": "safe_tool", "inputSchema": {"type": "object"}}]

    monkeypatch.setattr(
        runtime,
        "_primary_sdk_install_paths",
        lambda: (tmp_path / "entrypoint.mjs", tmp_path / "plugin", tmp_path / "manifest.json"),
    )
    monkeypatch.setattr(runtime, "sdk_tool_definitions", lambda **_kwargs: list(definitions))
    monkeypatch.setattr(
        runtime,
        "enforce_sdk_mode_tool_policy",
        lambda **kwargs: (list(kwargs["tool_definitions"]), policy),
    )
    monkeypatch.setattr(runtime, "_build_skill_resource_reader", lambda **_kwargs: lambda _args: "")
    monkeypatch.setattr(
        runtime,
        "build_primary_sdk_env",
        lambda **_kwargs: {
            "PATH": os.environ.get("PATH", ""),
            "ANTHROPIC_API_KEY": "scoped-capability",
            "CLAUDE_CONFIG_DIR": str(tmp_path / "config"),
        },
    )
    monkeypatch.setattr(
        runtime,
        "_primary_sdk_node_runtime",
        lambda **_kwargs: ("node", tmp_path / "sdk.mjs", tmp_path / "zod.mjs"),
    )
    monkeypatch.setattr(runtime, "ScopedToolBridge", lambda **_kwargs: next(bridges))
    monkeypatch.setattr(runtime.subprocess, "Popen", lambda *_args, **_kwargs: process)

    def terminate(candidate, **_kwargs) -> None:
        assert candidate is process
        events.append("process_group_terminated")
        process.terminated = True

    monkeypatch.setattr(runtime, "_terminate_process_group", terminate)

    with pytest.raises(KeyboardInterrupt):
        runtime.run_primary_sdk_subprocess(
            business="acme",
            operator_user_id="operator-1",
            system_prompt="system",
            user_prompt="turn",
            enabled_toolsets=["takyon"],
            workspace_root=tmp_path,
            session_id=str(uuid.uuid4()),
            resume_session=False,
            session_store=runtime.InMemorySessionStoreBackend(),
            mode="interactive",
            epoch="interactive:test",
            max_turns=2,
            max_budget_usd=1,
            stop_probe=lambda *_args: (_ for _ in ()).throw(KeyboardInterrupt()),
        )

    assert events.index("process_group_terminated") < events.index("session_closed")
    assert events.index("process_group_terminated") < events.index("tool_closed")
    assert events.count("tool_child_closed") == 1
    assert events.count("session_child_closed") == 1
    assert events.count("tool_closed") == 1
    assert events.count("session_closed") == 1
    assert process.stdin.closed is True


def test_terminate_process_group_kills_pipe_holding_descendants_after_parent_exit(
    monkeypatch,
) -> None:
    from plugins.takyon import claude_sdk_runtime as runtime

    signals: list[int] = []

    class ExitedParent:
        pid = 4242

        @staticmethod
        def poll():
            return 0

    def killpg(pid: int, sig: int) -> None:
        assert pid == 4242
        signals.append(sig)

    monkeypatch.setattr(runtime.os, "killpg", killpg)
    monkeypatch.setattr(runtime.time, "sleep", lambda _seconds: None)

    runtime._terminate_process_group(ExitedParent(), grace_seconds=0.1)

    assert signals == [signal.SIGTERM, 0, signal.SIGKILL]
