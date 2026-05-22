import { upsertBusinessMemory } from "./business-memory";
import { createEvent } from "./events";
import { toJson } from "./json";

type WorkflowLearningInput = {
  businessId: string;
  profileId?: string | null;
  workflowJobId: string;
  workflowId: string;
  lane: string;
  status: "completed" | "blocked" | "failed" | "cancelled";
  attempts?: number;
  error?: string | null;
  result?: unknown;
};

function shortResult(value: unknown) {
  const json = JSON.stringify(toJson(value ?? {}), null, 2);
  return json.length > 6000 ? `${json.slice(0, 6000)}\n...truncated` : json;
}

function namespaceForStatus(status: WorkflowLearningInput["status"]) {
  if (status === "completed") return "operations";
  if (status === "blocked" || status === "failed") return "operations";
  return "control";
}

function titleFor(input: WorkflowLearningInput) {
  if (input.status === "completed") return `Workflow completed: ${input.workflowId}`;
  if (input.status === "blocked") return `Workflow blocked: ${input.workflowId}`;
  if (input.status === "failed") return `Workflow failed: ${input.workflowId}`;
  return `Workflow cancelled: ${input.workflowId}`;
}

export async function recordWorkflowOutcomeMemory(input: WorkflowLearningInput) {
  const evidence = [
    {
      kind: "workflow_job",
      workflow_job_id: input.workflowJobId,
      workflow_id: input.workflowId,
      lane: input.lane,
      status: input.status,
      attempts: input.attempts ?? null,
      error: input.error ?? null
    }
  ];

  const content = [
    `status: ${input.status}`,
    `workflow: ${input.workflowId}`,
    `lane: ${input.lane}`,
    `attempts: ${input.attempts ?? "unknown"}`,
    input.error ? `error: ${input.error}` : "error: none",
    "",
    "result:",
    shortResult(input.result)
  ].join("\n");

  const namespace = namespaceForStatus(input.status);
  await upsertBusinessMemory({
    businessId: input.businessId,
    profileId: input.profileId ?? null,
    namespace,
    memoryKey: `workflow-${input.workflowId}-latest-${input.status}`,
    title: titleFor(input),
    content,
    evidence,
    metadata: { source: "local_worker", workflow_job_id: input.workflowJobId }
  });

  if (input.status === "blocked" || input.status === "failed") {
    await upsertBusinessMemory({
      businessId: input.businessId,
      profileId: input.profileId ?? null,
      namespace: "strategy",
      memoryKey: "next-recovery-focus",
      title: "Next recovery focus",
      content: [
        `${input.workflowId} is currently ${input.status}.`,
        input.error ? `Reason: ${input.error}` : "Reason: no error detail recorded.",
        "Do not treat the business as complete until this lane is repaired, skipped with evidence, or replaced by an equivalent deterministic path."
      ].join("\n"),
      evidence,
      metadata: { source: "local_worker", workflow_job_id: input.workflowJobId }
    });
  }

  await createEvent({
    businessId: input.businessId,
    actorProfileId: input.profileId ?? null,
    kind: "business_learning.workflow_outcome_recorded",
    subjectType: "workflow_job",
    subjectId: input.workflowJobId,
    payload: { workflow_id: input.workflowId, lane: input.lane, status: input.status }
  });
}
