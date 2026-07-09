"""assemble_roas_run_history — the production half of the per-skill ROAS feedback loop.

For every NEW insights-sync receipt on a channel campaign, one truthful run entry (launch-plan
process + sync-receipt results + ROAS) is appended to the per-business metrics/roas/<channel>.md
— the file the channel skill reads before its next launch (takyon-meta-ads-v2 Procedure step 1).
Runs pre-wake in the worker beside the distiller; must be idempotent per sync receipt and
best-effort throughout.

Postgres-backed (pg_store_dsn fixture): the policy registry read is real. Skips without
TAKYON_TEST_PG_DSN, same posture as test_takyon_rl_rails.py.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

psycopg = pytest.importorskip("psycopg")

from plugins.takyon import business_ad_spend  # noqa: E402
from plugins.takyon import core as takyon_core  # noqa: E402


@pytest.fixture
def pg_store(pg_store_dsn, tmp_path):
    return takyon_core.TakyonStore(root=tmp_path, database_url=pg_store_dsn)


def _seed_business(dsn: str, slug: str) -> None:
    with psycopg.connect(dsn, autocommit=True) as conn:
        uid = conn.execute(
            "insert into users (auth0_sub) values (%s) returning id", (f"auth0|{slug}",)
        ).fetchone()[0]
        conn.execute(
            "insert into businesses (slug, name, owner_user_id, mode) values (%s, %s, %s, %s)",
            (slug, slug.title(), uid, "live"),
        )


def _upsert_meta_policy(dsn: str, business: str, campaign: str) -> None:
    with psycopg.connect(dsn, autocommit=True) as conn:
        business_ad_spend.upsert_policy(
            conn, business_slug=business, channel="meta", slug=campaign,
            reservation_key=f"resv-{business}-{campaign}",
            reserved_credits=2000, daily_budget_cents=500, total_budget_cents=2000,
            start_at=datetime.now(timezone.utc) - timedelta(days=1),
            end_at=datetime.now(timezone.utc) + timedelta(days=30),
            status="active", last_synced_spend_cents=1041,
        )


def _write_plan(store, business: str, campaign: str, **overrides) -> None:
    plan = {
        "slug": campaign,
        "asset_kind": "video",
        "headline": "Stop losing leads after the demo",
        "message": "Your CRM forgets. Coscale follows up for you, automatically.",
        "call_to_action_type": "SIGN_UP",
        "daily_budget_usd": 10.0,
        **overrides,
    }
    path = store._resolve_business_file(business, f"distribution/meta-ads/{campaign}/plan.json", sync=False)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(plan), encoding="utf-8")


def _write_sync(store, business: str, campaign: str, name: str, totals: dict) -> None:
    path = store._resolve_business_file(
        business, f"metrics/meta-ads/{campaign}/syncs/{name}", sync=False)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"totals": totals}), encoding="utf-8")


_PIXEL_TOTALS = {
    "impressions": 12304, "clicks": 187, "spend_usd": 10.41,
    "purchase_count": 3, "purchase_value_usd": 57.0, "roas": 5.4755,
}


def _history(store, business: str) -> str:
    path = store._resolve_business_file(business, "metrics/roas/meta.md", sync=False)
    return path.read_text(encoding="utf-8") if path.exists() else ""


def test_new_sync_appends_one_truthful_entry(pg_store, pg_store_dsn):
    _seed_business(pg_store_dsn, "roashist1")
    _upsert_meta_policy(pg_store_dsn, "roashist1", "camp-a")
    _write_plan(pg_store, "roashist1", "camp-a")
    _write_sync(pg_store, "roashist1", "camp-a", "2026-07-08.json", _PIXEL_TOTALS)

    out = pg_store.assemble_roas_run_history("roashist1")
    assert out["appended"] == 1 and out["errors"] == 0, out
    text = _history(pg_store, "roashist1")
    # the PROCESS from the plan…
    for token in ("video ad", 'headline "Stop losing leads after the demo"',
                  "CTA SIGN_UP", "$10.0/day"):
        assert token in text, (token, text)
    # …and the MEASURED result from the sync receipt.
    for token in ("12304 impressions", "3 purchases", "attributed revenue $57.00",
                  "spend $10.41", "ROAS 5.48", "sync camp-a/2026-07-08.json"):
        assert token in text, (token, text)


def test_idempotent_per_sync_receipt(pg_store, pg_store_dsn):
    _seed_business(pg_store_dsn, "roashist2")
    _upsert_meta_policy(pg_store_dsn, "roashist2", "camp-a")
    _write_plan(pg_store, "roashist2", "camp-a")
    _write_sync(pg_store, "roashist2", "camp-a", "k1.json", _PIXEL_TOTALS)

    first = pg_store.assemble_roas_run_history("roashist2")
    second = pg_store.assemble_roas_run_history("roashist2")
    assert first["appended"] == 1
    assert second["appended"] == 0 and second["skipped"] >= 1, second
    assert _history(pg_store, "roashist2").count("- campaign ") == 1


def test_second_sync_appends_second_entry(pg_store, pg_store_dsn):
    _seed_business(pg_store_dsn, "roashist3")
    _upsert_meta_policy(pg_store_dsn, "roashist3", "camp-a")
    _write_plan(pg_store, "roashist3", "camp-a")
    _write_sync(pg_store, "roashist3", "camp-a", "k1.json", _PIXEL_TOTALS)
    pg_store.assemble_roas_run_history("roashist3")
    _write_sync(pg_store, "roashist3", "camp-a", "k2.json",
                {**_PIXEL_TOTALS, "purchase_value_usd": 12.0, "roas": 1.1528})

    out = pg_store.assemble_roas_run_history("roashist3")
    assert out["appended"] == 1, out
    text = _history(pg_store, "roashist3")
    assert text.count("- campaign ") == 2
    assert "sync camp-a/k1.json" in text and "sync camp-a/k2.json" in text
    assert "ROAS 1.15" in text  # the second sync's own figure, not a rollup


def test_missing_plan_degrades_to_planless_process(pg_store, pg_store_dsn):
    _seed_business(pg_store_dsn, "roashist4")
    _upsert_meta_policy(pg_store_dsn, "roashist4", "camp-b")
    _write_sync(pg_store, "roashist4", "camp-b", "k1.json", _PIXEL_TOTALS)

    out = pg_store.assemble_roas_run_history("roashist4")
    assert out["appended"] == 1 and out["errors"] == 0, out
    assert "no launch plan recorded" in _history(pg_store, "roashist4")


def test_no_attributed_revenue_marks_roas_na(pg_store, pg_store_dsn):
    # Pre-pixel receipts (or channels without purchase attribution) carry no roas — the
    # entry must say so instead of inventing a figure.
    _seed_business(pg_store_dsn, "roashist5")
    _upsert_meta_policy(pg_store_dsn, "roashist5", "camp-c")
    _write_plan(pg_store, "roashist5", "camp-c")
    _write_sync(pg_store, "roashist5", "camp-c", "k1.json",
                {"impressions": 900, "clicks": 21, "spend_usd": 8.0})

    out = pg_store.assemble_roas_run_history("roashist5")
    assert out["appended"] == 1, out
    text = _history(pg_store, "roashist5")
    assert "ROAS n/a (no attributed revenue synced)" in text
    assert "spend $8.00" in text


def test_no_campaigns_is_a_quiet_noop(pg_store, pg_store_dsn):
    _seed_business(pg_store_dsn, "roashist6")
    out = pg_store.assemble_roas_run_history("roashist6")
    assert out == {"success": True, "business": "roashist6", "appended": 0,
                   "skipped": 0, "errors": 0, "entries": []}
    assert _history(pg_store, "roashist6") == ""
