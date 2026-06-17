#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  deploy/shared/upsert-env-file.sh validate-file <env-file> <KEY> [<KEY> ...]
  deploy/shared/upsert-env-file.sh upsert-file <env-file> <KEY> [<KEY> ...]

Contract:
  - Keys must be env-style names such as UMAMI_API_KEY.
  - upsert-file reads values from the CURRENT process environment.
  - validate-file reads values from the CURRENT process environment first,
    then falls back to the target env file.
EOF
}

is_valid_key_name() {
  [[ "${1:-}" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]]
}

read_file_value() {
  local path="$1"
  local key="$2"
  if [[ ! -f "$path" ]]; then
    return 0
  fi
  awk -v key="$key" '
    /^[[:space:]]*#/ || index($0, "=") == 0 { next }
    {
      name = $0
      sub(/=.*/, "", name)
      gsub(/^[[:space:]]+|[[:space:]]+$/, "", name)
      if (name == key) {
        value = substr($0, index($0, "=") + 1)
        found = value
      }
    }
    END {
      if (found != "") {
        print found
      }
    }
  ' "$path"
}

validate_file() {
  local path="$1"
  shift
  local key value
  for key in "$@"; do
    if ! is_valid_key_name "$key"; then
      echo "Invalid environment variable name: $key" >&2
      exit 1
    fi
    value="${!key:-}"
    if [[ -z "$value" ]]; then
      value="$(read_file_value "$path" "$key")"
    fi
    if [[ -z "$value" ]]; then
      echo "Missing required key $key in current env or $path" >&2
      exit 1
    fi
  done
  echo "Validated $path"
}

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

upsert_file() {
  local path="$1"
  shift
  local key value
  for key in "$@"; do
    if ! is_valid_key_name "$key"; then
      echo "Invalid environment variable name: $key" >&2
      exit 1
    fi
    value="${!key:-}"
    if [[ -z "$value" ]]; then
      echo "Set $key in the current environment before running upsert-file." >&2
      exit 1
    fi
    upsert_one_key "$path" "$key" "$value"
  done
  validate_file "$path" "$@"
  echo "Updated $path"
}

if [[ $# -lt 3 ]]; then
  usage >&2
  exit 1
fi

command_name="$1"
target_file="$2"
shift 2

case "$command_name" in
  validate-file)
    validate_file "$target_file" "$@"
    ;;
  upsert-file)
    upsert_file "$target_file" "$@"
    ;;
  *)
    usage >&2
    exit 1
    ;;
esac
