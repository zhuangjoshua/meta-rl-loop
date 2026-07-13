from __future__ import annotations

import json
from pathlib import Path
import types

from plugins.takyon import core as takyon_core
from plugins.takyon.core import TakyonStore


def test_delete_business_is_unavailable_on_subuser_host(monkeypatch):
    cleanup_calls: list[str] = []
    monkeypatch.setenv("TAKYON_HOST_ROLE", "subuser")
    monkeypatch.setattr(
        takyon_core,
        "_delete_subuser_product_site",
        lambda slug: cleanup_calls.append(slug),
    )

    result = json.loads(
        takyon_core.handle_business_delete_business(
            {
                "business": "notewave",
                "confirm": True,
                "idempotency_key": "subuser-delete-must-fail",
            }
        )
    )

    assert result["success"] is False
    assert "subuser host cannot open the default operator store" in result["error"]
    assert cleanup_calls == []


def test_delete_business_removes_product_runtime_surfaces(tmp_path, monkeypatch):
    monkeypatch.setenv("TAKYON_PRODUCT_DEPLOY_DRY_RUN", "1")
    monkeypatch.setenv("TAKYON_PRODUCT_SERVICE_ROOT", str(tmp_path / "product-services"))
    monkeypatch.setenv("TAKYON_PRODUCT_SYSTEMD_DIR", str(tmp_path / "systemd"))
    monkeypatch.setenv("TAKYON_PRODUCT_CADDYFILE", str(tmp_path / "Caddyfile"))

    store = TakyonStore(tmp_path)
    slug = "latexflow"
    business_root = tmp_path / "businesses" / slug
    (business_root / "product").mkdir(parents=True)
    (business_root / "product" / "spec.md").write_text("# Spec\n", encoding="utf-8")

    service_root = tmp_path / "product-services" / slug
    service_root.mkdir(parents=True)
    (service_root / "server.js").write_text("console.log('live');\n", encoding="utf-8")

    systemd_dir = tmp_path / "systemd"
    systemd_dir.mkdir(parents=True)
    service_file = systemd_dir / "takyon-product-latexflow.service"
    service_file.write_text("[Unit]\nDescription=Takyon Product - latexflow\n", encoding="utf-8")

    caddyfile = tmp_path / "Caddyfile"
    caddyfile.write_text(
        "latexflow.fourmanifold.com {\n"
        "    handle {\n"
        "        reverse_proxy 127.0.0.1:4010\n"
        "    }\n"
        "}\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        takyon_core,
        "_delete_subuser_product_site",
        lambda business_slug: {
            "target": "root@134.209.123.8",
            "path": f"/opt/takyon/.takyon/product-sites/{business_slug}",
            "removed": True,
            "status": "removed",
        },
    )

    store._ensure_business = types.MethodType(lambda self, conn, business_slug: {"slug": business_slug}, store)
    store._delete_business_crons = types.MethodType(
        lambda self, business_slug, confirm=False: {"matched": [], "removed": []},
        store,
    )
    store._business_delete_db_counts = types.MethodType(lambda self, conn, business_slug: {"businesses": 1}, store)
    store._delete_business_db_rows = types.MethodType(
        lambda self, conn, business_slug, db_counts=None: {"businesses": 1},
        store,
    )
    store._record_event = types.MethodType(lambda self, conn, **kwargs: None, store)
    store._delete_business_workspace_remote = types.MethodType(lambda self, business_slug: None, store)
    store._delete_business_domains = types.MethodType(
        lambda self, domains, confirm=True: {"provider": "vercel", "candidates": domains, "results": []},
        store,
    )
    store._workspace_root_override = tmp_path
    store._business_root = types.MethodType(
        lambda self, business_slug, sync=True: tmp_path / "businesses" / business_slug,
        store,
    )

    result = store._delete_business(
        object(),
        {"business": slug, "confirm": True, "delete_domains": False},
        reason="test",
        actor="test",
    )

    assert result["product_service"]["service_root"]["removed"] is True
    assert result["product_service"]["service_file"]["removed"] is True
    assert result["product_service"]["caddy_route"]["removed"] is True
    assert result["subuser_product_site"]["removed"] is True
    assert not business_root.exists()
    assert not service_root.exists()
    assert not service_file.exists()
    assert "latexflow.fourmanifold.com" not in caddyfile.read_text(encoding="utf-8")


def test_delete_business_subuser_cleanup_failure_is_not_fatal(tmp_path, monkeypatch):
    """The sub-user product-site cleanup SSHes to a host using a key that is intentionally absent on
    the operator host (least-privilege), so it raises there. That best-effort residue cleanup must NOT
    abort the authoritative DB-row delete — otherwise the dashboard "X" delete would strand the
    control-plane row while every other asset is already gone."""
    monkeypatch.setenv("TAKYON_PRODUCT_DEPLOY_DRY_RUN", "1")
    monkeypatch.setenv("TAKYON_PRODUCT_SERVICE_ROOT", str(tmp_path / "product-services"))
    monkeypatch.setenv("TAKYON_PRODUCT_SYSTEMD_DIR", str(tmp_path / "systemd"))
    monkeypatch.setenv("TAKYON_PRODUCT_CADDYFILE", str(tmp_path / "Caddyfile"))

    store = TakyonStore(tmp_path)
    slug = "octopus"
    business_root = tmp_path / "businesses" / slug
    (business_root / "product").mkdir(parents=True)
    (business_root / "product" / "spec.md").write_text("# Spec\n", encoding="utf-8")

    def _raise_subuser(business_slug):
        raise takyon_core.TakyonError(f"sub-user VPS ssh key not found for {business_slug}")

    monkeypatch.setattr(takyon_core, "_delete_subuser_product_site", _raise_subuser)
    monkeypatch.setattr(
        takyon_core,
        "_delete_public_edge_product_site",
        lambda business_slug: {"provider": "cloudflare_r2", "removed": True, "status": "removed"},
    )

    deleted_rows: list[str] = []
    store._ensure_business = types.MethodType(lambda self, conn, business_slug: {"slug": business_slug}, store)
    store._delete_business_crons = types.MethodType(
        lambda self, business_slug, confirm=False: {"matched": [], "removed": []},
        store,
    )
    store._business_delete_db_counts = types.MethodType(lambda self, conn, business_slug: {"businesses": 1}, store)
    store._delete_business_db_rows = types.MethodType(
        lambda self, conn, business_slug, db_counts=None: deleted_rows.append(business_slug) or {"businesses": 1},
        store,
    )
    store._record_event = types.MethodType(lambda self, conn, **kwargs: None, store)
    store._delete_business_workspace_remote = types.MethodType(lambda self, business_slug: None, store)
    store._delete_business_domains = types.MethodType(
        lambda self, domains, confirm=True: {"provider": "vercel", "candidates": domains, "results": []},
        store,
    )
    store._workspace_root_override = tmp_path
    store._business_root = types.MethodType(
        lambda self, business_slug, sync=True: tmp_path / "businesses" / business_slug,
        store,
    )

    result = store._delete_business(
        object(),
        {"business": slug, "confirm": True, "delete_domains": False},
        reason="test",
        actor="test",
    )

    # The authoritative DB delete still ran despite the sub-user cleanup raising.
    assert deleted_rows == [slug]
    assert result["database"]["deleted"] == {"businesses": 1}
    assert not business_root.exists()
    # The failure is recorded, not swallowed silently or fatal.
    assert result["subuser_product_site"]["removed"] is False
    assert result["subuser_product_site"]["cleanup_complete"] is False
    assert result["subuser_product_site"]["skipped"] is True
    assert "ssh key not found" in result["subuser_product_site"]["error"]
    assert result["still_serving"] is True
    assert any("sub-user replica site/cache cleanup incomplete" in reason for reason in result["still_serving_reasons"])


def test_delete_business_no_files_still_removes_public_surfaces(tmp_path, monkeypatch):
    monkeypatch.setenv("TAKYON_PRODUCT_DEPLOY_DRY_RUN", "1")
    monkeypatch.setenv("TAKYON_PRODUCT_SITE_ROOT", str(tmp_path / "published-sites"))
    monkeypatch.setenv("TAKYON_PRODUCT_SERVICE_ROOT", str(tmp_path / "product-services"))
    monkeypatch.setenv("TAKYON_PRODUCT_SYSTEMD_DIR", str(tmp_path / "systemd"))
    monkeypatch.setenv("TAKYON_PRODUCT_CADDYFILE", str(tmp_path / "Caddyfile"))

    store = TakyonStore(tmp_path)
    slug = "anki"
    business_root = tmp_path / "businesses" / slug
    (business_root / "product").mkdir(parents=True)
    (business_root / "product" / "spec.md").write_text("# Spec\n", encoding="utf-8")

    published = tmp_path / "published-sites" / slug
    published.mkdir(parents=True)
    (published / "index.html").write_text("<h1>Anki</h1>\n", encoding="utf-8")

    service_root = tmp_path / "product-services" / slug
    service_root.mkdir(parents=True)
    (service_root / "server.js").write_text("console.log('live');\n", encoding="utf-8")

    systemd_dir = tmp_path / "systemd"
    systemd_dir.mkdir(parents=True)
    service_file = systemd_dir / "takyon-product-anki.service"
    service_file.write_text("[Unit]\nDescription=Takyon Product - anki\n", encoding="utf-8")

    caddyfile = tmp_path / "Caddyfile"
    caddyfile.write_text(
        "anki.fourmanifold.com {\n"
        "    reverse_proxy 127.0.0.1:4010\n"
        "}\n",
        encoding="utf-8",
    )

    public_edge_calls: list[str] = []
    subuser_calls: list[str] = []
    monkeypatch.setattr(
        takyon_core,
        "_delete_public_edge_product_site",
        lambda business_slug: public_edge_calls.append(business_slug)
        or {
            "provider": "cloudflare_r2",
            "slug": business_slug,
            "prefix": f"{business_slug}/",
            "status": "removed",
            "removed": True,
            "deleted": [f"{business_slug}/current"],
            "deleted_count": 1,
        },
    )
    monkeypatch.setattr(
        takyon_core,
        "_delete_subuser_product_site",
        lambda business_slug: subuser_calls.append(business_slug)
        or {
            "target": "root@134.209.123.8",
            "path": f"/opt/takyon/.takyon/product-sites/{business_slug}",
            "removed": True,
            "status": "removed",
        },
    )

    store._ensure_business = types.MethodType(lambda self, conn, business_slug: {"slug": business_slug}, store)
    store._delete_business_crons = types.MethodType(
        lambda self, business_slug, confirm=False: {"matched": [], "removed": []},
        store,
    )
    store._business_delete_db_counts = types.MethodType(lambda self, conn, business_slug: {"businesses": 1}, store)
    store._delete_business_db_rows = types.MethodType(
        lambda self, conn, business_slug, db_counts=None: {"businesses": 1},
        store,
    )
    store._record_event = types.MethodType(lambda self, conn, **kwargs: None, store)
    store._delete_business_workspace_remote = types.MethodType(lambda self, business_slug: None, store)
    store._delete_business_domains = types.MethodType(
        lambda self, domains, confirm=True: {"provider": "vercel", "candidates": domains, "results": []},
        store,
    )
    store._workspace_root_override = tmp_path
    store._business_root = types.MethodType(
        lambda self, business_slug, sync=True: tmp_path / "businesses" / business_slug,
        store,
    )

    result = store._delete_business(
        object(),
        {
            "business": slug,
            "confirm": True,
            "delete_files": False,
            "delete_domains": False,
        },
        reason="test",
        actor="test",
    )

    assert result["filesystem"]["skipped"] is True
    assert business_root.exists()
    assert result["public_edge_site"]["removed"] is True
    assert result["published_site"]["removed"] is True
    assert result["subuser_product_site"]["removed"] is True
    assert result["product_service"]["service_root"]["removed"] is True
    assert result["product_service"]["service_file"]["removed"] is True
    assert result["product_service"]["caddy_route"]["removed"] is True
    assert public_edge_calls == [slug]
    assert subuser_calls == [slug]
    assert not published.exists()
    assert not service_root.exists()
    assert not service_file.exists()
    assert "anki.fourmanifold.com" not in caddyfile.read_text(encoding="utf-8")


def test_clear_stale_supabase_business_cache_removes_deleted_business_cache(tmp_path):
    store = TakyonStore(tmp_path)
    slug = "longer"
    root = tmp_path / "cache" / "businesses" / slug
    root.mkdir(parents=True)
    (root / "product" / "site" / "README.md").parent.mkdir(parents=True)
    (root / "product" / "site" / "README.md").write_text("stale cache\n", encoding="utf-8")
    store._workspace_storage_backend_kind = types.MethodType(lambda self: "supabase_s3", store)

    cleared = store._clear_stale_supabase_business_cache(slug, root)

    assert cleared is True
    assert not root.exists()


def test_delete_subuser_product_site_uses_tracked_ssh_defaults(tmp_path, monkeypatch):
    key_path = tmp_path / "takyon_argon_alpha14"
    key_path.write_text("dummy-key\n", encoding="utf-8")

    calls: list[list[str]] = []

    def fake_run(command, **kwargs):
        calls.append(command)
        return types.SimpleNamespace(
            returncode=0,
            stdout="product_site=removed\nbusiness_cache=removed\n",
            stderr="",
        )

    monkeypatch.setenv("TAKYON_ENV", "prod")
    monkeypatch.setenv("TAKYON_SUBUSER_VPS_SSH_KEY", str(key_path))
    monkeypatch.delenv("TAKYON_SUBUSER_VPS_HOST", raising=False)
    monkeypatch.delenv("TAKYON_SUBUSER_VPS_HOSTS", raising=False)
    monkeypatch.delenv("TAKYON_SUBUSER_REPLICA_HOSTS", raising=False)
    monkeypatch.delenv("TAKYON_SUBUSER_VPS_USER", raising=False)
    monkeypatch.delenv("TAKYON_SUBUSER_REMOTE_HOME", raising=False)
    monkeypatch.delenv("TAKYON_SUBUSER_REMOTE_PRODUCT_SITES", raising=False)
    monkeypatch.setattr(takyon_core.shutil, "which", lambda name: "/usr/bin/ssh" if name == "ssh" else None)
    monkeypatch.setattr(takyon_core.subprocess, "run", fake_run)

    result = takyon_core._delete_subuser_product_site("latexflow")

    assert result["target"] == "root@134.209.123.8"
    assert result["path"] == "/opt/takyon/.takyon/product-sites/latexflow"
    assert result["cache_path"] == "/opt/takyon/.takyon/cache/businesses/latexflow"
    assert result["removed"] is True
    assert result["cleanup_complete"] is True
    assert result["status"] == "removed"
    assert [replica["target"] for replica in result["replicas"]] == [
        "root@134.209.123.8",
        "root@206.81.10.173",
    ]
    assert all(replica["product_site"]["removed"] for replica in result["replicas"])
    assert all(replica["business_cache"]["removed"] for replica in result["replicas"])
    assert len(calls) == 2
    assert all(call[0] == "/usr/bin/ssh" for call in calls)
    assert calls[0][1:10] == [
        "-i",
        str(key_path),
        "-o",
        "BatchMode=yes",
        "-o",
        "ConnectTimeout=10",
        "-o",
        "IdentitiesOnly=yes",
        "-o",
    ]
    assert calls[0][10] == "StrictHostKeyChecking=accept-new"
    assert calls[0][11] == "root@134.209.123.8"
    assert calls[1][11] == "root@206.81.10.173"
    assert "delete_one product_site /opt/takyon/.takyon/product-sites" in calls[0][12]
    assert "/opt/takyon/.takyon/product-sites/latexflow" in calls[0][12]
    assert "delete_one business_cache /opt/takyon/.takyon/cache/businesses" in calls[0][12]
    assert "/opt/takyon/.takyon/cache/businesses/latexflow" in calls[0][12]


def test_delete_subuser_product_site_attempts_all_replicas_after_failure(tmp_path, monkeypatch):
    key_path = tmp_path / "takyon_argon_alpha14"
    key_path.write_text("dummy-key\n", encoding="utf-8")
    calls: list[str] = []

    def fake_run(command, **kwargs):
        target = command[11]
        calls.append(target)
        if target == "root@134.209.123.8":
            return types.SimpleNamespace(returncode=255, stdout="", stderr="connection refused")
        return types.SimpleNamespace(
            returncode=0,
            stdout="product_site=missing\nbusiness_cache=removed\n",
            stderr="",
        )

    monkeypatch.setenv("TAKYON_ENV", "prod")
    monkeypatch.setenv("TAKYON_SUBUSER_VPS_SSH_KEY", str(key_path))
    monkeypatch.delenv("TAKYON_SUBUSER_VPS_HOST", raising=False)
    monkeypatch.delenv("TAKYON_SUBUSER_VPS_HOSTS", raising=False)
    monkeypatch.delenv("TAKYON_SUBUSER_REPLICA_HOSTS", raising=False)
    monkeypatch.setattr(takyon_core.shutil, "which", lambda name: "/usr/bin/ssh" if name == "ssh" else None)
    monkeypatch.setattr(takyon_core.subprocess, "run", fake_run)

    result = takyon_core._delete_subuser_product_site("latexflow")

    assert calls == ["root@134.209.123.8", "root@206.81.10.173"]
    assert result["removed"] is True
    assert result["cleanup_complete"] is False
    assert result["status"] == "partial"
    assert result["replicas"][0]["status"] == "failed"
    assert result["replicas"][1]["cleanup_complete"] is True
    assert "root@134.209.123.8: connection refused" in result["error"]


def test_subuser_vps_ssh_key_path_falls_back_to_tracked_operator_secret(monkeypatch, tmp_path):
    home_key = tmp_path / "home" / ".ssh" / "takyon_argon_alpha14"
    tracked_key = tmp_path / "secrets" / "takyon-subuser-sync.key"
    tracked_key.parent.mkdir(parents=True, exist_ok=True)
    tracked_key.write_text("dummy-key\n", encoding="utf-8")

    monkeypatch.delenv("TAKYON_SUBUSER_VPS_SSH_KEY", raising=False)
    monkeypatch.setattr(
        takyon_core,
        "_subuser_vps_default_ssh_key_candidates",
        lambda: [home_key, tracked_key],
    )

    assert takyon_core._subuser_vps_ssh_key_path() == tracked_key
