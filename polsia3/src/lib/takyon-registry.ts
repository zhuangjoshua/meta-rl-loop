import type { WorkflowLane } from "./workflow-jobs";

export type TakyonStage =
  | "product_context"
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
  autoBuild?: boolean;
  maxAttempts?: number;
  description: string;
};

export const takyonWorkflowRegistry: TakyonWorkflowSpec[] = [
  {
    workflowId: "website_build_deploy",
    lane: "website",
    priority: 95,
    dependencies: [],
    stages: ["product_context", "conversion", "recovery"],
    capabilityAll: ["vercel", "claude_agent_sdk"],
    repeatable: true,
    retryOnFailure: true,
    dispatchable: true,
    description: "Build and deploy a customer-facing website/product entry surface."
  },
  {
    workflowId: "product_backend",
    lane: "product_backend",
    priority: 75,
    dependencies: ["website_build_deploy"],
    stages: ["product_context", "recovery"],
    capabilityAll: ["anthropic"],
    retryOnFailure: true,
    dispatchable: true,
    description: "Specialize the generated app backend while keeping platform rails deterministic."
  },
  {
    workflowId: "product_ui",
    lane: "product_ui",
    priority: 74,
    dependencies: ["website_build_deploy", "product_backend"],
    stages: ["product_context", "conversion", "recovery"],
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
    dependencies: [],
    stages: ["product_context"],
    dispatchable: true,
    description: "Ensure generated-app auth routes are available."
  },
  {
    workflowId: "generated_app_users_entitlements",
    lane: "generated_app_users_entitlements",
    priority: 71,
    dependencies: [],
    stages: ["product_context"],
    dispatchable: true,
    description: "Ensure generated-app users, plans, entitlements, wallet, and project AI key."
  },
  {
    workflowId: "stripe_setup",
    lane: "stripe",
    priority: 70,
    dependencies: [],
    stages: ["product_context", "checkout", "conversion", "recovery"],
    capabilityAll: ["stripe"],
    retryOnFailure: true,
    dispatchable: true,
    description: "Create or verify Stripe checkout/payment link rails."
  },
  {
    workflowId: "ai_gateway_setup",
    lane: "ai_gateway",
    priority: 69,
    dependencies: [],
    stages: ["product_context"],
    dispatchable: true,
    description: "Ensure generated-app AI gateway policy and proxy-key rails."
  },
  {
    workflowId: "x_social",
    lane: "x_social",
    priority: 66,
    dependencies: [],
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
    dependencies: [],
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
    dependencies: [],
    stages: ["distribution", "learning"],
    capabilityAll: ["tavily"],
    repeatable: true,
    dispatchable: true,
    description: "Research current communities, channels, customer language, and distribution angles."
  },
  {
    workflowId: "conversation_watch",
    lane: "community",
    priority: 57,
    dependencies: [],
    stages: ["distribution", "learning"],
    repeatable: true,
    dispatchable: true,
    autoBuild: false,
    maxAttempts: 1,
    description: "Refresh per-business watched conversation threads and normalize inbound replies before more outward distribution."
  },
  {
    workflowId: "outreach_copy",
    lane: "outreach",
    priority: 63,
    dependencies: ["community_research"],
    stages: ["distribution", "conversion"],
    repeatable: true,
    dispatchable: true,
    description: "Write outbound/community copy grounded in current research."
  },
  {
    workflowId: "business_marketing_context",
    lane: "outreach",
    priority: 68,
    dependencies: [],
    stages: ["distribution", "conversion", "learning"],
    capabilityAny: ["anthropic", "openai"],
    repeatable: true,
    dispatchable: true,
    autoBuild: false,
    description: "Create a Takyon-owned shared marketing context from explicit business evidence."
  },
  {
    workflowId: "business_search_visibility",
    lane: "website",
    priority: 67,
    dependencies: [],
    stages: ["distribution", "conversion", "learning"],
    capabilityAny: ["anthropic", "openai"],
    repeatable: true,
    dispatchable: true,
    autoBuild: false,
    description: "Create an inspectable SEO/GEO visibility scorecard and backlog without publishing changes."
  },
  {
    workflowId: "business_conversion_review",
    lane: "website",
    priority: 62,
    dependencies: [],
    stages: ["conversion", "learning", "recovery"],
    capabilityAny: ["anthropic", "openai"],
    repeatable: true,
    dispatchable: true,
    autoBuild: false,
    description: "Review the visible funnel and write a CRO experiment backlog."
  },
  {
    workflowId: "business_product_design",
    lane: "website",
    priority: 89,
    dependencies: [],
    stages: ["product_context", "conversion", "learning"],
    capabilityAny: ["anthropic", "openai"],
    repeatable: true,
    dispatchable: true,
    autoBuild: false,
    description: "Create an Open Design-inspired webpage and app design brief without running external design daemons."
  },
  {
    workflowId: "business_content_engine",
    lane: "outreach",
    priority: 60,
    dependencies: ["business_marketing_context"],
    stages: ["distribution", "conversion"],
    capabilityAny: ["anthropic", "openai"],
    repeatable: true,
    dispatchable: true,
    autoBuild: false,
    description: "Create content pillars, page briefs, social angles, and draft copy from business context."
  },
  {
    workflowId: "business_outreach_pipeline",
    lane: "outreach",
    priority: 59,
    dependencies: ["community_research", "business_marketing_context"],
    stages: ["distribution", "conversion"],
    capabilityAny: ["anthropic", "openai"],
    repeatable: true,
    dispatchable: true,
    autoBuild: false,
    description: "Create a no-sending outbound and sales pipeline plan from visible leads and targets."
  },
  {
    workflowId: "business_paid_media_review",
    lane: "meta_seedance",
    priority: 58,
    dependencies: ["business_marketing_context"],
    stages: ["distribution", "conversion", "learning"],
    capabilityAny: ["anthropic", "openai"],
    repeatable: true,
    dispatchable: true,
    autoBuild: false,
    description: "Review paid-media readiness and draft planning-only creative/ad recommendations without Meta mutation."
  },
  {
    workflowId: "business_measurement_plan",
    lane: "website",
    priority: 57,
    dependencies: [],
    stages: ["conversion", "learning"],
    capabilityAny: ["anthropic", "openai"],
    repeatable: true,
    dispatchable: true,
    autoBuild: false,
    description: "Create an analytics, attribution, experiment, and Pixel/CAPI audit plan without sending events."
  },
  {
    workflowId: "customer_ops_watch",
    lane: "ceo",
    priority: 56,
    dependencies: [],
    stages: ["conversion", "learning", "recovery"],
    repeatable: true,
    dispatchable: true,
    autoBuild: false,
    maxAttempts: 1,
    description: "Refresh generated-app customer, entitlement, subscription, usage, and revenue state for CEO inspection."
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
