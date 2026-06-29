"""Grouped E2E (real Postgres) for the cron-wake group's plan-gated wake-schedule write boundary.

This is the rule-#2 tie-in for the **Cron scheduling / adjustment** card: it exercises the SAME
boundary the CLI ``/wake`` command, the CEO ``cron.ensure_ceo_wakeup`` operation, and ``/create``
all flow through — ``TakyonStore._ensure_ceo_cron`` — against a migrated throwaway Postgres DB, and
proves the cadence is gated by the operator's subscription plan with no bypass.

Acceptance coverage (spec.md "Cron scheduling / adjustment"):
  * #1 — a sub-floor cadence is REJECTED and no wake_schedules row is written.
  * #2 — an in-floor cadence SUCCEEDS and writes interval_seconds.
  * #4/#5 — a no/low subscription operator (downgrade) is held to the most-restrictive floor; the
    same in-floor request that succeeds for an active plan is rejected.
  * #6 — the pure leaf ``wakes.upsert_wake_schedule`` still has NO plan gate (it accepts any
    positive interval); the gate lives ONLY at the _ensure_ceo_cron boundary.

Skips unless psycopg is importable and TAKYON_TEST_PG_DSN is set (via the pg_store_dsn fixture).
"""

from __future__ import annotations

import uuid

import pytest

psycopg = pytest.importorskip("psycopg")

from plugins.takyon import core as takyon_core  # noqa: E402
from plugins.takyon import wakes  # noqa: E402
from plugins.takyon.core import TakyonError  # noqa: E402


def _seed_business(dsn: str, *, subscription_status: str) -> str:
    """Insert a users row (with a cached operator subscription status) + an owned business; return
    the slug. The status is what ``operator_plan_name_for_business`` reads to resolve the plan."""
    slug = f"biz-{uuid.uuid4().hex[:8]}"
    with psycopg.connect(dsn, autocommit=True) as conn:
        uid = conn.execute(
            "insert into users (auth0_sub, operator_billing_subscription_status) "
            "values (%s, %s) returning id",
            (f"auth0|{slug}", subscription_status),
        ).fetchone()[0]
        conn.execute(
            "insert into businesses (slug, name, owner_user_id, mode) values (%s, %s, %s, %s)",
            (slug, slug.title(), uid, "test"),
        )
    return slug


def _wake_row(dsn: str, slug: str):
    with psycopg.connect(dsn, autocommit=True) as conn:
        return wakes.get_wake_schedule(conn, slug)


@pytest.fixture
def store(pg_store_dsn, tmp_path):
    return takyon_core.TakyonStore(root=tmp_path, database_url=pg_store_dsn)


def test_in_floor_cadence_is_written(store, pg_store_dsn, monkeypatch):
    # Active subscription -> default plan "PRO" (floor 3h). A 6h cadence is at/above the floor.
    # New recurring wakes default paused; they must be explicitly enabled later.
    monkeypatch.setenv("TAKYON_OPERATOR_DEFAULT_PLAN_NAME", "PRO")
    slug = _seed_business(pg_store_dsn, subscription_status="active")

    result = store._ensure_ceo_cron(slug, schedule="every 6h", reason="test")

    assert result["interval_seconds"] == 6 * 3600
    assert result["enabled"] is False
    row = _wake_row(pg_store_dsn, slug)
    assert row is not None
    assert row.interval_seconds == 6 * 3600
    assert row.enabled is False


def test_in_floor_cadence_can_be_explicitly_enabled(store, pg_store_dsn, monkeypatch):
    monkeypatch.setenv("TAKYON_OPERATOR_DEFAULT_PLAN_NAME", "PRO")
    slug = _seed_business(pg_store_dsn, subscription_status="active")

    result = store._ensure_ceo_cron(slug, schedule="every 6h", reason="test", enabled=True)

    assert result["interval_seconds"] == 6 * 3600
    assert result["enabled"] is True
    row = _wake_row(pg_store_dsn, slug)
    assert row is not None
    assert row.interval_seconds == 6 * 3600
    assert row.enabled is True


def test_sub_floor_cadence_is_rejected_and_not_written(store, pg_store_dsn, monkeypatch):
    # Active "PRO" plan floor is 3h; "every 1h" is below it -> rejected, nothing written.
    monkeypatch.setenv("TAKYON_OPERATOR_DEFAULT_PLAN_NAME", "PRO")
    slug = _seed_business(pg_store_dsn, subscription_status="active")

    with pytest.raises(TakyonError, match="too fast for plan"):
        store._ensure_ceo_cron(slug, schedule="every 1h", reason="test")

    assert _wake_row(pg_store_dsn, slug) is None


def test_no_subscription_operator_held_to_restrictive_floor(store, pg_store_dsn, monkeypatch):
    # No active subscription -> plan resolves to None -> most-restrictive 6h floor. A 1h cadence
    # that an active plan would (or wouldn't) allow is rejected here; a 6h cadence succeeds.
    monkeypatch.setenv("TAKYON_OPERATOR_DEFAULT_PLAN_NAME", "PRO")
    slug = _seed_business(pg_store_dsn, subscription_status="none")

    with pytest.raises(TakyonError, match="too fast for plan"):
        store._ensure_ceo_cron(slug, schedule="every 1h", reason="test")
    assert _wake_row(pg_store_dsn, slug) is None

    result = store._ensure_ceo_cron(slug, schedule="every 6h", reason="test")
    assert result["interval_seconds"] == 6 * 3600
    assert _wake_row(pg_store_dsn, slug) is not None


def test_downgrade_rejects_a_previously_in_floor_cadence(store, pg_store_dsn, monkeypatch):
    # On an active DEV plan (60s floor) a tight 1h cadence is allowed and written. After a downgrade
    # (subscription canceled -> status not allowance-bearing -> plan None -> 6h floor), the same 1h
    # set attempt is rejected and the existing row is NOT overwritten to the sub-floor cadence.
    monkeypatch.setenv("TAKYON_OPERATOR_DEFAULT_PLAN_NAME", "DEV")
    slug = _seed_business(pg_store_dsn, subscription_status="active")

    store._ensure_ceo_cron(slug, schedule="every 1h", reason="test")
    assert _wake_row(pg_store_dsn, slug).interval_seconds == 3600

    with psycopg.connect(pg_store_dsn, autocommit=True) as conn:
        conn.execute(
            "update users set operator_billing_subscription_status = 'canceled' "
            "where id = (select owner_user_id from businesses where slug = %s)",
            (slug,),
        )

    with pytest.raises(TakyonError, match="too fast for plan"):
        store._ensure_ceo_cron(slug, schedule="every 1h", reason="test")
    # existing schedule untouched (still the pre-downgrade cadence; not corrupted, not deleted)
    assert _wake_row(pg_store_dsn, slug).interval_seconds == 3600


def test_pure_leaf_upsert_has_no_plan_gate(pg_store_dsn):
    # Acceptance #6: the pure leaf accepts ANY positive interval — the plan gate is exclusively at
    # the _ensure_ceo_cron boundary, never in wakes.upsert_wake_schedule.
    slug = _seed_business(pg_store_dsn, subscription_status="none")
    with psycopg.connect(pg_store_dsn, autocommit=True) as conn:
        sched = wakes.upsert_wake_schedule(conn, slug, interval_seconds=60)
    assert sched.interval_seconds == 60
