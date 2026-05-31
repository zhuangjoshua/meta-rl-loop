"""Idempotent migration runner — the single production path that brings a Postgres database to the
current takyon schema.

``run_migrations(conn)`` applies every file in ``db/migrations/*.sql`` in lexical order (0001…,
0002…, …). It is deliberately the SAME logic the test fixtures use (``tests/plugins/conftest.py``
delegates to it), so the schema a test runs against and the schema production runs against come from
ONE definition in ONE order — there is no second, silently-drifting copy of "apply the migrations".

Idempotent by construction: every migration uses ``create table if not exists`` / ``create index if
not exists`` / guarded ``do $$ … $$`` REPLACE blocks, so running this on an already-current database
is a safe no-op and is the intended "bring the DB to current" operation. There is no down-migration
and no partial state to reconcile; a genuinely wrong-shaped pre-existing table makes a guard raise
loudly (robustness #1 — mediationplan.md) rather than bind silently.

Pure leaf: it takes a psycopg ``conn`` and never opens or closes one itself — the caller owns the
connection and its mode. Both the test fixtures and the runtime host pass an autocommit connection,
matching how every leaf in this package is exercised.
"""

from __future__ import annotations

from pathlib import Path

# The migrations live next to this module. Single source of the path for BOTH production and tests.
MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"


def migration_files() -> list[Path]:
    """Every migration file in apply order (lexical by name — the 0001/0002/… prefixes ARE the
    order). Scoped to ``migrations/`` so anything kept deliberately outside it (e.g. the
    manually-gated ``retire_polsia2_public.sql`` teardown) is never swept into a normal run."""
    return sorted(MIGRATIONS_DIR.glob("*.sql"))


def run_migrations(conn) -> list[str]:
    """Apply every migration in order against ``conn``; return the applied filenames (in order).

    One ``conn.execute(<file text>)`` per file — exactly what the test fixtures have always done — so
    this runner and the test harness can never disagree about what "the schema" is. Idempotent and
    safe to call on an already-current database. The caller owns the connection/transaction mode.
    """
    applied: list[str] = []
    for sql_path in migration_files():
        conn.execute(sql_path.read_text())
        applied.append(sql_path.name)
    return applied
