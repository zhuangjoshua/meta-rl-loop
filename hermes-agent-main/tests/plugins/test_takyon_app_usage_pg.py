"""Postgres integration tests for the product AI-spend budget gate — Phase 5(c).

Phase 5 acceptance (this slice): a business spends on AI on behalf of its sub-users under ONE
authoritative reserve-then-settle gate, replacing the SQLite trunk's broken two-path gate. The
correctness this pins:
  * reserve() is the single gate — it holds the estimate atomically and is the only thing that
    can refuse spend; committed = Σ(reserved estimates) + Σ(completed actuals) in the period;
  * settle() records the REAL provider spend and never re-checks the cap (money-truth: once spent,
    recording is mandatory — even if actual slightly exceeds the reserved estimate);
  * release() frees a held reservation on the failure path (no spend recorded);
  * the gate is idempotent on reservation_key (replay holds/charges once) and business-scoped;
  * under real concurrency the cap is NEVER oversold — the test that the SQLite read-then-act
    pre-check would fail.

Real engine on real Postgres (never mocks). Skips unless psycopg is importable and
TAKYON_TEST_PG_DSN is set.
"""

from __future__ import annotations

import os
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

import pytest

psycopg = pytest.importorskip("psycopg")

from plugins.takyon import app_identity, app_usage  # noqa: E402
from plugins.takyon.app_usage import (  # noqa: E402
    AppBudgetExceeded,
    AppBudgetInactive,
    AppUserBudgetExceeded,
    AppUserNotFound,
    UnknownReservation,
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


def _user(conn, slug, email="cust@example.com") -> str:
    return app_identity.upsert_app_user(conn, slug, email).id


def _new_conn(pg_conn):
    """A fresh autocommit connection to the SAME throwaway DB — for real concurrency."""
    return psycopg.connect(
        os.environ["TAKYON_TEST_PG_DSN"], dbname=pg_conn.info.dbname, autocommit=True
    )


# ── budget catalog ─────────────────────────────────────────────────────────────────


def test_ensure_budget_opens_with_no_pool_cap_and_is_idempotent(pg_conn):
    # Invariant 9 (GOAL_RULES §3, migration 0029): a new budget opens with NO per-business pool
    # cap (hard_limit_microusd = NULL). There is no flat $5 default pool; budget derives from the
    # active paid subscription's per-subuser included_ai_budget_microusd (the per-subuser gate).
    slug = _business(pg_conn, _owner(pg_conn))
    budget = app_usage.ensure_app_budget(pg_conn, slug)
    assert budget.status == "active"
    assert budget.hard_limit_microusd is None  # no per-business pool cap (sentinel)
    assert isinstance(budget.current_period_start, datetime)
    assert budget.current_period_end > budget.current_period_start
    app_usage.ensure_app_budget(pg_conn, slug)  # second call must not open a second row
    count = pg_conn.execute(
        "select count(*) from app_budgets where business_slug = %s", (slug,)
    ).fetchone()[0]
    assert count == 1


def test_set_budget_sets_cap_and_status_opening_if_absent(pg_conn):
    slug = _business(pg_conn, _owner(pg_conn))
    budget = app_usage.set_app_budget(pg_conn, slug, hard_limit_microusd=1_000)
    assert (budget.hard_limit_microusd, budget.status) == (1_000, "active")
    suspended = app_usage.set_app_budget(
        pg_conn, slug, hard_limit_microusd=2_000, status="suspended"
    )
    assert (suspended.hard_limit_microusd, suspended.status) == (2_000, "suspended")


def test_get_budget_is_none_until_opened(pg_conn):
    slug = _business(pg_conn, _owner(pg_conn))
    assert app_usage.get_app_budget(pg_conn, slug) is None


def test_summary_for_unopened_budget_reports_missing_with_no_pool(pg_conn):
    # Invariant 9: a never-opened budget must NOT hand back a free per-business pool. It reports
    # status 'missing' with NO pool cap (hard_limit/remaining = None) — an unentitled business
    # has 0 product budget (the per-subuser subscription gate, not a pool, governs spend).
    slug = _business(pg_conn, _owner(pg_conn))
    summary = app_usage.get_usage_summary(pg_conn, slug)
    assert summary["status"] == "missing"
    assert summary["hard_limit_microusd"] is None
    assert summary["committed_microusd"] == 0
    assert summary["remaining_microusd"] is None


def test_set_budget_unknown_business_raises(pg_conn):
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        app_usage.set_app_budget(pg_conn, "nope", hard_limit_microusd=1_000)


def test_set_budget_negative_cap_raises(pg_conn):
    slug = _business(pg_conn, _owner(pg_conn))
    with pytest.raises(ValueError):
        app_usage.set_app_budget(pg_conn, slug, hard_limit_microusd=-1)


# ── the gate: reserve → settle | release ───────────────────────────────────────────


def test_reserve_holds_estimate_then_settle_records_actual(pg_conn):
    slug = _business(pg_conn, _owner(pg_conn))
    app_usage.set_app_budget(pg_conn, slug, hard_limit_microusd=1_000)
    ev = app_usage.reserve_usage(pg_conn, slug, estimated_cost_microusd=400, reservation_key="r1")
    assert ev.status == "reserved"
    assert ev.estimated_cost_microusd == 400
    assert ev.actual_cost_microusd == 0
    # while reserved, the held estimate counts as committed
    summary = app_usage.get_usage_summary(pg_conn, slug)
    assert summary["committed_microusd"] == 400
    assert summary["remaining_microusd"] == 600
    # settle at a lower actual: committed drops to the actual, the slack is freed
    settled = app_usage.settle_usage(pg_conn, slug, "r1", actual_cost_microusd=250)
    assert settled.status == "completed"
    assert settled.actual_cost_microusd == 250
    summary = app_usage.get_usage_summary(pg_conn, slug)
    assert summary["committed_microusd"] == 250
    assert summary["remaining_microusd"] == 750


def test_settle_records_true_actual_even_if_over_estimate(pg_conn):
    # money-truth: the cap is enforced at reserve; settle records what the provider really cost,
    # even if it slightly exceeds the reserved estimate. Refusing would under-count real spend.
    slug = _business(pg_conn, _owner(pg_conn))
    app_usage.set_app_budget(pg_conn, slug, hard_limit_microusd=1_000)
    app_usage.reserve_usage(pg_conn, slug, estimated_cost_microusd=100, reservation_key="r1")
    settled = app_usage.settle_usage(pg_conn, slug, "r1", actual_cost_microusd=150)
    assert settled.actual_cost_microusd == 150  # recorded, not raised, not capped
    assert app_usage.get_usage_summary(pg_conn, slug)["committed_microusd"] == 150


def test_release_frees_the_hold(pg_conn):
    slug = _business(pg_conn, _owner(pg_conn))
    app_usage.set_app_budget(pg_conn, slug, hard_limit_microusd=1_000)
    app_usage.reserve_usage(pg_conn, slug, estimated_cost_microusd=400, reservation_key="r1")
    assert app_usage.get_usage_summary(pg_conn, slug)["committed_microusd"] == 400
    released = app_usage.release_usage(pg_conn, slug, "r1", error="provider 500")
    assert released.status == "failed"  # an error → 'failed'
    assert released.actual_cost_microusd == 0
    assert released.error == "provider 500"
    # the hold is fully freed
    assert app_usage.get_usage_summary(pg_conn, slug)["committed_microusd"] == 0


def test_release_without_error_marks_released(pg_conn):
    slug = _business(pg_conn, _owner(pg_conn))
    app_usage.set_app_budget(pg_conn, slug, hard_limit_microusd=1_000)
    app_usage.reserve_usage(pg_conn, slug, estimated_cost_microusd=400, reservation_key="r1")
    released = app_usage.release_usage(pg_conn, slug, "r1")
    assert released.status == "released"  # clean cancel, no error
    assert released.actual_cost_microusd == 0


def test_reserve_is_idempotent_on_reservation_key(pg_conn):
    slug = _business(pg_conn, _owner(pg_conn))
    app_usage.set_app_budget(pg_conn, slug, hard_limit_microusd=1_000)
    first = app_usage.reserve_usage(pg_conn, slug, estimated_cost_microusd=400, reservation_key="r1")
    again = app_usage.reserve_usage(pg_conn, slug, estimated_cost_microusd=400, reservation_key="r1")
    assert again.id == first.id  # same row, not a second hold
    assert app_usage.get_usage_summary(pg_conn, slug)["committed_microusd"] == 400  # held once
    rows = pg_conn.execute(
        "select count(*) from app_usage_events where business_slug = %s and reservation_key = 'r1'",
        (slug,),
    ).fetchone()[0]
    assert rows == 1


def test_settle_is_idempotent_first_finalizer_wins(pg_conn):
    slug = _business(pg_conn, _owner(pg_conn))
    app_usage.set_app_budget(pg_conn, slug, hard_limit_microusd=1_000)
    app_usage.reserve_usage(pg_conn, slug, estimated_cost_microusd=400, reservation_key="r1")
    app_usage.settle_usage(pg_conn, slug, "r1", actual_cost_microusd=250)
    # a second settle is a no-op; the first finalization stands
    again = app_usage.settle_usage(pg_conn, slug, "r1", actual_cost_microusd=999)
    assert again.actual_cost_microusd == 250
    assert app_usage.get_usage_summary(pg_conn, slug)["committed_microusd"] == 250


def test_release_after_settle_is_noop(pg_conn):
    slug = _business(pg_conn, _owner(pg_conn))
    app_usage.set_app_budget(pg_conn, slug, hard_limit_microusd=1_000)
    app_usage.reserve_usage(pg_conn, slug, estimated_cost_microusd=400, reservation_key="r1")
    app_usage.settle_usage(pg_conn, slug, "r1", actual_cost_microusd=250)
    released = app_usage.release_usage(pg_conn, slug, "r1", error="late")
    assert released.status == "completed"  # already finalized; release is a no-op
    assert released.actual_cost_microusd == 250


def test_settle_unknown_reservation_raises(pg_conn):
    slug = _business(pg_conn, _owner(pg_conn))
    app_usage.set_app_budget(pg_conn, slug, hard_limit_microusd=1_000)
    with pytest.raises(UnknownReservation):
        app_usage.settle_usage(pg_conn, slug, "ghost", actual_cost_microusd=10)


def test_release_unknown_reservation_raises(pg_conn):
    slug = _business(pg_conn, _owner(pg_conn))
    app_usage.set_app_budget(pg_conn, slug, hard_limit_microusd=1_000)
    with pytest.raises(UnknownReservation):
        app_usage.release_usage(pg_conn, slug, "ghost")


def test_reserve_refused_when_budget_inactive_writes_nothing(pg_conn):
    slug = _business(pg_conn, _owner(pg_conn))
    app_usage.set_app_budget(pg_conn, slug, hard_limit_microusd=1_000, status="suspended")
    with pytest.raises(AppBudgetInactive):
        app_usage.reserve_usage(pg_conn, slug, estimated_cost_microusd=10, reservation_key="r1")
    rows = pg_conn.execute(
        "select count(*) from app_usage_events where business_slug = %s", (slug,)
    ).fetchone()[0]
    assert rows == 0


def test_reserve_over_cap_raises_with_figures_and_writes_nothing(pg_conn):
    slug = _business(pg_conn, _owner(pg_conn))
    app_usage.set_app_budget(pg_conn, slug, hard_limit_microusd=1_000)
    app_usage.reserve_usage(pg_conn, slug, estimated_cost_microusd=800, reservation_key="r1")
    with pytest.raises(AppBudgetExceeded) as excinfo:
        app_usage.reserve_usage(pg_conn, slug, estimated_cost_microusd=300, reservation_key="r2")
    exc = excinfo.value
    assert exc.hard_limit_microusd == 1_000
    assert exc.committed_microusd == 800
    assert exc.requested_microusd == 300
    assert exc.remaining_microusd == 200
    # only the first reservation exists
    rows = pg_conn.execute(
        "select count(*) from app_usage_events where business_slug = %s", (slug,)
    ).fetchone()[0]
    assert rows == 1


def test_freed_headroom_lets_a_later_reserve_fit(pg_conn):
    slug = _business(pg_conn, _owner(pg_conn))
    app_usage.set_app_budget(pg_conn, slug, hard_limit_microusd=1_000)
    app_usage.reserve_usage(pg_conn, slug, estimated_cost_microusd=800, reservation_key="r1")
    # settling at a much lower actual frees headroom that a previously-too-big reserve now fits in
    app_usage.settle_usage(pg_conn, slug, "r1", actual_cost_microusd=200)
    ev = app_usage.reserve_usage(pg_conn, slug, estimated_cost_microusd=700, reservation_key="r2")
    assert ev.status == "reserved"
    assert app_usage.get_usage_summary(pg_conn, slug)["committed_microusd"] == 900


def test_reserve_with_unknown_app_user_raises(pg_conn):
    slug = _business(pg_conn, _owner(pg_conn))
    other = _business(pg_conn, _owner(pg_conn), name="Other")
    app_usage.set_app_budget(pg_conn, slug, hard_limit_microusd=1_000)
    cross = _user(pg_conn, other)  # a real sub-user, but of a DIFFERENT business
    with pytest.raises(AppUserNotFound):
        app_usage.reserve_usage(
            pg_conn, slug, estimated_cost_microusd=10, reservation_key="r1", app_user_id=cross
        )


def test_reserve_enforces_per_user_monthly_budget(pg_conn):
    slug = _business(pg_conn, _owner(pg_conn))
    user_id = _user(pg_conn, slug)
    app_usage.set_app_budget(pg_conn, slug, hard_limit_microusd=10_000)
    app_usage.record_completed_usage(
        pg_conn,
        slug,
        actual_cost_microusd=600,
        reservation_key="u1",
        app_user_id=user_id,
        user_monthly_limit_microusd=1_000,
    )
    with pytest.raises(AppUserBudgetExceeded) as excinfo:
        app_usage.reserve_usage(
            pg_conn,
            slug,
            estimated_cost_microusd=500,
            reservation_key="r2",
            app_user_id=user_id,
            user_monthly_limit_microusd=1_000,
        )
    exc = excinfo.value
    assert exc.app_user_id == user_id
    assert exc.user_monthly_limit_microusd == 1_000
    assert exc.committed_microusd == 600
    assert exc.requested_microusd == 500
    assert exc.remaining_microusd == 400


def test_reserve_validates_inputs(pg_conn):
    slug = _business(pg_conn, _owner(pg_conn))
    app_usage.set_app_budget(pg_conn, slug, hard_limit_microusd=1_000)
    with pytest.raises(ValueError):
        app_usage.reserve_usage(pg_conn, slug, estimated_cost_microusd=-1, reservation_key="r1")
    with pytest.raises(ValueError):
        app_usage.reserve_usage(pg_conn, slug, estimated_cost_microusd=10, reservation_key="  ")


def test_settle_coalesce_preserves_reserve_fields_and_merges_metadata(pg_conn):
    slug = _business(pg_conn, _owner(pg_conn))
    app_usage.set_app_budget(pg_conn, slug, hard_limit_microusd=1_000)
    app_usage.reserve_usage(
        pg_conn,
        slug,
        estimated_cost_microusd=400,
        reservation_key="r1",
        provider="anthropic",
        model="claude",
        metadata={"a": 1},
    )
    # settle omits provider/model → the reserve values survive; metadata is merged
    settled = app_usage.settle_usage(
        pg_conn, slug, "r1", actual_cost_microusd=250, metadata={"b": 2}
    )
    assert settled.provider == "anthropic"
    assert settled.model == "claude"
    assert settled.metadata == {"a": 1, "b": 2}


def test_record_completed_usage_gates_and_writes_in_one_shot(pg_conn):
    slug = _business(pg_conn, _owner(pg_conn))
    app_usage.set_app_budget(pg_conn, slug, hard_limit_microusd=1_000)
    ev = app_usage.record_completed_usage(
        pg_conn, slug, actual_cost_microusd=300, reservation_key="u1", purpose="product_usage"
    )
    assert ev.status == "completed"
    assert ev.actual_cost_microusd == 300
    assert app_usage.get_usage_summary(pg_conn, slug)["committed_microusd"] == 300
    # idempotent on reservation_key
    again = app_usage.record_completed_usage(pg_conn, slug, actual_cost_microusd=300, reservation_key="u1")
    assert again.id == ev.id
    assert app_usage.get_usage_summary(pg_conn, slug)["committed_microusd"] == 300


def test_record_completed_usage_refused_over_cap(pg_conn):
    slug = _business(pg_conn, _owner(pg_conn))
    app_usage.set_app_budget(pg_conn, slug, hard_limit_microusd=1_000)
    app_usage.record_completed_usage(pg_conn, slug, actual_cost_microusd=800, reservation_key="u1")
    with pytest.raises(AppBudgetExceeded):
        app_usage.record_completed_usage(pg_conn, slug, actual_cost_microusd=300, reservation_key="u2")


def test_record_completed_usage_gate_uses_max_of_estimate_and_actual(pg_conn):
    slug = _business(pg_conn, _owner(pg_conn))
    app_usage.set_app_budget(pg_conn, slug, hard_limit_microusd=1_000)
    app_usage.record_completed_usage(pg_conn, slug, actual_cost_microusd=600, reservation_key="u1")
    # actual 300 would fit, but the larger estimate 500 pushes committed past the cap
    with pytest.raises(AppBudgetExceeded):
        app_usage.record_completed_usage(
            pg_conn, slug, actual_cost_microusd=300, estimated_cost_microusd=500, reservation_key="u2"
        )


def test_record_completed_usage_enforces_per_user_monthly_budget(pg_conn):
    slug = _business(pg_conn, _owner(pg_conn))
    user_id = _user(pg_conn, slug)
    app_usage.set_app_budget(pg_conn, slug, hard_limit_microusd=10_000)
    app_usage.record_completed_usage(
        pg_conn,
        slug,
        actual_cost_microusd=700,
        reservation_key="u1",
        app_user_id=user_id,
        user_monthly_limit_microusd=1_000,
    )
    with pytest.raises(AppUserBudgetExceeded):
        app_usage.record_completed_usage(
            pg_conn,
            slug,
            actual_cost_microusd=200,
            estimated_cost_microusd=400,
            reservation_key="u2",
            app_user_id=user_id,
            user_monthly_limit_microusd=1_000,
        )


def test_reservation_keys_are_business_scoped(pg_conn):
    owner = _owner(pg_conn)
    a = _business(pg_conn, owner, name="A")
    b = _business(pg_conn, owner, name="B")
    app_usage.set_app_budget(pg_conn, a, hard_limit_microusd=1_000)
    app_usage.set_app_budget(pg_conn, b, hard_limit_microusd=1_000)
    # the SAME key in two businesses are independent reservations
    ea = app_usage.reserve_usage(pg_conn, a, estimated_cost_microusd=400, reservation_key="same")
    eb = app_usage.reserve_usage(pg_conn, b, estimated_cost_microusd=400, reservation_key="same")
    assert ea.id != eb.id
    assert app_usage.get_usage_summary(pg_conn, a)["committed_microusd"] == 400
    assert app_usage.get_usage_summary(pg_conn, b)["committed_microusd"] == 400


def test_usage_event_survives_subuser_delete(pg_conn):
    slug = _business(pg_conn, _owner(pg_conn))
    app_usage.set_app_budget(pg_conn, slug, hard_limit_microusd=1_000)
    user_id = _user(pg_conn, slug)
    app_usage.record_completed_usage(
        pg_conn, slug, actual_cost_microusd=100, reservation_key="u1", app_user_id=user_id
    )
    pg_conn.execute("delete from app_users where id = %s", (user_id,))
    # the spend record remains (app_user_id SET NULL), so the ledger keeps its history
    ev = app_usage.list_usage_events(pg_conn, slug)[0]
    assert ev.app_user_id is None
    assert ev.actual_cost_microusd == 100


def test_list_usage_events_newest_first_and_per_user_filter(pg_conn):
    slug = _business(pg_conn, _owner(pg_conn))
    app_usage.set_app_budget(pg_conn, slug, hard_limit_microusd=10_000)
    alice = _user(pg_conn, slug, email="alice@example.com")
    bob = _user(pg_conn, slug, email="bob@example.com")
    app_usage.record_completed_usage(pg_conn, slug, actual_cost_microusd=10, reservation_key="a1", app_user_id=alice)
    app_usage.record_completed_usage(pg_conn, slug, actual_cost_microusd=20, reservation_key="b1", app_user_id=bob)
    app_usage.record_completed_usage(pg_conn, slug, actual_cost_microusd=30, reservation_key="a2", app_user_id=alice)
    all_events = app_usage.list_usage_events(pg_conn, slug)
    assert [e.reservation_key for e in all_events] == ["a2", "b1", "a1"]  # newest first
    alice_events = app_usage.list_usage_events(pg_conn, slug, app_user_id=alice)
    assert {e.reservation_key for e in alice_events} == {"a1", "a2"}


# ── concurrency: the double-charge fix (the point of this increment) ────────────────


def test_concurrent_reserves_never_overspend(pg_conn):
    # The SQLite two-path gate would FAIL this: its estimate pre-check read remaining without
    # reserving, so N concurrent callers all saw headroom and all proceeded. Here the budget-row
    # lock serializes reserves, so the cap holds exactly.
    slug = _business(pg_conn, _owner(pg_conn))
    app_usage.set_app_budget(pg_conn, slug, hard_limit_microusd=1_000)  # exactly 10 holds of 100 fit
    n = 25
    barrier = threading.Barrier(n)

    def worker(i: int):
        conn = _new_conn(pg_conn)
        try:
            barrier.wait()
            app_usage.reserve_usage(conn, slug, estimated_cost_microusd=100, reservation_key=f"r{i}")
            return "ok"
        except AppBudgetExceeded:
            return "exceeded"
        finally:
            conn.close()

    with ThreadPoolExecutor(max_workers=n) as ex:
        results = list(ex.map(worker, range(n)))

    assert results.count("ok") == 10
    assert results.count("exceeded") == 15  # no other outcome (no errors, no oversell)
    summary = app_usage.get_usage_summary(pg_conn, slug)
    assert summary["committed_microusd"] == 1_000  # never driven over the cap
    assert summary["remaining_microusd"] == 0


def test_concurrent_identical_reservation_key_holds_once(pg_conn):
    slug = _business(pg_conn, _owner(pg_conn))
    app_usage.set_app_budget(pg_conn, slug, hard_limit_microusd=1_000)
    n = 10
    barrier = threading.Barrier(n)

    def worker(_):
        conn = _new_conn(pg_conn)
        try:
            barrier.wait()
            app_usage.reserve_usage(conn, slug, estimated_cost_microusd=100, reservation_key="same")
            return "ok"
        finally:
            conn.close()

    with ThreadPoolExecutor(max_workers=n) as ex:
        results = list(ex.map(worker, range(n)))

    assert results.count("ok") == n  # all succeed (replay is not an error)
    assert app_usage.get_usage_summary(pg_conn, slug)["committed_microusd"] == 100  # held once
    rows = pg_conn.execute(
        "select count(*) from app_usage_events where business_slug = %s and reservation_key = 'same'",
        (slug,),
    ).fetchone()[0]
    assert rows == 1


# ── ledger privilege boundary (migration 0037) ──────────────────────────────────────
# The reserve/settle/release row ops now live in SECURITY DEFINER functions that are the ONLY
# sanctioned writers of app_usage_events. The restricted app-request role (takyon_app, migration
# 0030) keeps SELECT but LOSES direct INSERT/UPDATE/DELETE, so a forged or stray write under the
# app scope is denied at the DB while the gate function — owned by the privileged role — still
# writes. This is the integrity boundary the safebox broker depends on.


def test_takyon_app_role_cannot_write_usage_events_directly(pg_conn):
    slug = _business(pg_conn, _owner(pg_conn))
    app_usage.ensure_app_budget(pg_conn, slug)
    # Drop to the restricted app-request role for the forged write, exactly as a stray app-scoped
    # query would run; RESET ROLE afterwards so the fixture teardown keeps its privileged role.
    pg_conn.execute("set role takyon_app")
    try:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            pg_conn.execute(
                "insert into app_usage_events "
                "(business_slug, reservation_key, route, purpose, status, actual_cost_microusd) "
                "values (%s, 'forge', 'app', 'product_usage', 'completed', 999)",
                (slug,),
            )
    finally:
        pg_conn.execute("reset role")
    # The forged row was never written.
    assert app_usage.list_usage_events(pg_conn, slug) == []
    # SELECT is retained: the role can still read (the 0027 RLS read path needs it).
    pg_conn.execute("set role takyon_app")
    try:
        pg_conn.execute("select count(*) from app_usage_events where business_slug = %s", (slug,))
    finally:
        pg_conn.execute("reset role")


def test_gate_function_writes_usage_even_under_restricted_app_role(pg_conn):
    # The SECURITY DEFINER gate runs as its privileged owner, so reserve_usage SUCCEEDS even when the
    # connection is dropped to the non-writing app role — the gate is the one sanctioned writer.
    slug = _business(pg_conn, _owner(pg_conn))
    app_usage.set_app_budget(pg_conn, slug, hard_limit_microusd=1_000)
    pg_conn.execute("set role takyon_app")
    try:
        ev = app_usage.reserve_usage(
            pg_conn, slug, estimated_cost_microusd=100, reservation_key="r1"
        )
    finally:
        pg_conn.execute("reset role")
    assert ev.status == "reserved"
    assert ev.estimated_cost_microusd == 100
    assert app_usage.get_usage_summary(pg_conn, slug)["committed_microusd"] == 100


def test_reconcile_held_usage_releases_orphaned_holds(pg_conn):
    # A reserved row whose provider call never settled/released would pin its estimate against
    # committed spend forever; the reconciliation sweep releases reserved rows past an age cutoff.
    slug = _business(pg_conn, _owner(pg_conn))
    app_usage.set_app_budget(pg_conn, slug, hard_limit_microusd=1_000)
    app_usage.reserve_usage(pg_conn, slug, estimated_cost_microusd=400, reservation_key="held")
    assert app_usage.get_usage_summary(pg_conn, slug)["committed_microusd"] == 400
    # cutoff 0s → every reserved row is eligible; one orphaned hold is reconciled to 'released'.
    released = app_usage.reconcile_held_usage(pg_conn, older_than_seconds=0)
    assert released == 1
    ev = app_usage.list_usage_events(pg_conn, slug)[0]
    assert ev.status == "released"
    assert ev.actual_cost_microusd == 0
    # the held estimate is freed — committed drops back to zero.
    assert app_usage.get_usage_summary(pg_conn, slug)["committed_microusd"] == 0
