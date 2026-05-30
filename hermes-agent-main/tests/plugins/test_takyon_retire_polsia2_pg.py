"""Postgres integration tests for the polsia2 REPLACE cutover (mediationplan.md
Ground Truth, 2026-05-30: takyon OWNS public; polsia2's rows are disposable).

Proves four robustness properties of the replace, all on a real throwaway Postgres
(never mocks), so the destructive live step is designed and verified locally first:

  1. The forward migrations (0001/0002) FAIL LOUD — they do not silently bind via
     `create table if not exists` — when a differently-shaped polsia2 `businesses`
     or `billing_accounts` is already present.
  2. The gated teardown (db/retire_polsia2_public.sql) drops exactly those two
     colliding roots (cascade clears dependents' FK constraints, not their tables),
     after which 0001/0002 install the takyon shape cleanly and its FKs resolve.
  3. The teardown is a pure no-op on a clean database.
  4. Re-running the teardown after takyon ALREADY owns the tables is a no-op that
     NEVER destroys takyon data (the guard is the inverse of 0001/0002's guards).

Skips unless psycopg is importable and TAKYON_TEST_PG_DSN is set.
"""

from __future__ import annotations

from pathlib import Path

import pytest

psycopg = pytest.importorskip("psycopg")

_DB_DIR = Path(__file__).resolve().parents[2] / "plugins" / "takyon" / "db"
_MIGRATIONS_DIR = _DB_DIR / "migrations"
_RETIRE_SQL = _DB_DIR / "retire_polsia2_public.sql"

# Minimal stand-ins for polsia2's real shapes: an id-PK / owner_profile_id businesses
# (NO owner_user_id), a business_id-cascade dependent, and a Stripe-subscription-shaped
# billing_accounts (NO allowance_included_cents). Enough to trip the takyon guards.
_POLSIA2_BUSINESSES = """
    create table public.businesses (
        id uuid primary key default gen_random_uuid(),
        owner_profile_id uuid not null,
        name text not null,
        created_at timestamptz not null default now()
    );
"""
_POLSIA2_DEPENDENT = """
    create table public.agent_runs (
        id uuid primary key default gen_random_uuid(),
        business_id uuid not null references public.businesses (id) on delete cascade,
        note text
    );
"""
_POLSIA2_BILLING_ACCOUNTS = """
    create table public.billing_accounts (
        id uuid primary key default gen_random_uuid(),
        stripe_customer_id text,
        stripe_subscription_id text,
        status text
    );
"""


def _apply_migrations(conn) -> None:
    for sql_path in sorted(_MIGRATIONS_DIR.glob("*.sql")):
        conn.execute(sql_path.read_text())


def _retire(conn) -> None:
    conn.execute(_RETIRE_SQL.read_text())


def _table_exists(conn, name: str) -> bool:
    row = conn.execute("select to_regclass(%s)", (f"public.{name}",)).fetchone()
    return row[0] is not None


def _column_exists(conn, table: str, col: str) -> bool:
    return (
        conn.execute(
            "select 1 from information_schema.columns where table_schema='public' "
            "and table_name=%s and column_name=%s",
            (table, col),
        ).fetchone()
        is not None
    )


def _fk_count(conn, table: str) -> int:
    return conn.execute(
        "select count(*) from information_schema.table_constraints where "
        "table_schema='public' and table_name=%s and constraint_type='FOREIGN KEY'",
        (table,),
    ).fetchone()[0]


def test_forward_migration_refuses_polsia2_businesses_shadow(pg_conn_raw):
    pg_conn_raw.execute(_POLSIA2_BUSINESSES)
    with pytest.raises(psycopg.errors.FeatureNotSupported, match="not the takyon shape"):
        pg_conn_raw.execute((_MIGRATIONS_DIR / "0001_identity_spine.sql").read_text())
    # The guard fired before creating anything: takyon's users table was not made.
    assert not _table_exists(pg_conn_raw, "users")


def test_forward_migration_refuses_polsia2_billing_accounts_shadow(pg_conn_raw):
    pg_conn_raw.execute(_POLSIA2_BILLING_ACCOUNTS)
    with pytest.raises(psycopg.errors.FeatureNotSupported, match="not the takyon shape"):
        pg_conn_raw.execute((_MIGRATIONS_DIR / "0002_ledgers.sql").read_text())


def test_retire_then_migrate_yields_takyon_shape(pg_conn_raw):
    # Stand up the legacy polsia2 control schema (root + dependent + billing).
    pg_conn_raw.execute(_POLSIA2_BUSINESSES)
    pg_conn_raw.execute(_POLSIA2_DEPENDENT)
    pg_conn_raw.execute(_POLSIA2_BILLING_ACCOUNTS)
    assert _fk_count(pg_conn_raw, "agent_runs") == 1

    _retire(pg_conn_raw)

    # Colliding roots gone; the dependent TABLE survives (only its FK was cascaded).
    assert not _table_exists(pg_conn_raw, "businesses")
    assert not _table_exists(pg_conn_raw, "billing_accounts")
    assert _table_exists(pg_conn_raw, "agent_runs")
    assert _fk_count(pg_conn_raw, "agent_runs") == 0

    # Forward migrations now install the takyon shape cleanly.
    _apply_migrations(pg_conn_raw)
    assert _column_exists(pg_conn_raw, "businesses", "owner_user_id")
    assert _column_exists(pg_conn_raw, "billing_accounts", "allowance_included_cents")
    assert _table_exists(pg_conn_raw, "billing_entries")
    assert _table_exists(pg_conn_raw, "custody_entries")

    # Functional proof the takyon FKs resolved: a full ownership + ledger insert chain.
    uid = pg_conn_raw.execute(
        "insert into users (auth0_sub, email) values ('auth0|cutover','c@x.io') returning id"
    ).fetchone()[0]
    pg_conn_raw.execute(
        "insert into businesses (slug, name, owner_user_id) values ('cutbiz','Cut',%s)",
        (uid,),
    )
    pg_conn_raw.execute(
        "insert into billing_entries (user_id, business_slug, bucket, kind, "
        "amount_cents, balance_after_cents, idempotency_key) values "
        "(%s,'cutbiz','topup','topup',1000,1000,'cutover-1')",
        (uid,),
    )
    assert (
        pg_conn_raw.execute(
            "select balance_after_cents from billing_entries where idempotency_key='cutover-1'"
        ).fetchone()[0]
        == 1000
    )


def test_retire_is_noop_on_clean_db(pg_conn_raw):
    # No legacy schema present: the teardown must do nothing and raise nothing.
    _retire(pg_conn_raw)
    assert not _table_exists(pg_conn_raw, "businesses")
    assert not _table_exists(pg_conn_raw, "billing_accounts")


def test_retire_preserves_takyon_data_on_rerun(pg_conn):
    # pg_conn already has 0001+0002 applied — takyon OWNS businesses/billing_accounts.
    uid = pg_conn.execute(
        "insert into users (auth0_sub) values ('auth0|keep') returning id"
    ).fetchone()[0]
    pg_conn.execute(
        "insert into businesses (slug, name, owner_user_id) values ('keepbiz','Keep',%s)",
        (uid,),
    )
    pg_conn.execute(
        "insert into billing_accounts (user_id, allowance_included_cents) values (%s, 5000)",
        (uid,),
    )
    pg_conn.execute(
        "insert into billing_entries (user_id, business_slug, bucket, kind, "
        "amount_cents, balance_after_cents, idempotency_key) values "
        "(%s,'keepbiz','topup','topup',5000,5000,'keep-1')",
        (uid,),
    )

    # Re-running the teardown against a DB takyon owns must be a pure no-op.
    _retire(pg_conn)

    assert _column_exists(pg_conn, "businesses", "owner_user_id")
    assert _table_exists(pg_conn, "billing_accounts")
    assert pg_conn.execute("select count(*) from users where id=%s", (uid,)).fetchone()[0] == 1
    assert (
        pg_conn.execute("select count(*) from businesses where slug='keepbiz'").fetchone()[0] == 1
    )
    assert (
        pg_conn.execute(
            "select count(*) from billing_accounts where user_id=%s", (uid,)
        ).fetchone()[0]
        == 1
    )
    assert (
        pg_conn.execute(
            "select count(*) from billing_entries where idempotency_key='keep-1'"
        ).fetchone()[0]
        == 1
    )
