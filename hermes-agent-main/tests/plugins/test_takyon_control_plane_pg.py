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
from plugins.takyon.billing import get_billing_balances, open_billing_account  # noqa: E402
from plugins.takyon.control_plane import (  # noqa: E402
    _ensure_starter_allowance,
    get_or_create_user,
    mint_api_key,
    provision_user_on_first_login,
    resolve_api_key,
    rotate_api_key,
)
from plugins.takyon.custody import open_custody_account  # noqa: E402
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


def test_provision_grants_starter_allowance_to_existing_empty_account(pg_conn, monkeypatch):
    sub = _sub()
    monkeypatch.setenv("TAKYON_STARTER_ALLOWANCE_CENTS", "250")
    uid, _ = get_or_create_user(pg_conn, sub, "starter@example.com")
    mint_api_key(pg_conn, uid)
    open_billing_account(pg_conn, uid)
    open_custody_account(pg_conn, uid)

    resolved_uid, created, raw_key = provision_user_on_first_login(
        pg_conn,
        sub,
        "starter@example.com",
    )

    balances = get_billing_balances(pg_conn, uid)
    grants = pg_conn.execute(
        "select count(*) from billing_entries where user_id = %s and bucket = 'allowance' and kind = 'grant'",
        (uid,),
    ).fetchone()[0]
    assert resolved_uid == uid
    assert created is False
    assert raw_key is None
    assert balances.allowance_included_cents == 250
    assert balances.allowance_remaining_cents == 250
    assert grants == 1


def test_starter_allowance_degrades_to_warning_without_row_lock_privilege(pg_conn, monkeypatch, caplog):
    """UC3 dev-twin acceptance gap: the boot-time platform-owner seed's starter-allowance precheck
    does ``SELECT … FOR UPDATE`` on billing_accounts, which needs table UPDATE — revoked from
    ``takyon_operator_runtime`` by migration 0044 (and never restored by 0045+/topology.sql: money
    writes go through the Safebox authority / SECURITY DEFINER ports, see 0056). So on the operator
    plane — prod and the dev twin alike — the precheck raises insufficient_privilege. It must
    degrade to the documented cosmetic warning (return 0, no raise), never fail provisioning."""
    import logging

    sub = _sub()
    monkeypatch.setenv("TAKYON_STARTER_ALLOWANCE_CENTS", "250")
    uid, _ = get_or_create_user(pg_conn, sub, "locked@example.com")

    pg_conn.execute("set role takyon_operator_runtime")
    try:
        with caplog.at_level(logging.WARNING, logger="plugins.takyon.control_plane"):
            granted = _ensure_starter_allowance(pg_conn, uid)
    finally:
        pg_conn.execute("reset role")

    assert granted == 0
    assert any(
        "starter allowance skipped" in rec.message and "billing_accounts" in rec.message
        for rec in caplog.records
    ), "the degrade must be a visible, documented warning"
    # Nothing moved: no allowance grant entry exists for this user.
    grants = pg_conn.execute(
        "select count(*) from billing_entries where user_id = %s",
        (uid,),
    ).fetchone()[0]
    assert grants == 0


def test_starter_allowance_non_privilege_errors_still_raise(pg_conn, monkeypatch):
    """Only insufficient_privilege degrades. A genuinely missing billing account (provisioning
    invariant violation) keeps raising — the degrade must not become a blanket swallow."""
    sub = _sub()
    monkeypatch.setenv("TAKYON_STARTER_ALLOWANCE_CENTS", "250")
    uid, _ = get_or_create_user(pg_conn, sub, "no-account@example.com")
    with pytest.raises(RuntimeError, match="billing account missing"):
        _ensure_starter_allowance(pg_conn, uid)


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
