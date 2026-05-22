import { db } from "./db";
import { upsertBusinessDocument } from "./documents";
import { toJson } from "./json";

export async function runOutreachCopy(input: { businessId: string; campaignId?: string | null }) {
  const sql = db();
  const rows = await sql<{ name: string; public_pitch: string }[]>`
    SELECT b.name, cs.public_pitch
    FROM businesses b
    LEFT JOIN company_sites cs ON cs.business_id = b.id
    WHERE b.id = ${input.businessId}
    LIMIT 1
  `;
  const company = rows[0];
  if (!company) throw new Error("Company not found for outreach copy.");
  const targets = await sql<{ title: string; url: string; source: string; generated_copy: string }[]>`
    SELECT title, url, source, generated_copy
    FROM community_targets
    WHERE business_id = ${input.businessId}
    ORDER BY created_at DESC
    LIMIT 8
  `;
  if (targets.length === 0) throw new Error("Outreach copy requires real community targets first.");

  const assets = targets.map((target) => ({
    target: target.title,
    url: target.url,
    source: target.source,
    subject: `${company.name}: useful for this workflow?`,
    body: [
      `Quick note for ${target.title}:`,
      "",
      `${company.name} is live for teams dealing with this problem: ${company.public_pitch}`,
      "",
      target.generated_copy,
      "",
      "A short yes/no or missing-feature reply would be useful."
    ].join("\n")
  }));

  await upsertBusinessDocument({
    companyId: input.businessId,
    title: "Outreach Assets",
    kind: "task_report",
    source: "agent",
    content: [
      "# Outreach Assets",
      "",
      ...assets.map((asset) => `## ${asset.target}\n${asset.url}\n\nSubject: ${asset.subject}\n\n${asset.body}`)
    ].join("\n\n"),
    metadata: { assets: assets.length, no_sending: true, source: "community_targets" }
  });

  await sql`
    INSERT INTO cold_outreach_events (business_id, campaign_id, event_type, channel, recipient, metadata)
    VALUES (${input.businessId}, ${input.campaignId ?? null}, 'copy_generated', 'community', 'not_sent', ${sql.json(toJson({ assets, campaign_id: input.campaignId ?? null }))})
  `;

  return { assets: assets.length };
}
