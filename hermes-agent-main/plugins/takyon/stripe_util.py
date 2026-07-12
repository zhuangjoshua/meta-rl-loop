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
import os
import threading
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


_VERIFIED_LIVE_ACCOUNTS: set[tuple[str, str]] = set()
_VERIFIED_LIVE_ACCOUNTS_LOCK = threading.Lock()
_CHECKOUT_BRANDING_API_VERSION = "2025-09-30.clover"


def _stripe_mode() -> str:
    mode = str(os.getenv("TAKYON_STRIPE_MODE") or "test").strip().lower() or "test"
    if mode not in {"test", "live"}:
        raise StripeError("TAKYON_STRIPE_MODE must be test or live")
    return mode


def _validated_stripe_key(key: Any) -> str:
    """Return a normalized Stripe secret key only when it matches the explicit mode.

    Test mode is the fail-closed default so an existing deployment cannot start moving real
    money merely because a live key was provisioned. Live mode additionally requires the
    production Safebox host; no runtime plane may use a live Stripe credential directly.
    """
    mode = _stripe_mode()

    normalized_key = str(key or "").strip()
    if normalized_key.startswith(("sk_test_", "rk_test_")):
        key_mode = "test"
    elif normalized_key.startswith(("sk_live_", "rk_live_")):
        key_mode = "live"
    else:
        raise StripeError(
            "Stripe secret key has an unrecognized prefix; expected sk_test_/rk_test_ "
            "or sk_live_/rk_live_"
        )

    if key_mode != mode:
        raise StripeError(
            f"Stripe {key_mode} key does not match TAKYON_STRIPE_MODE={mode}"
        )

    if mode == "live":
        takyon_env = str(os.getenv("TAKYON_ENV") or "").strip().lower()
        host_role = str(os.getenv("TAKYON_HOST_ROLE") or "").strip().lower()
        if takyon_env != "prod" or host_role != "safebox":
            raise StripeError(
                "Stripe live mode requires TAKYON_ENV=prod and TAKYON_HOST_ROLE=safebox"
            )

    return normalized_key


def stripe_key_livemode() -> bool:
    """Return the configured key's mode without exposing the key."""
    key = str(safebox.read_env_backed_value("STRIPE_SECRET_KEY") or "").strip()
    if not key:
        raise StripeError("Stripe action requires STRIPE_SECRET_KEY")
    if key.startswith(("sk_live_", "rk_live_")):
        return True
    if key.startswith(("sk_test_", "rk_test_")):
        return False
    raise StripeError("STRIPE_SECRET_KEY has an unrecognized mode prefix")


def _stripe_http_request(
    path: str,
    params: dict[str, Any],
    *,
    method: str,
    key: str,
    idempotency_key: str | None = None,
    api_version: str | None = None,
) -> dict[str, Any]:
    verb = str(method or "POST").strip().upper() or "POST"
    encoded = urllib.parse.urlencode(
        {k: v for k, v in params.items() if v is not None}
    )
    base_url = f"https://api.stripe.com/v1/{path.lstrip('/')}"
    data = None if verb == "GET" else encoded.encode("utf-8")
    request_url = base_url
    if verb == "GET" and encoded:
        request_url = f"{base_url}?{encoded}"
    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/x-www-form-urlencoded",
    }
    if idempotency_key is not None:
        normalized_idempotency_key = str(idempotency_key).strip()
        if verb != "POST":
            raise StripeError("Stripe idempotency keys require POST")
        if not normalized_idempotency_key or len(normalized_idempotency_key) > 255:
            raise StripeError("invalid Stripe idempotency key")
        headers["Idempotency-Key"] = normalized_idempotency_key
    if api_version is not None:
        normalized_api_version = str(api_version).strip()
        if not normalized_api_version:
            raise StripeError("invalid Stripe API version")
        headers["Stripe-Version"] = normalized_api_version
    request = urllib.request.Request(
        request_url,
        data=data,
        headers=headers,
        method=verb,
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise StripeError(f"Stripe {path} failed: {exc.code} {body}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        # A transport failure is ambiguous: Stripe may have committed the request before the
        # response disappeared. Normalize it into StripeError so mutation-specific callers (most
        # importantly immediate subscription cancellation) can read provider truth before deciding
        # whether the operation failed.
        raise StripeError(
            f"Stripe {path} transport failed: {exc.__class__.__name__}"
        ) from exc


def _verify_live_account_identity(key: str) -> None:
    """Verify a live key belongs to the configured account before any money-moving request."""
    expected_account_id = str(os.getenv("TAKYON_STRIPE_ACCOUNT_ID") or "").strip()
    if not expected_account_id:
        raise StripeError("Stripe live mode requires TAKYON_STRIPE_ACCOUNT_ID")
    key_fingerprint = hashlib.sha256(key.encode("utf-8")).hexdigest()
    cache_key = (key_fingerprint, expected_account_id)
    with _VERIFIED_LIVE_ACCOUNTS_LOCK:
        if cache_key in _VERIFIED_LIVE_ACCOUNTS:
            return
        account = _stripe_http_request("account", {}, method="GET", key=key)
        actual_account_id = (
            str(account.get("id") or "").strip() if isinstance(account, dict) else ""
        )
        if not actual_account_id:
            raise StripeError("Stripe live account verification returned no account id")
        if actual_account_id != expected_account_id:
            raise StripeError(
                "Stripe live key account mismatch: "
                f"expected {expected_account_id}, got {actual_account_id}"
            )
        _VERIFIED_LIVE_ACCOUNTS.add(cache_key)


def stripe_request(
    path: str,
    params: dict[str, Any],
    *,
    method: str = "POST",
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    """Send a mode- and account-gated Stripe API request.

    POST bodies are form-encoded and GET params are query-encoded. Live keys are accepted only
    on the production Safebox and are verified against TAKYON_STRIPE_ACCOUNT_ID before the first
    operation for that key in this process.
    """
    if safebox._remote_enabled() and not safebox._local_authority_enabled():
        if idempotency_key is not None:
            raise StripeError("Stripe idempotency keys are Safebox-local only")
        try:
            return safebox.stripe_request(path, params, method=method)
        except safebox.RemoteSafeboxError as exc:
            raise StripeError(str(exc)) from exc
    key = safebox.read_env_backed_value("STRIPE_SECRET_KEY")
    if not key:
        raise StripeError("Stripe action requires STRIPE_SECRET_KEY")
    key = _validated_stripe_key(key)
    if key.startswith(("sk_live_", "rk_live_")):
        _verify_live_account_identity(key)
    return _stripe_http_request(
        path,
        params,
        method=method,
        key=key,
        idempotency_key=idempotency_key,
        api_version=(
            _CHECKOUT_BRANDING_API_VERSION
            if str(path or "").strip().lstrip("/") == "checkout/sessions"
            and any(str(name).startswith("branding_settings[") for name in params)
            else None
        ),
    )


def validate_stripe_webhook_event_mode(event: Any) -> None:
    """Reject a signed Stripe event whose live/test mode conflicts with this deployment.

    Stripe always sends a boolean ``livemode``. Test mode tolerates old fixtures that omit the
    field, but a present boolean must still be false. Live mode requires an explicit boolean true
    so a missing or malformed field can never be interpreted as authorization for real money.
    """
    mode = _stripe_mode()
    livemode = event.get("livemode") if isinstance(event, dict) else None
    if isinstance(livemode, bool):
        if livemode != (mode == "live"):
            raise StripeError(
                f"Stripe webhook livemode={livemode} does not match TAKYON_STRIPE_MODE={mode}"
            )
        return
    if mode == "live":
        raise StripeError("Stripe live webhook requires boolean livemode=true")


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
