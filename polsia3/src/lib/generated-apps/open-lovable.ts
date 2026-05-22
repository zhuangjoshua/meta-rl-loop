import fs from "node:fs/promises";
import path from "node:path";
import { db } from "../db";
import { listBusinessDocuments } from "../documents";
import { ConfigurationError, IntegrationCallError } from "../errors";
import { loadLocalSecrets } from "../secrets";
import type { CompanyBuildInput } from "./records";

type OpenLovableWorkflow = "build_site" | "product_ui";

type GeneratedFile = {
  path: string;
  content: string;
};

type JsonRecord = Record<string, unknown>;

type PlanContext = {
  planKey: string;
  tier: string;
  priceUsdCents: number;
  billingInterval: string;
  includedAiBudgetMicrousd: number;
  includedActionQuota: number | null;
  metadata: unknown;
};

export type GeneratedAppSurfaceContext = {
  mission: string | null;
  marketResearch: string | null;
  marketingContext: string | null;
  designBrief: string | null;
  conversionReview: string | null;
  plans: PlanContext[];
  routes: {
    signup: string;
    product: string;
    authRequest: string;
    checkoutStarter: string;
  };
  economics: {
    definitions: string;
    guardrails: string;
  };
};

export const requiredSurfaceFiles = [
  "src/product/module.ts",
  "src/app/page.tsx",
  "src/app/account/page.tsx",
  "src/app/product/page.tsx",
  "src/app/signup/page.tsx",
  "src/app/globals.css"
];
const requiredFiles = requiredSurfaceFiles;

function openLovableEnv() {
  loadLocalSecrets();
  return {
    dir: process.env.OPEN_LOVABLE_DIR?.trim() || "/Users/Zygote/Downloads/open-lovable-main",
    baseUrl: (process.env.OPEN_LOVABLE_BASE_URL?.trim() || "http://127.0.0.1:3001").replace(/\/$/, ""),
    model: process.env.OPEN_LOVABLE_MODEL?.trim() || "anthropic/claude-opus-4-7"
  };
}

function truncate(value: string, length = 12_000) {
  if (value.length <= length) return value;
  return `${value.slice(0, length)}\n[truncated ${value.length - length} chars]`;
}

function compactWhitespace(value: string) {
  return value.replace(/[ \t]+/g, " ").replace(/\n{3,}/g, "\n\n").trim();
}

function safeExcerpt(value: string | null | undefined, length = 5000) {
  const compacted = compactWhitespace(value || "");
  return compacted ? truncate(compacted, length) : null;
}

function metadataSeeded(value: unknown) {
  return Boolean(value && typeof value === "object" && "seeded" in value && (value as { seeded?: unknown }).seeded);
}

function priceLabel(plan: PlanContext) {
  if (plan.priceUsdCents <= 0) return "Free";
  const amount = (plan.priceUsdCents / 100).toLocaleString("en-US", {
    style: "currency",
    currency: "USD",
    minimumFractionDigits: Number.isInteger(plan.priceUsdCents / 100) ? 0 : 2
  });
  return `${amount}/${plan.billingInterval === "year" ? "yr" : "mo"}`;
}

function formatPlans(plans: PlanContext[]) {
  if (plans.length === 0) return "No generated-app plan policy rows were found.";
  return plans
    .map((plan) =>
      [
        `- ${plan.planKey} (${plan.tier}): ${priceLabel(plan)}`,
        `included actions: ${plan.includedActionQuota ?? "not set"}`,
        `included AI budget: ${(plan.includedAiBudgetMicrousd / 1_000_000).toFixed(2)} USD-equivalent/month`
      ].join("; ")
    )
    .join("\n");
}

function contextForPrompt(context: GeneratedAppSurfaceContext | null | undefined) {
  if (!context) return "No business context was loaded.";
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
    context.designBrief || "No Business Product Design document found.",
    "",
    "Takyon conversion review:",
    context.conversionReview || "No Business Conversion Review document found.",
    "",
    "Generated-app pricing and limits from generated_app_plan_policies:",
    formatPlans(context.plans),
    "",
    "Auth/payment routes the generated customer surface may link to:",
    `- signup page: ${context.routes.signup}`,
    `- product page: ${context.routes.product}`,
    `- magic-link request: ${context.routes.authRequest}`,
    `- starter checkout: ${context.routes.checkoutStarter}`,
    "",
    "Economics definitions:",
    context.economics.definitions,
    "",
    "Economics guardrails:",
    context.economics.guardrails
  ].join("\n");
}

export async function loadGeneratedAppSurfaceContext(company: CompanyBuildInput): Promise<GeneratedAppSurfaceContext> {
  const sql = db();
  const [documents, plans] = await Promise.all([
    listBusinessDocuments(company.id, 40),
    sql<PlanContext[]>`
      SELECT
        plan_key AS "planKey",
        tier,
        price_usd_cents AS "priceUsdCents",
        billing_interval AS "billingInterval",
        included_ai_budget_microusd AS "includedAiBudgetMicrousd",
        included_action_quota AS "includedActionQuota",
        metadata
      FROM generated_app_plan_policies
      WHERE business_id = ${company.id}
      ORDER BY price_usd_cents ASC, plan_key ASC
    `
  ]);

  const realDocuments = documents.filter((document) => !metadataSeeded(document.metadata));
  const mission = realDocuments.find((document) => document.kind === "mission" || /^mission$/i.test(document.title));
  const marketResearch = realDocuments.find((document) => document.kind === "research_report" || /market research/i.test(document.title));
  const marketingContext = realDocuments.find((document) => /business marketing context|product marketing context/i.test(document.title));
  const designBrief = realDocuments.find((document) => /business product design|product design|design brief/i.test(document.title));
  const conversionReview = realDocuments.find((document) => /business conversion review|conversion review/i.test(document.title));

  return {
    mission: safeExcerpt(mission?.content, 5500),
    marketResearch: safeExcerpt(marketResearch?.content, 7000),
    marketingContext: safeExcerpt(marketingContext?.content, 5000),
    designBrief: safeExcerpt(designBrief?.content, 6500),
    conversionReview: safeExcerpt(conversionReview?.content, 4500),
    plans,
    routes: {
      signup: "/signup",
      product: "/product",
      authRequest: `/api/generated-apps/${company.slug}/auth/request`,
      checkoutStarter: `/api/generated-apps/${company.slug}/checkout?plan=starter`
    },
    economics: {
      definitions:
        "Revenue is generated-app customer revenue before CAC and COGS. CAC is customer acquisition cost. COGS includes AI model/API cost, email, browser/runtime, storage, and compute. Profit is Revenue - CAC - COGS.",
      guardrails:
        "Paid generated-app users reserve finite included AI allowance first. Free or anonymous users can use only leftover funded project AI wallet after paid-user reserves and safety buffer. Generated apps call the platform AI gateway with a project-scoped key and never receive raw provider keys."
    }
  };
}

async function fetchWithTimeout(url: URL, init: RequestInit = {}, timeoutMs = 20 * 60_000) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    return await fetch(url, { ...init, signal: controller.signal });
  } finally {
    clearTimeout(timer);
  }
}

async function readResponseText(response: Response) {
  try {
    return await response.text();
  } catch {
    return "";
  }
}

async function assertOpenLovableReady() {
  const env = openLovableEnv();
  const stat = await fs.stat(env.dir).catch(() => null);
  if (!stat?.isDirectory()) {
    throw new ConfigurationError(`OPEN_LOVABLE_DIR is missing or not a directory: ${env.dir}`);
  }
  if (!process.env.ANTHROPIC_API_KEY?.trim()) {
    throw new ConfigurationError("ANTHROPIC_API_KEY is required for the OpenLovable Claude builder.");
  }

  try {
    const response = await fetchWithTimeout(new URL("/api/sandbox-status", env.baseUrl), undefined, 5000);
    if (!response.ok) {
      throw new IntegrationCallError("OpenLovable", `/api/sandbox-status returned ${response.status}: ${await readResponseText(response)}`);
    }
  } catch (error) {
    if (error instanceof IntegrationCallError) throw error;
    throw new ConfigurationError(`OpenLovable is not reachable at ${env.baseUrl}. Start it locally before running generated-app builds.`);
  }
}

async function postJson(baseUrl: string, route: string, body: unknown) {
  const response = await fetchWithTimeout(
    new URL(route, baseUrl),
    {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(body)
    },
    20 * 60_000
  );
  if (!response.ok) {
    throw new IntegrationCallError("OpenLovable", `${route} returned ${response.status}: ${await readResponseText(response)}`);
  }
  return response;
}

async function resetOpenLovableConversation(baseUrl: string) {
  const response = await fetchWithTimeout(
    new URL("/api/conversation-state", baseUrl),
    { method: "DELETE" },
    5000
  ).catch(() => null);
  if (response && !response.ok && response.status !== 404) {
    throw new IntegrationCallError("OpenLovable", `/api/conversation-state reset returned ${response.status}: ${await readResponseText(response)}`);
  }
}

async function readSse(response: Response) {
  if (!response.body) throw new IntegrationCallError("OpenLovable", "generation stream returned no body");
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  const events: JsonRecord[] = [];
  let buffer = "";
  let dataLines: string[] = [];

  function flushEvent() {
    if (dataLines.length === 0) return;
    const raw = dataLines.join("\n");
    dataLines = [];
    try {
      events.push(JSON.parse(raw) as JsonRecord);
    } catch {
      events.push({ type: "raw", raw });
    }
  }

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let newlineIndex = buffer.indexOf("\n");
    while (newlineIndex !== -1) {
      const line = buffer.slice(0, newlineIndex).replace(/\r$/, "");
      buffer = buffer.slice(newlineIndex + 1);
      if (line === "") {
        flushEvent();
      } else if (line.startsWith("data:")) {
        dataLines.push(line.slice(5).trimStart());
      }
      newlineIndex = buffer.indexOf("\n");
    }
  }
  if (buffer.trim().startsWith("data:")) dataLines.push(buffer.trim().slice(5).trimStart());
  flushEvent();
  return events;
}

function latestGeneratedCode(events: JsonRecord[]) {
  const complete = [...events].reverse().find((event) => event.type === "complete");
  return typeof complete?.generatedCode === "string" ? complete.generatedCode : "";
}

function extractGeneratedFiles(code: string): GeneratedFile[] {
  const files: GeneratedFile[] = [];
  const pattern = /<file\s+path=["']([^"']+)["']\s*>([\s\S]*?)<\/file>/g;
  let match: RegExpExecArray | null;
  while ((match = pattern.exec(code)) !== null) {
    files.push({
      path: match[1].replace(/^\/+/, ""),
      content: match[2].trim()
    });
  }
  return files;
}

function sanitizeGeneratedFile(file: GeneratedFile): GeneratedFile {
  let content = file.content;
  if (file.path === "src/app/page.tsx" || file.path === "src/app/product/page.tsx") {
    content = content
      .replace(/^\s*import\s+["'][./@a-zA-Z0-9_-]*globals\.css["'];?\s*$/gm, "")
      .replace(/\n{3,}/g, "\n\n")
      .trim();
  }
  return { ...file, content };
}

function normalizeGeneratedFiles(files: GeneratedFile[]) {
  const normalized = new Map<string, GeneratedFile>();
  for (const file of files) {
    normalized.set(file.path, sanitizeGeneratedFile(file));
  }
  return [...normalized.values()];
}

function coerceSingleFile(targetFile: string, generatedCode: string): GeneratedFile | null {
  const trimmed = generatedCode.trim();
  if (!trimmed) return null;
  const fence = trimmed.match(/```(?:tsx|ts|css|typescript|javascript)?\s*([\s\S]*?)```/i);
  const content = (fence?.[1] || trimmed)
    .replace(/^<file\s+path=["'][^"']+["']\s*>/i, "")
    .replace(/<\/file>\s*$/i, "")
    .trim();
  if (!content) return null;
  if (targetFile.endsWith(".css") && !/[{}]/.test(content)) return null;
  if ((targetFile.endsWith(".tsx") || targetFile.endsWith(".ts")) && !/\b(export|import|function|const)\b/.test(content)) return null;
  return { path: targetFile, content };
}

function isSubpath(root: string, relativePath: string) {
  const absolute = path.resolve(root, relativePath);
  const relative = path.relative(root, absolute);
  return relative === "" || (!relative.startsWith("..") && !path.isAbsolute(relative));
}

function escapeRegExp(value: string) {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

function extractClassNames(code: string) {
  const classes = new Set<string>();
  const quoted = /className\s*=\s*["']([^"']+)["']/g;
  let match: RegExpExecArray | null;
  while ((match = quoted.exec(code)) !== null) {
    for (const item of match[1].split(/\s+/)) {
      const cleaned = item.trim();
      if (/^[a-zA-Z][a-zA-Z0-9_-]*$/.test(cleaned)) classes.add(cleaned);
    }
  }

  const template = /className\s*=\s*{\s*`([^`]+)`\s*}/g;
  while ((match = template.exec(code)) !== null) {
    for (const item of match[1].split(/\s+/)) {
      const cleaned = item.trim();
      if (/^[a-zA-Z][a-zA-Z0-9_-]*$/.test(cleaned)) classes.add(cleaned);
    }
  }
  return classes;
}

function selectorCoverage(files: Map<string, string>) {
  const css = files.get("src/app/globals.css") || "";
  const pageFiles = ["src/app/page.tsx", "src/app/product/page.tsx", "src/app/signup/page.tsx"];
  const usedClasses = new Set<string>();
  for (const file of pageFiles) {
    for (const className of extractClassNames(files.get(file) || "")) {
      usedClasses.add(className);
    }
  }
  const missing = [...usedClasses].filter((className) => !new RegExp(`\\.${escapeRegExp(className)}(?![a-zA-Z0-9_-])`).test(css));
  const covered = usedClasses.size - missing.length;
  return {
    used: usedClasses.size,
    covered,
    missing,
    ratio: usedClasses.size === 0 ? 1 : covered / usedClasses.size
  };
}

function cssCompletenessIssues(css: string) {
  const issues: string[] = [];
  let balance = 0;
  for (const char of css) {
    if (char === "{") balance += 1;
    if (char === "}") balance -= 1;
    if (balance < 0) {
      issues.push("generated CSS has an extra closing brace");
      break;
    }
  }
  if (balance !== 0) issues.push(`generated CSS has unbalanced braces (${balance})`);
  const tail = css.trim().slice(-120);
  if (/[,{]\s*(?:\.[a-zA-Z0-9_-]*)?$/.test(tail) || /\.[a-zA-Z0-9_-]*$/.test(tail)) {
    issues.push("generated CSS appears truncated mid-selector");
  }
  return issues;
}

export function validateGeneratedFiles(files: GeneratedFile[]) {
  const issues: string[] = [];
  const fileMap = new Map(files.map((file) => [file.path, file.content]));
  const joined = files.map((file) => file.content).join("\n");
  const visibleJoined = ["src/app/page.tsx", "src/app/product/page.tsx", "src/app/signup/page.tsx", "src/app/globals.css"]
    .map((file) => fileMap.get(file) || "")
    .join("\n");
  const allowed = new Set(requiredFiles);

  for (const file of requiredFiles) {
    if (!fileMap.has(file)) issues.push(`missing required file ${file}`);
  }
  for (const file of files) {
    if (!allowed.has(file.path)) issues.push(`unexpected file ${file.path}; generated frontend may only edit required customer-surface files`);
  }

  const visibleForbidden = [
    /\bNow building\b/i,
    /\bbackend receipt\b/i,
    /\bstructured product result\b/i,
    /\bTakyon\b/i,
    /\bFour Manifold\b/i,
    /\bimplementation plan\b/i,
    /\bproduct plan\b/i,
    /\bE2E\s*\d+/i,
    /\bmock(?:ed)?\b/i,
    /\bsimulat(?:e|ed|ion)\b/i,
    /\bdummy\b/i,
    /\bfake\b/i,
    /\bTrusted by\b/i,
    /\bAcme\b/i,
    /\bexample\.com\b/i,
    /\bhref=["']#[^"']*["']/i,
    /\bruns?\s+real\s+browser/i,
    /\breal\s+browser\s+sessions\b/i,
    /\baround\s+the\s+clock\b/i,
    /\bthe\s+moment\s+they\s+appear\b/i,
    /\bexecutes?\s+(it|the flow|the journey|real browser|browser sessions)\b/i
  ];
  const joinedForbidden = [
    /\bexample\.com\b/i,
    /\bExample:/i,
    /\bmock(?:ed)?\b/i,
    /\bsimulat(?:e|ed|ion)\b/i,
    /\bdummy\b/i,
    /\bfake\b/i,
    /\bTrusted by\b/i,
    /\bAcme\b/i,
    /\bruns?\s+real\s+browser/i,
    /\breal\s+browser\s+sessions\b/i,
    /\baround\s+the\s+clock\b/i,
    /\bthe\s+moment\s+they\s+appear\b/i,
    /\bexecutes?\s+(it|the flow|the journey|real browser|browser sessions)\b/i
  ];
  const forbiddenCode = [
    /\bANTHROPIC_API_KEY\b/,
    /\bOPENAI_API_KEY\b/,
    /\bSTRIPE_SECRET\b/,
    /\bprocess\.env\b/
  ];
  if (visibleForbidden.some((pattern) => pattern.test(visibleJoined))) {
    issues.push("visible customer surface contains internal or fake/demo language");
  }
  if (joinedForbidden.some((pattern) => pattern.test(joined))) {
    issues.push("generated files contain placeholder/demo language or unsupported live-execution claims");
  }
  if (forbiddenCode.some((pattern) => pattern.test(joined))) {
    issues.push("generated files contain forbidden secrets or environment access");
  }

  const productPage = fileMap.get("src/app/product/page.tsx") || "";
  const homePage = fileMap.get("src/app/page.tsx") || "";
  const signupPage = fileMap.get("src/app/signup/page.tsx") || "";
  const css = fileMap.get("src/app/globals.css") || "";
  if (!productPage.includes("/api/product/run")) {
    issues.push("product page does not call /api/product/run");
  }
  if (!/href=["']\/signup["']/.test(homePage)) {
    issues.push("homepage must include a visible /signup CTA");
  }
  if (!/href=["']\/product["']/.test(homePage)) {
    issues.push("homepage must include a visible /product CTA");
  }
  if (!/requestMagicLink/.test(signupPage)) {
    issues.push("signup page must call the platform requestMagicLink helper");
  }
  if (!/generatedAppCheckoutUrl|checkout\?plan=starter/.test(joined)) {
    issues.push("generated app must expose the starter checkout path from platform rails");
  }
  if (!/export\s+(const|default)\s+productModule/.test(fileMap.get("src/product/module.ts") || "")) {
    issues.push("src/product/module.ts must export productModule");
  }
  if (!/systemPrompt/.test(fileMap.get("src/product/module.ts") || "")) {
    issues.push("product module must include a systemPrompt for platform AI execution");
  }
  const coverage = selectorCoverage(fileMap);
  if (coverage.used < 10 || coverage.ratio < 0.85) {
    issues.push(
      `generated CSS does not cover JSX classNames (${coverage.covered}/${coverage.used} covered; missing: ${coverage.missing
        .slice(0, 12)
        .join(", ")})`
    );
  }
  if (!/@media\b/.test(css)) {
    issues.push("generated CSS must include responsive media rules");
  }
  if (!/\b(display:\s*(grid|flex)|grid-template-columns|flex-direction)\b/i.test(css) || !/\bborder-radius\b/i.test(css)) {
    issues.push("generated CSS is too thin to pass the visual quality gate");
  }
  issues.push(...cssCompletenessIssues(css));

  return [...new Set(issues)];
}

function buildPrompt(input: {
  company: CompanyBuildInput;
  workflow: OpenLovableWorkflow;
  context?: GeneratedAppSurfaceContext | null;
}) {
  const workflowLabel = input.workflow === "product_ui" ? "product UI/module refinement" : "initial website and product app";
  return [
    "You are OpenLovable generating the customer-facing app surface for a real AI micro-SaaS.",
    "",
    "Return XML file blocks only. No markdown outside file blocks.",
    "Generate exactly these files and no others:",
    ...requiredFiles.map((file) => `- ${file}`),
    "Generate the five files as one coherent surface. The JSX class names and CSS selectors must match exactly.",
    "Keep the total output compact enough to finish in one response. Target under 650 lines across all files.",
    "Each page should be polished but concise: homepage, signup/pricing, and product workflow.",
    "",
    "This is NOT a generic template exercise. Build the actual website and product workflow for the company below.",
    "The page must look like a finished modern B2B SaaS product, with specific positioning, real domain language, and a usable product workflow.",
    "Do not mention scaffolding, backend receipts, build plans, Takyon, Four Manifold, Stripe, X, Meta, internal queues, or implementation status.",
    "Do not include fake customers, fake metrics, fake logos, demo datasets, mock data, or href=\"#\" links.",
    "Do not claim live browser execution, continuous monitoring, around-the-clock checks, observed failures, real integrations, or automatic vendor side effects unless they are present in the platform context below.",
    "Do not read secrets or use process.env. Do not invent auth, payment, analytics, social posting, database, or vendor integrations.",
    "Use plain React/Next.js and plain CSS. No Tailwind utility-only styling, no external packages, no image assets.",
    "Avoid complex parsers, long option arrays, long compliance lists, and verbose CSS. Prefer simple, robust UI that compiles.",
    "Use short, stable class names and define every className selector in src/app/globals.css.",
    "",
    "Deterministic platform rails already exist and must be used:",
    "- src/app/api/product/run/route.ts exists and must stay platform-owned.",
    "- src/lib/platform-client.ts exists and must stay platform-owned.",
    "- The product page must POST visitor input to /api/product/run.",
    "- src/lib/platform-client.ts exports requestMagicLink(email) and generatedAppCheckoutUrl(planKey).",
    "- The platform route handles auth/session, entitlements, AI gateway execution, usage limits, persistence, and receipts.",
    "",
    "src/product/module.ts contract:",
    "- export const productModule = { ... }",
    "- include: productName, category, actionLabel, inputLabel, inputPlaceholder, resultLabel, systemPrompt, outputInstructions",
    "- systemPrompt must define the real product behavior for this company and forbid internal/vendor/build language.",
    "",
    "src/app/signup/page.tsx requirements:",
    "- It must be a client component.",
    "- It must import requestMagicLink and generatedAppCheckoutUrl from '@/lib/platform-client'.",
    "- It must collect a work email, call requestMagicLink(email), and render success/error states.",
    "- It must show the real starter/free plan positioning from the plan policy context.",
    "- It must include a checkout CTA using generatedAppCheckoutUrl('starter') or an anchor to that URL.",
    "",
    "src/app/product/page.tsx requirements:",
    "- It may be a client component.",
    "- It should import productModule from '@/product/module' when labels/copy are needed.",
    "- It must collect a work email and one substantial product-specific input.",
    "- It must call fetch('/api/product/run', { method: 'POST', headers: {'content-type':'application/json'}, body: JSON.stringify({ email, brief }) }).",
    "- It must render loading, success, and blocked/error states as customer-facing product states.",
    "",
    "src/app/page.tsx requirements:",
    "- It must be a polished customer-facing website for this specific business.",
    "- It must use the Mission and Market Research context below.",
    "- It must include clear /signup and /product CTAs.",
    "- It must include mission, proof/market pain, pricing/plan, and product workflow sections.",
    "- It must avoid oversized broken text and fit on desktop/mobile.",
    "",
    `Workflow: ${workflowLabel}`,
    "",
    "Business, research, pricing, auth, and economics context:",
    contextForPrompt(input.context),
    "",
    "Company:",
    JSON.stringify(
      {
        id: input.company.id,
        name: input.company.name.replace(/\s+E2E\s+\d+/gi, "").trim(),
        slug: input.company.slug,
        publicPitch: input.company.public_pitch,
        customer: input.company.customer,
        pain: input.company.pain,
        offer: input.company.offer
      },
      null,
      2
    )
  ].join("\n");
}

function buildRepairPrompt(input: {
  company: CompanyBuildInput;
  workflow: OpenLovableWorkflow;
  context?: GeneratedAppSurfaceContext | null;
  issues: string[];
}) {
  return [
    buildPrompt({ company: input.company, workflow: input.workflow, context: input.context }),
    "",
    "The previous OpenLovable/Claude response was rejected by the platform validator.",
    "Rejection reasons:",
    JSON.stringify(input.issues, null, 2),
    "",
    "Repair instructions:",
    "- Return all five required files again, complete and untruncated.",
    "- Keep each file compact and focused; avoid long feature sections or elaborate parsers.",
    "- Target under 650 lines total across all files.",
    "- Do not use href=\"#...\" anchors; links must go to real routes such as /product or /.",
    "- Define every className used in page JSX inside src/app/globals.css.",
    "- Keep CSS concise but polished.",
    "- The homepage must include clear /signup and /product CTAs.",
    "- The signup page must call requestMagicLink(email) and include the starter checkout URL.",
    "- The product page must include the fetch('/api/product/run'...) call.",
    "- The module file must export productModule and include systemPrompt.",
    "- Return only XML file blocks."
  ].join("\n");
}

function buildSingleFilePrompt(input: {
  company: CompanyBuildInput;
  workflow: OpenLovableWorkflow;
  context?: GeneratedAppSurfaceContext | null;
  targetFile: string;
  previousIssue?: string;
}) {
  const companyName = input.company.name.replace(/\s+E2E\s+\d+/gi, "").trim();
  const targetGuide: Record<string, string> = {
    "src/product/module.ts": [
      `Write the productModule contract only for ${companyName}.`,
      "Keep it under 80 lines.",
      "Make the product behavior a real AI-powered workflow for the specific company pitch.",
      "The systemPrompt must produce useful output from the visitor's submitted input, but must not claim live browsing, vendor posting, payment, database, or monitoring side effects it has not actually performed.",
      "Do not use example domains, sample customers, fake findings, or fake metrics."
    ].join("\n"),
    "src/app/globals.css": [
      "Write concise, polished CSS for the homepage, signup page, and product page already present in currentFiles.",
      "Keep it under 180 lines. Do not exceed 12,000 characters.",
      "Define every className used in currentFiles. This is a hard validator gate.",
      "Prefer grouped selectors and compact rules. Finish every @media block and selector completely.",
      "Avoid comments. Avoid gradients as the main design language. Use a restrained, finished B2B SaaS interface."
    ].join("\n"),
    "src/app/page.tsx": [
      "Write the polished homepage only.",
      "Keep it under 120 lines.",
      "Use CSS classes only; no inline style objects.",
      "No in-page hash links.",
      "It must include clear links to /signup and /product.",
      "It must use the Mission and Market Research context, including pricing and product positioning.",
      "Use specific customer-facing copy from the company pitch, with a compact hero, proof/market pain, pricing, product workflow, and CTA sections."
    ].join("\n"),
    "src/app/product/page.tsx": [
      "Write the customer-facing product workflow only.",
      "Keep it under 170 lines.",
      "Use CSS classes only; no inline style objects.",
      "Import productModule from '@/product/module'.",
      "It must collect a work email and one substantial product-specific input.",
      "It must call fetch('/api/product/run', { method: 'POST', headers: {'content-type':'application/json'}, body: JSON.stringify({ email, brief }) }).",
      "It must render loading, success, and blocked/error states without mentioning backend receipts, queues, vendors, or implementation status."
    ].join("\n"),
    "src/app/signup/page.tsx": [
      "Write the customer-facing signup/pricing page only.",
      "Keep it under 150 lines.",
      "Use CSS classes only; no inline style objects.",
      "Import requestMagicLink and generatedAppCheckoutUrl from '@/lib/platform-client'.",
      "It must collect a work email and call requestMagicLink(email).",
      "It must render success/error states without pretending payment or auth completed.",
      "It must show the Free and Starter plan in customer-facing language using the plan policy context.",
      "It must include a checkout link created with generatedAppCheckoutUrl('starter')."
    ].join("\n")
  };
  return [
    "You are OpenLovable generating one customer-facing file for a real AI micro-SaaS.",
    "",
    `Return exactly one XML file block and nothing else: <file path="${input.targetFile}">...</file>.`,
    "Do not return any other file. Do not include markdown outside the file block.",
    "Complete the file fully with compact, compiling code.",
    "Use plain React/Next.js and plain CSS. No external packages, no image assets, no Tailwind-only styling.",
    "Use CSS class names, not inline style objects, for page layout and styling.",
    "Do not mention scaffolding, backend receipts, build plans, Takyon, Four Manifold, Stripe, X, Meta, internal queues, or implementation status.",
    "Do not include fake customers, fake metrics, fake logos, demo datasets, mock data, example.com, or href=\"#\" links.",
    "Do not use Example: placeholders or named sample vendors. Ask for the visitor's actual URL/tool/provider details instead.",
    "Do not read secrets or use process.env. Do not invent auth, payment, analytics, social posting, database, or vendor integrations.",
    "For this slice, the product runtime is an AI flow audit/planning workflow. It does not run a live browser, schedule monitoring, observe production, or execute website checks. Do not claim continuous monitoring, real browser execution, observed findings, or around-the-clock detection.",
    "The root layout already imports src/app/globals.css. Page files must not import globals.css.",
    "Existing deterministic rails own auth/session, entitlements, AI gateway execution, usage limits, persistence, receipts, and deployment.",
    "The product page must POST to /api/product/run; that route already exists and must not be regenerated.",
    "The product module must export productModule with productName, category, actionLabel, inputLabel, inputPlaceholder, resultLabel, systemPrompt, and outputInstructions.",
    "Use the business context below; do not ignore pricing, mission, market research, auth, or checkout routes.",
    "",
    targetGuide[input.targetFile],
    input.previousIssue ? `Previous rejection for this file: ${input.previousIssue}` : "",
    "",
    "Business, research, pricing, auth, and economics context:",
    contextForPrompt(input.context),
    "",
    "Company:",
    JSON.stringify(
      {
        id: input.company.id,
        name: companyName,
        slug: input.company.slug,
        publicPitch: input.company.public_pitch,
        customer: input.company.customer,
        pain: input.company.pain,
        offer: input.company.offer
      },
      null,
      2
    )
  ]
    .filter(Boolean)
    .join("\n");
}

async function generateOpenLovableFiles(input: {
  baseUrl: string;
  model: string;
  prompt: string;
  currentFiles: Record<string, string>;
  workflow: OpenLovableWorkflow;
  isEdit: boolean;
}) {
  const events = await readSse(
    await postJson(input.baseUrl, "/api/generate-ai-code-stream", {
      prompt: input.prompt,
      model: input.model,
      context: {
        currentFiles: input.currentFiles,
        workflowId: input.workflow
      },
      isEdit: input.isEdit
    })
  );
  const generatedCode = latestGeneratedCode(events);
  if (!generatedCode) throw new IntegrationCallError("OpenLovable", "generation stream completed without generatedCode");
  return { events, generatedCode, files: extractGeneratedFiles(generatedCode) };
}

async function generateOneFile(input: {
  baseUrl: string;
  model: string;
  currentFiles: Record<string, string>;
  company: CompanyBuildInput;
  workflow: OpenLovableWorkflow;
  context?: GeneratedAppSurfaceContext | null;
  targetFile: string;
  previousIssue?: string;
}) {
  await resetOpenLovableConversation(input.baseUrl);
  const generated = await generateOpenLovableFiles({
    baseUrl: input.baseUrl,
    model: input.model,
    prompt: buildSingleFilePrompt({
      company: input.company,
      workflow: input.workflow,
      context: input.context,
      targetFile: input.targetFile,
      previousIssue: input.previousIssue
    }),
    currentFiles: input.currentFiles,
    workflow: input.workflow,
    isEdit: false
  });
  const target = generated.files.find((file) => file.path === input.targetFile);
  const coerced = target ?? coerceSingleFile(input.targetFile, generated.generatedCode);
  if (!coerced) {
    throw new IntegrationCallError(
      "OpenLovable",
      `single-file generation for ${input.targetFile} did not return the requested file`
    );
  }
  return { file: coerced, events: generated.events, generatedCodeChars: generated.generatedCode.length };
}

async function generateFileSet(input: {
  baseUrl: string;
  model: string;
  company: CompanyBuildInput;
  workflow: OpenLovableWorkflow;
  context?: GeneratedAppSurfaceContext | null;
  previousIssue?: string;
}) {
  const assembled: GeneratedFile[] = [];
  const currentFiles: Record<string, string> = {};
  let eventCount = 0;
  let generatedCodeChars = 0;

  for (const targetFile of requiredFiles) {
    const result = await generateOneFile({
      baseUrl: input.baseUrl,
      model: input.model,
      currentFiles,
      company: input.company,
      workflow: input.workflow,
      context: input.context,
      targetFile,
      previousIssue: input.previousIssue
    });
    const file = sanitizeGeneratedFile(result.file);
    assembled.push(file);
    currentFiles[file.path] = file.content;
    eventCount += result.events.length;
    generatedCodeChars += result.generatedCodeChars;
  }

  return {
    events: Array.from({ length: eventCount }, (_, index) => ({ type: "coordinated-file", index })),
    generatedCode: "x".repeat(generatedCodeChars),
    files: normalizeGeneratedFiles(assembled)
  };
}

export async function applyOpenLovableSurface(input: {
  rootDir: string;
  company: CompanyBuildInput;
  workflow: OpenLovableWorkflow;
  context?: GeneratedAppSurfaceContext | null;
}) {
  await assertOpenLovableReady();
  const env = openLovableEnv();
  const context = input.context ?? (await loadGeneratedAppSurfaceContext(input.company));
  let generated = await generateFileSet({
    baseUrl: env.baseUrl,
    model: env.model,
    company: input.company,
    workflow: input.workflow,
    context
  });

  for (const file of generated.files) {
    if (!isSubpath(input.rootDir, file.path)) {
      throw new IntegrationCallError("OpenLovable", `refused path outside generated app: ${file.path}`);
    }
    const absolute = path.join(input.rootDir, file.path);
    await fs.mkdir(path.dirname(absolute), { recursive: true });
    await fs.writeFile(absolute, `${file.content}\n`, "utf8");
  }

  return {
    source: "open-lovable-local",
    baseUrl: env.baseUrl,
    model: env.model,
    workflow: input.workflow,
    eventCount: generated.events.length,
    generatedCodeChars: generated.generatedCode.length,
    files: generated.files.map((file) => file.path),
    context: {
      mission: Boolean(context.mission),
      marketResearch: Boolean(context.marketResearch),
      plans: context.plans.map((plan) => ({
        planKey: plan.planKey,
        tier: plan.tier,
        priceUsdCents: plan.priceUsdCents,
        includedActionQuota: plan.includedActionQuota,
        includedAiBudgetMicrousd: plan.includedAiBudgetMicrousd
      }))
    },
    summary: truncate(
      JSON.stringify(
        {
          files: generated.files.map((file) => file.path),
          eventCount: generated.events.length,
          context: { mission: Boolean(context.mission), marketResearch: Boolean(context.marketResearch), plans: context.plans.length }
        },
        null,
        2
      ),
      2000
    )
  };
}
