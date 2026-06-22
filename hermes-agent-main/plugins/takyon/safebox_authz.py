"""Two-tier identity validation the safebox runs BEFORE minting a capability token
(Phase 2/3 of deploy/SAFEBOX-BROKER-REMEDIATION-PLAN.md). ADDITIVE — not yet wired into any route.

The safebox derives the AUTHORITATIVE scope here from validated reads, never from a client-asserted
value. This is what closes the red-team's scope-swap + cross-tenant holes:

  * Boundary 2 (sub-user ↔ sub-user): validate the session token -> the REAL app_user_id; a billable
    call requires an active, paid (tier-conferring) entitlement for THAT app_user under THIS business.
    A sub-user can only ever get a token for their own app_user_id, so they cannot act as, or draw
    down the budget of, another sub-user.
  * Boundary 1 (user ↔ user): resolve the business's owner_user_id (businesses.owner_user_id ->
    users.id, migration 0001). For an operator/platform call the authenticated operator MUST equal the
    owner. For a product call the takyon_user IS the owner of the business the (business-scoped)
    session belongs to — a sub-user cannot point at another user's business.

Reuses app_identity.validate_session + app_entitlements.get_active_entitlement; adds only the ownership
read. The returned CapabilityScope is then signed by safebox_capability.mint_capability.
"""
from __future__ import annotations

from collections.abc import Mapping

from .safebox_capability import CapabilityScope

_UNENTITLING = {"", "free", "none", "unentitled"}


class AuthzError(Exception):
    """Identity validation failed — no capability token may be minted."""


def _resolve_owner_user_id(conn, business_slug: str) -> str:
    row = conn.execute(
        "select owner_user_id from businesses where slug = %s", (business_slug,)
    ).fetchone()
    if row is None:
        raise AuthzError("unknown_business")
    owner = str((row["owner_user_id"] if isinstance(row, Mapping) else row[0]) or "").strip()
    if not owner:
        raise AuthzError("business_owner_missing")
    return owner


def authorize_product_call(
    conn,
    *,
    business_slug: str,
    session_token: str,
    action: str,
    max_cost_microusd: int,
) -> CapabilityScope:
    """Boundary 2 + 1 for a product (sub-user) call. Returns the authoritative scope or raises."""
    from . import app_entitlements, app_identity

    business_slug = str(business_slug or "").strip()
    action = str(action or "").strip()
    if not business_slug or not action:
        raise AuthzError("missing_identity")
    user = app_identity.validate_session(conn, business_slug, session_token)
    if user is None:
        raise AuthzError("invalid_session")
    app_user_id = str(getattr(user, "id", "") or "").strip()
    if not app_user_id:
        raise AuthzError("invalid_session")
    if int(max_cost_microusd) > 0:
        ent = app_entitlements.get_active_entitlement(conn, business_slug, app_user_id)
        if ent is None or str(getattr(ent, "tier", "") or "").strip().lower() in _UNENTITLING:
            raise AuthzError("subscription_required")
    owner_user_id = _resolve_owner_user_id(conn, business_slug)
    return CapabilityScope(
        takyon_user_id=owner_user_id,
        business_slug=business_slug,
        app_user_id=app_user_id,
        action=action,
        max_cost_microusd=int(max_cost_microusd),
    )


def authorize_operator_call(
    conn,
    *,
    business_slug: str,
    operator_user_id: str,
    action: str,
    max_cost_microusd: int,
) -> CapabilityScope:
    """Boundary 1 for an operator/platform call (no product sub-user). The authenticated operator must
    OWN the business. Returns the authoritative scope or raises."""
    business_slug = str(business_slug or "").strip()
    operator_user_id = str(operator_user_id or "").strip()
    action = str(action or "").strip()
    if not business_slug or not operator_user_id or not action:
        raise AuthzError("missing_identity")
    owner_user_id = _resolve_owner_user_id(conn, business_slug)
    if owner_user_id != operator_user_id:
        raise AuthzError("not_business_owner")
    return CapabilityScope(
        takyon_user_id=owner_user_id,
        business_slug=business_slug,
        app_user_id=None,
        action=action,
        max_cost_microusd=int(max_cost_microusd),
    )
