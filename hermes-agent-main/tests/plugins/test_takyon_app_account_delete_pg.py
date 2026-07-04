"""Sub-user account deletion (Apple 5.1.1(v)) — security tests.

The App Store rail's ONE subuser-plane addition. Load-bearing for the operator's hard constraint
"do not make the subuser plane any less secure". Three layers of proof:

  1. LEAF COMPOSITION (pg rig): the exact op sequence the tool performs — validate_session →
     revoke_app_user_sessions → set_app_user_status('closed') — is IDOR-safe (deleting via A's
     token closes ONLY A, even for a second user B in the SAME business), cross-business isolated,
     and idempotent.
  2. TOOL PLANE-GATING (pg rig): handle_business_delete_app_account REFUSES off the app-plane DB
     login (operator-plane store) — it can only run under the app-runtime SECURITY DEFINER port,
     exactly like delete_app_session. And a missing token is a clean error.
  3. STRUCTURAL NO-IDOR (source): the tool derives the target user ONLY from the validated session,
     never from a body-supplied app_user_id.

The full tool-through-app-role E2E is the dashboard/dev-twin acceptance gate (no rig test enters
the app plane). Skips unless psycopg + TAKYON_TEST_PG_DSN.
"""

from __future__ import annotations

import inspect
import json
import tempfile
import uuid

import pytest

psycopg = pytest.importorskip("psycopg")

from plugins.takyon import app_identity  # noqa: E402
from plugins.takyon import core as takyon_core  # noqa: E402


def _owner(conn) -> str:
    uid = str(uuid.uuid4())
    conn.execute("insert into users (id, auth0_sub) values (%s, %s)", (uid, f"auth0|{uuid.uuid4().hex}"))
    return uid


def _business(conn, owner_id) -> str:
    slug = f"biz-{uuid.uuid4().hex[:8]}"
    conn.execute(
        "insert into businesses (slug, name, owner_user_id) values (%s, %s, %s)",
        (slug, slug, owner_id),
    )
    return slug


def _user_with_session(conn, slug: str, email: str):
    user = app_identity.upsert_app_user(conn, slug, email)
    _session, token = app_identity.start_session(conn, slug, user.id)
    return user, token


def _tool_close(conn, slug: str, token: str) -> bool:
    """Exercise the REAL leaf function the tool calls (owner path on the rig). Returns whether an
    account was closed."""
    _uid, closed = app_identity.close_app_account(conn, slug, token)
    return closed


def _status(conn, slug, user_id):
    return conn.execute(
        "select status from app_users where business_slug = %s and id = %s", (slug, user_id)
    ).fetchone()[0]


# ── 1. leaf composition: IDOR-safety, isolation, idempotency ──────────────────────────


def test_close_own_account_invalidates_session_and_closes_user(pg_conn):
    slug = _business(pg_conn, _owner(pg_conn))
    user_a, token_a = _user_with_session(pg_conn, slug, "a@example.com")
    assert app_identity.validate_session(pg_conn, slug, token_a) is not None
    assert _tool_close(pg_conn, slug, token_a) is True
    assert app_identity.validate_session(pg_conn, slug, token_a) is None  # session dead
    assert _status(pg_conn, slug, user_a.id) == "closed"


def test_no_idor_same_business_second_user_untouched(pg_conn):
    """THE pin: A's token closes only A; B in the SAME business is fully untouched."""
    slug = _business(pg_conn, _owner(pg_conn))
    _user_a, token_a = _user_with_session(pg_conn, slug, "a@example.com")
    user_b, token_b = _user_with_session(pg_conn, slug, "b@example.com")
    _tool_close(pg_conn, slug, token_a)
    assert app_identity.validate_session(pg_conn, slug, token_b) is not None  # B still logged in
    assert _status(pg_conn, slug, user_b.id) == "active"


def test_cross_business_isolation(pg_conn):
    owner = _owner(pg_conn)
    slug_x = _business(pg_conn, owner)
    slug_y = _business(pg_conn, owner)
    _ux, token_x = _user_with_session(pg_conn, slug_x, "x@example.com")
    user_y, token_y = _user_with_session(pg_conn, slug_y, "y@example.com")
    _tool_close(pg_conn, slug_x, token_x)
    assert app_identity.validate_session(pg_conn, slug_y, token_y) is not None
    assert _status(pg_conn, slug_y, user_y.id) == "active"


def test_idempotent_on_stale_token(pg_conn):
    slug = _business(pg_conn, _owner(pg_conn))
    _u, token = _user_with_session(pg_conn, slug, "a@example.com")
    assert _tool_close(pg_conn, slug, token) is True
    # Second close with the now-invalid token: nothing to do, no error.
    assert _tool_close(pg_conn, slug, token) is False


def test_anonymizes_email_for_fresh_resignup(pg_conn):
    slug = _business(pg_conn, _owner(pg_conn))
    user, token = _user_with_session(pg_conn, slug, "reuse@example.com")
    _tool_close(pg_conn, slug, token)
    row = pg_conn.execute(
        "select email, status from app_users where business_slug = %s and id = %s", (slug, user.id)
    ).fetchone()
    assert row[1] == "closed"
    assert str(row[0]) != "reuse@example.com"  # tombstoned → the address is free again
    # The original email is now free: a new user can take it (no unique collision).
    fresh = app_identity.upsert_app_user(pg_conn, slug, "reuse@example.com")
    assert fresh.id != user.id


# ── the REAL app-plane proof: the SECURITY DEFINER port under takyon_app_runtime ──────


def test_close_account_port_works_under_app_role_and_direct_dml_denied(pg_conn):
    """The critical proof the owner-path tests can't give: on the actual app-runtime role, direct
    DML on app_sessions/app_users is DENIED (0045), and the account close only succeeds through the
    takyon_app_close_account SECURITY DEFINER port. This is why the tool must use the port."""
    from plugins.takyon.app_identity import _hash_token

    if pg_conn.execute("select to_regrole('takyon_app_runtime')").fetchone()[0] is None:
        pytest.skip("takyon_app_runtime role not present in this rig")
    slug = _business(pg_conn, _owner(pg_conn))
    user, token = _user_with_session(pg_conn, slug, "a@example.com")

    pg_conn.execute("set role takyon_app_runtime")
    try:
        # Direct UPDATE on the identity tables is denied for the app role (0045 revoked DML).
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            pg_conn.execute(
                "update app_sessions set revoked_at = now() where business_slug = %s", (slug,)
            )
        # But the bounded port succeeds for the same role (EXECUTE granted; runs SECURITY DEFINER).
        row = pg_conn.execute(
            "select * from takyon_app_close_account(%s, %s)",
            (slug, _hash_token(token)),
        ).fetchone()
        assert str(row[0]) == user.id and row[1] is True
    finally:
        pg_conn.execute("reset role")
    assert _status(pg_conn, slug, user.id) == "closed"


def test_close_account_port_direct_call_closes_and_scopes(pg_conn):
    """Call the port function directly (as owner — the function is SECURITY DEFINER either way) and
    prove it closes exactly the session's user and is self-scoping."""
    from plugins.takyon.app_identity import _hash_token

    slug = _business(pg_conn, _owner(pg_conn))
    user_a, token_a = _user_with_session(pg_conn, slug, "a@example.com")
    user_b, _token_b = _user_with_session(pg_conn, slug, "b@example.com")
    row = pg_conn.execute(
        "select * from takyon_app_close_account(%s, %s)",
        (slug, _hash_token(token_a)),
    ).fetchone()
    assert str(row[0]) == user_a.id and row[1] is True
    assert _status(pg_conn, slug, user_a.id) == "closed"
    assert _status(pg_conn, slug, user_b.id) == "active"  # B untouched by A's session hash
    # Bogus hash closes nothing.
    none_row = pg_conn.execute(
        "select * from takyon_app_close_account(%s, %s)", (slug, "deadbeef")
    ).fetchone()
    assert none_row[0] is None and none_row[1] is False


# ── 2. tool plane-gating (the tool refuses off the app plane) ─────────────────────────


def _operator_store(pg_conn, monkeypatch):
    from psycopg.conninfo import make_conninfo
    import os

    dsn = make_conninfo(os.environ["TAKYON_TEST_PG_DSN"], dbname=pg_conn.info.dbname)
    store = takyon_core.TakyonStore(root=tempfile.mkdtemp(), database_url=dsn)  # default plane=operator
    monkeypatch.setattr(takyon_core, "load_takyon_env", lambda *a, **k: None)
    monkeypatch.setattr(takyon_core, "_store", lambda: store)
    monkeypatch.setenv("TAKYON_DB_BACKEND", "postgres")
    monkeypatch.setenv("TAKYON_ALLOW_POSTGRES_OUTSIDE_VPS", "1")
    return store


def test_tool_refuses_off_app_plane(pg_conn, monkeypatch):
    _operator_store(pg_conn, monkeypatch)
    slug = _business(pg_conn, _owner(pg_conn))
    _u, token = _user_with_session(pg_conn, slug, "a@example.com")
    res = json.loads(
        takyon_core.handle_business_delete_app_account({"business": slug, "session_token": token})
    )
    # Operator-plane store → the app-plane guard fires; deletion never runs.
    assert res["success"] is False
    assert "app-plane" in res.get("error", "")
    # And the user is NOT closed (the guard fired before any mutation).
    assert _status(pg_conn, slug, _u.id) == "active"


def test_tool_missing_token_is_error(pg_conn, monkeypatch):
    _operator_store(pg_conn, monkeypatch)
    slug = _business(pg_conn, _owner(pg_conn))
    res = json.loads(
        takyon_core.handle_business_delete_app_account({"business": slug, "session_token": ""})
    )
    assert res["success"] is False


# ── 3. structural no-IDOR: target derived only from the validated session ─────────────


def test_tool_never_reads_body_app_user_id():
    src = inspect.getsource(takyon_core.handle_business_delete_app_account)
    # The target must come from the session-scoped close port, never a caller-supplied id.
    assert "close_app_account" in src
    assert 'args.get("app_user_id")' not in src
    assert 'args["app_user_id"]' not in src
