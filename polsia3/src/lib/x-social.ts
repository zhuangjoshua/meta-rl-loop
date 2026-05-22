import { getAppEnv } from "./env";
import { preflightResponseAwareDistribution } from "./business-conversations";
import { isBusinessTestMode } from "./companies";
import { markSocialPostFailed, markSocialPostPublished, recordReadySocialPost, xPostAllowance } from "./social-posts";
import { assertTakyonRunnable } from "./takyon-control";
import { xCapability } from "./tool-availability";
import { publishXPost } from "./vendors/x";

function ensureUrl(text: string, url: string) {
  if (text.includes(url)) return text.slice(0, 280);
  const suffix = ` ${url}`;
  return `${text.slice(0, Math.max(0, 280 - suffix.length)).trim()}${suffix}`;
}

export async function runXSocialLane(input: { businessId: string; profileId?: string | null; campaignId?: string | null }) {
  await assertTakyonRunnable({ businessId: input.businessId, provider: "x" });
  const testMode = await isBusinessTestMode(input.businessId);
  let allowance: Awaited<ReturnType<typeof xPostAllowance>> | null = null;
  if (!testMode) {
    const capability = await xCapability({ businessId: input.businessId, profileId: input.profileId ?? null });
    if (!capability.canRun) {
      return {
        status: "blocked" as const,
        reason: capability.reason,
        capability,
        publishAttempted: false
      };
    }

    allowance = await xPostAllowance(input.businessId);
    if (!allowance.allowed) {
      return {
        status: "blocked" as const,
        reason: allowance.reason,
        allowance,
        publishAttempted: false
      };
    }
  }

  const responseBlock = await preflightResponseAwareDistribution({
    businessId: input.businessId,
    profileId: input.profileId ?? null,
    workflowId: "x_social"
  });
  if (responseBlock) {
    return {
      status: "blocked" as const,
      reason: responseBlock.reason,
      responseCheck: responseBlock,
      publishAttempted: false
    };
  }

  const { db } = await import("./db");
  const sql = db();
  const rows = await sql<{ name: string; slug: string; public_pitch: string; alias_url: string | null; deployment_url: string | null }[]>`
    SELECT b.name, cs.slug, cs.public_pitch, gd.alias_url, gd.deployment_url
    FROM businesses b
    LEFT JOIN company_sites cs ON cs.business_id = b.id
    LEFT JOIN LATERAL (
      SELECT alias_url, deployment_url
      FROM generated_app_deployments
      WHERE business_id = b.id
        AND status = 'completed'
      ORDER BY created_at DESC
      LIMIT 1
    ) gd ON true
    WHERE b.id = ${input.businessId}
    LIMIT 1
  `;
  const company = rows[0];
  if (!company) throw new Error("Company not found for X social lane.");
  const url = company.alias_url ?? company.deployment_url ?? `https://${company.slug}.${getAppEnv().PUBLIC_COMPANY_BASE_DOMAIN}`;
  const text = ensureUrl(`${company.name} is live: ${company.public_pitch}`.slice(0, 240), url);
  const socialPost = await recordReadySocialPost({
    businessId: input.businessId,
    profileId: input.profileId ?? null,
    campaignId: input.campaignId ?? null,
    provider: "x",
    text,
    result: {
      workflow: "x_social",
      publish_attempted: false,
      campaign_id: input.campaignId ?? null,
      business_mode: testMode ? "test" : "live",
      external_side_effects: testMode ? "suppressed" : "pending"
    }
  });

  if (testMode) {
    return {
      status: "completed" as const,
      socialPostId: socialPost.id,
      providerPostId: null,
      allowance: null,
      publishAttempted: false,
      testMode: true,
      reason: "Business is in test mode; X publish was suppressed and a ready social post receipt was recorded."
    };
  }

  try {
    const result = await publishXPost({ text, madeWithAi: true, businessId: input.businessId, profileId: input.profileId ?? null });
    const response = result.response as { data?: { id?: string } } | null;
    const postId = response?.data?.id ?? null;
    const published = await markSocialPostPublished({
      socialPostId: socialPost.id,
      providerPostId: postId,
      providerUrl: postId ? `https://x.com/i/web/status/${postId}` : null,
      result
    });
    return { status: "completed" as const, socialPostId: published?.id ?? socialPost.id, providerPostId: postId, allowance };
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    await markSocialPostFailed({ socialPostId: socialPost.id, error: message });
    throw error;
  }
}
