"""Autonomous-wake product/destructive-op ban (Roomier incident fix).

Proves the wake-turn marker gates product + sibling handlers WITHOUT touching bootstrap or chat:
- under task_kind="ceo_wake"  -> refused (the ban)
- under task_kind="ceo_bootstrap" -> allowed (bootstrap must still build the product 0-shot)
- under no task_kind (chat/cli/tui) -> allowed (explicit operator request is the sanctioned path)

The guard is read from the ContextVar (in-process) with a session-env fallback; setting the
ContextVar here drives the predicate the same way worker.py's ceo_wake_handler does at runtime.
"""
import pytest

from plugins.takyon import core
from plugins.takyon.core import TakyonError


def _with_task_kind(kind: str):
    """Bind the active task kind like worker.py does, returning a reset token."""
    return core._ACTIVE_OPERATOR_TASK_KIND.set(kind)


def test_predicate_only_true_for_wake():
    tok = _with_task_kind("ceo_wake")
    try:
        assert core._is_autonomous_wake_turn() is True
    finally:
        core._ACTIVE_OPERATOR_TASK_KIND.reset(tok)
    for kind in ("ceo_bootstrap", "", "claude.agent_task"):
        tok = _with_task_kind(kind)
        try:
            assert core._is_autonomous_wake_turn() is False, kind
        finally:
            core._ACTIVE_OPERATOR_TASK_KIND.reset(tok)


def test_refuse_helper_raises_only_on_wake():
    tok = _with_task_kind("ceo_wake")
    try:
        with pytest.raises(TakyonError):
            core._refuse_on_autonomous_wake("product edits")
    finally:
        core._ACTIVE_OPERATOR_TASK_KIND.reset(tok)
    for kind in ("ceo_bootstrap", ""):
        tok = _with_task_kind(kind)
        try:
            core._refuse_on_autonomous_wake("product edits")  # must not raise
        finally:
            core._ACTIVE_OPERATOR_TASK_KIND.reset(tok)


def test_product_file_edit_guard_is_path_scoped():
    tok = _with_task_kind("ceo_wake")
    try:
        # product SOURCE is blocked on a wake, including case / normalization evasions
        for p in (
            "product/site/src/screens/app-home.tsx",
            "product/site",
            "PRODUCT/site/x.tsx",
            "./product/site/x",
            "product//site/x",
        ):
            with pytest.raises(TakyonError):
                core._refuse_product_file_edit_on_autonomous_wake(p)
        # research/metrics/memory AND distribution/creative receipts+assets under product/ stay allowed
        for p in (
            "research/strategy.md",
            "metrics/wake-history.md",
            "product/public-assets/demo/receipt.json",
            "product/brand/logos/demo/receipt.json",
            "product/static-ads/demo/run.json",
            "product/surface.md",
            "",
        ):
            core._refuse_product_file_edit_on_autonomous_wake(p)  # must not raise
    finally:
        core._ACTIVE_OPERATOR_TASK_KIND.reset(tok)


@pytest.mark.parametrize(
    "handler_name",
    [
        "handle_business_claude_agent_task",
        "handle_business_upsert_app_surface_contract",
        "handle_business_refresh_product_surface",
        "handle_business_write_instant_landing",
        "handle_business_upsert_app_plan",
        "handle_business_grant_app_entitlement",
        "handle_business_set_mode",
        "handle_business_delete_business",
        "handle_business_gc",
        "handle_business_set_control",
        "handle_business_set_channel_credit_budgets",
    ],
)
def test_gated_handlers_refuse_on_wake(handler_name):
    """Each gated handler fails closed on a wake: the refusal is the first thing it does, so it
    surfaces as an error result (raised TakyonError or a tool_error JSON carrying the refusal)."""
    handler = getattr(core, handler_name)
    tok = _with_task_kind("ceo_wake")
    try:
        try:
            out = handler({"business": "demo"})
        except TakyonError as exc:
            assert "autonomous CEO wake" in str(exc)
            return
        # handlers that wrap in try/except return a tool_error JSON instead of raising
        assert "autonomous CEO wake" in str(out)
    finally:
        core._ACTIVE_OPERATOR_TASK_KIND.reset(tok)


def test_product_file_handlers_refuse_product_path_on_wake():
    tok = _with_task_kind("ceo_wake")
    try:
        for handler_name in ("handle_business_write_file", "handle_business_patch_file"):
            handler = getattr(core, handler_name)
            with pytest.raises(TakyonError):
                handler({"business": "demo", "path": "product/site/src/main.tsx", "content": "x", "old": "a", "new": "b"})
        # workspace scaffolding under product/site is blocked too (R1)
        with pytest.raises(TakyonError):
            core.handle_business_create_workspace({"business": "demo", "path": "product/site"})
    finally:
        core._ACTIVE_OPERATOR_TASK_KIND.reset(tok)


def test_upgrade_businesses_apply_refused_on_wake():
    # The APPLY path is a global durable mutator (all businesses when no slug); it must fail closed on
    # a wake. handle_business_upgrade_businesses wraps in try/except, so the refusal returns as a
    # tool_error JSON carrying the message rather than raising.
    tok = _with_task_kind("ceo_wake")
    try:
        out = core.handle_business_upgrade_businesses({"apply": True})
        assert "autonomous CEO wake" in str(out)
    finally:
        core._ACTIVE_OPERATOR_TASK_KIND.reset(tok)
