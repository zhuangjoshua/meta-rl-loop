#!/usr/bin/env bash
set -euo pipefail

WEB_DIR="${1:?usage: build-web-locked.sh WEB_DIR}"
if [[ ! -f "$WEB_DIR/package-lock.json" ]]; then
  echo "web package lock not found: $WEB_DIR/package-lock.json" >&2
  exit 1
fi

# Operator, Safebox, and sub-user deploys share one checkout. Concurrent npm ci runs delete and
# recreate the same node_modules tree, leaving partially empty packages and nondeterministic builds.
lock_key="$(printf '%s' "$WEB_DIR" | cksum | awk '{print $1}')"
lock_dir="${TMPDIR:-/tmp}/takyon-web-build-${lock_key}.lock"
missing_owner_observations=0

cleanup() {
  rm -f "$lock_dir/pid"
  rmdir "$lock_dir" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

while ! mkdir "$lock_dir" 2>/dev/null; do
  owner_pid="$(cat "$lock_dir/pid" 2>/dev/null || true)"
  if [[ "$owner_pid" =~ ^[0-9]+$ ]] && kill -0 "$owner_pid" 2>/dev/null; then
    missing_owner_observations=0
    sleep 1
    continue
  fi
  if [[ -z "$owner_pid" ]] && (( missing_owner_observations < 5 )); then
    missing_owner_observations=$((missing_owner_observations + 1))
    sleep 1
    continue
  fi
  rm -f "$lock_dir/pid"
  rmdir "$lock_dir" 2>/dev/null || true
  missing_owner_observations=0
done

printf '%s\n' "$$" > "$lock_dir/pid"
(
  cd "$WEB_DIR"
  npm ci
  npm run build
)
