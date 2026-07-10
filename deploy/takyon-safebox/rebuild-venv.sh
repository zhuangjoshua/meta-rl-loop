#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
RUNTIME_DIR="$ROOT_DIR/hermes-agent-main"
VERIFY_LOCK_SCRIPT="$ROOT_DIR/deploy/takyon-safebox/verify-requirements-lock.sh"
LOCK_FILE="$RUNTIME_DIR/packaging/safebox-requirements.lock"

TAKYON_VPS_HOST="${TAKYON_VPS_HOST:-root@67.205.158.170}"
TAKYON_VPS_KEY="${TAKYON_VPS_KEY:-$HOME/.ssh/takyon_argon_alpha14}"
TAKYON_REMOTE_RUNTIME="${TAKYON_REMOTE_RUNTIME:-/opt/takyon/hermes-agent-main}"
TAKYON_REMOTE_VENV_ROOT="${TAKYON_REMOTE_VENV_ROOT:-/opt/takyon/venvs}"

[[ -x "$VERIFY_LOCK_SCRIPT" ]] || {
  echo "Safebox lock verifier not found or not executable: $VERIFY_LOCK_SCRIPT" >&2
  exit 1
}
[[ -f "$TAKYON_VPS_KEY" ]] || {
  echo "deploy key not found: $TAKYON_VPS_KEY" >&2
  exit 1
}

"$VERIFY_LOCK_SCRIPT"
lock_sha="$(python3 - "$LOCK_FILE" <<'PY'
from pathlib import Path
import hashlib
import sys

print(hashlib.sha256(Path(sys.argv[1]).read_bytes()).hexdigest())
PY
)"

ssh -i "$TAKYON_VPS_KEY" -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new \
  "$TAKYON_VPS_HOST" bash -s -- \
  "$TAKYON_REMOTE_RUNTIME" "$TAKYON_REMOTE_VENV_ROOT" "$lock_sha" <<'REMOTE'
set -euo pipefail

runtime="$1"
venv_root="$2"
lock_sha="$3"
lock_file="$runtime/packaging/safebox-requirements.lock"
candidate="$venv_root/safebox-${lock_sha:0:16}"
current="$venv_root/safebox-current"
previous="$venv_root/safebox-previous"
marker="$candidate/.takyon-safebox-lock-sha256"

[[ -f "$lock_file" ]] || {
  echo "remote Safebox dependency lock not found: $lock_file" >&2
  exit 1
}
remote_sha="$(sha256sum "$lock_file" | awk '{print $1}')"
[[ "$remote_sha" == "$lock_sha" ]] || {
  echo "remote Safebox dependency lock does not match the verified local lock" >&2
  exit 1
}

install -d -o root -g root -m 0755 "$venv_root"
exec 9>"$venv_root/.safebox-build.lock"
flock 9

smoke() {
  local python_bin="$1"
  "$python_bin" -m pip check
  TAKYON_HOME=/opt/takyon/.takyon TAKYON_HOST_ROLE=safebox "$python_bin" - <<'PY'
from importlib.metadata import version

expected = {
    "anthropic": "0.87.0",
    "google-genai": "1.65.0",
    "setuptools": "82.0.1",
    "tenacity": "9.1.4",
    "wheel": "0.47.0",
    "websockets": "16.0",
}
actual = {name: version(name) for name in expected}
if actual != expected:
    raise SystemExit(f"Safebox dependency versions are wrong: {actual}")

import anthropic  # noqa: F401
import boto3  # noqa: F401
import fal_client  # noqa: F401
import fastapi  # noqa: F401
import google.genai  # noqa: F401
from google.genai import types
import google.oauth2.service_account  # noqa: F401
import googleapiclient  # noqa: F401
import mcp  # noqa: F401
import numpy  # noqa: F401
import openai  # noqa: F401
import psycopg  # noqa: F401
import uvicorn  # noqa: F401
from PIL import Image  # noqa: F401
from plugins.takyon import safebox_app  # noqa: F401

raw = b"gemini-sdk-shape"
part = types.Part(inline_data=types.Blob(data=raw, mime_type="image/png"))
response = types.GenerateContentResponse(
    candidates=[types.Candidate(content=types.Content(parts=[part]))]
)
if not response.parts or bytes(response.parts[0].inline_data.data) != raw:
    raise SystemExit("google-genai response.parts/inline_data contract changed")
PY
}

candidate_valid=0
if [[ -x "$candidate/bin/python" && -f "$marker" ]]; then
  if [[ "$(<"$marker")" == "$lock_sha" ]] && smoke "$candidate/bin/python"; then
    candidate_valid=1
  fi
fi

if [[ "$candidate_valid" != 1 ]]; then
  if [[ -e "$candidate" || -L "$candidate" ]]; then
    mv "$candidate" "$candidate.invalid-$(date +%Y%m%d%H%M%S)"
  fi
  python3 -m venv "$candidate"
  "$candidate/bin/python" -m pip install \
    --disable-pip-version-check \
    --only-binary=:all: \
    --require-hashes \
    -r "$lock_file"
  "$candidate/bin/python" -m pip install \
    --disable-pip-version-check \
    --no-deps \
    --no-build-isolation \
    -e "$runtime"
  smoke "$candidate/bin/python"
  "$candidate/bin/python" - "$marker" "$lock_sha" <<'PY'
from pathlib import Path
import sys

Path(sys.argv[1]).write_text(sys.argv[2] + "\n", encoding="utf-8")
PY
fi

if [[ -e "$current" && ! -L "$current" ]]; then
  echo "refusing to replace non-symlink Safebox environment pointer: $current" >&2
  exit 1
fi
old_target=""
if [[ -L "$current" ]]; then
  old_target="$(readlink -f "$current")"
fi
if [[ -n "$old_target" && "$old_target" != "$candidate" ]]; then
  ln -sfn "$old_target" "$previous.next"
  mv -Tf "$previous.next" "$previous"
fi
ln -sfn "$candidate" "$current.next"
mv -Tf "$current.next" "$current"

if ! smoke "$current/bin/python"; then
  if [[ -n "$old_target" ]]; then
    ln -sfn "$old_target" "$current.rollback"
    mv -Tf "$current.rollback" "$current"
  else
    rm -f "$current"
  fi
  echo "Safebox candidate failed after activation; restored the previous pointer" >&2
  exit 1
fi
echo "Safebox environment ready: $candidate"
REMOTE
