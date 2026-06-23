from __future__ import annotations

import json
import os
import time
import uuid

import jwt
import pytest
from psycopg.conninfo import make_conninfo

from plugins.takyon import app_identity, app_supabase_auth
from plugins.takyon.control_plane import provision_user_on_first_login

SECRET = "test-supabase-jwt-secret-0123456789"


def _sub() -> str:
    return f"auth0|{uuid.uuid4().hex}"


def _owner(conn) -> str:
    uid, _, _ = provision_user_on_first_login(conn, _sub())
    return uid


def _business(conn, owner_id, name="Acme") -> str:
    slug = f"biz-{uuid.uuid4().hex[:8]}"
    conn.execute(
        "insert into businesses (slug, name, owner_user_id) values (%s, %s, %s)",
        (slug, name, owner_id),
    )
    return slug


def _token(sub: str, email: str) -> str:
    return jwt.encode(
        {
            "sub": sub,
            "email": email,
            "email_verified": True,
            "aud": "authenticated",
            "exp": int(time.time()) + 600,
        },
        SECRET,
        algorithm="HS256",
    )


def test_supabase_login_adopts_legacy_user_and_mints_real_session(pg_conn, tmp_path, monkeypatch):
    from plugins.takyon import core as takyon_core

    owner = _owner(pg_conn)
    slug = _business(pg_conn, owner)
    legacy = app_identity.upsert_app_user(pg_conn, slug, "legacy@example.com", name="Legacy User")

    dsn = make_conninfo(os.environ["TAKYON_TEST_PG_DSN"], dbname=pg_conn.info.dbname)
    store = takyon_core.TakyonStore(root=tmp_path, database_url=dsn)

    monkeypatch.setattr(takyon_core, "load_takyon_env", lambda *a, **k: None)
    monkeypatch.setattr(takyon_core, "_store", lambda: store)
    monkeypatch.setenv("TAKYON_DB_BACKEND", "postgres")
    monkeypatch.setenv("TAKYON_ALLOW_POSTGRES_OUTSIDE_VPS", "1")
    # Verification is now server-side (alg-confusion fix): the runtime no longer trusts a local
    # symmetric SUPABASE_JWT_SECRET, so the HS test token is validated through a mocked Supabase Auth
    # server rather than verified locally with the env secret.
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setattr(app_supabase_auth, "_publishable_key", lambda: "sb_publishable_test")
    monkeypatch.setattr(
        app_supabase_auth,
        "_verified_user_via_auth_server",
        lambda _t, *, project_url, publishable_key: {
            "id": "11111111-1111-1111-1111-111111111111",
            "email": "legacy@example.com",
            "email_confirmed_at": "2026-06-21T00:00:00Z",
        },
    )

    payload = json.loads(
        takyon_core.handle_business_supabase_login(
            {
                "business": slug,
                "access_token": _token("11111111-1111-1111-1111-111111111111", "legacy@example.com"),
                "name": "Legacy User",
            }
        )
    )

    assert payload["success"] is True
    assert payload["app_user_id"] == legacy.id
    assert payload["email"] == "legacy@example.com"
    assert payload["session_token"]

    session = json.loads(
        takyon_core.handle_business_read_app_session(
            {"business": slug, "session_token": payload["session_token"]}
        )
    )
    account = json.loads(
        takyon_core.handle_business_read_app_account(
            {"business": slug, "session_token": payload["session_token"]}
        )
    )

    assert session["success"] is True
    assert session["authenticated"] is True
    assert session["user"]["id"] == legacy.id
    assert account["success"] is True
    assert account["user"]["id"] == legacy.id
    assert account["user"]["tier"] == "unentitled"
    assert account["entitlements"] == []

    profile_row = pg_conn.execute(
        "select display_name from app_user_profiles where business_slug = %s and id = %s",
        (slug, legacy.id),
    ).fetchone()
    assert profile_row is not None
    assert profile_row[0] == "Legacy User"

    adopted_row = pg_conn.execute(
        "select supabase_user_id from app_users where business_slug = %s and id = %s",
        (slug, legacy.id),
    ).fetchone()
    assert str(adopted_row[0]) == "11111111-1111-1111-1111-111111111111"
