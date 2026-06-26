#!/usr/bin/env bash
set -euo pipefail

if command -v doppler >/dev/null 2>&1; then
  doppler --version
  exit 0
fi

if ! command -v apt-get >/dev/null 2>&1; then
  echo "Doppler CLI installer currently supports Debian/Ubuntu hosts with apt-get" >&2
  exit 1
fi

export DEBIAN_FRONTEND=noninteractive

apt-get update
apt-get install -y ca-certificates curl gnupg

install -d -m 0755 /usr/share/keyrings /etc/apt/sources.list.d
curl -sLf --retry 3 --tlsv1.2 --proto "=https" \
  "https://packages.doppler.com/public/cli/gpg.DE2A7741A397C129.key" \
  | gpg --dearmor --yes -o /usr/share/keyrings/doppler-archive-keyring.gpg

echo "deb [signed-by=/usr/share/keyrings/doppler-archive-keyring.gpg] https://packages.doppler.com/public/cli/deb/debian any-version main" \
  > /etc/apt/sources.list.d/doppler-cli.list

apt-get update
apt-get install -y doppler

command -v doppler >/dev/null 2>&1
doppler --version
