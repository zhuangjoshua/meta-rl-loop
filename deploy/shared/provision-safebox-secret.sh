#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
UPSERT_SCRIPT="$ROOT_DIR/deploy/shared/upsert-env-file.sh"

LOCAL_WORKSPACE_SECRETS="${TAKYON_LOCAL_WORKSPACE_SECRETS:-$ROOT_DIR/secrets/.env}"
LOCAL_WORKSPACE_HOME_ENV="${TAKYON_LOCAL_WORKSPACE_HOME_ENV:-$ROOT_DIR/.takyon/.env}"
LOCAL_DEV_ROOT="${TAKYON_LOCAL_DEV_ROOT:-$HOME/.takyon-fourmanifold-local-dev}"
LOCAL_DEV_SAFEBOX_ENV="${TAKYON_LOCAL_DEV_SAFEBOX_ENV:-$LOCAL_DEV_ROOT/safebox/.env}"

TAKYON_VPS_HOST="${TAKYON_VPS_HOST:-root@137.184.75.57}"
TAKYON_VPS_KEY="${TAKYON_VPS_KEY:-$HOME/.ssh/takyon_argon_alpha14}"
TAKYON_REMOTE_HOME="${TAKYON_REMOTE_HOME:-/opt/takyon/.takyon}"
TAKYON_REMOTE_RUNTIME="${TAKYON_REMOTE_RUNTIME:-/opt/takyon/hermes-agent-main}"
TAKYON_REMOTE_OPERATOR_SECRETS="${TAKYON_REMOTE_OPERATOR_SECRETS:-/opt/takyon/secrets/.env}"
TAKYON_REMOTE_SAFEBOX_URL="${TAKYON_REMOTE_SAFEBOX_URL:-http://10.116.0.2:8000}"

TAKYON_SAFEBOX_VPS_HOST="${TAKYON_SAFEBOX_VPS_HOST:-root@67.205.158.170}"
TAKYON_SAFEBOX_VPS_KEY="${TAKYON_SAFEBOX_VPS_KEY:-$HOME/.ssh/takyon_argon_alpha14}"
TAKYON_SAFEBOX_REMOTE_HOME="${TAKYON_SAFEBOX_REMOTE_HOME:-/opt/takyon/.takyon}"
TAKYON_SAFEBOX_REMOTE_SECRETS="${TAKYON_SAFEBOX_REMOTE_SECRETS:-/opt/takyon/secrets/.env}"

usage() {
  cat <<'EOF'
Usage:
  export UMAMI_API_KEY=...
  deploy/shared/provision-safebox-secret.sh UMAMI_API_KEY

What it does:
  1. Upserts the key into the local workspace secret files.
  2. Upserts the key into the local-dev Safebox secret file.
  3. Upserts the key into BOTH operator-host env files.
  4. Upserts the key into BOTH Safebox-host env files.
  5. Refreshes the live remote Safebox authority via takyon-cli secret set.

Notes:
  - The secret value is read from the current shell environment.
  - This is a provision/rotation helper, not a git-tracked secret store.
EOF
}

is_valid_key_name() {
  [[ "${1:-}" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]]
}

encode_base64() {
  printf '%s' "$1" | base64 | tr -d '\n'
}

remote_upsert_files() {
  local host="$1"
  local key_file="$2"
  local key_name="$3"
  shift 3
  local remote_files=("$@")
  local encoded_value
  local remote_file_args=()
  local file

  encoded_value="$(encode_base64 "${!key_name}")"

  for file in "${remote_files[@]}"; do
    remote_file_args+=("'$file'")
  done

  ssh -i "$key_file" -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new "$host" \
    "env KEY_NAME='$key_name' VALUE_B64='$encoded_value' bash -s -- ${remote_file_args[*]}" \
    <<'EOF'
decode_value() {
  if printf '' | base64 --decode >/dev/null 2>&1; then
    printf '%s' "$VALUE_B64" | base64 --decode
    return
  fi
  if printf '' | base64 -d >/dev/null 2>&1; then
    printf '%s' "$VALUE_B64" | base64 -d
    return
  fi
  printf '%s' "$VALUE_B64" | base64 -D
}

set -euo pipefail

upsert_one_key() {
  local path="$1"
  local key="$2"
  local value="$3"
  local dir tmp

  dir="$(dirname "$path")"
  mkdir -p "$dir"
  touch "$path"
  chmod 600 "$path" || true

  tmp="$(mktemp "$dir/.env.tmp.XXXXXX")"
  awk -v key="$key" -v value="$value" '
    BEGIN { written = 0 }
    /^[[:space:]]*#/ || index($0, "=") == 0 {
      print
      next
    }
    {
      name = $0
      sub(/=.*/, "", name)
      gsub(/^[[:space:]]+|[[:space:]]+$/, "", name)
      if (name == key) {
        if (!written) {
          print key "=" value
          written = 1
        }
        next
      }
      print
    }
    END {
      if (!written) {
        print key "=" value
      }
    }
  ' "$path" > "$tmp"
  mv "$tmp" "$path"
  chmod 600 "$path" || true
}

decoded_value="$(decode_value)"
export "$KEY_NAME=$decoded_value"
for target_file in "$@"; do
  upsert_one_key "$target_file" "$KEY_NAME" "$decoded_value"
  if id -u takyon >/dev/null 2>&1; then
    chown takyon:takyon "$target_file" || true
  fi
  echo "Updated $target_file"
done
EOF
}

refresh_live_remote_safebox() {
  local key_name="$1"
  local encoded_value

  encoded_value="$(encode_base64 "${!key_name}")"

  ssh -i "$TAKYON_VPS_KEY" -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new "$TAKYON_VPS_HOST" \
    "env KEY_NAME='$key_name' VALUE_B64='$encoded_value' REMOTE_HOME='$TAKYON_REMOTE_HOME' REMOTE_RUNTIME='$TAKYON_REMOTE_RUNTIME' REMOTE_SAFEBOX_URL='$TAKYON_REMOTE_SAFEBOX_URL' REMOTE_OPERATOR_SECRETS='$TAKYON_REMOTE_OPERATOR_SECRETS' bash -s" <<'EOF'
set -euo pipefail

decode_value() {
  if printf '' | base64 --decode >/dev/null 2>&1; then
    printf '%s' "$VALUE_B64" | base64 --decode
    return
  fi
  if printf '' | base64 -d >/dev/null 2>&1; then
    printf '%s' "$VALUE_B64" | base64 -d
    return
  fi
  printf '%s' "$VALUE_B64" | base64 -D
}

decoded_value="$(decode_value)"
env \
  TAKYON_HOME="$REMOTE_HOME" \
  TAKYON_HOST_ROLE=operator \
  TAKYON_SAFEBOX_URL="$REMOTE_SAFEBOX_URL" \
  "$REMOTE_RUNTIME/.venv/bin/takyon-cli" takyon secret set "$KEY_NAME" "$decoded_value"
EOF
}

if [[ $# -ne 1 ]]; then
  usage >&2
  exit 1
fi

key_name="$1"
if ! is_valid_key_name "$key_name"; then
  echo "Invalid environment variable name: $key_name" >&2
  exit 1
fi

if [[ ! -x "$UPSERT_SCRIPT" ]]; then
  echo "Missing helper script: $UPSERT_SCRIPT" >&2
  exit 1
fi

if [[ -z "${!key_name:-}" ]]; then
  echo "Set $key_name in the current shell before running this script." >&2
  exit 1
fi

if [[ ! -f "$TAKYON_VPS_KEY" ]]; then
  echo "Operator deploy key not found: $TAKYON_VPS_KEY" >&2
  exit 1
fi

if [[ ! -f "$TAKYON_SAFEBOX_VPS_KEY" ]]; then
  echo "Safebox deploy key not found: $TAKYON_SAFEBOX_VPS_KEY" >&2
  exit 1
fi

"$UPSERT_SCRIPT" upsert-file "$LOCAL_WORKSPACE_SECRETS" "$key_name"
"$UPSERT_SCRIPT" upsert-file "$LOCAL_WORKSPACE_HOME_ENV" "$key_name"
"$UPSERT_SCRIPT" upsert-file "$LOCAL_DEV_SAFEBOX_ENV" "$key_name"

remote_upsert_files \
  "$TAKYON_VPS_HOST" \
  "$TAKYON_VPS_KEY" \
  "$key_name" \
  "$TAKYON_REMOTE_OPERATOR_SECRETS" \
  "$TAKYON_REMOTE_HOME/.env"

remote_upsert_files \
  "$TAKYON_SAFEBOX_VPS_HOST" \
  "$TAKYON_SAFEBOX_VPS_KEY" \
  "$key_name" \
  "$TAKYON_SAFEBOX_REMOTE_SECRETS" \
  "$TAKYON_SAFEBOX_REMOTE_HOME/.env"

refresh_live_remote_safebox "$key_name"

echo "Provisioned $key_name across local files, operator source files, Safebox source files, and the live remote Safebox authority."
