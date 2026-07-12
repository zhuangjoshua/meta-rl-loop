"""Pins the Safebox billing-function ACL repair added after the 2026-07-11 refund outage."""

from __future__ import annotations

import re
from pathlib import Path


_MIGRATION = (
    Path(__file__).resolve().parents[2]
    / "plugins"
    / "takyon"
    / "db"
    / "migrations"
    / "0083_safebox_billing_function_acl.sql"
)

_EXPECTED_FUNCTIONS = {
    "safebox_billing_open_account(uuid,bigint)",
    "safebox_billing_grant_allowance(uuid,bigint,text,timestamptz,timestamptz)",
    "safebox_billing_reserve(uuid,bigint,text,text,text)",
    "safebox_billing_settle(text,bigint)",
    "safebox_billing_refund(text)",
}


def _grant_statements() -> list[tuple[str, str]]:
    sql = re.sub(r"--[^\n]*", "", _MIGRATION.read_text(encoding="utf-8").lower())
    statements = re.findall(
        r"grant\s+execute\s+on\s+function\s+([^;]+?)\s+to\s+([a-z0-9_,\s]+)\s*;",
        sql,
        flags=re.DOTALL,
    )
    return [
        (re.sub(r"\s+", "", signature), re.sub(r"\s+", "", roles))
        for signature, roles in statements
    ]


def test_acl_repair_explicitly_pins_every_live_safebox_billing_entry_point():
    assert _MIGRATION.exists()
    grants = _grant_statements()
    assert {signature for signature, _ in grants} == _EXPECTED_FUNCTIONS
    assert {role for _, role in grants} == {"takyon_safebox_authority"}


def test_acl_repair_pins_the_reserve_finalize_path_that_releases_job_holds():
    grants = dict(_grant_statements())
    for signature in (
        "safebox_billing_reserve(uuid,bigint,text,text,text)",
        "safebox_billing_settle(text,bigint)",
        "safebox_billing_refund(text)",
    ):
        assert grants[signature] == "takyon_safebox_authority"
