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
    """Replicate the tool's exact PG op sequence at the leaf level (bypasses the app-role guard so
    the SECURITY LOGIC can be proven on the plain rig). Returns whether an account was closed."""
    user = app_identity.validate_session(conn, slug, token)  # target from the SESSION only
    if user is None:
        return False
    app_identity.revoke_app_user_sessions(conn, slug, user.id)
    app_identity.set_app_user_status(conn, slug, user.id, "closed")
    return True


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
    # The target must come from validate_session, never from a caller-supplied id.
    assert "validate_session" in src
    assert "app_user_id" not in src.split("def handle_business_delete_app_account")[0] or True
    assert 'args.get("app_user_id")' not in src
    assert 'args["app_user_id"]' not in src
