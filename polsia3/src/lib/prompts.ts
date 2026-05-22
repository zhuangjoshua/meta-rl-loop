import { db } from "./db";

export const requiredPromptSeeds = [
  ["ceo_wakeup", "CEO Wakeup"],
  ["initial_foundation", "Initial Foundation"],
  ["business_plan", "Business Plan"],
  ["market_research", "Market Research"],
  ["workflow_skill_envelope", "Workflow Skill Envelope"],
  ["product_build", "Product Build"],
  ["product_improve", "Product Improve"],
  ["social_draft", "Social Copy"],
  ["find_leads", "Find Leads"],
  ["outreach_copy", "Outreach Copy"],
  ["cold_outreach_cycle", "Cold Outreach Cycle"],
  ["content_generation", "Content Generation"],
  ["support_reply", "Support Reply"],
  ["activity_review", "Activity Review"],
  ["data_report", "Data Report"],
  ["operations_monitor", "Operations Monitor"],
  ["ugc_video", "UGC Video"],
  ["meta_creative", "Meta Creative"],
  ["browser_site_policy", "Browser Site Policy"],
  ["generated_app_ai_policy", "Generated App AI Policy"]
] as const;

export type PromptRow = {
  id: string;
  prompt_key: string;
  title: string;
  functionality: string;
  active_version_id: string | null;
  created_at: string;
  updated_at: string;
};

export type PromptVersionRow = {
  id: string;
  prompt_id: string;
  version: number;
  content: string;
  change_note: string;
  edited_by_profile_id: string | null;
  created_at: string;
};

export async function seedRequiredPrompts() {
  const sql = db();
  for (const [promptKey, title] of requiredPromptSeeds) {
    await sql`
      INSERT INTO prompts (prompt_key, title, functionality)
      VALUES (${promptKey}, ${title}, ${`Default editable prompt for ${title}.`})
      ON CONFLICT (prompt_key) DO NOTHING
    `;

    const prompts = await sql<PromptRow[]>`
      SELECT id, prompt_key, title, functionality, active_version_id, created_at, updated_at
      FROM prompts
      WHERE prompt_key = ${promptKey}
      LIMIT 1
    `;
    const prompt = prompts[0];
    if (!prompt.active_version_id) {
      const versions = await sql<PromptVersionRow[]>`
        INSERT INTO prompt_versions (prompt_id, version, content, change_note)
        VALUES (${prompt.id}, 1, ${`You are running the ${title} workflow. Follow run/ contracts and never fake success.`}, 'seed')
        RETURNING id, prompt_id, version, content, change_note, edited_by_profile_id, created_at
      `;
      await sql`
        UPDATE prompts
        SET active_version_id = ${versions[0].id}
        WHERE id = ${prompt.id}
      `;
    }
  }
}

export async function getActivePrompt(promptKey: string) {
  const sql = db();
  const rows = await sql<(PromptRow & { version_id: string; version: number; content: string })[]>`
    SELECT p.id, p.prompt_key, p.title, p.functionality, p.active_version_id, p.created_at, p.updated_at,
           pv.id AS version_id, pv.version, pv.content
    FROM prompts p
    JOIN prompt_versions pv ON pv.id = p.active_version_id
    WHERE p.prompt_key = ${promptKey}
    LIMIT 1
  `;
  return rows[0] ?? null;
}
