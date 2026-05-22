import { db } from "./db";
import { createEvent } from "./events";

export type TaskStatus = "queued" | "running" | "completed" | "blocked" | "failed" | "cancelled";

export type TaskRow = {
  id: string;
  business_id: string;
  title: string;
  description: string;
  category: string;
  status: TaskStatus;
  priority: number;
  created_by_profile_id: string | null;
  claimed_by: string | null;
  due_at: string | null;
  completed_at: string | null;
  created_at: string;
  updated_at: string;
};

export async function createTask(input: {
  companyId: string;
  profileId?: string | null;
  title: string;
  description?: string;
  category?: string;
  priority?: number;
}) {
  const sql = db();
  const rows = await sql<TaskRow[]>`
    INSERT INTO tasks (business_id, created_by_profile_id, title, description, category, priority, status)
    VALUES (
      ${input.companyId},
      ${input.profileId ?? null},
      ${input.title},
      ${input.description ?? ""},
      ${input.category ?? "general"},
      ${input.priority ?? 50},
      'queued'
    )
    RETURNING id, business_id, title, description, category, status, priority, created_by_profile_id,
              claimed_by, due_at, completed_at, created_at, updated_at
  `;

  await createEvent({
    businessId: input.companyId,
    actorProfileId: input.profileId ?? null,
    kind: "task.created",
    subjectType: "task",
    subjectId: rows[0].id,
    payload: { title: input.title, category: input.category ?? "general" }
  });

  return rows[0];
}

export async function updateTaskStatus(input: { companyId: string; taskId: string; status: TaskStatus }) {
  const sql = db();
  const terminal = ["completed", "blocked", "failed", "cancelled"].includes(input.status);
  const rows = await sql<TaskRow[]>`
    UPDATE tasks
    SET status = ${input.status},
        completed_at = CASE WHEN ${terminal} THEN COALESCE(completed_at, now()) ELSE NULL END,
        updated_at = now()
    WHERE id = ${input.taskId}
      AND business_id = ${input.companyId}
      AND status <> ${input.status}
    RETURNING id, business_id, title, description, category, status, priority, created_by_profile_id,
              claimed_by, due_at, completed_at, created_at, updated_at
  `;

  if (rows[0]) {
    await createEvent({
      businessId: input.companyId,
      kind: `task.${input.status}`,
      subjectType: "task",
      subjectId: input.taskId,
      payload: { status: input.status }
    });
  }

  return rows[0] ?? null;
}

export async function listCompanyTasks(companyId: string, limit = 30) {
  const sql = db();
  return sql<TaskRow[]>`
    SELECT id, business_id, title, description, category, status, priority, created_by_profile_id,
           claimed_by, due_at, completed_at, created_at, updated_at
    FROM tasks
    WHERE business_id = ${companyId}
    ORDER BY created_at DESC
    LIMIT ${limit}
  `;
}
