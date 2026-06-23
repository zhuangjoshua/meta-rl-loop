"""Per-session operator identity resolution for TUI gateway sessions.

The resolution chain (tui_gateway.server._resolve_session_operator_user_id) is the dashboard
plane's ONLY identity source order: authenticated transport principal, then the server-injected
session param, then per-session TAKYON_SESSION_USER_ID, then — strictly on planes that have NOT
declared per-session identity — the legacy process-global TAKYON_OPERATOR_USER_ID convenience.
"""

from __future__ import annotations

import importlib
import types


def _server():
    return importlib.import_module("tui_gateway.server")


def _bare_transport():
    return types.SimpleNamespace(operator_principal=None)


def test_transport_principal_beats_client_param(monkeypatch):
    """A ws client cannot pick its principal: the authenticated transport principal always wins
    over a request param."""
    server = _server()
    transport = types.SimpleNamespace(
        operator_principal=types.SimpleNamespace(user_id="auth-user")
    )
    resolved = server._resolve_session_operator_user_id(
        {"_takyon_operator_user_id": "attacker-chosen"}, transport
    )
    assert resolved == "auth-user"


def test_stale_session_is_lazily_bound_from_transport_principal(monkeypatch):
    server = _server()
    session = {
        "takyon_store": object(),
        "transport": types.SimpleNamespace(
            operator_principal=types.SimpleNamespace(user_id="auth-user")
        ),
    }

    assert server._bind_takyon_operator_user_id(session, {}) == "auth-user"
    assert session["takyon_operator_user_id"] == "auth-user"
    assert "takyon_store" not in session


def test_param_then_session_env_resolution(monkeypatch):
    server = _server()
    from gateway.session_context import clear_session_vars, set_session_vars

    # No transport principal: the (server-injected) param is next.
    assert (
        server._resolve_session_operator_user_id(
            {"_takyon_operator_user_id": "injected-by-server"}, _bare_transport()
        )
        == "injected-by-server"
    )

    # Then the per-session TAKYON_SESSION_USER_ID (PTY child / slash worker env).
    tokens = set_session_vars(user_id="session-user")
    try:
        assert (
            server._resolve_session_operator_user_id({}, _bare_transport())
            == "session-user"
        )
    finally:
        clear_session_vars(tokens)


def test_legacy_global_env_is_gated_by_identity_mode(monkeypatch):
    """Process-global TAKYON_OPERATOR_USER_ID satisfies identity ONLY where no per-session plane
    is declared; under warn/enforce it is never an identity source."""
    server = _server()
    from gateway.session_context import clear_session_vars, set_session_vars

    monkeypatch.setenv("TAKYON_OPERATOR_USER_ID", "global-operator")
    monkeypatch.delenv("TAKYON_REQUIRE_OPERATOR_IDENTITY", raising=False)
    # Pin the session contextvar to explicit-empty so the legacy-env step is what resolves.
    tokens = set_session_vars(user_id="")
    try:
        assert (
            server._resolve_session_operator_user_id({}, _bare_transport())
            == "global-operator"
        )
        for mode in ("warn", "enforce"):
            monkeypatch.setenv("TAKYON_REQUIRE_OPERATOR_IDENTITY", mode)
            assert server._resolve_session_operator_user_id({}, _bare_transport()) == ""
    finally:
        clear_session_vars(tokens)
