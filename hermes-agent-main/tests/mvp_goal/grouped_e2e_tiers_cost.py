#!/usr/bin/env python3
"""Grouped E2E (GOAL_RULES §2) for the tiers-cost group — drives the REAL money-rail
handlers against the local Postgres rig, NOT unit mocks. Stripe is stubbed at the
`stripe_util.stripe_request` seam (no key, no network); auth/DB/ledger are the real engine.

Real entrypoint: the `/v1` Control API router (`build_control_router`) — the exact surface
the dashboard mounts for operator billing. One throwaway DB.

Cards proven:
  (1) Subscription tiers get settled — the tier MENU lists multiple configured tiers and the
      Stripe price_id is NEVER exposed to the caller (no price-substitution oracle).
  (2) Subscription tiers get settled — a chosen tier opens a Stripe SUBSCRIPTION checkout
      whose metadata carries the plan name + weekly allowance; an unknown plan is refused 404;
      no Stripe key ⇒ 503 (never a faked URL).
  (3) Subscription tiers get settled — the billing webhook for a paid operator_subscription
      checkout SETTLES the chosen tier's allowance onto the operator's billing account.
  (4) Exact cost per business — a settled usage event is queryable per-business for the
      current metering period via the real reserve→settle ledger (get_usage_summary), with
      exact micro-USD cost (no markup) and web-search tracked separately via the route field.

Run:  source harness/.pg_env ; python tests/mvp_goal/grouped_e2e_tiers_cost.py
Exits 0 and prints a STRICT JSON verdict line prefixed `RESULT_JSON: `.
"""

from __future__ import annotations

import json
import os
import sys
import traceback
import uuid
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import psycopg
from psycopg.conninfo import make_conninfo
from fastapi import FastAPI
from fastapi.testclient import TestClient

from plugins.takyon import app_usage, billing, stripe_util
from plugins.takyon.control_api import build_control_router, get_control_conn
from plugins.takyon.control_plane import provision_user_on_first_login
from plugins.takyon.db.runner import run_migrations
from plugins.takyon.stripe_util import build_signature_header

ADMIN_DSN = os.environ["TAKYON_TEST_PG_DSN"]

evidence: list[str] = []
results = {
    "plans_listed_no_price_id": False,
    "checkout_stamps_tier_metadata": False,
    "unknown_plan_refused": False,
    "no_key_refused_503": False,
    "webhook_settles_tier_allowance": False,
    "exact_cost_per_period_no_markup": False,
    "web_search_route_separate": False,
}


def log(msg: str) -> None:
    evidence.append(msg)
    print(msg, flush=True)


def make_db() -> str:
    dbname = f"mvpe2e_tc_{uuid.uuid4().hex[:10]}"
    with psycopg.connect(ADMIN_DSN, autocommit=True) as admin:
        admin.execute(f'create database "{dbname}"')
    dsn = make_conninfo(ADMIN_DSN, dbname=dbname)
    with psycopg.connect(dsn, autocommit=True) as conn:
        run_migrations(conn)
    return dsn


def drop_db(dsn: str) -> None:
    name = psycopg.conninfo.conninfo_to_dict(dsn)["dbname"]
    with psycopg.connect(ADMIN_DSN, autocommit=True) as admin:
        admin.execute(f'drop database if exists "{name}" with (force)')


_CATALOG = json.dumps(
    [
        {"id": "starter", "price_id": "price_e2e_starter", "name": "Starter",
         "amount_cents": 2000, "weekly_allowance_cents": 1000, "interval": "month"},
        {"id": "pro", "price_id": "price_e2e_pro", "name": "Pro",
         "amount_cents": 5000, "weekly_allowance_cents": 5000, "interval": "month",
         "featured": True, "features": ["5 companies"]},
    ]
)


def _client(conn) -> TestClient:
    app = FastAPI()
    app.include_router(build_control_router())
    app.dependency_overrides[get_control_conn] = lambda: conn
    return app, TestClient(app)


def run_tier_group(conn) -> None:
    os.environ["TAKYON_HOST_ROLE"] = "safebox"
    os.environ["TAKYON_OPERATOR_PLANS_JSON"] = _CATALOG
    _app, client = _client(conn)
    uid, _created, raw = provision_user_on_first_login(conn, f"auth0|{uuid.uuid4().hex}", "owner@example.com")
    auth = {"Authorization": f"Bearer {raw}"}

    # (1) tier menu lists multiple tiers and HIDES the Stripe price id
    resp = client.get("/v1/billing/plans", headers=auth)
    assert resp.status_code == 200, resp.text
    plans = resp.json()["plans"]
    ids = [p["id"] for p in plans]
    no_price = all("price_id" not in p and "priceId" not in p for p in plans)
    results["plans_listed_no_price_id"] = ids == ["starter", "pro"] and no_price
    log(f"[1] tier menu ids={ids} price_id_hidden={no_price}")

    # (2) chosen-tier subscription checkout stamps tier metadata; (3-prep) webhook will settle.
    captured: dict = {}

    def _stub_checkout(path, params, *, method="POST"):
        if path == "customers" or path.startswith("customers/"):
            return {"id": "cus_e2e_1", "metadata": {}}
        if path == "checkout/sessions":
            captured["params"] = params
            return {"id": "cs_e2e_pro", "url": "https://checkout.stripe.com/c/pay/cs_e2e_pro"}
        return {}

    os.environ["STRIPE_SECRET_KEY"] = "sk_test_e2e"
    real_request = stripe_util.stripe_request
    stripe_util.stripe_request = _stub_checkout
    try:
        resp = client.post(
            "/v1/billing/subscription/checkout",
            headers=auth,
            json={"plan_id": "pro", "success_url": "https://app/ok", "cancel_url": "https://app/no"},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        p = captured.get("params", {})
        results["checkout_stamps_tier_metadata"] = (
            body["plan_id"] == "pro"
            and p.get("mode") == "subscription"
            and p.get("line_items[0][price]") == "price_e2e_pro"
            and p.get("metadata[takyon_plan_name]") == "Pro"
            and p.get("subscription_data[metadata][takyon_allowance_weekly_cents]") == 5000
            and bool(body.get("checkout_url"))
        )
        log(f"[2] checkout url={body.get('checkout_url')} mode={p.get('mode')} price={p.get('line_items[0][price]')}")

        # (2b) unknown plan refused
        r2 = client.post(
            "/v1/billing/subscription/checkout",
            headers=auth,
            json={"plan_id": "ghost", "success_url": "x", "cancel_url": "y"},
        )
        results["unknown_plan_refused"] = r2.status_code == 404
        log(f"[2b] unknown plan status={r2.status_code}")
    finally:
        stripe_util.stripe_request = real_request

    # (2c) no Stripe key ⇒ 503 (never a faked URL)
    os.environ.pop("STRIPE_SECRET_KEY", None)
    r3 = client.post(
        "/v1/billing/subscription/checkout",
        headers=auth,
        json={"plan_id": "pro", "success_url": "x", "cancel_url": "y"},
    )
    results["no_key_refused_503"] = r3.status_code == 503
    log(f"[2c] no-key status={r3.status_code} detail={r3.json().get('detail')}")

    # (3) webhook settles the chosen tier's weekly allowance onto the operator's account
    os.environ["STRIPE_BILLING_WEBHOOK_SECRET"] = "whsec_e2e"

    def _stub_subscriptions(path, params, *, method="POST"):
        if path == "subscriptions" and method == "GET":
            return {"data": [{
                "id": "sub_e2e_pro", "status": "active", "customer": "cus_e2e_1",
                "metadata": {"takyon_plan_name": "Pro", "takyon_allowance_weekly_cents": "5000"},
                "items": {"data": []},
            }]}
        return {}

    stripe_util.stripe_request = _stub_subscriptions
    try:
        event = {
            "id": f"evt_{uuid.uuid4().hex}",
            "type": "checkout.session.completed",
            "data": {"object": {
                "id": f"cs_{uuid.uuid4().hex}", "client_reference_id": uid,
                "payment_status": "paid", "mode": "subscription", "customer": "cus_e2e_1",
                "metadata": {"purpose": "operator_subscription", "user_id": uid,
                             "takyon_plan_name": "Pro", "takyon_allowance_weekly_cents": "5000"},
            }},
        }
        raw_body = json.dumps(event)
        resp = client.post(
            "/v1/billing/webhook",
            content=raw_body,
            headers={"stripe-signature": build_signature_header(raw_body, "whsec_e2e")},
        )
        assert resp.status_code == 200, resp.text
        bal = billing.get_billing_balances(conn, uid)
        results["webhook_settles_tier_allowance"] = (
            resp.json().get("plan_name") == "Pro" and bal.allowance_included_cents == 5000
        )
        log(f"[3] webhook settled allowance_included_cents={bal.allowance_included_cents}")
    finally:
        stripe_util.stripe_request = real_request


def run_exact_cost_group(conn) -> None:
    slug = f"biz-{uuid.uuid4().hex[:8]}"
    conn.execute(
        "insert into businesses (slug, name, owner_user_id) values (%s, %s, %s)",
        (slug, "Acme", _owner_id(conn)),
    )
    # Open a budget with a pool cap so committed/remaining are queryable per period.
    app_usage.set_app_budget(conn, slug, hard_limit_microusd=10_000_000)

    # Inference event: reserve→settle exact provider cost (no markup).
    rk_inf = f"inf-{uuid.uuid4().hex[:8]}"
    app_usage.reserve_usage(conn, slug, estimated_cost_microusd=500_000, reservation_key=rk_inf, route="app")
    app_usage.settle_usage(conn, slug, rk_inf, actual_cost_microusd=437_000, provider="anthropic", model="claude-x")

    # Web-search event: tracked in the SAME ledger but tagged with a distinct route.
    rk_web = f"web-{uuid.uuid4().hex[:8]}"
    app_usage.reserve_usage(conn, slug, estimated_cost_microusd=10_000, reservation_key=rk_web, route="ceo_tool")
    app_usage.settle_usage(conn, slug, rk_web, actual_cost_microusd=9_000, provider="tavily")

    summary = app_usage.get_usage_summary(conn, slug)
    # exact cost == settled actual sum for the current period, no scaling/markup
    results["exact_cost_per_period_no_markup"] = (
        summary["status"] == "active"
        and summary["committed_microusd"] == 437_000 + 9_000
    )
    log(f"[4] usage_summary committed_microusd={summary['committed_microusd']} (expect 446000)")

    events = app_usage.list_usage_events(conn, slug)
    routes = {e.route for e in events if e.status == "completed"}
    results["web_search_route_separate"] = {"app", "ceo_tool"} <= routes
    log(f"[4b] completed-event routes={sorted(routes)}")


def _owner_id(conn) -> str:
    row = conn.execute("select id from users limit 1").fetchone()
    return str(row[0])


def main():
    dsn = make_db()
    try:
        conn = psycopg.connect(dsn, autocommit=True)
        try:
            run_tier_group(conn)
        except Exception:
            log("[tier] FATAL:\n" + traceback.format_exc())
        try:
            run_exact_cost_group(conn)
        except Exception:
            log("[exact-cost] FATAL:\n" + traceback.format_exc())
        finally:
            conn.close()
    finally:
        drop_db(dsn)

    wired = all(results.values())
    verdict = {
        "check": "grouped-e2e-tiers-cost",
        **results,
        "verdict": "WIRED" if wired else "NOT_WIRED",
        "evidence": " | ".join(evidence),
    }
    print("RESULT_JSON: " + json.dumps(verdict))
    sys.exit(0 if wired else 1)


if __name__ == "__main__":
    main()
