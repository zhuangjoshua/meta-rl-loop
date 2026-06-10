"""Postgres integration tests for the Control API read path — the opaque
Takyon-user HTTP boundary (Phase 1 acceptance: a request resolves to exactly one
user + their businesses before any privileged work; revoked/unknown keys are
rejected; one tenant can't read another's businesses).

Exercises the REAL FastAPI request path (resolver + DB), not mocks. Uses the shared
`pg_conn` fixture (per-worker throwaway DB); skips unless psycopg AND fastapi are
importable and TAKYON_TEST_PG_DSN is set.
"""

from __future__ import annotations

import json
import uuid

import pytest

psycopg = pytest.importorskip("psycopg")
pytest.importorskip("fastapi")

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from plugins.takyon import billing, business_credits, custody, safebox, stripe_util  # noqa: E402
from plugins.takyon.control_api import (  # noqa: E402
    build_control_router,
    get_control_conn,
    sync_operator_subscription_allowance,
)
from plugins.takyon.control_plane import (  # noqa: E402
    provision_user_on_first_login,
    resolve_api_key,
)
from plugins.takyon.stripe_util import build_signature_header  # noqa: E402


def _sub() -> str:
    return f"auth0|{uuid.uuid4().hex}"


def _add_business(conn, owner_id, name="Acme") -> str:
    slug = f"biz-{uuid.uuid4().hex[:8]}"
    conn.execute(
        "insert into businesses (slug, name, owner_user_id) values (%s, %s, %s)",
        (slug, name, owner_id),
    )
    return slug


def _topup_event(
    user_id, *, amount=2000, event_id=None, purpose="takyon_topup", payment_status="paid"
) -> dict:
    """A Stripe checkout.session.completed shaped exactly like what the topup checkout
    session produces — client_reference_id + metadata.purpose are how the webhook maps the
    payment back to the user."""
    return {
        "id": event_id or f"evt_{uuid.uuid4().hex}",
        "type": "checkout.session.completed",
        "data": {
            "object": {
                "id": f"cs_{uuid.uuid4().hex}",
                "client_reference_id": user_id,
                "amount_total": amount,
                "payment_status": payment_status,
                "metadata": {"purpose": purpose, "user_id": user_id},
            }
        },
    }


def _creative_credit_event(
    business_slug,
    *,
    user_id="user-test",
    amount=5000,
    credits=50,
    purpose="creative_credit_topup",
    pack_id="starter",
    event_id=None,
    payment_status="paid",
) -> dict:
    metadata = {
        "purpose": purpose,
        "user_id": user_id,
        "business_slug": business_slug,
        "credits": str(credits),
    }
    if pack_id:
        metadata["pack_id"] = pack_id
    return {
        "id": event_id or f"evt_{uuid.uuid4().hex}",
        "type": "checkout.session.completed",
        "data": {
            "object": {
                "id": f"cs_{uuid.uuid4().hex}",
                "client_reference_id": business_slug,
                "amount_total": amount,
                "payment_status": payment_status,
                "metadata": metadata,
            }
        },
    }


def _post_webhook(client, event: dict, secret: str):
    """POST a locally-signed event to the topup webhook (no Stripe, no network)."""
    body = json.dumps(event)
    return client.post(
        "/v1/billing/webhook",
        content=body,
        headers={"stripe-signature": build_signature_header(body, secret)},
    )


@pytest.fixture
def client(pg_conn, monkeypatch):
    monkeypatch.setenv("TAKYON_HOST_ROLE", "safebox")
    app = FastAPI()
    app.include_router(build_control_router())
    app.dependency_overrides[get_control_conn] = lambda: pg_conn
    return TestClient(app)


def _auth(raw: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {raw}"}


def test_me_requires_bearer(client):
    resp = client.get("/v1/me")
    assert resp.status_code == 401
    assert resp.json()["detail"] == "missing_bearer_token"
    assert resp.headers.get("WWW-Authenticate") == "Bearer"


def test_me_rejects_garbage_bearer(client):
    resp = client.get("/v1/me", headers=_auth("not-a-key"))
    assert resp.status_code == 401
    assert resp.json()["detail"] == "invalid_api_key"


def test_me_rejects_unknown_but_wellformed_key(client, pg_conn):
    from plugins.takyon.user_api_keys import generate_api_key

    resp = client.get("/v1/me", headers=_auth(generate_api_key()))
    assert resp.status_code == 401


def test_me_rejects_revoked_key(client, pg_conn):
    _uid, _, raw = provision_user_on_first_login(pg_conn, _sub())
    principal = resolve_api_key(pg_conn, raw)
    assert principal is not None
    assert safebox.revoke_user_api_key(principal.key_id) is True
    resp = client.get("/v1/me", headers=_auth(raw))
    assert resp.status_code == 401


def test_me_returns_resolved_identity(client, pg_conn):
    uid, created, raw = provision_user_on_first_login(pg_conn, _sub())
    assert created is True
    resp = client.get("/v1/me", headers=_auth(raw))
    assert resp.status_code == 200
    body = resp.json()
    assert body == {"user_id": uid, "status": "active"}


def test_sync_operator_subscription_allowance_falls_back_to_dev_plan(pg_conn):
    uid, _created, _raw = provision_user_on_first_login(pg_conn, _sub(), "owner@example.com")

    state = sync_operator_subscription_allowance(pg_conn, uid, refresh_live=False)
    balances = billing.get_billing_balances(pg_conn, uid)

    assert state.plan_name == "DEV"
    assert state.subscription_status == "none"
    assert state.weekly_allowance_cents == 10_000
    assert balances.allowance_included_cents == 10_000
    assert balances.allowance_resets_at is not None


def test_me_payouts_returns_custody_and_connect_state(client, pg_conn, monkeypatch):
    uid, _, raw = provision_user_on_first_login(pg_conn, _sub())
    slug = _add_business(pg_conn, uid)
    custody.accrue(pg_conn, uid, slug, 5000, "custody-1", fee_bps=0)
    pg_conn.execute(
        "update users set stripe_connect_account_id = %s, stripe_connect_status = %s where id = %s",
        ("acct_live_123", "pending", uid),
    )

    def _fake_request(path, params, *, method="POST"):
        assert path == "accounts/acct_live_123"
        assert method == "GET"
        return {
            "id": "acct_live_123",
            "default_currency": "usd",
            "details_submitted": True,
            "payouts_enabled": True,
            "requirements": {"disabled_reason": None, "past_due": []},
        }

    monkeypatch.setattr(stripe_util, "stripe_request", _fake_request)
    resp = client.get("/v1/me/payouts", headers=_auth(raw))
    assert resp.status_code == 200, resp.text
    assert resp.json() == {
        "user_id": uid,
        "stripe_connect_account_id": "acct_live_123",
        "stripe_connect_status": "active",
        "payouts_enabled": True,
        "details_submitted": True,
        "payout_currency": "usd",
        "owed_balance_cents": 5000,
        "paid_out_cents": 0,
    }


def test_payout_connect_creates_account_and_onboarding_link(client, pg_conn, monkeypatch):
    uid, _, raw = provision_user_on_first_login(pg_conn, _sub(), "owner@example.com")
    captured: list[tuple[str, dict, str]] = []

    def _fake_request(path, params, *, method="POST"):
        captured.append((path, dict(params), method))
        if path == "accounts":
            return {
                "id": "acct_connect_1",
                "default_currency": "usd",
                "details_submitted": False,
                "payouts_enabled": False,
                "requirements": {"disabled_reason": None, "past_due": []},
            }
        if path == "account_links":
            return {"url": "https://connect.stripe.com/onboarding/acct_connect_1"}
        raise AssertionError(f"unexpected path: {path}")

    monkeypatch.setattr(stripe_util, "stripe_request", _fake_request)
    resp = client.post(
        "/v1/me/payouts/connect",
        headers=_auth(raw),
        json={
            "return_url": "https://app.example.com/return",
            "refresh_url": "https://app.example.com/refresh",
        },
    )
    assert resp.status_code == 200, resp.text
    assert resp.json() == {
        "connect_url": "https://connect.stripe.com/onboarding/acct_connect_1",
        "link_type": "account_onboarding",
        "stripe_connect_account_id": "acct_connect_1",
        "stripe_connect_status": "pending",
    }
    assert captured[0][0] == "accounts"
    assert captured[0][1]["type"] == "express"
    assert captured[0][1]["email"] == "owner@example.com"
    assert captured[0][1]["capabilities[transfers][requested]"] == "true"
    assert captured[1][0] == "account_links"
    row = pg_conn.execute(
        "select stripe_connect_account_id, stripe_connect_status from users where id = %s",
        (uid,),
    ).fetchone()
    assert tuple(row) == ("acct_connect_1", "pending")


def test_payout_connect_active_account_skips_refresh_lookup(client, pg_conn, monkeypatch):
    uid, _, raw = provision_user_on_first_login(pg_conn, _sub(), "owner@example.com")
    pg_conn.execute(
        "update users set stripe_connect_account_id = %s, stripe_connect_status = %s where id = %s",
        ("acct_live_123", "active", uid),
    )
    captured: list[tuple[str, dict, str]] = []

    def _fake_request(path, params, *, method="POST"):
        captured.append((path, dict(params), method))
        if path == "accounts/acct_live_123/login_links":
            return {"url": "https://connect.stripe.com/login/acct_live_123"}
        raise AssertionError(f"unexpected path: {path}")

    monkeypatch.setattr(stripe_util, "stripe_request", _fake_request)
    resp = client.post(
        "/v1/me/payouts/connect",
        headers=_auth(raw),
        json={
            "return_url": "https://app.example.com/return",
            "refresh_url": "https://app.example.com/refresh",
        },
    )
    assert resp.status_code == 200, resp.text
    assert resp.json() == {
        "connect_url": "https://connect.stripe.com/login/acct_live_123",
        "link_type": "login_link",
        "stripe_connect_account_id": "acct_live_123",
        "stripe_connect_status": "active",
    }
    assert captured == [("accounts/acct_live_123/login_links", {}, "POST")]


def test_businesses_lists_only_owned(client, pg_conn):
    uid, _, raw = provision_user_on_first_login(pg_conn, _sub())
    mine = {_add_business(pg_conn, uid), _add_business(pg_conn, uid)}
    # a second user with their own business must not leak into the first's list
    other_uid, _, _ = provision_user_on_first_login(pg_conn, _sub())
    _add_business(pg_conn, other_uid, name="NotMine")

    resp = client.get("/v1/businesses", headers=_auth(raw))
    assert resp.status_code == 200
    slugs = {b["slug"] for b in resp.json()["businesses"]}
    assert slugs == mine


def test_business_detail_happy_path(client, pg_conn):
    uid, _, raw = provision_user_on_first_login(pg_conn, _sub())
    slug = _add_business(pg_conn, uid)
    resp = client.get(f"/v1/businesses/{slug}", headers=_auth(raw))
    assert resp.status_code == 200
    body = resp.json()
    assert body["slug"] == slug
    assert body["mode"] == "test"  # safe default


def test_business_detail_cross_tenant_is_404(client, pg_conn):
    owner_uid, _, _ = provision_user_on_first_login(pg_conn, _sub())
    secret_slug = _add_business(pg_conn, owner_uid, name="Confidential")
    # a different user, holding a valid key, must not be able to read it — and must
    # not even learn it exists, so the answer is 404, not 403.
    other_uid, _, other_raw = provision_user_on_first_login(pg_conn, _sub())
    resp = client.get(f"/v1/businesses/{secret_slug}", headers=_auth(other_raw))
    assert resp.status_code == 404


def test_creative_credit_balance_happy_path(client, pg_conn):
    uid, _, raw = provision_user_on_first_login(pg_conn, _sub())
    slug = _add_business(pg_conn, uid)
    business_credits.grant_credits(pg_conn, slug, 12, "grant-1")

    resp = client.get(f"/v1/businesses/{slug}/creative-credits", headers=_auth(raw))
    assert resp.status_code == 200
    assert resp.json() == {
        "business_slug": slug,
        "balance_credits": 12,
        "reserved_credits": 0,
        "supports_custom_credits": True,
        "price_cents_per_credit": 1,
        "minimum_checkout_credits": 50,
        "minimum_checkout_amount_cents": 50,
    }


def test_creative_credit_packs_list_configured_catalog(client, pg_conn, monkeypatch):
    uid, _, raw = provision_user_on_first_login(pg_conn, _sub())
    slug = _add_business(pg_conn, uid)
    monkeypatch.setenv(
        "TAKYON_CREATIVE_CREDIT_PACKS_JSON",
        json.dumps(
            [
                {"id": "starter", "name": "Starter", "credits": 10, "amount_cents": 2500},
                {"id": "pro", "credits": 50, "amount_cents": 10000},
            ]
        ),
    )

    resp = client.get(f"/v1/businesses/{slug}/creative-credits/packs", headers=_auth(raw))
    assert resp.status_code == 200
    assert resp.json() == {
        "business_slug": slug,
        "packs": [
            {
                "id": "starter",
                "name": "Starter",
                "description": "",
                "credits": 10,
                "amount_cents": 2500,
                "currency": "usd",
            },
            {
                "id": "pro",
                "name": "pro",
                "description": "",
                "credits": 50,
                "amount_cents": 10000,
                "currency": "usd",
            },
        ],
        "supports_custom_credits": True,
        "price_cents_per_credit": 1,
        "minimum_checkout_credits": 50,
        "minimum_checkout_amount_cents": 50,
    }


def test_creative_credit_packs_list_reads_takyon_home_env_when_process_env_missing(
    client, pg_conn, monkeypatch
):
    import takyon_cli.config as takyon_config

    uid, _, raw = provision_user_on_first_login(pg_conn, _sub())
    slug = _add_business(pg_conn, uid)
    monkeypatch.delenv("TAKYON_CREATIVE_CREDIT_PACKS_JSON", raising=False)
    monkeypatch.setattr(
        takyon_config,
        "load_env",
        lambda: {
            "TAKYON_CREATIVE_CREDIT_PACKS_JSON": json.dumps(
                [{"id": "starter", "credits": 10, "amount_cents": 2500}]
            )
        },
    )

    resp = client.get(f"/v1/businesses/{slug}/creative-credits/packs", headers=_auth(raw))
    assert resp.status_code == 200
    assert resp.json() == {
        "business_slug": slug,
        "packs": [
            {
                "id": "starter",
                "name": "starter",
                "description": "",
                "credits": 10,
                "amount_cents": 2500,
                "currency": "usd",
            }
        ],
        "supports_custom_credits": True,
        "price_cents_per_credit": 1,
        "minimum_checkout_credits": 50,
        "minimum_checkout_amount_cents": 50,
    }


def test_jit_provision_is_idempotent_and_mints_once(pg_conn):
    sub = _sub()
    uid1, created1, raw1 = provision_user_on_first_login(pg_conn, sub, "a@example.com")
    uid2, created2, raw2 = provision_user_on_first_login(pg_conn, sub, "a@example.com")
    assert created1 is True and raw1 is not None
    assert created2 is False and raw2 is None
    assert uid1 == uid2
    # the once-minted key resolves to the same user
    principal = resolve_api_key(pg_conn, raw1)
    assert principal is not None and principal.user_id == uid1
    # exactly one active key
    active = pg_conn.execute(
        "select count(*) from user_api_keys where user_id = %s and revoked_at is null",
        (uid1,),
    ).fetchone()[0]
    assert active == 1


def test_read_path_runs_resolver_and_stamps_last_used(client, pg_conn):
    uid, _, raw = provision_user_on_first_login(pg_conn, _sub())
    before = pg_conn.execute(
        "select last_used_at from user_api_keys where user_id = %s", (uid,)
    ).fetchone()[0]
    assert before is None
    assert client.get("/v1/me", headers=_auth(raw)).status_code == 200
    after = pg_conn.execute(
        "select last_used_at from user_api_keys where user_id = %s", (uid,)
    ).fetchone()[0]
    assert after is not None


# --- Phase 3: flow-A topup checkout + webhook -------------------------------------

def test_topup_checkout_requires_bearer(client):
    # Valid body, no auth -> the boundary refuses before any Stripe work.
    resp = client.post(
        "/v1/billing/topup/checkout",
        json={"amount_cents": 1000, "success_url": "https://x/ok", "cancel_url": "https://x/no"},
    )
    assert resp.status_code == 401


def test_topup_checkout_rejects_nonpositive_amount(client, pg_conn):
    uid, _, raw = provision_user_on_first_login(pg_conn, _sub())
    resp = client.post(
        "/v1/billing/topup/checkout",
        headers=_auth(raw),
        json={"amount_cents": 0, "success_url": "https://x/ok", "cancel_url": "https://x/no"},
    )
    assert resp.status_code == 422  # pydantic gt=0


def test_topup_checkout_blocked_without_stripe_key(client, pg_conn, monkeypatch):
    # Missing STRIPE_SECRET_KEY must block (503) with a reason, never fake a URL.
    monkeypatch.delenv("STRIPE_SECRET_KEY", raising=False)
    uid, _, raw = provision_user_on_first_login(pg_conn, _sub())
    resp = client.post(
        "/v1/billing/topup/checkout",
        headers=_auth(raw),
        json={"amount_cents": 1000, "success_url": "https://x/ok", "cancel_url": "https://x/no"},
    )
    assert resp.status_code == 503
    assert resp.json()["detail"] == "topup_unconfigured"


def test_topup_checkout_returns_url_and_tags_user(client, pg_conn, monkeypatch):
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_xyz")
    uid, _, raw = provision_user_on_first_login(pg_conn, _sub())
    captured: dict = {}

    def _fake_request(path, params):
        captured["path"] = path
        captured["params"] = params
        return {"id": "cs_test_1", "url": "https://checkout.stripe.com/c/cs_test_1"}

    monkeypatch.setattr(stripe_util, "stripe_request", _fake_request)
    resp = client.post(
        "/v1/billing/topup/checkout",
        headers=_auth(raw),
        json={
            "amount_cents": 2500,
            "success_url": "https://app.example.com/ok",
            "cancel_url": "https://app.example.com/no",
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["checkout_url"] == "https://checkout.stripe.com/c/cs_test_1"
    assert body["amount_cents"] == 2500
    # The session is tagged so the webhook can credit the right user, once.
    assert captured["path"] == "checkout/sessions"
    p = captured["params"]
    assert p["client_reference_id"] == uid
    assert p["metadata[purpose]"] == "takyon_topup"
    assert p["metadata[user_id]"] == uid
    assert p["line_items[0][price_data][unit_amount]"] == 2500
    assert p["success_url"] == "https://app.example.com/ok"


def test_creative_credit_checkout_returns_url_and_tags_business(client, pg_conn, monkeypatch):
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_xyz")
    uid, _, raw = provision_user_on_first_login(pg_conn, _sub())
    slug = _add_business(pg_conn, uid)
    captured: dict = {}

    def _fake_request(path, params):
        captured["path"] = path
        captured["params"] = params
        return {"id": "cs_credit_1", "url": "https://checkout.stripe.com/c/cs_credit_1"}

    monkeypatch.setattr(stripe_util, "stripe_request", _fake_request)
    resp = client.post(
        f"/v1/businesses/{slug}/creative-credits/checkout",
        headers=_auth(raw),
        json={
            "credits": 125,
            "success_url": "https://app.example.com/ok",
            "cancel_url": "https://app.example.com/no",
        },
    )
    assert resp.status_code == 200, resp.text
    assert resp.json() == {
        "checkout_url": "https://checkout.stripe.com/c/cs_credit_1",
        "session_id": "cs_credit_1",
        "business_slug": slug,
        "pack_id": None,
        "credits": 125,
        "amount_cents": 125,
        "price_cents_per_credit": 1,
    }
    p = captured["params"]
    assert captured["path"] == "checkout/sessions"
    assert p["client_reference_id"] == slug
    assert p["line_items[0][price_data][unit_amount]"] == 125
    assert p["metadata[purpose]"] == "creative_credit_topup"
    assert p["metadata[business_slug]"] == slug
    assert p["metadata[user_id]"] == uid
    assert p["metadata[credits]"] == 125
    assert p["metadata[price_cents_per_credit]"] == 1


def test_creative_credit_checkout_rejects_below_stripe_minimum(client, pg_conn, monkeypatch):
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_xyz")
    uid, _, raw = provision_user_on_first_login(pg_conn, _sub())
    slug = _add_business(pg_conn, uid)

    resp = client.post(
        f"/v1/businesses/{slug}/creative-credits/checkout",
        headers=_auth(raw),
        json={
            "credits": 25,
            "success_url": "https://app.example.com/ok",
            "cancel_url": "https://app.example.com/no",
        },
    )
    assert resp.status_code == 400
    assert "minimum creative credit purchase is 50 credits ($0.50)" in resp.json()["detail"]


def test_creative_credit_reconcile_session_credits_business_and_dedupes_webhook(
    client, pg_conn, monkeypatch
):
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_xyz")
    monkeypatch.setenv("STRIPE_BILLING_WEBHOOK_SECRET", "whsec_test_xyz")
    uid, _, raw = provision_user_on_first_login(pg_conn, _sub())
    slug = _add_business(pg_conn, uid)
    session_id = "cs_credit_paid_1"

    def _fake_request(path, params, *, method="POST"):
        assert path == f"checkout/sessions/{session_id}"
        assert params == {}
        assert method == "GET"
        return {
            "id": session_id,
            "client_reference_id": slug,
            "amount_total": 125,
            "payment_status": "paid",
            "metadata": {
                "purpose": "creative_credit_topup",
                "user_id": uid,
                "business_slug": slug,
                "credits": "125",
                "price_cents_per_credit": "1",
            },
        }

    monkeypatch.setattr(stripe_util, "stripe_request", _fake_request)
    resp = client.post(
        f"/v1/businesses/{slug}/creative-credits/reconcile",
        headers=_auth(raw),
        json={"session_id": session_id},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json() == {
        "ok": True,
        "business_slug": slug,
        "credited_credits": 125,
        "balance_credits": 125,
        "reserved_credits": 0,
        "session_id": session_id,
    }
    assert business_credits.get_business_credit_balances(pg_conn, slug).balance_credits == 125

    event = _creative_credit_event(
        slug,
        user_id=uid,
        credits=125,
        amount=125,
        event_id="evt_credit_late_webhook",
    )
    event["data"]["object"]["id"] = session_id
    event["data"]["object"]["metadata"]["price_cents_per_credit"] = "1"
    resp2 = _post_webhook(client, event, "whsec_test_xyz")
    assert resp2.status_code == 200, resp2.text
    assert business_credits.get_business_credit_balances(pg_conn, slug).balance_credits == 125
    grant_entries = [
        entry for entry in business_credits.list_credit_entries(pg_conn, slug)
        if entry.kind == "grant"
    ]
    assert len(grant_entries) == 1
    assert grant_entries[0].stripe_ref == session_id


def test_creative_credit_reconcile_session_rejects_unpaid(client, pg_conn, monkeypatch):
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_xyz")
    uid, _, raw = provision_user_on_first_login(pg_conn, _sub())
    slug = _add_business(pg_conn, uid)
    session_id = "cs_credit_open_1"

    def _fake_request(path, params, *, method="POST"):
        assert path == f"checkout/sessions/{session_id}"
        assert params == {}
        assert method == "GET"
        return {
            "id": session_id,
            "client_reference_id": slug,
            "amount_total": 125,
            "payment_status": "open",
            "metadata": {
                "purpose": "creative_credit_topup",
                "user_id": uid,
                "business_slug": slug,
                "credits": "125",
            },
        }

    monkeypatch.setattr(stripe_util, "stripe_request", _fake_request)
    resp = client.post(
        f"/v1/businesses/{slug}/creative-credits/reconcile",
        headers=_auth(raw),
        json={"session_id": session_id},
    )
    assert resp.status_code == 409
    assert resp.json()["detail"] == "creative_credit_checkout_unpaid"
    assert business_credits.get_business_credit_balances(pg_conn, slug).balance_credits == 0


def test_billing_webhook_blocked_without_secret(client, monkeypatch):
    monkeypatch.delenv("STRIPE_BILLING_WEBHOOK_SECRET", raising=False)
    resp = client.post(
        "/v1/billing/webhook", content="{}", headers={"stripe-signature": "t=1,v1=deadbeef"}
    )
    assert resp.status_code == 503
    assert resp.json()["detail"] == "billing_webhook_unconfigured"


def test_billing_webhook_rejects_bad_signature(client, monkeypatch):
    monkeypatch.setenv("STRIPE_BILLING_WEBHOOK_SECRET", "whsec_test_xyz")
    body = json.dumps(_topup_event("u-irrelevant"))
    # signed with the WRONG secret -> verification fails -> 400 (Stripe won't retry)
    bad = build_signature_header(body, "whsec_wrong")
    resp = client.post(
        "/v1/billing/webhook", content=body, headers={"stripe-signature": bad}
    )
    assert resp.status_code == 400
    assert resp.json()["detail"] == "invalid_signature"


def test_billing_webhook_credits_user_and_is_idempotent(client, pg_conn, monkeypatch):
    monkeypatch.setenv("STRIPE_BILLING_WEBHOOK_SECRET", "whsec_test_xyz")
    uid, _, _ = provision_user_on_first_login(pg_conn, _sub())
    event = _topup_event(uid, amount=2000)

    resp = _post_webhook(client, event, "whsec_test_xyz")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["credited_cents"] == 2000
    assert body["topup_balance_cents"] == 2000
    assert billing.get_billing_balances(pg_conn, uid).topup_balance_cents == 2000

    # Replay the SAME Stripe event id -> credited exactly once (idempotent).
    resp2 = _post_webhook(client, event, "whsec_test_xyz")
    assert resp2.status_code == 200
    assert billing.get_billing_balances(pg_conn, uid).topup_balance_cents == 2000


def test_billing_webhook_credits_business_creative_topup_and_is_idempotent(
    client, pg_conn, monkeypatch
):
    monkeypatch.setenv("STRIPE_BILLING_WEBHOOK_SECRET", "whsec_test_xyz")
    uid, _, _ = provision_user_on_first_login(pg_conn, _sub())
    slug = _add_business(pg_conn, uid)
    event = _creative_credit_event(slug, user_id=uid, credits=75, amount=75)

    resp = _post_webhook(client, event, "whsec_test_xyz")
    assert resp.status_code == 200, resp.text
    assert resp.json() == {
        "ok": True,
        "business_slug": slug,
        "credited_credits": 75,
        "balance_credits": 75,
        "reserved_credits": 0,
        "event_id": event["id"],
    }
    balances = business_credits.get_business_credit_balances(pg_conn, slug)
    assert balances.balance_credits == 75

    resp2 = _post_webhook(client, event, "whsec_test_xyz")
    assert resp2.status_code == 200
    assert business_credits.get_business_credit_balances(pg_conn, slug).balance_credits == 75


def test_billing_webhook_still_accepts_legacy_creative_credit_pack_events(
    client, pg_conn, monkeypatch
):
    monkeypatch.setenv("STRIPE_BILLING_WEBHOOK_SECRET", "whsec_test_xyz")
    uid, _, _ = provision_user_on_first_login(pg_conn, _sub())
    slug = _add_business(pg_conn, uid)
    event = _creative_credit_event(
        slug,
        user_id=uid,
        credits=25,
        amount=2500,
        purpose="creative_credit_pack",
        pack_id="starter",
    )

    resp = _post_webhook(client, event, "whsec_test_xyz")
    assert resp.status_code == 200, resp.text
    assert resp.json()["credited_credits"] == 25
    assert business_credits.get_business_credit_balances(pg_conn, slug).balance_credits == 25


def test_billing_webhook_ignores_non_topup(client, pg_conn, monkeypatch):
    monkeypatch.setenv("STRIPE_BILLING_WEBHOOK_SECRET", "whsec_test_xyz")
    uid, _, _ = provision_user_on_first_login(pg_conn, _sub())
    resp = _post_webhook(client, _topup_event(uid, purpose="product_subscription"), "whsec_test_xyz")
    assert resp.status_code == 200
    assert resp.json()["ignored"] == "not_a_topup"
    assert billing.get_billing_balances(pg_conn, uid).topup_balance_cents == 0


def test_billing_webhook_ignores_unpaid(client, pg_conn, monkeypatch):
    monkeypatch.setenv("STRIPE_BILLING_WEBHOOK_SECRET", "whsec_test_xyz")
    uid, _, _ = provision_user_on_first_login(pg_conn, _sub())
    resp = _post_webhook(client, _topup_event(uid, payment_status="unpaid"), "whsec_test_xyz")
    assert resp.status_code == 200
    assert resp.json()["ignored"] == "unpaid"
    assert billing.get_billing_balances(pg_conn, uid).topup_balance_cents == 0


# --- Phase 3: per-user rate limiting on the authenticated boundary ----------------

def test_authenticated_endpoint_rate_limited_after_cap(client, pg_conn, monkeypatch):
    # Drive the cap low via env so a few real requests cross it. The first `limit`
    # requests pass; the next is refused with 429 + a Retry-After hint. window=60s keeps
    # all calls in one window.
    monkeypatch.setenv("TAKYON_CONTROL_RATE_LIMIT", "2")
    monkeypatch.setenv("TAKYON_CONTROL_RATE_WINDOW_SECONDS", "60")
    _, _, raw = provision_user_on_first_login(pg_conn, _sub())
    assert client.get("/v1/me", headers=_auth(raw)).status_code == 200
    assert client.get("/v1/me", headers=_auth(raw)).status_code == 200
    resp = client.get("/v1/me", headers=_auth(raw))
    assert resp.status_code == 429
    assert resp.json()["detail"] == "rate_limited"
    assert int(resp.headers["Retry-After"]) >= 1


def test_rate_limit_is_per_user(client, pg_conn, monkeypatch):
    # One user hitting the cap must not lock out a different key-holder.
    monkeypatch.setenv("TAKYON_CONTROL_RATE_LIMIT", "1")
    monkeypatch.setenv("TAKYON_CONTROL_RATE_WINDOW_SECONDS", "60")
    _, _, raw_a = provision_user_on_first_login(pg_conn, _sub())
    _, _, raw_b = provision_user_on_first_login(pg_conn, _sub())
    assert client.get("/v1/me", headers=_auth(raw_a)).status_code == 200
    assert client.get("/v1/me", headers=_auth(raw_a)).status_code == 429
    # user b still has a full allowance
    assert client.get("/v1/me", headers=_auth(raw_b)).status_code == 200
