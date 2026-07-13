#!/usr/bin/env bash
set -euo pipefail

# Local production operator rail.
#
# This is NOT local-dev and not a local dashboard. It runs the operator shell/worker on this Mac
# against the same production control plane as app.fourmanifold.com, while reaching private
# Safebox and localhost-only operator creative routes through explicit SSH tunnels.
# Product/sub-user serving stays on the sub-user plane.

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUNTIME_DIR="$ROOT/hermes-agent-main"
TAKYON_ENTRY="$ROOT/takyon"
TAKYON_CLI_BIN="$RUNTIME_DIR/.venv/bin/takyon-cli"
TAKYON_CLI_PYTHON="$RUNTIME_DIR/.venv/bin/python"

SSH_HOST="${TAKYON_OPERATOR_VPS_HOST:-root@137.184.75.57}"
SSH_KEY="${TAKYON_OPERATOR_VPS_KEY:-$HOME/.ssh/takyon_argon_alpha14}"
SAFEBOX_SSH_HOST="${TAKYON_SAFEBOX_VPS_HOST:-root@67.205.158.170}"
SAFEBOX_SSH_KEY="${TAKYON_SAFEBOX_VPS_KEY:-$SSH_KEY}"
SAFEBOX_PRIVATE_HOST="${TAKYON_REMOTE_SAFEBOX_PRIVATE_HOST:-10.116.0.2}"
SAFEBOX_PRIVATE_PORT="${TAKYON_REMOTE_SAFEBOX_PRIVATE_PORT:-8000}"
LOCAL_SAFEBOX_PORT="${TAKYON_LOCAL_SAFEBOX_PORT:-8765}"
LOCAL_SAFEBOX_URL="${TAKYON_LOCAL_SAFEBOX_URL:-http://127.0.0.1:${LOCAL_SAFEBOX_PORT}}"
REMOTE_DASHBOARD_HOST="${TAKYON_REMOTE_DASHBOARD_HOST:-127.0.0.1}"
REMOTE_DASHBOARD_PORT="${TAKYON_REMOTE_DASHBOARD_PORT:-9119}"
LOCAL_DASHBOARD_PORT="${TAKYON_LOCAL_DASHBOARD_PORT:-9129}"
LOCAL_DASHBOARD_URL="${TAKYON_LOCAL_DASHBOARD_URL:-http://127.0.0.1:${LOCAL_DASHBOARD_PORT}}"
CONTAINER_SAFEBOX_URL="${TAKYON_CONTAINER_SAFEBOX_URL:-http://host.docker.internal:${LOCAL_SAFEBOX_PORT}}"
LOCAL_PROD_ROOT="${TAKYON_OPERATOR_PROD_ROOT:-$HOME/.takyon-fourmanifold-operator-prod}"
ACTIVE_LOCAL_WORKER_PREFIX_FILE="${TAKYON_OPERATOR_ACTIVE_WORKER_PREFIX_FILE:-$LOCAL_PROD_ROOT/active-local-worker-prefix}"
OPERATOR_HOME="${TAKYON_OPERATOR_PROD_HOME:-$LOCAL_PROD_ROOT/operator}"
DEFAULT_OPERATOR_USER_ID="${TAKYON_OPERATOR_DEFAULT_USER_ID:-150e4213-4006-4dc1-9cf3-ca7ab3b4696f}"
OPERATOR_USER_ID_OVERRIDE=""

# ── Named operator profiles ──────────────────────────────────────────────────────────────
# Short name -> Takyon operator user-id, so you never paste a UUID:
#   scripts/takyon-operator-prod.sh sai                    # = console 4 --user-id <sai> --shells 4
#   scripts/takyon-operator-prod.sh josh 10 foo --shells 2 # name + your own console args
#   scripts/takyon-operator-prod.sh console 4 --user sai --shells 4   # or as a --user flag
# resolve_operator_alias / is_operator_alias live in scripts/operator-users.sh — ONE source of
# truth shared with the dev rail, so `sai`/`josh` mean the same person in dev and prod.
# shellcheck source=scripts/operator-users.sh
source "$ROOT/scripts/operator-users.sh"

# ── Target plane: prod (default) or the dev twin ─────────────────────────────────────────
# EXACT MIRROR: the entire command surface below (console / worker pool / shell / ClaimScope)
# is SHARED. Only *which plane we connect to* differs. `TAKYON_OPERATOR_TARGET=dev` (set by the
# thin scripts/takyon-operator-dev.sh wrapper) swaps the secret source + Safebox URL + TAKYON_ENV
# via early-return dev branches, so the prod path stays byte-identical.
TARGET="${TAKYON_OPERATOR_TARGET:-prod}"
DEV_STORE="${TAKYON_DEV_STORE:-$ROOT/.takyon-dev-safebox}"
DEV_STORE_ENV="$DEV_STORE/.env"
if [[ "$TARGET" == "dev" ]]; then
  LOCAL_PROD_ROOT="${TAKYON_DEV_OPERATOR_HOME:-$HOME/.takyon-fourmanifold-dev-operator}"
  OPERATOR_HOME="$LOCAL_PROD_ROOT/operator"
  ACTIVE_LOCAL_WORKER_PREFIX_FILE="$LOCAL_PROD_ROOT/active-local-worker-prefix"
fi

# Read one KEY=value from the dev store .env (values may be quoted). No secrets echoed.
_dev_store_get() {
  local key="$1"
  [[ -f "$DEV_STORE_ENV" ]] || die "dev store not found at $DEV_STORE_ENV (run 'takyon env create dev' first)"
  sed -n "s/^${key}=//p" "$DEV_STORE_ENV" | head -1 | sed -e 's/^"//' -e 's/"$//'
}

dev_env_config_path() {
  local candidates=(
    "$ROOT/.takyon/environments/dev/config.yaml"
    "$DEV_STORE/environments/dev/config.yaml"
  )
  local path
  for path in "${candidates[@]}"; do
    if [[ -f "$path" ]]; then
      printf '%s' "$path"
      return 0
    fi
  done
  return 1
}

fetch_dev_topology_exports() {
  local config_path py
  config_path="$(dev_env_config_path 2>/dev/null || true)"
  [[ -n "$config_path" && -f "$config_path" ]] || return 1
  py="$TAKYON_CLI_PYTHON"
  [[ -x "$py" ]] || py="python3"
  "$py" - <<'PY' "$config_path"
from __future__ import annotations

import shlex
import sys
from pathlib import Path

import yaml

path = Path(sys.argv[1])
data = yaml.safe_load(path.read_text()) or {}
dev_split = data.get("dev_split") or {}
safebox = dev_split.get("safebox") or {}
ssh_key_path = str(dev_split.get("ssh_key_path") or "").strip()
public_ip = str(safebox.get("public_ip") or "").strip()
private_ip = str(safebox.get("private_ip") or "").strip()
if not ssh_key_path or not public_ip or not private_ip:
    raise SystemExit(1)
for key, value in (
    ("TAKYON_DEV_REMOTE_SAFEBOX_SSH_HOST", f"root@{public_ip}"),
    ("TAKYON_DEV_REMOTE_SAFEBOX_PUBLIC_IP", public_ip),
    ("TAKYON_DEV_REMOTE_SAFEBOX_PRIVATE_IP", private_ip),
    ("TAKYON_DEV_REMOTE_SAFEBOX_SSH_KEY", ssh_key_path),
):
    print(f"export {key}={shlex.quote(value)}")
PY
}

dev_remote_safebox_configured() {
  local exports
  exports="$(fetch_dev_topology_exports 2>/dev/null || true)"
  [[ -n "$exports" ]]
}

load_dev_remote_topology() {
  local exports
  exports="$(fetch_dev_topology_exports 2>/dev/null || true)"
  [[ -n "$exports" ]] || return 1
  # shellcheck disable=SC1090
  eval "$exports"
  [[ -n "${TAKYON_DEV_REMOTE_SAFEBOX_SSH_HOST:-}" ]] || return 1
  [[ -n "${TAKYON_DEV_REMOTE_SAFEBOX_PRIVATE_IP:-}" ]] || return 1
  [[ -n "${TAKYON_DEV_REMOTE_SAFEBOX_SSH_KEY:-}" ]] || return 1
  SSH_HOST="$TAKYON_DEV_REMOTE_SAFEBOX_SSH_HOST"
  SSH_KEY="$TAKYON_DEV_REMOTE_SAFEBOX_SSH_KEY"
  SAFEBOX_PRIVATE_HOST="$TAKYON_DEV_REMOTE_SAFEBOX_PRIVATE_IP"
  SAFEBOX_PRIVATE_PORT="8000"
  return 0
}

fetch_dev_remote_env_exports() {
  load_dev_remote_topology || return 1
  local -a args=(
    -i "$SSH_KEY"
    -o IdentitiesOnly=yes
    -o StrictHostKeyChecking=accept-new
  )
  ssh "${args[@]}" "$SSH_HOST" "python3 - <<'PY'
from __future__ import annotations

import os
import shlex
from pathlib import Path

keys = (
    'TAKYON_DEV_OPERATOR_DATABASE_URL',
    'TAKYON_DEV_RUNTIME_DATABASE_URL',
    'TAKYON_DEV_SAFEBOX_DATABASE_URL',
    'TAKYON_DEV_MIGRATION_DATABASE_URL',
    'TAKYON_SAFEBOX_TOKEN',
    'TAKYON_SAFEBOX_OPERATOR_TOKEN',
)

values = {}
env_path = Path('/opt/takyon/.takyon/.env')
if env_path.exists():
    for raw_line in env_path.read_text(encoding='utf-8').splitlines():
        line = raw_line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, value = line.split('=', 1)
        values[key.strip()] = value.strip().strip('\"').strip(\"'\")

for key in keys:
    value = str(os.environ.get(key) or values.get(key) or '').strip()
    if value:
        print(f'export {key}={shlex.quote(value)}')
PY"
}

# Bring up the dev Safebox (its own process, dev store) if its URL is not already answering.
ensure_dev_safebox_up() {
  if dev_remote_safebox_configured; then
    return 0
  fi
  local url token host port
  url="$(_dev_store_get TAKYON_DEV_SAFEBOX_URL)"; token="$(_dev_store_get TAKYON_DEV_SAFEBOX_TOKEN)"
  [[ -n "$url" ]] || die "TAKYON_DEV_SAFEBOX_URL missing from dev store"
  if curl -fsS -m 4 "$url/healthz" >/dev/null 2>&1; then return 0; fi
  host="$(printf '%s' "$url" | sed -E 's#^https?://([^:/]+).*#\1#')"
  port="$(printf '%s' "$url" | sed -E 's#^https?://[^:]+:([0-9]+).*#\1#')"; port="${port:-8378}"
  mkdir -p "$OPERATOR_HOME"
  echo "Starting dev Safebox on $host:$port ..." >&2
  # The operator-plane routes (billing reserve/settle, workspace, caps) check os.environ directly,
  # so the operator token + client allowlist + cap-signing key must be in the Safebox PROCESS env
  # (not only the store .env). This is what lets the dev worker's billing.reserve succeed.
  ( cd "$RUNTIME_DIR" && env TAKYON_ENV=dev TAKYON_HOST_ROLE=safebox TAKYON_HOME="$DEV_STORE" \
      TAKYON_SAFEBOX_TOKEN="$token" TAKYON_ALLOW_POSTGRES_OUTSIDE_VPS=1 \
      TAKYON_DEV_OPERATOR_DATABASE_URL="$(_dev_store_get TAKYON_DEV_OPERATOR_DATABASE_URL)" \
      TAKYON_DEV_RUNTIME_DATABASE_URL="$(_dev_store_get TAKYON_DEV_RUNTIME_DATABASE_URL)" \
      TAKYON_DEV_SAFEBOX_DATABASE_URL="$(_dev_store_get TAKYON_DEV_SAFEBOX_DATABASE_URL)" \
      TAKYON_DEV_MIGRATION_DATABASE_URL="$(_dev_store_get TAKYON_DEV_MIGRATION_DATABASE_URL)" \
      TAKYON_SAFEBOX_OPERATOR_TOKEN="$(_dev_store_get TAKYON_SAFEBOX_OPERATOR_TOKEN)" \
      TAKYON_SAFEBOX_OPERATOR_CLIENTS="$(_dev_store_get TAKYON_SAFEBOX_OPERATOR_CLIENTS)" \
      TAKYON_CAP_SIGNING_KEY="$(_dev_store_get TAKYON_CAP_SIGNING_KEY)" \
      TAKYON_OPERATOR_USAGE_GATE_DISABLED=1 \
      TAKYON_PG_POOL_SIZE="${TAKYON_DEV_PG_POOL_SIZE:-3}" \
      "$RUNTIME_DIR/.venv/bin/uvicorn" --app-dir "$RUNTIME_DIR" \
      "plugins.takyon.safebox_app:build_safebox_app" --factory --host "$host" --port "$port" \
      >"$OPERATOR_HOME/dev-safebox.log" 2>&1 & )
  for _ in $(seq 1 30); do
    curl -fsS -m 2 "$url/healthz" >/dev/null 2>&1 && { echo "dev Safebox up." >&2; return 0; }
    sleep 1
  done
  die "dev Safebox did not come up; see $OPERATOR_HOME/dev-safebox.log"
}

ensure_dev_operator_budget_ready() {
  local operator_user_id="${1:-}"
  [[ -n "$operator_user_id" ]] || return 0
  [[ -x "$TAKYON_CLI_PYTHON" ]] || die "takyon CLI missing: expected $TAKYON_CLI_PYTHON"
  if dev_remote_safebox_configured; then
    return 0
  fi
  # Dev shells/workers bind directly to an explicit operator user id (`josh`, `sai`, etc.) instead
  # of coming through the dashboard/Auth0 first-login path. Seed that existing dev user's billing
  # and custody rails up front so the first CLI bootstrap reserve cannot die on missing safebox
  # account state. Dev-only and idempotent: zero-balance opens plus the one-time starter allowance.
  env PYTHONPATH="$RUNTIME_DIR${PYTHONPATH:+:$PYTHONPATH}" \
    TAKYON_ENV=dev TAKYON_HOST_ROLE=safebox TAKYON_HOME="$DEV_STORE" \
    TAKYON_ALLOW_POSTGRES_OUTSIDE_VPS=1 \
    TAKYON_DEV_OPERATOR_DATABASE_URL="$(_dev_store_get TAKYON_DEV_OPERATOR_DATABASE_URL)" \
    TAKYON_DEV_RUNTIME_DATABASE_URL="$(_dev_store_get TAKYON_DEV_RUNTIME_DATABASE_URL)" \
    TAKYON_DEV_SAFEBOX_DATABASE_URL="$(_dev_store_get TAKYON_DEV_SAFEBOX_DATABASE_URL)" \
    TAKYON_DEV_MIGRATION_DATABASE_URL="$(_dev_store_get TAKYON_DEV_MIGRATION_DATABASE_URL)" \
    "$TAKYON_CLI_PYTHON" - "$operator_user_id" <<'PY' >/dev/null
from __future__ import annotations

import sys

import psycopg

from plugins.takyon import safebox
from plugins.takyon.runtime_app import assert_takyon_pg_role, resolve_database_url

user_id = str(sys.argv[1] or "").strip()
if not user_id:
    raise SystemExit(0)

with psycopg.connect(
    resolve_database_url(plane="safebox"),
    autocommit=False,
    prepare_threshold=None,
) as conn:
    assert_takyon_pg_role(conn, "safebox")
    row = conn.execute("select 1 from users where id = %s", (user_id,)).fetchone()
    if row is None:
        raise SystemExit(
            f"dev operator user missing from users: {user_id}. "
            "Log into the dev dashboard once or seed that operator account first."
        )
    safebox._local_open_billing_account(conn, user_id, allowance_included_cents=0)
    safebox._local_open_custody_account(conn, user_id)
    safebox._local_grant_starter_allowance(
        conn,
        user_id,
        idempotency_subject=f"dev-operator:{user_id}",
    )
    billing_row = conn.execute(
        "select 1 from billing_accounts where user_id = %s",
        (user_id,),
    ).fetchone()
    if billing_row is None:
        raise SystemExit(f"dev billing account missing after seed for {user_id}")
PY
}

# Keep the dev twin's schema current with the repo. It DRIFTS when new migration files land (a
# fresh `git pull` that adds 0062 money_shape, say) because the twin was bootstrapped earlier — and
# unlike prod (where `takyon migrate` is a deliberate deploy step) the dev twin should self-heal so a
# CLI bootstrap never runs against a stale schema (the money_shape drift that broke dev /create).
# Gated on the migration FILE COUNT via a marker: a current twin is a zero-cost skip; when new files
# appear, the TRACKED `takyon migrate` rail replays every migration idempotently as takyon_migration
# against TAKYON_DEV_MIGRATION_DATABASE_URL (additive/nullable, so re-running is always safe).
ensure_dev_schema_current() {
  if dev_remote_safebox_configured; then
    return 0
  fi
  local repo_sig marker
  repo_sig="$(ls "$RUNTIME_DIR/plugins/takyon/db/migrations/"*.sql 2>/dev/null | wc -l | tr -d ' ')"
  [[ "${repo_sig:-0}" -gt 0 ]] || return 0
  marker="$OPERATOR_HOME/.dev-schema-migrated"
  [[ -f "$marker" && "$(cat "$marker" 2>/dev/null)" == "$repo_sig" ]] && return 0
  echo "dev twin schema: $repo_sig migration files vs marker — running tracked migrate…" >&2
  if env TAKYON_ENV=dev TAKYON_HOST_ROLE=operator TAKYON_HOME="$OPERATOR_HOME" \
       TAKYON_ALLOW_POSTGRES_OUTSIDE_VPS=1 \
       TAKYON_DEV_MIGRATION_DATABASE_URL="$(_dev_store_get TAKYON_DEV_MIGRATION_DATABASE_URL)" \
       "$TAKYON_ENTRY" migrate >/dev/null 2>&1; then
    printf '%s' "$repo_sig" > "$marker"
    echo "dev twin schema current." >&2
  else
    echo "warning: dev migrate rail failed — schema may be stale; run: TAKYON_ENV=dev takyon migrate" >&2
  fi
}

# The dev mirror of load_operator_env. Admin/bootstrap mode still supports the local dev store, but
# ordinary dev use prefers the remote dev safebox host described in environments/dev config so the
# shell no longer depends on a local authority env copy.
load_dev_operator_env() {
  mkdir -p "$OPERATOR_HOME" "$LOCAL_PROD_ROOT/logs"
  if [[ -f "$ROOT/.takyon/config.yaml" ]] && ! cmp -s "$ROOT/.takyon/config.yaml" "$OPERATOR_HOME/config.yaml" 2>/dev/null; then
    cp "$ROOT/.takyon/config.yaml" "$OPERATOR_HOME/config.yaml"
  fi
  if dev_remote_safebox_configured; then
    load_dev_remote_topology
    # shellcheck disable=SC1090
    eval "$(fetch_dev_remote_env_exports)"
    export TAKYON_ENV=dev
    export TAKYON_STRIPE_MODE=test
    export TAKYON_HOME="$OPERATOR_HOME"
    export TAKYON_HOST_ROLE=operator
    export TAKYON_DB_BACKEND=postgres
    export TAKYON_ALLOW_POSTGRES_OUTSIDE_VPS=1
    export TAKYON_ALLOW_REMOTE_STORAGE_SYNC_OUTSIDE_VPS=1
    export TAKYON_SAFEBOX_URL="$LOCAL_SAFEBOX_URL"
    export TAKYON_PROVIDER_BROKER=1
    export TERMINAL_ENV="${TERMINAL_ENV:-docker}"
    export TAKYON_OPERATOR_GATEWAY_BROKER_URL="$LOCAL_SAFEBOX_URL"
    if [[ "$TERMINAL_ENV" == "docker" ]]; then
      export TAKYON_CLAUDE_AGENT_BROKER_URL="$CONTAINER_SAFEBOX_URL"
    else
      export TAKYON_CLAUDE_AGENT_BROKER_URL="$LOCAL_SAFEBOX_URL"
    fi
    export TAKYON_STORAGE_BACKEND=local
    export TAKYON_PG_POOL_SIZE="${TAKYON_DEV_PG_POOL_SIZE:-3}"
    export TAKYON_OPERATOR_USAGE_GATE_DISABLED=1
    export TAKYON_SESSION_USER_ID="$(resolved_operator_user_id)"
    local remote_worker_pool; remote_worker_pool="$(resolve_local_worker_pool_id)"
    [[ -n "$remote_worker_pool" ]] && export TAKYON_WORKER_POOL_ID="$remote_worker_pool"
    unset_raw_runtime_authority_env
    return 0
  fi
  ensure_dev_safebox_up
  ensure_dev_schema_current
  export TAKYON_ENV=dev
  export TAKYON_STRIPE_MODE=test
  export TAKYON_HOME="$OPERATOR_HOME"
  export TAKYON_HOST_ROLE=operator
  export TAKYON_DB_BACKEND=postgres
  export TAKYON_ALLOW_POSTGRES_OUTSIDE_VPS=1
  export TAKYON_ALLOW_REMOTE_STORAGE_SYNC_OUTSIDE_VPS=1
  export TAKYON_DEV_OPERATOR_DATABASE_URL="$(_dev_store_get TAKYON_DEV_OPERATOR_DATABASE_URL)"
  export TAKYON_DEV_RUNTIME_DATABASE_URL="$(_dev_store_get TAKYON_DEV_RUNTIME_DATABASE_URL)"
  export TAKYON_DEV_SAFEBOX_DATABASE_URL="$(_dev_store_get TAKYON_DEV_SAFEBOX_DATABASE_URL)"
  export TAKYON_DEV_MIGRATION_DATABASE_URL="$(_dev_store_get TAKYON_DEV_MIGRATION_DATABASE_URL)"
  export TAKYON_SAFEBOX_URL="$(_dev_store_get TAKYON_DEV_SAFEBOX_URL)"
  export TAKYON_SAFEBOX_TOKEN="$(_dev_store_get TAKYON_DEV_SAFEBOX_TOKEN)"
  export TAKYON_SAFEBOX_OPERATOR_TOKEN="$(_dev_store_get TAKYON_SAFEBOX_OPERATOR_TOKEN)"
  export TAKYON_PROVIDER_BROKER=1
  export TERMINAL_ENV="${TERMINAL_ENV:-docker}"
  # Mirror the prod split: the host-side operator shell talks to the local dev Safebox loopback,
  # while Dockerized Claude product workers need the container-reachable host alias.
  export TAKYON_OPERATOR_GATEWAY_BROKER_URL="$TAKYON_SAFEBOX_URL"
  if [[ "$TERMINAL_ENV" == "docker" ]]; then
    export TAKYON_CLAUDE_AGENT_BROKER_URL="$CONTAINER_SAFEBOX_URL"
  else
    export TAKYON_CLAUDE_AGENT_BROKER_URL="$TAKYON_SAFEBOX_URL"
  fi
  export TAKYON_STORAGE_BACKEND=local
  # Dev twin runs on the Supabase SESSION pooler (15-client cap — role GUCs need session mode). The
  # default 8-conn pool per process (safebox + runtime + worker) exhausts it and starves the job
  # lease-heartbeat, churning long bootstraps. A small pool per process fits the single-user dev twin
  # with headroom for the bootstrap's own connections. Override with TAKYON_DEV_PG_POOL_SIZE.
  export TAKYON_PG_POOL_SIZE="${TAKYON_DEV_PG_POOL_SIZE:-3}"
  # Operator usage gate — mirror PROD exactly. Prod runs with TAKYON_OPERATOR_USAGE_GATE_DISABLED=1
  # (verified live in the prod worker env), so the operator's OWN agent is never throttled when its
  # allowance is exhausted (billing.py softens `insufficient_balance` ONLY when this is set; the
  # ledger still records every reserve→settle). ensure_dev_operator_budget_ready (above) opens the
  # billing/custody account + a starter allowance; this keeps the operator unthrottled like prod so
  # a long dev bootstrap can never re-block on a spent allowance. Clear it to re-enable the gate.
  export TAKYON_OPERATOR_USAGE_GATE_DISABLED=1
  export TAKYON_SESSION_USER_ID="$(resolved_operator_user_id)"
  ensure_dev_operator_budget_ready "$TAKYON_SESSION_USER_ID"
  # Stage 2 (ClaimScope): identical to prod — bind this session's enqueues to the Mac's pool.
  local local_worker_pool; local_worker_pool="$(resolve_local_worker_pool_id)"
  [[ -n "$local_worker_pool" ]] && export TAKYON_WORKER_POOL_ID="$local_worker_pool"
  unset_raw_runtime_authority_env
}
SSH_SERVER_ALIVE_INTERVAL="${TAKYON_OPERATOR_SSH_SERVER_ALIVE_INTERVAL:-15}"
SSH_SERVER_ALIVE_COUNT_MAX="${TAKYON_OPERATOR_SSH_SERVER_ALIVE_COUNT_MAX:-3}"
CONSOLE_TUNNEL_MONITOR_SECONDS="${TAKYON_OPERATOR_TUNNEL_MONITOR_SECONDS:-5}"

ssh_base() {
  local -a args=(
    -i "$SSH_KEY"
    -o IdentitiesOnly=yes
    -o StrictHostKeyChecking=accept-new
  )
  ssh "${args[@]}" "$SSH_HOST" "$@"
}

ssh_tunnel_exec() {
  local -a args=(
    -i "$SSH_KEY"
    -o IdentitiesOnly=yes
    -o StrictHostKeyChecking=accept-new
    -o ExitOnForwardFailure=yes
    -o "ServerAliveInterval=${SSH_SERVER_ALIVE_INTERVAL}"
    -o "ServerAliveCountMax=${SSH_SERVER_ALIVE_COUNT_MAX}"
    -o TCPKeepAlive=yes
    -N
  )
  exec ssh "${args[@]}" "$@" "$SSH_HOST"
}

resolved_operator_user_id() {
  local explicit="${1:-}"
  if [[ -n "$explicit" ]]; then
    printf '%s' "$explicit"
    return 0
  fi
  if [[ -n "${OPERATOR_USER_ID_OVERRIDE:-}" ]]; then
    printf '%s' "$OPERATOR_USER_ID_OVERRIDE"
    return 0
  fi
  if [[ -n "${TAKYON_SESSION_USER_ID:-}" ]]; then
    printf '%s' "$TAKYON_SESSION_USER_ID"
    return 0
  fi
  printf '%s' "$DEFAULT_OPERATOR_USER_ID"
}

# Stage 2 (ClaimScope): pool identity replaces the old worker-id-prefix affinity hint. The
# pool id is a plain worker id (no trailing dash); durable ownership lives in the Postgres
# worker_pools registry + jobs reservation columns, enforced by claim_one. The sidecar file
# below is only a LOCAL DISCOVERY hint so a plain `shell` (no console-started worker) binds
# its enqueues to the Mac's active pool; process liveness is checked before trusting it.
local_worker_pool_id_for_pid() {
  local pid="${1:-}"
  [[ "$pid" =~ ^[0-9]+$ ]] || return 1
  printf 'mac-operator-%s-%s' "$(hostname -s)" "$pid"
}

record_active_local_worker_pool() {
  local pid="${1:-}"
  local pool_id="${2:-}"
  [[ "$pid" =~ ^[0-9]+$ ]] || return 1
  if [[ -z "$pool_id" ]]; then
    pool_id="$(local_worker_pool_id_for_pid "$pid")" || return 1
  fi
  mkdir -p "$LOCAL_PROD_ROOT"
  printf '%s %s\n' "$pid" "$pool_id" >"$ACTIVE_LOCAL_WORKER_PREFIX_FILE"
  chmod 600 "$ACTIVE_LOCAL_WORKER_PREFIX_FILE" 2>/dev/null || true
  printf '%s' "$pool_id"
}

active_local_worker_pool_id() {
  [[ -f "$ACTIVE_LOCAL_WORKER_PREFIX_FILE" ]] || return 0

  local stored_pid=""
  local stored_pool=""
  local command_text=""
  local direct_worker_match="0"
  local wrapper_worker_match="0"
  read -r stored_pid stored_pool <"$ACTIVE_LOCAL_WORKER_PREFIX_FILE" || true
  if [[ ! "$stored_pid" =~ ^[0-9]+$ ]] || [[ -z "$stored_pool" ]]; then
    rm -f "$ACTIVE_LOCAL_WORKER_PREFIX_FILE"
    return 0
  fi

  if ! kill -0 "$stored_pid" >/dev/null 2>&1; then
    rm -f "$ACTIVE_LOCAL_WORKER_PREFIX_FILE"
    return 0
  fi

  if command -v ps >/dev/null 2>&1; then
    command_text="$(ps -p "$stored_pid" -o command= 2>/dev/null || true)"
    if [[ -n "$command_text" ]]; then
      [[ "$command_text" == *"worker --worker-id ${stored_pool}"* ]] && direct_worker_match="1"
      [[ "$command_text" == *"takyon-operator-prod.sh worker"* ]] && wrapper_worker_match="1"
    fi
    if [[ -n "$command_text" ]] && [[ "$direct_worker_match" != "1" ]] && [[ "$wrapper_worker_match" != "1" ]]; then
      rm -f "$ACTIVE_LOCAL_WORKER_PREFIX_FILE"
      return 0
    fi
  fi

  printf '%s' "$stored_pool"
}

resolve_local_worker_pool_id() {
  if [[ -n "${TAKYON_WORKER_POOL_ID:-}" ]]; then
    printf '%s' "$TAKYON_WORKER_POOL_ID"
    return 0
  fi
  active_local_worker_pool_id
}

die() {
  echo "takyon-prod: $*" >&2
  exit 1
}

shell_join() {
  local out=""
  local arg=""
  local quoted=""
  for arg in "$@"; do
    printf -v quoted '%q' "$arg"
    if [[ -n "$out" ]]; then
      out+=" "
    fi
    out+="$quoted"
  done
  printf '%s' "$out"
}

applescript_escape() {
  local value="${1:-}"
  value="${value//\\/\\\\}"
  value="${value//\"/\\\"}"
  printf '%s' "$value"
}

open_terminal_window() {
  command -v osascript >/dev/null 2>&1 || die "osascript is required to open Terminal windows automatically"
  local command_text="${1:-}"
  local escaped_command=""
  escaped_command="$(applescript_escape "$command_text")"
  osascript <<EOF
tell application "Terminal"
  activate
  do script "$escaped_command"
end tell
EOF
}

takyon_cli_shim_ready() {
  [[ -x "$TAKYON_CLI_BIN" ]]
}

takyon_cli_fallback_ready() {
  [[ -x "$TAKYON_CLI_PYTHON" ]] || return 1
  PYTHONPATH="$RUNTIME_DIR${PYTHONPATH:+:$PYTHONPATH}" "$TAKYON_CLI_PYTHON" - <<'PY' >/dev/null 2>&1
import takyon_cli.main
PY
}

bootstrap_takyon_cli_runtime() {
  echo "Bootstrapping local Takyon runtime into $RUNTIME_DIR/.venv ..." >&2
  if command -v uv >/dev/null 2>&1; then
    (
      cd "$RUNTIME_DIR"
      uv venv "$RUNTIME_DIR/.venv"
      UV_PROJECT_ENVIRONMENT="$RUNTIME_DIR/.venv" uv pip install -e ".[all,postgres]"
    ) || die "failed to bootstrap Takyon runtime into $RUNTIME_DIR/.venv with uv"
    return 0
  fi
  local bootstrap_python=""
  if command -v python3 >/dev/null 2>&1; then
    bootstrap_python="$(command -v python3)"
  elif command -v python >/dev/null 2>&1; then
    bootstrap_python="$(command -v python)"
  else
    die "takyon CLI missing: expected $TAKYON_CLI_BIN or runnable fallback via $TAKYON_CLI_PYTHON -m takyon_cli.main; install uv or Python 3 with venv support"
  fi
  (
    cd "$RUNTIME_DIR"
    "$bootstrap_python" -m venv "$RUNTIME_DIR/.venv"
    "$RUNTIME_DIR/.venv/bin/python" -m pip install -e ".[all,postgres]"
  ) || die "failed to bootstrap Takyon runtime into $RUNTIME_DIR/.venv with $bootstrap_python"
}

ensure_takyon_cli_runtime() {
  if takyon_cli_shim_ready || takyon_cli_fallback_ready; then
    return 0
  fi
  bootstrap_takyon_cli_runtime
  if takyon_cli_shim_ready || takyon_cli_fallback_ready; then
    return 0
  fi
  die "takyon CLI missing after bootstrap: expected $TAKYON_CLI_BIN or runnable fallback via $TAKYON_CLI_PYTHON -m takyon_cli.main"
}

run_takyon_cli() {
  # Always import the CLI from this sealed checkout. A reused/copied venv can contain a
  # console-script shim whose editable-install pointer targets a different worktree; invoking that
  # shim would silently run stale code while advertising this checkout's release SHA.
  PYTHONPATH="$RUNTIME_DIR${PYTHONPATH:+:$PYTHONPATH}" "$TAKYON_CLI_PYTHON" -m takyon_cli.main "$@"
}

exec_takyon_cli() {
  # Keep the exec path source-sealed for the same reason as run_takyon_cli above.
  exec env PYTHONPATH="$RUNTIME_DIR${PYTHONPATH:+:$PYTHONPATH}" "$TAKYON_CLI_PYTHON" -m takyon_cli.main "$@"
}

operator_runtime_deps_ready() {
  [[ -x "$TAKYON_CLI_PYTHON" ]] || return 1
  "$TAKYON_CLI_PYTHON" - <<'PY' >/dev/null 2>&1
import psycopg
import fastapi
import uvicorn
import simple_term_menu
PY
}

ensure_operator_runtime_deps() {
  if operator_runtime_deps_ready; then
    return 0
  fi
  command -v uv >/dev/null 2>&1 || die "operator runtime deps missing in $TAKYON_CLI_PYTHON and uv is not installed; run: cd $RUNTIME_DIR && uv pip install -e '.[all,postgres]'"
  echo "Installing local operator runtime deps into $RUNTIME_DIR/.venv ..." >&2
  (
    cd "$RUNTIME_DIR"
    UV_PROJECT_ENVIRONMENT="$RUNTIME_DIR/.venv" uv pip install -e ".[all,postgres]"
  ) || die "failed to install .[all,postgres] into $RUNTIME_DIR/.venv"
  operator_runtime_deps_ready || die "operator runtime deps still missing after installing .[all,postgres]"
}

require_files() {
  if [[ "$TARGET" == "dev" ]]; then
    [[ -x "$TAKYON_ENTRY" ]] || die "Takyon entrypoint missing: $TAKYON_ENTRY"
    ensure_takyon_cli_runtime
    if ! dev_remote_safebox_configured; then
      [[ -f "$DEV_STORE_ENV" ]] || die "dev store not found at $DEV_STORE_ENV (run 'takyon env create dev' first)"
    fi
    return 0
  fi
  [[ -x "$TAKYON_ENTRY" ]] || die "Takyon entrypoint missing: $TAKYON_ENTRY"
  ensure_takyon_cli_runtime
  [[ -f "$SSH_KEY" ]] || die "SSH key missing: $SSH_KEY"
}

ensure_home() {
  mkdir -p "$OPERATOR_HOME" "$LOCAL_PROD_ROOT/logs"
  # The production VPS config is canonical. A workspace-local config must never override the
  # production CEO/worker model split on the Mac-primary compute rail.
  # Retry the fetch: startup fires many ssh_base calls in a burst (plus the persistent tunnels),
  # so a single dropped SSH under transient load must not abort the whole console boot with a
  # false "config is empty" — a fresh connection almost always succeeds.
  local _cfg_try
  for _cfg_try in 1 2 3 4 5; do
    if ssh_base "cat /opt/takyon/.takyon/config.yaml" >"$OPERATOR_HOME/config.yaml.vps.tmp" 2>/dev/null \
        && [[ -s "$OPERATOR_HOME/config.yaml.vps.tmp" ]]; then
      break
    fi
    [[ "$_cfg_try" == 5 ]] && die "operator VPS config is empty after 5 attempts (SSH to $SSH_HOST unstable?)"
    sleep 2
  done
  if ! cmp -s "$OPERATOR_HOME/config.yaml.vps.tmp" "$OPERATOR_HOME/config.yaml" 2>/dev/null; then
    mv "$OPERATOR_HOME/config.yaml.vps.tmp" "$OPERATOR_HOME/config.yaml"
    echo "→ Adopted canonical runtime config from the VPS"
  else
    rm -f "$OPERATOR_HOME/config.yaml.vps.tmp"
  fi
  if ssh_base "test -f /opt/takyon/.takyon/dashboard_session_token"; then
    ssh_base "cat /opt/takyon/.takyon/dashboard_session_token" >"$OPERATOR_HOME/dashboard_session_token"
    chmod 600 "$OPERATOR_HOME/dashboard_session_token"
  fi
}

fetch_operator_env_exports() {
  ssh_base "python3 - <<'PY'
from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys

keys = {
    'TAKYON_ENV',
    'TAKYON_STRIPE_MODE',
    'TAKYON_OPERATOR_DATABASE_URL',
    'TAKYON_SAFEBOX_TOKEN',
    'TAKYON_SAFEBOX_OPERATOR_TOKEN',
    'TAKYON_STORAGE_BACKEND',
    'SUPABASE_S3_ENDPOINT',
    'SUPABASE_S3_REGION',
    'TAKYON_STORAGE_BUCKET',
    'PUBLIC_COMPANY_BASE_DOMAIN',
    'CLOUDFLARE_ZONE_NAME',
    'TAKYON_PROVIDER_BROKER',
    'R2_S3_ENDPOINT',
    'R2_S3_REGION',
    'R2_BUCKET',
    'TERMINAL_DOCKER_IMAGE',
    'TAKYON_CLAUDE_AGENT_DOCKER_IMAGE',
    'TAKYON_STRICT_MODEL_ROLES',
    'TAKYON_MODEL',
    'TAKYON_CLAUDE_AGENT_MODEL',
    'ANTHROPIC_MODEL',
    'ANTHROPIC_DEFAULT_OPUS_MODEL',
    'ANTHROPIC_DEFAULT_SONNET_MODEL',
    'ANTHROPIC_DEFAULT_HAIKU_MODEL',
    'CLAUDE_CODE_SUBAGENT_MODEL',
}

pid = subprocess.check_output(
    ['systemctl', 'show', '-p', 'MainPID', '--value', 'takyon-dashboard.service'],
    text=True,
).strip()
if not pid or pid == '0':
    raise SystemExit('takyon-dashboard.service is not running on the operator VPS')

env_path = f'/proc/{pid}/environ'
data = open(env_path, 'rb').read().split(b'\\0')
env = {}
for part in data:
    if not part or b'=' not in part:
        continue
    key, value = part.split(b'=', 1)
    name = key.decode('utf-8', errors='replace')
    if name in keys:
        env[name] = value.decode('utf-8', errors='replace')

missing = [
    key
    for key in (
        'TAKYON_OPERATOR_DATABASE_URL',
        'TAKYON_SAFEBOX_TOKEN',
        'TAKYON_SAFEBOX_OPERATOR_TOKEN',
    )
    if not env.get(key)
]
if missing:
    raise SystemExit('missing required operator env: ' + ', '.join(missing))

expected_runtime = {
    'TAKYON_ENV': 'prod',
    'TAKYON_STRIPE_MODE': 'live',
}
wrong_runtime = [
    f'{key}={env.get(key) or \"<missing>\"}'
    for key, expected in expected_runtime.items()
    if env.get(key) != expected
]
if wrong_runtime:
    raise SystemExit('operator runtime pins do not match production contract: ' + ', '.join(wrong_runtime))

expected_models = {
    'TAKYON_STRICT_MODEL_ROLES': '1',
    'TAKYON_MODEL': 'gpt-5.5',
    'TAKYON_CLAUDE_AGENT_MODEL': 'deepseek-v4-pro',
    'ANTHROPIC_MODEL': 'deepseek-v4-pro',
    'ANTHROPIC_DEFAULT_OPUS_MODEL': 'deepseek-v4-pro',
    'ANTHROPIC_DEFAULT_SONNET_MODEL': 'deepseek-v4-pro',
    'ANTHROPIC_DEFAULT_HAIKU_MODEL': 'deepseek-v4-pro',
    'CLAUDE_CODE_SUBAGENT_MODEL': 'deepseek-v4-pro',
}
wrong = [
    f'{key}={env.get(key) or \"<missing>\"}'
    for key, expected in expected_models.items()
    if env.get(key) != expected
]
if wrong:
    raise SystemExit('operator model pins do not match production contract: ' + ', '.join(wrong))

manifest_path = '/opt/takyon/hermes-agent-main/.takyon-deploy-artifact.json'
try:
    manifest = json.loads(open(manifest_path, encoding='utf-8').read())
except (OSError, ValueError, TypeError) as exc:
    raise SystemExit(f'invalid operator deploy manifest: {exc}') from None
release_sha = str(manifest.get('source_revision') or '').strip().lower() if isinstance(manifest, dict) else ''
if len(release_sha) != 40 or any(ch not in '0123456789abcdef' for ch in release_sha):
    raise SystemExit('operator deploy manifest has no valid source_revision')
env['TAKYON_RUNTIME_RELEASE_SHA'] = release_sha

for key in sorted(env):
    print(f'export {key}={shlex.quote(env[key])}')
PY"
}

verify_local_runtime_release() {
  [[ "$TARGET" == "prod" ]] || return 0
  local dirty=""
  local head=""
  local published=""
  local deployed="${TAKYON_RUNTIME_RELEASE_SHA:-}"

  # Only executable operator/runtime paths participate in the seal; unrelated notes do not make
  # parallel CLI use harder. Any tracked or untracked runtime mutation fails closed.
  dirty="$(git -C "$ROOT" status --porcelain --untracked-files=all -- \
    hermes-agent-main takyon scripts/takyon-operator-prod.sh scripts/operator-users.sh \
    scripts/operator-users.conf)"
  [[ -z "$dirty" ]] || die "refusing production compute from modified runtime source"
  git -C "$ROOT" fetch --quiet origin main \
    || die "could not verify the published production release"
  head="$(git -C "$ROOT" rev-parse HEAD)"
  published="$(git -C "$ROOT" rev-parse refs/remotes/origin/main)"
  [[ "$head" =~ ^[0-9a-f]{40}$ ]] || die "local runtime release is invalid"
  [[ "$head" == "$published" ]] \
    || die "local runtime $head is not the published origin/main release $published"
  [[ "$head" == "$deployed" ]] \
    || die "local runtime $head does not match the live operator release $deployed"
  export TAKYON_RUNTIME_RELEASE_SHA="$head"
}

load_operator_env() {
  if [[ "$TARGET" == "dev" ]]; then
    require_files
    ensure_operator_runtime_deps
    load_dev_operator_env
    return 0
  fi
  require_files
  ensure_operator_runtime_deps
  ensure_home
  # shellcheck disable=SC1090
  eval "$(fetch_operator_env_exports)"
  verify_local_runtime_release

  export TAKYON_ENV=prod
  export TAKYON_STRIPE_MODE=live
  export TAKYON_HOME="$OPERATOR_HOME"
  export HOME="$HOME"
  export TAKYON_HOST_ROLE=operator
  export TAKYON_DB_BACKEND=postgres
  export TAKYON_ALLOW_POSTGRES_OUTSIDE_VPS=1
  export TAKYON_ALLOW_REMOTE_STORAGE_SYNC_OUTSIDE_VPS=1
  export TAKYON_SAFEBOX_URL="$LOCAL_SAFEBOX_URL"
  export TAKYON_DASHBOARD_URL="$LOCAL_DASHBOARD_URL"
  export TAKYON_PROVIDER_BROKER="${TAKYON_PROVIDER_BROKER:-1}"
  export TERMINAL_ENV="${TERMINAL_ENV:-docker}"
  # The host-side operator shell talks to the localhost tunnel. Dockerized business workers need the
  # container-reachable host alias. Keep both explicit so CEO chat and Docker compute do not fight over
  # one Safebox broker URL.
  export TAKYON_OPERATOR_GATEWAY_BROKER_URL="$LOCAL_SAFEBOX_URL"
  if [[ "$TERMINAL_ENV" == "docker" ]]; then
    export TAKYON_CLAUDE_AGENT_BROKER_URL="$CONTAINER_SAFEBOX_URL"
  else
    export TAKYON_CLAUDE_AGENT_BROKER_URL="$LOCAL_SAFEBOX_URL"
  fi
  export TAKYON_STORAGE_BACKEND="${TAKYON_STORAGE_BACKEND:-supabase_s3}"
  export TAKYON_SESSION_USER_ID="$(resolved_operator_user_id)"
  # Stage 2 (ClaimScope): one pool-id env binds this session's enqueues to the Mac's active
  # worker pool via the jobs reservation columns (claim_scope.session_claim_scope).
  local_worker_pool="$(resolve_local_worker_pool_id)"
  if [[ -n "$local_worker_pool" ]]; then
    export TAKYON_WORKER_POOL_ID="$local_worker_pool"
  fi
  export TERMINAL_DOCKER_MOUNT_CWD_TO_WORKSPACE="${TERMINAL_DOCKER_MOUNT_CWD_TO_WORKSPACE:-true}"
  export TERMINAL_CONTAINER_PERSISTENT="${TERMINAL_CONTAINER_PERSISTENT:-false}"
  unset TAKYON_DOCKER_BINARY TAKYON_DOCKER_BROKER_URL TAKYON_DOCKER_BROKER_TOKEN
  unset_raw_runtime_authority_env
}

unset_raw_runtime_authority_env() {
  # Local prod compute must use Safebox brokers/tunnels, not accidental caller shell secrets.
  unset \
    ANTHROPIC_API_KEY ANTHROPIC_TOKEN CLAUDE_CODE_OAUTH_TOKEN \
    OPENAI_API_KEY OPENAI_KEY AZURE_OPENAI_API_KEY AZURE_OPENAI_KEY \
    GEMINI_API_KEY GOOGLE_API_KEY TAKYON_GEMINI_API_KEY \
    FAL_KEY FAL_API_KEY REPLICATE_API_TOKEN \
    TAVILY_API_KEY FIRECRAWL_API_KEY PARALLEL_API_KEY XAI_API_KEY \
    COMPOSIO_API_KEY META_MCP_OAUTH_TOKEN META_SYSTEM_USER_ACCESS_TOKEN META_ACCESS_TOKEN META_CAPI_TOKEN \
    STRIPE_SECRET_KEY STRIPE_WEBHOOK_SECRET STRIPE_BILLING_WEBHOOK_SECRET \
    POSTMARK_SERVER_TOKEN AUTH0_SECRET AUTH0_CLIENT_SECRET \
    VERCEL_TOKEN CLOUDFLARE_API_TOKEN \
    SUPABASE_S3_ACCESS_KEY_ID SUPABASE_S3_SECRET_ACCESS_KEY \
    R2_S3_ACCESS_KEY_ID R2_S3_SECRET_ACCESS_KEY \
    SUPABASE_SERVICE_ROLE_KEY TAKYON_CAP_SIGNING_KEY
}

require_tunnel() {
  if [[ "$TARGET" == "dev" ]]; then
    if dev_remote_safebox_configured; then
      if safebox_tunnel_healthy; then
        return 0
      fi
      mkdir -p "$LOCAL_PROD_ROOT/logs"
      local timestamp tunnel_log tunnel_pid_file
      timestamp="$(date +%Y%m%d-%H%M%S)"
      tunnel_log="$LOCAL_PROD_ROOT/logs/tunnel-$timestamp.log"
      tunnel_pid_file="$LOCAL_PROD_ROOT/logs/tunnel-$timestamp.pid"
      ensure_managed_tunnel "Dev Safebox" "$LOCAL_SAFEBOX_URL" "$LOCAL_SAFEBOX_URL/healthz" "safebox-tunnel" "$tunnel_log" "$tunnel_pid_file" safebox_tunnel_healthy "$LOCAL_SAFEBOX_PORT"
      return 0
    fi
    ensure_dev_safebox_up
    return 0
  fi
  if tunnel_healthy; then
    return 0
  fi
  local missing=()
  if ! safebox_tunnel_healthy; then
    missing+=("Safebox $LOCAL_SAFEBOX_URL")
  fi
  if ! dashboard_tunnel_healthy; then
    missing+=("dashboard $LOCAL_DASHBOARD_URL")
  fi
  local missing_text="local production tunnel endpoints"
  if ((${#missing[@]} > 0)); then
    missing_text="${missing[*]}"
  fi
  cat >&2 <<EOF
Required local production tunnel is not reachable: ${missing_text}.

Start it in another terminal:
  scripts/takyon-operator-prod.sh tunnel
EOF
  exit 1
}

safebox_tunnel_healthy() {
  curl --silent --fail --max-time 2 "$LOCAL_SAFEBOX_URL/healthz" >/dev/null 2>&1
}

dashboard_tunnel_healthy() {
  curl --silent --fail --max-time 2 "$LOCAL_DASHBOARD_URL/healthz" >/dev/null 2>&1
}

local_tcp_port_listening() {
  local port="$1"
  nc -z 127.0.0.1 "$port" >/dev/null 2>&1
}

tunnel_restart_lock_path() {
  local port="$1"
  local lock_root="$LOCAL_PROD_ROOT/tunnel-locks"
  mkdir -p "$lock_root"
  printf '%s/restart-%s.lock' "$lock_root" "$port"
}

current_shell_process_pid() {
  # Bash 3.2 keeps $$ pinned to the parent shell inside an async function, and this helper is
  # itself called through command substitution. A tiny child asks for its parent's parent: the
  # stable monitor process that must own and release this cross-shell lock.
  sh -c 'ps -o ppid= -p "$PPID" | tr -d "[:space:]"'
}

require_tunnel_restart_lock_tool() {
  if [[ ! -x /usr/bin/shlock ]]; then
    echo "takyon-prod: /usr/bin/shlock is required for shared tunnel ownership on the Mac production rail" >&2
    return 1
  fi
  if ! command -v lsof >/dev/null 2>&1; then
    echo "takyon-prod: lsof is required to verify shared tunnel listener ownership" >&2
    return 1
  fi
  if ! command -v nc >/dev/null 2>&1; then
    echo "takyon-prod: nc is required to verify shared tunnel listener health" >&2
    return 1
  fi
}

acquire_tunnel_restart_lock() {
  local port="$1"
  local lock_file current_pid
  require_tunnel_restart_lock_tool || return 2
  lock_file="$(tunnel_restart_lock_path "$port")"
  current_pid="$(current_shell_process_pid)"
  # shlock performs stale validation and the replacement link as one established dot-lock
  # protocol, so two contenders cannot both remove an observed stale lock and delete the winner.
  /usr/bin/shlock -f "$lock_file" -p "$current_pid"
}

acquire_tunnel_restart_lock_wait() {
  local port="$1"
  local wait_seconds="${TAKYON_TUNNEL_LOCK_WAIT_SECONDS:-45}"
  if ! [[ "$wait_seconds" =~ ^[0-9]+$ ]] || [[ "$wait_seconds" -lt 1 ]]; then
    wait_seconds="45"
  fi
  local attempts=$((wait_seconds * 10))
  local _attempt lock_status
  for _attempt in $(seq 1 "$attempts"); do
    lock_status=0
    acquire_tunnel_restart_lock "$port" || lock_status="$?"
    if [[ "$lock_status" == "0" ]]; then
      return 0
    fi
    if [[ "$lock_status" != "1" ]]; then
      return "$lock_status"
    fi
    sleep 0.1
  done
  return 1
}

release_tunnel_restart_lock() {
  local port="$1"
  local lock_file owner_pid current_pid
  lock_file="$(tunnel_restart_lock_path "$port")"
  owner_pid="$(tr -d '[:space:]' <"$lock_file" 2>/dev/null || true)"
  current_pid="$(current_shell_process_pid)"
  if [[ "$owner_pid" == "$current_pid" ]]; then
    rm -f "$lock_file"
  fi
}

tunnel_owner_record_path() {
  local port="$1"
  local owner_root="$LOCAL_PROD_ROOT/tunnel-locks"
  mkdir -p "$owner_root"
  printf '%s/owner-%s.pid' "$owner_root" "$port"
}

process_start_identity() {
  local pid="${1:-}"
  [[ "$pid" =~ ^[0-9]+$ ]] || return 1
  ps -o lstart= -p "$pid" 2>/dev/null \
    | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//'
}

exact_tracked_tunnel_listener_pid() {
  local port="$1"
  local command="$2"
  local pid forward expected actual executable
  pid="$(lsof -nP -iTCP:"$port" -sTCP:LISTEN -t 2>/dev/null | sort -u)"
  [[ "$pid" =~ ^[0-9]+$ ]] || return 1
  case "$command" in
    safebox-tunnel)
      forward="127.0.0.1:${LOCAL_SAFEBOX_PORT}:${SAFEBOX_PRIVATE_HOST}:${SAFEBOX_PRIVATE_PORT}"
      ;;
    dashboard-tunnel)
      forward="127.0.0.1:${LOCAL_DASHBOARD_PORT}:${REMOTE_DASHBOARD_HOST}:${REMOTE_DASHBOARD_PORT}"
      ;;
    *) return 1 ;;
  esac
  for value in "$SSH_KEY" "$SSH_HOST" "$SSH_SERVER_ALIVE_INTERVAL" "$SSH_SERVER_ALIVE_COUNT_MAX" "$forward"; do
    [[ -n "$value" && ! "$value" =~ [[:space:]] ]] || return 1
  done
  expected="ssh -i $SSH_KEY -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new -o ExitOnForwardFailure=yes -o ServerAliveInterval=$SSH_SERVER_ALIVE_INTERVAL -o ServerAliveCountMax=$SSH_SERVER_ALIVE_COUNT_MAX -o TCPKeepAlive=yes -N -L $forward $SSH_HOST"
  actual="$(ps -ww -o command= -p "$pid" 2>/dev/null | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')"
  executable="$(lsof -nP -a -p "$pid" -d txt -Fn 2>/dev/null | awk 'substr($0,1,1)=="n" {print substr($0,2); exit}')"
  [[ "$actual" == "$expected" && "$executable" == "$(command -v ssh)" ]] || return 1
  [[ -n "$(process_start_identity "$pid")" ]] || return 1
  printf '%s' "$pid"
}

adopt_exact_tracked_tunnel_locked() {
  local command="$1"
  local port="$2"
  local pid_file="${3:-}"
  local pid owner_record owner_pid owner_identity
  managed_tunnel_recorded_owner_owns_listener "$port" && return 0
  pid="$(exact_tracked_tunnel_listener_pid "$port" "$command")" || return 1
  owner_record="$(read_managed_tunnel_owner "$port")" || owner_record=""
  if [[ -n "$owner_record" ]]; then
    IFS=$'\t' read -r owner_pid owner_identity <<<"$owner_record"
    managed_tunnel_record_matches_process "$port" "$owner_pid" && return 1
    clear_managed_tunnel_owner "$port" "$owner_pid"
  fi
  record_managed_tunnel_owner "$port" "$pid" || return 1
  if [[ "$(exact_tracked_tunnel_listener_pid "$port" "$command")" != "$pid" ]] \
    || ! managed_tunnel_recorded_owner_owns_listener "$port" "$pid"; then
    clear_managed_tunnel_owner "$port" "$pid"
    return 1
  fi
  if [[ -n "$pid_file" ]]; then
    read_managed_tunnel_owner "$port" >"$pid_file"
    chmod 600 "$pid_file" 2>/dev/null || true
  fi
}

read_managed_tunnel_owner() {
  local port="$1"
  local owner_file owner_pid owner_identity
  owner_file="$(tunnel_owner_record_path "$port")"
  [[ -f "$owner_file" ]] || return 1
  IFS=$'\t' read -r owner_pid owner_identity <"$owner_file" || return 1
  [[ "$owner_pid" =~ ^[0-9]+$ ]] || return 1
  [[ -n "$owner_identity" ]] || return 1
  printf '%s\t%s\n' "$owner_pid" "$owner_identity"
}

record_managed_tunnel_owner() {
  local port="$1"
  local pid="$2"
  local owner_file owner_identity owner_tmp
  owner_identity="$(process_start_identity "$pid")"
  [[ -n "$owner_identity" ]] || return 1
  owner_file="$(tunnel_owner_record_path "$port")"
  owner_tmp="${owner_file}.$$.$RANDOM.tmp"
  printf '%s\t%s\n' "$pid" "$owner_identity" >"$owner_tmp"
  chmod 600 "$owner_tmp" 2>/dev/null || true
  mv -f "$owner_tmp" "$owner_file"
}

managed_tunnel_record_matches_process() {
  local port="$1"
  local expected_pid="${2:-}"
  local owner_record owner_pid owner_identity current_identity
  owner_record="$(read_managed_tunnel_owner "$port")" || return 1
  IFS=$'\t' read -r owner_pid owner_identity <<<"$owner_record"
  if [[ -n "$expected_pid" && "$owner_pid" != "$expected_pid" ]]; then
    return 1
  fi
  kill -0 "$owner_pid" >/dev/null 2>&1 || return 1
  current_identity="$(process_start_identity "$owner_pid")"
  [[ -n "$current_identity" && "$current_identity" == "$owner_identity" ]]
}

managed_tunnel_recorded_owner_owns_listener() {
  local port="$1"
  local expected_pid="${2:-}"
  local owner_record owner_pid owner_identity
  managed_tunnel_record_matches_process "$port" "$expected_pid" || return 1
  owner_record="$(read_managed_tunnel_owner "$port")" || return 1
  IFS=$'\t' read -r owner_pid owner_identity <<<"$owner_record"
  lsof -nP -a -p "$owner_pid" -iTCP:"$port" -sTCP:LISTEN -t 2>/dev/null \
    | grep -qx "$owner_pid"
}

clear_managed_tunnel_owner() {
  local port="$1"
  local expected_pid="${2:-}"
  local owner_file owner_record owner_pid owner_identity
  owner_file="$(tunnel_owner_record_path "$port")"
  if [[ -n "$expected_pid" ]]; then
    owner_record="$(read_managed_tunnel_owner "$port")" || return 0
    IFS=$'\t' read -r owner_pid owner_identity <<<"$owner_record"
    [[ "$owner_pid" == "$expected_pid" ]] || return 0
  fi
  rm -f "$owner_file"
}

tunnel_consumer_root_path() {
  local port="$1"
  local root="$LOCAL_PROD_ROOT/tunnel-locks/consumers-$port"
  mkdir -p "$root"
  chmod 700 "$root" 2>/dev/null || true
  printf '%s' "$root"
}

tunnel_consumer_record_path() {
  local port="$1"
  local consumer_pid="$2"
  printf '%s/%s.lease' "$(tunnel_consumer_root_path "$port")" "$consumer_pid"
}

register_managed_tunnel_consumer_locked() {
  local port="$1"
  local consumer_pid="$2"
  local identity lease_file lease_tmp
  identity="$(process_start_identity "$consumer_pid")"
  [[ -n "$identity" ]] || return 1
  lease_file="$(tunnel_consumer_record_path "$port" "$consumer_pid")"
  lease_tmp="${lease_file}.$$.$RANDOM.tmp"
  printf '%s\t%s\n' "$consumer_pid" "$identity" >"$lease_tmp"
  chmod 600 "$lease_tmp" 2>/dev/null || true
  mv -f "$lease_tmp" "$lease_file"
}

prune_managed_tunnel_consumers_locked() {
  local port="$1"
  local root lease_file consumer_pid consumer_identity current_identity
  root="$(tunnel_consumer_root_path "$port")"
  for lease_file in "$root"/*.lease; do
    [[ -f "$lease_file" ]] || continue
    consumer_pid=""
    consumer_identity=""
    IFS=$'\t' read -r consumer_pid consumer_identity <"$lease_file" || true
    if ! [[ "$consumer_pid" =~ ^[0-9]+$ ]] || [[ -z "$consumer_identity" ]] \
      || ! kill -0 "$consumer_pid" >/dev/null 2>&1; then
      rm -f "$lease_file"
      continue
    fi
    current_identity="$(process_start_identity "$consumer_pid")"
    [[ -n "$current_identity" && "$current_identity" == "$consumer_identity" ]] \
      || rm -f "$lease_file"
  done
}

managed_tunnel_consumer_count_locked() {
  local port="$1"
  local root count=0 lease_file
  prune_managed_tunnel_consumers_locked "$port"
  root="$(tunnel_consumer_root_path "$port")"
  for lease_file in "$root"/*.lease; do
    [[ -f "$lease_file" ]] && count=$((count + 1))
  done
  printf '%s' "$count"
}

release_managed_tunnel_consumer() {
  local port="$1"
  local consumer_pid="$2"
  local pid_file="${3:-}"
  local lock_status=0 lease_file
  acquire_tunnel_restart_lock_wait "$port" || lock_status="$?"
  if [[ "$lock_status" != "0" ]]; then
    [[ "$lock_status" == "1" ]] \
      && echo "takyon-prod: timed out acquiring shared tunnel lock for consumer cleanup on port $port" >&2
    return "$lock_status"
  fi
  lease_file="$(tunnel_consumer_record_path "$port" "$consumer_pid")"
  rm -f "$lease_file"
  # The tunnel is shared Mac infrastructure, not a child resource of whichever console happened
  # to start it. Keep the managed owner alive at zero leases so separately spawned/free shells do
  # not lose broker access; a later health reconciler replaces it only if it is actually unhealthy.
  managed_tunnel_consumer_count_locked "$port" >/dev/null
  [[ -n "$pid_file" ]] && rm -f "$pid_file"
  release_tunnel_restart_lock "$port"
}

terminate_recorded_tunnel_owner() {
  local port="$1"
  local expected_pid="${2:-}"
  local owner_record owner_pid owner_identity
  owner_record="$(read_managed_tunnel_owner "$port")" || return 0
  IFS=$'\t' read -r owner_pid owner_identity <<<"$owner_record"
  if [[ -n "$expected_pid" && "$owner_pid" != "$expected_pid" ]]; then
    return 0
  fi
  if managed_tunnel_record_matches_process "$port" "$owner_pid"; then
    terminate_pid "$owner_pid"
  fi
  clear_managed_tunnel_owner "$port" "$owner_pid"
}

tunnel_healthy() {
  safebox_tunnel_healthy && dashboard_tunnel_healthy
}

wait_for_url() {
  local name="$1"
  local url="$2"
  local log_file="$3"
  for _ in $(seq 1 30); do
    if curl --silent --fail --max-time 2 "$url" >/dev/null 2>&1; then
      return 0
    fi
    sleep 0.5
  done
  echo "$name tunnel did not become healthy at $url" >&2
  if [[ -f "$log_file" ]]; then
    echo "Tunnel log tail:" >&2
    tail -40 "$log_file" >&2 || true
  fi
  return 1
}

terminate_pid() {
  local pid="${1:-}"
  [[ "$pid" =~ ^[0-9]+$ ]] || return 0
  if ! kill -0 "$pid" >/dev/null 2>&1; then
    return 0
  fi
  kill "$pid" >/dev/null 2>&1 || true
  for _ in $(seq 1 20); do
    if ! kill -0 "$pid" >/dev/null 2>&1; then
      return 0
    fi
    sleep 0.25
  done
  kill -KILL "$pid" >/dev/null 2>&1 || true
}

gracefully_drain_worker_pid() {
  local pid="${1:-}"
  [[ "$pid" =~ ^[0-9]+$ ]] || return 0
  if ! kill -0 "$pid" >/dev/null 2>&1; then
    wait "$pid" >/dev/null 2>&1 || true
    return 0
  fi
  # WorkerPool handles TERM by refusing new claims and joining every in-flight handler.  A worker
  # process is never eligible for the generic five-second SIGKILL helper: doing so can leave its
  # Docker child editing a mounted business workspace after the durable claim is requeued.
  kill -TERM "$pid" >/dev/null 2>&1 || true
  while kill -0 "$pid" >/dev/null 2>&1; do
    sleep 1
  done
  wait "$pid" >/dev/null 2>&1 || true
}

local_worker_stop_grace_seconds() {
  local seconds="${TAKYON_LOCAL_WORKER_STOP_GRACE_SECONDS:-900}"
  if ! [[ "$seconds" =~ ^[0-9]+$ ]] || [[ "$seconds" -lt 5 ]]; then
    seconds="900"
  fi
  printf '%s' "$seconds"
}

stop_managed_tunnel_pid_file() {
  local pid_file="${1:-}"
  local port="$2"
  [[ -n "$pid_file" && -f "$pid_file" ]] || return 0
  local pid="" pid_identity="" owner_record owner_pid="" owner_identity="" lock_status=0
  IFS=$'\t' read -r pid pid_identity <"$pid_file" || true
  acquire_tunnel_restart_lock_wait "$port" || lock_status="$?"
  if [[ "$lock_status" != "0" ]]; then
    if [[ "$lock_status" == "1" ]]; then
      echo "takyon-prod: timed out acquiring shared tunnel lock for cleanup on port $port" >&2
    fi
    return "$lock_status"
  fi
  owner_record="$(read_managed_tunnel_owner "$port")" || owner_record=""
  if [[ -n "$owner_record" ]]; then
    IFS=$'\t' read -r owner_pid owner_identity <<<"$owner_record"
  fi
  if [[ "$pid" =~ ^[0-9]+$ \
    && -n "$pid_identity" \
    && "$pid" == "$owner_pid" \
    && "$pid_identity" == "$owner_identity" \
  ]] && managed_tunnel_record_matches_process "$port" "$pid"; then
    terminate_recorded_tunnel_owner "$port" "$pid"
  fi
  rm -f "$pid_file"
  release_tunnel_restart_lock "$port"
}

start_managed_tunnel() {
  local label="$1"
  local command="$2"
  local health_url="$3"
  local log_file="$4"
  local pid_file="$5"
  local port="$6"
  nohup "$0" "$command" </dev/null >>"$log_file" 2>&1 &
  local pid="$!"
  printf '%s\n' "$pid" >"$pid_file"
  if ! record_managed_tunnel_owner "$port" "$pid"; then
    echo "$label tunnel owner could not be recorded for pid $pid" >&2
    terminate_pid "$pid"
    rm -f "$pid_file"
    return 1
  fi
  # Keep the same PID+process-start identity in the console-local file. Cleanup compares both
  # fields with the shared owner record, so a long-lived console can never kill a replacement
  # tunnel merely because macOS eventually reused its original numeric PID.
  read_managed_tunnel_owner "$port" >"$pid_file"
  if wait_for_url "$label" "$health_url" "$log_file" \
    && managed_tunnel_recorded_owner_owns_listener "$port" "$pid"; then
    return 0
  fi
  if curl --silent --fail --max-time 2 "$health_url" >/dev/null 2>&1; then
    echo "$label health endpoint is reachable, but pid $pid does not own listener 127.0.0.1:$port; refusing false tunnel ownership" >&2
  fi
  terminate_recorded_tunnel_owner "$port" "$pid"
  rm -f "$pid_file"
  return 1
}

ensure_managed_tunnel_locked() {
  local label="$1"
  local display_url="$2"
  local health_url="$3"
  local command="$4"
  local log_file="$5"
  local pid_file="$6"
  local health_fn="$7"
  local port="$8"
  if "$health_fn"; then
    adopt_exact_tracked_tunnel_locked "$command" "$port" "$pid_file" || true
    echo "$label tunnel: already healthy at $display_url"
    return 0
  fi

  if local_tcp_port_listening "$port"; then
    # A remote service restart can briefly leave a valid SSH listener returning connection errors.
    # Confirm the failed health check before replacing a listener that this rail provably owns.
    local _health_retry
    for _health_retry in 1 2 3; do
      sleep 0.5
      if "$health_fn"; then
        adopt_exact_tracked_tunnel_locked "$command" "$port" "$pid_file" || true
        echo "$label tunnel: healthy again at $display_url"
        return 0
      fi
    done
    if managed_tunnel_recorded_owner_owns_listener "$port"; then
      echo "$label tunnel listener is owned by this rail but unhealthy; restarting under the shared lock..."
      terminate_recorded_tunnel_owner "$port"
    else
      echo "$label tunnel is unhealthy and 127.0.0.1:$port is occupied by an unowned listener; refusing to rebind or terminate an unrelated process" >&2
      return 1
    fi
  else
    # Reap a recorded process that never reached LISTEN (failed ssh setup, dead remote, or stale
    # owner record) before creating a replacement. PID start identity prevents killing a reused PID.
    terminate_recorded_tunnel_owner "$port"
  fi

  echo "Starting $label tunnel in background..."
  start_managed_tunnel "$label" "$command" "$health_url" "$log_file" "$pid_file" "$port"
}

ensure_managed_tunnel() {
  local label="$1"
  local display_url="$2"
  local health_url="$3"
  local command="$4"
  local log_file="$5"
  local pid_file="$6"
  local health_fn="$7"
  local port="$8"
  local consumer_pid="${9:-}"
  local result=0
  local lock_status=0

  acquire_tunnel_restart_lock_wait "$port" || lock_status="$?"
  if [[ "$lock_status" != "0" ]]; then
    if [[ "$lock_status" == "1" ]]; then
      echo "takyon-prod: timed out acquiring shared tunnel lock for $label on port $port" >&2
    fi
    return "$lock_status"
  fi
  ensure_managed_tunnel_locked \
    "$label" "$display_url" "$health_url" "$command" "$log_file" "$pid_file" "$health_fn" "$port" \
    || result="$?"
  if [[ "$result" == "0" && -n "$consumer_pid" ]]; then
    register_managed_tunnel_consumer_locked "$port" "$consumer_pid" || result=1
  fi
  release_tunnel_restart_lock "$port"
  return "$result"
}

monitor_console_tunnels() {
  local safebox_log="$1"
  local safebox_pid_file="$2"
  local dashboard_log="$3"
  local dashboard_pid_file="$4"
  require_tunnel_restart_lock_tool || return 2
  while true; do
    sleep "$CONSOLE_TUNNEL_MONITOR_SECONDS"
    if ! safebox_tunnel_healthy; then
      ensure_managed_tunnel \
        "Safebox" "$LOCAL_SAFEBOX_URL" "$LOCAL_SAFEBOX_URL/healthz" "safebox-tunnel" \
        "$safebox_log" "$safebox_pid_file" safebox_tunnel_healthy "$LOCAL_SAFEBOX_PORT" || true
    fi
    if [[ "$TARGET" == "dev" ]]; then
      continue
    fi
    if ! dashboard_tunnel_healthy; then
      ensure_managed_tunnel \
        "Operator dashboard" "$LOCAL_DASHBOARD_URL" "$LOCAL_DASHBOARD_URL/healthz" "dashboard-tunnel" \
        "$dashboard_log" "$dashboard_pid_file" dashboard_tunnel_healthy "$LOCAL_DASHBOARD_PORT" || true
    fi
  done
}

WORKER_TUNNEL_GUARD_MONITOR_PID=""
WORKER_TUNNEL_GUARD_TUNNEL_PID_FILE=""
WORKER_TUNNEL_GUARD_DASHBOARD_PID_FILE=""
WORKER_TUNNEL_GUARD_CHILD_PID=""
WORKER_TUNNEL_GUARD_CONSUMER_PID=""

cleanup_worker_tunnel_guard() {
  if [[ -n "${WORKER_TUNNEL_GUARD_CHILD_PID:-}" ]] && kill -0 "$WORKER_TUNNEL_GUARD_CHILD_PID" >/dev/null 2>&1; then
    gracefully_drain_worker_pid "$WORKER_TUNNEL_GUARD_CHILD_PID"
  fi
  if [[ -n "${WORKER_TUNNEL_GUARD_MONITOR_PID:-}" ]] && kill -0 "$WORKER_TUNNEL_GUARD_MONITOR_PID" >/dev/null 2>&1; then
    terminate_pid "$WORKER_TUNNEL_GUARD_MONITOR_PID"
  fi
  if [[ -n "${WORKER_TUNNEL_GUARD_CONSUMER_PID:-}" ]]; then
    release_managed_tunnel_consumer "$LOCAL_SAFEBOX_PORT" "$WORKER_TUNNEL_GUARD_CONSUMER_PID" "${WORKER_TUNNEL_GUARD_TUNNEL_PID_FILE:-}" || true
    if [[ "$TARGET" != "dev" ]]; then
      release_managed_tunnel_consumer "$LOCAL_DASHBOARD_PORT" "$WORKER_TUNNEL_GUARD_CONSUMER_PID" "${WORKER_TUNNEL_GUARD_DASHBOARD_PID_FILE:-}" || true
    fi
  fi
  WORKER_TUNNEL_GUARD_MONITOR_PID=""
  WORKER_TUNNEL_GUARD_TUNNEL_PID_FILE=""
  WORKER_TUNNEL_GUARD_DASHBOARD_PID_FILE=""
  WORKER_TUNNEL_GUARD_CHILD_PID=""
  WORKER_TUNNEL_GUARD_CONSUMER_PID=""
}

start_worker_tunnel_guard() {
  if [[ "${TAKYON_OPERATOR_TUNNELS_MANAGED:-0}" == "1" ]]; then
    WORKER_TUNNEL_GUARD_MONITOR_PID=""
    WORKER_TUNNEL_GUARD_TUNNEL_PID_FILE=""
    WORKER_TUNNEL_GUARD_DASHBOARD_PID_FILE=""
    WORKER_TUNNEL_GUARD_CHILD_PID=""
    WORKER_TUNNEL_GUARD_CONSUMER_PID=""
    return 0
  fi

  require_tunnel_restart_lock_tool || die "shared tunnel ownership preflight failed"
  mkdir -p "$LOCAL_PROD_ROOT/logs"
  local timestamp
  timestamp="$(date +%Y%m%d-%H%M%S)"
  local tunnel_log="$LOCAL_PROD_ROOT/logs/tunnel-$timestamp.log"
  local dashboard_tunnel_log="$LOCAL_PROD_ROOT/logs/dashboard-tunnel-$timestamp.log"
  WORKER_TUNNEL_GUARD_TUNNEL_PID_FILE="$LOCAL_PROD_ROOT/logs/tunnel-$timestamp.pid"
  WORKER_TUNNEL_GUARD_DASHBOARD_PID_FILE="$LOCAL_PROD_ROOT/logs/dashboard-tunnel-$timestamp.pid"
  WORKER_TUNNEL_GUARD_CONSUMER_PID="$(current_shell_process_pid)"

  trap cleanup_worker_tunnel_guard EXIT INT TERM

  if [[ "$TARGET" == "dev" ]]; then
    if ! dev_remote_safebox_configured; then
      return 0
    fi
    ensure_managed_tunnel "Dev Safebox" "$LOCAL_SAFEBOX_URL" "$LOCAL_SAFEBOX_URL/healthz" "safebox-tunnel" "$tunnel_log" "$WORKER_TUNNEL_GUARD_TUNNEL_PID_FILE" safebox_tunnel_healthy "$LOCAL_SAFEBOX_PORT" "$WORKER_TUNNEL_GUARD_CONSUMER_PID"
    monitor_console_tunnels "$tunnel_log" "$WORKER_TUNNEL_GUARD_TUNNEL_PID_FILE" "$dashboard_tunnel_log" "$WORKER_TUNNEL_GUARD_DASHBOARD_PID_FILE" >>"$tunnel_log" 2>&1 &
    WORKER_TUNNEL_GUARD_MONITOR_PID="$!"
    return 0
  fi

  ensure_managed_tunnel "Safebox" "$LOCAL_SAFEBOX_URL" "$LOCAL_SAFEBOX_URL/healthz" "safebox-tunnel" "$tunnel_log" "$WORKER_TUNNEL_GUARD_TUNNEL_PID_FILE" safebox_tunnel_healthy "$LOCAL_SAFEBOX_PORT" "$WORKER_TUNNEL_GUARD_CONSUMER_PID"
  ensure_managed_tunnel "Operator dashboard" "$LOCAL_DASHBOARD_URL" "$LOCAL_DASHBOARD_URL/healthz" "dashboard-tunnel" "$dashboard_tunnel_log" "$WORKER_TUNNEL_GUARD_DASHBOARD_PID_FILE" dashboard_tunnel_healthy "$LOCAL_DASHBOARD_PORT" "$WORKER_TUNNEL_GUARD_CONSUMER_PID"

  monitor_console_tunnels "$tunnel_log" "$WORKER_TUNNEL_GUARD_TUNNEL_PID_FILE" "$dashboard_tunnel_log" "$WORKER_TUNNEL_GUARD_DASHBOARD_PID_FILE" >>"$tunnel_log" 2>&1 &
  WORKER_TUNNEL_GUARD_MONITOR_PID="$!"
}

require_docker_for_worker() {
  # Product/site isolation is a dedicated rail: in auto/forced mode it uses Docker even when the
  # generic terminal backend is local. Skip Docker only when both rails explicitly avoid it.
  if [[ "${TERMINAL_ENV:-docker}" != "docker" ]]; then
    case "${TAKYON_CLAUDE_AGENT_DOCKER:-auto}" in
      0|false|no|off) return 0 ;;
    esac
  fi
  if ! command -v docker >/dev/null 2>&1; then
    die "Docker CLI is not installed or not on PATH; local worker compute needs Docker Desktop running"
  fi
  if ! docker version >/dev/null 2>&1; then
    die "Docker is not reachable; start Docker Desktop before running the local worker pool"
  fi
  local tracked_worker_image="takyon/claude-worker:node20-chromium-v1"
  local worker_image="${TAKYON_CLAUDE_AGENT_DOCKER_IMAGE:-$tracked_worker_image}"
  if [[ "$worker_image" == "$tracked_worker_image" ]]; then
    local worker_dockerfile="$ROOT/deploy/argon-alpha-14/takyon-claude-worker.Dockerfile"
    [[ -f "$worker_dockerfile" ]] || die "tracked Claude worker Dockerfile is missing: $worker_dockerfile"
    docker build --tag "$worker_image" - < "$worker_dockerfile" \
      || die "failed to build tracked Claude worker image: $worker_image"
  fi
  # Prove the exact image selected for product work, including operator overrides. The renderer
  # invokes this absolute Chromium path, so a generic Node image is not an acceptable substitute.
  if ! docker run --rm --entrypoint /bin/sh "$worker_image" -lc \
    'command -v agent-browser >/dev/null && test -x /usr/bin/chromium && /usr/bin/chromium --version >/dev/null'; then
    die "Claude worker image lacks the Chromium visual-preflight rail: $worker_image"
  fi
  # One validated image owns both the dedicated product-worker variable and the legacy terminal
  # fallback inherited by older/nested launch paths. This prevents preflighting one image and
  # launching another.
  export TAKYON_CLAUDE_AGENT_DOCKER_IMAGE="$worker_image"
  export TERMINAL_DOCKER_IMAGE="$worker_image"
  if ! docker run --rm \
    --entrypoint /bin/sh \
    --mount "type=bind,src=$RUNTIME_DIR,dst=/takyon-runtime,readonly" \
    "$worker_image" \
    -c 'test -d /takyon-runtime/agent && test -d /takyon-runtime/plugins/takyon' \
    >/dev/null 2>&1; then
    die "Docker cannot bind-mount the runtime checkout at $RUNTIME_DIR; move or create the checkout under a Docker Desktop shared path (normally /Users/...) before starting the production worker"
  fi
}

worker_preflight_wait_seconds() {
  local seconds="${TAKYON_WORKER_PREFLIGHT_WAIT_SECONDS:-180}"
  if ! [[ "$seconds" =~ ^[0-9]+$ ]] || [[ "$seconds" -lt 5 ]]; then
    seconds="180"
  fi
  printf '%s' "$seconds"
}

surface_worker_preflight_failure() {
  local worker_log="$1"
  echo "Local worker preflight failed; refusing to open an operator shell without the requested Mac worker." >&2
  if [[ -f "$worker_log" ]]; then
    echo "Worker preflight log:" >&2
    tail -80 "$worker_log" >&2 || true
  fi
}

wait_for_worker_preflight() {
  local worker_pid="$1"
  local ready_file="$2"
  local worker_log="$3"
  local wait_seconds attempts _attempt
  wait_seconds="$(worker_preflight_wait_seconds)"
  attempts=$((wait_seconds * 10))
  for _attempt in $(seq 1 "$attempts"); do
    if [[ -f "$ready_file" ]]; then
      # WorkerPool writes this marker atomically only after its database-backed pool registration
      # succeeds. Still require the producer process to be alive before opening the shell.
      if kill -0 "$worker_pid" >/dev/null 2>&1; then
        return 0
      fi
      wait "$worker_pid" >/dev/null 2>&1 || true
      surface_worker_preflight_failure "$worker_log"
      return 1
    fi
    if ! kill -0 "$worker_pid" >/dev/null 2>&1; then
      wait "$worker_pid" >/dev/null 2>&1 || true
      surface_worker_preflight_failure "$worker_log"
      return 1
    fi
    sleep 0.1
  done
  echo "Local worker preflight did not become ready within ${wait_seconds}s; stopping the preflight child." >&2
  terminate_pid "$worker_pid"
  wait "$worker_pid" >/dev/null 2>&1 || true
  surface_worker_preflight_failure "$worker_log"
  return 1
}

ensure_deno_toolchain() {
  # Self-heal a missing Deno (the product actions-rail runtime) into
  # $TAKYON_HOME/deno so no teammate has to `brew install deno` by hand for their
  # Mac to build+publish a product that ships actions. Non-fatal: action-free
  # products still publish (see app_actions.action_refresh_blocker). Runs in a
  # subshell so PATH/env side effects don't leak into the operator shell; the
  # binary it writes to disk persists regardless.
  local boot="$RUNTIME_DIR/scripts/lib/deno-bootstrap.sh"
  [[ -f "$boot" ]] || return 0
  ( set +e; source "$boot"; ensure_deno ) || true
  return 0
}

cmd_preflight() {
  if [[ "$TARGET" == "dev" ]]; then
    # Dev preflight = load the dev env + prove the dev Safebox is up. The prod-shaped env/storage
    # check below (R2/Cloudflare/S3) does not apply to the local dev twin.
    load_operator_env
    require_tunnel
    return 0
  fi
  load_operator_env
  require_tunnel
  cd "$ROOT"
  ensure_deno_toolchain
  PYTHONPATH="$RUNTIME_DIR" "$RUNTIME_DIR/.venv/bin/python" - <<'PY'
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

REQUIRED_ENV = (
    "TAKYON_OPERATOR_DATABASE_URL",
    "TAKYON_SAFEBOX_TOKEN",
    "TAKYON_SAFEBOX_OPERATOR_TOKEN",
    "TAKYON_SAFEBOX_URL",
    "TAKYON_DASHBOARD_URL",
    "TAKYON_STORAGE_BACKEND",
    "SUPABASE_S3_ENDPOINT",
    "SUPABASE_S3_REGION",
    "TAKYON_STORAGE_BUCKET",
    "R2_S3_ENDPOINT",
    "R2_BUCKET",
    "PUBLIC_COMPANY_BASE_DOMAIN",
    "CLOUDFLARE_ZONE_NAME",
    "TAKYON_PROVIDER_BROKER",
    "TAKYON_OPERATOR_GATEWAY_BROKER_URL",
    "TAKYON_CLAUDE_AGENT_BROKER_URL",
)

FORBIDDEN_ENV = (
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_TOKEN",
    "CLAUDE_CODE_OAUTH_TOKEN",
    "OPENAI_API_KEY",
    "OPENAI_KEY",
    "GEMINI_API_KEY",
    "GOOGLE_API_KEY",
    "TAKYON_GEMINI_API_KEY",
    "FAL_KEY",
    "FAL_API_KEY",
    "TAVILY_API_KEY",
    "FIRECRAWL_API_KEY",
    "PARALLEL_API_KEY",
    "XAI_API_KEY",
    "COMPOSIO_API_KEY",
    "META_MCP_OAUTH_TOKEN",
    "META_SYSTEM_USER_ACCESS_TOKEN",
    "META_ACCESS_TOKEN",
    "META_CAPI_TOKEN",
    "STRIPE_SECRET_KEY",
    "POSTMARK_SERVER_TOKEN",
    "VERCEL_TOKEN",
    "CLOUDFLARE_API_TOKEN",
    "SUPABASE_S3_ACCESS_KEY_ID",
    "SUPABASE_S3_SECRET_ACCESS_KEY",
    "R2_S3_ACCESS_KEY_ID",
    "R2_S3_SECRET_ACCESS_KEY",
    "SUPABASE_SERVICE_ROLE_KEY",
    "TAKYON_CAP_SIGNING_KEY",
)

rows: list[tuple[str, str, str, bool]] = []


def add(status: str, surface: str, detail: str = "", *, required: bool = True) -> None:
    rows.append((status, surface, detail, required))


def env_present(name: str, *, required: bool = True) -> None:
    add("ok" if os.environ.get(name) else "fail", f"env:{name}", "present" if os.environ.get(name) else "missing", required=required)


def http_get(url: str) -> tuple[bool, str]:
    try:
        with urllib.request.urlopen(url, timeout=4) as resp:
            return 200 <= int(getattr(resp, "status", 200) or 200) < 300, f"http {getattr(resp, 'status', 200)}"
    except Exception as exc:  # noqa: BLE001 - diagnostics only
        return False, str(exc)


for key in REQUIRED_ENV:
    env_present(key)

if os.environ.get("TAKYON_STORAGE_BACKEND") != "supabase_s3":
    add("fail", "storage backend", f"expected supabase_s3, got {os.environ.get('TAKYON_STORAGE_BACKEND')!r}")
else:
    add("ok", "storage backend", "supabase_s3")

if os.environ.get("TAKYON_PROVIDER_BROKER") != "1":
    add("fail", "provider broker", f"expected 1, got {os.environ.get('TAKYON_PROVIDER_BROKER')!r}")
else:
    add("ok", "provider broker", "enabled")

leaked = [name for name in FORBIDDEN_ENV if os.environ.get(name)]
add("fail" if leaked else "ok", "raw paid/provider/storage secrets in local process", ", ".join(leaked) if leaked else "none")

ok, detail = http_get(os.environ["TAKYON_SAFEBOX_URL"].rstrip("/") + "/healthz")
add("ok" if ok else "fail", "Safebox tunnel", detail)
ok, detail = http_get(os.environ["TAKYON_DASHBOARD_URL"].rstrip("/") + "/healthz")
add("ok" if ok else "fail", "operator dashboard/creative tunnel", detail)

try:
    from plugins.takyon import safebox, storage

    if storage.r2_configured():
        add("ok", "R2 public publish config", "configured through Safebox storage authority")
    else:
        add("fail", "R2 public publish config", "missing R2_S3_ENDPOINT or R2_BUCKET")

    for provider in ("supabase_s3", "r2"):
        try:
            safebox.storage_list_digests(provider, "homework-solver/__takyon-preflight__/")
            add("ok", f"Safebox storage broker:{provider}", "list-digests authorized")
        except Exception as exc:  # noqa: BLE001
            add("fail", f"Safebox storage broker:{provider}", str(exc))

    try:
        gsc = safebox.gsc_verification_token("https://coscale.app/")
        add("ok" if gsc.get("verification_token") else "warn", "Google Search Console", "operator broker reachable" if gsc.get("verification_token") else "empty verification token", required=False)
    except Exception as exc:  # noqa: BLE001
        add("warn", "Google Search Console", str(exc), required=False)

    try:
        from plugins.takyon import openmeter_backend
        if not openmeter_backend.enabled():
            add("warn", "OpenMeter mirror", "not configured", required=False)
        else:
            safebox.openmeter_request("GET", "/openmeter/customers", query={"limit": 1})
            add("ok", "OpenMeter mirror", "operator broker reachable", required=False)
    except Exception as exc:  # noqa: BLE001
        add("warn", "OpenMeter mirror", str(exc), required=False)
except Exception as exc:  # noqa: BLE001
    add("fail", "preflight imports/checks", str(exc))

width = max([len(surface) for _, surface, _, _ in rows] + [7])
print("Operator prod parity preflight")
print(f"{'status':<6} {'surface':<{width}} detail")
print(f"{'-' * 6} {'-' * width} {'-' * 40}")
failed = False
for status, surface, detail, required in rows:
    if status == "fail" and required:
        failed = True
    print(f"{status:<6} {surface:<{width}} {detail}")
if failed:
    sys.exit(1)
PY
}

local_worker_pids() {
  command -v pgrep >/dev/null 2>&1 || return 0
  {
    pgrep -f "$TAKYON_CLI_BIN worker --worker-id mac-operator-" 2>/dev/null || true
    pgrep -f "$TAKYON_CLI_PYTHON -m takyon_cli.main worker --worker-id mac-operator-" 2>/dev/null || true
  } | awk '!seen[$0]++'
}

collect_local_worker_pids() {
  local pids=()
  local pid
  while IFS= read -r pid; do
    [[ -n "$pid" ]] || continue
    [[ "$pid" != "$$" ]] || continue
    pids+=("$pid")
  done < <(local_worker_pids)
  if [[ "${#pids[@]}" -eq 0 ]]; then
    return 0
  fi
  printf '%s\n' "${pids[@]}"
}

_wait_for_local_worker_exit() {
  local grace_seconds="$1"
  shift || true
  local pids=("$@")
  [[ "${#pids[@]}" -gt 0 ]] || return 0

  local alive=()
  local _wait
  for _wait in $(seq 1 "$grace_seconds"); do
    alive=()
    for pid in "${pids[@]}"; do
      if kill -0 "$pid" >/dev/null 2>&1; then
        alive+=("$pid")
      fi
    done
    if [[ "${#alive[@]}" -eq 0 ]]; then
      return 0
    fi
    sleep 1
  done

  if [[ "${#alive[@]}" -gt 0 ]]; then
    echo "Force-stopping local worker pool(s) after ${grace_seconds}s: ${alive[*]}" >&2
    kill -KILL "${alive[@]}" >/dev/null 2>&1 || true
  fi
}

stop_local_workers() {
  local pids=()
  local pid
  while IFS= read -r pid; do
    [[ -n "$pid" ]] || continue
    pids+=("$pid")
  done < <(collect_local_worker_pids)

  if [[ "${#pids[@]}" -eq 0 ]]; then
    return 0
  fi

  local grace_seconds
  grace_seconds="$(local_worker_stop_grace_seconds)"
  echo "Stopping existing local worker pool(s): ${pids[*]} (grace ${grace_seconds}s)" >&2
  kill -TERM "${pids[@]}" >/dev/null 2>&1 || true
  _wait_for_local_worker_exit "$grace_seconds" "${pids[@]}"
}

stop_local_workers_background() {
  local pids=()
  local pid
  while IFS= read -r pid; do
    [[ -n "$pid" ]] || continue
    pids+=("$pid")
  done < <(collect_local_worker_pids)

  if [[ "${#pids[@]}" -eq 0 ]]; then
    return 0
  fi

  local grace_seconds
  grace_seconds="$(local_worker_stop_grace_seconds)"
  echo "Stopping existing local worker pool(s): ${pids[*]} (grace ${grace_seconds}s; replacement starts immediately)" >&2
  kill -TERM "${pids[@]}" >/dev/null 2>&1 || true
  (
    _wait_for_local_worker_exit "$grace_seconds" "${pids[@]}"
  ) >/dev/null 2>&1 &
}

cmd_safebox_tunnel() {
  require_files
  if [[ "$TARGET" == "dev" ]]; then
    load_dev_remote_topology || die "dev remote safebox metadata missing; provision the dev split first"
  fi
  echo "Opening Safebox tunnel: $LOCAL_SAFEBOX_URL -> $SAFEBOX_PRIVATE_HOST:$SAFEBOX_PRIVATE_PORT via $SSH_HOST" >&2
  ssh_tunnel_exec \
    -L "127.0.0.1:${LOCAL_SAFEBOX_PORT}:${SAFEBOX_PRIVATE_HOST}:${SAFEBOX_PRIVATE_PORT}"
}

cmd_dashboard_tunnel() {
  if [[ "$TARGET" == "dev" ]]; then
    die "dev remote mode does not expose a dashboard tunnel yet"
  fi
  require_files
  echo "Opening operator dashboard tunnel: $LOCAL_DASHBOARD_URL -> $REMOTE_DASHBOARD_HOST:$REMOTE_DASHBOARD_PORT via $SSH_HOST" >&2
  ssh_tunnel_exec \
    -L "127.0.0.1:${LOCAL_DASHBOARD_PORT}:${REMOTE_DASHBOARD_HOST}:${REMOTE_DASHBOARD_PORT}"
}

cmd_tunnel() {
  if [[ "$TARGET" == "dev" ]]; then
    cmd_safebox_tunnel
    return 0
  fi
  require_files
  echo "Opening Safebox + operator dashboard tunnels:" >&2
  echo "  $LOCAL_SAFEBOX_URL -> $SAFEBOX_PRIVATE_HOST:$SAFEBOX_PRIVATE_PORT" >&2
  echo "  $LOCAL_DASHBOARD_URL -> $REMOTE_DASHBOARD_HOST:$REMOTE_DASHBOARD_PORT" >&2
  ssh_tunnel_exec \
    -L "127.0.0.1:${LOCAL_SAFEBOX_PORT}:${SAFEBOX_PRIVATE_HOST}:${SAFEBOX_PRIVATE_PORT}" \
    -L "127.0.0.1:${LOCAL_DASHBOARD_PORT}:${REMOTE_DASHBOARD_HOST}:${REMOTE_DASHBOARD_PORT}"
}

cmd_shell() {
  load_operator_env
  require_tunnel
  cd "$ROOT"
  exec "$TAKYON_ENTRY" --logs shell "$@"
}

cmd_shell_quiet() {
  load_operator_env
  require_tunnel
  cd "$ROOT"
  exec "$TAKYON_ENTRY" shell "$@"
}

cmd_run() {
  load_operator_env
  require_tunnel
  # Preflight goes to stderr so `run --json ...` emits pure JSON on stdout — automation
  # (batch runners, jobs polls) parses stdout directly instead of sed-stripping the table.
  cmd_preflight 1>&2
  cd "$ROOT"
  exec "$TAKYON_ENTRY" "$@"
}

cmd_product_edge_deploy() {
  [[ "$TARGET" == "prod" ]] || {
    echo "takyon-prod: product-edge deployment is production-only" >&2
    return 1
  }
  local edge_dir="$ROOT/deploy/cloudflare/product-worker"
  [[ -f "$edge_dir/worker.js" && -f "$edge_dir/wrangler.toml" ]] || {
    echo "takyon-prod: tracked product-edge worker is missing" >&2
    return 1
  }
  command -v npx >/dev/null 2>&1 || {
    echo "takyon-prod: npx is required to deploy the product-edge worker" >&2
    return 1
  }
  command -v ssh >/dev/null 2>&1 || {
    echo "takyon-prod: ssh is required to read the product-edge deploy credential" >&2
    return 1
  }
  [[ -f "$SAFEBOX_SSH_KEY" ]] || {
    echo "takyon-prod: Safebox deploy key is missing" >&2
    return 1
  }
  local dirty head published
  dirty="$(git -C "$ROOT" status --porcelain --untracked-files=all)"
  [[ -z "$dirty" ]] || {
    echo "takyon-prod: refusing product-edge deploy from a dirty worktree" >&2
    return 1
  }
  git -C "$ROOT" fetch --quiet origin main || {
    echo "takyon-prod: could not verify the published origin/main revision" >&2
    return 1
  }
  head="$(git -C "$ROOT" rev-parse HEAD)"
  published="$(git -C "$ROOT" rev-parse refs/remotes/origin/main)"
  [[ "$head" == "$published" ]] || {
    echo "takyon-prod: refusing product-edge deploy from unpublished revision $head" >&2
    return 1
  }
  # CLOUDFLARE_API_TOKEN is intentionally NOT vendable through /v1/env. Retrieve this infra-only
  # deployment credential over the same root-only SSH boundary used by the tracked Safebox deploy,
  # keep it in memory, and exec pinned Wrangler with a minimal environment. The token is never
  # written to a terminal, log, or disk, or inherited alongside operator DB authority.
  cd "$ROOT"
  PYTHONPATH="$RUNTIME_DIR" exec "$RUNTIME_DIR/.venv/bin/python" - \
    "$edge_dir" "$SAFEBOX_SSH_HOST" "$SAFEBOX_SSH_KEY" "$head" <<'PY'
import json
import io
import os
from pathlib import Path
import signal
import subprocess
import sys
import tarfile
import tempfile

edge_dir = Path(sys.argv[1]).resolve()
repo_root = edge_dir.parents[2]
safebox_host = str(sys.argv[2]).strip()
safebox_key = str(sys.argv[3]).strip()
source_revision = str(sys.argv[4]).strip()
if len(source_revision) != 40 or any(ch not in "0123456789abcdef" for ch in source_revision):
    raise SystemExit("takyon-prod: product-edge source revision is invalid")
safe_names = ("PATH", "HOME", "TMPDIR", "LANG", "LC_ALL")
base_env = {name: os.environ[name] for name in safe_names if os.environ.get(name)}


class DeployTerminated(SystemExit):
    pass


def _handle_term(signum, _frame):
    raise DeployTerminated(128 + signum)


signal.signal(signal.SIGTERM, _handle_term)


def _terminate_process_group(process):
    if process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        process.communicate(timeout=5)
        return
    except subprocess.TimeoutExpired:
        pass
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    process.communicate()


def run_bounded(
    argv,
    *,
    child_env,
    timeout,
    capture_output=False,
    input_data=None,
    text=False,
):
    process = subprocess.Popen(
        argv,
        env=child_env,
        start_new_session=True,
        text=text,
        stdin=subprocess.PIPE if input_data is not None else None,
        stdout=subprocess.PIPE if capture_output else None,
        stderr=subprocess.PIPE if capture_output else None,
    )
    try:
        stdout, stderr = process.communicate(input=input_data, timeout=timeout)
    except BaseException:
        _terminate_process_group(process)
        raise
    return subprocess.CompletedProcess(argv, process.returncode, stdout, stderr)


try:
    remote = run_bounded(
        [
            "ssh",
            "-i",
            safebox_key,
            "-o",
            "IdentitiesOnly=yes",
            "-o",
            "BatchMode=yes",
            "-o",
            "ConnectTimeout=10",
            "-o",
            "StrictHostKeyChecking=accept-new",
            safebox_host,
            "env PYTHONPATH=/opt/takyon/hermes-agent-main "
            "/opt/takyon/venvs/safebox-current/bin/python -",
        ],
        input_data=(
            "import json\n"
            "import os\n"
            "from dotenv import load_dotenv\n"
            "for path in ('/opt/takyon/.takyon/.env', '/opt/takyon/secrets/.env'):\n"
            "    load_dotenv(path, override=True)\n"
            "os.environ['TAKYON_HOME'] = '/opt/takyon/.takyon'\n"
            "os.environ['TAKYON_HOST_ROLE'] = 'safebox'\n"
            "os.environ['TAKYON_ENV'] = 'prod'\n"
            "os.environ.pop('TAKYON_SAFEBOX_URL', None)\n"
            "from plugins.takyon import safebox\n"
            "value = str(safebox.read_env_backed_value('CLOUDFLARE_API_TOKEN') or '').strip()\n"
            "if not value: raise SystemExit('cloudflare token unavailable')\n"
            "print(json.dumps({'token': value}), end='')\n"
        ),
        capture_output=True,
        timeout=30,
        child_env=base_env,
        text=True,
    )
except (OSError, subprocess.TimeoutExpired):
    raise SystemExit("takyon-prod: could not read the Cloudflare credential from Safebox") from None
if remote.returncode != 0:
    raise SystemExit("takyon-prod: CLOUDFLARE_API_TOKEN is unavailable on Safebox")
try:
    payload = json.loads(str(remote.stdout or ""))
except (TypeError, ValueError):
    raise SystemExit("takyon-prod: Safebox returned an invalid Cloudflare credential response") from None
token = str(payload.get("token") or "").strip() if isinstance(payload, dict) else ""
if not token:
    raise SystemExit("takyon-prod: CLOUDFLARE_API_TOKEN is unavailable on Safebox")
env = dict(base_env)
env["CLOUDFLARE_API_TOKEN"] = token
message = f"Takyon source {source_revision}"

# Wrangler must never read ignored/untracked project state. Build its complete input from the exact
# clean origin/main Git object that passed the shell fence, into an isolated temporary directory.
archive_paths = {
    "deploy/cloudflare/product-worker/worker.js": "worker.js",
    "deploy/cloudflare/product-worker/wrangler.toml": "wrangler.toml",
}
archive_dirs = {
    "deploy",
    "deploy/cloudflare",
    "deploy/cloudflare/product-worker",
}
try:
    archive = run_bounded(
        [
            "git",
            "-C",
            str(repo_root),
            "archive",
            "--format=tar",
            source_revision,
            "--",
            *archive_paths,
        ],
        child_env=base_env,
        timeout=15,
        capture_output=True,
    )
except (OSError, subprocess.TimeoutExpired):
    raise SystemExit("takyon-prod: could not snapshot the product-edge revision") from None
if archive.returncode != 0 or not archive.stdout:
    raise SystemExit("takyon-prod: could not snapshot the product-edge revision")

with tempfile.TemporaryDirectory(prefix="takyon-product-edge-") as temp_dir:
    snapshot_edge = Path(temp_dir) / "product-worker"
    snapshot_edge.mkdir(mode=0o700)
    seen = set()
    try:
        with tarfile.open(fileobj=io.BytesIO(archive.stdout), mode="r:") as source:
            for member in source.getmembers():
                normalized = member.name.rstrip("/")
                if normalized in archive_dirs and member.isdir():
                    continue
                destination = archive_paths.get(normalized)
                if destination is None or not member.isfile() or normalized in seen:
                    raise ValueError("unexpected product-edge archive member")
                extracted = source.extractfile(member)
                if extracted is None:
                    raise ValueError("unreadable product-edge archive member")
                (snapshot_edge / destination).write_bytes(extracted.read())
                seen.add(normalized)
    except (OSError, tarfile.TarError, ValueError):
        raise SystemExit("takyon-prod: product-edge revision snapshot is invalid") from None
    if seen != set(archive_paths):
        raise SystemExit("takyon-prod: product-edge revision snapshot is incomplete")
    for name in archive_paths.values():
        path = snapshot_edge / name
        if not path.is_file() or path.is_symlink() or path.stat().st_size == 0:
            raise SystemExit("takyon-prod: product-edge revision snapshot is invalid")

    config_path = str(snapshot_edge / "wrangler.toml")
    os.chdir(snapshot_edge)
    try:
        try:
            upload = run_bounded(
                [
                    "npx",
                    "--yes",
                    "wrangler@4.110.0",
                    "versions",
                    "upload",
                    "--config",
                    config_path,
                    "--tag",
                    source_revision,
                    "--message",
                    message,
                ],
                child_env=env,
                timeout=180,
            )
        except (OSError, subprocess.TimeoutExpired):
            raise SystemExit(
                "takyon-prod: product-edge version upload failed or timed out"
            ) from None
        if upload.returncode != 0:
            raise SystemExit(upload.returncode)

        # A concurrent push or checkout after the first gate may not activate this older upload.
        # Re-fetch and compare both refs immediately before the version-only traffic activation.
        try:
            refreshed = run_bounded(
                ["git", "-C", str(repo_root), "fetch", "--quiet", "origin", "main"],
                child_env=base_env,
                timeout=30,
            )
            current_main = run_bounded(
                ["git", "-C", str(repo_root), "rev-parse", "refs/remotes/origin/main"],
                child_env=base_env,
                timeout=10,
                capture_output=True,
                text=True,
            )
            current_head = run_bounded(
                ["git", "-C", str(repo_root), "rev-parse", "HEAD"],
                child_env=base_env,
                timeout=10,
                capture_output=True,
                text=True,
            )
        except (OSError, subprocess.TimeoutExpired):
            raise SystemExit(
                "takyon-prod: could not revalidate origin/main before edge activation"
            ) from None
        if any(item.returncode != 0 for item in (refreshed, current_main, current_head)):
            raise SystemExit(
                "takyon-prod: could not revalidate origin/main before edge activation"
            )
        if {
            str(current_main.stdout or "").strip(),
            str(current_head.stdout or "").strip(),
        } != {source_revision}:
            raise SystemExit(
                "takyon-prod: origin/main or HEAD moved during edge upload; "
                "inactive version was not deployed"
            )

        try:
            deployment = run_bounded(
                [
                    "npx",
                    "--yes",
                    "wrangler@4.110.0",
                    "versions",
                    "deploy",
                    "--config",
                    config_path,
                    "--version-tag",
                    source_revision,
                    "--percentage",
                    "100",
                    "--message",
                    message,
                    "--yes",
                ],
                child_env=env,
                timeout=120,
            )
        except (OSError, subprocess.TimeoutExpired):
            raise SystemExit(
                "takyon-prod: product-edge activation failed or timed out"
            ) from None
        raise SystemExit(deployment.returncode)
    finally:
        os.chdir(repo_root)
PY
}

cmd_overview() {
  load_operator_env
  require_tunnel
  cd "$ROOT"
  local json_payload
  json_payload="$("$TAKYON_ENTRY" --json businesses)"
  TAKYON_OVERVIEW_JSON="$json_payload" "$RUNTIME_DIR/.venv/bin/python" - <<'PY'
import json
import os

payload = json.loads(os.environ["TAKYON_OVERVIEW_JSON"])
if not payload.get("success"):
    raise SystemExit(json.dumps(payload, indent=2))

controls = {}
for item in payload.get("controls") or []:
    scope = str(item.get("scope") or "")
    if scope:
        controls[scope] = item

businesses = payload.get("businesses") or []
print("Businesses")
print(f"{'state':<16} {'slug':<32} name")
print(f"{'-' * 16} {'-' * 32} {'-' * 24}")
for item in businesses:
    slug = str(item.get("slug") or item.get("business") or "").strip()
    name = str(item.get("name") or slug).strip()
    state = str((controls.get(f"business:{slug}") or {}).get("state") or item.get("status") or "active")
    app_state = str((controls.get(f"business:{slug}/app") or {}).get("state") or "")
    label = state
    if app_state and app_state != state:
        label = f"{state}/app:{app_state}"
    print(f"{label:<16.16} {slug:<32.32} {name}")
PY
}

cmd_worker() {
  local concurrency="10"
  local concurrency_set="0"
  local operator_user_id=""
  local worker_pid=""
  local worker_status="0"
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --operator-user-id)
        shift || die "usage: $0 worker [concurrency] [--user-id <id>]"
        [[ $# -gt 0 ]] || die "usage: $0 worker [concurrency] [--user-id <id>]"
        operator_user_id="$1"
        ;;
      --operator-user-id=*)
        operator_user_id="${1#*=}"
        [[ -n "$operator_user_id" ]] || die "usage: $0 worker [concurrency] [--user-id <id>]"
        ;;
      --user-id)
        shift || die "usage: $0 worker [concurrency] [--user-id <id>]"
        [[ $# -gt 0 ]] || die "usage: $0 worker [concurrency] [--user-id <id>]"
        operator_user_id="$1"
        ;;
      --user-id=*)
        operator_user_id="${1#*=}"
        [[ -n "$operator_user_id" ]] || die "usage: $0 worker [concurrency] [--user-id <id>]"
        ;;
      -h|--help)
        die "usage: $0 worker [concurrency] [--user-id <id>]"
        ;;
      *)
        if [[ "$1" =~ ^[0-9]+$ ]] && [[ "$concurrency_set" == "0" ]]; then
          concurrency="$1"
          concurrency_set="1"
        else
          die "usage: $0 worker [concurrency] [--user-id <id>]"
        fi
        ;;
    esac
    shift || true
  done
  if ! [[ "$concurrency" =~ ^[0-9]+$ ]] || [[ "$concurrency" -lt 1 ]]; then
    die "worker concurrency must be a positive integer"
  fi
  OPERATOR_USER_ID_OVERRIDE="$operator_user_id"
  require_files
  start_worker_tunnel_guard
  load_operator_env
  require_tunnel
  cmd_preflight
  require_docker_for_worker
  # A worker wrapper owns only the pool it starts.  Never stop every local pool here: another
  # operator shell may be building a DIFFERENT business, and cross-business parallelism is a
  # supported production posture.  Durable release fencing rejects incompatible pools, while the
  # per-business/workspace writer lease serializes only conflicting work.
  local worker_id=""
  worker_id="${TAKYON_WORKER_POOL_ID:-$(local_worker_pool_id_for_pid "$$")}"
  record_active_local_worker_pool "$$" "$worker_id" >/dev/null || true
  export TAKYON_WORKER_POOL_ID="$worker_id"
  export TAKYON_WORKER_CONCURRENCY="$concurrency"
  export TAKYON_WORKER_POLL_SECONDS="${TAKYON_WORKER_POLL_SECONDS:-1}"
  export TAKYON_WORKER_STALE_SECONDS="${TAKYON_WORKER_STALE_SECONDS:-900}"
  cd "$RUNTIME_DIR"
  if [[ "${TAKYON_OPERATOR_TUNNELS_MANAGED:-0}" == "1" ]]; then
    exec_takyon_cli worker \
      --worker-id "$worker_id" \
      --user-id "$(resolved_operator_user_id)"
  fi
  run_takyon_cli worker \
    --worker-id "$worker_id" \
    --user-id "$(resolved_operator_user_id)" &
  worker_pid="$!"
  WORKER_TUNNEL_GUARD_CHILD_PID="$worker_pid"
  if wait "$worker_pid"; then
    worker_status="0"
  else
    worker_status="$?"
  fi
  WORKER_TUNNEL_GUARD_CHILD_PID=""
  return "$worker_status"
}

cmd_worker_once() {
  local operator_user_id=""
  local worker_pid=""
  local worker_status="0"
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --operator-user-id)
        shift || die "usage: $0 worker-once [--user-id <id>]"
        [[ $# -gt 0 ]] || die "usage: $0 worker-once [--user-id <id>]"
        operator_user_id="$1"
        ;;
      --operator-user-id=*)
        operator_user_id="${1#*=}"
        [[ -n "$operator_user_id" ]] || die "usage: $0 worker-once [--user-id <id>]"
        ;;
      --user-id)
        shift || die "usage: $0 worker-once [--user-id <id>]"
        [[ $# -gt 0 ]] || die "usage: $0 worker-once [--user-id <id>]"
        operator_user_id="$1"
        ;;
      --user-id=*)
        operator_user_id="${1#*=}"
        [[ -n "$operator_user_id" ]] || die "usage: $0 worker-once [--user-id <id>]"
        ;;
      -h|--help)
        die "usage: $0 worker-once [--user-id <id>]"
        ;;
      *)
        die "usage: $0 worker-once [--user-id <id>]"
        ;;
    esac
    shift || true
  done
  OPERATOR_USER_ID_OVERRIDE="$operator_user_id"
  require_files
  start_worker_tunnel_guard
  load_operator_env
  require_tunnel
  cmd_preflight
  require_docker_for_worker
  cd "$RUNTIME_DIR"
  if [[ "${TAKYON_OPERATOR_TUNNELS_MANAGED:-0}" == "1" ]]; then
    exec_takyon_cli worker \
      --once \
      --worker-id "mac-operator-$(hostname -s)-once-$$" \
      --user-id "$(resolved_operator_user_id)"
  fi
  run_takyon_cli worker \
    --once \
    --worker-id "mac-operator-$(hostname -s)-once-$$" \
    --user-id "$(resolved_operator_user_id)" &
  worker_pid="$!"
  WORKER_TUNNEL_GUARD_CHILD_PID="$worker_pid"
  if wait "$worker_pid"; then
    worker_status="0"
  else
    worker_status="$?"
  fi
  WORKER_TUNNEL_GUARD_CHILD_PID=""
  return "$worker_status"
}

console_usage() {
  cat >&2 <<EOF
usage: $0 console [concurrency] [business] [--user-id <id>] [--shells N] [--quiet]
EOF
  exit 1
}

spawn_console_shell_windows() {
  local shell_count="$1"
  local shell_mode="$2"
  local business="${3:-}"
  local operator_user_id="${4:-}"
  local extra_shells=$((shell_count - 1))
  local subcommand="shell"
  local root_quoted=""
  local tail_command=""
  local command_text=""
  local session_pool_id="${TAKYON_WORKER_POOL_ID:-}"
  local session_pool_exclusive="${TAKYON_WORKER_POOL_EXCLUSIVE:-1}"
  [[ "$shell_mode" == "quiet" ]] && subcommand="quiet"
  if [[ "$extra_shells" -le 0 ]]; then
    return 0
  fi
  printf -v root_quoted '%q' "$ROOT"
  if [[ -n "$business" ]]; then
    tail_command="$(shell_join env TAKYON_OPERATOR_TARGET="$TARGET" TAKYON_SESSION_USER_ID="$operator_user_id" TAKYON_OPERATOR_TASKS_VIA_WORKER=1 TAKYON_WORKER_POOL_ID="$session_pool_id" TAKYON_WORKER_POOL_EXCLUSIVE="$session_pool_exclusive" ./scripts/takyon-operator-prod.sh "$subcommand" "$business")"
  else
    tail_command="$(shell_join env TAKYON_OPERATOR_TARGET="$TARGET" TAKYON_SESSION_USER_ID="$operator_user_id" TAKYON_OPERATOR_TASKS_VIA_WORKER=1 TAKYON_WORKER_POOL_ID="$session_pool_id" TAKYON_WORKER_POOL_EXCLUSIVE="$session_pool_exclusive" ./scripts/takyon-operator-prod.sh "$subcommand")"
  fi
  command_text="cd $root_quoted && $tail_command"
  local index=0
  for ((index = 0; index < extra_shells; index += 1)); do
    open_terminal_window "$command_text"
  done
}

run_console_shell() {
  local shell_mode="$1"
  local business="${2:-}"
  local shell_status=0
  if [[ "$shell_mode" == "quiet" ]]; then
    if [[ -n "$business" ]]; then
      echo "Opening quiet operator shell for $business..."
      "$TAKYON_ENTRY" shell "$business" || shell_status="$?"
    else
      echo "Opening quiet operator shell..."
      "$TAKYON_ENTRY" shell || shell_status="$?"
    fi
    return "$shell_status"
  fi
  if [[ -n "$business" ]]; then
    echo "Opening operator shell for $business..."
    "$TAKYON_ENTRY" --logs shell "$business" || shell_status="$?"
  else
    echo "Opening operator shell..."
    "$TAKYON_ENTRY" --logs shell || shell_status="$?"
  fi
  return "$shell_status"
}

cmd_console() {
  local concurrency="10"
  local business=""
  local shell_count="1"
  local shell_mode="shell"
  local concurrency_set="0"
  local operator_user_id=""
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --operator-user-id)
        shift || console_usage
        [[ $# -gt 0 ]] || console_usage
        operator_user_id="$1"
        ;;
      --operator-user-id=*)
        operator_user_id="${1#*=}"
        [[ -n "$operator_user_id" ]] || console_usage
        ;;
      --user-id)
        shift || console_usage
        [[ $# -gt 0 ]] || console_usage
        operator_user_id="$1"
        ;;
      --user-id=*)
        operator_user_id="${1#*=}"
        [[ -n "$operator_user_id" ]] || console_usage
        ;;
      --user)
        shift || console_usage
        [[ $# -gt 0 ]] || console_usage
        operator_user_id="$(resolve_operator_alias "$1")"
        ;;
      --user=*)
        operator_user_id="$(resolve_operator_alias "${1#*=}")"
        [[ -n "$operator_user_id" ]] || console_usage
        ;;
      --shells)
        shift || console_usage
        [[ $# -gt 0 ]] || console_usage
        shell_count="$1"
        ;;
      --quiet)
        shell_mode="quiet"
        ;;
      -h|--help)
        console_usage
        ;;
      *)
        if [[ "$1" =~ ^[0-9]+$ ]] && [[ "$concurrency_set" == "0" ]]; then
          concurrency="$1"
          concurrency_set="1"
        elif [[ -z "$business" ]]; then
          business="$1"
        else
          console_usage
        fi
        ;;
    esac
    shift || true
  done
  if ! [[ "$shell_count" =~ ^[0-9]+$ ]] || [[ "$shell_count" -lt 1 ]]; then
    die "shell count must be a positive integer"
  fi

  require_files
  require_tunnel_restart_lock_tool || die "shared tunnel ownership preflight failed"
  mkdir -p "$LOCAL_PROD_ROOT/logs"
  OPERATOR_USER_ID_OVERRIDE="$operator_user_id"

  local tunnel_monitor_pid=""
  local worker_pid=""
  local worker_ready_file=""
  local tunnel_consumer_pid=""
  local timestamp
  timestamp="$(date +%Y%m%d-%H%M%S)"
  local tunnel_log="$LOCAL_PROD_ROOT/logs/tunnel-$timestamp.log"
  local dashboard_tunnel_log="$LOCAL_PROD_ROOT/logs/dashboard-tunnel-$timestamp.log"
  local worker_log="$LOCAL_PROD_ROOT/logs/worker-$timestamp.log"
  local tunnel_pid_file="$LOCAL_PROD_ROOT/logs/tunnel-$timestamp.pid"
  local dashboard_tunnel_pid_file="$LOCAL_PROD_ROOT/logs/dashboard-tunnel-$timestamp.pid"
  worker_ready_file="$LOCAL_PROD_ROOT/logs/worker-$timestamp-$$.ready"
  tunnel_consumer_pid="$(current_shell_process_pid)"
  rm -f "$worker_ready_file"

  cleanup() {
    if [[ -n "${tunnel_monitor_pid:-}" ]] && kill -0 "$tunnel_monitor_pid" >/dev/null 2>&1; then
      terminate_pid "$tunnel_monitor_pid"
    fi
    # This console owns exactly worker_pid.  A global stop would interrupt unrelated businesses
    # being served by other same-release consoles.  TERM drains this pool and waits for its current
    # handler; the tunnel stays owned until that drain completes, and no bootstrap is SIGKILLed.
    if [[ -n "${worker_pid:-}" ]] && kill -0 "$worker_pid" >/dev/null 2>&1; then
      gracefully_drain_worker_pid "$worker_pid"
    fi
    if [[ -n "${tunnel_consumer_pid:-}" ]]; then
      release_managed_tunnel_consumer "$LOCAL_SAFEBOX_PORT" "$tunnel_consumer_pid" "$tunnel_pid_file" || true
      if [[ "$TARGET" != "dev" ]]; then
        release_managed_tunnel_consumer "$LOCAL_DASHBOARD_PORT" "$tunnel_consumer_pid" "$dashboard_tunnel_pid_file" || true
      fi
    fi
    rm -f "$worker_ready_file"
  }
  trap cleanup EXIT INT TERM

  # Prod always tunnels Safebox+dashboard. Dev remote mode tunnels Safebox only; dev local mode
  # still runs its own local safebox process.
  if [[ "$TARGET" != "dev" ]]; then
    ensure_managed_tunnel "Safebox" "$LOCAL_SAFEBOX_URL" "$LOCAL_SAFEBOX_URL/healthz" "safebox-tunnel" "$tunnel_log" "$tunnel_pid_file" safebox_tunnel_healthy "$LOCAL_SAFEBOX_PORT" "$tunnel_consumer_pid"
    ensure_managed_tunnel "Operator dashboard" "$LOCAL_DASHBOARD_URL" "$LOCAL_DASHBOARD_URL/healthz" "dashboard-tunnel" "$dashboard_tunnel_log" "$dashboard_tunnel_pid_file" dashboard_tunnel_healthy "$LOCAL_DASHBOARD_PORT" "$tunnel_consumer_pid"
  elif dev_remote_safebox_configured; then
    ensure_managed_tunnel "Dev Safebox" "$LOCAL_SAFEBOX_URL" "$LOCAL_SAFEBOX_URL/healthz" "safebox-tunnel" "$tunnel_log" "$tunnel_pid_file" safebox_tunnel_healthy "$LOCAL_SAFEBOX_PORT" "$tunnel_consumer_pid"
  fi

  load_operator_env
  cmd_preflight
  # Stage 2 (UC1): the console session OWNS its pool. Mint the pool id up front so the worker
  # child and the shell share one identity; exclusive means this pool claims ONLY this
  # session's jobs and this session's jobs are claimable only by it while the pool lives
  # (kill the console -> the pool's registry lease lapses -> jobs SPILL, never strand).
  local session_pool_id="mac-operator-$(hostname -s)-$$"
  export TAKYON_WORKER_POOL_ID="$session_pool_id"
  export TAKYON_WORKER_POOL_EXCLUSIVE="${TAKYON_WORKER_POOL_EXCLUSIVE:-1}"
  echo "Starting local worker pool: concurrency=$concurrency pool=$session_pool_id exclusive=$TAKYON_WORKER_POOL_EXCLUSIVE (log: $worker_log)"
  # The operator shell and this console share the terminal foreground process group.  A user
  # interrupt intended only for `/create` or `/product` must not also stop the session-owned worker
  # and leave later jobs queued behind its still-live pool lease.  Start the worker in a separate
  # session while retaining its exact PID so console cleanup can still TERM+drain only this pool.
  TAKYON_OPERATOR_TUNNELS_MANAGED=1 \
  TAKYON_WORKER_READY_FILE="$worker_ready_file" \
    "$TAKYON_CLI_PYTHON" -c \
      'import os, sys; os.setsid(); os.execv(sys.argv[1], sys.argv[1:])' \
      "$ROOT/scripts/takyon-operator-prod.sh" worker "$concurrency" \
      --user-id "$(resolved_operator_user_id)" >"$worker_log" 2>&1 &
  worker_pid="$!"
  if ! wait_for_worker_preflight "$worker_pid" "$worker_ready_file" "$worker_log"; then
    worker_pid=""
    cleanup
    trap - EXIT INT TERM
    return 1
  fi
  rm -f "$worker_ready_file"
  record_active_local_worker_pool "$worker_pid" "$session_pool_id" >/dev/null || true
  # This console has now proven its session-owned worker is ready.  The shell must opt long-running
  # authority tools into that durable worker lane; otherwise a business-bound shell rejects them as
  # inline authority calls even though the worker it owns is healthy and waiting.
  export TAKYON_OPERATOR_TASKS_VIA_WORKER=1

  cmd_overview
  echo
  echo "Worker log: $worker_log"
  echo "VPS worker remains delayed fallback. Exit the shell to stop this local worker."
  echo
  cd "$ROOT"
  if [[ "$shell_count" -gt 1 ]]; then
    echo "Opening $((shell_count - 1)) additional operator shell window(s)..."
    spawn_console_shell_windows "$shell_count" "$shell_mode" "$business" "$(resolved_operator_user_id)"
  fi
  local shell_status=0
  monitor_console_tunnels "$tunnel_log" "$tunnel_pid_file" "$dashboard_tunnel_log" "$dashboard_tunnel_pid_file" &
  tunnel_monitor_pid="$!"
  run_console_shell "$shell_mode" "$business" || shell_status="$?"
  cleanup
  trap - EXIT INT TERM
  return "$shell_status"
}

cmd_vps_worker() {
  local action="${1:-status}"
  case "$action" in
    status)
      ssh_base "systemctl is-enabled takyon-worker.service 2>/dev/null || true; systemctl is-active takyon-worker.service 2>/dev/null || true"
      ;;
    stop|off)
      ssh_base "systemctl stop takyon-worker.service; systemctl disable takyon-worker.service"
      ;;
    start|on)
      ssh_base "systemctl enable takyon-worker.service; systemctl start takyon-worker.service"
      ;;
    *)
      die "usage: $0 vps-worker {status|stop|start}"
      ;;
  esac
}

cmd_status() {
  echo "Local prod root:   $LOCAL_PROD_ROOT"
  echo "Operator home:     $OPERATOR_HOME"
  echo "Safebox URL:       $LOCAL_SAFEBOX_URL"
  echo "Dashboard URL:     $LOCAL_DASHBOARD_URL"
  echo "Docker broker URL: $CONTAINER_SAFEBOX_URL"
  echo "Safebox tunnel:    $(if safebox_tunnel_healthy; then echo ok; else echo missing; fi)"
  echo "Dashboard tunnel:  $(if dashboard_tunnel_healthy; then echo ok; else echo missing; fi)"
  local pids
  pids="$(local_worker_pids | tr '\n' ' ' | sed 's/[[:space:]]*$//')"
  echo "Local workers:     ${pids:-none}"
  echo "VPS worker:"
  cmd_vps_worker status | sed 's/^/  /'
}

usage() {
  cat <<EOF
Usage:
  scripts/takyon-operator-prod.sh <name>            # named operator profile → console 4 --shells 4
                                                    #   e.g.  sai   josh   (see resolve_operator_alias)
  scripts/takyon-operator-prod.sh tunnel
  scripts/takyon-operator-prod.sh safebox-tunnel
  scripts/takyon-operator-prod.sh dashboard-tunnel
  scripts/takyon-operator-prod.sh console [concurrency] [business] [--user-id <id>] [--shells N] [--quiet]
  scripts/takyon-operator-prod.sh preflight
  scripts/takyon-operator-prod.sh overview
  scripts/takyon-operator-prod.sh shell [business]
  scripts/takyon-operator-prod.sh quiet [business]
  scripts/takyon-operator-prod.sh run <takyon args...>
  scripts/takyon-operator-prod.sh product-edge-deploy
  scripts/takyon-operator-prod.sh worker [concurrency] [--user-id <id>]
  scripts/takyon-operator-prod.sh worker-once [--user-id <id>]
  scripts/takyon-operator-prod.sh stop-workers
  scripts/takyon-operator-prod.sh vps-worker {status|stop|start}
  scripts/takyon-operator-prod.sh status

Common flow:
  # Terminal 1: keep Safebox + operator creative gateway reachable from this Mac.
  scripts/takyon-operator-prod.sh tunnel

  # Terminal 2: run Mac worker pool. The VPS worker stays on as delayed fallback.
  scripts/takyon-operator-prod.sh worker 10

  # Terminal 3: local operator shell against the same prod state as app.fourmanifold.com.
  scripts/takyon-operator-prod.sh shell homework-solver

  One-terminal flow:
  scripts/takyon-operator-prod.sh console 10 homework-solver

  Multi-shell flow:
  scripts/takyon-operator-prod.sh console 1 --user-id 150e4213-4006-4dc1-9cf3-ca7ab3b4696f --shells 4
EOF
}

command="${1:-shell}"
case "$command" in
  tunnel)
    shift || true
    cmd_tunnel "$@"
    ;;
  safebox-tunnel)
    shift || true
    cmd_safebox_tunnel "$@"
    ;;
  dashboard-tunnel)
    shift || true
    cmd_dashboard_tunnel "$@"
    ;;
  shell)
    shift || true
    cmd_shell "$@"
    ;;
  console)
    shift || true
    cmd_console "$@"
    ;;
  preflight)
    shift || true
    cmd_preflight "$@"
    ;;
  overview|businesses)
    shift || true
    cmd_overview "$@"
    ;;
  quiet|shell-quiet|--no-logs)
    shift || true
    cmd_shell_quiet "$@"
    ;;
  run|exec)
    shift || true
    cmd_run "$@"
    ;;
  product-edge-deploy)
    shift || true
    cmd_product_edge_deploy "$@"
    ;;
  worker)
    shift || true
    cmd_worker "$@"
    ;;
  worker-once)
    shift || true
    cmd_worker_once "$@"
    ;;
  stop-workers|workers-stop)
    shift || true
    stop_local_workers "$@"
    ;;
  vps-worker)
    shift || true
    cmd_vps_worker "$@"
    ;;
  status)
    shift || true
    cmd_status "$@"
    ;;
  help|-h|--help)
    usage
    ;;
  *)
    # Named operator profile (sai / josh / scripts/operator-users.conf) → one-terminal console.
    if is_operator_alias "$command"; then
      resolved_user="$(resolve_operator_alias "$command")"
      shift || true
      if [[ $# -eq 0 ]]; then
        cmd_console 4 --shells 4 --user-id "$resolved_user"
      else
        cmd_console "$@" --user-id "$resolved_user"
      fi
    else
      cmd_shell "$@"
    fi
    ;;
esac
