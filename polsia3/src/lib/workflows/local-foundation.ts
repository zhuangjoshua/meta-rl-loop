import { randomUUID } from "node:crypto";
import { z } from "zod";
import { db } from "../db";
import { getLocalFoundationEnv } from "../env";
import { IntegrationCallError, IntegrationNotConfiguredError } from "../errors";
import { toJson } from "../json";

type BusinessContext = {
  id: string;
  name: string;
  slug: string;
  status: string;
  site_slug: string;
  site_status: string;
  public_title: string;
  public_pitch: string;
  site_config?: unknown;
};

type LocalFoundationInput = {
  business: BusinessContext;
  workflowId: string;
  workflowTitle: string;
  operatorPrompt?: string;
  skillInstructions: string;
};

type Evidence = {
  query: string;
  title: string;
  url: string;
  content: string;
  score?: number;
};

function asRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" && !Array.isArray(value) ? (value as Record<string, unknown>) : null;
}

function stringField(record: Record<string, unknown> | null, key: string) {
  const value = record?.[key];
  return typeof value === "string" ? value.trim() : "";
}

function stripInternalPromptNoise(value: string) {
  return value
    .replace(/\bArgon(?:-style)?\b/gi, "autonomous")
    .replace(/\bH(?:ermes)\b/gi, "")
    .replace(/\bPolsia\s*\d*\b/gi, "")
    .replace(/\bpolsia\d+\b/gi, "")
    .replace(/\bTakyon\b/gi, "")
    .replace(/\bBHPV3\b/gi, "")
    .replace(/\bRun the autonomous .*?foundation\b/gi, "")
    .replace(/\bDo not ask the founder[^.]*\./gi, "")
    .replace(/\bStart from the raw founder idea\b/gi, "")
    .replace(/\bspecialize the concept\b/gi, "")
    .replace(/\bBuild the real foundation first\.?/gi, "")
    .replace(/\bDo not create placeholder reports\.?/gi, "")
    .replace(/\s+/g, " ")
    .trim();
}

function sanitizeSkillInstructions(value: string) {
  return value
    .replace(/^#\s+Takyon Company Skill:/gim, "# Company Skill:")
    .replace(/\bproduct-owned Takyon company skill\b/gi, "product-owned company skill")
    .replace(/\bTakyon\b/g, "the platform")
    .replace(/\bPolsia(?:-style)?\b/gi, "operator")
    .replace(/\bArgon\b/gi, "the runtime")
    .replace(/\bfixed workflow cockpit\b/gi, "workflow runner");
}

function meaningfulBusinessName(name: string) {
  const normalized = name.trim();
  if (!normalized) return "";
  if (/^(underrock|untitled|new company|test|demo)$/i.test(normalized)) return "";
  return normalized;
}

function researchSeed(input: LocalFoundationInput) {
  const siteConfig = asRecord(input.business.site_config);
  const onboarding = asRecord(siteConfig?.onboarding);
  const foundation = asRecord(siteConfig?.foundation);
  const foundationOutput = asRecord(foundation?.output);
  const rawIdea = stringField(onboarding, "raw_idea");
  const oneLiner = stringField(foundationOutput, "one_liner");
  const targetCustomer = stringField(foundationOutput, "target_customer");
  const pain = stringField(foundationOutput, "pain");
  const offer = stringField(foundationOutput, "offer");
  const firstWorkflow = stringField(foundationOutput, "first_workflow");
  const cleanedOperatorPrompt = stripInternalPromptNoise(input.operatorPrompt || "");
  const businessName = meaningfulBusinessName(input.business.name);
  const publicTitle = meaningfulBusinessName(input.business.public_title);
  const parts = [
    rawIdea,
    oneLiner,
    targetCustomer,
    pain,
    offer,
    firstWorkflow,
    input.business.public_pitch,
    cleanedOperatorPrompt,
    businessName,
    publicTitle
  ]
    .map((part) => stripInternalPromptNoise(part))
    .filter((part, index, all) => part && all.indexOf(part) === index);

  return parts.join(" ").slice(0, 240) || "early stage SaaS product";
}

const generatedFoundationSchema = z
  .object({
    specialized_name: z.string().optional(),
    one_liner: z.string().optional(),
    target_customer: z.string().optional(),
    pain: z.string().optional(),
    offer: z.string().optional(),
    first_workflow: z.string().optional(),
    pricing_hypothesis: z.string().optional(),
    site_requirements: z.union([z.string(), z.array(z.string())]).optional(),
    seo_targets: z.array(z.string()).optional(),
    buying_intent: z.array(z.string()).optional(),
    competitors: z.array(z.unknown()).optional(),
    pain_evidence: z.array(z.unknown()).optional(),
    pricing_evidence: z.array(z.unknown()).optional(),
    first_outreach_angles: z.array(z.string()).optional(),
    risks: z.array(z.string()).optional(),
    next_fixed_workflows: z.array(z.string()).optional(),
    summary: z.string().optional()
  })
  .passthrough();

function hasEnv(value: string) {
  return value.trim().length > 0;
}

async function parseJsonResponse(response: Response) {
  const text = await response.text();
  if (!text) return null;
  try {
    return JSON.parse(text) as unknown;
  } catch {
    return { raw: text };
  }
}

async function tavilySearch(query: string, apiKey: string) {
  const response = await fetch("https://api.tavily.com/search", {
    method: "POST",
    headers: {
      Authorization: `Bearer ${apiKey}`,
      "Content-Type": "application/json"
    },
    body: JSON.stringify({
      query,
      search_depth: "advanced",
      include_answer: true,
      include_raw_content: false,
      max_results: 5
    }),
    signal: AbortSignal.timeout(30_000)
  });

  const body = await parseJsonResponse(response);
  if (!response.ok) {
    throw new IntegrationCallError("Tavily", `${response.status} ${JSON.stringify(body)}`, response.status);
  }

  return body as {
    answer?: string;
    results?: Array<{
      title?: string;
      url?: string;
      content?: string;
      score?: number;
    }>;
  };
}

async function collectEvidence(input: LocalFoundationInput, tavilyApiKey: string) {
  const seed = researchSeed(input);
  const queries = [
    `${seed} SaaS market pain points competitors`.slice(0, 350),
    `${seed} online buying intent SEO keywords pricing`.slice(0, 350),
    `${seed} forums customer complaints alternatives`.slice(0, 350)
  ];

  const searches = await Promise.all(queries.map((query) => tavilySearch(query, tavilyApiKey)));
  const evidence: Evidence[] = [];

  searches.forEach((search, index) => {
    for (const result of search.results ?? []) {
      if (!result.url || !result.title) continue;
      evidence.push({
        query: queries[index],
        title: result.title,
        url: result.url,
        content: result.content ?? "",
        score: result.score
      });
    }
  });

  return {
    queries,
    answers: searches.map((search, index) => ({ query: queries[index], answer: search.answer ?? "" })),
    evidence: evidence.slice(0, 12)
  };
}

function extractJson(text: string) {
  const fenced = text.match(/```(?:json)?\s*([\s\S]*?)```/i);
  const candidate = fenced?.[1] ?? text;
  const start = candidate.indexOf("{");
  const end = candidate.lastIndexOf("}");
  if (start === -1 || end === -1 || end <= start) {
    throw new IntegrationCallError("Local foundation LLM", "response did not include a JSON object");
  }

  try {
    return JSON.parse(candidate.slice(start, end + 1)) as unknown;
  } catch (error) {
    throw new IntegrationCallError(
      "Local foundation LLM",
      error instanceof Error ? `invalid JSON: ${error.message}` : "invalid JSON"
    );
  }
}

function buildGenerationPrompt(input: LocalFoundationInput, research: Awaited<ReturnType<typeof collectEvidence>>) {
  return [
    "You are running a company-foundation workflow locally.",
    "This is real execution using Tavily evidence and an LLM, not a mock.",
    "",
    "Core requirement: specialize the operator's idea into a sharper, revenue-capable micro-SaaS wedge.",
    "Do not merely restate the business name or raw idea.",
    "Use the evidence below to choose a specific customer, pain, buying-intent channel, SEO angle, first product workflow, and revenue path.",
    "",
    "Business context:",
    JSON.stringify(input.business, null, 2),
    "",
    "Workflow:",
    JSON.stringify({ workflow_id: input.workflowId, workflow_title: input.workflowTitle }, null, 2),
    "",
    "Fixed skill instructions:",
    sanitizeSkillInstructions(input.skillInstructions),
    "",
    "Operator request:",
    input.operatorPrompt || "Run the workflow using the current business state.",
    "",
    "Tavily research answers:",
    JSON.stringify(research.answers, null, 2),
    "",
    "Evidence snippets:",
    JSON.stringify(research.evidence, null, 2),
    "",
    "Return strict JSON only with these keys:",
    JSON.stringify(
      {
        specialized_name: "short name if the original name should be sharpened",
        one_liner: "specific customer + pain + outcome",
        target_customer: "specific buyer/user",
        pain: "concrete recurring pain",
        offer: "what the generated business sells",
        first_workflow: "smallest useful product workflow",
        pricing_hypothesis: "specific initial price/path",
        site_requirements: ["landing page/product requirements"],
        seo_targets: ["buying-intent or comparison keyword"],
        buying_intent: ["where online demand appears"],
        competitors: [{ name: "competitor", url: "https://...", gap: "specific gap" }],
        pain_evidence: [{ claim: "observation", url: "https://..." }],
        pricing_evidence: [{ claim: "observation", url: "https://..." }],
        first_outreach_angles: ["angle"],
        risks: ["risk"],
        next_fixed_workflows: ["build_site", "setup_revenue_path", "generate_social_post"],
        summary: "operator-readable decision brief"
      },
      null,
      2
    )
  ].join("\n");
}

async function generateWithAnthropic(prompt: string, apiKey: string, model: string) {
  const response = await fetch("https://api.anthropic.com/v1/messages", {
    method: "POST",
    headers: {
      "x-api-key": apiKey,
      "anthropic-version": "2023-06-01",
      "Content-Type": "application/json"
    },
    body: JSON.stringify({
      model,
      max_tokens: 4000,
      messages: [{ role: "user", content: prompt }]
    }),
    signal: AbortSignal.timeout(90_000)
  });

  const body = await parseJsonResponse(response);
  if (!response.ok) {
    throw new IntegrationCallError("Anthropic", `${response.status} ${JSON.stringify(body)}`, response.status);
  }

  const content = (body as { content?: Array<{ type?: string; text?: string }> })?.content ?? [];
  const text = content
    .map((part) => (part.type === "text" || !part.type ? part.text ?? "" : ""))
    .join("\n")
    .trim();
  if (!text) throw new IntegrationCallError("Anthropic", "message response did not include text content");

  return {
    provider: "anthropic" as const,
    model,
    text
  };
}

async function generateWithOpenAi(prompt: string, apiKey: string, model: string) {
  const response = await fetch("https://api.openai.com/v1/chat/completions", {
    method: "POST",
    headers: {
      Authorization: `Bearer ${apiKey}`,
      "Content-Type": "application/json"
    },
    body: JSON.stringify({
      model,
      temperature: 0.2,
      messages: [{ role: "user", content: prompt }]
    }),
    signal: AbortSignal.timeout(90_000)
  });

  const body = await parseJsonResponse(response);
  if (!response.ok) {
    throw new IntegrationCallError("OpenAI", `${response.status} ${JSON.stringify(body)}`, response.status);
  }

  const text = (body as { choices?: Array<{ message?: { content?: string } }> })?.choices?.[0]?.message?.content?.trim();
  if (!text) throw new IntegrationCallError("OpenAI", "chat completion response did not include text content");

  return {
    provider: "openai" as const,
    model,
    text
  };
}

async function updateCompanyFoundation(input: {
  businessId: string;
  workflowId: string;
  output: Record<string, unknown>;
  evidence: Evidence[];
}) {
  const sql = db();
  const oneLiner = typeof input.output.one_liner === "string" ? input.output.one_liner : null;
  const specializedName = typeof input.output.specialized_name === "string" ? input.output.specialized_name : null;
  const siteTitle = specializedName?.trim() || null;

  await sql`
    UPDATE company_sites
    SET
      public_title = COALESCE(${siteTitle}, public_title),
      public_pitch = COALESCE(${oneLiner}, public_pitch),
      config = config || ${sql.json(
        toJson({
          foundation: {
            last_workflow_id: input.workflowId,
            output: input.output,
            evidence: input.evidence
          }
        })
      )}::jsonb
    WHERE business_id = ${input.businessId}
  `;
}

export async function runLocalFoundationWorkflow(input: LocalFoundationInput) {
  const env = getLocalFoundationEnv();
  if (!hasEnv(env.TAVILY_API_KEY)) throw new IntegrationNotConfiguredError("TAVILY_API_KEY");
  if (!hasEnv(env.ANTHROPIC_API_KEY) && !hasEnv(env.OPENAI_API_KEY)) {
    throw new IntegrationNotConfiguredError("ANTHROPIC_API_KEY or OPENAI_API_KEY");
  }

  const research = await collectEvidence(input, env.TAVILY_API_KEY);
  const prompt = buildGenerationPrompt(input, research);
  const generation = hasEnv(env.ANTHROPIC_API_KEY)
    ? await generateWithAnthropic(prompt, env.ANTHROPIC_API_KEY, env.ARGON_FOUNDATION_MODEL)
    : await generateWithOpenAi(prompt, env.OPENAI_API_KEY, env.ARGON_FOUNDATION_MODEL);
  const output = generatedFoundationSchema.parse(extractJson(generation.text));

  await updateCompanyFoundation({
    businessId: input.business.id,
    workflowId: input.workflowId,
    output,
    evidence: research.evidence
  });

  return {
    provider: "local-foundation",
    runId: randomUUID(),
    workflowId: input.workflowId,
    llmProvider: generation.provider,
    model: generation.model,
    searchQueries: research.queries,
    evidence: research.evidence,
    output,
    summary:
      output.summary ||
      output.one_liner ||
      `${input.workflowTitle} completed with Tavily evidence and ${generation.provider}.`
  };
}
