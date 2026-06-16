#!/usr/bin/env bash
set -euo pipefail

TARGET_HOST="${TARGET_HOST:?TARGET_HOST is required}"
TARGET_KEY="${TARGET_KEY:?TARGET_KEY is required}"
TAKYON_REMOTE_RUNTIME="${TAKYON_REMOTE_RUNTIME:?TAKYON_REMOTE_RUNTIME is required}"
TAKYON_REMOTE_HOME="${TAKYON_REMOTE_HOME:-/opt/takyon/.takyon}"
TAKYON_REMOTE_SAFEBOX_URL="${TAKYON_REMOTE_SAFEBOX_URL:-http://10.116.0.2:8000}"
# The runtime services run as the 'takyon' user with HOME=/opt/takyon (ProtectHome=true hides
# /root), so the seeded auth must land in the service HOME, not /root.
TAKYON_REMOTE_XURL_PATH="${TAKYON_REMOTE_XURL_PATH:-/opt/takyon/.xurl}"

if [[ ! -f "$TARGET_KEY" ]]; then
  echo "target key not found: $TARGET_KEY" >&2
  exit 1
fi

ssh_opts=(-i "$TARGET_KEY" -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new)

ssh "${ssh_opts[@]}" "$TARGET_HOST" 'bash -s' <<EOF
set -euo pipefail

command -v xurl >/dev/null 2>&1 || [ -x /root/.local/bin/xurl ]

env \
  TAKYON_HOME='$TAKYON_REMOTE_HOME' \
  HOME=/root \
  PYTHONUNBUFFERED=1 \
  TAKYON_SAFEBOX_URL='$TAKYON_REMOTE_SAFEBOX_URL' \
  TAKYON_REMOTE_XURL_PATH='$TAKYON_REMOTE_XURL_PATH' \
  '$TAKYON_REMOTE_RUNTIME/.venv/bin/python' - <<'PY'
import base64
import os
import sys
from pathlib import Path

from plugins.takyon import safebox
from plugins.takyon.core import load_takyon_env

try:
    from plugins.takyon.core import (
        _apply_xurl_oauth1_credentials,
        _read_x_oauth1_credentials,
        _xurl_auth_status_ok,
    )
except ImportError:
    # X auth migrated from xurl/OAuth1 to Composio; the legacy xurl seed helpers were removed
    # from core. There is nothing to seed on this rail anymore — exit cleanly so the deploy
    # continues (the operator rail already tolerated this; the sub-user rail must too).
    print("xurl OAuth1 machinery retired (X uses Composio); skipping xurl auth seed")
    raise SystemExit(0)

load_takyon_env()

auth_path = Path(os.environ.get("TAKYON_REMOTE_XURL_PATH") or "/opt/takyon/.xurl").expanduser()
auth_path.parent.mkdir(parents=True, exist_ok=True)

raw_secret = ""
for key in ("XURL_SHARED_AUTH_B64_SECRET", "XURL_SHARED_AUTH_SECRET"):
    raw_secret = str(safebox.read_env_backed_value(key) or "").strip()
    if raw_secret:
        break

oauth1 = _read_x_oauth1_credentials()
have_oauth1 = all(str(oauth1.get(key) or "").strip() for key in ("consumer_key", "consumer_secret", "access_token", "token_secret"))

if have_oauth1:
    if not _apply_xurl_oauth1_credentials(home=str(auth_path.parent)):
        print("failed to seed xurl OAuth1 credentials", file=sys.stderr)
        raise SystemExit(1)
    print(f"seeded {auth_path} from OAuth1 credentials")
elif raw_secret:
    try:
        decoded = base64.b64decode(raw_secret.encode("utf-8"), validate=True)
    except Exception:
        decoded = raw_secret.encode("utf-8")
    decoded = decoded.replace(b"\r\n", b"\n")
    if not decoded.strip():
        print("shared xurl auth secret is empty after decode", file=sys.stderr)
        raise SystemExit(1)
    auth_path.write_bytes(decoded)
    os.chmod(auth_path, 0o600)
    print(f"seeded {auth_path} from Safebox")
elif auth_path.exists():
    data = auth_path.read_bytes()
    if not data.strip():
        print(f"existing {auth_path} is empty", file=sys.stderr)
        raise SystemExit(1)
    encoded = base64.b64encode(data).decode("ascii")
    safebox.save_env_backed_value("XURL_SHARED_AUTH_B64_SECRET", encoded)
    print(f"promoted existing {auth_path} into Safebox")
else:
    print("shared xurl auth is not configured yet; leaving xurl auth unseeded")
    raise SystemExit(0)

if not _xurl_auth_status_ok(home=str(auth_path.parent)):
    print(f"xurl auth status failed for {auth_path}", file=sys.stderr)
    raise SystemExit(1)
PY

# Seeding runs as root; the service user must own the result (no-op before the user exists).
if id -u takyon >/dev/null 2>&1 && [ -e '$TAKYON_REMOTE_XURL_PATH' ]; then
  chown -R takyon:takyon '$TAKYON_REMOTE_XURL_PATH'
fi
EOF
