"""Per-session operator identity at the store layer: mode parsing, identity-source gating in
TakyonStore.__init__, the cli-side resolver, and the narrow system-plane exemption. These are
constructor/gate tests — no database is touched (conn=None call sites prove the short-circuits)."""

from __future__ import annotations

import json
import types
from contextlib import contextmanager

import pytest

from plugins.takyon import core as takyon_core
from plugins.takyon.cli import _resolved_operator_user_id


def test_operator_identity_mode_parses_stages(monkeypatch):
    monkeypatch.delenv("TAKYON_REQUIRE_OPERATOR_IDENTITY", raising=False)
    assert takyon_core.operator_identity_mode() == ""
    for raw in ("1", "true", "yes", "on", "enforce"):
        monkeypatch.setenv("TAKYON_REQUIRE_OPERATOR_IDENTITY", raw)
        assert takyon_core.operator_identity_mode() == "enforce"
    monkeypatch.setenv("TAKYON_REQUIRE_OPERATOR_IDENTITY", "warn")
    assert takyon_core.operator_identity_mode() == "warn"


def test_store_ignores_process_global_operator_env_on_per_session_planes(tmp_path, monkeypatch):
    """The legacy process-global TAKYON_OPERATOR_USER_ID can never satisfy identity on a plane
    that declared per-session identity (warn/enforce); the per-session TAKYON_SESSION_USER_ID and
    the explicit constructor argument remain the session sources."""
    from gateway.session_context import clear_session_vars, set_session_vars

    monkeypatch.setenv("TAKYON_OPERATOR_USER_ID", "global-operator")
    # Pin the session contextvar to explicit-empty so the env-fallback step under test is the
    # legacy global var, deterministically.
    tokens = set_session_vars(user_id="")
    try:
        monkeypatch.delenv("TAKYON_REQUIRE_OPERATOR_IDENTITY", raising=False)
        assert (
            takyon_core.TakyonStore(root=tmp_path)._operator_user_id == "global-operator"
        )
        for mode in ("warn", "enforce"):
            monkeypatch.setenv("TAKYON_REQUIRE_OPERATOR_IDENTITY", mode)
            assert takyon_core.TakyonStore(root=tmp_path)._operator_user_id == ""
    finally:
        clear_session_vars(tokens)

    # Per-session sources keep working under enforce.
    monkeypatch.setenv("TAKYON_REQUIRE_OPERATOR_IDENTITY", "enforce")
    tokens = set_session_vars(user_id="session-user")
    try:
        assert takyon_core.TakyonStore(root=tmp_path)._operator_user_id == "session-user"
    finally:
        clear_session_vars(tokens)
    assert (
        takyon_core.TakyonStore(root=tmp_path, operator_user_id="arg-user")._operator_user_id
        == "arg-user"
    )


def test_cli_resolver_prefers_session_env_and_gates_legacy_env(monkeypatch):
    monkeypatch.setenv("TAKYON_OPERATOR_USER_ID", "global-operator")
    monkeypatch.setenv("TAKYON_SESSION_USER_ID", "session-user")
    monkeypatch.delenv("TAKYON_REQUIRE_OPERATOR_IDENTITY", raising=False)
    assert _resolved_operator_user_id("explicit-user") == "explicit-user"
    assert _resolved_operator_user_id() == "session-user"
    monkeypatch.delenv("TAKYON_SESSION_USER_ID", raising=False)
    assert _resolved_operator_user_id() == "global-operator"
    monkeypatch.setenv("TAKYON_REQUIRE_OPERATOR_IDENTITY", "enforce")
    assert _resolved_operator_user_id() == ""


def test_system_plane_is_narrow_and_code_only(tmp_path, monkeypatch):
    """A declared system serving surface (TakyonStore(system_plane=...)) is exempt ONLY from the
    missing-principal gate; a principal-bound store is still owner-checked, and ordinary stores
    under enforce are still denied."""
    monkeypatch.setenv("TAKYON_REQUIRE_OPERATOR_IDENTITY", "enforce")
    from gateway.session_context import clear_session_vars, set_session_vars

    tokens = set_session_vars(user_id="")
    try:
        system_store = takyon_core.TakyonStore(root=tmp_path, system_plane="product-serving")
        # conn=None proves the exemption short-circuits before any DB access.
        assert system_store._enforce_operator_business_access(None, "acme") is None

        plain_store = takyon_core.TakyonStore(root=tmp_path)
        with pytest.raises(takyon_core.TakyonError, match="operator identity required"):
            plain_store._enforce_operator_business_access(None, "acme")

        # system_plane does NOT bypass the owner check when a principal exists.
        bound_store = takyon_core.TakyonStore(
            root=tmp_path, operator_user_id="user-1", system_plane="product-serving"
        )
        conn = types.SimpleNamespace(
            execute=lambda *a, **k: types.SimpleNamespace(fetchone=lambda: None)
        )
        with pytest.raises(takyon_core.TakyonError, match="does not exist"):
            bound_store._enforce_operator_business_access(conn, "acme")
    finally:
        clear_session_vars(tokens)


def test_worker_tool_handler_binds_workspace_root_into_session(monkeypatch):
    from plugins.takyon import worker as takyon_worker

    observed = {}

    @contextmanager
    def _workspace(slug, *, operator_user_id=None, sync_on_exception=False):
        observed["workspace"] = {
            "slug": slug,
            "operator_user_id": operator_user_id,
            "sync_on_exception": sync_on_exception,
        }
        yield "/tmp/takyon-worker-test-home"

    def _set_session_vars(**kwargs):
        observed["set_session_vars"] = dict(kwargs)
        return ["token"]

    def _clear_session_vars(tokens):
        observed["clear_session_vars"] = list(tokens)

    @contextmanager
    def _bound_context(*, run_id="", task_kind=""):
        observed["run_id"] = run_id
        observed["task_kind"] = task_kind
        yield

    monkeypatch.setattr(takyon_worker, "_business_owner_user_id", lambda slug: "user-123")
    monkeypatch.setattr(takyon_worker, "_update_work_request", lambda *args, **kwargs: None)
    monkeypatch.setattr("plugins.takyon.turn_runtime._business_workspace_execution_context", _workspace)
    monkeypatch.setattr("gateway.session_context.set_session_vars", _set_session_vars)
    monkeypatch.setattr("gateway.session_context.clear_session_vars", _clear_session_vars)
    monkeypatch.setattr(takyon_core, "_bound_operator_task_context", _bound_context)

    def _tool(_args, **_kw):
        return json.dumps({"success": True, "summary": "ok"})

    job = takyon_worker.Job(
        id="job-ctx",
        business_slug="acme",
        kind="product.surface_refresh",
        status="running",
        idempotency_key="ik-ctx",
        payload={"args": {"business": "acme"}, "work_request_id": "wr-ctx"},
        result=None,
        error=None,
        reserved_billing_entry_id=None,
        attempts=1,
        max_attempts=1,
        locked_by="w1",
        locked_at=None,
        created_at=None,
        updated_at=None,
    )

    outcome = takyon_worker._operator_tool_task_handler(
        job,
        tool_name="business_refresh_product_surface",
        handler_fn=_tool,
    )

    assert observed["workspace"] == {
        "slug": "acme",
        "operator_user_id": "user-123",
        "sync_on_exception": True,
    }
    assert observed["set_session_vars"] == {
        "user_id": "user-123",
        "workspace_root": "/tmp/takyon-worker-test-home",
        "business_slug": "acme",
        "task_kind": "business_refresh_product_surface",
    }
    assert observed["clear_session_vars"] == ["token"]
    assert observed["run_id"] == "wr-ctx"
    assert observed["task_kind"] == "business_refresh_product_surface"
    assert outcome.result == {"status": "completed", "work_request_id": "wr-ctx"}


def test_worker_tool_handler_restores_exact_parent_bootstrap_context(monkeypatch):
    from plugins.takyon import worker as takyon_worker

    observed: dict[str, object] = {}

    @contextmanager
    def _workspace(*_args, **_kwargs):
        yield "/tmp/takyon-worker-parent-context"

    monkeypatch.setattr(takyon_worker, "_business_owner_user_id", lambda _slug: "user-123")
    monkeypatch.setattr(takyon_worker, "_update_work_request", lambda *_a, **_k: None)
    monkeypatch.setattr(
        "plugins.takyon.turn_runtime._business_workspace_execution_context",
        _workspace,
    )
    monkeypatch.setattr("gateway.session_context.set_session_vars", lambda **_k: [])
    monkeypatch.setattr("gateway.session_context.clear_session_vars", lambda *_a, **_k: None)

    def _tool(_args, **_kwargs):
        observed.update(takyon_core._active_operator_task_receipt_context())
        return json.dumps({"success": True})

    job = takyon_worker.Job(
        id="child-job",
        business_slug="acme",
        kind="product.surface_refresh",
        status="running",
        idempotency_key="child-idem",
        payload={
            "args": {"business": "acme"},
            "work_request_id": "child-request",
            "parent_operator_task": {
                "task_kind": "ceo_bootstrap",
                "run_id": "bootstrap-job",
                "attempt": 4,
            },
        },
        result=None,
        error=None,
        reserved_billing_entry_id=None,
        attempts=1,
        max_attempts=1,
        locked_by="worker-1",
        locked_at=None,
        created_at=None,
        updated_at=None,
    )

    outcome = takyon_worker._operator_tool_task_handler(
        job,
        tool_name="business_refresh_product_surface",
        handler_fn=_tool,
    )

    assert observed == {
        "run_id": "bootstrap-job",
        "task_kind": "ceo_bootstrap",
        "attempt": 4,
    }
    assert outcome.result == {"status": "completed", "work_request_id": "child-request"}
