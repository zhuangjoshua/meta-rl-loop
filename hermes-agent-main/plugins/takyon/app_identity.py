"""Product sub-user identity + magic-link auth + sessions — Phase 5 of mediationplan.md.

These are the customers OF a business the Takyon user runs (product sub-users), NOT
the top-level Takyon operator — that identity lives in `control_plane.py` /
`user_api_keys.py`. Everything here is scoped by `business_slug`: a sub-user belongs to
exactly one business, an email is unique only within that business, and a session is
only valid for the business it was minted in. This is the Postgres port of the SQLite
trunk's app_users / app_magic_links / app_sessions (core.py).

Auth is magic-link only. Raw tokens are never stored — only their SHA-256 hex hash
(identical to the SQLite `_hash_token`, so a ported app keeps working). A magic link is
single-use and short-lived; a session is a 30-day bearer token. This module owns only
the guarded STATE change (mint a link, redeem it for a session, validate/revoke a
session) — email DELIVERY is a side effect owned by the layer above (the HTTP/tool
surface decides live-send vs. test-mode suppression and records `provider_message_id`),
exactly as the billing layer keeps allowance accounting separate from the Stripe call.

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

_DEFAULT_MAGIC_LINK_TTL_MINUTES = 15
_DEFAULT_SESSION_TTL_DAYS = 30
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_VALID_APP_USER_STATUSES = {"active", "suspended", "closed"}
UNENTITLED_TIER = "unentitled"


class AppIdentityError(Exception):
    """Base for product sub-user identity errors."""


class InvalidEmail(AppIdentityError):
    """The supplied email is missing or malformed."""


class InvalidMagicLink(AppIdentityError):
    """The magic link is unknown, expired, or already redeemed."""


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
class MagicLink:
    """A minted login link. The raw token is returned ALONGSIDE this record exactly
    once (never stored in clear) and is not a field here."""

    id: str
    business_slug: str
    app_user_id: str
    email: str
    purpose: str
    expires_at: object


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


def _normalize_status(value: str | None) -> str:
    status = str(value or "active").strip().lower()
    if status not in _VALID_APP_USER_STATUSES:
        raise ValueError("status must be one of active, suspended, or closed")
    return status


def _app_user_from_row(row) -> AppUser:
    return AppUser(
        id=str(row[0]),
        business_slug=str(row[1]),
        email=str(row[2]),
        name=None if row[3] is None else str(row[3]),
        status=str(row[4]),
        tier=str(row[5]),
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


def create_magic_link(
    conn,
    business_slug: str,
    email: str,
    *,
    purpose: str = "login",
    name: str | None = None,
    ttl_minutes: int = _DEFAULT_MAGIC_LINK_TTL_MINUTES,
) -> tuple[MagicLink, str]:
    """Ensure the sub-user exists and mint a single-use login token for them, all in
    one transaction. Returns (MagicLink, raw_token); the raw token is returned exactly
    once and only its hash is stored. The expiry is computed from the server clock
    (`now() + ttl_minutes`) so it never depends on a caller's wall clock. This mints
    only — sending the email is the caller's concern."""
    if not isinstance(ttl_minutes, int) or ttl_minutes <= 0:
        raise ValueError("ttl_minutes must be a positive integer")
    if not purpose:
        raise ValueError("purpose must be a non-empty string")
    raw = _random_token()
    with conn.transaction():
        user = conn.execute(
            "insert into app_users (business_slug, email, name, status, tier) values (%s, %s, %s, 'active', %s) "
            "on conflict (business_slug, email) do update set "
            " name = coalesce(excluded.name, app_users.name), "
            " updated_at = now() "
            "returning id, email, status",
            (business_slug, _normalize_email(email), name, UNENTITLED_TIER),
        ).fetchone()
        app_user_id, normalized_email, status = str(user[0]), str(user[1]), str(user[2])
        if status != "active":
            raise InactiveAppUser(app_user_id)
        row = conn.execute(
            "insert into app_magic_links "
            "(business_slug, app_user_id, email, token_hash, purpose, expires_at) "
            "values (%s, %s, %s, %s, %s, now() + make_interval(mins => %s)) "
            "returning id, expires_at",
            (business_slug, app_user_id, normalized_email, _hash_token(raw), purpose, ttl_minutes),
        ).fetchone()
    link = MagicLink(
        id=str(row[0]),
        business_slug=business_slug,
        app_user_id=app_user_id,
        email=normalized_email,
        purpose=purpose,
        expires_at=row[1],
    )
    return link, raw


def verify_magic_link(
    conn,
    business_slug: str,
    raw_token: str,
    *,
    session_ttl_days: int = _DEFAULT_SESSION_TTL_DAYS,
) -> tuple[AppSession, str]:
    """Redeem a magic link and open a session. Returns (AppSession, raw_session_token).

    Single-use is enforced atomically: the redemption is an
    `update ... where used_at is null and expires_at > now() returning` so two
    concurrent verifications can't both win the same link (it stamps used_at and tells
    us the owning user in one statement — closing the read-then-write race the SQLite
    version had). Raises InvalidMagicLink if the token is unknown/expired/already used,
    and InactiveAppUser if the resolved sub-user is suspended/closed."""
    if not isinstance(session_ttl_days, int) or session_ttl_days <= 0:
        raise ValueError("session_ttl_days must be a positive integer")
    token = str(raw_token or "").strip()
    if not token:
        raise InvalidMagicLink("token is required")
    raw_session = _random_token()
    with conn.transaction():
        redeemed = conn.execute(
            "update app_magic_links set used_at = now() "
            "where business_slug = %s and token_hash = %s "
            "  and used_at is null and expires_at > now() "
            "returning app_user_id",
            (business_slug, _hash_token(token)),
        ).fetchone()
        if redeemed is None:
            raise InvalidMagicLink("magic link is invalid, expired, or already used")
        app_user_id = str(redeemed[0])
        status_row = conn.execute(
            "select status from app_users where business_slug = %s and id = %s",
            (business_slug, app_user_id),
        ).fetchone()
        if status_row is None or str(status_row[0]) != "active":
            raise InactiveAppUser(app_user_id)
        row = conn.execute(
            "insert into app_sessions (business_slug, app_user_id, token_hash, expires_at) "
            "values (%s, %s, %s, now() + make_interval(days => %s)) "
            "returning id, expires_at",
            (business_slug, app_user_id, _hash_token(raw_session), session_ttl_days),
        ).fetchone()
    session = AppSession(
        id=str(row[0]),
        business_slug=business_slug,
        app_user_id=app_user_id,
        expires_at=row[1],
    )
    return session, raw_session


def start_session(
    conn,
    business_slug: str,
    app_user_id: str,
    *,
    session_ttl_days: int = _DEFAULT_SESSION_TTL_DAYS,
) -> tuple[AppSession, str]:
    """Mint a 30-day bearer session for an already-resolved sub-user. Returns
    (AppSession, raw_session_token); only the hash is stored. Refuses a suspended/closed user
    (InactiveAppUser). This is the session-minting half of ``verify_magic_link``, factored out so
    the Supabase login path mints an IDENTICAL session — `validate_session` and everything downstream
    cannot tell, nor need to, how the user authenticated."""
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
    legacy email-only row for (business, email) with no supabase id is ADOPTED — its
    ``supabase_user_id`` is set — so a customer who pre-existed via magic-link keeps the same
    identity, entitlements, and usage history on their first Google login; else (3) a brand-new
    ``active`` sub-user. A suspended/closed user is never reactivated here, and the caller (the
    login route) only mints a session via ``start_session``, which refuses non-active users.

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
