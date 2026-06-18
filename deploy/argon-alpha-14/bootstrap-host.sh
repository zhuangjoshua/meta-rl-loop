#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SERVICE_FILE="$ROOT_DIR/deploy/argon-alpha-14/takyon-dashboard.service"
WORKER_SERVICE_FILE="$ROOT_DIR/deploy/argon-alpha-14/takyon-worker.service"
DOCKER_BROKER_SERVICE_FILE="$ROOT_DIR/deploy/argon-alpha-14/takyon-docker-broker.service"
ENSURE_DENO_SCRIPT="$ROOT_DIR/deploy/shared/ensure-deno.sh"
ENSURE_CADDY_RATELIMIT_SCRIPT="$ROOT_DIR/deploy/shared/ensure-caddy-ratelimit.sh"

TARGET_HOST="${TAKYON_VPS_HOST:-root@137.184.75.57}"
TARGET_KEY="${TAKYON_VPS_KEY:-$HOME/.ssh/takyon_argon_alpha14}"
REMOTE_ROOT="${TAKYON_REMOTE_ROOT:-/opt/takyon}"
REMOTE_HOME="${TAKYON_REMOTE_HOME:-$REMOTE_ROOT/.takyon}"
REMOTE_SECRETS="${TAKYON_REMOTE_SECRETS:-$REMOTE_ROOT/secrets}"
REMOTE_RUNTIME="${TAKYON_REMOTE_RUNTIME:-$REMOTE_ROOT/hermes-agent-main}"
REMOTE_DOCKER_IMAGE="${TAKYON_CLAUDE_AGENT_DOCKER_IMAGE:-${TERMINAL_DOCKER_IMAGE:-nikolaik/python-nodejs:python3.11-nodejs20}}"
TAKYON_DENO_VERSION="${TAKYON_DENO_VERSION:-2.8.3}"

target_ssh=(-i "$TARGET_KEY" -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new)

if [[ ! -f "$SERVICE_FILE" ]]; then
  echo "service file not found: $SERVICE_FILE" >&2
  exit 1
fi

if [[ ! -f "$WORKER_SERVICE_FILE" ]]; then
  echo "worker service file not found: $WORKER_SERVICE_FILE" >&2
  exit 1
fi

if [[ ! -f "$DOCKER_BROKER_SERVICE_FILE" ]]; then
  echo "docker broker service file not found: $DOCKER_BROKER_SERVICE_FILE" >&2
  exit 1
fi

if [[ ! -f "$TARGET_KEY" ]]; then
  echo "target key not found: $TARGET_KEY" >&2
  exit 1
fi

if [[ ! -f "$ENSURE_DENO_SCRIPT" ]]; then
  echo "deno bootstrap helper not found: $ENSURE_DENO_SCRIPT" >&2
  exit 1
fi

if [[ ! -f "$ENSURE_CADDY_RATELIMIT_SCRIPT" ]]; then
  echo "caddy rate-limit bootstrap helper not found: $ENSURE_CADDY_RATELIMIT_SCRIPT" >&2
  exit 1
fi

ssh "${target_ssh[@]}" "$TARGET_HOST" \
  "env TAKYON_DENO_VERSION='$TAKYON_DENO_VERSION' TAKYON_REQUIRE_SYSTEMD_RUN=1 bash -s" \
  < "$ENSURE_DENO_SCRIPT"

ssh "${target_ssh[@]}" "$TARGET_HOST" "set -euo pipefail
  export DEBIAN_FRONTEND=noninteractive
  apt-get update
  apt-get install -y ca-certificates curl rsync caddy docker.io ffmpeg
  if ! command -v xurl >/dev/null 2>&1; then
    curl -fsSL https://raw.githubusercontent.com/xdevplatform/xurl/main/install.sh | bash
  fi
  if ! command -v xurl >/dev/null 2>&1 && [ -x /root/.local/bin/xurl ]; then
    # A real copy, not a symlink: the services run with ProtectHome=true, so a link into
    # /root/.local would be unreachable for them.
    install -m 0755 /root/.local/bin/xurl /usr/local/bin/xurl
  fi
  command -v xurl >/dev/null 2>&1 || [ -x /root/.local/bin/xurl ]
  install -d '$REMOTE_ROOT' '$REMOTE_HOME' '$REMOTE_HOME/businesses' '$REMOTE_SECRETS'
  # The tracked units run as the dedicated non-root 'takyon' user. Docker authority lives in the
  # dedicated broker unit only, so the user itself must not remain in the docker group. The
  # runtime tree stays root-owned (read-only to the services via the units' ReadOnlyPaths).
  if ! id -u takyon >/dev/null 2>&1; then
    useradd --system --user-group --home-dir '$REMOTE_ROOT' --shell /usr/sbin/nologin takyon
  fi
  takyon_uid=\"\$(id -u takyon)\"
  if id -nG takyon | grep -qw docker; then
    gpasswd -d takyon docker >/dev/null 2>&1 || deluser takyon docker >/dev/null 2>&1 || true
  fi
  loginctl enable-linger takyon
  install -d /etc/systemd/system/user@.service.d
  cat >/etc/systemd/system/user@.service.d/delegate.conf <<'EOF'
[Service]
Delegate=cpu cpuset io memory pids
EOF
  systemctl daemon-reload
  systemctl restart \"user@\${takyon_uid}.service\"
  runuser -u takyon -- env \
    XDG_RUNTIME_DIR=\"/run/user/\${takyon_uid}\" \
    DBUS_SESSION_BUS_ADDRESS=\"unix:path=/run/user/\${takyon_uid}/bus\" \
    systemd-run --user --scope --quiet \
      -p CPUQuota=20% \
      -p MemoryMax=64M \
      -p TasksMax=8 \
      -- /bin/true
  chown takyon:takyon '$REMOTE_ROOT'
  chown -R takyon:takyon '$REMOTE_HOME' '$REMOTE_SECRETS'
  systemctl enable docker >/dev/null
  systemctl start docker
  systemctl is-active --quiet docker
  docker version >/dev/null
  if ! docker image inspect '$REMOTE_DOCKER_IMAGE' >/dev/null 2>&1; then
    docker pull '$REMOTE_DOCKER_IMAGE'
  fi
  install -d '$REMOTE_RUNTIME'
"

# Provision the rate_limit module into the Caddy binary so the tracked Caddyfile
# (edge DDoS controls) validates and reloads. Idempotent; runs on every host.
ssh "${target_ssh[@]}" "$TARGET_HOST" "bash -s" < "$ENSURE_CADDY_RATELIMIT_SCRIPT"

echo "Operator host bootstrap complete: $TARGET_HOST"
