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

from .user_api_keys import hash_api_key, is_well_formed, key_prefix
from .business_credits import (
    CreativeCreditBalances,
    CreativeCreditReservation,
    InsufficientCreativeCredits,
    UnknownCreativeCreditReservation,
)

_EXACT_SENSITIVE_ENV_KEYS = frozenset(
    {
        "AUTH0_CLIENT_SECRET",
        "AUTH0_SECRET",
        "DATABASE_URL",
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
_HOST_ROLE_ENV = "TAKYON_HOST_ROLE"
_SAFEBOX_HOST_ROLE = "safebox"


class RemoteSafeboxError(RuntimeError):
    """A Safebox remote request failed with a concrete HTTP status/payload."""

    def __init__(self, message: str, *, status_code: int, payload: dict[str, Any]):
        super().__init__(message)
        self.status_code = int(status_code)
        self.payload = payload


class SafeboxAuthorityUnavailable(RuntimeError):
    """No remote Safebox is configured and this process is not the Safebox host."""


class StripeBillingWebhookUnconfigured(RuntimeError):
    """Billing webhook verification is unavailable because Safebox lacks the secret."""


class StripeBillingWebhookInvalidSignature(RuntimeError):
    """The presented Stripe billing webhook signature failed verification."""


class StripeAppWebhookUnconfigured(RuntimeError):
    """App (flow-B) webhook verification is unavailable because Safebox lacks the secret."""


class StripeAppWebhookInvalidSignature(RuntimeError):
    """The presented Stripe app (flow-B) webhook signature failed verification."""


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
    return str(os.environ.get(_HOST_ROLE_ENV) or "").strip().lower()


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


def _remote_headers(*, with_json: bool = False) -> dict[str, str]:
    headers: dict[str, str] = {"Accept": "application/json"}
    token = str(os.environ.get(_SAFEBOX_REMOTE_TOKEN_ENV) or "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if with_json:
        headers["Content-Type"] = "application/json"
    return headers


def _remote_json(
    method: str, path: str, payload: dict[str, Any] | None = None, *, timeout: float = 10.0
) -> dict[str, Any]:
    base = _remote_base_url()
    if not base:
        raise RuntimeError("Safebox remote URL is not configured")
    body: bytes | None = None
    headers = _remote_headers(with_json=payload is not None)
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(f"{base}{path}", data=body, method=method.upper(), headers=headers)
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
            f"Safebox remote {method.upper()} {path} failed: {parsed}",
            status_code=exc.code,
            payload=parsed if isinstance(parsed, dict) else {"detail": detail},
        ) from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        # Transport failure (timeout / connection refused / DNS), NOT an HTTP status — HTTPError is a
        # URLError subclass and is handled above, so this only catches unreachable-safebox cases. Fail
        # closed as a 504 so a brokered provider call surfaces a clean upstream error and never falls
        # back to a raw key.
        raise RemoteSafeboxError(
            f"Safebox remote {method.upper()} {path} unreachable: {exc}",
            status_code=504,
            payload={"detail": "safebox_unreachable"},
        ) from exc


def _public_config_value(name: str) -> str:
    value = os.environ.get(name)
    if value is not None:
        return str(value).strip()
    return str(load_env().get(name) or "").strip()


def stripe_request(path: str, params: dict[str, Any] | None = None, *, method: str = "POST") -> dict[str, Any]:
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
        payload = _remote_json(
            "POST",
            "/v1/stripe/request",
            {"path": stripe_path, "params": dict(params or {}), "method": stripe_method},
            timeout=35.0,
        )
        return payload if isinstance(payload, dict) else {}
    from . import stripe_util

    return stripe_util.stripe_request(stripe_path, dict(params or {}), method=stripe_method)


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
    ("tavily", "search"): "/v1/providers/tavily/search",
    ("gemini", "image"): "/v1/providers/gemini/image",
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
_OPERATOR_SESSION_DEFAULT_MAX_COST_MICROUSD = 2_000_000


def mint_operator_session_token(
    business: str,
    operator_user_id: str,
    *,
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

    Uses the same internal-token transport (``_remote_json`` -> ``/v1/operator/session-token``) as the
    other broker clients. Fails CLOSED: raises ``RemoteSafeboxError`` when the safebox is unreachable,
    refuses the mint (e.g. the operator does not own the business), or returns no token — it NEVER falls
    back to a raw key. The caller MUST treat any exception as "no key-free auth" and refuse the run."""
    slug = str(business or "").strip()
    owner = str(operator_user_id or "").strip()
    if not slug or not owner:
        raise RemoteSafeboxError(
            "operator session token requires both a business and an owner operator_user_id",
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
    stream: bool = False,
    timeout: float = _PROVIDER_PROXY_TIMEOUT_S,
):
    """Call the operator/platform provider PROXY at ``/v1/proxy/<provider>/<path>`` and return the
    KEY-FREE result.

    Non-streaming: POSTs JSON via the existing internal-token transport (``_remote_json``) and returns
    the parsed JSON dict. Streaming (``stream=True``): yields raw response bytes (the verbatim SSE
    stream) so a caller can re-emit the provider event stream.

    The provider KEY never reaches this process: the safebox resolves it locally and forwards. Fails
    closed (``RemoteSafeboxError`` / ``SafeboxAuthorityUnavailable``) — it NEVER falls back to a raw key.
    """
    prov = str(provider or "").strip().strip("/")
    sub = str(path or "").strip().strip("/")
    if not prov:
        raise ValueError("provider is required")
    route = f"/v1/proxy/{prov}" + (f"/{sub}" if sub else "")
    base = _remote_base_url()
    if not base:
        # The proxy is remote-only and must never quietly fall back to a local raw key.
        raise SafeboxAuthorityUnavailable(
            f"provider proxy requires {_SAFEBOX_REMOTE_URL_ENV}; not set on this plane"
        )
    if not stream:
        return _remote_json("POST", route, dict(payload or {}), timeout=timeout)
    return _proxy_stream_bytes(base + route, dict(payload or {}), timeout=timeout)


def _proxy_stream_bytes(url: str, payload: dict[str, Any], *, timeout: float):
    """Yield the verbatim response bytes from a streaming proxy POST (e.g. the Anthropic SSE stream),
    using the same internal-token bearer as ``_remote_json``. Fails closed as ``RemoteSafeboxError`` on
    an HTTP error status or a transport failure — never falls back to a raw key."""
    headers = _remote_headers(with_json=True)
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
    from .runtime_app import resolve_database_url
    import psycopg

    raw_conn = psycopg.connect(
        resolve_database_url(),
        autocommit=True,
        prepare_threshold=None,
    )
    try:
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


def _local_grant_starter_allowance(conn, user_id: str) -> int:
    included_cents = _starter_allowance_cents()
    if included_cents <= 0:
        return 0
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
            f"starter-allowance:{user_id}",
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


def grant_starter_allowance(conn, user_id: str) -> int:
    """Grant the one-time starter allowance, with replay/balance checks inside the safebox."""
    user_ref = str(user_id or "").strip()
    if not user_ref:
        raise ValueError("missing user_id")
    if _remote_enabled() and not _local_authority_enabled():
        payload = _remote_json(
            "POST",
            "/v1/billing/starter-allowance",
            {"user_id": user_ref},
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
    return event if isinstance(event, dict) else {}


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
) -> CreativeCreditBalances:
    """Commit (settle) the ONE creative-credit reservation on the safebox after the action succeeds."""
    key = str(reservation_key or "").strip()
    if not key:
        raise ValueError("reservation_key is required")
    body = {"reservation_key": key, "actual_credits": (None if actual_credits is None else int(actual_credits))}
    if _use_remote_authority():
        try:
            payload = _remote_json("POST", "/v1/creative/commit", body, timeout=_CREATIVE_GATE_TIMEOUT_S)
        except RemoteSafeboxError as exc:
            if exc.status_code == 404:
                raise UnknownCreativeCreditReservation(key) from exc
            raise
        return _balances_from_payload(payload, business_slug="")
    return _local_commit_credits(None, key, actual_credits=actual_credits, metadata={"via": "safebox_creative_gate"})


def creative_release(*, reservation_key: str) -> CreativeCreditBalances:
    """Release the ONE creative-credit reservation on the safebox after the action fails."""
    key = str(reservation_key or "").strip()
    if not key:
        raise ValueError("reservation_key is required")
    body = {"reservation_key": key}
    if _use_remote_authority():
        try:
            payload = _remote_json("POST", "/v1/creative/release", body, timeout=_CREATIVE_GATE_TIMEOUT_S)
        except RemoteSafeboxError as exc:
            if exc.status_code == 404:
                raise UnknownCreativeCreditReservation(key) from exc
            raise
        return _balances_from_payload(payload, business_slug="")
    return _local_release_credits(None, key, metadata={"via": "safebox_creative_gate"})


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
    value = os.environ.get(name)
    if value is not None:
        return value.strip()
    return str(load_env().get(name) or "").strip()


def first_env_backed_value(*keys: str) -> str:
    """Return the first non-empty env-backed value across explicit aliases.

    Sensitive keys still flow through the normal Safebox authority gate. For authenticated internal
    callers that request a short allowlist of public aliases (for example Supabase browser config),
    this also falls back to the local env file on the Safebox host instead of failing closed on the
    sensitive-only reader.
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
            # Non-sensitive public alias (for example DATABASE_URL): the
            # sensitive reader refuses it. Resolve from the process env first —
            # which systemd loads from .env and _save_env_value_direct keeps in
            # sync — then the parsed file, so resolution survives an unreadable
            # .env rather than collapsing to "" and breaking DB-URL lookup.
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
        _remote_json(
            "POST",
            f"/v1/env/{urllib.parse.quote(_require_sensitive(key), safe='')}",
            {"value": value},
        )
        return
    if is_managed():
        managed_error(f"set {key}")
        return
    name = _require_sensitive(key)
    _save_env_value_direct(name, _normalize_env_value_for_storage(name, value))


def remove_env_backed_value(key: str) -> bool:
    """Remove one sensitive env-backed value through the Safebox authority."""
    if _use_remote_authority():
        payload = _remote_json(
            "DELETE",
            f"/v1/env/{urllib.parse.quote(_require_sensitive(key), safe='')}",
        )
        return bool(payload.get("removed"))
    if is_managed():
        managed_error(f"remove {key}")
        return False
    name = _require_sensitive(key)
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
    return sorted(str(key or "").strip() for key in merged.keys() if str(key or "").strip())
