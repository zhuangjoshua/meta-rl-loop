#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
RUNTIME_DIR="$ROOT_DIR/hermes-agent-main"

TAKYON_VPS_HOST="${TAKYON_VPS_HOST:-root@137.184.75.57}"
TAKYON_VPS_KEY="${TAKYON_VPS_KEY:-$HOME/.ssh/takyon_argon_alpha14}"
TAKYON_REMOTE_RUNTIME="${TAKYON_REMOTE_RUNTIME:-/opt/takyon/hermes-agent-main}"
TAKYON_RUN_WEB_BUILD="${TAKYON_RUN_WEB_BUILD:-1}"
TAKYON_APPLY_CADDY="${TAKYON_APPLY_CADDY:-0}"
TAKYON_SMOKE_HOST="${TAKYON_SMOKE_HOST:-https://app.fourmanifold.com/}"

if [[ ! -d "$RUNTIME_DIR" ]]; then
  echo "runtime directory not found: $RUNTIME_DIR" >&2
  exit 1
fi

if [[ ! -f "$TAKYON_VPS_KEY" ]]; then
  echo "deploy key not found: $TAKYON_VPS_KEY" >&2
  exit 1
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

ssh -i "$TAKYON_VPS_KEY" -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new "$TAKYON_VPS_HOST" \
  "python3 -m compileall -q '$TAKYON_REMOTE_RUNTIME/plugins/takyon' '$TAKYON_REMOTE_RUNTIME/takyon_cli' '$TAKYON_REMOTE_RUNTIME/tui_gateway' && systemctl restart takyon-dashboard.service && systemctl is-active --quiet takyon-dashboard.service"

if [[ "$TAKYON_APPLY_CADDY" == "1" ]]; then
  TAKYON_VPS_HOST="$TAKYON_VPS_HOST" TAKYON_VPS_KEY="$TAKYON_VPS_KEY" \
    "$ROOT_DIR/deploy/argon-alpha-14/apply-caddyfile.sh"
fi

for attempt in {1..12}; do
  if curl -fsSI --max-time 20 "$TAKYON_SMOKE_HOST" >/dev/null; then
    exit 0
  fi
  sleep 5
done

curl -fsSI --max-time 20 "$TAKYON_SMOKE_HOST" >/dev/null
