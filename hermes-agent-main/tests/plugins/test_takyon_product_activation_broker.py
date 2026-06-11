from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from plugins.takyon import product_activation_broker_app as broker_mod


def test_product_activation_broker_fails_closed_without_token(monkeypatch):
    monkeypatch.delenv("TAKYON_PRODUCT_ACTIVATION_BROKER_TOKEN", raising=False)
    monkeypatch.delenv("TAKYON_SAFEBOX_TOKEN", raising=False)
    client = TestClient(broker_mod.build_product_activation_broker_app())

    response = client.post(
        "/v1/publish-next-product-service",
        json={
            "source_path": "product/site",
            "slug": "tomato",
            "publish_target": "https://tomato.fourmanifold.com/",
        },
    )

    assert response.status_code == 401


def test_product_activation_broker_runs_prepared_publish_for_allowed_business_root(tmp_path, monkeypatch):
    business_root = tmp_path / "cache" / "businesses" / "tomato" / "product" / "site"
    business_root.mkdir(parents=True)
    monkeypatch.setenv("TAKYON_HOME", str(tmp_path))
    monkeypatch.setenv("TAKYON_PRODUCT_ACTIVATION_BROKER_TOKEN", "shared-token")

    captured: dict[str, object] = {}

    def _fake_publish_next_product_service_prepared(*, source_root: Path, slug: str, publish_target: str) -> dict[str, str]:
        captured["source_root"] = source_root
        captured["slug"] = slug
        captured["publish_target"] = publish_target
        return {
            "status": "published",
            "public_url": publish_target,
            "publish_target": publish_target,
            "published_at": "2026-06-10T00:00:00Z",
            "publish_root": "/opt/takyon/.takyon/product-services/tomato",
            "publish_source_path": "product/site",
            "blocker": "",
        }

    monkeypatch.setattr(
        broker_mod.takyon_core,
        "_publish_next_product_service_prepared",
        _fake_publish_next_product_service_prepared,
    )
    client = TestClient(broker_mod.build_product_activation_broker_app())

    response = client.post(
        "/v1/publish-next-product-service",
        headers={"Authorization": "Bearer shared-token"},
        json={
            "source_path": "product/site",
            "slug": "tomato",
            "publish_target": "https://tomato.fourmanifold.com/",
        },
    )

    assert response.status_code == 200
    assert response.json()["status"] == "published"
    assert captured["source_root"] == business_root.resolve()
    assert captured["slug"] == "tomato"
    assert captured["publish_target"] == "https://tomato.fourmanifold.com/"


def test_product_activation_broker_prefers_allowed_scratch_publish_root(tmp_path, monkeypatch):
    canonical_root = tmp_path / "cache" / "businesses" / "tomato" / "product" / "site"
    canonical_root.mkdir(parents=True)
    scratch_root = (
        tmp_path
        / "tmp"
        / "workspaces"
        / "takyon-user-tomato-abc123"
        / "businesses"
        / "tomato"
        / "product"
        / "site"
    )
    scratch_root.mkdir(parents=True)
    monkeypatch.setenv("TAKYON_HOME", str(tmp_path))
    monkeypatch.setenv("TAKYON_PRODUCT_ACTIVATION_BROKER_TOKEN", "shared-token")

    captured: dict[str, object] = {}

    def _fake_publish_next_product_service_prepared(*, source_root: Path, slug: str, publish_target: str) -> dict[str, str]:
        captured["source_root"] = source_root
        captured["slug"] = slug
        captured["publish_target"] = publish_target
        return {
            "status": "published",
            "public_url": publish_target,
            "publish_target": publish_target,
            "published_at": "2026-06-10T00:00:00Z",
            "publish_root": "/opt/takyon/.takyon/product-services/tomato",
            "publish_source_path": "product/site",
            "blocker": "",
        }

    monkeypatch.setattr(
        broker_mod.takyon_core,
        "_publish_next_product_service_prepared",
        _fake_publish_next_product_service_prepared,
    )
    client = TestClient(broker_mod.build_product_activation_broker_app())

    response = client.post(
        "/v1/publish-next-product-service",
        headers={"Authorization": "Bearer shared-token"},
        json={
            "source_path": "product/site",
            "source_root": str(scratch_root),
            "slug": "tomato",
            "publish_target": "https://tomato.fourmanifold.com/",
        },
    )

    assert response.status_code == 200
    assert response.json()["status"] == "published"
    assert captured["source_root"] == scratch_root.resolve()
    assert captured["slug"] == "tomato"
    assert captured["publish_target"] == "https://tomato.fourmanifold.com/"
