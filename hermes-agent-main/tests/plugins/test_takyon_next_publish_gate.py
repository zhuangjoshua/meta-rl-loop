from __future__ import annotations

import json

from plugins.takyon import core as takyon_core


def test_product_next_service_metadata_blocks_incomplete_next_build(tmp_path):
    site_root = tmp_path / "product-site"
    site_root.mkdir()
    (site_root / "package.json").write_text(
        json.dumps(
            {
                "scripts": {"start": "next start"},
                "dependencies": {"next": "15.0.0"},
            }
        ),
        encoding="utf-8",
    )
    (site_root / "next.config.js").write_text("module.exports = {};\n", encoding="utf-8")
    (site_root / ".next").mkdir()

    metadata, blocker = takyon_core._product_next_service_metadata(site_root)  # type: ignore[attr-defined]

    assert metadata is None
    assert "BUILD_ID" in blocker


def test_product_next_service_metadata_accepts_complete_next_build(tmp_path):
    site_root = tmp_path / "product-site"
    site_root.mkdir()
    (site_root / "package.json").write_text(
        json.dumps(
            {
                "scripts": {"start": "next start"},
                "dependencies": {"next": "15.0.0"},
            }
        ),
        encoding="utf-8",
    )
    (site_root / "next.config.js").write_text("module.exports = {};\n", encoding="utf-8")
    next_root = site_root / ".next"
    next_root.mkdir()
    (next_root / "BUILD_ID").write_text("build-123\n", encoding="utf-8")
    (next_root / "build-manifest.json").write_text("{}", encoding="utf-8")

    metadata, blocker = takyon_core._product_next_service_metadata(site_root)  # type: ignore[attr-defined]

    assert blocker == ""
    assert metadata is not None
    assert metadata["kind"] == "next_systemd_caddy"
