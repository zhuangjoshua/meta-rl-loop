#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
RUNTIME_DIR="$ROOT_DIR/hermes-agent-main"
BOOTSTRAP_SCRIPT="$ROOT_DIR/deploy/argon-alpha-14/bootstrap-host.sh"
SERVICE_FILE="$ROOT_DIR/deploy/argon-alpha-14/takyon-dashboard.service"
WORKER_SERVICE_FILE="$ROOT_DIR/deploy/argon-alpha-14/takyon-worker.service"

TAKYON_VPS_HOST="${TAKYON_VPS_HOST:-root@137.184.75.57}"
TAKYON_VPS_KEY="${TAKYON_VPS_KEY:-$HOME/.ssh/takyon_argon_alpha14}"
TAKYON_REMOTE_RUNTIME="${TAKYON_REMOTE_RUNTIME:-/opt/takyon/hermes-agent-main}"
TAKYON_REMOTE_SERVICE_FILE="${TAKYON_REMOTE_SERVICE_FILE:-/etc/systemd/system/takyon-dashboard.service}"
TAKYON_REMOTE_WORKER_SERVICE_FILE="${TAKYON_REMOTE_WORKER_SERVICE_FILE:-/etc/systemd/system/takyon-worker.service}"
TAKYON_RUN_WEB_BUILD="${TAKYON_RUN_WEB_BUILD:-1}"
TAKYON_BOOTSTRAP_HOST="${TAKYON_BOOTSTRAP_HOST:-1}"
TAKYON_APPLY_CADDY="${TAKYON_APPLY_CADDY:-0}"
TAKYON_SMOKE_HOST="${TAKYON_SMOKE_HOST:-https://app.fourmanifold.com/}"
TAKYON_SMOKE_HOST_HEADER="${TAKYON_SMOKE_HOST_HEADER:-}"
TAKYON_SMOKE_CONNECT_TIMEOUT="${TAKYON_SMOKE_CONNECT_TIMEOUT:-5}"
TAKYON_SMOKE_MAX_TIME="${TAKYON_SMOKE_MAX_TIME:-10}"
TAKYON_CLAUDE_AGENT_DOCKER_IMAGE="${TAKYON_CLAUDE_AGENT_DOCKER_IMAGE:-${TERMINAL_DOCKER_IMAGE:-nikolaik/python-nodejs:python3.11-nodejs20}}"

if [[ ! -d "$RUNTIME_DIR" ]]; then
  echo "runtime directory not found: $RUNTIME_DIR" >&2
  exit 1
fi

if [[ ! -f "$BOOTSTRAP_SCRIPT" ]]; then
  echo "bootstrap script not found: $BOOTSTRAP_SCRIPT" >&2
  exit 1
fi

if [[ ! -f "$SERVICE_FILE" ]]; then
  echo "service file not found: $SERVICE_FILE" >&2
  exit 1
fi

if [[ ! -f "$WORKER_SERVICE_FILE" ]]; then
  echo "worker service file not found: $WORKER_SERVICE_FILE" >&2
  exit 1
fi

if [[ ! -f "$TAKYON_VPS_KEY" ]]; then
  echo "deploy key not found: $TAKYON_VPS_KEY" >&2
  exit 1
fi

if [[ "$TAKYON_BOOTSTRAP_HOST" == "1" ]]; then
  TAKYON_VPS_HOST="$TAKYON_VPS_HOST" \
  TAKYON_VPS_KEY="$TAKYON_VPS_KEY" \
  TAKYON_REMOTE_RUNTIME="$TAKYON_REMOTE_RUNTIME" \
  TAKYON_CLAUDE_AGENT_DOCKER_IMAGE="$TAKYON_CLAUDE_AGENT_DOCKER_IMAGE" \
    "$BOOTSTRAP_SCRIPT"
fi

if [[ "$TAKYON_RUN_WEB_BUILD" == "1" ]]; then
  (cd "$RUNTIME_DIR/web" && npm ci && npm run build)
fi

python3 -m compileall -q \
  "$RUNTIME_DIR/plugins/takyon" \
  "$RUNTIME_DIR/takyon_cli" \
  "$RUNTIME_DIR/tui_gateway"

rsync -az --delete --force \
  --exclude '.git/' \
  --exclude '.pytest_cache/' \
  --exclude '__pycache__/' \
  --exclude '*.pyc' \
  --exclude 'node_modules/' \
  --exclude 'web/node_modules/' \
  --exclude '.venv/' \
  -e "ssh -i $TAKYON_VPS_KEY -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new" \
  "$RUNTIME_DIR/" \
  "$TAKYON_VPS_HOST:$TAKYON_REMOTE_RUNTIME/"

scp -i "$TAKYON_VPS_KEY" -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new \
  "$SERVICE_FILE" \
  "$TAKYON_VPS_HOST:$TAKYON_REMOTE_SERVICE_FILE"

scp -i "$TAKYON_VPS_KEY" -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new \
  "$WORKER_SERVICE_FILE" \
  "$TAKYON_VPS_HOST:$TAKYON_REMOTE_WORKER_SERVICE_FILE"

ssh -i "$TAKYON_VPS_KEY" -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new "$TAKYON_VPS_HOST" \
  "set -euo pipefail
  grep -F -- '--tui' '$TAKYON_REMOTE_SERVICE_FILE' >/dev/null
  systemctl enable docker >/dev/null
  systemctl start docker
  systemctl is-active --quiet docker
  docker version >/dev/null
  if ! docker image inspect '$TAKYON_CLAUDE_AGENT_DOCKER_IMAGE' >/dev/null 2>&1; then
    docker pull '$TAKYON_CLAUDE_AGENT_DOCKER_IMAGE'
  fi
  docker run --rm --entrypoint node '$TAKYON_CLAUDE_AGENT_DOCKER_IMAGE' --version >/dev/null
  python3 -m compileall -q '$TAKYON_REMOTE_RUNTIME/plugins/takyon' '$TAKYON_REMOTE_RUNTIME/takyon_cli' '$TAKYON_REMOTE_RUNTIME/tui_gateway'
  systemctl daemon-reload
  systemctl restart takyon-dashboard.service
  systemctl is-active --quiet takyon-dashboard.service
  if grep -F -- 'TAKYON_DB_BACKEND=postgres' '$TAKYON_REMOTE_SERVICE_FILE' >/dev/null; then
    systemctl enable takyon-worker.service >/dev/null
    systemctl restart takyon-worker.service
    systemctl is-active --quiet takyon-worker.service
  fi"

if [[ "$TAKYON_APPLY_CADDY" == "1" ]]; then
  TAKYON_VPS_HOST="$TAKYON_VPS_HOST" TAKYON_VPS_KEY="$TAKYON_VPS_KEY" \
    "$ROOT_DIR/deploy/argon-alpha-14/apply-caddyfile.sh"
fi

if [[ -z "$TAKYON_SMOKE_HOST_HEADER" ]]; then
  TAKYON_SMOKE_HOST_HEADER="${TAKYON_SMOKE_HOST#*://}"
  TAKYON_SMOKE_HOST_HEADER="${TAKYON_SMOKE_HOST_HEADER%%/*}"
  TAKYON_SMOKE_HOST_HEADER="${TAKYON_SMOKE_HOST_HEADER%%:*}"
fi

run_remote_smoke() {
  ssh -i "$TAKYON_VPS_KEY" -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new "$TAKYON_VPS_HOST" \
    "set -euo pipefail
    root_status=\$(curl -sS -o /dev/null -w '%{http_code}' -H 'Host: $TAKYON_SMOKE_HOST_HEADER' http://127.0.0.1/)
    case \"\$root_status\" in
      200|302) ;;
      *)
        echo \"unexpected dashboard root status: \$root_status\" >&2
        exit 1
        ;;
    esac
    api_status=\$(curl -sS -o /dev/null -w '%{http_code}' -H 'Host: $TAKYON_SMOKE_HOST_HEADER' http://127.0.0.1/api/status)
    case \"\$api_status\" in
      200|401) ;;
      *)
        echo \"unexpected dashboard api status: \$api_status\" >&2
        exit 1
        ;;
    esac"
}

for attempt in {1..12}; do
  curl_status=0
  if curl -fsSI \
    --connect-timeout "$TAKYON_SMOKE_CONNECT_TIMEOUT" \
    --max-time "$TAKYON_SMOKE_MAX_TIME" \
    "$TAKYON_SMOKE_HOST" >/dev/null; then
    exit 0
  else
    curl_status=$?
  fi
  if [[ "$curl_status" == "6" || "$curl_status" == "7" || "$curl_status" == "28" ]]; then
    break
  fi
  sleep 5
done

for attempt in {1..12}; do
  if run_remote_smoke; then
    exit 0
  fi
  sleep 5
done

run_remote_smoke
