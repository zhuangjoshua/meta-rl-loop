import { z } from "zod";
import { db } from "./db";
import { createEvent } from "./events";
import type { Profile } from "./auth";
import { getAppEnv } from "./env";
import { syncBusinessWorkspace } from "./business-workspace";
import { ensureBusinessCeoCronJob } from "./cron-jobs";

export type CompanyRow = {
  id: string;
  owner_profile_id: string;
  name: string;
  slug: string;
  status: "active" | "paused" | "archived";
  created_at: string;
  updated_at: string;
};

export type CompanySiteRow = {
  id: string;
  business_id: string;
  slug: string;
  status: "draft" | "published" | "offline";
  base_domain: string | null;
  public_title: string;
  public_pitch: string;
  config: unknown;
  created_at: string;
  updated_at: string;
};

export const createCompanySchema = z.object({
  name: z.string().trim().min(2).max(120),
  pitch: z.string().trim().min(8).max(1200),
  customer: z.string().trim().max(240).optional(),
  pain: z.string().trim().max(500).optional(),
  offer: z.string().trim().max(500).optional(),
  template: z.string().trim().max(80).optional()
});

export function slugify(input: string) {
  const slug = input
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/(^-|-$)+/g, "")
    .slice(0, 54);
  return slug || "company";
}

async function uniqueSlug(base: string) {
  const sql = db();
  for (let attempt = 0; attempt < 20; attempt += 1) {
    const slug = attempt === 0 ? base : `${base}-${attempt + 1}`;
    const rows = await sql<{ exists: boolean }[]>`
      SELECT EXISTS(SELECT 1 FROM businesses WHERE slug = ${slug}) AS exists
    `;
    if (!rows[0].exists) return slug;
  }
  return `${base}-${Date.now().toString(36)}`;
}

export async function createCompany(input: z.infer<typeof createCompanySchema>, profile: Profile) {
  const parsed = createCompanySchema.parse(input);
  const sql = db();
  const baseSlug = slugify(parsed.name);
  const slug = await uniqueSlug(baseSlug);
  const appEnv = getAppEnv();

  const rows = await sql.begin(async (tx) => {
    const companyRows = await tx<CompanyRow[]>`
      INSERT INTO businesses (owner_profile_id, name, slug, status)
      VALUES (${profile.id}, ${parsed.name}, ${slug}, 'active')
      RETURNING id, owner_profile_id, name, slug, status, created_at, updated_at
    `;
    const company = companyRows[0];

    await tx`
      INSERT INTO business_memberships (business_id, profile_id, role)
      VALUES (${company.id}, ${profile.id}, 'owner')
      ON CONFLICT (business_id, profile_id) DO NOTHING
    `;

    const siteRows = await tx<CompanySiteRow[]>`
      INSERT INTO company_sites (business_id, slug, status, base_domain, public_title, public_pitch, config)
      VALUES (
        ${company.id},
        ${slug},
        'draft',
        ${appEnv.PUBLIC_COMPANY_BASE_DOMAIN},
        ${parsed.name},
        ${parsed.pitch},
        ${tx.json({
          customer: parsed.customer ?? "",
          pain: parsed.pain ?? "",
          offer: parsed.offer ?? "",
          template: parsed.template ?? "",
          readiness: {
            website: "queued",
            product: "queued"
          }
        })}
      )
      RETURNING id, business_id, slug, status, base_domain, public_title, public_pitch, config, created_at, updated_at
    `;

    return [{ company, site: siteRows[0] }];
  });

  await createEvent({
    businessId: rows[0].company.id,
    actorProfileId: profile.id,
    kind: "company.created",
    subjectType: "business",
    subjectId: rows[0].company.id,
    payload: { slug, pitch: parsed.pitch, template: parsed.template ?? null }
  });
  await syncBusinessWorkspace({ businessId: rows[0].company.id, profileId: profile.id, reason: "company_created" });
  await ensureBusinessCeoCronJob({
    businessId: rows[0].company.id,
    ownerProfileId: rows[0].company.owner_profile_id,
    slug: rows[0].company.slug,
    name: rows[0].company.name
  });

  return rows[0];
}

export async function listCompaniesForProfile(profileId: string, limit = 12) {
  const sql = db();
  return sql<(CompanyRow & { public_pitch: string | null; site_status: string | null })[]>`
    SELECT b.id, b.owner_profile_id, b.name, b.slug, b.status, b.created_at, b.updated_at,
           cs.public_pitch, cs.status AS site_status
    FROM businesses b
    JOIN business_memberships bm ON bm.business_id = b.id
    LEFT JOIN company_sites cs ON cs.business_id = b.id
    WHERE bm.profile_id = ${profileId}
    ORDER BY b.created_at DESC
    LIMIT ${limit}
  `;
}

export async function getCompanyForProfile(companyId: string, profileId: string) {
  const sql = db();
  const rows = await sql<(CompanyRow & { public_pitch: string | null; site_slug: string | null; site_status: string | null })[]>`
    SELECT b.id, b.owner_profile_id, b.name, b.slug, b.status, b.created_at, b.updated_at,
           cs.public_pitch, cs.slug AS site_slug, cs.status AS site_status
    FROM businesses b
    JOIN business_memberships bm ON bm.business_id = b.id
    LEFT JOIN company_sites cs ON cs.business_id = b.id
    WHERE b.id = ${companyId}
      AND bm.profile_id = ${profileId}
    LIMIT 1
  `;
  return rows[0] ?? null;
}

export async function getPublicSite(slug: string) {
  const sql = db();
  const rows = await sql<{
    business_id: string;
    slug: string;
    status: string;
    public_title: string;
    public_pitch: string;
  }[]>`
    SELECT business_id, slug, status, public_title, public_pitch
    FROM company_sites
    WHERE slug = ${slug}
    LIMIT 1
  `;
  return rows[0] ?? null;
}
