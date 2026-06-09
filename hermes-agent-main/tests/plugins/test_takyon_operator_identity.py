"""Per-session operator identity at the store layer: mode parsing, identity-source gating in
TakyonStore.__init__, the cli-side resolver, and the narrow system-plane exemption. These are
constructor/gate tests — no database is touched (conn=None call sites prove the short-circuits)."""

from __future__ import annotations

import types

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
