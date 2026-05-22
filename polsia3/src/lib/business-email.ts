import { db } from "./db";

export type BusinessEmailMessage = {
  id: string;
  business_id: string;
  direction: "outbound" | "inbound";
  from_email: string;
  to_email: string;
  subject: string;
  body_text: string;
  status: string;
  action_id: string | null;
  provider_message_id: string | null;
  result: unknown;
  error: string | null;
  sent_at: string | null;
  lead_id: string | null;
  provider: string | null;
  audience_type: string;
  metadata: unknown;
  created_at: string;
};

export async function listBusinessEmails(businessId: string) {
  const sql = db();
  return sql<BusinessEmailMessage[]>`
    SELECT
      id,
      business_id,
      direction,
      from_email,
      to_email,
      subject,
      body_text,
      status,
      action_id,
      provider_message_id,
      result,
      error,
      sent_at,
      lead_id,
      provider,
      audience_type,
      metadata,
      created_at
    FROM business_email_messages
    WHERE business_id = ${businessId}
    ORDER BY created_at DESC
    LIMIT 100
  `;
}
