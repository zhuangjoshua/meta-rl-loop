"""Shopify commerce executor slice — rig-independent tests (operator ruling 2026-07-03,
"try Shopify"; readmodular §1.5 v1 semantics: selling happens ON Shopify, this platform
moves no order money).

Covers, without DB or network (fake Composio transport, exactly like test_takyon_shopify_uc4):
  * input validation is fail-closed (price/status/title/tags/image urls) BEFORE any call;
  * `create_product` happy path: dedup search → productCreate → variant price → media, with the
    business namespacing tag always present in the create input;
  * idempotency: an existing (tag, exact title) match is ADOPTED (`deduped=True`) with zero
    mutation calls;
  * store refusals surface: GraphQL top-level errors, userErrors, and a missing default variant
    all raise `ShopifyCommerceError` — never a partial/guessed result;
  * media attach failures are WARNINGS (product exists; the store is truth), never rollbacks;
  * `read_orders` parses the proxy envelope fail-closed and clamps `first` to 1..50;
  * registration: both executor tools ride TAKYON_TOOL_DEFINITIONS with requires_api=['composio']
    and plugin.yaml — and NO subuser-plane surface changes (no new route, no allowlist edit;
    these are operator-plane business tools only).

Handler-level gating (ACTIVE-connection gate, mode guard, wake refusal) is deterministic code
inside `core.handle_business_shopify_create_product`; the pure halves it delegates to are pinned
here and the connect-side state contract is pinned by the uc4 suite.
"""

from __future__ import annotations

import json

import pytest

from plugins.takyon import shopify_util as su

SHOP = "ourmanifold-uc4-test.myshopify.com"
ACCOUNT = "ca_test123"
BIZ = "roasted-peak"
TAG = f"takyon:business:{BIZ}"


class _FakeTransport:
    """Scripted `_composio_request` double: records every call, replays queued responses."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls: list[dict] = []

    def __call__(self, method, path, *, json_body=None, params=None, timeout=60.0):
        self.calls.append(
            {"method": method, "path": path, "json_body": json_body, "params": params}
        )
        if not self.responses:
            raise AssertionError(f"unexpected extra composio call: {method} {path}")
        return self.responses.pop(0)


def _proxy_data(root_key, root):
    # The Composio proxy wraps upstream JSON in `data`, sometimes twice — mirror that.
    return {"successful": True, "data": {"data": {root_key: root}}}


def _empty_search():
    return _proxy_data("products", {"nodes": []})


def _created_product(status="ACTIVE"):
    return _proxy_data(
        "productCreate",
        {
            "product": {
                "id": "gid://shopify/Product/777",
                "handle": "trail-blend",
                "title": "Trail Blend",
                "status": status,
                "onlineStorePreviewUrl": f"https://{SHOP}/products/trail-blend",
                "variants": {"nodes": [{"id": "gid://shopify/ProductVariant/888"}]},
            },
            "userErrors": [],
        },
    )


def _price_ok():
    return _proxy_data(
        "productVariantsBulkUpdate",
        {"productVariants": [{"id": "gid://shopify/ProductVariant/888", "price": "19.99"}], "userErrors": []},
    )


def _media_ok():
    return _proxy_data("productCreateMedia", {"media": [{"alt": ""}], "mediaUserErrors": []})


# ── pure validation (fail-closed before any provider call) ─────────────────────────


def test_business_product_tag_shape():
    assert su.business_product_tag("My-Biz") == "takyon:business:my-biz"
    with pytest.raises(su.ShopifyCommerceError):
        su.business_product_tag("")


def test_price_normalization():
    assert su._normalize_price("19.9") == "19.90"
    assert su._normalize_price(5) == "5.00"
    for bad in ("", "free", "0", "-3", None):
        with pytest.raises(su.ShopifyCommerceError):
            su._normalize_price(bad)


def test_create_product_refuses_bad_inputs(monkeypatch):
    transport = _FakeTransport([])  # any provider call would fail the test
    monkeypatch.setattr(su, "_composio_request", transport)
    common = dict(shop_domain=SHOP, connected_account_id=ACCOUNT, business_slug=BIZ)
    with pytest.raises(su.ShopifyCommerceError, match="title"):
        su.create_product(**common, title="  ", price="9.99")
    with pytest.raises(su.ShopifyCommerceError, match="status"):
        su.create_product(**common, title="X", price="9.99", status="published")
    with pytest.raises(su.ShopifyCommerceError, match="comma"):
        su.create_product(**common, title="X", price="9.99", extra_tags=["a,b"])
    with pytest.raises(su.ShopifyCommerceError, match="http"):
        su.create_product(**common, title="X", price="9.99", image_urls=["ftp://nope"])
    with pytest.raises(su.ShopifyCommerceError, match="at most 8"):
        su.create_product(
            **common, title="X", price="9.99",
            image_urls=[f"https://img/{i}.png" for i in range(9)],
        )
    assert transport.calls == []  # nothing reached the provider


# ── create: happy path, dedup, refusals ────────────────────────────────────────────


def _publish_ok():
    return {"successful": True, "data": {"product": {"id": 777, "published_at": "2026-07-04T00:00:00Z"}}}


def test_create_product_happy_path(monkeypatch):
    transport = _FakeTransport([_empty_search(), _created_product(), _price_ok(), _publish_ok(), _media_ok()])
    monkeypatch.setattr(su, "_composio_request", transport)
    result = su.create_product(
        shop_domain=SHOP,
        connected_account_id=ACCOUNT,
        business_slug=BIZ,
        title="Trail Blend",
        price="19.99",
        description_html="<p>Good coffee.</p>",
        status="active",
        extra_tags=["coffee"],
        image_urls=["https://img.example/1.png"],
    )
    assert result["deduped"] is False
    assert result["product_id"] == "gid://shopify/Product/777"
    assert result["product_numeric_id"] == "777"
    assert result["variant_id"] == "gid://shopify/ProductVariant/888"
    assert result["price"] == "19.99"
    assert result["tag"] == TAG
    assert result["media_warnings"] == []
    assert result["publish_warnings"] == []
    assert len(transport.calls) == 5
    publish_call = transport.calls[3]["json_body"]
    assert publish_call["method"] == "PUT" and "/products/777.json" in publish_call["endpoint"]
    assert publish_call["body"] == {"product": {"id": 777, "published": True}}
    # every call rides the proven proxy path against the store's Admin GraphQL endpoint
    for call in transport.calls:
        assert call["path"] == su.COMPOSIO_PROXY_TOOL_PATH
        assert call["json_body"]["connected_account_id"] == ACCOUNT
        assert SHOP in call["json_body"]["endpoint"]
    create_vars = transport.calls[1]["json_body"]["body"]["variables"]["product"]
    assert TAG in create_vars["tags"] and "coffee" in create_vars["tags"]
    assert create_vars["status"] == "ACTIVE"


def test_create_product_dedupes_on_tag_and_exact_title(monkeypatch):
    existing = _proxy_data(
        "products",
        {
            "nodes": [
                {
                    "id": "gid://shopify/Product/555",
                    "handle": "trail-blend",
                    "title": "Trail Blend",
                    "status": "DRAFT",
                    "onlineStorePreviewUrl": "",
                    "tags": [TAG, "coffee"],
                },
                # near-miss: right title, wrong tag — must NOT be adopted
                {
                    "id": "gid://shopify/Product/556",
                    "handle": "other",
                    "title": "Trail Blend",
                    "status": "ACTIVE",
                    "onlineStorePreviewUrl": "",
                    "tags": ["takyon:business:someone-else"],
                },
            ]
        },
    )
    transport = _FakeTransport([existing])
    monkeypatch.setattr(su, "_composio_request", transport)
    result = su.create_product(
        shop_domain=SHOP, connected_account_id=ACCOUNT, business_slug=BIZ,
        title="Trail Blend", price="19.99",
    )
    assert result["deduped"] is True
    assert result["product_id"] == "gid://shopify/Product/555"
    assert len(transport.calls) == 1  # search only — zero mutations


def test_create_product_surfaces_user_errors(monkeypatch):
    refused = _proxy_data(
        "productCreate",
        {"product": None, "userErrors": [{"field": ["title"], "message": "already exists"}]},
    )
    transport = _FakeTransport([_empty_search(), refused])
    monkeypatch.setattr(su, "_composio_request", transport)
    with pytest.raises(su.ShopifyCommerceError, match="refused by the store"):
        su.create_product(
            shop_domain=SHOP, connected_account_id=ACCOUNT, business_slug=BIZ,
            title="Trail Blend", price="19.99",
        )


def test_create_product_fails_closed_on_graphql_errors(monkeypatch):
    errored = {"successful": True, "data": {"errors": [{"message": "Throttled"}]}}
    transport = _FakeTransport([errored])
    monkeypatch.setattr(su, "_composio_request", transport)
    with pytest.raises(su.ShopifyCommerceError, match="graphql errors"):
        su.create_product(
            shop_domain=SHOP, connected_account_id=ACCOUNT, business_slug=BIZ,
            title="Trail Blend", price="19.99",
        )


def test_create_product_requires_default_variant(monkeypatch):
    no_variant = _proxy_data(
        "productCreate",
        {
            "product": {
                "id": "gid://shopify/Product/777",
                "handle": "x",
                "title": "Trail Blend",
                "status": "DRAFT",
                "onlineStorePreviewUrl": "",
                "variants": {"nodes": []},
            },
            "userErrors": [],
        },
    )
    transport = _FakeTransport([_empty_search(), no_variant])
    monkeypatch.setattr(su, "_composio_request", transport)
    with pytest.raises(su.ShopifyCommerceError, match="no default variant"):
        su.create_product(
            shop_domain=SHOP, connected_account_id=ACCOUNT, business_slug=BIZ,
            title="Trail Blend", price="19.99",
        )


def test_media_failure_is_warning_not_rollback(monkeypatch):
    media_refused = _proxy_data(
        "productCreateMedia",
        {"media": [], "mediaUserErrors": [{"field": ["media"], "message": "bad image"}]},
    )
    transport = _FakeTransport(
        [_empty_search(), _created_product(status="DRAFT"), _price_ok(), media_refused]
    )
    monkeypatch.setattr(su, "_composio_request", transport)
    result = su.create_product(
        shop_domain=SHOP, connected_account_id=ACCOUNT, business_slug=BIZ,
        title="Trail Blend", price="19.99",
        image_urls=["https://img.example/broken.png"],
    )
    assert result["deduped"] is False
    assert result["media_warnings"] == ["bad image"]
    assert result["publish_warnings"] == []  # DRAFT is never published to the online store
    assert len(transport.calls) == 4  # search, create, price, media — no publish call


def test_composio_proxy_failure_fails_closed(monkeypatch):
    transport = _FakeTransport([{"successful": False, "error": "connection expired"}])
    monkeypatch.setattr(su, "_composio_request", transport)
    with pytest.raises(su.ShopifyCommerceError, match="successful=false"):
        su.create_product(
            shop_domain=SHOP, connected_account_id=ACCOUNT, business_slug=BIZ,
            title="Trail Blend", price="19.99",
        )


# ── orders read ─────────────────────────────────────────────────────────────────────


def test_read_orders_parses_and_normalizes(monkeypatch):
    payload = _proxy_data(
        "orders",
        {
            "nodes": [
                {
                    "id": "gid://shopify/Order/1",
                    "name": "#1001",
                    "createdAt": "2026-07-03T00:00:00Z",
                    "displayFinancialStatus": "PAID",
                    "displayFulfillmentStatus": "UNFULFILLED",
                    "totalPriceSet": {"shopMoney": {"amount": "19.99", "currencyCode": "USD"}},
                    "lineItems": {"nodes": [{"title": "Trail Blend", "quantity": 2}]},
                }
            ]
        },
    )
    transport = _FakeTransport([payload])
    monkeypatch.setattr(su, "_composio_request", transport)
    result = su.read_orders(shop_domain=SHOP, connected_account_id=ACCOUNT, first=999)
    assert result["count"] == 1
    order = result["orders"][0]
    assert order["name"] == "#1001"
    assert order["financial_status"] == "PAID"
    assert order["total"] == {"amount": "19.99", "currency": "USD"}
    assert order["line_items"] == [{"title": "Trail Blend", "quantity": 2}]
    # first clamps to 50
    assert transport.calls[0]["json_body"]["body"]["variables"]["first"] == 50


def test_read_orders_fails_closed_on_missing_nodes(monkeypatch):
    transport = _FakeTransport([_proxy_data("orders", {"pageInfo": {}})])
    monkeypatch.setattr(su, "_composio_request", transport)
    with pytest.raises(su.ShopifyCommerceError, match="no nodes"):
        su.read_orders(shop_domain=SHOP, connected_account_id=ACCOUNT)


# ── registration: operator-plane tools only, nothing on the subuser plane ───────────


def test_commerce_tools_registered():
    from plugins.takyon import core

    for name, handler_name, required in (
        (
            "business_shopify_create_product",
            "handle_business_shopify_create_product",
            {"business", "title", "price", "idempotency_key"},
        ),
        ("business_shopify_read_orders", "handle_business_shopify_read_orders", {"business"}),
    ):
        spec = next(
            (t for t in core.TAKYON_TOOL_DEFINITIONS if t.get("name") == name), None
        )
        assert spec is not None, f"{name} missing from TAKYON_TOOL_DEFINITIONS"
        assert spec["handler"] is getattr(core, handler_name)
        assert spec.get("requires_api") == ["composio"]
        assert set(spec["schema"]["parameters"]["required"]) == required


def test_commerce_tools_ride_plugin_yaml():
    from pathlib import Path

    import plugins.takyon as pkg

    text = (Path(pkg.__file__).parent / "plugin.yaml").read_text(encoding="utf-8")
    assert "- business_shopify_create_product" in text
    assert "- business_shopify_read_orders" in text


def test_no_new_subuser_surface():
    """The slice adds ZERO subuser-plane surface: no new public route, no allowlist edit.
    Pin it: the app-plane route table and public-path predicate contain nothing shopify-commerce
    shaped beyond the pre-existing webhook route."""
    import takyon_cli.web_server as ws
    from plugins.takyon import core

    route_blob = json.dumps(
        [str(r) for r in getattr(core, "_RAIL_ROUTES", [])], default=str
    ).lower()
    assert "shopify_create_product" not in route_blob
    assert "shopify_read_orders" not in route_blob
    ws_src = open(ws.__file__, encoding="utf-8").read()
    assert "business_shopify_create_product" not in ws_src
    assert "business_shopify_read_orders" not in ws_src


# ── idempotency layer 2: local receipts + product-by-id (2026-07-04 live-acceptance fix) ──────
# Shopify's products SEARCH index is eventually consistent — the first live rerun duplicated a
# product seconds after creating it. Dedup now reads OUR receipts first and verifies by id.


def test_match_product_receipts_exact_title_and_domain():
    payloads = [
        {"title": "Other", "shop_domain": SHOP, "product_id": "gid://shopify/Product/1"},
        {"title": "Trail Blend", "shop_domain": "elsewhere.myshopify.com", "product_id": "gid://shopify/Product/2"},
        {"title": "Trail Blend", "shop_domain": SHOP.upper(), "product_id": "gid://shopify/Product/3"},
        {"title": "Trail Blend", "shop_domain": SHOP, "product_id": "gid://shopify/Product/3"},
        {"title": "Trail Blend", "shop_domain": SHOP, "product_id": "gid://shopify/Product/4"},
        "not-a-mapping",
    ]
    # ordered, deduped, every matching receipt (newest first as given)
    assert su.match_product_receipts(payloads, title="Trail Blend", shop_domain=SHOP) == [
        "gid://shopify/Product/3",
        "gid://shopify/Product/4",
    ]
    assert su.match_product_receipts(payloads, title="Nope", shop_domain=SHOP) == []
    assert (
        su.match_product_receipts(
            [{"title": "Trail Blend", "shop_domain": SHOP, "product_id": ""}],
            title="Trail Blend",
            shop_domain=SHOP,
        )
        == []
    )


def test_get_product_present(monkeypatch):
    payload = _proxy_data(
        "ignored", {}
    )
    payload["data"]["data"] = {
        "product": {
            "id": "gid://shopify/Product/777",
            "handle": "trail-blend",
            "title": "Trail Blend",
            "status": "ACTIVE",
            "onlineStorePreviewUrl": "https://x",
        }
    }
    transport = _FakeTransport([payload])
    monkeypatch.setattr(su, "_composio_request", transport)
    product = su.get_product(
        shop_domain=SHOP, connected_account_id=ACCOUNT, product_id="gid://shopify/Product/777"
    )
    assert product is not None and product["id"] == "gid://shopify/Product/777"


def test_get_product_null_means_deleted(monkeypatch):
    payload = {"successful": True, "data": {"data": {"product": None}}}
    transport = _FakeTransport([payload])
    monkeypatch.setattr(su, "_composio_request", transport)
    assert (
        su.get_product(
            shop_domain=SHOP, connected_account_id=ACCOUNT, product_id="gid://shopify/Product/9"
        )
        is None
    )


def test_get_product_fails_closed_on_missing_field(monkeypatch):
    transport = _FakeTransport([{"successful": True, "data": {"data": {"shop": {}}}}])
    monkeypatch.setattr(su, "_composio_request", transport)
    with pytest.raises(su.ShopifyCommerceError, match="no product field"):
        su.get_product(
            shop_domain=SHOP, connected_account_id=ACCOUNT, product_id="gid://shopify/Product/9"
        )


def test_get_product_surfaces_graphql_errors(monkeypatch):
    transport = _FakeTransport([{"successful": True, "data": {"errors": [{"message": "boom"}]}}])
    monkeypatch.setattr(su, "_composio_request", transport)
    with pytest.raises(su.ShopifyCommerceError, match="graphql errors"):
        su.get_product(
            shop_domain=SHOP, connected_account_id=ACCOUNT, product_id="gid://shopify/Product/9"
        )


# ── buyable catalog: permalinks + the receipts→catalog projection (storefront slice) ──────────


def test_cart_permalink_shape():
    assert (
        su.cart_permalink(SHOP, "gid://shopify/ProductVariant/888")
        == f"https://{SHOP}/cart/888:1"
    )
    assert su.cart_permalink(SHOP, "gid://shopify/ProductVariant/888", 3).endswith("/cart/888:3")
    assert su.cart_permalink(SHOP, "not-a-gid") == ""
    assert su.cart_permalink(SHOP, "") == ""


def test_first_variant_extraction():
    product = {"variants": {"nodes": [{"id": "gid://shopify/ProductVariant/9", "price": "5.00"}]}}
    assert su.first_variant(product) == ("gid://shopify/ProductVariant/9", "5.00")
    assert su.first_variant({"variants": {"nodes": []}}) == ("", "")
    assert su.first_variant(None) == ("", "")


def test_catalog_from_receipts_projection():
    payloads = [
        {  # newest wins for product 1 (active, has variant → buyable)
            "shop_domain": SHOP, "product_id": "gid://shopify/Product/1",
            "title": "A", "price": "9.99", "status": "active",
            "variant_id": "gid://shopify/ProductVariant/11", "handle": "a",
            "preview_url": "https://p/a",
        },
        {  # older duplicate of product 1 — ignored
            "shop_domain": SHOP, "product_id": "gid://shopify/Product/1",
            "title": "A old", "price": "1.00", "status": "draft",
            "variant_id": "", "handle": "a",
        },
        {  # draft product → not buyable
            "shop_domain": SHOP, "product_id": "gid://shopify/Product/2",
            "title": "B", "price": "4.00", "status": "draft",
            "variant_id": "gid://shopify/ProductVariant/22", "handle": "b",
        },
        {  # active but no variant → not buyable (no permalink is ever guessed)
            "shop_domain": SHOP, "product_id": "gid://shopify/Product/3",
            "title": "C", "price": "2.00", "status": "active", "variant_id": "", "handle": "c",
        },
        {  # different store → excluded
            "shop_domain": "other.myshopify.com", "product_id": "gid://shopify/Product/4",
            "title": "D", "price": "2.00", "status": "active",
            "variant_id": "gid://shopify/ProductVariant/44", "handle": "d",
        },
        {"no_product_id": True},
        "not-a-mapping",
    ]
    catalog = su.catalog_from_receipts(payloads, business_slug="Roasted-Peak", shop_domain=SHOP)
    assert catalog["business"] == "roasted-peak" and catalog["shop_domain"] == SHOP
    by_id = {p["product_id"]: p for p in catalog["products"]}
    # The mirror is a PUBLIC artifact: drafts and variant-less products are EXCLUDED entirely
    # (unreleased titles/prices must not leak), so only the buyable product ships.
    assert set(by_id) == {"gid://shopify/Product/1"}
    p1 = by_id["gid://shopify/Product/1"]
    assert p1["title"] == "A" and p1["buyable"] is True
    assert p1["cart_permalink"] == f"https://{SHOP}/cart/11:1"


def test_catalog_tombstone_newest_wins():
    payloads = [
        {"tombstone": True, "shop_domain": SHOP, "product_id": "gid://shopify/Product/1"},
        {  # older create receipt for the SAME product — must NOT resurrect it
            "shop_domain": SHOP, "product_id": "gid://shopify/Product/1",
            "title": "A", "price": "9.99", "status": "active",
            "variant_id": "gid://shopify/ProductVariant/11", "handle": "a",
        },
        {  # a re-push NEWER than a tombstone resurrects (newest wins in the given order)
            "shop_domain": SHOP, "product_id": "gid://shopify/Product/2",
            "title": "B", "price": "5.00", "status": "active",
            "variant_id": "gid://shopify/ProductVariant/22", "handle": "b",
        },
        {"tombstone": True, "shop_domain": SHOP, "product_id": "gid://shopify/Product/2"},
    ]
    catalog = su.catalog_from_receipts(payloads, business_slug=BIZ, shop_domain=SHOP)
    ids = {p["product_id"] for p in catalog["products"]}
    assert ids == {"gid://shopify/Product/2"}  # 1 tombstoned; 2 re-pushed after its tombstone


def test_create_result_carries_variant_for_permalink(monkeypatch):
    transport = _FakeTransport([_empty_search(), _created_product(), _price_ok(), _publish_ok()])
    monkeypatch.setattr(su, "_composio_request", transport)
    result = su.create_product(
        shop_domain=SHOP, connected_account_id=ACCOUNT, business_slug=BIZ,
        title="Trail Blend", price="19.99", status="active",
    )
    assert result["variant_id"] == "gid://shopify/ProductVariant/888"
    assert su.cart_permalink(SHOP, result["variant_id"]) == f"https://{SHOP}/cart/888:1"
