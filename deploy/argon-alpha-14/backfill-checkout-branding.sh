#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
TAKYON_VPS_HOST="${TAKYON_VPS_HOST:-root@137.184.75.57}"
TAKYON_SSH_KEY="${TAKYON_SSH_KEY:-$HOME/.ssh/takyon_argon_alpha14}"
REMOTE_RUNTIME="${TAKYON_REMOTE_RUNTIME:-/opt/takyon/hermes-agent-main}"
REMOTE_HOME="${TAKYON_REMOTE_HOME:-/opt/takyon/.takyon}"
REMOTE_ENV="${TAKYON_REMOTE_ENV:-prod}"
COMPANY_BASE_DOMAIN="${TAKYON_COMPANY_BASE_DOMAIN:-}"
if [[ -n "$COMPANY_BASE_DOMAIN" && ! "$COMPANY_BASE_DOMAIN" =~ ^[A-Za-z0-9.-]+$ ]]; then
  echo "invalid TAKYON_COMPANY_BASE_DOMAIN" >&2
  exit 2
fi

ssh -i "$TAKYON_SSH_KEY" -o BatchMode=yes "$TAKYON_VPS_HOST" \
  "env TAKYON_HOME=$REMOTE_HOME TAKYON_ENV=$REMOTE_ENV TAKYON_HOST_ROLE=operator \
  PYTHONPATH=$REMOTE_RUNTIME $REMOTE_RUNTIME/.venv/bin/python \
  $REMOTE_RUNTIME/scripts/backfill_checkout_branding.py \
  ${COMPANY_BASE_DOMAIN:+--company-base-domain $COMPANY_BASE_DOMAIN}"
