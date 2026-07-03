#!/usr/bin/env bash
# Bootstrap one takyon-env-dev droplet (Stage 4b dev split) the way PROD hosts deploy:
# rsync the hermes-agent-main tree (same excludes as the workspace deploy recipe), build the
# venv with uv against uv.lock (same rail as setup-takyon.sh), write the DEV-alias-only env
# file, install the role's systemd unit rendered from the tracked template, start + verify.
#
# Usage:
#   deploy/takyon-dev-split/bootstrap-dev-droplet.sh <public-ip> subuser <node-name> <safebox-vpc-ip>
#   deploy/takyon-dev-split/bootstrap-dev-droplet.sh <public-ip> safebox <node-name> <bind-vpc-ip>
#
# REDEPLOYING A LIVE SPLIT (graceful drain — the full-4b zero-loss rail): this script ends with a
# HARD `systemctl restart`, which black-holes LB traffic for the health-check detection window
# (~4.5s) on a serving replica. On a live split, either (a) rsync/stage new code with this script
# ONLY while the replica is drained, or (b) preferred: sync code first, then activate it with
#   takyon env restart dev
# (`EnvironmentProvisioner.rolling_restart`): per replica it removes the node from the LB, waits
# out in-flight requests, converges this Caddy front from the tracked template, restarts
# takyon-subuser.service, health-verifies locally, re-adds to the LB, and PROVES the LB routes to
# the node again (X-Takyon-Node) before touching the next replica. Fail-closed: it refuses to
# start a drain unless every other replica is a healthy LB member. Zero requests lost on planned
# restarts/deploys.
#
# Env:
#   TAKYON_DEV_STORE  path to the dev store env file (default:
#                     <workspace>/.takyon-dev-safebox/.env). Secrets are COPIED to the target
#                     env file over scp with mode 600 — never echoed, never committed.
#   TAKYON_DEV_KEY    ssh private key (default: ~/.ssh/takyon_dev_split)
#
# Secrets policy: the subuser replica receives ONLY the dev runtime/app-plane DSN + the safebox
# transport token (same posture as prod — provider keys stay behind the dev safebox). The dev
# safebox host receives the dev store minus the DO/infra-only aliases, plus its transport token
# and cap signing key.
#
# PER-REPLICA CREDENTIALS (Stage 4b hardening): this script seeds the SHARED dev DSN/token so a
# fresh box can boot. `takyon env create <env>` then enrolls the replica with its OWN scoped DB
# login (takyon_app_runtime__<node>) and its OWN safebox transport token, replacing the shared
# values in the box's env file (activate with `takyon env restart <env>`). Re-running this script
# on an enrolled replica reverts it to the shared credentials until the next `takyon env create`
# converges it — always re-run create after a re-bootstrap.
set -euo pipefail

HOST="${1:?public ip}"
ROLE="${2:?subuser|safebox}"
NODE_NAME="${3:?node name}"
VPC_IP="${4:?safebox vpc ip (subuser) / bind vpc ip (safebox)}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
TREE="$ROOT_DIR/hermes-agent-main"
STORE="${TAKYON_DEV_STORE:-$(cd "$ROOT_DIR/.." && pwd)/takyon/.takyon-dev-safebox/.env}"
KEY="${TAKYON_DEV_KEY:-$HOME/.ssh/takyon_dev_split}"
SSH=(ssh -i "$KEY" -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new "root@$HOST")

[[ -f "$STORE" ]] || { echo "dev store not found: $STORE" >&2; exit 1; }
[[ -f "$KEY" ]] || { echo "dev ssh key not found: $KEY" >&2; exit 1; }
case "$ROLE" in subuser|safebox) ;; *) echo "role must be subuser|safebox" >&2; exit 1;; esac

store_get() { grep -m1 "^$1=" "$STORE" | cut -d= -f2- || true; }

echo "→ [$NODE_NAME] preparing host"
"${SSH[@]}" "set -euo pipefail
  export DEBIAN_FRONTEND=noninteractive
  command -v rsync >/dev/null || (apt-get update -qq && apt-get install -y -qq rsync curl ca-certificates)
  install -d /opt/takyon /opt/takyon/.takyon /opt/takyon/.takyon/businesses /opt/takyon/.takyon/product-sites
"

echo "→ [$NODE_NAME] rsync runtime tree"
COPYFILE_DISABLE=1 rsync -rt --no-perms --no-owner --no-group --checksum --delete \
  --exclude='.git' --exclude='.venv' --exclude='venv' --exclude='node_modules' \
  --exclude='__pycache__' --exclude='*.pyc' --exclude='._*' --exclude='.DS_Store' \
  --exclude='.env' --exclude='secrets' --exclude='logs' --exclude='tmp' \
  -e "ssh -i $KEY -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new" \
  "$TREE/" "root@$HOST:/opt/takyon/hermes-agent-main/"

# The dashboard web dist is a BUILT artifact shipped inside the prod tree (the unit starts with
# --skip-build). A fresh worktree does not contain it; source it from TAKYON_WEB_DIST (default:
# the tree's own takyon_cli/web_dist when present).
WEB_DIST="${TAKYON_WEB_DIST:-$TREE/takyon_cli/web_dist}"
if [[ "$ROLE" == "subuser" ]]; then
  if [[ -d "$WEB_DIST" ]]; then
    echo "→ [$NODE_NAME] rsync built web dist ($WEB_DIST)"
    COPYFILE_DISABLE=1 rsync -rt --no-perms --no-owner --no-group --checksum --delete \
      --exclude='._*' --exclude='.DS_Store' \
      -e "ssh -i $KEY -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new" \
      "$WEB_DIST/" "root@$HOST:/opt/takyon/hermes-agent-main/takyon_cli/web_dist/"
  else
    echo "⚠ no built web dist at $WEB_DIST — the unit's --skip-build start will refuse" >&2
  fi
fi

echo "→ [$NODE_NAME] venv (uv sync --locked, hash-verified — same rail as setup-takyon.sh)"
"${SSH[@]}" "set -euo pipefail
  cd /opt/takyon/hermes-agent-main
  find . -name '._*' -delete
  if ! command -v /root/.local/bin/uv >/dev/null && ! command -v uv >/dev/null; then
    curl -LsSf https://astral.sh/uv/install.sh | sh >/dev/null
  fi
  UV=\$(command -v uv || echo /root/.local/bin/uv)
  export UV_NO_CONFIG=1 UV_PROJECT_ENVIRONMENT=/opt/takyon/hermes-agent-main/.venv
  # Same multi-tier rail as setup-takyon.sh: hash-verified lockfile sync first, then the
  # curated-extra pip fallback when the lock is stale vs pyproject (uv prints why).
  if ! \$UV sync --locked --extra all --extra postgres --no-progress; then
    echo '⚠ lockfile sync failed — falling back to uv pip install (transitives re-resolved)'
    [ -d .venv ] || \$UV venv .venv
    \$UV pip install -e '.[all]' --no-progress
    \$UV pip install -e '.[postgres]' --no-progress
  fi
  .venv/bin/python -m compileall -q plugins/takyon takyon_cli tui_gateway || true
"

echo "→ [$NODE_NAME] env file (dev aliases only; scp mode 600, never echoed)"
TMPENV="$(mktemp)"
trap 'rm -f "$TMPENV"' EXIT
if [[ "$ROLE" == "subuser" ]]; then
  {
    printf 'TAKYON_DEV_RUNTIME_DATABASE_URL=%s\n' "$(store_get TAKYON_DEV_RUNTIME_DATABASE_URL)"
    printf 'TAKYON_SAFEBOX_TOKEN=%s\n' "$(store_get TAKYON_DEV_SAFEBOX_TOKEN)"
  } > "$TMPENV"
else
  # Dev safebox = the dev store minus infra-only aliases (DO token, ssh cidr, mac-local safebox
  # url), plus its own transport token + cap signing key.
  grep -vE '^(TAKYON_DO_API_TOKEN|TAKYON_DEV_SSH_ALLOW_CIDR|TAKYON_DEV_SAFEBOX_URL|TAKYON_DEV_SAFEBOX_TOKEN|TAKYON_DEV_CAP_SIGNING_KEY)=' "$STORE" > "$TMPENV"
  printf 'TAKYON_SAFEBOX_TOKEN=%s\n' "$(store_get TAKYON_DEV_SAFEBOX_TOKEN)" >> "$TMPENV"
  printf 'TAKYON_CAP_SIGNING_KEY=%s\n' "$(store_get TAKYON_DEV_CAP_SIGNING_KEY)" >> "$TMPENV"
fi
scp -q -i "$KEY" -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new \
  "$TMPENV" "root@$HOST:/opt/takyon/.takyon/.env"
rm -f "$TMPENV"; trap - EXIT

if [[ "$ROLE" == "subuser" ]]; then
  echo "→ [$NODE_NAME] caddy front (:80 → loopback uvicorn, prod topology; node-identity header)"
  TMPCADDY="$(mktemp)"
  sed -e "s/__NODE_NAME__/$NODE_NAME/g" "$SCRIPT_DIR/Caddyfile.dev" > "$TMPCADDY"
  scp -q -i "$KEY" -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new \
    "$TMPCADDY" "root@$HOST:/root/Caddyfile.staged"
  rm -f "$TMPCADDY"
  "${SSH[@]}" "set -euo pipefail
    export DEBIAN_FRONTEND=noninteractive
    command -v caddy >/dev/null || (apt-get update -qq && apt-get install -y -qq caddy)
    install -d /etc/caddy
    mv /root/Caddyfile.staged /etc/caddy/Caddyfile
    caddy validate --config /etc/caddy/Caddyfile >/dev/null
    systemctl enable caddy >/dev/null
    systemctl restart caddy
    systemctl is-active --quiet caddy
  "
fi

echo "→ [$NODE_NAME] unit + user + start"
UNIT_TMPL="$SCRIPT_DIR/takyon-$( [[ "$ROLE" == subuser ]] && echo subuser || echo safebox )-dev.service.tmpl"
SERVICE_NAME="$( [[ "$ROLE" == subuser ]] && echo takyon-subuser || echo takyon-safebox ).service"
TMPUNIT="$(mktemp)"
sed -e "s/__NODE_NAME__/$NODE_NAME/g" -e "s/__SAFEBOX_VPC_IP__/$VPC_IP/g" -e "s/__BIND_IP__/$VPC_IP/g" \
  "$UNIT_TMPL" > "$TMPUNIT"
scp -q -i "$KEY" -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new \
  "$TMPUNIT" "root@$HOST:/etc/systemd/system/$SERVICE_NAME"
rm -f "$TMPUNIT"

HEALTH_URL="$( [[ "$ROLE" == subuser ]] && echo http://127.0.0.1:9119/healthz || echo "http://$VPC_IP:8000/healthz" )"
"${SSH[@]}" "set -euo pipefail
  if ! id -u takyon >/dev/null 2>&1; then
    useradd --system --user-group --home-dir /opt/takyon --shell /usr/sbin/nologin takyon
  fi
  chown takyon:takyon /opt/takyon
  chown -R takyon:takyon /opt/takyon/.takyon
  chmod 600 /opt/takyon/.takyon/.env
  systemctl daemon-reload
  systemctl enable '$SERVICE_NAME' >/dev/null
  systemctl restart '$SERVICE_NAME'
  for _ in \$(seq 1 60); do curl -fsS '$HEALTH_URL' >/dev/null 2>&1 && break; sleep 2; done
  curl -fsS '$HEALTH_URL' >/dev/null
  systemctl is-active --quiet '$SERVICE_NAME'
  echo \"$NODE_NAME: \$(systemctl is-active '$SERVICE_NAME') + healthz OK\"
"
