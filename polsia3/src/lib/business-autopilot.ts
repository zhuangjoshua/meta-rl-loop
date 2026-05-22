import { ensureBudgetAccount, usdToMicrousd } from "./business-budget";
import { upsertBusinessCampaign } from "./business-campaigns";
import { upsertBusinessMemory } from "./business-memory";
import { syncBusinessWorkspace } from "./business-workspace";
import { db } from "./db";
import { createEvent } from "./events";
import { listToolCapabilities } from "./tool-availability";
import { enqueueWorkflowJob } from "./workflow-jobs";

type AutopilotQueueItem =
  | { workflow_id: string; status: "queued"; jobId: string }
  | { workflow_id: string; status: "already_present"; existingStatus: string }
  | { workflow_id: string; status: "blocked"; reason: string; missing: string[]; setup: string[]; setupCommand?: string };

async function loadBusiness(businessId: string) {
  const sql = db();
  const rows = await sql<{ id: string; name: string; slug: string }[]>`
    SELECT id, name, slug
    FROM businesses
    WHERE id = ${businessId}
    LIMIT 1
  `;
  const business = rows[0];
  if (!business) throw new Error("Business not found.");
  return business;
}

export async function runBusinessAutopilot(input: {
  businessId: string;
  profileId: string;
  instruction?: string | null;
  campaignName?: string | null;
  campaignBudgetUsd?: number | null;
}) {
  const business = await loadBusiness(input.businessId);
  const capabilities = await listToolCapabilities({ businessId: input.businessId, profileId: input.profileId });
  let campaignId: string | null = null;

  if (input.campaignName || input.campaignBudgetUsd) {
    const campaign = await upsertBusinessCampaign({
      businessId: input.businessId,
      profileId: input.profileId,
      name: input.campaignName || `autopilot-${new Date().toISOString().slice(0, 10)}`,
      kind: "distribution",
      status: "active",
      budgetCapMicrousd: input.campaignBudgetUsd ? usdToMicrousd(input.campaignBudgetUsd) : null,
      metadata: { source: "takyon_autopilot", instruction: input.instruction ?? null }
    });
    campaignId = campaign.id;
    if (input.campaignBudgetUsd) {
      await ensureBudgetAccount({
        businessId: input.businessId,
        campaignId,
        hardLimitMicrousd: usdToMicrousd(input.campaignBudgetUsd),
        metadata: { source: "takyon_autopilot" }
      });
    }
  }

  const workspace = await syncBusinessWorkspace({
    businessId: input.businessId,
    profileId: input.profileId,
    reason: "autopilot_wake"
  });
  const unavailableCapabilities = capabilities.filter((capability) => !capability.canRun);
  const runtimeCapability = capabilities.find((capability) => capability.key === "takyon_runtime");
  if (!runtimeCapability?.canRun) {
    const blockedItem = {
      workflow_id: "ceo_wakeup",
      status: "blocked",
      reason: runtimeCapability?.reason ?? "Local Mac CEO runtime capability is unavailable.",
      missing: runtimeCapability?.missing ?? ["ANTHROPIC_API_KEY or OPENAI_API_KEY"],
      setup: runtimeCapability?.setup ?? ["Set ANTHROPIC_API_KEY or OPENAI_API_KEY in .env.local with: ./takyon secret set ANTHROPIC_API_KEY --stdin"],
      setupCommand: runtimeCapability?.setupCommand
    } satisfies AutopilotQueueItem;
    const blocked: AutopilotQueueItem[] = [blockedItem];
    await createEvent({
      businessId: input.businessId,
      actorProfileId: input.profileId,
      kind: "takyon.ceo_wake_blocked",
      subjectType: "business",
      subjectId: input.businessId,
      payload: { reason: blockedItem.reason, missing: blockedItem.missing, workspace_root: workspace.root }
    });
    const finalWorkspace = await syncBusinessWorkspace({
      businessId: input.businessId,
      profileId: input.profileId,
      reason: "ceo_wake_blocked"
    });
    return {
      business,
      workspace: { root: finalWorkspace.root },
      reasons: [
        "CEO wake was not queued because the local Mac CEO runtime is unavailable.",
        "No remote runtime URL is required. Configure a local model key before autonomous CEO work can run.",
        "The business workspace and missing capability report were still updated."
      ],
      ceoWakeupJobId: null,
      campaignId,
      queued: [] as AutopilotQueueItem[],
      blocked,
      unavailableCapabilities
    };
  }
  const reasons = [
    "Queued the CEO wake/sleep loop; terminal code did not choose product, outreach, pricing, or recovery work.",
    "Product/value delivery and outreach/demand creation are standing obligations in the business workspace; the CEO must inspect evidence before deciding what to do next.",
    unavailableCapabilities.length
      ? `${unavailableCapabilities.length} capabilities are unavailable and will be visible in tools/missing-keys.md.`
      : "Capability reports are available in tools/."
  ];

  const ceo = await enqueueWorkflowJob({
    companyId: input.businessId,
    profileId: input.profileId,
    workflowId: "ceo_wakeup",
    lane: "ceo",
    priority: 110,
    maxAttempts: 1,
    payload: {
      source: "takyon_autopilot",
      operator_instruction: input.instruction ?? null,
      campaign_id: campaignId,
      workspace_root: workspace.root,
      standing_obligations: ["product", "outreach"],
      rule: "CEO decides from workspace evidence; deterministic runner executes only bounded jobs with receipts."
    }
  });
  const queued: AutopilotQueueItem[] = [{ workflow_id: "ceo_wakeup", status: "queued", jobId: ceo.id }];

  await upsertBusinessMemory({
    businessId: input.businessId,
    profileId: input.profileId,
    namespace: "operations",
    memoryKey: "latest-wake",
    title: "Latest CEO wake",
    content: JSON.stringify({
      instruction: input.instruction ?? null,
      campaign_id: campaignId,
      ceo_wakeup_job_id: ceo.id,
      workspace_root: workspace.root,
      unavailable_capabilities: unavailableCapabilities.map((capability) => ({
        key: capability.key,
        label: capability.label,
        reason: capability.reason,
        missing: capability.missing
      }))
    }, null, 2),
    evidence: [{ kind: "workflow_job", workflow_job_id: ceo.id }],
    metadata: { source: "takyon_autopilot" }
  });

  await createEvent({
    businessId: input.businessId,
    actorProfileId: input.profileId,
    kind: "takyon.ceo_wake_queued",
    subjectType: "workflow_job",
    subjectId: ceo.id,
    payload: {
      instruction: input.instruction ?? null,
      campaign_id: campaignId,
      unavailable_capability_count: unavailableCapabilities.length,
      workspace_root: workspace.root
    }
  });
  const finalWorkspace = await syncBusinessWorkspace({
    businessId: input.businessId,
    profileId: input.profileId,
    reason: "ceo_wake_queued"
  });

  return {
    business,
    workspace: { root: finalWorkspace.root },
    reasons,
    ceoWakeupJobId: ceo.id,
    campaignId,
    queued,
    blocked: [] as AutopilotQueueItem[],
    unavailableCapabilities
  };
}
