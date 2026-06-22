from __future__ import annotations

import io
import json
import urllib.request

from plugins.takyon import core, safebox


class _JsonResponse(io.BytesIO):
    def __init__(self, payload: dict):
        super().__init__(json.dumps(payload).encode("utf-8"))


def test_product_edge_route_skips_when_wildcard_route_exists(monkeypatch):
    monkeypatch.setenv("CLOUDFLARE_ZONE_NAME", "coscale.app")
    monkeypatch.setattr(safebox, "first_env_backed_value", lambda name: "token")
    calls: list[tuple[str, str]] = []

    def fake_urlopen(req: urllib.request.Request, timeout: int = 0):
        calls.append((req.get_method(), req.full_url))
        if req.get_method() == "POST":
            raise AssertionError("wildcard route should cover every business slug")
        if req.full_url.endswith("/zones?name=coscale.app"):
            return _JsonResponse({"success": True, "result": [{"id": "zone-id"}]})
        if req.full_url.endswith("/zones/zone-id/workers/routes"):
            return _JsonResponse(
                {
                    "success": True,
                    "result": [
                        {"pattern": "*.coscale.app/*", "script": "takyon-product-worker"}
                    ],
                }
            )
        raise AssertionError(f"unexpected request: {req.get_method()} {req.full_url}")

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    core._ensure_product_edge_route("bookshelf")

    assert calls == [
        ("GET", "https://api.cloudflare.com/client/v4/zones?name=coscale.app"),
        ("GET", "https://api.cloudflare.com/client/v4/zones/zone-id/workers/routes"),
    ]


def test_product_edge_route_creates_business_route_when_uncovered(monkeypatch):
    monkeypatch.setenv("CLOUDFLARE_ZONE_NAME", "coscale.app")
    monkeypatch.setattr(safebox, "first_env_backed_value", lambda name: "token")
    posts: list[dict] = []

    def fake_urlopen(req: urllib.request.Request, timeout: int = 0):
        if req.full_url.endswith("/zones?name=coscale.app"):
            return _JsonResponse({"success": True, "result": [{"id": "zone-id"}]})
        if req.full_url.endswith("/zones/zone-id/workers/routes") and req.get_method() == "GET":
            return _JsonResponse({"success": True, "result": []})
        if req.full_url.endswith("/zones/zone-id/workers/routes") and req.get_method() == "POST":
            posts.append(json.loads((req.data or b"{}").decode("utf-8")))
            return _JsonResponse({"success": True, "result": posts[-1]})
        raise AssertionError(f"unexpected request: {req.get_method()} {req.full_url}")

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    core._ensure_product_edge_route("Bookshelf")

    assert posts == [
        {"pattern": "bookshelf.coscale.app/*", "script": "takyon-product-worker"}
    ]
