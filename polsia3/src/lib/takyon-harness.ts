import fs from "node:fs/promises";
import path from "node:path";

export type TakyonHarnessCommand = {
  name: string;
  path: string;
  description: string | null;
  requiresBusiness: boolean;
  priorityBand: string | null;
  allowedTools: string[];
  body: string;
};

export type TakyonHarnessSettings = {
  root: string;
  ui: {
    defaultMode: "compact" | "full";
    livePollMs: number;
  };
  workspace: {
    generatedReadonlyRoots: string[];
    agentAuthoredRoots: string[];
  };
};

const defaultSettings: Omit<TakyonHarnessSettings, "root"> = {
  ui: {
    defaultMode: "full",
    livePollMs: 2500
  },
  workspace: {
    generatedReadonlyRoots: ["state", "jobs", "ledger", "tools", "receipts", "website"],
    agentAuthoredRoots: ["ceo", "goals", "product", "outreach", "campaigns", "memory", "agents"]
  }
};

const secretPatterns = [
  { name: "private key", pattern: /-----BEGIN [A-Z ]*PRIVATE KEY-----/ },
  { name: "Anthropic API key", pattern: /ANTHROPIC_API_KEY\s*=\s*\S+|sk-ant-[A-Za-z0-9_-]{20,}/ },
  { name: "OpenAI API key", pattern: /OPENAI_API_KEY\s*=\s*\S+|sk-proj-[A-Za-z0-9_-]{20,}/ },
  { name: "Stripe secret key", pattern: /STRIPE_SECRET_KEY\s*=\s*\S+|sk_(live|test)_[A-Za-z0-9]{20,}/ },
  { name: "Vercel token", pattern: /VERCEL_TOKEN\s*=\s*\S+/ },
  { name: "X OAuth secret", pattern: /X_CLIENT_SECRET\s*=\s*\S+|x\.refresh_token\s*[:=]\s*\S+/i }
];

const warningPatterns = [
  { name: "shell exec", pattern: /\b(exec|execSync|spawnSync)\s*\(/ },
  { name: "eval", pattern: /\beval\s*\(|new Function\b/ },
  { name: "dangerous HTML", pattern: /dangerouslySetInnerHTML|(?:\.|\b)innerHTML\s*=/ },
  { name: "document.write", pattern: /document\.write\s*\(/ },
  { name: "pickle/os.system", pattern: /\bpickle\b|os\.system\s*\(/ }
];

function harnessRoot() {
  return path.resolve(process.env.TAKYON_HARNESS_ROOT || path.join(process.cwd(), "harness", "takyon"));
}

function stripQuotes(value: string) {
  const trimmed = value.trim();
  if ((trimmed.startsWith("\"") && trimmed.endsWith("\"")) || (trimmed.startsWith("'") && trimmed.endsWith("'"))) {
    return trimmed.slice(1, -1);
  }
  return trimmed;
}

function parseScalar(value: string): string | boolean | string[] {
  const trimmed = value.trim();
  if (trimmed === "true") return true;
  if (trimmed === "false") return false;
  if (trimmed.startsWith("[") && trimmed.endsWith("]")) {
    try {
      const parsed = JSON.parse(trimmed.replace(/'/g, "\"")) as unknown;
      if (Array.isArray(parsed)) return parsed.map((item) => String(item));
    } catch {
      return trimmed.slice(1, -1).split(",").map((item) => stripQuotes(item)).filter(Boolean);
    }
  }
  return stripQuotes(trimmed);
}

function parseFrontmatter(markdown: string) {
  if (!markdown.startsWith("---\n")) return { meta: {} as Record<string, string | boolean | string[]>, body: markdown };
  const end = markdown.indexOf("\n---", 4);
  if (end === -1) return { meta: {} as Record<string, string | boolean | string[]>, body: markdown };
  const meta: Record<string, string | boolean | string[]> = {};
  const raw = markdown.slice(4, end).trim();
  for (const line of raw.split(/\r?\n/)) {
    const index = line.indexOf(":");
    if (index === -1) continue;
    const key = line.slice(0, index).trim();
    if (!key) continue;
    meta[key] = parseScalar(line.slice(index + 1));
  }
  return { meta, body: markdown.slice(end + 4).trimStart() };
}

function asString(value: unknown) {
  return typeof value === "string" ? value : null;
}

function asStringArray(value: unknown) {
  if (Array.isArray(value)) return value.map((item) => String(item));
  if (typeof value === "string" && value.trim()) return [value.trim()];
  return [];
}

async function readJsonFile<T>(filePath: string): Promise<T | null> {
  const raw = await fs.readFile(filePath, "utf8").catch(() => null);
  if (!raw) return null;
  return JSON.parse(raw) as T;
}

export async function readTakyonHarnessSettings(): Promise<TakyonHarnessSettings> {
  const root = harnessRoot();
  const local = await readJsonFile<Partial<TakyonHarnessSettings>>(path.join(root, "settings.json")).catch(() => null);
  return {
    root,
    ui: {
      defaultMode: local?.ui?.defaultMode === "compact" ? "compact" : defaultSettings.ui.defaultMode,
      livePollMs: Math.max(500, Math.min(Number(local?.ui?.livePollMs ?? defaultSettings.ui.livePollMs), 30_000))
    },
    workspace: {
      generatedReadonlyRoots: local?.workspace?.generatedReadonlyRoots?.length
        ? local.workspace.generatedReadonlyRoots
        : defaultSettings.workspace.generatedReadonlyRoots,
      agentAuthoredRoots: local?.workspace?.agentAuthoredRoots?.length
        ? local.workspace.agentAuthoredRoots
        : defaultSettings.workspace.agentAuthoredRoots
    }
  };
}

export async function listTakyonHarnessCommands() {
  const root = harnessRoot();
  const commandRoot = path.join(root, "commands");
  const entries = await fs.readdir(commandRoot, { withFileTypes: true }).catch(() => []);
  const commands: TakyonHarnessCommand[] = [];
  for (const entry of entries) {
    if (!entry.isFile() || !entry.name.endsWith(".md")) continue;
    const filePath = path.join(commandRoot, entry.name);
    const raw = await fs.readFile(filePath, "utf8");
    const parsed = parseFrontmatter(raw);
    const name = asString(parsed.meta.name) || entry.name.replace(/\.md$/, "");
    commands.push({
      name,
      path: path.relative(process.cwd(), filePath),
      description: asString(parsed.meta.description),
      requiresBusiness: parsed.meta["requires-business"] !== false,
      priorityBand: asString(parsed.meta["priority-band"]),
      allowedTools: asStringArray(parsed.meta["allowed-tools"]),
      body: parsed.body
    });
  }
  return commands.sort((a, b) => a.name.localeCompare(b.name));
}

export async function getTakyonHarnessCommand(name: string) {
  const normalized = name.toLowerCase().replace(/^\/+/, "");
  const commands = await listTakyonHarnessCommands();
  return commands.find((command) => command.name.toLowerCase() === normalized) ?? null;
}

export function renderTakyonHarnessCommand(input: {
  command: TakyonHarnessCommand;
  args: string[];
  businessSlug?: string | null;
  workspaceRoot?: string | null;
}) {
  const argumentText = input.args.join(" ").trim();
  return [
    `Harness slash command: /${input.command.name}${argumentText ? ` ${argumentText}` : ""}`,
    input.command.description ? `Description: ${input.command.description}` : "",
    input.command.priorityBand ? `Priority band: ${input.command.priorityBand}` : "",
    input.command.allowedTools.length ? `Allowed tool categories: ${input.command.allowedTools.join(", ")}` : "",
    input.businessSlug ? `Business: ${input.businessSlug}` : "",
    input.workspaceRoot ? `Workspace: ${input.workspaceRoot}` : "",
    "",
    input.command.body
      .replace(/\$ARGUMENTS/g, argumentText)
      .replace(/\$BUSINESS/g, input.businessSlug ?? "")
      .replace(/\$WORKSPACE_ROOT/g, input.workspaceRoot ?? "")
  ].filter(Boolean).join("\n");
}

export function runWorkspaceWriteGuards(input: { relativePath: string; content: string }) {
  const blocked: string[] = [];
  const warnings: string[] = [];
  for (const secret of secretPatterns) {
    if (secret.pattern.test(input.content)) blocked.push(`Possible ${secret.name} in ${input.relativePath}. Put secrets in ./takyon secret set, not business workspace files.`);
  }
  for (const warning of warningPatterns) {
    if (warning.pattern.test(input.content)) warnings.push(`Harness warning: ${warning.name} pattern appears in ${input.relativePath}.`);
  }
  return { blocked, warnings };
}
