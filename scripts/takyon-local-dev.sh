#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TAKYON_ENTRY="$ROOT/takyon"
SOURCE_CONFIG="$ROOT/.takyon/config.yaml"

LOCAL_DEV_ROOT="${TAKYON_LOCAL_DEV_ROOT:-$HOME/.takyon-fourmanifold-local-dev}"
OPERATOR_HOME="${TAKYON_LOCAL_OPERATOR_HOME:-$LOCAL_DEV_ROOT/operator}"
SAFEBOX_HOME="${TAKYON_LOCAL_SAFEBOX_HOME:-$LOCAL_DEV_ROOT/safebox}"
SAFEBOX_PORT="${TAKYON_LOCAL_SAFEBOX_PORT:-8765}"
SAFEBOX_URL="${TAKYON_LOCAL_SAFEBOX_URL:-http://127.0.0.1:${SAFEBOX_PORT}}"
SAFEBOX_TOKEN="${TAKYON_LOCAL_SAFEBOX_TOKEN:-takyon-local-dev-token}"
SAFEBOX_LOG="${TAKYON_LOCAL_SAFEBOX_LOG:-$LOCAL_DEV_ROOT/safebox.log}"
LOCAL_STORAGE_BACKEND="${TAKYON_LOCAL_STORAGE_BACKEND:-local}"
LOCAL_DEV_TOPUP_CENTS="${TAKYON_LOCAL_DEV_TOPUP_CENTS:-50000}"
ALLOW_REMOTE_DB="${TAKYON_LOCAL_DEV_ALLOW_REMOTE_DB:-0}"
LOCAL_ENFORCE_SUPABASE_AUTH="${TAKYON_LOCAL_ENFORCE_SUPABASE_AUTH:-1}"
SKIP_CREATE_FOLLOWUP="${TAKYON_LOCAL_DEV_SKIP_CREATE_FOLLOWUP:-0}"
CREATE_FOLLOWUP_MAX_TURNS="${TAKYON_LOCAL_DEV_CREATE_FOLLOWUP_MAX_TURNS:-12}"
CREATE_FOLLOWUP_PROMPT="${TAKYON_LOCAL_DEV_CREATE_FOLLOWUP_PROMPT:-Immediately load and execute takyon-product-workflow in this same run. Do not stop after takyon-build-product. If the current source is still a starter shell, generic access/account UI, or / redirects instead of showing a real product-specific landing page, repair that bootstrap defect first and then continue into the real in-app product workflow. Do not stop at workflow_pending, starter access shell, or placeholder product state; keep going until there is a real landing page and a real in-app product workflow, or return one exact blocker.}"
SUPABASE_AUTH_HELPER="$ROOT/deploy/shared/supabase-auth-env.sh"

find_python() {
  local candidate
  for candidate in \
    "$ROOT/hermes-agent-main/.venv/bin/python" \
    "$ROOT/hermes-agent-main/venv/bin/python"
  do
    if [ -x "$candidate" ]; then
      printf '%s\n' "$candidate"
      return 0
    fi
  done
  return 1
}

PYTHON_BIN="$(find_python || true)"

ensure_layout() {
  mkdir -p "$LOCAL_DEV_ROOT" "$OPERATOR_HOME" "$SAFEBOX_HOME"

  if [ ! -f "$OPERATOR_HOME/config.yaml" ] && [ -f "$SOURCE_CONFIG" ]; then
    cp "$SOURCE_CONFIG" "$OPERATOR_HOME/config.yaml"
  fi

  if [ ! -f "$SAFEBOX_HOME/.env" ]; then
    cat >"$SAFEBOX_HOME/.env" <<'EOF'
# Local Takyon dev secrets live here. This file is outside the repo on purpose.
# Fill in the real values your local dev rail needs.
#
# Common keys:
# DATABASE_URL=postgresql://...
# ANTHROPIC_API_KEY=...
# OPENAI_API_KEY=...
EOF
    chmod 600 "$SAFEBOX_HOME/.env"
  fi
}

require_python() {
  if [ -n "$PYTHON_BIN" ]; then
    return 0
  fi
  echo "Missing Python virtualenv for local Takyon dev." >&2
  echo "Expected one of:" >&2
  echo "  $ROOT/hermes-agent-main/.venv/bin/python" >&2
  echo "  $ROOT/hermes-agent-main/venv/bin/python" >&2
  exit 1
}

validate_local_supabase_auth() {
  if [[ "$LOCAL_ENFORCE_SUPABASE_AUTH" != "1" ]]; then
    return 0
  fi
  if [[ ! -x "$SUPABASE_AUTH_HELPER" ]]; then
    echo "Missing Supabase auth helper: $SUPABASE_AUTH_HELPER" >&2
    exit 1
  fi
  "$SUPABASE_AUTH_HELPER" validate-file "$SAFEBOX_HOME/.env" >/dev/null
}

prepare_local_database() {
  ensure_layout
  require_python

  (
    cd "$ROOT/hermes-agent-main"
    env \
      TAKYON_HOME="$SAFEBOX_HOME" \
      TAKYON_ALLOW_POSTGRES_OUTSIDE_VPS=1 \
      TAKYON_LOCAL_DEV_ALLOW_REMOTE_DB="$ALLOW_REMOTE_DB" \
      "$PYTHON_BIN" - <<'PY'
from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import urlparse

import psycopg
from psycopg.rows import dict_row

from plugins.takyon.core import load_takyon_env
from plugins.takyon.db.runner import run_migrations
from plugins.takyon.runtime_app import resolve_database_url


def _local_database_url_from_env_file() -> str:
    env_path = Path(os.environ["TAKYON_HOME"]) / ".env"
    if not env_path.exists():
        raise SystemExit(f"Local dev secrets file missing: {env_path}")
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key.strip() == "DATABASE_URL":
            return value.strip().strip('"').strip("'")
    raise SystemExit(
        f"DATABASE_URL missing from local dev secrets file: {env_path}. "
        "Point it at a local Postgres DSN."
    )


load_takyon_env()
database_url = resolve_database_url(_local_database_url_from_env_file())
parsed = urlparse(database_url)
host = (parsed.hostname or "").strip().lower()
dbname = (parsed.path or "/").lstrip("/") or "postgres"
allow_remote = str(os.environ.get("TAKYON_LOCAL_DEV_ALLOW_REMOTE_DB") or "").strip().lower() in {
    "1", "true", "yes", "on"
}
if host not in {"127.0.0.1", "localhost", "::1"} and not allow_remote:
    raise SystemExit(
        "scripts/takyon-local-dev.sh requires a LOCAL Postgres DATABASE_URL by default; "
        f"current host={host or 'unset'}. Set DATABASE_URL to a local DSN "
        "(for example postgresql://<user>@127.0.0.1:54329/takyon_local_dev), "
        "or set TAKYON_LOCAL_DEV_ALLOW_REMOTE_DB=1 for an intentional temporary override."
    )

admin_kwargs = {
    "host": parsed.hostname,
    "port": parsed.port,
    "user": parsed.username,
    "password": parsed.password,
    "dbname": "postgres",
    "autocommit": True,
    "prepare_threshold": None,
    "row_factory": dict_row,
}

with psycopg.connect(**admin_kwargs) as admin:
    exists = admin.execute("SELECT 1 FROM pg_database WHERE datname = %s", (dbname,)).fetchone()
    if not exists:
        admin.execute(f'CREATE DATABASE "{dbname}"')

with psycopg.connect(database_url, autocommit=True, prepare_threshold=None, row_factory=dict_row) as conn:
    run_migrations(conn)
PY
  )
}

seed_local_platform_owner() {
  ensure_layout
  require_python

  (
    cd "$ROOT/hermes-agent-main"
    env \
      TAKYON_HOME="$OPERATOR_HOME" \
      TAKYON_SAFEBOX_URL="$SAFEBOX_URL" \
      TAKYON_SAFEBOX_TOKEN="$SAFEBOX_TOKEN" \
      TAKYON_STORAGE_BACKEND="$LOCAL_STORAGE_BACKEND" \
      TAKYON_LOCAL_DEV_TOPUP_CENTS="$LOCAL_DEV_TOPUP_CENTS" \
      TAKYON_ALLOW_POSTGRES_OUTSIDE_VPS=1 \
      "$PYTHON_BIN" - <<'PY'
import os

from plugins.takyon.core import TakyonStore
from plugins.takyon import billing, control_plane

store = TakyonStore()
topup_cents = max(0, int(str(os.environ.get("TAKYON_LOCAL_DEV_TOPUP_CENTS") or "0").strip() or "0"))
with store._connect() as conn:
    with store._leaf_conn(conn) as raw:
        user_id, _raw_key = control_plane.ensure_platform_owner(raw)
        if topup_cents > 0:
            billing.topup(raw, user_id, topup_cents, f"local-dev-topup:{user_id}:v1")
PY
  ) >/dev/null
}

run_local_bootstrap_job() {
  local slug="$1"
  ensure_layout
  require_python

  (
    cd "$ROOT/hermes-agent-main"
    env \
      TAKYON_HOME="$OPERATOR_HOME" \
      TAKYON_SAFEBOX_URL="$SAFEBOX_URL" \
      TAKYON_SAFEBOX_TOKEN="$SAFEBOX_TOKEN" \
      TAKYON_STORAGE_BACKEND="$LOCAL_STORAGE_BACKEND" \
      TAKYON_ALLOW_POSTGRES_OUTSIDE_VPS=1 \
      "$PYTHON_BIN" - "$slug" <<'PY'
from __future__ import annotations

import json
import os
import sys

import psycopg
from psycopg.rows import dict_row, tuple_row

from plugins.takyon.core import load_takyon_env
from plugins.takyon.runtime_app import resolve_database_url
from plugins.takyon import worker

slug = sys.argv[1]
load_takyon_env()
worker_id = f"local-dev-bootstrap-{os.getpid()}"
database_url = resolve_database_url(None)


def _job_record() -> dict[str, object] | None:
    with psycopg.connect(
        database_url,
        autocommit=True,
        prepare_threshold=None,
        row_factory=dict_row,
    ) as conn:
        return conn.execute(
            "select id, status, error, result, created_at "
            "from jobs "
            "where business_slug = %s and kind = 'ceo_bootstrap' "
            "order by created_at desc limit 1",
            (slug,),
        ).fetchone()


record = _job_record()
if record is None:
    print(json.dumps({"success": True, "slug": slug, "status": "missing"}))
    raise SystemExit(0)

if str(record["status"] or "") == "queued":
    # Local create tests should run the bootstrap for THIS slug immediately instead of accidentally
    # draining some older queued ceo_bootstrap from another local-dev experiment first.
    with psycopg.connect(
        database_url,
        autocommit=True,
        prepare_threshold=None,
        row_factory=tuple_row,
    ) as conn:
        conn.execute(
            "update jobs "
            "set created_at = now() - interval '365 days', updated_at = now() "
            "where id = %s and status = 'queued'",
            (record["id"],),
        )

drained = 0
for _ in range(40):
    record = _job_record()
    status = str((record or {}).get("status") or "")
    if status in {"completed", "blocked", "failed", "cancelled"}:
        break
    drained += int(
        worker.run_worker_loop(
            worker_id=worker_id,
            kinds=["ceo_bootstrap"],
            dispatch=False,
            once=True,
            max_jobs=1,
            database_url=database_url,
        )
        or 0
    )

record = _job_record()
status = str((record or {}).get("status") or "queued")
result = {
    "success": status == "completed",
    "slug": slug,
    "job_id": str((record or {}).get("id") or ""),
    "status": status,
    "error": ((record or {}).get("error") if record else None),
    "drained_jobs": drained,
}
print(json.dumps(result))
if result["status"] not in {"completed", "missing"}:
    raise SystemExit(1)
PY
  )
}

safebox_healthcheck() {
  curl --silent --fail --max-time 2 "$SAFEBOX_URL/healthz" >/dev/null 2>&1
}

start_safebox() {
  ensure_layout
  require_python
  validate_local_supabase_auth

  if safebox_healthcheck; then
    seed_local_platform_owner
    return 0
  fi

  prepare_local_database

  (
    cd "$ROOT/hermes-agent-main"
    env \
      TAKYON_HOME="$SAFEBOX_HOME" \
      TAKYON_HOST_ROLE="safebox" \
      TAKYON_SAFEBOX_TOKEN="$SAFEBOX_TOKEN" \
      "$PYTHON_BIN" -m uvicorn plugins.takyon.safebox_app:app \
        --host 127.0.0.1 \
        --port "$SAFEBOX_PORT"
  ) >"$SAFEBOX_LOG" 2>&1 &

  local attempt
  for attempt in $(seq 1 20); do
    if safebox_healthcheck; then
      seed_local_platform_owner
      return 0
    fi
    sleep 0.25
  done

  echo "Local Safebox did not become ready at $SAFEBOX_URL." >&2
  echo "See log: $SAFEBOX_LOG" >&2
  exit 1
}

takyon_env() {
  ensure_layout
  start_safebox

  env \
    TAKYON_HOME="$OPERATOR_HOME" \
    TAKYON_SAFEBOX_URL="$SAFEBOX_URL" \
    TAKYON_SAFEBOX_TOKEN="$SAFEBOX_TOKEN" \
    TAKYON_STORAGE_BACKEND="$LOCAL_STORAGE_BACKEND" \
    TAKYON_ALLOW_POSTGRES_OUTSIDE_VPS=1 \
    "$TAKYON_ENTRY" "$@"
}

run_takyon() {
  ensure_layout
  start_safebox

  exec env \
    TAKYON_HOME="$OPERATOR_HOME" \
    TAKYON_SAFEBOX_URL="$SAFEBOX_URL" \
    TAKYON_SAFEBOX_TOKEN="$SAFEBOX_TOKEN" \
    TAKYON_STORAGE_BACKEND="$LOCAL_STORAGE_BACKEND" \
    TAKYON_ALLOW_POSTGRES_OUTSIDE_VPS=1 \
    "$TAKYON_ENTRY" "$@"
}

detect_subcommand() {
  local token
  for token in "$@"; do
    case "$token" in
      --*)
        continue
        ;;
      *)
        printf '%s\n' "$token"
        return 0
        ;;
    esac
  done
  return 1
}

extract_create_slug() {
  local seen_create=0
  local token
  for token in "$@"; do
    if [ "$seen_create" -eq 0 ]; then
      case "$token" in
        --*)
          continue
          ;;
        create)
          seen_create=1
          continue
          ;;
        *)
          return 1
          ;;
      esac
    else
      case "$token" in
        --*)
          continue
          ;;
        *)
          printf '%s\n' "$token"
          return 0
          ;;
      esac
    fi
  done
  return 1
}

show_status() {
  ensure_layout
  cat <<EOF
Local dev root:   $LOCAL_DEV_ROOT
Operator home:    $OPERATOR_HOME
Safebox home:     $SAFEBOX_HOME
Safebox URL:      $SAFEBOX_URL
Safebox log:      $SAFEBOX_LOG
Config source:    $SOURCE_CONFIG
Secrets file:     $SAFEBOX_HOME/.env
Supabase auth:    $(if "$SUPABASE_AUTH_HELPER" validate-file "$SAFEBOX_HOME/.env" >/dev/null 2>&1; then echo configured; else echo missing; fi)
EOF
}

usage() {
  cat <<'EOF'
Usage:
  scripts/takyon-local-dev.sh init
  scripts/takyon-local-dev.sh safebox
  scripts/takyon-local-dev.sh shell
  scripts/takyon-local-dev.sh status
  scripts/takyon-local-dev.sh <takyon args...>

This launcher keeps the local Takyon dev rail outside the repo at
~/.takyon-fourmanifold-local-dev/ by default, starts a local Safebox authority,
and then runs the normal ./takyon entrypoint against that local-only state.
EOF
}

command="${1:-shell}"
case "$command" in
  init)
    ensure_layout
    show_status
    ;;
  safebox)
    start_safebox
    show_status
    ;;
  shell)
    shift || true
    run_takyon shell "$@"
    ;;
  status)
    show_status
    ;;
  help|-h|--help)
    usage
    ;;
  *)
    subcommand="$(detect_subcommand "$@" || true)"
    if [ "$subcommand" = "create" ]; then
      slug="$(extract_create_slug "$@" || true)"
      if takyon_env "$@"; then
        if [ -n "${slug:-}" ]; then
          echo "Local dev bootstrap: draining queued ceo_bootstrap for $slug." >&2
          run_local_bootstrap_job "$slug"
          if [ "${SKIP_CREATE_FOLLOWUP}" != "1" ]; then
            echo "Local dev follow-up: continuing $slug into takyon-product-workflow." >&2
            takyon_env --max-turns "$CREATE_FOLLOWUP_MAX_TURNS" run "$slug" "$CREATE_FOLLOWUP_PROMPT"
          fi
        fi
      else
        exit $?
      fi
    else
      run_takyon "$@"
    fi
    ;;
esac
