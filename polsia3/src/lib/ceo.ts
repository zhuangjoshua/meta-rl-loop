import { db } from "./db";
import { listBusinessMemory, upsertBusinessMemory } from "./business-memory";
import { upsertBusinessDocument } from "./documents";
import { createEvent } from "./events";
import { createInboxMessage } from "./inbox";
import { toJson } from "./json";
import { runTakyonRuntimeReasoning } from "./takyon-runtime";
import { businessWorkspaceContext } from "./business-workspace";
import { getTakyonWorkflowSpec, takyonCapabilityGroups, takyonWorkflowRegistry } from "./takyon-registry";
import { preflightCapabilityGroups } from "./tool-availability";
import { enqueueWorkflowJob } from "./workflow-jobs";

const CEO_ACTION_EXCLUDED_WORKFLOWS = new Set(["ceo_wakeup"]);

function ceoActionWorkflowSpecs() {
  return takyonWorkflowRegistry
    .filter((workflow) => workflow.dispatchable && !CEO_ACTION_EXCLUDED_WORKFLOWS.has(workflow.workflowId))
    .sort((a, b) => b.priority - a.priority);
}

function isCeoActionWorkflowId(value: string) {
  const spec = getTakyonWorkflowSpec(value);
  return Boolean(spec?.dispatchable && !CEO_ACTION_EXCLUDED_WORKFLOWS.has(value));
}

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value) ? (value as Record<string, unknown>) : {};
}

function extractJsonObjects(text: string) {
  const objects: Record<string, unknown>[] = [];
  for (const match of text.matchAll(/```(?:json)?\s*([\s\S]*?)```/gi)) {
    const raw = match[1].trim();
    if (!raw.startsWith("{")) continue;
    try {
      objects.push(asRecord(JSON.parse(raw)));
    } catch {
      // Ignore non-action code fences. The report remains useful even without queued actions.
    }
  }
  return objects;
}

function ceoBusinessActions(text: string) {
  const parsed = extractJsonObjects(text).reverse();
  const actions: Array<{ workflow_id: string; reason: string }> = [];
  for (const object of parsed) {
    const raw = Array.isArray(object.next_actions) ? object.next_actions : [];
    for (const item of raw) {
      const record = asRecord(item);
      const workflowId = typeof record.workflow_id === "string" ? record.workflow_id.trim() : "";
      if (!isCeoActionWorkflowId(workflowId)) continue;
      const reason = typeof record.reason === "string" && record.reason.trim() ? record.reason.trim() : "ceo_recommended_business_skill";
      if (actions.some((action) => action.workflow_id === workflowId)) continue;
      actions.push({ workflow_id: workflowId, reason });
      if (actions.length >= 4) return actions;
    }
    if (actions.length) return actions;
  }
  return actions;
}

type QueuedCeoWorkflow =
  | { workflow_id: string; status: "queued"; jobId: string; reason: string; dependencyOf?: string }
  | { workflow_id: string; status: "skipped_existing"; existingStatus: string; jobId: string; reason: string; dependencyOf?: string }
  | { workflow_id: string; status: "blocked"; reason: string; error: string; missing: string[]; dependencyOf?: string }
  | { workflow_id: string; status: "blocked_dependency"; reason: string; dependencyOf: string }
  | { workflow_id: string; status: "invalid"; reason: string; dependencyOf?: string };

async function findCoveringWorkflow(input: { businessId: string; workflowId: string; repeatable: boolean; dependency: boolean }) {
  const sql = db();
  if (input.dependency || !input.repeatable) {
    const rows = await sql<{ id: string; status: string }[]>`
      SELECT id, status
      FROM workflow_jobs
      WHERE business_id = ${input.businessId}
        AND workflow_id = ${input.workflowId}
        AND status IN ('queued', 'running', 'completed')
      ORDER BY updated_at DESC
      LIMIT 1
    `;
    return rows[0] ?? null;
  }

  const rows = await sql<{ id: string; status: string }[]>`
    SELECT id, status
    FROM workflow_jobs
    WHERE business_id = ${input.businessId}
      AND workflow_id = ${input.workflowId}
      AND (
        status IN ('queued', 'running')
        OR (status = 'completed' AND updated_at >= now() - interval '24 hours')
      )
    ORDER BY updated_at DESC
    LIMIT 1
  `;
  return rows[0] ?? null;
}

async function queueCeoWorkflowWithDependencies(input: {
  businessId: string;
  reportId: string;
  workflowId: string;
  reason: string;
  dependencyOf?: string;
  path?: string[];
}): Promise<{ ready: boolean; queued: QueuedCeoWorkflow[] }> {
  const spec = getTakyonWorkflowSpec(input.workflowId);
  if (!spec?.dispatchable || CEO_ACTION_EXCLUDED_WORKFLOWS.has(input.workflowId)) {
    return {
      ready: false,
      queued: [
        {
          workflow_id: input.workflowId,
          status: "invalid",
          reason: input.reason,
          dependencyOf: input.dependencyOf
        }
      ]
    };
  }

  const path = input.path ?? [];
  if (path.includes(input.workflowId)) {
    return {
      ready: false,
      queued: [
        {
          workflow_id: input.workflowId,
          status: "invalid",
          reason: `Dependency cycle: ${[...path, input.workflowId].join(" -> ")}`,
          dependencyOf: input.dependencyOf
        }
      ]
    };
  }

  const queued: QueuedCeoWorkflow[] = [];
  let dependenciesReady = true;
  for (const dependency of spec.dependencies) {
    const dependencyResult = await queueCeoWorkflowWithDependencies({
      businessId: input.businessId,
      reportId: input.reportId,
      workflowId: dependency,
      reason: `Dependency for ${input.workflowId}.`,
      dependencyOf: input.workflowId,
      path: [...path, input.workflowId]
    });
    queued.push(...dependencyResult.queued);
    if (!dependencyResult.ready) dependenciesReady = false;
  }

  if (!dependenciesReady) {
    return {
      ready: false,
      queued: [
        ...queued,
        {
          workflow_id: input.workflowId,
          status: "blocked_dependency",
          reason: input.reason,
          dependencyOf: input.dependencyOf ?? input.workflowId
        }
      ]
    };
  }

  const existing = await findCoveringWorkflow({
    businessId: input.businessId,
    workflowId: input.workflowId,
    repeatable: Boolean(spec.repeatable),
    dependency: Boolean(input.dependencyOf)
  });
  if (existing) {
    return {
      ready: true,
      queued: [
        ...queued,
        {
          workflow_id: input.workflowId,
          status: "skipped_existing",
          existingStatus: existing.status,
          jobId: existing.id,
          reason: input.reason,
          dependencyOf: input.dependencyOf
        }
      ]
    };
  }

  const block = await preflightCapabilityGroups({
    workflowId: input.workflowId,
    groups: takyonCapabilityGroups(input.workflowId),
    businessId: input.businessId,
    profileId: null
  });
  if (block) {
    await createEvent({
      businessId: input.businessId,
      kind: "tool.capability_blocked",
      subjectType: "business_document",
      subjectId: input.reportId,
      payload: block
    });
    return {
      ready: false,
      queued: [
        ...queued,
        {
          workflow_id: input.workflowId,
          status: "blocked",
          reason: input.reason,
          error: block.error,
          missing: block.missing,
          dependencyOf: input.dependencyOf
        }
      ]
    };
  }

  const job = await enqueueWorkflowJob({
    companyId: input.businessId,
    profileId: null,
    workflowId: input.workflowId,
    lane: spec.lane,
    dependencies: spec.dependencies,
    priority: spec.priority,
    maxAttempts: spec.maxAttempts,
    payload: {
      source: "ceo_wakeup",
      source_report_id: input.reportId,
      reason: input.reason,
      dependency_of: input.dependencyOf ?? null
    }
  });
  return {
    ready: true,
    queued: [
      ...queued,
      { workflow_id: input.workflowId, status: "queued", jobId: job.id, reason: input.reason, dependencyOf: input.dependencyOf }
    ]
  };
}

async function queueCeoBusinessActions(input: { businessId: string; reportId: string; reportText: string }) {
  const actions = ceoBusinessActions(input.reportText);
  const queued: QueuedCeoWorkflow[] = [];
  for (const action of actions) {
    const result = await queueCeoWorkflowWithDependencies({
      businessId: input.businessId,
      reportId: input.reportId,
      workflowId: action.workflow_id,
      reason: action.reason
    });
    queued.push(...result.queued);
  }

  if (queued.length) {
    await createEvent({
      businessId: input.businessId,
      kind: "ceo.business_actions_selected",
      subjectType: "business_document",
      subjectId: input.reportId,
      payload: { queued }
    });
  }

  return queued;
}

export async function runCeoReasoning(input: { businessId: string }) {
  const sql = db();
  const [companyRows, documentRows, jobRows, conversationRows, memoryRows, workspace] = await Promise.all([
    sql<{ name: string; public_pitch: string | null }[]>`
      SELECT b.name, cs.public_pitch
      FROM businesses b
      LEFT JOIN company_sites cs ON cs.business_id = b.id
      WHERE b.id = ${input.businessId}
      LIMIT 1
    `,
    sql<{ title: string; kind: string; content: string }[]>`
      SELECT title, kind, content
      FROM business_documents
      WHERE business_id = ${input.businessId}
      ORDER BY updated_at DESC
      LIMIT 8
    `,
    sql<{ workflow_id: string; lane: string; status: string; error: string | null }[]>`
      SELECT workflow_id, lane, status, error
      FROM workflow_jobs
      WHERE business_id = ${input.businessId}
      ORDER BY created_at DESC
      LIMIT 20
    `,
    sql<{ source: string; author_label: string; body: string; status: string; received_at: string; thread_title: string; thread_url: string | null }[]>`
      SELECT m.source, m.author_label, m.body, m.status, m.received_at::text, t.title AS thread_title, t.url AS thread_url
      FROM business_conversation_messages m
      JOIN business_conversation_threads t ON t.id = m.thread_id
      WHERE m.business_id = ${input.businessId}
      ORDER BY m.received_at DESC
      LIMIT 20
    `,
    listBusinessMemory({ businessId: input.businessId, limit: 12 }),
    businessWorkspaceContext({ businessId: input.businessId })
  ]);
  const company = companyRows[0];
  if (!company) throw new Error("Company not found for CEO reasoning.");
  const workspaceFilePromptLimit = 120;
  const visibleWorkspaceFiles = workspace.files.slice(0, workspaceFilePromptLimit);
  const omittedWorkspaceFileCount = Math.max(0, workspace.files.length - visibleWorkspaceFiles.length);
  const prompt = [
    `Company: ${company.name}`,
    `Pitch: ${company.public_pitch ?? ""}`,
    "",
    "Workflow state:",
    ...jobRows.map((job) => `- ${job.workflow_id} (${job.lane}): ${job.status}${job.error ? ` - ${job.error}` : ""}`),
    "",
    "Recent documents:",
    ...documentRows.map((document) => `## ${document.title} (${document.kind})\n${document.content.slice(0, 1600)}`),
    "",
    "Recent conversation responses:",
    ...conversationRows.map((message) => `- ${message.status} ${message.source} ${message.received_at} ${message.thread_title}${message.thread_url ? ` ${message.thread_url}` : ""}: ${message.author_label}: ${message.body.slice(0, 900)}`),
    "",
    "Business memory:",
    ...memoryRows
      .map((memory) => `## ${memory.title} (${memory.namespace})\n${memory.content.slice(0, 1800)}`),
    "",
    "Business workspace:",
    `Root: ${workspace.root}`,
    `Filesystem files discovered: ${workspace.files.length}`,
    `Filesystem read strategy: ${workspace.readStrategy.policy}`,
    "",
    "Workspace top-level map:",
    ...workspace.topLevelMap.map((entry) => `- ${entry.path}: ${entry.files} files, ${entry.bytes} bytes${entry.sampleFiles.length ? `; examples: ${entry.sampleFiles.join(", ")}` : ""}`),
    "",
    "Boot files:",
    ...workspace.bootFiles.map((file) => `- ${file}`),
    "",
    `Workspace files shown in prompt: ${visibleWorkspaceFiles.length} of ${workspace.files.length}`,
    ...visibleWorkspaceFiles.map((file) => `- ${file.path} (${file.bytes} bytes, ${file.updatedAt})`),
    omittedWorkspaceFileCount > 0
      ? `- ${omittedWorkspaceFileCount} files are not listed in this prompt. They still exist in the business workspace; use the files tool on the root above before deciding they are irrelevant.`
      : "- No workspace files are omitted from this prompt listing.",
    "",
    "Boot file excerpts:",
    ...workspace.excerpts.map((file) => `## ${file.path}${file.truncated ? " (truncated)" : ""}\n${file.content.slice(0, 6000)}`),
    "",
    "CEO evidence rule:",
    "Only make decisions from explicit workspace files, database rows, receipts, and capability reports in this prompt.",
    "If a needed fact is not visible in the workspace or rows, say it is unknown and name the file or receipt that should exist.",
    "No part left unused: if a business workspace path appears relevant but is omitted, unreadable, or not inspected when it should be, flag that as a blocker or open question instead of ignoring it.",
    "Do not infer product completion, campaign success, revenue, auth, checkout, deployment, posting, or spend from intentions or queued work.",
    "",
    "Write a concise CEO operating report with priorities, blockers, next actions, and any business-memory updates you would make.",
    "Use ceo/map.md and state/index.json to decide which files to inspect. If the runtime file tools are available and the operating picture changed, keep ceo/brief.md short and current.",
    "Track strategy, positioning, pricing ideas, customer objections, distribution lessons, product lessons, and recovery lessons when evidence supports them.",
    "Do not claim vendor side effects unless listed as completed.",
    "",
    "Optional bounded workflow actions:",
    "If bounded runner work should run next, append one final fenced JSON block. The runner validates capabilities and records receipts; do not invent a fixed startup sequence.",
    "Product/value delivery and outreach/demand creation are standing obligations. Do not use planning-only reviews as substitutes for missing product, site, checkout, auth, or distribution work.",
    "Choose at most four workflow ids from the central registry below:",
    ...ceoActionWorkflowSpecs().map(
      (workflow) =>
        `- ${workflow.workflowId}: ${workflow.description} (depends: ${workflow.dependencies.length ? workflow.dependencies.join(", ") : "none"})`
    ),
    "Avoid recommending a workflow if a fresh completed or active version is already visible in jobs/documents/workspace.",
    "The JSON shape is: {\"next_actions\":[{\"workflow_id\":\"website_build_deploy\",\"reason\":\"short evidence-grounded reason\"}]}",
    "Use {\"next_actions\":[]} when no business skill should be queued."
  ].join("\n");

  const runtime = await runTakyonRuntimeReasoning({
    businessId: input.businessId,
    prompt,
    metadata: { workflow: "ceo", business_workspace_root: workspace.root }
  });
  const provider = runtime.provider;
  const model = runtime.model;
  const text = runtime.output;
  const raw = runtime.raw;

  const report = await upsertBusinessDocument({
    companyId: input.businessId,
    title: `Daily Report ${new Date().toISOString().slice(0, 10)}`,
    kind: "daily_report",
    source: "workflow",
    content: text,
    metadata: { provider, model, raw: toJson(raw) }
  });
  await createInboxMessage({
    companyId: input.businessId,
    authorLabel: "CEO",
    body: text.slice(0, 1200),
    source: "ceo"
  });
  await upsertBusinessMemory({
    businessId: input.businessId,
    profileId: null,
    namespace: "strategy",
    memoryKey: "latest-ceo-report",
    title: "Latest CEO report",
    content: text,
    evidence: [{ kind: "business_document", document_id: report.id }],
    metadata: { provider, model, source: "ceo_wakeup" }
  });
  const queuedActions = await queueCeoBusinessActions({ businessId: input.businessId, reportId: report.id, reportText: text });
  return { reportId: report.id, provider, model, queuedActions };
}
