"""Postgres integration tests for product sub-user identity.

Product sub-users are scoped by business_slug and authenticate through Supabase before
Takyon mints an app session. The sharp correctness details pinned here:
  * sessions are business-scoped — a token minted under one business never validates
    under another;
  * raw tokens are never stored, only their SHA-256 hash.

Real engine on real Postgres (never mocks). Concurrency tests open their own extra
connections to the same per-worker throwaway DB. Skips unless psycopg is importable and
TAKYON_TEST_PG_DSN is set.
"""

from __future__ import annotations

import uuid

import pytest

psycopg = pytest.importorskip("psycopg")

from plugins.takyon import app_identity  # noqa: E402
from plugins.takyon.app_identity import (  # noqa: E402
    InactiveAppUser,
    InvalidEmail,
)
from plugins.takyon.control_plane import provision_user_on_first_login  # noqa: E402


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


def _session_for_email(conn, slug: str, email: str):
    user = app_identity.upsert_app_user(conn, slug, email)
    session, token = app_identity.start_session(conn, slug, user.id)
    return user, session, token


# ── upsert_app_user ─────────────────────────────────────────────────────────────


def test_upsert_creates_active_unentitled_user(pg_conn):
    slug = _business(pg_conn, _owner(pg_conn))
    user = app_identity.upsert_app_user(pg_conn, slug, "Alice@Example.com", name="Alice")
    assert user.business_slug == slug
    assert user.email == "alice@example.com"  # normalized to lowercase
    assert user.name == "Alice"
    assert user.status == "active"
    assert user.tier == "unentitled"  # documented default


def test_upsert_idempotent_same_id_reactivates_and_preserves_name(pg_conn):
    slug = _business(pg_conn, _owner(pg_conn))
    first = app_identity.upsert_app_user(pg_conn, slug, "bob@example.com", name="Bob")
    pg_conn.execute("update app_users set status = 'suspended' where id = %s", (first.id,))
    # Re-request with no explicit status keeps the existing suspension and original name.
    again = app_identity.upsert_app_user(pg_conn, slug, "bob@example.com")
    assert again.id == first.id  # upsert on (business_slug, email), not a new row
    assert again.status == "suspended"
    assert again.name == "Bob"  # coalesce keeps the existing name when none is given


def test_upsert_explicit_status_can_reactivate(pg_conn):
    slug = _business(pg_conn, _owner(pg_conn))
    first = app_identity.upsert_app_user(pg_conn, slug, "reactivate@example.com")
    pg_conn.execute("update app_users set status = 'suspended' where id = %s", (first.id,))
    again = app_identity.upsert_app_user(
        pg_conn, slug, "reactivate@example.com", status="active"
    )
    assert again.id == first.id
    assert again.status == "active"


def test_upsert_normalizes_email_so_case_variants_collapse(pg_conn):
    slug = _business(pg_conn, _owner(pg_conn))
    a = app_identity.upsert_app_user(pg_conn, slug, "Carol@Example.com")
    b = app_identity.upsert_app_user(pg_conn, slug, "carol@example.com")
    assert a.id == b.id  # case-insensitive identity, one sub-user not two
    rows = pg_conn.execute(
        "select count(*) from app_users where business_slug = %s", (slug,)
    ).fetchone()[0]
    assert rows == 1


def test_upsert_rejects_bad_email(pg_conn):
    slug = _business(pg_conn, _owner(pg_conn))
    with pytest.raises(InvalidEmail):
        app_identity.upsert_app_user(pg_conn, slug, "not-an-email")


def test_upsert_unknown_business_fails_loud(pg_conn):
    # FK to businesses(slug) — a sub-user can't attach to a business that doesn't exist.
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        app_identity.upsert_app_user(pg_conn, "ghost-biz", "dave@example.com")


def test_same_email_is_a_distinct_user_per_business(pg_conn):
    owner = _owner(pg_conn)
    slug_a = _business(pg_conn, owner, "A")
    slug_b = _business(pg_conn, owner, "B")
    a = app_identity.upsert_app_user(pg_conn, slug_a, "eve@example.com")
    b = app_identity.upsert_app_user(pg_conn, slug_b, "eve@example.com")
    assert a.id != b.id  # the same person is two separate customers across businesses


# ── start_session ───────────────────────────────────────────────────────────────


def test_start_session_opens_session_that_validates_to_the_user(pg_conn):
    slug = _business(pg_conn, _owner(pg_conn))
    user = app_identity.upsert_app_user(pg_conn, slug, "grace@example.com")
    session, session_token = app_identity.start_session(pg_conn, slug, user.id)
    assert session.app_user_id == user.id
    who = app_identity.validate_session(pg_conn, slug, session_token)
    assert who is not None
    assert who.email == "grace@example.com"
    assert who.id == user.id


def test_start_session_rejects_nonpositive_ttl(pg_conn):
    slug = _business(pg_conn, _owner(pg_conn))
    user = app_identity.upsert_app_user(pg_conn, slug, "ttl@example.com")
    with pytest.raises(ValueError):
        app_identity.start_session(pg_conn, slug, user.id, session_ttl_days=0)


def test_start_session_rejects_inactive_user(pg_conn):
    slug = _business(pg_conn, _owner(pg_conn))
    user = app_identity.upsert_app_user(pg_conn, slug, "inactive@example.com")
    app_identity.set_app_user_status(pg_conn, slug, user.id, "suspended")
    with pytest.raises(InactiveAppUser):
        app_identity.start_session(pg_conn, slug, user.id)


# ── validate_session / revoke_session ───────────────────────────────────────────


def test_validate_rejects_revoked_session(pg_conn):
    slug = _business(pg_conn, _owner(pg_conn))
    _, _, token = _session_for_email(pg_conn, slug, "nina@example.com")
    assert app_identity.revoke_session(pg_conn, slug, token) is True
    assert app_identity.validate_session(pg_conn, slug, token) is None


def test_set_status_revokes_live_sessions(pg_conn):
    slug = _business(pg_conn, _owner(pg_conn))
    user, _, token = _session_for_email(pg_conn, slug, "kill@example.com")
    updated = app_identity.set_app_user_status(pg_conn, slug, user.id, "closed")
    assert updated.status == "closed"
    assert app_identity.validate_session(pg_conn, slug, token) is None


def test_validate_rejects_expired_session(pg_conn):
    slug = _business(pg_conn, _owner(pg_conn))
    _, session, token = _session_for_email(pg_conn, slug, "olga@example.com")
    pg_conn.execute(
        "update app_sessions set expires_at = now() - interval '1 second' where id = %s",
        (session.id,),
    )
    assert app_identity.validate_session(pg_conn, slug, token) is None


def test_session_is_business_scoped(pg_conn):
    owner = _owner(pg_conn)
    slug_a = _business(pg_conn, owner, "A")
    slug_b = _business(pg_conn, owner, "B")
    _, _, token = _session_for_email(pg_conn, slug_a, "peter@example.com")
    assert app_identity.validate_session(pg_conn, slug_a, token) is not None
    # the same token must NOT validate under a different business
    assert app_identity.validate_session(pg_conn, slug_b, token) is None


def test_revoke_is_idempotent(pg_conn):
    slug = _business(pg_conn, _owner(pg_conn))
    _, _, token = _session_for_email(pg_conn, slug, "quinn@example.com")
    assert app_identity.revoke_session(pg_conn, slug, token) is True
    assert app_identity.revoke_session(pg_conn, slug, token) is False  # nothing live left


def test_validate_and_revoke_tolerate_garbage_tokens(pg_conn):
    slug = _business(pg_conn, _owner(pg_conn))
    assert app_identity.validate_session(pg_conn, slug, "") is None
    assert app_identity.validate_session(pg_conn, slug, "garbage") is None
    assert app_identity.revoke_session(pg_conn, slug, "") is False
    assert app_identity.revoke_session(pg_conn, slug, "garbage") is False
