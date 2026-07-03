#!/usr/bin/env bash
set -euo pipefail

# Dev-twin operator rail — the DEV mirror of scripts/takyon-operator-prod.sh.
#
# Runs the operator shell/console on this Mac against the DEV TWIN (four-manifold-dev Supabase,
# its OWN database — never prod), with the SAME named profiles as prod so `sai`/`josh` mean the
# same person in both. It brings up the dev Safebox locally, points the runtime at TAKYON_ENV=dev
# (which resolves the TAKYON_DEV_* DSN twins + fails closed on any prod literal), and scopes the
# session to the resolved operator user-id (owner_user_id isolation, identical to prod).
#
#   scripts/takyon-operator-dev.sh sai              # dev shell as sai
#   scripts/takyon-operator-dev.sh josh             # dev shell as josh
#   scripts/takyon-operator-dev.sh shell --user sai # explicit
#   scripts/takyon-operator-dev.sh run sai <business> "<prompt>"
#   scripts/takyon-operator-dev.sh seed sai josh    # one-time: create these users in the dev twin

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUNTIME_DIR="$ROOT/hermes-agent-main"
TAKYON_ENTRY="$ROOT/takyon"
PYTHON_BIN="$RUNTIME_DIR/.venv/bin/python"
DEV_STORE="${TAKYON_DEV_STORE:-$ROOT/.takyon-dev-safebox}"
DEV_STORE_ENV="$DEV_STORE/.env"
OPERATOR_HOME="${TAKYON_DEV_OPERATOR_HOME:-$HOME/.takyon-fourmanifold-dev-operator}"
SOURCE_CONFIG="$ROOT/.takyon/config.yaml"

# shellcheck source=scripts/operator-users.sh
source "$ROOT/scripts/operator-users.sh"

die() { echo "error: $*" >&2; exit 1; }

# Read one KEY=value from the dev store .env (values may be quoted). No secrets are echoed.
dev_env() {
  local key="$1"
  [[ -f "$DEV_STORE_ENV" ]] || die "dev store not found at $DEV_STORE_ENV (run 'takyon env create dev' first)"
  sed -n "s/^${key}=//p" "$DEV_STORE_ENV" | head -1 | sed -e 's/^"//' -e 's/"$//'
}

ensure_operator_home() {
  mkdir -p "$OPERATOR_HOME"
  if [[ -f "$SOURCE_CONFIG" && ! -f "$OPERATOR_HOME/config.yaml" ]]; then
    cp "$SOURCE_CONFIG" "$OPERATOR_HOME/config.yaml"
  fi
}

# Bring up the dev Safebox (its own process, dev store) if its URL is not already answering.
ensure_dev_safebox() {
  local url token host port
  url="$(dev_env TAKYON_DEV_SAFEBOX_URL)"; token="$(dev_env TAKYON_DEV_SAFEBOX_TOKEN)"
  [[ -n "$url" ]] || die "TAKYON_DEV_SAFEBOX_URL missing from dev store"
  if curl -fsS -m 4 "$url/healthz" >/dev/null 2>&1; then return 0; fi
  host="$(printf '%s' "$url" | sed -E 's#^https?://([^:/]+).*#\1#')"
  port="$(printf '%s' "$url" | sed -E 's#^https?://[^:]+:([0-9]+).*#\1#')"; port="${port:-8378}"
  echo "Starting dev Safebox on $host:$port ..." >&2
  ( cd "$RUNTIME_DIR" && env \
      TAKYON_HOST_ROLE=safebox TAKYON_HOME="$DEV_STORE" TAKYON_SAFEBOX_TOKEN="$token" \
      TAKYON_ALLOW_POSTGRES_OUTSIDE_VPS=1 \
      "$RUNTIME_DIR/.venv/bin/uvicorn" --app-dir "$RUNTIME_DIR" \
      "plugins.takyon.safebox_app:build_safebox_app" --factory --host "$host" --port "$port" \
      >"$OPERATOR_HOME/dev-safebox.log" 2>&1 & )
  for _ in $(seq 1 30); do
    curl -fsS -m 2 "$url/healthz" >/dev/null 2>&1 && { echo "dev Safebox up." >&2; return 0; }
    sleep 1
  done
  die "dev Safebox did not come up; see $OPERATOR_HOME/dev-safebox.log"
}

# Common env for any runtime invocation against the dev twin.
dev_run() {
  local user_id="$1"; shift
  ensure_operator_home
  ensure_dev_safebox
  exec env \
    TAKYON_ENV=dev \
    TAKYON_HOME="$OPERATOR_HOME" \
    TAKYON_HOST_ROLE=operator \
    TAKYON_SESSION_USER_ID="$user_id" \
    TAKYON_DEV_OPERATOR_DATABASE_URL="$(dev_env TAKYON_DEV_OPERATOR_DATABASE_URL)" \
    TAKYON_DEV_RUNTIME_DATABASE_URL="$(dev_env TAKYON_DEV_RUNTIME_DATABASE_URL)" \
    TAKYON_DEV_SAFEBOX_DATABASE_URL="$(dev_env TAKYON_DEV_SAFEBOX_DATABASE_URL)" \
    TAKYON_DEV_MIGRATION_DATABASE_URL="$(dev_env TAKYON_DEV_MIGRATION_DATABASE_URL)" \
    TAKYON_SAFEBOX_URL="$(dev_env TAKYON_DEV_SAFEBOX_URL)" \
    TAKYON_SAFEBOX_TOKEN="$(dev_env TAKYON_DEV_SAFEBOX_TOKEN)" \
    TAKYON_STORAGE_BACKEND=local \
    TAKYON_ALLOW_POSTGRES_OUTSIDE_VPS=1 \
    "$TAKYON_ENTRY" "$@"
}

# One-time: create the named users in the dev twin (their own DB) so operating-as-them is real.
cmd_seed() {
  [[ $# -gt 0 ]] || die "usage: seed <name> [<name>...]"
  local dsn; dsn="$(dev_env TAKYON_DEV_MIGRATION_DATABASE_URL)"
  local uids=() n
  for n in "$@"; do uids+=("$n=$(resolve_operator_alias "$n")"); done
  env TAKYON_ALLOW_POSTGRES_OUTSIDE_VPS=1 DEV_MIGRATION_DSN="$dsn" DEV_SEED_USERS="${uids[*]}" \
    "$PYTHON_BIN" - <<'PY'
import os, psycopg
dsn = os.environ["DEV_MIGRATION_DSN"]
pairs = [p.split("=", 1) for p in os.environ["DEV_SEED_USERS"].split()]
with psycopg.connect(dsn, autocommit=True) as c, c.cursor() as cur:
    for name, uid in pairs:
        cur.execute(
            "insert into users (id, auth0_sub) values (%s, %s) on conflict (id) do nothing",
            (uid, f"dev-operator|{name}"),
        )
        cur.execute("select 1 from users where id = %s", (uid,))
        print(f"  dev user '{name}' ({uid[:8]}...): {'present' if cur.fetchone() else 'FAILED'}")
print("dev users seeded.")
PY
}

command="${1:-shell}"
case "$command" in
  seed)     shift || true; cmd_seed "$@" ;;
  shell|quiet)
    shift || true
    user=""; rest=()
    while [[ $# -gt 0 ]]; do
      case "$1" in
        --user) shift; user="$(resolve_operator_alias "${1:-}")" ;;
        --user=*) user="$(resolve_operator_alias "${1#*=}")" ;;
        *) rest+=("$1") ;;
      esac
      shift || true
    done
    [[ -n "$user" ]] || user="$(resolve_operator_alias josh)"
    dev_run "$user" shell "${rest[@]:-}"
    ;;
  run)
    shift || true
    name="${1:-}"; shift || true
    dev_run "$(resolve_operator_alias "$name")" run "$@"
    ;;
  -h|--help|help)
    sed -n '3,20p' "$0" | sed 's/^# \{0,1\}//'
    ;;
  *)
    # Named operator profile → dev shell as that user.
    if is_operator_alias "$command"; then
      user_id="$(resolve_operator_alias "$command")"; shift || true
      dev_run "$user_id" shell "$@"
    else
      die "unknown command '$command' (try: sai | josh | shell --user <name> | seed <name> | run <name> ...)"
    fi
    ;;
esac
