"""Local product activation broker service for the operator activation plane.

The dashboard and worker services run as the locked-down ``takyon`` user, so
they cannot mutate host activation surfaces like ``/etc/systemd/system`` or
``/etc/caddy`` directly. This localhost-only broker owns that authority.
"""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel

from plugins.takyon import core as takyon_core

_BROKER_TOKEN_ENV = "TAKYON_PRODUCT_ACTIVATION_BROKER_TOKEN"
_SAFEBOX_TOKEN_ENV = "TAKYON_SAFEBOX_TOKEN"


class _PublishBody(BaseModel):
    source_root: str
    slug: str
    publish_target: str


def _broker_token() -> str:
    direct = str(os.getenv(_BROKER_TOKEN_ENV) or "").strip()
    if direct:
        return direct
    return str(os.getenv(_SAFEBOX_TOKEN_ENV) or "").strip()


def _require_internal_token(authorization: str | None = Header(default=None)) -> None:
    expected = _broker_token()
    if not expected:
        raise HTTPException(status_code=401, detail="product activation broker token not configured")
    presented = str(authorization or "").strip()
    if presented != f"Bearer {expected}":
        raise HTTPException(status_code=401, detail="unauthorized")


def _takyon_home() -> Path:
    raw = str(os.getenv("TAKYON_HOME") or "").strip()
    return Path(raw or "~/.takyon").expanduser().resolve()


def _validate_source_root(raw: str) -> Path:
    source_root = Path(str(raw or "").strip()).expanduser().resolve()
    if not source_root.exists():
        raise HTTPException(status_code=404, detail="product source root not found")
    allowed_roots = (
        (_takyon_home() / "businesses").resolve(),
        (_takyon_home() / "cache" / "businesses").resolve(),
    )
    if not any(root == source_root or root in source_root.parents for root in allowed_roots):
        raise HTTPException(status_code=400, detail="product source root escaped Takyon business roots")
    return source_root


def build_product_activation_broker_app() -> FastAPI:
    app = FastAPI(title="Takyon Product Activation Broker")

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/v1/publish-next-product-service")
    def publish_next_product_service(
        body: _PublishBody,
        authorization: str | None = Header(default=None),
    ) -> dict:
        _require_internal_token(authorization)
        source_root = _validate_source_root(body.source_root)
        return takyon_core._publish_next_product_service_prepared(
            source_root=source_root,
            slug=str(body.slug or "").strip(),
            publish_target=str(body.publish_target or "").strip(),
        )

    return app


app = build_product_activation_broker_app()
