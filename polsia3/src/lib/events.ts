import { db } from "./db";
import { toJson } from "./json";

export type EventRow = {
  id: string;
  business_id: string | null;
  actor_profile_id: string | null;
  kind: string;
  subject_type: string | null;
  subject_id: string | null;
  payload: unknown;
  created_at: string;
};

export async function createEvent(input: {
  businessId?: string | null;
  actorProfileId?: string | null;
  kind: string;
  subjectType?: string | null;
  subjectId?: string | null;
  payload?: Record<string, unknown>;
}) {
  const sql = db();
  const rows = await sql<EventRow[]>`
    INSERT INTO events (business_id, actor_profile_id, kind, subject_type, subject_id, payload)
    VALUES (
      ${input.businessId ?? null},
      ${input.actorProfileId ?? null},
      ${input.kind},
      ${input.subjectType ?? null},
      ${input.subjectId ?? null},
      ${sql.json(toJson(input.payload ?? {}))}
    )
    RETURNING id, business_id, actor_profile_id, kind, subject_type, subject_id, payload, created_at
  `;
  return rows[0];
}

export async function listCompanyEvents(companyId: string, limit = 50) {
  const sql = db();
  return sql<EventRow[]>`
    SELECT id, business_id, actor_profile_id, kind, subject_type, subject_id, payload, created_at
    FROM events
    WHERE business_id = ${companyId}
    ORDER BY created_at DESC
    LIMIT ${limit}
  `;
}
