"""Postgres integration tests for the custody ledger — flow B (sub-users → user,
held by the platform).

Phase 2 acceptance: a sub-user payment accrues net (gross − app fee) to the owner's
owed balance; accrual works with NO Stripe Connect connected; payouts drain owed and
can't exceed it; everything is idempotent and reconciles. Custody is never netted
against billing.

Real engine on real Postgres. Skips unless psycopg is importable and
TAKYON_TEST_PG_DSN is set (the fee-clamp/floor unit checks run whenever psycopg is
importable, since they don't touch the DB).
"""

from __future__ import annotations

import os
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest

psycopg = pytest.importorskip("psycopg")

from plugins.takyon import custody  # noqa: E402
from plugins.takyon.control_plane import provision_user_on_first_login  # noqa: E402
from plugins.takyon.custody import CustodyClawbackPending, InsufficientCustody  # noqa: E402


def _sub() -> str:
    return f"auth0|{uuid.uuid4().hex}"


def _user(conn) -> str:
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
    return psycopg.connect(
        os.environ["TAKYON_TEST_PG_DSN"], dbname=pg_conn.info.dbname, autocommit=True
    )


def test_fee_bps_defaults_and_clamps(monkeypatch):
    monkeypatch.delenv("STRIPE_CONNECT_APPLICATION_FEE_BPS", raising=False)
    assert custody.app_fee_bps() == 2000
    monkeypatch.setenv("STRIPE_CONNECT_APPLICATION_FEE_BPS", "99999")
    assert custody.app_fee_bps() == 10000  # clamped to 100%
    monkeypatch.setenv("STRIPE_CONNECT_APPLICATION_FEE_BPS", "-5")
    assert custody.app_fee_bps() == 0
    monkeypatch.setenv("STRIPE_CONNECT_APPLICATION_FEE_BPS", "garbage")
    assert custody.app_fee_bps() == 2000  # falls back, never crashes


def test_jit_opens_custody_account_at_zero(pg_conn):
    uid = _user(pg_conn)
    bal = custody.get_custody_balances(pg_conn, uid)
    assert (bal.owed_balance_cents, bal.paid_out_cents) == (0, 0)
    assert bal.currency == "usd"
    assert custody.reconcile_custody(pg_conn, uid)["ok"] is True


def test_open_custody_account_is_idempotent(pg_conn):
    uid = _user(pg_conn)
    custody.open_custody_account(pg_conn, uid)
    rows = pg_conn.execute(
        "select count(*) from custody_accounts where user_id = %s", (uid,)
    ).fetchone()[0]
    assert rows == 1


def test_accrual_takes_app_fee_and_credits_net(pg_conn):
    uid = _user(pg_conn)
    slug = _business(pg_conn, uid)
    owed = custody.accrue(pg_conn, uid, slug, 10000, "pay-1", fee_bps=2000)
    assert owed == 8000  # 10000 − 20%
    row = pg_conn.execute(
        "select gross_cents, fee_cents, net_cents, kind from custody_entries "
        "where idempotency_key = 'pay-1'"
    ).fetchone()
    assert (row[0], row[1], row[2], row[3]) == (10000, 2000, 8000, "accrual")


def test_accrual_works_without_connect(pg_conn):
    uid = _user(pg_conn)
    slug = _business(pg_conn, uid)
    # the user has NOT connected Stripe Connect — accrual must still happen
    status = pg_conn.execute(
        "select stripe_connect_status from users where id = %s", (uid,)
    ).fetchone()[0]
    assert status == "none"
    owed = custody.accrue(pg_conn, uid, slug, 5000, "pay-1", fee_bps=2000)
    assert owed == 4000


def test_accrual_default_fee_from_env(pg_conn, monkeypatch):
    monkeypatch.setenv("STRIPE_CONNECT_APPLICATION_FEE_BPS", "1000")  # 10%
    uid = _user(pg_conn)
    slug = _business(pg_conn, uid)
    owed = custody.accrue(pg_conn, uid, slug, 10000, "pay-1")
    assert owed == 9000


def test_accrual_fee_is_floored(pg_conn):
    uid = _user(pg_conn)
    slug = _business(pg_conn, uid)
    # 99 * 2000 / 10000 = 19.8 → floored to 19; net = 80 (platform never over-takes)
    custody.accrue(pg_conn, uid, slug, 99, "pay-1", fee_bps=2000)
    row = pg_conn.execute(
        "select fee_cents, net_cents from custody_entries where idempotency_key = 'pay-1'"
    ).fetchone()
    assert (row[0], row[1]) == (19, 80)


def test_accrual_is_idempotent(pg_conn):
    uid = _user(pg_conn)
    slug = _business(pg_conn, uid)
    assert custody.accrue(pg_conn, uid, slug, 10000, "pay-1", fee_bps=2000) == 8000
    assert custody.accrue(pg_conn, uid, slug, 10000, "pay-1", fee_bps=2000) == 8000
    assert custody.reconcile_custody(pg_conn, uid)["ok"] is True


def test_payout_drains_owed(pg_conn):
    uid = _user(pg_conn)
    slug = _business(pg_conn, uid)
    custody.accrue(pg_conn, uid, slug, 10000, "pay-1", fee_bps=2000)  # owed 8000
    owed = custody.payout(pg_conn, uid, 5000, "wd-1")
    assert owed == 3000
    bal = custody.get_custody_balances(pg_conn, uid)
    assert bal.paid_out_cents == 5000
    assert custody.reconcile_custody(pg_conn, uid)["ok"] is True


def test_payout_exceeding_owed_rejected(pg_conn):
    uid = _user(pg_conn)
    slug = _business(pg_conn, uid)
    custody.accrue(pg_conn, uid, slug, 10000, "pay-1", fee_bps=2000)  # owed 8000
    with pytest.raises(InsufficientCustody) as ei:
        custody.payout(pg_conn, uid, 9000, "wd-1")
    assert ei.value.requested_cents == 9000 and ei.value.owed_cents == 8000
    bal = custody.get_custody_balances(pg_conn, uid)
    assert bal.owed_balance_cents == 8000  # untouched


def test_payout_is_idempotent(pg_conn):
    uid = _user(pg_conn)
    slug = _business(pg_conn, uid)
    custody.accrue(pg_conn, uid, slug, 10000, "pay-1", fee_bps=2000)
    assert custody.payout(pg_conn, uid, 5000, "wd-1") == 3000
    assert custody.payout(pg_conn, uid, 5000, "wd-1") == 3000  # drained once
    bal = custody.get_custody_balances(pg_conn, uid)
    assert bal.paid_out_cents == 5000


def test_refund_clawback_debits_owed_recovers_shortfall_and_blocks_payout(pg_conn):
    uid = str(uuid.uuid4())
    pg_conn.execute(
        "insert into users (id, auth0_sub) values (%s, %s)",
        (uid, f"auth0|{uuid.uuid4().hex}"),
    )
    slug = _business(pg_conn, uid)
    custody.open_custody_account(pg_conn, uid)
    custody.accrue(pg_conn, uid, slug, 8000, "pay-before-refund", fee_bps=0)

    pg_conn.execute("set session authorization takyon_safebox_authority")
    try:
        first = custody.clawback(
            pg_conn, uid, slug, 3000, "refund-1", stripe_ref="ch_1"
        )
        assert first == {
            "applied_cents": 3000,
            "shortfall_cents": 0,
            "owed_balance_cents": 5000,
            "replayed": False,
        }
        replay = custody.clawback(
            pg_conn, uid, slug, 3000, "refund-1", stripe_ref="ch_1"
        )
        assert replay["replayed"] is True
        second = custody.clawback(
            pg_conn, uid, slug, 7000, "refund-2", stripe_ref="ch_1"
        )
        assert second["applied_cents"] == 5000
        assert second["shortfall_cents"] == 2000
        assert second["owed_balance_cents"] == 0
        with pytest.raises(CustodyClawbackPending) as exc:
            custody.payout(pg_conn, uid, 1, "blocked-payout")
        assert exc.value.pending_cents == 2000
        assert custody.accrue(
            pg_conn, uid, slug, 3000, "pay-after-refund", fee_bps=0
        ) == 1000
        assert custody.payout(pg_conn, uid, 1000, "allowed-payout") == 0
    finally:
        pg_conn.execute("reset session authorization")

    assert custody.reconcile_custody(pg_conn, uid)["ok"] is True


def test_concurrent_accruals_sum_correctly(pg_conn):
    uid = _user(pg_conn)
    slug = _business(pg_conn, uid)
    n = 20
    barrier = threading.Barrier(n)

    def worker(i: int):
        conn = _new_conn(pg_conn)
        try:
            barrier.wait()
            custody.accrue(conn, uid, slug, 1000, f"pay-{i}", fee_bps=2000)
            return "ok"
        finally:
            conn.close()

    with ThreadPoolExecutor(max_workers=n) as ex:
        results = list(ex.map(worker, range(n)))

    assert results.count("ok") == n
    bal = custody.get_custody_balances(pg_conn, uid)
    assert bal.owed_balance_cents == 800 * n  # no lost updates
    assert custody.reconcile_custody(pg_conn, uid)["ok"] is True
