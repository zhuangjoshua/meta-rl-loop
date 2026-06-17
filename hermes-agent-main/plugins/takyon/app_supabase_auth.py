"""Supabase Auth verifier for product sub-users (AUTH0.md §7).

The sub-app front door is Supabase Auth (Google OAuth + email) on the SAME project that hosts the
control-plane Postgres. The browser completes the Supabase OAuth flow and receives a Supabase-issued
access token (a JWT); this leaf VERIFIES that JWT server-side and returns the stable identity
(``sub`` = ``auth.users`` uuid, plus email). The runtime then upserts an ``app_users`` row keyed on
``(business_slug, supabase_user_id)`` and mints the SAME ``app_session`` the magic-link path mints,
so ``validate_session``, the ``tkg_`` gateway, and reserve/settle metering are UNCHANGED.

Operators are unaffected: this is sub-app/customer auth only.

Verification follows Supabase's current guidance:

- Asymmetric tokens (for example ES256 / RS256) verify locally against the project's JWKS endpoint.
- Legacy shared-secret tokens (HS256) verify either with an explicit/local JWT secret, or by asking
  the Supabase Auth server to validate the token and return the user.

Fail-closed: a missing token, missing project config, bad signature, wrong audience, expiry, or Auth
server rejection raises ``SupabaseAuthError`` — never a partial result a caller could mistake for
success.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from functools import lru_cache

# Supabase stamps `aud: "authenticated"` on a signed-in user's access token.
_DEFAULT_AUDIENCE = "authenticated"
_JWKS_CACHE_SECONDS = 600


class SupabaseAuthError(Exception):
    """A Supabase access token is missing, malformed, or fails verification."""


@dataclass(frozen=True)
class SupabaseIdentity:
    """The verified identity behind a Supabase access token."""

    supabase_user_id: str  # auth.users.id (uuid) — the stable join key
    email: str | None
    raw_claims: dict


def _env_or_safebox(*names: str) -> str:
    resolved_names: list[str] = []
    for raw_name in names:
        name = str(raw_name or "").strip()
        if not name:
            continue
        resolved_names.append(name)
        direct = str(os.getenv(name) or "").strip()
        if direct:
            return direct
    if not resolved_names:
        return ""
    try:
        from . import safebox

        value = str(safebox.first_env_backed_value(*resolved_names) or "").strip()
    except Exception:
        value = ""
    if value:
        return value
    for name in resolved_names:
        try:
            value = str(safebox.read_env_backed_value(name) or "").strip()
        except Exception:
            value = ""
        if value:
            return value
    return ""


def _project_url() -> str:
    return _env_or_safebox(
        "SUPABASE_URL",
        "NEXT_PUBLIC_SUPABASE_URL",
        "TAKYON_SUPABASE_URL",
    )


def _publishable_key() -> str:
    return _env_or_safebox(
        "SUPABASE_PUBLISHABLE_KEY",
        "SUPABASE_ANON_KEY",
        "NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY",
        "NEXT_PUBLIC_SUPABASE_ANON_KEY",
    )


def _jwt_secret() -> str:
    """The project JWT secret, resolved server-side (safebox-aware, then env). Sensitive — never an
    argument the browser supplies, never returned. "" when unconfigured → callers must block."""
    try:
        from . import safebox

        if safebox.is_sensitive_env_key("SUPABASE_JWT_SECRET"):
            value = safebox.read_env_backed_value("SUPABASE_JWT_SECRET")
            if value:
                return str(value).strip()
    except Exception:
        pass
    return str(os.getenv("SUPABASE_JWT_SECRET") or "").strip()


@lru_cache(maxsize=8)
def _jwks_client(jwks_url: str):
    import jwt  # PyJWT

    return jwt.PyJWKClient(
        jwks_url,
        cache_jwk_set=True,
        lifespan=_JWKS_CACHE_SECONDS,
        cache_keys=False,
        timeout=10,
    )


def _jwks_url(project_url: str | None = None) -> str:
    base = str(project_url or _project_url() or "").strip().rstrip("/")
    if not base:
        raise SupabaseAuthError("SUPABASE_URL is not configured")
    return f"{base}/auth/v1/.well-known/jwks.json"


def _decode_hs_token(
    token: str,
    *,
    secret: str,
    audience: str | None,
    leeway: int,
):
    import jwt  # PyJWT

    return jwt.decode(
        token,
        secret,
        algorithms=["HS256"],
        audience=audience,
        leeway=leeway,
        options={"require": ["exp", "sub"]},
    )


def _decode_asymmetric_token(
    token: str,
    *,
    audience: str | None,
    leeway: int,
    jwks_url: str,
):
    import jwt  # PyJWT

    signing_key = _jwks_client(jwks_url).get_signing_key_from_jwt(token)
    return jwt.decode(
        token,
        signing_key.key,
        algorithms=["ES256", "RS256", "EdDSA"],
        audience=audience,
        leeway=leeway,
        options={"require": ["exp", "sub"]},
    )


def _verified_user_via_auth_server(
    token: str,
    *,
    project_url: str,
    publishable_key: str,
) -> dict:
    request = urllib.request.Request(
        f"{project_url.rstrip('/')}/auth/v1/user",
        method="GET",
        headers={
            "Accept": "application/json",
            "apikey": publishable_key,
            "Authorization": f"Bearer {token}",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            raw = response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace").strip() or exc.reason
        raise SupabaseAuthError(f"invalid supabase token: auth server rejected it ({detail})") from exc
    except OSError as exc:
        raise SupabaseAuthError(f"invalid supabase token: auth server verification failed ({exc})") from exc
    try:
        payload = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError as exc:
        raise SupabaseAuthError("invalid supabase token: auth server returned invalid JSON") from exc
    if not isinstance(payload, dict):
        raise SupabaseAuthError("invalid supabase token: auth server returned an unexpected payload")
    return payload


def _identity_from_claims(claims: dict) -> SupabaseIdentity:
    sub = str(claims.get("sub") or claims.get("id") or "").strip()
    if not sub:
        raise SupabaseAuthError("supabase token has no subject")
    email = claims.get("email")
    return SupabaseIdentity(
        supabase_user_id=sub,
        email=(str(email).strip().lower() if email else None),
        raw_claims=claims,
    )


def verify_supabase_jwt(
    token: str,
    *,
    secret: str | None = None,
    audience: str | None = _DEFAULT_AUDIENCE,
    leeway: int = 0,
    project_url: str | None = None,
) -> SupabaseIdentity:
    """Verify a Supabase-issued access token (JWT) and return its identity, FAIL-CLOSED.

    ``exp`` and ``sub`` are required; the audience must match (default ``"authenticated"``).
    Raises ``SupabaseAuthError`` on a missing token/config, bad signature, wrong audience, or
    expiry. Returns the ``auth.users`` uuid and the (lower-cased) email."""
    import jwt  # PyJWT

    raw = str(token or "").strip()
    if not raw:
        raise SupabaseAuthError("missing access token")
    try:
        header = jwt.get_unverified_header(raw)
    except jwt.PyJWTError as exc:
        raise SupabaseAuthError(f"invalid supabase token: {exc}") from exc
    algorithm = str(header.get("alg") or "").strip().upper()
    project = str(project_url or _project_url() or "").strip()
    try:
        if algorithm.startswith("HS"):
            key = secret if secret is not None else _jwt_secret()
            if key:
                claims = _decode_hs_token(raw, secret=key, audience=audience, leeway=leeway)
                return _identity_from_claims(claims)
            publishable_key = _publishable_key()
            if not project:
                raise SupabaseAuthError("SUPABASE_URL is not configured")
            if not publishable_key:
                raise SupabaseAuthError(
                    "SUPABASE_PUBLISHABLE_KEY or SUPABASE_ANON_KEY is not configured"
                )
            claims = dict(jwt.decode(raw, options={"verify_signature": False}))
            user_payload = _verified_user_via_auth_server(
                raw,
                project_url=project,
                publishable_key=publishable_key,
            )
            claims.setdefault("sub", user_payload.get("id"))
            claims.setdefault("email", user_payload.get("email"))
            return _identity_from_claims(claims)

        claims = _decode_asymmetric_token(
            raw,
            audience=audience,
            leeway=leeway,
            jwks_url=_jwks_url(project),
        )
        return _identity_from_claims(claims)
    except jwt.PyJWTError as exc:
        raise SupabaseAuthError(f"invalid supabase token: {exc}") from exc
