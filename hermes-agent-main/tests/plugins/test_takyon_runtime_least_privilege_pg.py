"""Postgres integration tests for the operator/Safebox money-ledger split.

Migration 0044 replaces the old runtime-role demotion model with explicit login roles:

  * `takyon_operator_runtime` can read/write normal operator runtime state, but direct money-ledger
    INSERT/UPDATE/DELETE and businesses.owner_user_id rewrites are DENIED;
  * `takyon_safebox_authority` is the live money authority. Python billing/credit/custody ops that
    reserve/settle/release money must run on that Safebox DB role and reach the ledgers only through
    SECURITY DEFINER functions;
  * SELECT on money tables is retained where reconciliation and balance derivation need it;
  * the operator runtime may still INSERT a business and UPDATE its non-owner columns.

The older `SET ROLE takyon_runtime` bridge is intentionally not exercised here. Runtime planes should
not temporarily become a money-writing role.

Real engine on real Postgres (never mocks). Skips unless psycopg is importable and
TAKYON_TEST_PG_DSN is set.
"""

from __future__ import annotations

import uuid

import pytest

psycopg = pytest.importorskip("psycopg")

from plugins.takyon import billing, business_credits, custody  # noqa: E402
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


# ── the boundary: direct money-table DML is denied under the operator runtime role ──────────


def test_operator_role_cannot_write_billing_accounts_directly(pg_conn):
    uid = _owner(pg_conn)
    billing.open_billing_account(pg_conn, uid)
    before = billing.get_billing_balances(pg_conn, uid).allowance_included_cents
    pg_conn.execute("set role takyon_operator_runtime")
    try:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            pg_conn.execute(
                "update billing_accounts set allowance_included_cents = 999999 where user_id = %s",
                (uid,),
            )
    finally:
        pg_conn.execute("reset role")
    # The forged grant never landed — the allowance is unchanged.
    assert billing.get_billing_balances(pg_conn, uid).allowance_included_cents == before


def test_operator_role_cannot_write_billing_entries_directly(pg_conn):
    uid = _owner(pg_conn)
    billing.open_billing_account(pg_conn, uid)
    pg_conn.execute("set role takyon_operator_runtime")
    try:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            pg_conn.execute(
                "insert into billing_entries (user_id, bucket, kind, amount_cents, "
                "balance_after_cents, idempotency_key) "
                "values (%s, 'allowance', 'grant', 999999, 0, 'forge')",
                (uid,),
            )
    finally:
        pg_conn.execute("reset role")


def test_operator_role_cannot_write_custody_accounts_directly(pg_conn):
    uid = _owner(pg_conn)
    custody.open_custody_account(pg_conn, uid)
    pg_conn.execute("set role takyon_operator_runtime")
    try:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            pg_conn.execute(
                "update custody_accounts set owed_balance_cents = 0 where user_id = %s",
                (uid,),
            )
    finally:
        pg_conn.execute("reset role")


def test_operator_role_cannot_write_creative_credit_accounts_directly(pg_conn):
    slug = _business(pg_conn, _owner(pg_conn))
    business_credits.open_business_credit_account(pg_conn, slug)
    pg_conn.execute("set role takyon_operator_runtime")
    try:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            pg_conn.execute(
                "update business_creative_credit_accounts set balance_credits = 100000 "
                "where business_slug = %s",
                (slug,),
            )
    finally:
        pg_conn.execute("reset role")
    assert business_credits.get_business_credit_balances(pg_conn, slug).balance_credits == 0


def test_operator_role_cannot_write_creative_credit_entries_directly(pg_conn):
    slug = _business(pg_conn, _owner(pg_conn))
    business_credits.open_business_credit_account(pg_conn, slug)
    pg_conn.execute("set role takyon_operator_runtime")
    try:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            pg_conn.execute(
                "insert into business_creative_credit_entries "
                "(business_slug, kind, amount_credits, balance_after_credits, idempotency_key) "
                "values (%s, 'grant', 100000, 100000, 'forge')",
                (slug,),
            )
    finally:
        pg_conn.execute("reset role")


def test_operator_role_cannot_repoint_business_owner(pg_conn):
    owner_a = _owner(pg_conn)
    owner_b = _owner(pg_conn)
    slug = _business(pg_conn, owner_a)
    pg_conn.execute("set role takyon_operator_runtime")
    try:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            pg_conn.execute(
                "update businesses set owner_user_id = %s where slug = %s",
                (owner_b, slug),
            )
    finally:
        pg_conn.execute("reset role")
    # Ownership unchanged.
    row = pg_conn.execute(
        "select owner_user_id from businesses where slug = %s", (slug,)
    ).fetchone()
    assert str(row[0]) == owner_a


def test_operator_role_can_still_insert_business_and_update_non_owner_columns(pg_conn):
    # Only owner_user_id is column-revoked; the operator runtime can still create a business and change its
    # other columns (e.g. mode). This proves the column-level revoke did not over-broadly block DML.
    owner_a = _owner(pg_conn)
    slug = f"biz-{uuid.uuid4().hex[:8]}"
    pg_conn.execute("set role takyon_operator_runtime")
    try:
        pg_conn.execute(
            "insert into businesses (slug, name, owner_user_id) values (%s, %s, %s)",
            (slug, "Acme", owner_a),
        )
        pg_conn.execute("update businesses set mode = 'live' where slug = %s", (slug,))
    finally:
        pg_conn.execute("reset role")
    row = pg_conn.execute("select mode from businesses where slug = %s", (slug,)).fetchone()
    assert row[0] == "live"


# ── the gate writes only under the Safebox authority role ──────────────────────────────────


def test_billing_ops_write_under_safebox_authority_role(pg_conn):
    # The SECURITY DEFINER funcs run as their privileged owner, so the Python billing ops SUCCEED
    # under the explicit Safebox authority role.
    uid = _owner(pg_conn)
    billing.open_billing_account(pg_conn, uid)
    billing.grant_allowance(pg_conn, uid, 1000, "grant-1")
    pg_conn.execute("set role takyon_safebox_authority")
    try:
        res = billing.reserve(pg_conn, uid, 400, "rk-1")
        assert res.allowance_cents == 400
        billing.settle(pg_conn, "rk-1", 250)
    finally:
        pg_conn.execute("reset role")
    bal = billing.get_billing_balances(pg_conn, uid)
    assert bal.allowance_used_cents == 250  # 400 held, settled 250, 150 released
    assert billing.reconcile_billing(pg_conn, uid)["ok"] is True


def test_operator_role_cannot_execute_billing_mint_functions(pg_conn):
    uid = _owner(pg_conn)
    billing.open_billing_account(pg_conn, uid)
    pg_conn.execute("set role takyon_operator_runtime")
    try:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            pg_conn.execute("select safebox_billing_open_account(%s, %s)", (uid, 0))
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            pg_conn.execute(
                "select * from safebox_billing_grant_allowance(%s, %s, %s, null, null)",
                (uid, 999999, "forge-grant"),
            )
    finally:
        pg_conn.execute("reset role")


def test_credit_ops_write_under_safebox_authority_role(pg_conn):
    slug = _business(pg_conn, _owner(pg_conn))
    business_credits.grant_credits(pg_conn, slug, 50, "grant-1")
    pg_conn.execute("set role takyon_safebox_authority")
    try:
        resv = business_credits.reserve_credits(pg_conn, slug, 20, "rk-1")
        assert resv.reserved_credits == 20
        business_credits.commit_credits(pg_conn, "rk-1", actual_credits=12)
    finally:
        pg_conn.execute("reset role")
    bal = business_credits.get_business_credit_balances(pg_conn, slug)
    assert bal.balance_credits == 38  # 50 - 20 reserve = 30, commit actual 12 refunds 8 -> 38
    assert bal.reserved_credits == 0


def test_operator_role_cannot_execute_credit_mint_functions(pg_conn):
    slug = _business(pg_conn, _owner(pg_conn))
    business_credits.open_business_credit_account(pg_conn, slug)
    pg_conn.execute("set role takyon_operator_runtime")
    try:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            pg_conn.execute("select safebox_credits_open_account(%s)", (slug,))
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            pg_conn.execute(
                "select * from safebox_credits_grant(%s, %s, %s, '{}'::jsonb, null)",
                (slug, 100000, "forge-grant"),
            )
    finally:
        pg_conn.execute("reset role")


def test_operator_role_cannot_execute_custody_mint_functions(pg_conn):
    uid = _owner(pg_conn)
    slug = _business(pg_conn, uid)  # custody_entries.business_slug FKs to businesses
    custody.open_custody_account(pg_conn, uid)
    pg_conn.execute("set role takyon_operator_runtime")
    try:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            pg_conn.execute("select safebox_custody_open_account(%s, %s)", (uid, "usd"))
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            pg_conn.execute(
                "select * from safebox_custody_accrue(%s, %s, %s, %s, %s, %s, %s, '{}'::jsonb)",
                (uid, slug, 1000, 200, 800, "evt-forge", "cs_forge"),
            )
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            pg_conn.execute(
                "select * from safebox_custody_payout(%s, %s, %s, %s)",
                (uid, 300, "po-forge", "tr_forge"),
            )
    finally:
        pg_conn.execute("reset role")


def test_operator_role_retains_select_on_money_tables(pg_conn):
    # SELECT is retained (reconciliation + balance derivation need it under any scope).
    uid = _owner(pg_conn)
    billing.open_billing_account(pg_conn, uid)
    pg_conn.execute("set role takyon_operator_runtime")
    try:
        pg_conn.execute("select count(*) from billing_accounts where user_id = %s", (uid,))
        pg_conn.execute("select count(*) from custody_accounts")
        pg_conn.execute("select count(*) from business_creative_credit_accounts")
    finally:
        pg_conn.execute("reset role")


def test_migration_role_can_create_tracked_schema_objects(pg_conn):
    row = pg_conn.execute(
        "select has_schema_privilege(%s, %s, %s)",
        ("takyon_migration", "public", "CREATE"),
    ).fetchone()
    assert row[0] is True


def test_operator_delete_helper_detaches_business_slug_without_money_write_grants(pg_conn):
    uid = _owner(pg_conn)
    slug = _business(pg_conn, uid)
    billing.open_billing_account(pg_conn, uid)
    custody.open_custody_account(pg_conn, uid)
    pg_conn.execute(
        "insert into billing_entries (user_id, business_slug, bucket, kind, amount_cents, "
        "balance_after_cents, idempotency_key) values (%s, %s, 'allowance', 'reserve', 100, 100, %s)",
        (uid, slug, f"{slug}-billing"),
    )
    pg_conn.execute(
        "insert into custody_entries (user_id, business_slug, kind, gross_cents, fee_cents, "
        "net_cents, idempotency_key) values (%s, %s, 'accrual', 500, 50, 450, %s)",
        (uid, slug, f"{slug}-custody"),
    )

    pg_conn.execute("set role takyon_operator_runtime")
    try:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            pg_conn.execute(
                "update billing_entries set amount_cents = 0 where business_slug = %s",
                (slug,),
            )
    finally:
        pg_conn.execute("reset role")

    pg_conn.execute("set role takyon_operator_runtime")
    try:
        preview = pg_conn.execute(
            "select ledger_table, affected from takyon_business_delete_money_ledger_touch(%s, false)",
            (slug,),
        ).fetchall()
        applied = pg_conn.execute(
            "select ledger_table, affected from takyon_business_delete_money_ledger_touch(%s, true)",
            (slug,),
        ).fetchall()
    finally:
        pg_conn.execute("reset role")

    assert dict(preview) == {"billing_entries": 1, "custody_entries": 1}
    assert dict(applied) == {"billing_entries": 1, "custody_entries": 1}
    assert pg_conn.execute(
        "select amount_cents, business_slug from billing_entries where idempotency_key = %s",
        (f"{slug}-billing",),
    ).fetchone() == (100, None)
    assert pg_conn.execute(
        "select gross_cents, fee_cents, net_cents, business_slug "
        "from custody_entries where idempotency_key = %s",
        (f"{slug}-custody",),
    ).fetchone() == (500, 50, 450, None)
