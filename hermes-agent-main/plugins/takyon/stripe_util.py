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
from typing import Any


class StripeError(Exception):
    """Any Stripe REST call or webhook-signature check that failed in the control plane.
    Raised (never swallowed) so a missing key or bad signature surfaces as a clear
    error instead of a silently-faked success."""


def stripe_request(path: str, params: dict[str, Any]) -> dict[str, Any]:
    """POST form-encoded `params` to `https://api.stripe.com/v1/{path}` with the shared
    platform secret key, dropping any None-valued params. Returns the parsed JSON object.
    Raises StripeError if STRIPE_SECRET_KEY is absent (the call is never faked) or Stripe
    returns a non-2xx response."""
    key = os.environ.get("STRIPE_SECRET_KEY")
    if not key:
        raise StripeError("Stripe action requires STRIPE_SECRET_KEY")
    data = urllib.parse.urlencode(
        {k: v for k, v in params.items() if v is not None}
    ).encode("utf-8")
    request = urllib.request.Request(
        f"https://api.stripe.com/v1/{path.lstrip('/')}",
        data=data,
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        method="POST",
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
