"""Project gateway-key boundary — the per-business capability that fronts the platform provider key.

Net-new for Phase 5 (mediationplan ADD (b)). A business's app runtime and the product app it
generates receive a ``tkg_…`` gateway key. They present it to the internal AI gateway, which
resolves it to a ``business_slug`` and only then calls the SHARED platform provider key
server-side. The generated app therefore holds ONLY its own gateway key, never the provider key —
the Phase 5 acceptance "generated app never holds provider key".

At-rest discipline mirrors ``user_api_keys`` (0001): store only the SHA-256 ``key_hash`` plus a
non-secret ``prefix`` for display/lookup; the raw key is returned exactly once at mint and is
unrecoverable thereafter. The hashing is reused verbatim from ``user_api_keys`` (it is
prefix-agnostic); only the keyspace differs — gateway keys live in the ``tkg_`` namespace, DISJOINT
from the per-user ``tk_`` namespace, so a per-user key and a per-business gateway key can never be
confused or cross-resolve.

Pure leaf: every function takes a psycopg ``conn``; the mutating ops open their own
``conn.transaction()`` so they compose under any connection/transaction strategy.
"""

from __future__ import annotations

import secrets
import string
from dataclasses import dataclass
from datetime import datetime

from .user_api_keys import hash_api_key

GATEWAY_KEY_PREFIX = "tkg_"

# secrets.token_urlsafe(32) -> 43 url-safe base64 chars of 256-bit entropy (matches user_api_keys).
_TOKEN_NBYTES = 32
# Random-body chars retained as a non-secret lookup hint (alongside GATEWAY_KEY_PREFIX).
_PREFIX_VISIBLE_CHARS = 8
# Lower bound on the random body; rejects obviously malformed input before any DB hit.
_MIN_BODY_LEN = 32

_URLSAFE_ALPHABET = frozenset(string.ascii_letters + string.digits + "-_")


class AppGatewayKeyError(Exception):
    """Base for gateway-key errors."""


@dataclass(frozen=True)
class GatewayKey:
    """A stored gateway-key record. Never carries the raw key — only its non-secret prefix."""

    id: str
    business_slug: str
    prefix: str
    revoked_at: datetime | None
    created_at: datetime


@dataclass(frozen=True)
class GatewayPrincipal:
    """The only thing a raw gateway key resolves to. Deliberately small and opaque: the business it
    is scoped to plus the key id for audit — never a provider key, another tenant, or an internal
    handle the caller could use to escape its business."""

    business_slug: str
    key_id: str


def generate_gateway_key() -> str:
    """Mint a new opaque gateway key in the ``tkg_`` keyspace. Platform-minted only."""
    return f"{GATEWAY_KEY_PREFIX}{secrets.token_urlsafe(_TOKEN_NBYTES)}"


def is_well_formed(raw: str) -> bool:
    """Cheap structural check before any DB lookup. This is not authentication. Rejects anything
    outside the ``tkg_`` keyspace — so a per-user ``tk_`` key is never even looked up as a gateway
    key (the keyspaces are disjoint: ``tk_…`` never starts with ``tkg_`` and vice versa)."""
    if not isinstance(raw, str) or not raw.startswith(GATEWAY_KEY_PREFIX):
        return False
    body = raw[len(GATEWAY_KEY_PREFIX):]
    if len(body) < _MIN_BODY_LEN:
        return False
    return all(c in _URLSAFE_ALPHABET for c in body)


def gateway_key_prefix(raw: str) -> str:
    """Non-secret leading slice safe to store in clear and show in UIs/logs."""
    body = raw[len(GATEWAY_KEY_PREFIX):] if raw.startswith(GATEWAY_KEY_PREFIX) else raw
    return f"{GATEWAY_KEY_PREFIX}{body[:_PREFIX_VISIBLE_CHARS]}"


def _key_from_row(row) -> GatewayKey:
    return GatewayKey(
        id=str(row[0]),
        business_slug=row[1],
        prefix=row[2],
        revoked_at=row[3],
        created_at=row[4],
    )


def mint_gateway_key(conn, business_slug: str) -> tuple[str, GatewayKey]:
    """Mint a new gateway key for a business. Returns ``(raw_key, record)``; the raw key is returned
    exactly once and only its hash + prefix are stored.

    A business may hold several active keys at once (app runtime + generated app, or an overlapping
    rotation), so this always INSERTs — it never revokes an existing key. Rotation is mint-new then
    ``revoke_gateway_key`` on the old one. Unknown business -> ForeignKeyViolation (the FK is the
    guard)."""
    raw = generate_gateway_key()
    with conn.transaction():
        row = conn.execute(
            "insert into app_gateway_keys (business_slug, key_hash, prefix) "
            "values (%s, %s, %s) "
            "returning id, business_slug, prefix, revoked_at, created_at",
            (business_slug, hash_api_key(raw), gateway_key_prefix(raw)),
        ).fetchone()
    return raw, _key_from_row(row)


def resolve_gateway_key(conn, raw_key: str) -> GatewayPrincipal | None:
    """Resolve a presented raw gateway key to its business, or None.

    Returns None for malformed keys (including a per-user ``tk_`` key), unknown keys, and revoked
    keys. On success returns ONLY ``business_slug`` + ``key_id`` — the opaque boundary the internal
    AI gateway needs to route (business -> policy -> budget -> shared provider key -> settle). A
    resolvable key always points at a live business: ``business_slug`` CASCADEs, so a deleted
    business leaves no keys behind, and the resolver needs no existence join."""
    if not is_well_formed(raw_key):
        return None
    row = conn.execute(
        "select id, business_slug from app_gateway_keys "
        "where key_hash = %s and revoked_at is null",
        (hash_api_key(raw_key),),
    ).fetchone()
    if row is None:
        return None
    return GatewayPrincipal(business_slug=row[1], key_id=str(row[0]))


def revoke_gateway_key(
    conn,
    *,
    key_id: str | None = None,
    raw_key: str | None = None,
    business_slug: str | None = None,
) -> bool:
    """Soft-revoke a gateway key (set ``revoked_at``). Identify it by ``key_id`` OR ``raw_key``;
    optionally scope to ``business_slug`` so one business cannot revoke another's key.

    Idempotent: an already-revoked, unknown, or out-of-scope key revokes nothing and returns False.
    Returns True iff a row moved to revoked. The old (revoked) row is kept for audit."""
    if key_id is None and raw_key is None:
        raise AppGatewayKeyError("revoke_gateway_key requires key_id or raw_key")
    key_hash = hash_api_key(raw_key) if raw_key is not None else None
    with conn.transaction():
        row = conn.execute(
            "update app_gateway_keys set revoked_at = now() "
            "where revoked_at is null "
            "and (%s::uuid is null or id = %s::uuid) "
            "and (%s::text is null or key_hash = %s::text) "
            "and (%s::text is null or business_slug = %s::text) "
            "returning id",
            (key_id, key_id, key_hash, key_hash, business_slug, business_slug),
        ).fetchone()
    return row is not None


def list_gateway_keys(
    conn, business_slug: str, *, include_revoked: bool = False
) -> list[GatewayKey]:
    """List a business's gateway keys, newest first. Active-only by default."""
    if include_revoked:
        rows = conn.execute(
            "select id, business_slug, prefix, revoked_at, created_at "
            "from app_gateway_keys where business_slug = %s "
            "order by created_at desc",
            (business_slug,),
        ).fetchall()
    else:
        rows = conn.execute(
            "select id, business_slug, prefix, revoked_at, created_at "
            "from app_gateway_keys where business_slug = %s and revoked_at is null "
            "order by created_at desc",
            (business_slug,),
        ).fetchall()
    return [_key_from_row(r) for r in rows]
