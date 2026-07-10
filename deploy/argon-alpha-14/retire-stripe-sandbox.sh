#!/usr/bin/env bash
set -euo pipefail

TAKYON_VPS_HOST="${TAKYON_VPS_HOST:-root@137.184.75.57}"
TAKYON_VPS_KEY="${TAKYON_VPS_KEY:-$HOME/.ssh/takyon_argon_alpha14}"
TAKYON_REMOTE_RUNTIME="${TAKYON_REMOTE_RUNTIME:-/opt/takyon/hermes-agent-main}"
TAKYON_SAFEBOX_VPS_HOST="${TAKYON_SAFEBOX_VPS_HOST:-root@67.205.158.170}"
TAKYON_SUBUSER_VPS_HOSTS="${TAKYON_SUBUSER_VPS_HOSTS:-root@134.209.123.8 root@206.81.10.173}"

[[ -f "$TAKYON_VPS_KEY" ]] || { echo "deploy key not found: $TAKYON_VPS_KEY" >&2; exit 1; }

if ps ax -o command= | grep -Eq '[t]akyon-cli worker .*--worker-id mac-operator-'; then
  echo 'local Mac production worker must be drained and stopped before Stripe retirement' >&2
  exit 1
fi

# Stop every public producer and the old test-mode authority before the one-shot DB transition.
# They stay stopped; the coordinated live deploy restarts them only after live secrets/mode exist.
ssh -i "$TAKYON_VPS_KEY" -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new \
  "$TAKYON_SAFEBOX_VPS_HOST" 'bash -s' <<'SAFEBOX'
set -euo pipefail
paused=0
account=0
for file in /opt/takyon/.takyon/.env /opt/takyon/secrets/.env; do
  [ -f "$file" ] || continue
  grep -Eq '^TAKYON_STRIPE_CHECKOUT_DISABLED=(1|true|TRUE|yes|YES|on|ON)$' "$file" && paused=1
  grep -Fxq 'TAKYON_STRIPE_ACCOUNT_ID=acct_1TXWsW7tYL4lkVC6' "$file" && account=1
done
[ "$paused" = 1 ] || { echo 'persisted Stripe checkout pause missing on Safebox' >&2; exit 1; }
[ "$account" = 1 ] || { echo 'live Stripe account binding missing on Safebox' >&2; exit 1; }

# Prove the running service resolves a live managed key for the exact target account. The token and
# key never enter shell output; the endpoint returns only account id + livemode.
/opt/takyon/venvs/safebox-current/bin/python - <<'PY'
import json
import subprocess
import urllib.request

pid = int(subprocess.check_output(
    ["systemctl", "show", "-p", "MainPID", "--value", "takyon-safebox.service"],
    text=True,
).strip() or "0")
if pid <= 1:
    raise SystemExit("Safebox process is not running for live Stripe proof")
values = {}
for item in open(f"/proc/{pid}/environ", "rb").read().split(b"\0"):
    if b"=" in item:
        key, value = item.split(b"=", 1)
        values[key.decode("utf-8", "strict")] = value.decode("utf-8", "strict")
token = values.get("TAKYON_SAFEBOX_TOKEN", "")
if not token:
    raise SystemExit("running Safebox transport token unavailable")
request = urllib.request.Request(
    "http://10.116.0.2:8000/v1/stripe/account-proof",
    headers={"Authorization": f"Bearer {token}"},
)
with urllib.request.urlopen(request, timeout=15) as response:
    proof = json.loads(response.read().decode("utf-8"))
if proof != {"account_id": "acct_1TXWsW7tYL4lkVC6", "livemode": True}:
    raise SystemExit("running Safebox live Stripe account proof failed")
print("running Safebox live Stripe account proof verified")
PY

systemctl stop takyon-safebox.service
! systemctl is-active --quiet takyon-safebox.service
SAFEBOX
for host in $TAKYON_SUBUSER_VPS_HOSTS; do
  ssh -i "$TAKYON_VPS_KEY" -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new \
    "$host" \
    "set -euo pipefail; systemctl stop takyon-subuser.service; ! systemctl is-active --quiet takyon-subuser.service"
done

ssh -i "$TAKYON_VPS_KEY" -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new \
  "$TAKYON_VPS_HOST" \
  'exec env -i PATH=/usr/sbin:/usr/bin:/sbin:/bin HOME=/root SSH_CONNECTION="$SSH_CONNECTION" TAKYON_REMOTE_RUNTIME=/opt/takyon/hermes-agent-main bash -s' <<'REMOTE'
set -euo pipefail
for unit in takyon-dashboard.service takyon-worker.service; do
  if systemctl is-active --quiet "$unit"; then
    echo "$unit must be drained and stopped before Stripe sandbox retirement" >&2
    exit 1
  fi
done
if curl -fsS --max-time 2 http://10.116.0.2:8000/healthz >/dev/null 2>&1; then
  echo 'Safebox must remain stopped during Stripe sandbox retirement' >&2
  exit 1
fi

migration_dir=/root/.config/takyon/migration
migration_file="$migration_dir/database-url"
[[ "$(stat -c '%u:%g:%a' "$migration_dir")" == '0:0:700' ]] \
  || { echo 'root-only migration credential directory permissions invalid' >&2; exit 1; }
[[ -f "$migration_file" && ! -L "$migration_file" ]] \
  || { echo 'root-only migration credential missing' >&2; exit 1; }
[[ "$(stat -c '%u:%g:%a' "$migration_file")" == '0:0:600' ]] \
  || { echo 'root-only migration credential permissions invalid' >&2; exit 1; }
IFS= read -r migration_dsn <"$migration_file"
[[ "$migration_dsn" == postgres://* || "$migration_dsn" == postgresql://* ]] \
  || { echo 'root-only migration credential malformed' >&2; exit 1; }

export PYTHONPATH="$TAKYON_REMOTE_RUNTIME"
export TAKYON_MIGRATION_DATABASE_URL="$migration_dsn"
unset migration_dsn
export TAKYON_ENV=prod
export TAKYON_HOST_ROLE=operator
export TAKYON_STRIPE_MODE=test
export TAKYON_STRIPE_CHECKOUT_DISABLED=1
export TAKYON_STRIPE_ACCOUNT_ID=acct_1TXWsW7tYL4lkVC6
exec "$TAKYON_REMOTE_RUNTIME/.venv/bin/python" \
  "$TAKYON_REMOTE_RUNTIME/scripts/stripe_live_retire_sandbox.py" \
  --source-account acct_1TXWsc9n69Zj6BuE \
  --target-account acct_1TXWsW7tYL4lkVC6
REMOTE
