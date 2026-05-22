import { spawn } from "node:child_process";
import fs from "node:fs/promises";
import path from "node:path";
import { getAppEnv, getVercelEnv } from "../env";
import { createEvent } from "../events";
import { runClaudeSdkSurfaceBuilder } from "./surface-builder";
import { writeGeneratedAppTemplate } from "./template";
import {
  createGeneratedAppDeployment,
  createGeneratedAppBuild,
  ensureGeneratedAppRails,
  ensureProjectAiProxyKey,
  finishGeneratedAppBuild,
  getCompanyBuildInput,
  recordBuildStep,
  updateGeneratedAppBuildManifest,
  upsertRuntimeManifest
} from "./records";

type CommandResult = {
  code: number | null;
  output: string;
};

function runCommand(
  command: string,
  args: string[],
  cwd: string,
  timeoutMs = 120_000,
  env: NodeJS.ProcessEnv = process.env
): Promise<CommandResult> {
  return new Promise((resolve) => {
    const child = spawn(command, args, { cwd, env });
    let output = "";
    const timer = setTimeout(() => {
      child.kill("SIGTERM");
      output += "\nCommand timed out.";
    }, timeoutMs);

    child.stdout.on("data", (chunk) => {
      output += chunk.toString();
    });
    child.stderr.on("data", (chunk) => {
      output += chunk.toString();
    });
    child.on("close", (code) => {
      clearTimeout(timer);
      resolve({ code, output: output.slice(-12000) });
    });
  });
}

async function runGate(buildId: string, stepKey: string, command: string, args: string[], cwd: string, timeoutMs?: number) {
  await recordBuildStep({ buildId, stepKey, status: "running" });
  const result = await runCommand(command, args, cwd, timeoutMs);
  if (result.code !== 0) {
    await recordBuildStep({ buildId, stepKey, status: "failed", log: result.output, error: `${stepKey} exited ${result.code}` });
    throw new Error(`${stepKey} failed.`);
  }
  await recordBuildStep({ buildId, stepKey, status: "completed", log: result.output });
  return result.output;
}

function escapeJsxText(value: string) {
  return value
    .replace(/&(?!(?:amp|lt|gt|quot|apos|#\d+|#x[0-9a-fA-F]+);)/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}

async function sanitizeGeneratedTsxText(rootDir: string) {
  const sourceRoot = path.join(rootDir, "src");
  const files = (await listSourceFiles(sourceRoot).catch(() => []))
    .filter((file) => file.endsWith(".tsx"))
    .map((file) => path.join(sourceRoot, file));

  for (const file of files) {
    const original = await fs.readFile(file, "utf8");
    const sanitized = original.replace(/<code>([^<{}`]+)<\/code>/g, (_match, inner: string) => `<code>${escapeJsxText(inner)}</code>`);
    if (sanitized !== original) {
      await fs.writeFile(file, sanitized);
    }
  }
}

function sleep(ms: number) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function checkHttpHealth(url: string, attempts = 6) {
  let lastStatus = "not_checked";
  let lastError: string | null = null;
  for (let attempt = 1; attempt <= attempts; attempt += 1) {
    try {
      const response = await fetch(url, { cache: "no-store" });
      lastStatus = `${response.status}`;
      lastError = null;
      if (response.ok) {
        return { ok: true, status: lastStatus, error: null };
      }
    } catch (error) {
      lastStatus = "fetch_failed";
      lastError = error instanceof Error ? error.message : "fetch failed";
    }
    if (attempt < attempts) await sleep(5000);
  }
  return { ok: false, status: lastStatus, error: lastError ?? "Deployment health check failed." };
}

async function applyGeneratedWebsiteSurface(input: {
  buildId: string;
  rootDir: string;
  company: Awaited<ReturnType<typeof getCompanyBuildInput>> & {};
  workflow: "build_site";
  operatorInstruction?: string | null;
}) {
  await recordBuildStep({ buildId: input.buildId, stepKey: "surface_builder", status: "running" });
  try {
    const sdk = await runClaudeSdkSurfaceBuilder({
      rootDir: input.rootDir,
      company: input.company,
      workflow: input.workflow,
      operatorInstruction: input.operatorInstruction ?? null
    });
    await recordBuildStep({
      buildId: input.buildId,
      stepKey: "surface_builder",
      status: "completed",
      log: sdk.summary
    });
    return sdk;
  } catch (error) {
    await recordBuildStep({
      buildId: input.buildId,
      stepKey: "surface_builder",
      status: "failed",
      error: error instanceof Error ? error.message : String(error)
    });
    throw error;
  }
}

export async function buildGeneratedWebsite(input: { companyId: string; workflowJobId?: string | null; operatorInstruction?: string | null }) {
  const company = await getCompanyBuildInput(input.companyId);
  if (!company) {
    throw new Error("Company not found for generated app build.");
  }

  await ensureGeneratedAppRails(input.companyId);
  const projectAiKey = await ensureProjectAiProxyKey(input.companyId);
  const appEnv = getAppEnv();
  const workspaceRoot = path.join(process.cwd(), ".takyon", "generated", company.id);
  await fs.mkdir(path.dirname(workspaceRoot), { recursive: true });

  const manifest = await writeGeneratedAppTemplate(workspaceRoot, {
    company,
    platformUrl: appEnv.APP_URL,
    projectAiKey
  });

  let generatedManifest = {
    ...manifest,
    files: await listSourceFiles(workspaceRoot),
    surface_builder: { source: "pending" }
  };
  const build = await createGeneratedAppBuild({
    companyId: input.companyId,
    workflowJobId: input.workflowJobId ?? null,
    sourceDir: workspaceRoot,
    manifest: generatedManifest
  });

  let installLog = "";
  let typecheckLog = "";
  let buildLog = "";

  try {
    const surface = await applyGeneratedWebsiteSurface({
      buildId: build.id,
      rootDir: workspaceRoot,
      company,
      workflow: "build_site",
      operatorInstruction: input.operatorInstruction ?? null
    });
    await sanitizeGeneratedTsxText(workspaceRoot);
    generatedManifest = {
      ...manifest,
      files: await listSourceFiles(workspaceRoot),
      surface_builder: surface
    };
    await updateGeneratedAppBuildManifest({ buildId: build.id, manifest: generatedManifest });

    installLog = await runGate(build.id, "install", "npm", ["install"], workspaceRoot, 180_000);
    typecheckLog = await runGate(build.id, "typecheck", "npm", ["run", "typecheck"], workspaceRoot, 120_000);
    buildLog = await runGate(build.id, "build", "npm", ["run", "build"], workspaceRoot, 180_000);

    const deployment = await deployGeneratedApp({
      companyId: input.companyId,
      buildId: build.id,
      slug: company.slug,
      sourceDir: workspaceRoot,
      platformUrl: appEnv.APP_URL,
      projectAiKey
    });

    await finishGeneratedAppBuild({
      buildId: build.id,
      status: deployment.status === "completed" ? "completed" : "blocked",
      installLog,
      typecheckLog,
      buildLog,
      smokeLog: deployment.healthStatus ?? "Deployment blocked before health check.",
      error: deployment.status === "completed" ? null : deployment.error
    });

    await upsertRuntimeManifest({
      companyId: input.companyId,
      activeBuildId: build.id,
      websiteStatus: deployment.status === "completed" ? "published" : "blocked",
      productStatus: "queued",
      publicUrl: deployment.deploymentUrl,
      aliasUrl: deployment.aliasUrl,
      config: {
        source_dir: workspaceRoot,
        generated_files: generatedManifest.files,
        surface_builder: generatedManifest.surface_builder,
        project_ai_key_created: Boolean(projectAiKey),
        deployment
      }
    });

    await createEvent({
      businessId: input.companyId,
      kind: deployment.status === "completed" ? "generated_app.website_deployed" : "generated_app.website_blocked",
      subjectType: "generated_app_build",
      subjectId: build.id,
      payload: { source_dir: workspaceRoot, files: generatedManifest.files.length, surface_builder: generatedManifest.surface_builder, deployment }
    });

    return { buildId: build.id, sourceDir: workspaceRoot, projectAiKeyCreated: Boolean(projectAiKey), deployment };
  } catch (error) {
    const message = error instanceof Error ? error.message : "Generated app build failed.";
    await finishGeneratedAppBuild({
      buildId: build.id,
      status: "failed",
      installLog,
      typecheckLog,
      buildLog,
      error: message
    });
    await upsertRuntimeManifest({
      companyId: input.companyId,
      activeBuildId: build.id,
      websiteStatus: "failed",
      productStatus: "queued",
      config: { source_dir: workspaceRoot, generated_files: generatedManifest.files, surface_builder: generatedManifest.surface_builder }
    });
    throw error;
  }
}

async function listSourceFiles(rootDir: string, current = rootDir): Promise<string[]> {
  const entries = await fs.readdir(current, { withFileTypes: true });
  const files: string[] = [];
  for (const entry of entries) {
    if (["node_modules", ".next", ".vercel"].includes(entry.name)) continue;
    const absolute = path.join(current, entry.name);
    if (entry.isDirectory()) {
      files.push(...(await listSourceFiles(rootDir, absolute)));
    } else {
      files.push(path.relative(rootDir, absolute));
    }
  }
  return files.sort();
}

async function readExistingManifest(sourceDir: string) {
  try {
    const raw = await fs.readFile(path.join(sourceDir, "takyon-manifest.json"), "utf8");
    const parsed = JSON.parse(raw) as Record<string, unknown>;
    return {
      ...parsed,
      files: await listSourceFiles(sourceDir)
    };
  } catch {
    return {
      version: 1,
      files: await listSourceFiles(sourceDir)
    };
  }
}

export async function buildAndDeployExistingGeneratedApp(input: {
  companyId: string;
  workflowJobId?: string | null;
  sourceDir: string;
  productStatus?: string;
}) {
  const company = await getCompanyBuildInput(input.companyId);
  if (!company) {
    throw new Error("Company not found for generated app build.");
  }

  await ensureGeneratedAppRails(input.companyId);
  const projectAiKey = await ensureProjectAiProxyKey(input.companyId);
  const appEnv = getAppEnv();
  const manifest = await readExistingManifest(input.sourceDir);
  await sanitizeGeneratedTsxText(input.sourceDir);
  const build = await createGeneratedAppBuild({
    companyId: input.companyId,
    workflowJobId: input.workflowJobId ?? null,
    sourceDir: input.sourceDir,
    manifest
  });

  let installLog = "";
  let typecheckLog = "";
  let buildLog = "";

  try {
    installLog = await runGate(build.id, "install", "npm", ["install"], input.sourceDir, 180_000);
    typecheckLog = await runGate(build.id, "typecheck", "npm", ["run", "typecheck"], input.sourceDir, 120_000);
    buildLog = await runGate(build.id, "build", "npm", ["run", "build"], input.sourceDir, 180_000);

    const deployment = await deployGeneratedApp({
      companyId: input.companyId,
      buildId: build.id,
      slug: company.slug,
      sourceDir: input.sourceDir,
      platformUrl: appEnv.APP_URL,
      projectAiKey
    });

    await finishGeneratedAppBuild({
      buildId: build.id,
      status: deployment.status === "completed" ? "completed" : "blocked",
      installLog,
      typecheckLog,
      buildLog,
      smokeLog: deployment.healthStatus ?? "Deployment blocked before health check.",
      error: deployment.status === "completed" ? null : deployment.error
    });

    await upsertRuntimeManifest({
      companyId: input.companyId,
      activeBuildId: build.id,
      websiteStatus: deployment.status === "completed" ? "published" : "blocked",
      productStatus: deployment.status === "completed" ? input.productStatus ?? "published" : "blocked",
      publicUrl: deployment.deploymentUrl,
      aliasUrl: deployment.aliasUrl,
      config: {
        source_dir: input.sourceDir,
        generated_files: manifest.files,
        product_status: deployment.status === "completed" ? input.productStatus ?? "published" : "blocked",
        project_ai_key_created: Boolean(projectAiKey),
        deployment
      }
    });

    return { buildId: build.id, sourceDir: input.sourceDir, projectAiKeyCreated: Boolean(projectAiKey), deployment };
  } catch (error) {
    const message = error instanceof Error ? error.message : "Generated app build failed.";
    await finishGeneratedAppBuild({
      buildId: build.id,
      status: "failed",
      installLog,
      typecheckLog,
      buildLog,
      error: message
    });
    await upsertRuntimeManifest({
      companyId: input.companyId,
      activeBuildId: build.id,
      websiteStatus: "failed",
      productStatus: "failed",
      config: { source_dir: input.sourceDir }
    });
    throw error;
  }
}

async function deployGeneratedApp(input: {
  companyId: string;
  buildId: string;
  slug: string;
  sourceDir: string;
  platformUrl: string;
  projectAiKey: string | null;
}) {
  const deployFlag = (process.env.ARGON_BUILDER_DEPLOY || "").toLowerCase();
  const deployDisabled = ["0", "false", "no", "off"].includes(deployFlag);
  if (deployDisabled) {
    await createGeneratedAppDeployment({
      companyId: input.companyId,
      buildId: input.buildId,
      status: "blocked",
      error: "ARGON_BUILDER_DEPLOY explicitly disabled deployment."
    });
    return {
      status: "blocked" as const,
      deploymentUrl: null,
      aliasUrl: null,
      healthStatus: null,
      error: "ARGON_BUILDER_DEPLOY explicitly disabled deployment."
    };
  }

  if (!input.projectAiKey) {
    await createGeneratedAppDeployment({
      companyId: input.companyId,
      buildId: input.buildId,
      status: "blocked",
      error: "A raw project AI key was not available for this build."
    });
    return {
      status: "blocked" as const,
      deploymentUrl: null,
      aliasUrl: null,
      healthStatus: null,
      error: "A raw project AI key was not available for this build."
    };
  }

  const configuredVercel = getVercelEnv();

  const deployArgs = [
    "deploy",
    "--prod",
    "--yes",
    "--force",
    "--env",
    `TAKYON_PLATFORM_URL=${input.platformUrl}`,
    "--env",
    `ARGON_PROJECT_AI_KEY=${input.projectAiKey}`
  ];
  deployArgs.push("--token", configuredVercel.VERCEL_TOKEN);

  const vercelEnv = {
    ...process.env,
    VERCEL_ORG_ID: process.env.VERCEL_ORG_ID || configuredVercel.VERCEL_TEAM_ID
  };

  const deploy = await runCommand("vercel", deployArgs, input.sourceDir, 240_000, vercelEnv);
  if (deploy.code !== 0) {
    await createGeneratedAppDeployment({
      companyId: input.companyId,
      buildId: input.buildId,
      status: "failed",
      receipt: { deploy_log: deploy.output },
      error: "Vercel deploy failed."
    });
    throw new Error("Vercel deploy failed.");
  }

  const deploymentUrl = deploy.output
    .split(/\s+/)
    .map((part) => part.trim())
    .find((part) => /^https:\/\/[^\s]+\.vercel\.app$/.test(part));
  if (!deploymentUrl) {
    await createGeneratedAppDeployment({
      companyId: input.companyId,
      buildId: input.buildId,
      status: "failed",
      receipt: { deploy_log: deploy.output },
      error: "Vercel deploy did not return a deployment URL."
    });
    throw new Error("Vercel deploy did not return a deployment URL.");
  }

  let aliasUrl: string | null = null;
  const baseDomain = process.env.PUBLIC_COMPANY_BASE_DOMAIN || "fourmanifold.com";
  const aliasHost = `${input.slug}.${baseDomain}`;
  const aliasArgs = ["alias", "set", deploymentUrl, aliasHost];
  aliasArgs.push("--token", configuredVercel.VERCEL_TOKEN);
  const alias = await runCommand("vercel", aliasArgs, input.sourceDir, 120_000, vercelEnv);
  if (alias.code === 0) {
    aliasUrl = `https://${aliasHost}`;
  }

  let healthTarget = aliasUrl ?? deploymentUrl;
  let health = await checkHttpHealth(healthTarget);
  if (!health.ok && aliasUrl) {
    healthTarget = deploymentUrl;
    health = await checkHttpHealth(deploymentUrl);
    aliasUrl = null;
  }

  const healthStatus = health.status;
  const status = health.ok ? "completed" : "failed";
  await createGeneratedAppDeployment({
    companyId: input.companyId,
    buildId: input.buildId,
    status,
    deploymentUrl,
    aliasUrl,
    healthStatus,
    receipt: {
      deployment_url: deploymentUrl,
      alias_url: aliasUrl,
      alias_log: alias.output,
      health_target: healthTarget,
      health_status: healthStatus
    },
    error: health.ok ? null : health.error ?? "Deployment health check failed."
  });

  if (!health.ok) {
    throw new Error(health.error ?? "Deployment health check failed.");
  }

  return {
    status: "completed" as const,
    deploymentUrl,
    aliasUrl,
    healthStatus,
    error: null
  };
}
