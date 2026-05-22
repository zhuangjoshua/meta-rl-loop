import { db } from "./db";
import { createEvent } from "./events";
import { toJson } from "./json";

export type BusinessInboxMessage = {
  id: string;
  business_id: string;
  author_profile_id: string | null;
  author_label: string;
  body: string;
  source: string;
  created_at: string;
};

export async function listInboxMessages(companyId: string, limit = 50) {
  const sql = db();
  return sql<BusinessInboxMessage[]>`
    SELECT id, business_id, author_profile_id, author_label, body, source, created_at
    FROM business_inbox_messages
    WHERE business_id = ${companyId}
    ORDER BY created_at DESC
    LIMIT ${limit}
  `;
}

export async function createInboxMessage(input: {
  companyId: string;
  profileId?: string | null;
  authorLabel: string;
  body: string;
  source?: string;
  forwardToCeo?: boolean;
}) {
  const sql = db();
  const rows = await sql<BusinessInboxMessage[]>`
    INSERT INTO business_inbox_messages (business_id, author_profile_id, author_label, body, source)
    VALUES (${input.companyId}, ${input.profileId ?? null}, ${input.authorLabel}, ${input.body}, ${input.source ?? "dashboard"})
    RETURNING id, business_id, author_profile_id, author_label, body, source, created_at
  `;

  await createEvent({
    businessId: input.companyId,
    actorProfileId: input.profileId ?? null,
    kind: input.forwardToCeo ? "inbox.message_forwarded_to_ceo" : "inbox.message_created",
    subjectType: "business_inbox_message",
    subjectId: rows[0].id,
    payload: { source: input.source ?? "dashboard", forwardToCeo: Boolean(input.forwardToCeo) }
  });

  if (input.forwardToCeo) {
    await sql`
      INSERT INTO workflow_jobs (business_id, profile_id, workflow_id, status, payload, priority, max_attempts)
      VALUES (
        ${input.companyId},
        ${input.profileId ?? null},
        'ceo_wakeup',
        'queued',
        ${sql.json(toJson({ reason: "operator_message", inbox_message_id: rows[0].id }))},
        80,
        2
      )
    `;
  }

  return rows[0];
}
