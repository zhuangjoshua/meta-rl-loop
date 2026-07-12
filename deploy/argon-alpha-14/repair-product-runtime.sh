#!/usr/bin/env bash
set -euo pipefail

TARGET_HOST="${TAKYON_VPS_HOST:-root@137.184.75.57}"
TARGET_KEY="${TAKYON_VPS_KEY:-$HOME/.ssh/takyon_argon_alpha14}"
REMOTE_HOME="${TAKYON_REMOTE_HOME:-/opt/takyon/.takyon}"
REMOTE_SYSTEMD_DIR="${TAKYON_REMOTE_SYSTEMD_DIR:-/etc/systemd/system}"
STOP_CORE_SERVICES="${TAKYON_STOP_CORE_SERVICES:-0}"

if [[ ! -f "$TARGET_KEY" ]]; then
  echo "target key not found: $TARGET_KEY" >&2
  exit 1
fi

ssh_opts=(-i "$TARGET_KEY" -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new)

ssh "${ssh_opts[@]}" "$TARGET_HOST" 'bash -s' <<EOF
set -euo pipefail

remote_home='$REMOTE_HOME'
systemd_dir='$REMOTE_SYSTEMD_DIR'
stop_core='$STOP_CORE_SERVICES'
service_root="\$remote_home/product-services"
business_root="\$remote_home/businesses"
tmp_root='/tmp/takyon-workspaces'
removed_units=0

if [[ "\$stop_core" == "1" ]]; then
  systemctl stop takyon-worker.service >/dev/null 2>&1 || true
  systemctl stop takyon-dashboard.service >/dev/null 2>&1 || true
  systemctl stop takyon-docker-broker.service >/dev/null 2>&1 || true
fi

if command -v systemctl >/dev/null 2>&1; then
  while IFS= read -r unit; do
    [[ -n "\$unit" ]] || continue
    wd="\$(systemctl show "\$unit" -p WorkingDirectory --value 2>/dev/null || true)"
    case "\$wd" in
      "\$service_root"/*) continue ;;
    esac
    systemctl stop "\$unit" >/dev/null 2>&1 || true
    systemctl disable "\$unit" >/dev/null 2>&1 || true
    rm -f "\$systemd_dir/\$unit"
    removed_units=\$((removed_units + 1))
  done < <(systemctl list-unit-files 'takyon-product-*.service' --no-legend --no-pager 2>/dev/null | awk '{print \$1}')
  systemctl daemon-reload
fi

if [[ -d "\$business_root" ]]; then
  find "\$business_root" -type d \\( -name node_modules -o -name .next \\) -prune -exec rm -rf {} +
fi

if [[ -d "\$tmp_root" ]]; then
  find "\$tmp_root" -mindepth 1 -maxdepth 1 -exec rm -rf {} +
fi

printf 'removed_bad_product_units=%s\n' "\$removed_units"
printf 'pruned_business_build_cache=1\n'
printf 'cleared_tmp_workspaces=1\n'
EOF
