#!/usr/bin/env bash
set -euo pipefail

VERSION="${TAKYON_DENO_VERSION:-2.8.3}"
REQUIRE_SYSTEMD_RUN="${TAKYON_REQUIRE_SYSTEMD_RUN:-0}"

current_version=""
if command -v deno >/dev/null 2>&1; then
  current_version="$(deno --version | awk 'NR==1{print $2}')"
fi

if [[ "$current_version" != "$VERSION" ]]; then
  export DEBIAN_FRONTEND=noninteractive
  apt-get update
  apt-get install -y ca-certificates curl unzip
  curl -fsSL https://deno.land/install.sh | DENO_INSTALL=/usr/local sh -s -- -f "v$VERSION"
fi

command -v deno >/dev/null 2>&1
installed_version="$(deno --version | awk 'NR==1{print $2}')"
if [[ "$installed_version" != "$VERSION" ]]; then
  echo "expected deno $VERSION but found $installed_version" >&2
  exit 1
fi

if [[ "$REQUIRE_SYSTEMD_RUN" == "1" ]]; then
  command -v systemd-run >/dev/null 2>&1
fi
