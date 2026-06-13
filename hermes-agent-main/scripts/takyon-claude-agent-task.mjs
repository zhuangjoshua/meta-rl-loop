#!/usr/bin/env node
import path from "node:path";

const PROGRESS_PREFIX = "TAKYON_SDK_EVENT ";
let workerStderr = "";

function readStdin() {
  return new Promise((resolve, reject) => {
    let data = "";
    process.stdin.setEncoding("utf8");
    process.stdin.on("data", (chunk) => {
      data += chunk;
    });
    process.stdin.on("end", () => resolve(data));
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
  return `/usr/bin/env -i PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin HOME=/tmp /bin/bash -lc ${JSON.stringify(script)}`;
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

function progressEventFromSdkMessage(message) {
  const record = message && typeof message === "object" ? message : null;
  if (!record) return null;
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
  return [
    "You are a Claude Agent SDK worker called by Takyon for one bounded business-scoped task.",
    "",
    `You may inspect and edit files only inside the provided current workspace. ${bashRule}`,
    "The provided current workspace is already your working directory. Write paths relative to it; do not prefix paths with the workspace name again.",
    "",
    `Make the smallest useful changes that satisfy the task. Preserve existing business files unless the instruction asks to update them. ${noteRule}`,
    "",
    "Do not claim external execution happened. If the task needs a vendor/API/payment/deploy/posting action you cannot perform, report the blocker in the final summary instead of pretending it ran.",
    "Do not create request/spec/verification markdown files unless the instruction explicitly asks for them.",
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
  const raw = await readStdin();
  const input = JSON.parse(raw || "{}");
  const cwd = path.resolve(String(input.cwd || "."));
  const root = path.resolve(String(input.root || cwd));
  if (!isSubpath(root, cwd)) {
    throw new Error("cwd must be inside the business root");
  }
  if (!process.env.ANTHROPIC_API_KEY && !process.env.ANTHROPIC_TOKEN) {
    throw new Error("ANTHROPIC_API_KEY or ANTHROPIC_TOKEN is required");
  }

  const { query } = await import("@anthropic-ai/claude-agent-sdk");
  const abortController = new AbortController();
  const timeoutMs = Number.parseInt(String(input.timeoutMs || ""), 10) || 300000;
  const maxTurns = Number.parseInt(String(input.maxTurns || ""), 10) || 12;
  const maxBudgetUsd = Number.parseFloat(String(input.maxBudgetUsd || "")) || 2;
  const model = String(input.model || process.env.TAKYON_CLAUDE_AGENT_MODEL || "claude-sonnet-4-6").trim();
  const effort = String(input.effort || process.env.TAKYON_CLAUDE_AGENT_EFFORT || "high").trim().toLowerCase();
  const allowBash = Boolean(input.allowBash);
  const pathToClaudeCodeExecutable = String(process.env.TAKYON_CLAUDE_CODE_EXECUTABLE || "").trim();

  let timeout = null;
  let text = "";
  let totalCostUsd = null;
  let finalUsage = null;
  workerStderr = "";
  try {
    await Promise.race([
      (async () => {
        for await (const message of query({
          prompt: buildPrompt(input),
          options: {
            abortController,
            cwd,
            env: {
              ANTHROPIC_API_KEY: process.env.ANTHROPIC_API_KEY,
              ANTHROPIC_TOKEN: process.env.ANTHROPIC_TOKEN,
              CLAUDE_AGENT_SDK_CLIENT_APP: "takyon-business-agent"
            },
            model,
            thinking: { type: "adaptive", display: "omitted" },
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
      })(),
      new Promise((_, reject) => {
        timeout = setTimeout(() => {
          abortController.abort();
          reject(new Error(`Claude Agent SDK task timed out after ${timeoutMs}ms`));
        }, timeoutMs);
      })
    ]);
  } finally {
    if (timeout) clearTimeout(timeout);
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

main().catch((error) => {
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
