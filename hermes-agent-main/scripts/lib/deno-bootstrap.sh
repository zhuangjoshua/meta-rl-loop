#!/usr/bin/env bash
# ============================================================================
# scripts/lib/deno-bootstrap.sh
# ----------------------------------------------------------------------------
# Sourceable helper: ensure Deno >= MIN_VERSION is available for the product
# actions rail (product/site/actions/*.ts run in a sandboxed `deno run`).
#
# This mirrors scripts/lib/node-bootstrap.sh so a missing Deno self-heals into
# $TAKYON_HOME/deno/ with zero shell-rc edits — the whole point is that no
# teammate has to `brew install deno` by hand for their Mac to build+publish a
# product. Deno ships as a single self-contained binary (no runtime deps), so
# the bundled-binary fallback is a plain download+unzip.
#
# Strategy (first hit wins — respects the user's existing tooling):
#   1. modern `deno` already on PATH
#   2. $TAKYON_HOME/deno/bin/deno from a prior Takyon-managed install
#   3. ~/.deno/bin/deno ($DENO_INSTALL) from the official installer
#   4. macOS Homebrew
#   5. pinned github.com/denoland/deno release zip into $TAKYON_HOME/deno/bin/
#      (always works, zero shell rc edits)
#
# Usage:
#   source scripts/lib/deno-bootstrap.sh
#   ensure_deno   # returns 0 on success, non-zero on failure
#   if [ "$TAKYON_DENO_AVAILABLE" = true ]; then ...; fi
#
# Env inputs (set before sourcing to override defaults):
#   TAKYON_DENO_MIN_VERSION      (default: 2)       — major accepted on PATH
#   TAKYON_DENO_TARGET_VERSION   (default: 2.8.3)   — version installed when we install
#   TAKYON_HOME                  (default: $HOME/.takyon)
# ============================================================================

TAKYON_DENO_MIN_VERSION="${TAKYON_DENO_MIN_VERSION:-2}"
TAKYON_DENO_TARGET_VERSION="${TAKYON_DENO_TARGET_VERSION:-2.8.3}"
TAKYON_HOME="${TAKYON_HOME:-$HOME/.takyon}"
TAKYON_DENO_AVAILABLE=false

# ---------------------------------------------------------------------------
# Logging — prefer the host script's log_* helpers when present
# ---------------------------------------------------------------------------

_db_log()  { declare -F log_info    >/dev/null 2>&1 && log_info    "$*" || printf '→ %s\n' "$*" >&2; }
_db_ok()   { declare -F log_success >/dev/null 2>&1 && log_success "$*" || printf '✓ %s\n' "$*" >&2; }
_db_warn() { declare -F log_warn    >/dev/null 2>&1 && log_warn    "$*" || printf '⚠ %s\n' "$*" >&2; }

# ---------------------------------------------------------------------------
# Version helpers
# ---------------------------------------------------------------------------

_db_deno_major() {
    local v
    # `deno --version` → "deno 2.8.3 (stable, ...)"
    v=$(deno --version 2>/dev/null | head -1 | sed -E 's/^deno[[:space:]]+v?([0-9]+)\..*/\1/')
    [[ "$v" =~ ^[0-9]+$ ]] && echo "$v" || echo 0
}

_db_have_modern_deno() {
    command -v deno >/dev/null 2>&1 || return 1
    [ "$(_db_deno_major)" -ge "$TAKYON_DENO_MIN_VERSION" ]
}

# ---------------------------------------------------------------------------
# Existing-install paths — respect what the user already has
# ---------------------------------------------------------------------------

_db_try_deno_install_dir() {
    # The official installer drops the binary at $DENO_INSTALL/bin/deno (default ~/.deno).
    local dir="${DENO_INSTALL:-$HOME/.deno}"
    [ -x "$dir/bin/deno" ] || return 1
    export PATH="$dir/bin:$PATH"
    _db_have_modern_deno || return 1
    _db_ok "Deno $(deno --version | head -1) found ($dir)"
    return 0
}

_db_try_brew() {
    [ "$(uname -s)" = "Darwin" ] || return 1
    command -v brew >/dev/null 2>&1 || return 1
    _db_log "Installing Deno via Homebrew..."
    brew install deno >/dev/null 2>&1 || return 1
    _db_have_modern_deno || return 1
    _db_ok "Deno $(deno --version | head -1) installed via Homebrew"
    return 0
}

# ---------------------------------------------------------------------------
# Bundled binary fallback — always works, no shell rc edits
# ---------------------------------------------------------------------------

_db_install_bundled_deno() {
    command -v unzip >/dev/null 2>&1 || {
        _db_warn "unzip is required to install the bundled Deno binary — install unzip or Deno manually: https://deno.land/"
        return 1
    }

    local arch os_name target
    arch=$(uname -m)
    os_name=$(uname -s)
    case "$os_name-$arch" in
        Darwin-arm64|Darwin-aarch64) target="aarch64-apple-darwin"      ;;
        Darwin-x86_64)               target="x86_64-apple-darwin"       ;;
        Linux-x86_64)                target="x86_64-unknown-linux-gnu"  ;;
        Linux-aarch64|Linux-arm64)   target="aarch64-unknown-linux-gnu" ;;
        *)
            _db_warn "Unsupported platform ($os_name-$arch) — install Deno manually: https://deno.land/"
            return 1
            ;;
    esac

    local ver="$TAKYON_DENO_TARGET_VERSION"
    local url="https://github.com/denoland/deno/releases/download/v${ver}/deno-${target}.zip"
    local tmp
    tmp=$(mktemp -d)
    _db_log "Downloading Deno ${ver} (${target})..."
    if ! curl -fsSL "$url" -o "$tmp/deno.zip"; then
        _db_warn "Download failed: $url"
        rm -rf "$tmp"
        return 1
    fi

    if ! unzip -oq "$tmp/deno.zip" -d "$tmp"; then
        _db_warn "unzip failed for $tmp/deno.zip"
        rm -rf "$tmp"
        return 1
    fi
    if [ ! -f "$tmp/deno" ]; then
        _db_warn "Deno archive did not contain a deno binary"
        rm -rf "$tmp"
        return 1
    fi

    mkdir -p "$TAKYON_HOME/deno/bin"
    mv "$tmp/deno" "$TAKYON_HOME/deno/bin/deno"
    chmod +x "$TAKYON_HOME/deno/bin/deno"
    rm -rf "$tmp"

    mkdir -p "$HOME/.local/bin"
    ln -sf "$TAKYON_HOME/deno/bin/deno" "$HOME/.local/bin/deno"
    export PATH="$TAKYON_HOME/deno/bin:$PATH"

    _db_have_modern_deno || return 1
    _db_ok "Deno $(deno --version | head -1) installed to $TAKYON_HOME/deno/"
    return 0
}

# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

ensure_deno() {
    TAKYON_DENO_AVAILABLE=false

    if _db_have_modern_deno; then
        _db_ok "Deno $(deno --version | head -1) found"
        TAKYON_DENO_AVAILABLE=true
        return 0
    fi

    if [ -x "$TAKYON_HOME/deno/bin/deno" ]; then
        export PATH="$TAKYON_HOME/deno/bin:$PATH"
        if _db_have_modern_deno; then
            _db_ok "Deno $(deno --version | head -1) found (Takyon-managed)"
            TAKYON_DENO_AVAILABLE=true
            return 0
        fi
    fi

    # Existing installs first — respect the user's setup.
    _db_try_deno_install_dir && { TAKYON_DENO_AVAILABLE=true; return 0; }
    _db_try_brew             && { TAKYON_DENO_AVAILABLE=true; return 0; }

    # Last resort: pinned github.com/denoland/deno release binary.
    _db_install_bundled_deno && { TAKYON_DENO_AVAILABLE=true; return 0; }

    _db_warn "Deno install failed — the product actions rail will be unavailable on this host."
    _db_warn "Install manually: https://deno.land/  (or: \`brew install deno\`, \`curl -fsSL https://deno.land/install.sh | sh\`)"
    return 1
}
