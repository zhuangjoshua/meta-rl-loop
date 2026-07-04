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
ROLE="${2:?subuser|safebox|operator}"
NODE_NAME="${3:?node name}"
VPC_IP="${4:?safebox vpc ip (subuser/operator) / bind vpc ip (safebox)}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
TREE="$ROOT_DIR/hermes-agent-main"
STORE="${TAKYON_DEV_STORE:-$(cd "$ROOT_DIR/.." && pwd)/takyon/.takyon-dev-safebox/.env}"
KEY="${TAKYON_DEV_KEY:-$HOME/.ssh/takyon_dev_split}"
SSH=(ssh -i "$KEY" -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new "root@$HOST")

[[ -f "$STORE" ]] || { echo "dev store not found: $STORE" >&2; exit 1; }
[[ -f "$KEY" ]] || { echo "dev ssh key not found: $KEY" >&2; exit 1; }
case "$ROLE" in subuser|safebox|operator) ;; *) echo "role must be subuser|safebox|operator" >&2; exit 1;; esac
# For the operator role VPC_IP is the DEV SAFEBOX private VPC IP (the dashboard/worker resolve
# provider secrets from http://$VPC_IP:8000). The claude-agent build image is overridable.
DOCKER_IMAGE="${TAKYON_CLAUDE_AGENT_DOCKER_IMAGE:-${TERMINAL_DOCKER_IMAGE:-nikolaik/python-nodejs:python3.11-nodejs20}}"

store_get() { grep -m1 "^$1=" "$STORE" | cut -d= -f2- || true; }

echo "→ [$NODE_NAME] preparing host"
"${SSH[@]}" "set -euo pipefail
  export DEBIAN_FRONTEND=noninteractive
  command -v rsync >/dev/null || (apt-get update -qq && apt-get install -y -qq rsync curl ca-certificates)
  install -d /opt/takyon /opt/takyon/.takyon /opt/takyon/.takyon/businesses /opt/takyon/.takyon/product-sites
"

if [[ "$ROLE" == "operator" ]]; then
  echo "→ [$NODE_NAME] operator host prep (docker + user/linger + agent image) — mirrors prod bootstrap-host.sh"
  "${SSH[@]}" "set -euo pipefail
    export DEBIAN_FRONTEND=noninteractive
    apt-get update -qq
    apt-get install -y -qq docker.io ffmpeg
    # deno for the product-action sandbox, installed where the ProtectHome=true units can reach it.
    if ! command -v deno >/dev/null 2>&1 && [ ! -x /usr/local/bin/deno ]; then
      curl -fsSL https://deno.land/install.sh | DENO_INSTALL=/usr/local sh -s -- -y >/dev/null 2>&1 || true
    fi
    # Dedicated non-root takyon user. Docker authority lives ONLY in the broker unit (which has
    # SupplementaryGroups=docker); the dashboard/worker reach docker via that broker.
    if ! id -u takyon >/dev/null 2>&1; then
      useradd --system --user-group --home-dir /opt/takyon --shell /usr/sbin/nologin takyon
    fi
    getent group docker >/dev/null || groupadd docker
    id -nG takyon | grep -qw docker || usermod -aG docker takyon
    takyon_uid=\$(id -u takyon)
    # user-manager + cgroup delegation for the product-action systemd-run --user --scope carve-out.
    loginctl enable-linger takyon
    install -d /etc/systemd/system/user@.service.d
    printf '[Service]\nDelegate=cpu cpuset io memory pids\n' > /etc/systemd/system/user@.service.d/delegate.conf
    systemctl daemon-reload
    systemctl restart \"user@\${takyon_uid}.service\" || true
    systemctl enable docker >/dev/null
    systemctl start docker
    systemctl is-active --quiet docker
    docker version >/dev/null
    docker image inspect '$DOCKER_IMAGE' >/dev/null 2>&1 || docker pull '$DOCKER_IMAGE'
    echo \"operator host prep OK: takyon uid=\$takyon_uid docker=\$(systemctl is-active docker)\"
  "
fi

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
if [[ "$ROLE" == "subuser" || "$ROLE" == "operator" ]]; then
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

if [[ "$ROLE" == "operator" ]]; then
  # The operator plane RUNS the CEO, so it needs the provider-specific extras that are NOT in
  # .[all] (pyproject keeps anthropic/gemini as separate opt extras). Without these the worker's
  # ceo_bootstrap fails with "the 'anthropic' package is required" / missing genai. Installed as
  # root with root's uv (the venv was built by root; runuser-takyon cannot reach /root/.local/bin/uv).
  echo "→ [$NODE_NAME] operator provider extras (anthropic model + google-genai creative)"
  "${SSH[@]}" "set -euo pipefail
    cd /opt/takyon/hermes-agent-main
    UV=\$(command -v uv || echo /root/.local/bin/uv)
    export UV_NO_CONFIG=1 UV_PROJECT_ENVIRONMENT=/opt/takyon/hermes-agent-main/.venv
    \$UV pip install -e '.[anthropic]' google-genai --no-progress
  "
fi

echo "→ [$NODE_NAME] env file (dev aliases only; scp mode 600, never echoed)"
TMPENV="$(mktemp)"
trap 'rm -f "$TMPENV"' EXIT
if [[ "$ROLE" == "subuser" ]]; then
  {
    printf 'TAKYON_DEV_RUNTIME_DATABASE_URL=%s\n' "$(store_get TAKYON_DEV_RUNTIME_DATABASE_URL)"
    printf 'TAKYON_SAFEBOX_TOKEN=%s\n' "$(store_get TAKYON_DEV_SAFEBOX_TOKEN)"
  } > "$TMPENV"
elif [[ "$ROLE" == "operator" ]]; then
  # Operator plane: its OWN control-plane DSN + the migration DSN (for `takyon migrate` run on this
  # host) + the safebox transport token + the operator AUTHORITY token. Provider/model keys stay
  # behind the dev safebox (resolved via TAKYON_SAFEBOX_URL, never resident here). The operator
  # token is forbidden on subuser hosts but REQUIRED here — this is an operator-trust host.
  {
    printf 'TAKYON_DEV_OPERATOR_DATABASE_URL=%s\n' "$(store_get TAKYON_DEV_OPERATOR_DATABASE_URL)"
    printf 'TAKYON_DEV_MIGRATION_DATABASE_URL=%s\n' "$(store_get TAKYON_DEV_MIGRATION_DATABASE_URL)"
    printf 'TAKYON_SAFEBOX_TOKEN=%s\n' "$(store_get TAKYON_DEV_SAFEBOX_TOKEN)"
    printf 'TAKYON_SAFEBOX_OPERATOR_TOKEN=%s\n' "$(store_get TAKYON_SAFEBOX_OPERATOR_TOKEN)"
  } > "$TMPENV"
else
  # Dev safebox = the dev store minus infra-only aliases (DO token, ssh cidr, mac-local safebox
  # url), plus its own transport token + cap signing key.
  grep -vE '^(TAKYON_DO_API_TOKEN|TAKYON_DEV_SSH_ALLOW_CIDR|TAKYON_DEV_SAFEBOX_URL|TAKYON_DEV_SAFEBOX_TOKEN|TAKYON_DEV_CAP_SIGNING_KEY|TAKYON_SAFEBOX_OPERATOR_CLIENTS|TAKYON_OPERATOR_USAGE_GATE_DISABLED)=' "$STORE" > "$TMPENV"
  printf 'TAKYON_SAFEBOX_TOKEN=%s\n' "$(store_get TAKYON_DEV_SAFEBOX_TOKEN)" >> "$TMPENV"
  printf 'TAKYON_CAP_SIGNING_KEY=%s\n' "$(store_get TAKYON_DEV_CAP_SIGNING_KEY)" >> "$TMPENV"
  # Operator money rail, mirroring prod (the remote reserve/refund/broker calls resolve IN the
  # safebox, so these must live on the safebox host, not the operator): the dev operator droplet's
  # VPC IP is the ONLY allowed operator client (subusers stay disallowed — the local-dev store
  # carried a loopback value that is wrong once the operator is a separate droplet), and the
  # operator usage gate is disabled so the dev operator agent runs $0.
  printf 'TAKYON_SAFEBOX_OPERATOR_CLIENTS=%s\n' "${TAKYON_DEV_OPERATOR_VPC_IP:-10.200.0.2} 127.0.0.1 ::1" >> "$TMPENV"
  printf 'TAKYON_OPERATOR_USAGE_GATE_DISABLED=1\n' >> "$TMPENV"
fi
scp -q -i "$KEY" -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new \
  "$TMPENV" "root@$HOST:/opt/takyon/.takyon/.env"
rm -f "$TMPENV"; trap - EXIT

if [[ "$ROLE" == "operator" ]]; then
  # The CEO runtime config ($TAKYON_HOME/config.yaml — model.provider/default, etc.) is NOT part of
  # the runtime tree; the Mac rail copies it at launch, so the droplet needs it installed here or the
  # worker's ceo_bootstrap fails with "model config missing model.provider, model.default".
  CONFIG_SRC="${TAKYON_DEV_CONFIG_YAML:-$ROOT_DIR/.takyon/config.yaml}"
  if [[ -f "$CONFIG_SRC" ]]; then
    echo "→ [$NODE_NAME] operator config.yaml ($CONFIG_SRC)"
    scp -q -i "$KEY" -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new \
      "$CONFIG_SRC" "root@$HOST:/opt/takyon/.takyon/config.yaml"
    "${SSH[@]}" "chown takyon:takyon /opt/takyon/.takyon/config.yaml"
  else
    echo "⚠ no config.yaml at $CONFIG_SRC — the CEO will fail on missing model config; set TAKYON_DEV_CONFIG_YAML" >&2
  fi
  echo "→ [$NODE_NAME] operator units (docker-broker + worker + dashboard) rendered + started"
  # The units' BindPaths=/run/user/<uid> must use the ACTUAL takyon uid on THIS host (a fresh dev
  # droplet rarely lands on prod's 995), so resolve it and render __TAKYON_UID__.
  TAKYON_UID="$("${SSH[@]}" 'id -u takyon' | tr -d '[:space:]')"
  [[ -n "$TAKYON_UID" ]] || { echo "could not resolve takyon uid on $HOST" >&2; exit 1; }
  for u in docker-broker worker dashboard; do
    SVC="takyon-$u.service"
    TMPL="$SCRIPT_DIR/takyon-$u-dev.service.tmpl"
    [[ -f "$TMPL" ]] || { echo "missing unit template: $TMPL" >&2; exit 1; }
    TMPU="$(mktemp)"
    sed -e "s/__NODE_NAME__/$NODE_NAME/g" -e "s/__SAFEBOX_VPC_IP__/$VPC_IP/g" -e "s/__TAKYON_UID__/$TAKYON_UID/g" "$TMPL" > "$TMPU"
    scp -q -i "$KEY" -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new "$TMPU" "root@$HOST:/etc/systemd/system/$SVC"
    rm -f "$TMPU"
  done
  "${SSH[@]}" "set -euo pipefail
    chown takyon:takyon /opt/takyon
    chown -R takyon:takyon /opt/takyon/.takyon
    chmod 600 /opt/takyon/.takyon/.env
    systemctl daemon-reload
    for SVC in takyon-docker-broker takyon-worker takyon-dashboard; do systemctl enable \$SVC.service >/dev/null; done
    # Order: docker authority first, then the drain worker, then the dashboard front.
    systemctl restart takyon-docker-broker.service; sleep 2
    systemctl restart takyon-worker.service
    systemctl restart takyon-dashboard.service
    for _ in \$(seq 1 60); do curl -fsS http://127.0.0.1:9119/healthz >/dev/null 2>&1 && break; sleep 2; done
    curl -fsS http://127.0.0.1:9119/healthz >/dev/null
    echo \"$NODE_NAME: dashboard=\$(systemctl is-active takyon-dashboard.service) worker=\$(systemctl is-active takyon-worker.service) broker=\$(systemctl is-active takyon-docker-broker.service) + healthz OK\"
  "
  exit 0
fi

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
