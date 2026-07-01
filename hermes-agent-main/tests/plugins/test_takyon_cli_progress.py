import contextlib
import io
import json
import os
import sqlite3
from types import SimpleNamespace

from plugins.takyon import cli, worker


class _FakeStore:
    pass


def test_business_exists_prefers_access_gate_over_summary_read():
    class _Store:
        def enforce_operator_business_access(self, slug):
            assert slug == "roomier"

        def read(self, **_kwargs):
            raise AssertionError("summary read should not be used for /use existence checks")

    assert cli._business_exists(_Store(), "roomier") is True


def test_business_exists_returns_false_when_access_gate_denies():
    class _Store:
        def enforce_operator_business_access(self, _slug):
            raise cli.TakyonError("business:roomier does not exist")

    assert cli._business_exists(_Store(), "roomier") is False


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


def test_shell_progress_surfaces_reasoning_summary():
    read_fd, write_fd = os.pipe()
    progress = cli._ShellProgress(False)
    progress.fd = write_fd

    try:
        progress.tool_progress(
            "reasoning.available",
            "_thinking",
            "Plan the landing brief before running the product surface tools.",
            None,
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

    assert "reasoning -> Plan the landing brief before running the product surface tools." in output


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


def test_runtime_progress_records_reasoning_summary(monkeypatch):
    recorded = []

    def fake_record(slug, **kwargs):
        recorded.append({"slug": slug, **kwargs})

    monkeypatch.setattr(worker, "_record_runtime_event", fake_record)
    progress = worker._RuntimeProgress(slug="demo", kind="ceo_bootstrap", command="/create demo")

    progress.tool_progress(
        "reasoning.available",
        "_thinking",
        "Plan the landing brief before running the product surface tools.",
        None,
    )

    lines = [item.get("line") for item in recorded if item.get("status") == "output"]
    assert "reasoning -> Plan the landing brief before running the product surface tools." in lines


def test_run_agent_wires_reasoning_config_and_callback(monkeypatch):
    captured: dict[str, object] = {}

    class FakeProgress:
        def __init__(self, enabled, *, raw_hermes=False):
            self.enabled = bool(enabled)
            self.streamed_chars = 0
            self.tool_progress_calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

        def tool_progress(self, *args, **kwargs):
            self.tool_progress_calls.append((args, kwargs))

        def tool_started(self, *_args, **_kwargs):
            pass

        def tool_generating(self, *_args, **_kwargs):
            pass

        def tool_completed(self, *_args, **_kwargs):
            pass

        def activity(self, *_args, **_kwargs):
            pass

        def start_thinking(self):
            pass

        def _stop_thinking(self):
            pass

        def close(self):
            pass

    class FakeStream:
        def __init__(self, *, progress, store, business_slug):
            captured["progress"] = progress

        def hermes_turn(self, *_args, **_kwargs):
            pass

        def stream_delta(self, *_args, **_kwargs):
            pass

        def finish_stream(self):
            pass

    class FakeAgent:
        def __init__(self):
            self.session_estimated_cost_usd = 0.0
            self.session_cost_status = "ok"
            self._memory_nudge_interval = 0
            self._skill_nudge_interval = 0
            self.activity_callback = None
            self.suppress_status_output = False

        def run_conversation(self, _prompt, stream_callback=None):
            captured["stream_callback"] = stream_callback
            callback = captured["agent_kwargs"]["reasoning_callback"]
            assert callback is not None
            callback("Plan the landing brief before running the product surface tools.")
            return {"final_response": "Done"}

    def fake_builder(*, runtime, model, operator_user_id, business_slug, agent_kwargs):
        captured["runtime"] = runtime
        captured["agent_kwargs"] = agent_kwargs
        return FakeAgent()

    monkeypatch.setattr(cli, "load_takyon_env", lambda: None)
    monkeypatch.setattr(cli, "_load_ceo_prompt", lambda: "ceo")
    monkeypatch.setattr(cli, "TakyonStore", lambda: _FakeStore())
    monkeypatch.setattr(cli, "_read_model_config", lambda _store: {"provider": "anthropic"})
    monkeypatch.setattr(cli, "_require_agent_model_config", lambda _cfg, model_override="": "claude-sonnet-5")
    monkeypatch.setattr(cli, "_config_bool", lambda value, default=False: default if value in {None, ""} else bool(value))
    monkeypatch.setattr(cli, "_resolved_operator_user_id", lambda _value=None: "")
    monkeypatch.setattr(cli, "_ShellProgress", FakeProgress)
    monkeypatch.setattr(cli, "_ShellRuntimeStream", FakeStream)
    monkeypatch.setattr(cli, "_business_workspace_execution_context", lambda *_args, **_kwargs: contextlib.nullcontext(None))
    monkeypatch.setattr(cli, "_silence_process_stdio", lambda: contextlib.nullcontext())
    monkeypatch.setattr(cli, "_AgentLogTail", lambda enabled=False: contextlib.nullcontext())
    monkeypatch.setattr(
        "takyon_cli.runtime_provider.resolve_runtime_provider",
        lambda requested=None, target_model=None: {"provider": "anthropic", "api_mode": "anthropic_messages"},
    )
    monkeypatch.setattr("plugins.takyon.operator_gateway.build_operator_gateway_agent", fake_builder)

    response, _meta = cli._run_agent_with_meta(
        "ship it",
        model="",
        max_turns=3,
        show_activity=False,
        show_indicator=True,
        current_business="demo",
    )

    assert response == "Done"
    assert captured["agent_kwargs"]["reasoning_config"] == {"enabled": True, "effort": "medium"}
    assert captured["progress"].tool_progress_calls == [
        (
            (
                "reasoning.available",
                "_thinking",
                "Plan the landing brief before running the product surface tools.",
                None,
            ),
            {},
        )
    ]


def test_worker_run_ceo_turn_wires_reasoning_config_and_callback(monkeypatch):
    captured: dict[str, object] = {}

    class FakeProgress:
        def __init__(self):
            self.tool_progress_calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

        def tool_progress(self, *args, **kwargs):
            self.tool_progress_calls.append((args, kwargs))

        def tool_started(self, *_args, **_kwargs):
            pass

        def tool_generating(self, *_args, **_kwargs):
            pass

        def tool_completed(self, *_args, **_kwargs):
            pass

        def activity(self, *_args, **_kwargs):
            pass

        def stream_delta(self, *_args, **_kwargs):
            pass

        def finish_stream(self):
            pass

    class FakeAgent:
        def __init__(self):
            self.session_estimated_cost_usd = 0.0
            self.session_cost_status = "ok"
            self._memory_nudge_interval = 0
            self._skill_nudge_interval = 0
            self.activity_callback = None
            self.suppress_status_output = False

        def run_conversation(self, _prompt, stream_callback=None):
            captured["stream_callback"] = stream_callback
            callback = captured["agent_kwargs"]["reasoning_callback"]
            assert callback is not None
            callback("Draft the pricing copy before the checkout pass.")
            return {"final_response": "Done", "completed": True}

    def fake_builder(*, runtime, model, operator_user_id, business_slug, agent_kwargs):
        captured["runtime"] = runtime
        captured["agent_kwargs"] = agent_kwargs
        return FakeAgent()

    monkeypatch.setattr(cli, "_read_model_config", lambda _store: {"provider": "anthropic"})
    monkeypatch.setattr(cli, "_require_agent_model_config", lambda _cfg, model_override="": "claude-sonnet-5")
    monkeypatch.setattr(worker, "_business_owner_user_id", lambda _slug: "user-1")
    monkeypatch.setattr(worker, "_record_ceo_turn_chat", lambda _slug, _text: None)
    monkeypatch.setattr("plugins.takyon.core.load_takyon_env", lambda: None)
    monkeypatch.setattr("plugins.takyon.core.TakyonStore", lambda: _FakeStore())
    monkeypatch.setattr(
        "takyon_cli.runtime_provider.resolve_runtime_provider",
        lambda requested=None, target_model=None: {"provider": "anthropic", "api_mode": "anthropic_messages"},
    )
    monkeypatch.setattr("plugins.takyon.operator_gateway.build_operator_gateway_agent", fake_builder)

    progress = FakeProgress()
    response, cost_usd, cost_status, turn_completed = worker._run_ceo_turn(
        slug="demo",
        system_prompt="ceo",
        user_prompt="ship it",
        toolsets=["takyon"],
        max_turns=3,
        inactivity_limit=0,
        progress=progress,
    )

    assert response == "Done"
    assert cost_usd == 0.0
    assert cost_status == "ok"
    assert turn_completed is True
    assert captured["agent_kwargs"]["reasoning_config"] == {"enabled": True, "effort": "medium"}
    assert progress.tool_progress_calls == [
        (
            (
                "reasoning.available",
                "_thinking",
                "Draft the pricing copy before the checkout pass.",
                None,
            ),
            {},
        )
    ]


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
    assert "tool started -> business_read_business" in output


def test_runtime_event_tail_prints_claude_worker_runtime_events():
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
            "dashboard.run.started",
            json.dumps(
                {
                    "kind": "claude_agent_sdk",
                    "status": "started",
                    "detail": "Claude worker started for product/site.",
                    "command": "Claude worker -> product/site",
                }
            ),
            "2026-06-28T12:00:00Z",
        ),
    )
    store.conn.execute(
        "INSERT INTO events VALUES (?, ?, ?, ?, ?)",
        (
            "evt-2",
            "demo",
            "dashboard.run.running",
            json.dumps(
                {
                    "kind": "task",
                    "status": "running",
                    "detail": "Generating the landing page.",
                    "line": "Generating the landing page.",
                    "command": "Claude worker -> product/site",
                }
            ),
            "2026-06-28T12:00:01Z",
        ),
    )
    store.conn.execute(
        "INSERT INTO events VALUES (?, ?, ?, ?, ?)",
        (
            "evt-2b",
            "demo",
            "dashboard.run.output",
            json.dumps(
                {
                    "kind": "claude_agent_sdk",
                    "status": "output",
                    "detail": "reasoning -> Sketch the landing flow, then tighten the CTA before the build checks.",
                    "line": "reasoning -> Sketch the landing flow, then tighten the CTA before the build checks.",
                    "command": "Claude worker -> product/site",
                }
            ),
            "2026-06-28T12:00:01.500000Z",
        ),
    )
    store.conn.execute(
        "INSERT INTO events VALUES (?, ?, ?, ?, ?)",
        (
            "evt-3",
            "demo",
            "dashboard.run.completed",
            json.dumps(
                {
                    "kind": "claude_agent_sdk",
                    "status": "completed",
                    "detail": "Worker finished cleanly.",
                    "command": "Claude worker -> product/site",
                }
            ),
            "2026-06-28T12:00:02Z",
        ),
    )
    store.conn.execute(
        "INSERT INTO events VALUES (?, ?, ?, ?, ?)",
        (
            "evt-4",
            "demo",
            "dashboard.run.output",
            json.dumps({"line": "tool started -> business_read_business"}),
            "2026-06-28T12:00:03Z",
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

    assert "— Claude worker:started —" in output
    assert "Claude worker started for product/site." in output
    assert "— Claude worker:running —" in output
    assert "Generating the landing page." in output
    assert "reasoning -> Sketch the landing flow, then tighten the CTA before the build checks." in output
    assert "— Claude worker:completed —" in output
    assert "Worker finished cleanly." in output
    assert "tool started -> business_read_business" in output


def test_runtime_event_tail_dedupes_immediate_worker_note_repeats():
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
    repeated_note = "reasoning -> Sketch the landing flow, then tighten the CTA before the build checks."
    store.conn.execute(
        "INSERT INTO events VALUES (?, ?, ?, ?, ?)",
        (
            "evt-1",
            "demo",
            "dashboard.run.output",
            json.dumps(
                {
                    "kind": "claude_agent_sdk",
                    "status": "output",
                    "detail": repeated_note,
                    "line": repeated_note,
                    "command": "Claude worker -> product/site",
                }
            ),
            "2026-06-28T12:00:00Z",
        ),
    )
    store.conn.execute(
        "INSERT INTO events VALUES (?, ?, ?, ?, ?)",
        (
            "evt-2",
            "demo",
            "dashboard.run.running",
            json.dumps(
                {
                    "kind": "task",
                    "status": "running",
                    "detail": repeated_note,
                    "line": repeated_note,
                    "command": "Claude worker -> product/site",
                }
            ),
            "2026-06-28T12:00:01Z",
        ),
    )
    store.conn.execute(
        "INSERT INTO events VALUES (?, ?, ?, ?, ?)",
        (
            "evt-3",
            "demo",
            "dashboard.run.output",
            json.dumps(
                {
                    "kind": "claude_agent_sdk",
                    "status": "output",
                    "detail": repeated_note,
                    "line": repeated_note,
                    "command": "Claude worker -> product/site",
                }
            ),
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

    assert output.count(repeated_note) == 1


def test_follow_worker_job_dedupes_immediate_worker_note_repeats(monkeypatch):
    class Store:
        def __init__(self):
            self.conn = sqlite3.connect(":memory:")
            self.conn.row_factory = sqlite3.Row

        def _connect(self):
            return self.conn

        def _leaf_conn(self, conn):
            return contextlib.nullcontext(conn)

        def _row_to_dict(self, row):
            data = dict(row)
            payload = data.pop("payload_json", "")
            data["payload"] = json.loads(payload) if payload else {}
            return data

        def read_ceo_turn_events(self, _slug, limit=200):
            return []

    store = Store()
    repeated_note = "reasoning -> Sketch the landing flow, then tighten the CTA before the build checks."
    runtime_rows = [
        {
            "id": f"evt-{idx}",
            "business_slug": "demo",
            "event_type": event_type,
            "payload": {
                "kind": "claude_agent_sdk" if status == "output" else "task",
                "status": status,
                "detail": repeated_note,
                "line": repeated_note,
                "command": "Claude worker -> product/site",
            },
        }
        for idx, (event_type, status) in enumerate(
            [
                ("dashboard.run.output", "output"),
                ("dashboard.run.running", "running"),
                ("dashboard.run.output", "output"),
            ],
            start=1,
        )
    ]

    runtime_calls = {"count": 0}

    def _fake_runtime_rows(_store, _scope, limit=300):
        runtime_calls["count"] += 1
        if runtime_calls["count"] == 1:
            return []
        return runtime_rows

    statuses = iter(
        [
            SimpleNamespace(status="queued", result=None, error=None),
            SimpleNamespace(status="running", result=None, error=None),
            SimpleNamespace(status="completed", result={"ok": True}, error=None),
        ]
    )

    def _fake_get_job(_conn, _job_id):
        try:
            return next(statuses)
        except StopIteration:
            return SimpleNamespace(status="completed", result={"ok": True}, error=None)

    monkeypatch.setattr("plugins.takyon.jobs.get_job", _fake_get_job)
    monkeypatch.setattr(cli, "_runtime_event_rows_for_business", _fake_runtime_rows)

    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        result = cli._follow_worker_job(
            store,
            "demo",
            "job-1",
            label="bootstrap",
            tail_logs=False,
            poll_seconds=0.0,
            max_seconds=5.0,
        )

    output = out.getvalue()
    assert output.count(repeated_note) == 1
    assert result["status"] == "completed"


def test_follow_worker_job_dedupes_chat_when_stream_contains_prefixed_restart(monkeypatch):
    class Store:
        def __init__(self):
            self.conn = sqlite3.connect(":memory:")
            self.conn.row_factory = sqlite3.Row
            self._chat_reads = 0

        def _connect(self):
            return self.conn

        def _leaf_conn(self, conn):
            return contextlib.nullcontext(conn)

        def _row_to_dict(self, row):
            data = dict(row)
            payload = data.pop("payload_json", "")
            data["payload"] = json.loads(payload) if payload else {}
            return data

        def read_ceo_turn_events(self, _slug, limit=200):
            self._chat_reads += 1
            if self._chat_reads <= 2:
                return []
            return [
                {
                    "id": "chat-1",
                    "payload": {
                        "text": (
                            "I'll start building your move-out evidence product right now. "
                            "Let me kick off with the customer's first update and get research going."
                        )
                    },
                }
            ]

    store = Store()
    final_text = (
        "I'll start building your move-out evidence product right now. "
        "Let me kick off with the customer's first update and get research going."
    )
    streamed_text = (
        "moving on this right away. Let me start by posting the customer update and "
        "setting up the initial brief."
        + final_text
    )
    runtime_rows = [
        {
            "id": "evt-1",
            "business_slug": "demo",
            "event_type": "dashboard.run.output",
            "payload": {"stream": "message_delta", "line": streamed_text},
        },
        {
            "id": "evt-2",
            "business_slug": "demo",
            "event_type": "dashboard.run.output",
            "payload": {"stream": "message_flush"},
        },
    ]

    runtime_calls = {"count": 0}

    def _fake_runtime_rows(_store, _scope, limit=300):
        runtime_calls["count"] += 1
        if runtime_calls["count"] == 1:
            return []
        return runtime_rows

    statuses = iter(
        [
            SimpleNamespace(status="queued", result=None, error=None),
            SimpleNamespace(status="running", result=None, error=None),
            SimpleNamespace(status="completed", result={"ok": True}, error=None),
        ]
    )

    def _fake_get_job(_conn, _job_id):
        try:
            return next(statuses)
        except StopIteration:
            return SimpleNamespace(status="completed", result={"ok": True}, error=None)

    monkeypatch.setattr("plugins.takyon.jobs.get_job", _fake_get_job)
    monkeypatch.setattr(cli, "_runtime_event_rows_for_business", _fake_runtime_rows)

    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        result = cli._follow_worker_job(
            store,
            "demo",
            "job-1",
            label="bootstrap",
            tail_logs=False,
            poll_seconds=0.0,
            max_seconds=5.0,
        )

    output = out.getvalue()
    assert output.count(final_text) == 1
    assert result["status"] == "completed"


def test_follow_chat_matches_just_streamed_ceo_text():
    assert cli._follow_chat_matches_stream("Hello\nworld", " Hello world ") is True
    assert (
        cli._follow_chat_matches_stream(
            "moving on this right away. Let me start by posting the customer update."
            "I'll start building your move-out evidence product right now.",
            "I'll start building your move-out evidence product right now.",
        )
        is True
    )
    assert cli._follow_chat_matches_stream("Hello world", "Hello there") is False


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
    assert "success" in output
    assert "yes" in output
    assert captured["argv"] == [
        "create",
        "homework-solver",
        "--",
        'No-signup trial: don\'t block on an unfinished "quote',
    ]
