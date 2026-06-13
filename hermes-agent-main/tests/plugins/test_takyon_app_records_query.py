"""Hermetic tests for the records-v2 bounded query compiler (§18), both dialects + SQLite e2e."""

from __future__ import annotations

import sqlite3
import uuid

import pytest

from plugins.takyon import app_records
from plugins.takyon.app_records import (
    _PG_DIALECT,
    _SQLITE_DIALECT,
    RecordQueryError,
    compile_record_query,
    decode_record_cursor,
    encode_record_cursor,
)


# --- compiler: dialect output + safety ---------------------------------------------

def test_compiler_pg_vs_sqlite_data_field_expressions():
    pg_where, _, _, pg_params = compile_record_query(
        filters=[{"field": "data.age", "op": "gte", "value": 25}],
        sort=None, cursor=None, limit=None, dialect=_PG_DIALECT,
    )
    sq_where, _, _, sq_params = compile_record_query(
        filters=[{"field": "data.age", "op": "gte", "value": 25}],
        sort=None, cursor=None, limit=None, dialect=_SQLITE_DIALECT,
    )
    assert pg_where == ["(data->>'age')::numeric >= %s"]
    assert sq_where == ["CAST(json_extract(data_json, '$.age') AS REAL) >= ?"]
    assert pg_params == [25] and sq_params == [25]


def test_compiler_real_columns_are_bare_identifiers():
    where, order_sql, limit, params = compile_record_query(
        filters=[{"field": "record_type", "op": "eq", "value": "profile"}],
        sort=[{"field": "created_at", "dir": "asc"}],
        cursor=None, limit=10, dialect=_PG_DIALECT,
    )
    assert where == ["record_type = %s"]
    assert params == ["profile"]
    assert order_sql == "ORDER BY created_at ASC, id ASC"
    assert limit == 10


def test_compiler_in_ilike_exists():
    where, _, _, params = compile_record_query(
        filters=[
            {"field": "record_type", "op": "in", "value": ["a", "b"]},
            {"field": "title", "op": "ilike", "value": "alpha"},
            {"field": "data.flag", "op": "exists", "value": True},
        ],
        sort=None, cursor=None, limit=None, dialect=_PG_DIALECT,
    )
    assert where[0] == "record_type IN (%s, %s)"
    assert where[1] == "title ILIKE %s"
    assert where[2] == "data->>'flag' IS NOT NULL"
    assert params == ["a", "b", "%alpha%"]


def test_compiler_rejects_hostile_field_and_op_and_key():
    # field names never interpolated: a non-whitelisted field is rejected
    with pytest.raises(RecordQueryError, match="not allowed"):
        compile_record_query(filters=[{"field": "id; drop table", "op": "eq", "value": 1}],
                             sort=None, cursor=None, limit=None, dialect=_PG_DIALECT)
    with pytest.raises(RecordQueryError, match="invalid data key"):
        compile_record_query(filters=[{"field": "data.a' or '1'='1", "op": "eq", "value": 1}],
                             sort=None, cursor=None, limit=None, dialect=_PG_DIALECT)
    with pytest.raises(RecordQueryError, match="op `evil`"):
        compile_record_query(filters=[{"field": "title", "op": "evil", "value": 1}],
                             sort=None, cursor=None, limit=None, dialect=_PG_DIALECT)


def test_compiler_caps_and_limit_clamp():
    with pytest.raises(RecordQueryError, match="at most 5 filters"):
        compile_record_query(filters=[{"field": "title", "op": "eq", "value": i} for i in range(6)],
                             sort=None, cursor=None, limit=None, dialect=_PG_DIALECT)
    _, _, limit, _ = compile_record_query(filters=None, sort=None, cursor=None, limit=9999, dialect=_PG_DIALECT)
    assert limit == 100  # clamped to QUERY_MAX_LIMIT
    _, _, default_limit, _ = compile_record_query(filters=None, sort=None, cursor=None, limit=None, dialect=_PG_DIALECT)
    assert default_limit == 25


def test_cursor_roundtrip_and_malformed():
    cur = encode_record_cursor("2026-06-13T01:00:00+00:00", "abc123")
    assert decode_record_cursor(cur) == ("2026-06-13T01:00:00+00:00", "abc123")
    pg_cur = encode_record_cursor("2026-06-13 01:00:00+00:00", "abc123")
    assert decode_record_cursor(pg_cur) == ("2026-06-13 01:00:00+00:00", "abc123")
    assert decode_record_cursor("") is None
    with pytest.raises(RecordQueryError, match="malformed"):
        decode_record_cursor("!!!not-base64!!!")


def test_cursor_rejects_different_sort_order():
    cur = encode_record_cursor("2026-06-13T01:00:00+00:00", "abc123")
    with pytest.raises(RecordQueryError, match="different sort order"):
        compile_record_query(
            filters=None,
            sort=[{"field": "created_at", "dir": "asc"}],
            cursor=cur,
            limit=None,
            dialect=_PG_DIALECT,
        )


# --- end-to-end against a real SQLite table ----------------------------------------

def _seed_records(conn, rows):
    for rt, title, data, updated in rows:
        conn.execute(
            "INSERT INTO app_records (id, business_slug, app_user_id, record_type, title, data_json, "
            "metadata_json, created_at, updated_at) VALUES (?, 'biz', 'u1', ?, ?, ?, '{}', ?, ?)",
            (uuid.uuid4().hex, rt, title, data, updated, updated),
        )
    conn.commit()


def _run_query(conn, **kwargs):
    query_kwargs = {"filters": None, "sort": None, "cursor": None, "limit": None, **kwargs}
    where, order_sql, limit, params = compile_record_query(dialect=_SQLITE_DIALECT, **query_kwargs)
    sql = "SELECT * FROM app_records WHERE business_slug = ? AND app_user_id = ?"
    sql_params = ["biz", "u1"]
    for frag in where:
        sql += f" AND {frag}"
    sql_params.extend(params)
    sql += f" {order_sql} LIMIT ?"
    sql_params.append(limit + 1)
    rows = [dict(r) for r in conn.execute(sql, tuple(sql_params)).fetchall()]
    nxt = None
    if len(rows) > limit:
        rows = rows[:limit]
        nxt = app_records.encode_query_record_cursor(rows[-1], sort=query_kwargs.get("sort"), dialect=_SQLITE_DIALECT)
    return rows, nxt


@pytest.fixture
def records_conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE app_records (id TEXT PRIMARY KEY, business_slug TEXT, app_user_id TEXT, "
        "record_type TEXT, title TEXT, data_json TEXT, metadata_json TEXT, created_at TEXT, updated_at TEXT)"
    )
    return conn


def test_sqlite_filter_and_sort_on_data_field(records_conn):
    _seed_records(records_conn, [
        ("profile", "young", '{"age": 22}', "2026-06-13T01:00:00"),
        ("profile", "mid", '{"age": 30}', "2026-06-13T02:00:00"),
        ("profile", "old", '{"age": 55}', "2026-06-13T03:00:00"),
    ])
    rows, _ = _run_query(
        records_conn,
        filters=[{"field": "data.age", "op": "gte", "value": 25}, {"field": "data.age", "op": "lte", "value": 40}],
    )
    assert [r["title"] for r in rows] == ["mid"]


def test_sqlite_keyset_pagination_no_overlap(records_conn):
    _seed_records(records_conn, [
        ("note", f"n{i}", "{}", f"2026-06-13T0{i}:00:00") for i in range(1, 6)
    ])
    page1, cursor = _run_query(records_conn, limit=2)
    assert [r["title"] for r in page1] == ["n5", "n4"]
    assert cursor
    page2, cursor2 = _run_query(records_conn, limit=2, cursor=cursor)
    assert [r["title"] for r in page2] == ["n3", "n2"]
    # no overlap between pages
    assert set(r["id"] for r in page1).isdisjoint(r["id"] for r in page2)
    page3, _ = _run_query(records_conn, limit=2, cursor=cursor2)
    assert [r["title"] for r in page3] == ["n1"]


def test_sqlite_keyset_pagination_follows_custom_sort(records_conn):
    _seed_records(records_conn, [
        ("note", "a", "{}", "2026-06-13T01:00:00"),
        ("note", "b", "{}", "2026-06-13T02:00:00"),
        ("note", "c", "{}", "2026-06-13T05:00:00"),
        ("note", "d", "{}", "2026-06-13T00:00:00"),
    ])
    records_conn.execute("UPDATE app_records SET created_at = ? WHERE title = 'a'", ("2026-06-13T01:00:00",))
    records_conn.execute("UPDATE app_records SET created_at = ? WHERE title = 'b'", ("2026-06-13T02:00:00",))
    records_conn.execute("UPDATE app_records SET created_at = ? WHERE title = 'c'", ("2026-06-13T03:00:00",))
    records_conn.execute("UPDATE app_records SET created_at = ? WHERE title = 'd'", ("2026-06-13T04:00:00",))
    records_conn.commit()
    page1, cursor = _run_query(records_conn, limit=2, sort=[{"field": "created_at", "dir": "asc"}])
    assert [r["title"] for r in page1] == ["a", "b"]
    assert cursor
    page2, _ = _run_query(records_conn, limit=2, sort=[{"field": "created_at", "dir": "asc"}], cursor=cursor)
    assert [r["title"] for r in page2] == ["c", "d"]
    assert set(r["id"] for r in page1).isdisjoint(r["id"] for r in page2)
