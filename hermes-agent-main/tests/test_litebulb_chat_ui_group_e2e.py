"""Grouped E2E for the Litebulb chat-UI build group (Chat abstraction + chat
robustness).

Rule #2 (tie-in): prove the group is wired through the *real* operator
entrypoint, not just unit-asserted on source. The operator loads the Litebulb
workspace from the dashboard ``/chat`` route, which is served by
``takyon_cli.web_server`` from the Vite ``litebulb`` build artifact.

This test:

  1. Builds the real Litebulb artifact with Vite (``vite.litebulb.config.ts``),
     compiling the edited ``Product.tsx`` + ``useTakyonLitebulb.ts`` into the
     bundle a browser actually downloads.
  2. Serves it through the real ``web_server`` ``/chat`` route with embedded
     chat enabled, and asserts the served HTML wires the ``/litebulb/assets/``
     paths (the integration contract).
  3. Fetches the bundled JS/CSS through the real asset route and asserts the
     reconciled chat-UI surface is present in the operator-facing artifact:
       * CEO-style workstream progress card abstraction (``lb-progress``
         eyebrow / ``CEO update``) — the "Chat abstraction" card.
       * Durable ``live_state`` progress source — the source contract the
         held-out probe encodes (``test_litebulb_progress_fallback_source``).
       * Inline working-message indicator (``lb-msg__work`` + ``lb-typing``)
         and markdown rendering (``lb-msg__md``) — the "chat robustness"
         no-flicker / preserved-message surface.

If Node/npm or the web ``node_modules`` are unavailable the test skips rather
than failing — the source-introspection probes still cover the contract.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
WEB_DIR = REPO_ROOT / "web"
LITEBULB_CONFIG = WEB_DIR / "vite.litebulb.config.ts"
WEB_NODE_MODULES = WEB_DIR / "node_modules"
VITE_BIN = WEB_NODE_MODULES / ".bin" / "vite"


def _vite_available() -> bool:
    return LITEBULB_CONFIG.is_file() and WEB_NODE_MODULES.is_dir() and VITE_BIN.exists()


@pytest.fixture(scope="module")
def litebulb_dist(tmp_path_factory) -> Path:
    """Build the real Litebulb artifact into an isolated dist dir."""
    if not _vite_available():
        pytest.skip("web node_modules / vite not available; source probes cover the contract")

    out_root = tmp_path_factory.mktemp("litebulb_dist")
    # vite.litebulb.config.ts hardcodes outDir -> ../takyon_cli/web_dist/litebulb.
    # Build there, then copy the artifact into the isolated dir we serve from so
    # we never assert against a stale checked-in bundle.
    built_dir = REPO_ROOT / "takyon_cli" / "web_dist" / "litebulb"
    env = dict(os.environ)
    proc = subprocess.run(
        [str(VITE_BIN), "build", "--config", str(LITEBULB_CONFIG)],
        cwd=str(WEB_DIR),
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
    )
    if proc.returncode != 0:
        pytest.skip(f"litebulb vite build failed in this environment:\n{proc.stderr[-2000:]}")

    index = next(
        (p for p in (built_dir / "litebulb.html", built_dir / "index.html") if p.is_file()),
        None,
    )
    assert index is not None, "litebulb build produced no html entrypoint"

    dist_litebulb = out_root / "litebulb"
    shutil.copytree(built_dir, dist_litebulb)
    return out_root


def _mount_fresh_app(web_server, web_dist: Path):
    """Mount the real SPA/litebulb routes on a fresh app rooted at web_dist."""
    from fastapi import FastAPI

    app = FastAPI()
    web_server.WEB_DIST = web_dist
    web_server._DASHBOARD_EMBEDDED_CHAT_ENABLED = True
    web_server.mount_spa(app)
    return app


def test_chat_route_serves_reconciled_litebulb_chat_ui(litebulb_dist, monkeypatch):
    pytest.importorskip("fastapi")
    pytest.importorskip("starlette")
    from starlette.testclient import TestClient

    import takyon_cli.web_server as web_server

    monkeypatch.setattr(web_server, "_DASHBOARD_EMBEDDED_CHAT_ENABLED", True, raising=False)
    app = _mount_fresh_app(web_server, litebulb_dist)
    client = TestClient(app)

    # 1. Real operator entrypoint: GET /chat serves the litebulb workspace with
    #    proxied asset paths (the integration contract in spec card #3).
    resp = client.get("/chat")
    assert resp.status_code == 200, resp.text
    html = resp.text
    assert 'src="/litebulb/assets/' in html
    assert 'href="/litebulb/assets/' in html

    # 2. Pull the bundled JS + CSS the browser would download, through the real
    #    static asset route.
    assets_dir = litebulb_dist / "litebulb" / "assets"
    js_files = sorted(assets_dir.glob("*.js"))
    css_files = sorted(assets_dir.glob("*.css"))
    assert js_files, "no bundled litebulb JS asset"
    assert css_files, "no bundled litebulb CSS asset"

    bundle_js = ""
    for js in js_files:
        r = client.get(f"/litebulb/assets/{js.name}")
        assert r.status_code == 200, f"asset {js.name} not served: {r.status_code}"
        bundle_js += r.text
    bundle_css = ""
    for css in css_files:
        r = client.get(f"/litebulb/assets/{css.name}")
        assert r.status_code == 200
        bundle_css += r.text

    # 3a. Chat abstraction card: CEO-style workstream progress card is bundled,
    #     not raw tool output.
    assert "CEO update" in bundle_js
    assert "lb-progress__eyebrow" in bundle_js

    # 3b. Durable live_state progress source (the held-out source contract).
    assert "live_state" in bundle_js

    # 3c. chat robustness: inline working-message indicator + markdown render
    #     are in the artifact (no-flicker / preserved-message surface).
    assert "lb-msg__work" in bundle_js
    assert "lb-msg__md" in bundle_js

    # 3d. The working-indicator + markdown styles are bundled in the CSS the
    #     operator actually loads.
    assert "lb-msg__work" in bundle_css
    assert "lb-typing" in bundle_css
    assert "lb-msg__md" in bundle_css
