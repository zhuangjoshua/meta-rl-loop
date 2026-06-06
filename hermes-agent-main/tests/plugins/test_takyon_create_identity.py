import pytest
import types

from plugins.takyon.cli import (
    TakyonError,
    _business_bootstrap_instruction,
    _derive_name_from_goal_with_llm,
    _resolve_create_identity,
    _resolve_dashboard_create_identity,
)


def test_resolve_create_identity_prefers_name_derived_from_goal_text():
    name, slug = _resolve_create_identity("", "build Longer - a men's health app")

    assert name == "Longer"
    assert slug == "longer"


def test_resolve_create_identity_supports_double_dash_name_separator():
    name, slug = _resolve_create_identity("", "Longer -- a men's health app")

    assert name == "Longer"
    assert slug == "longer"


def test_resolve_create_identity_falls_back_to_humanized_slug_hint():
    name, slug = _resolve_create_identity("", "", "coachesyard")

    assert name == "Coachesyard"
    assert slug == "coachesyard"


def test_bootstrap_prompt_pins_canonical_business_name():
    prompt = _business_bootstrap_instruction(
        "longer",
        "a men's health app",
        "live",
        business_name="Longer",
    )

    assert "Canonical business name: Longer" in prompt
    assert "Use exactly this business name on the first pass." in prompt


def test_resolve_dashboard_create_identity_prefers_llm_name(monkeypatch):
    monkeypatch.setattr(
        "plugins.takyon.cli._derive_name_from_goal_with_llm",
        lambda goal, operator_user_id=None: "Longer",
    )

    name, slug = _resolve_dashboard_create_identity(
        "",
        "Longer -- a men's health app",
        "",
        operator_user_id="user-1",
    )

    assert name == "Longer"
    assert slug == "longer"


def test_derive_name_from_goal_with_llm_uses_operator_budget_rail(monkeypatch):
    captured: dict[str, object] = {}

    def fake_reserve(**kwargs):
        captured["reserve"] = dict(kwargs)
        return "rk-create-name", 1

    def fake_finalize(**kwargs):
        captured["finalize"] = dict(kwargs)
        return ""

    def fake_call_llm(**kwargs):
        captured["llm"] = dict(kwargs)
        return types.SimpleNamespace(
            choices=[types.SimpleNamespace(message=types.SimpleNamespace(content="Longer"))],
            usage=None,
        )

    monkeypatch.setattr("plugins.takyon.cli.load_takyon_env", lambda: None)
    monkeypatch.setattr(
        "plugins.takyon.cli._read_model_config",
        lambda _store: {"provider": "openrouter", "model": "main-model", "path": "config.yaml"},
    )
    monkeypatch.setattr(
        "plugins.takyon.cli._require_agent_model_config",
        lambda config, model_override=None: "main-model",
    )
    monkeypatch.setattr("plugins.takyon.cli._operator_budget_reserve", fake_reserve)
    monkeypatch.setattr("plugins.takyon.cli._operator_budget_finalize", fake_finalize)
    monkeypatch.setattr(
        "takyon_cli.runtime_provider.resolve_runtime_provider",
        lambda **_kwargs: {
            "provider": "openrouter",
            "model": "main-model",
            "base_url": "https://openrouter.ai/api/v1",
            "api_key": "sk-test",
        },
    )
    monkeypatch.setattr("agent.auxiliary_client.call_llm", fake_call_llm)

    name = _derive_name_from_goal_with_llm(
        "Longer -- a men's health app",
        operator_user_id="user-1",
    )

    assert name == "Longer"
    assert captured["reserve"] == {
        "operator_user_id": "user-1",
        "business_slug": None,
        "reservation_key": captured["reserve"]["reservation_key"],
        "estimate_cents": 1,
    }
    assert captured["llm"]["model"] == "main-model"
    assert captured["finalize"] == {
        "operator_user_id": "user-1",
        "business_slug": None,
        "reservation_key": "rk-create-name",
        "reserved_cents": 1,
        "actual_cents": 1,
    }


def test_resolve_dashboard_create_identity_falls_back_when_llm_name_call_fails(monkeypatch):
    def _boom(goal, operator_user_id=None):
        raise RuntimeError("aux unavailable")

    monkeypatch.setattr("plugins.takyon.cli._derive_name_from_goal_with_llm", _boom)

    name, slug = _resolve_dashboard_create_identity("", "Longer -- a men's health app")

    assert name == "Longer"
    assert slug == "longer"


def test_resolve_dashboard_create_identity_preserves_budget_exhaustion(monkeypatch):
    def _exhausted(goal, operator_user_id=None):
        raise TakyonError("operator budget exhausted: need 1c, allowance 0c + topup 0c")

    monkeypatch.setattr("plugins.takyon.cli._derive_name_from_goal_with_llm", _exhausted)

    with pytest.raises(TakyonError, match="operator budget exhausted"):
        _resolve_dashboard_create_identity(
            "",
            "Longer -- a men's health app",
            operator_user_id="user-1",
        )
