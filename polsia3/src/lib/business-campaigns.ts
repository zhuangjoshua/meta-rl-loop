import { db } from "./db";
import { NotFoundError } from "./errors";
import { createEvent } from "./events";
import { setTakyonControl } from "./takyon-control";
import { toJson } from "./json";

export type BusinessCampaignStatus = "draft" | "active" | "paused" | "completed" | "failed" | "killed" | "archived";

export type BusinessCampaignRow = {
  id: string;
  business_id: string;
  slug: string;
  name: string;
  kind: string;
  status: BusinessCampaignStatus;
  workspace_path: string;
  budget_cap_microusd: string | null;
  metadata: unknown;
  created_by_profile_id: string | null;
  created_at: string;
  updated_at: string;
};

function slugify(input: string) {
  const slug = input
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/(^-|-$)+/g, "")
    .slice(0, 80);
  if (!slug) throw new Error("Campaign slug is required.");
  return slug;
}

export async function upsertBusinessCampaign(input: {
  businessId: string;
  slug?: string;
  name: string;
  kind?: string;
  status?: BusinessCampaignStatus;
  workspacePath?: string;
  budgetCapMicrousd?: number | bigint | null;
  profileId?: string | null;
  metadata?: Record<string, unknown>;
}) {
  const slug = slugify(input.slug || input.name);
  const workspacePath = input.workspacePath || `campaigns/${slug}`;
  const sql = db();
  const rows = await sql<BusinessCampaignRow[]>`
    INSERT INTO business_campaigns (
      business_id,
      slug,
      name,
      kind,
      status,
      workspace_path,
      budget_cap_microusd,
      metadata,
      created_by_profile_id
    )
    VALUES (
      ${input.businessId},
      ${slug},
      ${input.name},
      ${input.kind ?? "campaign"},
      ${input.status ?? "draft"},
      ${workspacePath},
      ${input.budgetCapMicrousd == null ? null : String(input.budgetCapMicrousd)},
      ${sql.json(toJson(input.metadata ?? {}))},
      ${input.profileId ?? null}
    )
    ON CONFLICT (business_id, slug) DO UPDATE SET
      name = EXCLUDED.name,
      kind = EXCLUDED.kind,
      status = EXCLUDED.status,
      workspace_path = EXCLUDED.workspace_path,
      budget_cap_microusd = COALESCE(EXCLUDED.budget_cap_microusd, business_campaigns.budget_cap_microusd),
      metadata = business_campaigns.metadata || EXCLUDED.metadata,
      updated_at = now()
    RETURNING id, business_id, slug, name, kind, status, workspace_path, budget_cap_microusd,
              metadata, created_by_profile_id, created_at, updated_at
  `;

  await createEvent({
    businessId: input.businessId,
    actorProfileId: input.profileId ?? null,
    kind: "campaign.upserted",
    subjectType: "campaign",
    subjectId: rows[0].id,
    payload: { slug, status: rows[0].status, workspace_path: rows[0].workspace_path }
  });
  return rows[0];
}

export async function listBusinessCampaigns(businessId: string, limit = 50) {
  const sql = db();
  return sql<BusinessCampaignRow[]>`
    SELECT id, business_id, slug, name, kind, status, workspace_path, budget_cap_microusd,
           metadata, created_by_profile_id, created_at, updated_at
    FROM business_campaigns
    WHERE business_id = ${businessId}
    ORDER BY updated_at DESC
    LIMIT ${Math.max(1, Math.min(limit, 200))}
  `;
}

export async function getBusinessCampaign(input: { businessId: string; campaignIdOrSlug: string }) {
  const sql = db();
  const rows = await sql<BusinessCampaignRow[]>`
    SELECT id, business_id, slug, name, kind, status, workspace_path, budget_cap_microusd,
           metadata, created_by_profile_id, created_at, updated_at
    FROM business_campaigns
    WHERE business_id = ${input.businessId}
      AND (id::text = ${input.campaignIdOrSlug} OR slug = ${input.campaignIdOrSlug})
    LIMIT 1
  `;
  return rows[0] ?? null;
}

export async function requireBusinessCampaign(input: { businessId: string; campaignIdOrSlug: string }) {
  const row = await getBusinessCampaign(input);
  if (!row) throw new NotFoundError("Campaign not found.");
  return row;
}

export async function setBusinessCampaignStatus(input: {
  businessId: string;
  campaignIdOrSlug: string;
  status: BusinessCampaignStatus;
  profileId?: string | null;
  reason?: string;
}) {
  const campaign = await requireBusinessCampaign(input);
  const sql = db();
  const rows = await sql<BusinessCampaignRow[]>`
    UPDATE business_campaigns
    SET status = ${input.status},
        ended_at = CASE WHEN ${input.status} IN ('completed', 'failed', 'killed', 'archived') THEN COALESCE(ended_at, now()) ELSE ended_at END,
        updated_at = now()
    WHERE id = ${campaign.id}
    RETURNING id, business_id, slug, name, kind, status, workspace_path, budget_cap_microusd,
              metadata, created_by_profile_id, created_at, updated_at
  `;

  if (input.status === "paused" || input.status === "killed") {
    await setTakyonControl({
      scopeType: "campaign",
      businessId: input.businessId,
      campaignId: campaign.id,
      state: input.status === "paused" ? "paused" : "killed",
      reason: input.reason ?? "",
      actorProfileId: input.profileId ?? null
    });
  }

  await createEvent({
    businessId: input.businessId,
    actorProfileId: input.profileId ?? null,
    kind: "campaign.status_changed",
    subjectType: "campaign",
    subjectId: campaign.id,
    payload: { status: input.status, reason: input.reason ?? "" }
  });
  return rows[0];
}
