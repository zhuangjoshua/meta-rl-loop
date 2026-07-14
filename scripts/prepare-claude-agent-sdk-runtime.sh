#!/usr/bin/env bash
set -euo pipefail

ROOT="${1:?repository root required}"
TAKYON_HOME_DIR="${2:?Takyon home required}"
RUNTIME="$ROOT/hermes-agent-main"
PYTHON="${TAKYON_PYTHON:-$RUNTIME/.venv/bin/python}"
NPM="${TAKYON_NPM:-$(command -v npm || true)}"
NODE="${TAKYON_NODE_EXECUTABLE:-$(command -v node || true)}"

[[ -x "$PYTHON" ]] || { echo "Takyon Python not found: $PYTHON" >&2; exit 1; }
[[ -x "$NODE" ]] || { echo "Node.js not found" >&2; exit 1; }
[[ -x "$NPM" ]] || { echo "npm not found" >&2; exit 1; }
"$NODE" -e 'const major=Number(process.versions.node.split(".")[0]); if (major < 20) process.exit(1)' \
  || { echo "Node.js 20 or newer is required" >&2; exit 1; }
[[ -f "$RUNTIME/skills/approved-skills.json" ]] || {
  echo "approved skill manifest not found" >&2
  exit 1
}

"$PYTHON" "$RUNTIME/scripts/build_approved_skills_manifest.py" \
  --skills-root "$RUNTIME/skills" --check >/dev/null

release_id="$($PYTHON - "$RUNTIME/skills/approved-skills.json" "$RUNTIME/package-lock.json" <<'PY'
from __future__ import annotations

import hashlib
import pathlib
import sys

digest = hashlib.sha256()
for raw in sys.argv[1:]:
    path = pathlib.Path(raw)
    digest.update(path.name.encode("utf-8"))
    digest.update(b"\0")
    digest.update(path.read_bytes())
    digest.update(b"\0")
print(digest.hexdigest())
PY
)"

sdk_root="$TAKYON_HOME_DIR/runtime/claude-agent-sdk"
release_root="$sdk_root/releases/$release_id"
plugin_root="$release_root/plugin"
node_runtime="$release_root/node-runtime"
mkdir -p "$sdk_root/releases"
if [[ -e "$sdk_root/current" && ! -L "$sdk_root/current" ]]; then
  echo "refusing to replace non-symlink SDK current path: $sdk_root/current" >&2
  exit 1
fi
if [[ ! -d "$release_root" ]]; then
  stage="$(mktemp -d "$sdk_root/releases/.${release_id}.XXXXXX")"
  cleanup_stage() {
    if [[ -n "${stage:-}" && -d "$stage" ]]; then
      chmod -R u+rwX "$stage" 2>/dev/null || true
      rm -rf "$stage"
    fi
  }
  trap cleanup_stage EXIT
  mkdir -p "$stage/node-runtime"
  install -m 0644 "$RUNTIME/package.json" "$stage/node-runtime/package.json"
  install -m 0644 "$RUNTIME/package-lock.json" "$stage/node-runtime/package-lock.json"
  (
    cd "$stage/node-runtime"
    "$NPM" ci --omit=dev --ignore-scripts --no-audit --no-fund >/dev/null
  )
  "$PYTHON" "$RUNTIME/scripts/build_approved_skills_manifest.py" \
    --skills-root "$RUNTIME/skills" \
    --check \
    --publish-root "$stage/plugin" >/dev/null
  "$PYTHON" - "$stage/node-runtime" <<'PY'
from pathlib import Path
import os
import sys

root = Path(sys.argv[1])
for path in sorted(root.rglob("*"), key=lambda item: len(item.parts), reverse=True):
    mode = path.stat().st_mode
    os.chmod(path, 0o555 if path.is_dir() or mode & 0o111 else 0o444)
os.chmod(root, 0o555)
PY
  chmod 0555 "$stage"
  mv "$stage" "$release_root"
  stage=""
  trap - EXIT
fi

sdk_module="$node_runtime/node_modules/@anthropic-ai/claude-agent-sdk/sdk.mjs"
zod_module="$node_runtime/node_modules/zod/index.js"
[[ -r "$sdk_module" && -r "$zod_module" ]] || {
  echo "sealed Agent SDK runtime is incomplete: $release_root" >&2
  exit 1
}
TAKYON_CLAUDE_AGENT_SDK_MODULE="$sdk_module" \
TAKYON_CLAUDE_ZOD_MODULE="$zod_module" \
TAKYON_CLAUDE_SKILLS_PLUGIN="$plugin_root" \
TAKYON_CLAUDE_SKILLS_MANIFEST="$plugin_root/approved-skills.json" \
TAKYON_RUNTIME_SOURCE="$RUNTIME" \
  "$NODE" --input-type=module - <<'JS'
import fs from "node:fs";
import { pathToFileURL } from "node:url";
const sdkPackage = JSON.parse(fs.readFileSync(
  new URL("./package.json", pathToFileURL(process.env.TAKYON_CLAUDE_AGENT_SDK_MODULE)),
  "utf8",
));
const zodPackage = JSON.parse(fs.readFileSync(
  new URL("./package.json", pathToFileURL(process.env.TAKYON_CLAUDE_ZOD_MODULE)),
  "utf8",
));
if (sdkPackage.version !== "0.3.148") throw new Error(`unexpected Agent SDK ${sdkPackage.version}`);
if (zodPackage.version !== "4.4.3") throw new Error(`unexpected Zod ${zodPackage.version}`);
const sdk = await import(pathToFileURL(process.env.TAKYON_CLAUDE_AGENT_SDK_MODULE).href);
const zod = await import(pathToFileURL(process.env.TAKYON_CLAUDE_ZOD_MODULE).href);
if (typeof sdk.query !== "function" || typeof sdk.createSdkMcpServer !== "function") {
  throw new Error("Agent SDK API unavailable");
}
if (typeof zod.z?.fromJSONSchema !== "function") throw new Error("Zod API unavailable");
const { verifyApprovedSkillPlugin } = await import(
  pathToFileURL(process.env.TAKYON_RUNTIME_SOURCE + "/scripts/takyon-claude-primary-runtime.mjs").href
);
await verifyApprovedSkillPlugin({
  pluginPath: process.env.TAKYON_CLAUDE_SKILLS_PLUGIN,
  manifestPath: process.env.TAKYON_CLAUDE_SKILLS_MANIFEST,
});
JS

current_next="$sdk_root/.current.$$.next"
rm -f "$current_next"
ln -s "releases/$release_id" "$current_next"
"$PYTHON" - "$current_next" "$sdk_root/current" <<'PY'
import os
import sys

os.replace(sys.argv[1], sys.argv[2])
PY

# The reviewed plugin is the only skill source for the primary SDK runtime.
# Refuse symlink tricks, then remove the obsolete mutable skill copy from this home.
legacy_skills="$TAKYON_HOME_DIR/skills"
if [[ -L "$legacy_skills" ]]; then
  echo "refusing to remove symlinked legacy skills path: $legacy_skills" >&2
  exit 1
fi
rm -rf "$legacy_skills"
[[ ! -e "$legacy_skills" ]] || {
  echo "legacy skills path remains after SDK activation: $legacy_skills" >&2
  exit 1
}

printf 'export TAKYON_CLAUDE_SKILLS_PLUGIN=%q\n' "$sdk_root/current/plugin"
printf 'export TAKYON_CLAUDE_SKILLS_MANIFEST=%q\n' \
  "$sdk_root/current/plugin/approved-skills.json"
printf 'export TAKYON_CLAUDE_NODE_RUNTIME=%q\n' \
  "$sdk_root/current/node-runtime"
printf 'export TAKYON_DISABLE_LEGACY_SKILL_SYNC=1\n'
