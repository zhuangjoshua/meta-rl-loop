import { db } from "./db";
import { executeAiProvider } from "./ai-provider";
import { loadLocalSecrets } from "./secrets";
import { buildTakyonSelfDescription, type TakyonSelfDescription } from "./takyon-self-description";

export type TakyonTerminalAgentAction = "chat" | "attach" | "wake" | "autopilot" | "create_business";

export type TakyonTerminalAgentTurn = {
  reply: string;
  action: TakyonTerminalAgentAction;
  businessSlug: string | null;
  businessName: string | null;
  businessPitch: string | null;
  budgetUsd: number | null;
  operatorInstruction: string | null;
  confidence: number;
  source: "model" | "local";
};

export type TakyonTerminalRecentTurn = {
  role: "operator" | "takyon";
  text: string;
};

type BusinessSummary = {
  id: string;
  name: string;
  slug: string;
  status: string;
  public_pitch: string | null;
  site_status: string | null;
  updated_at: string;
};

const outputSchema = {
  type: "object",
  additionalProperties: false,
  required: ["reply", "action", "business_slug", "business_name", "business_pitch", "budget_usd", "operator_instruction", "confidence"],
  properties: {
    reply: { type: "string" },
    action: { type: "string", enum: ["chat", "attach", "wake", "autopilot", "create_business"] },
    business_slug: { anyOf: [{ type: "string" }, { type: "null" }] },
    business_name: { anyOf: [{ type: "string" }, { type: "null" }] },
    business_pitch: { anyOf: [{ type: "string" }, { type: "null" }] },
    budget_usd: { anyOf: [{ type: "number" }, { type: "null" }] },
    operator_instruction: { anyOf: [{ type: "string" }, { type: "null" }] },
    confidence: { type: "number", minimum: 0, maximum: 1 }
  }
};

function coerceTurn(value: Record<string, unknown>, defaultReply: string): Omit<TakyonTerminalAgentTurn, "source"> {
  const action = ["chat", "attach", "wake", "autopilot", "create_business"].includes(String(value.action))
    ? (String(value.action) as TakyonTerminalAgentAction)
    : "chat";
  const confidence = typeof value.confidence === "number" && Number.isFinite(value.confidence) ? value.confidence : 0;
  const budgetUsd = typeof value.budget_usd === "number" && Number.isFinite(value.budget_usd)
    ? Math.max(0, value.budget_usd)
    : null;
  return {
    reply: typeof value.reply === "string" && value.reply.trim() ? value.reply.trim() : defaultReply,
    action,
    businessSlug: typeof value.business_slug === "string" && value.business_slug.trim() ? value.business_slug.trim() : null,
    businessName: typeof value.business_name === "string" && value.business_name.trim() ? value.business_name.trim() : null,
    businessPitch: typeof value.business_pitch === "string" && value.business_pitch.trim() ? value.business_pitch.trim() : null,
    budgetUsd,
    operatorInstruction:
      typeof value.operator_instruction === "string" && value.operator_instruction.trim()
        ? value.operator_instruction.trim()
        : null,
    confidence: Math.max(0, Math.min(1, confidence))
  };
}

async function listBusinessesForAgent(profileId: string) {
  const sql = db();
  return sql<BusinessSummary[]>`
    SELECT b.id,
           b.name,
           b.slug,
           b.status,
           cs.public_pitch,
           cs.status AS site_status,
           GREATEST(b.updated_at, COALESCE(cs.updated_at, b.updated_at))::text AS updated_at
    FROM businesses b
    JOIN business_memberships bm ON bm.business_id = b.id
    LEFT JOIN company_sites cs ON cs.business_id = b.id
    WHERE bm.profile_id = ${profileId}
    ORDER BY GREATEST(b.updated_at, COALESCE(cs.updated_at, b.updated_at)) DESC, b.created_at DESC
    LIMIT 40
  `;
}

async function businessContext(profileId: string, slug: string | null) {
  if (!slug) return null;
  const sql = db();
  const rows = await sql<BusinessSummary[]>`
    SELECT b.id,
           b.name,
           b.slug,
           b.status,
           cs.public_pitch,
           cs.status AS site_status,
           GREATEST(b.updated_at, COALESCE(cs.updated_at, b.updated_at))::text AS updated_at
    FROM businesses b
    JOIN business_memberships bm ON bm.business_id = b.id
    LEFT JOIN company_sites cs ON cs.business_id = b.id
    WHERE bm.profile_id = ${profileId}
      AND b.slug = ${slug}
    LIMIT 1
  `;
  const business = rows[0];
  if (!business) return null;

  const [campaigns, memory, jobs, counts] = await Promise.all([
    sql<{ slug: string; name: string; kind: string; status: string; budget_cap_microusd: string | null; updated_at: string }[]>`
      SELECT slug, name, kind, status, budget_cap_microusd, updated_at::text
      FROM business_campaigns
      WHERE business_id = ${business.id}
      ORDER BY updated_at DESC
      LIMIT 12
    `,
    sql<{ namespace: string; memory_key: string; title: string; content: string; confidence: string | null; updated_at: string }[]>`
      SELECT namespace, memory_key, title, content, confidence::text, updated_at::text
      FROM business_memory_records
      WHERE business_id = ${business.id}
        AND status = 'active'
      ORDER BY updated_at DESC
      LIMIT 12
    `,
    sql<{ workflow_id: string; lane: string; status: string; error: string | null; updated_at: string }[]>`
      SELECT workflow_id, lane, status, error, updated_at::text
      FROM workflow_jobs
      WHERE business_id = ${business.id}
      ORDER BY updated_at DESC
      LIMIT 16
    `,
    sql<{
      deployments: number;
      product_builds: number;
      social_posts: number;
      leads: number;
      users: number;
      revenue_cents: number;
    }[]>`
      SELECT
        (SELECT count(*)::int FROM generated_app_deployments WHERE business_id = ${business.id} AND status = 'completed') AS deployments,
        (SELECT count(*)::int FROM generated_app_builds WHERE business_id = ${business.id} AND status = 'completed') AS product_builds,
        (SELECT count(*)::int FROM business_social_posts WHERE business_id = ${business.id} AND status IN ('ready', 'published')) AS social_posts,
        (SELECT count(*)::int FROM leads WHERE business_id = ${business.id}) AS leads,
        (SELECT count(*)::int FROM generated_app_users WHERE business_id = ${business.id}) AS users,
        COALESCE((SELECT SUM(amount_paid_cents) FROM company_revenue_events WHERE business_id = ${business.id} AND status IN ('paid', 'succeeded', 'complete', 'completed')), 0)::int AS revenue_cents
    `
  ]);

  return {
    business,
    counts: counts[0] ?? {},
    campaigns,
    memory,
    recent_jobs: jobs
  };
}

function stableDoctrine() {
  return [
    "You are Takyon in terminal mode: a terminal-native CEO/operator agent for building and operating isolated businesses.",
    "",
    "Operating posture:",
    "- Treat slash-prefixed input as deterministic CLI control handled outside the model.",
    "- Treat plain text as operator conversation or instruction.",
    "- Be direct, business-aware, and evidence-bound. Do not sound like a generic assistant.",
    "- Know what you can do only from LIVE SELF-DESCRIPTION and CURRENT BUSINESS CONTEXT, not from memory or guesswork.",
    "- If asked what features you have, summarize the actual live commands, workflows, skills, capabilities, controls, and missing-key blockers.",
    "- Never answer with internal routing labels as the feature list.",
    "",
    "Business isolation:",
    "- Prefer the attached/current business.",
    "- If no business is attached, infer from recent conversation and the visible business list.",
    "- Do not blend business memory, campaigns, jobs, budgets, or conclusions across businesses unless the operator asks cross-business.",
    "- If a business-specific request cannot be grounded to a business, ask for a business slug and suggest /businesses.",
    "- If the operator asks to start, create, build, or launch a new business and the message contains enough concept/pitch to proceed, return action create_business instead of asking them to use a slash command.",
    "",
    "Evidence and side effects:",
    "- Treat the business workspace in LIVE SELF-DESCRIPTION and CURRENT BUSINESS CONTEXT as the agent-facing filesystem view.",
    "- The workspace top-level map is first-class evidence. Prompt truncation never means a path is absent.",
    "- If the operator asks whether something is done, answer only from visible workspace files, receipts, jobs, and capability reports.",
    "- If relevant evidence is omitted, truncated, unreadable, or not visible, say it is unknown and identify what file/receipt/check should exist.",
    "- Never claim work, posting, deployment, spend, revenue, users, or provider mutations happened unless context says so.",
    "- If a capability is blocked, explain the exact missing keys/setup path from LIVE SELF-DESCRIPTION.",
    "- You may propose or request work, but deterministic Takyon code executes queues, budgets, provider calls, receipts, cleanup, and kill switches.",
    "",
    "Work selection:",
    "- Product/value delivery and outreach/demand creation are standing obligations, not a fixed stage ladder.",
    "- Decide what to wake or explain from the visible business workspace, receipts, queue state, capability reports, and operator intent.",
    "- Failures require recovery and learning before claiming progress.",
    "",
    "Memory:",
    "- Treat memory as business-specific unless explicitly marked cross-business.",
    "- Learn strategy, positioning, pricing ideas, customer objections, distribution lessons, product lessons, and operator preferences only from evidence or operator statements.",
    "",
    "Return contract:",
    "- Return strict JSON only.",
    "- reply: short operator-facing answer.",
    "- action: chat, attach, wake, autopilot, or create_business.",
    "- business_slug: selected business slug or null.",
    "- business_name: concise new business name only for create_business, otherwise null.",
    "- business_pitch: concise business pitch only for create_business, otherwise null.",
    "- budget_usd: initial business budget only if the operator stated one, otherwise null.",
    "- operator_instruction: concise instruction for Takyon runner when action is wake/autopilot, otherwise null.",
    "- confidence: 0 to 1."
  ].join("\n");
}

function agentUserPrompt(input: {
  text: string;
  businesses: BusinessSummary[];
  currentBusinessSlug?: string | null;
  recentBusinessSlug?: string | null;
  scopedBusiness: Awaited<ReturnType<typeof businessContext>>;
  recentTurns: TakyonTerminalRecentTurn[];
  selfDescription: unknown;
}) {
  return [
    "OUTPUT JSON SCHEMA:",
    JSON.stringify(outputSchema, null, 2),
    "",
    "LIVE SELF-DESCRIPTION JSON:",
    JSON.stringify(input.selfDescription, null, 2),
    "",
    "CURRENT BUSINESS CONTEXT JSON:",
    JSON.stringify(
      {
        current_business_slug: input.currentBusinessSlug ?? null,
        recent_business_slug: input.recentBusinessSlug ?? null,
        businesses: input.businesses,
        scoped_business: input.scopedBusiness,
        recent_turns: input.recentTurns.slice(-8)
      },
      null,
      2
    ),
    "",
    "Operator message:",
    input.text
  ].join("\n");
}

function parseJsonObject(text: string) {
  const trimmed = text.trim();
  const jsonText = trimmed.startsWith("```")
    ? trimmed.replace(/^```(?:json)?\s*/i, "").replace(/\s*```$/, "")
    : trimmed;
  const direct = JSON.parse(jsonText) as unknown;
  if (direct && typeof direct === "object" && !Array.isArray(direct)) return direct as Record<string, unknown>;
  throw new Error("Model did not return a JSON object.");
}

function configuredProvider() {
  const explicit = process.env.TAKYON_TERMINAL_AGENT_PROVIDER?.trim().toLowerCase();
  if (explicit === "anthropic" || explicit === "openai") return explicit;
  if (process.env.ANTHROPIC_API_KEY?.trim()) return "anthropic";
  if (process.env.OPENAI_API_KEY?.trim()) return "openai";
  return null;
}

function terminalModel(provider: string) {
  return (
    process.env.TAKYON_TERMINAL_AGENT_MODEL?.trim() ||
    (provider === "anthropic" ? "claude-sonnet-4-6" : "gpt-5.2")
  );
}

function asksForSelfDescription(text: string) {
  const normalized = text.toLowerCase();
  return (
    /\bwhat (features|tools|skills|commands|capabilities)\b/.test(normalized) ||
    /\bwhat can you do\b/.test(normalized) ||
    /\bwhat do you do\b/.test(normalized) ||
    /\blist (features|tools|skills|commands|capabilities)\b/.test(normalized)
  );
}

function renderSelfDescription(description: TakyonSelfDescription) {
  const commandLines = description.terminal_mode.command_help
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter((line) => line.startsWith("/") || line.includes(" /"));
  const skills = description.skills.flatMap((manifest) => manifest.skills.map((skill) => skill.id));
  const available = description.capabilities.filter((capability) => capability.can_run);
  const blocked = description.capabilities.filter((capability) => !capability.can_run);

  return [
    "I can operate Takyon from this terminal using the live repo state.",
    "",
    `Slash controls: ${commandLines.slice(0, 14).join(", ") || "run /help for command help"}.`,
    `Harness commands: ${description.harness.commands.map((command) => `/${command.name}`).join(", ") || "none"}.`,
    `Workflows: ${description.workflows.dispatchable_workflow_ids.join(", ")}.`,
    `Skills: ${skills.slice(0, 18).join(", ")}${skills.length > 18 ? `, and ${skills.length - 18} more` : ""}.`,
    `Available capabilities: ${available.map((capability) => capability.label).join(", ") || "none configured"}.`,
    blocked.length
      ? `Blocked capabilities: ${blocked.map((capability) => `${capability.label}${capability.missing.length ? ` missing ${capability.missing.join(", ")}` : ""}`).join("; ")}.`
      : "Blocked capabilities: none reported.",
    `Kill switches: ${description.controls.scope_types.join(", ")} scopes with ${description.controls.states.join("/")} states.`,
    "",
    "Plain text is for business conversation/instruction; deterministic work still runs through queues, capability checks, budgets, receipts, and kill switches."
  ].join("\n");
}

export async function runTakyonTerminalAgent(input: {
  profileId: string;
  text: string;
  currentBusinessSlug?: string | null;
  recentBusinessSlug?: string | null;
  recentTurns?: TakyonTerminalRecentTurn[];
  terminalHelp: string;
  signal?: AbortSignal;
}): Promise<TakyonTerminalAgentTurn> {
  loadLocalSecrets();
  input.signal?.throwIfAborted();
  const scopedSlug = input.currentBusinessSlug ?? input.recentBusinessSlug ?? null;
  const scopedBusiness = await businessContext(input.profileId, scopedSlug);
  input.signal?.throwIfAborted();
  const selfDescription = await buildTakyonSelfDescription({
    profileId: input.profileId,
    businessId: scopedBusiness?.business.id ?? null,
    terminalHelp: input.terminalHelp,
    operatorText: input.text
  });
  input.signal?.throwIfAborted();

  if (asksForSelfDescription(input.text)) {
    return {
      source: "local",
      reply: renderSelfDescription(selfDescription),
      action: "chat",
      businessSlug: input.currentBusinessSlug ?? input.recentBusinessSlug ?? null,
      businessName: null,
      businessPitch: null,
      budgetUsd: null,
      operatorInstruction: null,
      confidence: 1
    };
  }

  const provider = configuredProvider();
  if (!provider) {
    return {
      source: "local",
      reply: "Takyon terminal agent is unavailable because no model key is configured. Set ANTHROPIC_API_KEY or OPENAI_API_KEY with `/secret set <KEY> --stdin`.",
      action: "chat",
      businessSlug: input.currentBusinessSlug ?? input.recentBusinessSlug ?? null,
      businessName: null,
      businessPitch: null,
      budgetUsd: null,
      operatorInstruction: null,
      confidence: 1
    };
  }

  const businesses = await listBusinessesForAgent(input.profileId);

  try {
    const response = await executeAiProvider({
      provider,
      model: terminalModel(provider),
      maxOutputTokens: 900,
      signal: input.signal,
      messages: [
        { role: "system", content: stableDoctrine() },
        {
          role: "user",
          content: agentUserPrompt({
            text: input.text,
            businesses,
            currentBusinessSlug: input.currentBusinessSlug ?? null,
            recentBusinessSlug: input.recentBusinessSlug ?? null,
            scopedBusiness,
            selfDescription,
            recentTurns: input.recentTurns ?? []
          })
        }
      ]
    });
    return { ...coerceTurn(parseJsonObject(response.text), response.text), source: "model" };
  } catch (error) {
    if (input.signal?.aborted) throw error;
    return {
      source: "local",
      reply: `Takyon terminal agent could not complete that turn: ${error instanceof Error ? error.message : String(error)}`,
      action: "chat",
      businessSlug: input.currentBusinessSlug ?? input.recentBusinessSlug ?? null,
      businessName: null,
      businessPitch: null,
      budgetUsd: null,
      operatorInstruction: null,
      confidence: 0
    };
  }
}
