"""UC4 REAL-Shopify acceptance wiring — the rig-independent half (modularization plan §2.7
Stage 5, "Shopify slices"; §4p UC4 acceptance).

Covers, without DB or network:
  * the explicit plan_name→fee map (fail-closed on unknown; partner-dev needs an EXPLICIT fee);
  * the X-Shopify-Hmac-Sha256 verifier (base64 HMAC-SHA256 over the raw body);
  * the content-derived dedup id (headers are not HMAC-covered, so the id must not use them);
  * `PlanComposition` serialization round-trip (what makes webhook recompose possible);
  * `connect_shopify` through a FAKE Composio transport: fail-closed when COMPOSIO_API_KEY is
    absent, adopt vs initiate resolution;
  * the shop-plan read parse + cost-basis output;
  * registration: the business tool exists with requires_api=['composio'] and rides plugin.yaml;
    the public webhook route exists and sits in the app-plane/public allowlists exactly like
    Stripe's (no auth bypass added — the subuser role table stays byte-identical otherwise).

The PG half (dedup replay = one effect; recompose mints the next plan_key version; grandfather
untouched; safebox route HMAC-before-processing) lives in test_takyon_shopify_webhook_pg.py.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json

import pytest

from plugins.takyon import plan_composition as pc
from plugins.takyon import shopify_util as su

SHOP = "ourmanifold-uc4-test.myshopify.com"


# ── plan_name → fee map (fail-closed) ─────────────────────────────────────────────


def test_fee_map_known_plans():
    assert su.plan_fee_microusd("basic") == 39_000_000
    assert su.plan_fee_microusd("Shopify") == 105_000_000
    assert su.plan_fee_microusd("professional") == 105_000_000
    assert su.plan_fee_microusd("Advanced") == 399_000_000
    assert su.plan_fee_microusd("unlimited") == 399_000_000


def test_fee_map_unknown_plan_refuses():
    with pytest.raises(su.ShopifyPlanUnmapped, match="refusing to"):
        su.plan_fee_microusd("shopify_plus")
    with pytest.raises(su.ShopifyPlanUnmapped):
        su.plan_fee_microusd("trial")
    with pytest.raises(su.ShopifyPlanUnmapped):
        su.plan_fee_microusd("")


def test_partner_dev_plan_requires_explicit_fee():
    # A dev store has no public price — with no configured fee it REFUSES (never a guess)...
    with pytest.raises(su.ShopifyPlanUnmapped, match="partner"):
        su.plan_fee_microusd("partner_test")
    with pytest.raises(su.ShopifyPlanUnmapped):
        su.plan_fee_microusd("Developer Preview")
    # ...and with an explicit configured fee it resolves to exactly that fee.
    assert su.plan_fee_microusd("partner_test", partner_dev_fee_microusd=5_000_000) == 5_000_000
    assert su.plan_fee_microusd("Partner test account", partner_dev_fee_microusd=0) == 0


def test_cost_basis_is_fixed_monthly():
    basis = su.plan_fee_cost_basis("basic")
    assert isinstance(basis, pc.CostBasis)
    assert basis.kind == "fixed"
    assert basis.fee_microusd_month == 39_000_000


def test_shop_domain_validation():
    assert su.normalize_shop_domain(f"https://{SHOP}/") == SHOP
    assert su.normalize_shop_domain(SHOP.upper()) == SHOP
    for bad in ("evil.com", "a.myshopify.com.evil.com", "", "https://x", "x.myshopify.dev"):
        with pytest.raises(su.ShopifyError):
            su.normalize_shop_domain(bad)


# ── HMAC verifier ─────────────────────────────────────────────────────────────────


def _sign(body: str, secret: str) -> str:
    return base64.b64encode(
        hmac.new(secret.encode(), body.encode(), hashlib.sha256).digest()
    ).decode()


def test_hmac_accepts_valid_signature():
    body = json.dumps({"myshopify_domain": SHOP, "plan_name": "basic"})
    assert su.verify_webhook_hmac(body, _sign(body, "shpss_secret"), "shpss_secret") is None


def test_hmac_rejects_forged_signature():
    body = json.dumps({"myshopify_domain": SHOP, "plan_name": "basic"})
    with pytest.raises(su.ShopifyWebhookInvalidSignature):
        su.verify_webhook_hmac(body, _sign(body, "attacker"), "shpss_secret")
    with pytest.raises(su.ShopifyWebhookInvalidSignature):
        su.verify_webhook_hmac(body + " ", _sign(body, "shpss_secret"), "shpss_secret")
    with pytest.raises(su.ShopifyWebhookInvalidSignature):
        su.verify_webhook_hmac(body, "", "shpss_secret")


def test_hmac_missing_secret_is_unconfigured_never_pass():
    body = "{}"
    with pytest.raises(su.ShopifyWebhookUnconfigured):
        su.verify_webhook_hmac(body, _sign(body, "x"), "")


def test_dedup_id_is_content_derived_not_header_derived():
    """Shopify's HMAC covers only the body — header ids are attacker-controllable on a replayed
    body. The dedup id must therefore be a pure function of (topic, body): identical content →
    identical id (replay = one effect BY CONSTRUCTION), different content → different id."""
    body_a = json.dumps({"myshopify_domain": SHOP, "plan_name": "basic"})
    body_b = json.dumps({"myshopify_domain": SHOP, "plan_name": "unlimited"})
    assert su.webhook_dedup_event_id("shop/update", body_a) == su.webhook_dedup_event_id(
        "shop/update", body_a
    )
    assert su.webhook_dedup_event_id("shop/update", body_a) != su.webhook_dedup_event_id(
        "shop/update", body_b
    )
    assert su.webhook_dedup_event_id("shop/update", body_a) != su.webhook_dedup_event_id(
        "app/uninstalled", body_a
    )


# ── composition serialization round-trip ──────────────────────────────────────────


def _composition() -> pc.PlanComposition:
    return pc.PlanComposition(
        components=(
            pc.PricedComponent(
                kind="ai_allowance",
                key="ai_allowance",
                cost_basis=pc.CostBasis.metered(
                    pc.MeteredAllowance(
                        model="claude-opus-4-8",
                        provider="anthropic",
                        input_tokens=1_000_000,
                        output_tokens=1_000_000,
                    )
                ),
                grants={"model_allowlist": ["claude-opus-4-8"], "features": {"ai_chat": True}},
            ),
            pc.PricedComponent(
                kind="external_fee",
                key=su.SHOPIFY_STORE_COMPONENT_KEY,
                cost_basis=pc.CostBasis.fixed(39_000_000),
                grants={"rail": "shopify"},
            ),
        ),
        margin_policy=pc.MarginPolicy(margin_floor=0.30, rounding="dollar"),
    )


def test_composition_round_trips_through_dict():
    original = _composition()
    data = pc.composition_to_dict(original)
    rebuilt = pc.composition_from_dict(json.loads(json.dumps(data)))  # through real JSON
    assert rebuilt == original
    # and the rebuilt composition composes to the SAME derived economics
    assert pc.compose_plan(rebuilt) == pc.compose_plan(original)


def test_composition_from_dict_fails_closed_on_malformed():
    with pytest.raises(pc.InvalidComponent):
        pc.composition_from_dict({"components": []})
    with pytest.raises(pc.InvalidComponent):
        pc.composition_from_dict({"components": [{"key": "x", "kind": "external_fee"}]})
    with pytest.raises(pc.InvalidComponent):
        pc.composition_from_dict(
            {"components": [{"key": "x", "kind": "f", "cost_basis": {"kind": "percentage"}}]}
        )
    with pytest.raises(pc.InvalidMarginPolicy):
        pc.composition_from_dict(
            {
                "components": [
                    {"key": "x", "kind": "f", "cost_basis": {"kind": "fixed", "fee_microusd_month": 1}}
                ],
                "margin_policy": {"margin_floor": 1.5},
            }
        )


# ── connect_shopify through a FAKE Composio transport ─────────────────────────────


def _patch_transport(monkeypatch, handler):
    from plugins.takyon import composio_distribution as cd

    monkeypatch.setattr(cd, "_request", handler)


def test_connect_fails_closed_without_composio_key(monkeypatch):
    """COMPOSIO_API_KEY absent (local plane spelling) → ShopifyComposioUnconfigured BEFORE any
    connection state exists; the message carries the *_unconfigured marker."""
    from plugins.takyon import composio_distribution as cd

    def _missing_key(method, path, **kwargs):
        raise cd.ComposioDistributionError("missing COMPOSIO_API_KEY")

    _patch_transport(monkeypatch, _missing_key)
    with pytest.raises(su.ShopifyComposioUnconfigured, match="shopify_composio_unconfigured"):
        su.connect_shopify(shop_domain=SHOP, user_id="takyon_prod_operator")


def test_connect_fails_closed_when_safebox_authority_unavailable(monkeypatch):
    from plugins.takyon import composio_distribution as cd, safebox

    def _no_authority(method, path, **kwargs):
        raise safebox.SafeboxAuthorityUnavailable("no authority")

    _patch_transport(monkeypatch, _no_authority)
    with pytest.raises(su.ShopifyComposioUnconfigured):
        su.connect_shopify(shop_domain=SHOP, user_id="takyon_prod_operator")


def test_connect_adopts_single_active_account(monkeypatch):
    def _one_active(method, path, **kwargs):
        assert (method, path) == ("GET", su.COMPOSIO_CONNECTED_ACCOUNTS_PATH)
        return {
            "items": [
                {"id": "ca_shop_1", "status": "ACTIVE", "toolkit": {"slug": "shopify"}},
            ]
        }

    _patch_transport(monkeypatch, _one_active)
    out = su.connect_shopify(shop_domain=SHOP, user_id="takyon_prod_operator")
    assert out["connected_account_id"] == "ca_shop_1"
    assert out["status"] == "active"


def test_connect_disambiguates_by_shop_domain(monkeypatch):
    def _two_active(method, path, **kwargs):
        return {
            "items": [
                {"id": "ca_a", "status": "ACTIVE", "toolkit": {"slug": "shopify"},
                 "params": {"subdomain": "other-store"}},
                {"id": "ca_b", "status": "ACTIVE", "toolkit": {"slug": "shopify"},
                 "params": {"subdomain": "ourmanifold-uc4-test"}},
            ]
        }

    _patch_transport(monkeypatch, _two_active)
    out = su.connect_shopify(shop_domain=SHOP, user_id="takyon_prod_operator")
    assert out["connected_account_id"] == "ca_b"


def test_connect_ambiguous_accounts_refuse(monkeypatch):
    def _two_active(method, path, **kwargs):
        return {
            "items": [
                {"id": "ca_a", "status": "ACTIVE", "toolkit": {"slug": "shopify"}},
                {"id": "ca_b", "status": "ACTIVE", "toolkit": {"slug": "shopify"}},
            ]
        }

    _patch_transport(monkeypatch, _two_active)
    with pytest.raises(su.ShopifyConnectionError, match="explicitly"):
        su.connect_shopify(shop_domain=SHOP, user_id="takyon_prod_operator")


def test_connect_initiates_when_no_active_account(monkeypatch):
    calls = []

    def _initiate(method, path, **kwargs):
        calls.append((method, path, kwargs))
        if (method, path) == ("GET", su.COMPOSIO_CONNECTED_ACCOUNTS_PATH):
            return {"items": []}
        if (method, path) == ("GET", su.COMPOSIO_AUTH_CONFIGS_PATH):
            return {"items": [{"id": "ac_shopify_1"}]}
        if (method, path) == ("POST", su.COMPOSIO_CONNECTED_ACCOUNTS_PATH):
            body = kwargs.get("json_body") or {}
            assert body["auth_config"] == {"id": "ac_shopify_1"}
            assert body["connection"]["user_id"] == "takyon_prod_operator"
            # Live-acceptance finding (2026-07-03): Shopify's connected-account creation REQUIRES
            # the store subdomain under connection.data — Composio 400s "Missing required fields:
            # Store Subdomain" without it. subdomain = the shop name minus ".myshopify.com".
            assert body["connection"]["data"] == {"subdomain": "ourmanifold-uc4-test"}
            return {"id": "ca_new", "status": "INITIALIZING", "redirect_url": "https://composio/oauth"}
        raise AssertionError(f"unexpected call {method} {path}")

    _patch_transport(monkeypatch, _initiate)
    out = su.connect_shopify(shop_domain=SHOP, user_id="takyon_prod_operator")
    assert out == {
        "connected_account_id": "ca_new",
        "status": "initiated",
        "auth_config_id": "ac_shopify_1",
        "source": "initiated",
        "redirect_url": "https://composio/oauth",
    }


def test_connect_explicit_account_id_wins_without_any_call(monkeypatch):
    def _boom(method, path, **kwargs):
        raise AssertionError("no Composio call expected for an explicit id")

    _patch_transport(monkeypatch, _boom)
    out = su.connect_shopify(
        shop_domain=SHOP, user_id="u", connected_account_id="ca_explicit"
    )
    assert out["connected_account_id"] == "ca_explicit"


# ── shop-plan read (cost-basis reader) ─────────────────────────────────────────────


def test_read_shop_plan_parses_proxy_envelope(monkeypatch):
    def _proxy(method, path, **kwargs):
        assert (method, path) == ("POST", su.COMPOSIO_PROXY_TOOL_PATH)
        body = kwargs["json_body"]
        assert body["connected_account_id"] == "ca_shop_1"
        assert body["endpoint"] == (
            f"https://{SHOP}/admin/api/{su.SHOPIFY_ADMIN_API_VERSION}/graphql.json"
        )
        assert body["body"] == {"query": su.SHOPIFY_SHOP_PLAN_GRAPHQL_QUERY}
        # Composio proxy envelope: upstream GraphQL JSON nested under data.
        return {
            "successful": True,
            "data": {
                "data": {
                    "shop": {
                        "myshopifyDomain": SHOP,
                        "plan": {
                            "displayName": "Advanced",
                            "partnerDevelopment": False,
                            "shopifyPlus": False,
                        },
                    }
                }
            },
        }

    _patch_transport(monkeypatch, _proxy)
    basis, info = su.read_shop_plan_cost_basis(shop_domain=SHOP, connected_account_id="ca_shop_1")
    assert info["plan_name"] == "Advanced"
    assert basis.kind == "fixed"
    assert basis.fee_microusd_month == 399_000_000


def test_read_shop_plan_partner_dev_without_fee_refuses(monkeypatch):
    def _proxy(method, path, **kwargs):
        return {
            "data": {
                "shop": {
                    "myshopifyDomain": SHOP,
                    "plan": {"displayName": "Developer Preview", "partnerDevelopment": True},
                }
            }
        }

    _patch_transport(monkeypatch, _proxy)
    with pytest.raises(su.ShopifyPlanUnmapped):
        su.read_shop_plan_cost_basis(shop_domain=SHOP, connected_account_id="ca_shop_1")
    # with the explicit configured dev fee it resolves
    basis, _ = su.read_shop_plan_cost_basis(
        shop_domain=SHOP, connected_account_id="ca_shop_1", partner_dev_fee_microusd=7_000_000
    )
    assert basis.fee_microusd_month == 7_000_000


def test_read_shop_plan_unparseable_fails_closed(monkeypatch):
    _patch_transport(monkeypatch, lambda method, path, **kwargs: {"data": {"nope": True}})
    with pytest.raises(su.ShopifyConnectionError, match="guess"):
        su.read_shop_plan(shop_domain=SHOP, connected_account_id="ca_shop_1")


# ── plan_key versioning ───────────────────────────────────────────────────────────


def test_plan_key_family_and_next_version():
    assert su.plan_key_family("starter") == ("starter", 1)
    assert su.plan_key_family("starter-v2") == ("starter", 2)
    assert su.plan_key_family("starter-v10") == ("starter", 10)
    assert su.next_plan_key_version(["starter"], "starter") == "starter-v2"
    assert su.next_plan_key_version(["starter", "starter-v2"], "starter") == "starter-v3"
    assert su.next_plan_key_version(["starter", "starter-v2"], "starter-v2") == "starter-v3"
    assert su.next_plan_key_version(["other"], "starter") == "starter-v2"


# ── registration: tool + route + allowlists ───────────────────────────────────────


def test_business_connect_shopify_tool_is_registered():
    from plugins.takyon import core

    spec = next(
        (t for t in core.TAKYON_TOOL_DEFINITIONS if t.get("name") == "business_connect_shopify"),
        None,
    )
    assert spec is not None, "business_connect_shopify missing from TAKYON_TOOL_DEFINITIONS"
    assert spec["handler"] is core.handle_business_connect_shopify
    assert spec.get("requires_api") == ["composio"]
    params = spec["schema"]["parameters"]
    assert "shop_domain" in params["properties"]
    assert "partner_dev_fee_microusd" in params["properties"]
    assert set(params["required"]) == {"business", "shop_domain", "idempotency_key"}


def test_business_connect_shopify_rides_plugin_yaml():
    from pathlib import Path

    import plugins.takyon as pkg

    text = (Path(pkg.__file__).parent / "plugin.yaml").read_text(encoding="utf-8")
    assert "- business_connect_shopify" in text


def test_webhook_route_registered_and_public_like_stripe():
    import takyon_cli.web_server as ws

    paths = {route.path for route in ws.app.routes}
    assert "/api/webhooks/shopify" in paths
    # Sits in the SAME two allowlists as the Stripe webhook — nothing else weakened.
    assert "/api/webhooks/shopify" in ws._APP_PLANE_EXACT_PATHS
    assert ws._is_public_api_path("/api/webhooks/shopify") is True
    # Subuser role serves it on a product host; operator role refuses it (app-plane path).
    assert ws._http_path_allowed_for_host_role(
        role=ws._HOST_ROLE_SUBUSER, host="mathflow.coscale.app", path="/api/webhooks/shopify"
    ) is True
    assert ws._http_path_allowed_for_host_role(
        role=ws._HOST_ROLE_OPERATOR, host="app.fourmanifold.com", path="/api/webhooks/shopify"
    ) is False
    # And the pre-existing gates are untouched.
    assert ws._is_public_api_path("/api/ws") is False
    assert ws._is_public_api_path("/api/webhooks/stripe") is True


def test_tool_handler_fails_closed_without_composio_key(monkeypatch):
    """The TOOL-level gate: with COMPOSIO_API_KEY unavailable (locally and via the safebox
    broker), business_connect_shopify errors with the *_unconfigured marker BEFORE any Composio
    call or state write — the credential gate rides `_require_api_access(['composio'])`."""
    import json as _json

    from plugins.takyon import core, shopify_util

    class _StoreStub:
        def enforce_operator_business_access(self, business):
            return None

        def _connect(self):  # pragma: no cover - must never be reached
            raise AssertionError("no state write may happen when the key gate refuses")

    monkeypatch.setattr(core, "_store", lambda: _StoreStub())
    monkeypatch.setattr(
        core, "_missing_env_for_requirement", lambda req: ["COMPOSIO_API_KEY"] if req == "composio" else []
    )

    def _boom(**_kwargs):
        raise AssertionError("connect_shopify must not run when the key gate refuses")

    monkeypatch.setattr(shopify_util, "connect_shopify", _boom)

    raw = core.handle_business_connect_shopify(
        {"business": "shopbiz", "shop_domain": SHOP, "idempotency_key": "connect-1"}
    )
    payload = _json.loads(raw)
    assert payload["success"] is False
    assert "shopify_composio_unconfigured" in payload["error"]
    assert "COMPOSIO_API_KEY" in payload["error"]


# ── the public route end-to-end (fail-closed mappings; safebox client faked) ───────


@pytest.fixture()
def shopify_route_client(monkeypatch):
    import takyon_cli.web_server as ws

    ws.app.state.bound_host = "127.0.0.1"
    client = __import__("starlette.testclient", fromlist=["TestClient"]).TestClient(ws.app)
    try:
        yield client
    finally:
        if hasattr(ws.app.state, "bound_host"):
            del ws.app.state.bound_host


def _post_webhook(client, body: str, **headers):
    base = {"Host": "mathflow.coscale.app", "Content-Type": "application/json"}
    base.update(headers)
    return client.post("/api/webhooks/shopify", content=body.encode("utf-8"), headers=base)


def test_route_forwards_raw_body_and_hmac_to_safebox(monkeypatch, shopify_route_client):
    from plugins.takyon import safebox

    captured = {}

    def _fake_process(raw_body, hmac_header, topic):
        captured["call"] = (raw_body, hmac_header, topic)
        return {"provider_event_id": "shopify_evt_x", "deduplicated": False, "processed": {}}

    monkeypatch.setattr(safebox, "process_shopify_app_webhook", _fake_process)
    body = json.dumps({"myshopify_domain": SHOP, "plan_name": "basic"})
    resp = _post_webhook(
        shopify_route_client,
        body,
        **{"X-Shopify-Hmac-Sha256": "sig==", "X-Shopify-Topic": "shop/update"},
    )
    assert resp.status_code == 200
    assert resp.json() == {"ok": True, "deduplicated": False}
    assert captured["call"] == (body, "sig==", "shop/update")


def test_route_invalid_signature_is_401_and_reflects_nothing(monkeypatch, shopify_route_client):
    from plugins.takyon import safebox

    def _reject(raw_body, hmac_header, topic):
        raise safebox.ShopifyAppWebhookInvalidSignature("invalid_signature")

    monkeypatch.setattr(safebox, "process_shopify_app_webhook", _reject)
    resp = _post_webhook(shopify_route_client, "{}", **{"X-Shopify-Hmac-Sha256": "bad"})
    assert resp.status_code == 401
    assert resp.json() == {"ok": False, "error": "invalid_signature"}


def test_route_unconfigured_is_503(monkeypatch, shopify_route_client):
    from plugins.takyon import safebox

    def _unconfigured(raw_body, hmac_header, topic):
        raise safebox.ShopifyAppWebhookUnconfigured("shopify_webhook_unconfigured")

    monkeypatch.setattr(safebox, "process_shopify_app_webhook", _unconfigured)
    resp = _post_webhook(shopify_route_client, "{}", **{"X-Shopify-Hmac-Sha256": "x"})
    assert resp.status_code == 503
    assert resp.json() == {"ok": False, "error": "shopify_webhook_unconfigured"}


def test_route_oversized_body_is_413_before_any_processing(monkeypatch, shopify_route_client):
    from plugins.takyon import safebox

    def _boom(raw_body, hmac_header, topic):
        raise AssertionError("oversized body must never reach processing")

    monkeypatch.setattr(safebox, "process_shopify_app_webhook", _boom)
    resp = _post_webhook(
        shopify_route_client,
        "x" * (su.SHOPIFY_WEBHOOK_MAX_BODY_BYTES + 1),
        **{"X-Shopify-Hmac-Sha256": "x"},
    )
    assert resp.status_code == 413
