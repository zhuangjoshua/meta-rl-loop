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


def test_refresh_normalizes_legacy_starter_primitives_contract(tmp_path: Path, monkeypatch):
    business_root = tmp_path / "businesses" / "scopesync"
    site = business_root / "product" / "site"
    starter_primitives = site / "src" / "components" / "starter-primitives.js"
    app_page = site / "src" / "app" / "app" / "page.js"
    starter_primitives.parent.mkdir(parents=True, exist_ok=True)
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
    starter_primitives.write_text(
        '"use client";\n'
        '\n'
        'import Link from "next/link";\n'
        'import { createContext, useContext, useEffect, useState } from "react";\n'
        '\n'
        'import {\n'
        '  starterBusinessName,\n'
        '  starterConfiguredPlans,\n'
        '  starterDefaultMonthlyPlan,\n'
        '  starterDefaultPlanKey,\n'
        '  starterSurfaceContext,\n'
        '  starterLoadAppState,\n'
        '  starterRequestAuth,\n'
        '  starterCancelSubscription,\n'
        '  starterCheckout,\n'
        '  starterProfile,\n'
        '  starterUpdateProfile,\n'
        '} from "./starter-context.js";\n'
        '\n'
        'const StarterAppStateContext = createContext({ appState: null, setAppState: () => {} });\n'
        '\n'
        'export {\n'
        '  starterBusinessName,\n'
        '  starterConfiguredPlans,\n'
        '  starterDefaultMonthlyPlan,\n'
        '  starterDefaultPlanKey,\n'
        '  starterSurfaceContext,\n'
        '  starterLoadAppState,\n'
        '  starterProfile,\n'
        '  starterUpdateProfile,\n'
        '  starterCancelSubscription,\n'
        '};\n'
        '\n'
        'export function StarterAppStateProvider({ initialAppState, children }) {\n'
        '  const [appState, setAppState] = useState(initialAppState || null);\n'
        '  useEffect(() => {\n'
        '    setAppState(initialAppState || null);\n'
        '  }, [initialAppState]);\n'
        '  return (\n'
        '    <StarterAppStateContext.Provider value={{ appState, setAppState }}>\n'
        '      {children}\n'
        '    </StarterAppStateContext.Provider>\n'
        '  );\n'
        '}\n'
        '\n'
        'export function useStarterAppState() {\n'
        '  return useContext(StarterAppStateContext);\n'
        '}\n'
        '\n'
        'export function StarterAuthCard() {\n'
        '  return <Link href=\"/\">Back</Link>;\n'
        '}\n',
        encoding="utf-8",
    )
    app_page.write_text(
        takyon_core._subuser_app_starter_app_page_js(),
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
    assert "appkit_starter_primitives_normalize" in repair_kinds
    normalized = starter_primitives.read_text(encoding="utf-8")
    assert "export function useStarterApp()" in normalized
    assert "starterCanGenerate" in normalized


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


def test_refresh_uses_isolated_js_runtime_env_for_install_build_and_typecheck(tmp_path: Path, monkeypatch):
    business_root = tmp_path / "businesses" / "scopesync"
    site = business_root / "product" / "site"
    site.mkdir(parents=True, exist_ok=True)
    (site / "package.json").write_text(
        json.dumps(
            {
                "name": "scopesync-site",
                "private": True,
                "scripts": {
                    "build": "next build",
                    "typecheck": "tsc --noEmit",
                    "start": "next start",
                },
                "dependencies": {"next": "^15.0.0", "react": "^19.0.0", "react-dom": "^19.0.0"},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("TAKYON_HOME", str(tmp_path / ".takyon"))
    monkeypatch.setattr(
        takyon_core,
        "_javascript_package_manager_command",
        lambda name: {"available": True, "name": "npm", "command": ["/usr/bin/npm"], "source": "test"},
    )
    calls: list[dict[str, object]] = []

    def fake_run_surface_command(command, **kwargs):
        calls.append({"command": command, "env": dict(kwargs.get("env") or {})})
        return {"command": command, "status": "passed"}

    monkeypatch.setattr(takyon_core, "_run_surface_command", fake_run_surface_command)

    verification = takyon_core._refresh_product_surface_path(business_root, "product/site", install=True)

    assert verification["status"] == "passed"
    assert len(calls) == 3
    runtime_homes = {str(item["env"].get("HOME") or "") for item in calls}
    npm_caches = {str(item["env"].get("NPM_CONFIG_CACHE") or "") for item in calls}
    xdg_caches = {str(item["env"].get("XDG_CACHE_HOME") or "") for item in calls}
    assert len(runtime_homes) == 1
    assert len(npm_caches) == 1
    assert len(xdg_caches) == 1
    runtime_home = Path(next(iter(runtime_homes)))
    npm_cache = Path(next(iter(npm_caches)))
    xdg_cache = Path(next(iter(xdg_caches)))
    assert runtime_home.is_dir()
    assert npm_cache.is_dir()
    assert xdg_cache.is_dir()
    assert runtime_home.as_posix().startswith((tmp_path / ".takyon" / "tmp" / "surface-js").as_posix())
    assert npm_cache.as_posix().startswith((tmp_path / ".takyon" / "tmp" / "surface-js").as_posix())
    assert xdg_cache.as_posix().startswith((tmp_path / ".takyon" / "tmp" / "surface-js").as_posix())
    assert not npm_cache.as_posix().startswith("/opt/takyon/.npm")
    install_env = calls[0]["env"]
    assert install_env.get("NODE_ENV") == "development"
    assert install_env.get("NPM_CONFIG_PRODUCTION") == "false"
