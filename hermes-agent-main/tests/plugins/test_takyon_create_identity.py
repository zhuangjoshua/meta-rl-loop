import pytest
import types
from pathlib import Path

from plugins.takyon.cli import (
    TakyonError,
    _business_bootstrap_instruction,
    _ceo_bootstrap_turn_config,
    _derive_name_from_goal_with_llm,
    _resolve_create_identity,
    _resolve_dashboard_create_identity,
    run_takyon_command,
)


def test_resolve_create_identity_prefers_name_derived_from_goal_text():
    name, slug = _resolve_create_identity("", "build Longer - a men's health app")

    assert name == "Longer"
    assert slug == "longer"


def test_resolve_create_identity_supports_double_dash_name_separator():
    name, slug = _resolve_create_identity("", "Longer -- a men's health app")

    assert name == "Longer"
    assert slug == "longer"


def test_resolve_create_identity_strips_inline_markdown_section_from_pasted_brief():
    brief = """RoomRemix ## 1. AI Room Redesign / Virtual Staging -- roomgpt.io, interiorai.com

*Input & upload*
- Upload via drag-drop zone, file picker, and mobile camera capture.
- Before image preview with crop/rotate/remove."""

    name, slug = _resolve_create_identity("", brief)

    assert name == "RoomRemix"
    assert slug == "roomremix"


def test_resolve_create_identity_falls_back_to_humanized_slug_hint():
    name, slug = _resolve_create_identity("", "", "coachesyard")

    assert name == "Coachesyard"
    assert slug == "coachesyard"


def test_resolve_create_identity_avoids_reserved_public_subdomains():
    name, slug = _resolve_create_identity("App", "", "")

    assert name == "App"
    assert slug == "app-site"


def test_resolve_create_identity_avoids_configured_reserved_public_subdomains(tmp_path, monkeypatch):
    monkeypatch.setenv("TAKYON_HOME", str(tmp_path))
    (tmp_path / "config.yaml").write_text(
        "dashboard:\n  reserved_public_subdomains:\n    - sai\n",
        encoding="utf-8",
    )

    name, slug = _resolve_create_identity("SAI", "", "")

    assert name == "SAI"
    assert slug == "sai-site"


def test_bootstrap_prompt_pins_canonical_business_name():
    prompt = _business_bootstrap_instruction(
        "longer",
        "a men's health app",
        "live",
        business_name="Longer",
    )

    assert "Canonical business name: Longer" in prompt
    assert "Use exactly the business name above. Do not invent a second company, umbrella brand, or product name." in prompt


def test_bootstrap_prompt_requires_bold_landing_and_branded_appkit_auth_surface():
    prompt = _business_bootstrap_instruction(
        "crm",
        "construction CRM",
        "live",
        business_name="CRM",
    )

    assert "Keep /app present and wired through the existing Hermes app kit runtime rails for sign-in, subscription, account, and profile access." in prompt
    assert "This must NOT look like a generic starter kit, membership template, or placeholder SaaS shell." in prompt
    assert "If `Explicit product workflow requested: no`, do NOT build a bespoke product application, custom backend workflow, domain-specific dashboard, fake coach/product tabs, sample domain data, charts, or invented in-app flows in this pass." in prompt
    assert "The landing page should be bold, visually opinionated, and unmistakably product-specific from the first pass, not timid, generic, or scaffold-like." in prompt
    assert "Make the existing sign-in, subscription, account, and profile surfaces polished, branded, and customer-specific instead of generic starter UI." in prompt
    assert "The result should be publishable and product-specific on the first pass." in prompt


def test_bootstrap_prompt_folds_workflow_build_into_second_pass_and_verifies_real_action():
    prompt = _business_bootstrap_instruction(
        "quoteforge",
        "Build a service that turns messy home-repair notes into a contractor-ready scope of work and quote request.",
        "live",
        business_name="QuoteForge",
    )

    assert "In this SAME second business_claude_agent_task, extend `/app` into the requested real signed-in subscribed customer workflow" in prompt
    assert "do NOT start a third product build pass here" in prompt
    assert "Verify that `/app` is wired to at least one real non-underscore HTTP action file under `product/site/actions/`" in prompt
    assert "call business_check_runtime_capabilities for the business and confirm the requested action is exposed" not in prompt


def test_bootstrap_prompt_trusts_authoritative_product_result_and_stops_on_blocker():
    prompt = _business_bootstrap_instruction(
        "crm",
        "construction CRM",
        "live",
        business_name="CRM",
    )

    # The contract, not the sentence: the CEO must trust the tool's exact result, stop on a real
    # blocker, and never proceed to X as if a blocked build had completed.
    assert "trusting only its exact success/blocker and surface_refresh publish status" in prompt
    assert "record that exact blocker in research/strategy.md and stop bootstrap there" in prompt
    assert "do not continue to X as if the product build completed." in prompt
    assert "Do not inspect the worker result." not in prompt
    assert "If something is blocked, record the blocker in research/strategy.md and continue with the next step." not in prompt


def test_bootstrap_prompt_passes_explicit_search_console_site_url():
    prompt = _business_bootstrap_instruction(
        "roomviewer-2",
        "room viewer",
        "live",
        business_name="Roomviewer 2",
    )

    assert 'Call business_register_search_console with the business, site_url "https://roomviewer-2.coscale.app/"' in prompt
    assert "Do not rely on inferred public_url here." in prompt
    assert '- Call business_seo_add_property with site_url "https://roomviewer-2.coscale.app/"' in prompt


def test_bootstrap_turn_config_uses_expanded_shared_turn_budget():
    config = _ceo_bootstrap_turn_config(
        "crm",
        "construction CRM",
        "live",
        business_name="CRM",
    )

    assert config["max_turns"] == 30


def test_ceo_prompt_rule_8_stops_redelegation_on_blocked_authority_violation():
    prompt_path = Path(__file__).resolve().parents[2] / "plugins" / "takyon" / "prompts" / "ceo.md"
    text = prompt_path.read_text(encoding="utf-8")

    assert "bounded same-run continuation" in text
    assert "do not re-delegate the same unchanged `product/site/` task" in text
    assert "automatic local source/build repair retry" not in text


def test_completion_discipline_rule_present_for_normal_turns_but_suppressed_on_bootstrap():
    from plugins.takyon.cli import _load_ceo_prompt

    anchor = "inspect what you actually built against what the operator asked for"
    proof_rule = "Do not call a product/runtime feature wired, done, published"

    # Interactive/cron turns share the full ceo.md verbatim → the completion-discipline rule
    # and the always-on proof rule are both present.
    full = _load_ceo_prompt()
    assert anchor in full
    assert proof_rule in full

    # Bootstrap runs the standard build sequence under its own instruction → the per-request
    # completion-discipline rule is dropped, but the always-on proof rule survives and the
    # fence markers must not leak. (A future ceo.md sentinel rename makes the strip a no-op,
    # which fails `anchor not in bootstrap_prompt` loudly rather than silently disabling it.)
    bootstrap_prompt = _ceo_bootstrap_turn_config(
        "crm", "construction CRM", "live", business_name="CRM"
    )["ephemeral_system_prompt"]
    assert anchor not in bootstrap_prompt
    assert proof_rule in bootstrap_prompt
    assert "COMPLETION-DISCIPLINE" not in bootstrap_prompt


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


def test_resolve_dashboard_create_identity_avoids_reserved_llm_slug(monkeypatch):
    monkeypatch.setattr(
        "plugins.takyon.cli._derive_name_from_goal_with_llm",
        lambda goal, operator_user_id=None: "App",
    )

    name, slug = _resolve_dashboard_create_identity(
        "",
        "build app for teams",
        "",
        operator_user_id="user-1",
    )

    assert name == "App"
    assert slug == "app-site"


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
        raise TakyonError("operator budget exhausted: need 1c, allowance 0c")

    monkeypatch.setattr("plugins.takyon.cli._derive_name_from_goal_with_llm", _exhausted)

    with pytest.raises(TakyonError, match="operator budget exhausted"):
        _resolve_dashboard_create_identity(
            "",
            "Longer -- a men's health app",
            operator_user_id="user-1",
        )


def test_confirmed_delete_uses_fresh_idempotency_key(monkeypatch):
    captured: list[str] = []

    class FakeStore:
        def __init__(self, *args, **kwargs):
            pass

        def commit(self, *, idempotency_key, **kwargs):
            captured.append(idempotency_key)
            return {"success": True}

    monkeypatch.setattr("plugins.takyon.cli.load_takyon_env", lambda: None)
    monkeypatch.setattr("plugins.takyon.cli.TakyonStore", FakeStore)
    monkeypatch.setattr("plugins.takyon.cli._resolved_operator_user_id", lambda _operator_user_id=None: "user-1")

    run_takyon_command(["delete", "roomremix", "--confirm", "--no-domains"])
    run_takyon_command(["delete", "roomremix", "--confirm", "--no-domains"])

    assert len(captured) == 2
    assert captured[0] != captured[1]
