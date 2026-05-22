import { closeDbConnections, db } from "../src/lib/db";
import { createInterface as createQuestionInterface } from "node:readline/promises";
import { clearLine, createInterface as createLiveInterface, cursorTo, emitKeypressEvents, type Interface as LiveReadline } from "node:readline";
import { stdin as input, stdout as output } from "node:process";
import { spawn, spawnSync, type ChildProcess } from "node:child_process";
import path from "node:path";
import { readFile, rm } from "node:fs/promises";
import { createCompany } from "../src/lib/companies";
import { getAppEnv, getLocalAuthSeed } from "../src/lib/env";
import { upsertProfile } from "../src/lib/auth";
import { getCompanyForProfile, listCompaniesForProfile } from "../src/lib/companies";
import { runBusinessAutopilot } from "../src/lib/business-autopilot";
import { upsertBusinessCampaign, listBusinessCampaigns, requireBusinessCampaign, setBusinessCampaignStatus } from "../src/lib/business-campaigns";
import { ensureBudgetAccount, reserveBusinessBudget, usdToMicrousd } from "../src/lib/business-budget";
import { listBusinessMemory, upsertBusinessMemory } from "../src/lib/business-memory";
import { startTakyonGoal } from "../src/lib/goals";
import { enqueueWorkflowJob, listWorkflowJobs, type WorkflowJobRow } from "../src/lib/workflow-jobs";
import { setTakyonControl } from "../src/lib/takyon-control";
import { runTakyonTerminalAgent, type TakyonTerminalRecentTurn } from "../src/lib/takyon-terminal-agent";
import { listToolCapabilities, preflightCapabilityGroups, type ToolCapability } from "../src/lib/tool-availability";
import { upsertLocalSecret } from "../src/lib/secrets";
import { getTakyonWorkflowSpec, takyonCapabilityGroups, takyonLaneByWorkflow } from "../src/lib/takyon-registry";
import { listBusinessWorkspaceFiles, readBusinessWorkspaceFile, syncBusinessWorkspace, writeBusinessWorkspaceFile, removeBusinessWorkspace } from "../src/lib/business-workspace";
import { getTakyonHarnessCommand, listTakyonHarnessCommands, readTakyonHarnessSettings, renderTakyonHarnessCommand, type TakyonHarnessCommand } from "../src/lib/takyon-harness";
import { dispatchDueCronJobs, listCronJobs, type CronJobRow } from "../src/lib/cron-jobs";
import { getTakyonRuntimeStatus, type TakyonRuntimeStatus } from "../src/lib/takyon-runtime-status";

type JsonValue = string | number | boolean | null | JsonValue[] | { [key: string]: JsonValue };
type TerminalProfile = Awaited<ReturnType<typeof terminalProfile>>;

const topLevelCommands = new Set([
  "help",
  "-h",
  "--help",
  "shell",
  "interactive",
  "build",
  "create",
  "businesses",
  "business",
  "jobs",
  "list",
  "status",
  "capabilities",
  "caps",
  "campaigns",
  "campaign",
  "files",
  "read",
  "write",
  "workspace",
  "setup",
  "connect",
  "secret",
  "delete",
  "purge",
  "budget",
  "memory",
  "wake",
  "run",
  "goal",
  "/goal",
  "watch",
  "gc",
  "enqueue",
  "pause",
  "resume",
  "kill",
  "auto",
  "stop",
  "logs",
  "commands",
  "skills",
  "harness",
  "command",
  "ui",
  "runtime",
  "runtimes",
  "cron",
  "vps"
]);

const laneByWorkflow = takyonLaneByWorkflow();

function flag(args: string[], name: string, defaultValue = "") {
  const index = args.indexOf(name);
  if (index === -1) return defaultValue;
  return args[index + 1] ?? defaultValue;
}

function hasFlag(args: string[], name: string) {
  return args.includes(name);
}

function flagText(args: string[], name: string, defaultValue = "") {
  const index = args.indexOf(name);
  if (index === -1) return defaultValue;
  const values: string[] = [];
  for (const value of args.slice(index + 1)) {
    if (value.startsWith("--")) break;
    values.push(value);
  }
  return values.join(" ").trim() || defaultValue;
}

function buildArgs(args: string[]) {
  const firstFlag = args.findIndex((arg, index) => index > 0 && arg.startsWith("--"));
  const name = flagText(args, "--name", firstFlag === -1 ? args.slice(1).join(" ") : args.slice(1, firstFlag).join(" ")).trim();
  const pitch = flagText(args, "--pitch", "").trim();
  return { name, pitch };
}

function print(value: unknown, json: boolean) {
  if (json) {
    console.log(JSON.stringify(value, null, 2));
    return;
  }
  if (typeof value === "string") console.log(value);
  else console.log(JSON.stringify(value, null, 2));
}

const colorEnabled = output.isTTY
  && process.env.TAKYON_COLOR !== "0"
  && (process.env.TAKYON_COLOR === "1" || process.env.NO_COLOR !== "1");
const ansi = {
  reset: "\x1b[0m",
  bold: "\x1b[1m",
  dim: "\x1b[2m",
  red: "\x1b[31m",
  green: "\x1b[32m",
  yellow: "\x1b[33m",
  blue: "\x1b[34m",
  magenta: "\x1b[35m",
  cyan: "\x1b[36m",
  gray: "\x1b[90m",
  brightBlue: "\x1b[94m",
  brightMagenta: "\x1b[95m",
  electricBlue: "\x1b[38;2;0;176;255m"
};

const theme = {
  brand: ansi.electricBlue,
  primary: ansi.electricBlue,
  secondary: ansi.cyan,
  skill: ansi.cyan,
  control: ansi.gray,
  muted: ansi.gray,
  success: ansi.green,
  warning: ansi.yellow,
  danger: ansi.red
};

function color(text: string, code: string) {
  return colorEnabled ? `${code}${text}${ansi.reset}` : text;
}

function stripAnsi(text: string) {
  return text.replace(/\x1b\[[0-9;]*m/g, "");
}

function visibleLength(text: string) {
  return stripAnsi(text).length;
}

function padVisible(text: string, width: number) {
  return `${text}${" ".repeat(Math.max(0, width - visibleLength(text)))}`;
}

function truncatePlain(text: string, width: number) {
  if (width <= 1) return "";
  if (text.length <= width) return text;
  if (width <= 3) return text.slice(0, width);
  return `${text.slice(0, Math.max(1, width - 3))}...`;
}

function bold(text: string) {
  return color(text, ansi.bold);
}

function dim(text: string) {
  return color(text, ansi.dim);
}

function tag(label: string, code: string) {
  return color(`[${label}]`, code);
}

function frameLine(width = 66) {
  return color(`+${"-".repeat(Math.max(10, width - 2))}+`, ansi.gray);
}

function framedText(text: string, width = 66) {
  const plain = stripAnsi(text);
  const padding = Math.max(0, width - plain.length - 4);
  return `${color("|", ansi.gray)} ${text}${" ".repeat(padding)} ${color("|", ansi.gray)}`;
}

function shellFrameWidth() {
  const columns = output.columns || 92;
  return Math.max(64, Math.min(columns - 2, 112));
}

function inputRule() {
  return color("-".repeat(shellFrameWidth()), ansi.gray);
}

function inputPromptLabel(currentBusiness: string | null) {
  const scope = currentBusiness ? color(currentBusiness, theme.secondary) : dim("terminal");
  return `${color("takyon", theme.brand)}${dim("/")}${scope}`;
}

function inputBarTop(currentBusiness: string | null) {
  const width = shellFrameWidth();
  const label = ` ${inputPromptLabel(currentBusiness)} `;
  const fill = Math.max(0, width - visibleLength(label));
  const left = Math.floor(fill / 2);
  const right = fill - left;
  return `${color("─".repeat(left), theme.muted)}${label}${color("─".repeat(right), theme.muted)}`;
}

function inputPrompt(currentBusiness: string | null) {
  if (!output.isTTY) return `${inputPromptLabel(currentBusiness)} > `;
  return `${inputBarTop(currentBusiness)}\n${color("›", theme.primary)} `;
}

function closeInputBox() {
  if (!output.isTTY) return;
  const width = shellFrameWidth();
  console.log(color("─".repeat(width), theme.muted));
}

function statusColor(status: string) {
  if (status === "completed" || status === "ok" || status === "active" || status === "published") return ansi.green;
  if (status === "running" || status === "queued") return ansi.cyan;
  if (status === "blocked" || status === "paused" || status === "draft") return ansi.yellow;
  if (status === "failed" || status === "cancelled" || status === "killed") return ansi.red;
  return ansi.gray;
}

function paintStatus(status: string) {
  return color(status, statusColor(status));
}

function shortId(id: string) {
  return id.slice(0, 8);
}

function printCapabilities(capabilities: ToolCapability[], json: boolean) {
  if (json) return print(capabilities, true);
  for (const capability of capabilities) {
    const mark = capability.canRun ? color("ok", ansi.green) : color("blocked", ansi.red);
    console.log(`${mark}: ${bold(capability.label)} ${dim(`(${capability.category}/${capability.key})`)}`);
    if (capability.reason) console.log(`  ${dim(capability.reason)}`);
    if (!capability.canRun && capability.missing.length) console.log(`  ${color("Missing:", ansi.yellow)} ${capability.missing.join(", ")}`);
    for (const report of capability.reports.filter((item) => !item.ok)) {
      const source = `${report.source}:${report.key}`;
      console.log(`  ${color("Source:", theme.secondary)} ${source}${report.missing.length ? ` missing ${report.missing.join(", ")}` : ""}`);
      if (report.detail) console.log(`    ${dim(report.detail)}`);
    }
    if (!capability.canRun && capability.setup.length) {
      for (const step of capability.setup) console.log(`  ${color("Setup:", ansi.cyan)} ${step}`);
    }
    if (!capability.canRun && capability.docsUrl) console.log(`  ${dim("Docs:")} ${capability.docsUrl}`);
  }
}

function printBusinesses(businesses: Awaited<ReturnType<typeof listCompaniesForProfile>>, json: boolean) {
  if (json) return print(businesses, true);
  if (!businesses.length) {
    console.log("No businesses yet.");
    return;
  }
  for (const business of businesses) {
    const status = business.site_status ?? "no site";
    console.log(`${color(business.slug.padEnd(34), ansi.cyan)} ${bold(business.name)} ${dim("(")}${paintStatus(status)}${dim(")")}`);
  }
}

function printBusinessDeleteResult(result: Awaited<ReturnType<typeof deleteBusinessesForProfile>>, json: boolean) {
  if (json) return print(result, true);
  if (result.dryRun) {
    console.log(`${tag("delete", ansi.yellow)} would delete ${bold(String(result.count))} business${result.count === 1 ? "" : "es"}`);
    for (const business of result.businesses.slice(0, 20)) {
      console.log(`  ${color(business.slug.padEnd(34), ansi.cyan)} ${bold(business.name)} ${dim(shortId(business.id))}`);
    }
    if (result.count > 20) console.log(`  ${dim(`...and ${result.count - 20} more`)}`);
    console.log(dim(result.next));
    return;
  }
  console.log(`${tag("delete", ansi.red)} deleted ${bold(String(result.deleted.length))} business${result.deleted.length === 1 ? "" : "es"}`);
  for (const business of result.deleted.slice(0, 20)) {
    console.log(`  ${color(business.slug.padEnd(34), ansi.cyan)} ${bold(business.name)} ${dim(shortId(business.id))}`);
  }
  if (result.deleted.length > 20) console.log(`  ${dim(`...and ${result.deleted.length - 20} more`)}`);
  if (result.localGeneratedDirsRemoved.length) {
    console.log(`${dim("removed generated dirs:")} ${result.localGeneratedDirsRemoved.length}`);
  }
  if (result.localWorkspaceDirsRemoved.length) {
    console.log(`${dim("removed workspace dirs:")} ${result.localWorkspaceDirsRemoved.length}`);
  }
}

function printCampaigns(campaigns: Awaited<ReturnType<typeof listBusinessCampaigns>>, json: boolean) {
  if (json) return print(campaigns, true);
  if (!campaigns.length) {
    console.log("No campaigns yet.");
    return;
  }
  for (const campaign of campaigns) {
    console.log(`${color(campaign.slug.padEnd(34), theme.secondary)} ${bold(campaign.name)} ${dim("(")}${paintStatus(campaign.status)}, ${campaign.kind}${dim(")")}`);
  }
}

type AutopilotPlan = Awaited<ReturnType<typeof runBusinessAutopilot>>;

function printAutopilotPlan(plan: AutopilotPlan, json: boolean) {
  if (json) return print(plan, true);
  console.log(`${tag("ceo", theme.primary)} ${bold("Takyon plan:")} ${plan.business.name} ${dim(`(${plan.business.slug})`)}`);
  for (const reason of plan.reasons) console.log(`  ${color("-", theme.primary)} ${reason}`);
  console.log(`${color("CEO wakeup:", theme.primary)} ${plan.ceoWakeupJobId ? shortId(plan.ceoWakeupJobId) : color("not queued", ansi.yellow)}`);
  if (plan.campaignId) console.log(`${color("Campaign:", theme.secondary)} ${shortId(plan.campaignId)}`);

  const queued = plan.queued.filter((item) => item.status === "queued");
  const existing = plan.queued.filter((item) => item.status === "already_present");
  const blocked = [...plan.queued.filter((item) => item.status === "blocked"), ...plan.blocked.filter((item) => item.status === "blocked")];

  if (queued.length) {
    console.log("");
    console.log(color("Queued:", ansi.cyan));
    for (const item of queued) console.log(`  - ${color(item.workflow_id, ansi.cyan)} ${dim(`(${shortId(item.jobId)})`)}`);
  }

  if (existing.length) {
    console.log("");
    console.log(color("Already present:", ansi.gray));
    for (const item of existing) console.log(`  - ${item.workflow_id} ${dim(`(${item.existingStatus})`)}`);
  }

  if (blocked.length) {
    console.log("");
    console.log(color("Blocked:", ansi.yellow));
    for (const item of blocked) {
      console.log(`  - ${color(item.workflow_id, ansi.yellow)}: ${item.reason ?? "capability unavailable"}`);
      if (item.missing.length) console.log(`    ${color("Missing:", ansi.yellow)} ${item.missing.join(", ")}`);
      for (const step of item.setup) console.log(`    ${color("Setup:", ansi.cyan)} ${step}`);
    }
  }
}

function workflowLine(job: WorkflowJobRow) {
  const status = color(job.status.padEnd(9), statusColor(job.status));
  const workflow = color(job.workflow_id.padEnd(34), ansi.cyan);
  const lane = color(job.lane.padEnd(34), theme.secondary);
  const attempts = `${job.attempts}/${job.max_attempts}`;
  const updated = dim(new Date(job.updated_at).toLocaleTimeString());
  return `${status} ${workflow} ${lane} p${job.priority} attempts ${attempts} ${updated} ${dim(shortId(job.id))}`;
}

function printWorkflowJobs(jobs: WorkflowJobRow[], json: boolean) {
  if (json) return print(jobs, true);
  if (!jobs.length) {
    console.log("No workflow jobs yet.");
    return;
  }
  for (const job of jobs) {
    console.log(workflowLine(job));
    if (job.dependencies.length) console.log(`  ${dim("waits for:")} ${job.dependencies.join(", ")}`);
    if (job.error) console.log(`  ${color("error:", ansi.red)} ${job.error}`);
  }
}

function printWorkspaceFiles(files: Awaited<ReturnType<typeof listBusinessWorkspaceFiles>>, json: boolean) {
  if (json) return print(files, true);
  if (!files.length) {
    console.log("No workspace files.");
    return;
  }
  for (const file of files) {
    console.log(`${color(file.path, ansi.cyan)} ${dim(`${file.bytes}b ${new Date(file.updatedAt).toLocaleString()}`)}`);
  }
}

function printHarnessCommands(commands: TakyonHarnessCommand[], json: boolean) {
  if (json) return print(commands.map(({ body: _body, ...command }) => command), true);
  if (!commands.length) {
    console.log("No harness commands found.");
    return;
  }
  for (const command of commands) {
    const scope = command.requiresBusiness ? color("business", theme.secondary) : color("global", ansi.gray);
    const band = command.priorityBand ? color(command.priorityBand, ansi.cyan) : dim("unbanded");
    console.log(`${color(`/${command.name}`.padEnd(18), theme.primary)} ${scope} ${band} ${command.description ?? ""}`);
    if (command.allowedTools.length) console.log(`  ${dim("tools:")} ${command.allowedTools.join(", ")}`);
    console.log(`  ${dim(command.path)}`);
  }
}

function formatWhen(value: string | null) {
  if (!value) return "never";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString();
}

function cronMeta(job: CronJobRow) {
  return job.metadata && typeof job.metadata === "object" && !Array.isArray(job.metadata)
    ? job.metadata as Record<string, unknown>
    : {};
}

function cronScope(job: CronJobRow) {
  const metadata = cronMeta(job);
  if (metadata.scope === "business") {
    const slug = typeof metadata.business_slug === "string" ? metadata.business_slug : "";
    const name = typeof metadata.business_name === "string" ? metadata.business_name : "";
    return `${color("business", theme.secondary)} ${slug || name || String(metadata.business_id ?? "")}`;
  }
  if (job.job_key === "agent_runner") return `${color("system", ansi.gray)} local worker pulse`;
  return color("system", ansi.gray);
}

function printCronJobs(jobs: CronJobRow[], json: boolean) {
  if (json) return print(jobs, true);
  if (!jobs.length) {
    console.log("No cron jobs configured.");
    return;
  }
  const ceoJobs = jobs.filter((job) => job.job_key.startsWith("ceo_wakeup:"));
  console.log(dim("CEO wake schedules are per business. The only global row here should be the non-CEO worker pulse."));
  if (!ceoJobs.length) console.log(`${tag("cron", ansi.gray)} no active business CEO schedules`);
  for (const job of jobs) {
    const cadence = job.schedule_type === "interval"
      ? `${job.interval_seconds ?? 0}s`
      : `daily ${job.daily_time_utc ?? ""}`.trim();
    const key = job.job_key.startsWith("ceo_wakeup:") ? "ceo_wakeup" : job.job_key;
    console.log(`${paintStatus(job.status).padEnd(16)} ${color(key.padEnd(18), ansi.cyan)} ${dim(cadence)} ${cronScope(job)} next ${formatWhen(job.next_run_at)}`);
    if (job.locked_by) console.log(`  ${color("locked:", ansi.yellow)} ${job.locked_by} ${dim(formatWhen(job.locked_at))}`);
    if (job.last_error) console.log(`  ${color("last error:", ansi.red)} ${job.last_error}`);
  }
}

function printRuntimeStatus(status: TakyonRuntimeStatus, json: boolean) {
  if (json) return print(status, true);
  const localMark = status.localMac.ok ? color("ok", ansi.green) : color("blocked", ansi.red);
  const nodeMark = paintStatus(status.localMac.node);
  const tsxMark = paintStatus(status.localMac.tsx);
  console.log(`${tag("runtime", ansi.brightBlue)} local Mac ${localMark} ${dim(status.cwd)}`);
  console.log(`  ${color("model", ansi.cyan)} ${paintStatus(status.localMac.modelRuntime)} ${status.localMac.provider ? dim(status.localMac.provider) : color("no provider", ansi.yellow)}`);
  console.log(`  ${color("node", ansi.cyan)} ${nodeMark}  ${color("tsx", ansi.cyan)} ${tsxMark}`);
  console.log(`  ${color("worker", ansi.cyan)} queued ${status.worker.queued}, running ${status.worker.running}, blocked ${status.worker.blocked}, failed ${status.worker.failed}`);
  console.log(`  ${color("cron", ansi.cyan)} ${status.cron.active} active, ${status.cron.paused} paused, ${status.cron.due} due`);
  console.log(`  ${color("harness", theme.primary)} ${status.harness.commandCount} commands at ${status.harness.root}`);
  console.log(`  ${color("remote", ansi.gray)} ${status.remoteRuntime.enabled ? "enabled" : "off"} ${status.remoteRuntime.configured ? dim("ARGON_RUNTIME_URL configured") : dim("no URL needed")}`);
  if (status.missing.length) {
    console.log(`  ${color("missing", ansi.yellow)} ${status.missing.join(", ")}`);
    console.log(`  ${color("setup", ansi.cyan)} ./takyon secret set ANTHROPIC_API_KEY --stdin`);
  }
  console.log(`  ${dim("local VPS:")} ${status.localMac.vpsCommand}`);
}

type SlashCommandEntry = {
  name: string;
  kind: "control" | "skill";
  description: string;
  priorityBand?: string | null;
  requiresBusiness?: boolean;
};

const builtInSlashCommands: SlashCommandEntry[] = [
  { name: "businesses", kind: "control", description: "List businesses" },
  { name: "use", kind: "control", description: "Attach a business session", requiresBusiness: false },
  { name: "create", kind: "control", description: "Create and start a new business; /create <name> --pitch <pitch> [--budget-usd 100]" },
  { name: "build", kind: "control", description: "Alias for /create <name> --pitch <pitch> [--budget-usd 100]" },
  { name: "delete", kind: "control", description: "Delete business rows/workspaces; dry-run unless confirm is included" },
  { name: "runtimes", kind: "control", description: "Show local runtime, cron, and worker status" },
  { name: "cron", kind: "control", description: "Show or run local cron; try /cron status" },
  { name: "commands", kind: "control", description: "List available slash skills" },
  { name: "skills", kind: "control", description: "Alias for /commands" },
  { name: "status", kind: "control", description: "Show current business status", requiresBusiness: true },
  { name: "workspace", kind: "control", description: "Show current business filesystem", requiresBusiness: true },
  { name: "jobs", kind: "control", description: "Show current business work queue", requiresBusiness: true },
  { name: "files", kind: "control", description: "List business workspace files", requiresBusiness: true },
  { name: "read", kind: "control", description: "Read a business workspace file", requiresBusiness: true },
  { name: "capabilities", kind: "control", description: "Show available and blocked tools" },
  { name: "campaigns", kind: "control", description: "List current business campaigns", requiresBusiness: true },
  { name: "run", kind: "control", description: "Wake/autopilot current business with instructions", requiresBusiness: true },
  { name: "wake", kind: "control", description: "Queue a CEO wake for current business", requiresBusiness: true },
  { name: "auto", kind: "control", description: "Start or stop background worker stream", requiresBusiness: true },
  { name: "logs", kind: "control", description: "Toggle worker internals in the shell", requiresBusiness: true },
  { name: "ui", kind: "control", description: "Switch compact/full session panel" },
  { name: "goal", kind: "control", description: "Start a business goal", requiresBusiness: true },
  { name: "budget", kind: "control", description: "Set or reserve business/campaign budget", requiresBusiness: true },
  { name: "memory", kind: "control", description: "List or record business memory", requiresBusiness: true },
  { name: "connect", kind: "control", description: "Connect provider integrations" },
  { name: "setup", kind: "control", description: "Show setup for a blocked capability" },
  { name: "kill", kind: "control", description: "Kill switch for business/campaign/job/provider/global" },
  { name: "stop", kind: "control", description: "Detach current live session", requiresBusiness: true },
  { name: "exit", kind: "control", description: "Exit Takyon shell" }
];

function slashEntriesFromHarness(commands: TakyonHarnessCommand[]) {
  const skillEntries = commands.map((command) => ({
    name: command.name,
    kind: "skill" as const,
    description: command.description ?? "Harness skill",
    priorityBand: command.priorityBand,
    requiresBusiness: command.requiresBusiness
  }));
  return [...builtInSlashCommands, ...skillEntries].sort((left, right) => left.name.localeCompare(right.name));
}

function slashPrefix(line: string) {
  if (!line.startsWith("/")) return "";
  return line.slice(1).trimStart().split(/\s+/)[0]?.toLowerCase() ?? "";
}

function shouldShowSlashPalette(line: string) {
  if (!line.startsWith("/")) return false;
  return !/\s/.test(line.slice(1));
}

function visibleSlashEntries(entries: SlashCommandEntry[], currentBusiness: string | null) {
  return entries.filter((entry) => !entry.requiresBusiness || Boolean(currentBusiness));
}

function slashMatches(entries: SlashCommandEntry[], line: string, currentBusiness: string | null) {
  const prefix = slashPrefix(line);
  const visible = visibleSlashEntries(entries, currentBusiness);
  if (!prefix) return visible;
  return visible.filter((entry) => entry.name.toLowerCase().startsWith(prefix));
}

function slashPalettePageSize() {
  return Math.max(6, Math.min(18, (output.rows || 24) - 8));
}

function renderSlashPalette(entries: SlashCommandEntry[], line: string, currentBusiness: string | null, offset = 0) {
  const prefix = slashPrefix(line);
  const matches = slashMatches(entries, line, currentBusiness);
  const visibleCount = visibleSlashEntries(entries, currentBusiness).length;
  const width = Math.max(58, Math.min((output.columns || 96) - 6, 96));
  const inner = width - 4;
  const maxRows = slashPalettePageSize();
  const start = Math.max(0, Math.min(offset, Math.max(0, matches.length - maxRows)));
  const end = Math.min(matches.length, start + maxRows);
  const title = prefix ? `/${prefix}` : "/";
  const header = `${color("Takyon", theme.brand)} ${dim("slash")} ${color(title, theme.primary)} ${dim(`${matches.length}/${visibleCount}`)}`;
  const context = currentBusiness
    ? `${dim("business")} ${color(currentBusiness, theme.secondary)}`
    : `${dim("attach")} ${color("/use <business>", theme.primary)}`;
  const scrollHint = matches.length > maxRows ? `  ${start + 1}-${end} ↑/↓ scroll` : "";
  const hint = currentBusiness
    ? dim(`return runs  tab completes  plain text chats${scrollHint}`)
    : dim(`business skills appear after /use${scrollHint}`);
  const borderTop = color(`.${"-".repeat(width - 2)}.`, theme.muted);
  const borderBottom = color(`'${"-".repeat(width - 2)}'`, theme.muted);
  const boxLine = (text = "") => `${color("|", theme.muted)} ${padVisible(text, inner)} ${color("|", theme.muted)}`;
  const rows = matches.slice(start, end).map((entry) => {
    const command = padVisible(color(`/${entry.name}`, entry.kind === "skill" ? theme.skill : theme.primary), 16);
    const kind = entry.kind === "skill" ? color("skill", theme.skill) : color("control", theme.control);
    const scope = entry.requiresBusiness ? color("business", theme.secondary) : color("global", theme.muted);
    const band = entry.priorityBand ? ` ${color(entry.priorityBand, theme.secondary)}` : "";
    const meta = padVisible(`${kind} ${scope}${band}`, 24);
    const descriptionWidth = Math.max(10, inner - 16 - 1 - 24 - 1);
    return `${command} ${meta} ${dim(truncatePlain(entry.description, descriptionWidth))}`;
  });
  if (matches.length > maxRows) rows.push(dim(`${matches.length - end} more; arrows scroll, typing narrows`));
  if (!matches.length) {
    rows.push(`${color("no matches", ansi.yellow)} ${dim(currentBusiness ? "plain text still chats with Takyon" : "try /businesses or /use <business>")}`);
  }
  return [
    borderTop,
    boxLine(`${header}  ${context}`),
    boxLine(hint),
    boxLine(),
    ...rows.map((row) => boxLine(row)),
    borderBottom
  ].join("\n");
}

type TakyonMascot = {
  kind: "ansi" | "pixel";
  lines: string[];
};

const defaultMascot: TakyonMascot = {
  kind: "pixel",
  lines: [
  "    ####        ",
  "  ########      ",
  " ##########     ",
  "###..##..###==> ",
  " ############   ",
  "  ########      ",
  "    ####        ",
  "   ##  ##       "
  ]
};

function decodeMascotAnsi(text: string) {
  return text
    .replace(/\\x1b/g, "\x1b")
    .replace(/\\u001b/g, "\x1b")
    .replace(/\\e/g, "\x1b");
}

async function readTakyonMascot() {
  const explicitPath = process.env.TAKYON_MASCOT_FILE?.trim();
  const ansiPath = explicitPath || path.join(process.cwd(), "harness", "takyon", "mascot.ansi");
  const ansiRaw = await readFile(ansiPath, "utf8").catch(() => null);
  if (ansiRaw) {
    const lines = decodeMascotAnsi(ansiRaw).replace(/\n+$/, "").split(/\r?\n/);
    return lines.length ? { kind: "ansi" as const, lines } : defaultMascot;
  }

  const pixelPath = path.join(process.cwd(), "harness", "takyon", "mascot.txt");
  const pixelRaw = await readFile(pixelPath, "utf8").catch(() => null);
  if (!pixelRaw) return defaultMascot;
  const lines = pixelRaw.replace(/\n+$/, "").split(/\r?\n/);
  return lines.length ? { kind: "pixel" as const, lines } : defaultMascot;
}

function renderPixelMascotLine(line: string, width = 16) {
  const cells = line.slice(0, width).padEnd(width, " ").split("");
  return cells.map((cell) => {
    if (cell === "#" || cell === "@") return color(colorEnabled ? "██" : "##", ansi.electricBlue);
    if (cell === ".") return color(colorEnabled ? "██" : "##", ansi.cyan);
    if (cell === "=") return color("==", ansi.cyan);
    if (cell === ">") return color("=>", ansi.cyan);
    return "  ";
  }).join("");
}

function renderMascotLine(mascot: TakyonMascot, index: number) {
  const line = mascot.lines[index] ?? "";
  if (mascot.kind === "ansi") return colorEnabled ? line : stripAnsi(line);
  return renderPixelMascotLine(line);
}

function startupGraphic(input: { commandCount: number; uiMode: "compact" | "full"; mascot: TakyonMascot }) {
  const width = Math.max(92, Math.min(shellFrameWidth(), 112));
  const wordmark = [
    " _____     _                      ",
    "|_   _|_ _| | ___   _  ___  _ __  ",
    "  | |/ _` | |/ / | | |/ _ \\| '_ \\ ",
    "  | | (_| |   <| |_| | (_) | | | |",
    "  |_|\\__,_|_|\\_\\\\__, |\\___/|_| |_|",
    "                |___/             "
  ];
  const logoRows = wordmark.map((line, index) => {
    const mark = renderMascotLine(input.mascot, index);
    return framedText(`${mark}  ${color(line, ansi.electricBlue)}`, width);
  });
  const extraMascotRows = input.mascot.lines.slice(wordmark.length).map((_mark, index) => {
    return framedText(`${renderMascotLine(input.mascot, index + wordmark.length)}  ${dim(" ")}`, width);
  });
  return [
    frameLine(width),
    ...logoRows,
    ...extraMascotRows,
    framedText("", width),
    framedText(`${bold("local CEO harness")} ${color("ready", theme.success)}  ${dim(process.cwd())}`, width),
    framedText(`${color("plain text", theme.primary)} chats and steers    ${color("/", theme.primary)} opens skills    ${color("tab", theme.primary)} completes`, width),
    framedText(`${color(String(input.commandCount), theme.primary)} slash commands    ${dim(`ui ${input.uiMode}`)}    ${dim("local Mac runtime")}`, width),
    frameLine(width)
  ].join("\n");
}

async function watchBusinessWork(input: { businessId: string; json: boolean; once: boolean; intervalMs: number; limit: number }) {
  let lastSignature = "";
  do {
    const jobs = await listWorkflowJobs(input.businessId, input.limit);
    if (input.json || input.once) {
      printWorkflowJobs(jobs, input.json);
      return;
    }

    const signature = JSON.stringify(
      jobs.map((job) => [job.id, job.status, job.error, job.attempts, job.updated_at, job.locked_by, job.run_after])
    );
    if (signature !== lastSignature) {
      console.log("");
      console.log(`${tag(new Date().toLocaleTimeString(), ansi.gray)} ${bold("workflow jobs")}`);
      printWorkflowJobs(jobs, false);
      lastSignature = signature;
    }
    await new Promise((resolve) => setTimeout(resolve, input.intervalMs));
  } while (true);
}

function spawnWorkerProcess(input: {
  businessId?: string | null;
  once: boolean;
  stdio: "inherit" | "pipe";
}) {
  const tsx = path.join(process.cwd(), "node_modules", ".bin", "tsx");
  const args = ["scripts/local-worker.ts"];
  if (!input.once) args.push("--loop");
  return spawn(tsx, args, {
    cwd: process.cwd(),
    stdio: input.stdio === "inherit" ? "inherit" : ["ignore", "pipe", "pipe"],
    env: {
      ...process.env,
      WORKER_BUSINESS_ID: input.businessId ?? "",
      WORKER_CLAIM_LIMIT: process.env.WORKER_CLAIM_LIMIT || "6",
      WORKER_IDLE_MS: process.env.WORKER_IDLE_MS || "3000",
      WORKER_VERBOSE: process.env.WORKER_VERBOSE || "1"
    }
  });
}

async function runWorkerForBusiness(input: { businessId?: string | null; once: boolean }) {
  const child = spawnWorkerProcess({ ...input, stdio: "inherit" });
  await new Promise<void>((resolve, reject) => {
    const onSigint = () => child.kill("SIGINT");
    process.once("SIGINT", onSigint);
    child.on("exit", (code, signal) => {
      process.off("SIGINT", onSigint);
      if (code === 0 || signal === "SIGINT") resolve();
      else reject(new Error(`Worker exited with ${signal ?? code}.`));
    });
    child.on("error", (error) => {
      process.off("SIGINT", onSigint);
      reject(error);
    });
  });
}

function delay(ms: number) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function printCronDispatchResult(result: Awaited<ReturnType<typeof dispatchDueCronJobs>>, json: boolean) {
  if (json) return print(result, true);
  console.log(`${tag("cron", ansi.brightBlue)} claimed ${bold(String(result.claimed))} due job${result.claimed === 1 ? "" : "s"}`);
  for (const item of result.results) {
    const status = item.status === "completed" ? color(item.status, ansi.green) : color(item.status, item.status === "blocked" ? ansi.yellow : ansi.red);
    console.log(`  ${status} ${color(item.job_key, ansi.cyan)}${item.message ? ` ${dim(item.message)}` : ""}`);
  }
}

async function runCronLoop(input: { once: boolean; intervalMs: number; limit: number; json: boolean }) {
  do {
    const result = await dispatchDueCronJobs({ dispatcherId: "takyon-local-cron", limit: input.limit });
    if (input.once || input.json || result.claimed > 0) printCronDispatchResult(result, input.json);
    if (input.once) return;
    await delay(input.intervalMs);
  } while (true);
}

async function runLocalVps(input: { once: boolean; cronIntervalMs: number; limit: number; json: boolean }) {
  if (input.once) {
    const result = await dispatchDueCronJobs({ dispatcherId: "takyon-local-vps", limit: input.limit });
    printCronDispatchResult(result, input.json);
    if (!input.json) console.log(`${tag("worker", ansi.brightBlue)} running one local worker pass`);
    await runWorkerForBusiness({ businessId: null, once: true });
    return;
  }

  if (!input.json) {
    console.log(`${tag("vps", ansi.brightBlue)} local Mac VPS started`);
    console.log(`  ${color("cron", ansi.cyan)} every ${input.cronIntervalMs}ms`);
    console.log(`  ${color("worker", ansi.cyan)} global local-worker loop`);
    console.log(`  ${dim("Press Ctrl-C to stop.")}`);
  }
  const worker = spawnWorkerProcess({ businessId: null, once: false, stdio: "inherit" });
  let closed = false;
  const runCron = () => {
    void dispatchDueCronJobs({ dispatcherId: "takyon-local-vps", limit: input.limit })
      .then((result) => {
        if (result.claimed > 0) printCronDispatchResult(result, input.json);
      })
      .catch((error) => {
        console.error(`${tag("cron", ansi.red)} ${error instanceof Error ? error.message : String(error)}`);
      });
  };
  runCron();
  const timer = setInterval(runCron, input.cronIntervalMs);
  await new Promise<void>((resolve, reject) => {
    const stop = () => {
      if (closed) return;
      closed = true;
      clearInterval(timer);
      if (!worker.killed) worker.kill("SIGINT");
    };
    const onSigint = () => stop();
    const onSigterm = () => stop();
    process.once("SIGINT", onSigint);
    process.once("SIGTERM", onSigterm);
    worker.on("exit", (code, signal) => {
      process.off("SIGINT", onSigint);
      process.off("SIGTERM", onSigterm);
      clearInterval(timer);
      if (code === 0 || signal === "SIGINT" || closed) resolve();
      else reject(new Error(`Local VPS worker exited with ${signal ?? code}.`));
    });
    worker.on("error", (error) => {
      process.off("SIGINT", onSigint);
      process.off("SIGTERM", onSigterm);
      clearInterval(timer);
      reject(error);
    });
  });
}

async function runTakyonGc(input: {
  businessId?: string | null;
  olderThanDays: number;
  maxDelete: number;
  confirm: boolean;
}) {
  const sql = db();
  const cutoff = new Date(Date.now() - input.olderThanDays * 24 * 60 * 60 * 1000);
  const businessId = input.businessId ?? null;
  const terminalStatuses = ["completed", "blocked", "failed", "cancelled"];
  const eventKinds = [
    "workflow.job_queued",
    "workflow.job_completed",
    "workflow.job_blocked",
    "workflow.job_failed",
    "workflow.job_cancelled",
    "workflow.job_retry_queued",
    "workflow.job_lock_recovered",
    "takyon.autopilot_planned"
  ];

  const [workflowRows, runRows, eventRows] = await Promise.all([
    sql<{ count: number }[]>`
      SELECT count(*)::int AS count
      FROM workflow_jobs
      WHERE status IN ${sql(terminalStatuses)}
        AND updated_at < ${cutoff}
        AND (${businessId}::uuid IS NULL OR business_id = ${businessId})
    `,
    sql<{ count: number }[]>`
      SELECT count(*)::int AS count
      FROM agent_runs
      WHERE status IN ${sql(terminalStatuses)}
        AND updated_at < ${cutoff}
        AND (${businessId}::uuid IS NULL OR business_id = ${businessId})
    `,
    sql<{ count: number }[]>`
      SELECT count(*)::int AS count
      FROM events
      WHERE kind IN ${sql(eventKinds)}
        AND created_at < ${cutoff}
        AND (${businessId}::uuid IS NULL OR business_id = ${businessId})
    `
  ]);

  if (!input.confirm) {
    return {
      dryRun: true,
      cutoff: cutoff.toISOString(),
      olderThanDays: input.olderThanDays,
      maxDelete: input.maxDelete,
      wouldDelete: {
        workflowJobs: workflowRows[0]?.count ?? 0,
        agentRuns: runRows[0]?.count ?? 0,
        events: eventRows[0]?.count ?? 0
      },
      next: "Re-run with `confirm` to delete these conservative maintenance rows."
    };
  }

  const [deletedWorkflowJobs, deletedAgentRuns, deletedEvents] = await Promise.all([
    sql<{ id: string }[]>`
      WITH doomed AS (
        SELECT id
        FROM workflow_jobs
        WHERE status IN ${sql(terminalStatuses)}
          AND updated_at < ${cutoff}
          AND (${businessId}::uuid IS NULL OR business_id = ${businessId})
        ORDER BY updated_at ASC
        LIMIT ${input.maxDelete}
      )
      DELETE FROM workflow_jobs
      WHERE id IN (SELECT id FROM doomed)
      RETURNING id
    `,
    sql<{ id: string }[]>`
      WITH doomed AS (
        SELECT id
        FROM agent_runs
        WHERE status IN ${sql(terminalStatuses)}
          AND updated_at < ${cutoff}
          AND (${businessId}::uuid IS NULL OR business_id = ${businessId})
        ORDER BY updated_at ASC
        LIMIT ${input.maxDelete}
      )
      DELETE FROM agent_runs
      WHERE id IN (SELECT id FROM doomed)
      RETURNING id
    `,
    sql<{ id: string }[]>`
      WITH doomed AS (
        SELECT id
        FROM events
        WHERE kind IN ${sql(eventKinds)}
          AND created_at < ${cutoff}
          AND (${businessId}::uuid IS NULL OR business_id = ${businessId})
        ORDER BY created_at ASC
        LIMIT ${input.maxDelete}
      )
      DELETE FROM events
      WHERE id IN (SELECT id FROM doomed)
      RETURNING id
    `
  ]);

  return {
    dryRun: false,
    cutoff: cutoff.toISOString(),
    deleted: {
      workflowJobs: deletedWorkflowJobs.length,
      agentRuns: deletedAgentRuns.length,
      events: deletedEvents.length
    }
  };
}

type DeleteBusinessRow = {
  id: string;
  name: string;
  slug: string;
  status: string;
};

async function removeGeneratedDirsForBusinesses(businessIds: string[]) {
  const removed: string[] = [];
  for (const businessId of businessIds) {
    const dir = path.join(process.cwd(), ".takyon", "generated", businessId);
    await rm(dir, { recursive: true, force: true });
    removed.push(dir);
  }
  return removed;
}

async function removeWorkspaceDirsForBusinesses(businesses: Array<{ slug: string }>) {
  const removed: string[] = [];
  for (const business of businesses) {
    removed.push(await removeBusinessWorkspace({ slug: business.slug }));
  }
  return removed;
}

async function deleteBusinessesForProfile(input: {
  profileId: string;
  businessId?: string | null;
  confirm: boolean;
}) {
  const sql = db();
  const targets = input.businessId
    ? await sql<DeleteBusinessRow[]>`
        SELECT b.id, b.name, b.slug, b.status
        FROM businesses b
        JOIN business_memberships bm ON bm.business_id = b.id
        WHERE bm.profile_id = ${input.profileId}
          AND b.id = ${input.businessId}
        ORDER BY b.created_at DESC
      `
    : await sql<DeleteBusinessRow[]>`
        SELECT b.id, b.name, b.slug, b.status
        FROM businesses b
        JOIN business_memberships bm ON bm.business_id = b.id
        WHERE bm.profile_id = ${input.profileId}
        ORDER BY b.created_at DESC
      `;

  if (!input.confirm) {
    return {
      dryRun: true as const,
      count: targets.length,
      businesses: targets,
      next: input.businessId
        ? "Re-run with `confirm` to delete this business and its cascaded rows."
        : "Re-run with `confirm` to delete all businesses visible to this terminal profile."
    };
  }

  const ids = targets.map((business) => business.id);
  const deleted = ids.length
    ? await sql<DeleteBusinessRow[]>`
        DELETE FROM businesses
        WHERE id IN ${sql(ids)}
        RETURNING id, name, slug, status
      `
    : [];
  const localGeneratedDirsRemoved = await removeGeneratedDirsForBusinesses(ids);
  const localWorkspaceDirsRemoved = await removeWorkspaceDirsForBusinesses(targets);
  return {
    dryRun: false as const,
    deleted,
    localGeneratedDirsRemoved,
    localWorkspaceDirsRemoved,
    note: "Business-scoped database rows were removed by ON DELETE CASCADE. External deployments/provider assets are not removed by this local purge."
  };
}

type LiveJobSnapshot = {
  status: string;
  attempts: number;
  error: string | null;
  lockedBy: string | null;
};

type LiveSession = {
  businessId: string;
  profileId: string;
  slug: string;
  name: string;
  worker: ChildProcess | null;
  monitor: NodeJS.Timeout | null;
  lastJobs: Map<string, LiveJobSnapshot>;
  logsEnabled: boolean;
  closed: boolean;
};

let suppressLiveWritePrompt = false;
let beforeLiveWrite: (() => void) | null = null;
let afterLiveWritePrompt: (() => void) | null = null;

class ShellOperationCancelled extends Error {
  constructor(label: string) {
    super(`${label} cancelled.`);
    this.name = "ShellOperationCancelled";
  }
}

function isAbortLike(error: unknown) {
  if (!(error instanceof Error)) return false;
  return error.name === "AbortError"
    || error.name === "TimeoutError"
    || error.message.toLowerCase().includes("aborted")
    || error.message.toLowerCase().includes("abort");
}

function jobSnapshot(job: WorkflowJobRow): LiveJobSnapshot {
  return {
    status: job.status,
    attempts: job.attempts,
    error: job.error,
    lockedBy: job.locked_by
  };
}

function jobChanged(previous: LiveJobSnapshot | undefined, next: LiveJobSnapshot) {
  return !previous
    || previous.status !== next.status
    || previous.attempts !== next.attempts
    || previous.error !== next.error
    || previous.lockedBy !== next.lockedBy;
}

function liveWrite(rl: LiveReadline, message: string) {
  const text = message.trimEnd();
  if (!text) return;
  if ((rl as LiveReadline & { closed?: boolean }).closed) {
    console.log(text);
    return;
  }
  if (!output.isTTY) {
    console.log(text);
    return;
  }

  beforeLiveWrite?.();
  clearLine(output, 0);
  cursorTo(output, 0);
  output.write(`${text}\n`);
  if (!suppressLiveWritePrompt) {
    rl.prompt(true);
    afterLiveWritePrompt?.();
  }
}

function startSpinner(rl: LiveReadline, label: string, message: string) {
  const frames = ["-", "\\", "|", "/"];
  if (!output.isTTY) {
    console.log(`${tag(label, theme.primary)} ${message}`);
    return { stop: () => undefined };
  }

  let index = 0;
  const render = () => {
    clearLine(output, 0);
    cursorTo(output, 0);
    output.write(`${tag(label, theme.primary)} ${color(frames[index % frames.length], theme.secondary)} ${message}`);
    index += 1;
  };
  render();
  const timer = setInterval(render, 120);
  return {
    stop: () => {
      clearInterval(timer);
      clearLine(output, 0);
      cursorTo(output, 0);
    }
  };
}

function activeSummary(jobs: WorkflowJobRow[]) {
  const active = jobs.filter((job) => ["queued", "running", "blocked", "failed"].includes(job.status));
  if (!active.length) return "no active jobs";
  const counts = active.reduce<Record<string, number>>((acc, job) => {
    acc[job.status] = (acc[job.status] ?? 0) + 1;
    return acc;
  }, {});
  return Object.entries(counts).map(([status, count]) => `${count} ${paintStatus(status)}`).join(", ");
}

async function sessionPanel(session: LiveSession, rl: LiveReadline, uiMode: "compact" | "full") {
  const [jobs, capabilities, workspace, commands, runtime] = await Promise.all([
    listWorkflowJobs(session.businessId, 30),
    listToolCapabilities({ businessId: session.businessId, profileId: session.profileId }),
    syncBusinessWorkspace({ businessId: session.businessId, reason: "terminal_session_panel" }),
    listTakyonHarnessCommands(),
    getTakyonRuntimeStatus({ businessId: session.businessId, profileId: session.profileId })
  ]);
  const blockedCapabilities = capabilities.filter((capability) => !capability.canRun);
  const header = `${tag("session", ansi.brightBlue)} ${bold(session.name)} ${dim(session.slug)} ${color(activeSummary(jobs), ansi.cyan)}`;
  if (uiMode === "compact") {
    liveWrite(rl, `${header}; ${dim(`${workspace.files.length} files`)}; ${runtime.localMac.ok ? color("runtime ok", ansi.green) : color("runtime blocked", ansi.red)}; ${blockedCapabilities.length ? color(`${blockedCapabilities.length} blocked caps`, ansi.yellow) : color("caps ok", ansi.green)}`);
    return;
  }
  liveWrite(rl, [
    header,
    `  ${color("workspace", ansi.cyan)} ${workspace.root}`,
    `  ${color("files", ansi.cyan)} ${workspace.files.length} ${dim("business filesystem entries")}`,
    `  ${color("runtime", ansi.brightBlue)} local Mac ${runtime.localMac.ok ? color("ok", ansi.green) : color("blocked", ansi.red)}; cron ${runtime.cron.active} active/${runtime.cron.due} due; worker queued ${runtime.worker.queued}, running ${runtime.worker.running}`,
    `  ${color("capabilities", ansi.cyan)} ${capabilities.length - blockedCapabilities.length} ok, ${blockedCapabilities.length ? color(`${blockedCapabilities.length} blocked`, ansi.yellow) : color("0 blocked", ansi.green)}`,
    `  ${color("harness", theme.primary)} ${commands.map((command) => `/${command.name}`).join(" ")}`,
    `  ${dim("controls")} /runtimes /workspace /files /read /commands /ui compact /logs on /auto off /kill business`
  ].join("\n"));
}

function liveJobLine(job: WorkflowJobRow, previous?: LiveJobSnapshot) {
  const transition = previous && previous.status !== job.status
    ? `${paintStatus(previous.status)} -> ${paintStatus(job.status)}`
    : paintStatus(job.status);
  const suffix = job.error ? ` ${color("error=", ansi.red)}${job.error}` : "";
  return `${tag("work", ansi.cyan)} ${transition} ${color(job.workflow_id, ansi.cyan)} ${dim(shortId(job.id))}${suffix}`;
}

async function pollLiveSession(session: LiveSession, rl: LiveReadline, initial = false) {
  if (session.closed) return;
  const jobs = await listWorkflowJobs(session.businessId, 30);
  if (initial) {
    session.lastJobs = new Map(jobs.map((job) => [job.id, jobSnapshot(job)]));
    liveWrite(rl, `${tag("live", ansi.brightBlue)} attached to ${bold(session.name)}; ${activeSummary(jobs)}`);
    for (const job of jobs.filter((item) => ["queued", "running", "blocked"].includes(item.status)).slice(0, 6).reverse()) {
      liveWrite(rl, liveJobLine(job));
    }
    return;
  }

  for (const job of jobs.slice().reverse()) {
    const snapshot = jobSnapshot(job);
    const previous = session.lastJobs.get(job.id);
    if (jobChanged(previous, snapshot)) {
      session.lastJobs.set(job.id, snapshot);
      liveWrite(rl, liveJobLine(job, previous));
    }
  }
}

function pipeWorkerOutput(child: ChildProcess, session: LiveSession, rl: LiveReadline) {
  const attach = (stream: NodeJS.ReadableStream | null, prefix: string) => {
    if (!stream) return;
    let buffer = "";
    stream.setEncoding("utf8");
    stream.on("data", (chunk: string) => {
      buffer += chunk;
      const lines = buffer.split(/\r?\n/);
      buffer = lines.pop() ?? "";
      for (const line of lines) {
        const text = line.trim();
        if (text && session.logsEnabled) {
          const renderedPrefix = prefix ? color(prefix, ansi.red) : dim("worker ");
          liveWrite(rl, `${renderedPrefix}${text}`);
        }
      }
    });
  };
  attach(child.stdout, "");
  attach(child.stderr, "stderr: ");
}

function startLiveWorker(session: LiveSession, rl: LiveReadline) {
  if (session.worker && !session.worker.killed) return;
  const child = spawnWorkerProcess({ businessId: session.businessId, once: false, stdio: "pipe" });
  session.worker = child;
  pipeWorkerOutput(child, session, rl);
  child.on("exit", (code, signal) => {
    if (session.worker === child) session.worker = null;
    if (!session.closed) liveWrite(rl, `${tag("live", ansi.brightBlue)} worker stopped ${dim(`(${signal ?? code ?? "exit"})`)}`);
  });
  child.on("error", (error) => {
    if (!session.closed) liveWrite(rl, `${tag("live", ansi.red)} worker failed: ${error.message}`);
  });
  liveWrite(rl, `${tag("live", ansi.brightBlue)} worker loop started in background`);
}

async function stopLiveSession(session: LiveSession | null, rl: LiveReadline, reason = "detached") {
  if (!session || session.closed) return;
  session.closed = true;
  if (session.monitor) clearInterval(session.monitor);
  if (session.worker && !session.worker.killed) session.worker.kill("SIGINT");
  if (!(rl as LiveReadline & { closed?: boolean }).closed) {
    liveWrite(rl, `${tag("live", ansi.brightBlue)} ${reason}: ${session.name}`);
  }
}

function concisePlan(plan: AutopilotPlan) {
  const queued = plan.queued.filter((item) => item.status === "queued").length;
  const existing = plan.queued.filter((item) => item.status === "already_present").length;
  const blocked = [...plan.queued.filter((item) => item.status === "blocked"), ...plan.blocked.filter((item) => item.status === "blocked")];
  const lines = [`${tag("ceo", theme.primary)} ${bold(plan.business.name)}: ${plan.reasons.join(" ")}`];
  lines.push(`${tag("ceo", theme.primary)} wakeup ${plan.ceoWakeupJobId ? dim(shortId(plan.ceoWakeupJobId)) : color("not queued", ansi.yellow)}; ${color(`queued ${queued}`, theme.secondary)}; ${dim(`already active ${existing}`)}; ${blocked.length ? color(`blocked ${blocked.length}`, ansi.yellow) : "blocked 0"}`);
  for (const item of blocked.slice(0, 4)) {
    lines.push(`${tag("blocked", ansi.yellow)} ${item.workflow_id}: ${item.reason ?? "capability unavailable"}`);
  }
  return lines.join("\n");
}

function parseInput(line: string) {
  const tokens: string[] = [];
  const matcher = /"([^"]*)"|'([^']*)'|(\S+)/g;
  for (const match of line.matchAll(matcher)) {
    tokens.push(match[1] ?? match[2] ?? match[3]);
  }
  return tokens;
}

function usage() {
  return [
    "Takyon terminal control",
    "",
    "Interactive:",
    "  ./takyon shell",
    "  plain text                            # chat/instruct the Takyon agent",
    "  /businesses",
    "  /create <name> --pitch <pitch> [--budget-usd 100]",
    "  /delete business <business-id-or-slug> [confirm]",
    "  /delete businesses [confirm]",
    "  /use <business-id-or-slug>",
    "  /status",
    "  /runtimes",
    "  /workspace",
    "  /commands",
    "  /skills",
    "  /jobs",
    "  /files [path]",
    "  /read <path>",
    "  /ui compact|full",
    "  /capabilities",
    "  /campaigns",
    "  /auto on|off                          # start/stop background worker + stream",
    "  /logs on|off                          # show/hide worker internals",
    "",
    "Commands:",
    "  ./takyon businesses",
    "  ./takyon build <name> --pitch <pitch> [--budget-usd 100]",
    "  ./takyon business <business-id-or-slug>",
    "  ./takyon runtimes",
    "  ./takyon cron status [--all]",
    "  ./takyon cron run|loop [--interval-ms 60000] [--limit 5]",
    "  ./takyon vps [--once] [--cron-interval-ms 60000] [--limit 5]",
    "  ./takyon workspace <business-id-or-slug>",
    "  ./takyon files <business-id-or-slug> [path]",
    "  ./takyon read <business-id-or-slug> <path>",
    "  ./takyon write <business-id-or-slug> <path> --stdin",
    "  ./takyon commands",
    "  ./takyon harness",
    "  ./takyon command <business-id-or-slug> <harness-command> [args...]",
    "  ./takyon jobs <business-id-or-slug> [--limit 30]",
    "  ./takyon capabilities [business-id-or-slug]",
    "  ./takyon setup <capability> [business-id-or-slug]",
    "  ./takyon connect x [business-id-or-slug] [--platform|--business|--profile] [--open]",
    "  ./takyon secret set <ENV_KEY> --stdin",
    "  ./takyon delete business <business-id-or-slug> [confirm]",
    "  ./takyon delete businesses [confirm]",
    "  ./takyon campaigns <business-id-or-slug>",
    "  ./takyon campaign create <business> <name> [--budget-usd 50] [--kind distribution]",
    "  ./takyon budget set <business> --usd 100 [--campaign <campaign-id-or-slug>]",
    "  ./takyon budget reserve <business> --usd 10 --purpose <text> [--campaign <campaign-id-or-slug>]",
    "  ./takyon memory list <business> [--namespace strategy]",
    "  ./takyon memory record <business> <key> <content> [--namespace strategy]",
    "  ./takyon wake <business> [operator instruction...]",
    "  ./takyon run <business> [operator instruction...] [--once|--no-worker]",
    "  ./takyon goal <business> get_first_customer",
    "  ./takyon watch <business> [--once] [--interval-ms 3000] [--limit 30]",
    "  ./takyon gc [business-id-or-slug] [--older-than-days 30] [--max-delete 1000] [confirm]",
    "  ./takyon enqueue <business> <workflow-id> [--campaign <campaign-id-or-slug>]",
    "  ./takyon pause|resume|kill business <business> [reason]",
    "  ./takyon pause|resume|kill campaign <business> <campaign> [reason]",
    "  ./takyon pause|resume|kill job <workflow-job-id> [reason]",
    "  ./takyon pause|resume|kill agent <agent-run-id> [reason]",
    "  ./takyon pause|resume|kill provider <provider> [reason]",
    "  ./takyon pause|resume|kill global [reason]",
    "",
    "Use --json for machine-readable output."
  ].join("\n");
}

async function terminalProfile() {
  const seed = getLocalAuthSeed();
  return upsertProfile({
    authProvider: process.env.TAKYON_TERMINAL_AUTH_PROVIDER || "local-dev",
    authSubject: process.env.TAKYON_TERMINAL_SUBJECT || seed.subject,
    email: process.env.TAKYON_TERMINAL_EMAIL || seed.email,
    name: process.env.TAKYON_TERMINAL_NAME || seed.name
  });
}

async function resolveBusiness(idOrSlug: string, profileId: string) {
  const byId = await getCompanyForProfile(idOrSlug, profileId).catch(() => null);
  if (byId) return byId;
  const sql = db();
  const rows = await sql<{ id: string }[]>`
    SELECT b.id
    FROM businesses b
    JOIN business_memberships bm ON bm.business_id = b.id
    WHERE b.slug = ${idOrSlug}
      AND bm.profile_id = ${profileId}
    LIMIT 1
  `;
  if (!rows[0]) throw new Error(`Business not found or not accessible: ${idOrSlug}`);
  const bySlug = await getCompanyForProfile(rows[0].id, profileId);
  if (!bySlug) throw new Error(`Business not found or not accessible: ${idOrSlug}`);
  return bySlug;
}

async function assertWorkflowAvailable(workflowId: string, businessId: string, profileId: string) {
  const groups = takyonCapabilityGroups(workflowId);
  const block = await preflightCapabilityGroups({ workflowId, groups, businessId, profileId });
  if (!block) return;
  throw new Error(
    [
      block.error,
      block.missing.length ? `Missing: ${block.missing.join(", ")}` : "",
      ...block.reports.map((report) => `Source ${report.source}:${report.key}${report.missing.length ? ` missing ${report.missing.join(", ")}` : ""}${report.detail ? ` - ${report.detail}` : ""}`),
      ...block.setup.map((step) => `Setup: ${step}`)
    ].filter(Boolean).join("\n")
  );
}

async function readSecretValue(args: string[]) {
  if (hasFlag(args, "--stdin")) {
    const chunks: Buffer[] = [];
    for await (const chunk of process.stdin) chunks.push(Buffer.from(chunk));
    return Buffer.concat(chunks).toString("utf8").trim();
  }
  const value = args[3];
  if (value) return value;
  const rl = createQuestionInterface({ input, output });
  try {
    return (await rl.question("value: ")).trim();
  } finally {
    rl.close();
  }
}

async function printConnectX(inputArgs: string[], profile: TerminalProfile, json: boolean) {
  const businessArg = inputArgs.find((arg) => !arg.startsWith("--") && arg !== "connect" && arg !== "x") || "";
  const business = businessArg ? await resolveBusiness(businessArg, profile.id) : null;
  const forceBusiness = hasFlag(inputArgs, "--business");
  const forceProfile = hasFlag(inputArgs, "--profile");
  const forcePlatform = hasFlag(inputArgs, "--platform") || (!forceBusiness && !forceProfile);
  const forcedScopes = [forceBusiness, forceProfile, hasFlag(inputArgs, "--platform")].filter(Boolean).length;
  if (forcedScopes > 1) throw new Error("Choose only one X scope: --platform, --business, or --profile.");
  if (forceBusiness && !business) throw new Error("Use: ./takyon connect x <business-id-or-slug> --business");
  const base = getAppEnv().APP_BASE_URL;
  const url = new URL("/api/integrations/x/oauth/start", base);
  if (business) url.searchParams.set("businessId", business.id);
  if (forcePlatform) url.searchParams.set("scope", "platform");
  else if (forceProfile) url.searchParams.set("scope", "profile");
  url.searchParams.set("returnTo", business ? `/dashboard/companies/${business.id}` : "/dashboard");
  const result = {
    provider: "x",
    scope: forceBusiness && business ? "business" : forceProfile ? "profile" : "platform",
    business: business ? { id: business.id, slug: business.slug, name: business.name } : null,
    url: url.toString(),
    next: "Open this URL while signed into Takyon. OAuth stores encrypted X access and refresh tokens for the selected scope. Platform scope is the shared outreach identity used by business posting."
  };
  if (hasFlag(inputArgs, "--open")) spawnSync("open", [url.toString()], { stdio: "ignore" });
  return print(result, json);
}

async function runCommand(cleanArgs: string[], profile: TerminalProfile, json: boolean) {
  const command = cleanArgs[0] || "help";
  if (command === "help" || command === "-h" || command === "--help") return print(usage(), json);

  if (command === "runtime" || command === "runtimes") {
    return printRuntimeStatus(await getTakyonRuntimeStatus({ profileId: profile.id }), json);
  }

  if (command === "cron") {
    const subcommand = cleanArgs[1] || "status";
    const limit = Math.max(1, Number(flag(cleanArgs, "--limit", "5")));
    const intervalMs = Math.max(1000, Number(flag(cleanArgs, "--interval-ms", "60000")));
    if (subcommand === "status" || subcommand === "list") return printCronJobs(await listCronJobs({ profileId: profile.id, includeAll: hasFlag(cleanArgs, "--all") }), json);
    if (subcommand === "run" || subcommand === "dispatch") {
      return printCronDispatchResult(await dispatchDueCronJobs({ dispatcherId: "takyon-local-cli", limit }), json);
    }
    if (subcommand === "loop") return runCronLoop({ once: false, intervalMs, limit, json });
    throw new Error("Use: ./takyon cron status|run|loop [--interval-ms 60000] [--limit 5]");
  }

  if (command === "vps") {
    const cronIntervalMs = Math.max(1000, Number(flag(cleanArgs, "--cron-interval-ms", flag(cleanArgs, "--interval-ms", "60000"))));
    const limit = Math.max(1, Number(flag(cleanArgs, "--limit", "5")));
    return runLocalVps({ once: hasFlag(cleanArgs, "--once"), cronIntervalMs, limit, json });
  }

  if (command === "businesses" || command === "list") {
    return printBusinesses(await listCompaniesForProfile(profile.id, 100), json);
  }

  if (command === "build" || command === "create") {
    const { name, pitch } = buildArgs(cleanArgs);
    if (!name || !pitch || pitch.length < 8) {
      throw new Error("Use: ./takyon build <name> --pitch <what this business does> [--budget-usd 100]");
    }
    const created = await createCompany({ name, pitch }, profile);
    const budgetUsd = Number(flag(cleanArgs, "--budget-usd", "0"));
    if (budgetUsd > 0) {
      await ensureBudgetAccount({
        businessId: created.company.id,
        hardLimitMicrousd: usdToMicrousd(budgetUsd),
        metadata: { source: "takyon_terminal_build" }
      });
    }
    const plan = await runBusinessAutopilot({
      businessId: created.company.id,
      profileId: profile.id,
      instruction: `Build this business end to end. ${pitch}`,
      campaignBudgetUsd: budgetUsd > 0 ? budgetUsd : null
    });
    if (json) return print({ created, autopilot: plan }, true);
    console.log(`Created business: ${created.company.name} (${created.company.slug})`);
    console.log("");
    return printAutopilotPlan(plan, false);
  }

  if (command === "business") {
    const business = await resolveBusiness(cleanArgs[1], profile.id);
    const workspace = await syncBusinessWorkspace({ businessId: business.id, profileId: profile.id, reason: "terminal_business_read" });
    const [campaigns, capabilities, memory] = await Promise.all([
      listBusinessCampaigns(business.id, 20),
      listToolCapabilities({ businessId: business.id, profileId: profile.id }),
      listBusinessMemory({ businessId: business.id, limit: 20 })
    ]);
    return print({ business, workspace: { root: workspace.root, fileCount: workspace.files.length }, campaigns, capabilities, memory }, json);
  }

  if (command === "workspace") {
    const business = await resolveBusiness(cleanArgs[1], profile.id);
    const workspace = await syncBusinessWorkspace({ businessId: business.id, profileId: profile.id, reason: "terminal_workspace_sync" });
    if (json) return print(workspace, true);
    console.log(`${tag("workspace", ansi.brightBlue)} ${bold(business.name)} ${dim(business.slug)}`);
    console.log(`${color("root:", ansi.cyan)} ${workspace.root}`);
    return printWorkspaceFiles(workspace.files, false);
  }

  if (command === "files") {
    const business = await resolveBusiness(cleanArgs[1], profile.id);
    await syncBusinessWorkspace({ businessId: business.id, profileId: profile.id, reason: "terminal_files" });
    return printWorkspaceFiles(
      await listBusinessWorkspaceFiles({ businessId: business.id, relativePath: cleanArgs[2] || "." }),
      json
    );
  }

  if (command === "read") {
    const business = await resolveBusiness(cleanArgs[1], profile.id);
    const relativePath = cleanArgs[2];
    if (!relativePath) throw new Error("Use: ./takyon read <business-id-or-slug> <path>");
    await syncBusinessWorkspace({ businessId: business.id, profileId: profile.id, reason: "terminal_read" });
    const file = await readBusinessWorkspaceFile({ businessId: business.id, relativePath });
    if (json) return print(file, true);
    console.log(`${tag("read", ansi.brightBlue)} ${color(file.path, ansi.cyan)} ${dim(`${file.bytes}b${file.truncated ? ", truncated" : ""}`)}`);
    console.log(file.content);
    return;
  }

  if (command === "commands" || command === "skills") {
    return printHarnessCommands(await listTakyonHarnessCommands(), json);
  }

  if (command === "harness") {
    const [settings, commands] = await Promise.all([readTakyonHarnessSettings(), listTakyonHarnessCommands()]);
    return print({ settings, commands: commands.map(({ body: _body, ...item }) => item) }, json);
  }

  if (command === "command") {
    const business = await resolveBusiness(cleanArgs[1], profile.id);
    const harnessName = cleanArgs[2];
    if (!harnessName) throw new Error("Use: ./takyon command <business-id-or-slug> <harness-command> [args...]");
    const harnessCommand = await getTakyonHarnessCommand(harnessName);
    if (!harnessCommand) throw new Error(`Unknown harness command: ${harnessName}. Use ./takyon commands.`);
    const workspace = await syncBusinessWorkspace({ businessId: business.id, profileId: profile.id, reason: "harness_command" });
    const instruction = renderTakyonHarnessCommand({
      command: harnessCommand,
      args: cleanArgs.slice(3),
      businessSlug: business.slug,
      workspaceRoot: workspace.root
    });
    return printAutopilotPlan(await runBusinessAutopilot({ businessId: business.id, profileId: profile.id, instruction }), json);
  }

  if (command === "write") {
    const business = await resolveBusiness(cleanArgs[1], profile.id);
    const relativePath = cleanArgs[2];
    if (!relativePath) throw new Error("Use: ./takyon write <business-id-or-slug> <path> --stdin");
    const content = hasFlag(cleanArgs, "--stdin")
      ? await readSecretValue(["secret", "set", "WORKSPACE_FILE", "--stdin"])
      : cleanArgs.slice(3).join(" ");
    const written = await writeBusinessWorkspaceFile({ businessId: business.id, relativePath, content });
    return print(written, json);
  }

  if (command === "jobs") {
    const business = await resolveBusiness(cleanArgs[1], profile.id);
    const limit = Math.max(1, Number(flag(cleanArgs, "--limit", "30")));
    return printWorkflowJobs(await listWorkflowJobs(business.id, limit), json);
  }

  if (command === "capabilities") {
    const businessArg = cleanArgs[1];
    const business = businessArg ? await resolveBusiness(businessArg, profile.id) : null;
    return printCapabilities(await listToolCapabilities({ businessId: business?.id ?? null, profileId: profile.id }), json);
  }

  if (command === "setup") {
    const capabilityKey = cleanArgs[1];
    if (!capabilityKey) throw new Error("Use: ./takyon setup <capability> [business-id-or-slug]");
    const businessArg = cleanArgs[2];
    const business = businessArg ? await resolveBusiness(businessArg, profile.id) : null;
    const capabilities = await listToolCapabilities({ businessId: business?.id ?? null, profileId: profile.id });
    const capability = capabilities.find((item) => item.key === capabilityKey || item.label.toLowerCase().includes(capabilityKey));
    if (!capability) throw new Error(`Unknown capability: ${capabilityKey}`);
    return printCapabilities([capability], json);
  }

  if (command === "connect" && cleanArgs[1] === "x") {
    return printConnectX(cleanArgs, profile, json);
  }

  if (command === "delete" || command === "purge") {
    const scope = cleanArgs[1];
    const confirm = cleanArgs.includes("confirm");
    if (scope === "business") {
      const business = await resolveBusiness(cleanArgs[2], profile.id);
      return printBusinessDeleteResult(
        await deleteBusinessesForProfile({ profileId: profile.id, businessId: business.id, confirm }),
        json
      );
    }
    if (scope === "businesses" || scope === "all") {
      return printBusinessDeleteResult(
        await deleteBusinessesForProfile({ profileId: profile.id, confirm }),
        json
      );
    }
    throw new Error("Use: ./takyon delete business <business-id-or-slug> [confirm] OR ./takyon delete businesses [confirm]");
  }

  if (command === "secret" && cleanArgs[1] === "set") {
    const key = cleanArgs[2];
    if (!key) throw new Error("Use: ./takyon secret set <ENV_KEY> --stdin");
    const value = await readSecretValue(cleanArgs);
    if (!value) throw new Error("Refusing to store an empty secret.");
    const envPath = upsertLocalSecret(key, value);
    return print({ ok: true, key, envPath, next: "Restart any running worker/server so it picks up the new secret." }, json);
  }

  if (command === "campaigns") {
    const business = await resolveBusiness(cleanArgs[1], profile.id);
    return printCampaigns(await listBusinessCampaigns(business.id, 100), json);
  }

  if (command === "campaign" && cleanArgs[1] === "create") {
    const business = await resolveBusiness(cleanArgs[2], profile.id);
    const name = cleanArgs[3];
    if (!name) throw new Error("Campaign name is required.");
    const budgetUsd = Number(flag(cleanArgs, "--budget-usd", "0"));
    const campaign = await upsertBusinessCampaign({
      businessId: business.id,
      name,
      kind: flag(cleanArgs, "--kind", "campaign"),
      status: "active",
      budgetCapMicrousd: budgetUsd > 0 ? usdToMicrousd(budgetUsd) : null,
      profileId: profile.id,
      metadata: { source: "takyon_terminal" }
    });
    if (budgetUsd > 0) {
      await ensureBudgetAccount({
        businessId: business.id,
        campaignId: campaign.id,
        hardLimitMicrousd: usdToMicrousd(budgetUsd),
        metadata: { source: "takyon_terminal" }
      });
    }
    return print(campaign, json);
  }

  if (command === "budget" && cleanArgs[1] === "set") {
    const business = await resolveBusiness(cleanArgs[2], profile.id);
    const campaignArg = flag(cleanArgs, "--campaign");
    const campaign = campaignArg ? await requireBusinessCampaign({ businessId: business.id, campaignIdOrSlug: campaignArg }) : null;
    const usd = Number(flag(cleanArgs, "--usd", "0"));
    const account = await ensureBudgetAccount({
      businessId: business.id,
      campaignId: campaign?.id ?? null,
      hardLimitMicrousd: usdToMicrousd(usd),
      metadata: { source: "takyon_terminal" }
    });
    return print(account, json);
  }

  if (command === "budget" && cleanArgs[1] === "reserve") {
    const business = await resolveBusiness(cleanArgs[2], profile.id);
    const campaignArg = flag(cleanArgs, "--campaign");
    const campaign = campaignArg ? await requireBusinessCampaign({ businessId: business.id, campaignIdOrSlug: campaignArg }) : null;
    const usd = Number(flag(cleanArgs, "--usd", "0"));
    const purpose = flag(cleanArgs, "--purpose", cleanArgs.slice(3).join(" "));
    return print(
      await reserveBusinessBudget({
        businessId: business.id,
        campaignId: campaign?.id ?? null,
        profileId: profile.id,
        amountMicrousd: usdToMicrousd(usd),
        purpose,
        metadata: { source: "takyon_terminal" }
      }),
      json
    );
  }

  if (command === "memory" && cleanArgs[1] === "list") {
    const business = await resolveBusiness(cleanArgs[2], profile.id);
    return print(await listBusinessMemory({ businessId: business.id, namespace: flag(cleanArgs, "--namespace") || undefined }), json);
  }

  if (command === "memory" && cleanArgs[1] === "record") {
    const business = await resolveBusiness(cleanArgs[2], profile.id);
    const key = cleanArgs[3];
    const content = cleanArgs.slice(4).filter((part) => !part.startsWith("--")).join(" ");
    return print(
      await upsertBusinessMemory({
        businessId: business.id,
        profileId: profile.id,
        namespace: flag(cleanArgs, "--namespace", "strategy"),
        memoryKey: key,
        title: key,
        content,
        metadata: { source: "takyon_terminal" }
      }),
      json
    );
  }

  if (command === "wake") {
    const business = await resolveBusiness(cleanArgs[1], profile.id);
    const instruction = cleanArgs.slice(2).join(" ").trim();
    return printAutopilotPlan(await runBusinessAutopilot({ businessId: business.id, profileId: profile.id, instruction }), json);
  }

  if (command === "goal" || command === "/goal") {
    const business = await resolveBusiness(cleanArgs[1], profile.id);
    const goalText = cleanArgs.slice(2).join(" ").trim() || "get_first_customer";
    const result = await startTakyonGoal({
      companyId: business.id,
      profileId: profile.id,
      goalText,
      operatorInstruction: `/goal ${goalText}`,
      source: "takyon_terminal"
    });
    if (json || !result.supported) return print(result, json);
    console.log(`${tag("goal", theme.primary)} ${bold("started")} ${goalText} for ${business.name}`);
    console.log(`${color("Campaign:", theme.secondary)} ${shortId(result.campaign.id)}`);
    console.log(`${color("Goal tick:", ansi.cyan)} ${result.tick.status} ${dim(result.tick.jobId)}`);
    return;
  }

  if (command === "watch") {
    const business = await resolveBusiness(cleanArgs[1], profile.id);
    const intervalMs = Math.max(500, Number(flag(cleanArgs, "--interval-ms", "3000")));
    const limit = Math.max(1, Number(flag(cleanArgs, "--limit", "30")));
    const once = json || hasFlag(cleanArgs, "--once");
    if (!once) console.log(`Watching ${business.name}. Press Ctrl-C to stop.`);
    return watchBusinessWork({ businessId: business.id, json, once, intervalMs, limit });
  }

  if (command === "run") {
    const business = await resolveBusiness(cleanArgs[1], profile.id);
    const noWorker = hasFlag(cleanArgs, "--no-worker") || hasFlag(cleanArgs, "--plan-only");
    const once = hasFlag(cleanArgs, "--once");
    const instruction = cleanArgs
      .slice(2)
      .filter((part) => part !== "--plan-only" && part !== "--no-worker" && part !== "--once")
      .join(" ")
      .trim();
    const plan = await runBusinessAutopilot({ businessId: business.id, profileId: profile.id, instruction });
    printAutopilotPlan(plan, json);
    if (json || noWorker) return;
    console.log("");
    console.log(once ? "Running one worker pass." : "Starting scoped worker. Press Ctrl-C to stop.");
    return runWorkerForBusiness({ businessId: business.id, once });
  }

  if (command === "gc") {
    const maybeBusiness = cleanArgs[1] && cleanArgs[1] !== "confirm" && !cleanArgs[1].startsWith("--") ? cleanArgs[1] : "";
    const business = maybeBusiness ? await resolveBusiness(maybeBusiness, profile.id) : null;
    const olderThanDays = Math.max(1, Number(flag(cleanArgs, "--older-than-days", "30")));
    const maxDelete = Math.max(1, Number(flag(cleanArgs, "--max-delete", "1000")));
    const confirm = cleanArgs.includes("confirm") || hasFlag(cleanArgs, "--confirm");
    return print(await runTakyonGc({ businessId: business?.id ?? null, olderThanDays, maxDelete, confirm }), json);
  }

  if (command === "enqueue") {
    const business = await resolveBusiness(cleanArgs[1], profile.id);
    const workflowId = cleanArgs[2];
    const spec = getTakyonWorkflowSpec(workflowId);
    const lane = laneByWorkflow[workflowId];
    if (!spec || !lane) throw new Error(`Unknown workflow id: ${workflowId}`);
    await assertWorkflowAvailable(workflowId, business.id, profile.id);
    const campaignArg = flag(cleanArgs, "--campaign");
    const campaign = campaignArg ? await requireBusinessCampaign({ businessId: business.id, campaignIdOrSlug: campaignArg }) : null;
    return print(
      await enqueueWorkflowJob({
        companyId: business.id,
        profileId: profile.id,
        workflowId,
        lane,
        priority: spec.priority,
        dependencies: spec.dependencies,
        maxAttempts: spec.maxAttempts,
        payload: { source: "takyon_terminal", campaign_id: campaign?.id ?? null }
      }),
      json
    );
  }

  if (["pause", "resume", "kill"].includes(command)) {
    const state = command === "resume" ? "active" : command === "pause" ? "paused" : "killed";
    const scopeKind = cleanArgs[1];
    if (scopeKind === "global") {
      const control = await setTakyonControl({
        scopeType: "global",
        state,
        actorProfileId: profile.id,
        reason: cleanArgs.slice(2).join(" ")
      });
      const sql = db();
      const cancelled = state === "killed"
        ? {
            workflowJobs: (await sql<{ id: string }[]>`
              UPDATE workflow_jobs
              SET status = 'cancelled',
                  error = COALESCE(NULLIF(error, ''), 'Killed by global Takyon kill switch.'),
                  locked_by = NULL,
                  locked_at = NULL,
                  completed_at = COALESCE(completed_at, now()),
                  updated_at = now()
              WHERE status IN ('queued', 'running')
              RETURNING id
            `).length,
            agentRuns: (await sql<{ id: string }[]>`
              UPDATE agent_runs
              SET status = 'cancelled',
                  error = COALESCE(NULLIF(error, ''), 'Killed by global Takyon kill switch.'),
                  completed_at = COALESCE(completed_at, now()),
                  updated_at = now()
              WHERE status IN ('queued', 'running')
              RETURNING id
            `).length
          }
        : null;
      return print({ control, cancelled }, json);
    }
    if (scopeKind === "business") {
      const business = await resolveBusiness(cleanArgs[2], profile.id);
      const control = await setTakyonControl({
        scopeType: "business",
        businessId: business.id,
        state,
        actorProfileId: profile.id,
        reason: cleanArgs.slice(3).join(" ")
      });
      const sql = db();
      const cancelled = state === "killed"
        ? {
            workflowJobs: (await sql<{ id: string }[]>`
              UPDATE workflow_jobs
              SET status = 'cancelled',
                  error = COALESCE(NULLIF(error, ''), 'Killed by business Takyon kill switch.'),
                  locked_by = NULL,
                  locked_at = NULL,
                  completed_at = COALESCE(completed_at, now()),
                  updated_at = now()
              WHERE business_id = ${business.id}
                AND status IN ('queued', 'running')
              RETURNING id
            `).length,
            agentRuns: (await sql<{ id: string }[]>`
              UPDATE agent_runs
              SET status = 'cancelled',
                  error = COALESCE(NULLIF(error, ''), 'Killed by business Takyon kill switch.'),
                  completed_at = COALESCE(completed_at, now()),
                  updated_at = now()
              WHERE business_id = ${business.id}
                AND status IN ('queued', 'running')
              RETURNING id
            `).length
          }
        : null;
      return print({ control, cancelled }, json);
    }
    if (scopeKind === "campaign") {
      const business = await resolveBusiness(cleanArgs[2], profile.id);
      const campaign = await requireBusinessCampaign({ businessId: business.id, campaignIdOrSlug: cleanArgs[3] });
      if (state === "active") {
        return print(await setBusinessCampaignStatus({ businessId: business.id, campaignIdOrSlug: campaign.id, status: "active", profileId: profile.id }), json);
      }
      const campaignControl = await setBusinessCampaignStatus({ businessId: business.id, campaignIdOrSlug: campaign.id, status: state === "paused" ? "paused" : "killed", profileId: profile.id, reason: cleanArgs.slice(4).join(" ") });
      const sql = db();
      const cancelled = state === "killed"
        ? (await sql<{ id: string }[]>`
            UPDATE workflow_jobs
            SET status = 'cancelled',
                error = COALESCE(NULLIF(error, ''), 'Killed by campaign Takyon kill switch.'),
                locked_by = NULL,
                locked_at = NULL,
                completed_at = COALESCE(completed_at, now()),
                updated_at = now()
            WHERE business_id = ${business.id}
              AND payload->>'campaign_id' = ${campaign.id}
              AND status IN ('queued', 'running')
            RETURNING id
          `).length
        : 0;
      return print({ campaign: campaignControl, cancelledWorkflowJobs: cancelled }, json);
    }
    if (scopeKind === "provider") {
      const provider = cleanArgs[2];
      return print(
        await setTakyonControl({
          scopeType: "provider",
          provider,
          state,
          actorProfileId: profile.id,
          reason: cleanArgs.slice(3).join(" ")
        }),
        json
      );
    }
    if (scopeKind === "agent" || scopeKind === "agent_run") {
      const agentRunId = cleanArgs[2];
      if (!agentRunId) throw new Error(`Use: ./takyon ${command} agent <agent-run-id> [reason]`);
      const sql = db();
      const rows = await sql<{ business_id: string | null }[]>`
        SELECT business_id
        FROM agent_runs
        WHERE id = ${agentRunId}
        LIMIT 1
      `;
      if (!rows[0]) throw new Error(`Agent run not found: ${agentRunId}`);
      const control = await setTakyonControl({
          scopeType: "agent_run",
          agentRunId,
          businessId: rows[0]?.business_id ?? null,
          state,
          actorProfileId: profile.id,
          reason: cleanArgs.slice(3).join(" ")
      });
      const cancelled = state === "killed"
        ? await sql<{ id: string }[]>`
            UPDATE agent_runs
            SET status = 'cancelled',
                error = COALESCE(NULLIF(error, ''), 'Killed from Takyon terminal.'),
                completed_at = COALESCE(completed_at, now()),
                updated_at = now()
            WHERE id = ${agentRunId}
              AND status IN ('queued', 'running')
            RETURNING id
          `
        : [];
      return print({ control, cancelledAgentRun: cancelled.length === 1 }, json);
    }
    if (scopeKind === "job" || scopeKind === "workflow_job") {
      const jobId = cleanArgs[2];
      if (!jobId) throw new Error(`Use: ./takyon ${command} job <workflow-job-id> [reason]`);
      const sql = db();
      const rows = await sql<{ business_id: string }[]>`
        SELECT business_id
        FROM workflow_jobs
        WHERE id = ${jobId}
        LIMIT 1
      `;
      if (!rows[0]) throw new Error(`Workflow job not found: ${jobId}`);
      const control = await setTakyonControl({
        scopeType: "workflow_job",
        workflowJobId: jobId,
        businessId: rows[0].business_id,
        state,
        actorProfileId: profile.id,
        reason: cleanArgs.slice(3).join(" ")
      });
      const cancelled = state === "killed"
        ? await sql<{ id: string }[]>`
            UPDATE workflow_jobs
            SET status = 'cancelled',
                error = COALESCE(NULLIF(error, ''), 'Killed from Takyon terminal.'),
                locked_by = NULL,
                locked_at = NULL,
                completed_at = COALESCE(completed_at, now()),
                updated_at = now()
            WHERE id = ${jobId}
              AND status IN ('queued', 'running')
            RETURNING id
          `
        : [];
      return print({ control, cancelledJob: cancelled.length === 1 }, json);
    }
  }

  throw new Error(`Unknown Takyon command.\n\n${usage()}`);
}

function commandWithContext(tokens: string[], currentBusiness: string | null) {
  const command = tokens[0]?.toLowerCase() || "";
  if (!command) return tokens;
  if (command === "status") {
    if (!currentBusiness) throw new Error("Select a business first with: use <business>");
    return ["business", currentBusiness];
  }
  if (command === "caps") {
    return currentBusiness ? ["capabilities", currentBusiness] : ["capabilities"];
  }
  if (command === "jobs") {
    if (!currentBusiness && !tokens[1]) throw new Error("Select a business first with: /use <business>");
    if (currentBusiness && (!tokens[1] || tokens[1].startsWith("--"))) return ["jobs", currentBusiness, ...tokens.slice(1)];
  }
  if (command === "workspace") {
    if (!currentBusiness && !tokens[1]) throw new Error("Select a business first with: /use <business>");
    if (currentBusiness && (!tokens[1] || tokens[1].startsWith("--"))) return ["workspace", currentBusiness, ...tokens.slice(1)];
  }
  if (command === "files") {
    if (!currentBusiness && !tokens[1]) throw new Error("Select a business first with: /use <business>");
    if (currentBusiness && tokens[1] !== currentBusiness) return ["files", currentBusiness, ...tokens.slice(1)];
  }
  if (command === "read") {
    if (!currentBusiness && !tokens[1]) throw new Error("Select a business first with: /use <business>");
    if (currentBusiness && tokens[1] !== currentBusiness) return ["read", currentBusiness, ...tokens.slice(1)];
  }
  if (command === "write") {
    if (!currentBusiness && !tokens[1]) throw new Error("Select a business first with: /use <business>");
    if (currentBusiness && tokens[1] !== currentBusiness) return ["write", currentBusiness, ...tokens.slice(1)];
  }
  if (command === "capabilities" && tokens.length === 1 && currentBusiness) return ["capabilities", currentBusiness];
  if (command === "campaigns" && tokens.length === 1 && currentBusiness) return ["campaigns", currentBusiness];
  if (command === "watch") {
    if (!currentBusiness && !tokens[1]) throw new Error("Select a business first with: use <business>");
    if (currentBusiness && (!tokens[1] || tokens[1].startsWith("--"))) return ["watch", currentBusiness, ...tokens.slice(1)];
  }
  if (command === "run") {
    if (!currentBusiness && !tokens[1]) throw new Error("Select a business first with: use <business>");
    if (currentBusiness && tokens[1] !== currentBusiness) return ["run", currentBusiness, ...tokens.slice(1)];
  }
  if (command === "gc" && currentBusiness && (!tokens[1] || tokens[1] === "confirm" || tokens[1].startsWith("--"))) {
    return ["gc", currentBusiness, ...tokens.slice(1)];
  }
  if (command === "wake") {
    if (!currentBusiness && !tokens[1]) throw new Error("Select a business first with: use <business>");
    if (currentBusiness && tokens[1] !== currentBusiness) return ["wake", currentBusiness, ...tokens.slice(1)];
  }
  if (command === "goal" || command === "/goal") {
    if (!currentBusiness && !tokens[1]) throw new Error("Select a business first with: use <business>");
    if (currentBusiness && (!tokens[1] || tokens[1] !== currentBusiness)) {
      return ["goal", currentBusiness, ...tokens.slice(1)];
    }
  }
  if (command === "setup" && currentBusiness && tokens.length === 2) {
    return ["setup", tokens[1], currentBusiness];
  }
  if (command === "connect" && tokens[1] === "x" && currentBusiness && (!tokens[2] || tokens[2].startsWith("--"))) {
    return ["connect", "x", currentBusiness, ...tokens.slice(2)];
  }
  if (command === "enqueue" && currentBusiness && laneByWorkflow[tokens[1]]) {
    return ["enqueue", currentBusiness, ...tokens.slice(1)];
  }
  if (command === "campaign" && tokens[1] === "create" && currentBusiness && tokens[2] !== currentBusiness) {
    return ["campaign", "create", currentBusiness, ...tokens.slice(2)];
  }
  if (command === "budget" && ["set", "reserve"].includes(tokens[1] || "") && currentBusiness && tokens[2] !== currentBusiness) {
    return ["budget", tokens[1], currentBusiness, ...tokens.slice(2)];
  }
  if (command === "memory" && ["list", "record"].includes(tokens[1] || "") && currentBusiness && tokens[2] !== currentBusiness) {
    return ["memory", tokens[1], currentBusiness, ...tokens.slice(2)];
  }
  if (["pause", "resume", "kill"].includes(command)) {
    if (tokens[1] === "campaign" && currentBusiness && tokens[2] !== currentBusiness) {
      return [command, "campaign", currentBusiness, ...tokens.slice(2)];
    }
    if (!tokens[1] && currentBusiness) return [command, "business", currentBusiness];
  }
  return tokens;
}

async function interactive(profile: TerminalProfile, initialBusiness?: string | null) {
  const [harnessSettings, initialHarnessCommands] = await Promise.all([
    readTakyonHarnessSettings(),
    listTakyonHarnessCommands()
  ]);
  const mascot = await readTakyonMascot();
  let slashEntries = slashEntriesFromHarness(initialHarnessCommands);
  let slashEntriesLoadedAt = Date.now();
  let currentBusiness: string | null = null;
  const rl = createLiveInterface({
    input,
    output,
    completer: (line: string) => {
      if (!line.startsWith("/")) return [[], line];
      const visible = visibleSlashEntries(slashEntries, currentBusiness).map((entry) => `/${entry.name}`);
      const completions = slashMatches(slashEntries, line, currentBusiness).map((entry) => `/${entry.name}`);
      return [completions.length ? completions : visible, line];
    }
  });
  let currentSession: LiveSession | null = null;
  let recentBusinessSlug: string | null = null;
  let recentTurns: TakyonTerminalRecentTurn[] = [];
  let commandChain = Promise.resolve();
  let closing = false;
  let uiMode: "compact" | "full" = harnessSettings.ui.defaultMode;
  let lastSlashPaletteKey = "";
  let lastSlashLine = "";
  let slashPaletteOffset = 0;
  let slashPaletteTimer: NodeJS.Timeout | null = null;
  let slashPopupVisible = false;
  let promptVisible = false;
  let activeOperation: { label: string; controller: AbortController } | null = null;

  const refreshSlashEntries = async (force = false) => {
    if (!force && Date.now() - slashEntriesLoadedAt < 2_000) return;
    slashEntries = slashEntriesFromHarness(await listTakyonHarnessCommands());
    slashEntriesLoadedAt = Date.now();
  };

  const clearSlashPopup = () => {
    if (!output.isTTY || !slashPopupVisible) return;
    output.write("\x1b[s");
    output.write("\x1b[E");
    output.write("\x1b[J");
    output.write("\x1b[u");
    slashPopupVisible = false;
  };

  const clearActivePrompt = () => {
    if (!output.isTTY || !promptVisible) return;
    output.write("\x1b[1A");
    cursorTo(output, 0);
    output.write("\x1b[J");
    promptVisible = false;
  };

  const previousBeforeLiveWrite = beforeLiveWrite;
  const previousAfterLiveWritePrompt = afterLiveWritePrompt;
  beforeLiveWrite = () => {
    clearSlashPopup();
    clearActivePrompt();
  };
  afterLiveWritePrompt = () => {
    promptVisible = true;
  };

  const runCancelable = async <T>(label: string, fn: (signal: AbortSignal) => Promise<T>) => {
    const controller = new AbortController();
    activeOperation = { label, controller };
    try {
      const result = await fn(controller.signal);
      if (controller.signal.aborted) throw new ShellOperationCancelled(label);
      return result;
    } catch (error) {
      if (controller.signal.aborted || isAbortLike(error)) throw new ShellOperationCancelled(label);
      throw error;
    } finally {
      if (activeOperation?.controller === controller) activeOperation = null;
    }
  };

  const prompt = () => {
    if (closing) return;
    clearSlashPopup();
    rl.setPrompt(inputPrompt(currentBusiness));
    rl.prompt();
    promptVisible = true;
  };

  const renderCurrentSlashPalette = async () => {
    await refreshSlashEntries();
    const line = String((rl as LiveReadline & { line?: string }).line ?? "");
    if (!shouldShowSlashPalette(line)) {
      lastSlashPaletteKey = "";
      lastSlashLine = "";
      slashPaletteOffset = 0;
      clearSlashPopup();
      return;
    }
    if (!output.isTTY) return;
    if (line !== lastSlashLine) {
      lastSlashLine = line;
      slashPaletteOffset = 0;
    }
    const maxOffset = Math.max(0, slashMatches(slashEntries, line, currentBusiness).length - slashPalettePageSize());
    slashPaletteOffset = Math.max(0, Math.min(slashPaletteOffset, maxOffset));
    const key = `${currentBusiness ?? ""}:${line}:${slashPaletteOffset}`;
    if (key === lastSlashPaletteKey) return;
    lastSlashPaletteKey = key;
    output.write("\x1b[s");
    output.write("\x1b[E");
    output.write("\x1b[J");
    output.write(`${renderSlashPalette(slashEntries, line, currentBusiness, slashPaletteOffset)}\n`);
    output.write("\x1b[u");
    slashPopupVisible = true;
  };

  const scheduleSlashPalette = () => {
    if (slashPaletteTimer) clearTimeout(slashPaletteTimer);
    slashPaletteTimer = setTimeout(() => void renderCurrentSlashPalette().catch((error) => {
      liveWrite(rl, `[slash] ${error instanceof Error ? error.message : String(error)}`);
    }), 35);
  };

  const scrollSlashPalette = (delta: number) => {
    const line = String((rl as LiveReadline & { line?: string }).line ?? "");
    if (!shouldShowSlashPalette(line)) return false;
    const maxOffset = Math.max(0, slashMatches(slashEntries, line, currentBusiness).length - slashPalettePageSize());
    const nextOffset = Math.max(0, Math.min(maxOffset, slashPaletteOffset + delta));
    if (nextOffset === slashPaletteOffset) return true;
    slashPaletteOffset = nextOffset;
    lastSlashPaletteKey = "";
    void renderCurrentSlashPalette().catch((error) => {
      liveWrite(rl, `[slash] ${error instanceof Error ? error.message : String(error)}`);
    });
    return true;
  };

  const onKeypress = (_chunk: string, key: { name?: string; meta?: boolean; shift?: boolean; ctrl?: boolean } = {}) => {
    if (closing) return;
    if (key.name === "escape" && activeOperation && !activeOperation.controller.signal.aborted) {
      activeOperation.controller.abort();
      liveWrite(rl, `${tag("cancel", theme.warning)} stopping ${activeOperation.label}`);
      return;
    }
    const jump = key.meta || key.shift || key.ctrl ? slashPalettePageSize() : 1;
    if ((key.name === "down" || key.name === "pagedown") && scrollSlashPalette(jump)) return;
    if ((key.name === "up" || key.name === "pageup") && scrollSlashPalette(-jump)) return;
    if (key.name === "return" || key.name === "enter") {
      lastSlashPaletteKey = "";
      clearSlashPopup();
      return;
    }
    if (key.name === "escape") {
      lastSlashPaletteKey = "";
      clearSlashPopup();
      return;
    }
    scheduleSlashPalette();
  };
  if (input.isTTY) {
    emitKeypressEvents(input, rl);
    input.on("keypress", onKeypress);
  }

  const attachBusiness = async (idOrSlug: string, wakeIfIdle = true) => {
    const business = await resolveBusiness(idOrSlug, profile.id);
    if (currentSession && currentSession.businessId !== business.id) {
      await stopLiveSession(currentSession, rl, "detached");
      currentSession = null;
    }
    currentBusiness = business.slug;
    recentBusinessSlug = business.slug;
    if (!currentSession) {
      currentSession = {
        businessId: business.id,
        profileId: profile.id,
        slug: business.slug,
        name: business.name,
        worker: null,
        monitor: null,
        lastJobs: new Map(),
        logsEnabled: false,
        closed: false
      };
      startLiveWorker(currentSession, rl);
      currentSession.monitor = setInterval(() => {
        if (!currentSession) return;
        void pollLiveSession(currentSession, rl).catch((error) => {
          liveWrite(rl, `[live] monitor error: ${error instanceof Error ? error.message : String(error)}`);
        });
      }, harnessSettings.ui.livePollMs);
      await sessionPanel(currentSession, rl, uiMode);
      await pollLiveSession(currentSession, rl, true);
    }

    if (wakeIfIdle) {
      const jobs = await listWorkflowJobs(business.id, 20);
      const hasActiveWork = jobs.some((job) => job.status === "queued" || job.status === "running");
      if (!hasActiveWork) {
        const spinner = startSpinner(rl, "ceo", "checking idle business");
        const plan = await runCancelable("CEO idle check", (signal) => runBusinessAutopilot({
          businessId: business.id,
          profileId: profile.id,
          instruction: "Live terminal attached. Continue autonomously from current business state.",
          signal
        })).finally(() => spinner.stop());
        liveWrite(rl, concisePlan(plan));
      }
    }
  };

  const steerCurrentBusiness = async (instruction: string) => {
    if (!currentSession || !currentBusiness) {
      liveWrite(rl, "Select a business first with `use <business>`.");
      return;
    }
    const session = currentSession;
    const spinner = startSpinner(rl, "ceo", "planning next move");
    const plan = await runCancelable("CEO planning", (signal) => runBusinessAutopilot({
      businessId: session.businessId,
      profileId: profile.id,
      instruction,
      signal
    })).finally(() => spinner.stop());
    liveWrite(rl, concisePlan(plan));
    startLiveWorker(session, rl);
  };

  const rememberTurn = (turn: TakyonTerminalRecentTurn) => {
    recentTurns = [...recentTurns, turn].slice(-12);
  };

  const chatWithTakyon = async (instruction: string) => {
    const spinner = startSpinner(rl, "agent", "thinking");
    const turn = await runCancelable("agent thinking", (signal) => runTakyonTerminalAgent({
      profileId: profile.id,
      text: instruction,
      currentBusinessSlug: currentSession?.slug ?? currentBusiness,
      recentBusinessSlug,
      recentTurns,
      terminalHelp: usage(),
      signal
    })).finally(() => spinner.stop());
    if (turn.businessSlug) recentBusinessSlug = turn.businessSlug;
    liveWrite(rl, `${tag("agent", theme.primary)} ${turn.reply}`);
    rememberTurn({ role: "operator", text: instruction });
    rememberTurn({ role: "takyon", text: turn.reply });

    if (turn.action === "chat") return;

    if (turn.action === "create_business") {
      const name = turn.businessName;
      const pitch = turn.businessPitch ?? turn.operatorInstruction ?? instruction;
      if (!name || !pitch || pitch.length < 8) {
        liveWrite(rl, `${tag("agent", ansi.yellow)} I need a business name and a concrete pitch to create it.`);
        return;
      }
      const created = await createCompany({ name, pitch }, profile);
      const budgetUsd = turn.budgetUsd ?? 0;
      if (budgetUsd > 0) {
        await ensureBudgetAccount({
          businessId: created.company.id,
          hardLimitMicrousd: usdToMicrousd(budgetUsd),
          metadata: { source: "takyon_terminal_agent" }
        });
      }
      liveWrite(rl, `${tag("business", ansi.cyan)} created ${bold(created.company.name)} ${dim(`(${created.company.slug})`)}`);
      await attachBusiness(created.company.slug, false);
      const ceoSpinner = startSpinner(rl, "ceo", "starting business loop");
      const plan = await runCancelable("CEO startup", (signal) => runBusinessAutopilot({
        businessId: created.company.id,
        profileId: profile.id,
        instruction: turn.operatorInstruction ?? `Build this business end to end. ${pitch}`,
        campaignBudgetUsd: budgetUsd > 0 ? budgetUsd : null,
        signal
      })).finally(() => ceoSpinner.stop());
      liveWrite(rl, concisePlan(plan));
      if (currentSession) startLiveWorker(currentSession, rl);
      return;
    }

    const targetSlug = turn.businessSlug ?? currentSession?.slug ?? currentBusiness ?? recentBusinessSlug;
    if (!targetSlug) {
      liveWrite(rl, `${tag("agent", ansi.yellow)} I need a business slug first. Use ${bold("/businesses")} or ${bold("/use <business>")}.`);
      return;
    }

    if (!currentSession || currentSession.slug !== targetSlug) {
      await attachBusiness(targetSlug, false);
    }
    recentBusinessSlug = targetSlug;

    if (turn.action === "attach") return;
    if (!currentSession) {
      liveWrite(rl, `${tag("agent", ansi.yellow)} Could not attach to ${targetSlug}.`);
      return;
    }

    const session = currentSession;
    const ceoSpinner = startSpinner(rl, "ceo", "planning next move");
    const plan = await runCancelable("CEO planning", (signal) => runBusinessAutopilot({
      businessId: session.businessId,
      profileId: profile.id,
      instruction: turn.operatorInstruction ?? instruction,
      signal
    })).finally(() => ceoSpinner.stop());
    liveWrite(rl, concisePlan(plan));
    startLiveWorker(session, rl);
  };

  const handleLine = async (rawLine: string) => {
    const line = rawLine.trim();
    if (!line) return;
    if (["exit", "quit", ":q", "/exit", "/quit", "/:q"].includes(line.toLowerCase())) {
      closing = true;
      rl.close();
      return;
    }
    if (!line.startsWith("/")) {
      await chatWithTakyon(line);
      return;
    }

    const slashLine = line.slice(1).trim();
    if (!slashLine) {
      return;
    }
    const tokens = parseInput(slashLine);
    const command = tokens[0]?.toLowerCase() || "";

    if (command === "use") {
      if (!tokens[1]) throw new Error("Use: use <business-id-or-slug>");
      await attachBusiness(tokens[1]);
      return;
    }

    if (command === "auto") {
      if (!currentSession) throw new Error("Select a business first with: use <business>");
      const mode = tokens[1] || "on";
      if (mode === "off" || mode === "stop") {
        await stopLiveSession(currentSession, rl, "auto off");
        currentSession = null;
        currentBusiness = null;
        return;
      }
      if (mode !== "on" && mode !== "start") throw new Error("Use: auto on|off");
      startLiveWorker(currentSession, rl);
      if (!currentSession.monitor) {
        currentSession.monitor = setInterval(() => {
          if (!currentSession) return;
          void pollLiveSession(currentSession, rl).catch((error) => {
            liveWrite(rl, `[live] monitor error: ${error instanceof Error ? error.message : String(error)}`);
          });
        }, harnessSettings.ui.livePollMs);
      }
      liveWrite(rl, "[live] auto on");
      return;
    }

    if (command === "stop") {
      await stopLiveSession(currentSession, rl, "stopped");
      currentSession = null;
      currentBusiness = null;
      return;
    }

    if (command === "logs") {
      if (!currentSession) throw new Error("Select a business first with: use <business>");
      const mode = tokens[1] || "on";
      currentSession.logsEnabled = mode !== "off";
      liveWrite(rl, `[live] worker logs ${currentSession.logsEnabled ? "on" : "off"}`);
      return;
    }

    if (command === "ui") {
      const mode = tokens[1] || (uiMode === "full" ? "compact" : "full");
      if (mode !== "compact" && mode !== "full") throw new Error("Use: /ui compact|full");
      uiMode = mode;
      liveWrite(rl, `${tag("ui", ansi.brightBlue)} mode ${uiMode}`);
      if (currentSession) await sessionPanel(currentSession, rl, uiMode);
      return;
    }

    if (command === "commands" || command === "skills") {
      await refreshSlashEntries(true);
      liveWrite(rl, renderSlashPalette(slashEntries, "/", currentBusiness));
      return;
    }

    if (command === "run" || command === "wake") {
      const normalized = commandWithContext(tokens, currentBusiness);
      const business = await resolveBusiness(normalized[1], profile.id);
      if (!currentSession || currentSession.businessId !== business.id) await attachBusiness(business.slug, false);
      await steerCurrentBusiness(normalized.slice(2).join(" ").trim() || "Continue autonomously from current business state.");
      return;
    }

    const harnessCommand = await getTakyonHarnessCommand(command);
    if (harnessCommand) {
      if (harnessCommand.requiresBusiness && !currentSession) throw new Error("Select a business first with: /use <business>");
      const workspace = currentSession
        ? await syncBusinessWorkspace({ businessId: currentSession.businessId, profileId: profile.id, reason: `harness_${harnessCommand.name}` })
        : null;
      const instruction = renderTakyonHarnessCommand({
        command: harnessCommand,
        args: tokens.slice(1),
        businessSlug: currentSession?.slug ?? currentBusiness,
        workspaceRoot: workspace?.root ?? null
      });
      if (!currentSession) {
        await chatWithTakyon(instruction);
        return;
      }
      const session = currentSession;
      const spinner = startSpinner(rl, "ceo", `running /${harnessCommand.name}`);
      const plan = await runCancelable(`/${harnessCommand.name}`, (signal) => runBusinessAutopilot({
        businessId: session.businessId,
        profileId: profile.id,
        instruction,
        signal
      })).finally(() => spinner.stop());
      liveWrite(rl, concisePlan(plan));
      startLiveWorker(session, rl);
      return;
    }

    await runCommand(commandWithContext(tokens, currentBusiness), profile, false);
  };

  console.log(startupGraphic({ commandCount: slashEntries.length, uiMode, mascot }));
  rl.on("line", (line) => {
    clearSlashPopup();
    closeInputBox();
    promptVisible = false;
    commandChain = commandChain
      .then(async () => {
        suppressLiveWritePrompt = true;
        try {
          await handleLine(line);
        } finally {
          suppressLiveWritePrompt = false;
        }
      })
      .catch((error) => {
        suppressLiveWritePrompt = true;
        if (error instanceof ShellOperationCancelled) {
          liveWrite(rl, `${tag("cancel", theme.warning)} ${error.message}`);
        } else {
          liveWrite(rl, error instanceof Error ? error.message : String(error));
        }
        suppressLiveWritePrompt = false;
      })
      .finally(prompt);
  });

  await new Promise<void>((resolve) => {
    rl.on("close", () => {
      closing = true;
      if (activeOperation && !activeOperation.controller.signal.aborted) activeOperation.controller.abort();
      if (slashPaletteTimer) clearTimeout(slashPaletteTimer);
      clearSlashPopup();
      beforeLiveWrite = previousBeforeLiveWrite;
      afterLiveWritePrompt = previousAfterLiveWritePrompt;
      if (input.isTTY) input.off("keypress", onKeypress);
      commandChain = commandChain.finally(async () => {
        await stopLiveSession(currentSession, rl, "session closed");
        resolve();
      });
    });
    if (initialBusiness) {
      commandChain = commandChain
        .then(() => attachBusiness(initialBusiness))
        .catch((error) => liveWrite(rl, error instanceof Error ? error.message : String(error)))
        .finally(prompt);
    } else {
      prompt();
    }
  });
}

async function main() {
  const args = process.argv.slice(2);
  const json = hasFlag(args, "--json");
  const cleanArgs = args.filter((arg) => arg !== "--json");
  const profile = await terminalProfile();

  if (process.stdin.isTTY && cleanArgs.length === 1 && !topLevelCommands.has(cleanArgs[0].toLowerCase())) {
    await interactive(profile, cleanArgs[0]);
    return;
  }

  if ((cleanArgs.length === 0 || cleanArgs[0] === "shell" || cleanArgs[0] === "interactive") && process.stdin.isTTY) {
    await interactive(profile, cleanArgs[1] ?? null);
    return;
  }

  await runCommand(cleanArgs, profile, json);
}

main()
  .catch((error) => {
    console.error(error instanceof Error ? error.message : error);
    process.exitCode = 1;
  })
  .finally(async () => {
    await closeDbConnections();
  });
