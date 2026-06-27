"""Hermetic unit tests for the thin OpenMeter billing mirror adapter.

These tests stay fully local: no real network, no Postgres, no live OpenMeter deployment. They
exercise the two riskiest behaviors of the adapter itself:

* recurring-plan-only guardrails stay explicit
* plan mirroring is idempotent when the active OpenMeter version already matches the Takyon
  fingerprint
* access projection can recover the original Takyon plan metadata from the mirrored OpenMeter plan
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from plugins.takyon import openmeter_backend


def _policy(**overrides):
    base = {
        "business_slug": "acme",
        "plan_key": "pro",
        "tier": "paid",
        "price_cents": 2000,
        "currency": "usd",
        "billing_interval": "month",
        "included_ai_budget_microusd": 5_000_000,
        "included_action_quota": 0,
        "notes": "",
        "metadata": {},
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def test_billing_cadence_guard_rejects_one_time():
    with pytest.raises(openmeter_backend.OpenMeterConfigurationError, match="recurring"):
        openmeter_backend.billing_cadence_for("one_time")


def test_sync_customer_uses_key_filter_and_customer_id(monkeypatch):
    calls: list[tuple[str, str, object]] = []
    customer_key = openmeter_backend.customer_key_for("acme", "user-123")

    def _fake_request(method, path, **kwargs):
        calls.append((method, path, kwargs.get("query")))
        if method == "GET" and path == "/openmeter/customers":
            assert kwargs.get("query") == {"filter[key]": customer_key}
            return {"data": [{"id": "cust_123", "key": customer_key}]}
        if method == "PUT" and path == "/openmeter/customers/cust_123":
            return {"id": "cust_123", "key": customer_key}
        raise AssertionError(f"unexpected OpenMeter call: {method} {path}")

    monkeypatch.setattr(openmeter_backend, "_require_enabled", lambda: None)
    monkeypatch.setattr(openmeter_backend, "_request_json", _fake_request)

    mirrored = openmeter_backend.sync_customer(
        business_slug="acme",
        app_user_id="user-123",
        email="user@example.com",
        name="User Example",
    )

    assert mirrored["id"] == "cust_123"
    assert calls == [
        ("GET", "/openmeter/customers", {"filter[key]": customer_key}),
        ("PUT", "/openmeter/customers/cust_123", None),
    ]


def test_sync_access_plan_reuses_matching_active_version(monkeypatch):
    policy = _policy()
    feature_key = openmeter_backend.access_feature_key_for(policy.business_slug)
    metadata = openmeter_backend._plan_metadata(policy, feature_key)
    calls: list[tuple[str, str, object]] = []

    def _fake_request(method, path, **kwargs):
        calls.append((method, path, kwargs.get("query")))
        if method == "GET" and path == "/openmeter/plans":
            assert kwargs.get("query") == {"filter[key]": openmeter_backend.plan_key_for("acme", "pro")}
            return {
                "data": [{
                    "id": "plan_123",
                    "key": openmeter_backend.plan_key_for("acme", "pro"),
                    "version": 7,
                    "status": "active",
                    "metadata": metadata,
                }]
            }
        raise AssertionError(f"unexpected OpenMeter call: {method} {path}")

    monkeypatch.setattr(openmeter_backend, "_require_enabled", lambda: None)
    monkeypatch.setattr(openmeter_backend, "_ensure_feature", lambda *args, **kwargs: None)
    monkeypatch.setattr(openmeter_backend, "_request_json", _fake_request)

    mirrored = openmeter_backend.sync_access_plan(policy)

    assert mirrored.id == "plan_123"
    assert mirrored.version == 7
    assert calls == [("GET", "/openmeter/plans", {"filter[key]": openmeter_backend.plan_key_for("acme", "pro")})]


def test_project_customer_access_reads_plan_metadata(monkeypatch):
    monkeypatch.setattr(openmeter_backend, "_require_enabled", lambda: None)
    monkeypatch.setattr(
        openmeter_backend,
        "_customer_by_key",
        lambda key: {"id": "cust_123", "key": key},
    )
    monkeypatch.setattr(
        openmeter_backend,
        "current_subscription",
        lambda **kwargs: {
            "id": "sub_123",
            "status": "active",
            "active_to": "2026-07-01T00:00:00Z",
            "plan": {"id": "plan_123", "key": "tk_acme_plan_pro", "version": 3},
        },
    )

    def _fake_request(method, path, **kwargs):
        if path == "/openmeter/customers/cust_123/entitlement-access":
            return {
                "data": [
                    {
                        "feature": {"key": "tk_acme_app_access"},
                        "has_access": True,
                    }
                ]
            }
        if path == "/openmeter/plans/plan_123":
            return {
                "id": "plan_123",
                "key": "tk_acme_plan_pro",
                "version": 3,
                "metadata": {
                    "takyon_plan_key": "pro",
                    "takyon_tier": "paid",
                },
            }
        raise AssertionError(f"unexpected OpenMeter call: {method} {path}")

    monkeypatch.setattr(openmeter_backend, "_request_json", _fake_request)

    snapshot = openmeter_backend.project_customer_access(
        business_slug="acme",
        app_user_id="user-123",
    )

    assert snapshot.has_access is True
    assert snapshot.takyon_plan_key == "pro"
    assert snapshot.tier == "paid"
    assert snapshot.subscription_id == "sub_123"
    assert snapshot.plan_version == 3
    assert snapshot.degraded is False
    assert snapshot.metadata["projection"] == "openmeter"
    assert "authority" not in snapshot.metadata


# --- OpenMeter-first migration: billing anchor + subscription correlation + fail-open grace ---


def test_upsert_customer_stripe_data_puts_billing_anchor(monkeypatch):
    """The previously-unwired billing anchor: PUT /customers/{id}/billing binds the OpenMeter
    customer to the real Stripe customer so subscriptions invoice against the actual charge."""
    customer_key = openmeter_backend.customer_key_for("acme", "user-123")
    calls: list[tuple[str, str, object]] = []

    monkeypatch.setattr(openmeter_backend, "_require_enabled", lambda: None)
    monkeypatch.setattr(openmeter_backend, "_customer_by_key", lambda key: {"id": "cust_9", "key": key})

    def _fake_request(method, path, **kwargs):
        calls.append((method, path, kwargs.get("payload")))
        return {"id": "cust_9", "type": "stripe"}

    monkeypatch.setattr(openmeter_backend, "_request_json", _fake_request)

    openmeter_backend.upsert_customer_stripe_data(
        business_slug="acme",
        app_user_id="user-123",
        stripe_customer_id="cus_live_42",
        stripe_default_payment_method_id="pm_7",
    )

    # Verified against live OpenMeter: the binding goes under app_data.stripe with snake_case
    # customer_id / default_payment_method_id (the old {type, stripe_customer_id} shape 400s).
    assert calls == [
        (
            "PUT",
            "/openmeter/customers/cust_9/billing",
            {"app_data": {"stripe": {"customer_id": "cus_live_42", "default_payment_method_id": "pm_7"}}},
        )
    ]


def test_upsert_customer_stripe_data_raises_when_customer_missing(monkeypatch):
    """Fail-closed: binding a Stripe customer to a non-existent OpenMeter customer must raise, not
    silently no-op (the caller wraps it fail-soft, but the adapter itself stays honest)."""
    monkeypatch.setattr(openmeter_backend, "_require_enabled", lambda: None)
    monkeypatch.setattr(openmeter_backend, "_customer_by_key", lambda key: None)
    monkeypatch.setattr(
        openmeter_backend, "_request_json",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not PUT when customer missing")),
    )
    with pytest.raises(openmeter_backend.OpenMeterAPIError, match="customer not found"):
        openmeter_backend.upsert_customer_stripe_data(
            business_slug="acme", app_user_id="ghost", stripe_customer_id="cus_x",
        )


def test_ensure_subscription_carries_stripe_subscription_id(monkeypatch):
    """The OpenMeter sub correlates to the real Stripe sub via metadata, so the mirror is auditable."""
    monkeypatch.setattr(openmeter_backend, "_require_enabled", lambda: None)
    monkeypatch.setattr(openmeter_backend, "current_subscription", lambda **kw: None)
    captured: dict[str, object] = {}

    def _fake_request(method, path, **kwargs):
        if method == "POST" and path == "/openmeter/subscriptions":
            captured["payload"] = kwargs.get("payload")
            return {"id": "sub_new"}
        raise AssertionError(f"unexpected OpenMeter call: {method} {path}")

    monkeypatch.setattr(openmeter_backend, "_request_json", _fake_request)
    plan = openmeter_backend.OpenMeterPlanSnapshot(id="p1", key="tk_acme_plan_pro", version=2, status="active", metadata={})

    openmeter_backend.ensure_subscription(
        business_slug="acme", app_user_id="user-123", plan=plan, stripe_subscription_id="sub_1Stripe",
    )
    md = captured["payload"]["metadata"]
    assert md["takyon_stripe_subscription_id"] == "sub_1Stripe"
    # absent when not supplied → no spurious metadata churn
    captured.clear()
    openmeter_backend.ensure_subscription(business_slug="acme", app_user_id="user-123", plan=plan)
    assert "takyon_stripe_subscription_id" not in captured["payload"]["metadata"]


def test_project_customer_access_degraded_when_unreadable_and_no_subscription(monkeypatch):
    """Fail-OPEN grace: a 404 on entitlement-access with NO active subscription is NOT an
    explicit 'no access' — it is degraded, so the projection must preserve last-known-good."""
    monkeypatch.setattr(openmeter_backend, "_require_enabled", lambda: None)
    monkeypatch.setattr(openmeter_backend, "_customer_by_key", lambda key: {"id": "cust_1", "key": key})
    monkeypatch.setattr(openmeter_backend, "current_subscription", lambda **kw: None)
    # entitlement-access 404 -> _request_json returns None for the allow_status soft-miss.
    monkeypatch.setattr(openmeter_backend, "_request_json", lambda *a, **k: None)

    snapshot = openmeter_backend.project_customer_access(business_slug="acme", app_user_id="user-123")
    assert snapshot.degraded is True
    assert snapshot.has_access is False
    assert snapshot.metadata["projection"] == "openmeter"
    assert "authority" not in snapshot.metadata


def test_project_customer_access_active_subscription_is_definitive_access(monkeypatch):
    """An active subscription mirrors access even if entitlement-access 404s — and that is a
    definitive positive, NOT degraded."""
    monkeypatch.setattr(openmeter_backend, "_require_enabled", lambda: None)
    monkeypatch.setattr(openmeter_backend, "_customer_by_key", lambda key: {"id": "cust_1", "key": key})
    monkeypatch.setattr(
        openmeter_backend, "current_subscription",
        lambda **kw: {"id": "sub_a", "status": "active", "plan": {}},
    )
    monkeypatch.setattr(openmeter_backend, "_request_json", lambda *a, **k: None)

    snapshot = openmeter_backend.project_customer_access(business_slug="acme", app_user_id="user-123")
    assert snapshot.has_access is True
    assert snapshot.degraded is False


def test_project_customer_access_degraded_on_200_without_explicit_access(monkeypatch):
    """Fail-OPEN grace covers more than 404: a 200 that does NOT carry an explicit access decision
    for this feature (empty body, or an envelope missing the feature — the live Kong-misroute case)
    is non-definitive => degraded=True, preserve last-known-good. (raw_access is {} here, not None.)"""
    monkeypatch.setattr(openmeter_backend, "_require_enabled", lambda: None)
    monkeypatch.setattr(openmeter_backend, "_customer_by_key", lambda key: {"id": "cust_1", "key": key})
    monkeypatch.setattr(openmeter_backend, "current_subscription", lambda **kw: None)
    monkeypatch.setattr(openmeter_backend, "_request_json", lambda *a, **k: {})  # 200 empty/feature-missing
    snap = openmeter_backend.project_customer_access(business_slug="acme", app_user_id="u")
    assert snap.degraded is True
    assert snap.has_access is False


def test_project_customer_access_explicit_no_access_is_definitive(monkeypatch):
    """A 200 that explicitly says has_access=false IS definitive (degraded=False) — safe to retire."""
    monkeypatch.setattr(openmeter_backend, "_require_enabled", lambda: None)
    monkeypatch.setattr(openmeter_backend, "_customer_by_key", lambda key: {"id": "cust_1", "key": key})
    monkeypatch.setattr(openmeter_backend, "current_subscription", lambda **kw: None)
    feature = openmeter_backend.access_feature_key_for("acme")

    def _req(method, path, **kw):
        if "entitlement-access" in path:
            return {"entitlements": {feature: {"has_access": False}}}
        return None

    monkeypatch.setattr(openmeter_backend, "_request_json", _req)
    snap = openmeter_backend.project_customer_access(business_slug="acme", app_user_id="u")
    assert snap.degraded is False
    assert snap.has_access is False


def test_access_plan_rate_card_is_zero_priced_to_avoid_double_billing():
    """OpenMeter is an access/usage MIRROR, not a second charger: the plan rate card must be $0 so
    OpenMeter never issues a Stripe invoice on top of the product's own Checkout (double-billing)."""
    policy = _policy(price_cents=2000)
    feature_key = openmeter_backend.access_feature_key_for(policy.business_slug)
    body = openmeter_backend._plan_create_body(policy, feature_key, "P1M", {})
    rate_card = body["phases"][0]["rate_cards"][0]
    assert rate_card["price"]["amount"] == "0"
