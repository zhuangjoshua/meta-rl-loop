"""Hermetic product refresh enforcement tests."""

from __future__ import annotations

import json
import shutil
import subprocess

import pytest

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


def test_action_backed_product_refuses_unlimited_usage_claim(tmp_path):
    site = tmp_path / "product" / "site"
    site.mkdir(parents=True)
    (site / "index.html").write_text(
        "<main>Subscribe to unlock unlimited proposals and revisions.</main>\n"
        "<script>client.checkout({ plan_key: 'pro' });</script>\n",
        encoding="utf-8",
    )
    (site / "actions").mkdir()
    (site / "actions" / "generate.ts").write_text(
        "export default async (payload, ctx) => ({ ok: true });\n", encoding="utf-8"
    )
    (site / "src").mkdir()
    (site / "src" / "app.ts").write_text(
        'client.invokeAction("generate", {});\n', encoding="utf-8"
    )
    surface = {
        "runtime_features": ["auth", "account", "checkout", "actions"],
        "routes": [{"path": "/"}, {"path": "/app"}],
        "metadata": {"product_workflow": {"actions": [{"name": "generate", "trigger": "http"}]}},
    }

    inventory = _bounded_product_inventory(tmp_path, "product/site", surface=surface)
    ok, blocker = _validate_product_surface_contract(inventory, surface)

    assert ok is False
    assert "unlimited action-backed usage" in blocker


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


def test_requested_workflow_gate_rejects_buildable_unchanged_app_starter(tmp_path, monkeypatch):
    from plugins.takyon import core as takyon_core

    scaffold = tmp_path / "scaffold"
    site = tmp_path / "product" / "site"
    scaffold_home = scaffold / "src" / "screens" / "app-home.tsx"
    site_home = site / "src" / "screens" / "app-home.tsx"
    scaffold_home.parent.mkdir(parents=True)
    site_home.parent.mkdir(parents=True)
    seed = "export function AppHomeScreen() { return <main>Starter</main>; }\n"
    scaffold_home.write_text(seed, encoding="utf-8")
    site_home.write_text(seed, encoding="utf-8")
    monkeypatch.setattr(takyon_core, "_subuser_app_scaffold_source_dir", lambda: scaffold)
    surface = {"metadata": {"workflow_completion_required": True}}

    markers = takyon_core._requested_workflow_completeness_markers(site, surface)
    refresh = {"status": "passed", "inventory": {"risk_markers": markers}}
    blocker = takyon_core._requested_workflow_unfinished_blocker(refresh)

    assert any("byte-identical" in marker["snippet"] for marker in markers)
    assert any("no UI-referenced runnable action" in marker["snippet"] for marker in markers)
    assert "incomplete even though the source builds" in blocker
    assert "before publish" in blocker


def test_requested_workflow_gate_accepts_action_generate_and_records_wiring(tmp_path, monkeypatch):
    from plugins.takyon import core as takyon_core

    scaffold = tmp_path / "scaffold"
    site = tmp_path / "product" / "site"
    scaffold_home = scaffold / "src" / "screens" / "app-home.tsx"
    site_home = site / "src" / "screens" / "app-home.tsx"
    action = site / "actions" / "generate-proposal.ts"
    scaffold_home.parent.mkdir(parents=True)
    site_home.parent.mkdir(parents=True)
    action.parent.mkdir(parents=True)
    scaffold_home.write_text("export const AppHomeScreen = () => <main>Starter</main>;\n", encoding="utf-8")
    site_home.write_text(
        'const runner = useActionRunner("generate-proposal");\n'
        "async function saveAndReopen() { await saveRecord({ record_type: 'proposal' }); return listRecords('proposal'); }\n",
        encoding="utf-8",
    )
    action.write_text(
        "export default async function generateProposal(payload, ctx) {\n"
        "  const generated = await ctx.generate({ messages: [{ role: 'user', content: String(payload) }] });\n"
        "  return { text: generated.text };\n"
        "}\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(takyon_core, "_subuser_app_scaffold_source_dir", lambda: scaffold)
    surface = {"metadata": {"workflow_completion_required": True}}

    assert takyon_core._requested_workflow_completeness_markers(site, surface) == []
    assert takyon_core._requested_workflow_unfinished_blocker(
        {"status": "passed", "inventory": {"risk_markers": []}}
    ) == ""


def test_app_access_gate_null_markers_flag_blank_app_routes(tmp_path):
    from plugins.takyon import core as takyon_core

    site = tmp_path / "product" / "site"
    (site / "src" / "screens").mkdir(parents=True)
    (site / "src" / "screens" / "app-home.tsx").write_text(
        "export function AppHomeScreen() {\n"
        "  const access = useViewerAccess();\n"
        "  if (!access.entitled) {\n"
        "    return null;\n"
        "  }\n"
        "  return <main>Ready</main>;\n"
        "}\n",
        encoding="utf-8",
    )
    (site / "src" / "screens" / "profile.tsx").write_text(
        "export function ProfileScreen() {\n"
        "  const viewerAccess = useViewerAccess();\n"
        "  if (!viewerAccess.authenticated) return null;\n"
        "  return <main>Profile</main>;\n"
        "}\n",
        encoding="utf-8",
    )

    markers = takyon_core._app_access_gate_null_markers(site)

    assert [marker["path"] for marker in markers] == [
        "src/screens/app-home.tsx",
        "src/screens/profile.tsx",
    ]
    assert all(marker["issue"] == "app_access_gate_returns_null" for marker in markers)


def test_app_access_gate_null_blocks_publish():
    from plugins.takyon import core as takyon_core

    passed_refresh = {
        "status": "passed",
        "inventory": {
            "risk_markers": [
                {
                    "path": "src/screens/app-home.tsx",
                    "issue": "app_access_gate_returns_null",
                    "snippet": "if (!access.entitled) { return null;",
                }
            ]
        },
    }

    blocker = takyon_core._app_access_gate_null_unfinished_blocker(passed_refresh)

    assert "render blank" in blocker
    assert "src/screens/app-home.tsx" in blocker
    assert "useProductAuth()" in blocker
    assert "resolveViewerCta()" in blocker


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


def test_starter_uses_strategy_copy_when_surface_notes_are_empty(tmp_path):
    from plugins.takyon import core as takyon_core

    site_root = tmp_path / "product" / "site"
    strategy = tmp_path / "research" / "strategy.md"
    site_root.mkdir(parents=True)
    strategy.parent.mkdir(parents=True)
    strategy.write_text(
        """# MetaProof — Initial Strategy

## Business name
MetaProof (metaproof0704c)

## Tagline
Turn customer conversations into your next product decision.

## Core value proposition
Transform raw interview notes into prioritized product briefs and weekly insight digests.
""",
        encoding="utf-8",
    )

    takyon_core._materialize_subuser_app_starter(
        site_root, slug="metaproof0704c", surface=_app_shell_surface("vite_react_ts")
    )

    index_html = (site_root / "index.html").read_text(encoding="utf-8")
    llms = (site_root / "public" / "llms.txt").read_text(encoding="utf-8")

    assert "<title>MetaProof</title>" in index_html
    assert 'content="Turn customer conversations into your next product decision."' in index_html
    assert "Metaproof0704c" not in index_html
    assert "MetaProof" in llms
    assert "Turn customer conversations into your next product decision." in llms


def test_starter_uses_tagline_when_strategy_title_is_generic(tmp_path):
    from plugins.takyon import core as takyon_core

    site_root = tmp_path / "product" / "site"
    strategy = tmp_path / "research" / "strategy.md"
    site_root.mkdir(parents=True)
    strategy.parent.mkdir(parents=True)
    strategy.write_text(
        """# metaproof0704i — Landing Brief

## Business name
metaproof0704i

## Tagline
Turn every customer conversation into the next roadmap decision.
""",
        encoding="utf-8",
    )

    takyon_core._materialize_subuser_app_starter(
        site_root, slug="metaproof0704i", surface=_app_shell_surface("vite_react_ts")
    )

    index_html = (site_root / "index.html").read_text(encoding="utf-8")

    assert (
        "<title>Turn every customer conversation into the next roadmap decision.</title>"
        in index_html
    )
    assert 'content="Turn every customer conversation into the next roadmap decision."' in index_html
    assert "<title>metaproof0704i</title>" not in index_html


def test_starter_owned_metadata_refreshes_on_rebuild(tmp_path):
    from plugins.takyon import core as takyon_core

    surface = _app_shell_surface("vite_react_ts")
    surface["notes"] = "Original metadata"
    takyon_core._materialize_subuser_app_starter(tmp_path, slug="fresh-co", surface=surface)

    (tmp_path / "index.html").write_text(
        """<!doctype html>
<html>
  <head>
    <!-- SCAFFOLD-PLACEHOLDER: stale -->
    <title>Wrong Title</title>
    <meta name="description" content="Wrong description" />
  </head>
  <body></body>
</html>
""",
        encoding="utf-8",
    )

    refreshed_surface = _app_shell_surface("vite_react_ts")
    refreshed_surface["notes"] = "Fresh metadata for rebuild"
    takyon_core._rematerialize_starter_owned_files(  # type: ignore[attr-defined]
        tmp_path,
        slug="fresh-co",
        surface=refreshed_surface,
    )

    index_html = (tmp_path / "index.html").read_text(encoding="utf-8")
    assert "SCAFFOLD-PLACEHOLDER" not in index_html
    assert "<title>Fresh Co</title>" in index_html
    assert 'content="Fresh metadata for rebuild"' in index_html
    assert "__STARTER_SITE_NAME__" not in index_html
    assert "Wrong Title" not in index_html

    llms = (tmp_path / "public" / "llms.txt").read_text(encoding="utf-8")
    assert "Fresh Co" in llms
    assert "Fresh metadata for rebuild" in llms
    assert "__STARTER_PUBLIC_ORIGIN__" not in llms


def test_starter_metadata_never_promotes_transient_auth_failure_to_seo_copy(tmp_path):
    from plugins.takyon import core as takyon_core

    landing = tmp_path / "src" / "screens" / "landing.tsx"
    landing.parent.mkdir(parents=True)
    landing.write_text(
        "<h1>Sign-in is temporarily unavailable.</h1>"
        "<p>Sign-in is temporarily unavailable. Please try again shortly.</p>\n",
        encoding="utf-8",
    )
    surface = _app_shell_surface("vite_react_ts")
    surface["notes"] = "Proposal workflow for independent consultants."

    metadata = takyon_core._subuser_app_starter_strings(
        surface, slug="proposal-flow", workspace_root=tmp_path
    )

    assert metadata["title"] == "Proposal Flow"
    assert "Sign-in is temporarily unavailable" not in metadata["title"]
    assert "Sign-in is temporarily unavailable" not in metadata["description"]


def test_starter_refresh_uses_custom_landing_copy_when_notes_are_empty(tmp_path):
    from plugins.takyon import core as takyon_core

    strategy = tmp_path / "research" / "strategy.md"
    strategy.parent.mkdir(parents=True)
    strategy.write_text(
        """# fresh-co — Strategy

## Tagline
Ignore this tagline for the title fallback.
""",
        encoding="utf-8",
    )

    takyon_core._materialize_subuser_app_starter(
        tmp_path, slug="fresh-co", surface=_app_shell_surface("vite_react_ts")
    )

    (tmp_path / "src" / "screens" / "landing.tsx").write_text(
        """
export function LandingScreen() {
  return (
    <main>
      <h1>Turn customer conversations into your next product decision.</h1>
      <p>Paste your interview notes and get a ranked product brief in minutes.</p>
    </main>
  );
}
""",
        encoding="utf-8",
    )

    takyon_core._rematerialize_starter_owned_files(  # type: ignore[attr-defined]
        tmp_path,
        slug="fresh-co",
        surface=_app_shell_surface("vite_react_ts"),
    )

    index_html = (tmp_path / "index.html").read_text(encoding="utf-8")

    assert (
        "<title>Turn customer conversations into your next product decision.</title>"
        in index_html
    )
    assert "Get started with Fresh Co" not in index_html


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
        "tsconfig.actions.json",
        "action-env.d.ts",
        "vite.config.ts",
        "vite.config.js",
    }
    normalized_package = json.loads((tmp_path / "package.json").read_text(encoding="utf-8"))
    assert normalized_package["name"] == "plantmeter-587969"
    assert normalized_package["scripts"]["build"] == "vite build"
    assert normalized_package["scripts"]["typecheck"] == (
        "tsc --noEmit -p tsconfig.json && tsc --noEmit -p tsconfig.actions.json"
    )
    assert normalized_package["dependencies"]["@supabase/supabase-js"] == "2.108.2"
    assert not (tmp_path / "vite.config.js").exists()
    assert '"@takyon/*": ["./_takyon/*"]' in (tmp_path / "tsconfig.json").read_text(encoding="utf-8")
    action_config = json.loads((tmp_path / "tsconfig.actions.json").read_text(encoding="utf-8"))
    assert action_config["compilerOptions"]["lib"] == ["ES2020"]
    assert action_config["compilerOptions"]["noImplicitAny"] is True
    assert action_config["files"] == ["action-env.d.ts"]
    assert action_config["include"] == ["actions/**/*.ts"]
    assert '"@takyon": new URL("./_takyon", import.meta.url).pathname' in (
        tmp_path / "vite.config.ts"
    ).read_text(encoding="utf-8")
    scaffold_lock = (
        takyon_core._subuser_app_scaffold_source_dir() / "package-lock.json"
    ).read_bytes()
    assert (tmp_path / "package-lock.json").read_bytes() == scaffold_lock


def test_scaffold_typechecks_browser_and_action_code_in_separate_global_environments(tmp_path):
    from plugins.takyon import core as takyon_core

    scaffold = takyon_core._subuser_app_scaffold_source_dir()
    tsc = scaffold / "node_modules" / ".bin" / "tsc"
    if shutil.which("node") is None or not tsc.is_file():
        pytest.skip("scaffold TypeScript compiler is not installed")

    shutil.copy2(scaffold / "tsconfig.json", tmp_path / "tsconfig.json")
    shutil.copy2(scaffold / "tsconfig.actions.json", tmp_path / "tsconfig.actions.json")
    shutil.copy2(scaffold / "action-env.d.ts", tmp_path / "action-env.d.ts")
    src = tmp_path / "src"
    actions = tmp_path / "actions"
    kit = tmp_path / "_takyon"
    src.mkdir()
    actions.mkdir()
    kit.mkdir()
    shutil.copy2(
        scaffold.parent / "runtime-client.d.ts",
        kit / "runtime-client.d.ts",
    )
    # Mirror a real materialized workspace: the platform JS implementation is present beside its
    # declaration, but the browser project typechecks product source through that declaration
    # boundary instead of strict-checking platform-owned JS as if it were generated app source.
    shutil.copy2(
        scaffold.parent / "runtime-client.js",
        kit / "runtime-client.js",
    )
    (src / "browser.ts").write_text(
        "export const browserUrl = window.location.href;\n"
        "export const browserTitle = document.title;\n",
        encoding="utf-8",
    )
    no_actions = subprocess.run(
        [str(tsc), "--noEmit", "-p", "tsconfig.actions.json"],
        cwd=tmp_path,
        text=True,
        capture_output=True,
        check=False,
    )
    assert no_actions.returncode == 0, no_actions.stdout + no_actions.stderr
    action = actions / "run.ts"
    action.write_text(
        'import type { RecordRef } from "../_takyon/runtime-client.js";\n'
        "export default async function run(\n"
        "  payload: { url: string; ref: RecordRef },\n"
        "  ctx: TakyonActionContext,\n"
        ") {\n"
        "  const response = await fetch(String(payload.url));\n"
        "  const record = await ctx.readRecord(payload.ref);\n"
        "  return { ok: response.ok, record };\n"
        "}\n",
        encoding="utf-8",
    )

    browser = subprocess.run(
        [str(tsc), "--noEmit", "-p", "tsconfig.json"],
        cwd=tmp_path,
        text=True,
        capture_output=True,
        check=False,
    )
    assert browser.returncode == 0, browser.stdout + browser.stderr
    server_safe = subprocess.run(
        [str(tsc), "--noEmit", "-p", "tsconfig.actions.json"],
        cwd=tmp_path,
        text=True,
        capture_output=True,
        check=False,
    )
    assert server_safe.returncode == 0, server_safe.stdout + server_safe.stderr

    action.write_text(
        "export default async function run(\n"
        "  payload: TakyonActionPayload,\n"
        "  ctx: TakyonActionContext,\n"
        ") {\n"
        "  await ctx.getRecord(\"proposal\", \"ui-invented-slug\");\n"
        "  await ctx.publishRecord(\"ui-invented-slug\");\n"
        "  return {\n"
        "    payload,\n"
        "    window,\n"
        "    document,\n"
        "    location,\n"
        "    navigator,\n"
        "    caches,\n"
        "    indexedDB,\n"
        "    self,\n"
        "    globalLocation: globalThis.location,\n"
        "  };\n"
        "}\n",
        encoding="utf-8",
    )
    browser_only = subprocess.run(
        [str(tsc), "--noEmit", "-p", "tsconfig.actions.json"],
        cwd=tmp_path,
        text=True,
        capture_output=True,
        check=False,
    )
    diagnostics = browser_only.stdout + browser_only.stderr
    assert browser_only.returncode != 0
    for unavailable in ("window", "document", "location", "navigator", "caches", "indexedDB", "self"):
        assert unavailable in diagnostics
    assert "Expected 1 arguments, but got 2" in diagnostics
    assert "Property 'publishRecord' does not exist" in diagnostics

    (src / "unchecked-record-read.js").write_text(
        'import { createSubuserRuntimeClient } from "../_takyon/runtime-client.js";\n'
        'const client = createSubuserRuntimeClient({ runtimeFeatures: ["records"] });\n'
        'client.getRecord("proposal", "ui-invented-slug");\n',
        encoding="utf-8",
    )
    checked_javascript = subprocess.run(
        [str(tsc), "--noEmit", "-p", "tsconfig.json"],
        cwd=tmp_path,
        text=True,
        capture_output=True,
        check=False,
    )
    js_diagnostics = checked_javascript.stdout + checked_javascript.stderr
    assert checked_javascript.returncode != 0
    assert "Expected 1 arguments, but got 2" in js_diagnostics


def test_frontend_stack_creation_default():
    from plugins.takyon import core as takyon_core

    assert takyon_core._frontend_stack_for_contract_upsert({}, None) == "vite_react_ts"
    assert takyon_core._frontend_stack_for_contract_upsert(None, None) == "vite_react_ts"
    assert takyon_core._frontend_stack_for_contract_upsert({"status": "active"}, None) is None
    assert takyon_core._frontend_stack_for_contract_upsert({}, "legacy") == "vite_react_ts"
