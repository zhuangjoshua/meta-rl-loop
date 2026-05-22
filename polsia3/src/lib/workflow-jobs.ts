import { db } from "./db";
import { createEvent } from "./events";
import { toJson } from "./json";
import { takyonBuildCompanyLanes, takyonDispatchableWorkflowIds } from "./takyon-registry";

export type WorkflowJobStatus = "queued" | "running" | "completed" | "blocked" | "failed" | "cancelled";

export type WorkflowLane =
  | "foundation"
  | "website"
  | "product_backend"
  | "product_ui"
  | "generated_app_auth"
  | "generated_app_users_entitlements"
  | "stripe"
  | "ai_gateway"
  | "x_social"
  | "meta_seedance"
  | "community"
  | "outreach"
  | "ceo"
  | "goal";

export type WorkflowJobRow = {
  id: string;
  business_id: string;
  profile_id: string | null;
  task_id: string | null;
  workflow_id: string;
  lane: WorkflowLane;
  status: WorkflowJobStatus;
  priority: number;
  payload: unknown;
  dependencies: string[];
  result: unknown;
  error: string | null;
  attempts: number;
  max_attempts: number;
  run_after: string;
  locked_by: string | null;
  locked_at: string | null;
  started_at: string | null;
  completed_at: string | null;
  created_at: string;
  updated_at: string;
};

function postgresTextArrayLiteral(values: string[]) {
  return `{${values.map((value) => `"${value.replace(/\\/g, "\\\\").replace(/"/g, '\\"')}"`).join(",")}}`;
}

export const buildCompanyLanes = takyonBuildCompanyLanes();

export const workerDispatchableWorkflowIds = takyonDispatchableWorkflowIds();

export async function enqueueCampaignObservationJob(input: {
  companyId: string;
  profileId?: string | null;
  campaignId?: string | null;
  sourceWorkflowId: string;
  reason?: string;
  runAfter?: Date;
}) {
  const sql = db();
  const existing = await sql<{ id: string }[]>`
    SELECT id
    FROM workflow_jobs
    WHERE business_id = ${input.companyId}
      AND workflow_id = 'observe_campaign_results'
      AND status IN ('queued', 'running')
    ORDER BY created_at DESC
    LIMIT 1
  `;
  if (existing[0]) return existing[0];

  return enqueueWorkflowJob({
    companyId: input.companyId,
    profileId: input.profileId ?? null,
    workflowId: "observe_campaign_results",
    lane: "ceo",
    dependencies: [],
    priority: 40,
    maxAttempts: 1,
    runAfter: input.runAfter ?? new Date(Date.now() + 6 * 60 * 60 * 1000),
    payload: {
      source: "post_launch_observation_stub",
      campaign_id: input.campaignId ?? null,
      source_workflow_id: input.sourceWorkflowId,
      reason: input.reason ?? "Observe launch/ad/social results after initial distribution."
    }
  });
}

export async function enqueueSoraSyncJob(input: {
  companyId: string;
  profileId?: string | null;
  pollCount?: number;
  runAfter?: Date;
}) {
  const pollCount = Math.max(0, Math.floor(input.pollCount ?? 0));
  if (pollCount >= 15) return null;
  const sql = db();
  const existing = await sql<{ id: string }[]>`
    SELECT id
    FROM workflow_jobs
    WHERE business_id = ${input.companyId}
      AND workflow_id = 'meta_seedance'
      AND status = 'queued'
      AND payload->>'source' = 'sora_sync'
    ORDER BY created_at DESC
    LIMIT 1
  `;
  if (existing[0]) return existing[0];

  return enqueueWorkflowJob({
    companyId: input.companyId,
    profileId: input.profileId ?? null,
    workflowId: "meta_seedance",
    lane: "meta_seedance",
    dependencies: ["foundation"],
    priority: 55,
    maxAttempts: 1,
    runAfter: input.runAfter ?? new Date(Date.now() + 2 * 60 * 1000),
    payload: {
      source: "sora_sync",
      poll_count: pollCount + 1
    }
  });
}

export async function enqueueWorkflowJob(input: {
  companyId: string;
  profileId?: string | null;
  taskId?: string | null;
  workflowId: string;
  lane: WorkflowLane;
  payload?: Record<string, unknown>;
  dependencies?: string[];
  priority?: number;
  maxAttempts?: number;
  runAfter?: Date;
}) {
  const sql = db();
  const dependencies = postgresTextArrayLiteral(input.dependencies ?? []);
  const payload = input.payload ?? {};
  const campaignId = typeof payload.campaign_id === "string" ? payload.campaign_id : "";
  const active = await sql<WorkflowJobRow[]>`
    SELECT id, business_id, profile_id, task_id, workflow_id, lane, status, priority, payload,
           dependencies, result, error, attempts, max_attempts, run_after, locked_by, locked_at,
           started_at, completed_at, created_at, updated_at
    FROM workflow_jobs
    WHERE business_id = ${input.companyId}
      AND workflow_id = ${input.workflowId}
      AND status IN ('queued', 'running')
      AND COALESCE(payload->>'campaign_id', '') = ${campaignId}
    ORDER BY created_at DESC
    LIMIT 1
  `;
  if (active[0]) return active[0];

  const rows = await sql<WorkflowJobRow[]>`
    INSERT INTO workflow_jobs (
      business_id,
      profile_id,
      task_id,
      workflow_id,
      lane,
      status,
      priority,
      payload,
      dependencies,
      max_attempts,
      run_after
    )
    VALUES (
      ${input.companyId},
      ${input.profileId ?? null},
      ${input.taskId ?? null},
      ${input.workflowId},
      ${input.lane},
      'queued',
      ${input.priority ?? 50},
      ${sql.json(toJson(payload))},
      ${dependencies}::text[],
      ${input.maxAttempts ?? 2},
      ${input.runAfter ?? new Date()}
    )
    RETURNING id, business_id, profile_id, task_id, workflow_id, lane, status, priority, payload,
              dependencies, result, error, attempts, max_attempts, run_after, locked_by, locked_at,
              started_at, completed_at, created_at, updated_at
  `;

  await createEvent({
    businessId: input.companyId,
    actorProfileId: input.profileId ?? null,
    kind: "workflow.job_queued",
    subjectType: "workflow_job",
    subjectId: rows[0].id,
    payload: { workflow_id: input.workflowId, lane: input.lane, dependencies: input.dependencies ?? [] }
  });

  return rows[0];
}

export async function enqueueBuildCompanyPlan(input: {
  companyId: string;
  profileId: string;
  taskId?: string | null;
  brief: Record<string, unknown>;
}) {
  const jobs: WorkflowJobRow[] = [];
  const template = typeof input.brief.template === "string" ? input.brief.template.trim() : "";
  for (const lane of buildCompanyLanes) {
    jobs.push(
      await enqueueWorkflowJob({
        companyId: input.companyId,
        profileId: input.profileId,
        taskId: input.taskId ?? null,
        workflowId: lane.workflowId,
        lane: lane.lane,
        priority: lane.priority,
        dependencies: lane.dependencies,
        payload: {
          brief: input.brief,
          template,
          independent_lane: lane.dependencies.length <= 1,
          product_failure_must_not_block: ["website", "x_social", "meta_seedance", "community", "outreach"].includes(lane.lane)
        }
      })
    );
  }
  return jobs;
}

export async function listWorkflowJobs(companyId: string, limit = 50) {
  const sql = db();
  return sql<WorkflowJobRow[]>`
    SELECT id, business_id, profile_id, task_id, workflow_id, lane, status, priority, payload,
           dependencies, result, error, attempts, max_attempts, run_after, locked_by, locked_at,
           started_at, completed_at, created_at, updated_at
    FROM workflow_jobs
    WHERE business_id = ${companyId}
    ORDER BY created_at DESC
    LIMIT ${limit}
  `;
}

export async function claimWorkflowJobs(input: { workerId: string; limit?: number; businessId?: string | null }) {
  const sql = db();
  const limit = input.limit ?? 1;
  const businessId = input.businessId ?? null;
  return sql.begin(async (tx) => {
    const rows = await tx<WorkflowJobRow[]>`
      WITH candidates AS (
        SELECT j.id
        FROM workflow_jobs j
        JOIN businesses b ON b.id = j.business_id
        WHERE j.status = 'queued'
          AND b.status = 'active'
          AND j.workflow_id IN ${tx(workerDispatchableWorkflowIds)}
          AND (${businessId}::uuid IS NULL OR j.business_id = ${businessId})
          AND j.run_after <= now()
          AND NOT EXISTS (
            SELECT 1
            FROM workflow_jobs running
            WHERE running.business_id = j.business_id
              AND running.status = 'running'
              AND running.id <> j.id
              AND (
                running.workflow_id = j.workflow_id
                OR running.lane = j.lane
                OR (
                  running.lane IN ('website', 'product_backend', 'product_ui')
                  AND j.lane IN ('website', 'product_backend', 'product_ui')
                )
              )
          )
          AND NOT EXISTS (
            SELECT 1
            FROM takyon_control_states c
            WHERE c.state IN ('paused', 'killed')
              AND (
                c.scope_type = 'global'
                OR (c.scope_type = 'business' AND c.business_id = j.business_id)
                OR (c.scope_type = 'workflow_job' AND c.workflow_job_id = j.id)
              )
          )
          AND NOT EXISTS (
            SELECT 1
            FROM unnest(j.dependencies) AS dep(workflow_id)
            WHERE NOT EXISTS (
              SELECT 1
              FROM workflow_jobs done
              WHERE done.business_id = j.business_id
                AND done.workflow_id = dep.workflow_id
                AND done.status = 'completed'
            )
          )
        ORDER BY j.priority DESC, j.created_at ASC
        FOR UPDATE SKIP LOCKED
        LIMIT ${limit}
      )
      UPDATE workflow_jobs j
      SET status = 'running',
          attempts = attempts + 1,
          locked_by = ${input.workerId},
          locked_at = now(),
          started_at = COALESCE(started_at, now()),
          updated_at = now()
      FROM candidates
      WHERE j.id = candidates.id
      RETURNING j.id, j.business_id, j.profile_id, j.task_id, j.workflow_id, j.lane, j.status, j.priority,
                j.payload, j.dependencies, j.result, j.error, j.attempts, j.max_attempts, j.run_after,
                j.locked_by, j.locked_at, j.started_at, j.completed_at, j.created_at, j.updated_at
    `;
    return rows;
  });
}

export async function recoverStaleWorkflowJobs(input: {
  workerId: string;
  businessId?: string | null;
  staleAfterMinutes?: number;
}) {
  const sql = db();
  const businessId = input.businessId ?? null;
  const staleAfterMinutes = Math.max(0, Math.floor(input.staleAfterMinutes ?? 30));
  const rows = await sql<WorkflowJobRow[]>`
    UPDATE workflow_jobs
    SET status = CASE
          WHEN attempts >= max_attempts THEN 'failed'
          ELSE 'queued'
        END,
        error = CASE
          WHEN attempts >= max_attempts THEN COALESCE(error, 'Worker lock expired and max attempts were reached.')
          ELSE 'Recovered stale worker lock; job returned to queued for retry.'
        END,
        locked_by = NULL,
        locked_at = NULL,
        updated_at = now()
    WHERE status = 'running'
      AND (${businessId}::uuid IS NULL OR business_id = ${businessId})
      AND COALESCE(locked_at, started_at, updated_at) < now() - (${staleAfterMinutes}::int * interval '1 minute')
    RETURNING id, business_id, profile_id, task_id, workflow_id, lane, status, priority, payload,
              dependencies, result, error, attempts, max_attempts, run_after, locked_by, locked_at,
              started_at, completed_at, created_at, updated_at
  `;

  for (const row of rows) {
    await createEvent({
      businessId: row.business_id,
      kind: "workflow.job_lock_recovered",
      subjectType: "workflow_job",
      subjectId: row.id,
      payload: {
        workflow_id: row.workflow_id,
        lane: row.lane,
        recovered_by: input.workerId,
        status: row.status,
        stale_after_minutes: staleAfterMinutes
      }
    });
  }

  return rows;
}

export async function completeWorkflowJob(input: {
  jobId: string;
  status: Extract<WorkflowJobStatus, "completed" | "blocked" | "failed" | "cancelled">;
  result?: Record<string, unknown>;
  error?: string | null;
}) {
  const sql = db();
  const rows = await sql<WorkflowJobRow[]>`
    UPDATE workflow_jobs
    SET status = ${input.status},
        result = ${sql.json(toJson(input.result ?? {}))},
        error = ${input.error ?? null},
        locked_by = NULL,
        locked_at = NULL,
        completed_at = now(),
        updated_at = now()
    WHERE id = ${input.jobId}
      AND status <> 'cancelled'
    RETURNING id, business_id, profile_id, task_id, workflow_id, lane, status, priority, payload,
              dependencies, result, error, attempts, max_attempts, run_after, locked_by, locked_at,
              started_at, completed_at, created_at, updated_at
  `;

  if (rows[0]) {
    await createEvent({
      businessId: rows[0].business_id,
      kind: `workflow.job_${input.status}`,
      subjectType: "workflow_job",
      subjectId: rows[0].id,
      payload: { workflow_id: rows[0].workflow_id, lane: rows[0].lane, error: input.error ?? null }
    });
  }

  return rows[0] ?? null;
}

export async function retryWorkflowJob(input: {
  jobId: string;
  error: string;
  runAfter?: Date;
  result?: Record<string, unknown>;
}) {
  const sql = db();
  const rows = await sql<WorkflowJobRow[]>`
    UPDATE workflow_jobs
    SET status = 'queued',
        result = ${sql.json(toJson(input.result ?? {}))},
        error = ${input.error},
        locked_by = NULL,
        locked_at = NULL,
        run_after = ${input.runAfter ?? new Date(Date.now() + 60_000)},
        updated_at = now()
    WHERE id = ${input.jobId}
      AND status <> 'cancelled'
      AND attempts < max_attempts
    RETURNING id, business_id, profile_id, task_id, workflow_id, lane, status, priority, payload,
              dependencies, result, error, attempts, max_attempts, run_after, locked_by, locked_at,
              started_at, completed_at, created_at, updated_at
  `;

  if (rows[0]) {
    await createEvent({
      businessId: rows[0].business_id,
      kind: "workflow.job_retry_queued",
      subjectType: "workflow_job",
      subjectId: rows[0].id,
      payload: {
        workflow_id: rows[0].workflow_id,
        lane: rows[0].lane,
        error: input.error,
        run_after: rows[0].run_after,
        attempts: rows[0].attempts,
        max_attempts: rows[0].max_attempts
      }
    });
  }

  return rows[0] ?? null;
}
