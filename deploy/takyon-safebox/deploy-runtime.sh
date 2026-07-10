#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
RUNTIME_DIR="$ROOT_DIR/hermes-agent-main"
SERVICE_FILE="$ROOT_DIR/deploy/takyon-safebox/takyon-safebox.service"
REBUILD_VENV_SCRIPT="$ROOT_DIR/deploy/takyon-safebox/rebuild-venv.sh"
VERIFY_LOCK_SCRIPT="$ROOT_DIR/deploy/takyon-safebox/verify-requirements-lock.sh"
SUPABASE_AUTH_HELPER="$ROOT_DIR/deploy/shared/supabase-auth-env.sh"
VALIDATE_AUTHORITY_ENV_SCRIPT="$ROOT_DIR/deploy/shared/validate-authority-env.sh"

TAKYON_VPS_HOST="${TAKYON_VPS_HOST:-root@67.205.158.170}"
TAKYON_VPS_KEY="${TAKYON_VPS_KEY:-$HOME/.ssh/takyon_argon_alpha14}"
TAKYON_REMOTE_RUNTIME="${TAKYON_REMOTE_RUNTIME:-/opt/takyon/hermes-agent-main}"
TAKYON_REMOTE_SERVICE_FILE="${TAKYON_REMOTE_SERVICE_FILE:-/etc/systemd/system/takyon-safebox.service}"
TAKYON_REMOTE_SERVICE_NAME="${TAKYON_REMOTE_SERVICE_NAME:-takyon-safebox.service}"
TAKYON_REMOTE_SAFEBOX_PYTHON="${TAKYON_REMOTE_SAFEBOX_PYTHON:-/opt/takyon/venvs/safebox-current/bin/python}"
TAKYON_RUN_WEB_BUILD="${TAKYON_RUN_WEB_BUILD:-0}"

if [[ ! -d "$RUNTIME_DIR" ]]; then
  echo "runtime directory not found: $RUNTIME_DIR" >&2
  exit 1
fi

if [[ -L "$RUNTIME_DIR/.venv" ]]; then
  echo "refusing deploy: runtime .venv is a symlink; remove it before rsync" >&2
  exit 1
fi

if [[ ! -f "$SERVICE_FILE" ]]; then
  echo "service file not found: $SERVICE_FILE" >&2
  exit 1
fi

if [[ ! -x "$REBUILD_VENV_SCRIPT" ]]; then
  echo "Safebox environment builder not found or not executable: $REBUILD_VENV_SCRIPT" >&2
  exit 1
fi

if [[ ! -x "$VERIFY_LOCK_SCRIPT" ]]; then
  echo "Safebox lock verifier not found or not executable: $VERIFY_LOCK_SCRIPT" >&2
  exit 1
fi

if [[ ! -f "$TAKYON_VPS_KEY" ]]; then
  echo "deploy key not found: $TAKYON_VPS_KEY" >&2
  exit 1
fi

if [[ ! -f "$VALIDATE_AUTHORITY_ENV_SCRIPT" ]]; then
  echo "authority env validator not found: $VALIDATE_AUTHORITY_ENV_SCRIPT" >&2
  exit 1
fi

if [[ ! -x "$SUPABASE_AUTH_HELPER" ]]; then
  echo "supabase auth helper not found or not executable: $SUPABASE_AUTH_HELPER" >&2
  exit 1
fi

"$VERIFY_LOCK_SCRIPT"

ssh -i "$TAKYON_VPS_KEY" -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new "$TAKYON_VPS_HOST" \
  "bash -s -- safebox /opt/takyon/.takyon/.env /opt/takyon/secrets/.env" \
  < "$VALIDATE_AUTHORITY_ENV_SCRIPT"

if [[ "$TAKYON_RUN_WEB_BUILD" == "1" ]]; then
  (cd "$RUNTIME_DIR/web" && npm ci && npm run build)
fi

python3 -m compileall -q \
  "$RUNTIME_DIR/plugins/takyon" \
  "$RUNTIME_DIR/takyon_cli" \
  "$RUNTIME_DIR/tui_gateway"

rsync -az --delete \
  --filter='protect /.venv' \
  --exclude '.git/' \
  --exclude '.pytest_cache/' \
  --exclude '__pycache__/' \
  --exclude '*.pyc' \
  --exclude 'node_modules/' \
  --exclude 'web/node_modules/' \
  --exclude '/.venv' \
  -e "ssh -i $TAKYON_VPS_KEY -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new" \
  "$RUNTIME_DIR/" \
  "$TAKYON_VPS_HOST:$TAKYON_REMOTE_RUNTIME/"

previous_venv_target="$(
  ssh -i "$TAKYON_VPS_KEY" -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new \
    "$TAKYON_VPS_HOST" \
    'if [ -L /opt/takyon/venvs/safebox-current ]; then readlink -f /opt/takyon/venvs/safebox-current; fi'
)"

TAKYON_VPS_HOST="$TAKYON_VPS_HOST" \
TAKYON_VPS_KEY="$TAKYON_VPS_KEY" \
TAKYON_REMOTE_RUNTIME="$TAKYON_REMOTE_RUNTIME" \
  "$REBUILD_VENV_SCRIPT"

ssh -i "$TAKYON_VPS_KEY" -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new "$TAKYON_VPS_HOST" \
  "set -euo pipefail
  # The tracked unit runs as the dedicated non-root 'takyon' user — provision idempotently here,
  # before daemon-reload/restart, since this script is the rail that ships the unit.
  if ! id -u takyon >/dev/null 2>&1; then
    useradd --system --user-group --home-dir /opt/takyon --shell /usr/sbin/nologin takyon
  fi
  chown takyon:takyon /opt/takyon
  chown -R takyon:takyon /opt/takyon/.takyon
  if [ -d /opt/takyon/secrets ]; then chown -R takyon:takyon /opt/takyon/secrets; fi
	  if ! grep -q '^TAKYON_SAFEBOX_TOKEN=' /opt/takyon/.takyon/.env 2>/dev/null \
	    && ! grep -q '^TAKYON_SAFEBOX_TOKEN=' /opt/takyon/secrets/.env 2>/dev/null; then
	    echo 'TAKYON_SAFEBOX_TOKEN missing from both /opt/takyon/.takyon/.env and /opt/takyon/secrets/.env' >&2
	    exit 1
	  fi
	  if ! grep -q '^TAKYON_SAFEBOX_OPERATOR_TOKEN=' /opt/takyon/.takyon/.env 2>/dev/null \
	    && ! grep -q '^TAKYON_SAFEBOX_OPERATOR_TOKEN=' /opt/takyon/secrets/.env 2>/dev/null; then
	    echo 'TAKYON_SAFEBOX_OPERATOR_TOKEN missing from both /opt/takyon/.takyon/.env and /opt/takyon/secrets/.env' >&2
	    exit 1
	  fi
	  bash -s -- validate-file /opt/takyon/.takyon/.env /opt/takyon/secrets/.env" \
  < "$SUPABASE_AUTH_HELPER"

remote_service_backup="${TAKYON_REMOTE_SERVICE_FILE}.pre-deploy"
ssh -i "$TAKYON_VPS_KEY" -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new "$TAKYON_VPS_HOST" \
  "set -euo pipefail; cp -p '$TAKYON_REMOTE_SERVICE_FILE' '$remote_service_backup'"

scp -i "$TAKYON_VPS_KEY" -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new \
  "$SERVICE_FILE" \
  "$TAKYON_VPS_HOST:$TAKYON_REMOTE_SERVICE_FILE"

ssh -i "$TAKYON_VPS_KEY" -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new "$TAKYON_VPS_HOST" \
  "set -euo pipefail
  rollback() {
    rc=\$?
    trap - EXIT
    if [ -f '$remote_service_backup' ]; then
      cp -p '$remote_service_backup' '$TAKYON_REMOTE_SERVICE_FILE'
    fi
    if [ -n '$previous_venv_target' ]; then
      ln -sfn '$previous_venv_target' /opt/takyon/venvs/safebox-current.rollback
      mv -Tf /opt/takyon/venvs/safebox-current.rollback /opt/takyon/venvs/safebox-current
    fi
    systemctl daemon-reload
    systemctl restart '$TAKYON_REMOTE_SERVICE_NAME' || true
    exit \$rc
  }
  trap rollback EXIT
  cd '$TAKYON_REMOTE_RUNTIME'
  '$TAKYON_REMOTE_SAFEBOX_PYTHON' -m pip check
  python3 -m compileall -q '$TAKYON_REMOTE_RUNTIME/plugins/takyon' '$TAKYON_REMOTE_RUNTIME/takyon_cli' '$TAKYON_REMOTE_RUNTIME/tui_gateway'
  systemctl daemon-reload
  systemctl restart '$TAKYON_REMOTE_SERVICE_NAME'
  systemctl is-active --quiet '$TAKYON_REMOTE_SERVICE_NAME'
  # The service binds the VPC interface only (see the unit), so the health probe targets it too.
  for _ in \$(seq 1 30); do
    if curl -fsS http://10.116.0.2:8000/healthz >/dev/null; then
      rm -f '$remote_service_backup'
      trap - EXIT
      exit 0
    fi
    sleep 1
  done
  curl -fsS http://10.116.0.2:8000/healthz >/dev/null"
