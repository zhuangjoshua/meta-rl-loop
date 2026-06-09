from __future__ import annotations

import json
from pathlib import Path

from plugins.takyon import core as takyon_core


def test_copy_product_service_tree_rewrites_next_build_paths_to_durable_root(tmp_path: Path):
    source_root = tmp_path / "businesses" / "scopesync" / "product" / "site"
    target_root = tmp_path / "product-services" / "scopesync"
    manifest = source_root / ".next" / "server" / "app" / "app" / "page_client-reference-manifest.js"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(
        (
            'globalThis.__RSC_MANIFEST={"/app/page":{"clientModules":{"'
            + str(source_root.resolve())
            + '/src/components/starter-access-page.js":{"id":1}},"entryCSSFiles":{"'
            + str(source_root.resolve())
            + '/src/app/app/page":[]}}};\n'
        ),
        encoding="utf-8",
    )
    required_server_files = source_root / ".next" / "required-server-files.json"
    required_server_files.parent.mkdir(parents=True, exist_ok=True)
    required_server_files.write_text(
        json.dumps({"appDir": str(source_root.resolve() / "src" / "app")}),
        encoding="utf-8",
    )
    (source_root / "package.json").write_text(
        json.dumps({"name": "scopesync-site", "scripts": {"start": "next start"}}),
        encoding="utf-8",
    )

    takyon_core._copy_product_service_tree(  # type: ignore[attr-defined]
        source_root=source_root,
        target_root=target_root,
    )

    copied_manifest = (
        target_root / ".next" / "server" / "app" / "app" / "page_client-reference-manifest.js"
    ).read_text(encoding="utf-8")
    assert str(source_root.resolve()) not in copied_manifest
    assert str(target_root.resolve()) in copied_manifest
    copied_required = (target_root / ".next" / "required-server-files.json").read_text(encoding="utf-8")
    assert str(source_root.resolve()) not in copied_required
    assert str(target_root.resolve()) in copied_required
