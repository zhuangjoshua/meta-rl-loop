"""Postgres integration tests for the generic product profile rail.

The profile rail is intentionally small: it hangs one optional mutable profile row off the
existing ``app_users`` spine, keeps business scoping intact, and resolves the current user via
the same session rail as auth/account.
"""

from __future__ import annotations

import uuid

import pytest

from plugins.takyon import app_connections, app_directory, app_identity, app_profiles, app_records  # noqa: E402
from plugins.takyon.control_plane import provision_user_on_first_login  # noqa: E402

psycopg = pytest.importorskip("psycopg")


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


def test_upsert_profile_by_email_provisions_user_and_persists_fields(pg_conn):
    slug = _business(pg_conn, _owner(pg_conn))

    resolved = app_profiles.upsert_profile(
        pg_conn,
        slug,
        email="alice@example.com",
        display_name="Alice",
        headline="Ready to meet people",
        bio="Coffee first.",
        attributes={"intent": "dating"},
        metadata={"source": "test"},
    )

    assert resolved.user.email == "alice@example.com"
    assert resolved.profile is not None
    assert resolved.profile.id == resolved.user.id
    assert resolved.profile.display_name == "Alice"
    assert resolved.profile.headline == "Ready to meet people"
    assert resolved.profile.bio == "Coffee first."
    assert resolved.profile.attributes == {"intent": "dating"}
    assert resolved.profile.metadata == {"source": "test"}


def test_upsert_profile_preserves_omitted_fields(pg_conn):
    slug = _business(pg_conn, _owner(pg_conn))
    user = app_identity.upsert_app_user(pg_conn, slug, "preserve@example.com", name="Preserve")

    first = app_profiles.upsert_profile(
        pg_conn,
        slug,
        app_user_id=user.id,
        display_name="Original",
        headline="First headline",
        bio="First bio",
        attributes={"a": 1},
    )
    again = app_profiles.upsert_profile(
        pg_conn,
        slug,
        app_user_id=user.id,
        bio="Second bio",
    )

    assert first.profile is not None
    assert again.profile is not None
    assert again.profile.id == first.profile.id
    assert again.profile.display_name == "Original"
    assert again.profile.headline == "First headline"
    assert again.profile.bio == "Second bio"
    assert again.profile.attributes == {"a": 1}


def test_ensure_profile_creates_standard_one_to_one_row(pg_conn):
    slug = _business(pg_conn, _owner(pg_conn))
    user = app_identity.upsert_app_user(pg_conn, slug, "ensure@example.com", name="Ensure Me")

    resolved = app_profiles.ensure_profile(pg_conn, slug, app_user_id=user.id)

    assert resolved.profile is not None
    assert resolved.profile.id == user.id
    assert resolved.profile.display_name == "Ensure Me"


def test_get_profile_resolves_from_session_token(pg_conn):
    slug = _business(pg_conn, _owner(pg_conn))
    user = app_identity.upsert_app_user(pg_conn, slug, "session@example.com")
    _, session_token = app_identity.start_session(pg_conn, slug, user.id)
    app_profiles.upsert_profile(pg_conn, slug, app_user_id=user.id, display_name="Session User")

    resolved = app_profiles.get_profile(pg_conn, slug, session_token=session_token)

    assert resolved is not None
    assert resolved.user.id == user.id
    assert resolved.profile is not None
    assert resolved.profile.display_name == "Session User"


def test_profile_rejects_session_token_with_app_user_override(pg_conn):
    slug = _business(pg_conn, _owner(pg_conn))
    alice = app_identity.upsert_app_user(pg_conn, slug, "alice@example.com")
    bob = app_identity.upsert_app_user(pg_conn, slug, "bob@example.com")
    _, session_token = app_identity.start_session(pg_conn, slug, alice.id)

    with pytest.raises(ValueError, match="session_token is authoritative"):
        app_profiles.upsert_profile(
            pg_conn,
            slug,
            session_token=session_token,
            app_user_id=bob.id,
            display_name="Not Alice",
        )


def test_record_save_rejects_session_token_with_app_user_override(pg_conn):
    slug = _business(pg_conn, _owner(pg_conn))
    alice = app_identity.upsert_app_user(pg_conn, slug, "alice-records@example.com")
    bob = app_identity.upsert_app_user(pg_conn, slug, "bob-records@example.com")
    _, session_token = app_identity.start_session(pg_conn, slug, alice.id)

    with pytest.raises(ValueError, match="session_token is authoritative"):
        app_records.save_record(
            pg_conn,
            slug,
            record_type="note",
            data={"text": "wrong owner"},
            session_token=session_token,
            app_user_id=bob.id,
        )


def test_directory_write_rejects_session_token_with_app_user_override(pg_conn):
    slug = _business(pg_conn, _owner(pg_conn))
    alice = app_identity.upsert_app_user(pg_conn, slug, "alice-directory@example.com")
    bob = app_identity.upsert_app_user(pg_conn, slug, "bob-directory@example.com")
    _, session_token = app_identity.start_session(pg_conn, slug, alice.id)

    with pytest.raises(ValueError, match="session_token is authoritative"):
        app_directory.upsert_entry(
            pg_conn,
            slug,
            session_token=session_token,
            app_user_id=bob.id,
            display_name="Not Alice",
        )


def test_connection_action_rejects_session_token_with_app_user_override(pg_conn):
    slug = _business(pg_conn, _owner(pg_conn))
    alice = app_identity.upsert_app_user(pg_conn, slug, "alice-connections@example.com")
    bob = app_identity.upsert_app_user(pg_conn, slug, "bob-connections@example.com")
    target = app_identity.upsert_app_user(pg_conn, slug, "target-connections@example.com")
    _, session_token = app_identity.start_session(pg_conn, slug, alice.id)

    with pytest.raises(ValueError, match="session_token is authoritative"):
        app_connections.set_connection(
            pg_conn,
            slug,
            session_token=session_token,
            app_user_id=bob.id,
            target_app_user_id=target.id,
            action="block",
        )


def test_profiles_are_business_scoped(pg_conn):
    owner = _owner(pg_conn)
    slug_a = _business(pg_conn, owner, "A")
    slug_b = _business(pg_conn, owner, "B")

    profile_a = app_profiles.upsert_profile(
        pg_conn,
        slug_a,
        email="same@example.com",
        display_name="Alpha",
    )
    profile_b = app_profiles.upsert_profile(
        pg_conn,
        slug_b,
        email="same@example.com",
        display_name="Beta",
    )

    assert profile_a.user.id != profile_b.user.id
    assert profile_a.profile is not None
    assert profile_b.profile is not None
    assert profile_a.profile.display_name == "Alpha"
    assert profile_b.profile.display_name == "Beta"
