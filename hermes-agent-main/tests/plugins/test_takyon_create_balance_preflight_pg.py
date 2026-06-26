"""Company-creation balance preflight — operator create is UNGATED from the plan (dogfooding).

The create chokepoint ``plugins.takyon.cli._operator_create_balance_preflight`` used to gate company
creation on ``allowance_percent_remaining > 3`` and decrement 3% of the operator plan allowance per
create. That plan coupling was deliberately removed: a Takyon user may create any number of
businesses regardless of plan balance, including a fully drained or never-funded wallet. The real
money chokepoint is the per-turn runtime usage gate (``billing.reserve``), not company creation, and
the subuser/product rails are untouched.

These tests pin the NEW contract: the preflight never refuses on balance and never charges the
operator wallet on create. The dev fail-opens (identity-less / non-Postgres) still short-circuit
before touching the DB.

Skips unless psycopg is importable and TAKYON_TEST_PG_DSN is set (see tests/conftest.py).
"""
from __future__ import annotations

import uuid
from contextlib import contextmanager

import psycopg.conninfo
import pytest

psycopg = pytest.importorskip("psycopg")

from plugins.takyon import billing, cli  # noqa: E402
from plugins.takyon import runtime_app  # noqa: E402
from plugins.takyon.control_plane import provision_user_on_first_login  # noqa: E402


def _provision_operator(conn) -> str:
    uid, _created, _raw = provision_user_on_first_login(conn, f"auth0|{uuid.uuid4().hex}")
    return uid


@contextmanager
def _preflight_pointed_at(pg_conn, monkeypatch):
    """Point the preflight's lazy ``resolve_database_url`` + ``_db_backend`` at the throwaway DB so
    its own ``psycopg.connect`` lands on the same per-worker test database. Patches the functions
    the preflight imports lazily (runtime_app.resolve_database_url, core._db_backend), bypassing the
    macOS URL policy entirely."""
    from plugins.takyon import core

    dsn = psycopg.conninfo.make_conninfo(
        "", **{**psycopg.conninfo.conninfo_to_dict(pg_conn.info.dsn or ""), "dbname": pg_conn.info.dbname}
    )
    monkeypatch.setattr(runtime_app, "resolve_database_url", lambda *a, **k: dsn)
    monkeypatch.setattr(core, "_db_backend", lambda: "postgres")
    yield


def test_zero_balance_operator_is_allowed(pg_conn, monkeypatch):
    """An operator whose spendable allowance is drained to zero may STILL create a company: the
    ungated preflight returns without raising. (Previously this raised InsufficientOperatorBalance.)"""
    uid = _provision_operator(pg_conn)
    billing.grant_allowance(pg_conn, uid, 0, f"drain:{uid}")  # exhaust the starter allowance
    bal = billing.get_billing_balances(pg_conn, uid)
    assert bal.allowance_remaining_cents == 0
    with _preflight_pointed_at(pg_conn, monkeypatch):
        assert cli._operator_create_balance_preflight(uid) is None  # must NOT raise
        # ...and a slug-bearing create call must not charge either.
        assert (
            cli._operator_create_balance_preflight(uid, business_slug="acme", defer_settle=True)
            is None
        )
    bal_after = billing.get_billing_balances(pg_conn, uid)
    assert bal_after.allowance_remaining_cents == 0  # no negative, no charge applied


def test_funded_operator_is_allowed_and_not_charged(pg_conn, monkeypatch):
    """A funded operator passes the preflight (no raise) AND is NOT decremented: company creation no
    longer consumes the operator plan allowance, so business count is decoupled from plan size."""
    uid = _provision_operator(pg_conn)
    billing.grant_allowance(pg_conn, uid, 5000, f"grant:{uid}")  # $50 allowance
    before = billing.get_billing_balances(pg_conn, uid).allowance_remaining_cents
    assert before == 5000
    with _preflight_pointed_at(pg_conn, monkeypatch):
        # The real create chokepoint calls the slug-bearing, deferred-settle form.
        assert (
            cli._operator_create_balance_preflight(uid, business_slug="acme", defer_settle=True)
            is None
        )
    after = billing.get_billing_balances(pg_conn, uid).allowance_remaining_cents
    assert after == 5000, "ungated create must not decrement the operator allowance"


def test_missing_billing_account_is_allowed(pg_conn, monkeypatch):
    """A resolved operator with NO billing account row may still create: the ungated preflight does
    not read or require a billing account. (Previously this failed CLOSED.)"""
    ghost_uid = str(uuid.uuid4())  # valid uuid, never provisioned → no billing_accounts row
    with _preflight_pointed_at(pg_conn, monkeypatch):
        assert cli._operator_create_balance_preflight(ghost_uid) is None  # must NOT raise


def test_no_operator_identity_fails_open_for_dev(pg_conn, monkeypatch):
    """Fail-open ONLY for the genuinely identity-less dev path: with no resolved operator id the
    preflight returns without touching the DB (local development must not be blocked)."""
    from plugins.takyon import core

    monkeypatch.setattr(core, "_db_backend", lambda: "postgres")
    # No operator id and no session/global identity env → _resolved_operator_user_id == "".
    monkeypatch.delenv("TAKYON_SESSION_USER_ID", raising=False)
    monkeypatch.delenv("TAKYON_OPERATOR_USER_ID", raising=False)
    monkeypatch.setattr(core, "operator_identity_mode", lambda: True)
    cli._operator_create_balance_preflight(None)  # must NOT raise, must NOT connect


def test_non_postgres_backend_fails_open(pg_conn, monkeypatch):
    """Fail-open for the non-Postgres (SQLite dev) backend: there is no billing plane to read, so
    creation is not blocked locally."""
    from plugins.takyon import core

    uid = _provision_operator(pg_conn)
    monkeypatch.setattr(core, "_db_backend", lambda: "sqlite")
    cli._operator_create_balance_preflight(uid)  # must NOT raise
