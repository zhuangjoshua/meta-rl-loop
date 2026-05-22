import { executeAiProvider } from "./ai-provider";
import { upsertBusinessCampaign, setBusinessCampaignStatus, type BusinessCampaignRow } from "./business-campaigns";
import { upsertBusinessMemory } from "./business-memory";
import { isBusinessTestMode } from "./companies";
import { createEvent } from "./events";
import { IntegrationCallError } from "./errors";
import { createInboxMessage } from "./inbox";
import { db } from "./db";
import { upsertBusinessDocument } from "./documents";
import { createTask } from "./tasks";
import { toJson } from "./json";
import { listToolCapabilities, type ToolCapability } from "./tool-availability";
import { enqueueWorkflowJob, type WorkflowJobRow, type WorkflowLane } from "./workflow-jobs";
import { loadCompanyFactorySkill } from "./workflows/skills";

export const GET_FIRST_CUSTOMER_GOAL = "get_first_customer";
export const GET_FIRST_CUSTOMER_WORKFLOW_ID = "goal_get_first_customer";

type GoalKey = typeof GET_FIRST_CUSTOMER_GOAL;

type GoalCommand =
  | { isGoalCommand: false }
  | {
      isGoalCommand: true;
      goalText: string;
      goalKey: GoalKey | null;
    };

type GoalActionConfig = {
  workflowId: string;
  lane: WorkflowLane;
  priority: number;
  dependencies: string[];
  capabilityKey?: string;
  capabilityAll?: string[];
  capabilityAny?: string[];
};

type GoalQueueResult =
  | { workflow_id: string; status: "queued"; jobId: string; reason: string }
  | { workflow_id: string; status: "already_active"; jobId: string; existingStatus: string; reason: string }
  | { workflow_id: string; status: "already_completed"; existingStatus: string; reason: string }
  | { workflow_id: string; status: "blocked"; reason: string; missing: string[]; setup: string[] };

type FirstCustomerState = {
  business: {
    id: string;
    name: string;
    slug: string;
    status: string;
    public_title: string | null;
    public_pitch: string | null;
    site_status: string | null;
    site_config: unknown;
  };
  counts: {
    deployment_count: number;
    product_build_count: number;
    payment_link_count: number;
    checkout_intent_count: number;
    checkout_session_count: number;
    generated_user_count: number;
    lead_count: number;
    community_target_count: number;
    social_post_count: number;
    outreach_event_count: number;
    revenue_cents: number;
    goal_tick_count: number;
  };
  recent: {
    revenue: unknown[];
    paymentLinks: unknown[];
    leads: unknown[];
    communityTargets: unknown[];
    socialPosts: unknown[];
    jobs: unknown[];
  };
};

type GoalStrategy = {
  provider: string;
  model: string;
  rawText?: string;
  output: Record<string, unknown>;
};

const goalActionCatalog: Record<string, GoalActionConfig> = {
  website_build_deploy: {
    workflowId: "website_build_deploy",
    lane: "website",
    priority: 94,
    dependencies: [],
    capabilityKey: "vercel"
  },
  product_backend: {
    workflowId: "product_backend",
    lane: "product_backend",
    priority: 76,
    dependencies: ["website_build_deploy"],
    capabilityKey: "anthropic"
  },
  product_ui: {
    workflowId: "product_ui",
    lane: "product_ui",
    priority: 75,
    dependencies: ["website_build_deploy", "product_backend"],
    capabilityKey: "anthropic"
  },
  generated_app_auth: {
    workflowId: "generated_app_auth",
    lane: "generated_app_auth",
    priority: 73,
    dependencies: []
  },
  generated_app_users_entitlements: {
    workflowId: "generated_app_users_entitlements",
    lane: "generated_app_users_entitlements",
    priority: 72,
    dependencies: []
  },
  stripe_setup: {
    workflowId: "stripe_setup",
    lane: "stripe",
    priority: 90,
    dependencies: [],
    capabilityKey: "stripe"
  },
  ai_gateway_setup: {
    workflowId: "ai_gateway_setup",
    lane: "ai_gateway",
    priority: 70,
    dependencies: []
  },
  community_research: {
    workflowId: "community_research",
    lane: "community",
    priority: 85,
    dependencies: [],
    capabilityKey: "tavily"
  },
  outreach_copy: {
    workflowId: "outreach_copy",
    lane: "outreach",
    priority: 84,
    dependencies: ["community_research"]
  },
  business_marketing_context: {
    workflowId: "business_marketing_context",
    lane: "outreach",
    priority: 88,
    dependencies: [],
    capabilityAny: ["anthropic", "openai"]
  },
  business_search_visibility: {
    workflowId: "business_search_visibility",
    lane: "website",
    priority: 77,
    dependencies: [],
    capabilityAny: ["anthropic", "openai"]
  },
  business_conversion_review: {
    workflowId: "business_conversion_review",
    lane: "website",
    priority: 76,
    dependencies: [],
    capabilityAny: ["anthropic", "openai"]
  },
  business_product_design: {
    workflowId: "business_product_design",
    lane: "website",
    priority: 89,
    dependencies: [],
    capabilityAny: ["anthropic", "openai"]
  },
  business_content_engine: {
    workflowId: "business_content_engine",
    lane: "outreach",
    priority: 75,
    dependencies: ["business_marketing_context"],
    capabilityAny: ["anthropic", "openai"]
  },
  business_outreach_pipeline: {
    workflowId: "business_outreach_pipeline",
    lane: "outreach",
    priority: 83,
    dependencies: ["community_research", "business_marketing_context"],
    capabilityAny: ["anthropic", "openai"]
  },
  business_paid_media_review: {
    workflowId: "business_paid_media_review",
    lane: "meta_seedance",
    priority: 70,
    dependencies: ["business_marketing_context"],
    capabilityAny: ["anthropic", "openai"]
  },
  business_measurement_plan: {
    workflowId: "business_measurement_plan",
    lane: "website",
    priority: 74,
    dependencies: [],
    capabilityAny: ["anthropic", "openai"]
  },
  x_social: {
    workflowId: "x_social",
    lane: "x_social",
    priority: 78,
    dependencies: [],
    capabilityKey: "x_posting"
  },
  ceo_wakeup: {
    workflowId: "ceo_wakeup",
    lane: "ceo",
    priority: 60,
    dependencies: []
  }
};

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value) ? (value as Record<string, unknown>) : {};
}

function stringField(record: Record<string, unknown>, key: string) {
  const value = record[key];
  return typeof value === "string" ? value.trim() : "";
}

function payloadString(value: unknown, key: string) {
  const record = asRecord(value);
  const text = record[key];
  return typeof text === "string" && text.trim() ? text.trim() : null;
}

function normalizeGoalText(goalText: string): GoalKey | null {
  const normalized = goalText.toLowerCase().replace(/[_-]+/g, " ").trim();
  if (!normalized) return GET_FIRST_CUSTOMER_GOAL;
  if (normalized === "get first customer") return GET_FIRST_CUSTOMER_GOAL;
  if (normalized === "get first paying customer") return GET_FIRST_CUSTOMER_GOAL;
  if (/\bfirst\b/.test(normalized) && /\b(customer|paying customer|sale|payment|revenue)\b/.test(normalized)) {
    return GET_FIRST_CUSTOMER_GOAL;
  }
  return null;
}

export function parseGoalCommand(body: string): GoalCommand {
  const trimmed = body.trim();
  const match = trimmed.match(/^\/goal(?:\s+([\s\S]+))?$/i);
  if (!match) return { isGoalCommand: false };
  const goalText = (match[1] || "").trim() || GET_FIRST_CUSTOMER_GOAL;
  return { isGoalCommand: true, goalText, goalKey: normalizeGoalText(goalText) };
}

function goalName(goalKey: GoalKey) {
  if (goalKey === GET_FIRST_CUSTOMER_GOAL) return "Get first paying customer";
  return goalKey;
}

async function ensureGoalCampaign(input: {
  businessId: string;
  profileId?: string | null;
  goalKey: GoalKey;
  operatorInstruction?: string | null;
}) {
  return upsertBusinessCampaign({
    businessId: input.businessId,
    profileId: input.profileId ?? null,
    slug: input.goalKey.replace(/_/g, "-"),
    name: goalName(input.goalKey),
    kind: "goal",
    status: "active",
    workspacePath: `goals/${input.goalKey.replace(/_/g, "-")}`,
    metadata: {
      source: "takyon_goal",
      goal_key: input.goalKey,
      success_condition: "verified positive Stripe-backed company_revenue_events row",
      operator_instruction: input.operatorInstruction ?? null
    }
  });
}

async function activeGoalJob(input: { businessId: string; workflowId: string; excludeJobId?: string | null }) {
  const sql = db();
  const rows = await sql<Pick<WorkflowJobRow, "id" | "status" | "run_after">[]>`
    SELECT id, status, run_after
    FROM workflow_jobs
    WHERE business_id = ${input.businessId}
      AND workflow_id = ${input.workflowId}
      AND (${input.excludeJobId ?? null}::uuid IS NULL OR id <> ${input.excludeJobId ?? null})
      AND status IN ('queued', 'running')
    ORDER BY created_at DESC
    LIMIT 1
  `;
  return rows[0] ?? null;
}

async function enqueueGoalTick(input: {
  businessId: string;
  profileId?: string | null;
  campaignId: string;
  operatorInstruction?: string | null;
  reason: string;
  runAfter?: Date;
  taskId?: string | null;
  excludeJobId?: string | null;
}) {
  const existing = await activeGoalJob({
    businessId: input.businessId,
    workflowId: GET_FIRST_CUSTOMER_WORKFLOW_ID,
    excludeJobId: input.excludeJobId ?? null
  });
  if (existing) {
    return {
      workflow_id: GET_FIRST_CUSTOMER_WORKFLOW_ID,
      status: "already_active" as const,
      jobId: existing.id,
      existingStatus: existing.status,
      reason: input.reason
    };
  }

  const job = await enqueueWorkflowJob({
    companyId: input.businessId,
    profileId: input.profileId ?? null,
    taskId: input.taskId ?? null,
    workflowId: GET_FIRST_CUSTOMER_WORKFLOW_ID,
    lane: "goal",
    priority: 120,
    maxAttempts: 2,
    runAfter: input.runAfter ?? new Date(),
    payload: {
      source: "takyon_goal",
      goal_key: GET_FIRST_CUSTOMER_GOAL,
      campaign_id: input.campaignId,
      operator_instruction: input.operatorInstruction ?? null,
      reason: input.reason
    }
  });

  return {
    workflow_id: GET_FIRST_CUSTOMER_WORKFLOW_ID,
    status: "queued" as const,
    jobId: job.id,
    reason: input.reason
  };
}

export async function startTakyonGoal(input: {
  companyId: string;
  profileId: string;
  goalText: string;
  operatorInstruction?: string | null;
  operatorMessageId?: string | null;
  source?: string;
}) {
  const goalKey = normalizeGoalText(input.goalText);
  if (!goalKey) {
    return {
      supported: false as const,
      goalText: input.goalText,
      reason: "Only /goal get_first_customer is supported right now."
    };
  }

  const campaign = await ensureGoalCampaign({
    businessId: input.companyId,
    profileId: input.profileId,
    goalKey,
    operatorInstruction: input.operatorInstruction ?? input.goalText
  });
  const existingTick = await activeGoalJob({
    businessId: input.companyId,
    workflowId: GET_FIRST_CUSTOMER_WORKFLOW_ID
  });
  const task = existingTick
    ? null
    : await createTask({
        companyId: input.companyId,
        profileId: input.profileId,
        title: goalName(goalKey),
        description: input.operatorInstruction ?? input.goalText,
        category: "goal",
        priority: 120
      });
  const tick = existingTick
    ? {
        workflow_id: GET_FIRST_CUSTOMER_WORKFLOW_ID,
        status: "already_active" as const,
        jobId: existingTick.id,
        existingStatus: existingTick.status,
        reason: "operator_started_goal"
      }
    : await enqueueGoalTick({
        businessId: input.companyId,
        profileId: input.profileId,
        campaignId: campaign.id,
        operatorInstruction: input.operatorInstruction ?? input.goalText,
        reason: "operator_started_goal",
        taskId: task?.id ?? null
      });

  await upsertBusinessMemory({
    businessId: input.companyId,
    profileId: input.profileId,
    campaignId: campaign.id,
    namespace: "goals",
    memoryKey: goalKey,
    title: goalName(goalKey),
    content: JSON.stringify(
      {
        goal_key: goalKey,
        goal_text: input.goalText,
        operator_instruction: input.operatorInstruction ?? null,
        operator_message_id: input.operatorMessageId ?? null,
        source: input.source ?? "takyon_chat",
        started_at: new Date().toISOString(),
        success_condition: "positive company_revenue_events receipt"
      },
      null,
      2
    ),
    evidence: [],
    metadata: { source: input.source ?? "takyon_chat", latest_goal_job_id: tick.jobId }
  });

  await createEvent({
    businessId: input.companyId,
    actorProfileId: input.profileId,
    kind: "takyon.goal_started",
    subjectType: "campaign",
    subjectId: campaign.id,
    payload: {
      goal_key: goalKey,
      operator_message_id: input.operatorMessageId ?? null,
      workflow_job_id: tick.jobId,
      status: tick.status
    }
  });

  return {
    supported: true as const,
    goalKey,
    campaign,
    task,
    tick
  };
}

function capabilityMap(capabilities: ToolCapability[]) {
  return new Map(capabilities.map((capability) => [capability.key, capability]));
}

function capabilityBlock(workflow: GoalActionConfig, capabilities: Map<string, ToolCapability>, options: { testMode?: boolean } = {}) {
  if (options.testMode && workflow.workflowId === "x_social") return null;
  const requiredAll = [...(workflow.capabilityAll ?? []), ...(workflow.capabilityKey ? [workflow.capabilityKey] : [])];
  const missingRequired = requiredAll.map((key) => capabilities.get(key)).filter((capability) => !capability?.canRun);
  const anyKeys = workflow.capabilityAny ?? [];
  const anySatisfied = anyKeys.length === 0 || anyKeys.some((key) => capabilities.get(key)?.canRun);
  const missingAny = anySatisfied ? [] : anyKeys.map((key) => capabilities.get(key)).filter(Boolean);
  const blocked = [...missingRequired, ...missingAny].filter(Boolean) as ToolCapability[];
  if (!blocked.length) return null;
  return {
    reason: `Required capability unavailable: ${blocked.map((capability) => capability.label).join(", ")}.`,
    missing: [...new Set(blocked.flatMap((capability) => capability.missing))],
    setup: [...new Set(blocked.flatMap((capability) => capability.setup))]
  };
}

async function enqueueGoalWorkflow(input: {
  businessId: string;
  profileId?: string | null;
  campaignId: string;
  workflowId: string;
  capabilities: Map<string, ToolCapability>;
  reason: string;
  strategy: Record<string, unknown>;
  repeatable?: boolean;
  testMode?: boolean;
}) {
  const workflow = goalActionCatalog[input.workflowId];
  if (!workflow) return null;
  const blocked = capabilityBlock(workflow, input.capabilities, { testMode: input.testMode });
  if (blocked) {
    return { workflow_id: workflow.workflowId, status: "blocked" as const, ...blocked, reason: `${input.reason}: ${blocked.reason}` };
  }

  const sql = db();
  const active = await sql<Pick<WorkflowJobRow, "id" | "status">[]>`
    SELECT id, status
    FROM workflow_jobs
    WHERE business_id = ${input.businessId}
      AND workflow_id = ${workflow.workflowId}
      AND status IN ('queued', 'running')
    ORDER BY created_at DESC
    LIMIT 1
  `;
  if (active[0]) {
    return {
      workflow_id: workflow.workflowId,
      status: "already_active" as const,
      jobId: active[0].id,
      existingStatus: active[0].status,
      reason: input.reason
    };
  }

  const latest = await sql<Pick<WorkflowJobRow, "status">[]>`
    SELECT status
    FROM workflow_jobs
    WHERE business_id = ${input.businessId}
      AND workflow_id = ${workflow.workflowId}
    ORDER BY created_at DESC
    LIMIT 1
  `;
  if (latest[0]?.status === "completed" && !input.repeatable) {
    return {
      workflow_id: workflow.workflowId,
      status: "already_completed" as const,
      existingStatus: latest[0].status,
      reason: input.reason
    };
  }

  const job = await enqueueWorkflowJob({
    companyId: input.businessId,
    profileId: input.profileId ?? null,
    workflowId: workflow.workflowId,
    lane: workflow.lane,
    priority: workflow.priority,
    dependencies: workflow.dependencies,
    payload: {
      source: "get_first_customer_goal",
      goal_key: GET_FIRST_CUSTOMER_GOAL,
      campaign_id: input.campaignId,
      reason: input.reason,
      strategy: input.strategy
    }
  });

  return { workflow_id: workflow.workflowId, status: "queued" as const, jobId: job.id, reason: input.reason };
}

async function loadFirstCustomerState(businessId: string): Promise<FirstCustomerState> {
  const sql = db();
  const [summaryRows, revenue, paymentLinks, leads, communityTargets, socialPosts, jobs] = await Promise.all([
    sql<{
      id: string;
      name: string;
      slug: string;
      status: string;
      public_title: string | null;
      public_pitch: string | null;
      site_status: string | null;
      site_config: unknown;
      deployment_count: number;
      product_build_count: number;
      payment_link_count: number;
      checkout_intent_count: number;
      checkout_session_count: number;
      generated_user_count: number;
      lead_count: number;
      community_target_count: number;
      social_post_count: number;
      outreach_event_count: number;
      revenue_cents: string;
      goal_tick_count: number;
    }[]>`
      SELECT
        b.id,
        b.name,
        b.slug,
        b.status,
        cs.public_title,
        cs.public_pitch,
        cs.status AS site_status,
        cs.config AS site_config,
        (SELECT count(*)::int FROM generated_app_deployments WHERE business_id = b.id AND status = 'completed') AS deployment_count,
        (SELECT count(*)::int FROM generated_app_builds WHERE business_id = b.id AND status = 'completed') AS product_build_count,
        (SELECT count(*)::int FROM company_payment_links WHERE business_id = b.id AND active = true) AS payment_link_count,
        (SELECT count(*)::int FROM company_checkout_intents WHERE business_id = b.id) AS checkout_intent_count,
        (SELECT count(*)::int FROM company_checkout_sessions WHERE business_id = b.id) AS checkout_session_count,
        (SELECT count(*)::int FROM generated_app_users WHERE business_id = b.id) AS generated_user_count,
        (SELECT count(*)::int FROM leads WHERE business_id = b.id) AS lead_count,
        (SELECT count(*)::int FROM community_targets WHERE business_id = b.id) AS community_target_count,
        (SELECT count(*)::int FROM business_social_posts WHERE business_id = b.id) AS social_post_count,
        (SELECT count(*)::int FROM cold_outreach_events WHERE business_id = b.id) AS outreach_event_count,
        COALESCE((SELECT SUM(amount_paid_cents) FROM company_revenue_events WHERE business_id = b.id AND status IN ('paid', 'succeeded', 'complete', 'completed')), 0)::text AS revenue_cents,
        (SELECT count(*)::int FROM workflow_jobs WHERE business_id = b.id AND workflow_id = ${GET_FIRST_CUSTOMER_WORKFLOW_ID} AND status = 'completed') AS goal_tick_count
      FROM businesses b
      LEFT JOIN company_sites cs ON cs.business_id = b.id
      WHERE b.id = ${businessId}
      LIMIT 1
    `,
    sql`
      SELECT revenue_type, status, currency, amount_paid_cents, customer_email, stripe_checkout_session_id, occurred_at
      FROM company_revenue_events
      WHERE business_id = ${businessId}
      ORDER BY occurred_at DESC
      LIMIT 8
    `,
    sql`
      SELECT plan_key, name, unit_amount_cents, currency, stripe_payment_link_url, active, created_at
      FROM company_payment_links
      WHERE business_id = ${businessId}
      ORDER BY created_at DESC
      LIMIT 5
    `,
    sql`
      SELECT id, email, name, url, source, status, last_event, last_contacted_at, created_at
      FROM leads
      WHERE business_id = ${businessId}
      ORDER BY created_at DESC
      LIMIT 25
    `,
    sql`
      SELECT id, source, title, url, match_reason, generated_copy, created_at
      FROM community_targets
      WHERE business_id = ${businessId}
      ORDER BY created_at DESC
      LIMIT 25
    `,
    sql`
      SELECT provider, status, text, provider_url, error, published_at, created_at
      FROM business_social_posts
      WHERE business_id = ${businessId}
      ORDER BY created_at DESC
      LIMIT 12
    `,
    sql`
      SELECT workflow_id, lane, status, error, result, created_at, updated_at
      FROM workflow_jobs
      WHERE business_id = ${businessId}
      ORDER BY created_at DESC
      LIMIT 24
    `
  ]);

  const summary = summaryRows[0];
  if (!summary) throw new Error("Business not found for goal.");

  return {
    business: {
      id: summary.id,
      name: summary.name,
      slug: summary.slug,
      status: summary.status,
      public_title: summary.public_title,
      public_pitch: summary.public_pitch,
      site_status: summary.site_status,
      site_config: summary.site_config
    },
    counts: {
      deployment_count: summary.deployment_count,
      product_build_count: summary.product_build_count,
      payment_link_count: summary.payment_link_count,
      checkout_intent_count: summary.checkout_intent_count,
      checkout_session_count: summary.checkout_session_count,
      generated_user_count: summary.generated_user_count,
      lead_count: summary.lead_count,
      community_target_count: summary.community_target_count,
      social_post_count: summary.social_post_count,
      outreach_event_count: summary.outreach_event_count,
      revenue_cents: Number(summary.revenue_cents) || 0,
      goal_tick_count: summary.goal_tick_count
    },
    recent: {
      revenue,
      paymentLinks,
      leads,
      communityTargets,
      socialPosts,
      jobs
    }
  };
}

function providerPolicy() {
  if (process.env.ANTHROPIC_API_KEY?.trim()) {
    return { provider: "anthropic", model: process.env.ARGON_GOAL_MODEL?.trim() || process.env.ARGON_CEO_MODEL?.trim() || "claude-opus-4-7" };
  }
  return { provider: "openai", model: process.env.ARGON_GOAL_MODEL?.trim() || process.env.ARGON_CEO_MODEL?.trim() || "gpt-5.2" };
}

function extractJson(text: string) {
  const fenced = text.match(/```(?:json)?\s*([\s\S]*?)```/i);
  const candidate = fenced?.[1] ?? text;
  const start = candidate.indexOf("{");
  const end = candidate.lastIndexOf("}");
  if (start === -1 || end === -1 || end <= start) return {};
  try {
    return asRecord(JSON.parse(candidate.slice(start, end + 1)));
  } catch {
    return {};
  }
}

async function runGoalStrategy(input: {
  state: FirstCustomerState;
  capabilities: ToolCapability[];
  operatorInstruction?: string | null;
}): Promise<GoalStrategy> {
  const skill = await loadCompanyFactorySkill("get-first-customer.md");
  const policy = providerPolicy();
  try {
    const response = await executeAiProvider({
      provider: policy.provider,
      model: policy.model,
      maxOutputTokens: 1200,
      messages: [
        {
          role: "system",
          content: [
            "Run this fixed Takyon company skill. Return strict JSON only.",
            "Do not invent people, emails, revenue, posts, payments, or vendor receipts.",
            "",
            skill
          ].join("\n")
        },
        {
          role: "user",
          content: JSON.stringify(
            {
              operator_instruction: input.operatorInstruction ?? null,
              state: input.state,
              capabilities: input.capabilities.map((capability) => ({
                key: capability.key,
                label: capability.label,
                canRun: capability.canRun,
                reason: capability.reason,
                missing: capability.missing
              }))
            },
            null,
            2
          )
        }
      ]
    });
    const output = extractJson(response.text);
    if (!Object.keys(output).length) {
      throw new IntegrationCallError("Takyon goal strategy", "model returned no JSON strategy");
    }
    return {
      provider: policy.provider,
      model: policy.model,
      rawText: response.text,
      output
    };
  } catch (error) {
    if (error instanceof IntegrationCallError) throw error;
    throw new IntegrationCallError("Takyon goal strategy", error instanceof Error ? error.message : String(error));
  }
}

function strategyActionIds(strategy: Record<string, unknown>) {
  const raw = strategy.next_actions;
  if (!Array.isArray(raw)) return [];
  return raw
    .map((item) => {
      const record = asRecord(item);
      return stringField(record, "workflow_id");
    })
    .filter((id) => Boolean(goalActionCatalog[id]));
}

function desiredActions(state: FirstCustomerState, strategy: Record<string, unknown>) {
  const desired: Array<{ workflowId: string; reason: string; repeatable?: boolean }> = [];
  const add = (workflowId: string, reason: string, repeatable = false) => {
    if (!goalActionCatalog[workflowId]) return;
    if (desired.some((item) => item.workflowId === workflowId)) return;
    desired.push({ workflowId, reason, repeatable });
  };

  const noProduct = state.counts.deployment_count === 0 || state.counts.product_build_count === 0;
  const noCheckout = state.counts.payment_link_count === 0;
  const noTargets = state.counts.lead_count < 10 && state.counts.community_target_count < 10;
  const noDistribution = state.counts.social_post_count === 0 && state.counts.outreach_event_count === 0;

  if (noProduct) {
    add("business_marketing_context", "goal_requires_clear_positioning");
    add("business_product_design", "goal_requires_web_and_app_design_brief");
    add("website_build_deploy", "goal_requires_live_offer");
    add("product_backend", "goal_requires_customer_workflow");
    add("product_ui", "goal_requires_customer_workflow");
    add("generated_app_auth", "goal_requires_customer_auth");
    add("generated_app_users_entitlements", "goal_requires_entitlements");
    add("ai_gateway_setup", "goal_requires_product_runtime");
    add("stripe_setup", "goal_requires_checkout");
    return desired;
  }

  if (noCheckout) {
    add("stripe_setup", "goal_requires_paid_checkout");
    add("business_conversion_review", "goal_requires_checkout_conversion_review", true);
    add("business_measurement_plan", "goal_requires_checkout_measurement_plan", true);
    return desired;
  }

  if (noTargets) {
    add("business_marketing_context", "goal_needs_icp_and_offer_context", true);
    add("community_research", "goal_needs_specific_targets", true);
    add("business_outreach_pipeline", "goal_needs_no_sending_outreach_plan", true);
    add("outreach_copy", "goal_needs_targeted_outreach", true);
    return desired;
  }

  if (noDistribution) {
    add("business_content_engine", "goal_needs_content_angles", true);
    add("business_outreach_pipeline", "goal_needs_first_outreach_plan", true);
    add("outreach_copy", "goal_needs_first_distribution", true);
    add("x_social", "goal_needs_public_launch_signal");
    return desired;
  }

  add("business_conversion_review", "goal_no_conversion_find_conversion_friction", true);
  add("business_product_design", "goal_no_conversion_refresh_web_and_app_design", true);
  add("business_search_visibility", "goal_no_conversion_improve_discoverability", true);
  add("business_measurement_plan", "goal_no_conversion_measure_next_attempt", true);
  add("website_build_deploy", "goal_no_conversion_refine_offer", true);
  add("community_research", "goal_no_conversion_find_sharper_targets", true);
  add("business_content_engine", "goal_no_conversion_refresh_content_angles", true);
  add("business_outreach_pipeline", "goal_no_conversion_refresh_outreach_pipeline", true);
  add("outreach_copy", "goal_no_conversion_rewrite_outreach", true);
  add("x_social", "goal_no_conversion_refresh_launch_post", true);

  for (const workflowId of strategyActionIds(strategy)) {
    add(workflowId, "goal_strategy_recommended_action", true);
  }

  return desired;
}

function hardBlockers(state: FirstCustomerState, capabilities: Map<string, ToolCapability>) {
  const blockers: string[] = [];
  const stripe = capabilities.get("stripe");
  const tavily = capabilities.get("tavily");
  const vercel = capabilities.get("vercel");
  const anthropic = capabilities.get("anthropic");
  const needsDeployment = state.counts.deployment_count === 0;
  const needsProductBuild = state.counts.product_build_count === 0;
  const noProduct = needsDeployment || needsProductBuild;
  const noCheckout = state.counts.payment_link_count === 0;
  const noTargets = state.counts.lead_count < 10 && state.counts.community_target_count < 10;

  if (noProduct) {
    if (needsProductBuild && !tavily?.canRun) blockers.push(tavily?.reason || "Tavily research is unavailable for market/customer research.");
    if (needsDeployment && !vercel?.canRun) blockers.push(vercel?.reason || "Vercel deployment is unavailable.");
    if (needsProductBuild && !anthropic?.canRun) blockers.push(anthropic?.reason || "Anthropic model calls are unavailable for the product/surface builder.");
    return blockers;
  }
  if (noCheckout && !stripe?.canRun) blockers.push(stripe?.reason || "Stripe checkout is unavailable.");
  if (!noCheckout && noTargets && !tavily?.canRun) blockers.push(tavily?.reason || "Tavily target research is unavailable.");
  return blockers;
}

function planDocument(input: {
  state: FirstCustomerState;
  strategy: GoalStrategy;
  queued: GoalQueueResult[];
  nextTickAt?: Date | null;
}) {
  const strategy = input.strategy.output;
  return [
    "# Get First Customer Goal",
    "",
    `Updated: ${new Date().toISOString()}`,
    "",
    "## Success Condition",
    "One verified positive Stripe-backed company_revenue_events row.",
    "",
    "## Current Blocker",
    stringField(strategy, "current_blocker") || "unknown",
    "",
    "## Target Customer",
    stringField(strategy, "target_customer") || input.state.business.public_pitch || input.state.business.name,
    "",
    "## Offer Revision",
    stringField(strategy, "offer_revision") || input.state.business.public_pitch || "No offer revision returned.",
    "",
    "## Channel Strategy",
    stringField(strategy, "channel_strategy") || "No channel strategy returned.",
    "",
    "## Evidence Counts",
    `- Revenue cents: ${input.state.counts.revenue_cents}`,
    `- Payment links: ${input.state.counts.payment_link_count}`,
    `- Leads: ${input.state.counts.lead_count}`,
    `- Community targets: ${input.state.counts.community_target_count}`,
    `- Social posts: ${input.state.counts.social_post_count}`,
    `- Outreach events: ${input.state.counts.outreach_event_count}`,
    "",
    "## Queued Or Blocked Actions",
    input.queued.length
      ? input.queued.map((item) => `- ${item.workflow_id}: ${item.status} - ${item.reason}`).join("\n")
      : "- No actions queued.",
    "",
    "## Next Tick",
    input.nextTickAt ? input.nextTickAt.toISOString() : "none"
  ].join("\n");
}

function goalTickMinutes() {
  const configured = Number(process.env.TAKYON_GOAL_TICK_MINUTES || "120");
  if (!Number.isFinite(configured)) return 120;
  return Math.max(15, Math.min(configured, 24 * 60));
}

async function saveGoalProgress(input: {
  businessId: string;
  profileId?: string | null;
  campaignId: string;
  state: FirstCustomerState;
  strategy: GoalStrategy;
  queued: GoalQueueResult[];
  nextTickAt?: Date | null;
  status: string;
  blockers?: string[];
}) {
  const content = planDocument({
    state: input.state,
    strategy: input.strategy,
    queued: input.queued,
    nextTickAt: input.nextTickAt ?? null
  });

  await Promise.all([
    upsertBusinessMemory({
      businessId: input.businessId,
      profileId: input.profileId ?? null,
      campaignId: input.campaignId,
      namespace: "goals",
      memoryKey: GET_FIRST_CUSTOMER_GOAL,
      title: "Get first paying customer",
      content: JSON.stringify(
        {
          status: input.status,
          state: input.state,
          strategy: input.strategy.output,
          queued: input.queued,
          blockers: input.blockers ?? [],
          next_tick_at: input.nextTickAt?.toISOString() ?? null
        },
        null,
        2
      ),
      evidence: [
        { kind: "revenue_cents", value: input.state.counts.revenue_cents },
        { kind: "lead_count", value: input.state.counts.lead_count },
        { kind: "community_target_count", value: input.state.counts.community_target_count }
      ],
      metadata: {
        provider: input.strategy.provider,
        model: input.strategy.model,
        status: input.status
      }
    }),
    upsertBusinessDocument({
      companyId: input.businessId,
      title: "Get First Customer Goal",
      kind: "task_report",
      source: "agent",
      content,
      metadata: {
        status: input.status,
        goal_key: GET_FIRST_CUSTOMER_GOAL,
        campaign_id: input.campaignId,
        provider: input.strategy.provider,
        model: input.strategy.model
      },
      replaceMetadata: true
    })
  ]);
}

async function goalCampaignFromPayload(input: {
  businessId: string;
  profileId?: string | null;
  payload: unknown;
}) {
  const campaignId = payloadString(input.payload, "campaign_id");
  if (campaignId) {
    const sql = db();
    const rows = await sql<BusinessCampaignRow[]>`
      SELECT id, business_id, slug, name, kind, status, workspace_path, budget_cap_microusd,
             metadata, created_by_profile_id, created_at, updated_at
      FROM business_campaigns
      WHERE business_id = ${input.businessId}
        AND id = ${campaignId}
      LIMIT 1
    `;
    if (rows[0]) return rows[0];
  }
  return ensureGoalCampaign({
    businessId: input.businessId,
    profileId: input.profileId ?? null,
    goalKey: GET_FIRST_CUSTOMER_GOAL,
    operatorInstruction: payloadString(input.payload, "operator_instruction")
  });
}

export async function runGetFirstCustomerGoal(input: {
  businessId: string;
  profileId?: string | null;
  payload?: unknown;
  workflowJobId?: string | null;
  taskId?: string | null;
}) {
  const campaign = await goalCampaignFromPayload({
    businessId: input.businessId,
    profileId: input.profileId ?? null,
    payload: input.payload
  });
  const state = await loadFirstCustomerState(input.businessId);
  const capabilities = await listToolCapabilities({ businessId: input.businessId, profileId: input.profileId ?? null });
  const caps = capabilityMap(capabilities);
  const testMode = await isBusinessTestMode(input.businessId);
  const operatorInstruction = payloadString(input.payload, "operator_instruction");
  const strategy = await runGoalStrategy({ state, capabilities, operatorInstruction });

  if (state.counts.revenue_cents > 0) {
    await setBusinessCampaignStatus({
      businessId: input.businessId,
      campaignIdOrSlug: campaign.id,
      status: "completed",
      profileId: input.profileId ?? null,
      reason: "First paying customer verified from company_revenue_events."
    });
    await saveGoalProgress({
      businessId: input.businessId,
      profileId: input.profileId ?? null,
      campaignId: campaign.id,
      state,
      strategy,
      queued: [],
      status: "won"
    });
    await createInboxMessage({
      companyId: input.businessId,
      profileId: input.profileId ?? null,
      authorLabel: "Takyon",
      body: `Goal won: ${goalName(GET_FIRST_CUSTOMER_GOAL)}. Verified revenue is now recorded at $${(state.counts.revenue_cents / 100).toFixed(0)}.`,
      source: "system"
    });
    await createEvent({
      businessId: input.businessId,
      actorProfileId: input.profileId ?? null,
      kind: "takyon.goal_won",
      subjectType: "campaign",
      subjectId: campaign.id,
      payload: { goal_key: GET_FIRST_CUSTOMER_GOAL, revenue_cents: state.counts.revenue_cents }
    });
    return { status: "completed" as const, goalStatus: "won", campaignId: campaign.id, revenueCents: state.counts.revenue_cents };
  }

  const blockers = hardBlockers(state, caps);
  if (blockers.length) {
    await saveGoalProgress({
      businessId: input.businessId,
      profileId: input.profileId ?? null,
      campaignId: campaign.id,
      state,
      strategy,
      queued: [],
      status: "blocked",
      blockers
    });
    await createEvent({
      businessId: input.businessId,
      actorProfileId: input.profileId ?? null,
      kind: "takyon.goal_blocked",
      subjectType: "campaign",
      subjectId: campaign.id,
      payload: { goal_key: GET_FIRST_CUSTOMER_GOAL, blockers }
    });
    return { status: "blocked" as const, goalStatus: "blocked", campaignId: campaign.id, blockers };
  }

  const queued: GoalQueueResult[] = [];
  for (const action of desiredActions(state, strategy.output)) {
    const result = await enqueueGoalWorkflow({
      businessId: input.businessId,
      profileId: input.profileId ?? null,
      campaignId: campaign.id,
      workflowId: action.workflowId,
      capabilities: caps,
      reason: action.reason,
      strategy: strategy.output,
      repeatable: action.repeatable,
      testMode
    });
    if (result) queued.push(result);
  }

  const nextTickAt = new Date(Date.now() + goalTickMinutes() * 60_000);
  const nextTick = await enqueueGoalTick({
    businessId: input.businessId,
    profileId: input.profileId ?? null,
    campaignId: campaign.id,
    operatorInstruction,
    reason: "continue_until_first_customer",
    runAfter: nextTickAt,
    excludeJobId: input.workflowJobId ?? null,
    taskId: input.taskId ?? null
  });

  await saveGoalProgress({
    businessId: input.businessId,
    profileId: input.profileId ?? null,
    campaignId: campaign.id,
    state,
    strategy,
    queued,
    nextTickAt,
    status: "active"
  });

  await createEvent({
    businessId: input.businessId,
    actorProfileId: input.profileId ?? null,
    kind: "takyon.goal_tick_completed",
    subjectType: "campaign",
    subjectId: campaign.id,
    payload: {
      goal_key: GET_FIRST_CUSTOMER_GOAL,
      queued,
      next_goal_job: nextTick,
      strategy: toJson(strategy.output)
    }
  });

  return {
    status: "completed" as const,
    goalStatus: "active",
    campaignId: campaign.id,
    queued,
    nextTick,
    nextTickAt: nextTickAt.toISOString(),
    strategy: strategy.output
  };
}
