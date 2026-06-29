from __future__ import annotations

import json

import pytest

from plugins.takyon import core as takyon_core
from plugins.takyon.core import TakyonError


def test_seo_build_credentials_uses_canonical_gsc_safebox_alias(monkeypatch):
    seen: list[tuple[str, ...]] = []

    def _fake_first(*keys: str) -> str:
        seen.append(tuple(keys))
        return ""

    monkeypatch.setattr(takyon_core.safebox, "first_env_backed_value", _fake_first)

    with pytest.raises(TakyonError) as excinfo:
        takyon_core._seo_build_credentials(["https://www.googleapis.com/auth/webmasters"])

    assert seen == [("TAKYON_GSC_SERVICE_ACCOUNT_KEY",)]
    assert "TAKYON_GSC_SERVICE_ACCOUNT_KEY" in str(excinfo.value)


def test_seo_add_property_in_business_session_uses_brokered_registration(monkeypatch):
    seen = []

    def _fake_register(args):
        seen.append(args)
        return json.dumps(
            {
                "success": True,
                "receipt": "product/seo/search-console/demo/receipt.json",
                "status": "registered",
                "value": {"ok": True},
            }
        )

    monkeypatch.setattr(takyon_core, "_session_business_slug", lambda: "demo")
    monkeypatch.setattr(takyon_core, "handle_business_register_search_console", _fake_register)

    out = json.loads(
        takyon_core.handle_business_seo_add_property(
            {"site_url": "https://demo.coscale.app"}
        )
    )

    assert out["success"] is True
    assert out["registered_via"] == "business_register_search_console"
    assert out["business"] == "demo"
    assert seen and seen[0]["business"] == "demo"
    assert seen[0]["site_url"] == "https://demo.coscale.app"
    assert seen[0]["idempotency_key"]


def test_seo_add_property_without_business_session_uses_safebox_route(monkeypatch):
    seen = []

    def _fake_add(site_url):
        seen.append(site_url)
        return {"success": True, "site_url": "https://demo.coscale.app/", "already_existed": False}

    monkeypatch.setattr(takyon_core, "_session_business_slug", lambda: "")
    monkeypatch.setattr(takyon_core.safebox, "gsc_add_property", _fake_add)

    out = json.loads(
        takyon_core.handle_business_seo_add_property(
            {"site_url": "https://demo.coscale.app"}
        )
    )

    assert out["success"] is True
    assert seen == ["https://demo.coscale.app"]
