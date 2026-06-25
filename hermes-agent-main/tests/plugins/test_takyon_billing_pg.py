"""Postgres integration tests for the billing ledger — flow A (user → platform).

Phase 2 acceptance: a costly action writes correct reserve/settle/refund entries;
spend draws on the metered allowance bucket (the à-la-carte topup overflow bucket
was removed 2026-06-18 — allowance is the only flow-A bucket); double-charge is
impossible under real concurrency; cached balances always reconcile with the
append-only ledger.

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


def test_jit_opens_billing_account_unspent(pg_conn):
    # First-login provisioning opens the single billing account and applies the
    # starter allowance once. Nothing has been spent or reserved yet, so used is 0,
    # remaining == included, and the cache reconciles with the empty ledger.
    uid = _user(pg_conn)
    bal = billing.get_billing_balances(pg_conn, uid)
    assert bal.allowance_used_cents == 0
    assert bal.allowance_remaining_cents == bal.allowance_included_cents
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


def test_topup_buy_path_is_gone(pg_conn):
    # The à-la-carte topup buy/credit rail was removed 2026-06-18 — allowance is the
    # only flow-A bucket. The function must not exist; funding goes through
    # grant_allowance, which is idempotent on its key (a replay must NOT re-grant).
    assert not hasattr(billing, "topup")
    uid = _user(pg_conn)
    assert billing.grant_allowance(pg_conn, uid, 5000, "g-1") == 5000
    bal = billing.get_billing_balances(pg_conn, uid)
    assert bal.allowance_included_cents == 5000
    # replay of the same key must NOT change the granted allowance
    assert billing.grant_allowance(pg_conn, uid, 5000, "g-1") == 5000
    bal = billing.get_billing_balances(pg_conn, uid)
    assert bal.allowance_included_cents == 5000
    assert billing.reconcile_billing(pg_conn, uid)["ok"] is True


def test_reserve_draws_against_allowance(pg_conn):
    uid = _user(pg_conn)
    billing.grant_allowance(pg_conn, uid, 2000, "g")
    resv = billing.reserve(pg_conn, uid, 600, "r1")
    assert resv.allowance_cents == 600
    assert resv.total_cents == 600  # allowance is the only bucket
    bal = billing.get_billing_balances(pg_conn, uid)
    assert bal.allowance_used_cents == 600
    assert bal.allowance_remaining_cents == 1400
    assert bal.reserved_cents == 600


def test_reserve_over_allowance_raises_no_overflow_bucket(pg_conn):
    # There is no longer a topup bucket to spill into once allowance is exhausted —
    # a reserve that exceeds the remaining allowance must fail closed and write nothing.
    uid = _user(pg_conn)
    billing.grant_allowance(pg_conn, uid, 1000, "g")
    with pytest.raises(InsufficientBalance) as ei:
        billing.reserve(pg_conn, uid, 1500, "r1")
    assert ei.value.estimate_cents == 1500
    assert ei.value.allowance_available_cents == 1000
    bal = billing.get_billing_balances(pg_conn, uid)
    assert bal.allowance_used_cents == 0  # untouched — nothing was written
    assert bal.reserved_cents == 0


def test_reserve_insufficient_raises_with_figures(pg_conn):
    uid = _user(pg_conn)
    billing.grant_allowance(pg_conn, uid, 100, "g")
    with pytest.raises(InsufficientBalance) as ei:
        billing.reserve(pg_conn, uid, 300, "r1")
    assert ei.value.estimate_cents == 300
    assert ei.value.allowance_available_cents == 100
    assert not hasattr(ei.value, "topup_available_cents")
    # nothing was written — the account is untouched
    bal = billing.get_billing_balances(pg_conn, uid)
    assert bal.allowance_used_cents == 0
    assert bal.allowance_remaining_cents == 100


def test_dogfood_switch_ungates_reserve_but_keeps_ledger_truthful(pg_conn, monkeypatch):
    # Pre-release dogfooding switch: with TAKYON_OPERATOR_USAGE_GATE_DISABLED set, an over-allowance
    # reserve must NOT raise (the operator agent is no longer throttled), yet the ledger stays
    # truthful — it holds exactly what the allowance still covers (clamped, never oversold), so a
    # following settle/refund is well-defined, balances never go negative, and reconcile still passes.
    monkeypatch.setenv("TAKYON_OPERATOR_USAGE_GATE_DISABLED", "1")
    uid = _user(pg_conn)
    billing.grant_allowance(pg_conn, uid, 1000, "g")
    # 1500 > 1000 available: the gate would normally refuse. Ungated, it clamps the hold to 1000.
    resv = billing.reserve(pg_conn, uid, 1500, "r1")
    assert resv.allowance_cents == 1000  # held what the allowance covered — no oversell
    bal = billing.get_billing_balances(pg_conn, uid)
    assert bal.allowance_used_cents == 1000
    assert bal.allowance_remaining_cents == 0  # floored at zero, never negative
    assert billing.reconcile_billing(pg_conn, uid)["ok"] is True
    # A further over-budget reserve (allowance already drained) holds a zero anchor and still runs —
    # settle/refund remain well-defined, so the agent path is never blocked.
    resv2 = billing.reserve(pg_conn, uid, 700, "r2")
    assert resv2.allowance_cents == 0
    billing.settle(pg_conn, "r2", 0)
    billing.settle(pg_conn, "r1", 1000)
    bal = billing.get_billing_balances(pg_conn, uid)
    assert bal.allowance_used_cents == 1000
    assert bal.reserved_cents == 0
    assert billing.reconcile_billing(pg_conn, uid)["ok"] is True


def test_dogfood_switch_off_by_default_still_gates(pg_conn, monkeypatch):
    # Default posture (env unset) is unchanged: the gate still fails closed on an exhausted allowance.
    monkeypatch.delenv("TAKYON_OPERATOR_USAGE_GATE_DISABLED", raising=False)
    uid = _user(pg_conn)
    billing.grant_allowance(pg_conn, uid, 1000, "g")
    with pytest.raises(InsufficientBalance):
        billing.reserve(pg_conn, uid, 1500, "r1")
    bal = billing.get_billing_balances(pg_conn, uid)
    assert bal.allowance_used_cents == 0  # nothing written — gate intact when the switch is off


def test_settle_actual_under_reserved_releases_difference(pg_conn):
    uid = _user(pg_conn)
    billing.grant_allowance(pg_conn, uid, 1000, "g")
    billing.reserve(pg_conn, uid, 800, "r1")
    billing.settle(pg_conn, "r1", 300)
    bal = billing.get_billing_balances(pg_conn, uid)
    assert bal.allowance_used_cents == 300  # only the 300 actually spent
    assert bal.allowance_remaining_cents == 700  # 1000 − 300
    assert bal.reserved_cents == 0
    assert billing.reconcile_billing(pg_conn, uid)["ok"] is True


def test_settle_full_consumes_reservation(pg_conn):
    uid = _user(pg_conn)
    billing.grant_allowance(pg_conn, uid, 1000, "g")
    billing.reserve(pg_conn, uid, 500, "r1")
    billing.settle(pg_conn, "r1", 500)
    bal = billing.get_billing_balances(pg_conn, uid)
    assert bal.allowance_used_cents == 500
    assert bal.allowance_remaining_cents == 500
    assert bal.reserved_cents == 0


def test_partial_settle_within_allowance_releases_remainder(pg_conn):
    uid = _user(pg_conn)
    billing.grant_allowance(pg_conn, uid, 2000, "g")
    billing.reserve(pg_conn, uid, 1500, "r1")  # held entirely against allowance
    billing.settle(pg_conn, "r1", 1200)  # spends 1200, releases the 300 remainder
    bal = billing.get_billing_balances(pg_conn, uid)
    assert bal.allowance_used_cents == 1200  # only the actual spend stays used
    assert bal.allowance_remaining_cents == 800  # 2000 − 1200
    assert bal.reserved_cents == 0
    assert billing.reconcile_billing(pg_conn, uid)["ok"] is True


def test_refund_releases_whole_reservation(pg_conn):
    uid = _user(pg_conn)
    billing.grant_allowance(pg_conn, uid, 1000, "g")
    billing.reserve(pg_conn, uid, 800, "r1")
    billing.refund(pg_conn, "r1")
    bal = billing.get_billing_balances(pg_conn, uid)
    assert bal.allowance_used_cents == 0  # fully restored
    assert bal.allowance_remaining_cents == 1000
    assert bal.reserved_cents == 0


def test_reserve_replay_returns_same_hold_without_recharging(pg_conn):
    uid = _user(pg_conn)
    billing.grant_allowance(pg_conn, uid, 1000, "g")
    first = billing.reserve(pg_conn, uid, 200, "dup")
    second = billing.reserve(pg_conn, uid, 200, "dup")
    assert first.allowance_cents == second.allowance_cents == 200
    bal = billing.get_billing_balances(pg_conn, uid)
    assert bal.allowance_used_cents == 200  # debited once, not twice
    assert bal.allowance_remaining_cents == 800
    assert bal.reserved_cents == 200


def test_settle_is_idempotent(pg_conn):
    uid = _user(pg_conn)
    billing.grant_allowance(pg_conn, uid, 1000, "g")
    billing.reserve(pg_conn, uid, 400, "r1")
    billing.settle(pg_conn, "r1", 250)
    billing.settle(pg_conn, "r1", 250)  # no-op
    bal = billing.get_billing_balances(pg_conn, uid)
    assert bal.allowance_used_cents == 250
    assert bal.allowance_remaining_cents == 750
    assert bal.reserved_cents == 0


def test_refund_after_settle_is_noop(pg_conn):
    uid = _user(pg_conn)
    billing.grant_allowance(pg_conn, uid, 1000, "g")
    billing.reserve(pg_conn, uid, 400, "r1")
    billing.settle(pg_conn, "r1", 400)
    billing.refund(pg_conn, "r1")  # already finalized → no-op
    bal = billing.get_billing_balances(pg_conn, uid)
    assert bal.allowance_used_cents == 400
    assert bal.allowance_remaining_cents == 600


def test_settle_more_than_reserved_rejected(pg_conn):
    uid = _user(pg_conn)
    billing.grant_allowance(pg_conn, uid, 1000, "g")
    billing.reserve(pg_conn, uid, 300, "r1")
    with pytest.raises(ValueError):
        billing.settle(pg_conn, "r1", 400)


def test_concurrent_reserves_never_oversell(pg_conn):
    uid = _user(pg_conn)
    billing.grant_allowance(pg_conn, uid, 1000, "g")  # exactly 10 reservations of 100 fit
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
    assert bal.allowance_remaining_cents == 0  # never driven negative
    assert bal.reserved_cents == 1000
    assert billing.reconcile_billing(pg_conn, uid)["ok"] is True


def test_concurrent_identical_reserve_charges_once(pg_conn):
    uid = _user(pg_conn)
    billing.grant_allowance(pg_conn, uid, 1000, "g")
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
    assert bal.allowance_used_cents == 100  # debited exactly once
    assert bal.allowance_remaining_cents == 900
    entries = pg_conn.execute(
        "select count(*) from billing_entries "
        "where reservation_key = 'same-key' and kind = 'reserve'",
        (),
    ).fetchone()[0]
    assert entries == 1
