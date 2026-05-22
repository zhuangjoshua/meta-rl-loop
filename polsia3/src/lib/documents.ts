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

export async function seedFoundationDocuments(input: {
  companyId: string;
  title: string;
  pitch: string;
  customer?: string;
  pain?: string;
  offer?: string;
}) {
  // Deprecated for Build Company. Real Mission/Market Research docs must be written
  // by the foundation workflow only after evidence-backed execution finishes.
  const mission = [
    `# Mission`,
    ``,
    `## Public Title`,
    input.title,
    ``,
    `## One-Liner`,
    input.pitch,
    ``,
    `## Customer`,
    input.customer || "To be sharpened during research.",
    ``,
    `## Pain`,
    input.pain || "To be sharpened during research.",
    ``,
    `## Offer`,
    input.offer || "To be sharpened during product planning.",
    ``,
    `## First Workflow`,
    "Build website first, then run product/auth/payments/add-on lanes independently."
  ].join("\n");

  const research = [
    `# Market Research`,
    ``,
    `## Summary`,
    `Initial research queued from the operator brief. The local worker must replace this with sourced evidence before marking research complete.`,
    ``,
    `## Buying Intent`,
    `Blocked until research workflow collects evidence.`,
    ``,
    `## Competitors`,
    `Blocked until research workflow collects evidence.`,
    ``,
    `## Evidence`,
    `No evidence gathered yet.`
  ].join("\n");

  const missionDoc = await upsertBusinessDocument({
    companyId: input.companyId,
    title: "Mission",
    kind: "mission",
    content: mission,
    source: "system",
    metadata: { seeded: true }
  });

  const researchDoc = await upsertBusinessDocument({
    companyId: input.companyId,
    title: "Market Research",
    kind: "research_report",
    content: research,
    source: "system",
    metadata: { seeded: true, status: "blocked_until_research_runs" }
  });

  return { missionDoc, researchDoc };
}
