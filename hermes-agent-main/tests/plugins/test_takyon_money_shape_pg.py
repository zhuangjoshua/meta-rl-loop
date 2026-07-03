"""Money-shape gate + operator-approval rail (modularization plan §2.7 UC4, archetypes §1.5 seam).

The gate closes the Roomier hole ON EVERY PATH: a business's plan/credit writes must match its
DECLARED money shape (default 'subscription' when undeclared — existing businesses byte-identical),
and the SHAPE itself can only change through an approved, single-consume operator approval — never
silently by a plan write, never by a wake turn.

Rig posture: plain PG rig (migrated throwaway DB via the pg_conn fixture), no safebox — mirrors
test_takyon_monthly_only_pg.py so the gate is CI/rig-provable.
"""

from __future__ import annotations

import uuid

import pytest

psycopg = pytest.importorskip("psycopg")

from plugins.takyon import app_entitlements as ents  # noqa: E402
from plugins.takyon import money_shape as ms  # noqa: E402


def _mk_business(conn) -> str:
    uid = str(uuid.uuid4())
    conn.execute(
        "insert into users (id, auth0_sub) values (%s, %s)", (uid, f"auth0|{uuid.uuid4().hex}")
    )
    slug = f"shape-{uuid.uuid4().hex[:8]}"
    conn.execute(
        "insert into businesses (slug, name, goal, status, mode, owner_user_id) "
        "values (%s, %s, 'g', 'active', 'test', %s)",
        (slug, slug, uid),
    )
    return slug


def _upsert_plan(conn, slug: str, *, plan_key: str = "starter", task_kind: str = ""):
    return ents.upsert_plan_policy(
        conn,
        slug,
        plan_key,
        price_cents=500,
        billing_interval="month",
        tier="starter",
        money_shape_task_kind=task_kind,
    )


# ── default shape: undeclared businesses behave exactly as today ─────────────────────────


def test_undeclared_business_defaults_to_subscription_and_plan_write_passes(pg_conn):
    slug = _mk_business(pg_conn)
    assert ms.get_money_shape(pg_conn, slug) == ms.SUBSCRIPTION
    _upsert_plan(pg_conn, slug)  # must not raise — prod default path byte-identical
    row = pg_conn.execute(
        "select money_shape from businesses where slug = %s", (slug,)
    ).fetchone()
    assert row[0] is None  # the gate never writes the shape as a side effect


def test_unknown_business_reads_default_never_masks_fk(pg_conn):
    assert ms.get_money_shape(pg_conn, f"phantom-{uuid.uuid4().hex[:8]}") == ms.SUBSCRIPTION


def test_invalid_shape_refused(pg_conn):
    slug = _mk_business(pg_conn)
    with pytest.raises(ms.InvalidMoneyShape):
        ms.set_money_shape(pg_conn, slug, "one_time_packs")
    with pytest.raises(ms.InvalidMoneyShape):
        ms.assert_write_matches_shape(pg_conn, slug, "")


# ── the gate at the choke point (the Roomier hole, every task kind) ──────────────────────


def test_credit_packs_business_refuses_subscription_plan_write_on_every_kind(pg_conn):
    slug = _mk_business(pg_conn)
    ms.set_money_shape(pg_conn, slug, ms.CREDIT_PACKS, require_approval=False)
    for kind in ("", "chat", "ceo_bootstrap", "ceo_wake"):
        with pytest.raises(ms.MoneyShapeViolation) as exc:
            _upsert_plan(pg_conn, slug, task_kind=kind)
        msg = str(exc.value)
        assert "credit_packs" in msg and "subscription" in msg  # names declared vs attempted
    rows = pg_conn.execute(
        "select count(*) from app_plan_policies where business_slug = %s", (slug,)
    ).fetchone()
    assert rows[0] == 0  # nothing written on any refused path


def test_subscription_business_plan_write_passes_and_gate_returns_declared(pg_conn):
    slug = _mk_business(pg_conn)
    ms.set_money_shape(pg_conn, slug, ms.SUBSCRIPTION, require_approval=False)
    assert ms.assert_write_matches_shape(pg_conn, slug, ms.SUBSCRIPTION) == ms.SUBSCRIPTION
    _upsert_plan(pg_conn, slug)


# ── shape declaration/change: approval-gated, idempotent, atomic single-consume ──────────


def test_same_shape_redeclaration_is_free(pg_conn):
    slug = _mk_business(pg_conn)
    # Declaring the default explicitly never needs an approval.
    assert ms.set_money_shape(pg_conn, slug, ms.SUBSCRIPTION) == ms.SUBSCRIPTION
    assert ms.set_money_shape(pg_conn, slug, ms.SUBSCRIPTION) == ms.SUBSCRIPTION


def test_shape_change_without_approval_fails_closed(pg_conn):
    slug = _mk_business(pg_conn)
    with pytest.raises(ms.ApprovalRequired):
        ms.set_money_shape(pg_conn, slug, ms.CREDIT_PACKS)
    assert ms.get_money_shape(pg_conn, slug) == ms.SUBSCRIPTION  # unchanged


def test_shape_change_with_approved_record_flips_and_single_consumes(pg_conn):
    slug = _mk_business(pg_conn)
    payload = {"from": ms.SUBSCRIPTION, "to": ms.CREDIT_PACKS}
    req = ms.request_approval(pg_conn, slug, ms.SHAPE_CHANGE_ACTION_KIND, payload)
    assert req.status == "pending"
    # Idempotent re-request returns the same pending record, no duplicate.
    again = ms.request_approval(pg_conn, slug, ms.SHAPE_CHANGE_ACTION_KIND, payload)
    assert again.payload_digest == req.payload_digest
    ms.decide_approval(pg_conn, slug, ms.SHAPE_CHANGE_ACTION_KIND, payload, approve=True)
    assert ms.set_money_shape(pg_conn, slug, ms.CREDIT_PACKS) == ms.CREDIT_PACKS
    # The approval is CONSUMED: flipping back (or re-running) needs a fresh approval.
    with pytest.raises(ms.ApprovalRequired):
        ms.set_money_shape(pg_conn, slug, ms.SUBSCRIPTION)
    status = pg_conn.execute(
        "select status from operator_approvals where business_slug = %s", (slug,)
    ).fetchone()
    assert status[0] == "consumed"


def test_denied_approval_never_authorizes(pg_conn):
    slug = _mk_business(pg_conn)
    payload = {"from": ms.SUBSCRIPTION, "to": ms.COGS_PASSTHROUGH}
    ms.request_approval(pg_conn, slug, ms.SHAPE_CHANGE_ACTION_KIND, payload)
    ms.decide_approval(pg_conn, slug, ms.SHAPE_CHANGE_ACTION_KIND, payload, approve=False)
    with pytest.raises(ms.ApprovalRequired):
        ms.set_money_shape(pg_conn, slug, ms.COGS_PASSTHROUGH)


def test_expired_approval_refused_and_marked(pg_conn):
    slug = _mk_business(pg_conn)
    payload = {"from": ms.SUBSCRIPTION, "to": ms.CREDIT_PACKS}
    ms.request_approval(pg_conn, slug, ms.SHAPE_CHANGE_ACTION_KIND, payload, ttl_seconds=1)
    ms.decide_approval(pg_conn, slug, ms.SHAPE_CHANGE_ACTION_KIND, payload, approve=True)
    pg_conn.execute(
        "update operator_approvals set expires_at = now() - interval '1 second' "
        "where business_slug = %s",
        (slug,),
    )
    # Direct consume (no wrapping transaction): refusal is the gate; the best-effort
    # 'expired' marking persists on the autocommit path.
    with pytest.raises(ms.ApprovalRequired):
        ms.consume_approval(
            pg_conn, slug, ms.SHAPE_CHANGE_ACTION_KIND, {"from": ms.SUBSCRIPTION, "to": ms.CREDIT_PACKS}
        )
    with pytest.raises(ms.ApprovalRequired):
        ms.set_money_shape(pg_conn, slug, ms.CREDIT_PACKS)
    assert ms.get_money_shape(pg_conn, slug) == ms.SUBSCRIPTION


def test_approval_for_different_payload_does_not_authorize(pg_conn):
    slug = _mk_business(pg_conn)
    ms.request_approval(
        pg_conn, slug, ms.SHAPE_CHANGE_ACTION_KIND, {"from": ms.SUBSCRIPTION, "to": ms.COGS_PASSTHROUGH}
    )
    ms.decide_approval(
        pg_conn,
        slug,
        ms.SHAPE_CHANGE_ACTION_KIND,
        {"from": ms.SUBSCRIPTION, "to": ms.COGS_PASSTHROUGH},
        approve=True,
    )
    # Approved for cogs_passthrough — a credit_packs change must still refuse (digest-exact).
    with pytest.raises(ms.ApprovalRequired):
        ms.set_money_shape(pg_conn, slug, ms.CREDIT_PACKS)


# ── migration/topology invariants (0062) ─────────────────────────────────────────────────


def test_migration_column_check_and_subuser_denial(pg_conn):
    # CHECK constraint on businesses.money_shape refuses unknown shapes at the DB layer too.
    slug = _mk_business(pg_conn)
    with pytest.raises(psycopg.errors.CheckViolation):
        with pg_conn.transaction():
            pg_conn.execute(
                "update businesses set money_shape = 'lemonade_stand' where slug = %s", (slug,)
            )
    # operator_approvals is OPERATOR-plane: the app/subuser runtime role holds no privilege on it.
    for role in ("takyon_app_runtime", "takyon_app"):
        got = pg_conn.execute(
            "select has_table_privilege(%s, 'operator_approvals', 'select') "
            " or has_table_privilege(%s, 'operator_approvals', 'insert') "
            " or has_table_privilege(%s, 'operator_approvals', 'update') "
            " or has_table_privilege(%s, 'operator_approvals', 'delete')",
            (role, role, role, role),
        ).fetchone()
        assert got[0] is False, f"{role} must hold NO privilege on operator_approvals"
    # No app-plane-readable view exposes the money_shape column (behavior-shaped: whatever
    # app-runtime views exist, none carries the new column).
    exposed = pg_conn.execute(
        "select v.table_name from information_schema.view_column_usage u "
        "join information_schema.views v on v.table_name = u.view_name "
        "where u.column_name = 'money_shape'"
    ).fetchall()
    assert exposed == [], f"views exposing money_shape: {exposed}"
