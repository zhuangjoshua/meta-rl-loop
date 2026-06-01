#!/usr/bin/env node
import path from "node:path";

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

function buildPrompt(input) {
  return [
    "You are a Claude Agent SDK worker called by Takyon for one bounded business-scoped task.",
    "",
    "You may inspect and edit files only inside the provided current workspace. Do not attempt shell commands, network calls, vendor side effects, credential reads, deployment, posting, payment changes, or filesystem access outside this workspace.",
    "The provided current workspace is already your working directory. Write paths relative to it; do not prefix paths with the workspace name again.",
    "",
    "Make the smallest useful changes that satisfy the task. Preserve existing business files unless the instruction asks to update them. If durable business truth changes, write a concise note into an appropriate file in this workspace or a child path.",
    "",
    "Do not claim external execution happened. If the task needs a vendor/API/payment/deploy/posting action, write the request/spec/receipt expectation as a file for Takyon to review rather than pretending it ran.",
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

  let timeout = null;
  let text = "";
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
            effort: String(process.env.TAKYON_CLAUDE_AGENT_EFFORT || "high"),
            tools: ["Read", "Write", "Edit", "MultiEdit", "Grep", "Glob"],
            disallowedTools: ["Bash"],
            permissionMode: "acceptEdits",
            persistSession: false,
            maxTurns,
            maxBudgetUsd,
            canUseTool: async (toolName, toolInput, options) => {
              if (toolName === "Bash") {
                return {
                  behavior: "deny",
                  message: "Bash is disabled for Takyon Claude SDK business tasks.",
                  toolUseID: options.toolUseID
                };
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
          const chunk = textFromSdkMessage(message);
          if (chunk) text += `${chunk}\n`;
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
    summary: redact(text).trim()
  }));
}

main().catch((error) => {
  process.stdout.write(JSON.stringify({
    success: false,
    source: "claude-agent-sdk",
    error: redact(error?.stack || error?.message || String(error))
  }));
  process.exitCode = 1;
});
