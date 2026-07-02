"""Monthly-only plan enforcement + fail-loud budget cap (modularization plan §2.7 / Stage-5 slice).

Subuser plans are MONTHLY-ONLY (operator decision 2026-07-02): new writes refuse any
non-month interval, the included-AI-budget cap REFUSES with figures instead of silently
clamping, and the one legal non-month operation is an idempotent re-pass of a frozen legacy
row's identical terms (keeps metadata/Stripe-linkage edits working; the 2026-07-02 prod
check found zero active non-month subscribers).

Rig posture: direct row inserts + ``upsert_plan_policy`` on the plain PG rig — no safebox
(the broader entitlements PG suite needs the operator safebox token, which the hermetic
runner scrubs; these tests deliberately avoid that path so the gate is CI/rig-provable).
"""

from __future__ import annotations

import uuid

import pytest

psycopg = pytest.importorskip("psycopg")

from plugins.takyon import app_entitlements as ents  # noqa: E402


def _mk_business(conn) -> str:
    uid = str(uuid.uuid4())
    conn.execute(
        "insert into users (id, auth0_sub) values (%s, %s)", (uid, f"auth0|{uuid.uuid4().hex}")
    )
    slug = f"mo-{uuid.uuid4().hex[:8]}"
    conn.execute(
        "insert into businesses (slug, name, goal, status, mode, owner_user_id) "
        "values (%s, %s, 'g', 'active', 'test', %s)",
        (slug, slug, uid),
    )
    return slug


def test_new_month_plan_upserts_normally(pg_conn):
    slug = _mk_business(pg_conn)
    plan = ents.upsert_plan_policy(
        pg_conn, slug, "starter", tier="paid", price_cents=1900,
        billing_interval="monthly",  # spelling normalizes
        included_ai_budget_microusd=5_000_000,
    )
    assert plan.billing_interval == "month"
    assert plan.included_ai_budget_microusd == 5_000_000


@pytest.mark.parametrize("interval", ["year", "one_time", "once", "annual", "week"])
def test_new_non_month_plan_refused(pg_conn, interval):
    slug = _mk_business(pg_conn)
    with pytest.raises(ents.InvalidPlan, match="monthly-only"):
        ents.upsert_plan_policy(
            pg_conn, slug, "credits-pack", tier="paid", price_cents=900,
            billing_interval=interval,
        )


def test_budget_above_price_cap_refuses_with_figures_not_clamped(pg_conn):
    slug = _mk_business(pg_conn)
    with pytest.raises(ents.InvalidPlan) as exc:
        ents.upsert_plan_policy(
            pg_conn, slug, "starter", tier="paid", price_cents=500,
            included_ai_budget_microusd=9_000_000,  # cap = 500*10_000 = 5_000_000
        )
    msg = str(exc.value)
    assert "9000000" in msg and "5000000" in msg and "price_cents=500" in msg
    # and nothing was written (refused, not clamped-and-stored)
    assert ents.get_plan_policy(pg_conn, slug, "starter") is None


def test_budget_at_cap_passes(pg_conn):
    slug = _mk_business(pg_conn)
    plan = ents.upsert_plan_policy(
        pg_conn, slug, "starter", tier="paid", price_cents=500,
        included_ai_budget_microusd=5_000_000,
    )
    assert plan.included_ai_budget_microusd == 5_000_000


def test_frozen_legacy_non_month_row_identical_repass_passes(pg_conn):
    """The grandfather contract: a legacy one_time row (pre-slice) stays serviceable —
    an idempotent re-pass with identical terms must not trip the month gate or the cap."""
    slug = _mk_business(pg_conn)
    # Simulate the legacy row exactly as prod holds it (written by pre-slice code).
    pg_conn.execute(
        "insert into app_plan_policies (business_slug, plan_key, tier, price_cents, currency,"
        " billing_interval, included_ai_budget_microusd, included_action_quota, source, notes, metadata)"
        " values (%s, 'credits-pro', 'paid', 2900, 'usd', 'one_time', 99_000_000, 0, 'takyon', '', '{}'::jsonb)",
        (slug,),
    )
    plan = ents.upsert_plan_policy(
        pg_conn, slug, "credits-pro", tier="paid", price_cents=2900,
        billing_interval="one_time", included_ai_budget_microusd=99_000_000,
        notes="linkage edit still works",
    )
    assert plan.billing_interval == "one_time"
    assert plan.notes == "linkage edit still works"


def test_frozen_legacy_non_month_row_changed_terms_refused(pg_conn):
    slug = _mk_business(pg_conn)
    pg_conn.execute(
        "insert into app_plan_policies (business_slug, plan_key, tier, price_cents, currency,"
        " billing_interval, included_ai_budget_microusd, included_action_quota, source, notes, metadata)"
        " values (%s, 'credits-pro', 'paid', 2900, 'usd', 'one_time', 0, 0, 'takyon', '', '{}'::jsonb)",
        (slug,),
    )
    with pytest.raises(ents.InvalidPlan, match="monthly-only"):
        ents.upsert_plan_policy(
            pg_conn, slug, "credits-pro", tier="paid", price_cents=3900,  # changed price
            billing_interval="one_time",
        )


def test_openmeter_cadence_is_monthly_only():
    from plugins.takyon import openmeter_backend as om

    assert om.billing_cadence_for("month") == "P1M"
    with pytest.raises(om.OpenMeterConfigurationError):
        om.billing_cadence_for("year")


def test_core_budget_normalizer_refuses_instead_of_clamping():
    from plugins.takyon import core

    with pytest.raises(core.TakyonError) as exc:
        core._normalize_included_ai_budget_microusd(
            9_000_000, price_cents=500, billing_interval="month", tier="paid"
        )
    assert "9000000" in str(exc.value)
    assert (
        core._normalize_included_ai_budget_microusd(
            5_000_000, price_cents=500, billing_interval="month", tier="paid"
        )
        == 5_000_000
    )
