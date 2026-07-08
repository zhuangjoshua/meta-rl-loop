"""Unit tests for the app AI gateway's plan model gate (`_model_allowed`).

No DB, no runtime app — these pin the 2026-07-08 provider-split regression at
the function level so they run everywhere (the PG-backed gateway suite needs
the full local rig and is env-gated).

Incident: the platform app default model moved (TAKYON_APP_OPENAI_MODEL
introduced) while 219 live plans still pinned metadata.model_allowlist =
["claude-sonnet-4-6"]. Every ctx.generate call (which never names a model)
resolved to the new default and 403'd model_not_in_plan — observed as
AIpeekaboo's run-visibility action failing with `model_not_in_plan`.

Contract: the plan allowlist gates CLIENT-chosen models; the platform's own
configured default models are always usable (pricing + reserve/settle money
gates still meter them).
"""
from types import SimpleNamespace
from unittest.mock import patch

from plugins.takyon.ai_gateway import _model_allowed, _platform_default_models


def _plan(allowlist):
    return SimpleNamespace(metadata={"model_allowlist": allowlist})


def test_platform_default_models_resolve_env_overrides(monkeypatch):
    monkeypatch.setenv("TAKYON_APP_OPENAI_MODEL", "gpt-5.4-mini")
    monkeypatch.setenv("TAKYON_APP_ANTHROPIC_MODEL", "claude-sonnet-4-6")
    defaults = _platform_default_models()
    assert "gpt-5.4-mini" in defaults
    assert "claude-sonnet-4-6" in defaults


def test_platform_default_model_allowed_despite_stale_allowlist(monkeypatch):
    """THE provider-split strand: default moved to an openai model, plan still
    pins the old anthropic name. The default must stay allowed."""
    monkeypatch.setenv("TAKYON_APP_OPENAI_MODEL", "gpt-5.4-mini")
    plan = _plan(["claude-sonnet-4-6"])
    assert _model_allowed(plan, "gpt-5.4-mini") is True


def test_explicit_model_outside_allowlist_still_refused(monkeypatch):
    monkeypatch.setenv("TAKYON_APP_OPENAI_MODEL", "gpt-5.4-mini")
    plan = _plan(["claude-sonnet-4-6"])
    assert _model_allowed(plan, "claude-forbidden-9") is False
    assert _model_allowed(plan, "gpt-5.5") is False


def test_allowlisted_model_still_allowed(monkeypatch):
    monkeypatch.setenv("TAKYON_APP_OPENAI_MODEL", "gpt-5.4-mini")
    plan = _plan(["claude-sonnet-4-6"])
    assert _model_allowed(plan, "claude-sonnet-4-6") is True


def test_plan_none_refuses_non_default_models(monkeypatch):
    monkeypatch.setenv("TAKYON_APP_OPENAI_MODEL", "gpt-5.4-mini")
    assert _model_allowed(None, "claude-forbidden-9") is False
    # ... but the platform default is the platform's own routing choice.
    assert _model_allowed(None, "gpt-5.4-mini") is True


def test_broken_default_resolver_does_not_block_the_other_lane():
    with patch("plugins.takyon.ai_provider.openai_model", side_effect=RuntimeError):
        defaults = _platform_default_models()
    # anthropic default still resolves
    assert any(m.startswith("claude-") for m in defaults)
