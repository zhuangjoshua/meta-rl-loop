import { getArgonRuntimeEnv } from "../env";
import { IntegrationCallError } from "../errors";

export type ArgonRunRequest = {
  input: string;
  instructions: string;
  metadata: Record<string, unknown>;
  sessionId: string;
  runtimeOptions?: {
    skipMemory?: boolean;
    skipContextFiles?: boolean;
    enabledToolsets?: string[];
    disabledToolsets?: string[];
  };
};

export type ArgonRunResponse = {
  run_id?: string;
  id?: string;
  status?: string;
  [key: string]: unknown;
};

export const DEFAULT_ARGON_RUNTIME_TOOLSETS = ["web", "skills", "todo"] as const;

function runtimeUrl(path: string) {
  const env = getArgonRuntimeEnv();
  return `${env.ARGON_RUNTIME_URL.replace(/\/+$/, "")}${path}`;
}

export async function checkArgonRuntimeHealth(timeoutMs = 1500) {
  let response: Response;
  try {
    response = await fetch(runtimeUrl("/health"), {
      method: "GET",
      signal: AbortSignal.timeout(timeoutMs)
    });
  } catch (error) {
    return {
      ok: false,
      status: "unreachable",
      detail: error instanceof Error ? error.message : "request failed"
    };
  }

  const body = await parseResponse(response);
  return {
    ok: response.ok,
    status: String(response.status),
    detail: typeof body === "string" ? body : JSON.stringify(body)
  };
}

async function parseResponse(response: Response) {
  const text = await response.text();
  if (!text) return null;

  try {
    return JSON.parse(text) as unknown;
  } catch {
    return { raw: text };
  }
}

export async function submitArgonRun(request: ArgonRunRequest): Promise<ArgonRunResponse> {
  const env = getArgonRuntimeEnv();
  const headers: Record<string, string> = {
    "Content-Type": "application/json"
  };

  if (env.ARGON_RUNTIME_API_KEY) {
    headers.Authorization = `Bearer ${env.ARGON_RUNTIME_API_KEY}`;
  }

  const runtimeOptions = {
    skip_memory: request.runtimeOptions?.skipMemory ?? true,
    skip_context_files: request.runtimeOptions?.skipContextFiles ?? true,
    enabled_toolsets: request.runtimeOptions?.enabledToolsets ?? [...DEFAULT_ARGON_RUNTIME_TOOLSETS],
    disabled_toolsets: request.runtimeOptions?.disabledToolsets ?? ["memory", "session_search", "cronjob"]
  };
  const body = {
    input: request.input,
    instructions: request.instructions,
    metadata: request.metadata,
    session_id: request.sessionId,
    model: env.ARGON_RUNTIME_MODEL,
    runtime_options: runtimeOptions,
    memory: false,
    learning: false
  };

  let response: Response;
  try {
    response = await fetch(runtimeUrl("/v1/runs"), {
      method: "POST",
      headers,
      body: JSON.stringify(body),
      signal: AbortSignal.timeout(30_000)
    });
  } catch (error) {
    throw new IntegrationCallError(
      "Argon runtime",
      error instanceof Error ? error.message : "request failed"
    );
  }

  const responseBody = await parseResponse(response);
  if (!response.ok) {
    throw new IntegrationCallError("Argon runtime", `${response.status} ${JSON.stringify(responseBody)}`, response.status);
  }

  return responseBody as ArgonRunResponse;
}

export async function getArgonRun(runId: string): Promise<ArgonRunResponse> {
  const env = getArgonRuntimeEnv();
  const headers: Record<string, string> = {};

  if (env.ARGON_RUNTIME_API_KEY) {
    headers.Authorization = `Bearer ${env.ARGON_RUNTIME_API_KEY}`;
  }

  let response: Response;
  try {
    response = await fetch(runtimeUrl(`/v1/runs/${encodeURIComponent(runId)}`), {
      method: "GET",
      headers,
      signal: AbortSignal.timeout(30_000)
    });
  } catch (error) {
    throw new IntegrationCallError(
      "Argon runtime",
      error instanceof Error ? error.message : "request failed"
    );
  }

  const body = await parseResponse(response);
  if (!response.ok) {
    throw new IntegrationCallError("Argon runtime", `${response.status} ${JSON.stringify(body)}`, response.status);
  }

  return body as ArgonRunResponse;
}

export async function waitForArgonRunCompletion(runId: string, timeoutMs = 120_000): Promise<ArgonRunResponse> {
  const startedAt = Date.now();
  let last = await getArgonRun(runId);

  while (Date.now() - startedAt < timeoutMs) {
    if (["completed", "failed", "cancelled"].includes(String(last.status ?? ""))) {
      return last;
    }

    await new Promise((resolve) => setTimeout(resolve, 1500));
    last = await getArgonRun(runId);
  }

  return last;
}
