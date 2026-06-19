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
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, Field

from . import billing, custody, rate_limit, safebox, stripe_util
from .control_plane import ResolvedPrincipal, resolve_api_key

_BEARER_PREFIX = "Bearer "
_UNAUTH_HEADERS = {"WWW-Authenticate": "Bearer"}


class OperatorSubscriptionCheckoutRequest(BaseModel):
    """Body for POST /v1/billing/subscription/checkout. `plan_id` selects one configured
    operator tier; the server maps it to the tier's Stripe price (the caller never supplies
    a price or amount, so a tier's economic terms cannot be substituted)."""

    plan_id: str = Field(..., min_length=1)
    success_url: str = Field(..., min_length=1)
    cancel_url: str = Field(..., min_length=1)


class CreativeCreditCheckoutRequest(BaseModel):
    """Body for POST /v1/businesses/{slug}/creative-credits/checkout."""

    credits: int | None = Field(default=None, gt=0)
    pack_id: str | None = Field(default=None, min_length=1)
    success_url: str = Field(..., min_length=1)
    cancel_url: str = Field(..., min_length=1)


class CreativeCreditReconcileRequest(BaseModel):
    """Body for POST /v1/businesses/{slug}/creative-credits/reconcile."""

    session_id: str = Field(..., min_length=1)


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


@dataclass(frozen=True)
class OperatorSubscriptionState:
    user_id: str
    customer_id: str | None
    subscription_id: str | None
    subscription_status: str
    plan_name: str | None
    weekly_allowance_cents: int
    allowance_period_start: str | None
    allowance_resets_at: str | None
    synced: bool


_ALLOWANCE_BEARING_SUBSCRIPTION_STATUSES = {"active", "trialing"}


def _stripe_connect_country() -> str:
    raw = str(_env_value("TAKYON_STRIPE_CONNECT_COUNTRY") or "US").strip().upper()
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


def _read_operator_billing_row(conn, user_id: str, *, for_update: bool = False):
    sql = (
        "select email, operator_billing_customer_id, operator_billing_subscription_id, "
        "operator_billing_subscription_status "
        "from users where id = %s"
    )
    if for_update:
        sql += " for update"
    row = conn.execute(sql, (user_id,)).fetchone()
    if row is None:
        raise LookupError(f"user_not_found:{user_id}")
    return row


def _metadata_int(metadata: dict[str, Any], key: str) -> int | None:
    raw = metadata.get(key)
    if raw in (None, ""):
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _env_nonnegative_int(name: str, default: int) -> int:
    raw = str(os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        return max(0, int(raw))
    except ValueError:
        return default


def _operator_billing_customer_search(user_id: str) -> dict[str, Any] | None:
    payload = stripe_util.stripe_request(
        "customers/search",
        {
            "query": (
                f"metadata['takyon_user_id']:'{user_id}' "
                "AND metadata['purpose']:'operator_billing'"
            ),
            "limit": 1,
        },
        method="GET",
    )
    rows = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        return None
    for item in rows:
        if isinstance(item, dict) and not item.get("deleted"):
            return item
    return None


def _persist_operator_billing_identity(
    conn,
    user_id: str,
    *,
    customer_id: str | None = None,
    subscription_id: str | None = None,
    subscription_status: str | None = None,
) -> None:
    sets: list[str] = []
    params: list[Any] = []
    if customer_id is not None:
        sets.append("operator_billing_customer_id = %s")
        params.append(customer_id or None)
    if subscription_id is not None:
        sets.append("operator_billing_subscription_id = %s")
        params.append(subscription_id or None)
    if subscription_status is not None:
        sets.append("operator_billing_subscription_status = %s")
        params.append(subscription_status or "none")
    if not sets:
        return
    params.append(user_id)
    with conn.transaction():
        _read_operator_billing_row(conn, user_id, for_update=True)
        conn.execute(f"update users set {', '.join(sets)} where id = %s", tuple(params))


def _stripe_customer_id(payload: Any) -> str | None:
    if isinstance(payload, str):
        return payload.strip() or None
    if isinstance(payload, dict):
        customer_id = str(payload.get("id") or "").strip()
        return customer_id or None
    return None


def _subscription_items(subscription: dict[str, Any]) -> list[dict[str, Any]]:
    items = subscription.get("items") if isinstance(subscription.get("items"), dict) else {}
    rows = items.get("data") if isinstance(items.get("data"), list) else []
    return [item for item in rows if isinstance(item, dict)]


def _weekly_allowance_from_subscription_item(item: dict[str, Any]) -> int:
    price = item.get("price") if isinstance(item.get("price"), dict) else {}
    metadata = price.get("metadata") if isinstance(price.get("metadata"), dict) else {}
    quantity_raw = item.get("quantity")
    try:
        quantity = max(1, int(quantity_raw or 1))
    except (TypeError, ValueError):
        quantity = 1

    override = _metadata_int(metadata, "takyon_allowance_weekly_cents")
    if override is not None:
        return max(0, override * quantity)

    try:
        amount_cents = max(0, int(price.get("unit_amount") or 0)) * quantity
    except (TypeError, ValueError):
        amount_cents = 0
    if amount_cents <= 0:
        return 0

    recurring = price.get("recurring") if isinstance(price.get("recurring"), dict) else {}
    interval = str(recurring.get("interval") or "").strip().lower()
    try:
        interval_count = max(1, int(recurring.get("interval_count") or 1))
    except (TypeError, ValueError):
        interval_count = 1

    if interval == "week":
        return max(0, int(round(amount_cents / interval_count)))
    if interval == "day":
        return max(0, int(round(amount_cents * 7 / interval_count)))
    if interval == "month":
        return max(0, int(round(amount_cents * 12 / (52 * interval_count))))
    if interval == "year":
        return max(0, int(round(amount_cents / (52 * interval_count))))
    return amount_cents


def _operator_subscription_weekly_allowance_cents(subscription: dict[str, Any]) -> int:
    metadata = subscription.get("metadata") if isinstance(subscription.get("metadata"), dict) else {}
    override = _metadata_int(metadata, "takyon_allowance_weekly_cents")
    if override is not None:
        return max(0, override)
    return sum(_weekly_allowance_from_subscription_item(item) for item in _subscription_items(subscription))


def _operator_subscription_plan_name(subscription: dict[str, Any] | None) -> str | None:
    if not isinstance(subscription, dict):
        return None
    metadata = subscription.get("metadata") if isinstance(subscription.get("metadata"), dict) else {}
    raw = str(metadata.get("takyon_plan_name") or "").strip()
    if raw:
        return raw
    for item in _subscription_items(subscription):
        price = item.get("price") if isinstance(item.get("price"), dict) else {}
        price_metadata = price.get("metadata") if isinstance(price.get("metadata"), dict) else {}
        candidate = str(price_metadata.get("takyon_plan_name") or price.get("nickname") or "").strip()
        if candidate:
            return candidate
    return None


def _fallback_operator_plan_name() -> str:
    raw = str(os.getenv("TAKYON_OPERATOR_DEFAULT_PLAN_NAME") or "").strip()
    return raw or "DEV"


def _fallback_operator_weekly_allowance_cents() -> int:
    return _env_nonnegative_int("TAKYON_OPERATOR_DEFAULT_WEEKLY_ALLOWANCE_CENTS", 10_000)


def operator_plan_name_for_business(conn, business_slug: str) -> str | None:
    """PURE READ — the operator (business owner)'s effective plan name, for entitlement gates such
    as the wake-cadence floor. NO side effects: it never touches Stripe, never grants allowance,
    never writes billing rows (unlike ``sync_operator_subscription_allowance``).

    Resolution is deliberately conservative and fail-restrictive: it reads the cached
    ``operator_billing_subscription_status`` from the owner's ``users`` row. Only when that status
    is allowance-bearing (active/trialing) does it return the configured plan name
    (``TAKYON_OPERATOR_DEFAULT_PLAN_NAME``, default ``DEV``); otherwise — no business, no owner, no
    active subscription, or a plan DOWNGRADE that dropped the subscription — it returns ``None`` so
    the caller applies the most-restrictive floor. This makes a downgrade TIGHTEN the wake-cadence
    floor, never loosen it (Cron-scheduling acceptance #5)."""
    row = conn.execute(
        "select u.operator_billing_subscription_status "
        "from businesses b join users u on u.id = b.owner_user_id "
        "where b.slug = %s",
        (business_slug,),
    ).fetchone()
    if row is None:
        return None
    status = str(row[0] or "none").strip().lower() or "none"
    if status not in _ALLOWANCE_BEARING_SUBSCRIPTION_STATUSES:
        return None
    return _fallback_operator_plan_name()


def _operator_plan_catalog() -> list[dict[str, Any]]:
    """Configured operator subscription tiers from `TAKYON_OPERATOR_PLANS_JSON`.

    Multi-tier operator billing is BUY-not-build (GOAL_RULES §4): every tier maps to a
    real Stripe Price (`price_id`) on a Stripe Product, and the recurring allowance/plan
    name ride as price metadata that `sync_operator_subscription_allowance` already
    resolves (`takyon_plan_name` / `takyon_allowance_weekly_cents`). This catalog is the
    operator-facing menu the dashboard renders and the checkout endpoint validates a
    chosen tier against; it never invents a price the operator did not configure in Stripe.

    Shape: a JSON array of objects with `id` (stable tier key), `price_id` (Stripe price),
    and `name`, plus optional `description`, `weekly_allowance_cents`, `amount_cents`,
    `interval`, `featured`, `tagline`, and `features` (string list). Invalid/missing config
    yields an empty catalog rather than crashing the boundary. As a single-tier
    compatibility fallback, an unset catalog with `STRIPE_PRICE_PLATFORM_MONTHLY` present
    synthesizes one default tier so the existing single price keeps working.
    """
    raw = _env_value("TAKYON_OPERATOR_PLANS_JSON")
    plans: list[dict[str, Any]] = []
    seen: set[str] = set()
    if raw:
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            payload = None
        if isinstance(payload, list):
            for item in payload:
                if not isinstance(item, dict):
                    continue
                plan_id = str(item.get("id") or "").strip()
                price_id = str(item.get("price_id") or item.get("priceId") or "").strip()
                if not plan_id or not price_id or plan_id in seen:
                    continue
                seen.add(plan_id)
                feature_list = item.get("features")
                features = (
                    [str(f).strip() for f in feature_list if str(f).strip()]
                    if isinstance(feature_list, list)
                    else []
                )
                try:
                    weekly_allowance_cents = max(0, int(item.get("weekly_allowance_cents") or 0))
                except (TypeError, ValueError):
                    weekly_allowance_cents = 0
                try:
                    amount_cents = max(0, int(item.get("amount_cents") or 0))
                except (TypeError, ValueError):
                    amount_cents = 0
                plans.append(
                    {
                        "id": plan_id,
                        "price_id": price_id,
                        "name": str(item.get("name") or plan_id),
                        "description": str(item.get("description") or ""),
                        "tagline": str(item.get("tagline") or ""),
                        "weekly_allowance_cents": weekly_allowance_cents,
                        "amount_cents": amount_cents,
                        "currency": str(item.get("currency") or "usd").lower(),
                        "interval": str(item.get("interval") or "month").lower(),
                        "featured": bool(item.get("featured")),
                        "features": features,
                    }
                )
    if plans:
        return plans
    # Single-tier compatibility: surface the legacy single price as one default tier so a
    # not-yet-multi-tier deployment still has a wired, checkout-able operator plan.
    legacy_price = _env_value("STRIPE_PRICE_PLATFORM_MONTHLY")
    if legacy_price:
        return [
            {
                "id": "platform-monthly",
                "price_id": legacy_price,
                "name": _fallback_operator_plan_name() or "Platform",
                "description": "",
                "tagline": "",
                "weekly_allowance_cents": _fallback_operator_weekly_allowance_cents(),
                "amount_cents": 0,
                "currency": "usd",
                "interval": "month",
                "featured": True,
                "features": [],
            }
        ]
    return []


def configured_operator_plans() -> list[dict[str, Any]]:
    """Return the configured operator subscription tiers for shared UI/read paths."""
    return _operator_plan_catalog()


def _operator_plan(plan_id: str) -> dict[str, Any] | None:
    target = str(plan_id or "").strip()
    if not target:
        return None
    for plan in _operator_plan_catalog():
        if plan["id"] == target:
            return plan
    return None


def create_operator_subscription_checkout_session(
    user_id: str,
    *,
    plan_id: str | None = None,
    price_id: str | None = None,
    success_url: str,
    cancel_url: str,
    customer_id: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Create a Stripe subscription-mode Checkout session for an operator tier.

    The caller selects a tier by `plan_id` (validated against the configured catalog) or,
    as a compatibility path, a raw catalog `price_id`. We stamp `takyon_plan_name` and
    `takyon_allowance_weekly_cents` onto the subscription metadata so the billing webhook +
    `sync_operator_subscription_allowance` settle the allowance for the chosen tier exactly
    once. Money authority stays in Stripe (the price) and the existing allowance ledger;
    this only routes the operator to the hosted checkout for a tier they picked.
    """
    plan: dict[str, Any] | None
    if plan_id:
        plan = _operator_plan(plan_id)
        if plan is None:
            raise LookupError(f"unknown_operator_plan:{plan_id}")
    elif price_id:
        target = str(price_id).strip()
        plan = next(
            (p for p in _operator_plan_catalog() if p["price_id"] == target),
            None,
        )
        if plan is None:
            raise LookupError(f"unknown_operator_plan_price:{price_id}")
    else:
        raise ValueError("plan_id or price_id is required")

    params: dict[str, Any] = {
        "mode": "subscription",
        "client_reference_id": user_id,
        "success_url": success_url,
        "cancel_url": cancel_url,
        "line_items[0][price]": plan["price_id"],
        "line_items[0][quantity]": 1,
        "metadata[purpose]": "operator_subscription",
        "metadata[user_id]": user_id,
        "metadata[takyon_plan_id]": plan["id"],
        "metadata[takyon_plan_name]": plan["name"],
        "subscription_data[metadata][purpose]": "operator_subscription",
        "subscription_data[metadata][user_id]": user_id,
        "subscription_data[metadata][takyon_plan_id]": plan["id"],
        "subscription_data[metadata][takyon_plan_name]": plan["name"],
    }
    if int(plan.get("weekly_allowance_cents") or 0) > 0:
        weekly = int(plan["weekly_allowance_cents"])
        params["metadata[takyon_allowance_weekly_cents]"] = weekly
        params["subscription_data[metadata][takyon_allowance_weekly_cents]"] = weekly
    if customer_id:
        params["customer"] = customer_id
    session = stripe_util.stripe_request("checkout/sessions", params)
    return session, plan


def _pick_operator_subscription(payload: dict[str, Any] | None) -> dict[str, Any] | None:
    rows = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        return None
    ranked: list[tuple[int, dict[str, Any]]] = []
    for item in rows:
        if not isinstance(item, dict):
            continue
        status = str(item.get("status") or "").strip().lower()
        if status == "active":
            ranked.append((0, item))
        elif status == "trialing":
            ranked.append((1, item))
    if not ranked:
        return None
    ranked.sort(key=lambda pair: pair[0])
    return ranked[0][1]


def _subscription_status(subscription: dict[str, Any] | None) -> str:
    if not isinstance(subscription, dict):
        return "none"
    return str(subscription.get("status") or "none").strip().lower() or "none"


def _subscription_bears_allowance(subscription: dict[str, Any] | None) -> bool:
    return _subscription_status(subscription) in _ALLOWANCE_BEARING_SUBSCRIPTION_STATUSES


def _weekly_window(now: datetime | None = None) -> tuple[datetime, datetime]:
    current = now.astimezone(timezone.utc) if isinstance(now, datetime) else datetime.now(timezone.utc)
    start = current.replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=7)
    return start, end


def sync_operator_subscription_allowance(
    conn,
    user_id: str,
    *,
    refresh_live: bool = True,
    subscription: dict[str, Any] | None = None,
) -> OperatorSubscriptionState:
    billing.open_billing_account(conn, user_id)
    row = _read_operator_billing_row(conn, user_id, for_update=False)
    customer_id = str(row[1] or "").strip() or None
    cached_subscription_id = str(row[2] or "").strip() or None
    cached_status = str(row[3] or "none").strip().lower() or "none"

    incoming_subscription = subscription if isinstance(subscription, dict) else None
    incoming_subscription_id = str((incoming_subscription or {}).get("id") or "").strip() or None
    incoming_status = _subscription_status(incoming_subscription)
    active_subscription = (
        incoming_subscription
        if incoming_subscription is not None and _subscription_bears_allowance(incoming_subscription)
        else None
    )
    if active_subscription is None and refresh_live and customer_id:
        payload = stripe_util.stripe_request(
            "subscriptions",
            {"customer": customer_id, "status": "all", "limit": 20},
            method="GET",
        )
        active_subscription = _pick_operator_subscription(payload)

    if active_subscription is None:
        inactive_customer_id = customer_id or _stripe_customer_id((incoming_subscription or {}).get("customer"))
        inactive_status = incoming_status if incoming_subscription is not None else "none"
        inactive_subscription_id = incoming_subscription_id
        if (
            inactive_customer_id != customer_id
            or inactive_subscription_id != cached_subscription_id
            or inactive_status != cached_status
        ):
            _persist_operator_billing_identity(
                conn,
                user_id,
                customer_id=inactive_customer_id or "",
                subscription_id=inactive_subscription_id or "",
                subscription_status=inactive_status,
            )
        acct = conn.execute(
            "select allowance_included_cents, allowance_period_start, allowance_resets_at "
            "from billing_accounts where user_id = %s",
            (user_id,),
        ).fetchone()
        synced = False
        current_included = int(acct[0] or 0) if acct else 0
        period_start = acct[1] if acct else None
        resets_at = acct[2] if acct else None
        fallback_allowance_cents = _fallback_operator_weekly_allowance_cents()
        fallback_plan_name = _fallback_operator_plan_name() if fallback_allowance_cents > 0 else None
        if fallback_allowance_cents > 0:
            now = datetime.now(timezone.utc)
            should_refresh = (
                current_included != fallback_allowance_cents
                or not isinstance(resets_at, datetime)
                or resets_at <= now
            )
            if should_refresh:
                reconcile = billing.reconcile_billing(conn, user_id)
                if int(reconcile.get("reserved_allowance_cents") or 0) <= 0:
                    period_start, resets_at = _weekly_window(now)
                    week_key = int(period_start.timestamp() // 604800)
                    billing.grant_allowance(
                        conn,
                        user_id,
                        fallback_allowance_cents,
                        f"operator-plan:{fallback_plan_name or 'default'}:{fallback_allowance_cents}:{week_key}",
                        period_start=period_start,
                        resets_at=resets_at,
                    )
                    synced = True
            return OperatorSubscriptionState(
                user_id=user_id,
                customer_id=inactive_customer_id,
                subscription_id=None,
                subscription_status="none",
                plan_name=fallback_plan_name,
                weekly_allowance_cents=fallback_allowance_cents,
                allowance_period_start=period_start.isoformat() if period_start is not None else None,
                allowance_resets_at=resets_at.isoformat() if resets_at is not None else None,
                synced=synced,
            )
        should_clear_allowance = (
            current_included > 0
            and (
                incoming_subscription is not None
                or cached_subscription_id is not None
                or cached_status in _ALLOWANCE_BEARING_SUBSCRIPTION_STATUSES
            )
        )
        if should_clear_allowance:
            reconcile = billing.reconcile_billing(conn, user_id)
            if int(reconcile.get("reserved_allowance_cents") or 0) <= 0:
                billing.grant_allowance(
                    conn,
                    user_id,
                    0,
                    f"operator-subscription-clear:{inactive_subscription_id or cached_subscription_id or 'none'}:{inactive_status}",
                    resets_at=None,
                )
                synced = True
                acct = conn.execute(
                    "select allowance_period_start, allowance_resets_at from billing_accounts where user_id = %s",
                    (user_id,),
                ).fetchone()
                period_start = acct[0] if acct else None
                resets_at = acct[1] if acct else None
        return OperatorSubscriptionState(
            user_id=user_id,
            customer_id=inactive_customer_id,
            subscription_id=inactive_subscription_id,
            subscription_status=inactive_status,
            plan_name=None,
            weekly_allowance_cents=0,
            allowance_period_start=period_start.isoformat() if period_start is not None else None,
            allowance_resets_at=resets_at.isoformat() if resets_at is not None else None,
            synced=synced,
        )

    subscription_id = str(active_subscription.get("id") or "").strip() or None
    subscription_status = str(active_subscription.get("status") or "none").strip().lower() or "none"
    if customer_id is None:
        customer_id = _stripe_customer_id(active_subscription.get("customer"))
    weekly_allowance_cents = _operator_subscription_weekly_allowance_cents(active_subscription)
    synced = False

    if (
        customer_id != (str(row[1] or "").strip() or None)
        or subscription_id != cached_subscription_id
        or subscription_status != cached_status
    ):
        _persist_operator_billing_identity(
            conn,
            user_id,
            customer_id=customer_id or "",
            subscription_id=subscription_id or "",
            subscription_status=subscription_status,
        )

    acct = conn.execute(
        "select allowance_included_cents, allowance_period_start, allowance_resets_at "
        "from billing_accounts where user_id = %s",
        (user_id,),
    ).fetchone()
    current_included = int(acct[0] or 0) if acct else 0
    period_start = acct[1] if acct else None
    resets_at = acct[2] if acct else None
    now = datetime.now(timezone.utc)
    should_refresh = (
        weekly_allowance_cents > 0
        and (
            current_included != weekly_allowance_cents
            or not isinstance(resets_at, datetime)
            or resets_at <= now
            or subscription_id != cached_subscription_id
        )
    )
    if should_refresh:
        reconcile = billing.reconcile_billing(conn, user_id)
        if int(reconcile.get("reserved_allowance_cents") or 0) <= 0:
            period_start, resets_at = _weekly_window(now)
            week_key = int(period_start.timestamp() // 604800)
            billing.grant_allowance(
                conn,
                user_id,
                weekly_allowance_cents,
                f"operator-subscription:{subscription_id or 'none'}:{weekly_allowance_cents}:{week_key}",
                period_start=period_start,
                resets_at=resets_at,
            )
            synced = True

    return OperatorSubscriptionState(
        user_id=user_id,
        customer_id=customer_id,
        subscription_id=subscription_id,
        subscription_status=subscription_status,
        plan_name=_operator_subscription_plan_name(active_subscription),
        weekly_allowance_cents=weekly_allowance_cents,
        allowance_period_start=period_start.isoformat() if isinstance(period_start, datetime) else None,
        allowance_resets_at=resets_at.isoformat() if isinstance(resets_at, datetime) else None,
        synced=synced,
    )


def ensure_operator_billing_customer(
    conn,
    user_id: str,
) -> dict[str, Any]:
    row = _read_operator_billing_row(conn, user_id, for_update=False)
    email = str(row[0] or "").strip()
    if not email:
        raise ValueError("operator_email_unavailable")

    cached_customer_id = str(row[1] or "").strip()
    existing = None
    if cached_customer_id:
        try:
            candidate = stripe_util.stripe_request(
                f"customers/{cached_customer_id}",
                {},
                method="GET",
            )
        except stripe_util.StripeError:
            candidate = None
        if isinstance(candidate, dict) and not candidate.get("deleted"):
            existing = candidate
    if existing is None:
        existing = _operator_billing_customer_search(user_id)
    if existing:
        customer_id = str(existing.get("id") or "").strip()
        if customer_id:
            _persist_operator_billing_identity(conn, user_id, customer_id=customer_id)
            try:
                stripe_util.stripe_request(
                    f"customers/{customer_id}",
                    {
                        "email": email,
                        "metadata[takyon_user_id]": user_id,
                        "metadata[purpose]": "operator_billing",
                    },
                )
            except stripe_util.StripeError:
                # Portal/checkout should still work if metadata backfill fails.
                pass
        return existing

    created = stripe_util.stripe_request(
        "customers",
        {
            "email": email,
            "metadata[takyon_user_id]": user_id,
            "metadata[purpose]": "operator_billing",
        },
    )
    customer_id = str(created.get("id") or "").strip()
    if not customer_id:
        raise stripe_util.StripeError("Stripe customer creation returned no customer id")
    _persist_operator_billing_identity(conn, user_id, customer_id=customer_id)
    return created


def create_operator_billing_portal_session(
    conn,
    user_id: str,
    *,
    return_url: str,
) -> dict[str, Any]:
    if not str(return_url or "").strip():
        raise ValueError("return_url is required")
    customer = ensure_operator_billing_customer(conn, user_id)
    customer_id = str(customer.get("id") or "").strip()
    if not customer_id:
        raise stripe_util.StripeError("Stripe customer unavailable for billing portal")
    return stripe_util.stripe_request(
        "billing_portal/sessions",
        {
            "customer": customer_id,
            "return_url": return_url,
        },
    )


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


def _resolve_operator_user_id_from_customer(conn, customer_id: str) -> str | None:
    raw = str(customer_id or "").strip()
    if not raw:
        return None
    row = conn.execute(
        "select id from users where operator_billing_customer_id = %s",
        (raw,),
    ).fetchone()
    if row is None or row[0] is None:
        return None
    return str(row[0]).strip() or None


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
    row = _read_operator_payout_row(conn, user_id, for_update=False)
    account_id = None if row[0] is None else str(row[0])
    cached_status = str(row[1] or "none")
    payout_currency = str(row[2] or "usd").lower()
    email = str(row[3] or "").strip() or None

    if not account_id:
        with conn.transaction():
            row = _read_operator_payout_row(conn, user_id, for_update=True)
            account_id = None if row[0] is None else str(row[0])
            cached_status = str(row[1] or "none")
            payout_currency = str(row[2] or "usd").lower()
            email = str(row[3] or "").strip() or None
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

    if account_id and cached_status == "active":
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
    unset, empty, non-integer, or non-positive. Control-plane config may come from
    the live process env or `TAKYON_HOME/.env`, so read both."""
    raw = _env_value(name)
    if not raw or not raw.strip():
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return value if value > 0 else default


def _env_value(name: str) -> str:
    """Read non-secret config from process env first, then `TAKYON_HOME/.env`.

    The dashboard already resolves config from both places, and the creative-credit
    checkout rail is operator-facing dashboard functionality. Falling back to
    `load_env()` keeps env-backed dashboard config truthful even when the current
    Python process was not launched with every non-secret knob exported.
    """
    value = os.environ.get(name)
    if value is not None:
        return value.strip()
    try:
        from takyon_cli.config import load_env

        return str(load_env().get(name) or "").strip()
    except Exception:
        return ""


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
    raw = _env_value("TAKYON_CREATIVE_CREDIT_PACKS_JSON")
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


def creative_credit_checkout_config() -> dict[str, Any]:
    """Return the current pricing/minimum metadata for custom creative-credit checkout."""
    return {
        "supports_custom_credits": True,
        "price_cents_per_credit": _creative_credit_price_cents(),
        "minimum_checkout_credits": _creative_credit_min_checkout_credits(),
        "minimum_checkout_amount_cents": _creative_credit_min_checkout_amount_cents(),
    }


def _creative_credit_price_cents() -> int:
    """Price one creative credit in cents. Defaults to 1 cent per credit."""
    return _positive_int_env("TAKYON_CREATIVE_CREDIT_PRICE_CENTS", 1)


def _creative_credit_min_checkout_amount_cents() -> int:
    """Minimum USD checkout amount for Stripe-hosted creative-credit purchases.

    Stripe's current USD minimum charge is $0.50, so the default is 50 cents.
    This stays env-overridable in case the account/currency policy changes.
    """
    return _positive_int_env("TAKYON_CREATIVE_CREDIT_MIN_CHARGE_CENTS", 50)


def _creative_credit_min_checkout_credits() -> int:
    unit_price = max(1, _creative_credit_price_cents())
    minimum_amount = max(1, _creative_credit_min_checkout_amount_cents())
    return (minimum_amount + unit_price - 1) // unit_price


def create_creative_credit_checkout_session(
    user_id: str,
    slug: str,
    *,
    credits: int | None = None,
    pack_id: str | None = None,
    success_url: str,
    cancel_url: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Create a Stripe Checkout session for business creative-credit topups.

    The canonical path is direct credit topup (`credits`) at 1 cent per credit by
    default. `pack_id` remains as a compatibility fallback for older callers.
    """
    charge: dict[str, Any]
    if credits is not None:
        credit_count = int(credits)
        if credit_count <= 0:
            raise ValueError("credits must be > 0")
        minimum_credits = _creative_credit_min_checkout_credits()
        minimum_amount_cents = _creative_credit_min_checkout_amount_cents()
        if credit_count < minimum_credits:
            raise ValueError(
                "minimum creative credit purchase is "
                f"{minimum_credits} credits (${minimum_amount_cents / 100:.2f})"
            )
        unit_price = _creative_credit_price_cents()
        amount_cents = credit_count * unit_price
        charge = {
            "credits": credit_count,
            "amount_cents": amount_cents,
            "currency": "usd",
            "price_cents_per_credit": unit_price,
            "purpose": "creative_credit_topup",
        }
    else:
        pack = _creative_credit_pack(str(pack_id or ""))
        if pack is None:
            raise LookupError(f"unknown_credit_pack:{pack_id}")
        charge = {
            "pack_id": pack["id"],
            "credits": int(pack["credits"]),
            "amount_cents": int(pack["amount_cents"]),
            "currency": str(pack["currency"] or "usd"),
            "purpose": "creative_credit_pack",
        }
    params = {
        "mode": "payment",
        "client_reference_id": slug,
        "success_url": success_url,
        "cancel_url": cancel_url,
        "line_items[0][quantity]": 1,
        "line_items[0][price_data][currency]": charge["currency"],
        "line_items[0][price_data][unit_amount]": charge["amount_cents"],
        "line_items[0][price_data][product_data][name]": (
            f"Takyon creative credits ({charge['credits']} credits)"
        ),
        "metadata[purpose]": charge["purpose"],
        "metadata[user_id]": user_id,
        "metadata[business_slug]": slug,
        "metadata[credits]": charge["credits"],
        "payment_intent_data[metadata][purpose]": charge["purpose"],
        "payment_intent_data[metadata][user_id]": user_id,
        "payment_intent_data[metadata][business_slug]": slug,
        "payment_intent_data[metadata][credits]": charge["credits"],
    }
    if charge.get("pack_id"):
        params["metadata[pack_id]"] = charge["pack_id"]
        params["payment_intent_data[metadata][pack_id]"] = charge["pack_id"]
    if charge.get("price_cents_per_credit"):
        params["metadata[price_cents_per_credit]"] = charge["price_cents_per_credit"]
        params["payment_intent_data[metadata][price_cents_per_credit]"] = charge[
            "price_cents_per_credit"
        ]
    session = stripe_util.stripe_request("checkout/sessions", params)
    return session, charge


def reconcile_creative_credit_checkout_session(
    conn,
    *,
    session_id: str,
    expected_business_slug: str | None = None,
) -> dict[str, Any]:
    """Settle one paid creative-credit checkout exactly once through Safebox authority."""
    return safebox.reconcile_creative_credit_checkout(
        conn,
        session_id=session_id,
        expected_business_slug=expected_business_slug,
    )


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
        # Identity projection only. Allowance (opaque "included usage") joins here once
        # the billing/custody ledgers exist; we do NOT fabricate it in the meantime.
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
        balances = safebox.get_business_credit_balances(conn, slug)
        return {
            "business_slug": slug,
            "balance_credits": balances.balance_credits,
            "reserved_credits": balances.reserved_credits,
            **creative_credit_checkout_config(),
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
        return {
            "business_slug": slug,
            "packs": configured_creative_credit_packs(),
            **creative_credit_checkout_config(),
        }

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
            return safebox.create_creative_credit_checkout(
                principal.user_id,
                slug,
                credits=body.credits,
                pack_id=body.pack_id,
                success_url=body.success_url,
                cancel_url=body.cancel_url,
            )
        except LookupError as exc:
            raise HTTPException(status_code=404, detail="unknown_credit_pack") from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except stripe_util.StripeError as exc:
            msg = str(exc)
            if "STRIPE_SECRET_KEY" in msg or "creative_credit_checkout_unconfigured" in msg:
                raise HTTPException(
                    status_code=503, detail="creative_credit_checkout_unconfigured"
                ) from exc
            raise HTTPException(status_code=502, detail=f"stripe_error: {msg}") from exc

    @router.post("/businesses/{slug}/creative-credits/reconcile")
    def reconcile_creative_credit_checkout(
        slug: str,
        body: CreativeCreditReconcileRequest,
        principal: ResolvedPrincipal = Depends(_rate_limited_principal),
        conn=Depends(get_control_conn),
    ) -> dict[str, Any]:
        if slug not in principal.business_slugs:
            raise HTTPException(status_code=404, detail="not_found")
        row = conn.execute(
            "select slug from businesses where slug = %s",
            (slug,),
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="not_found")
        try:
            return reconcile_creative_credit_checkout_session(
                conn,
                session_id=body.session_id,
                expected_business_slug=slug,
            )
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except RuntimeError as exc:
            if str(exc) == "creative_credit_checkout_unpaid":
                raise HTTPException(status_code=409, detail=str(exc)) from exc
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except stripe_util.StripeError as exc:
            msg = str(exc)
            if "STRIPE_SECRET_KEY" in msg:
                raise HTTPException(
                    status_code=503, detail="creative_credit_reconcile_unconfigured"
                ) from exc
            raise HTTPException(status_code=502, detail=f"stripe_error: {msg}") from exc

    @router.get("/billing/plans")
    def list_operator_plans(
        principal: ResolvedPrincipal = Depends(_rate_limited_principal),
    ) -> dict[str, Any]:
        """The operator subscription tier menu (multi-tier). Each entry carries a stable
        `id`, display fields, and the recurring allowance the tier confers — the price_id is
        deliberately NOT exposed to the caller (checkout selects by `id` server-side, so a
        caller can never substitute an arbitrary Stripe price)."""
        plans = [
            {
                "id": plan["id"],
                "name": plan["name"],
                "description": plan["description"],
                "tagline": plan["tagline"],
                "weekly_allowance_cents": int(plan["weekly_allowance_cents"] or 0),
                "amount_cents": int(plan["amount_cents"] or 0),
                "currency": plan["currency"],
                "interval": plan["interval"],
                "featured": bool(plan["featured"]),
                "features": list(plan["features"]),
            }
            for plan in configured_operator_plans()
        ]
        return {"plans": plans}

    @router.post("/billing/subscription/checkout")
    def create_operator_subscription_checkout(
        body: OperatorSubscriptionCheckoutRequest,
        principal: ResolvedPrincipal = Depends(_rate_limited_principal),
        conn=Depends(get_control_conn),
    ) -> dict[str, Any]:
        """Start a Stripe subscription checkout for the CALLER on a chosen operator tier.
        The tier is resolved server-side from the configured catalog (`plan_id`); the price
        the operator pays is the Stripe price on that tier, never a caller-supplied amount.
        Requires STRIPE_SECRET_KEY; absent ⇒ 503 (never a faked URL)."""
        try:
            customer = ensure_operator_billing_customer(conn, principal.user_id)
            session, plan = create_operator_subscription_checkout_session(
                principal.user_id,
                plan_id=body.plan_id,
                success_url=body.success_url,
                cancel_url=body.cancel_url,
                customer_id=str(customer.get("id") or "").strip() or None,
            )
        except LookupError as exc:
            raise HTTPException(status_code=404, detail="unknown_operator_plan") from exc
        except ValueError as exc:
            if "operator_email_unavailable" in str(exc):
                raise HTTPException(status_code=409, detail="operator_email_unavailable") from exc
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except stripe_util.StripeError as exc:
            msg = str(exc)
            if "STRIPE_SECRET_KEY" in msg:
                raise HTTPException(
                    status_code=503, detail="operator_subscription_unconfigured"
                ) from exc
            raise HTTPException(status_code=502, detail=f"stripe_error: {msg}") from exc
        return {
            "checkout_url": session.get("url"),
            "session_id": session.get("id"),
            "plan_id": plan["id"],
            "plan_name": plan["name"],
        }

    @router.post("/billing/webhook")
    async def billing_webhook(
        request: Request,
        conn=Depends(get_control_conn),
    ) -> dict[str, Any]:
        """Dedicated control-plane (flow-A) webhook — SEPARATE from the product (flow B)
        webhook so it carries its OWN signing secret. Verifies the raw body with
        STRIPE_BILLING_WEBHOOK_SECRET; if that secret is absent the event is NOT trusted and
        we return 503 so Stripe retries — nothing is ever faked around a missing credential.
        It settles operator subscription allowance (`operator_subscription` +
        `customer.subscription.*`) and business creative-credit packs, each idempotent on the
        Stripe event id."""
        raw = (await request.body()).decode("utf-8")
        signature = request.headers.get("stripe-signature", "")
        try:
            event = safebox.verify_stripe_billing_webhook(raw, signature)
        except safebox.StripeBillingWebhookUnconfigured:
            raise HTTPException(status_code=503, detail="billing_webhook_unconfigured")
        except safebox.StripeBillingWebhookInvalidSignature:
            raise HTTPException(status_code=400, detail="invalid_signature")
        event_id = str(event.get("id") or "")
        event_type = str(event.get("type") or "")
        obj = (event.get("data") or {}).get("object") or {}
        if event_type in {
            "customer.subscription.created",
            "customer.subscription.updated",
            "customer.subscription.deleted",
        }:
            customer_id = _stripe_customer_id(obj.get("customer"))
            user_id = _resolve_operator_user_id_from_customer(conn, customer_id or "")
            if not user_id:
                return {"ok": True, "ignored": "unknown_operator_customer"}
            state = sync_operator_subscription_allowance(
                conn,
                user_id,
                refresh_live=False,
                subscription=obj if isinstance(obj, dict) else None,
            )
            return {
                "ok": True,
                "user_id": user_id,
                "customer_id": customer_id,
                "subscription_id": state.subscription_id,
                "subscription_status": state.subscription_status,
                "weekly_allowance_cents": state.weekly_allowance_cents,
                "allowance_resets_at": state.allowance_resets_at,
                "event_id": event_id,
            }
        if event_type != "checkout.session.completed":
            return {"ok": True, "ignored": event_type or "unknown_event"}
        session = obj if isinstance(obj, dict) else {}
        metadata = session.get("metadata") or {}
        if session.get("payment_status") not in ("paid", "no_payment_required"):
            return {"ok": True, "ignored": "unpaid"}
        purpose = str(metadata.get("purpose") or "")
        if purpose == "operator_subscription":
            # Subscription-mode checkout completed: settle the chosen tier's allowance for
            # the operator. The customer.subscription.created event ALSO fires and syncs;
            # this branch makes the checkout completion alone sufficient and idempotent —
            # grant_allowance is keyed on the week+subscription, so the two paths converge.
            checkout_customer_id = _stripe_customer_id(session.get("customer"))
            user_id = str(
                session.get("client_reference_id") or metadata.get("user_id") or ""
            ).strip()
            if not user_id and checkout_customer_id:
                user_id = _resolve_operator_user_id_from_customer(conn, checkout_customer_id) or ""
            if not user_id:
                return {"ok": True, "ignored": "unknown_operator_customer"}
            # Persist the checkout's Stripe customer so the refresh below can find the new
            # subscription on Stripe (the operator may not have had a cached customer yet).
            if checkout_customer_id:
                billing.open_billing_account(conn, user_id)
                existing_customer = str(
                    _read_operator_billing_row(conn, user_id)[1] or ""
                ).strip()
                if existing_customer != checkout_customer_id:
                    _persist_operator_billing_identity(
                        conn, user_id, customer_id=checkout_customer_id
                    )
            state = sync_operator_subscription_allowance(conn, user_id, refresh_live=True)
            return {
                "ok": True,
                "user_id": user_id,
                "subscription_id": state.subscription_id,
                "subscription_status": state.subscription_status,
                "plan_name": state.plan_name,
                "weekly_allowance_cents": state.weekly_allowance_cents,
                "allowance_resets_at": state.allowance_resets_at,
                "event_id": event_id,
            }
        if purpose in {"creative_credit_pack", "creative_credit_topup"}:
            business_slug = str(
                metadata.get("business_slug") or session.get("client_reference_id") or ""
            ).strip()
            pack_id = str(metadata.get("pack_id") or "").strip()
            try:
                credits = int(metadata.get("credits") or 0)
            except (TypeError, ValueError):
                credits = 0
            try:
                price_cents_per_credit = int(metadata.get("price_cents_per_credit") or 0)
            except (TypeError, ValueError):
                price_cents_per_credit = 0
            if not business_slug or credits <= 0 or not event_id:
                return {"ok": True, "ignored": "incomplete_session"}
            grant_metadata = {
                "purpose": purpose,
                "user_id": metadata.get("user_id"),
                "stripe_checkout_session_id": session.get("id"),
                "amount_cents": int(session.get("amount_total") or 0),
                "price_cents_per_credit": price_cents_per_credit,
            }
            if pack_id:
                grant_metadata["pack_id"] = pack_id
            balances = safebox.grant_credits(
                conn,
                business_slug,
                credits,
                idempotency_key=event_id,
                metadata=grant_metadata,
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
        return {"ok": True, "ignored": purpose or "unhandled_purpose"}

    return router
