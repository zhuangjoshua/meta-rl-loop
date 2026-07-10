from __future__ import annotations

import contextlib
import uuid

import pytest
from starlette.testclient import TestClient

from plugins.takyon import app_entitlements, app_identity, app_payments, safebox_app


def _invoice_payment(payment_id: str, payment_intent_id: str) -> dict:
    return {
        "id": payment_id,
        "object": "invoice_payment",
        "payment": {
            "type": "payment_intent",
            "payment_intent": payment_intent_id,
        },
    }


def test_stripe_invoice_payment_fetch_paginates_every_page(monkeypatch):
    calls: list[tuple[str, dict, str]] = []

    def stripe_request(path, params=None, *, method="POST", idempotency_key=None):
        calls.append((path, dict(params or {}), method))
        if path == "invoices/in_basil_pages":
            return {"id": "in_basil_pages", "object": "invoice", "livemode": True}
        if path == "invoice_payments" and "starting_after" not in (params or {}):
            return {
                "object": "list",
                "has_more": True,
                "data": [
                    _invoice_payment("inpay_1", "pi_1"),
                    "malformed-row",
                    _invoice_payment("inpay_2", "pi_2"),
                ],
            }
        if path == "invoice_payments" and params.get("starting_after") == "inpay_2":
            return {
                "object": "list",
                "has_more": False,
                "data": [_invoice_payment("inpay_3", "pi_3")],
            }
        pytest.fail(f"unexpected Stripe request: {path} {params}")

    monkeypatch.setattr(safebox_app.safebox, "stripe_request", stripe_request)

    invoice = safebox_app._stripe_invoice_with_all_payments("in_basil_pages")

    assert [row["id"] for row in invoice["payments"]["data"]] == [
        "inpay_1",
        "inpay_2",
        "inpay_3",
    ]
    assert invoice["payments"]["has_more"] is False
    assert calls == [
        ("invoices/in_basil_pages", {}, "GET"),
        ("invoice_payments", {"invoice": "in_basil_pages", "limit": 100}, "GET"),
        (
            "invoice_payments",
            {"invoice": "in_basil_pages", "limit": 100, "starting_after": "inpay_2"},
            "GET",
        ),
    ]


def test_stripe_invoice_payment_fetch_refuses_repeated_cursor(monkeypatch):
    page_calls = 0

    def stripe_request(path, params=None, *, method="POST", idempotency_key=None):
        nonlocal page_calls
        if path == "invoices/in_cursor_loop":
            return {"id": "in_cursor_loop", "object": "invoice", "livemode": True}
        assert path == "invoice_payments"
        page_calls += 1
        return {
            "object": "list",
            "has_more": True,
            "data": [_invoice_payment("inpay_repeated", f"pi_{page_calls}")],
        }

    monkeypatch.setattr(safebox_app.safebox, "stripe_request", stripe_request)

    assert safebox_app._stripe_invoice_with_all_payments("in_cursor_loop") == {}
    assert page_calls == 2


def _seed_business(conn, slug: str):
    owner_id = conn.execute(
        "insert into users (auth0_sub) values (%s) returning id",
        (f"auth0|{uuid.uuid4().hex}",),
    ).fetchone()[0]
    conn.execute(
        "insert into businesses (slug, name, owner_user_id) values (%s, %s, %s)",
        (slug, slug, owner_id),
    )
    return owner_id


def _stub_custody(monkeypatch):
    monkeypatch.setattr(app_payments.safebox, "open_custody_account", lambda *a, **k: None)
    monkeypatch.setattr(app_payments.safebox, "accrue_custody", lambda *a, **k: 0)
    monkeypatch.setattr(
        app_payments.safebox,
        "clawback_custody",
        lambda *a, **k: {
            "applied_cents": int(a[3]),
            "shortfall_cents": 0,
            "owed_balance_cents": 0,
            "replayed": False,
        },
    )


def _refund_without_invoice(payment_intent_id: str, *, event_id: str, charge_id: str) -> dict:
    return {
        "id": event_id,
        "type": "charge.refunded",
        "created": 300,
        "data": {
            "object": {
                "id": charge_id,
                "payment_intent": payment_intent_id,
                "amount": 2000,
                "amount_refunded": 2000,
                "currency": "usd",
            }
        },
    }


def test_basil_initial_checkout_mapping_resolves_charge_without_invoice(pg_conn, monkeypatch):
    _stub_custody(monkeypatch)
    slug = f"basil-checkout-{uuid.uuid4().hex[:8]}"
    _seed_business(pg_conn, slug)
    user_id = app_identity.upsert_app_user(pg_conn, slug, "initial@basil.test").id
    intent = app_payments.create_checkout_intent(
        pg_conn,
        slug,
        plan_key="pro",
        client_reference_id=f"ref-{uuid.uuid4().hex}",
        app_user_id=user_id,
        customer_email="initial@basil.test",
    )
    payment_intent_id = "pi_basil_checkout"
    checkout = app_payments.record_webhook_and_process(
        pg_conn,
        {
            "id": "evt_basil_checkout_mapping",
            "type": "checkout.session.completed",
            "created": 100,
            "data": {
                "object": {
                    "id": "cs_basil_checkout_mapping",
                    "mode": "subscription",
                    "payment_status": "paid",
                    "status": "complete",
                    "currency": "usd",
                    "amount_total": 2000,
                    "customer": "cus_basil_checkout",
                    "subscription": "sub_basil_checkout",
                    "invoice": "in_basil_checkout",
                    "customer_details": {"email": "initial@basil.test"},
                    "metadata": {"checkout_intent_id": intent.id},
                    "_takyon_invoice": {
                        "id": "in_basil_checkout",
                        "payments": {
                            "object": "list",
                            "has_more": False,
                            "data": [
                                _invoice_payment("inpay_basil_checkout", payment_intent_id)
                            ],
                        },
                    },
                }
            },
        },
    )["processed"]
    assert checkout["revenue_recorded"] is True
    assert pg_conn.execute(
        "select metadata->'stripe_payment_intent_ids' from app_revenue_events "
        "where provider_event_id = 'evt_basil_checkout_mapping'"
    ).fetchone()[0] == [payment_intent_id]

    refund = app_payments.record_webhook_and_process(
        pg_conn,
        _refund_without_invoice(
            payment_intent_id,
            event_id="evt_basil_checkout_refund_no_invoice",
            charge_id="ch_basil_checkout_no_invoice",
        ),
    )["processed"]
    assert refund["reversal_recorded"] is True
    assert app_payments.get_revenue_summary(pg_conn, slug)["amount_paid_cents"] == 0


def test_basil_renewal_mapping_resolves_charge_without_invoice(pg_conn, monkeypatch):
    _stub_custody(monkeypatch)
    slug = f"basil-renewal-{uuid.uuid4().hex[:8]}"
    _seed_business(pg_conn, slug)
    user_id = app_identity.upsert_app_user(pg_conn, slug, "renewal@basil.test").id
    app_entitlements.grant_entitlement(
        pg_conn,
        slug,
        app_user_id=user_id,
        tier="paid",
        status="active",
        source="stripe",
        stripe_customer_id="cus_basil_renewal",
        stripe_subscription_id="sub_basil_renewal",
        plan_key="pro",
    )
    payment_intent_id = "pi_basil_renewal"
    renewal = app_payments.record_webhook_and_process(
        pg_conn,
        {
            "id": "evt_basil_renewal_mapping",
            "type": "invoice.paid",
            "created": 200,
            "data": {
                "object": {
                    "id": "in_basil_renewal",
                    "billing_reason": "subscription_cycle",
                    "parent": {
                        "subscription_details": {"subscription": "sub_basil_renewal"}
                    },
                    "payments": {
                        "object": "list",
                        "has_more": False,
                        "data": [
                            _invoice_payment("inpay_basil_renewal", payment_intent_id)
                        ],
                    },
                    "customer": "cus_basil_renewal",
                    "customer_email": "renewal@basil.test",
                    "amount_paid": 2000,
                    "currency": "usd",
                }
            },
        },
    )["processed"]
    assert renewal["revenue_recorded"] is True
    assert pg_conn.execute(
        "select metadata->'stripe_payment_intent_ids' from app_revenue_events "
        "where provider_event_id = 'evt_basil_renewal_mapping'"
    ).fetchone()[0] == [payment_intent_id]

    refund = app_payments.record_webhook_and_process(
        pg_conn,
        _refund_without_invoice(
            payment_intent_id,
            event_id="evt_basil_renewal_refund_no_invoice",
            charge_id="ch_basil_renewal_no_invoice",
        ),
    )["processed"]
    assert refund["reversal_recorded"] is True
    assert app_entitlements.get_active_entitlement(pg_conn, slug, user_id) is None
    assert app_payments.get_revenue_summary(pg_conn, slug)["amount_paid_cents"] == 0


def test_basil_paid_allocation_caps_refund_and_ignores_canceled_payment(
    pg_conn, monkeypatch
):
    _stub_custody(monkeypatch)
    slug = f"basil-allocation-{uuid.uuid4().hex[:8]}"
    _seed_business(pg_conn, slug)
    user_id = app_identity.upsert_app_user(pg_conn, slug, "allocation@basil.test").id
    app_entitlements.grant_entitlement(
        pg_conn,
        slug,
        app_user_id=user_id,
        tier="paid",
        status="active",
        source="stripe",
        stripe_customer_id="cus_basil_allocation",
        stripe_subscription_id="sub_basil_allocation",
        plan_key="pro",
    )
    paid_payment_intent = "pi_basil_allocation_paid"
    canceled_payment_intent = "pi_basil_allocation_canceled"
    app_payments.record_webhook_and_process(
        pg_conn,
        {
            "id": "evt_basil_allocation_paid",
            "type": "invoice.paid",
            "created": 200,
            "data": {
                "object": {
                    "id": "in_basil_allocation",
                    "subscription": "sub_basil_allocation",
                    "billing_reason": "subscription_cycle",
                    "payments": {
                        "object": "list",
                        "has_more": False,
                        "data": [
                            {
                                "id": "inpay_basil_allocation_paid",
                                "object": "invoice_payment",
                                "invoice": "in_basil_allocation",
                                "status": "paid",
                                "amount_paid": 500,
                                "currency": "usd",
                                "livemode": False,
                                "payment": {
                                    "type": "payment_intent",
                                    "payment_intent": paid_payment_intent,
                                },
                            },
                            {
                                "id": "inpay_basil_allocation_canceled",
                                "object": "invoice_payment",
                                "invoice": "in_basil_allocation",
                                "status": "canceled",
                                "amount_paid": None,
                                "currency": "usd",
                                "livemode": False,
                                "payment": {
                                    "type": "payment_intent",
                                    "payment_intent": canceled_payment_intent,
                                },
                            },
                        ],
                    },
                    "amount_paid": 500,
                    "currency": "usd",
                }
            },
        },
    )
    metadata = pg_conn.execute(
        "select metadata from app_revenue_events "
        "where provider_event_id = 'evt_basil_allocation_paid'"
    ).fetchone()[0]
    assert metadata["stripe_payment_intent_ids"] == [paid_payment_intent]
    assert metadata["stripe_payment_allocations_cents"] == {
        "payment_intent": {paid_payment_intent: 500},
        "charge": {},
    }

    reversal = app_payments.record_webhook_and_process(
        pg_conn,
        {
            "id": "evt_basil_allocation_refund",
            "type": "charge.refunded",
            "created": 300,
            "data": {
                "object": {
                    "id": "ch_basil_allocation",
                    "payment_intent": paid_payment_intent,
                    "amount": 2000,
                    "amount_refunded": 2000,
                    "currency": "usd",
                }
            },
        },
    )["processed"]
    assert reversal["amount_reversed_cents"] == 500
    assert app_payments.get_revenue_summary(pg_conn, slug)["amount_paid_cents"] == 0


def test_live_invoice_out_of_band_amount_never_mints_revenue(pg_conn, monkeypatch):
    _stub_custody(monkeypatch)
    monkeypatch.setenv("TAKYON_STRIPE_MODE", "live")
    monkeypatch.setenv("TAKYON_STRIPE_ACCOUNT_ID", "acct_live_allocation")
    slug = f"basil-out-of-band-{uuid.uuid4().hex[:8]}"
    _seed_business(pg_conn, slug)
    app_entitlements.upsert_plan_policy(
        pg_conn, slug, "pro", tier="paid", price_cents=2000
    )
    user_id = app_identity.upsert_app_user(pg_conn, slug, "out-of-band@basil.test").id
    app_entitlements.grant_entitlement(
        pg_conn,
        slug,
        app_user_id=user_id,
        tier="paid",
        status="active",
        source="stripe",
        stripe_customer_id="cus_live_out_of_band",
        stripe_subscription_id="sub_live_out_of_band",
        plan_key="pro",
    )
    binding = {
        "source": "takyon_app",
        "business": slug,
        "plan_key": "pro",
        "takyon_stripe_account_id": "acct_live_allocation",
    }
    event = {
        "id": "evt_live_out_of_band",
        "type": "invoice.paid",
        "created": 300,
        "livemode": True,
        "data": {
            "object": {
                "id": "in_live_out_of_band",
                "object": "invoice",
                "status": "paid",
                "livemode": True,
                "billing_reason": "subscription_cycle",
                "parent": {
                    "type": "subscription_details",
                    "subscription_details": {
                        "subscription": "sub_live_out_of_band",
                        "metadata": binding,
                    },
                },
                "payments": {
                    "object": "list",
                    "has_more": False,
                    "data": [],
                },
                "customer": "cus_live_out_of_band",
                "customer_email": "out-of-band@basil.test",
                "amount_paid": 2000,
                "currency": "usd",
                "_takyon_subscription": {
                    "id": "sub_live_out_of_band",
                    "object": "subscription",
                    "status": "active",
                    "livemode": True,
                    "customer": "cus_live_out_of_band",
                    "metadata": binding,
                },
            }
        },
    }

    with pytest.raises(app_payments.RetryableWebhookEvent):
        app_payments.record_webhook_and_process(pg_conn, event)
    assert pg_conn.execute(
        "select count(*) from app_revenue_events where provider_event_id = %s",
        (event["id"],),
    ).fetchone()[0] == 0


def test_live_subscription_webhook_replaces_stale_snapshot_with_current_proof(monkeypatch):
    monkeypatch.setenv("TAKYON_HOST_ROLE", "safebox")
    monkeypatch.setenv("TAKYON_SAFEBOX_TOKEN", "shared-token")
    monkeypatch.setenv("TAKYON_STRIPE_MODE", "live")
    stale_event = {
        "id": "evt_stale_subscription",
        "type": "customer.subscription.updated",
        "livemode": True,
        "data": {
            "object": {
                "id": "sub_current_proof",
                "object": "subscription",
                "livemode": True,
                "status": "past_due",
                "cancel_at_period_end": False,
                "metadata": {"source": "takyon_app", "snapshot": "stale"},
            }
        },
    }
    current_proof = {
        "id": "sub_current_proof",
        "object": "subscription",
        "livemode": True,
        "status": "canceled",
        "cancel_at_period_end": True,
        "metadata": {"source": "takyon_app", "snapshot": "current"},
    }
    stripe_calls: list[str] = []
    captured: dict = {}

    monkeypatch.setattr(
        safebox_app.safebox,
        "verify_stripe_app_webhook",
        lambda raw_body, signature: stale_event,
    )

    def stripe_request(path, params=None, *, method="POST", idempotency_key=None):
        stripe_calls.append(path)
        assert path == "subscriptions/sub_current_proof"
        assert method == "GET"
        return dict(current_proof)

    monkeypatch.setattr(safebox_app.safebox, "stripe_request", stripe_request)

    @contextlib.contextmanager
    def fake_conn():
        yield object()

    monkeypatch.setattr(safebox_app, "_safebox_db_conn", fake_conn)

    def process(_conn, event):
        captured.update(event["data"]["object"])
        return {
            "provider_event_id": event["id"],
            "type": event["type"],
            "deduplicated": False,
            "processed": {"recorded": True},
        }

    monkeypatch.setattr(app_payments, "record_webhook_and_process", process)

    response = TestClient(safebox_app.build_safebox_app()).post(
        "/v1/stripe/app-webhook/process",
        headers={"Authorization": "Bearer shared-token"},
        json={"raw_body": "{}", "signature": "sig"},
    )

    assert response.status_code == 200, response.text
    assert stripe_calls == ["subscriptions/sub_current_proof"]
    assert captured == current_proof
    assert response.json()["processed"] == {"recorded": True}


class _AtomicWebhookConn:
    def __init__(self):
        self.in_transaction = False
        self.rolled_back = False

    @contextlib.contextmanager
    def transaction(self):
        self.in_transaction = True
        try:
            yield
        except Exception:
            self.rolled_back = True
            raise
        finally:
            self.in_transaction = False


def _live_checkout_event() -> dict:
    return {
        "id": "evt_live_checkout_atomic",
        "type": "checkout.session.completed",
        "livemode": True,
        "data": {
            "object": {
                "id": "cs_live_atomic",
                "object": "checkout.session",
                "livemode": True,
                "status": "complete",
                "payment_status": "paid",
                "subscription": "sub_live_atomic",
                "metadata": {
                    "source": "takyon_app",
                    "business": "atomic-co",
                },
            }
        },
    }


def _live_checkout_subscription() -> dict:
    return {
        "id": "sub_live_atomic",
        "object": "subscription",
        "livemode": True,
        "status": "active",
        "metadata": {
            "source": "takyon_app",
            "business": "atomic-co",
            "takyon_stripe_account_id": "acct_live_atomic",
        },
    }


def test_live_checkout_webhook_fetches_subscription_before_database(monkeypatch):
    monkeypatch.setenv("TAKYON_HOST_ROLE", "safebox")
    monkeypatch.setenv("TAKYON_SAFEBOX_TOKEN", "shared-token")
    monkeypatch.setenv("TAKYON_STRIPE_MODE", "live")
    monkeypatch.setenv("TAKYON_STRIPE_ACCOUNT_ID", "acct_live_atomic")
    event = _live_checkout_event()
    monkeypatch.setattr(
        safebox_app.safebox,
        "verify_stripe_app_webhook",
        lambda *_args, **_kwargs: event,
    )

    def stripe_request(path, params=None, *, method="POST", idempotency_key=None):
        if path == "checkout/sessions/cs_live_atomic":
            return dict(event["data"]["object"])
        if path == "subscriptions/sub_live_atomic":
            raise RuntimeError("subscription proof unavailable")
        pytest.fail(f"unexpected Stripe request: {path}")

    monkeypatch.setattr(safebox_app.safebox, "stripe_request", stripe_request)

    @contextlib.contextmanager
    def no_database_before_proof():
        pytest.fail("database mutated before subscription proof")
        yield

    monkeypatch.setattr(safebox_app, "_safebox_db_conn", no_database_before_proof)
    response = TestClient(safebox_app.build_safebox_app()).post(
        "/v1/stripe/app-webhook/process",
        headers={"Authorization": "Bearer shared-token"},
        json={"raw_body": "{}", "signature": "sig"},
    )

    assert response.status_code == 503, response.text
    assert response.json()["detail"] == "stripe_subscription_reconcile_pending"


def test_live_checkout_webhook_rolls_back_when_subscription_not_recorded(monkeypatch):
    monkeypatch.setenv("TAKYON_HOST_ROLE", "safebox")
    monkeypatch.setenv("TAKYON_SAFEBOX_TOKEN", "shared-token")
    monkeypatch.setenv("TAKYON_STRIPE_MODE", "live")
    monkeypatch.setenv("TAKYON_STRIPE_ACCOUNT_ID", "acct_live_atomic")
    event = _live_checkout_event()
    subscription = _live_checkout_subscription()
    conn = _AtomicWebhookConn()
    calls: list[str] = []
    monkeypatch.setattr(
        safebox_app.safebox,
        "verify_stripe_app_webhook",
        lambda *_args, **_kwargs: event,
    )

    def stripe_request(path, params=None, *, method="POST", idempotency_key=None):
        if path == "checkout/sessions/cs_live_atomic":
            return dict(event["data"]["object"])
        if path == "subscriptions/sub_live_atomic":
            return dict(subscription)
        pytest.fail(f"unexpected Stripe request: {path}")

    monkeypatch.setattr(safebox_app.safebox, "stripe_request", stripe_request)

    @contextlib.contextmanager
    def fake_conn():
        yield conn

    monkeypatch.setattr(safebox_app, "_safebox_db_conn", fake_conn)

    def process(_conn, _event):
        assert conn.in_transaction
        calls.append("checkout")
        return {
            "provider_event_id": event["id"],
            "type": event["type"],
            "deduplicated": False,
            "processed": {"recorded": True},
        }

    def reconcile(_conn, _subscription):
        assert conn.in_transaction
        calls.append("subscription")
        return {"recorded": False, "reason": "metadata_mismatch"}

    monkeypatch.setattr(app_payments, "record_webhook_and_process", process)
    monkeypatch.setattr(app_payments, "reconcile_subscription", reconcile)
    response = TestClient(safebox_app.build_safebox_app()).post(
        "/v1/stripe/app-webhook/process",
        headers={"Authorization": "Bearer shared-token"},
        json={"raw_body": "{}", "signature": "sig"},
    )

    assert response.status_code == 503, response.text
    assert response.json()["detail"] == "stripe_event_dependency_pending"
    assert calls == ["checkout", "subscription"]
    assert conn.rolled_back is True


def test_live_won_dispute_resolves_subscription_without_charge_invoice(monkeypatch):
    monkeypatch.setenv("TAKYON_HOST_ROLE", "safebox")
    monkeypatch.setenv("TAKYON_SAFEBOX_TOKEN", "shared-token")
    monkeypatch.setenv("TAKYON_STRIPE_MODE", "live")
    monkeypatch.setenv("TAKYON_STRIPE_ACCOUNT_ID", "acct_live_dispute")
    event = {
        "id": "evt_live_dispute_won",
        "type": "charge.dispute.closed",
        "livemode": True,
        "data": {
            "object": {
                "id": "du_live_won",
                "object": "dispute",
                "livemode": True,
                "status": "won",
                "charge": "ch_live_won",
                "amount": 2000,
                "currency": "usd",
            }
        },
    }
    charge = {
        "id": "ch_live_won",
        "object": "charge",
        "livemode": True,
        "amount": 2000,
        "amount_refunded": 0,
        "currency": "usd",
        "payment_intent": "pi_live_won",
        "metadata": {},
    }
    subscription = {
        "id": "sub_live_won",
        "object": "subscription",
        "livemode": True,
        "status": "active",
        "metadata": {
            "source": "takyon_app",
            "business": "live-dispute-co",
            "takyon_stripe_account_id": "acct_live_dispute",
        },
    }
    calls: list[str] = []
    captured: dict = {}
    monkeypatch.setattr(
        safebox_app.safebox,
        "verify_stripe_app_webhook",
        lambda *_args, **_kwargs: event,
    )

    def stripe_request(path, params=None, *, method="POST", idempotency_key=None):
        calls.append(path)
        if path == "disputes/du_live_won":
            return dict(event["data"]["object"])
        if path == "charges/ch_live_won":
            return dict(charge)
        if path == "subscriptions/sub_live_won":
            return dict(subscription)
        pytest.fail(f"unexpected Stripe request: {path}")

    monkeypatch.setattr(safebox_app.safebox, "stripe_request", stripe_request)
    monkeypatch.setattr(
        safebox_app,
        "_stripe_payment_subscription_binding",
        lambda payment_intent_id, charge_id: ("live-dispute-co", "sub_live_won"),
    )

    @contextlib.contextmanager
    def fake_conn():
        yield object()

    monkeypatch.setattr(safebox_app, "_safebox_db_conn", fake_conn)

    def process(_conn, current_event):
        captured.update(current_event["data"]["object"])
        return {
            "provider_event_id": event["id"],
            "type": event["type"],
            "deduplicated": False,
            "processed": {"recorded": True},
        }

    monkeypatch.setattr(app_payments, "record_webhook_and_process", process)
    response = TestClient(safebox_app.build_safebox_app()).post(
        "/v1/stripe/app-webhook/process",
        headers={"Authorization": "Bearer shared-token"},
        json={"raw_body": "{}", "signature": "sig"},
    )

    assert response.status_code == 200, response.text
    assert calls == [
        "disputes/du_live_won",
        "charges/ch_live_won",
        "subscriptions/sub_live_won",
    ]
    assert captured["_takyon_subscription"] == subscription
    assert captured["_takyon_charge_gross_cents"] == 2000
    assert captured["_takyon_charge_amount_refunded_cents"] == 0
