from __future__ import annotations

import uuid

import pytest

psycopg = pytest.importorskip("psycopg")

from plugins.takyon import core  # noqa: E402


def test_live_reporting_excludes_sandbox_rows_and_nets_reversals(
    pg_store_dsn, tmp_path, monkeypatch
):
    slug = f"revenue-{uuid.uuid4().hex[:10]}"
    with psycopg.connect(pg_store_dsn, autocommit=True) as conn:
        owner_id = conn.execute(
            "insert into users (auth0_sub) values (%s) returning id",
            (f"auth0|{slug}",),
        ).fetchone()[0]
        conn.execute(
            "insert into businesses (slug, name, owner_user_id, mode) values (%s, %s, %s, 'live')",
            (slug, slug, owner_id),
        )
        conn.execute(
            "insert into app_revenue_events "
            "(business_slug, amount_paid_cents, revenue_type, customer_email, metadata) values "
            "(%s, 9000, 'checkout', 'buyer@example.test', '{}'::jsonb), "
            "(%s, 8000, 'checkout', 'buyer@example.test', '{\"stripe_environment\":\"test\"}'::jsonb), "
            "(%s, 3000, 'checkout', 'buyer@example.test', '{\"stripe_environment\":\"live\"}'::jsonb), "
            "(%s, 1000, 'reversal', 'buyer@example.test', '{\"stripe_environment\":\"live\"}'::jsonb)",
            (slug, slug, slug, slug),
        )

    monkeypatch.setenv("TAKYON_STRIPE_MODE", "live")
    monkeypatch.setenv("TAKYON_DISPLAY_METRICS", "0")
    store = core.TakyonStore(root=tmp_path, database_url=pg_store_dsn)

    app = store.read(scope=f"business:{slug}", query="app")
    assert app["app"]["revenue"] == {"events": 2, "amount_paid_cents": 2000}

    pulse = store.calculate_pulse(slug)
    assert pulse["summary"]["revenue_cents"] == 2000
    assert pulse["windows"]["lifetime"]["metrics"]["revenue"]["events"] == 2

    traction = store.traction_timeseries(slug, range_key="D")
    assert traction["totals"]["revenue_cents"] == 2000

    with store._connect() as conn:
        snapshot = core._episode_metrics_snapshot(store, conn, slug, None)
    assert snapshot["revenue_cents"] == 2000
