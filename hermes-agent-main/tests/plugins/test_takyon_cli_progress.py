import json
import os

from plugins.takyon import cli


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
