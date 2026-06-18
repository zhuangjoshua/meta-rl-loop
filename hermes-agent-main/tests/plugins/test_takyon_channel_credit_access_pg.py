"""Cross-tenant access gate on the channel creative-credit READ tool (Postgres).

Regression for a real read-only cross-tenant leak: ``business_read_channel_credit_budgets`` resolved
its slug through ``_resolved_business_slug`` (which only enforces the session-pin) but skipped the
operator-ownership check that the matching WRITE
(``business_set_channel_credit_budgets`` → ``store.commit`` →
``_enforce_operator_business_access``) always runs. An unpinned/global operator turn belonging to one
tenant could therefore read a DIFFERENT tenant's credit balance/allocations/usage. The fix routes the
read through the same owner gate via the public ``TakyonStore.enforce_operator_business_access`` seam.

Drives the REAL ``core`` read handler against a throwaway migrated Postgres DB, with the store wired
to a chosen operator principal exactly the way the runtime resolves it (``_store()`` →
``TakyonStore`` bound to the session/operator user). Asserts:
  * the OWNER's operator turn reads its own business successfully,
  * an unpinned operator turn from a DIFFERENT tenant is DENIED ("access denied for business:<slug>"),
  * an operator turn whose session is pinned to the attacker's business is DENIED before the read
    ("business is bound to the current session: <slug>").

Skips unless psycopg is importable and TAKYON_TEST_PG_DSN is set (see tests/plugins/conftest.py).
"""
from __future__ import annotations

import json
import uuid

import pytest

psycopg = pytest.importorskip("psycopg")

from plugins.takyon import core  # noqa: E402
from plugins.takyon.control_plane import provision_user_on_first_login  # noqa: E402


def _seed_owned_business(dsn: str) -> tuple[str, str]:
    """Provision a fresh Takyon user (operator) and an owned business on the SAME dsn the store will
    open. Returns ``(slug, owner_user_id)``."""
    slug = f"biz-{uuid.uuid4().hex[:8]}"
    with psycopg.connect(dsn, autocommit=True) as conn:
        owner_id, _created, _raw = provision_user_on_first_login(conn, f"auth0|{uuid.uuid4().hex}")
        conn.execute(
            "insert into businesses (slug, name, owner_user_id) values (%s, %s, %s)",
            (slug, slug.title(), str(owner_id)),
        )
    return slug, str(owner_id)


def _bound_store(pg_store_dsn, tmp_path, operator_user_id: str) -> core.TakyonStore:
    """A TakyonStore on the throwaway DB bound to a specific operator principal — the same shape
    ``_store()`` builds from the resolved session/operator user at runtime."""
    return core.TakyonStore(
        root=tmp_path,
        database_url=pg_store_dsn,
        operator_user_id=operator_user_id,
    )


def test_owner_read_succeeds_cross_tenant_denied_and_pinned_attacker_denied(
    pg_store_dsn, tmp_path, monkeypatch
):
    victim_slug, victim_owner = _seed_owned_business(pg_store_dsn)
    attacker_slug, attacker_owner = _seed_owned_business(pg_store_dsn)
    assert victim_owner != attacker_owner

    # Stub the credit-balance snapshot so the assertion targets the OWNERSHIP GATE (the thing under
    # test), not the creative-credit balance backend (which proxies to the Safebox service and is
    # environment-dependent). A deny must short-circuit BEFORE the snapshot, so we also record which
    # business the snapshot was ever computed for — it must only ever be the victim's own read.
    snapshot_calls: list[str] = []

    def _fake_snapshot(business: str) -> dict:
        snapshot_calls.append(business)
        return {"balance_credits": 10, "channels": {}}

    monkeypatch.setattr(core, "_creative_credit_budget_snapshot", _fake_snapshot)
    monkeypatch.setattr(core, "_creative_credit_action_costs", lambda: {})

    # 1) OWNER, unpinned: passes the gate and reads its own business successfully.
    monkeypatch.setattr(
        core, "_store", lambda: _bound_store(pg_store_dsn, tmp_path, victim_owner)
    )
    owner_read = json.loads(core.handle_business_read_channel_credit_budgets({"business": victim_slug}))
    assert owner_read["success"] is True, owner_read
    assert owner_read["business"] == victim_slug
    assert owner_read["value"]["balance_credits"] == 10
    assert snapshot_calls == [victim_slug]

    # 2) CROSS-TENANT, unpinned: a different tenant's operator turn is DENIED, no value leaked, and
    #    the snapshot is NEVER computed for the victim.
    monkeypatch.setattr(
        core, "_store", lambda: _bound_store(pg_store_dsn, tmp_path, attacker_owner)
    )
    cross = json.loads(core.handle_business_read_channel_credit_budgets({"business": victim_slug}))
    assert cross.get("success") is False, cross
    assert f"access denied for business:{victim_slug}" in str(cross.get("error") or "")
    assert "value" not in cross and "balance_credits" not in json.dumps(cross)
    assert snapshot_calls == [victim_slug]  # unchanged: the gate fired before any read

    # 3) PINNED-TO-ATTACKER: session bound to the attacker business, asking for the victim → DENIED
    #    before any read (slug-pin mismatch).
    from gateway.session_context import clear_session_vars, set_session_vars

    tokens = set_session_vars(user_id=attacker_owner, business_slug=attacker_slug)
    try:
        pinned = json.loads(
            core.handle_business_read_channel_credit_budgets({"business": victim_slug})
        )
    finally:
        clear_session_vars(tokens)
    assert pinned.get("success") is False, pinned
    assert f"business is bound to the current session: {attacker_slug}" in str(
        pinned.get("error") or ""
    )
    assert "balance_credits" not in json.dumps(pinned)
    assert snapshot_calls == [victim_slug]  # still unchanged: pinned mismatch denied before read
