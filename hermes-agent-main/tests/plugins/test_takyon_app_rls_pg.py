from __future__ import annotations

import json
import uuid
from contextlib import contextmanager

import pytest

from plugins.takyon import app_identity, app_profiles
from plugins.takyon.control_plane import provision_user_on_first_login
from plugins.takyon.core import _hash_token
from plugins.takyon.runtime_app import configure_takyon_pg_session

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


def _user_and_session(conn, slug: str, email: str) -> tuple[app_identity.AppUser, str]:
    user = app_identity.upsert_app_user(conn, slug, email)
    _, token = app_identity.start_session(conn, slug, user.id)
    return user, token


@contextmanager
def _customer_scope(conn, *, business_slug: str, session_token: str):
    conn.execute("select set_config('takyon.rls_bypass', '0', false)")
    conn.execute("select set_config('takyon.rls_business_slug', %s, false)", (business_slug,))
    conn.execute("select set_config('takyon.rls_app_user_id', '', false)")
    conn.execute("select set_config('takyon.rls_session_hash', %s, false)", (_hash_token(session_token),))
    try:
        yield
    finally:
        configure_takyon_pg_session(conn, bypass=True)


def test_app_plane_rls_limits_records_entitlements_usage_revenue_and_media(pg_conn):
    slug = _business(pg_conn, _owner(pg_conn))
    alice, alice_token = _user_and_session(pg_conn, slug, "alice@example.com")
    bob, _ = _user_and_session(pg_conn, slug, "bob@example.com")

    pg_conn.execute(
        "insert into app_records (id, business_slug, app_user_id, record_type, title, data, metadata) "
        "values (%s, %s, %s, 'note', 'Alice note', '{}'::jsonb, '{}'::jsonb), "
        "(%s, %s, %s, 'note', 'Bob note', '{}'::jsonb, '{}'::jsonb)",
        ("r1", slug, alice.id, "r2", slug, bob.id),
    )
    pg_conn.execute(
        "insert into app_entitlements (business_slug, app_user_id, tier, status, source) "
        "values (%s, %s, 'paid', 'active', 'stripe'), (%s, %s, 'pro', 'active', 'stripe')",
        (slug, alice.id, slug, bob.id),
    )
    pg_conn.execute(
        "insert into app_usage_events (business_slug, app_user_id, reservation_key, route, purpose, status, actual_cost_microusd) "
        "values (%s, %s, 'ua', 'app', 'product_usage', 'completed', 111), "
        "(%s, %s, 'ub', 'app', 'product_usage', 'completed', 222)",
        (slug, alice.id, slug, bob.id),
    )
    pg_conn.execute(
        "insert into app_revenue_events (business_slug, customer_email, amount_paid_cents) "
        "values (%s, 'alice@example.com', 1900), (%s, 'bob@example.com', 2900)",
        (slug, slug),
    )
    pg_conn.execute(
        "insert into app_media (id, business_slug, app_user_id, media_id, mime, size_bytes, storage_key) "
        "values ('m1', %s, %s, 'alice-media', 'image/png', 10, 'media/a'), "
        "('m2', %s, %s, 'bob-media', 'image/png', 10, 'media/b')",
        (slug, alice.id, slug, bob.id),
    )

    with _customer_scope(pg_conn, business_slug=slug, session_token=alice_token):
        records = pg_conn.execute(
            "select id, app_user_id from app_records where business_slug = %s order by id",
            (slug,),
        ).fetchall()
        entitlements = pg_conn.execute(
            "select app_user_id, tier from app_entitlements where business_slug = %s",
            (slug,),
        ).fetchall()
        usage = pg_conn.execute(
            "select coalesce(sum(actual_cost_microusd), 0) from app_usage_events where business_slug = %s",
            (slug,),
        ).fetchone()[0]
        revenue = pg_conn.execute(
            "select coalesce(sum(amount_paid_cents), 0) from app_revenue_events where business_slug = %s",
            (slug,),
        ).fetchone()[0]
        media = pg_conn.execute(
            "select media_id, app_user_id from app_media where business_slug = %s order by media_id",
            (slug,),
        ).fetchall()

    assert [(row[0], str(row[1])) for row in records] == [("r1", alice.id)]
    assert [(str(row[0]), row[1]) for row in entitlements] == [(alice.id, "paid")]
    assert int(usage) == 111
    assert int(revenue) == 1900
    assert [(row[0], row[1]) for row in media] == [("alice-media", alice.id)]


def test_app_plane_rls_exposes_only_self_and_directory_enabled_profiles(pg_conn):
    slug = _business(pg_conn, _owner(pg_conn))
    alice, alice_token = _user_and_session(pg_conn, slug, "alice@example.com")
    bob, _ = _user_and_session(pg_conn, slug, "bob@example.com")
    carol, _ = _user_and_session(pg_conn, slug, "carol@example.com")

    app_profiles.ensure_profile(pg_conn, slug, app_user_id=alice.id)
    app_profiles.ensure_profile(pg_conn, slug, app_user_id=bob.id)
    app_profiles.ensure_profile(pg_conn, slug, app_user_id=carol.id)
    pg_conn.execute(
        "update app_user_profiles set directory_enabled = true where business_slug = %s and id = %s",
        (slug, bob.id),
    )

    with _customer_scope(pg_conn, business_slug=slug, session_token=alice_token):
        visible_ids = [
            str(row[0])
            for row in pg_conn.execute(
                "select id from app_user_profiles where business_slug = %s order by id",
                (slug,),
            ).fetchall()
        ]

    assert visible_ids == sorted([alice.id, bob.id])
    assert carol.id not in visible_ids


def test_app_plane_rls_limits_connections_to_rows_touching_the_actor(pg_conn):
    slug = _business(pg_conn, _owner(pg_conn))
    alice, alice_token = _user_and_session(pg_conn, slug, "alice@example.com")
    bob, _ = _user_and_session(pg_conn, slug, "bob@example.com")
    carol, _ = _user_and_session(pg_conn, slug, "carol@example.com")

    pg_conn.execute(
        "insert into app_connections (business_slug, source_app_user_id, target_app_user_id, state) "
        "values (%s, %s, %s, 'like'), (%s, %s, %s, 'like'), (%s, %s, %s, 'block')",
        (slug, alice.id, bob.id, slug, bob.id, alice.id, slug, carol.id, bob.id),
    )

    with _customer_scope(pg_conn, business_slug=slug, session_token=alice_token):
        rows = pg_conn.execute(
            "select source_app_user_id::text, target_app_user_id::text from app_connections "
            "where business_slug = %s order by source_app_user_id::text, target_app_user_id::text",
            (slug,),
        ).fetchall()
        with pytest.raises(psycopg.Error, match="row-level security"):
            pg_conn.execute(
                "insert into app_connections (business_slug, source_app_user_id, target_app_user_id, state) "
                "values (%s, %s, %s, 'like')",
                (slug, bob.id, carol.id),
            )

    assert [(row[0], row[1]) for row in rows] == sorted(
        [(alice.id, bob.id), (bob.id, alice.id)]
    )
