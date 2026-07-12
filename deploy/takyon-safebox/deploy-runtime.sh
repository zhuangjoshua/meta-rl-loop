#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
RUNTIME_DIR="$ROOT_DIR/hermes-agent-main"
WEB_BUILD_SCRIPT="$ROOT_DIR/deploy/shared/build-web-locked.sh"
RUNTIME_RELEASE_SCRIPT="$ROOT_DIR/deploy/shared/runtime-release.sh"
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
TAKYON_RUN_WEB_BUILD="${TAKYON_RUN_WEB_BUILD:-1}"
TAKYON_EXPECT_STRIPE_CHECKOUT_DISABLED="${TAKYON_EXPECT_STRIPE_CHECKOUT_DISABLED:-0}"

if [[ "$TAKYON_EXPECT_STRIPE_CHECKOUT_DISABLED" != "0" \
  && "$TAKYON_EXPECT_STRIPE_CHECKOUT_DISABLED" != "1" ]]; then
  echo "TAKYON_EXPECT_STRIPE_CHECKOUT_DISABLED must be exactly 0 or 1" >&2
  exit 1
fi

# Every production plane promotes one immutable outer-repository revision under the same Mac lock.
# Safebox does not serve the dashboard, but building the bounded bundle here keeps this canonical
# deploy entrypoint on the same revision-pinned rail as operator and sub-user deployments.
if [[ "${TAKYON_DEPLOY_LOCK_HELD:-0}" != "1" ]]; then
  if [[ "$TAKYON_RUN_WEB_BUILD" != "1" ]]; then
    echo "refusing unlocked Safebox deploy: TAKYON_RUN_WEB_BUILD=0 is internal-only" >&2
    exit 1
  fi
  if [[ ! -f "$WEB_BUILD_SCRIPT" ]]; then
    echo "web build helper not found: $WEB_BUILD_SCRIPT" >&2
    exit 1
  fi
  exec bash "$WEB_BUILD_SCRIPT" "$RUNTIME_DIR" -- "$ROOT_DIR/deploy/takyon-safebox/deploy-runtime.sh" "$@"
fi

DEPLOY_REPO_DIR="${TAKYON_DEPLOY_REPO_ARTIFACT:-}"
DEPLOY_RUNTIME_DIR="${TAKYON_DEPLOY_RUNTIME_ARTIFACT:-}"
if [[ ! -d "$DEPLOY_RUNTIME_DIR" || ! -f "$DEPLOY_RUNTIME_DIR/.takyon-deploy-artifact.json" ]]; then
  echo "immutable deploy runtime artifact is missing or invalid: $DEPLOY_RUNTIME_DIR" >&2
  exit 1
fi
if [[ ! -d "$DEPLOY_REPO_DIR" \
  || "$(cd "$DEPLOY_REPO_DIR" && pwd -P)" != "$(cd "$ROOT_DIR" && pwd -P)" \
  || "$(cd "$DEPLOY_RUNTIME_DIR" && pwd -P)" != "$(cd "$RUNTIME_DIR" && pwd -P)" ]]; then
  echo "Safebox deploy callback is not running entirely from the immutable repository artifact" >&2
  exit 1
fi

if [[ ! -f "$RUNTIME_RELEASE_SCRIPT" ]]; then
  echo "runtime release helper not found: $RUNTIME_RELEASE_SCRIPT" >&2
  exit 1
fi
# shellcheck disable=SC1090
source "$RUNTIME_RELEASE_SCRIPT"
takyon_runtime_release_init "$TAKYON_REMOTE_RUNTIME" "$TAKYON_DEPLOY_SOURCE_REVISION"

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

if [[ ! -f "$WEB_BUILD_SCRIPT" ]]; then
  echo "web build helper not found: $WEB_BUILD_SCRIPT" >&2
  exit 1
fi

"$VERIFY_LOCK_SCRIPT"

ssh -i "$TAKYON_VPS_KEY" -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new "$TAKYON_VPS_HOST" \
  "bash -s -- safebox /opt/takyon/.takyon/.env /opt/takyon/secrets/.env" \
  < "$VALIDATE_AUTHORITY_ENV_SCRIPT"

ssh -i "$TAKYON_VPS_KEY" -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new \
  "$TAKYON_VPS_HOST" \
  "set -euo pipefail
  env_files=(/opt/takyon/.takyon/.env /opt/takyon/secrets/.env)
  test \"\$(grep -hE '^TAKYON_STRIPE_CHECKOUT_DISABLED=' \"\${env_files[@]}\" | tail -n 1)\" = \
    'TAKYON_STRIPE_CHECKOUT_DISABLED=$TAKYON_EXPECT_STRIPE_CHECKOUT_DISABLED'
  test \"\$(grep -hE '^TAKYON_STRIPE_ACCOUNT_ID=' \"\${env_files[@]}\" | tail -n 1)\" = \
    'TAKYON_STRIPE_ACCOUNT_ID=acct_1TXWsW7tYL4lkVC6'
  test \"\$(grep -hE '^TAKYON_STRIPE_OPERATOR_CHECKOUT_DISABLED=' \"\${env_files[@]}\" | tail -n 1)\" = \
    'TAKYON_STRIPE_OPERATOR_CHECKOUT_DISABLED=1'
  test \"\$(grep -hE '^TAKYON_STRIPE_CREATIVE_CHECKOUT_DISABLED=' \"\${env_files[@]}\" | tail -n 1)\" = \
    'TAKYON_STRIPE_CREATIVE_CHECKOUT_DISABLED=1'"

(
  compile_cache="$(mktemp -d)"
  trap 'rm -rf "$compile_cache"' EXIT
  PYTHONPYCACHEPREFIX="$compile_cache" python3 -m compileall -q \
    "$DEPLOY_RUNTIME_DIR/plugins/takyon" \
    "$DEPLOY_RUNTIME_DIR/takyon_cli" \
    "$DEPLOY_RUNTIME_DIR/tui_gateway"
)

safebox_stage_incomplete=1
cleanup_incomplete_safebox_stage() {
  local status="$?"
  if [[ "$safebox_stage_incomplete" == "1" ]]; then
    takyon_discard_staged_runtime_release "$TAKYON_VPS_HOST" "$TAKYON_VPS_KEY" || true
  fi
  return "$status"
}
trap cleanup_incomplete_safebox_stage EXIT

takyon_stage_runtime_release \
  "$DEPLOY_RUNTIME_DIR" "$TAKYON_VPS_HOST" "$TAKYON_VPS_KEY" "$TAKYON_DEPLOY_SOURCE_REVISION"

remote_service_candidate="$TAKYON_REMOTE_RELEASE_META/candidates/takyon-safebox.service"
remote_service_backup="$TAKYON_REMOTE_RELEASE_META/backups/takyon-safebox.service"
remote_venv_result="$TAKYON_REMOTE_RELEASE_META/safebox-venv-candidate"
scp -i "$TAKYON_VPS_KEY" -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new \
  "$SERVICE_FILE" \
  "$TAKYON_VPS_HOST:$remote_service_candidate"
ssh -i "$TAKYON_VPS_KEY" -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new "$TAKYON_VPS_HOST" \
  "set -euo pipefail
  test -s '$remote_service_candidate'
  systemd-analyze verify '$remote_service_candidate' >/dev/null"

previous_venv_target="$(
  ssh -i "$TAKYON_VPS_KEY" -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new \
    "$TAKYON_VPS_HOST" \
    'set -euo pipefail; test -L /opt/takyon/venvs/safebox-current; readlink -f /opt/takyon/venvs/safebox-current'
)"

TAKYON_VPS_HOST="$TAKYON_VPS_HOST" \
TAKYON_VPS_KEY="$TAKYON_VPS_KEY" \
TAKYON_REMOTE_RUNTIME="$TAKYON_REMOTE_STAGED_RUNTIME" \
TAKYON_SAFEBOX_VENV_ACTIVATE=0 \
TAKYON_SAFEBOX_VENV_REPAIR_ID="$TAKYON_DEPLOY_SOURCE_REVISION" \
TAKYON_SAFEBOX_VENV_RESULT_FILE="$remote_venv_result" \
  "$REBUILD_VENV_SCRIPT"

remote_venv_candidate="$(
  ssh -i "$TAKYON_VPS_KEY" -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new \
    "$TAKYON_VPS_HOST" "set -euo pipefail; cat '$remote_venv_result'"
)"
if ! [[ "$remote_venv_candidate" =~ ^/opt/takyon/venvs/safebox-[0-9a-f]{16}(-repair-[0-9A-Za-z._-]{1,12})?$ ]]; then
  echo "Safebox environment builder returned an unsafe candidate: $remote_venv_candidate" >&2
  exit 1
fi

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

ssh -i "$TAKYON_VPS_KEY" -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new "$TAKYON_VPS_HOST" \
  "set -euo pipefail
  cd '$TAKYON_REMOTE_STAGED_RUNTIME'
  test -x '$remote_venv_candidate/bin/python'
  PYTHONPATH='$TAKYON_REMOTE_STAGED_RUNTIME' '$remote_venv_candidate/bin/python' -m pip check
  PYTHONPATH='$TAKYON_REMOTE_STAGED_RUNTIME' python3 -m compileall -q \
    '$TAKYON_REMOTE_STAGED_RUNTIME/plugins/takyon' \
    '$TAKYON_REMOTE_STAGED_RUNTIME/takyon_cli' \
    '$TAKYON_REMOTE_STAGED_RUNTIME/tui_gateway'
  PYTHONPATH='$TAKYON_REMOTE_STAGED_RUNTIME' TAKYON_HOME=/opt/takyon/.takyon \
    TAKYON_HOST_ROLE=safebox '$remote_venv_candidate/bin/python' -c \
    'from plugins.takyon import safebox_app; assert safebox_app.app is not None'"

safebox_stage_incomplete=0
trap - EXIT
activation_started=0
rollback_safebox_activation() {
  local status="$?"
  if [[ "$activation_started" == "1" ]]; then
    if ssh -i "$TAKYON_VPS_KEY" -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new "$TAKYON_VPS_HOST" \
      "set -euo pipefail
      systemctl stop '$TAKYON_REMOTE_SERVICE_NAME'
      systemctl is-active --quiet '$TAKYON_REMOTE_SERVICE_NAME' && exit 1 || true" \
      && takyon_rollback_runtime_if_pending "$TAKYON_VPS_HOST" "$TAKYON_VPS_KEY"; then
      if ! ssh -i "$TAKYON_VPS_KEY" -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new "$TAKYON_VPS_HOST" \
        "set -euo pipefail
        if [[ -f '$remote_service_backup' ]]; then
          cp -p '$remote_service_backup' '$TAKYON_REMOTE_SERVICE_FILE'
        fi
        ln -sfn '$previous_venv_target' /opt/takyon/venvs/safebox-current.rollback
        mv -Tf /opt/takyon/venvs/safebox-current.rollback /opt/takyon/venvs/safebox-current
        systemctl daemon-reload
        systemctl restart '$TAKYON_REMOTE_SERVICE_NAME'
        systemctl is-active --quiet '$TAKYON_REMOTE_SERVICE_NAME'"; then
        echo "Safebox unit/venv rollback failed; leaving the service stopped for manual recovery" >&2
      fi
    else
      echo "Safebox could not enter a safe rollback state; leaving release state intact for manual recovery" >&2
    fi
  fi
  return "$status"
}
trap rollback_safebox_activation EXIT

takyon_prepare_runtime_rollback "$TAKYON_VPS_HOST" "$TAKYON_VPS_KEY"
activation_started=1
takyon_begin_runtime_activation "$TAKYON_VPS_HOST" "$TAKYON_VPS_KEY"
ssh -i "$TAKYON_VPS_KEY" -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new "$TAKYON_VPS_HOST" \
  "set -euo pipefail
  systemctl stop '$TAKYON_REMOTE_SERVICE_NAME'
  systemctl is-active --quiet '$TAKYON_REMOTE_SERVICE_NAME' && exit 1 || true
  cp -p '$TAKYON_REMOTE_SERVICE_FILE' '$remote_service_backup'
  install -o root -g root -m 0644 '$remote_service_candidate' '$TAKYON_REMOTE_SERVICE_FILE'
  if [[ -n '$previous_venv_target' && '$previous_venv_target' != '$remote_venv_candidate' ]]; then
    ln -sfn '$previous_venv_target' /opt/takyon/venvs/safebox-previous.next
    mv -Tf /opt/takyon/venvs/safebox-previous.next /opt/takyon/venvs/safebox-previous
  fi
  ln -sfn '$remote_venv_candidate' /opt/takyon/venvs/safebox-current.next
  mv -Tf /opt/takyon/venvs/safebox-current.next /opt/takyon/venvs/safebox-current"
takyon_activate_staged_runtime "$TAKYON_VPS_HOST" "$TAKYON_VPS_KEY" "$TAKYON_DEPLOY_SOURCE_REVISION"
ssh -i "$TAKYON_VPS_KEY" -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new "$TAKYON_VPS_HOST" \
  "set -euo pipefail
  systemctl daemon-reload
  systemctl restart '$TAKYON_REMOTE_SERVICE_NAME'
  systemctl is-active --quiet '$TAKYON_REMOTE_SERVICE_NAME'
  pid=\$(systemctl show -p MainPID --value '$TAKYON_REMOTE_SERVICE_NAME')
  [[ \"\$pid\" != 0 ]]
  process_env=\$(tr '\\000' '\\n' < \"/proc/\$pid/environ\")
  grep -Fxq 'TAKYON_STRIPE_CHECKOUT_DISABLED=$TAKYON_EXPECT_STRIPE_CHECKOUT_DISABLED' <<<\"\$process_env\"
  grep -Fxq 'TAKYON_STRIPE_ACCOUNT_ID=acct_1TXWsW7tYL4lkVC6' <<<\"\$process_env\"
  grep -Fxq 'TAKYON_STRIPE_MODE=live' <<<\"\$process_env\"
  grep -Fxq 'TAKYON_STRIPE_OPERATOR_CHECKOUT_DISABLED=1' <<<\"\$process_env\"
  grep -Fxq 'TAKYON_STRIPE_CREATIVE_CHECKOUT_DISABLED=1' <<<\"\$process_env\"
  for _ in \$(seq 1 30); do
    curl -fsS http://10.116.0.2:8000/healthz >/dev/null && break
    sleep 1
  done
  curl -fsS http://10.116.0.2:8000/healthz >/dev/null
  test \"\$(readlink -f /opt/takyon/venvs/safebox-current)\" = '$remote_venv_candidate'"
activation_started=0
trap - EXIT
takyon_finalize_runtime_release "$TAKYON_VPS_HOST" "$TAKYON_VPS_KEY"
