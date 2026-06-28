#!/usr/bin/env bash
set -euo pipefail

# Local production operator rail.
#
# This is NOT local-dev and NOT a dashboard tunnel. It runs the operator shell/worker on this Mac
# against the same production control plane as app.fourmanifold.com, while reaching the private
# Safebox through an explicit SSH tunnel. Product/sub-user serving stays on the sub-user plane.

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUNTIME_DIR="$ROOT/hermes-agent-main"
TAKYON_ENTRY="$ROOT/takyon"
TAKYON_CLI_BIN="$RUNTIME_DIR/.venv/bin/takyon-cli"

SSH_HOST="${TAKYON_OPERATOR_VPS_HOST:-root@137.184.75.57}"
SSH_KEY="${TAKYON_OPERATOR_VPS_KEY:-$HOME/.ssh/takyon_argon_alpha14}"
SAFEBOX_PRIVATE_HOST="${TAKYON_REMOTE_SAFEBOX_PRIVATE_HOST:-10.116.0.2}"
SAFEBOX_PRIVATE_PORT="${TAKYON_REMOTE_SAFEBOX_PRIVATE_PORT:-8000}"
LOCAL_SAFEBOX_PORT="${TAKYON_LOCAL_SAFEBOX_PORT:-8765}"
LOCAL_SAFEBOX_URL="${TAKYON_LOCAL_SAFEBOX_URL:-http://127.0.0.1:${LOCAL_SAFEBOX_PORT}}"
CONTAINER_SAFEBOX_URL="${TAKYON_CONTAINER_SAFEBOX_URL:-http://host.docker.internal:${LOCAL_SAFEBOX_PORT}}"
LOCAL_PROD_ROOT="${TAKYON_OPERATOR_PROD_ROOT:-$HOME/.takyon-fourmanifold-operator-prod}"
OPERATOR_HOME="${TAKYON_OPERATOR_PROD_HOME:-$LOCAL_PROD_ROOT/operator}"
DEFAULT_OPERATOR_USER_ID="${TAKYON_SESSION_USER_ID:-150e4213-4006-4dc1-9cf3-ca7ab3b4696f}"

ssh_base() {
  ssh -i "$SSH_KEY" -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new "$SSH_HOST" "$@"
}

die() {
  echo "takyon-prod: $*" >&2
  exit 1
}

require_files() {
  [[ -x "$TAKYON_ENTRY" ]] || die "Takyon entrypoint missing: $TAKYON_ENTRY"
  [[ -x "$TAKYON_CLI_BIN" ]] || die "takyon-cli missing: $TAKYON_CLI_BIN"
  [[ -f "$SSH_KEY" ]] || die "SSH key missing: $SSH_KEY"
}

ensure_home() {
  mkdir -p "$OPERATOR_HOME" "$LOCAL_PROD_ROOT/logs"
  if [[ ! -f "$OPERATOR_HOME/config.yaml" ]]; then
    if ssh_base "test -f /opt/takyon/.takyon/config.yaml"; then
      ssh_base "cat /opt/takyon/.takyon/config.yaml" >"$OPERATOR_HOME/config.yaml"
    elif [[ -f "$ROOT/.takyon/config.yaml" ]]; then
      cp "$ROOT/.takyon/config.yaml" "$OPERATOR_HOME/config.yaml"
    fi
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
  require_files
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
  export TAKYON_SESSION_USER_ID="$DEFAULT_OPERATOR_USER_ID"
  export TERMINAL_DOCKER_MOUNT_CWD_TO_WORKSPACE="${TERMINAL_DOCKER_MOUNT_CWD_TO_WORKSPACE:-true}"
  export TERMINAL_CONTAINER_PERSISTENT="${TERMINAL_CONTAINER_PERSISTENT:-false}"
  unset TAKYON_DOCKER_BINARY TAKYON_DOCKER_BROKER_URL TAKYON_DOCKER_BROKER_TOKEN
}

require_tunnel() {
  if curl --silent --fail --max-time 2 "$LOCAL_SAFEBOX_URL/healthz" >/dev/null 2>&1; then
    return 0
  fi
  cat >&2 <<EOF
Safebox tunnel is not reachable at $LOCAL_SAFEBOX_URL.

Start it in another terminal:
  scripts/takyon-operator-prod.sh tunnel
EOF
  exit 1
}

tunnel_healthy() {
  curl --silent --fail --max-time 2 "$LOCAL_SAFEBOX_URL/healthz" >/dev/null 2>&1
}

wait_for_tunnel() {
  local log_file="$1"
  for _ in $(seq 1 30); do
    if tunnel_healthy; then
      return 0
    fi
    sleep 0.5
  done
  echo "Safebox tunnel did not become healthy at $LOCAL_SAFEBOX_URL" >&2
  if [[ -f "$log_file" ]]; then
    echo "Tunnel log tail:" >&2
    tail -40 "$log_file" >&2 || true
  fi
  return 1
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

cmd_tunnel() {
  require_files
  echo "Opening Safebox tunnel: $LOCAL_SAFEBOX_URL -> $SAFEBOX_PRIVATE_HOST:$SAFEBOX_PRIVATE_PORT via $SSH_HOST" >&2
  exec ssh \
    -i "$SSH_KEY" \
    -o IdentitiesOnly=yes \
    -o StrictHostKeyChecking=accept-new \
    -N \
    -L "127.0.0.1:${LOCAL_SAFEBOX_PORT}:${SAFEBOX_PRIVATE_HOST}:${SAFEBOX_PRIVATE_PORT}" \
    "$SSH_HOST"
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
  local concurrency="${1:-10}"
  if ! [[ "$concurrency" =~ ^[0-9]+$ ]] || [[ "$concurrency" -lt 1 ]]; then
    die "worker concurrency must be a positive integer"
  fi
  load_operator_env
  require_tunnel
  require_docker_for_worker
  export TAKYON_WORKER_CONCURRENCY="$concurrency"
  export TAKYON_WORKER_POLL_SECONDS="${TAKYON_WORKER_POLL_SECONDS:-5}"
  cd "$RUNTIME_DIR"
  exec "$TAKYON_CLI_BIN" worker --worker-id "mac-operator-$(hostname -s)-$$"
}

cmd_worker_once() {
  load_operator_env
  require_tunnel
  require_docker_for_worker
  cd "$RUNTIME_DIR"
  exec "$TAKYON_CLI_BIN" worker --once --worker-id "mac-operator-$(hostname -s)-once-$$"
}

cmd_console() {
  local concurrency="10"
  local business=""
  if [[ "${1:-}" =~ ^[0-9]+$ ]]; then
    concurrency="$1"
    shift || true
  fi
  business="${1:-}"

  require_files
  mkdir -p "$LOCAL_PROD_ROOT/logs"

  local tunnel_pid=""
  local worker_pid=""
  local timestamp
  timestamp="$(date +%Y%m%d-%H%M%S)"
  local tunnel_log="$LOCAL_PROD_ROOT/logs/tunnel-$timestamp.log"
  local worker_log="$LOCAL_PROD_ROOT/logs/worker-$timestamp.log"

  cleanup() {
    if [[ -n "$worker_pid" ]] && kill -0 "$worker_pid" >/dev/null 2>&1; then
      kill "$worker_pid" >/dev/null 2>&1 || true
      wait "$worker_pid" >/dev/null 2>&1 || true
    fi
    if [[ -n "$tunnel_pid" ]] && kill -0 "$tunnel_pid" >/dev/null 2>&1; then
      kill "$tunnel_pid" >/dev/null 2>&1 || true
      wait "$tunnel_pid" >/dev/null 2>&1 || true
    fi
  }
  trap cleanup EXIT INT TERM

  if tunnel_healthy; then
    echo "Safebox tunnel: already healthy at $LOCAL_SAFEBOX_URL"
  else
    echo "Starting Safebox tunnel in background..."
    "$0" tunnel >"$tunnel_log" 2>&1 &
    tunnel_pid="$!"
    wait_for_tunnel "$tunnel_log"
  fi

  load_operator_env
  require_docker_for_worker
  echo "Starting local worker pool: concurrency=$concurrency (log: $worker_log)"
  "$0" worker "$concurrency" >"$worker_log" 2>&1 &
  worker_pid="$!"
  sleep 1
  if ! kill -0 "$worker_pid" >/dev/null 2>&1; then
    echo "Local worker exited immediately." >&2
    tail -80 "$worker_log" >&2 || true
    exit 1
  fi

  cmd_overview
  echo
  echo "Worker log: $worker_log"
  echo "VPS worker remains delayed fallback. Exit the shell to stop this local worker."
  echo
  cd "$ROOT"
  if [[ -n "$business" ]]; then
    echo "Opening operator shell for $business..."
    "$TAKYON_ENTRY" --logs shell "$business"
  else
    echo "Opening operator shell..."
    "$TAKYON_ENTRY" --logs shell
  fi
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
  echo "Docker broker URL: $CONTAINER_SAFEBOX_URL"
  echo "Tunnel health:     $(if curl --silent --fail --max-time 2 "$LOCAL_SAFEBOX_URL/healthz" >/dev/null 2>&1; then echo ok; else echo missing; fi)"
  echo "VPS worker:"
  cmd_vps_worker status | sed 's/^/  /'
}

usage() {
  cat <<EOF
Usage:
  scripts/takyon-operator-prod.sh tunnel
  scripts/takyon-operator-prod.sh console [concurrency] [business]
  scripts/takyon-operator-prod.sh overview
  scripts/takyon-operator-prod.sh shell [business]
  scripts/takyon-operator-prod.sh quiet [business]
  scripts/takyon-operator-prod.sh worker [concurrency]
  scripts/takyon-operator-prod.sh worker-once
  scripts/takyon-operator-prod.sh vps-worker {status|stop|start}
  scripts/takyon-operator-prod.sh status

Common flow:
  # Terminal 1: keep private Safebox reachable from this Mac.
  scripts/takyon-operator-prod.sh tunnel

  # Terminal 2: run Mac worker pool. The VPS worker stays on as delayed fallback.
  scripts/takyon-operator-prod.sh worker 10

  # Terminal 3: local operator shell against the same prod state as app.fourmanifold.com.
  scripts/takyon-operator-prod.sh shell homework-solver

One-terminal flow:
  scripts/takyon-operator-prod.sh console 10 homework-solver
EOF
}

command="${1:-shell}"
case "$command" in
  tunnel)
    shift || true
    cmd_tunnel "$@"
    ;;
  shell)
    shift || true
    cmd_shell "$@"
    ;;
  console)
    shift || true
    cmd_console "$@"
    ;;
  overview|businesses)
    shift || true
    cmd_overview "$@"
    ;;
  quiet|shell-quiet|--no-logs)
    shift || true
    cmd_shell_quiet "$@"
    ;;
  worker)
    shift || true
    cmd_worker "$@"
    ;;
  worker-once)
    shift || true
    cmd_worker_once "$@"
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
    cmd_shell "$@"
    ;;
esac
