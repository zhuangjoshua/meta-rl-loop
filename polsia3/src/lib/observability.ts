import { createHash } from "node:crypto";
import { db } from "./db";
import { publicErrorMessage, statusForError } from "./errors";
import { jsonError } from "./http";
import { toJson } from "./json";

type ObservationPatch = {
  businessId?: string | null;
  profileId?: string | null;
  appUserId?: string | null;
  metadata?: Record<string, unknown>;
};

type ObservationContext = {
  set: (patch: ObservationPatch) => void;
};

type RequestObservationInput = {
  request: Request;
  route: string;
  action: string;
  businessId?: string | null;
  profileId?: string | null;
  appUserId?: string | null;
  metadata?: Record<string, unknown>;
};

function truncate(value: string | null, max = 500) {
  if (!value) return null;
  return value.length > max ? value.slice(0, max) : value;
}

export function hashObservabilityValue(kind: string, value: string) {
  const secret = process.env.APP_ENCRYPTION_KEY || process.env.AUTH0_SECRET || "takyon-observability";
  return `${kind}:${createHash("sha256").update(`${secret}:${kind}:${value.trim().toLowerCase()}`).digest("hex")}`;
}

export function clientIpValue(request: Request) {
  const forwarded = request.headers.get("x-forwarded-for")?.split(",")[0]?.trim();
  return forwarded || request.headers.get("x-real-ip") || request.headers.get("cf-connecting-ip") || "";
}

export function clientIpHash(request: Request) {
  const value = clientIpValue(request);
  return value ? hashObservabilityValue("ip", value) : null;
}

export async function recordPlatformRequest(input: {
  request: Request;
  route: string;
  action: string;
  status: "completed" | "blocked" | "failed";
  statusCode: number;
  durationMs: number;
  businessId?: string | null;
  profileId?: string | null;
  appUserId?: string | null;
  error?: string | null;
  metadata?: Record<string, unknown>;
}) {
  const sql = db();
  await sql`
    INSERT INTO platform_request_logs (
      business_id,
      profile_id,
      app_user_id,
      route,
      method,
      action,
      status,
      status_code,
      duration_ms,
      ip_hash,
      user_agent,
      request_id,
      error,
      metadata
    )
    VALUES (
      ${input.businessId ?? null},
      ${input.profileId ?? null},
      ${input.appUserId ?? null},
      ${input.route},
      ${input.request.method},
      ${input.action},
      ${input.status},
      ${input.statusCode},
      ${Math.max(0, Math.round(input.durationMs))},
      ${clientIpHash(input.request)},
      ${truncate(input.request.headers.get("user-agent"))},
      ${truncate(input.request.headers.get("x-request-id") || input.request.headers.get("x-vercel-id"), 180)},
      ${truncate(input.error ?? null, 1200)},
      ${sql.json(toJson(input.metadata ?? {}))}
    )
  `;
}

export async function observedRequest(input: RequestObservationInput, handler: (context: ObservationContext) => Promise<Response>) {
  const startedAt = Date.now();
  const state: Required<Pick<ObservationPatch, "metadata">> & Omit<ObservationPatch, "metadata"> = {
    businessId: input.businessId ?? null,
    profileId: input.profileId ?? null,
    appUserId: input.appUserId ?? null,
    metadata: { ...(input.metadata ?? {}) }
  };
  const context: ObservationContext = {
    set: (patch) => {
      if ("businessId" in patch) state.businessId = patch.businessId ?? null;
      if ("profileId" in patch) state.profileId = patch.profileId ?? null;
      if ("appUserId" in patch) state.appUserId = patch.appUserId ?? null;
      if (patch.metadata) state.metadata = { ...state.metadata, ...patch.metadata };
    }
  };

  try {
    const response = await handler(context);
    const status = response.status >= 500 ? "failed" : response.status >= 400 ? "blocked" : "completed";
    await recordPlatformRequest({
      request: input.request,
      route: input.route,
      action: input.action,
      status,
      statusCode: response.status,
      durationMs: Date.now() - startedAt,
      businessId: state.businessId,
      profileId: state.profileId,
      appUserId: state.appUserId,
      metadata: state.metadata
    }).catch(() => null);
    return response;
  } catch (error) {
    const statusCode = statusForError(error);
    await recordPlatformRequest({
      request: input.request,
      route: input.route,
      action: input.action,
      status: statusCode >= 500 ? "failed" : "blocked",
      statusCode,
      durationMs: Date.now() - startedAt,
      businessId: state.businessId,
      profileId: state.profileId,
      appUserId: state.appUserId,
      error: publicErrorMessage(error),
      metadata: state.metadata
    }).catch(() => null);
    return jsonError(error);
  }
}

export type BusinessObservabilitySummary = {
  requests24h: number;
  blocked24h: number;
  rateLimited24h: number;
  productRuns24h: number;
  aiRequests24h: number;
  aiCostMicrousd24h: number;
  lastError: string | null;
};

export async function getBusinessObservabilitySummary(businessId: string): Promise<BusinessObservabilitySummary> {
  const sql = db();
  const [requests, productRuns, aiUsage] = await Promise.all([
    sql<{ requests: string; blocked: string; rate_limited: string; last_error: string | null }[]>`
      SELECT
        COUNT(*)::text AS requests,
        COUNT(*) FILTER (WHERE status <> 'completed')::text AS blocked,
        COUNT(*) FILTER (WHERE status_code = 429)::text AS rate_limited,
        (
          SELECT error
          FROM platform_request_logs
          WHERE business_id = ${businessId}
            AND error IS NOT NULL
          ORDER BY created_at DESC
          LIMIT 1
        ) AS last_error
      FROM platform_request_logs
      WHERE business_id = ${businessId}
        AND created_at >= now() - interval '24 hours'
    `,
    sql<{ count: string }[]>`
      SELECT COUNT(*)::text AS count
      FROM generated_app_product_runs
      WHERE business_id = ${businessId}
        AND created_at >= now() - interval '24 hours'
    `,
    sql<{ requests: string; cost: string }[]>`
      SELECT
        COUNT(*)::text AS requests,
        COALESCE(SUM(actual_cost_microusd), 0)::text AS cost
      FROM project_ai_usage_events
      WHERE business_id = ${businessId}
        AND created_at >= now() - interval '24 hours'
    `
  ]);

  return {
    requests24h: Number(requests[0]?.requests) || 0,
    blocked24h: Number(requests[0]?.blocked) || 0,
    rateLimited24h: Number(requests[0]?.rate_limited) || 0,
    productRuns24h: Number(productRuns[0]?.count) || 0,
    aiRequests24h: Number(aiUsage[0]?.requests) || 0,
    aiCostMicrousd24h: Number(aiUsage[0]?.cost) || 0,
    lastError: requests[0]?.last_error ?? null
  };
}
