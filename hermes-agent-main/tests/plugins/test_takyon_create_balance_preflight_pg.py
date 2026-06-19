"""Company-creation balance preflight — GOAL_RULES §3 gap #2 (zero-balance create block).

Drives the authoritative server-side gate ``plugins.takyon.cli._operator_create_balance_preflight``
against real Postgres. This is the gate the ``takyon.dashboard.create`` gateway method calls BEFORE
any business row or bootstrap spend, so an operator with no spendable balance cannot have a company
page built (which would spend real provider money on the operator billing rail).

Spendable balance == allowance remaining (the same quantity the dashboard surfaces as
``account.spendable_cents``; the operator money rail is allowance-only). The preflight fails OPEN
only for identity-less / non-Postgres dev.

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
from plugins.takyon.cli import InsufficientOperatorBalance  # noqa: E402
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


def test_zero_balance_operator_is_blocked(pg_conn, monkeypatch):
    """An operator whose spendable balance is drained to zero (starter allowance reset to 0) MUST be
    refused company creation with InsufficientOperatorBalance.

    NOTE: provisioning grants a $1 starter allowance, so zero-balance is the *post-exhaustion* state
    — modelled here by resetting the included allowance to 0."""
    uid = _provision_operator(pg_conn)
    billing.grant_allowance(pg_conn, uid, 0, f"drain:{uid}")  # exhaust the starter allowance
    bal = billing.get_billing_balances(pg_conn, uid)
    assert bal.allowance_remaining_cents == 0  # truly empty (allowance is the only money rail)
    with _preflight_pointed_at(pg_conn, monkeypatch):
        with pytest.raises(InsufficientOperatorBalance) as exc:
            cli._operator_create_balance_preflight(uid)
    assert exc.value.spendable_cents == 0
    assert "insufficient_balance" in str(exc.value)


def test_fresh_operator_starter_allowance_is_allowed(pg_conn, monkeypatch):
    """A freshly provisioned operator carries the $1 starter allowance (spendable > 0), so the
    preflight lets them create their first company. Proves the gate blocks only a genuinely empty
    wallet, not every new operator."""
    uid = _provision_operator(pg_conn)
    bal = billing.get_billing_balances(pg_conn, uid)
    assert bal.allowance_remaining_cents > 0  # starter allowance present
    with _preflight_pointed_at(pg_conn, monkeypatch):
        cli._operator_create_balance_preflight(uid)  # must NOT raise


def test_operator_with_allowance_is_allowed(pg_conn, monkeypatch):
    """An operator with a positive subscription allowance has spendable > 0 → creation passes the
    preflight (no exception)."""
    uid = _provision_operator(pg_conn)
    billing.grant_allowance(pg_conn, uid, 5000, f"grant:{uid}")  # $50 allowance
    with _preflight_pointed_at(pg_conn, monkeypatch):
        cli._operator_create_balance_preflight(uid)  # must NOT raise


def test_operator_refunded_allowance_after_drain_is_allowed(pg_conn, monkeypatch):
    """Spendable tracks the CURRENT allowance, not a one-time starter grant: an operator whose
    starter allowance was drained to zero, then re-funded via a fresh ``grant_allowance``, can create
    again (proves the gate re-reads allowance_remaining each time, the sole operator money rail)."""
    uid = _provision_operator(pg_conn)
    billing.grant_allowance(pg_conn, uid, 0, f"drain:{uid}")  # zero out the starter allowance
    billing.grant_allowance(pg_conn, uid, 1500, f"refund:{uid}")  # $15 fresh allowance period
    bal = billing.get_billing_balances(pg_conn, uid)
    assert bal.allowance_remaining_cents == 1500
    with _preflight_pointed_at(pg_conn, monkeypatch):
        cli._operator_create_balance_preflight(uid)  # must NOT raise


def test_missing_billing_account_fails_closed(pg_conn, monkeypatch):
    """§3 (assume evil): a resolved operator on the Postgres plane with NO billing account row has no
    provable funding, so the preflight fails CLOSED (blocks), it does not silently allow."""
    ghost_uid = str(uuid.uuid4())  # valid uuid, never provisioned → no billing_accounts row
    with _preflight_pointed_at(pg_conn, monkeypatch):
        with pytest.raises(InsufficientOperatorBalance):
            cli._operator_create_balance_preflight(ghost_uid)


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
