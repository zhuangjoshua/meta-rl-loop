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


def test_business_session_product_deferral_freezes_refresh_surface_true(monkeypatch):
    class _StoreStub:
        _workspace_root_override = "/tmp/takyon-session-workspace"

        def __init__(self):
            self.commits = []

        def commit(self, **kwargs):
            self.commits.append(kwargs)
            return {
                "success": True,
                "results": [{"action": "job.enqueue", "job": "run-1", "worker_job": "wj-1"}],
            }

    store = _StoreStub()
    monkeypatch.setenv("TAKYON_OPERATOR_TASKS_VIA_WORKER", "1")
    monkeypatch.delenv("TAKYON_WORKER_PROCESS", raising=False)
    monkeypatch.setattr(takyon_core, "_store", lambda: store)
    monkeypatch.setattr(takyon_core, "_session_business_slug", lambda: "acme")
    monkeypatch.setattr(takyon_core, "_require_api_access", lambda op, **kw: {})
    monkeypatch.setattr(
        takyon_core,
        "_read_work_request_run",
        lambda _store, _run_id: ("completed", {"result": {"success": True, "summary": "done"}}),
    )
    monkeypatch.setattr(takyon_core, "_WORKER_DEFERRAL_POLL_SECONDS", 0.0)

    raw = takyon_core._defer_claude_agent_task_to_worker(
        {
            "business": "acme",
            "instruction": "build",
            "idempotency_key": "task-override",
            "workspace": "product/site",
        }
    )

    result = json.loads(raw)
    assert result["success"] is True
    assert store.commits[0]["operations"][0]["payload"]["args"]["refresh_surface"] is True


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
        kind="claude.agent_task",
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
        tool_name="business_claude_agent_task",
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
        "task_kind": "business_claude_agent_task",
    }
    assert observed["clear_session_vars"] == ["token"]
    assert observed["run_id"] == "wr-ctx"
    assert observed["task_kind"] == "business_claude_agent_task"
    assert outcome.result == {"status": "completed", "work_request_id": "wr-ctx"}
