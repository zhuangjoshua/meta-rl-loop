from __future__ import annotations

import contextlib
import uuid
from pathlib import Path

import pytest

from cron import scheduler
from gateway.session_context import get_session_env
from plugins.takyon import (
    claude_sdk_runtime,
    claude_sdk_sessions,
    operator_gateway,
    safebox,
    turn_runtime,
)


def _install_scoped_sdk_stubs(monkeypatch, tmp_path: Path):
    owner = str(uuid.uuid4())
    calls: list[dict] = []
    stores: list[dict] = []
    seen_sessions: set[str] = set()

    class Store:
        def __init__(self, *, operator_user_id: str, business_slug: str):
            stores.append(
                {
                    "owner": operator_user_id,
                    "business": business_slug,
                    "loads": [],
                }
            )
            self.record = stores[-1]

        def load(self, key):
            self.record["loads"].append(dict(key))
            session_id = str(key.get("sessionId") or "")
            resumed = session_id in seen_sessions
            seen_sessions.add(session_id)
            return [] if resumed else None

    @contextlib.contextmanager
    def workspace(slug, *, operator_user_id=None, sync_on_exception=False):
        assert slug == "acme"
        assert operator_user_id == owner
        assert sync_on_exception is True
        yield tmp_path

    def run_sdk(**kwargs):
        calls.append(dict(kwargs))
        calls[-1]["bound_context"] = {
            "owner": get_session_env("TAKYON_SESSION_USER_ID", ""),
            "business": get_session_env("TAKYON_SESSION_BUSINESS_SLUG", ""),
            "workspace": get_session_env("TAKYON_SESSION_WORKSPACE_ROOT", ""),
            "task_kind": get_session_env("TAKYON_SESSION_TASK_KIND", ""),
            "delivery_platform": get_session_env(
                "TAKYON_CRON_AUTO_DELIVER_PLATFORM", ""
            ),
            "delivery_chat_id": get_session_env(
                "TAKYON_CRON_AUTO_DELIVER_CHAT_ID", ""
            ),
            "delivery_thread_id": get_session_env(
                "TAKYON_CRON_AUTO_DELIVER_THREAD_ID", ""
            ),
        }
        return {
            "session_id": kwargs["session_id"],
            "summary": "scheduled report",
            "usage": {"input_tokens": 4, "output_tokens": 2},
        }

    monkeypatch.setattr(scheduler, "_cron_business_owner_user_id", lambda _slug: owner)
    monkeypatch.setattr(scheduler, "_cron_sdk_budget_usd", lambda: 2.5)
    monkeypatch.setattr(
        scheduler,
        "_cron_sdk_invocation_allowed_tools",
        lambda _job, _cfg: ["business_read_business", "skill_read_resource"],
    )
    monkeypatch.setattr(scheduler, "_resolve_delivery_target", lambda _job: None)
    monkeypatch.setattr(scheduler, "_get_takyon_home", lambda: tmp_path)
    monkeypatch.setattr(claude_sdk_sessions, "PostgresClaudeSdkSessionStore", Store)
    monkeypatch.setattr(turn_runtime, "_business_workspace_execution_context", workspace)
    monkeypatch.setattr(claude_sdk_runtime, "run_primary_sdk_subprocess", run_sdk)
    monkeypatch.setattr(
        operator_gateway,
        "compose_primary_agent_system_prompt",
        lambda *parts: "\n".join(str(part) for part in parts),
    )
    monkeypatch.setenv("TAKYON_CRON_TIMEOUT", "0")
    import dotenv

    monkeypatch.setattr(dotenv, "load_dotenv", lambda *_args, **_kwargs: True)
    return owner, calls, stores


def test_scoped_cron_uses_primary_sdk_exact_scope_and_durable_session(
    monkeypatch, tmp_path
):
    owner, calls, stores = _install_scoped_sdk_stubs(monkeypatch, tmp_path)
    job = {
        "id": "cron-1",
        "name": "daily",
        "prompt": "Prepare the daily report.",
        "business": "acme",
        "next_run_at": "2026-07-13T12:00:00+00:00",
        "schedule_display": "every 1d",
    }

    success, output, final, error = scheduler.run_job(job)

    assert (success, final, error) == (True, "scheduled report", None)
    assert "scheduled report" in output
    assert len(calls) == 1
    call = calls[0]
    assert call["business"] == "acme"
    assert call["operator_user_id"] == owner
    assert call["workspace_root"] == str(tmp_path.resolve())
    assert call["mode"] == "wake"
    assert call["epoch"] == "cron:cron-1:2026-07-13T12:00:00+00:00"
    assert call["max_budget_usd"] == 2.5
    assert call["invocation_allowed_tools"] == [
        "business_read_business",
        "skill_read_resource",
    ]
    assert call["bound_context"] == {
        "owner": owner,
        "business": "acme",
        "workspace": str(tmp_path.resolve()),
        "task_kind": "ceo_wake",
        "delivery_platform": "",
        "delivery_chat_id": "",
        "delivery_thread_id": "",
    }
    assert stores[0]["owner"] == owner
    assert stores[0]["business"] == "acme"
    assert stores[0]["loads"][0]["sessionId"] == call["session_id"]


def test_distinct_cron_occurrences_get_distinct_sessions_and_epochs(
    monkeypatch, tmp_path
):
    _owner, calls, _stores = _install_scoped_sdk_stubs(monkeypatch, tmp_path)
    base = {
        "id": "cron-2",
        "name": "daily",
        "prompt": "Report.",
        "business": "acme",
    }

    scheduler.run_job({**base, "next_run_at": "2026-07-13T12:00:00+00:00"})
    scheduler.run_job({**base, "next_run_at": "2026-07-14T12:00:00+00:00"})

    assert calls[0]["session_id"] != calls[1]["session_id"]
    assert calls[0]["epoch"] != calls[1]["epoch"]
    assert calls[0]["resume_session"] is False
    assert calls[1]["resume_session"] is False


def test_same_cron_occurrence_retry_resumes_same_session(monkeypatch, tmp_path):
    _owner, calls, _stores = _install_scoped_sdk_stubs(monkeypatch, tmp_path)
    job = {
        "id": "cron-retry",
        "name": "daily",
        "prompt": "Report.",
        "business": "acme",
        "next_run_at": "2026-07-13T12:00:00+00:00",
    }

    scheduler.run_job(dict(job))
    scheduler.run_job(dict(job))

    assert calls[0]["session_id"] == calls[1]["session_id"]
    assert calls[0]["epoch"] == calls[1]["epoch"]
    assert calls[0]["resume_session"] is False
    assert calls[1]["resume_session"] is True


def test_cron_accepts_exact_pinned_sdk_routing_assertions(monkeypatch, tmp_path):
    _owner, calls, _stores = _install_scoped_sdk_stubs(monkeypatch, tmp_path)
    monkeypatch.setattr(
        safebox,
        "provider_proxy_base_url",
        lambda: "http://10.116.0.2:8000/",
    )

    success, _output, final, error = scheduler.run_job(
        {
            "id": "cron-pinned-routing",
            "name": "pinned routing",
            "prompt": "Report.",
            "business": "acme",
            "model": "deepseek-v4-pro",
            "provider": "deepseek",
            "base_url": "http://10.116.0.2:8000",
            "next_run_at": "2026-07-13T12:00:00+00:00",
        }
    )

    assert (success, final, error) == (True, "scheduled report", None)
    assert len(calls) == 1


@pytest.mark.parametrize(
    ("field", "value", "expected"),
    (
        ("model", "gpt-5.5", "model='gpt-5.5' conflicts"),
        ("provider", "openai", "provider='openai' conflicts"),
        (
            "base_url",
            "https://api.deepseek.com",
            "base_url='https://api.deepseek.com' conflicts",
        ),
    ),
)
def test_cron_blocks_divergent_sdk_routing_without_fallback(
    monkeypatch, tmp_path, field, value, expected
):
    _owner, calls, _stores = _install_scoped_sdk_stubs(monkeypatch, tmp_path)
    monkeypatch.setattr(
        safebox,
        "provider_proxy_base_url",
        lambda: "http://10.116.0.2:8000",
    )
    job = {
        "id": f"cron-bad-{field}",
        "name": "blocked routing",
        "prompt": "Report.",
        "business": "acme",
        "next_run_at": "2026-07-13T12:00:00+00:00",
        field: value,
    }

    success, output, final, error = scheduler.run_job(job)

    assert success is False
    assert final == ""
    assert "**Status:** BLOCKED" in output
    assert expected in error
    assert "no model/provider fallback is permitted" in error
    assert calls == []


def test_scoped_cron_preserves_delivery_context(monkeypatch, tmp_path):
    _owner, calls, _stores = _install_scoped_sdk_stubs(monkeypatch, tmp_path)
    monkeypatch.setattr(
        scheduler,
        "_resolve_delivery_target",
        lambda _job: {
            "platform": "telegram",
            "chat_id": "123",
            "thread_id": "456",
        },
    )

    scheduler.run_job(
        {
            "id": "cron-delivery",
            "prompt": "Report.",
            "business": "acme",
            "next_run_at": "2026-07-13T12:00:00+00:00",
        }
    )

    assert calls[0]["bound_context"]["delivery_platform"] == "telegram"
    assert calls[0]["bound_context"]["delivery_chat_id"] == "123"
    assert calls[0]["bound_context"]["delivery_thread_id"] == "456"


def test_wake_gate_false_skips_scoped_sdk(monkeypatch):
    called = False

    def run_sdk(**_kwargs):
        nonlocal called
        called = True

    monkeypatch.setattr(claude_sdk_runtime, "run_primary_sdk_subprocess", run_sdk)
    monkeypatch.setattr(
        scheduler,
        "_run_job_script",
        lambda _path: (True, '{"wakeAgent": false}'),
    )

    success, _output, final, error = scheduler.run_job(
        {
            "id": "cron-gated",
            "prompt": "Report.",
            "business": "acme",
            "script": "gate.py",
        }
    )

    assert (success, final, error) == (True, scheduler.SILENT_MARKER, None)
    assert called is False


def test_wake_gate_true_runs_script_once_and_injects_output(monkeypatch, tmp_path):
    _owner, calls, _stores = _install_scoped_sdk_stubs(monkeypatch, tmp_path)
    count = 0

    def run_script(_path):
        nonlocal count
        count += 1
        return True, '{"wakeAgent": true, "items": 3}'

    monkeypatch.setattr(scheduler, "_run_job_script", run_script)
    success, _output, _final, error = scheduler.run_job(
        {
            "id": "cron-gated",
            "prompt": "Report.",
            "business": "acme",
            "script": "gate.py",
            "next_run_at": "2026-07-13T12:00:00+00:00",
        }
    )

    assert success is True and error is None
    assert count == 1
    assert '{"wakeAgent": true, "items": 3}' in calls[0]["user_prompt"]


def test_root_cron_without_authenticated_owner_fails_before_sdk(monkeypatch, tmp_path):
    called = False

    def run_sdk(**_kwargs):
        nonlocal called
        called = True

    monkeypatch.setattr(claude_sdk_runtime, "run_primary_sdk_subprocess", run_sdk)
    monkeypatch.setattr(scheduler, "_get_takyon_home", lambda: tmp_path)
    monkeypatch.setattr(scheduler, "_resolve_delivery_target", lambda _job: None)
    import dotenv

    monkeypatch.setattr(dotenv, "load_dotenv", lambda *_args, **_kwargs: True)
    success, _output, final, error = scheduler.run_job(
        {"id": "unscoped", "name": "legacy", "prompt": "work"}
    )

    assert success is False
    assert final == ""
    assert "no authenticated operator identity" in error
    assert called is False


def test_root_scoped_cron_uses_primary_sdk_with_read_only_global_policy(
    monkeypatch, tmp_path
):
    owner, calls, stores = _install_scoped_sdk_stubs(monkeypatch, tmp_path)

    success, _output, final, error = scheduler.run_job(
        {
            "id": "root-cron",
            "name": "portfolio report",
            "prompt": "Summarize my businesses.",
            "operator_user_id": owner,
            "next_run_at": "2026-07-13T12:00:00+00:00",
        }
    )

    assert (success, final, error) == (True, "scheduled report", None)
    call = calls[0]
    assert call["business"] == ""
    assert call["operator_user_id"] == owner
    assert call["mode"] == "interactive"
    assert call["bound_context"]["business"] == ""
    assert call["bound_context"]["task_kind"] == "cron"
    assert call["invocation_allowed_tools"] == sorted(
        claude_sdk_runtime.SDK_GLOBAL_OPERATOR_TOOLS
    )
    assert stores[0]["business"] == ""


def test_no_agent_cron_remains_unscoped_and_never_calls_sdk(monkeypatch, tmp_path):
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    script = scripts / "watch.py"
    script.write_text("print('watchdog ok')\n", encoding="utf-8")
    monkeypatch.setattr(scheduler, "_get_takyon_home", lambda: tmp_path)
    called = False

    def run_sdk(**_kwargs):
        nonlocal called
        called = True

    monkeypatch.setattr(claude_sdk_runtime, "run_primary_sdk_subprocess", run_sdk)
    success, _output, final, error = scheduler.run_job(
        {
            "id": "watchdog",
            "name": "watchdog",
            "no_agent": True,
            "script": "watch.py",
        }
    )

    assert (success, final, error) == (True, "watchdog ok", None)
    assert called is False


def test_cron_explicit_skill_uses_native_approved_wake_skill():
    prompt = scheduler._build_job_prompt(
        {
            "id": "skill-job",
            "skills": ["takyon-market-research"],
            "prompt": "Research this market.",
        }
    )

    assert "`takyon-approved-skills:takyon-market-research`" in prompt
    assert "full skill content is loaded below" not in prompt


def test_cron_accepts_every_release_skill_in_wake_mode():
    prompt = scheduler._build_job_prompt(
        {
            "id": "skill-job",
            "skills": ["design-taste-frontend"],
            "prompt": "Redesign the site.",
        }
    )
    assert "`takyon-approved-skills:design-taste-frontend`" in prompt


def test_scheduler_has_no_legacy_agent_or_mutable_skill_loader_reachability():
    source = Path(scheduler.__file__).read_text(encoding="utf-8")
    assert "AIAgent" not in source
    assert "skill_view" not in source
    assert "build_operator_gateway_agent" not in source
    assert "run_conversation" not in source
