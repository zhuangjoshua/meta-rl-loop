from __future__ import annotations

import os
import uuid
from pathlib import Path

import pytest

# Shared fixture for Postgres control-plane integration tests. Importing psycopg
# is done lazily inside the fixture (never at conftest import time) so the rest of
# the tests/plugins suite still collects in environments without psycopg.
_DSN = os.environ.get("TAKYON_TEST_PG_DSN")
_MIGRATIONS_DIR = (
    Path(__file__).resolve().parents[2] / "plugins" / "takyon" / "db" / "migrations"
)


@pytest.fixture(scope="module")
def pg_conn(worker_id):
    """A connection to a fresh, per-worker throwaway database with all control-plane
    migrations applied. Skips unless TAKYON_TEST_PG_DSN points at a Postgres server.

    Per-worker isolation keeps concurrent pytest-xdist workers from racing on
    shared-catalog DDL, and mirrors how migrations run for real: once, on a clean
    database.
    """
    if not _DSN:
        pytest.skip("TAKYON_TEST_PG_DSN not set; Postgres integration test skipped")

    import psycopg

    dbname = f"takyon_test_{worker_id}_{uuid.uuid4().hex[:8]}"
    with psycopg.connect(_DSN, autocommit=True) as admin:
        admin.execute(f'create database "{dbname}"')
    conn = psycopg.connect(_DSN, dbname=dbname, autocommit=True)
    try:
        for sql_path in sorted(_MIGRATIONS_DIR.glob("*.sql")):
            conn.execute(sql_path.read_text())
        yield conn
    finally:
        conn.close()
        with psycopg.connect(_DSN, autocommit=True) as admin:
            admin.execute(f'drop database if exists "{dbname}" with (force)')
