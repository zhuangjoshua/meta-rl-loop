"""Postgres integration tests for the execution-policy engine — Phase 4.

Phase 4 acceptance: a unit of the CEO's own work degrades gracefully under budget
pressure instead of hard-failing — it runs inline, downgrades to a cheaper tier,
routes to a background job, or blocks with a precise reason. The decision is advisory;
billing.reserve stays the atomic money gate, so a decision must move no money.

The sharp correctness detail is per-business spend: billing's settle/refund entries
carry business_slug=NULL (only the reserve is tagged), so the monthly sub-cap must net
spend via the reservation_key set, not a direct business_slug filter. The cap /
refund / settle tests below pin that down on real Postgres.

Real engine on real Postgres (never mocks). Skips unless psycopg is importable and
TAKYON_TEST_PG_DSN is set; the env-knob unit test runs whenever psycopg is importable.
"""

from __future__ import annotations

import uuid

import pytest

psycopg = pytest.importorskip("psycopg")

from plugins.takyon import billing, policy  # noqa: E402
from plugins.takyon.control_plane import provision_user_on_first_login  # noqa: E402
from plugins.takyon.policy import NoBusiness  # noqa: E402


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


def _policy_rows(conn, slug) -> int:
    return conn.execute(
        "select count(*) from app_execution_policies where business_slug = %s", (slug,)
    ).fetchone()[0]


# ── env knob (no DB) ──────────────────────────────────────────────────────────


def test_expensive_threshold_defaults_and_clamps(monkeypatch):
    monkeypatch.delenv("TAKYON_EXECUTION_EXPENSIVE_THRESHOLD_CENTS", raising=False)
    assert policy.expensive_threshold_cents() == 100
    monkeypatch.setenv("TAKYON_EXECUTION_EXPENSIVE_THRESHOLD_CENTS", "250")
    assert policy.expensive_threshold_cents() == 250
    monkeypatch.setenv("TAKYON_EXECUTION_EXPENSIVE_THRESHOLD_CENTS", "-5")
    assert policy.expensive_threshold_cents() == 0  # clamped, can't invert the gate
    monkeypatch.setenv("TAKYON_EXECUTION_EXPENSIVE_THRESHOLD_CENTS", "garbage")
    assert policy.expensive_threshold_cents() == 100  # bad value → conservative default


# ── policy storage ────────────────────────────────────────────────────────────


def test_get_policy_returns_defaults_without_inserting(pg_conn):
    uid = _user(pg_conn)
    slug = _business(pg_conn, uid)
    pol = policy.get_execution_policy(pg_conn, slug)
    assert pol.preferred_model_tier == "standard"
    assert pol.max_runtime_seconds == 300
    assert pol.monthly_app_budget_cents is None
    assert pol.allow_worker_escalation is True
    # pure read: a missing policy is NOT auto-inserted
    assert _policy_rows(pg_conn, slug) == 0


def test_upsert_creates_then_preserves_unspecified_fields(pg_conn):
    uid = _user(pg_conn)
    slug = _business(pg_conn, uid)
    policy.upsert_execution_policy(pg_conn, slug, preferred_model_tier="premium")
    policy.upsert_execution_policy(pg_conn, slug, monthly_app_budget_cents=5000)
    pol = policy.get_execution_policy(pg_conn, slug)
    assert pol.preferred_model_tier == "premium"  # set in the first upsert, preserved
    assert pol.monthly_app_budget_cents == 5000  # set in the second
    assert pol.max_runtime_seconds == 300  # never touched → still the default
    assert _policy_rows(pg_conn, slug) == 1  # upsert, not duplicate insert


def test_upsert_rejects_unknown_field(pg_conn):
    uid = _user(pg_conn)
    slug = _business(pg_conn, uid)
    with pytest.raises(ValueError):
        policy.upsert_execution_policy(pg_conn, slug, not_a_real_knob=1)


def test_upsert_rejects_bad_values(pg_conn):
    uid = _user(pg_conn)
    slug = _business(pg_conn, uid)
    with pytest.raises(ValueError):
        policy.upsert_execution_policy(pg_conn, slug, max_runtime_seconds=0)
    with pytest.raises(ValueError):
        policy.upsert_execution_policy(pg_conn, slug, preferred_model_tier="")
    with pytest.raises(ValueError):
        policy.upsert_execution_policy(pg_conn, slug, monthly_app_budget_cents=-1)


def test_upsert_unknown_business_fails_loud(pg_conn):
    # FK to businesses(slug) — an unknown business can't get a policy silently.
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        policy.upsert_execution_policy(pg_conn, "ghost-biz", preferred_model_tier="x")


# ── decide_execution: the four outcomes ────────────────────────────────────────


def test_inline_when_affordable(pg_conn):
    uid = _user(pg_conn)
    slug = _business(pg_conn, uid)
    billing.grant_allowance(pg_conn, uid, 1000, "tu")
    d = policy.decide_execution(pg_conn, business_slug=slug, estimate_cents=100)
    assert d.outcome == "inline"
    assert d.reason == "ok"
    assert d.model_tier == "standard"  # the policy's preferred tier
    assert d.estimate_cents == 100
    assert d.detail["downgraded"] is False


def test_blocked_insufficient_balance(pg_conn, monkeypatch):
    # Provisioning grants a starter allowance ("first company on the house"); suppress it
    # so the account is genuinely empty and the estimate can't be covered.
    monkeypatch.setenv("TAKYON_STARTER_ALLOWANCE_CENTS", "0")
    uid = _user(pg_conn)  # provisioned at zero allowance
    slug = _business(pg_conn, uid)
    d = policy.decide_execution(pg_conn, business_slug=slug, estimate_cents=100)
    assert d.outcome == "blocked"
    assert d.reason == "insufficient_balance"
    assert d.model_tier is None


def test_cheaper_downgrades_to_closest_affordable_tier(pg_conn):
    uid = _user(pg_conn)
    slug = _business(pg_conn, uid)
    billing.grant_allowance(pg_conn, uid, 120, "tu")  # premium unaffordable; standard/mini fit
    d = policy.decide_execution(
        pg_conn,
        business_slug=slug,
        estimate_cents=200,
        requested_tier="premium",
        tier_estimates={"premium": 200, "standard": 100, "mini": 50},
    )
    assert d.outcome == "cheaper"
    assert d.reason == "downgraded_to_affordable_tier"
    assert d.model_tier == "standard"  # most expensive affordable = closest quality
    assert d.estimate_cents == 100
    assert d.detail["downgraded"] is True


def test_job_when_runtime_exceeds_inline_and_escalation_allowed(pg_conn):
    uid = _user(pg_conn)
    slug = _business(pg_conn, uid)
    billing.grant_allowance(pg_conn, uid, 1000, "tu")
    d = policy.decide_execution(
        pg_conn,
        business_slug=slug,
        estimate_cents=100,
        estimated_runtime_seconds=600,  # > default max_runtime_seconds (300)
    )
    assert d.outcome == "job"
    assert d.reason == "exceeds_inline_limits"
    assert d.model_tier == "standard"  # tier still chosen; it just runs as a job
    assert d.estimate_cents == 100


def test_blocked_when_exceeds_inline_and_escalation_disabled(pg_conn):
    uid = _user(pg_conn)
    slug = _business(pg_conn, uid)
    billing.grant_allowance(pg_conn, uid, 1000, "tu")
    policy.upsert_execution_policy(pg_conn, slug, allow_worker_escalation=False)
    d = policy.decide_execution(
        pg_conn,
        business_slug=slug,
        estimate_cents=100,
        estimated_output_bytes=6_000_000,  # > default max_output_bytes (5_000_000)
    )
    assert d.outcome == "blocked"
    assert d.reason == "exceeds_inline_limits_and_escalation_disabled"
    assert d.model_tier is None


def test_blocked_expensive_branch_disallowed(pg_conn):
    uid = _user(pg_conn)
    slug = _business(pg_conn, uid)
    billing.grant_allowance(pg_conn, uid, 100000, "tu")  # plenty of money…
    policy.upsert_execution_policy(pg_conn, slug, allow_expensive_branches=False)
    # 200 > default threshold (100) → expensive; affordable but disallowed.
    d = policy.decide_execution(pg_conn, business_slug=slug, estimate_cents=200)
    assert d.outcome == "blocked"
    assert d.reason == "expensive_branch_disallowed"


def test_zero_estimate_runs_inline_even_with_no_balance(pg_conn):
    uid = _user(pg_conn)  # zero balance
    slug = _business(pg_conn, uid)
    d = policy.decide_execution(pg_conn, business_slug=slug, estimate_cents=0)
    assert d.outcome == "inline"  # free actions are never blocked on budget
    assert d.model_tier == "standard"
    assert d.estimate_cents == 0


# ── per-business monthly sub-cap (the reservation_key netting detail) ───────────


def test_business_cap_blocks_before_flow_a_runs_out(pg_conn):
    uid = _user(pg_conn)
    slug = _business(pg_conn, uid)
    billing.grant_allowance(pg_conn, uid, 100000, "tu")  # flow-A is huge
    policy.upsert_execution_policy(pg_conn, slug, monthly_app_budget_cents=500)
    # Consume the whole sub-cap with a business-tagged reservation (still outstanding).
    billing.reserve(pg_conn, uid, 500, "r-cap", business_slug=slug)
    d = policy.decide_execution(pg_conn, business_slug=slug, estimate_cents=100)
    assert d.outcome == "blocked"
    assert d.reason == "business_cap_exhausted"  # NOT insufficient_balance — flow-A is fine
    assert d.detail["business_period_spend_cents"] == 500
    assert d.detail["business_cap_headroom_cents"] == 0


def test_refund_restores_cap_headroom(pg_conn):
    uid = _user(pg_conn)
    slug = _business(pg_conn, uid)
    billing.grant_allowance(pg_conn, uid, 100000, "tu")
    policy.upsert_execution_policy(pg_conn, slug, monthly_app_budget_cents=500)
    billing.reserve(pg_conn, uid, 500, "r-ref", business_slug=slug)
    billing.refund(pg_conn, "r-ref")  # released — must NOT keep counting against the cap
    d = policy.decide_execution(pg_conn, business_slug=slug, estimate_cents=100)
    assert d.detail["business_period_spend_cents"] == 0  # netted via reservation_key
    assert d.outcome == "inline"


def test_settled_actual_counts_toward_cap_not_the_reservation(pg_conn):
    uid = _user(pg_conn)
    slug = _business(pg_conn, uid)
    billing.grant_allowance(pg_conn, uid, 100000, "tu")
    policy.upsert_execution_policy(pg_conn, slug, monthly_app_budget_cents=100000)
    billing.reserve(pg_conn, uid, 800, "r-settle", business_slug=slug)
    billing.settle(pg_conn, "r-settle", 300)  # spent 300, released 500 (business_slug NULL)
    d = policy.decide_execution(pg_conn, business_slug=slug, estimate_cents=1)
    # netting = Σreserve(800) − Σrefund(500 release) = 300 actual spend, not 800 or 0
    assert d.detail["business_period_spend_cents"] == 300


# ── preconditions & advisory invariant ─────────────────────────────────────────


def test_unknown_business_raises(pg_conn):
    with pytest.raises(NoBusiness):
        policy.decide_execution(
            pg_conn, business_slug=f"missing-{uuid.uuid4().hex[:8]}", estimate_cents=10
        )


def test_negative_estimate_raises(pg_conn):
    uid = _user(pg_conn)
    slug = _business(pg_conn, uid)
    with pytest.raises(ValueError):
        policy.decide_execution(pg_conn, business_slug=slug, estimate_cents=-1)


def test_decision_moves_no_money_and_inserts_no_policy(pg_conn):
    uid = _user(pg_conn)
    slug = _business(pg_conn, uid)
    billing.grant_allowance(pg_conn, uid, 1000, "tu")
    before = billing.get_billing_balances(pg_conn, uid)
    policy.decide_execution(pg_conn, business_slug=slug, estimate_cents=400)
    after = billing.get_billing_balances(pg_conn, uid)
    # advisory: the decision reserves nothing and leaves balances untouched
    assert (after.allowance_used_cents, after.reserved_cents) == (
        before.allowance_used_cents,
        before.reserved_cents,
    )
    assert _policy_rows(pg_conn, slug) == 0  # decide() never writes a policy row
