import { getOpenAiVideoEnv } from "../env";

const OPENAI_VIDEO_BASE_URL = "https://api.openai.com/v1";

async function readBody(response: Response) {
  const contentType = response.headers.get("content-type") || "";
  if (contentType.includes("application/json")) return response.json();
  const text = await response.text();
  if (!text) return null;
  try {
    return JSON.parse(text) as unknown;
  } catch {
    return { raw: text };
  }
}

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value) ? (value as Record<string, unknown>) : {};
}

function stringValue(record: Record<string, unknown>, key: string) {
  const value = record[key];
  return typeof value === "string" ? value : "";
}

export function defaultSoraModel() {
  return process.env.OPENAI_SORA_MODEL?.trim() || "sora-2";
}

export function defaultSoraSeconds() {
  const seconds = process.env.OPENAI_SORA_SECONDS?.trim() || "8";
  return seconds === "4" || seconds === "8" || seconds === "12" ? seconds : "8";
}

export function defaultSoraSize() {
  const size = process.env.OPENAI_SORA_SIZE?.trim() || "720x1280";
  return ["720x1280", "1280x720", "1024x1792", "1792x1024"].includes(size) ? size : "720x1280";
}

export async function createOpenAiVideo(input: {
  prompt: string;
  model?: string;
  seconds?: string;
  size?: string;
  metadata?: Record<string, unknown>;
}) {
  const env = getOpenAiVideoEnv();
  const model = input.model?.trim() || defaultSoraModel();
  const body = new FormData();
  body.set("model", model);
  body.set("prompt", input.prompt);
  body.set("seconds", input.seconds || defaultSoraSeconds());
  body.set("size", input.size || defaultSoraSize());
  const response = await fetch(`${OPENAI_VIDEO_BASE_URL}/videos`, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${env.OPENAI_API_KEY}`
    },
    body
  });
  const parsed = await readBody(response);
  if (!response.ok) throw new Error(`OpenAI video create returned ${response.status}.`);
  const data = asRecord(parsed);
  const id = stringValue(data, "id");
  if (!id) throw new Error("OpenAI video response did not include an id.");
  return {
    id,
    status: stringValue(data, "status") || "queued",
    model,
    raw: parsed
  };
}

export async function getOpenAiVideo(videoId: string) {
  const env = getOpenAiVideoEnv();
  const response = await fetch(`${OPENAI_VIDEO_BASE_URL}/videos/${encodeURIComponent(videoId)}`, {
    headers: { Authorization: `Bearer ${env.OPENAI_API_KEY}` },
    cache: "no-store"
  });
  const parsed = await readBody(response);
  if (!response.ok) throw new Error(`OpenAI video status returned ${response.status}.`);
  const data = asRecord(parsed);
  const errorRecord = asRecord(data.error);
  const error = stringValue(errorRecord, "message") || stringValue(data, "error") || null;
  return {
    id: stringValue(data, "id") || videoId,
    status: stringValue(data, "status") || "in_progress",
    progress: typeof data.progress === "number" ? data.progress : null,
    error,
    raw: parsed
  };
}

export async function downloadOpenAiVideoContent(videoId: string) {
  const env = getOpenAiVideoEnv();
  const response = await fetch(`${OPENAI_VIDEO_BASE_URL}/videos/${encodeURIComponent(videoId)}/content`, {
    headers: { Authorization: `Bearer ${env.OPENAI_API_KEY}` },
    cache: "no-store"
  });
  if (!response.ok) {
    const parsed = await readBody(response);
    const message = asRecord(parsed).message || JSON.stringify(parsed);
    throw new Error(`OpenAI video content returned ${response.status}: ${message}`);
  }
  return response;
}
