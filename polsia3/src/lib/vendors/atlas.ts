import { getAtlasEnv } from "../env";

async function readBody(response: Response) {
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

function dataOrRecord(value: unknown) {
  const record = asRecord(value);
  const data = asRecord(record.data);
  return Object.keys(data).length ? data : record;
}

export function defaultSeedanceModel() {
  return process.env.ATLAS_SEEDANCE_MODEL?.trim() || "bytedance/seedance-2.0/text-to-video";
}

export async function generateAtlasVideo(input: { prompt: string; model?: string; metadata?: Record<string, unknown> }) {
  const env = getAtlasEnv();
  const model = input.model?.trim() || defaultSeedanceModel();
  const response = await fetch("https://api.atlascloud.ai/api/v1/model/generateVideo", {
    method: "POST",
    headers: {
      Authorization: `Bearer ${env.ATLAS_API_KEY}`,
      "Content-Type": "application/json"
    },
    body: JSON.stringify({ model, prompt: input.prompt, metadata: input.metadata ?? undefined })
  });
  const parsed = await readBody(response);
  if (!response.ok) throw new Error(`Atlas generateVideo returned ${response.status}.`);
  const data = dataOrRecord(parsed);
  const id = typeof data.id === "string" ? data.id : "";
  if (!id) throw new Error("Atlas generateVideo response did not include a prediction id.");
  return {
    id,
    status: typeof data.status === "string" ? data.status : "submitted",
    model: typeof data.model === "string" ? data.model : model,
    raw: parsed
  };
}

export async function getAtlasPrediction(predictionId: string) {
  const env = getAtlasEnv();
  const response = await fetch(`https://api.atlascloud.ai/api/v1/model/prediction/${encodeURIComponent(predictionId)}`, {
    headers: { Authorization: `Bearer ${env.ATLAS_API_KEY}` },
    cache: "no-store"
  });
  const parsed = await readBody(response);
  if (!response.ok) throw new Error(`Atlas prediction returned ${response.status}.`);
  const data = dataOrRecord(parsed);
  const outputs = Array.isArray(data.outputs) ? data.outputs.filter((value): value is string => typeof value === "string") : [];
  return {
    id: typeof data.id === "string" ? data.id : predictionId,
    status: typeof data.status === "string" ? data.status : "processing",
    outputs,
    error: typeof data.error === "string" ? data.error : null,
    raw: parsed
  };
}
