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

from plugins.takyon import app_entitlements, custody

_SUBSCRIPTION_EVENT_TYPES = (
    "customer.subscription.created",
    "customer.subscription.updated",
    "customer.subscription.deleted",
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
            "select id, business_slug, plan_key, customer_email from app_checkout_intents "
            "where id = %s",
            (intent_id,),
        ).fetchone()
        if row is not None:
            return row
    client_ref = session.get("client_reference_id")
    if client_ref:
        return conn.execute(
            "select id, business_slug, plan_key, customer_email from app_checkout_intents "
            "where client_reference_id = %s",
            (client_ref,),
        ).fetchone()
    return None


def _process_checkout_completed(conn, event: dict, session: dict) -> dict:
    """Port of core.py:6844 + the net-new owner accrual. Runs inside the caller's transaction
    (does not open its own); the entitlement grant and custody accrual it delegates to open
    savepoints under that transaction."""
    metadata = session.get("metadata") if isinstance(session.get("metadata"), dict) else {}
    intent = _find_intent_row(conn, metadata, session)
    if intent is None:
        return {"recorded": False, "reason": "missing_checkout_intent"}
    intent_id, business, plan_key, intent_email = (
        str(intent[0]),
        intent[1],
        intent[2],
        intent[3],
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
    occurred = _epoch_to_dt(event.get("created"))
    payment_status = session.get("payment_status")
    currency = session.get("currency")
    amount_total = int(session.get("amount_total") or 0)

    conn.execute(
        "insert into app_checkout_sessions "
        "(business_slug, checkout_intent_id, plan_key, stripe_checkout_session_id, "
        " stripe_customer_id, stripe_payment_intent_id, stripe_subscription_id, "
        " stripe_invoice_id, mode, payment_status, status, currency, amount_subtotal_cents, "
        " amount_total_cents, client_reference_id, customer_email, raw_event_id, metadata, "
        " completed_at) "
        "values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, "
        " coalesce(%s, now())) "
        "on conflict (stripe_checkout_session_id) do update set "
        " payment_status = excluded.payment_status, status = excluded.status, "
        " stripe_subscription_id = excluded.stripe_subscription_id, "
        " stripe_invoice_id = excluded.stripe_invoice_id, "
        " completed_at = excluded.completed_at, updated_at = now()",
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
            event.get("id"),
            _json_dumps(metadata),
            occurred,
        ),
    )
    conn.execute(
        "update app_checkout_intents set status = 'completed', "
        "completed_at = coalesce(%s, now()), updated_at = now() where id = %s",
        (occurred, intent_id),
    )

    app_user_id = None
    if customer_email and (subscription_id or payment_status == "paid"):
        entitlement, _tier = app_entitlements.grant_entitlement(
            conn,
            business,
            email=customer_email,
            tier="paid",
            status="active",
            source="stripe",
            stripe_customer_id=customer_id,
            stripe_subscription_id=subscription_id,
            stripe_checkout_session_id=session_id,
            plan_key=plan_key,
            metadata={"raw_event_id": event.get("id")},
        )
        app_user_id = entitlement.app_user_id

    revenue_recorded = False
    accrued_to_owner = False
    owner_user_id = None
    owed_balance_cents = None
    if currency and payment_status == "paid":
        inserted = conn.execute(
            "insert into app_revenue_events "
            "(business_slug, provider_event_id, stripe_object_type, stripe_object_id, "
            " stripe_checkout_session_id, stripe_customer_id, revenue_type, status, currency, "
            " amount_paid_cents, customer_email, occurred_at, metadata) "
            "values (%s, %s, 'checkout.session', %s, %s, %s, 'checkout', %s, %s, %s, %s, "
            " coalesce(%s, now()), %s::jsonb) "
            "on conflict (business_slug, provider_event_id, stripe_object_id) do nothing "
            "returning id",
            (
                business,
                event.get("id"),
                session_id,
                session_id,
                customer_id,
                payment_status or "paid",
                currency or "usd",
                amount_total,
                customer_email,
                occurred,
                _json_dumps(metadata),
            ),
        ).fetchone()
        revenue_recorded = inserted is not None
        # ADD (flow B): accrue the gross to the OWNER's custody minus the app fee. Keyed
        # deterministically on the revenue identity, so custody.accrue dedups even if the
        # revenue row pre-existed from a partial run (belt-and-suspenders with the webhook gate).
        if amount_total > 0:
            owner_user_id = _resolve_owner(conn, business)
            custody.open_custody_account(conn, owner_user_id)
            owed_balance_cents = custody.accrue(
                conn,
                owner_user_id,
                business,
                amount_total,
                f"app_revenue:{business}:{event.get('id')}:{session_id}",
                stripe_ref=session_id,
            )
            accrued_to_owner = True

    return {
        "recorded": True,
        "business_slug": business,
        "app_user_id": app_user_id,
        "revenue_recorded": revenue_recorded,
        "accrued_to_owner": accrued_to_owner,
        "owner_user_id": owner_user_id,
        "owner_owed_balance_cents": owed_balance_cents,
    }


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
    """Total recorded revenue for a business (mirrors core.py:3710). Read."""
    row = conn.execute(
        "select coalesce(sum(amount_paid_cents), 0), count(*) from app_revenue_events "
        "where business_slug = %s",
        (business_slug,),
    ).fetchone()
    return {
        "business_slug": business_slug,
        "amount_paid_cents": int(row[0]),
        "events": int(row[1]),
    }
