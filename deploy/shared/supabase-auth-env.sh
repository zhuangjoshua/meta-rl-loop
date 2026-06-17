#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  deploy/shared/supabase-auth-env.sh validate-file <env-file> [<env-file> ...]
  deploy/shared/supabase-auth-env.sh upsert-file <env-file>

Contract:
  Required:
    - SUPABASE_URL
    - one of SUPABASE_PUBLISHABLE_KEY or SUPABASE_ANON_KEY
  Optional:
    - SUPABASE_JWT_SECRET (legacy HS256 fallback only)

Notes:
  - `upsert-file` reads values from the CURRENT process environment and writes them
    idempotently into the target env file.
  - `validate-file` reads from the current process environment first, then the listed
    env files in order.
EOF
}

if [[ $# -lt 1 ]]; then
  usage >&2
  exit 1
fi

command_name="$1"
shift

python3 - "$command_name" "$@" <<'PY'
from __future__ import annotations

import os
import stat
import sys
from pathlib import Path


REQUIRED_KEYS = ("SUPABASE_URL",)
OPTIONAL_KEYS = ("SUPABASE_JWT_SECRET",)
PUBLIC_KEY_CHOICES = ("SUPABASE_PUBLISHABLE_KEY", "SUPABASE_ANON_KEY")


def _strip_wrapping_quotes(value: str) -> str:
    text = value.strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in {"'", '"'}:
        return text[1:-1]
    return text


def _load_env_files(paths: list[str]) -> dict[str, str]:
    merged: dict[str, str] = {}
    for raw_path in paths:
        path = Path(raw_path).expanduser()
        if not path.exists():
            continue
        for raw_line in path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            if not key:
                continue
            merged[key] = _strip_wrapping_quotes(value)
    return merged


def _resolve(key: str, file_values: dict[str, str]) -> str:
    direct = str(os.environ.get(key) or "").strip()
    if direct:
        return direct
    return str(file_values.get(key) or "").strip()


def _validate(file_paths: list[str]) -> None:
    file_values = _load_env_files(file_paths)
    missing: list[str] = []
    for key in REQUIRED_KEYS:
        if not _resolve(key, file_values):
            missing.append(key)
    if not any(_resolve(key, file_values) for key in PUBLIC_KEY_CHOICES):
        missing.append("SUPABASE_PUBLISHABLE_KEY|SUPABASE_ANON_KEY")
    if missing:
        joined = ", ".join(missing)
        where = ", ".join(file_paths) if file_paths else "<current env>"
        raise SystemExit(
            "Supabase auth config missing required key(s): "
            f"{joined}. Checked current env first, then: {where}"
        )
    public_key_name = next(
        key for key in PUBLIC_KEY_CHOICES if _resolve(key, file_values)
    )
    print(
        "Validated Supabase auth config "
        f"(SUPABASE_URL, {public_key_name})"
    )


def _upsert_file(target_path: str) -> None:
    path = Path(target_path).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        lines = path.read_text(encoding="utf-8").splitlines()
    else:
        lines = []
    values: dict[str, str] = {}
    for key in (*REQUIRED_KEYS, *OPTIONAL_KEYS, *PUBLIC_KEY_CHOICES):
        raw = str(os.environ.get(key) or "").strip()
        if raw:
            values[key] = raw
    if "SUPABASE_URL" not in values:
        raise SystemExit("upsert-file requires SUPABASE_URL in the current environment")
    if not any(key in values for key in PUBLIC_KEY_CHOICES):
        raise SystemExit(
            "upsert-file requires SUPABASE_PUBLISHABLE_KEY or SUPABASE_ANON_KEY in the current environment"
        )

    seen: set[str] = set()
    next_lines: list[str] = []
    for raw_line in lines:
        if "=" not in raw_line or raw_line.lstrip().startswith("#"):
            next_lines.append(raw_line)
            continue
        key, _value = raw_line.split("=", 1)
        normalized = key.strip()
        if normalized in values:
            next_lines.append(f"{normalized}={values[normalized]}")
            seen.add(normalized)
        else:
            next_lines.append(raw_line)
    for key in (*REQUIRED_KEYS, *OPTIONAL_KEYS, *PUBLIC_KEY_CHOICES):
        if key in values and key not in seen:
            next_lines.append(f"{key}={values[key]}")

    rendered = "\n".join(next_lines).rstrip() + "\n"
    path.write_text(rendered, encoding="utf-8")
    current_mode = stat.S_IMODE(path.stat().st_mode)
    if current_mode & 0o077:
        path.chmod(0o600)
    _validate([str(path)])
    print(f"Updated {path}")


def main(argv: list[str]) -> int:
    if not argv:
        usage()
        return 1
    command = argv[0]
    if command == "validate-file":
        if len(argv) < 2:
            raise SystemExit("validate-file requires at least one env file path")
        _validate(argv[1:])
        return 0
    if command == "upsert-file":
        if len(argv) != 2:
            raise SystemExit("upsert-file requires exactly one env file path")
        _upsert_file(argv[1])
        return 0
    usage()
    return 1


def usage() -> None:
    sys.stderr.write("unknown or missing command\n")


raise SystemExit(main(sys.argv[1:]))
PY
