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
  if takyon_cli_shim_ready; then
    "$TAKYON_CLI_BIN" "$@"
    return
  fi
  PYTHONPATH="$RUNTIME_DIR${PYTHONPATH:+:$PYTHONPATH}" "$TAKYON_CLI_PYTHON" -m takyon_cli.main "$@"
}

exec_takyon_cli() {
  if takyon_cli_shim_ready; then
    exec "$TAKYON_CLI_BIN" "$@"
  fi
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
  if [[ -f "$ROOT/.takyon/config.yaml" ]]; then
    if ! cmp -s "$ROOT/.takyon/config.yaml" "$OPERATOR_HOME/config.yaml" 2>/dev/null; then
      cp "$ROOT/.takyon/config.yaml" "$OPERATOR_HOME/config.yaml"
    fi
  elif [[ ! -f "$OPERATOR_HOME/config.yaml" ]] && ssh_base "test -f /opt/takyon/.takyon/config.yaml"; then
    ssh_base "cat /opt/takyon/.takyon/config.yaml" >"$OPERATOR_HOME/config.yaml"
  fi
  if ssh_base "test -f /opt/takyon/.takyon/dashboard_session_token"; then
    ssh_base "cat /opt/takyon/.takyon/dashboard_session_token" >"$OPERATOR_HOME/dashboard_session_token"
    chmod 600 "$OPERATOR_HOME/dashboard_session_token"
  fi
}

fetch_operator_env_exports() {
  ssh_base "python3 - <<'PY'
from __future__ import annotations

import os
import shlex
import subprocess
import sys

keys = {
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

for key in sorted(env):
    print(f'export {key}={shlex.quote(env[key])}')
PY"
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
      ensure_managed_tunnel "Dev Safebox" "$LOCAL_SAFEBOX_URL" "$LOCAL_SAFEBOX_URL/healthz" "safebox-tunnel" "$tunnel_log" "$tunnel_pid_file" safebox_tunnel_healthy
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
  cat >&2 <<EOF
Required local production tunnel is not reachable: ${missing[*]}.

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

local_worker_stop_grace_seconds() {
  local seconds="${TAKYON_LOCAL_WORKER_STOP_GRACE_SECONDS:-900}"
  if ! [[ "$seconds" =~ ^[0-9]+$ ]] || [[ "$seconds" -lt 5 ]]; then
    seconds="900"
  fi
  printf '%s' "$seconds"
}

pid_file_process_running() {
  local pid_file="$1"
  [[ -f "$pid_file" ]] || return 1
  local pid
  pid="$(tr -d '[:space:]' <"$pid_file" 2>/dev/null || true)"
  [[ "$pid" =~ ^[0-9]+$ ]] || return 1
  kill -0 "$pid" >/dev/null 2>&1
}

stop_pid_file_process() {
  local pid_file="$1"
  if [[ ! -f "$pid_file" ]]; then
    return 0
  fi
  local pid
  pid="$(tr -d '[:space:]' <"$pid_file" 2>/dev/null || true)"
  terminate_pid "$pid"
  rm -f "$pid_file"
}

start_managed_tunnel() {
  local label="$1"
  local command="$2"
  local health_url="$3"
  local log_file="$4"
  local pid_file="$5"
  "$0" "$command" >>"$log_file" 2>&1 &
  local pid="$!"
  printf '%s\n' "$pid" >"$pid_file"
  if wait_for_url "$label" "$health_url" "$log_file"; then
    return 0
  fi
  stop_pid_file_process "$pid_file"
  return 1
}

ensure_managed_tunnel() {
  local label="$1"
  local display_url="$2"
  local health_url="$3"
  local command="$4"
  local log_file="$5"
  local pid_file="$6"
  local health_fn="$7"
  if "$health_fn"; then
    echo "$label tunnel: already healthy at $display_url"
    return 0
  fi
  echo "Starting $label tunnel in background..."
  start_managed_tunnel "$label" "$command" "$health_url" "$log_file" "$pid_file"
}

monitor_console_tunnels() {
  local safebox_log="$1"
  local safebox_pid_file="$2"
  local dashboard_log="$3"
  local dashboard_pid_file="$4"
  while true; do
    sleep "$CONSOLE_TUNNEL_MONITOR_SECONDS"
    if ! safebox_tunnel_healthy; then
      stop_pid_file_process "$safebox_pid_file"
      echo "Safebox tunnel dropped; restarting..."
      start_managed_tunnel "Safebox" "safebox-tunnel" "$LOCAL_SAFEBOX_URL/healthz" "$safebox_log" "$safebox_pid_file" || true
    fi
    if [[ "$TARGET" == "dev" ]]; then
      continue
    fi
    if ! dashboard_tunnel_healthy; then
      stop_pid_file_process "$dashboard_pid_file"
      echo "Operator dashboard tunnel dropped; restarting..."
      start_managed_tunnel "Operator dashboard" "dashboard-tunnel" "$LOCAL_DASHBOARD_URL/healthz" "$dashboard_log" "$dashboard_pid_file" || true
    fi
  done
}

WORKER_TUNNEL_GUARD_MONITOR_PID=""
WORKER_TUNNEL_GUARD_TUNNEL_PID_FILE=""
WORKER_TUNNEL_GUARD_DASHBOARD_PID_FILE=""
WORKER_TUNNEL_GUARD_CHILD_PID=""

cleanup_worker_tunnel_guard() {
  if [[ -n "${WORKER_TUNNEL_GUARD_CHILD_PID:-}" ]] && kill -0 "$WORKER_TUNNEL_GUARD_CHILD_PID" >/dev/null 2>&1; then
    terminate_pid "$WORKER_TUNNEL_GUARD_CHILD_PID"
  fi
  if [[ -n "${WORKER_TUNNEL_GUARD_MONITOR_PID:-}" ]] && kill -0 "$WORKER_TUNNEL_GUARD_MONITOR_PID" >/dev/null 2>&1; then
    terminate_pid "$WORKER_TUNNEL_GUARD_MONITOR_PID"
  fi
  stop_pid_file_process "${WORKER_TUNNEL_GUARD_TUNNEL_PID_FILE:-}"
  stop_pid_file_process "${WORKER_TUNNEL_GUARD_DASHBOARD_PID_FILE:-}"
  WORKER_TUNNEL_GUARD_MONITOR_PID=""
  WORKER_TUNNEL_GUARD_TUNNEL_PID_FILE=""
  WORKER_TUNNEL_GUARD_DASHBOARD_PID_FILE=""
  WORKER_TUNNEL_GUARD_CHILD_PID=""
}

start_worker_tunnel_guard() {
  if [[ "${TAKYON_OPERATOR_TUNNELS_MANAGED:-0}" == "1" ]]; then
    WORKER_TUNNEL_GUARD_MONITOR_PID=""
    WORKER_TUNNEL_GUARD_TUNNEL_PID_FILE=""
    WORKER_TUNNEL_GUARD_DASHBOARD_PID_FILE=""
    WORKER_TUNNEL_GUARD_CHILD_PID=""
    return 0
  fi

  mkdir -p "$LOCAL_PROD_ROOT/logs"
  local timestamp
  timestamp="$(date +%Y%m%d-%H%M%S)"
  local tunnel_log="$LOCAL_PROD_ROOT/logs/tunnel-$timestamp.log"
  local dashboard_tunnel_log="$LOCAL_PROD_ROOT/logs/dashboard-tunnel-$timestamp.log"
  WORKER_TUNNEL_GUARD_TUNNEL_PID_FILE="$LOCAL_PROD_ROOT/logs/tunnel-$timestamp.pid"
  WORKER_TUNNEL_GUARD_DASHBOARD_PID_FILE="$LOCAL_PROD_ROOT/logs/dashboard-tunnel-$timestamp.pid"

  trap cleanup_worker_tunnel_guard EXIT INT TERM

  if [[ "$TARGET" == "dev" ]]; then
    if ! dev_remote_safebox_configured; then
      return 0
    fi
    ensure_managed_tunnel "Dev Safebox" "$LOCAL_SAFEBOX_URL" "$LOCAL_SAFEBOX_URL/healthz" "safebox-tunnel" "$tunnel_log" "$WORKER_TUNNEL_GUARD_TUNNEL_PID_FILE" safebox_tunnel_healthy
    monitor_console_tunnels "$tunnel_log" "$WORKER_TUNNEL_GUARD_TUNNEL_PID_FILE" "$dashboard_tunnel_log" "$WORKER_TUNNEL_GUARD_DASHBOARD_PID_FILE" >>"$tunnel_log" 2>&1 &
    WORKER_TUNNEL_GUARD_MONITOR_PID="$!"
    return 0
  fi

  ensure_managed_tunnel "Safebox" "$LOCAL_SAFEBOX_URL" "$LOCAL_SAFEBOX_URL/healthz" "safebox-tunnel" "$tunnel_log" "$WORKER_TUNNEL_GUARD_TUNNEL_PID_FILE" safebox_tunnel_healthy
  ensure_managed_tunnel "Operator dashboard" "$LOCAL_DASHBOARD_URL" "$LOCAL_DASHBOARD_URL/healthz" "dashboard-tunnel" "$dashboard_tunnel_log" "$WORKER_TUNNEL_GUARD_DASHBOARD_PID_FILE" dashboard_tunnel_healthy

  monitor_console_tunnels "$tunnel_log" "$WORKER_TUNNEL_GUARD_TUNNEL_PID_FILE" "$dashboard_tunnel_log" "$WORKER_TUNNEL_GUARD_DASHBOARD_PID_FILE" >>"$tunnel_log" 2>&1 &
  WORKER_TUNNEL_GUARD_MONITOR_PID="$!"
}

require_docker_for_worker() {
  if [[ "${TERMINAL_ENV:-docker}" != "docker" ]]; then
    return 0
  fi
  if ! command -v docker >/dev/null 2>&1; then
    die "Docker CLI is not installed or not on PATH; local worker compute needs Docker Desktop running"
  fi
  if ! docker version >/dev/null 2>&1; then
    die "Docker is not reachable; start Docker Desktop before running the local worker pool"
  fi
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
  stop_local_workers_background
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
  [[ "$shell_mode" == "quiet" ]] && subcommand="quiet"
  if [[ "$extra_shells" -le 0 ]]; then
    return 0
  fi
  printf -v root_quoted '%q' "$ROOT"
  if [[ -n "$business" ]]; then
    tail_command="$(shell_join env TAKYON_OPERATOR_TARGET="$TARGET" TAKYON_SESSION_USER_ID="$operator_user_id" ./scripts/takyon-operator-prod.sh "$subcommand" "$business")"
  else
    tail_command="$(shell_join env TAKYON_OPERATOR_TARGET="$TARGET" TAKYON_SESSION_USER_ID="$operator_user_id" ./scripts/takyon-operator-prod.sh "$subcommand")"
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
  mkdir -p "$LOCAL_PROD_ROOT/logs"
  OPERATOR_USER_ID_OVERRIDE="$operator_user_id"

  local tunnel_monitor_pid=""
  local worker_pid=""
  local local_worker_started="0"
  local timestamp
  timestamp="$(date +%Y%m%d-%H%M%S)"
  local tunnel_log="$LOCAL_PROD_ROOT/logs/tunnel-$timestamp.log"
  local dashboard_tunnel_log="$LOCAL_PROD_ROOT/logs/dashboard-tunnel-$timestamp.log"
  local worker_log="$LOCAL_PROD_ROOT/logs/worker-$timestamp.log"
  local tunnel_pid_file="$LOCAL_PROD_ROOT/logs/tunnel-$timestamp.pid"
  local dashboard_tunnel_pid_file="$LOCAL_PROD_ROOT/logs/dashboard-tunnel-$timestamp.pid"

  cleanup() {
    if [[ -n "${tunnel_monitor_pid:-}" ]] && kill -0 "$tunnel_monitor_pid" >/dev/null 2>&1; then
      terminate_pid "$tunnel_monitor_pid"
    fi
    if [[ "$local_worker_started" == "1" ]]; then
      stop_local_workers
    elif [[ -n "${worker_pid:-}" ]] && kill -0 "$worker_pid" >/dev/null 2>&1; then
      terminate_pid "$worker_pid"
    fi
    stop_pid_file_process "$tunnel_pid_file"
    stop_pid_file_process "$dashboard_tunnel_pid_file"
  }
  trap cleanup EXIT INT TERM

  # Prod always tunnels Safebox+dashboard. Dev remote mode tunnels Safebox only; dev local mode
  # still runs its own local safebox process.
  if [[ "$TARGET" != "dev" ]]; then
    ensure_managed_tunnel "Safebox" "$LOCAL_SAFEBOX_URL" "$LOCAL_SAFEBOX_URL/healthz" "safebox-tunnel" "$tunnel_log" "$tunnel_pid_file" safebox_tunnel_healthy
    ensure_managed_tunnel "Operator dashboard" "$LOCAL_DASHBOARD_URL" "$LOCAL_DASHBOARD_URL/healthz" "dashboard-tunnel" "$dashboard_tunnel_log" "$dashboard_tunnel_pid_file" dashboard_tunnel_healthy
  elif dev_remote_safebox_configured; then
    ensure_managed_tunnel "Dev Safebox" "$LOCAL_SAFEBOX_URL" "$LOCAL_SAFEBOX_URL/healthz" "safebox-tunnel" "$tunnel_log" "$tunnel_pid_file" safebox_tunnel_healthy
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
  TAKYON_OPERATOR_TUNNELS_MANAGED=1 "$0" worker "$concurrency" --user-id "$(resolved_operator_user_id)" >"$worker_log" 2>&1 &
  worker_pid="$!"
  sleep 1
  if kill -0 "$worker_pid" >/dev/null 2>&1; then
    record_active_local_worker_pool "$worker_pid" "$session_pool_id" >/dev/null || true
    local_worker_started="1"
  else
    worker_pid=""
    echo "Local worker unavailable; relying on delayed VPS worker fallback (log: $worker_log)." >&2
  fi

  cmd_overview
  echo
  echo "Worker log: $worker_log"
  if [[ "$local_worker_started" == "1" ]]; then
    echo "VPS worker remains delayed fallback. Exit the shell to stop this local worker."
  else
    echo "No local worker is running on this Mac. The VPS worker will claim after its queue-age delay."
  fi
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
