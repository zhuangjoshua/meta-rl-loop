"""Postgres integration test for the migration runner (``plugins/takyon/db/runner.py``) — the single
production path that brings a database to the current takyon schema.

Proves ``run_migrations`` applies every migration to a pristine empty database (the identity spine
plus a Phase-5 table appear) and is idempotent (a second run is a safe no-op), so the schema
production receives is exactly the schema the rest of the suite is validated against.

Real engine on real Postgres (never mocks). Skips unless psycopg is importable and
TAKYON_TEST_PG_DSN is set (the pg_conn_raw fixture skips on its own when unset).
"""

from __future__ import annotations

import pytest

pytest.importorskip("psycopg")

from plugins.takyon.db.runner import migration_files, run_migrations  # noqa: E402


def _table_exists(conn, name: str) -> bool:
    return (
        conn.execute("select to_regclass(%s)", (f"public.{name}",)).fetchone()[0]
        is not None
    )


def test_run_migrations_brings_empty_db_to_current(pg_conn_raw):
    # Pristine DB: none of the takyon tables exist yet.
    assert not _table_exists(pg_conn_raw, "users")
    assert not _table_exists(pg_conn_raw, "app_gateway_keys")

    applied = run_migrations(pg_conn_raw)

    # Applied exactly the migration set, in lexical (0001, 0002, …) apply order.
    expected = [p.name for p in migration_files()]
    assert applied == expected
    assert applied == sorted(applied)
    assert applied[0].startswith("0001")

    # Spine (0001) + a Phase-5 table (0009) are now present — the run reached the latest migration.
    assert _table_exists(pg_conn_raw, "users")
    assert _table_exists(pg_conn_raw, "user_api_keys")
    assert _table_exists(pg_conn_raw, "businesses")
    assert _table_exists(pg_conn_raw, "app_gateway_keys")


def test_run_migrations_is_idempotent(pg_conn_raw):
    first = run_migrations(pg_conn_raw)
    # Re-running over an already-current DB applies the same files again with no error (every
    # migration is idempotent by construction) and leaves the schema intact.
    second = run_migrations(pg_conn_raw)
    assert first == second
    assert _table_exists(pg_conn_raw, "users")
    assert _table_exists(pg_conn_raw, "app_gateway_keys")


def test_migration_files_are_ordered_and_nonempty():
    # Pure path-level check (no DB) — the apply order is exactly the lexical filename order.
    files = migration_files()
    assert files, "expected at least one migration file"
    names = [p.name for p in files]
    assert names == sorted(names)
    assert names[0].startswith("0001")
    assert all(name.endswith(".sql") for name in names)
