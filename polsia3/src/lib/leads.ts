import { db } from "./db";
import { NotFoundError } from "./errors";
import { toJson } from "./json";

export type LeadRow = {
  id: string;
  business_id: string;
  email: string | null;
  name: string | null;
  url: string | null;
  source: string;
  status: string;
  outbound_vendor: string | null;
  campaign_url_or_vendor_id: string | null;
  last_event: string | null;
  last_contacted_at: string | null;
  created_at: string;
};

export async function createLead(input: {
  businessId: string;
  campaignId?: string | null;
  email: string;
  name?: string | null;
  source?: string;
  metadata?: Record<string, unknown>;
}) {
  const sql = db();
  const rows = await sql<LeadRow[]>`
    INSERT INTO leads (business_id, campaign_id, email, name, source, metadata)
    VALUES (
      ${input.businessId},
      ${input.campaignId ?? null},
      ${input.email.toLowerCase()},
      ${input.name ?? null},
      ${input.source ?? "website"},
      ${sql.json(toJson(input.metadata ?? {}))}
    )
    ON CONFLICT (business_id, email)
    DO UPDATE SET
      name = COALESCE(EXCLUDED.name, leads.name),
      campaign_id = COALESCE(EXCLUDED.campaign_id, leads.campaign_id),
      source = EXCLUDED.source,
      metadata = leads.metadata || EXCLUDED.metadata
    RETURNING id, business_id, email, name, url, source, status, outbound_vendor, campaign_url_or_vendor_id, last_event, last_contacted_at, created_at
  `;

  if (rows.length === 0) throw new NotFoundError("Lead not found.");

  await sql`
    INSERT INTO events (business_id, kind, subject_type, subject_id, payload)
    VALUES (${input.businessId}, 'lead.captured', 'lead', ${rows[0].id}, ${sql.json(toJson({ source: input.source ?? "website" }))})
  `;

  return rows[0];
}

export async function createLeadCandidate(input: {
  businessId: string;
  campaignId?: string | null;
  name: string;
  url: string;
  source?: string;
  metadata?: Record<string, unknown>;
}) {
  const sql = db();
  const rows = await sql<LeadRow[]>`
    INSERT INTO leads (business_id, campaign_id, email, name, url, source, status, metadata)
    VALUES (
      ${input.businessId},
      ${input.campaignId ?? null},
      NULL,
      ${input.name},
      ${input.url},
      ${input.source ?? "community"},
      'candidate',
      ${sql.json(toJson(input.metadata ?? {}))}
    )
    ON CONFLICT (business_id, url) WHERE url IS NOT NULL
    DO UPDATE SET
      name = EXCLUDED.name,
      campaign_id = COALESCE(EXCLUDED.campaign_id, leads.campaign_id),
      source = EXCLUDED.source,
      status = CASE WHEN leads.status = 'new' THEN leads.status ELSE EXCLUDED.status END,
      metadata = leads.metadata || EXCLUDED.metadata
    RETURNING id, business_id, email, name, url, source, status, outbound_vendor, campaign_url_or_vendor_id, last_event, last_contacted_at, created_at
  `;

  if (rows.length === 0) throw new NotFoundError("Lead candidate not found.");

  await sql`
    INSERT INTO events (business_id, kind, subject_type, subject_id, payload)
    VALUES (${input.businessId}, 'lead.candidate_found', 'lead', ${rows[0].id}, ${sql.json(toJson({ source: input.source ?? "community", url: input.url }))})
  `;

  return rows[0];
}

export async function listLeads(businessId: string) {
  const sql = db();
  return sql<LeadRow[]>`
    SELECT
      id,
      business_id,
      email,
      name,
      url,
      source,
      status,
      outbound_vendor,
      campaign_url_or_vendor_id,
      last_event,
      last_contacted_at,
      created_at
    FROM leads
    WHERE business_id = ${businessId}
    ORDER BY created_at DESC
    LIMIT 100
  `;
}
