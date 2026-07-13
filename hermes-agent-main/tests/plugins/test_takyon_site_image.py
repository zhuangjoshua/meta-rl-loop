from __future__ import annotations

import base64
import json
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

from plugins.takyon import core, creative_provider_registry


def test_site_image_tool_writes_key_free_public_asset(tmp_path, monkeypatch):
    monkeypatch.setenv("TAKYON_HOME", str(tmp_path))
    call_order: list[str] = []

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
            call_order.append("event")
            return {"success": True}

        def _sync_business_workspace_remote(self, _business):
            call_order.append("workspace")
            return None

    store = FakeStore()
    encoded = base64.b64encode(b"site-image-bytes").decode("ascii")

    def fake_gated(spec, **kwargs):
        assert spec.canonical_id == "image:gemini-site"
        assert kwargs["payload"] == {
            "prompt": "A precise, product-specific hero image without text.",
            "aspect_ratio": "16:9",
            "image_size": "1K",
        }
        processed = kwargs["on_result"]({"image_base64": encoded, "format": "raw"})
        return {
            "provider": "gemini",
            "model": "gemini-3.1-flash-image",
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
    assert call_order == ["workspace", "event"]


def test_site_image_tool_is_authority_only():
    assert "business_generate_site_image" in core.TAKYON_AUTHORITY_TOOL_NAMES
    assert "business_generate_site_image" in {
        definition["name"] for definition in core.TAKYON_TOOL_DEFINITIONS
    }


def test_site_image_capability_is_scoped_to_gemini_site_images():
    from plugins.takyon import safebox_app

    audience = safebox_app._CREATIVE_SITE_IMAGE_AUDIENCE
    assert safebox_app._CREATIVE_AUDIENCE_CREDIT_ACTION[audience] == "site_image_generate"
    assert audience in safebox_app._CREATIVE_GEMINI_AUDIENCES
    assert audience not in safebox_app._CREATIVE_OPENAI_AUDIENCES
    assert audience not in safebox_app._CREATIVE_FAL_AUDIENCES


def test_site_image_worker_bridge_caps_two_distinct_successes(tmp_path, monkeypatch):
    class FakeStore:
        def _business_root(self, business, *, sync=True):
            return tmp_path / "businesses" / business

        def _resolve_business_file(self, business, relative):
            return tmp_path / "businesses" / business / relative

    calls: list[dict[str, object]] = []

    def fake_generate(args, fake_store):
        calls.append(dict(args))
        slug = str(args["slug"])
        asset = fake_store._resolve_business_file(
            args["business"], f"product/site/public/generated/{slug}.png"
        )
        receipt = fake_store._resolve_business_file(
            args["business"], f"product/site/.takyon/site-images/{slug}.json"
        )
        asset.parent.mkdir(parents=True, exist_ok=True)
        receipt.parent.mkdir(parents=True, exist_ok=True)
        asset.write_bytes(b"\x89PNG\r\n\x1a\n" + (b"x" * 600))
        receipt.write_text(
            json.dumps(
                {
                    "success": True,
                    "slug": slug,
                    "public_path": f"/generated/{slug}.png",
                }
            ),
            encoding="utf-8",
        )
        return json.dumps(
            {
                "success": True,
                "slug": slug,
                "public_path": f"/generated/{slug}.png",
            }
        )

    monkeypatch.setattr(core, "_handle_business_generate_site_image_with_store", fake_generate)
    bridge_root = tmp_path / "bridge"
    bridge = core._SiteImageWorkerBridge(
        store=FakeStore(),
        business="lumen",
        idempotency_prefix="taste-run-1",
        root=bridge_root,
    )
    bridge.start()
    try:
        responses = []
        for slug in ("hero-atmosphere", "supporting-detail", "third-image"):
            request_id = str(uuid.uuid4())
            (bridge.requests_dir / f"{request_id}.json").write_text(
                json.dumps(
                    {
                        "args": {
                            "slug": slug,
                            "prompt": f"Art-directed {slug}, no text or logos.",
                            "aspect_ratio": "16:9",
                            "purpose": slug,
                        }
                    }
                ),
                encoding="utf-8",
            )
            response_path = bridge.responses_dir / f"{request_id}.json"
            deadline = time.monotonic() + 2
            while not response_path.exists() and time.monotonic() < deadline:
                time.sleep(0.01)
            responses.append(json.loads(response_path.read_text(encoding="utf-8")))
    finally:
        assert bridge.close() is True

    assert [response["success"] for response in responses] == [True, True, False]
    assert "exactly two" in responses[2]["error"]
    assert [call["idempotency_key"] for call in calls] == [
        "taste-run-1:site-image:hero-atmosphere",
        "taste-run-1:site-image:supporting-detail",
    ]
    hero = tmp_path / "businesses/lumen/product/site/public/generated/hero-atmosphere.png"
    hero.unlink()
    assert bridge.restore_generated_assets() == 2
    assert hero.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")


def test_site_image_worker_bridge_uses_explicit_docker_shared_parent(tmp_path):
    class FakeStore:
        def _resolve_business_file(self, business, relative):
            return tmp_path / "businesses" / business / relative

    shared_parent = tmp_path / "operator-workspace-home" / ".takyon-worker-bridges"
    with core._site_image_worker_bridge(
        store=FakeStore(),
        business="lumen",
        idempotency_prefix="taste-run-2",
        parent_dir=shared_parent,
    ) as bridge:
        assert bridge.root.parent == shared_parent.resolve()
        assert bridge.root.is_dir()
    assert not bridge.root.exists()


def test_taste_landing_asset_contract_requires_generated_hero_and_supporting_images(tmp_path):
    root = tmp_path / "product" / "site"
    receipts = root / ".takyon" / "site-images"
    generated = root / "public" / "generated"
    landing = root / "src" / "screens" / "landing.tsx"
    receipts.mkdir(parents=True)
    generated.mkdir(parents=True)
    landing.parent.mkdir(parents=True)
    png = b"\x89PNG\r\n\x1a\n" + (b"x" * 600)
    for slug in ("hero-atmosphere", "supporting-detail"):
        (generated / f"{slug}.png").write_bytes(png)
        (receipts / f"{slug}.json").write_text(
            json.dumps(
                {
                    "success": True,
                    "slug": slug,
                    "public_path": f"/generated/{slug}.png",
                }
            ),
            encoding="utf-8",
        )
    landing.write_text(
        '<img src="/generated/hero-atmosphere.png" data-takyon-landing-asset="hero" />\n'
        '<img data-takyon-landing-asset="supporting" src="/generated/supporting-detail.png" />\n',
        encoding="utf-8",
    )

    contract, blocker = core._read_taste_landing_asset_contract(root)

    assert blocker == ""
    assert contract["hero_path"] == "/generated/hero-atmosphere.png"
    assert {asset["role"] for asset in contract["assets"]} == {"hero", "supporting"}
