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
import {{ buildPrompt, progressEventFromSdkMessage as progress }} from {json.dumps(SCRIPT.as_uri())};
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
