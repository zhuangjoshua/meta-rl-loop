"""Phase 2/3 two-tier validation (deploy/SAFEBOX-BROKER-REMEDIATION-PLAN.md).

Pins that the safebox derives the AUTHORITATIVE scope from validated reads — boundary 1 (the operator
must own the business) and boundary 2 (the session resolves the real sub-user; a billable call needs a
paid entitlement). No client-asserted slug/sub-user can leak through.
"""
import types

import pytest

from plugins.takyon import app_entitlements, app_identity
from plugins.takyon.safebox_authz import (
    AuthzError,
    authorize_operator_call,
    authorize_product_call,
)


class _Cursor:
    def __init__(self, row):
        self._row = row

    def fetchone(self):
        return self._row


class _Conn:
    """Fake conn whose only query is the owner_user_id lookup."""

    def __init__(self, owner_row):
        self._owner_row = owner_row

    def execute(self, sql, params=None):
        return _Cursor(self._owner_row)


def _user(uid):
    return types.SimpleNamespace(id=uid)


def _ent(tier):
    return types.SimpleNamespace(tier=tier, status="active")


def test_product_call_returns_validated_owner_and_subuser(monkeypatch):
    monkeypatch.setattr(app_identity, "validate_session", lambda c, b, t: _user("cust_X"))
    monkeypatch.setattr(app_entitlements, "get_active_entitlement", lambda c, b, u: _ent("pro"))
    scope = authorize_product_call(
        _Conn({"owner_user_id": "user_A"}),
        business_slug="climblog",
        session_token="tok",
        action="ai.generate",
        max_cost_microusd=2000,
    )
    assert scope.takyon_user_id == "user_A"
    assert scope.business_slug == "climblog"
    assert scope.app_user_id == "cust_X"
    assert scope.max_cost_microusd == 2000


def test_product_call_invalid_session_rejected(monkeypatch):
    monkeypatch.setattr(app_identity, "validate_session", lambda c, b, t: None)
    with pytest.raises(AuthzError, match="invalid_session"):
        authorize_product_call(
            _Conn({"owner_user_id": "user_A"}),
            business_slug="climblog",
            session_token="bad",
            action="ai.generate",
            max_cost_microusd=2000,
        )


def test_product_call_unentitled_billable_rejected(monkeypatch):
    monkeypatch.setattr(app_identity, "validate_session", lambda c, b, t: _user("cust_X"))
    monkeypatch.setattr(app_entitlements, "get_active_entitlement", lambda c, b, u: None)
    with pytest.raises(AuthzError, match="subscription_required"):
        authorize_product_call(
            _Conn({"owner_user_id": "user_A"}),
            business_slug="climblog",
            session_token="tok",
            action="ai.generate",
            max_cost_microusd=2000,
        )


def test_product_free_action_skips_entitlement(monkeypatch):
    monkeypatch.setattr(app_identity, "validate_session", lambda c, b, t: _user("cust_X"))

    def _boom(*a, **k):
        raise AssertionError("entitlement must not be checked for a zero-cost action")

    monkeypatch.setattr(app_entitlements, "get_active_entitlement", _boom)
    scope = authorize_product_call(
        _Conn({"owner_user_id": "user_A"}),
        business_slug="climblog",
        session_token="tok",
        action="ping",
        max_cost_microusd=0,
    )
    assert scope.app_user_id == "cust_X" and scope.max_cost_microusd == 0


def test_operator_call_enforces_ownership_cross_user_rejected():
    conn = _Conn({"owner_user_id": "user_A"})
    ok = authorize_operator_call(
        conn, business_slug="climblog", operator_user_id="user_A", action="coding.task", max_cost_microusd=5000
    )
    assert ok.takyon_user_id == "user_A" and ok.app_user_id is None
    with pytest.raises(AuthzError, match="not_business_owner"):
        authorize_operator_call(
            conn, business_slug="climblog", operator_user_id="user_B", action="coding.task", max_cost_microusd=5000
        )


def test_operator_call_accepts_tuple_rows_from_live_psycopg():
    scope = authorize_operator_call(
        _Conn(("user_A",)),
        business_slug="climblog",
        operator_user_id="user_A",
        action="coding.task",
        max_cost_microusd=5000,
    )
    assert scope.takyon_user_id == "user_A"


def test_unknown_business_rejected(monkeypatch):
    monkeypatch.setattr(app_identity, "validate_session", lambda c, b, t: _user("cust_X"))
    monkeypatch.setattr(app_entitlements, "get_active_entitlement", lambda c, b, u: _ent("pro"))
    with pytest.raises(AuthzError, match="unknown_business"):
        authorize_product_call(
            _Conn(None),
            business_slug="ghost",
            session_token="tok",
            action="ai.generate",
            max_cost_microusd=2000,
        )
