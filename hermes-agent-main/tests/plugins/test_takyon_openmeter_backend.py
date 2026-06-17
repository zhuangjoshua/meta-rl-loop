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


def test_sync_access_plan_reuses_matching_active_version(monkeypatch):
    policy = _policy()
    feature_key = openmeter_backend.access_feature_key_for(policy.business_slug)
    metadata = openmeter_backend._plan_metadata(policy, feature_key)
    calls: list[tuple[str, str]] = []

    def _fake_request(method, path, **kwargs):
        calls.append((method, path))
        if method == "GET" and path.endswith(openmeter_backend.plan_key_for("acme", "pro")):
            return {
                "id": "plan_123",
                "key": openmeter_backend.plan_key_for("acme", "pro"),
                "version": 7,
                "status": "active",
                "metadata": metadata,
            }
        raise AssertionError(f"unexpected OpenMeter call: {method} {path}")

    monkeypatch.setattr(openmeter_backend, "_require_enabled", lambda: None)
    monkeypatch.setattr(openmeter_backend, "_ensure_feature", lambda *args, **kwargs: None)
    monkeypatch.setattr(openmeter_backend, "_request_json", _fake_request)

    mirrored = openmeter_backend.sync_access_plan(policy)

    assert mirrored.id == "plan_123"
    assert mirrored.version == 7
    assert calls == [("GET", f"/api/v1/plans/{openmeter_backend.plan_key_for('acme', 'pro')}")]


def test_project_customer_access_reads_plan_metadata(monkeypatch):
    monkeypatch.setattr(openmeter_backend, "_require_enabled", lambda: None)
    monkeypatch.setattr(
        openmeter_backend,
        "current_subscription",
        lambda **kwargs: {
            "id": "sub_123",
            "status": "active",
            "activeTo": "2026-07-01T00:00:00Z",
            "plan": {"id": "plan_123", "key": "tk_acme_plan_pro", "version": 3},
        },
    )

    def _fake_request(method, path, **kwargs):
        if path.endswith("/access"):
            return {"entitlements": {"tk_acme_app_access": {"hasAccess": True}}}
        if path == "/api/v1/plans/plan_123":
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
