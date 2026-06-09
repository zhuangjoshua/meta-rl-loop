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


def test_refresh_normalizes_legacy_appkit_product_root_route(tmp_path: Path, monkeypatch):
    business_root = tmp_path / "businesses" / "scopesync"
    site = business_root / "product" / "site"
    legacy_route = site / "src" / "app" / "app" / "(product)" / "page.js"
    app_page = site / "src" / "app" / "app" / "page.js"
    legacy_route.parent.mkdir(parents=True, exist_ok=True)
    app_page.parent.mkdir(parents=True, exist_ok=True)
    (site / "package.json").write_text(
        json.dumps(
            {
                "name": "scopesync-site",
                "private": True,
                "scripts": {"build": "next build", "start": "next start"},
                "dependencies": {"next": "^15.0.0", "react": "^19.0.0", "react-dom": "^19.0.0"},
            }
        ),
        encoding="utf-8",
    )
    legacy_app_template = takyon_core._subuser_app_starter_app_page_js().replace(
        'import ProductRoot from "./(product)/root";\n', ""
    ).replace(
        '  if (initialAppState?.access?.state === "ready") {\n'
        '    return <ProductRoot initialAppState={initialAppState} searchParams={searchParams} />;\n'
        '  }\n',
        "",
    ).replace("export default async function AppPage({ searchParams }) {", "export default async function AppPage() {")
    app_page.write_text(legacy_app_template, encoding="utf-8")
    legacy_route.write_text(
        'export default function ScopeSyncRoot() {\n  return <main>ScopeSync</main>;\n}\n',
        encoding="utf-8",
    )

    monkeypatch.setattr(
        takyon_core,
        "_javascript_package_manager_command",
        lambda name: {"available": True, "name": "npm", "command": ["/usr/bin/npm"], "source": "test"},
    )
    monkeypatch.setattr(
        takyon_core,
        "_run_surface_command",
        lambda command, **kwargs: {"command": command, "status": "passed"},
    )

    verification = takyon_core._refresh_product_surface_path(business_root, "product/site", install=True)

    assert verification["status"] == "passed"
    repair_kinds = [item.get("kind") for item in verification["repairs"]]
    assert "appkit_product_root_route_normalize" in repair_kinds
    assert "appkit_app_entry_normalize" in repair_kinds
    assert not legacy_route.exists()
    canonical_root = site / "src" / "app" / "app" / "(product)" / "root.js"
    assert canonical_root.read_text(encoding="utf-8").startswith("export default function ScopeSyncRoot()")
    normalized_app_page = app_page.read_text(encoding="utf-8")
    assert 'import ProductRoot from "./(product)/root";' in normalized_app_page
    assert "return <ProductRoot initialAppState={initialAppState} searchParams={searchParams} />;" in normalized_app_page


def test_refresh_rewrites_legacy_product_root_starter_imports(tmp_path: Path, monkeypatch):
    business_root = tmp_path / "businesses" / "scopesync"
    site = business_root / "product" / "site"
    legacy_route = site / "src" / "app" / "app" / "(product)" / "page.js"
    app_page = site / "src" / "app" / "app" / "page.js"
    legacy_route.parent.mkdir(parents=True, exist_ok=True)
    app_page.parent.mkdir(parents=True, exist_ok=True)
    (site / "package.json").write_text(
        json.dumps(
            {
                "name": "scopesync-site",
                "private": True,
                "scripts": {"build": "next build", "start": "next start"},
                "dependencies": {"next": "^15.0.0", "react": "^19.0.0", "react-dom": "^19.0.0"},
            }
        ),
        encoding="utf-8",
    )
    legacy_app_template = takyon_core._subuser_app_starter_app_page_js().replace(
        'import ProductRoot from "./(product)/root";\n', ""
    ).replace(
        '  if (initialAppState?.access?.state === "ready") {\n'
        '    return <ProductRoot initialAppState={initialAppState} searchParams={searchParams} />;\n'
        '  }\n',
        "",
    ).replace("export default async function AppPage({ searchParams }) {", "export default async function AppPage() {")
    app_page.write_text(legacy_app_template, encoding="utf-8")
    legacy_route.write_text(
        '"use client";\n'
        '\n'
        'import { useStarterApp } from "../../../../components/starter-context";\n'
        '\n'
        'export default function ScopeSyncRoot() {\n'
        '  const { appState } = useStarterApp();\n'
        '  return <main>{appState ? "ready" : "missing"}</main>;\n'
        '}\n',
        encoding="utf-8",
    )

    monkeypatch.setattr(
        takyon_core,
        "_javascript_package_manager_command",
        lambda name: {"available": True, "name": "npm", "command": ["/usr/bin/npm"], "source": "test"},
    )
    monkeypatch.setattr(
        takyon_core,
        "_run_surface_command",
        lambda command, **kwargs: {"command": command, "status": "passed"},
    )

    verification = takyon_core._refresh_product_surface_path(business_root, "product/site", install=True)

    assert verification["status"] == "passed"
    repair_kinds = [item.get("kind") for item in verification["repairs"]]
    assert "appkit_product_root_imports_normalize" in repair_kinds
    canonical_root = site / "src" / "app" / "app" / "(product)" / "root.js"
    normalized_root = canonical_root.read_text(encoding="utf-8")
    assert '../../../../components/starter-context' not in normalized_root
    assert '../../../components/starter-primitives' in normalized_root


def test_refresh_warns_when_custom_app_page_needs_manual_product_root_render(tmp_path: Path, monkeypatch):
    business_root = tmp_path / "businesses" / "scopesync"
    site = business_root / "product" / "site"
    legacy_route = site / "src" / "app" / "app" / "(product)" / "page.js"
    app_page = site / "src" / "app" / "app" / "page.js"
    legacy_route.parent.mkdir(parents=True, exist_ok=True)
    app_page.parent.mkdir(parents=True, exist_ok=True)
    (site / "package.json").write_text(
        json.dumps(
            {
                "name": "scopesync-site",
                "private": True,
                "scripts": {"build": "next build", "start": "next start"},
                "dependencies": {"next": "^15.0.0", "react": "^19.0.0", "react-dom": "^19.0.0"},
            }
        ),
        encoding="utf-8",
    )
    app_page.write_text(
        'export default function AppPage() {\n  return <main>Custom app shell</main>;\n}\n',
        encoding="utf-8",
    )
    legacy_route.write_text(
        'export default function ScopeSyncRoot() {\n  return <main>ScopeSync</main>;\n}\n',
        encoding="utf-8",
    )

    monkeypatch.setattr(
        takyon_core,
        "_javascript_package_manager_command",
        lambda name: {"available": True, "name": "npm", "command": ["/usr/bin/npm"], "source": "test"},
    )
    monkeypatch.setattr(
        takyon_core,
        "_run_surface_command",
        lambda command, **kwargs: {"command": command, "status": "passed"},
    )

    verification = takyon_core._refresh_product_surface_path(business_root, "product/site", install=True)

    assert verification["status"] == "passed"
    assert not legacy_route.exists()
    assert (site / "src" / "app" / "app" / "(product)" / "root.js").exists()
    assert any("root.js" in warning for warning in verification["warnings"])
