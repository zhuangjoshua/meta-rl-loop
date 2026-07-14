#!/usr/bin/env bash
set -euo pipefail

: "${TAKYON_STAGED_RUNTIME:?}"
: "${TAKYON_LIVE_RUNTIME:?}"
: "${TAKYON_REMOTE_HOME:?}"
: "${TAKYON_DASHBOARD_UNIT_CANDIDATE:?}"
: "${TAKYON_WORKER_UNIT_CANDIDATE:?}"
: "${TAKYON_DOCKER_BROKER_UNIT_CANDIDATE:?}"
: "${TAKYON_OPERATOR_CLI_CANDIDATE:?}"
: "${TAKYON_CLAUDE_RELEASE_ROOT:?}"
: "${TAKYON_DEPLOY_SOURCE_REVISION:?}"
: "${TAKYON_REMOTE_SAFEBOX_URL:?}"
: "${TAKYON_DENO_VERSION:?}"

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
command -v node >/dev/null 2>&1
command -v npm >/dev/null 2>&1
command -v google-chrome-stable >/dev/null 2>&1
google-chrome-stable --version >/dev/null
timeout 20 runuser -u takyon -- env HOME=/opt/takyon \
  google-chrome-stable --headless=new --no-sandbox --disable-dev-shm-usage \
  --dump-dom about:blank >/dev/null
node -e 'const major=Number(process.versions.node.split(".")[0]); if (major < 20) process.exit(1)'

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

node --check "$runtime/scripts/takyon-claude-primary-runtime.mjs" >/dev/null

# Build one content-addressed SDK runtime owned by this exact release.  The
# service never imports mutable checkout dependencies or a mutable skills tree.
sdk_release="$TAKYON_CLAUDE_RELEASE_ROOT/releases/$TAKYON_DEPLOY_SOURCE_REVISION"
if [[ -e "$sdk_release" && ! -d "$sdk_release" ]]; then
  echo "Claude SDK release path is not a directory: $sdk_release" >&2
  exit 1
fi
if [[ ! -d "$sdk_release" ]]; then
  install -d -m 0755 "$TAKYON_CLAUDE_RELEASE_ROOT/releases"
  sdk_stage="$(mktemp -d "$TAKYON_CLAUDE_RELEASE_ROOT/releases/.${TAKYON_DEPLOY_SOURCE_REVISION}.XXXXXX")"
  cleanup_sdk_stage() {
    if [[ -n "${sdk_stage:-}" && -d "$sdk_stage" ]]; then
      chmod -R u+rwX "$sdk_stage" 2>/dev/null || true
      rm -rf "$sdk_stage"
    fi
  }
  trap cleanup_sdk_stage EXIT
  install -d -m 0755 "$sdk_stage/node-runtime"
  install -m 0644 "$runtime/package.json" "$sdk_stage/node-runtime/package.json"
  install -m 0644 "$runtime/package-lock.json" "$sdk_stage/node-runtime/package-lock.json"
  (
    cd "$sdk_stage/node-runtime"
    npm ci --omit=dev --ignore-scripts --no-audit --no-fund
  )
  env PYTHONPATH="$runtime" "$python" \
    "$runtime/scripts/build_approved_skills_manifest.py" \
    --skills-root "$runtime/skills" \
    --check \
    --publish-root "$sdk_stage/plugin"
  find "$sdk_stage/node-runtime" -type d -exec chmod 0555 {} +
  find "$sdk_stage/node-runtime" -type f -perm /111 -exec chmod 0555 {} +
  find "$sdk_stage/node-runtime" -type f ! -perm /111 -exec chmod 0444 {} +
  chmod 0555 "$sdk_stage"
  mv "$sdk_stage" "$sdk_release"
  sdk_stage=""
  trap - EXIT
fi

sdk_module="$sdk_release/node-runtime/node_modules/@anthropic-ai/claude-agent-sdk/sdk.mjs"
zod_module="$sdk_release/node-runtime/node_modules/zod/index.js"
test -r "$sdk_module"
test -r "$zod_module"
env \
  TAKYON_CLAUDE_NODE_RUNTIME="$sdk_release/node-runtime" \
  TAKYON_CLAUDE_AGENT_SDK_MODULE="$sdk_module" \
  TAKYON_CLAUDE_ZOD_MODULE="$zod_module" \
  node --input-type=module -e '
  import fs from "node:fs";
  import { pathToFileURL } from "node:url";
  const pkg = JSON.parse(fs.readFileSync(process.env.TAKYON_CLAUDE_NODE_RUNTIME + "/node_modules/@anthropic-ai/claude-agent-sdk/package.json", "utf8"));
  if (pkg.version !== "0.3.148") throw new Error(`unexpected Agent SDK ${pkg.version}`);
  const sdk = await import(pathToFileURL(process.env.TAKYON_CLAUDE_AGENT_SDK_MODULE).href);
  const zod = await import(pathToFileURL(process.env.TAKYON_CLAUDE_ZOD_MODULE).href);
  if (typeof sdk.query !== "function" || typeof sdk.createSdkMcpServer !== "function") throw new Error("Agent SDK API unavailable");
  if (typeof zod.z?.fromJSONSchema !== "function") throw new Error("Zod JSON-schema API unavailable");
'
(
  cd "$runtime"
  env \
    TAKYON_CLAUDE_AGENT_SDK_MODULE="$sdk_module" \
    TAKYON_CLAUDE_ZOD_MODULE="$zod_module" \
    TAKYON_CLAUDE_SKILLS_PLUGIN="$sdk_release/plugin" \
    TAKYON_CLAUDE_SKILLS_MANIFEST="$sdk_release/plugin/approved-skills.json" \
    node --input-type=module -e '
      import { verifyApprovedSkillPlugin } from "./scripts/takyon-claude-primary-runtime.mjs";
      await verifyApprovedSkillPlugin({
        pluginPath: process.env.TAKYON_CLAUDE_SKILLS_PLUGIN,
        manifestPath: process.env.TAKYON_CLAUDE_SKILLS_MANIFEST,
      });
    '
)

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
env PYTHONPATH="$runtime" TAKYON_HOME="$TAKYON_REMOTE_HOME" HOME=/root \
  PYTHONUNBUFFERED=1 TAKYON_HOST_ROLE=operator TAKYON_SAFEBOX_URL="$TAKYON_REMOTE_SAFEBOX_URL" \
  "$python" "$runtime/scripts/verify-supabase-auth-runtime.py"

for unit_file in "$TAKYON_DASHBOARD_UNIT_CANDIDATE" "$TAKYON_WORKER_UNIT_CANDIDATE"; do
  grep -Fx -- 'Environment=TAKYON_STRICT_MODEL_ROLES=1' "$unit_file" >/dev/null
  grep -Fx -- 'Environment=TAKYON_MODEL=deepseek-v4-pro' "$unit_file" >/dev/null
  grep -Fx -- 'Environment=TAKYON_CLAUDE_AGENT_MODEL=deepseek-v4-pro' "$unit_file" >/dev/null
  grep -Fx -- 'Environment=ANTHROPIC_MODEL=deepseek-v4-pro' "$unit_file" >/dev/null
  grep -Fx -- 'Environment=ANTHROPIC_DEFAULT_OPUS_MODEL=deepseek-v4-pro' "$unit_file" >/dev/null
  grep -Fx -- 'Environment=ANTHROPIC_DEFAULT_SONNET_MODEL=deepseek-v4-pro' "$unit_file" >/dev/null
  grep -Fx -- 'Environment=ANTHROPIC_DEFAULT_HAIKU_MODEL=deepseek-v4-pro' "$unit_file" >/dev/null
  grep -Fx -- 'Environment=CLAUDE_CODE_SUBAGENT_MODEL=deepseek-v4-pro' "$unit_file" >/dev/null
  grep -Fx -- 'Environment=TAKYON_PRIMARY_AGENT_MAX_BUDGET_USD=5' "$unit_file" >/dev/null
  grep -Fx -- 'Environment=TAKYON_PRIMARY_AGENT_PER_CALL_MAX_BUDGET_USD=2' "$unit_file" >/dev/null
  grep -Fx -- 'Environment=TAKYON_OPERATOR_SESSION_MAX_COST_MICROUSD=2000000' "$unit_file" >/dev/null
  grep -Fx -- 'Environment=TAKYON_CLAUDE_SKILLS_PLUGIN=/opt/takyon/.takyon/runtime/claude-agent-sdk/current/plugin' "$unit_file" >/dev/null
  grep -Fx -- 'Environment=TAKYON_CLAUDE_SKILLS_MANIFEST=/opt/takyon/.takyon/runtime/claude-agent-sdk/current/plugin/approved-skills.json' "$unit_file" >/dev/null
  grep -Fx -- 'Environment=TAKYON_CLAUDE_NODE_RUNTIME=/opt/takyon/.takyon/runtime/claude-agent-sdk/current/node-runtime' "$unit_file" >/dev/null
  grep -Fx -- 'Environment=TAKYON_DISABLE_LEGACY_SKILL_SYNC=1' "$unit_file" >/dev/null
done

PYTHONPATH="$runtime" "$python" - <<'PY'
from pathlib import Path
import yaml

data = yaml.safe_load(Path("/opt/takyon/.takyon/config.yaml").read_text()) or {}
model = data.get("model") or {}
expected = {
    "claude_agent_default": "deepseek-v4-pro",
}
wrong = {key: model.get(key) for key, value in expected.items() if model.get(key) != value}
if wrong:
    raise SystemExit(f"operator model config violates strict role contract: {wrong}")
if data.get("fallback_model") or data.get("fallback_providers"):
    raise SystemExit("operator model fallback config must be absent")
PY
