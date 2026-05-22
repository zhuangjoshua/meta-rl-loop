import { createEvent } from "../events";
import { upsertBusinessDocument } from "../documents";
import { IntegrationCallError } from "../errors";
import { toJson } from "../json";
import { db } from "../db";
import { submitArgonRun } from "../vendors/argon-runtime";
import { runLocalFoundationWorkflow } from "./local-foundation";
import { loadCompanyFactorySkill } from "./skills";

type FoundationBusiness = {
  id: string;
  name: string;
  slug: string;
  status: string;
  site_slug: string;
  site_status: string;
  public_title: string;
  public_pitch: string;
  site_config: unknown;
};

type FoundationWorkflow = {
  id: "plan_business" | "research_market";
  title: string;
  skillFile: string;
  skillId: string;
};

type RuntimeAttempt =
  | { status: "skipped"; reason: string }
  | { status: "submitted"; runId: string; raw: unknown }
  | { status: "failed"; reason: string };

const FOUNDATION_WORKFLOWS: FoundationWorkflow[] = [
  {
    id: "plan_business",
    title: "Plan Business",
    skillFile: "business-plan.md",
    skillId: "takyon-company-factory/business-plan"
  },
  {
    id: "research_market",
    title: "Research Market",
    skillFile: "market-research.md",
    skillId: "takyon-company-factory/market-research"
  }
];

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value) ? (value as Record<string, unknown>) : {};
}

function asString(value: unknown) {
  return typeof value === "string" ? value.trim() : "";
}

function asArray(value: unknown) {
  return Array.isArray(value) ? value : [];
}

function compactLines(lines: Array<string | null | undefined>) {
  return lines.filter((line): line is string => Boolean(line && line.trim())).join("\n");
}

function markdownList(value: unknown, defaultText: string) {
  const values = asArray(value)
    .map((item) => {
      if (typeof item === "string") return item.trim();
      const record = asRecord(item);
      const parts = [record.name, record.title, record.claim, record.gap, record.url]
        .map((part) => asString(part))
        .filter(Boolean);
      return parts.join(" - ");
    })
    .filter(Boolean);

  if (!values.length) return `- ${defaultText}`;
  return values.map((item) => `- ${item}`).join("\n");
}

function evidenceMarkdown(evidence: unknown) {
  const rows = asArray(evidence)
    .map((item) => asRecord(item))
    .filter((item) => asString(item.url) && asString(item.title))
    .slice(0, 10);

  if (!rows.length) return "- No source snippets returned by the research provider.";
  return rows
    .map((item) => {
      const title = asString(item.title);
      const url = asString(item.url);
      const content = asString(item.content).slice(0, 260);
      return `- [${title}](${url})${content ? ` - ${content}` : ""}`;
    })
    .join("\n");
}

function mergeFoundationOutputs(results: Array<{ result: Record<string, unknown> }>) {
  return results.reduce<Record<string, unknown>>((merged, item) => {
    const output = asRecord(item.result.output);
    return { ...merged, ...output };
  }, {});
}

function mergeEvidence(results: Array<{ result: Record<string, unknown> }>) {
  const seen = new Set<string>();
  const evidence: unknown[] = [];
  for (const item of results) {
    for (const row of asArray(item.result.evidence)) {
      const record = asRecord(row);
      const key = asString(record.url) || JSON.stringify(record);
      if (!key || seen.has(key)) continue;
      seen.add(key);
      evidence.push(record);
    }
  }
  return evidence;
}

function buildOperatorPrompt(payload: unknown) {
  const brief = asRecord(asRecord(payload).brief);
  return compactLines([
    asString(brief.name) ? `Company name: ${asString(brief.name)}` : null,
    asString(brief.pitch) ? `Raw idea: ${asString(brief.pitch)}` : null,
    asString(brief.customer) ? `Customer: ${asString(brief.customer)}` : null,
    asString(brief.pain) ? `Pain: ${asString(brief.pain)}` : null,
    asString(brief.offer) ? `Offer: ${asString(brief.offer)}` : null
  ]);
}

async function loadBusiness(companyId: string) {
  const sql = db();
  const rows = await sql<FoundationBusiness[]>`
    SELECT
      b.id,
      b.name,
      b.slug,
      b.status,
      cs.slug AS site_slug,
      cs.status AS site_status,
      cs.public_title,
      cs.public_pitch,
      cs.config AS site_config
    FROM businesses b
    JOIN company_sites cs ON cs.business_id = b.id
    WHERE b.id = ${companyId}
    LIMIT 1
  `;
  if (!rows[0]) throw new Error("Company not found for foundation workflow.");
  return rows[0];
}

function takyonRuntimeSkillName(skillId: string) {
  return skillId.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "");
}

async function trySubmitTakyonRuntime(input: {
  business: FoundationBusiness;
  workflow: FoundationWorkflow;
  skillInstructions: string;
  operatorPrompt: string;
  profileId?: string | null;
}): Promise<RuntimeAttempt> {
  if (!process.env.ARGON_RUNTIME_URL?.trim() && process.env.ARGON_USE_LOCAL_HERMES !== "1") {
    return { status: "skipped", reason: "Optional remote runtime is not enabled; local foundation workflow will run on this Mac." };
  }

  const fixedRuntimeSkillName = takyonRuntimeSkillName(input.workflow.skillId);
  const runInput = [
    "Run the fixed Takyon company skill for this business. Do not choose another skill.",
    "",
    "Business context:",
    JSON.stringify(input.business, null, 2),
    "",
    "Operator request:",
    input.operatorPrompt,
    "",
    "Workflow request:",
    JSON.stringify({ workflow_id: input.workflow.id, workflow_title: input.workflow.title }, null, 2)
  ].join("\n");

  const instructions = [
    `Fixed Takyon runtime skill: ${fixedRuntimeSkillName}.`,
    "If the skills tool is available, call skill_view with that skill name before working.",
    "Runtime memory and learning are disabled. The provided business context packet is the source of truth.",
    "Do not claim external side effects happened unless a deterministic app/vendor workflow actually did them.",
    "",
    input.skillInstructions
  ].join("\n");

  try {
    const run = await submitArgonRun({
      input: runInput,
      instructions,
      sessionId: `business:${input.business.id}:${input.workflow.id}`,
      metadata: {
        surface: "takyon_v3_build_company",
        business_id: input.business.id,
        workflow_id: input.workflow.id,
        skill_id: input.workflow.skillId,
        takyon_runtime_skill_name: fixedRuntimeSkillName,
        operator_profile_id: input.profileId ?? null
      },
      runtimeOptions: {
        skipMemory: true,
        skipContextFiles: true,
        enabledToolsets: ["web", "skills", "todo"],
        disabledToolsets: ["memory", "session_search", "cronjob"]
      }
    });
    const runId = typeof run.run_id === "string" ? run.run_id : typeof run.id === "string" ? run.id : "";
    if (!runId) throw new IntegrationCallError("Argon runtime", `run response did not include run_id: ${JSON.stringify(run)}`);
    return { status: "submitted", runId, raw: run };
  } catch (error) {
    return { status: "failed", reason: error instanceof Error ? error.message : String(error) };
  }
}

async function saveMergedFoundation(input: {
  business: FoundationBusiness;
  output: Record<string, unknown>;
  evidence: unknown[];
  workflowResults: unknown[];
  runtimeAttempts: RuntimeAttempt[];
}) {
  const mission = compactLines([
    `# ${asString(input.output.specialized_name) || input.business.public_title || input.business.name}`,
    "",
    "## One-Liner",
    asString(input.output.one_liner) || input.business.public_pitch,
    "",
    "## Customer",
    asString(input.output.target_customer),
    "",
    "## Pain",
    asString(input.output.pain),
    "",
    "## Offer",
    asString(input.output.offer),
    "",
    "## First Workflow",
    asString(input.output.first_workflow),
    "",
    "## Pricing Hypothesis",
    asString(input.output.pricing_hypothesis)
  ]);

  const research = compactLines([
    "# Market Research",
    "",
    "## Summary",
    asString(input.output.summary) || asString(input.output.one_liner),
    "",
    "## Buying Intent",
    markdownList(input.output.buying_intent, "No buying-intent notes returned."),
    "",
    "## Competitors",
    markdownList(input.output.competitors, "No competitor notes returned."),
    "",
    "## Pain Evidence",
    markdownList(input.output.pain_evidence, "No pain evidence returned."),
    "",
    "## Pricing Evidence",
    markdownList(input.output.pricing_evidence, "No pricing evidence returned."),
    "",
    "## Sources",
    evidenceMarkdown(input.evidence)
  ]);

  const metadata = {
    generated_from: "foundation",
    seeded: false,
    provider: "local-foundation",
    runtime_attempts: input.runtimeAttempts,
    workflow_results: input.workflowResults,
    evidence_count: input.evidence.length
  };

  const [missionDoc, researchDoc] = await Promise.all([
    upsertBusinessDocument({
      companyId: input.business.id,
      title: "Mission",
      kind: "mission",
      content: mission,
      source: "agent",
      metadata,
      replaceMetadata: true
    }),
    upsertBusinessDocument({
      companyId: input.business.id,
      title: "Market Research",
      kind: "research_report",
      content: research,
      source: "agent",
      metadata,
      replaceMetadata: true
    })
  ]);

  await createEvent({
    businessId: input.business.id,
    kind: "foundation.documents_saved",
    subjectType: "business",
    subjectId: input.business.id,
    payload: { mission_document_id: missionDoc.id, research_document_id: researchDoc.id, evidence_count: input.evidence.length }
  });

  return { missionDoc, researchDoc };
}

export async function runInitialFoundation(input: {
  companyId: string;
  profileId?: string | null;
  payload?: unknown;
}) {
  const business = await loadBusiness(input.companyId);
  const operatorPrompt = buildOperatorPrompt(input.payload);
  const workflowResults: Array<{ workflowId: string; runtime: RuntimeAttempt; result: Record<string, unknown> }> = [];

  for (const workflow of FOUNDATION_WORKFLOWS) {
    const skillInstructions = await loadCompanyFactorySkill(workflow.skillFile);
    const runtime = await trySubmitTakyonRuntime({
      business,
      workflow,
      skillInstructions,
      operatorPrompt,
      profileId: input.profileId
    });
    const local = await runLocalFoundationWorkflow({
      business,
      workflowId: workflow.id,
      workflowTitle: workflow.title,
      operatorPrompt,
      skillInstructions
    });
    workflowResults.push({ workflowId: workflow.id, runtime, result: asRecord(local) });
  }

  const output = mergeFoundationOutputs(workflowResults);
  const evidence = mergeEvidence(workflowResults);
  const docs = await saveMergedFoundation({
    business,
    output,
    evidence,
    workflowResults: workflowResults.map((result) => toJson(result)),
    runtimeAttempts: workflowResults.map((result) => result.runtime)
  });

  return {
    status: "completed" as const,
    provider: "local-foundation",
    runtime: workflowResults.map((result) => result.runtime),
    workflows: workflowResults.map((result) => ({
      workflowId: result.workflowId,
      summary: asString(asRecord(result.result.output).summary) || asString(asRecord(result.result.output).one_liner),
      evidenceCount: asArray(result.result.evidence).length
    })),
    documents: {
      mission_document_id: docs.missionDoc.id,
      research_document_id: docs.researchDoc.id
    },
    evidence_count: evidence.length
  };
}
