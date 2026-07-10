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

from plugins.takyon.db.runner import (  # noqa: E402
    MigrationTopologyError,
    assert_migration_topology,
    migration_files,
    run_migrations,
    topology_sql_path,
)


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

    # Spine (0001) + a Phase-5 table (0009) + a Phase-8 operator table (0011) are all present —
    # the run reached the latest migration, not just an early one.
    assert _table_exists(pg_conn_raw, "users")
    assert _table_exists(pg_conn_raw, "user_api_keys")
    assert _table_exists(pg_conn_raw, "businesses")
    assert _table_exists(pg_conn_raw, "app_gateway_keys")
    assert _table_exists(pg_conn_raw, "control_states")


def test_run_migrations_is_idempotent(pg_conn_raw):
    first = run_migrations(pg_conn_raw)
    # Re-running over an already-current DB applies the same files again with no error (every
    # migration is idempotent by construction) and leaves the schema intact.
    second = run_migrations(pg_conn_raw)
    assert first == second
    assert _table_exists(pg_conn_raw, "users")
    assert _table_exists(pg_conn_raw, "app_gateway_keys")


def test_cutover_migrations_run_as_nocreaterole_production_principal(pg_conn_raw):
    role_before = pg_conn_raw.execute(
        "select exists(select 1 from pg_roles where rolname = 'takyon_operator_access')"
    ).fetchone()[0]
    assert pg_conn_raw.execute(
        "select rolsuper, rolcreaterole, rolcreatedb, rolbypassrls "
        "from pg_roles where rolname = 'takyon_migration'"
    ).fetchone() == (False, False, False, False)

    # Production already has 0001-0072. Seed that exact pre-cutover schema with the test admin,
    # then replay the canonical all-files runner as the real non-CREATEROLE migration principal.
    for path in migration_files():
        if path.name >= "0073_":
            break
        pg_conn_raw.execute(path.read_text())
    pg_conn_raw.execute(topology_sql_path().read_text())

    pg_conn_raw.execute("set session authorization takyon_migration")
    try:
        assert pg_conn_raw.execute(
            "select current_user, session_user"
        ).fetchone() == ("takyon_migration", "takyon_migration")
        assert_migration_topology(pg_conn_raw)
        applied = []
        for path in migration_files():
            if path.name < "0073_":
                continue
            pg_conn_raw.execute(path.read_text())
            applied.append(path.name)
    finally:
        pg_conn_raw.execute("reset session authorization")

    assert applied == [path.name for path in migration_files() if path.name >= "0073_"]
    assert _table_exists(pg_conn_raw, "app_operator_access_grants")
    assert pg_conn_raw.execute(
        "select exists(select 1 from pg_roles where rolname = 'takyon_operator_access')"
    ).fetchone()[0] is role_before
    assert all(
        "takyon_operator_access" not in path.read_text()
        for path in migration_files()
    )


def test_assert_migration_topology_accepts_prepared_migration_role(pg_conn_raw):
    pg_conn_raw.execute("set role takyon_migration")
    try:
        assert_migration_topology(pg_conn_raw)
    finally:
        pg_conn_raw.execute("reset role")


def test_assert_migration_topology_reports_public_owner_fix(pg_conn_raw):
    pg_conn_raw.execute("create table public.bad_owner(id integer)")

    pg_conn_raw.execute("set role takyon_migration")
    try:
        with pytest.raises(MigrationTopologyError) as exc:
            assert_migration_topology(pg_conn_raw)
    finally:
        pg_conn_raw.execute("reset role")

    assert "ALTER TABLE public.bad_owner OWNER TO takyon_migration;" in str(exc.value)


def test_migration_files_are_ordered_and_nonempty():
    # Pure path-level check (no DB) — the apply order is exactly the lexical filename order.
    files = migration_files()
    assert files, "expected at least one migration file"
    names = [p.name for p in files]
    assert names == sorted(names)
    assert names[0].startswith("0001")
    assert all(name.endswith(".sql") for name in names)
