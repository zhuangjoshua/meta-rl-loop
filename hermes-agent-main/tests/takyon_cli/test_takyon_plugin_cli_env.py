from __future__ import annotations

import os
import sys


def test_run_takyon_command_loads_env_before_store(monkeypatch):
    import plugins.takyon.cli as takyon_cli

    order: list[str] = []

    class _Store:
        def __init__(self, *args, **kwargs):
            order.append(f"store:{bool(os.environ.get('DATABASE_URL'))}")

        def read(self, *, scope, query, **kwargs):
            return {"success": True, "scope": scope, "businesses": []}

    monkeypatch.delenv("DATABASE_URL", raising=False)

    def _fake_load() -> list[str]:
        order.append("env")
        os.environ["DATABASE_URL"] = "postgresql://example.invalid:6543/takyon"
        return ["/tmp/.takyon/.env"]

    monkeypatch.setattr(takyon_cli, "load_takyon_env", _fake_load)
    monkeypatch.setattr(takyon_cli, "TakyonStore", _Store)

    result = takyon_cli.run_takyon_command(["businesses"])

    assert result["success"] is True
    assert order == ["env", "store:True"]


def test_log_follow_flags_are_stripped_from_command_args():
    import plugins.takyon.cli as takyon_cli

    clean, follow_logs = takyon_cli._strip_log_follow_flags(
        ["shell", "homework-solver", "--logs"],
        default=False,
    )
    assert clean == ["shell", "homework-solver"]
    assert follow_logs is True

    clean, follow_logs = takyon_cli._strip_log_follow_flags(
        ["run", "homework-solver", "--follow-logs", "inspect state", "--no-logs"],
        default=True,
    )
    assert clean == ["run", "homework-solver", "inspect state"]
    assert follow_logs is False


def test_shell_command_accepts_trailing_logs_flag(monkeypatch):
    import plugins.takyon.cli as takyon_cli

    captured: dict[str, object] = {}

    class _Store:
        def __init__(self, *args, **kwargs):
            pass

    def fake_shell(**kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(takyon_cli, "load_takyon_env", lambda: [])
    monkeypatch.setattr(takyon_cli, "TakyonStore", _Store)
    monkeypatch.setattr(takyon_cli, "_interactive_shell", fake_shell)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)

    result = takyon_cli.run_takyon_command(["shell", "homework-solver", "--logs"])

    assert result is None
    assert captured["initial_business"] == "homework-solver"
    assert captured["follow_logs"] is True


def test_credits_shell_command_uses_current_business():
    import plugins.takyon.cli as takyon_cli

    assert takyon_cli._command_with_current_business(
        ["credits", "buy", "125"],
        "alpha",
    ) == ["credits", "alpha", "buy", "125"]
    assert takyon_cli._command_with_current_business(
        ["credits", "allocate", "meta", "50"],
        "alpha",
    ) == ["credits", "alpha", "allocate", "meta", "50"]


def test_credits_buy_creates_checkout(monkeypatch):
    import plugins.takyon.cli as takyon_cli
    from plugins.takyon import safebox

    captured: dict[str, object] = {}

    class _Store:
        def __init__(self, *args, **kwargs):
            pass

    def fake_checkout(user_id, business, **kwargs):
        captured["user_id"] = user_id
        captured["business"] = business
        captured.update(kwargs)
        return {
            "checkout_url": "https://checkout.stripe.test/session",
            "session_id": "cs_test_123",
            "business_slug": business,
            "credits": kwargs["credits"],
            "amount_cents": 125,
        }

    monkeypatch.setattr(takyon_cli, "load_takyon_env", lambda: [])
    monkeypatch.setattr(takyon_cli, "TakyonStore", _Store)
    monkeypatch.setattr(takyon_cli, "_credit_business_owner_user_id", lambda *_a, **_k: "user_123")
    monkeypatch.setattr(takyon_cli, "_credits_default_return_url", lambda _business: "https://app.example/")
    monkeypatch.setattr(safebox, "create_creative_credit_checkout", fake_checkout)

    result = takyon_cli.run_takyon_command(
        ["credits", "alpha", "buy", "125"],
        operator_user_id="user_123",
    )

    assert "checkout_url=https://checkout.stripe.test/session" in result
    assert captured["business"] == "alpha"
    assert captured["credits"] == 125
    assert captured["success_url"] == "https://app.example/"


def test_credits_allocate_preserves_unspecified_channels(monkeypatch):
    import json

    import plugins.takyon.cli as takyon_cli
    from plugins.takyon import core

    captured: dict[str, object] = {}

    class _Store:
        def __init__(self, *args, **kwargs):
            pass

        def enforce_operator_business_access(self, business):
            captured["access"] = business

    snapshot = {
        "balance_credits": 100,
        "reserved_credits": 0,
        "unallocated_credits": 99,
        "channels": {
            "x": {"allocated_credits": 1, "used_credits": 0, "reserved_credits": 0, "remaining_credits": 1},
            "meta": {"allocated_credits": 0, "used_credits": 0, "reserved_credits": 0, "remaining_credits": 0},
            "reddit": {"allocated_credits": 0, "used_credits": 0, "reserved_credits": 0, "remaining_credits": 0},
        },
    }

    def fake_set(args):
        captured["allocations"] = args["allocations"]
        updated = {
            **snapshot,
            "channels": {
                **snapshot["channels"],
                "meta": {"allocated_credits": 50, "used_credits": 0, "reserved_credits": 0, "remaining_credits": 50},
            },
        }
        return json.dumps({"success": True, "value": updated})

    monkeypatch.setattr(takyon_cli, "load_takyon_env", lambda: [])
    monkeypatch.setattr(takyon_cli, "TakyonStore", _Store)
    monkeypatch.setattr(takyon_cli, "_read_credit_snapshot", lambda _business: snapshot)
    monkeypatch.setattr(core, "handle_business_set_channel_credit_budgets", fake_set)

    result = takyon_cli.run_takyon_command(["credits", "alpha", "allocate", "meta", "50"])

    assert captured["access"] == "alpha"
    assert captured["allocations"] == {"x": 1, "meta": 50, "reddit": 0}
    assert "meta: allocated=50" in result


def test_platform_owner_seed_skips_when_session_user_bound(monkeypatch, capsys):
    import plugins.takyon.cli as takyon_cli

    class _Store:
        def seed_platform_owner(self):
            raise AssertionError("session-bound operator shell must not seed platform owner")

    monkeypatch.setenv("TAKYON_SESSION_USER_ID", "150e4213-4006-4dc1-9cf3-ca7ab3b4696f")

    takyon_cli._seed_platform_owner_at_startup(_Store())

    captured = capsys.readouterr()
    assert "platform-owner seed skipped" not in captured.err


def test_shell_does_not_attach_ambient_log_tail(monkeypatch):
    import plugins.takyon.cli as takyon_cli

    events: list[object] = []

    class _Store:
        pass

    class _Tail:
        def __init__(self, *, enabled, prefix="  · ", business_filter=None):
            events.append(("init", enabled, prefix))
            events.append(("business_filter", business_filter()))

        def __enter__(self):
            events.append("enter")
            return self

        def __exit__(self, _exc_type, _exc, _tb):
            events.append("exit")

    monkeypatch.setattr(takyon_cli, "TakyonStore", _Store)
    monkeypatch.setattr(takyon_cli, "_seed_platform_owner_at_startup", lambda _store: None)
    monkeypatch.setattr(takyon_cli, "_business_exists", lambda _store, _slug: True)
    monkeypatch.setattr(takyon_cli, "_slash_entries", lambda: [])
    monkeypatch.setattr(takyon_cli, "_startup_graphic", lambda _business: "ready")
    monkeypatch.setattr(takyon_cli, "_read_shell_line", lambda _business, _entries: "exit")
    monkeypatch.setattr(takyon_cli, "_AgentLogTail", _Tail)

    takyon_cli._interactive_shell(
        initial_business="homework-solver",
        model="",
        max_turns=1,
        follow_logs=True,
    )

    assert events == []


def test_agent_log_tail_nested_context_does_not_duplicate(monkeypatch, tmp_path):
    import plugins.takyon.cli as takyon_cli

    log_path = tmp_path / "agent.log"
    log_path.write_text("", encoding="utf-8")
    monkeypatch.setattr(takyon_cli, "_agent_log_path", lambda: log_path)
    takyon_cli._AgentLogTail._active = 0

    with takyon_cli._AgentLogTail(enabled=True) as outer:
        with takyon_cli._AgentLogTail(enabled=True) as inner:
            assert outer.enabled is True
            assert inner.enabled is False
            assert takyon_cli._AgentLogTail._active == 1

    assert takyon_cli._AgentLogTail._active == 0


def test_agent_log_tail_filters_to_shell_business():
    import plugins.takyon.cli as takyon_cli

    tail = takyon_cli._AgentLogTail(enabled=True, business_filter="homework-solver")

    assert not tail._should_print_line(
        "INFO [20260628_160240_69990c] conversation turn: session=other msg='CEO wakeup for business:agaagv'"
    )
    assert tail._should_print_line(
        "INFO [20260628_160240_111111] conversation turn: session=mine msg='CEO wakeup for business:homework-solver'"
    )
    assert tail._should_print_line(
        "INFO [20260628_160240_111111] agent.tool_executor: tool business_read_file completed session=mine"
    )
    assert not tail._should_print_line(
        "INFO [20260628_160240_69990c] agent.tool_executor: tool business_read_file completed session=other"
    )


def test_agent_log_tail_stays_silent_in_global_scope():
    import plugins.takyon.cli as takyon_cli

    tail = takyon_cli._AgentLogTail(enabled=True, business_filter=None)

    assert not tail._should_print_line(
        "INFO [20260628_160240_111111] conversation turn: session=mine msg='CEO wakeup for business:homework-solver'"
    )
    assert not tail._should_print_line(
        "2026-06-30 09:20:39,682 INFO takyon.worker: worker[mac-operator-Anuradhas-MacBook-Pro-85887]: starting (dispatch=True poll=5s concurrency=10)"
    )


def test_business_list_formats_as_short_picker():
    import plugins.takyon.cli as takyon_cli

    rendered = takyon_cli._format_cli_value(
        {
            "scope": "global",
            "businesses": [
                {
                    "slug": "homework-solver",
                    "name": "Homework Solver",
                    "status": "active",
                    "mode": "live",
                    "goal": "Long goal text should not show in the picker.",
                    "work_focus": "all",
                },
                {
                    "slug": "simple",
                    "name": "Simple",
                    "goal": "Another long goal that should stay hidden.",
                },
            ],
            "controls": [
                {
                    "scope": "business:homework-solver",
                    "state": "active",
                    "reason": "operator-only diagnostic detail",
                }
            ],
        }
    )

    assert rendered == "Businesses:\n  homework-solver - Homework Solver\n  simple - Simple"
    assert "Long goal text" not in rendered
    assert "Controls:" not in rendered
