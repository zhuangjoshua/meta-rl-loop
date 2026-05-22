#!/usr/bin/env bash
set -euo pipefail

ROOT="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
RUNTIME_DIR="${ARGON_RUNTIME_DIR:-$ROOT/vendor/argon-hermes-runtime}"
VENV_DIR="${ARGON_RUNTIME_VENV:-$RUNTIME_DIR/.venv}"
export HERMES_HOME="${HERMES_HOME:-$ROOT/.argon-hermes-home}"

load_env_file() {
  local file="$1"
  [ -f "$file" ] || return 0
  while IFS= read -r line || [ -n "$line" ]; do
    case "$line" in
      ""|\#*) continue ;;
    esac
    case "$line" in
      *=*)
        local key="${line%%=*}"
        local value="${line#*=}"
        key="$(printf '%s' "$key" | tr -d '[:space:]')"
        value="${value%\"}"
        value="${value#\"}"
        value="${value%\'}"
        value="${value#\'}"
        if [ -n "$key" ] && [ -z "${!key:-}" ]; then
          export "$key=$value"
        fi
        ;;
    esac
  done < "$file"
}

load_env_file "$ROOT/.env"
load_env_file "$ROOT/.env.local"

if [ ! -x "$VENV_DIR/bin/python" ]; then
  echo "Runtime venv is missing. Run scripts/setup-argon-hermes-runtime.sh first." >&2
  exit 1
fi

export API_SERVER_ENABLED="${API_SERVER_ENABLED:-true}"
export API_SERVER_HOST="${API_SERVER_HOST:-127.0.0.1}"
export API_SERVER_PORT="${API_SERVER_PORT:-8642}"
export API_SERVER_KEY="${API_SERVER_KEY:-${ARGON_RUNTIME_API_KEY:-}}"
export API_SERVER_CORS_ORIGINS="${API_SERVER_CORS_ORIGINS:-http://localhost:3000,http://127.0.0.1:3000,http://localhost:3055,http://127.0.0.1:3055}"
export HERMES_API_SERVER_SKIP_MEMORY="${HERMES_API_SERVER_SKIP_MEMORY:-true}"
export HERMES_API_SERVER_SKIP_CONTEXT_FILES="${HERMES_API_SERVER_SKIP_CONTEXT_FILES:-true}"
export HERMES_MAX_ITERATIONS="${HERMES_MAX_ITERATIONS:-40}"
export HERMES_KANBAN_DISPATCH_IN_GATEWAY="${HERMES_KANBAN_DISPATCH_IN_GATEWAY:-false}"

if [ -n "${ARGON_RUNTIME_MODEL:-}" ]; then
  export ARGON_INFERENCE_MODEL="$ARGON_RUNTIME_MODEL"
fi

exec "$VENV_DIR/bin/python" "$RUNTIME_DIR/argon" gateway run --replace
