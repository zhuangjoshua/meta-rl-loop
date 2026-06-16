from __future__ import annotations

from pathlib import Path
import types

from plugins.takyon import core as takyon_core
from plugins.takyon.core import TakyonStore


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


def test_delete_subuser_product_site_uses_tracked_ssh_defaults(tmp_path, monkeypatch):
    key_path = tmp_path / "takyon_argon_alpha14"
    key_path.write_text("dummy-key\n", encoding="utf-8")

    calls: list[list[str]] = []

    def fake_run(command, **kwargs):
        calls.append(command)
        return types.SimpleNamespace(returncode=0, stdout="removed\n", stderr="")

    monkeypatch.setenv("TAKYON_SUBUSER_VPS_SSH_KEY", str(key_path))
    monkeypatch.delenv("TAKYON_SUBUSER_VPS_HOST", raising=False)
    monkeypatch.delenv("TAKYON_SUBUSER_VPS_USER", raising=False)
    monkeypatch.delenv("TAKYON_SUBUSER_REMOTE_HOME", raising=False)
    monkeypatch.delenv("TAKYON_SUBUSER_REMOTE_PRODUCT_SITES", raising=False)
    monkeypatch.setattr(takyon_core.shutil, "which", lambda name: "/usr/bin/ssh" if name == "ssh" else None)
    monkeypatch.setattr(takyon_core.subprocess, "run", fake_run)

    result = takyon_core._delete_subuser_product_site("latexflow")

    assert result == {
        "target": "root@134.209.123.8",
        "path": "/opt/takyon/.takyon/product-sites/latexflow",
        "removed": True,
        "status": "removed",
    }
    assert calls and calls[0][0] == "/usr/bin/ssh"
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
    assert "target=/opt/takyon/.takyon/product-sites/latexflow" in calls[0][12]
    assert "root=/opt/takyon/.takyon/product-sites" in calls[0][12]
