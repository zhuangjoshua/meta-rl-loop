#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
RUNTIME_DIR="$ROOT_DIR/hermes-agent-main"
BOOTSTRAP_SCRIPT="$ROOT_DIR/deploy/argon-alpha-14/bootstrap-host.sh"
REPAIR_PRODUCT_RUNTIME_SCRIPT="$ROOT_DIR/deploy/argon-alpha-14/repair-product-runtime.sh"
SEED_XURL_AUTH_SCRIPT="$ROOT_DIR/deploy/shared/seed-xurl-auth.sh"
VERIFY_SUPABASE_AUTH_SCRIPT="$RUNTIME_DIR/scripts/verify-supabase-auth-runtime.py"
VALIDATE_AUTHORITY_ENV_SCRIPT="$ROOT_DIR/deploy/shared/validate-authority-env.sh"
REMOVE_STRIPE_AUTHORITY_ENV_SCRIPT="$ROOT_DIR/deploy/shared/remove-stripe-authority-env.py"
WEB_BUILD_SCRIPT="$ROOT_DIR/deploy/shared/build-web-locked.sh"
RUNTIME_RELEASE_SCRIPT="$ROOT_DIR/deploy/shared/runtime-release.sh"
PREFLIGHT_STAGED_RUNTIME_SCRIPT="$ROOT_DIR/deploy/argon-alpha-14/preflight-staged-runtime.sh"
ISOLATE_OPERATOR_MIGRATION_DSN_SCRIPT="$ROOT_DIR/deploy/argon-alpha-14/isolate-operator-migration-dsn.sh"
SERVICE_FILE="$ROOT_DIR/deploy/argon-alpha-14/takyon-dashboard.service"
WORKER_SERVICE_FILE="$ROOT_DIR/deploy/argon-alpha-14/takyon-worker.service"
DOCKER_BROKER_SERVICE_FILE="$ROOT_DIR/deploy/argon-alpha-14/takyon-docker-broker.service"
OPERATOR_CLI_FILE="$ROOT_DIR/deploy/argon-alpha-14/takyon-op"
RETIRE_STRIPE_SANDBOX_SCRIPT="$ROOT_DIR/deploy/argon-alpha-14/retire-stripe-sandbox.sh"

TAKYON_VPS_HOST="${TAKYON_VPS_HOST:-root@137.184.75.57}"
TAKYON_VPS_KEY="${TAKYON_VPS_KEY:-$HOME/.ssh/takyon_argon_alpha14}"
TAKYON_REMOTE_RUNTIME="${TAKYON_REMOTE_RUNTIME:-/opt/takyon/hermes-agent-main}"
TAKYON_REMOTE_HOME="${TAKYON_REMOTE_HOME:-/opt/takyon/.takyon}"
TAKYON_REMOTE_SERVICE_FILE="${TAKYON_REMOTE_SERVICE_FILE:-/etc/systemd/system/takyon-dashboard.service}"
TAKYON_REMOTE_WORKER_SERVICE_FILE="${TAKYON_REMOTE_WORKER_SERVICE_FILE:-/etc/systemd/system/takyon-worker.service}"
TAKYON_REMOTE_DOCKER_BROKER_SERVICE_FILE="${TAKYON_REMOTE_DOCKER_BROKER_SERVICE_FILE:-/etc/systemd/system/takyon-docker-broker.service}"
TAKYON_REMOTE_SAFEBOX_URL="${TAKYON_REMOTE_SAFEBOX_URL:-http://10.116.0.2:8000}"
TAKYON_RUN_WEB_BUILD="${TAKYON_RUN_WEB_BUILD:-1}"
# Migrations are an explicit deploy step only when this revision adds db/migrations/*.sql. Replaying
# every historical DDL file on an ordinary code deploy can wait on live Mac-owned transactions and
# is not a no-cost health check. Call with TAKYON_RUN_DB_MIGRATIONS=1 for migration-bearing revisions.
TAKYON_RUN_DB_MIGRATIONS="${TAKYON_RUN_DB_MIGRATIONS:-0}"
TAKYON_FINALIZE_STRIPE_LIVE="${TAKYON_FINALIZE_STRIPE_LIVE:-0}"
TAKYON_BOOTSTRAP_HOST="${TAKYON_BOOTSTRAP_HOST:-1}"
TAKYON_APPLY_CADDY="${TAKYON_APPLY_CADDY:-0}"
TAKYON_SMOKE_HOST="${TAKYON_SMOKE_HOST:-https://app.fourmanifold.com/}"
TAKYON_SMOKE_HOST_HEADER="${TAKYON_SMOKE_HOST_HEADER:-}"
TAKYON_SMOKE_CONNECT_TIMEOUT="${TAKYON_SMOKE_CONNECT_TIMEOUT:-5}"
TAKYON_SMOKE_MAX_TIME="${TAKYON_SMOKE_MAX_TIME:-10}"
TAKYON_DEPLOY_DRAIN_TIMEOUT_SECONDS="${TAKYON_DEPLOY_DRAIN_TIMEOUT_SECONDS:-900}"
TAKYON_DEPLOY_DRAIN_POLL_SECONDS="${TAKYON_DEPLOY_DRAIN_POLL_SECONDS:-5}"
TAKYON_DEPLOY_ACTIVE_WORK_REQUEST_FRESHNESS_SECONDS="${TAKYON_DEPLOY_ACTIVE_WORK_REQUEST_FRESHNESS_SECONDS:-1800}"
TAKYON_DEPLOY_SERVICE_READY_TIMEOUT_SECONDS="${TAKYON_DEPLOY_SERVICE_READY_TIMEOUT_SECONDS:-60}"
TAKYON_CLAUDE_AGENT_DOCKER_IMAGE="${TAKYON_CLAUDE_AGENT_DOCKER_IMAGE:-takyon/claude-worker:node20-chromium-v1}"
TAKYON_REQUIRE_XURL_AUTH="${TAKYON_REQUIRE_XURL_AUTH:-0}"
TAKYON_DENO_VERSION="${TAKYON_DENO_VERSION:-2.8.3}"

# The outer invocation builds a read-only, revision-pinned runtime snapshot and re-enters this
# script while holding the home-level promotion lock. The lock stays owned through rsync, service
# activation, smoke checks, and every other exit gate below; no deploy reads the mutable worktree.
if [[ "${TAKYON_DEPLOY_LOCK_HELD:-0}" != "1" ]]; then
  if [[ "$TAKYON_RUN_WEB_BUILD" != "1" ]]; then
    echo "refusing unlocked operator deploy: TAKYON_RUN_WEB_BUILD=0 is internal-only" >&2
    exit 1
  fi
  if [[ ! -f "$WEB_BUILD_SCRIPT" ]]; then
    echo "web build helper not found: $WEB_BUILD_SCRIPT" >&2
    exit 1
  fi
  exec bash "$WEB_BUILD_SCRIPT" "$RUNTIME_DIR" -- "$ROOT_DIR/deploy/argon-alpha-14/deploy-runtime.sh" "$@"
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
  echo "operator deploy callback is not running entirely from the immutable repository artifact" >&2
  exit 1
fi

if [[ ! -f "$RUNTIME_RELEASE_SCRIPT" ]]; then
  echo "runtime release helper not found: $RUNTIME_RELEASE_SCRIPT" >&2
  exit 1
fi
if [[ ! -f "$PREFLIGHT_STAGED_RUNTIME_SCRIPT" ]]; then
  echo "staged runtime preflight not found: $PREFLIGHT_STAGED_RUNTIME_SCRIPT" >&2
  exit 1
fi
# shellcheck disable=SC1090
source "$RUNTIME_RELEASE_SCRIPT"
takyon_runtime_release_init "$TAKYON_REMOTE_RUNTIME" "$TAKYON_DEPLOY_SOURCE_REVISION"

remote_dashboard_candidate="$TAKYON_REMOTE_RELEASE_META/candidates/takyon-dashboard.service"
remote_worker_candidate="$TAKYON_REMOTE_RELEASE_META/candidates/takyon-worker.service"
remote_docker_broker_candidate="$TAKYON_REMOTE_RELEASE_META/candidates/takyon-docker-broker.service"
remote_operator_cli_candidate="$TAKYON_REMOTE_RELEASE_META/candidates/takyon-op"
remote_dashboard_backup="$TAKYON_REMOTE_RELEASE_META/backups/takyon-dashboard.service"
remote_worker_backup="$TAKYON_REMOTE_RELEASE_META/backups/takyon-worker.service"
remote_docker_broker_backup="$TAKYON_REMOTE_RELEASE_META/backups/takyon-docker-broker.service"
remote_operator_cli_backup="$TAKYON_REMOTE_RELEASE_META/backups/takyon-op"
remote_release_files_marker="$TAKYON_REMOTE_RELEASE_META/release-files-installed"
remote_operator_cli_existed_marker="$TAKYON_REMOTE_RELEASE_META/operator-cli-existed"
remote_skills_backup="$TAKYON_REMOTE_RELEASE_META/backups/home-skills"
remote_skills_existed_marker="$TAKYON_REMOTE_RELEASE_META/home-skills-existed"
remote_skills_activation_marker="$TAKYON_REMOTE_RELEASE_META/home-skills-installed"
remote_sdk_root="$TAKYON_REMOTE_HOME/runtime/claude-agent-sdk"
remote_sdk_release="$TAKYON_REMOTE_HOME/runtime/claude-agent-sdk/releases/$TAKYON_DEPLOY_SOURCE_REVISION"
remote_sdk_current="$TAKYON_REMOTE_HOME/runtime/claude-agent-sdk/current"
remote_sdk_current_backup="$TAKYON_REMOTE_RELEASE_META/backups/claude-sdk-current-target"
remote_sdk_current_existed_marker="$TAKYON_REMOTE_RELEASE_META/claude-sdk-current-existed"
remote_sdk_activation_marker="$TAKYON_REMOTE_RELEASE_META/claude-sdk-current-activated"

if [[ ! -d "$RUNTIME_DIR" ]]; then
  echo "runtime directory not found: $RUNTIME_DIR" >&2
  exit 1
fi

if [[ -L "$RUNTIME_DIR/.venv" ]]; then
  echo "refusing deploy: runtime .venv is a symlink; remove it before rsync" >&2
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

if [[ ! -f "$DOCKER_BROKER_SERVICE_FILE" ]]; then
  echo "docker broker service file not found: $DOCKER_BROKER_SERVICE_FILE" >&2
  exit 1
fi

if [[ ! -f "$OPERATOR_CLI_FILE" ]]; then
  echo "operator CLI wrapper not found: $OPERATOR_CLI_FILE" >&2
  exit 1
fi

if [[ ! -x "$RETIRE_STRIPE_SANDBOX_SCRIPT" ]]; then
  echo "Stripe sandbox retirement script not executable: $RETIRE_STRIPE_SANDBOX_SCRIPT" >&2
  exit 1
fi

if [[ ! -f "$SEED_XURL_AUTH_SCRIPT" ]]; then
  echo "xurl auth seed script not found: $SEED_XURL_AUTH_SCRIPT" >&2
  exit 1
fi

if [[ ! -f "$REPAIR_PRODUCT_RUNTIME_SCRIPT" ]]; then
  echo "repair script not found: $REPAIR_PRODUCT_RUNTIME_SCRIPT" >&2
  exit 1
fi

if [[ ! -f "$VERIFY_SUPABASE_AUTH_SCRIPT" ]]; then
  echo "supabase auth verifier not found: $VERIFY_SUPABASE_AUTH_SCRIPT" >&2
  exit 1
fi

if [[ ! -f "$VALIDATE_AUTHORITY_ENV_SCRIPT" ]]; then
  echo "authority env validator not found: $VALIDATE_AUTHORITY_ENV_SCRIPT" >&2
  exit 1
fi

if [[ ! -f "$REMOVE_STRIPE_AUTHORITY_ENV_SCRIPT" ]]; then
  echo "Stripe authority env cleanup not found: $REMOVE_STRIPE_AUTHORITY_ENV_SCRIPT" >&2
  exit 1
fi

if [[ ! -f "$WEB_BUILD_SCRIPT" ]]; then
  echo "web build helper not found: $WEB_BUILD_SCRIPT" >&2
  exit 1
fi

if [[ ! -x "$ISOLATE_OPERATOR_MIGRATION_DSN_SCRIPT" ]]; then
  echo "operator migration credential isolator not executable: $ISOLATE_OPERATOR_MIGRATION_DSN_SCRIPT" >&2
  exit 1
fi

if [[ ! -f "$TAKYON_VPS_KEY" ]]; then
  echo "deploy key not found: $TAKYON_VPS_KEY" >&2
  exit 1
fi

for numeric_setting in \
  TAKYON_DEPLOY_DRAIN_TIMEOUT_SECONDS \
  TAKYON_DEPLOY_DRAIN_POLL_SECONDS \
  TAKYON_DEPLOY_ACTIVE_WORK_REQUEST_FRESHNESS_SECONDS \
  TAKYON_DEPLOY_SERVICE_READY_TIMEOUT_SECONDS; do
  numeric_value="${!numeric_setting}"
  if ! [[ "$numeric_value" =~ ^[0-9]+$ ]] || (( numeric_value < 1 )); then
    echo "$numeric_setting must be a positive integer" >&2
    exit 1
  fi
done

operator_worker_quiesce_attempted=0
operator_dashboard_stopped=0
operator_runtime_activation_started=0
operator_services_activated=0
worker_release_fence_activated=0
worker_release_previous_sha=""
worker_release_activation_receipt=""

restore_operator_services_on_failure() {
  local exit_status="$?"
  local operator_rollback_ready=1
  local release_restore_receipt=""
  if [[ "$operator_worker_quiesce_attempted" == "1" && "$operator_services_activated" != "1" ]]; then
    echo "operator deploy did not activate; restoring core services before exit" >&2
    if [[ "$operator_runtime_activation_started" == "1" ]]; then
      if ! ssh -i "$TAKYON_VPS_KEY" -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new "$TAKYON_VPS_HOST" \
        "set -euo pipefail
        systemctl stop takyon-worker.service takyon-dashboard.service takyon-docker-broker.service
        for unit in takyon-worker.service takyon-dashboard.service takyon-docker-broker.service; do
          systemctl is-active --quiet \"\$unit\" && exit 1 || true
        done"; then
        operator_rollback_ready=0
      fi
      if [[ "$operator_rollback_ready" == "1" ]] \
        && ! takyon_rollback_runtime_if_pending "$TAKYON_VPS_HOST" "$TAKYON_VPS_KEY"; then
        operator_rollback_ready=0
      fi
      if [[ "$operator_rollback_ready" == "1" ]] \
        && ! ssh -i "$TAKYON_VPS_KEY" -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new "$TAKYON_VPS_HOST" \
        "set -euo pipefail
        if [[ -f '$remote_release_files_marker' ]]; then
          cp -p '$remote_dashboard_backup' '$TAKYON_REMOTE_SERVICE_FILE'
          cp -p '$remote_worker_backup' '$TAKYON_REMOTE_WORKER_SERVICE_FILE'
          cp -p '$remote_docker_broker_backup' '$TAKYON_REMOTE_DOCKER_BROKER_SERVICE_FILE'
          if [[ -f '$remote_operator_cli_existed_marker' ]]; then
            cp -p '$remote_operator_cli_backup' /usr/local/bin/takyon-op
          else
            rm -f /usr/local/bin/takyon-op
          fi
        fi"; then
        operator_rollback_ready=0
      fi
      if [[ "$operator_rollback_ready" == "1" ]] \
        && ! ssh -i "$TAKYON_VPS_KEY" -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new "$TAKYON_VPS_HOST" \
        "set -euo pipefail
        if [[ -f '$remote_sdk_activation_marker' ]]; then
          rm -f '$remote_sdk_current'
          if [[ -f '$remote_sdk_current_existed_marker' ]]; then
            previous_target=\"\$(cat '$remote_sdk_current_backup')\"
            test -n \"\$previous_target\"
            ln -s \"\$previous_target\" '$remote_sdk_current.rollback'
            mv -Tf '$remote_sdk_current.rollback' '$remote_sdk_current'
          fi
        fi
        if [[ -f '$remote_skills_activation_marker' ]]; then
          rm -rf '$TAKYON_REMOTE_HOME/skills'
          if [[ -f '$remote_skills_existed_marker' ]]; then
            test -d '$remote_skills_backup'
            cp -a '$remote_skills_backup' '$TAKYON_REMOTE_HOME/skills'
          fi
        fi"; then
        operator_rollback_ready=0
      fi
    fi
    if [[ "$operator_rollback_ready" == "1" && "$worker_release_fence_activated" == "1" ]]; then
      if release_restore_receipt="$(
        remote_worker_release_fence restore \
          "$TAKYON_DEPLOY_SOURCE_REVISION" "$worker_release_previous_sha"
      )"; then
        echo "worker release fence restored: $release_restore_receipt" >&2
        worker_release_fence_activated=0
      else
        echo "worker release fence restore refused; target work may have started" >&2
        operator_rollback_ready=0
      fi
    fi
    if [[ "$operator_rollback_ready" == "1" ]]; then
      ssh -i "$TAKYON_VPS_KEY" -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new "$TAKYON_VPS_HOST" \
        "set +e
        systemctl daemon-reload
        if [[ '$operator_dashboard_stopped' == '1' ]]; then
          systemctl restart takyon-docker-broker.service
          systemctl restart takyon-dashboard.service
        fi
        if grep -F -- 'TAKYON_DB_BACKEND=postgres' '$TAKYON_REMOTE_SERVICE_FILE' >/dev/null; then
          # Queue the restart behind any still-draining stop job. The deploy must never turn a
          # bounded drain timeout into SIGKILL of a product writer.
          systemctl restart --no-block takyon-worker.service
        fi
        systemctl is-active --quiet takyon-dashboard.service" \
        || true
    else
      echo "operator rollback failed; leaving core services stopped for manual recovery" >&2
    fi
    if [[ "$operator_runtime_activation_started" != "1" ]]; then
      takyon_discard_staged_runtime_release "$TAKYON_VPS_HOST" "$TAKYON_VPS_KEY" || true
    fi
  fi
  return "$exit_status"
}
trap restore_operator_services_on_failure EXIT

quiesce_remote_worker() {
  operator_worker_quiesce_attempted=1
  ssh -i "$TAKYON_VPS_KEY" -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new "$TAKYON_VPS_HOST" \
    "set -euo pipefail
    if ! grep -F -- 'TAKYON_DB_BACKEND=postgres' '$TAKYON_REMOTE_SERVICE_FILE' >/dev/null; then
      exit 0
    fi
    # SIGTERM tells WorkerPool to stop claiming immediately and join every in-flight lane. The
    # transient infinite stop bound is deliberate: deployment may time out and report blockers,
    # but it must never SIGKILL a running bootstrap/product writer. --no-block lets the tracked
    # database drain below report exact Mac/VPS blockers while systemd waits.
    install -d -m 0755 /run/systemd/system/takyon-worker.service.d
    printf '%s\n' '[Service]' 'TimeoutStopSec=infinity' \
      > /run/systemd/system/takyon-worker.service.d/90-takyon-deploy-stop-grace.conf
    chmod 0644 /run/systemd/system/takyon-worker.service.d/90-takyon-deploy-stop-grace.conf
    systemctl daemon-reload
    systemctl stop --no-block takyon-worker.service"
}

TAKYON_VPS_HOST="$TAKYON_VPS_HOST" \
TAKYON_VPS_KEY="$TAKYON_VPS_KEY" \
  "$ISOLATE_OPERATOR_MIGRATION_DSN_SCRIPT"

ssh -i "$TAKYON_VPS_KEY" -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new "$TAKYON_VPS_HOST" \
  "python3 - /opt/takyon/.takyon/.env /opt/takyon/secrets/.env" \
  < "$REMOVE_STRIPE_AUTHORITY_ENV_SCRIPT"

ssh -i "$TAKYON_VPS_KEY" -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new "$TAKYON_VPS_HOST" \
  "bash -s -- operator /opt/takyon/.takyon/.env /opt/takyon/secrets/.env" \
  < "$VALIDATE_AUTHORITY_ENV_SCRIPT"

if [[ "$TAKYON_BOOTSTRAP_HOST" == "1" ]]; then
  TAKYON_VPS_HOST="$TAKYON_VPS_HOST" \
  TAKYON_VPS_KEY="$TAKYON_VPS_KEY" \
  TAKYON_REMOTE_RUNTIME="$TAKYON_REMOTE_RUNTIME" \
  TAKYON_DENO_VERSION="$TAKYON_DENO_VERSION" \
  TAKYON_CLAUDE_AGENT_DOCKER_IMAGE="$TAKYON_CLAUDE_AGENT_DOCKER_IMAGE" \
    "$BOOTSTRAP_SCRIPT"
fi

# Stop fresh claims before staged preflight reconciles shared dependencies and skills; the dashboard
# keeps serving the old revision until the later bounded source/unit activation window.
quiesce_remote_worker

(
  compile_cache="$(mktemp -d)"
  trap 'rm -rf "$compile_cache"' EXIT
  PYTHONPYCACHEPREFIX="$compile_cache" python3 -m compileall -q \
    "$DEPLOY_RUNTIME_DIR/plugins/takyon" \
    "$DEPLOY_RUNTIME_DIR/takyon_cli" \
    "$DEPLOY_RUNTIME_DIR/tui_gateway"
)

takyon_stage_runtime_release \
  "$DEPLOY_RUNTIME_DIR" "$TAKYON_VPS_HOST" "$TAKYON_VPS_KEY" "$TAKYON_DEPLOY_SOURCE_REVISION"

scp -i "$TAKYON_VPS_KEY" -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new \
  "$SERVICE_FILE" \
  "$TAKYON_VPS_HOST:$remote_dashboard_candidate"

scp -i "$TAKYON_VPS_KEY" -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new \
  "$WORKER_SERVICE_FILE" \
  "$TAKYON_VPS_HOST:$remote_worker_candidate"

scp -i "$TAKYON_VPS_KEY" -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new \
  "$DOCKER_BROKER_SERVICE_FILE" \
  "$TAKYON_VPS_HOST:$remote_docker_broker_candidate"

scp -i "$TAKYON_VPS_KEY" -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new \
  "$OPERATOR_CLI_FILE" \
  "$TAKYON_VPS_HOST:$remote_operator_cli_candidate"

if ! TARGET_HOST="$TAKYON_VPS_HOST" \
  TARGET_KEY="$TAKYON_VPS_KEY" \
  TAKYON_REMOTE_RUNTIME="$TAKYON_REMOTE_RUNTIME" \
  TAKYON_REMOTE_HOME="$TAKYON_REMOTE_HOME" \
  TAKYON_REMOTE_SAFEBOX_URL="$TAKYON_REMOTE_SAFEBOX_URL" \
    "$SEED_XURL_AUTH_SCRIPT"; then
  if [[ "$TAKYON_REQUIRE_XURL_AUTH" == "1" ]]; then
    exit 1
  fi
  echo "warning: xurl auth seed failed; continuing deploy" >&2
fi

run_remote_migrations() {
  ssh -i "$TAKYON_VPS_KEY" -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new "$TAKYON_VPS_HOST" \
    "exec env -i PATH=/usr/sbin:/usr/bin:/sbin:/bin HOME=/root \
      TAKYON_RUN_DB_MIGRATIONS='$TAKYON_RUN_DB_MIGRATIONS' \
      TAKYON_REMOTE_SERVICE_FILE='$TAKYON_REMOTE_SERVICE_FILE' \
      TAKYON_REMOTE_RUNTIME='$TAKYON_REMOTE_STAGED_RUNTIME' \
      TAKYON_REMOTE_SAFEBOX_URL='$TAKYON_REMOTE_SAFEBOX_URL' \
      bash -s" <<'REMOTE_MIGRATE'
set -euo pipefail
if [[ "$TAKYON_RUN_DB_MIGRATIONS" != "1" ]] \
  || ! grep -F -- 'TAKYON_DB_BACKEND=postgres' "$TAKYON_REMOTE_SERVICE_FILE" >/dev/null; then
  exit 0
fi
migration_dir=/root/.config/takyon/migration
migration_file="$migration_dir/database-url"
[[ "$(stat -c '%u:%g:%a' "$migration_dir")" == '0:0:700' ]] \
  || { echo 'root-only migration credential directory permissions invalid' >&2; exit 1; }
[[ -f "$migration_file" && ! -L "$migration_file" ]] \
  || { echo 'root-only migration credential missing' >&2; exit 1; }
[[ "$(stat -c '%u:%g:%a' "$migration_file")" == '0:0:600' ]] \
  || { echo 'root-only migration credential permissions invalid' >&2; exit 1; }
IFS= read -r migration_dsn <"$migration_file"
[[ "$migration_dsn" == postgres://* || "$migration_dsn" == postgresql://* ]] \
  || { echo 'root-only migration credential malformed' >&2; exit 1; }
export TAKYON_HOME=/opt/takyon/.takyon
export PYTHONUNBUFFERED=1
export PYTHONDONTWRITEBYTECODE=1
export PYTHONPATH="$TAKYON_REMOTE_RUNTIME"
export TAKYON_ENV=prod
export TAKYON_DB_BACKEND=postgres
export TAKYON_HOST_ROLE=operator
export TAKYON_SAFEBOX_URL="$TAKYON_REMOTE_SAFEBOX_URL"
export TAKYON_MIGRATION_DATABASE_URL="$migration_dsn"
unset migration_dsn
exec "$TAKYON_REMOTE_RUNTIME/.venv/bin/takyon-cli" migrate
REMOTE_MIGRATE
}

# The release singleton is authority state, not runtime configuration. Only the root-isolated
# migration DSN may change it; no operator service or Mac shell receives that credential.
remote_worker_release_fence() {
  local action="$1"
  local target_release_sha="$2"
  local previous_release_sha="${3:-}"
  case "$action" in
    activate|inspect|restore) ;;
    *) echo "invalid worker release fence action: $action" >&2; return 2 ;;
  esac
  [[ "$target_release_sha" =~ ^[0-9a-f]{40}$ ]] \
    || { echo "invalid target worker release SHA" >&2; return 2; }
  if [[ -n "$previous_release_sha" && ! "$previous_release_sha" =~ ^[0-9a-f]{40}$ ]]; then
    echo "invalid previous worker release SHA" >&2
    return 2
  fi

  ssh -i "$TAKYON_VPS_KEY" -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new "$TAKYON_VPS_HOST" \
    "exec env -i PATH=/usr/sbin:/usr/bin:/sbin:/bin HOME=/root \
      PYTHONPATH='$TAKYON_REMOTE_STAGED_RUNTIME' \
      '$TAKYON_REMOTE_STAGED_RUNTIME/.venv/bin/python' - '$action' '$target_release_sha' '$previous_release_sha'" <<'PY'
from __future__ import annotations

import json
import os
from pathlib import Path
import stat
import sys

import psycopg

from plugins.takyon.runtime_app import assert_takyon_pg_role


def read_migration_dsn() -> str:
    directory = Path('/root/.config/takyon/migration')
    path = directory / 'database-url'
    directory_info = directory.lstat()
    path_info = path.lstat()
    if (
        not stat.S_ISDIR(directory_info.st_mode)
        or stat.S_ISLNK(directory_info.st_mode)
        or (directory_info.st_uid, directory_info.st_gid, stat.S_IMODE(directory_info.st_mode))
        != (0, 0, 0o700)
    ):
        raise RuntimeError('root-only migration credential directory permissions invalid')
    if (
        not stat.S_ISREG(path_info.st_mode)
        or stat.S_ISLNK(path_info.st_mode)
        or (path_info.st_uid, path_info.st_gid, stat.S_IMODE(path_info.st_mode))
        != (0, 0, 0o600)
    ):
        raise RuntimeError('root-only migration credential permissions invalid')
    flags = os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0)
    fd = os.open(path, flags)
    try:
        opened = os.fstat(fd)
        if (opened.st_dev, opened.st_ino) != (path_info.st_dev, path_info.st_ino):
            raise RuntimeError('root-only migration credential changed while opening')
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(fd, 4096)
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > 16_384:
                raise RuntimeError('root-only migration credential is oversized')
        value = b''.join(chunks).decode('utf-8').strip()
    finally:
        os.close(fd)
    if not value.startswith(('postgres://', 'postgresql://')) or any(ch.isspace() for ch in value):
        raise RuntimeError('root-only migration credential malformed')
    return value


action, target_release_sha, previous_release_sha = sys.argv[1:4]
with psycopg.connect(read_migration_dsn(), autocommit=False, prepare_threshold=None) as conn:
    assert_takyon_pg_role(conn, 'migration')
    if action == 'activate':
        receipt = conn.execute(
            'select takyon_activate_worker_release(%s)', (target_release_sha,)
        ).fetchone()[0]
    elif action == 'restore':
        receipt = conn.execute(
            'select takyon_restore_worker_release(%s, %s)',
            (target_release_sha, previous_release_sha),
        ).fetchone()[0]
    else:
        active_release_sha = conn.execute(
            'select takyon_get_worker_active_release()'
        ).fetchone()[0]
        jobs = conn.execute(
            """
            select id::text, business_slug, kind, status, attempts,
                   required_release_sha, claimed_release_sha, coalesce(locked_by, '')
            from jobs
            where status = 'running'
               or (status = 'queued' and required_release_sha <> %s)
            order by created_at, id
            """,
            (target_release_sha,),
        ).fetchall()
        pools = conn.execute(
            """
            select pool_id, hostname, status, release_sha, lease_expires_at::text
            from worker_pools
            where status in ('joining', 'active')
              and lease_expires_at > now()
              and release_sha <> %s
            order by pool_id
            """,
            (target_release_sha,),
        ).fetchall()
        receipt = {
            'active_release_sha': active_release_sha,
            'jobs': [list(row) for row in jobs],
            'pools': [list(row) for row in pools],
            'ok': active_release_sha == target_release_sha and not jobs and not pools,
        }
    conn.commit()
print(json.dumps(receipt, default=str, separators=(',', ':'), sort_keys=True))
PY
}

activate_remote_worker_release() {
  worker_release_activation_receipt="$(
    remote_worker_release_fence activate "$TAKYON_DEPLOY_SOURCE_REVISION"
  )"
  worker_release_previous_sha="$(
    python3 -c 'import json,sys; print(json.load(sys.stdin)["previous_release_sha"])' \
      <<<"$worker_release_activation_receipt"
  )"
  [[ "$worker_release_previous_sha" =~ ^[0-9a-f]{40}$ ]] \
    || { echo "worker release activation returned an invalid previous SHA" >&2; return 1; }
  worker_release_fence_activated=1
  echo "worker release fence activated: $worker_release_activation_receipt"
}

verify_remote_worker_release_cutover() {
  local receipt
  receipt="$(remote_worker_release_fence inspect "$TAKYON_DEPLOY_SOURCE_REVISION")"
  if ! python3 -c 'import json,sys; raise SystemExit(0 if json.load(sys.stdin).get("ok") is True else 1)' \
    <<<"$receipt"; then
    echo "worker release cutover is not sealed to $TAKYON_DEPLOY_SOURCE_REVISION: $receipt" >&2
    return 1
  fi
  echo "worker release cutover verified: $receipt"
}

wait_for_remote_runtime_idle() {
  if ! ssh -i "$TAKYON_VPS_KEY" -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new "$TAKYON_VPS_HOST" \
    "grep -F -- 'TAKYON_DB_BACKEND=postgres' '$TAKYON_REMOTE_SERVICE_FILE' >/dev/null"; then
    return 0
  fi

  local deadline=$((SECONDS + TAKYON_DEPLOY_DRAIN_TIMEOUT_SECONDS))
  while true; do
    local probe summary
    probe="$(
      ssh -i "$TAKYON_VPS_KEY" -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new "$TAKYON_VPS_HOST" \
        "set -euo pipefail
        env TAKYON_HOME=/opt/takyon/.takyon HOME=/root PYTHONUNBUFFERED=1 TAKYON_DB_BACKEND=postgres TAKYON_HOST_ROLE=operator TAKYON_SAFEBOX_URL='$TAKYON_REMOTE_SAFEBOX_URL' \
          '$TAKYON_REMOTE_RUNTIME/.venv/bin/python' - <<'PY'
from plugins.takyon.core import load_takyon_env
from plugins.takyon.runtime_app import assert_takyon_pg_role, resolve_database_url
import json
import psycopg

load_takyon_env()
with psycopg.connect(resolve_database_url(plane='operator'), autocommit=True, prepare_threshold=None) as conn:
    assert_takyon_pg_role(conn, 'operator')
    with conn.cursor() as cur:
        cur.execute(
            \"\"\"
            SELECT id, business_slug, kind, status
            FROM business_work_requests
            WHERE status = 'running'
              AND NULLIF(updated_at, '')::timestamptz >= (NOW() - %s::interval)
            ORDER BY created_at, id
            \"\"\",
            (f\"$TAKYON_DEPLOY_ACTIVE_WORK_REQUEST_FRESHNESS_SECONDS seconds\",),
        )
        work_requests = list(cur.fetchall())
        cur.execute(
            \"\"\"
            SELECT id::text, business_slug, kind, status, attempts,
                   COALESCE(locked_by, '')
            FROM jobs
            WHERE status = 'running'
               OR (status = 'queued' AND attempts > 0)
            ORDER BY created_at, id
            \"\"\"
        )
        jobs = list(cur.fetchall())
print(f\"{len(work_requests)} {len(jobs)}\")
for row in work_requests:
    print('work_request ' + json.dumps(list(row), default=str, separators=(',', ':')))
for row in jobs:
    print('job ' + json.dumps(list(row), default=str, separators=(',', ':')))
PY"
    )"
    summary="${probe%%$'\n'*}"
    local running_work_requests blocking_jobs
    read -r running_work_requests blocking_jobs <<<"$summary"
    if [[ "$running_work_requests" == "0" \
      && "$blocking_jobs" == "0" ]]; then
      return 0
    fi
    if (( SECONDS >= deadline )); then
      echo "deploy cutover timed out: ${running_work_requests} recent running work request(s), ${blocking_jobs} running/previously-attempted job(s)" >&2
      echo "no job was killed or repinned; resolve the exact durable rows, then redeploy:" >&2
      printf '%s\n' "$probe" >&2
      return 1
    fi
    echo "waiting for sealed operator cutover: ${running_work_requests} recent running work request(s), ${blocking_jobs} running/previously-attempted job(s)" >&2
    sleep "$TAKYON_DEPLOY_DRAIN_POLL_SECONDS"
  done
}

preflight_remote_staged_runtime() {
  ssh -i "$TAKYON_VPS_KEY" -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new "$TAKYON_VPS_HOST" \
    "exec env \
      TAKYON_STAGED_RUNTIME='$TAKYON_REMOTE_STAGED_RUNTIME' \
      TAKYON_LIVE_RUNTIME='$TAKYON_REMOTE_RUNTIME' \
      TAKYON_REMOTE_HOME='$TAKYON_REMOTE_HOME' \
      TAKYON_DASHBOARD_UNIT_CANDIDATE='$remote_dashboard_candidate' \
      TAKYON_WORKER_UNIT_CANDIDATE='$remote_worker_candidate' \
      TAKYON_DOCKER_BROKER_UNIT_CANDIDATE='$remote_docker_broker_candidate' \
      TAKYON_OPERATOR_CLI_CANDIDATE='$remote_operator_cli_candidate' \
      TAKYON_CLAUDE_RELEASE_ROOT='$remote_sdk_root' \
      TAKYON_DEPLOY_SOURCE_REVISION='$TAKYON_DEPLOY_SOURCE_REVISION' \
      TAKYON_REMOTE_SAFEBOX_URL='$TAKYON_REMOTE_SAFEBOX_URL' \
      TAKYON_DENO_VERSION='$TAKYON_DENO_VERSION' \
      TAKYON_CLAUDE_AGENT_DOCKER_IMAGE='$TAKYON_CLAUDE_AGENT_DOCKER_IMAGE' \
      bash -s" < "$PREFLIGHT_STAGED_RUNTIME_SCRIPT"
}

OPERATOR_SERVICE_ENV_PINS=(
  TAKYON_STRICT_MODEL_ROLES=1
  TAKYON_MODEL=deepseek-v4-pro
  TAKYON_CLAUDE_AGENT_MODEL=deepseek-v4-pro
  ANTHROPIC_MODEL=deepseek-v4-pro
  ANTHROPIC_DEFAULT_OPUS_MODEL=deepseek-v4-pro
  ANTHROPIC_DEFAULT_SONNET_MODEL=deepseek-v4-pro
  ANTHROPIC_DEFAULT_HAIKU_MODEL=deepseek-v4-pro
  CLAUDE_CODE_SUBAGENT_MODEL=deepseek-v4-pro
  TAKYON_PRIMARY_AGENT_MAX_BUDGET_USD=5
  TAKYON_PRIMARY_AGENT_PER_CALL_MAX_BUDGET_USD=2
  TAKYON_OPERATOR_SESSION_MAX_COST_MICROUSD=2000000
  TAKYON_CLAUDE_SKILLS_PLUGIN=/opt/takyon/.takyon/runtime/claude-agent-sdk/current/plugin
  TAKYON_CLAUDE_SKILLS_MANIFEST=/opt/takyon/.takyon/runtime/claude-agent-sdk/current/plugin/approved-skills.json
  TAKYON_CLAUDE_NODE_RUNTIME=/opt/takyon/.takyon/runtime/claude-agent-sdk/current/node-runtime
  TAKYON_DISABLE_LEGACY_SKILL_SYNC=1
)

wait_for_remote_service_env() {
  local unit="$1"
  local health_url="$2"
  shift 2
  ssh -i "$TAKYON_VPS_KEY" -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new \
    "$TAKYON_VPS_HOST" bash -s -- \
    "$unit" "$TAKYON_DEPLOY_SERVICE_READY_TIMEOUT_SECONDS" "$health_url" "$@" <<'REMOTE_SERVICE_READY'
set -euo pipefail
unit="$1"
timeout_seconds="$2"
health_url="$3"
shift 3
if [[ "$health_url" == "-" ]]; then
  health_url=''
fi
deadline=$((SECONDS + timeout_seconds))
ready=0
stable_pid=''
stable_probes=0
env_candidate_pid=''
env_missing_probes=0
pid=0
fatal_reason=''
last_probe_reason='service has not started'

while (( SECONDS < deadline )); do
  probe_ok=1
  if systemctl is-failed --quiet "$unit"; then
    fatal_reason="$unit entered failed state"
    break
  fi
  if ! systemctl is-active --quiet "$unit"; then
    probe_ok=0
    last_probe_reason='service is not active'
    env_candidate_pid=''
    env_missing_probes=0
  fi
  pid="$(systemctl show -p MainPID --value "$unit" 2>/dev/null || true)"
  if ! [[ "$pid" =~ ^[1-9][0-9]*$ && -r "/proc/$pid/environ" ]]; then
    probe_ok=0
    last_probe_reason='MainPID environment is not readable yet'
    env_candidate_pid=''
    env_missing_probes=0
  fi
  if [[ "$probe_ok" == 1 ]]; then
    for expected in "$@"; do
      if ! grep -Fzqx -- "$expected" "/proc/$pid/environ" 2>/dev/null; then
        current_pid="$(systemctl show -p MainPID --value "$unit" 2>/dev/null || true)"
        if [[ "$current_pid" == "$pid" && -r "/proc/$pid/environ" ]]; then
          probe_ok=0
          last_probe_reason="process environment is not installed yet: $expected"
          if [[ "$pid" == "$env_candidate_pid" ]]; then
            env_missing_probes=$((env_missing_probes + 1))
          else
            env_candidate_pid="$pid"
            env_missing_probes=1
          fi
          if (( env_missing_probes >= 3 )); then
            fatal_reason="missing $unit process invariant after exec grace: $expected"
          fi
        else
          probe_ok=0
          last_probe_reason='MainPID changed during environment verification'
          env_candidate_pid=''
          env_missing_probes=0
        fi
        break
      fi
    done
  fi
  if [[ "$probe_ok" == 1 ]]; then
    env_candidate_pid=''
    env_missing_probes=0
  fi
  if [[ -z "$fatal_reason" && "$probe_ok" == 1 ]]; then
    if grep -zEq '^(TAKYON_MIGRATION_DATABASE_URL|MIGRATION_DATABASE_URL)=' \
        "/proc/$pid/environ" 2>/dev/null; then
      fatal_reason="migration credential present in $unit process environment"
    else
      grep_status=$?
      if [[ "$grep_status" != 1 ]]; then
        probe_ok=0
        last_probe_reason='MainPID environment changed during forbidden-key verification'
      fi
    fi
  fi
  if [[ -n "$fatal_reason" ]]; then
    break
  fi
  if [[ "$probe_ok" == 1 && -n "$health_url" ]] \
      && ! curl -fsS --connect-timeout 2 --max-time 3 "$health_url" >/dev/null 2>&1; then
    probe_ok=0
    last_probe_reason="health endpoint is not ready: $health_url"
  fi

  if [[ "$probe_ok" == 1 ]]; then
    if [[ "$pid" == "$stable_pid" ]]; then
      stable_probes=$((stable_probes + 1))
    else
      stable_pid="$pid"
      stable_probes=1
    fi
    if (( stable_probes >= 2 )); then
      ready=1
      break
    fi
  else
    stable_pid=''
    stable_probes=0
  fi
  sleep 1
done

if [[ "$ready" != 1 ]]; then
  echo "${fatal_reason:-$unit did not reach stable verified readiness: $last_probe_reason}" >&2
  systemctl show "$unit" -p ActiveState -p SubState -p MainPID >&2 || true
  if [[ -n "$health_url" ]]; then
    health_status="$(curl -sS -o /dev/null -w '%{http_code}' \
      --connect-timeout 2 --max-time 3 "$health_url" 2>/dev/null || true)"
    echo "$unit health status: ${health_status:-unreachable} ($health_url)" >&2
  fi
  pid="$(systemctl show -p MainPID --value "$unit" 2>/dev/null || true)"
  if [[ "$pid" =~ ^[1-9][0-9]*$ && -r "/proc/$pid/environ" ]]; then
    for expected in "$@"; do
      grep -Fzqx -- "$expected" "/proc/$pid/environ" 2>/dev/null \
        || echo "missing $unit process invariant: $expected" >&2
    done
    if grep -zEq '^(TAKYON_MIGRATION_DATABASE_URL|MIGRATION_DATABASE_URL)=' \
        "/proc/$pid/environ" 2>/dev/null; then
      echo "migration credential present in $unit process environment" >&2
    fi
  fi
  journalctl -u "$unit" --since '-2 minutes' --no-pager -n 80 >&2 || true
  exit 1
fi
REMOTE_SERVICE_READY
}

preflight_remote_staged_runtime
wait_for_remote_runtime_idle

takyon_prepare_runtime_rollback "$TAKYON_VPS_HOST" "$TAKYON_VPS_KEY"

operator_dashboard_stopped=1
TAKYON_VPS_HOST="$TAKYON_VPS_HOST" \
  TAKYON_VPS_KEY="$TAKYON_VPS_KEY" \
TAKYON_REMOTE_HOME="$TAKYON_REMOTE_HOME" \
TAKYON_STOP_CORE_SERVICES=1 \
  "$REPAIR_PRODUCT_RUNTIME_SCRIPT"

# The first drain happened while the old dashboard was still serving. Stop that canonical enqueue
# source, then recheck the shared queue state. Mac pools are never killed; the activation transaction
# fences their enqueue/claim race and marks every non-target pool draining.
wait_for_remote_runtime_idle

# Migrations run only after the old release has no running or previously-attempted work. Untouched
# queued rows are deliberately left for the migration-only activation transaction to repin
# atomically; idle old pools are marked draining in that same transaction. The release fence stays
# reversible until that privileged activation step seals the target SHA.
run_remote_migrations
activate_remote_worker_release
verify_remote_worker_release_cutover

operator_runtime_activation_started=1
takyon_begin_runtime_activation "$TAKYON_VPS_HOST" "$TAKYON_VPS_KEY"
ssh -i "$TAKYON_VPS_KEY" -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new "$TAKYON_VPS_HOST" \
  "set -euo pipefail
  cp -p '$TAKYON_REMOTE_SERVICE_FILE' '$remote_dashboard_backup'
  cp -p '$TAKYON_REMOTE_WORKER_SERVICE_FILE' '$remote_worker_backup'
  cp -p '$TAKYON_REMOTE_DOCKER_BROKER_SERVICE_FILE' '$remote_docker_broker_backup'
  if [[ -e /usr/local/bin/takyon-op ]]; then
    cp -p /usr/local/bin/takyon-op '$remote_operator_cli_backup'
    touch '$remote_operator_cli_existed_marker'
  fi
  test -d '$remote_sdk_release/plugin'
  test -d '$remote_sdk_release/node-runtime'
  install -d -m 0755 '$remote_sdk_root'
  if [[ -e '$remote_sdk_current' && ! -L '$remote_sdk_current' ]]; then
    echo 'refusing to replace non-symlink Claude SDK current path' >&2
    exit 1
  fi
  if [[ -L '$remote_sdk_current' ]]; then
    readlink '$remote_sdk_current' > '$remote_sdk_current_backup'
    touch '$remote_sdk_current_existed_marker'
  fi
  if [[ -L '$TAKYON_REMOTE_HOME/skills' ]]; then
    echo 'refusing to replace symlinked operator skills tree' >&2
    exit 1
  fi
  if [[ -d '$TAKYON_REMOTE_HOME/skills' ]]; then
    cp -a '$TAKYON_REMOTE_HOME/skills' '$remote_skills_backup'
    touch '$remote_skills_existed_marker'
  fi
  touch '$remote_skills_activation_marker'
  touch '$remote_release_files_marker'
  install -o root -g root -m 0644 '$remote_dashboard_candidate' '$TAKYON_REMOTE_SERVICE_FILE'
  install -o root -g root -m 0644 '$remote_worker_candidate' '$TAKYON_REMOTE_WORKER_SERVICE_FILE'
  install -o root -g root -m 0644 '$remote_docker_broker_candidate' '$TAKYON_REMOTE_DOCKER_BROKER_SERVICE_FILE'
  install -o root -g root -m 0750 '$remote_operator_cli_candidate' /usr/local/bin/takyon-op"
takyon_activate_staged_runtime "$TAKYON_VPS_HOST" "$TAKYON_VPS_KEY" "$TAKYON_DEPLOY_SOURCE_REVISION"
ssh -i "$TAKYON_VPS_KEY" -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new "$TAKYON_VPS_HOST" \
  "set -euo pipefail
  ln -s 'releases/$TAKYON_DEPLOY_SOURCE_REVISION' '$remote_sdk_current.next'
  mv -Tf '$remote_sdk_current.next' '$remote_sdk_current'
  touch '$remote_sdk_activation_marker'
  rm -rf '$TAKYON_REMOTE_HOME/skills'
  test -r '$remote_sdk_current/plugin/approved-skills.json'
  test -r '$remote_sdk_current/node-runtime/node_modules/@anthropic-ai/claude-agent-sdk/sdk.mjs'"

if [[ "$TAKYON_FINALIZE_STRIPE_LIVE" == "1" ]]; then
  TAKYON_VPS_HOST="$TAKYON_VPS_HOST" \
  TAKYON_VPS_KEY="$TAKYON_VPS_KEY" \
  TAKYON_REMOTE_RUNTIME="$TAKYON_REMOTE_RUNTIME" \
    "$RETIRE_STRIPE_SANDBOX_SCRIPT"
fi

ssh -i "$TAKYON_VPS_KEY" -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new "$TAKYON_VPS_HOST" \
  "set -euo pipefail
  grep -F -- '--tui' '$TAKYON_REMOTE_SERVICE_FILE' >/dev/null
  systemctl daemon-reload
  systemctl enable takyon-docker-broker.service >/dev/null
  systemctl restart takyon-docker-broker.service
  systemctl is-active --quiet takyon-docker-broker.service
  systemctl restart takyon-dashboard.service
  if grep -Eq '^[[:space:]]*(export[[:space:]]+)?(TAKYON_MIGRATION_DATABASE_URL|MIGRATION_DATABASE_URL)=' \
      /opt/takyon/.takyon/.env /opt/takyon/secrets/.env 2>/dev/null; then
    echo 'migration credential remains in a service-readable env file' >&2
    exit 1
  fi
  test \"\$(stat -c '%u:%g:%a' /root/.config/takyon/migration)\" = '0:0:700'
  test \"\$(stat -c '%u:%g:%a' /root/.config/takyon/migration/database-url)\" = '0:0:600'"

wait_for_remote_service_env \
  takyon-dashboard.service \
  http://127.0.0.1:9119/healthz \
  "${OPERATOR_SERVICE_ENV_PINS[@]}"

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
      # The dashboard root may serve directly or redirect into the current
      # auth/bootstrap entrypoint depending on session state and proxy mode.
      200|301|302|303|307|308) ;;
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

operator_smoke_succeeded=0
for attempt in {1..12}; do
  curl_status=0
  if curl -fsS -o /dev/null \
    --connect-timeout "$TAKYON_SMOKE_CONNECT_TIMEOUT" \
    --max-time "$TAKYON_SMOKE_MAX_TIME" \
    "$TAKYON_SMOKE_HOST" >/dev/null 2>&1; then
    operator_smoke_succeeded=1
    break
  else
    curl_status=$?
  fi
  if [[ "$curl_status" == "6" || "$curl_status" == "7" || "$curl_status" == "28" ]]; then
    break
  fi
  sleep 5
done

if [[ "$operator_smoke_succeeded" != "1" ]]; then
  for attempt in {1..12}; do
    if run_remote_smoke; then
      operator_smoke_succeeded=1
      break
    fi
    sleep 5
  done
fi

if [[ "$operator_smoke_succeeded" != "1" ]]; then
  run_remote_smoke
fi

# Prove the customer-facing dashboard before opening the queue. Until this point rollback can
# restore the previous release fence because no target worker has been allowed to attempt a job.
ssh -i "$TAKYON_VPS_KEY" -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new "$TAKYON_VPS_HOST" \
  "set -euo pipefail
  systemctl enable takyon-worker.service >/dev/null
  systemctl restart takyon-worker.service"

wait_for_remote_service_env \
  takyon-worker.service \
  - \
  "${OPERATOR_SERVICE_ENV_PINS[@]}"

operator_runtime_activation_started=0
operator_services_activated=1
takyon_finalize_runtime_release "$TAKYON_VPS_HOST" "$TAKYON_VPS_KEY"
ssh -i "$TAKYON_VPS_KEY" -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new "$TAKYON_VPS_HOST" \
  "set -euo pipefail
  find '$remote_sdk_root/releases' -mindepth 1 -maxdepth 1 -type d \
    ! -name '$TAKYON_DEPLOY_SOURCE_REVISION' -exec rm -rf {} +
  test ! -e '$TAKYON_REMOTE_HOME/skills'"
worker_release_fence_activated=0
