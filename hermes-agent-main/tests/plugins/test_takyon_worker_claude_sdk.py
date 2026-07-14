from __future__ import annotations

import uuid
from pathlib import Path

import pytest

from plugins.takyon import claude_sdk_runtime, claude_sdk_sessions, core, cost_events, worker
from toolsets import resolve_toolset


def test_worker_runtime_has_no_rollout_selector(monkeypatch) -> None:
    monkeypatch.setenv("TAKYON_WORKER_AGENT_RUNTIME", "retired-value")
    assert worker._selected_worker_agent_runtime() == "claude-agent-sdk"


def test_final_cutover_has_one_agent_loop_and_no_nested_model_worker() -> None:
    project = Path(__file__).resolve().parents[2]
    assert "business_claude_agent_task" not in {
        item["name"] for item in core.TAKYON_TOOL_DEFINITIONS
    }
    assert "claude.agent_task" not in worker.HANDLERS
    assert not hasattr(worker, "_run_hermes_ceo_turn")
    assert not hasattr(core, "handle_business_claude_agent_task")
    assert not (project / "scripts" / "takyon-claude-agent-task.mjs").exists()
    assert not (project / "tools" / "delegate_tool.py").exists()
    assert "delegate_task" not in resolve_toolset("takyon-cli")
    assert "delegate_task" not in resolve_toolset("takyon-api-server")
    for relative in (
        "plugins/takyon/cli.py",
        "plugins/takyon/worker.py",
        "plugins/takyon/operator_gateway.py",
        "plugins/takyon/turn_runtime.py",
    ):
        source = (project / relative).read_text(encoding="utf-8")
        assert "from run_agent import AIAgent" not in source
        assert "import run_agent" not in source


def test_run_ceo_turn_passes_stable_sdk_contract_without_fallback(monkeypatch) -> None:
    captured = {}

    def run_sdk(**kwargs):
        captured.update(kwargs)
        return "done", 0.25, "actual", True

    monkeypatch.setattr(worker, "_run_claude_sdk_ceo_turn", run_sdk)
    result = worker._run_ceo_turn(
        slug="acme",
        system_prompt="system",
        user_prompt="work",
        toolsets=["takyon"],
        max_turns=10,
        inactivity_limit=30,
        wall_clock_limit=90,
        agent_runtime="claude-agent-sdk",
        sdk_session_id="job-1",
        sdk_resume_session=True,
        sdk_max_budget_usd=2.0,
        sdk_effort="high",
        sdk_epoch="bootstrap:2",
        record_final_chat=False,
    )

    assert result == ("done", 0.25, "actual", True)
    assert captured["sdk_session_id"] == "job-1"
    assert captured["sdk_resume_session"] is True
    assert captured["sdk_epoch"] == "bootstrap:2"
    assert captured["wall_clock_limit"] == 90
    assert captured["record_final_chat"] is False


@pytest.mark.parametrize("task_kind", ["ceo_bootstrap", "ceo_wake"])
def test_sdk_retry_before_session_init_restarts_same_stable_session(
    monkeypatch, tmp_path, task_kind
) -> None:
    owner = str(uuid.uuid4())
    captured = {}

    class Store:
        project_key = "scoped-project"

        def has_durable_transcript(self, key):
            captured["evidence_key"] = key
            return False

    def fake_run(**kwargs):
        captured.update(kwargs)
        return {
            "session_id": kwargs["session_id"],
            "summary": "done",
            "total_cost_usd": 0.0,
        }

    monkeypatch.setattr(worker, "_business_owner_user_id", lambda _slug: owner)
    monkeypatch.setattr(claude_sdk_sessions, "PostgresClaudeSdkSessionStore", lambda **_kw: Store())
    monkeypatch.setattr(claude_sdk_runtime, "run_primary_sdk_subprocess", fake_run)
    monkeypatch.setattr(
        core,
        "_active_operator_task_receipt_context",
        lambda: {"run_id": "job-1", "task_kind": task_kind},
    )
    monkeypatch.setattr(cost_events, "record_operator_event_autoconn", lambda **_kw: None)
    monkeypatch.setattr(worker, "_record_ceo_turn_chat", lambda *_a: None)
    monkeypatch.setattr(
        "gateway.session_context.get_session_env",
        lambda key, default="": str(tmp_path)
        if key == "TAKYON_SESSION_WORKSPACE_ROOT"
        else default,
    )

    worker._run_claude_sdk_ceo_turn(
        slug="acme",
        system_prompt="system",
        user_prompt="retry",
        toolsets=["takyon"],
        max_turns=10,
        max_budget_usd=2,
        effort="high",
        inactivity_limit=30,
        sdk_session_id="job-1",
        sdk_resume_session=True,
        sdk_epoch="bootstrap" if task_kind == "ceo_bootstrap" else "wake",
    )

    stable_session = claude_sdk_runtime.stable_sdk_session_id("job-1")
    assert captured["resume_session"] is False
    assert captured["session_id"] == stable_session
    assert captured["evidence_key"] == {
        "projectKey": "scoped-project",
        "sessionId": stable_session,
    }


@pytest.mark.parametrize("task_kind", ["ceo_bootstrap", "ceo_wake"])
def test_sdk_retry_resumes_only_with_real_durable_transcript(
    monkeypatch, tmp_path, task_kind
) -> None:
    owner = str(uuid.uuid4())
    captured = {}

    class Store:
        project_key = "scoped-project"
        transcript = [{"type": "user", "uuid": "durable-user-turn"}]

        def has_durable_transcript(self, _key):
            return bool(self.transcript)

    def fake_run(**kwargs):
        captured.update(kwargs)
        return {
            "session_id": kwargs["session_id"],
            "summary": "continued",
            "total_cost_usd": 0.0,
        }

    monkeypatch.setattr(worker, "_business_owner_user_id", lambda _slug: owner)
    monkeypatch.setattr(claude_sdk_sessions, "PostgresClaudeSdkSessionStore", lambda **_kw: Store())
    monkeypatch.setattr(claude_sdk_runtime, "run_primary_sdk_subprocess", fake_run)
    monkeypatch.setattr(
        core,
        "_active_operator_task_receipt_context",
        lambda: {"run_id": "job-1", "task_kind": task_kind},
    )
    monkeypatch.setattr(cost_events, "record_operator_event_autoconn", lambda **_kw: None)
    monkeypatch.setattr(worker, "_record_ceo_turn_chat", lambda *_a: None)
    monkeypatch.setattr(
        "gateway.session_context.get_session_env",
        lambda key, default="": str(tmp_path)
        if key == "TAKYON_SESSION_WORKSPACE_ROOT"
        else default,
    )

    worker._run_claude_sdk_ceo_turn(
        slug="acme",
        system_prompt="system",
        user_prompt="retry",
        toolsets=["takyon"],
        max_turns=10,
        max_budget_usd=2,
        effort="high",
        inactivity_limit=30,
        sdk_session_id="job-1",
        sdk_resume_session=True,
        sdk_epoch="bootstrap" if task_kind == "ceo_bootstrap" else "wake",
    )

    assert captured["resume_session"] is True


@pytest.mark.parametrize(
    ("record_final_chat", "expected_chats"),
    (
        (
            True,
            [
                "Research complete; moving into product design.",
                "Published the real product.",
            ],
        ),
        (False, ["Research complete; moving into product design."]),
    ),
)
def test_sdk_turn_mirrors_interim_chat_and_only_requested_final_chat(
    monkeypatch, tmp_path, record_final_chat, expected_chats
) -> None:
    owner = str(uuid.uuid4())
    chats: list[str] = []
    captured = {}

    class Progress:
        def __init__(self):
            self.skills = []
            self.lines = []

        def has_active_tool(self):
            return False

        def _record_trace(self, **kwargs):
            self.skills.append(kwargs)

        def emit(self, line):
            self.lines.append(line)

        def tool_started(self, *_args):
            pass

        def tool_completed(self, *_args):
            pass

    progress = Progress()

    def fake_run(**kwargs):
        captured.update(kwargs)
        kwargs["progress_callback"](
            {
                "kind": "assistant",
                "status": "output",
                "detail": "Research complete; moving into product design.",
                "trace": {"message_role": "interim"},
            }
        )
        kwargs["progress_callback"](
            {
                "kind": "skill",
                "status": "started",
                "detail": "Taste started",
                "trace": {"skill_name": "design-taste-frontend"},
            }
        )
        return {
            "session_id": kwargs["session_id"],
            "summary": "Published the real product.",
            "model": "claude-test",
            "actual_models": ["claude-test"],
            "usage": {
                "input_tokens": 10,
                "output_tokens": 5,
                "cache_read_input_tokens": 2,
            },
            "total_cost_usd": 0.125,
            "skill_receipt": {
                "started": ["design-taste-frontend"],
                "completed": ["design-taste-frontend"],
            },
            "invocation_id": str(uuid.uuid4()),
            "invocation_total_ceiling_microusd": 2_000_000,
            "invocation_per_call_ceiling_microusd": 500_000,
        }

    monkeypatch.setattr(worker, "_business_owner_user_id", lambda _slug: owner)
    monkeypatch.setattr(worker, "_record_ceo_turn_chat", lambda _slug, text: chats.append(text))
    monkeypatch.setattr(claude_sdk_runtime, "run_primary_sdk_subprocess", fake_run)
    monkeypatch.setattr(
        claude_sdk_sessions, "PostgresClaudeSdkSessionStore", lambda **_kwargs: object()
    )
    monkeypatch.setattr(
        core,
        "_active_operator_task_receipt_context",
        lambda: {"run_id": "job-1", "task_kind": "ceo_bootstrap"},
    )
    monkeypatch.setattr(cost_events, "record_operator_event_autoconn", lambda **_kwargs: None)
    monkeypatch.setattr(
        "gateway.session_context.get_session_env",
        lambda key, default="": str(tmp_path)
        if key == "TAKYON_SESSION_WORKSPACE_ROOT"
        else default,
    )

    final, cost, status, completed = worker._run_claude_sdk_ceo_turn(
        slug="acme",
        system_prompt="system",
        user_prompt="work",
        toolsets=["takyon"],
        max_turns=10,
        max_budget_usd=2,
        effort="high",
        inactivity_limit=30,
        sdk_session_id="job-1",
        sdk_resume_session=False,
        sdk_epoch="bootstrap:1",
        wall_clock_limit=90,
        progress=progress,
        record_final_chat=record_final_chat,
    )

    assert (final, cost, status, completed) == (
        "Published the real product.",
        0.125,
        "actual",
        True,
    )
    assert chats == expected_chats
    assert captured["mode"] == "bootstrap"
    assert progress.skills[0]["skill_name"] == "design-taste-frontend"
    receipt = worker._consume_sdk_turn_receipt()
    assert receipt["epoch"] == "bootstrap:1"
    assert receipt["usage"]["input_tokens"] == 10
    assert receipt["skill_receipt"]["completed"] == ["design-taste-frontend"]
    assert receipt["invocation_total_ceiling_microusd"] == 2_000_000
