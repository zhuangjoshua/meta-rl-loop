import { db } from "./db";
import { ForbiddenError } from "./errors";
import { createEvent } from "./events";
import { toJson } from "./json";

export const takyonControlStates = ["active", "paused", "killed"] as const;
export const takyonControlScopeTypes = ["global", "business", "campaign", "workflow_job", "agent_run", "provider"] as const;

export type TakyonControlState = (typeof takyonControlStates)[number];
export type TakyonControlScopeType = (typeof takyonControlScopeTypes)[number];

export type TakyonControlRow = {
  id: string;
  scope_type: TakyonControlScopeType;
  business_id: string | null;
  campaign_id: string | null;
  workflow_job_id: string | null;
  agent_run_id: string | null;
  provider: string | null;
  scope_key: string;
  state: TakyonControlState;
  reason: string;
  actor_profile_id: string | null;
  metadata: unknown;
  updated_at: string;
};

export function takyonScopeKey(input: {
  scopeType: TakyonControlScopeType;
  businessId?: string | null;
  campaignId?: string | null;
  workflowJobId?: string | null;
  agentRunId?: string | null;
  provider?: string | null;
}) {
  if (input.scopeType === "global") return "global";
  if (input.scopeType === "business") return `business:${input.businessId}`;
  if (input.scopeType === "campaign") return `business:${input.businessId}/campaign:${input.campaignId}`;
  if (input.scopeType === "workflow_job") return `workflow_job:${input.workflowJobId}`;
  if (input.scopeType === "agent_run") return `agent_run:${input.agentRunId}`;
  return `provider:${input.provider?.trim().toLowerCase() || "unknown"}${input.businessId ? `/business:${input.businessId}` : ""}`;
}

export async function setTakyonControl(input: {
  scopeType: TakyonControlScopeType;
  state: TakyonControlState;
  businessId?: string | null;
  campaignId?: string | null;
  workflowJobId?: string | null;
  agentRunId?: string | null;
  provider?: string | null;
  reason?: string;
  actorProfileId?: string | null;
  metadata?: Record<string, unknown>;
}) {
  const scopeKey = takyonScopeKey(input);
  const sql = db();
  const rows = await sql<TakyonControlRow[]>`
    INSERT INTO takyon_control_states (
      scope_type,
      business_id,
      campaign_id,
      workflow_job_id,
      agent_run_id,
      provider,
      scope_key,
      state,
      reason,
      actor_profile_id,
      metadata
    )
    VALUES (
      ${input.scopeType},
      ${input.businessId ?? null},
      ${input.campaignId ?? null},
      ${input.workflowJobId ?? null},
      ${input.agentRunId ?? null},
      ${input.provider ?? null},
      ${scopeKey},
      ${input.state},
      ${input.reason ?? ""},
      ${input.actorProfileId ?? null},
      ${sql.json(toJson(input.metadata ?? {}))}
    )
    ON CONFLICT (scope_type, scope_key) DO UPDATE SET
      state = EXCLUDED.state,
      reason = EXCLUDED.reason,
      actor_profile_id = EXCLUDED.actor_profile_id,
      metadata = EXCLUDED.metadata,
      updated_at = now()
    RETURNING id, scope_type, business_id, campaign_id, workflow_job_id, agent_run_id,
              provider, scope_key, state, reason, actor_profile_id, metadata, updated_at
  `;

  await createEvent({
    businessId: input.businessId ?? null,
    actorProfileId: input.actorProfileId ?? null,
    kind: "takyon.control_set",
    subjectType: input.scopeType,
    subjectId: input.campaignId ?? input.workflowJobId ?? input.agentRunId ?? input.businessId ?? null,
    payload: { scope_key: scopeKey, state: input.state, provider: input.provider ?? null, reason: input.reason ?? "" }
  });

  return rows[0];
}

export async function listBlockingTakyonControls(input: {
  businessId?: string | null;
  campaignId?: string | null;
  workflowJobId?: string | null;
  agentRunId?: string | null;
  provider?: string | null;
}) {
  const provider = input.provider?.trim().toLowerCase() || null;
  const sql = db();
  return sql<TakyonControlRow[]>`
    SELECT id, scope_type, business_id, campaign_id, workflow_job_id, agent_run_id,
           provider, scope_key, state, reason, actor_profile_id, metadata, updated_at
    FROM takyon_control_states
    WHERE state IN ('paused', 'killed')
      AND (
        scope_type = 'global'
        OR (scope_type = 'business' AND business_id = ${input.businessId ?? null})
        OR (scope_type = 'campaign' AND campaign_id = ${input.campaignId ?? null})
        OR (scope_type = 'workflow_job' AND workflow_job_id = ${input.workflowJobId ?? null})
        OR (scope_type = 'agent_run' AND agent_run_id = ${input.agentRunId ?? null})
        OR (
          scope_type = 'provider'
          AND provider = ${provider}
          AND (business_id IS NULL OR business_id = ${input.businessId ?? null})
        )
      )
    ORDER BY CASE state WHEN 'killed' THEN 0 ELSE 1 END, updated_at DESC
  `;
}

export async function assertTakyonRunnable(input: {
  businessId?: string | null;
  campaignId?: string | null;
  workflowJobId?: string | null;
  agentRunId?: string | null;
  provider?: string | null;
}) {
  const blockers = await listBlockingTakyonControls(input);
  const blocker = blockers[0];
  if (!blocker) return;
  throw new ForbiddenError(
    `Takyon ${blocker.scope_key} is ${blocker.state}${blocker.reason ? `: ${blocker.reason}` : "."}`
  );
}
