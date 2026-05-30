"""Postgres control-plane access for the opaque per-user API key boundary.

This is the server-side resolver from mediationplan.md: the raw API key is the
only user-provided input, and everything behind it (shared provider keys, other
tenants, billing internals) is opaque. `resolve_api_key` takes a raw key and
returns either a small `ResolvedPrincipal` (identity + owned business slugs) or
None — never secrets, provider keys, or internals.

Targets the Postgres/Supabase control plane defined by the migrations in
`db/migrations/`. Functions take a psycopg connection so they compose with any
transaction/connection-pool strategy the caller chooses.
"""

from __future__ import annotations

from dataclasses import dataclass

from .user_api_keys import (
    generate_api_key,
    hash_api_key,
    is_well_formed,
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
    conn, auth0_sub: str, email: str | None = None
) -> tuple[str, bool, str | None]:
    """First-login JIT provisioning (the zero-friction onboarding step).

    In one transaction: ensure a `users` row exists for this Auth0 `sub`, and ONLY
    when it is brand new, mint its single API key. So a half-provisioned user (row
    without a key, or key without a row) can never be observed.

    Returns (user_id, created, raw_key): `raw_key` is the freshly minted key on the
    very first login (returned exactly once, never stored in clear) and None on every
    later login. Idempotent and race-safe — a concurrent first login yields
    created=False and does not mint a second key.

    Phase-1 scope: the full plan also opens billing/custody accounts here, but those
    tables land in later migrations; this provisions identity + key only.
    """
    with conn.transaction():
        user_id, created = get_or_create_user(conn, auth0_sub, email)
        raw = mint_api_key(conn, user_id) if created else None
    return user_id, created, raw


def mint_api_key(conn, user_id: str) -> str:
    """Mint THE single active key for a user. Raises if one is already active —
    callers rotate instead. The raw key is returned exactly once; only its hash
    and a non-secret prefix are stored."""
    raw = generate_api_key()
    conn.execute(
        "insert into user_api_keys (user_id, key_hash, prefix) values (%s, %s, %s)",
        (user_id, hash_api_key(raw), key_prefix(raw)),
    )
    return raw


def rotate_api_key(conn, user_id: str) -> str:
    """Atomically revoke the active key (if any) and mint a new one. Returns the
    new raw key. The old row is kept (revoked) for audit."""
    with conn.transaction():
        conn.execute(
            "update user_api_keys set revoked_at = now() "
            "where user_id = %s and revoked_at is null",
            (user_id,),
        )
        raw = generate_api_key()
        conn.execute(
            "insert into user_api_keys (user_id, key_hash, prefix) values (%s, %s, %s)",
            (user_id, hash_api_key(raw), key_prefix(raw)),
        )
    return raw


def resolve_api_key(conn, raw_key: str) -> ResolvedPrincipal | None:
    """Resolve a presented raw API key to a `ResolvedPrincipal`, or None.

    Returns None for malformed keys, unknown or revoked keys, and non-active
    users. On success, stamps `last_used_at` and returns only identity + owned
    business slugs.
    """
    if not is_well_formed(raw_key):
        return None
    row = conn.execute(
        "select k.id, k.user_id, u.status "
        "from user_api_keys k join users u on u.id = k.user_id "
        "where k.key_hash = %s and k.revoked_at is null",
        (hash_api_key(raw_key),),
    ).fetchone()
    if row is None:
        return None
    key_id, user_id, status = str(row[0]), str(row[1]), row[2]
    if status != "active":
        return None
    slugs = tuple(
        r[0]
        for r in conn.execute(
            "select slug from businesses where owner_user_id = %s order by slug",
            (user_id,),
        ).fetchall()
    )
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
