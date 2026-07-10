#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
HELPER="$ROOT_DIR/deploy/argon-alpha-14/provision-operator-access-db.py"
TAKYON_VPS_HOST="${TAKYON_VPS_HOST:-root@137.184.75.57}"
TAKYON_VPS_KEY="${TAKYON_VPS_KEY:-$HOME/.ssh/takyon_argon_alpha14}"
TAKYON_REMOTE_RUNTIME="${TAKYON_REMOTE_RUNTIME:-/opt/takyon/hermes-agent-main}"

[[ -f "$HELPER" ]] || { echo "operator-access provisioner not found" >&2; exit 1; }
[[ -f "$TAKYON_VPS_KEY" ]] || { echo "operator deploy key not found" >&2; exit 1; }

# The helper source contains no credential. It runs from stdin on the operator as root, generates
# the password there, and writes it only beneath /root. A clean environment prevents caller exports
# from changing the production/host-role pin or smuggling a different DSN into the helper.
ssh -i "$TAKYON_VPS_KEY" -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new \
  "$TAKYON_VPS_HOST" \
  "cd '$TAKYON_REMOTE_RUNTIME' && exec env -i \
    PATH=/usr/sbin:/usr/bin:/sbin:/bin \
    HOME=/root \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH='$TAKYON_REMOTE_RUNTIME' \
    TAKYON_ENV=prod \
    TAKYON_HOST_ROLE=operator \
    '$TAKYON_REMOTE_RUNTIME/.venv/bin/python' -" \
  < "$HELPER"
