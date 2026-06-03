"""Postgres integration tests for the opaque API key resolver and control-plane
access helpers. Uses the shared `pg_conn` fixture (per-worker throwaway DB);
skips unless psycopg is importable and TAKYON_TEST_PG_DSN is set.
"""

from __future__ import annotations

import uuid

import pytest

psycopg = pytest.importorskip("psycopg")
from psycopg import errors as pg_errors  # noqa: E402

from plugins.takyon import safebox  # noqa: E402
from plugins.takyon.control_plane import (  # noqa: E402
    get_or_create_user,
    mint_api_key,
    resolve_api_key,
    rotate_api_key,
)
from plugins.takyon.user_api_keys import generate_api_key, is_well_formed  # noqa: E402


def _sub() -> str:
    return f"auth0|{uuid.uuid4().hex}"


def test_jit_provision_is_idempotent(pg_conn):
    sub = _sub()
    uid1, created1 = get_or_create_user(pg_conn, sub, "a@example.com")
    uid2, created2 = get_or_create_user(pg_conn, sub, "a@example.com")
    assert created1 is True
    assert created2 is False
    assert uid1 == uid2


def test_mint_then_resolve_round_trip(pg_conn):
    uid, _ = get_or_create_user(pg_conn, _sub())
    raw = mint_api_key(pg_conn, uid)
    assert is_well_formed(raw)
    principal = resolve_api_key(pg_conn, raw)
    assert principal is not None
    assert principal.user_id == uid
    assert principal.business_slugs == ()


def test_resolve_reflects_ownership(pg_conn):
    uid, _ = get_or_create_user(pg_conn, _sub())
    raw = mint_api_key(pg_conn, uid)
    slug = f"biz-{uuid.uuid4().hex[:8]}"
    pg_conn.execute(
        "insert into businesses (slug, name, owner_user_id) values (%s, %s, %s)",
        (slug, "Acme", uid),
    )
    principal = resolve_api_key(pg_conn, raw)
    assert principal is not None
    assert slug in principal.business_slugs


def test_resolve_rejects_garbage_and_unknown(pg_conn):
    assert resolve_api_key(pg_conn, "not-a-key") is None
    # well-formed but never minted -> unknown
    assert resolve_api_key(pg_conn, generate_api_key()) is None


def test_resolve_rejects_revoked_key(pg_conn):
    uid, _ = get_or_create_user(pg_conn, _sub())
    raw = mint_api_key(pg_conn, uid)
    principal = resolve_api_key(pg_conn, raw)
    assert principal is not None
    assert safebox.revoke_user_api_key(principal.key_id) is True
    assert resolve_api_key(pg_conn, raw) is None


def test_db_revoke_alone_does_not_become_auth_authority(pg_conn):
    uid, _ = get_or_create_user(pg_conn, _sub())
    raw = mint_api_key(pg_conn, uid)
    pg_conn.execute(
        "update user_api_keys set revoked_at = now() where user_id = %s", (uid,)
    )
    principal = resolve_api_key(pg_conn, raw)
    assert principal is not None
    assert principal.user_id == uid


def test_mint_twice_violates_one_active(pg_conn):
    uid, _ = get_or_create_user(pg_conn, _sub())
    mint_api_key(pg_conn, uid)
    with pytest.raises(pg_errors.UniqueViolation):
        mint_api_key(pg_conn, uid)


def test_rotate_revokes_old_and_issues_new(pg_conn):
    uid, _ = get_or_create_user(pg_conn, _sub())
    old = mint_api_key(pg_conn, uid)
    new = rotate_api_key(pg_conn, uid)
    assert old != new
    assert resolve_api_key(pg_conn, old) is None
    principal = resolve_api_key(pg_conn, new)
    assert principal is not None and principal.user_id == uid
    active = pg_conn.execute(
        "select count(*) from user_api_keys where user_id = %s and revoked_at is null",
        (uid,),
    ).fetchone()[0]
    assert active == 1


def test_resolve_rejects_non_active_user(pg_conn):
    uid, _ = get_or_create_user(pg_conn, _sub())
    raw = mint_api_key(pg_conn, uid)
    pg_conn.execute("update users set status = 'suspended' where id = %s", (uid,))
    assert resolve_api_key(pg_conn, raw) is None


def test_resolve_stamps_last_used_at(pg_conn):
    uid, _ = get_or_create_user(pg_conn, _sub())
    raw = mint_api_key(pg_conn, uid)
    before = pg_conn.execute(
        "select last_used_at from user_api_keys where user_id = %s", (uid,)
    ).fetchone()[0]
    assert before is None
    resolve_api_key(pg_conn, raw)
    after = pg_conn.execute(
        "select last_used_at from user_api_keys where user_id = %s", (uid,)
    ).fetchone()[0]
    assert after is not None
