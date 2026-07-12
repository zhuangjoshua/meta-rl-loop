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
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable

from plugins.takyon import app_entitlements, custody, safebox

_SUBSCRIPTION_EVENT_TYPES = (
    "customer.subscription.created",
    "customer.subscription.updated",
    "customer.subscription.deleted",
)
_CHARGE_REVERSAL_EVENT_TYPES = ("charge.refunded",)
_DISPUTE_WITHDRAWAL_EVENT_TYPES = (
    "charge.dispute.created",
    "charge.dispute.funds_withdrawn",
)
_DISPUTE_RELEASE_EVENT_TYPES = (
    "charge.dispute.closed",
    "charge.dispute.funds_reinstated",
)
_DISPUTE_UPDATED_EVENT_TYPE = "charge.dispute.updated"


class AppPaymentError(Exception):
    """Base for checkout/webhook/revenue errors."""


class InvalidWebhookEvent(AppPaymentError):
    """The event payload is missing a usable provider event id."""


class RetryableWebhookEvent(AppPaymentError):
    """A valid live event arrived before the state it must update; Stripe should retry."""


class CheckoutIntentNotFound(AppPaymentError):
    """No checkout intent matches the given handle."""


class ActiveSubscriptionExists(AppPaymentError):
    """The profile already has a non-terminal Stripe subscription."""


class CheckoutAlreadyOpen(AppPaymentError):
    """The profile already has an unexpired Checkout attempt for another plan."""


class BusinessOwnerMissing(AppPaymentError):
    """A business has no resolvable owner_user_id — an integrity violation (0001 makes it
    NOT NULL), so a paid revenue event could not be accrued to anyone."""


class CancelableSubscriptionNotFound(AppPaymentError):
    """The sub-user has no Stripe-backed subscription that can still be cancelled."""


class InvalidSubscriptionCancellation(AppPaymentError):
    """Stripe did not confirm immediate cancellation of the exact subscription requested."""


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


@dataclass(frozen=True)
class InvoicePaymentEvidence:
    payment_intent_ids: tuple[str, ...]
    charge_ids: tuple[str, ...]
    payment_intent_allocations_cents: dict[str, int]
    charge_allocations_cents: dict[str, int]
    collected_cents: int
    valid: bool


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


def _stripe_environment(*payloads: object) -> str:
    """Classify Stripe money without ever promoting an unknown/test object to live."""
    for payload in payloads:
        if isinstance(payload, dict) and isinstance(payload.get("livemode"), bool):
            return "live" if payload["livemode"] else "test"
    return "test"


def _stripe_metadata(payload: object) -> dict[str, Any]:
    if not isinstance(payload, dict) or not isinstance(payload.get("metadata"), dict):
        return {}
    return payload["metadata"]


def _invoice_subscription_metadata(invoice: object) -> dict[str, Any]:
    if not isinstance(invoice, dict):
        return {}
    direct = _stripe_metadata(invoice)
    if direct:
        return direct
    parent = invoice.get("parent")
    details = parent.get("subscription_details") if isinstance(parent, dict) else None
    return _stripe_metadata(details)


def _is_live_takyon_app_object(payload: object, *, invoice: bool = False) -> bool:
    metadata = _invoice_subscription_metadata(payload) if invoice else _stripe_metadata(payload)
    return (
        _expected_stripe_environment() == "live"
        and str(metadata.get("source") or "") == "takyon_app"
    )


def _expected_stripe_environment() -> str:
    return "live" if str(os.getenv("TAKYON_STRIPE_MODE") or "test").strip().lower() == "live" else "test"


def _expected_live_stripe_account_id() -> str:
    return str(os.getenv("TAKYON_STRIPE_ACCOUNT_ID") or "").strip()


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
    event_metadata = dict(metadata or {})
    normalized_status = str(status or "paid")
    if event_metadata.get("stripe_environment") != "live" and not normalized_status.startswith(
        "test_"
    ):
        normalized_status = f"test_{normalized_status}"
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
            normalized_status,
            currency,
            int(amount_paid_cents or 0),
            customer_email,
            occurred_at,
            _json_dumps(event_metadata),
        ),
    ).fetchone()
    return bool(row and row[0])


# --------------------------------------------------------------------------- checkout intents


def create_session_checkout_intent(
    conn,
    business_slug: str,
    *,
    session_hash: str,
    plan_key: str,
    client_reference_id: str,
    metadata: dict | None = None,
) -> CheckoutIntent:
    """Create/reuse a Checkout intent through the session-bound app-runtime DB port."""
    business = str(business_slug or "").strip()
    session = str(session_hash or "").strip()
    plan = str(plan_key or "").strip()
    ref = str(client_reference_id or "").strip()
    if not business or not session or not plan or not ref:
        raise ValueError("business_slug, session_hash, plan_key, and client_reference_id are required")
    try:
        row = conn.execute(
            f"select {_INTENT_COLUMNS} from takyon_app_create_checkout_intent("
            "%s, %s, %s, %s, %s::jsonb)",
            (business, session, plan, ref, _json_dumps(metadata)),
        ).fetchone()
    except Exception as exc:
        message = str(exc)
        if "app_checkout_active_subscription" in message:
            raise ActiveSubscriptionExists(business) from exc
        if "app_checkout_already_open:" in message:
            existing_plan = message.split("app_checkout_already_open:", 1)[1].splitlines()[0]
            raise CheckoutAlreadyOpen(existing_plan) from exc
        raise
    if row is None:
        raise AppPaymentError("app_checkout_intent_not_created")
    return _intent_from_row(row)


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
        app_entitlements.lock_plan_economics(conn, business_slug, plan)
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
            processed = _process_subscription_event(conn, obj, event=event)
        elif event_type == "invoice.paid" and isinstance(obj, dict):
            processed = _process_invoice_paid(conn, event, obj)
        elif event_type == "invoice.payment_failed" and isinstance(obj, dict):
            processed = _process_invoice_payment_failed(conn, event, obj)
        elif event_type in _CHARGE_REVERSAL_EVENT_TYPES and isinstance(obj, dict):
            processed = _process_charge_reversal(conn, event, obj)
        elif event_type in _DISPUTE_WITHDRAWAL_EVENT_TYPES and isinstance(obj, dict):
            dispute_status = str(obj.get("status") or "").strip().lower()
            if dispute_status in {"won", "warning_closed"}:
                processed = _process_dispute_closed(conn, event, obj)
            elif dispute_status.startswith("warning_"):
                processed = {
                    "recorded": True,
                    "type": event_type,
                    "dispute_status": dispute_status,
                    "inquiry_observed": True,
                }
            else:
                processed = _process_charge_reversal(conn, event, obj)
        elif event_type in _DISPUTE_RELEASE_EVENT_TYPES and isinstance(obj, dict):
            processed = _process_dispute_closed(conn, event, obj)
        elif event_type == _DISPUTE_UPDATED_EVENT_TYPE and isinstance(obj, dict):
            processed = {
                "recorded": True,
                "type": event_type,
                "dispute_status": str(obj.get("status") or ""),
                "update_observed": True,
            }
        else:
            processed = {"recorded": False, "ignored": event_type}
        if _expected_stripe_environment() == "live" and isinstance(processed, dict):
            dependency_pending = False
            if event_type == "checkout.session.completed" and _is_live_takyon_app_object(obj):
                dependency_pending = not bool(processed.get("recorded"))
            elif event_type in _SUBSCRIPTION_EVENT_TYPES and _is_live_takyon_app_object(obj):
                dependency_pending = not bool(processed.get("recorded"))
            elif event_type in {"invoice.paid", "invoice.payment_failed"} and _is_live_takyon_app_object(
                obj, invoice=True
            ):
                dependency_pending = processed.get("reason") in {
                    "unknown_subscription",
                    "invoice_economics_mismatch",
                    "invoice_payment_evidence_mismatch",
                    "invoice_account_binding_mismatch",
                    "invoice_subscription_proof_mismatch",
                } or (
                    not bool(processed.get("recorded"))
                    and event_type == "invoice.payment_failed"
                )
            elif event_type in _CHARGE_REVERSAL_EVENT_TYPES:
                metadata = _stripe_metadata(obj)
                is_known_non_app = bool(metadata.get("purpose")) and not bool(
                    metadata.get("source") == "takyon_app"
                )
                dependency_pending = (
                    processed.get("reason")
                    in {
                        "unknown_payment",
                        "reversal_account_binding_mismatch",
                        "reversal_economics_missing",
                        "reversal_economics_mismatch",
                        "reversal_payment_allocation_mismatch",
                        "reversal_recipient_missing",
                        "reversal_timestamp_missing",
                        "reversal_overlap_pending",
                    }
                    and not is_known_non_app
                )
            elif event_type in _DISPUTE_WITHDRAWAL_EVENT_TYPES:
                metadata = _stripe_metadata(obj)
                is_known_non_app = bool(metadata.get("purpose")) and not bool(
                    metadata.get("source") == "takyon_app"
                )
                dependency_pending = (
                    processed.get("reason")
                    in {
                        "unknown_payment",
                        "reversal_account_binding_mismatch",
                        "reversal_economics_missing",
                        "reversal_economics_mismatch",
                        "reversal_payment_allocation_mismatch",
                        "reversal_recipient_missing",
                        "reversal_timestamp_missing",
                        "reversal_overlap_pending",
                    }
                    and not is_known_non_app
                )
            elif event_type in _DISPUTE_RELEASE_EVENT_TYPES:
                metadata = _stripe_metadata(obj)
                is_known_non_app = bool(metadata.get("purpose")) and not bool(
                    metadata.get("source") == "takyon_app"
                )
                dependency_pending = (
                    processed.get("reason")
                    in {
                        "unknown_dispute",
                        "unknown_payment",
                        "dispute_release_invalid",
                        "reversal_account_binding_mismatch",
                        "reversal_economics_missing",
                        "reversal_economics_mismatch",
                        "reversal_recipient_missing",
                        "reversal_timestamp_missing",
                        "reversal_overlap_pending",
                    }
                    and not is_known_non_app
                )
            if dependency_pending:
                raise RetryableWebhookEvent(f"stripe_event_dependency_pending:{event_type}")
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
    livemode: bool | None = None,
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
    stripe_environment = _stripe_environment(
        session,
        {"livemode": livemode} if isinstance(livemode, bool) else None,
    )
    metadata = {**metadata, "stripe_environment": stripe_environment}
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
    invoice_proof = session.get("_takyon_invoice")
    invoice_evidence = _invoice_payment_evidence(invoice_proof)
    invoice_payment_intents = list(invoice_evidence.payment_intent_ids)
    invoice_charges = list(invoice_evidence.charge_ids)
    payment_intent_ids = sorted(
        {value for value in [payment_intent_id, *invoice_payment_intents] if value}
    )
    charge_ids = sorted(set(invoice_charges))
    if not payment_intent_id and payment_intent_ids:
        payment_intent_id = payment_intent_ids[0]
    session_id = str(session.get("id") or "")
    if not session_id:
        return {"recorded": False, "reason": "missing_session_id"}
    occurred = _epoch_to_dt(event_created)
    payment_status = session.get("payment_status")
    currency = session.get("currency")
    amount_total = int(session.get("amount_total") or 0)
    if str(payment_status or "").strip().lower() != "paid":
        return {
            "recorded": False,
            "reason": "payment_not_settled",
            "business_slug": business,
            "plan_key": plan_key,
        }
    expected_stripe_environment = _expected_stripe_environment()
    if stripe_environment != expected_stripe_environment:
        return {"recorded": False, "reason": "checkout_environment_mismatch"}
    if expected_stripe_environment == "live":
        invoice_evidence = _invoice_payment_evidence(invoice_proof, strict=True)
        if (
            not isinstance(invoice_proof, dict)
            or _stripe_object_id(invoice_proof.get("id")) != invoice_id
            or str(invoice_proof.get("status") or "").strip().lower() != "paid"
            or str(invoice_proof.get("currency") or "").strip().lower()
            != str(currency or "").strip().lower()
            or int(invoice_proof.get("amount_paid") or 0) != amount_total
            or not invoice_evidence.valid
            or invoice_evidence.collected_cents != amount_total
            or (
                payment_intent_id
                and payment_intent_id not in invoice_evidence.payment_intent_ids
            )
        ):
            return {"recorded": False, "reason": "checkout_payment_evidence_mismatch"}
        payment_intent_ids = list(invoice_evidence.payment_intent_ids)
        charge_ids = list(invoice_evidence.charge_ids)
        if not payment_intent_id and payment_intent_ids:
            payment_intent_id = payment_intent_ids[0]
    plan_policy = app_entitlements.get_plan_policy(conn, business, plan_key)
    session_economics_version = str(metadata.get("economics_version") or "").strip()
    if expected_stripe_environment == "live":
        expected_account_id = _expected_live_stripe_account_id()
        if not expected_account_id:
            return {"recorded": False, "reason": "checkout_account_binding_missing"}
        expected_binding = {
            "source": "takyon_app",
            "business": str(business),
            "business_id": str(business),
            "plan_key": str(plan_key),
            "checkout_intent_id": intent_id,
            "takyon_stripe_account_id": expected_account_id,
        }
        if any(
            str(metadata.get(key) or "") != value
            for key, value in expected_binding.items()
        ):
            return {"recorded": False, "reason": "checkout_account_binding_mismatch"}
    if expected_stripe_environment == "live" and not session_economics_version:
        return {"recorded": False, "reason": "checkout_economics_missing"}
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
        expected_plan_binding = {
            "tier": str(plan_policy.tier or ""),
            "currency": str(plan_policy.currency or "usd").lower(),
            "price_cents": str(int(plan_policy.price_cents)),
            "billing_interval": str(plan_policy.billing_interval or "month"),
            "included_ai_budget_microusd": str(
                int(plan_policy.included_ai_budget_microusd)
            ),
            "included_action_quota": str(int(plan_policy.included_action_quota)),
        }
        if (
            session_economics_version != current_economics_version
            or any(
                str(metadata.get(key) or "") != value
                for key, value in expected_plan_binding.items()
            )
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
    if (intent_app_user_id or customer_email) and payment_status == "paid":
        entitlement_metadata = {"stripe_environment": stripe_environment}
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
            tier=(
                str(plan_policy.tier)
                if plan_policy is not None
                else "paid"
            ),
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
    owner_user_id = None
    custody_key = None
    if currency and payment_status == "paid" and amount_total > 0:
        owner_user_id = _resolve_owner(conn, business)
        custody_key = (
            f"app_revenue:{business}:{provider_event_id}:{session_id}"
            if provider_event_id
            else f"app_revenue_session:{business}:{session_id}"
        )
    revenue_metadata = {
        **metadata,
        "stripe_invoice_id": invoice_id,
        "stripe_subscription_id": subscription_id,
        "stripe_payment_intent_ids": payment_intent_ids,
        "stripe_charge_ids": charge_ids,
        "stripe_payment_allocations_cents": {
            "payment_intent": invoice_evidence.payment_intent_allocations_cents,
            "charge": invoice_evidence.charge_allocations_cents,
        },
        "stripe_collected_cents": invoice_evidence.collected_cents,
        "custody_user_id": owner_user_id,
        "custody_accrual_key": custody_key,
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
        if revenue_recorded and amount_total > 0 and owner_user_id and custody_key:
            safebox.open_custody_account(conn, owner_user_id)
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


def _subscription_entitlement_for_cancellation(conn, business_slug: str, app_user_id: str):
    """Newest Stripe subscription for this user, preferring one not already canceled.

    Keeping the terminal row as a fallback makes the customer cancellation operation idempotent:
    a repeated click returns the already-canceled provider truth instead of turning a successful
    first request into a misleading 404.
    """
    terminal = None
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
            if terminal is None:
                terminal = entitlement
            continue
        return entitlement
    return terminal


def cancel_subscription(
    conn,
    business_slug: str,
    *,
    app_user_id: str,
    subscription_canceler: Callable[[str], dict[str, Any]],
) -> dict[str, Any]:
    """Immediately cancel one Stripe-backed product subscription for a sub-user.

    Cancellation is serialized per business/user, provider-authoritative, and has no grace-period
    mode: the injected callback must perform Stripe's DELETE subscription operation and return the
    exact subscription with terminal ``canceled`` status. Durable truth is reconciled before this
    call commits, so access ends in the same request. A repeated call returns the terminal local
    projection without issuing another provider mutation.
    """
    business = str(business_slug or "").strip()
    user = str(app_user_id or "").strip()
    if not business or not user:
        raise ValueError("business_slug and app_user_id are required")

    with conn.transaction():
        conn.execute(
            "select pg_advisory_xact_lock(hashtextextended(%s, 0))",
            (f"takyon-app-subscription-cancel:{business}:{user}",),
        )
        entitlement = _subscription_entitlement_for_cancellation(conn, business, user)
        if entitlement is None:
            raise CancelableSubscriptionNotFound("no Stripe subscription found")
        subscription_id = str(entitlement.stripe_subscription_id or "")
        existing_status = str(entitlement.status or "").strip().lower()
        if existing_status in {"cancelled", "canceled"}:
            metadata = entitlement.metadata or {}
            return {
                "recorded": True,
                "business_slug": business,
                "app_user_id": user,
                "stripe_subscription_id": subscription_id,
                "plan_key": entitlement.plan_key,
                "cancel_at_period_end": False,
                "current_period_end": entitlement.current_period_end,
                "stripe_subscription_status": str(
                    metadata.get("stripe_subscription_status") or "canceled"
                ),
                "effective_immediately": True,
                "already_canceled": True,
                "already_canceling": False,
            }

        subscription = subscription_canceler(subscription_id)
        if not isinstance(subscription, dict):
            raise InvalidSubscriptionCancellation(
                "subscription_canceler must return a subscription object"
            )
        returned_id = _stripe_object_id(subscription.get("id"))
        returned_status = str(subscription.get("status") or "").strip().lower()
        if returned_id != subscription_id or returned_status not in {"canceled", "cancelled"}:
            raise InvalidSubscriptionCancellation(
                "Stripe did not confirm immediate cancellation of the requested subscription"
            )
        reconcile_subscription(conn, subscription)
        refreshed = (
            _subscription_entitlement_for_cancellation(conn, business, user) or entitlement
        )
        refreshed_metadata = refreshed.metadata or {}
        return {
            "recorded": True,
            "business_slug": business,
            "app_user_id": user,
            "stripe_subscription_id": subscription_id,
            "plan_key": refreshed.plan_key,
            "cancel_at_period_end": False,
            "current_period_end": refreshed.current_period_end,
            "stripe_subscription_status": returned_status,
            "effective_immediately": True,
            "already_canceled": False,
            "already_canceling": False,
        }


def _process_checkout_completed(conn, event: dict, session: dict) -> dict:
    """Port of core.py:6844 + the net-new owner accrual. Runs inside the caller's transaction
    (does not open its own); the entitlement grant and custody accrual it delegates to open
    savepoints under that transaction."""
    if _expected_stripe_environment() == "live" and not _is_live_takyon_app_object(session):
        return {"recorded": False, "ignored": "non_app_checkout"}
    return reconcile_checkout_session(
        conn,
        session,
        provider_event_id=str(event.get("id") or "") or None,
        event_created=event.get("created"),
        livemode=event.get("livemode"),
    )


def _process_subscription_event(
    conn, subscription: dict, *, event: dict | None = None
) -> dict:
    """Port of core.py:6929. Map the Stripe status and push it onto every stripe-sourced
    entitlement carrying this subscription id (in the canonical entitlements home)."""
    if _expected_stripe_environment() == "live" and not _is_live_takyon_app_object(subscription):
        return {"recorded": False, "ignored": "non_app_subscription"}
    if _expected_stripe_environment() == "live" and (
        not _expected_live_stripe_account_id()
        or _stripe_metadata(subscription).get("takyon_stripe_account_id")
        != _expected_live_stripe_account_id()
    ):
        return {"recorded": False, "reason": "subscription_account_binding_mismatch"}
    subscription_id = subscription.get("id")
    if not subscription_id:
        return {"recorded": False, "reason": "missing_subscription_id"}
    status = _subscription_entitlement_status(str(subscription.get("status") or ""))
    updated = app_entitlements.set_subscription_status(
        conn,
        str(subscription_id),
        status=status,
        stripe_customer_id=_stripe_object_id(subscription.get("customer")),
        current_period_end=_subscription_period_end(subscription),
        metadata={
            "stripe_subscription_status": subscription.get("status"),
            "cancel_at_period_end": subscription.get("cancel_at_period_end"),
            "stripe_environment": _stripe_environment(subscription, event),
            "stripe_lifecycle_event_id": str((event or {}).get("id") or ""),
            "stripe_lifecycle_event_created": int((event or {}).get("created") or 0),
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


def _subscription_period_end(subscription: dict) -> datetime | None:
    """Resolve the legacy subscription period or the earliest Dahlia item period.

    Stripe subscriptions can contain items with different billing periods. The earliest valid
    item boundary is the conservative entitlement fallback when no legacy top-level value exists.
    """
    legacy_end = _epoch_to_dt(subscription.get("current_period_end"))
    if legacy_end is not None:
        return legacy_end
    items = subscription.get("items")
    data = items.get("data") if isinstance(items, dict) else None
    if not isinstance(data, list):
        return None
    item_ends = [
        period_end
        for item in data
        if isinstance(item, dict)
        for period_end in [_epoch_to_dt(item.get("current_period_end"))]
        if period_end is not None
    ]
    return min(item_ends) if item_ends else None


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


def _invoice_subscription_id(invoice: dict) -> str | None:
    """Resolve the subscription across Stripe's legacy and 2026 invoice schemas."""
    legacy_id = _stripe_object_id(invoice.get("subscription"))
    if legacy_id:
        return legacy_id
    parent = invoice.get("parent")
    if not isinstance(parent, dict):
        return None
    subscription_details = parent.get("subscription_details")
    if not isinstance(subscription_details, dict):
        return None
    return _stripe_object_id(subscription_details.get("subscription"))


def _invoice_payment_evidence(
    invoice: object, *, strict: bool = False
) -> InvoicePaymentEvidence:
    """Resolve only credited Stripe payments and preserve their invoice allocation caps.

    Basil Invoice ``amount_paid`` can include out-of-band money, and one PaymentIntent can be
    allocated across multiple invoices. A live proof is therefore valid only when the complete
    InvoicePayment list identifies each credited amount, currency, environment, invoice, and
    reversible Stripe payment object. Test/legacy fixtures retain reference-only compatibility but
    never become valid strict evidence without allocation amounts.
    """
    if not isinstance(invoice, dict):
        return InvoicePaymentEvidence((), (), {}, {}, 0, not strict)

    invoice_id = str(invoice.get("id") or "").strip()
    invoice_currency = str(invoice.get("currency") or "").strip().lower()
    invoice_livemode = invoice.get("livemode")
    payment_intents: set[str] = set()
    charges: set[str] = set()
    payment_intent_allocations: dict[str, int] = {}
    charge_allocations: dict[str, int] = {}
    valid = True

    payments = invoice.get("payments")
    rows = payments.get("data") if isinstance(payments, dict) else None
    if strict and (
        not isinstance(payments, dict)
        or not isinstance(rows, list)
        or payments.get("object") != "list"
        or payments.get("has_more") is not False
        or not invoice_id
        or not invoice_currency
        or invoice_livemode is not True
    ):
        valid = False

    if isinstance(rows, list):
        for row in rows:
            if not isinstance(row, dict):
                if strict:
                    valid = False
                continue
            if strict and row.get("object") != "invoice_payment":
                valid = False
                continue
            status = str(row.get("status") or "").strip().lower()
            # Older test fixtures predate InvoicePayment status. They remain reference-compatible;
            # strict live evidence requires Stripe's explicit `paid` allocation state.
            if status and status != "paid":
                continue
            if strict and status != "paid":
                valid = False
                continue

            payment = row.get("payment")
            if not isinstance(payment, dict):
                if strict:
                    valid = False
                continue
            payment_type = str(payment.get("type") or "").strip().lower()
            payment_id = None
            allocations = None
            if payment_type == "payment_intent" or (
                not strict and not payment_type and payment.get("payment_intent")
            ):
                payment_id = _stripe_object_id(payment.get("payment_intent"))
                allocations = payment_intent_allocations
                if payment_id:
                    payment_intents.add(payment_id)
            elif payment_type == "charge" or (
                not strict and not payment_type and payment.get("charge")
            ):
                payment_id = _stripe_object_id(payment.get("charge"))
                allocations = charge_allocations
                if payment_id:
                    charges.add(payment_id)
            elif strict:
                valid = False
                continue
            else:
                payment_intent = _stripe_object_id(payment.get("payment_intent"))
                charge = _stripe_object_id(payment.get("charge"))
                if payment_intent:
                    payment_intents.add(payment_intent)
                if charge:
                    charges.add(charge)
                continue

            raw_amount = row.get("amount_paid")
            amount = (
                int(raw_amount)
                if isinstance(raw_amount, int) and not isinstance(raw_amount, bool)
                else 0
            )
            if strict and (
                not payment_id
                or amount <= 0
                or _stripe_object_id(row.get("invoice")) != invoice_id
                or row.get("livemode") is not invoice_livemode
                or str(row.get("currency") or "").strip().lower() != invoice_currency
            ):
                valid = False
                continue
            if payment_id and amount > 0 and allocations is not None:
                allocations[payment_id] = allocations.get(payment_id, 0) + amount

    if not strict:
        legacy_payment_intent = _stripe_object_id(invoice.get("payment_intent"))
        legacy_charge = _stripe_object_id(invoice.get("charge"))
        if legacy_payment_intent:
            payment_intents.add(legacy_payment_intent)
        if legacy_charge:
            charges.add(legacy_charge)

    collected_cents = sum(payment_intent_allocations.values()) + sum(
        charge_allocations.values()
    )
    return InvoicePaymentEvidence(
        tuple(sorted(payment_intents)),
        tuple(sorted(charges)),
        payment_intent_allocations,
        charge_allocations,
        collected_cents,
        valid,
    )


def _invoice_payment_references(invoice: object) -> tuple[list[str], list[str]]:
    """Compatibility projection for callers/tests that need only immutable Stripe IDs."""
    evidence = _invoice_payment_evidence(invoice)
    return list(evidence.payment_intent_ids), list(evidence.charge_ids)


def _process_invoice_paid(conn, event: dict, invoice: dict) -> dict:
    """Record recurring-subscription RENEWAL revenue + owner custody accrual on `invoice.paid`.

    The FIRST invoice of a subscription (`billing_reason == 'subscription_create'`) is already
    counted by the checkout.session.completed path, so it is skipped here to avoid double-counting;
    only renewal/proration cycles accrue. A paid renewal also confirms the subscription is current,
    so the entitlement is refreshed to active with the new period end (restoring a sub-user whose
    earlier attempt left them past_due, and clearing any dunning flag). Idempotent on the invoice
    id via the revenue unique key + the webhook_events dedup, so a resent invoice cannot
    double-record."""
    if _expected_stripe_environment() == "live" and not _is_live_takyon_app_object(
        invoice, invoice=True
    ):
        return {"recorded": False, "ignored": "non_app_invoice"}
    if _expected_stripe_environment() == "live" and (
        not _expected_live_stripe_account_id()
        or _invoice_subscription_metadata(invoice).get("takyon_stripe_account_id")
        != _expected_live_stripe_account_id()
    ):
        return {"recorded": False, "reason": "invoice_account_binding_mismatch"}
    invoice_id = str(invoice.get("id") or "")
    if not invoice_id:
        return {"recorded": False, "reason": "missing_invoice_id"}
    if str(invoice.get("billing_reason") or "") == "subscription_create":
        return {"recorded": False, "reason": "initial_invoice_counted_at_checkout"}
    subscription_id = _invoice_subscription_id(invoice)
    if not subscription_id:
        return {"recorded": False, "reason": "missing_subscription_id"}
    targets = _subscription_targets(conn, subscription_id)
    if not targets:
        return {"recorded": False, "reason": "unknown_subscription"}

    business = str(targets[0][0])
    plan_key = None if targets[0][2] is None else str(targets[0][2])
    plan_policy = app_entitlements.get_plan_policy(conn, business, plan_key) if plan_key else None

    customer_id = _stripe_object_id(invoice.get("customer"))
    customer_email = invoice.get("customer_email")
    currency = invoice.get("currency")
    amount_paid = int(invoice.get("amount_paid") or 0)
    provider_event_id = str(event.get("id") or "") or None
    occurred = _epoch_to_dt(event.get("created"))
    if _expected_stripe_environment() == "live" and (
        plan_policy is None
        or str(currency or "").lower() != "usd"
        or str(plan_policy.currency or "").lower() != "usd"
    ):
        return {"recorded": False, "reason": "invoice_economics_mismatch"}
    invoice_evidence = _invoice_payment_evidence(
        invoice, strict=_expected_stripe_environment() == "live"
    )
    invoice_payment_intents = list(invoice_evidence.payment_intent_ids)
    invoice_charges = list(invoice_evidence.charge_ids)
    if _expected_stripe_environment() == "live" and (
        str(invoice.get("status") or "").strip().lower() != "paid"
        or not invoice_evidence.valid
        or invoice_evidence.collected_cents != amount_paid
    ):
        return {"recorded": False, "reason": "invoice_payment_evidence_mismatch"}
    subscription_proof = invoice.get("_takyon_subscription")
    if _expected_stripe_environment() == "live" and (
        not isinstance(subscription_proof, dict)
        or _stripe_object_id(subscription_proof.get("id")) != subscription_id
        or not _is_live_takyon_app_object(subscription_proof)
        or _stripe_metadata(subscription_proof).get("takyon_stripe_account_id")
        != _expected_live_stripe_account_id()
    ):
        return {"recorded": False, "reason": "invoice_subscription_proof_mismatch"}
    current_subscription = (
        subscription_proof if isinstance(subscription_proof, dict) else None
    )
    entitlement_status = (
        _subscription_entitlement_status(
            str(current_subscription.get("status") or "")
        )
        if current_subscription is not None
        else "active"
    )

    # Revenue follows the paid Invoice, while access follows Stripe's CURRENT subscription state.
    refreshed = app_entitlements.set_subscription_status(
        conn,
        subscription_id,
        status=entitlement_status,
        stripe_customer_id=customer_id,
        current_period_end=(
            _subscription_period_end(current_subscription)
            if current_subscription is not None
            else _invoice_period_end(invoice)
        ),
        metadata={
            "stripe_subscription_status": (
                current_subscription.get("status")
                if current_subscription is not None
                else "active"
            ),
            "dunning": False,
            "last_invoice_id": invoice_id,
            "stripe_environment": _stripe_environment(invoice, event),
            "payment_settled": True,
            "payment_revoked": False,
            "payment_reactivation_blocked": False,
            "stripe_payment_event_id": provider_event_id,
            "stripe_payment_event_created": int(event.get("created") or 0),
        },
    )

    payout_split = _owner_payout_split(plan_policy, amount_paid)
    owner_user_id = None
    custody_key = None
    if currency and amount_paid > 0:
        owner_user_id = _resolve_owner(conn, business)
        custody_key = f"app_invoice:{business}:{invoice_id}"
    revenue_metadata = {
        **_invoice_subscription_metadata(invoice),
        "stripe_object": "invoice",
        "stripe_subscription_id": subscription_id,
        "billing_reason": invoice.get("billing_reason"),
        "stripe_invoice_id": invoice_id,
        "stripe_payment_intent_ids": invoice_payment_intents,
        "stripe_charge_ids": invoice_charges,
        "stripe_payment_allocations_cents": {
            "payment_intent": invoice_evidence.payment_intent_allocations_cents,
            "charge": invoice_evidence.charge_allocations_cents,
        },
        "stripe_collected_cents": invoice_evidence.collected_cents,
        "stripe_environment": _stripe_environment(invoice, event),
        "custody_user_id": owner_user_id,
        "custody_accrual_key": custody_key,
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
        if revenue_recorded and owner_user_id and custody_key:
            safebox.open_custody_account(conn, owner_user_id)
            owed_balance_cents = safebox.accrue_custody(
                conn,
                owner_user_id,
                business,
                amount_paid,
                custody_key,
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
    if _expected_stripe_environment() == "live" and not _is_live_takyon_app_object(
        invoice, invoice=True
    ):
        return {"recorded": False, "ignored": "non_app_invoice"}
    if _expected_stripe_environment() == "live" and (
        not _expected_live_stripe_account_id()
        or _invoice_subscription_metadata(invoice).get("takyon_stripe_account_id")
        != _expected_live_stripe_account_id()
    ):
        return {"recorded": False, "reason": "invoice_account_binding_mismatch"}
    subscription_id = _invoice_subscription_id(invoice)
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


def _lock_stripe_dispute(conn, dispute_id: str) -> tuple[str, bool, int] | None:
    conn.execute(
        "select pg_advisory_xact_lock(hashtextextended(%s, 0))",
        (f"takyon-stripe-dispute:{dispute_id}",),
    )
    row = conn.execute(
        "select status, terminal, provider_event_created "
        "from stripe_dispute_states where stripe_dispute_id = %s",
        (dispute_id,),
    ).fetchone()
    if row is None:
        return None
    return str(row[0]), bool(row[1]), int(row[2] or 0)


def _write_stripe_dispute_state(
    conn,
    dispute_id: str,
    *,
    status: str,
    terminal: bool,
    event: dict,
) -> None:
    conn.execute(
        "insert into stripe_dispute_states "
        "(stripe_dispute_id, status, terminal, provider_event_id, provider_event_created) "
        "values (%s, %s, %s, %s, %s) "
        "on conflict (stripe_dispute_id) do update set "
        "status = excluded.status, terminal = stripe_dispute_states.terminal or excluded.terminal, "
        "provider_event_id = excluded.provider_event_id, "
        "provider_event_created = greatest(stripe_dispute_states.provider_event_created, "
        "excluded.provider_event_created), updated_at = now()",
        (
            dispute_id,
            str(status or "unknown"),
            bool(terminal),
            str(event.get("id") or "") or None,
            max(0, int(event.get("created") or 0)),
        ),
    )


def _process_charge_reversal(conn, event: dict, obj: dict) -> dict:
    """Revoke paid access and record a reversal on a refund (`charge.refunded`) or chargeback
    (`charge.dispute.created`). Resolve the original payment back to its business via the stored
    checkout session (by payment_intent, else by customer), flip its stripe-sourced entitlement(s)
    to `cancelled` so the sub-user loses paid access, and append a `reversal` revenue row (stored
    as a positive amount but netted OUT of revenue totals by `get_revenue_summary`).

    Owner custody is clawed back by the exact pro-rata owner-net delta. If the owner has already
    withdrawn those funds, the custody rail records a durable shortfall and blocks further payout
    until later accruals recover it."""
    event_type = str(event.get("type") or "")
    object_metadata = _stripe_metadata(obj)
    if (
        _expected_stripe_environment() == "live"
        and object_metadata.get("purpose")
        and object_metadata.get("source") != "takyon_app"
    ):
        return {"recorded": False, "ignored": "non_app_reversal", "type": event_type}
    is_dispute = event_type in _DISPUTE_WITHDRAWAL_EVENT_TYPES
    object_id = str(obj.get("id") or "")
    if not object_id:
        return {"recorded": False, "reason": "missing_object_id", "type": event_type}
    if is_dispute:
        dispute_state = _lock_stripe_dispute(conn, object_id)
        if dispute_state is not None and dispute_state[1]:
            return {
                "recorded": True,
                "type": event_type,
                "dispute_status": dispute_state[0],
                "terminal_dispute": True,
                "amount_reversed_cents": 0,
                "reversal_recorded": False,
            }
    payment_intent_id = _stripe_object_id(obj.get("payment_intent"))
    charge_id = _stripe_object_id(obj.get("charge")) if is_dispute else object_id
    customer_id = _stripe_object_id(obj.get("customer"))
    invoice_id = _stripe_object_id(obj.get("invoice"))
    amount = int((obj.get("amount") if is_dispute else obj.get("amount_refunded")) or 0)
    currency = obj.get("currency")
    provider_event_id = str(event.get("id") or "") or None
    reversal_event_created = int(event.get("created") or 0)
    occurred = _epoch_to_dt(event.get("created"))
    if _expected_stripe_environment() == "live" and reversal_event_created <= 0:
        return {"recorded": False, "reason": "reversal_timestamp_missing", "type": event_type}

    original = None
    if payment_intent_id or charge_id:
        original = conn.execute(
            "select id, business_slug, stripe_checkout_session_id, stripe_customer_id, "
            "customer_email, amount_paid_cents, currency, metadata "
            "from app_revenue_events where revenue_type in ('checkout', 'subscription_renewal') "
            "and ((%s <> '' and coalesce(metadata->'stripe_payment_intent_ids', '[]'::jsonb) ? %s) "
            "or (%s <> '' and coalesce(metadata->'stripe_charge_ids', '[]'::jsonb) ? %s)) "
            "order by occurred_at desc limit 1",
            (payment_intent_id, payment_intent_id, charge_id, charge_id),
        ).fetchone()
    if original is None and invoice_id:
        original = conn.execute(
            "select id, business_slug, stripe_checkout_session_id, stripe_customer_id, "
            "customer_email, amount_paid_cents, currency, metadata "
            "from app_revenue_events where "
            "((stripe_object_type = 'invoice' and stripe_object_id = %s "
            "and revenue_type = 'subscription_renewal') "
            "or (revenue_type = 'checkout' and metadata->>'stripe_invoice_id' = %s)) "
            "order by occurred_at desc limit 1",
            (invoice_id, invoice_id),
        ).fetchone()
    session_row = None
    if original is None and payment_intent_id:
        session_row = conn.execute(
            "select business_slug, stripe_subscription_id, stripe_customer_id, customer_email, "
            "stripe_checkout_session_id from app_checkout_sessions "
            "where stripe_payment_intent_id = %s limit 1",
            (payment_intent_id,),
        ).fetchone()
    if original is None and session_row is not None:
        original = conn.execute(
            "select id, business_slug, stripe_checkout_session_id, stripe_customer_id, "
            "customer_email, amount_paid_cents, currency, metadata "
            "from app_revenue_events where business_slug = %s "
            "and stripe_checkout_session_id = %s and revenue_type = 'checkout' limit 1",
            (session_row[0], session_row[4]),
        ).fetchone()
    if original is None:
        return {"recorded": False, "reason": "unknown_payment", "type": event_type}
    original_revenue_id = str(original[0])
    business = str(original[1])
    session_id = None if original[2] is None else str(original[2])
    resolved_customer = customer_id or (None if original[3] is None else str(original[3]))
    customer_email = None if original[4] is None else str(original[4])
    original_gross_cents = int(original[5] or 0)
    original_currency = str(original[6] or "").lower()
    original_metadata = original[7] if isinstance(original[7], dict) else {}
    if _expected_stripe_environment() == "live" and (
        not _expected_live_stripe_account_id()
        or original_metadata.get("takyon_stripe_account_id")
        != _expected_live_stripe_account_id()
    ):
        return {
            "recorded": False,
            "reason": "reversal_account_binding_mismatch",
            "type": event_type,
        }
    pricing_split = (
        original_metadata.get("pricing_split")
        if isinstance(original_metadata.get("pricing_split"), dict)
        else {}
    )
    custody_user_id = str(original_metadata.get("custody_user_id") or "").strip()
    if not custody_user_id:
        if _expected_stripe_environment() == "live":
            return {
                "recorded": False,
                "reason": "reversal_recipient_missing",
                "type": event_type,
            }
        custody_user_id = _resolve_owner(conn, business)
    subscription_id = None
    if session_row is not None and session_row[1] is not None:
        subscription_id = str(session_row[1])
    if not subscription_id:
        subscription_id = str(original_metadata.get("stripe_subscription_id") or "").strip() or None
    if (
        original_gross_cents <= 0
        or original_currency != "usd"
        or str(currency or "").lower() != original_currency
    ):
        return {"recorded": False, "reason": "reversal_economics_mismatch", "type": event_type}
    try:
        original_owner_net_cents = int(pricing_split["owner_net_cents"])
    except (KeyError, TypeError, ValueError):
        return {"recorded": False, "reason": "reversal_economics_missing", "type": event_type}
    if original_owner_net_cents < 0 or original_owner_net_cents > original_gross_cents:
        return {"recorded": False, "reason": "reversal_economics_mismatch", "type": event_type}
    allocation_metadata = original_metadata.get("stripe_payment_allocations_cents")
    allocation_metadata = allocation_metadata if isinstance(allocation_metadata, dict) else {}
    payment_intent_allocations = allocation_metadata.get("payment_intent")
    payment_intent_allocations = (
        payment_intent_allocations if isinstance(payment_intent_allocations, dict) else {}
    )
    charge_allocations = allocation_metadata.get("charge")
    charge_allocations = charge_allocations if isinstance(charge_allocations, dict) else {}
    allocation_candidates: list[int] = []
    for allocation_value in (
        payment_intent_allocations.get(payment_intent_id) if payment_intent_id else None,
        charge_allocations.get(charge_id) if charge_id else None,
    ):
        try:
            normalized_allocation = int(allocation_value)
        except (TypeError, ValueError):
            continue
        if normalized_allocation > 0:
            allocation_candidates.append(normalized_allocation)
    payment_allocation_cents = max(allocation_candidates, default=0)
    provider_charge_gross_cents = int(
        obj.get("_takyon_charge_gross_cents")
        or (obj.get("amount") if not is_dispute else 0)
        or original_gross_cents
    )
    if _expected_stripe_environment() == "live" and (
        payment_allocation_cents <= 0
        or provider_charge_gross_cents < payment_allocation_cents
    ):
        return {
            "recorded": False,
            "reason": "reversal_payment_allocation_mismatch",
            "type": event_type,
        }
    charge_gross_cents = min(
        original_gross_cents,
        max(0, payment_allocation_cents or provider_charge_gross_cents),
    )
    if charge_gross_cents <= 0:
        return {"recorded": False, "reason": "reversal_economics_mismatch", "type": event_type}

    conn.execute(
        "select pg_advisory_xact_lock(hashtextextended(%s, 0))",
        (f"takyon-stripe-reversal:{original_revenue_id}",),
    )
    prior = conn.execute(
        "select coalesce(sum(case when revenue_type = 'reversal' then amount_paid_cents "
        "else -amount_paid_cents end), 0), "
        "coalesce(sum(case when revenue_type = 'reversal' then amount_paid_cents "
        "else -amount_paid_cents end) filter "
        "(where metadata->>'stripe_charge_id' = %s), 0), "
        "coalesce(sum(case when revenue_type = 'reversal' then amount_paid_cents "
        "else -amount_paid_cents end) filter "
        "(where metadata->>'stripe_dispute_id' = %s), 0), "
        "coalesce(sum(amount_paid_cents) filter (where revenue_type = 'reversal' "
        "and metadata->>'stripe_charge_id' = %s "
        "and coalesce(metadata->>'stripe_dispute_id', '') = ''), 0) "
        "from app_revenue_events "
        "where revenue_type in ('reversal', 'reversal_release') "
        "and metadata->>'original_revenue_id' = %s "
        "and coalesce(metadata->>'stripe_environment', 'test') = %s",
        (
            charge_id,
            object_id,
            charge_id,
            original_revenue_id,
            _stripe_environment(obj, event),
        ),
    ).fetchone()
    prior_reversed_cents = min(
        original_gross_cents, max(0, int((prior or (0, 0, 0, 0))[0] or 0))
    )
    prior_charge_reversed_cents = min(
        charge_gross_cents, max(0, int((prior or (0, 0, 0, 0))[1] or 0))
    )
    prior_dispute_reversed_cents = min(
        charge_gross_cents, max(0, int((prior or (0, 0, 0, 0))[2] or 0))
    )
    prior_refund_reversed_cents = min(
        charge_gross_cents, max(0, int((prior or (0, 0, 0, 0))[3] or 0))
    )
    remaining_reversible_cents = max(0, original_gross_cents - prior_reversed_cents)
    remaining_charge_cents = max(0, charge_gross_cents - prior_charge_reversed_cents)
    refund_cumulative_claim_cents = 0
    refund_unapplied_cents = 0
    if is_dispute:
        requested_delta_cents = min(
            remaining_charge_cents,
            max(0, max(0, amount) - prior_dispute_reversed_cents),
        )
    else:
        refund_cumulative_claim_cents = min(charge_gross_cents, max(0, amount))
        requested_delta_cents = max(
            0, refund_cumulative_claim_cents - prior_refund_reversed_cents
        )
        active_dispute_reversal_cents = max(
            0, prior_charge_reversed_cents - prior_refund_reversed_cents
        )
    amount = min(remaining_reversible_cents, remaining_charge_cents, requested_delta_cents)
    if not is_dispute and active_dispute_reversal_cents > 0:
        refund_unapplied_cents = max(0, requested_delta_cents - amount)
    cumulative_reversed_cents = prior_reversed_cents + amount
    prior_owner_clawback_cents = (
        original_owner_net_cents * prior_reversed_cents
    ) // original_gross_cents
    cumulative_owner_clawback_cents = (
        original_owner_net_cents * cumulative_reversed_cents
    ) // original_gross_cents
    owner_clawback_cents = max(
        0, cumulative_owner_clawback_cents - prior_owner_clawback_cents
    )

    revoke_meta = {
        "reversal": event_type,
        "reversal_object_id": object_id,
        "stripe_environment": _stripe_environment(obj, event),
        "payment_revoked": True,
        "payment_revoked_event_id": provider_event_id,
        "payment_revoked_event_created": reversal_event_created,
    }
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

    clawback = {
        "applied_cents": 0,
        "shortfall_cents": 0,
        "owed_balance_cents": 0,
        "replayed": False,
    }
    clawback_idempotency_key = None
    if owner_clawback_cents > 0:
        safebox.open_custody_account(conn, custody_user_id)
        clawback_subject = provider_event_id or (
            f"{event_type}:{object_id}:{prior_reversed_cents + amount}"
        )
        clawback_idempotency_key = f"app_reversal:{business}:{clawback_subject}"
        clawback = safebox.clawback_custody(
            conn,
            custody_user_id,
            business,
            owner_clawback_cents,
            clawback_idempotency_key,
            stripe_ref=object_id,
            metadata={
                "stripe_environment": _stripe_environment(obj, event),
                "reversal": event_type,
                "reversal_object_id": object_id,
                "original_revenue_id": original_revenue_id,
                "gross_reversal_delta_cents": amount,
            },
        )

    reversal_metadata = {
        "stripe_object": "dispute" if is_dispute else "charge",
        "reversal": event_type,
        "stripe_charge_id": charge_id,
        "stripe_dispute_id": object_id if is_dispute else None,
        "original_payment_intent": payment_intent_id,
        "original_revenue_id": original_revenue_id,
        "original_gross_cents": original_gross_cents,
        "stripe_charge_gross_cents": charge_gross_cents,
        "stripe_payment_allocation_cents": payment_allocation_cents,
        "original_owner_net_cents": original_owner_net_cents,
        "custody_user_id": custody_user_id,
        "custody_clawback_idempotency_key": clawback_idempotency_key,
        "owner_clawback_delta_cents": owner_clawback_cents,
        "stripe_subscription_id": subscription_id,
        "custody_clawback_applied_cents": int(clawback["applied_cents"]),
        "custody_clawback_shortfall_cents": int(clawback["shortfall_cents"]),
        "custody_clawback_pending": int(clawback["shortfall_cents"]) > 0,
        "stripe_environment": _stripe_environment(obj, event),
        "takyon_stripe_account_id": original_metadata.get(
            "takyon_stripe_account_id"
        ),
    }
    if not is_dispute:
        reversal_metadata.update(
            {
                "prior_refunded_cents": prior_reversed_cents,
                "prior_charge_refunded_cents": prior_refund_reversed_cents,
                "cumulative_charge_refunded_cents": (
                    prior_refund_reversed_cents + amount
                ),
                "cumulative_refunded_cents": cumulative_reversed_cents,
                "stripe_refund_cumulative_claim_cents": refund_cumulative_claim_cents,
                "stripe_refund_unapplied_cents": refund_unapplied_cents,
            }
        )
    reversal_recorded = False
    if amount > 0 or refund_unapplied_cents > 0:
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
    if is_dispute:
        _write_stripe_dispute_state(
            conn,
            object_id,
            status=str(obj.get("status") or "funds_withdrawn"),
            terminal=False,
            event=event,
        )

    return {
        "recorded": True,
        "type": event_type,
        "business_slug": business,
        "access_revoked": revoked,
        "reversal_recorded": reversal_recorded,
        "amount_reversed_cents": abs(amount),
        "custody_clawback_applied_cents": int(clawback["applied_cents"]),
        "custody_clawback_shortfall_cents": int(clawback["shortfall_cents"]),
        "custody_clawback_pending": int(clawback["shortfall_cents"]) > 0,
        "refund_unapplied_cents": refund_unapplied_cents,
    }


def _process_dispute_closed(conn, event: dict, obj: dict) -> dict:
    """Finalize a dispute using its current Stripe status.

    A lost dispute leaves the created-time reversal in place. A won dispute releases exactly that
    dispute's revenue and custody clawback, and only restores access when no other refund/dispute
    reversal remains for the payment and Stripe currently reports the subscription as entitling.
    """
    event_type = str(event.get("type") or "charge.dispute.closed")
    object_metadata = _stripe_metadata(obj)
    if (
        _expected_stripe_environment() == "live"
        and object_metadata.get("purpose")
        and object_metadata.get("source") != "takyon_app"
    ):
        return {"recorded": False, "ignored": "non_app_dispute", "type": event_type}
    dispute_id = str(obj.get("id") or "")
    status = str(obj.get("status") or "").strip().lower()
    if not dispute_id:
        return {"recorded": False, "reason": "missing_object_id", "type": event_type}
    dispute_state = _lock_stripe_dispute(conn, dispute_id)
    release_status = status in {"won", "warning_closed"}
    terminal_release_override = (
        dispute_state is not None
        and dispute_state[1]
        and release_status
        and dispute_state[0] not in {"won", "warning_closed"}
    )
    if dispute_state is not None and dispute_state[1] and not terminal_release_override:
        return {
            "recorded": True,
            "type": event_type,
            "dispute_status": dispute_state[0],
            "terminal_dispute": True,
            "already_released": dispute_state[0] in {"won", "warning_closed"},
        }

    originals = conn.execute(
        "select id, business_slug, stripe_checkout_session_id, stripe_customer_id, "
        "customer_email, amount_paid_cents, currency, metadata "
        "from app_revenue_events where revenue_type = 'reversal' "
        "and stripe_object_type = 'dispute' and stripe_object_id = %s "
        "and metadata->>'reversal' in "
        "('charge.dispute.created', 'charge.dispute.funds_withdrawn') "
        "order by occurred_at, created_at, id",
        (dispute_id,),
    ).fetchall()

    if not originals:
        if release_status:
            # A stale created event delivered after Stripe already closed the dispute as won must
            # not create a new clawback. There is nothing local to release.
            _write_stripe_dispute_state(
                conn,
                dispute_id,
                status=status,
                terminal=True,
                event=event,
            )
            return {
                "recorded": True,
                "type": event_type,
                "dispute_status": status,
                "already_released": True,
            }
        if status in {"lost"}:
            synthetic = dict(event)
            synthetic["type"] = "charge.dispute.created"
            result = _process_charge_reversal(conn, synthetic, obj)
            if not bool(result.get("recorded")):
                return {**result, "type": event_type, "dispute_status": status}
            _write_stripe_dispute_state(
                conn,
                dispute_id,
                status=status,
                terminal=True,
                event=event,
            )
            return {**result, "type": event_type, "dispute_status": status}
        return {"recorded": False, "reason": "unknown_dispute", "type": event_type}
    if not release_status:
        if status == "lost":
            _write_stripe_dispute_state(
                conn,
                dispute_id,
                status=status,
                terminal=True,
                event=event,
            )
        return {
            "recorded": True,
            "type": event_type,
            "dispute_status": status,
            "reversal_retained": True,
        }

    first = originals[0]
    original_reversal_ids = [str(row[0]) for row in originals]
    business = str(first[1])
    session_id = None if first[2] is None else str(first[2])
    customer_id = None if first[3] is None else str(first[3])
    customer_email = None if first[4] is None else str(first[4])
    released_gross_cents = sum(int(row[5] or 0) for row in originals)
    currency = str(first[6] or "").lower()
    original_metadata = first[7] if isinstance(first[7], dict) else {}
    original_revenue_id = str(original_metadata.get("original_revenue_id") or "")
    custody_user_id = str(original_metadata.get("custody_user_id") or "").strip()
    stripe_charge_id = str(original_metadata.get("stripe_charge_id") or "").strip()
    rows_valid = all(
        str(row[1]) == business
        and str(row[6] or "").lower() == currency
        and isinstance(row[7], dict)
        and str(row[7].get("original_revenue_id") or "") == original_revenue_id
        and str(row[7].get("custody_user_id") or "").strip() == custody_user_id
        and str(row[7].get("stripe_charge_id") or "").strip() == stripe_charge_id
        for row in originals
    )
    live_binding_valid = all(
        isinstance(row[7], dict)
        and row[7].get("takyon_stripe_account_id") == _expected_live_stripe_account_id()
        for row in originals
    )
    if _expected_stripe_environment() == "live" and (
        not _expected_live_stripe_account_id() or not live_binding_valid
    ):
        return {"recorded": False, "reason": "dispute_release_invalid", "type": event_type}
    if (
        not rows_valid
        or not original_revenue_id
        or not stripe_charge_id
        or released_gross_cents <= 0
        or currency != "usd"
    ):
        return {"recorded": False, "reason": "dispute_release_invalid", "type": event_type}

    charge_allocation_cents = int(
        original_metadata.get("stripe_charge_gross_cents") or released_gross_cents
    )
    provider_charge_gross = obj.get("_takyon_charge_gross_cents")
    provider_refund_claim = obj.get("_takyon_charge_amount_refunded_cents")
    provider_refund_proof_valid = (
        isinstance(provider_charge_gross, int)
        and not isinstance(provider_charge_gross, bool)
        and isinstance(provider_refund_claim, int)
        and not isinstance(provider_refund_claim, bool)
        and provider_charge_gross >= charge_allocation_cents > 0
        and 0 <= provider_refund_claim <= provider_charge_gross
    )
    if _expected_stripe_environment() == "live" and not provider_refund_proof_valid:
        return {"recorded": False, "reason": "dispute_release_invalid", "type": event_type}
    provider_refund_claim_cents = (
        min(charge_allocation_cents, provider_refund_claim)
        if provider_refund_proof_valid
        else 0
    )

    # Serialize the refund-claim read, dispute release, synthetic refund reconciliation, and access
    # decision with ordinary charge.refunded processing. Without the shared lock, a refund could
    # commit after this read and a won dispute could transiently reactivate refunded access.
    conn.execute(
        "select pg_advisory_xact_lock(hashtextextended(%s, 0))",
        (f"takyon-stripe-reversal:{original_revenue_id}",),
    )
    refund_claim_row = conn.execute(
        "select coalesce(max(case when "
        "coalesce(metadata->>'stripe_refund_cumulative_claim_cents', "
        "metadata->>'cumulative_charge_refunded_cents', '') ~ '^[0-9]+$' then "
        "coalesce(metadata->>'stripe_refund_cumulative_claim_cents', "
        "metadata->>'cumulative_charge_refunded_cents')::bigint else 0 end), 0) "
        "from app_revenue_events where revenue_type = 'reversal' "
        "and metadata->>'reversal' = 'charge.refunded' "
        "and metadata->>'original_revenue_id' = %s "
        "and metadata->>'stripe_charge_id' = %s "
        "and coalesce(metadata->>'stripe_environment', 'test') = %s",
        (
            original_revenue_id,
            stripe_charge_id,
            _stripe_environment(obj, event),
        ),
    ).fetchone()
    refund_cumulative_claim_cents = max(
        provider_refund_claim_cents,
        min(
            charge_allocation_cents,
            max(0, int((refund_claim_row or (0,))[0] or 0)),
        ),
    )

    conn.execute(
        "select pg_advisory_xact_lock(hashtextextended(%s, 0))",
        (f"takyon-stripe-dispute-release:{dispute_id}",),
    )
    existing_release = conn.execute(
        "select 1 from app_revenue_events where revenue_type = 'reversal_release' "
        "and metadata->>'stripe_dispute_id' = %s limit 1",
        (dispute_id,),
    ).fetchone()
    if existing_release is not None:
        _write_stripe_dispute_state(
            conn,
            dispute_id,
            status=status,
            terminal=True,
            event=event,
        )
        return {
            "recorded": True,
            "type": event_type,
            "dispute_status": status,
            "already_released": True,
        }

    custody_release_credited_cents = 0
    release_keys: list[str] = []
    for row in originals:
        reversal_id = str(row[0])
        row_metadata = row[7] if isinstance(row[7], dict) else {}
        clawback_key = str(
            row_metadata.get("custody_clawback_idempotency_key") or ""
        ).strip()
        owner_clawback_cents = int(row_metadata.get("owner_clawback_delta_cents") or 0)
        release_key = f"app_dispute_release:{business}:{dispute_id}:{reversal_id}"
        release_keys.append(release_key)
        if owner_clawback_cents <= 0:
            continue
        if not custody_user_id or not clawback_key:
            return {
                "recorded": False,
                "reason": "dispute_release_invalid",
                "type": event_type,
            }
        custody_release = safebox.release_custody_clawback(
            conn,
            custody_user_id,
            business,
            clawback_key,
            release_key,
            stripe_ref=dispute_id,
            metadata={
                "stripe_environment": _stripe_environment(obj, event),
                "dispute_id": dispute_id,
                "dispute_status": status,
                "original_reversal_id": reversal_id,
            },
        )
        custody_release_credited_cents += int(custody_release["credited_cents"])

    release_metadata = {
        "stripe_object": "dispute",
        "stripe_dispute_id": dispute_id,
        "stripe_charge_id": stripe_charge_id,
        "dispute_status": status,
        "original_reversal_id": original_reversal_ids[0],
        "original_reversal_ids": original_reversal_ids,
        "original_revenue_id": original_revenue_id,
        "custody_user_id": custody_user_id,
        "custody_release_idempotency_keys": release_keys,
        "custody_release_credited_cents": custody_release_credited_cents,
        "stripe_provider_charge_gross_cents": (
            provider_charge_gross if provider_refund_proof_valid else None
        ),
        "stripe_provider_refund_claim_cents": provider_refund_claim_cents,
        "stripe_refund_cumulative_claim_cents": refund_cumulative_claim_cents,
        "stripe_environment": _stripe_environment(obj, event),
        "takyon_stripe_account_id": original_metadata.get(
            "takyon_stripe_account_id"
        ),
    }
    release_recorded = _insert_revenue_event(
        conn,
        business_slug=business,
        provider_event_id=str(event.get("id") or "") or None,
        stripe_object_type="dispute",
        stripe_object_id=dispute_id,
        stripe_checkout_session_id=session_id,
        stripe_customer_id=customer_id,
        revenue_type="reversal_release",
        status="dispute_won",
        currency=currency,
        amount_paid_cents=released_gross_cents,
        customer_email=customer_email,
        occurred_at=_epoch_to_dt(event.get("created")),
        metadata=release_metadata,
    )

    refund_reconciled = None
    if refund_cumulative_claim_cents > 0:
        synthetic_event_id = f"{str(event.get('id') or dispute_id)}:refund-overlap"
        refund_reconciled = _process_charge_reversal(
            conn,
            {
                "id": synthetic_event_id,
                "type": "charge.refunded",
                "created": int(event.get("created") or 0),
                "livemode": event.get("livemode"),
            },
            {
                "id": stripe_charge_id,
                "payment_intent": original_metadata.get("original_payment_intent"),
                "customer": customer_id,
                "amount": int(
                    original_metadata.get("stripe_charge_gross_cents")
                    or refund_cumulative_claim_cents
                ),
                "amount_refunded": refund_cumulative_claim_cents,
                "currency": currency,
                "_takyon_charge_gross_cents": (
                    provider_charge_gross
                    if provider_refund_proof_valid
                    else charge_allocation_cents
                ),
                "metadata": {
                    "source": "takyon_app",
                    "takyon_stripe_account_id": original_metadata.get(
                        "takyon_stripe_account_id"
                    ),
                },
            },
        )
        if not bool(refund_reconciled.get("recorded")):
            return {
                "recorded": False,
                "reason": "dispute_release_invalid",
                "type": event_type,
            }

    remaining = conn.execute(
        "select coalesce(sum(case when revenue_type = 'reversal' then amount_paid_cents "
        "when revenue_type = 'reversal_release' then -amount_paid_cents else 0 end), 0) "
        "from app_revenue_events where metadata->>'original_revenue_id' = %s "
        "and revenue_type in ('reversal', 'reversal_release') "
        "and coalesce(metadata->>'stripe_environment', 'test') = %s",
        (original_revenue_id, _stripe_environment(obj, event)),
    ).fetchone()
    remaining_reversed_cents = max(0, int((remaining or (0,))[0] or 0))
    access_updated = 0
    subscription = obj.get("_takyon_subscription")
    expected_subscription_id = str(
        original_metadata.get("stripe_subscription_id") or ""
    ).strip()
    if remaining_reversed_cents == 0 and isinstance(subscription, dict):
        subscription_id = _stripe_object_id(subscription.get("id"))
        live_binding_valid = (
            _expected_stripe_environment() != "live"
            or (
                _is_live_takyon_app_object(subscription)
                and _stripe_metadata(subscription).get("takyon_stripe_account_id")
                == _expected_live_stripe_account_id()
            )
        )
        if (
            subscription_id
            and subscription_id == expected_subscription_id
            and live_binding_valid
        ):
            access_updated = len(
                app_entitlements.set_subscription_status(
                    conn,
                    subscription_id,
                    status=_subscription_entitlement_status(
                        str(subscription.get("status") or "")
                    ),
                    stripe_customer_id=_stripe_object_id(subscription.get("customer")),
                    current_period_end=_subscription_period_end(subscription),
                    metadata={
                        "stripe_subscription_status": subscription.get("status"),
                        "stripe_environment": _stripe_environment(subscription, event),
                        "payment_settled": True,
                        "payment_revoked": False,
                        "payment_reactivation_blocked": False,
                        "stripe_payment_event_id": str(event.get("id") or ""),
                        "stripe_payment_event_created": int(event.get("created") or 0),
                        "dispute_won": dispute_id,
                    },
                )
            )

    _write_stripe_dispute_state(
        conn,
        dispute_id,
        status=status,
        terminal=True,
        event=event,
    )

    return {
        "recorded": True,
        "type": event_type,
        "dispute_status": status,
        "business_slug": business,
        "release_recorded": release_recorded,
        "amount_released_cents": released_gross_cents,
        "custody_release_credited_cents": custody_release_credited_cents,
        "refund_reconciled": refund_reconciled,
        "remaining_reversed_cents": remaining_reversed_cents,
        "access_updated": access_updated,
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
        "where business_slug = %s "
        "and coalesce(metadata->>'stripe_environment', 'test') = %s "
        "order by occurred_at desc, created_at desc limit %s",
        (business_slug, _expected_stripe_environment(), int(limit)),
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
        "where business_slug = %s "
        "and coalesce(metadata->>'stripe_environment', 'test') = %s",
        (business_slug, _expected_stripe_environment()),
    ).fetchone()
    return {
        "business_slug": business_slug,
        "amount_paid_cents": int(row[0]),
        "events": int(row[1]),
    }
