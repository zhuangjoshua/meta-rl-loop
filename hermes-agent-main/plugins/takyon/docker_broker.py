"""Internal Docker broker helpers.

The operator dashboard/worker planes use this module indirectly through the
``TAKYON_DOCKER_BINARY`` shim so they no longer need direct access to the host
Docker socket. A dedicated local broker service owns that authority instead.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any

_BROKER_URL_ENV = "TAKYON_DOCKER_BROKER_URL"
_BROKER_TOKEN_ENV = "TAKYON_DOCKER_BROKER_TOKEN"


def broker_url() -> str:
    return str(os.getenv(_BROKER_URL_ENV) or "").strip().rstrip("/")


def broker_token() -> str:
    # Least-privilege credential: the docker broker authorizes ONLY container lifecycle (it cannot
    # vend secrets or spend), so it gets its OWN dedicated token and never reuses the master
    # ``TAKYON_SAFEBOX_TOKEN``. Co-locating the safebox master token here was the red-team's
    # blast-radius bug (one client-plane env read → every secret); the docker broker must hold a
    # token that authorizes nothing but the docker proxy.
    return str(os.getenv(_BROKER_TOKEN_ENV) or "").strip()


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
    timeout: float = 30.0,
) -> dict[str, Any]:
    url = f"{broker_url()}{path}"
    if not broker_url():
        raise RuntimeError("docker broker URL is not configured")
    data = None
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(url, data=data, headers=broker_headers(), method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"docker broker http {exc.code}: {detail.strip() or exc.reason}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"docker broker unavailable: {exc.reason}") from exc
    try:
        decoded = json.loads(body or "{}")
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"docker broker returned invalid JSON for {path}") from exc
    if not isinstance(decoded, dict):
        raise RuntimeError(f"docker broker returned invalid payload for {path}")
    return decoded
