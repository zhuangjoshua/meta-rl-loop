import { db } from "./db";
import { createEvent } from "./events";
import { createInboxMessage } from "./inbox";
import { toJson } from "./json";
import { upsertBusinessDocument } from "./documents";
import { enqueueWorkflowJob } from "./workflow-jobs";

export const RESPONSE_CHECK_POLICY_KEY = "distribution.response_check";

export type BusinessConversationThread = {
  id: string;
  business_id: string;
  campaign_id: string | null;
  source: string;
  external_id: string;
  url: string | null;
  title: string;
  status: "active" | "paused" | "archived";
  last_checked_at: string | null;
  last_message_at: string | null;
  metadata: unknown;
  created_at: string;
  updated_at: string;
};

export type BusinessConversationMessage = {
  id: string;
  business_id: string;
  thread_id: string;
  campaign_id: string | null;
  source: string;
  external_id: string;
  direction: "inbound" | "outbound" | "internal";
  author_label: string;
  body: string;
  status: "needs_response" | "responded" | "ignored" | "archived";
  received_at: string;
  metadata: unknown;
  created_at: string;
  updated_at: string;
};

export type ResponseCheckPolicy = {
  enabled: boolean;
  policyKey: typeof RESPONSE_CHECK_POLICY_KEY;
};

function clean(value: string | null | undefined, fallback = "") {
  return value?.replace(/\s+/g, " ").trim() || fallback;
}

function threadExternalId(input: { source: string; externalId?: string | null; url?: string | null; title: string }) {
  if (input.externalId?.trim()) return input.externalId.trim();
  if (input.url?.trim()) return input.url.trim();
  return `${input.source}:${input.title}`.toLowerCase().replace(/[^a-z0-9:._/-]+/g, "-").slice(0, 180);
}

export async function getDistributionResponseCheckPolicy(businessId: string): Promise<ResponseCheckPolicy> {
  const sql = db();
  const rows = await sql<{ enabled: boolean }[]>`
    SELECT COALESCE(cap.requires_approval, ap.default_requires_approval, true) AS enabled
    FROM action_policies ap
    LEFT JOIN company_action_policies cap
      ON cap.policy_key = ap.policy_key
     AND cap.business_id = ${businessId}
    WHERE ap.policy_key = ${RESPONSE_CHECK_POLICY_KEY}
    LIMIT 1
  `;
  return { enabled: rows[0]?.enabled ?? true, policyKey: RESPONSE_CHECK_POLICY_KEY };
}

export async function setDistributionResponseCheckPolicy(input: {
  businessId: string;
  profileId?: string | null;
  enabled: boolean;
}) {
  const sql = db();
  await sql`
    INSERT INTO company_action_policies (business_id, policy_key, requires_approval, metadata)
    VALUES (
      ${input.businessId},
      ${RESPONSE_CHECK_POLICY_KEY},
      ${input.enabled},
      ${sql.json(toJson({ source: "takyon_dashboard", updated_by_profile_id: input.profileId ?? null }))}
    )
    ON CONFLICT (business_id, policy_key)
    DO UPDATE SET
      requires_approval = EXCLUDED.requires_approval,
      metadata = company_action_policies.metadata || EXCLUDED.metadata,
      updated_at = now()
  `;
  await createEvent({
    businessId: input.businessId,
    actorProfileId: input.profileId ?? null,
    kind: "distribution.response_check_policy_updated",
    subjectType: "action_policy",
    subjectId: RESPONSE_CHECK_POLICY_KEY,
    payload: { enabled: input.enabled }
  });
  return getDistributionResponseCheckPolicy(input.businessId);
}

export async function upsertBusinessConversationThread(input: {
  businessId: string;
  campaignId?: string | null;
  source: string;
  externalId?: string | null;
  url?: string | null;
  title: string;
  metadata?: Record<string, unknown>;
}) {
  const sql = db();
  const source = clean(input.source, "unknown");
  const title = clean(input.title, input.url || source);
  const externalId = threadExternalId({ source, externalId: input.externalId, url: input.url, title });
  const rows = await sql<BusinessConversationThread[]>`
    INSERT INTO business_conversation_threads (
      business_id,
      campaign_id,
      source,
      external_id,
      url,
      title,
      metadata
    )
    VALUES (
      ${input.businessId},
      ${input.campaignId ?? null},
      ${source},
      ${externalId},
      ${input.url ?? null},
      ${title},
      ${sql.json(toJson(input.metadata ?? {}))}
    )
    ON CONFLICT (business_id, source, external_id)
    WHERE external_id <> ''
    DO UPDATE SET
      campaign_id = COALESCE(EXCLUDED.campaign_id, business_conversation_threads.campaign_id),
      url = COALESCE(EXCLUDED.url, business_conversation_threads.url),
      title = EXCLUDED.title,
      status = 'active',
      metadata = business_conversation_threads.metadata || EXCLUDED.metadata,
      updated_at = now()
    RETURNING id, business_id, campaign_id, source, external_id, url, title, status,
              last_checked_at, last_message_at, metadata, created_at, updated_at
  `;
  return rows[0];
}

export async function recordBusinessConversationMessage(input: {
  businessId: string;
  threadId: string;
  campaignId?: string | null;
  source: string;
  externalId?: string | null;
  direction: "inbound" | "outbound" | "internal";
  authorLabel?: string | null;
  body: string;
  status?: "needs_response" | "responded" | "ignored" | "archived";
  receivedAt?: Date | string | null;
  metadata?: Record<string, unknown>;
}) {
  const sql = db();
  const externalId = input.externalId?.trim() || `${input.threadId}:${input.direction}:${Date.parse(String(input.receivedAt ?? new Date()))}:${clean(input.body).slice(0, 80)}`;
  const status = input.status ?? (input.direction === "inbound" ? "needs_response" : "responded");
  const rows = await sql<BusinessConversationMessage[]>`
    INSERT INTO business_conversation_messages (
      business_id,
      thread_id,
      campaign_id,
      source,
      external_id,
      direction,
      author_label,
      body,
      status,
      received_at,
      metadata
    )
    VALUES (
      ${input.businessId},
      ${input.threadId},
      ${input.campaignId ?? null},
      ${clean(input.source, "unknown")},
      ${externalId},
      ${input.direction},
      ${clean(input.authorLabel, input.direction)},
      ${input.body},
      ${status},
      ${input.receivedAt ?? new Date()},
      ${sql.json(toJson(input.metadata ?? {}))}
    )
    ON CONFLICT (business_id, source, external_id)
    WHERE external_id <> ''
    DO UPDATE SET
      status = CASE
        WHEN business_conversation_messages.status = 'responded' THEN business_conversation_messages.status
        ELSE EXCLUDED.status
      END,
      body = EXCLUDED.body,
      metadata = business_conversation_messages.metadata || EXCLUDED.metadata,
      updated_at = now()
    RETURNING id, business_id, thread_id, campaign_id, source, external_id, direction,
              author_label, body, status, received_at, metadata, created_at, updated_at
  `;
  await sql`
    UPDATE business_conversation_threads
    SET last_message_at = GREATEST(COALESCE(last_message_at, ${input.receivedAt ?? new Date()}), ${input.receivedAt ?? new Date()}),
        updated_at = now()
    WHERE id = ${input.threadId}
  `;
  return rows[0];
}

async function syncCommunityThreads(businessId: string) {
  const sql = db();
  const targets = await sql<{ id: string; source: string; title: string; url: string; campaign_id: string | null; match_reason: string | null }[]>`
    SELECT id, source, title, url, campaign_id, match_reason
    FROM community_targets
    WHERE business_id = ${businessId}
    ORDER BY created_at DESC
    LIMIT 100
  `;
  let threads = 0;
  for (const target of targets) {
    await upsertBusinessConversationThread({
      businessId,
      campaignId: target.campaign_id,
      source: target.source,
      externalId: `community_target:${target.id}`,
      url: target.url,
      title: target.title,
      metadata: {
        source_table: "community_targets",
        source_id: target.id,
        match_reason: target.match_reason,
        adapter_status: "thread_known_no_reply_fetcher"
      }
    });
    threads += 1;
  }
  return threads;
}

async function syncInboundEmailReplies(businessId: string) {
  const sql = db();
  const emails = await sql<{
    id: string;
    campaign_id: string | null;
    from_email: string;
    to_email: string;
    subject: string;
    body_text: string;
    provider_message_id: string | null;
    created_at: string;
    metadata: unknown;
  }[]>`
    SELECT id, campaign_id, from_email, to_email, subject, body_text, provider_message_id, created_at::text, metadata
    FROM business_email_messages
    WHERE business_id = ${businessId}
      AND direction = 'inbound'
    ORDER BY created_at DESC
    LIMIT 100
  `;
  let messages = 0;
  for (const email of emails) {
    const thread = await upsertBusinessConversationThread({
      businessId,
      campaignId: email.campaign_id,
      source: "email",
      externalId: `email:${email.from_email}:${email.subject}`,
      title: `Email: ${email.subject}`,
      metadata: { source_table: "business_email_messages", adapter_status: "email_inbound" }
    });
    await recordBusinessConversationMessage({
      businessId,
      threadId: thread.id,
      campaignId: email.campaign_id,
      source: "email",
      externalId: email.provider_message_id || `business_email:${email.id}`,
      direction: "inbound",
      authorLabel: email.from_email,
      body: email.body_text,
      receivedAt: email.created_at,
      metadata: { business_email_message_id: email.id, to_email: email.to_email }
    });
    messages += 1;
  }
  return messages;
}

export async function getBusinessConversationSummary(businessId: string) {
  const sql = db();
  const [summary] = await sql<{
    active_threads: number;
    unresolved_messages: number;
    stale_threads: number;
    last_checked_at: string | null;
    latest_message_at: string | null;
  }[]>`
    SELECT
      (SELECT count(*)::int FROM business_conversation_threads WHERE business_id = ${businessId} AND status = 'active') AS active_threads,
      (SELECT count(*)::int FROM business_conversation_messages WHERE business_id = ${businessId} AND direction = 'inbound' AND status = 'needs_response') AS unresolved_messages,
      (SELECT count(*)::int FROM business_conversation_threads WHERE business_id = ${businessId} AND status = 'active' AND (last_checked_at IS NULL OR last_checked_at < now() - interval '12 hours')) AS stale_threads,
      (SELECT max(last_checked_at)::text FROM business_conversation_threads WHERE business_id = ${businessId} AND status = 'active') AS last_checked_at,
      (SELECT max(received_at)::text FROM business_conversation_messages WHERE business_id = ${businessId}) AS latest_message_at
  `;
  return summary ?? { active_threads: 0, unresolved_messages: 0, stale_threads: 0, last_checked_at: null, latest_message_at: null };
}

export async function listUnresolvedBusinessConversationMessages(businessId: string, limit = 20) {
  const sql = db();
  return sql<BusinessConversationMessage[]>`
    SELECT id, business_id, thread_id, campaign_id, source, external_id, direction,
           author_label, body, status, received_at, metadata, created_at, updated_at
    FROM business_conversation_messages
    WHERE business_id = ${businessId}
      AND direction = 'inbound'
      AND status = 'needs_response'
    ORDER BY received_at DESC
    LIMIT ${Math.max(1, Math.min(limit, 100))}
  `;
}

export async function runConversationWatch(input: { businessId: string; profileId?: string | null }) {
  const policy = await getDistributionResponseCheckPolicy(input.businessId);
  const communityThreads = await syncCommunityThreads(input.businessId);
  const inboundEmailMessages = await syncInboundEmailReplies(input.businessId);
  const sql = db();
  await sql`
    UPDATE business_conversation_threads
    SET last_checked_at = now(),
        metadata = metadata || ${sql.json(toJson({ last_watch: new Date().toISOString() }))}::jsonb,
        updated_at = now()
    WHERE business_id = ${input.businessId}
      AND status = 'active'
  `;
  const summary = await getBusinessConversationSummary(input.businessId);
  const unresolved = await listUnresolvedBusinessConversationMessages(input.businessId, 8);
  const content = [
    "# Conversation Watch",
    "",
    `Updated: ${new Date().toISOString()}`,
    "",
    "## Policy",
    `Response-aware distribution: ${policy.enabled ? "enabled" : "disabled"}`,
    "",
    "## Summary",
    `- Active watched threads: ${summary.active_threads}`,
    `- Unresolved inbound messages: ${summary.unresolved_messages}`,
    `- Community/forum threads known: ${communityThreads}`,
    `- Inbound email replies synced: ${inboundEmailMessages}`,
    "",
    "## Unresolved",
    unresolved.length
      ? unresolved.map((message) => `- ${message.source} from ${message.author_label}: ${clean(message.body, "(empty)").slice(0, 240)}`).join("\n")
      : "- No unresolved inbound messages.",
    "",
    "## Adapter Notes",
    "- Community/forum target URLs are tracked per business as watched threads.",
    "- This workflow normalizes known inbound rows. Platform-specific reply fetchers can add messages to the same tables.",
    "- Missing adapter evidence means unknown, not no replies."
  ].join("\n");

  const document = await upsertBusinessDocument({
    companyId: input.businessId,
    title: "Conversation Watch",
    kind: "task_report",
    source: "workflow",
    content,
    metadata: { workflow_id: "conversation_watch", policy_enabled: policy.enabled, summary },
    replaceMetadata: true
  });

  await createEvent({
    businessId: input.businessId,
    actorProfileId: input.profileId ?? null,
    kind: "conversation_watch.completed",
    subjectType: "business_document",
    subjectId: document.id,
    payload: { summary, community_threads: communityThreads, inbound_email_messages: inboundEmailMessages }
  });

  return { status: "completed" as const, summary, communityThreads, inboundEmailMessages, documentId: document.id };
}

async function queueConversationWatch(input: { businessId: string; profileId?: string | null; reason: string; sourceWorkflowId?: string | null }) {
  return enqueueWorkflowJob({
    companyId: input.businessId,
    profileId: input.profileId ?? null,
    workflowId: "conversation_watch",
    lane: "community",
    priority: 86,
    maxAttempts: 1,
    payload: {
      source: "response_aware_distribution",
      reason: input.reason,
      source_workflow_id: input.sourceWorkflowId ?? null
    }
  });
}

export async function preflightResponseAwareDistribution(input: {
  businessId: string;
  profileId?: string | null;
  workflowId: string;
}) {
  const policy = await getDistributionResponseCheckPolicy(input.businessId);
  if (!policy.enabled) return null;

  const summary = await getBusinessConversationSummary(input.businessId);
  if (summary.unresolved_messages > 0) {
    await enqueueWorkflowJob({
      companyId: input.businessId,
      profileId: input.profileId ?? null,
      workflowId: "ceo_wakeup",
      lane: "ceo",
      priority: 92,
      maxAttempts: 1,
      payload: {
        source: "response_aware_distribution",
        reason: "unresolved_inbound_messages",
        source_workflow_id: input.workflowId
      }
    });
    return {
      status: "blocked" as const,
      reason: `Response-aware distribution is enabled and ${summary.unresolved_messages} inbound response${summary.unresolved_messages === 1 ? "" : "s"} need review before new outward distribution.`,
      summary
    };
  }

  if (summary.active_threads > 0 && summary.stale_threads > 0) {
    const job = await queueConversationWatch({
      businessId: input.businessId,
      profileId: input.profileId ?? null,
      reason: "watched_threads_stale_before_distribution",
      sourceWorkflowId: input.workflowId
    });
    return {
      status: "blocked" as const,
      reason: "Response-aware distribution is enabled and watched conversations need a fresh check before new outward distribution.",
      summary,
      queuedConversationWatchJobId: job.id
    };
  }

  return null;
}

export async function explainResponseAwareBlock(input: {
  businessId: string;
  profileId?: string | null;
  reason: string;
}) {
  await createInboxMessage({
    companyId: input.businessId,
    profileId: input.profileId ?? null,
    authorLabel: "Takyon",
    body: input.reason,
    source: "system"
  });
}
