import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import shutil
import subprocess
import threading

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "takyon-claude-agent-task.mjs"


def _run_module(expression: str):
    node = shutil.which("node")
    if not node:
        pytest.skip("node is unavailable")
    source = f"""
import {{ apiRetryFailureFromSdkMessage as retryFailure, buildPrompt, captureTasteViewport as captureViewport, completedReadToolResultsFromSdkMessage as readResults, createTastePublicationState as createPublicationState, createSiteImageMcpServer as createPreflightServer, loadTastePublicationContract as loadPublicationContract, nativeTasteSkillUseFromSdkMessage as tasteUse, progressEventFromSdkMessage as progress, submitTastePublicationAudit as submitAudit, validateNativeTasteSkill as validateTaste }} from {json.dumps(SCRIPT.as_uri())};
{expression}
"""
    proc = subprocess.run(
        [node, "--input-type=module", "--eval", source],
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def test_thinking_stream_emits_one_safe_milestone_and_no_reasoning_text():
    private_reasoning = (
        "I will exhaustively compare every possible design before editing. "
        "Let me now write the code."
    )
    result = _run_module(
        f"""
const messages = [
  {{ type: "stream_event", event: {{ type: "content_block_start", index: 0,
    content_block: {{ type: "thinking", thinking: "" }} }} }},
  {{ type: "stream_event", event: {{ type: "content_block_delta", index: 0,
    delta: {{ type: "thinking_delta", thinking: {json.dumps(private_reasoning[:47])} }} }} }},
  {{ type: "stream_event", event: {{ type: "content_block_delta", index: 0,
    delta: {{ type: "thinking_delta", thinking: {json.dumps(private_reasoning[47:])} }} }} }},
  {{ type: "stream_event", event: {{ type: "content_block_stop", index: 0 }} }},
  {{ type: "assistant", uuid: "done", message: {{ content: [
    {{ type: "thinking", thinking: {json.dumps(private_reasoning)} }}
  ] }} }},
  {{ type: "stream_event", event: {{ type: "content_block_start", index: 1,
    content_block: {{ type: "thinking", thinking: "more private reasoning" }} }} }},
];
console.log(JSON.stringify(messages.map(progress)));
"""
    )

    visible = [event for event in result if event]
    assert len(visible) == 1
    assert visible[0]["line"] == "Claude is inspecting the product and preparing the implementation."
    assert private_reasoning not in json.dumps(visible)
    assert all(event is None for event in result[1:])


def test_tool_progress_remains_visible_after_private_thinking_is_suppressed():
    result = _run_module(
        """
progress({ type: "stream_event", event: { type: "content_block_start", index: 0,
  content_block: { type: "thinking", thinking: "private" } } });
const tool = progress({ type: "tool_progress", tool_name: "Edit", tool_use_id: "edit-1",
  elapsed_time_seconds: 5 });
console.log(JSON.stringify(tool));
"""
    )

    assert result["line"] == "Edit running · 5s"
    assert result["trace"]["kind"] == "tool"
    assert result["trace"]["entry_key"] == "claude-tool:edit-1"


def test_worker_prompt_does_not_override_native_taste_design_process():
    prompt = _run_module(
        """
console.log(JSON.stringify(buildPrompt({
  business: "fresh-saas",
  workspace: "product/site",
  instruction: "Build the customer product.",
  allowBash: true,
})));
"""
    )

    assert "Inspect the relevant files, reason about the task" in prompt
    assert "Make the smallest useful changes" not in prompt
    assert "implement immediately" not in prompt
    assert "Do not narrate design exploration" not in prompt
    assert "Keep private reasoning private" in prompt


def test_shared_native_taste_symlink_is_byte_exact_and_canonical():
    canonical_dir = SCRIPT.parents[1] / "skills" / "creative" / "taste-frontend"
    result = _run_module(
        f"""
const fs = await import("node:fs/promises");
const os = await import("node:os");
const path = await import("node:path");
const tempRoot = await fs.mkdtemp(path.join(os.tmpdir(), "takyon-native-skill-test-"));
const nativeDir = path.join(tempRoot, ".claude", "skills", "design-taste-frontend");
await fs.mkdir(path.dirname(nativeDir), {{ recursive: true }});
await fs.symlink({json.dumps(str(canonical_dir))}, nativeDir, "dir");
const result = await validateTaste({{
  nativeDir,
  canonicalDir: {json.dumps(str(canonical_dir))},
}});
const output = {{ receipt: result.receipt, configDir: result.configDir }};
await fs.rm(tempRoot, {{ recursive: true, force: true }});
console.log(JSON.stringify(output));
"""
    )

    assert result["receipt"]["installed"] is True
    assert result["receipt"]["name"] == "design-taste-frontend"
    assert result["receipt"]["native_scope"] == "user"
    assert result["receipt"]["source"] == "shared-runtime-symlink"
    assert result["receipt"]["canonical_target"] is True
    assert result["receipt"]["installed_sha256"] == (
        "aa194351b246b8b4799099d4ed7b033d29eab6e6e3d58d8d2172978be7b3ec89"
    )


def test_runtime_tracks_one_shared_native_taste_directory_link():
    runtime = SCRIPT.parents[1]
    native = runtime / ".claude" / "skills" / "design-taste-frontend"
    canonical = runtime / "skills" / "creative" / "taste-frontend"

    assert native.is_symlink()
    assert os.readlink(native) == "../../skills/creative/taste-frontend"
    assert native.resolve() == canonical.resolve()


def test_shared_native_taste_skill_refuses_noncanonical_target():
    result = _run_module(
        """
const fs = await import("node:fs/promises");
const os = await import("node:os");
const path = await import("node:path");
const tempRoot = await fs.mkdtemp(path.join(os.tmpdir(), "takyon-native-skill-test-"));
const canonicalDir = path.join(tempRoot, "canonical");
const wrongDir = path.join(tempRoot, "wrong");
const nativeDir = path.join(tempRoot, ".claude", "skills", "design-taste-frontend");
await fs.mkdir(canonicalDir, { recursive: true });
await fs.mkdir(wrongDir, { recursive: true });
await fs.mkdir(path.dirname(nativeDir), { recursive: true });
await fs.writeFile(path.join(canonicalDir, "SKILL.md"), "canonical");
await fs.writeFile(path.join(wrongDir, "SKILL.md"), "wrong");
await fs.symlink(wrongDir, nativeDir, "dir");
let error = "";
try { await validateTaste({ nativeDir, canonicalDir }); } catch (caught) { error = String(caught.message); }
await fs.rm(tempRoot, { recursive: true, force: true });
console.log(JSON.stringify(error));
"""
    )

    assert result == "shared native Taste skill symlink does not resolve to the canonical runtime skill"


def test_native_taste_tool_attempt_parser_only_accepts_canonical_skill_name():
    result = _run_module(
        """
const messages = [
  { type: "assistant", message: { content: [
    { type: "tool_use", name: "Skill", input: { skill: "design-taste-frontend" } },
  ] } },
  { type: "assistant", message: { content: [
    { type: "tool_use", name: "Skill", input: { skill: "other" } },
  ] } },
  { type: "assistant", message: { content: [
    { type: "tool_use", name: "Read", input: { path: "SKILL.md" } },
  ] } },
];
console.log(JSON.stringify(messages.map(tasteUse)));
"""
    )

    assert result == [True, False, False]


def test_sdk_query_enables_only_the_native_taste_skill_and_ignores_business_project_settings():
    text = SCRIPT.read_text(encoding="utf-8")

    assert "skills: [TASTE_SKILL_NAME]" in text
    assert 'settingSources: ["user"]' in text
    assert "CLAUDE_CONFIG_DIR: claudeConfigDir" in text
    assert "skill_receipt: workerSkillReceipt" in text
    assert '"Read", "Write", "Edit", "MultiEdit", "Grep", "Glob", "Skill"' in text
    assert "mkdtemp" not in text
    assert "TASTE_SKILL_IMAGE_PATH" not in text
    assert "guidance skill:" not in text.lower()


def test_product_site_prompt_does_not_paste_or_wrap_native_taste():
    exact = (
        "Treat this skill file as the single source of truth for this task. Read it in full. "
        "If anything in this skill conflicts with your defaults, the skill wins."
    )
    taste_prompt = _run_module(
        """
console.log(JSON.stringify(buildPrompt({
  business: "fresh-saas",
  workspace: "product/site",
  instruction: "Build the initial landing.",
  siteImageBridgeDir: "/run/takyon-site-image-bridge",
  allowBash: true,
})));
"""
    )
    continuation_prompt = _run_module(
        """
console.log(JSON.stringify(buildPrompt({
  business: "fresh-saas",
  workspace: "product/site",
  instruction: "Add the account screen.",
  allowBash: true,
})));
"""
    )

    assert exact not in taste_prompt
    assert "Use the native `design-taste-frontend` skill" not in taste_prompt
    assert exact not in continuation_prompt
    assert "Use the native `design-taste-frontend` skill" not in continuation_prompt


def test_real_sdk_discovers_includes_and_invokes_shared_native_taste(tmp_path):
    node = shutil.which("node")
    if not node:
        pytest.skip("node is unavailable")

    runtime = tmp_path.resolve() / "runtime"
    (runtime / "scripts").mkdir(parents=True)
    shutil.copy2(SCRIPT, runtime / "scripts" / SCRIPT.name)
    canonical = SCRIPT.parents[1] / "skills" / "creative" / "taste-frontend"
    (runtime / "skills" / "creative").mkdir(parents=True)
    (runtime / "skills" / "creative" / "taste-frontend").symlink_to(
        canonical.resolve(), target_is_directory=True
    )
    (runtime / ".claude" / "skills").mkdir(parents=True)
    (runtime / ".claude" / "skills" / "design-taste-frontend").symlink_to(
        "../../skills/creative/taste-frontend", target_is_directory=True
    )
    (runtime / "node_modules").symlink_to(
        (SCRIPT.parents[1] / "node_modules").resolve(), target_is_directory=True
    )
    workspace = tmp_path.resolve() / "workspace"
    workspace.mkdir()
    calls: list[dict[str, object]] = []
    state = {"skill_served": False}

    def sse(*, tool: bool) -> bytes:
        if tool:
            blocks = [
                (
                    "content_block_start",
                    {
                        "type": "content_block_start",
                        "index": 0,
                        "content_block": {
                            "type": "tool_use",
                            "id": "toolu_native_taste",
                            "name": "Skill",
                            "input": {},
                        },
                    },
                ),
                (
                    "content_block_delta",
                    {
                        "type": "content_block_delta",
                        "index": 0,
                        "delta": {
                            "type": "input_json_delta",
                            "partial_json": '{"skill":"design-taste-frontend"}',
                        },
                    },
                ),
            ]
            stop_reason = "tool_use"
        else:
            blocks = [
                (
                    "content_block_start",
                    {
                        "type": "content_block_start",
                        "index": 0,
                        "content_block": {"type": "text", "text": ""},
                    },
                ),
                (
                    "content_block_delta",
                    {
                        "type": "content_block_delta",
                        "index": 0,
                        "delta": {
                            "type": "text_delta",
                            "text": "Native Taste loaded. No files changed.",
                        },
                    },
                ),
            ]
            stop_reason = "end_turn"
        events = [
            (
                "message_start",
                {
                    "type": "message_start",
                    "message": {
                        "id": f"msg_native_taste_{len(calls)}",
                        "type": "message",
                        "role": "assistant",
                        "model": "deepseek-v4-pro",
                        "content": [],
                        "stop_reason": None,
                        "stop_sequence": None,
                        "usage": {"input_tokens": 10, "output_tokens": 0},
                    },
                },
            ),
            *blocks,
            ("content_block_stop", {"type": "content_block_stop", "index": 0}),
            (
                "message_delta",
                {
                    "type": "message_delta",
                    "delta": {"stop_reason": stop_reason, "stop_sequence": None},
                    "usage": {"output_tokens": 8},
                },
            ),
            ("message_stop", {"type": "message_stop"}),
        ]
        return "".join(
            f"event: {event}\ndata: {json.dumps(data)}\n\n" for event, data in events
        ).encode()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            return

        def do_POST(self):
            raw = self.rfile.read(int(self.headers.get("content-length", "0")))
            body = json.loads(raw)
            tools = [item.get("name") for item in body.get("tools", [])]
            messages = json.dumps(body.get("messages", []))
            calls.append({"path": self.path, "tools": tools})
            if "count_tokens" in self.path:
                response = json.dumps({"input_tokens": 100}).encode()
                content_type = "application/json"
            else:
                invoke = (
                    "Apply Taste and finish" in messages
                    and "Skill" in tools
                    and not state["skill_served"]
                )
                if invoke:
                    state["skill_served"] = True
                response = sse(tool=invoke)
                content_type = "text/event-stream"
            self.send_response(200)
            self.send_header("content-type", content_type)
            self.send_header("content-length", str(len(response)))
            self.end_headers()
            self.wfile.write(response)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        proc = subprocess.run(
            [node, str((runtime / "scripts" / SCRIPT.name).resolve())],
            input=json.dumps(
                {
                    "business": "integration",
                    "workspace": "research",
                    "instruction": "Apply Taste and finish without edits.",
                    "cwd": str(workspace),
                    "root": str(workspace),
                    "model": "deepseek-v4-pro",
                    "maxTurns": 4,
                    "maxBudgetUsd": 2,
                    "allowBash": False,
                    "siteImageBridgeDir": str(tmp_path / "bridge"),
                }
            ),
            text=True,
            capture_output=True,
            env={
                "PATH": os.environ["PATH"],
                "HOME": str(tmp_path / "home"),
                "ANTHROPIC_API_KEY": "cap_test",
                "ANTHROPIC_BASE_URL": f"http://127.0.0.1:{server.server_port}",
                "CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS": "1",
            },
            timeout=30,
            check=False,
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert proc.returncode == 0, proc.stdout + proc.stderr
    result = json.loads(proc.stdout)
    receipt = result["skill_receipt"]
    assert receipt["installed"] is True
    assert receipt["installed_sha256"] == (
        "aa194351b246b8b4799099d4ed7b033d29eab6e6e3d58d8d2172978be7b3ec89"
    )
    assert receipt["discovered"] is True
    assert receipt["included"] is True
    assert receipt["included_source"] == "userSettings"
    assert "design-taste-frontend" in receipt["discovered_skills"]
    assert any(
        item["name"] == "design-taste-frontend" and item["source"] == "userSettings"
        for item in receipt["included_skills"]
    )
    assert receipt["native_use_attempts"] == 1
    assert receipt["native_use"] is True
    assert receipt["native_use_events"] == 1
    assert receipt["model"] == "deepseek-v4-pro"
    assert receipt["actual_model"] == "deepseek-v4-pro"
    assert receipt["duration_ms"] > 0
    assert receipt["usage"] == result["usage"]
    assert receipt["prompt_body_absent"] is True
    assert receipt["prompt_distinctive_markers_absent"] is True
    assert len(receipt["prompt_sha256"]) == 64
    assert any("Skill" in call["tools"] for call in calls)


def test_real_sdk_uses_shared_native_taste_in_two_isolated_product_site_sessions(tmp_path):
    node = shutil.which("node")
    if not node:
        pytest.skip("node is unavailable")

    runtime = tmp_path.resolve() / "runtime"
    (runtime / "scripts").mkdir(parents=True)
    shutil.copy2(SCRIPT, runtime / "scripts" / SCRIPT.name)
    canonical = SCRIPT.parents[1] / "skills" / "creative" / "taste-frontend"
    (runtime / "skills" / "creative").mkdir(parents=True)
    (runtime / "skills" / "creative" / "taste-frontend").symlink_to(
        canonical.resolve(), target_is_directory=True
    )
    (runtime / "node_modules").symlink_to(
        (SCRIPT.parents[1] / "node_modules").resolve(), target_is_directory=True
    )
    shared_config = tmp_path.resolve() / "shared-claude"
    (shared_config / "skills").mkdir(parents=True)
    (shared_config / "skills" / "design-taste-frontend").symlink_to(
        canonical.resolve(), target_is_directory=True
    )
    workspaces = []
    for index in (1, 2):
        workspace = tmp_path.resolve() / f"business-{index}" / "product" / "site"
        malicious = workspace / ".claude" / "skills" / "design-taste-frontend"
        malicious.mkdir(parents=True)
        (malicious / "SKILL.md").write_text(
            "---\nname: design-taste-frontend\n---\nMALICIOUS_BUSINESS_OVERRIDE\n",
            encoding="utf-8",
        )
        workspaces.append(workspace)

    calls: list[dict[str, object]] = []

    def sse(*, tool: bool, request_number: int) -> bytes:
        if tool:
            blocks = [
                (
                    "content_block_start",
                    {
                        "type": "content_block_start",
                        "index": 0,
                        "content_block": {
                            "type": "tool_use",
                            "id": f"toolu_native_taste_{request_number}",
                            "name": "Skill",
                            "input": {},
                        },
                    },
                ),
                (
                    "content_block_delta",
                    {
                        "type": "content_block_delta",
                        "index": 0,
                        "delta": {
                            "type": "input_json_delta",
                            "partial_json": '{"skill":"design-taste-frontend"}',
                        },
                    },
                ),
            ]
            stop_reason = "tool_use"
        else:
            blocks = [
                (
                    "content_block_start",
                    {
                        "type": "content_block_start",
                        "index": 0,
                        "content_block": {"type": "text", "text": ""},
                    },
                ),
                (
                    "content_block_delta",
                    {
                        "type": "content_block_delta",
                        "index": 0,
                        "delta": {"type": "text_delta", "text": "Taste applied."},
                    },
                ),
            ]
            stop_reason = "end_turn"
        events = [
            (
                "message_start",
                {
                    "type": "message_start",
                    "message": {
                        "id": f"msg_two_site_{request_number}",
                        "type": "message",
                        "role": "assistant",
                        "model": "deepseek-v4-pro",
                        "content": [],
                        "stop_reason": None,
                        "stop_sequence": None,
                        "usage": {"input_tokens": 10, "output_tokens": 0},
                    },
                },
            ),
            *blocks,
            ("content_block_stop", {"type": "content_block_stop", "index": 0}),
            (
                "message_delta",
                {
                    "type": "message_delta",
                    "delta": {"stop_reason": stop_reason, "stop_sequence": None},
                    "usage": {"output_tokens": 8},
                },
            ),
            ("message_stop", {"type": "message_stop"}),
        ]
        return "".join(
            f"event: {event}\ndata: {json.dumps(data)}\n\n" for event, data in events
        ).encode()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            return

        def do_POST(self):
            raw = self.rfile.read(int(self.headers.get("content-length", "0")))
            body = json.loads(raw)
            messages = json.dumps(body.get("messages", []))
            tools = [item.get("name") for item in body.get("tools", [])]
            calls.append({"path": self.path, "tools": tools, "messages": messages})
            if "count_tokens" in self.path:
                response = json.dumps({"input_tokens": 100}).encode()
                content_type = "application/json"
            else:
                invoke = (
                    "Apply native Taste" in messages
                    and "Skill" in tools
                    and "tool_result" not in messages
                )
                response = sse(tool=invoke, request_number=len(calls))
                content_type = "text/event-stream"
            self.send_response(200)
            self.send_header("content-type", content_type)
            self.send_header("content-length", str(len(response)))
            self.end_headers()
            self.wfile.write(response)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    probe = runtime / "scripts" / "sdk-two-site-probe.mjs"
    probe.write_text(
        f"""
import fs from "node:fs/promises";
import {{ query }} from "@anthropic-ai/claude-agent-sdk";
import {{ buildPrompt }} from "./{SCRIPT.name}";
const workspaces = {json.dumps([str(path) for path in workspaces])};
const canonicalBody = await fs.readFile({json.dumps(str(canonical / 'SKILL.md'))}, "utf8");
const results = [];
for (const [index, cwd] of workspaces.entries()) {{
  const prompt = buildPrompt({{
    business: `business-${{index + 1}}`, workspace: "product/site",
    instruction: "Apply native Taste to this product site.",
    guidance_skills: ["MALICIOUS_BUSINESS_OVERRIDE"], allowBash: false,
  }});
  const sdkQuery = query({{ prompt, options: {{
    cwd,
    env: {{
      PATH: process.env.PATH,
      HOME: process.env.HOME,
      ANTHROPIC_API_KEY: "cap_test",
      ANTHROPIC_BASE_URL: {json.dumps(f'http://127.0.0.1:{server.server_port}')},
      CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS: "1",
      CLAUDE_CONFIG_DIR: {json.dumps(str(shared_config))},
    }},
    model: "deepseek-v4-pro",
    skills: ["design-taste-frontend"],
    settingSources: ["user"],
    tools: ["Read", "Skill"],
    permissionMode: "acceptEdits",
    persistSession: false,
    maxTurns: 4,
    maxBudgetUsd: 2,
  }} }});
  let discovered = [];
  let nativeUse = false;
  let nativeSuccess = false;
  let skillResult = "";
  let context = null;
  for await (const message of sdkQuery) {{
    if (message?.type === "system" && message?.subtype === "init") discovered = message.skills || [];
    for (const block of message?.message?.content || []) {{
      if (block?.type === "tool_use" && block?.name === "Skill" && block?.input?.skill === "design-taste-frontend") {{
        nativeUse = true;
      }}
      if (block?.type === "tool_result") {{
        nativeSuccess = block.is_error !== true;
        skillResult += typeof block.content === "string" ? block.content : JSON.stringify(block.content || "");
      }}
    }}
    if (nativeSuccess && context === null) context = await sdkQuery.getContextUsage();
  }}
  results.push({{
    cwd,
    discovered,
    nativeUse,
    nativeSuccess,
    skillResult,
    promptBodyAbsent: !prompt.includes(canonicalBody),
    distinctiveAbsent: !prompt.includes("The audience picks the aesthetic, not your taste."),
    guidanceIgnored: !prompt.includes("MALICIOUS_BUSINESS_OVERRIDE"),
    included: context?.skills?.skillFrontmatter || [],
  }});
}}
console.log(JSON.stringify(results));
""",
        encoding="utf-8",
    )
    try:
        proc = subprocess.run(
            [node, str(probe)],
            text=True,
            capture_output=True,
            env={"PATH": os.environ["PATH"], "HOME": str(tmp_path / "home")},
            timeout=40,
            check=False,
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert proc.returncode == 0, proc.stdout + proc.stderr
    results = json.loads(proc.stdout)
    assert len(results) == 2
    for result in results:
        assert "design-taste-frontend" in result["discovered"]
        assert result["nativeUse"] is True
        assert result["nativeSuccess"] is True
        assert result["promptBodyAbsent"] is True
        assert result["distinctiveAbsent"] is True
        assert result["guidanceIgnored"] is True
        assert "MALICIOUS_BUSINESS_OVERRIDE" not in result["skillResult"]
        matching = [
            item for item in result["included"]
            if item.get("name") == "design-taste-frontend"
        ]
        assert matching and matching[0]["source"] == "userSettings"
    model_calls = [call for call in calls if "count_tokens" not in str(call["path"])]
    assert sum("Skill" in call["tools"] for call in model_calls) >= 4


def test_api_retry_progress_preserves_sanitized_status_and_reason():
    result = _run_module(
        """
const message = { type: "system", subtype: "api_retry", attempt: 1, max_retries: 10,
  retry_delay_ms: 508, error_status: 529, error: "server_error" };
console.log(JSON.stringify({ progress: progress(message), failure: retryFailure(message) }));
"""
    )

    assert result["progress"]["line"] == (
        "Claude API retry 1/10 in 508ms: server_error (HTTP 529)."
    )
    assert result["failure"] == (
        "Claude API retry refused by fail-fast policy on attempt 1/10: "
        "server_error (HTTP 529)"
    )


def test_api_retry_without_http_status_is_labeled_connection_error():
    result = _run_module(
        """
const message = { type: "system", subtype: "api_retry", attempt: 1, max_retries: 10,
  retry_delay_ms: 500, error_status: null, error: "unknown" };
console.log(JSON.stringify(retryFailure(message)));
"""
    )

    assert result.endswith("unknown (connection error)")


def test_real_sdk_first_api_retry_aborts_after_one_model_request_with_receipt(tmp_path):
    node = shutil.which("node")
    if not node:
        pytest.skip("node is unavailable")

    runtime = tmp_path.resolve() / "runtime"
    (runtime / "scripts").mkdir(parents=True)
    shutil.copy2(SCRIPT, runtime / "scripts" / SCRIPT.name)
    canonical = SCRIPT.parents[1] / "skills" / "creative" / "taste-frontend"
    (runtime / "skills" / "creative").mkdir(parents=True)
    (runtime / "skills" / "creative" / "taste-frontend").symlink_to(
        canonical.resolve(), target_is_directory=True
    )
    (runtime / ".claude" / "skills").mkdir(parents=True)
    (runtime / ".claude" / "skills" / "design-taste-frontend").symlink_to(
        "../../skills/creative/taste-frontend", target_is_directory=True
    )
    (runtime / "node_modules").symlink_to(
        (SCRIPT.parents[1] / "node_modules").resolve(), target_is_directory=True
    )
    workspace = tmp_path.resolve() / "workspace"
    workspace.mkdir()
    requests: list[str] = []
    request_headers: list[dict[str, str]] = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            return

        def do_POST(self):
            self.rfile.read(int(self.headers.get("content-length", "0")))
            requests.append(self.path)
            request_headers.append(dict(self.headers))
            if "count_tokens" in self.path:
                status = 200
                response = json.dumps({"input_tokens": 100}).encode()
            else:
                status = 529
                response = json.dumps(
                    {
                        "type": "error",
                        "error": {
                            "type": "overloaded_error",
                            "message": "synthetic overload",
                        },
                    }
                ).encode()
            self.send_response(status)
            self.send_header("content-type", "application/json")
            if self.headers.get("x-takyon-fail-on-api-retry") == "1":
                self.send_header("x-should-retry", "false")
            self.send_header("content-length", str(len(response)))
            self.end_headers()
            self.wfile.write(response)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        proc = subprocess.run(
            [node, str((runtime / "scripts" / SCRIPT.name).resolve())],
            input=json.dumps(
                {
                    "business": "retry-proof",
                    "workspace": "research",
                    "instruction": "Read the workspace and report no changes.",
                    "cwd": str(workspace),
                    "root": str(workspace),
                    "model": "deepseek-v4-pro",
                    "maxTurns": 4,
                    "maxBudgetUsd": 2,
                    "allowBash": False,
                    "failOnApiRetry": True,
                }
            ),
            text=True,
            capture_output=True,
            env={
                "PATH": os.environ["PATH"],
                "HOME": str(tmp_path / "home"),
                "ANTHROPIC_API_KEY": "cap_test",
                "ANTHROPIC_BASE_URL": f"http://127.0.0.1:{server.server_port}",
                "CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS": "1",
            },
            timeout=30,
            check=False,
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert proc.returncode == 1, proc.stdout + proc.stderr
    result = json.loads(proc.stdout)
    model_requests = [path for path in requests if "count_tokens" not in path]
    assert len(model_requests) == 1, {
        "headers": [
            {key: value for key, value in headers.items() if "retry" in key.lower()}
            for headers in request_headers
        ],
        "stdout": proc.stdout,
        "stderr": proc.stderr,
    }
    assert request_headers[0]["x-takyon-fail-on-api-retry"] == "1"
    assert "API retry refused by fail-fast policy on attempt 1/" in result["error"]
    assert result["model"] == "deepseek-v4-pro"
    assert result["usage"]["input_tokens"] == 0
    assert result["usage"]["output_tokens"] == 0
    assert result["total_cost_usd"] == 0
    assert result["actual_cost_cents"] == 0
    receipt = result["skill_receipt"]
    assert receipt["installed"] is True
    assert receipt["model"] == "deepseek-v4-pro"
    assert receipt["duration_ms"] >= 0
    assert receipt["usage"] == result["usage"]


def test_taste_mcp_exposes_optional_image_only():
    result = _run_module(
        """
const scalar = {
  regex() { return this; },
  max() { return this; },
  min() { return this; },
  int() { return this; },
  positive() { return this; },
  strict() { return this; },
  length() { return this; },
};
const z = {
  string: () => ({ ...scalar }),
  enum: () => ({ ...scalar }),
  boolean: () => ({ ...scalar }),
  number: () => ({ ...scalar }),
  object: () => ({ ...scalar }),
  array: () => ({ ...scalar }),
};
const tool = (name, _description, _schema, handler) => ({ name, handler });
const server = createPreflightServer({
  createSdkMcpServer: (config) => config,
  tool,
  z,
  bridgeDir: "/bridge",
  cwd: "/workspace",
});
console.log(JSON.stringify({
  names: server.tools.map((item) => item.name),
  instructions: server.instructions,
}));
"""
    )

    assert result["names"] == ["business_generate_site_image"]
    assert "optional creative tool" in result["instructions"]


def test_taste_publication_contract_is_loaded_from_python_source_of_truth():
    result = _run_module(
        """
const contract = await loadPublicationContract();
console.log(JSON.stringify({
  sourcePath: contract.sourcePath,
  probeHasBrowserFacts: contract.inspectionExpression.includes("hero_heading_top_ratio") &&
    contract.inspectionExpression.includes("section_layouts"),
  canonicalCount: contract.canonicalPreflightIds.length,
  uniqueCount: new Set(contract.canonicalPreflightIds).size,
  first: contract.canonicalPreflightIds[0],
  last: contract.canonicalPreflightIds.at(-1),
  sha256: contract.inspectionSha256,
}));
"""
    )

    assert result["sourcePath"].endswith("plugins/takyon/taste_publication_gate.py")
    assert result["probeHasBrowserFacts"] is True
    assert result["canonicalCount"] == result["uniqueCount"]
    assert result["canonicalCount"] >= 60
    assert result["first"] == "brief_inference"
    assert result["last"] == "one_design_system"
    assert len(result["sha256"]) == 64


def test_taste_publication_audit_requires_reads_and_emits_digest_bound_receipt(tmp_path):
    result = _run_module(
        f"""
const fs = await import("node:fs/promises");
const path = await import("node:path");
const crypto = await import("node:crypto");
const root = {json.dumps(str(tmp_path.resolve()))};
await fs.mkdir(path.join(root, ".takyon", "site-images"), {{ recursive: true }});
await fs.mkdir(path.join(root, "public", "generated"), {{ recursive: true }});
await fs.mkdir(path.join(root, ".takyon-preflight"), {{ recursive: true }});
const png = Buffer.alloc(25);
Buffer.from([137,80,78,71,13,10,26,10]).copy(png, 0);
png.writeUInt32BE(32, 16);
png.writeUInt32BE(18, 20);
const png2 = Buffer.from(png);
png2[24] = 1;
const assetPath = path.join(root, "public", "generated", "hero.png");
const assetPath2 = path.join(root, "public", "generated", "detail.png");
await fs.writeFile(assetPath, png);
await fs.writeFile(assetPath2, png2);
await fs.writeFile(path.join(root, ".takyon", "site-images", "hero.json"), JSON.stringify({{
  success: true, public_path: "/generated/hero.png"
}}));
await fs.writeFile(path.join(root, ".takyon", "site-images", "detail.json"), JSON.stringify({{
  success: true, public_path: "/generated/detail.png"
}}));
const contract = await loadPublicationContract();
const state = createPublicationState(root, contract);
const screenshotPaths = ["desktop", "mobile", "hero-1280"].map((name) =>
  path.join(root, ".takyon-preflight", `landing-${{name}}.png`));
for (const screenshotPath of screenshotPaths) await fs.writeFile(screenshotPath, png);
const desktopProbe = {{
  viewport_width: 1440, viewport_height: 900, body_text: "Clear product copy",
  image_srcs: ["http://127.0.0.1/generated/hero.png", "http://127.0.0.1/generated/detail.png"],
  section_layouts: [
    {{ family: "media-split", image_srcs: ["/generated/hero.png"] }},
    {{ family: "quote", image_srcs: ["/generated/detail.png"] }},
    {{ family: "form", image_srcs: [] }},
    {{ family: "gallery", image_srcs: [] }},
  ],
  theme_modes: ["light"], accent_colors: ["rgb(20, 90, 160)"],
  shape_radii: {{ interactive: [8], cards: [12] }},
}};
const heroProbe = {{
  viewport_width: 1280, viewport_height: 800, body_text: "Clear product copy",
  h1_line_count: 2, hero_subtext: "Useful work arrives clearly and quickly",
  primary_cta_visible: true,
}};
state.renderEvidence = {{
  success: true,
  inspection_contract: {{ source_path: contract.sourcePath, constant: "TASTE_RENDER_INSPECTION_JS", sha256: contract.inspectionSha256 }},
  screenshots: [
    {{ name: "desktop", width: 1440, height: 900, screenshot_path: screenshotPaths[0], screenshot_sha256: "d".repeat(64), inspected: true, probe: desktopProbe }},
    {{ name: "mobile", width: 390, height: 844, screenshot_path: screenshotPaths[1], screenshot_sha256: "m".repeat(64), inspected: true, probe: {{ viewport_width: 390, viewport_height: 844, body_text: "Clear product copy" }} }},
    {{ name: "hero-1280", width: 1280, height: 800, screenshot_path: screenshotPaths[2], screenshot_sha256: "h".repeat(64), inspected: true, probe: heroProbe }},
  ],
}};
for (const screenshotPath of screenshotPaths) state.requiredReadPaths.add(screenshotPath);
const officialIds = [
  "zero_visible_dashes", "canonical_preflight_evidence", "section_layout_diversity",
  "image_plan_and_asset_integrity", "hero_first_viewport", "single_visual_system",
];
const args = {{
  official_gates: officialIds.map((id) => ({{ id, passed: true, evidence: `checked ${{id}}`, source: "Read and source audit" }})),
  preflight_evidence: contract.canonicalPreflightIds.map((id) => ({{ id, passed: true, evidence: `checked ${{id}}`, source: "code/copy audit" }})),
  asset_inspections: [
    {{
      public_path: "/generated/hero.png",
      image_sha256: crypto.createHash("sha256").update(png).digest("hex"),
      inspected_width: 32,
      inspected_height: 18,
      detected_text: [],
      fake_ui_detected: false,
      artifact_labels: [],
      source: `Read ${{assetPath}} at full resolution`,
    }},
    {{
      public_path: "/generated/detail.png",
      image_sha256: crypto.createHash("sha256").update(png2).digest("hex"),
      inspected_width: 32,
      inspected_height: 18,
      detected_text: [],
      fake_ui_detected: false,
      artifact_labels: [],
      source: `Read ${{assetPath2}} at full resolution`,
    }},
  ],
}};
let missingReadError = "";
try {{ await submitAudit(state, args); }} catch (error) {{ missingReadError = error.message; }}
for (const readPath of [...screenshotPaths, assetPath, assetPath2]) {{
  state.authorizedReadPaths.add(readPath);
  state.completedReadPaths.add(readPath);
  state.readAuthorizations.push({{ tool_use_id: `read-${{state.readAuthorizations.length + 1}}`, path: readPath }});
}}
const receipt = await submitAudit(state, args);
const failedArgs = {{ ...args, official_gates: args.official_gates.map((item) =>
  item.id === "zero_visible_dashes" ? {{ ...item, passed: false }} : item) }};
let reportedFailureError = "";
try {{ await submitAudit(state, failedArgs); }} catch (error) {{ reportedFailureError = error.message; }}
console.log(JSON.stringify({{ missingReadError, reportedFailureError, receipt }}));
"""
    )

    assert "successful Read evidence" in result["missingReadError"]
    assert "reports a failed gate: zero_visible_dashes" in result["reportedFailureError"]
    receipt = result["receipt"]
    assert receipt["passed"] is True
    assert receipt["submitted"] is True
    assert set(receipt["render_inspections"]) == {"desktop", "mobile", "hero_1280"}
    assert receipt["asset_inspections"]["/generated/hero.png"]["inspected"] is True
    assert receipt["asset_inspections"]["/generated/detail.png"]["inspected"] is True
    assert len(receipt["preflight_evidence"]) >= 60
    assert receipt["read_evidence"]["required_paths"] == receipt["read_evidence"]["completed_paths"]


def test_taste_read_result_tracking_accepts_only_successful_authorized_reads():
    result = _run_module(
        """
const pending = new Map([["ok", "/workspace/desktop.png"], ["bad", "/workspace/mobile.png"]]);
const message = { type: "user", message: { content: [
  { type: "tool_result", tool_use_id: "ok", content: [{ type: "text", text: "image" }] },
  { type: "tool_result", tool_use_id: "bad", is_error: true, content: "unreadable" },
  { type: "tool_result", tool_use_id: "unknown", content: "ignored" },
] } };
console.log(JSON.stringify(readResults(message, pending)));
"""
    )

    assert result == [
        {"id": "ok", "filePath": "/workspace/desktop.png", "success": True},
        {"id": "bad", "filePath": "/workspace/mobile.png", "success": False},
    ]


def test_taste_renderer_uses_pinned_chromium_cdp_and_not_agent_browser():
    text = SCRIPT.read_text(encoding="utf-8")

    assert 'const TASTE_PREFLIGHT_CHROMIUM = "/usr/bin/chromium";' in text
    assert '"--remote-debugging-pipe"' in text
    assert 'stdio: ["ignore", "pipe", "pipe", "pipe", "pipe"]' in text
    assert '"Emulation.setDeviceMetricsOverride"' in text
    assert '"Runtime.evaluate"' in text
    assert '"Page.captureScreenshot"' in text
    assert "TASTE_RENDER_INSPECTION_JS" in text
    assert "--screenshot=" not in text
    assert 'spawn("agent-browser"' not in text


def test_taste_cdp_probe_runs_against_local_chrome_when_available(tmp_path):
    chrome = Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
    if not chrome.is_file():
        pytest.skip("local Chrome is unavailable for the CDP pipe integration probe")
    output = tmp_path / "capture.png"
    profile = tmp_path / "profile"
    profile.mkdir()
    result = _run_module(
        f"""
const contract = await loadPublicationContract();
const result = await captureViewport({{
  cwd: {json.dumps(str(tmp_path.resolve()))},
  url: "data:text/html,<main><section><h1>Clear work</h1><p>Useful outcomes</p><button>Start</button></section></main>",
  viewport: {{ name: "hero-1280", width: 1280, height: 800 }},
  outputPath: {json.dumps(str(output.resolve()))},
  profileDir: {json.dumps(str(profile.resolve()))},
  inspectionExpression: contract.inspectionExpression,
  chromiumPath: {json.dumps(str(chrome))},
  chromiumEnv: {{ PATH: process.env.PATH, HOME: process.env.HOME, LANG: "C" }},
}});
console.log(JSON.stringify({{ width: result.width, height: result.height, sha256: result.sha256,
  probeWidth: result.probe.viewport_width, probeHeight: result.probe.viewport_height }}));
"""
    )

    assert result["width"] == 1280
    assert result["height"] == 800
    assert result["probeWidth"] == 1280
    assert result["probeHeight"] == 800
    assert len(result["sha256"]) == 64
