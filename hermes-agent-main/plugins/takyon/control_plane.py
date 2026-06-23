"""Postgres control-plane access for the opaque per-user API key boundary.

This is the server-side resolver from mediationplan.md: the raw API key is the
only user-provided input, and everything behind it (shared provider keys, other
tenants, billing internals) is opaque. `resolve_api_key` takes a raw key and
returns either a small `ResolvedPrincipal` (identity + owned business slugs) or
None — never secrets, provider keys, or internals.

Safebox is the granting authority for ``tk_...`` keys. Postgres mirrors only
non-secret metadata for joins and audit.

Targets the Postgres/Supabase control plane defined by the migrations in
`db/migrations/`. Functions take a psycopg connection so they compose with any
transaction/connection-pool strategy the caller chooses.
"""

from __future__ import annotations

import os
import uuid
from dataclasses import dataclass

from . import safebox
from .user_api_keys import (
    generate_api_key,
    key_prefix,
)


@dataclass(frozen=True)
class ResolvedPrincipal:
    """The only thing a raw API key resolves to. Deliberately small: nothing here
    is a secret or an internal handle the caller could use to escape their tenant."""

    user_id: str
    key_id: str
    status: str
    business_slugs: tuple[str, ...]


def _business_slugs_for_user(conn, user_id: str) -> tuple[str, ...]:
    return tuple(
        str(r[0])
        for r in conn.execute(
            "select slug from businesses where owner_user_id = %s order by slug",
            (user_id,),
        ).fetchall()
    )


def _user_api_key_mirror_hash(key_id: str) -> str:
    return f"safebox:{key_id}"


def _starter_allowance_cents() -> int:
    raw = str(os.environ.get("TAKYON_STARTER_ALLOWANCE_CENTS") or "").strip()
    if not raw:
        return 100
    try:
        return max(0, int(raw))
    except ValueError:
        return 100


def _ensure_starter_allowance(conn, user_id: str, *, session_token: str | None = None) -> int:
    """Grant the landing-page starter allowance once to an otherwise-empty account.

    This keeps "your first company is on the house" honest for both fresh Auth0
    users and the local platform owner, without resetting any account that has
    already received allowance or spend.
    """
    included_cents = _starter_allowance_cents()
    if included_cents <= 0:
        return 0
    with conn.transaction():
        acct = conn.execute(
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
        existing_entry = conn.execute(
            "select 1 from billing_entries where user_id = %s limit 1",
            (user_id,),
        ).fetchone()
        if existing_entry is not None:
            return included
    return int(safebox.grant_starter_allowance(conn, user_id, session_token=session_token))


def _mint_api_key_record(conn, user_id: str) -> tuple[str, str]:
    raw = generate_api_key()
    key_id = str(uuid.uuid4())
    try:
        with conn.transaction():
            conn.execute(
                "insert into user_api_keys (id, user_id, key_hash, prefix) values (%s, %s, %s, %s)",
                (key_id, user_id, _user_api_key_mirror_hash(key_id), key_prefix(raw)),
            )
            safebox.register_user_api_key(user_id, raw, key_id=key_id)
    except Exception:
        safebox.delete_user_api_key(key_id)
        raise
    return key_id, raw


def _active_api_key_exists(conn, user_id: str) -> bool:
    row = conn.execute(
        "select 1 from user_api_keys where user_id = %s and revoked_at is null limit 1",
        (user_id,),
    ).fetchone()
    return row is not None


def get_or_create_user(conn, auth0_sub: str, email: str | None = None) -> tuple[str, bool]:
    """JIT-provision a top-level Takyon user keyed by the Auth0 OIDC `sub`.

    Returns (user_id, created). Safe under concurrent first-logins: the insert is
    guarded by `on conflict (auth0_sub) do nothing` and falls back to a re-read.
    """
    row = conn.execute(
        "select id from users where auth0_sub = %s", (auth0_sub,)
    ).fetchone()
    if row is not None:
        return str(row[0]), False
    row = conn.execute(
        "insert into users (auth0_sub, email) values (%s, %s) "
        "on conflict (auth0_sub) do nothing returning id",
        (auth0_sub, email),
    ).fetchone()
    if row is None:  # lost the race to a concurrent insert
        row = conn.execute(
            "select id from users where auth0_sub = %s", (auth0_sub,)
        ).fetchone()
        return str(row[0]), False
    return str(row[0]), True


def provision_user_on_first_login(
    conn,
    auth0_sub: str,
    email: str | None = None,
    *,
    session_token: str | None = None,
) -> tuple[str, bool, str | None]:
    """First-login JIT provisioning (the zero-friction onboarding step).

    Ensure a `users` row exists for this Auth0 `sub`, open the two zero-balance money accounts through
    Safebox authority, then mint the single API key only after those account opens succeed.

    Returns (user_id, created, raw_key): `raw_key` is the freshly minted key on the
    very first login (returned exactly once, never stored in clear) and None on every
    later login. Idempotent and race-safe — a concurrent first login yields
    created=False and does not mint a second key.

    The user row commits before remote Safebox account opens so the Safebox can satisfy the account
    foreign keys on its own connection. The account opens and active-key check are idempotent, so a
    retry repairs any earlier interruption without minting a second active key.
    """
    user_id = ""
    created = False
    raw = None
    minted_key_id: str | None = None
    try:
        with conn.transaction():
            user_id, created = get_or_create_user(conn, auth0_sub, email)
        safebox.open_billing_account(conn, user_id)
        safebox.open_custody_account(conn, user_id)
        if not _active_api_key_exists(conn, user_id):
            minted_key_id, raw = _mint_api_key_record(conn, user_id)
    except Exception:
        if minted_key_id:
            safebox.delete_user_api_key(minted_key_id)
            try:
                conn.execute("delete from user_api_keys where id = %s", (minted_key_id,))
            except Exception:
                pass
        raise
    _ensure_starter_allowance(conn, user_id, session_token=session_token)
    return user_id, created, raw


# --------------------------------------------------------------------------- platform owner
#
# The local Takyon CEO/shell creates businesses with no Auth0/login context, but PG
# `businesses.owner_user_id` is NOT NULL (0001 spine). So every shell-created business is owned by a
# single platform/operator user, keyed by an `auth0_sub` read from config. Set TAKYON_PLATFORM_OWNER_SUB
# to your real Auth0 `sub` to unify the businesses the shell creates with the set your dashboard login
# sees (control_api's /v1/businesses scopes by owner_user_id). It is a NON-secret identifier with a
# working default — never a credential that gates onboarding.

_PLATFORM_OWNER_SUB_ENV = "TAKYON_PLATFORM_OWNER_SUB"
_PLATFORM_OWNER_EMAIL_ENV = "TAKYON_PLATFORM_OWNER_EMAIL"
_DEFAULT_PLATFORM_OWNER_SUB = "takyon|platform-owner"


def platform_owner_sub() -> str:
    """The configured platform-owner Auth0 `sub` (default `takyon|platform-owner`). Config, not a
    secret, so it is read straight from the environment like the rest of the control plane."""
    raw = os.environ.get(_PLATFORM_OWNER_SUB_ENV)
    return raw.strip() if raw and raw.strip() else _DEFAULT_PLATFORM_OWNER_SUB


def platform_owner_email() -> str | None:
    """Optional email stamped on the platform owner at first provisioning; None when unset."""
    raw = os.environ.get(_PLATFORM_OWNER_EMAIL_ENV)
    return raw.strip() if raw and raw.strip() else None


def resolve_platform_owner_id(conn) -> str | None:
    """Read-only: the platform owner's `user_id` if already provisioned, else None.

    Used by the operator store's `business.upsert` so creating a business NEVER mints or surfaces an
    API key as a side effect (the one-time key is surfaced only by the explicit `ensure_platform_owner`
    bootstrap). Reads positionally, so the caller must lend a tuple-row psycopg connection."""
    row = conn.execute(
        "select id from users where auth0_sub = %s", (platform_owner_sub(),)
    ).fetchone()
    return str(row[0]) if row else None


def ensure_platform_owner(conn) -> tuple[str, str | None]:
    """Idempotently provision the single platform/operator owner (the explicit bootstrap seam).

    Returns (user_id, raw_key): `raw_key` is the one-time API key minted on the very first creation
    (surface it once, e.g. to the operator console / server log — it is never stored in clear) and
    None on every later call. Full provisioning (key + billing + custody, one txn) via
    `provision_user_on_first_login`, so the owner is never observed half-made. Call this at startup
    (the serving flip) — NOT from inside the store's commit path, which must stay key-secret-free."""
    user_id, _created, raw = provision_user_on_first_login(
        conn, platform_owner_sub(), platform_owner_email()
    )
    return user_id, raw


def resolve_user_principal(
    conn,
    user_id: str,
    *,
    key_id: str = "dashboard-session",
) -> ResolvedPrincipal | None:
    """Resolve a known user id to the same small principal shape as API-key auth."""
    row = conn.execute(
        "select status from users where id = %s",
        (user_id,),
    ).fetchone()
    if row is None:
        return None
    status = str(row[0] or "")
    if status != "active":
        return None
    return ResolvedPrincipal(
        user_id=str(user_id),
        key_id=key_id,
        status=status,
        business_slugs=_business_slugs_for_user(conn, str(user_id)),
    )


def resolve_auth0_principal(
    conn,
    auth0_sub: str,
    email: str | None = None,
    *,
    key_id: str = "dashboard-session",
    session_token: str | None = None,
) -> ResolvedPrincipal | None:
    """Resolve an Auth0-backed dashboard identity to the canonical principal shape."""
    user_id, _created, _raw_key = provision_user_on_first_login(
        conn,
        auth0_sub,
        email,
        session_token=session_token,
    )
    return resolve_user_principal(conn, user_id, key_id=key_id)


def mint_api_key(conn, user_id: str) -> str:
    """Mint THE single active key for a user. Raises if one is already active —
    callers rotate instead. The raw key is returned exactly once; only
    Safebox keeps the verifier and Postgres keeps non-secret mirror metadata."""
    _key_id, raw = _mint_api_key_record(conn, user_id)
    return raw


def rotate_api_key(conn, user_id: str) -> str:
    """Atomically revoke the active key (if any) and mint a new one. Returns the
    new raw key. The old row is kept (revoked) for audit."""
    raw = ""
    revoked_key_ids: list[str] = []
    minted_key_id: str | None = None
    try:
        with conn.transaction():
            conn.execute(
                "update user_api_keys set revoked_at = now() "
                "where user_id = %s and revoked_at is null",
                (user_id,),
            )
            revoked_key_ids = safebox.revoke_user_api_keys_for_user(user_id)
            minted_key_id, raw = _mint_api_key_record(conn, user_id)
    except Exception:
        if minted_key_id:
            safebox.delete_user_api_key(minted_key_id)
        if revoked_key_ids:
            safebox.restore_user_api_keys(revoked_key_ids)
        raise
    return raw


def resolve_api_key(conn, raw_key: str) -> ResolvedPrincipal | None:
    """Resolve a presented raw API key to a `ResolvedPrincipal`, or None.

    Returns None for malformed keys, unknown or revoked keys, and non-active
    users. On success, stamps `last_used_at` and returns only identity + owned
    business slugs.
    """
    record = safebox.resolve_user_api_key(raw_key)
    if record is None:
        return None
    key_id = str(record.get("id") or "")
    user_id = str(record.get("user_id") or "")
    if not key_id or not user_id:
        return None
    row = conn.execute(
        "select status from users where id = %s",
        (user_id,),
    ).fetchone()
    if row is None:
        return None
    status = row[0]
    if status != "active":
        return None
    slugs = _business_slugs_for_user(conn, user_id)
    # Throttle the stamp: skip the write when it was touched in the last minute, so
    # one hot key can't serialize every request on this row's write lock (and to keep
    # WAL volume down). A coarse last_used_at is good enough; the no-match case is a
    # cheap read with no heap write.
    conn.execute(
        "update user_api_keys set last_used_at = now() "
        "where id = %s "
        "and (last_used_at is null or last_used_at < now() - interval '60 seconds')",
        (key_id,),
    )
    return ResolvedPrincipal(
        user_id=user_id, key_id=key_id, status=status, business_slugs=slugs
    )
