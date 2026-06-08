from __future__ import annotations

import os
import uuid
from contextlib import contextmanager
from pathlib import Path

import pytest

_BLOCKED_TEST_PG_HOST_ROLES = frozenset({"operator", "subuser", "safebox"})


def _normalized_host_role() -> str:
    raw = str(os.environ.get("TAKYON_HOST_ROLE") or "").strip().lower()
    aliases = {
        "": "",
        "all": "combined",
        "combined": "combined",
        "default": "combined",
        "operator": "operator",
        "dashboard": "operator",
        "subuser": "subuser",
        "app": "subuser",
        "product": "subuser",
        "safebox": "safebox",
    }
    return aliases.get(raw, raw)


def _resolve_test_pg_dsn() -> str | None:
    dsn = str(os.environ.get("TAKYON_TEST_PG_DSN") or "").strip()
    if dsn and _normalized_host_role() in _BLOCKED_TEST_PG_HOST_ROLES:
        raise RuntimeError(
            f"Refusing to run Postgres integration tests on managed TAKYON_HOST_ROLE={_normalized_host_role()}."
        )
    return dsn or None

# Shared fixtures for Postgres control-plane integration tests. Importing psycopg
# is done lazily inside the fixtures (never at conftest import time) so the rest of
# the tests/plugins suite still collects in environments without psycopg.
_DSN = _resolve_test_pg_dsn()
_DB_DIR = Path(__file__).resolve().parents[2] / "plugins" / "takyon" / "db"
# Manual, gated polsia2 teardown — lives OUTSIDE migrations/ so it is never swept.
RETIRE_POLSIA2_SQL = _DB_DIR / "retire_polsia2_public.sql"


def _apply_migrations(conn) -> None:
    """Apply every db/migrations/*.sql in order via the canonical production runner, so the test
    schema and the production schema come from ONE definition (no second, drifting copy) — and the
    suite validates the real runner for free. Imported lazily (like psycopg below) to keep conftest
    side-effect-free at collection time."""
    from plugins.takyon.db.runner import run_migrations

    run_migrations(conn)


@contextmanager
def _throwaway_db(worker_id):
    """Create a fresh, per-test, per-worker uuid-named database; drop it on exit.
    Yields an autocommit psycopg connection to it. psycopg is imported lazily so the
    rest of the suite still collects where psycopg is absent."""
    import psycopg

    dbname = f"takyon_test_{worker_id}_{uuid.uuid4().hex[:8]}"
    with psycopg.connect(_DSN, autocommit=True) as admin:
        admin.execute(f'create database "{dbname}"')
    conn = psycopg.connect(_DSN, dbname=dbname, autocommit=True)
    try:
        yield conn
    finally:
        conn.close()
        with psycopg.connect(_DSN, autocommit=True) as admin:
            admin.execute(f'drop database if exists "{dbname}" with (force)')


@pytest.fixture
def pg_conn(worker_id):
    """A connection to a fresh, per-TEST throwaway database with all control-plane
    migrations applied. Skips unless TAKYON_TEST_PG_DSN points at a Postgres server.

    Function scope (NOT module): every test gets a pristine database. The ledger
    engines treat idempotency keys as GLOBALLY unique (a replayed key is one effect),
    so a fixed literal like "pay-1" or "t" reused across tests would otherwise leak
    between them on a shared DB and be silently swallowed as a replay. A clean DB per
    test makes each test's keys independent. The per-worker name segment keeps
    concurrent pytest-xdist workers from colliding on the database name.
    """
    if not _DSN:
        pytest.skip("TAKYON_TEST_PG_DSN not set; Postgres integration test skipped")
    with _throwaway_db(worker_id) as conn:
        _apply_migrations(conn)
        yield conn


@pytest.fixture
def pg_conn_raw(worker_id):
    """Like pg_conn but with NO migrations applied — a pristine empty database. For
    tests that exercise migration application itself (e.g. the polsia2 REPLACE cutover:
    stand up a simulated legacy schema, then apply the takyon SQL by hand)."""
    if not _DSN:
        pytest.skip("TAKYON_TEST_PG_DSN not set; Postgres integration test skipped")
    with _throwaway_db(worker_id) as conn:
        yield conn


@pytest.fixture
def pg_store_dsn(worker_id):
    """A libpq conninfo STRING for a fresh, per-test throwaway database with all migrations applied.

    Unlike pg_conn (which hands back a live handle), the Postgres-backed TakyonStore opens its OWN
    connections from a URL/DSN — so the seam needs a connection string, not a connection. Built with
    ``make_conninfo`` so a URL-style TAKYON_TEST_PG_DSN merges cleanly with the throwaway dbname into a
    string ``psycopg.connect`` (and the store's ``resolve_database_url`` passthrough) accepts. The DB is
    created and migrated here and dropped on teardown; the store's per-block connections close on
    ``with`` exit, and the teardown drop uses ``(force)`` regardless."""
    if not _DSN:
        pytest.skip("TAKYON_TEST_PG_DSN not set; Postgres integration test skipped")
    from psycopg.conninfo import make_conninfo

    with _throwaway_db(worker_id) as conn:
        _apply_migrations(conn)
        yield make_conninfo(_DSN, dbname=conn.info.dbname)
