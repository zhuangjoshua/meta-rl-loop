#!/usr/bin/env node
import path from "node:path";
import { fileURLToPath } from "node:url";

const PROGRESS_PREFIX = "TAKYON_SDK_EVENT ";
const SANDBOX_PATH = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin";
let workerStderr = "";

function readStdin() {
  return new Promise((resolve, reject) => {
    const chunks = [];
    process.stdin.on("data", (chunk) => {
      chunks.push(Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk));
    });
    process.stdin.on("end", () => resolve(Buffer.concat(chunks).toString("utf8")));
    process.stdin.on("error", reject);
  });
}

function isSubpath(root, maybePath) {
  const absolute = path.resolve(root, String(maybePath || "."));
  const relative = path.relative(root, absolute);
  return relative === "" || (!relative.startsWith("..") && !path.isAbsolute(relative));
}

function pathValues(input) {
  if (!input || typeof input !== "object") return [];
  return ["file_path", "path", "notebook_path"]
    .map((key) => input[key])
    .filter((value) => typeof value === "string" && value.length > 0);
}

function normalizeRelative(value) {
  return String(value || "")
    .replace(/\\/g, "/")
    .replace(/^\.\/+/, "")
    .replace(/^\/+/, "")
    .replace(/\/+$/, "");
}

function sandboxedBashCommand(command) {
  const script = String(command || "");
  return `/usr/bin/env -i PATH=${SANDBOX_PATH} HOME=/tmp /bin/bash -lc ${JSON.stringify(script)}`;
}

function buildClaudeSessionEnv({
  anthropicApiKey,
  anthropicToken,
  disableExperimentalBetas,
  anthropicBaseUrl,
  anthropicModel,
  anthropicDefaultOpusModel,
  anthropicDefaultSonnetModel,
  anthropicDefaultHaikuModel,
  claudeCodeSubagentModel,
  inDockerWorker,
  cwd,
}) {
  const env = {
    PATH: String(process.env.PATH || SANDBOX_PATH).trim() || SANDBOX_PATH,
    HOME: String(process.env.HOME || "/tmp").trim() || "/tmp",
    ANTHROPIC_API_KEY: anthropicApiKey,
    ANTHROPIC_TOKEN: anthropicToken,
    CLAUDE_AGENT_SDK_CLIENT_APP: "takyon-business-agent",
    CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS: disableExperimentalBetas,
    ...(anthropicBaseUrl ? { ANTHROPIC_BASE_URL: anthropicBaseUrl } : {}),
    ...(anthropicModel ? { ANTHROPIC_MODEL: anthropicModel } : {}),
    ...(anthropicDefaultOpusModel ? { ANTHROPIC_DEFAULT_OPUS_MODEL: anthropicDefaultOpusModel } : {}),
    ...(anthropicDefaultSonnetModel ? { ANTHROPIC_DEFAULT_SONNET_MODEL: anthropicDefaultSonnetModel } : {}),
    ...(anthropicDefaultHaikuModel ? { ANTHROPIC_DEFAULT_HAIKU_MODEL: anthropicDefaultHaikuModel } : {}),
    ...(claudeCodeSubagentModel ? { CLAUDE_CODE_SUBAGENT_MODEL: claudeCodeSubagentModel } : {})
  };
  for (const key of ["LANG", "LC_ALL", "SHELL", "TERM", "TMPDIR", "TMP", "TEMP", "USER"]) {
    const value = String(process.env[key] || "").trim();
    if (value) env[key] = value;
  }
  if (inDockerWorker) {
    env.TERMINAL_ENV = "local";
    env.TERMINAL_CWD = cwd;
    env.TERMINAL_DOCKER_MOUNT_CWD_TO_WORKSPACE = "0";
  }
  return env;
}

function rewriteWorkspacePrefixedPaths(input, workspace) {
  if (!input || typeof input !== "object") return input;
  const workspacePrefix = normalizeRelative(workspace);
  if (!workspacePrefix || workspacePrefix === ".") return input;
  let changed = false;
  const updated = { ...input };
  for (const key of ["file_path", "path", "notebook_path"]) {
    const value = updated[key];
    if (typeof value !== "string" || path.isAbsolute(value)) continue;
    const clean = normalizeRelative(value);
    if (clean === workspacePrefix) {
      updated[key] = ".";
      changed = true;
    } else if (clean.startsWith(`${workspacePrefix}/`)) {
      updated[key] = clean.slice(workspacePrefix.length + 1) || ".";
      changed = true;
    }
  }
  return changed ? updated : input;
}

function textFromSdkMessage(message) {
  const record = message && typeof message === "object" ? message : null;
  if (!record) return "";
  if (record.type === "assistant") {
    const inner = record.message && typeof record.message === "object" ? record.message : null;
    const content = Array.isArray(inner?.content) ? inner.content : [];
    return content
      .map((part) => (part?.type === "text" && typeof part.text === "string" ? part.text : ""))
      .filter(Boolean)
      .join("\n");
  }
  if (record.type === "result" && typeof record.result === "string") return record.result;
  return "";
}

function redact(text) {
  return String(text || "")
    .replace(/sk-[A-Za-z0-9_-]{12,}/g, "sk-[redacted]")
    .replace(/EAA[A-Za-z0-9_-]{20,}/g, "EAA[redacted]")
    .replace(/\b(api[_-]?key|access[_-]?token|secret)=([^&\s]+)/gi, "$1=[redacted]");
}

function compactText(text, maxLen = 240) {
  const cleaned = redact(String(text || "").replace(/\s+/g, " ").trim());
  if (!cleaned) return "";
  return cleaned.length > maxLen ? `${cleaned.slice(0, maxLen - 1)}…` : cleaned;
}

function humanizeLabel(value) {
  const cleaned = String(value || "")
    .replace(/[._-]+/g, " ")
    .trim();
  if (!cleaned) return "Claude task";
  return cleaned.replace(/\b\w/g, (match) => match.toUpperCase());
}

const thinkingBlockState = new Set();
let emittedPlanningMilestone = false;

function planningProgressEvent() {
  const detail = "Claude is inspecting the product and preparing the implementation.";
  return {
    kind: "claude_agent_sdk",
    status: "trace",
    detail,
    line: detail,
    trace: {
      kind: "reasoning",
      entry_key: "claude-planning",
      label: "Planning",
      detail,
      status: "running",
      summary: detail,
    },
  };
}

function assistantThinkingText(message) {
  const record = message && typeof message === "object" ? message : null;
  if (!record || record.type !== "assistant") return "";
  const inner = record.message && typeof record.message === "object" ? record.message : null;
  const content = Array.isArray(inner?.content) ? inner.content : [];
  return content
    .map((part) => (part?.type === "thinking" && typeof part.thinking === "string" ? part.thinking : ""))
    .filter(Boolean)
    .join("\n");
}

function thinkingProgressEventFromStream(message) {
  const record = message && typeof message === "object" ? message : null;
  const event = record && record.type === "stream_event" && record.event && typeof record.event === "object"
    ? record.event
    : null;
  if (!event) return null;
  if (event.type === "content_block_start") {
    const block = event.content_block && typeof event.content_block === "object" ? event.content_block : null;
    if (block?.type !== "thinking") return null;
    thinkingBlockState.add(Number(event.index));
    if (emittedPlanningMilestone) return null;
    emittedPlanningMilestone = true;
    return planningProgressEvent();
  }
  if (event.type === "content_block_delta") {
    const delta = event.delta && typeof event.delta === "object" ? event.delta : null;
    if (!delta || delta.type !== "thinking_delta") return null;
    // Thinking deltas are private model reasoning, not user-facing progress. Streaming their
    // arbitrary token boundaries caused split words, cumulative replay, and hundreds of lines
    // of design deliberation. Tool/task events below remain the authoritative progress lane.
    return null;
  }
  if (event.type === "content_block_stop") {
    thinkingBlockState.delete(Number(event.index));
    return null;
  }
  return null;
}

function progressEventFromSdkMessage(message) {
  const record = message && typeof message === "object" ? message : null;
  if (!record) return null;
  const thinkingProgress = thinkingProgressEventFromStream(record);
  if (thinkingProgress) return thinkingProgress;
  const assistantThinking = assistantThinkingText(record);
  if (assistantThinking) {
    // The completed assistant envelope repeats thinking blocks after their stream events. Never
    // expose that private reasoning or replay it as a second, larger progress message.
    return null;
  }
  if (record.type === "system" && record.subtype === "task_started") {
    const entryKey = `claude-task:${String(record.task_id || record.uuid || "task").trim()}`;
    const detail = compactText(record.description || record.prompt || record.workflow_name || record.task_type || "Claude task started.");
    return {
      kind: "claude_agent_sdk",
      status: "trace",
      detail,
      line: detail,
      trace: {
        kind: "task",
        entry_key: entryKey,
        label: compactText(record.description || record.workflow_name || record.subagent_type || record.task_type || "Claude task", 80) || "Claude task",
        detail,
        status: "running",
        skill_name: compactText(record.workflow_name || record.subagent_type || "", 80),
      },
    };
  }
  if (record.type === "system" && record.subtype === "task_progress") {
    const entryKey = `claude-task:${String(record.task_id || record.uuid || "task").trim()}`;
    const detail = compactText(record.summary || record.description || record.last_tool_name || "Claude task is running.");
    return {
      kind: "claude_agent_sdk",
      status: "trace",
      detail,
      line: detail,
      trace: {
        kind: "task",
        entry_key: entryKey,
        label: compactText(record.description || record.subagent_type || "Claude task", 80) || "Claude task",
        detail,
        status: "running",
        tool_name: compactText(record.last_tool_name || "", 80),
        summary: detail,
      },
    };
  }
  if (record.type === "system" && record.subtype === "task_updated") {
    const patch = record.patch && typeof record.patch === "object" ? record.patch : {};
    const rawStatus = String(patch.status || "running").trim().toLowerCase();
    const traceStatus = rawStatus === "completed" ? "completed" : rawStatus === "failed" || rawStatus === "killed" ? "failed" : "running";
    const entryKey = `claude-task:${String(record.task_id || record.uuid || "task").trim()}`;
    const detail = compactText(patch.error || patch.description || `Claude task ${rawStatus || "updated"}.`);
    return {
      kind: "claude_agent_sdk",
      status: "trace",
      detail,
      line: detail,
      trace: {
        kind: "task",
        entry_key: entryKey,
        label: compactText(patch.description || "Claude task", 80) || "Claude task",
        detail,
        status: traceStatus,
        summary: detail,
      },
    };
  }
  if (record.type === "tool_progress") {
    const roundedSeconds = Math.max(0, Math.round(Number(record.elapsed_time_seconds || 0)));
    if (roundedSeconds > 0 && roundedSeconds % 5 !== 0) return null;
    const detail = compactText(`${humanizeLabel(record.tool_name)} running${roundedSeconds > 0 ? ` · ${roundedSeconds}s` : ""}`);
    return {
      kind: "claude_agent_sdk",
      status: "output",
      detail,
      line: detail,
      trace: {
        kind: "tool",
        entry_key: `claude-tool:${String(record.tool_use_id || record.uuid || record.tool_name || "tool").trim()}`,
        label: humanizeLabel(record.tool_name || "Tool"),
        detail,
        status: "running",
        tool_name: compactText(record.tool_name || "", 80),
      },
    };
  }
  if (record.type === "tool_use_summary") {
    const detail = compactText(record.summary || "Claude tool completed.");
    return detail ? { kind: "claude_agent_sdk", status: "output", detail, line: detail } : null;
  }
  if (record.type === "system" && record.subtype === "api_retry") {
    const detail = compactText(`Claude API retry ${Number(record.attempt || 0)}/${Number(record.max_retries || 0)} in ${Number(record.retry_delay_ms || 0)}ms.`);
    return detail ? { kind: "claude_agent_sdk", status: "output", detail, line: detail } : null;
  }
  return null;
}

let lastProgressSignature = "";

function emitProgress(event) {
  if (!event || typeof event !== "object") return;
  const payload = {
    kind: compactText(event.kind || "claude_agent_sdk", 80) || "claude_agent_sdk",
    status: compactText(event.status || "output", 24) || "output",
    detail: compactText(event.detail || event.line || "", 240),
    line: compactText(event.line || event.detail || "", 240),
  };
  if (event.trace && typeof event.trace === "object") {
    payload.trace = Object.fromEntries(
      Object.entries(event.trace)
        .map(([key, value]) => [key, compactText(value, key === "entry_key" ? 120 : 160)])
        .filter(([, value]) => value)
    );
  }
  if (!payload.detail && !payload.line && !payload.trace) return;
  const serialized = JSON.stringify(payload);
  if (serialized === lastProgressSignature) return;
  lastProgressSignature = serialized;
  process.stderr.write(`${PROGRESS_PREFIX}${serialized}\n`);
}

function buildPrompt(input) {
  const normalizedWorkspace = normalizeRelative(input.workspace || ".");
  const bashRule = input.allowBash
    ? "You may use Bash only for local build/test/install/cleanup inside the current workspace. Do not use it for provider calls, deployment, posting, payment changes, or filesystem access outside this workspace."
    : "Do not attempt shell commands, network calls, vendor side effects, credential reads, deployment, posting, payment changes, or filesystem access outside this workspace.";
  const noteRule = normalizedWorkspace === "product/site" || normalizedWorkspace.startsWith("product/site/")
    ? "For product/site work, reflect durable truth in the source itself and your final summary. Do not create helper markdown, request files, verification notes, or scratch docs unless the instruction explicitly asks for them."
    : "If durable business truth changes, write a concise note into an appropriate file in this workspace or a child path.";
  const buildGate = input.allowBash
    ? "Customer-facing product build gate (HARD): before you finish you MUST run `npm run build` and `npm run typecheck` and confirm BOTH exit green. Diagnosing the error is not done; only a green build is done. If you cannot land both green this pass, do NOT report success — your FINAL line MUST start with BLOCKED: followed by the exact remaining build/typecheck error and the file(s) involved."
    : "";
  return [
    "You are a Claude Agent SDK worker called by Takyon for one bounded business-scoped task.",
    "",
    `You may inspect and edit files only inside the provided current workspace. ${bashRule}`,
    "The provided current workspace is already your working directory. Write paths relative to it; do not prefix paths with the workspace name again.",
    "",
    `Make the smallest useful changes that satisfy the task. Preserve existing business files unless the instruction asks to update them. ${noteRule}`,
    "",
    "Execution posture (HARD): begin with targeted file inspection, then implement immediately. Do not narrate design exploration, produce an implementation plan, or spend a turn deliberating before using tools. Keep private reasoning private; expose only concise tool progress and the final result.",
    "",
    "Do not claim external execution happened. If the task needs a vendor/API/payment/deploy/posting action you cannot perform, report the blocker in the final summary instead of pretending it ran.",
    "Do not create request/spec/verification markdown files unless the instruction explicitly asks for them.",
    ...(buildGate ? ["", buildGate] : []),
    "",
    `Business: ${input.business}`,
    `Workspace: ${input.workspace || "."}`,
    "",
    "Task:",
    String(input.instruction || "").trim(),
    "",
    "Finish with a short summary of files changed and any blocker. If blocked, start the final line with BLOCKED:."
  ].join("\n");
}

async function main() {
  thinkingBlockState.clear();
  emittedPlanningMilestone = false;
  lastProgressSignature = "";
  const raw = await readStdin();
  const input = JSON.parse(raw || "{}");
  const cwd = path.resolve(String(input.cwd || "."));
  const root = path.resolve(String(input.root || cwd));
  if (!isSubpath(root, cwd)) {
    throw new Error("cwd must be inside the business root");
  }
  const anthropicApiKey = String(process.env.ANTHROPIC_API_KEY || "").trim();
  const anthropicToken = String(
    process.env.ANTHROPIC_TOKEN || process.env.CLAUDE_CODE_OAUTH_TOKEN || ""
  ).trim();
  // Broker lockdown: the operator host points the SDK at the safebox broker (ANTHROPIC_BASE_URL) and
  // injects a short-TTL capability token as ANTHROPIC_API_KEY — the raw provider key never enters this
  // container. In that mode ANTHROPIC_API_KEY (the capability token) is the auth and the base URL is
  // present; outside lockdown a raw key/token is still required.
  const anthropicBaseUrl = String(process.env.ANTHROPIC_BASE_URL || "").trim();
  const anthropicModel = String(process.env.ANTHROPIC_MODEL || "").trim();
  const anthropicDefaultOpusModel = String(process.env.ANTHROPIC_DEFAULT_OPUS_MODEL || "").trim();
  const anthropicDefaultSonnetModel = String(process.env.ANTHROPIC_DEFAULT_SONNET_MODEL || "").trim();
  const anthropicDefaultHaikuModel = String(process.env.ANTHROPIC_DEFAULT_HAIKU_MODEL || "").trim();
  const claudeCodeSubagentModel = String(process.env.CLAUDE_CODE_SUBAGENT_MODEL || "").trim();
  const disableExperimentalBetas = String(
    process.env.CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS || "1"
  ).trim() || "1";
  if (!anthropicApiKey && !anthropicToken) {
    throw new Error(
      "ANTHROPIC_API_KEY, ANTHROPIC_TOKEN, or CLAUDE_CODE_OAUTH_TOKEN is required"
    );
  }

  const requestedModel = String(input.model || "").trim();
  const pinnedModel = String(process.env.TAKYON_CLAUDE_AGENT_MODEL || "").trim();
  if (requestedModel && pinnedModel && requestedModel !== pinnedModel) {
    throw new Error(
      `coding worker model override refused: requested ${JSON.stringify(requestedModel)}, ` +
      `pinned ${JSON.stringify(pinnedModel)} by TAKYON_CLAUDE_AGENT_MODEL`
    );
  }
  const model = requestedModel || pinnedModel;
  if (!model) {
    throw new Error(
      "coding worker model is not configured; no fallback model is available"
    );
  }
  for (const [name, value] of Object.entries({
    ANTHROPIC_MODEL: anthropicModel,
    ANTHROPIC_DEFAULT_OPUS_MODEL: anthropicDefaultOpusModel,
    ANTHROPIC_DEFAULT_SONNET_MODEL: anthropicDefaultSonnetModel,
    ANTHROPIC_DEFAULT_HAIKU_MODEL: anthropicDefaultHaikuModel,
    CLAUDE_CODE_SUBAGENT_MODEL: claudeCodeSubagentModel,
  })) {
    if (value && value !== model) {
      throw new Error(
        `coding worker model alias ${name}=${JSON.stringify(value)} conflicts with pinned model ` +
        JSON.stringify(model)
      );
    }
  }

  const { query } = await import("@anthropic-ai/claude-agent-sdk");
  const abortController = new AbortController();
  const maxTurns = Number.parseInt(String(input.maxTurns || ""), 10) || 12;
  const maxBudgetUsd = Number.parseFloat(String(input.maxBudgetUsd || "")) || 2;
  const effort = String(input.effort || process.env.TAKYON_CLAUDE_AGENT_EFFORT || "high").trim().toLowerCase();
  const allowBash = Boolean(input.allowBash);
  const pathToClaudeCodeExecutable = String(process.env.TAKYON_CLAUDE_CODE_EXECUTABLE || "").trim();
  const inDockerWorker = String(process.env.TAKYON_CLAUDE_AGENT_IN_DOCKER || "").trim() === "1";

  let text = "";
  let totalCostUsd = null;
  let finalUsage = null;
  workerStderr = "";
  let parentAbortRequested = false;
  const requestParentAbort = () => {
    parentAbortRequested = true;
    abortController.abort();
  };
  process.once("SIGTERM", requestParentAbort);
  process.once("SIGINT", requestParentAbort);
  try {
    for await (const message of query({
          prompt: buildPrompt(input),
          options: {
            abortController,
            cwd,
            env: buildClaudeSessionEnv({
              anthropicApiKey,
              anthropicToken,
              disableExperimentalBetas,
              anthropicBaseUrl,
              anthropicModel: model,
              anthropicDefaultOpusModel: model,
              anthropicDefaultSonnetModel: model,
              anthropicDefaultHaikuModel: model,
              claudeCodeSubagentModel: model,
              inDockerWorker,
              cwd,
            }),
            model,
            includePartialMessages: true,
            thinking: { type: "adaptive", display: "summarized" },
            effort: ["low", "medium", "high"].includes(effort) ? effort : "high",
            tools: allowBash
              ? ["Read", "Write", "Edit", "MultiEdit", "Grep", "Glob", "Bash"]
              : ["Read", "Write", "Edit", "MultiEdit", "Grep", "Glob"],
            disallowedTools: allowBash ? [] : ["Bash"],
            permissionMode: "acceptEdits",
            persistSession: false,
            maxTurns,
            maxBudgetUsd,
            ...(pathToClaudeCodeExecutable ? { pathToClaudeCodeExecutable } : {}),
            stderr: (chunk) => {
              if (typeof chunk !== "string" || !chunk) return;
              if (workerStderr.length >= 12000) return;
              workerStderr += chunk.slice(0, 12000 - workerStderr.length);
            },
            canUseTool: async (toolName, toolInput, options) => {
              if (toolName === "Bash") {
                if (!allowBash) {
                  return {
                    behavior: "deny",
                    message: "Bash is disabled for Takyon Claude SDK business tasks.",
                    toolUseID: options.toolUseID
                  };
                }
                const updatedInput = { ...(toolInput || {}) };
                if (typeof updatedInput.command === "string") {
                  updatedInput.command = sandboxedBashCommand(updatedInput.command);
                } else if (typeof updatedInput.cmd === "string") {
                  updatedInput.cmd = sandboxedBashCommand(updatedInput.cmd);
                }
                return { behavior: "allow", updatedInput, toolUseID: options.toolUseID };
              }
              const updatedInput = rewriteWorkspacePrefixedPaths(toolInput, input.workspace || ".");
              const outside = pathValues(updatedInput).find((value) => !isSubpath(root, path.resolve(cwd, value)));
              if (outside) {
                return {
                  behavior: "deny",
                  message: "Takyon Claude SDK tasks may only access files inside the requested workspace.",
                  toolUseID: options.toolUseID
                };
              }
              return { behavior: "allow", updatedInput, toolUseID: options.toolUseID };
            }
          }
    })) {
      emitProgress(progressEventFromSdkMessage(message));
      const chunk = textFromSdkMessage(message);
      if (chunk) text += `${chunk}\n`;
      if (message && typeof message === "object" && message.type === "result") {
        if (typeof message.total_cost_usd === "number" && Number.isFinite(message.total_cost_usd)) {
          totalCostUsd = message.total_cost_usd;
        }
        if (message.usage && typeof message.usage === "object") {
          finalUsage = message.usage;
        }
      }
    }
    if (parentAbortRequested) {
      throw new Error("Claude Agent SDK task cancelled by parent supervisor");
    }
  } finally {
    process.removeListener("SIGTERM", requestParentAbort);
    process.removeListener("SIGINT", requestParentAbort);
  }

  process.stdout.write(JSON.stringify({
    success: true,
    source: "claude-agent-sdk",
    model,
    summary: redact(text).trim(),
    total_cost_usd: typeof totalCostUsd === "number" ? totalCostUsd : null,
    actual_cost_cents: typeof totalCostUsd === "number" ? Math.max(0, Math.round(totalCostUsd * 100)) : null,
    usage: finalUsage,
    worker_stderr: redact(workerStderr).trim() || null,
  }));
}

export {
  buildPrompt,
  progressEventFromSdkMessage,
};

const isMainModule = process.argv[1]
  && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url);

if (isMainModule) main().catch((error) => {
  emitProgress({
    kind: "claude_agent_sdk",
    status: "failed",
    detail: compactText(error?.message || String(error)),
    line: compactText(error?.message || String(error)),
  });
  process.stdout.write(JSON.stringify({
    success: false,
    source: "claude-agent-sdk",
    error: redact(error?.stack || error?.message || String(error)),
    worker_stderr: redact(workerStderr).trim() || null,
  }));
  process.exitCode = 1;
});
