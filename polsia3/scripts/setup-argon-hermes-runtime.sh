#!/usr/bin/env bash
set -euo pipefail

ROOT="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
RUNTIME_DIR="${ARGON_RUNTIME_DIR:-$ROOT/vendor/argon-hermes-runtime}"
VENV_DIR="${ARGON_RUNTIME_VENV:-$RUNTIME_DIR/.venv}"
HERMES_HOME_DIR="${HERMES_HOME:-$ROOT/.argon-hermes-home}"
export HERMES_HOME="$HERMES_HOME_DIR"

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

if [ ! -f "$RUNTIME_DIR/pyproject.toml" ]; then
  echo "Missing vendored runtime at $RUNTIME_DIR" >&2
  exit 1
fi

choose_python() {
  if [ -n "${PYTHON:-}" ]; then
    printf '%s\n' "$PYTHON"
    return 0
  fi
  for candidate in python3.13 python3.12 python3.11 python3; do
    if command -v "$candidate" >/dev/null 2>&1; then
      if "$candidate" - <<'PY' >/dev/null 2>&1
import sys
raise SystemExit(0 if sys.version_info >= (3, 11) else 1)
PY
      then
        command -v "$candidate"
        return 0
      fi
    fi
  done
  return 1
}

PYTHON_BIN="$(choose_python || true)"
if [ -z "$PYTHON_BIN" ]; then
  echo "Hermes runtime requires Python >= 3.11. Install one, for example: brew install python@3.12" >&2
  exit 1
fi

if [ -x "$VENV_DIR/bin/python" ] && ! "$VENV_DIR/bin/python" - <<'PY' >/dev/null 2>&1
import sys
raise SystemExit(0 if sys.version_info >= (3, 11) else 1)
PY
then
  rm -rf "$VENV_DIR"
fi

"$PYTHON_BIN" -m venv "$VENV_DIR"
"$VENV_DIR/bin/python" -m pip install --upgrade pip
"$VENV_DIR/bin/python" -m pip install -e "$RUNTIME_DIR" "aiohttp>=3.13.3,<4"

mkdir -p "$HERMES_HOME_DIR"

MODEL="${ARGON_RUNTIME_MODEL:-${ARGON_CEO_MODEL:-claude-sonnet-4-20250514}}"
if [ -n "${ANTHROPIC_API_KEY:-}" ]; then
  PROVIDER="${ARGON_INFERENCE_PROVIDER:-anthropic}"
elif [ -n "${OPENAI_API_KEY:-}" ]; then
  PROVIDER="${ARGON_INFERENCE_PROVIDER:-openai}"
else
  PROVIDER="${ARGON_INFERENCE_PROVIDER:-auto}"
fi

cat > "$HERMES_HOME_DIR/config.yaml" <<YAML
model:
  provider: "$PROVIDER"
  default: "$MODEL"
agent:
  max_turns: ${HERMES_MAX_ITERATIONS:-40}
memory:
  memory_enabled: false
  user_profile_enabled: false
kanban:
  dispatch_in_gateway: false
platform_toolsets:
  api_server: [web, skills, todo, files]
YAML

(cd "$ROOT" && node scripts/sync-argon-hermes-skills.mjs)

cat <<EOF
Argon/Hermes runtime is installed.

Runtime dir: $RUNTIME_DIR
Hermes home: $HERMES_HOME_DIR

Start it with:
  scripts/start-argon-hermes-runtime.sh
EOF
