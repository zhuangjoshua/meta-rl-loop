"""Product checkout + Stripe webhook + revenue ledger, with the net-new owner->custody
accrual — Phase 5 (increment d) of mediationplan.md.

This is the money-IN side of a product: a business's customers (sub-users) pay on the shared
platform Stripe. Three things happen on a paid checkout, and the third is what the SQLite trunk
never did:

  1. RECORD the settled checkout session (`app_checkout_sessions`, UNIQUE on the Stripe session
     id) and a REVENUE event (`app_revenue_events`, UNIQUE per business+event+object).
  2. GRANT the paying sub-user their entitlement (delegated to `app_entitlements.grant_entitlement`
     with Stripe evidence — the canonical entitlements home; it auto-provisions the sub-user).
  3. ACCRUE the payment to the business OWNER's custody ledger (flow B in 0002). Resolve
     business_slug -> businesses.owner_user_id (the linkage 0001 added, SQLite lacks) and call the
     EXISTING `custody.accrue`, which takes the platform app fee (STRIPE_CONNECT_APPLICATION_FEE_BPS)
     and accrues the net. The SQLite product path performed ZERO owner accrual; this closes the
     Phase 5 acceptance "sub-user payment shows in owner custody". Accrual does not need Connect —
     the owed balance is a ledger fact from day one.

Webhook idempotency / concurrency (robustness #1, an improvement over SQLite): the SQLite handler
INSERT-OR-IGNOREs the dedup row but then processes UNCONDITIONALLY, and its entitlement insert is a
plain INSERT — so a redelivered checkout.session.completed would DOUBLE-grant. `record_webhook_and_
process` instead takes `... for update` on the `webhook_events` row and skips when processed_at is
already set, so each event is processed to completion at most once even under concurrent
redelivery (mirrors billing.py's single-row-lock invariant). The whole dispatch runs in ONE
transaction; a mid-failure rolls back the dedup row too, so the event stays cleanly retryable.

House style (matches billing.py / custody.py / app_identity.py / app_entitlements.py): pure leaf,
takes a psycopg connection, imports no psycopg, opens its own `conn.transaction()` per mutating op,
raises typed errors on broken preconditions. An unknown business fails loud through the FK to
businesses(slug). Stripe NETWORK + SIGNATURE verification live in `stripe_util`; this leaf takes an
already-verified event dict (separation of concerns — the endpoint composes the two).

Postgres port of the SQLite trunk's checkout/webhook/revenue (core.py:6844-6988); the SQLite
product path is the predecessor, retired in Phase 8.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable

from plugins.takyon import app_entitlements, custody, safebox

_SUBSCRIPTION_EVENT_TYPES = (
    "customer.subscription.created",
    "customer.subscription.updated",
    "customer.subscription.deleted",
)
_CHARGE_REVERSAL_EVENT_TYPES = (
    "charge.refunded",
    "charge.dispute.created",
)


class AppPaymentError(Exception):
    """Base for checkout/webhook/revenue errors."""


class InvalidWebhookEvent(AppPaymentError):
    """The event payload is missing a usable provider event id."""


class CheckoutIntentNotFound(AppPaymentError):
    """No checkout intent matches the given handle."""


class BusinessOwnerMissing(AppPaymentError):
    """A business has no resolvable owner_user_id — an integrity violation (0001 makes it
    NOT NULL), so a paid revenue event could not be accrued to anyone."""


class CancelableSubscriptionNotFound(AppPaymentError):
    """The sub-user has no Stripe-backed subscription that can still be cancelled."""


@dataclass(frozen=True)
class CheckoutIntent:
    id: str
    business_slug: str
    app_user_id: str | None
    plan_key: str
    status: str
    client_reference_id: str
    stripe_checkout_session_id: str | None
    checkout_url: str | None
    customer_email: str | None
    metadata: dict
    created_at: object
    updated_at: object
    completed_at: object


@dataclass(frozen=True)
class RevenueEvent:
    id: str
    business_slug: str
    provider_event_id: str | None
    stripe_object_type: str | None
    stripe_object_id: str | None
    stripe_checkout_session_id: str | None
    stripe_customer_id: str | None
    revenue_type: str
    status: str
    currency: str
    amount_paid_cents: int
    customer_email: str | None
    occurred_at: object
    metadata: dict


_INTENT_COLUMNS = (
    "id, business_slug, app_user_id, plan_key, status, client_reference_id, "
    "stripe_checkout_session_id, checkout_url, customer_email, metadata, "
    "created_at, updated_at, completed_at"
)
_REVENUE_COLUMNS = (
    "id, business_slug, provider_event_id, stripe_object_type, stripe_object_id, "
    "stripe_checkout_session_id, stripe_customer_id, revenue_type, status, currency, "
    "amount_paid_cents, customer_email, occurred_at, metadata"
)


def _json_dumps(value) -> str:
    return json.dumps(value or {}, ensure_ascii=False, sort_keys=True)


def _intent_from_row(row) -> CheckoutIntent:
    return CheckoutIntent(
        id=str(row[0]),
        business_slug=row[1],
        app_user_id=None if row[2] is None else str(row[2]),
        plan_key=row[3],
        status=row[4],
        client_reference_id=row[5],
        stripe_checkout_session_id=row[6],
        checkout_url=row[7],
        customer_email=row[8],
        metadata=row[9] if isinstance(row[9], dict) else {},
        created_at=row[10],
        updated_at=row[11],
        completed_at=row[12],
    )


def _revenue_from_row(row) -> RevenueEvent:
    return RevenueEvent(
        id=str(row[0]),
        business_slug=row[1],
        provider_event_id=row[2],
        stripe_object_type=row[3],
        stripe_object_id=row[4],
        stripe_checkout_session_id=row[5],
        stripe_customer_id=row[6],
        revenue_type=row[7],
        status=row[8],
        currency=row[9],
        amount_paid_cents=int(row[10]),
        customer_email=row[11],
        occurred_at=row[12],
        metadata=row[13] if isinstance(row[13], dict) else {},
    )


def _stripe_object_id(value) -> str | None:
    """Stripe expands some refs to an object and leaves others as a bare id string."""
    if isinstance(value, str):
        return value
    if isinstance(value, dict) and isinstance(value.get("id"), str):
        return value["id"]
    return None


def _subscription_entitlement_status(stripe_status: str) -> str:
    """Map a Stripe subscription.status to an entitlement status. Verbatim from
    core.py:6026."""
    if stripe_status in {"active", "trialing"}:
        return "active"
    if stripe_status in {"canceled", "cancelled"}:
        return "cancelled"
    return "past_due"


def _epoch_to_dt(value) -> datetime | None:
    """Stripe timestamps are unix epoch seconds; convert to a tz-aware datetime psycopg can
    adapt to timestamptz. Non-numeric / missing -> None (SQL coalesces to now()/keeps prior)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return datetime.fromtimestamp(int(value), timezone.utc)


def _owner_payout_split(plan, gross_cents: int) -> dict[str, int]:
    included_budget_microusd = 0
    if plan is not None and str(getattr(plan, "billing_interval", "") or "").strip().lower() == "month":
        included_budget_microusd = max(
            0, int(getattr(plan, "included_ai_budget_microusd", 0) or 0)
        )
    platform_fee_cents = (max(0, int(gross_cents or 0)) * custody.app_fee_bps()) // 10000
    max_withhold_cents = max(0, int(gross_cents or 0) - platform_fee_cents)
    prepaid_withheld_cents = min(max_withhold_cents, included_budget_microusd // 10_000)
    owner_net_cents = max(0, int(gross_cents or 0) - platform_fee_cents - prepaid_withheld_cents)
    return {
        "platform_fee_cents": platform_fee_cents,
        "included_ai_budget_microusd": included_budget_microusd,
        "prepaid_withheld_cents": prepaid_withheld_cents,
        "prepaid_withheld_microusd": prepaid_withheld_cents * 10_000,
        "prepaid_dust_microusd": max(
            0, included_budget_microusd - (prepaid_withheld_cents * 10_000)
        ),
        "owner_net_cents": owner_net_cents,
    }


def _insert_revenue_event(
    conn,
    *,
    business_slug: str,
    provider_event_id: str | None,
    stripe_object_type: str,
    stripe_object_id: str,
    stripe_checkout_session_id: str | None = None,
    stripe_customer_id: str | None = None,
    revenue_type: str,
    status: str,
    currency: str,
    amount_paid_cents: int,
    customer_email: str | None = None,
    occurred_at: object = None,
    metadata: dict | None = None,
) -> bool:
    row = conn.execute(
        "select safebox_insert_app_revenue_event("
        "%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb"
        ")",
        (
            business_slug,
            provider_event_id,
            stripe_object_type,
            stripe_object_id,
            stripe_checkout_session_id,
            stripe_customer_id,
            revenue_type,
            status,
            currency,
            int(amount_paid_cents or 0),
            customer_email,
            occurred_at,
            _json_dumps(metadata or {}),
        ),
    ).fetchone()
    return bool(row and row[0])


# --------------------------------------------------------------------------- checkout intents


def create_checkout_intent(
    conn,
    business_slug: str,
    *,
    plan_key: str,
    client_reference_id: str,
    app_user_id: str | None = None,
    customer_email: str | None = None,
    status: str = "created",
    metadata: dict | None = None,
) -> CheckoutIntent:
    """Record a started checkout. Idempotent on `client_reference_id` (the caller's stable
    handle into Stripe): a replay returns the ORIGINAL intent unchanged, so reusing the key can
    never fork a second checkout. Unknown business / sub-user fail loud through their FKs. The
    Stripe Checkout Session is created by the caller (via stripe_util) and linked with
    `attach_checkout_session`."""
    plan = str(plan_key or "")
    ref = str(client_reference_id or "")
    if not plan:
        raise ValueError("plan_key is required")
    if not ref:
        raise ValueError("client_reference_id is required")
    with conn.transaction():
        row = conn.execute(
            "insert into app_checkout_intents "
            "(business_slug, app_user_id, plan_key, status, client_reference_id, "
            " customer_email, metadata) "
            "values (%s, %s, %s, %s, %s, %s, %s::jsonb) "
            "on conflict (client_reference_id) do update set updated_at = now() "
            f"returning {_INTENT_COLUMNS}",
            (
                business_slug,
                app_user_id,
                plan,
                str(status or "created"),
                ref,
                customer_email,
                _json_dumps(metadata),
            ),
        ).fetchone()
    return _intent_from_row(row)


def attach_checkout_session(
    conn,
    *,
    client_reference_id: str | None = None,
    intent_id: str | None = None,
    stripe_checkout_session_id: str,
    checkout_url: str | None = None,
    status: str = "pending",
) -> CheckoutIntent:
    """Link a created intent to the Stripe session the caller just opened (session id + url),
    advancing its status. Mirrors core.py:6833. Identify the intent by id or client_reference_id;
    no match -> CheckoutIntentNotFound."""
    session_id = str(stripe_checkout_session_id or "")
    if not session_id:
        raise ValueError("stripe_checkout_session_id is required")
    if not (intent_id or client_reference_id):
        raise ValueError("attach_checkout_session requires intent_id or client_reference_id")
    with conn.transaction():
        row = conn.execute(
            "update app_checkout_intents set status = %s, "
            "stripe_checkout_session_id = %s, checkout_url = %s, updated_at = now() "
            "where (%s::uuid is null or id = %s::uuid) "
            "and (%s::text is null or client_reference_id = %s) "
            f"returning {_INTENT_COLUMNS}",
            (
                str(status or "pending"),
                session_id,
                checkout_url,
                intent_id,
                intent_id,
                client_reference_id,
                client_reference_id,
            ),
        ).fetchone()
    if row is None:
        raise CheckoutIntentNotFound(intent_id or client_reference_id or "")
    return _intent_from_row(row)


def get_checkout_intent(
    conn,
    business_slug: str,
    *,
    client_reference_id: str | None = None,
    intent_id: str | None = None,
) -> CheckoutIntent | None:
    """Look up a checkout intent within a business by id or client_reference_id, or None. Read."""
    if intent_id is not None:
        row = conn.execute(
            f"select {_INTENT_COLUMNS} from app_checkout_intents "
            "where business_slug = %s and id = %s",
            (business_slug, intent_id),
        ).fetchone()
    elif client_reference_id is not None:
        row = conn.execute(
            f"select {_INTENT_COLUMNS} from app_checkout_intents "
            "where business_slug = %s and client_reference_id = %s",
            (business_slug, client_reference_id),
        ).fetchone()
    else:
        raise ValueError("get_checkout_intent requires intent_id or client_reference_id")
    return None if row is None else _intent_from_row(row)


# --------------------------------------------------------------------------- webhook gate


def record_webhook_and_process(conn, event: dict, *, provider: str = "stripe") -> dict:
    """THE webhook entry (DB side; the endpoint verifies the signature via stripe_util first and
    passes the parsed event). Dedups GLOBALLY on (provider, provider_event_id) and processes each
    event to completion AT MOST ONCE, even under concurrent redelivery, by locking the
    `webhook_events` row `for update` and skipping when processed_at is already set. The whole
    dispatch is one transaction so a mid-failure rolls back the dedup row and stays retryable.

    Returns {provider_event_id, type, deduplicated, processed}. `deduplicated=True` means this
    event was already fully processed and nothing ran this time."""
    if not isinstance(event, dict):
        raise InvalidWebhookEvent("event payload must be an object")
    event_id = str(event.get("id") or "")
    if not event_id:
        raise InvalidWebhookEvent("event id is required for dedup")
    event_type = str(event.get("type") or "")
    data = event.get("data")
    obj = data.get("object") if isinstance(data, dict) else None

    with conn.transaction():
        conn.execute(
            "insert into webhook_events (provider, provider_event_id, payload) "
            "values (%s, %s, %s::jsonb) "
            "on conflict (provider, provider_event_id) do nothing",
            (provider, event_id, _json_dumps(event)),
        )
        locked = conn.execute(
            "select processed_at from webhook_events "
            "where provider = %s and provider_event_id = %s for update",
            (provider, event_id),
        ).fetchone()
        if locked is not None and locked[0] is not None:
            return {
                "provider_event_id": event_id,
                "type": event_type,
                "deduplicated": True,
                "processed": None,
            }
        if event_type == "checkout.session.completed" and isinstance(obj, dict):
            processed = _process_checkout_completed(conn, event, obj)
        elif event_type in _SUBSCRIPTION_EVENT_TYPES and isinstance(obj, dict):
            processed = _process_subscription_event(conn, obj)
        elif event_type == "invoice.paid" and isinstance(obj, dict):
            processed = _process_invoice_paid(conn, event, obj)
        elif event_type == "invoice.payment_failed" and isinstance(obj, dict):
            processed = _process_invoice_payment_failed(conn, event, obj)
        elif event_type in _CHARGE_REVERSAL_EVENT_TYPES and isinstance(obj, dict):
            processed = _process_charge_reversal(conn, event, obj)
        else:
            processed = {"recorded": False, "ignored": event_type}
        conn.execute(
            "update webhook_events set processed_at = now(), error = null "
            "where provider = %s and provider_event_id = %s",
            (provider, event_id),
        )
    return {
        "provider_event_id": event_id,
        "type": event_type,
        "deduplicated": False,
        "processed": processed,
    }


# --------------------------------------------------------------------------- dispatch (in-txn)


def _find_intent_row(conn, event_metadata: dict, session: dict):
    intent_id = event_metadata.get("checkout_intent_id")
    if intent_id:
        row = conn.execute(
            "select id, business_slug, app_user_id, plan_key, customer_email from app_checkout_intents "
            "where id = %s",
            (intent_id,),
        ).fetchone()
        if row is not None:
            return row
    client_ref = session.get("client_reference_id")
    if client_ref:
        return conn.execute(
            "select id, business_slug, app_user_id, plan_key, customer_email from app_checkout_intents "
            "where client_reference_id = %s",
            (client_ref,),
        ).fetchone()
    return None


def reconcile_checkout_session(
    conn,
    session: dict,
    *,
    provider_event_id: str | None = None,
    event_created: int | float | None = None,
) -> dict:
    """Reconcile a settled Stripe Checkout Session by the session object itself.

    This is the cross-path idempotent entry used both by webhooks and by recovery flows that can
    prove a checkout reached Stripe but missed the webhook rail. The unique
    `app_checkout_sessions.stripe_checkout_session_id` row is the authoritative gate: the first
    transaction to insert that row owns the downstream side effects (entitlement grant, revenue
    ledger, owner custody accrual). Any later replay or alternate path updates the stored session
    facts but skips those side effects, so a late webhook cannot double-record a recovered payment.
    """
    if not isinstance(session, dict):
        raise ValueError("session payload must be an object")
    metadata = session.get("metadata") if isinstance(session.get("metadata"), dict) else {}
    intent = _find_intent_row(conn, metadata, session)
    if intent is None:
        return {"recorded": False, "reason": "missing_checkout_intent"}
    intent_id, business, intent_app_user_id, plan_key, intent_email = (
        str(intent[0]),
        intent[1],
        None if intent[2] is None else str(intent[2]),
        intent[3],
        intent[4],
    )
    details = session.get("customer_details") if isinstance(session.get("customer_details"), dict) else {}
    customer_email = details.get("email") or session.get("customer_email") or intent_email
    customer_id = _stripe_object_id(session.get("customer"))
    subscription_id = _stripe_object_id(session.get("subscription"))
    payment_intent_id = _stripe_object_id(session.get("payment_intent"))
    invoice_id = _stripe_object_id(session.get("invoice"))
    session_id = str(session.get("id") or "")
    if not session_id:
        return {"recorded": False, "reason": "missing_session_id"}
    occurred = _epoch_to_dt(event_created)
    payment_status = session.get("payment_status")
    currency = session.get("currency")
    amount_total = int(session.get("amount_total") or 0)
    plan_policy = app_entitlements.get_plan_policy(conn, business, plan_key)
    session_economics_version = str(metadata.get("economics_version") or "").strip()
    if session_economics_version:
        if plan_policy is None:
            return {"recorded": False, "reason": "checkout_plan_missing"}
        current_economics_version = app_entitlements.plan_economics_version(
            business_slug=plan_policy.business_slug,
            plan_key=plan_policy.plan_key,
            tier=plan_policy.tier,
            price_cents=plan_policy.price_cents,
            currency=plan_policy.currency,
            billing_interval=plan_policy.billing_interval,
            included_ai_budget_microusd=plan_policy.included_ai_budget_microusd,
            included_action_quota=plan_policy.included_action_quota,
        )
        if (
            session_economics_version != current_economics_version
            or str(currency or "").lower() != str(plan_policy.currency or "").lower()
            or amount_total != int(plan_policy.price_cents)
            or str(session.get("mode") or "") != "subscription"
        ):
            return {"recorded": False, "reason": "checkout_economics_mismatch"}

    inserted = conn.execute(
        "insert into app_checkout_sessions "
        "(business_slug, checkout_intent_id, plan_key, stripe_checkout_session_id, "
        " stripe_customer_id, stripe_payment_intent_id, stripe_subscription_id, "
        " stripe_invoice_id, mode, payment_status, status, currency, amount_subtotal_cents, "
        " amount_total_cents, client_reference_id, customer_email, raw_event_id, metadata, "
        " completed_at) "
        "values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, "
        " coalesce(%s, now())) "
        "on conflict (stripe_checkout_session_id) do nothing "
        "returning id",
        (
            business,
            intent_id,
            plan_key,
            session_id,
            customer_id,
            payment_intent_id,
            subscription_id,
            invoice_id,
            session.get("mode"),
            payment_status,
            session.get("status"),
            currency,
            session.get("amount_subtotal"),
            session.get("amount_total"),
            session.get("client_reference_id"),
            customer_email,
            provider_event_id,
            _json_dumps(metadata),
            occurred,
        ),
    ).fetchone()
    if inserted is None:
        conn.execute(
            "update app_checkout_sessions set "
            " checkout_intent_id = coalesce(app_checkout_sessions.checkout_intent_id, %s), "
            " plan_key = coalesce(app_checkout_sessions.plan_key, %s), "
            " stripe_customer_id = coalesce(%s, app_checkout_sessions.stripe_customer_id), "
            " stripe_payment_intent_id = coalesce(%s, app_checkout_sessions.stripe_payment_intent_id), "
            " stripe_subscription_id = coalesce(%s, app_checkout_sessions.stripe_subscription_id), "
            " stripe_invoice_id = coalesce(%s, app_checkout_sessions.stripe_invoice_id), "
            " mode = coalesce(%s, app_checkout_sessions.mode), "
            " payment_status = coalesce(%s, app_checkout_sessions.payment_status), "
            " status = coalesce(%s, app_checkout_sessions.status), "
            " currency = coalesce(%s, app_checkout_sessions.currency), "
            " amount_subtotal_cents = coalesce(%s, app_checkout_sessions.amount_subtotal_cents), "
            " amount_total_cents = coalesce(%s, app_checkout_sessions.amount_total_cents), "
            " client_reference_id = coalesce(%s, app_checkout_sessions.client_reference_id), "
            " customer_email = coalesce(%s, app_checkout_sessions.customer_email), "
            " raw_event_id = coalesce(app_checkout_sessions.raw_event_id, %s), "
            " metadata = app_checkout_sessions.metadata || %s::jsonb, "
            " completed_at = coalesce(%s, app_checkout_sessions.completed_at), "
            " updated_at = now() "
            "where stripe_checkout_session_id = %s",
            (
                intent_id,
                plan_key,
                customer_id,
                payment_intent_id,
                subscription_id,
                invoice_id,
                session.get("mode"),
                payment_status,
                session.get("status"),
                currency,
                session.get("amount_subtotal"),
                session.get("amount_total"),
                session.get("client_reference_id"),
                customer_email,
                provider_event_id,
                _json_dumps(metadata),
                occurred,
                session_id,
            ),
        )
    conn.execute(
        "update app_checkout_intents set status = 'completed', "
        "completed_at = coalesce(%s, now()), updated_at = now() where id = %s",
        (occurred, intent_id),
    )
    if inserted is None:
        return {
            "recorded": True,
            "business_slug": business,
            "app_user_id": intent_app_user_id,
            "plan_key": plan_key,
            "revenue_recorded": False,
            "accrued_to_owner": False,
            "owner_user_id": None,
            "owner_owed_balance_cents": None,
            "already_recorded": True,
        }

    app_user_id = None
    if (intent_app_user_id or customer_email) and (subscription_id or payment_status == "paid"):
        entitlement_metadata = {}
        if provider_event_id:
            entitlement_metadata["raw_event_id"] = provider_event_id
        if intent_app_user_id and customer_email and intent_email and customer_email.lower() != str(intent_email).lower():
            entitlement_metadata["checkout_email_mismatch"] = {
                "intent_customer_email": intent_email,
                "stripe_customer_email": customer_email,
            }
        entitlement, _tier = app_entitlements.grant_entitlement(
            conn,
            business,
            app_user_id=intent_app_user_id,
            email=None if intent_app_user_id else customer_email,
            tier="paid",
            status="active",
            source="stripe",
            stripe_customer_id=customer_id,
            stripe_subscription_id=subscription_id,
            stripe_checkout_session_id=session_id,
            plan_key=plan_key,
            metadata=entitlement_metadata,
        )
        app_user_id = entitlement.app_user_id

    payout_split = _owner_payout_split(plan_policy, amount_total)
    revenue_metadata = {
        **metadata,
        "pricing_split": {
            "authority": "takyon",
            "gross_cents": int(amount_total or 0),
            "platform_fee_cents": payout_split["platform_fee_cents"],
            "prepaid_withheld_cents": payout_split["prepaid_withheld_cents"],
            "prepaid_withheld_microusd": payout_split["prepaid_withheld_microusd"],
            "prepaid_dust_microusd": payout_split["prepaid_dust_microusd"],
            "owner_net_cents": payout_split["owner_net_cents"],
            "included_ai_budget_microusd": payout_split["included_ai_budget_microusd"],
            "x_plus_y_plus_z_cents": int(amount_total or 0),
        },
    }

    revenue_recorded = False
    accrued_to_owner = False
    owner_user_id = None
    owed_balance_cents = None
    if currency and payment_status == "paid":
        revenue_recorded = _insert_revenue_event(
            conn,
            business_slug=business,
            provider_event_id=provider_event_id,
            stripe_object_type="checkout.session",
            stripe_object_id=session_id,
            stripe_checkout_session_id=session_id,
            stripe_customer_id=customer_id,
            revenue_type="checkout",
            status=payment_status or "paid",
            currency=currency or "usd",
            amount_paid_cents=amount_total,
            customer_email=customer_email,
            occurred_at=occurred,
            metadata=revenue_metadata,
        )
        if amount_total > 0:
            owner_user_id = _resolve_owner(conn, business)
            safebox.open_custody_account(conn, owner_user_id)
            custody_key = (
                f"app_revenue:{business}:{provider_event_id}:{session_id}"
                if provider_event_id
                else f"app_revenue_session:{business}:{session_id}"
            )
            owed_balance_cents = safebox.accrue_custody(
                conn,
                owner_user_id,
                business,
                amount_total,
                custody_key,
                stripe_ref=session_id,
                withheld_cents=payout_split["prepaid_withheld_cents"],
                metadata=revenue_metadata.get("pricing_split"),
            )
            accrued_to_owner = True

    return {
        "recorded": True,
        "business_slug": business,
        "app_user_id": app_user_id,
        "plan_key": plan_key,
        "revenue_recorded": revenue_recorded,
        "accrued_to_owner": accrued_to_owner,
        "owner_user_id": owner_user_id,
        "owner_owed_balance_cents": owed_balance_cents,
        "platform_fee_cents": payout_split["platform_fee_cents"],
        "prepaid_withheld_cents": payout_split["prepaid_withheld_cents"],
        "prepaid_withheld_microusd": payout_split["prepaid_withheld_microusd"],
        "owner_net_cents": payout_split["owner_net_cents"],
        "included_ai_budget_microusd": payout_split["included_ai_budget_microusd"],
        "already_recorded": False,
    }


def reconcile_subscription(conn, subscription: dict) -> dict:
    """Apply a Stripe subscription object to the canonical entitlement rows."""
    if not isinstance(subscription, dict):
        raise ValueError("subscription payload must be an object")
    return _process_subscription_event(conn, subscription)


def _cancelable_subscription_entitlement(conn, business_slug: str, app_user_id: str):
    for entitlement in app_entitlements.list_entitlements(
        conn,
        business_slug,
        app_user_id=app_user_id,
    ):
        subscription_id = str(entitlement.stripe_subscription_id or "").strip()
        status = str(entitlement.status or "").strip().lower()
        if not subscription_id:
            continue
        if status in {"cancelled", "canceled"}:
            continue
        return entitlement
    return None


def cancel_subscription(
    conn,
    business_slug: str,
    *,
    app_user_id: str,
    subscription_updater: Callable[[str, bool], dict[str, Any]],
    cancel_at_period_end: bool = True,
) -> dict[str, Any]:
    """Cancel one Stripe-backed product subscription for a sub-user.

    The provider mutation is injected via ``subscription_updater`` so this leaf stays testable and
    keeps the provider/network policy at the layer above. Durable truth still lands here: the
    Stripe response is reconciled back onto the canonical entitlement rows through the existing
    subscription lifecycle path.
    """
    entitlement = _cancelable_subscription_entitlement(conn, business_slug, app_user_id)
    if entitlement is None:
        raise CancelableSubscriptionNotFound("no cancelable Stripe subscription found")
    existing_cancel = bool((entitlement.metadata or {}).get("cancel_at_period_end"))
    if cancel_at_period_end and existing_cancel:
        return {
            "recorded": True,
            "business_slug": business_slug,
            "app_user_id": app_user_id,
            "stripe_subscription_id": str(entitlement.stripe_subscription_id or ""),
            "plan_key": entitlement.plan_key,
            "cancel_at_period_end": True,
            "current_period_end": entitlement.current_period_end,
            "stripe_subscription_status": str(
                (entitlement.metadata or {}).get("stripe_subscription_status")
                or entitlement.status
                or ""
            ),
            "already_canceling": True,
        }
    subscription_id = str(entitlement.stripe_subscription_id or "")
    subscription = subscription_updater(subscription_id, bool(cancel_at_period_end))
    if not isinstance(subscription, dict):
        raise ValueError("subscription_updater must return a subscription object")
    reconcile_subscription(conn, subscription)
    refreshed = _cancelable_subscription_entitlement(conn, business_slug, app_user_id) or entitlement
    refreshed_metadata = refreshed.metadata or {}
    return {
        "recorded": True,
        "business_slug": business_slug,
        "app_user_id": app_user_id,
        "stripe_subscription_id": subscription_id,
        "plan_key": refreshed.plan_key,
        "cancel_at_period_end": bool(
            subscription.get("cancel_at_period_end")
            or refreshed_metadata.get("cancel_at_period_end")
        ),
        "current_period_end": refreshed.current_period_end,
        "stripe_subscription_status": str(
            subscription.get("status")
            or refreshed_metadata.get("stripe_subscription_status")
            or refreshed.status
            or ""
        ),
        "already_canceling": False,
    }


def _process_checkout_completed(conn, event: dict, session: dict) -> dict:
    """Port of core.py:6844 + the net-new owner accrual. Runs inside the caller's transaction
    (does not open its own); the entitlement grant and custody accrual it delegates to open
    savepoints under that transaction."""
    return reconcile_checkout_session(
        conn,
        session,
        provider_event_id=str(event.get("id") or "") or None,
        event_created=event.get("created"),
    )


def _process_subscription_event(conn, subscription: dict) -> dict:
    """Port of core.py:6929. Map the Stripe status and push it onto every stripe-sourced
    entitlement carrying this subscription id (in the canonical entitlements home)."""
    subscription_id = subscription.get("id")
    if not subscription_id:
        return {"recorded": False, "reason": "missing_subscription_id"}
    status = _subscription_entitlement_status(str(subscription.get("status") or ""))
    updated = app_entitlements.set_subscription_status(
        conn,
        str(subscription_id),
        status=status,
        stripe_customer_id=_stripe_object_id(subscription.get("customer")),
        current_period_end=_epoch_to_dt(subscription.get("current_period_end")),
        metadata={
            "stripe_subscription_status": subscription.get("status"),
            "cancel_at_period_end": subscription.get("cancel_at_period_end"),
        },
    )
    return {"recorded": bool(updated), "updated": updated}


def _subscription_targets(conn, subscription_id: str):
    """Distinct (business_slug, app_user_id, plan_key) carrying this Stripe subscription id across
    the canonical stripe-sourced entitlements. Empty when the subscription is unknown here — a
    webhook for a subscription this platform never recorded is a no-op, not an error (mirrors
    set_subscription_status)."""
    return conn.execute(
        "select distinct business_slug, app_user_id, plan_key from app_entitlements "
        "where source = 'stripe' and stripe_subscription_id = %s",
        (subscription_id,),
    ).fetchall()


def _invoice_period_end(invoice: dict):
    """The renewed period end from an invoice — the last line item's period end, falling back to
    the invoice-level period_end. Used to extend the entitlement on a successful renewal."""
    lines = invoice.get("lines") if isinstance(invoice.get("lines"), dict) else {}
    data = lines.get("data") if isinstance(lines.get("data"), list) else []
    if data and isinstance(data[-1], dict):
        period = data[-1].get("period")
        if isinstance(period, dict):
            end = _epoch_to_dt(period.get("end"))
            if end is not None:
                return end
    return _epoch_to_dt(invoice.get("period_end"))


def _process_invoice_paid(conn, event: dict, invoice: dict) -> dict:
    """Record recurring-subscription RENEWAL revenue + owner custody accrual on `invoice.paid`.

    The FIRST invoice of a subscription (`billing_reason == 'subscription_create'`) is already
    counted by the checkout.session.completed path, so it is skipped here to avoid double-counting;
    only renewal/proration cycles accrue. A paid renewal also confirms the subscription is current,
    so the entitlement is refreshed to active with the new period end (restoring a sub-user whose
    earlier attempt left them past_due, and clearing any dunning flag). Idempotent on the invoice
    id via the revenue unique key + the webhook_events dedup, so a resent invoice cannot
    double-record."""
    invoice_id = str(invoice.get("id") or "")
    if not invoice_id:
        return {"recorded": False, "reason": "missing_invoice_id"}
    if str(invoice.get("billing_reason") or "") == "subscription_create":
        return {"recorded": False, "reason": "initial_invoice_counted_at_checkout"}
    subscription_id = _stripe_object_id(invoice.get("subscription"))
    if not subscription_id:
        return {"recorded": False, "reason": "missing_subscription_id"}
    targets = _subscription_targets(conn, subscription_id)
    if not targets:
        return {"recorded": False, "reason": "unknown_subscription"}

    customer_id = _stripe_object_id(invoice.get("customer"))
    customer_email = invoice.get("customer_email")
    currency = invoice.get("currency")
    amount_paid = int(invoice.get("amount_paid") or 0)
    provider_event_id = str(event.get("id") or "") or None
    occurred = _epoch_to_dt(event.get("created"))

    # A successful renewal confirms payment -> refresh entitlement(s) active + new period, clear dunning.
    refreshed = app_entitlements.set_subscription_status(
        conn,
        subscription_id,
        status="active",
        stripe_customer_id=customer_id,
        current_period_end=_invoice_period_end(invoice),
        metadata={
            "stripe_subscription_status": "active",
            "dunning": False,
            "last_invoice_id": invoice_id,
        },
    )

    business = str(targets[0][0])
    plan_key = None if targets[0][2] is None else str(targets[0][2])
    plan_policy = app_entitlements.get_plan_policy(conn, business, plan_key) if plan_key else None
    payout_split = _owner_payout_split(plan_policy, amount_paid)
    revenue_metadata = {
        "stripe_object": "invoice",
        "stripe_subscription_id": subscription_id,
        "billing_reason": invoice.get("billing_reason"),
        "pricing_split": {
            "authority": "takyon",
            "gross_cents": amount_paid,
            "platform_fee_cents": payout_split["platform_fee_cents"],
            "prepaid_withheld_cents": payout_split["prepaid_withheld_cents"],
            "prepaid_withheld_microusd": payout_split["prepaid_withheld_microusd"],
            "prepaid_dust_microusd": payout_split["prepaid_dust_microusd"],
            "owner_net_cents": payout_split["owner_net_cents"],
            "included_ai_budget_microusd": payout_split["included_ai_budget_microusd"],
        },
    }

    revenue_recorded = False
    owner_user_id = None
    owed_balance_cents = None
    if currency and amount_paid > 0:
        revenue_recorded = _insert_revenue_event(
            conn,
            business_slug=business,
            provider_event_id=provider_event_id,
            stripe_object_type="invoice",
            stripe_object_id=invoice_id,
            stripe_customer_id=customer_id,
            revenue_type="subscription_renewal",
            status="paid",
            currency=currency or "usd",
            amount_paid_cents=amount_paid,
            customer_email=customer_email,
            occurred_at=occurred,
            metadata=revenue_metadata,
        )
        if revenue_recorded:
            owner_user_id = _resolve_owner(conn, business)
            safebox.open_custody_account(conn, owner_user_id)
            owed_balance_cents = safebox.accrue_custody(
                conn,
                owner_user_id,
                business,
                amount_paid,
                f"app_invoice:{business}:{invoice_id}",
                stripe_ref=invoice_id,
                withheld_cents=payout_split["prepaid_withheld_cents"],
                metadata=revenue_metadata.get("pricing_split"),
            )

    return {
        "recorded": True,
        "type": "invoice.paid",
        "business_slug": business,
        "plan_key": plan_key,
        "amount_paid_cents": amount_paid,
        "revenue_recorded": revenue_recorded,
        "accrued_to_owner": owner_user_id is not None,
        "owner_user_id": owner_user_id,
        "owner_owed_balance_cents": owed_balance_cents,
        "entitlements_refreshed": len(refreshed),
    }


def _process_invoice_payment_failed(conn, event: dict, invoice: dict) -> dict:
    """Mark a failed recurring charge as DUNNING on the matching entitlements WITHOUT changing
    access. Access stays governed by the Stripe subscription status (active/past_due/canceled) via
    `customer.subscription.updated/deleted` — Stripe runs its own smart-retry grace window, so a
    single failed attempt must not revoke a sub-user who will retry-succeed. Flipping to a
    non-active status here would revoke immediately (past_due is NOT an access status), so this only
    records the dunning signal for visibility; the next `invoice.paid` clears it."""
    subscription_id = _stripe_object_id(invoice.get("subscription"))
    if not subscription_id:
        return {"recorded": False, "reason": "missing_subscription_id"}
    marked = app_entitlements.patch_subscription_metadata(
        conn,
        subscription_id,
        metadata={
            "dunning": True,
            "last_payment_failed_invoice": str(invoice.get("id") or ""),
            "last_payment_failed_event": str(event.get("id") or ""),
        },
    )
    return {
        "recorded": bool(marked),
        "type": "invoice.payment_failed",
        "dunning_marked": int(marked),
    }


def _process_charge_reversal(conn, event: dict, obj: dict) -> dict:
    """Revoke paid access and record a reversal on a refund (`charge.refunded`) or chargeback
    (`charge.dispute.created`). Resolve the original payment back to its business via the stored
    checkout session (by payment_intent, else by customer), flip its stripe-sourced entitlement(s)
    to `cancelled` so the sub-user loses paid access, and append a `reversal` revenue row (stored
    as a positive amount but netted OUT of revenue totals by `get_revenue_summary`).

    NOTE: owner custody is NOT auto-clawed-back here — the owner may already have been paid out, so
    clawback belongs to the payout/netting rail. The reversal row carries
    `custody_clawback_pending=true` so that rail can offset it; this leaf does not silently
    over- or under-credit custody."""
    event_type = str(event.get("type") or "")
    is_dispute = event_type == "charge.dispute.created"
    object_id = str(obj.get("id") or "")
    if not object_id:
        return {"recorded": False, "reason": "missing_object_id", "type": event_type}
    payment_intent_id = _stripe_object_id(obj.get("payment_intent"))
    customer_id = _stripe_object_id(obj.get("customer"))
    amount = int((obj.get("amount") if is_dispute else obj.get("amount_refunded")) or 0)
    currency = obj.get("currency")
    provider_event_id = str(event.get("id") or "") or None
    occurred = _epoch_to_dt(event.get("created"))

    cols = (
        "business_slug, stripe_subscription_id, stripe_customer_id, customer_email, "
        "stripe_checkout_session_id"
    )
    row = None
    if payment_intent_id:
        row = conn.execute(
            f"select {cols} from app_checkout_sessions where stripe_payment_intent_id = %s limit 1",
            (payment_intent_id,),
        ).fetchone()
    if row is None and customer_id:
        row = conn.execute(
            f"select {cols} from app_checkout_sessions where stripe_customer_id = %s "
            "order by created_at desc limit 1",
            (customer_id,),
        ).fetchone()
    if row is None:
        return {"recorded": False, "reason": "unknown_payment", "type": event_type}
    business = str(row[0])
    subscription_id = None if row[1] is None else str(row[1])
    resolved_customer = customer_id or (None if row[2] is None else str(row[2]))
    customer_email = None if row[3] is None else str(row[3])
    session_id = None if row[4] is None else str(row[4])

    revoke_meta = {"reversal": event_type, "reversal_object_id": object_id}
    revoked = 0
    if subscription_id:
        revoked = len(
            app_entitlements.set_subscription_status(
                conn,
                subscription_id,
                status="cancelled",
                stripe_customer_id=resolved_customer,
                metadata={"stripe_subscription_status": "cancelled", **revoke_meta},
            )
        )
    elif session_id:
        revoked = app_entitlements.cancel_checkout_session_entitlements(
            conn,
            business,
            session_id,
            metadata=revoke_meta,
        )

    reversal_metadata = {
        "stripe_object": "dispute" if is_dispute else "charge",
        "reversal": event_type,
        "original_payment_intent": payment_intent_id,
        "stripe_subscription_id": subscription_id,
        "custody_clawback_pending": amount > 0,
    }
    reversal_recorded = _insert_revenue_event(
        conn,
        business_slug=business,
        provider_event_id=provider_event_id,
        stripe_object_type="dispute" if is_dispute else "charge",
        stripe_object_id=object_id,
        stripe_checkout_session_id=session_id,
        stripe_customer_id=resolved_customer,
        revenue_type="reversal",
        status="disputed" if is_dispute else "refunded",
        currency=currency or "usd",
        amount_paid_cents=abs(amount),
        customer_email=customer_email,
        occurred_at=occurred,
        metadata=reversal_metadata,
    )

    return {
        "recorded": True,
        "type": event_type,
        "business_slug": business,
        "access_revoked": revoked,
        "reversal_recorded": reversal_recorded,
        "amount_reversed_cents": abs(amount),
        "custody_clawback_pending": amount > 0,
    }


def _resolve_owner(conn, business_slug: str) -> str:
    row = conn.execute(
        "select owner_user_id from businesses where slug = %s",
        (business_slug,),
    ).fetchone()
    if row is None or row[0] is None:
        raise BusinessOwnerMissing(business_slug)
    return str(row[0])


# --------------------------------------------------------------------------- reads


def list_revenue_events(
    conn, business_slug: str, *, limit: int = 100
) -> list[RevenueEvent]:
    """Revenue events for a business, newest first. Read."""
    rows = conn.execute(
        f"select {_REVENUE_COLUMNS} from app_revenue_events "
        "where business_slug = %s order by occurred_at desc, created_at desc limit %s",
        (business_slug, int(limit)),
    ).fetchall()
    return [_revenue_from_row(r) for r in rows]


def get_revenue_summary(conn, business_slug: str) -> dict:
    """NET recorded revenue for a business (mirrors core.py:3710). Read.

    Reversal rows (refunds/chargebacks) are stored with a positive amount_paid_cents (the table
    CHECKs amount >= 0) but tagged revenue_type='reversal'; they are subtracted here so the total
    reflects money actually kept. `events` counts all rows including reversals."""
    row = conn.execute(
        "select coalesce(sum(case when revenue_type = 'reversal' then -amount_paid_cents "
        " else amount_paid_cents end), 0), count(*) from app_revenue_events "
        "where business_slug = %s",
        (business_slug,),
    ).fetchone()
    return {
        "business_slug": business_slug,
        "amount_paid_cents": int(row[0]),
        "events": int(row[1]),
    }
