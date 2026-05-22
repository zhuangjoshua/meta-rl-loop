import { submitArgonRun } from "./vendors/argon-runtime";

export async function runTakyonRuntimeReasoning(input: { businessId: string; prompt: string; metadata?: Record<string, unknown> }) {
  const json = await submitArgonRun({
    input: input.prompt,
    instructions: [
      "Run the requested Takyon reasoning task against the provided context.",
      "Do not claim external side effects unless the context explicitly says they happened."
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
  const record = json && typeof json === "object" ? (json as Record<string, unknown>) : {};
  const output =
    typeof record.output === "string"
      ? record.output
      : typeof record.text === "string"
        ? record.text
        : typeof record.result === "string"
          ? record.result
      : JSON.stringify(json);
  return { output, raw: json, runtimeUrl: "/v1/runs" };
}
