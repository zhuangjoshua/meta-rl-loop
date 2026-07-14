import { createHash, randomUUID } from "node:crypto";
import fs from "node:fs/promises";
import path from "node:path";
import { pathToFileURL } from "node:url";

export const PRIMARY_AGENT_MODEL = "deepseek-v4-pro";
export const PRIMARY_RUNTIME_EVENT_VERSION = 1;
export const PRIMARY_RUNTIME_SOURCE = "claude-agent-sdk";
export const SDK_PROGRESS_PREFIX = "TAKYON_SDK_EVENT ";
export const SDK_MODULE_PATH_ENV = "TAKYON_CLAUDE_AGENT_SDK_MODULE";

const SANDBOX_PATH = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin";
const DEFAULT_LOCAL_TOOLS = Object.freeze(["Skill"]);
const AGENT_TOOLS = new Set([
  "Agent",
  "Task",
  "TaskOutput",
  "TaskStop",
  "TeamCreate",
  "TeamDelete",
  "SendMessage",
]);
const PATH_KEYS = Object.freeze(["file_path", "path", "notebook_path"]);
const MODEL_ALIASES = Object.freeze([
  "ANTHROPIC_MODEL",
  "ANTHROPIC_DEFAULT_OPUS_MODEL",
  "ANTHROPIC_DEFAULT_SONNET_MODEL",
  "ANTHROPIC_DEFAULT_HAIKU_MODEL",
  "CLAUDE_CODE_SUBAGENT_MODEL",
]);
const UUID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const DIGEST_PATTERN = /^sha256:([0-9a-f]{64})$/;
const INVOCATION_MODES = new Set(["interactive", "bootstrap", "wake"]);
const SESSION_APPEND_MAX_BYTES = 8 * 1024 * 1024;
const SESSION_LOAD_MAX_BYTES = 64 * 1024 * 1024;
const SESSION_APPEND_MAX_ENTRIES = 2_000;
const SESSION_LOAD_MAX_ENTRIES = 250_000;

export class PrimaryRuntimeError extends Error {
  constructor(message, { code = "primary_runtime_error", receipt = null } = {}) {
    super(message);
    this.name = "PrimaryRuntimeError";
    this.code = code;
    this.receipt = receipt;
  }
}

function fail(code, message, receipt = null) {
  throw new PrimaryRuntimeError(message, { code, receipt });
}

function cleanString(value) {
  return typeof value === "string" ? value.trim() : "";
}

async function loadSdkModule(sourceEnv) {
  const configured = cleanString(sourceEnv?.[SDK_MODULE_PATH_ENV]);
  if (!configured) return import("@anthropic-ai/claude-agent-sdk");
  if (!path.isAbsolute(configured)) {
    fail("sdk_module_path", `${SDK_MODULE_PATH_ENV} must be an absolute file path`);
  }
  return import(pathToFileURL(configured).href);
}

function normalizeRelative(value) {
  return String(value || "")
    .replace(/\\/g, "/")
    .replace(/^\.\/+/, "")
    .replace(/^\/+/, "")
    .replace(/\/+$/, "");
}

function isSubpath(root, candidate) {
  const relative = path.relative(path.resolve(root), path.resolve(candidate));
  return relative === "" || (!relative.startsWith("..") && !path.isAbsolute(relative));
}

function ensureSubpath(root, candidate, label) {
  if (!isSubpath(root, candidate)) {
    fail("path_escape", `${label} must remain inside ${root}`);
  }
}

function redacted(value) {
  return String(value || "")
    .replace(/sk-[A-Za-z0-9_-]{12,}/g, "sk-[redacted]")
    .replace(/EAA[A-Za-z0-9_-]{20,}/g, "EAA[redacted]")
    .replace(/\b(api[_-]?key|access[_-]?token|secret)=([^&\s]+)/gi, "$1=[redacted]");
}

function compact(value, limit = 240) {
  const text = redacted(value).replace(/\s+/g, " ").trim();
  if (!text) return "";
  return text.length <= limit ? text : `${text.slice(0, limit - 1)}…`;
}

function skillNameFromFrontmatter(value) {
  const match = String(value || "").slice(0, 16_384).match(
    /^---\s*$[\s\S]*?^name:\s*['"]?([^'"\n]+?)['"]?\s*$[\s\S]*?^---\s*$/m
  );
  return cleanString(match?.[1]);
}

function ignoredSkillDigestPath(relativePath) {
  const parts = normalizeRelative(relativePath).split("/").filter(Boolean);
  const filename = parts.at(-1) || "";
  return parts.includes("__pycache__") || /\.(?:pyc|pyo)$/i.test(filename);
}

async function walkTree(root, { includeDirectories = false, ignore = () => false } = {}) {
  const output = [];
  async function visit(directory) {
    const entries = await fs.readdir(directory, { withFileTypes: true });
    entries.sort((left, right) => left.name < right.name ? -1 : left.name > right.name ? 1 : 0);
    for (const entry of entries) {
      const absolute = path.join(directory, entry.name);
      const relative = normalizeRelative(path.relative(root, absolute));
      if (ignore(relative)) continue;
      const stat = await fs.lstat(absolute);
      if (stat.isSymbolicLink()) {
        fail("skill_plugin_symlink", `skill plugin contains forbidden symlink ${relative}`);
      }
      if (stat.isDirectory()) {
        if (includeDirectories) output.push({ absolute, relative, stat, type: "directory" });
        await visit(absolute);
        continue;
      }
      if (!stat.isFile()) {
        fail("skill_plugin_entry_type", `skill plugin contains non-regular entry ${relative}`);
      }
      output.push({ absolute, relative, stat, type: "file" });
    }
  }
  await visit(root);
  return output;
}

export async function digestSkillDirectory(skillDirectory) {
  const root = path.resolve(skillDirectory);
  const entries = await walkTree(root, { ignore: ignoredSkillDigestPath });
  const digest = createHash("sha256");
  for (const entry of entries) {
    digest.update(entry.relative, "utf8");
    digest.update(Buffer.from([0]));
    digest.update(await fs.readFile(entry.absolute));
    digest.update(Buffer.from([0]));
  }
  return `sha256:${digest.digest("hex")}`;
}

function normalizeManifestSkill(raw, pluginRoot) {
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) {
    fail("skill_manifest_schema", "each approved skill manifest entry must be an object");
  }
  const name = cleanString(raw.name);
  const sourcePath = normalizeRelative(raw.source_path || raw.path);
  const pluginPath = normalizeRelative(raw.plugin_path);
  const sourceSkillFile = normalizeRelative(
    raw.skill_file || (sourcePath ? `${sourcePath}/SKILL.md` : "")
  );
  const runtimeSkillFile = pluginPath ? `${pluginPath}/SKILL.md` : "";
  const contentDigest = cleanString(raw.content_digest || raw.sha256).toLowerCase();
  const description = cleanString(raw.description);
  const allowedModes = Array.isArray(raw.allowed_modes)
    ? raw.allowed_modes.map(cleanString).filter(Boolean)
    : [];
  if (!/^[a-z0-9]+(?:-[a-z0-9]+)*$/.test(name)) {
    fail("skill_manifest_schema", `invalid approved skill name ${JSON.stringify(name)}`);
  }
  if (
    !sourcePath
    || !pluginPath
    || !sourceSkillFile
    || path.posix.isAbsolute(sourcePath)
    || path.posix.isAbsolute(pluginPath)
    || path.posix.isAbsolute(sourceSkillFile)
  ) {
    fail("skill_manifest_schema", `approved skill ${name} has an invalid source path`);
  }
  if (pluginPath !== `skills/${name}`) {
    fail(
      "skill_manifest_schema",
      `approved skill ${name} must publish at the canonical plugin path skills/${name}`
    );
  }
  if (!DIGEST_PATTERN.test(contentDigest)) {
    fail("skill_manifest_schema", `approved skill ${name} has an invalid SHA-256 digest`);
  }
  if (!description) {
    fail("skill_manifest_schema", `approved skill ${name} has no routing description`);
  }
  if (
    allowedModes.length === 0
    || new Set(allowedModes).size !== allowedModes.length
    || allowedModes.some((mode) => !INVOCATION_MODES.has(mode))
  ) {
    fail("skill_manifest_schema", `approved skill ${name} has invalid allowed_modes`);
  }
  const runtimeDirectory = path.resolve(pluginRoot, pluginPath);
  const absoluteSkillFile = path.resolve(pluginRoot, runtimeSkillFile);
  ensureSubpath(pluginRoot, runtimeDirectory, `approved skill ${name} plugin_path`);
  ensureSubpath(runtimeDirectory, absoluteSkillFile, `approved skill ${name} runtime SKILL.md`);
  return {
    name,
    sourcePath,
    pluginPath,
    sourceSkillFile,
    runtimeSkillFile,
    runtimeDirectory,
    absoluteSkillFile,
    contentDigest,
    description,
    allowedModes,
  };
}

function normalizeDiscoveryRoot(value) {
  return normalizeRelative(String(value || "").replace(/^\.\//, ""));
}

function assertPluginManifest(pluginMetadata, approvedManifest) {
  if (!pluginMetadata || typeof pluginMetadata !== "object" || Array.isArray(pluginMetadata)) {
    fail("skill_plugin_metadata", ".claude-plugin/plugin.json must contain an object");
  }
  const expectedName = cleanString(approvedManifest.plugin?.name);
  const expectedVersion = cleanString(approvedManifest.plugin?.version);
  if (!expectedName || cleanString(pluginMetadata.name) !== expectedName) {
    fail("skill_plugin_metadata", "plugin name does not match approved-skills.json");
  }
  if (!expectedVersion || cleanString(pluginMetadata.version) !== expectedVersion) {
    fail("skill_plugin_metadata", "plugin version does not match approved-skills.json");
  }
  const manifestRoots = Array.isArray(approvedManifest.discovery_roots)
    ? approvedManifest.discovery_roots.map(normalizeDiscoveryRoot).filter(Boolean)
    : [];
  const pluginRoots = Array.isArray(pluginMetadata.skills)
    ? pluginMetadata.skills.map(normalizeDiscoveryRoot).filter(Boolean)
    : ["skills"];
  if (
    manifestRoots.length === 0
    || new Set(manifestRoots).size !== manifestRoots.length
    || pluginRoots.length !== 1
    || pluginRoots[0] !== "skills"
  ) {
    fail(
      "skill_plugin_discovery_roots",
      "published plugin must discover exactly the flat skills/ runtime directory"
    );
  }
  const allowedMetadataFields = new Set(["name", "version", "description", "skills"]);
  const unexpectedFields = Object.keys(pluginMetadata).filter((field) => !allowedMetadataFields.has(field));
  if (unexpectedFields.length) {
    fail(
      "skill_plugin_capability",
      `skills-only plugin contains unsupported metadata: ${unexpectedFields.join(",")}`
    );
  }
  for (const field of ["agents", "commands", "hooks", "mcpServers", "mcp_servers"]) {
    if (pluginMetadata[field] !== undefined) {
      fail("skill_plugin_capability", `skills-only plugin may not declare ${field}`);
    }
  }
}

async function assertReadOnlyPlugin(pluginRoot, manifestPath) {
  const roots = [
    { absolute: pluginRoot, relative: ".", stat: await fs.lstat(pluginRoot) },
    { absolute: manifestPath, relative: normalizeRelative(path.relative(pluginRoot, manifestPath)), stat: await fs.lstat(manifestPath) },
    ...await walkTree(pluginRoot, { includeDirectories: true }),
  ];
  const seen = new Set();
  for (const entry of roots) {
    if (seen.has(entry.absolute)) continue;
    seen.add(entry.absolute);
    if ((entry.stat.mode & 0o222) !== 0) {
      fail("skill_plugin_writable", `approved skill plugin entry is writable: ${entry.relative}`);
    }
  }
}

export async function verifyApprovedSkillPlugin({ pluginPath, manifestPath }) {
  const pluginRoot = path.resolve(cleanString(pluginPath));
  const approvedManifestPath = path.resolve(cleanString(manifestPath));
  if (!cleanString(pluginPath) || !cleanString(manifestPath)) {
    fail("skill_plugin_configuration", "pluginPath and manifestPath are required");
  }
  ensureSubpath(pluginRoot, approvedManifestPath, "approved skill manifest");
  const [pluginRootStat, approvedManifestStat] = await Promise.all([
    fs.lstat(pluginRoot),
    fs.lstat(approvedManifestPath),
  ]).catch((error) => {
    fail("skill_plugin_unreadable", `approved skill plugin is unreadable: ${error?.message || error}`);
  });
  if (!pluginRootStat.isDirectory() || pluginRootStat.isSymbolicLink()) {
    fail("skill_plugin_entry_type", "approved skill plugin root must be a real directory");
  }
  if (!approvedManifestStat.isFile() || approvedManifestStat.isSymbolicLink()) {
    fail("skill_plugin_entry_type", "approved skill manifest must be a regular file");
  }
  let approvedManifest;
  let pluginMetadata;
  try {
    [approvedManifest, pluginMetadata] = await Promise.all([
      fs.readFile(approvedManifestPath, "utf8").then(JSON.parse),
      fs.readFile(path.join(pluginRoot, ".claude-plugin", "plugin.json"), "utf8").then(JSON.parse),
    ]);
  } catch (error) {
    fail("skill_plugin_unreadable", `approved skill plugin is unreadable: ${error?.message || error}`);
  }
  if (approvedManifest?.schema_version !== 1 || !Array.isArray(approvedManifest.skills)) {
    fail("skill_manifest_schema", "approved-skills.json must use schema_version 1 and contain skills[]");
  }
  assertPluginManifest(pluginMetadata, approvedManifest);
  const pluginName = cleanString(approvedManifest.plugin?.name);
  const discoveryRoots = approvedManifest.discovery_roots.map(normalizeDiscoveryRoot);
  const skills = approvedManifest.skills.map((entry) => normalizeManifestSkill(entry, pluginRoot));
  if (skills.length === 0) fail("skill_manifest_empty", "approved skill manifest must not be empty");
  const names = new Set();
  const skillFiles = new Set();
  for (const skill of skills) {
    if (names.has(skill.name)) fail("skill_manifest_duplicate", `duplicate approved skill ${skill.name}`);
    if (skillFiles.has(skill.runtimeSkillFile)) {
      fail("skill_manifest_duplicate", `multiple approved skills own ${skill.runtimeSkillFile}`);
    }
    names.add(skill.name);
    skillFiles.add(skill.runtimeSkillFile);
    if (!discoveryRoots.some((root) => skill.sourcePath === root || skill.sourcePath.startsWith(`${root}/`))) {
      fail(
        "skill_plugin_discovery_roots",
        `approved skill ${skill.name} is outside the declared plugin discovery roots`
      );
    }
    const [body, actualDigest] = await Promise.all([
      fs.readFile(skill.absoluteSkillFile, "utf8"),
      digestSkillDirectory(skill.runtimeDirectory),
    ]);
    const frontmatterName = skillNameFromFrontmatter(body);
    if (frontmatterName !== skill.name) {
      fail(
        "skill_frontmatter_name",
        `${skill.runtimeSkillFile} declares ${JSON.stringify(frontmatterName)}, expected ${JSON.stringify(skill.name)}`
      );
    }
    if (actualDigest !== skill.contentDigest) {
      fail("skill_digest_mismatch", `approved skill digest mismatch for ${skill.name}`);
    }
  }
  const discoveredSkillFiles = (await walkTree(pluginRoot))
    .map((entry) => entry.relative)
    .filter((relative) => path.posix.basename(relative) === "SKILL.md");
  const extras = discoveredSkillFiles.filter((relative) => !skillFiles.has(relative));
  const missing = [...skillFiles].filter((relative) => !discoveredSkillFiles.includes(relative));
  if (extras.length || missing.length) {
    fail(
      "skill_manifest_coverage",
      `skill manifest coverage mismatch; extra=${extras.join(",") || "none"}; missing=${missing.join(",") || "none"}`
    );
  }
  for (const prohibited of ["agents", "commands", "hooks", ".mcp.json"]) {
    try {
      await fs.lstat(path.join(pluginRoot, prohibited));
      fail("skill_plugin_capability", `skills-only plugin contains forbidden ${prohibited}`);
    } catch (error) {
      if (error instanceof PrimaryRuntimeError) throw error;
      if (error?.code !== "ENOENT") throw error;
    }
  }
  // The source checkout is intentionally writable. Callers must pass the separately published,
  // content-addressed runtime copy after it has been made read-only; this check prevents the SDK
  // subprocess from mutating its own skill policy.
  await assertReadOnlyPlugin(pluginRoot, approvedManifestPath);
  return Object.freeze({
    pluginPath: pluginRoot,
    manifestPath: approvedManifestPath,
    pluginName,
    pluginVersion: cleanString(approvedManifest.plugin?.version),
    skillNames: Object.freeze(skills.map((skill) => skill.name)),
    qualifiedSkillNames: Object.freeze(skills.map((skill) => `${pluginName}:${skill.name}`)),
    skills: Object.freeze(skills.map((skill) => Object.freeze({
      name: skill.name,
      source_path: skill.sourcePath,
      plugin_path: skill.pluginPath,
      skill_file: skill.sourceSkillFile,
      content_digest: skill.contentDigest,
      description: skill.description,
      allowed_modes: Object.freeze([...skill.allowedModes]),
    }))),
  });
}

export function resolveSafeboxBrokerConfiguration({
  sourceEnv = process.env,
  requestedModel = "",
  expectedModel = PRIMARY_AGENT_MODEL,
} = {}) {
  if (expectedModel !== PRIMARY_AGENT_MODEL) {
    fail("model_pin_conflict", `primary runtime model policy is fixed to ${PRIMARY_AGENT_MODEL}`);
  }
  const baseUrl = cleanString(sourceEnv.ANTHROPIC_BASE_URL);
  const capabilityToken = cleanString(sourceEnv.ANTHROPIC_API_KEY);
  const pinnedModel = cleanString(sourceEnv.TAKYON_CLAUDE_AGENT_MODEL);
  const requested = cleanString(requestedModel);
  if (!baseUrl) fail("broker_configuration", "ANTHROPIC_BASE_URL must point at the Safebox broker");
  if (!/^https?:\/\//i.test(baseUrl)) {
    fail("broker_configuration", "ANTHROPIC_BASE_URL must be an HTTP(S) Safebox broker URL");
  }
  if (!capabilityToken) {
    fail("broker_configuration", "ANTHROPIC_API_KEY must contain a Safebox-minted operator capability");
  }
  if (!pinnedModel) fail("model_pin_missing", "TAKYON_CLAUDE_AGENT_MODEL is required");
  if (expectedModel && pinnedModel !== expectedModel) {
    fail("model_pin_conflict", `primary runtime requires model ${expectedModel}; found ${pinnedModel}`);
  }
  if (requested && requested !== pinnedModel) {
    fail("model_override", `requested model ${requested} conflicts with deployment pin ${pinnedModel}`);
  }
  for (const name of MODEL_ALIASES) {
    const value = cleanString(sourceEnv[name]);
    if (value && value !== pinnedModel) {
      fail("model_alias_conflict", `${name}=${value} conflicts with deployment pin ${pinnedModel}`);
    }
  }
  return Object.freeze({ baseUrl, capabilityToken, model: pinnedModel });
}

export function buildPrimaryRuntimeEnvironment({
  broker,
  configDir,
  cwd,
  sourceEnv = process.env,
  failOnApiRetry = true,
} = {}) {
  if (!broker?.baseUrl || !broker?.capabilityToken || !broker?.model) {
    fail("broker_configuration", "validated Safebox broker configuration is required");
  }
  const environment = {
    PATH: cleanString(sourceEnv.PATH) || SANDBOX_PATH,
    HOME: cleanString(sourceEnv.HOME) || "/tmp",
    ANTHROPIC_API_KEY: broker.capabilityToken,
    ANTHROPIC_BASE_URL: broker.baseUrl,
    ANTHROPIC_MODEL: broker.model,
    ANTHROPIC_DEFAULT_OPUS_MODEL: broker.model,
    ANTHROPIC_DEFAULT_SONNET_MODEL: broker.model,
    ANTHROPIC_DEFAULT_HAIKU_MODEL: broker.model,
    CLAUDE_CODE_SUBAGENT_MODEL: broker.model,
    CLAUDE_AGENT_SDK_CLIENT_APP: "takyon-primary-agent",
    CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS: "1",
    CLAUDE_CONFIG_DIR: path.resolve(configDir),
    ...(failOnApiRetry
      ? {
          CLAUDE_CODE_MAX_RETRIES: "0",
          ANTHROPIC_CUSTOM_HEADERS: "x-takyon-fail-on-api-retry: 1",
        }
      : {}),
  };
  for (const key of ["LANG", "LC_ALL", "SHELL", "TERM", "TMPDIR", "TMP", "TEMP", "USER"]) {
    const value = cleanString(sourceEnv[key]);
    if (value) environment[key] = value;
  }
  if (cleanString(sourceEnv.TAKYON_CLAUDE_AGENT_IN_DOCKER) === "1") {
    environment.TERMINAL_ENV = "local";
    environment.TERMINAL_CWD = path.resolve(cwd);
    environment.TERMINAL_DOCKER_MOUNT_CWD_TO_WORKSPACE = "0";
  }
  return environment;
}

function sandboxedBashCommand(command) {
  return `/usr/bin/env -i PATH=${SANDBOX_PATH} HOME=/tmp /bin/bash -lc ${JSON.stringify(String(command || ""))}`;
}

function rewriteWorkspacePrefixedPaths(input, workspace) {
  if (!input || typeof input !== "object" || Array.isArray(input)) return input;
  const prefix = normalizeRelative(workspace);
  if (!prefix || prefix === ".") return input;
  const updated = { ...input };
  let changed = false;
  for (const key of PATH_KEYS) {
    const value = updated[key];
    if (typeof value !== "string" || path.isAbsolute(value)) continue;
    const normalized = normalizeRelative(value);
    if (normalized === prefix) {
      updated[key] = ".";
      changed = true;
    } else if (normalized.startsWith(`${prefix}/`)) {
      updated[key] = normalized.slice(prefix.length + 1) || ".";
      changed = true;
    }
  }
  return changed ? updated : input;
}

function pathValues(input) {
  if (!input || typeof input !== "object" || Array.isArray(input)) return [];
  return PATH_KEYS.map((key) => input[key]).filter((value) => typeof value === "string" && value);
}

function toolNameAllowed(toolName, localTools, allowedMcpTools) {
  return localTools.has(toolName) || allowedMcpTools.has(toolName);
}

export function createPrimaryToolGuard({
  cwd,
  workspaceRoot,
  workspace = ".",
  localTools = DEFAULT_LOCAL_TOOLS,
  allowedMcpTools = [],
  allowBash = false,
  externalSandboxed = false,
  mode = "interactive",
  pluginName = "",
  approvedSkills = [],
} = {}) {
  const resolvedCwd = path.resolve(cwd);
  const resolvedRoot = path.resolve(workspaceRoot || cwd);
  ensureSubpath(resolvedRoot, resolvedCwd, "runtime cwd");
  const local = new Set(localTools);
  const mcp = new Set(allowedMcpTools);
  const invocationMode = cleanString(mode);
  if (!INVOCATION_MODES.has(invocationMode)) {
    fail("invocation_mode", `unsupported primary runtime mode ${JSON.stringify(invocationMode)}`);
  }
  const approvedSkillModes = new Map(
    approvedSkills.map((skill) => [cleanString(skill?.name), new Set(skill?.allowed_modes || [])])
  );
  for (const name of [...local, ...mcp]) {
    if (AGENT_TOOLS.has(name)) fail("agent_tool_forbidden", `model-agent tool ${name} is forbidden`);
  }
  if (local.has("Bash") && !allowBash) {
    fail("bash_configuration", "Bash cannot be exposed unless allowBash is true");
  }
  if ((local.has("Bash") || allowBash) && !externalSandboxed) {
    fail("bash_configuration", "Bash requires an externally enforced sandbox");
  }
  async function decide(toolName, toolInput = {}) {
    if (AGENT_TOOLS.has(toolName)) {
      return { allowed: false, reason: `model-agent tool ${toolName} is disabled` };
    }
    if (!toolNameAllowed(toolName, local, mcp)) {
      return { allowed: false, reason: `tool ${toolName} is outside this invocation's capability set` };
    }
    if (toolName === "Skill") {
      const requested = cleanString(toolInput?.skill || toolInput?.name || toolInput?.command);
      const prefix = `${cleanString(pluginName)}:`;
      const canonical = requested.startsWith(prefix) ? requested.slice(prefix.length) : requested;
      const allowedModes = approvedSkillModes.get(canonical);
      if (!requested || !allowedModes || (requested.includes(":") && !requested.startsWith(prefix))) {
        return { allowed: false, reason: `skill ${requested || "<empty>"} is not in the approved manifest` };
      }
      if (!allowedModes.has(invocationMode)) {
        return {
          allowed: false,
          reason: `skill ${canonical} is not allowed during ${invocationMode} invocations`,
        };
      }
    }
    let updatedInput = rewriteWorkspacePrefixedPaths(toolInput, workspace);
    if (toolName === "Bash") {
      if (!allowBash) return { allowed: false, reason: "Bash is disabled for this invocation" };
      updatedInput = { ...(updatedInput || {}) };
      if (typeof updatedInput.command === "string") {
        updatedInput.command = sandboxedBashCommand(updatedInput.command);
      } else if (typeof updatedInput.cmd === "string") {
        updatedInput.cmd = sandboxedBashCommand(updatedInput.cmd);
      } else {
        return { allowed: false, reason: "Bash request omitted command" };
      }
    }
    const outside = pathValues(updatedInput).find((value) => {
      const absolute = path.isAbsolute(value) ? path.resolve(value) : path.resolve(resolvedCwd, value);
      return !isSubpath(resolvedRoot, absolute);
    });
    if (outside) {
      return { allowed: false, reason: "filesystem access is limited to the scoped workspace" };
    }
    return { allowed: true, updatedInput };
  }
  return {
    localTools: Object.freeze([...local]),
    allowedMcpTools: Object.freeze([...mcp]),
    decide,
    preToolUse: async (input) => {
      const decision = await decide(cleanString(input?.tool_name), input?.tool_input || {});
      return {
        hookSpecificOutput: {
          hookEventName: "PreToolUse",
          permissionDecision: decision.allowed ? "allow" : "deny",
          permissionDecisionReason: decision.allowed ? "Takyon scoped capability" : decision.reason,
          ...(decision.allowed ? { updatedInput: decision.updatedInput } : {}),
        },
      };
    },
    canUseTool: async (toolName, toolInput, options = {}) => {
      const decision = await decide(toolName, toolInput);
      if (!decision.allowed) {
        return { behavior: "deny", message: decision.reason, toolUseID: options.toolUseID };
      }
      return { behavior: "allow", updatedInput: decision.updatedInput, toolUseID: options.toolUseID };
    },
  };
}

function textFromAssistant(message) {
  if (message?.type !== "assistant" || !Array.isArray(message.message?.content)) return "";
  return message.message.content
    .filter((part) => part?.type === "text" && typeof part.text === "string")
    .map((part) => part.text)
    .join("\n");
}

function textDeltaFromAssistant(message) {
  if (message?.type !== "stream_event") return "";
  const event = message.event;
  if (event?.type !== "content_block_delta" || event.delta?.type !== "text_delta") return "";
  return typeof event.delta.text === "string" ? event.delta.text : "";
}

function sdkUserMessage(text, priority = "now") {
  return {
    type: "user",
    message: { role: "user", content: String(text) },
    parent_tool_use_id: null,
    priority,
  };
}

export function createPrimaryStreamingInput(initialPrompt) {
  const initial = cleanString(initialPrompt);
  if (!initial) fail("prompt_missing", "primary streaming input requires an initial prompt");
  const queued = [sdkUserMessage(initial)];
  const waiters = [];
  let closed = false;
  const wake = () => {
    while (waiters.length && (queued.length || closed)) waiters.shift()();
  };
  const messages = Object.freeze({
    [Symbol.asyncIterator]() {
      return {
        async next() {
          while (!queued.length && !closed) {
            await new Promise((resolve) => waiters.push(resolve));
          }
          if (queued.length) return { value: queued.shift(), done: false };
          return { value: undefined, done: true };
        },
      };
    },
  });
  return Object.freeze({
    messages,
    steer(text) {
      const value = String(text || "");
      if (closed || !value.trim()) return false;
      if (Buffer.byteLength(value, "utf8") > 32 * 1024) {
        fail("steer_payload", "mid-turn steering text exceeds 32768 bytes");
      }
      queued.push(sdkUserMessage(value, "now"));
      wake();
      return true;
    },
    close() {
      closed = true;
      wake();
    },
  });
}

function toolUses(message) {
  if (message?.type !== "assistant" || !Array.isArray(message.message?.content)) return [];
  return message.message.content.filter((part) => part?.type === "tool_use");
}

function toolResults(message) {
  if (message?.type !== "user" || !Array.isArray(message.message?.content)) return [];
  return message.message.content.filter((part) => part?.type === "tool_result");
}

export function createProgressProjector({ epoch, onProgress = async () => {}, now = () => Date.now() } = {}) {
  const epochName = cleanString(epoch) || "interactive";
  let sequence = 0;
  let lastSignature = "";
  const pendingTools = new Map();
  const lastToolPulse = new Map();
  async function emit(event) {
    const eventKind = cleanString(event.kind) || "runtime";
    const detail = eventKind === "assistant"
      ? event.status === "delta"
        ? redacted(String(event.detail || event.line || ""))
        : redacted(String(event.detail || event.line || "")).trim()
      : compact(event.detail || event.line, 240);
    const body = {
      version: PRIMARY_RUNTIME_EVENT_VERSION,
      source: PRIMARY_RUNTIME_SOURCE,
      epoch: epochName,
      kind: eventKind,
      status: cleanString(event.status) || "running",
      detail,
      ...(event.trace && typeof event.trace === "object"
        ? {
            trace: Object.fromEntries(
              Object.entries(event.trace)
                .map(([key, value]) => [key, compact(value, key === "entry_key" ? 120 : 160)])
                .filter(([, value]) => value)
            ),
          }
        : {}),
    };
    if (!body.detail && !body.trace) return null;
    const signature = JSON.stringify(body);
    if (event.status !== "delta" && signature === lastSignature) return null;
    lastSignature = signature;
    const payload = { ...body, sequence: ++sequence, timestamp_ms: now() };
    await onProgress(payload);
    return payload;
  }
  async function project(message) {
    if (!message || typeof message !== "object") return;
    const assistantDelta = textDeltaFromAssistant(message);
    if (assistantDelta) {
      await emit({
        kind: "assistant",
        status: "delta",
        detail: assistantDelta,
        trace: {
          entry_key: `assistant:${cleanString(message.uuid) || "stream"}`,
          status: "running",
        },
      });
      return;
    }
    if (message.type === "system" && message.subtype === "api_retry") {
      await emit({
        kind: "provider",
        status: "failed",
        detail: `Provider retry refused: ${message.error_status ? `HTTP ${message.error_status}` : "connection error"}.`,
      });
      return;
    }
    if (message.type === "system" && message.subtype === "mirror_error") {
      await emit({ kind: "session", status: "failed", detail: "Durable session transcript mirroring failed." });
      return;
    }
    if (message.type === "system" && message.subtype === "compact_boundary") {
      await emit({
        kind: "session",
        status: "compacted",
        detail: "Durable agent context compacted.",
        trace: {
          entry_key: `compact:${cleanString(message.uuid) || randomUUID()}`,
          trigger: cleanString(message.compact_metadata?.trigger),
          pre_tokens: Number(message.compact_metadata?.pre_tokens || 0),
          post_tokens: Number(message.compact_metadata?.post_tokens || 0),
        },
      });
      return;
    }
    if (message.type === "tool_progress") {
      const id = cleanString(message.tool_use_id || message.uuid || message.tool_name);
      const seconds = Math.max(0, Math.round(Number(message.elapsed_time_seconds || 0)));
      if (seconds === 0 || seconds - (lastToolPulse.get(id) || 0) >= 10) {
        lastToolPulse.set(id, seconds);
        await emit({
          kind: "tool",
          status: "running",
          detail: `${compact(message.tool_name || "Tool", 80)} running${seconds ? ` · ${seconds}s` : ""}`,
          trace: { entry_key: `tool:${id}`, tool_name: message.tool_name, status: "running" },
        });
      }
      return;
    }
    const assistantText = textFromAssistant(message);
    if (assistantText) {
      const assistantToolUses = toolUses(message);
      await emit({
        kind: "assistant",
        status: "output",
        detail: assistantText,
        trace: {
          entry_key: `assistant:${cleanString(message.uuid) || randomUUID()}`,
          status: "completed",
          message_role: assistantToolUses.length ? "interim" : "final",
        },
      });
    }
    for (const block of toolUses(message)) {
      const id = cleanString(block.id) || randomUUID();
      const name = cleanString(block.name) || "Tool";
      const skillName = name === "Skill"
        ? cleanString(block.input?.skill || block.input?.name || block.input?.command)
        : "";
      pendingTools.set(id, { name, skillName });
      await emit({
        kind: skillName ? "skill" : "tool",
        status: "started",
        detail: skillName ? `Using ${skillName}.` : `${name} started.`,
        trace: { entry_key: `${skillName ? "skill" : "tool"}:${id}`, tool_name: name, skill_name: skillName },
      });
    }
    for (const block of toolResults(message)) {
      const id = cleanString(block.tool_use_id);
      const pending = pendingTools.get(id);
      if (!pending) continue;
      pendingTools.delete(id);
      await emit({
        kind: pending.skillName ? "skill" : "tool",
        status: block.is_error === true ? "failed" : "completed",
        detail: pending.skillName
          ? `${pending.skillName} ${block.is_error === true ? "failed" : "completed"}.`
          : `${pending.name} ${block.is_error === true ? "failed" : "completed"}.`,
        trace: {
          entry_key: `${pending.skillName ? "skill" : "tool"}:${id}`,
          tool_name: pending.name,
          skill_name: pending.skillName,
        },
      });
    }
    if (message.type === "result") {
      await emit({
        kind: "turn",
        status: message.subtype === "success" && message.is_error !== true ? "completed" : "failed",
        detail: message.subtype === "success" && message.is_error !== true
          ? "Agent turn completed."
          : "Agent turn failed.",
      });
    }
  }
  return Object.freeze({ emit, project });
}

export function createJsonLineProgressSink({ stream = process.stderr, prefix = SDK_PROGRESS_PREFIX } = {}) {
  if (!stream || typeof stream.write !== "function") {
    fail("progress_sink", "progress stream must expose write()");
  }
  return async (event) => {
    const line = `${String(prefix)}${JSON.stringify(event)}\n`;
    if (stream.write(line) !== false) return;
    await new Promise((resolve, reject) => {
      stream.once("drain", resolve);
      stream.once("error", reject);
    });
  };
}

function jsonPayloadBytes(value, label) {
  try {
    return Buffer.byteLength(JSON.stringify(value), "utf8");
  } catch (error) {
    fail("session_store_payload", `${label} is not JSON-serializable: ${error?.message || error}`);
  }
}

export function createBoundedSessionStore(sessionStore, {
  maxAppendBytes = SESSION_APPEND_MAX_BYTES,
  maxLoadBytes = SESSION_LOAD_MAX_BYTES,
  maxAppendEntries = SESSION_APPEND_MAX_ENTRIES,
  maxLoadEntries = SESSION_LOAD_MAX_ENTRIES,
} = {}) {
  if (
    !sessionStore
    || typeof sessionStore.append !== "function"
    || typeof sessionStore.load !== "function"
  ) {
    fail("session_store_configuration", "SessionStore must expose append() and load()");
  }
  const bounded = {
    async append(key, entries) {
      if (!Array.isArray(entries) || entries.length > maxAppendEntries) {
        fail("session_store_payload", `SessionStore append exceeds ${maxAppendEntries} entries`);
      }
      if (jsonPayloadBytes(entries, "SessionStore append") > maxAppendBytes) {
        fail("session_store_payload", `SessionStore append exceeds ${maxAppendBytes} bytes`);
      }
      return sessionStore.append(key, entries);
    },
    async load(key) {
      const entries = await sessionStore.load(key);
      if (entries === null) return null;
      if (!Array.isArray(entries) || entries.length > maxLoadEntries) {
        fail("session_store_payload", `SessionStore load exceeds ${maxLoadEntries} entries`);
      }
      if (jsonPayloadBytes(entries, "SessionStore load") > maxLoadBytes) {
        fail("session_store_payload", `SessionStore load exceeds ${maxLoadBytes} bytes`);
      }
      return entries;
    },
  };
  if (typeof sessionStore.listSubkeys === "function") {
    bounded.listSubkeys = async (key) => {
      const subkeys = await sessionStore.listSubkeys(key);
      if (
        !Array.isArray(subkeys)
        || subkeys.length > 10_000
        || jsonPayloadBytes(subkeys, "SessionStore subkeys") > 1024 * 1024
        || subkeys.some((value) => {
          const normalized = cleanString(value).replace(/\\/g, "/");
          return !normalized || normalized.startsWith("/") || normalized.split("/").includes("..");
        })
      ) {
        fail("session_store_payload", "SessionStore returned unsafe subkeys");
      }
      return subkeys;
    };
  }
  for (const name of ["listSessions", "listSessionSummaries", "delete"]) {
    if (typeof sessionStore[name] === "function") {
      bounded[name] = (...args) => sessionStore[name](...args);
    }
  }
  return Object.freeze(bounded);
}

function canonicalDiscoveredSkill(discoveredName, pluginName, expectedNames) {
  const value = cleanString(discoveredName);
  if (value.startsWith(`${pluginName}:`)) return value.slice(pluginName.length + 1);
  if (expectedNames.has(value)) return value;
  return "";
}

function verifySdkInitialization(message, { plugin, tools, allowedMcpTools, model, resumeSessionId }) {
  if (message?.type !== "system" || message.subtype !== "init") return null;
  const sessionId = cleanString(message.session_id);
  if (!UUID_PATTERN.test(sessionId)) fail("session_id_invalid", "SDK returned an invalid session ID");
  if (resumeSessionId && sessionId !== resumeSessionId) {
    fail("session_resume_mismatch", `SDK resumed ${sessionId}, expected ${resumeSessionId}`);
  }
  if (cleanString(message.model) !== model) {
    fail("actual_model_mismatch", `SDK initialized ${message.model || "no model"}, expected ${model}`);
  }
  const initializedTools = new Set(Array.isArray(message.tools) ? message.tools.map(cleanString) : []);
  for (const forbidden of AGENT_TOOLS) {
    if (initializedTools.has(forbidden)) {
      fail("agent_tool_exposed", `SDK exposed forbidden model-agent tool ${forbidden}`);
    }
  }
  const configuredTools = new Set([...tools, ...allowedMcpTools]);
  for (const initializedTool of initializedTools) {
    if (!configuredTools.has(initializedTool)) {
      fail("sdk_tool_set_mismatch", `SDK exposed unconfigured tool ${initializedTool}`);
    }
  }
  for (const required of tools) {
    if (!initializedTools.has(required)) fail("sdk_tool_missing", `SDK omitted configured tool ${required}`);
  }
  const expectedSkills = new Set(plugin.skillNames);
  const discovered = Array.isArray(message.skills) ? message.skills : [];
  const canonical = discovered.map((name) => canonicalDiscoveredSkill(name, plugin.pluginName, expectedSkills));
  if (canonical.some((name) => !name) || new Set(canonical).size !== expectedSkills.size) {
    fail("sdk_skill_set_mismatch", "SDK discovered a skill set outside the approved manifest");
  }
  for (const expected of expectedSkills) {
    if (!canonical.includes(expected)) fail("sdk_skill_set_mismatch", `SDK did not discover approved skill ${expected}`);
  }
  const initializedPlugins = Array.isArray(message.plugins) ? message.plugins : [];
  if (initializedPlugins.length !== 1) {
    fail("sdk_plugin_set_mismatch", "SDK must initialize exactly one approved skills plugin");
  }
  const initializedPlugin = initializedPlugins[0] || {};
  if (
    cleanString(initializedPlugin.name) !== plugin.pluginName
    || path.resolve(cleanString(initializedPlugin.path)) !== plugin.pluginPath
  ) {
    fail("sdk_plugin_set_mismatch", "SDK initialized an unexpected plugin");
  }
  return sessionId;
}

function retryFailure(message) {
  if (message?.type !== "system" || message.subtype !== "api_retry") return "";
  const status = Number.isInteger(message.error_status) ? `HTTP ${message.error_status}` : "connection error";
  return `provider retry refused by fail-fast policy (${status})`;
}

function mirrorFailure(message) {
  if (message?.type !== "system" || message.subtype !== "mirror_error") return "";
  return `durable session mirror failed: ${compact(message.error || "unknown", 160)}`;
}

export async function buildPrimaryRuntimeOptions({
  prompt,
  systemPrompt,
  cwd,
  workspaceRoot,
  workspace = ".",
  configDir,
  pluginPath,
  manifestPath,
  mode = "interactive",
  operation = "turn",
  epoch = mode,
  sessionId = "",
  resumeSessionId = "",
  sessionStore = undefined,
  sessionStoreFlush = "batched",
  sourceEnv = process.env,
  requestedModel = "",
  expectedModel = PRIMARY_AGENT_MODEL,
  localTools = DEFAULT_LOCAL_TOOLS,
  allowedMcpTools = [],
  mcpServers = {},
  allowBash = false,
  externalSandboxed = false,
  maxTurns = 24,
  maxBudgetUsd = 8,
  effort = "high",
  failOnApiRetry = true,
  pathToClaudeCodeExecutable = "",
  abortController = new AbortController(),
  onProgress = async () => {},
} = {}) {
  const userPrompt = cleanString(prompt);
  const stableSystemPrompt = cleanString(systemPrompt);
  const invocationMode = cleanString(mode);
  const runtimeOperation = cleanString(operation) || "turn";
  if (!userPrompt) fail("prompt_missing", "primary runtime prompt is required");
  if (!stableSystemPrompt) fail("system_prompt_missing", "primary runtime system policy is required");
  if (!INVOCATION_MODES.has(invocationMode)) {
    fail("invocation_mode", `unsupported primary runtime mode ${JSON.stringify(invocationMode)}`);
  }
  if (!["turn", "compact"].includes(runtimeOperation)) {
    fail("runtime_operation", `unsupported primary runtime operation ${JSON.stringify(runtimeOperation)}`);
  }
  if (failOnApiRetry !== true) {
    fail("provider_retry_policy", "primary runtime provider retries must remain fail-fast");
  }
  const resolvedCwd = path.resolve(cleanString(cwd));
  const resolvedRoot = path.resolve(cleanString(workspaceRoot || cwd));
  const resolvedConfigDir = path.resolve(cleanString(configDir));
  if (!cleanString(cwd) || !cleanString(configDir)) {
    fail("runtime_path_missing", "cwd and configDir are required");
  }
  ensureSubpath(resolvedRoot, resolvedCwd, "runtime cwd");
  const newSessionId = cleanString(sessionId);
  const resumedSessionId = cleanString(resumeSessionId);
  if (newSessionId && resumedSessionId) {
    fail("session_configuration", "sessionId and resumeSessionId are mutually exclusive");
  }
  if (runtimeOperation === "compact" && !resumedSessionId) {
    fail("session_configuration", "manual compaction requires an exact resumed session");
  }
  if (
    runtimeOperation === "compact"
    && !/^\/compact(?: [^\r\n\0]{1,500})?$/u.test(userPrompt)
  ) {
    fail("compact_prompt", "manual compaction prompt must be canonical /compact [focus]");
  }
  for (const [label, value] of [["sessionId", newSessionId], ["resumeSessionId", resumedSessionId]]) {
    if (value && !UUID_PATTERN.test(value)) fail("session_configuration", `${label} must be a UUID`);
  }
  const plugin = await verifyApprovedSkillPlugin({
    pluginPath,
    manifestPath,
  });
  const broker = resolveSafeboxBrokerConfiguration({ sourceEnv, requestedModel, expectedModel });
  const configuredTools = runtimeOperation === "compact" ? [] : [...new Set(localTools)];
  if (runtimeOperation !== "compact" && !configuredTools.includes("Skill")) {
    configuredTools.push("Skill");
  }
  if (runtimeOperation !== "compact" && allowBash && !configuredTools.includes("Bash")) {
    configuredTools.push("Bash");
  }
  const effectiveMcpTools = runtimeOperation === "compact" ? [] : allowedMcpTools;
  const guard = createPrimaryToolGuard({
    cwd: resolvedCwd,
    workspaceRoot: resolvedRoot,
    workspace,
    localTools: configuredTools,
    allowedMcpTools: effectiveMcpTools,
    allowBash: runtimeOperation === "compact" ? false : allowBash,
    externalSandboxed,
    mode: invocationMode,
    pluginName: plugin.pluginName,
    approvedSkills: plugin.skills,
  });
  const projector = createProgressProjector({ epoch, onProgress });
  const boundedSessionStore = sessionStore ? createBoundedSessionStore(sessionStore) : undefined;
  const options = {
    abortController,
    cwd: resolvedCwd,
    env: buildPrimaryRuntimeEnvironment({
      broker,
      configDir: resolvedConfigDir,
      cwd: resolvedCwd,
      sourceEnv,
      failOnApiRetry,
    }),
    model: broker.model,
    title: `Takyon ${invocationMode}${runtimeOperation === "compact" ? " compact" : ""}`,
    systemPrompt: stableSystemPrompt,
    settingSources: [],
    settings: { autoMemoryEnabled: false, autoDreamEnabled: false },
    plugins: [{ type: "local", path: plugin.pluginPath }],
    skills: "all",
    tools: [...guard.localTools],
    allowedTools: [
      ...guard.localTools.filter((name) => name !== "Skill"),
      ...guard.allowedMcpTools,
    ],
    disallowedTools: [...AGENT_TOOLS],
    strictMcpConfig: true,
    mcpServers: runtimeOperation === "compact" ? {} : mcpServers,
    permissionMode: "dontAsk",
    hooks: { PreToolUse: [{ hooks: [guard.preToolUse] }] },
    canUseTool: guard.canUseTool,
    includePartialMessages: true,
    includeHookEvents: true,
    thinking: { type: "adaptive", display: "summarized" },
    effort: ["low", "medium", "high"].includes(effort) ? effort : "high",
    persistSession: true,
    ...(boundedSessionStore ? { sessionStore: boundedSessionStore, sessionStoreFlush } : {}),
    ...(newSessionId ? { sessionId: newSessionId } : {}),
    ...(resumedSessionId ? { resume: resumedSessionId } : {}),
    maxTurns: Number.isInteger(maxTurns) && maxTurns > 0 ? maxTurns : 24,
    maxBudgetUsd: Number.isFinite(maxBudgetUsd) && maxBudgetUsd > 0 ? maxBudgetUsd : 8,
    ...(cleanString(pathToClaudeCodeExecutable)
      ? { pathToClaudeCodeExecutable: cleanString(pathToClaudeCodeExecutable) }
      : {}),
  };
  return Object.freeze({
    prompt: userPrompt,
    operation: runtimeOperation,
    options,
    plugin,
    broker,
    guard,
    projector,
    resumeSessionId: resumedSessionId,
  });
}

export async function runPrimaryAgentTurn(configuration, { sdk = null } = {}) {
  const prepared = await buildPrimaryRuntimeOptions(configuration);
  const sdkModule = sdk || await loadSdkModule(configuration.sourceEnv || process.env);
  if (typeof sdkModule.query !== "function") fail("sdk_unavailable", "Claude Agent SDK query() is unavailable");
  const streamingInput = configuration.streamingInput || null;
  if (streamingInput && typeof streamingInput.messages?.[Symbol.asyncIterator] !== "function") {
    fail("streaming_input", "primary streaming input is invalid");
  }
  const queryInstance = sdkModule.query({
    prompt: streamingInput ? streamingInput.messages : prepared.prompt,
    options: prepared.options,
  });
  let initialized = false;
  let resolvedSessionId = "";
  let resultMessage = null;
  let lastAssistantText = "";
  let compactReceipt = null;
  const attemptedSkills = new Set();
  const invokedSkills = new Set();
  const pendingSkillUses = new Map();
  const actualModels = new Set();
  try {
    for await (const message of queryInstance) {
    await prepared.projector.project(message);
    const retryError = retryFailure(message);
    if (retryError) {
      prepared.options.abortController.abort();
      fail("provider_retry_refused", retryError);
    }
    const sessionMirrorError = mirrorFailure(message);
    if (sessionMirrorError) {
      prepared.options.abortController.abort();
      fail("session_mirror_failed", sessionMirrorError);
    }
    if (message?.type === "system" && ["task_started", "task_progress", "task_updated"].includes(message.subtype)) {
      prepared.options.abortController.abort();
      fail("subagent_event", "SDK emitted a model-subagent event while Agent tools were disabled");
    }
    if (message?.type === "system" && message.subtype === "init") {
      if (initialized) fail("sdk_duplicate_init", "SDK emitted multiple initialization messages");
      resolvedSessionId = verifySdkInitialization(message, {
        plugin: prepared.plugin,
        tools: prepared.guard.localTools,
        allowedMcpTools: prepared.guard.allowedMcpTools,
        model: prepared.broker.model,
        resumeSessionId: prepared.resumeSessionId,
      });
      initialized = true;
      await prepared.projector.emit({
        kind: "session",
        status: prepared.resumeSessionId ? "resumed" : "started",
        detail: prepared.resumeSessionId ? "Agent session resumed." : "Agent session started.",
        trace: { entry_key: `session:${resolvedSessionId}`, session_id: resolvedSessionId },
      });
      if (typeof configuration.onSession === "function") {
        await configuration.onSession({
          sessionId: resolvedSessionId,
          resumed: Boolean(prepared.resumeSessionId),
          requestedSessionId: prepared.resumeSessionId || cleanString(configuration.sessionId) || null,
        });
      }
    }
    if (message?.type === "system" && message.subtype === "compact_boundary") {
      if (prepared.operation === "compact") {
        if (!initialized || cleanString(message.session_id) !== resolvedSessionId) {
          fail("compact_boundary_session", "manual compaction boundary escaped the resumed session");
        }
        if (compactReceipt) fail("compact_boundary_duplicate", "manual compaction emitted multiple boundaries");
        const metadata = message.compact_metadata || {};
        if (cleanString(metadata.trigger) !== "manual") {
          fail("compact_boundary_trigger", "manual compaction did not emit a manual boundary");
        }
        const preTokens = Number(metadata.pre_tokens);
        const postTokens = metadata.post_tokens == null ? null : Number(metadata.post_tokens);
        const durationMs = metadata.duration_ms == null ? null : Number(metadata.duration_ms);
        if (!Number.isInteger(preTokens) || preTokens < 0) {
          fail("compact_boundary_receipt", "manual compaction boundary has invalid pre_tokens");
        }
        if (postTokens != null && (!Number.isInteger(postTokens) || postTokens < 0)) {
          fail("compact_boundary_receipt", "manual compaction boundary has invalid post_tokens");
        }
        if (durationMs != null && (!Number.isFinite(durationMs) || durationMs < 0)) {
          fail("compact_boundary_receipt", "manual compaction boundary has invalid duration_ms");
        }
        const boundaryUuid = cleanString(message.uuid);
        if (!UUID_PATTERN.test(boundaryUuid)) {
          fail("compact_boundary_receipt", "manual compaction boundary has an invalid UUID");
        }
        compactReceipt = Object.freeze({
          uuid: boundaryUuid,
          trigger: "manual",
          pre_tokens: preTokens,
          post_tokens: postTokens,
          duration_ms: durationMs,
        });
      }
    }
    const reportedModel = cleanString(message?.message?.model || message?.model);
    if (reportedModel) {
      actualModels.add(reportedModel);
      if (reportedModel !== prepared.broker.model) {
        prepared.options.abortController.abort();
        fail("actual_model_mismatch", `SDK used model ${reportedModel}; expected ${prepared.broker.model}`);
      }
    }
    const observedToolUses = toolUses(message);
    if (prepared.operation === "compact" && observedToolUses.length > 0) {
      prepared.options.abortController.abort();
      fail("compact_tool_forbidden", "manual compaction attempted to invoke a tool");
    }
    for (const block of observedToolUses) {
      if (cleanString(block.name) !== "Skill") continue;
      const requested = cleanString(block.input?.skill || block.input?.name || block.input?.command);
      if (requested) {
        attemptedSkills.add(requested);
        if (cleanString(block.id)) pendingSkillUses.set(cleanString(block.id), requested);
      }
    }
    for (const block of toolResults(message)) {
      const toolUseId = cleanString(block.tool_use_id);
      const requested = pendingSkillUses.get(toolUseId);
      if (!requested) continue;
      pendingSkillUses.delete(toolUseId);
      if (block.is_error !== true) invokedSkills.add(requested);
    }
    const assistantText = textFromAssistant(message);
    if (assistantText) lastAssistantText = assistantText;
      if (message?.type === "result") {
        resultMessage = message;
        // Streaming-input queries stay alive for another user turn after a result.
        // End our one-turn input here so the SDK can close stdin and finish naturally.
        streamingInput?.close?.();
      }
    }
  } finally {
    streamingInput?.close?.();
  }
  if (!initialized || !resolvedSessionId) {
    fail("sdk_init_missing", "SDK turn completed without an initialization receipt");
  }
  if (!resultMessage) fail("sdk_result_missing", "SDK turn completed without a result receipt");
  if (resultMessage.subtype !== "success" || resultMessage.is_error === true) {
    const errors = Array.isArray(resultMessage.errors) ? resultMessage.errors.join("; ") : resultMessage.result;
    fail("sdk_turn_failed", compact(errors || resultMessage.subtype || "SDK turn failed", 500), {
      session_id: resolvedSessionId,
      subtype: resultMessage.subtype,
    });
  }
  if (prepared.operation === "compact" && !compactReceipt) {
    fail("compact_boundary_missing", "manual compaction completed without a compact_boundary receipt");
  }
  // Detect any post-verification mutation before issuing the receipt. Production publishes this
  // tree read-only, and the primary runtime exposes no host file tools; the second verification is
  // a fail-closed TOCTOU guard rather than a substitute for that OS boundary.
  await verifyApprovedSkillPlugin({
    pluginPath: prepared.plugin.pluginPath,
    manifestPath: prepared.plugin.manifestPath,
  });
  return Object.freeze({
    success: true,
    source: PRIMARY_RUNTIME_SOURCE,
    model: prepared.broker.model,
    actual_models: Object.freeze([...actualModels]),
    session_id: resolvedSessionId,
    resumed: Boolean(prepared.resumeSessionId),
    operation: prepared.operation,
    summary: prepared.operation === "compact"
      ? "Durable agent context compacted."
      : redacted(cleanString(resultMessage.result) || lastAssistantText),
    compact_receipt: compactReceipt,
    total_cost_usd: Number.isFinite(resultMessage.total_cost_usd) ? resultMessage.total_cost_usd : null,
    actual_cost_cents: Number.isFinite(resultMessage.total_cost_usd)
      ? Math.max(0, Math.round(resultMessage.total_cost_usd * 100))
      : null,
    usage: resultMessage.usage && typeof resultMessage.usage === "object" ? resultMessage.usage : null,
    skill_receipt: Object.freeze({
      plugin: prepared.plugin.pluginName,
      plugin_version: prepared.plugin.pluginVersion,
      manifest_path: prepared.plugin.manifestPath,
      approved: Object.freeze([...prepared.plugin.qualifiedSkillNames]),
      digests: Object.freeze(Object.fromEntries(
        prepared.plugin.skills.map((skill) => [
          `${prepared.plugin.pluginName}:${skill.name}`,
          skill.content_digest,
        ])
      )),
      attempted: Object.freeze([...attemptedSkills]),
      invoked: Object.freeze([...invokedSkills]),
    }),
  });
}
