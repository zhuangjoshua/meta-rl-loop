from __future__ import annotations

import ast
from pathlib import Path

import pytest

from plugins.takyon import app_connections, app_directory, app_profiles, app_records


def test_session_identity_override_helpers_reject_source_user_mixups():
    helpers = (
        app_connections._reject_session_identity_override,
        app_directory._reject_session_identity_override,
        app_profiles._reject_session_identity_override,
        app_records._reject_session_identity_override,
    )
    for helper in helpers:
        helper(session_token="sess", app_user_id=None, email=None)
        helper(session_token=None, app_user_id="user_1", email=None)
        with pytest.raises(ValueError, match="session_token is authoritative"):
            helper(session_token="sess", app_user_id="user_2", email=None)
        with pytest.raises(ValueError, match="session_token is authoritative"):
            helper(session_token="sess", app_user_id=None, email="other@example.com")


def test_public_app_post_never_uses_body_app_user_id_as_source_identity():
    source_path = Path(__file__).resolve().parents[2] / "takyon_cli" / "web_server.py"
    source = source_path.read_text(encoding="utf-8")
    module = ast.parse(source)
    post_node = next(
        node
        for node in module.body
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "_takyon_app_post"
    )
    post_source = ast.get_source_segment(source, post_node) or ""
    body_user_lines = [
        line.strip()
        for line in post_source.splitlines()
        if 'body.get("app_user_id")' in line or 'body.get("appUserId")' in line
    ]

    assert body_user_lines == [
        '"app_user_id": body.get("app_user_id") or body.get("recipient_app_user_id"),'
    ]


def test_public_checkout_requires_session_before_creating_intent():
    source_path = Path(__file__).resolve().parents[2] / "takyon_cli" / "web_server.py"
    source = source_path.read_text(encoding="utf-8")
    start = source.index('    if parts == ["checkout"]:\n        token = _takyon_app_session_token(request)')
    end = source.index('    if parts == ["account"]:', start)
    checkout_block = source[start:end]

    assert 'if not token:' in checkout_block
    assert 'HTTPStatus.UNAUTHORIZED, {"success": False, "error": "missing app session"}' in checkout_block
    assert "account_status, account = _takyon_app_tool(handle_business_read_app_account" in checkout_block
    assert "if account_status != int(HTTPStatus.OK):" in checkout_block
    assert checkout_block.index("if not token:") < checkout_block.index(
        "handle_business_create_app_checkout"
    )
    assert checkout_block.index("if account_status != int(HTTPStatus.OK):") < checkout_block.index(
        "handle_business_create_app_checkout"
    )


def test_subuser_money_access_writes_go_through_gate_functions():
    root = Path(__file__).resolve().parents[2]
    entitlements_source = (root / "plugins" / "takyon" / "app_entitlements.py").read_text(
        encoding="utf-8"
    ).lower()
    payments_source = (root / "plugins" / "takyon" / "app_payments.py").read_text(
        encoding="utf-8"
    ).lower()
    migration = (
        root
        / "plugins"
        / "takyon"
        / "db"
        / "migrations"
        / "0041_subuser_money_ledger_write_gates.sql"
    ).read_text(encoding="utf-8").lower()

    assert "insert into app_entitlements" not in entitlements_source
    assert "update app_entitlements" not in entitlements_source
    assert "insert into app_revenue_events" not in payments_source
    assert "update app_entitlements" not in payments_source
    assert "safebox_insert_app_entitlement" in entitlements_source
    assert "safebox_insert_app_revenue_event" in payments_source
    assert (
        "revoke insert, update, delete on app_entitlements, app_revenue_events "
        "from takyon_runtime"
    ) in migration
    assert (
        "revoke insert, update, delete on app_entitlements, app_revenue_events "
        "from takyon_app"
    ) in migration
