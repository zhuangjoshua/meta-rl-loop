import fs from "node:fs/promises";
import path from "node:path";
import { db } from "./db";
import { runWorkspaceWriteGuards } from "./takyon-harness";
import { listToolCapabilities } from "./tool-availability";

type WorkspaceBusiness = {
  id: string;
  name: string;
  slug: string;
  status: string;
  public_pitch: string | null;
  public_title: string | null;
  site_status: string | null;
  site_config: unknown;
};

type WorkspaceFile = {
  path: string;
  bytes: number;
  updatedAt: string;
};

const generatedReadonlyRoots = ["state", "jobs", "ledger", "tools", "receipts", "website"];
const agentAuthoredRoots = ["ceo", "goals", "product", "outreach", "campaigns", "memory", "agents", "README.md"];
const standingObligations = [
  {
    key: "product",
    directive: "Keep value delivery alive. Decide what product/site/auth/checkout/app work matters from evidence, not from a fixed stage list."
  },
  {
    key: "outreach",
    directive: "Keep demand creation alive. Decide channels, campaigns, and publishing work from evidence, capabilities, budget, and operator intent."
  }
];
const ceoBootFiles = [
  "ceo/brief.md",
  "ceo/map.md",
  "ceo/doctrine.md",
  "state/index.json",
  "state/inbox.jsonl",
  "state/current.md",
  "state/blockers.md",
  "tools/missing-keys.md"
];

function safeSegment(value: string) {
  const segment = value.toLowerCase().replace(/[^a-z0-9._-]+/g, "-").replace(/(^-+|-+$)/g, "");
  if (!segment || segment === "." || segment === "..") throw new Error("Unsafe business workspace segment.");
  return segment;
}

function workspaceBase() {
  return path.resolve(process.env.TAKYON_BUSINESS_WORKSPACE_ROOT || path.join(process.cwd(), ".takyon", "businesses"));
}

function businessRoot(business: Pick<WorkspaceBusiness, "slug">) {
  return path.join(workspaceBase(), safeSegment(business.slug));
}

function resolveInside(root: string, relativePath = ".") {
  const target = path.resolve(root, relativePath || ".");
  const relative = path.relative(root, target);
  if (relative.startsWith("..") || path.isAbsolute(relative)) {
    throw new Error(`Path escapes business workspace: ${relativePath}`);
  }
  return target;
}

function normalizeWorkspacePath(relativePath: string) {
  return relativePath.replace(/\\/g, "/").replace(/^\/+/, "").replace(/\/+/g, "/");
}

function assertAgentWritable(relativePath: string) {
  const normalized = normalizeWorkspacePath(relativePath);
  const root = normalized.split("/")[0] || "";
  if (generatedReadonlyRoots.includes(root)) {
    throw new Error(
      `Generated workspace path is read-only: ${relativePath}. Write under ceo/, goals/, product/, outreach/, campaigns/, memory/, or agents/.`
    );
  }
  if (/^campaigns\/[^/]+\/(budget\.json|state\.json|worklog\.md|receipts\/)/.test(normalized)) {
    throw new Error(`Generated campaign path is read-only: ${relativePath}. Write campaign thinking to brief.md, plan.md, creatives/, outreach/, or learnings.md.`);
  }
}

function json(value: unknown) {
  return `${JSON.stringify(value, null, 2)}\n`;
}

function jsonl(rows: unknown[]) {
  return rows.map((row) => JSON.stringify(row)).join("\n") + (rows.length ? "\n" : "");
}

function usdFromMicrousd(value: string | number | null | undefined) {
  return Number(value ?? 0) / 1_000_000;
}

async function writeText(filePath: string, content: string) {
  await fs.mkdir(path.dirname(filePath), { recursive: true });
  await fs.writeFile(filePath, content, "utf8");
}

async function writeIfAbsent(filePath: string, content: string) {
  const exists = await fs.stat(filePath).then((stat) => stat.isFile()).catch(() => false);
  if (!exists) await writeText(filePath, content);
}

async function loadBusiness(businessId: string) {
  const sql = db();
  const rows = await sql<WorkspaceBusiness[]>`
    SELECT b.id,
           b.name,
           b.slug,
           b.status,
           cs.public_pitch,
           cs.public_title,
           cs.status AS site_status,
           cs.config AS site_config
    FROM businesses b
    LEFT JOIN company_sites cs ON cs.business_id = b.id
    WHERE b.id = ${businessId}
    LIMIT 1
  `;
  const business = rows[0];
  if (!business) throw new Error(`Business not found for workspace sync: ${businessId}`);
  return business;
}

async function workspaceRows(businessId: string) {
  const sql = db();
  return Promise.all([
    sql<{ id: string; slug: string; name: string; kind: string; status: string; workspace_path: string; budget_cap_microusd: string | null; metadata: unknown; updated_at: string }[]>`
      SELECT id, slug, name, kind, status, workspace_path, budget_cap_microusd::text, metadata, updated_at::text
      FROM business_campaigns
      WHERE business_id = ${businessId}
      ORDER BY updated_at DESC
      LIMIT 200
    `,
    sql<{ namespace: string; memory_key: string; title: string; content: string; campaign_id: string | null; evidence: unknown; confidence: string | null; updated_at: string }[]>`
      SELECT namespace, memory_key, title, content, campaign_id, evidence, confidence::text, updated_at::text
      FROM business_memory_records
      WHERE business_id = ${businessId}
        AND status = 'active'
      ORDER BY namespace ASC, updated_at DESC
      LIMIT 300
    `,
    sql<{ title: string; kind: string; source: string; content: string; metadata: unknown; updated_at: string }[]>`
      SELECT title, kind, source, content, metadata, updated_at::text
      FROM business_documents
      WHERE business_id = ${businessId}
      ORDER BY updated_at DESC
      LIMIT 80
    `,
    sql<{ id: string; workflow_id: string; lane: string; status: string; priority: number; payload: unknown; dependencies: string[]; result: unknown; error: string | null; attempts: number; max_attempts: number; run_after: string; updated_at: string }[]>`
      SELECT id, workflow_id, lane, status, priority, payload, dependencies, result, error, attempts, max_attempts, run_after::text, updated_at::text
      FROM workflow_jobs
      WHERE business_id = ${businessId}
      ORDER BY updated_at DESC
      LIMIT 300
    `,
    sql<{ id: string; name: string; campaign_id: string | null; status: string; hard_limit_microusd: string; committed_microusd: string; currency: string }[]>`
      SELECT a.id,
             a.name,
             a.campaign_id,
             a.status,
             a.hard_limit_microusd::text,
             COALESCE((
               SELECT SUM(CASE WHEN l.kind IN ('release', 'refund') THEN -l.amount_microusd ELSE l.amount_microusd END)
               FROM business_budget_ledger l
               WHERE l.budget_account_id = a.id
                 AND l.status IN ('active', 'committed')
             ), 0)::text AS committed_microusd,
             a.currency
      FROM business_budget_accounts a
      WHERE a.business_id = ${businessId}
      ORDER BY a.updated_at DESC
      LIMIT 100
    `,
    sql<{ id: string; kind: string; status: string; amount_microusd: string; currency: string; provider: string | null; purpose: string; campaign_id: string | null; workflow_job_id: string | null; created_at: string }[]>`
      SELECT id, kind, status, amount_microusd::text, currency, provider, purpose, campaign_id, workflow_job_id, created_at::text
      FROM business_budget_ledger
      WHERE business_id = ${businessId}
      ORDER BY created_at DESC
      LIMIT 300
    `,
    sql<{ id: string; status: string; deployment_url: string | null; alias_url: string | null; receipt: unknown; error: string | null; created_at: string }[]>`
      SELECT id, status, deployment_url, alias_url, receipt, error, created_at::text
      FROM generated_app_deployments
      WHERE business_id = ${businessId}
      ORDER BY created_at DESC
      LIMIT 100
    `,
    sql<{ id: string; provider: string; status: string; provider_url: string | null; error: string | null; campaign_id: string | null; created_at: string }[]>`
      SELECT id, provider, status, provider_url, error, campaign_id, created_at::text
      FROM business_social_posts
      WHERE business_id = ${businessId}
      ORDER BY created_at DESC
      LIMIT 100
    `,
    sql<{ id: string; revenue_type: string; status: string; amount_paid_cents: number; currency: string; campaign_id: string | null; occurred_at: string }[]>`
      SELECT id, revenue_type, status, amount_paid_cents, currency, campaign_id, occurred_at::text
      FROM company_revenue_events
      WHERE business_id = ${businessId}
      ORDER BY occurred_at DESC
      LIMIT 100
    `,
    sql<{ id: string; source: string; title: string; url: string | null; match_reason: string | null; campaign_id: string | null; created_at: string }[]>`
      SELECT id, source, title, url, match_reason, NULL::uuid AS campaign_id, created_at::text
      FROM community_targets
      WHERE business_id = ${businessId}
      ORDER BY created_at DESC
      LIMIT 100
    `,
    sql<{ id: string; email: string | null; source: string; status: string; campaign_id: string | null; created_at: string }[]>`
      SELECT id, email, source, status, campaign_id, created_at::text
      FROM leads
      WHERE business_id = ${businessId}
      ORDER BY created_at DESC
      LIMIT 100
    `,
    sql<{ id: string; provider: string; model: string; status: string; output_url: string | null; error: string | null; campaign_id: string | null; created_at: string }[]>`
      SELECT id, provider, model, status, output_url, error, campaign_id, created_at::text
      FROM media_generation_jobs
      WHERE business_id = ${businessId}
      ORDER BY created_at DESC
      LIMIT 100
    `,
    sql<{ id: string; kind: string; subject_type: string | null; subject_id: string | null; payload: unknown; created_at: string }[]>`
      SELECT id, kind, subject_type, subject_id, payload, created_at::text
      FROM events
      WHERE business_id = ${businessId}
      ORDER BY created_at DESC
      LIMIT 300
    `
  ]);
}

function currentMarkdown(input: {
  business: WorkspaceBusiness;
  campaigns: Awaited<ReturnType<typeof workspaceRows>>[0];
  memories: Awaited<ReturnType<typeof workspaceRows>>[1];
  jobs: Awaited<ReturnType<typeof workspaceRows>>[3];
  budgets: Awaited<ReturnType<typeof workspaceRows>>[4];
  deployments: Awaited<ReturnType<typeof workspaceRows>>[6];
  posts: Awaited<ReturnType<typeof workspaceRows>>[7];
  revenue: Awaited<ReturnType<typeof workspaceRows>>[8];
}) {
  const latestDeployment = input.deployments[0];
  const activeJobs = input.jobs.filter((job) => ["queued", "running", "blocked", "failed"].includes(job.status));
  const blockers = input.jobs.filter((job) => ["blocked", "failed"].includes(job.status));
  return [
    `# ${input.business.name}`,
    "",
    `Business ID: ${input.business.id}`,
    `Slug: ${input.business.slug}`,
    `Status: ${input.business.status}`,
    `Site status: ${input.business.site_status ?? "unknown"}`,
    `Pitch: ${input.business.public_pitch ?? ""}`,
    "",
    "## Current Evidence",
    `- Campaigns tracked: ${input.campaigns.length}`,
    `- Active/unfinished jobs: ${activeJobs.length}`,
    `- Blocked/failed jobs: ${blockers.length}`,
    `- Memory records: ${input.memories.length}`,
    `- Budget accounts: ${input.budgets.length}`,
    `- Completed deployments: ${input.deployments.filter((row) => row.status === "completed").length}`,
    `- Social posts tracked: ${input.posts.length}`,
    `- Revenue events tracked: ${input.revenue.length}`,
    latestDeployment?.alias_url || latestDeployment?.deployment_url ? `- Latest public URL: ${latestDeployment.alias_url ?? latestDeployment.deployment_url}` : "- Latest public URL: unknown",
    "",
    "## Rule",
    "The CEO must treat missing files or missing receipts as unknown, not as success."
  ].join("\n") + "\n";
}

function blockersMarkdown(input: { jobs: Awaited<ReturnType<typeof workspaceRows>>[3]; missingCapabilities: string[] }) {
  const blockers = input.jobs.filter((job) => ["blocked", "failed"].includes(job.status));
  const lines = ["# Blockers", ""];
  if (!blockers.length && !input.missingCapabilities.length) lines.push("No blockers are currently tracked.");
  for (const job of blockers) {
    lines.push(`- ${job.workflow_id} (${job.status}): ${job.error ?? "No error recorded."}`);
  }
  for (const missing of input.missingCapabilities) lines.push(`- Missing capability: ${missing}`);
  return `${lines.join("\n")}\n`;
}

function memoryMarkdown(namespace: string, rows: Array<{
  namespace: string;
  memory_key: string;
  title: string;
  content: string;
  campaign_id: string | null;
  evidence: unknown;
  confidence: string | null;
  updated_at: string;
}>) {
  const scoped = rows.filter((row) => row.namespace === namespace);
  if (!scoped.length) return `# ${namespace}\n\nNo tracked ${namespace} memory yet.\n`;
  return [
    `# ${namespace}`,
    "",
    ...scoped.flatMap((row) => [
      `## ${row.title}`,
      `Key: ${row.memory_key}`,
      `Updated: ${row.updated_at}`,
      row.confidence ? `Confidence: ${row.confidence}` : "",
      "",
      row.content,
      ""
    ])
  ].filter(Boolean).join("\n") + "\n";
}

function ceoMapMarkdown() {
  return [
    "# CEO Map",
    "",
    "Read this workspace like a repo. Postgres remains the source of truth; generated files mirror durable facts for agent inspection.",
    "",
    "## Read First",
    "- `ceo/brief.md`: concise evolving operating brief owned by the CEO.",
    "- `ceo/doctrine.md`: durable operating guardrails and standing obligations.",
    "- `state/index.json`: generated map of authoritative paths and what each path means.",
    "- `state/inbox.jsonl`: generated evidence feed from jobs, events, receipts, capabilities, and business facts.",
    "- `state/current.md`: generated current snapshot.",
    "- `state/blockers.md`: generated failures and missing capabilities.",
    "",
    "## Generated Read-Only",
    "- `state/`: snapshots, inbox, facts, blockers, indexes.",
    "- `jobs/`: queue state mirrored from Postgres.",
    "- `ledger/`: budget accounts, reservations, receipts.",
    "- `tools/`: capability reports and missing key instructions.",
    "- `website/`: deploy/auth/checkout receipts and notes.",
    "- `receipts/`: durable external side-effect receipts.",
    "",
    "## Agent Authored",
    "- `ceo/`: operating brief, decisions, open questions.",
    "- `goals/`: active goals and acceptance evidence the CEO cares about.",
    "- `product/`: product strategy, specs, acceptance criteria, V1 definition, search visibility, conversion review, and measurement plan.",
    "- `outreach/`: channel strategy, audiences, positioning, platform voice, content engine, outreach pipeline, and paid-media review.",
    "- `campaigns/`: isolated campaign workspaces.",
    "- `memory/`: structured lessons, pricing ideas, positioning, objections, strategy, and any other business memory the CEO chooses.",
    "- `agents/`: subagent scopes and handoffs.",
    "",
    "## Rule",
    "If evidence is not present in generated files, receipts, or explicit agent-authored notes, treat it as unknown."
  ].join("\n") + "\n";
}

function ceoDoctrineMarkdown() {
  return [
    "# CEO Doctrine",
    "",
    "Absolute directives:",
    "- Never claim product completion, deployment, auth, checkout, posting, spend, revenue, or vendor side effects without explicit evidence.",
    "- Keep all business-specific work inside this workspace unless a path is explicitly marked platform/global.",
    "- Do not write to generated read-only paths. Ask the runner/backend to create receipts instead.",
    "- Product/value delivery and outreach/demand creation must both remain alive; choose the next work from evidence instead of a fixed stage ladder.",
    "- Prefer impactful work over cheap work, while respecting budget caps and capability availability.",
    "- When a tool is unavailable, state the missing capability/key and do not try the side effect.",
    "",
    "Standing obligations:",
    ...standingObligations.map((item) => `- ${item.key}: ${item.directive}`),
    "",
    "Learning:",
    "- Improve business memory when evidence changes strategy, positioning, pricing, product direction, distribution, objections, or failure recovery.",
    "- Create memory files only when useful. The structure is allowed to evolve per business."
  ].join("\n") + "\n";
}

function ceoBriefMarkdown(business: WorkspaceBusiness) {
  return [
    "# CEO Brief",
    "",
    `Business: ${business.name}`,
    `Slug: ${business.slug}`,
    `Pitch: ${business.public_pitch ?? business.public_title ?? "unknown"}`,
    "",
    "Current priority: unknown until the CEO inspects evidence.",
    "",
    "Where to look first:",
    "- `ceo/map.md`",
    "- `ceo/doctrine.md`",
    "- `state/index.json`",
    "- `state/inbox.jsonl`",
    "- `state/current.md`",
    "- `state/blockers.md`",
    "",
    "Keep this file short and update it when the operating picture changes."
  ].join("\n") + "\n";
}

function workspaceIndex(input: {
  business: WorkspaceBusiness;
  campaigns: Awaited<ReturnType<typeof workspaceRows>>[0];
  capabilities: ToolCapabilityLike[];
  syncedAt: string;
  reason?: string | null;
}) {
  return {
    version: 1,
    synced_at: input.syncedAt,
    sync_reason: input.reason ?? null,
    business: {
      id: input.business.id,
      slug: input.business.slug,
      name: input.business.name,
      status: input.business.status
    },
    read_first: ceoBootFiles,
    generated_readonly_roots: generatedReadonlyRoots,
    agent_authored_roots: agentAuthoredRoots,
    standing_obligations: standingObligations,
    files_by_purpose: {
      operating_brief: ["ceo/brief.md", "ceo/doctrine.md", "ceo/map.md"],
      facts_and_evidence: ["state/current.md", "state/facts.jsonl", "state/inbox.jsonl", "state/blockers.md"],
      queue: ["jobs/queued.jsonl", "jobs/running.jsonl", "jobs/completed.jsonl", "jobs/blocked.jsonl"],
      budget: ["ledger/budget.json", "ledger/reservations.jsonl", "ledger/receipts.jsonl"],
      capabilities: ["tools/availability.json", "tools/missing-keys.md"],
      product: ["product/", "product/search-visibility.md", "product/conversion-review.md", "product/measurement-plan.md"],
      outreach: ["outreach/", "outreach/content-engine.md", "outreach/outreach-pipeline.md", "outreach/paid-media-review.md"],
      business_skills: [
        "memory/product-marketing-context.md",
        "product/search-visibility.md",
        "product/conversion-review.md",
        "product/measurement-plan.md",
        "outreach/content-engine.md",
        "outreach/outreach-pipeline.md",
        "outreach/paid-media-review.md"
      ],
      campaigns: input.campaigns.map((campaign) => campaign.workspace_path || `campaigns/${campaign.slug}`),
      memory: ["memory/"],
      receipts: ["receipts/", "website/deployments.jsonl"]
    },
    capability_summary: input.capabilities.map((capability) => ({
      key: capability.key,
      label: capability.label,
      can_run: capability.canRun,
      missing: capability.missing
    })),
    unknown_rule: "If a fact is not in generated evidence or explicit agent-authored notes, it is unknown."
  };
}

type ToolCapabilityLike = Awaited<ReturnType<typeof listToolCapabilities>>[number];

function evidenceInbox(input: {
  jobs: Awaited<ReturnType<typeof workspaceRows>>[3];
  ledger: Awaited<ReturnType<typeof workspaceRows>>[5];
  deployments: Awaited<ReturnType<typeof workspaceRows>>[6];
  posts: Awaited<ReturnType<typeof workspaceRows>>[7];
  revenue: Awaited<ReturnType<typeof workspaceRows>>[8];
  communityTargets: Awaited<ReturnType<typeof workspaceRows>>[9];
  leads: Awaited<ReturnType<typeof workspaceRows>>[10];
  mediaJobs: Awaited<ReturnType<typeof workspaceRows>>[11];
  events: Awaited<ReturnType<typeof workspaceRows>>[12];
  capabilities: ToolCapabilityLike[];
}) {
  const rows = [
    ...input.events.map((row) => ({ ...row, at: row.created_at, kind: "event", event_kind: row.kind })),
    ...input.jobs.map((row) => ({ at: row.updated_at, kind: "workflow_job", id: row.id, workflow_id: row.workflow_id, lane: row.lane, status: row.status, error: row.error, payload: row.payload })),
    ...input.ledger.map((row) => ({ ...row, at: row.created_at, kind: "budget_ledger", ledger_kind: row.kind })),
    ...input.deployments.map((row) => ({ ...row, at: row.created_at, kind: "deployment" })),
    ...input.posts.map((row) => ({ ...row, at: row.created_at, kind: "social_post" })),
    ...input.revenue.map((row) => ({ ...row, at: row.occurred_at, kind: "revenue_event" })),
    ...input.communityTargets.map((row) => ({ ...row, at: row.created_at, kind: "community_target" })),
    ...input.leads.map((row) => ({ ...row, at: row.created_at, kind: "lead" })),
    ...input.mediaJobs.map((row) => ({ ...row, at: row.created_at, kind: "media_job" })),
    ...input.capabilities.filter((capability) => !capability.canRun).map((capability) => ({
      at: new Date().toISOString(),
      kind: "missing_capability",
      key: capability.key,
      label: capability.label,
      reason: capability.reason,
      missing: capability.missing,
      setup: capability.setup
    }))
  ];
  return rows
    .sort((a, b) => String(b.at ?? "").localeCompare(String(a.at ?? "")))
    .slice(0, 500);
}

async function ensureBaseTree(root: string, business: WorkspaceBusiness) {
  const dirs = [
    "ceo",
    "state",
    "goals",
    "product",
    "outreach",
    "website",
    "campaigns",
    "memory",
    "ledger",
    "jobs",
    "tools",
    "agents",
    "receipts"
  ];
  await Promise.all(dirs.map((dir) => fs.mkdir(path.join(root, dir), { recursive: true })));
  await writeIfAbsent(path.join(root, "README.md"), [
    `# ${business.name}`,
    "",
    "This is the Takyon business workspace.",
    "",
    "The CEO can inspect this tree like a repo. Postgres remains the source of truth for locks, queues, budgets, auth, receipts, and provider state; this workspace is the synchronized agent-facing view.",
    "",
    "Rules:",
    "- Missing evidence means unknown.",
    "- Provider side effects require receipts.",
    "- Keep business-specific work inside this directory.",
    "- Use explicitly marked global/platform memory only for cross-business outreach identity and operator-level preferences."
  ].join("\n") + "\n");
  await writeIfAbsent(path.join(root, "ceo", "brief.md"), ceoBriefMarkdown(business));
  await writeIfAbsent(path.join(root, "ceo", "map.md"), ceoMapMarkdown());
  await writeIfAbsent(path.join(root, "ceo", "doctrine.md"), ceoDoctrineMarkdown());
  await writeIfAbsent(path.join(root, "goals", "README.md"), "# Goals\n\nCEO-created goals and acceptance evidence live here.\n");
  await writeIfAbsent(path.join(root, "product", "README.md"), "# Product\n\nProduct strategy, specs, V1 definition, and acceptance evidence live here.\n");
  await writeIfAbsent(path.join(root, "outreach", "README.md"), "# Outreach\n\nDemand creation strategy, channels, posts, community notes, and platform voice live here.\n");
  await writeIfAbsent(path.join(root, "campaigns", "README.md"), "# Campaigns\n\nEach campaign gets its own isolated folder.\n");
  await writeIfAbsent(path.join(root, "memory", "README.md"), "# Memory\n\nBusiness memory is agent-authored and flexible. Create files when they earn their keep: strategy, pricing, positioning, objections, distribution, failures, product lessons, or whatever this business needs.\n");
  await writeIfAbsent(path.join(root, "agents", "README.md"), "# Agents\n\nAgent and subagent notes live here when they are material to the business.\n");
}

export async function syncBusinessWorkspace(input: { businessId: string; profileId?: string | null; reason?: string | null }) {
  const business = await loadBusiness(input.businessId);
  const root = businessRoot(business);
  await ensureBaseTree(root, business);
  const [
    campaigns,
    memories,
    documents,
    jobs,
    budgets,
    ledger,
    deployments,
    posts,
    revenue,
    communityTargets,
    leads,
    mediaJobs,
    events
  ] = await workspaceRows(input.businessId);
  const capabilities = await listToolCapabilities({ businessId: input.businessId, profileId: input.profileId ?? null });
  const missingCapabilities = capabilities.filter((capability) => !capability.canRun).map((capability) => `${capability.label}: ${capability.reason}`);
  const syncedAt = new Date().toISOString();

  await writeText(path.join(root, ".takyon.json"), json({
    version: 1,
    business_id: business.id,
    slug: business.slug,
    name: business.name,
    synced_at: syncedAt,
    sync_reason: input.reason ?? null
  }));
  await writeText(path.join(root, "state", "index.json"), json(workspaceIndex({ business, campaigns, capabilities, syncedAt, reason: input.reason ?? null })));
  await writeText(path.join(root, "state", "current.md"), currentMarkdown({ business, campaigns, memories, jobs, budgets, deployments, posts, revenue }));
  await writeText(path.join(root, "state", "facts.jsonl"), jsonl([
    { kind: "business", business },
    { kind: "counts", campaigns: campaigns.length, jobs: jobs.length, memories: memories.length, documents: documents.length, budgets: budgets.length, deployments: deployments.length, posts: posts.length, revenue_events: revenue.length, community_targets: communityTargets.length, leads: leads.length, media_jobs: mediaJobs.length },
    ...campaigns.map((row) => ({ ...row, kind: "campaign", campaign_kind: row.kind })),
    ...memories.map((row) => ({ kind: "memory", ...row })),
    ...deployments.map((row) => ({ kind: "deployment", ...row })),
    ...posts.map((row) => ({ kind: "social_post", ...row })),
    ...revenue.map((row) => ({ kind: "revenue_event", ...row })),
    ...communityTargets.map((row) => ({ kind: "community_target", ...row })),
    ...leads.map((row) => ({ kind: "lead", ...row })),
    ...mediaJobs.map((row) => ({ kind: "media_job", ...row }))
  ]));
  await writeText(path.join(root, "state", "inbox.jsonl"), jsonl(evidenceInbox({ jobs, ledger, deployments, posts, revenue, communityTargets, leads, mediaJobs, events, capabilities })));
  await writeText(path.join(root, "state", "blockers.md"), blockersMarkdown({ jobs, missingCapabilities }));
  await writeText(path.join(root, "tools", "availability.json"), json(capabilities));
  await writeText(path.join(root, "tools", "missing-keys.md"), [
    "# Missing Keys And Capabilities",
    "",
    ...capabilities.filter((capability) => !capability.canRun).flatMap((capability) => [
      `## ${capability.label}`,
      capability.reason,
      capability.missing.length ? `Missing: ${capability.missing.join(", ")}` : "",
      ...capability.setup.map((step) => `Setup: ${step}`),
      capability.docsUrl ? `Docs: ${capability.docsUrl}` : "",
      ""
    ]).filter(Boolean)
  ].join("\n") + "\n");

  await writeText(path.join(root, "jobs", "queued.jsonl"), jsonl(jobs.filter((job) => job.status === "queued")));
  await writeText(path.join(root, "jobs", "running.jsonl"), jsonl(jobs.filter((job) => job.status === "running")));
  await writeText(path.join(root, "jobs", "completed.jsonl"), jsonl(jobs.filter((job) => job.status === "completed")));
  await writeText(path.join(root, "jobs", "blocked.jsonl"), jsonl(jobs.filter((job) => ["blocked", "failed", "cancelled"].includes(job.status))));
  await writeText(path.join(root, "ledger", "budget.json"), json(budgets.map((account) => ({
    ...account,
    hard_limit_usd: usdFromMicrousd(account.hard_limit_microusd),
    committed_usd: usdFromMicrousd(account.committed_microusd)
  }))));
  await writeText(path.join(root, "ledger", "reservations.jsonl"), jsonl(ledger.filter((row) => row.kind === "reservation")));
  await writeText(path.join(root, "ledger", "receipts.jsonl"), jsonl(ledger));
  await writeText(path.join(root, "website", "deployments.jsonl"), jsonl(deployments));
  await writeText(path.join(root, "website", "auth.md"), documents.find((doc) => /auth/i.test(doc.title))?.content ?? "# Auth\n\nNo explicit auth notes beyond tracked platform routes.\n");
  await writeText(path.join(root, "website", "checkout.md"), documents.find((doc) => /checkout|pricing|payment/i.test(doc.title))?.content ?? "# Checkout\n\nNo explicit checkout notes beyond tracked payment/link rows.\n");

  for (const campaign of campaigns) {
    const campaignRoot = resolveInside(root, campaign.workspace_path || `campaigns/${campaign.slug}`);
    await Promise.all([
      fs.mkdir(path.join(campaignRoot, "receipts"), { recursive: true }),
      fs.mkdir(path.join(campaignRoot, "creatives"), { recursive: true }),
      fs.mkdir(path.join(campaignRoot, "outreach"), { recursive: true }),
      fs.mkdir(path.join(campaignRoot, "research"), { recursive: true })
    ]);
    await writeIfAbsent(path.join(campaignRoot, "brief.md"), [
      `# ${campaign.name}`,
      "",
      `Campaign ID: ${campaign.id}`,
      `Slug: ${campaign.slug}`,
      `Kind: ${campaign.kind}`,
      `Status: ${campaign.status}`,
      `Budget cap: ${campaign.budget_cap_microusd ? `$${usdFromMicrousd(campaign.budget_cap_microusd).toFixed(2)}` : "unset"}`,
      "",
      "## Metadata",
      "```json",
      JSON.stringify(campaign.metadata ?? {}, null, 2),
      "```"
    ].join("\n") + "\n");
    await writeText(path.join(campaignRoot, "state.json"), json({
      id: campaign.id,
      slug: campaign.slug,
      name: campaign.name,
      kind: campaign.kind,
      status: campaign.status,
      workspace_path: campaign.workspace_path,
      updated_at: campaign.updated_at,
      metadata: campaign.metadata
    }));
    await writeText(path.join(campaignRoot, "budget.json"), json({
      campaign_id: campaign.id,
      budget_cap_microusd: campaign.budget_cap_microusd,
      budget_cap_usd: campaign.budget_cap_microusd ? usdFromMicrousd(campaign.budget_cap_microusd) : null,
      ledger: ledger.filter((row) => row.campaign_id === campaign.id)
    }));
    await writeText(path.join(campaignRoot, "worklog.md"), [
      `# ${campaign.name} Worklog`,
      "",
      ...jobs.filter((job) => {
        const payload = job.payload && typeof job.payload === "object" ? job.payload as Record<string, unknown> : {};
        return payload.campaign_id === campaign.id;
      }).map((job) => `- ${job.updated_at}: ${job.workflow_id} ${job.status}${job.error ? ` - ${job.error}` : ""}`)
    ].join("\n") + "\n");
    await writeIfAbsent(path.join(campaignRoot, "learnings.md"), memoryMarkdown("campaign_learning", memories.filter((memory) => memory.campaign_id === campaign.id)));
    await writeText(path.join(campaignRoot, "receipts", "events.jsonl"), jsonl([
      ...posts.filter((row) => row.campaign_id === campaign.id).map((row) => ({ kind: "social_post", ...row })),
      ...revenue.filter((row) => row.campaign_id === campaign.id).map((row) => ({ kind: "revenue_event", ...row })),
      ...mediaJobs.filter((row) => row.campaign_id === campaign.id).map((row) => ({ kind: "media_job", ...row }))
    ]));
  }

  return {
    business,
    root,
    files: await listBusinessWorkspaceFiles({ businessId: input.businessId, rootHint: root })
  };
}

export async function listBusinessWorkspaceFiles(input: { businessId: string; relativePath?: string; rootHint?: string; maxDepth?: number }) {
  const root = input.rootHint ?? businessRoot(await loadBusiness(input.businessId));
  const start = resolveInside(root, input.relativePath ?? ".");
  const maxDepth = Math.max(0, Math.min(input.maxDepth ?? 6, 12));
  const files: WorkspaceFile[] = [];

  async function walk(dir: string, depth: number) {
    if (depth > maxDepth) return;
    const entries = await fs.readdir(dir, { withFileTypes: true }).catch(() => []);
    for (const entry of entries) {
      const absolute = path.join(dir, entry.name);
      const relative = path.relative(root, absolute);
      if (entry.isDirectory()) {
        await walk(absolute, depth + 1);
        continue;
      }
      if (!entry.isFile()) continue;
      const stat = await fs.stat(absolute);
      files.push({ path: relative, bytes: stat.size, updatedAt: stat.mtime.toISOString() });
    }
  }

  await walk(start, 0);
  return files.sort((a, b) => a.path.localeCompare(b.path));
}

export async function readBusinessWorkspaceFile(input: { businessId: string; relativePath: string; maxBytes?: number }) {
  const root = businessRoot(await loadBusiness(input.businessId));
  const filePath = resolveInside(root, input.relativePath);
  const stat = await fs.stat(filePath);
  if (!stat.isFile()) throw new Error(`Business workspace path is not a file: ${input.relativePath}`);
  const maxBytes = Math.max(1, input.maxBytes ?? 80_000);
  const content = await fs.readFile(filePath, "utf8");
  return {
    path: path.relative(root, filePath),
    bytes: stat.size,
    truncated: Buffer.byteLength(content) > maxBytes,
    content: Buffer.byteLength(content) > maxBytes ? content.slice(0, maxBytes) : content
  };
}

export async function writeBusinessWorkspaceFile(input: { businessId: string; relativePath: string; content: string }) {
  const business = await loadBusiness(input.businessId);
  const root = businessRoot(business);
  assertAgentWritable(input.relativePath);
  const guard = runWorkspaceWriteGuards({ relativePath: input.relativePath, content: input.content });
  if (guard.blocked.length) throw new Error(guard.blocked.join("\n"));
  const filePath = resolveInside(root, input.relativePath);
  await writeText(filePath, input.content.endsWith("\n") ? input.content : `${input.content}\n`);
  return { business, root, path: path.relative(root, filePath), warnings: guard.warnings };
}

export async function businessWorkspaceContext(input: { businessId: string; profileId?: string | null }) {
  const synced = await syncBusinessWorkspace({ businessId: input.businessId, profileId: input.profileId ?? null, reason: "ceo_context" });
  const excerpts = [];
  for (const file of ceoBootFiles) {
    const read = await readBusinessWorkspaceFile({ businessId: input.businessId, relativePath: file, maxBytes: 12_000 }).catch(() => null);
    if (read) excerpts.push(read);
  }
  return {
    root: synced.root,
    files: synced.files,
    bootFiles: ceoBootFiles,
    excerpts
  };
}

export async function removeBusinessWorkspace(input: { slug: string }) {
  const root = businessRoot({ slug: input.slug });
  await fs.rm(root, { recursive: true, force: true });
  return root;
}
