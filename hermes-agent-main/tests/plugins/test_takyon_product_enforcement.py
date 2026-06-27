"""Hermetic product refresh enforcement tests."""

from __future__ import annotations

import json

from plugins.takyon.core import (
    WORKER_CAPABILITY_CONTRACT,
    _bounded_product_inventory,
    _scan_for_forbidden_product_backend_code,
    _scan_for_pinned_stack_client_generate_usage,
    _scan_for_pinned_stack_server_entrypoints,
    _format_forbidden_product_source_blockers,
    _refresh_product_surface_path,
    _validate_product_surface_contract,
)


def test_worker_capability_contract_keeps_runtime_namespace_boundary():
    assert "The path namespace `/api/takyon/apps/` is platform-reserved" in WORKER_CAPABILITY_CONTRACT
    assert "worker-surface-contract.json" not in WORKER_CAPABILITY_CONTRACT


def test_forbidden_product_scanner_detects_direct_provider_host(tmp_path):
    site = tmp_path / "product" / "site"
    site.mkdir(parents=True)
    (site / "provider-host.js").write_text("await fetch('https://api.openai.com/v1/responses');\n", encoding="utf-8")

    findings = _scan_for_forbidden_product_backend_code(site)

    assert findings == [
        {
            "path": "provider-host.js",
            "line": 1,
            "issue": "direct provider host",
            "blocker": "product source calls an AI provider directly",
            "snippet": "await fetch('https://api.openai.com/v1/responses');",
        }
    ]


def test_forbidden_product_scanner_detects_provider_env_reads(tmp_path):
    site = tmp_path / "product" / "site"
    site.mkdir(parents=True)
    (site / "provider-env.js").write_text(
        "const key = process.env.TAKYON_OPENAI_API_KEY || process.env.OPENAI_API_KEY;\n",
        encoding="utf-8",
    )

    findings = _scan_for_forbidden_product_backend_code(site)

    assert findings == [
        {
            "path": "provider-env.js",
            "line": 1,
            "issue": "provider credential or base-url env read",
            "blocker": "product source reads provider credentials or base URLs directly",
            "snippet": "const key = process.env.TAKYON_OPENAI_API_KEY || process.env.OPENAI_API_KEY;",
        }
    ]


def test_forbidden_product_scanner_detects_provider_sdk_imports(tmp_path):
    site = tmp_path / "product" / "site"
    site.mkdir(parents=True)
    (site / "provider-sdk.js").write_text("import OpenAI from 'openai';\n", encoding="utf-8")

    findings = _scan_for_forbidden_product_backend_code(site)

    assert findings == [
        {
            "path": "provider-sdk.js",
            "line": 1,
            "issue": "provider sdk import",
            "blocker": "product source imports or constructs an AI provider SDK directly",
            "snippet": "import OpenAI from 'openai';",
        }
    ]


def test_forbidden_product_scanner_allows_runtime_client_usage(tmp_path):
    site = tmp_path / "product" / "site"
    site.mkdir(parents=True)
    (site / "app.js").write_text(
        "import { createSubuserRuntimeClient } from './_takyon/runtime-client';\n"
        "const runtime = createSubuserRuntimeClient({ runtimeApiBase: '/api/takyon/apps/latexflow' });\n"
        "await runtime.generate({ prompt: 'hello' });\n"
        "await runtime.invokeAction('draft', { prompt: 'hello' });\n",
        encoding="utf-8",
    )

    assert _scan_for_forbidden_product_backend_code(site) == []


def test_forbidden_product_scanner_ignores_node_modules_dependency_entrypoints(tmp_path):
    """Regression: installed dependencies (vite, express, hono, …) legitimately contain
    server-entrypoint patterns inside node_modules, which is NEVER part of the published artifact
    (the pinned Vite scaffold ships only dist/). Scanning it produced a false positive that flagged
    Vite's own node_modules/vite/dist/node/ dev-server code as a "forbidden server entrypoint",
    forcing the CEO to hand-add a .takyonignore on every build. node_modules must be excluded from
    the scan entirely."""
    site = tmp_path / "product" / "site"
    vite_internal = site / "node_modules" / "vite" / "dist" / "node"
    vite_internal.mkdir(parents=True)
    (vite_internal / "cli.js").write_text(
        "const server = await createServer(config);\nserver.listen(5173);\n", encoding="utf-8"
    )
    express_pkg = site / "node_modules" / "express" / "lib"
    express_pkg.mkdir(parents=True)
    (express_pkg / "application.js").write_text(
        "const app = express();\napp.listen(3000);\n", encoding="utf-8"
    )
    assert _scan_for_forbidden_product_backend_code(site) == []


def test_forbidden_product_scanner_still_flags_entrypoint_smuggled_into_dist(tmp_path):
    """The node_modules carve-out must NOT weaken the real protection: a server entrypoint smuggled
    into the PUBLISHED build output (dist/) is still a finding — dist/ ships, node_modules does not."""
    site = tmp_path / "product" / "site"
    dist = site / "dist" / "assets"
    dist.mkdir(parents=True)
    (dist / "server.mjs").write_text("const app = express();\napp.listen(8080);\n", encoding="utf-8")

    findings = _scan_for_forbidden_product_backend_code(site)

    assert any(
        f.get("kind") == "server_entrypoint" and f["path"].startswith("dist/") for f in findings
    ), findings


def test_forbidden_product_scanner_still_flags_entrypoint_in_product_source(tmp_path):
    """A real server entrypoint in product source is still flagged (unchanged behavior)."""
    site = tmp_path / "product" / "site"
    src = site / "src"
    src.mkdir(parents=True)
    (src / "server.ts").write_text(
        "import http from 'http';\nhttp.createServer(handler).listen(3000);\n", encoding="utf-8"
    )

    findings = _scan_for_forbidden_product_backend_code(site)

    assert any(f.get("kind") == "server_entrypoint" for f in findings), findings


def test_bounded_product_inventory_flags_reserved_runtime_namespace_without_runtime_credit(tmp_path):
    business_root = tmp_path / "businesses" / "latexflow"
    site = business_root / "product" / "site"
    route_dir = site / "src" / "app" / "api" / "takyon" / "apps" / "latexflow" / "generate"
    route_dir.mkdir(parents=True)
    page = site / "app" / "page.tsx"
    page.parent.mkdir(parents=True)
    page.write_text("export default function Page() { return <main>Latexflow</main>; }\n", encoding="utf-8")
    (route_dir / "route.js").write_text(
        "export async function POST() {\n"
        "  return fetch('/api/takyon/apps/latexflow/generate', { method: 'POST' });\n"
        "}\n",
        encoding="utf-8",
    )

    inventory = _bounded_product_inventory(business_root, "product/site")

    assert inventory["reserved_namespace_routes"] == [
        {
            "path": "src/app/api/takyon/apps/latexflow/generate/route.js",
            "route": "/api/takyon/apps/latexflow/generate",
        }
    ]
    assert "runtime_api" not in inventory["runtime_integrations"]


def test_refresh_blocks_forbidden_product_backend_shims_with_exact_blockers(tmp_path):
    business_root = tmp_path / "businesses" / "latexflow"
    site = business_root / "product" / "site"
    route_dir = site / "src" / "app" / "api" / "takyon" / "apps" / "latexflow" / "generate"
    route_dir.mkdir(parents=True)
    (site / "index.html").write_text("<h1>Latexflow</h1>\n", encoding="utf-8")
    (site / "package.json").write_text(
        '{"name":"latexflow","private":true,"scripts":{"build":"vite build"},"devDependencies":{"vite":"5.4.21"}}\n',
        encoding="utf-8",
    )
    (site / "vite.config.ts").write_text("export default {};\n", encoding="utf-8")
    (route_dir / "route.js").write_text(
        "const base = process.env.TAKYON_OPENAI_BASE_URL || 'https://api.openai.com/v1';\n"
        "export async function POST() { return fetch(base, { headers: { Authorization: process.env.OPENAI_API_KEY } }); }\n",
        encoding="utf-8",
    )

    verification = _refresh_product_surface_path(business_root, "product/site", install=False)

    assert verification["status"] == "blocked"
    assert verification["blockers"] == [
        "product source calls an AI provider directly at src/app/api/takyon/apps/latexflow/generate/route.js:1; issue: direct provider host; snippet: const base = process.env.TAKYON_OPENAI_BASE_URL || 'https://api.openai.com/v1';; do not call providers directly or read provider keys/base URLs from product source; use the action runtime's `ctx` and shared Takyon rails instead",
        "product source reads provider credentials or base URLs directly at src/app/api/takyon/apps/latexflow/generate/route.js:2; issue: provider credential or base-url env read; snippet: export async function POST() { return fetch(base, { headers: { Authorization: process.env.OPENAI_API_KEY } }); }; do not call providers directly or read provider keys/base URLs from product source; use the action runtime's `ctx` and shared Takyon rails instead",
        "product source defines a server route handler on the pinned static Vite scaffold at src/app/api/takyon/apps/latexflow/generate/route.js:1; issue: server entrypoint; snippet: src/app/api/takyon/apps/latexflow/generate/route.js; pinned Vite SPA only: remove the product-side server entrypoint and put backend logic in product/site/actions/<name>.ts",
        "product source defines its own handler under the platform-reserved path /api/takyon/apps/latexflow/generate at src/app/api/takyon/apps/latexflow/generate/route.js; issue: reserved runtime namespace; platform rails are served by the Takyon runtime; remove the handler and call the rail from the client or a declared action",
    ]
    assert "product source violates runtime authority boundaries" in verification["error"]


def test_pinned_stack_scanner_detects_server_entrypoints(tmp_path):
    site = tmp_path / "product" / "site"
    (site / "src" / "app" / "api" / "thing").mkdir(parents=True)
    (site / "pages" / "api").mkdir(parents=True)
    (site / "src" / "app" / "api" / "thing" / "route.js").write_text(
        "export async function POST() {}\n", encoding="utf-8"
    )
    (site / "pages" / "api" / "hello.js").write_text(
        "export default function handler() {}\n", encoding="utf-8"
    )
    (site / "next.config.js").write_text("module.exports = {};\n", encoding="utf-8")
    (site / "server.js").write_text("const express = require('express');\n", encoding="utf-8")

    findings = _scan_for_pinned_stack_server_entrypoints(site)

    flagged = {finding["path"] for finding in findings}
    assert flagged == {
        "next.config.js",
        "src/app/api/thing/route.js",
        "pages/api/hello.js",
        "server.js",
    }
    assert all(finding["kind"] == "server_entrypoint" for finding in findings)
    assert all(finding["issue"] == "server entrypoint" for finding in findings)


def test_pinned_stack_scanner_ignores_honest_vite_spa(tmp_path):
    site = tmp_path / "product" / "site"
    (site / "src").mkdir(parents=True)
    (site / "vite.config.ts").write_text("export default {};\n", encoding="utf-8")
    (site / "src" / "App.tsx").write_text(
        "export default function App() { return null; }\n", encoding="utf-8"
    )

    assert _scan_for_pinned_stack_server_entrypoints(site) == []


def test_pinned_stack_scanner_blocks_client_generate_usage(tmp_path):
    site = tmp_path / "product" / "site"
    (site / "src" / "screens").mkdir(parents=True)
    (site / "src" / "screens" / "app-home.tsx").write_text(
        "export function AppHome() { return fetch('/generate', { method: 'POST' }); }\n",
        encoding="utf-8",
    )
    (site / "actions").mkdir(parents=True)
    (site / "actions" / "convert.ts").write_text(
        "export default async function convert(payload, ctx) { return ctx.generate(payload); }\n",
        encoding="utf-8",
    )

    findings = _scan_for_pinned_stack_client_generate_usage(site)

    assert findings == [
        {
            "path": "src/screens/app-home.tsx",
            "line": 1,
            "kind": "vite_client_generate",
            "issue": "client /generate call",
            "blocker": "product source calls the shared generate rail directly from pinned Vite client code",
            "snippet": "export function AppHome() { return fetch('/generate', { method: 'POST' }); }",
        }
    ]


def test_vite_ai_surface_requires_actions_instead_of_generate(tmp_path):
    site = tmp_path / "product" / "site"
    app = site / "app"
    app.mkdir(parents=True)
    (site / "index.html").write_text("<main><a href=\"/app\">Open app</a></main>\n", encoding="utf-8")
    (app / "index.html").write_text(
        """
        <form id="translate-form">
          <textarea name="prompt"></textarea>
          <button type="submit">Translate</button>
        </form>
        <script>
          fetch('/generate', { method: 'POST' });
        </script>
        """,
        encoding="utf-8",
    )
    surface = {
        "runtime_features": ["auth", "account", "checkout", "actions"],
        "metadata": {
            "subuser_app": {},
            "customer_experience": {"required_routes": ["/", "/app"], "required_app_tabs": ["Convert"]},
            "product_workflow": {
                "primary_job": "Generate compilable LaTeX from plain English.",
                "actions": [{"name": "convert-latex", "trigger": "http"}],
            },
        },
        "routes": [{"path": "/"}, {"path": "/app"}],
    }

    inventory = _bounded_product_inventory(tmp_path, "product/site", surface=surface)
    ok, blocker = _validate_product_surface_contract(inventory, surface)

    assert ok is True
    assert blocker == ""


def test_vite_ai_surface_accepts_use_action_runner(tmp_path):
    site = tmp_path / "product" / "site"
    app = site / "app"
    app.mkdir(parents=True)
    (site / "index.html").write_text("<main><a href=\"/app\">Open app</a></main>\n", encoding="utf-8")
    (app / "index.html").write_text(
        """
        <form id="translate-form">
          <textarea name="prompt"></textarea>
          <button type="submit">Translate</button>
        </form>
        <script>
          client.session();
          const runner = client.createActionRunner('convert-latex');
          runner.run({ prompt: 'x plus y' });
        </script>
        """,
        encoding="utf-8",
    )
    surface = {
        "runtime_features": ["auth", "account", "checkout", "actions"],
        "metadata": {
            "subuser_app": {"app_mode": "ai_tool", "subscription_style": "monthly", "frontend_stack": "vite_react_ts"},
            "customer_experience": {"required_routes": ["/", "/app"], "required_app_tabs": ["Convert"]},
            "product_workflow": {
                "primary_job": "Convert English to LaTeX.",
                "actions": [{"name": "convert-latex", "trigger": "http"}],
            },
        },
        "routes": [{"path": "/"}, {"path": "/app"}],
    }

    inventory = _bounded_product_inventory(tmp_path, "product/site", surface=surface)
    ok, blocker = _validate_product_surface_contract(inventory, surface)

    assert ok is True
    assert blocker == ""


def test_pinned_stack_gate_treats_legacy_alias_as_vite_stack(tmp_path):
    site = tmp_path / "product" / "site"
    site.mkdir(parents=True)
    (site / "next.config.js").write_text("module.exports = {};\n", encoding="utf-8")
    (site / "index.html").write_text("<html></html>\n", encoding="utf-8")

    legacy_surface = {"metadata": {"subuser_app": {"frontend_stack": "legacy"}}}
    pinned_surface = {"metadata": {"subuser_app": {"frontend_stack": "vite_react_ts"}}}

    legacy_inventory = _bounded_product_inventory(tmp_path, "product/site", surface=legacy_surface)
    pinned_inventory = _bounded_product_inventory(tmp_path, "product/site", surface=pinned_surface)

    legacy_kinds = {f.get("kind") for f in legacy_inventory.get("forbidden_findings") or []}
    pinned_kinds = {f.get("kind") for f in pinned_inventory.get("forbidden_findings") or []}
    assert "server_entrypoint" in legacy_kinds
    assert "server_entrypoint" in pinned_kinds


def test_server_entrypoint_blocker_text_names_the_static_lane():
    blockers = _format_forbidden_product_source_blockers(
        [
            {
                "path": "next.config.js",
                "line": 1,
                "kind": "server_entrypoint",
                "issue": "server entrypoint",
                "snippet": "next.config.js",
                "blocker": "product source carries a Next.js config on the pinned static Vite scaffold",
            }
        ],
        [],
    )
    assert blockers == [
        "product source carries a Next.js config on the pinned static Vite scaffold at next.config.js:1; "
        "issue: server entrypoint; snippet: next.config.js; pinned Vite SPA only: remove the "
        "product-side server entrypoint and put backend logic in product/site/actions/<name>.ts"
    ]


def test_client_generate_blocker_text_names_actions_requirement():
    blockers = _format_forbidden_product_source_blockers(
        [
            {
                "path": "src/screens/app-home.tsx",
                "line": 18,
                "kind": "vite_client_generate",
                "issue": "client /generate call",
                "snippet": "return fetch('/generate', { method: 'POST' });",
                "blocker": "product source calls the shared generate rail directly from pinned Vite client code",
            }
        ],
        [],
    )
    assert blockers == [
        "product source calls the shared generate rail directly from pinned Vite client code at "
        "src/screens/app-home.tsx:18; issue: client /generate call; snippet: return fetch('/generate', { "
        "method: 'POST' });; the browser must not call `/generate`; call a named action via "
        "createActionRunner/invokeAction instead"
    ]


def test_placeholder_token_marker_is_advisory_and_byte_exact(tmp_path, monkeypatch):
    from plugins.takyon import core as takyon_core

    scaffold_tokens = tmp_path / "scaffold-tokens.css"
    scaffold_tokens.write_text(":root { --tk-primary: #ff00aa; }\n", encoding="utf-8")
    monkeypatch.setattr(takyon_core, "_SCAFFOLD_PLACEHOLDER_TOKENS_PATH", scaffold_tokens)

    site = tmp_path / "product" / "site"
    (site / "src").mkdir(parents=True)
    (site / "src" / "tokens.css").write_text(":root { --tk-primary: #ff00aa; }\n", encoding="utf-8")

    marker = takyon_core._scaffold_placeholder_tokens_marker(site)
    assert marker is not None and marker["issue"] == "scaffold_placeholder_tokens"

    (site / "src" / "tokens.css").write_text(":root { --tk-primary: #0a0a0f; }\n", encoding="utf-8")
    assert takyon_core._scaffold_placeholder_tokens_marker(site) is None


def test_scaffold_placeholder_theme_blocks_publish():
    from plugins.takyon import core as takyon_core

    # A build that compiles but still carries the placeholder-tokens advisory must become a
    # do-not-publish blocker instead of silently shipping the scaffold theme.
    passed_refresh = {
        "status": "passed",
        "inventory": {
            "risk_markers": [
                {
                    "path": "src/tokens.css",
                    "issue": "scaffold_placeholder_tokens",
                    "snippet": "tokens.css is byte-identical to the scaffold placeholder; theme it from the design brief before publish",
                }
            ]
        },
    }
    blocker = takyon_core._scaffold_theme_unfinished_blocker(passed_refresh)
    assert "scaffold placeholder theme" in blocker
    assert "tokens.css" in blocker

    # No placeholder marker → no blocker → publish proceeds unchanged.
    clean_refresh = {"status": "passed", "inventory": {"risk_markers": []}}
    assert takyon_core._scaffold_theme_unfinished_blocker(clean_refresh) == ""


def test_scaffold_visible_shell_markers_flag_blank_core_screens(tmp_path):
    from plugins.takyon import core as takyon_core

    site = tmp_path / "product" / "site"
    (site / "src" / "screens").mkdir(parents=True)
    (site / "src" / "screens" / "landing.tsx").write_text(
        'export function LandingScreen() { return <main data-takyon-scaffold="landing" />; }\n',
        encoding="utf-8",
    )
    (site / "src" / "screens" / "app-home.tsx").write_text(
        'export function AppHomeScreen() { return <section data-takyon-scaffold="app-home" />; }\n',
        encoding="utf-8",
    )
    (site / "src" / "screens" / "profile.tsx").write_text(
        'export function ProfileScreen() { return <section data-takyon-scaffold="profile" />; }\n',
        encoding="utf-8",
    )
    # support.tsx is seeded as real, shippable content and is intentionally NOT gated as a
    # blank scaffold shell, so it must not appear in the visible-shell markers.
    (site / "src" / "screens" / "support.tsx").write_text(
        'export function FaqScreen() { return <main data-takyon-scaffold="support" />; }\n',
        encoding="utf-8",
    )

    markers = takyon_core._scaffold_visible_shell_markers(site)

    assert [marker["path"] for marker in markers] == [
        "src/screens/landing.tsx",
        "src/screens/app-home.tsx",
        "src/screens/profile.tsx",
    ]
    assert all(marker["issue"] == "scaffold_visible_shell" for marker in markers)


def test_scaffold_visible_shell_blocks_publish():
    from plugins.takyon import core as takyon_core

    passed_refresh = {
        "status": "passed",
        "inventory": {
            "risk_markers": [
                {
                    "path": "src/screens/landing.tsx",
                    "issue": "scaffold_visible_shell",
                    "snippet": "screen still carries the scaffold sentinel marker and renders the blank shell",
                }
            ]
        },
    }

    blocker = takyon_core._scaffold_visible_shell_unfinished_blocker(passed_refresh)

    assert "blank scaffold screen" in blocker
    assert "src/screens/landing.tsx" in blocker
    assert "/app/profile" in blocker
    assert "/privacy" in blocker


def test_kit_materialization_excludes_scaffold_and_artifacts(tmp_path):
    from plugins.takyon import core as takyon_core

    takyon_core._materialize_subuser_app_kit(tmp_path, slug="kit-test", surface=None)

    kit = tmp_path / "_takyon"
    assert (kit / "runtime-client.js").is_file()
    assert not (kit / "scaffold").exists()
    assert not list(kit.rglob("node_modules")) and not list(kit.rglob("dist"))


def _app_shell_surface(frontend_stack: str | None) -> dict:
    payload = {"app_mode": "standard_saas", "subscription_style": "monthly"}
    if frontend_stack:
        payload["frontend_stack"] = frontend_stack
    return {
        "metadata": {
            "subuser_app": payload,
            "customer_experience": {"required_app_tabs": ["Workspace"], "required_routes": ["/", "/app"]},
            "runtime_features": ["auth", "account", "checkout"],
        }
    }


def test_starter_seeds_scaffold_on_vite_lane(tmp_path):
    from plugins.takyon import core as takyon_core

    takyon_core._materialize_subuser_app_starter(
        tmp_path, slug="fresh-co", surface=_app_shell_surface("vite_react_ts")
    )

    assert (tmp_path / "vite.config.ts").is_file()
    assert (tmp_path / "package-lock.json").is_file()
    assert not (tmp_path / "next.config.js").exists()
    assert not (tmp_path / "_takyon").exists()
    assert not (tmp_path / "node_modules").exists() and not (tmp_path / "dist").exists()
    index_html = (tmp_path / "index.html").read_text(encoding="utf-8")
    assert "Fresh Co" in index_html and "__STARTER_SITE_NAME__" not in index_html


def test_starter_seeds_vite_when_lane_absent(tmp_path):
    from plugins.takyon import core as takyon_core

    takyon_core._materialize_subuser_app_starter(
        tmp_path, slug="old-co", surface=_app_shell_surface(None)
    )

    assert (tmp_path / "vite.config.ts").is_file()
    assert not (tmp_path / "next.config.js").exists()


def test_product_build_normalizer_restores_scaffold_owned_config(tmp_path):
    from plugins.takyon import core as takyon_core

    package = {
        "name": "plantmeter-587969",
        "private": True,
        "version": "0.1.0",
        "type": "module",
        "scripts": {"dev": "vite", "build": "tsc && vite build"},
        "dependencies": {
            "react": "^18.3.1",
            "react-dom": "^18.3.1",
            "react-router-dom": "^6.26.2",
        },
        "devDependencies": {
            "@vitejs/plugin-react": "^4.3.1",
            "typescript": "^5.5.3",
            "vite": "^5.4.2",
        },
    }
    (tmp_path / "package.json").write_text(
        json.dumps(package, indent=2) + "\n",
        encoding="utf-8",
    )
    (tmp_path / "package-lock.json").write_text("{}\n", encoding="utf-8")
    (tmp_path / "tsconfig.json").write_text(
        json.dumps({"compilerOptions": {"moduleResolution": "bundler"}, "include": ["src"]}) + "\n",
        encoding="utf-8",
    )
    (tmp_path / "vite.config.js").write_text("export default { build: { outDir: 'dist' } };\n", encoding="utf-8")
    (tmp_path / "vite.config.ts").write_text(
        "import { defineConfig } from 'vite';\nexport default defineConfig({});\n",
        encoding="utf-8",
    )

    result = takyon_core._normalize_supported_product_build_shape(
        tmp_path,
        scripts=package["scripts"],
        deps={**package["dependencies"], **package["devDependencies"]},
    )

    assert not result.get("blocked")
    assert {repair["path"] for repair in result["repairs"]} >= {
        "package.json",
        "package-lock.json",
        "tsconfig.json",
        "vite.config.ts",
        "vite.config.js",
    }
    normalized_package = json.loads((tmp_path / "package.json").read_text(encoding="utf-8"))
    assert normalized_package["name"] == "plantmeter-587969"
    assert normalized_package["scripts"]["build"] == "vite build"
    assert normalized_package["scripts"]["typecheck"] == "tsc --noEmit"
    assert normalized_package["dependencies"]["@supabase/supabase-js"] == "2.108.2"
    assert not (tmp_path / "vite.config.js").exists()
    assert '"@takyon/*": ["./_takyon/*"]' in (tmp_path / "tsconfig.json").read_text(encoding="utf-8")
    assert '"@takyon": new URL("./_takyon", import.meta.url).pathname' in (
        tmp_path / "vite.config.ts"
    ).read_text(encoding="utf-8")
    scaffold_lock = (
        takyon_core._subuser_app_scaffold_source_dir() / "package-lock.json"
    ).read_bytes()
    assert (tmp_path / "package-lock.json").read_bytes() == scaffold_lock


def test_frontend_stack_creation_default():
    from plugins.takyon import core as takyon_core

    assert takyon_core._frontend_stack_for_contract_upsert({}, None) == "vite_react_ts"
    assert takyon_core._frontend_stack_for_contract_upsert(None, None) == "vite_react_ts"
    assert takyon_core._frontend_stack_for_contract_upsert({"status": "active"}, None) is None
    assert takyon_core._frontend_stack_for_contract_upsert({}, "legacy") == "vite_react_ts"
