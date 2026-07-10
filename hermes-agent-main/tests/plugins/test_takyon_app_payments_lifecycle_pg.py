"""Subscription-lifecycle webhook handlers added to app_payments: invoice.paid (renewal revenue +
entitlement refresh), invoice.payment_failed (dunning, no revocation), and charge.refunded /
charge.dispute.created (revoke access + reversal revenue, netted out of the summary).

Owner custody accrual goes through the safebox authority (open_custody_account / accrue_custody);
these tests STUB that one call so the revenue-ledger + entitlement logic under test is exercised
without the safebox billing seam. PG-gated like the sibling suites (skips without TAKYON_TEST_PG_DSN).
"""

import concurrent.futures
import os
import threading
import uuid
from datetime import datetime, timezone

import psycopg
import pytest

from plugins.takyon import app_entitlements, app_identity, app_payments


def _seed_business(conn, slug: str) -> str:
    # Insert the owner directly (not via provision_user_on_first_login) so setup does not open a
    # safebox billing account — the handlers under test never need it, and the custody accrual they
    # do make is stubbed below.
    owner_id = conn.execute(
        "insert into users (auth0_sub) values (%s) returning id",
        (f"auth0|{uuid.uuid4().hex}",),
    ).fetchone()[0]
    conn.execute(
        "insert into businesses (slug, name, owner_user_id) values (%s, %s, %s)",
        (slug, slug, owner_id),
    )
    return owner_id


def _seed_paid_user(conn, slug: str, email: str, *, subscription_id: str) -> str:
    user_id = app_identity.upsert_app_user(conn, slug, email).id
    app_entitlements.grant_entitlement(
        conn,
        slug,
        app_user_id=user_id,
        tier="paid",
        status="active",
        source="stripe",
        stripe_customer_id="cus_x",
        stripe_subscription_id=subscription_id,
        plan_key="pro",
    )
    return user_id


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


def _ent(conn, slug, user_id):
    return app_entitlements.list_entitlements(conn, slug, app_user_id=user_id)[0]


@pytest.mark.parametrize(
    ("subscription", "expected_epoch"),
    [
        (
            {
                "current_period_end": 1_700_700_000,
                "items": {"data": [{"current_period_end": 1_700_600_000}]},
            },
            1_700_700_000,
        ),
        ({"items": {"data": [{"current_period_end": 1_700_600_000}]}}, 1_700_600_000),
        (
            {
                "items": {
                    "data": [
                        {"current_period_end": 1_700_800_000},
                        {"current_period_end": "invalid"},
                        {"current_period_end": 1_700_600_000},
                    ]
                }
            },
            1_700_600_000,
        ),
        ({}, None),
        ({"items": {"data": None}}, None),
    ],
)
def test_subscription_period_end_supports_legacy_and_dahlia_shapes(
    subscription, expected_epoch
):
    expected = (
        None
        if expected_epoch is None
        else datetime.fromtimestamp(expected_epoch, timezone.utc)
    )
    assert app_payments._subscription_period_end(subscription) == expected


def test_subscription_event_uses_dahlia_item_period(monkeypatch):
    captured = {}

    def _set_subscription_status(_conn, subscription_id, **kwargs):
        captured["subscription_id"] = subscription_id
        captured.update(kwargs)
        return [{"updated": True}]

    monkeypatch.setattr(
        app_payments.app_entitlements,
        "set_subscription_status",
        _set_subscription_status,
    )

    result = app_payments._process_subscription_event(
        object(),
        {
            "id": "sub_dahlia",
            "status": "active",
            "customer": "cus_dahlia",
            "items": {"data": [{"current_period_end": 1_700_600_000}]},
        },
    )

    assert result["recorded"] is True
    assert captured["subscription_id"] == "sub_dahlia"
    assert captured["current_period_end"] == datetime.fromtimestamp(
        1_700_600_000, timezone.utc
    )


@pytest.mark.parametrize(
    ("invoice", "expected"),
    [
        ({"subscription": "sub_legacy"}, "sub_legacy"),
        ({"subscription": {"id": "sub_legacy_expanded"}}, "sub_legacy_expanded"),
        (
            {"parent": {"subscription_details": {"subscription": "sub_dahlia"}}},
            "sub_dahlia",
        ),
        (
            {
                "parent": {
                    "subscription_details": {
                        "subscription": {"id": "sub_dahlia_expanded"}
                    }
                }
            },
            "sub_dahlia_expanded",
        ),
        (
            {
                "subscription": "sub_legacy_wins",
                "parent": {"subscription_details": {"subscription": "sub_dahlia"}},
            },
            "sub_legacy_wins",
        ),
        ({}, None),
        ({"parent": {"subscription_details": None}}, None),
    ],
)
def test_invoice_subscription_id_supports_legacy_and_dahlia_shapes(invoice, expected):
    assert app_payments._invoice_subscription_id(invoice) == expected


def test_invoice_paid_renewal_records_revenue_and_clears_dunning(pg_conn, monkeypatch):
    _stub_custody(monkeypatch)
    slug = "renew-co"
    _seed_business(pg_conn, slug)
    user_id = _seed_paid_user(pg_conn, slug, "a@x.test", subscription_id="sub_1")

    # a prior failed attempt marks dunning but must not revoke access
    app_payments.record_webhook_and_process(pg_conn, {
        "id": "evt_fail_1", "type": "invoice.payment_failed",
        "data": {"object": {
            "id": "in_0",
            "parent": {"subscription_details": {"subscription": "sub_1"}},
        }},
    })
    assert app_entitlements.get_active_entitlement(pg_conn, slug, user_id) is not None
    assert _ent(pg_conn, slug, user_id).metadata.get("dunning") is True

    res = app_payments.record_webhook_and_process(pg_conn, {
        "id": "evt_inv_1", "type": "invoice.paid", "created": 1_700_000_000,
        "data": {"object": {
            "id": "in_1",
            "parent": {"subscription_details": {"subscription": "sub_1"}},
            "customer": "cus_x",
            "billing_reason": "subscription_cycle", "amount_paid": 2000, "currency": "usd",
            "customer_email": "a@x.test",
        }},
    })
    assert res["processed"]["revenue_recorded"] is True
    assert app_payments.get_revenue_summary(pg_conn, slug)["amount_paid_cents"] == 2000
    revs = app_payments.list_revenue_events(pg_conn, slug)
    assert any(r.revenue_type == "subscription_renewal" and r.amount_paid_cents == 2000 for r in revs)
    # a successful renewal restores active access and clears the dunning flag
    assert app_entitlements.get_active_entitlement(pg_conn, slug, user_id) is not None
    assert _ent(pg_conn, slug, user_id).metadata.get("dunning") is False


def test_invoice_paid_replay_is_idempotent(pg_conn, monkeypatch):
    _stub_custody(monkeypatch)
    slug = "renew-idem-co"
    _seed_business(pg_conn, slug)
    _seed_paid_user(pg_conn, slug, "e@x.test", subscription_id="sub_e")
    event = {
        "id": "evt_inv_e", "type": "invoice.paid", "created": 1_700_000_000,
        "data": {"object": {"id": "in_e", "subscription": "sub_e", "billing_reason": "subscription_cycle",
                            "amount_paid": 2000, "currency": "usd"}},
    }
    app_payments.record_webhook_and_process(pg_conn, event)
    replay = app_payments.record_webhook_and_process(pg_conn, event)
    assert replay["deduplicated"] is True
    assert app_payments.get_revenue_summary(pg_conn, slug)["amount_paid_cents"] == 2000  # not 4000


def test_invoice_paid_initial_invoice_skipped(pg_conn, monkeypatch):
    _stub_custody(monkeypatch)
    slug = "skip-co"
    _seed_business(pg_conn, slug)
    _seed_paid_user(pg_conn, slug, "b@x.test", subscription_id="sub_2")
    res = app_payments.record_webhook_and_process(pg_conn, {
        "id": "evt_inv_2", "type": "invoice.paid", "created": 1_700_000_000,
        "data": {"object": {
            "id": "in_2", "subscription": "sub_2", "billing_reason": "subscription_create",
            "amount_paid": 2000, "currency": "usd",
        }},
    })
    assert res["processed"]["recorded"] is False
    assert res["processed"]["reason"] == "initial_invoice_counted_at_checkout"
    assert app_payments.get_revenue_summary(pg_conn, slug)["amount_paid_cents"] == 0


def test_paid_invoice_records_revenue_but_current_canceled_subscription_stays_canceled(
    pg_conn, monkeypatch
):
    _stub_custody(monkeypatch)
    slug = "paid-final-invoice-co"
    _seed_business(pg_conn, slug)
    user_id = _seed_paid_user(
        pg_conn, slug, "final@x.test", subscription_id="sub_final_invoice"
    )
    result = app_payments.record_webhook_and_process(
        pg_conn,
        {
            "id": "evt_final_invoice_paid",
            "type": "invoice.paid",
            "created": 300,
            "data": {
                "object": {
                    "id": "in_final_invoice",
                    "subscription": "sub_final_invoice",
                    "billing_reason": "subscription_cycle",
                    "payment_intent": "pi_final_invoice",
                    "amount_paid": 2000,
                    "currency": "usd",
                    "_takyon_subscription": {
                        "id": "sub_final_invoice",
                        "status": "canceled",
                        "customer": "cus_x",
                    },
                }
            },
        },
    )["processed"]

    assert result["revenue_recorded"] is True
    assert app_payments.get_revenue_summary(pg_conn, slug)["amount_paid_cents"] == 2000
    assert app_entitlements.get_active_entitlement(pg_conn, slug, user_id) is None
    assert _ent(pg_conn, slug, user_id).status == "cancelled"


def test_invoice_payment_failed_marks_dunning_without_revoking(pg_conn):
    slug = "dun-co"
    _seed_business(pg_conn, slug)
    user_id = _seed_paid_user(pg_conn, slug, "c@x.test", subscription_id="sub_3")
    res = app_payments.record_webhook_and_process(pg_conn, {
        "id": "evt_fail_3", "type": "invoice.payment_failed",
        "data": {"object": {"id": "in_3", "subscription": "sub_3"}},
    })
    assert res["processed"]["recorded"] is True
    # access NOT revoked — Stripe smart-retry grace governs revocation via subscription.updated
    assert app_entitlements.get_active_entitlement(pg_conn, slug, user_id) is not None
    ent = _ent(pg_conn, slug, user_id)
    assert ent.status == "active"
    assert ent.metadata.get("dunning") is True


def test_charge_refunded_revokes_access_and_nets_out_revenue(pg_conn, monkeypatch):
    _stub_custody(monkeypatch)
    slug = "refund-co"
    _seed_business(pg_conn, slug)
    user_id = _seed_paid_user(pg_conn, slug, "d@x.test", subscription_id="sub_4")
    pg_conn.execute(
        "insert into app_checkout_sessions (business_slug, stripe_checkout_session_id, "
        " stripe_subscription_id, stripe_payment_intent_id, stripe_customer_id, customer_email) "
        "values (%s, %s, %s, %s, %s, %s)",
        (slug, "cs_4", "sub_4", "pi_4", "cus_x", "d@x.test"),
    )
    app_payments.record_webhook_and_process(pg_conn, {
        "id": "evt_inv_4", "type": "invoice.paid", "created": 1_700_000_000,
        "data": {"object": {"id": "in_4", "subscription": "sub_4", "billing_reason": "subscription_cycle",
                            "payment_intent": "pi_4", "amount_paid": 2000,
                            "currency": "usd"}},
    })
    assert app_payments.get_revenue_summary(pg_conn, slug)["amount_paid_cents"] == 2000

    res = app_payments.record_webhook_and_process(pg_conn, {
        "id": "evt_ref_4", "type": "charge.refunded", "created": 1_700_000_100,
        "data": {"object": {"id": "ch_4", "payment_intent": "pi_4", "customer": "cus_x",
                            "amount_refunded": 2000, "currency": "usd"}},
    })
    assert res["processed"]["access_revoked"] >= 1
    assert res["processed"]["reversal_recorded"] is True
    # paid access is revoked after the refund
    assert app_entitlements.get_active_entitlement(pg_conn, slug, user_id) is None
    # revenue nets to zero (2000 renewal - 2000 reversal), proving reversals are subtracted
    assert app_payments.get_revenue_summary(pg_conn, slug)["amount_paid_cents"] == 0


def test_initial_subscription_refund_resolves_checkout_by_invoice(pg_conn, monkeypatch):
    _stub_custody(monkeypatch)
    slug = "initial-refund-co"
    _seed_business(pg_conn, slug)
    user_id = _seed_paid_user(
        pg_conn, slug, "initial@x.test", subscription_id="sub_initial"
    )
    pg_conn.execute(
        "insert into app_checkout_sessions (business_slug, stripe_checkout_session_id, "
        " stripe_subscription_id, stripe_customer_id, customer_email) "
        "values (%s, %s, %s, %s, %s)",
        (slug, "cs_initial", "sub_initial", "cus_initial", "initial@x.test"),
    )
    assert app_payments._insert_revenue_event(
        pg_conn,
        business_slug=slug,
        provider_event_id="evt_checkout_initial",
        stripe_object_type="checkout_session",
        stripe_object_id="cs_initial",
        stripe_checkout_session_id="cs_initial",
        stripe_customer_id="cus_initial",
        revenue_type="checkout",
        status="paid",
        currency="usd",
        amount_paid_cents=2000,
        customer_email="initial@x.test",
        metadata={
            "stripe_environment": "test",
            "stripe_invoice_id": "in_initial",
            "pricing_split": {"owner_net_cents": 1600},
            "stripe_subscription_id": "sub_initial",
        },
    )

    result = app_payments.record_webhook_and_process(
        pg_conn,
        {
            "id": "evt_refund_initial",
            "type": "charge.refunded",
            "data": {
                "object": {
                    "id": "ch_initial",
                    "invoice": "in_initial",
                    "customer": "cus_initial",
                    "amount_refunded": 2000,
                    "currency": "usd",
                }
            },
        },
    )["processed"]

    assert result["reversal_recorded"] is True
    assert result["amount_reversed_cents"] == 2000
    assert result["custody_clawback_applied_cents"] == 1600
    assert app_entitlements.get_active_entitlement(pg_conn, slug, user_id) is None
    assert app_payments.get_revenue_summary(pg_conn, slug)["amount_paid_cents"] == 0


def test_basil_initial_checkout_refund_resolves_payment_via_invoice_payments(
    pg_conn, monkeypatch
):
    _stub_custody(monkeypatch)
    slug = "basil-initial-refund-co"
    _seed_business(pg_conn, slug)
    user_id = app_identity.upsert_app_user(pg_conn, slug, "basil@x.test").id
    intent = app_payments.create_checkout_intent(
        pg_conn,
        slug,
        plan_key="pro",
        client_reference_id="ref-basil-initial",
        app_user_id=user_id,
        customer_email="basil@x.test",
    )
    checkout = app_payments.record_webhook_and_process(
        pg_conn,
        {
            "id": "evt_basil_checkout",
            "type": "checkout.session.completed",
            "created": 100,
            "data": {
                "object": {
                    "id": "cs_basil_initial",
                    "mode": "subscription",
                    "payment_status": "paid",
                    "status": "complete",
                    "currency": "usd",
                    "amount_total": 2000,
                    "customer": "cus_basil",
                    "subscription": "sub_basil_initial",
                    "invoice": "in_basil_initial",
                    "customer_details": {"email": "basil@x.test"},
                    "metadata": {"checkout_intent_id": intent.id},
                    "_takyon_invoice": {
                        "id": "in_basil_initial",
                        "payments": {
                            "data": [
                                {
                                    "id": "inpay_basil_initial",
                                    "payment": {
                                        "type": "payment_intent",
                                        "payment_intent": "pi_basil_initial",
                                    },
                                }
                            ]
                        },
                    },
                }
            },
        },
    )["processed"]
    assert checkout["revenue_recorded"] is True

    refund = app_payments.record_webhook_and_process(
        pg_conn,
        {
            "id": "evt_basil_refund",
            "type": "charge.refunded",
            "created": 200,
            "data": {
                "object": {
                    "id": "ch_basil_initial",
                    "payment_intent": "pi_basil_initial",
                    "amount_refunded": 2000,
                    "amount": 2000,
                    "currency": "usd",
                }
            },
        },
    )["processed"]

    assert refund["reversal_recorded"] is True
    assert app_entitlements.get_active_entitlement(pg_conn, slug, user_id) is None
    assert app_payments.get_revenue_summary(pg_conn, slug)["amount_paid_cents"] == 0


def test_partial_refund_events_record_only_cumulative_delta(pg_conn, monkeypatch):
    _stub_custody(monkeypatch)
    slug = "partial-refund-co"
    _seed_business(pg_conn, slug)
    _seed_paid_user(pg_conn, slug, "partial@x.test", subscription_id="sub_partial")
    pg_conn.execute(
        "insert into app_checkout_sessions (business_slug, stripe_checkout_session_id, "
        " stripe_subscription_id, stripe_payment_intent_id, stripe_customer_id, customer_email) "
        "values (%s, %s, %s, %s, %s, %s)",
        (slug, "cs_partial", "sub_partial", "pi_partial", "cus_partial", "partial@x.test"),
    )
    app_payments.record_webhook_and_process(
        pg_conn,
        {
            "id": "evt_partial_invoice",
            "type": "invoice.paid",
            "data": {
                "object": {
                    "id": "in_partial",
                    "subscription": "sub_partial",
                    "billing_reason": "subscription_cycle",
                    "payment_intent": "pi_partial",
                    "amount_paid": 2000,
                    "currency": "usd",
                }
            },
        },
    )

    deltas = []
    for event_id, cumulative in (
        ("evt_partial_500", 500),
        ("evt_partial_1000", 1000),
        ("evt_partial_repeat", 1000),
    ):
        result = app_payments.record_webhook_and_process(
            pg_conn,
            {
                "id": event_id,
                "type": "charge.refunded",
                "data": {
                    "object": {
                        "id": "ch_partial",
                        "payment_intent": "pi_partial",
                        "customer": "cus_partial",
                        "amount_refunded": cumulative,
                        "currency": "usd",
                    }
                },
            },
        )["processed"]
        deltas.append(
            (result["amount_reversed_cents"], result["reversal_recorded"])
        )

    assert deltas == [(500, True), (500, True), (0, False)]
    assert app_payments.get_revenue_summary(pg_conn, slug)["amount_paid_cents"] == 1000
    assert pg_conn.execute(
        "select amount_paid_cents from app_revenue_events "
        "where business_slug = %s and revenue_type = 'reversal' order by created_at",
        (slug,),
    ).fetchall() == [(500,), (500,)]


def test_stale_active_subscription_event_cannot_undo_newer_cancellation(pg_conn):
    slug = "ordered-subscription-co"
    _seed_business(pg_conn, slug)
    user_id = _seed_paid_user(
        pg_conn, slug, "ordered@x.test", subscription_id="sub_ordered"
    )

    app_payments.record_webhook_and_process(
        pg_conn,
        {
            "id": "evt_deleted_newer",
            "type": "customer.subscription.deleted",
            "created": 200,
            "data": {
                "object": {
                    "id": "sub_ordered",
                    "status": "canceled",
                    "customer": "cus_x",
                }
            },
        },
    )
    app_payments.record_webhook_and_process(
        pg_conn,
        {
            "id": "evt_active_older",
            "type": "customer.subscription.updated",
            "created": 100,
            "data": {
                "object": {
                    "id": "sub_ordered",
                    "status": "active",
                    "customer": "cus_x",
                }
            },
        },
    )

    entitlement = _ent(pg_conn, slug, user_id)
    assert entitlement.status == "cancelled"
    assert entitlement.metadata["stripe_lifecycle_event_created"] == 200
    assert entitlement.metadata["stripe_lifecycle_event_ignored"] == "evt_active_older"


def test_refund_cumulative_amount_is_scoped_per_charge(pg_conn, monkeypatch):
    _stub_custody(monkeypatch)
    slug = "multi-charge-refund-co"
    _seed_business(pg_conn, slug)
    _seed_paid_user(pg_conn, slug, "multi@x.test", subscription_id="sub_multi_charge")
    app_payments.record_webhook_and_process(
        pg_conn,
        {
            "id": "evt_multi_charge_paid",
            "type": "invoice.paid",
            "created": 100,
            "data": {
                "object": {
                    "id": "in_multi_charge",
                    "subscription": "sub_multi_charge",
                    "billing_reason": "subscription_cycle",
                    "payments": {
                        "data": [
                            {"payment": {"charge": "ch_multi_a"}},
                            {"payment": {"charge": "ch_multi_b"}},
                        ]
                    },
                    "amount_paid": 2000,
                    "currency": "usd",
                }
            },
        },
    )

    deltas = []
    for event_id, charge_id, cumulative in (
        ("evt_multi_refund_a", "ch_multi_a", 600),
        ("evt_multi_refund_b", "ch_multi_b", 700),
    ):
        result = app_payments.record_webhook_and_process(
            pg_conn,
            {
                "id": event_id,
                "type": "charge.refunded",
                "created": 200,
                "data": {
                    "object": {
                        "id": charge_id,
                        "amount_refunded": cumulative,
                        "currency": "usd",
                    }
                },
            },
        )["processed"]
        deltas.append(result["amount_reversed_cents"])

    assert deltas == [600, 700]
    assert app_payments.get_revenue_summary(pg_conn, slug)["amount_paid_cents"] == 700


def test_refund_blocks_active_update_until_strictly_newer_paid_invoice(pg_conn, monkeypatch):
    _stub_custody(monkeypatch)
    slug = "refund-reactivation-co"
    _seed_business(pg_conn, slug)
    user_id = _seed_paid_user(
        pg_conn, slug, "reactivation@x.test", subscription_id="sub_reactivation"
    )

    app_payments.record_webhook_and_process(
        pg_conn,
        {
            "id": "evt_paid_before_refund",
            "type": "invoice.paid",
            "created": 100,
            "data": {
                "object": {
                    "id": "in_before_refund",
                    "subscription": "sub_reactivation",
                    "billing_reason": "subscription_cycle",
                    "payment_intent": "pi_before_refund",
                    "amount_paid": 2000,
                    "currency": "usd",
                }
            },
        },
    )
    app_payments.record_webhook_and_process(
        pg_conn,
        {
            "id": "evt_refund_reactivation",
            "type": "charge.refunded",
            "created": 200,
            "data": {
                "object": {
                    "id": "ch_refund_reactivation",
                    "payment_intent": "pi_before_refund",
                    "amount_refunded": 2000,
                    "currency": "usd",
                }
            },
        },
    )
    app_payments.record_webhook_and_process(
        pg_conn,
        {
            "id": "evt_active_after_refund",
            "type": "customer.subscription.updated",
            "created": 300,
            "data": {
                "object": {
                    "id": "sub_reactivation",
                    "status": "active",
                    "customer": "cus_x",
                }
            },
        },
    )
    blocked = _ent(pg_conn, slug, user_id)
    assert blocked.status == "cancelled"
    assert blocked.metadata["payment_revoked"] is True
    assert blocked.metadata["payment_reactivation_blocked"] is True

    app_payments.record_webhook_and_process(
        pg_conn,
        {
            "id": "evt_paid_after_refund",
            "type": "invoice.paid",
            "created": 400,
            "data": {
                "object": {
                    "id": "in_after_refund",
                    "subscription": "sub_reactivation",
                    "billing_reason": "subscription_cycle",
                    "payment_intent": "pi_after_refund",
                    "amount_paid": 2000,
                    "currency": "usd",
                }
            },
        },
    )
    restored = _ent(pg_conn, slug, user_id)
    assert restored.status == "active"
    assert restored.metadata["payment_revoked"] is False


def test_past_due_subscription_freezes_plan_until_paid_retry(pg_conn, monkeypatch):
    _stub_custody(monkeypatch)
    slug = "past-due-plan-freeze-co"
    _seed_business(pg_conn, slug)
    app_entitlements.upsert_plan_policy(
        pg_conn,
        slug,
        "pro",
        tier="paid",
        price_cents=2000,
        included_ai_budget_microusd=1_000_000,
    )
    user_id = _seed_paid_user(
        pg_conn, slug, "past-due@x.test", subscription_id="sub_past_due_freeze"
    )
    app_payments.record_webhook_and_process(
        pg_conn,
        {
            "id": "evt_past_due_freeze",
            "type": "customer.subscription.updated",
            "created": 200,
            "data": {
                "object": {
                    "id": "sub_past_due_freeze",
                    "status": "past_due",
                    "customer": "cus_past_due_freeze",
                }
            },
        },
    )
    assert _ent(pg_conn, slug, user_id).status == "past_due"

    with pytest.raises(app_entitlements.GrandfatheredPlanFrozen):
        app_entitlements.upsert_plan_policy(
            pg_conn,
            slug,
            "pro",
            tier="paid",
            price_cents=3000,
            included_ai_budget_microusd=1_000_000,
        )
    assert app_entitlements.get_plan_policy(pg_conn, slug, "pro").price_cents == 2000

    paid = app_payments.record_webhook_and_process(
        pg_conn,
        {
            "id": "evt_past_due_paid_retry",
            "type": "invoice.paid",
            "created": 300,
            "data": {
                "object": {
                    "id": "in_past_due_paid_retry",
                    "subscription": "sub_past_due_freeze",
                    "billing_reason": "subscription_cycle",
                    "payment_intent": "pi_past_due_paid_retry",
                    "amount_paid": 2000,
                    "currency": "usd",
                }
            },
        },
    )["processed"]
    assert paid["amount_paid_cents"] == 2000
    assert _ent(pg_conn, slug, user_id).status == "active"


def test_refund_claws_back_immutable_original_custody_recipient(pg_conn, monkeypatch):
    _stub_custody(monkeypatch)
    slug = "ownership-transfer-refund-co"
    original_owner = _seed_business(pg_conn, slug)
    _seed_paid_user(
        pg_conn, slug, "transfer@x.test", subscription_id="sub_transfer"
    )
    app_payments.record_webhook_and_process(
        pg_conn,
        {
            "id": "evt_transfer_paid",
            "type": "invoice.paid",
            "created": 100,
            "data": {
                "object": {
                    "id": "in_transfer",
                    "subscription": "sub_transfer",
                    "billing_reason": "subscription_cycle",
                    "payment_intent": "pi_transfer",
                    "amount_paid": 2000,
                    "currency": "usd",
                }
            },
        },
    )
    replacement_owner = pg_conn.execute(
        "insert into users (auth0_sub) values (%s) returning id",
        (f"auth0|{uuid.uuid4().hex}",),
    ).fetchone()[0]
    pg_conn.execute(
        "update businesses set owner_user_id = %s where slug = %s",
        (replacement_owner, slug),
    )
    clawed_back_users = []

    def _capture_clawback(*args, **_kwargs):
        clawed_back_users.append(str(args[1]))
        return {
            "applied_cents": int(args[3]),
            "shortfall_cents": 0,
            "owed_balance_cents": 0,
            "replayed": False,
        }

    monkeypatch.setattr(app_payments.safebox, "clawback_custody", _capture_clawback)
    app_payments.record_webhook_and_process(
        pg_conn,
        {
            "id": "evt_transfer_refund",
            "type": "charge.refunded",
            "created": 200,
            "data": {
                "object": {
                    "id": "ch_transfer",
                    "payment_intent": "pi_transfer",
                    "amount_refunded": 2000,
                    "currency": "usd",
                }
            },
        },
    )

    assert clawed_back_users == [str(original_owner)]
    assert clawed_back_users != [str(replacement_owner)]


def test_inquiry_refund_then_warning_closed_never_releases_real_refund(
    pg_conn, monkeypatch
):
    _stub_custody(monkeypatch)
    slug = "inquiry-refund-co"
    _seed_business(pg_conn, slug)
    user_id = _seed_paid_user(
        pg_conn, slug, "inquiry@x.test", subscription_id="sub_inquiry"
    )
    app_payments.record_webhook_and_process(
        pg_conn,
        {
            "id": "evt_inquiry_paid",
            "type": "invoice.paid",
            "created": 100,
            "data": {
                "object": {
                    "id": "in_inquiry",
                    "subscription": "sub_inquiry",
                    "billing_reason": "subscription_cycle",
                    "payment_intent": "pi_inquiry",
                    "amount_paid": 2000,
                    "currency": "usd",
                }
            },
        },
    )
    inquiry = {
        "id": "du_inquiry",
        "status": "warning_needs_response",
        "charge": "ch_inquiry",
        "payment_intent": "pi_inquiry",
        "amount": 2000,
        "currency": "usd",
    }
    result = app_payments.record_webhook_and_process(
        pg_conn,
        {
            "id": "evt_inquiry_created",
            "type": "charge.dispute.created",
            "created": 150,
            "data": {"object": inquiry},
        },
    )["processed"]
    assert result["inquiry_observed"] is True
    assert app_payments.get_revenue_summary(pg_conn, slug)["amount_paid_cents"] == 2000

    app_payments.record_webhook_and_process(
        pg_conn,
        {
            "id": "evt_inquiry_refund",
            "type": "charge.refunded",
            "created": 200,
            "data": {
                "object": {
                    "id": "ch_inquiry",
                    "payment_intent": "pi_inquiry",
                    "amount": 2000,
                    "amount_refunded": 1000,
                    "currency": "usd",
                }
            },
        },
    )
    closed = {
        **inquiry,
        "status": "warning_closed",
        "_takyon_subscription": {
            "id": "sub_inquiry",
            "status": "active",
            "customer": "cus_x",
        },
    }
    app_payments.record_webhook_and_process(
        pg_conn,
        {
            "id": "evt_inquiry_closed",
            "type": "charge.dispute.closed",
            "created": 300,
            "data": {"object": closed},
        },
    )
    assert app_payments.get_revenue_summary(pg_conn, slug)["amount_paid_cents"] == 1000
    assert app_entitlements.get_active_entitlement(pg_conn, slug, user_id) is None


def test_inquiry_escalation_reverses_only_when_funds_withdrawn(pg_conn, monkeypatch):
    _stub_custody(monkeypatch)
    slug = "inquiry-escalation-co"
    _seed_business(pg_conn, slug)
    user_id = _seed_paid_user(
        pg_conn, slug, "escalation@x.test", subscription_id="sub_escalation"
    )
    app_payments.record_webhook_and_process(
        pg_conn,
        {
            "id": "evt_escalation_paid",
            "type": "invoice.paid",
            "created": 100,
            "data": {
                "object": {
                    "id": "in_escalation",
                    "subscription": "sub_escalation",
                    "billing_reason": "subscription_cycle",
                    "payment_intent": "pi_escalation",
                    "amount_paid": 2000,
                    "currency": "usd",
                }
            },
        },
    )
    dispute = {
        "id": "du_escalation",
        "charge": "ch_escalation",
        "payment_intent": "pi_escalation",
        "amount": 2000,
        "currency": "usd",
        "status": "warning_needs_response",
    }
    app_payments.record_webhook_and_process(
        pg_conn,
        {
            "id": "evt_escalation_inquiry",
            "type": "charge.dispute.created",
            "created": 150,
            "data": {"object": dispute},
        },
    )
    app_payments.record_webhook_and_process(
        pg_conn,
        {
            "id": "evt_escalation_updated",
            "type": "charge.dispute.updated",
            "created": 200,
            "data": {"object": {**dispute, "status": "needs_response"}},
        },
    )
    assert app_payments.get_revenue_summary(pg_conn, slug)["amount_paid_cents"] == 2000
    assert app_entitlements.get_active_entitlement(pg_conn, slug, user_id) is not None

    app_payments.record_webhook_and_process(
        pg_conn,
        {
            "id": "evt_escalation_withdrawn",
            "type": "charge.dispute.funds_withdrawn",
            "created": 250,
            "data": {"object": {**dispute, "status": "needs_response"}},
        },
    )
    assert app_payments.get_revenue_summary(pg_conn, slug)["amount_paid_cents"] == 0
    assert app_entitlements.get_active_entitlement(pg_conn, slug, user_id) is None


def test_dispute_withdrawal_duplicate_is_cumulative_and_won_releases_once(
    pg_conn, monkeypatch
):
    _stub_custody(monkeypatch)
    slug = "dispute-lifecycle-co"
    _seed_business(pg_conn, slug)
    user_id = _seed_paid_user(
        pg_conn, slug, "dispute@x.test", subscription_id="sub_dispute"
    )
    app_payments.record_webhook_and_process(
        pg_conn,
        {
            "id": "evt_dispute_paid",
            "type": "invoice.paid",
            "created": 100,
            "data": {
                "object": {
                    "id": "in_dispute",
                    "subscription": "sub_dispute",
                    "billing_reason": "subscription_cycle",
                    "payment_intent": "pi_dispute",
                    "amount_paid": 10_000,
                    "currency": "usd",
                }
            },
        },
    )
    dispute = {
        "id": "du_dispute",
        "charge": "ch_dispute",
        "payment_intent": "pi_dispute",
        "amount": 4000,
        "_takyon_charge_gross_cents": 10_000,
        "currency": "usd",
        "status": "needs_response",
    }
    created = app_payments.record_webhook_and_process(
        pg_conn,
        {
            "id": "evt_dispute_created",
            "type": "charge.dispute.created",
            "created": 200,
            "data": {"object": dispute},
        },
    )["processed"]
    withdrawn = app_payments.record_webhook_and_process(
        pg_conn,
        {
            "id": "evt_dispute_withdrawn",
            "type": "charge.dispute.funds_withdrawn",
            "created": 210,
            "data": {"object": dispute},
        },
    )["processed"]
    assert created["amount_reversed_cents"] == 4000
    assert withdrawn["amount_reversed_cents"] == 0
    assert app_payments.get_revenue_summary(pg_conn, slug)["amount_paid_cents"] == 6000

    overlapping_refund = {
        "id": "evt_refund_while_dispute_open",
        "type": "charge.refunded",
        "created": 250,
    }
    pending_refund = app_payments._process_charge_reversal(
        pg_conn,
        overlapping_refund,
        {
            "id": "ch_dispute",
            "payment_intent": "pi_dispute",
            "amount": 10_000,
            "amount_refunded": 10_000,
            "currency": "usd",
        },
    )
    assert pending_refund["amount_reversed_cents"] == 6000
    assert pending_refund["refund_unapplied_cents"] == 4000
    assert app_payments.get_revenue_summary(pg_conn, slug)["amount_paid_cents"] == 0

    release_calls = []

    def _release(*args, **_kwargs):
        release_calls.append((str(args[1]), str(args[3]), str(args[4])))
        return {"credited_cents": 3200, "owed_balance_cents": 8000, "replayed": False}

    monkeypatch.setattr(
        app_payments.safebox,
        "release_custody_clawback",
        _release,
        raising=False,
    )
    won = app_payments.record_webhook_and_process(
        pg_conn,
        {
            "id": "evt_dispute_won",
            "type": "charge.dispute.closed",
            "created": 300,
            "data": {
                "object": {
                    **dispute,
                    "status": "won",
                    "_takyon_subscription": {
                        "id": "sub_dispute",
                        "status": "active",
                        "customer": "cus_x",
                    },
                }
            },
        },
    )["processed"]
    assert won["amount_released_cents"] == 4000
    assert len(release_calls) == 1
    assert won["refund_reconciled"]["amount_reversed_cents"] == 4000
    assert won["remaining_reversed_cents"] == 10_000
    assert app_payments.get_revenue_summary(pg_conn, slug)["amount_paid_cents"] == 0
    assert app_entitlements.get_active_entitlement(pg_conn, slug, user_id) is None

    later_refund = app_payments.record_webhook_and_process(
        pg_conn,
        {
            "id": "evt_refund_after_dispute_win",
            "type": "charge.refunded",
            "created": 400,
            "data": {
                "object": {
                    "id": "ch_dispute",
                    "payment_intent": "pi_dispute",
                    "amount": 10_000,
                    "amount_refunded": 10_000,
                    "currency": "usd",
                }
            },
        },
    )["processed"]
    assert later_refund["amount_reversed_cents"] == 0
    assert app_payments.get_revenue_summary(pg_conn, slug)["amount_paid_cents"] == 0
    assert app_entitlements.get_active_entitlement(pg_conn, slug, user_id) is None


def test_won_dispute_waits_for_concurrent_refund_claim(pg_conn, monkeypatch):
    _stub_custody(monkeypatch)
    slug = "concurrent-refund-dispute-co"
    _seed_business(pg_conn, slug)
    user_id = _seed_paid_user(
        pg_conn, slug, "concurrent@x.test", subscription_id="sub_concurrent"
    )
    app_payments.record_webhook_and_process(
        pg_conn,
        {
            "id": "evt_concurrent_paid",
            "type": "invoice.paid",
            "created": 100,
            "data": {
                "object": {
                    "id": "in_concurrent",
                    "subscription": "sub_concurrent",
                    "billing_reason": "subscription_cycle",
                    "payment_intent": "pi_concurrent",
                    "amount_paid": 2000,
                    "currency": "usd",
                }
            },
        },
    )
    original_revenue_id = str(
        pg_conn.execute(
            "select id from app_revenue_events where provider_event_id = %s",
            ("evt_concurrent_paid",),
        ).fetchone()[0]
    )
    dispute = {
        "id": "du_concurrent",
        "charge": "ch_concurrent",
        "payment_intent": "pi_concurrent",
        "amount": 2000,
        "_takyon_charge_gross_cents": 2000,
        "currency": "usd",
        "status": "needs_response",
    }
    app_payments.record_webhook_and_process(
        pg_conn,
        {
            "id": "evt_concurrent_dispute",
            "type": "charge.dispute.created",
            "created": 200,
            "data": {"object": dispute},
        },
    )
    monkeypatch.setattr(
        app_payments.safebox,
        "release_custody_clawback",
        lambda *_args, **_kwargs: {
            "credited_cents": 1600,
            "owed_balance_cents": 1600,
            "replayed": False,
        },
        raising=False,
    )
    admin_dsn = str(os.environ["TAKYON_TEST_PG_DSN"])
    database_dsn = psycopg.conninfo.make_conninfo(
        admin_dsn, dbname=pg_conn.info.dbname
    )
    refund_conn = psycopg.connect(database_dsn, autocommit=True)
    won_conn = psycopg.connect(database_dsn, autocommit=True)
    started = threading.Event()

    def close_won_dispute():
        started.set()
        return app_payments.record_webhook_and_process(
            won_conn,
            {
                "id": "evt_concurrent_won",
                "type": "charge.dispute.closed",
                "created": 300,
                "data": {
                    "object": {
                        **dispute,
                        "status": "won",
                        "_takyon_subscription": {
                            "id": "sub_concurrent",
                            "status": "active",
                            "customer": "cus_x",
                        },
                    }
                },
            },
        )["processed"]

    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            with refund_conn.transaction():
                refund_conn.execute(
                    "select pg_advisory_xact_lock(hashtextextended(%s, 0))",
                    (f"takyon-stripe-reversal:{original_revenue_id}",),
                )
                future = executor.submit(close_won_dispute)
                assert started.wait(timeout=2)
                refund = app_payments.record_webhook_and_process(
                    refund_conn,
                    {
                        "id": "evt_concurrent_refund",
                        "type": "charge.refunded",
                        "created": 250,
                        "data": {
                            "object": {
                                "id": "ch_concurrent",
                                "payment_intent": "pi_concurrent",
                                "amount": 2000,
                                "amount_refunded": 2000,
                                "currency": "usd",
                            }
                        },
                    },
                )["processed"]
                assert refund["amount_reversed_cents"] == 0
                assert refund["refund_unapplied_cents"] == 2000
                with pytest.raises(concurrent.futures.TimeoutError):
                    future.result(timeout=0.2)
            won = future.result(timeout=5)
    finally:
        refund_conn.close()
        won_conn.close()

    assert won["refund_reconciled"]["amount_reversed_cents"] == 2000
    assert won["remaining_reversed_cents"] == 2000
    assert app_payments.get_revenue_summary(pg_conn, slug)["amount_paid_cents"] == 0
    assert app_entitlements.get_active_entitlement(pg_conn, slug, user_id) is None


def test_partial_refund_then_disjoint_dispute_reverses_full_charge(pg_conn, monkeypatch):
    _stub_custody(monkeypatch)
    slug = "partial-refund-dispute-co"
    _seed_business(pg_conn, slug)
    _seed_paid_user(pg_conn, slug, "split@x.test", subscription_id="sub_split")
    app_payments.record_webhook_and_process(
        pg_conn,
        {
            "id": "evt_split_paid",
            "type": "invoice.paid",
            "created": 100,
            "data": {
                "object": {
                    "id": "in_split",
                    "subscription": "sub_split",
                    "billing_reason": "subscription_cycle",
                    "payment_intent": "pi_split",
                    "amount_paid": 10_000,
                    "currency": "usd",
                }
            },
        },
    )
    refund = app_payments.record_webhook_and_process(
        pg_conn,
        {
            "id": "evt_split_refund",
            "type": "charge.refunded",
            "created": 200,
            "data": {
                "object": {
                    "id": "ch_split",
                    "payment_intent": "pi_split",
                    "amount": 10_000,
                    "amount_refunded": 2000,
                    "currency": "usd",
                }
            },
        },
    )["processed"]
    dispute = app_payments.record_webhook_and_process(
        pg_conn,
        {
            "id": "evt_split_dispute",
            "type": "charge.dispute.funds_withdrawn",
            "created": 300,
            "data": {
                "object": {
                    "id": "du_split",
                    "status": "needs_response",
                    "charge": "ch_split",
                    "payment_intent": "pi_split",
                    "amount": 8000,
                    "_takyon_charge_gross_cents": 10_000,
                    "currency": "usd",
                }
            },
        },
    )["processed"]

    assert refund["amount_reversed_cents"] == 2000
    assert dispute["amount_reversed_cents"] == 8000
    assert app_payments.get_revenue_summary(pg_conn, slug)["amount_paid_cents"] == 0


def test_won_dispute_before_late_withdrawal_is_terminal(pg_conn, monkeypatch):
    _stub_custody(monkeypatch)
    slug = "won-before-withdrawal-co"
    _seed_business(pg_conn, slug)
    user_id = _seed_paid_user(
        pg_conn, slug, "late@x.test", subscription_id="sub_late_withdrawal"
    )
    app_payments.record_webhook_and_process(
        pg_conn,
        {
            "id": "evt_late_paid",
            "type": "invoice.paid",
            "created": 100,
            "data": {
                "object": {
                    "id": "in_late",
                    "subscription": "sub_late_withdrawal",
                    "billing_reason": "subscription_cycle",
                    "payment_intent": "pi_late",
                    "amount_paid": 2000,
                    "currency": "usd",
                }
            },
        },
    )
    won = {
        "id": "du_late",
        "status": "won",
        "charge": "ch_late",
        "payment_intent": "pi_late",
        "amount": 2000,
        "_takyon_charge_gross_cents": 2000,
        "currency": "usd",
    }
    app_payments.record_webhook_and_process(
        pg_conn,
        {
            "id": "evt_late_won_first",
            "type": "charge.dispute.closed",
            "created": 300,
            "data": {"object": won},
        },
    )
    late = app_payments.record_webhook_and_process(
        pg_conn,
        {
            "id": "evt_late_withdrawal_after_win",
            "type": "charge.dispute.funds_withdrawn",
            "created": 200,
            "data": {"object": {**won, "status": "needs_response"}},
        },
    )["processed"]

    assert late["terminal_dispute"] is True
    assert late["amount_reversed_cents"] == 0
    assert app_payments.get_revenue_summary(pg_conn, slug)["amount_paid_cents"] == 2000
    assert app_entitlements.get_active_entitlement(pg_conn, slug, user_id) is not None


def test_live_won_dispute_preserves_account_binding_and_releases(pg_conn, monkeypatch):
    _stub_custody(monkeypatch)
    slug = "live-won-dispute-co"
    account_id = "acct_live_won"
    _seed_business(pg_conn, slug)
    user_id = _seed_paid_user(
        pg_conn, slug, "live-won@x.test", subscription_id="sub_live_won"
    )
    app_payments.record_webhook_and_process(
        pg_conn,
        {
            "id": "evt_live_won_paid",
            "type": "invoice.paid",
            "created": 100,
            "data": {
                "object": {
                    "id": "in_live_won",
                    "subscription": "sub_live_won",
                    "billing_reason": "subscription_cycle",
                    "payment_intent": "pi_live_won",
                    "amount_paid": 2000,
                    "currency": "usd",
                }
            },
        },
    )
    pg_conn.execute(
        "update app_revenue_events set metadata = metadata || %s::jsonb "
        "where provider_event_id = %s",
        (
            '{"stripe_environment":"live",'
            '"takyon_stripe_account_id":"acct_live_won",'
            '"stripe_payment_allocations_cents":{'
            '"payment_intent":{"pi_live_won":2000},"charge":{}},'
            '"stripe_collected_cents":2000}',
            "evt_live_won_paid",
        ),
    )
    monkeypatch.setenv("TAKYON_STRIPE_MODE", "live")
    monkeypatch.setenv("TAKYON_STRIPE_ACCOUNT_ID", account_id)
    dispute = {
        "id": "du_livewon",
        "livemode": True,
        "charge": "ch_live_won",
        "payment_intent": "pi_live_won",
        "amount": 2000,
        "_takyon_charge_gross_cents": 2000,
        "_takyon_charge_amount_refunded_cents": 0,
        "currency": "usd",
        "status": "needs_response",
    }
    withdrawn = app_payments.record_webhook_and_process(
        pg_conn,
        {
            "id": "evt_live_won_withdrawn",
            "type": "charge.dispute.funds_withdrawn",
            "created": 200,
            "livemode": True,
            "data": {"object": dispute},
        },
    )["processed"]
    assert withdrawn["reversal_recorded"] is True
    reversal_binding = pg_conn.execute(
        "select metadata->>'takyon_stripe_account_id' from app_revenue_events "
        "where provider_event_id = %s",
        ("evt_live_won_withdrawn",),
    ).fetchone()[0]
    assert reversal_binding == account_id

    monkeypatch.setattr(
        app_payments.safebox,
        "release_custody_clawback",
        lambda *_args, **_kwargs: {
            "credited_cents": 1600,
            "owed_balance_cents": 1600,
            "replayed": False,
        },
        raising=False,
    )
    released = app_payments.record_webhook_and_process(
        pg_conn,
        {
            "id": "evt_live_won_closed",
            "type": "charge.dispute.closed",
            "created": 300,
            "livemode": True,
            "data": {
                "object": {
                    **dispute,
                    "status": "won",
                    "_takyon_subscription": {
                        "id": "sub_live_won",
                        "livemode": True,
                        "status": "active",
                        "customer": "cus_x",
                        "metadata": {
                            "source": "takyon_app",
                            "business": slug,
                            "takyon_stripe_account_id": account_id,
                        },
                    },
                }
            },
        },
    )["processed"]
    assert released["release_recorded"] is True
    release_binding = pg_conn.execute(
        "select metadata->>'takyon_stripe_account_id' from app_revenue_events "
        "where provider_event_id = %s",
        ("evt_live_won_closed",),
    ).fetchone()[0]
    assert release_binding == account_id
    assert app_entitlements.get_active_entitlement(pg_conn, slug, user_id) is not None


def test_live_lost_dispute_before_payment_stays_retryable(pg_conn, monkeypatch):
    monkeypatch.setenv("TAKYON_STRIPE_MODE", "live")
    monkeypatch.setenv("TAKYON_STRIPE_ACCOUNT_ID", "acct_live_ordering")
    event = {
        "id": "evt_lost_before_payment",
        "type": "charge.dispute.closed",
        "created": 300,
        "livemode": True,
        "data": {
            "object": {
                "id": "du_lostbeforepayment",
                "livemode": True,
                "status": "lost",
                "charge": "ch_lost_before_payment",
                "payment_intent": "pi_lost_before_payment",
                "amount": 2000,
                "_takyon_charge_gross_cents": 2000,
                "currency": "usd",
            }
        },
    }

    with pytest.raises(app_payments.RetryableWebhookEvent):
        app_payments.record_webhook_and_process(pg_conn, event)

    assert (
        pg_conn.execute(
            "select 1 from stripe_dispute_states where stripe_dispute_id = %s",
            ("du_lostbeforepayment",),
        ).fetchone()
        is None
    )
    assert (
        pg_conn.execute(
            "select 1 from webhook_events where provider = 'stripe' "
            "and provider_event_id = %s",
            ("evt_lost_before_payment",),
        ).fetchone()
        is None
    )


def test_live_won_dispute_reconciles_current_charge_refund_without_webhook(
    pg_conn, monkeypatch
):
    _stub_custody(monkeypatch)
    slug = "live-won-refunded-co"
    account_id = "acct_live_won_refunded"
    _seed_business(pg_conn, slug)
    user_id = _seed_paid_user(
        pg_conn, slug, "live-won-refunded@x.test", subscription_id="sub_livewonrefunded"
    )
    app_payments.record_webhook_and_process(
        pg_conn,
        {
            "id": "evt_live_won_refunded_paid",
            "type": "invoice.paid",
            "created": 100,
            "data": {
                "object": {
                    "id": "in_live_won_refunded",
                    "subscription": "sub_livewonrefunded",
                    "billing_reason": "subscription_cycle",
                    "payment_intent": "pi_livewonrefunded",
                    "amount_paid": 2000,
                    "currency": "usd",
                }
            },
        },
    )
    pg_conn.execute(
        "update app_revenue_events set metadata = metadata || %s::jsonb "
        "where provider_event_id = %s",
        (
            '{"stripe_environment":"live",'
            '"takyon_stripe_account_id":"acct_live_won_refunded",'
            '"stripe_payment_allocations_cents":{'
            '"payment_intent":{"pi_livewonrefunded":2000},"charge":{}},'
            '"stripe_collected_cents":2000}',
            "evt_live_won_refunded_paid",
        ),
    )
    monkeypatch.setenv("TAKYON_STRIPE_MODE", "live")
    monkeypatch.setenv("TAKYON_STRIPE_ACCOUNT_ID", account_id)
    dispute = {
        "id": "du_livewonrefunded",
        "livemode": True,
        "charge": "ch_livewonrefunded",
        "payment_intent": "pi_livewonrefunded",
        "amount": 2000,
        "_takyon_charge_gross_cents": 2000,
        "_takyon_charge_amount_refunded_cents": 0,
        "currency": "usd",
        "status": "needs_response",
    }
    app_payments.record_webhook_and_process(
        pg_conn,
        {
            "id": "evt_live_won_refunded_withdrawn",
            "type": "charge.dispute.funds_withdrawn",
            "created": 200,
            "livemode": True,
            "data": {"object": dispute},
        },
    )
    monkeypatch.setattr(
        app_payments.safebox,
        "release_custody_clawback",
        lambda *_args, **_kwargs: {
            "credited_cents": 1600,
            "owed_balance_cents": 1600,
            "replayed": False,
        },
        raising=False,
    )

    won = app_payments.record_webhook_and_process(
        pg_conn,
        {
            "id": "evt_live_won_refunded_closed",
            "type": "charge.dispute.closed",
            "created": 300,
            "livemode": True,
            "data": {
                "object": {
                    **dispute,
                    "status": "won",
                    "_takyon_charge_amount_refunded_cents": 2000,
                    "_takyon_subscription": {
                        "id": "sub_livewonrefunded",
                        "livemode": True,
                        "status": "active",
                        "customer": "cus_x",
                        "metadata": {
                            "source": "takyon_app",
                            "business": slug,
                            "takyon_stripe_account_id": account_id,
                        },
                    },
                }
            },
        },
    )["processed"]

    assert won["amount_released_cents"] == 2000
    assert won["refund_reconciled"]["amount_reversed_cents"] == 2000
    assert won["remaining_reversed_cents"] == 2000
    assert app_payments.get_revenue_summary(pg_conn, slug)["amount_paid_cents"] == 0
    assert app_entitlements.get_active_entitlement(pg_conn, slug, user_id) is None


def test_current_funds_reinstated_can_override_terminal_lost(pg_conn, monkeypatch):
    _stub_custody(monkeypatch)
    slug = "lost-then-won-co"
    _seed_business(pg_conn, slug)
    user_id = _seed_paid_user(
        pg_conn, slug, "reinstated@x.test", subscription_id="sub_reinstated"
    )
    app_payments.record_webhook_and_process(
        pg_conn,
        {
            "id": "evt_reinstated_paid",
            "type": "invoice.paid",
            "created": 100,
            "data": {
                "object": {
                    "id": "in_reinstated",
                    "subscription": "sub_reinstated",
                    "billing_reason": "subscription_cycle",
                    "payment_intent": "pi_reinstated",
                    "amount_paid": 2000,
                    "currency": "usd",
                }
            },
        },
    )
    dispute = {
        "id": "du_reinstated",
        "charge": "ch_reinstated",
        "payment_intent": "pi_reinstated",
        "amount": 2000,
        "_takyon_charge_gross_cents": 2000,
        "currency": "usd",
        "status": "needs_response",
    }
    app_payments.record_webhook_and_process(
        pg_conn,
        {
            "id": "evt_reinstated_created",
            "type": "charge.dispute.created",
            "created": 200,
            "data": {"object": dispute},
        },
    )
    app_payments.record_webhook_and_process(
        pg_conn,
        {
            "id": "evt_reinstated_lost",
            "type": "charge.dispute.closed",
            "created": 300,
            "data": {"object": {**dispute, "status": "lost"}},
        },
    )
    monkeypatch.setattr(
        app_payments.safebox,
        "release_custody_clawback",
        lambda *_args, **_kwargs: {
            "credited_cents": 1600,
            "owed_balance_cents": 1600,
            "replayed": False,
        },
        raising=False,
    )
    reinstated = app_payments.record_webhook_and_process(
        pg_conn,
        {
            "id": "evt_reinstated_won",
            "type": "charge.dispute.funds_reinstated",
            "created": 400,
            "data": {
                "object": {
                    **dispute,
                    "status": "won",
                    "_takyon_subscription": {
                        "id": "sub_reinstated",
                        "status": "active",
                        "customer": "cus_x",
                    },
                }
            },
        },
    )["processed"]

    assert reinstated["release_recorded"] is True
    assert app_payments.get_revenue_summary(pg_conn, slug)["amount_paid_cents"] == 2000
    assert app_entitlements.get_active_entitlement(pg_conn, slug, user_id) is not None
