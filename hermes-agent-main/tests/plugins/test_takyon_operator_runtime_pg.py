"""Postgres integration test for 0011_operator_runtime.sql — the Phase-8 storage half of the
SQLite kill (mediationplan.md > Phase 8 gate finding, 2026-05-31).

Proves the migration that ports the 10 SQLite-only operator tables to Postgres does what the
ported TakyonStore seam will depend on:

  1. All 10 operator tables exist after the canonical runner, each carrying its takyon-distinctive
     column (the same column its REPLACE guard keys on).
  2. `businesses` is ENRICHED to the operator shape (goal/status/work_focus/budget_json/
     metadata_json/updated_at) and stays ONE slug-keyed table — the enrich did not fork the 0001 spine.
  3. The work_focus CHECK is enforced (closed enum all|marketing|product); status is left open.
  4. A representative operator-table insert relies on its defaults and its businesses FK, and the
     FK cascade is real.
  5. The fail-loud REPLACE guard refuses a polsia2-shaped same-named collision (robustness #1)
     instead of silently `create table if not exists`-binding to the wrong shape.

Real engine on real Postgres (never mocks). Skips unless psycopg is importable and
TAKYON_TEST_PG_DSN is set (the pg_conn / pg_conn_raw fixtures skip on their own when unset).
"""

from __future__ import annotations

from pathlib import Path

import pytest

psycopg = pytest.importorskip("psycopg")

_MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "plugins" / "takyon" / "db" / "migrations"

# The 10 SQLite-only operator tables 0011 ports, each paired with the takyon-distinctive column its
# REPLACE guard keys on — so this mapping double-checks both "table exists" and "guard column present".
_OPERATOR_TABLES = {
    "workspaces": "budget_json",
    "agent_runs": "scope",
    "ledger_entries": "scope",
    "control_states": "actor",
    "events": "event_type",
    "conversation_threads": "external_id",
    "conversation_messages": "direction",
    "idempotency_keys": "operation_hash",
    "app_surface_contracts": "design_brief_path",
    "business_work_requests": "scope",
}

# Columns 0011 adds to the 0001 slim businesses spine.
_BUSINESSES_ENRICH_COLS = ("goal", "status", "work_focus", "budget_json", "metadata_json", "updated_at")


def _column_exists(conn, table: str, col: str) -> bool:
    return (
        conn.execute(
            "select 1 from information_schema.columns where table_schema='public' "
            "and table_name=%s and column_name=%s",
            (table, col),
        ).fetchone()
        is not None
    )


def _0011_sql() -> str:
    return (_MIGRATIONS_DIR / "0011_operator_runtime.sql").read_text()


def test_0011_creates_operator_tables_with_shape(pg_conn):
    # pg_conn has every migration (including 0011) applied by the canonical runner.
    for table, distinctive_col in _OPERATOR_TABLES.items():
        assert _column_exists(pg_conn, table, distinctive_col), f"{table}.{distinctive_col} missing"


def test_0011_enriches_businesses_in_place(pg_conn):
    for col in _BUSINESSES_ENRICH_COLS:
        assert _column_exists(pg_conn, "businesses", col), f"businesses.{col} missing"
    # Still ONE table keyed by the 0001 spine (slug PK, owner_user_id) — enrich did not fork it.
    assert _column_exists(pg_conn, "businesses", "slug")
    assert _column_exists(pg_conn, "businesses", "owner_user_id")


def test_businesses_work_focus_check_enforced(pg_conn):
    uid = pg_conn.execute(
        "insert into users (auth0_sub) values ('auth0|wf') returning id"
    ).fetchone()[0]
    # Every closed-enum value is accepted.
    for wf in ("all", "marketing", "product"):
        pg_conn.execute(
            "insert into businesses (slug, name, owner_user_id, work_focus) values (%s,%s,%s,%s)",
            (f"wf-{wf}", wf, uid, wf),
        )
    # An out-of-enum value is rejected by businesses_work_focus_chk.
    with pytest.raises(psycopg.errors.CheckViolation):
        pg_conn.execute(
            "insert into businesses (slug, name, owner_user_id, work_focus) "
            "values ('wf-bad','bad',%s,'sales')",
            (uid,),
        )


def test_workspaces_defaults_and_fk_cascade(pg_conn):
    uid = pg_conn.execute(
        "insert into users (auth0_sub) values ('auth0|ws') returning id"
    ).fetchone()[0]
    pg_conn.execute(
        "insert into businesses (slug, name, owner_user_id) values ('wsbiz','WS',%s)", (uid,)
    )
    pg_conn.execute(
        "insert into workspaces (id, business_slug, path, created_at, updated_at) "
        "values ('ws1','wsbiz','/x','2026-05-31T00:00:00Z','2026-05-31T00:00:00Z')"
    )
    # kind/status carry their declared defaults.
    assert pg_conn.execute(
        "select kind, status from workspaces where id='ws1'"
    ).fetchone() == ("workspace", "active")
    # The businesses FK cascade is real: deleting the business removes its workspace row.
    pg_conn.execute("delete from businesses where slug='wsbiz'")
    assert pg_conn.execute("select count(*) from workspaces where id='ws1'").fetchone()[0] == 0


def test_0011_guard_refuses_polsia2_collision(pg_conn_raw):
    # 0011 enriches businesses, so the spine must exist first; 0011 depends on 0001 only.
    pg_conn_raw.execute((_MIGRATIONS_DIR / "0001_identity_spine.sql").read_text())
    # A polsia2-shaped events (has `kind`, NO `event_type`) — exactly the live-introspected collision.
    pg_conn_raw.execute(
        "create table public.events (id uuid primary key default gen_random_uuid(), "
        "kind text not null, subject_type text)"
    )
    # 0011 must FAIL LOUD, not `create table if not exists`-bind to the wrong shape.
    with pytest.raises(psycopg.errors.FeatureNotSupported, match="not the takyon shape"):
        pg_conn_raw.execute(_0011_sql())
    # Proof the raise actually halted the migration: control_states is defined AFTER the events guard
    # in 0011, so it is never reached — it must be absent regardless of multi-statement rollback.
    assert pg_conn_raw.execute("select to_regclass('public.control_states')").fetchone()[0] is None
