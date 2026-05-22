import { db } from "./db";
import { getVercelEnv } from "./env";
import { createEvent } from "./events";
import { createInboxMessage } from "./inbox";

type VercelRemovalResult = {
  target: string;
  kind: "alias" | "deployment";
  status: "removed" | "skipped" | "failed";
  detail: string;
};

function hostname(value: string | null) {
  if (!value) return null;
  try {
    return new URL(value).hostname;
  } catch {
    return value.replace(/^https?:\/\//, "").replace(/\/.*$/, "") || null;
  }
}

async function vercelDelete(path: string, search: Record<string, string>) {
  const env = getVercelEnv();
  const url = new URL(`https://api.vercel.com${path}`);
  url.searchParams.set("teamId", env.VERCEL_TEAM_ID);
  for (const [key, value] of Object.entries(search)) {
    if (value) url.searchParams.set(key, value);
  }
  const response = await fetch(url, {
    method: "DELETE",
    headers: { Authorization: `Bearer ${env.VERCEL_TOKEN}` },
    signal: AbortSignal.timeout(30_000)
  });
  const body = await response.json().catch(() => null);
  if (!response.ok && response.status !== 404) {
    const message =
      body && typeof body === "object" && "error" in body
        ? JSON.stringify((body as { error?: unknown }).error)
        : `Vercel returned ${response.status}.`;
    throw new Error(message);
  }
  return response.status === 404 ? { status: "not_found" } : body;
}

async function removeVercelTargets(deployments: Array<{ alias_url: string | null; deployment_url: string | null }>) {
  const targets: VercelRemovalResult[] = [];
  for (const deployment of deployments) {
    const aliasHost = hostname(deployment.alias_url);
    if (aliasHost) {
      try {
        await vercelDelete(`/v2/aliases/${encodeURIComponent(aliasHost)}`, {});
        targets.push({ target: aliasHost, kind: "alias", status: "removed", detail: "Alias removed from Vercel." });
      } catch (error) {
        targets.push({
          target: aliasHost,
          kind: "alias",
          status: "failed",
          detail: error instanceof Error ? error.message : "Alias removal failed."
        });
      }
    }

    if (deployment.deployment_url) {
      try {
        await vercelDelete("/v13/deployments/by-url", { url: deployment.deployment_url });
        targets.push({
          target: deployment.deployment_url,
          kind: "deployment",
          status: "removed",
          detail: "Deployment delete requested through Vercel."
        });
      } catch (error) {
        targets.push({
          target: deployment.deployment_url,
          kind: "deployment",
          status: "failed",
          detail: error instanceof Error ? error.message : "Deployment deletion failed."
        });
      }
    }
  }

  if (!targets.length) {
    targets.push({ target: "generated app", kind: "deployment", status: "skipped", detail: "No deployment URL was recorded." });
  }
  return targets;
}

export async function endCompanyOperations(input: { companyId: string; profileId: string; reason?: string }) {
  const sql = db();
  const deployments = await sql<{ id: string; alias_url: string | null; deployment_url: string | null }[]>`
    SELECT id, alias_url, deployment_url
    FROM generated_app_deployments
    WHERE business_id = ${input.companyId}
      AND status IN ('queued', 'running', 'completed', 'blocked', 'failed')
    ORDER BY created_at DESC
    LIMIT 20
  `;

  const dbResult = await sql.begin(async (tx) => {
    const jobs = await tx<{ count: number }[]>`
        UPDATE workflow_jobs
        SET status = 'cancelled',
            error = COALESCE(error, 'Company ended by operator.'),
            locked_by = NULL,
            locked_at = NULL,
            completed_at = COALESCE(completed_at, now()),
            updated_at = now()
        WHERE business_id = ${input.companyId}
          AND status IN ('queued', 'running')
        RETURNING 1 AS count
      `;
    const tasks = await tx<{ count: number }[]>`
        UPDATE tasks
        SET status = 'cancelled',
            completed_at = COALESCE(completed_at, now()),
            updated_at = now()
        WHERE business_id = ${input.companyId}
          AND status IN ('queued', 'running', 'blocked', 'failed')
        RETURNING 1 AS count
      `;
    const agentRuns = await tx<{ count: number }[]>`
        UPDATE agent_runs
        SET status = 'cancelled',
            error = COALESCE(error, 'Company ended by operator.'),
            completed_at = COALESCE(completed_at, now()),
            updated_at = now()
        WHERE business_id = ${input.companyId}
          AND status IN ('queued', 'running')
        RETURNING 1 AS count
      `;
    const builds = await tx<{ count: number }[]>`
        UPDATE generated_app_builds
        SET status = 'cancelled',
            error = COALESCE(error, 'Company ended by operator.'),
            completed_at = COALESCE(completed_at, now()),
            updated_at = now()
        WHERE business_id = ${input.companyId}
          AND status IN ('queued', 'running', 'blocked', 'failed')
        RETURNING 1 AS count
      `;
    const generatedDeployments = await tx<{ count: number }[]>`
        UPDATE generated_app_deployments
        SET status = 'cancelled',
            error = COALESCE(error, 'Company ended by operator.'),
            updated_at = now()
        WHERE business_id = ${input.companyId}
          AND status IN ('queued', 'running', 'completed', 'blocked', 'failed')
        RETURNING 1 AS count
      `;

    await tx`
      UPDATE company_sites
      SET status = 'offline',
          updated_at = now()
      WHERE business_id = ${input.companyId}
    `;
    await tx`
      UPDATE businesses
      SET status = 'archived',
          updated_at = now()
      WHERE id = ${input.companyId}
    `;

    return {
      workflowJobsCancelled: jobs.length,
      tasksCancelled: tasks.length,
      agentRunsCancelled: agentRuns.length,
      buildsCancelled: builds.length,
      deploymentsMarkedCancelled: generatedDeployments.length
    };
  });

  const vercelTargets = await removeVercelTargets(deployments);
  const failedVercel = vercelTargets.filter((target) => target.status === "failed");
  const summary = [
    "Ended app operations.",
    `${dbResult.workflowJobsCancelled} workflow jobs cancelled.`,
    `${dbResult.agentRunsCancelled} agent runs cancelled.`,
    `${dbResult.tasksCancelled} tasks cancelled.`,
    `${dbResult.deploymentsMarkedCancelled} deployment rows marked cancelled.`,
    failedVercel.length ? `${failedVercel.length} Vercel removal attempts failed; see event details.` : "Vercel alias/deployment removal attempted."
  ].join(" ");

  await createEvent({
    businessId: input.companyId,
    actorProfileId: input.profileId,
    kind: "company.ended",
    subjectType: "business",
    subjectId: input.companyId,
    payload: { reason: input.reason ?? null, db: dbResult, vercelTargets }
  });
  await createInboxMessage({
    companyId: input.companyId,
    profileId: input.profileId,
    authorLabel: "Takyon",
    body: summary,
    source: failedVercel.length ? "shutdown_blocked" : "shutdown"
  });

  return { ...dbResult, vercelTargets };
}
