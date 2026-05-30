"""Postgres integration tests for the billing ledger — flow A (user → platform).

Phase 2 acceptance: a costly action writes correct reserve/settle/refund entries;
spend is allowance-first then topup; double-charge is impossible under real
concurrency; cached balances always reconcile with the append-only ledger.

Exercises the REAL engine on real Postgres (never mocks). Concurrency tests open
their own extra connections to the same per-worker throwaway DB so FOR UPDATE row
locking is genuinely contended. Skips unless psycopg is importable and
TAKYON_TEST_PG_DSN is set.
"""

from __future__ import annotations

import os
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest

psycopg = pytest.importorskip("psycopg")

from plugins.takyon import billing  # noqa: E402
from plugins.takyon.billing import InsufficientBalance  # noqa: E402
from plugins.takyon.control_plane import provision_user_on_first_login  # noqa: E402


def _sub() -> str:
    return f"auth0|{uuid.uuid4().hex}"


def _user(conn) -> str:
    uid, _, _ = provision_user_on_first_login(conn, _sub())
    return uid


def _new_conn(pg_conn):
    """A fresh autocommit connection to the SAME throwaway DB — for real concurrency."""
    return psycopg.connect(
        os.environ["TAKYON_TEST_PG_DSN"], dbname=pg_conn.info.dbname, autocommit=True
    )


def test_jit_opens_billing_account_at_zero(pg_conn):
    uid = _user(pg_conn)
    bal = billing.get_billing_balances(pg_conn, uid)
    assert (bal.allowance_included_cents, bal.allowance_used_cents) == (0, 0)
    assert bal.topup_balance_cents == 0
    assert bal.reserved_cents == 0
    assert billing.reconcile_billing(pg_conn, uid)["ok"] is True


def test_open_billing_account_is_idempotent(pg_conn):
    uid = _user(pg_conn)
    billing.open_billing_account(pg_conn, uid)  # provisioning already opened it
    rows = pg_conn.execute(
        "select count(*) from billing_accounts where user_id = %s", (uid,)
    ).fetchone()[0]
    assert rows == 1


def test_grant_sets_included_and_resets_used(pg_conn):
    uid = _user(pg_conn)
    billing.grant_allowance(pg_conn, uid, 5000, "grant-1")
    bal = billing.get_billing_balances(pg_conn, uid)
    assert bal.allowance_included_cents == 5000
    assert bal.allowance_used_cents == 0
    assert bal.allowance_remaining_cents == 5000


def test_topup_adds_money_and_is_idempotent(pg_conn):
    uid = _user(pg_conn)
    assert billing.topup(pg_conn, uid, 5000, "tu-1") == 5000
    # replay of the same key must NOT double-credit
    assert billing.topup(pg_conn, uid, 5000, "tu-1") == 5000
    # a distinct key credits again
    assert billing.topup(pg_conn, uid, 5000, "tu-2") == 10000
    assert billing.reconcile_billing(pg_conn, uid)["ok"] is True


def test_reserve_spends_allowance_first(pg_conn):
    uid = _user(pg_conn)
    billing.grant_allowance(pg_conn, uid, 1000, "g")
    billing.topup(pg_conn, uid, 1000, "t")
    resv = billing.reserve(pg_conn, uid, 600, "r1")
    assert (resv.allowance_cents, resv.topup_cents) == (600, 0)
    bal = billing.get_billing_balances(pg_conn, uid)
    assert bal.allowance_used_cents == 600
    assert bal.topup_balance_cents == 1000  # topup untouched
    assert bal.reserved_cents == 600


def test_reserve_spills_to_topup_after_allowance(pg_conn):
    uid = _user(pg_conn)
    billing.grant_allowance(pg_conn, uid, 1000, "g")
    billing.topup(pg_conn, uid, 1000, "t")
    resv = billing.reserve(pg_conn, uid, 1500, "r1")
    assert (resv.allowance_cents, resv.topup_cents) == (1000, 500)
    bal = billing.get_billing_balances(pg_conn, uid)
    assert bal.allowance_used_cents == 1000
    assert bal.topup_balance_cents == 500
    assert bal.reserved_cents == 1500


def test_reserve_insufficient_raises_with_figures(pg_conn):
    uid = _user(pg_conn)
    billing.grant_allowance(pg_conn, uid, 100, "g")
    billing.topup(pg_conn, uid, 100, "t")
    with pytest.raises(InsufficientBalance) as ei:
        billing.reserve(pg_conn, uid, 300, "r1")
    assert ei.value.estimate_cents == 300
    assert ei.value.allowance_available_cents == 100
    assert ei.value.topup_available_cents == 100
    # nothing was written — the account is untouched
    bal = billing.get_billing_balances(pg_conn, uid)
    assert bal.allowance_used_cents == 0 and bal.topup_balance_cents == 100


def test_settle_actual_under_reserved_releases_difference(pg_conn):
    uid = _user(pg_conn)
    billing.topup(pg_conn, uid, 1000, "t")
    billing.reserve(pg_conn, uid, 800, "r1")
    billing.settle(pg_conn, "r1", 300)
    bal = billing.get_billing_balances(pg_conn, uid)
    assert bal.topup_balance_cents == 700  # 1000 − 300 actually spent
    assert bal.reserved_cents == 0
    assert billing.reconcile_billing(pg_conn, uid)["ok"] is True


def test_settle_full_consumes_reservation(pg_conn):
    uid = _user(pg_conn)
    billing.topup(pg_conn, uid, 1000, "t")
    billing.reserve(pg_conn, uid, 500, "r1")
    billing.settle(pg_conn, "r1", 500)
    bal = billing.get_billing_balances(pg_conn, uid)
    assert bal.topup_balance_cents == 500
    assert bal.reserved_cents == 0


def test_split_reservation_settles_allowance_first(pg_conn):
    uid = _user(pg_conn)
    billing.grant_allowance(pg_conn, uid, 1000, "g")
    billing.topup(pg_conn, uid, 1000, "t")
    billing.reserve(pg_conn, uid, 1500, "r1")  # 1000 allowance + 500 topup
    billing.settle(pg_conn, "r1", 1200)  # spends all allowance + 200 topup
    bal = billing.get_billing_balances(pg_conn, uid)
    assert bal.allowance_used_cents == 1000  # all allowance spent
    assert bal.topup_balance_cents == 800  # (1000−500) + 300 released
    assert bal.reserved_cents == 0
    assert billing.reconcile_billing(pg_conn, uid)["ok"] is True


def test_refund_releases_whole_reservation(pg_conn):
    uid = _user(pg_conn)
    billing.topup(pg_conn, uid, 1000, "t")
    billing.reserve(pg_conn, uid, 800, "r1")
    billing.refund(pg_conn, "r1")
    bal = billing.get_billing_balances(pg_conn, uid)
    assert bal.topup_balance_cents == 1000  # fully restored
    assert bal.reserved_cents == 0


def test_reserve_replay_returns_same_split_without_recharging(pg_conn):
    uid = _user(pg_conn)
    billing.topup(pg_conn, uid, 1000, "t")
    first = billing.reserve(pg_conn, uid, 200, "dup")
    second = billing.reserve(pg_conn, uid, 200, "dup")
    assert (first.allowance_cents, first.topup_cents) == (second.allowance_cents, second.topup_cents)
    bal = billing.get_billing_balances(pg_conn, uid)
    assert bal.topup_balance_cents == 800  # debited once, not twice
    assert bal.reserved_cents == 200


def test_settle_is_idempotent(pg_conn):
    uid = _user(pg_conn)
    billing.topup(pg_conn, uid, 1000, "t")
    billing.reserve(pg_conn, uid, 400, "r1")
    billing.settle(pg_conn, "r1", 250)
    billing.settle(pg_conn, "r1", 250)  # no-op
    bal = billing.get_billing_balances(pg_conn, uid)
    assert bal.topup_balance_cents == 750
    assert bal.reserved_cents == 0


def test_refund_after_settle_is_noop(pg_conn):
    uid = _user(pg_conn)
    billing.topup(pg_conn, uid, 1000, "t")
    billing.reserve(pg_conn, uid, 400, "r1")
    billing.settle(pg_conn, "r1", 400)
    billing.refund(pg_conn, "r1")  # already finalized → no-op
    bal = billing.get_billing_balances(pg_conn, uid)
    assert bal.topup_balance_cents == 600


def test_settle_more_than_reserved_rejected(pg_conn):
    uid = _user(pg_conn)
    billing.topup(pg_conn, uid, 1000, "t")
    billing.reserve(pg_conn, uid, 300, "r1")
    with pytest.raises(ValueError):
        billing.settle(pg_conn, "r1", 400)


def test_concurrent_reserves_never_oversell(pg_conn):
    uid = _user(pg_conn)
    billing.topup(pg_conn, uid, 1000, "t")  # exactly 10 reservations of 100 fit
    n = 25
    barrier = threading.Barrier(n)

    def worker(i: int):
        conn = _new_conn(pg_conn)
        try:
            barrier.wait()
            billing.reserve(conn, uid, 100, f"r{i}")
            return "ok"
        except InsufficientBalance:
            return "insufficient"
        finally:
            conn.close()

    with ThreadPoolExecutor(max_workers=n) as ex:
        results = list(ex.map(worker, range(n)))

    assert results.count("ok") == 10
    assert results.count("insufficient") == 15  # no other outcome (no errors)
    bal = billing.get_billing_balances(pg_conn, uid)
    assert bal.topup_balance_cents == 0  # never driven negative
    assert bal.reserved_cents == 1000
    assert billing.reconcile_billing(pg_conn, uid)["ok"] is True


def test_concurrent_identical_reserve_charges_once(pg_conn):
    uid = _user(pg_conn)
    billing.topup(pg_conn, uid, 1000, "t")
    n = 10
    barrier = threading.Barrier(n)

    def worker(_):
        conn = _new_conn(pg_conn)
        try:
            barrier.wait()
            billing.reserve(conn, uid, 100, "same-key")
            return "ok"
        finally:
            conn.close()

    with ThreadPoolExecutor(max_workers=n) as ex:
        results = list(ex.map(worker, range(n)))

    assert results.count("ok") == n  # all succeed (replay is not an error)
    bal = billing.get_billing_balances(pg_conn, uid)
    assert bal.topup_balance_cents == 900  # debited exactly once
    entries = pg_conn.execute(
        "select count(*) from billing_entries "
        "where reservation_key = 'same-key' and kind = 'reserve'",
        (),
    ).fetchone()[0]
    assert entries == 1
