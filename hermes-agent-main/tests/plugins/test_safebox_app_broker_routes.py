"""Phase 2 cutover-prep routes on the safebox service app (TASK B).

Pins that the safebox app still imports + boots with the new action-shaped routes registered, and that
the /v1/token/mint route runs the authoritative two-tier validation and returns a capability token that
verifies back to the SAME scope the safebox derived (mint -> verify roundtrip). The env routes stay
intact (Codex STEP E deletes them later), so we also assert one of them is still mounted.

No live DB / no live provider: the safebox's own connection is stubbed and the identity reads are
monkeypatched, exactly as the safebox_authz unit test does. The point here is route wiring + the
mint->verify identity roundtrip, not the SECURITY DEFINER ledger (that is exercised in the broker core
test against a FakeLedger)."""
from __future__ import annotations

import contextlib
import types

import pytest
from starlette.testclient import TestClient

from plugins.takyon import app_entitlements, app_identity, safebox_app
from plugins.takyon.safebox_capability import verify_capability

_SIGNING_KEY = "safebox-only-signing-key-not-on-any-client"
_TOKEN = "secret-internal-token"


class _OwnerCursor:
    def __init__(self, row):
        self._row = row

    def fetchone(self):
        return self._row


class _OwnerConn:
    """Fake safebox conn: the only query the mint path runs is the owner_user_id lookup."""

    def __init__(self, owner):
        self._owner = owner

    def execute(self, sql, params=None):
        return _OwnerCursor({"owner_user_id": self._owner})


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv(safebox_app._SAFEBOX_TOKEN_ENV, _TOKEN)
    monkeypatch.setenv(safebox_app._CAP_SIGNING_KEY_ENV, _SIGNING_KEY)

    # Stub the safebox's own DB connection so authorize_*_call can resolve the business owner without a
    # live Postgres. yield the same fake conn the authz reads run against.
    @contextlib.contextmanager
    def _fake_conn():
        yield _OwnerConn("user_A")

    monkeypatch.setattr(safebox_app, "_safebox_db_conn", _fake_conn)
    return TestClient(safebox_app.build_safebox_app())


def _auth():
    return {"Authorization": f"Bearer {_TOKEN}"}


def test_app_imports_and_boots(client):
    # Boot smoke: the app constructs and answers healthz with the new routes registered.
    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_new_routes_are_registered_alongside_env_routes():
    app = safebox_app.build_safebox_app()
    paths = {route.path for route in app.routes}
    # New action-shaped routes are mounted...
    assert "/v1/token/mint" in paths
    assert "/v1/providers/anthropic/messages" in paths
    assert "/v1/providers/tavily/search" in paths
    assert "/v1/providers/gemini/image" in paths
    # ...and the legacy env egress route is STILL intact (Codex STEP E removes it later, not now).
    assert "/v1/env/{key}" in paths


def test_mint_operator_token_roundtrips_to_validated_scope(client, monkeypatch):
    # Operator mint: boundary 1 only — the operator must own the business. The owner resolves to
    # user_A (fake conn), and we mint for that operator, so minting succeeds.
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
    assert resp.status_code == 200, resp.text
    data = resp.json()
    token = data["token"]
    # Known action -> canonical audience default, so the token is brokerable by the anthropic route.
    assert data["audience"] == safebox_app._ANTHROPIC_AUDIENCE

    scope, nonce, exp = verify_capability(
        token,
        signing_key=_SIGNING_KEY.encode("utf-8"),
        expected_audience=safebox_app._ANTHROPIC_AUDIENCE,
        now=0,
    )
    # The verified scope is the AUTHORITATIVE one the safebox derived (not a client-asserted value).
    assert scope.takyon_user_id == "user_A"
    assert scope.business_slug == "climblog"
    assert scope.app_user_id is None  # operator/platform call has no product sub-user
    assert scope.action == "anthropic.messages"
    assert scope.max_cost_microusd == 5000
    assert nonce and exp > 0


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


def test_mint_requires_exactly_one_identity_shape(client):
    # Neither identity -> ambiguous_identity (also covers "both" via the same XOR guard).
    resp = client.post(
        "/v1/token/mint",
        headers=_auth(),
        json={"business": "climblog", "action": "anthropic.messages", "max_cost_microusd": 1000},
    )
    assert resp.status_code == 400
    assert resp.json()["detail"] == "ambiguous_identity"


def test_mint_requires_internal_token(client):
    # The route is internal-only; a missing/garbage bearer is rejected before any work.
    resp = client.post(
        "/v1/token/mint",
        json={
            "business": "climblog",
            "action": "anthropic.messages",
            "max_cost_microusd": 1000,
            "operator_user_id": "user_A",
        },
    )
    assert resp.status_code == 401


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
            "operator_user_id": "user_A",
        },
    )
    assert resp.status_code == 400
    assert resp.json()["detail"] == "unmappable_action"


def test_mint_ignores_body_audience_and_uses_the_action_map(client):
    # A caller cannot mint action="anthropic.messages" under a forged audience: body.audience is
    # IGNORED, the audience is derived SOLELY from the action map (so entitlement/ceiling and the
    # provider invocation are the SAME action).
    resp = client.post(
        "/v1/token/mint",
        headers=_auth(),
        json={
            "business": "climblog",
            "action": "anthropic.messages",
            "max_cost_microusd": 1000,
            "operator_user_id": "user_A",
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


def test_provider_route_malformed_payload_is_400_not_500(client):
    # A valid pre-minted token but a malformed provider payload (anthropic_payload rejects an empty
    # messages body) must surface as a clean 400 — the payload builders run BEFORE token verification,
    # so a 500 here would mean a malformed body crashes the route (regression: the builders used to run
    # outside the guard).
    minted = client.post(
        "/v1/token/mint",
        headers=_auth(),
        json={
            "business": "climblog",
            "action": "anthropic.messages",
            "max_cost_microusd": 5000,
            "operator_user_id": "user_A",
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
