"""Postgres integration tests for the per-user fixed-window rate limiter
(plugins/takyon/rate_limit.py). Exercises the REAL atomic upsert against Postgres via
the shared `pg_conn` throwaway-DB fixture (the limiter's whole correctness story is the
single SQL statement, so mocking it would test nothing). Skips unless psycopg is
importable and TAKYON_TEST_PG_DSN is set.
"""

from __future__ import annotations

import time
import uuid

import pytest

psycopg = pytest.importorskip("psycopg")

from plugins.takyon import app_identity  # noqa: E402
from plugins.takyon.rate_limit import check_rate_limit, prune_rate_limits  # noqa: E402


def _user(conn) -> str:
    """Insert a bare users row and return its id."""
    return conn.execute(
        "insert into users (auth0_sub) values (%s) returning id",
        (f"auth0|{uuid.uuid4().hex}",),
    ).fetchone()[0]


def _business(conn, owner_id: str) -> str:
    slug = f"biz-{uuid.uuid4().hex[:8]}"
    conn.execute(
        "insert into businesses (slug, name, owner_user_id) values (%s, %s, %s)",
        (slug, "Acme", owner_id),
    )
    return slug


def test_first_request_allowed(pg_conn):
    uid = _user(pg_conn)
    r = check_rate_limit(pg_conn, uid, limit=5, window_seconds=60)
    assert r.allowed is True
    assert r.count == 1
    assert r.remaining == 4
    assert r.limit == 5
    assert r.window_seconds == 60


def test_allows_up_to_limit_then_blocks(pg_conn):
    # window_seconds=60 so all four calls land in the same window (tests run in ms).
    uid = _user(pg_conn)
    seen = [check_rate_limit(pg_conn, uid, limit=3, window_seconds=60) for _ in range(4)]
    assert [r.allowed for r in seen] == [True, True, True, False]
    assert [r.count for r in seen] == [1, 2, 3, 4]
    assert [r.remaining for r in seen] == [2, 1, 0, 0]
    # the refused request tells the client when to come back
    assert seen[-1].retry_after_seconds >= 1


def test_users_have_independent_windows(pg_conn):
    a, b = _user(pg_conn), _user(pg_conn)
    assert check_rate_limit(pg_conn, a, limit=1, window_seconds=60).allowed is True
    assert check_rate_limit(pg_conn, a, limit=1, window_seconds=60).allowed is False
    # user b's counter is untouched by user a hitting the cap
    rb = check_rate_limit(pg_conn, b, limit=1, window_seconds=60)
    assert rb.allowed is True
    assert rb.count == 1


def test_app_user_ids_can_use_same_limiter(pg_conn):
    owner = _user(pg_conn)
    slug = _business(pg_conn, owner)
    app_a = app_identity.upsert_app_user(pg_conn, slug, "alice@example.com")
    app_b = app_identity.upsert_app_user(pg_conn, slug, "bob@example.com")

    seen = [
        check_rate_limit(pg_conn, app_a.id, limit=2, window_seconds=60),
        check_rate_limit(pg_conn, app_a.id, limit=2, window_seconds=60),
        check_rate_limit(pg_conn, app_a.id, limit=2, window_seconds=60),
    ]

    assert [r.allowed for r in seen] == [True, True, False]
    rb = check_rate_limit(pg_conn, app_b.id, limit=2, window_seconds=60)
    assert rb.allowed is True
    assert rb.count == 1


def test_old_window_does_not_count_against_current(pg_conn):
    # A previous window hammered well past any cap must not bleed into the current one:
    # the counter is keyed by (user, window_start), so a new window starts fresh.
    uid = _user(pg_conn)
    pg_conn.execute(
        "insert into api_rate_limits (user_id, window_start, request_count) "
        "values (%s, now() - make_interval(hours => 1), 999)",
        (uid,),
    )
    r = check_rate_limit(pg_conn, uid, limit=3, window_seconds=60)
    assert r.allowed is True
    assert r.count == 1


def test_window_rolls_over(pg_conn):
    # Proves the epoch-aligned window actually advances with wall-clock time: after the
    # window elapses, a later request lands in a strictly later window and counts fresh.
    uid = _user(pg_conn)
    r1 = check_rate_limit(pg_conn, uid, limit=100, window_seconds=1)
    time.sleep(1.2)
    r2 = check_rate_limit(pg_conn, uid, limit=100, window_seconds=1)
    assert r2.reset_at > r1.reset_at
    assert r2.count == 1


def test_rejects_nonpositive_limit_and_window(pg_conn):
    uid = _user(pg_conn)
    with pytest.raises(ValueError):
        check_rate_limit(pg_conn, uid, limit=0, window_seconds=60)
    with pytest.raises(ValueError):
        check_rate_limit(pg_conn, uid, limit=5, window_seconds=0)
    with pytest.raises(ValueError):
        prune_rate_limits(pg_conn, older_than_seconds=-1)


def test_prune_removes_only_expired_windows(pg_conn):
    uid = _user(pg_conn)
    # one expired window (2h old) + one current window via the real engine
    pg_conn.execute(
        "insert into api_rate_limits (user_id, window_start, request_count) "
        "values (%s, now() - make_interval(hours => 2), 5)",
        (uid,),
    )
    check_rate_limit(pg_conn, uid, limit=10, window_seconds=60)
    removed = prune_rate_limits(pg_conn, older_than_seconds=3600)
    assert removed == 1
    surviving = pg_conn.execute(
        "select count(*) from api_rate_limits where user_id = %s", (uid,)
    ).fetchone()[0]
    assert surviving == 1  # only the current window remains
