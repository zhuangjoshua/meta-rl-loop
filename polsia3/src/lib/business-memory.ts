import { db } from "./db";
import { createEvent } from "./events";
import { toJson } from "./json";

export async function upsertBusinessMemory(input: {
  businessId: string;
  namespace?: string;
  memoryKey: string;
  title?: string;
  content: string;
  campaignId?: string | null;
  evidence?: unknown[];
  confidence?: number | null;
  profileId?: string | null;
  metadata?: Record<string, unknown>;
}) {
  const namespace = input.namespace?.trim() || "strategy";
  const memoryKey = input.memoryKey.trim();
  if (!memoryKey) throw new Error("memoryKey is required.");

  const sql = db();
  const rows = await sql<{ id: string }[]>`
    INSERT INTO business_memory_records (
      business_id,
      campaign_id,
      namespace,
      memory_key,
      title,
      content,
      evidence,
      confidence,
      created_by_profile_id,
      metadata
    )
    VALUES (
      ${input.businessId},
      ${input.campaignId ?? null},
      ${namespace},
      ${memoryKey},
      ${input.title ?? memoryKey},
      ${input.content},
      ${sql.json(toJson(input.evidence ?? []))}::jsonb,
      ${input.confidence ?? null},
      ${input.profileId ?? null},
      ${sql.json(toJson(input.metadata ?? {}))}::jsonb
    )
    ON CONFLICT (business_id, namespace, memory_key) DO UPDATE SET
      campaign_id = COALESCE(EXCLUDED.campaign_id, business_memory_records.campaign_id),
      title = EXCLUDED.title,
      content = EXCLUDED.content,
      evidence = EXCLUDED.evidence,
      confidence = EXCLUDED.confidence,
      metadata = business_memory_records.metadata || EXCLUDED.metadata,
      status = 'active',
      updated_at = now()
    RETURNING id
  `;

  await createEvent({
    businessId: input.businessId,
    actorProfileId: input.profileId ?? null,
    kind: "business_memory.upserted",
    subjectType: "business_memory",
    subjectId: rows[0].id,
    payload: { namespace, memory_key: memoryKey, campaign_id: input.campaignId ?? null }
  });
  return rows[0];
}

export async function listBusinessMemory(input: { businessId: string; namespace?: string; limit?: number }) {
  const sql = db();
  const limit = Math.max(1, Math.min(input.limit ?? 50, 200));
  return sql<{
    id: string;
    namespace: string;
    memory_key: string;
    title: string;
    content: string;
    evidence: unknown;
    confidence: string | null;
    campaign_id: string | null;
    updated_at: string;
  }[]>`
    SELECT id, namespace, memory_key, title, content, evidence, confidence::text, campaign_id, updated_at
    FROM business_memory_records
    WHERE business_id = ${input.businessId}
      AND (${input.namespace ?? null}::text IS NULL OR namespace = ${input.namespace ?? null})
      AND status = 'active'
    ORDER BY updated_at DESC
    LIMIT ${limit}
  `;
}
