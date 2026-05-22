import { db } from "./db";
import { toJson } from "./json";
import { enqueueWorkflowJob } from "./workflow-jobs";
import { getDistributionResponseCheckPolicy } from "./business-conversations";

const businessCeoCronPrefix = "ceo_wakeup:";
const businessConversationCronPrefix = "conversation_watch:";
const businessCustomerOpsCronPrefix = "customer_ops_watch:";

export type CronJobRow = {
  job_key: string;
  status: "active" | "paused";
  schedule_type: "interval" | "daily";
  interval_seconds: number | null;
  daily_time_utc: string | null;
  default_limit: number;
  next_run_at: string;
  last_started_at: string | null;
  last_completed_at: string | null;
  last_result: unknown;
  last_error: string | null;
  locked_by: string | null;
  locked_at: string | null;
  metadata: unknown;
  created_at: string;
  updated_at: string;
};

function cronMetadata(value: unknown) {
  return value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : {};
}

function cronBusinessId(job: CronJobRow) {
  const metadata = cronMetadata(job.metadata);
  return typeof metadata.business_id === "string" ? metadata.business_id : null;
}

function cronBusinessProfileId(job: CronJobRow) {
  const metadata = cronMetadata(job.metadata);
  return typeof metadata.owner_profile_id === "string" ? metadata.owner_profile_id : null;
}

function isBusinessCeoCronJob(jobKey: string) {
  return jobKey.startsWith(businessCeoCronPrefix);
}

function isBusinessConversationCronJob(jobKey: string) {
  return jobKey.startsWith(businessConversationCronPrefix);
}

function isBusinessCustomerOpsCronJob(jobKey: string) {
  return jobKey.startsWith(businessCustomerOpsCronPrefix);
}

function nextDailyUtcDate(time: string) {
  const [hourRaw, minuteRaw, secondRaw] = time.split(":");
  const hour = Math.max(0, Math.min(23, Number(hourRaw) || 0));
  const minute = Math.max(0, Math.min(59, Number(minuteRaw) || 0));
  const second = Math.max(0, Math.min(59, Number(secondRaw) || 0));
  const now = new Date();
  const next = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate(), hour, minute, second));
  if (next.getTime() <= now.getTime()) next.setUTCDate(next.getUTCDate() + 1);
  return next;
}

function nextRunSqlExpression(jobAlias = "j") {
  return `
    CASE
      WHEN ${jobAlias}.schedule_type = 'interval'
        THEN now() + make_interval(secs => ${jobAlias}.interval_seconds)
      WHEN date_trunc('day', now()) + (${jobAlias}.daily_time_utc::text)::interval > now()
        THEN date_trunc('day', now()) + (${jobAlias}.daily_time_utc::text)::interval
      ELSE date_trunc('day', now()) + interval '1 day' + (${jobAlias}.daily_time_utc::text)::interval
    END
  `;
}

export async function ensureBusinessCeoCronJob(input: {
  businessId: string;
  ownerProfileId: string;
  slug: string;
  name: string;
}) {
  const sql = db();
  const jobKey = `${businessCeoCronPrefix}${input.businessId}`;
  const dailyTimeUtc = process.env.TAKYON_CEO_WAKEUP_UTC?.trim() || "09:00:00";
  const nextRunAt = nextDailyUtcDate(dailyTimeUtc);
  await sql`
    INSERT INTO cron_jobs (job_key, status, schedule_type, interval_seconds, daily_time_utc, default_limit, next_run_at, metadata)
    VALUES (
      ${jobKey},
      'active',
      'daily',
      NULL,
      ${dailyTimeUtc},
      1,
      ${nextRunAt},
      ${sql.json(toJson({
        scope: "business",
        kind: "ceo_wakeup",
        business_id: input.businessId,
        owner_profile_id: input.ownerProfileId,
        business_slug: input.slug,
        business_name: input.name,
        description: "Wake this business CEO only."
      }))}
    )
    ON CONFLICT (job_key) DO UPDATE
    SET schedule_type = 'daily',
        interval_seconds = NULL,
        daily_time_utc = EXCLUDED.daily_time_utc,
        default_limit = 1,
        metadata = EXCLUDED.metadata,
        updated_at = now()
  `;
}

export async function ensureBusinessConversationCronJob(input: {
  businessId: string;
  ownerProfileId: string;
  slug: string;
  name: string;
}) {
  const policy = await getDistributionResponseCheckPolicy(input.businessId);
  const sql = db();
  const jobKey = `${businessConversationCronPrefix}${input.businessId}`;
  if (!policy.enabled) {
    await sql`DELETE FROM cron_jobs WHERE job_key = ${jobKey}`;
    return;
  }
  const intervalSeconds = Math.max(900, Math.min(Number(process.env.TAKYON_CONVERSATION_WATCH_SECONDS || 3600), 24 * 60 * 60));
  await sql`
    INSERT INTO cron_jobs (job_key, status, schedule_type, interval_seconds, daily_time_utc, default_limit, next_run_at, metadata)
    VALUES (
      ${jobKey},
      'active',
      'interval',
      ${intervalSeconds},
      NULL,
      1,
      now() + make_interval(secs => ${intervalSeconds}),
      ${sql.json(toJson({
        scope: "business",
        kind: "conversation_watch",
        business_id: input.businessId,
        owner_profile_id: input.ownerProfileId,
        business_slug: input.slug,
        business_name: input.name,
        description: "Check this business's watched conversations before more outward distribution."
      }))}
    )
    ON CONFLICT (job_key) DO UPDATE
    SET status = 'active',
        schedule_type = 'interval',
        interval_seconds = EXCLUDED.interval_seconds,
        daily_time_utc = NULL,
        default_limit = 1,
        metadata = EXCLUDED.metadata,
        updated_at = now()
  `;
}

export async function ensureBusinessCustomerOpsCronJob(input: {
  businessId: string;
  ownerProfileId: string;
  slug: string;
  name: string;
}) {
  const sql = db();
  const jobKey = `${businessCustomerOpsCronPrefix}${input.businessId}`;
  const intervalSeconds = Math.max(3600, Math.min(Number(process.env.TAKYON_CUSTOMER_OPS_WATCH_SECONDS || 6 * 60 * 60), 24 * 60 * 60));
  await sql`
    INSERT INTO cron_jobs (job_key, status, schedule_type, interval_seconds, daily_time_utc, default_limit, next_run_at, metadata)
    VALUES (
      ${jobKey},
      'active',
      'interval',
      ${intervalSeconds},
      NULL,
      1,
      now() + make_interval(secs => ${intervalSeconds}),
      ${sql.json(toJson({
        scope: "business",
        kind: "customer_ops_watch",
        business_id: input.businessId,
        owner_profile_id: input.ownerProfileId,
        business_slug: input.slug,
        business_name: input.name,
        description: "Refresh generated-app customer, entitlement, subscription, usage, and revenue state for this business."
      }))}
    )
    ON CONFLICT (job_key) DO UPDATE
    SET status = 'active',
        schedule_type = 'interval',
        interval_seconds = EXCLUDED.interval_seconds,
        daily_time_utc = NULL,
        default_limit = 1,
        metadata = EXCLUDED.metadata,
        updated_at = now()
  `;
}

export async function reconcileBusinessCeoCronJobs(input: { profileId?: string | null } = {}) {
  const sql = db();
  const activeBusinesses = input.profileId
    ? await sql<{ id: string; owner_profile_id: string; slug: string; name: string }[]>`
        SELECT b.id, b.owner_profile_id, b.slug, b.name
        FROM businesses b
        JOIN business_memberships bm ON bm.business_id = b.id
        WHERE b.status = 'active'
          AND bm.profile_id = ${input.profileId}
        ORDER BY b.created_at ASC
      `
    : await sql<{ id: string; owner_profile_id: string; slug: string; name: string }[]>`
        SELECT id, owner_profile_id, slug, name
        FROM businesses
        WHERE status = 'active'
        ORDER BY created_at ASC
      `;

  for (const business of activeBusinesses) {
    await ensureBusinessCeoCronJob({
      businessId: business.id,
      ownerProfileId: business.owner_profile_id,
      slug: business.slug,
      name: business.name
    });
    await ensureBusinessConversationCronJob({
      businessId: business.id,
      ownerProfileId: business.owner_profile_id,
      slug: business.slug,
      name: business.name
    });
    await ensureBusinessCustomerOpsCronJob({
      businessId: business.id,
      ownerProfileId: business.owner_profile_id,
      slug: business.slug,
      name: business.name
    });
  }

  await sql`
    DELETE FROM cron_jobs
    WHERE job_key = 'ceo_wakeup'
  `;

  await sql`
    DELETE FROM cron_jobs cj
    WHERE cj.job_key LIKE ${`${businessCeoCronPrefix}%`}
      AND NOT EXISTS (
        SELECT 1
        FROM businesses b
        WHERE b.id::text = cj.metadata->>'business_id'
          AND b.status IN ('active', 'paused')
      )
  `;

  await sql`
    DELETE FROM cron_jobs cj
    WHERE cj.job_key LIKE ${`${businessConversationCronPrefix}%`}
      AND NOT EXISTS (
        SELECT 1
        FROM businesses b
        WHERE b.id::text = cj.metadata->>'business_id'
          AND b.status IN ('active', 'paused')
      )
  `;

  await sql`
    DELETE FROM cron_jobs cj
    WHERE cj.job_key LIKE ${`${businessCustomerOpsCronPrefix}%`}
      AND NOT EXISTS (
        SELECT 1
        FROM businesses b
        WHERE b.id::text = cj.metadata->>'business_id'
          AND b.status IN ('active', 'paused')
      )
  `;

  const dailyTimeUtc = process.env.TAKYON_CEO_WAKEUP_UTC?.trim() || "09:00:00";
  await sql`
    UPDATE cron_jobs
    SET next_run_at = ${nextDailyUtcDate(dailyTimeUtc)},
        updated_at = now()
    WHERE job_key LIKE ${`${businessCeoCronPrefix}%`}
      AND status = 'active'
      AND last_started_at IS NULL
      AND next_run_at <= now()
  `;
}

export async function listCronJobs(input: { profileId?: string | null; includeAll?: boolean } = {}) {
  await reconcileBusinessCeoCronJobs({ profileId: input.includeAll ? null : input.profileId ?? null });
  const sql = db();
  if (input.profileId && !input.includeAll) {
    return sql<CronJobRow[]>`
      SELECT cj.job_key, cj.status, cj.schedule_type, cj.interval_seconds, cj.daily_time_utc,
             cj.default_limit, cj.next_run_at, cj.last_started_at, cj.last_completed_at,
             cj.last_result, cj.last_error, cj.locked_by, cj.locked_at, cj.metadata, cj.created_at, cj.updated_at
      FROM cron_jobs cj
      WHERE cj.job_key = 'agent_runner'
         OR EXISTS (
           SELECT 1
           FROM business_memberships bm
           WHERE bm.business_id::text = cj.metadata->>'business_id'
             AND bm.profile_id = ${input.profileId}
         )
      ORDER BY cj.next_run_at ASC, cj.job_key ASC
    `;
  }
  return sql<CronJobRow[]>`
    SELECT job_key, status, schedule_type, interval_seconds, daily_time_utc,
           default_limit, next_run_at, last_started_at, last_completed_at,
           last_result, last_error, locked_by, locked_at, metadata, created_at, updated_at
    FROM cron_jobs
    ORDER BY next_run_at ASC, job_key ASC
  `;
}

export async function dispatchDueCronJobs(input: { dispatcherId: string; limit?: number }) {
  await reconcileBusinessCeoCronJobs();
  const sql = db();
  const limit = input.limit ?? 5;
  const claimed = await sql.begin(async (tx) => {
    return tx<CronJobRow[]>`
      WITH due AS (
        SELECT job_key
        FROM cron_jobs
        WHERE status = 'active'
          AND next_run_at <= now()
          AND (locked_at IS NULL OR locked_at < now() - interval '10 minutes')
        ORDER BY next_run_at ASC
        FOR UPDATE SKIP LOCKED
        LIMIT ${limit}
      )
      UPDATE cron_jobs j
      SET locked_by = ${input.dispatcherId},
          locked_at = now(),
          last_started_at = now(),
          updated_at = now()
      FROM due
      WHERE j.job_key = due.job_key
      RETURNING j.job_key, j.status, j.schedule_type, j.interval_seconds, j.daily_time_utc,
                j.default_limit, j.next_run_at, j.last_started_at, j.last_completed_at,
                j.last_result, j.last_error, j.locked_by, j.locked_at, j.metadata, j.created_at, j.updated_at
    `;
  });

  const results: Array<{ job_key: string; status: "completed" | "blocked" | "failed"; message?: string }> = [];
  for (const job of claimed) {
    try {
      if (isBusinessCeoCronJob(job.job_key)) {
        await enqueueBusinessCeoWakeup(job);
      } else if (isBusinessConversationCronJob(job.job_key)) {
        await enqueueBusinessConversationWatch(job);
      } else if (isBusinessCustomerOpsCronJob(job.job_key)) {
        await enqueueBusinessCustomerOpsWatch(job);
      } else if (job.job_key === "agent_runner") {
        // The local worker performs the long-running claims. The cron tick only records the pulse.
      } else {
        throw new Error(`No dispatcher registered for cron job ${job.job_key}.`);
      }

      await markCronJobComplete(job.job_key, { dispatched: true });
      results.push({ job_key: job.job_key, status: "completed" });
    } catch (error) {
      const message = error instanceof Error ? error.message : "Unknown cron error.";
      await markCronJobFailed(job.job_key, message);
      results.push({ job_key: job.job_key, status: "failed", message });
    }
  }

  return { claimed: claimed.length, results };
}

async function enqueueBusinessCeoWakeup(job: CronJobRow) {
  const businessId = cronBusinessId(job);
  if (!businessId) throw new Error(`Business CEO cron job ${job.job_key} is missing metadata.business_id.`);
  await enqueueWorkflowJob({
    companyId: businessId,
    profileId: cronBusinessProfileId(job),
    workflowId: "ceo_wakeup",
    lane: "ceo",
    priority: 45,
    payload: { reason: "cron", cron_job_key: job.job_key }
  });
}

async function enqueueBusinessConversationWatch(job: CronJobRow) {
  const businessId = cronBusinessId(job);
  if (!businessId) throw new Error(`Business conversation cron job ${job.job_key} is missing metadata.business_id.`);
  await enqueueWorkflowJob({
    companyId: businessId,
    profileId: cronBusinessProfileId(job),
    workflowId: "conversation_watch",
    lane: "community",
    priority: 57,
    maxAttempts: 1,
    payload: { reason: "cron", cron_job_key: job.job_key }
  });
}

async function enqueueBusinessCustomerOpsWatch(job: CronJobRow) {
  const businessId = cronBusinessId(job);
  if (!businessId) throw new Error(`Business customer ops cron job ${job.job_key} is missing metadata.business_id.`);
  await enqueueWorkflowJob({
    companyId: businessId,
    profileId: cronBusinessProfileId(job),
    workflowId: "customer_ops_watch",
    lane: "ceo",
    priority: 56,
    maxAttempts: 1,
    payload: { reason: "cron", cron_job_key: job.job_key }
  });
}

async function markCronJobComplete(jobKey: string, result: Record<string, unknown>) {
  const sql = db();
  await sql.unsafe(`
    UPDATE cron_jobs j
    SET locked_by = NULL,
        locked_at = NULL,
        last_completed_at = now(),
        last_result = $1::jsonb,
        last_error = NULL,
        next_run_at = ${nextRunSqlExpression("j")},
        updated_at = now()
    WHERE job_key = $2
  `, [JSON.stringify(toJson(result)), jobKey]);
}

async function markCronJobFailed(jobKey: string, error: string) {
  const sql = db();
  await sql.unsafe(`
    UPDATE cron_jobs j
    SET locked_by = NULL,
        locked_at = NULL,
        last_completed_at = now(),
        last_error = $1,
        next_run_at = ${nextRunSqlExpression("j")},
        updated_at = now()
    WHERE job_key = $2
  `, [error, jobKey]);
}
