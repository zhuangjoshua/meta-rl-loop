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
TAKYON_CLAUDE_AGENT_DOCKER_IMAGE="${TAKYON_CLAUDE_AGENT_DOCKER_IMAGE:-${TERMINAL_DOCKER_IMAGE:-nikolaik/python-nodejs:python3.11-nodejs20}}"
TAKYON_REQUIRE_XURL_AUTH="${TAKYON_REQUIRE_XURL_AUTH:-0}"
TAKYON_DENO_VERSION="${TAKYON_DENO_VERSION:-2.8.3}"

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

if [[ ! -x "$ISOLATE_OPERATOR_MIGRATION_DSN_SCRIPT" ]]; then
  echo "operator migration credential isolator not executable: $ISOLATE_OPERATOR_MIGRATION_DSN_SCRIPT" >&2
  exit 1
fi

if [[ ! -f "$TAKYON_VPS_KEY" ]]; then
  echo "deploy key not found: $TAKYON_VPS_KEY" >&2
  exit 1
fi

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

scp -i "$TAKYON_VPS_KEY" -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new \
  "$SERVICE_FILE" \
  "$TAKYON_VPS_HOST:$TAKYON_REMOTE_SERVICE_FILE"

scp -i "$TAKYON_VPS_KEY" -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new \
  "$WORKER_SERVICE_FILE" \
  "$TAKYON_VPS_HOST:$TAKYON_REMOTE_WORKER_SERVICE_FILE"

scp -i "$TAKYON_VPS_KEY" -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new \
  "$DOCKER_BROKER_SERVICE_FILE" \
  "$TAKYON_VPS_HOST:$TAKYON_REMOTE_DOCKER_BROKER_SERVICE_FILE"

scp -i "$TAKYON_VPS_KEY" -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new \
  "$OPERATOR_CLI_FILE" \
  "$TAKYON_VPS_HOST:/tmp/takyon-op"

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
      TAKYON_REMOTE_RUNTIME='$TAKYON_REMOTE_RUNTIME' \
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
            FROM business_work_requests
            WHERE status IN ('queued', 'running')
              AND NULLIF(updated_at, '')::timestamptz >= (NOW() - %s::interval)
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

wait_for_remote_runtime_idle

# Migrations are additive and run against the newly rsynced tree. Apply them while the current
# services are still healthy, then enter the short stop/restart window. A slow advisory-lock wait
# must never turn into operator-dashboard downtime.
run_remote_migrations

TAKYON_VPS_HOST="$TAKYON_VPS_HOST" \
  TAKYON_VPS_KEY="$TAKYON_VPS_KEY" \
TAKYON_REMOTE_HOME="$TAKYON_REMOTE_HOME" \
TAKYON_STOP_CORE_SERVICES=1 \
  "$REPAIR_PRODUCT_RUNTIME_SCRIPT"

if [[ "$TAKYON_FINALIZE_STRIPE_LIVE" == "1" ]]; then
  TAKYON_VPS_HOST="$TAKYON_VPS_HOST" \
  TAKYON_VPS_KEY="$TAKYON_VPS_KEY" \
  TAKYON_REMOTE_RUNTIME="$TAKYON_REMOTE_RUNTIME" \
    "$RETIRE_STRIPE_SANDBOX_SCRIPT"
fi

ssh -i "$TAKYON_VPS_KEY" -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new "$TAKYON_VPS_HOST" \
  "set -euo pipefail
  grep -F -- '--tui' '$TAKYON_REMOTE_SERVICE_FILE' >/dev/null
  systemctl enable docker >/dev/null
  systemctl start docker
  systemctl is-active --quiet docker
  docker version >/dev/null
  command -v xurl >/dev/null 2>&1 || [ -x /root/.local/bin/xurl ]
  command -v deno >/dev/null 2>&1
  test \"\$(deno --version | awk 'NR==1 {print \$2}')\" = '$TAKYON_DENO_VERSION'
  command -v systemd-run >/dev/null 2>&1
  # The tracked units run as the dedicated non-root 'takyon' user. Docker authority now lives in
  # takyon-docker-broker.service only, so the user must NOT remain in the docker group.
  if ! id -u takyon >/dev/null 2>&1; then
    useradd --system --user-group --home-dir /opt/takyon --shell /usr/sbin/nologin takyon
  fi
  if id -nG takyon | grep -qw docker; then
    gpasswd -d takyon docker >/dev/null 2>&1 || deluser takyon docker >/dev/null 2>&1 || true
  fi
  chown takyon:takyon /opt/takyon
  chown -R takyon:takyon '$TAKYON_REMOTE_HOME'
  if [ -d /opt/takyon/secrets ]; then chown -R takyon:takyon /opt/takyon/secrets; fi
  # Service HOME moved /root -> /opt/takyon (ProtectHome=true hides /root): migrate existing xurl
  # auth state once, then keep it owned by the service user.
  if [ -e /root/.xurl ] && [ ! -e /opt/takyon/.xurl ]; then cp -a /root/.xurl /opt/takyon/.xurl; fi
  if [ -e /opt/takyon/.xurl ]; then chown -R takyon:takyon /opt/takyon/.xurl; fi
  install -o root -g root -m 0750 /tmp/takyon-op /usr/local/bin/takyon-op
  rm -f /tmp/takyon-op
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
	  for key in R2_S3_ENDPOINT R2_BUCKET; do
	    if ! grep -q \"^\${key}=\" /opt/takyon/.takyon/.env 2>/dev/null \
	      && ! grep -q \"^\${key}=\" /opt/takyon/secrets/.env 2>/dev/null; then
	      echo \"\${key} missing from operator env; product edge publish will not mirror to R2\" >&2
	      exit 1
	    fi
	  done
	  if grep -q '^R2_S3_ACCESS_KEY_ID=' /opt/takyon/.takyon/.env /opt/takyon/secrets/.env 2>/dev/null \
	    || grep -q '^R2_S3_SECRET_ACCESS_KEY=' /opt/takyon/.takyon/.env /opt/takyon/secrets/.env 2>/dev/null; then
	    echo 'R2 write credentials must live only on Safebox, not on the operator host' >&2
	    exit 1
	  fi
  # A /usr/local/bin/xurl SYMLINK into /root/.local is unreachable for the service under
  # ProtectHome=true — replace it with a real copy once.
  if [ -L /usr/local/bin/xurl ] && [ -x /root/.local/bin/xurl ]; then
    install -m 0755 /root/.local/bin/xurl /usr/local/bin/xurl
  fi
  if ! docker image inspect '$TAKYON_CLAUDE_AGENT_DOCKER_IMAGE' >/dev/null 2>&1; then
    docker pull '$TAKYON_CLAUDE_AGENT_DOCKER_IMAGE'
  fi
  docker run --rm --entrypoint node '$TAKYON_CLAUDE_AGENT_DOCKER_IMAGE' --version >/dev/null
  if grep -F -- 'TAKYON_STORAGE_BACKEND=supabase_s3' '$TAKYON_REMOTE_SERVICE_FILE' >/dev/null \
    || grep -F -- 'TAKYON_STORAGE_BACKEND=supabase_s3' '$TAKYON_REMOTE_WORKER_SERVICE_FILE' >/dev/null; then
    if ! '$TAKYON_REMOTE_RUNTIME/.venv/bin/python' -c 'import boto3' >/dev/null 2>&1; then
      '$TAKYON_REMOTE_RUNTIME/.venv/bin/python' -m pip install 'boto3==1.42.89'
    fi
    env TAKYON_HOME=/opt/takyon/.takyon HOME=/root PYTHONUNBUFFERED=1 TAKYON_STORAGE_BACKEND=supabase_s3 TAKYON_HOST_ROLE=operator TAKYON_SAFEBOX_URL='$TAKYON_REMOTE_SAFEBOX_URL' \
      '$TAKYON_REMOTE_RUNTIME/.venv/bin/python' - <<'PY'
from plugins.takyon.core import load_takyon_env
from plugins.takyon import storage

load_takyon_env()
backend = storage.get_storage_backend()
if getattr(backend, 'name', '') != 'supabase_s3':
    raise SystemExit('unexpected storage backend: %r' % (getattr(backend, 'name', ''),))
PY
  fi
  if ! (cd '$TAKYON_REMOTE_RUNTIME' && '$TAKYON_REMOTE_RUNTIME/.venv/bin/python' - <<'PY'
from tools.lazy_deps import feature_missing

raise SystemExit(1 if feature_missing('image.logo_postprocess') else 0)
PY
  )
  then
    '$TAKYON_REMOTE_RUNTIME/.venv/bin/python' -m pip install 'Pillow>=10.4,<12' 'numpy==2.4.3'
  fi
  python3 -m compileall -q '$TAKYON_REMOTE_RUNTIME/plugins/takyon' '$TAKYON_REMOTE_RUNTIME/takyon_cli' '$TAKYON_REMOTE_RUNTIME/tui_gateway'
  env TAKYON_HOME='$TAKYON_REMOTE_HOME' HOME=/opt/takyon TAKYON_FORCE_RESTORE_BUNDLED_SKILLS=1 \
    '$TAKYON_REMOTE_RUNTIME/.venv/bin/python' - <<'PY'
from tools.skills_sync import sync_skills

result = sync_skills(quiet=False)
if result.get('user_modified'):
    raise SystemExit(f\"bundled skill sync left user-modified entries behind: {result['user_modified']}\")
PY
  env TAKYON_HOME='$TAKYON_REMOTE_HOME' HOME=/root PYTHONUNBUFFERED=1 TAKYON_HOST_ROLE=operator TAKYON_SAFEBOX_URL='$TAKYON_REMOTE_SAFEBOX_URL' \
    '$TAKYON_REMOTE_RUNTIME/.venv/bin/python' '$TAKYON_REMOTE_RUNTIME/scripts/verify-supabase-auth-runtime.py'
  systemctl stop takyon-activation-broker.service >/dev/null 2>&1 || true
  systemctl disable takyon-activation-broker.service >/dev/null 2>&1 || true
  rm -f /etc/systemd/system/takyon-activation-broker.service
  for unit_file in '$TAKYON_REMOTE_SERVICE_FILE' '$TAKYON_REMOTE_WORKER_SERVICE_FILE'; do
    grep -Fx -- 'Environment=TAKYON_STRICT_MODEL_ROLES=1' "\$unit_file" >/dev/null
    grep -Fx -- 'Environment=TAKYON_MODEL=gpt-5.5' "\$unit_file" >/dev/null
    grep -Fx -- 'Environment=TAKYON_CLAUDE_AGENT_MODEL=deepseek-v4-pro' "\$unit_file" >/dev/null
    grep -Fx -- 'Environment=ANTHROPIC_MODEL=deepseek-v4-pro' "\$unit_file" >/dev/null
    grep -Fx -- 'Environment=ANTHROPIC_DEFAULT_OPUS_MODEL=deepseek-v4-pro' "\$unit_file" >/dev/null
    grep -Fx -- 'Environment=ANTHROPIC_DEFAULT_SONNET_MODEL=deepseek-v4-pro' "\$unit_file" >/dev/null
    grep -Fx -- 'Environment=ANTHROPIC_DEFAULT_HAIKU_MODEL=deepseek-v4-pro' "\$unit_file" >/dev/null
    grep -Fx -- 'Environment=CLAUDE_CODE_SUBAGENT_MODEL=deepseek-v4-pro' "\$unit_file" >/dev/null
  done
  '$TAKYON_REMOTE_RUNTIME/.venv/bin/python' - <<'PY'
from pathlib import Path
import yaml

data = yaml.safe_load(Path('/opt/takyon/.takyon/config.yaml').read_text()) or {}
model = data.get('model') or {}
expected = {
    'provider': 'custom',
    'base_url': 'https://api.openai.com/v1',
    'api_mode': 'codex_responses',
    'default': 'gpt-5.5',
    'claude_agent_default': 'deepseek-v4-pro',
}
wrong = {key: model.get(key) for key, value in expected.items() if model.get(key) != value}
if wrong:
    raise SystemExit(f'operator model config violates strict role contract: {wrong}')
if data.get('fallback_model') or data.get('fallback_providers'):
    raise SystemExit('operator model fallback config must be absent')
PY
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

for attempt in {1..12}; do
  curl_status=0
  if curl -fsS -o /dev/null \
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
