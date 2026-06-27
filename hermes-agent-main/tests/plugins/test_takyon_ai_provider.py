from __future__ import annotations

import pytest

from plugins.takyon.ai_provider import (
    AnthropicPricingUnavailable,
    anthropic_payload,
    anthropic_env,
    anthropic_key,
    call_tavily,
    microusd_cost,
    normalize_tavily_endpoint_operation,
    tavily_request_microusd,
    TavilyPricingUnavailable,
)


def test_anthropic_payload_accepts_prompt_and_estimates_tokens():
    payload, model, estimated_input_tokens = anthropic_payload(
        {"prompt": "Write a changelog.", "max_tokens": 128}
    )

    assert model
    assert payload["messages"] == [{"role": "user", "content": "Write a changelog."}]
    assert payload["max_tokens"] == 128
    assert estimated_input_tokens > 0


def test_microusd_cost_uses_exact_model_catalog():
    assert microusd_cost("claude-haiku-4.5", 100, 20) == 200
    assert microusd_cost("anthropic/claude-sonnet-4.6", 100, 20) == 600
    assert microusd_cost("claude-opus-4.6", 100, 20) == 1000


def test_microusd_cost_blocks_unknown_anthropic_model():
    with pytest.raises(AnthropicPricingUnavailable):
        microusd_cost("claude-imaginary-99", 100, 20)


def test_anthropic_env_prefers_safebox_over_local_env(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "local-api-key")
    monkeypatch.setenv("ANTHROPIC_TOKEN", "local-token")

    def fake_first_env_backed_value(*names):
        if names == ("ANTHROPIC_API_KEY",):
            return "remote-api-key"
        if names == ("ANTHROPIC_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN"):
            return "remote-token"
        return ""

    monkeypatch.setattr("plugins.takyon.safebox.first_env_backed_value", fake_first_env_backed_value)

    assert anthropic_env() == {
        "ANTHROPIC_API_KEY": "remote-api-key",
        "ANTHROPIC_TOKEN": "remote-token",
    }


def test_anthropic_key_falls_back_to_claude_code_oauth_token(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_TOKEN", raising=False)
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)

    def fake_first_env_backed_value(*names):
        if names == ("ANTHROPIC_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN"):
            return "remote-oauth-token"
        return ""

    monkeypatch.setattr("plugins.takyon.safebox.first_env_backed_value", fake_first_env_backed_value)

    assert anthropic_key() == "remote-oauth-token"


def test_tavily_request_microusd_uses_exact_catalog_entries():
    assert tavily_request_microusd("search") == 8_000
    assert tavily_request_microusd("search_advanced") == 16_000
    assert tavily_request_microusd("extract", units=2) == 16_000


def test_tavily_request_microusd_blocks_unknown_operation():
    with pytest.raises(TavilyPricingUnavailable):
        tavily_request_microusd("imaginary")


def test_tavily_endpoint_operation_is_fixed_to_search_or_extract():
    assert normalize_tavily_endpoint_operation("search", "search") == ("search", "search")
    assert normalize_tavily_endpoint_operation("search", "search_advanced") == (
        "search",
        "search_advanced",
    )
    assert normalize_tavily_endpoint_operation("extract", "extract") == ("extract", "extract")
    with pytest.raises(ValueError):
        normalize_tavily_endpoint_operation("crawl", "crawl")


def test_call_tavily_rejects_unsupported_endpoint_before_socket(monkeypatch):
    monkeypatch.setattr(
        "plugins.takyon.ai_provider.urllib.request.urlopen",
        lambda *a, **k: pytest.fail("opened socket"),
    )
    with pytest.raises(ValueError, match="unsupported_tavily_operation"):
        call_tavily("crawl", {"url": "https://example.com", "operation": "crawl"}, "tvly-key")
