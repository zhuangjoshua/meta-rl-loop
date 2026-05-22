import { ConfigurationError } from "./errors";
import type { AiMessage } from "./project-ai";

function splitSystem(messages: AiMessage[]) {
  const system = messages
    .filter((message) => message.role === "system")
    .map((message) => message.content)
    .join("\n\n")
    .trim();
  const conversation = messages.filter((message) => message.role !== "system") as Array<{ role: "user" | "assistant"; content: string }>;
  return { system, conversation };
}

function outputTextFromOpenAi(value: unknown) {
  const record = value && typeof value === "object" ? (value as Record<string, unknown>) : {};
  if (typeof record.output_text === "string" && record.output_text.trim()) return record.output_text.trim();
  const output = Array.isArray(record.output) ? record.output : [];
  const chunks: string[] = [];
  for (const item of output) {
    const itemRecord = item && typeof item === "object" ? (item as Record<string, unknown>) : {};
    const content = Array.isArray(itemRecord.content) ? itemRecord.content : [];
    for (const part of content) {
      const partRecord = part && typeof part === "object" ? (part as Record<string, unknown>) : {};
      if (typeof partRecord.text === "string") chunks.push(partRecord.text);
    }
  }
  return chunks.join("\n").trim();
}

function supportsAdaptiveThinking(model: string) {
  return (
    model.startsWith("claude-opus-4-7") ||
    model.startsWith("claude-opus-4-6") ||
    model.startsWith("claude-sonnet-4-6") ||
    model.startsWith("claude-mythos")
  );
}

function anthropicEffort(model: string) {
  const configured = process.env.ARGON_PRODUCT_AI_EFFORT?.trim();
  if (configured === "low" || configured === "medium" || configured === "high" || configured === "xhigh" || configured === "max") {
    if (configured === "xhigh" && !model.startsWith("claude-opus-4-7")) return "high";
    return configured;
  }
  return model.startsWith("claude-opus-4-7") ? "high" : undefined;
}

export async function executeAiProvider(input: {
  provider: string;
  model: string;
  messages: AiMessage[];
  maxOutputTokens: number;
}) {
  const provider = input.provider.toLowerCase();
  if (provider === "openai") return executeOpenAi(input);
  if (provider === "anthropic") return executeAnthropic(input);
  throw new ConfigurationError(`Unsupported AI provider: ${input.provider}`);
}

async function executeOpenAi(input: { model: string; messages: AiMessage[]; maxOutputTokens: number }) {
  const apiKey = process.env.OPENAI_API_KEY?.trim();
  if (!apiKey) throw new ConfigurationError("OPENAI_API_KEY is not configured.");
  const { system, conversation } = splitSystem(input.messages);
  const response = await fetch("https://api.openai.com/v1/responses", {
    method: "POST",
    headers: {
      Authorization: `Bearer ${apiKey}`,
      "Content-Type": "application/json"
    },
    body: JSON.stringify({
      model: input.model,
      instructions: system || undefined,
      input: conversation.map((message) => ({
        role: message.role,
        content: [{ type: "input_text", text: message.content }]
      })),
      max_output_tokens: input.maxOutputTokens
    }),
    signal: AbortSignal.timeout(60_000)
  });
  const json = await response.json().catch(() => null);
  if (!response.ok) {
    const error = json && typeof json === "object" ? (json as { error?: { message?: string } }).error?.message : null;
    throw new Error(error || `OpenAI returned ${response.status}.`);
  }
  const usage = json && typeof json === "object" ? (json as { usage?: { input_tokens?: number; output_tokens?: number }; id?: string }) : {};
  return {
    text: outputTextFromOpenAi(json),
    providerRequestId: usage.id ?? null,
    inputTokens: usage.usage?.input_tokens ?? null,
    outputTokens: usage.usage?.output_tokens ?? null,
    raw: json
  };
}

async function executeAnthropic(input: { model: string; messages: AiMessage[]; maxOutputTokens: number }) {
  const apiKey = process.env.ANTHROPIC_API_KEY?.trim();
  if (!apiKey) throw new ConfigurationError("ANTHROPIC_API_KEY is not configured.");
  const { system, conversation } = splitSystem(input.messages);
  const effort = anthropicEffort(input.model);
  const adaptiveThinking = supportsAdaptiveThinking(input.model);
  const requestBody = {
    model: input.model,
    max_tokens: input.maxOutputTokens,
    system: system || undefined,
    messages: conversation,
    thinking: adaptiveThinking ? { type: "adaptive", display: "omitted" } : undefined,
    output_config: effort ? { effort } : undefined
  };
  const response = await fetch("https://api.anthropic.com/v1/messages", {
    method: "POST",
    headers: {
      "x-api-key": apiKey,
      "anthropic-version": "2023-06-01",
      "content-type": "application/json"
    },
    body: JSON.stringify(requestBody),
    signal: AbortSignal.timeout(60_000)
  });
  const json = (await response.json().catch(() => null)) as
    | { id?: string; content?: Array<{ type?: string; text?: string }>; usage?: { input_tokens?: number; output_tokens?: number }; error?: { message?: string } }
    | null;
  if (!response.ok) throw new Error(json?.error?.message || `Anthropic returned ${response.status}.`);
  return {
    text: (json?.content ?? []).map((part) => (part.type === "text" || !part.type ? part.text ?? "" : "")).join("\n").trim(),
    providerRequestId: json?.id ?? null,
    inputTokens: json?.usage?.input_tokens ?? null,
    outputTokens: json?.usage?.output_tokens ?? null,
    raw: json
  };
}
