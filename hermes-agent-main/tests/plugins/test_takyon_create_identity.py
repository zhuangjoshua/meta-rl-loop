import pytest
import types
import uuid
from pathlib import Path

from plugins.takyon.bootstrap_phases import (
    PHASE_MAX_TURNS,
    BootstrapPhaseRun,
    bootstrap_phase_idempotency,
    phase_prompt,
)

from plugins.takyon.cli import (
    TakyonError,
    _business_bootstrap_instruction,
    _ceo_bootstrap_turn_config,
    _derive_name_from_goal_with_llm,
    _resolve_create_identity,
    _resolve_dashboard_create_identity,
    run_takyon_command,
)


def _checkpointed_phase_prompt(phase: str, *, workflow: bool = False) -> str:
    job_id = str(uuid.uuid4())
    run = BootstrapPhaseRun(
        job_id=job_id,
        sdk_session_id=job_id,
        owner_user_id=str(uuid.uuid4()),
        business_slug="quoteforge" if workflow else "crm",
        immutable_inputs={
            "goal": (
                "Build a service that turns messy notes into a contractor-ready quote request."
                if workflow
                else "construction CRM"
            ),
            "business_name": "QuoteForge" if workflow else "CRM",
            "workflow_requested": workflow,
            "archetype": "web_app",
        },
        phase_idempotency=bootstrap_phase_idempotency(job_id),
        current_phase=phase,
        completed_phases=(),
        phase_evidence={},
        phase_receipts={},
        phase_attempts={},
        status="running",
    )
    return phase_prompt(
        run,
        phase,
        public_site_url=f"https://{run.business_slug}.coscale.app/",
        animations=False,
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
    assert "Treat the canonical business name above as the owner/account name." in prompt
    assert "choose ONE short human product display name" in prompt
    assert "Do not invent a second competing brand." in prompt


def test_bootstrap_prompt_requires_bold_landing_and_branded_appkit_auth_surface():
    landing = _checkpointed_phase_prompt("landing_build_publish")
    final = _checkpointed_phase_prompt("final_workflow_build_publish")

    assert "Invoke takyon-product and design-taste-frontend" in landing
    assert "preserving PublicSiteHeader" in landing
    assert "Do not customize app-layout.tsx, app-home.tsx, or profile.tsx yet." in landing
    assert "Customize app-home.tsx and profile.tsx while preserving app-layout.tsx" in final
    assert "landing, auth, checkout, profile, and support rails" in final


def test_bootstrap_prompt_folds_workflow_build_into_second_pass_and_verifies_real_action():
    prompt = _checkpointed_phase_prompt("final_workflow_build_publish", workflow=True)

    assert "real product/site/actions/*.ts generation" in prompt
    assert "useDecodedActionRunner UI wiring" in prompt
    assert "records persistence/reopen" in prompt
    assert "Do not invoke the app action" in prompt
    assert "business_claude_agent_task" not in prompt


def test_bootstrap_prompt_trusts_authoritative_product_result_and_stops_on_blocker():
    prompt = _checkpointed_phase_prompt("landing_build_publish")

    assert "require structured publish.status published plus a real public_url" in prompt
    assert "pre-publication build, type, action, or path validation failure" in prompt
    assert "retry once with that same exact key" in prompt
    assert "publication or activation is blocked or ambiguous" in prompt
    assert "record the exact blocker in research/strategy.md and stop" in prompt
    assert "Never fake a receipt" in prompt
    assert "X" not in prompt


def test_bootstrap_prompt_passes_explicit_search_console_site_url():
    prompt = _business_bootstrap_instruction(
        "roomviewer-2",
        "room viewer",
        "live",
        business_name="Roomviewer 2",
    )

    assert 'Call business_register_search_console with the business, site_url "https://roomviewer-2.coscale.app/"' in prompt
    assert "Do not rely on inferred public_url here." in prompt
    assert "business_seo_add_property" not in prompt


def test_bootstrap_turn_config_uses_expanded_shared_turn_budget():
    config = _ceo_bootstrap_turn_config(
        "crm",
        "construction CRM",
        "live",
        business_name="CRM",
    )

    assert config["max_turns"] == 90
    assert PHASE_MAX_TURNS["final_workflow_build_publish"] == 60


def test_ceo_prompt_rule_8_stops_redelegation_on_blocked_authority_violation():
    prompt_path = Path(__file__).resolve().parents[2] / "plugins" / "takyon" / "prompts" / "ceo.md"
    text = prompt_path.read_text(encoding="utf-8")

    assert "You are the single primary model agent" in text
    assert "Never spawn or delegate to another model agent" in text
    assert "business_claude_agent_task" not in text
    assert "automatic local source/build repair retry" not in text


def test_interactive_only_rules_are_suppressed_on_bootstrap():
    from plugins.takyon.cli import _load_ceo_prompt

    anchor = "inspect what you actually built against what the operator asked for"
    proof_rule = "Do not call a product/runtime feature wired, done, published"

    # Interactive/cron turns share the full ceo.md verbatim → the completion-discipline rule
    # and the always-on proof rule are both present.
    full = _load_ceo_prompt()
    assert anchor in full
    assert proof_rule in full
    assert "Each call you make is one conversational message" in full

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
    assert "Each call you make is one conversational message" not in bootstrap_prompt
    assert "MODEL-OWNED-OPERATOR-UPDATES" not in bootstrap_prompt
    assert "Customer milestone updates are runtime-owned" in bootstrap_prompt
    assert "intentionally unavailable to bootstrap phase queries" in bootstrap_prompt


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
