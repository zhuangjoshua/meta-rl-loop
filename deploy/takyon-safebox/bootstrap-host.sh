#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SERVICE_FILE="$ROOT_DIR/deploy/takyon-safebox/takyon-safebox.service"

SOURCE_HOST="${TAKYON_SOURCE_HOST:-root@137.184.75.57}"
SOURCE_KEY="${TAKYON_SOURCE_KEY:-$HOME/.ssh/takyon_argon_alpha14}"
TARGET_HOST="${TAKYON_VPS_HOST:-root@67.205.158.170}"
TARGET_KEY="${TAKYON_VPS_KEY:-$HOME/.ssh/takyon_argon_alpha14}"
REMOTE_ROOT="${TAKYON_REMOTE_ROOT:-/opt/takyon}"
REMOTE_RUNTIME="${TAKYON_REMOTE_RUNTIME:-$REMOTE_ROOT/hermes-agent-main}"
REMOTE_HOME="${TAKYON_REMOTE_HOME:-$REMOTE_ROOT/.takyon}"
REMOTE_SECRETS="${TAKYON_REMOTE_SECRETS:-$REMOTE_ROOT/secrets}"
REMOTE_SERVICE_FILE="${TAKYON_REMOTE_SERVICE_FILE:-/etc/systemd/system/takyon-safebox.service}"
REMOTE_SERVICE_NAME="${TAKYON_REMOTE_SERVICE_NAME:-takyon-safebox.service}"

source_ssh=(-i "$SOURCE_KEY" -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new)
target_ssh=(-i "$TARGET_KEY" -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new)

if [[ ! -f "$SERVICE_FILE" ]]; then
  echo "service file not found: $SERVICE_FILE" >&2
  exit 1
fi

if [[ ! -f "$SOURCE_KEY" ]]; then
  echo "source key not found: $SOURCE_KEY" >&2
  exit 1
fi

if [[ ! -f "$TARGET_KEY" ]]; then
  echo "target key not found: $TARGET_KEY" >&2
  exit 1
fi

ssh "${target_ssh[@]}" "$TARGET_HOST" "set -euo pipefail
  export DEBIAN_FRONTEND=noninteractive
  apt-get update
  apt-get install -y ca-certificates curl rsync
  install -d '$REMOTE_ROOT' '$REMOTE_HOME' '$REMOTE_HOME/businesses' '$REMOTE_SECRETS'
"

ssh "${source_ssh[@]}" "$SOURCE_HOST" 'bash -s' <<'EOF' | ssh "${target_ssh[@]}" "$TARGET_HOST" "tar -C '$REMOTE_ROOT' -xf -"
set -euo pipefail
cd /opt/takyon
paths=(hermes-agent-main .takyon/.env .takyon/config.yaml)
if [ -f secrets/.env ]; then
  paths+=(secrets/.env)
fi
if [ -d .takyon/safebox ]; then
  paths+=(.takyon/safebox)
fi
tar \
  --exclude='.git' \
  --exclude='.pytest_cache' \
  --exclude='__pycache__' \
  --exclude='*.pyc' \
  --exclude='node_modules' \
  --exclude='web/node_modules' \
  -cf - "${paths[@]}"
EOF

scp "${target_ssh[@]}" "$SERVICE_FILE" "$TARGET_HOST:$REMOTE_SERVICE_FILE"

ssh "${target_ssh[@]}" "$TARGET_HOST" "set -euo pipefail
  python3 -m compileall -q '$REMOTE_RUNTIME/plugins/takyon' '$REMOTE_RUNTIME/takyon_cli' '$REMOTE_RUNTIME/tui_gateway'
  systemctl daemon-reload
  systemctl enable '$REMOTE_SERVICE_NAME' >/dev/null
  systemctl restart '$REMOTE_SERVICE_NAME'
  systemctl is-active --quiet '$REMOTE_SERVICE_NAME'
  for _ in \$(seq 1 30); do
    if curl -fsS http://127.0.0.1:8000/healthz >/dev/null; then
      break
    fi
    sleep 1
  done
  curl -fsS http://127.0.0.1:8000/healthz >/dev/null
"

echo "Safebox host bootstrap complete: $TARGET_HOST"
