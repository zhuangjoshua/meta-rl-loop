"""Authoritative-suite PG fixture override — make the DB-layer RLS proof FAITHFUL.

The shared ``tests/conftest.py`` ``pg_conn`` yields an *autocommit* connection bound to the
privileged login role (``postgres`` on the local rig — a superuser). That is fine for the
control-plane integration tests, but it cannot prove invariant 3's DB-layer claim that RLS
denies a cross-tenant read/write, for two independent reasons:

  1. A superuser / BYPASSRLS role is NEVER subject to RLS, even under ``FORCE`` — so the 0027
     policies could be silently broken and the assertions would still "pass" (everything is
     visible to a superuser). The only honest proof runs the customer-scoped assertions under a
     NON-bypassing role, exactly as production does via ``core.TakyonStore._pg_app_scope`` →
     ``SET LOCAL ROLE takyon_app`` (migration 0030).
  2. Under autocommit, the request-scope bindings the inv3 test sets with
     ``set_config(..., is_local := true)`` evaporate after their own statement, so a later
     ``SELECT`` would see an empty scope. The store connection that runs app requests in
     production is ``autocommit=False`` (``core._PGConn``), holding one transaction for the
     scope; this fixture mirrors that so the request-local bindings persist across the test body.

So for ``tests/authoritative`` ONLY, ``pg_conn`` yields a non-autocommit connection running under
a non-bypassing role. To let the inv3 test perform its privileged SETUP (provision users /
businesses / app_users — control-plane tables that have NO RLS) on the same connection, the
fixture's role is granted DML across the throwaway DB. This is a TEST-HARNESS role, not
production: production's ``takyon_app`` (migration 0030) deliberately has NO control-plane grants —
it can only ever touch the per-customer ``app_*`` substrate. The tenant boundary the invariant
actually protects (the ``app_*`` RLS policies) is enforced identically here, because this role is
``NOBYPASSRLS`` and the ``app_*`` tables are ``FORCE``'d.

This overrides ``pg_conn`` for this directory only (pytest resolves the nearest conftest); every
other suite keeps the shared autocommit/superuser fixture unchanged.
"""

from __future__ import annotations

import uuid

import pytest

# Reuse the shared throwaway-DB machinery + migration runner from the root conftest, so the schema
# under test is the exact production one (no second "apply migrations" copy).
from tests.conftest import (  # type: ignore[attr-defined]
    _TAKYON_TEST_PG_DSN,
    _apply_takyon_pg_migrations,
    _throwaway_takyon_pg_db,
)

# Only the cross-tenant RLS module needs the non-bypassing, transaction-held connection. Every
# OTHER authoritative PG test (inv1/inv2/inv4/… ledger + pricing checks) expects the shared
# autocommit/superuser ``pg_conn`` and would break under a restricted role — so the override below
# is scoped to this one module and delegates to the shared fixture for everything else.
_RLS_MODULE_BASENAME = "test_inv3_no_cross_tenant_access.py"

# A test-harness role that is subject to RLS (NOBYPASSRLS, non-superuser) yet can run the inv3
# test's privileged control-plane setup. PostgreSQL roles are CLUSTER-global (not per-database),
# so the name must be UNIQUE per fixture instance — otherwise concurrent xdist workers
# create/drop the same role and stomp each other (a fixed name caused intermittent
# "permission denied" / role errors under -n>1). The prefix keeps it clearly distinct from the
# migrations' own production ``takyon_app`` role.
_AUTH_TEST_ROLE_PREFIX = "takyon_authrt"


@pytest.fixture
def pg_conn(worker_id, request):
    import psycopg

    if not _TAKYON_TEST_PG_DSN:
        pytest.skip("TAKYON_TEST_PG_DSN not set; Postgres integration test skipped")

    # Scope the RLS-enforcing connection to the cross-tenant module only. Any other authoritative
    # test asking for ``pg_conn`` gets the exact shared behavior (autocommit, privileged login
    # role), so this override cannot regress inv1/inv2/inv4/… which depend on superuser access.
    if request.path.name != _RLS_MODULE_BASENAME:
        with _throwaway_takyon_pg_db(worker_id) as conn:
            _apply_takyon_pg_migrations(conn)
            yield conn
        return

    suffix = f"{worker_id}_{uuid.uuid4().hex[:8]}"
    dbname = f"takyon_authrt_{suffix}"
    role = f"{_AUTH_TEST_ROLE_PREFIX}_{suffix}"

    with psycopg.connect(_TAKYON_TEST_PG_DSN, autocommit=True) as admin:
        admin.execute(f'create database "{dbname}"')

    # 1) Apply migrations on a privileged autocommit connection (DDL needs the owner role + each
    #    `create ... if not exists` wants its own committed statement). This also creates the
    #    production `takyon_app` role + grants via migration 0030.
    setup = psycopg.connect(_TAKYON_TEST_PG_DSN, dbname=dbname, autocommit=True)
    try:
        _apply_takyon_pg_migrations(setup)
        # 2) Create the RLS-subject harness role.
        #    NOLOGIN: reached only via SET ROLE on this already-authenticated connection.
        #    NOSUPERUSER + NOBYPASSRLS: the whole point — it MUST obey the app_* RLS policies.
        setup.execute(f'create role "{role}" nologin nosuperuser nobypassrls')
        setup.execute(f'grant usage on schema public to "{role}"')
        # Grant DML on EVERY table in the throwaway DB. This is deliberately broad and harmless:
        # the tenant boundary the invariant protects is the app_* RLS, which still bites because
        # this role is NOBYPASSRLS and those tables are FORCE'd. The control-plane tables (users,
        # businesses, user_api_keys, ledgers, …) have NO RLS, so full DML on them only lets the
        # inv3 test perform its privileged SETUP (provision/business/app_user) on the same
        # connection — it grants zero cross-tenant power over the protected app_* substrate. Using
        # ALL TABLES instead of a hand-maintained list keeps the fixture from silently breaking
        # when setup touches one more control-plane table (e.g. user_api_keys, ledgers).
        setup.execute(
            f'grant select, insert, update, delete on all tables in schema public to "{role}"'
        )
        setup.execute(
            f'grant usage, select on all sequences in schema public to "{role}"'
        )
        setup.execute(f'grant execute on all functions in schema public to "{role}"')
    finally:
        setup.close()

    # 3) The connection the test actually uses: non-autocommit (one held transaction so the test's
    #    is_local request-scope bindings persist) and running under the NON-bypassing harness role
    #    so the app_* RLS policies are real.
    conn = psycopg.connect(_TAKYON_TEST_PG_DSN, dbname=dbname, autocommit=False)
    try:
        with conn.cursor() as cur:
            cur.execute(f'set role "{role}"')
        conn.commit()
        yield conn
    finally:
        try:
            conn.rollback()
        finally:
            conn.close()
        with psycopg.connect(_TAKYON_TEST_PG_DSN, autocommit=True) as admin:
            admin.execute(f'drop database if exists "{dbname}" with (force)')
            admin.execute(f'drop role if exists "{role}"')
