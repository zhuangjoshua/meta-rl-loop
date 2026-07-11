import time

from agent.chat_completion_helpers import interruptible_api_call


class _ActiveCodexAgent:
    api_mode = "codex_responses"
    _interrupt_requested = False
    _codex_on_first_delta = None

    def __init__(self):
        self.closed = []
        self.activities = []

    def _compute_non_stream_stale_timeout(self, _messages):
        return 0.05

    def _create_request_openai_client(self, **_kwargs):
        return object()

    def _close_request_openai_client(self, client, *, reason):
        self.closed.append((client, reason))

    def _touch_activity(self, desc):
        self.activities.append(desc)

    def _run_codex_stream(self, _kwargs, *, client, on_first_delta=None):
        # Total wall time exceeds the 50ms stale threshold, but real stream events arrive well
        # inside it. The outer wrapper must not start an overlapping retry or abort this call.
        self._codex_stream_last_event_ts = time.time()
        for _ in range(8):
            time.sleep(0.04)
            self._codex_stream_last_event_ts = time.time()
            self._touch_activity("receiving stream response")
        return {"ok": True}


def test_active_codex_responses_stream_is_not_killed_by_wall_clock_stale_guard():
    agent = _ActiveCodexAgent()

    result = interruptible_api_call(
        agent, {"model": "gpt-5", "messages": [{"role": "user", "content": "go"}]}
    )

    assert result == {"ok": True}
    assert any(desc == "receiving stream response" for desc in agent.activities)
    assert not any("stale non-streaming call killed" in desc for desc in agent.activities)
    assert agent.closed and agent.closed[-1][1] == "request_complete"
