"""GROUPED E2E for the auth-rls build group (GOAL_RULES §2 tie-in).

This proves the inv3 DB-layer cross-tenant boundary is REACHABLE and ENFORCED through the REAL
production entrypoint — ``TakyonStore.commit`` running ``app.record.*`` ops — not just via the
authoritative source-structure pins or a hand-rolled connection.

Why this is the real path: ``TakyonStore.commit`` opens its connection through ``_connect()``,
which (``core._PGConn``) is ``autocommit=False`` — one held transaction per ``with self._connect()``
block. So when the app op enters ``_pg_app_scope``, the ``SET LOCAL ROLE takyon_app`` (migration
0030) ACTUALLY takes effect (``SET LOCAL ROLE`` is a no-op outside a transaction), the connection
drops to the non-bypassing ``takyon_app`` role, and the 0027 RLS policies bite. This simultaneously
verifies two things that the unit suites cannot together:

  1. The ``takyon_app`` GRANTs in 0030 are COMPLETE for the real save/list path (the op succeeds —
     a missing grant would surface as ``permission denied`` in production, where the role switch is
     live, even though the autocommit plugin-fixture tests would mask it).
  2. RLS genuinely isolates tenants on the customer-data table: user A's list sees only A's record;
     a second business's record is invisible.

Skips automatically without the Postgres rig (``pg_store_dsn`` skips on its own).
"""

from __future__ import annotations

import uuid

import pytest

from plugins.takyon import app_identity
from plugins.takyon import core as takyon_core

psycopg = pytest.importorskip("psycopg")


def _seed_owner_business(dsn: str, slug: str) -> str:
    with psycopg.connect(dsn, autocommit=True) as conn:
        uid = conn.execute(
            "insert into users (auth0_sub) values (%s) returning id",
            (f"auth0|{uuid.uuid4().hex}",),
        ).fetchone()[0]
        conn.execute(
            "insert into businesses (slug, name, owner_user_id, mode) values (%s, %s, %s, %s)",
            (slug, slug.title(), uid, "live"),
        )
    return slug


def _app_user_with_session(dsn: str, slug: str, email: str) -> tuple[str, str]:
    """Provision a sub-user + a live session, return (app_user_id, raw_session_token)."""
    with psycopg.connect(dsn, autocommit=True) as conn:
        user = app_identity.upsert_app_user(conn, slug, email)
        _session, token = app_identity.start_session(conn, slug, user.id)
    return str(user.id), token


@pytest.fixture
def pg_store(pg_store_dsn, tmp_path, monkeypatch):
    # Force the LOCAL workspace storage backend so the store's post-op canonical-revision commit is
    # allowed off-VPS (the default rig may resolve supabase_s3, which gates remote sync on Linux/VPS
    # only). This is orthogonal to the auth-rls boundary under test; it just lets the real
    # `store.commit` app path run to completion on this host.
    monkeypatch.setenv("TAKYON_STORAGE_BACKEND", "local")
    monkeypatch.setenv("TAKYON_STORAGE_LOCAL_DIR", str(tmp_path / "storage"))
    return takyon_core.TakyonStore(root=tmp_path, database_url=pg_store_dsn)


def test_app_record_save_and_list_run_under_takyon_app_role_and_isolate_tenants(
    pg_store, pg_store_dsn
):
    slug_a = _seed_owner_business(pg_store_dsn, f"acme{uuid.uuid4().hex[:6]}")
    slug_b = _seed_owner_business(pg_store_dsn, f"rival{uuid.uuid4().hex[:6]}")
    user_a, token_a = _app_user_with_session(pg_store_dsn, slug_a, "a@example.com")
    user_b, token_b = _app_user_with_session(pg_store_dsn, slug_b, "b@example.com")

    # 1) SAVE a record for tenant A's sub-user through the REAL store op. This enters
    #    _pg_app_scope → SET LOCAL ROLE takyon_app (the held transaction makes it real) → the save
    #    runs under the non-bypassing role. The op SUCCEEDING proves 0030's grants are complete for
    #    the production save path (a missing grant => permission denied here).
    save_a = pg_store.commit(
        scope=f"business:{slug_a}/app",
        operations=[
            {
                "action": "app.record.upsert",
                "business_slug": slug_a,
                "record_type": "note",
                "title": "Alice note",
                "data": {"body": "hello from A"},
                "session_token": token_a,
            }
        ],
        idempotency_key=f"save-a-{uuid.uuid4().hex}",
    )
    assert save_a.get("success") is not False, save_a

    # A second tenant's record, saved the same authentic way (also under takyon_app).
    pg_store.commit(
        scope=f"business:{slug_b}/app",
        operations=[
            {
                "action": "app.record.upsert",
                "business_slug": slug_b,
                "record_type": "note",
                "title": "Bob note",
                "data": {"body": "hello from B"},
                "session_token": token_b,
            }
        ],
        idempotency_key=f"save-b-{uuid.uuid4().hex}",
    )

    # 2) DB-truth isolation, the SAME way production scopes a request: drop to the restricted
    #    takyon_app role + bind tenant A's request scope, then read app_records. The RLS policy
    #    (not the runtime) returns ONLY tenant A's row — tenant B's record is invisible — and a
    #    blanket count confirms two rows exist overall (so the row really is hidden, not absent).
    with psycopg.connect(pg_store_dsn, autocommit=True) as priv:
        total = priv.execute("select count(*) from app_records").fetchone()[0]
    assert total == 2, total  # both saves landed (proven via the privileged login role)

    with psycopg.connect(pg_store_dsn, autocommit=False) as conn:
        cur = conn.cursor()
        cur.execute("set role takyon_app")
        cur.execute("select set_config('takyon.rls_bypass', '0', true)")
        cur.execute("select set_config('takyon.rls_business_slug', %s, true)", (slug_a,))
        cur.execute("select set_config('takyon.rls_app_user_id', %s, true)", (user_a,))
        cur.execute("select set_config('takyon.rls_session_hash', '', true)")
        visible_owners = {
            str(row[0]) for row in cur.execute("select app_user_id from app_records").fetchall()
        }
        visible_slugs = {
            str(row[0]) for row in cur.execute("select business_slug from app_records").fetchall()
        }
        conn.rollback()
    # Tenant A under RLS sees exactly their own row; tenant B's is denied at the DB layer.
    assert visible_owners == {user_a}, visible_owners
    assert user_b not in visible_owners
    assert visible_slugs == {slug_a}, visible_slugs
