"""Pins the internal-reservation release rename and removal of the old refund surface."""

from __future__ import annotations

import re
from pathlib import Path

from plugins.takyon import billing, safebox, safebox_app


_MIGRATION = (
    Path(__file__).resolve().parents[2]
    / "plugins"
    / "takyon"
    / "db"
    / "migrations"
    / "0088_billing_reservation_release.sql"
)


def _sql() -> str:
    return re.sub(r"--[^\n]*", "", _MIGRATION.read_text(encoding="utf-8").lower())


def test_internal_allowance_cleanup_has_no_refund_api():
    assert callable(billing.release_reservation)
    assert callable(safebox.billing_release_reservation)
    assert not hasattr(billing, "refund")
    assert not hasattr(safebox, "billing_refund")
    paths = {route.path for route in safebox_app.build_safebox_app().routes}
    assert "/v1/billing/reservations/release" in paths
    assert "/v1/billing/refund" not in paths


def test_migration_renames_the_event_and_removes_the_old_function():
    sql = _sql()
    assert "alter type billing_entry_kind rename value 'refund' to 'release'" in sql
    assert "create or replace function safebox_billing_release_reservation(" in sql
    assert "drop function if exists safebox_billing_refund(text)" in sql
    assert "kind in ('settle', 'release')" in sql
    assert "'allowance', 'release'" in sql


def test_release_function_is_safebox_authority_only():
    sql = _sql()
    signature = "safebox_billing_release_reservation(text)"
    assert re.search(
        rf"revoke\s+execute\s+on\s+function\s+{re.escape(signature)}\s+"
        r"from\s+public,\s*takyon_runtime,\s*takyon_operator_runtime,\s*"
        r"takyon_app_runtime,\s*takyon_app,\s*safebox\s*;",
        sql,
        flags=re.DOTALL,
    )
    assert re.search(
        rf"grant\s+execute\s+on\s+function\s+{re.escape(signature)}\s+"
        r"to\s+takyon_safebox_authority\s*;",
        sql,
        flags=re.DOTALL,
    )
