"""Self-contained Stripe REST + webhook-signature helpers for the Postgres control
plane (flow A: top-level user topups).

Why a second copy of helpers that already live in core.py: core's `_stripe_request`
and `_verify_stripe_signature` sit inside the large SQLite trunk module, raise
`TakyonError`, and call `load_takyon_env()`. Importing them here would couple the
Postgres control plane to that trunk and risk an import cycle — core's provisioning
path already reaches into control-plane modules. These are pure-stdlib reimplementations
with their own `StripeError`, reading configuration directly from `os.environ` exactly
as custody.py does. The wire format is byte-for-byte identical to core's (form-encoded
REST; `t=<unix>,v1=<hex>` signed-payload HMAC-SHA256 over `"{t}.{body}"`; 300s
tolerance) so control-plane behavior matches the rest of the platform.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

try:
    from dotenv import load_dotenv
except Exception:  # pragma: no cover - Takyon normally depends on python-dotenv.
    def load_dotenv(dotenv_path: Path, override: bool = False, encoding: str = "utf-8") -> bool:
        """Tiny fallback so Stripe helpers fail on missing APIs, not imports."""
        try:
            lines = Path(dotenv_path).read_text(encoding=encoding).splitlines()
        except OSError:
            return False
        for line in lines:
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            key, value = stripped.split("=", 1)
            key = key.strip().removeprefix("export ").strip()
            value = value.strip().strip('"').strip("'")
            if key and (override or key not in os.environ):
                os.environ[key] = value
        return True

from takyon_constants import get_takyon_home


_loaded_env_paths: set[Path] = set()


class StripeError(Exception):
    """Any Stripe REST call or webhook-signature check that failed in the control plane.
    Raised (never swallowed) so a missing key or bad signature surfaces as a clear
    error instead of a silently-faked success."""


def _candidate_env_files() -> list[Path]:
    takyon_home = Path(os.getenv("TAKYON_HOME") or get_takyon_home()).expanduser()
    repo_root = Path(__file__).resolve().parents[2]
    return [takyon_home / ".env", repo_root / ".env"]


def _load_control_plane_env() -> None:
    """Load Takyon env files without overriding process-level secrets."""
    for path in _candidate_env_files():
        try:
            resolved = path.resolve()
        except OSError:
            continue
        if resolved in _loaded_env_paths or not resolved.exists() or not resolved.is_file():
            continue
        load_dotenv(dotenv_path=resolved, override=False, encoding="utf-8")
        _loaded_env_paths.add(resolved)


def stripe_request(
    path: str,
    params: dict[str, Any],
    *,
    method: str = "POST",
) -> dict[str, Any]:
    """Send a Stripe API request to `https://api.stripe.com/v1/{path}` with the shared
    platform secret key, dropping any None-valued params. POST bodies are form-encoded;
    GET params are query-encoded. Returns the parsed JSON object.
    Raises StripeError if STRIPE_SECRET_KEY is absent (the call is never faked) or Stripe
    returns a non-2xx response."""
    _load_control_plane_env()
    key = os.environ.get("STRIPE_SECRET_KEY")
    if not key:
        raise StripeError("Stripe action requires STRIPE_SECRET_KEY")
    verb = str(method or "POST").strip().upper() or "POST"
    encoded = urllib.parse.urlencode(
        {k: v for k, v in params.items() if v is not None}
    )
    base_url = f"https://api.stripe.com/v1/{path.lstrip('/')}"
    data = None if verb == "GET" else encoded.encode("utf-8")
    request_url = base_url
    if verb == "GET" and encoded:
        request_url = f"{base_url}?{encoded}"
    request = urllib.request.Request(
        request_url,
        data=data,
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        method=verb,
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise StripeError(f"Stripe {path} failed: {exc.code} {body}") from exc


def verify_stripe_signature(raw_body: str, signature: str, secret: str) -> None:
    """Verify a Stripe `Stripe-Signature` header against the exact `raw_body` bytes-as-text
    using `secret`. Returns None on success; raises StripeError on a malformed header, a
    timestamp outside the 300s replay tolerance, or a digest that matches no provided v1.
    The signed payload is `"{timestamp}.{raw_body}"`."""
    parts: dict[str, list[str]] = {}
    for part in str(signature or "").split(","):
        if "=" not in part:
            continue
        k, v = part.split("=", 1)
        parts.setdefault(k, []).append(v)
    timestamp = parts.get("t", [""])[0]
    signatures = parts.get("v1", [])
    if not timestamp or not signatures:
        raise StripeError("invalid Stripe signature header")
    try:
        if abs(time.time() - int(timestamp)) > 300:
            raise StripeError("Stripe signature timestamp is outside tolerance")
    except ValueError as exc:
        raise StripeError("invalid Stripe signature timestamp") from exc
    expected = hmac.new(
        secret.encode("utf-8"),
        f"{timestamp}.{raw_body}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    if not any(hmac.compare_digest(expected, sig) for sig in signatures):
        raise StripeError("Stripe signature verification failed")


def build_signature_header(raw_body: str, secret: str, *, timestamp: int | None = None) -> str:
    """Construct a valid `Stripe-Signature` header for `raw_body` signed with `secret`.
    For tests and local webhook simulation ONLY — it produces exactly what Stripe would
    send so `verify_stripe_signature` round-trips, with no network and no live secret."""
    ts = int(time.time()) if timestamp is None else int(timestamp)
    sig = hmac.new(
        secret.encode("utf-8"),
        f"{ts}.{raw_body}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return f"t={ts},v1={sig}"
