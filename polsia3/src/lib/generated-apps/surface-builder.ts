import crypto from "node:crypto";
import fs from "node:fs/promises";
import path from "node:path";
import { ConfigurationError, IntegrationCallError } from "../errors";
import { loadLocalSecrets } from "../secrets";
import type { CompanyBuildInput } from "./records";
import {
  loadGeneratedAppSurfaceContext,
  requiredSurfaceFiles,
  validateGeneratedFiles,
  type GeneratedAppSurfaceContext
} from "./open-lovable";

type SurfaceWorkflow = "build_site" | "product_ui";

type SdkSurfaceResult = {
  source: "claude-agent-sdk";
  model: string;
  workflow: SurfaceWorkflow;
  files: string[];
  turnsBudget: number;
  summary: string;
  context: {
    mission: boolean;
    marketResearch: boolean;
    designBrief: boolean;
    plans: number;
  };
};

type GeneratedFile = {
  path: string;
  content: string;
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

function formatSurfaceContext(context: GeneratedAppSurfaceContext) {
  const planLines = context.plans.map((plan) => {
    const amount = plan.priceUsdCents <= 0 ? "$0" : `$${(plan.priceUsdCents / 100).toFixed(0)}/${plan.billingInterval}`;
    return `- ${plan.planKey} (${plan.tier}): ${amount}, ${plan.includedActionQuota ?? "unknown"} actions, ${(plan.includedAiBudgetMicrousd / 1_000_000).toFixed(2)} USD AI budget`;
  });
  return [
    "Mission document:",
    context.mission || "No non-seeded Mission document found.",
    "",
    "Market research document:",
    context.marketResearch || "No non-seeded Market Research document found.",
    "",
    "Takyon marketing context:",
    context.marketingContext || "No Business Marketing Context document found.",
    "",
    "Takyon product design brief:",
    context.designBrief || "No Business Product Design document found. If present later, use it as the primary web/app design system and screen-brief source.",
    "",
    "Takyon conversion review:",
    context.conversionReview || "No Business Conversion Review document found.",
    "",
    "Plan policies:",
    planLines.length ? planLines.join("\n") : "No plan policy rows found.",
    "",
    "Routes:",
    `- signup: ${context.routes.signup}`,
    `- product: ${context.routes.product}`,
    `- magic link request: ${context.routes.authRequest}`,
    `- starter checkout: ${context.routes.checkoutStarter}`,
    "",
    "Economics:",
    context.economics.definitions,
    context.economics.guardrails
  ].join("\n");
}

function surfacePrompt(input: {
  company: CompanyBuildInput;
  context: GeneratedAppSurfaceContext;
  workflow: SurfaceWorkflow;
  operatorInstruction?: string | null;
  issues?: string[];
}) {
  const cleanName = input.company.name.replace(/\s+E2E\s+\d+/gi, "").trim() || input.company.name;
  const repair = input.issues?.length
    ? [
        "",
        "The previous generated surface was rejected by the validator. Fix every issue below before finishing:",
        ...input.issues.map((issue) => `- ${issue}`)
      ]
    : [];

  return [
    "You are the Claude Agent SDK running as the Takyon generated-app surface builder.",
    "Your job is to edit the generated Next.js app workspace into a polished, customer-facing micro-SaaS.",
    "",
    "Use OpenLovable-level polish as a taste target: cohesive visual hierarchy, crisp app-builder gloss, modern B2B SaaS density, and finished product UX.",
    "Do not use the OpenLovable app/server/API. You are editing files directly in this workspace.",
    "If Takyon product design guidance is present in the context, treat it as the business-owned design brief. It may adapt Open Design patterns, but you must not invoke Open Design, MCP tools, daemons, exports, media pipelines, or external repo-writing agents.",
    "",
    "Edit exactly these customer-surface files:",
    ...requiredSurfaceFiles.map((file) => `- ${file}`),
    "",
    "Do not edit or replace deterministic platform rails:",
    "- src/app/api/product/run/route.ts",
    "- src/lib/platform-client.ts",
    "- package.json",
    "- next.config.ts",
    "- any secrets, env files, vendor APIs, or deployment files",
    "",
    "Required behavior:",
    "- Homepage must be a finished customer-facing website for this business, not an internal build/status page.",
    "- For build_site, prioritize a fast initial deploy: landing page, signup, pricing/subscription CTA, and a lightweight product preview/mock. Do not try to finish the full product UI in this lane.",
    "- Treat product_ui as one bounded improved-product iteration; do not try to finish an arbitrarily large product in one run.",
    "- For product_ui, ship one coherent compiling UI slice against the existing product backend.",
    "- Treat build_site as the public website lane; it must return a useful public site even when product_ui is still queued.",
    "- Homepage must include visible links to /signup and /product.",
    "- Signup page must import requestMagicLink and generatedAppCheckoutUrl from '@/lib/platform-client'.",
    "- Signup page must call requestMagicLink(email) and show Free/Starter pricing using the plan policy context.",
    "- Product page must collect work email plus substantial product-specific input.",
    "- Product page must POST to /api/product/run with JSON { email, brief }.",
    "- src/product/module.ts must export productModule with productName, category, actionLabel, inputLabel, inputPlaceholder, resultLabel, systemPrompt, outputInstructions.",
    "- src/app/globals.css must define every className used by page JSX and include responsive rules.",
    "",
    "Truthfulness constraints:",
    "- Do not mention Takyon, Four Manifold, backend receipts, product plans, build status, queues, Vercel, Stripe, X, Meta, OpenLovable, or implementation details in customer-facing UI.",
    "- Do not include fake customers, fake logos, fake metrics, fake testimonials, demo data, example.com, href='#', mock/fake/dummy language, or unsupported vendor claims.",
    "- Do not claim live browser execution, continuous monitoring, around-the-clock detection, observed production findings, real integrations, posting, billing completion, or vendor side effects unless the deterministic rails actually perform them.",
    "- If the product is only generating AI analysis/plans from user input, say that plainly and make it useful. Do not pretend it already executed the work.",
    "",
    "Design constraints:",
    "- No giant broken type. Text must fit on desktop and mobile.",
    "- No one-note purple/blue gradient slab. Use a polished but restrained palette.",
    "- Use CSS classes, not inline style objects.",
    "- Keep cards to individual repeated items/tools only; do not nest cards inside cards.",
    "- Prefer a real app-like first screen with clear CTAs and workflow, not a marketing-only splash.",
    "",
    "Company:",
    JSON.stringify(
      {
        id: input.company.id,
        name: cleanName,
        slug: input.company.slug,
        publicPitch: input.company.public_pitch,
        customer: input.company.customer,
        pain: input.company.pain,
        offer: input.company.offer
      },
      null,
      2
    ),
    "",
    input.operatorInstruction?.trim()
      ? [
          "Operator-requested surface change:",
          input.operatorInstruction.trim(),
          "",
          "Apply that request to the customer-facing surface while preserving all deterministic platform rails and truthfulness constraints."
        ].join("\n")
      : "",
    "",
    "Foundation/research/pricing/auth context:",
    formatSurfaceContext(input.context),
    ...repair,
    "",
    "When done, do not run shell commands. End with a short summary of the files changed. If more product UI work should continue, end with NEXT_ITERATION: <specific next slice>. If genuinely blocked, end with exactly BLOCKED: <reason>."
  ].join("\n");
}

async function runSdkEdit(input: {
  sourceDir: string;
  company: CompanyBuildInput;
  context: GeneratedAppSurfaceContext;
  workflow: SurfaceWorkflow;
  operatorInstruction?: string | null;
  issues?: string[];
}) {
  const { query } = await import("@anthropic-ai/claude-agent-sdk");
  const abortController = new AbortController();
  const timeoutMs = Number.parseInt(process.env.ARGON_CLAUDE_AGENT_SDK_TIMEOUT_MS || "", 10) || 300_000;
  const maxTurns = Number.parseInt(process.env.ARGON_CLAUDE_SURFACE_MAX_TURNS || "", 10) || 12;
  const model = process.env.ARGON_CLAUDE_SURFACE_MODEL?.trim() || "claude-sonnet-4-6";
  const permissionMode = (process.env.ARGON_CLAUDE_SURFACE_PERMISSION_MODE?.trim() || "acceptEdits") as "default";
  let timeout: NodeJS.Timeout | null = null;
  let text = "";

  try {
    await Promise.race([
      (async () => {
        for await (const message of query({
          prompt: surfacePrompt(input),
          options: {
            abortController,
            cwd: input.sourceDir,
            env: {
              ANTHROPIC_API_KEY: process.env.ANTHROPIC_API_KEY,
              CLAUDE_AGENT_SDK_CLIENT_APP: "polsia-takyon-v3-surface-builder"
            },
            model,
            thinking: { type: "adaptive", display: "omitted" },
            effort: (process.env.ARGON_CLAUDE_SURFACE_EFFORT?.trim() as "low" | "medium" | "high" | "xhigh") || "high",
            tools: ["Read", "Write", "Edit", "MultiEdit", "Grep", "Glob"],
            disallowedTools: ["Bash"],
            permissionMode,
            persistSession: false,
            maxTurns,
            maxBudgetUsd: Number.parseFloat(process.env.ARGON_CLAUDE_SURFACE_MAX_BUDGET_USD || "") || 4,
            canUseTool: async (toolName, toolInput, options) => {
              if (toolName === "Bash") {
                return {
                  behavior: "deny",
                  message: "Bash is disabled inside the SDK surface builder; platform gates run commands after edits.",
                  toolUseID: options.toolUseID
                };
              }
              const paths = pathValues(toolInput);
              const outside = paths.find((value) => !isSubpath(input.sourceDir, value));
              if (outside) {
                return {
                  behavior: "deny",
                  message: "Generated-app surface builder may only access files inside the generated workspace.",
                  toolUseID: options.toolUseID
                };
              }
              return { behavior: "allow", updatedInput: toolInput, toolUseID: options.toolUseID };
            }
          }
        })) {
          text += textFromSdkMessage(message);
        }
      })(),
      new Promise<never>((_, reject) => {
        timeout = setTimeout(() => {
          abortController.abort();
          reject(new Error(`Claude Agent SDK surface builder timed out after ${timeoutMs}ms.`));
        }, timeoutMs);
      })
    ]);
  } finally {
    if (timeout) clearTimeout(timeout);
    abortController.abort();
  }

  return { summary: redact(text).slice(-8000), maxTurns, model };
}

async function readSurfaceFiles(sourceDir: string): Promise<GeneratedFile[]> {
  const files: GeneratedFile[] = [];
  for (const relative of requiredSurfaceFiles) {
    const absolute = path.join(sourceDir, relative);
    const content = await fs.readFile(absolute, "utf8").catch(() => "");
    files.push({ path: relative, content });
  }
  return files;
}

function fileFingerprints(files: GeneratedFile[]) {
  return new Map(files.map((file) => [file.path, crypto.createHash("sha256").update(file.content).digest("hex")]));
}

function changedFiles(before: Map<string, string>, after: GeneratedFile[]) {
  return after
    .filter((file) => file.content.trim() && before.get(file.path) !== crypto.createHash("sha256").update(file.content).digest("hex"))
    .map((file) => file.path);
}

export async function runClaudeSdkSurfaceBuilder(input: {
  rootDir: string;
  company: CompanyBuildInput;
  workflow: SurfaceWorkflow;
  operatorInstruction?: string | null;
}): Promise<SdkSurfaceResult> {
  loadLocalSecrets();
  if (!process.env.ANTHROPIC_API_KEY?.trim()) {
    throw new ConfigurationError("ANTHROPIC_API_KEY is required for Claude Agent SDK generated-app surface builds.");
  }

  const context = await loadGeneratedAppSurfaceContext(input.company);
  const before = fileFingerprints(await readSurfaceFiles(input.rootDir));
  const sdk = await runSdkEdit({
    sourceDir: input.rootDir,
    company: input.company,
    context,
    workflow: input.workflow,
    operatorInstruction: input.operatorInstruction ?? null
  });
  const files = await readSurfaceFiles(input.rootDir);
  const changed = changedFiles(before, files);

  if (/\bBLOCKED:/i.test(sdk.summary)) {
    throw new IntegrationCallError("Claude Agent SDK", sdk.summary.match(/\bBLOCKED:\s*(.+)/i)?.[1]?.trim() || "surface builder blocked");
  }
  if (!changed.length) {
    throw new IntegrationCallError("Claude Agent SDK", "surface builder finished without changing any required customer-surface files");
  }
  const issues = validateGeneratedFiles(files);
  if (issues.length) {
    throw new IntegrationCallError("Claude Agent SDK", `surface builder output failed validation: ${issues.join("; ")}`);
  }

  return {
    source: "claude-agent-sdk",
    model: sdk.model,
    workflow: input.workflow,
    files: changed,
    turnsBudget: sdk.maxTurns,
    summary: sdk.summary,
    context: {
      mission: Boolean(context.mission),
      marketResearch: Boolean(context.marketResearch),
      plans: context.plans.length,
      designBrief: Boolean(context.designBrief)
    }
  };
}
