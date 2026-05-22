import { db } from "./db";
import { ConfigurationError } from "./errors";
import { toJson } from "./json";
import { createOpenAiVideo, defaultSoraModel, defaultSoraSeconds, defaultSoraSize, getOpenAiVideo } from "./vendors/openai-video";

function ugcVideoPrompt(input: { companyName: string; pitch: string; landingUrl: string | null }) {
  return [
    "Create a 9:16 vertical UGC-style product demo video asset.",
    "This is creative generation only; do not launch Meta campaigns or imply paid distribution.",
    "Style: credible software product walkthrough, clean captions, no celebrity likeness, no real-person likeness, no copyrighted characters, no unverifiable claims.",
    `Product: ${input.companyName}`,
    `Pitch: ${input.pitch}`,
    input.landingUrl ? `Landing URL for end card: ${input.landingUrl}` : "No public landing URL yet; omit URL.",
    "Scene 1: name the painful workflow quickly.",
    "Scene 2: show the product making the workflow easier.",
    "Scene 3: show a concrete result screen.",
    "Scene 4: simple CTA to try it."
  ].join("\n");
}

export async function createSoraCreative(input: { businessId: string; campaignId?: string | null }) {
  if (!process.env.OPENAI_API_KEY?.trim()) {
    throw new ConfigurationError("OPENAI_API_KEY is not configured; Sora creative generation is blocked.");
  }
  const sql = db();
  const rows = await sql<{ name: string; public_pitch: string; site_slug: string; public_url: string | null; alias_url: string | null }[]>`
    SELECT b.name, cs.public_pitch, cs.slug AS site_slug,
           gd.deployment_url AS public_url, gd.alias_url
    FROM businesses b
    LEFT JOIN company_sites cs ON cs.business_id = b.id
    LEFT JOIN LATERAL (
      SELECT deployment_url, alias_url
      FROM generated_app_deployments
      WHERE business_id = b.id
        AND status = 'completed'
      ORDER BY created_at DESC
      LIMIT 1
    ) gd ON true
    WHERE b.id = ${input.businessId}
    LIMIT 1
  `;
  const company = rows[0];
  if (!company) throw new Error("Company not found for Sora creative.");
  const prompt = ugcVideoPrompt({
    companyName: company.name,
    pitch: company.public_pitch,
    landingUrl: company.alias_url ?? company.public_url
  });
  const model = defaultSoraModel();
  const seconds = defaultSoraSeconds();
  const size = defaultSoraSize();
  const created = await sql<{ id: string }[]>`
    INSERT INTO media_generation_jobs (business_id, campaign_id, provider, model, status, prompt, input)
    VALUES (
      ${input.businessId},
      ${input.campaignId ?? null},
      'openai',
      ${model},
      'queued',
      ${prompt},
      ${sql.json(toJson({ prompt, model, seconds, size, workflow: "meta_sora_display_only", no_meta_campaign: true, campaign_id: input.campaignId ?? null }))}
    )
    RETURNING id
  `;

  try {
    const submitted = await createOpenAiVideo({
      prompt,
      model,
      seconds,
      size,
      metadata: { business_id: input.businessId, campaign_id: input.campaignId ?? null, media_generation_job_id: created[0].id, workflow: "meta_sora_display_only" }
    });
    const updated = await sql<{ id: string; status: string; provider_job_id: string | null }[]>`
      UPDATE media_generation_jobs
      SET status = ${submitted.status === "failed" ? "failed" : "submitted"},
          provider_job_id = ${submitted.id},
          storage_provider = 'openai_proxy',
          result = ${sql.json(toJson(submitted.raw))},
          submitted_at = now()
      WHERE id = ${created[0].id}
      RETURNING id, status, provider_job_id
    `;
    await sql`
      INSERT INTO growth_variants (business_id, campaign_id, channel, variant_type, name, prompt, payload, status)
      VALUES (
        ${input.businessId},
        ${input.campaignId ?? null},
        'meta_ads',
        'sora_video',
        ${`${company.name} Sora creative`},
        ${prompt},
        ${sql.json(toJson({ media_generation_job_id: updated[0].id, provider_job_id: updated[0].provider_job_id, campaign_id: input.campaignId ?? null, no_meta_campaign: true }))},
        'draft'
      )
    `;
    return updated[0];
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    await sql`
      UPDATE media_generation_jobs
      SET status = 'failed',
          error = ${message}
      WHERE id = ${created[0].id}
    `;
    throw error;
  }
}

export async function syncSoraCreative(input: { businessId: string }) {
  const sql = db();
  const rows = await sql<{ id: string; provider_job_id: string | null }[]>`
    SELECT id, provider_job_id
    FROM media_generation_jobs
    WHERE business_id = ${input.businessId}
      AND provider = 'openai'
      AND status IN ('submitted', 'processing', 'in_progress', 'queued')
    ORDER BY created_at ASC
    LIMIT 3
  `;
  if (rows.some((row) => row.provider_job_id) && !process.env.OPENAI_API_KEY?.trim()) {
    throw new ConfigurationError("OPENAI_API_KEY is not configured; Sora sync is blocked before calling OpenAI.");
  }
  const synced = [];
  for (const row of rows) {
    if (!row.provider_job_id) continue;
    const video = await getOpenAiVideo(row.provider_job_id);
    const status = video.status === "completed" ? "completed" : video.status === "failed" ? "failed" : "processing";
    const outputUrl = status === "completed" ? `/api/media-generation/${row.id}/content` : null;
    const updated = await sql<{ id: string; status: string; output_url: string | null }[]>`
      UPDATE media_generation_jobs
      SET status = ${status},
          output_url = COALESCE(${outputUrl}, output_url),
          stored_url = COALESCE(${outputUrl}, stored_url),
          storage_provider = 'openai_proxy',
          result = ${sql.json(toJson(video.raw))},
          error = ${video.error},
          completed_at = CASE WHEN ${status} IN ('completed', 'failed') THEN now() ELSE completed_at END
      WHERE id = ${row.id}
      RETURNING id, status, output_url
    `;
    synced.push(updated[0]);
  }
  return synced;
}
