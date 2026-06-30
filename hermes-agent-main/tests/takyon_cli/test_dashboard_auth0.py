"""Tests for the optional Auth0 dashboard gate."""

from __future__ import annotations

import urllib.parse
import time

import pytest
from fastapi.testclient import TestClient


HOST = "app.fourmanifold.com"


@pytest.fixture
def auth0_env(monkeypatch):
    from takyon_cli import web_server as ws

    monkeypatch.setenv("AUTH0_DOMAIN", "fourmanifold.auth0.com")
    monkeypatch.setenv("AUTH0_CLIENT_ID", "client-id")
    monkeypatch.setenv("AUTH0_CLIENT_SECRET", "client-secret")
    monkeypatch.setenv("AUTH0_SECRET", "cookie-signing-secret")
    monkeypatch.setenv("APP_BASE_URL", f"https://{HOST}")
    monkeypatch.setenv("ARGON_BETA_ALLOWED_EMAIL_DOMAINS", "fourmanifold.com")
    monkeypatch.setenv("TAKYON_HOST_ROLE", "safebox")
    monkeypatch.delenv("TAKYON_SAFEBOX_URL", raising=False)
    ws._clear_auth0_config_cache()
    ws.app.state.bound_host = "127.0.0.1"
    try:
        yield ws
    finally:
        if hasattr(ws.app.state, "bound_host"):
            del ws.app.state.bound_host
        ws._clear_auth0_config_cache()


def _client(ws) -> TestClient:
    return TestClient(ws.app, base_url=f"https://{HOST}")


def _state_from_login(resp) -> str:
    location = resp.headers["location"]
    parsed = urllib.parse.urlparse(location)
    assert parsed.scheme == "https"
    assert parsed.netloc == "fourmanifold.auth0.com"
    return urllib.parse.parse_qs(parsed.query)["state"][0]


def _signed_session(user: dict[str, object]) -> str:
    from plugins.takyon import safebox

    return safebox._auth0_sign_payload(  # type: ignore[attr-defined]
        "cookie-signing-secret",
        {**user, "iat": int(time.time()), "exp": int(time.time()) + 3600},
    )


def test_public_app_host_redirects_spa_to_auth0_login(auth0_env):
    client = _client(auth0_env)

    resp = client.get("/sessions", follow_redirects=False)

    assert resp.status_code == 302
    assert resp.headers["location"].startswith("/auth/login?")


def test_public_app_host_redirects_root_to_auth0_login(auth0_env):
    client = _client(auth0_env)

    resp = client.get("/", follow_redirects=False)

    assert resp.status_code == 302
    assert resp.headers["location"].startswith("/auth/login?")


def test_localhost_dashboard_is_not_forced_through_auth0(auth0_env):
    client = TestClient(auth0_env.app, base_url="http://localhost:9119")

    resp = client.get("/api/status")

    assert resp.status_code == 200


def test_app_host_protects_public_status_endpoint_when_auth0_applies(auth0_env):
    client = _client(auth0_env)

    resp = client.get("/api/status")

    assert resp.status_code == 401
    assert resp.json()["detail"] == "Auth0 login required"


def test_product_tls_ask_bypasses_auth0_for_caddy(auth0_env, tmp_path, monkeypatch, pg_store_dsn):
    from plugins.takyon.core import TakyonStore

    monkeypatch.setenv("DATABASE_URL", pg_store_dsn)
    monkeypatch.setenv("TAKYON_PLATFORM_OWNER_SUB", "auth0|dashboard-auth0")
    monkeypatch.setattr(auth0_env, "get_takyon_home", lambda: tmp_path)
    store = TakyonStore(tmp_path, database_url=pg_store_dsn)
    store.seed_platform_owner()
    store.commit(
        scope="business:latexflow",
        operations=[{"action": "business.upsert", "business": "latexflow", "name": "Latexflow"}],
        idempotency_key="auth0-tls-ask-business",
        reason="test",
        actor="test",
    )
    client = _client(auth0_env)

    resp = client.get("/api/product-tls/ask?domain=latexflow.fourmanifold.com")

    assert resp.status_code == 200


def test_auth0_login_redirect_uses_current_app_base_url(auth0_env):
    client = _client(auth0_env)

    resp = client.get("/auth/login?return_to=/chat", follow_redirects=False)

    assert resp.status_code == 302
    location = resp.headers["location"]
    parsed = urllib.parse.urlparse(location)
    query = urllib.parse.parse_qs(parsed.query)
    assert parsed.netloc == "fourmanifold.auth0.com"
    assert query["client_id"] == ["client-id"]
    assert query["redirect_uri"] == [f"https://{HOST}/auth/callback"]
    assert query["scope"] == ["openid profile email"]
    assert "takyon_auth0_state" in resp.headers["set-cookie"]
    assert "takyon_auth0_nonce" in resp.headers["set-cookie"]


def test_auth0_callback_sets_dashboard_session_for_fourmanifold_email(
    auth0_env,
    monkeypatch,
):
    client = _client(auth0_env)
    login = client.get("/auth/login?return_to=/chat", follow_redirects=False)
    state = _state_from_login(login)

    user = {
        "sub": "auth0|1",
        "email": "operator@fourmanifold.com",
        "email_verified": True,
        "name": "Operator",
    }

    async def fake_complete(cfg, *, code, state, state_token, nonce_token, redirect_uri):
        assert code == "ok"
        assert state
        assert state_token
        assert nonce_token
        assert redirect_uri == f"https://{HOST}/auth/callback"
        return {
            "user": user,
            "session_token": _signed_session(user),
            "return_to": "/chat",
        }

    monkeypatch.setattr(auth0_env, "_auth0_complete_callback", fake_complete)

    resp = client.get(
        f"/auth/callback?code=ok&state={urllib.parse.quote(state)}",
        follow_redirects=False,
    )

    assert resp.status_code == 302
    assert resp.headers["location"] == "/chat"
    assert "takyon_dashboard_auth" in resp.headers["set-cookie"]

    status = client.get("/api/status")
    assert status.status_code == 200


def test_public_app_host_authenticated_html_omits_session_token(auth0_env):
    client = _client(auth0_env)
    client.cookies.set(
        "takyon_dashboard_auth",
        _signed_session(
            {
                "sub": "auth0|html",
                "email": "operator@fourmanifold.com",
                "email_verified": True,
                "name": "Operator Html",
            }
        ),
    )

    resp = client.get("/chat")

    assert resp.status_code == 200
    assert "__TAKYON_SESSION_TOKEN__" not in resp.text


def test_public_app_host_shared_session_header_cannot_access_local_only_admin_api(auth0_env):
    client = _client(auth0_env)

    resp = client.get(
        "/api/env",
        headers={"X-Takyon-Session-Token": auth0_env._SESSION_TOKEN},
    )

    assert resp.status_code == 404
    assert resp.json()["detail"] == "Not Found"


def test_auth0_me_reports_current_dashboard_user(auth0_env, monkeypatch):
    client = _client(auth0_env)
    login = client.get("/auth/login?return_to=/chat", follow_redirects=False)
    state = _state_from_login(login)

    async def fake_complete(cfg, *, code, state, state_token, nonce_token, redirect_uri):
        user = {
            "sub": "auth0|me",
            "email": "operator@fourmanifold.com",
            "email_verified": True,
            "name": "Operator Me",
        }
        return {
            "user": user,
            "session_token": _signed_session(user),
            "return_to": "/chat",
        }

    monkeypatch.setattr(auth0_env, "_auth0_complete_callback", fake_complete)

    resp = client.get(f"/auth/callback?code=ok&state={state}", follow_redirects=False)

    assert resp.status_code == 302
    me = client.get("/auth/me")
    assert me.status_code == 200
    assert me.json() == {
        "authenticated": True,
        "auth0_required": True,
        "user": {
            "email": "operator@fourmanifold.com",
            "name": "Operator Me",
            "sub": "auth0|me",
        },
    }


def test_auth0_logout_clears_dashboard_session(auth0_env, monkeypatch):
    client = _client(auth0_env)
    login = client.get("/auth/login?return_to=/chat", follow_redirects=False)
    state = _state_from_login(login)

    async def fake_complete(cfg, *, code, state, state_token, nonce_token, redirect_uri):
        user = {
            "sub": "auth0|logout",
            "email": "operator@fourmanifold.com",
            "email_verified": True,
            "name": "Operator Logout",
        }
        return {
            "user": user,
            "session_token": _signed_session(user),
            "return_to": "/chat",
        }

    monkeypatch.setattr(auth0_env, "_auth0_complete_callback", fake_complete)

    resp = client.get(f"/auth/callback?code=ok&state={state}", follow_redirects=False)
    assert resp.status_code == 302
    assert client.get("/api/status").status_code == 200

    logout = client.get("/auth/logout?return_to=/chat", follow_redirects=False)

    assert logout.status_code == 302
    assert logout.headers["location"].startswith("https://fourmanifold.auth0.com/v2/logout?")
    assert client.get("/api/status").status_code == 401


def test_auth0_callback_rejects_non_fourmanifold_email(auth0_env, monkeypatch):
    client = _client(auth0_env)
    login = client.get("/auth/login", follow_redirects=False)
    state = _state_from_login(login)

    async def fake_complete(cfg, *, code, state, state_token, nonce_token, redirect_uri):
        raise auth0_env.Auth0ConfigError("someone@example.com is not allowed for this dashboard")

    monkeypatch.setattr(auth0_env, "_auth0_complete_callback", fake_complete)

    resp = client.get(f"/auth/callback?code=ok&state={state}")

    assert resp.status_code == 403
    assert "not allowed" in resp.text


def test_auth0_callback_rejects_unverified_email(auth0_env, monkeypatch):
    client = _client(auth0_env)
    login = client.get("/auth/login", follow_redirects=False)
    state = _state_from_login(login)

    async def fake_complete(cfg, *, code, state, state_token, nonce_token, redirect_uri):
        raise auth0_env.Auth0ConfigError("Auth0 email address is not verified")

    monkeypatch.setattr(auth0_env, "_auth0_complete_callback", fake_complete)

    resp = client.get(f"/auth/callback?code=ok&state={state}")

    assert resp.status_code == 403
    assert "not verified" in resp.text


def test_auth0_allowed_identities_label_reports_email_allowlist(auth0_env):
    cfg = auth0_env.Auth0DashboardConfig(
        domain="https://fourmanifold.auth0.com",
        client_id="client-id",
        base_url=f"https://{HOST}",
        allowed_domains=(),
        allowed_emails=("jmzworkhub@gmail.com",),
        force=False,
    )

    assert auth0_env._auth0_allowed_identities_label(cfg) == "emails: jmzworkhub@gmail.com"


def test_auth0_allowed_identities_label_reports_email_and_domain_allowlists(auth0_env):
    cfg = auth0_env.Auth0DashboardConfig(
        domain="https://fourmanifold.auth0.com",
        client_id="client-id",
        base_url=f"https://{HOST}",
        allowed_domains=("fourmanifold.com",),
        allowed_emails=("jmzworkhub@gmail.com",),
        force=False,
    )

    assert auth0_env._auth0_allowed_identities_label(cfg) == (
        "emails: jmzworkhub@gmail.com; domains: fourmanifold.com"
    )


def test_configured_public_host_is_accepted_for_reverse_proxy(auth0_env):
    assert auth0_env._is_accepted_host(HOST, "127.0.0.1")
    assert not auth0_env._is_accepted_host("evil.example", "127.0.0.1")
