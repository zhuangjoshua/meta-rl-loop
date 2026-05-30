"""Postgres integration tests for the P1 identity-spine migration.

Uses the shared `pg_conn` fixture (per-worker throwaway DB with all migrations
applied); skips unless psycopg is importable and TAKYON_TEST_PG_DSN is set, so it
is a no-op where there is no Postgres.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest

psycopg = pytest.importorskip("psycopg")
from psycopg import errors as pg_errors  # noqa: E402

_MIGRATION = (
    Path(__file__).resolve().parents[2]
    / "plugins"
    / "takyon"
    / "db"
    / "migrations"
    / "0001_identity_spine.sql"
)


def _new_user(conn, auth0_sub: str | None = None, email: str | None = None):
    sub = auth0_sub or f"auth0|{uuid.uuid4().hex}"
    return conn.execute(
        "insert into users (auth0_sub, email) values (%s, %s) returning id",
        (sub, email),
    ).fetchone()[0]


def _add_key(conn, user_id, prefix: str):
    return conn.execute(
        "insert into user_api_keys (user_id, key_hash, prefix) "
        "values (%s, %s, %s) returning id",
        (user_id, uuid.uuid4().hex, prefix),
    ).fetchone()[0]


def test_migration_applies_idempotently(pg_conn):
    # re-running the migration must not error
    pg_conn.execute(_MIGRATION.read_text())
    tables = {
        r[0]
        for r in pg_conn.execute(
            "select table_name from information_schema.tables "
            "where table_schema = 'public'"
        ).fetchall()
    }
    assert {"users", "user_api_keys", "businesses"} <= tables


def test_only_one_active_key_per_user(pg_conn):
    uid = _new_user(pg_conn)
    _add_key(pg_conn, uid, "tk_aaaa1111")
    with pytest.raises(pg_errors.UniqueViolation):
        _add_key(pg_conn, uid, "tk_bbbb2222")


def test_rotation_revoke_then_issue(pg_conn):
    uid = _new_user(pg_conn)
    first = _add_key(pg_conn, uid, "tk_first000")
    pg_conn.execute(
        "update user_api_keys set revoked_at = now() where id = %s", (first,)
    )
    _add_key(pg_conn, uid, "tk_second00")  # allowed once the prior key is revoked
    active = pg_conn.execute(
        "select count(*) from user_api_keys where user_id = %s and revoked_at is null",
        (uid,),
    ).fetchone()[0]
    assert active == 1


def test_auth0_sub_is_case_insensitive_unique(pg_conn):
    sub = f"auth0|User{uuid.uuid4().hex}"
    _new_user(pg_conn, auth0_sub=sub)
    with pytest.raises(pg_errors.UniqueViolation):
        _new_user(pg_conn, auth0_sub=sub.upper())


def test_business_requires_existing_owner(pg_conn):
    with pytest.raises(pg_errors.ForeignKeyViolation):
        pg_conn.execute(
            "insert into businesses (slug, name, owner_user_id) values (%s, %s, %s)",
            (f"ghost-{uuid.uuid4().hex[:8]}", "Ghost", uuid.uuid4()),
        )


def test_business_owner_happy_path(pg_conn):
    uid = _new_user(pg_conn)
    slug = f"biz-{uuid.uuid4().hex[:8]}"
    pg_conn.execute(
        "insert into businesses (slug, name, owner_user_id) values (%s, %s, %s)",
        (slug, "Acme", uid),
    )
    owner = pg_conn.execute(
        "select owner_user_id from businesses where slug = %s", (slug,)
    ).fetchone()[0]
    assert owner == uid
    mode = pg_conn.execute(
        "select mode from businesses where slug = %s", (slug,)
    ).fetchone()[0]
    assert mode == "test"  # safe default
