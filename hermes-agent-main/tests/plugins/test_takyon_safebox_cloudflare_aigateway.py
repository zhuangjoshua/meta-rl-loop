import json

import pytest

from plugins.takyon import ai_provider, safebox_app
from plugins.takyon.safebox_capability import CapabilityScope


class _FakeResponse:
    def __init__(self, payload: dict):
        self._payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def read(self) -> bytes:
        return json.dumps(self._payload).encode("utf-8")


def _scope() -> CapabilityScope:
    return CapabilityScope(
        takyon_user_id="operator-1",
        business_slug="demo-biz",
        app_user_id="app-user-1",
        action="anthropic.messages",
        max_cost_microusd=1_000_000,
    )


def test_product_anthropic_uses_cloudflare_gateway_metadata(monkeypatch):
    captured = {}

    monkeypatch.setenv("CLOUDFLARE_AIG_ACCOUNT_ID", "acct-123")
    monkeypatch.setenv("CLOUDFLARE_AIG_GATEWAY_ID", "takyon-subuser")
    monkeypatch.setattr(safebox_app.safebox, "read_env_backed_value", lambda key: "cf-run-token")
    monkeypatch.setattr(ai_provider, "billed_microusd_cost", lambda *args, **kwargs: (10, 20))

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["headers"] = {key.lower(): value for key, value in request.header_items()}
        captured["body"] = json.loads(request.data.decode("utf-8"))
        captured["timeout"] = timeout
        return _FakeResponse({"usage": {"input_tokens": 2, "output_tokens": 3}, "content": []})

    monkeypatch.setattr(safebox_app.urllib.request, "urlopen", fake_urlopen)

    caller = safebox_app._anthropic_provider_caller(
        {"model": "claude-3-5-haiku-latest", "messages": [{"role": "user", "content": "hi"}]}
    )
    raw, actual = caller(_scope(), "anthropic-key")

    assert actual == 20
    assert raw["usage"]["output_tokens"] == 3
    assert captured["url"] == (
        "https://gateway.ai.cloudflare.com/v1/acct-123/takyon-subuser/anthropic/v1/messages"
    )
    assert captured["headers"]["cf-aig-authorization"] == "Bearer cf-run-token"
    assert captured["headers"]["x-api-key"] == "anthropic-key"
    assert captured["headers"]["cf-aig-collect-log-payload"] == "false"
    assert captured["headers"]["user-agent"] == "Takyon-Safebox/1.0"
    metadata = json.loads(captured["headers"]["cf-aig-metadata"])
    assert metadata == {
        "app_user_id": "app-user-1",
        "business_slug": "demo-biz",
        "provider": "anthropic",
        "action": "anthropic.messages",
        "model": "claude-3-5-haiku-latest",
    }
    assert captured["body"]["messages"][0]["content"] == "hi"


def test_product_anthropic_falls_back_when_cloudflare_gateway_disabled(monkeypatch):
    monkeypatch.delenv("CLOUDFLARE_AIG_ACCOUNT_ID", raising=False)
    monkeypatch.delenv("CLOUDFLARE_ACCOUNT_ID", raising=False)
    monkeypatch.setattr(ai_provider, "billed_microusd_cost", lambda *args, **kwargs: (10, 20))

    called = {}

    def fake_call_anthropic(payload, api_key):
        called["payload"] = payload
        called["api_key"] = api_key
        return {"usage": {"input_tokens": 2, "output_tokens": 3}, "content": []}

    monkeypatch.setattr(ai_provider, "call_anthropic", fake_call_anthropic)

    caller = safebox_app._anthropic_provider_caller(
        {"model": "claude-3-5-haiku-latest", "messages": [{"role": "user", "content": "hi"}]}
    )
    _raw, actual = caller(_scope(), "anthropic-key")

    assert actual == 20
    assert called["api_key"] == "anthropic-key"
    assert called["payload"]["messages"][0]["content"] == "hi"


def test_configured_cloudflare_gateway_without_token_fails_closed(monkeypatch):
    monkeypatch.setenv("CLOUDFLARE_AIG_ACCOUNT_ID", "acct-123")
    monkeypatch.setattr(safebox_app.safebox, "read_env_backed_value", lambda key: "")

    with pytest.raises(safebox_app.BrokerLedgerError, match="cloudflare_aig_unconfigured"):
        safebox_app._cloudflare_aig_anthropic_messages(
            {"model": "claude-3-5-haiku-latest", "messages": [{"role": "user", "content": "hi"}]},
            api_key="anthropic-key",
            scope=_scope(),
            model="claude-3-5-haiku-latest",
        )
