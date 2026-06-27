"""Product sub-user identity + sessions.

These are the customers OF a business the Takyon user runs (product sub-users), NOT
the top-level Takyon operator — that identity lives in `control_plane.py` /
`user_api_keys.py`. Everything here is scoped by `business_slug`: a sub-user belongs to
exactly one business, an email is unique only within that business, and a session is
only valid for the business it was minted in. Supabase Auth verifies the customer; this
module owns the guarded state change that binds that verified identity to an `app_user`
and mints/validates/revokes the Takyon app session.

Raw session tokens are never stored — only their SHA-256 hex hash (identical to the
SQLite `_hash_token`, so a ported app keeps working). A session is a 30-day bearer token.

House style (matches billing.py / custody.py / policy.py): pure leaf, takes a psycopg
connection, imports no psycopg, opens its own `conn.transaction()` per mutating op, and
raises typed errors on broken preconditions rather than returning sentinels. An unknown
business fails loud through the FK to businesses(slug).
"""

from __future__ import annotations

import hashlib
import re
import secrets
from dataclasses import dataclass

_DEFAULT_SESSION_TTL_DAYS = 30
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_VALID_APP_USER_STATUSES = {"active", "suspended", "closed"}
UNENTITLED_TIER = "unentitled"


class AppIdentityError(Exception):
    """Base for product sub-user identity errors."""


class InvalidEmail(AppIdentityError):
    """The supplied email is missing or malformed."""


class InactiveAppUser(AppIdentityError):
    """The sub-user exists but is suspended/closed, so cannot start a session."""


@dataclass(frozen=True)
class AppUser:
    """One product sub-user (a business's customer).

    `tier` is the cached effective access tier. Unpaid users stay `unentitled`; access-bearing
    tiers come from the entitlement rail rather than being bootstrapped here."""

    id: str
    business_slug: str
    email: str
    name: str | None
    status: str
    tier: str


@dataclass(frozen=True)
class AppSession:
    """A redeemed bearer session. The raw session token is returned alongside this
    record exactly once and is not a field here."""

    id: str
    business_slug: str
    app_user_id: str
    expires_at: object


def _hash_token(token: str) -> str:
    """SHA-256 hex of a raw token — the only form stored. Matches the SQLite trunk's
    `_hash_token` so links/sessions minted by either path verify identically."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _random_token() -> str:
    """A fresh opaque token (URL-safe, 32 bytes of entropy)."""
    return secrets.token_urlsafe(32)


def _normalize_email(value: str) -> str:
    """Lowercase + trim + validate. Stored values stay tidy; citext makes lookups
    case-insensitive regardless, but normalizing on write keeps the data clean."""
    email = str(value or "").strip().lower()
    if not _EMAIL_RE.match(email):
        raise InvalidEmail("valid email is required")
    return email


def _first_col(row, default=None):
    if row is None:
        return default
    if isinstance(row, dict):
        return next(iter(row.values()), default)
    return row[0] if len(row) else default


def _row_item(row, index: int, key: str, default=None):
    if row is None:
        return default
    if isinstance(row, dict):
        return row.get(key, default)
    return row[index] if len(row) > index else default


def _current_user(conn) -> str:
    try:
        row = conn.execute("select current_user").fetchone()
    except Exception:
        return ""
    return str(_first_col(row, "") or "").strip()


def _is_app_runtime_user(conn) -> bool:
    return _current_user(conn) in {"takyon_app", "takyon_app_runtime"}


def _normalize_status(value: str | None) -> str:
    status = str(value or "active").strip().lower()
    if status not in _VALID_APP_USER_STATUSES:
        raise ValueError("status must be one of active, suspended, or closed")
    return status


def _app_user_from_row(row) -> AppUser:
    return AppUser(
        id=str(_row_item(row, 0, "id", _row_item(row, 0, "app_user_id"))),
        business_slug=str(_row_item(row, 1, "business_slug")),
        email=str(_row_item(row, 2, "email")),
        name=None if _row_item(row, 3, "name") is None else str(_row_item(row, 3, "name")),
        status=str(_row_item(row, 4, "status")),
        tier=str(_row_item(row, 5, "tier")),
    )


_APP_USER_COLUMNS = "id, business_slug, email, name, status, tier"


def upsert_app_user(
    conn,
    business_slug: str,
    email: str,
    *,
    name: str | None = None,
    status: str | None = None,
) -> AppUser:
    """Create or update a sub-user for (business, email).

    New rows default to ``active`` unless an explicit status is supplied. Existing rows keep their
    current status unless an explicit status is supplied, so a suspended/closed user is not
    silently reactivated by login or billing side paths. When an explicit non-active status is
    written, all live sessions for that sub-user are revoked in the same transaction."""
    normalized = _normalize_email(email)
    status_value = None if status is None else _normalize_status(status)
    with conn.transaction():
        row = conn.execute(
            "insert into app_users (business_slug, email, name, status, tier) values (%s, %s, %s, %s, %s) "
            "on conflict (business_slug, email) do update set "
            " status = coalesce(%s, app_users.status), "
            " name = coalesce(excluded.name, app_users.name), "
            " updated_at = now() "
            f"returning {_APP_USER_COLUMNS}",
            (
                business_slug,
                normalized,
                name,
                status_value or "active",
                UNENTITLED_TIER,
                status_value,
            ),
        ).fetchone()
        user = _app_user_from_row(row)
        if user.status != "active":
            conn.execute(
                "update app_sessions set revoked_at = now() "
                "where business_slug = %s and app_user_id = %s and revoked_at is null",
                (business_slug, user.id),
            )
            row = conn.execute(
                f"select {_APP_USER_COLUMNS} from app_users where business_slug = %s and id = %s",
                (business_slug, user.id),
            ).fetchone()
            user = _app_user_from_row(row)
    return user


def get_app_user(
    conn, business_slug: str, *, app_user_id: str | None = None, email: str | None = None
) -> AppUser | None:
    """Look up a sub-user by id or email within a business, or None. Pure read."""
    if app_user_id is not None:
        row = conn.execute(
            f"select {_APP_USER_COLUMNS} from app_users "
            "where business_slug = %s and id = %s",
            (business_slug, app_user_id),
        ).fetchone()
    elif email is not None:
        row = conn.execute(
            f"select {_APP_USER_COLUMNS} from app_users "
            "where business_slug = %s and email = %s",
            (business_slug, _normalize_email(email)),
        ).fetchone()
    else:
        raise ValueError("get_app_user requires app_user_id or email")
    return None if row is None else _app_user_from_row(row)


def start_session(
    conn,
    business_slug: str,
    app_user_id: str,
    *,
    session_ttl_days: int = _DEFAULT_SESSION_TTL_DAYS,
) -> tuple[AppSession, str]:
    """Mint a 30-day bearer session for an already-resolved sub-user. Returns
    (AppSession, raw_session_token); only the hash is stored. Refuses a suspended/closed user
    (InactiveAppUser). The Supabase login path calls this after verifying the access token."""
    if not isinstance(session_ttl_days, int) or session_ttl_days <= 0:
        raise ValueError("session_ttl_days must be a positive integer")
    raw_session = _random_token()
    with conn.transaction():
        status_row = conn.execute(
            "select status from app_users where business_slug = %s and id = %s",
            (business_slug, app_user_id),
        ).fetchone()
        if status_row is None or str(status_row[0]) != "active":
            raise InactiveAppUser(str(app_user_id))
        row = conn.execute(
            "insert into app_sessions (business_slug, app_user_id, token_hash, expires_at) "
            "values (%s, %s, %s, now() + make_interval(days => %s)) "
            "returning id, expires_at",
            (business_slug, app_user_id, _hash_token(raw_session), session_ttl_days),
        ).fetchone()
    session = AppSession(
        id=str(row[0]),
        business_slug=business_slug,
        app_user_id=str(app_user_id),
        expires_at=row[1],
    )
    return session, raw_session


def start_supabase_session(
    conn,
    business_slug: str,
    supabase_user_id: str,
    email: str | None,
    *,
    name: str | None = None,
    session_ttl_days: int = _DEFAULT_SESSION_TTL_DAYS,
) -> tuple[AppUser, AppSession, str]:
    """Bind a verified Supabase identity and mint one Takyon app session.

    On the final app plane, the login role has no broad DML on app_users/app_sessions. It calls the
    bounded SECURITY DEFINER port instead, passing only the session hash so the raw bearer token is
    returned to the browser once and never stored.
    """
    sub = str(supabase_user_id or "").strip()
    if not sub:
        raise ValueError("supabase_user_id is required")
    if not isinstance(session_ttl_days, int) or session_ttl_days <= 0:
        raise ValueError("session_ttl_days must be a positive integer")
    raw_session = _random_token()
    if _is_app_runtime_user(conn):
        row = conn.execute(
            "select app_user_id, business_slug, email, name, status, tier, session_id, session_expires_at "
            "from takyon_app_bind_supabase_session(%s, %s, %s, %s, %s, %s)",
            (
                business_slug,
                sub,
                _normalize_email(email) if email else None,
                name,
                _hash_token(raw_session),
                session_ttl_days,
            ),
        ).fetchone()
        if row is None:
            raise AppIdentityError("supabase session bind returned no row")
        user = _app_user_from_row(row)
        session = AppSession(
            id=str(_row_item(row, 6, "session_id")),
            business_slug=str(_row_item(row, 1, "business_slug")),
            app_user_id=str(_row_item(row, 0, "app_user_id")),
            expires_at=_row_item(row, 7, "session_expires_at"),
        )
        return user, session, raw_session

    user = upsert_app_user_by_supabase_id(
        conn,
        business_slug,
        sub,
        email,
        name=name,
    )
    try:
        from plugins.takyon import app_entitlements

        tier = app_entitlements.resolve_user_tier(conn, business_slug, user.id)
        user = AppUser(
            id=user.id,
            business_slug=user.business_slug,
            email=user.email,
            name=user.name,
            status=user.status,
            tier=tier,
        )
    except Exception:
        pass
    session, raw_session = start_session(
        conn,
        business_slug,
        user.id,
        session_ttl_days=session_ttl_days,
    )
    return user, session, raw_session


def upsert_app_user_by_supabase_id(
    conn,
    business_slug: str,
    supabase_user_id: str,
    email: str | None,
    *,
    name: str | None = None,
) -> AppUser:
    """Upsert a sub-user for a VERIFIED Supabase identity (AUTH0.md §7).

    Resolution order: (1) an existing row already bound to this ``supabase_user_id``; else (2) a
    pre-existing email-only row for (business, email) with no supabase id is ADOPTED — its
    ``supabase_user_id`` is set — so a customer keeps the same identity, entitlements, and usage
    history on their first Supabase login; else (3) a brand-new ``active`` sub-user. A suspended/closed
    user is never reactivated here, and the caller only mints a session via ``start_session``, which
    refuses non-active users.

    The caller MUST have verified the Supabase token first (``app_supabase_auth.verify_supabase_jwt``)
    — this never trusts a raw subject/email straight off the wire."""
    sub = str(supabase_user_id or "").strip()
    if not sub:
        raise ValueError("supabase_user_id is required")
    normalized = _normalize_email(email) if email else None
    with conn.transaction():
        row = conn.execute(
            f"select {_APP_USER_COLUMNS} from app_users "
            "where business_slug = %s and supabase_user_id = %s",
            (business_slug, sub),
        ).fetchone()
        if row is None and normalized is not None:
            row = conn.execute(
                "update app_users set supabase_user_id = %s, "
                " name = coalesce(%s, name), updated_at = now() "
                "where business_slug = %s and email = %s and supabase_user_id is null "
                f"returning {_APP_USER_COLUMNS}",
                (sub, name, business_slug, normalized),
            ).fetchone()
        if row is None:
            row = conn.execute(
                "insert into app_users (business_slug, email, name, status, tier, supabase_user_id) "
                "values (%s, %s, %s, 'active', %s, %s) "
                f"returning {_APP_USER_COLUMNS}",
                (business_slug, normalized or f"{sub}@supabase.local", name, UNENTITLED_TIER, sub),
            ).fetchone()
    return _app_user_from_row(row)


def validate_session(conn, business_slug: str, raw_session_token: str) -> AppUser | None:
    """Resolve a presented session token to its sub-user, or None. A session counts as
    valid only while it is unrevoked, unexpired, and its sub-user is active. Pure read —
    the entire product boundary keys off this. None for missing/garbage tokens."""
    token = str(raw_session_token or "").strip()
    if not token:
        return None
    if _is_app_runtime_user(conn):
        row = conn.execute(
            "select app_user_id, business_slug, email, name, status, tier "
            "from takyon_app_validate_session(%s, %s)",
            (business_slug, _hash_token(token)),
        ).fetchone()
        return None if row is None else _app_user_from_row(row)
    row = conn.execute(
        f"select {', '.join('u.' + c for c in _APP_USER_COLUMNS.split(', '))} "
        "from app_sessions s join app_users u on u.id = s.app_user_id "
        "where s.business_slug = %s and s.token_hash = %s "
        "  and s.revoked_at is null and s.expires_at > now() "
        "  and u.status = 'active' limit 1",
        (business_slug, _hash_token(token)),
    ).fetchone()
    return None if row is None else _app_user_from_row(row)


def revoke_session(conn, business_slug: str, raw_session_token: str) -> bool:
    """Revoke a session by its token. Returns True if a live session was revoked, False
    if there was nothing to revoke (unknown or already revoked) — idempotent."""
    token = str(raw_session_token or "").strip()
    if not token:
        return False
    if _is_app_runtime_user(conn):
        row = conn.execute(
            "select takyon_app_revoke_session(%s, %s)",
            (business_slug, _hash_token(token)),
        ).fetchone()
        return bool(_first_col(row, False))
    with conn.transaction():
        row = conn.execute(
            "update app_sessions set revoked_at = now() "
            "where business_slug = %s and token_hash = %s and revoked_at is null "
            "returning id",
            (business_slug, _hash_token(token)),
        ).fetchone()
    return row is not None


def revoke_app_user_sessions(conn, business_slug: str, app_user_id: str) -> int:
    """Revoke every live session for one sub-user. Returns the number of sessions revoked."""
    with conn.transaction():
        row = conn.execute(
            "update app_sessions set revoked_at = now() "
            "where business_slug = %s and app_user_id = %s and revoked_at is null "
            "returning id",
            (business_slug, app_user_id),
        ).fetchall()
    return len(row)


def set_app_user_status(conn, business_slug: str, app_user_id: str, status: str) -> AppUser:
    """Set one sub-user's status and revoke live sessions when the new status is non-active."""
    status_value = _normalize_status(status)
    with conn.transaction():
        row = conn.execute(
            "update app_users set status = %s, updated_at = now() "
            "where business_slug = %s and id = %s "
            f"returning {_APP_USER_COLUMNS}",
            (status_value, business_slug, app_user_id),
        ).fetchone()
        if row is None:
            raise AppIdentityError(f"unknown app user: {app_user_id}")
        user = _app_user_from_row(row)
        if user.status != "active":
            conn.execute(
                "update app_sessions set revoked_at = now() "
                "where business_slug = %s and app_user_id = %s and revoked_at is null",
                (business_slug, app_user_id),
            )
    return user
