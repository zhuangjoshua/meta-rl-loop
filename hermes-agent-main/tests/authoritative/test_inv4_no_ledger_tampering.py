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
  * ``billing.settle`` refuses ``actual_cents > a_resv`` (the reserved allowance) with a
    ValueError, and is a no-op on replay via ``_finalized``.
  * ``billing.release_reservation`` is a no-op on replay via ``_finalized``.
  * ``billing.reconcile_billing`` proves cached == ledger and flags
    ``allowance_oversold`` / ``reserved_allowance_negative`` / ``allowance_used`` drift.
    (The à-la-carte topup bucket was removed 2026-06-18; the rail is allowance-only.)
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
import contextlib
import inspect
import os
import pathlib
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


# The 0037 refactor moved the product-usage RESERVE/SETTLE/RELEASE row ops out of open Python writes
# in app_usage.py and into SECURITY DEFINER SQL functions (the privilege boundary — only the gate can
# write the ledger). The money-integrity guards (finalized-status no-op, true-actual settle with NO
# estimate cap, idempotent reservation_key lookup) therefore now live in the migration SQL, not the
# Python source, so the source-level invariant tests assert them THERE.
_LEDGER_BOUNDARY_SQL = (
    pathlib.Path(__file__).resolve().parents[2]
    / "plugins"
    / "takyon"
    / "db"
    / "migrations"
    / "0037_safebox_ledger_boundary.sql"
)


def _ledger_boundary_sql() -> str:
    return _LEDGER_BOUNDARY_SQL.read_text(encoding="utf-8")


def _sql_function_body(sql: str, fn_name: str) -> str:
    """Slice the body of ``create or replace function <fn_name>(`` up to the next
    ``create or replace function `` (or EOF). Lets the source-level tests assert a guard lives in ONE
    specific gate function, not merely somewhere in the migration."""
    marker = f"create or replace function {fn_name}("
    start = sql.find(marker)
    assert start != -1, f"{fn_name} not found in ledger-boundary SQL"
    rest = sql.find("create or replace function ", start + len(marker))
    return sql[start:] if rest == -1 else sql[start:rest]


# Migration 0038 moved the operator-BILLING / creative-CREDIT / CUSTODY reserve/settle/refund/grant row
# ops out of open Python writes into SECURITY DEFINER functions (mirroring 0037 for product-usage), so
# the runtime can be demoted off the money ledgers. The replay/idempotency guards now live in THAT SQL.
_RUNTIME_LEAST_PRIV_SQL = (
    pathlib.Path(__file__).resolve().parents[2]
    / "plugins"
    / "takyon"
    / "db"
    / "migrations"
    / "0038_runtime_least_privilege.sql"
)


def _runtime_least_priv_sql() -> str:
    return _RUNTIME_LEAST_PRIV_SQL.read_text(encoding="utf-8")


_BILLING_RELEASE_SQL = _RUNTIME_LEAST_PRIV_SQL.with_name(
    "0088_billing_reservation_release.sql"
)


def _billing_release_sql() -> str:
    return _BILLING_RELEASE_SQL.read_text(encoding="utf-8")


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
    """Every money rail must expose the reserve -> (settle|commit|release)
    envelope. A missing finalizer is a tampering surface (spend with no way to record
    actual / release the hold)."""
    # billing: reserve -> settle | release (+ reconcile, grant_allowance for the flow-A credit)
    for name in (
        "reserve",
        "settle",
        "release_reservation",
        "reconcile_billing",
        "grant_allowance",
    ):
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
    in source: ``if actual_cents > a_resv: raise ValueError(...)``. We assert the guard
    structurally so deleting it (letting a caller settle MORE than they reserved = minting
    money) turns this RED."""
    src = _func_source(billing.settle)
    tree = _func_ast(billing.settle)
    # A comparison of the actual against the reserved sum, guarding a ValueError.
    assert "actual_cents > a_resv" in src, (
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
    # Post-0038 the over-commit / negative guards are pre-checked in the Python wrapper (ValueErrors,
    # not ledger refusals) BEFORE the SECURITY DEFINER gate writes — exactly as before, just worded
    # against the locally-read reserved amount.
    assert "spent > reserved" in src, (
        "commit_credits lost its actual<=reserved guard"
    )
    assert _raises_valueerror_nodes(tree), (
        "commit_credits no longer raises ValueError on over-commit"
    )
    assert "spent < 0" in src, "commit_credits dropped its negative-actual guard"


def test_app_usage_settle_is_money_truth_not_a_silent_cap_recheck():
    """The settle gate deliberately does NOT re-check actual <= reserved (money is already
    spent; recording it is mandatory). This pins that documented choice so the invariant-4
    "actual <= reserved" clause is not falsely expected here — it lives on the billing/credits
    rails. Post-0037 the settle row op is the ``safebox_settle_usage`` SECURITY DEFINER function,
    so we assert there: it writes the TRUE provider actual and never caps it to the estimate
    (no ``least(`` / ``min(`` and no read of the reserved ``estimated_cost_microusd``)."""
    settle = _sql_function_body(_ledger_boundary_sql(), "safebox_settle_usage")
    # It records the true actual the caller supplied (the settle write), not a capped figure.
    assert "actual_cost_microusd = p_actual_cost_microusd" in settle, (
        "safebox_settle_usage no longer records the true provider actual"
    )
    # No cap/clamp of the actual against the reserved estimate anywhere in the function (that would
    # silently drop real spend).
    assert "least(" not in settle.lower(), "safebox_settle_usage now caps actual via least(...)"
    assert "min(" not in settle.lower(), "safebox_settle_usage now caps actual via min(...)"
    # The WRITE path (the UPDATE ... SET that records the cost) must not read the reserved estimate to
    # bound the actual. The finalized-no-op SELECT legitimately reads back the full event row (every
    # column, incl. estimated_cost_microusd), so scope the estimate check to the UPDATE write block.
    update_write = settle.split("update app_usage_events set", 1)[-1].split("returning", 1)[0]
    assert "estimated_cost_microusd" not in update_write, (
        "safebox_settle_usage's write now reads the reserved estimate to cap the actual — verify "
        "intended; the reserve/credits rails own the actual<=reserved gate"
    )


# --------------------------------------------------------------------------------------
# 3. Idempotency — replaying a finalizer is a no-op (no double-charge / double-refund).
# --------------------------------------------------------------------------------------

def test_billing_settle_and_release_guard_on_prior_finalization_in_source():
    """A replayed settle/release on a finalized reservation is a first-finalizer no-op.

    Migration 0088 owns the current ``safebox_billing_settle`` and
    ``safebox_billing_release_reservation`` SECURITY DEFINER bodies.
    The live-DB no-op proof is the billing pg-suite."""
    sql = _billing_release_sql()
    for fn_name in ("safebox_billing_settle", "safebox_billing_release_reservation"):
        body = _sql_function_body(sql, fn_name)
        assert "v_finalized" in body, f"{fn_name} lost its finalized replay guard"
        assert "if v_finalized then" in body, f"{fn_name} no longer branches on prior finalization"
        # The finalized branch returns the row (a no-op), it does not re-write the ledger.
        assert "return r;" in body, f"{fn_name} no longer no-ops on a replayed finalizer"


def test_app_usage_finalizers_guard_on_finalized_status_in_source():
    """The settle / release gates must no-op once the event is finalized (no double-charge /
    double-refund on a replayed finalizer). Post-0037 the row ops are the
    ``safebox_settle_usage`` / ``safebox_release_usage`` SECURITY DEFINER functions, so we assert
    the finalized-status guard THERE: each checks ``status in ('completed', 'failed', 'released')``
    before writing and returns the existing row instead."""
    # The Python finalized-status set still mirrors the SQL guard (it is the typed exception/no-op
    # contract app_usage exposes), so pin the two together.
    assert app_usage._FINALIZED_STATUSES == ("completed", "failed", "released")
    sql = _ledger_boundary_sql()
    for fn_name in ("safebox_settle_usage", "safebox_release_usage"):
        body = _sql_function_body(sql, fn_name)
        assert "status in ('completed', 'failed', 'released')" in body, (
            f"{fn_name} lost its finalized-status replay guard"
        )
        # The finalized branch returns the existing row (a no-op), it does not re-write the ledger.
        assert "r.is_noop := true" in body, (
            f"{fn_name} no longer no-ops on a replayed finalizer"
        )


def test_business_credits_commit_and_release_guard_on_prior_finalization_in_source():
    """commit_credits / release_credits must no-op on a reservation already committed/released.
    Post-0038 the row ops are the ``safebox_credits_commit`` / ``safebox_credits_release`` SECURITY
    DEFINER functions, so the replay guard lives THERE: each queries a prior commit/release entry and
    returns the current balances without writing. The live-DB no-op proof is the credits pg-suite."""
    sql = _runtime_least_priv_sql()
    for fn_name in ("safebox_credits_commit", "safebox_credits_release"):
        body = _sql_function_body(sql, fn_name)
        assert "kind in ('commit', 'release')" in body, (
            f"{fn_name} lost its prior-finalization replay query"
        )
        assert "return r;" in body, (
            f"{fn_name} no longer no-ops on a replayed finalizer"
        )


def test_reserve_paths_are_idempotent_on_their_key_in_source():
    """Each reserve must return the SAME existing reservation on a replayed key (hold once, never
    twice). Post-0038 the billing + creative-credit reserve row ops are SECURITY DEFINER functions; the
    product-usage rail is the 0037 function. Each is asserted where its idempotent lookup now lives."""
    # billing + creative-credit reserve (0038): idempotent-on-reservation_key lookup short-circuits to
    # the existing hold before any new write.
    rl_sql = _runtime_least_priv_sql()
    for fn_name in ("safebox_billing_reserve", "safebox_credits_reserve"):
        body = _sql_function_body(rl_sql, fn_name)
        assert "reservation_key = p_reservation_key and kind = 'reserve'" in body, (
            f"{fn_name} lost its idempotent reservation_key lookup"
        )
    # app_usage.reserve_usage: post-0037 the row op is the ``safebox_reserve_usage`` SECURITY DEFINER
    # function — its idempotent-on-key lookup short-circuits to the existing row before any new hold.
    reserve = _sql_function_body(_ledger_boundary_sql(), "safebox_reserve_usage")
    assert "where e.business_slug = p_business_slug and e.reservation_key = p_reservation_key" in reserve, (
        "safebox_reserve_usage lost its idempotent reservation_key lookup"
    )


# --------------------------------------------------------------------------------------
# 4. reconcile proves cached == ledger (and flags oversold / negative reservations).
# --------------------------------------------------------------------------------------

def test_reconcile_billing_recomputes_from_ledger_and_flags_drift_in_source():
    """reconcile_billing must (a) recompute allowance_used from the append-only entries,
    (b) flag cached != ledger drift, (c) flag negative outstanding reservations, and
    (d) flag oversold allowance. Confirmed in source. (The rail is allowance-only since the
    topup bucket was removed 2026-06-18.)"""
    src = _func_source(billing.reconcile_billing)
    # Recompute from the ledger, not from the cached account columns.
    assert "from billing_entries" in src
    assert "calc_used" in src
    # Cached-vs-ledger drift comparisons.
    assert "calc_used != used" in src
    # Negative outstanding reservation and oversold allowance are surfaced.
    assert "reserved_allowance < 0" in src
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
    assert after_replays.allowance_remaining_cents == after_first.allowance_remaining_cents
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


# --------------------------------------------------------------------------------------
# GOAL_RULES §1/§3 cutover step 5 — the usage ledger is reachable ONLY via the
# safebox_*_usage SECURITY DEFINER gate functions, never by direct DML, under the
# NON-bypassing `takyon_app` runtime role (migration 0037's REVOKE actually binds).
# --------------------------------------------------------------------------------------

@contextlib.contextmanager
def _takyon_app_conn(pg_conn):
    """A second connection to the SAME throwaway DB, scoped to the restricted `takyon_app` role
    (NOBYPASSRLS, no direct app_usage_events DML after migration 0037). Mirrors the runtime's
    ledger-write scope so the boundary can be proven the way production enforces it."""
    import psycopg
    from psycopg.conninfo import make_conninfo

    dsn = make_conninfo(os.environ["TAKYON_TEST_PG_DSN"], dbname=pg_conn.info.dbname)
    conn = psycopg.connect(dsn, autocommit=True)
    try:
        conn.execute("set role takyon_app")
        conn.execute("select set_config('takyon.rls_bypass', '0', false)")
        yield conn
    finally:
        conn.close()


@_PG
def test_pg_usage_ledger_direct_dml_is_denied_under_takyon_app(pg_conn):
    """Direct INSERT/UPDATE/DELETE on app_usage_events is DENIED for the runtime `takyon_app` role —
    the gate functions are the ONLY sanctioned writer (migration 0037 boundary)."""
    import psycopg.errors

    slug = _business(pg_conn)  # privileged setup on the superuser conn
    with _takyon_app_conn(pg_conn) as app_conn:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            app_conn.execute(
                "insert into app_usage_events "
                "(business_slug, reservation_key, status, estimated_cost_microusd) "
                "values (%s, %s, 'reserved', 1)",
                (slug, "forged-rk"),
            )


@_PG
def test_pg_usage_gate_functions_work_under_takyon_app(pg_conn):
    """The reserve/settle SECURITY DEFINER gate functions DO run under `takyon_app` (granted EXECUTE)
    and write the ledger with the owner's privilege — the intended path is open while direct DML is
    closed."""
    slug = _business(pg_conn)  # privileged setup on the superuser conn
    rk = "gate-rk-1"
    with _takyon_app_conn(pg_conn) as app_conn:
        reserved = app_conn.execute(
            "select status, estimated_cost_microusd from safebox_reserve_usage("
            "%s, %s, %s, null, null, null, 'product_usage', 'app', null, null, '{}'::jsonb)",
            (slug, 10_000, rk),
        ).fetchone()
        assert reserved[0] == "reserved"
        assert reserved[1] == 10_000
        settled = app_conn.execute(
            "select status, actual_cost_microusd from safebox_settle_usage("
            "%s, %s, %s, null, null, null, null, null, null)",
            (slug, rk, 7_000),
        ).fetchone()
        assert settled[0] == "completed"
        assert settled[1] == 7_000
    # The row is visible on the superuser conn — the gate wrote it.
    n = pg_conn.execute(
        "select count(*) from app_usage_events where business_slug = %s and reservation_key = %s",
        (slug, rk),
    ).fetchone()[0]
    assert n == 1


@_PG
def test_pg_usage_reserve_settle_release_via_app_layer_under_takyon_app(pg_conn):
    """The real app_usage.reserve/settle/release (which internally drop to `takyon_app` for the gate
    call via _ledger_gate_scope) still work end-to-end on the runtime connection — the product
    /generate + /search reserve→settle→release path is intact."""
    slug = _business(pg_conn)
    r1 = app_usage.reserve_usage(pg_conn, slug, estimated_cost_microusd=5_000, reservation_key="ap-rk-1")
    assert r1.status == "reserved"
    s1 = app_usage.settle_usage(pg_conn, slug, "ap-rk-1", actual_cost_microusd=4_200)
    assert s1.status == "completed"
    assert s1.actual_cost_microusd == 4_200

    # release path on a fresh reservation
    app_usage.reserve_usage(pg_conn, slug, estimated_cost_microusd=5_000, reservation_key="ap-rk-2")
    rel = app_usage.release_usage(pg_conn, slug, "ap-rk-2", error="provider_error")
    assert rel.status == "failed"
    assert rel.actual_cost_microusd == 0
