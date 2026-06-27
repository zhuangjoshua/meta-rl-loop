#!/usr/bin/env python3
"""Probe Takyon DB authority split on a live host.

Run this on the target VPS with that service's env loaded. It intentionally
prints role names and check results, never DSNs or secrets.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import psycopg  # noqa: E402

from plugins.takyon.runtime_app import (  # noqa: E402
    assert_takyon_pg_role,
    configure_takyon_pg_session,
    current_takyon_pg_roles,
    resolve_database_url,
)


APP_DENIED_SQL: tuple[tuple[str, str], ...] = (
    ("app_cannot_select_businesses", "select 1 from businesses limit 1"),
    ("app_cannot_select_app_users", "select 1 from app_users limit 1"),
    ("app_cannot_select_app_sessions", "select 1 from app_sessions limit 1"),
    ("app_cannot_insert_usage", "insert into app_usage_events default values"),
    ("app_cannot_insert_entitlements", "insert into app_entitlements default values"),
    ("app_cannot_insert_revenue", "insert into app_revenue_events default values"),
)

APP_SET_ROLE_DENIED: tuple[str, ...] = (
    "takyon_operator_runtime",
    "takyon_safebox_authority",
    "takyon_runtime",
    "postgres",
)

OPERATOR_DENIED_SQL: tuple[tuple[str, str], ...] = (
    ("operator_cannot_insert_usage", "insert into app_usage_events default values"),
    ("operator_cannot_insert_entitlements", "insert into app_entitlements default values"),
    ("operator_cannot_insert_revenue", "insert into app_revenue_events default values"),
    ("operator_cannot_insert_billing", "insert into billing_entries default values"),
    ("operator_cannot_insert_custody", "insert into custody_entries default values"),
    (
        "operator_cannot_insert_creative_credit",
        "insert into business_creative_credit_entries default values",
    ),
)

OPERATOR_SET_ROLE_DENIED: tuple[str, ...] = (
    "takyon_app_runtime",
    "takyon_safebox_authority",
    "postgres",
)


def _check(name: str, ok: bool, **extra: Any) -> dict[str, Any]:
    payload = {"name": name, "ok": bool(ok)}
    payload.update(extra)
    return payload


def _error_payload(exc: BaseException) -> dict[str, str]:
    return {
        "error": str(exc),
        "sqlstate": str(getattr(exc, "sqlstate", "") or ""),
    }


def _expected_denied(conn: Any, name: str, sql: str) -> dict[str, Any]:
    try:
        conn.execute(sql)
    except Exception as exc:  # noqa: BLE001 - any DB refusal is useful evidence.
        conn.rollback()
        return _check(name, True, denied=True, **_error_payload(exc))
    try:
        conn.execute("reset role")
    except Exception:
        pass
    conn.rollback()
    return _check(name, False, denied=False, error="unexpected_success")


def _expected_allowed(conn: Any, name: str, sql: str) -> dict[str, Any]:
    try:
        conn.execute(sql)
    except Exception as exc:  # noqa: BLE001 - report the live DB refusal.
        conn.rollback()
        return _check(name, False, **_error_payload(exc))
    conn.rollback()
    return _check(name, True)


def _expected_set_role_denied(conn: Any, role: str) -> dict[str, Any]:
    return _expected_denied(conn, f"cannot_set_role_{role}", f"set role {role}")


def _probe_app_bypass_guc(conn: Any) -> dict[str, Any]:
    try:
        conn.execute("select set_config('takyon.rls_bypass', '1', false)")
        row = conn.execute("select takyon_rls_bypass()").fetchone()
        enabled = bool(row[0]) if row is not None else False
    except Exception as exc:  # noqa: BLE001 - function absence or SQL failure is a failed probe.
        conn.rollback()
        return _check("app_cannot_enable_rls_bypass_guc", False, **_error_payload(exc))
    finally:
        try:
            conn.execute("select set_config('takyon.rls_bypass', '0', false)")
        except Exception:
            pass
    conn.rollback()
    return _check(
        "app_cannot_enable_rls_bypass_guc",
        not enabled,
        takyon_rls_bypass=enabled,
    )


def _probe_operator_set_role(conn: Any) -> list[dict[str, Any]]:
    return [_expected_set_role_denied(conn, role) for role in OPERATOR_SET_ROLE_DENIED]


def _probe_app_set_role(conn: Any) -> list[dict[str, Any]]:
    return [_expected_set_role_denied(conn, role) for role in APP_SET_ROLE_DENIED]


def _run_plane_checks(conn: Any, plane: str) -> list[dict[str, Any]]:
    if plane == "app":
        checks = [_expected_denied(conn, name, sql) for name, sql in APP_DENIED_SQL]
        checks.extend(_probe_app_set_role(conn))
        checks.append(_probe_app_bypass_guc(conn))
        return checks
    if plane == "operator":
        checks = [_expected_allowed(conn, "operator_can_select_businesses", "select 1 from businesses limit 1")]
        checks.extend(_expected_denied(conn, name, sql) for name, sql in OPERATOR_DENIED_SQL)
        checks.extend(_probe_operator_set_role(conn))
        return checks
    if plane == "safebox":
        return [_expected_allowed(conn, "safebox_can_select_businesses", "select 1 from businesses limit 1")]
    return []


def probe_plane(plane: str, *, explicit_dsn: str | None = None) -> dict[str, Any]:
    database_url = resolve_database_url(explicit_dsn, plane=plane)
    conn = None
    try:
        conn = psycopg.connect(database_url, autocommit=False, prepare_threshold=None)
        configure_takyon_pg_session(conn, bypass=(plane != "app"))
        conn.commit()
        session_user, current_user = assert_takyon_pg_role(conn, plane)
        checks = [
            _check(
                "role_assertion",
                True,
                session_user=session_user,
                current_user=current_user,
                plane=plane,
            )
        ]
        checks.extend(_run_plane_checks(conn, plane))
        live_session_user, live_current_user = current_takyon_pg_roles(conn)
        checks.append(
            _check(
                "final_role_state",
                live_session_user == session_user and live_current_user == current_user,
                session_user=live_session_user,
                current_user=live_current_user,
            )
        )
    except Exception as exc:  # noqa: BLE001 - surfaced as JSON for deployment logs.
        checks = [_check("probe_failed", False, **_error_payload(exc))]
    finally:
        if conn is not None:
            conn.close()
    return {
        "plane": plane,
        "ok": all(bool(item.get("ok")) for item in checks),
        "checks": checks,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--plane",
        action="append",
        choices=("operator", "app", "safebox"),
        help="Plane to probe. Repeat for multiple planes. Defaults to all.",
    )
    parser.add_argument(
        "--dsn",
        default=None,
        help="Explicit DSN override for one-off maintenance. Do not use in normal production probes.",
    )
    args = parser.parse_args(argv)

    planes = args.plane or ["operator", "app", "safebox"]
    if args.dsn and len(planes) != 1:
        parser.error("--dsn may be used with exactly one --plane")
    results = [probe_plane(plane, explicit_dsn=args.dsn) for plane in planes]
    ok = all(item.get("ok") for item in results)
    print(json.dumps({"ok": ok, "results": results}, indent=2, sort_keys=True))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
