from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import sqlite3

import pytest

from plugins.takyon import core as takyon_core
from plugins.takyon import storage as takyon_storage


@pytest.fixture(autouse=True)
def _stage_immutable_builds(monkeypatch):
    """Keep publish units hermetic while dedicated suites own warm-prebake behavior."""
    # These tests exercise build/publish semantics, not the warm node_modules cache. Copying the
    # real scaffold dependency tree into every tmp_path makes xdist cleanup dominate the suite and
    # starve unrelated timeout/concurrency tests; test_takyon_warm_prebake.py covers that rail.
    monkeypatch.setattr(takyon_core, "_seed_warm_node_modules", lambda *_a, **_k: False)
    publish = takyon_core._publish_product_surface_path

    def wrapped(**kwargs):
        kwargs.setdefault("stage_build", lambda _build: None)
        kwargs.setdefault("activate_build", lambda _build: None)
        kwargs.setdefault("rollback_build", lambda _build: {"rolled_back": True})
        return publish(**kwargs)

    monkeypatch.setattr(takyon_core, "_publish_product_surface_path", wrapped)


def test_scaffold_materialization_prunes_dependency_tree_before_descent(tmp_path, monkeypatch):
    scaffold = tmp_path / "scaffold"
    (scaffold / "src").mkdir(parents=True)
    (scaffold / "src" / "main.tsx").write_text("export {};\n", encoding="utf-8")
    (scaffold / "node_modules" / "huge-package").mkdir(parents=True)
    (scaffold / "node_modules" / "huge-package" / "poison.js").write_text(
        "throw new Error('must never be visited');\n",
        encoding="utf-8",
    )
    target = tmp_path / "business" / "product" / "site"
    target.mkdir(parents=True)
    visited: list[Path] = []
    real_walk = takyon_core.os.walk

    def recording_walk(root):
        for current, dirnames, filenames in real_walk(root):
            visited.append(Path(current).relative_to(scaffold))
            yield current, dirnames, filenames

    monkeypatch.setattr(takyon_core, "_subuser_app_scaffold_source_dir", lambda: scaffold)
    monkeypatch.setattr(takyon_core.os, "walk", recording_walk)

    takyon_core._materialize_subuser_app_scaffold(target, slug="future-app", surface=None)

    assert (target / "src" / "main.tsx").is_file()
    assert not (target / "node_modules").exists()
    assert all("node_modules" not in path.parts for path in visited)


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
    served_html = (current_root / "index.html").read_text(encoding="utf-8")
    assert "<body>new site</body>" in served_html
    assert f'content="{result["live_build_id"]}"' in served_html
    assert (current_root / "assets" / "app.css").read_text(encoding="utf-8") == "body{color:#123456}\n"


def test_publish_product_surface_bakes_meta_pixel_into_served_build_when_enabled(tmp_path, monkeypatch):
    """Opt-in Meta pixel is baked into the build BEFORE the build_id hash, so the served
    (content-addressed) build carries it. This is the fix for meta_pixel_ensure's perennial
    installed_r2=false: overwriting the live dist / one served build_id never reaches the edge
    and is dropped by the next rebuild; baking it on every enabled publish does."""
    business_root = tmp_path / "businesses" / "climbly"
    dist = business_root / "product" / "site" / "dist"
    dist.mkdir(parents=True)
    (dist / "index.html").write_text(
        "<html><head><title>x</title></head><body>app</body></html>\n", encoding="utf-8"
    )
    publish_root = tmp_path / "product-sites"
    monkeypatch.setenv("TAKYON_HOME", str(tmp_path))
    monkeypatch.setenv("TAKYON_PRODUCT_SITE_ROOT", str(publish_root))
    monkeypatch.setenv("TAKYON_STORAGE_BACKEND", "local")
    monkeypatch.setenv("TAKYON_STORAGE_LOCAL_DIR", str(tmp_path / "storage"))
    pixel_id = "1340991031280635"
    monkeypatch.setattr(
        takyon_core, "_meta_pixel_config", lambda: {"pixel_id": pixel_id, "script_src": ""}
    )

    enabled = takyon_core._publish_product_surface_path(
        business_root=business_root,
        slug="climbly",
        source_path="product/site",
        publish_target="https://climbly.fourmanifold.com/",
        surface={"metadata": {"meta_pixel": {"enabled": True, "pixel_id": pixel_id}}},
    )
    assert enabled["status"] == "published"
    served = (publish_root / "climbly" / "current" / "index.html").read_text(encoding="utf-8")
    assert f'data-takyon-meta-pixel="{pixel_id}"' in served  # baked into the SERVED build

    # Opt-in only: a publish without the enabled flag must NOT carry the pixel.
    (dist / "index.html").write_text(
        "<html><head><title>x</title></head><body>v2</body></html>\n", encoding="utf-8"
    )
    disabled = takyon_core._publish_product_surface_path(
        business_root=business_root,
        slug="climbly",
        source_path="product/site",
        publish_target="https://climbly.fourmanifold.com/",
        surface={"metadata": {"meta_pixel": {"enabled": False}}},
    )
    assert disabled["status"] == "published"
    served_off = (publish_root / "climbly" / "current" / "index.html").read_text(encoding="utf-8")
    assert "data-takyon-meta-pixel" not in served_off


def test_publish_product_surface_bakes_umami_analytics_into_served_build(tmp_path, monkeypatch):
    """Shared Umami analytics is baked into every published build, so the R2 edge (which serves
    bytes raw) carries it. Self-gates on analytics.umami.enabled — fixes the regression where the
    R2 cutover dropped the legacy serve-time-injected tag from every product page."""
    business_root = tmp_path / "businesses" / "trackly"
    dist = business_root / "product" / "site" / "dist"
    dist.mkdir(parents=True)
    (dist / "index.html").write_text(
        "<html><head><title>x</title></head><body>a</body></html>\n", encoding="utf-8"
    )
    publish_root = tmp_path / "product-sites"
    monkeypatch.setenv("TAKYON_HOME", str(tmp_path))
    monkeypatch.setenv("TAKYON_PRODUCT_SITE_ROOT", str(publish_root))
    monkeypatch.setenv("TAKYON_STORAGE_BACKEND", "local")
    monkeypatch.setenv("TAKYON_STORAGE_LOCAL_DIR", str(tmp_path / "storage"))
    monkeypatch.setattr(
        takyon_core,
        "_analytics_umami_config",
        lambda: {"enabled": True, "website_id": "53c7278e-x", "script_src": "https://cloud.umami.is/script.js"},
    )

    r = takyon_core._publish_product_surface_path(
        business_root=business_root,
        slug="trackly",
        source_path="product/site",
        publish_target="https://trackly.fourmanifold.com/",
    )
    assert r["status"] == "published"
    served = (publish_root / "trackly" / "current" / "index.html").read_text(encoding="utf-8")
    assert 'data-website-id="53c7278e-x"' in served
    assert "cloud.umami.is/script.js" in served

    # Disabled analytics → no tag baked.
    (dist / "index.html").write_text(
        "<html><head><title>x</title></head><body>b</body></html>\n", encoding="utf-8"
    )
    monkeypatch.setattr(takyon_core, "_analytics_umami_config", lambda: {"enabled": False})
    takyon_core._publish_product_surface_path(
        business_root=business_root,
        slug="trackly",
        source_path="product/site",
        publish_target="https://trackly.fourmanifold.com/",
    )
    served_off = (publish_root / "trackly" / "current" / "index.html").read_text(encoding="utf-8")
    assert "data-website-id" not in served_off


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
    assert "<body>dist site</body>" in (live_root / "current" / "index.html").read_text(encoding="utf-8")
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
    stored_html = (stored_dist_root / "index.html").read_text(encoding="utf-8")
    assert "<body>dist site</body>" in stored_html
    assert f'content="{result["live_build_id"]}"' in stored_html
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


def test_publish_product_surface_does_not_block_on_best_effort_source_cache_sync_failure(tmp_path, monkeypatch):
    business_root = tmp_path / "businesses" / "plannerly"
    dist = business_root / "product" / "site" / "dist"
    dist.mkdir(parents=True)
    (dist / "index.html").write_text("<html><body>dist site</body></html>\n", encoding="utf-8")

    publish_root = tmp_path / "product-sites"
    monkeypatch.setenv("TAKYON_HOME", str(tmp_path))
    monkeypatch.setenv("TAKYON_PRODUCT_SITE_ROOT", str(publish_root))
    monkeypatch.setenv("TAKYON_STORAGE_BACKEND", "local")
    monkeypatch.setenv("TAKYON_STORAGE_LOCAL_DIR", str(tmp_path / "storage"))
    monkeypatch.delenv("TAKYON_REQUIRE_PRODUCT_SOURCE_CACHE_SYNC", raising=False)
    monkeypatch.setattr(
        takyon_core,
        "_sync_product_source_caches",
        lambda _slug, _source_root: {
            "local": {"synced": True, "status": "synced"},
            "subuser": {"synced": False, "status": "blocked", "error": "ssh key not found"},
        },
    )

    result = takyon_core._publish_product_surface_path(
        business_root=business_root,
        slug="plannerly",
        source_path="product/site",
        publish_target="https://plannerly.fourmanifold.com/",
    )

    assert result["status"] == "published"
    assert result["blocker"] == ""
    assert result["source_cache_sync_warning"] == "product source cache sync failed: subuser: ssh key not found"


def test_publish_product_surface_can_explicitly_require_source_cache_sync(tmp_path, monkeypatch):
    business_root = tmp_path / "businesses" / "plannerly"
    dist = business_root / "product" / "site" / "dist"
    dist.mkdir(parents=True)
    (dist / "index.html").write_text("<html><body>dist site</body></html>\n", encoding="utf-8")

    publish_root = tmp_path / "product-sites"
    monkeypatch.setenv("TAKYON_HOME", str(tmp_path))
    monkeypatch.setenv("TAKYON_PRODUCT_SITE_ROOT", str(publish_root))
    monkeypatch.setenv("TAKYON_STORAGE_BACKEND", "local")
    monkeypatch.setenv("TAKYON_STORAGE_LOCAL_DIR", str(tmp_path / "storage"))
    monkeypatch.setenv("TAKYON_REQUIRE_PRODUCT_SOURCE_CACHE_SYNC", "1")
    monkeypatch.setattr(
        takyon_core,
        "_sync_product_source_caches",
        lambda _slug, _source_root: {
            "local": {"synced": True, "status": "synced"},
            "subuser": {"synced": False, "status": "blocked", "error": "ssh key not found"},
        },
    )

    result = takyon_core._publish_product_surface_path(
        business_root=business_root,
        slug="plannerly",
        source_path="product/site",
        publish_target="https://plannerly.fourmanifold.com/",
    )

    assert result["status"] == "blocked"
    assert result["blocker"] == "product source cache sync failed: subuser: ssh key not found"


def test_publish_product_surface_mirrors_to_r2_via_remote_storage_authority(tmp_path, monkeypatch):
    business_root = tmp_path / "businesses" / "plannerly"
    dist = business_root / "product" / "site" / "dist"
    dist.mkdir(parents=True)
    (dist / "index.html").write_text("<html><body>dist site</body></html>\n", encoding="utf-8")

    publish_root = tmp_path / "product-sites"
    monkeypatch.setenv("TAKYON_HOME", str(tmp_path))
    monkeypatch.setenv("TAKYON_PRODUCT_SITE_ROOT", str(publish_root))
    monkeypatch.setenv("TAKYON_STORAGE_BACKEND", "local")
    monkeypatch.setenv("TAKYON_STORAGE_LOCAL_DIR", str(tmp_path / "storage"))
    monkeypatch.setattr(takyon_storage, "r2_configured", lambda: True)
    monkeypatch.setattr(takyon_storage, "_remote_storage_authority_enabled", lambda: True)
    mirrored: dict[str, object] = {}

    def fake_write_public_site_to_r2(
        slug,
        build_id,
        build_root,
        *,
        before_pointer=None,
        before_pointer_state=None,
        after_pointer=None,
        pointer_guard=None,
        on_pointer_failure=None,
    ):
        mirrored.update({"slug": slug, "build_id": build_id, "build_root": Path(build_root)})
        with (pointer_guard() if pointer_guard is not None else takyon_core.nullcontext()):
            receipt = {}
            if before_pointer_state is not None:
                receipt = before_pointer_state(
                    {"observed_current_build_id": "", "prior_r2_previous_pointer": ""}
                )
            elif before_pointer is not None:
                receipt = before_pointer()
            if after_pointer is not None:
                after_pointer(receipt or {})
        return {"slug": slug, "build_id": build_id, "files": {"index.html": "digest"}}

    monkeypatch.setattr(takyon_storage, "write_public_site_to_r2", fake_write_public_site_to_r2)
    monkeypatch.setattr(takyon_core, "_ensure_product_edge_route", lambda slug: None)

    result = takyon_core._publish_product_surface_path(
        business_root=business_root,
        slug="plannerly",
        source_path="product/site",
        publish_target="https://plannerly.fourmanifold.com/",
    )

    assert result["status"] == "published"
    assert mirrored["slug"] == "plannerly"
    assert mirrored["build_id"] == result["live_build_id"]
    assert mirrored["build_root"] == (
        publish_root / "plannerly" / "builds" / result["live_build_id"]
    ).resolve()


def test_publish_stages_exact_action_bundle_before_public_pointer_flip(tmp_path, monkeypatch):
    business_root = tmp_path / "businesses" / "proposal"
    site = business_root / "product" / "site"
    dist = site / "dist"
    actions = site / "actions"
    dist.mkdir(parents=True)
    actions.mkdir(parents=True)
    (dist / "index.html").write_text(
        "<html><head></head><body>proposal</body></html>\n",
        encoding="utf-8",
    )
    (actions / "generate.ts").write_text(
        "export default async (_payload: TakyonActionPayload, _ctx: TakyonActionContext) "
        "=> ({ ok: true });\n",
        encoding="utf-8",
    )
    (site / "src").mkdir()
    (site / "src" / "app.ts").write_text(
        'client.invokeAction("generate", {});\n',
        encoding="utf-8",
    )
    publish_root = tmp_path / "product-sites"
    monkeypatch.setenv("TAKYON_HOME", str(tmp_path))
    monkeypatch.setenv("TAKYON_PRODUCT_SITE_ROOT", str(publish_root))
    monkeypatch.setenv("TAKYON_STORAGE_BACKEND", "local")
    monkeypatch.setenv("TAKYON_STORAGE_LOCAL_DIR", str(tmp_path / "storage"))
    monkeypatch.setattr(takyon_storage, "r2_configured", lambda: True)
    monkeypatch.setattr(
        takyon_core,
        "_sync_product_source_caches",
        lambda *_args: {"local": {"synced": True, "status": "synced"}},
    )
    monkeypatch.setattr(takyon_core, "_ensure_product_edge_route", lambda _slug: None)
    order: list[str] = []
    staged: dict[str, object] = {}

    def stage_build(build):
        order.append("database-stage")
        staged.update(build)
        built_html = (
            publish_root / "proposal" / "builds" / build["build_id"] / "index.html"
        ).read_text(encoding="utf-8")
        assert build["build_id"] in built_html

    def mirror(
        _slug,
        build_id,
        build_root,
        *,
        before_pointer=None,
        before_pointer_state=None,
        after_pointer=None,
        pointer_guard=None,
        on_pointer_failure=None,
    ):
        order.append("r2-upload")
        assert staged["build_id"] == build_id
        assert Path(build_root).resolve() == (
            publish_root / "proposal" / "builds" / build_id
        ).resolve()
        assert not (publish_root / "proposal" / "current").exists()
        assert before_pointer is not None
        with (pointer_guard() if pointer_guard is not None else takyon_core.nullcontext()):
            receipt = (
                before_pointer_state(
                    {"observed_current_build_id": "", "prior_r2_previous_pointer": ""}
                )
                if before_pointer_state is not None
                else before_pointer()
            )
            order.append("r2-pointer")
            if after_pointer is not None:
                after_pointer(receipt or {})
        return {"files": {"index.html": "digest"}, "build_id": build_id}

    def activate_build(build):
        order.append("database-activate")
        assert build["build_id"] == staged["build_id"]
        assert not (publish_root / "proposal" / "current").exists()

    @contextmanager
    def pointer_guard():
        order.append("guard-enter")
        try:
            yield
        finally:
            order.append("guard-exit")

    monkeypatch.setattr(takyon_storage, "write_public_site_to_r2", mirror)

    result = takyon_core._publish_product_surface_path(
        business_root=business_root,
        slug="proposal",
        source_path="product/site",
        publish_target="https://proposal.coscale.app/",
        stage_build=stage_build,
        activate_build=activate_build,
        pointer_guard=pointer_guard,
    )

    assert result["status"] == "published"
    assert result["database_build_staged"] is True
    assert order == [
        "database-stage",
        "r2-upload",
        "guard-enter",
        "database-activate",
        "r2-pointer",
        "guard-exit",
    ]
    assert staged["action_bundle_sha256"] == result["action_bundle_sha256"]


def test_publish_rolls_back_r2_and_database_when_public_pointer_activation_fails(
    tmp_path,
    monkeypatch,
):
    business_root = tmp_path / "businesses" / "proposal"
    dist = business_root / "product" / "site" / "dist"
    dist.mkdir(parents=True)
    (dist / "index.html").write_text("<html><body>new</body></html>\n", encoding="utf-8")
    publish_root = tmp_path / "product-sites"
    previous_id = "a" * 32
    previous_root = publish_root / "proposal" / "builds" / previous_id
    previous_root.mkdir(parents=True)
    (previous_root / "index.html").write_text("<html><body>old</body></html>\n", encoding="utf-8")
    current = publish_root / "proposal" / "current"
    current.parent.mkdir(parents=True, exist_ok=True)
    current.symlink_to(previous_root)
    monkeypatch.setenv("TAKYON_HOME", str(tmp_path))
    monkeypatch.setenv("TAKYON_PRODUCT_SITE_ROOT", str(publish_root))
    monkeypatch.setenv("TAKYON_STORAGE_BACKEND", "local")
    monkeypatch.setenv("TAKYON_STORAGE_LOCAL_DIR", str(tmp_path / "storage"))
    monkeypatch.setattr(takyon_storage, "r2_configured", lambda: True)
    def mirror_then_fail(
        *_args,
        before_pointer=None,
        pointer_guard=None,
        on_pointer_failure=None,
        **_kwargs,
    ):
        assert before_pointer is not None
        with (pointer_guard() if pointer_guard is not None else takyon_core.nullcontext()):
            receipt = before_pointer()
            exc = RuntimeError("pointer unavailable")
            if on_pointer_failure is not None:
                on_pointer_failure(exc, receipt or {})
            raise exc

    monkeypatch.setattr(takyon_storage, "write_public_site_to_r2", mirror_then_fail)
    rollback_calls: list[dict[str, str]] = []

    def rollback(_slug, **kwargs):
        rollback_calls.append(kwargs)
        return {"attempted": True, "restored": True, "status": "restored_previous"}

    monkeypatch.setattr(takyon_storage, "restore_public_site_pointer_from_r2", rollback)
    monkeypatch.setattr(takyon_core, "_ensure_product_edge_route", lambda _slug: None)

    database_rollbacks: list[dict[str, str]] = []

    def rollback_build(build):
        database_rollbacks.append(build)
        return {"rolled_back": True}

    result = takyon_core._publish_product_surface_path(
        business_root=business_root,
        slug="proposal",
        source_path="product/site",
        publish_target="https://proposal.coscale.app/",
        surface={"live_build_id": previous_id},
        stage_build=lambda _build: None,
        activate_build=lambda _build: None,
        rollback_build=rollback_build,
    )

    assert result["status"] == "blocked"
    assert "r2_pointer_activation_failed" in result["blocker"]
    assert current.resolve() == previous_root.resolve()
    assert len(rollback_calls) == 1
    assert rollback_calls[0]["previous_build_id"] == previous_id
    assert rollback_calls[0]["failed_build_id"] != previous_id
    assert len(database_rollbacks) == 1
    assert database_rollbacks[0]["previous_build_id"] == previous_id
    assert result["database_build_activated"] is False


def test_database_activation_rollback_is_compare_and_swap_and_restores_previous_build(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv("TAKYON_HOME", str(tmp_path))
    store = takyon_core.TakyonStore(tmp_path)
    monkeypatch.setattr(store, "_rewrite_app_files", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(takyon_core, "_compile_stripe_checkout_branding", lambda **_kwargs: {})
    business_root = tmp_path / "businesses" / "proposal"
    (business_root / "product" / "site").mkdir(parents=True)
    monkeypatch.setattr(store, "_business_root", lambda *_args, **_kwargs: business_root)
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    store._init_db(conn)
    previous_id = "a" * 32
    failed_id = "b" * 32
    now = takyon_core._now()
    conn.execute(
        "INSERT INTO businesses (slug, name, goal, created_at, updated_at) "
        "VALUES ('proposal', 'Proposal', '', ?, ?)",
        (now, now),
    )
    conn.execute(
            "INSERT INTO app_surface_contracts (business_slug, status, source_path, "
            "publish_target, publish_policy, mode_behavior, done_gate, public_url, "
            "publish_status, published_at, live_build_id, live_probe_status, metadata_json, "
            "created_at, updated_at) VALUES (?, 'active', 'product/site', ?, "
            "'publish_after_refresh', 'live_only_hard_fail_missing_gates', "
            "'business_refresh_product_surface:published_or_exact_blocker', ?, 'published', ?, ?, "
            "'unknown', '{}', ?, ?)",
            (
                "proposal",
                "https://proposal.coscale.app/",
                "https://proposal.coscale.app/",
                now,
                failed_id,
                now,
                now,
            ),
        )
    for build_id, status in ((previous_id, "previous"), (failed_id, "live")):
        conn.execute(
            "INSERT INTO product_builds (build_id, business_slug, source_revision, "
            "artifact_prefix, action_bundle_json, action_bundle_sha256, status, "
            "activated_at, servable_until, created_at) VALUES (?, 'proposal', 1, ?, '{}', '', ?, ?, ?, ?)",
            (
                build_id,
                f"products/proposal/{build_id}",
                status,
                now,
                now if status == "previous" else None,
                now,
            ),
        )

    result = store._apply_operation(
        conn,
        {"raw": "business:proposal/app", "business": "proposal", "kind": "resource", "resource": "app"},
        {
            "action": "app.surface.rollback_build_activation",
            "business_slug": "proposal",
            "target_scope": "business:proposal/app",
            "failed_build_id": failed_id,
            "previous_build_id": previous_id,
            "previous_publish_status": "published",
            "previous_publish_target": "https://proposal.coscale.app/",
            "previous_public_url": "https://proposal.coscale.app/",
            "previous_published_at": now,
            "previous_live_probe_status": "unknown",
        },
        reason="test",
        actor="test",
    )

    assert result["rolled_back"] is True
    surface = store._stored_app_surface_contract(conn, "proposal")
    statuses = {
        row["build_id"]: row["status"]
        for row in conn.execute(
            "SELECT build_id, status FROM product_builds WHERE business_slug = ?",
            ("proposal",),
        ).fetchall()
    }
    assert surface["live_build_id"] == previous_id
    assert statuses == {previous_id: "live", failed_id: "built"}


def test_database_activation_cas_returns_displaced_build_and_requires_finalize(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv("TAKYON_HOME", str(tmp_path))
    store = takyon_core.TakyonStore(tmp_path)
    monkeypatch.setattr(store, "_rewrite_app_files", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(takyon_core, "_compile_stripe_checkout_branding", lambda **_kwargs: {})
    business_root = tmp_path / "businesses" / "proposal"
    (business_root / "product" / "site").mkdir(parents=True)
    monkeypatch.setattr(store, "_business_root", lambda *_args, **_kwargs: business_root)
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    store._init_db(conn)
    old_id = "a" * 32
    new_id = "b" * 32
    attempt_id = "c" * 32
    now = takyon_core._now()
    bundle = json.dumps(
        {"files": [], "http_action_names": [], "version": 1},
        sort_keys=True,
        separators=(",", ":"),
    )
    digest = hashlib.sha256(bundle.encode()).hexdigest()
    conn.execute(
        "INSERT INTO businesses (slug, name, goal, created_at, updated_at) "
        "VALUES ('proposal', 'Proposal', '', ?, ?)",
        (now, now),
    )
    conn.execute(
        "INSERT INTO app_surface_contracts (business_slug, status, source_path, "
        "publish_target, publish_policy, mode_behavior, done_gate, public_url, "
        "publish_status, published_at, live_build_id, live_probe_status, metadata_json, "
        "created_at, updated_at) VALUES ('proposal', 'active', 'product/site', ?, "
        "'publish_after_refresh', 'live_only_hard_fail_missing_gates', "
        "'business_refresh_product_surface:published_or_exact_blocker', ?, 'published', ?, ?, "
        "'unknown', '{}', ?, ?)",
        ("https://proposal.coscale.app/", "https://proposal.coscale.app/", now, old_id, now, now),
    )
    for build_id, status, state in (
        (old_id, "live", "live"),
        (new_id, "staged", "staged"),
    ):
        conn.execute(
            "INSERT INTO product_builds (build_id, business_slug, source_revision, "
            "artifact_prefix, action_bundle_json, action_bundle_sha256, status, "
            "activation_state, created_at) VALUES (?, 'proposal', 1, ?, ?, ?, ?, ?, ?)",
            (build_id, f"products/proposal/{build_id}", bundle, digest, status, state, now),
        )
    operation = {
        "action": "app.surface.publish_result",
        "business_slug": "proposal",
        "target_scope": "business:proposal/app",
        "publish_status": "published",
        "publish_target": "https://proposal.coscale.app/",
        "public_url": "https://proposal.coscale.app/",
        "published_at": now,
        "live_build_id": new_id,
        "artifact_prefix": f"products/proposal/{new_id}",
        "action_bundle_json": bundle,
        "action_bundle_sha256": digest,
        "expected_previous_build_id": old_id,
        "activation_attempt_id": attempt_id,
    }

    stale = dict(operation, expected_previous_build_id="d" * 32)
    with pytest.raises(takyon_core.TakyonError, match="stale product build activation"):
        store._apply_operation(
            conn,
            {"raw": "business:proposal/app", "business": "proposal", "kind": "resource", "resource": "app"},
            stale,
            reason="test",
            actor="test",
        )
    assert conn.execute(
        "SELECT live_build_id FROM app_surface_contracts WHERE business_slug='proposal'"
    ).fetchone()[0] == old_id

    activated = store._apply_operation(
        conn,
        {"raw": "business:proposal/app", "business": "proposal", "kind": "resource", "resource": "app"},
        operation,
        reason="test",
        actor="test",
    )
    assert activated["previous_build_id"] == old_id
    assert activated["previous_servable_until"]
    assert activated["activation_state"] == "pointer_pending"
    states = {
        row["build_id"]: row["activation_state"]
        for row in conn.execute(
            "SELECT build_id, activation_state FROM product_builds WHERE business_slug='proposal'"
        ).fetchall()
    }
    assert states == {old_id: "previous", new_id: "pointer_pending"}

    finalized = store._apply_operation(
        conn,
        {"raw": "business:proposal/app", "business": "proposal", "kind": "resource", "resource": "app"},
        {
            "action": "app.surface.finalize_build_activation",
            "business_slug": "proposal",
            "target_scope": "business:proposal/app",
            "build_id": new_id,
            "activation_attempt_id": attempt_id,
        },
        reason="test",
        actor="test",
    )
    assert finalized["activation_state"] == "live"


@pytest.mark.parametrize(
    ("edge_choice", "expected_status", "expected_live"),
    [
        ("pending", "finalized", "b" * 32),
        ("previous", "rolled_back", "a" * 32),
    ],
)
def test_pending_activation_reconciles_from_verified_edge_pointer(
    tmp_path,
    monkeypatch,
    edge_choice,
    expected_status,
    expected_live,
):
    from contextlib import contextmanager, nullcontext

    monkeypatch.setenv("TAKYON_HOME", str(tmp_path))
    store = takyon_core.TakyonStore(tmp_path)
    monkeypatch.setattr(store, "_rewrite_app_files", lambda *_args, **_kwargs: None)
    business_root = tmp_path / "businesses" / "proposal"
    business_root.mkdir(parents=True)
    monkeypatch.setattr(store, "_business_root", lambda *_args, **_kwargs: business_root)
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    store._init_db(conn)
    previous_id = "a" * 32
    pending_id = "b" * 32
    attempt_id = "c" * 32
    now = takyon_core._now()
    conn.execute(
        "INSERT INTO businesses (slug, name, goal, created_at, updated_at) "
        "VALUES ('proposal', 'Proposal', '', ?, ?)",
        (now, now),
    )
    conn.execute(
        "INSERT INTO app_surface_contracts (business_slug, status, source_path, "
        "publish_target, publish_policy, mode_behavior, done_gate, public_url, "
        "publish_status, published_at, live_build_id, live_probe_status, metadata_json, "
        "created_at, updated_at) VALUES ('proposal', 'active', 'product/site', ?, "
        "'publish_after_refresh', 'live_only_hard_fail_missing_gates', "
        "'business_refresh_product_surface:published_or_exact_blocker', ?, 'published', ?, ?, "
        "'unknown', '{}', ?, ?)",
        ("https://proposal.coscale.app/", "https://proposal.coscale.app/", now, pending_id, now, now),
    )
    conn.execute(
        "INSERT INTO product_builds (build_id, business_slug, source_revision, artifact_prefix, "
        "status, activation_state, created_at) VALUES (?, 'proposal', 1, ?, 'previous', "
        "'previous', ?)",
        (previous_id, f"products/proposal/{previous_id}", now),
    )
    conn.execute(
        "INSERT INTO product_builds (build_id, business_slug, source_revision, artifact_prefix, "
        "status, activation_state, activation_attempt_id, activation_previous_build_id, "
        "created_at) VALUES (?, 'proposal', 2, ?, 'live', 'pointer_pending', ?, ?, ?)",
        (pending_id, f"products/proposal/{pending_id}", attempt_id, previous_id, now),
    )

    @contextmanager
    def connect():
        yield conn

    monkeypatch.setattr(store, "_connect", connect)
    monkeypatch.setattr(
        takyon_core,
        "_hold_active_worker_claim_for_publish",
        lambda *_args, **_kwargs: nullcontext(),
    )
    monkeypatch.setattr(takyon_storage, "r2_configured", lambda: True)
    monkeypatch.setattr(
        takyon_storage,
        "read_public_site_pointer_from_r2",
        lambda _slug: pending_id if edge_choice == "pending" else previous_id,
    )
    monkeypatch.setattr(
        takyon_storage,
        "restore_public_site_pointer_from_r2",
        lambda *_args, **_kwargs: {
            "restored": True,
            "status": "current_already_previous",
            "current_build_id": previous_id,
        },
    )

    result = takyon_core._reconcile_pending_product_build_activation(store, "proposal")

    assert result["status"] == expected_status
    assert result["surface"]["live_build_id"] == expected_live
    state = conn.execute(
        "SELECT activation_state FROM product_builds WHERE build_id = ?",
        (pending_id,),
    ).fetchone()[0]
    assert state == ("live" if edge_choice == "pending" else "rolled_back")


def test_publish_fence_serializes_manual_publishers_with_business_advisory_lock(monkeypatch):
    events: list[object] = []

    class Connection:
        def __enter__(self):
            events.append("transaction-enter")
            return self

        def __exit__(self, *_exc):
            events.append("transaction-exit")

        def execute(self, sql, params=()):
            events.append((str(sql), tuple(params)))
            return self

    class Store:
        def _connect(self):
            return Connection()

    monkeypatch.setattr(takyon_core, "_active_worker_claim_guard", lambda: None)
    with takyon_core._hold_active_worker_claim_for_publish(
        Store(),
        "test activation",
        business="proposal",
    ):
        events.append("body")

    advisory = next(item for item in events if isinstance(item, tuple))
    assert "pg_advisory_xact_lock" in advisory[0]
    assert advisory[1] == ("takyon-product-publish:proposal",)
    assert events.index(advisory) < events.index("body") < events.index("transaction-exit")


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
        if command == [
            "/usr/bin/npm",
            "ci",
            "--prefer-offline",
            "--no-audit",
            "--no-fund",
            "--ignore-scripts",
        ]:
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
    commands = [check["command"] for check in result["checks"]]
    assert ["/usr/bin/npm", "run", "build"] in commands
    assert ["/usr/bin/npm", "run", "typecheck"] in commands
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
        "export default async (_payload: TakyonActionPayload, _ctx: TakyonActionContext) "
        "=> ({ ok: true });\n",
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
        if command == [
            "/usr/bin/npm",
            "ci",
            "--prefer-offline",
            "--no-audit",
            "--no-fund",
            "--ignore-scripts",
        ]:
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
        if command == ["/usr/bin/npm", "run", "typecheck"]:
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
            [
                "/usr/bin/npm",
                "ci",
                "--prefer-offline",
                "--no-audit",
                "--no-fund",
                "--ignore-scripts",
            ],
            ["/usr/bin/npm", "run", "build"],
            ["/usr/bin/npm", "run", "typecheck"],
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
