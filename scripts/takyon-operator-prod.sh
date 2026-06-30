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

ssh_base() {
  ssh -i "$SSH_KEY" -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new "$SSH_HOST" "$@"
}

die() {
  echo "takyon-prod: $*" >&2
  exit 1
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
  if ! takyon_cli_shim_ready && ! takyon_cli_fallback_ready; then
    die "takyon CLI missing: expected $TAKYON_CLI_BIN or runnable fallback via $TAKYON_CLI_PYTHON -m takyon_cli.main"
  fi
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

cmd_preflight() {
  load_operator_env
  require_tunnel
  cd "$ROOT"
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
  exec ssh \
    -i "$SSH_KEY" \
    -o IdentitiesOnly=yes \
    -o StrictHostKeyChecking=accept-new \
    -N \
    -L "127.0.0.1:${LOCAL_SAFEBOX_PORT}:${SAFEBOX_PRIVATE_HOST}:${SAFEBOX_PRIVATE_PORT}" \
    "$SSH_HOST"
}

cmd_dashboard_tunnel() {
  require_files
  echo "Opening operator dashboard tunnel: $LOCAL_DASHBOARD_URL -> $REMOTE_DASHBOARD_HOST:$REMOTE_DASHBOARD_PORT via $SSH_HOST" >&2
  exec ssh \
    -i "$SSH_KEY" \
    -o IdentitiesOnly=yes \
    -o StrictHostKeyChecking=accept-new \
    -N \
    -L "127.0.0.1:${LOCAL_DASHBOARD_PORT}:${REMOTE_DASHBOARD_HOST}:${REMOTE_DASHBOARD_PORT}" \
    "$SSH_HOST"
}

cmd_tunnel() {
  require_files
  echo "Opening Safebox + operator dashboard tunnels:" >&2
  echo "  $LOCAL_SAFEBOX_URL -> $SAFEBOX_PRIVATE_HOST:$SAFEBOX_PRIVATE_PORT" >&2
  echo "  $LOCAL_DASHBOARD_URL -> $REMOTE_DASHBOARD_HOST:$REMOTE_DASHBOARD_PORT" >&2
  exec ssh \
    -i "$SSH_KEY" \
    -o IdentitiesOnly=yes \
    -o StrictHostKeyChecking=accept-new \
    -N \
    -L "127.0.0.1:${LOCAL_SAFEBOX_PORT}:${SAFEBOX_PRIVATE_HOST}:${SAFEBOX_PRIVATE_PORT}" \
    -L "127.0.0.1:${LOCAL_DASHBOARD_PORT}:${REMOTE_DASHBOARD_HOST}:${REMOTE_DASHBOARD_PORT}" \
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
  local dashboard_tunnel_pid=""
  local worker_pid=""
  local timestamp
  timestamp="$(date +%Y%m%d-%H%M%S)"
  local tunnel_log="$LOCAL_PROD_ROOT/logs/tunnel-$timestamp.log"
  local dashboard_tunnel_log="$LOCAL_PROD_ROOT/logs/dashboard-tunnel-$timestamp.log"
  local worker_log="$LOCAL_PROD_ROOT/logs/worker-$timestamp.log"

  cleanup() {
    if [[ -n "${worker_pid:-}" ]] && kill -0 "$worker_pid" >/dev/null 2>&1; then
      kill "$worker_pid" >/dev/null 2>&1 || true
      wait "$worker_pid" >/dev/null 2>&1 || true
    fi
    if [[ -n "${tunnel_pid:-}" ]] && kill -0 "$tunnel_pid" >/dev/null 2>&1; then
      kill "$tunnel_pid" >/dev/null 2>&1 || true
      wait "$tunnel_pid" >/dev/null 2>&1 || true
    fi
    if [[ -n "${dashboard_tunnel_pid:-}" ]] && kill -0 "$dashboard_tunnel_pid" >/dev/null 2>&1; then
      kill "$dashboard_tunnel_pid" >/dev/null 2>&1 || true
      wait "$dashboard_tunnel_pid" >/dev/null 2>&1 || true
    fi
  }
  trap cleanup EXIT INT TERM

  if safebox_tunnel_healthy; then
    echo "Safebox tunnel: already healthy at $LOCAL_SAFEBOX_URL"
  else
    echo "Starting Safebox tunnel in background..."
    "$0" safebox-tunnel >"$tunnel_log" 2>&1 &
    tunnel_pid="$!"
    wait_for_url "Safebox" "$LOCAL_SAFEBOX_URL/healthz" "$tunnel_log"
  fi

  if dashboard_tunnel_healthy; then
    echo "Operator dashboard tunnel: already healthy at $LOCAL_DASHBOARD_URL"
  else
    echo "Starting operator dashboard tunnel in background..."
    "$0" dashboard-tunnel >"$dashboard_tunnel_log" 2>&1 &
    dashboard_tunnel_pid="$!"
    wait_for_url "Operator dashboard" "$LOCAL_DASHBOARD_URL/healthz" "$dashboard_tunnel_log"
  fi

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
  local shell_status=0
  if [[ -n "$business" ]]; then
    echo "Opening operator shell for $business..."
    "$TAKYON_ENTRY" --logs shell "$business" || shell_status="$?"
  else
    echo "Opening operator shell..."
    "$TAKYON_ENTRY" --logs shell || shell_status="$?"
  fi
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
  scripts/takyon-operator-prod.sh console [concurrency] [business]
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
