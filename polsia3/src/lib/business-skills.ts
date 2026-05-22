import { executeAiProvider } from "./ai-provider";
import { listBusinessMemory, upsertBusinessMemory } from "./business-memory";
import { upsertBusinessDocument } from "./documents";
import { db } from "./db";
import { ConfigurationError, IntegrationCallError } from "./errors";
import { createEvent } from "./events";
import { toJson } from "./json";
import { loadLocalSecrets } from "./secrets";
import { listToolCapabilities } from "./tool-availability";
import { businessWorkspaceContext, writeBusinessWorkspaceFile } from "./business-workspace";
import { loadBusinessMarketingSkill } from "./workflows/skills";

const BUSINESS_SKILL_CONFIGS = {
  business_marketing_context: {
    file: "business-marketing-context.md",
    title: "Business Marketing Context",
    documentTitle: "Business Marketing Context",
    workspacePath: "memory/product-marketing-context.md",
    memoryKey: "business-marketing-context",
    description: "Shared positioning, ICP, offer, proof, channel, objection, and messaging context."
  },
  business_search_visibility: {
    file: "business-search-visibility.md",
    title: "Business Search Visibility",
    documentTitle: "Business Search Visibility",
    workspacePath: "product/search-visibility.md",
    memoryKey: "business-search-visibility",
    description: "SEO/GEO visibility scorecard, gaps, and implementation backlog."
  },
  business_conversion_review: {
    file: "business-conversion-review.md",
    title: "Business Conversion Review",
    documentTitle: "Business Conversion Review",
    workspacePath: "product/conversion-review.md",
    memoryKey: "business-conversion-review",
    description: "Conversion funnel review and small experiment backlog."
  },
  business_product_design: {
    file: "business-product-design.md",
    title: "Business Product Design",
    documentTitle: "Business Product Design",
    workspacePath: "product/design-brief.md",
    memoryKey: "business-product-design",
    description: "Open Design-inspired webpage and app design brief, design-system guidance, and UI QA checklist."
  },
  business_content_engine: {
    file: "business-content-engine.md",
    title: "Business Content Engine",
    documentTitle: "Business Content Engine",
    workspacePath: "outreach/content-engine.md",
    memoryKey: "business-content-engine",
    description: "Content pillars, page briefs, social angles, and draft copy."
  },
  business_outreach_pipeline: {
    file: "business-outreach-pipeline.md",
    title: "Business Outreach Pipeline",
    documentTitle: "Business Outreach Pipeline",
    workspacePath: "outreach/outreach-pipeline.md",
    memoryKey: "business-outreach-pipeline",
    description: "No-sending target segments, qualification, sequence drafts, and sales enablement."
  },
  business_paid_media_review: {
    file: "business-paid-media-review.md",
    title: "Business Paid Media Review",
    documentTitle: "Business Paid Media Review",
    workspacePath: "outreach/paid-media-review.md",
    memoryKey: "business-paid-media-review",
    description: "Read-only paid media readiness, creative hypotheses, and planning-only recommendations."
  },
  business_measurement_plan: {
    file: "business-measurement-plan.md",
    title: "Business Measurement Plan",
    documentTitle: "Business Measurement Plan",
    workspacePath: "product/measurement-plan.md",
    memoryKey: "business-measurement-plan",
    description: "Event taxonomy, funnel metrics, attribution, and gated Pixel/CAPI audit plan."
  }
} as const;

export type BusinessSkillWorkflowId = keyof typeof BUSINESS_SKILL_CONFIGS;

export function businessSkillWorkflowIds() {
  return Object.keys(BUSINESS_SKILL_CONFIGS) as BusinessSkillWorkflowId[];
}

export function isBusinessSkillWorkflowId(workflowId: string): workflowId is BusinessSkillWorkflowId {
  return workflowId in BUSINESS_SKILL_CONFIGS;
}

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value) ? (value as Record<string, unknown>) : {};
}

function payloadString(value: unknown, key: string) {
  const text = asRecord(value)[key];
  return typeof text === "string" && text.trim() ? text.trim() : null;
}

function compactRows(value: unknown, max = 8000) {
  return JSON.stringify(toJson(value), null, 2).slice(0, max);
}

function providerPolicy() {
  loadLocalSecrets();
  const explicit = process.env.TAKYON_BUSINESS_SKILL_PROVIDER?.trim().toLowerCase();
  if (explicit === "anthropic" || explicit === "openai") {
    return {
      provider: explicit,
      model: process.env.TAKYON_BUSINESS_SKILL_MODEL?.trim() || process.env.ARGON_PRODUCT_AI_MODEL?.trim() || (explicit === "anthropic" ? "claude-opus-4-7" : "gpt-5.2")
    };
  }
  if (process.env.ANTHROPIC_API_KEY?.trim()) {
    return {
      provider: "anthropic",
      model: process.env.TAKYON_BUSINESS_SKILL_MODEL?.trim() || process.env.ARGON_PRODUCT_AI_MODEL?.trim() || "claude-opus-4-7"
    };
  }
  if (process.env.OPENAI_API_KEY?.trim()) {
    return {
      provider: "openai",
      model: process.env.TAKYON_BUSINESS_SKILL_MODEL?.trim() || process.env.ARGON_PRODUCT_AI_MODEL?.trim() || "gpt-5.2"
    };
  }
  throw new ConfigurationError("Business skills require ANTHROPIC_API_KEY or OPENAI_API_KEY.");
}

async function loadBusinessState(input: { businessId: string; profileId?: string | null }) {
  const sql = db();
  const [business, documents, jobs, campaigns, communityTargets, leads, conversations, posts, mediaJobs, revenue, paymentLinks, memories, workspace, capabilities] = await Promise.all([
    sql`
      SELECT b.id, b.name, b.slug, b.status, b."mode", cs.public_title, cs.public_pitch, cs.status AS site_status, cs.config AS site_config
      FROM businesses b
      LEFT JOIN company_sites cs ON cs.business_id = b.id
      WHERE b.id = ${input.businessId}
      LIMIT 1
    `,
    sql`
      SELECT title, kind, source, content, metadata, updated_at
      FROM business_documents
      WHERE business_id = ${input.businessId}
      ORDER BY updated_at DESC
      LIMIT 12
    `,
    sql`
      SELECT workflow_id, lane, status, error, result, updated_at
      FROM workflow_jobs
      WHERE business_id = ${input.businessId}
      ORDER BY updated_at DESC
      LIMIT 28
    `,
    sql`
      SELECT slug, name, kind, status, metadata, updated_at
      FROM business_campaigns
      WHERE business_id = ${input.businessId}
      ORDER BY updated_at DESC
      LIMIT 20
    `,
    sql`
      SELECT source, title, url, match_reason, generated_copy, created_at
      FROM community_targets
      WHERE business_id = ${input.businessId}
      ORDER BY created_at DESC
      LIMIT 20
    `,
    sql`
      SELECT name, email, url, source, status, last_event, created_at
      FROM leads
      WHERE business_id = ${input.businessId}
      ORDER BY created_at DESC
      LIMIT 20
    `,
    sql`
      SELECT m.source, m.author_label, m.body, m.status, m.received_at, t.title AS thread_title, t.url AS thread_url
      FROM business_conversation_messages m
      JOIN business_conversation_threads t ON t.id = m.thread_id
      WHERE m.business_id = ${input.businessId}
      ORDER BY m.received_at DESC
      LIMIT 20
    `,
    sql`
      SELECT provider, status, text, provider_url, error, published_at, created_at
      FROM business_social_posts
      WHERE business_id = ${input.businessId}
      ORDER BY created_at DESC
      LIMIT 12
    `,
    sql`
      SELECT provider, model, status, output_url, error, prompt, created_at
      FROM media_generation_jobs
      WHERE business_id = ${input.businessId}
      ORDER BY created_at DESC
      LIMIT 10
    `,
    sql`
      SELECT revenue_type, status, currency, amount_paid_cents, occurred_at
      FROM company_revenue_events
      WHERE business_id = ${input.businessId}
      ORDER BY occurred_at DESC
      LIMIT 12
    `,
    sql`
      SELECT plan_key, name, unit_amount_cents, currency, active, created_at
      FROM company_payment_links
      WHERE business_id = ${input.businessId}
      ORDER BY created_at DESC
      LIMIT 8
    `,
    listBusinessMemory({ businessId: input.businessId, limit: 24 }),
    businessWorkspaceContext({ businessId: input.businessId, profileId: input.profileId ?? null }),
    listToolCapabilities({ businessId: input.businessId, profileId: input.profileId ?? null })
  ]);
  const workspaceFilePromptLimit = 160;
  const visibleWorkspaceFiles = workspace.files.slice(0, workspaceFilePromptLimit);

  return {
    business,
    documents: documents.map((document) => ({
      ...document,
      content: typeof document.content === "string" ? document.content.slice(0, 2600) : document.content
    })),
    jobs,
    campaigns,
    communityTargets,
    leads,
    conversations,
    posts,
    mediaJobs,
    revenue,
    paymentLinks,
    memories: memories.map((memory) => ({
      namespace: memory.namespace,
      title: memory.title,
      content: memory.content.slice(0, 2200),
      confidence: memory.confidence,
      updated_at: memory.updated_at
    })),
    workspace: {
      root: workspace.root,
      readStrategy: workspace.readStrategy,
      bootFiles: workspace.bootFiles,
      topLevelMap: workspace.topLevelMap,
      files: visibleWorkspaceFiles,
      omittedFilesFromPacket: Math.max(0, workspace.files.length - visibleWorkspaceFiles.length),
      excerpts: workspace.excerpts.map((file) => ({
        path: file.path,
        truncated: file.truncated,
        content: file.content.slice(0, 2200)
      }))
    },
    capabilities: capabilities.map((capability) => ({
      key: capability.key,
      label: capability.label,
      canRun: capability.canRun,
      reason: capability.reason,
      missing: capability.missing
    }))
  };
}

function normalizeReport(title: string, text: string) {
  const trimmed = text.trim();
  if (!trimmed) throw new IntegrationCallError("Business skill", "model returned an empty report");
  if (/^#\s+/m.test(trimmed)) return `${trimmed}\n`;
  return `# ${title}\n\n${trimmed}\n`;
}

export async function runBusinessSkillWorkflow(input: {
  businessId: string;
  profileId?: string | null;
  workflowId: BusinessSkillWorkflowId;
  payload?: unknown;
  workflowJobId?: string | null;
}) {
  const config = BUSINESS_SKILL_CONFIGS[input.workflowId];
  const [skill, state] = await Promise.all([
    loadBusinessMarketingSkill(config.file),
    loadBusinessState({ businessId: input.businessId, profileId: input.profileId ?? null })
  ]);
  const policy = providerPolicy();
  const operatorInstruction = payloadString(input.payload, "operator_instruction");
  const response = await executeAiProvider({
    provider: policy.provider,
    model: policy.model,
    maxOutputTokens: 2600,
    messages: [
      {
        role: "system",
        content: [
          "Run this fixed Takyon business skill.",
          "You are inside Takyon's bounded business-skills layer.",
          "Return Markdown only. Do not return JSON unless the skill explicitly asks for it.",
          "Do not call tools, browse, mutate vendor accounts, send email, publish, scrape private contacts, or claim hidden side effects.",
          "Use only the supplied Takyon state, workspace files, receipts, capabilities, and operator instruction.",
          "",
          skill
        ].join("\n")
      },
      {
        role: "user",
        content: [
          `Workflow: ${input.workflowId}`,
          `Purpose: ${config.description}`,
          operatorInstruction ? `Operator instruction: ${operatorInstruction}` : "Operator instruction: none",
          "",
          "Takyon state packet:",
          compactRows(state, 26000)
        ].join("\n")
      }
    ]
  });

  const content = normalizeReport(config.title, response.text);
  const document = await upsertBusinessDocument({
    companyId: input.businessId,
    title: config.documentTitle,
    kind: "task_report",
    source: "workflow",
    content,
    metadata: {
      workflow_id: input.workflowId,
      workflow_job_id: input.workflowJobId ?? null,
      provider: policy.provider,
      model: policy.model,
      skill_file: config.file,
      side_effects: "none"
    },
    replaceMetadata: true
  });

  await upsertBusinessMemory({
    businessId: input.businessId,
    profileId: input.profileId ?? null,
    namespace: "business_skills",
    memoryKey: config.memoryKey,
    title: config.title,
    content,
    evidence: [{ kind: "business_document", document_id: document.id }],
    metadata: {
      workflow_id: input.workflowId,
      workflow_job_id: input.workflowJobId ?? null,
      provider: policy.provider,
      model: policy.model,
      workspace_path: config.workspacePath
    }
  });

  const workspaceWrite = await writeBusinessWorkspaceFile({
    businessId: input.businessId,
    relativePath: config.workspacePath,
    content
  });

  await createEvent({
    businessId: input.businessId,
    actorProfileId: input.profileId ?? null,
    kind: "business_skill.completed",
    subjectType: "business_document",
    subjectId: document.id,
    payload: {
      workflow_id: input.workflowId,
      workflow_job_id: input.workflowJobId ?? null,
      workspace_path: workspaceWrite.path,
      warnings: workspaceWrite.warnings,
      provider: policy.provider,
      model: policy.model,
      side_effects: "none"
    }
  });

  return {
    status: "completed" as const,
    workflowId: input.workflowId,
    documentId: document.id,
    workspacePath: workspaceWrite.path,
    warnings: workspaceWrite.warnings,
    provider: policy.provider,
    model: policy.model,
    sideEffects: "none" as const
  };
}
