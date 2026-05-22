import fs from "node:fs";
import path from "node:path";
import { spawnSync } from "node:child_process";
import { loadLocalSecrets } from "./secrets";
import { readResolvedProviderSecrets } from "./provider-integrations";
import { checkArgonRuntimeHealth } from "./vendors/argon-runtime";

export type ToolCapabilityReport = {
  source: "env" | "provider_integration" | "runner" | "cli";
  key: string;
  ok: boolean;
  missing: string[];
  detail: string | null;
};

export type ToolCapability = {
  key: string;
  label: string;
  category: "core" | "model" | "research" | "checkout" | "deploy" | "email" | "distribution" | "creative";
  canRun: boolean;
  reason: string | null;
  missing: string[];
  setup: string[];
  reports: ToolCapabilityReport[];
  setupCommand?: string;
  docsUrl?: string;
};

export type ToolCapabilityBlock = {
  workflowId: string;
  error: string;
  required: string[][];
  blockedKeys: string[];
  missing: string[];
  setup: string[];
  reports: ToolCapabilityReport[];
};

type EnvToolDefinition = {
  key: string;
  label: string;
  category: ToolCapability["category"];
  envNames: string[];
  runnerPaths?: string[];
  commandNames?: string[];
  docsUrl?: string;
};

const envToolDefinitions: EnvToolDefinition[] = [
  {
    key: "database",
    label: "Postgres",
    category: "core",
    envNames: ["DATABASE_URL"],
    runnerPaths: ["src/lib/db.ts"]
  },
  {
    key: "migration_database",
    label: "Migration database",
    category: "core",
    envNames: ["MIGRATION_DATABASE_URL"],
    runnerPaths: ["scripts/migrate.ts"]
  },
  {
    key: "anthropic",
    label: "Anthropic model calls",
    category: "model",
    envNames: ["ANTHROPIC_API_KEY"],
    runnerPaths: ["src/lib/ai-provider.ts"]
  },
  {
    key: "claude_agent_sdk",
    label: "Claude Agent SDK surface builder",
    category: "model",
    envNames: ["ANTHROPIC_API_KEY"],
    runnerPaths: ["src/lib/generated-apps/surface-builder.ts", "node_modules/@anthropic-ai/claude-agent-sdk"]
  },
  {
    key: "openai",
    label: "OpenAI model/video calls",
    category: "model",
    envNames: ["OPENAI_API_KEY"],
    runnerPaths: ["src/lib/ai-provider.ts", "src/lib/vendors/openai-video.ts"]
  },
  {
    key: "tavily",
    label: "Tavily web research",
    category: "research",
    envNames: ["TAVILY_API_KEY"],
    runnerPaths: ["src/lib/community.ts"]
  },
  {
    key: "stripe",
    label: "Stripe checkout/payment links",
    category: "checkout",
    envNames: ["STRIPE_SECRET_KEY", "STRIPE_WEBHOOK_SECRET"],
    runnerPaths: ["src/lib/generated-apps/commerce.ts"],
    docsUrl: "https://docs.stripe.com/keys"
  },
  {
    key: "vercel",
    label: "Vercel deploy/shutdown",
    category: "deploy",
    envNames: ["VERCEL_TOKEN", "VERCEL_TEAM_ID", "VERCEL_PROJECT_ID"],
    commandNames: ["vercel"],
    runnerPaths: ["src/lib/generated-apps/builder.ts"],
    docsUrl: "https://vercel.com/docs/rest-api#creating-an-access-token"
  },
  {
    key: "postmark",
    label: "Postmark magic links",
    category: "email",
    envNames: ["POSTMARK_SERVER_TOKEN", "POSTMARK_FROM_EMAIL"],
    runnerPaths: ["src/lib/generated-app-auth.ts"],
    docsUrl: "https://postmarkapp.com/developer/user-guide/send-email-with-api"
  },
  {
    key: "meta_ads_read",
    label: "Meta Ads read-only credentials",
    category: "distribution",
    envNames: ["META_ACCESS_TOKEN", "META_AD_ACCOUNT_ID"],
    docsUrl: "https://developers.facebook.com/docs/marketing-api"
  }
];

function hasEnv(name: string) {
  return Boolean(process.env[name]?.trim());
}

function runnerMissing(runnerPaths: string[]) {
  return runnerPaths.filter((runnerPath) => !fs.existsSync(path.join(process.cwd(), runnerPath)));
}

function commandMissing(commandNames: string[]) {
  return commandNames.filter((commandName) => spawnSync("sh", ["-lc", `command -v ${commandName}`], { stdio: "ignore" }).status !== 0);
}

function capabilityFromReports(input: {
  key: string;
  label: string;
  category: ToolCapability["category"];
  reports: ToolCapabilityReport[];
  setup: string[];
  docsUrl?: string;
  setupCommand?: string;
  integrationStatus?: string | null;
  integrationError?: string | null;
}) {
  const missing = [...new Set(input.reports.flatMap((report) => report.missing))];
  const canRun = input.reports.every((report) => report.ok);
  const statusReason = input.integrationStatus && !["active", "not_configured"].includes(input.integrationStatus)
    ? `${input.label} integration is ${input.integrationStatus}${input.integrationError ? `: ${input.integrationError}` : "."}`
    : null;
  return {
    key: input.key,
    label: input.label,
    category: input.category,
    canRun,
    reason: statusReason ?? (missing.length ? `Missing ${missing.join(", ")}.` : null),
    missing,
    setup: input.setup,
    reports: input.reports,
    setupCommand: input.setupCommand,
    docsUrl: input.docsUrl
  } satisfies ToolCapability;
}

function envCapability(definition: EnvToolDefinition): ToolCapability {
  const missingEnv = definition.envNames.filter((name) => !hasEnv(name));
  const missingRunners = runnerMissing(definition.runnerPaths ?? []);
  const missingCommands = commandMissing(definition.commandNames ?? []);
  const reports: ToolCapabilityReport[] = [
    {
      source: "env",
      key: `${definition.key}:env`,
      ok: missingEnv.length === 0,
      missing: missingEnv,
      detail: definition.envNames.length ? `Checked ${definition.envNames.join(", ")}.` : "No env secrets required."
    }
  ];
  if (definition.runnerPaths?.length) {
    reports.push({
      source: "runner",
      key: `${definition.key}:runner`,
      ok: missingRunners.length === 0,
      missing: missingRunners.map((runnerPath) => `runner:${runnerPath}`),
      detail: `Checked ${definition.runnerPaths.join(", ")}.`
    });
  }
  if (definition.commandNames?.length) {
    reports.push({
      source: "cli",
      key: `${definition.key}:cli`,
      ok: missingCommands.length === 0,
      missing: missingCommands.map((commandName) => `cli:${commandName}`),
      detail: `Checked ${definition.commandNames.join(", ")} on PATH.`
    });
  }

  return capabilityFromReports({
    key: definition.key,
    label: definition.label,
    category: definition.category,
    reports,
    docsUrl: definition.docsUrl,
    setup: [
      ...missingEnv.map((name) => `Set ${name} in .env.local with: ./takyon secret set ${name} --stdin`),
      ...missingRunners.map((runnerPath) => `Install or implement the runner at ${runnerPath}.`),
      ...missingCommands.map((commandName) => `Install ${commandName} and make sure it is on PATH for the Takyon worker.`)
    ],
    setupCommand: missingEnv.length ? `./takyon secret set ${missingEnv[0]} --stdin` : undefined
  });
}

async function takyonRuntimeCapability(): Promise<ToolCapability> {
  loadLocalSecrets();
  const runnerPaths = [
    "src/lib/ceo.ts",
    "src/lib/takyon-runtime.ts",
    "src/lib/vendors/argon-runtime.ts",
    "src/lib/cron-jobs.ts",
    "scripts/local-worker.ts",
    "scripts/start-argon-hermes-runtime.sh",
    "vendor/argon-hermes-runtime"
  ];
  const missingRunners = runnerMissing(runnerPaths);
  const runtimeVenvPath = "vendor/argon-hermes-runtime/.venv/bin/python";
  const missingRuntimeVenv = !fs.existsSync(path.join(process.cwd(), runtimeVenvPath));
  const hasAnthropic = hasEnv("ANTHROPIC_API_KEY");
  const hasOpenAi = hasEnv("OPENAI_API_KEY");
  const missingModel = hasAnthropic || hasOpenAi ? [] : ["ANTHROPIC_API_KEY or OPENAI_API_KEY"];
  const health = missingRunners.length || missingRuntimeVenv
    ? { ok: false, status: "not_checked", detail: "Runtime files or venv are missing." }
    : await checkArgonRuntimeHealth();
  const missingRuntime = health.ok ? [] : ["hermes_runtime_gateway"];

  return capabilityFromReports({
    key: "takyon_runtime",
    label: "Hermes CEO runtime",
    category: "core",
    reports: [
      {
        source: "runner",
        key: "takyon_runtime:local_runner",
        ok: missingRunners.length === 0,
        missing: missingRunners.map((runnerPath) => `runner:${runnerPath}`),
        detail: `Checked ${runnerPaths.join(", ")}.`
      },
      {
        source: "env",
        key: "takyon_runtime:local_model",
        ok: missingModel.length === 0,
        missing: missingModel,
        detail: hasAnthropic
          ? "Local CEO runtime can use ANTHROPIC_API_KEY."
          : hasOpenAi
            ? "Local CEO runtime can use OPENAI_API_KEY."
            : "Local CEO runtime needs one model key on this Mac."
      },
      {
        source: "runner",
        key: "takyon_runtime:venv",
        ok: !missingRuntimeVenv,
        missing: missingRuntimeVenv ? [`runner:${runtimeVenvPath}`] : [],
        detail: missingRuntimeVenv
          ? "Local Hermes runtime venv is missing."
          : "Local Hermes runtime venv is installed."
      },
      {
        source: "runner",
        key: "takyon_runtime:gateway",
        ok: health.ok,
        missing: missingRuntime,
        detail: health.ok
          ? "Local Hermes gateway is reachable and will be used for CEO wakeups."
          : `Local Hermes gateway is not reachable: ${health.detail}`
      }
    ],
    setup: [
      ...missingRunners.map((runnerPath) => `Install or implement the runner at ${runnerPath}.`),
      ...(missingRuntimeVenv ? ["Install the local Hermes runtime with: scripts/setup-argon-hermes-runtime.sh"] : []),
      ...(missingModel.length ? ["Set ANTHROPIC_API_KEY or OPENAI_API_KEY in .env.local with: ./takyon secret set ANTHROPIC_API_KEY --stdin"] : []),
      ...(missingRuntime.length && !missingRuntimeVenv ? ["Start the local Hermes gateway with: scripts/start-argon-hermes-runtime.sh"] : [])
    ],
    setupCommand: missingModel.length
      ? "./takyon secret set ANTHROPIC_API_KEY --stdin"
      : missingRuntimeVenv
        ? "scripts/setup-argon-hermes-runtime.sh"
        : missingRuntime.length
          ? "scripts/start-argon-hermes-runtime.sh"
          : undefined
  });
}

export async function xCapability(input: { businessId?: string | null; profileId?: string | null } = {}): Promise<ToolCapability> {
  loadLocalSecrets();
  const missingEnv = ["X_CLIENT_ID", "X_CLIENT_SECRET"].filter((name) => !hasEnv(name));
  const missingRunners = runnerMissing(["src/lib/x-social.ts"]);
  const resolved = await readResolvedProviderSecrets({
    provider: "x",
    keys: ["access_token", "refresh_token"],
    includePlatformScope: true
  }).catch(() => ({ row: null, secrets: {} as Record<string, string> }));

  const missingProvider = [
    resolved.secrets.access_token ? null : "x.access_token",
    resolved.secrets.refresh_token ? null : "x.refresh_token"
  ].filter((value): value is string => Boolean(value));
  const integrationBlocked = Boolean(resolved.row && resolved.row.status !== "active");
  const setup: string[] = [];
  if (missingEnv.length) setup.push("Create/configure an X OAuth 2.0 app, then set X_CLIENT_ID and X_CLIENT_SECRET in .env.local.");
  if (missingProvider.length || integrationBlocked) setup.push("Connect the shared platform X OAuth identity so business outreach can post from the same account.");
  for (const runnerPath of missingRunners) setup.push(`Install or implement the runner at ${runnerPath}.`);
  if (setup.length && (missingProvider.length || integrationBlocked)) setup.push("Use: ./takyon connect x --platform");

  return capabilityFromReports({
    key: "x_posting",
    label: "X posting",
    category: "distribution",
    reports: [
      {
        source: "env",
        key: "x_posting:env",
        ok: missingEnv.length === 0,
        missing: missingEnv,
        detail: "Checked X_CLIENT_ID and X_CLIENT_SECRET."
      },
      {
        source: "provider_integration",
        key: "x_posting:provider_integration",
        ok: missingProvider.length === 0 && (!resolved.row || resolved.row.status === "active"),
        missing: missingProvider,
        detail: resolved.row
          ? `Resolved ${resolved.row.scope_type} X integration with status ${resolved.row.status}; business outreach uses the shared platform identity.`
          : "No shared platform X provider integration row resolved."
      },
      {
        source: "runner",
        key: "x_posting:runner",
        ok: missingRunners.length === 0,
        missing: missingRunners.map((runnerPath) => `runner:${runnerPath}`),
        detail: "Checked src/lib/x-social.ts."
      }
    ],
    integrationStatus: resolved.row?.status ?? null,
    integrationError: resolved.row?.last_error ?? null,
    setup,
    setupCommand: missingProvider.length || integrationBlocked ? "./takyon connect x --platform" : missingEnv.length ? `./takyon secret set ${missingEnv[0]} --stdin` : undefined,
    docsUrl: "https://developer.x.com/en/docs/authentication/oauth-2-0"
  });
}

export async function listToolCapabilities(input: { businessId?: string | null; profileId?: string | null } = {}) {
  loadLocalSecrets();
  const capabilities: ToolCapability[] = await Promise.all([
    ...envToolDefinitions.map((definition) => envCapability(definition)),
    takyonRuntimeCapability(),
    xCapability(input)
  ]);

  return capabilities;
}

function unknownCapability(key: string): ToolCapability {
  return {
    key,
    label: key,
    category: "core",
    canRun: false,
    reason: `Capability ${key} is not registered.`,
    missing: [`capability:${key}`],
    setup: [`Add ${key} to the Takyon tool capability registry before any workflow can require it.`],
    reports: [
      {
        source: "runner",
        key: `${key}:registry`,
        ok: false,
        missing: [`capability:${key}`],
        detail: "No capability definition exists for this key."
      }
    ]
  };
}

export function capabilityBlockForGroups(input: {
  workflowId: string;
  groups: string[][];
  capabilities: ToolCapability[];
}) {
  const capabilitiesByKey = new Map(input.capabilities.map((capability) => [capability.key, capability]));
  const blocked: ToolCapability[] = [];

  for (const group of input.groups) {
    if (group.some((key) => capabilitiesByKey.get(key)?.canRun)) continue;
    for (const key of group) blocked.push(capabilitiesByKey.get(key) ?? unknownCapability(key));
  }

  if (!blocked.length) return null;
  const unique = Array.from(new Map(blocked.map((capability) => [capability.key, capability])).values());
  const missing = [...new Set(unique.flatMap((capability) => capability.missing))];
  const setup = [...new Set(unique.flatMap((capability) => capability.setup))];
  const reports = unique.flatMap((capability) => capability.reports.filter((report) => !report.ok));

  return {
    workflowId: input.workflowId,
    error: `Required capability unavailable for ${input.workflowId}: ${unique.map((capability) => capability.label).join(", ")}.`,
    required: input.groups,
    blockedKeys: unique.map((capability) => capability.key),
    missing,
    setup,
    reports
  } satisfies ToolCapabilityBlock;
}

export async function preflightCapabilityGroups(input: {
  workflowId: string;
  groups: string[][];
  businessId?: string | null;
  profileId?: string | null;
}) {
  if (!input.groups.length) return null;
  const capabilities = await listToolCapabilities({ businessId: input.businessId ?? null, profileId: input.profileId ?? null });
  return capabilityBlockForGroups({ workflowId: input.workflowId, groups: input.groups, capabilities });
}

export async function requireCapability(key: string, input: { businessId?: string | null; profileId?: string | null } = {}) {
  const capabilities = await listToolCapabilities(input);
  const capability = capabilities.find((item) => item.key === key);
  if (!capability || !capability.canRun) {
    throw new Error(capability?.reason || `${key} is unavailable.`);
  }
  return capability;
}
