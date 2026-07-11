from __future__ import annotations

import base64
import json
from contextlib import contextmanager
from pathlib import Path

from plugins.takyon import core, creative_provider_registry


def test_site_image_tool_writes_key_free_public_asset(tmp_path, monkeypatch):
    monkeypatch.setenv("TAKYON_HOME", str(tmp_path))

    class FakeStore:
        @contextmanager
        def _connect(self):
            yield object()

        def _ensure_business(self, _conn, business):
            assert business == "lumen"
            return {"slug": business, "mode": "live", "owner_user_id": "owner-1"}

        def _resolve_business_file(self, business, relative):
            return Path(tmp_path) / "businesses" / business / relative

        def commit(self, **_kwargs):
            return {"success": True}

        def _sync_business_workspace_remote(self, _business):
            return None

    store = FakeStore()
    encoded = base64.b64encode(b"site-image-bytes").decode("ascii")

    def fake_gated(spec, **kwargs):
        assert spec.canonical_id == "image:openai-site"
        assert kwargs["payload"]["model"] == "gpt-image-2"
        processed = kwargs["on_result"]({"data": [{"b64_json": encoded}]})
        return {
            "provider": "openai",
            "model": "gpt-image-2",
            "provider_cost_usd": 0.10,
            "credits_charged": 2,
            "balance_credits": 8,
            "reserved_credits": 0,
            "processed": processed,
        }

    monkeypatch.setattr(core, "_store", lambda: store)
    monkeypatch.setattr(creative_provider_registry, "gated_creative_call", fake_gated)

    result = json.loads(
        core.handle_business_generate_site_image(
            {
                "business": "lumen",
                "slug": "hero-atmosphere",
                "prompt": "A precise, product-specific hero image without text.",
                "idempotency_key": "lumen-site-image-v1",
            }
        )
    )

    assert result["success"] is True
    assert result["public_path"] == "/generated/hero-atmosphere.png"
    asset = (
        tmp_path
        / "businesses"
        / "lumen"
        / "product"
        / "site"
        / "public"
        / "generated"
        / "hero-atmosphere.png"
    )
    assert asset.read_bytes() == b"site-image-bytes"
    receipt = (
        tmp_path
        / "businesses"
        / "lumen"
        / "product"
        / "site"
        / ".takyon"
        / "site-images"
        / "hero-atmosphere.json"
    )
    assert json.loads(receipt.read_text(encoding="utf-8"))["credits_charged"] == 2


def test_site_image_tool_is_authority_only():
    assert "business_generate_site_image" in core.TAKYON_AUTHORITY_TOOL_NAMES
    assert "business_generate_site_image" in {
        definition["name"] for definition in core.TAKYON_TOOL_DEFINITIONS
    }


def test_site_image_capability_is_scoped_to_openai_images():
    from plugins.takyon import safebox_app

    audience = safebox_app._CREATIVE_SITE_IMAGE_AUDIENCE
    assert safebox_app._CREATIVE_AUDIENCE_CREDIT_ACTION[audience] == "site_image_generate"
    assert audience in safebox_app._CREATIVE_OPENAI_AUDIENCES
    assert audience not in safebox_app._CREATIVE_GEMINI_AUDIENCES
    assert audience not in safebox_app._CREATIVE_FAL_AUDIENCES
