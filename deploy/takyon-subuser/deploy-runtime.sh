#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
TAKYON_VPS_HOST_EXPLICIT="${TAKYON_VPS_HOST+x}"
RUNTIME_DIR="$ROOT_DIR/hermes-agent-main"
SEED_XURL_AUTH_SCRIPT="$ROOT_DIR/deploy/shared/seed-xurl-auth.sh"
SERVICE_FILE="$ROOT_DIR/deploy/takyon-subuser/takyon-subuser.service"
ENSURE_DENO_SCRIPT="$ROOT_DIR/deploy/shared/ensure-deno.sh"
VALIDATE_AUTHORITY_ENV_SCRIPT="$ROOT_DIR/deploy/shared/validate-authority-env.sh"
REMOVE_STRIPE_AUTHORITY_ENV_SCRIPT="$ROOT_DIR/deploy/shared/remove-stripe-authority-env.py"
VERIFY_SUBUSER_RUNTIME_SURFACE_SCRIPT="$ROOT_DIR/deploy/shared/verify-subuser-runtime-surface.py"
WEB_BUILD_SCRIPT="$ROOT_DIR/deploy/shared/build-web-locked.sh"
RUNTIME_RELEASE_SCRIPT="$ROOT_DIR/deploy/shared/runtime-release.sh"
VERIFY_SUPABASE_AUTH_SCRIPT="$RUNTIME_DIR/scripts/verify-supabase-auth-runtime.py"
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
TAKYON_REMOTE_SAFEBOX_URL="${TAKYON_REMOTE_SAFEBOX_URL:-http://10.116.0.2:8000}"
TAKYON_RUN_WEB_BUILD="${TAKYON_RUN_WEB_BUILD:-1}"
TAKYON_APPLY_CADDY="${TAKYON_APPLY_CADDY:-0}"
TAKYON_SYNC_PRODUCT_SITES="${TAKYON_SYNC_PRODUCT_SITES:-1}"
TAKYON_SYNC_PRODUCT_SOURCE_CACHE="${TAKYON_SYNC_PRODUCT_SOURCE_CACHE:-1}"
TAKYON_RUN_DB_MIGRATIONS="${TAKYON_RUN_DB_MIGRATIONS:-0}"
# 0 only for a replica whose app DSN resolves through the safebox authority (no local DSN by
# design — takyon-subuser-2); see deploy/shared/validate-authority-env.sh.
TAKYON_REQUIRE_APP_DATABASE_URL="${TAKYON_REQUIRE_APP_DATABASE_URL:-1}"
TAKYON_DENO_VERSION="${TAKYON_DENO_VERSION:-2.8.3}"
TAKYON_HEALTH_WAIT_SECONDS="${TAKYON_HEALTH_WAIT_SECONDS:-90}"
TAKYON_SUBUSER_FANOUT_CHILD="${TAKYON_SUBUSER_FANOUT_CHILD:-0}"
TAKYON_SUBUSER_DEPLOY_PHASE="${TAKYON_SUBUSER_DEPLOY_PHASE:-full}"
TAKYON_SUBUSER_IS_PRIMARY="${TAKYON_SUBUSER_IS_PRIMARY:-0}"
TAKYON_SUBUSER_CANONICAL_PRIMARY_HOST="root@134.209.123.8"
TAKYON_SUBUSER_CANONICAL_REPLICA_HOST="root@206.81.10.173"
TAKYON_SUBUSER_PRIMARY_HOST="${TAKYON_SUBUSER_PRIMARY_HOST:-$TAKYON_SUBUSER_CANONICAL_PRIMARY_HOST}"
TAKYON_SUBUSER_REPLICA_HOSTS="${TAKYON_SUBUSER_REPLICA_HOSTS:-root@206.81.10.173}"
TAKYON_SUBUSER_LB_LIVE="${TAKYON_SUBUSER_LB_LIVE:-0}"
TAKYON_SUBUSER_ALLOW_PRIMARY_HARD_RESTART="${TAKYON_SUBUSER_ALLOW_PRIMARY_HARD_RESTART:-0}"

# Build and seal one revision-pinned runtime before fanout, then keep the shared home-level lock
# through both replicas' staging, activation, and health verification. Children inherit this
# exact artifact and cannot rebuild or read a mutable worktree.
if [[ "${TAKYON_DEPLOY_LOCK_HELD:-0}" != "1" ]]; then
  if [[ "$TAKYON_RUN_WEB_BUILD" != "1" ]]; then
    echo "refusing unlocked sub-user deploy: TAKYON_RUN_WEB_BUILD=0 is internal-only" >&2
    exit 1
  fi
  if [[ ! -f "$WEB_BUILD_SCRIPT" ]]; then
    echo "web build helper not found: $WEB_BUILD_SCRIPT" >&2
    exit 1
  fi
  exec bash "$WEB_BUILD_SCRIPT" "$RUNTIME_DIR" -- "$ROOT_DIR/deploy/takyon-subuser/deploy-runtime.sh" "$@"
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
  echo "sub-user deploy callback is not running entirely from the immutable repository artifact" >&2
  exit 1
fi

if [[ ! -f "$RUNTIME_RELEASE_SCRIPT" ]]; then
  echo "runtime release helper not found: $RUNTIME_RELEASE_SCRIPT" >&2
  exit 1
fi
# shellcheck disable=SC1090
source "$RUNTIME_RELEASE_SCRIPT"
takyon_runtime_release_init "$TAKYON_REMOTE_RUNTIME" "$TAKYON_DEPLOY_SOURCE_REVISION"

# Every outer invocation stages the exact artifact on the complete production sub-user plane before
# activating anything. The non-serving replica activates first and is health-gated before the live
# direct-primary enters its bounded stop/local-copy/restart window. Once Cloudflare is deliberately cut
# over to the DO LB, this rail fails closed until the tracked LB removal/rejoin activator is wired.
if [[ "$TAKYON_SUBUSER_FANOUT_CHILD" != "1" ]]; then
  if [[ "$TAKYON_RUN_DB_MIGRATIONS" == "1" ]]; then
    echo "sub-user deploy never runs migrations; migrate once on the operator host before fanout" >&2
    exit 1
  fi
  if [[ "$TAKYON_SUBUSER_LB_LIVE" == "1" ]]; then
    echo "sub-user LB serving is marked live; refusing non-drained activation (use the tracked LB remove/rejoin rail)" >&2
    exit 1
  fi

  host_endpoint() { printf '%s' "${1##*@}"; }
  add_host_once() {
    local candidate="$1" existing
    [[ -n "$candidate" ]] || return 0
    for existing in "${hosts[@]:-}"; do
      [[ "$(host_endpoint "$existing")" == "$(host_endpoint "$candidate")" ]] && return 0
    done
    hosts+=("$candidate")
  }

  if [[ "$(host_endpoint "$TAKYON_SUBUSER_PRIMARY_HOST")" != "$(host_endpoint "$TAKYON_SUBUSER_CANONICAL_PRIMARY_HOST")" ]]; then
    echo "TAKYON_SUBUSER_PRIMARY_HOST cannot replace canonical production primary $TAKYON_SUBUSER_CANONICAL_PRIMARY_HOST" >&2
    exit 1
  fi
  primary_host="$TAKYON_SUBUSER_CANONICAL_PRIMARY_HOST"
  hosts=()
  add_host_once "$primary_host"
  add_host_once "$TAKYON_SUBUSER_CANONICAL_REPLICA_HOST"
  IFS=',' read -r -a replica_hosts <<< "$TAKYON_SUBUSER_REPLICA_HOSTS"
  for replica_host in "${replica_hosts[@]}"; do
    replica_host="${replica_host//[[:space:]]/}"
    add_host_once "$replica_host"
  done
  # Repair/CI callers may add a host, but can never replace either canonical production member.
  [[ -n "$TAKYON_VPS_HOST_EXPLICIT" ]] && add_host_once "$TAKYON_VPS_HOST"

  # Stage non-serving replicas first and the direct-serving primary last. A partial replica-stage
  # failure therefore leaves the live primary's on-disk source untouched.
  staging_hosts=()
  for host in "${hosts[@]}"; do
    [[ "$(host_endpoint "$host")" == "$(host_endpoint "$primary_host")" ]] || staging_hosts+=("$host")
  done
  staging_hosts+=("$primary_host")
  staged_hosts_done=()
  activated_hosts=()
  subuser_plane_committed=0
  cleanup_subuser_fanout() {
    local status="$?" host is_primary
    if [[ "$subuser_plane_committed" != "1" ]]; then
      for ((index=${#activated_hosts[@]} - 1; index >= 0; index--)); do
        host="${activated_hosts[$index]}"
        is_primary=0
        [[ "$(host_endpoint "$host")" == "$(host_endpoint "$primary_host")" ]] && is_primary=1
        env \
          TAKYON_SUBUSER_FANOUT_CHILD=1 \
          TAKYON_SUBUSER_DEPLOY_PHASE=rollback \
          TAKYON_SUBUSER_IS_PRIMARY="$is_primary" \
          TAKYON_VPS_HOST="$host" \
          TAKYON_REQUIRE_APP_DATABASE_URL="$is_primary" \
          TAKYON_RUN_WEB_BUILD=0 \
          "$0" || true
      done
      # Bash 3.2 with nounset rejects a direct expansion of an empty array. The deploy
      # runs from macOS, so skip the expansion when no host finished staging.
      if (( ${#staged_hosts_done[@]} )); then
        for host in "${staged_hosts_done[@]}"; do
          env \
            TAKYON_SUBUSER_FANOUT_CHILD=1 \
            TAKYON_SUBUSER_DEPLOY_PHASE=discard \
            TAKYON_VPS_HOST="$host" \
            TAKYON_RUN_WEB_BUILD=0 \
            "$0" || true
        done
      fi
    fi
    return "$status"
  }
  trap cleanup_subuser_fanout EXIT
  for host in "${staging_hosts[@]}"; do
    is_primary=0
    require_app_database_url=0
    if [[ "$(host_endpoint "$host")" == "$(host_endpoint "$primary_host")" ]]; then
      is_primary=1
      require_app_database_url=1
    fi
    env \
      TAKYON_SUBUSER_FANOUT_CHILD=1 \
      TAKYON_SUBUSER_DEPLOY_PHASE=stage \
      TAKYON_SUBUSER_IS_PRIMARY="$is_primary" \
      TAKYON_VPS_HOST="$host" \
      TAKYON_REQUIRE_APP_DATABASE_URL="$require_app_database_url" \
      TAKYON_RUN_WEB_BUILD=0 \
      "$0"
    staged_hosts_done+=("$host")
  done

  # Activate and prove every replica before touching the direct-serving primary.
  activation_hosts=()
  for host in "${hosts[@]}"; do
    [[ "$(host_endpoint "$host")" == "$(host_endpoint "$primary_host")" ]] || activation_hosts+=("$host")
  done
  activation_hosts+=("$primary_host")
  for host in "${activation_hosts[@]}"; do
    is_primary=0
    [[ "$(host_endpoint "$host")" == "$(host_endpoint "$primary_host")" ]] && is_primary=1
    # Record the attempted host before crossing the SSH boundary. If this parent is interrupted
    # after the remote host committed activation but before the child returns, cleanup must still
    # drive that host through the idempotent rollback rail.
    activated_hosts+=("$host")
    env \
      TAKYON_SUBUSER_FANOUT_CHILD=1 \
      TAKYON_SUBUSER_DEPLOY_PHASE=activate \
      TAKYON_SUBUSER_IS_PRIMARY="$is_primary" \
      TAKYON_VPS_HOST="$host" \
      TAKYON_REQUIRE_APP_DATABASE_URL="$is_primary" \
      TAKYON_RUN_WEB_BUILD=0 \
      "$0"
  done

  # Commit the plane only after every host independently proves the same revision and a live health
  # response. Until this gate passes, an outer failure rolls every already-activated replica back.
  for host in "${activation_hosts[@]}"; do
    env \
      TAKYON_SUBUSER_FANOUT_CHILD=1 \
      TAKYON_SUBUSER_DEPLOY_PHASE=verify \
      TAKYON_VPS_HOST="$host" \
      TAKYON_RUN_WEB_BUILD=0 \
      "$0"
  done
  subuser_plane_committed=1

  finalize_status=0
  for host in "${activation_hosts[@]}"; do
    env \
      TAKYON_SUBUSER_FANOUT_CHILD=1 \
      TAKYON_SUBUSER_DEPLOY_PHASE=finalize \
      TAKYON_VPS_HOST="$host" \
      TAKYON_RUN_WEB_BUILD=0 \
      "$0" || finalize_status=1
  done
  trap - EXIT
  if [[ "$finalize_status" != "0" ]]; then
    echo "sub-user release is healthy on every host, but release cleanup failed" >&2
    exit 1
  fi
  exit 0
fi

if ! [[ "$TAKYON_HEALTH_WAIT_SECONDS" =~ ^[0-9]+$ ]] || (( TAKYON_HEALTH_WAIT_SECONDS < 1 )); then
  echo "TAKYON_HEALTH_WAIT_SECONDS must be a positive integer" >&2
  exit 1
fi

case "$TAKYON_SUBUSER_DEPLOY_PHASE" in
  stage|activate|rollback|verify|finalize|discard) ;;
  *)
    echo "invalid internal sub-user deploy phase: $TAKYON_SUBUSER_DEPLOY_PHASE" >&2
    exit 1
    ;;
esac

if [[ "$TAKYON_RUN_DB_MIGRATIONS" == "1" ]]; then
  echo "sub-user deploy never runs migrations; migrate once on the operator host before fanout" >&2
  exit 1
fi

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

if [[ ! -f "$SEED_XURL_AUTH_SCRIPT" ]]; then
  echo "xurl auth seed script not found: $SEED_XURL_AUTH_SCRIPT" >&2
  exit 1
fi

if [[ ! -f "$ENSURE_DENO_SCRIPT" ]]; then
  echo "deno bootstrap helper not found: $ENSURE_DENO_SCRIPT" >&2
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

if [[ ! -f "$VERIFY_SUBUSER_RUNTIME_SURFACE_SCRIPT" ]]; then
  echo "sub-user runtime surface verifier not found: $VERIFY_SUBUSER_RUNTIME_SURFACE_SCRIPT" >&2
  exit 1
fi

if [[ ! -f "$WEB_BUILD_SCRIPT" ]]; then
  echo "web build helper not found: $WEB_BUILD_SCRIPT" >&2
  exit 1
fi

if [[ ! -f "$VERIFY_SUPABASE_AUTH_SCRIPT" ]]; then
  echo "supabase auth verifier not found: $VERIFY_SUPABASE_AUTH_SCRIPT" >&2
  exit 1
fi

if [[ ! -f "$TAKYON_VPS_KEY" ]]; then
  echo "deploy key not found: $TAKYON_VPS_KEY" >&2
  exit 1
fi

if [[ "$TAKYON_SUBUSER_DEPLOY_PHASE" == "stage" \
  && ( "$TAKYON_SYNC_PRODUCT_SITES" == "1" || "$TAKYON_SYNC_PRODUCT_SOURCE_CACHE" == "1" ) \
  && ! -f "$PRODUCT_SITES_SOURCE_KEY" ]]; then
  echo "product-sites source key not found: $PRODUCT_SITES_SOURCE_KEY" >&2
  exit 1
fi

remote_service_candidate="$TAKYON_REMOTE_RELEASE_META/candidates/takyon-subuser.service"
remote_service_backup="$TAKYON_REMOTE_RELEASE_META/backups/takyon-subuser.service"
remote_unit_activation_marker="$TAKYON_REMOTE_RELEASE_META/unit-installed"
remote_skills_backup="$TAKYON_REMOTE_RELEASE_META/backups/home-skills"
remote_skills_existed_marker="$TAKYON_REMOTE_RELEASE_META/home-skills-existed"
remote_skills_activation_marker="$TAKYON_REMOTE_RELEASE_META/home-skills-installed"

verify_subuser_runtime_surface() {
  local runtime_root="$1"
  local verify_home="${2:-0}"
  ssh -i "$TAKYON_VPS_KEY" -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new "$TAKYON_VPS_HOST" \
    "python3 - '$runtime_root' '$TAKYON_REMOTE_HOME' '$verify_home'" \
    < "$VERIFY_SUBUSER_RUNTIME_SURFACE_SCRIPT"
}

rollback_subuser_host() {
  ssh -i "$TAKYON_VPS_KEY" -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new "$TAKYON_VPS_HOST" \
    "set -euo pipefail
    systemctl stop '$TAKYON_REMOTE_SERVICE_NAME'
    systemctl is-active --quiet '$TAKYON_REMOTE_SERVICE_NAME' && exit 1 || true"
  takyon_rollback_runtime_if_pending "$TAKYON_VPS_HOST" "$TAKYON_VPS_KEY"
  ssh -i "$TAKYON_VPS_KEY" -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new "$TAKYON_VPS_HOST" \
    "set -euo pipefail
    if [[ -f '$remote_unit_activation_marker' ]]; then
      test -f '$remote_service_backup'
      cp -p '$remote_service_backup' '$TAKYON_REMOTE_SERVICE_FILE'
    fi
    if [[ -f '$remote_skills_activation_marker' ]]; then
      rm -rf '$TAKYON_REMOTE_HOME/skills'
      if [[ -f '$remote_skills_existed_marker' ]]; then
        test -d '$remote_skills_backup'
        cp -a '$remote_skills_backup' '$TAKYON_REMOTE_HOME/skills'
      fi
    fi
    systemctl daemon-reload
    systemctl restart '$TAKYON_REMOTE_SERVICE_NAME'
    systemctl is-active --quiet '$TAKYON_REMOTE_SERVICE_NAME'
    for _ in \$(seq 1 '$TAKYON_HEALTH_WAIT_SECONDS'); do
      curl -fsS http://127.0.0.1:9119/healthz >/dev/null && break
      sleep 1
    done
    curl -fsS http://127.0.0.1:9119/healthz >/dev/null"
  takyon_discard_staged_runtime_release "$TAKYON_VPS_HOST" "$TAKYON_VPS_KEY"
}

activate_subuser_host() {
  local activation_started=0
  rollback_subuser_activation() {
    local status="$?"
    if [[ "$activation_started" == "1" ]]; then
      rollback_subuser_host || true
    fi
    return "$status"
  }
  trap rollback_subuser_activation EXIT

  ssh -i "$TAKYON_VPS_KEY" -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new "$TAKYON_VPS_HOST" \
    "set -euo pipefail
    test -f '$remote_service_candidate'
    if [[ '$TAKYON_SUBUSER_IS_PRIMARY' == '1' \
      && '$TAKYON_SUBUSER_ALLOW_PRIMARY_HARD_RESTART' != '1' ]] \
      && ! cmp -s '$remote_service_candidate' '$TAKYON_REMOTE_SERVICE_FILE'; then
      echo 'primary service unit changed; explicit drained hard-restart authorization is required' >&2
      exit 1
    fi"

  takyon_prepare_runtime_rollback "$TAKYON_VPS_HOST" "$TAKYON_VPS_KEY"
  activation_started=1
  takyon_begin_runtime_activation "$TAKYON_VPS_HOST" "$TAKYON_VPS_KEY"
  ssh -i "$TAKYON_VPS_KEY" -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new "$TAKYON_VPS_HOST" \
    "set -euo pipefail
    systemctl stop '$TAKYON_REMOTE_SERVICE_NAME'
    systemctl is-active --quiet '$TAKYON_REMOTE_SERVICE_NAME' && exit 1 || true
    cp -p '$TAKYON_REMOTE_SERVICE_FILE' '$remote_service_backup'
    if [[ -L '$TAKYON_REMOTE_HOME/skills' ]]; then
      echo 'refusing to replace symlinked sub-user skills tree' >&2
      exit 1
    fi
    if [[ -d '$TAKYON_REMOTE_HOME/skills' ]]; then
      cp -a '$TAKYON_REMOTE_HOME/skills' '$remote_skills_backup'
      touch '$remote_skills_existed_marker'
    fi
    touch '$remote_skills_activation_marker'
    touch '$remote_unit_activation_marker'
    install -o root -g root -m 0644 '$remote_service_candidate' '$TAKYON_REMOTE_SERVICE_FILE'"
  takyon_activate_staged_runtime "$TAKYON_VPS_HOST" "$TAKYON_VPS_KEY" "$TAKYON_DEPLOY_SOURCE_REVISION"
  ssh -i "$TAKYON_VPS_KEY" -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new "$TAKYON_VPS_HOST" \
    "set -euo pipefail
    rm -rf '$TAKYON_REMOTE_HOME/skills' '$TAKYON_REMOTE_HOME/claude-agent-sdk' '$TAKYON_REMOTE_HOME/runtime/claude-agent-sdk'
    rm -rf '$TAKYON_REMOTE_RUNTIME/node_modules/@anthropic-ai'/claude-agent-sdk*
    rm -f '$TAKYON_REMOTE_RUNTIME/node_modules/.bin'/claude*
    rm -f '$TAKYON_REMOTE_RUNTIME/plugins/takyon'/claude_sdk_runtime*.pyc
    rm -f '$TAKYON_REMOTE_RUNTIME/plugins/takyon'/claude_sdk_sessions*.pyc
    rm -f '$TAKYON_REMOTE_RUNTIME/plugins/takyon'/bootstrap_phases*.pyc
    rm -f '$TAKYON_REMOTE_RUNTIME/plugins/takyon/__pycache__'/claude_sdk_runtime*.pyc
    rm -f '$TAKYON_REMOTE_RUNTIME/plugins/takyon/__pycache__'/claude_sdk_sessions*.pyc
    rm -f '$TAKYON_REMOTE_RUNTIME/plugins/takyon/__pycache__'/bootstrap_phases*.pyc
    rm -f '$TAKYON_REMOTE_RUNTIME/scripts'/build_approved_skills_manifest*.pyc
    rm -f '$TAKYON_REMOTE_RUNTIME/scripts/__pycache__'/build_approved_skills_manifest*.pyc
    "
  verify_subuser_runtime_surface "$TAKYON_REMOTE_RUNTIME" 1
  ssh -i "$TAKYON_VPS_KEY" -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new "$TAKYON_VPS_HOST" \
    "set -euo pipefail
    systemctl daemon-reload
    systemctl restart '$TAKYON_REMOTE_SERVICE_NAME'
    systemctl is-active --quiet '$TAKYON_REMOTE_SERVICE_NAME'
    for _ in \$(seq 1 '$TAKYON_HEALTH_WAIT_SECONDS'); do
      curl -fsS http://127.0.0.1:9119/healthz >/dev/null && break
      sleep 1
    done
    curl -fsS http://127.0.0.1:9119/healthz >/dev/null"
  if [[ "$TAKYON_APPLY_CADDY" == "1" ]]; then
    TAKYON_VPS_HOST="$TAKYON_VPS_HOST" TAKYON_VPS_KEY="$TAKYON_VPS_KEY" \
      "$ROOT_DIR/deploy/takyon-subuser/apply-caddyfile.sh"
  fi
  activation_started=0
  trap - EXIT
}

case "$TAKYON_SUBUSER_DEPLOY_PHASE" in
  activate)
    activate_subuser_host
    exit 0
    ;;
  rollback)
    rollback_subuser_host
    exit 0
    ;;
  verify)
    verify_subuser_runtime_surface "$TAKYON_REMOTE_RUNTIME" 1
    ssh -i "$TAKYON_VPS_KEY" -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new "$TAKYON_VPS_HOST" \
      "set -euo pipefail
      systemctl is-active --quiet '$TAKYON_REMOTE_SERVICE_NAME'
      curl -fsS http://127.0.0.1:9119/healthz >/dev/null
      python3 - '$TAKYON_REMOTE_RUNTIME/.takyon-deploy-artifact.json' '$TAKYON_DEPLOY_SOURCE_REVISION' <<'PY'
import json
import pathlib
import sys

manifest = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding='utf-8'))
if manifest.get('source_revision') != sys.argv[2]:
    raise SystemExit('sub-user host source revision differs from the fanout revision')
PY"
    exit 0
    ;;
  finalize)
    takyon_finalize_runtime_release "$TAKYON_VPS_HOST" "$TAKYON_VPS_KEY"
    exit 0
    ;;
  discard)
    takyon_discard_staged_runtime_release "$TAKYON_VPS_HOST" "$TAKYON_VPS_KEY"
    exit 0
    ;;
esac

ssh -i "$TAKYON_VPS_KEY" -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new "$TAKYON_VPS_HOST" \
  "python3 - /opt/takyon/.takyon/.env /opt/takyon/secrets/.env" \
  < "$REMOVE_STRIPE_AUTHORITY_ENV_SCRIPT"

ssh -i "$TAKYON_VPS_KEY" -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new "$TAKYON_VPS_HOST" \
  "TAKYON_REQUIRE_MIGRATION_DATABASE_URL='$TAKYON_RUN_DB_MIGRATIONS' TAKYON_REQUIRE_APP_DATABASE_URL='$TAKYON_REQUIRE_APP_DATABASE_URL' bash -s -- subuser /opt/takyon/.takyon/.env /opt/takyon/secrets/.env" \
  < "$VALIDATE_AUTHORITY_ENV_SCRIPT"

(
  compile_cache="$(mktemp -d)"
  trap 'rm -rf "$compile_cache"' EXIT
  PYTHONPYCACHEPREFIX="$compile_cache" python3 -m compileall -q \
    "$DEPLOY_RUNTIME_DIR/plugins/takyon" \
    "$DEPLOY_RUNTIME_DIR/takyon_cli" \
  "$DEPLOY_RUNTIME_DIR/tui_gateway"
)

subuser_stage_incomplete=1
cleanup_incomplete_subuser_stage() {
  local status="$?"
  if [[ "$subuser_stage_incomplete" == "1" ]]; then
    takyon_discard_staged_runtime_release "$TAKYON_VPS_HOST" "$TAKYON_VPS_KEY" || true
  fi
  return "$status"
}
trap cleanup_incomplete_subuser_stage EXIT

takyon_stage_runtime_release \
  "$DEPLOY_RUNTIME_DIR" "$TAKYON_VPS_HOST" "$TAKYON_VPS_KEY" "$TAKYON_DEPLOY_SOURCE_REVISION" subuser

verify_subuser_runtime_surface "$TAKYON_REMOTE_STAGED_RUNTIME" 0

scp -i "$TAKYON_VPS_KEY" -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new \
  "$SERVICE_FILE" \
  "$TAKYON_VPS_HOST:$remote_service_candidate"
ssh -i "$TAKYON_VPS_KEY" -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new "$TAKYON_VPS_HOST" \
  "set -euo pipefail
  test -s '$remote_service_candidate'
  systemd-analyze verify '$remote_service_candidate' >/dev/null"

TARGET_HOST="$TAKYON_VPS_HOST" \
TARGET_KEY="$TAKYON_VPS_KEY" \
TAKYON_REMOTE_RUNTIME="$TAKYON_REMOTE_RUNTIME" \
TAKYON_REMOTE_HOME="$TAKYON_REMOTE_HOME" \
TAKYON_REMOTE_SAFEBOX_URL="$TAKYON_REMOTE_SAFEBOX_URL" \
  "$SEED_XURL_AUTH_SCRIPT"

ssh -i "$TAKYON_VPS_KEY" -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new "$TAKYON_VPS_HOST" \
  "env TAKYON_DENO_VERSION='$TAKYON_DENO_VERSION' TAKYON_REQUIRE_SYSTEMD_RUN=1 bash -s" \
  < "$ENSURE_DENO_SCRIPT"

ssh -i "$TAKYON_VPS_KEY" -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new "$TAKYON_VPS_HOST" \
  "set -euo pipefail
  install -d '$TAKYON_REMOTE_PRODUCT_SITES'
  # The tracked unit runs as the dedicated non-root 'takyon' user — provision idempotently here,
  # before daemon-reload/restart, since this script is the rail that ships the unit. No docker
  # group: this plane spawns no containers.
  if ! id -u takyon >/dev/null 2>&1; then
    useradd --system --user-group --home-dir /opt/takyon --shell /usr/sbin/nologin takyon
  fi
  chown takyon:takyon /opt/takyon
  chown -R takyon:takyon '$TAKYON_REMOTE_HOME'
  if [ -d /opt/takyon/secrets ]; then chown -R takyon:takyon /opt/takyon/secrets; fi
  # Service HOME moved /root -> /opt/takyon (ProtectHome=true hides /root): migrate xurl auth state
  # once and keep it owned by the service user; replace a /usr/local/bin/xurl symlink into /root
  # with a real copy (the symlink target is unreachable for the service).
  if [ -e /root/.xurl ] && [ ! -e /opt/takyon/.xurl ]; then cp -a /root/.xurl /opt/takyon/.xurl; fi
  if [ -e /opt/takyon/.xurl ]; then chown -R takyon:takyon /opt/takyon/.xurl; fi
  if ! grep -q '^TAKYON_SAFEBOX_TOKEN=' /opt/takyon/.takyon/.env 2>/dev/null \
    && ! grep -q '^TAKYON_SAFEBOX_TOKEN=' /opt/takyon/secrets/.env 2>/dev/null; then
    echo 'TAKYON_SAFEBOX_TOKEN missing from both /opt/takyon/.takyon/.env and /opt/takyon/secrets/.env' >&2
    exit 1
  fi
  if [ -L /usr/local/bin/xurl ] && [ -x /root/.local/bin/xurl ]; then
    install -m 0755 /root/.local/bin/xurl /usr/local/bin/xurl
  fi
  command -v deno >/dev/null 2>&1
  test \"\$(deno --version | awk 'NR==1 {print \$2}')\" = '$TAKYON_DENO_VERSION'
  command -v systemd-run >/dev/null 2>&1
  PYTHONPATH='$TAKYON_REMOTE_STAGED_RUNTIME' python3 -m compileall -q '$TAKYON_REMOTE_STAGED_RUNTIME/plugins/takyon' '$TAKYON_REMOTE_STAGED_RUNTIME/takyon_cli' '$TAKYON_REMOTE_STAGED_RUNTIME/tui_gateway'
  env TAKYON_HOME='$TAKYON_REMOTE_HOME' HOME=/root PYTHONUNBUFFERED=1 TAKYON_HOST_ROLE=subuser TAKYON_SAFEBOX_URL='$TAKYON_REMOTE_SAFEBOX_URL' \
    PYTHONPATH='$TAKYON_REMOTE_STAGED_RUNTIME' '$TAKYON_REMOTE_STAGED_RUNTIME/.venv/bin/python' '$TAKYON_REMOTE_STAGED_RUNTIME/scripts/verify-supabase-auth-runtime.py'
  # Stage-only: every replica must receive and validate the exact artifact before the parent
  # activates any service. The currently serving process remains untouched here.
  systemctl is-active --quiet '$TAKYON_REMOTE_SERVICE_NAME'"

if [[ "$TAKYON_SYNC_PRODUCT_SITES" == "1" ]]; then
  ssh_opts_source=(-i "$PRODUCT_SITES_SOURCE_KEY" -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new)
  ssh_opts_target=(-i "$TAKYON_VPS_KEY" -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new)
  ssh "${ssh_opts_source[@]}" "$PRODUCT_SITES_SOURCE_HOST" "set -euo pipefail; cd /opt/takyon; tar -cf - .takyon/product-sites" \
    | ssh "${ssh_opts_target[@]}" "$TAKYON_VPS_HOST" "set -euo pipefail
      incoming=\$(mktemp -d)
      trap 'rm -rf \"\$incoming\"' EXIT
      tar -C \"\$incoming\" -xf -
      rsync -a --force \"\$incoming/.takyon/product-sites/\" '$TAKYON_REMOTE_PRODUCT_SITES/'
      chown -R takyon:takyon '$TAKYON_REMOTE_PRODUCT_SITES'"
fi

if [[ "$TAKYON_SYNC_PRODUCT_SOURCE_CACHE" == "1" ]]; then
  ssh_opts_source=(-i "$PRODUCT_SITES_SOURCE_KEY" -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new)
  ssh_opts_target=(-i "$TAKYON_VPS_KEY" -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new)
  ssh "${ssh_opts_source[@]}" "$PRODUCT_SITES_SOURCE_HOST" "python3 - <<'PY'
import pathlib
import sys
import tarfile

root = pathlib.Path('/opt/takyon')
base = root / '.takyon' / 'cache' / 'businesses'
excluded_names = {
    '.cache',
    '.env',
    '.env.local',
    '.env.production',
    '.git',
    '.next',
    '__pycache__',
    'build',
    'builds',
    'dist',
    'node_modules',
    'secrets',
}

def include(info):
    parts = pathlib.PurePosixPath(info.name).parts
    if any(part in excluded_names or part.endswith('.pyc') for part in parts):
        return None
    return info

with tarfile.open(fileobj=sys.stdout.buffer, mode='w|') as tar:
    if not base.is_dir():
        raise SystemExit(0)
    for site in sorted(base.glob('*/product/site')):
        if site.is_dir():
            tar.add(site, arcname=str(site.relative_to(root)), recursive=True, filter=include)
PY" \
    | ssh "${ssh_opts_target[@]}" "$TAKYON_VPS_HOST" "set -euo pipefail
      install -d '$TAKYON_REMOTE_HOME/cache/businesses'
      find '$TAKYON_REMOTE_HOME/cache/businesses' -mindepth 4 -maxdepth 4 -type d -path '*/product/site' -prune -exec rm -rf -- {} +
      tar -C '$TAKYON_REMOTE_ROOT' -xf -
      chown -R takyon:takyon '$TAKYON_REMOTE_HOME/cache/businesses'"
fi

subuser_stage_incomplete=0
trap - EXIT
