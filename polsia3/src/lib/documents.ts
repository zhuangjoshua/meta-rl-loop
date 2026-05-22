import { db } from "./db";
import { createEvent } from "./events";
import { toJson } from "./json";

export type BusinessDocumentKind =
  | "mission"
  | "research_report"
  | "daily_report"
  | "task_report"
  | "website_brief"
  | "document";

export type BusinessDocumentSource = "agent" | "workflow" | "system" | "operator";

export type BusinessDocumentRow = {
  id: string;
  business_id: string;
  title: string;
  kind: BusinessDocumentKind;
  content: string;
  source: BusinessDocumentSource;
  metadata: unknown;
  created_at: string;
  updated_at: string;
};

export async function listBusinessDocuments(companyId: string, limit = 100) {
  const sql = db();
  return sql<BusinessDocumentRow[]>`
    SELECT id, business_id, title, kind, content, source, metadata, created_at, updated_at
    FROM business_documents
    WHERE business_id = ${companyId}
    ORDER BY created_at DESC
    LIMIT ${limit}
  `;
}

export async function upsertBusinessDocument(input: {
  companyId: string;
  title: string;
  kind?: BusinessDocumentKind;
  content: string;
  source?: BusinessDocumentSource;
  metadata?: Record<string, unknown>;
  replaceMetadata?: boolean;
}) {
  const sql = db();
  const rows = await sql<BusinessDocumentRow[]>`
    INSERT INTO business_documents (business_id, title, kind, content, source, metadata)
    VALUES (
      ${input.companyId},
      ${input.title},
      ${input.kind ?? "document"},
      ${input.content},
      ${input.source ?? "agent"},
      ${sql.json(toJson(input.metadata ?? {}))}
    )
    ON CONFLICT (business_id, title)
    DO UPDATE SET
      kind = EXCLUDED.kind,
      content = EXCLUDED.content,
      source = EXCLUDED.source,
      metadata = CASE
        WHEN ${input.replaceMetadata ?? false} THEN EXCLUDED.metadata
        ELSE business_documents.metadata || EXCLUDED.metadata
      END,
      updated_at = now()
    RETURNING id, business_id, title, kind, content, source, metadata, created_at, updated_at
  `;

  await createEvent({
    businessId: input.companyId,
    kind: "document.saved",
    subjectType: "business_document",
    subjectId: rows[0].id,
    payload: { title: input.title, kind: input.kind ?? "document", source: input.source ?? "agent" }
  });

  return rows[0];
}
