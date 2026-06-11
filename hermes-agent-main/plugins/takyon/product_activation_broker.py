"""Internal product activation broker helpers.

The dashboard and worker planes run as a locked-down ``takyon`` user and may
not write host activation surfaces like ``/etc/systemd/system`` directly. A
dedicated local broker service owns that authority instead.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

_BROKER_URL_ENV = "TAKYON_PRODUCT_ACTIVATION_BROKER_URL"
_BROKER_TOKEN_ENV = "TAKYON_PRODUCT_ACTIVATION_BROKER_TOKEN"
_SAFEBOX_TOKEN_ENV = "TAKYON_SAFEBOX_TOKEN"


def broker_url() -> str:
    return str(os.getenv(_BROKER_URL_ENV) or "").strip().rstrip("/")


def broker_token() -> str:
    direct = str(os.getenv(_BROKER_TOKEN_ENV) or "").strip()
    if direct:
        return direct
    return str(os.getenv(_SAFEBOX_TOKEN_ENV) or "").strip()


def broker_enabled() -> bool:
    return bool(broker_url())


def broker_headers() -> dict[str, str]:
    token = broker_token()
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def broker_request(
    path: str,
    *,
    payload: dict[str, Any] | None = None,
    timeout: float = 60.0,
) -> dict[str, Any]:
    url = f"{broker_url()}{path}"
    if not broker_url():
        raise RuntimeError("product activation broker URL is not configured")
    data = None
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(url, data=data, headers=broker_headers(), method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"product activation broker http {exc.code}: {detail.strip() or exc.reason}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"product activation broker unavailable: {exc.reason}") from exc
    try:
        decoded = json.loads(body or "{}")
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"product activation broker returned invalid JSON for {path}") from exc
    if not isinstance(decoded, dict):
        raise RuntimeError(f"product activation broker returned invalid payload for {path}")
    return decoded


def publish_next_product_service(*, source_root: Path, slug: str, publish_target: str) -> dict[str, Any]:
    return broker_request(
        "/v1/publish-next-product-service",
        payload={
            "source_root": str(source_root),
            "slug": str(slug),
            "publish_target": str(publish_target),
        },
        timeout=600.0,
    )
