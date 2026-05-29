#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
caddyfile="${script_dir}/Caddyfile"

vps_host="${TAKYON_VPS_HOST:-root@137.184.75.57}"
vps_key="${TAKYON_VPS_KEY:-${HOME}/.ssh/takyon_argon_alpha14}"
remote_caddyfile="${TAKYON_REMOTE_CADDYFILE:-/etc/caddy/Caddyfile}"
remote_tmp="/tmp/takyon-caddyfile.$$"

ssh_opts=(-i "${vps_key}" -o BatchMode=yes)

if [[ ! -f "${caddyfile}" ]]; then
  echo "missing tracked Caddyfile: ${caddyfile}" >&2
  exit 1
fi

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
