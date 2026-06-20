#!/usr/bin/env bash
set -euo pipefail

TARGET_HOST="${TARGET_HOST:-${TAKYON_VPS_HOST:-}}"
TARGET_KEY="${TARGET_KEY:-${TAKYON_VPS_KEY:-}}"

TAKYON_REMOTE_CLOUDFLARE_ORIGIN_CERT="${TAKYON_REMOTE_CLOUDFLARE_ORIGIN_CERT:-/etc/ssl/certs/fourmanifold-cloudflare-origin.crt}"
TAKYON_REMOTE_CLOUDFLARE_ORIGIN_KEY="${TAKYON_REMOTE_CLOUDFLARE_ORIGIN_KEY:-/etc/ssl/private/fourmanifold-cloudflare-origin.key}"
TAKYON_CLOUDFLARE_ORIGIN_CERT_PATH="${TAKYON_CLOUDFLARE_ORIGIN_CERT_PATH:-}"
TAKYON_CLOUDFLARE_ORIGIN_KEY_PATH="${TAKYON_CLOUDFLARE_ORIGIN_KEY_PATH:-}"
TAKYON_CLOUDFLARE_ORIGIN_CERT_PEM="${TAKYON_CLOUDFLARE_ORIGIN_CERT_PEM:-}"
TAKYON_CLOUDFLARE_ORIGIN_KEY_PEM="${TAKYON_CLOUDFLARE_ORIGIN_KEY_PEM:-}"
TAKYON_REQUIRE_CLOUDFLARE_ORIGIN_CERT="${TAKYON_REQUIRE_CLOUDFLARE_ORIGIN_CERT:-1}"

usage() {
  cat <<'EOF'
Usage:
  TARGET_HOST=root@137.184.75.57 TARGET_KEY=~/.ssh/takyon_argon_alpha14 \
    TAKYON_CLOUDFLARE_ORIGIN_CERT_PATH=/path/to/origin.crt \
    TAKYON_CLOUDFLARE_ORIGIN_KEY_PATH=/path/to/origin.key \
    deploy/shared/ensure-cloudflare-origin-cert.sh

Contract:
  - If local cert/key material is provided, install or rotate the remote Cloudflare
    origin cert/key idempotently.
  - If no local material is provided, the script accepts an already-valid remote
    install and exits cleanly.
  - If neither local material nor a valid remote install exists, the script fails
    closed by default (TAKYON_REQUIRE_CLOUDFLARE_ORIGIN_CERT=1).
EOF
}

if [[ -z "$TARGET_HOST" || -z "$TARGET_KEY" ]]; then
  usage >&2
  echo "TARGET_HOST and TARGET_KEY are required" >&2
  exit 1
fi

if [[ ! -f "$TARGET_KEY" ]]; then
  echo "target key not found: $TARGET_KEY" >&2
  exit 1
fi

if [[ -n "$TAKYON_CLOUDFLARE_ORIGIN_CERT_PATH" && ! -f "$TAKYON_CLOUDFLARE_ORIGIN_CERT_PATH" ]]; then
  echo "Cloudflare origin cert file not found: $TAKYON_CLOUDFLARE_ORIGIN_CERT_PATH" >&2
  exit 1
fi

if [[ -n "$TAKYON_CLOUDFLARE_ORIGIN_KEY_PATH" && ! -f "$TAKYON_CLOUDFLARE_ORIGIN_KEY_PATH" ]]; then
  echo "Cloudflare origin key file not found: $TAKYON_CLOUDFLARE_ORIGIN_KEY_PATH" >&2
  exit 1
fi

if [[ -n "$TAKYON_CLOUDFLARE_ORIGIN_CERT_PATH" && -z "$TAKYON_CLOUDFLARE_ORIGIN_KEY_PATH" ]]; then
  echo "Set TAKYON_CLOUDFLARE_ORIGIN_KEY_PATH with TAKYON_CLOUDFLARE_ORIGIN_CERT_PATH" >&2
  exit 1
fi

if [[ -n "$TAKYON_CLOUDFLARE_ORIGIN_KEY_PATH" && -z "$TAKYON_CLOUDFLARE_ORIGIN_CERT_PATH" ]]; then
  echo "Set TAKYON_CLOUDFLARE_ORIGIN_CERT_PATH with TAKYON_CLOUDFLARE_ORIGIN_KEY_PATH" >&2
  exit 1
fi

if [[ -n "$TAKYON_CLOUDFLARE_ORIGIN_CERT_PEM" && -z "$TAKYON_CLOUDFLARE_ORIGIN_KEY_PEM" ]]; then
  echo "Set TAKYON_CLOUDFLARE_ORIGIN_KEY_PEM with TAKYON_CLOUDFLARE_ORIGIN_CERT_PEM" >&2
  exit 1
fi

if [[ -n "$TAKYON_CLOUDFLARE_ORIGIN_KEY_PEM" && -z "$TAKYON_CLOUDFLARE_ORIGIN_CERT_PEM" ]]; then
  echo "Set TAKYON_CLOUDFLARE_ORIGIN_CERT_PEM with TAKYON_CLOUDFLARE_ORIGIN_KEY_PEM" >&2
  exit 1
fi

ssh_opts=(-i "$TARGET_KEY" -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new)

remote_cert_is_valid() {
  ssh "${ssh_opts[@]}" "$TARGET_HOST" \
    "env REMOTE_CERT='$TAKYON_REMOTE_CLOUDFLARE_ORIGIN_CERT' REMOTE_KEY='$TAKYON_REMOTE_CLOUDFLARE_ORIGIN_KEY' bash -s" <<'EOF'
set -euo pipefail

test -s "$REMOTE_CERT"
test -s "$REMOTE_KEY"
openssl x509 -in "$REMOTE_CERT" -noout >/dev/null
cert_md5="$(openssl x509 -noout -modulus -in "$REMOTE_CERT" | openssl md5)"
key_md5="$(openssl rsa -noout -modulus -in "$REMOTE_KEY" | openssl md5)"
test "$cert_md5" = "$key_md5"
EOF
}

have_local_paths=0
have_local_pems=0
if [[ -n "$TAKYON_CLOUDFLARE_ORIGIN_CERT_PATH" || -n "$TAKYON_CLOUDFLARE_ORIGIN_KEY_PATH" ]]; then
  have_local_paths=1
fi
if [[ -n "$TAKYON_CLOUDFLARE_ORIGIN_CERT_PEM" || -n "$TAKYON_CLOUDFLARE_ORIGIN_KEY_PEM" ]]; then
  have_local_pems=1
fi

if [[ "$have_local_paths" == "0" && "$have_local_pems" == "0" ]]; then
  if remote_cert_is_valid; then
    echo "Cloudflare origin cert already present on $TARGET_HOST"
    exit 0
  fi
  if [[ "$TAKYON_REQUIRE_CLOUDFLARE_ORIGIN_CERT" == "1" ]]; then
    cat >&2 <<EOF
Cloudflare origin cert/key are missing or invalid on $TARGET_HOST.
Provide either:
  - TAKYON_CLOUDFLARE_ORIGIN_CERT_PATH + TAKYON_CLOUDFLARE_ORIGIN_KEY_PATH
  - TAKYON_CLOUDFLARE_ORIGIN_CERT_PEM + TAKYON_CLOUDFLARE_ORIGIN_KEY_PEM
EOF
    exit 1
  fi
  echo "Cloudflare origin cert missing on $TARGET_HOST, but not required; skipping"
  exit 0
fi

tmp_dir="$(mktemp -d)"
cleanup() {
  rm -rf "$tmp_dir"
}
trap cleanup EXIT

local_cert="$tmp_dir/fourmanifold-cloudflare-origin.crt"
local_key="$tmp_dir/fourmanifold-cloudflare-origin.key"

if [[ "$have_local_paths" == "1" ]]; then
  cp "$TAKYON_CLOUDFLARE_ORIGIN_CERT_PATH" "$local_cert"
  cp "$TAKYON_CLOUDFLARE_ORIGIN_KEY_PATH" "$local_key"
else
  printf '%s\n' "$TAKYON_CLOUDFLARE_ORIGIN_CERT_PEM" > "$local_cert"
  printf '%s\n' "$TAKYON_CLOUDFLARE_ORIGIN_KEY_PEM" > "$local_key"
fi

chmod 0644 "$local_cert"
chmod 0600 "$local_key"

openssl x509 -in "$local_cert" -noout >/dev/null
local_cert_md5="$(openssl x509 -noout -modulus -in "$local_cert" | openssl md5)"
local_key_md5="$(openssl rsa -noout -modulus -in "$local_key" | openssl md5)"
if [[ "$local_cert_md5" != "$local_key_md5" ]]; then
  echo "Local Cloudflare origin cert and key do not match" >&2
  exit 1
fi

remote_tmp_cert="/tmp/takyon-cloudflare-origin.crt.$$"
remote_tmp_key="/tmp/takyon-cloudflare-origin.key.$$"

scp "${ssh_opts[@]}" "$local_cert" "$TARGET_HOST:$remote_tmp_cert"
scp "${ssh_opts[@]}" "$local_key" "$TARGET_HOST:$remote_tmp_key"

ssh "${ssh_opts[@]}" "$TARGET_HOST" \
  "env REMOTE_CERT='$TAKYON_REMOTE_CLOUDFLARE_ORIGIN_CERT' REMOTE_KEY='$TAKYON_REMOTE_CLOUDFLARE_ORIGIN_KEY' TMP_CERT='$remote_tmp_cert' TMP_KEY='$remote_tmp_key' bash -s" <<'EOF'
set -euo pipefail

# Caddy runs as an unprivileged user (`caddy`). It must be able to TRAVERSE the key directory and
# READ the key, or every `caddy reload`/restart fails with "permission denied: <key>" while the old
# in-memory config silently keeps serving — i.e. config changes stop taking effect with no obvious
# error (observed in prod: a root:root 0600 key wedged Caddy reloads for ~2 days). Grant the minimum:
# the `caddy` group can traverse the key dir (0710, no listing) and read THIS key (0640). Any other
# private key in that dir keeps its own (typically root:root 0600) perms, so it stays caddy-unreadable.
key_group=root
key_dir_mode=0700
key_file_mode=0600
if getent group caddy >/dev/null 2>&1; then
  key_group=caddy
  key_dir_mode=0710
  key_file_mode=0640
fi
install -d -m 0755 "$(dirname "$REMOTE_CERT")"
install -d -m "$key_dir_mode" -o root -g "$key_group" "$(dirname "$REMOTE_KEY")"
if [ -f "$REMOTE_CERT" ]; then
  cp "$REMOTE_CERT" "$REMOTE_CERT.bak-$(date +%Y%m%d%H%M%S)"
fi
if [ -f "$REMOTE_KEY" ]; then
  cp "$REMOTE_KEY" "$REMOTE_KEY.bak-$(date +%Y%m%d%H%M%S)"
fi
install -o root -g root -m 0644 "$TMP_CERT" "$REMOTE_CERT"
install -o root -g "$key_group" -m "$key_file_mode" "$TMP_KEY" "$REMOTE_KEY"
rm -f "$TMP_CERT" "$TMP_KEY"
openssl x509 -in "$REMOTE_CERT" -noout >/dev/null
cert_md5="$(openssl x509 -noout -modulus -in "$REMOTE_CERT" | openssl md5)"
key_md5="$(openssl rsa -noout -modulus -in "$REMOTE_KEY" | openssl md5)"
test "$cert_md5" = "$key_md5"
EOF

echo "Cloudflare origin cert installed on $TARGET_HOST"
