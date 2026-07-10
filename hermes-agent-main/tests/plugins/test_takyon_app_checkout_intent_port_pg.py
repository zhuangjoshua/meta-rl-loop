from __future__ import annotations

import hashlib
import json
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest

psycopg = pytest.importorskip("psycopg")


_FUNCTION_SQL = (
    "select * from takyon_app_create_checkout_intent(%s, %s, %s, %s, %s::jsonb)"
)


def _session_hash() -> str:
    return hashlib.sha256(uuid.uuid4().bytes).hexdigest()


def _seed(conn):
    owner = conn.execute(
        "insert into users (auth0_sub, email) values (%s, %s) returning id",
        (f"auth0|{uuid.uuid4().hex}", f"owner-{uuid.uuid4().hex[:8]}@example.com"),
    ).fetchone()[0]
    business = f"checkout-port-{uuid.uuid4().hex[:8]}"
    other_business = f"checkout-port-other-{uuid.uuid4().hex[:8]}"
    conn.execute(
        "insert into businesses (slug, name, owner_user_id, mode) "
        "values (%s, 'Checkout Port', %s, 'live'), (%s, 'Other', %s, 'live')",
        (business, owner, other_business, owner),
    )
    alice, bob, outsider = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    conn.execute(
        "insert into app_users (id, business_slug, email) values "
        "(%s, %s, 'alice@example.com'), (%s, %s, 'bob@example.com'), "
        "(%s, %s, 'outsider@example.com')",
        (alice, business, bob, business, outsider, other_business),
    )
    alice_hash, bob_hash, outsider_hash = _session_hash(), _session_hash(), _session_hash()
    conn.execute(
        "insert into app_sessions (business_slug, app_user_id, token_hash, expires_at) values "
        "(%s, %s, %s, now() + interval '1 day'), "
        "(%s, %s, %s, now() + interval '1 day'), "
        "(%s, %s, %s, now() + interval '1 day')",
        (
            business,
            alice,
            alice_hash,
            business,
            bob,
            bob_hash,
            other_business,
            outsider,
            outsider_hash,
        ),
    )
    conn.execute(
        "insert into app_plan_policies "
        "(business_slug, plan_key, tier, price_cents, currency, billing_interval, saleable) "
        "values (%s, 'pro', 'paid', 1900, 'usd', 'month', true), "
        "(%s, 'basic', 'paid', 900, 'usd', 'month', true)",
        (business, business),
    )
    return {
        "business": business,
        "other_business": other_business,
        "alice": alice,
        "bob": bob,
        "outsider": outsider,
        "alice_hash": alice_hash,
        "bob_hash": bob_hash,
        "outsider_hash": outsider_hash,
    }


def _call(
    conn,
    seeded,
    *,
    session_hash: str | None = None,
    plan_key: str = "pro",
    reference: str | None = None,
    business: str | None = None,
    metadata: dict | None = None,
):
    conn.execute("set role takyon_app_runtime")
    try:
        return conn.execute(
            _FUNCTION_SQL,
            (
                business or seeded["business"],
                session_hash or seeded["alice_hash"],
                plan_key,
                reference or f"ref-{uuid.uuid4().hex}",
                json.dumps(metadata or {}),
            ),
        ).fetchone()
    finally:
        conn.execute("reset role")


def _primary_message(exc: BaseException) -> str:
    return str(getattr(getattr(exc, "diag", None), "message_primary", "") or str(exc))


def test_port_derives_identity_and_reuses_open_intent_through_24_hours(pg_conn):
    seeded = _seed(pg_conn)
    first = _call(
        pg_conn,
        seeded,
        reference="first-reference",
        metadata={"surface": "pricing"},
    )
    assert first[1:6] == (
        seeded["business"],
        seeded["alice"],
        "pro",
        "created",
        "first-reference",
    )
    assert first[8] == "alice@example.com"
    assert first[9] == {"surface": "pricing"}

    pg_conn.execute(
        "update app_checkout_intents set created_at = now() - interval '24 hours' where id = %s",
        (first[0],),
    )
    reused = _call(pg_conn, seeded, reference="ignored-on-reuse")
    assert reused[0] == first[0]
    assert reused[5] == "first-reference"

    pg_conn.execute(
        "update app_checkout_intents "
        "set created_at = now() - interval '25 hours 1 minute' where id = %s",
        (first[0],),
    )
    fresh = _call(pg_conn, seeded, reference="after-window")
    assert fresh[0] != first[0]
    assert fresh[5] == "after-window"
    assert pg_conn.execute(
        "select count(*) from app_checkout_intents where business_slug = %s and app_user_id = %s",
        (seeded["business"], seeded["alice"]),
    ).fetchone()[0] == 2


def test_invalid_cross_business_and_forged_cross_user_scope_cannot_choose_identity(pg_conn):
    seeded = _seed(pg_conn)
    with pytest.raises(psycopg.errors.InvalidAuthorizationSpecification) as invalid:
        _call(pg_conn, seeded, session_hash="f" * 64)
    assert _primary_message(invalid.value) == "app_checkout_invalid_session"

    with pytest.raises(psycopg.errors.InvalidAuthorizationSpecification) as cross_business:
        _call(
            pg_conn,
            seeded,
            session_hash=seeded["outsider_hash"],
            business=seeded["business"],
        )
    assert _primary_message(cross_business.value) == "app_checkout_invalid_session"

    # A hostile caller can set USERSET RLS hints, but the definer port ignores them and derives Bob
    # from Bob's actual session. There is intentionally no caller-supplied app_user_id parameter.
    pg_conn.execute("set role takyon_app_runtime")
    try:
        pg_conn.execute(
            "select set_config('takyon.rls_app_user_id', %s, false)",
            (str(seeded["alice"]),),
        )
        row = pg_conn.execute(
            _FUNCTION_SQL,
            (
                seeded["business"],
                seeded["bob_hash"],
                "pro",
                "bob-session-reference",
                "{}",
            ),
        ).fetchone()
    finally:
        pg_conn.execute("reset role")
        pg_conn.execute("select set_config('takyon.rls_app_user_id', '', false)")
    assert row[2] == seeded["bob"]
    assert row[8] == "bob@example.com"


def test_nonterminal_subscription_and_other_plan_open_have_stable_refusals(pg_conn):
    seeded = _seed(pg_conn)
    entitlement_id = pg_conn.execute(
        "insert into app_entitlements "
        "(business_slug, app_user_id, tier, status, source, stripe_subscription_id, plan_key) "
        "values (%s, %s, 'paid', 'active', 'stripe', 'sub_live_existing', 'pro') returning id",
        (seeded["business"], seeded["alice"]),
    ).fetchone()[0]
    with pytest.raises(psycopg.errors.RaiseException) as active:
        _call(pg_conn, seeded)
    assert _primary_message(active.value) == "app_checkout_active_subscription"

    pg_conn.execute(
        "update app_entitlements set status = 'canceled' where id = %s", (entitlement_id,)
    )
    first = _call(pg_conn, seeded, reference="pro-open")
    assert first[3] == "pro"
    with pytest.raises(psycopg.errors.RaiseException) as other_plan:
        _call(pg_conn, seeded, plan_key="basic", reference="basic-blocked")
    assert _primary_message(other_plan.value) == "app_checkout_already_open:pro"
    assert pg_conn.execute(
        "select count(*) from app_checkout_intents where business_slug = %s",
        (seeded["business"],),
    ).fetchone()[0] == 1


def test_port_rejects_every_unsaleable_money_shape(pg_conn):
    seeded = _seed(pg_conn)
    pg_conn.execute(
        "insert into app_plan_policies "
        "(business_slug, plan_key, tier, price_cents, currency, billing_interval, saleable, metadata) "
        "values (%s, 'disabled', 'paid', 100, 'usd', 'month', false, '{}'::jsonb), "
        "(%s, 'zero', 'paid', 0, 'usd', 'month', true, '{}'::jsonb), "
        "(%s, 'annual', 'paid', 100, 'usd', 'year', true, '{}'::jsonb), "
        "(%s, 'eur', 'paid', 100, 'eur', 'month', true, '{}'::jsonb), "
        "(%s, 'retired', 'paid', 100, 'usd', 'month', true, '{\"status\":\"retired\"}'::jsonb)",
        (seeded["business"],) * 5,
    )
    for plan_key in ("disabled", "zero", "annual", "eur", "retired", "missing"):
        with pytest.raises(psycopg.errors.RaiseException) as unavailable:
            _call(pg_conn, seeded, plan_key=plan_key, reference=f"invalid-{plan_key}")
        assert _primary_message(unavailable.value) == "app_checkout_plan_unavailable"
    assert pg_conn.execute(
        "select count(*) from app_checkout_intents where business_slug = %s",
        (seeded["business"],),
    ).fetchone()[0] == 0


def test_function_acl_and_direct_insert_are_fail_closed(pg_conn):
    seeded = _seed(pg_conn)
    signature = "takyon_app_create_checkout_intent(text,text,text,text,jsonb)"
    assert pg_conn.execute(
        "select has_function_privilege('takyon_app_runtime', %s, 'execute'), "
        "has_function_privilege('takyon_app', %s, 'execute'), "
        "has_function_privilege('takyon_operator_runtime', %s, 'execute'), "
        "has_function_privilege('takyon_operator_access', %s, 'execute'), "
        "has_function_privilege('takyon_safebox_authority', %s, 'execute')",
        (signature,) * 5,
    ).fetchone() == (True, True, False, False, False)

    pg_conn.execute("set role takyon_operator_runtime")
    try:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            pg_conn.execute(
                _FUNCTION_SQL,
                (
                    seeded["business"],
                    seeded["alice_hash"],
                    "pro",
                    "operator-refused",
                    "{}",
                ),
            ).fetchone()
    finally:
        pg_conn.execute("reset role")

    pg_conn.execute("set role takyon_app_runtime")
    try:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            pg_conn.execute(
                "insert into app_checkout_intents "
                "(business_slug, app_user_id, plan_key, client_reference_id, customer_email) "
                "values (%s, %s, 'pro', 'direct-bypass', 'forged@example.com')",
                (seeded["business"], seeded["alice"]),
            )
    finally:
        pg_conn.execute("reset role")


def test_concurrent_app_runtime_calls_create_exactly_one_intent(pg_store_dsn):
    with psycopg.connect(pg_store_dsn, autocommit=True) as admin:
        seeded = _seed(admin)

    workers = 8
    barrier = threading.Barrier(workers)

    def create(index: int):
        with psycopg.connect(pg_store_dsn, autocommit=True) as conn:
            conn.execute("set role takyon_app_runtime")
            barrier.wait()
            return conn.execute(
                _FUNCTION_SQL,
                (
                    seeded["business"],
                    seeded["alice_hash"],
                    "pro",
                    f"concurrent-{index}",
                    "{}",
                ),
            ).fetchone()

    with ThreadPoolExecutor(max_workers=workers) as pool:
        rows = [future.result() for future in [pool.submit(create, i) for i in range(workers)]]

    assert len({row[0] for row in rows}) == 1
    assert len({row[5] for row in rows}) == 1
    assert {row[2] for row in rows} == {seeded["alice"]}
    with psycopg.connect(pg_store_dsn, autocommit=True) as admin:
        assert admin.execute(
            "select count(*) from app_checkout_intents "
            "where business_slug = %s and app_user_id = %s",
            (seeded["business"], seeded["alice"]),
        ).fetchone()[0] == 1
