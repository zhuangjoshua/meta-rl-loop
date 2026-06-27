#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  deploy/shared/validate-authority-env.sh <operator|subuser|safebox> [env-file ...]

Validates authority-bearing env names by presence only. It never prints secret
values. Values are read from the current process environment first, then from
the supplied env files.
EOF
}

if [[ $# -lt 1 ]]; then
  usage >&2
  exit 1
fi

plane="$1"
shift

if [[ $# -eq 0 ]]; then
  set -- /opt/takyon/.takyon/.env /opt/takyon/secrets/.env
fi

errors=0

record_error() {
  echo "$1" >&2
  errors=1
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

has_key() {
  local key="$1"
  local path value
  if ! is_valid_key_name "$key"; then
    echo "invalid environment variable name: $key" >&2
    exit 1
  fi
  value="${!key:-}"
  if [[ -n "$value" ]]; then
    return 0
  fi
  for path in "$@"; do
    if [[ "$path" == "$key" ]]; then
      continue
    fi
    value="$(read_file_value "$path" "$key")"
    if [[ -n "$value" ]]; then
      return 0
    fi
  done
  return 1
}

require_key() {
  local key="$1"
  if ! has_key "$key" "$@"; then
    record_error "missing required authority env: $key"
  fi
}

require_one_of() {
  local label="$1"
  shift
  local key
  for key in "$@"; do
    if has_key "$key" "${env_files[@]}"; then
      return 0
    fi
  done
  record_error "missing required authority env: $label"
}

reject_key() {
  local key="$1"
  if has_key "$key" "$@"; then
    record_error "forbidden authority env present on $plane host: $key"
  fi
}

reject_legacy_database_urls() {
  reject_key DATABASE_URL "$@"
  reject_key POSTGRES_URL "$@"
  reject_key POSTGRES_PRISMA_URL "$@"
  reject_key POSTGRES_URL_NON_POOLING "$@"
  reject_key MIGRATION_DATABASE_URL "$@"
  reject_key TAKYON_RUNTIME_DATABASE_URL "$@"
}

env_files=("$@")
reject_legacy_database_urls "${env_files[@]}"

case "$plane" in
  operator)
    require_key TAKYON_OPERATOR_DATABASE_URL "${env_files[@]}"
    require_key TAKYON_MIGRATION_DATABASE_URL "${env_files[@]}"
    require_key TAKYON_SAFEBOX_TOKEN "${env_files[@]}"
    require_key TAKYON_SAFEBOX_OPERATOR_TOKEN "${env_files[@]}"
    reject_key TAKYON_APP_DATABASE_URL "${env_files[@]}"
    reject_key TAKYON_SAFEBOX_DATABASE_URL "${env_files[@]}"
    ;;
  subuser)
    require_key TAKYON_APP_DATABASE_URL "${env_files[@]}"
    require_key TAKYON_MIGRATION_DATABASE_URL "${env_files[@]}"
    require_key TAKYON_SAFEBOX_TOKEN "${env_files[@]}"
    reject_key TAKYON_OPERATOR_DATABASE_URL "${env_files[@]}"
    reject_key TAKYON_SAFEBOX_DATABASE_URL "${env_files[@]}"
    reject_key TAKYON_SAFEBOX_OPERATOR_TOKEN "${env_files[@]}"
    ;;
  safebox)
    require_key TAKYON_SAFEBOX_DATABASE_URL "${env_files[@]}"
    require_key TAKYON_SAFEBOX_TOKEN "${env_files[@]}"
    require_key TAKYON_SAFEBOX_OPERATOR_TOKEN "${env_files[@]}"
    reject_key TAKYON_OPERATOR_DATABASE_URL "${env_files[@]}"
    reject_key TAKYON_APP_DATABASE_URL "${env_files[@]}"
    ;;
  *)
    usage >&2
    exit 1
    ;;
esac

if [[ "$errors" -ne 0 ]]; then
  exit 1
fi

echo "Validated authority env for $plane"
