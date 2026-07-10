"""Self-contained Stripe REST + webhook-signature helpers for the Postgres control
plane (flow A: top-level user subscription billing).

Why a second copy of helpers that already live in core.py: core's `_stripe_request`
and `_verify_stripe_signature` sit inside the large SQLite trunk module and raise
`TakyonError`. Importing them here would couple the Postgres control plane to that
trunk and risk an import cycle — core's provisioning path already reaches into
control-plane modules. These are pure-stdlib reimplementations with their own
`StripeError`, but they now resolve secrets through the read-only Safebox env
authority instead of reading raw env directly. The wire format is byte-for-byte
identical to core's (form-encoded REST; `t=<unix>,v1=<hex>` signed-payload
HMAC-SHA256 over `"{t}.{body}"`; 300s tolerance) so control-plane behavior
matches the rest of the platform.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from . import safebox


class StripeError(Exception):
    """Any Stripe REST call or webhook-signature check that failed in the control plane.
    Raised (never swallowed) so a missing key or bad signature surfaces as a clear
    error instead of a silently-faked success."""


def stripe_key_livemode() -> bool:
    """Return the configured key's mode without exposing the key; unknown key shapes fail closed."""
    key = str(safebox.read_env_backed_value("STRIPE_SECRET_KEY") or "").strip()
    if not key:
        raise StripeError("Stripe action requires STRIPE_SECRET_KEY")
    if key.startswith(("sk_live_", "rk_live_")):
        return True
    if key.startswith(("sk_test_", "rk_test_")):
        return False
    raise StripeError("STRIPE_SECRET_KEY has an unrecognized mode prefix")

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
    if safebox._remote_enabled() and not safebox._local_authority_enabled():
        try:
            return safebox.stripe_request(path, params, method=method)
        except safebox.RemoteSafeboxError as exc:
            raise StripeError(str(exc)) from exc
    key = safebox.read_env_backed_value("STRIPE_SECRET_KEY")
    if not key:
        raise StripeError("Stripe action requires STRIPE_SECRET_KEY")
    # Hard rail (GOAL_RULES §0): this MVP runs Stripe in test mode only. A live secret key
    # (`sk_live_…`) must NEVER reach the wire — refuse BEFORE any network call so a
    # mis-provisioned live key cannot move real money. rstrip() guards a trailing-newline key.
    if str(key).strip().startswith("sk_live_"):
        raise StripeError(
            "refusing to use a live Stripe key (sk_live_): this deployment is restricted to "
            "Stripe test mode (sk_test_)"
        )
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
