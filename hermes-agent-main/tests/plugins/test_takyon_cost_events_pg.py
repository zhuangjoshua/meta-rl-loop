"""Postgres integration tests for the cost/log observability ledgers (migration 0070).

Pins the contract of the two debugging tables and their recorder (plugins/takyon/cost_events.py):
  * migration 0070 creates operator_cost_events + app_cost_events + the SECURITY DEFINER append
    port takyon_app_record_cost_event, via the canonical runner (the pg_conn fixture replays every
    migration file through plugins.takyon.db.runner);
  * the recorder is BEST-EFFORT — it never raises into the caller and never poisons the caller's
    open transaction (savepoint isolation), because these writes ride money paths;
  * NULL means "not applicable" (unset token/cost fields stay NULL; negatives clamp to 0);
  * the security posture is deny-by-default: the app plane has no direct table access on either
    table (append is ONLY through the port, which works under the app plane's rls_bypass='0' pin
    per the 0065 definer semantics), and runtime roles are append-only on the operator table.

Real engine on real Postgres (never mocks). Skips unless psycopg is importable and
TAKYON_TEST_PG_DSN is set.
"""

from __future__ import annotations

import json

import pytest

psycopg = pytest.importorskip("psycopg")

from plugins.takyon import cost_events  # noqa: E402


def test_returned_id_accepts_runtime_mapping_and_test_tuple_rows():
    assert cost_events._returned_id(("tuple-id",)) == "tuple-id"
    assert cost_events._returned_id({"id": "mapping-id"}) == "mapping-id"
    assert cost_events._returned_id({"takyon_app_record_cost_event": "port-id"}) == "port-id"


def test_migration_creates_ledgers_and_port(pg_conn):
    for table in ("operator_cost_events", "app_cost_events"):
        row = pg_conn.execute("select to_regclass(%s)", (f"public.{table}",)).fetchone()
        assert row and row[0] == table
    row = pg_conn.execute(
        "select count(*) from pg_proc where proname = 'takyon_app_record_cost_event'"
    ).fetchone()
    assert row and int(row[0]) == 1


def test_operator_event_roundtrip(pg_conn):
    event_id = cost_events.record_operator_cost_event(
        pg_conn,
        event_kind=cost_events.KIND_LLM_CALL,
        business_slug="biz-a",
        job_id="job-1",
        run_id="job-1",
        session_id="sess-1",
        task_kind="ceo_wake",
        name="claude-fable-5",
        status="ok",
        provider="anthropic",
        model="claude-fable-5",
        input_tokens=100,
        output_tokens=50,
        cache_read_tokens=1000,
        cache_write_tokens=0,
        reasoning_tokens=None,
        cost_microusd=1234,
        cost_status="estimated",
        reservation_key="rk-1",
        duration_ms=2500,
        error=None,
        payload={"api_call_count": 3},
    )
    assert event_id
    row = pg_conn.execute(
        "select business_slug, job_id, event_kind, name, status, provider, model,"
        " input_tokens, output_tokens, cache_read_tokens, cache_write_tokens, reasoning_tokens,"
        " cost_microusd, cost_status, reservation_key, duration_ms, error, payload, created_at"
        " from operator_cost_events where id = %s",
        (event_id,),
    ).fetchone()
    assert row is not None
    assert row[0] == "biz-a" and row[1] == "job-1" and row[2] == "llm_call"
    assert row[3] == "claude-fable-5" and row[4] == "ok"
    assert row[5] == "anthropic" and row[6] == "claude-fable-5"
    assert (row[7], row[8], row[9], row[10]) == (100, 50, 1000, 0)
    assert row[11] is None  # unset stays NULL, never 0
    assert row[12] == 1234 and row[13] == "estimated" and row[14] == "rk-1"
    assert row[15] == 2500 and row[16] is None
    payload = row[17] if isinstance(row[17], dict) else json.loads(row[17])
    assert payload == {"api_call_count": 3}
    assert row[18] is not None  # timestamped by the database


def test_operator_event_clamps_and_kill_switch(pg_conn, monkeypatch):
    event_id = cost_events.record_operator_cost_event(
        pg_conn,
        event_kind=cost_events.KIND_TOOL_CALL,
        name="business_upsert_business",
        input_tokens=-5,
        cost_microusd=-1,
    )
    assert event_id
    row = pg_conn.execute(
        "select input_tokens, cost_microusd, output_tokens from operator_cost_events where id = %s",
        (event_id,),
    ).fetchone()
    assert row == (0, 0, None)  # negatives clamp to 0; unset stays NULL

    monkeypatch.setenv("TAKYON_COST_EVENTS_DISABLED", "1")
    before = pg_conn.execute("select count(*) from operator_cost_events").fetchone()[0]
    assert cost_events.record_operator_cost_event(pg_conn, event_kind="llm_call") is None
    after = pg_conn.execute("select count(*) from operator_cost_events").fetchone()[0]
    assert before == after


def test_recorder_never_raises_and_preserves_caller_transaction(pg_conn, monkeypatch):
    # Break the recorder's SQL: the write must fail SILENTLY (returns None) and, inside a caller
    # transaction, must not poison the caller's work (savepoint isolation).
    monkeypatch.setattr(cost_events, "_OPERATOR_INSERT_SQL", "insert into no_such_table values (1)")
    with pg_conn.transaction():
        pg_conn.execute(
            "insert into operator_cost_events (event_kind, name) values ('log', 'caller-marker')"
        )
        assert cost_events.record_operator_cost_event(pg_conn, event_kind="llm_call") is None
        # caller transaction still healthy after the failed event write
        pg_conn.execute(
            "insert into operator_cost_events (event_kind, name) values ('log', 'caller-marker-2')"
        )
    rows = pg_conn.execute(
        "select count(*) from operator_cost_events where name like 'caller-marker%'"
    ).fetchone()
    assert rows[0] == 2


def test_app_event_roundtrip_through_port(pg_conn):
    event_id = cost_events.record_app_cost_event(
        pg_conn,
        business_slug="biz-b",
        event_kind=cost_events.KIND_LLM_CALL,
        name="ai_generate",
        status="ok",
        route="internal_ai_gateway",
        purpose="ai_generate",
        provider="anthropic",
        model="claude-sonnet-5",
        input_tokens=10,
        output_tokens=20,
        cost_microusd=999,
        cost_status="actual",
        reservation_key="rk-app-1",
        provider_request_id="msg_123",
        app_user_tier="pro",
        duration_ms=1200,
        payload={"billed_cost_microusd": 999},
    )
    assert event_id
    row = pg_conn.execute(
        "select business_slug, event_kind, name, status, route, purpose, provider, model,"
        " input_tokens, output_tokens, cost_microusd, cost_status, reservation_key,"
        " provider_request_id, app_user_tier, duration_ms, payload"
        " from app_cost_events where id = %s",
        (event_id,),
    ).fetchone()
    assert row is not None
    assert row[0] == "biz-b" and row[1] == "llm_call" and row[2] == "ai_generate"
    assert row[3] == "ok" and row[4] == "internal_ai_gateway" and row[5] == "ai_generate"
    assert row[6] == "anthropic" and row[7] == "claude-sonnet-5"
    assert (row[8], row[9], row[10], row[11]) == (10, 20, 999, "actual")
    assert row[12] == "rk-app-1" and row[13] == "msg_123" and row[14] == "pro"
    assert row[15] == 1200
    payload = row[16] if isinstance(row[16], dict) else json.loads(row[16])
    assert payload == {"billed_cost_microusd": 999}


def test_app_port_guards_and_junk_payload(pg_conn):
    # empty business/kind: the recorder refuses locally (no SQL, no row)
    assert cost_events.record_app_cost_event(pg_conn, business_slug="", event_kind="llm_call") is None
    assert cost_events.record_app_cost_event(pg_conn, business_slug="biz-c", event_kind="") is None
    # the port itself refuses an empty business
    with pytest.raises(psycopg.errors.RaiseException):
        pg_conn.execute(
            "select takyon_app_record_cost_event('', 'llm_call', null, null, null, null, null, null,"
            " null, null, null, null, null, null, null, null, null, null, null, null, null, null)"
        )
    # junk payload JSON is tolerated (kept as unparsed_payload), never raised
    row = pg_conn.execute(
        "select takyon_app_record_cost_event('biz-c', 'log', null, null, null, null, null, null,"
        " null, null, null, null, null, null, null, null, null, null, null, null,"
        " 'not-json{{{', null)"
    ).fetchone()
    assert row and row[0]
    payload = pg_conn.execute(
        "select payload from app_cost_events where id = %s", (row[0],)
    ).fetchone()[0]
    payload = payload if isinstance(payload, dict) else json.loads(payload)
    assert payload.get("unparsed_payload") == "not-json{{{"


def test_app_plane_denied_direct_access_but_port_appends(pg_conn):
    """The subuser plane's whole surface is: execute the port. No direct read/write on either
    table, even though the port keeps working under the app plane's rls_bypass='0' session pin
    (0065 definer semantics). This is the no-security-regression pin."""
    pg_conn.execute("set role takyon_app_runtime")
    try:
        pg_conn.execute("select set_config('takyon.rls_bypass', '0', false)")
        for probe in (
            "select count(*) from operator_cost_events",
            "insert into operator_cost_events (event_kind) values ('log')",
            "select count(*) from app_cost_events",
            "insert into app_cost_events (business_slug, event_kind) values ('biz-d', 'log')",
            "update app_cost_events set status = 'x'",
            "delete from app_cost_events",
        ):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                pg_conn.execute(probe)
        row = pg_conn.execute(
            "select takyon_app_record_cost_event('biz-d', 'llm_call', null, null, null, null,"
            " 'anthropic', 'claude-sonnet-5', 5, 7, null, null, 42, 'actual', 'rk-d', null,"
            " null, 'pro', 10, null, '{}', null)"
        ).fetchone()
        assert row and row[0]
    finally:
        pg_conn.execute("reset role")
        pg_conn.execute("select set_config('takyon.rls_bypass', '', false)")
    row = pg_conn.execute(
        "select business_slug, input_tokens, output_tokens, cost_microusd from app_cost_events"
        " where reservation_key = 'rk-d'"
    ).fetchone()
    assert row == ("biz-d", 5, 7, 42)


def test_metrics_observation_payload_shape(monkeypatch):
    """record_metrics_observation is the NON-PRESCRIPTIVE channel-metrics recorder: whatever the
    provider returned lands verbatim under payload['metrics']; rows cap at 50 with the true count
    kept; empty identifier values are dropped. (Pure unit test — the SQL path is pinned above.)"""
    captured = {}
    monkeypatch.setattr(
        cost_events, "record_operator_event_autoconn", lambda **fields: captured.update(fields)
    )
    cost_events.record_metrics_observation(
        provider="meta",
        name="meta:adset:123",
        metrics={"impressions": 1000, "ctr": 0.021, "cpm": 4.2, "anything_new": "kept"},
        rows=[{"i": i} for i in range(60)],
        business_slug="biz-m",
        identifiers={"slug": "camp", "object_id": "123", "empty": "", "none": None},
    )
    assert captured["event_kind"] == "metrics"
    assert captured["provider"] == "meta"
    assert captured["business_slug"] == "biz-m"
    payload = captured["payload"]
    assert payload["metrics"] == {"impressions": 1000, "ctr": 0.021, "cpm": 4.2, "anything_new": "kept"}
    assert payload["rows_total"] == 60 and len(payload["rows"]) == 50
    assert payload["identifiers"] == {"slug": "camp", "object_id": "123"}


def test_metrics_observation_roundtrip_row(pg_conn):
    event_id = cost_events.record_operator_cost_event(
        pg_conn,
        event_kind=cost_events.KIND_METRICS,
        business_slug="biz-m",
        provider="reddit",
        name="reddit:campaign:c_1",
        payload={"metrics": {"impressions": 5, "ctr": 0.5, "spend_usd": 1.25}, "rows_total": 2},
    )
    assert event_id
    row = pg_conn.execute(
        "select event_kind, provider, payload from operator_cost_events where id = %s",
        (event_id,),
    ).fetchone()
    assert row[0] == "metrics" and row[1] == "reddit"
    payload = row[2] if isinstance(row[2], dict) else json.loads(row[2])
    assert payload["metrics"]["ctr"] == 0.5 and payload["rows_total"] == 2


def test_operator_runtime_role_is_append_only(pg_conn):
    pg_conn.execute("set role takyon_operator_runtime")
    try:
        event_id = cost_events.record_operator_cost_event(
            pg_conn, event_kind="job", business_slug="biz-e", job_id="job-9", status="completed"
        )
        assert event_id
        for probe in (
            "update operator_cost_events set status = 'x'",
            "delete from operator_cost_events",
        ):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                pg_conn.execute(probe)
    finally:
        pg_conn.execute("reset role")
    row = pg_conn.execute(
        "select business_slug, status from operator_cost_events where job_id = 'job-9'"
    ).fetchone()
    assert row == ("biz-e", "completed")
