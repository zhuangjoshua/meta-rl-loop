"""STEP C gateway cutover (deploy/SAFEBOX-BROKER-REMEDIATION-PLAN.md).

When the safebox provider broker is enabled, the gateway must POST the call to the safebox and NOT
reserve/settle the usage ledger itself (the broker is the one money gate — a local reserve would
double-charge). The response envelope and the broker error mapping must be preserved. These run without
Postgres by stubbing the identity / pricing seams and the safebox client.
"""
from decimal import Decimal

import pytest

from plugins.takyon import ai_gateway, app_identity


class _FakeUser:
    def __init__(self):
        self.id = "cust_X"
        self.tier = "pro"


class _Plan:
    metadata = {}
    tier = "pro"
    included_ai_budget_microusd = 5_000_000


@pytest.fixture
def broker_on(monkeypatch):
    """Enable the broker path and stub every non-broker seam so the focus is the cutover branch."""
    monkeypatch.setattr(ai_gateway.safebox, "provider_broker_enabled", lambda: True)
    monkeypatch.setattr(app_identity, "validate_session", lambda c, b, t: _FakeUser())
    monkeypatch.setattr(ai_gateway, "_resolve_plan_for_user", lambda c, b, u: (object(), _Plan()))
    monkeypatch.setattr(ai_gateway, "_check_app_ai_rate_limit", lambda c, u: None)
    # A local reserve/settle on the broker path would DOUBLE-CHARGE — make them explode if reached.
    def _boom(*a, **k):
        raise AssertionError("local usage ledger must NOT be touched on the broker path")
    monkeypatch.setattr(ai_gateway, "reserve_usage", _boom)
    monkeypatch.setattr(ai_gateway, "settle_usage", _boom)
    monkeypatch.setattr(ai_gateway, "release_usage", _boom)
    # Deterministic pricing so the test does not depend on the live catalog.
    monkeypatch.setattr(ai_gateway, "anthropic_payload", lambda body: ({"model": "claude-x", "messages": body.get("messages", [])}, "claude-x", 50))
    monkeypatch.setattr(ai_gateway, "billed_microusd_cost", lambda *a, **k: (90, 120))
    monkeypatch.setattr(ai_gateway, "anthropic_rates_microusd_per_token", lambda m: (Decimal(0), Decimal(0), "src"))
    monkeypatch.setattr(ai_gateway, "anthropic_text", lambda raw: "hello")
    monkeypatch.setattr(ai_gateway, "tavily_request_microusd", lambda op, units=1: 1500)


def test_message_broker_path_posts_and_does_not_meter_locally(broker_on, monkeypatch):
    seen = {}

    def _fake_broker(provider, op, payload, **kw):
        seen.update(provider=provider, op=op, payload=payload, kw=kw)
        return {"id": "msg_1", "content": [{"type": "text", "text": "hello"}],
                "usage": {"input_tokens": 50, "output_tokens": 20}}

    monkeypatch.setattr(ai_gateway.safebox, "broker_provider_call", _fake_broker)
    body = {"messages": [{"role": "user", "content": "hi"}], "model": "claude-x"}
    out = ai_gateway.broker_message_for_business(
        object(), business_slug="climblog", raw_session_token="sess", body=body
    )
    # Routed to the broker with the inline-mint identity shape + the RAW body.
    assert seen["provider"] == "anthropic" and seen["op"] == "messages"
    assert seen["payload"] == body
    assert seen["kw"]["business"] == "climblog"
    assert seen["kw"]["action"] == "anthropic.messages"
    assert seen["kw"]["session_token"] == "sess"
    assert seen["kw"]["estimate_microusd"] == 120
    # Response envelope preserved; display cost computed from the broker's returned usage.
    assert out["success"] is True
    assert out["text"] == "hello"
    assert out["usage"]["actual_cost_microusd"] == 120
    assert out["usage"]["input_tokens"] == 50


def test_message_broker_remote_error_maps_to_gateway_error(broker_on, monkeypatch):
    def _raise(*a, **k):
        raise ai_gateway.safebox.RemoteSafeboxError(
            "boom", status_code=402, payload={"detail": {"error": "app_user_budget_exceeded"}}
        )

    monkeypatch.setattr(ai_gateway.safebox, "broker_provider_call", _raise)
    with pytest.raises(ai_gateway.GatewayMessageError) as ei:
        ai_gateway.broker_message_for_business(
            object(), business_slug="climblog", raw_session_token="sess",
            body={"messages": [{"role": "user", "content": "hi"}]},
        )
    assert ei.value.status_code == 402


def test_message_broker_403_maps_to_subscription_required(broker_on, monkeypatch):
    def _raise(*a, **k):
        raise ai_gateway.safebox.RemoteSafeboxError("nope", status_code=403, payload={"detail": "subscription_required"})

    monkeypatch.setattr(ai_gateway.safebox, "broker_provider_call", _raise)
    with pytest.raises(ai_gateway.GatewayMessageError) as ei:
        ai_gateway.broker_message_for_business(
            object(), business_slug="b", raw_session_token="s",
            body={"messages": [{"role": "user", "content": "hi"}]},
        )
    assert ei.value.status_code == 402
    assert ei.value.detail == {"error": "subscription_required"}


def test_search_broker_path_posts_tavily_and_does_not_meter_locally(broker_on, monkeypatch):
    seen = {}

    def _fake_broker(provider, op, payload, **kw):
        seen.update(provider=provider, op=op, payload=payload, kw=kw)
        return {"results": [{"title": "T", "url": "http://x", "content": "c"}]}

    monkeypatch.setattr(ai_gateway.safebox, "broker_provider_call", _fake_broker)
    out = ai_gateway.broker_search_for_business(
        object(), business_slug="climblog", raw_session_token="sess",
        body={"operation": "search", "query": "best widgets", "max_results": 3},
    )
    assert seen["provider"] == "tavily" and seen["op"] == "search"
    # The broker route reads endpoint/operation/units off the payload.
    assert seen["payload"]["endpoint"] == "search"
    assert seen["payload"]["operation"] == "search"
    assert seen["payload"]["units"] == 1
    assert seen["payload"]["query"] == "best widgets"
    assert seen["kw"]["action"] == "tavily.search"
    assert seen["kw"]["estimate_microusd"] == 1500
    assert out["success"] is True
    assert out["operation"] == "search"
    assert out["results"][0]["url"] == "http://x"
    assert out["usage"]["settled"] is True
