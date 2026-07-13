#!/usr/bin/env bash
set -euo pipefail

: "${TAKYON_STAGED_RUNTIME:?}"
: "${TAKYON_LIVE_RUNTIME:?}"
: "${TAKYON_REMOTE_HOME:?}"
: "${TAKYON_DASHBOARD_UNIT_CANDIDATE:?}"
: "${TAKYON_WORKER_UNIT_CANDIDATE:?}"
: "${TAKYON_DOCKER_BROKER_UNIT_CANDIDATE:?}"
: "${TAKYON_OPERATOR_CLI_CANDIDATE:?}"
: "${TAKYON_SKILLS_PREFLIGHT_HOME:?}"
: "${TAKYON_REMOTE_SAFEBOX_URL:?}"
: "${TAKYON_DENO_VERSION:?}"
: "${TAKYON_CLAUDE_AGENT_DOCKER_IMAGE:?}"

runtime="$TAKYON_STAGED_RUNTIME"
python="$TAKYON_LIVE_RUNTIME/.venv/bin/python"

grep -F -- '--tui' "$TAKYON_DASHBOARD_UNIT_CANDIDATE" >/dev/null
systemd-analyze verify \
  "$TAKYON_DASHBOARD_UNIT_CANDIDATE" \
  "$TAKYON_WORKER_UNIT_CANDIDATE" \
  "$TAKYON_DOCKER_BROKER_UNIT_CANDIDATE" >/dev/null
[[ -s "$TAKYON_OPERATOR_CLI_CANDIDATE" ]]
bash -n "$TAKYON_OPERATOR_CLI_CANDIDATE"
systemctl enable docker >/dev/null
systemctl start docker
systemctl is-active --quiet docker
docker version >/dev/null
command -v xurl >/dev/null 2>&1 || [[ -x /root/.local/bin/xurl ]]
command -v deno >/dev/null 2>&1
[[ "$(deno --version | awk 'NR==1 {print $2}')" == "$TAKYON_DENO_VERSION" ]]
command -v systemd-run >/dev/null 2>&1

if ! id -u takyon >/dev/null 2>&1; then
  useradd --system --user-group --home-dir /opt/takyon --shell /usr/sbin/nologin takyon
fi
if id -nG takyon | grep -qw docker; then
  gpasswd -d takyon docker >/dev/null 2>&1 || deluser takyon docker >/dev/null 2>&1 || true
fi
chown takyon:takyon /opt/takyon
chown -R takyon:takyon "$TAKYON_REMOTE_HOME"
[[ ! -d /opt/takyon/secrets ]] || chown -R takyon:takyon /opt/takyon/secrets
if [[ -e /root/.xurl && ! -e /opt/takyon/.xurl ]]; then cp -a /root/.xurl /opt/takyon/.xurl; fi
if [[ -e /opt/takyon/.xurl ]]; then chown -R takyon:takyon /opt/takyon/.xurl; fi
grep -q '^TAKYON_SAFEBOX_TOKEN=' /opt/takyon/.takyon/.env /opt/takyon/secrets/.env 2>/dev/null \
  || { echo 'TAKYON_SAFEBOX_TOKEN missing from operator env' >&2; exit 1; }
grep -q '^TAKYON_SAFEBOX_OPERATOR_TOKEN=' /opt/takyon/.takyon/.env /opt/takyon/secrets/.env 2>/dev/null \
  || { echo 'TAKYON_SAFEBOX_OPERATOR_TOKEN missing from operator env' >&2; exit 1; }
for key in R2_S3_ENDPOINT R2_BUCKET; do
  grep -q "^${key}=" /opt/takyon/.takyon/.env /opt/takyon/secrets/.env 2>/dev/null \
    || { echo "$key missing from operator env" >&2; exit 1; }
done
if grep -qE '^R2_S3_(ACCESS_KEY_ID|SECRET_ACCESS_KEY)=' \
    /opt/takyon/.takyon/.env /opt/takyon/secrets/.env 2>/dev/null; then
  echo 'R2 write credentials must not exist on operator host' >&2
  exit 1
fi
if [[ -L /usr/local/bin/xurl && -x /root/.local/bin/xurl ]]; then
  install -m 0755 /root/.local/bin/xurl /usr/local/bin/xurl
fi

if ! docker image inspect "$TAKYON_CLAUDE_AGENT_DOCKER_IMAGE" >/dev/null 2>&1; then
  docker pull "$TAKYON_CLAUDE_AGENT_DOCKER_IMAGE"
fi
docker run --rm --entrypoint node "$TAKYON_CLAUDE_AGENT_DOCKER_IMAGE" --version >/dev/null
if docker run --rm --entrypoint /bin/sh "$TAKYON_CLAUDE_AGENT_DOCKER_IMAGE" -lc \
  'test -x /usr/bin/chromium && /usr/bin/chromium --version >/dev/null' >/dev/null 2>&1; then
  echo "optional Claude worker Chromium renderer available"
else
  echo "optional Claude worker Chromium renderer unavailable; continuing"
fi
docker run --rm \
  --entrypoint node \
  --mount "type=bind,src=$runtime,dst=/takyon-runtime,readonly" \
  --workdir /takyon-runtime \
  "$TAKYON_CLAUDE_AGENT_DOCKER_IMAGE" \
  --input-type=module \
  -e 'import fs from "node:fs"; const pkg = JSON.parse(fs.readFileSync("package.json", "utf8")); const lock = JSON.parse(fs.readFileSync("package-lock.json", "utf8")); const sdk = "@anthropic-ai/claude-agent-sdk"; if (!pkg.dependencies?.[sdk] || !lock.packages?.[`node_modules/${sdk}`]) throw new Error("Agent SDK dependency is not pinned"); const { validateNativeTasteSkill } = await import("./scripts/takyon-claude-agent-task.mjs"); await validateNativeTasteSkill();' \
  >/dev/null

if grep -F -- 'TAKYON_STORAGE_BACKEND=supabase_s3' "$TAKYON_DASHBOARD_UNIT_CANDIDATE" >/dev/null \
  || grep -F -- 'TAKYON_STORAGE_BACKEND=supabase_s3' "$TAKYON_WORKER_UNIT_CANDIDATE" >/dev/null; then
  "$python" -c 'import boto3' >/dev/null 2>&1 || "$python" -m pip install 'boto3==1.42.89'
  env PYTHONPATH="$runtime" TAKYON_HOME="$TAKYON_REMOTE_HOME" HOME=/root \
    PYTHONUNBUFFERED=1 TAKYON_STORAGE_BACKEND=supabase_s3 TAKYON_HOST_ROLE=operator \
    TAKYON_SAFEBOX_URL="$TAKYON_REMOTE_SAFEBOX_URL" "$python" - <<'PY'
from plugins.takyon.core import load_takyon_env
from plugins.takyon import storage

load_takyon_env()
if getattr(storage.get_storage_backend(), "name", "") != "supabase_s3":
    raise SystemExit("unexpected storage backend")
PY
fi

if ! (cd "$runtime" && PYTHONPATH="$runtime" "$python" - <<'PY'
from tools.lazy_deps import feature_missing

raise SystemExit(1 if feature_missing("image.logo_postprocess") else 0)
PY
); then
  "$python" -m pip install 'Pillow>=10.4,<12' 'numpy==2.4.3'
fi

PYTHONPATH="$runtime" python3 -m compileall -q \
  "$runtime/plugins/takyon" "$runtime/takyon_cli" "$runtime/tui_gateway"
rm -rf "$TAKYON_SKILLS_PREFLIGHT_HOME"
env PYTHONPATH="$runtime" TAKYON_HOME="$TAKYON_SKILLS_PREFLIGHT_HOME" HOME=/opt/takyon \
  TAKYON_FORCE_RESTORE_BUNDLED_SKILLS=1 "$python" - <<'PY'
from tools.skills_sync import sync_skills

result = sync_skills(quiet=False)
if not result.get("total_bundled"):
    raise SystemExit("staged runtime contains no bundled skills")
if result.get("user_modified"):
    raise SystemExit(f"bundled skill sync left user-modified entries behind: {result['user_modified']}")
PY
env PYTHONPATH="$runtime" TAKYON_HOME="$TAKYON_REMOTE_HOME" HOME=/root \
  PYTHONUNBUFFERED=1 TAKYON_HOST_ROLE=operator TAKYON_SAFEBOX_URL="$TAKYON_REMOTE_SAFEBOX_URL" \
  "$python" "$runtime/scripts/verify-supabase-auth-runtime.py"

for unit_file in "$TAKYON_DASHBOARD_UNIT_CANDIDATE" "$TAKYON_WORKER_UNIT_CANDIDATE"; do
  grep -Fx -- 'Environment=TAKYON_STRICT_MODEL_ROLES=1' "$unit_file" >/dev/null
  grep -Fx -- 'Environment=TAKYON_MODEL=gpt-5.5' "$unit_file" >/dev/null
  grep -Fx -- 'Environment=TAKYON_CLAUDE_AGENT_MODEL=deepseek-v4-pro' "$unit_file" >/dev/null
  grep -Fx -- 'Environment=ANTHROPIC_MODEL=deepseek-v4-pro' "$unit_file" >/dev/null
  grep -Fx -- 'Environment=ANTHROPIC_DEFAULT_OPUS_MODEL=deepseek-v4-pro' "$unit_file" >/dev/null
  grep -Fx -- 'Environment=ANTHROPIC_DEFAULT_SONNET_MODEL=deepseek-v4-pro' "$unit_file" >/dev/null
  grep -Fx -- 'Environment=ANTHROPIC_DEFAULT_HAIKU_MODEL=deepseek-v4-pro' "$unit_file" >/dev/null
  grep -Fx -- 'Environment=CLAUDE_CODE_SUBAGENT_MODEL=deepseek-v4-pro' "$unit_file" >/dev/null
done

PYTHONPATH="$runtime" "$python" - <<'PY'
from pathlib import Path
import yaml

data = yaml.safe_load(Path("/opt/takyon/.takyon/config.yaml").read_text()) or {}
model = data.get("model") or {}
expected = {
    "provider": "custom",
    "base_url": "https://api.openai.com/v1",
    "api_mode": "codex_responses",
    "default": "gpt-5.5",
    "claude_agent_default": "deepseek-v4-pro",
}
wrong = {key: model.get(key) for key, value in expected.items() if model.get(key) != value}
if wrong:
    raise SystemExit(f"operator model config violates strict role contract: {wrong}")
if data.get("fallback_model") or data.get("fallback_providers"):
    raise SystemExit("operator model fallback config must be absent")
PY
