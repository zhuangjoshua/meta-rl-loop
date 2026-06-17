"""Authoritative invariant 4 — NO LEDGER TAMPERING.

GOAL_RULES.md §3 invariant 4 (the prime directive's money-integrity clause):

    "Every ledger is append-only, idempotent on its key, asserts actual <= reserved,
     and reconciles cached == ledger."

The system is an authoritative money service: assume every caller (operator AND
sub-user) is EVIL and is trying to over-settle a reservation, replay a finalizer to
double-charge / double-refund, or drift the cached balance away from the append-only
ledger. This module pins that the THREE real ledgers refuse all of those, over the
WHOLE money surface — not one card:

  * ``plugins/takyon/billing.py``          — flow A, the Takyon user <-> platform rail.
  * ``plugins/takyon/app_usage.py``        — per-business product AI usage rail (microUSD).
  * ``plugins/takyon/business_credits.py`` — business-scoped creative-credit rail.

Grounding (every symbol below was confirmed by reading the real source before this
test was written):
  * ``billing.settle``  (billing.py:240) refuses ``actual_cents > a_resv + t_resv`` with a
    ValueError, and is a no-op on replay via ``_finalized`` (billing.py:265, :431).
  * ``billing.refund``  (billing.py:296) is a no-op on replay via ``_finalized``.
  * ``billing.reconcile_billing`` (billing.py:368) proves cached == ledger and flags
    ``allowance_oversold`` / ``reserved_*_negative`` / ``allowance_used`` /
    ``topup_balance`` drift.
  * ``business_credits.commit_credits`` (business_credits.py:321) refuses
    ``spent > reserve.amount_credits`` (line 367) and is a no-op on replay (the
    ``prior`` commit/release guard, line 358).
  * ``app_usage.reserve_usage`` (app_usage.py:323) is idempotent on the UNIQUE
    ``reservation_key`` — a replay returns the SAME reserved row without holding twice.
  * ``app_usage.settle_usage`` (app_usage.py:398) is idempotent on replay via
    ``_FINALIZED_STATUSES`` (app_usage.py:147). It deliberately does NOT re-check
    ``actual <= reserved`` (money-truth: once spent, recording is mandatory) — so the
    ``actual <= reserved`` clause of invariant 4 is enforced on the RESERVE-and-RELEASE
    rails, not on the post-hoc usage settle. This test asserts that exactly; it does NOT
    pretend app_usage refuses over-settle.

Test shape:
  * The structural / source-level tests need NO credential or network and run everywhere
    (the hermetic conftest scrubs creds + redirects TAKYON_HOME). They import the REAL
    symbols and assert the guard exists in the real source AST, so a future edit that
    deletes "actual <= reserved", the replay guard, or a reconcile drift check turns this
    suite RED.
  * The behavioral tests exercise the REAL engines on real Postgres via the repo's
    ``pg_conn`` fixture (throwaway per-worker DB + real migrations). They skip cleanly
    when ``TAKYON_TEST_PG_DSN`` is unset.

RED meaning: any RED here is a real invariant 4 breach (a ledger that lets over-settle
through, double-charges on replay, or drifts cached away from the ledger), NOT an
aspirational target — invariant 4 describes guards that exist in the current source.
"""

from __future__ import annotations

import ast
import inspect
import os
import textwrap
import uuid

import pytest

# Import the REAL ledger modules. A bare import failure here is itself an invariant-4
# regression (a money rail vanished or stopped importing).
from plugins.takyon import app_usage, billing, business_credits


# --------------------------------------------------------------------------------------
# Helpers — source introspection (no credentials, no network).
# --------------------------------------------------------------------------------------

def _func_source(func) -> str:
    return textwrap.dedent(inspect.getsource(func))


def _func_ast(func) -> ast.AST:
    return ast.parse(_func_source(func))


def _raises_valueerror_nodes(tree: ast.AST) -> list[ast.Raise]:
    out: list[ast.Raise] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Raise) and node.exc is not None:
            exc = node.exc
            name = None
            if isinstance(exc, ast.Call) and isinstance(exc.func, ast.Name):
                name = exc.func.id
            elif isinstance(exc, ast.Name):
                name = exc.id
            if name == "ValueError":
                out.append(node)
    return out


# --------------------------------------------------------------------------------------
# 1. The money surface is present and importable (all three ledgers).
# --------------------------------------------------------------------------------------

def test_all_three_ledger_rails_expose_reserve_finalize_api():
    """Every money rail must expose the reserve -> (settle|commit | release|refund)
    envelope. A missing finalizer is a tampering surface (spend with no way to record
    actual / release the hold)."""
    # billing: reserve -> settle | refund (+ reconcile)
    for name in ("reserve", "settle", "refund", "reconcile_billing", "topup", "grant_allowance"):
        assert callable(getattr(billing, name)), f"billing.{name} missing"
    # app_usage: reserve -> settle | release
    for name in ("reserve_usage", "settle_usage", "release_usage"):
        assert callable(getattr(app_usage, name)), f"app_usage.{name} missing"
    # business_credits: reserve -> commit | release (+ grant)
    for name in ("reserve_credits", "commit_credits", "release_credits", "grant_credits"):
        assert callable(getattr(business_credits, name)), f"business_credits.{name} missing"


# --------------------------------------------------------------------------------------
# 2. actual <= reserved is asserted where the invariant places it (reserve/credit rails).
# --------------------------------------------------------------------------------------

def test_billing_settle_refuses_over_settle_in_source():
    """billing.settle must reject actual_cents that exceeds the reserved total. Confirmed
    in source: ``if actual_cents > a_resv + t_resv: raise ValueError(...)``. We assert the
    guard structurally so deleting it (letting a caller settle MORE than they reserved =
    minting money) turns this RED."""
    src = _func_source(billing.settle)
    tree = _func_ast(billing.settle)
    # A comparison of the actual against the reserved sum, guarding a ValueError.
    assert "actual_cents > a_resv + t_resv" in src, (
        "billing.settle lost its actual<=reserved guard"
    )
    ve = _raises_valueerror_nodes(tree)
    assert ve, "billing.settle no longer raises ValueError on over-settle"
    # The guard must be a strict-greater comparison (over by exactly the reserved amount
    # is allowed; over by 1 is refused).
    has_gt = any(
        isinstance(n, ast.Compare) and any(isinstance(op, ast.Gt) for op in n.ops)
        for n in ast.walk(tree)
    )
    assert has_gt, "billing.settle over-settle guard is not a strict > comparison"


def test_billing_settle_rejects_negative_actual_in_source():
    src = _func_source(billing.settle)
    assert "actual_cents < 0" in src, "billing.settle dropped its negative-actual guard"


def test_business_credits_commit_refuses_over_commit_in_source():
    """business_credits.commit_credits must reject spent > reserved. Confirmed in source:
    ``if spent > reserve.amount_credits: raise ValueError(...)``."""
    src = _func_source(business_credits.commit_credits)
    tree = _func_ast(business_credits.commit_credits)
    assert "spent > reserve.amount_credits" in src, (
        "commit_credits lost its actual<=reserved guard"
    )
    assert _raises_valueerror_nodes(tree), (
        "commit_credits no longer raises ValueError on over-commit"
    )
    assert "spent < 0" in src, "commit_credits dropped its negative-actual guard"


def test_app_usage_settle_is_money_truth_not_a_silent_cap_recheck():
    """app_usage.settle_usage deliberately does NOT re-check actual <= reserved (money is
    already spent; recording it is mandatory). This pins that documented choice so the
    invariant-4 "actual <= reserved" clause is not falsely expected here — it lives on the
    billing/credits rails. If someone later makes settle_usage silently DROP real spend to
    fit the estimate, that is a different bug and this test documents the boundary."""
    src = _func_source(app_usage.settle_usage)
    # It records the true actual, and never raises ValueError for actual exceeding the
    # estimate (only for a negative actual).
    assert "actual_cost_microusd = %s" in src
    assert "estimated_cost_microusd" not in src.split("with conn.transaction()")[-1], (
        "settle_usage now compares actual against the estimate — verify this is intended; "
        "the reserve/credits rails own the actual<=reserved gate"
    )


# --------------------------------------------------------------------------------------
# 3. Idempotency — replaying a finalizer is a no-op (no double-charge / double-refund).
# --------------------------------------------------------------------------------------

def test_billing_settle_and_refund_guard_on_prior_finalization_in_source():
    """A replayed settle/refund on a finalized reservation must be a no-op. Confirmed in
    source: both call ``_finalized(conn, rk)`` and ``return`` early."""
    assert callable(getattr(billing, "_finalized"))
    for func in (billing.settle, billing.refund):
        src = _func_source(func)
        assert "_finalized(conn, rk)" in src, f"{func.__name__} lost its replay guard"
        # The finalized branch returns without writing.
        tree = _func_ast(func)
        guarded_return = False
        for node in ast.walk(tree):
            if isinstance(node, ast.If):
                cond = node.test
                if (
                    isinstance(cond, ast.Call)
                    and isinstance(cond.func, ast.Name)
                    and cond.func.id == "_finalized"
                    and any(isinstance(b, ast.Return) for b in node.body)
                ):
                    guarded_return = True
        assert guarded_return, f"{func.__name__} _finalized branch no longer returns early"


def test_app_usage_finalizers_guard_on_finalized_status_in_source():
    """app_usage.settle_usage / release_usage must no-op once the event is finalized.
    Confirmed in source: both check ``event.status in _FINALIZED_STATUSES`` and return the
    existing row."""
    assert app_usage._FINALIZED_STATUSES == ("completed", "failed", "released")
    for func in (app_usage.settle_usage, app_usage.release_usage):
        src = _func_source(func)
        assert "_FINALIZED_STATUSES" in src, f"{func.__name__} lost its replay guard"
        assert "return event" in src, f"{func.__name__} no longer returns the existing row"


def test_business_credits_commit_and_release_guard_on_prior_finalization_in_source():
    """commit_credits / release_credits must no-op on a reservation already
    committed/released. Confirmed in source: both query a ``prior`` commit/release row and
    return the cached balances when it exists."""
    for func in (business_credits.commit_credits, business_credits.release_credits):
        src = _func_source(func)
        assert "kind in ('commit', 'release')" in src, (
            f"{func.__name__} lost its prior-finalization replay query"
        )
        assert "if prior is not None" in src, (
            f"{func.__name__} no longer no-ops on a replayed finalizer"
        )


def test_reserve_paths_are_idempotent_on_their_key_in_source():
    """Each reserve must return the SAME existing reservation on a replayed key (hold
    once, never twice). Confirmed in source for all three rails."""
    # billing.reserve: replays the reservation_key's reserve entries.
    assert "reservation_key = %s and kind = 'reserve'" in _func_source(billing.reserve)
    # app_usage.reserve_usage: returns the existing row for a duplicate reservation_key.
    au = _func_source(app_usage.reserve_usage)
    assert "where business_slug = %s and reservation_key = %s" in au
    assert "if existing is not None" in au
    # business_credits.reserve_credits: returns the existing reserve for a duplicate key.
    bc = _func_source(business_credits.reserve_credits)
    assert "kind = 'reserve'" in bc
    assert "if existing is not None" in bc


# --------------------------------------------------------------------------------------
# 4. reconcile proves cached == ledger (and flags oversold / negative reservations).
# --------------------------------------------------------------------------------------

def test_reconcile_billing_recomputes_from_ledger_and_flags_drift_in_source():
    """reconcile_billing must (a) recompute used/topup from the append-only entries,
    (b) flag cached != ledger drift, (c) flag negative outstanding reservations, and
    (d) flag oversold allowance. Confirmed in source."""
    src = _func_source(billing.reconcile_billing)
    # Recompute from the ledger, not from the cached account columns.
    assert "from billing_entries" in src
    assert "calc_used" in src and "calc_topup" in src
    # Cached-vs-ledger drift comparisons.
    assert "calc_used != used" in src
    assert "calc_topup != topup_bal" in src
    # Negative outstanding reservation and oversold allowance are surfaced.
    assert "reserved_allowance < 0" in src
    assert "reserved_topup < 0" in src
    assert "used > included" in src
    # The result's ok flag is the negation of any drift.
    assert '"ok": not drift' in src


# --------------------------------------------------------------------------------------
# Behavioral proof on real Postgres (needs the pg rig; skips cleanly without it).
# --------------------------------------------------------------------------------------

psycopg = pytest.importorskip("psycopg")

from plugins.takyon import app_identity  # noqa: E402
from plugins.takyon.app_usage import UnknownReservation  # noqa: E402
from plugins.takyon.control_plane import provision_user_on_first_login  # noqa: E402

_PG = pytest.mark.skipif(
    not str(os.environ.get("TAKYON_TEST_PG_DSN") or "").strip(),
    reason="TAKYON_TEST_PG_DSN not set; Postgres invariant-4 behavioral test skipped",
)


def _user(conn) -> str:
    uid, _, _ = provision_user_on_first_login(conn, f"auth0|{uuid.uuid4().hex}")
    return uid


def _business(conn) -> str:
    owner = _user(conn)
    slug = f"biz-{uuid.uuid4().hex[:8]}"
    conn.execute(
        "insert into businesses (slug, name, owner_user_id) values (%s, %s, %s)",
        (slug, "Acme", owner),
    )
    return slug


@_PG
def test_pg_billing_over_settle_is_refused(pg_conn):
    """A caller that reserves N then tries to settle N+1 must be refused (no minting)."""
    uid = _user(pg_conn)
    billing.grant_allowance(pg_conn, uid, 1000, "grant-1")
    rk = "resv-over"
    billing.reserve(pg_conn, uid, 100, rk)
    with pytest.raises(ValueError):
        billing.settle(pg_conn, rk, 101)
    # The reservation is untouched and still reconciles.
    assert billing.reconcile_billing(pg_conn, uid)["ok"] is True


@_PG
def test_pg_billing_settle_replay_is_a_noop_no_double_charge(pg_conn):
    """Replaying settle on a finalized reservation must not move balances again."""
    uid = _user(pg_conn)
    billing.grant_allowance(pg_conn, uid, 1000, "grant-1")
    rk = "resv-replay"
    billing.reserve(pg_conn, uid, 100, rk)
    billing.settle(pg_conn, rk, 60)
    after_first = billing.get_billing_balances(pg_conn, uid)
    billing.settle(pg_conn, rk, 60)  # exact replay -> no-op (first finalizer wins)
    billing.settle(pg_conn, rk, 10)  # malicious cheaper re-settle -> still a no-op
    after_replays = billing.get_billing_balances(pg_conn, uid)
    assert after_replays.allowance_used_cents == after_first.allowance_used_cents
    assert after_replays.topup_balance_cents == after_first.topup_balance_cents
    assert after_replays.reserved_cents == 0
    # And a replay that ALSO tries to over-settle (actual > reserved) is refused outright,
    # not silently absorbed — the over-settle guard fires before the no-op path.
    with pytest.raises(ValueError):
        billing.settle(pg_conn, rk, 999)
    assert billing.reconcile_billing(pg_conn, uid)["ok"] is True


@_PG
def test_pg_billing_reconcile_holds_through_a_full_cycle(pg_conn):
    """cached == ledger across reserve -> settle (partial) -> refund-of-remainder."""
    uid = _user(pg_conn)
    billing.grant_allowance(pg_conn, uid, 500, "grant-1")
    assert billing.reconcile_billing(pg_conn, uid)["ok"] is True
    billing.reserve(pg_conn, uid, 200, "rk-a")
    assert billing.reconcile_billing(pg_conn, uid)["ok"] is True
    billing.settle(pg_conn, "rk-a", 50)  # releases 150
    rep = billing.reconcile_billing(pg_conn, uid)
    assert rep["ok"] is True
    assert rep["reserved_cents"] == 0


@_PG
def test_pg_app_usage_reserve_is_idempotent_on_key(pg_conn):
    """A replayed reservation_key returns the SAME reserved row (hold once)."""
    slug = _business(pg_conn)
    rk = "usage-rk-1"
    first = app_usage.reserve_usage(
        pg_conn, slug, estimated_cost_microusd=10_000, reservation_key=rk
    )
    second = app_usage.reserve_usage(
        pg_conn, slug, estimated_cost_microusd=999_999, reservation_key=rk
    )
    assert second.id == first.id
    assert second.estimated_cost_microusd == first.estimated_cost_microusd == 10_000
    # Only one event row exists for that key.
    n = pg_conn.execute(
        "select count(*) from app_usage_events where business_slug = %s and reservation_key = %s",
        (slug, rk),
    ).fetchone()[0]
    assert n == 1


@_PG
def test_pg_app_usage_settle_replay_is_a_noop(pg_conn):
    slug = _business(pg_conn)
    rk = "usage-rk-2"
    app_usage.reserve_usage(pg_conn, slug, estimated_cost_microusd=10_000, reservation_key=rk)
    settled = app_usage.settle_usage(pg_conn, slug, rk, actual_cost_microusd=7_000)
    replay = app_usage.settle_usage(pg_conn, slug, rk, actual_cost_microusd=999_999)
    assert replay.status == settled.status == "completed"
    assert replay.actual_cost_microusd == settled.actual_cost_microusd == 7_000


@_PG
def test_pg_app_usage_settle_unknown_key_is_refused(pg_conn):
    slug = _business(pg_conn)
    with pytest.raises(UnknownReservation):
        app_usage.settle_usage(pg_conn, slug, "never-reserved", actual_cost_microusd=1)


@_PG
def test_pg_business_credits_over_commit_is_refused(pg_conn):
    """Reserve N credits, try to commit N+1 -> refused (no credit minting)."""
    slug = _business(pg_conn)
    business_credits.grant_credits(pg_conn, slug, 10, "grant-1")
    business_credits.reserve_credits(pg_conn, slug, 4, "credit-rk-1")
    with pytest.raises(ValueError):
        business_credits.commit_credits(pg_conn, "credit-rk-1", actual_credits=5)


@_PG
def test_pg_business_credits_commit_replay_is_a_noop(pg_conn):
    slug = _business(pg_conn)
    business_credits.grant_credits(pg_conn, slug, 10, "grant-1")
    business_credits.reserve_credits(pg_conn, slug, 6, "credit-rk-2")
    first = business_credits.commit_credits(pg_conn, "credit-rk-2", actual_credits=4)
    replay = business_credits.commit_credits(pg_conn, "credit-rk-2", actual_credits=4)
    # 6 reserved, 4 spent -> 2 refunded; balance 10-6+2 = 6, and replay does not refund again.
    assert first.balance_credits == 6
    assert replay.balance_credits == 6
