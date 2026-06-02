"""FastAPI Control API — the opaque Takyon-user boundary (Phase 1 read path).

Bearer auth: `Authorization: Bearer tk_...` is the only user-provided input. It is
resolved (`resolve_api_key`) to a small `ResolvedPrincipal`; endpoints then return
only the deliberately-exposed projection (identity + owned business slugs / their
read-only fields). Provider keys, other tenants, billing internals, and
control-plane handles are never reachable through this surface.

DB-agnostic by design: endpoints depend on `get_control_conn`, which the host app
overrides — tests with a throwaway-DB connection, production with a pooled one.
Mounting this router into the live dashboard app is a separate, deliberate step; the
module is standalone so the boundary can be verified without disturbing the current
SQLite dashboard runtime.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, Field

from . import billing, business_credits, custody, rate_limit, stripe_util
from .control_plane import ResolvedPrincipal, resolve_api_key

_BEARER_PREFIX = "Bearer "
_UNAUTH_HEADERS = {"WWW-Authenticate": "Bearer"}


class TopupCheckoutRequest(BaseModel):
    """Body for POST /v1/billing/topup/checkout. `amount_cents` is exact money the user
    pays in (flow A — topups ARE money, unlike allowance). success_url/cancel_url are
    where Stripe returns the user after hosted checkout; the caller supplies them, mirroring
    the product-checkout convention, so the server never invents or open-redirects to a
    target it picked."""

    amount_cents: int = Field(..., gt=0)
    success_url: str = Field(..., min_length=1)
    cancel_url: str = Field(..., min_length=1)


class CreativeCreditCheckoutRequest(BaseModel):
    """Body for POST /v1/businesses/{slug}/creative-credits/checkout."""

    pack_id: str = Field(..., min_length=1)
    success_url: str = Field(..., min_length=1)
    cancel_url: str = Field(..., min_length=1)


class PayoutConnectRequest(BaseModel):
    """Body for POST /v1/me/payouts/connect."""

    return_url: str = Field(..., min_length=1)
    refresh_url: str = Field(..., min_length=1)


@dataclass(frozen=True)
class OperatorPayoutState:
    user_id: str
    stripe_connect_account_id: str | None
    stripe_connect_status: str
    payouts_enabled: bool
    details_submitted: bool
    payout_currency: str
    owed_balance_cents: int
    paid_out_cents: int


def _stripe_connect_country() -> str:
    raw = str(os.environ.get("TAKYON_STRIPE_CONNECT_COUNTRY") or "US").strip().upper()
    if len(raw) == 2 and raw.isalpha():
        return raw
    return "US"


def _classify_connect_status(account: dict[str, Any]) -> tuple[str, bool, bool]:
    payouts_enabled = bool(account.get("payouts_enabled"))
    details_submitted = bool(account.get("details_submitted"))
    requirements = (
        account.get("requirements") if isinstance(account.get("requirements"), dict) else {}
    )
    disabled_reason = str(requirements.get("disabled_reason") or "").strip()
    past_due = requirements.get("past_due")
    if payouts_enabled:
        return "active", payouts_enabled, details_submitted
    if disabled_reason or (isinstance(past_due, list) and past_due):
        return "restricted", payouts_enabled, details_submitted
    return "pending", payouts_enabled, details_submitted


def _read_operator_payout_row(conn, user_id: str, *, for_update: bool = False):
    sql = (
        "select stripe_connect_account_id, stripe_connect_status, payout_currency, email "
        "from users where id = %s"
    )
    if for_update:
        sql += " for update"
    row = conn.execute(sql, (user_id,)).fetchone()
    if row is None:
        raise LookupError(f"user_not_found:{user_id}")
    return row


def create_topup_checkout_session(
    user_id: str,
    *,
    amount_cents: int,
    success_url: str,
    cancel_url: str,
) -> dict[str, Any]:
    params = {
        "mode": "payment",
        "client_reference_id": user_id,
        "success_url": success_url,
        "cancel_url": cancel_url,
        "line_items[0][quantity]": 1,
        "line_items[0][price_data][currency]": "usd",
        "line_items[0][price_data][unit_amount]": amount_cents,
        "line_items[0][price_data][product_data][name]": "Takyon balance top-up",
        "metadata[purpose]": "takyon_topup",
        "metadata[user_id]": user_id,
        "payment_intent_data[metadata][purpose]": "takyon_topup",
        "payment_intent_data[metadata][user_id]": user_id,
    }
    return stripe_util.stripe_request("checkout/sessions", params)


def get_operator_payout_state(
    conn,
    user_id: str,
    *,
    refresh_live: bool = True,
) -> OperatorPayoutState:
    row = _read_operator_payout_row(conn, user_id, for_update=False)
    account_id = None if row[0] is None else str(row[0])
    cached_status = str(row[1] or "none")
    payout_currency = str(row[2] or "usd")
    payouts_enabled = cached_status == "active"
    details_submitted = cached_status in {"pending", "active", "restricted"}

    if refresh_live and account_id:
        try:
            account = stripe_util.stripe_request(f"accounts/{account_id}", {}, method="GET")
        except stripe_util.StripeError:
            account = None
        if account is not None:
            status, payouts_enabled, details_submitted = _classify_connect_status(account)
            payout_currency = str(
                account.get("default_currency") or payout_currency or "usd"
            ).lower()
            if status != cached_status or payout_currency != str(row[2] or "usd"):
                with conn.transaction():
                    _read_operator_payout_row(conn, user_id, for_update=True)
                    conn.execute(
                        "update users set stripe_connect_status = %s, payout_currency = %s "
                        "where id = %s",
                        (status, payout_currency, user_id),
                    )
            cached_status = status

    custody.open_custody_account(conn, user_id)
    balances = custody.get_custody_balances(conn, user_id)
    return OperatorPayoutState(
        user_id=user_id,
        stripe_connect_account_id=account_id,
        stripe_connect_status=cached_status,
        payouts_enabled=payouts_enabled,
        details_submitted=details_submitted,
        payout_currency=str(balances.currency or payout_currency or "usd").lower(),
        owed_balance_cents=int(balances.owed_balance_cents),
        paid_out_cents=int(balances.paid_out_cents),
    )


def create_operator_payout_connect_link(
    conn,
    user_id: str,
    *,
    return_url: str,
    refresh_url: str,
) -> dict[str, Any]:
    if not str(return_url or "").strip():
        raise ValueError("return_url is required")
    if not str(refresh_url or "").strip():
        raise ValueError("refresh_url is required")
    with conn.transaction():
        row = _read_operator_payout_row(conn, user_id, for_update=True)
        account_id = None if row[0] is None else str(row[0])
        cached_status = str(row[1] or "none")
        payout_currency = str(row[2] or "usd").lower()
        email = str(row[3] or "").strip() or None

        account_payload: dict[str, Any] | None = None
        if account_id:
            account_payload = stripe_util.stripe_request(
                f"accounts/{account_id}", {}, method="GET"
            )
            cached_status, _payouts_enabled, _details_submitted = _classify_connect_status(
                account_payload
            )
            payout_currency = str(
                account_payload.get("default_currency") or payout_currency or "usd"
            ).lower()
            conn.execute(
                "update users set stripe_connect_status = %s, payout_currency = %s where id = %s",
                (cached_status, payout_currency, user_id),
            )

        if not account_id:
            params = {
                "type": "express",
                "country": _stripe_connect_country(),
                "default_currency": payout_currency or "usd",
                "capabilities[transfers][requested]": "true",
                "metadata[takyon_user_id]": user_id,
                "metadata[purpose]": "operator_payouts",
            }
            if email:
                params["email"] = email
            account_payload = stripe_util.stripe_request("accounts", params)
            account_id = str(account_payload.get("id") or "").strip()
            if not account_id:
                raise stripe_util.StripeError("Stripe account creation returned no account id")
            cached_status, _payouts_enabled, _details_submitted = _classify_connect_status(
                account_payload
            )
            payout_currency = str(
                account_payload.get("default_currency") or payout_currency or "usd"
            ).lower()
            conn.execute(
                "update users set stripe_connect_account_id = %s, stripe_connect_status = %s, "
                "payout_currency = %s where id = %s",
                (account_id, cached_status, payout_currency, user_id),
            )

    if cached_status == "active":
        link = stripe_util.stripe_request(f"accounts/{account_id}/login_links", {})
        return {
            "url": link.get("url"),
            "link_type": "login_link",
            "stripe_connect_account_id": account_id,
            "stripe_connect_status": cached_status,
        }

    link = stripe_util.stripe_request(
        "account_links",
        {
            "account": account_id,
            "refresh_url": refresh_url,
            "return_url": return_url,
            "type": "account_onboarding",
        },
    )
    return {
        "url": link.get("url"),
        "link_type": "account_onboarding",
        "stripe_connect_account_id": account_id,
        "stripe_connect_status": cached_status,
    }


def get_control_conn():
    """Dependency seam for the per-request control-plane connection.

    Unconfigured by default — the host app MUST override it
    (`app.dependency_overrides[get_control_conn] = ...`). This keeps the router free
    of any connection/pool strategy so the same code serves tests and production.
    """
    raise RuntimeError("control-plane connection not configured")


def _resolve_principal(
    authorization: str | None = Header(default=None),
    conn=Depends(get_control_conn),
) -> ResolvedPrincipal:
    """Turn the presented bearer token into a principal, or refuse with one
    undifferentiated 401. Malformed, unknown, revoked, and non-active all look
    identical from outside — the boundary never reveals which, nor whether any key
    or user exists."""
    if not authorization or not authorization.startswith(_BEARER_PREFIX):
        raise HTTPException(
            status_code=401, detail="missing_bearer_token", headers=_UNAUTH_HEADERS
        )
    raw = authorization[len(_BEARER_PREFIX) :].strip()
    principal = resolve_api_key(conn, raw)
    if principal is None:
        raise HTTPException(
            status_code=401, detail="invalid_api_key", headers=_UNAUTH_HEADERS
        )
    return principal


def _positive_int_env(name: str, default: int) -> int:
    """Read a positive-int knob from the environment, falling back to `default` when
    unset, empty, non-integer, or non-positive. Config (not a secret), so it is read
    straight from os.environ like the rest of the control plane."""
    raw = os.environ.get(name)
    if not raw or not raw.strip():
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return value if value > 0 else default


def _rate_limit_config() -> tuple[int, int]:
    """Per-user control-plane rate limit: (max requests, window seconds). Defaults to
    120 requests / 60s; override via TAKYON_CONTROL_RATE_LIMIT and
    TAKYON_CONTROL_RATE_WINDOW_SECONDS."""
    return (
        _positive_int_env("TAKYON_CONTROL_RATE_LIMIT", 120),
        _positive_int_env("TAKYON_CONTROL_RATE_WINDOW_SECONDS", 60),
    )


def _creative_credit_packs() -> list[dict[str, Any]]:
    """Configured creative-credit packs from `TAKYON_CREATIVE_CREDIT_PACKS_JSON`.

    Shape: a JSON array of objects carrying `id`, `credits`, and `amount_cents`, plus
    optional `name` / `description`. Invalid or missing config yields an empty catalog
    rather than crashing the whole boundary.
    """
    raw = os.environ.get("TAKYON_CREATIVE_CREDIT_PACKS_JSON", "").strip()
    if not raw:
        return []
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if not isinstance(payload, list):
        return []
    packs: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in payload:
        if not isinstance(item, dict):
            continue
        pack_id = str(item.get("id") or "").strip()
        if not pack_id or pack_id in seen:
            continue
        try:
            credits = int(item.get("credits") or 0)
            amount_cents = int(item.get("amount_cents") or 0)
        except (TypeError, ValueError):
            continue
        if credits <= 0 or amount_cents <= 0:
            continue
        seen.add(pack_id)
        pack = {
            "id": pack_id,
            "name": str(item.get("name") or pack_id),
            "description": str(item.get("description") or ""),
            "credits": credits,
            "amount_cents": amount_cents,
            "currency": "usd",
        }
        packs.append(pack)
    return packs


def _creative_credit_pack(pack_id: str) -> dict[str, Any] | None:
    target = str(pack_id or "").strip()
    if not target:
        return None
    for pack in _creative_credit_packs():
        if pack["id"] == target:
            return pack
    return None


def configured_creative_credit_packs() -> list[dict[str, Any]]:
    """Return the configured creative-credit packs for shared UI/read paths."""
    return _creative_credit_packs()


def create_creative_credit_checkout_session(
    user_id: str,
    slug: str,
    *,
    pack_id: str,
    success_url: str,
    cancel_url: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Create a Stripe Checkout session for one business creative-credit pack."""
    pack = _creative_credit_pack(pack_id)
    if pack is None:
        raise LookupError(f"unknown_credit_pack:{pack_id}")
    params = {
        "mode": "payment",
        "client_reference_id": slug,
        "success_url": success_url,
        "cancel_url": cancel_url,
        "line_items[0][quantity]": 1,
        "line_items[0][price_data][currency]": pack["currency"],
        "line_items[0][price_data][unit_amount]": pack["amount_cents"],
        "line_items[0][price_data][product_data][name]": (
            f"Takyon creative credit pack ({pack['credits']} credits)"
        ),
        "metadata[purpose]": "creative_credit_pack",
        "metadata[user_id]": user_id,
        "metadata[business_slug]": slug,
        "metadata[pack_id]": pack["id"],
        "metadata[credits]": pack["credits"],
        "payment_intent_data[metadata][purpose]": "creative_credit_pack",
        "payment_intent_data[metadata][user_id]": user_id,
        "payment_intent_data[metadata][business_slug]": slug,
        "payment_intent_data[metadata][pack_id]": pack["id"],
        "payment_intent_data[metadata][credits]": pack["credits"],
    }
    session = stripe_util.stripe_request("checkout/sessions", params)
    return session, pack


def _rate_limited_principal(
    principal: ResolvedPrincipal = Depends(_resolve_principal),
    conn=Depends(get_control_conn),
) -> ResolvedPrincipal:
    """Resolve the bearer principal, then count this request against the caller's own
    fixed window. Over the cap → 429 with a Retry-After hint. Applied to the
    authenticated read/checkout endpoints; the Stripe webhook is deliberately exempt
    (it is signature-authenticated, carries no bearer principal, and Stripe's retries
    must not be throttled)."""
    limit, window_seconds = _rate_limit_config()
    result = rate_limit.check_rate_limit(
        conn, principal.user_id, limit=limit, window_seconds=window_seconds
    )
    if not result.allowed:
        raise HTTPException(
            status_code=429,
            detail="rate_limited",
            headers={"Retry-After": str(max(1, result.retry_after_seconds))},
        )
    return principal


def build_control_router() -> APIRouter:
    """Build the `/v1` Control API router. Call `app.include_router(...)` on it and
    override `get_control_conn` to supply connections."""
    router = APIRouter(prefix="/v1")

    @router.get("/me")
    def get_me(
        principal: ResolvedPrincipal = Depends(_rate_limited_principal),
    ) -> dict[str, Any]:
        # Identity projection only. Topup balance (money) + allowance (opaque
        # "included usage") join here once the billing/custody ledgers exist; we do
        # NOT fabricate them in the meantime.
        return {"user_id": principal.user_id, "status": principal.status}

    @router.get("/me/payouts")
    def get_my_payouts(
        principal: ResolvedPrincipal = Depends(_rate_limited_principal),
        conn=Depends(get_control_conn),
    ) -> dict[str, Any]:
        state = get_operator_payout_state(conn, principal.user_id, refresh_live=True)
        return {
            "user_id": state.user_id,
            "stripe_connect_account_id": state.stripe_connect_account_id,
            "stripe_connect_status": state.stripe_connect_status,
            "payouts_enabled": state.payouts_enabled,
            "details_submitted": state.details_submitted,
            "payout_currency": state.payout_currency,
            "owed_balance_cents": state.owed_balance_cents,
            "paid_out_cents": state.paid_out_cents,
        }

    @router.post("/me/payouts/connect")
    def connect_my_payouts(
        body: PayoutConnectRequest,
        principal: ResolvedPrincipal = Depends(_rate_limited_principal),
        conn=Depends(get_control_conn),
    ) -> dict[str, Any]:
        try:
            link = create_operator_payout_connect_link(
                conn,
                principal.user_id,
                return_url=body.return_url,
                refresh_url=body.refresh_url,
            )
        except LookupError:
            raise HTTPException(status_code=404, detail="user_not_found")
        except stripe_util.StripeError as exc:
            msg = str(exc)
            if "STRIPE_SECRET_KEY" in msg:
                raise HTTPException(
                    status_code=503, detail="payout_connect_unconfigured"
                ) from exc
            raise HTTPException(status_code=502, detail=f"stripe_error: {msg}") from exc
        return {
            "connect_url": link.get("url"),
            "link_type": link.get("link_type"),
            "stripe_connect_account_id": link.get("stripe_connect_account_id"),
            "stripe_connect_status": link.get("stripe_connect_status"),
        }

    @router.get("/businesses")
    def list_businesses(
        principal: ResolvedPrincipal = Depends(_rate_limited_principal),
        conn=Depends(get_control_conn),
    ) -> dict[str, Any]:
        rows = conn.execute(
            "select slug, name, mode from businesses "
            "where owner_user_id = %s order by slug",
            (principal.user_id,),
        ).fetchall()
        return {
            "businesses": [{"slug": r[0], "name": r[1], "mode": r[2]} for r in rows]
        }

    @router.get("/businesses/{slug}")
    def get_business(
        slug: str,
        principal: ResolvedPrincipal = Depends(_rate_limited_principal),
        conn=Depends(get_control_conn),
    ) -> dict[str, Any]:
        # Consult ONLY the caller's owned set. A slug the caller doesn't own returns
        # 404 (not 403): revealing "exists but not yours" would make this surface a
        # cross-tenant existence oracle, and "other tenants are unreachable" outranks
        # the plan's looser 403 wording.
        if slug not in principal.business_slugs:
            raise HTTPException(status_code=404, detail="not_found")
        row = conn.execute(
            "select slug, name, mode from businesses where slug = %s", (slug,)
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="not_found")
        return {"slug": row[0], "name": row[1], "mode": row[2]}

    @router.get("/businesses/{slug}/creative-credits")
    def get_creative_credits(
        slug: str,
        principal: ResolvedPrincipal = Depends(_rate_limited_principal),
        conn=Depends(get_control_conn),
    ) -> dict[str, Any]:
        if slug not in principal.business_slugs:
            raise HTTPException(status_code=404, detail="not_found")
        row = conn.execute("select 1 from businesses where slug = %s", (slug,)).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="not_found")
        balances = business_credits.get_business_credit_balances(conn, slug)
        return {
            "business_slug": slug,
            "balance_credits": balances.balance_credits,
            "reserved_credits": balances.reserved_credits,
        }

    @router.get("/businesses/{slug}/creative-credits/packs")
    def list_creative_credit_packs(
        slug: str,
        principal: ResolvedPrincipal = Depends(_rate_limited_principal),
        conn=Depends(get_control_conn),
    ) -> dict[str, Any]:
        if slug not in principal.business_slugs:
            raise HTTPException(status_code=404, detail="not_found")
        row = conn.execute("select 1 from businesses where slug = %s", (slug,)).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="not_found")
        return {"business_slug": slug, "packs": configured_creative_credit_packs()}

    @router.post("/businesses/{slug}/creative-credits/checkout")
    def create_creative_credit_checkout(
        slug: str,
        body: CreativeCreditCheckoutRequest,
        principal: ResolvedPrincipal = Depends(_rate_limited_principal),
        conn=Depends(get_control_conn),
    ) -> dict[str, Any]:
        if slug not in principal.business_slugs:
            raise HTTPException(status_code=404, detail="not_found")
        row = conn.execute(
            "select slug, name from businesses where slug = %s",
            (slug,),
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="not_found")
        try:
            session, pack = create_creative_credit_checkout_session(
                principal.user_id,
                slug,
                pack_id=body.pack_id,
                success_url=body.success_url,
                cancel_url=body.cancel_url,
            )
        except LookupError as exc:
            raise HTTPException(status_code=404, detail="unknown_credit_pack") from exc
        except stripe_util.StripeError as exc:
            msg = str(exc)
            if "STRIPE_SECRET_KEY" in msg:
                raise HTTPException(
                    status_code=503, detail="creative_credit_checkout_unconfigured"
                ) from exc
            raise HTTPException(status_code=502, detail=f"stripe_error: {msg}") from exc
        return {
            "checkout_url": session.get("url"),
            "session_id": session.get("id"),
            "business_slug": slug,
            "pack_id": pack["id"],
            "credits": pack["credits"],
            "amount_cents": pack["amount_cents"],
        }

    @router.post("/billing/topup/checkout")
    def create_topup_checkout(
        body: TopupCheckoutRequest,
        principal: ResolvedPrincipal = Depends(_rate_limited_principal),
    ) -> dict[str, Any]:
        """Create a Stripe Checkout session that tops up the CALLER's own balance (flow A).
        client_reference_id + metadata.purpose=takyon_topup let the billing webhook credit
        the right user exactly once when payment completes. Requires STRIPE_SECRET_KEY; if
        it is absent the call is blocked (503) with a reason — never a faked URL."""
        try:
            session = create_topup_checkout_session(
                principal.user_id,
                amount_cents=body.amount_cents,
                success_url=body.success_url,
                cancel_url=body.cancel_url,
            )
        except stripe_util.StripeError as exc:
            msg = str(exc)
            if "STRIPE_SECRET_KEY" in msg:
                raise HTTPException(status_code=503, detail="topup_unconfigured") from exc
            raise HTTPException(status_code=502, detail=f"stripe_error: {msg}") from exc
        return {
            "checkout_url": session.get("url"),
            "session_id": session.get("id"),
            "amount_cents": body.amount_cents,
        }

    @router.post("/billing/webhook")
    async def billing_webhook(
        request: Request,
        conn=Depends(get_control_conn),
    ) -> dict[str, Any]:
        """Dedicated control-plane webhook for flow-A topups — SEPARATE from the product
        (flow B) webhook so it carries its OWN signing secret. Verifies the raw body with
        STRIPE_BILLING_WEBHOOK_SECRET; if that secret is absent the event is NOT trusted and
        we return 503 so Stripe retries — crediting is never faked around a missing
        credential. A paid checkout.session.completed bearing metadata.purpose=takyon_topup
        credits the user once, idempotent on the Stripe event id."""
        secret = os.environ.get("STRIPE_BILLING_WEBHOOK_SECRET")
        if not secret:
            raise HTTPException(status_code=503, detail="billing_webhook_unconfigured")
        raw = (await request.body()).decode("utf-8")
        signature = request.headers.get("stripe-signature", "")
        try:
            stripe_util.verify_stripe_signature(raw, signature, secret)
        except stripe_util.StripeError:
            raise HTTPException(status_code=400, detail="invalid_signature")
        event = json.loads(raw)
        event_id = str(event.get("id") or "")
        event_type = str(event.get("type") or "")
        if event_type != "checkout.session.completed":
            return {"ok": True, "ignored": event_type or "unknown_event"}
        session = (event.get("data") or {}).get("object") or {}
        metadata = session.get("metadata") or {}
        if session.get("payment_status") not in ("paid", "no_payment_required"):
            return {"ok": True, "ignored": "unpaid"}
        purpose = str(metadata.get("purpose") or "")
        if purpose == "creative_credit_pack":
            business_slug = str(
                metadata.get("business_slug") or session.get("client_reference_id") or ""
            ).strip()
            pack_id = str(metadata.get("pack_id") or "").strip()
            try:
                credits = int(metadata.get("credits") or 0)
            except (TypeError, ValueError):
                credits = 0
            if not business_slug or credits <= 0 or not event_id:
                return {"ok": True, "ignored": "incomplete_session"}
            balances = business_credits.grant_credits(
                conn,
                business_slug,
                credits,
                idempotency_key=event_id,
                metadata={
                    "purpose": purpose,
                    "pack_id": pack_id,
                    "user_id": metadata.get("user_id"),
                    "stripe_checkout_session_id": session.get("id"),
                    "amount_cents": int(session.get("amount_total") or 0),
                },
                stripe_ref=str(session.get("id") or ""),
            )
            return {
                "ok": True,
                "business_slug": business_slug,
                "credited_credits": credits,
                "balance_credits": balances.balance_credits,
                "reserved_credits": balances.reserved_credits,
                "event_id": event_id,
            }
        if purpose != "takyon_topup":
            return {"ok": True, "ignored": "not_a_topup"}
        user_id = session.get("client_reference_id") or metadata.get("user_id")
        amount = int(session.get("amount_total") or 0)
        if not user_id or amount <= 0 or not event_id:
            return {"ok": True, "ignored": "incomplete_session"}
        new_balance = billing.topup(conn, user_id, amount, idempotency_key=event_id)
        return {
            "ok": True,
            "credited_cents": amount,
            "topup_balance_cents": new_balance,
            "event_id": event_id,
        }

    return router
