from __future__ import annotations

from pathlib import Path

from plugins.takyon import core as takyon_core


def test_publish_product_surface_static_swap_is_atomic_and_preserves_runtime_overlay(tmp_path, monkeypatch):
    business_root = tmp_path / "businesses" / "plannerly"
    site = business_root / "product" / "site"
    site.mkdir(parents=True)
    (site / "index.html").write_text("<html><body>new site</body></html>\n", encoding="utf-8")
    (site / "assets").mkdir()
    (site / "assets" / "app.css").write_text("body{color:#123456}\n", encoding="utf-8")

    publish_root = tmp_path / "product-sites"
    live_root = publish_root / "plannerly"
    (live_root / "_takyon").mkdir(parents=True)
    (live_root / "_takyon" / "runtime.json").write_text('{"ok":true}\n', encoding="utf-8")
    (live_root / "index.html").write_text("<html><body>old site</body></html>\n", encoding="utf-8")

    monkeypatch.setenv("TAKYON_PRODUCT_SITE_ROOT", str(publish_root))
    monkeypatch.setenv("TAKYON_PRODUCT_LOCAL_BASE_URL", "http://127.0.0.1:9000/site")
    monkeypatch.setattr(takyon_core, "_ensure_product_static_caddy_route", lambda **_: (None, ""))

    observed: dict[str, object] = {}
    real_copytree = takyon_core.shutil.copytree

    def _recording_copytree(src, dst, *args, **kwargs):
        src_path = Path(src).resolve()
        if src_path == site.resolve() and "publish_copy_target" not in observed:
            observed["publish_copy_target"] = Path(dst)
            observed["live_target_exists_during_copy"] = live_root.exists()
            observed["live_target_html_during_copy"] = (live_root / "index.html").read_text(encoding="utf-8")
        return real_copytree(src, dst, *args, **kwargs)

    monkeypatch.setattr(takyon_core.shutil, "copytree", _recording_copytree)

    result = takyon_core._publish_product_surface_path(
        business_root=business_root,
        slug="plannerly",
        source_path="product/site",
        publish_target="https://plannerly.fourmanifold.com/",
    )

    assert result["status"] == "published"
    assert observed["live_target_exists_during_copy"] is True
    assert observed["live_target_html_during_copy"] == "<html><body>old site</body></html>\n"
    assert observed["publish_copy_target"] != live_root
    assert (live_root / "index.html").read_text(encoding="utf-8") == "<html><body>new site</body></html>\n"
    assert (live_root / "assets" / "app.css").read_text(encoding="utf-8") == "body{color:#123456}\n"
    assert (live_root / "_takyon" / "runtime.json").read_text(encoding="utf-8") == '{"ok":true}\n'


def test_product_surface_operational_facts_surface_real_build_and_publish_receipt_details():
    facts = takyon_core._product_surface_operational_facts(
        surface={
            "public_url": "https://plannerly.fourmanifold.com/",
            "publish_status": "blocked",
        },
        receipt={
            "status": "blocked_build",
            "checks": [
                {"status": "passed", "command": ["npm", "install"]},
                {
                    "status": "failed",
                    "command": ["npm", "run", "build"],
                    "error": "Missing CSS asset manifest",
                },
            ],
            "publish": {
                "status": "blocked",
                "deploy_kind": "local_static",
                "publish_source_path": "product/site",
                "publish_root": "/tmp/product-sites/plannerly",
                "public_url": "https://plannerly.fourmanifold.com/",
                "blocker": "Missing CSS asset manifest",
            },
        },
        inventory={
            "status": "collected",
            "package": {"frameworks": ["vite"], "package_manager": "npm"},
            "runtime_integrations": ["checkout"],
            "workflow_markers": ["static_export"],
            "routes": ["/", "/pricing"],
        },
    )

    assert facts["detected_frameworks"] == ["vite"]
    assert facts["detected_package_manager"] == "npm"
    assert facts["publish_mode"] == "local_static"
    assert facts["publish_source_path"] == "product/site"
    assert facts["latest_check_status"] == "failed"
    assert facts["latest_check_command"] == "npm run build"
    assert facts["latest_failed_command"] == "npm run build"
    assert facts["latest_check_error"] == "Missing CSS asset manifest"
    assert facts["blocker"] == "npm run build failed: Missing CSS asset manifest"
