"""Tests for tui_gateway JSON-RPC protocol plumbing."""

import io
import json
import sqlite3
import sys
import threading
import time
import types
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import pytest

_original_stdout = sys.stdout


@pytest.fixture(autouse=True)
def _restore_stdout():
    yield
    sys.stdout = _original_stdout


@pytest.fixture()
def server():
    with patch.dict("sys.modules", {
        "takyon_constants": MagicMock(get_takyon_home=MagicMock(return_value="/tmp/takyon_test")),
        "takyon_cli.env_loader": MagicMock(),
        "takyon_cli.banner": MagicMock(),
        "takyon_state": MagicMock(),
    }):
        import importlib
        mod = importlib.import_module("tui_gateway.server")
        yield mod
        mod._sessions.clear()
        mod._pending.clear()
        mod._answers.clear()
        mod._methods.clear()
        importlib.reload(mod)


@pytest.fixture()
def capture(server):
    """Redirect server's real stdout to a StringIO and return (server, buf)."""
    buf = io.StringIO()
    server._real_stdout = buf
    return server, buf


# ── JSON-RPC envelope ────────────────────────────────────────────────


def test_unknown_method(server):
    resp = server.handle_request({"id": "1", "method": "bogus"})
    assert resp["error"]["code"] == -32601


def test_ok_envelope(server):
    assert server._ok("r1", {"x": 1}) == {
        "jsonrpc": "2.0", "id": "r1", "result": {"x": 1},
    }


def test_err_envelope(server):
    assert server._err("r2", 4001, "nope") == {
        "jsonrpc": "2.0", "id": "r2", "error": {"code": 4001, "message": "nope"},
    }


def test_takyon_wake_shell_exec_returns_before_background_run(server, monkeypatch):
    sid = "takyon-session"
    server._sessions[sid] = {"takyon_current_business": "latexflow"}
    ran = threading.Event()

    fake_cli = types.ModuleType("plugins.takyon.cli")

    class FakeStore:
        def __init__(self, *args, **kwargs):
            pass

    def fake_handle_shell_line(*_args, **_kwargs):
        ran.set()
        return "wake finished", "latexflow"

    def fake_record_shell_turn(history, line, output):
        history.append({"line": line, "output": output})

    fake_cli.TakyonStore = FakeStore
    fake_cli._handle_shell_line = fake_handle_shell_line
    fake_cli._record_shell_turn = fake_record_shell_turn
    monkeypatch.setitem(sys.modules, "plugins.takyon.cli", fake_cli)
    class FakeProcess:
        def __init__(self, *args, **kwargs):
            ran.set()
            self.stdout = iter(["wake finished\n"])

        def poll(self):
            return 0

        def wait(self, timeout=None):
            return 0

        def kill(self):
            return None

    monkeypatch.setattr(server.subprocess, "Popen", FakeProcess)
    monkeypatch.setattr(server, "_takyon_require_business_access", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        server,
        "_takyon_scope_payload",
        lambda session: {"scope": "business:latexflow", "business": "latexflow", "current": {}, "businesses": []},
    )

    response = server._methods["takyon.shell.exec"]("wake-1", {"session_id": sid, "line": "/wake"})

    assert response["result"]["output"].startswith("Wake started for business:latexflow")
    assert ran.wait(1)


def test_takyon_create_shell_exec_returns_before_background_bootstrap(server, monkeypatch):
    sid = "takyon-session"
    server._sessions[sid] = {"takyon_current_business": ""}
    ran = threading.Event()

    fake_cli = types.ModuleType("plugins.takyon.cli")

    class FakeStore:
        def __init__(self, *args, **kwargs):
            pass

        def read(self, *_args, **_kwargs):
            return {"businesses": []}

    def fake_parse_business_start_args(*_args, **_kwargs):
        return ("latexflow", "latexflow", "overleaf competitor", "test", "every 6h", True, False)

    def fake_handle_shell_line(*_args, **_kwargs):
        ran.set()
        return "create finished", "latexflow"

    def fake_record_shell_turn(history, line, output):
        history.append({"line": line, "output": output})

    fake_cli.TakyonStore = FakeStore
    fake_cli._parse_business_start_args = fake_parse_business_start_args
    fake_cli._handle_shell_line = fake_handle_shell_line
    fake_cli._record_shell_turn = fake_record_shell_turn
    monkeypatch.setitem(sys.modules, "plugins.takyon.cli", fake_cli)
    monkeypatch.setattr(
        server,
        "_takyon_scope_payload",
        lambda session: {"scope": f"business:{session.get('takyon_current_business')}", "business": session.get("takyon_current_business"), "current": {}, "businesses": []},
    )

    response = server._methods["takyon.shell.exec"](
        "create-1",
        {
            "session_id": sid,
            "line": '/create --test --schedule "every 6h" latexflow "overleaf competitor"',
        },
    )

    assert response["result"]["output"].startswith("Create started for business:latexflow")
    assert response["result"]["business"] == "latexflow"
    assert server._sessions[sid]["takyon_current_business"] == "latexflow"
    assert ran.is_set()


def test_takyon_create_from_global_rejects_existing_normalized_slug(server, monkeypatch):
    sid = "takyon-session"
    server._sessions[sid] = {"takyon_current_business": ""}

    fake_cli = types.ModuleType("plugins.takyon.cli")

    class FakeStore:
        def __init__(self, *args, **kwargs):
            pass

    def fake_handle_shell_line(*_args, **_kwargs):
        raise SystemExit(
            "business:latexflow already exists. /create requires a fresh slug and will not reuse an existing business."
        )

    def fake_record_shell_turn(history, line, output):
        history.append({"line": line, "output": output})

    fake_cli.TakyonStore = FakeStore
    fake_cli._handle_shell_line = fake_handle_shell_line
    fake_cli._record_shell_turn = fake_record_shell_turn
    monkeypatch.setitem(sys.modules, "plugins.takyon.cli", fake_cli)
    monkeypatch.setattr(
        server,
        "_takyon_scope_payload",
        lambda session: {"scope": f"business:{session.get('takyon_current_business')}", "business": session.get("takyon_current_business"), "current": {}, "businesses": []},
    )

    response = server._methods["takyon.shell.exec"](
        "create-unique-1",
        {
            "session_id": sid,
            "line": '/create --test --schedule "every 6h" latexflow "overleaf competitor"',
        },
    )

    assert "fresh slug" in response["result"]["output"]
    assert "latexflow" in response["result"]["output"]
    assert server._sessions[sid]["takyon_current_business"] == ""


def test_takyon_create_no_auto_stays_synchronous(server, monkeypatch):
    sid = "takyon-session"
    server._sessions[sid] = {"takyon_current_business": ""}
    ran = threading.Event()

    fake_cli = types.ModuleType("plugins.takyon.cli")

    class FakeStore:
        def __init__(self, *args, **kwargs):
            pass

    def fake_parse_business_start_args(*_args, **_kwargs):
        return ("latexflow", "latexflow", "overleaf competitor", "test", "", False, True)

    def fake_handle_shell_line(*_args, **_kwargs):
        ran.set()
        return "created without auto", "latexflow"

    def fake_record_shell_turn(history, line, output):
        history.append({"line": line, "output": output})

    fake_cli.TakyonStore = FakeStore
    fake_cli._parse_business_start_args = fake_parse_business_start_args
    fake_cli._handle_shell_line = fake_handle_shell_line
    fake_cli._record_shell_turn = fake_record_shell_turn
    monkeypatch.setitem(sys.modules, "plugins.takyon.cli", fake_cli)
    monkeypatch.setattr(
        server,
        "_takyon_scope_payload",
        lambda session: {"scope": f"business:{session.get('takyon_current_business')}", "business": session.get("takyon_current_business"), "current": {}, "businesses": []},
    )

    response = server._methods["takyon.shell.exec"](
        "create-no-auto-1",
        {"session_id": sid, "line": '/create --no-auto latexflow "overleaf competitor"'},
    )

    assert response["result"]["output"] == "created without auto"
    assert response["result"]["business"] == "latexflow"
    assert ran.is_set()


def test_takyon_dashboard_create_returns_structured_workspace(server, monkeypatch):
    sid = "takyon-session"
    server._sessions[sid] = {"takyon_current_business": "", "takyon_operator_user_id": "user-1"}

    fake_cli = types.ModuleType("plugins.takyon.cli")

    class FakeStore:
        def __init__(self, *args, **kwargs):
            self._operator_user_id = kwargs.get("operator_user_id")

    def fake_resolve_dashboard_create_identity(name, goal, slug_hint="", *, operator_user_id=None, store=None):
        assert name == "Latexflow"
        assert goal == "Overleaf competitor"
        assert slug_hint == "latexflow"
        assert operator_user_id == "user-1"
        return "Latexflow", "latexflow"

    def fake_run_takyon_command(*_args, **_kwargs):
        return {
            "success": True,
            "business": "latexflow",
            "mode": "live",
            "bootstrap_job": {
                "job_id": "job-123",
                "kind": "ceo_bootstrap",
                "status": "queued",
            },
        }

    fake_cli.TakyonStore = FakeStore
    fake_cli._resolve_dashboard_create_identity = fake_resolve_dashboard_create_identity
    fake_cli.run_takyon_command = fake_run_takyon_command
    # §3 gap #2 preflight: default the fake to a funded operator (no-op) so existing create-path
    # tests exercise the happy path; the dedicated balance-block test overrides this to raise.
    fake_cli._operator_create_balance_preflight = lambda *_a, **_k: None
    monkeypatch.setitem(sys.modules, "plugins.takyon.cli", fake_cli)
    monkeypatch.setattr(server, "_takyon_unique_business_slug", lambda *_args, **_kwargs: "latexflow")
    monkeypatch.setattr(
        server,
        "_takyon_require_durable_business",
        lambda *_args, **_kwargs: {"business": {"slug": "latexflow", "name": "Latexflow", "mode": "live"}},
    )
    monkeypatch.setattr(
        server,
        "_takyon_workspace_payload",
        lambda *_args, **_kwargs: {
            "business_slug": "latexflow",
            "current": {"slug": "latexflow", "name": "Latexflow", "mode": "live"},
            "overview": {"goal": "Overleaf competitor"},
            "outputs": [{"id": "surface", "title": "surface.md", "detail": "", "kind": "file", "at": 1}],
            "background_run": {"kind": "create", "status": "queued"},
        },
    )
    monkeypatch.setattr(
        server,
        "_takyon_businesses_for_session",
        lambda *_args, **_kwargs: [{"slug": "latexflow", "name": "Latexflow"}],
    )

    response = server._methods["takyon.dashboard.create"](
        "dashboard-create-1",
        {
            "session_id": sid,
            "business": "latexflow",
            "business_name": "Latexflow",
            "goal": "Overleaf competitor",
            "mode": "live",
        },
    )

    result = response["result"]
    assert result["business_slug"] == "latexflow"
    assert result["business_name"] == "Latexflow"
    assert result["job_id"] == "job-123"
    assert result["lifecycle_state"] == "queued"
    assert result["scope"] == "business:latexflow"
    assert result["current"]["slug"] == "latexflow"
    assert result["outputs"][0]["id"] == "surface"
    assert server._sessions[sid]["takyon_current_business"] == "latexflow"


def test_takyon_dashboard_create_rejects_business_scoped_session(server):
    sid = "takyon-session"
    server._sessions[sid] = {
        "takyon_current_business": "ching",
        "takyon_operator_user_id": "user-1",
    }

    response = server._methods["takyon.dashboard.create"](
        "dashboard-create-scoped-1",
        {
            "session_id": sid,
            "goal": "Create another product",
            "mode": "live",
        },
    )

    assert response["error"]["code"] == 4004
    assert "cannot create a business from business:ching" in response["error"]["message"]


def test_takyon_dashboard_create_rejects_bootstrap_false_off_skills_host(server):
    sid = "takyon-session"
    server._sessions[sid] = {
        "takyon_current_business": "",
        "takyon_operator_user_id": "user-1",
        "takyon_request_host": "app.fourmanifold.com",
    }

    response = server._methods["takyon.dashboard.create"](
        "dashboard-create-skill-lab-gate-1",
        {
            "session_id": sid,
            "business": "dev-lab",
            "business_name": "Dev Lab",
            "goal": "test goal",
            "mode": "live",
            "bootstrap": False,
        },
    )

    assert response["error"]["code"] == 4048
    assert "skills.fourmanifold.com" in response["error"]["message"]


def test_takyon_dashboard_create_bootstrap_false_creates_ready_dev_business(server, monkeypatch):
    sid = "takyon-session"
    server._sessions[sid] = {
        "takyon_current_business": "",
        "takyon_operator_user_id": "user-1",
        "takyon_request_host": "skills.fourmanifold.com",
        "agent_ready": threading.Event(),
    }
    captured: dict[str, object] = {}

    fake_cli = types.ModuleType("plugins.takyon.cli")

    class FakeStore:
        def __init__(self, *args, **kwargs):
            self._operator_user_id = kwargs.get("operator_user_id")

    def fake_resolve_dashboard_create_identity(name, goal, slug_hint="", *, operator_user_id=None, store=None):
        assert name == "Dev Lab"
        assert goal == "test goal"
        assert slug_hint == "dev-lab"
        assert operator_user_id == "user-1"
        return "Dev Lab", "dev-lab"

    def fake_run_takyon_command(argv, **_kwargs):
        captured["argv"] = list(argv)
        return {
            "success": True,
            "business": "dev-lab",
            "mode": "live",
        }

    fake_cli.TakyonStore = FakeStore
    fake_cli._resolve_dashboard_create_identity = fake_resolve_dashboard_create_identity
    fake_cli.run_takyon_command = fake_run_takyon_command
    # §3 gap #2 preflight: default the fake to a funded operator (no-op) so existing create-path
    # tests exercise the happy path; the dedicated balance-block test overrides this to raise.
    fake_cli._operator_create_balance_preflight = lambda *_a, **_k: None
    monkeypatch.setitem(sys.modules, "plugins.takyon.cli", fake_cli)
    monkeypatch.setattr(server, "_takyon_unique_business_slug", lambda *_args, **_kwargs: "dev-lab")
    monkeypatch.setattr(
        server,
        "_takyon_require_durable_business",
        lambda *_args, **_kwargs: {"business": {"slug": "dev-lab", "name": "Dev Lab", "mode": "live"}},
    )
    monkeypatch.setattr(
        server,
        "_takyon_workspace_payload",
        lambda *_args, **_kwargs: {
            "business_slug": "dev-lab",
            "current": {"slug": "dev-lab", "name": "Dev Lab", "mode": "live"},
            "overview": {"goal": "test goal"},
            "outputs": [],
            "background_run": None,
        },
    )
    monkeypatch.setattr(
        server,
        "_takyon_businesses_for_session",
        lambda *_args, **_kwargs: [{"slug": "dev-lab", "name": "Dev Lab"}],
    )
    monkeypatch.setattr(server, "_takyon_store", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(
        server,
        "_start_streaming_session_turn",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("bootstrap stream should not start")),
    )

    response = server._methods["takyon.dashboard.create"](
        "dashboard-create-skill-lab-ready-1",
        {
            "session_id": sid,
            "business": "dev-lab",
            "business_name": "Dev Lab",
            "goal": "test goal",
            "mode": "live",
            "bootstrap": False,
        },
    )

    assert captured["argv"] == [
        "create",
        "--live",
        "--no-auto",
        "--name",
        "Dev Lab",
        "dev-lab",
        "test goal",
    ]
    result = response["result"]
    assert result["business_slug"] == "dev-lab"
    assert result["lifecycle_state"] == "ready"
    assert result["job_id"] == ""
    assert result["job_kind"] == ""
    assert result["job_status"] == ""
    assert result["dev_mode"] is True
    assert result["background_run"] is None
    assert server._sessions[sid]["takyon_current_business"] == "dev-lab"


def test_takyon_dashboard_create_rejects_existing_normalized_slug(server, monkeypatch):
    sid = "takyon-session"
    server._sessions[sid] = {"takyon_current_business": "", "takyon_operator_user_id": "user-1"}

    fake_cli = types.ModuleType("plugins.takyon.cli")

    class FakeStore:
        def __init__(self, *args, **kwargs):
            self._operator_user_id = kwargs.get("operator_user_id")

    def fake_resolve_dashboard_create_identity(name, goal, slug_hint="", *, operator_user_id=None, store=None):
        assert name == "Cat App"
        assert goal == "cat app"
        assert slug_hint == "cat-app"
        assert operator_user_id == "user-1"
        return "Cat App", "cat-app"

    def fake_run_takyon_command(_argv, **_kwargs):
        raise SystemExit(
            "business:cat-app already exists. /create requires a fresh slug and will not reuse an existing business."
        )

    fake_cli.TakyonStore = FakeStore
    fake_cli._resolve_dashboard_create_identity = fake_resolve_dashboard_create_identity
    fake_cli.run_takyon_command = fake_run_takyon_command
    # §3 gap #2 preflight: default the fake to a funded operator (no-op) so existing create-path
    # tests exercise the happy path; the dedicated balance-block test overrides this to raise.
    fake_cli._operator_create_balance_preflight = lambda *_a, **_k: None
    monkeypatch.setitem(sys.modules, "plugins.takyon.cli", fake_cli)

    response = server._methods["takyon.dashboard.create"](
        "dashboard-create-unique-1",
        {
            "session_id": sid,
            "business": "cat-app",
            "business_name": "Cat App",
            "goal": "cat app",
            "mode": "live",
        },
    )

    assert response["error"]["code"] == 4004
    assert "fresh slug" in response["error"]["message"]
    assert "cat-app" in response["error"]["message"]


def test_takyon_dashboard_create_blocks_on_insufficient_operator_balance(server, monkeypatch):
    """GOAL_RULES §3 gap #2 end-to-end: the dashboard.create method runs the operator-balance
    preflight and, when it raises InsufficientOperatorBalance, returns a clean 4030 block and NEVER
    calls run_takyon_command (no business is created, no bootstrap spend)."""
    sid = "takyon-session"
    server._sessions[sid] = {"takyon_current_business": "", "takyon_operator_user_id": "broke-user"}

    fake_cli = types.ModuleType("plugins.takyon.cli")

    class InsufficientOperatorBalance(Exception):
        pass

    class FakeStore:
        def __init__(self, *args, **kwargs):
            self._operator_user_id = kwargs.get("operator_user_id")

    called = {"run": False, "resolve_identity": False}

    def fake_preflight(operator_user_id):
        assert operator_user_id == "broke-user"
        raise InsufficientOperatorBalance(
            "insufficient_balance: company creation requires a positive operator balance "
            "(spendable 0c = allowance 0c)"
        )

    def fake_resolve_dashboard_create_identity(*_a, **_k):
        called["resolve_identity"] = True  # must NOT happen — preflight runs first
        return "Broke Co", "broke-co"

    def fake_run_takyon_command(*_a, **_k):
        called["run"] = True  # must NEVER be reached on a balance block
        return {"success": True, "business": "broke-co", "mode": "live"}

    fake_cli.TakyonStore = FakeStore
    fake_cli.InsufficientOperatorBalance = InsufficientOperatorBalance
    fake_cli._operator_create_balance_preflight = fake_preflight
    fake_cli._resolve_dashboard_create_identity = fake_resolve_dashboard_create_identity
    fake_cli.run_takyon_command = fake_run_takyon_command
    monkeypatch.setitem(sys.modules, "plugins.takyon.cli", fake_cli)
    # Pin the gateway's except-clause class to the one the fake preflight raises (bypass the cached
    # lazy resolver so the block is caught deterministically regardless of test ordering).
    monkeypatch.setattr(server, "_INSUFFICIENT_OPERATOR_BALANCE_CLS", InsufficientOperatorBalance)

    response = server._methods["takyon.dashboard.create"](
        "dashboard-create-broke-1",
        {
            "session_id": sid,
            "business": "broke-co",
            "business_name": "Broke Co",
            "goal": "doomed idea",
            "mode": "live",
        },
    )

    assert response["error"]["code"] == 4030  # insufficient operator balance block
    assert "insufficient_balance" in response["error"]["message"]
    assert called["run"] is False  # run_takyon_command never reached → no business created
    assert called["resolve_identity"] is False  # preflight gates before identity resolution
    assert server._sessions[sid].get("running") in (False, None)  # session not left busy


def test_takyon_dashboard_create_derives_name_from_goal_once(server, monkeypatch):
    sid = "takyon-session"
    server._sessions[sid] = {"takyon_current_business": "", "takyon_operator_user_id": "user-1"}
    captured: dict[str, object] = {}

    fake_cli = types.ModuleType("plugins.takyon.cli")

    class FakeStore:
        def __init__(self, *args, **kwargs):
            self._operator_user_id = kwargs.get("operator_user_id")

    def fake_resolve_dashboard_create_identity(name, goal, slug_hint="", *, operator_user_id=None, store=None):
        captured["resolve"] = (name, goal, slug_hint, operator_user_id)
        return "Longer", "longer"

    def fake_run_takyon_command(argv, **_kwargs):
        captured["argv"] = list(argv)
        return {
            "success": True,
            "business": "longer",
            "mode": "live",
            "bootstrap_job": {
                "job_id": "job-321",
                "kind": "ceo_bootstrap",
                "status": "queued",
            },
        }

    fake_cli.TakyonStore = FakeStore
    fake_cli._resolve_dashboard_create_identity = fake_resolve_dashboard_create_identity
    fake_cli.run_takyon_command = fake_run_takyon_command
    # §3 gap #2 preflight: default the fake to a funded operator (no-op) so existing create-path
    # tests exercise the happy path; the dedicated balance-block test overrides this to raise.
    fake_cli._operator_create_balance_preflight = lambda *_a, **_k: None
    monkeypatch.setitem(sys.modules, "plugins.takyon.cli", fake_cli)
    monkeypatch.setattr(server, "_takyon_unique_business_slug", lambda *_args, **_kwargs: "longer")
    monkeypatch.setattr(
        server,
        "_takyon_require_durable_business",
        lambda *_args, **_kwargs: {"business": {"slug": "longer", "name": "Longer", "mode": "live"}},
    )
    monkeypatch.setattr(
        server,
        "_takyon_workspace_payload",
        lambda *_args, **_kwargs: {
            "business_slug": "longer",
            "current": {"slug": "longer", "name": "Longer", "mode": "live"},
            "overview": {"goal": "build Longer - a men's health app"},
            "outputs": [],
            "background_run": {"kind": "create", "status": "queued"},
        },
    )
    monkeypatch.setattr(
        server,
        "_takyon_businesses_for_session",
        lambda *_args, **_kwargs: [{"slug": "longer", "name": "Longer"}],
    )

    response = server._methods["takyon.dashboard.create"](
        "dashboard-create-goal-name-1",
        {
            "session_id": sid,
            "business_name": "",
            "business": "",
            "goal": "build Longer - a men's health app",
            "mode": "live",
        },
    )

    assert captured["resolve"] == ("", "build Longer - a men's health app", "", "user-1")
    assert captured["argv"] == [
        "create",
        "--live",
        "--name",
        "Longer",
        "longer",
        "build Longer - a men's health app",
    ]
    result = response["result"]
    assert result["business_slug"] == "longer"
    assert result["business_name"] == "Longer"


def test_takyon_dashboard_create_seeds_current_name_on_first_response(server, monkeypatch):
    sid = "takyon-session"
    server._sessions[sid] = {"takyon_current_business": "", "takyon_operator_user_id": "user-1"}

    fake_cli = types.ModuleType("plugins.takyon.cli")

    class FakeStore:
        def __init__(self, *args, **kwargs):
            self._operator_user_id = kwargs.get("operator_user_id")

    def fake_resolve_dashboard_create_identity(name, goal, slug_hint="", *, operator_user_id=None, store=None):
        assert operator_user_id == "user-1"
        return "Longer", "longer"

    def fake_run_takyon_command(*_args, **_kwargs):
        return {
            "success": True,
            "business": "longer",
            "mode": "live",
            "bootstrap_job": {
                "job_id": "job-456",
                "kind": "ceo_bootstrap",
                "status": "queued",
            },
        }

    fake_cli.TakyonStore = FakeStore
    fake_cli._resolve_dashboard_create_identity = fake_resolve_dashboard_create_identity
    fake_cli.run_takyon_command = fake_run_takyon_command
    # §3 gap #2 preflight: default the fake to a funded operator (no-op) so existing create-path
    # tests exercise the happy path; the dedicated balance-block test overrides this to raise.
    fake_cli._operator_create_balance_preflight = lambda *_a, **_k: None
    monkeypatch.setitem(sys.modules, "plugins.takyon.cli", fake_cli)
    monkeypatch.setattr(server, "_takyon_unique_business_slug", lambda *_args, **_kwargs: "longer")
    monkeypatch.setattr(
        server,
        "_takyon_require_durable_business",
        lambda *_args, **_kwargs: {"business": {"slug": "longer", "name": "Longer", "mode": "live"}},
    )
    monkeypatch.setattr(
        server,
        "_takyon_workspace_payload",
        lambda *_args, **_kwargs: {
            "business_slug": "longer",
            "current": {},
            "overview": {"goal": "Longer -- a men's health app"},
            "outputs": [],
            "background_run": {"kind": "create", "status": "queued"},
        },
    )
    monkeypatch.setattr(
        server,
        "_takyon_businesses_for_session",
        lambda *_args, **_kwargs: [{"slug": "longer", "name": "Longer"}],
    )
    monkeypatch.setattr(server, "_takyon_store", lambda *_args, **_kwargs: object())

    response = server._methods["takyon.dashboard.create"](
        "dashboard-create-current-name-1",
        {
            "session_id": sid,
            "business_name": "",
            "business": "",
            "goal": "Longer -- a men's health app",
            "mode": "live",
        },
    )

    current = response["result"]["current"]
    assert current["name"] == "Longer"
    assert current["slug"] == "longer"
    assert current["mode"] == "live"


def test_takyon_dashboard_create_requires_durable_business_before_streaming(server, monkeypatch):
    sid = "takyon-session"
    server._sessions[sid] = {
        "takyon_current_business": "",
        "takyon_operator_user_id": "user-1",
        "agent_ready": threading.Event(),
    }
    started = {"value": False}

    fake_cli = types.ModuleType("plugins.takyon.cli")

    class FakeStore:
        def __init__(self, *args, **kwargs):
            self._operator_user_id = kwargs.get("operator_user_id")

        def read(self, *, scope="global", query="summary", **_kwargs):
            if scope == "global":
                return {"businesses": []}
            if scope == "business:ghost":
                return {"success": True, "business": {}}
            return {"success": True}

    def fake_resolve_dashboard_create_identity(name, goal, slug_hint="", *, operator_user_id=None, store=None):
        assert name == "Ghost"
        assert goal == "ghost goal"
        assert slug_hint == "ghost"
        assert operator_user_id == "user-1"
        return "Ghost", "ghost"

    def fake_run_takyon_command(*_args, **_kwargs):
        return {"success": True, "business": "ghost", "mode": "live"}

    fake_cli.TakyonStore = FakeStore
    fake_cli._resolve_dashboard_create_identity = fake_resolve_dashboard_create_identity
    fake_cli.run_takyon_command = fake_run_takyon_command
    # §3 gap #2 preflight: default the fake to a funded operator (no-op) so existing create-path
    # tests exercise the happy path; the dedicated balance-block test overrides this to raise.
    fake_cli._operator_create_balance_preflight = lambda *_a, **_k: None
    monkeypatch.setitem(sys.modules, "plugins.takyon.cli", fake_cli)
    monkeypatch.setattr(server, "_takyon_unique_business_slug", lambda *_args, **_kwargs: "ghost")
    monkeypatch.setattr(
        server,
        "_start_streaming_session_turn",
        lambda *_args, **_kwargs: started.__setitem__("value", True),
    )

    response = server._methods["takyon.dashboard.create"](
        "dashboard-create-stream-missing-1",
        {
            "session_id": sid,
            "business": "ghost",
            "business_name": "Ghost",
            "goal": "ghost goal",
            "mode": "live",
        },
    )

    assert response["error"]["code"] == 5051
    assert "did not persist business:ghost" in response["error"]["message"]
    assert started["value"] is False
    assert server._sessions[sid]["running"] is False


def test_takyon_dashboard_create_backfills_missing_bootstrap_job(server, monkeypatch):
    sid = "takyon-session"
    server._sessions[sid] = {
        "takyon_current_business": "",
        "takyon_operator_user_id": "user-1",
        "agent_ready": threading.Event(),
        "history_lock": threading.Lock(),
    }
    captured: dict[str, object] = {}

    fake_cli = types.ModuleType("plugins.takyon.cli")

    class FakeStore:
        def __init__(self, *args, **kwargs):
            self._operator_user_id = kwargs.get("operator_user_id")

    def fake_resolve_dashboard_create_identity(name, goal, slug_hint="", *, operator_user_id=None, store=None):
        assert operator_user_id == "user-1"
        return "Latexflow", "latexflow"

    def fake_run_takyon_command(argv, *_args, **_kwargs):
        captured["argv"] = list(argv)
        return {"success": True, "business": "latexflow", "mode": "live"}

    def fake_enqueue(store, slug, *, goal, mode, schedule, max_turns):
        captured["enqueue"] = {
            "operator_user_id": store._operator_user_id,
            "slug": slug,
            "goal": goal,
            "mode": mode,
            "schedule": schedule,
            "max_turns": max_turns,
        }
        return {"job_id": "job-backfill", "kind": "ceo_bootstrap", "status": "queued"}

    fake_cli.TakyonStore = FakeStore
    fake_cli._resolve_dashboard_create_identity = fake_resolve_dashboard_create_identity
    fake_cli.run_takyon_command = fake_run_takyon_command
    fake_cli._enqueue_pg_ceo_bootstrap = fake_enqueue
    fake_cli._operator_create_balance_preflight = lambda *_a, **_k: None
    monkeypatch.setitem(sys.modules, "plugins.takyon.cli", fake_cli)
    monkeypatch.setattr(
        server,
        "_takyon_require_durable_business",
        lambda *_args, **_kwargs: {"business": {"slug": "latexflow", "name": "Latexflow", "mode": "live"}},
    )
    monkeypatch.setattr(
        server,
        "_takyon_workspace_payload",
        lambda *_args, **_kwargs: {
            "business_slug": "latexflow",
            "current": {"slug": "latexflow", "name": "Latexflow", "mode": "live"},
            "overview": {},
            "outputs": [],
            "deliverables": [],
            "background_run": {"kind": "create", "status": "queued", "job_id": "job-backfill"},
            "live_state": {},
        },
    )
    monkeypatch.setattr(server, "_takyon_businesses_for_session", lambda *_args, **_kwargs: [])

    response = server._methods["takyon.dashboard.create"](
        "dashboard-create-backfill-1",
        {
            "session_id": sid,
            "business": "latexflow",
            "business_name": "Latexflow",
            "goal": "Overleaf competitor",
            "mode": "live",
        },
    )

    result = response["result"]
    assert result["job_id"] == "job-backfill"
    assert result["lifecycle_state"] == "queued"
    assert captured["enqueue"] == {
        "operator_user_id": "user-1",
        "slug": "latexflow",
        "goal": "Overleaf competitor",
        "mode": "live",
        "schedule": None,
        "max_turns": 30,
    }
    assert "--no-auto" not in captured["argv"]
    assert server._sessions[sid]["running"] is False


def test_takyon_dashboard_create_with_agent_enqueues_durable_job(server, monkeypatch):
    # BUG-004: even on a streaming-capable session (a live agent), the dashboard create must enqueue the
    # DURABLE ceo_bootstrap worker job and surface it as queued — NOT run the CEO turn in-process via
    # _start_streaming_session_turn (which a dashboard restart would kill, and which ran in the chat
    # session's agent rather than the worker's fresh isolated agent). It must also release the busy
    # guard it took, so the chat session is not left wedged "busy".
    sid = "takyon-session"
    server._sessions[sid] = {
        "takyon_current_business": "",
        "takyon_operator_user_id": "user-1",
        "agent_ready": threading.Event(),
        "history_lock": threading.Lock(),
        "session_key": "sess-1",
    }
    captured: dict[str, object] = {}

    fake_cli = types.ModuleType("plugins.takyon.cli")

    class FakeStore:
        def __init__(self, *args, **kwargs):
            self._operator_user_id = kwargs.get("operator_user_id")

        def commit(self, *args, **kwargs):
            return None

    def fake_resolve_dashboard_create_identity(name, goal, slug_hint="", *, operator_user_id=None, store=None):
        assert name == "Latexflow"
        assert goal == "Overleaf competitor"
        assert slug_hint == "latexflow"
        assert operator_user_id == "user-1"
        return "Latexflow", "latexflow"

    def fake_run_takyon_command(argv, *_args, **_kwargs):
        captured["argv"] = list(argv)
        return {
            "success": True,
            "business": "latexflow",
            "mode": "live",
            "bootstrap_job": {"job_id": "job-777", "kind": "ceo_bootstrap", "status": "queued"},
        }

    fake_cli.TakyonStore = FakeStore
    fake_cli._resolve_dashboard_create_identity = fake_resolve_dashboard_create_identity
    fake_cli.run_takyon_command = fake_run_takyon_command
    # §3 gap #2 preflight: default the fake to a funded operator (no-op) so existing create-path
    # tests exercise the happy path; the dedicated balance-block test overrides this to raise.
    fake_cli._operator_create_balance_preflight = lambda *_a, **_k: None
    monkeypatch.setitem(sys.modules, "plugins.takyon.cli", fake_cli)
    monkeypatch.setattr(server, "_takyon_unique_business_slug", lambda *_args, **_kwargs: "latexflow")
    monkeypatch.setattr(
        server,
        "_takyon_require_durable_business",
        lambda *_args, **_kwargs: {"business": {"slug": "latexflow", "name": "Latexflow", "mode": "live"}},
    )
    monkeypatch.setattr(
        server,
        "_takyon_workspace_payload",
        lambda *_args, **_kwargs: {
            "business_slug": "latexflow",
            "current": {"slug": "latexflow", "name": "Latexflow", "mode": "live"},
            "overview": {"goal": "Overleaf competitor"},
            "outputs": [],
            "deliverables": [],
            "background_run": {"kind": "create", "status": "queued"},
            "live_state": {},
        },
    )
    monkeypatch.setattr(
        server,
        "_takyon_businesses_for_session",
        lambda *_args, **_kwargs: [{"slug": "latexflow", "name": "Latexflow"}],
    )
    # The bootstrap turn must NOT run in-process on the dashboard gateway anymore — it runs in the worker.
    monkeypatch.setattr(
        server,
        "_start_streaming_session_turn",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("dashboard bootstrap must enqueue the durable worker job, not stream in-process")
        ),
    )

    response = server._methods["takyon.dashboard.create"](
        "dashboard-create-durable-1",
        {
            "session_id": sid,
            "business": "latexflow",
            "business_name": "Latexflow",
            "goal": "Overleaf competitor",
            "mode": "live",
        },
    )

    result = response["result"]
    # The durable ceo_bootstrap job is surfaced as queued (not an in-process stream).
    assert result["job_id"] == "job-777"
    assert result["job_kind"] == "ceo_bootstrap"
    assert result["job_status"] == "queued"
    assert result["lifecycle_state"] == "queued"
    assert "streaming" not in result
    # The create subprocess must NOT suppress the durable enqueue on a real "Start building" click.
    assert "--no-auto" not in captured["argv"]
    # The busy guard taken for the streaming-capable session is released (chat not left wedged).
    assert server._sessions[sid]["running"] is False


def test_takyon_dashboard_workspace_uses_explicit_business_slug(server, monkeypatch):
    sid = "takyon-session"
    server._sessions[sid] = {"takyon_current_business": "other-biz"}
    seen: dict[str, str] = {}

    def fake_require_access(session, business):
        seen["business"] = business
        return None

    monkeypatch.setattr(server, "_takyon_require_business_access", fake_require_access)
    monkeypatch.setattr(
        server,
        "_takyon_workspace_payload",
        lambda *_args, **_kwargs: {
            "business_slug": "latexflow",
            "current": {"slug": "latexflow"},
            "overview": {"goal": "Overleaf competitor"},
            "outputs": [],
            "background_run": None,
        },
    )

    response = server._methods["takyon.dashboard.workspace"](
        "workspace-1",
        {"session_id": sid, "business_slug": "latexflow"},
    )

    assert seen["business"] == "latexflow"
    assert response["result"]["business_slug"] == "latexflow"


def test_takyon_dashboard_state_sets_explicit_business(server, monkeypatch):
    sid = "takyon-session"
    server._sessions[sid] = {"takyon_current_business": ""}

    monkeypatch.setattr(
        server,
        "_takyon_dashboard_state_payload",
        lambda session, **_kwargs: {
            "scope": "business:latexflow",
            "business": "latexflow",
            "business_slug": "latexflow",
            "businesses": [{"slug": "latexflow"}],
            "current": {"slug": "latexflow"},
            "overview": {"goal": "Overleaf competitor"},
            "outputs": [],
            "background_run": None,
            "auto_switched_business": "",
            "auto_scope_warning": "",
        },
    )

    response = server._methods["takyon.dashboard.state"](
        "dashboard-state-1",
        {"session_id": sid, "business_slug": "latexflow"},
    )

    assert response["result"]["business_slug"] == "latexflow"
    assert response["result"]["scope"] == "business:latexflow"


def test_takyon_dashboard_runtime_tails_incrementally(server, monkeypatch):
    sid = "takyon-session"
    server._sessions[sid] = {"takyon_current_business": "demo"}

    class FakeStore:
        def __init__(self):
            self.conn = sqlite3.connect(":memory:")
            self.conn.row_factory = sqlite3.Row
            self.conn.executescript(
                """
                CREATE TABLE events (
                  id TEXT,
                  business_slug TEXT,
                  event_type TEXT,
                  payload_json TEXT,
                  created_at TEXT
                );
                """
            )
            self.conn.execute(
                """
                INSERT INTO events (id, business_slug, event_type, payload_json, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    "evt-1",
                    "demo",
                    "dashboard.run.started",
                    json.dumps(
                        {
                            "kind": "ceo_bootstrap",
                            "status": "started",
                            "detail": "CEO bootstrap is running.",
                            "trace": {
                                "entry_key": "turn:ceo_bootstrap",
                                "kind": "turn",
                                "label": "CEO bootstrap",
                                "detail": "CEO bootstrap is running.",
                                "status": "running",
                            },
                        }
                    ),
                    "2026-06-05T19:00:00Z",
                ),
            )
            self.conn.execute(
                """
                INSERT INTO events (id, business_slug, event_type, payload_json, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    "evt-2",
                    "demo",
                    "dashboard.run.output",
                    json.dumps(
                        {
                            "kind": "ceo_bootstrap",
                            "status": "output",
                            "detail": "tool started -> business_claude_agent_task · product/site",
                            "line": "tool started -> business_claude_agent_task · product/site",
                            "command": "/create demo",
                        }
                    ),
                    "2026-06-05T19:00:02Z",
                ),
            )
            self.conn.execute(
                """
                INSERT INTO events (id, business_slug, event_type, payload_json, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    "evt-3",
                    "demo",
                    "dashboard.run.completed",
                    json.dumps(
                        {
                            "kind": "ceo_bootstrap",
                            "status": "completed",
                            "detail": "CEO bootstrap completed.",
                        }
                    ),
                    "2026-06-05T19:00:05Z",
                ),
            )
            self.conn.commit()

        @contextmanager
        def _connect(self):
            yield self.conn

        def _row_to_dict(self, row):
            data = dict(row)
            payload_raw = data.pop("payload_json", "")
            data["payload"] = json.loads(payload_raw) if payload_raw else {}
            return data

    store = FakeStore()
    monkeypatch.setattr(server, "_takyon_store", lambda _session: store)
    monkeypatch.setattr(server, "_takyon_require_business_access", lambda *_args, **_kwargs: None)

    initial = server._methods["takyon.dashboard.runtime"](
        "runtime-1",
        {"session_id": sid, "business_slug": "demo", "limit": 8},
    )

    initial_result = initial["result"]
    assert [item["id"] for item in initial_result["events"]] == ["evt-1", "evt-2", "evt-3"]
    assert initial_result["events"][0]["trace"]["entry_key"] == "turn:ceo_bootstrap"
    assert initial_result["cursor"] == "2026-06-05T19:00:05Z::evt-3"

    store.conn.execute(
        """
        INSERT INTO events (id, business_slug, event_type, payload_json, created_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            "evt-4",
            "demo",
            "dashboard.run.output",
            json.dumps(
                {
                    "kind": "ceo_bootstrap",
                    "status": "output",
                    "detail": "product surface -> published https://demo.fourmanifold.com/",
                }
            ),
            "2026-06-05T19:00:06Z",
        ),
    )
    store.conn.commit()

    incremental = server._methods["takyon.dashboard.runtime"](
        "runtime-2",
        {
            "session_id": sid,
            "business_slug": "demo",
            "after": initial_result["cursor"],
            "limit": 8,
        },
    )

    incremental_result = incremental["result"]
    assert [item["id"] for item in incremental_result["events"]] == ["evt-4"]
    assert incremental_result["after"] == "2026-06-05T19:00:05Z::evt-3"
    assert incremental_result["cursor"] == "2026-06-05T19:00:06Z::evt-4"


def test_takyon_businesses_for_session_caches_reads(server, monkeypatch):
    session = {}

    class FakeStore:
        def __init__(self):
            self.calls = 0

        def read(self, **_kwargs):
            self.calls += 1
            return {"businesses": [{"slug": "latexflow", "name": "Latexflow"}]}

    store = FakeStore()
    monkeypatch.setattr(server, "_takyon_store", lambda *_args, **_kwargs: store)

    first = server._takyon_businesses_for_session(session, store=store)
    second = server._takyon_businesses_for_session(session, store=store)

    assert first == second == [{"slug": "latexflow", "name": "Latexflow"}]
    assert store.calls == 1


def test_takyon_can_access_business_falls_back_to_owner_row(server, monkeypatch):
    session = {
        "takyon_operator_user_id": "user-123",
        "takyon_businesses_cache": {
            "at": time.monotonic(),
            "items": [{"slug": "stale-old"}],
        },
    }

    class FakeResult:
        def fetchone(self):
            return {"exists": 1}

    class FakeConn:
        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

        def execute(self, sql, params=()):
            assert "FROM businesses" in sql
            assert params == ("fresh-new", "user-123")
            return FakeResult()

    class FakeStore:
        def _connect(self):
            return FakeConn()

    monkeypatch.setattr(server, "_takyon_store", lambda *_args, **_kwargs: FakeStore())

    assert server._takyon_can_access_business(session, "fresh-new")
    assert "takyon_businesses_cache" not in session


def test_takyon_dashboard_state_payload_reuses_prefetched_businesses(server, monkeypatch):
    session = {"takyon_current_business": "latexflow"}

    class FakeStore:
        def __init__(self):
            self.calls: list[tuple[str, str]] = []

        def read(self, *, scope="global", query="summary", **_kwargs):
            self.calls.append((scope, query))
            if scope == "global" and query == "list_businesses":
                return {"businesses": [{"slug": "latexflow", "name": "Latexflow"}]}
            raise AssertionError(f"unexpected store.read({scope=}, {query=})")

    store = FakeStore()
    monkeypatch.setattr(server, "_takyon_store", lambda *_args, **_kwargs: store)
    monkeypatch.setattr(
        server,
        "_takyon_workspace_payload",
        lambda *_args, **_kwargs: {
            "business_slug": "latexflow",
            "current": {"slug": "latexflow"},
            "overview": {"goal": "Overleaf competitor"},
            "outputs": [],
            "background_run": None,
        },
    )

    result = server._takyon_dashboard_state_payload(
        session,
        explicit_business=True,
        business="latexflow",
    )

    assert result["business_slug"] == "latexflow"
    assert store.calls == [("global", "list_businesses")]


# ── write_json ───────────────────────────────────────────────────────


def test_write_json(capture):
    server, buf = capture
    assert server.write_json({"test": True})
    assert json.loads(buf.getvalue()) == {"test": True}


def test_write_json_broken_pipe(server):
    class _Broken:
        def write(self, _): raise BrokenPipeError
        def flush(self): raise BrokenPipeError

    server._real_stdout = _Broken()
    assert server.write_json({"x": 1}) is False


def test_write_json_closed_stream_returns_false(server):
    """ValueError ('I/O on closed file') used to bubble up; treat as gone."""

    class _Closed:
        def write(self, _): raise ValueError("I/O operation on closed file")
        def flush(self): raise ValueError("I/O operation on closed file")

    server._real_stdout = _Closed()
    assert server.write_json({"x": 1}) is False


def test_write_json_unicode_encode_error_re_raises(server):
    """A non-UTF-8 stdout encoding raises UnicodeEncodeError (a ValueError
    subclass).  It must NOT be swallowed as 'peer gone' — that would let
    `entry.py` exit cleanly via the False path and hide the real config
    bug.  We re-raise so the existing crash-log infrastructure records it."""

    class _AsciiOnly:
        def write(self, line):
            line.encode("ascii")  # raises UnicodeEncodeError on non-ascii
        def flush(self): pass

    server._real_stdout = _AsciiOnly()
    with pytest.raises(UnicodeEncodeError):
        server.write_json({"msg": "héllo"})


def test_write_json_unrelated_value_error_re_raises(server):
    """Only ValueError('...closed file...') means peer gone.  Other
    ValueErrors are programming errors and must surface."""

    class _BadValue:
        def write(self, _): raise ValueError("something else entirely")
        def flush(self): pass

    server._real_stdout = _BadValue()
    with pytest.raises(ValueError, match="something else entirely"):
        server.write_json({"x": 1})


def test_write_json_non_serializable_payload_re_raises(server):
    """Non-JSON-safe payloads are programming errors — they must NOT be
    silently dropped via the False path (which would trigger a clean exit
    in entry.py and mask the real bug)."""
    import io

    server._real_stdout = io.StringIO()
    with pytest.raises(TypeError):
        server.write_json({"obj": object()})


def test_write_json_peer_gone_oserror_on_flush_returns_false(server):
    """A flush that raises a peer-gone OSError (EPIPE) must not strand
    the lock or crash; it returns False so the dispatcher exits cleanly."""
    import errno

    written = []

    class _FlushPeerGone:
        def write(self, line): written.append(line)
        def flush(self): raise OSError(errno.EPIPE, "broken pipe")

    server._real_stdout = _FlushPeerGone()
    assert server.write_json({"x": 1}) is False
    assert written and json.loads(written[0]) == {"x": 1}


def test_write_json_non_peer_gone_oserror_re_raises(server):
    """Host I/O failures (ENOSPC, EACCES, EIO …) are NOT peer-gone — they
    must re-raise so the crash log records them instead of looking like
    a clean disconnect via the False path."""
    import errno

    class _DiskFull:
        def write(self, _): raise OSError(errno.ENOSPC, "no space left")
        def flush(self): pass

    server._real_stdout = _DiskFull()
    with pytest.raises(OSError, match="no space"):
        server.write_json({"x": 1})


def test_write_json_skips_flush_when_disable_flush_true(monkeypatch):
    """`StdioTransport` skips flush when `_DISABLE_FLUSH` is true.

    Tests the runtime *behaviour* via direct module-attr patch.  The env
    var → module constant wiring is covered by the dedicated env test
    below; reloading server.py here would re-register atexit hooks and
    recreate the worker pool.
    """
    import importlib

    transport_mod = importlib.import_module("tui_gateway.transport")
    monkeypatch.setattr(transport_mod, "_DISABLE_FLUSH", True)

    flushed = {"count": 0}
    written = []

    class _Stream:
        def write(self, line): written.append(line)
        def flush(self): flushed["count"] += 1

    stream = _Stream()
    transport = transport_mod.StdioTransport(lambda: stream, threading.Lock())

    assert transport.write({"x": 1}) is True
    assert flushed["count"] == 0


def test_disable_flush_env_var_actually_wires_to_module_constant(monkeypatch):
    """End-to-end: setting `TAKYON_TUI_GATEWAY_NO_FLUSH=1` and importing
    `tui_gateway.transport` fresh actually flips `_DISABLE_FLUSH` true.

    Reloads only the transport module — server.py is untouched so its
    atexit hooks/worker pool stay intact."""
    import importlib

    monkeypatch.setenv("TAKYON_TUI_GATEWAY_NO_FLUSH", "1")
    transport_mod = importlib.reload(importlib.import_module("tui_gateway.transport"))

    try:
        assert transport_mod._DISABLE_FLUSH is True
    finally:
        # Restore the env-disabled state so other tests see the default.
        monkeypatch.delenv("TAKYON_TUI_GATEWAY_NO_FLUSH", raising=False)
        importlib.reload(transport_mod)


# ── _emit ────────────────────────────────────────────────────────────


def test_emit_with_payload(capture):
    server, buf = capture
    server._emit("test.event", "s1", {"key": "val"})
    msg = json.loads(buf.getvalue())

    assert msg["method"] == "event"
    assert msg["params"]["type"] == "test.event"
    assert msg["params"]["session_id"] == "s1"
    assert msg["params"]["payload"]["key"] == "val"


def test_emit_without_payload(capture):
    server, buf = capture
    server._emit("ping", "s2")

    assert "payload" not in json.loads(buf.getvalue())["params"]


# ── Blocking prompt round-trip ───────────────────────────────────────


def test_block_and_respond(capture):
    server, _ = capture
    result = [None]

    threading.Thread(
        target=lambda: result.__setitem__(0, server._block("test.prompt", "s1", {"q": "?"}, timeout=5)),
    ).start()

    for _ in range(100):
        if server._pending:
            break
        threading.Event().wait(0.01)

    rid = next(iter(server._pending))
    server._answers[rid] = "my_answer"
    # _pending values are (sid, Event) tuples — unpack to set the Event
    _, ev = server._pending[rid]
    ev.set()

    threading.Event().wait(0.1)
    assert result[0] == "my_answer"


def test_clear_pending(server):
    ev = threading.Event()
    # _pending values are (sid, Event) tuples
    server._pending["r1"] = ("sid-x", ev)
    server._clear_pending()

    assert ev.is_set()
    assert server._answers["r1"] == ""


# ── Session lookup ───────────────────────────────────────────────────


def test_sess_missing(server):
    _, err = server._sess({"session_id": "nope"}, "r1")
    assert err["error"]["code"] == 4001


def test_sess_found(server):
    server._sessions["abc"] = {"agent": MagicMock()}
    s, err = server._sess({"session_id": "abc"}, "r1")

    assert s is not None
    assert err is None


# ── session.resume payload ────────────────────────────────────────────


def test_session_resume_returns_hydrated_messages(server, monkeypatch):
    class _DB:
        def get_session(self, _sid):
            return {"id": "20260409_010101_abc123"}

        def get_session_by_title(self, _title):
            return None

        def reopen_session(self, _sid):
            return None

        def get_messages_as_conversation(self, _sid, include_ancestors=False):
            return [
                {"role": "user", "content": "hello"},
                {"role": "assistant", "content": "yo"},
                {"role": "tool", "content": "searched"},
                {"role": "assistant", "content": "   "},
                {"role": "assistant", "content": None},
                {"role": "narrator", "content": "skip"},
            ]

    monkeypatch.setattr(server, "_get_db", lambda: _DB())
    monkeypatch.setattr(server, "_make_agent", lambda sid, key, session_id=None: object())
    monkeypatch.setattr(
        server,
        "_init_session",
        lambda sid, key, agent, history, cols=80, operator_user_id="": None,
    )
    monkeypatch.setattr(server, "_session_info", lambda _agent: {"model": "test/model"})

    resp = server.handle_request(
        {
            "id": "r1",
            "method": "session.resume",
            "params": {"session_id": "20260409_010101_abc123", "cols": 100},
        }
    )

    assert "error" not in resp
    assert resp["result"]["message_count"] == 3
    assert resp["result"]["messages"] == [
        {"role": "user", "text": "hello"},
        {"role": "assistant", "text": "yo"},
        {"role": "tool", "name": "tool", "context": ""},
    ]


# ── Config I/O ───────────────────────────────────────────────────────


def test_config_load_missing(server, tmp_path):
    server._takyon_home = tmp_path
    assert server._load_cfg() == {}


def test_config_roundtrip(server, tmp_path):
    server._takyon_home = tmp_path
    server._save_cfg({"model": "test/model"})
    assert server._load_cfg()["model"] == "test/model"


# ── _cli_exec_blocked ────────────────────────────────────────────────


@pytest.mark.parametrize("argv", [
    [],
    ["setup"],
    ["gateway"],
    ["sessions", "browse"],
    ["config", "edit"],
])
def test_cli_exec_blocked(server, argv):
    assert server._cli_exec_blocked(argv) is not None


@pytest.mark.parametrize("argv", [
    ["version"],
    ["sessions", "list"],
])
def test_cli_exec_allowed(server, argv):
    assert server._cli_exec_blocked(argv) is None


# ── slash.exec skill command interception ────────────────────────────


def test_slash_exec_rejects_skill_commands(server):
    """slash.exec must reject skill commands so the TUI falls through to command.dispatch."""
    # Register a mock session
    sid = "test-session"
    server._sessions[sid] = {"session_key": sid, "agent": None}

    # Mock scan_skill_commands to return a known skill
    fake_skills = {"/takyon-agent-dev": {"name": "takyon-agent-dev", "description": "Dev workflow"}}

    with patch("agent.skill_commands.get_skill_commands", return_value=fake_skills):
        resp = server.handle_request({
            "id": "r1",
            "method": "slash.exec",
            "params": {"command": "takyon-agent-dev", "session_id": sid},
        })

    # Should return an error so the TUI's .catch() fires command.dispatch
    assert "error" in resp
    assert resp["error"]["code"] == 4018
    assert "skill command" in resp["error"]["message"]


def test_slash_exec_handles_plugin_commands_in_live_gateway(server):
    """Plugin slash commands return normal slash.exec output without using the worker."""
    sid = "test-session"

    class Worker:
        def __init__(self):
            self.calls = []

        def run(self, cmd):
            self.calls.append(cmd)
            return f"worker:{cmd}"

    worker = Worker()
    server._sessions[sid] = {"session_key": sid, "agent": None, "slash_worker": worker}

    with patch(
        "takyon_cli.plugins.get_plugin_command_handler",
        lambda name: (lambda arg: f"plugin:{arg}") if name == "plugin-cmd" else None,
    ):
        resp = server.handle_request({
            "id": "r-plugin-slash",
            "method": "slash.exec",
            "params": {"command": "plugin-cmd hello", "session_id": sid},
        })

    assert "error" not in resp
    assert resp["result"] == {"output": "plugin:hello"}
    assert worker.calls == []


def test_slash_exec_plugin_lookup_failure_falls_back_to_worker(server):
    """Plugin discovery failures must not break ordinary slash-worker commands."""
    sid = "test-session"

    class Worker:
        def __init__(self):
            self.calls = []

        def run(self, cmd):
            self.calls.append(cmd)
            return f"worker:{cmd}"

    worker = Worker()
    server._sessions[sid] = {"session_key": sid, "agent": None, "slash_worker": worker}

    with patch(
        "takyon_cli.plugins.get_plugin_command_handler",
        side_effect=RuntimeError("discovery boom"),
    ):
        resp = server.handle_request({
            "id": "r-plugin-lookup-failure",
            "method": "slash.exec",
            "params": {"command": "help", "session_id": sid},
        })

    assert "error" not in resp
    assert resp["result"] == {"output": "worker:help"}
    assert worker.calls == ["help"]


def test_slash_exec_plugin_handler_error_returns_output(server):
    """Plugin handler failures return slash output so the TUI does not redispatch."""
    sid = "test-session"

    class Worker:
        def __init__(self):
            self.calls = []

        def run(self, cmd):
            self.calls.append(cmd)
            return f"worker:{cmd}"

    def handler(arg):
        raise RuntimeError(f"handler boom: {arg}")

    worker = Worker()
    server._sessions[sid] = {"session_key": sid, "agent": None, "slash_worker": worker}

    with patch(
        "takyon_cli.plugins.get_plugin_command_handler",
        lambda name: handler if name == "plugin-cmd" else None,
    ):
        resp = server.handle_request({
            "id": "r-plugin-handler-error",
            "method": "slash.exec",
            "params": {"command": "plugin-cmd hello", "session_id": sid},
        })

    assert "error" not in resp
    assert resp["result"] == {"output": "Plugin command error: handler boom: hello"}
    assert worker.calls == []


@pytest.mark.parametrize("cmd", ["retry", "queue hello", "q hello", "steer fix the test", "plan"])
def test_slash_exec_rejects_pending_input_commands(server, cmd):
    """slash.exec must reject commands that use _pending_input in the CLI."""
    sid = "test-session"
    server._sessions[sid] = {"session_key": sid, "agent": None}

    resp = server.handle_request({
        "id": "r1",
        "method": "slash.exec",
        "params": {"command": cmd, "session_id": sid},
    })

    assert "error" in resp
    assert resp["error"]["code"] == 4018
    assert "pending-input command" in resp["error"]["message"]


def test_command_dispatch_queue_sends_message(server):
    """command.dispatch /queue returns {type: 'send', message: ...} for the TUI."""
    sid = "test-session"
    server._sessions[sid] = {"session_key": sid}

    resp = server.handle_request({
        "id": "r1",
        "method": "command.dispatch",
        "params": {"name": "queue", "arg": "tell me about quantum computing", "session_id": sid},
    })

    assert "error" not in resp
    result = resp["result"]
    assert result["type"] == "send"
    assert result["message"] == "tell me about quantum computing"


def test_command_dispatch_queue_requires_arg(server):
    """command.dispatch /queue without an argument returns an error."""
    sid = "test-session"
    server._sessions[sid] = {"session_key": sid}

    resp = server.handle_request({
        "id": "r2",
        "method": "command.dispatch",
        "params": {"name": "queue", "arg": "", "session_id": sid},
    })

    assert "error" in resp
    assert resp["error"]["code"] == 4004


def test_skills_manage_search_uses_tools_hub_sources(server):
    result = type("Result", (), {
        "description": "Build better terminal demos",
        "name": "showroom",
    })()
    auth = MagicMock(return_value="auth")
    router = MagicMock(return_value=["source"])
    search = MagicMock(return_value=[result])
    fake_hub = types.SimpleNamespace(
        GitHubAuth=auth,
        create_source_router=router,
        unified_search=search,
    )

    with patch.dict(sys.modules, {"tools.skills_hub": fake_hub}):
        resp = server.handle_request({
            "id": "skills-search",
            "method": "skills.manage",
            "params": {"action": "search", "query": "showroom"},
        })

    assert "error" not in resp
    assert resp["result"] == {
        "results": [{"description": "Build better terminal demos", "name": "showroom"}]
    }
    auth.assert_called_once_with()
    router.assert_called_once_with("auth")
    search.assert_called_once_with("showroom", ["source"], source_filter="all", limit=20)


def test_command_dispatch_steer_fallback_sends_message(server):
    """command.dispatch /steer with no active agent falls back to send."""
    sid = "test-session"
    server._sessions[sid] = {"session_key": sid, "agent": None}

    resp = server.handle_request({
        "id": "r3",
        "method": "command.dispatch",
        "params": {"name": "steer", "arg": "focus on testing", "session_id": sid},
    })

    assert "error" not in resp
    result = resp["result"]
    assert result["type"] == "send"
    assert result["message"] == "focus on testing"


def test_command_dispatch_retry_finds_last_user_message(server):
    """command.dispatch /retry walks session['history'] to find the last user message."""
    sid = "test-session"
    history = [
        {"role": "user", "content": "first question"},
        {"role": "assistant", "content": "first answer"},
        {"role": "user", "content": "second question"},
        {"role": "assistant", "content": "second answer"},
    ]
    server._sessions[sid] = {
        "session_key": sid,
        "agent": None,
        "history": history,
        "history_lock": threading.Lock(),
        "history_version": 0,
    }

    resp = server.handle_request({
        "id": "r4",
        "method": "command.dispatch",
        "params": {"name": "retry", "session_id": sid},
    })

    assert "error" not in resp
    result = resp["result"]
    assert result["type"] == "send"
    assert result["message"] == "second question"
    # Verify history was truncated: everything from last user message onward removed
    assert len(server._sessions[sid]["history"]) == 2
    assert server._sessions[sid]["history"][-1]["role"] == "assistant"
    assert server._sessions[sid]["history_version"] == 1


def test_command_dispatch_retry_empty_history(server):
    """command.dispatch /retry with empty history returns error."""
    sid = "test-session"
    server._sessions[sid] = {
        "session_key": sid,
        "agent": None,
        "history": [],
        "history_lock": threading.Lock(),
        "history_version": 0,
    }

    resp = server.handle_request({
        "id": "r5",
        "method": "command.dispatch",
        "params": {"name": "retry", "session_id": sid},
    })

    assert "error" in resp
    assert resp["error"]["code"] == 4018


def test_command_dispatch_retry_handles_multipart_content(server):
    """command.dispatch /retry extracts text from multipart content lists."""
    sid = "test-session"
    history = [
        {"role": "user", "content": [
            {"type": "text", "text": "analyze this"},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,..."}}
        ]},
        {"role": "assistant", "content": "I see the image."},
    ]
    server._sessions[sid] = {
        "session_key": sid,
        "agent": None,
        "history": history,
        "history_lock": threading.Lock(),
        "history_version": 0,
    }

    resp = server.handle_request({
        "id": "r6",
        "method": "command.dispatch",
        "params": {"name": "retry", "session_id": sid},
    })

    assert "error" not in resp
    result = resp["result"]
    assert result["type"] == "send"
    assert result["message"] == "analyze this"


def test_command_dispatch_returns_skill_payload(server):
    """command.dispatch returns structured skill payload for the TUI to send()."""
    sid = "test-session"
    server._sessions[sid] = {"session_key": sid}

    fake_skills = {"/takyon-agent-dev": {"name": "takyon-agent-dev", "description": "Dev workflow"}}
    fake_msg = "Loaded skill content here"

    with patch("agent.skill_commands.scan_skill_commands", return_value=fake_skills), \
         patch("agent.skill_commands.build_skill_invocation_message", return_value=fake_msg):
        resp = server.handle_request({
            "id": "r2",
            "method": "command.dispatch",
            "params": {"name": "takyon-agent-dev", "session_id": sid},
        })

    assert "error" not in resp
    result = resp["result"]
    assert result["type"] == "skill"
    assert result["message"] == fake_msg
    assert result["name"] == "takyon-agent-dev"


def test_command_dispatch_awaits_async_plugin_handler(server):
    async def _handler(arg):
        return f"async:{arg}"

    with patch(
        "takyon_cli.plugins.get_plugin_command_handler",
        lambda name: _handler if name == "async-cmd" else None,
    ):
        resp = server.handle_request({
            "id": "r-plugin",
            "method": "command.dispatch",
            "params": {"name": "async-cmd", "arg": "hello"},
        })

    assert "error" not in resp
    assert resp["result"] == {"type": "plugin", "output": "async:hello"}


# ── dispatch(): pool routing for long handlers (#12546) ──────────────


def test_dispatch_runs_short_handlers_inline(server):
    """Non-long handlers return their response synchronously from dispatch()."""
    server._methods["fast.ping"] = lambda rid, params: server._ok(rid, {"pong": True})

    resp = server.dispatch({"id": "r1", "method": "fast.ping", "params": {}})

    assert resp == {"jsonrpc": "2.0", "id": "r1", "result": {"pong": True}}


def test_dispatch_offloads_long_handlers_and_emits_via_stdout(capture):
    """Long handlers run on the pool and write their response via write_json."""
    server, buf = capture
    server._methods["slash.exec"] = lambda rid, params: server._ok(rid, {"output": "hi"})

    resp = server.dispatch({"id": "r2", "method": "slash.exec", "params": {}})
    assert resp is None

    for _ in range(50):
        if buf.getvalue():
            break
        time.sleep(0.01)

    written = json.loads(buf.getvalue())
    assert written == {"jsonrpc": "2.0", "id": "r2", "result": {"output": "hi"}}


def test_dispatch_long_handler_does_not_block_fast_handler(server):
    """A slow long handler must not prevent a concurrent fast handler from completing."""
    released = threading.Event()
    server._methods["slash.exec"] = lambda rid, params: (released.wait(timeout=5), server._ok(rid, {"done": True}))[1]
    server._methods["fast.ping"] = lambda rid, params: server._ok(rid, {"pong": True})

    t0 = time.monotonic()
    assert server.dispatch({"id": "slow", "method": "slash.exec", "params": {}}) is None

    fast_resp = server.dispatch({"id": "fast", "method": "fast.ping", "params": {}})
    fast_elapsed = time.monotonic() - t0

    assert fast_resp["result"] == {"pong": True}
    assert fast_elapsed < 0.5, f"fast handler blocked for {fast_elapsed:.2f}s behind slow handler"

    released.set()


def test_dispatch_session_compress_does_not_block_fast_handler(server):
    """Manual TUI compaction can take minutes, so it must not block the RPC loop."""
    released = threading.Event()

    def slow_compress(rid, params):
        released.wait(timeout=5)
        return server._ok(rid, {"done": True})

    server._methods["session.compress"] = slow_compress
    server._methods["fast.ping"] = lambda rid, params: server._ok(rid, {"pong": True})

    t0 = time.monotonic()
    assert server.dispatch({"id": "slow", "method": "session.compress", "params": {}}) is None

    fast_resp = server.dispatch({"id": "fast", "method": "fast.ping", "params": {}})
    fast_elapsed = time.monotonic() - t0

    assert fast_resp["result"] == {"pong": True}
    assert fast_elapsed < 0.5, f"fast handler blocked for {fast_elapsed:.2f}s behind session.compress"

    released.set()


def test_dispatch_long_handler_exception_produces_error_response(capture):
    """An exception inside a pool-dispatched handler still yields a JSON-RPC error."""
    server, buf = capture

    def boom(rid, params):
        raise RuntimeError("kaboom")

    server._methods["slash.exec"] = boom

    server.dispatch({"id": "r3", "method": "slash.exec", "params": {}})

    for _ in range(50):
        if buf.getvalue():
            break
        time.sleep(0.01)

    written = json.loads(buf.getvalue())
    assert written["id"] == "r3"
    assert written["error"]["code"] == -32000
    assert "kaboom" in written["error"]["message"]


def test_dispatch_unknown_long_method_still_goes_inline(server):
    """Method name not in _LONG_HANDLERS takes the sync path even if handler is slow."""
    server._methods["some.method"] = lambda rid, params: server._ok(rid, {"ok": True})

    resp = server.dispatch({"id": "r4", "method": "some.method", "params": {}})

    assert resp["result"] == {"ok": True}
