import { db } from "./db";
import { RateLimitError } from "./errors";
import { clientIpHash, hashObservabilityValue } from "./observability";
import { toJson } from "./json";

type RateLimitInput = {
  action: string;
  bucketKey: string;
  limit: number;
  windowSeconds: number;
  businessId?: string | null;
  profileId?: string | null;
  appUserId?: string | null;
  metadata?: Record<string, unknown>;
  message?: string;
};

export function envInt(name: string, defaultValue: number) {
  const value = Number.parseInt(process.env[name] || "", 10);
  return Number.isFinite(value) && value >= 0 ? value : defaultValue;
}

export function emailBucket(email: string) {
  return hashObservabilityValue("email", email);
}

export function profileBucket(profileId: string) {
  return `profile:${profileId}`;
}

export function businessBucket(businessId: string) {
  return `business:${businessId}`;
}

export function projectKeyBucket(keyId: string) {
  return `project_key:${keyId}`;
}

export function requestIpBucket(request: Request) {
  return clientIpHash(request) || "ip:unknown";
}

function windowStart(windowSeconds: number) {
  const windowMs = windowSeconds * 1000;
  return new Date(Math.floor(Date.now() / windowMs) * windowMs);
}

export async function consumeRateLimit(input: RateLimitInput) {
  if (input.limit <= 0) return { allowed: true, used: 0, limit: input.limit };
  const sql = db();
  const windowSeconds = Math.max(1, Math.floor(input.windowSeconds));
  const rows = await sql<{ id: string; used_count: number; limit_count: number }[]>`
    INSERT INTO platform_rate_limit_buckets (
      action,
      bucket_key,
      business_id,
      profile_id,
      app_user_id,
      window_start,
      window_seconds,
      limit_count,
      used_count,
      metadata
    )
    VALUES (
      ${input.action},
      ${input.bucketKey},
      ${input.businessId ?? null},
      ${input.profileId ?? null},
      ${input.appUserId ?? null},
      ${windowStart(windowSeconds)},
      ${windowSeconds},
      ${input.limit},
      1,
      ${sql.json(toJson(input.metadata ?? {}))}
    )
    ON CONFLICT (action, bucket_key, window_start)
    DO UPDATE SET
      used_count = platform_rate_limit_buckets.used_count + 1,
      limit_count = EXCLUDED.limit_count,
      business_id = COALESCE(platform_rate_limit_buckets.business_id, EXCLUDED.business_id),
      profile_id = COALESCE(platform_rate_limit_buckets.profile_id, EXCLUDED.profile_id),
      app_user_id = COALESCE(platform_rate_limit_buckets.app_user_id, EXCLUDED.app_user_id),
      metadata = platform_rate_limit_buckets.metadata || EXCLUDED.metadata
    RETURNING id, used_count, limit_count
  `;
  const row = rows[0];
  if (row.used_count > row.limit_count) {
    await sql`
      UPDATE platform_rate_limit_buckets
      SET blocked_count = blocked_count + 1
      WHERE id = ${row.id}
    `;
    throw new RateLimitError(input.message || "Too many requests for this action. Try again later.");
  }
  return { allowed: true, used: row.used_count, limit: row.limit_count };
}

export async function consumeRateLimits(inputs: RateLimitInput[]) {
  const results = [];
  for (const input of inputs) {
    results.push(await consumeRateLimit(input));
  }
  return results;
}
