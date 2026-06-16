from __future__ import annotations

import pytest

from plugins.takyon.ai_provider import (
    AnthropicPricingUnavailable,
    anthropic_payload,
    microusd_cost,
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


def test_tavily_request_microusd_uses_exact_catalog_entries():
    assert tavily_request_microusd("search") == 8_000
    assert tavily_request_microusd("search_advanced") == 16_000
    assert tavily_request_microusd("extract", units=2) == 16_000


def test_tavily_request_microusd_blocks_unknown_operation():
    with pytest.raises(TavilyPricingUnavailable):
        tavily_request_microusd("imaginary")
