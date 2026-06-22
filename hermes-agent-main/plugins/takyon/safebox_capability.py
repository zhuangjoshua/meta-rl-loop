"""Capability tokens for the safebox broker (Phase 2 of deploy/SAFEBOX-BROKER-REMEDIATION-PLAN.md).

ADDITIVE + OFF THE LIVE PATH. This is the token *primitive* only; it is NOT yet wired into any route,
so it changes no live behaviour. The safebox will mint a token only AFTER it independently validates
identity — boundary 1: takyon_user -> business ownership; boundary 2: session -> app_user ->
entitlement — and verify it on every brokered call.

Why this enforces tenant isolation (no evil user/sub-user can do anything):
  * The scope {takyon_user_id, business_slug, app_user_id, action, max_cost_microusd} is SIGNED, so a
    verified token's scope is AUTHORITATIVE — the broker MUST act on the verified scope, never on a
    client-supplied value. A client cannot change business_slug/app_user_id/takyon_user_id after minting
    without breaking the signature (no scope-swap).
  * The signing key lives ONLY in the safebox (a new safebox-only secret, never written to any client
    .env), so a client cannot mint a token for another user / business / sub-user, nor raise its own
    cost ceiling. A leaked token is single-use (nonce) + short-TTL + audience-bound, so it does exactly
    one {user, business, sub-user, action, <=cost} thing.

Symmetric HMAC-SHA256 is sufficient because the safebox is BOTH minter and verifier (one trust domain);
no client ever needs to verify, so there is no need for asymmetric keys. Single-use is enforced by the
caller via a nonce store (this module is stateless crypto).
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
from dataclasses import asdict, dataclass
from typing import Any, Mapping


class CapabilityError(Exception):
    """Token failed to verify (bad signature, expired, wrong audience, malformed, incomplete scope)."""


@dataclass(frozen=True)
class CapabilityScope:
    takyon_user_id: str
    business_slug: str
    app_user_id: str | None  # None for operator/platform-plane actions (no product customer)
    action: str
    max_cost_microusd: int

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64url_decode(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + ("=" * (-len(s) % 4)))


def _canonical(payload: Mapping[str, Any]) -> bytes:
    # deterministic, key-sorted, compact: mint and verify must agree byte-for-byte
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def mint_capability(
    scope: CapabilityScope,
    *,
    signing_key: bytes,
    audience: str,
    nonce: str,
    issued_at: int,
    ttl_seconds: int,
) -> str:
    """Mint a signed capability token. Callable ONLY where the safebox signing key is in scope."""
    if not signing_key:
        raise CapabilityError("missing signing key")
    if ttl_seconds <= 0:
        raise CapabilityError("ttl must be positive")
    if int(scope.max_cost_microusd) < 0:
        raise CapabilityError("max_cost_microusd must be >= 0")
    if not scope.takyon_user_id or not scope.business_slug or not scope.action:
        raise CapabilityError("incomplete scope")
    if not nonce:
        raise CapabilityError("missing nonce")
    payload = {
        "tu": scope.takyon_user_id,
        "b": scope.business_slug,
        "au": scope.app_user_id,
        "act": scope.action,
        "mc": int(scope.max_cost_microusd),
        "aud": audience,
        "n": nonce,
        "iat": int(issued_at),
        "exp": int(issued_at) + int(ttl_seconds),
    }
    body = _canonical(payload)
    sig = hmac.new(signing_key, body, hashlib.sha256).digest()
    return f"{_b64url(body)}.{_b64url(sig)}"


def verify_capability(
    token: str,
    *,
    signing_key: bytes,
    expected_audience: str,
    now: int,
) -> tuple[CapabilityScope, str, int]:
    """Verify signature + audience + expiry; return (authoritative scope, nonce, exp) or raise.

    Single-use is the CALLER's responsibility: take the returned nonce and atomically check-and-mark it
    in a seen-nonce store, rejecting any second presentation. The returned scope is AUTHORITATIVE — the
    broker must reserve/spend on it and ignore any client-supplied business_slug/app_user_id.
    """
    if not signing_key:
        raise CapabilityError("missing signing key")
    if not token or token.count(".") != 1:
        raise CapabilityError("malformed token")
    body_b64, sig_b64 = token.split(".", 1)
    try:
        body = _b64url_decode(body_b64)
        presented_sig = _b64url_decode(sig_b64)
    except Exception as exc:  # noqa: BLE001 - any decode failure is a bad token
        raise CapabilityError("undecodable token") from exc
    expected_sig = hmac.new(signing_key, body, hashlib.sha256).digest()
    if not hmac.compare_digest(presented_sig, expected_sig):
        raise CapabilityError("bad signature")
    try:
        payload = json.loads(body.decode("utf-8"))
    except Exception as exc:  # noqa: BLE001
        raise CapabilityError("bad payload") from exc
    if str(payload.get("aud") or "") != expected_audience:
        raise CapabilityError("audience mismatch")
    exp = int(payload.get("exp") or 0)
    if int(now) >= exp:
        raise CapabilityError("expired")
    nonce = str(payload.get("n") or "")
    if not nonce:
        raise CapabilityError("missing nonce")
    scope = CapabilityScope(
        takyon_user_id=str(payload.get("tu") or ""),
        business_slug=str(payload.get("b") or ""),
        app_user_id=(payload.get("au") if payload.get("au") is not None else None),
        action=str(payload.get("act") or ""),
        max_cost_microusd=int(payload.get("mc") or 0),
    )
    if not scope.takyon_user_id or not scope.business_slug or not scope.action:
        raise CapabilityError("incomplete scope")
    return scope, nonce, exp
