"""Generic Shopify storefront rail.

``product/shopify-catalog.json`` (written by ``business_shopify_create_product``) is baked into the
product site's surface context as ``shopifyCatalog`` at publish time — the same publish-time
injection wiring as ``plans`` — so EVERY shopify_commerce site renders a buyable Store section with
no per-business bake. A business with no catalog file gets an empty list and shows no store section.

Covers the fail-soft reader and the payload injection. The scaffold render gating (Store section
hidden when the list is empty) is exercised by the scaffold's own tsc/vite build.
"""

import json

from plugins.takyon import core as takyon_core


def _seed_catalog(tmp_path, products):
    """A business workspace at ``<root>/product/site`` with a catalog at ``<root>/product/…``."""
    workspace_root = tmp_path / "product" / "site"
    workspace_root.mkdir(parents=True)
    (tmp_path / "product" / "shopify-catalog.json").write_text(
        json.dumps(
            {"business": "blockcrate", "shop_domain": "s.myshopify.com", "products": products}
        ),
        encoding="utf-8",
    )
    return workspace_root


def test_reader_returns_empty_when_absent(tmp_path):
    workspace_root = tmp_path / "product" / "site"
    workspace_root.mkdir(parents=True)
    assert takyon_core._read_shopify_catalog_products(workspace_root) == []


def test_reader_projects_only_buyable_products(tmp_path):
    workspace_root = _seed_catalog(
        tmp_path,
        [
            {
                "product_id": "gid://shopify/Product/1",
                "title": "Space Crate",
                "price": "39.99",
                "handle": "space-crate",
                "cart_permalink": "https://s.myshopify.com/cart/9:1",
                "preview_url": "https://x",
            },
            # blank title -> excluded (never leak an unlabeled buy button)
            {"product_id": "2", "title": "  ", "cart_permalink": "https://s.myshopify.com/cart/8:1"},
            # no permalink -> excluded (not buyable)
            {"product_id": "3", "title": "No Link", "cart_permalink": ""},
            "not-a-mapping",
        ],
    )
    out = takyon_core._read_shopify_catalog_products(workspace_root)
    assert [p["title"] for p in out] == ["Space Crate"]
    assert out[0]["cart_permalink"] == "https://s.myshopify.com/cart/9:1"
    assert out[0]["price"] == "39.99"


def test_reader_fails_soft_on_bad_json(tmp_path):
    workspace_root = tmp_path / "product" / "site"
    workspace_root.mkdir(parents=True)
    (tmp_path / "product" / "shopify-catalog.json").write_text("{not json", encoding="utf-8")
    assert takyon_core._read_shopify_catalog_products(workspace_root) == []


def test_surface_context_payload_bakes_catalog(tmp_path):
    workspace_root = _seed_catalog(
        tmp_path,
        [
            {
                "product_id": "gid://shopify/Product/1",
                "title": "Space Crate",
                "price": "39.99",
                "handle": "space-crate",
                "cart_permalink": "https://s.myshopify.com/cart/9:1",
            }
        ],
    )
    payload = takyon_core._subuser_surface_context_payload(
        None, slug="blockcrate", workspace_root=workspace_root
    )
    assert [p["title"] for p in payload["shopifyCatalog"]] == ["Space Crate"]


def test_non_shopify_business_has_empty_catalog(tmp_path):
    """No catalog file (never pushed a product) -> [] -> the scaffold hides the store section."""
    workspace_root = tmp_path / "product" / "site"
    workspace_root.mkdir(parents=True)
    payload = takyon_core._subuser_surface_context_payload(
        None, slug="plainsaas", workspace_root=workspace_root
    )
    assert payload["shopifyCatalog"] == []
