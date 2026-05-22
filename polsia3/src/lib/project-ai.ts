import { db } from "./db";
import { ForbiddenError } from "./errors";
import { toJson } from "./json";

export type AiMessage = {
  role: "system" | "user" | "assistant";
  content: string;
};

export function estimateProviderCostMicrousd(provider: string, inputTokens = 0, outputTokens = 0) {
  const normalized = provider.toLowerCase();
  const inputUsdPerMillion = normalized === "openai" ? 0.4 : 3;
  const outputUsdPerMillion = normalized === "openai" ? 1.6 : 15;
  const usd = (inputTokens / 1_000_000) * inputUsdPerMillion + (outputTokens / 1_000_000) * outputUsdPerMillion;
  return Math.max(0, Math.ceil(usd * 1_000_000));
}

export async function reserveProjectAiUsage(input: {
  businessId: string;
  proxyKeyId: string;
  purpose: string;
  route: string;
  appUserKey: string;
  appUserTier: string;
  metadata?: Record<string, unknown>;
}) {
  const sql = db();
  const policies = await sql<{
    provider: string;
    model: string;
    max_output_tokens: number;
    max_estimated_cost_microusd: number;
    allowed: boolean;
  }[]>`
    SELECT provider, model, max_output_tokens, max_estimated_cost_microusd, allowed
    FROM project_ai_model_policies
    WHERE business_id = ${input.businessId}
      AND purpose IN (${input.purpose}, 'default')
    ORDER BY CASE
      WHEN purpose = ${input.purpose} THEN 0
      ELSE 1
    END
    LIMIT 1
  `;
  const policy = policies[0];
  if (!policy || !policy.allowed) throw new ForbiddenError("This generated-app AI purpose is not allowed.");

  const walletRows = await sql<{ hard_limit_microusd: string; committed_microusd: string }[]>`
    SELECT
      hard_limit_microusd::text,
      COALESCE((
        SELECT SUM(COALESCE(actual_cost_microusd, estimated_cost_microusd))
        FROM project_ai_usage_events
        WHERE business_id = ${input.businessId}
          AND status IN ('reserved', 'completed')
      ), 0)::text AS committed_microusd
    FROM project_ai_wallets
    WHERE business_id = ${input.businessId}
    LIMIT 1
  `;
  const wallet = walletRows[0];
  const estimate = Number(policy.max_estimated_cost_microusd);
  if (!wallet || Number(wallet.committed_microusd) + estimate > Number(wallet.hard_limit_microusd)) {
    throw new ForbiddenError("Project AI wallet does not have enough budget for this request.");
  }

  const rows = await sql<{ id: string }[]>`
    INSERT INTO project_ai_usage_events (
      business_id,
      proxy_key_id,
      app_user_key,
      app_user_tier,
      purpose,
      route,
      status,
      estimated_cost_microusd,
      provider,
      model,
      metadata
    )
    VALUES (
      ${input.businessId},
      ${input.proxyKeyId},
      ${input.appUserKey},
      ${input.appUserTier},
      ${input.purpose},
      ${input.route},
      'reserved',
      ${estimate},
      ${policy.provider},
      ${policy.model},
      ${sql.json(toJson(input.metadata ?? {}))}
    )
    RETURNING id
  `;

  return {
    usageEventId: rows[0].id,
    provider: policy.provider,
    model: policy.model,
    maxOutputTokens: policy.max_output_tokens,
    estimatedCostMicrousd: estimate
  };
}

export async function completeProjectAiUsage(input: {
  usageEventId: string;
  provider: string;
  inputTokens?: number | null;
  outputTokens?: number | null;
  providerRequestId?: string | null;
}) {
  const actualCostMicrousd = estimateProviderCostMicrousd(input.provider, input.inputTokens ?? 0, input.outputTokens ?? 0);
  const sql = db();
  await sql`
    UPDATE project_ai_usage_events
    SET status = 'completed',
        actual_cost_microusd = ${actualCostMicrousd},
        input_tokens = ${input.inputTokens ?? null},
        output_tokens = ${input.outputTokens ?? null},
        provider_request_id = ${input.providerRequestId ?? null},
        completed_at = now()
    WHERE id = ${input.usageEventId}
  `;
  return { actualCostMicrousd };
}

export async function failProjectAiUsage(usageEventId: string, error: unknown) {
  const sql = db();
  const message = error instanceof Error ? error.message : String(error);
  await sql`
    UPDATE project_ai_usage_events
    SET status = 'failed',
        error = ${message},
        completed_at = now()
    WHERE id = ${usageEventId}
  `;
}
