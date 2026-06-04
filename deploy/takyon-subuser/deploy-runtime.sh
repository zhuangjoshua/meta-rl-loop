#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
RUNTIME_DIR="$ROOT_DIR/hermes-agent-main"
SERVICE_FILE="$ROOT_DIR/deploy/takyon-subuser/takyon-subuser.service"
PRODUCT_SITES_SOURCE_HOST="${TAKYON_PRODUCT_SITES_SOURCE_HOST:-root@137.184.75.57}"
PRODUCT_SITES_SOURCE_KEY="${TAKYON_PRODUCT_SITES_SOURCE_KEY:-$HOME/.ssh/takyon_argon_alpha14}"

TAKYON_VPS_HOST="${TAKYON_VPS_HOST:-root@134.209.123.8}"
TAKYON_VPS_KEY="${TAKYON_VPS_KEY:-$HOME/.ssh/takyon_argon_alpha14}"
TAKYON_REMOTE_ROOT="${TAKYON_REMOTE_ROOT:-/opt/takyon}"
TAKYON_REMOTE_RUNTIME="${TAKYON_REMOTE_RUNTIME:-/opt/takyon/hermes-agent-main}"
TAKYON_REMOTE_HOME="${TAKYON_REMOTE_HOME:-/opt/takyon/.takyon}"
TAKYON_REMOTE_PRODUCT_SITES="${TAKYON_REMOTE_PRODUCT_SITES:-$TAKYON_REMOTE_HOME/product-sites}"
TAKYON_REMOTE_SERVICE_FILE="${TAKYON_REMOTE_SERVICE_FILE:-/etc/systemd/system/takyon-subuser.service}"
TAKYON_REMOTE_SERVICE_NAME="${TAKYON_REMOTE_SERVICE_NAME:-takyon-subuser.service}"
TAKYON_RUN_WEB_BUILD="${TAKYON_RUN_WEB_BUILD:-1}"
TAKYON_APPLY_CADDY="${TAKYON_APPLY_CADDY:-0}"
TAKYON_SYNC_PRODUCT_SITES="${TAKYON_SYNC_PRODUCT_SITES:-1}"

if [[ ! -d "$RUNTIME_DIR" ]]; then
  echo "runtime directory not found: $RUNTIME_DIR" >&2
  exit 1
fi

if [[ ! -f "$SERVICE_FILE" ]]; then
  echo "service file not found: $SERVICE_FILE" >&2
  exit 1
fi

if [[ ! -f "$TAKYON_VPS_KEY" ]]; then
  echo "deploy key not found: $TAKYON_VPS_KEY" >&2
  exit 1
fi

if [[ "$TAKYON_SYNC_PRODUCT_SITES" == "1" && ! -f "$PRODUCT_SITES_SOURCE_KEY" ]]; then
  echo "product-sites source key not found: $PRODUCT_SITES_SOURCE_KEY" >&2
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

scp -i "$TAKYON_VPS_KEY" -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new \
  "$SERVICE_FILE" \
  "$TAKYON_VPS_HOST:$TAKYON_REMOTE_SERVICE_FILE"

ssh -i "$TAKYON_VPS_KEY" -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new "$TAKYON_VPS_HOST" \
  "set -euo pipefail
  install -d '$TAKYON_REMOTE_PRODUCT_SITES'
  python3 -m compileall -q '$TAKYON_REMOTE_RUNTIME/plugins/takyon' '$TAKYON_REMOTE_RUNTIME/takyon_cli' '$TAKYON_REMOTE_RUNTIME/tui_gateway'
  if grep -F -- 'TAKYON_DB_BACKEND=postgres' '$TAKYON_REMOTE_SERVICE_FILE' >/dev/null; then
    env TAKYON_HOME='$TAKYON_REMOTE_HOME' HOME=/root PYTHONUNBUFFERED=1 TAKYON_DB_BACKEND=postgres TAKYON_HOST_ROLE=subuser \
      '$TAKYON_REMOTE_RUNTIME/.venv/bin/python' - <<'PY'
from plugins.takyon.core import load_takyon_env
from plugins.takyon.db.runner import run_migrations
from plugins.takyon.runtime_app import resolve_database_url
import psycopg

load_takyon_env()
with psycopg.connect(resolve_database_url(), autocommit=True, prepare_threshold=None) as conn:
    run_migrations(conn)
PY
  fi
  systemctl daemon-reload
  systemctl restart '$TAKYON_REMOTE_SERVICE_NAME'
  systemctl is-active --quiet '$TAKYON_REMOTE_SERVICE_NAME'
  for _ in \$(seq 1 30); do
    if curl -fsS http://127.0.0.1:9119/healthz >/dev/null; then
      break
    fi
    sleep 1
  done
  curl -fsS http://127.0.0.1:9119/healthz >/dev/null"

if [[ "$TAKYON_SYNC_PRODUCT_SITES" == "1" ]]; then
  ssh_opts_source=(-i "$PRODUCT_SITES_SOURCE_KEY" -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new)
  ssh_opts_target=(-i "$TAKYON_VPS_KEY" -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new)
  ssh "${ssh_opts_source[@]}" "$PRODUCT_SITES_SOURCE_HOST" "set -euo pipefail; cd /opt/takyon; tar -cf - .takyon/product-sites" \
    | ssh "${ssh_opts_target[@]}" "$TAKYON_VPS_HOST" "set -euo pipefail; tar -C '$TAKYON_REMOTE_ROOT' -xf -"
fi

if [[ "$TAKYON_APPLY_CADDY" == "1" ]]; then
  TAKYON_VPS_HOST="$TAKYON_VPS_HOST" TAKYON_VPS_KEY="$TAKYON_VPS_KEY" \
    "$ROOT_DIR/deploy/takyon-subuser/apply-caddyfile.sh"
fi
