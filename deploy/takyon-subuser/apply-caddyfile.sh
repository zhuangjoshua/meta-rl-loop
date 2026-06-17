#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
caddyfile="${script_dir}/Caddyfile"
ensure_ratelimit="${script_dir}/../shared/ensure-caddy-ratelimit.sh"

vps_host="${TAKYON_VPS_HOST:-root@134.209.123.8}"
vps_key="${TAKYON_VPS_KEY:-${HOME}/.ssh/takyon_argon_alpha14}"
remote_caddyfile="${TAKYON_REMOTE_CADDYFILE:-/etc/caddy/Caddyfile}"
remote_tmp="/tmp/takyon-subuser-caddyfile.$$"

ssh_opts=(-i "${vps_key}" -o BatchMode=yes -o StrictHostKeyChecking=accept-new)

if [[ ! -f "${caddyfile}" ]]; then
  echo "missing tracked Caddyfile: ${caddyfile}" >&2
  exit 1
fi

if [[ ! -f "${ensure_ratelimit}" ]]; then
  echo "missing caddy rate-limit ensure helper: ${ensure_ratelimit}" >&2
  exit 1
fi

# The tracked Caddyfile uses the `rate_limit` directive (edge DDoS controls), which stock apt caddy
# lacks. Ensure the module is in the binary BEFORE `caddy validate` runs below, so a Caddyfile-only
# deploy (the workflow path triggered when only the Caddyfile changed) self-provisions it idempotently.
ssh "${ssh_opts[@]}" "${vps_host}" "bash -s" < "${ensure_ratelimit}"

scp "${ssh_opts[@]}" "${caddyfile}" "${vps_host}:${remote_tmp}"
ssh "${ssh_opts[@]}" "${vps_host}" "set -euo pipefail
  caddy validate --config '${remote_tmp}' --adapter caddyfile
  if [ -f '${remote_caddyfile}' ]; then
    cp '${remote_caddyfile}' '${remote_caddyfile}.bak-'\"\$(date +%Y%m%d%H%M%S)\"
  fi
  install -o root -g root -m 0644 '${remote_tmp}' '${remote_caddyfile}'
  caddy fmt --overwrite '${remote_caddyfile}'
  caddy validate --config '${remote_caddyfile}' --adapter caddyfile
  systemctl reload caddy
  systemctl is-active caddy
  rm -f '${remote_tmp}'
"
