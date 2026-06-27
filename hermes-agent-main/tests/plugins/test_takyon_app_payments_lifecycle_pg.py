"""Subscription-lifecycle webhook handlers added to app_payments: invoice.paid (renewal revenue +
entitlement refresh), invoice.payment_failed (dunning, no revocation), and charge.refunded /
charge.dispute.created (revoke access + reversal revenue, netted out of the summary).

Owner custody accrual goes through the safebox authority (open_custody_account / accrue_custody);
these tests STUB that one call so the revenue-ledger + entitlement logic under test is exercised
without the safebox billing seam. PG-gated like the sibling suites (skips without TAKYON_TEST_PG_DSN).
"""

import uuid

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


def _ent(conn, slug, user_id):
    return app_entitlements.list_entitlements(conn, slug, app_user_id=user_id)[0]


def test_invoice_paid_renewal_records_revenue_and_clears_dunning(pg_conn, monkeypatch):
    _stub_custody(monkeypatch)
    slug = "renew-co"
    _seed_business(pg_conn, slug)
    user_id = _seed_paid_user(pg_conn, slug, "a@x.test", subscription_id="sub_1")

    # a prior failed attempt marks dunning but must not revoke access
    app_payments.record_webhook_and_process(pg_conn, {
        "id": "evt_fail_1", "type": "invoice.payment_failed",
        "data": {"object": {"id": "in_0", "subscription": "sub_1"}},
    })
    assert app_entitlements.get_active_entitlement(pg_conn, slug, user_id) is not None
    assert _ent(pg_conn, slug, user_id).metadata.get("dunning") is True

    res = app_payments.record_webhook_and_process(pg_conn, {
        "id": "evt_inv_1", "type": "invoice.paid", "created": 1_700_000_000,
        "data": {"object": {
            "id": "in_1", "subscription": "sub_1", "customer": "cus_x",
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
                            "amount_paid": 2000, "currency": "usd"}},
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
