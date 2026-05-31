from __future__ import annotations

import os
import uuid
from contextlib import contextmanager
from pathlib import Path

import pytest

# Shared fixtures for Postgres control-plane integration tests. Importing psycopg
# is done lazily inside the fixtures (never at conftest import time) so the rest of
# the tests/plugins suite still collects in environments without psycopg.
_DSN = os.environ.get("TAKYON_TEST_PG_DSN")
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
