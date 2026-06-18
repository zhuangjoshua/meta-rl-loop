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
