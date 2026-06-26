#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  env DOPPLER_PROJECT=takyon DOPPLER_CONFIG=prd \
    bash deploy/shared/migrate-safebox-env-key-to-doppler.sh STRIPE_SECRET_KEY

Run this on the Safebox host after `doppler login` has been completed for the
service config dir and scope. It migrates exactly one key at a time from the
Safebox env files to Doppler, verifies the value without printing it, removes
the plaintext env entry, updates TAKYON_MANAGED_SECRET_KEYS, and restarts
takyon-safebox.

This helper refuses to migrate Safebox self-authority secrets such as
TAKYON_SAFEBOX_TOKEN and TAKYON_CAP_SIGNING_KEY.
EOF
}

if [[ $# -ne 1 || "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  usage >&2
  exit 1
fi

KEY_NAME="$1"
PROJECT="${DOPPLER_PROJECT:-takyon}"
CONFIG="${DOPPLER_CONFIG:-prd}"
DOPPLER_CONFIG_DIR="${DOPPLER_CONFIG_DIR:-/opt/takyon/.doppler}"
DOPPLER_SCOPE="${DOPPLER_SCOPE:-/opt/takyon}"
SAFEBOX_RUNTIME="${TAKYON_REMOTE_RUNTIME:-/opt/takyon/hermes-agent-main}"
PYTHON_BIN="${TAKYON_SAFEBOX_PYTHON:-$SAFEBOX_RUNTIME/.venv/bin/python}"
ENV_FILES="${TAKYON_SAFEBOX_ENV_FILES:-/opt/takyon/.takyon/.env /opt/takyon/secrets/.env}"
CONTROL_ENV_FILE="${TAKYON_SAFEBOX_CONTROL_ENV_FILE:-/opt/takyon/secrets/.env}"
SERVICE_NAME="${TAKYON_SAFEBOX_SERVICE_NAME:-takyon-safebox.service}"
RESTART_SERVICE="${TAKYON_RESTART_SAFEBOX:-1}"

if [[ ! "$KEY_NAME" =~ ^[A-Z_][A-Z0-9_]*$ ]]; then
  echo "Invalid key name: $KEY_NAME" >&2
  exit 1
fi

case "$KEY_NAME" in
  TAKYON_SAFEBOX_TOKEN|TAKYON_CAP_SIGNING_KEY)
    echo "Refusing to migrate Safebox self-authority secret $KEY_NAME" >&2
    exit 1
    ;;
esac

if [[ ! -x "$PYTHON_BIN" ]]; then
  echo "Python runtime not found or not executable: $PYTHON_BIN" >&2
  exit 1
fi

if ! command -v doppler >/dev/null 2>&1; then
  echo "Doppler CLI is not installed; run deploy/shared/ensure-doppler-cli.sh first." >&2
  exit 1
fi

"$PYTHON_BIN" - "$KEY_NAME" "$PROJECT" "$CONFIG" "$DOPPLER_CONFIG_DIR" "$DOPPLER_SCOPE" "$CONTROL_ENV_FILE" "$ENV_FILES" <<'PY'
from __future__ import annotations

import hashlib
import os
import re
import subprocess
import sys
from pathlib import Path

try:
    from dotenv import dotenv_values
except Exception:  # pragma: no cover - deploy-host dependency guard
    dotenv_values = None


key, project, config, doppler_config_dir, doppler_scope, control_env_file, env_files_raw = sys.argv[1:8]
env_files = [Path(item) for item in env_files_raw.split() if item.strip()]
control_path = Path(control_env_file)
name_pattern = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=")


def read_env_values(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    if dotenv_values is not None:
        return {str(k): str(v or "") for k, v in dotenv_values(path).items() if k}
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        match = name_pattern.match(line)
        if not match:
            continue
        name = match.group(1)
        value = line.split("=", 1)[1].strip()
        if (value.startswith('"') and value.endswith('"')) or (
            value.startswith("'") and value.endswith("'")
        ):
            value = value[1:-1]
        values[name] = value
    return values


def upsert_env(path: Path, updates: dict[str, str], remove: set[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    written: set[str] = set()
    output: list[str] = []
    for line in lines:
        match = name_pattern.match(line)
        if not match:
            output.append(line)
            continue
        name = match.group(1)
        if name in remove:
            continue
        if name in updates:
            output.append(f"{name}={updates[name]}")
            written.add(name)
            continue
        output.append(line)
    for name, value in updates.items():
        if name not in written:
            output.append(f"{name}={value}")
    tmp = path.with_name(f"{path.name}.tmp.{os.getpid()}")
    tmp.write_text("\n".join(output).rstrip() + "\n", encoding="utf-8")
    tmp.chmod(0o600)
    tmp.replace(path)


merged: dict[str, str] = {}
for path in env_files:
    merged.update({k: v for k, v in read_env_values(path).items() if v})
value = str(merged.get(key) or "")
if not value:
    raise SystemExit(f"{key} was not found in the configured Safebox env files")

managed_keys: set[str] = set()
for path in env_files:
    raw = read_env_values(path).get("TAKYON_MANAGED_SECRET_KEYS", "")
    managed_keys.update(item.strip() for item in raw.replace(",", " ").split() if item.strip())
managed_keys.add(key)

command = (
    f"doppler --config-dir {doppler_config_dir} --scope {doppler_scope} secrets get {{key}} "
    f"--plain --raw --project {project} --config {config}"
)

set_result = subprocess.run(
    [
        "doppler",
        "--config-dir",
        doppler_config_dir,
        "--scope",
        doppler_scope,
        "secrets",
        "set",
        key,
        "--project",
        project,
        "--config",
        config,
        "--no-interactive",
    ],
    input=value,
    text=True,
    capture_output=True,
)
if set_result.returncode != 0:
    raise SystemExit(f"doppler failed to set {key}: {set_result.stderr.strip() or 'unknown error'}")

get_result = subprocess.run(
    [
        "doppler",
        "--config-dir",
        doppler_config_dir,
        "--scope",
        doppler_scope,
        "secrets",
        "get",
        key,
        "--plain",
        "--raw",
        "--project",
        project,
        "--config",
        config,
    ],
    text=True,
    capture_output=True,
)
if get_result.returncode != 0:
    raise SystemExit(f"doppler failed to read back {key}: {get_result.stderr.strip() or 'unknown error'}")

source_hash = hashlib.sha256(value.encode("utf-8")).hexdigest()
managed_value = get_result.stdout.rstrip("\n")
managed_hash = hashlib.sha256(managed_value.encode("utf-8")).hexdigest()
if source_hash != managed_hash:
    raise SystemExit(f"doppler read-back hash mismatch for {key}; plaintext env files were not changed")

for path in env_files:
    upsert_env(path, {}, {key})
upsert_env(
    control_path,
    {
        "TAKYON_MANAGED_SECRET_COMMAND": command,
        "TAKYON_MANAGED_SECRET_KEYS": ",".join(sorted(managed_keys)),
    },
    set(),
)

print(f"Migrated {key} to Doppler and removed plaintext env entries")
PY

if id -u takyon >/dev/null 2>&1; then
  chown -R takyon:takyon "$DOPPLER_CONFIG_DIR" || true
  for path in $ENV_FILES "$CONTROL_ENV_FILE"; do
    if [[ -e "$path" ]]; then
      chown takyon:takyon "$path" || true
    fi
  done
fi

if [[ "$RESTART_SERVICE" == "1" ]]; then
  systemctl daemon-reload
  systemctl restart "$SERVICE_NAME"
  systemctl is-active --quiet "$SERVICE_NAME"
  for _ in $(seq 1 30); do
    if curl -fsS http://10.116.0.2:8000/healthz >/dev/null; then
      echo "Safebox healthy after migrating $KEY_NAME"
      exit 0
    fi
    sleep 1
  done
  curl -fsS http://10.116.0.2:8000/healthz >/dev/null
fi
