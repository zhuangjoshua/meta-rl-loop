from __future__ import annotations

import pytest

from plugins.takyon.app_api import (
    _anthropic_payload,
    _app_budget_remaining_microusd,
    _microusd_cost,
    run_app_api_server,
)
from plugins.takyon.ai_provider import AnthropicPricingUnavailable
from plugins.takyon.core import TakyonStore


def test_anthropic_payload_accepts_prompt_and_estimates_tokens():
    payload, model, estimated_input_tokens = _anthropic_payload(
        {"prompt": "Write a changelog.", "max_tokens": 128}
    )

    assert model
    assert payload["messages"] == [{"role": "user", "content": "Write a changelog."}]
    assert payload["max_tokens"] == 128
    assert estimated_input_tokens > 0


def test_microusd_cost_uses_exact_model_catalog():
    assert _microusd_cost("claude-haiku-4.5", 100, 20) == 200
    assert _microusd_cost("anthropic/claude-sonnet-4.6", 100, 20) == 600
    assert _microusd_cost("claude-opus-4.6", 100, 20) == 1000


def test_microusd_cost_blocks_unknown_anthropic_model():
    with pytest.raises(AnthropicPricingUnavailable):
        _microusd_cost("claude-imaginary-99", 100, 20)


def test_app_budget_remaining_counts_recorded_usage(tmp_path, monkeypatch, pg_store_dsn):
    monkeypatch.setenv("TAKYON_HOME", str(tmp_path))
    monkeypatch.setenv("DATABASE_URL", pg_store_dsn)
    monkeypatch.setenv("TAKYON_PLATFORM_OWNER_SUB", "auth0|takyon-app-api")
    store = TakyonStore(tmp_path, database_url=pg_store_dsn)
    store.seed_platform_owner()
    store.commit(
        scope="business:clipbook",
        operations=[{"action": "business.upsert", "business": "clipbook", "name": "Clipbook"}],
        idempotency_key="init",
        reason="test",
        actor="test",
    )
    store.commit(
        scope="business:clipbook",
        operations=[{"action": "app.budget.set", "business": "clipbook", "hard_limit_microusd": 1000}],
        idempotency_key="budget",
        reason="test",
        actor="test",
    )
    store.commit(
        scope="business:clipbook",
        operations=[
            {
                "action": "app.usage.record",
                "business": "clipbook",
                "purpose": "ai_generate",
                "route": "/api/takyon/apps/clipbook/generate",
                "actual_cost_microusd": 375,
            }
        ],
        idempotency_key="usage",
        reason="test",
        actor="test",
    )

    budget = _app_budget_remaining_microusd("clipbook")

    assert budget["status"] == "active"
    assert budget["hard_limit_microusd"] == 1000
    assert budget["used_microusd"] == 375
    assert budget["remaining_microusd"] == 625


def test_run_app_api_server_is_retired():
    with pytest.raises(RuntimeError, match="retired"):
        run_app_api_server()
