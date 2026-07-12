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
TAKYON_DEPLOY_WORKER_STOP_GRACE_SECONDS="${TAKYON_DEPLOY_WORKER_STOP_GRACE_SECONDS:-960}"
TAKYON_CLAUDE_AGENT_DOCKER_IMAGE="${TAKYON_CLAUDE_AGENT_DOCKER_IMAGE:-${TERMINAL_DOCKER_IMAGE:-nikolaik/python-nodejs:python3.11-nodejs20}}"
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
remote_skills_preflight_home="$TAKYON_REMOTE_RELEASE_META/skills-preflight-home"
remote_skills_backup="$TAKYON_REMOTE_RELEASE_META/backups/home-skills"
remote_skills_existed_marker="$TAKYON_REMOTE_RELEASE_META/home-skills-existed"
remote_skills_activation_marker="$TAKYON_REMOTE_RELEASE_META/home-skills-installed"

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
  TAKYON_DEPLOY_WORKER_STOP_GRACE_SECONDS; do
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

restore_operator_services_on_failure() {
  local exit_status="$?"
  local operator_rollback_ready=1
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
    if [[ "$operator_rollback_ready" == "1" ]]; then
      ssh -i "$TAKYON_VPS_KEY" -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new "$TAKYON_VPS_HOST" \
        "set +e
        systemctl daemon-reload
        if [[ '$operator_dashboard_stopped' == '1' ]]; then
          systemctl restart takyon-docker-broker.service
          systemctl restart takyon-dashboard.service
        fi
        if grep -F -- 'TAKYON_DB_BACKEND=postgres' '$TAKYON_REMOTE_SERVICE_FILE' >/dev/null; then
          systemctl restart takyon-worker.service
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
    # SIGTERM tells WorkerPool to stop claiming immediately and join every in-flight lane. Give a
    # full 900-second product task an additional minute to finish instead of letting systemd's old
    # 120-second stop ceiling SIGKILL and requeue it.
    systemctl set-property --runtime takyon-worker.service TimeoutStopUSec='${TAKYON_DEPLOY_WORKER_STOP_GRACE_SECONDS}s'
    systemctl stop takyon-worker.service
    systemctl is-active --quiet takyon-worker.service && exit 1 || true"
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

wait_for_remote_runtime_idle() {
  if ! ssh -i "$TAKYON_VPS_KEY" -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new "$TAKYON_VPS_HOST" \
    "grep -F -- 'TAKYON_DB_BACKEND=postgres' '$TAKYON_REMOTE_SERVICE_FILE' >/dev/null"; then
    return 0
  fi

  local deadline=$((SECONDS + TAKYON_DEPLOY_DRAIN_TIMEOUT_SECONDS))
  while true; do
    local counts
    counts="$(
      ssh -i "$TAKYON_VPS_KEY" -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new "$TAKYON_VPS_HOST" \
        "set -euo pipefail
        env TAKYON_HOME=/opt/takyon/.takyon HOME=/root PYTHONUNBUFFERED=1 TAKYON_DB_BACKEND=postgres TAKYON_HOST_ROLE=operator TAKYON_SAFEBOX_URL='$TAKYON_REMOTE_SAFEBOX_URL' \
          '$TAKYON_REMOTE_RUNTIME/.venv/bin/python' - <<'PY'
from plugins.takyon.core import load_takyon_env
from plugins.takyon.runtime_app import assert_takyon_pg_role, resolve_database_url
import psycopg

load_takyon_env()
with psycopg.connect(resolve_database_url(plane='operator'), autocommit=True, prepare_threshold=None) as conn:
    assert_takyon_pg_role(conn, 'operator')
    with conn.cursor() as cur:
        cur.execute(
            \"\"\"
            SELECT COUNT(*)
            FROM business_work_requests AS work_request
            WHERE work_request.status = 'running'
              AND NULLIF(work_request.updated_at, '')::timestamptz >= (NOW() - %s::interval)
              -- A live Mac worker is outside the VPS restart boundary. Ignore its mirrored work
              -- request only when no other queued/running execution for that request has an
              -- unknown or non-Mac owner. Missing links and ambiguous owners remain counted.
              AND NOT (
                  EXISTS (
                      SELECT 1
                      FROM jobs AS mac_job
                      WHERE mac_job.payload->>'work_request_id' = work_request.id
                        AND mac_job.status = 'running'
                        AND COALESCE(mac_job.locked_by, '') LIKE 'mac-operator-%%'
                  )
                  AND NOT EXISTS (
                      SELECT 1
                      FROM jobs AS other_job
                      WHERE other_job.payload->>'work_request_id' = work_request.id
                        AND other_job.status = 'running'
                        AND (
                            COALESCE(other_job.locked_by, '') NOT LIKE 'mac-operator-%%'
                        )
                  )
              )
            \"\"\",
            (f\"$TAKYON_DEPLOY_ACTIVE_WORK_REQUEST_FRESHNESS_SECONDS seconds\",),
        )
        work_requests = int(cur.fetchone()[0] or 0)
        # Restarting the operator VPS services cannot terminate a job claimed by an operator Mac;
        # those workers execute from their own local runtime trees. Drain only claims that may live
        # in the target-host process we are about to restart, while still failing closed for empty or
        # unknown owner labels.
        cur.execute(
            \"\"\"
            SELECT COUNT(*)
            FROM jobs
            WHERE status = 'running'
              AND COALESCE(locked_by, '') NOT LIKE 'mac-operator-%'
            \"\"\"
        )
        worker_jobs = int(cur.fetchone()[0] or 0)
print(f\"{work_requests} {worker_jobs}\")
PY"
    )"
    local queued_or_running_work_requests="${counts%% *}"
    local running_worker_jobs="${counts##* }"
    if [[ "$queued_or_running_work_requests" == "0" && "$running_worker_jobs" == "0" ]]; then
      return 0
    fi
    if (( SECONDS >= deadline )); then
      echo "deploy drain timed out with ${queued_or_running_work_requests} active work request(s) and ${running_worker_jobs} running worker job(s)" >&2
      return 1
    fi
    echo "waiting for operator runtime to go idle: ${queued_or_running_work_requests} active work request(s), ${running_worker_jobs} running worker job(s)" >&2
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
      TAKYON_SKILLS_PREFLIGHT_HOME='$remote_skills_preflight_home' \
      TAKYON_REMOTE_SAFEBOX_URL='$TAKYON_REMOTE_SAFEBOX_URL' \
      TAKYON_DENO_VERSION='$TAKYON_DENO_VERSION' \
      TAKYON_CLAUDE_AGENT_DOCKER_IMAGE='$TAKYON_CLAUDE_AGENT_DOCKER_IMAGE' \
      bash -s" < "$PREFLIGHT_STAGED_RUNTIME_SCRIPT"
}

preflight_remote_staged_runtime
wait_for_remote_runtime_idle

# Migrations are additive and run against the newly rsynced tree after the VPS worker has stopped
# accepting work. The dashboard remains healthy until the short stop/restart cutover.
run_remote_migrations

takyon_prepare_runtime_rollback "$TAKYON_VPS_HOST" "$TAKYON_VPS_KEY"

operator_dashboard_stopped=1
TAKYON_VPS_HOST="$TAKYON_VPS_HOST" \
  TAKYON_VPS_KEY="$TAKYON_VPS_KEY" \
TAKYON_REMOTE_HOME="$TAKYON_REMOTE_HOME" \
TAKYON_STOP_CORE_SERVICES=1 \
  "$REPAIR_PRODUCT_RUNTIME_SCRIPT"

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
  env PYTHONPATH='$TAKYON_REMOTE_RUNTIME' TAKYON_HOME='$TAKYON_REMOTE_HOME' HOME=/opt/takyon \
    TAKYON_FORCE_RESTORE_BUNDLED_SKILLS=1 '$TAKYON_REMOTE_RUNTIME/.venv/bin/python' - <<'PY'
from tools.skills_sync import sync_skills

result = sync_skills(quiet=False)
if not result.get('total_bundled'):
    raise SystemExit('activated runtime contains no bundled skills')
if result.get('user_modified'):
    raise SystemExit(f\"bundled skill sync left user-modified entries behind: {result['user_modified']}\")
PY"

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
  systemctl is-active --quiet takyon-dashboard.service
  if grep -F -- 'TAKYON_DB_BACKEND=postgres' '$TAKYON_REMOTE_SERVICE_FILE' >/dev/null; then
    systemctl enable takyon-worker.service >/dev/null
    systemctl restart takyon-worker.service
    systemctl is-active --quiet takyon-worker.service
  fi
  if grep -Eq '^[[:space:]]*(export[[:space:]]+)?(TAKYON_MIGRATION_DATABASE_URL|MIGRATION_DATABASE_URL)=' \
      /opt/takyon/.takyon/.env /opt/takyon/secrets/.env 2>/dev/null; then
    echo 'migration credential remains in a service-readable env file' >&2
    exit 1
  fi
  test \"\$(stat -c '%u:%g:%a' /root/.config/takyon/migration)\" = '0:0:700'
  test \"\$(stat -c '%u:%g:%a' /root/.config/takyon/migration/database-url)\" = '0:0:600'
  for unit in takyon-dashboard.service takyon-worker.service; do
    pid=\$(systemctl show -p MainPID --value "\$unit")
    [ "\$pid" != 0 ]
    process_env=\$(tr '\\000' '\\n' < "/proc/\$pid/environ")
    grep -Fx -- 'TAKYON_STRICT_MODEL_ROLES=1' <<<"\$process_env" >/dev/null
    grep -Fx -- 'TAKYON_MODEL=gpt-5.5' <<<"\$process_env" >/dev/null
    grep -Fx -- 'TAKYON_CLAUDE_AGENT_MODEL=deepseek-v4-pro' <<<"\$process_env" >/dev/null
    grep -Fx -- 'ANTHROPIC_MODEL=deepseek-v4-pro' <<<"\$process_env" >/dev/null
    grep -Fx -- 'ANTHROPIC_DEFAULT_OPUS_MODEL=deepseek-v4-pro' <<<"\$process_env" >/dev/null
    grep -Fx -- 'ANTHROPIC_DEFAULT_SONNET_MODEL=deepseek-v4-pro' <<<"\$process_env" >/dev/null
    grep -Fx -- 'ANTHROPIC_DEFAULT_HAIKU_MODEL=deepseek-v4-pro' <<<"\$process_env" >/dev/null
    grep -Fx -- 'CLAUDE_CODE_SUBAGENT_MODEL=deepseek-v4-pro' <<<"\$process_env" >/dev/null
    if grep -Eq '^(TAKYON_MIGRATION_DATABASE_URL|MIGRATION_DATABASE_URL)=' <<<"\$process_env"; then
      echo "migration credential present in \$unit process environment" >&2
      exit 1
    fi
  done"

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
    "$TAKYON_SMOKE_HOST" >/dev/null; then
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

operator_runtime_activation_started=0
operator_services_activated=1
takyon_finalize_runtime_release "$TAKYON_VPS_HOST" "$TAKYON_VPS_KEY"
