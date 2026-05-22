import { db } from "./db";
import { toJson } from "./json";

function envInt(name: string, defaultValue: number) {
  const value = Number.parseInt(process.env[name] || "", 10);
  return Number.isFinite(value) && value >= 0 ? value : defaultValue;
}

export async function xPostAllowance(businessId: string) {
  const sql = db();
  const publishEnabled = !["0", "false", "no", "off"].includes((process.env.X_PUBLISH_ENABLED || "true").toLowerCase());
  if (!publishEnabled) {
    return {
      allowed: false,
      used: 0,
      limit: envInt("X_COMPANY_DAILY_POST_LIMIT", 1),
      globalUsed: 0,
      globalLimit: envInt("X_PLATFORM_DAILY_POST_LIMIT", 50),
      reason: "X publishing is disabled by X_PUBLISH_ENABLED."
    };
  }

  const [companyRows, globalRows] = await Promise.all([
    sql<{ count: number }[]>`
      SELECT COUNT(*)::int AS count
      FROM business_social_posts
      WHERE business_id = ${businessId}
        AND provider = 'x'
        AND status = 'published'
        AND COALESCE(published_at, created_at) >= now() - interval '1 day'
    `,
    sql<{ count: number }[]>`
      SELECT COUNT(*)::int AS count
      FROM business_social_posts
      WHERE provider = 'x'
        AND status = 'published'
        AND COALESCE(published_at, created_at) >= now() - interval '1 day'
    `
  ]);
  const used = companyRows[0]?.count ?? 0;
  const globalUsed = globalRows[0]?.count ?? 0;
  const limit = envInt("X_COMPANY_DAILY_POST_LIMIT", 1);
  const globalLimit = envInt("X_PLATFORM_DAILY_POST_LIMIT", 50);
  return {
    allowed: used < limit && globalUsed < globalLimit,
    used,
    limit,
    globalUsed,
    globalLimit,
    reason:
      used >= limit
        ? `X daily company limit reached: ${used}/${limit}.`
        : globalUsed >= globalLimit
          ? `X daily platform limit reached: ${globalUsed}/${globalLimit}.`
          : null
  };
}

export async function recordReadySocialPost(input: {
  businessId: string;
  profileId?: string | null;
  campaignId?: string | null;
  provider: "x" | "meta_page";
  text: string;
  result?: unknown;
}) {
  const sql = db();
  const rows = await sql<{ id: string }[]>`
    INSERT INTO business_social_posts (
      business_id,
      provider,
      text,
      status,
      result,
      campaign_id,
      created_by_profile_id
    )
    VALUES (
      ${input.businessId},
      ${input.provider},
      ${input.text},
      'ready',
      ${sql.json(toJson(input.result ?? {}))},
      ${input.campaignId ?? null},
      ${input.profileId ?? null}
    )
    RETURNING id
  `;
  return rows[0];
}

export async function recordPublishedSocialPost(input: {
  businessId: string;
  profileId?: string | null;
  campaignId?: string | null;
  provider: "x" | "meta_page";
  text: string;
  providerPostId?: string | null;
  providerUrl?: string | null;
  result: unknown;
}) {
  const sql = db();
  const rows = await sql<{ id: string }[]>`
    INSERT INTO business_social_posts (
      business_id,
      provider,
      text,
      provider_post_id,
      provider_url,
      status,
      result,
      campaign_id,
      created_by_profile_id,
      published_at
    )
    VALUES (
      ${input.businessId},
      ${input.provider},
      ${input.text},
      ${input.providerPostId ?? null},
      ${input.providerUrl ?? null},
      'published',
      ${sql.json(toJson(input.result))},
      ${input.campaignId ?? null},
      ${input.profileId ?? null},
      now()
    )
    RETURNING id
  `;
  return rows[0];
}

export async function markSocialPostPublished(input: {
  socialPostId: string;
  providerPostId?: string | null;
  providerUrl?: string | null;
  result: unknown;
}) {
  const sql = db();
  const rows = await sql<{ id: string }[]>`
    UPDATE business_social_posts
    SET provider_post_id = ${input.providerPostId ?? null},
        provider_url = ${input.providerUrl ?? null},
        status = 'published',
        result = ${sql.json(toJson(input.result))},
        published_at = now()
    WHERE id = ${input.socialPostId}
    RETURNING id
  `;
  return rows[0] ?? null;
}

export async function markSocialPostFailed(input: { socialPostId: string; error: string }) {
  const sql = db();
  const rows = await sql<{ id: string }[]>`
    UPDATE business_social_posts
    SET status = 'failed',
        error = ${input.error}
    WHERE id = ${input.socialPostId}
    RETURNING id
  `;
  return rows[0] ?? null;
}

export async function recordFailedSocialPost(input: { businessId: string; campaignId?: string | null; provider: "x" | "meta_page"; text: string; error: string }) {
  const sql = db();
  const rows = await sql<{ id: string }[]>`
    INSERT INTO business_social_posts (business_id, campaign_id, provider, text, status, error, result)
    VALUES (${input.businessId}, ${input.campaignId ?? null}, ${input.provider}, ${input.text}, 'failed', ${input.error}, '{}'::jsonb)
    RETURNING id
  `;
  return rows[0];
}
