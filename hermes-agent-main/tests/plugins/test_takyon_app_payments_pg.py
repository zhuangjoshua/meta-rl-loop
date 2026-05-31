"""Postgres integration tests for product checkout + Stripe webhook + revenue + the net-new
owner->custody accrual — Phase 5(d).

Phase 5 acceptance (this slice): "sub-user payment shows in owner custody". The correctness
this pins:
  * a paid checkout.session.completed records the session + a revenue event, grants the paying
    sub-user a paid entitlement (with Stripe evidence), AND accrues the gross minus the platform
    app fee into the business OWNER's custody ledger (flow B) — the thing the SQLite product path
    never did;
  * the webhook is processed AT MOST ONCE per provider event id, even under concurrent
    redelivery (the SQLite plain-INSERT entitlement path would double-grant) — the single-row
    lock on webhook_events is the gate;
  * checkout intents are idempotent on client_reference_id;
  * subscription lifecycle events flip entitlement status (cancel -> tier drops to free);
  * non-paid / zero-amount / unknown-intent / ignored events are consumed without faking revenue
    or accrual.

Real engine on real Postgres (never mocks). Skips unless psycopg is importable and
TAKYON_TEST_PG_DSN is set.
"""

from __future__ import annotations

import os
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest

psycopg = pytest.importorskip("psycopg")

from plugins.takyon import app_entitlements, app_identity, app_payments, custody  # noqa: E402
from plugins.takyon.app_payments import (  # noqa: E402
    BusinessOwnerMissing,
    CheckoutIntentNotFound,
    InvalidWebhookEvent,
)
from plugins.takyon.control_plane import provision_user_on_first_login  # noqa: E402


def _sub() -> str:
    return f"auth0|{uuid.uuid4().hex}"


def _owner(conn) -> str:
    uid, _, _ = provision_user_on_first_login(conn, _sub())
    return uid


def _business(conn, owner_id, name="Acme") -> str:
    slug = f"biz-{uuid.uuid4().hex[:8]}"
    conn.execute(
        "insert into businesses (slug, name, owner_user_id) values (%s, %s, %s)",
        (slug, name, owner_id),
    )
    return slug


def _new_conn(pg_conn):
    """A fresh autocommit connection to the SAME throwaway DB — for real concurrency."""
    return psycopg.connect(
        os.environ["TAKYON_TEST_PG_DSN"], dbname=pg_conn.info.dbname, autocommit=True
    )


def _expected_net(gross: int) -> int:
    """Net accrued to the owner after the platform app fee — computed from the live config so the
    test is correct regardless of STRIPE_CONNECT_APPLICATION_FEE_BPS."""
    return gross - (gross * custody.app_fee_bps()) // 10000


def _checkout_event(
    *,
    event_id: str,
    session_id: str,
    intent_id: str | None = None,
    client_reference_id: str | None = None,
    email: str | None = "cust@example.com",
    amount_total: int = 1000,
    payment_status: str = "paid",
    currency: str = "usd",
    subscription: str | None = None,
    customer: str | None = "cus_123",
    created: int = 1_700_000_000,
    mode: str = "payment",
) -> dict:
    return {
        "id": event_id,
        "type": "checkout.session.completed",
        "created": created,
        "data": {
            "object": {
                "id": session_id,
                "object": "checkout.session",
                "mode": mode,
                "payment_status": payment_status,
                "status": "complete",
                "currency": currency,
                "amount_subtotal": amount_total,
                "amount_total": amount_total,
                "customer": customer,
                "subscription": subscription,
                "customer_details": {"email": email} if email else {},
                "customer_email": email,
                "client_reference_id": client_reference_id,
                "metadata": {"checkout_intent_id": intent_id} if intent_id else {},
            }
        },
    }


def _subscription_event(
    *,
    event_id: str,
    subscription_id: str,
    status: str = "canceled",
    customer: str | None = "cus_123",
    created: int = 1_700_000_100,
    event_type: str = "customer.subscription.updated",
    current_period_end: int = 1_700_600_000,
) -> dict:
    return {
        "id": event_id,
        "type": event_type,
        "created": created,
        "data": {
            "object": {
                "id": subscription_id,
                "object": "subscription",
                "status": status,
                "customer": customer,
                "current_period_end": current_period_end,
                "cancel_at_period_end": status in {"canceled", "cancelled"},
            }
        },
    }


# ── checkout intents ─────────────────────────────────────────────────────────────────


def test_create_checkout_intent_records_created(pg_conn):
    slug = _business(pg_conn, _owner(pg_conn))
    intent = app_payments.create_checkout_intent(
        pg_conn, slug, plan_key="pro", client_reference_id="ref-1", customer_email="a@x.com"
    )
    assert intent.business_slug == slug
    assert intent.plan_key == "pro"
    assert intent.status == "created"
    assert intent.client_reference_id == "ref-1"
    assert intent.stripe_checkout_session_id is None


def test_create_checkout_intent_idempotent_on_client_reference(pg_conn):
    slug = _business(pg_conn, _owner(pg_conn))
    first = app_payments.create_checkout_intent(
        pg_conn, slug, plan_key="pro", client_reference_id="ref-dup"
    )
    # A replay with a DIFFERENT plan_key returns the ORIGINAL intent — the key can't fork.
    second = app_payments.create_checkout_intent(
        pg_conn, slug, plan_key="enterprise", client_reference_id="ref-dup"
    )
    assert second.id == first.id
    assert second.plan_key == "pro"


def test_create_checkout_intent_unknown_business_fails_loud(pg_conn):
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        app_payments.create_checkout_intent(
            pg_conn, "no-such-biz", plan_key="pro", client_reference_id="ref-x"
        )


def test_create_checkout_intent_requires_fields(pg_conn):
    slug = _business(pg_conn, _owner(pg_conn))
    with pytest.raises(ValueError):
        app_payments.create_checkout_intent(pg_conn, slug, plan_key="", client_reference_id="r")
    with pytest.raises(ValueError):
        app_payments.create_checkout_intent(pg_conn, slug, plan_key="pro", client_reference_id="")


def test_attach_checkout_session_links_and_advances(pg_conn):
    slug = _business(pg_conn, _owner(pg_conn))
    intent = app_payments.create_checkout_intent(
        pg_conn, slug, plan_key="pro", client_reference_id="ref-a"
    )
    linked = app_payments.attach_checkout_session(
        pg_conn,
        intent_id=intent.id,
        stripe_checkout_session_id="cs_a",
        checkout_url="https://stripe/cs_a",
    )
    assert linked.id == intent.id
    assert linked.status == "pending"
    assert linked.stripe_checkout_session_id == "cs_a"
    assert linked.checkout_url == "https://stripe/cs_a"


def test_attach_checkout_session_by_client_reference(pg_conn):
    slug = _business(pg_conn, _owner(pg_conn))
    app_payments.create_checkout_intent(
        pg_conn, slug, plan_key="pro", client_reference_id="ref-b"
    )
    linked = app_payments.attach_checkout_session(
        pg_conn, client_reference_id="ref-b", stripe_checkout_session_id="cs_b"
    )
    assert linked.stripe_checkout_session_id == "cs_b"


def test_attach_checkout_session_unknown_raises(pg_conn):
    with pytest.raises(CheckoutIntentNotFound):
        app_payments.attach_checkout_session(
            pg_conn, client_reference_id="missing", stripe_checkout_session_id="cs_z"
        )


def test_get_checkout_intent_by_id_and_ref_and_none(pg_conn):
    slug = _business(pg_conn, _owner(pg_conn))
    intent = app_payments.create_checkout_intent(
        pg_conn, slug, plan_key="pro", client_reference_id="ref-g"
    )
    assert app_payments.get_checkout_intent(pg_conn, slug, intent_id=intent.id).id == intent.id
    assert (
        app_payments.get_checkout_intent(pg_conn, slug, client_reference_id="ref-g").id
        == intent.id
    )
    assert app_payments.get_checkout_intent(pg_conn, slug, client_reference_id="nope") is None


# ── paid checkout → revenue + entitlement + OWNER ACCRUAL (the acceptance) ─────────────


def test_paid_checkout_accrues_to_owner_custody(pg_conn):
    owner = _owner(pg_conn)
    slug = _business(pg_conn, owner)
    intent = app_payments.create_checkout_intent(
        pg_conn, slug, plan_key="pro", client_reference_id="ref-pay", customer_email="c@x.com"
    )
    result = app_payments.record_webhook_and_process(
        pg_conn,
        _checkout_event(
            event_id="evt_1",
            session_id="cs_1",
            intent_id=intent.id,
            email="c@x.com",
            amount_total=1000,
            subscription="sub_1",
        ),
    )
    proc = result["processed"]
    assert result["deduplicated"] is False
    assert proc["recorded"] is True
    assert proc["revenue_recorded"] is True
    assert proc["accrued_to_owner"] is True
    assert proc["owner_user_id"] == owner

    # revenue ledger
    summary = app_payments.get_revenue_summary(pg_conn, slug)
    assert summary == {"business_slug": slug, "amount_paid_cents": 1000, "events": 1}

    # OWNER custody owed = gross - app fee
    assert custody.get_custody_balances(pg_conn, owner).owed_balance_cents == _expected_net(1000)
    assert custody.reconcile_custody(pg_conn, owner)["ok"] is True

    # paying sub-user got a paid entitlement carrying Stripe evidence
    ents = app_entitlements.list_entitlements(pg_conn, slug)
    assert len(ents) == 1
    assert ents[0].tier == "paid"
    assert ents[0].source == "stripe"
    assert ents[0].stripe_subscription_id == "sub_1"
    assert ents[0].stripe_checkout_session_id == "cs_1"

    user = app_identity.get_app_user(pg_conn, slug, email="c@x.com")
    assert app_entitlements.resolve_user_tier(pg_conn, slug, user.id) == "paid"


def test_owner_accrual_nets_exact_app_fee(pg_conn):
    owner = _owner(pg_conn)
    slug = _business(pg_conn, owner)
    intent = app_payments.create_checkout_intent(
        pg_conn, slug, plan_key="pro", client_reference_id="ref-fee", customer_email="f@x.com"
    )
    result = app_payments.record_webhook_and_process(
        pg_conn,
        _checkout_event(event_id="evt_fee", session_id="cs_fee", intent_id=intent.id,
                        email="f@x.com", amount_total=5000),
    )
    assert result["processed"]["owner_owed_balance_cents"] == _expected_net(5000)
    # default fee is 20% → owner keeps 80%
    bps = custody.app_fee_bps()
    entries = pg_conn.execute(
        "select gross_cents, fee_cents, net_cents from custody_entries "
        "where user_id = %s and kind = 'accrual'",
        (owner,),
    ).fetchall()
    assert entries == [(5000, (5000 * bps) // 10000, 5000 - (5000 * bps) // 10000)]


def test_webhook_idempotent_on_replay(pg_conn):
    owner = _owner(pg_conn)
    slug = _business(pg_conn, owner)
    intent = app_payments.create_checkout_intent(
        pg_conn, slug, plan_key="pro", client_reference_id="ref-rp", customer_email="r@x.com"
    )
    event = _checkout_event(event_id="evt_rp", session_id="cs_rp", intent_id=intent.id,
                            email="r@x.com", amount_total=2000, subscription="sub_rp")
    first = app_payments.record_webhook_and_process(pg_conn, event)
    second = app_payments.record_webhook_and_process(pg_conn, event)
    assert first["deduplicated"] is False
    assert second["deduplicated"] is True
    assert second["processed"] is None
    # exactly one of everything; owed balance not doubled
    assert app_payments.get_revenue_summary(pg_conn, slug)["events"] == 1
    assert len(app_entitlements.list_entitlements(pg_conn, slug)) == 1
    assert custody.get_custody_balances(pg_conn, owner).owed_balance_cents == _expected_net(2000)


def test_paid_checkout_without_email_accrues_but_no_entitlement(pg_conn):
    owner = _owner(pg_conn)
    slug = _business(pg_conn, owner)
    intent = app_payments.create_checkout_intent(
        pg_conn, slug, plan_key="pro", client_reference_id="ref-noem"
    )
    result = app_payments.record_webhook_and_process(
        pg_conn,
        _checkout_event(event_id="evt_noem", session_id="cs_noem", intent_id=intent.id,
                        email=None, amount_total=1200),
    )
    assert result["processed"]["app_user_id"] is None
    assert result["processed"]["accrued_to_owner"] is True
    assert app_payments.get_revenue_summary(pg_conn, slug)["amount_paid_cents"] == 1200
    assert custody.get_custody_balances(pg_conn, owner).owed_balance_cents == _expected_net(1200)
    assert app_entitlements.list_entitlements(pg_conn, slug) == []


def test_unpaid_checkout_records_session_but_no_revenue_or_accrual(pg_conn):
    owner = _owner(pg_conn)
    slug = _business(pg_conn, owner)
    intent = app_payments.create_checkout_intent(
        pg_conn, slug, plan_key="pro", client_reference_id="ref-unpaid", customer_email="u@x.com"
    )
    result = app_payments.record_webhook_and_process(
        pg_conn,
        _checkout_event(event_id="evt_unpaid", session_id="cs_unpaid", intent_id=intent.id,
                        email="u@x.com", amount_total=900, payment_status="unpaid"),
    )
    proc = result["processed"]
    assert proc["recorded"] is True
    assert proc["revenue_recorded"] is False
    assert proc["accrued_to_owner"] is False
    assert app_payments.get_revenue_summary(pg_conn, slug)["events"] == 0
    assert custody.get_custody_balances(pg_conn, owner).owed_balance_cents == 0
    assert app_entitlements.list_entitlements(pg_conn, slug) == []
    # the session row is still recorded (we saw it)
    sessions = pg_conn.execute(
        "select payment_status from app_checkout_sessions where business_slug = %s", (slug,)
    ).fetchall()
    assert sessions == [("unpaid",)]
    # the intent is marked completed
    assert app_payments.get_checkout_intent(pg_conn, slug, intent_id=intent.id).status == "completed"


def test_zero_amount_paid_records_revenue_but_no_accrual(pg_conn):
    owner = _owner(pg_conn)
    slug = _business(pg_conn, owner)
    intent = app_payments.create_checkout_intent(
        pg_conn, slug, plan_key="free", client_reference_id="ref-zero", customer_email="z@x.com"
    )
    result = app_payments.record_webhook_and_process(
        pg_conn,
        _checkout_event(event_id="evt_zero", session_id="cs_zero", intent_id=intent.id,
                        email="z@x.com", amount_total=0),
    )
    proc = result["processed"]
    assert proc["revenue_recorded"] is True
    assert proc["accrued_to_owner"] is False
    assert app_payments.get_revenue_summary(pg_conn, slug)["amount_paid_cents"] == 0
    assert custody.get_custody_balances(pg_conn, owner).owed_balance_cents == 0


def test_checkout_missing_intent_is_consumed_without_effect(pg_conn):
    _business(pg_conn, _owner(pg_conn))  # a business exists, but the event references no intent
    result = app_payments.record_webhook_and_process(
        pg_conn,
        _checkout_event(event_id="evt_noint", session_id="cs_noint", intent_id=None,
                        client_reference_id=None),
    )
    assert result["processed"] == {"recorded": False, "reason": "missing_checkout_intent"}
    # event was still consumed (processed_at set) so a replay dedups
    assert _processed_at(pg_conn, "evt_noint") is not None


# ── subscription lifecycle ─────────────────────────────────────────────────────────────


def test_subscription_cancel_drops_tier_to_free(pg_conn):
    owner = _owner(pg_conn)
    slug = _business(pg_conn, owner)
    intent = app_payments.create_checkout_intent(
        pg_conn, slug, plan_key="pro", client_reference_id="ref-sub", customer_email="s@x.com"
    )
    app_payments.record_webhook_and_process(
        pg_conn,
        _checkout_event(event_id="evt_s1", session_id="cs_s1", intent_id=intent.id,
                        email="s@x.com", amount_total=1500, subscription="sub_active"),
    )
    user = app_identity.get_app_user(pg_conn, slug, email="s@x.com")
    assert app_entitlements.resolve_user_tier(pg_conn, slug, user.id) == "paid"

    res = app_payments.record_webhook_and_process(
        pg_conn,
        _subscription_event(event_id="evt_s2", subscription_id="sub_active", status="canceled",
                            event_type="customer.subscription.deleted"),
    )
    assert res["processed"]["recorded"] is True
    assert res["processed"]["updated"][0]["tier"] == "free"
    assert app_entitlements.resolve_user_tier(pg_conn, slug, user.id) == "free"


def test_subscription_event_for_unknown_subscription_is_noop(pg_conn):
    _business(pg_conn, _owner(pg_conn))
    res = app_payments.record_webhook_and_process(
        pg_conn,
        _subscription_event(event_id="evt_unk", subscription_id="sub_unknown", status="active"),
    )
    assert res["processed"] == {"recorded": False, "updated": []}


# ── webhook plumbing ────────────────────────────────────────────────────────────────────


def test_ignored_event_type_is_consumed(pg_conn):
    res = app_payments.record_webhook_and_process(
        pg_conn, {"id": "evt_ig", "type": "invoice.paid", "data": {"object": {"id": "in_1"}}}
    )
    assert res["processed"] == {"recorded": False, "ignored": "invoice.paid"}
    assert _processed_at(pg_conn, "evt_ig") is not None


def test_event_without_id_raises(pg_conn):
    with pytest.raises(InvalidWebhookEvent):
        app_payments.record_webhook_and_process(pg_conn, {"type": "checkout.session.completed"})


def test_list_revenue_events_newest_first(pg_conn):
    owner = _owner(pg_conn)
    slug = _business(pg_conn, owner)
    for i, ts in enumerate((1_700_000_000, 1_700_000_500, 1_700_000_900)):
        intent = app_payments.create_checkout_intent(
            pg_conn, slug, plan_key="pro", client_reference_id=f"ref-l{i}", customer_email="l@x.com"
        )
        app_payments.record_webhook_and_process(
            pg_conn,
            _checkout_event(event_id=f"evt_l{i}", session_id=f"cs_l{i}", intent_id=intent.id,
                            email="l@x.com", amount_total=100 + i, created=ts),
        )
    events = app_payments.list_revenue_events(pg_conn, slug)
    assert [e.stripe_object_id for e in events] == ["cs_l2", "cs_l1", "cs_l0"]


# ── concurrency: the SQLite double-grant bug cannot happen here ─────────────────────────


def test_concurrent_identical_webhook_processes_exactly_once(pg_conn):
    owner = _owner(pg_conn)
    slug = _business(pg_conn, owner)
    intent = app_payments.create_checkout_intent(
        pg_conn, slug, plan_key="pro", client_reference_id="ref-conc", customer_email="cc@x.com"
    )
    event = _checkout_event(event_id="evt_conc", session_id="cs_conc", intent_id=intent.id,
                            email="cc@x.com", amount_total=1000, subscription="sub_conc")

    n = 8
    barrier = threading.Barrier(n)

    def worker():
        conn = _new_conn(pg_conn)
        try:
            barrier.wait()
            return app_payments.record_webhook_and_process(conn, event)
        finally:
            conn.close()

    with ThreadPoolExecutor(max_workers=n) as ex:
        results = [f.result() for f in [ex.submit(worker) for _ in range(n)]]

    processed = [r for r in results if not r["deduplicated"]]
    deduped = [r for r in results if r["deduplicated"]]
    assert len(processed) == 1
    assert len(deduped) == n - 1
    # exactly one revenue event, one entitlement (SQLite's plain INSERT would append duplicates),
    # owner owed accrued exactly once
    assert app_payments.get_revenue_summary(pg_conn, slug)["events"] == 1
    assert len(app_entitlements.list_entitlements(pg_conn, slug)) == 1
    assert custody.get_custody_balances(pg_conn, owner).owed_balance_cents == _expected_net(1000)
    assert custody.reconcile_custody(pg_conn, owner)["ok"] is True


# ── the LIVE tool entrypoint (core handler), not just the leaf ──────────────────────────
# Regression for the flow-B wiring hole: business_record_stripe_webhook (core.handle_business_
# record_stripe_webhook) is the serving-path entry the CEO/skills and the product webhook route
# actually call. On the SQLite era it routed to an accrual-free handler, so a sub-user payment
# reconciled but NEVER reached the owner's custody. This drives the real tool against Postgres and
# proves the owner custody balance moves through the tool — not only through a direct leaf call.


def test_record_stripe_webhook_tool_accrues_to_owner_custody(pg_conn, tmp_path, monkeypatch):
    import json

    from psycopg.conninfo import make_conninfo

    from plugins.takyon import core as takyon_core

    owner = _owner(pg_conn)
    slug = _business(pg_conn, owner)
    intent = app_payments.create_checkout_intent(
        pg_conn, slug, plan_key="pro", client_reference_id="ref-tool", customer_email="t@x.com"
    )

    # The tool calls _store() internally; point that store at THIS test's throwaway DB (the rows we
    # just committed over the autocommit pg_conn are visible to the store's own connection). Neutralize
    # the on-disk .env load and pin the backend, mirroring the worker/store PG tests.
    dsn = make_conninfo(os.environ["TAKYON_TEST_PG_DSN"], dbname=pg_conn.info.dbname)
    store = takyon_core.TakyonStore(root=tmp_path, database_url=dsn)
    monkeypatch.setattr(takyon_core, "load_takyon_env", lambda *a, **k: None)
    monkeypatch.setattr(takyon_core, "_store", lambda: store)
    monkeypatch.setenv("TAKYON_DB_BACKEND", "postgres")

    raw = takyon_core.handle_business_record_stripe_webhook(
        {
            "event": _checkout_event(
                event_id="evt_tool", session_id="cs_tool", intent_id=intent.id,
                email="t@x.com", amount_total=4000, subscription="sub_tool",
            )
        }
    )
    payload = json.loads(raw)
    assert payload["success"] is True
    assert payload["type"] == "checkout.session.completed"
    proc = payload["processed"]
    assert proc["accrued_to_owner"] is True
    assert proc["owner_user_id"] == owner

    # The headline: the owner's custody balance actually moved THROUGH THE TOOL (gross - app fee),
    # and revenue was recorded — the exact thing the legacy SQLite tool path never did.
    assert custody.get_custody_balances(pg_conn, owner).owed_balance_cents == _expected_net(4000)
    assert app_payments.get_revenue_summary(pg_conn, slug)["amount_paid_cents"] == 4000


def test_record_stripe_webhook_tool_dedups_on_replay(pg_conn, tmp_path, monkeypatch):
    # The tool inherits the leaf's at-most-once processing: a replayed event id accrues exactly once.
    import json

    from psycopg.conninfo import make_conninfo

    from plugins.takyon import core as takyon_core

    owner = _owner(pg_conn)
    slug = _business(pg_conn, owner)
    intent = app_payments.create_checkout_intent(
        pg_conn, slug, plan_key="pro", client_reference_id="ref-tool-rp", customer_email="rp@x.com"
    )
    dsn = make_conninfo(os.environ["TAKYON_TEST_PG_DSN"], dbname=pg_conn.info.dbname)
    store = takyon_core.TakyonStore(root=tmp_path, database_url=dsn)
    monkeypatch.setattr(takyon_core, "load_takyon_env", lambda *a, **k: None)
    monkeypatch.setattr(takyon_core, "_store", lambda: store)
    monkeypatch.setenv("TAKYON_DB_BACKEND", "postgres")

    event = _checkout_event(
        event_id="evt_tool_rp", session_id="cs_tool_rp", intent_id=intent.id,
        email="rp@x.com", amount_total=3000,
    )
    first = json.loads(takyon_core.handle_business_record_stripe_webhook({"event": event}))
    second = json.loads(takyon_core.handle_business_record_stripe_webhook({"event": event}))
    assert first["success"] is True and second["success"] is True
    assert first["processed"]["accrued_to_owner"] is True
    assert second["processed"] is None  # deduplicated → leaf returned processed=None
    # accrued exactly once despite two tool calls
    assert custody.get_custody_balances(pg_conn, owner).owed_balance_cents == _expected_net(3000)
    assert app_payments.get_revenue_summary(pg_conn, slug)["events"] == 1


def _processed_at(conn, event_id: str):
    row = conn.execute(
        "select processed_at from webhook_events where provider = 'stripe' and provider_event_id = %s",
        (event_id,),
    ).fetchone()
    return None if row is None else row[0]
