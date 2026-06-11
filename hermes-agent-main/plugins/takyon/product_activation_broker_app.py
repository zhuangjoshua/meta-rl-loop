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
    source_path: str
    source_root: str = ""
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


def _canonical_source_root(*, slug: str, source_path: str) -> Path:
    try:
        relative = takyon_core._safe_relpath(source_path or "product/site", field="source_path")
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    try:
        store = takyon_core.TakyonStore(root=_takyon_home(), system_plane="product_activation_broker")
        source_root = store._resolve_business_file(
            slug,
            relative.as_posix(),
            require_output_root=True,
            field="source_path",
        ).resolve()
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"failed to resolve canonical product source: {exc}") from exc
    if not source_root.exists():
        raise HTTPException(status_code=404, detail="product source root not found")
    return source_root


def _resolve_publish_source_root(*, slug: str, source_path: str, source_root: str = "") -> Path:
    canonical_root = _canonical_source_root(slug=slug, source_path=source_path)
    explicit = str(source_root or "").strip()
    if not explicit:
        return canonical_root

    try:
        relative = takyon_core._safe_relpath(source_path or "product/site", field="source_path")
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    resolved = Path(explicit).expanduser().resolve()
    if resolved == canonical_root:
        return resolved
    if not resolved.exists():
        raise HTTPException(status_code=404, detail="product source root not found")

    scratch_root = Path(takyon_core.get_takyon_home()).resolve() / "tmp" / "workspaces"
    expected_suffix = (Path("businesses") / takyon_core._slugify(slug) / relative).parts
    if (
        scratch_root in (resolved, *resolved.parents)
        and len(resolved.parts) >= len(expected_suffix)
        and tuple(resolved.parts[-len(expected_suffix):]) == expected_suffix
    ):
        return resolved

    raise HTTPException(status_code=400, detail="product source root is outside the allowed Takyon business paths")


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
        source_root = _resolve_publish_source_root(
            slug=str(body.slug or "").strip(),
            source_path=str(body.source_path or "").strip(),
            source_root=str(body.source_root or "").strip(),
        )
        return takyon_core._publish_next_product_service_prepared(
            source_root=source_root,
            slug=str(body.slug or "").strip(),
            publish_target=str(body.publish_target or "").strip(),
        )

    return app


app = build_product_activation_broker_app()
