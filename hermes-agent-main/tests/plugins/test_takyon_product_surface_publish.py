from __future__ import annotations

from pathlib import Path

from plugins.takyon import core as takyon_core


def test_publish_product_surface_writes_immutable_build_and_flips_current_pointer(tmp_path, monkeypatch):
    business_root = tmp_path / "businesses" / "plannerly"
    site = business_root / "product" / "site"
    dist = site / "dist"
    dist.mkdir(parents=True)
    (dist / "index.html").write_text("<html><body>new site</body></html>\n", encoding="utf-8")
    (dist / "assets").mkdir()
    (dist / "assets" / "app.css").write_text("body{color:#123456}\n", encoding="utf-8")

    publish_root = tmp_path / "product-sites"
    storage_root = tmp_path / "storage"
    monkeypatch.setenv("TAKYON_HOME", str(tmp_path))
    monkeypatch.setenv("TAKYON_PRODUCT_SITE_ROOT", str(publish_root))
    monkeypatch.setenv("TAKYON_STORAGE_BACKEND", "local")
    monkeypatch.setenv("TAKYON_STORAGE_LOCAL_DIR", str(storage_root))

    observed: dict[str, object] = {}
    real_copytree = takyon_core.shutil.copytree

    def _recording_copytree(src, dst, *args, **kwargs):
        src_path = Path(src).resolve()
        if src_path == dist.resolve() and "publish_copy_target" not in observed:
            observed["publish_copy_target"] = Path(dst)
        return real_copytree(src, dst, *args, **kwargs)

    monkeypatch.setattr(takyon_core.shutil, "copytree", _recording_copytree)

    result = takyon_core._publish_product_surface_path(
        business_root=business_root,
        slug="plannerly",
        source_path="product/site",
        publish_target="https://plannerly.fourmanifold.com/",
    )

    assert result["status"] == "published"
    live_root = publish_root / "plannerly"
    current_root = live_root / "current"
    build_root = live_root / "builds" / result["live_build_id"]
    assert Path(observed["publish_copy_target"]).name == build_root.name
    assert Path(observed["publish_copy_target"]).parent.name.startswith(".takyon-stage-")
    assert current_root.is_symlink()
    assert current_root.resolve() == build_root.resolve()
    assert (current_root / "index.html").read_text(encoding="utf-8") == "<html><body>new site</body></html>\n"
    assert (current_root / "assets" / "app.css").read_text(encoding="utf-8") == "body{color:#123456}\n"


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
    monkeypatch.setenv("TAKYON_STORAGE_BACKEND", "local")
    monkeypatch.setenv("TAKYON_STORAGE_LOCAL_DIR", str(storage_root))

    result = takyon_core._publish_product_surface_path(
        business_root=business_root,
        slug="plannerly",
        source_path="product/site",
        publish_target="https://plannerly.fourmanifold.com/",
    )

    live_root = publish_root / "plannerly"
    assert result["status"] == "published"
    assert result["publish_source_path"] == "product/site/dist"
    assert (live_root / "current" / "index.html").read_text(encoding="utf-8") == "<html><body>dist site</body></html>\n"
    assert (live_root / "current" / "assets" / "app.css").read_text(encoding="utf-8") == "body{color:#abcdef}\n"


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


def test_publish_product_surface_blocks_next_build_shape(tmp_path, monkeypatch):
    business_root = tmp_path / "businesses" / "plannerly"
    site = business_root / "product" / "site"
    site.mkdir(parents=True)
    (site / ".next").mkdir()
    (site / ".next" / "BUILD_ID").write_text("build-1\n", encoding="utf-8")
    (site / ".next" / "build-manifest.json").write_text("{}\n", encoding="utf-8")
    (site / "package.json").write_text(
        """
        {
          "name": "plannerly",
          "private": true,
          "scripts": {
            "build": "next build",
            "start": "next start"
          },
          "dependencies": {
            "next": "15.4.0"
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


def test_publish_product_surface_writes_build_artifact_and_returns_live_pointer(tmp_path, monkeypatch):
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
    monkeypatch.setenv("TAKYON_STORAGE_BACKEND", "local")
    monkeypatch.setenv("TAKYON_STORAGE_LOCAL_DIR", str(storage_root))

    result = takyon_core._publish_product_surface_path(
        business_root=business_root,
        slug="plannerly",
        source_path="product/site",
        publish_target="https://plannerly.fourmanifold.com/",
    )

    stored_dist_root = storage_root / "plannerly" / "__takyon" / "builds" / result["live_build_id"]
    assert result["status"] == "published"
    assert result["publish_root"] == str(publish_root / "plannerly" / "current")
    assert result["publish_mode"] == "pointer_static"
    assert result["public_url"] == "https://plannerly.fourmanifold.com/"
    assert (stored_dist_root / "index.html").read_text(encoding="utf-8") == "<html><body>dist site</body></html>\n"
    assert (stored_dist_root / "assets" / "app.css").read_text(encoding="utf-8") == "body{color:#fedcba}\n"


def test_publish_product_surface_records_probe_as_unknown_until_reconciled(tmp_path, monkeypatch):
    business_root = tmp_path / "businesses" / "plannerly"
    site = business_root / "product" / "site"
    dist = site / "dist"
    dist.mkdir(parents=True)
    (dist / "index.html").write_text("<html><body>dist site</body></html>\n", encoding="utf-8")
    (dist / "assets").mkdir()
    (dist / "assets" / "app.css").write_text("body{color:#456789}\n", encoding="utf-8")

    publish_root = tmp_path / "product-sites"
    storage_root = tmp_path / "storage"
    monkeypatch.setenv("TAKYON_HOME", str(tmp_path))
    monkeypatch.setenv("TAKYON_PRODUCT_SITE_ROOT", str(publish_root))
    monkeypatch.setenv("TAKYON_STORAGE_BACKEND", "local")
    monkeypatch.setenv("TAKYON_STORAGE_LOCAL_DIR", str(storage_root))
    monkeypatch.delenv("TAKYON_PRODUCT_LOCAL_BASE_URL", raising=False)
    result = takyon_core._publish_product_surface_path(
        business_root=business_root,
        slug="plannerly",
        source_path="product/site",
        publish_target="https://plannerly.fourmanifold.com/",
    )

    assert result["status"] == "published"
    assert result["live_probe_status"] == "unknown"
    assert result["live_probe_detail"] == ""


def test_publish_product_surface_reuses_the_same_build_id_for_identical_output(tmp_path, monkeypatch):
    business_root = tmp_path / "businesses" / "plannerly"
    site = business_root / "product" / "site"
    dist = site / "dist"
    dist.mkdir(parents=True)
    (dist / "index.html").write_text("<html><body>dist site</body></html>\n", encoding="utf-8")

    publish_root = tmp_path / "product-sites"
    monkeypatch.setenv("TAKYON_HOME", str(tmp_path))
    monkeypatch.setenv("TAKYON_PRODUCT_SITE_ROOT", str(publish_root))
    monkeypatch.setenv("TAKYON_STORAGE_BACKEND", "local")
    monkeypatch.setenv("TAKYON_STORAGE_LOCAL_DIR", str(tmp_path / "storage"))
    first = takyon_core._publish_product_surface_path(
        business_root=business_root,
        slug="plannerly",
        source_path="product/site",
        publish_target="https://plannerly.fourmanifold.com/",
    )

    second = takyon_core._publish_product_surface_path(
        business_root=business_root,
        slug="plannerly",
        source_path="product/site",
        publish_target="https://plannerly.fourmanifold.com/",
    )

    assert first["status"] == "published"
    assert second["status"] == "published"
    assert first["live_build_id"] == second["live_build_id"]


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


def test_refresh_product_surface_rematerializes_surface_context_before_build(tmp_path, monkeypatch):
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
            "react": "18.3.1",
            "vite": "5.4.21"
          }
        }
        """,
        encoding="utf-8",
    )
    (site / "actions").mkdir(parents=True)
    (site / "actions" / "coach-chat.ts").write_text(
        "export default async () => ({ ok: true });\n",
        encoding="utf-8",
    )
    kit_root = site / takyon_core.SUBUSER_KIT_DIRNAME
    kit_root.mkdir(parents=True)
    (kit_root / "surface-context.js").write_text(
        'export const surfaceContext = {"runtimeFeatures":["auth","account"]};\n'
        "export default surfaceContext;\n",
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
            surface_context = (cwd / takyon_core.SUBUSER_KIT_DIRNAME / "surface-context.js").read_text(
                encoding="utf-8"
            )
            assert '"actions"' in surface_context
            dist = cwd / "dist"
            dist.mkdir(parents=True, exist_ok=True)
            (dist / "index.html").write_text("<html><body>dist site</body></html>\n", encoding="utf-8")
            return {"command": command, "status": "passed", "stdout": "", "stderr": ""}
        raise AssertionError(f"unexpected surface command: {command}")

    monkeypatch.setattr(takyon_core, "_run_surface_command", fake_run_surface_command)

    result = takyon_core._refresh_product_surface_path(
        business_root,
        "product/site",
        surface={
            "runtime_features": ["auth", "account"],
            "routes": [{"path": "/"}, {"path": "/app"}],
        },
        install=True,
    )

    assert result["status"] == "passed"
    assert '"actions"' in (kit_root / "surface-context.js").read_text(encoding="utf-8")


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
