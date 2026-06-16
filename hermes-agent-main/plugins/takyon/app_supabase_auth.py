"""Supabase Auth verifier for product sub-users (AUTH0.md §7).

The sub-app front door is Supabase Auth (Google OAuth + email) on the SAME project that hosts the
control-plane Postgres. The browser completes the Supabase OAuth flow and receives a Supabase-issued
access token (a JWT); this leaf VERIFIES that JWT server-side and returns the stable identity
(``sub`` = ``auth.users`` uuid, plus email). The runtime then upserts an ``app_users`` row keyed on
(business_slug, supabase_user_id) via ``app_identity.upsert_app_user_by_supabase_id`` and mints the
SAME ``app_session`` the magic-link path mints — so ``validate_session``, the ``tkg_`` gateway, and
reserve/settle metering are UNCHANGED.

Operators are unaffected: this is sub-app/customer auth only.

Fail-closed: a missing secret, missing/empty token, bad signature, wrong audience, or expiry raises
``SupabaseAuthError`` — never a partial result a caller could mistake for success.

Signing: HS256 against the project JWT secret (``SUPABASE_JWT_SECRET``) — the Supabase default.
Pure: no DB, no network, so it unit-tests with a locally-minted token (see
``tests/plugins/test_takyon_supabase_auth.py``). Live use additionally requires Supabase Auth +
the Google provider enabled on the project, and ``SUPABASE_JWT_SECRET`` provisioned to the runtime.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

# Supabase stamps `aud: "authenticated"` on a signed-in user's access token.
_DEFAULT_AUDIENCE = "authenticated"


class SupabaseAuthError(Exception):
    """A Supabase access token is missing, malformed, or fails verification."""


@dataclass(frozen=True)
class SupabaseIdentity:
    """The verified identity behind a Supabase access token."""

    supabase_user_id: str  # auth.users.id (uuid) — the stable join key
    email: str | None
    raw_claims: dict


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


def verify_supabase_jwt(
    token: str,
    *,
    secret: str | None = None,
    audience: str | None = _DEFAULT_AUDIENCE,
    leeway: int = 0,
) -> SupabaseIdentity:
    """Verify a Supabase-issued access token (JWT) and return its identity, FAIL-CLOSED.

    HS256 against ``SUPABASE_JWT_SECRET`` (or an explicit ``secret``, for tests). ``exp`` and
    ``sub`` are required; the audience must match (default ``"authenticated"``). Raises
    ``SupabaseAuthError`` on a missing secret/token, bad signature, wrong audience, or expiry.
    Returns the ``auth.users`` uuid and the (lower-cased) email. Pure — no DB, no network."""
    import jwt  # PyJWT

    raw = str(token or "").strip()
    if not raw:
        raise SupabaseAuthError("missing access token")
    key = secret if secret is not None else _jwt_secret()
    if not key:
        raise SupabaseAuthError("SUPABASE_JWT_SECRET is not configured")
    try:
        claims = jwt.decode(
            raw,
            key,
            algorithms=["HS256"],
            audience=audience,
            leeway=leeway,
            options={"require": ["exp", "sub"]},
        )
    except jwt.PyJWTError as exc:
        raise SupabaseAuthError(f"invalid supabase token: {exc}") from exc

    sub = str(claims.get("sub") or "").strip()
    if not sub:
        raise SupabaseAuthError("supabase token has no subject")
    email = claims.get("email")
    return SupabaseIdentity(
        supabase_user_id=sub,
        email=(str(email).strip().lower() if email else None),
        raw_claims=claims,
    )
