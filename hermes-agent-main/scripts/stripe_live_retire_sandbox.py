#!/usr/bin/env python3
"""Explicit root-SSH production Stripe sandbox retirement; never run by migrations."""

from __future__ import annotations

import argparse
import json
import os
import socket

from plugins.takyon.runtime_app import resolve_database_url

_EXPECTED_SOURCE = "acct_1TXWsc9n69Zj6BuE"
_EXPECTED_TARGET = "acct_1TXWsW7tYL4lkVC6"


def _require_root_ssh_operator() -> tuple[str, str]:
    if os.geteuid() != 0:
        raise RuntimeError("root_required")
    if str(os.getenv("TAKYON_ENV") or "").strip().lower() != "prod":
        raise RuntimeError("prod_environment_required")
    if str(os.getenv("TAKYON_HOST_ROLE") or "").strip().lower() != "operator":
        raise RuntimeError("operator_host_required")
    if str(os.getenv("TAKYON_STRIPE_MODE") or "test").strip().lower() != "test":
        raise RuntimeError("sandbox_mode_required_before_finalization")
    if str(os.getenv("TAKYON_STRIPE_CHECKOUT_DISABLED") or "").strip().lower() not in {
        "1",
        "true",
        "yes",
        "on",
    }:
        raise RuntimeError("checkout_pause_required")
    ssh_connection = str(os.getenv("SSH_CONNECTION") or "").strip().split()
    if len(ssh_connection) != 4:
        raise RuntimeError("ssh_session_required")
    return ssh_connection[0], socket.gethostname()


def finalize(source_account: str, target_account: str) -> dict:
    if source_account != _EXPECTED_SOURCE or target_account != _EXPECTED_TARGET:
        raise RuntimeError("stripe_account_pair_mismatch")
    configured_target = str(os.getenv("TAKYON_STRIPE_ACCOUNT_ID") or "").strip()
    if configured_target != target_account:
        raise RuntimeError("TAKYON_STRIPE_ACCOUNT_ID_mismatch")
    ssh_client, operator_host = _require_root_ssh_operator()
    import psycopg

    with psycopg.connect(
        resolve_database_url(plane="migration"), autocommit=True, prepare_threshold=None
    ) as conn:
        row = conn.execute(
            "select takyon_finalize_stripe_live_cutover(%s, %s, %s::inet, %s)",
            (source_account, target_account, ssh_client, operator_host),
        ).fetchone()
    return dict((row or [{}])[0] or {})


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-account", required=True)
    parser.add_argument("--target-account", required=True)
    args = parser.parse_args()
    print(json.dumps(finalize(args.source_account, args.target_account), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
