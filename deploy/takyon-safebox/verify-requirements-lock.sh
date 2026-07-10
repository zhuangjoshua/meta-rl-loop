#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
RUNTIME_DIR="$ROOT_DIR/hermes-agent-main"
LOCK_FILE="$RUNTIME_DIR/packaging/safebox-requirements.lock"

command -v uv >/dev/null 2>&1 || {
  echo "uv is required to verify the Safebox dependency lock" >&2
  exit 1
}
[[ -f "$LOCK_FILE" ]] || {
  echo "Safebox dependency lock not found: $LOCK_FILE" >&2
  exit 1
}

tmp_lock="$(mktemp)"
trap 'rm -f "$tmp_lock"' EXIT
cp "$LOCK_FILE" "$tmp_lock"

(
  cd "$RUNTIME_DIR"
  uv pip compile packaging/safebox-requirements.in \
    --python-version 3.12 \
    --generate-hashes \
    --no-emit-package takyon-agent \
    --no-header \
    --quiet \
    --output-file "$tmp_lock"
)

if ! cmp -s <(tail -n +3 "$LOCK_FILE") "$tmp_lock"; then
  echo "Safebox dependency lock is stale; regenerate packaging/safebox-requirements.lock" >&2
  diff -u <(tail -n +3 "$LOCK_FILE") "$tmp_lock" >&2 || true
  exit 1
fi

echo "Safebox dependency lock is current"
