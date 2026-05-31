"""Postgres integration tests for the business creative-credit ledger.

Creative credits are a business-scoped fixed-price rail for future paid creative/ad
actions. These tests exercise the real ledger against a throwaway Postgres database:
granting packs, reserving before work, committing on success, releasing on failure,
and the row-locking invariant that prevents concurrent oversell.
"""

from __future__ import annotations

import os
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest

psycopg = pytest.importorskip("psycopg")

from plugins.takyon import business_credits  # noqa: E402
from plugins.takyon.control_plane import provision_user_on_first_login  # noqa: E402
from plugins.takyon.business_credits import InsufficientCreativeCredits  # noqa: E402


def _sub() -> str:
    return f"auth0|{uuid.uuid4().hex}"


def _business(conn, name="Acme") -> str:
    owner_id, _, _ = provision_user_on_first_login(conn, _sub())
    slug = f"biz-{uuid.uuid4().hex[:8]}"
    conn.execute(
        "insert into businesses (slug, name, owner_user_id) values (%s, %s, %s)",
        (slug, name, owner_id),
    )
    return slug


def _new_conn(pg_conn):
    return psycopg.connect(
        os.environ["TAKYON_TEST_PG_DSN"], dbname=pg_conn.info.dbname, autocommit=True
    )


def test_jit_opens_business_credit_account_at_zero(pg_conn):
    slug = _business(pg_conn)
    balances = business_credits.get_business_credit_balances(pg_conn, slug)
    assert balances.business_slug == slug
    assert balances.balance_credits == 0
    assert balances.reserved_credits == 0


def test_grant_credits_is_idempotent(pg_conn):
    slug = _business(pg_conn)
    balances = business_credits.grant_credits(pg_conn, slug, 25, "grant-1")
    assert balances.balance_credits == 25
    again = business_credits.grant_credits(pg_conn, slug, 25, "grant-1")
    assert again.balance_credits == 25


def test_reserve_and_release_restore_balance(pg_conn):
    slug = _business(pg_conn)
    business_credits.grant_credits(pg_conn, slug, 10, "grant-1")
    reservation = business_credits.reserve_credits(pg_conn, slug, 4, "resv-1")
    assert reservation.reserved_credits == 4
    balances = business_credits.get_business_credit_balances(pg_conn, slug)
    assert balances.balance_credits == 6
    assert balances.reserved_credits == 4

    released = business_credits.release_credits(pg_conn, "resv-1")
    assert released.balance_credits == 10
    assert released.reserved_credits == 0


def test_commit_partial_refunds_difference(pg_conn):
    slug = _business(pg_conn)
    business_credits.grant_credits(pg_conn, slug, 12, "grant-1")
    business_credits.reserve_credits(pg_conn, slug, 7, "resv-1")
    balances = business_credits.commit_credits(pg_conn, "resv-1", actual_credits=5)
    assert balances.balance_credits == 7
    assert balances.reserved_credits == 0


def test_reserve_insufficient_raises_with_figures(pg_conn):
    slug = _business(pg_conn)
    business_credits.grant_credits(pg_conn, slug, 3, "grant-1")
    with pytest.raises(InsufficientCreativeCredits) as excinfo:
        business_credits.reserve_credits(pg_conn, slug, 5, "resv-1")
    assert excinfo.value.requested_credits == 5
    assert excinfo.value.available_credits == 3


def test_concurrent_reserves_never_oversell(pg_conn):
    slug = _business(pg_conn)
    business_credits.grant_credits(pg_conn, slug, 10, "grant-1")
    n = 25
    barrier = threading.Barrier(n)

    def worker(i: int):
        conn = _new_conn(pg_conn)
        try:
            barrier.wait()
            business_credits.reserve_credits(conn, slug, 1, f"resv-{i}")
            return "ok"
        except InsufficientCreativeCredits:
            return "insufficient"
        finally:
            conn.close()

    with ThreadPoolExecutor(max_workers=n) as ex:
        results = list(ex.map(worker, range(n)))

    assert results.count("ok") == 10
    assert results.count("insufficient") == 15
    balances = business_credits.get_business_credit_balances(pg_conn, slug)
    assert balances.balance_credits == 0
    assert balances.reserved_credits == 10


def test_identical_reserve_replays_once(pg_conn):
    slug = _business(pg_conn)
    business_credits.grant_credits(pg_conn, slug, 10, "grant-1")
    n = 10
    barrier = threading.Barrier(n)

    def worker(_):
        conn = _new_conn(pg_conn)
        try:
            barrier.wait()
            business_credits.reserve_credits(conn, slug, 2, "same-key")
            return "ok"
        finally:
            conn.close()

    with ThreadPoolExecutor(max_workers=n) as ex:
        results = list(ex.map(worker, range(n)))

    assert results.count("ok") == n
    balances = business_credits.get_business_credit_balances(pg_conn, slug)
    assert balances.balance_credits == 8
    assert balances.reserved_credits == 2
    count = pg_conn.execute(
        "select count(*) from business_creative_credit_entries "
        "where reservation_key = 'same-key' and kind = 'reserve'"
    ).fetchone()[0]
    assert count == 1
