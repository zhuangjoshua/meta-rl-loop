import type { WorkflowLane } from "./workflow-jobs";

export type TakyonStage =
  | "product_foundation"
  | "distribution"
  | "conversion"
  | "checkout"
  | "recovery"
  | "learning";

export type TakyonWorkflowSpec = {
  workflowId: string;
  lane: WorkflowLane;
  priority: number;
  dependencies: string[];
  stages: TakyonStage[];
  capabilityAll?: string[];
  capabilityAny?: string[];
  repeatable?: boolean;
  retryOnFailure?: boolean;
  dispatchable?: boolean;
  maxAttempts?: number;
  description: string;
};

export const takyonWorkflowRegistry: TakyonWorkflowSpec[] = [
  {
    workflowId: "foundation",
    lane: "foundation",
    priority: 100,
    dependencies: [],
    stages: ["product_foundation", "recovery"],
    capabilityAll: ["tavily"],
    capabilityAny: ["anthropic", "openai"],
    retryOnFailure: true,
    dispatchable: true,
    description: "Research, positioning, mission, offer, and first product wedge."
  },
  {
    workflowId: "website_build_deploy",
    lane: "website",
    priority: 95,
    dependencies: ["foundation"],
    stages: ["product_foundation", "conversion", "recovery"],
    capabilityAll: ["vercel"],
    repeatable: true,
    retryOnFailure: true,
    dispatchable: true,
    description: "Build and deploy a customer-facing website/product entry surface."
  },
  {
    workflowId: "product_backend",
    lane: "product_backend",
    priority: 75,
    dependencies: ["foundation", "website_build_deploy"],
    stages: ["product_foundation", "recovery"],
    capabilityAll: ["anthropic"],
    retryOnFailure: true,
    dispatchable: true,
    description: "Specialize the generated app backend while keeping platform rails deterministic."
  },
  {
    workflowId: "product_ui",
    lane: "product_ui",
    priority: 74,
    dependencies: ["foundation", "website_build_deploy", "product_backend"],
    stages: ["product_foundation", "conversion", "recovery"],
    capabilityAll: ["anthropic"],
    repeatable: true,
    retryOnFailure: true,
    dispatchable: true,
    description: "Specialize the generated app product workflow UI."
  },
  {
    workflowId: "generated_app_auth",
    lane: "generated_app_auth",
    priority: 72,
    dependencies: ["foundation"],
    stages: ["product_foundation"],
    dispatchable: true,
    description: "Ensure generated-app auth routes are available."
  },
  {
    workflowId: "generated_app_users_entitlements",
    lane: "generated_app_users_entitlements",
    priority: 71,
    dependencies: ["foundation"],
    stages: ["product_foundation"],
    dispatchable: true,
    description: "Ensure generated-app users, plans, entitlements, wallet, and project AI key."
  },
  {
    workflowId: "stripe_setup",
    lane: "stripe",
    priority: 70,
    dependencies: ["foundation"],
    stages: ["product_foundation", "checkout", "conversion", "recovery"],
    capabilityAll: ["stripe"],
    retryOnFailure: true,
    dispatchable: true,
    description: "Create or verify Stripe checkout/payment link rails."
  },
  {
    workflowId: "ai_gateway_setup",
    lane: "ai_gateway",
    priority: 69,
    dependencies: ["foundation"],
    stages: ["product_foundation"],
    dispatchable: true,
    description: "Ensure generated-app AI gateway policy and proxy-key rails."
  },
  {
    workflowId: "x_social",
    lane: "x_social",
    priority: 66,
    dependencies: ["foundation"],
    stages: ["distribution", "recovery"],
    capabilityAll: ["x_posting"],
    repeatable: true,
    retryOnFailure: true,
    dispatchable: true,
    description: "Draft and publish an X post when OAuth is configured."
  },
  {
    workflowId: "meta_seedance",
    lane: "meta_seedance",
    priority: 65,
    dependencies: ["foundation"],
    stages: ["distribution", "recovery"],
    capabilityAll: ["openai"],
    repeatable: true,
    retryOnFailure: true,
    dispatchable: true,
    description: "Create display-only Sora/creative assets. Meta spend is gated separately."
  },
  {
    workflowId: "community_research",
    lane: "community",
    priority: 64,
    dependencies: ["foundation"],
    stages: ["distribution", "learning"],
    capabilityAll: ["tavily"],
    repeatable: true,
    dispatchable: true,
    description: "Research current communities, channels, customer language, and distribution angles."
  },
  {
    workflowId: "outreach_copy",
    lane: "outreach",
    priority: 63,
    dependencies: ["foundation", "community_research"],
    stages: ["distribution", "conversion"],
    repeatable: true,
    dispatchable: true,
    description: "Write outbound/community copy grounded in current research."
  },
  {
    workflowId: "ceo_wakeup",
    lane: "ceo",
    priority: 110,
    dependencies: [],
    stages: ["learning", "recovery"],
    capabilityAll: ["takyon_runtime"],
    repeatable: true,
    dispatchable: true,
    maxAttempts: 1,
    description: "CEO review, priorities, blockers, and next-action report."
  },
  {
    workflowId: "observe_campaign_results",
    lane: "ceo",
    priority: 40,
    dependencies: [],
    stages: ["learning"],
    repeatable: true,
    dispatchable: true,
    maxAttempts: 1,
    description: "Observe campaign/customer evidence and write learning records."
  },
  {
    workflowId: "goal_get_first_customer",
    lane: "goal",
    priority: 90,
    dependencies: [],
    stages: ["distribution", "conversion", "learning"],
    repeatable: true,
    dispatchable: true,
    description: "Goal loop for finding and converting the first customer."
  }
];

export function getTakyonWorkflowSpec(workflowId: string) {
  return takyonWorkflowRegistry.find((workflow) => workflow.workflowId === workflowId) ?? null;
}

export function takyonWorkflowsForStage(stage: TakyonStage) {
  return takyonWorkflowRegistry.filter((workflow) => workflow.stages.includes(stage));
}

export function takyonDispatchableWorkflowIds() {
  return takyonWorkflowRegistry.filter((workflow) => workflow.dispatchable).map((workflow) => workflow.workflowId);
}

export function takyonBuildCompanyLanes() {
  return takyonWorkflowRegistry
    .filter((workflow) => workflow.dispatchable && workflow.lane !== "ceo" && workflow.lane !== "goal")
    .map((workflow) => ({
      workflowId: workflow.workflowId,
      lane: workflow.lane,
      priority: workflow.priority,
      dependencies: workflow.dependencies
    }));
}

export function takyonLaneByWorkflow() {
  return Object.fromEntries(takyonWorkflowRegistry.map((workflow) => [workflow.workflowId, workflow.lane])) as Record<string, WorkflowLane>;
}

export function takyonCapabilityGroups(workflowId: string) {
  const workflow = getTakyonWorkflowSpec(workflowId);
  if (!workflow) return [];
  const groups: string[][] = [];
  for (const key of workflow.capabilityAll ?? []) groups.push([key]);
  if (workflow.capabilityAny?.length) groups.push(workflow.capabilityAny);
  return groups;
}
