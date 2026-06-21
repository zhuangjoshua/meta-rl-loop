"""Postgres integration tests for the product plan catalog + sub-user entitlements — Phase 5(b).

Phase 5 acceptance (this slice): a business defines a plan catalog and grants entitlements to its
customers (product sub-users), all scoped by business_slug. The correctness details pinned here:
  * a plan catalog is idempotent on (business_slug, plan_key); Stripe product/price ids survive a
    re-upsert that omits them (COALESCE-preserve), every other field overwrites;
  * the MONEY-TRUTH guard: an entitlement with no Stripe evidence is rejected and writes
    NOTHING (it would fake billing state); only real Stripe evidence lets it through;
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
    GrandfatheredPlanFrozen,
    InvalidEntitlementTier,
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
    app_entitlements.upsert_plan_policy(pg_conn, slug, "mid", price_cents=1000)
    app_entitlements.upsert_plan_policy(pg_conn, slug, "enterprise", price_cents=4000)
    prices = [p.price_cents for p in app_entitlements.list_plan_policies(pg_conn, slug)]
    assert prices == sorted(prices)


# ── entitlements: provisioning + the money-truth guard ─────────────────────────────


def test_grant_free_entitlement_is_rejected(pg_conn):
    slug = _business(pg_conn, _owner(pg_conn))
    with pytest.raises(InvalidEntitlementTier):
        app_entitlements.grant_entitlement(
            pg_conn, slug, email="Alice@Example.com", name="Alice", tier="free"
        )
    assert app_identity.get_app_user(pg_conn, slug, email="alice@example.com") is None


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
    # the guard fires BEFORE any write — no entitlement row, tier stays unentitled
    rows = pg_conn.execute(
        "select count(*) from app_entitlements where business_slug = %s and app_user_id = %s",
        (slug, user_id),
    ).fetchone()[0]
    assert rows == 0
    assert app_identity.get_app_user(pg_conn, slug, app_user_id=user_id).tier == "unentitled"


def test_grant_paid_without_evidence_rejects_even_with_non_billing_source(pg_conn):
    slug = _business(pg_conn, _owner(pg_conn))
    with pytest.raises(FakeBillingRejected):
        app_entitlements.grant_entitlement(
            pg_conn, slug, email="dave@example.com", tier="paid", source="comp"
        )


def test_grant_paid_without_evidence_rejects_even_with_non_billing_metadata(pg_conn):
    slug = _business(pg_conn, _owner(pg_conn))
    with pytest.raises(FakeBillingRejected):
        app_entitlements.grant_entitlement(
            pg_conn, slug, email="erin@example.com", tier="paid", metadata={"non_billing": True}
        )


# ── entitlements: effective-tier resolution ────────────────────────────────────────


def test_effective_tier_picks_highest_rank_among_active(pg_conn):
    slug = _business(pg_conn, _owner(pg_conn))
    user_id = _user(pg_conn, slug, "frank@example.com")
    pg_conn.execute(
        "insert into app_entitlements (business_slug, app_user_id, tier, status, source, metadata) "
        "values (%s, %s, 'free', 'active', 'legacy', '{}'::jsonb)",
        (slug, user_id),
    )
    _, after_paid = app_entitlements.grant_entitlement(
        pg_conn, slug, app_user_id=user_id, tier="paid", stripe_customer_id="cus_1"
    )
    assert after_paid == "paid"  # paid outranks and legacy free is ignored
    with pytest.raises(FakeBillingRejected):
        app_entitlements.grant_entitlement(
            pg_conn, slug, app_user_id=user_id, tier="owner", source="owner"
        )


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
    assert effective == "unentitled"  # cancelled is not active/trialing → confers nothing


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
    assert app_entitlements.resolve_user_tier(pg_conn, slug, user_id) == "unentitled"
    assert app_identity.get_app_user(pg_conn, slug, app_user_id=user_id).tier == "unentitled"


# ── entitlements: validation + scoping ─────────────────────────────────────────────


def test_grant_by_unknown_app_user_id_raises(pg_conn):
    slug = _business(pg_conn, _owner(pg_conn))
    with pytest.raises(AppUserNotFound):
        app_entitlements.grant_entitlement(
            pg_conn,
            slug,
            app_user_id=uuid.uuid4().hex,
            tier="paid",
            stripe_customer_id="cus_missing",
        )


def test_grant_requires_user_or_email(pg_conn):
    slug = _business(pg_conn, _owner(pg_conn))
    with pytest.raises(ValueError):
        app_entitlements.grant_entitlement(
            pg_conn,
            slug,
            tier="paid",
            stripe_customer_id="cus_missing_user",
        )


def test_grant_unknown_business_by_email_fails_loud(pg_conn):
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        app_entitlements.grant_entitlement(
            pg_conn,
            "ghost-biz",
            email="x@example.com",
            tier="paid",
            stripe_customer_id="cus_ghost",
        )


def test_entitlements_are_business_scoped(pg_conn):
    owner = _owner(pg_conn)
    slug_a = _business(pg_conn, owner, "A")
    slug_b = _business(pg_conn, owner, "B")
    app_entitlements.grant_entitlement(
        pg_conn,
        slug_a,
        email="ivan@example.com",
        tier="paid",
        stripe_customer_id="cus_a",
    )
    # the grant in A is invisible from B
    assert app_entitlements.list_entitlements(pg_conn, slug_b) == []
    # the same email in B is a distinct sub-user with its own (empty) entitlement history
    b_user = app_identity.upsert_app_user(pg_conn, slug_b, "ivan@example.com")
    assert app_entitlements.list_entitlements(pg_conn, slug_b, app_user_id=b_user.id) == []


def test_list_entitlements_scoped_to_user(pg_conn):
    slug = _business(pg_conn, _owner(pg_conn))
    u1 = _user(pg_conn, slug, "judy@example.com")
    u2 = _user(pg_conn, slug, "karl@example.com")
    app_entitlements.grant_entitlement(
        pg_conn, slug, app_user_id=u1, tier="paid", stripe_customer_id="cus_1"
    )
    app_entitlements.grant_entitlement(
        pg_conn, slug, app_user_id=u1, tier="pro", stripe_customer_id="cus_2"
    )
    app_entitlements.grant_entitlement(
        pg_conn, slug, app_user_id=u2, tier="paid", stripe_customer_id="cus_3"
    )
    assert len(app_entitlements.list_entitlements(pg_conn, slug, app_user_id=u1)) == 2
    assert len(app_entitlements.list_entitlements(pg_conn, slug, app_user_id=u2)) == 1
    assert len(app_entitlements.list_entitlements(pg_conn, slug)) == 3


# ── grandfather guard: a live plan_key's economic terms are frozen ───────────────────
#
# Re-pricing a plan_key that someone is actively subscribed to would silently mutate
# existing (grandfathered) users, because entitlements reference the live plan row, not a
# price snapshot. The leaf refuses it; new pricing must be a NEW plan_key. There is no
# override flag — this invariant is not bypassable by the caller.


def _subscribe(conn, slug, plan_key, *, status="active", email="cust@example.com"):
    """Grant an entitlement that locks `plan_key` (with Stripe evidence so it is not rejected)."""
    user_id = _user(conn, slug, email=email)
    app_entitlements.grant_entitlement(
        conn,
        slug,
        app_user_id=user_id,
        tier="paid",
        status=status,
        source="stripe",
        stripe_subscription_id=f"sub_{uuid.uuid4().hex[:8]}",
        plan_key=plan_key,
    )
    return user_id


def test_count_active_entitlements_for_plan_counts_only_active(pg_conn):
    slug = _business(pg_conn, _owner(pg_conn))
    app_entitlements.upsert_plan_policy(pg_conn, slug, "pro", tier="paid", price_cents=1000)
    assert app_entitlements.count_active_entitlements_for_plan(pg_conn, slug, "pro") == 0
    _subscribe(pg_conn, slug, "pro", email="a@example.com")
    _subscribe(pg_conn, slug, "pro", status="canceled", email="b@example.com")
    # canceled does not confer a tier, so it must not count toward the freeze
    assert app_entitlements.count_active_entitlements_for_plan(pg_conn, slug, "pro") == 1


def test_reprice_plan_without_subscribers_is_allowed(pg_conn):
    slug = _business(pg_conn, _owner(pg_conn))
    app_entitlements.upsert_plan_policy(pg_conn, slug, "pro", tier="paid", price_cents=1000)
    # no live subscribers → free to re-price in place
    plan = app_entitlements.upsert_plan_policy(pg_conn, slug, "pro", tier="paid", price_cents=2000)
    assert plan.price_cents == 2000


def test_reprice_plan_with_active_subscriber_is_frozen(pg_conn):
    slug = _business(pg_conn, _owner(pg_conn))
    app_entitlements.upsert_plan_policy(pg_conn, slug, "pro", tier="paid", price_cents=2000)
    _subscribe(pg_conn, slug, "pro")
    with pytest.raises(GrandfatheredPlanFrozen) as exc:
        app_entitlements.upsert_plan_policy(pg_conn, slug, "pro", tier="paid", price_cents=3000)
    assert "price_cents" in str(exc.value)
    # the live plan row is untouched — the grandfathered subscriber keeps the old price
    assert app_entitlements.get_plan_policy(pg_conn, slug, "pro").price_cents == 2000


def test_freeze_covers_included_ai_budget_on_live_plan(pg_conn):
    slug = _business(pg_conn, _owner(pg_conn))
    app_entitlements.upsert_plan_policy(
        pg_conn, slug, "pro", tier="paid", price_cents=2000, included_ai_budget_microusd=5_000_000
    )
    _subscribe(pg_conn, slug, "pro")
    # the included AI budget is an economic term; cutting it would tighten the runtime gate on
    # an existing subscriber, so it is frozen too
    with pytest.raises(GrandfatheredPlanFrozen) as exc:
        app_entitlements.upsert_plan_policy(
            pg_conn,
            slug,
            "pro",
            tier="paid",
            price_cents=2000,
            included_ai_budget_microusd=1_000_000,
        )
    assert "included_ai_budget_microusd" in str(exc.value)


def test_idempotent_reupsert_of_live_plan_is_allowed(pg_conn):
    slug = _business(pg_conn, _owner(pg_conn))
    app_entitlements.upsert_plan_policy(
        pg_conn, slug, "pro", tier="paid", price_cents=2000, included_ai_budget_microusd=1_000_000
    )
    _subscribe(pg_conn, slug, "pro")
    # identical economic terms → no change → must NOT be refused (retries/idempotency)
    plan = app_entitlements.upsert_plan_policy(
        pg_conn, slug, "pro", tier="paid", price_cents=2000, included_ai_budget_microusd=1_000_000
    )
    assert plan.price_cents == 2000


def test_non_economic_edit_of_live_plan_is_allowed(pg_conn):
    slug = _business(pg_conn, _owner(pg_conn))
    app_entitlements.upsert_plan_policy(pg_conn, slug, "pro", tier="paid", price_cents=2000)
    _subscribe(pg_conn, slug, "pro")
    # editing notes / Stripe linkage while re-passing the same economic terms is fine
    plan = app_entitlements.upsert_plan_policy(
        pg_conn,
        slug,
        "pro",
        tier="paid",
        price_cents=2000,
        notes="clarified copy",
        stripe_price_id="price_live",
    )
    assert plan.notes == "clarified copy"
    assert plan.stripe_price_id == "price_live"


def test_new_plan_key_for_new_pricing_is_allowed(pg_conn):
    slug = _business(pg_conn, _owner(pg_conn))
    app_entitlements.upsert_plan_policy(pg_conn, slug, "pro", tier="paid", price_cents=2000)
    _subscribe(pg_conn, slug, "pro")
    # the sanctioned path: mint a NEW plan_key version; the old subscriber is untouched
    new_plan = app_entitlements.upsert_plan_policy(pg_conn, slug, "pro-2", tier="paid", price_cents=3000)
    assert new_plan.plan_key == "pro-2"
    assert new_plan.price_cents == 3000
    assert app_entitlements.get_plan_policy(pg_conn, slug, "pro").price_cents == 2000


def test_project_openmeter_access_keeps_stripe_access_authoritative(pg_conn):
    slug = _business(pg_conn, _owner(pg_conn))
    user_id = _user(pg_conn, slug)
    app_entitlements.upsert_plan_policy(pg_conn, slug, "pro", tier="paid", price_cents=2000)
    stripe_entitlement, _ = app_entitlements.grant_entitlement(
        pg_conn,
        slug,
        app_user_id=user_id,
        tier="paid",
        source="stripe",
        stripe_customer_id="cus_test",
        stripe_subscription_id="sub_test",
        stripe_checkout_session_id="cs_test",
        plan_key="pro",
    )
    projected, effective = app_entitlements.project_openmeter_access(
        pg_conn,
        slug,
        user_id,
        active=True,
        tier="paid",
        plan_key="pro",
        metadata={"openmeter_customer_key": "om_customer"},
    )
    assert projected is not None
    assert projected.source == "openmeter"
    assert projected.plan_key == "pro"
    assert effective == "paid"
    stripe_row = pg_conn.execute(
        "select status from app_entitlements where id = %s",
        (stripe_entitlement.id,),
    ).fetchone()
    assert stripe_row is not None
    assert stripe_row[0] == "active"
    rows = pg_conn.execute(
        "select source, status from app_entitlements "
        "where business_slug = %s and app_user_id = %s order by source, id",
        (slug, user_id),
    ).fetchall()
    assert ("openmeter", "active") in rows
    assert ("stripe", "active") in rows


def test_project_openmeter_access_inactive_only_retires_openmeter_rows(pg_conn):
    slug = _business(pg_conn, _owner(pg_conn))
    user_id = _user(pg_conn, slug)
    app_entitlements.upsert_plan_policy(pg_conn, slug, "pro", tier="paid", price_cents=2000)
    app_entitlements.grant_entitlement(
        pg_conn,
        slug,
        app_user_id=user_id,
        tier="paid",
        source="stripe",
        stripe_customer_id="cus_test",
        stripe_subscription_id="sub_test",
        stripe_checkout_session_id="cs_test",
        plan_key="pro",
    )
    app_entitlements.project_openmeter_access(
        pg_conn,
        slug,
        user_id,
        active=True,
        tier="paid",
        plan_key="pro",
        metadata={"openmeter_customer_key": "om_customer"},
    )
    projected, effective = app_entitlements.project_openmeter_access(
        pg_conn,
        slug,
        user_id,
        active=False,
        metadata={"openmeter_customer_key": "om_customer"},
    )
    assert projected is None
    assert effective == "paid"
    active = app_entitlements.get_active_entitlement(pg_conn, slug, user_id)
    assert active is not None
    assert active.source == "stripe"
    statuses = [
        (row[0], row[1])
        for row in pg_conn.execute(
            "select source, status from app_entitlements "
            "where business_slug = %s and app_user_id = %s",
            (slug, user_id),
        ).fetchall()
    ]
    assert statuses
    assert ("stripe", "active") in statuses
    assert ("openmeter", "cancelled") in statuses
