"""Postgres proof for the root-SSH-only product-profile access rail.

Skips unless TAKYON_TEST_PG_DSN is configured; the shared fixture applies the tracked migrations.
"""

from __future__ import annotations

import hashlib
import uuid
import pytest

psycopg = pytest.importorskip("psycopg")

from plugins.takyon import app_entitlements, app_identity, app_usage, operator_access  # noqa: E402


def _verified_login(pg_conn, business: str, email: str) -> app_identity.AppUser:
    supabase_user_id = uuid.uuid4()
    session_hash = hashlib.sha256(uuid.uuid4().bytes).hexdigest()
    pg_conn.execute("set role takyon_app_runtime")
    try:
        row = pg_conn.execute(
            "select app_user_id, business_slug, email::text, name, status, tier "
            "from takyon_app_bind_supabase_session(%s, %s, %s, %s, %s, %s)",
            (business, str(supabase_user_id), email, None, session_hash, 30),
        ).fetchone()
    finally:
        pg_conn.execute("reset role")
    return app_identity.AppUser(
        id=str(row[0]),
        business_slug=str(row[1]),
        email=str(row[2]),
        name=row[3],
        status=str(row[4]),
        tier=str(row[5]),
    )


def _setup(pg_conn, *, email: str = "sai@fourmanifold.com", verified: bool = True):
    owner_id = pg_conn.execute(
        "insert into users (auth0_sub, email) values (%s, %s) returning id",
        (f"auth0|{uuid.uuid4().hex}", f"owner-{uuid.uuid4().hex[:8]}@example.com"),
    ).fetchone()[0]
    business = f"staff-test-{uuid.uuid4().hex[:8]}"
    pg_conn.execute(
        "insert into businesses (slug, name, owner_user_id) values (%s, %s, %s)",
        (business, "Staff Test", owner_id),
    )
    plan = app_entitlements.upsert_plan_policy(
        pg_conn,
        business,
        "monthly",
        tier="paid",
        price_cents=1000,
        included_ai_budget_microusd=1_000_000,
        metadata={"features": {"ai_generate": True}},
    )
    if verified:
        user = _verified_login(pg_conn, business, email)
    else:
        user = app_identity.upsert_app_user(pg_conn, business, email)
    return business, plan, user


def _access_call(pg_conn, callback):
    pg_conn.execute("set session authorization takyon_migration")
    try:
        return callback()
    finally:
        pg_conn.execute("reset session authorization")


def _context() -> operator_access.SSHOperatorContext:
    return operator_access.SSHOperatorContext("203.0.113.9", "argon-alpha-14")


def test_verified_staff_grant_is_audited_and_revoke_preserves_stripe_guard(pg_conn):
    business, plan, user = _setup(pg_conn)

    receipt = _access_call(
        pg_conn,
        lambda: operator_access.grant_profile_access(
            pg_conn,
            _context(),
            business_slug=business,
            email="SAI@FOURMANIFOLD.COM",
            plan_key=plan.plan_key,
            request_id=uuid.uuid4(),
        ),
    )
    assert receipt["changed"] is True
    entitlement = pg_conn.execute(
        "select source, status, plan_key, stripe_customer_id, stripe_subscription_id, "
        "stripe_checkout_session_id, metadata from app_entitlements where id = %s",
        (receipt["entitlement_id"],),
    ).fetchone()
    assert entitlement[:6] == ("operator_ssh", "active", "monthly", None, None, None)
    assert entitlement[6]["operator_access_grant_id"] == receipt["grant_id"]

    expected_supabase_user_id = pg_conn.execute(
        "select supabase_user_id from app_users where id = %s", (user.id,)
    ).fetchone()[0]
    audit = pg_conn.execute(
        "select verified_email::text, profile_supabase_user_id, grant_source, granted_from::text, "
        "granted_on_host, status from app_operator_access_grants where id = %s",
        (receipt["grant_id"],),
    ).fetchone()
    assert audit == (
        "sai@fourmanifold.com",
        expected_supabase_user_id,
        "root_ssh",
        "203.0.113.9/32",
        "argon-alpha-14",
        "active",
    )
    assert (
        pg_conn.execute(
            "select tier from app_users where id = %s", (user.id,)
        ).fetchone()[0]
        == "paid"
    )

    # The ordinary entitlement leaf remains payment-evidence-only; this exception did not open
    # source='manual' or any web-callable path.
    with pytest.raises(app_entitlements.FakeBillingRejected):
        app_entitlements.grant_entitlement(
            pg_conn,
            business,
            app_user_id=user.id,
            tier="paid",
            plan_key="monthly",
        )

    revoked = _access_call(
        pg_conn,
        lambda: operator_access.revoke_profile_access(
            pg_conn,
            _context(),
            business_slug=business,
            email="sai@fourmanifold.com",
            request_id=uuid.uuid4(),
        ),
    )
    assert revoked["changed"] is True
    assert (
        pg_conn.execute(
            "select status from app_entitlements where id = %s",
            (receipt["entitlement_id"],),
        ).fetchone()[0]
        == "cancelled"
    )
    assert (
        pg_conn.execute(
            "select tier from app_users where id = %s", (user.id,)
        ).fetchone()[0]
        == "unentitled"
    )


def test_database_rejects_external_or_unverified_profile(pg_conn):
    external_business, external_plan, _ = _setup(pg_conn, email="sai@example.com")

    def external_call():
        return pg_conn.execute(
            "select * from operator_ssh_grant_app_access(%s, %s, %s, %s, %s::inet, %s)",
            (
                external_business,
                "sai@example.com",
                external_plan.plan_key,
                uuid.uuid4(),
                "203.0.113.9",
                "argon-alpha-14",
            ),
        ).fetchone()

    with pytest.raises(
        psycopg.errors.InvalidParameterValue, match="fourmanifold_email_required"
    ):
        _access_call(pg_conn, external_call)

    business, plan, _ = _setup(pg_conn, verified=False)

    def unverified_call():
        return pg_conn.execute(
            "select * from operator_ssh_grant_app_access(%s, %s, %s, %s, %s::inet, %s)",
            (
                business,
                "sai@fourmanifold.com",
                plan.plan_key,
                uuid.uuid4(),
                "203.0.113.9",
                "argon-alpha-14",
            ),
        ).fetchone()

    with pytest.raises(
        psycopg.errors.RaiseException, match="verified_active_profile_required"
    ):
        _access_call(pg_conn, unverified_call)
    assert (
        pg_conn.execute("select count(*) from app_operator_access_grants").fetchone()[0]
        == 0
    )


def test_web_runtime_roles_cannot_execute_or_read_ssh_grants(pg_conn):
    business, plan, _ = _setup(pg_conn)

    pg_conn.execute("set role takyon_operator_runtime")
    try:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            pg_conn.execute(
                "select * from operator_ssh_grant_app_access(%s, %s, %s, %s, %s::inet, %s)",
                (
                    business,
                    "sai@fourmanifold.com",
                    plan.plan_key,
                    uuid.uuid4(),
                    "203.0.113.9",
                    "argon-alpha-14",
                ),
            ).fetchone()
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            pg_conn.execute("select * from app_operator_access_grants").fetchall()
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            pg_conn.execute(
                "select * from app_supabase_verified_email_bindings"
            ).fetchall()
    finally:
        pg_conn.execute("reset role")

    pg_conn.execute("set role takyon_app_runtime")
    try:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            pg_conn.execute("select * from app_operator_access_grants").fetchall()
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            pg_conn.execute(
                "select * from app_supabase_verified_email_bindings"
            ).fetchall()
    finally:
        pg_conn.execute("reset role")


def test_set_role_cannot_impersonate_root_only_migration_login(pg_conn):
    # SET ROLE changes current_user, not session_user. A runtime that somehow gained membership
    # still cannot impersonate the root-only connection used by the SSH launcher.
    pg_conn.execute("set role takyon_migration")
    try:
        with pytest.raises(
            psycopg.errors.InsufficientPrivilege,
            match="operator_migration_role_required",
        ):
            pg_conn.execute(
                "select * from operator_ssh_list_app_access(null, null)"
            ).fetchall()
    finally:
        pg_conn.execute("reset role")


def test_future_migration_functions_are_not_public(pg_conn):
    pg_conn.execute("set role takyon_migration")
    try:
        pg_conn.execute(
            "create function operator_access_future_probe() returns integer "
            "language sql as 'select 1'"
        )
    finally:
        pg_conn.execute("reset role")
    pg_conn.execute("set role takyon_operator_runtime")
    try:
        assert (
            pg_conn.execute(
                "select has_function_privilege(current_user, "
                "'operator_access_future_probe()', 'execute')"
            ).fetchone()[0]
            is False
        )
    finally:
        pg_conn.execute("reset role")
    pg_conn.execute("drop function operator_access_future_probe()")


def test_grant_requires_fresh_binding_and_identity_change_auto_revokes(pg_conn):
    business, plan, user = _setup(pg_conn)
    pg_conn.execute(
        "update app_supabase_verified_email_bindings "
        "set verified_at = now() - interval '16 minutes' where business_slug = %s and app_user_id = %s",
        (business, user.id),
    )
    with pytest.raises(
        psycopg.errors.RaiseException, match="fresh_verified_supabase_login_required"
    ):
        _access_call(
            pg_conn,
            lambda: operator_access.grant_profile_access(
                pg_conn,
                _context(),
                business_slug=business,
                email="sai@fourmanifold.com",
                plan_key=plan.plan_key,
            ),
        )

    supabase_user_id = pg_conn.execute(
        "select supabase_user_id from app_users where business_slug = %s and id = %s",
        (business, user.id),
    ).fetchone()[0]
    pg_conn.execute("set role takyon_app_runtime")
    try:
        pg_conn.execute(
            "select * from takyon_app_bind_supabase_session(%s, %s, %s, %s, %s, %s)",
            (
                business,
                str(supabase_user_id),
                "sai@fourmanifold.com",
                None,
                hashlib.sha256(uuid.uuid4().bytes).hexdigest(),
                30,
            ),
        ).fetchone()
    finally:
        pg_conn.execute("reset role")

    granted = _access_call(
        pg_conn,
        lambda: operator_access.grant_profile_access(
            pg_conn,
            _context(),
            business_slug=business,
            email="sai@fourmanifold.com",
            plan_key=plan.plan_key,
        ),
    )

    # A later verified login for the same Supabase subject with a changed email revokes only the
    # SSH grant; no root command or cleanup job is required.
    pg_conn.execute("set role takyon_app_runtime")
    try:
        pg_conn.execute(
            "select * from takyon_app_bind_supabase_session(%s, %s, %s, %s, %s, %s)",
            (
                business,
                str(supabase_user_id),
                "sai@example.com",
                None,
                hashlib.sha256(uuid.uuid4().bytes).hexdigest(),
                30,
            ),
        ).fetchone()
    finally:
        pg_conn.execute("reset role")
    assert pg_conn.execute(
        "select status, revoked_reason from app_operator_access_grants where id = %s",
        (granted["grant_id"],),
    ).fetchone() == ("revoked", "verified_identity_changed")
    assert (
        pg_conn.execute(
            "select status from app_entitlements where id = %s",
            (granted["entitlement_id"],),
        ).fetchone()[0]
        == "cancelled"
    )


def test_business_deactivation_auto_revokes_and_blocks_new_grant(pg_conn):
    business, plan, _ = _setup(pg_conn)
    granted = _access_call(
        pg_conn,
        lambda: operator_access.grant_profile_access(
            pg_conn,
            _context(),
            business_slug=business,
            email="sai@fourmanifold.com",
            plan_key=plan.plan_key,
        ),
    )
    pg_conn.execute(
        "update businesses set status = 'paused' where slug = %s", (business,)
    )
    assert pg_conn.execute(
        "select status, revoked_reason from app_operator_access_grants where id = %s",
        (granted["grant_id"],),
    ).fetchone() == ("revoked", "business_inactive")
    with pytest.raises(psycopg.errors.RaiseException, match="active_business_required"):
        _access_call(
            pg_conn,
            lambda: operator_access.grant_profile_access(
                pg_conn,
                _context(),
                business_slug=business,
                email="sai@fourmanifold.com",
                plan_key=plan.plan_key,
            ),
        )


def test_staff_usage_keeps_the_existing_plan_monthly_money_gate(pg_conn):
    business, plan, user = _setup(pg_conn)
    receipt = _access_call(
        pg_conn,
        lambda: operator_access.grant_profile_access(
            pg_conn,
            _context(),
            business_slug=business,
            email="sai@fourmanifold.com",
            plan_key=plan.plan_key,
        ),
    )
    fallback, calendar_month = pg_conn.execute(
        "select now() - interval '1 day', date_trunc('month', now())"
    ).fetchone()
    assert (
        app_usage._app_user_period_start(pg_conn, business, user.id, fallback)
        == calendar_month
    )
    pg_conn.execute(
        "update app_entitlements set current_period_end = now() - interval '1 day' where id = %s",
        (receipt["entitlement_id"],),
    )
    pg_conn.execute(
        "insert into app_budgets (business_slug) values (%s) on conflict (business_slug) do nothing",
        (business,),
    )
    pg_conn.execute(
        "update app_budgets set current_period_start = now(), current_period_end = now() + interval '1 week' "
        "where business_slug = %s",
        (business,),
    )
    pg_conn.execute(
        "insert into app_usage_events (business_slug, app_user_id, app_user_tier, reservation_key, "
        "purpose, route, status, estimated_cost_microusd, actual_cost_microusd, metadata, "
        "created_at, completed_at) values (%s, %s, %s, %s, %s, %s, 'completed', 800, 800, "
        "'{}'::jsonb, date_trunc('month', now()), now())",
        (business, user.id, plan.tier, f"seed:{uuid.uuid4()}", "product_usage", "test"),
    )
    listed = _access_call(
        pg_conn,
        lambda: operator_access.list_profile_access(
            pg_conn, business_slug=business, email="sai@fourmanifold.com"
        ),
    )
    assert listed["grants"][0]["usage_period_start"] == calendar_month
    assert listed["grants"][0]["used_microusd"] == 800
    assert (
        listed["grants"][0]["monthly_limit_microusd"]
        == plan.included_ai_budget_microusd
    )

    pg_conn.execute("set role takyon_safebox_authority")
    try:
        row = pg_conn.execute(
            "select * from safebox_reserve_usage(%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)",
            (
                business,
                300,
                f"staff-gate:{uuid.uuid4()}",
                user.id,
                1_000,
                plan.tier,
                "product_usage",
                "test",
                "anthropic",
                "claude-sonnet-4-5",
                "{}",
            ),
        ).fetchone()
        assert row[0] == "app_user_budget_exceeded"
    finally:
        pg_conn.execute("reset role")

    _, raw_session = app_identity.start_session(pg_conn, business, user.id)
    session_hash = hashlib.sha256(raw_session.encode()).hexdigest()
    pg_conn.execute("set role takyon_app_runtime")
    try:
        row = pg_conn.execute(
            "select * from takyon_app_reserve_usage(%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)",
            (
                business,
                session_hash,
                user.id,
                300,
                f"staff-session-gate:{uuid.uuid4()}",
                1_000,
                plan.tier,
                "product_usage",
                "test",
                "anthropic",
                "claude-sonnet-4-5",
                "{}",
            ),
        ).fetchone()
        assert row[0] == "app_user_budget_exceeded"
    finally:
        pg_conn.execute("reset role")
