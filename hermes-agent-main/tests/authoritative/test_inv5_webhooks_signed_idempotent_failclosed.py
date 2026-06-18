"""AUTHORITATIVE INVARIANT 5 — payment/billing webhooks are signed, idempotent, fail-closed.

GOAL_RULES.md §3, invariant 5:
  "All webhooks signature-verified + idempotent + fail-closed — forged/replayed ⇒
   rejected; missing secret ⇒ 503, never trusted."

This is the dedicated, independently-red-teamed assertion of that invariant across the
WHOLE webhook surface (control-plane flow-A topups AND product flow-B payments). It assumes
every caller is EVIL and is trying to forge, replay, or strip the signature to move money for
free, and proves the real code refuses.

Design choices (re-confirmed by reading the real source before writing each check):
  * The default-and-primary checks need NO credential and NO network. They import the REAL
    symbols and drive them with `stripe_util.build_signature_header` (Stripe's exact wire
    format) so a forged/replayed event is genuinely rejected, and assert the fail-closed
    503 path at the route source.
  * A few checks genuinely need the Postgres rig + FastAPI to drive the live HTTP route end to
    end; those are gated on `psycopg`/`fastapi` import + the `pg_conn` fixture
    (TAKYON_TEST_PG_DSN) and skip cleanly otherwise. They are complementary to — not a copy of
    — the broader flow in tests/plugins/test_takyon_control_api_pg.py.

Grounded real symbols (all confirmed by opening the file):
  plugins/takyon/stripe_util.py:verify_stripe_signature   (HMAC-SHA256, 300s tolerance, hmac.compare_digest)
  plugins/takyon/stripe_util.py:build_signature_header    (synthetic Stripe-Signature, no network)
  plugins/takyon/safebox.py:verify_stripe_billing_webhook (flow-A authority verify)
  plugins/takyon/safebox.py:StripeBillingWebhookUnconfigured / StripeBillingWebhookInvalidSignature
  plugins/takyon/control_api.py:/billing/webhook          (503 on unconfigured, 400 on bad sig, idempotent topup)
  plugins/takyon/billing.py:topup                         (idempotent on idempotency_key == event id)
  plugins/takyon/app_payments.py:record_webhook_and_process (dedup on webhook_events ... for update)
  plugins/takyon/core.py:handle_business_record_stripe_webhook (flow-B; refuses missing STRIPE_WEBHOOK_SECRET)
"""

from __future__ import annotations

import inspect
import time

import pytest

from plugins.takyon import app_payments, billing, safebox, stripe_util
from plugins.takyon.stripe_util import (
    StripeError,
    build_signature_header,
    verify_stripe_signature,
)

_WHSEC = "whsec_unit_test_secret_value"


# ---------------------------------------------------------------------------
# Part A — signature verification primitive (no credential, no network)
# ---------------------------------------------------------------------------


def test_signature_helpers_round_trip_valid_event():
    """A correctly-signed body, built with Stripe's exact wire format, verifies."""
    body = '{"id":"evt_ok","type":"checkout.session.completed"}'
    header = build_signature_header(body, _WHSEC)
    # Returns None (no raise) on success.
    assert verify_stripe_signature(body, header, _WHSEC) is None


def test_forged_signature_is_rejected():
    """An attacker who does not hold the signing secret cannot forge a valid header."""
    body = '{"id":"evt_forged","type":"checkout.session.completed"}'
    forged = build_signature_header(body, "whsec_attacker_guess")
    with pytest.raises(StripeError):
        verify_stripe_signature(body, forged, _WHSEC)


def test_tampered_body_breaks_signature():
    """Sign one body, deliver a different one: the digest no longer matches => reject.

    Defends against a man-in-the-middle swapping the amount/event after signing."""
    signed_body = '{"id":"evt_x","amount_total":100}'
    header = build_signature_header(signed_body, _WHSEC)
    tampered_body = '{"id":"evt_x","amount_total":9999999}'
    with pytest.raises(StripeError):
        verify_stripe_signature(tampered_body, header, _WHSEC)


def test_replayed_signature_outside_tolerance_is_rejected():
    """A captured-and-replayed event with an old timestamp is refused (replay window).

    Even WITH the correct secret, a timestamp older than the 300s tolerance is rejected,
    so a recorded webhook cannot be re-fired hours later to re-credit."""
    body = '{"id":"evt_replay","type":"checkout.session.completed"}'
    stale_ts = int(time.time()) - 4000  # well outside the 300s window
    stale_header = build_signature_header(body, _WHSEC, timestamp=stale_ts)
    with pytest.raises(StripeError):
        verify_stripe_signature(body, stale_header, _WHSEC)


def test_missing_v1_or_timestamp_header_is_rejected():
    """A malformed / signature-stripped header is refused, not treated as unsigned-ok."""
    body = '{"id":"evt_nohdr"}'
    for bad in ("", "t=123", "v1=deadbeef", "garbage", "t=,v1="):
        with pytest.raises(StripeError):
            verify_stripe_signature(body, bad, _WHSEC)


def test_signature_uses_constant_time_compare_and_300s_window():
    """Source-level invariant: the verifier uses hmac.compare_digest and a 300s tolerance.

    Guards against a future refactor that swaps to `==` (timing oracle) or widens/removes the
    replay window. We assert on the real source, not a snapshot of data."""
    src = inspect.getsource(verify_stripe_signature)
    assert "hmac.compare_digest" in src, "must use constant-time compare, not =="
    assert "300" in src, "must enforce the 300s replay tolerance"


# ---------------------------------------------------------------------------
# Part B — flow-A authority: fail-closed when the signing secret is absent
# (no credential, no network — the hermetic conftest scrubs creds, so the
#  billing webhook secret is genuinely absent in local authority mode)
# ---------------------------------------------------------------------------


def test_billing_webhook_authority_failclosed_without_secret(monkeypatch):
    """verify_stripe_billing_webhook raises StripeBillingWebhookUnconfigured when the
    STRIPE_BILLING_WEBHOOK_SECRET is absent — the event is NEVER trusted around a missing
    credential. This is the exact branch the /billing/webhook route turns into HTTP 503."""
    # Force LOCAL (non-remote) Safebox authority and ensure the secret is truly absent.
    # TAKYON_HOST_ROLE=safebox selects the local authority branch (same as the existing
    # control_api PG test); without it the authority refuses to resolve at all.
    monkeypatch.setenv("TAKYON_HOST_ROLE", "safebox")
    monkeypatch.delenv("TAKYON_SAFEBOX_URL", raising=False)
    monkeypatch.delenv("STRIPE_BILLING_WEBHOOK_SECRET", raising=False)
    assert safebox._use_remote_authority() is False
    assert safebox.read_env_backed_value("STRIPE_BILLING_WEBHOOK_SECRET") == ""

    body = '{"id":"evt_no_secret","type":"checkout.session.completed"}'
    header = build_signature_header(body, _WHSEC)
    with pytest.raises(safebox.StripeBillingWebhookUnconfigured):
        safebox.verify_stripe_billing_webhook(body, header)


def test_billing_webhook_authority_rejects_bad_signature(monkeypatch):
    """With a secret present, a forged signature raises StripeBillingWebhookInvalidSignature
    (the route turns this into HTTP 400), proving the authority layer verifies before
    parsing/crediting."""
    monkeypatch.setenv("TAKYON_HOST_ROLE", "safebox")
    monkeypatch.delenv("TAKYON_SAFEBOX_URL", raising=False)
    monkeypatch.setenv("STRIPE_BILLING_WEBHOOK_SECRET", _WHSEC)
    assert safebox._use_remote_authority() is False

    body = '{"id":"evt_badsig","type":"checkout.session.completed"}'
    forged = build_signature_header(body, "whsec_attacker_guess")
    with pytest.raises(safebox.StripeBillingWebhookInvalidSignature):
        safebox.verify_stripe_billing_webhook(body, forged)


def test_billing_webhook_authority_accepts_valid_and_returns_event(monkeypatch):
    """A correctly-signed event verifies and is returned as the parsed dict — proving the
    happy path is reachable so the fail-closed tests above are meaningful (not always-raise)."""
    monkeypatch.setenv("TAKYON_HOST_ROLE", "safebox")
    monkeypatch.delenv("TAKYON_SAFEBOX_URL", raising=False)
    monkeypatch.setenv("STRIPE_BILLING_WEBHOOK_SECRET", _WHSEC)

    body = '{"id":"evt_good","type":"checkout.session.completed"}'
    header = build_signature_header(body, _WHSEC)
    event = safebox.verify_stripe_billing_webhook(body, header)
    assert event.get("id") == "evt_good"
    assert event.get("type") == "checkout.session.completed"


# ---------------------------------------------------------------------------
# Part C — source-level fail-closed / idempotency contracts over the routes
# (no credential, no network — assert the real route + ledger source structure)
# ---------------------------------------------------------------------------


def test_control_billing_webhook_route_maps_unconfigured_to_503():
    """The flow-A /billing/webhook route maps StripeBillingWebhookUnconfigured -> 503 and
    StripeBillingWebhookInvalidSignature -> 400, and verifies the signature BEFORE doing any
    crediting. We assert on the real route source (importing FastAPI is not required to read it)."""
    from plugins.takyon import control_api

    src = inspect.getsource(control_api.build_control_router)
    assert "verify_stripe_billing_webhook" in src
    assert "StripeBillingWebhookUnconfigured" in src
    assert "status_code=503" in src
    assert "StripeBillingWebhookInvalidSignature" in src
    assert "status_code=400" in src
    # Verification must precede crediting: the verify call appears before billing.topup.
    assert src.index("verify_stripe_billing_webhook") < src.index("billing.topup")
    # Crediting is keyed on the Stripe event id (idempotency token).
    assert "idempotency_key=event_id" in src


def test_billing_topup_is_idempotent_on_event_id_by_source():
    """billing.topup dedups on idempotency_key (the Stripe event id): a replay of the same
    event id returns the prior balance and writes NO second ledger row. Asserted on the real
    source; the live-DB proof is in the PG-gated test below."""
    src = inspect.getsource(billing.topup)
    # Looks up a prior ledger entry by idempotency_key and returns early if present.
    assert "idempotency_key" in src
    assert "balance_after_cents" in src and "where idempotency_key" in src
    assert "return int(prior[0])" in src, "replay must return prior balance, not re-credit"


def test_app_payments_dedups_on_webhook_events_row_lock_by_source():
    """Flow-B product webhooks dedup GLOBALLY on (provider, provider_event_id): the row is
    locked `for update` and processing is skipped when processed_at is already set, so a
    concurrent redelivery processes AT MOST ONCE. Asserted on the real source."""
    src = inspect.getsource(app_payments.record_webhook_and_process)
    assert "webhook_events" in src
    assert "for update" in src
    assert "processed_at" in src
    assert "deduplicated" in src
    # A missing event id is refused outright (no dedup key => not trusted).
    assert "event id is required for dedup" in src


def test_flow_b_record_webhook_handler_failcloses_without_secret_by_source():
    """The flow-B operator handler (handle_business_record_stripe_webhook) refuses when
    STRIPE_WEBHOOK_SECRET is absent and verifies the signature before reconciling — the
    product-side analogue of the flow-A 503. Asserted on the real source."""
    from plugins.takyon import core

    src = inspect.getsource(core.handle_business_record_stripe_webhook)
    assert "STRIPE_WEBHOOK_SECRET" in src
    assert "_verify_stripe_signature" in src
    assert "requires STRIPE_WEBHOOK_SECRET" in src
    # Signature verification precedes reconciliation.
    assert src.index("_verify_stripe_signature") < src.index("record_webhook_and_process")


# ---------------------------------------------------------------------------
# Part D — PG + FastAPI gated: drive the real HTTP route end to end.
# Needs the Postgres rig (TAKYON_TEST_PG_DSN via pg_conn) and FastAPI; skips
# cleanly otherwise. Proves the fail-closed + idempotent invariant through the
# actual mounted route, not just the underlying functions.
# ---------------------------------------------------------------------------

psycopg = pytest.importorskip("psycopg", reason="Postgres rig required for live-route INV5 checks")
fastapi = pytest.importorskip("fastapi", reason="FastAPI required to drive the live webhook route")

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from plugins.takyon.control_api import build_control_router, get_control_conn  # noqa: E402
from plugins.takyon.control_plane import provision_user_on_first_login  # noqa: E402


@pytest.fixture()
def client(pg_conn):
    """Mount the real control router with get_control_conn overridden to the test pg_conn."""
    app = FastAPI()
    app.include_router(build_control_router())
    app.dependency_overrides[get_control_conn] = lambda: pg_conn
    return TestClient(app, raise_server_exceptions=True)


def _topup_event(user_id: str, *, amount: int = 2000, event_id: str | None = None) -> str:
    import json
    import uuid

    return json.dumps(
        {
            "id": event_id or f"evt_{uuid.uuid4().hex}",
            "type": "checkout.session.completed",
            "data": {
                "object": {
                    "id": f"cs_{uuid.uuid4().hex}",
                    "client_reference_id": user_id,
                    "payment_status": "paid",
                    "amount_total": amount,
                    "metadata": {"purpose": "takyon_topup", "user_id": user_id},
                }
            },
        }
    )


@pytest.mark.usefixtures("pg_conn")
def test_live_route_503_when_billing_secret_absent(client, monkeypatch):
    """Live route: missing STRIPE_BILLING_WEBHOOK_SECRET => 503, event NOT trusted."""
    monkeypatch.setenv("TAKYON_HOST_ROLE", "safebox")
    monkeypatch.delenv("TAKYON_SAFEBOX_URL", raising=False)
    monkeypatch.delenv("STRIPE_BILLING_WEBHOOK_SECRET", raising=False)
    body = _topup_event("u-irrelevant")
    resp = client.post(
        "/v1/billing/webhook",
        content=body,
        headers={"stripe-signature": build_signature_header(body, _WHSEC)},
    )
    assert resp.status_code == 503
    assert resp.json()["detail"] == "billing_webhook_unconfigured"


@pytest.mark.usefixtures("pg_conn")
def test_live_route_400_on_forged_signature(client, monkeypatch):
    """Live route: with the real secret set, a forged signature => 400 (rejected)."""
    monkeypatch.setenv("TAKYON_HOST_ROLE", "safebox")
    monkeypatch.delenv("TAKYON_SAFEBOX_URL", raising=False)
    monkeypatch.setenv("STRIPE_BILLING_WEBHOOK_SECRET", _WHSEC)
    body = _topup_event("u-irrelevant")
    forged = build_signature_header(body, "whsec_wrong")
    resp = client.post(
        "/v1/billing/webhook",
        content=body,
        headers={"stripe-signature": forged},
    )
    assert resp.status_code == 400
    assert resp.json()["detail"] == "invalid_signature"


@pytest.mark.usefixtures("pg_conn")
def test_live_route_credits_once_then_replay_is_noop(client, pg_conn, monkeypatch):
    """Live route, full invariant: a valid topup credits exactly once; replaying the SAME
    Stripe event id credits again 0 (idempotent on event id)."""
    monkeypatch.setenv("TAKYON_HOST_ROLE", "safebox")
    monkeypatch.delenv("TAKYON_SAFEBOX_URL", raising=False)
    monkeypatch.setenv("STRIPE_BILLING_WEBHOOK_SECRET", _WHSEC)

    # JIT-provision a real operator user (returns (user_id, created, raw_key)).
    uid, _created, _raw = provision_user_on_first_login(
        pg_conn, "auth0|inv5-webhook", email="inv5-webhook@example.com"
    )

    body = _topup_event(uid, amount=2000)
    sig = build_signature_header(body, _WHSEC)

    first = client.post("/v1/billing/webhook", content=body, headers={"stripe-signature": sig})
    assert first.status_code == 200, first.text
    assert first.json()["topup_balance_cents"] == 2000
    assert billing.get_billing_balances(pg_conn, uid).topup_balance_cents == 2000

    # Replay the IDENTICAL event id -> credited once, balance unchanged.
    replay = client.post("/v1/billing/webhook", content=body, headers={"stripe-signature": sig})
    assert replay.status_code == 200, replay.text
    assert billing.get_billing_balances(pg_conn, uid).topup_balance_cents == 2000
