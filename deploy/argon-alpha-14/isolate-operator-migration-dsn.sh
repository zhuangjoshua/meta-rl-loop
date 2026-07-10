#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
HELPER="$ROOT_DIR/deploy/argon-alpha-14/isolate-operator-migration-dsn.py"
TAKYON_VPS_HOST="${TAKYON_VPS_HOST:-root@137.184.75.57}"
TAKYON_VPS_KEY="${TAKYON_VPS_KEY:-$HOME/.ssh/takyon_argon_alpha14}"

[[ -f "$HELPER" ]] || { echo "operator migration isolator not found" >&2; exit 1; }
[[ -f "$TAKYON_VPS_KEY" ]] || { echo "operator deploy key not found" >&2; exit 1; }

ssh -i "$TAKYON_VPS_KEY" -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new \
  "$TAKYON_VPS_HOST" \
  "exec env -i PATH=/usr/sbin:/usr/bin:/sbin:/bin HOME=/root /usr/bin/python3 -" \
  < "$HELPER"
