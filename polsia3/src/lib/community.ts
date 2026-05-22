import { db } from "./db";
import { upsertBusinessDocument } from "./documents";
import { toJson } from "./json";
import { createLeadCandidate } from "./leads";
import { tavilySearch } from "./vendors/tavily";

function sourceKind(url: string) {
  const host = new URL(url).hostname.replace(/^www\./, "");
  if (host.includes("reddit.com")) return "reddit";
  if (host.includes("producthunt.com")) return "producthunt";
  if (host.includes("indiehackers.com")) return "indiehackers";
  if (host.includes("x.com") || host.includes("twitter.com")) return "x";
  return host;
}

function launchCopy(input: { companyName: string; pitch: string; title: string }) {
  return [
    `I am building ${input.companyName} for this workflow: ${input.pitch}`,
    "",
    `For people around ${input.title}: what would make this worth trying, and what would make you ignore it?`
  ].join("\n");
}

export async function runCommunityResearch(input: { businessId: string }) {
  const sql = db();
  const rows = await sql<{ name: string; public_pitch: string; customer: string | null; pain: string | null }[]>`
    SELECT b.name, cs.public_pitch, cs.config->>'customer' AS customer, cs.config->>'pain' AS pain
    FROM businesses b
    LEFT JOIN company_sites cs ON cs.business_id = b.id
    WHERE b.id = ${input.businessId}
    LIMIT 1
  `;
  const company = rows[0];
  if (!company) throw new Error("Company not found for community research.");
  const query = [company.public_pitch, company.customer, company.pain, "reddit OR producthunt OR indiehackers community"].filter(Boolean).join(" ");
  const results = await tavilySearch({ query, maxResults: 8 });
  if (results.length === 0) throw new Error("No real community targets found from Tavily.");

  const targets = [];
  for (const result of results.slice(0, 8)) {
    const kind = sourceKind(result.url);
    const copy = launchCopy({ companyName: company.name, pitch: company.public_pitch, title: result.title });
    const inserted = await sql<{ id: string }[]>`
      INSERT INTO community_targets (business_id, source, title, url, match_reason, generated_copy, metadata)
      VALUES (
        ${input.businessId},
        ${kind},
        ${result.title},
        ${result.url},
        ${result.content ?? "Search result matched the company brief."},
        ${copy},
        ${sql.json(toJson({ score: result.score ?? null, no_posting: true }))}
      )
      ON CONFLICT (business_id, url) DO UPDATE SET
        title = EXCLUDED.title,
        match_reason = EXCLUDED.match_reason,
        generated_copy = EXCLUDED.generated_copy,
        metadata = community_targets.metadata || EXCLUDED.metadata,
        updated_at = now()
      RETURNING id
    `;
    await createLeadCandidate({
      businessId: input.businessId,
      name: result.title,
      url: result.url,
      source: kind,
      metadata: {
        community_target_id: inserted[0].id,
        match_reason: result.content ?? "Search result matched the company brief.",
        score: result.score ?? null,
        no_posting: true,
        no_fake_email: true
      }
    });
    targets.push({ ...result, id: inserted[0].id, source: kind, generatedCopy: copy });
  }

  await upsertBusinessDocument({
    companyId: input.businessId,
    title: "Community Launch Targets",
    kind: "task_report",
    source: "agent",
    content: [
      "# Community Launch Targets",
      "",
      ...targets.map((target) => `- ${target.title} (${target.url})\n  ${target.generatedCopy}`)
    ].join("\n"),
    metadata: { target_count: targets.length, no_posting: true }
  });

  return { targets: targets.length, leadCandidates: targets.length };
}
