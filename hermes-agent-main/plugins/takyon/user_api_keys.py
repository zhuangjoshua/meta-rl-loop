"""Opaque per-user Takyon API key primitives.

Backend-agnostic: pure functions over strings, no DB or framework coupling, so
they behave identically on the current SQLite control plane and the Supabase
target. The key is a platform-minted capability and the entire per-user
boundary; it is never generated client-side and never stored in the clear.

At rest we keep only `hash_api_key(raw)` (SHA-256 hex) plus the non-secret
`key_prefix(raw)` for display/lookup. The raw key is shown to the user exactly
once at mint time and is unrecoverable thereafter.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import string

API_KEY_PREFIX = "tk_"

# secrets.token_urlsafe(32) -> 43 url-safe base64 chars of 256-bit entropy.
_TOKEN_NBYTES = 32
# Random-body chars retained as a non-secret lookup hint (alongside API_KEY_PREFIX).
_PREFIX_VISIBLE_CHARS = 8
# Lower bound on the random body; rejects obviously malformed input before any DB hit.
_MIN_BODY_LEN = 32

_URLSAFE_ALPHABET = frozenset(string.ascii_letters + string.digits + "-_")


def generate_api_key() -> str:
    """Mint a new opaque key. Platform-minted only; never user-supplied."""
    return f"{API_KEY_PREFIX}{secrets.token_urlsafe(_TOKEN_NBYTES)}"


def is_well_formed(raw: str) -> bool:
    """Cheap structural check before any DB lookup. This is not authentication."""
    if not isinstance(raw, str) or not raw.startswith(API_KEY_PREFIX):
        return False
    body = raw[len(API_KEY_PREFIX):]
    if len(body) < _MIN_BODY_LEN:
        return False
    return all(c in _URLSAFE_ALPHABET for c in body)


def key_prefix(raw: str) -> str:
    """Non-secret leading slice safe to store in clear and show in UIs/logs."""
    body = raw[len(API_KEY_PREFIX):] if raw.startswith(API_KEY_PREFIX) else raw
    return f"{API_KEY_PREFIX}{body[:_PREFIX_VISIBLE_CHARS]}"


def hash_api_key(raw: str) -> str:
    """Canonical at-rest representation: SHA-256 hex. Store this, never the raw key."""
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def verify_api_key(raw: str, stored_hash: str) -> bool:
    """Constant-time check of a presented key against a stored hash."""
    if not raw or not stored_hash:
        return False
    return hmac.compare_digest(hash_api_key(raw), stored_hash)
