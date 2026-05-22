import fs from "node:fs/promises";
import path from "node:path";
import { buildAndDeployExistingGeneratedApp } from "./builder";
import { getCompanyBuildInput, getLatestGeneratedAppBuild } from "./records";
import { runClaudeSdkSurfaceBuilder } from "./surface-builder";
import { ConfigurationError } from "../errors";
import { loadLocalSecrets } from "../secrets";

type ProductLane = "product_backend" | "product_ui";

type ProductLaneResult = {
  status: "completed" | "blocked";
  lane: ProductLane;
  sourceDir?: string;
  sdkSummary?: string;
  deployment?: unknown;
  reason?: string;
};

function redact(value: string) {
  return value
    .replace(/sk-[A-Za-z0-9_-]{12,}/g, "sk-[redacted]")
    .replace(/ak_[A-Za-z0-9_-]{12,}/g, "ak_[redacted]")
    .replace(/takyon_[A-Za-z0-9_-]{12,}/g, "takyon_[redacted]");
}

function isSubpath(root: string, maybePath: string) {
  const absolute = path.resolve(root, maybePath);
  const relative = path.relative(root, absolute);
  return relative === "" || (!relative.startsWith("..") && !path.isAbsolute(relative));
}

function pathValues(input: Record<string, unknown>) {
  return ["file_path", "path", "notebook_path"]
    .map((key) => input[key])
    .filter((value): value is string => typeof value === "string" && value.length > 0);
}

function textFromSdkMessage(message: unknown) {
  const record = message && typeof message === "object" ? (message as Record<string, unknown>) : null;
  if (!record) return "";
  if (record.type === "assistant") {
    const inner = record.message && typeof record.message === "object" ? (record.message as Record<string, unknown>) : null;
    const content = Array.isArray(inner?.content) ? inner.content : [];
    return content
      .map((part) => {
        const partRecord = part && typeof part === "object" ? (part as Record<string, unknown>) : null;
        return partRecord?.type === "text" && typeof partRecord.text === "string" ? partRecord.text : "";
      })
      .filter(Boolean)
      .join("\n");
  }
  if (record.type === "result" && typeof record.result === "string") return record.result;
  return "";
}

async function assertWorkspaceReady(sourceDir: string) {
  const stat = await fs.stat(sourceDir).catch(() => null);
  if (!stat?.isDirectory()) {
    throw new ConfigurationError(`Generated app source directory is missing: ${sourceDir}`);
  }
  const packageJson = await fs.stat(path.join(sourceDir, "package.json")).catch(() => null);
  if (!packageJson?.isFile()) {
    throw new ConfigurationError(`Generated app package.json is missing in ${sourceDir}`);
  }
}

function productPrompt(input: {
  lane: ProductLane;
  company: NonNullable<Awaited<ReturnType<typeof getCompanyBuildInput>>>;
}) {
  const laneInstruction =
    input.lane === "product_backend"
      ? [
          "Implement bounded product-specific backend behavior for this generated app.",
          "Prefer editing src/app/api/product/run/route.ts and adding small helpers under src/lib/product/.",
          "The backend must call the platform client for AI/auth/limits and must not invent provider-key, payment, or posting infrastructure.",
          "The generated product route must call runProductWorkflow with purpose: \"product\"; use productModule.category only as display/category metadata."
        ]
      : [
          "Implement bounded product-specific workflow UI for this generated app.",
          "Prefer editing src/app/product/page.tsx and src/app/globals.css.",
          "The UI should feel like a finished product workflow, not a demo page, but it must submit to the existing product API route."
        ];

  return [
    "You are the Claude Agent SDK running as a scoped Takyon generated-app product builder.",
    "",
    "Company:",
    `- id: ${input.company.id}`,
    `- name: ${input.company.name}`,
    `- public pitch: ${input.company.public_pitch}`,
    `- customer: ${input.company.customer || "unknown"}`,
    `- pain: ${input.company.pain || "unknown"}`,
    `- offer: ${input.company.offer || "unknown"}`,
    "",
    "Task:",
    ...laneInstruction.map((line) => `- ${line}`),
    "",
    "Hard constraints:",
    "- Edit only files inside the current generated-app workspace.",
    "- Do not add raw Anthropic/OpenAI/Stripe/X/Meta/Vercel/vendor secrets.",
    "- Do not create fake success, fake payments, fake users, fake AI, fake posts, or fake vendor receipts.",
    "- Keep auth, checkout, entitlements, AI metering, and secrets platform-owned through the existing platform client/API contracts.",
    "- Treat this lane as one bounded product iteration, not the entire final product.",
    "- Ship one coherent compiling slice. If more product work remains, update src/product/ROADMAP.md with the next slices.",
    "- Do not use BLOCKED just because scope remains. Use BLOCKED only when you cannot make a real compiling increment.",
    "- If the requested product specialization cannot be implemented within these constraints, do not pretend it is done. End with exactly: BLOCKED: <reason>.",
    "- Keep TypeScript strict and avoid adding new dependencies unless absolutely necessary.",
    "- Preserve the existing deployable Next.js app shape.",
    "",
    "When done, summarize changed files and the real behavior implemented. If more work should continue, end with NEXT_ITERATION: <specific next slice>."
  ].join("\n");
}

export async function runClaudeSdkProductLane(input: {
  companyId: string;
  workflowJobId?: string | null;
  lane: ProductLane;
}): Promise<ProductLaneResult> {
  loadLocalSecrets();

  const company = await getCompanyBuildInput(input.companyId);
  if (!company) {
    return { status: "blocked", lane: input.lane, reason: "Company not found for generated product build." };
  }

  const latestBuild = await getLatestGeneratedAppBuild(input.companyId);
  if (!latestBuild?.source_dir) {
    return {
      status: "blocked",
      lane: input.lane,
      reason: "Generated app source is not available yet. Run the website_build_deploy lane first."
    };
  }

  const sourceDir = path.resolve(latestBuild.source_dir);
  try {
    await assertWorkspaceReady(sourceDir);
  } catch (error) {
    return {
      status: "blocked",
      lane: input.lane,
      sourceDir,
      reason: error instanceof Error ? error.message : "Generated app workspace is not ready."
    };
  }

  if (!process.env.ANTHROPIC_API_KEY?.trim()) {
    return { status: "blocked", lane: input.lane, reason: "ANTHROPIC_API_KEY is not configured for Claude Agent SDK." };
  }

  if (input.lane === "product_ui") {
    try {
      const surface = await runClaudeSdkSurfaceBuilder({
        rootDir: sourceDir,
        company,
        workflow: "product_ui"
      });
      const built = await buildAndDeployExistingGeneratedApp({
        companyId: input.companyId,
        workflowJobId: input.workflowJobId ?? null,
        sourceDir,
        productStatus: "ui_published"
      });

      if (built.deployment.status !== "completed") {
        return {
          status: "blocked",
          lane: input.lane,
          sourceDir,
          sdkSummary: surface.summary,
          deployment: built.deployment,
          reason: built.deployment.error ?? "Generated product UI build was blocked before healthy deployment."
        };
      }

      return {
        status: "completed",
        lane: input.lane,
        sourceDir,
        sdkSummary: surface.summary,
        deployment: built.deployment
      };
    } catch (error) {
      return {
        status: "blocked",
        lane: input.lane,
        sourceDir,
        reason: error instanceof Error ? error.message : "Claude Agent SDK product UI surface build was blocked."
      };
    }
  }

  const { query } = await import("@anthropic-ai/claude-agent-sdk");
  const abortController = new AbortController();
  const timeoutMs = Number.parseInt(process.env.ARGON_CLAUDE_AGENT_SDK_TIMEOUT_MS || "", 10) || 600_000;
  const messages: unknown[] = [];
  let text = "";
  let timeout: NodeJS.Timeout | null = null;

  try {
    await Promise.race([
      (async () => {
        for await (const message of query({
          prompt: productPrompt({ lane: input.lane, company }),
          options: {
            abortController,
            cwd: sourceDir,
            env: {
              ANTHROPIC_API_KEY: process.env.ANTHROPIC_API_KEY,
              CLAUDE_AGENT_SDK_CLIENT_APP: "takyon-product-builder"
            },
            model: "claude-opus-4-7",
            thinking: { type: "adaptive", display: "omitted" },
            effort: "xhigh",
            tools: ["Read", "Write", "Edit", "MultiEdit", "Grep", "Glob"],
            disallowedTools: ["Bash"],
            permissionMode: "default",
            persistSession: false,
            maxTurns: 12,
            maxBudgetUsd: 6,
            canUseTool: async (toolName, toolInput, options) => {
              if (toolName === "Bash") {
                return {
                  behavior: "deny",
                  message: "Bash is disabled inside the SDK builder; platform gates run commands after edits.",
                  toolUseID: options.toolUseID
                };
              }
              const paths = pathValues(toolInput);
              const outside = paths.find((value) => !isSubpath(sourceDir, value));
              if (outside) {
                return {
                  behavior: "deny",
                  message: "Generated-app builder may only access files inside the generated workspace.",
                  toolUseID: options.toolUseID
                };
              }
              return { behavior: "allow", updatedInput: toolInput, toolUseID: options.toolUseID };
            }
          }
        })) {
          messages.push(message);
          text += textFromSdkMessage(message);
        }
      })(),
      new Promise<never>((_, reject) => {
        timeout = setTimeout(() => {
          abortController.abort();
          reject(new Error(`Claude Agent SDK timed out after ${timeoutMs}ms.`));
        }, timeoutMs);
      })
    ]);
  } finally {
    if (timeout) clearTimeout(timeout);
    abortController.abort();
  }

  const summary = redact(text).slice(-8000);
  if (/\bBLOCKED:/i.test(summary)) {
    return {
      status: "blocked",
      lane: input.lane,
      sourceDir,
      sdkSummary: summary,
      reason: summary.match(/\bBLOCKED:\s*(.+)/i)?.[1]?.trim() || "Claude Agent SDK reported the lane blocked."
    };
  }

  const built = await buildAndDeployExistingGeneratedApp({
    companyId: input.companyId,
    workflowJobId: input.workflowJobId ?? null,
    sourceDir,
    productStatus: "backend_published"
  });

  if (built.deployment.status !== "completed") {
    return {
      status: "blocked",
      lane: input.lane,
      sourceDir,
      sdkSummary: summary,
      deployment: built.deployment,
      reason: built.deployment.error ?? "Generated product build was blocked before healthy deployment."
    };
  }

  return {
    status: "completed",
    lane: input.lane,
    sourceDir,
    sdkSummary: summary,
    deployment: built.deployment
  };
}
