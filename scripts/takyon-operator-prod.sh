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
OPERATOR_HOME="${TAKYON_OPERATOR_PROD_HOME:-$LOCAL_PROD_ROOT/operator}"
DEFAULT_OPERATOR_USER_ID="${TAKYON_SESSION_USER_ID:-150e4213-4006-4dc1-9cf3-ca7ab3b4696f}"
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
  [[ -x "$TAKYON_ENTRY" ]] || die "Takyon entrypoint missing: $TAKYON_ENTRY"
  ensure_takyon_cli_runtime
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
  export TAKYON_SESSION_USER_ID="$DEFAULT_OPERATOR_USER_ID"
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
    if ! dashboard_tunnel_healthy; then
      stop_pid_file_process "$dashboard_pid_file"
      echo "Operator dashboard tunnel dropped; restarting..."
      start_managed_tunnel "Operator dashboard" "dashboard-tunnel" "$LOCAL_DASHBOARD_URL/healthz" "$dashboard_log" "$dashboard_pid_file" || true
    fi
  done
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
        token = safebox.mint_operator_session_token(
            "homework-solver",
            os.environ.get("TAKYON_SESSION_USER_ID", ""),
            max_cost_microusd=1,
            ttl_seconds=60,
        )
        add("ok" if token else "fail", "operator AI broker session", "minted scoped operator.session token" if token else "empty token")
    except Exception as exc:  # noqa: BLE001
        add("fail", "operator AI broker session", str(exc))

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

stop_local_workers() {
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

  echo "Stopping existing local worker pool(s): ${pids[*]}" >&2
  kill -TERM "${pids[@]}" >/dev/null 2>&1 || true
  sleep 1

  local alive=()
  for pid in "${pids[@]}"; do
    if kill -0 "$pid" >/dev/null 2>&1; then
      alive+=("$pid")
    fi
  done
  if [[ "${#alive[@]}" -gt 0 ]]; then
    kill -KILL "${alive[@]}" >/dev/null 2>&1 || true
  fi
}

cmd_safebox_tunnel() {
  require_files
  echo "Opening Safebox tunnel: $LOCAL_SAFEBOX_URL -> $SAFEBOX_PRIVATE_HOST:$SAFEBOX_PRIVATE_PORT via $SSH_HOST" >&2
  ssh_tunnel_exec \
    -L "127.0.0.1:${LOCAL_SAFEBOX_PORT}:${SAFEBOX_PRIVATE_HOST}:${SAFEBOX_PRIVATE_PORT}"
}

cmd_dashboard_tunnel() {
  require_files
  echo "Opening operator dashboard tunnel: $LOCAL_DASHBOARD_URL -> $REMOTE_DASHBOARD_HOST:$REMOTE_DASHBOARD_PORT via $SSH_HOST" >&2
  ssh_tunnel_exec \
    -L "127.0.0.1:${LOCAL_DASHBOARD_PORT}:${REMOTE_DASHBOARD_HOST}:${REMOTE_DASHBOARD_PORT}"
}

cmd_tunnel() {
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
  cmd_preflight
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
  local concurrency="${1:-10}"
  if ! [[ "$concurrency" =~ ^[0-9]+$ ]] || [[ "$concurrency" -lt 1 ]]; then
    die "worker concurrency must be a positive integer"
  fi
  load_operator_env
  require_tunnel
  cmd_preflight
  require_docker_for_worker
  stop_local_workers
  export TAKYON_WORKER_CONCURRENCY="$concurrency"
  export TAKYON_WORKER_POLL_SECONDS="${TAKYON_WORKER_POLL_SECONDS:-5}"
  export TAKYON_WORKER_STALE_SECONDS="${TAKYON_WORKER_STALE_SECONDS:-14400}"
  cd "$RUNTIME_DIR"
  run_takyon_cli worker --worker-id "mac-operator-$(hostname -s)-$$"
}

cmd_worker_once() {
  load_operator_env
  require_tunnel
  cmd_preflight
  require_docker_for_worker
  cd "$RUNTIME_DIR"
  run_takyon_cli worker --once --worker-id "mac-operator-$(hostname -s)-once-$$"
}

console_usage() {
  cat >&2 <<EOF
usage: $0 console [concurrency] [business] [--shells N] [--quiet]
EOF
  exit 1
}

spawn_console_shell_windows() {
  local shell_count="$1"
  local shell_mode="$2"
  local business="${3:-}"
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
    tail_command="$(shell_join exec ./scripts/takyon-operator-prod.sh "$subcommand" "$business")"
  else
    tail_command="$(shell_join exec ./scripts/takyon-operator-prod.sh "$subcommand")"
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
  while [[ $# -gt 0 ]]; do
    case "$1" in
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

  local tunnel_monitor_pid=""
  local worker_pid=""
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
    if [[ -n "${worker_pid:-}" ]] && kill -0 "$worker_pid" >/dev/null 2>&1; then
      terminate_pid "$worker_pid"
    fi
    stop_pid_file_process "$tunnel_pid_file"
    stop_pid_file_process "$dashboard_tunnel_pid_file"
  }
  trap cleanup EXIT INT TERM

  ensure_managed_tunnel "Safebox" "$LOCAL_SAFEBOX_URL" "$LOCAL_SAFEBOX_URL/healthz" "safebox-tunnel" "$tunnel_log" "$tunnel_pid_file" safebox_tunnel_healthy
  ensure_managed_tunnel "Operator dashboard" "$LOCAL_DASHBOARD_URL" "$LOCAL_DASHBOARD_URL/healthz" "dashboard-tunnel" "$dashboard_tunnel_log" "$dashboard_tunnel_pid_file" dashboard_tunnel_healthy

  load_operator_env
  cmd_preflight
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
  if [[ "$shell_count" -gt 1 ]]; then
    echo "Opening $((shell_count - 1)) additional operator shell window(s)..."
    spawn_console_shell_windows "$shell_count" "$shell_mode" "$business"
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
  scripts/takyon-operator-prod.sh tunnel
  scripts/takyon-operator-prod.sh safebox-tunnel
  scripts/takyon-operator-prod.sh dashboard-tunnel
  scripts/takyon-operator-prod.sh console [concurrency] [business] [--shells N] [--quiet]
  scripts/takyon-operator-prod.sh preflight
  scripts/takyon-operator-prod.sh overview
  scripts/takyon-operator-prod.sh shell [business]
  scripts/takyon-operator-prod.sh quiet [business]
  scripts/takyon-operator-prod.sh run <takyon args...>
  scripts/takyon-operator-prod.sh worker [concurrency]
  scripts/takyon-operator-prod.sh worker-once
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
  scripts/takyon-operator-prod.sh console 1 --shells 4
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
    cmd_shell "$@"
    ;;
esac
