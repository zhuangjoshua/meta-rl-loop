"""Postgres integration tests for the product plan catalog + sub-user entitlements — Phase 5(b).

Phase 5 acceptance (this slice): a business defines a plan catalog and grants entitlements to its
customers (product sub-users), all scoped by business_slug. The correctness details pinned here:
  * a plan catalog is idempotent on (business_slug, plan_key); Stripe product/price ids survive a
    re-upsert that omits them (COALESCE-preserve), every other field overwrites;
  * the MONEY-TRUTH guard: a manual non-free grant with no Stripe evidence is rejected and writes
    NOTHING (it would fake billing state), but an explicit non-billing source / metadata flag, or
    real Stripe evidence, lets it through;
  * the effective tier of a sub-user is resolved across their grants (active/trialing only,
    highest rank wins) and cached onto app_users.tier — a cancelled grant confers nothing.

Real engine on real Postgres (never mocks). Skips unless psycopg is importable and
TAKYON_TEST_PG_DSN is set.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest

psycopg = pytest.importorskip("psycopg")

from plugins.takyon import app_entitlements, app_identity  # noqa: E402
from plugins.takyon.app_entitlements import (  # noqa: E402
    AppUserNotFound,
    FakeBillingRejected,
    InvalidPlan,
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


def _user(conn, slug, email="cust@example.com") -> str:
    return app_identity.upsert_app_user(conn, slug, email).id


# ── plan catalog ─────────────────────────────────────────────────────────────────


def test_upsert_plan_creates_with_documented_defaults(pg_conn):
    slug = _business(pg_conn, _owner(pg_conn))
    plan = app_entitlements.upsert_plan_policy(pg_conn, slug, "pro")
    assert plan.plan_key == "pro"
    assert plan.tier == "pro"  # defaults to the plan_key when tier unspecified
    assert plan.price_cents == 0
    assert plan.currency == "usd"
    assert plan.billing_interval == "month"
    assert plan.included_ai_budget_microusd == 0
    assert plan.included_action_quota == 0  # documented default
    assert plan.source == "takyon"


def test_upsert_plan_idempotent_on_business_plan_key(pg_conn):
    slug = _business(pg_conn, _owner(pg_conn))
    first = app_entitlements.upsert_plan_policy(pg_conn, slug, "pro", price_cents=1000)
    again = app_entitlements.upsert_plan_policy(pg_conn, slug, "pro", price_cents=2500)
    assert again.id == first.id  # same row, not a second plan
    assert again.price_cents == 2500
    count = pg_conn.execute(
        "select count(*) from app_plan_policies where business_slug = %s", (slug,)
    ).fetchone()[0]
    assert count == 1


def test_upsert_plan_coalesce_preserves_stripe_ids(pg_conn):
    slug = _business(pg_conn, _owner(pg_conn))
    app_entitlements.upsert_plan_policy(
        pg_conn, slug, "pro", stripe_product_id="prod_A", stripe_price_id="price_A"
    )
    # re-upsert WITHOUT the Stripe ids: the prior linkage must survive
    kept = app_entitlements.upsert_plan_policy(pg_conn, slug, "pro", price_cents=999)
    assert kept.stripe_product_id == "prod_A"
    assert kept.stripe_price_id == "price_A"
    # re-upsert WITH new ids: they overwrite
    changed = app_entitlements.upsert_plan_policy(pg_conn, slug, "pro", stripe_price_id="price_B")
    assert changed.stripe_product_id == "prod_A"  # untouched, still preserved
    assert changed.stripe_price_id == "price_B"


def test_upsert_plan_normalizes_billing_interval_aliases(pg_conn):
    slug = _business(pg_conn, _owner(pg_conn))
    assert app_entitlements.upsert_plan_policy(
        pg_conn, slug, "m", billing_interval="monthly"
    ).billing_interval == "month"
    assert app_entitlements.upsert_plan_policy(
        pg_conn, slug, "y", billing_interval="yearly"
    ).billing_interval == "year"
    assert app_entitlements.upsert_plan_policy(
        pg_conn, slug, "o", billing_interval="once"
    ).billing_interval == "one_time"


def test_upsert_plan_rejects_bad_interval(pg_conn):
    slug = _business(pg_conn, _owner(pg_conn))
    with pytest.raises(InvalidPlan):
        app_entitlements.upsert_plan_policy(pg_conn, slug, "pro", billing_interval="weekly")


def test_upsert_plan_rejects_negative_price(pg_conn):
    slug = _business(pg_conn, _owner(pg_conn))
    with pytest.raises(InvalidPlan):
        app_entitlements.upsert_plan_policy(pg_conn, slug, "pro", price_cents=-1)


def test_upsert_monthly_plan_rejects_included_ai_budget_above_plan_price(pg_conn):
    slug = _business(pg_conn, _owner(pg_conn))
    with pytest.raises(InvalidPlan):
        app_entitlements.upsert_plan_policy(
            pg_conn,
            slug,
            "pro",
            tier="paid",
            price_cents=1900,
            billing_interval="month",
            included_ai_budget_microusd=19_000_001,
        )


def test_upsert_monthly_plan_price_drop_reuses_existing_budget_and_rejects_if_now_too_high(pg_conn):
    slug = _business(pg_conn, _owner(pg_conn))
    app_entitlements.upsert_plan_policy(
        pg_conn,
        slug,
        "pro",
        tier="paid",
        price_cents=1900,
        billing_interval="month",
        included_ai_budget_microusd=5_000_000,
    )
    with pytest.raises(InvalidPlan):
        app_entitlements.upsert_plan_policy(
            pg_conn,
            slug,
            "pro",
            tier="paid",
            price_cents=300,
            billing_interval="month",
        )


def test_upsert_plan_normalizes_plan_key_slug(pg_conn):
    slug = _business(pg_conn, _owner(pg_conn))
    plan = app_entitlements.upsert_plan_policy(pg_conn, slug, "Pro Plan")
    assert plan.plan_key == "pro-plan"  # slugified
    # a lookup with the un-slugified form resolves the same row
    assert app_entitlements.get_plan_policy(pg_conn, slug, "Pro Plan").id == plan.id


def test_upsert_plan_unknown_business_fails_loud(pg_conn):
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        app_entitlements.upsert_plan_policy(pg_conn, "ghost-biz", "pro")


def test_upsert_plan_folds_validation_warnings_into_metadata(pg_conn):
    slug = _business(pg_conn, _owner(pg_conn))
    # metadata claims unlimited, but quota is finite → a coherence warning
    plan = app_entitlements.upsert_plan_policy(
        pg_conn,
        slug,
        "pro",
        included_action_quota=10,
        metadata={"marketing": "unlimited everything"},
    )
    validation = plan.metadata.get("takyon_plan_validation")
    assert validation and validation["status"] == "warning"
    assert any("unlimited" in w for w in validation["warnings"])


def test_list_plan_policies_orders_cheapest_first(pg_conn):
    slug = _business(pg_conn, _owner(pg_conn))
    app_entitlements.upsert_plan_policy(pg_conn, slug, "pro", price_cents=2000)
    app_entitlements.upsert_plan_policy(pg_conn, slug, "free", price_cents=0)
    app_entitlements.upsert_plan_policy(pg_conn, slug, "mid", price_cents=1000)
    prices = [p.price_cents for p in app_entitlements.list_plan_policies(pg_conn, slug)]
    assert prices == sorted(prices)


# ── entitlements: provisioning + the money-truth guard ─────────────────────────────


def test_grant_free_entitlement_by_email_provisions_user_and_sets_tier(pg_conn):
    slug = _business(pg_conn, _owner(pg_conn))
    ent, effective = app_entitlements.grant_entitlement(
        pg_conn, slug, email="Alice@Example.com", name="Alice", tier="free"
    )
    assert effective == "free"
    # the sub-user was provisioned as a side of the grant
    user = app_identity.get_app_user(pg_conn, slug, email="alice@example.com")
    assert user is not None and user.id == ent.app_user_id
    assert user.tier == "free"  # cached onto app_users


def test_grant_paid_with_stripe_evidence_sets_paid_tier(pg_conn):
    slug = _business(pg_conn, _owner(pg_conn))
    period_end = datetime(2027, 1, 1, tzinfo=timezone.utc)
    ent, effective = app_entitlements.grant_entitlement(
        pg_conn,
        slug,
        email="bob@example.com",
        tier="paid",
        source="stripe",
        stripe_subscription_id="sub_123",
        current_period_end=period_end,
    )
    assert effective == "paid"
    assert ent.stripe_subscription_id == "sub_123"
    assert ent.current_period_end == period_end  # timestamptz round-trips
    assert app_identity.get_app_user(pg_conn, slug, email="bob@example.com").tier == "paid"


def test_grant_manual_paid_without_evidence_is_rejected_and_writes_nothing(pg_conn):
    slug = _business(pg_conn, _owner(pg_conn))
    user_id = _user(pg_conn, slug, "carol@example.com")
    with pytest.raises(FakeBillingRejected):
        app_entitlements.grant_entitlement(pg_conn, slug, app_user_id=user_id, tier="paid")
    # the guard fires BEFORE any write — no entitlement row, tier stays free
    rows = pg_conn.execute(
        "select count(*) from app_entitlements where business_slug = %s and app_user_id = %s",
        (slug, user_id),
    ).fetchone()[0]
    assert rows == 0
    assert app_identity.get_app_user(pg_conn, slug, app_user_id=user_id).tier == "free"


def test_grant_manual_paid_allowed_with_explicit_non_billing_source(pg_conn):
    slug = _business(pg_conn, _owner(pg_conn))
    _, effective = app_entitlements.grant_entitlement(
        pg_conn, slug, email="dave@example.com", tier="paid", source="comp"
    )
    assert effective == "paid"  # comp is an explicit non-billing grant, not a faked sale


def test_grant_manual_paid_allowed_with_metadata_non_billing(pg_conn):
    slug = _business(pg_conn, _owner(pg_conn))
    _, effective = app_entitlements.grant_entitlement(
        pg_conn, slug, email="erin@example.com", tier="paid", metadata={"non_billing": True}
    )
    assert effective == "paid"


# ── entitlements: effective-tier resolution ────────────────────────────────────────


def test_effective_tier_picks_highest_rank_among_active(pg_conn):
    slug = _business(pg_conn, _owner(pg_conn))
    user_id = _user(pg_conn, slug, "frank@example.com")
    app_entitlements.grant_entitlement(pg_conn, slug, app_user_id=user_id, tier="free")
    _, after_paid = app_entitlements.grant_entitlement(
        pg_conn, slug, app_user_id=user_id, tier="paid", stripe_customer_id="cus_1"
    )
    assert after_paid == "paid"  # paid outranks the still-present free grant
    _, after_owner = app_entitlements.grant_entitlement(
        pg_conn, slug, app_user_id=user_id, tier="owner", source="owner"
    )
    assert after_owner == "owner"  # owner outranks paid


def test_cancelled_entitlement_does_not_confer_tier(pg_conn):
    slug = _business(pg_conn, _owner(pg_conn))
    user_id = _user(pg_conn, slug, "grace@example.com")
    # a paid grant, but already cancelled (real Stripe evidence clears the money-truth guard)
    _, effective = app_entitlements.grant_entitlement(
        pg_conn,
        slug,
        app_user_id=user_id,
        tier="paid",
        status="cancelled",
        stripe_subscription_id="sub_dead",
    )
    assert effective == "free"  # cancelled is not active/trialing → confers nothing


def test_resolve_user_tier_recomputes_after_out_of_band_status_change(pg_conn):
    slug = _business(pg_conn, _owner(pg_conn))
    user_id = _user(pg_conn, slug, "heidi@example.com")
    ent, effective = app_entitlements.grant_entitlement(
        pg_conn, slug, app_user_id=user_id, tier="paid", stripe_subscription_id="sub_live"
    )
    assert effective == "paid"
    # simulate a subscription lapse recorded out of band (the 5d webhook will do this)
    pg_conn.execute(
        "update app_entitlements set status = 'cancelled' where id = %s", (ent.id,)
    )
    assert app_entitlements.resolve_user_tier(pg_conn, slug, user_id) == "free"
    assert app_identity.get_app_user(pg_conn, slug, app_user_id=user_id).tier == "free"


# ── entitlements: validation + scoping ─────────────────────────────────────────────


def test_grant_by_unknown_app_user_id_raises(pg_conn):
    slug = _business(pg_conn, _owner(pg_conn))
    with pytest.raises(AppUserNotFound):
        app_entitlements.grant_entitlement(pg_conn, slug, app_user_id=uuid.uuid4().hex, tier="free")


def test_grant_requires_user_or_email(pg_conn):
    slug = _business(pg_conn, _owner(pg_conn))
    with pytest.raises(ValueError):
        app_entitlements.grant_entitlement(pg_conn, slug, tier="free")


def test_grant_unknown_business_by_email_fails_loud(pg_conn):
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        app_entitlements.grant_entitlement(pg_conn, "ghost-biz", email="x@example.com", tier="free")


def test_entitlements_are_business_scoped(pg_conn):
    owner = _owner(pg_conn)
    slug_a = _business(pg_conn, owner, "A")
    slug_b = _business(pg_conn, owner, "B")
    app_entitlements.grant_entitlement(pg_conn, slug_a, email="ivan@example.com", tier="free")
    # the grant in A is invisible from B
    assert app_entitlements.list_entitlements(pg_conn, slug_b) == []
    # the same email in B is a distinct sub-user with its own (empty) entitlement history
    b_user = app_identity.upsert_app_user(pg_conn, slug_b, "ivan@example.com")
    assert app_entitlements.list_entitlements(pg_conn, slug_b, app_user_id=b_user.id) == []


def test_list_entitlements_scoped_to_user(pg_conn):
    slug = _business(pg_conn, _owner(pg_conn))
    u1 = _user(pg_conn, slug, "judy@example.com")
    u2 = _user(pg_conn, slug, "karl@example.com")
    app_entitlements.grant_entitlement(pg_conn, slug, app_user_id=u1, tier="free")
    app_entitlements.grant_entitlement(pg_conn, slug, app_user_id=u1, tier="paid", stripe_customer_id="c")
    app_entitlements.grant_entitlement(pg_conn, slug, app_user_id=u2, tier="free")
    assert len(app_entitlements.list_entitlements(pg_conn, slug, app_user_id=u1)) == 2
    assert len(app_entitlements.list_entitlements(pg_conn, slug, app_user_id=u2)) == 1
    assert len(app_entitlements.list_entitlements(pg_conn, slug)) == 3
