import json
import os
import sqlite3

from plugins.takyon import cli, worker


class _FakeStore:
    pass


def test_read_business_progress_summarizes_snapshot():
    result = {
        "success": True,
        "business": {"slug": "homework-solver", "name": "Homework Solver"},
        "app": {
            "product_surface": {
                "publish_status": "published",
                "public_url": "https://homework-solver.coscale.app/",
            },
            "customers": [{"id": "u1"}],
            "entitlements": [{"status": "active", "tier": "paid"}],
            "revenue": {"amount_paid_cents": 1200},
            "usage_this_period": {"events": 3},
        },
        "jobs": [{"kind": "ceo_bootstrap", "status": "completed"}],
        "controls": [{"scope": "business:homework-solver", "state": "paused"}],
    }

    lines = cli._tool_progress_lines(
        "business_read_business",
        {"business": "homework-solver"},
        json.dumps(result),
    )

    assert "state -> Homework Solver (homework-solver)" in lines
    assert "product -> published https://homework-solver.coscale.app/" in lines
    assert "app -> users=1 paid=1 revenue=$12.00 usage_events=3" in lines
    assert "jobs -> queued=0 latest=ceo_bootstrap:completed" in lines
    assert "controls -> paused" in lines


def test_pulse_progress_summarizes_metrics_and_traffic():
    result = {
        "success": True,
        "summary": {
            "users": 2,
            "paid_customers": 1,
            "mrr_cents": 1200,
            "revenue_cents": 1200,
            "usage_events": 4,
            "queued_jobs": 1,
            "unresolved_inbound": 0,
        },
        "current_state": {
            "product_surface": {
                "publish_status": "published",
                "public_url": "https://homework-solver.coscale.app/",
            },
        },
        "web_analytics": {
            "configured": True,
            "ok": True,
            "window_days": 7,
            "stats": {
                "visitors": {"value": 10},
                "visits": {"value": 12},
                "pageviews": {"value": 25},
            },
        },
    }

    lines = cli._tool_progress_lines(
        "business_calculate_pulse",
        {"business": "homework-solver"},
        json.dumps(result),
    )

    assert (
        "pulse -> users=2 paid=1 mrr=$12.00/mo revenue=$12.00 "
        "usage_events=4 queued_jobs=1 unresolved=0"
    ) in lines
    assert "product -> published https://homework-solver.coscale.app/" in lines
    assert "traffic -> 7d visitors=10 visits=12 pageviews=25" in lines


def test_raw_hermes_events_print_tool_args_and_results():
    read_fd, write_fd = os.pipe()
    progress = cli._ShellProgress(False, raw_hermes=True)
    progress.fd = write_fd
    progress.raw_max_chars = 0

    try:
        progress.tool_started("call_1", "business_read_business", {"business": "homework-solver"})
        progress.tool_completed(
            "call_1",
            "business_read_business",
            {"business": "homework-solver"},
            '{"success":true,"business":{"slug":"homework-solver"}}',
        )
        os.close(write_fd)
        progress.fd = None
        output = os.read(read_fd, 65536).decode("utf-8")
    finally:
        progress.close()
        try:
            os.close(read_fd)
        except OSError:
            pass

    assert "hermes.raw tool_call" in output
    assert '"name": "business_read_business"' in output
    assert '"business": "homework-solver"' in output
    assert "hermes.raw tool_result" in output
    assert '"result": "{\\"success\\":true,\\"business\\":{\\"slug\\":\\"homework-solver\\"}}"' in output


def test_hermes_turn_prints_existing_interim_assistant_text_only_once():
    read_fd, write_fd = os.pipe()
    progress = cli._ShellProgress(False)
    progress.fd = write_fd

    try:
        progress.hermes_turn("I am checking the current business state.", already_streamed=False)
        progress.hermes_turn("This was already streamed.", already_streamed=True)
        os.close(write_fd)
        progress.fd = None
        output = os.read(read_fd, 65536).decode("utf-8")
    finally:
        progress.close()
        try:
            os.close(read_fd)
        except OSError:
            pass

    assert "— Hermes —" in output
    assert "I am checking the current business state." in output
    assert "This was already streamed." not in output


def test_stream_delta_uses_natural_text_writer(monkeypatch):
    read_fd, write_fd = os.pipe()
    progress = cli._ShellProgress(False)
    progress.fd = write_fd
    progress.typewriter_enabled = True
    progress.typewriter_cps = 12000
    progress.typewriter_chunk_chars = 3
    monkeypatch.setattr("time.sleep", lambda _seconds: None)

    try:
        progress.stream_delta("abcdef")
        progress.finish_stream()
        os.close(write_fd)
        progress.fd = None
        output = os.read(read_fd, 65536).decode("utf-8")
    finally:
        progress.close()
        try:
            os.close(read_fd)
        except OSError:
            pass

    assert output == "abcdef\n"


def test_runtime_progress_records_ceo_stream_deltas(monkeypatch):
    recorded = []

    def fake_record(slug, **kwargs):
        recorded.append({"slug": slug, **kwargs})

    monkeypatch.setattr(worker, "_record_runtime_event", fake_record)
    progress = worker._RuntimeProgress(slug="demo", kind="ceo_bootstrap", command="/create demo")

    progress.stream_delta("Hello ")
    progress.stream_delta("world")
    progress.finish_stream()

    delta_events = [item for item in recorded if item.get("extra", {}).get("stream") == "message_delta"]
    assert "".join(str(item.get("line") or "") for item in delta_events) == "Hello world"
    assert recorded[-1]["extra"]["stream"] == "message_flush"


def test_runtime_event_tail_prints_ceo_stream_only():
    class Store:
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

        def _connect(self):
            return self.conn

        def _row_to_dict(self, row):
            data = dict(row)
            payload = data.pop("payload_json", "")
            data["payload"] = json.loads(payload) if payload else {}
            return data

    store = Store()
    store.conn.execute(
        "INSERT INTO events VALUES (?, ?, ?, ?, ?)",
        (
            "evt-1",
            "demo",
            "dashboard.run.output",
            json.dumps({"stream": "message_delta", "line": "CEO text"}),
            "2026-06-28T12:00:00Z",
        ),
    )
    store.conn.execute(
        "INSERT INTO events VALUES (?, ?, ?, ?, ?)",
        (
            "evt-2",
            "demo",
            "dashboard.run.output",
            json.dumps({"line": "tool started -> business_read_business"}),
            "2026-06-28T12:00:01Z",
        ),
    )
    store.conn.execute(
        "INSERT INTO events VALUES (?, ?, ?, ?, ?)",
        (
            "evt-3",
            "demo",
            "dashboard.run.output",
            json.dumps({"stream": "message_flush"}),
            "2026-06-28T12:00:02Z",
        ),
    )
    store.conn.commit()

    read_fd, write_fd = os.pipe()
    tail = cli._RuntimeEventTail(store=store, enabled=True, business_filter="demo")
    tail._out = os.fdopen(write_fd, "w", buffering=1, encoding="utf-8")
    tail._scope = "demo"
    try:
        tail._drain_once()
        tail._out.close()
        output = os.read(read_fd, 65536).decode("utf-8")
    finally:
        try:
            os.close(read_fd)
        except OSError:
            pass

    assert "— CEO —" in output
    assert "CEO text" in output
    assert "tool started" not in output


def test_runtime_event_tail_stays_silent_in_global_scope(tmp_path):
    class Store:
        def __init__(self):
            self.root = tmp_path
            self.conn = sqlite3.connect(":memory:")
            self.conn.row_factory = sqlite3.Row
            self.conn.execute(
                """
                CREATE TABLE events (
                    id TEXT PRIMARY KEY,
                    business_slug TEXT,
                    event_type TEXT,
                    payload_json TEXT,
                    created_at TEXT
                )
                """
            )

        def _connect(self):
            return self.conn

        def _row_to_dict(self, row):
            data = dict(row)
            payload = data.pop("payload_json", "")
            data["payload"] = json.loads(payload) if payload else {}
            return data

    store = Store()
    store.conn.execute(
        "INSERT INTO events VALUES (?, ?, ?, ?, ?)",
        (
            "evt-1",
            "demo",
            "dashboard.run.output",
            json.dumps({"stream": "message_delta", "line": "CEO text"}),
            "2026-06-28T12:00:00Z",
        ),
    )
    store.conn.commit()

    read_fd, write_fd = os.pipe()
    tail = cli._RuntimeEventTail(store=store, enabled=True, business_filter=None)
    tail._out = os.fdopen(write_fd, "w", buffering=1, encoding="utf-8")
    tail._scope = ""
    try:
        tail._drain_once()
        tail._out.close()
        output = os.read(read_fd, 65536).decode("utf-8")
    finally:
        try:
            os.close(read_fd)
        except OSError:
            pass

    assert output == ""


def test_use_without_args_switches_to_global(monkeypatch):
    monkeypatch.setattr(cli, "_local_shell_help_answer", lambda *_args, **_kwargs: "")

    output, business = cli._handle_shell_line(
        "/use",
        current_business="homework-solver",
        store=_FakeStore(),
        model="",
        max_turns=1,
    )

    assert output == "Using global scope"
    assert business is None


def test_use_global_alias_switches_to_global(monkeypatch):
    monkeypatch.setattr(cli, "_local_shell_help_answer", lambda *_args, **_kwargs: "")

    output, business = cli._handle_shell_line(
        "/use global",
        current_business="homework-solver",
        store=_FakeStore(),
        model="",
        max_turns=1,
    )

    assert output == "Using global scope"
    assert business is None


def test_global_plain_text_is_rejected_without_running_agent(monkeypatch):
    monkeypatch.setattr(cli, "_local_shell_help_answer", lambda *_args, **_kwargs: "")

    def fail_run_agent(*_args, **_kwargs):  # noqa: ANN001
        raise AssertionError("_run_agent should not be called in global scope for plain text")

    monkeypatch.setattr(cli, "_run_agent", fail_run_agent)

    output, business = cli._handle_shell_line(
        "hello",
        current_business=None,
        store=_FakeStore(),
        model="",
        max_turns=1,
    )

    assert output == "Plain text is disabled in global scope. Use /commands, /create, or /use <business>."
    assert business is None


def test_startup_graphic_disables_plain_text_in_global_scope():
    rendered = cli._startup_graphic(None)

    assert "plain text" in rendered
    assert "disabled until /use <business>" in rendered
    assert "/create" in rendered
    assert "talks to this company CEO" not in rendered


def test_ceo_focus_reports_global_plain_text_disabled(monkeypatch):
    monkeypatch.setattr(cli, "_read_model_config", lambda _store: {})

    rendered = cli._format_ceo_focus(None, _FakeStore(), "")

    assert "Scope: global" in rendered
    assert "Plain text is disabled in global scope" in rendered


def test_shell_create_preserves_raw_brief_after_explicit_slug():
    argv = cli._shell_create_argv(
        "create",
        "homework-solver No-signup trial: don't block on an unfinished \"quote",
    )

    assert argv == [
        "create",
        "homework-solver",
        "--",
        'No-signup trial: don\'t block on an unfinished "quote',
    ]

    slug, raw_name, goal, *_rest = cli._parse_business_start_args(
        argv,
        usage="usage",
        auto_default=True,
    )

    assert slug == "homework-solver"
    assert raw_name == "homework-solver"
    assert goal == 'No-signup trial: don\'t block on an unfinished "quote'


def test_shell_create_goal_only_derives_business_from_pasted_brief():
    argv = cli._shell_create_argv(
        "create",
        "Build a mobile-first AI study app that doesn't fake billing",
    )

    assert argv == [
        "create",
        "--goal-only",
        "--",
        "Build a mobile-first AI study app that doesn't fake billing",
    ]

    slug, raw_name, goal, *_rest = cli._parse_business_start_args(
        argv,
        usage="usage",
        auto_default=True,
    )

    assert slug == "mobile-first-ai-study"
    assert raw_name == "Mobile-First AI Study"
    assert goal == "Build a mobile-first AI study app that doesn't fake billing"


def test_shell_create_accepts_slug_flag_for_raw_pasted_brief():
    argv = cli._shell_create_argv(
        "create",
        "--slug study-sprint Build a study app that doesn't require shell quotes",
    )

    assert argv == [
        "create",
        "--slug",
        "study-sprint",
        "--",
        "Build a study app that doesn't require shell quotes",
    ]

    slug, raw_name, goal, *_rest = cli._parse_business_start_args(
        argv,
        usage="usage",
        auto_default=True,
    )

    assert slug == "study-sprint"
    assert raw_name == "Study Sprint"
    assert goal == "Build a study app that doesn't require shell quotes"


def test_handle_shell_create_does_not_shlex_pasted_brief(monkeypatch):
    captured: dict[str, list[str]] = {}
    monkeypatch.setattr(cli, "_local_shell_help_answer", lambda *_args, **_kwargs: "")

    def fake_run_takyon_command(argv, **_kwargs):  # noqa: ANN001
        captured["argv"] = argv
        return {"success": True}

    monkeypatch.setattr(cli, "run_takyon_command", fake_run_takyon_command)

    output, business = cli._handle_shell_line(
        '/create homework-solver No-signup trial: don\'t block on an unfinished "quote',
        current_business=None,
        store=_FakeStore(),
        model="",
        max_turns=1,
    )

    assert business == "homework-solver"
    assert output == '{\n  "success": true\n}'
    assert captured["argv"] == [
        "create",
        "homework-solver",
        "--",
        'No-signup trial: don\'t block on an unfinished "quote',
    ]
