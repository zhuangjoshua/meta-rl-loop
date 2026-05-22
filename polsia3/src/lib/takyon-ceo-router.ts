import { executeAiProvider } from "./ai-provider";
import { listBusinessMemory } from "./business-memory";
import { db } from "./db";
import { upsertBusinessDocument } from "./documents";
import { getLlmsEnv } from "./env";
import { createEvent } from "./events";
import { parseGoalCommand, startTakyonGoal } from "./goals";
import { createInboxMessage } from "./inbox";
import { toJson } from "./json";
import { createTask } from "./tasks";
import { takyonCapabilityGroups } from "./takyon-registry";
import { preflightCapabilityGroups } from "./tool-availability";
import { enqueueWorkflowJob, type WorkflowLane } from "./workflow-jobs";

type ChatAction = {
  workflowId: string;
  lane: WorkflowLane;
  title: string;
  priority: number;
  dependencies: string[];
};

const actionCatalog = {
  website: {
    workflowId: "website_build_deploy",
    lane: "website",
    title: "Improve website",
    priority: 88,
    dependencies: ["foundation"]
  },
  x: {
    workflowId: "x_social",
    lane: "x_social",
    title: "Publish X post",
    priority: 84,
    dependencies: ["foundation"]
  },
  sora: {
    workflowId: "meta_seedance",
    lane: "meta_seedance",
    title: "Generate Sora creative",
    priority: 83,
    dependencies: ["foundation"]
  },
  community: {
    workflowId: "community_research",
    lane: "community",
    title: "Refresh community targets",
    priority: 82,
    dependencies: ["foundation"]
  },
  outreach: {
    workflowId: "outreach_copy",
    lane: "outreach",
    title: "Refresh leads and outreach",
    priority: 81,
    dependencies: ["foundation"]
  },
  ceo: {
    workflowId: "ceo_wakeup",
    lane: "ceo",
    title: "Create CEO digest",
    priority: 80,
    dependencies: []
  }
} satisfies Record<string, ChatAction>;

function hasAny(text: string, patterns: RegExp[]) {
  return patterns.some((pattern) => pattern.test(text));
}

function isOutreachCampaignDescriptionRequest(body: string) {
  const text = body.toLowerCase();
  const asksForDescription = hasAny(text, [
    /\b(describe|summari[sz]e|explain|outline|review)\b/,
    /\bwhat(?:'s| is| are)?\b/,
    /\btell me about\b/,
    /\bcurrent\b.*\b(campaign|outreach)\b/
  ]);
  const aboutOutreachCampaign = hasAny(text, [/\boutreach\b/, /\bcampaign\b/]);
  const asksForAction = hasAny(text, [/\b(find|refresh|create|write|draft|email|post|publish|run|queue|generate|make|send)\b/]);

  return asksForDescription && aboutOutreachCampaign && !asksForAction;
}

function requestedActions(body: string) {
  const text = body.toLowerCase();
  const describesOutreachCampaign = isOutreachCampaignDescriptionRequest(body);
  const actions: ChatAction[] = [];
  if (hasAny(text, [/\b(edit|improve|change|update|rewrite|fix)\b.*\b(site|website|homepage|landing page)\b/, /\bwebsite\b.*\b(edit|improve|change|update|rewrite|fix)\b/])) {
    actions.push(actionCatalog.website);
  }
  if (hasAny(text, [/\b(x|twitter|tweet|post)\b/, /\bpublish\b.*\bpost\b/])) actions.push(actionCatalog.x);
  if (hasAny(text, [/\b(sora|ugc|ad creative|video|creative)\b/, /\bads?\b.*\b(generate|make|create|call|run)\b/])) {
    actions.push(actionCatalog.sora);
  }
  if (!describesOutreachCampaign && hasAny(text, [/\b(community|reddit|indie hackers|forums?|targets?)\b/])) actions.push(actionCatalog.community);
  if (!describesOutreachCampaign && hasAny(text, [/\b(leads?|outreach|prospects?|email copy|sales)\b/])) {
    actions.push(actionCatalog.community, actionCatalog.outreach);
  }
  if (hasAny(text, [/\b(daily digest|digest|daily report|ceo report|status report)\b/])) actions.push(actionCatalog.ceo);

  const seen = new Set<string>();
  return actions.filter((action) => {
    if (seen.has(action.workflowId)) return false;
    seen.add(action.workflowId);
    return true;
  });
}

function providerPolicy() {
  getLlmsEnv();
  if (process.env.ANTHROPIC_API_KEY?.trim()) {
    return { provider: "anthropic", model: process.env.ARGON_CEO_MODEL?.trim() || "claude-opus-4-7" };
  }
  return { provider: "openai", model: process.env.ARGON_CEO_MODEL?.trim() || "gpt-5.2" };
}

function compactRows(rows: unknown[]) {
  return JSON.stringify(toJson(rows), null, 2).slice(0, 6000);
}

async function loadCeoContext(companyId: string) {
  const sql = db();
  const [company, documents, jobs, tasks, events, social, community, leads, media, deployments, cron, memory] = await Promise.all([
    sql`
      SELECT b.id, b.name, b.status, b.slug, cs.public_title, cs.public_pitch, cs.status AS site_status
      FROM businesses b
      LEFT JOIN company_sites cs ON cs.business_id = b.id
      WHERE b.id = ${companyId}
      LIMIT 1
    `,
    sql`
      SELECT title, kind, source, content, updated_at
      FROM business_documents
      WHERE business_id = ${companyId}
      ORDER BY updated_at DESC
      LIMIT 6
    `,
    sql`
      SELECT workflow_id, lane, status, error, result, created_at, updated_at
      FROM workflow_jobs
      WHERE business_id = ${companyId}
      ORDER BY created_at DESC
      LIMIT 16
    `,
    sql`
      SELECT title, category, status, description, updated_at
      FROM tasks
      WHERE business_id = ${companyId}
      ORDER BY created_at DESC
      LIMIT 12
    `,
    sql`
      SELECT kind, subject_type, payload, created_at
      FROM events
      WHERE business_id = ${companyId}
      ORDER BY created_at DESC
      LIMIT 10
    `,
    sql`
      SELECT provider, status, text, provider_url, error, published_at, created_at
      FROM business_social_posts
      WHERE business_id = ${companyId}
      ORDER BY created_at DESC
      LIMIT 12
    `,
    sql`
      SELECT source, title, url, match_reason, generated_copy, created_at
      FROM community_targets
      WHERE business_id = ${companyId}
      ORDER BY created_at DESC
      LIMIT 12
    `,
    sql`
      SELECT name, email, source, status, created_at
      FROM leads
      WHERE business_id = ${companyId}
      ORDER BY created_at DESC
      LIMIT 12
    `,
    sql`
      SELECT provider, model, status, output_url, error, prompt, created_at
      FROM media_generation_jobs
      WHERE business_id = ${companyId}
      ORDER BY created_at DESC
      LIMIT 8
    `,
    sql`
      SELECT status, deployment_url, alias_url, error, created_at
      FROM generated_app_deployments
      WHERE business_id = ${companyId}
      ORDER BY created_at DESC
      LIMIT 6
    `,
    sql`
      SELECT job_key, status, schedule_type, interval_seconds, daily_time_utc, next_run_at, last_error
      FROM cron_jobs
      ORDER BY job_key
    `,
    listBusinessMemory({ businessId: companyId, limit: 16 })
  ]);

  return { company, documents, jobs, tasks, events, social, community, leads, media, deployments, cron, memory };
}

function contextText(context: Awaited<ReturnType<typeof loadCeoContext>>) {
  return [
    "Background execution model:",
    "Cron only schedules and reconciles work. Workflow jobs execute when an active background runner claims them. Do not mention local machine details in operator-facing chat unless the operator explicitly asks about runtime architecture.",
    "",
    "Company:",
    compactRows(context.company),
    "",
    "Documents:",
    compactRows(
      context.documents.map((document) => ({
        ...document,
        content: typeof document.content === "string" ? document.content.slice(0, 1800) : document.content
      }))
    ),
    "",
    "Workflow jobs:",
    compactRows(context.jobs),
    "",
    "Tasks:",
    compactRows(context.tasks),
    "",
    "Events:",
    compactRows(context.events),
    "",
    "Social posts:",
    compactRows(context.social),
    "",
    "Community targets:",
    compactRows(context.community),
    "",
    "Leads:",
    compactRows(context.leads),
    "",
    "Business memory:",
    compactRows(
      context.memory
        .filter((memory) => ["campaign_learning", "customer_learning", "autopilot"].includes(memory.namespace))
        .map((memory) => ({
          namespace: memory.namespace,
          title: memory.title,
          content: memory.content.slice(0, 1800),
          confidence: memory.confidence,
          updated_at: memory.updated_at
        }))
    ),
    "",
    "Media jobs:",
    compactRows(context.media),
    "",
    "Generated deployments:",
    compactRows(context.deployments),
    "",
    "Cron:",
    compactRows(context.cron)
  ].join("\n");
}

async function enqueueActions(input: {
  companyId: string;
  profileId: string;
  body: string;
  actions: ChatAction[];
  operatorMessageId: string;
}) {
  const task =
    input.actions.length > 0
      ? await createTask({
          companyId: input.companyId,
          profileId: input.profileId,
          title: "Run CEO request",
          description: input.body,
          category: "ceo_action",
          priority: 82
        })
      : null;
  const jobs = [];
  for (const action of input.actions) {
    const block = await preflightCapabilityGroups({
      workflowId: action.workflowId,
      groups: takyonCapabilityGroups(action.workflowId),
      businessId: input.companyId,
      profileId: input.profileId
    });
    if (block) {
      await createEvent({
        businessId: input.companyId,
        actorProfileId: input.profileId,
        kind: "tool.capability_blocked",
        subjectType: "business_inbox_message",
        subjectId: input.operatorMessageId,
        payload: block
      });
      jobs.push({
        title: action.title,
        workflowId: action.workflowId,
        lane: action.lane,
        status: "blocked",
        error: block.error,
        missing: block.missing,
        setup: block.setup,
        reports: block.reports
      });
      continue;
    }
    const job = await enqueueWorkflowJob({
      companyId: input.companyId,
      profileId: input.profileId,
      taskId: task?.id ?? null,
      workflowId: action.workflowId,
      lane: action.lane,
      dependencies: action.dependencies,
      priority: action.priority,
      payload: {
        source: "takyon_chat",
        operator_instruction: input.body,
        operator_message_id: input.operatorMessageId
      }
    });
    jobs.push({ title: action.title, workflowId: action.workflowId, lane: action.lane, status: "queued", jobId: job.id });
  }
  return jobs;
}

export async function handleTakyonCeoChat(input: {
  companyId: string;
  profileId: string;
  authorLabel: string;
  body: string;
}) {
  const operatorMessage = await createInboxMessage({
    companyId: input.companyId,
    profileId: input.profileId,
    authorLabel: input.authorLabel,
    body: input.body,
    source: "dashboard"
  });

  const goalCommand = parseGoalCommand(input.body);
  if (goalCommand.isGoalCommand) {
    if (!goalCommand.goalKey) {
      const ceoMessage = await createInboxMessage({
        companyId: input.companyId,
        authorLabel: "CEO",
        body: "I can run persistent goals, but the only wired goal right now is /goal get_first_customer.",
        source: "ceo_chat"
      });
      await createEvent({
        businessId: input.companyId,
        actorProfileId: input.profileId,
        kind: "takyon.goal_unsupported",
        subjectType: "business_inbox_message",
        subjectId: ceoMessage.id,
        payload: { requested_goal: goalCommand.goalText, operator_message_id: operatorMessage.id }
      });
      return { message: ceoMessage, queued: [] };
    }

    const started = await startTakyonGoal({
      companyId: input.companyId,
      profileId: input.profileId,
      goalText: goalCommand.goalText,
      operatorInstruction: input.body,
      operatorMessageId: operatorMessage.id,
      source: "takyon_chat"
    });
    const ceoMessage = await createInboxMessage({
      companyId: input.companyId,
      authorLabel: "CEO",
      body:
        started.supported
          ? "Goal started: get first paying customer. I will keep ticking this goal until there is a verified positive Stripe revenue event, or until a real blocker such as missing checkout/research/deployment capability stops progress."
          : started.reason,
      source: "ceo_action_router"
    });
    await createEvent({
      businessId: input.companyId,
      actorProfileId: input.profileId,
      kind: "takyon.chat_answered",
      subjectType: "business_inbox_message",
      subjectId: ceoMessage.id,
      payload: {
        provider: "deterministic_goal_router",
        queued_actions: started.supported ? [started.tick] : [],
        operator_message_id: operatorMessage.id
      }
    });
    return { message: ceoMessage, queued: started.supported ? [started.tick] : [] };
  }

  const actions = requestedActions(input.body);
  const jobs = await enqueueActions({
    companyId: input.companyId,
    profileId: input.profileId,
    body: input.body,
    actions,
    operatorMessageId: operatorMessage.id
  });
  const context = await loadCeoContext(input.companyId);
  const policy = providerPolicy();
  const response = await executeAiProvider({
    provider: policy.provider,
    model: policy.model,
    maxOutputTokens: 650,
    messages: [
      {
        role: "system",
        content: [
          "You are the scoped Takyon CEO chat router.",
          "Answer the operator from the provided business state only.",
          "Answer like a normal concise chat message. Use plain text only: no Markdown, no bold markers, no headings, no bullet lists, and no backticks unless quoting an exact path or command.",
          "Keep the answer to one short paragraph unless the operator explicitly asks for more. Mention queued actions only when they are in the queued_actions list.",
          "If queued_actions contains blocked entries, say the action was not queued and name the missing capability source.",
          "Do not claim vendor side effects, posts, ads, deployment, payments, or edits happened unless the context shows completed receipts.",
          "Never say agent_runner cron executes queued jobs. It is only a scheduling pulse.",
          "Do not mention the local Mac worker or local machine details in normal operator chat. Say background runner or queued runner instead, unless the operator explicitly asks about runtime architecture.",
          "If the operator asks for an action, explain the bounded job that was queued and what will execute it.",
          "For website edits, say the background runner will run the generated-app surface builder; do not imply the chat directly edited files.",
          "For Meta/ads, say Sora creative display only; no Meta launch/spend in v0.",
          "If a requested capability is not implemented, state it plainly."
        ].join("\n")
      },
      {
        role: "user",
        content: [
          `Operator message: ${input.body}`,
          "",
          "Queued or blocked actions from deterministic router:",
          compactRows(jobs),
          "",
          contextText(context)
        ].join("\n")
      }
    ]
  });

  const answer = response.text || "I could not produce a CEO answer from the available model response.";
  const ceoMessage = await createInboxMessage({
    companyId: input.companyId,
    authorLabel: "CEO",
    body: answer,
    source: jobs.length ? "ceo_action_router" : "ceo_chat"
  });

  if (actions.some((action) => action.workflowId === "ceo_wakeup") || /daily (digest|report)|ceo report/i.test(input.body)) {
    await upsertBusinessDocument({
      companyId: input.companyId,
      title: `Daily Report ${new Date().toISOString().slice(0, 10)}`,
      kind: "daily_report",
      source: "workflow",
      content: answer,
      metadata: { provider: policy.provider, model: policy.model, source: "takyon_chat" }
    });
  }

  await createEvent({
    businessId: input.companyId,
    actorProfileId: input.profileId,
    kind: "takyon.chat_answered",
    subjectType: "business_inbox_message",
    subjectId: ceoMessage.id,
    payload: { provider: policy.provider, model: policy.model, queued_actions: jobs }
  });

  return { message: ceoMessage, queued: jobs };
}
