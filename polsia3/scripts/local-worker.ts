import { randomUUID } from "node:crypto";
import { closeDbConnections } from "../src/lib/db";
import { createAgentRun, createAgentRunStep, finishAgentRun } from "../src/lib/agent-runs";
import { isBusinessSkillWorkflowId, runBusinessSkillWorkflow } from "../src/lib/business-skills";
import { runCeoReasoning } from "../src/lib/ceo";
import { observeCampaignAndCustomerLearning } from "../src/lib/campaign-learning";
import { runCommunityResearch } from "../src/lib/community";
import { isBusinessTestMode } from "../src/lib/companies";
import { runConversationWatch } from "../src/lib/business-conversations";
import { db } from "../src/lib/db";
import { ConfigurationError, IntegrationCallError } from "../src/lib/errors";
import { createEvent } from "../src/lib/events";
import { runClaudeSdkProductLane } from "../src/lib/generated-apps/agent-builder";
import { buildGeneratedWebsite } from "../src/lib/generated-apps/builder";
import { ensureGeneratedAppPaymentLink } from "../src/lib/generated-apps/commerce";
import { runCustomerOpsWatch } from "../src/lib/generated-apps/customer-ops";
import { ensureGeneratedAppRails } from "../src/lib/generated-apps/records";
import { runGetFirstCustomerGoal } from "../src/lib/goals";
import { createInboxMessage } from "../src/lib/inbox";
import { createSoraCreative, syncSoraCreative } from "../src/lib/media-generation";
import { runOutreachCopy } from "../src/lib/outreach";
import { updateTaskStatus, type TaskStatus } from "../src/lib/tasks";
import { preflightCapabilityGroups } from "../src/lib/tool-availability";
import { recordWorkflowOutcomeMemory } from "../src/lib/business-learning";
import { syncBusinessWorkspace } from "../src/lib/business-workspace";
import { takyonCapabilityGroups } from "../src/lib/takyon-registry";
import {
  claimWorkflowJobs,
  completeWorkflowJob as completeWorkflowJobRecord,
  enqueueCampaignObservationJob,
  enqueueWorkflowJob,
  enqueueSoraSyncJob,
  recoverStaleWorkflowJobs,
  retryWorkflowJob
} from "../src/lib/workflow-jobs";
import { runXSocialLane } from "../src/lib/x-social";

const workerId = `local-mac-${randomUUID()}`;

function workerLog(message: string) {
  if (process.env.WORKER_VERBOSE !== "0") console.log(message);
}

function shortId(id: string) {
  return id.slice(0, 8);
}

function payloadRecord(value: unknown) {
  return value && typeof value === "object" && !Array.isArray(value) ? (value as Record<string, unknown>) : {};
}

function payloadString(value: unknown, key: string) {
  const record = payloadRecord(value);
  const text = record[key];
  return typeof text === "string" && text.trim() ? text.trim() : null;
}

function payloadNumber(value: unknown, key: string) {
  const record = payloadRecord(value);
  const number = Number(record[key]);
  return Number.isFinite(number) ? number : 0;
}

function hasPendingSoraResult(value: unknown): boolean {
  const record = payloadRecord(value);
  const status = typeof record.status === "string" ? record.status : "";
  if (["submitted", "processing", "in_progress", "queued"].includes(status)) return true;
  const synced = Array.isArray(record.synced) ? record.synced : [];
  return synced.some((item) => {
    const row = payloadRecord(item);
    return ["submitted", "processing", "in_progress", "queued"].includes(String(row.status || ""));
  });
}

function nextIterationFromSummary(summary: string | null | undefined) {
  const match = summary?.match(/\bNEXT_ITERATION:\s*([^\n]+)/i);
  return match?.[1]?.trim() || null;
}

async function scheduleCeoNextIteration(input: {
  businessId: string;
  profileId?: string | null;
  sourceWorkflowId: string;
  sourceJobId: string;
  nextIteration: string;
}) {
  await createEvent({
    businessId: input.businessId,
    actorProfileId: input.profileId ?? null,
    kind: "product.next_iteration_requested",
    subjectType: "workflow_job",
    subjectId: input.sourceJobId,
    payload: {
      source_workflow_id: input.sourceWorkflowId,
      next_iteration: input.nextIteration
    }
  });
  return enqueueWorkflowJob({
    companyId: input.businessId,
    profileId: input.profileId ?? null,
    workflowId: "ceo_wakeup",
    lane: "ceo",
    priority: 95,
    maxAttempts: 1,
    runAfter: new Date(Date.now() + 30_000),
    payload: {
      source: "product_next_iteration",
      source_workflow_id: input.sourceWorkflowId,
      source_job_id: input.sourceJobId,
      operator_instruction: `Review product increment and decide whether to continue with: ${input.nextIteration}`,
      next_iteration: input.nextIteration
    }
  });
}

function isRetryableWorkerError(error: unknown) {
  const status = error instanceof IntegrationCallError ? error.status : undefined;
  const message = error instanceof Error ? error.message : String(error);
  return status === 408 || status === 409 || status === 425 || status === 429 || status === 500 || status === 502 || status === 503 || status === 504 || status === 529 || /\boverloaded\b/i.test(message);
}

async function capabilityGroupsForWorkflow(input: { workflowId: string; businessId: string }) {
  const groups = takyonCapabilityGroups(input.workflowId);
  if (input.workflowId === "x_social" && await isBusinessTestMode(input.businessId)) {
    return groups
      .map((group) => group.filter((key) => key !== "x_posting"))
      .filter((group) => group.length > 0);
  }
  return groups;
}

async function preflightJobCapabilities(job: Awaited<ReturnType<typeof claimWorkflowJobs>>[number]) {
  const groups = await capabilityGroupsForWorkflow({ workflowId: job.workflow_id, businessId: job.business_id });
  const block = await preflightCapabilityGroups({
    workflowId: job.workflow_id,
    groups,
    businessId: job.business_id,
    profileId: job.profile_id
  });
  if (!block) return null;
  await createEvent({
    businessId: job.business_id,
    actorProfileId: job.profile_id,
    kind: "tool.capability_blocked",
    subjectType: "workflow_job",
    subjectId: job.id,
    payload: block
  });
  return { error: block.error, result: block };
}

async function syncTaskStatusFromWorkflowJobs(companyId: string, taskId: string) {
  const sql = db();
  const rows = await sql<{ queued: number; running: number; failed: number; blocked: number }[]>`
    SELECT
      count(*) FILTER (WHERE status = 'queued')::int AS queued,
      count(*) FILTER (WHERE status = 'running')::int AS running,
      count(*) FILTER (WHERE status = 'failed')::int AS failed,
      count(*) FILTER (WHERE status = 'blocked')::int AS blocked
    FROM workflow_jobs
    WHERE business_id = ${companyId}
      AND task_id = ${taskId}
  `;
  const summary = rows[0] ?? { queued: 0, running: 0, failed: 0, blocked: 0 };
  let status: TaskStatus = "completed";
  if (summary.queued > 0 || summary.running > 0) status = "running";
  else if (summary.failed > 0 || summary.blocked > 0) status = "blocked";
  await updateTaskStatus({ companyId, taskId, status });
}

async function scheduleCeoReviewAfterWorkflowOutcome(row: Awaited<ReturnType<typeof completeWorkflowJobRecord>>) {
  if (!row || row.workflow_id === "ceo_wakeup") return null;
  if (!["completed", "blocked", "failed"].includes(row.status)) return null;
  return enqueueWorkflowJob({
    companyId: row.business_id,
    profileId: row.profile_id,
    workflowId: "ceo_wakeup",
    lane: "ceo",
    priority: 110,
    maxAttempts: 1,
    runAfter: new Date(Date.now() + 30_000),
    payload: {
      source: "workflow_outcome_review",
      source_workflow_id: row.workflow_id,
      source_job_id: row.id,
      source_status: row.status,
      operator_instruction: `Inspect the ${row.workflow_id} ${row.status} receipt and decide the next product, outreach, recovery, or learning move from business evidence.`
    }
  });
}

async function completeWorkflowJob(input: Parameters<typeof completeWorkflowJobRecord>[0]) {
  const row = await completeWorkflowJobRecord(input);
  if (row?.task_id) {
    await syncTaskStatusFromWorkflowJobs(row.business_id, row.task_id);
  }
  if (row) {
    if (["completed", "blocked", "failed", "cancelled"].includes(row.status)) {
      await recordWorkflowOutcomeMemory({
        businessId: row.business_id,
        profileId: row.profile_id,
        workflowJobId: row.id,
        workflowId: row.workflow_id,
        lane: row.lane,
        status: row.status as "completed" | "blocked" | "failed" | "cancelled",
        attempts: row.attempts,
        error: row.error,
        result: row.result
      }).catch((error) => {
        workerLog(`worker: learning write failed ${row.workflow_id} ${shortId(row.id)} error=${error instanceof Error ? error.message : String(error)}`);
      });
    }
    await syncBusinessWorkspace({ businessId: row.business_id, profileId: row.profile_id, reason: `workflow_${row.status}` }).catch(async (error) => {
      const message = error instanceof Error ? error.message : String(error);
      workerLog(`worker: workspace sync failed ${row.workflow_id} ${shortId(row.id)} error=${message}`);
      await createEvent({
        businessId: row.business_id,
        actorProfileId: row.profile_id,
        kind: "business_workspace.sync_failed",
        subjectType: "workflow_job",
        subjectId: row.id,
        payload: { workflow_id: row.workflow_id, status: row.status, error: message }
      }).catch(() => null);
    });
    await scheduleCeoReviewAfterWorkflowOutcome(row).catch((error) => {
      workerLog(`worker: ceo review schedule failed ${row.workflow_id} ${shortId(row.id)} error=${error instanceof Error ? error.message : String(error)}`);
    });
    workerLog(
      `worker: ${row.status} ${row.workflow_id} ${shortId(row.id)}${row.error ? ` error=${row.error}` : ""}`
    );
  }
  return row;
}

async function handleJob(job: Awaited<ReturnType<typeof claimWorkflowJobs>>[number]) {
  workerLog(
    `worker: start ${job.workflow_id} ${shortId(job.id)} business=${shortId(job.business_id)} attempt=${job.attempts}/${job.max_attempts}`
  );

  const capabilityBlock = await preflightJobCapabilities(job);
  if (capabilityBlock) {
    workerLog(`worker: blocked preflight ${job.workflow_id} ${shortId(job.id)} error=${capabilityBlock.error}`);
    return completeWorkflowJob({
      jobId: job.id,
      status: "blocked",
      result: capabilityBlock.result,
      error: capabilityBlock.error
    });
  }

  if (job.task_id) {
    await updateTaskStatus({ companyId: job.business_id, taskId: job.task_id, status: "running" });
  }

  const run = await createAgentRun({
    companyId: job.business_id,
    taskId: job.task_id,
    workflowJobId: job.id,
    workflowId: job.workflow_id,
    agentKey: "local-worker",
    inputSnapshot: { workflow_id: job.workflow_id, lane: job.lane, payload: job.payload as Record<string, unknown> }
  });

  try {
    if (job.workflow_id === "website_build_deploy") {
      const result = await buildGeneratedWebsite({
        companyId: job.business_id,
        workflowJobId: job.id,
        operatorInstruction: payloadString(job.payload, "operator_instruction")
      });
      const status = result.deployment.status === "completed" ? "completed" : "blocked";
      await createAgentRunStep({
        runId: run.id,
        stepIndex: 1,
        toolName: "generated-app.build",
        output: result
      });
      await finishAgentRun({
        runId: run.id,
        status,
        output: result,
        error: status === "completed" ? null : result.deployment.error
      });
      return completeWorkflowJob({
        jobId: job.id,
        status,
        result,
        error: status === "completed" ? null : result.deployment.error
      });
    }

    if (job.workflow_id === "ceo_wakeup") {
      const result = await runCeoReasoning({ businessId: job.business_id });
      await createAgentRunStep({ runId: run.id, stepIndex: 1, toolName: "ceo.reasoning", output: result });
      await finishAgentRun({ runId: run.id, status: "completed", output: result });
      return completeWorkflowJob({ jobId: job.id, status: "completed", result });
    }

    if (job.workflow_id === "observe_campaign_results") {
      const result = await observeCampaignAndCustomerLearning({
        businessId: job.business_id,
        profileId: job.profile_id,
        sourceWorkflowId: payloadString(job.payload, "source_workflow_id")
      });
      await createAgentRunStep({ runId: run.id, stepIndex: 1, toolName: "campaign.observe_results", output: result });
      await finishAgentRun({ runId: run.id, status: "completed", output: result });
      return completeWorkflowJob({ jobId: job.id, status: "completed", result });
    }

    if (job.workflow_id === "goal_get_first_customer") {
      const result = await runGetFirstCustomerGoal({
        businessId: job.business_id,
        profileId: job.profile_id,
        payload: job.payload,
        workflowJobId: job.id,
        taskId: job.task_id
      });
      const status = result.status === "blocked" ? "blocked" : "completed";
      await createAgentRunStep({ runId: run.id, stepIndex: 1, toolName: "goal.get_first_customer", output: result });
      await finishAgentRun({
        runId: run.id,
        status,
        output: result,
        error: status === "blocked" ? "Goal is blocked by missing required capabilities." : null
      });
      return completeWorkflowJob({
        jobId: job.id,
        status,
        result,
        error: status === "blocked" ? "Goal is blocked by missing required capabilities." : null
      });
    }

    if (job.workflow_id === "stripe_setup") {
      const result = await ensureGeneratedAppPaymentLink({ slug: await siteSlug(job.business_id), planKey: "starter" });
      const output = { paymentLinkId: result.id, stripePriceId: result.stripe_price_id, stripePaymentLinkId: result.stripe_payment_link_id };
      await createAgentRunStep({ runId: run.id, stepIndex: 1, toolName: "stripe.payment_link", output });
      await finishAgentRun({ runId: run.id, status: "completed", output });
      return completeWorkflowJob({ jobId: job.id, status: "completed", result: output });
    }

    if (job.workflow_id === "ai_gateway_setup") {
      await ensureGeneratedAppRails(job.business_id);
      const output = { projectAiPolicy: "ready", gateway: "/api/ai-gateway/messages" };
      await createAgentRunStep({ runId: run.id, stepIndex: 1, toolName: "ai_gateway.ready", output });
      await finishAgentRun({ runId: run.id, status: "completed", output });
      return completeWorkflowJob({ jobId: job.id, status: "completed", result: output });
    }

    if (job.workflow_id === "x_social") {
      const result = await runXSocialLane({ businessId: job.business_id, profileId: job.profile_id, campaignId: payloadString(job.payload, "campaign_id") });
      const status = result.status === "completed" ? "completed" : "blocked";
      await createAgentRunStep({ runId: run.id, stepIndex: 1, toolName: "x.publish", output: result });
      await finishAgentRun({ runId: run.id, status, output: result, error: status === "completed" ? null : result.reason });
      const completed = await completeWorkflowJob({ jobId: job.id, status, result, error: status === "completed" ? null : result.reason });
      if (status === "completed") {
        const testMode = "testMode" in result && result.testMode === true;
        await enqueueCampaignObservationJob({
          companyId: job.business_id,
          profileId: job.profile_id,
          campaignId: payloadString(job.payload, "campaign_id"),
          sourceWorkflowId: job.workflow_id,
          reason: testMode
            ? "Observe test-mode X draft receipt and record what would have been distributed."
            : "Observe X launch response and decide the next growth move."
        });
      }
      return completed;
    }

    if (job.workflow_id === "meta_seedance") {
      const synced = await syncSoraCreative({ businessId: job.business_id });
      const result = synced.length
        ? { synced }
        : await createSoraCreative({ businessId: job.business_id, campaignId: payloadString(job.payload, "campaign_id") });
      await createAgentRunStep({ runId: run.id, stepIndex: 1, toolName: "meta.sora.display_only", output: result });
      await finishAgentRun({ runId: run.id, status: "completed", output: result });
      const completed = await completeWorkflowJob({ jobId: job.id, status: "completed", result });
      if (hasPendingSoraResult(result)) {
        await enqueueSoraSyncJob({
          companyId: job.business_id,
          profileId: job.profile_id,
          pollCount: payloadNumber(job.payload, "poll_count")
        });
      } else {
        await enqueueCampaignObservationJob({
          companyId: job.business_id,
          profileId: job.profile_id,
          campaignId: payloadString(job.payload, "campaign_id"),
          sourceWorkflowId: job.workflow_id,
          reason: "Observe Sora creative and launch response before deciding the next campaign."
        });
      }
      return completed;
    }

    if (job.workflow_id === "community_research") {
      const result = await runCommunityResearch({ businessId: job.business_id });
      await createAgentRunStep({ runId: run.id, stepIndex: 1, toolName: "community.research", output: result });
      await finishAgentRun({ runId: run.id, status: "completed", output: result });
      return completeWorkflowJob({ jobId: job.id, status: "completed", result });
    }

    if (job.workflow_id === "conversation_watch") {
      const result = await runConversationWatch({ businessId: job.business_id, profileId: job.profile_id });
      await createAgentRunStep({ runId: run.id, stepIndex: 1, toolName: "conversation.watch", output: result });
      await finishAgentRun({ runId: run.id, status: "completed", output: result });
      return completeWorkflowJob({ jobId: job.id, status: "completed", result });
    }

    if (job.workflow_id === "customer_ops_watch") {
      const result = await runCustomerOpsWatch({ businessId: job.business_id, profileId: job.profile_id });
      await createAgentRunStep({ runId: run.id, stepIndex: 1, toolName: "customer_ops.watch", output: result });
      await finishAgentRun({ runId: run.id, status: "completed", output: result });
      return completeWorkflowJob({ jobId: job.id, status: "completed", result });
    }

    if (job.workflow_id === "outreach_copy") {
      const result = await runOutreachCopy({ businessId: job.business_id, campaignId: payloadString(job.payload, "campaign_id") });
      await createAgentRunStep({ runId: run.id, stepIndex: 1, toolName: "outreach.copy", output: result });
      await finishAgentRun({ runId: run.id, status: "completed", output: result });
      return completeWorkflowJob({ jobId: job.id, status: "completed", result });
    }

    if (isBusinessSkillWorkflowId(job.workflow_id)) {
      const result = await runBusinessSkillWorkflow({
        businessId: job.business_id,
        profileId: job.profile_id,
        workflowId: job.workflow_id,
        payload: job.payload,
        workflowJobId: job.id
      });
      await createAgentRunStep({ runId: run.id, stepIndex: 1, toolName: `business_skill.${job.workflow_id}`, output: result });
      await finishAgentRun({ runId: run.id, status: "completed", output: result });
      return completeWorkflowJob({ jobId: job.id, status: "completed", result });
    }

    if (job.workflow_id === "product_backend" || job.workflow_id === "product_ui") {
      const result = await runClaudeSdkProductLane({
        companyId: job.business_id,
        workflowJobId: job.id,
        lane: job.workflow_id
      });
      await createAgentRunStep({
        runId: run.id,
        stepIndex: 1,
        toolName: job.workflow_id === "product_ui" ? "claude_agent_sdk.surface_builder" : "claude_agent_sdk.product_builder",
        output: result
      });
      await finishAgentRun({
        runId: run.id,
        status: result.status,
        output: result,
        error: result.status === "completed" ? null : result.reason
      });
      const completed = await completeWorkflowJob({
        jobId: job.id,
        status: result.status,
        result,
        error: result.status === "completed" ? null : result.reason
      });
      const nextIteration = result.status === "completed" ? nextIterationFromSummary(result.sdkSummary) : null;
      if (nextIteration) {
        await scheduleCeoNextIteration({
          businessId: job.business_id,
          profileId: job.profile_id,
          sourceWorkflowId: job.workflow_id,
          sourceJobId: job.id,
          nextIteration
        });
      }
      return completed;
    }

    if (job.workflow_id === "generated_app_auth") {
      const result = {
        status: "completed",
        routes: [
          "/api/generated-apps/[slug]/auth/request",
          "/api/generated-apps/[slug]/auth/verify",
          "/api/generated-apps/[slug]/session",
          "/api/generated-apps/[slug]/account",
          "/api/generated-apps/[slug]/billing/portal"
        ]
      };
      await createAgentRunStep({ runId: run.id, stepIndex: 1, toolName: "generated_app_auth.routes", output: result });
      await finishAgentRun({ runId: run.id, status: "completed", output: result });
      return completeWorkflowJob({ jobId: job.id, status: "completed", result });
    }

    if (job.workflow_id === "generated_app_users_entitlements") {
      await ensureGeneratedAppRails(job.business_id);
      const sql = db();
      const rows = await sql<{ plan_count: number; wallet_count: number; proxy_key_count: number }[]>`
        SELECT
          (SELECT count(*)::int FROM generated_app_plan_policies WHERE business_id = ${job.business_id}) AS plan_count,
          (SELECT count(*)::int FROM project_ai_wallets WHERE business_id = ${job.business_id}) AS wallet_count,
          (SELECT count(*)::int FROM project_ai_proxy_keys WHERE business_id = ${job.business_id} AND status = 'active') AS proxy_key_count
      `;
      const result = rows[0] ?? { plan_count: 0, wallet_count: 0, proxy_key_count: 0 };
      const ready = result.plan_count >= 2 && result.wallet_count >= 1;
      await createAgentRunStep({ runId: run.id, stepIndex: 1, toolName: "generated_app_users_entitlements.gate", output: result });
      await finishAgentRun({
        runId: run.id,
        status: ready ? "completed" : "blocked",
        output: result,
        error: ready ? null : "Generated app user/entitlement rails are incomplete."
      });
      return completeWorkflowJob({
        jobId: job.id,
        status: ready ? "completed" : "blocked",
        result,
        error: ready ? null : "Generated app user/entitlement rails are incomplete."
      });
    }

    await createAgentRunStep({
      runId: run.id,
      stepIndex: 1,
      toolName: "lane.dispatch",
      output: { status: "blocked", reason: "Lane implementation pending." }
    });
    await finishAgentRun({ runId: run.id, status: "blocked", output: { lane: job.lane }, error: "Lane implementation pending." });
    return completeWorkflowJob({
      jobId: job.id,
      status: "blocked",
      result: { lane: job.lane },
      error: "Lane implementation pending."
    });
  } catch (error) {
    const message = error instanceof Error ? error.message : "Unknown worker error.";
    if (isRetryableWorkerError(error) && job.attempts < job.max_attempts) {
      await finishAgentRun({ runId: run.id, status: "blocked", error: message, output: { retry: true } });
      const retried = await retryWorkflowJob({
        jobId: job.id,
        error: message,
        result: { retry: true, reason: "Retryable vendor/runtime error." },
        runAfter: new Date(Date.now() + 60_000)
      });
      if (retried) workerLog(`worker: retry queued ${retried.workflow_id} ${shortId(retried.id)} error=${message}`);
      return retried;
    }
    const status = error instanceof ConfigurationError || error instanceof IntegrationCallError ? "blocked" : "failed";
    await finishAgentRun({ runId: run.id, status, error: message });
    return completeWorkflowJob({ jobId: job.id, status, error: message });
  }
}

async function siteSlug(businessId: string) {
  const { db } = await import("../src/lib/db");
  const sql = db();
  const rows = await sql<{ slug: string }[]>`
    SELECT slug
    FROM company_sites
    WHERE business_id = ${businessId}
    LIMIT 1
  `;
  if (!rows[0]) throw new Error("Company site not found.");
  return rows[0].slug;
}

async function main() {
  const limit = Number(process.env.WORKER_CLAIM_LIMIT || 1);
  const businessId = process.env.WORKER_BUSINESS_ID?.trim() || null;
  const loop = process.argv.includes("--loop") || process.env.WORKER_LOOP === "1";
  const idleMs = Number(process.env.WORKER_IDLE_MS || 5000);
  const recoverOnly = process.argv.includes("--recover-only");
  const staleAfterMinutes = Number(process.env.WORKER_STALE_LOCK_MINUTES || 30);

  do {
    const recovered = await recoverStaleWorkflowJobs({ workerId, businessId, staleAfterMinutes });
    if (recovered.length) {
      console.log(`worker: recovered ${recovered.length} stale job${recovered.length === 1 ? "" : "s"}`);
    }
    if (recoverOnly) break;

    const jobs = await claimWorkflowJobs({ workerId, limit, businessId });
    if (jobs.length) {
      workerLog(`worker: claimed ${jobs.length} ${jobs.map((job) => `${job.workflow_id}:${shortId(job.id)}`).join(", ")}`);
      await Promise.all(jobs.map((job) => handleJob(job)));
    } else if (process.env.WORKER_VERBOSE === "2") {
      workerLog("worker: claimed 0");
    }
    if (!loop) break;
    if (jobs.length === 0) {
      await new Promise((resolve) => setTimeout(resolve, idleMs));
    }
  } while (loop);
}

main()
  .catch((error) => {
    console.error(error instanceof Error ? error.message : error);
    process.exitCode = 1;
  })
  .finally(async () => {
    await closeDbConnections();
  });
