import { getCompanyBuildInput } from "./generated-apps/records";
import { db } from "./db";
import { listCompaniesForProfile } from "./companies";
import { listBusinessDocuments, type BusinessDocumentRow } from "./documents";
import { listBusinessEmails, type BusinessEmailMessage } from "./business-email";
import { listBusinessMemory } from "./business-memory";
import { listCompanyEvents, type EventRow } from "./events";
import { listInboxMessages, type BusinessInboxMessage } from "./inbox";
import { listLeads, type LeadRow } from "./leads";
import { getBusinessObservabilitySummary, type BusinessObservabilitySummary } from "./observability";
import { listCompanyTasks, type TaskRow } from "./tasks";
import { listWorkflowJobs, type WorkflowJobRow } from "./workflow-jobs";

export type TakyonStatusTone = "green" | "gray" | "amber" | "red" | "blue";

export type TakyonCompanyCard = {
  id: string;
  name: string;
  slug: string;
  status: string;
  role: string;
  href: string;
};

export type TakyonTaskItem = {
  id: string;
  title: string;
  description: string;
  category: string;
  status: string;
  statusLabel: string;
  tone: TakyonStatusTone;
  createdAt: string;
  scheduledAt?: string | null;
};

export type TakyonDocumentItem = {
  id: string;
  title: string;
  kind: string;
  label: string;
  excerpt: string;
  content: string;
  updatedAt: string;
};

export type TakyonDraftItem = {
  id: string;
  title: string;
  body: string;
  status: string;
  statusLabel: string;
  url?: string | null;
  createdAt: string;
};

export type TakyonTargetItem = {
  id: string;
  name: string;
  detail: string;
  status: string;
  source: string;
  url?: string | null;
  createdAt?: string;
};

export type TakyonTeamMember = {
  id: string;
  name: string;
  email: string;
  role: string;
};

export type TakyonActivityItem = {
  id: string;
  title: string;
  description: string;
  status: string;
  tone: TakyonStatusTone;
  createdAt: string;
};

export type TakyonPreview = {
  url: string | null;
  title: string;
  subtitle: string;
  state: "ready" | "working";
};

export type TakyonDashboardModel = {
  company: {
    id: string;
    name: string;
    slug: string;
    title: string;
    pitch: string;
    status: string;
    createdAt: string;
  };
  metrics: {
    revenueCents: number;
    revenueCurrency: string;
    customers: number;
    leads: number;
    posts: number;
    activeTasks: number;
    completedTasks: number;
    chart: number[];
  };
  observability: BusinessObservabilitySummary;
  learning: {
    campaign: string | null;
    customer: string | null;
  };
  previews: {
    site: TakyonPreview;
    product: TakyonPreview;
  };
  tasks: {
    active: TakyonTaskItem[];
    completed: TakyonTaskItem[];
    all: TakyonTaskItem[];
  };
  documents: TakyonDocumentItem[];
  payments: {
    connected: boolean;
    label: string;
    plans: TakyonDraftItem[];
    recent: TakyonDraftItem[];
  };
  support: TakyonDraftItem[];
  social: {
    x: TakyonDraftItem[];
    community: TakyonDraftItem[];
  };
  outreach: {
    leads: TakyonTargetItem[];
    emails: TakyonDraftItem[];
  };
  ads: {
    budgetLabel: string;
    campaigns: TakyonDraftItem[];
  };
  team: TakyonTeamMember[];
  chat: TakyonDraftItem[];
  activity: TakyonActivityItem[];
  live: boolean;
};

type CompanyWorkspaceRow = {
  id: string;
  name: string;
  slug: string;
  status: string;
  created_at: string;
  public_pitch: string | null;
  site_status: string | null;
  site_slug: string | null;
  alias_url: string | null;
  deployment_url: string | null;
};

type TeamRow = {
  profile_id: string;
  name: string | null;
  email: string;
  role: string;
};

type CountRow = {
  generated_users: string;
  social_posts: string;
  community_targets: string;
  leads: string;
  revenue_cents: string;
};

type CronScheduleRow = {
  job_key: string;
  status: "active" | "paused";
  schedule_type: "interval" | "daily";
  interval_seconds: number | null;
  daily_time_utc: string | null;
  next_run_at: string;
  last_error: string | null;
};

type PaymentLinkRow = {
  id: string;
  plan_key: string;
  name: string;
  unit_amount_cents: number;
  currency: string;
  stripe_payment_link_url: string;
  active: boolean;
  created_at: string;
};

type SocialPostRow = {
  id: string;
  provider: string;
  text: string;
  status: string;
  provider_url: string | null;
  created_at: string;
  published_at: string | null;
};

type CommunityTargetRow = {
  id: string;
  source: string;
  title: string;
  url: string;
  match_reason: string;
  generated_copy: string;
  created_at: string;
};

type MediaJobRow = {
  id: string;
  provider: string;
  model: string;
  status: string;
  prompt: string;
  output_url: string | null;
  error: string | null;
  created_at: string;
};

function sentence(value: string, max = 180) {
  const clean = value.replace(/\s+/g, " ").trim();
  if (clean.length <= max) return clean;
  return `${clean.slice(0, max - 1).trim()}...`;
}

function statusTone(status: string): TakyonStatusTone {
  if (["completed", "published", "active", "ready"].includes(status)) return "green";
  if (["failed", "cancelled", "offline"].includes(status)) return "red";
  if (["blocked", "paused"].includes(status)) return "amber";
  if (["running", "queued", "draft"].includes(status)) return "blue";
  return "gray";
}

function statusLabel(status: string) {
  return status.replace(/_/g, " ");
}

function mapTask(task: TaskRow): TakyonTaskItem {
  return {
    id: task.id,
    title: task.title,
    description: task.description || task.category,
    category: task.category,
    status: task.status,
    statusLabel: statusLabel(task.status),
    tone: statusTone(task.status),
    createdAt: task.created_at
  };
}

function workflowTitle(job: WorkflowJobRow) {
  return workflowIdTitle(job.workflow_id);
}

function workflowIdTitle(workflowId: string) {
  return workflowId
    .replace(/^generated_app_/, "app_")
    .replace(/_/g, " ")
    .replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function mapWorkflowJobTask(job: WorkflowJobRow, completedWorkflowIds: Set<string>): TakyonTaskItem {
  const result = job.result && typeof job.result === "object" ? (job.result as Record<string, unknown>) : {};
  const reason = typeof result.reason === "string" ? result.reason : job.error || "";
  const title = workflowTitle(job);
  const waitingOn = job.status === "queued" ? job.dependencies.find((dependency) => !completedWorkflowIds.has(dependency)) : null;
  return {
    id: `job:${job.id}`,
    title,
    description: waitingOn ? `${title} is waiting for ${workflowIdTitle(waitingOn)}.` : reason || `${title} is ${statusLabel(job.status)}.`,
    category: job.lane,
    status: job.status,
    statusLabel: waitingOn ? `waiting for ${workflowIdTitle(waitingOn).toLowerCase()}` : statusLabel(job.status),
    tone: statusTone(job.status),
    createdAt: job.created_at,
    scheduledAt: job.status === "queued" && !waitingOn ? job.run_after : null
  };
}

function mapCronTask(row: CronScheduleRow): TakyonTaskItem {
  const schedule =
    row.schedule_type === "daily" && row.daily_time_utc
      ? `Daily CEO wakeup at ${row.daily_time_utc.slice(0, 5)} UTC.`
      : row.interval_seconds
        ? `Runs every ${Math.round(row.interval_seconds / 60)} minutes.`
        : "Scheduled cron wakeup.";
  return {
    id: `cron:${row.job_key}`,
    title: "Next CEO Wakeup",
    description: row.last_error || schedule,
    category: "cron",
    status: row.status === "active" ? "scheduled" : "paused",
    statusLabel: row.status === "active" ? "next" : "paused",
    tone: row.status === "active" ? "blue" : "amber",
    createdAt: row.next_run_at,
    scheduledAt: row.next_run_at
  };
}

function mapDocument(document: BusinessDocumentRow): TakyonDocumentItem {
  const label = document.kind.replace(/_/g, " ");
  return {
    id: document.id,
    title: document.title,
    kind: document.kind,
    label,
    excerpt: sentence(document.content, 140),
    content: document.content,
    updatedAt: document.updated_at
  };
}

function isSeededPlaceholderDocument(document: BusinessDocumentRow) {
  const metadata = document.metadata && typeof document.metadata === "object" && !Array.isArray(document.metadata)
    ? (document.metadata as Record<string, unknown>)
    : {};
  return metadata.seeded === true;
}

function mapInboxMessage(message: BusinessInboxMessage): TakyonDraftItem {
  return {
    id: message.id,
    title: message.author_label,
    body: message.body,
    status: message.source,
    statusLabel: message.source.replace(/_/g, " "),
    createdAt: message.created_at
  };
}

function mapWorkflowActivity(job: WorkflowJobRow): TakyonActivityItem {
  return {
    id: job.id,
    title: job.workflow_id.replace(/_/g, " "),
    description: job.error || `${job.lane.replace(/_/g, " ")} lane is ${job.status}.`,
    status: job.status,
    tone: statusTone(job.status),
    createdAt: job.updated_at
  };
}

function mapEventActivity(event: EventRow): TakyonActivityItem {
  return {
    id: event.id,
    title: event.kind.replace(/[._]/g, " "),
    description: event.subject_type || "event",
    status: "event",
    tone: "gray",
    createdAt: event.created_at
  };
}

function mapPaymentPlan(link: PaymentLinkRow): TakyonDraftItem {
  const amount = new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: link.currency.toUpperCase(),
    maximumFractionDigits: 0
  }).format(link.unit_amount_cents / 100);
  return {
    id: link.id,
    title: link.name || link.plan_key,
    body: `${amount} / month`,
    status: link.active ? "active" : "inactive",
    statusLabel: link.active ? "active" : "inactive",
    url: link.stripe_payment_link_url,
    createdAt: link.created_at
  };
}

function mapSocialPost(post: SocialPostRow): TakyonDraftItem {
  return {
    id: post.id,
    title: post.provider.toUpperCase(),
    body: post.text,
    status: post.status,
    statusLabel: "published",
    url: post.provider_url,
    createdAt: post.published_at || post.created_at
  };
}

function communityChannelLabel(target: CommunityTargetRow) {
  if (target.source === "reddit") {
    const match = target.url.match(/reddit\.com\/r\/([^/]+)/i);
    return match?.[1] ? `r/${decodeURIComponent(match[1])}` : "Reddit";
  }
  if (target.source === "producthunt") return "Product Hunt";
  if (target.source === "indiehackers") return "Indie Hackers";
  return target.source;
}

function mapCommunityPost(target: CommunityTargetRow): TakyonDraftItem {
  const channel = communityChannelLabel(target);
  return {
    id: target.id,
    title: `Post for ${channel}`,
    body: target.generated_copy || target.match_reason || target.title,
    status: "ready",
    statusLabel: "sent",
    url: target.url,
    createdAt: target.created_at
  };
}

function mapLead(lead: LeadRow): TakyonTargetItem {
  return {
    id: lead.id,
    name: lead.name || lead.email || lead.url || "Lead",
    detail: [lead.email, lead.url, lead.source].filter(Boolean).join(" - "),
    status: lead.status === "new" ? "found" : lead.status,
    source: lead.source,
    url: lead.url ?? undefined,
    createdAt: lead.created_at
  };
}

function mapEmailLead(message: BusinessEmailMessage): TakyonTargetItem {
  const delivered = Boolean(message.sent_at) || ["sent", "delivered", "completed"].includes(message.status);
  return {
    id: `email:${message.id}`,
    name: message.to_email,
    detail: message.subject,
    status: delivered ? "emailed" : "email ready",
    source: message.provider || "email",
    createdAt: message.sent_at || message.created_at
  };
}

function mapBusinessEmail(message: BusinessEmailMessage): TakyonDraftItem {
  return {
    id: message.id,
    title: message.subject,
    body: message.body_text,
    status: message.status,
    statusLabel: statusLabel(message.status),
    createdAt: message.sent_at || message.created_at
  };
}

function mapMediaJob(job: MediaJobRow): TakyonDraftItem {
  return {
    id: job.id,
    title: `${job.provider} ${job.model}`,
    body: job.error || sentence(job.prompt, 220),
    status: job.status,
    statusLabel: job.output_url ? "completed" : job.status === "failed" ? "blocked" : "generating",
    url: job.output_url,
    createdAt: job.created_at
  };
}

function previewState(status: string | null | undefined, url: string | null): "ready" | "working" {
  if (url && (status === "published" || status === "completed")) return "ready";
  return "working";
}

function activityChart(jobs: WorkflowJobRow[], revenueCents: number) {
  const completed = jobs.filter((job) => job.status === "completed").length;
  const running = jobs.filter((job) => job.status === "running").length;
  return [0, completed, completed + running, completed + running, Math.max(completed, 1), completed + running, Math.max(completed + running, revenueCents > 0 ? completed + 2 : completed)];
}

function latestJobsByWorkflow(jobs: WorkflowJobRow[]) {
  const latest = new Map<string, WorkflowJobRow>();
  for (const job of jobs) {
    const existing = latest.get(job.workflow_id);
    if (!existing || Date.parse(job.created_at) > Date.parse(existing.created_at)) {
      latest.set(job.workflow_id, job);
    }
  }
  return [...latest.values()];
}

export async function listTakyonCompanies(profileId: string): Promise<TakyonCompanyCard[]> {
  const companies = await listCompaniesForProfile(profileId, 48);
  return companies.map((company) => ({
    id: company.id,
    name: company.name,
    slug: company.slug,
    status: company.site_status || company.status,
    role: "Owner",
    href: `/dashboard/companies/${company.id}`
  }));
}

export async function getTakyonDashboardModel(companyId: string): Promise<TakyonDashboardModel> {
  const sql = db();
  const companyRows = await sql<CompanyWorkspaceRow[]>`
    SELECT
      b.id,
      b.name,
      b.slug,
      b.status,
      b.created_at,
      cs.public_pitch,
      cs.status AS site_status,
      cs.slug AS site_slug,
      deploy.alias_url,
      deploy.deployment_url
    FROM businesses b
    LEFT JOIN company_sites cs ON cs.business_id = b.id
    LEFT JOIN LATERAL (
      SELECT alias_url, deployment_url
      FROM generated_app_deployments
      WHERE business_id = b.id
        AND status = 'completed'
      ORDER BY created_at DESC
      LIMIT 1
    ) deploy ON true
    WHERE b.id = ${companyId}
    LIMIT 1
  `;
  const company = companyRows[0];
  if (!company) throw new Error("Company not found.");

  const [documents, inbox, jobs, tasks, events, team, counts, paymentLinks, socialPosts, communityTargets, leads, emails, mediaJobs, buildInput, cronRows, observability, memory] =
    await Promise.all([
      listBusinessDocuments(companyId, 80),
      listInboxMessages(companyId, 60),
      listWorkflowJobs(companyId, 80),
      listCompanyTasks(companyId, 60),
      listCompanyEvents(companyId, 80),
      sql<TeamRow[]>`
        SELECT bm.profile_id, p.name, p.email, bm.role
        FROM business_memberships bm
        JOIN profiles p ON p.id = bm.profile_id
        WHERE bm.business_id = ${companyId}
        ORDER BY bm.created_at ASC
      `,
      sql<CountRow[]>`
        SELECT
          (SELECT COUNT(*) FROM generated_app_users WHERE business_id = ${companyId})::text AS generated_users,
          (SELECT COUNT(*) FROM business_social_posts WHERE business_id = ${companyId})::text AS social_posts,
          (SELECT COUNT(*) FROM community_targets WHERE business_id = ${companyId})::text AS community_targets,
          (SELECT COUNT(*) FROM leads WHERE business_id = ${companyId})::text AS leads,
          COALESCE((SELECT SUM(amount_paid_cents) FROM company_revenue_events WHERE business_id = ${companyId} AND status IN ('paid', 'succeeded', 'complete', 'completed')), 0)::text AS revenue_cents
      `,
      sql<PaymentLinkRow[]>`
        SELECT id, plan_key, name, unit_amount_cents, currency, stripe_payment_link_url, active, created_at
        FROM company_payment_links
        WHERE business_id = ${companyId}
        ORDER BY created_at DESC
        LIMIT 8
      `,
      sql<SocialPostRow[]>`
        SELECT id, provider, text, status, provider_url, created_at, published_at
        FROM business_social_posts
        WHERE business_id = ${companyId}
        ORDER BY created_at DESC
        LIMIT 8
      `,
      sql<CommunityTargetRow[]>`
        SELECT id, source, title, url, match_reason, generated_copy, created_at
        FROM community_targets
        WHERE business_id = ${companyId}
        ORDER BY created_at DESC
        LIMIT 8
      `,
      listLeads(companyId),
      listBusinessEmails(companyId),
      sql<MediaJobRow[]>`
        SELECT id, provider, model, status, prompt, output_url, error, created_at
        FROM media_generation_jobs
        WHERE business_id = ${companyId}
        ORDER BY created_at DESC
        LIMIT 8
      `,
      getCompanyBuildInput(companyId),
      sql<CronScheduleRow[]>`
        SELECT job_key, status, schedule_type, interval_seconds, daily_time_utc, next_run_at, last_error
        FROM cron_jobs
        WHERE job_key = 'ceo_wakeup'
        LIMIT 1
      `,
      getBusinessObservabilitySummary(companyId),
      listBusinessMemory({ businessId: companyId, limit: 20 })
    ]);

  const countRow = counts[0] ?? { generated_users: "0", social_posts: "0", community_targets: "0", leads: "0", revenue_cents: "0" };
  const revenueCents = Number(countRow.revenue_cents) || 0;
  const currentJobs = latestJobsByWorkflow(jobs);
  const activeTasks = tasks.filter((task) => task.status !== "completed" && task.status !== "cancelled").map(mapTask);
  const completedTasks = tasks.filter((task) => task.status === "completed").map(mapTask);
  const completedWorkflowIds = new Set(currentJobs.filter((job) => job.status === "completed").map((job) => job.workflow_id));
  const laneTasks = currentJobs
    .filter((job) => job.status !== "completed" && job.status !== "cancelled" && job.status !== "failed")
    .map((job) => mapWorkflowJobTask(job, completedWorkflowIds))
    .sort((a, b) => {
      const priority = (task: TakyonTaskItem) =>
        task.status === "running" ? 0 : task.status === "queued" ? 1 : task.status === "blocked" ? 2 : 3;
      return priority(a) - priority(b) || Date.parse(b.createdAt) - Date.parse(a.createdAt);
    });
  const cronTasks = cronRows.map(mapCronTask);
  const appUrl = company.alias_url || company.deployment_url || (company.site_slug ? `https://${company.site_slug}.fourmanifold.com` : null);
  const live = currentJobs.some((job) => job.status === "queued" || job.status === "running");
  const productJobs = currentJobs.filter((job) => job.lane === "product_backend" || job.lane === "product_ui");
  const productBackendDone = productJobs.some((job) => job.lane === "product_backend" && job.status === "completed");
  const productUiDone = productJobs.some((job) => job.lane === "product_ui" && job.status === "completed");
  const latestWebsite = currentJobs.find((job) => job.lane === "website");
  const productRouteAvailable = Boolean(appUrl) && (latestWebsite?.status === "completed" || productBackendDone || productUiDone);
  const outreachEmails = emails.filter((message) => message.audience_type !== "transactional");
  const emailLeadItems = outreachEmails.map(mapEmailLead);
  const directEmailLeadItems = leads.filter((lead) => Boolean(lead.email)).map(mapLead);
  const outreachLeadItems = emailLeadItems.length ? emailLeadItems : directEmailLeadItems;
  const campaignLearning = memory.find((item) => item.namespace === "campaign_learning")?.content ?? null;
  const customerLearning = memory.find((item) => item.namespace === "customer_learning")?.content ?? null;

  return {
    company: {
      id: company.id,
      name: company.name,
      slug: company.slug,
      title: buildInput?.public_pitch || company.public_pitch || company.name,
      pitch: company.public_pitch || "",
      status: company.site_status || company.status,
      createdAt: company.created_at
    },
    metrics: {
      revenueCents,
      revenueCurrency: "usd",
      customers: Number(countRow.generated_users) || 0,
      leads: outreachLeadItems.length,
      posts: Number(countRow.social_posts) || 0,
      activeTasks: activeTasks.length + laneTasks.length,
      completedTasks: completedTasks.length,
      chart: activityChart(currentJobs, revenueCents)
    },
    observability,
    learning: {
      campaign: campaignLearning ? sentence(campaignLearning.replace(/^# .+$/m, ""), 160) : null,
      customer: customerLearning ? sentence(customerLearning.replace(/^# .+$/m, ""), 160) : null
    },
    previews: {
      site: {
        url: appUrl,
        title: company.name,
        subtitle: latestWebsite?.status ? `Website ${latestWebsite.status}` : "Website lane pending",
        state: previewState(company.site_status, appUrl)
      },
      product: {
        url: productRouteAvailable && appUrl ? `${appUrl.replace(/\/$/, "")}/product` : null,
        title: buildInput?.offer || "Product workflow",
        subtitle: productUiDone
          ? "Product UI published"
          : productBackendDone
            ? "Product backend ready"
            : productRouteAvailable
              ? "Initial product route ready"
              : "Product lane building",
        state: productRouteAvailable ? "ready" : "working"
      }
    },
    tasks: {
      active: [...laneTasks, ...activeTasks, ...cronTasks],
      completed: completedTasks,
      all: [...laneTasks, ...cronTasks, ...tasks.map(mapTask)]
    },
    documents: documents.filter((document) => !isSeededPlaceholderDocument(document)).map(mapDocument),
    payments: {
      connected: paymentLinks.some((link) => link.active),
      label: paymentLinks.some((link) => link.active) ? "Stripe checkout" : "Payments",
      plans: paymentLinks.map(mapPaymentPlan),
      recent: []
    },
    support: inbox.filter((message) => message.source === "support").map(mapInboxMessage),
    social: {
      x: socialPosts
        .filter((post) => (post.provider.toLowerCase() === "x" || post.provider.toLowerCase() === "twitter") && Boolean(post.provider_url))
        .map(mapSocialPost),
      community: communityTargets.map(mapCommunityPost)
    },
    outreach: {
      leads: outreachLeadItems,
      emails: outreachEmails.map(mapBusinessEmail)
    },
    ads: {
      budgetLabel: mediaJobs.length ? "Sora creative" : "Creative lane pending",
      campaigns: mediaJobs.map(mapMediaJob)
    },
    team: team.map((member) => ({
      id: member.profile_id,
      name: member.name || member.email,
      email: member.email,
      role: member.role
    })),
    chat: inbox.map(mapInboxMessage).reverse(),
    activity: [...currentJobs.slice(0, 8).map(mapWorkflowActivity), ...events.slice(0, 8).map(mapEventActivity)].sort(
      (a, b) => Date.parse(b.createdAt) - Date.parse(a.createdAt)
    ),
    live
  };
}
