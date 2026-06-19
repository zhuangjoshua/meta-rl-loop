"""Postgres integration tests for the polsia2 REPLACE cutover (mediationplan.md
Ground Truth, 2026-05-30: takyon OWNS public; polsia2's rows are disposable).

Proves five robustness properties of the replace, all on a real throwaway Postgres
(never mocks), so the destructive live step is designed and verified locally first:

  1. The forward migrations (0001/0002) FAIL LOUD — they do not silently bind via
     `create table if not exists` — when a differently-shaped polsia2 `businesses`
     or `billing_accounts` is already present.
  2. The gated teardown (db/retire_polsia2_public.sql) drops exactly the five
     colliding roots (businesses + billing_accounts, plus the Phase-8 operator-port
     collisions agent_runs/events/idempotency_keys) — cascade clears dependents' FK
     constraints, not their tables — after which 0001/0002/0011 install the takyon
     shape cleanly and their FKs resolve.
  3. The teardown is a pure no-op on a clean database.
  4. Re-running the teardown after takyon ALREADY owns the tables is a no-op that
     NEVER destroys takyon data (the guard is the inverse of 0001/0002's guards).
  5. Phase 2 (the full orphan wipe applied live 2026-06-19) drops the enumerated
     prior-generation tables — even with an inter-orphan FK and in any order, via
     `drop ... cascade` — while a table whose name is NOT on the list survives
     untouched.

Skips unless psycopg is importable and TAKYON_TEST_PG_DSN is set.
"""

from __future__ import annotations

from pathlib import Path

import pytest

psycopg = pytest.importorskip("psycopg")

_DB_DIR = Path(__file__).resolve().parents[2] / "plugins" / "takyon" / "db"
_MIGRATIONS_DIR = _DB_DIR / "migrations"
_RETIRE_SQL = _DB_DIR / "retire_polsia2_public.sql"

# Minimal stand-ins for polsia2's real shapes, each missing the takyon-distinctive column so it
# trips the matching REPLACE guard: an id-PK / owner_profile_id businesses (NO owner_user_id), a
# Stripe-subscription-shaped billing_accounts (NO allowance_included_cents), and the three Phase-8
# operator-port collisions live introspection found in public — agent_runs (NO scope), events
# (kind, NO event_type), idempotency_keys (response, NO operation_hash).
_POLSIA2_BUSINESSES = """
    create table public.businesses (
        id uuid primary key default gen_random_uuid(),
        owner_profile_id uuid not null,
        name text not null,
        created_at timestamptz not null default now()
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
_POLSIA2_AGENT_RUNS = """
    create table public.agent_runs (
        id uuid primary key default gen_random_uuid(),
        business_id uuid not null references public.businesses (id) on delete cascade,
        status text,
        note text
    );
"""
_POLSIA2_EVENTS = """
    create table public.events (
        id uuid primary key default gen_random_uuid(),
        business_id uuid references public.businesses (id) on delete cascade,
        kind text not null,
        subject_type text
    );
"""
_POLSIA2_IDEMPOTENCY_KEYS = """
    create table public.idempotency_keys (
        key text primary key,
        business_id uuid references public.businesses (id) on delete cascade,
        response jsonb
    );
"""
# An INNOCENT business_id-cascade dependent that is NOT itself a retire target: it must SURVIVE the
# teardown — cascade clears only its FK constraint, the orphaned table itself remains (SCOPE note in
# retire_polsia2_public.sql: the ~20 business_id dependents are left for a separate gated wipe).
_POLSIA2_DEPENDENT = """
    create table public.workflow_runs (
        id uuid primary key default gen_random_uuid(),
        business_id uuid not null references public.businesses (id) on delete cascade,
        note text
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
    # Stand up the legacy polsia2 control schema: the two original roots, the three Phase-8
    # operator-name collisions, and one innocent business_id-cascade dependent (workflow_runs).
    pg_conn_raw.execute(_POLSIA2_BUSINESSES)
    pg_conn_raw.execute(_POLSIA2_BILLING_ACCOUNTS)
    pg_conn_raw.execute(_POLSIA2_AGENT_RUNS)
    pg_conn_raw.execute(_POLSIA2_EVENTS)
    pg_conn_raw.execute(_POLSIA2_IDEMPOTENCY_KEYS)
    pg_conn_raw.execute(_POLSIA2_DEPENDENT)
    assert _fk_count(pg_conn_raw, "workflow_runs") == 1

    _retire(pg_conn_raw)

    # All five colliding roots are gone…
    for root in ("businesses", "billing_accounts", "agent_runs", "events", "idempotency_keys"):
        assert not _table_exists(pg_conn_raw, root), f"{root} should have been retired"
    # …but the innocent dependent TABLE survives — only its FK constraint was cleared by cascade.
    assert _table_exists(pg_conn_raw, "workflow_runs")
    assert _fk_count(pg_conn_raw, "workflow_runs") == 0

    # Forward migrations now install the takyon shape cleanly: 0001/0002 for the spine + ledgers,
    # 0011 for the ported operator tables whose names polsia2 had collided on.
    _apply_migrations(pg_conn_raw)
    assert _column_exists(pg_conn_raw, "businesses", "owner_user_id")
    assert _column_exists(pg_conn_raw, "billing_accounts", "allowance_included_cents")
    assert _table_exists(pg_conn_raw, "billing_entries")
    assert _table_exists(pg_conn_raw, "custody_entries")
    # The three retired collisions are reinstalled in takyon (0011) shape:
    assert _column_exists(pg_conn_raw, "agent_runs", "scope")
    assert _column_exists(pg_conn_raw, "events", "event_type")
    assert _column_exists(pg_conn_raw, "idempotency_keys", "operation_hash")

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


def test_retire_phase2_wipes_orphan_legacy_tables(pg_conn_raw):
    # Phase 2 is the full orphan wipe deferred by the SCOPE note and applied live on
    # 2026-06-19: the 98 prior-generation tables that the takyon migrations do NOT own
    # and nothing current references. Stand up a REPRESENTATIVE handful (one per legacy
    # family), including a parent→child FK between two listed orphans to prove the
    # `drop ... cascade` loop handles dependencies regardless of array order…
    pg_conn_raw.execute("create table public.profiles (id uuid primary key default gen_random_uuid())")
    pg_conn_raw.execute("create table public.company_sites (id uuid primary key default gen_random_uuid())")
    pg_conn_raw.execute("create table public.meta_ads (id uuid primary key default gen_random_uuid())")
    pg_conn_raw.execute("create table public.business_documents (id uuid primary key default gen_random_uuid())")
    pg_conn_raw.execute(
        "create table public.generated_app_builds (id uuid primary key default gen_random_uuid())"
    )
    # child references parent; both are on the wipe list, and the parent sorts BEFORE the
    # child in the array — so dropping the parent first must cascade-clear this FK rather
    # than raise a dependency error.
    pg_conn_raw.execute(
        "create table public.generated_app_build_steps ("
        "  id uuid primary key default gen_random_uuid(),"
        "  build_id uuid not null references public.generated_app_builds (id) on delete cascade"
        ")"
    )
    # …plus an innocent table whose name is NOT on the 98-name list: it must SURVIVE.
    pg_conn_raw.execute("create table public.survivor_not_legacy (id uuid primary key default gen_random_uuid())")

    _retire(pg_conn_raw)

    for orphan in (
        "profiles",
        "company_sites",
        "meta_ads",
        "business_documents",
        "generated_app_builds",
        "generated_app_build_steps",
    ):
        assert not _table_exists(pg_conn_raw, orphan), f"{orphan} should have been wiped by phase 2"
    # The non-listed table is untouched — the wipe is an explicit enumeration, never a
    # schema-wide drop.
    assert _table_exists(pg_conn_raw, "survivor_not_legacy")

    # Idempotent: a second run is a pure no-op (every drop is `if exists`) and still
    # leaves the survivor alone.
    _retire(pg_conn_raw)
    assert _table_exists(pg_conn_raw, "survivor_not_legacy")


def test_retire_phase2_never_touches_canonical_tables(pg_conn):
    # pg_conn has the full canonical schema (all migrations applied). Running the teardown
    # against a DB takyon fully owns must not drop a single canonical table — Phase 1's
    # shape guards skip, and Phase 2's array contains zero canonical names (and self-aborts
    # if one ever leaks in).
    before = pg_conn.execute(
        "select count(*) from information_schema.tables "
        "where table_schema='public' and table_type='BASE TABLE'"
    ).fetchone()[0]

    _retire(pg_conn)

    after = pg_conn.execute(
        "select count(*) from information_schema.tables "
        "where table_schema='public' and table_type='BASE TABLE'"
    ).fetchone()[0]
    assert after == before, "teardown dropped a canonical table"
    # Spot-check load-bearing canonical tables are still present and usable.
    for canon in ("businesses", "billing_accounts", "events", "jobs", "app_usage_events"):
        assert _table_exists(pg_conn, canon)
