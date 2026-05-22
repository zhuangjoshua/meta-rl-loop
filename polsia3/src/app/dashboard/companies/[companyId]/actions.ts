"use server";

import { revalidatePath } from "next/cache";
import { notFound } from "next/navigation";
import { requireProfile } from "@/lib/auth";
import { explainResponseAwareBlock, preflightResponseAwareDistribution, setDistributionResponseCheckPolicy } from "@/lib/business-conversations";
import { getCompanyForProfile } from "@/lib/companies";
import { endCompanyOperations } from "@/lib/company-shutdown";
import { createEvent } from "@/lib/events";
import { createInboxMessage } from "@/lib/inbox";
import { handleTakyonCeoChat } from "@/lib/takyon-ceo-router";
import { enqueueWorkflowJob, type WorkflowLane } from "@/lib/workflow-jobs";

type LeverConfig = {
  workflowId: string;
  lane: WorkflowLane;
  title: string;
};

const leverMap: Record<string, LeverConfig> = {
  twitter: { workflowId: "x_social", lane: "x_social", title: "Publish X post" },
  community: { workflowId: "community_research", lane: "community", title: "Refresh community targets" },
  outreach: { workflowId: "outreach_copy", lane: "outreach", title: "Refresh leads" },
  ads: { workflowId: "meta_seedance", lane: "meta_seedance", title: "Generate Sora creative" }
};

const responseAwareLevers = new Set(["twitter", "outreach", "ads"]);

async function requireCompanyAccess(companyId: string) {
  const profile = await requireProfile();
  const company = await getCompanyForProfile(companyId, profile.id);
  if (!company) notFound();
  return { profile, company };
}

export async function sendTakyonCeoMessageFromForm(companyId: string, formData: FormData) {
  const { profile } = await requireCompanyAccess(companyId);
  const body = String(formData.get("body") || "").trim();
  if (!body) return;

  await handleTakyonCeoChat({
    companyId,
    profileId: profile.id,
    authorLabel: profile.name || profile.email,
    body
  });

  revalidatePath(`/dashboard/companies/${companyId}`);
}

export async function startTakyonLeverFromForm(companyId: string, formData: FormData) {
  const { profile } = await requireCompanyAccess(companyId);
  const lever = String(formData.get("lever") || "");
  const config = leverMap[lever];
  if (!config) return;

  if (responseAwareLevers.has(lever)) {
    const block = await preflightResponseAwareDistribution({
      businessId: companyId,
      profileId: profile.id,
      workflowId: config.workflowId
    });
    if (block) {
      await explainResponseAwareBlock({
        businessId: companyId,
        profileId: profile.id,
        reason: block.reason
      });
      revalidatePath(`/dashboard/companies/${companyId}`);
      return;
    }
  }

  const job = await enqueueWorkflowJob({
    companyId,
    profileId: profile.id,
    workflowId: config.workflowId,
    lane: config.lane,
    dependencies: ["foundation"],
    priority: 82,
    payload: { source: "takyon_ui", lever }
  });

  await createEvent({
    businessId: companyId,
    actorProfileId: profile.id,
    kind: "takyon.lever_started",
    subjectType: "workflow_job",
    subjectId: job.id,
    payload: { lever, workflow_id: config.workflowId, lane: config.lane }
  });

  await createInboxMessage({
    companyId,
    profileId: profile.id,
    authorLabel: "Takyon",
    body: `${config.title} queued as a ${config.lane.replace(/_/g, " ")} lane.`,
    source: "system"
  });

  revalidatePath(`/dashboard/companies/${companyId}`);
}

export async function updateTakyonDistributionPolicyFromForm(companyId: string, formData: FormData) {
  const { profile } = await requireCompanyAccess(companyId);
  const enabled = formData.get("responseCheck") === "on";
  await setDistributionResponseCheckPolicy({ businessId: companyId, profileId: profile.id, enabled });
  await createInboxMessage({
    companyId,
    profileId: profile.id,
    authorLabel: "Takyon",
    body: enabled
      ? "Response-aware distribution is on for this business."
      : "Response-aware distribution is off for this business.",
    source: "system"
  });
  revalidatePath(`/dashboard/companies/${companyId}`);
}

export async function endTakyonCompanyFromForm(companyId: string, formData: FormData) {
  const { profile } = await requireCompanyAccess(companyId);
  const reason = String(formData.get("reason") || "").trim() || "Operator ended the generated app.";
  await endCompanyOperations({ companyId, profileId: profile.id, reason });
  revalidatePath("/dashboard/takyon");
  revalidatePath(`/dashboard/companies/${companyId}`);
}
