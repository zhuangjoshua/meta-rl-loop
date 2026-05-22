import { db } from "./db";
import { createEvent } from "./events";
import { toJson } from "./json";
import { upsertBusinessMemory } from "./business-memory";
import { hashObservabilityValue } from "./observability";

type LearningInput = {
  businessId: string;
  profileId?: string | null;
  sourceWorkflowId?: string | null;
};

type SocialPostRow = {
  id: string;
  campaign_id: string | null;
  provider: string;
  text: string;
  status: string;
  provider_post_id: string | null;
  provider_url: string | null;
  result: unknown;
  error: string | null;
  published_at: string | null;
  created_at: string;
};

type MediaJobRow = {
  id: string;
  campaign_id: string | null;
  provider: string;
  model: string;
  status: string;
  output_url: string | null;
  result: unknown;
  error: string | null;
  created_at: string;
};

type EmailRow = {
  id: string;
  campaign_id: string | null;
  direction: "outbound" | "inbound";
  from_email: string;
  to_email: string;
  subject: string;
  body_text: string;
  status: string;
  provider: string | null;
  audience_type: string;
  sent_at: string | null;
  metadata: unknown;
  result: unknown;
  created_at: string;
};

type LeadRow = {
  id: string;
  campaign_id: string | null;
  email: string | null;
  name: string | null;
  url: string | null;
  source: string;
  status: string;
  last_event: string | null;
  created_at: string;
};

type ProductRunRow = {
  id: string;
  campaign_id: string | null;
  status: string;
  input: unknown;
  output: unknown;
  error: string | null;
  email: string | null;
  created_at: string;
};

type RevenueRow = {
  id: string;
  campaign_id: string | null;
  revenue_type: string;
  status: string;
  currency: string;
  amount_paid_cents: number;
  customer_email: string | null;
  metadata: unknown;
  occurred_at: string;
};

type InboxRow = {
  id: string;
  author_label: string;
  body: string;
  source: string;
  created_at: string;
};

type MetricSnapshot = {
  businessId: string;
  campaignId?: string | null;
  channel: string;
  sourceType: string;
  sourceId: string;
  provider?: string | null;
  providerObjectId?: string | null;
  spendMicrousd?: number | bigint;
  impressions?: number | bigint;
  clicks?: number | bigint;
  replies?: number | bigint;
  conversions?: number | bigint;
  customers?: number | bigint;
  revenueCents?: number | bigint;
  raw?: unknown;
  observedAt?: string | Date | null;
};

type CustomerSignal = {
  businessId: string;
  campaignId?: string | null;
  sourceType: string;
  sourceId: string;
  customerKey?: string | null;
  channel: string;
  responseType: string;
  sentiment: "positive" | "neutral" | "negative" | "unknown";
  intent: "buying" | "activation" | "support" | "objection" | "unsubscribe" | "interest" | "unknown";
  signalStrength: number;
  contentExcerpt?: string;
  raw?: unknown;
  occurredAt?: string | Date | null;
};

function record(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value) ? (value as Record<string, unknown>) : {};
}

function excerpt(value: string | null | undefined, max = 500) {
  const clean = (value ?? "").replace(/\s+/g, " ").trim();
  return clean.length > max ? clean.slice(0, max - 3).trim() + "..." : clean;
}

function numeric(value: unknown) {
  if (typeof value === "number" && Number.isFinite(value)) return value;
  if (typeof value === "string") {
    const parsed = Number(value.replace(/[$,%]/g, ""));
    return Number.isFinite(parsed) ? parsed : 0;
  }
  return 0;
}

function findMetric(value: unknown, names: string[], depth = 0): number {
  if (depth > 5 || value == null) return 0;
  if (Array.isArray(value)) return value.reduce((sum, item) => sum + findMetric(item, names, depth + 1), 0);
  const source = record(value);
  let found = 0;
  for (const [key, item] of Object.entries(source)) {
    const normalized = key.toLowerCase().replace(/[^a-z0-9]/g, "");
    if (names.includes(normalized)) found += numeric(item);
    if (typeof item === "object" && item !== null) found += findMetric(item, names, depth + 1);
  }
  return found;
}

function customerHash(kind: string, value: string | null | undefined) {
  const clean = value?.trim();
  return clean ? hashObservabilityValue(kind, clean) : null;
}

function classifyText(input: string): Pick<CustomerSignal, "sentiment" | "intent" | "signalStrength"> {
  const text = input.toLowerCase();
  const has = (patterns: RegExp[]) => patterns.some((pattern) => pattern.test(text));
  if (has([/\bunsubscribe\b/, /\bstop emailing\b/, /\bremove me\b/])) {
    return { sentiment: "negative", intent: "unsubscribe", signalStrength: 0.1 };
  }
  if (has([/\bbuy\b/, /\bpricing\b/, /\bprice\b/, /\bcheckout\b/, /\bsubscribe\b/, /\bpaid\b/, /\bpurchase\b/])) {
    return { sentiment: "positive", intent: "buying", signalStrength: 1 };
  }
  if (has([/\btry\b/, /\btried\b/, /\busing\b/, /\buse it\b/, /\bsign(ed)? up\b/, /\bupload\b/, /\bcompile\b/])) {
    return { sentiment: "positive", intent: "activation", signalStrength: 0.85 };
  }
  if (has([/\bhelp\b/, /\berror\b/, /\bissue\b/, /\bbroken\b/, /\bbug\b/, /\bfailed?\b/, /\bcan't\b/, /\bcannot\b/])) {
    return { sentiment: has([/\blove\b/, /\bgreat\b/, /\bthanks?\b/]) ? "neutral" : "negative", intent: "support", signalStrength: 0.65 };
  }
  if (has([/\bnot interested\b/, /\btoo expensive\b/, /\balready use\b/, /\bconcern\b/, /\bbut\b/, /\bwhy would\b/])) {
    return { sentiment: "negative", intent: "objection", signalStrength: 0.45 };
  }
  if (has([/\byes\b/, /\binterested\b/, /\blove\b/, /\bgreat\b/, /\buseful\b/, /\bhelpful\b/, /\btell me more\b/])) {
    return { sentiment: "positive", intent: "interest", signalStrength: 0.75 };
  }
  return { sentiment: "unknown", intent: "unknown", signalStrength: 0.3 };
}

function safeCampaignId(...values: unknown[]) {
  for (const value of values) {
    if (typeof value === "string" && /^[0-9a-f-]{36}$/i.test(value)) return value;
    const nested = record(value);
    for (const key of ["campaign_id", "campaignId"]) {
      if (typeof nested[key] === "string" && /^[0-9a-f-]{36}$/i.test(nested[key])) return nested[key] as string;
    }
  }
  return null;
}

async function insertMetricSnapshot(input: MetricSnapshot) {
  const sql = db();
  await sql`
    INSERT INTO campaign_metric_snapshots (
      business_id,
      campaign_id,
      channel,
      source_type,
      source_id,
      provider,
      provider_object_id,
      observed_at,
      spend_microusd,
      impressions,
      clicks,
      replies,
      conversions,
      customers,
      revenue_cents,
      raw
    )
    VALUES (
      ${input.businessId},
      ${input.campaignId ?? null},
      ${input.channel},
      ${input.sourceType},
      ${input.sourceId},
      ${input.provider ?? null},
      ${input.providerObjectId ?? null},
      ${input.observedAt ? new Date(input.observedAt) : new Date()},
      ${String(input.spendMicrousd ?? 0)},
      ${String(input.impressions ?? 0)},
      ${String(input.clicks ?? 0)},
      ${String(input.replies ?? 0)},
      ${String(input.conversions ?? 0)},
      ${String(input.customers ?? 0)},
      ${String(input.revenueCents ?? 0)},
      ${sql.json(toJson(input.raw ?? {}))}
    )
  `;
}

async function upsertCustomerSignal(input: CustomerSignal) {
  const sql = db();
  await sql`
    INSERT INTO customer_response_signals (
      business_id,
      campaign_id,
      source_type,
      source_id,
      customer_key,
      channel,
      response_type,
      sentiment,
      intent,
      signal_strength,
      content_excerpt,
      raw,
      occurred_at
    )
    VALUES (
      ${input.businessId},
      ${input.campaignId ?? null},
      ${input.sourceType},
      ${input.sourceId},
      ${input.customerKey ?? null},
      ${input.channel},
      ${input.responseType},
      ${input.sentiment},
      ${input.intent},
      ${input.signalStrength},
      ${excerpt(input.contentExcerpt)},
      ${sql.json(toJson(input.raw ?? {}))},
      ${input.occurredAt ? new Date(input.occurredAt) : new Date()}
    )
    ON CONFLICT (business_id, source_type, source_id, response_type)
    DO UPDATE SET
      campaign_id = COALESCE(EXCLUDED.campaign_id, customer_response_signals.campaign_id),
      customer_key = COALESCE(EXCLUDED.customer_key, customer_response_signals.customer_key),
      channel = EXCLUDED.channel,
      sentiment = EXCLUDED.sentiment,
      intent = EXCLUDED.intent,
      signal_strength = EXCLUDED.signal_strength,
      content_excerpt = EXCLUDED.content_excerpt,
      raw = customer_response_signals.raw || EXCLUDED.raw,
      occurred_at = EXCLUDED.occurred_at
  `;
}

function campaignMemoryContent(input: {
  sourceWorkflowId?: string | null;
  totals: Record<string, { impressions: number; clicks: number; replies: number; conversions: number; customers: number; revenueCents: number; spendMicrousd: number }>;
  socialPosts: SocialPostRow[];
  mediaJobs: MediaJobRow[];
  revenueEvents: RevenueRow[];
}) {
  const channelRows = Object.entries(input.totals)
    .sort(([, a], [, b]) => b.revenueCents - a.revenueCents || b.replies - a.replies || b.clicks - a.clicks)
    .map(([channel, total]) => {
      const ctr = total.impressions > 0 ? `${((total.clicks / total.impressions) * 100).toFixed(2)}%` : "n/a";
      const revenue = `$${(total.revenueCents / 100).toFixed(2)}`;
      return `- ${channel}: impressions ${total.impressions}, clicks ${total.clicks}, replies ${total.replies}, customers ${total.customers}, conversions ${total.conversions}, revenue ${revenue}, CTR ${ctr}`;
    });
  const best = channelRows[0]?.replace(/^- /, "") ?? "No channel has measurable response yet.";
  const blocked = [...input.socialPosts.filter((row) => row.status === "failed" || row.error), ...input.mediaJobs.filter((row) => row.status === "failed" || row.error)];
  return [
    "# Campaign Learning",
    "",
    `Observed at: ${new Date().toISOString()}`,
    `Source workflow: ${input.sourceWorkflowId ?? "manual/unknown"}`,
    "",
    "## Current Read",
    `- Best available signal: ${best}`,
    `- Revenue events observed: ${input.revenueEvents.length}`,
    `- Blocked/failed growth receipts: ${blocked.length}`,
    "",
    "## Channel Totals",
    channelRows.length ? channelRows.join("\n") : "- No metric snapshots yet.",
    "",
    "## Next Operating Rule",
    input.revenueEvents.length
      ? "- Preserve the message/source that produced paid revenue and test one adjacent variant before broadening spend."
      : blocked.length
        ? "- Fix blocked channels before generating more creative; otherwise Takyon will keep learning from missing distribution."
        : "- Keep collecting response receipts; do not infer winners from creative generation alone."
  ].join("\n");
}

function customerMemoryContent(input: {
  signals: Array<{ channel: string; response_type: string; sentiment: string; intent: string; signal_strength: string; content_excerpt: string; occurred_at: string }>;
  emails: EmailRow[];
  productRuns: ProductRunRow[];
  leads: LeadRow[];
}) {
  const intentCounts = new Map<string, number>();
  const sentimentCounts = new Map<string, number>();
  for (const signal of input.signals) {
    intentCounts.set(signal.intent, (intentCounts.get(signal.intent) ?? 0) + 1);
    sentimentCounts.set(signal.sentiment, (sentimentCounts.get(signal.sentiment) ?? 0) + 1);
  }
  const topIntent = [...intentCounts.entries()].sort((a, b) => b[1] - a[1])[0]?.[0] ?? "unknown";
  const topPositive = input.signals
    .filter((signal) => signal.sentiment === "positive")
    .slice(0, 5)
    .map((signal) => `- ${signal.channel}/${signal.intent}: ${signal.content_excerpt || signal.response_type}`);
  const topObjections = input.signals
    .filter((signal) => signal.intent === "objection" || signal.sentiment === "negative")
    .slice(0, 5)
    .map((signal) => `- ${signal.channel}/${signal.intent}: ${signal.content_excerpt || signal.response_type}`);
  return [
    "# Customer Learning",
    "",
    `Observed at: ${new Date().toISOString()}`,
    "",
    "## Current Read",
    `- Dominant intent: ${topIntent}`,
    `- Customer/response signals recorded: ${input.signals.length}`,
    `- Outreach emails inspected: ${input.emails.length}`,
    `- Product runs inspected: ${input.productRuns.length}`,
    `- Leads inspected: ${input.leads.length}`,
    "",
    "## What People Respond To",
    topPositive.length ? topPositive.join("\n") : "- No clearly positive customer response has been recorded yet.",
    "",
    "## Objections And Support Signals",
    topObjections.length ? topObjections.join("\n") : "- No strong objection/support pattern has been recorded yet.",
    "",
    "## Next Outreach Rule",
    topIntent === "buying"
      ? "- Lead with pricing/checkout clarity and make the next step explicit."
      : topIntent === "activation"
        ? "- Lead with the fastest product action customers are already trying."
        : topIntent === "support"
          ? "- Fix confusing or failing workflow moments before scaling outreach."
          : "- Ask for a concrete yes/no or missing-feature reply so customer response learning improves."
  ].join("\n");
}

export async function observeCampaignAndCustomerLearning(input: LearningInput) {
  const sql = db();
  const [socialPosts, mediaJobs, emails, leads, productRuns, revenueEvents, inboxMessages] = await Promise.all([
    sql<SocialPostRow[]>`
      SELECT id, campaign_id, provider, text, status, provider_post_id, provider_url, result, error, published_at, created_at
      FROM business_social_posts
      WHERE business_id = ${input.businessId}
      ORDER BY created_at DESC
      LIMIT 50
    `,
    sql<MediaJobRow[]>`
      SELECT id, campaign_id, provider, model, status, output_url, result, error, created_at
      FROM media_generation_jobs
      WHERE business_id = ${input.businessId}
      ORDER BY created_at DESC
      LIMIT 50
    `,
    sql<EmailRow[]>`
      SELECT id, campaign_id, direction, from_email, to_email, subject, body_text, status, provider, audience_type, sent_at, metadata, result, created_at
      FROM business_email_messages
      WHERE business_id = ${input.businessId}
      ORDER BY created_at DESC
      LIMIT 100
    `,
    sql<LeadRow[]>`
      SELECT id, campaign_id, email, name, url, source, status, last_event, created_at
      FROM leads
      WHERE business_id = ${input.businessId}
      ORDER BY created_at DESC
      LIMIT 100
    `,
    sql<ProductRunRow[]>`
      SELECT r.id, r.campaign_id, r.status, r.input, r.output, r.error, u.email, r.created_at
      FROM generated_app_product_runs r
      LEFT JOIN generated_app_users u ON u.id = r.generated_app_user_id
      WHERE r.business_id = ${input.businessId}
      ORDER BY r.created_at DESC
      LIMIT 100
    `,
    sql<RevenueRow[]>`
      SELECT id, campaign_id, revenue_type, status, currency, amount_paid_cents, customer_email, metadata, occurred_at
      FROM company_revenue_events
      WHERE business_id = ${input.businessId}
      ORDER BY occurred_at DESC
      LIMIT 100
    `,
    sql<InboxRow[]>`
      SELECT id, author_label, body, source, created_at
      FROM business_inbox_messages
      WHERE business_id = ${input.businessId}
        AND source IN ('support', 'customer', 'email', 'generated_app')
      ORDER BY created_at DESC
      LIMIT 50
    `
  ]);

  const totals: Record<string, { impressions: number; clicks: number; replies: number; conversions: number; customers: number; revenueCents: number; spendMicrousd: number }> = {};
  const addTotals = (channel: string, patch: Partial<(typeof totals)[string]>) => {
    totals[channel] ??= { impressions: 0, clicks: 0, replies: 0, conversions: 0, customers: 0, revenueCents: 0, spendMicrousd: 0 };
    for (const key of Object.keys(patch) as Array<keyof (typeof totals)[string]>) {
      totals[channel][key] += Number(patch[key] ?? 0);
    }
  };

  for (const post of socialPosts) {
    const impressions = findMetric(post.result, ["impressions", "impressioncount", "views", "viewcount"]);
    const clicks = findMetric(post.result, ["clicks", "urlclicks", "linkclicks"]);
    const replies = findMetric(post.result, ["replies", "replycount", "comments", "commentcount"]);
    addTotals(post.provider, { impressions, clicks, replies });
    await insertMetricSnapshot({
      businessId: input.businessId,
      campaignId: safeCampaignId(post.campaign_id, post.result),
      channel: post.provider,
      sourceType: "business_social_post",
      sourceId: post.id,
      provider: post.provider,
      providerObjectId: post.provider_post_id,
      impressions,
      clicks,
      replies,
      raw: { status: post.status, provider_url: post.provider_url, error: post.error, result: post.result },
      observedAt: post.published_at ?? post.created_at
    });
  }

  for (const media of mediaJobs) {
    addTotals("creative", { conversions: media.status === "completed" ? 1 : 0 });
    await insertMetricSnapshot({
      businessId: input.businessId,
      campaignId: safeCampaignId(media.campaign_id, media.result),
      channel: "creative",
      sourceType: "media_generation_job",
      sourceId: media.id,
      provider: media.provider,
      providerObjectId: record(media.result).id as string | null,
      conversions: media.status === "completed" ? 1 : 0,
      raw: { model: media.model, status: media.status, output_url: media.output_url, error: media.error, result: media.result },
      observedAt: media.created_at
    });
  }

  for (const email of emails) {
    const isInbound = email.direction === "inbound";
    if (isInbound) {
      const body = `${email.subject}\n${email.body_text}`;
      const classified = classifyText(body);
      addTotals("email", { replies: 1 });
      await upsertCustomerSignal({
        businessId: input.businessId,
        campaignId: safeCampaignId(email.campaign_id, email.metadata, email.result),
        sourceType: "business_email_message",
        sourceId: email.id,
        customerKey: customerHash("email", email.from_email),
        channel: "email",
        responseType: "email_reply",
        ...classified,
        contentExcerpt: body,
        raw: { subject: email.subject, status: email.status, provider: email.provider, audience_type: email.audience_type },
        occurredAt: email.created_at
      });
    }
    await insertMetricSnapshot({
      businessId: input.businessId,
      campaignId: safeCampaignId(email.campaign_id, email.metadata, email.result),
      channel: "email",
      sourceType: "business_email_message",
      sourceId: email.id,
      provider: email.provider,
      replies: isInbound ? 1 : 0,
      raw: { direction: email.direction, status: email.status, audience_type: email.audience_type, result: email.result },
      observedAt: email.sent_at ?? email.created_at
    });
  }

  for (const lead of leads) {
    const responseType = lead.status === "converted" ? "lead_converted" : lead.status === "qualified" ? "lead_qualified" : lead.status === "contacted" ? "lead_contacted" : "lead_seen";
    const intent = lead.status === "converted" ? "buying" : lead.status === "qualified" ? "interest" : "unknown";
    const sentiment = lead.status === "blocked" || lead.status === "failed" || lead.status === "archived" ? "negative" : lead.status === "candidate" ? "unknown" : "positive";
    await upsertCustomerSignal({
      businessId: input.businessId,
      campaignId: lead.campaign_id,
      sourceType: "lead",
      sourceId: lead.id,
      customerKey: customerHash("customer", lead.email ?? lead.url ?? lead.name ?? lead.id),
      channel: lead.source,
      responseType,
      sentiment,
      intent,
      signalStrength: lead.status === "converted" ? 1 : lead.status === "qualified" ? 0.8 : lead.status === "contacted" ? 0.45 : 0.25,
      contentExcerpt: [lead.name, lead.email, lead.url, lead.last_event].filter(Boolean).join(" "),
      raw: { status: lead.status, source: lead.source, last_event: lead.last_event },
      occurredAt: lead.created_at
    });
  }

  for (const run of productRuns) {
    const inputRecord = record(run.input);
    const outputRecord = record(run.output);
    const brief = typeof inputRecord.brief === "string" ? inputRecord.brief : "";
    const classified = classifyText(`${brief}\n${JSON.stringify(outputRecord)}`);
    await upsertCustomerSignal({
      businessId: input.businessId,
      campaignId: safeCampaignId(run.campaign_id, inputRecord),
      sourceType: "generated_app_product_run",
      sourceId: run.id,
      customerKey: customerHash("email", run.email ?? (typeof inputRecord.email === "string" ? inputRecord.email : null)),
      channel: "product",
      responseType: "product_run",
      sentiment: run.status === "completed" ? (classified.sentiment === "negative" ? "neutral" : classified.sentiment) : "negative",
      intent: classified.intent === "unknown" ? "activation" : classified.intent,
      signalStrength: run.status === "completed" ? Math.max(0.65, classified.signalStrength) : 0.35,
      contentExcerpt: brief || run.error || JSON.stringify(outputRecord).slice(0, 500),
      raw: { status: run.status, error: run.error, output: outputRecord },
      occurredAt: run.created_at
    });
  }

  for (const revenue of revenueEvents) {
    addTotals("revenue", { conversions: 1, customers: revenue.customer_email ? 1 : 0, revenueCents: revenue.amount_paid_cents });
    await insertMetricSnapshot({
      businessId: input.businessId,
      campaignId: safeCampaignId(revenue.campaign_id, revenue.metadata),
      channel: "revenue",
      sourceType: "company_revenue_event",
      sourceId: revenue.id,
      provider: "stripe",
      conversions: 1,
      customers: revenue.customer_email ? 1 : 0,
      revenueCents: revenue.amount_paid_cents,
      raw: { status: revenue.status, revenue_type: revenue.revenue_type, currency: revenue.currency, metadata: revenue.metadata },
      observedAt: revenue.occurred_at
    });
    await upsertCustomerSignal({
      businessId: input.businessId,
      campaignId: safeCampaignId(revenue.campaign_id, revenue.metadata),
      sourceType: "company_revenue_event",
      sourceId: revenue.id,
      customerKey: customerHash("email", revenue.customer_email),
      channel: "revenue",
      responseType: "purchase",
      sentiment: "positive",
      intent: "buying",
      signalStrength: 1,
      contentExcerpt: `${revenue.revenue_type} ${revenue.status} $${(revenue.amount_paid_cents / 100).toFixed(2)}`,
      raw: { status: revenue.status, revenue_type: revenue.revenue_type, currency: revenue.currency },
      occurredAt: revenue.occurred_at
    });
  }

  for (const message of inboxMessages) {
    const classified = classifyText(message.body);
    await upsertCustomerSignal({
      businessId: input.businessId,
      sourceType: "business_inbox_message",
      sourceId: message.id,
      customerKey: customerHash("customer_label", message.author_label),
      channel: message.source,
      responseType: "inbox_message",
      ...classified,
      contentExcerpt: message.body,
      raw: { author_label: message.author_label, source: message.source },
      occurredAt: message.created_at
    });
  }

  const latestSignals = await sql<Array<{ channel: string; response_type: string; sentiment: string; intent: string; signal_strength: string; content_excerpt: string; occurred_at: string }>>`
    SELECT channel, response_type, sentiment, intent, signal_strength::text, content_excerpt, occurred_at
    FROM customer_response_signals
    WHERE business_id = ${input.businessId}
    ORDER BY occurred_at DESC
    LIMIT 80
  `;

  const campaignMemory = await upsertBusinessMemory({
    businessId: input.businessId,
    profileId: input.profileId ?? null,
    namespace: "campaign_learning",
    memoryKey: "latest-campaign-learning",
    title: "Latest campaign learning",
    content: campaignMemoryContent({ sourceWorkflowId: input.sourceWorkflowId, totals, socialPosts, mediaJobs, revenueEvents }),
    evidence: [
      { table: "business_social_posts", count: socialPosts.length },
      { table: "media_generation_jobs", count: mediaJobs.length },
      { table: "business_email_messages", count: emails.length },
      { table: "company_revenue_events", count: revenueEvents.length },
      { table: "campaign_metric_snapshots", note: "snapshots appended during observation" }
    ],
    metadata: { source_workflow_id: input.sourceWorkflowId ?? null }
  });

  const customerMemory = await upsertBusinessMemory({
    businessId: input.businessId,
    profileId: input.profileId ?? null,
    namespace: "customer_learning",
    memoryKey: "latest-customer-learning",
    title: "Latest customer learning",
    content: customerMemoryContent({ signals: latestSignals, emails, productRuns, leads }),
    evidence: [
      { table: "customer_response_signals", count: latestSignals.length },
      { table: "business_email_messages", count: emails.length },
      { table: "generated_app_product_runs", count: productRuns.length },
      { table: "leads", count: leads.length },
      { table: "company_revenue_events", count: revenueEvents.length }
    ],
    metadata: { source_workflow_id: input.sourceWorkflowId ?? null }
  });

  await createEvent({
    businessId: input.businessId,
    actorProfileId: input.profileId ?? null,
    kind: "campaign_customer_learning.observed",
    subjectType: "business_memory",
    subjectId: customerMemory.id,
    payload: {
      campaign_memory_id: campaignMemory.id,
      customer_memory_id: customerMemory.id,
      signals: latestSignals.length,
      source_workflow_id: input.sourceWorkflowId ?? null
    }
  });

  return {
    status: "completed" as const,
    campaignMemoryId: campaignMemory.id,
    customerMemoryId: customerMemory.id,
    metricsFetch: "stored_receipts_only",
    counts: {
      socialPosts: socialPosts.length,
      mediaJobs: mediaJobs.length,
      emails: emails.length,
      leads: leads.length,
      productRuns: productRuns.length,
      revenueEvents: revenueEvents.length,
      customerSignals: latestSignals.length
    }
  };
}
