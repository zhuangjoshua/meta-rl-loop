#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  deploy/shared/validate-authority-env.sh <operator|subuser|safebox> [env-file ...]

Validates authority-bearing env names by presence only. It never prints secret
values. Values are read from the current process environment first, then from
the supplied env files.

The operator migration DSN is root-only and is always forbidden in these
service-readable files. TAKYON_REQUIRE_MIGRATION_DATABASE_URL applies only to
legacy subuser deploy modes; production subuser replicas set it to 0.

Set TAKYON_REQUIRE_APP_DATABASE_URL=0 only for a subuser host whose app DSN
resolves through the safebox authority at runtime (no local DSN by design —
the takyon-subuser-2 replica posture). A local DSN remains the default
requirement, and the operator/safebox rejects on that key are unaffected.
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
      sub(/^export[[:space:]]+/, "", name)
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

env_truthy() {
  case "${1:-}" in
    1|true|TRUE|yes|YES|on|ON)
      return 0
      ;;
    *)
      return 1
      ;;
  esac
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

reject_stripe_authority() {
  reject_key STRIPE_SECRET_KEY "$@"
  reject_key STRIPE_SANDBOX_SECRET_KEY "$@"
  reject_key STRIPE_WEBHOOK_SECRET "$@"
  reject_key STRIPE_BILLING_WEBHOOK_SECRET "$@"
  reject_key TAKYON_MANAGED_SECRET_COMMAND "$@"
  reject_key TAKYON_MANAGED_SECRET_KEYS "$@"
  reject_key DOPPLER_TOKEN "$@"
}

env_files=("$@")
reject_legacy_database_urls "${env_files[@]}"
require_migration_database_url="${TAKYON_REQUIRE_MIGRATION_DATABASE_URL:-1}"

case "$plane" in
  operator)
    reject_stripe_authority "${env_files[@]}"
    require_key TAKYON_OPERATOR_DATABASE_URL "${env_files[@]}"
    reject_key TAKYON_MIGRATION_DATABASE_URL "${env_files[@]}"
    require_key TAKYON_SAFEBOX_TOKEN "${env_files[@]}"
    require_key TAKYON_SAFEBOX_OPERATOR_TOKEN "${env_files[@]}"
    reject_key TAKYON_APP_DATABASE_URL "${env_files[@]}"
    reject_key TAKYON_SAFEBOX_DATABASE_URL "${env_files[@]}"
    ;;
  subuser)
    reject_stripe_authority "${env_files[@]}"
    if env_truthy "${TAKYON_REQUIRE_APP_DATABASE_URL:-1}"; then
      require_key TAKYON_APP_DATABASE_URL "${env_files[@]}"
    fi
    if env_truthy "$require_migration_database_url"; then
      require_key TAKYON_MIGRATION_DATABASE_URL "${env_files[@]}"
    fi
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
    reject_key TAKYON_MIGRATION_DATABASE_URL "${env_files[@]}"
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
