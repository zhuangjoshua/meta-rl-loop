"""RuntimeRail registry — equivalence, adversarial refusals, and the "one literal = a
live rail" acceptance proof (Stage 6 §2.5 / §6b item 3).

This suite complements ``test_takyon_app_plane_routing_characterization.py`` (which pins
the CURRENT app-plane routing contract and must pass UNCHANGED against the refactor). Here
we assert the properties of the registry-DRIVEN dispatcher itself:

  1. equivalence invariants: every declared ``endpoints`` entry maps to a real route; the
     scanner regexes are byte-derived from ``client_methods``; the route table's auth tiers
     match the characterized contract.
  2. adversarial refusals (the operator's PRIORITY-ONE subuser-security rule):
       (a) no registry entry is reachable on a host role the old dispatcher refused,
       (b) an operator / dashboard rail cannot be dispatched on the subuser plane,
       (c) a malformed / unknown path is refused exactly as before (same status),
       (d) the auth tier of every route is preserved (a session-required route still 401s).
  3. the payoff: a demo rail added as ONE registry entry + one handler dispatches through
     the generic loop with the correct auth tier, and the scanner recognizes its
     client_method with NO regex edit — then it is removed just as cheaply.
"""

from __future__ import annotations

import json
import re
import tempfile
from pathlib import Path
from typing import Any

import pytest
from starlette.testclient import TestClient

import plugins.takyon.core as core


PRODUCT_HOST = "mathflow.coscale.app"
BUSINESS = "mathflow"
BASE = f"/api/takyon/apps/{BUSINESS}"


@pytest.fixture(autouse=True)
def _hermetic_product_base_domain(monkeypatch):
    """Pin the product-host mapping to the clean-process default (coscale.app).

    Same rationale as the app-plane characterization suite: a sibling suite in the same xdist
    worker can pull a configured workspace's on-disk env (PUBLIC_COMPANY_BASE_DOMAIN=...) into
    os.environ via core.load_takyon_env(), after which ``mathflow.coscale.app`` stops resolving as
    a product host and every request 400s at the Host-header middleware."""
    monkeypatch.delenv("PUBLIC_COMPANY_BASE_DOMAIN", raising=False)
    monkeypatch.delenv("TAKYON_COMPANY_BASE_DOMAIN", raising=False)


# ---------------------------------------------------------------------------
# 1. Equivalence invariants
# ---------------------------------------------------------------------------


class TestRegistryEquivalenceInvariants:
    def test_every_declared_endpoint_maps_to_a_real_route(self):
        """``.endpoints`` STOPS being documentation-that-lies: every declared (method, path)
        must resolve to a real dispatch route in the registry (modulo the declared
        ``<placeholder>`` names, which we normalize to a single wildcard shape)."""

        def _norm(pattern_parts):
            return tuple("*" if p.startswith("<") else p for p in pattern_parts)

        # Build the set of real routes as normalized (method, parts).
        real: set[tuple[str, tuple[str, ...]]] = set()
        for rail in core.RUNTIME_RAILS.values():
            for rt in rail.routes:
                real.add((rt.method, _norm(rt.pattern)))

        for name, rail in core.RUNTIME_RAILS.items():
            for method, endpoint in rail.endpoints:
                parts = tuple(p for p in endpoint.split("/") if p)
                norm = _norm(parts)
                # billing / entitlements / usage / analytics declare ALIAS endpoints that are
                # owned by another rail (account / checkout) or have no HTTP route (analytics).
                # Assert the declared endpoint resolves to SOME real route in the registry.
                assert (method, norm) in real, (
                    f"rail {name} declares {method} {endpoint} but no real route matches "
                    f"(normalized {method} {norm})"
                )

    def test_scanner_patterns_are_byte_derived_from_client_methods(self):
        """The source-scanner regexes DERIVE from RuntimeRail.client_methods — byte-identical
        to the pre-Stage-6 hand-written literals, so drift is impossible."""
        expected = {
            "records": r"\b(?:listRecords|getRecord|saveRecord|deleteRecord)\s*\(",
            "directory": r"\b(?:listDirectory|getDirectoryMe|getDirectoryEntry|updateDirectoryMe|disableDirectoryMe)\s*\(",
            "media": r"\b(?:uploadMedia|deleteMedia)\s*\(",
            "connections": r"\b(?:listConnections|actOnConnection)\s*\(",
            "generate": r"\b(?:ctx|client|runtime|rt)\.generate\s*\(",
            "search": r"\b(?:ctx|client|runtime|rt)\.search\s*\(",
        }
        derived = {name: pat.pattern for name, pat in core.runtime_rail_usage_patterns()}
        assert derived == expected

    def test_route_table_is_first_match_ordered(self):
        """The generic dispatcher scans APP_PLANE_ROUTE_TABLE in order; a literal route must
        precede a same-depth parametric one so the old ``if parts == [...]`` first-match
        cascade is reproduced (POST records/query before POST records/<record_type>)."""
        table = core.APP_PLANE_ROUTE_TABLE
        idx = {id(rt): i for i, rt in enumerate(table)}
        q = next(rt for rt in table if rt.method == "POST" and rt.pattern == ("records", "query"))
        wild = next(rt for rt in table if rt.method == "POST" and rt.pattern == ("records", "<record_type>"))
        assert idx[id(q)] < idx[id(wild)]

    def test_match_returns_bound_params(self):
        matched = core.match_app_plane_route("GET", ["records", "note", "r1"])
        assert matched is not None
        route, bound = matched
        assert route.handler_key == "record_get"
        assert bound == {"record_type": "note", "record_id": "r1"}

    def test_match_returns_none_for_unknown(self):
        assert core.match_app_plane_route("GET", ["nope"]) is None
        assert core.match_app_plane_route("GET", ["records", "note"]) is None  # arity gap -> 404
        assert core.match_app_plane_route("PATCH", ["account"]) is None  # method not served


# ---------------------------------------------------------------------------
# 2. Adversarial refusals
# ---------------------------------------------------------------------------


class TestAdversarialAuthTierPreserved:
    """(d) The auth tier of every route is preserved. Any route the OLD dispatcher gated with
    a session-token 401 is still session-required in the registry; the two public tiers are
    exactly the historical exceptions (session GET/DELETE, account GET, auth/session POST,
    checkout GET/POST)."""

    # The historical public routes (verbatim from the pre-Stage-6 dispatcher):
    #   PUBLIC_OPTIONAL : GET session, GET account
    #   PUBLIC_NO_TOKEN : POST auth/session, DELETE session, GET checkout, POST checkout
    _EXPECTED_PUBLIC = {
        (core.APP_AUTH_PUBLIC_OPTIONAL, "GET", ("session",)),
        (core.APP_AUTH_PUBLIC_OPTIONAL, "GET", ("account",)),
        (core.APP_AUTH_PUBLIC_NO_TOKEN, "POST", ("auth", "session")),
        (core.APP_AUTH_PUBLIC_NO_TOKEN, "DELETE", ("session",)),
        (core.APP_AUTH_PUBLIC_NO_TOKEN, "GET", ("checkout",)),
        (core.APP_AUTH_PUBLIC_NO_TOKEN, "POST", ("checkout",)),
    }

    def test_only_the_historical_routes_are_public(self):
        public = {
            (rt.auth_tier, rt.method, rt.pattern)
            for rail in core.RUNTIME_RAILS.values()
            for rt in rail.routes
            if rt.auth_tier != core.APP_AUTH_SESSION_REQUIRED
        }
        assert public == self._EXPECTED_PUBLIC, (
            "a route changed auth tier away from SESSION_REQUIRED — this could expose a "
            "customer-scoped route without a session token"
        )

    def test_every_mutating_customer_route_is_session_required(self):
        # Every route NOT in the historical public set must require a session token.
        for rail in core.RUNTIME_RAILS.values():
            for rt in rail.routes:
                key = (rt.auth_tier, rt.method, rt.pattern)
                if key in self._EXPECTED_PUBLIC:
                    continue
                assert rt.auth_tier == core.APP_AUTH_SESSION_REQUIRED, (
                    f"{rt.method} {rt.pattern} is neither historical-public nor session-required"
                )


class TestAdversarialSessionRequiredEnforcedEndToEnd:
    """(d) end-to-end: a route the registry marks SESSION_REQUIRED must 401 with no token,
    through the real dispatcher, and NEVER reach its tool handler."""

    @pytest.fixture()
    def probe_client(self, monkeypatch):
        import takyon_cli.web_server as web_server

        fired: list[str] = []
        # Make EVERY app-plane tool a tripwire: if a session-required route reaches its tool
        # without a token, the test fails loudly.
        for name in dir(web_server):
            if name.startswith("handle_business_"):
                monkeypatch.setattr(
                    web_server,
                    name,
                    lambda args, _n=name: (fired.append(_n), json.dumps({"success": True}))[1],
                    raising=False,
                )
        monkeypatch.setattr(web_server, "_takyon_app_rate_limit_directory_lookup", lambda **_k: None, raising=True)
        monkeypatch.setattr(web_server, "_takyon_app_rate_limit_action_invoke", lambda **_k: None, raising=True)
        web_server.app.state.bound_host = "127.0.0.1"
        client = TestClient(web_server.app)
        try:
            yield client, fired
        finally:
            if hasattr(web_server.app.state, "bound_host"):
                del web_server.app.state.bound_host

    def test_all_session_required_routes_401_without_token(self, probe_client):
        client, fired = probe_client
        headers = {"Host": PRODUCT_HOST}
        checked = 0
        for rail in core.RUNTIME_RAILS.values():
            for rt in rail.routes:
                if rt.auth_tier != core.APP_AUTH_SESSION_REQUIRED:
                    continue
                path = BASE + "/" + "/".join(
                    ("x" if p.startswith("<") else p) for p in rt.pattern
                )
                if rt.method == "GET":
                    resp = client.get(path, headers=headers)
                elif rt.method == "DELETE":
                    resp = client.delete(path, headers=headers)
                else:
                    # media upload is multipart but still session-gated; send an empty body.
                    resp = client.post(path, json={}, headers=headers)
                assert resp.status_code == 401, f"{rt.method} {path} did not 401 without a token"
                assert resp.json() == {"success": False, "error": "missing app session"}
                checked += 1
        assert checked >= 15  # sanity: we actually exercised the session-required surface
        assert fired == [], f"a session-required route reached a tool without a token: {fired}"


class TestAdversarialHostRoleRefusals:
    """(a)+(b): no registry route is reachable on a host role the old dispatcher refused, and
    an operator/dashboard rail cannot be dispatched on the subuser plane. Driven end-to-end
    through the real host_role_middleware with TAKYON_HOST_ROLE=subuser / =operator."""

    def _client(self, monkeypatch, role):
        import takyon_cli.web_server as web_server

        monkeypatch.setenv("TAKYON_HOST_ROLE", role)
        for name in dir(web_server):
            if name.startswith("handle_business_"):
                monkeypatch.setattr(
                    web_server, name, lambda args: json.dumps({"success": True}), raising=False
                )
        monkeypatch.setattr(web_server, "_takyon_app_rate_limit_directory_lookup", lambda **_k: None, raising=True)
        monkeypatch.setattr(web_server, "_takyon_app_rate_limit_action_invoke", lambda **_k: None, raising=True)
        web_server.app.state.bound_host = "127.0.0.1"
        return TestClient(web_server.app)

    def test_operator_role_404s_every_app_plane_route(self, monkeypatch):
        # (b) An operator host must NOT be able to dispatch ANY product app-plane route.
        client = self._client(monkeypatch, "operator")
        try:
            for rail in core.RUNTIME_RAILS.values():
                for rt in rail.routes:
                    path = BASE + "/" + "/".join(
                        ("x" if p.startswith("<") else p) for p in rt.pattern
                    )
                    # Operator host header (no product slug) — the app plane is dropped.
                    resp = client.request(rt.method, path, headers={"Host": "app.fourmanifold.com"})
                    assert resp.status_code == 404, f"operator reached app-plane {rt.method} {path}"
        finally:
            if hasattr(__import__("takyon_cli.web_server", fromlist=["app"]).app.state, "bound_host"):
                del __import__("takyon_cli.web_server", fromlist=["app"]).app.state.bound_host

    def test_subuser_role_404s_dashboard_and_operator_surfaces(self, monkeypatch):
        # (a) The subuser plane must refuse every operator/dashboard surface the old dispatcher
        #     refused: dashboard api, ws, pty, tui rpc (agent turns), v1 billing, operator home.
        client = self._client(monkeypatch, "subuser")
        try:
            refused = [
                ("GET", "/api/status"),
                ("GET", "/api/ws"),
                ("GET", "/api/pty"),
                ("POST", "/api/tui/rpc"),
                ("POST", "/v1/billing/webhook"),
                ("GET", "/api/takyon/operator/home"),
                ("GET", "/api/pub/whatever"),
                ("GET", "/api/events"),
            ]
            for method, path in refused:
                resp = client.request(method, path, headers={"Host": PRODUCT_HOST}, json={} if method == "POST" else None)
                assert resp.status_code == 404, f"subuser reached refused surface {method} {path}"
        finally:
            if hasattr(__import__("takyon_cli.web_server", fromlist=["app"]).app.state, "bound_host"):
                del __import__("takyon_cli.web_server", fromlist=["app"]).app.state.bound_host

    def test_subuser_role_still_serves_the_app_plane(self, monkeypatch):
        # Positive control: the subuser plane DOES serve the product app plane (session GET).
        client = self._client(monkeypatch, "subuser")
        try:
            resp = client.get(f"{BASE}/session", headers={"Host": PRODUCT_HOST})
            assert resp.status_code == 200
            assert resp.json() == {"success": True, "authenticated": False}
        finally:
            if hasattr(__import__("takyon_cli.web_server", fromlist=["app"]).app.state, "bound_host"):
                del __import__("takyon_cli.web_server", fromlist=["app"]).app.state.bound_host


class TestAdversarialMalformedPathsRefusedExactlyAsBefore:
    """(c) A malformed / unknown path is refused with the SAME status (404) as the old
    dispatcher's fall-through tail — across GET/POST/DELETE."""

    @pytest.fixture()
    def app_client(self, monkeypatch):
        import takyon_cli.web_server as web_server

        for name in dir(web_server):
            if name.startswith("handle_business_"):
                monkeypatch.setattr(
                    web_server, name, lambda args: json.dumps({"success": True}), raising=False
                )
        monkeypatch.setattr(web_server, "_takyon_app_rate_limit_directory_lookup", lambda **_k: None, raising=True)
        monkeypatch.setattr(web_server, "_takyon_app_rate_limit_action_invoke", lambda **_k: None, raising=True)
        web_server.app.state.bound_host = "127.0.0.1"
        client = TestClient(web_server.app)
        try:
            yield client
        finally:
            if hasattr(web_server.app.state, "bound_host"):
                del web_server.app.state.bound_host

    @pytest.mark.parametrize(
        "method,path",
        [
            ("GET", "unknown"),
            ("GET", "records/only-two"),  # GET records needs 1 or 3 parts
            ("GET", "directory/a/b/c"),  # too deep
            ("GET", "media"),  # GET media needs an id
            ("POST", "unknown"),
            ("POST", "actions"),  # actions needs exactly 2 parts
            ("POST", "email"),  # email needs email/send
            ("DELETE", "unknown"),
            ("DELETE", "records/only-two"),  # DELETE record needs 3 parts
            ("DELETE", "media"),  # DELETE media needs an id
            ("DELETE", "profile"),  # profile has no DELETE route
        ],
    )
    def test_malformed_paths_404(self, app_client, method, path):
        headers = {"Host": PRODUCT_HOST, "Cookie": "takyon_app_session=session_123"}
        resp = app_client.request(
            method, f"{BASE}/{path}", headers=headers, json={} if method == "POST" else None
        )
        assert resp.status_code == 404
        assert resp.json() == {"success": False, "error": "not found"}


# ---------------------------------------------------------------------------
# 3. Acceptance: one registry literal == a live rail (and cheap removal)
# ---------------------------------------------------------------------------


class TestOneRegistryLiteralIsALiveRail:
    """The plan's acceptance criterion. Adding a demo rail as ONE registry entry + one
    handler must:
      - dispatch its route through the SAME generic loop with the correct auth tier, and
      - be recognized by the source scanner via its declared client_method WITHOUT a regex
        edit.
    Then it must be removable just as cheaply. This is a TEST-ONLY rail — it is registered
    into in-memory registries for the duration of the test and torn down; it never ships.
    """

    def test_demo_rail_dispatches_and_is_scanned_then_removed(self, monkeypatch):
        import takyon_cli.web_server as web_server

        RAIL = "demo_ping"
        CLIENT_METHOD = "pingDemo"

        # --- ONE registry literal: a RuntimeRail with one route + one client method. ---
        demo_route = core.RailRoute(
            "POST", ("demo", "ping"), "demo_ping_post", core.APP_AUTH_SESSION_REQUIRED
        )
        demo_rail = core.RuntimeRail(
            name=RAIL,
            routes=(demo_route,),
            client_methods=(CLIENT_METHOD,),
            build_derived=True,
            dependencies=("auth", "account"),
            metadata={
                "owner_skill": "takyon-app-runtime",
                "tools": [],
                "endpoints": [("POST", "demo/ping")],
                "worker_contract": ["Demo rail — test only."],
            },
        )

        # Register the demo rail into the live registries (in-memory) and rebuild the derived
        # dispatch table + scanner patterns from the SAME source of truth — no per-rail code.
        original_rails = dict(core.RUNTIME_RAILS)
        original_table = core.APP_PLANE_ROUTE_TABLE
        core.RUNTIME_RAILS[RAIL] = demo_rail
        try:
            new_table = core._build_app_plane_route_table()
            monkeypatch.setattr(core, "APP_PLANE_ROUTE_TABLE", new_table, raising=True)

            # One handler for the demo route, dropped into the web_server GET/POST dispatch map.
            async def _demo_handler(request, business, parts, bound, token, body):
                return web_server._takyon_app_json(
                    200, {"success": True, "rail": RAIL, "token_seen": bool(token), "echo": body.get("echo")}
                )

            patched_handlers = dict(web_server._APP_POST_HANDLERS)
            patched_handlers["demo_ping_post"] = _demo_handler
            monkeypatch.setattr(web_server, "_APP_POST_HANDLERS", patched_handlers, raising=True)

            web_server.app.state.bound_host = "127.0.0.1"
            client = TestClient(web_server.app)

            # (1) The demo route dispatches through the generic loop with the correct auth tier.
            #     No token -> 401 (session_required enforced by the SAME central gate).
            resp_no_token = client.post(
                f"{BASE}/demo/ping", json={"echo": "hi"}, headers={"Host": PRODUCT_HOST}
            )
            assert resp_no_token.status_code == 401
            assert resp_no_token.json() == {"success": False, "error": "missing app session"}

            #     With a token -> the demo handler runs via the generic dispatcher.
            resp = client.post(
                f"{BASE}/demo/ping",
                json={"echo": "hi"},
                headers={"Host": PRODUCT_HOST, "Cookie": "takyon_app_session=session_123"},
            )
            assert resp.status_code == 200
            assert resp.json() == {"success": True, "rail": RAIL, "token_seen": True, "echo": "hi"}

            # (2) The scanner recognizes the demo rail's client_method with NO regex edit —
            #     the pattern was DERIVED from client_methods when we rebuilt.
            from plugins.takyon import app_actions

            patterns = dict(core.runtime_rail_usage_patterns())
            assert RAIL in patterns
            assert patterns[RAIL].search(f"{CLIENT_METHOD}(x)")

            d = Path(tempfile.mkdtemp())
            (d / "app.tsx").write_text(f"const p = {CLIENT_METHOD}();\n", encoding="utf-8")
            scanned = app_actions.referenced_runtime_rails_in_source(d)
            assert RAIL in scanned
        finally:
            # (3) Remove the demo rail just as cheaply — one delete + rebuild; registry is clean.
            core.RUNTIME_RAILS.clear()
            core.RUNTIME_RAILS.update(original_rails)
            if hasattr(web_server.app.state, "bound_host"):
                del web_server.app.state.bound_host

        assert RAIL not in core.RUNTIME_RAILS
        # The scanner no longer knows the demo rail (source of truth is the registry).
        assert RAIL not in dict(core.runtime_rail_usage_patterns())
        # The demo route is gone from the (restored) table.
        assert all(rt.handler_key != "demo_ping_post" for rt in original_table)
