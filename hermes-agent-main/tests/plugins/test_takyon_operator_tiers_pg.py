"""Postgres integration tests for operator multi-tier subscription billing — the
"Subscription tiers get settled" card.

Covers the genuinely-new surface added by this group:
  * the config-driven operator tier catalog (TAKYON_OPERATOR_PLANS_JSON)
  * GET  /v1/billing/plans            (tier menu; price_id NEVER exposed to the caller)
  * POST /v1/billing/subscription/checkout (Stripe subscription-mode checkout for a chosen
                                            tier; 404 unknown plan; 503 without Stripe key;
                                            metadata stamped so the webhook can settle it)
  * the billing-webhook `operator_subscription` branch settling the chosen tier's allowance

Exercises the REAL FastAPI request path (resolver + DB), Stripe stubbed (no network).
"""

from __future__ import annotations

import json
import uuid

import pytest

psycopg = pytest.importorskip("psycopg")
pytest.importorskip("fastapi")

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from plugins.takyon import billing, stripe_util  # noqa: E402
from plugins.takyon import control_api as capi  # noqa: E402
from plugins.takyon.control_api import (  # noqa: E402
    build_control_router,
    get_control_conn,
)
from plugins.takyon.control_plane import provision_user_on_first_login  # noqa: E402
from plugins.takyon.stripe_util import build_signature_header  # noqa: E402


def _sub() -> str:
    return f"auth0|{uuid.uuid4().hex}"


def _auth(raw: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {raw}"}


_TWO_TIER_CATALOG = json.dumps(
    [
        {
            "id": "starter",
            "price_id": "price_test_starter",
            "name": "Starter",
            "amount_cents": 2000,
            "weekly_allowance_cents": 1000,
            "interval": "month",
            "features": ["1 company"],
        },
        {
            "id": "pro",
            "price_id": "price_test_pro",
            "name": "Pro",
            "amount_cents": 5000,
            "weekly_allowance_cents": 5000,
            "interval": "month",
            "featured": True,
            "features": ["5 companies", "Priority agent"],
        },
    ]
)


@pytest.fixture
def client(pg_conn, monkeypatch):
    monkeypatch.setenv("TAKYON_HOST_ROLE", "safebox")
    monkeypatch.setenv("TAKYON_OPERATOR_PLANS_JSON", _TWO_TIER_CATALOG)
    app = FastAPI()
    app.include_router(build_control_router())
    app.dependency_overrides[get_control_conn] = lambda: pg_conn
    return TestClient(app)


# ── catalog resolution ──────────────────────────────────────────────────────


def test_catalog_resolves_multiple_tiers(monkeypatch):
    monkeypatch.setenv("TAKYON_OPERATOR_PLANS_JSON", _TWO_TIER_CATALOG)
    plans = capi.configured_operator_plans()
    assert [p["id"] for p in plans] == ["starter", "pro"]
    assert capi._operator_plan("pro")["price_id"] == "price_test_pro"
    assert capi._operator_plan("pro")["featured"] is True
    assert capi._operator_plan("ghost") is None


def test_catalog_single_tier_fallback_from_legacy_price(monkeypatch):
    monkeypatch.delenv("TAKYON_OPERATOR_PLANS_JSON", raising=False)
    monkeypatch.setenv("STRIPE_PRICE_PLATFORM_MONTHLY", "price_legacy_monthly")
    plans = capi.configured_operator_plans()
    assert len(plans) == 1
    assert plans[0]["price_id"] == "price_legacy_monthly"


def test_catalog_empty_when_unconfigured(monkeypatch):
    monkeypatch.delenv("TAKYON_OPERATOR_PLANS_JSON", raising=False)
    monkeypatch.delenv("STRIPE_PRICE_PLATFORM_MONTHLY", raising=False)
    monkeypatch.setattr(capi, "_env_value", lambda name: "")
    assert capi.configured_operator_plans() == []


# ── the tier-menu endpoint ──────────────────────────────────────────────────


def test_list_operator_plans_hides_price_id(client, pg_conn):
    _uid, _, raw = provision_user_on_first_login(pg_conn, _sub())
    resp = client.get("/v1/billing/plans", headers=_auth(raw))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    ids = [p["id"] for p in body["plans"]]
    assert ids == ["starter", "pro"]
    # the Stripe price id must NEVER reach the caller (no price substitution oracle)
    for plan in body["plans"]:
        assert "price_id" not in plan
        assert "priceId" not in plan
    pro = next(p for p in body["plans"] if p["id"] == "pro")
    assert pro["featured"] is True
    assert pro["weekly_allowance_cents"] == 5000


def test_list_operator_plans_requires_bearer(client):
    resp = client.get("/v1/billing/plans")
    assert resp.status_code == 401


# ── subscription checkout ───────────────────────────────────────────────────


def test_subscription_checkout_returns_url_and_stamps_metadata(client, pg_conn, monkeypatch):
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_xyz")
    uid, _, raw = provision_user_on_first_login(pg_conn, _sub(), "owner@example.com")

    captured: dict = {}

    def _fake_request(path, params, *, method="POST"):
        # customer ensure path + checkout session creation both flow through here.
        if path == "customers" or path.startswith("customers/"):
            return {"id": "cus_op_1", "metadata": {}}
        if path == "checkout/sessions":
            captured["params"] = params
            return {"id": "cs_sub_1", "url": "https://checkout.stripe.com/c/pay/cs_sub_1"}
        return {}

    monkeypatch.setattr(stripe_util, "stripe_request", _fake_request)
    resp = client.post(
        "/v1/billing/subscription/checkout",
        headers=_auth(raw),
        json={
            "plan_id": "pro",
            "success_url": "https://app.example.com/ok",
            "cancel_url": "https://app.example.com/no",
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["checkout_url"].endswith("cs_sub_1")
    assert body["plan_id"] == "pro"
    assert body["plan_name"] == "Pro"

    p = captured["params"]
    assert p["mode"] == "subscription"
    assert p["line_items[0][price]"] == "price_test_pro"
    assert p["metadata[purpose]"] == "operator_subscription"
    assert p["metadata[user_id]"] == uid
    assert p["metadata[takyon_plan_name]"] == "Pro"
    assert p["subscription_data[metadata][takyon_plan_name]"] == "Pro"
    assert p["subscription_data[metadata][takyon_allowance_weekly_cents]"] == 5000
    assert p["customer"] == "cus_op_1"


def test_subscription_checkout_unknown_plan_is_404(client, pg_conn, monkeypatch):
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_xyz")
    _uid, _, raw = provision_user_on_first_login(pg_conn, _sub(), "owner@example.com")

    def _fake_request(path, params, *, method="POST"):
        if path == "customers" or path.startswith("customers/"):
            return {"id": "cus_op_2", "metadata": {}}
        raise AssertionError("checkout must not be attempted for an unknown plan")

    monkeypatch.setattr(stripe_util, "stripe_request", _fake_request)
    resp = client.post(
        "/v1/billing/subscription/checkout",
        headers=_auth(raw),
        json={
            "plan_id": "does-not-exist",
            "success_url": "https://a/ok",
            "cancel_url": "https://a/no",
        },
    )
    assert resp.status_code == 404
    assert resp.json()["detail"] == "unknown_operator_plan"


def test_subscription_checkout_blocked_without_stripe_key(client, pg_conn, monkeypatch):
    monkeypatch.delenv("STRIPE_SECRET_KEY", raising=False)
    _uid, _, raw = provision_user_on_first_login(pg_conn, _sub(), "owner@example.com")
    resp = client.post(
        "/v1/billing/subscription/checkout",
        headers=_auth(raw),
        json={
            "plan_id": "pro",
            "success_url": "https://a/ok",
            "cancel_url": "https://a/no",
        },
    )
    assert resp.status_code == 503
    assert resp.json()["detail"] == "operator_subscription_unconfigured"


# ── webhook settles the chosen tier's allowance ─────────────────────────────


def _subscription_checkout_event(user_id, *, plan_name="Pro", weekly=5000, event_id=None):
    return {
        "id": event_id or f"evt_{uuid.uuid4().hex}",
        "type": "checkout.session.completed",
        "data": {
            "object": {
                "id": f"cs_{uuid.uuid4().hex}",
                "client_reference_id": user_id,
                "payment_status": "paid",
                "mode": "subscription",
                "customer": "cus_op_3",
                "metadata": {
                    "purpose": "operator_subscription",
                    "user_id": user_id,
                    "takyon_plan_id": "pro",
                    "takyon_plan_name": plan_name,
                    "takyon_allowance_weekly_cents": str(weekly),
                },
            }
        },
    }


def test_webhook_operator_subscription_settles_allowance(client, pg_conn, monkeypatch):
    monkeypatch.setenv("STRIPE_BILLING_WEBHOOK_SECRET", "whsec_test_xyz")
    uid, _, _ = provision_user_on_first_login(pg_conn, _sub(), "owner@example.com")

    # The webhook syncs the operator allowance with refresh_live=True; stub Stripe so the
    # chosen tier's recurring allowance flows through subscription metadata, no network.
    def _fake_request(path, params, *, method="POST"):
        if path == "subscriptions" and method == "GET":
            return {
                "data": [
                    {
                        "id": "sub_op_1",
                        "status": "active",
                        "customer": "cus_op_3",
                        "metadata": {
                            "takyon_plan_name": "Pro",
                            "takyon_allowance_weekly_cents": "5000",
                        },
                        "items": {"data": []},
                    }
                ]
            }
        return {}

    monkeypatch.setattr(stripe_util, "stripe_request", _fake_request)

    event = _subscription_checkout_event(uid, event_id="evt_op_sub_1")
    body = json.dumps(event)
    resp = client.post(
        "/v1/billing/webhook",
        content=body,
        headers={"stripe-signature": build_signature_header(body, "whsec_test_xyz")},
    )
    assert resp.status_code == 200, resp.text
    payload = resp.json()
    assert payload["ok"] is True
    assert payload["user_id"] == uid
    assert payload["plan_name"] == "Pro"
    assert payload["weekly_allowance_cents"] == 5000

    balances = billing.get_billing_balances(pg_conn, uid)
    assert balances.allowance_included_cents == 5000
