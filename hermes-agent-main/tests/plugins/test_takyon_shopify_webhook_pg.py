"""UC4 Shopify shop/update rail — the PG half (modularization plan §2.7 Stage 5; §4p UC4 leg 2).

Proves on the real control-plane schema (throwaway per-test DB, all migrations via the canonical
runner — including the 0060 safebox plan-write grant):

  * a VERIFIED shop/update recomposes the affected composed plan and mints the NEXT plan_key
    version with derived (never typed) economics — while the live plan_key row stays
    byte-identical and its active subscriber's entitlement is untouched (grandfather preserved);
  * provider-keyed dedup on the EXISTING webhook_events table: replaying the same delivery is ONE
    effect (`deduplicated=True`, no second mint) — including a replay with different headers,
    because the dedup id is content-derived;
  * fail-closed no-ops: unknown shop domain, unmapped plan name, missing plan_name, non-shop/update
    topics — all record an outcome and change ZERO pricing state;
  * the safebox authority route (`/v1/shopify/app-webhook/process`): internal token required, HMAC
    verified safebox-side BEFORE any processing (bad HMAC → 401 and the processor never runs),
    missing secret → 503, valid HMAC → the same leaf runs on the safebox DB conn.

Harness posture matches test_takyon_plan_composition.py (direct inserts + the entitlements leaf on
`pg_conn`) and test_takyon_safebox.py (env-pinned safebox host + TestClient on build_safebox_app).
"""

from __future__ import annotations

import base64
import contextlib
import hashlib
import hmac
import json
import uuid

import pytest

psycopg = pytest.importorskip("psycopg")

from plugins.takyon import app_entitlements as ents  # noqa: E402
from plugins.takyon import plan_composition as pc  # noqa: E402
from plugins.takyon import shopify_util as su  # noqa: E402

SHOP = "ourmanifold-uc4-test.myshopify.com"


# ── fixtures / helpers ─────────────────────────────────────────────────────────────


def _mk_business(conn, *, shop_domain: str | None = SHOP, partner_dev_fee: int | None = None) -> str:
    uid = str(uuid.uuid4())
    conn.execute(
        "insert into users (id, auth0_sub) values (%s, %s)", (uid, f"auth0|{uuid.uuid4().hex}")
    )
    slug = f"shp-{uuid.uuid4().hex[:8]}"
    metadata = None
    if shop_domain:
        record = {
            "shop_domain": shop_domain,
            "connected_account_id": "ca_shop_1",
            "status": "active",
        }
        if partner_dev_fee is not None:
            record["partner_dev_fee_microusd"] = int(partner_dev_fee)
        metadata = json.dumps({su.SHOPIFY_CONNECTION_METADATA_KEY: record})
    conn.execute(
        "insert into businesses (slug, name, goal, status, mode, owner_user_id, metadata_json) "
        "values (%s, %s, 'g', 'active', 'test', %s, %s)",
        (slug, slug, uid, metadata),
    )
    return slug


def _composition(fee_microusd: int = 39_000_000) -> pc.PlanComposition:
    return pc.PlanComposition(
        components=(
            pc.PricedComponent(
                kind="ai_allowance",
                key="ai_allowance",
                cost_basis=pc.CostBasis.metered(
                    pc.MeteredAllowance(
                        model="claude-opus-4-8",
                        provider="anthropic",
                        input_tokens=1_000_000,
                        output_tokens=1_000_000,
                    )
                ),
                grants={"model_allowlist": ["claude-opus-4-8"], "features": {"ai_chat": True}},
            ),
            pc.PricedComponent(
                kind="external_fee",
                key=su.SHOPIFY_STORE_COMPONENT_KEY,
                cost_basis=pc.CostBasis.fixed(fee_microusd),
                grants={"rail": "shopify"},
            ),
        ),
        margin_policy=pc.MarginPolicy(margin_floor=0.30, rounding="dollar"),
    )


def _shop_update_body(domain: str = SHOP, plan_name: str = "unlimited", **extra) -> str:
    payload = {"myshopify_domain": domain, "plan_name": plan_name, "plan_display_name": plan_name}
    payload.update(extra)
    return json.dumps(payload)


def _process(conn, body: str, topic: str = "shop/update"):
    return su.record_webhook_and_process(conn, topic=topic, raw_body=body)


# ── recompose mints the next version; grandfather preserved ─────────────────────────


def test_shop_update_recomposes_and_mints_next_version(pg_conn):
    slug = _mk_business(pg_conn)
    v1 = ents.upsert_plan_from_composition(pg_conn, slug, "starter", _composition(), tier="paid")
    # freeze v1 with a live (stripe-backed) subscriber
    ents.grant_entitlement(
        pg_conn, slug, email="sub@example.com", tier="paid", status="active",
        plan_key="starter", stripe_subscription_id="sub_live_1",
    )

    out = _process(pg_conn, _shop_update_body(plan_name="unlimited"))  # $39 → $399
    assert out["deduplicated"] is False
    processed = out["processed"]
    assert processed["recorded"] is True
    assert processed["business_slug"] == slug
    assert processed["fee_microusd_month"] == 399_000_000
    assert len(processed["recomposed"]) == 1
    minted_ref = processed["recomposed"][0]
    assert minted_ref["from_plan_key"] == "starter"
    assert minted_ref["plan_key"] == "starter-v2"

    # the minted row carries DERIVED economics + the recompose provenance receipt
    v2 = ents.get_plan_policy(pg_conn, slug, "starter-v2")
    assert v2 is not None
    assert v2.billing_interval == "month"
    assert v2.price_cents > v1.price_cents  # higher store fee → higher derived price
    comp_meta = v2.metadata[ents._COMPOSITION_METADATA_KEY]
    assert comp_meta["receipt"]["total_cogs_microusd_month"] == 30_000_000 + 399_000_000
    stored_fee = su._composition_shopify_fee(comp_meta["composition"])
    assert stored_fee == 399_000_000
    recompose_meta = v2.metadata["takyon_shopify_recompose"]
    assert recompose_meta["from_plan_key"] == "starter"
    assert recompose_meta["old_fee_microusd_month"] == 39_000_000
    assert recompose_meta["new_fee_microusd_month"] == 399_000_000

    # grandfather: the live v1 row is byte-identical on economics; the subscriber is untouched
    still_v1 = ents.get_plan_policy(pg_conn, slug, "starter")
    assert still_v1.price_cents == v1.price_cents
    assert still_v1.included_ai_budget_microusd == v1.included_ai_budget_microusd
    ent_row = pg_conn.execute(
        "select plan_key, status from app_entitlements where business_slug = %s", (slug,)
    ).fetchone()
    assert ent_row == ("starter", "active")


def test_replay_is_one_effect(pg_conn):
    """The SAME delivery body replayed (Shopify retry OR an attacker replaying a captured body
    under fresh header ids — the dedup id is content-derived, headers don't matter) is exactly one
    effect: one minted version, second call deduplicated."""
    slug = _mk_business(pg_conn)
    ents.upsert_plan_from_composition(pg_conn, slug, "starter", _composition(), tier="paid")
    body = _shop_update_body(plan_name="unlimited")

    first = _process(pg_conn, body)
    second = _process(pg_conn, body)
    assert first["deduplicated"] is False
    assert second["deduplicated"] is True
    assert second["processed"] is None

    plans = {p.plan_key for p in ents.list_plan_policies(pg_conn, slug)}
    assert plans == {"starter", "starter-v2"}  # no starter-v3

    dedup_rows = pg_conn.execute(
        "select count(*) from webhook_events where provider = 'shopify'"
    ).fetchone()
    assert dedup_rows[0] == 1


def test_second_distinct_delivery_with_same_fee_is_a_noop_mint(pg_conn):
    """A different delivery (distinct body → distinct dedup id) reporting the SAME fee recomposes
    nothing: the latest version already carries that fee — convergent, no version churn."""
    slug = _mk_business(pg_conn)
    ents.upsert_plan_from_composition(pg_conn, slug, "starter", _composition(), tier="paid")
    _process(pg_conn, _shop_update_body(plan_name="unlimited", updated_at="t1"))
    out = _process(pg_conn, _shop_update_body(plan_name="unlimited", updated_at="t2"))
    assert out["deduplicated"] is False
    assert out["processed"]["recomposed"] == []
    assert out["processed"]["unchanged"] == [
        {"plan_key": "starter-v2", "unchanged_fee_microusd": 399_000_000}
    ]
    plans = {p.plan_key for p in ents.list_plan_policies(pg_conn, slug)}
    assert plans == {"starter", "starter-v2"}


def test_stale_plan_flip_replay_cannot_roll_pricing_back(pg_conn):
    """A→B→A: after the shop legitimately returns to plan A, replaying the OLD captured B body is
    deduplicated (its content was already processed once) — pricing cannot be flipped back by
    replay. This is exactly why the dedup id is content-derived."""
    slug = _mk_business(pg_conn)
    ents.upsert_plan_from_composition(pg_conn, slug, "starter", _composition(), tier="paid")
    body_b = _shop_update_body(plan_name="unlimited")  # $399
    body_a = _shop_update_body(plan_name="basic")  # $39
    _process(pg_conn, body_b)  # mints starter-v2 @ 399
    _process(pg_conn, body_a)  # mints starter-v3 @ 39
    replay = _process(pg_conn, body_b)  # attacker replays the stale B body
    assert replay["deduplicated"] is True
    latest = ents.get_plan_policy(pg_conn, slug, "starter-v3")
    assert latest is not None
    assert su._composition_shopify_fee(
        latest.metadata[ents._COMPOSITION_METADATA_KEY]["composition"]
    ) == 39_000_000
    assert ents.get_plan_policy(pg_conn, slug, "starter-v4") is None


# ── fail-closed no-ops ──────────────────────────────────────────────────────────────


def test_unknown_shop_domain_changes_nothing(pg_conn):
    slug = _mk_business(pg_conn, shop_domain="other-store.myshopify.com")
    ents.upsert_plan_from_composition(pg_conn, slug, "starter", _composition(), tier="paid")
    out = _process(pg_conn, _shop_update_body(domain=SHOP, plan_name="unlimited"))
    assert out["processed"]["error"] == "unknown_shop_domain"
    assert {p.plan_key for p in ents.list_plan_policies(pg_conn, slug)} == {"starter"}


def test_unmapped_plan_name_changes_nothing(pg_conn):
    slug = _mk_business(pg_conn)
    ents.upsert_plan_from_composition(pg_conn, slug, "starter", _composition(), tier="paid")
    out = _process(pg_conn, _shop_update_body(plan_name="shopify_plus"))
    assert "shopify_plan_unmapped" in out["processed"]["error"]
    assert {p.plan_key for p in ents.list_plan_policies(pg_conn, slug)} == {"starter"}


def test_partner_dev_plan_uses_configured_fee(pg_conn):
    slug = _mk_business(pg_conn, partner_dev_fee=9_000_000)
    ents.upsert_plan_from_composition(pg_conn, slug, "starter", _composition(), tier="paid")
    out = _process(pg_conn, _shop_update_body(plan_name="partner_test"))
    assert out["processed"]["fee_microusd_month"] == 9_000_000
    assert out["processed"]["recomposed"][0]["plan_key"] == "starter-v2"


def test_partner_dev_plan_without_configured_fee_refuses(pg_conn):
    slug = _mk_business(pg_conn)  # no partner_dev_fee on the connection
    ents.upsert_plan_from_composition(pg_conn, slug, "starter", _composition(), tier="paid")
    out = _process(pg_conn, _shop_update_body(plan_name="partner_test"))
    assert "shopify_plan_unmapped" in out["processed"]["error"]
    assert {p.plan_key for p in ents.list_plan_policies(pg_conn, slug)} == {"starter"}


def test_missing_plan_name_changes_nothing(pg_conn):
    slug = _mk_business(pg_conn)
    ents.upsert_plan_from_composition(pg_conn, slug, "starter", _composition(), tier="paid")
    out = _process(pg_conn, json.dumps({"myshopify_domain": SHOP}))
    assert out["processed"]["error"] == "shop_update_missing_plan_name"
    assert {p.plan_key for p in ents.list_plan_policies(pg_conn, slug)} == {"starter"}


def test_other_topics_are_recorded_but_ignored(pg_conn):
    slug = _mk_business(pg_conn)
    ents.upsert_plan_from_composition(pg_conn, slug, "starter", _composition(), tier="paid")
    out = _process(pg_conn, _shop_update_body(plan_name="unlimited"), topic="app/uninstalled")
    assert out["processed"] == {"recorded": False, "ignored": "app/uninstalled"}
    assert {p.plan_key for p in ents.list_plan_policies(pg_conn, slug)} == {"starter"}


def test_invalid_json_body_fails_loud(pg_conn):
    with pytest.raises(su.ShopifyWebhookInvalidEvent):
        _process(pg_conn, "not-json")
    with pytest.raises(su.ShopifyWebhookInvalidEvent):
        _process(pg_conn, json.dumps(["a", "list"]))


def test_freehand_plans_without_composition_are_never_touched(pg_conn):
    """A transitional freehand plan (no stored composition) is not the recompose's to touch."""
    slug = _mk_business(pg_conn)
    ents.upsert_plan_policy(
        pg_conn, slug, "manual", tier="paid", price_cents=1000, included_ai_budget_microusd=5_000_000
    )
    out = _process(pg_conn, _shop_update_body(plan_name="unlimited"))
    assert out["processed"]["recomposed"] == []
    assert {p.plan_key for p in ents.list_plan_policies(pg_conn, slug)} == {"manual"}


def test_two_businesses_sharing_a_shop_both_recompose(pg_conn):
    """Two businesses that recorded the same shop each get their own recompose (deterministic
    slug order) — no arbitrary first-match winner."""
    slug_a = _mk_business(pg_conn)
    slug_b = _mk_business(pg_conn)
    ents.upsert_plan_from_composition(pg_conn, slug_a, "starter", _composition(), tier="paid")
    ents.upsert_plan_from_composition(pg_conn, slug_b, "growth", _composition(), tier="paid")
    out = _process(pg_conn, _shop_update_body(plan_name="unlimited"))
    processed = out["processed"]
    assert processed["recorded"] is True
    per_business = {o["business_slug"]: o for o in processed["businesses"]}
    assert set(per_business) == {slug_a, slug_b}
    assert per_business[slug_a]["recomposed"][0]["plan_key"] == "starter-v2"
    assert per_business[slug_b]["recomposed"][0]["plan_key"] == "growth-v2"


def test_0060_grants_safebox_role_plan_write_authority(pg_conn):
    """The 0060 grants migration (applied by the canonical runner in this fixture) gives
    takyon_safebox_authority the app_plan_policies INSERT/UPDATE the webhook recompose runs
    under on prod — and nothing broader (no DELETE)."""
    row = pg_conn.execute(
        "select has_table_privilege('takyon_safebox_authority', 'app_plan_policies', 'insert'), "
        "       has_table_privilege('takyon_safebox_authority', 'app_plan_policies', 'update'), "
        "       has_table_privilege('takyon_safebox_authority', 'app_plan_policies', 'delete')"
    ).fetchone()
    assert row == (True, True, False)


# ── the safebox authority route: HMAC verified safebox-side BEFORE processing ────────


def _sign(body: str, secret: str) -> str:
    return base64.b64encode(
        hmac.new(secret.encode(), body.encode(), hashlib.sha256).digest()
    ).decode()


@pytest.fixture()
def safebox_client(monkeypatch, pg_conn):
    """The safebox app on the safebox host role, with its DB conn pointed at the throwaway PG —
    the FULL safebox-side path (token gate → local secret → HMAC → dedup → recompose) end-to-end."""
    from starlette.testclient import TestClient

    from plugins.takyon import safebox, safebox_app

    monkeypatch.setenv("TAKYON_HOST_ROLE", "safebox")
    monkeypatch.setenv(safebox_app._SAFEBOX_TOKEN_ENV, "shared-token")
    monkeypatch.setenv("SHOPIFY_WEBHOOK_SECRET", "shpss_test_secret")
    monkeypatch.setattr(safebox, "load_env", lambda: {})

    @contextlib.contextmanager
    def _conn():
        yield pg_conn

    monkeypatch.setattr(safebox_app, "_safebox_db_conn", _conn)
    return TestClient(safebox_app.build_safebox_app())


def test_safebox_route_requires_internal_token(safebox_client):
    body = _shop_update_body()
    resp = safebox_client.post(
        "/v1/shopify/app-webhook/process",
        json={"raw_body": body, "hmac_sha256": _sign(body, "shpss_test_secret"), "topic": "shop/update"},
    )
    assert resp.status_code == 401


def test_safebox_route_rejects_bad_hmac_before_any_processing(safebox_client, pg_conn, monkeypatch):
    from plugins.takyon import shopify_util

    def _boom(*a, **k):
        raise AssertionError("processor must not run on a failed HMAC")

    monkeypatch.setattr(shopify_util, "record_webhook_and_process", _boom)
    body = _shop_update_body()
    resp = safebox_client.post(
        "/v1/shopify/app-webhook/process",
        headers={"Authorization": "Bearer shared-token"},
        json={"raw_body": body, "hmac_sha256": _sign(body, "wrong-secret"), "topic": "shop/update"},
    )
    assert resp.status_code == 401
    assert resp.json()["detail"] == "invalid_signature"
    rows = pg_conn.execute("select count(*) from webhook_events").fetchone()
    assert rows[0] == 0  # nothing recorded, nothing processed


def test_safebox_route_missing_secret_is_503(safebox_client, monkeypatch):
    monkeypatch.delenv("SHOPIFY_WEBHOOK_SECRET", raising=False)
    body = _shop_update_body()
    resp = safebox_client.post(
        "/v1/shopify/app-webhook/process",
        headers={"Authorization": "Bearer shared-token"},
        json={"raw_body": body, "hmac_sha256": _sign(body, "shpss_test_secret"), "topic": "shop/update"},
    )
    assert resp.status_code == 503
    assert resp.json()["detail"] == "shopify_webhook_unconfigured"


def test_safebox_route_verified_hmac_processes_end_to_end(safebox_client, pg_conn):
    slug = _mk_business(pg_conn)
    ents.upsert_plan_from_composition(pg_conn, slug, "starter", _composition(), tier="paid")
    body = _shop_update_body(plan_name="unlimited")
    resp = safebox_client.post(
        "/v1/shopify/app-webhook/process",
        headers={"Authorization": "Bearer shared-token"},
        json={"raw_body": body, "hmac_sha256": _sign(body, "shpss_test_secret"), "topic": "shop/update"},
    )
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["deduplicated"] is False
    assert payload["processed"]["recomposed"][0]["plan_key"] == "starter-v2"
    assert ents.get_plan_policy(pg_conn, slug, "starter-v2") is not None
