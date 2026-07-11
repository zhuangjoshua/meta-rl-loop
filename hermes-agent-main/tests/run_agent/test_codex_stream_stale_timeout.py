import time
from types import SimpleNamespace

from agent.chat_completion_helpers import interruptible_api_call
from agent.codex_runtime import run_codex_stream


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


class _OneEventStream:
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def __iter__(self):
        return iter([SimpleNamespace(type="response.output_text.delta", delta="ready")])

    def get_final_response(self):
        return SimpleNamespace(output=[SimpleNamespace(type="message")])


class _CodexClient:
    responses = SimpleNamespace(stream=lambda **_kwargs: _OneEventStream())


class _CodexStreamAgent:
    _interrupt_requested = False

    def __init__(self):
        self.activities = []
        self.deltas = []

    def _touch_activity(self, description):
        self.activities.append(description)

    def _fire_stream_delta(self, delta):
        self.deltas.append(delta)

    def _fire_reasoning_delta(self, _delta):
        raise AssertionError("unexpected reasoning delta")


def test_real_codex_stream_activity_timestamp_path_is_executable():
    agent = _CodexStreamAgent()

    response = run_codex_stream(agent, {"model": "gpt-5"}, client=_CodexClient())

    assert response.output
    assert agent.deltas == ["ready"]
    assert agent.activities == ["receiving stream response"]
    assert isinstance(agent._codex_stream_last_event_ts, float)
