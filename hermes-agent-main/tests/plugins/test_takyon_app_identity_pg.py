"""Postgres integration tests for product sub-user identity — Phase 5 (increment a).

Phase 5 acceptance (this slice): a business's customers (product sub-users) get
magic-link auth and bearer sessions on Postgres, fully scoped by business_slug. The
sharp correctness details pinned here:
  * a magic link is SINGLE-USE even under concurrency — the redemption is one atomic
    `update ... where used_at is null returning`, so two simultaneous clicks yield
    exactly one session (the SQLite read-then-write version could double-redeem);
  * verify is ATOMIC — if the resolved sub-user is inactive the whole redemption rolls
    back, so the link survives for a later (reactivated) attempt;
  * sessions are business-scoped — a token minted under one business never validates
    under another;
  * raw tokens are never stored, only their SHA-256 hash.

Real engine on real Postgres (never mocks). Concurrency tests open their own extra
connections to the same per-worker throwaway DB. Skips unless psycopg is importable and
TAKYON_TEST_PG_DSN is set.
"""

from __future__ import annotations

import hashlib
import os
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest

psycopg = pytest.importorskip("psycopg")

from plugins.takyon import app_identity  # noqa: E402
from plugins.takyon.app_identity import (  # noqa: E402
    InactiveAppUser,
    InvalidEmail,
    InvalidMagicLink,
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


def _new_conn(pg_conn):
    """A fresh autocommit connection to the SAME throwaway DB — for real concurrency."""
    return psycopg.connect(
        os.environ["TAKYON_TEST_PG_DSN"], dbname=pg_conn.info.dbname, autocommit=True
    )


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


# ── create_magic_link ───────────────────────────────────────────────────────────


def test_create_magic_link_provisions_user_and_stores_only_the_hash(pg_conn):
    slug = _business(pg_conn, _owner(pg_conn))
    link, raw = app_identity.create_magic_link(pg_conn, slug, "frank@example.com")
    # the sub-user was upserted as a side of minting the link
    assert app_identity.get_app_user(pg_conn, slug, email="frank@example.com") is not None
    assert link.app_user_id
    # the raw token is never persisted — only its SHA-256 hash is
    stored = pg_conn.execute(
        "select token_hash from app_magic_links where id = %s", (link.id,)
    ).fetchone()[0]
    assert stored != raw
    assert stored == hashlib.sha256(raw.encode()).hexdigest()


def test_create_magic_link_rejects_nonpositive_ttl(pg_conn):
    slug = _business(pg_conn, _owner(pg_conn))
    with pytest.raises(ValueError):
        app_identity.create_magic_link(pg_conn, slug, "x@example.com", ttl_minutes=0)


# ── verify_magic_link ───────────────────────────────────────────────────────────


def test_verify_opens_session_that_validates_to_the_user(pg_conn):
    slug = _business(pg_conn, _owner(pg_conn))
    link, raw = app_identity.create_magic_link(pg_conn, slug, "grace@example.com")
    session, session_token = app_identity.verify_magic_link(pg_conn, slug, raw)
    assert session.app_user_id == link.app_user_id
    who = app_identity.validate_session(pg_conn, slug, session_token)
    assert who is not None
    assert who.email == "grace@example.com"
    assert who.id == link.app_user_id


def test_verify_is_single_use(pg_conn):
    slug = _business(pg_conn, _owner(pg_conn))
    _, raw = app_identity.create_magic_link(pg_conn, slug, "heidi@example.com")
    app_identity.verify_magic_link(pg_conn, slug, raw)  # first redemption wins
    with pytest.raises(InvalidMagicLink):
        app_identity.verify_magic_link(pg_conn, slug, raw)  # second is rejected


def test_verify_rejects_expired_link(pg_conn):
    slug = _business(pg_conn, _owner(pg_conn))
    link, raw = app_identity.create_magic_link(pg_conn, slug, "ivan@example.com")
    pg_conn.execute(
        "update app_magic_links set expires_at = now() - interval '1 minute' where id = %s",
        (link.id,),
    )
    with pytest.raises(InvalidMagicLink):
        app_identity.verify_magic_link(pg_conn, slug, raw)


def test_verify_rejects_unknown_and_empty_tokens(pg_conn):
    slug = _business(pg_conn, _owner(pg_conn))
    with pytest.raises(InvalidMagicLink):
        app_identity.verify_magic_link(pg_conn, slug, "not-a-real-token")
    with pytest.raises(InvalidMagicLink):
        app_identity.verify_magic_link(pg_conn, slug, "   ")


def test_verify_inactive_user_rolls_back_so_link_survives(pg_conn):
    slug = _business(pg_conn, _owner(pg_conn))
    link, raw = app_identity.create_magic_link(pg_conn, slug, "judy@example.com")
    pg_conn.execute(
        "update app_users set status = 'suspended' where id = %s", (link.app_user_id,)
    )
    with pytest.raises(InactiveAppUser):
        app_identity.verify_magic_link(pg_conn, slug, raw)
    # verify is atomic: the failed redemption rolled back, so the link was NOT consumed.
    pg_conn.execute(
        "update app_users set status = 'active' where id = %s", (link.app_user_id,)
    )
    session, token = app_identity.verify_magic_link(pg_conn, slug, raw)
    assert app_identity.validate_session(pg_conn, slug, token) is not None


def test_create_magic_link_rejects_inactive_user(pg_conn):
    slug = _business(pg_conn, _owner(pg_conn))
    user = app_identity.upsert_app_user(pg_conn, slug, "inactive@example.com")
    app_identity.set_app_user_status(pg_conn, slug, user.id, "suspended")
    with pytest.raises(InactiveAppUser):
        app_identity.create_magic_link(pg_conn, slug, "inactive@example.com")


def test_concurrent_verify_redeems_exactly_once(pg_conn):
    slug = _business(pg_conn, _owner(pg_conn))
    _, raw = app_identity.create_magic_link(pg_conn, slug, "mallory@example.com")
    n = 20
    barrier = threading.Barrier(n)

    def worker(_):
        conn = _new_conn(pg_conn)
        try:
            barrier.wait()
            app_identity.verify_magic_link(conn, slug, raw)
            return "ok"
        except InvalidMagicLink:
            return "rejected"
        finally:
            conn.close()

    with ThreadPoolExecutor(max_workers=n) as ex:
        results = list(ex.map(worker, range(n)))

    assert results.count("ok") == 1  # exactly one redemption wins
    assert results.count("rejected") == n - 1  # no other outcome (no errors)
    sessions = pg_conn.execute(
        "select count(*) from app_sessions where business_slug = %s", (slug,)
    ).fetchone()[0]
    assert sessions == 1  # and exactly one session row was created


# ── validate_session / revoke_session ───────────────────────────────────────────


def test_validate_rejects_revoked_session(pg_conn):
    slug = _business(pg_conn, _owner(pg_conn))
    _, raw = app_identity.create_magic_link(pg_conn, slug, "nina@example.com")
    _, token = app_identity.verify_magic_link(pg_conn, slug, raw)
    assert app_identity.revoke_session(pg_conn, slug, token) is True
    assert app_identity.validate_session(pg_conn, slug, token) is None


def test_set_status_revokes_live_sessions(pg_conn):
    slug = _business(pg_conn, _owner(pg_conn))
    _, raw = app_identity.create_magic_link(pg_conn, slug, "kill@example.com")
    session, token = app_identity.verify_magic_link(pg_conn, slug, raw)
    updated = app_identity.set_app_user_status(pg_conn, slug, session.app_user_id, "closed")
    assert updated.status == "closed"
    assert app_identity.validate_session(pg_conn, slug, token) is None


def test_validate_rejects_expired_session(pg_conn):
    slug = _business(pg_conn, _owner(pg_conn))
    _, raw = app_identity.create_magic_link(pg_conn, slug, "olga@example.com")
    session, token = app_identity.verify_magic_link(pg_conn, slug, raw)
    pg_conn.execute(
        "update app_sessions set expires_at = now() - interval '1 second' where id = %s",
        (session.id,),
    )
    assert app_identity.validate_session(pg_conn, slug, token) is None


def test_session_is_business_scoped(pg_conn):
    owner = _owner(pg_conn)
    slug_a = _business(pg_conn, owner, "A")
    slug_b = _business(pg_conn, owner, "B")
    _, raw = app_identity.create_magic_link(pg_conn, slug_a, "peter@example.com")
    _, token = app_identity.verify_magic_link(pg_conn, slug_a, raw)
    assert app_identity.validate_session(pg_conn, slug_a, token) is not None
    # the same token must NOT validate under a different business
    assert app_identity.validate_session(pg_conn, slug_b, token) is None


def test_revoke_is_idempotent(pg_conn):
    slug = _business(pg_conn, _owner(pg_conn))
    _, raw = app_identity.create_magic_link(pg_conn, slug, "quinn@example.com")
    _, token = app_identity.verify_magic_link(pg_conn, slug, raw)
    assert app_identity.revoke_session(pg_conn, slug, token) is True
    assert app_identity.revoke_session(pg_conn, slug, token) is False  # nothing live left


def test_validate_and_revoke_tolerate_garbage_tokens(pg_conn):
    slug = _business(pg_conn, _owner(pg_conn))
    assert app_identity.validate_session(pg_conn, slug, "") is None
    assert app_identity.validate_session(pg_conn, slug, "garbage") is None
    assert app_identity.revoke_session(pg_conn, slug, "") is False
    assert app_identity.revoke_session(pg_conn, slug, "garbage") is False
