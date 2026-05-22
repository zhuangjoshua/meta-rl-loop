import { getArgonRuntimeEnv } from "./env";
import { IntegrationCallError } from "./errors";
import { submitArgonRun, waitForArgonRunCompletion } from "./vendors/argon-runtime";

export async function runTakyonRuntimeReasoning(input: { businessId: string; prompt: string; metadata?: Record<string, unknown> }) {
  const env = getArgonRuntimeEnv();
  const started = await submitArgonRun({
    input: input.prompt,
    instructions: [
      "You are the Takyon CEO runtime for exactly one business.",
      "Use the supplied business context, workspace root, available skills, files, todos, and web tools.",
      "If the skills tool is available, inspect the relevant Takyon skills before choosing or explaining work.",
      "Do not claim external side effects unless the context explicitly says they happened.",
      "When deterministic work should run, return the bounded workflow JSON requested by the prompt."
    ].join("\n"),
    sessionId: `business:${input.businessId}:ceo`,
    metadata: {
      business_id: input.businessId,
      memory: false,
      business_workspace_root: input.metadata?.business_workspace_root ?? null,
      ...(input.metadata ?? {})
    },
    runtimeOptions: {
      skipMemory: true,
      skipContextFiles: false,
      enabledToolsets: ["web", "skills", "todo", "files"],
      disabledToolsets: ["memory", "session_search", "cronjob"]
    }
  });

  const runId = typeof started.run_id === "string" ? started.run_id : typeof started.id === "string" ? started.id : "";
  const json = runId
    ? await waitForArgonRunCompletion(runId, Number.parseInt(process.env.TAKYON_RUNTIME_RUN_TIMEOUT_MS || "", 10) || 180_000)
    : started;
  const record = json && typeof json === "object" ? (json as Record<string, unknown>) : {};
  const status = typeof record.status === "string" ? record.status : "";
  if (status === "failed" || status === "cancelled") {
    const error = typeof record.error === "string" ? record.error : `run ended with status ${status}`;
    throw new IntegrationCallError("Takyon runtime", error);
  }
  const output =
    typeof record.output === "string"
      ? record.output
      : typeof record.text === "string"
        ? record.text
        : typeof record.result === "string"
          ? record.result
      : JSON.stringify(json);
  if (!output.trim() || output === JSON.stringify(started)) {
    throw new IntegrationCallError("Takyon runtime", "run completed without CEO output");
  }
  return {
    output,
    raw: json,
    provider: "takyon-hermes-runtime",
    model: env.ARGON_RUNTIME_MODEL,
    runtimeUrl: "/v1/runs",
    runId: runId || null
  };
}
