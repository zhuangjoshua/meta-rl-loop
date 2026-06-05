#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SERVICE_FILE="$ROOT_DIR/deploy/argon-alpha-14/takyon-dashboard.service"
WORKER_SERVICE_FILE="$ROOT_DIR/deploy/argon-alpha-14/takyon-worker.service"

TARGET_HOST="${TAKYON_VPS_HOST:-root@137.184.75.57}"
TARGET_KEY="${TAKYON_VPS_KEY:-$HOME/.ssh/takyon_argon_alpha14}"
REMOTE_ROOT="${TAKYON_REMOTE_ROOT:-/opt/takyon}"
REMOTE_HOME="${TAKYON_REMOTE_HOME:-$REMOTE_ROOT/.takyon}"
REMOTE_SECRETS="${TAKYON_REMOTE_SECRETS:-$REMOTE_ROOT/secrets}"
REMOTE_RUNTIME="${TAKYON_REMOTE_RUNTIME:-$REMOTE_ROOT/hermes-agent-main}"
REMOTE_DOCKER_IMAGE="${TAKYON_CLAUDE_AGENT_DOCKER_IMAGE:-${TERMINAL_DOCKER_IMAGE:-nikolaik/python-nodejs:python3.11-nodejs20}}"

target_ssh=(-i "$TARGET_KEY" -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new)

if [[ ! -f "$SERVICE_FILE" ]]; then
  echo "service file not found: $SERVICE_FILE" >&2
  exit 1
fi

if [[ ! -f "$WORKER_SERVICE_FILE" ]]; then
  echo "worker service file not found: $WORKER_SERVICE_FILE" >&2
  exit 1
fi

if [[ ! -f "$TARGET_KEY" ]]; then
  echo "target key not found: $TARGET_KEY" >&2
  exit 1
fi

ssh "${target_ssh[@]}" "$TARGET_HOST" "set -euo pipefail
  export DEBIAN_FRONTEND=noninteractive
  apt-get update
  apt-get install -y ca-certificates curl rsync caddy docker.io
  if ! command -v xurl >/dev/null 2>&1; then
    curl -fsSL https://raw.githubusercontent.com/xdevplatform/xurl/main/install.sh | bash
  fi
  if ! command -v xurl >/dev/null 2>&1 && [ -x /root/.local/bin/xurl ]; then
    ln -sf /root/.local/bin/xurl /usr/local/bin/xurl
  fi
  command -v xurl >/dev/null 2>&1 || [ -x /root/.local/bin/xurl ]
  install -d '$REMOTE_ROOT' '$REMOTE_HOME' '$REMOTE_HOME/businesses' '$REMOTE_SECRETS'
  systemctl enable docker >/dev/null
  systemctl start docker
  systemctl is-active --quiet docker
  docker version >/dev/null
  if ! docker image inspect '$REMOTE_DOCKER_IMAGE' >/dev/null 2>&1; then
    docker pull '$REMOTE_DOCKER_IMAGE'
  fi
  install -d '$REMOTE_RUNTIME'
"

echo "Operator host bootstrap complete: $TARGET_HOST"
