"""CLI handlers for ``takyon migrate ...``."""
from __future__ import annotations

import hashlib
import json
import os
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from takyon_cli.colors import Colors, color
from takyon_cli.config import load_config


def cmd_migrate(args: Any) -> int:
    """Dispatcher for ``takyon migrate <subtype>``."""
    sub = getattr(args, "migrate_type", None)
    if sub == "xai":
        return cmd_migrate_xai(args)
    if sub is None:
        return cmd_migrate_db(args)

    print("usage: takyon migrate [--dry-run] | takyon migrate xai [--apply] [--no-backup]", file=sys.stderr)
    raise SystemExit(2)


def cmd_migrate_db(args: Any) -> int:
    """Run tracked Postgres migrations through the canonical migration role."""
    dry_run = bool(getattr(args, "dry_run", False))

    from plugins.takyon.core import load_takyon_env
    from plugins.takyon.db.runner import (
        assert_migration_topology,
        migration_files,
        run_migrations,
    )
    from plugins.takyon.runtime_app import assert_takyon_pg_role, resolve_database_url
    import psycopg

    load_takyon_env()
    host_role = _normalized_host_role()
    if not host_role:
        _die("TAKYON_HOST_ROLE is required for `takyon migrate`; refusing ambiguous host context.")
    if host_role in {"subuser", "app", "product"}:
        _die(f"Refusing to run migrations on TAKYON_HOST_ROLE={host_role}.")

    try:
        migration_database_url = resolve_database_url(plane="migration")
        with psycopg.connect(migration_database_url, autocommit=True, prepare_threshold=None) as conn:
            assert_takyon_pg_role(conn, "migration")
            conn.execute("select set_config('statement_timeout', '0', false)")
            assert_migration_topology(conn)

            files = [path.name for path in migration_files()]
            if dry_run:
                print(f"migrations_dry_run count={len(files)}")
                for name in files:
                    print(name)
                print(f"schema_fingerprint={_schema_fingerprint(conn)}")
                return 0

            applied = run_migrations(conn)
            print(f"migrations_ok count={len(applied)} last={applied[-1] if applied else 'none'}")
            for name in applied:
                print(name)
            print(f"schema_fingerprint={_schema_fingerprint(conn)}")
    except Exception as exc:
        _die(f"takyon migrate failed: {exc}")
    return 0


def cmd_migrate_xai(args: Any) -> int:
    """Run xAI May-15 model migration in dry-run or apply mode."""
    from takyon_cli.xai_retirement import (
        MIGRATION_GUIDE_URL,
        RETIREMENT_DATE,
        apply_migration,
        find_retired_xai_refs,
        format_issue,
    )

    apply = bool(getattr(args, "apply", False))
    no_backup = bool(getattr(args, "no_backup", False))

    config = load_config()
    issues = find_retired_xai_refs(config)

    print()
    print(color(
        f"◆ xAI Model Retirement Migration ({RETIREMENT_DATE})",
        Colors.CYAN, Colors.BOLD,
    ))
    print()

    if not issues:
        print(f"  {color('✓', Colors.GREEN)} No retired xAI models in config — nothing to migrate.")
        return 0

    print(f"  Found {len(issues)} retired xAI model reference(s):")
    print()
    for issue in issues:
        print(f"    {color('⚠', Colors.YELLOW)} {format_issue(issue)}")
    print()
    print(f"    {color('→', Colors.CYAN)} Migration guide: {MIGRATION_GUIDE_URL}")
    print()

    config_path = _resolve_config_path()

    if not apply:
        print(color("Dry-run mode — no changes written.", Colors.DIM))
        print(color(
            "Re-run with `takyon migrate xai --apply` to rewrite "
            f"{config_path} in-place (backup created automatically).",
            Colors.DIM,
        ))
        return 0

    if not config_path or not config_path.exists():
        print(
            f"  {color('✗', Colors.RED)} Could not locate config.yaml "
            f"(looked at: {config_path})",
            file=sys.stderr,
        )
        return 1

    try:
        result = apply_migration(
            config_path=config_path,
            issues=issues,
            backup=not no_backup,
        )
    except Exception as exc:
        print(
            f"  {color('✗', Colors.RED)} Migration failed: {exc}",
            file=sys.stderr,
        )
        return 1

    if not result.config_changed:
        print(f"  {color('⚠', Colors.YELLOW)} No changes written.")
        return 0

    if result.backup_path is not None:
        print(f"  {color('✓', Colors.GREEN)} Backup: {result.backup_path}")
    print(
        f"  {color('✓', Colors.GREEN)} Updated {len(result.issues_resolved)} "
        f"slot(s) in {result.file_path}"
    )
    print()
    print(color(
        "Run `takyon doctor` to confirm no retired xAI models remain.",
        Colors.DIM,
    ))
    return 0


def _resolve_config_path() -> Path:
    """Best-effort: locate the active config.yaml on disk."""
    from takyon_cli.config import get_takyon_home

    return get_takyon_home() / "config.yaml"


def _normalized_host_role() -> str:
    raw = str(os.environ.get("TAKYON_HOST_ROLE") or "").strip().lower()
    aliases = {
        "dashboard": "operator",
        "app": "subuser",
        "product": "subuser",
    }
    return aliases.get(raw, raw)


def _schema_fingerprint(conn) -> str:
    rows = conn.execute(
        """
        select table_name,
               column_name,
               ordinal_position,
               data_type,
               udt_name,
               is_nullable,
               column_default
        from information_schema.columns
        where table_schema = 'public'
        order by table_name, ordinal_position, column_name
        """
    ).fetchall()
    payload = [
        {
            "table": _cell(row, 0),
            "column": _cell(row, 1),
            "ordinal": _cell(row, 2),
            "data_type": _cell(row, 3),
            "udt_name": _cell(row, 4),
            "nullable": _cell(row, 5),
            "default": _cell(row, 6),
        }
        for row in rows
    ]
    raw = json.dumps(payload, sort_keys=True, default=str, separators=(",", ":")).encode()
    return hashlib.sha256(raw).hexdigest()


def _cell(row, index: int):
    if isinstance(row, Mapping):
        return list(row.values())[index]
    return row[index]


def _die(message: str) -> None:
    print(message, file=sys.stderr)
    raise SystemExit(1)
