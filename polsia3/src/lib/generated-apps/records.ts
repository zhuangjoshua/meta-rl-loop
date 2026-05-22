import crypto from "node:crypto";
import { db } from "../db";
import { getProductAiPolicyEnv } from "../env";
import { toJson } from "../json";

export type GeneratedAppBuildRow = {
  id: string;
  business_id: string;
  workflow_job_id: string | null;
  status: "queued" | "running" | "completed" | "blocked" | "failed" | "cancelled";
  source_dir: string;
  manifest: unknown;
  install_log: string | null;
  typecheck_log: string | null;
  build_log: string | null;
  smoke_log: string | null;
  error: string | null;
  started_at: string | null;
  completed_at: string | null;
  created_at: string;
  updated_at: string;
};

export type CompanyBuildInput = {
  id: string;
  name: string;
  slug: string;
  public_pitch: string;
  customer?: string;
  pain?: string;
  offer?: string;
  template?: string;
};

export async function getCompanyBuildInput(companyId: string) {
  const sql = db();
  const rows = await sql<CompanyBuildInput[]>`
    SELECT b.id, b.name, b.slug, cs.public_pitch,
           NULLIF(cs.config->>'customer', '') AS customer,
           NULLIF(cs.config->>'pain', '') AS pain,
           NULLIF(cs.config->>'offer', '') AS offer,
           cs.config->>'template' AS template
    FROM businesses b
    LEFT JOIN company_sites cs ON cs.business_id = b.id
    WHERE b.id = ${companyId}
    LIMIT 1
  `;
  return rows[0] ?? null;
}

export async function ensureGeneratedAppRails(companyId: string) {
  const sql = db();
  const productAi = getProductAiPolicyEnv();
  await sql.begin(async (tx) => {
    await tx`
      INSERT INTO generated_app_plan_policies (
        business_id,
        plan_key,
        tier,
        price_usd_cents,
        billing_interval,
        included_ai_budget_microusd,
        included_action_quota,
        source,
        metadata
      )
      VALUES (${companyId}, 'free', 'free', 0, 'month', 100000, 25, 'agent_suggested', '{"title":"Free"}'::jsonb)
      ON CONFLICT (business_id, plan_key) DO NOTHING
    `;

    await tx`
      INSERT INTO generated_app_plan_policies (
        business_id,
        plan_key,
        tier,
        price_usd_cents,
        billing_interval,
        included_ai_budget_microusd,
        included_action_quota,
        source,
        metadata
      )
      VALUES (${companyId}, 'starter', 'paid', 1900, 'month', 15000000, 500, 'agent_suggested', '{"title":"Starter"}'::jsonb)
      ON CONFLICT (business_id, plan_key) DO NOTHING
    `;

    await tx`
      INSERT INTO project_ai_wallets (business_id, status, hard_limit_microusd, current_period_start, current_period_end)
      VALUES (${companyId}, 'active', 5000000, date_trunc('month', now()), date_trunc('month', now()) + interval '1 month')
      ON CONFLICT (business_id) DO NOTHING
    `;

    await tx`
      INSERT INTO project_ai_model_policies (
        business_id,
        purpose,
        provider,
        model,
        quality_tier,
        allowed,
        max_input_tokens,
        max_output_tokens,
        max_estimated_cost_microusd,
        metadata
      )
      VALUES (
        ${companyId},
        'product',
        ${productAi.provider},
        ${productAi.model},
        ${productAi.qualityTier},
        true,
        12000,
        2200,
        3000000,
        '{"source":"default_generated_app_rails","tier":"frontier_default"}'::jsonb
      )
      ON CONFLICT (business_id, purpose) DO UPDATE SET
        provider = EXCLUDED.provider,
        model = EXCLUDED.model,
        quality_tier = EXCLUDED.quality_tier,
        max_input_tokens = EXCLUDED.max_input_tokens,
        max_output_tokens = EXCLUDED.max_output_tokens,
        max_estimated_cost_microusd = EXCLUDED.max_estimated_cost_microusd,
        metadata = project_ai_model_policies.metadata || EXCLUDED.metadata,
        updated_at = now()
    `;
  });
}

export async function ensureProjectAiProxyKey(companyId: string) {
  const sql = db();
  const secret = `takyon_${crypto.randomBytes(30).toString("base64url")}`;
  const hash = crypto.createHash("sha256").update(secret).digest("hex");
  await sql`
    INSERT INTO project_ai_proxy_keys (business_id, name, key_hash, key_prefix, last_four, status, metadata)
    VALUES (${companyId}, 'Generated app runtime', ${hash}, ${secret.slice(0, 14)}, ${secret.slice(-4)}, 'active', '{"source":"v3_generated_app_build"}'::jsonb)
  `;
  return secret;
}

export async function verifyProjectAiKey(rawKey: string) {
  const sql = db();
  const hash = crypto.createHash("sha256").update(rawKey).digest("hex");
  const rows = await sql<{ id: string; business_id: string; key_prefix: string }[]>`
    SELECT id, business_id, key_prefix
    FROM project_ai_proxy_keys
    WHERE key_hash = ${hash}
      AND status = 'active'
    LIMIT 1
  `;
  if (rows[0]) {
    await sql`
      UPDATE project_ai_proxy_keys
      SET last_used_at = now()
      WHERE id = ${rows[0].id}
    `;
  }
  return rows[0] ?? null;
}

export async function createGeneratedAppBuild(input: {
  companyId: string;
  workflowJobId?: string | null;
  sourceDir: string;
  manifest: Record<string, unknown>;
}) {
  const sql = db();
  const rows = await sql<GeneratedAppBuildRow[]>`
    INSERT INTO generated_app_builds (business_id, workflow_job_id, status, source_dir, manifest, started_at)
    VALUES (${input.companyId}, ${input.workflowJobId ?? null}, 'running', ${input.sourceDir}, ${sql.json(toJson(input.manifest))}, now())
    RETURNING id, business_id, workflow_job_id, status, source_dir, manifest, install_log, typecheck_log,
              build_log, smoke_log, error, started_at, completed_at, created_at, updated_at
  `;
  return rows[0];
}

export async function updateGeneratedAppBuildManifest(input: {
  buildId: string;
  manifest: Record<string, unknown>;
}) {
  const sql = db();
  const rows = await sql<GeneratedAppBuildRow[]>`
    UPDATE generated_app_builds
    SET manifest = ${sql.json(toJson(input.manifest))},
        updated_at = now()
    WHERE id = ${input.buildId}
    RETURNING id, business_id, workflow_job_id, status, source_dir, manifest, install_log, typecheck_log,
              build_log, smoke_log, error, started_at, completed_at, created_at, updated_at
  `;
  return rows[0] ?? null;
}

export async function getLatestGeneratedAppBuild(companyId: string) {
  const sql = db();
  const rows = await sql<GeneratedAppBuildRow[]>`
    SELECT id, business_id, workflow_job_id, status, source_dir, manifest, install_log, typecheck_log,
           build_log, smoke_log, error, started_at, completed_at, created_at, updated_at
    FROM generated_app_builds
    WHERE business_id = ${companyId}
    ORDER BY created_at DESC
    LIMIT 1
  `;
  return rows[0] ?? null;
}

export async function recordBuildStep(input: {
  buildId: string;
  stepKey: string;
  status: "running" | "completed" | "blocked" | "failed";
  log?: string | null;
  error?: string | null;
}) {
  const sql = db();
  await sql`
    INSERT INTO generated_app_build_steps (build_id, step_key, status, log, error, started_at, completed_at)
    VALUES (
      ${input.buildId},
      ${input.stepKey},
      ${input.status},
      ${input.log ?? null},
      ${input.error ?? null},
      now(),
      CASE WHEN ${input.status} IN ('completed', 'blocked', 'failed') THEN now() ELSE NULL END
    )
    ON CONFLICT (build_id, step_key)
    DO UPDATE SET status = EXCLUDED.status, log = EXCLUDED.log, error = EXCLUDED.error, completed_at = EXCLUDED.completed_at
  `;
}

export async function finishGeneratedAppBuild(input: {
  buildId: string;
  status: "completed" | "blocked" | "failed" | "cancelled";
  installLog?: string;
  typecheckLog?: string;
  buildLog?: string;
  smokeLog?: string;
  error?: string | null;
}) {
  const sql = db();
  const rows = await sql<GeneratedAppBuildRow[]>`
    UPDATE generated_app_builds
    SET status = ${input.status},
        install_log = ${input.installLog ?? null},
        typecheck_log = ${input.typecheckLog ?? null},
        build_log = ${input.buildLog ?? null},
        smoke_log = ${input.smokeLog ?? null},
        error = ${input.error ?? null},
        completed_at = now(),
        updated_at = now()
    WHERE id = ${input.buildId}
    RETURNING id, business_id, workflow_job_id, status, source_dir, manifest, install_log, typecheck_log,
              build_log, smoke_log, error, started_at, completed_at, created_at, updated_at
  `;
  return rows[0] ?? null;
}

export async function upsertRuntimeManifest(input: {
  companyId: string;
  activeBuildId?: string | null;
  websiteStatus?: string;
  productStatus?: string;
  publicUrl?: string | null;
  aliasUrl?: string | null;
  config?: Record<string, unknown>;
}) {
  const sql = db();
  await sql.begin(async (tx) => {
    await tx`
      INSERT INTO generated_app_runtime_manifests (
        business_id,
        generated_app_run_id,
        runtime,
        npm_packages,
        required_capabilities,
        setup_required_capabilities,
        notes,
        metadata
      )
      VALUES (
        ${input.companyId},
        ${input.activeBuildId ?? `manifest-${Date.now()}`},
        'nextjs-local-worker',
        ${tx.array(["next", "react", "react-dom", "zod"], 25)},
        ${tx.array(["product_api", "platform_product_runs", "generated_app_subusers", "project_ai_key"], 25)},
        ${tx.array(input.productStatus === "queued" ? ["product_lane"] : [], 25)},
        ${`website=${input.websiteStatus ?? "draft"} product=${input.productStatus ?? "queued"}`},
        ${tx.json(
          toJson({
            active_build_id: input.activeBuildId ?? null,
            website_status: input.websiteStatus ?? "draft",
            product_status: input.productStatus ?? "queued",
            public_url: input.publicUrl ?? null,
            alias_url: input.aliasUrl ?? null,
            config: input.config ?? {}
          })
        )}
      )
    `;

    await tx`
      UPDATE company_sites
      SET status = CASE
          WHEN ${input.websiteStatus ?? "draft"} = 'published' THEN 'published'
          WHEN ${input.websiteStatus ?? "draft"} = 'failed' THEN 'failed'
          WHEN ${input.websiteStatus ?? "draft"} = 'blocked' THEN 'blocked'
          ELSE status
        END,
        config = config || ${tx.json(
          toJson({
            generated_app: {
              active_build_id: input.activeBuildId ?? null,
              website_status: input.websiteStatus ?? "draft",
              product_status: input.productStatus ?? "queued",
              public_url: input.publicUrl ?? null,
              alias_url: input.aliasUrl ?? null
            }
          })
        )},
        updated_at = now()
      WHERE business_id = ${input.companyId}
    `;
  });
}

export async function createGeneratedAppDeployment(input: {
  companyId: string;
  buildId: string;
  status: "running" | "completed" | "blocked" | "failed";
  deploymentUrl?: string | null;
  aliasUrl?: string | null;
  healthStatus?: string | null;
  receipt?: Record<string, unknown>;
  error?: string | null;
}) {
  const sql = db();
  const rows = await sql<{ id: string }[]>`
    INSERT INTO generated_app_deployments (
      business_id,
      build_id,
      status,
      deployment_url,
      alias_url,
      health_status,
      health_checked_at,
      receipt,
      error
    )
    VALUES (
      ${input.companyId},
      ${input.buildId},
      ${input.status},
      ${input.deploymentUrl ?? null},
      ${input.aliasUrl ?? null},
      ${input.healthStatus ?? null},
      ${input.healthStatus ? new Date() : null},
      ${sql.json(toJson(input.receipt ?? {}))},
      ${input.error ?? null}
    )
    RETURNING id
  `;
  return rows[0];
}
