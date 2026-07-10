"""Safebox authority for sensitive env-backed values and top-level auth state.

The backing store for deployed Takyon secrets may still be process env
(e.g. Vercel/runtime injection) or ``TAKYON_HOME/.env``. The authority
boundary lives here: callers should route secret and funding-sensitive
reads and writes through Safebox instead of touching ``os.environ`` or
parsing/writing the env file directly.

This module exposes only typed env authority for keys that look secret-
backed or are explicitly sensitive (for example database URLs). It does
not provide a generic config store for arbitrary dashboard settings.

Safebox also owns the top-level ``tk_...`` API-key verifier registry. The
control plane may mirror key metadata into Postgres for joins/audit, but the
granting authority for a presented raw key lives here, not in a mutable DB row.
"""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import threading
import urllib.error
import urllib.parse
import urllib.request
import base64
import hashlib
import hmac
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows fallback
    fcntl = None

from takyon_constants import get_takyon_home
from takyon_cli.config import (
    _normalize_env_value_for_storage,
    _remove_env_value_direct,
    _save_env_value_direct,
    _validate_env_key,
    is_managed,
    load_env,
    managed_error,
)

from . import environment
from .user_api_keys import hash_api_key, is_well_formed, key_prefix
from .business_credits import (
    CreativeCreditBalances,
    CreativeCreditReservation,
    InsufficientCreativeCredits,
    UnknownCreativeCreditReservation,
)

_EXACT_SENSITIVE_ENV_KEYS = frozenset(
    {
        # The team distribution p12 (base64) is raw signing material; "_B64" is not a sensitive
        # suffix, so it is pinned here explicitly — without this the managed-secret manifest
        # silently drops it and the safebox build-credentials route fails closed unconfigured.
        "APP_STORE_DIST_P12_B64",
        "AUTH0_CLIENT_SECRET",
        "AUTH0_SECRET",
        "DATABASE_URL",
        "MIGRATION_DATABASE_URL",
        # DataForSEO authenticates with a login/password pair. The password matches
        # the _PASSWORD suffix, but the login does not end in a sensitive suffix, so
        # we keep it behind the Safebox gate explicitly (no os.getenv side door) the
        # same way the Google Ads client id below is kept.
        "DATAFORSEO_LOGIN",
        # Google OAuth client ids don't end in a sensitive suffix, but we keep the
        # full Google Ads credential set behind the Safebox gate (no os.getenv side door).
        "GOOGLE_ADS_CLIENT_ID",
        "POSTGRES_PRISMA_URL",
        "POSTGRES_URL",
        "POSTGRES_URL_NON_POOLING",
        "TAKYON_APP_DATABASE_URL",
        "TAKYON_MIGRATION_DATABASE_URL",
        "TAKYON_OPERATOR_DATABASE_URL",
        "TAKYON_SAFEBOX_DATABASE_URL",
    }
)

_SENSITIVE_ENV_SUFFIXES = (
    "_ACCESS_KEY_ID",
    "_ACCESS_TOKEN",
    "_API_KEY",
    "_CLIENT_SECRET",
    "_KEY",
    "_PASSWORD",
    "_PRIVATE_KEY",
    "_SECRET",
    "_SECRET_ACCESS_KEY",
    "_TOKEN",
    "_WEBHOOK_SECRET",
)

_SAFEBOX_DIRNAME = "safebox"
_USER_API_KEYS_FILE_NAME = "user_api_keys.json"
_USER_API_KEYS_STATE_VERSION = 1
_USER_API_KEYS_MUTEX = threading.RLock()
_SAFEBOX_REMOTE_URL_ENV = "TAKYON_SAFEBOX_URL"
_SAFEBOX_REMOTE_TOKEN_ENV = "TAKYON_SAFEBOX_TOKEN"
_SAFEBOX_OPERATOR_TOKEN_ENV = "TAKYON_SAFEBOX_OPERATOR_TOKEN"
_HOST_ROLE_ENV = "TAKYON_HOST_ROLE"
_SAFEBOX_HOST_ROLE = "safebox"
_MANAGED_SECRET_COMMAND_ENV = "TAKYON_MANAGED_SECRET_COMMAND"
_MANAGED_SECRET_KEYS_ENV = "TAKYON_MANAGED_SECRET_KEYS"
_MANAGED_SECRET_TIMEOUT_ENV = "TAKYON_MANAGED_SECRET_TIMEOUT_SECONDS"
_MANAGED_SECRET_CACHE_ENV = "TAKYON_MANAGED_SECRET_CACHE_SECONDS"
_MANAGED_SECRET_CACHE_DEFAULT_SECONDS = 60.0
_MANAGED_SECRET_MUTEX = threading.RLock()
# Keyed by (environment.cache_scope(), secret name) — plan R3: a dev-scoped instance must never
# read a prod-scoped cached secret out of this process-global map. Values stay (monotonic, value).
_MANAGED_SECRET_CACHE: dict[tuple[str, str], tuple[float, str]] = {}


class RemoteSafeboxError(RuntimeError):
    """A Safebox remote request failed with a concrete HTTP status/payload."""

    def __init__(self, message: str, *, status_code: int, payload: dict[str, Any]):
        super().__init__(message)
        self.status_code = int(status_code)
        self.payload = payload


class SafeboxAuthorityUnavailable(RuntimeError):
    """No remote Safebox is configured and this process is not the Safebox host."""


class ManagedSecretLookupError(RuntimeError):
    """The configured managed-secret command failed for a safebox-owned key."""


class StripeBillingWebhookUnconfigured(RuntimeError):
    """Billing webhook verification is unavailable because Safebox lacks the secret."""


class StripeBillingWebhookInvalidSignature(RuntimeError):
    """The presented Stripe billing webhook signature failed verification."""


class StripeAppWebhookUnconfigured(RuntimeError):
    """App (flow-B) webhook verification is unavailable because Safebox lacks the secret."""


class StripeAppWebhookInvalidSignature(RuntimeError):
    """The presented Stripe app (flow-B) webhook signature failed verification."""


class ShopifyAppWebhookUnconfigured(RuntimeError):
    """Shopify app webhook verification is unavailable because Safebox lacks the shared secret."""


class ShopifyAppWebhookInvalidSignature(RuntimeError):
    """The presented X-Shopify-Hmac-Sha256 failed verification against the raw body."""


class Auth0AuthorityUnconfigured(RuntimeError):
    """Auth0 authority is unavailable because Safebox lacks required config/secrets."""


class Auth0AuthorityRejected(RuntimeError):
    """An Auth0 login/session token failed Safebox-owned verification."""

    def __init__(self, message: str, *, status_code: int = 403):
        super().__init__(message)
        self.status_code = int(status_code)


def is_sensitive_env_key(key: str) -> bool:
    name = str(key or "").strip()
    if not name:
        return False
    if name in _EXACT_SENSITIVE_ENV_KEYS:
        return True
    return name.endswith(_SENSITIVE_ENV_SUFFIXES)


def _remote_base_url() -> str:
    return str(os.environ.get(_SAFEBOX_REMOTE_URL_ENV) or "").strip().rstrip("/")


def _remote_enabled() -> bool:
    return bool(_remote_base_url())


def _normalized_host_role() -> str:
    # Thin shim over the one role truth table (Stage 3): safebox gates on the exact "safebox"
    # spelling, so it uses the bare (no-alias) view.
    return environment.HostRole.bare()


def _local_authority_enabled() -> bool:
    return _normalized_host_role() == _SAFEBOX_HOST_ROLE


def _authority_mode() -> str:
    if _local_authority_enabled():
        return "local"
    if _remote_enabled():
        return "remote"
    role = _normalized_host_role() or "<unset>"
    raise SafeboxAuthorityUnavailable(
        "Safebox authority is unavailable for "
        f"{_HOST_ROLE_ENV}={role}; configure {_SAFEBOX_REMOTE_URL_ENV} "
        f"or run on the dedicated Safebox host with {_HOST_ROLE_ENV}={_SAFEBOX_HOST_ROLE}"
    )


def _use_remote_authority() -> bool:
    return _authority_mode() == "remote"


_OPERATOR_AUTH_EXACT_PATHS = frozenset(
    {
        "/v1/billing/accounts/open",
        "/v1/billing/balances",
        "/v1/billing/refund",
        "/v1/billing/reserve",
        "/v1/billing/settle",
        "/v1/billing/starter-allowance",
        "/v1/billing/operator-subscription/sync",
        "/v1/cloudflare/product-edge-route",
        "/v1/custody/accounts/open",
        "/v1/vercel/domain/delete",
    }
)
_OPERATOR_AUTH_PATH_PREFIXES = (
    "/v1/analytics/",
    "/v1/auth0/",
    "/v1/creative/",
    "/v1/creative-credits/",
    "/v1/gsc/",
    "/v1/openmeter/",
    "/v1/operator/",
    "/v1/postmark/",
    "/v1/providers/composio/",
    "/v1/providers/meta/",
    "/v1/store/",
    "/v1/storage/",
    "/v1/user-api-keys/",
)


def _remote_path_requires_operator_authority(path: str) -> bool:
    route = "/" + str(path or "").strip().lstrip("/")
    return route in _OPERATOR_AUTH_EXACT_PATHS or route.startswith(_OPERATOR_AUTH_PATH_PREFIXES)


def _remote_headers(*, with_json: bool = False, operator_authority: bool = False) -> dict[str, str]:
    headers: dict[str, str] = {"Accept": "application/json"}
    token = str(os.environ.get(_SAFEBOX_REMOTE_TOKEN_ENV) or "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if operator_authority:
        operator_token = str(os.environ.get(_SAFEBOX_OPERATOR_TOKEN_ENV) or "").strip()
        if not operator_token:
            raise SafeboxAuthorityUnavailable(
                f"operator Safebox route requires {_SAFEBOX_OPERATOR_TOKEN_ENV}; not set on this plane"
            )
        headers["X-Takyon-Operator-Token"] = operator_token
    if with_json:
        headers["Content-Type"] = "application/json"
    return headers


# Idempotent READ paths may retry on a TRANSPORT failure (tunnel restart, uvicorn worker
# recycle, dropped keepalive) — re-reading is always safe. Everything else stays single-shot:
# provider calls and writes must never double-fire on an ambiguous connection error.
# (Observed: a single tunnel blip on /v1/storage/get failed shelfscan0708's whole bootstrap
# attempt, 2026-07-08 — "Remote end closed connection without response".)
_REMOTE_IDEMPOTENT_READ_PATHS = (
    "/v1/storage/get",
    "/v1/storage/list-digests",
    "/v1/storage/list-sizes",
    "/healthz",
)
_REMOTE_READ_RETRY_DELAYS_S = (0.5, 1.5)


def _remote_json(
    method: str,
    path: str,
    payload: dict[str, Any] | None = None,
    *,
    timeout: float = 10.0,
    operator_authority: bool = False,
) -> dict[str, Any]:
    base = _remote_base_url()
    if not base:
        raise RuntimeError("Safebox remote URL is not configured")
    body: bytes | None = None
    headers = _remote_headers(
        with_json=payload is not None,
        operator_authority=operator_authority or _remote_path_requires_operator_authority(path),
    )
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
    retryable = any(path.startswith(prefix) for prefix in _REMOTE_IDEMPOTENT_READ_PATHS)
    transport_attempts = (1 + len(_REMOTE_READ_RETRY_DELAYS_S)) if retryable else 1
    for attempt in range(transport_attempts):
        req = urllib.request.Request(
            f"{base}{path}", data=body, method=method.upper(), headers=headers
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
                return json.loads(raw) if raw.strip() else {}
        except urllib.error.HTTPError as exc:
            # An HTTP status is a real answer from the safebox — never retried.
            raw = exc.read().decode("utf-8", errors="replace")
            detail = raw.strip() or exc.reason
            try:
                parsed = json.loads(raw) if raw.strip() else {}
            except json.JSONDecodeError:
                parsed = {"detail": detail}
            raise RemoteSafeboxError(
                f"Safebox remote {method.upper()} {path} failed: {parsed}",
                status_code=exc.code,
                payload=parsed if isinstance(parsed, dict) else {"detail": detail},
            ) from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            # Transport failure (timeout / connection refused / DNS / connection closed), NOT an
            # HTTP status — HTTPError is a URLError subclass and is handled above. Idempotent reads
            # retry with backoff; everything else fails closed immediately as a 504 so a brokered
            # provider call surfaces a clean upstream error and never falls back to a raw key.
            if attempt + 1 < transport_attempts:
                time.sleep(_REMOTE_READ_RETRY_DELAYS_S[attempt])
                continue
            raise RemoteSafeboxError(
                f"Safebox remote {method.upper()} {path} unreachable: {exc}",
                status_code=504,
                payload={"detail": "safebox_unreachable"},
            ) from exc
    raise RuntimeError("unreachable")  # pragma: no cover


def _managed_secret_command() -> str:
    return str(os.environ.get(_MANAGED_SECRET_COMMAND_ENV) or "").strip()


def _managed_secret_keys() -> set[str]:
    raw = str(os.environ.get(_MANAGED_SECRET_KEYS_ENV) or "").strip()
    keys: set[str] = set()
    for item in raw.replace(",", " ").split():
        name = str(item or "").strip()
        if not name:
            continue
        _validate_env_key(name)
        if is_sensitive_env_key(name):
            keys.add(name)
    return keys


def _managed_secret_applies(name: str) -> bool:
    if not _managed_secret_command():
        return False
    manifest = _managed_secret_keys()
    # Empty manifest means "the command is authoritative for every sensitive key". For gradual
    # cutovers, set TAKYON_MANAGED_SECRET_KEYS so only migrated keys stop falling back to .env.
    return not manifest or name in manifest


def _managed_secret_timeout_seconds() -> float:
    raw = str(os.environ.get(_MANAGED_SECRET_TIMEOUT_ENV) or "").strip()
    if not raw:
        return 8.0
    try:
        return max(1.0, min(60.0, float(raw)))
    except ValueError:
        return 8.0


def _managed_secret_cache_seconds() -> float:
    raw = str(os.environ.get(_MANAGED_SECRET_CACHE_ENV) or "").strip()
    if not raw:
        return _MANAGED_SECRET_CACHE_DEFAULT_SECONDS
    try:
        return max(0.0, min(3600.0, float(raw)))
    except ValueError:
        return _MANAGED_SECRET_CACHE_DEFAULT_SECONDS


def _managed_secret_argv(name: str) -> list[str]:
    try:
        parts = shlex.split(_managed_secret_command())
    except ValueError as exc:
        raise ManagedSecretLookupError("managed secret command is not valid shell-style syntax") from exc
    if not parts:
        return []
    replaced = False
    argv: list[str] = []
    for part in parts:
        if "{key}" in part:
            argv.append(part.replace("{key}", name))
            replaced = True
        else:
            argv.append(part)
    if not replaced:
        argv.append(name)
    return argv


def _read_managed_secret(name: str) -> str:
    ttl = _managed_secret_cache_seconds()
    now = time.monotonic()
    cache_key = (environment.cache_scope(), name)
    if ttl > 0:
        with _MANAGED_SECRET_MUTEX:
            cached = _MANAGED_SECRET_CACHE.get(cache_key)
            if cached and now - cached[0] <= ttl:
                return cached[1]

    argv = _managed_secret_argv(name)
    if not argv:
        return ""
    try:
        completed = subprocess.run(
            argv,
            check=False,
            capture_output=True,
            text=True,
            timeout=_managed_secret_timeout_seconds(),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ManagedSecretLookupError(f"managed secret lookup failed for {name}") from exc
    if completed.returncode != 0:
        raise ManagedSecretLookupError(
            f"managed secret lookup failed for {name} (exit {completed.returncode})"
        )
    value = str(completed.stdout or "").strip()
    if ttl > 0 and value:
        with _MANAGED_SECRET_MUTEX:
            _MANAGED_SECRET_CACHE[cache_key] = (time.monotonic(), value)
    return value


def _public_config_value(name: str) -> str:
    value = os.environ.get(name)
    if value is not None:
        return str(value).strip()
    return str(load_env().get(name) or "").strip()


def stripe_request(
    path: str,
    params: dict[str, Any] | None = None,
    *,
    method: str = "POST",
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    """Run one tightly allowlisted Stripe API operation on the safebox.

    Runtime planes use this instead of fetching ``STRIPE_SECRET_KEY``. The safebox route validates the
    Stripe path/method shape before the local authority code resolves the key, so the shared transport
    token cannot become a generic Stripe API tunnel.
    """
    stripe_path = str(path or "").strip().lstrip("/")
    stripe_method = str(method or "POST").strip().upper()
    if not stripe_path:
        raise ValueError("stripe path is required")
    if _remote_enabled() and not _local_authority_enabled():
        if idempotency_key is not None:
            raise ValueError("Stripe idempotency keys are Safebox-local only")
        operator_authority = stripe_method == "POST" and stripe_path in {"products", "prices"}
        payload = _remote_json(
            "POST",
            "/v1/stripe/request",
            {"path": stripe_path, "params": dict(params or {}), "method": stripe_method},
            timeout=35.0,
            operator_authority=operator_authority,
        )
        return payload if isinstance(payload, dict) else {}
    from . import stripe_util

    return stripe_util.stripe_request(
        stripe_path,
        dict(params or {}),
        method=stripe_method,
        idempotency_key=idempotency_key,
    )


def send_postmark_email(
    *,
    to_email: str,
    subject: str,
    text_body: str,
    html_body: str | None = None,
    message_stream: str | None = None,
) -> dict[str, Any]:
    """Send a transactional email with the Postmark token resolved only on the safebox."""
    body = {
        "to_email": str(to_email or "").strip(),
        "subject": str(subject or ""),
        "text_body": str(text_body or ""),
        "html_body": None if html_body is None else str(html_body),
        "message_stream": str(message_stream or "").strip() or None,
    }
    if _remote_enabled() and not _local_authority_enabled():
        payload = _remote_json("POST", "/v1/postmark/send", body, timeout=35.0)
        return payload if isinstance(payload, dict) else {}

    token = read_env_backed_value("POSTMARK_SERVER_TOKEN")
    from_email = _public_config_value("POSTMARK_FROM_EMAIL")
    if not token or not from_email:
        raise RuntimeError("postmark_unconfigured")
    payload: dict[str, Any] = {
        "From": from_email,
        "To": body["to_email"],
        "Subject": body["subject"],
        "TextBody": body["text_body"],
    }
    if body.get("html_body"):
        payload["HtmlBody"] = body["html_body"]
    stream = body.get("message_stream") or _public_config_value("TAKYON_APP_EMAIL_MESSAGE_STREAM")
    if stream:
        payload["MessageStream"] = stream
    req = urllib.request.Request(
        "https://api.postmarkapp.com/email",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "X-Postmark-Server-Token": token,
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            response_body = json.loads(response.read().decode("utf-8"))
            return {
                "message_id": response_body.get("MessageID"),
                "provider": "postmark",
                "status": "sent",
            }
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:300]
        raise RuntimeError(f"postmark_send_failed:{exc.code}:{detail}") from exc


def ensure_product_edge_route(slug: str) -> dict[str, Any]:
    """Ensure one product host route in Cloudflare using the token only on the safebox."""
    safe_slug = str(slug or "").strip().lower()
    if not safe_slug:
        raise ValueError("slug is required")
    if _remote_enabled() and not _local_authority_enabled():
        payload = _remote_json(
            "POST",
            "/v1/cloudflare/product-edge-route",
            {"slug": safe_slug},
            timeout=35.0,
        )
        return payload if isinstance(payload, dict) else {}

    import re

    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,95}", safe_slug):
        raise ValueError("unsafe slug")
    token = first_env_backed_value("CLOUDFLARE_API_TOKEN")
    if not token:
        return {"slug": safe_slug, "status": "unconfigured", "created": False}
    zone = (_public_config_value("CLOUDFLARE_ZONE_NAME") or "coscale.app").strip()
    worker = (_public_config_value("TAKYON_PRODUCT_EDGE_WORKER") or "takyon-product-worker").strip()
    pattern = f"{safe_slug}.{zone}/*"

    def _cf(method: str, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        req = urllib.request.Request(
            "https://api.cloudflare.com/client/v4" + path,
            data=(json.dumps(payload).encode("utf-8") if payload is not None else None),
            method=method,
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            try:
                return json.loads(exc.read().decode("utf-8", errors="replace"))
            except Exception:
                return {"success": False, "errors": [{"message": str(exc)}]}

    zones = _cf("GET", f"/zones?name={urllib.parse.quote(zone)}").get("result") or []
    zone_id = zones[0].get("id") if zones else None
    if not zone_id:
        return {"slug": safe_slug, "status": "zone_not_found", "created": False}
    existing = {
        route.get("pattern")
        for route in (_cf("GET", f"/zones/{zone_id}/workers/routes").get("result") or [])
        if isinstance(route, dict)
    }
    wildcard_pattern = f"*.{zone}/*"
    if pattern in existing or wildcard_pattern in existing:
        return {"slug": safe_slug, "status": "exists", "created": False, "pattern": pattern}
    created = _cf("POST", f"/zones/{zone_id}/workers/routes", {"pattern": pattern, "script": worker})
    if created.get("success"):
        return {"slug": safe_slug, "status": "created", "created": True, "pattern": pattern}
    errors = [
        str(err.get("message") or err)
        for err in (created.get("errors") or [])
        if isinstance(err, dict)
    ]
    return {"slug": safe_slug, "status": "failed", "created": False, "pattern": pattern, "errors": errors}


def delete_vercel_project_domain(domain: str) -> dict[str, Any]:
    """Delete one Vercel project domain with the Vercel token resolved only on the safebox."""
    name = str(domain or "").strip().lower()
    if not name or "/" in name or "@" in name or len(name) > 253:
        raise ValueError("invalid domain")
    if _remote_enabled() and not _local_authority_enabled():
        payload = _remote_json(
            "POST",
            "/v1/vercel/domain/delete",
            {"domain": name},
            timeout=35.0,
        )
        return payload if isinstance(payload, dict) else {}

    token = read_env_backed_value("VERCEL_TOKEN")
    project = _public_config_value("VERCEL_PROJECT_ID")
    team = _public_config_value("VERCEL_TEAM_ID")
    if not token:
        raise RuntimeError("vercel_token_unconfigured")
    if not project:
        raise RuntimeError("vercel_project_unconfigured")
    query = urllib.parse.urlencode({"teamId": team}) if team else ""
    url = (
        "https://api.vercel.com/v9/projects/"
        f"{urllib.parse.quote(project, safe='')}/domains/{urllib.parse.quote(name, safe='')}"
        f"{'?' + query if query else ''}"
    )
    req = urllib.request.Request(
        url,
        data=json.dumps({"removeRedirects": True}).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        method="DELETE",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            response.read()
            return {
                "domain": name,
                "provider": "vercel",
                "status": "removed",
                "http_status": int(getattr(response, "status", 200) or 200),
                "external_side_effects": "deleted",
            }
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        if exc.code == 404:
            return {
                "domain": name,
                "provider": "vercel",
                "status": "not_found",
                "http_status": 404,
                "external_side_effects": "none",
            }
        raise RuntimeError(f"vercel_domain_delete_failed:{exc.code}:{detail}") from exc


def _storage_backend(provider: str):
    from . import storage

    kind = str(provider or "").strip()
    if kind == "supabase_s3":
        return storage.SupabaseS3StorageBackend()
    if kind == "r2":
        return storage.R2StorageBackend()
    raise ValueError(f"unknown storage provider: {provider!r}")


def storage_put(provider: str, key: str, data: bytes, *, digest: str) -> dict[str, Any]:
    safe_key = str(key or "").strip()
    if len(data) > 256 * 1024 * 1024:
        raise ValueError("storage object too large")
    if _remote_enabled() and not _local_authority_enabled():
        return _remote_json(
            "POST",
            "/v1/storage/put",
            {
                "provider": str(provider or ""),
                "key": safe_key,
                "data_b64": base64.b64encode(data).decode("ascii"),
                "digest": str(digest or ""),
            },
            timeout=120.0,
        )
    _storage_backend(provider).put(safe_key, data, digest=str(digest or ""))
    return {"provider": str(provider or ""), "key": safe_key, "stored": True}


def storage_get(provider: str, key: str) -> bytes:
    safe_key = str(key or "").strip()
    if _remote_enabled() and not _local_authority_enabled():
        payload = _remote_json(
            "POST",
            "/v1/storage/get",
            {"provider": str(provider or ""), "key": safe_key},
            timeout=120.0,
        )
        return base64.b64decode(str(payload.get("data_b64") or ""))
    return _storage_backend(provider).get(safe_key)


def storage_delete(provider: str, key: str) -> dict[str, Any]:
    safe_key = str(key or "").strip()
    if _remote_enabled() and not _local_authority_enabled():
        return _remote_json(
            "POST",
            "/v1/storage/delete",
            {"provider": str(provider or ""), "key": safe_key},
            timeout=35.0,
        )
    _storage_backend(provider).delete(safe_key)
    return {"provider": str(provider or ""), "key": safe_key, "deleted": True}


def _app_media_component(value: str, *, field: str) -> str:
    text = str(value or "").strip()
    if (
        not text
        or len(text) > 128
        or not text[0].isalnum()
        or any(not (ch.isalnum() or ch in {"_", "-"}) for ch in text)
    ):
        raise ValueError(f"unsafe {field}")
    return text


def _app_media_storage_key(business: str, media_id: str) -> str:
    return (
        "media/"
        + _app_media_component(business.lower(), field="business")
        + "/"
        + _app_media_component(media_id, field="media_id")
    )


def app_media_put(
    provider: str,
    *,
    business: str,
    session_token: str,
    media_id: str,
    data: bytes,
    digest: str,
) -> dict[str, Any]:
    """Store product media through the app-session-scoped Safebox route.

    Unlike generic ``/v1/storage/*``, this path never sends operator authority and never lets the
    caller supply an object key. The safebox validates the product session and constructs
    ``media/<business>/<media_id>`` itself.
    """
    business_value = _app_media_component(business.lower(), field="business")
    media_value = _app_media_component(media_id, field="media_id")
    if len(data or b"") > 256 * 1024 * 1024:
        raise ValueError("storage object too large")
    if _remote_enabled() and not _local_authority_enabled():
        return _remote_json(
            "POST",
            "/v1/app-media/put",
            {
                "provider": str(provider or ""),
                "business": business_value,
                "session_token": str(session_token or ""),
                "media_id": media_value,
                "data_b64": base64.b64encode(data or b"").decode("ascii"),
                "digest": str(digest or ""),
            },
            timeout=120.0,
        )
    key = _app_media_storage_key(business_value, media_value)
    _storage_backend(provider).put(key, data or b"", digest=str(digest or ""))
    return {"provider": str(provider or ""), "business": business_value, "media_id": media_value, "stored": True}


def app_media_get(
    provider: str,
    *,
    business: str,
    session_token: str,
    media_id: str,
) -> bytes:
    business_value = _app_media_component(business.lower(), field="business")
    media_value = _app_media_component(media_id, field="media_id")
    if _remote_enabled() and not _local_authority_enabled():
        payload = _remote_json(
            "POST",
            "/v1/app-media/get",
            {
                "provider": str(provider or ""),
                "business": business_value,
                "session_token": str(session_token or ""),
                "media_id": media_value,
            },
            timeout=120.0,
        )
        return base64.b64decode(str(payload.get("data_b64") or ""))
    return _storage_backend(provider).get(_app_media_storage_key(business_value, media_value))


def app_media_delete(
    provider: str,
    *,
    business: str,
    session_token: str,
    media_id: str,
) -> dict[str, Any]:
    business_value = _app_media_component(business.lower(), field="business")
    media_value = _app_media_component(media_id, field="media_id")
    if _remote_enabled() and not _local_authority_enabled():
        return _remote_json(
            "POST",
            "/v1/app-media/delete",
            {
                "provider": str(provider or ""),
                "business": business_value,
                "session_token": str(session_token or ""),
                "media_id": media_value,
            },
            timeout=35.0,
        )
    _storage_backend(provider).delete(_app_media_storage_key(business_value, media_value))
    return {"provider": str(provider or ""), "business": business_value, "media_id": media_value, "deleted": True}


def storage_list_digests(provider: str, prefix: str) -> dict[str, str]:
    if _remote_enabled() and not _local_authority_enabled():
        payload = _remote_json(
            "POST",
            "/v1/storage/list-digests",
            {"provider": str(provider or ""), "prefix": str(prefix or "")},
            timeout=120.0,
        )
        digests = payload.get("digests")
        return {str(k): str(v) for k, v in digests.items()} if isinstance(digests, dict) else {}
    return _storage_backend(provider).list_digests(str(prefix or ""))


def storage_list_object_sizes(provider: str, prefix: str) -> dict[str, int]:
    if _remote_enabled() and not _local_authority_enabled():
        payload = _remote_json(
            "POST",
            "/v1/storage/list-sizes",
            {"provider": str(provider or ""), "prefix": str(prefix or "")},
            timeout=120.0,
        )
        sizes = payload.get("sizes")
        return {str(k): int(v or 0) for k, v in sizes.items()} if isinstance(sizes, dict) else {}
    return _storage_backend(provider).list_object_sizes(str(prefix or ""))


# ── Provider broker client (STEP C cutover) ─────────────────────────────────────────────────────
# Runtime planes (operator / sub-user) call the safebox BROKER instead of fetching a raw provider key
# over /v1/env/*. The key never leaves the safebox: the broker verifies the capability scope, meters
# the usage ledger, resolves the key locally, calls the provider, and returns a KEY-FREE result. A
# transitional flag (TAKYON_PROVIDER_BROKER, default off) lets the cutover deploy dormant and flip
# per-plane; it is removed together with the /v1/env provider-key egress at the cleanup step.
_PROVIDER_BROKER_FLAG_ENV = "TAKYON_PROVIDER_BROKER"
_PROVIDER_BROKER_PATHS = {
    ("anthropic", "messages"): "/v1/providers/anthropic/messages",
    ("openai", "messages"): "/v1/providers/openai/messages",
    ("tavily", "search"): "/v1/providers/tavily/search",
    ("gemini", "image"): "/v1/providers/gemini/image",
    ("postmark", "send"): "/v1/providers/postmark/send",
}
# Provider calls (Anthropic / Gemini) routinely exceed the 10s env-read timeout; give the broker round
# trip room for the upstream provider latency plus the reserve/settle.
_PROVIDER_BROKER_TIMEOUT_S = 180.0


def provider_broker_enabled() -> bool:
    """True when this (runtime) plane should route paid provider calls through the safebox broker
    rather than resolving a raw key: the transitional flag is on AND a remote safebox is configured AND
    this is not the safebox host itself. On the safebox host / local dev (local authority) this is
    False — that host IS the authority and resolves the key locally."""
    flag = str(os.environ.get(_PROVIDER_BROKER_FLAG_ENV) or "").strip().lower() in {"1", "true", "yes", "on"}
    return flag and _remote_enabled() and not _local_authority_enabled()


def broker_provider_call(
    provider: str,
    op: str,
    payload: dict[str, Any],
    *,
    estimate_microusd: int,
    business: str | None = None,
    action: str | None = None,
    session_token: str | None = None,
    token: str | None = None,
    timeout: float = _PROVIDER_BROKER_TIMEOUT_S,
) -> dict[str, Any]:
    """POST a paid provider call to the safebox BROKER and return its KEY-FREE result.

    Runtime-plane client for ``/v1/providers/*``. The provider KEY never reaches this process: the
    safebox verifies the capability scope, reserves+settles the usage ledger ITSELF, resolves the key
    locally, calls the provider, and returns only the provider's key-free response. Reuses the existing
    ``TAKYON_SAFEBOX_TOKEN`` bearer via ``_remote_json``. Fails closed (``RemoteSafeboxError``) — it
    never falls back to a raw key. The caller MUST NOT also reserve/settle usage: the broker is the one
    money gate, so a client that also meters would double-charge.

    Identity is either a pre-minted ``token`` (operator plane, via ``/v1/token/mint``) or the inline
    ``session_token`` + ``business`` + ``action`` shape (product sub-user); the safebox mints-then-brokers
    in one call for the latter. ``estimate_microusd`` is the client floor and, for inline mint, the
    capability ceiling — compute it from ``agent/usage_pricing`` (a too-small value is refused at the
    ceiling, never silently raised)."""
    path = _PROVIDER_BROKER_PATHS.get((str(provider), str(op)))
    if path is None:
        raise ValueError(f"no safebox broker route for provider={provider!r} op={op!r}")
    if not _remote_enabled():
        # Defensive: callers gate on provider_broker_enabled() first; the broker client is remote-only
        # and must never quietly fall back to a local raw key.
        raise SafeboxAuthorityUnavailable(
            f"provider broker requires {_SAFEBOX_REMOTE_URL_ENV}; not set on this plane"
        )
    body: dict[str, Any] = {
        "payload": dict(payload or {}),
        "estimate_microusd": int(estimate_microusd),
    }
    if token:
        body["token"] = str(token)
    if session_token:
        body["session_token"] = str(session_token)
    if business:
        body["business"] = str(business)
    if action:
        body["action"] = str(action)
    return _remote_json("POST", path, body, timeout=timeout)


def egress_call(
    *,
    business: str,
    session_token: str,
    connection_slug: str,
    method: str,
    path: str,
    query: dict[str, Any] | None = None,
    headers: dict[str, Any] | None = None,
    body: Any = None,
    timeout: float = _PROVIDER_BROKER_TIMEOUT_S,
) -> dict[str, Any]:
    """Runtime-plane client for the credentialed-egress rail (delta 6). POSTs to the safebox
    ``/v1/egress`` which resolves the operator-approved connection, unseals + attaches the credential
    server-side, meters the usage ledger, and returns a KEY-FREE response. The provider credential
    never reaches this process; identity is the product ``session_token`` (the safebox derives the
    authoritative {business, app_user} scope). Fails closed (``SafeboxAuthorityUnavailable``) —
    never a raw-key fallback."""
    if not _remote_enabled():
        raise SafeboxAuthorityUnavailable(
            f"egress requires {_SAFEBOX_REMOTE_URL_ENV}; not set on this plane"
        )
    payload: dict[str, Any] = {
        "business": str(business),
        "session_token": str(session_token),
        "connection_slug": str(connection_slug),
        "method": str(method or "GET"),
        "path": str(path or "/"),
    }
    if query is not None:
        payload["query"] = dict(query)
    if headers is not None:
        payload["headers"] = dict(headers)
    if body is not None:
        payload["body"] = body
    return _remote_json("POST", "/v1/egress", payload, timeout=timeout)


def deposit_connection_secret(
    *, business: str, connection_slug: str, secret: str, timeout: float = 30.0
) -> dict[str, Any]:
    """Operator-plane client: deposit a plaintext credential for an APPROVED provider connection.
    Requires the operator token (operator_authority=True) — the secret is sealed server-side and
    only the fingerprint is returned. Never call from the business/subuser runtime."""
    return _remote_json(
        "POST",
        "/v1/connections/deposit",
        {"business": str(business), "connection_slug": str(connection_slug), "secret": str(secret)},
        timeout=timeout,
        operator_authority=True,
    )


def rebind_connection_secret(
    *, business: str, connection_slug: str, timeout: float = 30.0
) -> dict[str, Any]:
    """Operator-plane client for plaintext-free exact-scope credential reactivation.

    The Safebox refuses unless the connection points to an approved, unexpired approval whose
    payload is the exact current canonical scope and its existing sealed credential verifies.
    """
    return _remote_json(
        "POST",
        "/v1/connections/rebind",
        {"business": str(business), "connection_slug": str(connection_slug)},
        timeout=timeout,
        operator_authority=True,
    )


# ── Operator/platform provider proxy client ─────────────────────────────────────────────────────
# Operator/platform/worker counterpart to ``broker_provider_call``: instead of the metered, capability
# -gated business broker (/v1/providers/*), these helpers talk to the TRUSTED operator/platform PROXY
# (/v1/proxy/*) so this plane can call paid providers WITHOUT ever holding a raw key. The proxy resolves
# the real key LOCALLY on the safebox and forwards; the key never reaches this process. Fail-closed:
# both helpers REQUIRE a configured remote safebox and never fall back to a raw key.
_PROVIDER_PROXY_TIMEOUT_S = 180.0


def provider_proxy_base_url() -> str:
    """The safebox remote base URL for operator/platform provider-proxy use (e.g. as
    ``ANTHROPIC_BASE_URL`` so the stock Anthropic SDK streams through ``/v1/messages``). Returns "" when
    no remote safebox is configured — callers MUST treat "" as "proxy unavailable", never fall back to a
    raw key/base URL."""
    return _remote_base_url()


# Operator session-token mint default ceiling (per-CALL cost cap the safebox proxy enforces on every
# metered call under the minted token). 2 USD mirrors the operator-turn / coding-worker per-run budget;
# the safebox additionally clamps the TTL, so a leaked token still expires within the hard bound.
# Env-overridable: the proxy estimate bills input + max_tokens at the model's FULL price, so a
# top-tier model lane (claude-fable-5) needs a higher per-call ceiling for large CEO turns.
_OPERATOR_SESSION_DEFAULT_MAX_COST_MICROUSD = int(
    float(os.environ.get("TAKYON_OPERATOR_SESSION_MAX_COST_MICROUSD") or 0) or 2_000_000
)


def mint_operator_session_token(
    business: str | None,
    operator_user_id: str,
    *,
    session_token: str | None = None,
    max_cost_microusd: int = _OPERATOR_SESSION_DEFAULT_MAX_COST_MICROUSD,
    ttl_seconds: int | None = None,
) -> str:
    """Mint a SESSION-scoped operator capability (audience ``operator.session``) for one CEO/coding-worker
    run and return the token.

    The operator plane presents this token as ``ANTHROPIC_API_KEY`` (with ``ANTHROPIC_BASE_URL`` = the
    safebox ROOT) on every Anthropic/Tavily proxy call. The safebox validates that ``operator_user_id``
    OWNS ``business`` (boundary 1 via ``authorize_operator_call``), binds the per-CALL cost ceiling, and
    issues a REUSABLE, TTL-bounded capability — so the operator host never holds the raw provider key and
    cannot forge or widen scope.

    Business-scoped runs prove boundary 1 through business ownership. Root-scope operator runs (before a
    business exists, e.g. ``/create``) may present a verified dashboard Auth0 session; when they do not,
    the safebox may fall back only to an ACTIVE Takyon user on the operator-only rail.

    Uses the same internal-token transport (``_remote_json`` -> ``/v1/operator/session-token``) as the
    other broker clients. Fails CLOSED: raises ``RemoteSafeboxError`` when the safebox is unreachable,
    refuses the mint (e.g. the operator does not own the business / the dashboard session does not match
    the requested operator), or returns no token — it NEVER falls back to a raw key. The caller MUST
    treat any exception as "no key-free auth" and refuse the run."""
    slug = str(business or "").strip()
    owner = str(operator_user_id or "").strip()
    session = str(session_token or "").strip()
    if not owner:
        raise RemoteSafeboxError(
            "operator session token requires an owner operator_user_id",
            status_code=400,
            payload={"detail": "missing_identity"},
        )
    if not _remote_enabled():
        raise SafeboxAuthorityUnavailable(
            f"operator session token requires {_SAFEBOX_REMOTE_URL_ENV}; not set on this plane"
        )
    body: dict[str, Any] = {
        "business": slug,
        "operator_user_id": owner,
        "max_cost_microusd": int(max_cost_microusd),
    }
    if session:
        body["session_token"] = session
    if ttl_seconds is not None:
        body["ttl_seconds"] = int(ttl_seconds)
    result = _remote_json("POST", "/v1/operator/session-token", body, timeout=10.0)
    token = str((result or {}).get("token") or "").strip() if isinstance(result, dict) else ""
    if not token:
        raise RemoteSafeboxError(
            "Safebox /v1/operator/session-token returned no token",
            status_code=502,
            payload={"detail": "no_session_token"},
        )
    return token


def proxy_request(
    provider: str,
    path: str,
    payload: dict[str, Any],
    *,
    token: str | None = None,
    stream: bool = False,
    timeout: float = _PROVIDER_PROXY_TIMEOUT_S,
):
    """Call the operator/platform provider PROXY at ``/v1/proxy/<provider>/<path>`` and return the
    KEY-FREE result.

    Non-streaming: POSTs JSON with the caller-bound operator capability as ``x-api-key`` and returns the
    parsed JSON dict. Streaming (``stream=True``): yields raw response bytes (the verbatim SSE stream)
    so a caller can re-emit the provider event stream.

    The provider KEY never reaches this process: the safebox resolves it locally and forwards. The shared
    ``TAKYON_SAFEBOX_TOKEN`` is only transport reachability; a signed ``operator.session`` or per-action
    capability is required before any request opens. Fails closed (``RemoteSafeboxError`` /
    ``SafeboxAuthorityUnavailable``) — it NEVER falls back to a raw key.
    """
    prov = str(provider or "").strip().strip("/")
    sub = str(path or "").strip().strip("/")
    capability = str(token or "").strip()
    if not prov:
        raise ValueError("provider is required")
    if not capability:
        raise SafeboxAuthorityUnavailable("operator provider proxy requires a signed operator capability")
    route = f"/v1/proxy/{prov}" + (f"/{sub}" if sub else "")
    base = _remote_base_url()
    if not base:
        # The proxy is remote-only and must never quietly fall back to a local raw key.
        raise SafeboxAuthorityUnavailable(
            f"provider proxy requires {_SAFEBOX_REMOTE_URL_ENV}; not set on this plane"
        )
    if not stream:
        headers = _remote_headers(with_json=True)
        headers["x-api-key"] = capability
        body = json.dumps(dict(payload or {})).encode("utf-8")
        req = urllib.request.Request(base + route, data=body, method="POST", headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
                return json.loads(raw) if raw.strip() else {}
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            detail = raw.strip() or exc.reason
            try:
                parsed = json.loads(raw) if raw.strip() else {}
            except json.JSONDecodeError:
                parsed = {"detail": detail}
            raise RemoteSafeboxError(
                f"Safebox proxy POST {route} failed: {parsed}",
                status_code=exc.code,
                payload=parsed if isinstance(parsed, dict) else {"detail": detail},
            ) from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise RemoteSafeboxError(
                f"Safebox proxy POST {route} unreachable: {exc}",
                status_code=504,
                payload={"detail": "safebox_unreachable"},
            ) from exc
    return _proxy_stream_bytes(base + route, dict(payload or {}), token=capability, timeout=timeout)


def _proxy_stream_bytes(url: str, payload: dict[str, Any], *, token: str, timeout: float):
    """Yield the verbatim response bytes from a streaming proxy POST (e.g. the Anthropic SSE stream),
    using a caller-bound operator capability. Fails closed as ``RemoteSafeboxError`` on an HTTP error
    status or a transport failure — never falls back to a raw key."""
    headers = _remote_headers(with_json=True)
    headers["x-api-key"] = str(token or "").strip()
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=body, method="POST", headers=headers)
    try:
        resp = urllib.request.urlopen(req, timeout=timeout)
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            parsed = json.loads(raw) if raw.strip() else {}
        except json.JSONDecodeError:
            parsed = {"detail": raw.strip() or exc.reason}
        raise RemoteSafeboxError(
            f"Safebox proxy stream POST {url} failed: {parsed}",
            status_code=exc.code,
            payload=parsed if isinstance(parsed, dict) else {"detail": raw.strip()},
        ) from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise RemoteSafeboxError(
            f"Safebox proxy stream POST {url} unreachable: {exc}",
            status_code=504,
            payload={"detail": "safebox_unreachable"},
        ) from exc
    try:
        while True:
            chunk = resp.read(8192)
            if not chunk:
                break
            yield chunk
    finally:
        resp.close()


def _remote_error_detail(exc: RemoteSafeboxError) -> dict[str, Any]:
    detail = exc.payload.get("detail")
    if isinstance(detail, dict):
        return detail
    if isinstance(detail, str) and detail.strip():
        return {"error": detail.strip()}
    return exc.payload if isinstance(exc.payload, dict) else {}


def _creative_credit_backend():
    from . import business_credits

    return business_credits


@contextmanager
def _creative_credit_conn(conn=None):
    if conn is not None:
        yield conn
        return
    from .runtime_app import assert_takyon_pg_role, resolve_database_url
    import psycopg

    raw_conn = psycopg.connect(
        resolve_database_url(plane="safebox"),
        autocommit=True,
        prepare_threshold=None,
    )
    try:
        assert_takyon_pg_role(raw_conn, "safebox")
        yield raw_conn
    finally:
        raw_conn.close()


def _balances_from_payload(payload: dict[str, Any], *, business_slug: str) -> CreativeCreditBalances:
    return CreativeCreditBalances(
        business_slug=str(payload.get("business_slug") or business_slug),
        balance_credits=int(payload.get("balance_credits") or 0),
        reserved_credits=int(payload.get("reserved_credits") or 0),
    )


def _reservation_from_payload(
    payload: dict[str, Any],
    *,
    reservation_key: str,
) -> CreativeCreditReservation:
    return CreativeCreditReservation(
        key=str(payload.get("key") or payload.get("reservation_key") or reservation_key),
        reserved_credits=int(payload.get("reserved_credits") or 0),
    )


def _operator_subscription_state_payload(state: Any) -> dict[str, Any]:
    if isinstance(state, dict):
        return dict(state)
    keys = (
        "user_id",
        "customer_id",
        "subscription_id",
        "subscription_status",
        "plan_name",
        "weekly_allowance_cents",
        "allowance_period_start",
        "allowance_resets_at",
        "synced",
    )
    return {key: getattr(state, key, None) for key in keys}


def _starter_allowance_cents() -> int:
    raw = str(os.environ.get("TAKYON_STARTER_ALLOWANCE_CENTS") or "").strip()
    if not raw:
        return 100
    try:
        return max(0, int(raw))
    except ValueError:
        return 100


def business_bootstrap_free_credits() -> int:
    """Fixed creative-credit starter pack for a newly paid-for business bootstrap.

    This is intentionally a safebox-side policy constant, not a caller-supplied amount: a runtime
    plane may ask for the bootstrap starter pack, but it cannot choose how many credits to mint.
    """
    raw = str(os.environ.get("TAKYON_BUSINESS_BOOTSTRAP_FREE_CREDITS") or "").strip()
    if not raw:
        return 3
    try:
        return max(0, int(raw))
    except ValueError:
        return 3


def _safebox_key_slug(value: Any, limit: int = 48) -> str:
    raw = str(value or "").strip().lower()
    chars: list[str] = []
    previous_dash = False
    for char in raw:
        if char.isalnum() or char == "_":
            chars.append(char)
            previous_dash = False
        elif char == "-" or char.isspace() or char in ":/.":
            if not previous_dash:
                chars.append("-")
                previous_dash = True
        elif not previous_dash:
            chars.append("-")
            previous_dash = True
    slug = "".join(chars).strip("-_")[:limit].strip("-_")
    return slug or "part"


def _safebox_idempotency_key(prefix: str, *parts: Any, max_length: int = 180) -> str:
    raw_parts = [str(part) for part in parts if part is not None and str(part) != ""]
    raw = json.dumps([prefix, *raw_parts], ensure_ascii=False, separators=(",", ":"))
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
    human_parts = [_safebox_key_slug(prefix), *(_safebox_key_slug(part) for part in raw_parts)]
    human = ":".join(part for part in human_parts if part).strip(":") or "takyon"
    suffix = f":{digest}"
    if len(human) + len(suffix) > max_length:
        human = human[: max(1, max_length - len(suffix))].rstrip(":-")
    return f"{human}{suffix}"


def _business_create_charge_reservation_key(business_slug: str) -> str:
    # Mirrors the historical plugins.takyon.cli._operator_create_balance_preflight reservation key.
    # The paid-create-charge verification was removed with the operator create ungate; this key is
    # now retained only for grant-metadata traceability (create_charge_reservation_key).
    return _safebox_idempotency_key("operator-create-charge", str(business_slug or "").strip(), "3")


def _local_open_billing_account(conn, user_id: str, *, allowance_included_cents: int = 0) -> None:
    with _creative_credit_conn(conn) as billing_conn:
        billing_conn.execute(
            "select safebox_billing_open_account(%s, %s)",
            (user_id, int(allowance_included_cents or 0)),
        )


def _local_grant_allowance(
    conn,
    user_id: str,
    included_cents: int,
    idempotency_key: str,
    *,
    period_start=None,
    resets_at=None,
) -> int:
    from . import billing

    with _creative_credit_conn(conn) as billing_conn:
        row = billing_conn.execute(
            "select * from safebox_billing_grant_allowance(%s, %s, %s, %s, %s)",
            (user_id, int(included_cents), str(idempotency_key or ""), period_start, resets_at),
        ).fetchone()
    billing._raise_for_billing_refusal(row, user_id=user_id)
    return int(billing._cell(row, 4))


def _starter_allowance_idempotency_key(subject: str) -> str:
    cleaned = str(subject or "").strip()
    if not cleaned:
        return ""
    return "starter-allowance:" + hashlib.sha256(cleaned.encode("utf-8")).hexdigest()


def _local_grant_starter_allowance(conn, user_id: str, *, idempotency_subject: str | None = None) -> int:
    included_cents = _starter_allowance_cents()
    if included_cents <= 0:
        return 0
    idempotency_key = _starter_allowance_idempotency_key(
        idempotency_subject or f"user:{user_id}"
    )
    with _creative_credit_conn(conn) as billing_conn:
        _local_open_billing_account(billing_conn, user_id)
        with billing_conn.transaction():
            acct = billing_conn.execute(
                "select allowance_included_cents, allowance_used_cents "
                "from billing_accounts where user_id = %s for update",
                (user_id,),
            ).fetchone()
            if acct is None:
                raise RuntimeError(f"billing account missing for user {user_id}")
            included = int(acct[0] or 0)
            used = int(acct[1] or 0)
            if included > 0 or used > 0:
                return included
            existing_entry = billing_conn.execute(
                "select 1 from billing_entries where user_id = %s limit 1",
                (user_id,),
            ).fetchone()
            if existing_entry is not None:
                return included
        return _local_grant_allowance(
            billing_conn,
            user_id,
            included_cents,
            idempotency_key,
        )


def _local_open_custody_account(conn, user_id: str, *, currency: str = "usd") -> None:
    with _creative_credit_conn(conn) as custody_conn:
        custody_conn.execute(
            "select safebox_custody_open_account(%s, %s)",
            (user_id, str(currency or "usd")),
        )


def _local_accrue_custody(
    conn,
    user_id: str,
    business_slug: str,
    gross_cents: int,
    idempotency_key: str,
    *,
    stripe_ref: str | None = None,
    fee_bps: int | None = None,
    withheld_cents: int = 0,
    metadata: dict | None = None,
) -> int:
    from . import custody

    with _creative_credit_conn(conn) as custody_conn:
        return custody.accrue(
            custody_conn,
            user_id,
            business_slug,
            gross_cents,
            idempotency_key,
            stripe_ref=stripe_ref,
            fee_bps=fee_bps,
            withheld_cents=withheld_cents,
            metadata=metadata,
        )


def _local_payout_custody(
    conn,
    user_id: str,
    amount_cents: int,
    idempotency_key: str,
    *,
    stripe_ref: str | None = None,
) -> int:
    from . import custody

    with _creative_credit_conn(conn) as custody_conn:
        return custody.payout(
            custody_conn,
            user_id,
            amount_cents,
            idempotency_key,
            stripe_ref=stripe_ref,
        )


def _local_clawback_custody(
    conn,
    user_id: str,
    business_slug: str,
    amount_cents: int,
    idempotency_key: str,
    *,
    stripe_ref: str | None = None,
    metadata: dict | None = None,
) -> dict[str, int | bool]:
    from . import custody

    with _creative_credit_conn(conn) as custody_conn:
        return custody.clawback(
            custody_conn,
            user_id,
            business_slug,
            amount_cents,
            idempotency_key,
            stripe_ref=stripe_ref,
            metadata=metadata,
        )


def _local_release_custody_clawback(
    conn,
    user_id: str,
    business_slug: str,
    clawback_idempotency_key: str,
    release_idempotency_key: str,
    *,
    stripe_ref: str | None = None,
    metadata: dict | None = None,
) -> dict[str, int | bool]:
    from . import custody

    with _creative_credit_conn(conn) as custody_conn:
        return custody.release_clawback(
            custody_conn,
            user_id,
            business_slug,
            clawback_idempotency_key,
            release_idempotency_key,
            stripe_ref=stripe_ref,
            metadata=metadata,
        )


def _local_open_business_credit_account(conn, business_slug: str) -> None:
    backend = _creative_credit_backend()
    with _creative_credit_conn(conn) as credit_conn:
        backend.open_business_credit_account(credit_conn, business_slug)


def _local_get_business_credit_balances(conn, business_slug: str) -> CreativeCreditBalances:
    backend = _creative_credit_backend()
    with _creative_credit_conn(conn) as credit_conn:
        backend.open_business_credit_account(credit_conn, business_slug)
        return backend.get_business_credit_balances(credit_conn, business_slug)


def _local_grant_credits(
    conn,
    business_slug: str,
    credits: int,
    idempotency_key: str,
    *,
    metadata: dict | None = None,
    stripe_ref: str | None = None,
) -> CreativeCreditBalances:
    backend = _creative_credit_backend()
    with _creative_credit_conn(conn) as credit_conn:
        backend.open_business_credit_account(credit_conn, business_slug)
        return backend.grant_credits(
            credit_conn,
            business_slug,
            credits,
            idempotency_key,
            metadata=metadata,
            stripe_ref=stripe_ref,
        )


def _local_grant_business_bootstrap_credits(
    conn,
    business_slug: str,
    operator_user_id: str,
) -> CreativeCreditBalances:
    slug = str(business_slug or "").strip()
    user_ref = str(operator_user_id or "").strip()
    if not slug:
        raise ValueError("missing business_slug")
    if not user_ref:
        raise ValueError("missing operator_user_id")
    credits = business_bootstrap_free_credits()
    if credits <= 0:
        return _local_get_business_credit_balances(conn, slug)
    reservation_key = _business_create_charge_reservation_key(slug)
    with _creative_credit_conn(conn) as credit_conn:
        row = credit_conn.execute(
            "select owner_user_id from businesses where slug = %s",
            (slug,),
        ).fetchone()
        if row is None:
            raise LookupError("business_not_found")
        owner_user_id = str(row[0] if not isinstance(row, dict) else row.get("owner_user_id") or "").strip()
        if owner_user_id != user_ref:
            raise PermissionError("business_bootstrap_credit_owner_mismatch")
        # Operator-plane dogfooding: the bootstrap starter seed (logo + X) is granted to the
        # verified business owner UNCONDITIONALLY. It previously required a settled operator create
        # charge (the 3% plan decrement), but operator company creation is now ungated from the plan
        # (see cli._operator_create_balance_preflight), so the seed no longer depends on a paid
        # create. Owner verification (above) and slug idempotency (the seed key below) still apply,
        # and the subuser/product credit rails are unaffected. To re-couple the seed to a paid
        # create, restore the settled-charge check here (git history) alongside the create gate.
        return _local_grant_credits(
            credit_conn,
            slug,
            credits,
            f"{slug}-bootstrap-free-seed",
            metadata={
                "reason": "bootstrap free starter (X+logo)",
                "operator_user_id": user_ref,
                "create_charge_reservation_key": reservation_key,
                "grant_policy": "business_bootstrap_starter",
            },
        )


def _local_reserve_credits(
    conn,
    business_slug: str,
    credits: int,
    reservation_key: str,
    *,
    metadata: dict | None = None,
) -> CreativeCreditReservation:
    backend = _creative_credit_backend()
    with _creative_credit_conn(conn) as credit_conn:
        backend.open_business_credit_account(credit_conn, business_slug)
        return backend.reserve_credits(
            credit_conn,
            business_slug,
            credits,
            reservation_key,
            metadata=metadata,
        )


def _local_commit_credits(
    conn,
    reservation_key: str,
    *,
    actual_credits: int | None = None,
    metadata: dict | None = None,
) -> CreativeCreditBalances:
    backend = _creative_credit_backend()
    with _creative_credit_conn(conn) as credit_conn:
        return backend.commit_credits(
            credit_conn,
            reservation_key,
            actual_credits=actual_credits,
            metadata=metadata,
        )


def _local_release_credits(
    conn,
    reservation_key: str,
    *,
    metadata: dict | None = None,
) -> CreativeCreditBalances:
    backend = _creative_credit_backend()
    with _creative_credit_conn(conn) as credit_conn:
        return backend.release_credits(
            credit_conn,
            reservation_key,
            metadata=metadata,
        )


def _require_sensitive(key: str) -> str:
    name = str(key or "").strip()
    _validate_env_key(name)
    if not is_sensitive_env_key(name):
        raise KeyError(f"safebox does not expose non-sensitive env key: {name}")
    return name


def _safebox_dir() -> Path:
    return Path(get_takyon_home()).expanduser() / _SAFEBOX_DIRNAME


def _user_api_keys_path() -> Path:
    return _safebox_dir() / _USER_API_KEYS_FILE_NAME


def _utc_now_iso() -> str:
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _empty_user_api_key_state() -> dict[str, Any]:
    return {
        "version": _USER_API_KEYS_STATE_VERSION,
        "user_api_keys": {},
    }


def _load_user_api_key_state() -> dict[str, Any]:
    path = _user_api_keys_path()
    if not path.exists():
        return _empty_user_api_key_state()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:  # pragma: no cover - fail closed on corruption
        raise RuntimeError(f"invalid Safebox user-api-key registry at {path}") from exc
    if not isinstance(raw, dict):
        raise RuntimeError(f"invalid Safebox user-api-key registry at {path}")
    records = raw.get("user_api_keys")
    if records is None:
        raw["user_api_keys"] = {}
    elif not isinstance(records, dict):
        raise RuntimeError(f"invalid Safebox user-api-key registry at {path}")
    raw.setdefault("version", _USER_API_KEYS_STATE_VERSION)
    return raw


def _write_user_api_key_state(state: dict[str, Any]) -> None:
    path = _user_api_keys_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp_path.write_text(
        json.dumps(state, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.chmod(tmp_path, 0o600)
    # Use the shared atomic_replace so a root-run authority write preserves the
    # registry's service-user ownership (same class of bug as the .env flip that
    # 502'd the dashboard); a plain rename would re-own it to the writer.
    from utils import atomic_replace

    real_path = atomic_replace(str(tmp_path), str(path))
    os.chmod(real_path, 0o600)


@contextmanager
def _locked_user_api_key_state(*, write: bool):
    lock_path = _user_api_keys_path().with_suffix(".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with _USER_API_KEYS_MUTEX:
        with lock_path.open("a+", encoding="utf-8") as lock_file:
            if fcntl is not None:
                fcntl.flock(
                    lock_file.fileno(),
                    fcntl.LOCK_EX if write else fcntl.LOCK_SH,
                )
            try:
                state = _load_user_api_key_state()
                yield state
                if write:
                    _write_user_api_key_state(state)
            finally:
                if fcntl is not None:
                    fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def _user_api_key_records(state: dict[str, Any]) -> dict[str, dict[str, Any]]:
    records = state.setdefault("user_api_keys", {})
    if not isinstance(records, dict):
        raise RuntimeError("invalid Safebox user-api-key registry")
    return records  # type: ignore[return-value]


def _normalize_user_api_key_record(record: dict[str, Any]) -> dict[str, str | None]:
    return {
        "id": str(record.get("id") or "").strip(),
        "user_id": str(record.get("user_id") or "").strip(),
        "key_hash": str(record.get("key_hash") or "").strip(),
        "prefix": str(record.get("prefix") or "").strip(),
        "created_at": str(record.get("created_at") or "").strip(),
        "revoked_at": str(record.get("revoked_at") or "").strip() or None,
    }


def register_user_api_key(
    user_id: str,
    raw_key: str,
    *,
    key_id: str,
    created_at: str | None = None,
) -> dict[str, str | None]:
    """Register a top-level ``tk_...`` API key in Safebox."""
    if _use_remote_authority():
        payload = _remote_json(
            "POST",
            "/v1/user-api-keys/register",
            {
                "user_id": str(user_id or "").strip(),
                "raw_key": str(raw_key or ""),
                "key_id": str(key_id or "").strip(),
                "created_at": str(created_at or "").strip() or None,
            },
        )
        record = payload.get("record")
        if not isinstance(record, dict):
            raise RuntimeError("Safebox remote register_user_api_key returned no record")
        return record
    owner = str(user_id or "").strip()
    if not owner:
        raise ValueError("missing user_id")
    key_ref = str(key_id or "").strip()
    if not key_ref:
        raise ValueError("missing key_id")
    if not is_well_formed(raw_key):
        raise ValueError("malformed api key")
    record = {
        "id": key_ref,
        "user_id": owner,
        "key_hash": hash_api_key(raw_key),
        "prefix": key_prefix(raw_key),
        "created_at": str(created_at or _utc_now_iso()),
        "revoked_at": None,
    }
    with _locked_user_api_key_state(write=True) as state:
        records = _user_api_key_records(state)
        for existing in records.values():
            normalized = _normalize_user_api_key_record(existing)
            if (
                normalized["user_id"] == owner
                and normalized["id"] != key_ref
                and normalized["revoked_at"] is None
            ):
                raise ValueError("active user api key already exists in Safebox")
        records[key_ref] = record
    return record


def resolve_user_api_key(raw_key: str) -> dict[str, str | None] | None:
    """Resolve a presented raw ``tk_...`` key against the Safebox registry."""
    if _use_remote_authority():
        payload = _remote_json(
            "POST",
            "/v1/user-api-keys/resolve",
            {"raw_key": str(raw_key or "")},
        )
        record = payload.get("record")
        return record if isinstance(record, dict) else None
    if not is_well_formed(raw_key):
        return None
    presented_hash = hash_api_key(raw_key)
    with _locked_user_api_key_state(write=False) as state:
        for existing in _user_api_key_records(state).values():
            normalized = _normalize_user_api_key_record(existing)
            if normalized["revoked_at"] is not None:
                continue
            if normalized["key_hash"] == presented_hash:
                return normalized
    return None


def revoke_user_api_key(key_id: str, *, revoked_at: str | None = None) -> bool:
    """Revoke one Safebox-owned top-level API key by id."""
    if _use_remote_authority():
        payload = _remote_json(
            "POST",
            "/v1/user-api-keys/revoke",
            {
                "key_id": str(key_id or "").strip(),
                "revoked_at": str(revoked_at or "").strip() or None,
            },
        )
        return bool(payload.get("revoked"))
    key_ref = str(key_id or "").strip()
    if not key_ref:
        return False
    changed = False
    with _locked_user_api_key_state(write=True) as state:
        record = _user_api_key_records(state).get(key_ref)
        if isinstance(record, dict) and not record.get("revoked_at"):
            record["revoked_at"] = str(revoked_at or _utc_now_iso())
            changed = True
    return changed


def revoke_user_api_keys_for_user(
    user_id: str,
    *,
    revoked_at: str | None = None,
) -> list[str]:
    """Revoke every active top-level API key owned by one Takyon user."""
    if _use_remote_authority():
        payload = _remote_json(
            "POST",
            "/v1/user-api-keys/revoke-for-user",
            {
                "user_id": str(user_id or "").strip(),
                "revoked_at": str(revoked_at or "").strip() or None,
            },
        )
        revoked = payload.get("revoked_ids")
        return [str(item) for item in revoked] if isinstance(revoked, list) else []
    owner = str(user_id or "").strip()
    if not owner:
        return []
    changed: list[str] = []
    stamp = str(revoked_at or _utc_now_iso())
    with _locked_user_api_key_state(write=True) as state:
        for key_id, record in _user_api_key_records(state).items():
            if not isinstance(record, dict):
                continue
            normalized = _normalize_user_api_key_record(record)
            if normalized["user_id"] != owner or normalized["revoked_at"] is not None:
                continue
            record["revoked_at"] = stamp
            changed.append(str(key_id))
    return changed


def restore_user_api_keys(key_ids: list[str]) -> None:
    """Undo a staged revoke when an outer transactional caller rolls back."""
    if _use_remote_authority():
        _remote_json("POST", "/v1/user-api-keys/restore", {"key_ids": list(key_ids or [])})
        return
    wanted = {
        str(key_id or "").strip()
        for key_id in key_ids
        if str(key_id or "").strip()
    }
    if not wanted:
        return
    with _locked_user_api_key_state(write=True) as state:
        for key_id, record in _user_api_key_records(state).items():
            if key_id in wanted and isinstance(record, dict):
                record["revoked_at"] = None


def delete_user_api_key(key_id: str) -> bool:
    """Delete one Safebox key record outright for transactional cleanup."""
    if _use_remote_authority():
        payload = _remote_json(
            "DELETE",
            f"/v1/user-api-keys/{urllib.parse.quote(str(key_id or '').strip(), safe='')}",
        )
        return bool(payload.get("deleted"))
    key_ref = str(key_id or "").strip()
    if not key_ref:
        return False
    deleted = False
    with _locked_user_api_key_state(write=True) as state:
        records = _user_api_key_records(state)
        deleted = records.pop(key_ref, None) is not None
    return deleted


def open_business_credit_account(conn, business_slug: str) -> None:
    """Ensure one business creative-credit account exists inside Safebox authority."""
    slug = str(business_slug or "").strip()
    if not slug:
        raise ValueError("missing business_slug")
    if _use_remote_authority():
        _remote_json(
            "POST",
            "/v1/creative-credits/accounts/open",
            {"business_slug": slug},
        )
        return
    _local_open_business_credit_account(conn, slug)


def grant_business_bootstrap_credits(
    conn,
    business_slug: str,
    operator_user_id: str,
) -> CreativeCreditBalances:
    """Grant the fixed create-time starter credits through safebox provisioning policy.

    Remote planes cannot call ``grant_credits`` with arbitrary amounts. This path mints only the
    fixed bootstrap starter pack, and the safebox verifies the business owner before writing the
    grant. (The paid-create-charge requirement was removed with the operator create ungate.)
    """
    slug = str(business_slug or "").strip()
    user_ref = str(operator_user_id or "").strip()
    if not slug:
        raise ValueError("missing business_slug")
    if not user_ref:
        raise ValueError("missing operator_user_id")
    if _use_remote_authority():
        payload = _remote_json(
            "POST",
            "/v1/creative-credits/bootstrap-starter",
            {"business_slug": slug, "operator_user_id": user_ref},
        )
        return _balances_from_payload(payload, business_slug=slug)
    return _local_grant_business_bootstrap_credits(conn, slug, user_ref)


def open_billing_account(conn, user_id: str, *, allowance_included_cents: int = 0) -> None:
    """Open a zero/explicit billing account through safebox authority.

    This route does not mint spend; any non-zero allowance still has to come through a bounded
    starter/subscription/webhook path.
    """
    user_ref = str(user_id or "").strip()
    if not user_ref:
        raise ValueError("missing user_id")
    amount = int(allowance_included_cents or 0)
    if _remote_enabled() and not _local_authority_enabled():
        _remote_json(
            "POST",
            "/v1/billing/accounts/open",
            {"user_id": user_ref, "allowance_included_cents": amount},
        )
        return
    _local_open_billing_account(conn, user_ref, allowance_included_cents=amount)


def _billing_error_detail(exc: RemoteSafeboxError) -> dict[str, Any]:
    detail = exc.payload.get("detail") if isinstance(exc.payload, dict) else None
    return detail if isinstance(detail, dict) else {"code": str(detail or "")}


def billing_reserve(
    conn,
    user_id: str,
    estimate_cents: int,
    reservation_key: str,
    *,
    business_slug: str | None = None,
    job_id: str | None = None,
) -> dict[str, Any]:
    """Reserve operator allowance through the Safebox authority boundary."""
    user_ref = str(user_id or "").strip()
    key = str(reservation_key or "").strip()
    if not user_ref:
        raise ValueError("missing user_id")
    if not key:
        raise ValueError("missing reservation_key")
    amount = max(0, int(estimate_cents or 0))
    if _remote_enabled() and not _local_authority_enabled():
        try:
            return _remote_json(
                "POST",
                "/v1/billing/reserve",
                {
                    "user_id": user_ref,
                    "estimate_cents": amount,
                    "reservation_key": key,
                    "business_slug": business_slug or None,
                    "job_id": job_id or None,
                },
            )
        except RemoteSafeboxError as exc:
            from . import billing

            detail = _billing_error_detail(exc)
            code = str(detail.get("code") or "").strip()
            if exc.status_code == 402 and code == "insufficient_balance":
                raise billing.InsufficientBalance(
                    estimate_cents=int(detail.get("estimate_cents") or amount),
                    allowance_available_cents=int(detail.get("allowance_available_cents") or 0),
                ) from exc
            if exc.status_code == 404 and code == "no_billing_account":
                raise billing.NoBillingAccount(user_ref) from exc
            raise
    from . import billing

    res = billing.reserve(
        conn,
        user_ref,
        amount,
        key,
        business_slug=business_slug or None,
        job_id=job_id or None,
    )
    return {"reservation_key": res.key, "allowance_cents": int(res.allowance_cents)}


def billing_settle(conn, reservation_key: str, actual_cents: int) -> None:
    """Settle an operator billing reservation through Safebox."""
    key = str(reservation_key or "").strip()
    if not key:
        raise ValueError("missing reservation_key")
    actual = max(0, int(actual_cents or 0))
    if _remote_enabled() and not _local_authority_enabled():
        try:
            _remote_json(
                "POST",
                "/v1/billing/settle",
                {"reservation_key": key, "actual_cents": actual},
            )
            return
        except RemoteSafeboxError as exc:
            from . import billing

            detail = _billing_error_detail(exc)
            if exc.status_code == 404 and str(detail.get("code") or "") == "unknown_reservation":
                raise billing.UnknownReservation(key) from exc
            raise
    from . import billing

    billing.settle(conn, key, actual)


def billing_refund(conn, reservation_key: str) -> None:
    """Release an operator billing reservation through Safebox."""
    key = str(reservation_key or "").strip()
    if not key:
        raise ValueError("missing reservation_key")
    if _remote_enabled() and not _local_authority_enabled():
        try:
            _remote_json("POST", "/v1/billing/refund", {"reservation_key": key})
            return
        except RemoteSafeboxError as exc:
            from . import billing

            detail = _billing_error_detail(exc)
            if exc.status_code == 404 and str(detail.get("code") or "") == "unknown_reservation":
                raise billing.UnknownReservation(key) from exc
            raise
    from . import billing

    billing.refund(conn, key)


def billing_balances(conn, user_id: str) -> dict[str, Any]:
    """Read operator billing balances through Safebox."""
    user_ref = str(user_id or "").strip()
    if not user_ref:
        raise ValueError("missing user_id")
    if _remote_enabled() and not _local_authority_enabled():
        try:
            return _remote_json("POST", "/v1/billing/balances", {"user_id": user_ref})
        except RemoteSafeboxError as exc:
            from . import billing

            detail = _billing_error_detail(exc)
            if exc.status_code == 404 and str(detail.get("code") or "") == "no_billing_account":
                raise billing.NoBillingAccount(user_ref) from exc
            raise
    from . import billing

    balances = billing.get_billing_balances(conn, user_ref)
    return {
        "user_id": balances.user_id,
        "allowance_included_cents": int(balances.allowance_included_cents),
        "allowance_used_cents": int(balances.allowance_used_cents),
        "allowance_remaining_cents": int(balances.allowance_remaining_cents),
        "reserved_cents": int(balances.reserved_cents),
        "allowance_period_start": balances.allowance_period_start.isoformat()
        if hasattr(balances.allowance_period_start, "isoformat")
        else balances.allowance_period_start,
        "allowance_resets_at": balances.allowance_resets_at.isoformat()
        if hasattr(balances.allowance_resets_at, "isoformat")
        else balances.allowance_resets_at,
    }


def grant_starter_allowance(conn, user_id: str, *, session_token: str | None = None) -> int:
    """Grant the one-time starter allowance, with replay/balance checks inside the safebox.

    Remote starter grants require a verified Auth0 dashboard session. A caller that only has the shared
    transport token can still open a zero account, but cannot mint allowance for an arbitrary user id.
    """
    user_ref = str(user_id or "").strip()
    if not user_ref:
        raise ValueError("missing user_id")
    if _remote_enabled() and not _local_authority_enabled():
        token = str(session_token or "").strip()
        if not token:
            return 0
        payload = _remote_json(
            "POST",
            "/v1/billing/starter-allowance",
            {"user_id": user_ref, "session_token": token},
        )
        returned_user = str(payload.get("user_id") or "").strip()
        if returned_user and returned_user != user_ref:
            raise RemoteSafeboxError(
                "Safebox starter allowance returned a different user",
                status_code=403,
                payload={"detail": "starter_user_mismatch"},
            )
        return int(payload.get("included_cents") or 0)
    return _local_grant_starter_allowance(conn, user_ref)


def sync_operator_subscription_allowance(user_id: str, *, refresh_live: bool = True) -> dict[str, Any]:
    """Ask the safebox to derive the operator allowance from Stripe/DB state.

    The caller supplies only the account identity and whether live Stripe refresh is allowed; the
    safebox derives any minted allowance from its own Stripe read or a verified webhook event.
    """
    user_ref = str(user_id or "").strip()
    if not user_ref:
        raise ValueError("missing user_id")
    if _remote_enabled() and not _local_authority_enabled():
        payload = _remote_json(
            "POST",
            "/v1/billing/operator-subscription/sync",
            {"user_id": user_ref, "refresh_live": bool(refresh_live)},
            timeout=30.0,
        )
        return payload if isinstance(payload, dict) else {}
    from .control_api import sync_operator_subscription_allowance as _sync

    with _creative_credit_conn(None) as conn:
        return _operator_subscription_state_payload(
            _sync(conn, user_ref, refresh_live=bool(refresh_live))
        )


def process_stripe_billing_webhook(raw_body: str, signature: str) -> dict[str, Any]:
    """Verify and process the flow-A billing webhook on the safebox."""
    body = str(raw_body or "")
    presented = str(signature or "").strip()
    if _remote_enabled() and not _local_authority_enabled():
        try:
            payload = _remote_json(
                "POST",
                "/v1/billing/webhook/process",
                {"raw_body": body, "signature": presented},
                timeout=30.0,
            )
        except RemoteSafeboxError as exc:
            if exc.status_code == 503:
                raise StripeBillingWebhookUnconfigured("billing_webhook_unconfigured") from exc
            if exc.status_code == 400:
                raise StripeBillingWebhookInvalidSignature("invalid_signature") from exc
            raise
        return payload if isinstance(payload, dict) else {}
    event = verify_stripe_billing_webhook(body, presented)
    from .control_api import process_billing_webhook_event

    with _creative_credit_conn(None) as conn:
        return process_billing_webhook_event(conn, event)


def get_operator_payout_state(user_id: str, *, refresh_live: bool = True) -> dict[str, Any]:
    """Read one operator payout state, refreshing Stripe Connect only on the safebox."""
    user_ref = str(user_id or "").strip()
    if not user_ref:
        raise ValueError("missing user_id")
    if _remote_enabled() and not _local_authority_enabled():
        payload = _remote_json(
            "POST",
            "/v1/operator/payouts/state",
            {"user_id": user_ref, "refresh_live": bool(refresh_live)},
            timeout=30.0,
        )
        return payload if isinstance(payload, dict) else {}
    from .control_api import get_operator_payout_state as _get

    with _creative_credit_conn(None) as conn:
        state = _get(conn, user_ref, refresh_live=bool(refresh_live))
    return {
        "user_id": state.user_id,
        "stripe_connect_account_id": state.stripe_connect_account_id,
        "stripe_connect_status": state.stripe_connect_status,
        "payouts_enabled": bool(state.payouts_enabled),
        "details_submitted": bool(state.details_submitted),
        "payout_currency": state.payout_currency,
        "owed_balance_cents": int(state.owed_balance_cents),
        "paid_out_cents": int(state.paid_out_cents),
    }


def create_operator_billing_portal(user_id: str, *, return_url: str) -> dict[str, Any]:
    """Create an operator billing portal session using Stripe only on the safebox."""
    user_ref = str(user_id or "").strip()
    if not user_ref:
        raise ValueError("missing user_id")
    if _remote_enabled() and not _local_authority_enabled():
        payload = _remote_json(
            "POST",
            "/v1/operator/billing/portal",
            {"user_id": user_ref, "return_url": str(return_url or "")},
            timeout=30.0,
        )
        return {
            "url": payload.get("portal_url") or payload.get("url"),
            "customer": payload.get("customer_id") or payload.get("customer"),
        } if isinstance(payload, dict) else {}
    from .control_api import create_operator_billing_portal_session

    with _creative_credit_conn(None) as conn:
        return create_operator_billing_portal_session(conn, user_ref, return_url=return_url)


def create_operator_subscription_checkout(
    user_id: str,
    *,
    plan_id: str,
    success_url: str,
    cancel_url: str,
) -> dict[str, Any]:
    """Create an operator subscription checkout using a safebox-derived customer and Stripe key."""
    user_ref = str(user_id or "").strip()
    plan_ref = str(plan_id or "").strip()
    if not user_ref:
        raise ValueError("missing user_id")
    if not plan_ref:
        raise ValueError("plan_id is required")
    if _remote_enabled() and not _local_authority_enabled():
        payload = _remote_json(
            "POST",
            "/v1/operator/billing/subscription/checkout",
            {
                "user_id": user_ref,
                "plan_id": plan_ref,
                "success_url": str(success_url or ""),
                "cancel_url": str(cancel_url or ""),
            },
            timeout=30.0,
        )
        return payload if isinstance(payload, dict) else {}
    from .control_api import (
        create_operator_subscription_checkout_session,
        ensure_operator_billing_customer,
    )

    with _creative_credit_conn(None) as conn:
        customer = ensure_operator_billing_customer(conn, user_ref)
        session, plan = create_operator_subscription_checkout_session(
            user_ref,
            plan_id=plan_ref,
            success_url=success_url,
            cancel_url=cancel_url,
            customer_id=str(customer.get("id") or "").strip() or None,
        )
    return {
        "checkout_url": session.get("url"),
        "session_id": session.get("id"),
        "plan_id": plan["id"],
        "plan_name": plan["name"],
    }


def create_operator_payout_connect(
    user_id: str,
    *,
    return_url: str,
    refresh_url: str,
) -> dict[str, Any]:
    """Create a Stripe Connect onboarding/login link using Stripe only on the safebox."""
    user_ref = str(user_id or "").strip()
    if not user_ref:
        raise ValueError("missing user_id")
    if _remote_enabled() and not _local_authority_enabled():
        payload = _remote_json(
            "POST",
            "/v1/operator/payouts/connect",
            {
                "user_id": user_ref,
                "return_url": str(return_url or ""),
                "refresh_url": str(refresh_url or ""),
            },
            timeout=30.0,
        )
        return payload if isinstance(payload, dict) else {}
    from .control_api import create_operator_payout_connect_link

    with _creative_credit_conn(None) as conn:
        return create_operator_payout_connect_link(
            conn,
            user_ref,
            return_url=return_url,
            refresh_url=refresh_url,
        )


def open_custody_account(conn, user_id: str, *, currency: str = "usd") -> None:
    """Open a zero custody account through safebox authority."""
    user_ref = str(user_id or "").strip()
    if not user_ref:
        raise ValueError("missing user_id")
    if _remote_enabled() and not _local_authority_enabled():
        _remote_json(
            "POST",
            "/v1/custody/accounts/open",
            {"user_id": user_ref, "currency": str(currency or "usd")},
        )
        return
    _local_open_custody_account(conn, user_ref, currency=currency)


def accrue_custody(
    conn,
    user_id: str,
    business_slug: str,
    gross_cents: int,
    idempotency_key: str,
    *,
    stripe_ref: str | None = None,
    fee_bps: int | None = None,
    withheld_cents: int = 0,
    metadata: dict | None = None,
) -> int:
    """Safebox-local custody accrual.

    Remote runtime planes should not call this with caller-supplied amounts; they process the signed
    app-payment webhook through ``process_stripe_app_webhook`` instead.
    """
    if _remote_enabled() and not _local_authority_enabled():
        raise SafeboxAuthorityUnavailable(
            "custody accrual must be derived from a signed app-payment webhook on the safebox"
        )
    return _local_accrue_custody(
        conn,
        user_id,
        business_slug,
        gross_cents,
        idempotency_key,
        stripe_ref=stripe_ref,
        fee_bps=fee_bps,
        withheld_cents=withheld_cents,
        metadata=metadata,
    )


def payout_custody(
    conn,
    user_id: str,
    amount_cents: int,
    idempotency_key: str,
    *,
    stripe_ref: str | None = None,
) -> int:
    """Safebox-local custody payout primitive; public HTTP access is operator-capability gated."""
    if _remote_enabled() and not _local_authority_enabled():
        raise SafeboxAuthorityUnavailable(
            "custody payout must use the operator-authorized safebox payout route"
        )
    return _local_payout_custody(
        conn,
        user_id,
        amount_cents,
        idempotency_key,
        stripe_ref=stripe_ref,
    )


def clawback_custody(
    conn,
    user_id: str,
    business_slug: str,
    amount_cents: int,
    idempotency_key: str,
    *,
    stripe_ref: str | None = None,
    metadata: dict | None = None,
) -> dict[str, int | bool]:
    """Safebox-local refund/dispute custody clawback primitive."""
    if _remote_enabled() and not _local_authority_enabled():
        raise SafeboxAuthorityUnavailable(
            "custody clawback must be derived from a signed app-payment webhook on the safebox"
        )
    return _local_clawback_custody(
        conn,
        user_id,
        business_slug,
        amount_cents,
        idempotency_key,
        stripe_ref=stripe_ref,
        metadata=metadata,
    )


def release_custody_clawback(
    conn,
    user_id: str,
    business_slug: str,
    clawback_idempotency_key: str,
    release_idempotency_key: str,
    *,
    stripe_ref: str | None = None,
    metadata: dict | None = None,
) -> dict[str, int | bool]:
    """Safebox-only idempotent release for one won Stripe dispute clawback."""
    if _remote_enabled() and not _local_authority_enabled():
        raise SafeboxAuthorityUnavailable(
            "custody clawback release must be derived from a signed dispute webhook on the safebox"
        )
    return _local_release_custody_clawback(
        conn,
        user_id,
        business_slug,
        clawback_idempotency_key,
        release_idempotency_key,
        stripe_ref=stripe_ref,
        metadata=metadata,
    )


def process_stripe_app_webhook(raw_body: str, signature: str) -> dict[str, Any]:
    """Verify and process the flow-B product app webhook on the safebox."""
    body = str(raw_body or "")
    presented = str(signature or "").strip()
    if _remote_enabled() and not _local_authority_enabled():
        try:
            payload = _remote_json(
                "POST",
                "/v1/stripe/app-webhook/process",
                {"raw_body": body, "signature": presented},
                timeout=30.0,
            )
        except RemoteSafeboxError as exc:
            if exc.status_code == 503:
                raise StripeAppWebhookUnconfigured("app_webhook_unconfigured") from exc
            if exc.status_code == 400:
                raise StripeAppWebhookInvalidSignature("invalid_signature") from exc
            raise
        return payload if isinstance(payload, dict) else {}
    event = verify_stripe_app_webhook(body, presented)
    from . import app_payments

    with _creative_credit_conn(None) as conn:
        return app_payments.record_webhook_and_process(conn, event)


def reconcile_app_checkout_session(
    conn,
    *,
    session_id: str,
    expected_business_slug: str | None = None,
    app_user_id: str | None = None,
    customer_email: str | None = None,
) -> dict[str, Any]:
    """Recover one completed product-app Checkout session through Safebox authority.

    This is the non-webhook companion to :func:`process_stripe_app_webhook`: the runtime plane may
    know a pending Stripe Checkout session id, but only the safebox may retrieve the Stripe session and
    turn it into entitlement/revenue/custody writes. A shared bearer token therefore remains
    reachability, not custody authority.
    """
    stripe_session_id = str(session_id or "").strip()
    if not stripe_session_id:
        raise ValueError("session_id is required")
    expected_slug = str(expected_business_slug or "").strip()
    expected_user = str(app_user_id or "").strip()
    expected_email = str(customer_email or "").strip()
    if _use_remote_authority():
        if not expected_slug or (not expected_user and not expected_email):
            raise ValueError("checkout recovery requires expected business and app user/email context")
        try:
            payload = _remote_json(
                "POST",
                "/v1/stripe/app-checkout/reconcile",
                {
                    "session_id": stripe_session_id,
                    "business_slug": expected_slug or None,
                    "app_user_id": expected_user or None,
                    "customer_email": expected_email or None,
                },
                timeout=35.0,
            )
        except RemoteSafeboxError as exc:
            detail = _remote_error_detail(exc)
            message = str(detail.get("error") or detail.get("detail") or str(exc)).strip() or str(exc)
            if exc.status_code == 404:
                raise LookupError(message) from exc
            if exc.status_code == 409:
                raise RuntimeError(message) from exc
            if exc.status_code == 400:
                raise ValueError(message) from exc
            if exc.status_code == 403:
                raise PermissionError(message) from exc
            raise
        return payload if isinstance(payload, dict) else {}

    from . import app_payments, stripe_util

    session = stripe_util.stripe_request(f"checkout/sessions/{stripe_session_id}", {}, method="GET")
    if str(session.get("status") or "").strip().lower() != "complete":
        raise RuntimeError("checkout_session_not_complete")
    if str(session.get("payment_status") or "").strip().lower() not in {"paid", "no_payment_required"}:
        raise RuntimeError("checkout_session_unpaid")
    invoice_value = session.get("invoice")
    invoice_id = (
        str(invoice_value.get("id") or "").strip()
        if isinstance(invoice_value, dict)
        else str(invoice_value or "").strip()
    )
    if invoice_id:
        invoice = stripe_util.stripe_request(f"invoices/{invoice_id}", {}, method="GET")
        if not isinstance(invoice, dict) or str(invoice.get("id") or "") != invoice_id:
            raise RuntimeError("stripe_invoice_payment_evidence_pending")
        rows: list[dict[str, Any]] = []
        starting_after = ""
        for _ in range(100):
            params: dict[str, Any] = {"invoice": invoice_id, "limit": 100}
            if starting_after:
                params["starting_after"] = starting_after
            page = stripe_util.stripe_request("invoice_payments", params, method="GET")
            data = page.get("data") if isinstance(page, dict) else None
            if not isinstance(data, list):
                raise RuntimeError("stripe_invoice_payment_evidence_pending")
            typed = [row for row in data if isinstance(row, dict)]
            rows.extend(typed)
            if not bool(page.get("has_more")):
                break
            cursor = str((typed[-1] if typed else {}).get("id") or "")
            if not cursor or cursor == starting_after:
                raise RuntimeError("stripe_invoice_payment_evidence_pending")
            starting_after = cursor
        else:
            raise RuntimeError("stripe_invoice_payment_evidence_pending")
        invoice["payments"] = {"data": rows, "has_more": False}
        session["_takyon_invoice"] = invoice
    subscription_value = session.get("subscription")
    subscription_id = (
        str(subscription_value.get("id") or "").strip()
        if isinstance(subscription_value, dict)
        else str(subscription_value or "").strip()
    )
    if not subscription_id:
        raise RuntimeError("stripe_subscription_reconcile_pending")
    subscription = stripe_util.stripe_request(
        f"subscriptions/{subscription_id}", {}, method="GET"
    )
    if (
        not isinstance(subscription, dict)
        or str(subscription.get("id") or "") != subscription_id
    ):
        raise RuntimeError("stripe_subscription_reconcile_pending")
    with _creative_credit_conn(conn) as payment_conn:
        with payment_conn.transaction():
            result = app_payments.reconcile_checkout_session(
                payment_conn,
                session,
                provider_event_id=f"checkout.session.reconcile:{stripe_session_id}",
                event_created=session.get("created"),
            )
            subscription_result = app_payments.reconcile_subscription(
                payment_conn, subscription
            )
    return {
        "ok": True,
        "session_id": stripe_session_id,
        "business_slug": expected_slug or result.get("business_slug"),
        "processed": result,
        "subscription": subscription_result,
    }


def cancel_app_subscription(
    *,
    business_slug: str,
    app_user_id: str,
    session_token: str,
    cancel_at_period_end: bool = True,
) -> dict[str, Any]:
    """Cancel one product-app Stripe subscription through Safebox authority."""
    business = str(business_slug or "").strip()
    user = str(app_user_id or "").strip()
    token = str(session_token or "").strip()
    if not business:
        raise ValueError("business_slug is required")
    if not user:
        raise ValueError("app_user_id is required")
    if not token:
        raise ValueError("session_token is required")
    if _use_remote_authority():
        try:
            payload = _remote_json(
                "POST",
                "/v1/stripe/app-subscription/cancel",
                {
                    "business_slug": business,
                    "app_user_id": user,
                    "session_token": token,
                    "cancel_at_period_end": bool(cancel_at_period_end),
                },
                timeout=35.0,
            )
        except RemoteSafeboxError as exc:
            detail = _remote_error_detail(exc)
            message = str(detail.get("error") or detail.get("detail") or str(exc)).strip() or str(exc)
            if exc.status_code == 404:
                raise LookupError(message) from exc
            if exc.status_code == 400:
                raise ValueError(message) from exc
            if exc.status_code == 503:
                raise RuntimeError(message) from exc
            raise
        return payload if isinstance(payload, dict) else {}

    from . import app_identity, app_payments, stripe_util

    with _creative_credit_conn(None) as payment_conn:
        session_user = app_identity.validate_session(payment_conn, business, token)
        if session_user is None:
            raise PermissionError("app_session_invalid")
        if str(session_user.id) != user:
            raise PermissionError("app_session_user_mismatch")
        return app_payments.cancel_subscription(
            payment_conn,
            business,
            app_user_id=user,
            cancel_at_period_end=bool(cancel_at_period_end),
            subscription_updater=lambda subscription_id, should_cancel_at_period_end: stripe_util.stripe_request(
                f"subscriptions/{subscription_id}",
                {"cancel_at_period_end": "true" if should_cancel_at_period_end else "false"},
            ),
        )


def get_business_credit_balances(conn, business_slug: str) -> CreativeCreditBalances:
    """Read one business creative-credit balance through Safebox authority."""
    slug = str(business_slug or "").strip()
    if not slug:
        raise ValueError("missing business_slug")
    if _use_remote_authority():
        payload = _remote_json(
            "GET",
            f"/v1/creative-credits/{urllib.parse.quote(slug, safe='')}",
        )
        return _balances_from_payload(payload, business_slug=slug)
    return _local_get_business_credit_balances(conn, slug)


def create_creative_credit_checkout(
    user_id: str,
    business_slug: str,
    *,
    credits: int | None = None,
    pack_id: str | None = None,
    success_url: str,
    cancel_url: str,
) -> dict[str, Any]:
    """Create a business creative-credit Stripe checkout through Safebox authority."""
    user_ref = str(user_id or "").strip()
    slug = str(business_slug or "").strip()
    if not user_ref:
        raise ValueError("missing user_id")
    if not slug:
        raise ValueError("missing business_slug")
    if _use_remote_authority():
        try:
            payload = _remote_json(
                "POST",
                "/v1/creative-credits/checkout",
                {
                    "user_id": user_ref,
                    "business_slug": slug,
                    "credits": None if credits is None else int(credits),
                    "pack_id": str(pack_id or "").strip() or None,
                    "success_url": success_url,
                    "cancel_url": cancel_url,
                },
            )
        except RemoteSafeboxError as exc:
            detail = _remote_error_detail(exc)
            message = str(detail.get("error") or detail.get("detail") or str(exc)).strip() or str(exc)
            if exc.status_code == 404:
                raise LookupError(message) from exc
            if exc.status_code == 400:
                raise ValueError(message) from exc
            if exc.status_code in {502, 503}:
                from . import stripe_util

                if exc.status_code == 503:
                    raise stripe_util.StripeError("creative_credit_checkout_unconfigured") from exc
                raise stripe_util.StripeError(message) from exc
            raise
        return payload if isinstance(payload, dict) else {}
    from . import stripe_util
    from .control_api import create_creative_credit_checkout_session

    try:
        session, charge = create_creative_credit_checkout_session(
            user_ref,
            slug,
            credits=credits,
            pack_id=pack_id,
            success_url=success_url,
            cancel_url=cancel_url,
        )
    except SafeboxAuthorityUnavailable as exc:
        raise stripe_util.StripeError("creative_credit_checkout_unconfigured") from exc
    return {
        "checkout_url": session.get("url"),
        "session_id": session.get("id"),
        "business_slug": slug,
        "pack_id": charge.get("pack_id"),
        "credits": charge["credits"],
        "amount_cents": charge["amount_cents"],
        "price_cents_per_credit": charge.get("price_cents_per_credit"),
    }


def reconcile_creative_credit_checkout(
    conn,
    *,
    session_id: str,
    expected_business_slug: str | None = None,
) -> dict[str, Any]:
    """Settle one paid creative-credit checkout through Safebox authority."""
    from . import stripe_util

    stripe_session_id = str(session_id or "").strip()
    expected_slug = str(expected_business_slug or "").strip()
    if not stripe_session_id:
        raise ValueError("session_id is required")
    if _use_remote_authority():
        try:
            payload = _remote_json(
                "POST",
                "/v1/creative-credits/reconcile",
                {
                    "session_id": stripe_session_id,
                    "business_slug": expected_slug or None,
                },
            )
        except RemoteSafeboxError as exc:
            detail = _remote_error_detail(exc)
            message = str(detail.get("error") or detail.get("detail") or str(exc)).strip() or str(exc)
            if exc.status_code == 404:
                raise LookupError(message) from exc
            if exc.status_code == 409:
                raise RuntimeError("creative_credit_checkout_unpaid") from exc
            if exc.status_code == 400:
                raise ValueError(message) from exc
            if exc.status_code in {502, 503}:
                if exc.status_code == 503:
                    raise stripe_util.StripeError("creative_credit_reconcile_unconfigured") from exc
                raise stripe_util.StripeError(message) from exc
            raise
        return payload if isinstance(payload, dict) else {}

    try:
        session = stripe_util.stripe_request(
            f"checkout/sessions/{stripe_session_id}",
            {},
            method="GET",
        )
    except stripe_util.StripeError as exc:
        message = str(exc)
        if " failed: 404" in message:
            raise LookupError(f"unknown_stripe_checkout_session:{stripe_session_id}") from exc
        raise
    if not isinstance(session, dict) or not session:
        raise LookupError(f"unknown_stripe_checkout_session:{stripe_session_id}")

    metadata = session.get("metadata") if isinstance(session.get("metadata"), dict) else {}
    purpose = str(metadata.get("purpose") or "").strip()
    if purpose not in {"creative_credit_pack", "creative_credit_topup"}:
        raise ValueError("not a creative credit checkout session")
    payment_status = str(session.get("payment_status") or "").strip()
    if payment_status not in {"paid", "no_payment_required"}:
        raise RuntimeError("creative_credit_checkout_unpaid")

    business_slug = str(
        metadata.get("business_slug") or session.get("client_reference_id") or ""
    ).strip()
    if not business_slug:
        raise ValueError("creative credit checkout session missing business_slug")
    if expected_slug and business_slug != expected_slug:
        raise ValueError("checkout session does not belong to requested business")

    try:
        credits = int(metadata.get("credits") or 0)
    except (TypeError, ValueError):
        credits = 0
    if credits <= 0:
        raise ValueError("creative credit checkout session missing credits")
    try:
        amount_cents = int(session.get("amount_total") or 0)
    except (TypeError, ValueError):
        amount_cents = 0
    try:
        price_cents_per_credit = int(metadata.get("price_cents_per_credit") or 0)
    except (TypeError, ValueError):
        price_cents_per_credit = 0
    pack_id = str(metadata.get("pack_id") or "").strip()

    grant_metadata = {
        "purpose": purpose,
        "user_id": metadata.get("user_id"),
        "stripe_checkout_session_id": stripe_session_id,
        "amount_cents": amount_cents,
        "price_cents_per_credit": price_cents_per_credit,
        "reconciled_via": "checkout_session_read",
    }
    if pack_id:
        grant_metadata["pack_id"] = pack_id

    balances = _local_grant_credits(
        conn,
        business_slug,
        credits,
        f"stripe_checkout_session:{stripe_session_id}",
        metadata=grant_metadata,
        stripe_ref=stripe_session_id,
    )
    return {
        "ok": True,
        "business_slug": business_slug,
        "credited_credits": credits,
        "balance_credits": balances.balance_credits,
        "reserved_credits": balances.reserved_credits,
        "session_id": stripe_session_id,
    }


def verify_stripe_billing_webhook(raw_body: str, signature: str) -> dict[str, Any]:
    """Verify one Stripe billing webhook through Safebox authority and return the event."""
    body = str(raw_body or "")
    presented = str(signature or "").strip()
    if _use_remote_authority():
        try:
            payload = _remote_json(
                "POST",
                "/v1/stripe/billing-webhook/verify",
                {"raw_body": body, "signature": presented},
            )
        except RemoteSafeboxError as exc:
            if exc.status_code == 503:
                raise StripeBillingWebhookUnconfigured("billing_webhook_unconfigured") from exc
            if exc.status_code == 400:
                raise StripeBillingWebhookInvalidSignature("invalid_signature") from exc
            raise
        event = payload.get("event")
        return event if isinstance(event, dict) else {}
    from . import stripe_util

    try:
        secret = read_env_backed_value("STRIPE_BILLING_WEBHOOK_SECRET")
    except SafeboxAuthorityUnavailable as exc:
        raise StripeBillingWebhookUnconfigured("billing_webhook_unconfigured") from exc
    if not secret:
        raise StripeBillingWebhookUnconfigured("billing_webhook_unconfigured")
    try:
        stripe_util.verify_stripe_signature(body, presented, secret)
    except stripe_util.StripeError as exc:
        raise StripeBillingWebhookInvalidSignature("invalid_signature") from exc
    event = json.loads(body)
    try:
        stripe_util.validate_stripe_webhook_event_mode(event)
    except stripe_util.StripeError as exc:
        raise StripeBillingWebhookInvalidSignature("invalid_livemode") from exc
    return event if isinstance(event, dict) else {}


def verify_stripe_app_webhook(raw_body: str, signature: str) -> dict[str, Any]:
    """Verify one Stripe app (flow-B) webhook through Safebox authority and return the event.

    Mirrors ``verify_stripe_billing_webhook`` exactly: a remote-authority runtime plane POSTs the
    raw body + signature to ``/v1/stripe/app-webhook/verify`` and the safebox reads
    STRIPE_WEBHOOK_SECRET locally, verifies, and returns the parsed event (never the secret). On the
    safebox host the secret is read and verified locally. Fails closed — a missing secret or an
    authority-unavailable error raises StripeAppWebhookUnconfigured, a bad signature raises
    StripeAppWebhookInvalidSignature; it NEVER returns an unverified event."""
    body = str(raw_body or "")
    presented = str(signature or "").strip()
    if _use_remote_authority():
        try:
            payload = _remote_json(
                "POST",
                "/v1/stripe/app-webhook/verify",
                {"raw_body": body, "signature": presented},
            )
        except RemoteSafeboxError as exc:
            if exc.status_code == 503:
                raise StripeAppWebhookUnconfigured("app_webhook_unconfigured") from exc
            if exc.status_code == 400:
                raise StripeAppWebhookInvalidSignature("invalid_signature") from exc
            raise
        event = payload.get("event")
        return event if isinstance(event, dict) else {}
    from . import stripe_util

    try:
        secret = read_env_backed_value("STRIPE_WEBHOOK_SECRET")
    except SafeboxAuthorityUnavailable as exc:
        raise StripeAppWebhookUnconfigured("app_webhook_unconfigured") from exc
    if not secret:
        raise StripeAppWebhookUnconfigured("app_webhook_unconfigured")
    try:
        stripe_util.verify_stripe_signature(body, presented, secret)
    except stripe_util.StripeError as exc:
        raise StripeAppWebhookInvalidSignature("invalid_signature") from exc
    event = json.loads(body)
    try:
        stripe_util.validate_stripe_webhook_event_mode(event)
    except stripe_util.StripeError as exc:
        raise StripeAppWebhookInvalidSignature("invalid_livemode") from exc
    return event if isinstance(event, dict) else {}


def verify_shopify_app_webhook(raw_body: str, hmac_header: str) -> dict[str, Any]:
    """Verify one Shopify app webhook (X-Shopify-Hmac-Sha256, base64 HMAC-SHA256 over the raw
    body) on SAFEBOX AUTHORITY ONLY and return the parsed event.

    Mirrors ``verify_stripe_app_webhook``'s local leg: the shared secret is resolved from the
    safebox's LOCAL env authority (``SHOPIFY_WEBHOOK_SECRET`` aliases) and never leaves it. This
    function deliberately has NO remote leg — a runtime plane must not resolve the secret at all;
    it calls :func:`process_shopify_app_webhook`, which forwards raw body + header to the safebox
    route where THIS verification runs. Fails closed: missing secret/authority raises
    ``ShopifyAppWebhookUnconfigured``, a bad HMAC raises ``ShopifyAppWebhookInvalidSignature``;
    it NEVER returns an unverified event."""
    from . import shopify_util

    if _remote_enabled() and not _local_authority_enabled():
        raise ShopifyAppWebhookUnconfigured(
            "shopify webhook verification runs only on safebox authority; "
            "use process_shopify_app_webhook from runtime planes"
        )
    body = str(raw_body or "")
    try:
        secret = first_env_backed_value(*shopify_util.SHOPIFY_WEBHOOK_SECRET_ALIASES)
    except SafeboxAuthorityUnavailable as exc:
        raise ShopifyAppWebhookUnconfigured("shopify_webhook_unconfigured") from exc
    if not secret:
        raise ShopifyAppWebhookUnconfigured("shopify_webhook_unconfigured")
    try:
        shopify_util.verify_webhook_hmac(body, hmac_header, secret)
    except shopify_util.ShopifyWebhookUnconfigured as exc:
        raise ShopifyAppWebhookUnconfigured("shopify_webhook_unconfigured") from exc
    except shopify_util.ShopifyWebhookInvalidSignature as exc:
        raise ShopifyAppWebhookInvalidSignature("invalid_signature") from exc
    try:
        event = json.loads(body)
    except (TypeError, ValueError) as exc:
        raise ShopifyAppWebhookInvalidSignature("invalid_body") from exc
    return event if isinstance(event, dict) else {}


def process_shopify_app_webhook(raw_body: str, hmac_header: str, topic: str) -> dict[str, Any]:
    """Verify and process one Shopify app webhook through Safebox authority.

    Mirrors ``process_stripe_app_webhook``: a runtime plane forwards the RAW body + HMAC header +
    topic to ``/v1/shopify/app-webhook/process``; the safebox resolves the shared secret locally,
    verifies the HMAC, and only then runs the dedup + shop/update recompose on its own DB role in
    the same signed-event path. The runtime plane never holds the secret and never writes plan
    rows itself. Fail-closed mapping: 503 → ShopifyAppWebhookUnconfigured, 400/401 →
    ShopifyAppWebhookInvalidSignature."""
    body = str(raw_body or "")
    presented = str(hmac_header or "").strip()
    topic_value = str(topic or "").strip()
    if _remote_enabled() and not _local_authority_enabled():
        try:
            payload = _remote_json(
                "POST",
                "/v1/shopify/app-webhook/process",
                {"raw_body": body, "hmac_sha256": presented, "topic": topic_value},
                timeout=30.0,
            )
        except RemoteSafeboxError as exc:
            if exc.status_code == 503:
                raise ShopifyAppWebhookUnconfigured("shopify_webhook_unconfigured") from exc
            if exc.status_code in {400, 401}:
                raise ShopifyAppWebhookInvalidSignature("invalid_signature") from exc
            raise
        return payload if isinstance(payload, dict) else {}
    verify_shopify_app_webhook(body, presented)
    from . import shopify_util

    with _creative_credit_conn(None) as conn:
        return shopify_util.record_webhook_and_process(conn, topic=topic_value, raw_body=body)


def grant_credits(
    conn,
    business_slug: str,
    credits: int,
    idempotency_key: str,
    *,
    metadata: dict | None = None,
    stripe_ref: str | None = None,
) -> CreativeCreditBalances:
    """Grant purchased business creative credits through safebox-local authority.

    Remote planes must use a verified checkout/webhook processor; accepting an arbitrary amount over
    the shared internal token would be a mint hole (GOAL_RULES §0).
    """
    slug = str(business_slug or "").strip()
    if not slug:
        raise ValueError("missing business_slug")
    if _remote_enabled() and not _local_authority_enabled():
        raise SafeboxAuthorityUnavailable(
            "creative credit grants must be derived from a verified checkout/webhook on the safebox"
        )
    return _local_grant_credits(
        conn,
        slug,
        credits,
        idempotency_key,
        metadata=metadata,
        stripe_ref=stripe_ref,
    )


def reserve_credits(
    conn,
    business_slug: str,
    credits: int,
    reservation_key: str,
    *,
    metadata: dict | None = None,
) -> CreativeCreditReservation:
    """Reserve business creative credits through Safebox authority."""
    slug = str(business_slug or "").strip()
    key = str(reservation_key or "").strip()
    if not slug:
        raise ValueError("missing business_slug")
    if _use_remote_authority():
        raise CreativeGateRefused(
            "creative_credit_spend_requires_creative_gate",
            status_code=403,
            payload={"error": "creative_credit_spend_requires_creative_gate"},
        )
    return _local_reserve_credits(
        conn,
        slug,
        credits,
        key,
        metadata=metadata,
    )


def commit_credits(
    conn,
    reservation_key: str,
    *,
    actual_credits: int | None = None,
    metadata: dict | None = None,
) -> CreativeCreditBalances:
    """Commit one business creative-credit reservation through Safebox authority."""
    key = str(reservation_key or "").strip()
    if not key:
        raise ValueError("reservation_key is required")
    if _use_remote_authority():
        raise CreativeGateRefused(
            "creative_credit_spend_requires_creative_gate",
            status_code=403,
            payload={"error": "creative_credit_spend_requires_creative_gate"},
        )
    return _local_commit_credits(
        conn,
        key,
        actual_credits=actual_credits,
        metadata=metadata,
    )


def release_credits(
    conn,
    reservation_key: str,
    *,
    metadata: dict | None = None,
) -> CreativeCreditBalances:
    """Release one business creative-credit reservation through Safebox authority."""
    key = str(reservation_key or "").strip()
    if not key:
        raise ValueError("reservation_key is required")
    if _use_remote_authority():
        raise CreativeGateRefused(
            "creative_credit_spend_requires_creative_gate",
            status_code=403,
            payload={"error": "creative_credit_spend_requires_creative_gate"},
        )
    return _local_release_credits(
        conn,
        key,
        metadata=metadata,
    )


# ── Creative-credit AUTHORITATIVE gate client (logo / UGC / static-ad) ────────────────────────────
# The creative-credit money gate for the fixed-price creative actions lives AUTHORITATIVELY on the
# safebox: the operator reserves the action's canonical fixed credits via /v1/creative/reserve (the
# safebox validates business ownership, resolves the canonical price, and reserves the credits ON THE
# SAFEBOX), which hands back a creative capability. The runtime then presents that capability to the
# gated /v1/providers/{gemini/logo,openai/images,fal/{path}} routes to call the provider key-free, and
# commits/releases the ONE reservation when the action finishes. The business runtime NEVER reserves
# credits itself (no double-charge) and NEVER holds a raw provider key. These helpers route
# local-vs-remote like reserve_credits/proxy_request: remote -> the safebox HTTP routes; local (the
# safebox host / local dev) -> the same authoritative safebox_app logic in-process.
_CREATIVE_GATE_TIMEOUT_S = 180.0


class CreativeGateRefused(RuntimeError):
    """The safebox creative-credit gate refused (insufficient credits / not owner / unmappable action).

    ``status_code`` mirrors the HTTP status the safebox returned (402 insufficient credits, 403 not the
    business owner, 400 unmappable action). ``payload`` carries the structured detail (e.g.
    ``requested_credits`` / ``available_credits`` for a 402)."""

    def __init__(self, message: str, *, status_code: int, payload: dict[str, Any]):
        super().__init__(message)
        self.status_code = int(status_code)
        self.payload = payload if isinstance(payload, dict) else {}


def creative_reserve(
    *,
    business: str,
    operator_user_id: str,
    action: str,
    reservation_key: str,
    units: int = 1,
    ttl_seconds: int | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Reserve a creative action's fixed credits on the safebox and return its creative capability.

    Returns ``{"token", "audience", "reservation_key", "reserved_credits", "credits"}``. The token is
    the creative capability the caller presents to ``creative_provider_call``. Raises
    ``CreativeGateRefused`` (402 insufficient credits / 403 not owner / 400 bad action) BEFORE any
    provider key is resolved or any provider is called — fail closed, never a raw-key fallback."""
    business = str(business or "").strip()
    operator_user_id = str(operator_user_id or "").strip()
    action = str(action or "").strip()
    reservation_key = str(reservation_key or "").strip()
    if not business or not operator_user_id or not action or not reservation_key:
        raise ValueError("creative_reserve requires business, operator_user_id, action, reservation_key")
    body: dict[str, Any] = {
        "business": business,
        "operator_user_id": operator_user_id,
        "action": action,
        "reservation_key": reservation_key,
        "units": int(max(1, units or 1)),
    }
    if ttl_seconds is not None:
        body["ttl_seconds"] = int(ttl_seconds)
    if metadata is not None:
        body["metadata"] = dict(metadata or {})
    if _use_remote_authority():
        try:
            return _remote_json("POST", "/v1/creative/reserve", body, timeout=_CREATIVE_GATE_TIMEOUT_S)
        except RemoteSafeboxError as exc:
            raise CreativeGateRefused(
                str(exc), status_code=exc.status_code, payload=_remote_error_detail(exc)
            ) from exc
    return _local_creative_reserve(body)


def creative_provider_call(
    provider: str,
    path: str,
    payload: dict[str, Any],
    *,
    token: str,
    timeout: float = _CREATIVE_GATE_TIMEOUT_S,
) -> dict[str, Any]:
    """Call a gated creative PROVIDER route (``/v1/providers/<provider>/<path>``) presenting a creative
    capability ``token`` and return the KEY-FREE result. The safebox verifies the capability, resolves
    the provider key LOCALLY, and forwards. Fails closed (``RemoteSafeboxError`` / refusal) — never a raw
    key. Used by the runtime AND by render subprocesses (which receive the token via env)."""
    prov = str(provider or "").strip().strip("/")
    sub = str(path or "").strip().strip("/")
    tok = str(token or "").strip()
    if not prov:
        raise ValueError("provider is required")
    if not tok:
        raise ValueError("creative capability token is required")
    route = f"/v1/providers/{prov}" + (f"/{sub}" if sub else "")
    body = {"token": tok, "payload": dict(payload or {})}
    if _use_remote_authority():
        return _remote_json("POST", route, body, timeout=timeout)
    return _local_creative_provider_call(prov, sub, body)


def creative_commit(
    *,
    reservation_key: str,
    actual_credits: int | None = None,
    metadata: dict[str, Any] | None = None,
) -> CreativeCreditBalances:
    """Commit (settle) the ONE creative-credit reservation on the safebox after the action succeeds."""
    key = str(reservation_key or "").strip()
    if not key:
        raise ValueError("reservation_key is required")
    body = {
        "reservation_key": key,
        "actual_credits": (None if actual_credits is None else int(actual_credits)),
    }
    if metadata is not None:
        body["metadata"] = dict(metadata or {})
    if _use_remote_authority():
        try:
            payload = _remote_json("POST", "/v1/creative/commit", body, timeout=_CREATIVE_GATE_TIMEOUT_S)
        except RemoteSafeboxError as exc:
            if exc.status_code == 404:
                raise UnknownCreativeCreditReservation(key) from exc
            raise
        return _balances_from_payload(payload, business_slug="")
    local_metadata = {
        **(metadata if isinstance(metadata, dict) else {}),
        "via": "safebox_creative_gate",
    }
    return _local_commit_credits(None, key, actual_credits=actual_credits, metadata=local_metadata)


def creative_release(
    *,
    reservation_key: str,
    metadata: dict[str, Any] | None = None,
) -> CreativeCreditBalances:
    """Release the ONE creative-credit reservation on the safebox after the action fails."""
    key = str(reservation_key or "").strip()
    if not key:
        raise ValueError("reservation_key is required")
    body = {"reservation_key": key}
    if metadata is not None:
        body["metadata"] = dict(metadata or {})
    if _use_remote_authority():
        try:
            payload = _remote_json("POST", "/v1/creative/release", body, timeout=_CREATIVE_GATE_TIMEOUT_S)
        except RemoteSafeboxError as exc:
            if exc.status_code == 404:
                raise UnknownCreativeCreditReservation(key) from exc
            raise
        return _balances_from_payload(payload, business_slug="")
    local_metadata = {
        **(metadata if isinstance(metadata, dict) else {}),
        "via": "safebox_creative_gate",
    }
    return _local_release_credits(None, key, metadata=local_metadata)


def _local_creative_reserve(body: dict[str, Any]) -> dict[str, Any]:
    """LOCAL (safebox host / local dev) creative reserve: run the SAME authoritative logic the
    /v1/creative/reserve route runs in-process (ownership validation + canonical price + reserve + mint)
    via a TestClient against the safebox app, so the gate is identical on both planes and there is no
    second code path. Maps the route's HTTP refusals to ``CreativeGateRefused`` /
    ``InsufficientCreativeCredits``."""
    from starlette.testclient import TestClient

    from . import safebox_app

    client = TestClient(safebox_app.build_safebox_app())
    resp = client.post("/v1/creative/reserve", headers=_local_internal_headers(), json=body)
    if resp.status_code == 200:
        return resp.json()
    detail = _testclient_detail(resp)
    if resp.status_code == 402:
        raise InsufficientCreativeCredits(
            requested_credits=int(detail.get("requested_credits") or 0),
            available_credits=int(detail.get("available_credits") or 0),
        )
    raise CreativeGateRefused(
        str(detail.get("error") or detail or resp.text),
        status_code=resp.status_code,
        payload=detail,
    )


def _local_creative_provider_call(provider: str, sub: str, body: dict[str, Any]) -> dict[str, Any]:
    """LOCAL creative provider call: present the creative capability to the in-process safebox app
    (verify -> key-local -> forward). Fail-closed: a non-200 surfaces as ``RemoteSafeboxError``."""
    from starlette.testclient import TestClient

    from . import safebox_app

    route = f"/v1/providers/{provider}" + (f"/{sub}" if sub else "")
    client = TestClient(safebox_app.build_safebox_app())
    resp = client.post(route, headers=_local_internal_headers(), json=body)
    if resp.status_code == 200:
        return resp.json()
    detail = _testclient_detail(resp)
    raise RemoteSafeboxError(
        f"local safebox {route} failed: {detail}",
        status_code=resp.status_code,
        payload=detail if isinstance(detail, dict) else {"detail": detail},
    )


def _local_internal_headers() -> dict[str, str]:
    """Authorization header for the in-process safebox TestClient. On the local/safebox host the internal
    token is whatever ``TAKYON_SAFEBOX_TOKEN`` is set to (the app's ``_require_internal_token`` reads the
    same env); when unset, the app's tokenless opt-out covers local dev / hermetic tests."""
    token = str(os.environ.get(_SAFEBOX_REMOTE_TOKEN_ENV) or "").strip()
    return {"Authorization": f"Bearer {token}"} if token else {}


def _testclient_detail(resp) -> dict[str, Any]:
    try:
        data = resp.json()
    except Exception:
        return {"detail": resp.text}
    if isinstance(data, dict):
        detail = data.get("detail", data)
        if isinstance(detail, dict):
            return detail
        return {"error": detail} if detail is not None else data
    return {"detail": data}


def read_env_backed_value(key: str) -> str:
    """Read one sensitive env-backed value from env or TAKYON_HOME/.env."""
    if _use_remote_authority():
        payload = _remote_json(
            "GET",
            f"/v1/env/{urllib.parse.quote(_require_sensitive(key), safe='')}",
        )
        return str(payload.get("value") or "").strip()
    name = _require_sensitive(key)
    if _managed_secret_applies(name):
        return _read_managed_secret(name).strip()
    value = os.environ.get(name)
    if value is not None:
        return value.strip()
    return str(load_env().get(name) or "").strip()


def composio_forward(
    *,
    method: str,
    path: str,
    json_body: dict | None = None,
    params: list | None = None,
    timeout: float = 60.0,
) -> dict:
    """Broker one Composio distribution HTTP call through the safebox.

    COMPOSIO_API_KEY is a provider secret the safebox holds and refuses to egress over /v1/env, so a
    runtime plane can't call Composio itself. This forwards the call to the safebox
    ``/v1/providers/composio/forward`` route, which resolves the key LOCALLY and returns the key-free
    upstream JSON. Used by ``composio_distribution._request`` for every channel
    (twitter/reddit/reddit_ads/metaads) and the connected-account lookup."""
    payload: dict = {"method": method, "path": path, "timeout": float(timeout)}
    if json_body is not None:
        payload["json_body"] = json_body
    if params is not None:
        payload["params"] = params
    return _remote_json(
        "POST",
        "/v1/providers/composio/forward",
        payload,
        timeout=max(15.0, float(timeout) + 10.0),
    )


def umami_forward(
    *,
    path: str,
    params: dict[str, Any] | None = None,
    timeout: float = 20.0,
) -> dict:
    """Broker one READ-ONLY Umami stats call through the safebox.

    UMAMI_API_KEY is account-scoped (it reads every business's analytics and can manage the shared
    Umami account), so it is denied /v1/env egress and never leaves the safebox. This forwards the
    read to the safebox ``/v1/analytics/umami/forward`` route, which resolves the key LOCALLY, calls
    Umami Cloud, and returns the key-free upstream JSON. Used by ``umami_util.umami_request`` on
    runtime (remote-authority) planes. The caller never supplies the upstream URL — the safebox uses
    its OWN configured api_endpoint — which closes the key-exfil vector."""
    payload: dict[str, Any] = {"path": str(path or ""), "timeout": float(timeout)}
    if params is not None:
        payload["params"] = dict(params)
    return _remote_json(
        "POST",
        "/v1/analytics/umami/forward",
        payload,
        timeout=max(15.0, float(timeout) + 10.0),
    )


def gsc_verification_token(site_url: str) -> dict:
    """Return a Google Search Console META verification token without vending the service account."""
    return _remote_json(
        "POST",
        "/v1/gsc/verification-token",
        {"site_url": str(site_url or "")},
        timeout=60.0,
    )


def gsc_verify_and_submit(site_url: str, *, submit_sitemap: bool = True) -> dict:
    """Verify a GSC URL-prefix property and optionally submit sitemap.xml, all on the safebox."""
    return _remote_json(
        "POST",
        "/v1/gsc/verify",
        {"site_url": str(site_url or ""), "submit_sitemap": bool(submit_sitemap)},
        timeout=90.0,
    )


def gsc_add_property(site_url: str) -> dict:
    """Add an already-verifiable GSC URL-prefix property without vending the service-account key."""
    return _remote_json(
        "POST",
        "/v1/gsc/add-property",
        {"site_url": str(site_url or "")},
        timeout=60.0,
    )


def store_asc_account_health() -> dict:
    """Probe the Apple developer account's health via the safebox (App Store rail, readmodular §4.1).
    The safebox resolves the custodied ASC .p8 + identifiers, mints a JWT, and probes Apple; only the
    receipt {state, status_code, detail, checked_at} returns — the key never egresses. state is one of
    ok | agreement_blocked | auth_error | error | unreachable."""
    return _remote_json("POST", "/v1/store/asc/account-health", {}, timeout=30.0)


def store_eas_build_credentials(business_slug: str, *, capabilities: list | None = None) -> dict:
    """Mint the per-build store-signing bundle via the safebox (host-independent builder lane).

    The safebox does the ASC provisioning SERVER-SIDE — ensures the business's deterministic bundle
    id, syncs capabilities, and (re)mints the App Store provisioning profile bound to the custodied
    team distribution cert — so the ASC .p8 never egresses. The response carries only the ephemeral
    signing material the eas-cli child process needs: expo_token, dist p12 (base64) + password,
    profile (base64), team/owner identifiers. Operator-plane only; generous timeout because the ASC
    provisioning is several sequential Apple API calls."""
    return _remote_json(
        "POST",
        "/v1/store/eas/build-credentials",
        {"business": str(business_slug or ""), "capabilities": list(capabilities or [])},
        timeout=180.0,
    )


def openmeter_request(
    method: str,
    path: str,
    *,
    payload: dict[str, Any] | None = None,
    query: dict[str, Any] | None = None,
    allow_status: list[int] | None = None,
    expected_status: list[int] | None = None,
    timeout: float = 20.0,
) -> dict:
    """Broker one OpenMeter mirror HTTP request through the safebox.

    OpenMeter is non-authoritative, but its API token is still a bearer credential. Runtime planes
    should not fetch it over /v1/env; they send key-free request metadata here and the safebox performs
    the reporting call locally.
    """
    return _remote_json(
        "POST",
        "/v1/openmeter/request",
        {
            "method": str(method or "GET"),
            "path": str(path or ""),
            "payload": payload,
            "query": query,
            "allow_status": list(allow_status or []),
            "expected_status": list(expected_status or []),
            "timeout": float(timeout),
        },
        timeout=max(15.0, float(timeout) + 10.0),
    )


def meta_config() -> dict:
    """Fetch the NON-SECRET Meta Ads config from the safebox.

    The Meta system-user token is a provider secret the safebox holds and refuses to egress over
    /v1/env, so a runtime plane cannot resolve it. This returns the brokered config (graph version,
    ad account id, page id, MCP endpoint/readiness) plus a ``has_token`` bool; the token VALUE is redacted to
    "" by the safebox route and never reaches this process. Used by ``core._meta_config`` on runtime
    planes; production launch/control/read calls use the official Meta Ads MCP broker."""
    if _use_remote_authority():
        payload = _remote_json("POST", "/v1/providers/meta/config", {})
        return payload if isinstance(payload, dict) else {}

    from . import meta_mcp

    token = _local_meta_graph_token()
    mcp_token = _local_meta_mcp_token()
    return {
        "token": "",
        "has_token": bool(token),
        "has_mcp_oauth_token": bool(mcp_token),
        "mcp_endpoint": _local_meta_mcp_endpoint(),
        "version": _local_meta_graph_version(),
        "ad_account_id": str(first_env_backed_value("META_AD_ACCOUNT_ID") or "").strip(),
        "page_id": str(first_env_backed_value("META_PAGE_ID") or "").strip(),
        "instagram_user_id": str(first_env_backed_value("META_INSTAGRAM_ID") or "").strip(),
        "composio_connected_account_id": "",
        "composio_user_id": "",
        "composio_alias": "",
    }


def meta_graph_forward(
    *,
    method: str,
    path: str,
    params: dict | None = None,
    host: str = "graph.facebook.com",
    timeout: float = 60.0,
) -> dict:
    """Broker one legacy Meta Graph API call through the safebox.

    Forwards method/path/params to the safebox ``/v1/providers/meta/graph`` route, which re-resolves
    the real Meta system-user token LOCALLY and returns the key-free upstream JSON. The token never
    leaves the safebox. The upstream host is fixed to graph.facebook.com; the ``host`` parameter is
    retained only to fail closed on stale callers that try to pick a host. Retained for
    diagnostics/compatibility; production v2 launch/control/read calls use the official Meta MCP
    broker instead."""
    from . import meta_graph

    requested_host = str(host or meta_graph._GRAPH_HOST).strip().lower()
    if requested_host != meta_graph._GRAPH_HOST:
        raise ValueError("meta_graph_host_not_allowed")
    payload: dict = {
        "method": str(method or "GET"),
        "path": str(path or ""),
        "params": dict(params or {}),
        "timeout": float(timeout),
    }
    if _use_remote_authority():
        result = _remote_json(
            "POST",
            "/v1/providers/meta/graph",
            payload,
            timeout=max(20.0, float(timeout) + 10.0),
        )
        return result if isinstance(result, dict) else {}

    token = _local_meta_graph_token()
    if not token:
        raise RemoteSafeboxError(
            "Meta system-user token is not configured on the safebox",
            status_code=428,
            payload={"detail": "meta_system_user_token_required"},
        )
    return meta_graph._graph(
        str(method or "GET"),
        str(path or "").lstrip("/"),
        dict(params or {}),
        token=token,
        version=_local_meta_graph_version(),
        host=meta_graph._GRAPH_HOST,
        timeout=float(timeout),
    )


def meta_graph_upload_video(
    *,
    ad_account_id: str,
    video_bytes: bytes,
    name: str,
    poll: bool = True,
    timeout: float = 180.0,
) -> str:
    """Upload raw video bytes to Meta through the safebox-held system-user token."""
    payload = {
        "ad_account_id": str(ad_account_id or ""),
        "name": str(name or ""),
        "data_b64": base64.b64encode(bytes(video_bytes or b"")).decode("ascii"),
        "poll": bool(poll),
        "timeout": float(timeout),
    }
    if _use_remote_authority():
        result = _remote_json(
            "POST",
            "/v1/providers/meta/graph/upload-video",
            payload,
            timeout=max(20.0, float(timeout) + 10.0),
        )
        if isinstance(result, dict):
            return str(result.get("video_id") or result.get("id") or "").strip()
        return ""

    from . import meta_graph

    token = _local_meta_graph_token()
    if not token:
        raise RemoteSafeboxError(
            "Meta system-user token is not configured on the safebox",
            status_code=428,
            payload={"detail": "meta_system_user_token_required"},
        )
    return meta_graph.upload_video(
        token,
        str(ad_account_id or ""),
        bytes(video_bytes or b""),
        name=str(name or ""),
        version=_local_meta_graph_version(),
        poll=bool(poll),
        timeout=float(timeout),
    )


def meta_graph_upload_image(
    *,
    ad_account_id: str,
    image_bytes: bytes,
    name: str,
    timeout: float = 180.0,
) -> dict[str, Any]:
    """Upload raw image bytes to Meta through the safebox-held system-user token."""
    payload = {
        "ad_account_id": str(ad_account_id or ""),
        "name": str(name or ""),
        "data_b64": base64.b64encode(bytes(image_bytes or b"")).decode("ascii"),
        "timeout": float(timeout),
    }
    if _use_remote_authority():
        result = _remote_json(
            "POST",
            "/v1/providers/meta/graph/upload-image",
            payload,
            timeout=max(20.0, float(timeout) + 10.0),
        )
        return result if isinstance(result, dict) else {}

    from . import meta_graph

    token = _local_meta_graph_token()
    if not token:
        raise RemoteSafeboxError(
            "Meta system-user token is not configured on the safebox",
            status_code=428,
            payload={"detail": "meta_system_user_token_required"},
        )
    return meta_graph.upload_image(
        token,
        str(ad_account_id or ""),
        bytes(image_bytes or b""),
        name=str(name or ""),
        version=_local_meta_graph_version(),
        timeout=float(timeout),
    )


def meta_graph_ensure_custom_conversion(
    *,
    ad_account_id: str,
    name: str,
    rule: str,
    custom_event_type: str,
    timeout: float = 60.0,
) -> dict[str, Any]:
    """Ensure a per-business custom conversion through the safebox-held system-user token."""
    payload = {
        "ad_account_id": str(ad_account_id or ""),
        "name": str(name or ""),
        "rule": str(rule or ""),
        "custom_event_type": str(custom_event_type or ""),
        "timeout": float(timeout),
    }
    if _use_remote_authority():
        result = _remote_json(
            "POST",
            "/v1/providers/meta/graph/ensure-custom-conversion",
            payload,
            timeout=max(20.0, float(timeout) + 10.0),
        )
        return result if isinstance(result, dict) else {}

    from . import meta_graph

    token = _local_meta_graph_token()
    if not token:
        raise RemoteSafeboxError(
            "Meta system-user token is not configured on the safebox",
            status_code=428,
            payload={"detail": "meta_system_user_token_required"},
        )
    return meta_graph.ensure_custom_conversion(
        token,
        str(ad_account_id or ""),
        name=str(name or ""),
        rule=str(rule or ""),
        custom_event_type=str(custom_event_type or ""),
        version=_local_meta_graph_version(),
    )


def _local_meta_graph_version() -> str:
    version = str(first_env_backed_value("META_GRAPH_VERSION") or "v23.0").strip().lstrip("/")
    if not version:
        return "v23.0"
    return version if version.startswith("v") else f"v{version}"


def _local_meta_graph_token() -> str:
    return str(first_env_backed_value("META_SYSTEM_USER_ACCESS_TOKEN", "META_ACCESS_TOKEN") or "").strip()


def _local_meta_mcp_endpoint() -> str:
    try:
        from . import meta_mcp

        endpoint = first_env_backed_value(*meta_mcp.META_MCP_ENDPOINT_ALIASES)
        return str(endpoint or meta_mcp.DEFAULT_META_MCP_ENDPOINT).strip()
    except Exception:
        return "https://mcp.facebook.com/ads"


def _local_meta_mcp_token() -> str:
    from . import meta_mcp

    return str(first_env_backed_value(*meta_mcp.META_MCP_TOKEN_ALIASES) or "").strip()


def meta_mcp_call(
    *,
    tool_name: str,
    arguments: dict | None = None,
    timeout: float = 60.0,
) -> dict:
    """Broker one official Meta Ads MCP tool call through the safebox.

    META_MCP_OAUTH_TOKEN is a provider credential and is denied /v1/env egress,
    so runtime planes call this key-free broker. On the safebox host the OAuth
    token is resolved locally and sent only to Meta's official MCP endpoint.
    """
    payload = {
        "tool_name": str(tool_name or ""),
        "arguments": dict(arguments or {}),
        "timeout": float(timeout),
    }
    if _use_remote_authority():
        result = _remote_json(
            "POST",
            "/v1/providers/meta/mcp/call",
            payload,
            timeout=max(20.0, float(timeout) + 10.0),
        )
        return result if isinstance(result, dict) else {}

    from . import meta_mcp

    token = _local_meta_mcp_token()
    if not token:
        raise RemoteSafeboxError(
            "Meta MCP OAuth is not configured on the safebox",
            status_code=428,
            payload={"detail": "meta_mcp_oauth_required"},
        )
    return meta_mcp.call_tool(
        str(tool_name or ""),
        dict(arguments or {}),
        token=token,
        endpoint=_local_meta_mcp_endpoint(),
        timeout=float(timeout),
    )


def meta_mcp_list_tools(*, timeout: float = 60.0) -> dict:
    """List official Meta Ads MCP tools through the safebox-held OAuth token."""
    if _use_remote_authority():
        result = _remote_json(
            "POST",
            "/v1/providers/meta/mcp/tools",
            {"timeout": float(timeout)},
            timeout=max(20.0, float(timeout) + 10.0),
        )
        return result if isinstance(result, dict) else {}

    from . import meta_mcp

    token = _local_meta_mcp_token()
    if not token:
        raise RemoteSafeboxError(
            "Meta MCP OAuth is not configured on the safebox",
            status_code=428,
            payload={"detail": "meta_mcp_oauth_required"},
        )
    return meta_mcp.list_tools(
        token=token,
        endpoint=_local_meta_mcp_endpoint(),
        timeout=float(timeout),
    )


def first_env_backed_value(*keys: str) -> str:
    """Return the first non-empty env-backed value across explicit aliases.

    On remote runtime planes this is a read-only, deny-by-default public-config rail; provider keys,
    DB authority, and self-authority secrets are not vendable. On the Safebox host itself, sensitive
    authority code may still resolve local secrets from the process env / local env file.
    """
    if _use_remote_authority():
        payload = _remote_json(
            "POST",
            "/v1/env/first",
            {"keys": [str(key or "").strip() for key in keys]},
        )
        return str(payload.get("value") or "").strip()
    try:
        env_values = load_env()
    except OSError:
        # The local .env may be momentarily unreadable (e.g. a concurrent
        # root-run secret write). It is a SECONDARY source — os.environ (the
        # systemd EnvironmentFile, kept in sync by _save_env_value_direct) is
        # authoritative — so degrade to an empty file view instead of 500ing
        # the /v1/env/first rail for every business.
        env_values = {}
    for key in keys:
        name = str(key or "").strip()
        if not name:
            continue
        try:
            value = read_env_backed_value(name)
        except KeyError:
            # Non-sensitive public alias (for example Supabase browser config):
            # the sensitive reader refuses it. Resolve from the process env first,
            # then the parsed local file. DB authority names are blocked from
            # remote egress by the Safebox app allowlist before this local branch.
            value = str(os.environ.get(name) or env_values.get(name) or "").strip()
        if value:
            return value
    return ""


def _auth0_b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _auth0_b64url_decode(raw: str) -> bytes:
    padding = "=" * (-len(raw) % 4)
    return base64.urlsafe_b64decode((raw + padding).encode("ascii"))


def _auth0_sign_payload(secret: str, payload: dict[str, Any]) -> str:
    body = _auth0_b64url_encode(
        json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    )
    sig = hmac.new(secret.encode("utf-8"), body.encode("ascii"), hashlib.sha256).digest()
    return f"{body}.{_auth0_b64url_encode(sig)}"


def _auth0_unsign_payload(secret: str, token: str) -> dict[str, Any] | None:
    try:
        body, sig = str(token or "").split(".", 1)
        expected = hmac.new(
            secret.encode("utf-8"), body.encode("ascii"), hashlib.sha256
        ).digest()
        if not hmac.compare_digest(_auth0_b64url_decode(sig), expected):
            return None
        payload = json.loads(_auth0_b64url_decode(body).decode("utf-8"))
        return payload if isinstance(payload, dict) else None
    except Exception:
        return None


def _auth0_env_value(*keys: str) -> str:
    try:
        env_values = load_env()
    except Exception:
        env_values = {}
    for key in keys:
        name = str(key or "").strip()
        if not name:
            continue
        value = str(os.environ.get(name) or env_values.get(name) or "").strip()
        if value:
            return value
    return ""


def _auth0_csv_env(*keys: str) -> tuple[str, ...]:
    values: list[str] = []
    for key in keys:
        raw = _auth0_env_value(key)
        if not raw:
            continue
        for item in raw.replace(";", ",").split(","):
            cleaned = item.strip().lower()
            if cleaned and cleaned not in values:
                values.append(cleaned)
    return tuple(values)


def _auth0_normalise_domain(domain: str) -> str:
    value = str(domain or "").strip().rstrip("/")
    if not value:
        return ""
    if not value.startswith(("http://", "https://")):
        value = f"https://{value}"
    return value.rstrip("/")


def _auth0_same_origin_path(path: str) -> str:
    value = str(path or "")
    if not value or not value.startswith("/") or value.startswith("//"):
        return "/"
    return value


def _auth0_secret() -> str:
    try:
        secret = read_env_backed_value("AUTH0_SECRET")
    except SafeboxAuthorityUnavailable as exc:
        raise Auth0AuthorityUnconfigured("auth0_secret_unconfigured") from exc
    if not secret:
        raise Auth0AuthorityUnconfigured("auth0_secret_unconfigured")
    return secret


def _auth0_client_secret() -> str:
    try:
        secret = read_env_backed_value("AUTH0_CLIENT_SECRET")
    except SafeboxAuthorityUnavailable as exc:
        raise Auth0AuthorityUnconfigured("auth0_client_secret_unconfigured") from exc
    if not secret:
        raise Auth0AuthorityUnconfigured("auth0_client_secret_unconfigured")
    return secret


def _auth0_domain() -> str:
    domain = _auth0_normalise_domain(_auth0_env_value("AUTH0_DOMAIN"))
    if not domain:
        raise Auth0AuthorityUnconfigured("auth0_domain_unconfigured")
    return domain


def _auth0_client_id() -> str:
    client_id = _auth0_env_value("AUTH0_CLIENT_ID")
    if not client_id:
        raise Auth0AuthorityUnconfigured("auth0_client_id_unconfigured")
    return client_id


def _auth0_allowed_domains() -> tuple[str, ...]:
    return _auth0_csv_env(
        "TAKYON_DASHBOARD_ALLOWED_EMAIL_DOMAINS",
        "AUTH0_ALLOWED_EMAIL_DOMAINS",
        "ARGON_BETA_ALLOWED_EMAIL_DOMAINS",
    )


def _auth0_allowed_emails() -> tuple[str, ...]:
    return _auth0_csv_env("TAKYON_DASHBOARD_ALLOWED_EMAILS", "AUTH0_ALLOWED_EMAILS")


def _auth0_email_allowed(email: str) -> bool:
    cleaned = str(email or "").strip().lower()
    if not cleaned or "@" not in cleaned:
        return False
    allowed_emails = _auth0_allowed_emails()
    if allowed_emails and cleaned in allowed_emails:
        return True
    domain = cleaned.rsplit("@", 1)[1]
    allowed_domains = _auth0_allowed_domains()
    if allowed_domains:
        return domain in allowed_domains
    return True


def _auth0_authorize_claims(claims: dict[str, Any]) -> dict[str, Any]:
    email = str(claims.get("email") or "").strip().lower()
    if not email:
        raise Auth0AuthorityRejected("Auth0 profile did not include an email address", status_code=403)
    if claims.get("email_verified") is not True:
        raise Auth0AuthorityRejected("Auth0 email address is not verified", status_code=403)
    if not _auth0_email_allowed(email):
        raise Auth0AuthorityRejected(f"{email} is not allowed for this dashboard", status_code=403)
    return {
        "sub": str(claims.get("sub") or ""),
        "email": email,
        "name": str(claims.get("name") or email),
        "email_verified": True,
    }


def auth0_login_state(
    *,
    state: str,
    nonce: str,
    return_to: str,
    issued_at: int | None = None,
) -> dict[str, Any]:
    """Mint opaque Auth0 login state/nonce cookies with the Safebox-owned cookie secret.

    Runtime planes may initiate a browser redirect, but they do not hold ``AUTH0_SECRET`` and cannot
    mint arbitrary dashboard sessions. This route only signs pre-login CSRF/nonce state; identity
    authority is completed by ``auth0_exchange_callback`` after Safebox verifies the Auth0 ID token.
    """
    state_value = str(state or "").strip()
    nonce_value = str(nonce or "").strip()
    if not state_value or not nonce_value:
        raise Auth0AuthorityRejected("auth0_state_nonce_required", status_code=400)
    body = {
        "state": state_value,
        "nonce": nonce_value,
        "return_to": _auth0_same_origin_path(return_to),
        "issued_at": int(issued_at if issued_at is not None else time.time()),
    }
    if _use_remote_authority():
        return _remote_json("POST", "/v1/auth0/login-state", body, timeout=10.0)
    secret = _auth0_secret()
    return {
        "state_token": _auth0_sign_payload(
            secret,
            {"state": state_value, "return_to": body["return_to"], "iat": body["issued_at"]},
        ),
        "nonce_token": _auth0_sign_payload(
            secret,
            {"nonce": nonce_value, "iat": body["issued_at"]},
        ),
        "return_to": body["return_to"],
    }


def _auth0_exchange_code(*, code: str, redirect_uri: str) -> dict[str, Any]:
    domain = _auth0_domain()
    client_id = _auth0_client_id()
    client_secret = _auth0_client_secret()
    payload = {
        "grant_type": "authorization_code",
        "client_id": client_id,
        "client_secret": client_secret,
        "code": str(code or ""),
        "redirect_uri": str(redirect_uri or ""),
    }
    if not payload["code"] or not payload["redirect_uri"]:
        raise Auth0AuthorityRejected("auth0_code_redirect_required", status_code=400)
    req = urllib.request.Request(
        f"{domain}/oauth/token",
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers={"Accept": "application/json", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=15.0) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="replace"))
    except urllib.error.HTTPError as exc:
        raise Auth0AuthorityRejected("auth0_token_exchange_failed", status_code=403) from exc
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        raise Auth0AuthorityRejected("auth0_token_exchange_unavailable", status_code=503) from exc
    if not isinstance(data, dict) or not data.get("id_token"):
        raise Auth0AuthorityRejected("Auth0 token response did not include an id_token", status_code=403)
    return data


def _auth0_verify_id_token(*, id_token: str, expected_nonce: str) -> dict[str, Any]:
    try:
        import jwt
        from jwt import PyJWKClient
    except ImportError as exc:  # pragma: no cover - dependency is pinned.
        raise Auth0AuthorityUnconfigured("PyJWT[crypto] is required for Auth0 validation") from exc

    domain = _auth0_domain()
    client_id = _auth0_client_id()
    try:
        jwks_client = PyJWKClient(f"{domain}/.well-known/jwks.json")
        signing_key = jwks_client.get_signing_key_from_jwt(id_token)
        claims = jwt.decode(
            id_token,
            signing_key.key,
            algorithms=["RS256"],
            audience=client_id,
            issuer=f"{domain}/",
            options={"require": ["exp", "iat", "iss", "aud", "sub"]},
        )
    except Exception as exc:
        raise Auth0AuthorityRejected("auth0_id_token_invalid", status_code=403) from exc
    if claims.get("nonce") != expected_nonce:
        raise Auth0AuthorityRejected("Auth0 nonce mismatch", status_code=400)
    return claims if isinstance(claims, dict) else {}


def auth0_exchange_callback(
    *,
    code: str,
    state: str,
    state_token: str,
    nonce_token: str,
    redirect_uri: str,
    now: int | None = None,
    state_max_age_seconds: int = 10 * 60,
    session_max_age_seconds: int = 12 * 60 * 60,
) -> dict[str, Any]:
    """Complete Auth0 login on the Safebox: verify state, exchange code, verify ID token, sign session."""
    body = {
        "code": str(code or ""),
        "state": str(state or ""),
        "state_token": str(state_token or ""),
        "nonce_token": str(nonce_token or ""),
        "redirect_uri": str(redirect_uri or ""),
        "now": now,
        "state_max_age_seconds": int(state_max_age_seconds),
        "session_max_age_seconds": int(session_max_age_seconds),
    }
    if _use_remote_authority():
        return _remote_json("POST", "/v1/auth0/callback", body, timeout=25.0)

    secret = _auth0_secret()
    state_payload = _auth0_unsign_payload(secret, body["state_token"])
    nonce_payload = _auth0_unsign_payload(secret, body["nonce_token"])
    current = int(now if now is not None else time.time())
    max_age = max(1, int(state_max_age_seconds))
    if (
        not state_payload
        or state_payload.get("state") != body["state"]
        or int(state_payload.get("iat") or 0) < current - max_age
    ):
        raise Auth0AuthorityRejected("Auth0 state mismatch", status_code=400)
    if (
        not nonce_payload
        or not nonce_payload.get("nonce")
        or int(nonce_payload.get("iat") or 0) < current - max_age
    ):
        raise Auth0AuthorityRejected("Auth0 nonce expired", status_code=400)
    token_data = _auth0_exchange_code(code=body["code"], redirect_uri=body["redirect_uri"])
    claims = _auth0_verify_id_token(
        id_token=str(token_data["id_token"]),
        expected_nonce=str(nonce_payload.get("nonce") or ""),
    )
    user = _auth0_authorize_claims(claims)
    ttl = max(60, min(24 * 60 * 60, int(session_max_age_seconds)))
    expires_at = current + ttl
    session_token = _auth0_sign_payload(secret, {**user, "iat": current, "exp": expires_at})
    return {
        "user": user,
        "session_token": session_token,
        "return_to": _auth0_same_origin_path(str(state_payload.get("return_to") or "/")),
        "expires_at": expires_at,
    }


def auth0_verify_session(*, session_token: str, now: int | None = None) -> dict[str, Any] | None:
    """Verify an Auth0 dashboard session token with the Safebox-owned cookie secret and policy."""
    token = str(session_token or "")
    if _use_remote_authority():
        payload = _remote_json("POST", "/v1/auth0/session/verify", {"session_token": token, "now": now})
        if not payload.get("authenticated"):
            return None
        user = payload.get("user")
        return user if isinstance(user, dict) else None

    secret = _auth0_secret()
    payload = _auth0_unsign_payload(secret, token)
    if not payload:
        return None
    current = int(now if now is not None else time.time())
    try:
        if int(payload.get("exp") or 0) < current:
            return None
    except (TypeError, ValueError):
        return None
    email = str(payload.get("email") or "").strip().lower()
    if payload.get("email_verified") is not True or not _auth0_email_allowed(email):
        return None
    return payload


def save_env_backed_value(key: str, value: str) -> None:
    """Persist one sensitive env-backed value through the Safebox authority."""
    if _use_remote_authority():
        raise SafeboxAuthorityUnavailable(
            "remote env mutation is disabled; provision Safebox secrets/config out-of-band on the authority host"
        )
    name = _require_sensitive(key)
    if _managed_secret_applies(name):
        raise ManagedSecretLookupError(
            f"{name} is owned by {_MANAGED_SECRET_COMMAND_ENV}; update it in the managed secret store"
        )
    if is_managed():
        managed_error(f"set {key}")
        return
    _save_env_value_direct(name, _normalize_env_value_for_storage(name, value))


def remove_env_backed_value(key: str) -> bool:
    """Remove one sensitive env-backed value through the Safebox authority."""
    if _use_remote_authority():
        raise SafeboxAuthorityUnavailable(
            "remote env mutation is disabled; provision Safebox secrets/config out-of-band on the authority host"
        )
    name = _require_sensitive(key)
    if _managed_secret_applies(name):
        raise ManagedSecretLookupError(
            f"{name} is owned by {_MANAGED_SECRET_COMMAND_ENV}; remove it in the managed secret store"
        )
    if is_managed():
        managed_error(f"remove {key}")
        return False
    return _remove_env_value_direct(name)


def sensitive_env_snapshot() -> Dict[str, str]:
    """Return the merged env-backed sensitive-key snapshot."""
    if _use_remote_authority():
        payload = _remote_json("GET", "/v1/env/snapshot")
        snapshot = payload.get("snapshot")
        if not isinstance(snapshot, dict):
            return {}
        return {
            str(key or "").strip(): str(value or "").strip()
            for key, value in snapshot.items()
            if str(key or "").strip()
        }
    snapshot = {
        key: str(value or "").strip()
        for key, value in load_env().items()
        if is_sensitive_env_key(key)
    }
    for key, value in os.environ.items():
        if is_sensitive_env_key(key):
            snapshot[key] = str(value or "").strip()
    for key in _managed_secret_keys():
        try:
            value = read_env_backed_value(key)
        except (KeyError, ManagedSecretLookupError):
            continue
        if value:
            snapshot[key] = value
    return snapshot


def list_env_backed_keys(*, sensitive_only: bool = True) -> list[str]:
    """List env-backed keys known to Safebox."""
    if _use_remote_authority():
        flag = "1" if sensitive_only else "0"
        payload = _remote_json("GET", f"/v1/env?{urllib.parse.urlencode({'sensitive_only': flag})}")
        keys = payload.get("keys")
        return [str(item or "").strip() for item in keys] if isinstance(keys, list) else []
    if sensitive_only:
        return sorted(sensitive_env_snapshot().keys())
    merged = {key: value for key, value in load_env().items()}
    for key, value in os.environ.items():
        merged[key] = value
    for key in _managed_secret_keys():
        merged[key] = "<managed>"
    return sorted(str(key or "").strip() for key in merged.keys() if str(key or "").strip())
