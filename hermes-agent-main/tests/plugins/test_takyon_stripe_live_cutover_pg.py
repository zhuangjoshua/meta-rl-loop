from __future__ import annotations

import uuid

import pytest

psycopg = pytest.importorskip("psycopg")


def _seed_business(conn):
    owner = uuid.uuid4()
    slug = f"cutover-{uuid.uuid4().hex[:8]}"
    app_user = uuid.uuid4()
    conn.execute(
        "insert into users (id, auth0_sub, operator_billing_customer_id, "
        "operator_billing_subscription_id, operator_billing_subscription_status) "
        "values (%s, %s, 'cus_test', 'sub_test', 'active')",
        (owner, f"auth0|{uuid.uuid4().hex}"),
    )
    conn.execute(
        "insert into businesses (slug, name, goal, status, mode, owner_user_id) "
        "values (%s, %s, 'g', 'active', 'live', %s)",
        (slug, slug, owner),
    )
    conn.execute(
        "insert into app_users (id, business_slug, email, tier) values (%s, %s, %s, 'paid')",
        (app_user, slug, "customer@example.com"),
    )
    conn.execute(
        "insert into app_plan_policies (business_slug, plan_key, tier, price_cents, currency, "
        "billing_interval, stripe_product_id, stripe_price_id) "
        "values (%s, 'monthly', 'paid', 2900, 'usd', 'month', 'prod_test', 'price_test')",
        (slug,),
    )
    conn.execute(
        "insert into app_entitlements (business_slug, app_user_id, tier, status, source, "
        "stripe_subscription_id, plan_key) values (%s, %s, 'paid', 'active', 'stripe', "
        "'sub_test', 'monthly')",
        (slug, app_user),
    )
    intent = conn.execute(
        "insert into app_checkout_intents (business_slug, app_user_id, plan_key, "
        "client_reference_id, customer_email) values (%s, %s, 'monthly', %s, %s) returning id",
        (slug, app_user, uuid.uuid4().hex, "customer@example.com"),
    ).fetchone()[0]
    conn.execute(
        "insert into app_revenue_events (business_slug, provider_event_id, stripe_object_type, "
        "stripe_object_id, status, currency, amount_paid_cents, customer_email) "
        "values (%s, %s, 'checkout.session', %s, 'paid', 'usd', 2900, %s)",
        (slug, f"evt_{uuid.uuid4().hex}", f"cs_{uuid.uuid4().hex}", "customer@example.com"),
    )
    return owner, slug, app_user, intent


def test_schema_migration_does_not_auto_retire_data_and_claim_acl_is_narrow(pg_conn):
    _owner, slug, app_user, intent = _seed_business(pg_conn)
    pg_conn.execute(
        "insert into app_plan_policies (business_slug, plan_key, tier, price_cents, currency, "
        "billing_interval, stripe_product_id, stripe_price_id) "
        "values (%s, 'unused', 'paid', 3900, 'usd', 'month', 'prod_unused', 'price_unused')",
        (slug,),
    )
    old_intent = pg_conn.execute(
        "insert into app_checkout_intents (business_slug, app_user_id, plan_key, "
        "client_reference_id, customer_email, created_at) "
        "values (%s, %s, 'monthly', %s, %s, now() - interval '24 hours') returning id",
        (slug, app_user, uuid.uuid4().hex, "customer@example.com"),
    ).fetchone()[0]
    assert pg_conn.execute(
        "select stripe_price_id from app_plan_policies "
        "where business_slug = %s and plan_key = 'monthly'",
        (slug,),
    ).fetchone()[0] == "price_test"
    assert pg_conn.execute("select count(*) from stripe_environment_cutovers").fetchone()[0] == 0

    pg_conn.execute("set role takyon_app_runtime")
    try:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            pg_conn.execute(
                "select * from takyon_safebox_claim_app_checkout_intent("
                "%s, %s, 'monthly', '', '', null)",
                (intent, slug),
            ).fetchone()
    finally:
        pg_conn.execute("reset role")

    pg_conn.execute("set session authorization takyon_safebox_authority")
    try:
        claimed = pg_conn.execute(
            "select * from takyon_safebox_claim_app_checkout_intent("
            "%s, %s, 'monthly', '', '', null)",
            (intent, slug),
        ).fetchone()
        assert claimed[0] == intent
        assert int(claimed[4]) == 2900
        retried = pg_conn.execute(
            "select * from takyon_safebox_claim_app_checkout_intent("
            "%s, %s, 'monthly', '', '', null)",
            (intent, slug),
        ).fetchone()
        assert retried[0] == intent
        expired = pg_conn.execute(
            "select * from takyon_safebox_claim_app_checkout_intent("
            "%s, %s, 'monthly', '', '', null)",
            (old_intent, slug),
        ).fetchone()
        assert expired is None
    finally:
        pg_conn.execute("reset session authorization")

    # Replaying the complete migration set must not clear an unused plan's Stripe identifiers.
    from plugins.takyon.db.runner import run_migrations

    run_migrations(pg_conn)
    assert pg_conn.execute(
        "select stripe_product_id, stripe_price_id from app_plan_policies "
        "where business_slug = %s and plan_key = 'unused'",
        (slug,),
    ).fetchone() == ("prod_unused", "price_unused")


def test_live_checkout_claim_requires_matching_durable_cutover_target(pg_conn):
    _owner, slug, _app_user, intent = _seed_business(pg_conn)
    target_account = f"acct_{uuid.uuid4().hex}"

    pg_conn.execute("set session authorization takyon_safebox_authority")
    try:
        with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState):
            pg_conn.execute(
                "select * from takyon_safebox_claim_app_checkout_intent("
                "%s, %s, 'monthly', '', '', %s)",
                (intent, slug, target_account),
            ).fetchone()
    finally:
        pg_conn.execute("reset session authorization")

    pg_conn.execute(
        "insert into stripe_environment_cutovers "
        "(cutover_key, source_account_id, target_account_id, ssh_client, operator_host) "
        "values ('sandbox-to-live-v1', %s, %s, '203.0.113.8', 'operator-test')",
        (f"acct_{uuid.uuid4().hex}", target_account),
    )

    pg_conn.execute("set session authorization takyon_safebox_authority")
    try:
        claimed = pg_conn.execute(
            "select * from takyon_safebox_claim_app_checkout_intent("
            "%s, %s, 'monthly', '', '', %s)",
            (intent, slug, target_account),
        ).fetchone()
        assert claimed[0] == intent
    finally:
        pg_conn.execute("reset session authorization")


def test_provider_scope_binding_replay_preserves_reapproved_connection(pg_conn):
    _owner, slug, _app_user, _intent = _seed_business(pg_conn)
    connection_id = pg_conn.execute(
        "insert into provider_connections (business_slug, connection_slug, provider_kind, "
        "allowed_host, status) values (%s, 'github', 'github', 'api.github.com', 'pending') "
        "returning id",
        (slug,),
    ).fetchone()[0]
    digest = "a" * 64
    pg_conn.execute(
        "update provider_connections set approved_scope_digest = %s, status = 'active' "
        "where id = %s",
        (digest, connection_id),
    )

    from plugins.takyon.db.runner import run_migrations

    run_migrations(pg_conn)
    assert pg_conn.execute(
        "select status, approved_scope_digest from provider_connections where id = %s",
        (connection_id,),
    ).fetchone() == ("active", digest)


@pytest.mark.parametrize(
    ("entry_kind", "stripe_ref"),
    [("accrual", "external_money"), ("adjustment", None)],
)
def test_finalizer_blocks_unclassified_custody_money(pg_conn, entry_kind, stripe_ref):
    owner, slug, _app_user, _intent = _seed_business(pg_conn)
    pg_conn.execute(
        "insert into custody_accounts (user_id, owed_balance_cents) values (%s, 100)", (owner,)
    )
    pg_conn.execute(
        "insert into custody_entries (user_id, business_slug, kind, gross_cents, fee_cents, "
        "net_cents, stripe_ref, idempotency_key) "
        "values (%s, %s, %s, 100, 0, 100, %s, %s)",
        (owner, slug, entry_kind, stripe_ref, f"external:{uuid.uuid4().hex}"),
    )

    pg_conn.execute("set role takyon_migration")
    try:
        with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState):
            pg_conn.execute(
                "select takyon_finalize_stripe_live_cutover(%s, %s, %s::inet, %s)",
                ("acct_source", "acct_target", "203.0.113.8", "operator-test"),
            )
    finally:
        pg_conn.execute("reset role")

    assert pg_conn.execute(
        "select owed_balance_cents from custody_accounts where user_id = %s", (owner,)
    ).fetchone()[0] == 100
    assert pg_conn.execute("select count(*) from stripe_environment_cutovers").fetchone()[0] == 0


def test_finalizer_blocks_unclassified_active_access(pg_conn):
    _owner, slug, _app_user, _intent = _seed_business(pg_conn)
    manual_user = uuid.uuid4()
    pg_conn.execute(
        "insert into app_users (id, business_slug, email, tier) values (%s, %s, %s, 'paid')",
        (manual_user, slug, "legacy@example.com"),
    )
    pg_conn.execute(
        "insert into app_entitlements (business_slug, app_user_id, tier, status, source, "
        "plan_key) values (%s, %s, 'paid', 'active', 'manual', 'monthly')",
        (slug, manual_user),
    )

    pg_conn.execute("set role takyon_migration")
    try:
        with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState):
            pg_conn.execute(
                "select takyon_finalize_stripe_live_cutover(%s, %s, %s::inet, %s)",
                ("acct_source", "acct_target", "203.0.113.8", "operator-test"),
            )
    finally:
        pg_conn.execute("reset role")

    assert pg_conn.execute(
        "select status from app_entitlements where business_slug = %s and app_user_id = %s",
        (slug, manual_user),
    ).fetchone()[0] == "active"
    assert pg_conn.execute("select count(*) from stripe_environment_cutovers").fetchone()[0] == 0


def test_finalizer_retires_past_due_stripe_entitlement_and_archives_status(pg_conn):
    _owner, slug, app_user, _intent = _seed_business(pg_conn)
    pg_conn.execute(
        "update app_entitlements set status = 'past_due' "
        "where business_slug = %s and app_user_id = %s and source = 'stripe'",
        (slug, app_user),
    )

    pg_conn.execute("set role takyon_migration")
    try:
        receipt = pg_conn.execute(
            "select takyon_finalize_stripe_live_cutover(%s, %s, %s::inet, %s)",
            ("acct_source", "acct_target", "203.0.113.8", "operator-test"),
        ).fetchone()[0]
        assert receipt["applied"] is True
    finally:
        pg_conn.execute("reset role")

    assert pg_conn.execute(
        "select status, metadata->>'sandbox_status_before_cutover' "
        "from app_entitlements where business_slug = %s and app_user_id = %s",
        (slug, app_user),
    ).fetchone() == ("sandbox_retired", "past_due")


def test_explicit_finalizer_retires_only_sandbox_value_once(pg_conn):
    owner, slug, app_user, _intent = _seed_business(pg_conn)
    test_user, manual_user, operator_user = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    manual_allowance_user, starter_allowance_user = uuid.uuid4(), uuid.uuid4()
    for user_id, email, source, status in (
        (test_user, "historical-test@example.com", "manual_test", "active"),
        (manual_user, "legitimate-manual@example.com", "manual", "canceled"),
        (operator_user, "staff@fourmanifold.com", "operator_ssh", "active"),
    ):
        pg_conn.execute(
            "insert into app_users (id, business_slug, email, tier) values (%s, %s, %s, 'paid')",
            (user_id, slug, email),
        )
        pg_conn.execute(
            "insert into app_entitlements (business_slug, app_user_id, tier, status, source, "
            "plan_key) values (%s, %s, 'paid', %s, %s, 'monthly')",
            (slug, user_id, status, source),
        )
    pg_conn.execute(
        "insert into app_user_credit_grants (business_slug, app_user_id, amount_microusd, "
        "remaining_microusd, source, source_id) values "
        "(%s, %s, 9000, 7000, 'stripe_payment', %s), "
        "(%s, %s, 5000, 2000, 'stripe_payment', %s)",
        (
            slug,
            app_user,
            f"pi_test_{uuid.uuid4().hex}",
            slug,
            test_user,
            f"pi_test_{uuid.uuid4().hex}",
        ),
    )
    pg_conn.execute(
        "insert into custody_accounts (user_id, owed_balance_cents) values (%s, 100)", (owner,)
    )
    pg_conn.execute(
        "insert into custody_entries (user_id, business_slug, kind, gross_cents, fee_cents, "
        "net_cents, stripe_ref, idempotency_key) "
        "values (%s, %s, 'accrual', 100, 0, 100, 'cs_test_cutover', %s)",
        (owner, slug, f"app_revenue:{uuid.uuid4().hex}"),
    )
    pg_conn.execute(
        "insert into business_creative_credit_accounts (business_slug, balance_credits) "
        "values (%s, 148)", (slug,)
    )
    pg_conn.execute(
        "insert into business_creative_credit_entries (business_slug, kind, amount_credits, "
        "balance_after_credits, reservation_key, idempotency_key, stripe_ref) values "
        "(%s, 'grant', 100, 100, null, %s, 'cs_test_credit'), "
        "(%s, 'grant', 50, 150, null, %s, null), "
        "(%s, 'reserve', 2, 148, 'open-credit-reserve', %s, null)",
        (
            slug,
            f"stripe-credit:{uuid.uuid4().hex}",
            slug,
            f"starter:{uuid.uuid4().hex}",
            slug,
            "open-credit-reserve",
        ),
    )
    pg_conn.execute(
        "insert into billing_accounts (user_id, allowance_included_cents, allowance_used_cents) "
        "values (%s, 1000, 100)", (owner,)
    )
    pg_conn.execute(
        "insert into billing_entries (user_id, bucket, kind, amount_cents, balance_after_cents, "
        "idempotency_key) values (%s, 'allowance', 'grant', 1000, 0, %s)",
        (owner, f"operator-subscription:sub_test:1000:{uuid.uuid4().hex}"),
    )
    pg_conn.execute(
        "insert into billing_entries (user_id, bucket, kind, amount_cents, balance_after_cents, "
        "reservation_key, idempotency_key) values "
        "(%s, 'allowance', 'reserve', 100, 100, 'open-allowance-reserve', 'open-allowance-reserve')",
        (owner,),
    )
    pg_conn.execute(
        "insert into users (id, auth0_sub) values (%s, %s), (%s, %s)",
        (
            manual_allowance_user,
            f"auth0|manual-test-{uuid.uuid4().hex}",
            starter_allowance_user,
            f"auth0|starter-{uuid.uuid4().hex}",
        ),
    )
    pg_conn.execute(
        "insert into billing_accounts (user_id, allowance_included_cents, allowance_used_cents) "
        "values (%s, 5000, 250), (%s, 100, 0)",
        (manual_allowance_user, starter_allowance_user),
    )
    pg_conn.execute(
        "insert into billing_entries (user_id, bucket, kind, amount_cents, balance_after_cents, "
        "idempotency_key) values "
        "(%s, 'allowance', 'grant', 5000, 0, %s), "
        "(%s, 'allowance', 'grant', 100, 0, %s)",
        (
            manual_allowance_user,
            f"manual-test-reup-{uuid.uuid4().hex}",
            starter_allowance_user,
            f"starter-allowance:{uuid.uuid4().hex}",
        ),
    )
    pg_conn.execute(
        "insert into billing_entries (user_id, bucket, kind, amount_cents, balance_after_cents, "
        "reservation_key, idempotency_key) values "
        "(%s, 'allowance', 'reserve', 250, 250, %s, %s)",
        (manual_allowance_user, "manual-test-open-reserve", "manual-test-open-reserve"),
    )

    pg_conn.execute("set role takyon_migration")
    try:
        receipt = pg_conn.execute(
            "select takyon_finalize_stripe_live_cutover(%s, %s, %s::inet, %s)",
            ("acct_source", "acct_target", "203.0.113.8", "operator-test"),
        ).fetchone()[0]
        assert receipt["applied"] is True
        assert receipt["app_user_credit_grants_archived"] == 2
        assert receipt["app_user_credit_grants_zeroed"] == 2
        assert receipt["creative_reservations_released"] == 1
        assert receipt["operator_allowances_cleared"] == 2
        assert receipt["operator_allowance_reservations_refunded"] == 2
        replay = pg_conn.execute(
            "select takyon_finalize_stripe_live_cutover(%s, %s, %s::inet, %s)",
            ("acct_source", "acct_target", "203.0.113.8", "operator-test"),
        ).fetchone()[0]
        assert replay == {"applied": False, "reason": "already_applied"}
    finally:
        pg_conn.execute("reset role")

    assert pg_conn.execute(
        "select stripe_price_id from app_plan_policies where business_slug = %s", (slug,)
    ).fetchone()[0] is None
    assert pg_conn.execute(
        "select status from app_entitlements where business_slug = %s and app_user_id = %s",
        (slug, app_user),
    ).fetchone()[0] == "sandbox_retired"
    assert pg_conn.execute(
        "select tier from app_users where business_slug = %s and id = %s", (slug, app_user)
    ).fetchone()[0] == "unentitled"
    assert pg_conn.execute(
        "select source, status, metadata->>'stripe_environment' from app_entitlements "
        "where business_slug = %s and app_user_id in (%s, %s, %s) order by source",
        (slug, test_user, manual_user, operator_user),
    ).fetchall() == [
        ("manual", "canceled", None),
        ("manual_test", "sandbox_retired", "test"),
        ("operator_ssh", "active", None),
    ]
    assert pg_conn.execute(
        "select email, tier from app_users where business_slug = %s "
        "and id in (%s, %s, %s) order by email",
        (slug, test_user, manual_user, operator_user),
    ).fetchall() == [
        ("historical-test@example.com", "unentitled"),
        ("legitimate-manual@example.com", "unentitled"),
        ("staff@fourmanifold.com", "paid"),
    ]
    assert pg_conn.execute(
        "select balance_credits from business_creative_credit_accounts where business_slug = %s",
        (slug,),
    ).fetchone()[0] == 50
    assert pg_conn.execute(
        "select source_id, remaining_microusd from app_user_credit_grants "
        "where source = 'stripe_payment' order by source_id"
    ).fetchall() == pg_conn.execute(
        "select source_id, 0::bigint from stripe_sandbox_app_user_credit_grants_archive "
        "where cutover_key = 'sandbox-to-live-v1' order by source_id"
    ).fetchall()
    assert pg_conn.execute(
        "select amount_microusd, remaining_before_microusd "
        "from stripe_sandbox_app_user_credit_grants_archive "
        "where cutover_key = 'sandbox-to-live-v1' order by amount_microusd"
    ).fetchall() == [(5000, 2000), (9000, 7000)]
    assert pg_conn.execute(
        "select allowance_included_cents, allowance_used_cents from billing_accounts where user_id = %s",
        (owner,),
    ).fetchone() == (0, 0)
    assert pg_conn.execute(
        "select user_id, allowance_included_cents, allowance_used_cents "
        "from billing_accounts where user_id in (%s, %s) order by user_id",
        (manual_allowance_user, starter_allowance_user),
    ).fetchall() == sorted(
        [
            (manual_allowance_user, 0, 0),
            (starter_allowance_user, 100, 0),
        ],
        key=lambda row: row[0],
    )
    assert pg_conn.execute(
        "select owed_balance_cents from custody_accounts where user_id = %s", (owner,)
    ).fetchone()[0] == 0

    # A post-cutover live row is untouched by both the one-shot function and migration replay.
    live_id = pg_conn.execute(
        "insert into app_revenue_events (business_slug, provider_event_id, stripe_object_type, "
        "stripe_object_id, status, currency, amount_paid_cents, metadata) "
        "values (%s, %s, 'checkout.session', %s, 'paid', 'usd', 500, "
        "'{\"stripe_environment\":\"live\"}'::jsonb) returning id",
        (slug, f"evt_live_{uuid.uuid4().hex}", f"cs_live_{uuid.uuid4().hex}"),
    ).fetchone()[0]
    from plugins.takyon.db.runner import run_migrations

    run_migrations(pg_conn)
    pg_conn.execute("set role takyon_migration")
    try:
        pg_conn.execute(
            "select takyon_finalize_stripe_live_cutover(%s, %s, %s::inet, %s)",
            ("acct_source", "acct_target", "203.0.113.8", "operator-test"),
        )
    finally:
        pg_conn.execute("reset role")
    assert pg_conn.execute(
        "select status, metadata->>'stripe_environment' from app_revenue_events where id = %s",
        (live_id,),
    ).fetchone() == ("paid", "live")
