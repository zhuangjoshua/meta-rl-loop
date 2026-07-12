"""Characterization pin for the subuser-facing app-plane HTTP routing contract.

PRIORITY-ONE (operator hard rule): "do NOT make subuser less secure; not just
expected use — if subuser can red-team in any way the system breaks, this is not
allowed." The app-plane dispatcher in ``takyon_cli/web_server.py`` (the
``_takyon_app_get`` / ``_takyon_app_post`` / ``_takyon_app_delete`` chain and the
``/api/takyon/apps/{business}/{route}`` FastAPI routes) is the ONLY HTTP surface a
product subuser can reach. This test pins its COMPLETE routing contract for the
CURRENT code so the Stage-6 RuntimeRail refactor cannot change it by one cell:

For every app-plane route it captures
  (path tuple -> handler identity, HTTP method, auth tier, host-role reachability)

and it pins the NEGATIVE contract — everything the subuser plane REFUSES
(dashboard routes, /api/ws, /api/pty, /v1/*, builds, agent turns,
operator-token-on-app-plane, reserved public hosts, unknown paths).

The refactor's equivalence proof is: THIS FILE MUST PASS UNCHANGED against the
registry-driven dispatcher. If any assertion here would change, the refactor
changed behavior and must stop.

Auth-tier vocabulary used below:
  PUBLIC_OPTIONAL  — no session token required; a token is read if present but its
                     absence yields a 200 "authenticated:false" (never a 401).
                     (GET session, GET account.)
  PUBLIC_NO_TOKEN  — the route does not require a session token to be reached and
                     does not 401 on its absence (auth/session POST mints a session;
                     checkout GET/POST proceed with an empty token).
  SESSION_REQUIRED — a missing session token yields HTTP 401 "missing app session"
                     BEFORE any tool handler runs.
  OWNER_REJECTED   — an ``Authorization: Bearer tk_...`` (operator token) on the app
                     plane yields HTTP 403 "owner_token_rejected_on_app_plane" BEFORE
                     any route parsing, on GET/POST/DELETE alike.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from starlette.testclient import TestClient


PRODUCT_HOST = "mathflow.coscale.app"
BUSINESS = "mathflow"
BASE = f"/api/takyon/apps/{BUSINESS}"
OWNER_TOKEN = "Bearer tk_attacksurface1234567890123456789012345678901234"
SESSION_COOKIE = "takyon_app_session=session_123"


@pytest.fixture(autouse=True)
def _hermetic_product_base_domain(monkeypatch):
    """Pin the product-host mapping to the clean-process default (coscale.app).

    The characterization host ``mathflow.coscale.app`` resolves through
    ``web_server._company_base_domain()``, which reads PUBLIC_COMPANY_BASE_DOMAIN /
    TAKYON_COMPANY_BASE_DOMAIN from the process env. Another suite in the same xdist worker can
    legitimately hit ``core.load_takyon_env()`` and pull a configured workspace's on-disk env
    (e.g. PUBLIC_COMPANY_BASE_DOMAIN=fourmanifold.com) into os.environ — after which every request
    here 400s at the Host-header middleware. Scrub the overrides so this suite always
    characterizes the same contract a clean process does."""
    monkeypatch.delenv("PUBLIC_COMPANY_BASE_DOMAIN", raising=False)
    monkeypatch.delenv("TAKYON_COMPANY_BASE_DOMAIN", raising=False)


# ---------------------------------------------------------------------------
# Handler stub harness
# ---------------------------------------------------------------------------
#
# Every app-plane tool handler is a synchronous ``handle_business_*`` symbol imported
# into web_server. We monkeypatch each to a recording stub that returns a benign
# success JSON and records the exact args dict it was called with. That lets us pin
# "path tuple -> handler identity" precisely: after a request we assert WHICH stub ran
# and WITH WHAT business/session_token. The stubs never touch Postgres, so the tests
# are hermetic and deterministic.


_APP_TOOL_HANDLERS: tuple[str, ...] = (
    "handle_business_read_app_session",
    "handle_business_delete_app_session",
    "handle_business_read_app_account",
    "handle_business_read_app_profile",
    "handle_business_upsert_app_profile",
    "handle_business_list_app_directory_entries",
    "handle_business_read_app_directory_entry",
    "handle_business_upsert_app_directory_entry",
    "handle_business_disable_app_directory_entry",
    "handle_business_list_app_records",
    "handle_business_read_app_record",
    "handle_business_upsert_app_record",
    "handle_business_delete_app_record",
    "handle_business_list_app_connections",
    "handle_business_act_on_app_connection",
    "handle_business_create_app_checkout",
    "handle_business_cancel_app_subscription",
    "handle_business_supabase_login",
    "handle_business_send_app_email",
    "handle_business_record_app_usage",
    "handle_business_upload_app_media",
    "handle_business_delete_app_media",
    "handle_business_invoke_app_action",
)


@pytest.fixture()
def app_client(monkeypatch):
    """A TestClient over the real FastAPI app with every app-plane tool stubbed.

    Yields (client, calls) where ``calls`` is a dict handler_name -> list[args].
    """
    import takyon_cli.web_server as web_server

    calls: dict[str, list[dict[str, Any]]] = {name: [] for name in _APP_TOOL_HANDLERS}

    def _make_stub(name: str):
        def _stub(args: dict[str, Any]) -> str:
            calls[name].append(args)
            # supabase_login must return a session_token so the cookie-set branch runs.
            payload: dict[str, Any] = {"success": True, "handler": name}
            if name == "handle_business_supabase_login":
                payload["session_token"] = "session_cookie_123"
            if name == "handle_business_read_app_session":
                payload["session"] = {"active": True}
            if name == "handle_business_read_app_account":
                payload["user"] = {"id": "u_123", "tier": "free", "email": "m@example.com"}
            return json.dumps(payload)

        return _stub

    for name in _APP_TOOL_HANDLERS:
        monkeypatch.setattr(web_server, name, _make_stub(name), raising=True)

    # app_media_get_bytes is called directly (not via the tool-off-loop path) for GET media/<id>.
    def _fake_media_get_bytes(business: str, media_id: str, token: str) -> dict[str, Any]:
        calls.setdefault("app_media_get_bytes", []).append(
            {"business": business, "media_id": media_id, "session_token": token}
        )
        return {"content": b"img", "mime": "image/png"}

    monkeypatch.setattr(web_server, "app_media_get_bytes", _fake_media_get_bytes, raising=True)

    # The per-session SQL rate-limit prechecks (directory lookups + action invokes) need the
    # Postgres rate-limit authority, which is not present in this hermetic env. They are ORTHOGONAL
    # to routing — this test pins path->handler, not the rate limiter — so stub them to no-ops.
    monkeypatch.setattr(
        web_server, "_takyon_app_rate_limit_directory_lookup", lambda **_k: None, raising=True
    )
    monkeypatch.setattr(
        web_server, "_takyon_app_rate_limit_action_invoke", lambda **_k: None, raising=True
    )

    web_server.app.state.bound_host = "127.0.0.1"
    client = TestClient(web_server.app)
    try:
        yield client, calls
    finally:
        if hasattr(web_server.app.state, "bound_host"):
            del web_server.app.state.bound_host


def _phost(extra: dict[str, str] | None = None) -> dict[str, str]:
    headers = {"Host": PRODUCT_HOST}
    if extra:
        headers.update(extra)
    return headers


def _with_session(extra: dict[str, str] | None = None) -> dict[str, str]:
    return _phost({"Cookie": SESSION_COOKIE, **(extra or {})})


def _only_called(calls: dict[str, list], name: str) -> list[dict[str, Any]]:
    """Assert exactly one handler fired and return its recorded call args."""
    fired = {k: v for k, v in calls.items() if v}
    assert set(fired) == {name}, f"expected only {name} to fire, got {sorted(fired)}"
    return calls[name]


# ---------------------------------------------------------------------------
# POSITIVE CONTRACT: path tuple -> handler identity + method + auth tier
# ---------------------------------------------------------------------------


class TestGetRoutingContract:
    """GET dispatcher (_takyon_app_get): path -> handler, PUBLIC_OPTIONAL vs SESSION_REQUIRED."""

    def test_get_session_public_optional_no_token(self, app_client):
        client, calls = app_client
        # PUBLIC_OPTIONAL: no token -> 200 authenticated:false, NO handler runs.
        resp = client.get(f"{BASE}/session", headers=_phost())
        assert resp.status_code == 200
        assert resp.json() == {"success": True, "authenticated": False}
        assert all(not v for v in calls.values())

    def test_get_session_routes_to_read_app_session(self, app_client):
        client, calls = app_client
        resp = client.get(f"{BASE}/session", headers=_with_session())
        assert resp.status_code == 200
        args = _only_called(calls, "handle_business_read_app_session")
        assert args == [{"business": BUSINESS, "session_token": "session_123"}]
        assert resp.json()["authenticated"] is True

    def test_get_account_public_optional_no_token(self, app_client):
        client, calls = app_client
        resp = client.get(f"{BASE}/account", headers=_phost())
        assert resp.status_code == 200
        assert resp.json() == {"success": True, "authenticated": False}
        assert all(not v for v in calls.values())

    def test_get_account_routes_to_read_app_account(self, app_client):
        client, calls = app_client
        resp = client.get(f"{BASE}/account", headers=_with_session())
        assert resp.status_code == 200
        args = _only_called(calls, "handle_business_read_app_account")
        assert args == [{"business": BUSINESS, "session_token": "session_123"}]

    def test_get_profile_session_required(self, app_client):
        client, calls = app_client
        # SESSION_REQUIRED: no token -> 401, no handler.
        resp = client.get(f"{BASE}/profile", headers=_phost())
        assert resp.status_code == 401
        assert resp.json() == {"success": False, "error": "missing app session"}
        assert all(not v for v in calls.values())

    def test_get_profile_routes_to_read_app_profile(self, app_client):
        client, calls = app_client
        resp = client.get(f"{BASE}/profile", headers=_with_session())
        assert resp.status_code == 200
        _only_called(calls, "handle_business_read_app_profile")

    def test_get_directory_session_required(self, app_client):
        client, calls = app_client
        resp = client.get(f"{BASE}/directory", headers=_phost())
        assert resp.status_code == 401
        assert all(not v for v in calls.values())

    def test_get_directory_list_routes_to_list_entries(self, app_client):
        client, calls = app_client
        resp = client.get(f"{BASE}/directory?limit=8", headers=_with_session())
        assert resp.status_code == 200
        args = _only_called(calls, "handle_business_list_app_directory_entries")
        assert args[0]["business"] == BUSINESS
        assert args[0]["session_token"] == "session_123"
        assert args[0]["limit"] == "8"

    def test_get_directory_me_routes_to_read_entry_without_target(self, app_client):
        client, calls = app_client
        resp = client.get(f"{BASE}/directory/me", headers=_with_session())
        assert resp.status_code == 200
        args = _only_called(calls, "handle_business_read_app_directory_entry")
        # directory/me does NOT pass app_user_id.
        assert "app_user_id" not in args[0]

    def test_get_directory_id_routes_to_read_entry_with_target(self, app_client):
        client, calls = app_client
        resp = client.get(f"{BASE}/directory/u_999", headers=_with_session())
        assert resp.status_code == 200
        args = _only_called(calls, "handle_business_read_app_directory_entry")
        assert args[0]["app_user_id"] == "u_999"

    def test_get_directory_too_deep_is_not_found(self, app_client):
        client, calls = app_client
        resp = client.get(f"{BASE}/directory/a/b", headers=_with_session())
        assert resp.status_code == 404
        assert resp.json() == {"success": False, "error": "not found"}

    def test_get_media_id_session_required(self, app_client):
        client, calls = app_client
        resp = client.get(f"{BASE}/media/m_1", headers=_phost())
        assert resp.status_code == 401
        assert all(not v for v in calls.values())

    def test_get_media_id_routes_to_media_get_bytes(self, app_client):
        client, calls = app_client
        resp = client.get(f"{BASE}/media/m_1", headers=_with_session())
        assert resp.status_code == 200
        assert resp.content == b"img"
        assert resp.headers["content-type"].startswith("image/png")
        assert calls["app_media_get_bytes"][0]["media_id"] == "m_1"

    def test_get_records_list_session_required(self, app_client):
        client, calls = app_client
        resp = client.get(f"{BASE}/records", headers=_phost())
        assert resp.status_code == 401
        assert all(not v for v in calls.values())

    def test_get_records_list_routes_to_list_records(self, app_client):
        client, calls = app_client
        resp = client.get(f"{BASE}/records?type=note&limit=5", headers=_with_session())
        assert resp.status_code == 200
        args = _only_called(calls, "handle_business_list_app_records")
        assert args[0]["record_type"] == "note"
        assert args[0]["limit"] == "5"

    def test_get_record_detail_routes_to_read_record(self, app_client):
        client, calls = app_client
        resp = client.get(f"{BASE}/records/note/r_1", headers=_with_session())
        assert resp.status_code == 200
        args = _only_called(calls, "handle_business_read_app_record")
        assert args[0]["record_type"] == "note"
        assert args[0]["record_id"] == "r_1"

    def test_get_record_by_ref_routes_without_exposing_type_or_id(self, app_client):
        client, calls = app_client
        ref = "tkr_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
        resp = client.get(f"{BASE}/records/by-ref/{ref}", headers=_with_session())
        assert resp.status_code == 200
        args = _only_called(calls, "handle_business_read_app_record")
        assert args[0]["record_ref"] == ref
        assert "record_type" not in args[0]
        assert "record_id" not in args[0]

    def test_get_records_two_parts_is_not_found(self, app_client):
        client, calls = app_client
        resp = client.get(f"{BASE}/records/note", headers=_with_session())
        assert resp.status_code == 404
        assert resp.json() == {"success": False, "error": "not found"}

    def test_get_connections_session_required(self, app_client):
        client, calls = app_client
        resp = client.get(f"{BASE}/connections", headers=_phost())
        assert resp.status_code == 401
        assert all(not v for v in calls.values())

    def test_get_connections_routes_to_list_connections(self, app_client):
        client, calls = app_client
        resp = client.get(f"{BASE}/connections?state=matches&limit=8", headers=_with_session())
        assert resp.status_code == 200
        args = _only_called(calls, "handle_business_list_app_connections")
        assert args[0]["state"] == "matches"

    def test_get_checkout_missing_intent_is_not_found(self, app_client):
        client, calls = app_client
        # checkout GET is PUBLIC_NO_TOKEN (no session read); missing intent -> 404.
        resp = client.get(f"{BASE}/checkout", headers=_phost())
        assert resp.status_code == 404
        assert resp.json() == {"success": False, "error": "not found"}

    def test_get_unknown_route_is_not_found(self, app_client):
        client, calls = app_client
        resp = client.get(f"{BASE}/does-not-exist", headers=_with_session())
        assert resp.status_code == 404
        assert resp.json() == {"success": False, "error": "not found"}
        assert all(not v for v in calls.values())


class TestPostRoutingContract:
    """POST dispatcher (_takyon_app_post): path -> handler + auth tier."""

    def test_post_auth_session_public_no_token(self, app_client):
        client, calls = app_client
        # PUBLIC_NO_TOKEN: mints a session; sets cookie on success.
        resp = client.post(
            f"{BASE}/auth/session",
            json={"access_token": "supa_tok"},
            headers=_phost(),
        )
        assert resp.status_code == 200
        args = _only_called(calls, "handle_business_supabase_login")
        assert args[0]["access_token"] == "supa_tok"
        assert "takyon_app_session=session_cookie_123" in resp.headers.get("set-cookie", "")

    def test_post_checkout_public_no_token(self, app_client):
        client, calls = app_client
        # checkout POST is reachable without a session token (token may be empty).
        resp = client.post(f"{BASE}/checkout", json={"plan_key": "pro"}, headers=_phost())
        assert resp.status_code == 200
        # create_app_checkout fires; with no token the account read is skipped.
        assert calls["handle_business_create_app_checkout"]
        assert not calls["handle_business_read_app_account"]

    def test_post_checkout_reads_account_when_token_present(self, app_client):
        client, calls = app_client
        resp = client.post(f"{BASE}/checkout", json={"plan_key": "pro"}, headers=_with_session())
        assert resp.status_code == 200
        assert calls["handle_business_read_app_account"]
        assert calls["handle_business_create_app_checkout"]

    def test_post_account_cancel_subscription_session_required(self, app_client):
        client, calls = app_client
        resp = client.post(
            f"{BASE}/account", json={"action": "cancel_subscription"}, headers=_phost()
        )
        assert resp.status_code == 401
        assert all(not v for v in calls.values())

    def test_post_account_cancel_subscription_routes(self, app_client):
        client, calls = app_client
        resp = client.post(
            f"{BASE}/account", json={"action": "cancel-subscription"}, headers=_with_session()
        )
        assert resp.status_code == 200
        _only_called(calls, "handle_business_cancel_app_subscription")

    def test_post_account_unsupported_action_is_bad_request(self, app_client):
        client, calls = app_client
        resp = client.post(f"{BASE}/account", json={"action": "nope"}, headers=_with_session())
        assert resp.status_code == 400
        assert resp.json() == {"success": False, "error": "unsupported_account_action"}
        assert all(not v for v in calls.values())

    def test_post_profile_session_required(self, app_client):
        client, calls = app_client
        resp = client.post(f"{BASE}/profile", json={"bio": "x"}, headers=_phost())
        assert resp.status_code == 401
        assert all(not v for v in calls.values())

    def test_post_profile_routes_to_upsert_profile(self, app_client):
        client, calls = app_client
        resp = client.post(f"{BASE}/profile", json={"bio": "x"}, headers=_with_session())
        assert resp.status_code == 200
        _only_called(calls, "handle_business_upsert_app_profile")

    def test_post_directory_me_session_required(self, app_client):
        client, calls = app_client
        resp = client.post(f"{BASE}/directory/me", json={"bio": "x"}, headers=_phost())
        assert resp.status_code == 401
        assert all(not v for v in calls.values())

    def test_post_directory_me_routes_to_upsert_entry(self, app_client):
        client, calls = app_client
        resp = client.post(f"{BASE}/directory/me", json={"bio": "x"}, headers=_with_session())
        assert resp.status_code == 200
        _only_called(calls, "handle_business_upsert_app_directory_entry")

    def test_post_records_query_session_required(self, app_client):
        client, calls = app_client
        resp = client.post(f"{BASE}/records/query", json={"type": "note"}, headers=_phost())
        assert resp.status_code == 401
        assert all(not v for v in calls.values())

    def test_post_records_query_routes_to_list_records(self, app_client):
        client, calls = app_client
        resp = client.post(
            f"{BASE}/records/query", json={"type": "note", "filters": []}, headers=_with_session()
        )
        assert resp.status_code == 200
        _only_called(calls, "handle_business_list_app_records")

    def test_post_records_upsert_session_required(self, app_client):
        client, calls = app_client
        resp = client.post(f"{BASE}/records", json={"type": "note"}, headers=_phost())
        assert resp.status_code == 401
        assert all(not v for v in calls.values())

    def test_post_records_upsert_routes_to_upsert_record(self, app_client):
        client, calls = app_client
        resp = client.post(f"{BASE}/records/note/r_1", json={"title": "t"}, headers=_with_session())
        assert resp.status_code == 200
        args = _only_called(calls, "handle_business_upsert_app_record")
        assert args[0]["record_type"] == "note"
        assert args[0]["record_id"] == "r_1"

    def test_post_record_by_ref_routes_only_opaque_ref(self, app_client):
        client, calls = app_client
        ref = "tkr_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
        resp = client.post(
            f"{BASE}/records/by-ref/{ref}",
            json={"title": "t", "data": {"v": 2}},
            headers=_with_session(),
        )
        assert resp.status_code == 200
        args = _only_called(calls, "handle_business_upsert_app_record")
        assert args[0]["record_ref"] == ref
        assert args[0]["record_id"] is None

    def test_post_record_by_ref_rejects_raw_identifier_overrides(self, app_client):
        client, calls = app_client
        ref = "tkr_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
        resp = client.post(
            f"{BASE}/records/by-ref/{ref}",
            json={"data": {}, "id": "forged"},
            headers=_with_session(),
        )
        assert resp.status_code == 400
        assert resp.json()["error"] == "unsupported_record_ref_update_fields"
        assert all(not values for values in calls.values())

    def test_post_connections_session_required(self, app_client):
        client, calls = app_client
        resp = client.post(f"{BASE}/connections", json={"action": "like"}, headers=_phost())
        assert resp.status_code == 401
        assert all(not v for v in calls.values())

    def test_post_connections_routes_to_act_on_connection(self, app_client):
        client, calls = app_client
        resp = client.post(
            f"{BASE}/connections",
            json={"action": "like", "target_app_user_id": "u_9"},
            headers=_with_session(),
        )
        assert resp.status_code == 200
        _only_called(calls, "handle_business_act_on_app_connection")

    def test_post_usage_session_required(self, app_client):
        client, calls = app_client
        resp = client.post(f"{BASE}/usage", json={"purpose": "x"}, headers=_phost())
        assert resp.status_code == 401
        assert all(not v for v in calls.values())

    def test_post_usage_routes_to_record_usage(self, app_client):
        client, calls = app_client
        resp = client.post(f"{BASE}/usage", json={"purpose": "x"}, headers=_with_session())
        assert resp.status_code == 200
        # usage first reads the account (for the user id/tier), then records usage.
        assert calls["handle_business_read_app_account"]
        assert calls["handle_business_record_app_usage"]

    def test_post_usage_rejects_client_priced_spend(self, app_client):
        client, calls = app_client
        resp = client.post(
            f"{BASE}/usage",
            json={"purpose": "x", "estimated_cost_microusd": 500},
            headers=_with_session(),
        )
        assert resp.status_code == 400
        assert "metered server brokers" in resp.json()["error"]
        # account read happens (to authenticate) but usage is NOT recorded.
        assert not calls["handle_business_record_app_usage"]

    def test_post_email_send_session_required(self, app_client):
        client, calls = app_client
        resp = client.post(f"{BASE}/email/send", json={"subject": "s"}, headers=_phost())
        assert resp.status_code == 401
        assert all(not v for v in calls.values())

    def test_post_email_send_routes_to_send_email(self, app_client):
        client, calls = app_client
        resp = client.post(f"{BASE}/email/send", json={"subject": "s"}, headers=_with_session())
        assert resp.status_code == 200
        _only_called(calls, "handle_business_send_app_email")

    def test_post_media_upload_session_required(self, app_client):
        client, calls = app_client
        resp = client.post(f"{BASE}/media", files={"file": ("a.png", b"x", "image/png")}, headers=_phost())
        assert resp.status_code == 401
        assert all(not v for v in calls.values())

    def test_post_media_upload_routes_to_upload_media(self, app_client):
        client, calls = app_client
        resp = client.post(
            f"{BASE}/media",
            files={"file": ("a.png", b"x", "image/png")},
            headers=_with_session(),
        )
        assert resp.status_code == 200
        _only_called(calls, "handle_business_upload_app_media")

    def test_post_actions_bad_arity_is_not_found(self, app_client):
        client, calls = app_client
        # POST actions with len(parts)!=2 -> 404 (falls through the guard tail).
        resp = client.post(f"{BASE}/actions", json={}, headers=_with_session())
        assert resp.status_code == 404
        assert resp.json() == {"success": False, "error": "not found"}

    def test_post_unknown_route_is_not_found(self, app_client):
        client, calls = app_client
        resp = client.post(f"{BASE}/nope", json={}, headers=_with_session())
        assert resp.status_code == 404
        assert resp.json() == {"success": False, "error": "not found"}
        assert all(not v for v in calls.values())


class TestDeleteRoutingContract:
    """DELETE dispatcher (_takyon_app_delete): path -> handler + auth tier."""

    def test_delete_session_public_no_token_clears_cookie(self, app_client):
        client, calls = app_client
        resp = client.delete(f"{BASE}/session", headers=_phost())
        assert resp.status_code == 200
        assert resp.json() == {"success": True, "revoked": False}
        assert "takyon_app_session=" in resp.headers.get("set-cookie", "")
        assert not calls["handle_business_delete_app_session"]

    def test_delete_session_routes_to_delete_session(self, app_client):
        client, calls = app_client
        resp = client.delete(f"{BASE}/session", headers=_with_session())
        assert resp.status_code == 200
        args = _only_called(calls, "handle_business_delete_app_session")
        assert args[0] == {"business": BUSINESS, "session_token": "session_123"}

    def test_delete_directory_me_session_required(self, app_client):
        client, calls = app_client
        resp = client.delete(f"{BASE}/directory/me", headers=_phost())
        assert resp.status_code == 401
        assert all(not v for v in calls.values())

    def test_delete_directory_me_routes_to_disable_entry(self, app_client):
        client, calls = app_client
        resp = client.delete(f"{BASE}/directory/me", headers=_with_session())
        assert resp.status_code == 200
        _only_called(calls, "handle_business_disable_app_directory_entry")

    def test_delete_record_session_required(self, app_client):
        client, calls = app_client
        resp = client.delete(f"{BASE}/records/note/r_1", headers=_phost())
        assert resp.status_code == 401
        assert all(not v for v in calls.values())

    def test_delete_record_routes_to_delete_record(self, app_client):
        client, calls = app_client
        resp = client.delete(f"{BASE}/records/note/r_1", headers=_with_session())
        assert resp.status_code == 200
        args = _only_called(calls, "handle_business_delete_app_record")
        assert args[0]["record_type"] == "note"
        assert args[0]["record_id"] == "r_1"

    def test_delete_record_by_ref_routes_only_opaque_ref(self, app_client):
        client, calls = app_client
        ref = "tkr_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
        resp = client.delete(f"{BASE}/records/by-ref/{ref}", headers=_with_session())
        assert resp.status_code == 200
        args = _only_called(calls, "handle_business_delete_app_record")
        assert args[0]["record_ref"] == ref
        assert "record_type" not in args[0]
        assert "record_id" not in args[0]

    def test_delete_media_session_required(self, app_client):
        client, calls = app_client
        resp = client.delete(f"{BASE}/media/m_1", headers=_phost())
        assert resp.status_code == 401
        assert all(not v for v in calls.values())

    def test_delete_media_routes_to_delete_media(self, app_client):
        client, calls = app_client
        resp = client.delete(f"{BASE}/media/m_1", headers=_with_session())
        assert resp.status_code == 200
        args = _only_called(calls, "handle_business_delete_app_media")
        assert args[0]["media_id"] == "m_1"

    def test_delete_unknown_route_is_not_found(self, app_client):
        client, calls = app_client
        resp = client.delete(f"{BASE}/nope", headers=_with_session())
        assert resp.status_code == 404
        assert resp.json() == {"success": False, "error": "not found"}
        assert all(not v for v in calls.values())


# ---------------------------------------------------------------------------
# NEGATIVE CONTRACT: everything the subuser plane REFUSES
# ---------------------------------------------------------------------------


class TestOwnerTokenRejectedOnAppPlane:
    """An operator (tk_) bearer token is rejected 403 on GET/POST/DELETE, BEFORE routing."""

    def test_owner_token_rejected_on_get(self, app_client):
        client, calls = app_client
        resp = client.get(f"{BASE}/account", headers=_phost({"Authorization": OWNER_TOKEN}))
        assert resp.status_code == 403
        assert resp.json()["error"] == "owner_token_rejected_on_app_plane"
        assert all(not v for v in calls.values())

    def test_owner_token_rejected_on_post(self, app_client):
        client, calls = app_client
        resp = client.post(
            f"{BASE}/records/note/r_1", json={"title": "t"}, headers=_phost({"Authorization": OWNER_TOKEN})
        )
        assert resp.status_code == 403
        assert resp.json()["error"] == "owner_token_rejected_on_app_plane"
        assert all(not v for v in calls.values())

    def test_owner_token_rejected_on_delete(self, app_client):
        client, calls = app_client
        resp = client.delete(
            f"{BASE}/records/note/r_1", headers=_phost({"Authorization": OWNER_TOKEN})
        )
        assert resp.status_code == 403
        assert resp.json()["error"] == "owner_token_rejected_on_app_plane"
        assert all(not v for v in calls.values())

    def test_owner_token_rejected_even_on_unknown_route(self, app_client):
        client, calls = app_client
        # The owner-token guard precedes route parsing, so even a nonsense path is 403 not 404.
        resp = client.get(f"{BASE}/whatever/deep/path", headers=_phost({"Authorization": OWNER_TOKEN}))
        assert resp.status_code == 403
        assert resp.json()["error"] == "owner_token_rejected_on_app_plane"


class TestProductHostBusinessMismatch:
    """The product host slug must match the {business} in the path, else 404."""

    def test_get_host_business_mismatch_is_not_found(self, app_client):
        client, calls = app_client
        # Host says mathflow, path says otherbiz -> 404, no handler.
        resp = client.get(
            "/api/takyon/apps/otherbiz/account",
            headers={"Host": PRODUCT_HOST, "Cookie": SESSION_COOKIE},
        )
        assert resp.status_code == 404
        assert resp.json() == {"success": False, "error": "not found"}
        assert all(not v for v in calls.values())

    def test_post_host_business_mismatch_is_not_found(self, app_client):
        client, calls = app_client
        resp = client.post(
            "/api/takyon/apps/otherbiz/records/note/r_1",
            json={"title": "t"},
            headers={"Host": PRODUCT_HOST, "Cookie": SESSION_COOKIE},
        )
        assert resp.status_code == 404
        assert all(not v for v in calls.values())


class TestSubuserHostRoleAllowlist:
    """_http_path_allowed_for_host_role — the byte-identical role allowlist truth table.

    This is the deterministic gate the host_role_middleware consults. The refactor must
    NOT touch it, so we pin its complete subuser + operator contract here (mirrors and
    extends the checks in test_web_server.py::test_http_path_allowed_for_host_roles).
    """

    @pytest.fixture(autouse=True)
    def _mod(self):
        import takyon_cli.web_server as web_server

        self.ws = web_server
        self.SUB = web_server._HOST_ROLE_SUBUSER
        self.OP = web_server._HOST_ROLE_OPERATOR
        self.PRODUCT = "latexflow.coscale.app"
        self.DASH = "app.fourmanifold.com"

    def _allowed(self, role, host, path):
        return self.ws._http_path_allowed_for_host_role(role=role, host=host, path=path)

    # --- subuser plane ALLOWS: healthz, product static, app-plane rails ---
    def test_subuser_allows_healthz(self):
        assert self._allowed(self.SUB, self.PRODUCT, "/healthz") is True

    def test_subuser_allows_product_static(self):
        assert self._allowed(self.SUB, self.PRODUCT, "/") is True
        assert self._allowed(self.SUB, self.PRODUCT, "/assets/app.js") is True

    def test_subuser_allows_app_plane_rails(self):
        assert self._allowed(self.SUB, self.PRODUCT, "/api/takyon/apps/latexflow/account") is True
        assert self._allowed(self.SUB, self.PRODUCT, "/api/webhooks/stripe") is True
        assert self._allowed(self.SUB, self.PRODUCT, "/api/product-tls/ask") is True

    # --- subuser plane REFUSES: dashboard api, ws, pty, v1, auth, internal, billing ---
    def test_subuser_refuses_dashboard_api(self):
        assert self._allowed(self.SUB, self.PRODUCT, "/api/status") is False
        assert self._allowed(self.SUB, self.PRODUCT, "/api/ws") is False
        assert self._allowed(self.SUB, self.PRODUCT, "/api/pty") is False
        assert self._allowed(self.SUB, self.PRODUCT, "/api/tui/rpc") is False
        assert self._allowed(self.SUB, self.PRODUCT, "/api/events") is False
        assert self._allowed(self.SUB, self.PRODUCT, "/api/pub/anything") is False

    def test_subuser_refuses_operator_denied_prefixes(self):
        assert self._allowed(self.SUB, self.PRODUCT, "/v1/billing/webhook") is False
        assert self._allowed(self.SUB, self.PRODUCT, "/auth/login") is False
        assert self._allowed(self.SUB, self.PRODUCT, "/internal/ai-gateway/generate") is False
        assert self._allowed(self.SUB, self.PRODUCT, "/billing/portal") is False

    def test_subuser_refuses_everything_on_non_product_host(self):
        # No product-host slug -> subuser role serves nothing (except /healthz, checked below).
        assert self._allowed(self.SUB, self.DASH, "/") is False
        assert self._allowed(self.SUB, self.DASH, "/api/takyon/apps/latexflow/account") is False
        assert self._allowed(self.SUB, self.DASH, "/api/status") is False

    def test_subuser_allows_healthz_on_any_host(self):
        assert self._allowed(self.SUB, self.DASH, "/healthz") is True

    # --- operator plane REFUSES the product app-plane ---
    def test_operator_refuses_app_plane(self):
        assert self._allowed(self.OP, self.DASH, "/api/takyon/apps/latexflow/account") is False
        assert self._allowed(self.OP, self.PRODUCT, "/api/takyon/apps/latexflow/account") is False

    def test_operator_refuses_any_product_host(self):
        assert self._allowed(self.OP, self.PRODUCT, "/") is False
        assert self._allowed(self.OP, self.PRODUCT, "/checkout") is False

    def test_operator_allows_dashboard_and_product_tls(self):
        assert self._allowed(self.OP, self.DASH, "/api/status") is True
        assert self._allowed(self.OP, self.DASH, "/api/product-tls/ask") is True


class TestPublicApiPathContract:
    """_is_public_api_path — which /api/ paths bypass the dashboard session gate.

    The app plane (and stripe webhook) must be public (they carry their own app session
    auth), while everything else under /api/ requires the dashboard session token.
    """

    @pytest.fixture(autouse=True)
    def _mod(self):
        import takyon_cli.web_server as web_server

        self.ws = web_server

    def test_app_plane_is_public(self):
        assert self.ws._is_public_api_path("/api/takyon/apps/latexflow/account") is True

    def test_stripe_webhook_is_public(self):
        assert self.ws._is_public_api_path("/api/webhooks/stripe") is True

    def test_dashboard_api_is_not_public(self):
        assert self.ws._is_public_api_path("/api/ws") is False
        assert self.ws._is_public_api_path("/api/pty") is False
        assert self.ws._is_public_api_path("/api/tui/rpc") is False


class TestSubuserPlaneRefusesOperatorSurfacesEndToEnd:
    """End-to-end (through the running middleware stack) proof that the subuser role
    serves the app plane but 404s every operator/dashboard surface, /api/ws, /api/pty,
    /v1/*, and agent-turn RPC. This is the security keystone of the refactor."""

    @pytest.fixture()
    def subuser_client(self, monkeypatch):
        import takyon_cli.web_server as web_server

        monkeypatch.setenv("TAKYON_HOST_ROLE", "subuser")
        # Neutralize the actual app-plane tools so a served app-plane request doesn't hit PG.
        for name in _APP_TOOL_HANDLERS:
            monkeypatch.setattr(
                web_server,
                name,
                lambda args, _n=name: json.dumps({"success": True, "handler": _n}),
                raising=True,
            )
        web_server.app.state.bound_host = "127.0.0.1"
        client = TestClient(web_server.app)
        try:
            yield client
        finally:
            if hasattr(web_server.app.state, "bound_host"):
                del web_server.app.state.bound_host

    def test_subuser_serves_app_plane_on_product_host(self, subuser_client):
        # session GET with no token -> 200 authenticated:false (reachable = served).
        resp = subuser_client.get(f"{BASE}/session", headers=_phost())
        assert resp.status_code == 200
        assert resp.json() == {"success": True, "authenticated": False}

    def test_subuser_404s_dashboard_status(self, subuser_client):
        resp = subuser_client.get("/api/status", headers=_phost())
        assert resp.status_code == 404

    def test_subuser_404s_ws(self, subuser_client):
        # /api/ws upgrade attempt on the subuser role is dropped by host_role_middleware.
        resp = subuser_client.get("/api/ws", headers=_phost())
        assert resp.status_code == 404

    def test_subuser_404s_pty(self, subuser_client):
        resp = subuser_client.get("/api/pty", headers=_phost())
        assert resp.status_code == 404

    def test_subuser_404s_tui_rpc_agent_turn(self, subuser_client):
        resp = subuser_client.post(
            "/api/tui/rpc",
            json={"jsonrpc": "2.0", "id": "1", "method": "session.create", "params": {}},
            headers=_phost(),
        )
        assert resp.status_code == 404

    def test_subuser_404s_v1_billing(self, subuser_client):
        resp = subuser_client.post("/v1/billing/webhook", json={}, headers=_phost())
        assert resp.status_code == 404

    def test_subuser_404s_operator_home(self, subuser_client):
        resp = subuser_client.get("/api/takyon/operator/home", headers=_phost())
        assert resp.status_code == 404
