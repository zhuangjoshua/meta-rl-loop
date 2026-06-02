"""Postgres integration tests for the app funding rail.

This pins the per-request payer split the AI gateway relies on:
  * plan-funded user credits are consumed first inside the current period,
  * business subsidy can cover the remainder,
  * subsidy is capped per sub-user per period,
  * release/settle are idempotent and restore the subsidy pool correctly.
"""

from __future__ import annotations

import uuid

import pytest

psycopg = pytest.importorskip("psycopg")

from plugins.takyon import app_funding, app_identity, app_usage  # noqa: E402
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


def _user(conn, business_slug: str, email: str = "cust@example.com"):
    return app_identity.upsert_app_user(conn, business_slug, email)


def test_grant_and_settle_credit_then_subsidy(pg_conn):
    slug = _business(pg_conn, _owner(pg_conn))
    user = _user(pg_conn, slug)
    period_start = app_usage.ensure_app_budget(pg_conn, slug).current_period_start

    balances = app_funding.grant_business_subsidy(
        pg_conn,
        slug,
        500,
        "subsidy-grant-1",
    )
    assert balances.balance_microusd == 500

    reservation = app_funding.reserve_funding(
        pg_conn,
        slug,
        app_user_id=user.id,
        reservation_key="funding-r1",
        estimated_cost_microusd=900,
        user_credit_limit_microusd=600,
        subsidy_cap_microusd=500,
        period_start=period_start,
        plan_key="paid",
    )
    assert reservation.user_credit_microusd == 600
    assert reservation.subsidy_microusd == 300
    assert app_funding.get_business_subsidy_balances(pg_conn, slug).balance_microusd == 200

    summary = app_funding.get_user_period_funding_summary(
        pg_conn, slug, user.id, period_start=period_start
    )
    assert summary.user_credit_reserved_microusd == 600
    assert summary.subsidy_reserved_microusd == 300

    outcome = app_funding.settle_funding(
        pg_conn,
        "funding-r1",
        actual_cost_microusd=700,
    )
    assert outcome.settled_user_credit_microusd == 600
    assert outcome.settled_subsidy_microusd == 100
    assert outcome.released_subsidy_microusd == 200
    assert app_funding.get_business_subsidy_balances(pg_conn, slug).balance_microusd == 400

    summary = app_funding.get_user_period_funding_summary(
        pg_conn, slug, user.id, period_start=period_start
    )
    assert summary.user_credit_settled_microusd == 600
    assert summary.user_credit_reserved_microusd == 0
    assert summary.subsidy_settled_microusd == 100
    assert summary.subsidy_reserved_microusd == 0


def test_release_restores_subsidy_pool(pg_conn):
    slug = _business(pg_conn, _owner(pg_conn))
    user = _user(pg_conn, slug)
    period_start = app_usage.ensure_app_budget(pg_conn, slug).current_period_start

    app_funding.grant_business_subsidy(pg_conn, slug, 250, "subsidy-grant-2")
    app_funding.reserve_funding(
        pg_conn,
        slug,
        app_user_id=user.id,
        reservation_key="funding-r2",
        estimated_cost_microusd=150,
        user_credit_limit_microusd=0,
        subsidy_cap_microusd=250,
        period_start=period_start,
        plan_key="free",
    )
    assert app_funding.get_business_subsidy_balances(pg_conn, slug).balance_microusd == 100

    outcome = app_funding.release_funding(pg_conn, "funding-r2")
    assert outcome.released_subsidy_microusd == 150
    assert app_funding.get_business_subsidy_balances(pg_conn, slug).balance_microusd == 250

    summary = app_funding.get_user_period_funding_summary(
        pg_conn, slug, user.id, period_start=period_start
    )
    assert summary.user_credit_reserved_microusd == 0
    assert summary.subsidy_reserved_microusd == 0


def test_subsidy_cap_is_per_user_per_period(pg_conn):
    slug = _business(pg_conn, _owner(pg_conn))
    user = _user(pg_conn, slug)
    period_start = app_usage.ensure_app_budget(pg_conn, slug).current_period_start

    app_funding.grant_business_subsidy(pg_conn, slug, 1_000, "subsidy-grant-3")
    app_funding.reserve_funding(
        pg_conn,
        slug,
        app_user_id=user.id,
        reservation_key="funding-r3",
        estimated_cost_microusd=300,
        user_credit_limit_microusd=0,
        subsidy_cap_microusd=300,
        period_start=period_start,
        plan_key="free",
    )
    app_funding.settle_funding(pg_conn, "funding-r3", actual_cost_microusd=300)

    with pytest.raises(app_funding.InsufficientAppFunding) as excinfo:
        app_funding.reserve_funding(
            pg_conn,
            slug,
            app_user_id=user.id,
            reservation_key="funding-r4",
            estimated_cost_microusd=50,
            user_credit_limit_microusd=0,
            subsidy_cap_microusd=300,
            period_start=period_start,
            plan_key="free",
        )
    err = excinfo.value
    assert err.user_subsidy_remaining_microusd == 0
    assert err.business_subsidy_remaining_microusd == 700


def test_reserve_is_idempotent_on_same_key(pg_conn):
    slug = _business(pg_conn, _owner(pg_conn))
    user = _user(pg_conn, slug)
    period_start = app_usage.ensure_app_budget(pg_conn, slug).current_period_start

    app_funding.grant_business_subsidy(pg_conn, slug, 400, "subsidy-grant-4")
    first = app_funding.reserve_funding(
        pg_conn,
        slug,
        app_user_id=user.id,
        reservation_key="funding-r5",
        estimated_cost_microusd=350,
        user_credit_limit_microusd=200,
        subsidy_cap_microusd=200,
        period_start=period_start,
        plan_key="paid",
    )
    second = app_funding.reserve_funding(
        pg_conn,
        slug,
        app_user_id=user.id,
        reservation_key="funding-r5",
        estimated_cost_microusd=999,
        user_credit_limit_microusd=0,
        subsidy_cap_microusd=0,
        period_start=period_start,
        plan_key="paid",
    )
    assert second == first
    assert app_funding.get_business_subsidy_balances(pg_conn, slug).balance_microusd == 250
