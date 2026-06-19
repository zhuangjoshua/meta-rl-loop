#!/usr/bin/env bash
# Ensure the host's Caddy binary includes the HTTP rate-limit module
# (github.com/mholt/caddy-ratelimit) AND the tracked minimum Caddy version.
# The tracked Caddyfiles now rely on both the `rate_limit` directive and newer
# global server options such as trusted_proxies, so an older binary can be
# "partly working" yet still reject the real production config. This script
# makes the binary self-consistent with the tracked config: idempotent, run
# over SSH via `bash -s` exactly like ensure-deno.sh, and applied on EVERY host
# (current + future) through each host's bootstrap-host.sh/apply-caddyfile.sh,
# never hand-installed on one box.
set -euo pipefail

RATELIMIT_MODULE="github.com/mholt/caddy-ratelimit"
CADDY_VERSION="${TAKYON_CADDY_VERSION:-2.8.4}"
GO_VERSION="${TAKYON_CADDY_GO_VERSION:-1.23.4}"
GO_TARBALL="go${GO_VERSION}.linux-amd64.tar.gz"

has_required_caddy() {
  command -v caddy >/dev/null 2>&1 \
    && caddy list-modules 2>/dev/null | grep -qx "http.handlers.rate_limit" \
    && caddy version 2>/dev/null | grep -Eq "(^|v)${CADDY_VERSION}([[:space:]]|$)"
}

if has_required_caddy; then
  echo "caddy already has rate_limit and required version ${CADDY_VERSION}; nothing to do"
  exit 0
fi

export DEBIAN_FRONTEND=noninteractive
apt-get update
# Install stock caddy first so the apt repo, the `caddy` group/user, and the
# systemd unit exist; we only swap the BINARY below.
apt-get install -y ca-certificates curl tar caddy

# Provide a Go toolchain for xcaddy without depending on the (often older) apt golang.
if ! command -v go >/dev/null 2>&1 || ! go version 2>/dev/null | grep -q "go${GO_VERSION%.*}"; then
  tmp_go="$(mktemp -d)"
  curl -fsSL "https://go.dev/dl/${GO_TARBALL}" -o "${tmp_go}/${GO_TARBALL}"
  rm -rf /usr/local/go
  tar -C /usr/local -xzf "${tmp_go}/${GO_TARBALL}"
  rm -rf "${tmp_go}"
fi
export PATH="/usr/local/go/bin:${PATH}"
export GOBIN="/usr/local/bin"
go version

# Build Caddy with the rate-limit module and replace the apt binary in place.
go install "github.com/caddyserver/xcaddy/cmd/xcaddy@latest"
build_dir="$(mktemp -d)"
(
  cd "${build_dir}"
  xcaddy build "v${CADDY_VERSION}" --with "${RATELIMIT_MODULE}" --output ./caddy
)
# Stop caddy if running so the busy binary can be replaced, then restore it.
caddy_was_active=0
if systemctl is-active --quiet caddy 2>/dev/null; then
  caddy_was_active=1
  systemctl stop caddy
fi
target_bin="$(command -v caddy || echo /usr/bin/caddy)"
install -m 0755 "${build_dir}/caddy" "${target_bin}"
rm -rf "${build_dir}"
if [[ "${caddy_was_active}" == "1" ]]; then
  systemctl start caddy
fi

# Fail closed: the module AND version MUST be present now, or the tracked Caddyfile
# would not validate consistently across hosts.
if ! has_required_caddy; then
  echo "ERROR: caddy still lacks rate_limit or required version ${CADDY_VERSION} after rebuild" >&2
  exit 1
fi
echo "caddy rebuilt with rate_limit module (${RATELIMIT_MODULE}) and version ${CADDY_VERSION}"
