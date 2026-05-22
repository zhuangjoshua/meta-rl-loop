import { db } from "./db";
import { listBusinessMemory, upsertBusinessMemory } from "./business-memory";
import { upsertBusinessDocument } from "./documents";
import { ConfigurationError } from "./errors";
import { createInboxMessage } from "./inbox";
import { toJson } from "./json";
import { runTakyonRuntimeReasoning } from "./takyon-runtime";
import { businessWorkspaceContext } from "./business-workspace";
import { executeAiProvider } from "./ai-provider";
import { loadLocalSecrets } from "./secrets";

function useRemoteRuntime() {
  return process.env.TAKYON_REMOTE_RUNTIME === "1";
}

function configuredLocalCeoProvider() {
  loadLocalSecrets();
  const explicit = process.env.TAKYON_CEO_PROVIDER?.trim().toLowerCase() || process.env.ARGON_CEO_PROVIDER?.trim().toLowerCase();
  if (explicit === "anthropic" || explicit === "openai") return explicit;
  if (process.env.ANTHROPIC_API_KEY?.trim()) return "anthropic";
  if (process.env.OPENAI_API_KEY?.trim()) return "openai";
  return null;
}

function localCeoModel(provider: string) {
  return (
    process.env.TAKYON_CEO_MODEL?.trim() ||
    process.env.ARGON_CEO_MODEL?.trim() ||
    process.env.ARGON_PRODUCT_AI_MODEL?.trim() ||
    (provider === "anthropic" ? "claude-opus-4-7" : "gpt-5.2")
  );
}

async function runLocalMacCeoReasoning(prompt: string) {
  const provider = configuredLocalCeoProvider();
  if (!provider) {
    throw new ConfigurationError("Local Mac CEO runtime requires ANTHROPIC_API_KEY or OPENAI_API_KEY. Set one with: ./takyon secret set ANTHROPIC_API_KEY --stdin");
  }
  const model = localCeoModel(provider);
  const response = await executeAiProvider({
    provider,
    model,
    maxOutputTokens: 1800,
    messages: [
      {
        role: "system",
        content: [
          "You are Takyon's terminal CEO runtime running locally on this Mac.",
          "Use only explicit business context, workspace files, database rows, receipts, and capability reports.",
          "Do not claim external side effects unless the context explicitly says they happened.",
          "Keep the operating report concise, evidence-grounded, and useful for the next wake."
        ].join("\n")
      },
      { role: "user", content: prompt }
    ]
  });
  return { output: response.text, raw: response.raw, provider: `local-mac:${provider}`, model };
}

export async function runCeoReasoning(input: { businessId: string }) {
  const sql = db();
  const [companyRows, documentRows, jobRows, memoryRows, workspace] = await Promise.all([
    sql<{ name: string; public_pitch: string | null }[]>`
      SELECT b.name, cs.public_pitch
      FROM businesses b
      LEFT JOIN company_sites cs ON cs.business_id = b.id
      WHERE b.id = ${input.businessId}
      LIMIT 1
    `,
    sql<{ title: string; kind: string; content: string }[]>`
      SELECT title, kind, content
      FROM business_documents
      WHERE business_id = ${input.businessId}
      ORDER BY updated_at DESC
      LIMIT 8
    `,
    sql<{ workflow_id: string; lane: string; status: string; error: string | null }[]>`
      SELECT workflow_id, lane, status, error
      FROM workflow_jobs
      WHERE business_id = ${input.businessId}
      ORDER BY created_at DESC
      LIMIT 20
    `,
    listBusinessMemory({ businessId: input.businessId, limit: 12 }),
    businessWorkspaceContext({ businessId: input.businessId })
  ]);
  const company = companyRows[0];
  if (!company) throw new Error("Company not found for CEO reasoning.");
  const prompt = [
    `Company: ${company.name}`,
    `Pitch: ${company.public_pitch ?? ""}`,
    "",
    "Workflow state:",
    ...jobRows.map((job) => `- ${job.workflow_id} (${job.lane}): ${job.status}${job.error ? ` - ${job.error}` : ""}`),
    "",
    "Recent documents:",
    ...documentRows.map((document) => `## ${document.title} (${document.kind})\n${document.content.slice(0, 1600)}`),
    "",
    "Business memory:",
    ...memoryRows
      .map((memory) => `## ${memory.title} (${memory.namespace})\n${memory.content.slice(0, 1800)}`),
    "",
    "Business workspace:",
    `Root: ${workspace.root}`,
    "Boot files:",
    ...workspace.bootFiles.map((file) => `- ${file}`),
    "",
    "Workspace files:",
    ...workspace.files.slice(0, 120).map((file) => `- ${file.path} (${file.bytes} bytes, ${file.updatedAt})`),
    "",
    "Boot file excerpts:",
    ...workspace.excerpts.map((file) => `## ${file.path}\n${file.content.slice(0, 6000)}`),
    "",
    "CEO evidence rule:",
    "Only make decisions from explicit workspace files, database rows, receipts, and capability reports in this prompt.",
    "If a needed fact is not visible in the workspace or rows, say it is unknown and name the file or receipt that should exist.",
    "Do not infer product completion, campaign success, revenue, auth, checkout, deployment, posting, or spend from intentions or queued work.",
    "",
    "Write a concise CEO operating report with priorities, blockers, next actions, and any business-memory updates you would make.",
    "Use ceo/map.md and state/index.json to decide which files to inspect. If the runtime file tools are available and the operating picture changed, keep ceo/brief.md short and current.",
    "Track strategy, positioning, pricing ideas, customer objections, distribution lessons, product lessons, and recovery lessons when evidence supports them.",
    "Do not claim vendor side effects unless listed as completed."
  ].join("\n");

  const runtime = useRemoteRuntime()
    ? await runTakyonRuntimeReasoning({ businessId: input.businessId, prompt, metadata: { workflow: "ceo", business_workspace_root: workspace.root } })
    : await runLocalMacCeoReasoning(prompt);
  const provider = "provider" in runtime ? runtime.provider : "remote-takyon-runtime";
  const model = "model" in runtime ? runtime.model : "remote-takyon-runtime";
  const text = runtime.output;
  const raw = runtime.raw;

  const report = await upsertBusinessDocument({
    companyId: input.businessId,
    title: `Daily Report ${new Date().toISOString().slice(0, 10)}`,
    kind: "daily_report",
    source: "workflow",
    content: text,
    metadata: { provider, model, raw: toJson(raw) }
  });
  await createInboxMessage({
    companyId: input.businessId,
    authorLabel: "CEO",
    body: text.slice(0, 1200),
    source: "ceo"
  });
  await upsertBusinessMemory({
    businessId: input.businessId,
    profileId: null,
    namespace: "strategy",
    memoryKey: "latest-ceo-report",
    title: "Latest CEO report",
    content: text,
    evidence: [{ kind: "business_document", document_id: report.id }],
    metadata: { provider, model, source: "ceo_wakeup" }
  });
  return { reportId: report.id, provider, model };
}
