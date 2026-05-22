import { db } from "./db";
import { toJson } from "./json";

export type AgentRunStatus = "queued" | "running" | "completed" | "blocked" | "failed" | "cancelled";

export type AgentRunRow = {
  id: string;
  business_id: string;
  task_id: string | null;
  workflow_job_id: string | null;
  workflow_id: string;
  addon_key: string | null;
  agent_key: string;
  status: AgentRunStatus;
  input_snapshot: unknown;
  prompt_id: string | null;
  prompt_version_id: string | null;
  output: unknown;
  error: string | null;
  started_at: string | null;
  completed_at: string | null;
  created_at: string;
  updated_at: string;
};

export async function createAgentRun(input: {
  companyId: string;
  taskId?: string | null;
  workflowJobId?: string | null;
  workflowId: string;
  addonKey?: string | null;
  agentKey: string;
  inputSnapshot?: Record<string, unknown>;
  promptId?: string | null;
  promptVersionId?: string | null;
}) {
  const sql = db();
  const rows = await sql<AgentRunRow[]>`
    INSERT INTO agent_runs (
      business_id,
      task_id,
      workflow_job_id,
      workflow_id,
      addon_key,
      agent_key,
      status,
      input_snapshot,
      prompt_id,
      prompt_version_id,
      started_at
    )
    VALUES (
      ${input.companyId},
      ${input.taskId ?? null},
      ${input.workflowJobId ?? null},
      ${input.workflowId},
      ${input.addonKey ?? null},
      ${input.agentKey},
      'running',
      ${sql.json(toJson(input.inputSnapshot ?? {}))},
      ${input.promptId ?? null},
      ${input.promptVersionId ?? null},
      now()
    )
    RETURNING id, business_id, task_id, workflow_job_id, workflow_id, addon_key, agent_key, status,
              input_snapshot, prompt_id, prompt_version_id, output, error, started_at, completed_at,
              created_at, updated_at
  `;
  return rows[0];
}

export async function createAgentRunStep(input: {
  runId: string;
  stepIndex: number;
  toolName: string;
  input?: Record<string, unknown>;
  output?: Record<string, unknown>;
  error?: string | null;
  receiptId?: string | null;
}) {
  const sql = db();
  await sql`
    INSERT INTO agent_run_steps (run_id, step_index, tool_name, input, output, error, receipt_id)
    VALUES (
      ${input.runId},
      ${input.stepIndex},
      ${input.toolName},
      ${sql.json(toJson(input.input ?? {}))},
      ${sql.json(toJson(input.output ?? {}))},
      ${input.error ?? null},
      ${input.receiptId ?? null}
    )
  `;
}

export async function finishAgentRun(input: {
  runId: string;
  status: Extract<AgentRunStatus, "completed" | "blocked" | "failed" | "cancelled">;
  output?: Record<string, unknown>;
  error?: string | null;
}) {
  if (input.status === "completed" && !input.output) {
    throw new Error("Agent run cannot complete without output evidence.");
  }

  const sql = db();
  const rows = await sql<AgentRunRow[]>`
    UPDATE agent_runs
    SET status = ${input.status},
        output = ${sql.json(toJson(input.output ?? {}))},
        error = ${input.error ?? null},
        completed_at = now(),
        updated_at = now()
    WHERE id = ${input.runId}
      AND status <> 'cancelled'
    RETURNING id, business_id, task_id, workflow_job_id, workflow_id, addon_key, agent_key, status,
              input_snapshot, prompt_id, prompt_version_id, output, error, started_at, completed_at,
              created_at, updated_at
  `;
  return rows[0] ?? null;
}
