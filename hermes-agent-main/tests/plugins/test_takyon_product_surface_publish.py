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
    storage_root = tmp_path / "storage"
    live_root = publish_root / "plannerly"
    (live_root / "_takyon").mkdir(parents=True)
    (live_root / "_takyon" / "runtime.json").write_text('{"ok":true}\n', encoding="utf-8")
    (live_root / "index.html").write_text("<html><body>old site</body></html>\n", encoding="utf-8")

    monkeypatch.setenv("TAKYON_HOME", str(tmp_path))
    monkeypatch.setenv("TAKYON_PRODUCT_SITE_ROOT", str(publish_root))
    monkeypatch.setenv("TAKYON_PRODUCT_LOCAL_BASE_URL", "http://127.0.0.1:9000/site")
    monkeypatch.setenv("TAKYON_STORAGE_BACKEND", "local")
    monkeypatch.setenv("TAKYON_STORAGE_LOCAL_DIR", str(storage_root))
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


def test_publish_product_surface_prefers_dist_over_source_root_when_both_exist(tmp_path, monkeypatch):
    business_root = tmp_path / "businesses" / "plannerly"
    site = business_root / "product" / "site"
    dist = site / "dist"
    dist.mkdir(parents=True)
    (site / "index.html").write_text("<html><body>source site</body></html>\n", encoding="utf-8")
    (dist / "index.html").write_text("<html><body>dist site</body></html>\n", encoding="utf-8")
    (dist / "assets").mkdir()
    (dist / "assets" / "app.css").write_text("body{color:#abcdef}\n", encoding="utf-8")

    publish_root = tmp_path / "product-sites"
    storage_root = tmp_path / "storage"
    monkeypatch.setenv("TAKYON_HOME", str(tmp_path))
    monkeypatch.setenv("TAKYON_PRODUCT_SITE_ROOT", str(publish_root))
    monkeypatch.setenv("TAKYON_PRODUCT_LOCAL_BASE_URL", "http://127.0.0.1:9000/site")
    monkeypatch.setenv("TAKYON_STORAGE_BACKEND", "local")
    monkeypatch.setenv("TAKYON_STORAGE_LOCAL_DIR", str(storage_root))
    monkeypatch.setattr(takyon_core, "_ensure_product_static_caddy_route", lambda **_: (None, ""))

    result = takyon_core._publish_product_surface_path(
        business_root=business_root,
        slug="plannerly",
        source_path="product/site",
        publish_target="https://plannerly.fourmanifold.com/",
    )

    live_root = publish_root / "plannerly"
    assert result["status"] == "published"
    assert result["publish_source_path"] == "product/site/dist"
    assert (live_root / "index.html").read_text(encoding="utf-8") == "<html><body>dist site</body></html>\n"
    assert (live_root / "assets" / "app.css").read_text(encoding="utf-8") == "body{color:#abcdef}\n"


def test_publish_product_surface_blocks_package_managed_source_without_built_output(tmp_path, monkeypatch):
    business_root = tmp_path / "businesses" / "plannerly"
    site = business_root / "product" / "site"
    site.mkdir(parents=True)
    (site / "index.html").write_text("<html><body>source site</body></html>\n", encoding="utf-8")
    (site / "package.json").write_text(
        """
        {
          "name": "plannerly",
          "private": true,
          "scripts": {
            "build": "vite build"
          },
          "dependencies": {
            "vite": "5.4.21"
          }
        }
        """,
        encoding="utf-8",
    )

    monkeypatch.setenv("TAKYON_HOME", str(tmp_path))

    result = takyon_core._publish_product_surface_path(
        business_root=business_root,
        slug="plannerly",
        source_path="product/site",
        publish_target="https://plannerly.fourmanifold.com/",
    )

    assert result["status"] == "blocked"
    assert "dist/index.html" in result["blocker"]
    assert "source/index.html" not in result["blocker"]


def test_publish_product_surface_syncs_built_workspace_to_canonical_storage_before_declaring_published(tmp_path, monkeypatch):
    business_root = tmp_path / "businesses" / "plannerly"
    site = business_root / "product" / "site"
    dist = site / "dist"
    dist.mkdir(parents=True)
    (site / "index.html").write_text("<html><body>source site</body></html>\n", encoding="utf-8")
    (dist / "index.html").write_text("<html><body>dist site</body></html>\n", encoding="utf-8")
    (dist / "assets").mkdir()
    (dist / "assets" / "app.css").write_text("body{color:#fedcba}\n", encoding="utf-8")

    publish_root = tmp_path / "product-sites"
    storage_root = tmp_path / "storage"
    monkeypatch.setenv("TAKYON_HOME", str(tmp_path))
    monkeypatch.setenv("TAKYON_PRODUCT_SITE_ROOT", str(publish_root))
    monkeypatch.setenv("TAKYON_PRODUCT_LOCAL_BASE_URL", "http://127.0.0.1:9000/site")
    monkeypatch.setenv("TAKYON_STORAGE_BACKEND", "local")
    monkeypatch.setenv("TAKYON_STORAGE_LOCAL_DIR", str(storage_root))
    monkeypatch.setattr(takyon_core, "_ensure_product_static_caddy_route", lambda **_: (None, ""))

    result = takyon_core._publish_product_surface_path(
        business_root=business_root,
        slug="plannerly",
        source_path="product/site",
        publish_target="https://plannerly.fourmanifold.com/",
    )

    stored_dist_root = storage_root / "plannerly" / "product" / "site" / "dist"
    assert result["status"] == "published"
    assert result["publish_root"] == str(publish_root / "plannerly")
    assert result["publish_mode"] == "local_static"
    assert result["public_url"] == "http://127.0.0.1:9000/site/plannerly/"
    assert result["activation_target"] == ""
    assert (stored_dist_root / "index.html").read_text(encoding="utf-8") == "<html><body>dist site</body></html>\n"
    assert (stored_dist_root / "assets" / "app.css").read_text(encoding="utf-8") == "body{color:#fedcba}\n"


def test_refresh_product_surface_builds_package_managed_vite_app_instead_of_short_circuiting_to_source(
    tmp_path,
    monkeypatch,
):
    business_root = tmp_path / "businesses" / "plannerly"
    site = business_root / "product" / "site"
    src = site / "src"
    src.mkdir(parents=True)
    (site / "index.html").write_text(
        "<!doctype html><html><body><div id='root'></div><script type='module' src='/src/main.tsx'></script></body></html>\n",
        encoding="utf-8",
    )
    (src / "main.tsx").write_text("console.log('hello');\n", encoding="utf-8")
    (site / "package.json").write_text(
        """
        {
          "name": "plannerly",
          "private": true,
          "scripts": {
            "build": "vite build",
            "typecheck": "tsc --noEmit"
          },
          "dependencies": {
            "react": "18.3.1",
            "vite": "5.4.21"
          }
        }
        """,
        encoding="utf-8",
    )

    monkeypatch.setenv("TAKYON_HOME", str(tmp_path))
    monkeypatch.setattr(
        takyon_core,
        "_javascript_package_manager_command",
        lambda name: {"available": True, "name": "npm", "command": ["/usr/bin/npm"], "source": "test"},
    )

    def fake_run_surface_command(command, *, cwd, timeout_seconds, env):
        if command == ["/usr/bin/npm", "install", "--ignore-scripts"]:
            return {"command": command, "status": "passed", "stdout": "", "stderr": ""}
        if command == ["/usr/bin/npm", "run", "build"]:
            dist = cwd / "dist"
            (dist / "assets").mkdir(parents=True, exist_ok=True)
            (dist / "index.html").write_text("<html><body>dist site</body></html>\n", encoding="utf-8")
            (dist / "assets" / "app.css").write_text("body{color:#0f0}\n", encoding="utf-8")
            return {"command": command, "status": "passed", "stdout": "", "stderr": ""}
        if command == ["/usr/bin/npm", "run", "typecheck"]:
            return {"command": command, "status": "passed", "stdout": "", "stderr": ""}
        raise AssertionError(f"unexpected surface command: {command}")

    monkeypatch.setattr(takyon_core, "_run_surface_command", fake_run_surface_command)

    result = takyon_core._refresh_product_surface_path(
        business_root,
        "product/site",
        install=True,
    )

    assert result["status"] == "passed"
    assert result["kind"] == "node_build"
    assert [check["command"] for check in result["checks"]] == [
        ["/usr/bin/npm", "install", "--ignore-scripts"],
        ["/usr/bin/npm", "run", "build"],
        ["/usr/bin/npm", "run", "typecheck"],
    ]
    assert (site / "dist" / "index.html").read_text(encoding="utf-8") == "<html><body>dist site</body></html>\n"


def test_refresh_product_surface_blocks_when_build_produces_no_publishable_output(tmp_path, monkeypatch):
    business_root = tmp_path / "businesses" / "plannerly"
    site = business_root / "product" / "site"
    src = site / "src"
    src.mkdir(parents=True)
    (site / "index.html").write_text(
        "<!doctype html><html><body><div id='root'></div><script type='module' src='/src/main.tsx'></script></body></html>\n",
        encoding="utf-8",
    )
    (src / "main.tsx").write_text("console.log('hello');\n", encoding="utf-8")
    (site / "package.json").write_text(
        """
        {
          "name": "plannerly",
          "private": true,
          "scripts": {
            "build": "vite build"
          },
          "dependencies": {
            "vite": "5.4.21"
          }
        }
        """,
        encoding="utf-8",
    )

    monkeypatch.setenv("TAKYON_HOME", str(tmp_path))
    monkeypatch.setattr(
        takyon_core,
        "_javascript_package_manager_command",
        lambda name: {"available": True, "name": "npm", "command": ["/usr/bin/npm"], "source": "test"},
    )

    def fake_run_surface_command(command, *, cwd, timeout_seconds, env):
        if command in (
            ["/usr/bin/npm", "install", "--ignore-scripts"],
            ["/usr/bin/npm", "run", "build"],
        ):
            return {"command": command, "status": "passed", "stdout": "", "stderr": ""}
        raise AssertionError(f"unexpected surface command: {command}")

    monkeypatch.setattr(takyon_core, "_run_surface_command", fake_run_surface_command)

    result = takyon_core._refresh_product_surface_path(
        business_root,
        "product/site",
        install=True,
    )

    assert result["status"] == "blocked"
    assert "no publishable output exists" in result["error"]
    assert "source root" not in result["error"]


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
