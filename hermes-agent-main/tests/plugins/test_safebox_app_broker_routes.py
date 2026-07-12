"""Safebox broker routes.

Pins that the safebox app still imports + boots with the new action-shaped routes registered, and that
the /v1/token/mint route runs the authoritative two-tier validation and returns a capability token that
verifies back to the SAME scope the safebox derived (mint -> verify roundtrip). The remaining env route
assertion pins the read-only public-config compatibility surface.

No live DB / no live provider: the safebox's own connection is stubbed and the identity reads are
monkeypatched, exactly as the safebox_authz unit test does. The point here is route wiring + the
mint->verify identity roundtrip, not the SECURITY DEFINER ledger (that is exercised in the broker core
test against a FakeLedger)."""
from __future__ import annotations

import base64
import contextlib
import json
import types
import urllib.error

import pytest
from starlette.testclient import TestClient

from plugins.takyon import (
    app_entitlements,
    app_identity,
    app_payments,
    business_credits,
    safebox,
    safebox_app,
    stripe_util,
)
from plugins.takyon.safebox_capability import verify_capability

_SIGNING_KEY = "safebox-only-signing-key-not-on-any-client"
_TOKEN = "secret-internal-token"
_OPERATOR_TOKEN = "operator-route-token-not-on-subuser"
_STRIPE_ACCOUNT_ID = "acct_platform_123"


def _plan_dict(*, price_id: str = "price_123", product_id: str = "prod_123") -> dict:
    return {
        "business_slug": "climblog",
        "plan_key": "monthly",
        "stripe_price_id": price_id,
        "stripe_product_id": product_id,
        "tier": "paid",
        "price_cents": 900,
        "currency": "usd",
        "billing_interval": "month",
        "included_ai_budget_microusd": 5_000_000,
        "included_action_quota": 0,
        "metadata": {},
        "saleable": True,
        "business_mode": "live",
    }


def _checkout_branding(*, overrides: dict | None = None) -> dict:
    params = {
        "branding_settings[display_name]": "Climb Log",
        "branding_settings[background_color]": "#fafafa",
        "branding_settings[button_color]": "#5b21b6",
        "branding_settings[border_style]": "rounded",
        "branding_settings[logo][type]": "url",
        "branding_settings[logo][url]": "https://climblog.coscale.app/brand-logo.png",
        "line_items[0][price_data][product_data][images][0]": (
            "https://climblog.coscale.app/brand-logo.png"
        ),
    }
    params.update(overrides or {})
    return {
        "schema": "takyon.stripe.checkout_branding.v1",
        "source_build_id": "build-brand-v1",
        "fingerprint": "a" * 64,
        "params": params,
    }


def _checkout_row(*, branding: dict | None = None):
    plan = _plan_dict()
    return (
        "00000000-0000-0000-0000-000000000123",
        "cust_X",
        "customer@example.com",
        "client_ref_123",
        plan["price_cents"],
        plan["currency"],
        plan["billing_interval"],
        plan["tier"],
        plan["included_ai_budget_microusd"],
        plan["included_action_quota"],
        plan["metadata"],
        plan["business_mode"],
        {} if branding is None else branding,
    )


def _catalog_row(*, price_id: str = "price_123", product_id: str = "prod_123"):
    plan = _plan_dict(price_id=price_id, product_id=product_id)
    return (
        plan["stripe_price_id"], plan["stripe_product_id"], plan["tier"], plan["price_cents"],
        plan["currency"], plan["billing_interval"], plan["included_ai_budget_microusd"],
        plan["included_action_quota"], plan["metadata"], plan["saleable"], plan["business_mode"],
    )


def _catalog_metadata() -> dict[str, str]:
    return safebox_app._plan_stripe_metadata(_plan_dict(), account_id=_STRIPE_ACCOUNT_ID)


def _price_object(*, price_id: str = "price_123", overrides: dict | None = None) -> dict:
    value = {
        "id": price_id,
        "object": "price",
        "active": True,
        "livemode": True,
        "type": "recurring",
        "currency": "usd",
        "unit_amount": 900,
        "recurring": {"interval": "month", "interval_count": 1},
        "product": "prod_123",
        "metadata": _catalog_metadata(),
    }
    value.update(overrides or {})
    return value


def _product_object(*, overrides: dict | None = None) -> dict:
    value = {
        "id": "prod_123", "object": "product", "active": True, "livemode": True,
        "metadata": _catalog_metadata(),
    }
    value.update(overrides or {})
    return value


def _checkout_session_object(
    params: dict,
    *,
    session_id: str = "cs_live_123",
    livemode: bool = True,
) -> dict:
    metadata = {
        key[len("metadata[") : -1]: str(value)
        for key, value in params.items()
        if str(key).startswith("metadata[") and str(key).endswith("]")
    }
    return {
        "id": session_id,
        "object": "checkout.session",
        "livemode": livemode,
        "mode": "subscription",
        "url": f"https://checkout.stripe.com/c/pay/{session_id}",
        "client_reference_id": params.get("client_reference_id"),
        "metadata": metadata,
    }


class _OwnerCursor:
    def __init__(self, row):
        self._row = row

    def fetchone(self):
        return self._row


class _OwnerConn:
    """Fake safebox conn for ownership and verified Auth0 user lookups."""

    def __init__(self, owner):
        self._owner = owner

    def execute(self, sql, params=None):
        if "from users where auth0_sub" in str(sql):
            return _OwnerCursor((self._owner,))
        return _OwnerCursor({"owner_user_id": self._owner})


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv(safebox_app._SAFEBOX_TOKEN_ENV, _TOKEN)
    monkeypatch.setenv(safebox_app._CAP_SIGNING_KEY_ENV, _SIGNING_KEY)
    monkeypatch.setenv(safebox_app._OPERATOR_TOKEN_ENV, _OPERATOR_TOKEN)
    monkeypatch.setenv(safebox_app._OPERATOR_CLIENTS_ENV, "testclient")
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_live_unit_test")
    monkeypatch.setenv("TAKYON_STRIPE_ACCOUNT_ID", _STRIPE_ACCOUNT_ID)
    monkeypatch.setattr(safebox_app, "_stripe_key_livemode", lambda: True)

    # Stub the safebox's own DB connection so authorize_*_call can resolve the business owner without a
    # live Postgres. yield the same fake conn the authz reads run against.
    @contextlib.contextmanager
    def _fake_conn():
        yield _OwnerConn("user_A")

    monkeypatch.setattr(safebox_app, "_safebox_db_conn", _fake_conn)
    return TestClient(safebox_app.build_safebox_app())


def _auth(*, operator: bool = True):
    headers = {"Authorization": f"Bearer {_TOKEN}"}
    if operator:
        headers["X-Takyon-Operator-Token"] = _OPERATOR_TOKEN
    return headers


def test_app_imports_and_boots(client):
    # Boot smoke: the app constructs and answers healthz with the new routes registered.
    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_stripe_account_proof_returns_no_secret(client, monkeypatch):
    monkeypatch.setattr(
        safebox_app,
        "_stripe_account_snapshot",
        lambda: (_STRIPE_ACCOUNT_ID, True),
    )

    response = client.get("/v1/stripe/account-proof", headers=_auth(operator=False))

    assert response.status_code == 200
    assert response.json() == {"account_id": _STRIPE_ACCOUNT_ID, "livemode": True}
    assert "key" not in response.text.lower()


def test_new_routes_are_registered_alongside_env_routes():
    app = safebox_app.build_safebox_app()
    paths = {route.path for route in app.routes}
    # New action-shaped routes are mounted...
    assert "/v1/token/mint" in paths
    assert "/v1/providers/anthropic/messages" in paths
    assert "/v1/providers/openai/messages" in paths
    assert "/v1/providers/tavily/search" in paths
    assert "/v1/providers/gemini/image" in paths
    assert "/v1/providers/postmark/send" in paths
    assert "/v1/app-media/put" in paths
    assert "/v1/app-media/get" in paths
    assert "/v1/gsc/add-property" in paths
    assert "/v1/app-media/delete" in paths
    # ...and the public-config env read compatibility route remains mounted.
    assert "/v1/env/{key}" in paths


def test_analytics_umami_forward_route_registered():
    app = safebox_app.build_safebox_app()
    paths = {route.path for route in app.routes}
    assert "/v1/analytics/umami/forward" in paths


def test_analytics_umami_forward_requires_operator_route_token(client):
    # Account-scoped analytics key => operator-gated broker, like GSC/Composio. The shared transport
    # token alone is not enough.
    resp = client.post(
        "/v1/analytics/umami/forward",
        headers=_auth(operator=False),
        json={"path": "websites/WID/stats", "params": {"hostname": "demo.coscale.app"}},
    )
    assert resp.status_code == 401
    assert resp.json()["detail"] == "operator_unauthorized"


def test_analytics_umami_forward_rejects_non_stats_path(client, monkeypatch):
    from plugins.takyon import umami_util

    monkeypatch.setattr(umami_util, "umami_request", lambda *a, **k: pytest.fail("must not reach upstream"))
    for bad in ("websites", "websites/WID", "websites/WID/reset", "teams", "../secrets"):
        resp = client.post(
            "/v1/analytics/umami/forward",
            headers=_auth(),
            json={"path": bad, "params": {}},
        )
        assert resp.status_code == 400, f"{bad!r} -> {resp.text}"
        assert resp.json()["detail"] == "umami_path_not_allowed"


def test_analytics_umami_forward_returns_key_free_stats(client, monkeypatch):
    from plugins.takyon import umami_util

    captured: dict[str, object] = {}

    def fake_request(path, params, api_endpoint, *, timeout=20):
        captured.update({"path": path, "params": params, "api_endpoint": api_endpoint})
        return {"pageviews": 58, "visitors": 4, "visits": 10, "bounces": 4, "totaltime": 4496}

    monkeypatch.setattr(umami_util, "umami_request", fake_request)
    resp = client.post(
        "/v1/analytics/umami/forward",
        headers=_auth(),
        json={
            "path": "websites/WID/stats",
            "params": {"hostname": "aipeekaboo.coscale.app", "startAt": 1, "endAt": 2},
        },
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["visitors"] == 4
    assert captured["path"] == "websites/WID/stats"
    assert captured["params"]["hostname"] == "aipeekaboo.coscale.app"
    # The safebox uses its OWN configured endpoint (caller never supplies the upstream URL).
    assert str(captured["api_endpoint"]).startswith("https://api.umami.is")


def test_analytics_umami_forward_maps_missing_key_to_404(client, monkeypatch):
    from plugins.takyon import umami_util

    def fake_request(*a, **k):
        raise umami_util.UmamiError("Umami analytics read requires UMAMI_API_KEY")

    monkeypatch.setattr(umami_util, "umami_request", fake_request)
    resp = client.post(
        "/v1/analytics/umami/forward",
        headers=_auth(),
        json={"path": "websites/WID/stats", "params": {}},
    )
    assert resp.status_code == 404
    assert resp.json()["detail"] == "umami_unconfigured"


def test_operator_session_token_roundtrips_to_validated_scope(client, monkeypatch):
    # Operator mint: boundary 1 only — the operator must own the business. The owner resolves to
    # user_A (fake conn), and we mint for that operator through the dedicated operator session route.
    resp = client.post(
        "/v1/operator/session-token",
        headers=_auth(),
        json={
            "business": "climblog",
            "max_cost_microusd": 5000,
            "operator_user_id": "user_A",
        },
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    token = data["token"]
    assert data["audience"] == safebox_app._OPERATOR_SESSION_AUDIENCE

    scope, nonce, exp = verify_capability(
        token,
        signing_key=_SIGNING_KEY.encode("utf-8"),
        expected_audience=safebox_app._OPERATOR_SESSION_AUDIENCE,
        now=0,
    )
    # The verified scope is the AUTHORITATIVE one the safebox derived (not a client-asserted value).
    assert scope.takyon_user_id == "user_A"
    assert scope.business_slug == "climblog"
    assert scope.app_user_id is None  # operator/platform call has no product sub-user
    assert scope.action == safebox_app._OPERATOR_SESSION_AUDIENCE
    assert scope.max_cost_microusd == 5000
    assert nonce and exp > 0


def test_operator_root_session_token_derives_user_from_verified_auth0_session(client, monkeypatch):
    monkeypatch.setattr(
        safebox_app.safebox,
        "auth0_verify_session",
        lambda **_kwargs: {"sub": "auth0|user_A", "email": "owner@example.com"},
    )

    resp = client.post(
        "/v1/operator/session-token",
        headers=_auth(),
        json={
            "business": "",
            "session_token": "dashboard-session-token",
            "max_cost_microusd": 5000,
            "operator_user_id": "user_A",
        },
    )
    assert resp.status_code == 200, resp.text

    token = resp.json()["token"]
    scope, _, _ = verify_capability(
        token,
        signing_key=_SIGNING_KEY.encode("utf-8"),
        expected_audience=safebox_app._OPERATOR_SESSION_AUDIENCE,
        now=0,
    )
    assert scope.takyon_user_id == "user_A"
    assert scope.business_slug == ""
    assert scope.app_user_id is None
    assert scope.action == safebox_app._OPERATOR_SESSION_AUDIENCE


def test_operator_root_session_token_refuses_session_user_mismatch(client, monkeypatch):
    monkeypatch.setattr(
        safebox_app.safebox,
        "auth0_verify_session",
        lambda **_kwargs: {"sub": "auth0|user_A", "email": "owner@example.com"},
    )

    resp = client.post(
        "/v1/operator/session-token",
        headers=_auth(),
        json={
            "business": "",
            "session_token": "dashboard-session-token",
            "max_cost_microusd": 5000,
            "operator_user_id": "user_B",
        },
    )

    assert resp.status_code == 403
    assert resp.json()["detail"] == "operator_user_mismatch"


def test_operator_root_session_token_allows_active_user_without_auth0_session(client, monkeypatch):
    monkeypatch.setattr(safebox_app.safebox, "auth0_verify_session", lambda **_kwargs: None)
    import plugins.takyon.control_plane as control_plane

    monkeypatch.setattr(
        control_plane,
        "resolve_user_principal",
        lambda _conn, _user_id, **_kwargs: types.SimpleNamespace(user_id="user_A"),
    )

    resp = client.post(
        "/v1/operator/session-token",
        headers=_auth(),
        json={
            "business": "",
            "max_cost_microusd": 5000,
            "operator_user_id": "user_A",
        },
    )
    assert resp.status_code == 200, resp.text

    token = resp.json()["token"]
    scope, _, _ = verify_capability(
        token,
        signing_key=_SIGNING_KEY.encode("utf-8"),
        expected_audience=safebox_app._OPERATOR_SESSION_AUDIENCE,
        now=0,
    )
    assert scope.takyon_user_id == "user_A"
    assert scope.business_slug == ""
    assert scope.app_user_id is None
    assert scope.action == safebox_app._OPERATOR_SESSION_AUDIENCE


def test_operator_root_session_token_refuses_unknown_user_without_auth0_session(client, monkeypatch):
    monkeypatch.setattr(safebox_app.safebox, "auth0_verify_session", lambda **_kwargs: None)
    import plugins.takyon.control_plane as control_plane

    monkeypatch.setattr(control_plane, "resolve_user_principal", lambda *_args, **_kwargs: None)

    resp = client.post(
        "/v1/operator/session-token",
        headers=_auth(),
        json={
            "business": "",
            "max_cost_microusd": 5000,
            "operator_user_id": "user_B",
        },
    )

    assert resp.status_code == 403
    assert resp.json()["detail"] == "operator_root_session_required"


def test_operator_session_token_needs_operator_client_authority(client, monkeypatch):
    monkeypatch.delenv(safebox_app._OPERATOR_CLIENTS_ENV, raising=False)
    resp = client.post(
        "/v1/operator/session-token",
        headers=_auth(),
        json={
            "business": "climblog",
            "max_cost_microusd": 5000,
            "operator_user_id": "user_A",
        },
    )

    assert resp.status_code == 503
    assert resp.json()["detail"] == "operator_client_allowlist_unconfigured"


def test_operator_session_token_needs_operator_route_token(client, monkeypatch):
    resp = client.post(
        "/v1/operator/session-token",
        headers=_auth(operator=False),
        json={
            "business": "climblog",
            "max_cost_microusd": 5000,
            "operator_user_id": "user_A",
        },
    )

    assert resp.status_code == 401
    assert resp.json()["detail"] == "operator_unauthorized"


def test_operator_session_token_refuses_non_allowlisted_client(client, monkeypatch):
    monkeypatch.setenv(safebox_app._OPERATOR_CLIENTS_ENV, "192.0.2.10")
    resp = client.post(
        "/v1/operator/session-token",
        headers=_auth(),
        json={
            "business": "climblog",
            "max_cost_microusd": 5000,
            "operator_user_id": "user_A",
        },
    )

    assert resp.status_code == 403
    assert resp.json()["detail"] == "operator_client_not_allowed"


def test_generic_storage_stays_operator_only(client):
    resp = client.post(
        "/v1/storage/put",
        headers=_auth(operator=False),
        json={
            "provider": "supabase_s3",
            "key": "climblog/private.txt",
            "data_b64": base64.b64encode(b"secret").decode("ascii"),
            "digest": "digest",
        },
    )

    assert resp.status_code == 401
    assert resp.json()["detail"] == "operator_unauthorized"


def test_operator_storage_put_allows_safe_new_business_namespace(client, monkeypatch):
    class _MissingBusinessConn:
        def execute(self, sql, params=None):
            return _OwnerCursor(None)

    @contextlib.contextmanager
    def _missing_business_conn():
        yield _MissingBusinessConn()

    captured = {}
    monkeypatch.setattr(safebox_app, "_safebox_db_conn", _missing_business_conn)

    def fake_storage_put(provider, key, data, *, digest):
        captured.update({"provider": provider, "key": key, "data": data, "digest": digest})
        return {"provider": provider, "key": key, "stored": True}

    monkeypatch.setattr(safebox, "storage_put", fake_storage_put)

    resp = client.post(
        "/v1/storage/put",
        headers=_auth(),
        json={
            "provider": "supabase_s3",
            "key": "fresh-bootstrap/product/site/index.html",
            "data_b64": base64.b64encode(b"<html></html>").decode("ascii"),
            "digest": "sha256",
        },
    )

    assert resp.status_code == 200, resp.text
    assert resp.json()["stored"] is True
    assert captured == {
        "provider": "supabase_s3",
        "key": "fresh-bootstrap/product/site/index.html",
        "data": b"<html></html>",
        "digest": "sha256",
    }


def test_operator_storage_put_still_rejects_unsafe_namespace(client, monkeypatch):
    monkeypatch.setattr(safebox, "storage_put", lambda *a, **k: pytest.fail("storage called"))
    resp = client.post(
        "/v1/storage/put",
        headers=_auth(),
        json={
            "provider": "supabase_s3",
            "key": "../escape.txt",
            "data_b64": base64.b64encode(b"bad").decode("ascii"),
            "digest": "sha256",
        },
    )

    assert resp.status_code == 403
    assert resp.json()["detail"] == "unsafe_slug"


def test_app_media_put_uses_app_session_not_operator_token(client, monkeypatch):
    captured = {}

    monkeypatch.setattr(app_identity, "validate_session", lambda c, b, t: types.SimpleNamespace(id="cust_X"))

    def fake_storage_put(provider, key, data, *, digest):
        captured.update({"provider": provider, "key": key, "data": data, "digest": digest})
        return {"stored": True, "key": key}

    monkeypatch.setattr(safebox, "storage_put", fake_storage_put)

    resp = client.post(
        "/v1/app-media/put",
        headers=_auth(operator=False),
        json={
            "provider": "supabase_s3",
            "business": "climblog",
            "session_token": "sess-abc",
            "media_id": "m_1",
            "data_b64": base64.b64encode(b"image-bytes").decode("ascii"),
            "digest": "sha256",
        },
    )

    assert resp.status_code == 200, resp.text
    assert resp.json() == {
        "provider": "supabase_s3",
        "business": "climblog",
        "media_id": "m_1",
        "stored": True,
    }
    assert captured == {
        "provider": "supabase_s3",
        "key": "media/climblog/m_1",
        "data": b"image-bytes",
        "digest": "sha256",
    }


class _MediaConn:
    def __init__(self, *, app_user_id: str = "cust_X", storage_key: str = "media/climblog/m_1"):
        self._app_user_id = app_user_id
        self._storage_key = storage_key

    def execute(self, sql, params=None):
        if "from app_media" in str(sql):
            return _OwnerCursor({"app_user_id": self._app_user_id, "storage_key": self._storage_key})
        return _OwnerCursor({"owner_user_id": "user_A"})


def test_app_media_get_uses_row_ownership_and_hides_storage_key(client, monkeypatch):
    @contextlib.contextmanager
    def _fake_conn():
        yield _MediaConn()

    monkeypatch.setattr(safebox_app, "_safebox_db_conn", _fake_conn)
    monkeypatch.setattr(app_identity, "validate_session", lambda c, b, t: types.SimpleNamespace(id="cust_X"))
    monkeypatch.setattr(safebox, "storage_get", lambda provider, key: b"stored-image")

    resp = client.post(
        "/v1/app-media/get",
        headers=_auth(operator=False),
        json={
            "provider": "supabase_s3",
            "business": "climblog",
            "session_token": "sess-abc",
            "media_id": "m_1",
        },
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["business"] == "climblog"
    assert body["media_id"] == "m_1"
    assert "key" not in body
    assert base64.b64decode(body["data_b64"]) == b"stored-image"


def test_app_media_get_refuses_cross_user_session(client, monkeypatch):
    @contextlib.contextmanager
    def _fake_conn():
        yield _MediaConn(app_user_id="cust_X")

    monkeypatch.setattr(safebox_app, "_safebox_db_conn", _fake_conn)
    monkeypatch.setattr(app_identity, "validate_session", lambda c, b, t: types.SimpleNamespace(id="cust_Y"))

    resp = client.post(
        "/v1/app-media/get",
        headers=_auth(operator=False),
        json={
            "provider": "supabase_s3",
            "business": "climblog",
            "session_token": "sess-other",
            "media_id": "m_1",
        },
    )

    assert resp.status_code == 404
    assert resp.json()["detail"] == "media_not_found"


def test_generic_mint_refuses_operator_identity(client):
    resp = client.post(
        "/v1/token/mint",
        headers=_auth(),
        json={
            "business": "climblog",
            "action": "anthropic.messages",
            "max_cost_microusd": 5000,
            "operator_user_id": "user_A",
        },
    )
    assert resp.status_code == 403
    assert resp.json()["detail"] == "operator_capabilities_use_session_route"


def test_generic_mint_refuses_creative_audiences(client):
    resp = client.post(
        "/v1/token/mint",
        headers=_auth(),
        json={
            "business": "climblog",
            "action": "creative.logo",
            "max_cost_microusd": 1,
            "session_token": "sess-abc",
        },
    )
    assert resp.status_code == 400
    assert resp.json()["detail"] == "unmappable_action"


def test_generic_mint_refuses_postmark_audience(client):
    resp = client.post(
        "/v1/token/mint",
        headers=_auth(),
        json={
            "business": "climblog",
            "action": "postmark.send",
            "max_cost_microusd": 1500,
            "session_token": "sess-abc",
        },
    )

    assert resp.status_code == 400
    assert resp.json()["detail"] == "unmappable_action"


def test_mint_product_token_roundtrips_with_subuser_scope(client, monkeypatch):
    # Product (sub-user) mint: boundary 2 resolves the REAL app_user from the session + a paid
    # entitlement, boundary 1 binds the owner. Stub both reads.
    monkeypatch.setattr(app_identity, "validate_session", lambda c, b, t: types.SimpleNamespace(id="cust_X"))
    monkeypatch.setattr(
        app_entitlements, "get_active_entitlement", lambda c, b, u: types.SimpleNamespace(tier="pro")
    )
    resp = client.post(
        "/v1/token/mint",
        headers=_auth(),
        json={
            "business": "climblog",
            "action": "tavily.search",
            "max_cost_microusd": 3000,
            "session_token": "sess-abc",
        },
    )
    assert resp.status_code == 200, resp.text
    token = resp.json()["token"]
    scope, _nonce, _exp = verify_capability(
        token,
        signing_key=_SIGNING_KEY.encode("utf-8"),
        expected_audience=safebox_app._TAVILY_AUDIENCE,
        now=0,
    )
    assert scope.takyon_user_id == "user_A"  # owner from fake conn
    assert scope.app_user_id == "cust_X"  # real sub-user from the session
    assert scope.action == "tavily.search"
    assert scope.max_cost_microusd == 3000


def test_mint_requires_product_session_shape(client):
    # The generic mint route is product-only now. Operator/platform and creative authorities go through
    # their own safebox gates.
    resp = client.post(
        "/v1/token/mint",
        headers=_auth(),
        json={"business": "climblog", "action": "anthropic.messages", "max_cost_microusd": 1000},
    )
    assert resp.status_code == 403
    assert resp.json()["detail"] == "product_session_token_required"


def test_mint_requires_internal_token(client):
    # The route is internal-only; a missing/garbage bearer is rejected before any work.
    resp = client.post(
        "/v1/token/mint",
        json={
            "business": "climblog",
            "action": "anthropic.messages",
            "max_cost_microusd": 1000,
            "session_token": "sess-abc",
        },
    )
    assert resp.status_code == 401


def test_legacy_creative_credit_spend_routes_are_closed(client):
    resp = client.post(
        "/v1/creative-credits/reserve",
        headers=_auth(),
        json={
            "business_slug": "climblog",
            "credits": 1,
            "reservation_key": "legacy-bypass",
            "metadata": {},
        },
    )
    assert resp.status_code == 403
    assert resp.json()["detail"] == "creative_credit_spend_requires_creative_gate"


def test_business_bootstrap_credits_route_uses_fixed_policy(client, monkeypatch):
    calls = []

    def _grant(_conn, business_slug, operator_user_id):
        calls.append({"business_slug": business_slug, "operator_user_id": operator_user_id})
        return business_credits.CreativeCreditBalances(
            business_slug=business_slug,
            balance_credits=3,
            reserved_credits=0,
        )

    monkeypatch.setattr(safebox_app.safebox, "_local_grant_business_bootstrap_credits", _grant)
    monkeypatch.setattr(safebox_app.safebox, "business_bootstrap_free_credits", lambda: 3)

    resp = client.post(
        "/v1/creative-credits/bootstrap-starter",
        headers=_auth(),
        json={"business_slug": "climblog", "operator_user_id": "user_A"},
    )

    assert resp.status_code == 200, resp.text
    assert resp.json() == {
        "ok": True,
        "business_slug": "climblog",
        "balance_credits": 3,
        "reserved_credits": 0,
        "credited_credits": 3,
    }
    assert calls == [{"business_slug": "climblog", "operator_user_id": "user_A"}]


def test_business_bootstrap_credits_route_refuses_owner_mismatch(client, monkeypatch):
    # The seed grant is ungated from any paid create charge (operator create is ungated), but it
    # still enforces business-owner isolation. A PermissionError from the local grant must surface as
    # a 403 with its detail intact — this is the route's error-mapping contract.
    def _grant(_conn, business_slug, operator_user_id):
        raise PermissionError("business_bootstrap_credit_owner_mismatch")

    monkeypatch.setattr(safebox_app.safebox, "_local_grant_business_bootstrap_credits", _grant)

    resp = client.post(
        "/v1/creative-credits/bootstrap-starter",
        headers=_auth(),
        json={"business_slug": "climblog", "operator_user_id": "user_A"},
    )

    assert resp.status_code == 403
    assert resp.json()["detail"] == "business_bootstrap_credit_owner_mismatch"


def test_starter_allowance_requires_verified_auth0_session(client, monkeypatch):
    monkeypatch.setattr(safebox_app.safebox, "auth0_verify_session", lambda **_kwargs: None)
    monkeypatch.setattr(
        safebox_app.safebox,
        "_local_grant_starter_allowance",
        lambda *a, **k: pytest.fail("starter allowance grant must not run"),
    )

    resp = client.post(
        "/v1/billing/starter-allowance",
        headers=_auth(),
        json={"user_id": "user_A"},
    )

    assert resp.status_code == 403
    assert resp.json()["detail"] == "starter_session_required"


def test_starter_allowance_derives_user_from_verified_auth0_session(client, monkeypatch):
    calls = []

    monkeypatch.setattr(
        safebox_app.safebox,
        "auth0_verify_session",
        lambda **_kwargs: {"sub": "auth0|starter", "email": "owner@example.com", "email_verified": True},
    )

    def _grant(_conn, user_id, *, idempotency_subject=None):
        calls.append({"user_id": user_id, "idempotency_subject": idempotency_subject})
        return 100

    monkeypatch.setattr(safebox_app.safebox, "_local_grant_starter_allowance", _grant)

    resp = client.post(
        "/v1/billing/starter-allowance",
        headers=_auth(),
        json={"user_id": "user_A", "session_token": "signed-session"},
    )

    assert resp.status_code == 200, resp.text
    assert resp.json()["user_id"] == "user_A"
    assert resp.json()["included_cents"] == 100
    assert calls == [{"user_id": "user_A", "idempotency_subject": "auth0:auth0|starter"}]


def test_starter_allowance_refuses_session_user_mismatch(client, monkeypatch):
    monkeypatch.setattr(
        safebox_app.safebox,
        "auth0_verify_session",
        lambda **_kwargs: {"sub": "auth0|starter", "email": "owner@example.com", "email_verified": True},
    )
    monkeypatch.setattr(
        safebox_app.safebox,
        "_local_grant_starter_allowance",
        lambda *a, **k: pytest.fail("mismatched starter grant must not run"),
    )

    resp = client.post(
        "/v1/billing/starter-allowance",
        headers=_auth(),
        json={"user_id": "other-user", "session_token": "signed-session"},
    )

    assert resp.status_code == 403
    assert resp.json()["detail"] == "starter_user_mismatch"


def test_generic_stripe_route_requires_takyon_app_scope(client, monkeypatch):
    monkeypatch.setattr(safebox_app.safebox, "stripe_request", lambda *a, **k: pytest.fail("stripe called"))
    resp = client.post(
        "/v1/stripe/request",
        headers=_auth(),
        json={"path": "checkout/sessions", "method": "POST", "params": {"mode": "payment"}},
    )
    assert resp.status_code == 403
    assert resp.json()["detail"] == "stripe_scope_required"


def test_generic_stripe_catalog_mutation_requires_operator_route_token(client, monkeypatch):
    monkeypatch.setattr(safebox_app.safebox, "stripe_request", lambda *a, **k: pytest.fail("stripe called"))

    resp = client.post(
        "/v1/stripe/request",
        headers=_auth(operator=False),
        json={
            "path": "products",
            "method": "POST",
            "params": {
                "name": "Climblog Monthly",
                "metadata[business]": "climblog",
                "metadata[plan_key]": "monthly",
                "metadata[source]": "takyon_app",
            },
        },
    )

    assert resp.status_code == 401
    assert resp.json()["detail"] == "operator_unauthorized"


def test_generic_stripe_catalog_mutation_allows_operator_route_token(client, monkeypatch):
    calls: list[tuple[str, str]] = []

    @contextlib.contextmanager
    def _fake_conn():
        yield _CheckoutConn(None)

    def _stripe(path, params=None, *, method="POST"):
        calls.append((path, method))
        if path == "account":
            return {"id": _STRIPE_ACCOUNT_ID, "object": "account"}
        if path == "products":
            return _product_object()
        pytest.fail(f"unexpected Stripe call: {path}")

    monkeypatch.setattr(safebox_app, "_safebox_db_conn", _fake_conn)
    monkeypatch.setattr(safebox_app.safebox, "stripe_request", _stripe)

    metadata_params = {
        f"metadata[{key}]": value
        for key, value in _catalog_metadata().items()
        if key != "takyon_stripe_account_id"
    }

    resp = client.post(
        "/v1/stripe/request",
        headers=_auth(),
        json={
            "path": "products",
            "method": "POST",
                "params": {
                    "name": "Climblog Monthly",
                    **metadata_params,
                },
        },
    )

    assert resp.status_code == 200, resp.text
    assert calls == [("account", "GET"), ("products", "POST")]
    assert resp.json()["id"] == "prod_123"


def test_generic_stripe_route_cannot_mutate_subscriptions(client, monkeypatch):
    monkeypatch.setattr(safebox_app.safebox, "stripe_request", lambda *a, **k: pytest.fail("stripe called"))
    resp = client.post(
        "/v1/stripe/request",
        headers=_auth(),
        json={
            "path": "subscriptions/sub_123",
            "method": "POST",
            "params": {"cancel_at_period_end": "true"},
        },
    )
    assert resp.status_code == 403
    assert resp.json()["detail"] == "stripe_path_not_allowed"


def test_generic_stripe_route_cannot_read_subscription_or_checkout_objects(client, monkeypatch):
    monkeypatch.setattr(safebox_app.safebox, "stripe_request", lambda *a, **k: pytest.fail("stripe called"))

    subscription_resp = client.post(
        "/v1/stripe/request",
        headers=_auth(operator=False),
        json={"path": "subscriptions/sub_123", "method": "GET", "params": {}},
    )
    checkout_resp = client.post(
        "/v1/stripe/request",
        headers=_auth(operator=False),
        json={"path": "checkout/sessions/cs_123", "method": "GET", "params": {}},
    )

    assert subscription_resp.status_code == 403
    assert subscription_resp.json()["detail"] == "stripe_path_not_allowed"
    assert checkout_resp.status_code == 403
    assert checkout_resp.json()["detail"] == "stripe_path_not_allowed"


def test_app_subscription_cancel_requires_app_session(client, monkeypatch):
    monkeypatch.setattr(
        safebox_app.safebox,
        "stripe_request",
        lambda *a, **k: pytest.fail("stripe called"),
    )
    resp = client.post(
        "/v1/stripe/app-subscription/cancel",
        headers=_auth(operator=False),
        json={"business_slug": "climblog", "app_user_id": "cust_X"},
    )

    assert resp.status_code == 403
    assert resp.json()["detail"] == "app_session_required"


def test_app_subscription_cancel_requires_session_user_match(client, monkeypatch):
    monkeypatch.setattr(
        app_identity,
        "validate_session",
        lambda c, b, t: types.SimpleNamespace(id="other_user"),
    )
    monkeypatch.setattr(
        safebox_app.safebox,
        "stripe_request",
        lambda *a, **k: pytest.fail("stripe called"),
    )

    resp = client.post(
        "/v1/stripe/app-subscription/cancel",
        headers=_auth(operator=False),
        json={
            "business_slug": "climblog",
            "app_user_id": "cust_X",
            "session_token": "sess-for-other-user",
        },
    )

    assert resp.status_code == 403
    assert resp.json()["detail"] == "app_session_user_mismatch"


def test_app_subscription_cancel_uses_session_bound_user(client, monkeypatch):
    calls: list[tuple[str, str]] = []
    stripe_calls: list[tuple[str, dict, str]] = []
    monkeypatch.setattr(
        app_identity,
        "validate_session",
        lambda c, b, t: types.SimpleNamespace(id="cust_X"),
    )

    def _stripe(path, params, *, method="POST", **_kwargs):
        stripe_calls.append((path, params, method))
        return {
            "id": "sub_123",
            "object": "subscription",
            "status": "canceled",
            "cancel_at_period_end": False,
        }

    def _cancel(conn, business, *, app_user_id, subscription_canceler):
        calls.append((business, app_user_id))
        canceled = subscription_canceler("sub_123")
        return {
            "business_slug": business,
            "app_user_id": app_user_id,
            "stripe_subscription_id": "sub_123",
            "stripe_subscription_status": canceled["status"],
            "cancel_at_period_end": False,
            "effective_immediately": True,
        }

    monkeypatch.setattr(safebox_app.safebox, "stripe_request", _stripe)
    monkeypatch.setattr(app_payments, "cancel_subscription", _cancel)

    resp = client.post(
        "/v1/stripe/app-subscription/cancel",
        headers=_auth(operator=False),
        json={
            "business_slug": "climblog",
            "app_user_id": "cust_X",
            "session_token": "sess-for-cust-x",
            # A legacy client cannot restore grace-period behavior; the dedicated server route
            # ignores this obsolete field and always performs Stripe DELETE.
            "cancel_at_period_end": True,
        },
    )

    assert resp.status_code == 200, resp.text
    assert calls == [("climblog", "cust_X")]
    assert stripe_calls == [("subscriptions/sub_123", {}, "DELETE")]
    assert resp.json()["stripe_subscription_id"] == "sub_123"
    assert resp.json()["effective_immediately"] is True


def test_immediate_subscription_cancel_retry_reads_terminal_provider_truth(monkeypatch):
    calls: list[tuple[str, dict, str]] = []

    def _stripe(path, params, *, method="POST", **_kwargs):
        calls.append((path, params, method))
        if method == "DELETE":
            raise stripe_util.StripeError("Stripe subscriptions/sub_123 failed: 404 resource_missing")
        return {
            "id": "sub_123",
            "object": "subscription",
            "status": "canceled",
            "cancel_at_period_end": False,
        }

    monkeypatch.setattr(safebox, "stripe_request", _stripe)
    result = safebox.cancel_stripe_subscription_immediately("sub_123")

    assert result["status"] == "canceled"
    assert calls == [
        ("subscriptions/sub_123", {}, "DELETE"),
        ("subscriptions/sub_123", {}, "GET"),
    ]


def test_immediate_subscription_cancel_reconciles_ambiguous_transport_failure(monkeypatch):
    calls: list[str] = []

    class _Response:
        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

        def read(self):
            return json.dumps(
                {
                    "id": "sub_123",
                    "object": "subscription",
                    "status": "canceled",
                    "cancel_at_period_end": False,
                }
            ).encode("utf-8")

    def _urlopen(request, timeout=None):
        calls.append(request.method)
        if request.method == "DELETE":
            raise urllib.error.URLError("response disappeared after write")
        return _Response()

    monkeypatch.setenv("TAKYON_STRIPE_MODE", "test")
    monkeypatch.setattr(safebox, "_remote_enabled", lambda: False)
    monkeypatch.setattr(safebox, "_local_authority_enabled", lambda: True)
    monkeypatch.setattr(
        safebox,
        "read_env_backed_value",
        lambda key: "sk_test_transport" if key == "STRIPE_SECRET_KEY" else None,
    )
    monkeypatch.setattr(stripe_util.urllib.request, "urlopen", _urlopen)

    result = safebox.cancel_stripe_subscription_immediately("sub_123")

    assert result["status"] == "canceled"
    assert calls == ["DELETE", "GET"]


@pytest.mark.parametrize(
    "reconciliation",
    [
        {"recorded": False, "reason": "subscription_account_binding_mismatch"},
        {"recorded": True, "updated": [{"business_slug": "climblog"}]},
    ],
)
def test_cancel_subscription_falls_back_to_terminal_local_revocation(
    monkeypatch, reconciliation
):
    entitlement = types.SimpleNamespace(
        stripe_subscription_id="sub_123",
        status="active",
        metadata={"stripe_subscription_status": "active"},
        plan_key="monthly",
        current_period_end=None,
    )

    class _Conn:
        def transaction(self):
            return contextlib.nullcontext()

        def execute(self, _sql, _params=None):
            return None

    monkeypatch.setattr(
        app_entitlements,
        "list_entitlements",
        lambda *_args, **_kwargs: [entitlement],
    )
    monkeypatch.setattr(
        app_payments,
        "reconcile_subscription",
        lambda *_args, **_kwargs: reconciliation,
    )
    fallback_metadata: dict = {}

    def _revoke(_conn, subscription_id, *, status, metadata, **_kwargs):
        assert subscription_id == "sub_123"
        assert status == "cancelled"
        entitlement.status = "cancelled"
        entitlement.metadata = {**entitlement.metadata, **metadata}
        fallback_metadata.update(metadata)
        return [{"business_slug": "climblog", "app_user_id": "cust_X"}]

    monkeypatch.setattr(app_entitlements, "set_subscription_status", _revoke)

    result = app_payments.cancel_subscription(
        _Conn(),
        "climblog",
        app_user_id="cust_X",
        subscription_canceler=lambda subscription_id: {
            "id": subscription_id,
            "object": "subscription",
            "status": "canceled",
            "cancel_at_period_end": False,
        },
    )

    assert result["effective_immediately"] is True
    assert result["stripe_subscription_status"] == "canceled"
    assert result["reconciliation_fallback"]
    assert fallback_metadata["cancellation_reconciliation_fallback"] == result[
        "reconciliation_fallback"
    ]
    assert fallback_metadata["cancel_at_period_end"] is False


def test_cancel_subscription_fails_if_terminal_local_reread_does_not_hold(monkeypatch):
    entitlement = types.SimpleNamespace(
        stripe_subscription_id="sub_123",
        status="active",
        metadata={},
        plan_key="monthly",
        current_period_end=None,
    )

    class _Conn:
        def transaction(self):
            return contextlib.nullcontext()

        def execute(self, _sql, _params=None):
            return None

    monkeypatch.setattr(
        app_entitlements,
        "list_entitlements",
        lambda *_args, **_kwargs: [entitlement],
    )
    monkeypatch.setattr(
        app_payments,
        "reconcile_subscription",
        lambda *_args, **_kwargs: {"recorded": False, "reason": "binding_mismatch"},
    )
    monkeypatch.setattr(
        app_entitlements,
        "set_subscription_status",
        lambda *_args, **_kwargs: [],
    )

    with pytest.raises(
        app_payments.InvalidSubscriptionCancellation,
        match="local entitlement did not become terminal",
    ):
        app_payments.cancel_subscription(
            _Conn(),
            "climblog",
            app_user_id="cust_X",
            subscription_canceler=lambda subscription_id: {
                "id": subscription_id,
                "status": "canceled",
            },
        )


def test_cancel_subscription_ignores_openmeter_mirror_when_stripe_row_is_terminal(monkeypatch):
    def _entitlement(source, status):
        return types.SimpleNamespace(
            source=source,
            stripe_subscription_id="sub_123",
            status=status,
            metadata={"stripe_subscription_status": status},
            plan_key="monthly",
            current_period_end=None,
        )

    monkeypatch.setattr(
        app_entitlements,
        "list_entitlements",
        lambda *_args, **_kwargs: [
            _entitlement("openmeter", "active"),
            _entitlement("stripe", "cancelled"),
        ],
    )

    class _Conn:
        def transaction(self):
            return contextlib.nullcontext()

        def execute(self, _sql, _params=None):
            return None

    result = app_payments.cancel_subscription(
        _Conn(),
        "climblog",
        app_user_id="cust_X",
        subscription_canceler=lambda _subscription_id: pytest.fail(
            "a terminal Stripe row must make the retry provider-free"
        ),
    )

    assert result["already_canceled"] is True
    assert result["effective_immediately"] is True


def test_app_checkout_reconcile_requires_expected_context(client, monkeypatch):
    monkeypatch.setattr(
        safebox_app.safebox,
        "stripe_request",
        lambda *a, **k: pytest.fail("stripe called without checkout context"),
    )

    resp = client.post(
        "/v1/stripe/app-checkout/reconcile",
        headers=_auth(operator=False),
        json={"session_id": "cs_paid_123"},
    )

    assert resp.status_code == 403
    assert resp.json()["detail"] == "checkout_context_required"


class _CheckoutReconcileConn:
    def execute(self, sql, params=None):
        sql_text = str(sql).lower()
        if "from businesses" in sql_text:
            return _OwnerCursor((1,))
        if "from app_checkout_intents" in sql_text:
            return _OwnerCursor(("climblog", "cust_X", "customer@example.com"))
        return _OwnerCursor(None)

    @contextlib.contextmanager
    def transaction(self):
        yield


def _app_checkout_reconcile_objects(*, subscription_metadata=None):
    session = {
        "id": "cs_live_123",
        "object": "checkout.session",
        "livemode": True,
        "status": "complete",
        "payment_status": "paid",
        "subscription": "sub_live_123",
        "metadata": {
            "business": "climblog",
            "source": "takyon_app",
            "checkout_intent_id": "00000000-0000-0000-0000-000000000123",
        },
    }
    subscription = {
        "id": "sub_live_123",
        "object": "subscription",
        "livemode": True,
        "status": "active",
        "metadata": subscription_metadata
        if subscription_metadata is not None
        else {
            "business": "climblog",
            "source": "takyon_app",
            "takyon_stripe_account_id": _STRIPE_ACCOUNT_ID,
        },
    }
    return session, subscription


@pytest.mark.parametrize(
    "subscription_metadata",
    [
        {"business": "climblog", "takyon_stripe_account_id": _STRIPE_ACCOUNT_ID},
        {
            "business": "other-business",
            "source": "takyon_app",
            "takyon_stripe_account_id": _STRIPE_ACCOUNT_ID,
        },
        {
            "business": "climblog",
            "source": "takyon_app",
            "takyon_stripe_account_id": "acct_wrong",
        },
    ],
)
def test_app_checkout_reconcile_rejects_unbound_subscription(
    client, monkeypatch, subscription_metadata
):
    session, subscription = _app_checkout_reconcile_objects(
        subscription_metadata=subscription_metadata
    )

    @contextlib.contextmanager
    def _fake_conn():
        yield _CheckoutReconcileConn()

    def _stripe(path, params=None, *, method="POST", idempotency_key=None):
        return subscription if path.startswith("subscriptions/") else session

    monkeypatch.setenv("TAKYON_STRIPE_MODE", "live")
    monkeypatch.setenv("TAKYON_STRIPE_ACCOUNT_ID", _STRIPE_ACCOUNT_ID)
    monkeypatch.setattr(safebox_app, "_safebox_db_conn", _fake_conn)
    monkeypatch.setattr(safebox_app.safebox, "stripe_request", _stripe)
    monkeypatch.setattr(
        app_payments,
        "reconcile_checkout_session",
        lambda *a, **k: pytest.fail("unbound subscription reached entitlement grant"),
    )

    resp = client.post(
        "/v1/stripe/app-checkout/reconcile",
        headers=_auth(operator=False),
        json={
            "session_id": "cs_live_123",
            "business_slug": "climblog",
            "app_user_id": "cust_X",
        },
    )

    assert resp.status_code == 503, resp.text
    assert resp.json()["detail"] == "stripe_subscription_reconcile_pending"


def test_app_checkout_reconcile_rolls_back_when_subscription_not_recorded(client, monkeypatch):
    session, subscription = _app_checkout_reconcile_objects()

    @contextlib.contextmanager
    def _fake_conn():
        yield _CheckoutReconcileConn()

    def _stripe(path, params=None, *, method="POST", idempotency_key=None):
        return subscription if path.startswith("subscriptions/") else session

    monkeypatch.setenv("TAKYON_STRIPE_MODE", "live")
    monkeypatch.setenv("TAKYON_STRIPE_ACCOUNT_ID", _STRIPE_ACCOUNT_ID)
    monkeypatch.setattr(safebox_app, "_safebox_db_conn", _fake_conn)
    monkeypatch.setattr(safebox_app.safebox, "stripe_request", _stripe)
    monkeypatch.setattr(
        app_payments, "reconcile_checkout_session", lambda *a, **k: {"recorded": True}
    )
    monkeypatch.setattr(
        app_payments,
        "reconcile_subscription",
        lambda *a, **k: {"recorded": False, "reason": "metadata_mismatch"},
    )

    resp = client.post(
        "/v1/stripe/app-checkout/reconcile",
        headers=_auth(operator=False),
        json={
            "session_id": "cs_live_123",
            "business_slug": "climblog",
            "app_user_id": "cust_X",
        },
    )

    assert resp.status_code == 503, resp.text
    assert resp.json()["detail"] == "stripe_subscription_reconcile_pending"


class _CheckoutConn:
    def __init__(self, intent_row, *, catalog_row=None):
        self.intent_row = intent_row
        self.catalog_row = _catalog_row() if catalog_row is None else catalog_row
        self.released = False
        self.claim_params = None

    def execute(self, sql, params=None):
        sql_text = str(sql).lower()
        if "from businesses" in sql_text:
            return _OwnerCursor((1,))
        if "takyon_safebox_claim_app_checkout_intent" in sql_text:
            self.claim_params = tuple(params or ())
            return _OwnerCursor(self.intent_row)
        if "from app_plan_policies" in sql_text:
            return _OwnerCursor(self.catalog_row)
        if "takyon_safebox_release_app_checkout_intent" in sql_text:
            self.released = True
            return _OwnerCursor((True,))
        return _OwnerCursor(None)


def _checkout_request(intent_id: str = "00000000-0000-0000-0000-000000000123") -> dict:
    return {
        "path": "checkout/sessions",
        "method": "POST",
        "params": {
            "mode": "subscription",
            "success_url": "https://climblog.coscale.app/app?checkout=success",
            "cancel_url": "https://climblog.coscale.app/app?checkout=cancel",
            "customer_email": "customer@example.com",
            "metadata[business]": "climblog",
            "metadata[plan_key]": "monthly",
            "metadata[checkout_intent_id]": intent_id,
            "metadata[source]": "takyon_app",
        },
    }


def test_generic_stripe_checkout_requires_recorded_intent(client, monkeypatch):
    @contextlib.contextmanager
    def _fake_conn():
        yield _CheckoutConn(None)

    calls: list[str] = []

    def _stripe(path, params=None, *, method="POST", idempotency_key=None):
        calls.append(path)
        if path == "account":
            return {"id": _STRIPE_ACCOUNT_ID, "object": "account"}
        pytest.fail("checkout session must not be created without a recorded app intent")

    monkeypatch.setattr(safebox_app, "_safebox_db_conn", _fake_conn)
    monkeypatch.setattr(safebox_app.safebox, "stripe_request", _stripe)

    resp = client.post("/v1/stripe/request", headers=_auth(), json=_checkout_request())

    assert resp.status_code == 409, resp.text
    assert resp.json()["detail"] == "stripe_checkout_intent_not_open"
    assert calls == ["account"]


def test_generic_stripe_checkout_rejects_all_client_pricing(client, monkeypatch):
    request = _checkout_request()
    request["params"]["line_items[0][price_data][unit_amount]"] = 1
    monkeypatch.setattr(
        safebox_app.safebox,
        "stripe_request",
        lambda *args, **kwargs: pytest.fail("client pricing reached Stripe"),
    )

    resp = client.post("/v1/stripe/request", headers=_auth(), json=request)

    assert resp.status_code == 403
    assert resp.json()["detail"] == "stripe_checkout_pricing_client_forbidden"


def test_generic_stripe_checkout_rejects_client_branding(client, monkeypatch):
    request = _checkout_request()
    request["params"]["branding_settings[display_name]"] = "Impersonated Brand"
    monkeypatch.setattr(
        safebox_app.safebox,
        "stripe_request",
        lambda *args, **kwargs: pytest.fail("client branding reached Stripe"),
    )

    resp = client.post("/v1/stripe/request", headers=_auth(), json=request)

    assert resp.status_code == 403
    assert resp.json()["detail"] == "stripe_checkout_presentation_client_forbidden"


def test_generic_stripe_checkout_rejects_client_billing_mode(client, monkeypatch):
    request = _checkout_request()
    request["params"]["subscription_data[billing_mode][type]"] = "flexible"
    monkeypatch.setattr(
        safebox_app.safebox,
        "stripe_request",
        lambda *args, **kwargs: pytest.fail("client billing mode reached Stripe"),
    )

    resp = client.post("/v1/stripe/request", headers=_auth(), json=request)

    assert resp.status_code == 403
    assert resp.json()["detail"] == "stripe_checkout_presentation_client_forbidden"


def test_generic_stripe_checkout_uses_recorded_intent_authority(client, monkeypatch):
    @contextlib.contextmanager
    def _fake_conn():
        yield _CheckoutConn(_checkout_row())

    calls: list[str] = []
    captured: dict[str, object] = {}

    def _stripe(path, params=None, *, method="POST", idempotency_key=None):
        calls.append(path)
        if path == "account":
            return {"id": _STRIPE_ACCOUNT_ID, "object": "account"}
        if path == "checkout/sessions":
            captured["params"] = dict(params or {})
            captured["idempotency_key"] = idempotency_key
            return _checkout_session_object(params or {})
        pytest.fail(f"unexpected stripe path: {path}")

    monkeypatch.setattr(safebox_app, "_safebox_db_conn", _fake_conn)
    monkeypatch.setattr(safebox_app.safebox, "stripe_request", _stripe)

    resp = client.post("/v1/stripe/request", headers=_auth(), json=_checkout_request())

    assert resp.status_code == 200, resp.text
    assert resp.json()["id"] == "cs_live_123"
    assert calls == ["account", "checkout/sessions"]
    authoritative = captured["params"]
    assert isinstance(authoritative, dict)
    assert authoritative["line_items[0][price_data][unit_amount]"] == 900
    assert authoritative["line_items[0][price_data][currency]"] == "usd"
    assert authoritative["line_items[0][price_data][recurring][interval]"] == "month"
    assert authoritative["client_reference_id"] == "client_ref_123"
    assert "line_items[0][price]" not in authoritative
    assert "subscription_data[billing_mode][type]" not in authoritative
    for key, value in _catalog_metadata().items():
        assert authoritative[f"metadata[{key}]"] == value
        assert authoritative[f"subscription_data[metadata][{key}]"] == value
        assert (
            authoritative[f"line_items[0][price_data][product_data][metadata][{key}]"]
            == value
        )
    assert captured["idempotency_key"] == (
        "takyon-app-checkout-00000000-0000-0000-0000-000000000123"
    )


def test_generic_stripe_checkout_forwards_precompiled_business_branding(client, monkeypatch):
    branding = _checkout_branding()

    @contextlib.contextmanager
    def _fake_conn():
        yield _CheckoutConn(_checkout_row(branding=branding))

    captured: dict[str, object] = {}

    def _stripe(path, params=None, *, method="POST", idempotency_key=None):
        if path == "account":
            return {"id": _STRIPE_ACCOUNT_ID, "object": "account"}
        if path == "checkout/sessions":
            captured["params"] = dict(params or {})
            return _checkout_session_object(params or {})
        pytest.fail(f"unexpected stripe path: {path}")

    monkeypatch.setattr(safebox_app, "_safebox_db_conn", _fake_conn)
    monkeypatch.setattr(safebox_app.safebox, "stripe_request", _stripe)

    resp = client.post("/v1/stripe/request", headers=_auth(), json=_checkout_request())

    assert resp.status_code == 200, resp.text
    authoritative = captured["params"]
    assert isinstance(authoritative, dict)
    for key, value in branding["params"].items():
        assert authoritative[key] == value
    assert authoritative["subscription_data[billing_mode][type]"] == "classic"
    assert authoritative["line_items[0][price_data][unit_amount]"] == 900


@pytest.mark.parametrize(
    "branding",
    [
        _checkout_branding(overrides={"line_items[0][price_data][unit_amount]": "1"}),
        _checkout_branding(
            overrides={
                "branding_settings[logo][url]": "https://evil.example/brand-logo.png",
                "line_items[0][price_data][product_data][images][0]": (
                    "https://evil.example/brand-logo.png"
                ),
            }
        ),
        _checkout_branding(overrides={"branding_settings[button_color]": "red"}),
    ],
)
def test_generic_stripe_checkout_drops_invalid_stored_branding(
    client, monkeypatch, branding
):
    @contextlib.contextmanager
    def _fake_conn():
        yield _CheckoutConn(_checkout_row(branding=branding))

    captured: dict[str, object] = {}

    def _stripe(path, params=None, *, method="POST", idempotency_key=None):
        if path == "account":
            return {"id": _STRIPE_ACCOUNT_ID, "object": "account"}
        if path == "checkout/sessions":
            captured["params"] = dict(params or {})
            return _checkout_session_object(params or {})
        pytest.fail(f"unexpected stripe path: {path}")

    monkeypatch.setattr(safebox_app, "_safebox_db_conn", _fake_conn)
    monkeypatch.setattr(safebox_app.safebox, "stripe_request", _stripe)

    resp = client.post("/v1/stripe/request", headers=_auth(), json=_checkout_request())

    assert resp.status_code == 200, resp.text
    authoritative = captured["params"]
    assert isinstance(authoritative, dict)
    assert not any(str(key).startswith("branding_settings[") for key in authoritative)
    assert "line_items[0][price_data][product_data][images][0]" not in authoritative
    assert "subscription_data[billing_mode][type]" not in authoritative
    assert authoritative["line_items[0][price_data][unit_amount]"] == 900


def test_generic_stripe_checkout_releases_claim_after_transport_error(client, monkeypatch):
    conn = _CheckoutConn(_checkout_row())

    @contextlib.contextmanager
    def _fake_conn():
        yield conn

    calls: list[str] = []

    def _stripe(path, params=None, *, method="POST", idempotency_key=None):
        calls.append(path)
        if path == "account":
            return {"id": _STRIPE_ACCOUNT_ID, "object": "account"}
        if path == "checkout/sessions":
            raise RuntimeError("network failed")
        pytest.fail(f"unexpected stripe path: {path}")

    monkeypatch.setattr(safebox_app, "_safebox_db_conn", _fake_conn)
    monkeypatch.setattr(safebox_app.safebox, "stripe_request", _stripe)

    resp = client.post("/v1/stripe/request", headers=_auth(), json=_checkout_request())

    assert resp.status_code == 502
    assert resp.json()["detail"] == "stripe_error"
    assert conn.released is True
    assert calls == ["account", "checkout/sessions"]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("object", "price"),
        ("livemode", False),
        ("mode", "payment"),
        ("id", "cs_test_wrong_mode"),
        ("url", "https://evil.example/checkout"),
        ("client_reference_id", "other_customer"),
        ("metadata", {}),
    ],
)
def test_generic_stripe_checkout_releases_claim_after_response_mismatch(
    client, monkeypatch, field, value
):
    conn = _CheckoutConn(_checkout_row())

    @contextlib.contextmanager
    def _fake_conn():
        yield conn

    def _stripe(path, params=None, *, method="POST", idempotency_key=None):
        if path == "account":
            return {"id": _STRIPE_ACCOUNT_ID, "object": "account"}
        if path == "checkout/sessions":
            result = _checkout_session_object(params or {})
            result[field] = value
            return result
        pytest.fail(f"unexpected stripe path: {path}")

    monkeypatch.setattr(safebox_app, "_safebox_db_conn", _fake_conn)
    monkeypatch.setattr(safebox_app.safebox, "stripe_request", _stripe)

    resp = client.post("/v1/stripe/request", headers=_auth(), json=_checkout_request())

    assert resp.status_code == 502
    assert resp.json()["detail"] == "stripe_checkout_create_mismatch"
    assert conn.released is True


def test_generic_stripe_checkout_idempotent_retry_reuses_one_session(client, monkeypatch):
    conn = _CheckoutConn(_checkout_row())

    @contextlib.contextmanager
    def _fake_conn():
        yield conn

    sessions: dict[str, dict] = {}
    keys: list[str] = []
    calls: list[str] = []

    def _stripe(path, params=None, *, method="POST", idempotency_key=None):
        calls.append(path)
        if path == "account":
            return {"id": _STRIPE_ACCOUNT_ID, "object": "account"}
        if path == "checkout/sessions":
            keys.append(idempotency_key)
            return sessions.setdefault(
                idempotency_key,
                _checkout_session_object(params or {}, session_id="cs_live_single"),
            )
        pytest.fail(f"unexpected stripe path: {path}")

    monkeypatch.setattr(safebox_app, "_safebox_db_conn", _fake_conn)
    monkeypatch.setattr(safebox_app.safebox, "stripe_request", _stripe)

    first = client.post("/v1/stripe/request", headers=_auth(), json=_checkout_request())
    second = client.post("/v1/stripe/request", headers=_auth(), json=_checkout_request())

    assert first.status_code == second.status_code == 200
    assert first.json()["id"] == second.json()["id"] == "cs_live_single"
    assert len(sessions) == 1
    assert keys[0] == keys[1] == (
        "takyon-app-checkout-00000000-0000-0000-0000-000000000123"
    )
    assert calls == ["account", "checkout/sessions", "account", "checkout/sessions"]
    assert conn.released is False


def test_checkout_pause_blocks_app_operator_and_creative_creation(client, monkeypatch):
    monkeypatch.setenv("TAKYON_STRIPE_CHECKOUT_DISABLED", "1")
    monkeypatch.setattr(
        safebox_app.safebox,
        "stripe_request",
        lambda *args, **kwargs: pytest.fail("paused checkout reached Stripe"),
    )
    app_resp = client.post("/v1/stripe/request", headers=_auth(), json=_checkout_request())
    operator_resp = client.post(
        "/v1/operator/billing/subscription/checkout",
        headers=_auth(),
        json={
            "user_id": "user_A",
            "plan_id": "pro",
            "success_url": "https://app.fourmanifold.com/ok",
            "cancel_url": "https://app.fourmanifold.com/no",
        },
    )
    creative_resp = client.post(
        "/v1/creative-credits/checkout",
        headers=_auth(),
        json={
            "user_id": "user_A",
            "business_slug": "climblog",
            "credits": 100,
            "success_url": "https://app.fourmanifold.com/ok",
            "cancel_url": "https://app.fourmanifold.com/no",
        },
    )
    assert app_resp.status_code == operator_resp.status_code == creative_resp.status_code == 503
    assert app_resp.json()["detail"] == "stripe_checkout_paused"
    assert operator_resp.json()["detail"] == "stripe_checkout_paused"
    assert creative_resp.json()["detail"] == "stripe_checkout_paused"


def test_generic_checkout_defaults_closed_only_in_live_mode(monkeypatch):
    monkeypatch.delenv("TAKYON_STRIPE_CHECKOUT_DISABLED", raising=False)
    monkeypatch.setattr(safebox_app.safebox, "load_env", lambda: {})

    monkeypatch.setenv("TAKYON_STRIPE_MODE", "live")
    assert safebox_app._stripe_checkout_disabled() is True

    monkeypatch.setenv("TAKYON_STRIPE_MODE", "test")
    assert safebox_app._stripe_checkout_disabled() is False


@pytest.mark.parametrize("raw", ["", "typo"])
def test_malformed_checkout_pause_flags_fail_closed_in_live_mode(monkeypatch, raw):
    monkeypatch.setenv("TAKYON_STRIPE_MODE", "live")
    monkeypatch.setenv("TAKYON_STRIPE_CHECKOUT_DISABLED", raw)
    monkeypatch.setenv("TAKYON_STRIPE_OPERATOR_CHECKOUT_DISABLED", raw)
    monkeypatch.setenv("TAKYON_STRIPE_CREATIVE_CHECKOUT_DISABLED", raw)
    monkeypatch.setattr(safebox_app.safebox, "load_env", lambda: {})

    assert safebox_app._stripe_checkout_disabled() is True
    assert (
        safebox_app._specialized_checkout_disabled(
            "TAKYON_STRIPE_OPERATOR_CHECKOUT_DISABLED"
        )
        is True
    )
    assert (
        safebox_app._specialized_checkout_disabled(
            "TAKYON_STRIPE_CREATIVE_CHECKOUT_DISABLED"
        )
        is True
    )


def test_live_app_checkout_requires_configured_account_binding(client, monkeypatch):
    monkeypatch.setenv("TAKYON_ENV", "prod")
    monkeypatch.setenv("TAKYON_STRIPE_MODE", "live")
    monkeypatch.setenv("TAKYON_STRIPE_CHECKOUT_DISABLED", "0")
    monkeypatch.delenv("TAKYON_STRIPE_ACCOUNT_ID", raising=False)
    monkeypatch.setattr(
        safebox_app,
        "_stripe_account_snapshot",
        lambda: (_STRIPE_ACCOUNT_ID, True),
    )
    conn = _CheckoutConn(_checkout_row())

    @contextlib.contextmanager
    def _fake_conn():
        yield conn

    monkeypatch.setattr(safebox_app, "_safebox_db_conn", _fake_conn)

    response = client.post("/v1/stripe/request", headers=_auth(), json=_checkout_request())

    assert response.status_code == 503
    assert response.json()["detail"] == "stripe_live_account_binding_required"
    assert conn.claim_params is None


def test_generic_stripe_checkout_rejects_off_business_redirects(client, monkeypatch):
    calls: list[str] = []

    def _stripe(path, params=None, *, method="POST"):
        calls.append(path)
        return {"metadata": {"business": "climblog", "source": "takyon_app"}}

    monkeypatch.setattr(safebox_app.safebox, "stripe_request", _stripe)

    request = _checkout_request()
    request["params"]["success_url"] = "https://evil.example/app?checkout=success"

    resp = client.post("/v1/stripe/request", headers=_auth(), json=request)

    assert resp.status_code == 403, resp.text
    assert resp.json()["detail"] == "stripe_redirect_not_allowed"
    assert calls == []


def test_postmark_route_is_magic_link_only(client, monkeypatch):
    monkeypatch.setattr(
        safebox_app.safebox, "send_postmark_email", lambda **kwargs: pytest.fail("postmark called")
    )
    resp = client.post(
        "/v1/postmark/send",
        headers=_auth(),
        json={
            "to_email": "customer@example.com",
            "subject": "A totally normal marketing blast",
            "text_body": "hello",
        },
    )
    assert resp.status_code == 403
    assert resp.json()["detail"] == "postmark_scope_required"


def test_postmark_legacy_route_requires_operator_route_token(client, monkeypatch):
    monkeypatch.setattr(
        safebox_app.safebox, "send_postmark_email", lambda **kwargs: pytest.fail("postmark called")
    )
    resp = client.post(
        "/v1/postmark/send",
        headers=_auth(operator=False),
        json={
            "to_email": "customer@example.com",
            "subject": "Sign in to Climblog",
            "text_body": "This link expires in 15 minutes and can be used once. https://climblog.coscale.app/app",
        },
    )
    assert resp.status_code == 401
    assert resp.json()["detail"] == "operator_unauthorized"


def test_vercel_domain_delete_refuses_non_product_domain(client, monkeypatch):
    monkeypatch.setattr(
        safebox_app.safebox, "delete_vercel_project_domain", lambda domain: pytest.fail("vercel called")
    )
    resp = client.post(
        "/v1/vercel/domain/delete",
        headers=_auth(),
        json={"domain": "app.fourmanifold.com"},
    )
    assert resp.status_code == 403
    assert resp.json()["detail"] == "domain_not_product_scoped"


def test_storage_routes_require_business_scoped_prefix(client, monkeypatch):
    monkeypatch.setattr(safebox_app.safebox, "storage_get", lambda *a, **k: pytest.fail("storage called"))
    resp = client.post(
        "/v1/storage/get",
        headers=_auth(),
        json={"provider": "supabase_s3", "key": ""},
    )
    assert resp.status_code == 403
    assert resp.json()["detail"] == "storage_scope_required"


def test_storage_routes_need_operator_client_authority(client, monkeypatch):
    monkeypatch.delenv(safebox_app._OPERATOR_CLIENTS_ENV, raising=False)
    monkeypatch.setattr(safebox_app.safebox, "storage_get", lambda *a, **k: pytest.fail("storage called"))
    resp = client.post(
        "/v1/storage/get",
        headers=_auth(),
        json={"provider": "supabase_s3", "key": "climblog/__takyon/workspace/manifests/1.json"},
    )

    assert resp.status_code == 503
    assert resp.json()["detail"] == "operator_client_allowlist_unconfigured"


def test_storage_routes_need_operator_route_token(client, monkeypatch):
    monkeypatch.setattr(safebox_app.safebox, "storage_get", lambda *a, **k: pytest.fail("storage called"))
    resp = client.post(
        "/v1/storage/get",
        headers=_auth(operator=False),
        json={"provider": "supabase_s3", "key": "climblog/__takyon/workspace/manifests/1.json"},
    )

    assert resp.status_code == 401
    assert resp.json()["detail"] == "operator_unauthorized"


def test_storage_list_unknown_business_is_empty_for_bootstrap(client, monkeypatch):
    class _MissingBusinessConn:
        def execute(self, sql, params=None):
            return _OwnerCursor(None)

    @contextlib.contextmanager
    def _missing_business_conn():
        yield _MissingBusinessConn()

    monkeypatch.setattr(safebox_app, "_safebox_db_conn", _missing_business_conn)
    monkeypatch.setattr(
        safebox_app.safebox,
        "storage_list_digests",
        lambda *a, **k: pytest.fail("unknown business list must not hit storage"),
    )
    monkeypatch.setattr(
        safebox_app.safebox,
        "storage_list_object_sizes",
        lambda *a, **k: pytest.fail("unknown business list must not hit storage"),
    )

    digest_resp = client.post(
        "/v1/storage/list-digests",
        headers=_auth(),
        json={"provider": "supabase_s3", "prefix": "fresh-bootstrap/product/"},
    )
    assert digest_resp.status_code == 200
    assert digest_resp.json()["digests"] == {}

    sizes_resp = client.post(
        "/v1/storage/list-sizes",
        headers=_auth(),
        json={"provider": "supabase_s3", "prefix": "fresh-bootstrap/product/"},
    )
    assert sizes_resp.status_code == 200
    assert sizes_resp.json()["sizes"] == {}


def test_mint_rejects_unmappable_action(client):
    # An action with no mapped audience is unbrokerable: the mint route refuses it (400) rather than
    # falling back to the raw action string as the audience.
    resp = client.post(
        "/v1/token/mint",
        headers=_auth(),
        json={
            "business": "climblog",
            "action": "ping",  # not in _ACTION_AUDIENCE_DEFAULTS
            "max_cost_microusd": 1000,
            "session_token": "sess-abc",
        },
    )
    assert resp.status_code == 400
    assert resp.json()["detail"] == "unmappable_action"


def test_mint_ignores_body_audience_and_uses_the_action_map(client, monkeypatch):
    # A caller cannot mint action="anthropic.messages" under a forged audience: body.audience is
    # IGNORED, the audience is derived SOLELY from the action map (so entitlement/ceiling and the
    # provider invocation are the SAME action).
    monkeypatch.setattr(app_identity, "validate_session", lambda c, b, t: types.SimpleNamespace(id="cust_X"))
    monkeypatch.setattr(
        app_entitlements, "get_active_entitlement", lambda c, b, u: types.SimpleNamespace(tier="pro")
    )
    resp = client.post(
        "/v1/token/mint",
        headers=_auth(),
        json={
            "business": "climblog",
            "action": "anthropic.messages",
            "max_cost_microusd": 1000,
            "session_token": "sess-abc",
            "audience": "tavily.search",  # attempted override — must be ignored
        },
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["audience"] == safebox_app._ANTHROPIC_AUDIENCE


def test_provider_route_unentitled_session_large_estimate_is_403_not_charged(client, monkeypatch):
    # A product session with NO active paid entitlement requesting a positive-cost provider call must
    # be refused at the authoritative mint (403 subscription_required) BEFORE any reserve/charge.
    monkeypatch.setattr(app_identity, "validate_session", lambda c, b, t: types.SimpleNamespace(id="cust_X"))
    monkeypatch.setattr(app_entitlements, "get_active_entitlement", lambda c, b, u: None)

    reserved = []
    # If we ever reach the ledger, record it — the test asserts we never do.
    monkeypatch.setattr(
        safebox_app._UsageLedgerAdapter,
        "reserve",
        lambda self, scope, est: reserved.append((scope.business_slug, est)),
    )

    resp = client.post(
        "/v1/providers/anthropic/messages",
        headers=_auth(),
        json={
            "business": "climblog",
            "action": "anthropic.messages",
            "session_token": "sess-abc",
            "payload": {"prompt": "hi"},
            "estimate_microusd": 5_000_000,
        },
    )
    assert resp.status_code == 403
    assert resp.json()["detail"] == "subscription_required"
    assert reserved == []  # never reserved, never charged


def test_provider_route_action_audience_mismatch_is_400(client):
    # Inline-mint path: the supplied action must map to THIS route's audience, else 400 — a caller
    # cannot mint a cheap action and broker an expensive provider under it.
    resp = client.post(
        "/v1/providers/anthropic/messages",
        headers=_auth(),
        json={
            "business": "climblog",
            "action": "tavily.search",  # maps to tavily, not the anthropic route's audience
            "session_token": "sess-abc",
            "payload": {"prompt": "hi"},
            "estimate_microusd": 2000,
        },
    )
    assert resp.status_code == 400
    assert resp.json()["detail"] == "action_audience_mismatch"


def test_tavily_provider_route_rejects_unsupported_endpoint_before_reserve(client, monkeypatch):
    monkeypatch.setattr(app_identity, "validate_session", lambda c, b, t: types.SimpleNamespace(id="cust_X"))
    monkeypatch.setattr(
        app_entitlements, "get_active_entitlement", lambda c, b, u: types.SimpleNamespace(tier="pro")
    )
    monkeypatch.setattr(
        safebox_app._UsageLedgerAdapter,
        "reserve",
        lambda self, scope, est: pytest.fail("reserved before endpoint validation"),
    )

    resp = client.post(
        "/v1/providers/tavily/search",
        headers=_auth(),
        json={
            "business": "climblog",
            "action": "tavily.search",
            "session_token": "sess-abc",
            "payload": {"endpoint": "crawl", "operation": "search", "url": "https://example.com"},
            "estimate_microusd": 8_000,
        },
    )

    assert resp.status_code == 400
    assert resp.json()["detail"] == "invalid_provider_payload"


def test_provider_postmark_route_reserves_and_sends_key_free(client, monkeypatch):
    reserved: list[tuple[str, int]] = []
    settled: list[int] = []
    provider_calls: list[tuple[str, str]] = []

    monkeypatch.setattr(
        safebox_app,
        "_postmark_authorize_service_send",
        lambda *, business, session_token, recipient_app_user_id: {
            "owner_user_id": "user_A",
            "service_app_user_id": "svc_X",
            "recipient_app_user_id": recipient_app_user_id,
            "recipient_email": "customer@example.com",
            "recipient_tier": "pro",
        },
    )
    monkeypatch.setattr(safebox_app._PgNonceStore, "claim", lambda self, nonce, expires_at, now: True)
    monkeypatch.setattr(
        safebox_app._UsageLedgerAdapter,
        "reserve",
        lambda self, scope, est: reserved.append((scope.app_user_id, int(est))) or {"reservation": "r1"},
    )
    monkeypatch.setattr(
        safebox_app._UsageLedgerAdapter,
        "settle",
        lambda self, reservation, actual: settled.append(int(actual)),
    )
    monkeypatch.setattr(
        safebox_app._UsageLedgerAdapter,
        "release",
        lambda self, reservation: pytest.fail("postmark send should settle, not release"),
    )
    monkeypatch.setattr(safebox_app, "_postmark_key_resolver", lambda scope: "pm-secret")
    monkeypatch.setattr(safebox_app, "_postmark_estimate", lambda payload: (lambda scope: 1500))

    def _caller(payload):
        def _call(scope, key):
            provider_calls.append((scope.app_user_id, key))
            return {"message_id": "pm_123", "provider": "postmark", "status": "sent"}, 1500

        return _call

    monkeypatch.setattr(safebox_app, "_postmark_provider_caller", _caller)

    resp = client.post(
        "/v1/providers/postmark/send",
        headers=_auth(operator=False),
        json={
            "business": "climblog",
            "action": "postmark.send",
            "session_token": "sess-abc",
            "estimate_microusd": 1500,
            "payload": {
                "recipient_app_user_id": "cust_X",
                "subject": "You have a new match",
                "text_body": "Someone liked you back.",
            },
        },
    )

    assert resp.status_code == 200, resp.text
    assert resp.json() == {"message_id": "pm_123", "provider": "postmark", "status": "sent"}
    assert reserved == [("cust_X", 1500)]
    assert settled == [1500]
    assert provider_calls == [("cust_X", "pm-secret")]


def test_provider_postmark_route_rejects_caller_supplied_email_without_recipient(client, monkeypatch):
    monkeypatch.setattr(
        safebox_app._UsageLedgerAdapter,
        "reserve",
        lambda self, scope, est: pytest.fail("must not reserve without safebox-resolved recipient"),
    )

    resp = client.post(
        "/v1/providers/postmark/send",
        headers=_auth(operator=False),
        json={
            "business": "climblog",
            "action": "postmark.send",
            "session_token": "sess-abc",
            "estimate_microusd": 1500,
            "payload": {
                "to_email": "attacker-chosen@example.com",
                "subject": "You have a new match",
                "text_body": "Someone liked you back.",
            },
        },
    )

    assert resp.status_code == 400
    assert resp.json()["detail"] == "recipient_app_user_id_required"


def test_provider_postmark_route_rejects_pre_minted_token(client):
    resp = client.post(
        "/v1/providers/postmark/send",
        headers=_auth(operator=False),
        json={
            "token": "cap.pre.minted",
            "estimate_microusd": 1500,
            "payload": {
                "recipient_app_user_id": "cust_X",
                "subject": "You have a new match",
                "text_body": "Someone liked you back.",
            },
        },
    )

    assert resp.status_code == 403
    assert resp.json()["detail"] == "postmark_requires_service_session"


def test_provider_route_malformed_payload_is_400_not_500(client, monkeypatch):
    # A valid pre-minted token but a malformed provider payload (anthropic_payload rejects an empty
    # messages body) must surface as a clean 400 — the payload builders run BEFORE token verification,
    # so a 500 here would mean a malformed body crashes the route (regression: the builders used to run
    # outside the guard).
    monkeypatch.setattr(app_identity, "validate_session", lambda c, b, t: types.SimpleNamespace(id="cust_X"))
    monkeypatch.setattr(
        app_entitlements, "get_active_entitlement", lambda c, b, u: types.SimpleNamespace(tier="pro")
    )
    minted = client.post(
        "/v1/token/mint",
        headers=_auth(),
        json={
            "business": "climblog",
            "action": "anthropic.messages",
            "max_cost_microusd": 5000,
            "session_token": "sess-abc",
        },
    )
    assert minted.status_code == 200, minted.text
    token = minted.json()["token"]
    resp = client.post(
        "/v1/providers/anthropic/messages",
        headers=_auth(),
        json={"token": token, "payload": {}, "estimate_microusd": 2000},
    )
    assert resp.status_code == 400, resp.text
    assert resp.json()["detail"] == "invalid_provider_payload"


def test_provider_route_fails_closed_without_signing_key(monkeypatch):
    # If the safebox host has no capability signing key, brokering must fail closed (503), never
    # proceed unsigned.
    monkeypatch.setenv(safebox_app._SAFEBOX_TOKEN_ENV, _TOKEN)
    monkeypatch.delenv(safebox_app._CAP_SIGNING_KEY_ENV, raising=False)
    client = TestClient(safebox_app.build_safebox_app())
    resp = client.post(
        "/v1/providers/anthropic/messages",
        headers=_auth(),
        json={
            "business": "climblog",
            "action": "anthropic.messages",
            "operator_user_id": "user_A",
            "payload": {"prompt": "hi"},
            "estimate_microusd": 2000,
        },
    )
    assert resp.status_code == 503
    assert resp.json()["detail"] == "capability_signing_unconfigured"


# ── checkout redirect host scoping (UC3 dev gap: env-aware product base domain) ──────────────────
#
# _require_app_checkout_redirect_url is the subuser-money redirect gate: Stripe Checkout success/
# cancel URLs may only point at THIS business's product host under the environment's declared
# company base domain. The first block CHARACTERIZES the prod-default accept/reject behavior
# (nothing declared -> coscale.app, byte-identical before and after the environment seam); the
# second block pins that a dev twin's base domain is honored ONLY when the environment explicitly
# declares it (environments/dev.yaml domains.company_base -> PUBLIC_COMPANY_BASE_DOMAIN on the dev
# safebox), and that everything else keeps failing closed.


def _clear_declared_company_base(monkeypatch):
    monkeypatch.delenv("PUBLIC_COMPANY_BASE_DOMAIN", raising=False)
    monkeypatch.delenv("TAKYON_COMPANY_BASE_DOMAIN", raising=False)
    monkeypatch.setattr(safebox_app.safebox, "load_env", lambda: {})


def _redirect_allowed(url: str, business: str = "climblog") -> bool:
    from fastapi import HTTPException

    try:
        safebox_app._require_app_checkout_redirect_url(url, business=business)
        return True
    except HTTPException as exc:
        assert exc.status_code == 403
        assert exc.detail == "stripe_redirect_not_allowed"
        return False


def test_checkout_redirect_prod_default_accepts_only_business_coscale_app(monkeypatch):
    """Characterization: with no base domain declared anywhere, the ONLY acceptable redirect host
    is https://<slug>.coscale.app with an /app path — exactly today's prod behavior."""
    _clear_declared_company_base(monkeypatch)
    assert _redirect_allowed("https://climblog.coscale.app/app")
    assert _redirect_allowed("https://climblog.coscale.app/app?checkout=success")
    assert _redirect_allowed("https://climblog.coscale.app/app/settings?checkout=cancel")


def test_checkout_redirect_prod_default_rejects_everything_else(monkeypatch):
    _clear_declared_company_base(monkeypatch)
    for url in (
        "",  # empty
        "https://climblog.coscale.app/app one two",  # interior whitespace (outer whitespace is stripped)
        "http://climblog.coscale.app/app",  # scheme
        "https://user:pw@climblog.coscale.app/app",  # userinfo
        "https://other.coscale.app/app",  # another business's host
        "https://coscale.app/app",  # bare base domain
        "https://climblog.coscale.app/",  # not the /app surface
        "https://climblog.coscale.app/application",  # /app prefix trick
        "https://climblog.coscale.app.evil.example/app",  # suffix trick
        "https://evil.example/app?next=climblog.coscale.app",  # off-platform
        "https://climblog.dev.coscale.app/app",  # dev twin host is NOT acceptable undeclared
    ):
        assert not _redirect_allowed(url), f"must reject {url!r}"


def test_checkout_redirect_honors_declared_dev_company_base(monkeypatch):
    """A dev twin that explicitly declares its base domain (environments/dev.yaml
    domains.company_base -> PUBLIC_COMPANY_BASE_DOMAIN) accepts <slug>.dev.coscale.app — and ONLY
    that base: the prod base and every other host keep failing closed."""
    _clear_declared_company_base(monkeypatch)
    monkeypatch.setenv("PUBLIC_COMPANY_BASE_DOMAIN", "dev.coscale.app")
    assert _redirect_allowed("https://climblog.dev.coscale.app/app")
    assert _redirect_allowed("https://climblog.dev.coscale.app/app?checkout=success")
    for url in (
        "https://climblog.coscale.app/app",  # ONE base at a time: prod base no longer matches
        "https://other.dev.coscale.app/app",
        "http://climblog.dev.coscale.app/app",
        "https://climblog.dev.coscale.app/",
        "https://climblog.dev.coscale.app.evil.example/app",
    ):
        assert not _redirect_allowed(url), f"must reject {url!r}"


def test_checkout_redirect_honors_base_declared_in_safebox_env_store(monkeypatch):
    """The dev safebox declares the base via its env store (safebox.load_env), the same source
    _domain_business_slug already honors — the process env stays empty."""
    monkeypatch.delenv("PUBLIC_COMPANY_BASE_DOMAIN", raising=False)
    monkeypatch.delenv("TAKYON_COMPANY_BASE_DOMAIN", raising=False)
    monkeypatch.setattr(
        safebox_app.safebox, "load_env", lambda: {"PUBLIC_COMPANY_BASE_DOMAIN": "dev.coscale.app"}
    )
    assert _redirect_allowed("https://climblog.dev.coscale.app/app")
    assert not _redirect_allowed("https://climblog.coscale.app/app")


def test_generic_stripe_checkout_accepts_declared_dev_base_redirects(client, monkeypatch):
    """The dev twin accepts its declared host while retaining the same intent authority path."""
    monkeypatch.setenv("PUBLIC_COMPANY_BASE_DOMAIN", "dev.coscale.app")
    monkeypatch.setenv("TAKYON_ENV", "dev")
    monkeypatch.setattr(safebox_app, "_stripe_key_livemode", lambda: False)

    @contextlib.contextmanager
    def _fake_conn():
        yield _CheckoutConn(_checkout_row())

    calls: list[str] = []

    def _stripe(path, params=None, *, method="POST", idempotency_key=None):
        calls.append(path)
        if path == "account":
            return {"id": _STRIPE_ACCOUNT_ID, "object": "account"}
        if path == "checkout/sessions":
            return _checkout_session_object(
                params or {}, session_id="cs_test_dev", livemode=False
            )
        pytest.fail(f"unexpected stripe path: {path}")

    monkeypatch.setattr(safebox_app, "_safebox_db_conn", _fake_conn)
    monkeypatch.setattr(safebox_app.safebox, "stripe_request", _stripe)

    request = _checkout_request()
    request["params"]["success_url"] = "https://climblog.dev.coscale.app/app?checkout=success"
    request["params"]["cancel_url"] = "https://climblog.dev.coscale.app/app?checkout=cancel"

    resp = client.post("/v1/stripe/request", headers=_auth(), json=request)

    assert resp.status_code == 200, resp.text
    assert resp.json()["id"] == "cs_test_dev"
    assert resp.json()["livemode"] is False
    assert calls == ["account", "checkout/sessions"]
