import json
from pathlib import Path
import shutil
import subprocess

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "takyon-claude-agent-task.mjs"


def _run_module(expression: str):
    node = shutil.which("node")
    if not node:
        pytest.skip("node is unavailable")
    source = f"""
import {{ apiRetryFailureFromSdkMessage as retryFailure, buildPrompt, createSiteImageMcpServer as createPreflightServer, progressEventFromSdkMessage as progress }} from {json.dumps(SCRIPT.as_uri())};
{expression}
"""
    proc = subprocess.run(
        [node, "--input-type=module", "--eval", source],
        text=True,
        capture_output=True,
        timeout=10,
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


def test_worker_prompt_requires_immediate_tool_execution_without_narrated_planning():
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

    assert "begin with targeted file inspection, then implement immediately" in prompt
    assert "Do not narrate design exploration" in prompt
    assert "Keep private reasoning private" in prompt


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


def test_taste_visual_preflight_is_single_use_even_when_first_render_fails():
    result = _run_module(
        """
const scalar = {
  regex() { return this; },
  max() { return this; },
  min() { return this; },
};
const z = { string: () => ({ ...scalar }), enum: () => ({ ...scalar }) };
const tool = (name, _description, _schema, handler) => ({ name, handler });
let renderCalls = 0;
const server = createPreflightServer({
  createSdkMcpServer: (config) => config,
  tool,
  z,
  bridgeDir: "/bridge",
  cwd: "/workspace",
  renderLandingPreflight: async () => {
    renderCalls += 1;
    throw new Error("Chromium missing");
  },
});
const renderTool = server.tools.find((item) => item.name === "business_render_landing_preflight");
const first = await renderTool.handler({});
const second = await renderTool.handler({});
console.log(JSON.stringify({ renderCalls, first, second }));
"""
    )

    assert result["renderCalls"] == 1
    assert result["first"]["isError"] is True
    assert result["first"]["content"][0]["text"] == "Chromium missing"
    assert result["second"]["isError"] is True
    assert result["second"]["content"][0]["text"] == (
        "Taste landing render preflight is single-use (cap: 1 call)"
    )
