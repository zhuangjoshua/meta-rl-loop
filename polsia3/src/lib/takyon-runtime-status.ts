import { spawnSync } from "node:child_process";
import { db } from "./db";
import { listCronJobs } from "./cron-jobs";
import { listToolCapabilities } from "./tool-availability";
import { listTakyonHarnessCommands, readTakyonHarnessSettings } from "./takyon-harness";
import { loadLocalSecrets } from "./secrets";

export type TakyonRuntimeStatus = {
  cwd: string;
  localMac: {
    ok: boolean;
    modelRuntime: "ok" | "blocked";
    provider: string | null;
    node: "ok" | "missing";
    tsx: "ok" | "missing";
    workerCommand: string;
    vpsCommand: string;
  };
  remoteRuntime: {
    configured: boolean;
    enabled: boolean;
    note: string;
  };
  cron: {
    total: number;
    active: number;
    paused: number;
    due: number;
    jobs: Awaited<ReturnType<typeof listCronJobs>>;
  };
  worker: {
    queued: number;
    running: number;
    blocked: number;
    failed: number;
    completed: number;
    cancelled: number;
  };
  harness: {
    root: string;
    commandCount: number;
    commands: string[];
  };
  missing: string[];
};

function commandExists(command: string) {
  return spawnSync("sh", ["-lc", `command -v ${command}`], { stdio: "ignore" }).status === 0;
}

function configuredProvider() {
  if (process.env.TAKYON_CEO_PROVIDER?.trim()) return process.env.TAKYON_CEO_PROVIDER.trim();
  if (process.env.ARGON_CEO_PROVIDER?.trim()) return process.env.ARGON_CEO_PROVIDER.trim();
  if (process.env.ANTHROPIC_API_KEY?.trim()) return "anthropic";
  if (process.env.OPENAI_API_KEY?.trim()) return "openai";
  return null;
}

export async function getTakyonRuntimeStatus(input: { businessId?: string | null; profileId?: string | null } = {}): Promise<TakyonRuntimeStatus> {
  loadLocalSecrets();
  const sql = db();
  const [capabilities, cronRows, jobCounts, settings, commands] = await Promise.all([
    listToolCapabilities(input),
    listCronJobs({ profileId: input.profileId ?? null }),
    sql<{ status: string; count: number }[]>`
      SELECT status, count(*)::int AS count
      FROM workflow_jobs
      GROUP BY status
    `,
    readTakyonHarnessSettings(),
    listTakyonHarnessCommands()
  ]);
  const runtime = capabilities.find((capability) => capability.key === "takyon_runtime");
  const missing = runtime?.missing ?? [];
  const counts = Object.fromEntries(jobCounts.map((row) => [row.status, Number(row.count)]));
  const now = Date.now();
  const due = cronRows.filter((row) => row.status === "active" && new Date(row.next_run_at).getTime() <= now).length;

  return {
    cwd: process.cwd(),
    localMac: {
      ok: Boolean(runtime?.canRun),
      modelRuntime: runtime?.canRun ? "ok" : "blocked",
      provider: configuredProvider(),
      node: commandExists("node") ? "ok" : "missing",
      tsx: commandExists("./node_modules/.bin/tsx") || commandExists("tsx") ? "ok" : "missing",
      workerCommand: "./takyon run <business> --once",
      vpsCommand: "./takyon vps"
    },
    remoteRuntime: {
      configured: Boolean(process.env.ARGON_RUNTIME_URL?.trim()),
      enabled: Boolean(runtime?.canRun),
      note: "CEO wakeups always use the Hermes gateway. ARGON_RUNTIME_URL only overrides the local gateway address."
    },
    cron: {
      total: cronRows.length,
      active: cronRows.filter((row) => row.status === "active").length,
      paused: cronRows.filter((row) => row.status === "paused").length,
      due,
      jobs: cronRows
    },
    worker: {
      queued: counts.queued ?? 0,
      running: counts.running ?? 0,
      blocked: counts.blocked ?? 0,
      failed: counts.failed ?? 0,
      completed: counts.completed ?? 0,
      cancelled: counts.cancelled ?? 0
    },
    harness: {
      root: settings.root,
      commandCount: commands.length,
      commands: commands.map((command) => `/${command.name}`)
    },
    missing
  };
}
