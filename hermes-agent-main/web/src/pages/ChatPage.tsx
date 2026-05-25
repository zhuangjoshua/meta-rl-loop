import {
  Activity,
  AlertCircle,
  ArrowUp,
  Briefcase,
  Building2,
  Check,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  Clock3,
  Code2,
  Command,
  DollarSign,
  ExternalLink,
  FileText,
  Folder,
  Gauge,
  Globe2,
  ListChecks,
  MessageCircle,
  PanelRight,
  Play,
  Plus,
  RefreshCw,
  Rocket,
  Search,
  Sparkles,
  Square,
  Users,
  Wallet,
  X,
} from "lucide-react";
import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type FormEvent,
  type KeyboardEvent,
  type ReactNode,
  type RefObject,
} from "react";
import { useSearchParams } from "react-router-dom";

import { Markdown } from "@/components/Markdown";
import { api } from "@/lib/api";
import {
  GatewayClient,
  type ConnectionState,
} from "@/lib/gatewayClient";
import { cn } from "@/lib/utils";
import { PluginSlot } from "@/plugins";

type ChatRole = "user" | "assistant" | "system";

interface ChatMessage {
  id: string;
  role: ChatRole;
  content: string;
  status?: "streaming" | "complete" | "error" | "interrupted";
}

interface SessionInfo {
  cwd?: string;
  model?: string;
  provider?: string;
  profile_name?: string;
  credential_warning?: string;
}

interface SessionCreateResponse {
  session_id: string;
  info?: SessionInfo;
}

interface SessionResumeResponse {
  session_id: string;
  resumed?: string;
  messages?: Array<{
    role?: "user" | "assistant" | "system" | "tool";
    text?: string;
    name?: string;
    context?: string;
  }>;
  info?: SessionInfo;
}

interface BusinessSummary {
  slug?: string;
  name?: string;
  goal?: string;
  mode?: string;
  status?: string;
  state?: string;
  reason?: string;
}

interface BusinessOverviewProduct {
  status?: string;
  source_path?: string;
  design_brief_path?: string;
  runtime_api_base?: string;
  publish_target?: string;
  publish_policy?: string;
  publish_status?: string;
  public_url?: string;
  published_at?: string;
  publish_receipt_path?: string;
  publish_blocker?: string;
  routes_count?: number;
  verification_status?: string;
  verification_receipt?: string;
  filesystem_index?: string;
  notes?: string;
}

interface BusinessOverviewMetrics {
  users?: number;
  paid_customers?: number;
  mrr_cents?: number;
  revenue_cents?: number;
  checkout_intents?: number;
  usage_events?: number;
  unresolved_inbound?: number;
  queued_jobs?: number;
}

interface BusinessOverviewBudget {
  business_amount?: number | null;
  business_status?: string;
  app_status?: string;
  app_limit_microusd?: number;
  app_spent_microusd?: number;
  app_remaining_microusd?: number;
}

interface BusinessOverviewCron {
  id?: string;
  name?: string;
  enabled?: boolean;
  state?: string;
  schedule?: string;
  next_run?: string;
  last_run?: string;
}

interface BusinessOverviewFile {
  path?: string;
  type?: string;
}

interface BusinessOverviewJob {
  id?: string;
  kind?: string;
  status?: string;
  updated_at?: string;
  created_at?: string;
  label?: string;
  detail?: string;
  tone?: string;
}

interface BusinessOverviewTask {
  id?: string;
  source?: string;
  label?: string;
  status?: string;
  detail?: string;
  tone?: string;
  updated_at?: string;
}

interface BusinessOverviewStatusCard {
  label?: string;
  status?: string;
  detail?: string;
  tone?: string;
}

interface BusinessOverviewCeoLoop {
  status?: string;
  headline?: string;
  detail?: string;
  next_action?: string;
}

interface BusinessOverviewResearch {
  status?: string;
  latest_path?: string;
  strategy_path?: string;
  icp_path?: string;
  channels_path?: string;
  count?: number;
}

interface BusinessOverviewWakeHealth {
  status?: string;
  headline?: string;
  detail?: string;
}

interface BusinessOverviewConversations {
  active_threads?: number;
  unresolved_messages?: number;
  latest_message_at?: string;
}

interface BusinessArtifactSummary {
  status?: string;
  path?: string;
  receipt?: string;
  updated_at?: number;
  deploy_status?: string;
  source_path?: string;
  public_url?: string;
  publish_target?: string;
  publish_status?: string;
  publish_blocker?: string;
  publish_receipt_path?: string;
  count?: number;
  published_count?: number;
}

interface BusinessOverviewPost {
  id?: string;
  title?: string;
  source?: string;
  status?: string;
  mode?: string;
  url?: string;
  artifact_path?: string;
  conversation_file?: string;
  created_at?: string;
  updated_at?: string;
  unresolved_messages?: number;
}

interface BusinessOverview {
  goal?: string;
  mode?: string;
  product?: BusinessOverviewProduct;
  metrics?: BusinessOverviewMetrics;
  budget?: BusinessOverviewBudget;
  cron?: BusinessOverviewCron[];
  files?: BusinessOverviewFile[];
  jobs?: BusinessOverviewJob[];
  agent_runs?: BusinessOverviewTask[];
  tasks?: BusinessOverviewTask[];
  status_cards?: BusinessOverviewStatusCard[];
  ceo_loop?: BusinessOverviewCeoLoop;
  research?: BusinessOverviewResearch;
  wake_health?: BusinessOverviewWakeHealth;
  posts?: BusinessOverviewPost[];
  artifacts?: {
    website?: BusinessArtifactSummary;
    outreach?: BusinessArtifactSummary;
    creative_assets?: BusinessArtifactSummary;
  };
  conversations?: BusinessOverviewConversations;
  generated_at?: string;
  pulse_warning?: string;
}

interface ScopeState {
  scope: "global" | string;
  business: string;
  current: BusinessSummary;
  businesses: BusinessSummary[];
  overview?: BusinessOverview;
  warning?: string;
  auto_switched_business?: string;
  auto_scope_warning?: string;
}

interface SlashCompletionItem {
  text: string;
  display?: string;
  meta?: string;
  description?: string;
  requires_business?: boolean;
  kind?: string;
}

interface SlashCompleteResponse {
  items?: SlashCompletionItem[];
  replace_from?: number;
}

interface TakyonShellResponse extends ScopeState {
  output?: string;
}

interface TakyonPromptContextResponse extends ScopeState {
  text?: string;
}

interface BusinessFilesResponse extends ScopeState {
  path?: string;
  files?: BusinessOverviewFile[];
}

interface BusinessOutputsResponse extends ScopeState {
  outputs?: Deliverable[];
}

interface BusinessMediaResponse extends ScopeState {
  path?: string;
  media_type?: string;
  size?: number;
  url?: string;
}

interface BusinessFileReadResponse extends ScopeState {
  path?: string;
  size?: number;
  content?: string;
  truncated?: boolean;
}

interface BusinessSitePreviewResponse extends ScopeState {
  path?: string;
  size?: number;
  url?: string;
}

interface ToolEntry {
  id: string;
  tool_id: string;
  name: string;
  context?: string;
  status: "running" | "done" | "error";
  preview?: string;
  summary?: string;
  error?: string;
  inline_diff?: string;
  startedAt: number;
  completedAt?: number;
}

interface Deliverable {
  id: string;
  title: string;
  detail: string;
  path?: string;
  kind: "file" | "diff" | "tool" | "deploy" | "receipt" | "report" | "image" | "video";
  at: number;
}

interface SourceDocTile {
  detail?: string;
  label: string;
  path: string;
  status?: string;
}

type PanelTab = "home" | "next" | "files" | "outputs" | "dev";

const STATE_LABEL: Record<ConnectionState, string> = {
  idle: "starting",
  connecting: "connecting",
  open: "ready",
  closed: "closed",
  error: "error",
};

const EMPTY_SCOPE_STATE: ScopeState = {
  scope: "global",
  business: "",
  current: {},
  businesses: [],
};

const CREATE_MODE_STORAGE_KEY = "takyon.chat.create_new_businesses_in_test_mode";
const PANEL_TABS: Array<{ id: PanelTab; label: string }> = [
  { id: "home", label: "Home" },
  { id: "next", label: "Next" },
  { id: "files", label: "Files" },
  { id: "outputs", label: "Outputs" },
  { id: "dev", label: "Dev" },
];
const MEDIA_EXTENSIONS = "mp4|mov|webm|m4v|png|jpg|jpeg|webp|gif";
const TEXT_EXTENSIONS = "ts|tsx|js|jsx|py|md|json|css|html|yml|yaml|toml|txt|sql";
const PATH_EXTENSIONS = `${TEXT_EXTENSIONS}|${MEDIA_EXTENSIONS}`;
const VIDEO_EXTENSIONS = new Set(["mp4", "mov", "webm", "m4v"]);
const IMAGE_EXTENSIONS = new Set(["png", "jpg", "jpeg", "webp", "gif"]);
const ANSI_PATTERN = new RegExp(
  `${String.fromCharCode(27)}(?:[@-Z\\\\-_]|\\[[0-?]*[ -/]*[@-~])`,
  "g",
);

function cleanText(text: string): string {
  return text.replace(ANSI_PATTERN, "").replace(/\r/g, "");
}

function nextId(prefix: string): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) {
    return `${prefix}-${crypto.randomUUID()}`;
  }
  return `${prefix}-${Date.now().toString(36)}-${Math.random()
    .toString(36)
    .slice(2)}`;
}

function makeMessage(role: ChatRole, content: string): ChatMessage {
  return { id: nextId(role), role, content: cleanText(content), status: "complete" };
}

function asText(value: unknown): string {
  return typeof value === "string" ? value : "";
}

function asNumber(value: unknown): number {
  return typeof value === "number" && Number.isFinite(value) ? value : 0;
}

function formatCount(value: unknown): string {
  return new Intl.NumberFormat().format(asNumber(value));
}

function formatCents(value: unknown): string {
  return new Intl.NumberFormat(undefined, {
    currency: "USD",
    maximumFractionDigits: 0,
    style: "currency",
  }).format(asNumber(value) / 100);
}

function formatMicrousd(value: unknown): string {
  const dollars = asNumber(value) / 1_000_000;
  if (dollars <= 0) return "$0";
  if (dollars < 1) return `$${dollars.toFixed(2)}`;
  return new Intl.NumberFormat(undefined, {
    currency: "USD",
    maximumFractionDigits: 0,
    style: "currency",
  }).format(dollars);
}

function readableDate(value?: string): string {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString([], {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
}

function humanizeJobKind(kind?: string): string {
  const value = (kind || "gated action").trim();
  if (value === "product.deploy") return "Deploy product site";
  if (value === "vendor.stripe_setup") return "Set up Stripe products";
  if (value === "product.api_route") return "Wire product API route";
  return value
    .replace(/[._-]+/g, " ")
    .replace(/\b\w/g, (char) => char.toUpperCase());
}

function humanizeArtifactStatus(status?: string): string {
  switch ((status || "").trim()) {
    case "local_source":
      return "Local";
    case "published_local":
      return "Published local";
    case "draft_only":
      return "Draft only";
    case "generated":
      return "Generated";
    case "asset_without_receipt":
      return "No receipt";
    case "missing":
    case "":
      return "Missing";
    default:
      return humanizeJobKind(status);
  }
}

function humanizeStatus(status?: string): string {
  const value = (status || "").trim().toLowerCase();
  if (!value) return "Recorded";
  if (/blocked|fail|error|overdue|attention|missing/.test(value)) return "Needs attention";
  if (/recover/.test(value)) return "Recovering";
  if (/queued|pending|waiting|scheduled|needed/.test(value)) return "Waiting";
  if (/running|active|watch|working|research_first/.test(value)) return "Working";
  if (/done|complete|success|passed|visible|previewable/.test(value)) return "Ready";
  if (value === "quiet") return "Quiet";
  return humanizeJobKind(value);
}

function taskLabel(task: BusinessOverviewTask | BusinessOverviewJob): string {
  const source = (task as BusinessOverviewTask).source;
  const kind = (task as BusinessOverviewJob).kind;
  return task.label || humanizeJobKind(source || kind);
}

function taskDetail(task: BusinessOverviewTask | BusinessOverviewJob): string {
  if (task.detail) return task.detail;
  if ("kind" in task) return gatedActionDetail(task);
  return task.updated_at ? `Updated ${readableDate(task.updated_at)}` : "";
}

function naturalToolLabel(tool: ToolEntry): string {
  const text = `${tool.name} ${tool.context || ""} ${tool.summary || ""}`.toLowerCase();
  if (tool.status === "error") return "Action needs attention";
  if (/write|patch|edit|file|agent|claude/.test(text)) return "Editing files";
  if (/build|npm|vite|compile|test|pytest/.test(text)) return "Checking build";
  if (/preview|site|surface|product/.test(text)) return "Checking product";
  if (/creative|video|image|ad/.test(text)) return "Creating ad asset";
  if (/research|icp|channel|competitor|pricing/.test(text)) return "Researching market";
  if (/cron|wake|schedule/.test(text)) return "Checking schedule";
  if (/shell|exec|command/.test(text)) return "Running command";
  return humanizeJobKind(tool.name || "Action");
}

function toolDetail(tool: ToolEntry): string {
  const detail = friendlyError(tool.error || tool.summary || tool.preview || tool.context || "");
  return detail || humanizeStatus(tool.status);
}

function toneClasses(tone?: string): string {
  const value = (tone || "").toLowerCase();
  if (/blocked|error|fail/.test(value)) return "border-red-400/25 bg-red-400/10 text-red-100";
  if (/done|ready|success/.test(value)) return "border-emerald-400/25 bg-emerald-400/10 text-emerald-100";
  if (/active|working|running/.test(value)) return "border-sky-400/25 bg-sky-400/10 text-sky-100";
  if (/waiting|pending|queued/.test(value)) return "border-amber-300/25 bg-amber-300/10 text-amber-100";
  return "border-zinc-800 bg-zinc-900 text-zinc-400";
}

function quoteTakyonArg(value: string): string {
  const trimmed = value.trim();
  if (!trimmed) return '""';
  if (/^[a-zA-Z0-9._:/-]+$/.test(trimmed)) return trimmed;
  return `"${trimmed.replace(/\\/g, "\\\\").replace(/"/g, '\\"')}"`;
}

function friendlyError(message?: string | null): string {
  const text = (message || "").trim();
  if (!text) return "";
  if (/No inference provider configured|OPENROUTER_API_KEY|OPENAI_API_KEY|ANTHROPIC_API_KEY/i.test(text)) {
    return "Model connection unavailable.";
  }
  return text.split(/\n/)[0].slice(0, 140);
}

function gatedActionDetail(job: BusinessOverviewJob): string {
  const kind = job.kind || "";
  const updated = job.updated_at ? `Updated ${readableDate(job.updated_at)}` : "";
  let gate = "Queued only as gated follow-up work; the CEO decides when state changes.";
  if (kind === "product.deploy") {
    gate = "Requires deploy target, domain/provider config, and live approval.";
  } else if (kind === "vendor.stripe_setup") {
    gate = "Requires live mode, Stripe credentials, products/prices, and webhook setup.";
  } else if (kind === "product.api_route") {
    gate = "Requires provider credentials, product auth, budget gates, and usage receipts.";
  }
  return [gate, updated].filter(Boolean).join(" · ");
}

function compactPath(path?: string): string {
  if (!path) return "";
  if (path.length <= 34) return path;
  const parts = path.split("/");
  if (parts.length <= 2) return path.slice(0, 31) + "...";
  return `${parts[0]}/.../${parts[parts.length - 1]}`;
}

function humanLabel(value?: string): string {
  const text = (value || "").trim();
  if (!text) return "";
  const name = text.split("/").pop() || text;
  const withoutExtension = name.replace(/\.[a-z0-9]+$/i, "");
  return withoutExtension
    .replace(/[._-]+/g, " ")
    .replace(/\b\w/g, (char) => char.toUpperCase());
}

function docTile(path?: string, label?: string, status?: string, detail?: string): SourceDocTile | null {
  const cleanPath = (path || "").trim();
  if (!cleanPath) return null;
  return {
    detail,
    label: label || humanLabel(cleanPath) || compactPath(cleanPath),
    path: cleanPath,
    status,
  };
}

function uniqueDocs(items: Array<SourceDocTile | null | undefined>): SourceDocTile[] {
  const byPath = new Map<string, SourceDocTile>();
  for (const item of items) {
    if (!item?.path || byPath.has(item.path)) continue;
    byPath.set(item.path, item);
  }
  return [...byPath.values()];
}

function loadCreateInTestModeDefault(): boolean {
  if (typeof window === "undefined") return false;
  return window.localStorage.getItem(CREATE_MODE_STORAGE_KEY) === "1";
}

function wait(ms: number): Promise<void> {
  return new Promise((resolve) => window.setTimeout(resolve, ms));
}

function isBusyError(err: unknown): boolean {
  const message = err instanceof Error ? err.message : String(err);
  return /session busy|busy|4009/i.test(message);
}

function updateStreamingAssistant(
  prev: ChatMessage[],
  updater: (current: ChatMessage | null) => ChatMessage,
): ChatMessage[] {
  const idx = [...prev]
    .reverse()
    .findIndex((m) => m.role === "assistant" && m.status === "streaming");
  if (idx === -1) return [...prev, updater(null)];
  const realIdx = prev.length - 1 - idx;
  const next = [...prev];
  next[realIdx] = updater(next[realIdx]);
  return next;
}

function messageFromResume(
  item: NonNullable<SessionResumeResponse["messages"]>[number],
): ChatMessage | null {
  if (item.role === "tool") {
    const label = item.name ? `Tool: ${item.name}` : "Tool";
    return makeMessage("system", `${label}${item.context ? `: ${item.context}` : ""}`);
  }
  if (item.role === "user" || item.role === "assistant" || item.role === "system") {
    const text = item.text?.trim();
    return text ? makeMessage(item.role, text) : null;
  }
  return null;
}

function extractPaths(text: string): string[] {
  const paths = new Set<string>();
  const patterns = [
    /^\*\*\* (?:Add|Update|Delete) File: (.+)$/gm,
    /^(?:\+\+\+|---) b\/(.+)$/gm,
    new RegExp(`\\b(?:[\\w.-]+\\/)+[\\w.+-]+\\.(?:${PATH_EXTENSIONS})\\b`, "g"),
    new RegExp(`\\b\\/(?:[\\w .+-]+\\/)+[\\w .+-]+\\.(?:${PATH_EXTENSIONS})\\b`, "g"),
  ];

  for (const pattern of patterns) {
    for (const match of text.matchAll(pattern)) {
      const candidate = (match[1] ?? match[0]).trim();
      if (candidate && !candidate.includes("node_modules")) {
        paths.add(cleanText(candidate.replace(/^["'`]|["'`]$/g, "")));
      }
    }
  }

  return [...paths].slice(0, 8);
}

function mediaKindForPath(path?: string): "image" | "video" | undefined {
  const ext = (path?.split("?")[0]?.split(".").pop() || "").toLowerCase();
  if (VIDEO_EXTENSIONS.has(ext)) return "video";
  if (IMAGE_EXTENSIONS.has(ext)) return "image";
  return undefined;
}

function deliverablesFromTool(tool: ToolEntry): Deliverable[] {
  const haystack = [tool.context, tool.summary, tool.inline_diff].filter(Boolean).join("\n");
  const paths = extractPaths(haystack);
  const at = Date.now();
  const kind: Deliverable["kind"] = tool.inline_diff
    ? "diff"
    : /deploy|vercel|caddy|systemd|service/i.test(tool.name + haystack)
      ? "deploy"
      : paths.length
        ? "file"
        : "tool";

  if (paths.length) {
    return paths.map((path, index) => ({
      id: `${tool.id}-${path}-${index}`,
      title: path.split("/").pop() || path,
      detail: cleanText(tool.summary || `${tool.name} touched this file`),
      path,
      kind: mediaKindForPath(path) || kind,
      at,
    }));
  }

  if (!tool.summary && !tool.inline_diff && !tool.error) return [];

  return [
    {
      id: `${tool.id}-summary`,
      title: tool.name,
      detail: cleanText(tool.error || tool.summary || "Tool output is available."),
      kind,
      at,
    },
  ];
}

function upsertDeliverables(prev: Deliverable[], incoming: Deliverable[]): Deliverable[] {
  if (!incoming.length) return prev;
  const byKey = new Map<string, Deliverable>();
  for (const item of [...incoming, ...prev]) {
    const key = item.path || item.id;
    if (!byKey.has(key)) byKey.set(key, item);
  }
  return [...byKey.values()].slice(0, 16);
}

function mergeOutputs(current: Deliverable[], historical: Deliverable[]): Deliverable[] {
  const byKey = new Map<string, Deliverable>();
  for (const item of [...current, ...historical]) {
    const key = item.path || item.id;
    if (!byKey.has(key)) byKey.set(key, item);
  }
  return [...byKey.values()].sort((a, b) => b.at - a.at).slice(0, 60);
}

function prettyTime(ts: number): string {
  return new Date(ts).toLocaleTimeString([], {
    hour: "numeric",
    minute: "2-digit",
  });
}

function connectionDot(state: ConnectionState): string {
  if (state === "open") return "bg-emerald-400";
  if (state === "connecting" || state === "idle") return "bg-amber-400";
  return "bg-red-500";
}

function normalizeScopeState(value: Partial<ScopeState> | null | undefined): ScopeState {
  const business = typeof value?.business === "string" ? value.business : "";
  const businesses = Array.isArray(value?.businesses)
    ? value.businesses.filter((item): item is BusinessSummary => !!item && typeof item === "object")
    : [];
  const current =
    value?.current && typeof value.current === "object" ? value.current : {};
  const overview =
    value?.overview && typeof value.overview === "object" ? value.overview : undefined;
  return {
    scope: business ? `business:${business}` : "global",
    business,
    current,
    businesses,
    overview,
    warning: typeof value?.warning === "string" ? value.warning : undefined,
    auto_switched_business:
      typeof value?.auto_switched_business === "string"
        ? value.auto_switched_business
        : undefined,
    auto_scope_warning:
      typeof value?.auto_scope_warning === "string"
        ? value.auto_scope_warning
        : undefined,
  };
}

function scopeName(scope: ScopeState): string {
  if (!scope.business) return "Global";
  const currentName = scope.current?.name || scope.current?.slug || scope.business;
  return currentName === scope.business ? `business:${scope.business}` : currentName;
}

function scopeDetail(scope: ScopeState): string {
  if (!scope.business) return "account scope";
  const mode = scope.current?.mode || scope.current?.status || scope.current?.state || "";
  return mode ? `business:${scope.business} · ${mode}` : `business:${scope.business}`;
}

function businessModeLabel(item: BusinessSummary): string {
  const parts = [item.state || item.status, item.mode].filter(Boolean);
  return parts.length ? parts.join("/") : "business";
}

function normalizeBusinessLookup(value: string): string {
  return value
    .trim()
    .toLowerCase()
    .replace(/^business:/, "")
    .replace(/^["']|["']$/g, "")
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "");
}

function naturalScopeChange(text: string, scope: ScopeState): string | undefined {
  const trimmed = text.trim().replace(/[.!?]+$/g, "");
  const lower = trimmed.toLowerCase();
  if (
    /^(switch|change|move|go|open|set)\s+(to\s+)?(global|account|root)(\s+scope)?$/.test(
      lower,
    )
  ) {
    return "";
  }

  const businessMatch =
    trimmed.match(/^(?:switch|change|move|go|open)\s+(?:to|into|in|on)\s+(?:business\s+)?(.+)$/i) ||
    trimmed.match(/^(?:use|set)\s+business\s+(.+)$/i);
  const candidate = businessMatch?.[1]?.trim();
  if (!candidate) return undefined;

  const normalized = normalizeBusinessLookup(candidate);
  const match = scope.businesses.find((item) => {
    const slug = normalizeBusinessLookup(item.slug || "");
    const name = normalizeBusinessLookup(item.name || "");
    return slug === normalized || name === normalized;
  });
  return match?.slug || undefined;
}

function applyCreateModeDefault(line: string, createInTestMode: boolean): string {
  if (!createInTestMode) return line;
  const trimmed = line.trimStart();
  if (!/^\/(?:create|build|init)(?:\s|$)/i.test(trimmed)) return line;
  if (/(?:^|\s)--(?:test|live)(?:\s|$)/i.test(trimmed)) return line;
  return line.replace(/^(\s*\/(?:create|build|init))(?:\s|$)/i, "$1 --test ");
}

function isSlashCommandPrefix(value: string): boolean {
  return value.startsWith("/") && !/\s/.test(value.slice(1));
}

export default function ChatPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const resumeParam = searchParams.get("resume");
  const initialBusinessParam = useMemo(
    () => normalizeBusinessLookup(searchParams.get("business") || searchParams.get("scope") || ""),
    [searchParams],
  );
  const [version, setVersion] = useState(0);
  const gw = useMemo(() => {
    void version;
    return new GatewayClient();
  }, [version]);

  const [state, setState] = useState<ConnectionState>("idle");
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [info, setInfo] = useState<SessionInfo>({});
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [tools, setTools] = useState<ToolEntry[]>([]);
  const [deliverables, setDeliverables] = useState<Deliverable[]>([]);
  const [historicalOutputs, setHistoricalOutputs] = useState<{
    business: string;
    items: Deliverable[];
  }>({ business: "", items: [] });
  const [statusItems, setStatusItems] = useState<string[]>([]);
  const [scopeState, setScopeState] = useState<ScopeState>(EMPTY_SCOPE_STATE);
  const [input, setInput] = useState("");
  const [slashItems, setSlashItems] = useState<SlashCompletionItem[]>([]);
  const [slashIndex, setSlashIndex] = useState(0);
  const [createInTestMode] = useState(loadCreateInTestModeDefault);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(() =>
    typeof window !== "undefined" && !window.__TAKYON_SESSION_TOKEN__
      ? "Session token unavailable. Open this page through the Takyon dashboard server."
      : null,
  );
  const [rightOpen, setRightOpen] = useState(false);

  const scrollerRef = useRef<HTMLDivElement | null>(null);
  const inputRef = useRef<HTMLTextAreaElement | null>(null);
  const sessionIdRef = useRef<string | null>(null);

  useEffect(() => {
    sessionIdRef.current = sessionId;
  }, [sessionId]);

  useEffect(() => {
    window.localStorage.setItem(CREATE_MODE_STORAGE_KEY, createInTestMode ? "1" : "0");
  }, [createInTestMode]);

  useEffect(() => {
    if (!resumeParam) return;
    let cancelled = false;
    api
      .getSessionLatestDescendant(resumeParam)
      .then((res) => {
        if (cancelled || !res.session_id || res.session_id === resumeParam) return;
        const next = new URLSearchParams(searchParams);
        next.set("resume", res.session_id);
        setSearchParams(next, { replace: true });
      })
      .catch(() => {
        /* resume correction is best effort */
      });
    return () => {
      cancelled = true;
    };
  }, [resumeParam, searchParams, setSearchParams]);

  useEffect(() => {
    const scroller = scrollerRef.current;
    if (!scroller) return;
    scroller.scrollTo({ top: scroller.scrollHeight, behavior: "smooth" });
  }, [messages, running]);

  useEffect(() => {
    let cancelled = false;
    const cleanup: Array<() => void> = [];

    const refreshScope = () => {
      const activeSessionId = sessionIdRef.current;
      if (!activeSessionId) return;
      void gw
        .request<ScopeState>(
          "takyon.scope.get",
          { session_id: activeSessionId },
          10_000,
        )
        .then((scope) => {
          if (cancelled) return;
          const nextScope = normalizeScopeState(scope);
          setScopeState(nextScope);
          if (nextScope.auto_switched_business) {
            setMessages((prev) => [
              ...prev,
              makeMessage(
                "system",
                `Entered business:${nextScope.auto_switched_business}`,
              ),
            ]);
          } else if (nextScope.auto_scope_warning) {
            setMessages((prev) => [
              ...prev,
              makeMessage("system", nextScope.auto_scope_warning || ""),
            ]);
          }
        })
        .catch(() => {
          /* scope refresh is best effort */
        });
    };

    cleanup.push(gw.onState(setState));

    cleanup.push(
      gw.on<SessionInfo>("session.info", (ev) => {
        if (ev.payload) setInfo((prev) => ({ ...prev, ...ev.payload }));
      }),
    );

    cleanup.push(
      gw.on("message.start", () => {
        setRunning(true);
        setError(null);
        setMessages((prev) =>
          updateStreamingAssistant(prev, () => ({
            id: nextId("assistant"),
            role: "assistant",
            content: "",
            status: "streaming",
          })),
        );
      }),
    );

    cleanup.push(
      gw.on<{ text?: string }>("message.delta", (ev) => {
        const text = asText(ev.payload?.text);
        if (!text) return;
        setMessages((prev) =>
          updateStreamingAssistant(prev, (current) => ({
            id: current?.id ?? nextId("assistant"),
            role: "assistant",
            content: cleanText(`${current?.content ?? ""}${text}`),
            status: "streaming",
          })),
        );
      }),
    );

    cleanup.push(
      gw.on<{ text?: string; status?: string; warning?: string }>(
        "message.complete",
        (ev) => {
          const text = asText(ev.payload?.text);
          const status = ev.payload?.status;
          setRunning(false);
          setMessages((prev) =>
            updateStreamingAssistant(prev, (current) => ({
              id: current?.id ?? nextId("assistant"),
              role: "assistant",
              content: cleanText(text || current?.content || "(empty response)"),
              status:
                status === "error" || status === "interrupted"
                  ? status
                  : "complete",
            })),
          );
          if (ev.payload?.warning) {
            setStatusItems((prev) => [ev.payload!.warning!, ...prev].slice(0, 5));
          }
          refreshScope();
        },
      ),
    );

    cleanup.push(
      gw.on<{ text?: string }>("thinking.delta", (ev) => {
        const text = asText(ev.payload?.text).trim();
          if (text) setStatusItems((prev) => [cleanText(text), ...prev].slice(0, 5));
      }),
    );

    cleanup.push(
      gw.on<{ text?: string }>("reasoning.delta", (ev) => {
        const text = asText(ev.payload?.text).trim();
        if (text) setStatusItems((prev) => [cleanText(text), ...prev].slice(0, 5));
      }),
    );

    cleanup.push(
      gw.on<{ text?: string }>("status.update", (ev) => {
        const text = asText(ev.payload?.text).trim();
        if (text) setStatusItems((prev) => [cleanText(text), ...prev].slice(0, 5));
      }),
    );

    cleanup.push(
      gw.on<{ message?: string }>("error", (ev) => {
        const message = ev.payload?.message || "The chat gateway reported an error.";
        setRunning(false);
        setError(message);
        setMessages((prev) => [...prev, makeMessage("system", message)]);
      }),
    );

    cleanup.push(
      gw.on<{ tool_id?: string; name?: string; context?: string }>(
        "tool.start",
        (ev) => {
          const p = ev.payload;
          if (!p?.tool_id) return;
          const toolId = p.tool_id;
          setTools((prev) =>
            [
              ...prev,
              {
                id: `tool-${toolId}-${Date.now()}`,
                tool_id: toolId,
                name: p.name || "tool",
                context: p.context,
                status: "running" as const,
                startedAt: Date.now(),
              },
            ].slice(-30),
          );
        },
      ),
    );

    cleanup.push(
      gw.on<{ name?: string; preview?: string }>("tool.progress", (ev) => {
        const p = ev.payload;
        if (!p?.name || !p.preview) return;
        setTools((prev) =>
          prev.map((tool) =>
            tool.status === "running" && tool.name === p.name
              ? { ...tool, preview: p.preview }
              : tool,
          ),
        );
      }),
    );

    cleanup.push(
      gw.on<{
        tool_id?: string;
        name?: string;
        summary?: string;
        error?: string;
        inline_diff?: string;
      }>("tool.complete", (ev) => {
        const p = ev.payload;
        if (!p?.tool_id) return;
        const completedAt = Date.now();
        const completedTool: ToolEntry = {
          id: `tool-${p.tool_id}`,
          tool_id: p.tool_id,
          name: p.name || "tool",
          status: p.error ? "error" : "done",
          summary: p.summary,
          error: p.error,
          inline_diff: p.inline_diff,
          startedAt: completedAt,
          completedAt,
        };

        setTools((prev) => {
          const exists = prev.some((tool) => tool.tool_id === p.tool_id);
          if (!exists) return [...prev, completedTool].slice(-30);
          return prev.map((tool) =>
            tool.tool_id === p.tool_id
              ? {
                  ...tool,
                  status: p.error ? "error" : "done",
                  summary: p.summary,
                  error: p.error,
                  inline_diff: p.inline_diff,
                  completedAt,
                }
              : tool,
          );
        });
        setDeliverables((prev) =>
          upsertDeliverables(prev, deliverablesFromTool(completedTool)),
        );
        refreshScope();
      }),
    );

    gw.connect()
      .then(async () => {
        if (cancelled) return;
        const hydrateScope = async (nextSessionId: string) => {
          const scope = !resumeParam && initialBusinessParam
            ? await gw.request<ScopeState>(
                "takyon.scope.set",
                { session_id: nextSessionId, business: initialBusinessParam },
                10_000,
              )
            : await gw.request<ScopeState>(
                "takyon.scope.get",
                { session_id: nextSessionId },
                10_000,
              );
          if (!cancelled) setScopeState(normalizeScopeState(scope));
        };

        if (resumeParam) {
          const res = await gw.request<SessionResumeResponse>(
            "session.resume",
            { session_id: resumeParam, cols: 100 },
          );
          if (cancelled) return;
          setSessionId(res.session_id);
          setInfo((prev) => ({ ...prev, ...res.info }));
          void hydrateScope(res.session_id).catch(() => {
            /* scope hydration is best effort */
          });
          const resumed = (res.messages || [])
            .map(messageFromResume)
            .filter((m): m is ChatMessage => !!m);
          setMessages(resumed);
          return;
        }

        const res = await gw.request<SessionCreateResponse>("session.create", {
          cols: 100,
        });
        if (cancelled) return;
        setSessionId(res.session_id);
        setInfo((prev) => ({ ...prev, ...res.info }));
        void hydrateScope(res.session_id).catch(() => {
          /* scope hydration is best effort */
        });
      })
      .catch((err: Error) => {
        if (!cancelled) {
          setError(err.message);
          setMessages((prev) => [...prev, makeMessage("system", err.message)]);
        }
      });

    return () => {
      cancelled = true;
      for (const fn of cleanup) fn();
      gw.close();
    };
  }, [gw, initialBusinessParam, resumeParam]);

  useEffect(() => {
    if (state !== "open" || !sessionId || !scopeState.business) return;
    let cancelled = false;
    void gw
      .request<BusinessOutputsResponse>(
        "takyon.outputs.list",
        { session_id: sessionId, limit: 50 },
        10_000,
      )
      .then((res) => {
        if (cancelled) return;
        const outputs = Array.isArray(res.outputs) ? res.outputs : [];
        setHistoricalOutputs({ business: scopeState.business, items: outputs });
      })
      .catch(() => {
        /* historical outputs are best effort */
      });
    return () => {
      cancelled = true;
    };
  }, [gw, scopeState.business, sessionId, state]);

  useEffect(() => {
    if (state !== "open" || !sessionId || !isSlashCommandPrefix(input)) {
      const resetTimer = window.setTimeout(() => {
        setSlashItems((prev) => (prev.length ? [] : prev));
        setSlashIndex(0);
      }, 0);
      return () => {
        window.clearTimeout(resetTimer);
      };
    }

    let cancelled = false;
    const timer = window.setTimeout(() => {
      gw.request<SlashCompleteResponse>(
        "takyon.slash.complete",
        { session_id: sessionId, text: input },
        10_000,
      )
        .then((res) => {
          if (cancelled) return;
          const items = Array.isArray(res.items) ? res.items.filter((item) => item.text) : [];
          setSlashItems(items);
          setSlashIndex(0);
        })
        .catch(() => {
          if (!cancelled) {
            setSlashItems([]);
            setSlashIndex(0);
          }
        });
    }, 80);

    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [gw, input, scopeState.business, sessionId, state]);

  const appendSystem = useCallback((text: string) => {
    setMessages((prev) => [...prev, makeMessage("system", text)]);
  }, []);

  const interrupt = useCallback(async () => {
    if (!sessionId) return;
    try {
      await gw.request("session.interrupt", { session_id: sessionId }, 10_000);
      setRunning(false);
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      setError(message);
      appendSystem(`Interrupt failed: ${message}`);
    }
  }, [appendSystem, gw, sessionId]);

  const setTakyonScope = useCallback(
    async (business: string) => {
      if (!sessionId) return;
      const res = await gw.request<ScopeState>(
        "takyon.scope.set",
        { session_id: sessionId, business },
        10_000,
      );
      const nextScope = normalizeScopeState(res);
      setScopeState(nextScope);
      appendSystem(
        nextScope.business
          ? `Using business:${nextScope.business}`
          : "Using global scope",
      );
      requestAnimationFrame(() => inputRef.current?.focus());
    },
    [appendSystem, gw, sessionId],
  );

  const applySlashCompletion = useCallback((item: SlashCompletionItem) => {
    const next = `${item.text} `;
    setInput(next);
    setSlashItems([]);
    setSlashIndex(0);
    requestAnimationFrame(() => {
      const target = inputRef.current;
      target?.focus();
      target?.setSelectionRange(next.length, next.length);
    });
  }, []);

  const contextForPrompt = useCallback(
    async (text: string): Promise<string> => {
      if (!sessionId) return text;
      const res = await gw.request<TakyonPromptContextResponse>(
        "takyon.prompt.context",
        { session_id: sessionId, text },
        10_000,
      );
      setScopeState(normalizeScopeState(res));
      const promptText = res.text || text;
      if (!createInTestMode) return promptText;
      return [
        "Operator UI preference: create any new business in test mode unless the operator explicitly asks for live mode.",
        "",
        promptText,
      ].join("\n");
    },
    [createInTestMode, gw, sessionId],
  );

  const requestPromptSubmit = useCallback(
    async (text: string) => {
      if (!sessionId) throw new Error("Chat is still connecting.");

      for (let attempt = 0; attempt < 8; attempt++) {
        try {
          await gw.request(
            "prompt.submit",
            { session_id: sessionId, text },
            30_000,
          );
          return;
        } catch (err) {
          if (attempt < 7 && isBusyError(err)) {
            await wait(350 + attempt * 200);
            continue;
          }
          throw err;
        }
      }
    },
    [gw, sessionId],
  );

  const submitPrompt = useCallback(
    async (text: string) => {
      if (!sessionId) throw new Error("Chat is still connecting.");

      if (running) {
        await gw.request("session.interrupt", { session_id: sessionId }, 10_000);
        await wait(400);
      }

      setRunning(true);
      setError(null);
      const promptText = await contextForPrompt(text);
      await requestPromptSubmit(promptText);
    },
    [contextForPrompt, gw, requestPromptSubmit, running, sessionId],
  );

  const executeTakyonSlash = useCallback(
    async (text: string) => {
      if (!sessionId) throw new Error("Chat is still connecting.");
      const effectiveText = applyCreateModeDefault(text, createInTestMode);
      if (effectiveText !== text) {
        appendSystem(`New-business default applied: \`${effectiveText.trim()}\``);
      }
      const res = await gw.request<TakyonShellResponse>(
        "takyon.shell.exec",
        { session_id: sessionId, line: effectiveText },
        180_000,
      );
      setScopeState(normalizeScopeState(res));
      const output = cleanText(res.output || "").trim();
      if (output) appendSystem(output);
    },
    [appendSystem, createInTestMode, gw, sessionId],
  );

  const runTakyonLine = useCallback(
    async (line: string) => {
      if (state !== "open") return;
      setMessages((prev) => [...prev, makeMessage("user", line)]);
      setRunning(true);
      setError(null);
      try {
        await executeTakyonSlash(line);
      } catch (err) {
        const message = err instanceof Error ? err.message : String(err);
        setError(message);
        appendSystem(message);
      } finally {
        setRunning(false);
        requestAnimationFrame(() => inputRef.current?.focus());
      }
    },
    [appendSystem, executeTakyonSlash, state],
  );

  const listBusinessFiles = useCallback(
    async (path: string): Promise<BusinessOverviewFile[]> => {
      if (!sessionId) return [];
      const res = await gw.request<BusinessFilesResponse>(
        "takyon.files.list",
        { session_id: sessionId, path },
        10_000,
      );
      setScopeState(normalizeScopeState(res));
      return Array.isArray(res.files) ? res.files : [];
    },
    [gw, sessionId],
  );

  const resolveBusinessMedia = useCallback(
    async (path: string): Promise<BusinessMediaResponse> => {
      if (!sessionId) throw new Error("Chat is still connecting.");
      const res = await gw.request<BusinessMediaResponse>(
        "takyon.file.media",
        { session_id: sessionId, path },
        20_000,
      );
      setScopeState(normalizeScopeState(res));
      return res;
    },
    [gw, sessionId],
  );

  const readBusinessFile = useCallback(
    async (path: string): Promise<BusinessFileReadResponse> => {
      if (!sessionId) throw new Error("Chat is still connecting.");
      const res = await gw.request<BusinessFileReadResponse>(
        "takyon.file.read",
        { session_id: sessionId, path },
        20_000,
      );
      setScopeState(normalizeScopeState(res));
      return res;
    },
    [gw, sessionId],
  );

  const resolveBusinessSitePreview = useCallback(
    async (path?: string): Promise<BusinessSitePreviewResponse> => {
      if (!sessionId) throw new Error("Chat is still connecting.");
      const res = await gw.request<BusinessSitePreviewResponse>(
        "takyon.site.preview",
        { session_id: sessionId, path },
        20_000,
      );
      setScopeState(normalizeScopeState(res));
      return res;
    },
    [gw, sessionId],
  );

  const handleSubmit = useCallback(async () => {
    const text = input.trim();
    if (state !== "open") return;
    if (!text) {
      if (running) await interrupt();
      return;
    }

    setInput("");
    setSlashItems([]);
    setSlashIndex(0);
    setMessages((prev) => [...prev, makeMessage("user", text)]);

    try {
      const requestedBusiness = naturalScopeChange(text, scopeState);
      if (requestedBusiness !== undefined) {
        await setTakyonScope(requestedBusiness);
      } else if (text.startsWith("/")) {
        setRunning(true);
        await executeTakyonSlash(text);
        setRunning(false);
      } else {
        await submitPrompt(text);
      }
      requestAnimationFrame(() => inputRef.current?.focus());
    } catch (err) {
      setRunning(false);
      const message = err instanceof Error ? err.message : String(err);
      setError(message);
      appendSystem(message);
    }
  }, [
    appendSystem,
    executeTakyonSlash,
    input,
    interrupt,
    running,
    scopeState,
    setTakyonScope,
    state,
    submitPrompt,
  ]);

  const reconnect = useCallback(() => {
    setError(null);
    setRunning(false);
    setTools([]);
    setVersion((v) => v + 1);
  }, []);

  const newChatHref = useMemo(() => {
    const params = new URLSearchParams();
    if (scopeState.business) params.set("business", scopeState.business);
    const query = params.toString();
    return `/chat${query ? `?${query}` : ""}`;
  }, [scopeState.business]);

  const onComposerSubmit = (event: FormEvent) => {
    event.preventDefault();
    void handleSubmit();
  };

  const onComposerKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (slashItems.length > 0 && isSlashCommandPrefix(input)) {
      if (event.key === "ArrowDown") {
        event.preventDefault();
        setSlashIndex((idx) => (idx + 1) % slashItems.length);
        return;
      }
      if (event.key === "ArrowUp") {
        event.preventDefault();
        setSlashIndex((idx) => (idx - 1 + slashItems.length) % slashItems.length);
        return;
      }
      if (event.key === "Tab") {
        event.preventDefault();
        applySlashCompletion(slashItems[slashIndex] || slashItems[0]);
        return;
      }
      if (event.key === "Escape") {
        event.preventDefault();
        setSlashItems([]);
        setSlashIndex(0);
        return;
      }
      if (event.key === "Enter" && !event.shiftKey) {
        const selected = slashItems[slashIndex] || slashItems[0];
        if (selected && input.trim() !== selected.text) {
          event.preventDefault();
          applySlashCompletion(selected);
          return;
        }
      }
    }

    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      void handleSubmit();
    }
  };

  const canAct = state === "open" && (!!input.trim() || running);
  const inBusiness = !!scopeState.business;
  const scopedHistoricalOutputs =
    historicalOutputs.business === scopeState.business
      ? historicalOutputs.items
      : [];

  return (
    <div className="flex h-full min-h-0 w-full flex-col overflow-hidden bg-black normal-case text-zinc-100 [font-family:Inter,ui-sans-serif,system-ui,-apple-system,BlinkMacSystemFont,'Segoe_UI',sans-serif]">
      <PluginSlot name="chat:top" />

      {inBusiness && rightOpen && (
        <button
          aria-label="Close side panel"
          onClick={() => setRightOpen(false)}
          className="fixed inset-0 z-[55] bg-black/70 backdrop-blur-sm lg:hidden"
          type="button"
        />
      )}

      <div
        className={cn(
          "grid min-h-0 min-w-0 flex-1 bg-black",
          inBusiness
            ? "lg:grid-cols-[minmax(0,1fr)_minmax(320px,26vw)]"
            : "lg:grid-cols-[minmax(0,1fr)]",
        )}
      >
        <main className="flex min-h-0 min-w-0 flex-col bg-black">
          <header className="flex h-14 shrink-0 items-center justify-between border-b border-zinc-900 px-4 sm:px-6">
            <div className="flex min-w-0 items-center gap-3">
              <div className="flex min-w-0 items-center gap-2 text-sm font-medium text-zinc-100">
                <span className={cn("h-2 w-2 rounded-full", connectionDot(state))} />
                <div className="min-w-0">
                  <div>Takyon</div>
                  <div className="mt-0.5 truncate text-xs font-normal text-zinc-500">
                    {STATE_LABEL[state]}
                  </div>
                </div>
              </div>
              <ScopeSwitcher
                disabled={state !== "open" || !sessionId}
                onSelect={setTakyonScope}
                scope={scopeState}
              />
            </div>

            <div className="flex items-center gap-1.5">
              {inBusiness && (
                <IconButton
                  label="Open CEO intercom"
                  onClick={() => setRightOpen(true)}
                  className="lg:hidden"
                >
                  <PanelRight className="h-4 w-4" />
                </IconButton>
              )}
              <HeaderLinkActionButton href={newChatHref} label="New chat">
                <Plus className="h-4 w-4" />
              </HeaderLinkActionButton>
              <IconButton label="Reconnect chat" onClick={reconnect}>
                <RefreshCw className="h-4 w-4" />
              </IconButton>
            </div>
          </header>

          {inBusiness ? (
            <CompanyWorkspace
              deliverables={deliverables}
              historicalOutputs={scopedHistoricalOutputs}
              onReadFile={readBusinessFile}
              onResolveMedia={resolveBusinessMedia}
              onResolveSitePreview={resolveBusinessSitePreview}
              scope={scopeState}
              statusItems={statusItems}
              tools={tools}
            />
          ) : (
            <GlobalLaunchpad
              error={error}
              onCreate={runTakyonLine}
              onSelectBusiness={setTakyonScope}
              running={running}
              scope={scopeState}
              state={state}
              statusItems={statusItems}
              tools={tools}
            />
          )}
        </main>

        <aside
          className={cn(
            "min-h-0 overflow-hidden border-l border-zinc-900 bg-black",
            inBusiness ? "lg:relative lg:z-auto lg:flex" : "hidden",
            rightOpen
              ? "fixed inset-y-0 right-0 z-[60] flex w-[min(92vw,390px)]"
              : "hidden",
          )}
        >
          {inBusiness ? (
            <IntercomPanel
              onClose={() => setRightOpen(false)}
              scope={scopeState}
              sessionId={sessionId}
              showClose={rightOpen}
            >
              <Thread
                compact
                error={error}
                messages={messages}
                running={running}
                scope={scopeState}
                scrollerRef={scrollerRef}
                statusItems={statusItems}
                tools={tools}
              >
                <Composer
                  canAct={canAct}
                  compact
                  inputRef={inputRef}
                  isRunning={running}
                  onChange={setInput}
                  onKeyDown={onComposerKeyDown}
                  onSlashApply={applySlashCompletion}
                  onSubmit={onComposerSubmit}
                  slashIndex={slashIndex}
                  slashItems={slashItems}
                  setSlashIndex={setSlashIndex}
                  value={input}
                />
              </Thread>
            </IntercomPanel>
          ) : (
            <DeliverablesPanel
              cwd={info.cwd}
              deliverables={deliverables}
              historicalOutputs={scopedHistoricalOutputs}
              onCommand={runTakyonLine}
              onListFiles={listBusinessFiles}
              onResolveMedia={resolveBusinessMedia}
              onResolveSitePreview={resolveBusinessSitePreview}
              onClose={() => setRightOpen(false)}
              scope={scopeState}
              sessionId={sessionId}
              showClose={rightOpen}
              statusItems={statusItems}
              tools={tools}
            />
          )}
        </aside>
      </div>

      <PluginSlot name="chat:bottom" />
    </div>
  );
}

function GlobalLaunchpad({
  error,
  onCreate,
  onSelectBusiness,
  running,
  scope,
  state,
  statusItems,
  tools,
}: {
  error: string | null;
  onCreate: (line: string) => void;
  onSelectBusiness: (business: string) => Promise<void>;
  running: boolean;
  scope: ScopeState;
  state: ConnectionState;
  statusItems: string[];
  tools: ToolEntry[];
}) {
  const [name, setName] = useState("");
  const [goal, setGoal] = useState("");
  const [mode, setMode] = useState<"test" | "live">("test");
  const [budget, setBudget] = useState("25");
  const [schedule, setSchedule] = useState("every 6h");
  const recentBusinesses = scope.businesses.slice(0, 6);
  const activeTool = tools.slice().reverse().find((tool) => tool.status === "running");
  const latestStatus = activeTool?.name || statusItems[0] || "";
  const canCreate = state === "open" && !running && (!!name.trim() || !!goal.trim());
  const displayError = friendlyError(error);

  const submit = (event: FormEvent) => {
    event.preventDefault();
    const rawName = name.trim() || goal.trim().split(/\s+/).slice(0, 3).join(" ");
    const slug = normalizeBusinessLookup(rawName);
    if (!slug) return;
    const parts = ["/create", mode === "test" ? "--test" : "--live"];
    const budgetValue = Number.parseFloat(budget);
    if (Number.isFinite(budgetValue) && budgetValue >= 0) {
      parts.push("--budget", String(budgetValue));
    }
    if (schedule.trim()) {
      parts.push("--schedule", quoteTakyonArg(schedule));
    }
    parts.push(slug);
    if (goal.trim()) parts.push(quoteTakyonArg(goal));
    onCreate(parts.join(" "));
  };

  return (
    <div className="min-h-0 flex-1 overflow-y-auto bg-black px-4 py-5 sm:px-6">
      <div className="mx-auto grid max-w-5xl gap-4 lg:grid-cols-[420px_minmax(0,1fr)]">
        <form className="rounded-2xl border border-zinc-900 bg-[#050505] p-4" onSubmit={submit}>
          <div className="flex items-center justify-between gap-3">
            <div className="flex items-center gap-2 text-sm font-medium text-zinc-100">
              <Rocket className="h-4 w-4 text-zinc-500" />
              New company
            </div>
            <span className={cn("h-2 w-2 rounded-full", running ? "animate-pulse bg-sky-300" : "bg-emerald-400")} />
          </div>
          {displayError && (
            <div className="mt-3 rounded-lg border border-amber-300/20 bg-amber-300/5 px-3 py-2 text-xs text-amber-100">
              {displayError}
            </div>
          )}
          {running && (
            <div className="mt-3 rounded-lg border border-sky-300/20 bg-sky-300/5 px-3 py-2 text-xs text-sky-100">
              {latestStatus || "Working"}
            </div>
          )}

          <div className="mt-4 grid gap-3">
            <label className="grid gap-1.5">
              <span className="text-xs text-zinc-500">Name</span>
              <input
                className="h-10 rounded-lg border border-zinc-800 bg-black px-3 text-sm text-zinc-100 outline-none transition-colors placeholder:text-zinc-700 focus:border-zinc-600"
                onChange={(event) => setName(event.target.value)}
                placeholder="latexflow"
                value={name}
              />
            </label>
            <label className="grid gap-1.5">
              <span className="text-xs text-zinc-500">Goal</span>
              <textarea
                className="min-h-28 resize-none rounded-lg border border-zinc-800 bg-black px-3 py-2 text-sm leading-6 text-zinc-100 outline-none transition-colors placeholder:text-zinc-700 focus:border-zinc-600"
                onChange={(event) => setGoal(event.target.value)}
                placeholder="Build a business around..."
                value={goal}
              />
            </label>
          </div>

          <div className="mt-4 grid gap-2 sm:grid-cols-[1.2fr_0.8fr_1fr]">
            <div className="rounded-lg border border-zinc-900 bg-black p-2">
              <div className="mb-2 text-xs text-zinc-500">Mode</div>
              <div className="grid grid-cols-2 gap-1 rounded-md bg-zinc-950 p-1">
                {(["test", "live"] as const).map((option) => (
                  <button
                    className={cn(
                      "h-8 rounded px-2 text-xs font-medium transition-colors",
                      mode === option ? "bg-zinc-100 text-black" : "text-zinc-500 hover:bg-zinc-900 hover:text-zinc-100",
                    )}
                    key={option}
                    onClick={() => setMode(option)}
                    type="button"
                  >
                    {option}
                  </button>
                ))}
              </div>
            </div>
            <label className="grid gap-2 rounded-lg border border-zinc-900 bg-black p-2">
              <span className="text-xs text-zinc-500">Budget</span>
              <input
                className="h-8 min-w-0 rounded-md border border-zinc-900 bg-zinc-950 px-2 text-xs text-zinc-100 outline-none focus:border-zinc-700"
                inputMode="decimal"
                onChange={(event) => setBudget(event.target.value)}
                value={budget}
              />
            </label>
            <label className="grid gap-2 rounded-lg border border-zinc-900 bg-black p-2">
              <span className="text-xs text-zinc-500">Wake</span>
              <input
                className="h-8 min-w-0 rounded-md border border-zinc-900 bg-zinc-950 px-2 text-xs text-zinc-100 outline-none focus:border-zinc-700"
                onChange={(event) => setSchedule(event.target.value)}
                value={schedule}
              />
            </label>
          </div>

          <div className="mt-4 flex items-center justify-between gap-3">
            <div className="flex min-w-0 gap-2 text-[0.68rem] text-zinc-600">
              <span className="rounded-full border border-zinc-900 px-2 py-1">research first</span>
              <span className="rounded-full border border-zinc-900 px-2 py-1">{STATE_LABEL[state]}</span>
            </div>
            <button
              className={cn(
                "inline-flex h-9 shrink-0 items-center justify-center gap-2 rounded-lg px-3 text-sm font-medium transition-colors",
                canCreate ? "bg-zinc-100 text-black hover:bg-white" : "bg-zinc-900 text-zinc-600",
              )}
              disabled={!canCreate}
              type="submit"
            >
              <Rocket className="h-4 w-4" />
              Create
            </button>
          </div>
        </form>

        <section className="rounded-2xl border border-zinc-900 bg-[#050505] p-4">
          <div className="flex items-center justify-between gap-3">
            <div className="flex items-center gap-2 text-sm font-medium text-zinc-100">
              <Building2 className="h-4 w-4 text-zinc-500" />
              Companies
            </div>
            <span className="rounded-full border border-zinc-900 px-2 py-0.5 text-[0.65rem] text-zinc-600">
              {formatCount(scope.businesses.length)}
            </span>
          </div>
          <div className="mt-3 grid gap-2">
            {recentBusinesses.length === 0 ? (
              <EmptyPanelLine text="No companies yet." />
            ) : (
              recentBusinesses.map((item) => {
                const slug = item.slug || "";
                return (
                  <button
                    className="group min-w-0 rounded-xl border border-zinc-900 bg-black px-3 py-2.5 text-left transition-colors hover:border-zinc-800 hover:bg-zinc-950"
                    disabled={!slug || state !== "open"}
                    key={slug || item.name}
                    onClick={() => {
                      if (slug) void onSelectBusiness(slug);
                    }}
                    type="button"
                  >
                    <div className="flex items-start justify-between gap-3">
                      <div className="min-w-0">
                        <div className="truncate text-sm font-medium text-zinc-100">
                          {item.name || slug}
                        </div>
                        <div className="mt-0.5 truncate text-xs text-zinc-600">
                          business:{slug} · {businessModeLabel(item)}
                        </div>
                      </div>
                      <ChevronRight className="h-4 w-4 shrink-0 text-zinc-700 transition-colors group-hover:text-zinc-300" />
                    </div>
                  </button>
                );
              })
            )}
          </div>
        </section>
      </div>
    </div>
  );
}

function Thread({
  children,
  compact = false,
  error,
  messages,
  running,
  scope,
  scrollerRef,
  statusItems,
  tools,
}: {
  children: ReactNode;
  compact?: boolean;
  error: string | null;
  messages: ChatMessage[];
  running: boolean;
  scope: ScopeState;
  scrollerRef: RefObject<HTMLDivElement | null>;
  statusItems?: string[];
  tools?: ToolEntry[];
}) {
  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div
        ref={scrollerRef}
        className={cn(
          "min-h-0 flex-1 overflow-y-auto",
          compact ? "px-3 py-3" : "px-4 py-6 sm:px-6",
        )}
      >
        <div
          className={cn(
            "mx-auto flex min-h-full w-full flex-col",
            compact ? "max-w-none" : "max-w-3xl",
          )}
        >
          {error && <ErrorBanner message={error} />}
          {messages.length === 0 ? (
            <ThreadWelcome compact={compact} scope={scope} />
          ) : (
            <div className={cn(compact ? "space-y-4 pb-4" : "space-y-6 pb-6")}>
              {messages.map((message) => (
                <Message compact={compact} key={message.id} message={message} />
              ))}
              {running && <LoadingIndicator statusItems={statusItems} tools={tools} />}
            </div>
          )}
        </div>
      </div>
      <div
        className={cn(
          "mx-auto w-full",
          compact ? "max-w-none px-3 pb-3" : "max-w-3xl px-4 pb-4 sm:px-6 sm:pb-6",
        )}
      >
        {children}
      </div>
    </div>
  );
}

function ThreadWelcome({ compact = false, scope }: { compact?: boolean; scope: ScopeState }) {
  const inBusiness = !!scope.business;
  return (
    <div className={cn("flex flex-1 items-center justify-center text-center", compact ? "py-5" : "py-10")}>
      <div>
        <h2 className={cn("font-medium text-zinc-100", compact ? "text-sm" : "text-xl")}>
          What should Takyon work on?
        </h2>
        <p className={cn("mt-2 text-zinc-500", compact ? "text-xs" : "text-sm")}>
          {inBusiness
            ? `Operating inside business:${scope.business}.`
            : "Global scope. Create a business or choose one above."}
        </p>
      </div>
    </div>
  );
}

function Composer({
  canAct,
  compact = false,
  inputRef,
  isRunning,
  onChange,
  onKeyDown,
  onSlashApply,
  onSubmit,
  setSlashIndex,
  slashIndex,
  slashItems,
  value,
}: {
  canAct: boolean;
  compact?: boolean;
  inputRef: RefObject<HTMLTextAreaElement | null>;
  isRunning: boolean;
  onChange: (value: string) => void;
  onKeyDown: (event: KeyboardEvent<HTMLTextAreaElement>) => void;
  onSlashApply: (item: SlashCompletionItem) => void;
  onSubmit: (event: FormEvent) => void;
  setSlashIndex: (value: number) => void;
  slashIndex: number;
  slashItems: SlashCompletionItem[];
  value: string;
}) {
  const hasInput = !!value.trim();

  return (
    <form className="relative pt-2" onSubmit={onSubmit}>
      {slashItems.length > 0 && (
        <SlashPalette
          activeIndex={slashIndex}
          items={slashItems}
          onApply={onSlashApply}
          onHover={setSlashIndex}
        />
      )}
      <div
        className={cn(
          "flex items-end gap-2 border border-zinc-800 bg-zinc-950 px-3 py-2 shadow-[0_0_0_1px_rgba(255,255,255,0.02)] transition-colors focus-within:border-zinc-600",
          compact ? "rounded-2xl" : "rounded-3xl",
        )}
      >
        <textarea
          ref={inputRef}
          aria-label="Message input"
          autoFocus
          className={cn(
            "flex-1 resize-none bg-transparent py-2 leading-6 text-zinc-100 outline-none placeholder:text-zinc-600",
            compact ? "max-h-28 min-h-9 text-xs" : "max-h-36 min-h-10 text-sm",
          )}
          onChange={(event) => onChange(event.target.value)}
          onKeyDown={onKeyDown}
          placeholder={isRunning ? "Add an interjection..." : "Ask Takyon anything or type /"}
          rows={1}
          value={value}
        />
        <button
          aria-label={isRunning && !hasInput ? "Stop generating" : "Send message"}
          className={cn(
            "flex h-9 w-9 shrink-0 items-center justify-center rounded-full transition-colors",
            canAct
              ? "bg-zinc-100 text-black hover:bg-white"
              : "bg-zinc-800 text-zinc-500",
          )}
          disabled={!canAct}
          type="submit"
        >
          {isRunning && !hasInput ? (
            <Square className="h-3.5 w-3.5 fill-current" />
          ) : (
            <ArrowUp className="h-4 w-4" />
          )}
        </button>
      </div>
    </form>
  );
}

function SlashPalette({
  activeIndex,
  items,
  onApply,
  onHover,
}: {
  activeIndex: number;
  items: SlashCompletionItem[];
  onApply: (item: SlashCompletionItem) => void;
  onHover: (index: number) => void;
}) {
  return (
    <div className="absolute inset-x-0 bottom-full z-20 mb-2 overflow-hidden rounded-2xl border border-zinc-800 bg-zinc-950/98 p-1 shadow-2xl shadow-black/70 backdrop-blur">
      <div className="max-h-72 overflow-y-auto">
        {items.map((item, index) => (
          <button
            className={cn(
              "flex w-full items-start gap-3 rounded-xl px-3 py-2 text-left transition-colors",
              index === activeIndex ? "bg-zinc-800 text-zinc-50" : "text-zinc-400 hover:bg-zinc-900 hover:text-zinc-100",
            )}
            key={`${item.text}-${index}`}
            onClick={() => onApply(item)}
            onMouseEnter={() => onHover(index)}
            type="button"
          >
            <span className="w-28 shrink-0 font-mono text-sm text-zinc-100">
              {item.display || item.text}
            </span>
            <span className="min-w-0 flex-1">
              <span className="block truncate text-xs text-zinc-500">
                {item.description || item.meta || "Takyon command"}
              </span>
              {item.meta && item.description && (
                <span className="mt-0.5 block truncate text-[0.68rem] uppercase tracking-[0.12em] text-zinc-600">
                  {item.meta}
                </span>
              )}
            </span>
          </button>
        ))}
      </div>
    </div>
  );
}

function Message({ compact = false, message }: { compact?: boolean; message: ChatMessage }) {
  const isUser = message.role === "user";
  const isSystem = message.role === "system";

  if (isSystem) {
    return (
      <div className="mx-auto w-full max-w-3xl whitespace-pre-wrap rounded-xl border border-zinc-800 bg-zinc-950 px-3 py-2 text-left text-xs leading-5 text-zinc-400">
        {message.content}
      </div>
    );
  }

  return (
    <div className={cn("flex", isUser ? "justify-end" : "justify-start")}>
      <div
        className={cn(
          "break-words leading-6",
          compact ? "max-w-[92%] text-xs" : "max-w-[86%] text-sm",
          isUser
            ? "whitespace-pre-wrap rounded-3xl bg-zinc-800 px-4 py-2.5 text-zinc-50"
            : "w-full max-w-none text-zinc-100",
        )}
      >
        {isUser ? (
          message.content
        ) : (
          <div className="[&_.text-foreground]:text-zinc-100 [&_a]:text-zinc-100 [&_code]:rounded [&_code]:bg-zinc-900 [&_code]:text-zinc-100 [&_pre]:rounded-xl [&_pre]:border-zinc-800 [&_pre]:bg-zinc-950">
            <Markdown
              content={message.content}
              streaming={message.status === "streaming"}
            />
          </div>
        )}
        {message.status === "interrupted" && (
          <div className="mt-2 text-xs text-zinc-500">Interrupted</div>
        )}
        {message.status === "error" && (
          <div className="mt-2 text-xs text-red-400">Error</div>
        )}
      </div>
    </div>
  );
}

function LoadingIndicator({
  statusItems = [],
  tools = [],
}: {
  statusItems?: string[];
  tools?: ToolEntry[];
}) {
  const activeTool = tools.slice().reverse().find((tool) => tool.status === "running");
  const latest = activeTool?.preview || activeTool?.context || activeTool?.name || statusItems[0] || "CEO is working";
  const stages = activeTool
    ? ["Reading", "Editing", "Checking", "Saving"]
    : ["Researching", "Planning", "Writing", "Recording"];
  return (
    <div className="rounded-2xl border border-zinc-900 bg-zinc-950 px-3 py-3">
      <div className="flex items-center justify-between gap-3">
        <div className="flex min-w-0 items-center gap-2">
          <span className="h-2 w-2 shrink-0 animate-pulse rounded-full bg-sky-300" />
          <div className="min-w-0 truncate text-sm text-zinc-200">{latest}</div>
        </div>
        <div className="flex shrink-0 items-center gap-1">
          <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-zinc-500 [animation-delay:-0.3s]" />
          <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-zinc-500 [animation-delay:-0.15s]" />
          <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-zinc-500" />
        </div>
      </div>
      <div className="mt-3 grid grid-cols-2 gap-1.5 sm:grid-cols-4">
        {stages.map((stage, index) => (
          <div
            className={cn(
              "rounded-lg border px-2 py-1.5 text-center text-[0.68rem]",
              index === 0
                ? "border-sky-300/30 bg-sky-300/10 text-sky-100"
                : "border-zinc-900 bg-black text-zinc-600",
            )}
            key={stage}
          >
            {stage}
          </div>
        ))}
      </div>
    </div>
  );
}

function ErrorBanner({ message }: { message: string }) {
  const display = friendlyError(message);
  if (!display) return null;
  return (
    <div className="mb-4 flex items-start gap-2 rounded-xl border border-amber-300/25 bg-amber-300/5 px-3 py-2 text-sm text-amber-100">
      <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />
      <span className="min-w-0 whitespace-pre-wrap">{display}</span>
    </div>
  );
}

function IntercomPanel({
  children,
  onClose,
  scope,
  sessionId,
  showClose,
}: {
  children: ReactNode;
  onClose: () => void;
  scope: ScopeState;
  sessionId: string | null;
  showClose: boolean;
}) {
  return (
    <div className="flex min-h-0 w-full flex-col bg-black text-zinc-100">
      <header className="flex h-14 shrink-0 items-center justify-between gap-3 border-b border-zinc-900 px-4">
        <div className="min-w-0">
          <div className="text-sm font-medium">CEO intercom</div>
          <div className="mt-0.5 truncate text-xs text-zinc-600">
            {scopeDetail(scope)}
            {sessionId ? ` · session ${sessionId}` : ""}
          </div>
        </div>
        {showClose && (
          <IconButton label="Close CEO intercom" onClick={onClose}>
            <X className="h-4 w-4" />
          </IconButton>
        )}
      </header>
      {children}
    </div>
  );
}

function CompanyWorkspace({
  deliverables,
  historicalOutputs,
  onReadFile,
  onResolveMedia,
  onResolveSitePreview,
  scope,
  statusItems,
  tools,
}: {
  deliverables: Deliverable[];
  historicalOutputs: Deliverable[];
  onReadFile: (path: string) => Promise<BusinessFileReadResponse>;
  onResolveMedia: (path: string) => Promise<BusinessMediaResponse>;
  onResolveSitePreview: (path?: string) => Promise<BusinessSitePreviewResponse>;
  scope: ScopeState;
  statusItems: string[];
  tools: ToolEntry[];
}) {
  const outputs = useMemo(
    () => mergeOutputs(deliverables, historicalOutputs),
    [deliverables, historicalOutputs],
  );

  return (
    <div className="flex min-h-0 flex-1 flex-col bg-[#050505]">
      <div className="flex shrink-0 items-center justify-between gap-3 border-b border-zinc-900 px-4 py-3">
        <div className="min-w-0">
          <div className="flex items-center gap-2 text-xs uppercase tracking-[0.16em] text-zinc-500">
            <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-emerald-400" />
            Company
          </div>
          <div className="mt-1 truncate text-lg font-semibold text-zinc-100">
            {scope.current?.name || scope.business}
          </div>
        </div>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto">
        <CompanyOverview
          onReadFile={onReadFile}
          onResolveMedia={onResolveMedia}
          onResolveSitePreview={onResolveSitePreview}
          outputs={outputs}
          scope={scope}
          statusItems={statusItems}
          tools={tools}
        />
      </div>
    </div>
  );
}

function CompanyOverview({
  onReadFile,
  onResolveMedia,
  onResolveSitePreview,
  outputs,
  scope,
  statusItems,
  tools,
}: {
  onReadFile: (path: string) => Promise<BusinessFileReadResponse>;
  onResolveMedia: (path: string) => Promise<BusinessMediaResponse>;
  onResolveSitePreview: (path?: string) => Promise<BusinessSitePreviewResponse>;
  outputs: Deliverable[];
  scope: ScopeState;
  statusItems: string[];
  tools: ToolEntry[];
}) {
  const overview = scope.overview || {};
  const product = overview.product || {};
  const artifacts = overview.artifacts || {};
  const website = artifacts.website || {};
  const outreach = artifacts.outreach || {};
  const creativeAssets = artifacts.creative_assets || {};
  const posts = overview.posts || [];
  const cron = overview.cron || [];
  const jobs = overview.jobs || [];
  const tasks = overview.tasks || [];
  const ceoLoop = overview.ceo_loop;
  const research = overview.research || {};
  const activeCron = cron.filter((job) => job.enabled !== false);
  const nextWake = activeCron.find((job) => job.next_run)?.next_run;
  const recentOutputs = outputs.slice(0, 4);
  const visibleJobs = jobs.filter((job) => job.kind || job.status).slice(0, 4);
  const previewPath = website.source_path || product.source_path || "product/site";
  const publicSiteUrl = website.public_url || product.public_url || "";
  const activeTool = tools.slice().reverse().find((tool) => tool.status === "running");
  const hasProductSurface = Boolean(
    website.path ||
    product.source_path ||
    product.design_brief_path ||
    product.filesystem_index ||
    product.publish_status ||
    product.verification_status,
  );
  const outputDocs = recentOutputs
    .filter((item) => item.path)
    .map((item) => ({
      detail: item.detail,
      label: item.title || compactPath(item.path),
      path: item.path || "",
      status: item.kind === "receipt" ? "Receipt" : item.kind === "video" ? "Video" : item.kind === "image" ? "Image" : "File",
    }));
  const researchDocs = uniqueDocs([
    docTile(research.latest_path, "Latest research", "Research"),
    docTile(research.strategy_path, "Strategy", "Research"),
    docTile(research.icp_path, "ICP", "Research"),
    docTile(research.channels_path, "Channels", "Research"),
    ...outputDocs.filter((doc) => /^(brain|research)\//.test(doc.path)),
  ]);
  const productDocs = uniqueDocs([
    docTile(website.path, "Website", "Product"),
    docTile(product.design_brief_path, "Design brief", "Product"),
    docTile(product.filesystem_index, "App index", "Runtime"),
    hasProductSurface ? docTile("app/surface.md", "Surface", "Runtime") : null,
    docTile(product.verification_receipt, "Verification", "Receipt"),
    docTile(product.publish_receipt_path || website.publish_receipt_path, "Publish receipt", "Receipt"),
    ...outputDocs.filter((doc) => /^(app|product)\//.test(doc.path)),
  ]);
  const growthDocs = uniqueDocs([
    docTile(outreach.path, "Outreach", "Growth"),
    docTile(outreach.receipt, "Outreach receipt", "Receipt"),
    docTile(creativeAssets.path, "Creative asset", "Growth"),
    docTile(creativeAssets.receipt, "Creative receipt", "Receipt"),
    ...posts.map((post) => docTile(post.artifact_path || post.conversation_file, post.title || "Post", post.status)),
    ...outputDocs.filter((doc) => /^(campaigns|distribution|outreach)\//.test(doc.path)),
  ]);
  const productStatus = publicSiteUrl
    ? "Live"
    : website.path
      ? "Preview ready"
      : product.publish_blocker
        ? "Needs attention"
        : "Not built";
  const workItems = tasks.length > 0 ? tasks.slice(0, 5) : visibleJobs.slice(0, 5);
  const latestActivity = [
    ...(activeTool ? [{ label: naturalToolLabel(activeTool), detail: toolDetail(activeTool), status: humanizeStatus(activeTool.status), tone: activeTool.status }] : []),
    ...tools
      .slice()
      .reverse()
      .filter((tool) => tool.id !== activeTool?.id)
      .slice(0, 3)
      .map((tool) => ({
        label: naturalToolLabel(tool),
        detail: toolDetail(tool),
        status: humanizeStatus(tool.status),
        tone: tool.status,
      })),
    ...statusItems.slice(0, 2).map((item) => ({
      label: "Live update",
      detail: item,
      status: "Working",
      tone: "active",
    })),
  ].slice(0, 5);
  const taskRows = [
    ...(ceoLoop
      ? [{
          detail: ceoLoop.next_action || ceoLoop.detail,
          id: "ceo-loop",
          label: ceoLoop.headline || "CEO",
          status: humanizeStatus(ceoLoop.status),
          tone: ceoLoop.status,
        }]
      : []),
    ...workItems.map((item, index) => ({
      detail: taskDetail(item),
      id: item.id || `${taskLabel(item)}-${index}`,
      label: taskLabel(item),
      status: humanizeStatus(item.status),
      tone: item.tone || item.status,
    })),
  ].slice(0, 6);
  const scheduleRows = activeCron.slice(0, 3).map((job, index) => ({
    detail: job.next_run ? `Next ${readableDate(job.next_run)}` : job.schedule || "",
    id: job.id || `${job.name || "schedule"}-${index}`,
    label: humanLabel(job.name?.replace(/^takyon-ceo:/, "")) || "CEO check",
    status: humanizeStatus(job.state || "scheduled"),
    tone: job.state || "waiting",
  }));
  const [viewer, setViewer] = useState<{
    content?: string;
    error?: string;
    loading?: boolean;
    media?: BusinessMediaResponse;
    path: string;
    title: string;
    truncated?: boolean;
  } | null>(null);

  useEffect(() => {
    setViewer(null);
  }, [scope.business]);

  const openDocument = useCallback(
    (doc: { label?: string; path?: string }) => {
      const path = (doc.path || "").trim();
      if (!path) return;
      const title = doc.label || compactPath(path);
      setViewer({ loading: true, path, title });
      if (mediaKindForPath(path)) {
        void onResolveMedia(path)
          .then((media) => setViewer({ loading: false, media, path: media.path || path, title }))
          .catch((err) => setViewer({
            error: friendlyError(err instanceof Error ? err.message : String(err)),
            loading: false,
            path,
            title,
          }));
        return;
      }
      void onReadFile(path)
        .then((res) => setViewer({
          content: res.content || "",
          loading: false,
          path: res.path || path,
          title,
          truncated: Boolean(res.truncated),
        }))
        .catch((err) => setViewer({
          error: friendlyError(err instanceof Error ? err.message : String(err)),
          loading: false,
          path,
          title,
        }));
    },
    [onReadFile, onResolveMedia],
  );
  const showAside = Boolean(viewer || latestActivity.length > 0);

  return (
    <div
      className={cn(
        "mx-auto grid w-full max-w-7xl gap-3 p-4",
        showAside && "xl:grid-cols-[minmax(0,1fr)_minmax(320px,420px)]",
      )}
    >
      <div className="grid content-start gap-3">
        <section className="grid gap-3 md:grid-cols-2">
          <SourceCard
            docs={researchDocs}
            empty="No research file yet."
            icon={<Search className="h-4 w-4" />}
            label="Research"
            onOpenDoc={openDocument}
            status={humanizeStatus(research.status)}
            tone={research.status === "visible" ? "done" : "waiting"}
          />
          <SourceCard
            action={
              <div className="flex flex-wrap gap-2">
                {publicSiteUrl && (
                  <PanelActionButton icon={<ExternalLink className="h-3.5 w-3.5" />} onClick={() => window.open(publicSiteUrl, "_blank", "noreferrer")}>
                    Open site
                  </PanelActionButton>
                )}
                {website.path && <OpenSitePreviewButton onResolveSitePreview={onResolveSitePreview} path={previewPath} />}
              </div>
            }
            docs={productDocs}
            empty="No product file yet."
            icon={<Globe2 className="h-4 w-4" />}
            label="Product"
            onOpenDoc={openDocument}
            status={productStatus}
            tone={product.publish_blocker ? "blocked" : publicSiteUrl || website.path ? "done" : "waiting"}
          />
          <SourceCard
            docs={growthDocs}
            empty={posts.length ? `${formatCount(posts.length)} posts` : "No growth file yet."}
            icon={<Sparkles className="h-4 w-4" />}
            label="Growth"
            onOpenDoc={openDocument}
            status={creativeAssets.path ? humanizeArtifactStatus(creativeAssets.status) : outreach.path ? humanizeArtifactStatus(outreach.status) : "Waiting"}
            tone={creativeAssets.path || outreach.path ? "done" : "waiting"}
          />
          <SourceCard
            empty={nextWake ? `Next ${readableDate(nextWake)}` : "No check scheduled."}
            icon={<Clock3 className="h-4 w-4" />}
            label="Schedule"
            status={humanizeStatus(overview.wake_health?.status || (activeCron.length ? "watching" : "quiet"))}
            tone={overview.wake_health?.status}
          >
            {scheduleRows.length > 0 && (
              <div className="grid gap-1.5">
                {scheduleRows.map((item) => (
                  <TaskRow
                    detail={item.detail}
                    key={item.id}
                    label={item.label}
                    status={item.status}
                    tone={item.tone}
                  />
                ))}
              </div>
            )}
          </SourceCard>
        </section>

        {taskRows.length > 0 && (
          <section>
            <div className="mb-2 flex items-center gap-2 text-xs font-medium uppercase tracking-[0.14em] text-zinc-600">
              <ListChecks className="h-4 w-4" />
              Tasks
            </div>
            <div className="grid gap-2 md:grid-cols-2">
              {taskRows.map((item) => (
                <TaskRow
                  detail={item.detail}
                  key={item.id}
                  label={item.label}
                  status={item.status}
                  tone={item.tone}
                />
              ))}
            </div>
          </section>
        )}
      </div>

      {showAside && (
        <div className="grid content-start gap-3">
          {viewer && (
            <InlineDocumentViewer
              onClose={() => setViewer(null)}
              viewer={viewer}
            />
          )}

          {latestActivity.length > 0 && (
            <section>
              <div className="mb-2 flex items-center gap-2 text-xs font-medium uppercase tracking-[0.14em] text-zinc-600">
                <Activity className="h-4 w-4" />
                Activity
              </div>
              <div className="grid gap-2">
                {latestActivity.map((item, index) => (
                  <TaskRow
                    detail={item.detail}
                    key={`${item.label}-${index}`}
                    label={item.label}
                    status={item.status}
                    tone={item.tone}
                  />
                ))}
              </div>
            </section>
          )}
        </div>
      )}
    </div>
  );
}

function SourceCard({
  action,
  children,
  docs = [],
  empty,
  icon,
  label,
  onOpenDoc,
  status,
  tone,
}: {
  action?: ReactNode;
  children?: ReactNode;
  docs?: SourceDocTile[];
  empty?: string;
  icon: ReactNode;
  label: string;
  onOpenDoc?: (doc: SourceDocTile) => void;
  status: string;
  tone?: string;
}) {
  return (
    <div className="rounded-xl border border-zinc-900 bg-zinc-950 px-3 py-3">
      <div className="flex items-start justify-between gap-3">
        <div className="flex min-w-0 items-start gap-2">
          <span className="mt-0.5 text-zinc-600">{icon}</span>
          <div className="min-w-0">
            <div className="text-sm font-medium text-zinc-100">{label}</div>
          </div>
        </div>
        <span className={cn("shrink-0 rounded-full border px-2 py-0.5 text-[0.65rem]", toneClasses(tone || status))}>
          {status}
        </span>
      </div>
      {docs.length > 0 ? (
        <div className="mt-3 grid grid-cols-2 gap-2">
          {docs.slice(0, 4).map((doc) => (
            <DocumentTileButton doc={doc} key={doc.path} onOpenDoc={onOpenDoc} />
          ))}
        </div>
      ) : empty ? (
        <div className="mt-3 rounded-lg border border-dashed border-zinc-900 px-3 py-3 text-xs text-zinc-600">
          {empty}
        </div>
      ) : null}
      {children && <div className="mt-3">{children}</div>}
      {action && <div className="mt-3 flex flex-wrap gap-2">{action}</div>}
    </div>
  );
}

function DocumentTileButton({
  doc,
  onOpenDoc,
}: {
  doc: SourceDocTile;
  onOpenDoc?: (doc: SourceDocTile) => void;
}) {
  const mediaKind = mediaKindForPath(doc.path);
  return (
    <button
      className="flex min-h-24 min-w-0 flex-col justify-between rounded-xl border border-zinc-900 bg-black px-3 py-2.5 text-left transition-colors hover:border-zinc-700 hover:bg-zinc-900"
      onClick={() => onOpenDoc?.(doc)}
      title={doc.path}
      type="button"
    >
      <span className="flex items-start justify-between gap-2">
        <span className="line-clamp-2 text-sm font-medium leading-5 text-zinc-100">
          {doc.label}
        </span>
        {mediaKind === "video" ? (
          <Play className="h-3.5 w-3.5 shrink-0 text-zinc-600" />
        ) : mediaKind === "image" ? (
          <ExternalLink className="h-3.5 w-3.5 shrink-0 text-zinc-600" />
        ) : (
          <FileText className="h-3.5 w-3.5 shrink-0 text-zinc-600" />
        )}
      </span>
      <span className="mt-2 min-w-0">
        <span className="block truncate font-mono text-[0.68rem] text-zinc-600">
          {compactPath(doc.path)}
        </span>
        {doc.status && (
          <span className="mt-1 inline-flex rounded-full border border-zinc-800 px-1.5 py-0.5 text-[0.62rem] text-zinc-500">
            {humanizeStatus(doc.status)}
          </span>
        )}
      </span>
    </button>
  );
}

function InlineDocumentViewer({
  onClose,
  viewer,
}: {
  onClose: () => void;
  viewer: {
    content?: string;
    error?: string;
    loading?: boolean;
    media?: BusinessMediaResponse;
    path: string;
    title: string;
    truncated?: boolean;
  };
}) {
  const isMarkdown = /\.md$/i.test(viewer.path);
  return (
    <section className="overflow-hidden rounded-xl border border-zinc-900 bg-zinc-950">
      <div className="flex items-start justify-between gap-3 border-b border-zinc-900 px-3 py-2.5">
        <div className="min-w-0">
          <div className="truncate text-sm font-medium text-zinc-100">{viewer.title}</div>
          <div className="mt-0.5 truncate font-mono text-[0.68rem] text-zinc-600">{viewer.path}</div>
        </div>
        <IconButton label="Close document" onClick={onClose}>
          <X className="h-4 w-4" />
        </IconButton>
      </div>
      {viewer.loading ? (
        <div className="p-3">
          <EmptyPanelLine text="Opening..." />
        </div>
      ) : viewer.error ? (
        <div className="p-3">
          <EmptyPanelLine text={viewer.error} />
        </div>
      ) : viewer.media ? (
        <div className="p-3">
          <MediaPreview media={viewer.media} title={viewer.title} />
        </div>
      ) : isMarkdown ? (
        <div className="max-h-[60vh] overflow-auto px-3 py-3 text-sm leading-6 text-zinc-300 [&_.text-foreground]:text-zinc-100 [&_a]:text-zinc-100 [&_code]:rounded [&_code]:bg-black [&_code]:text-zinc-100 [&_pre]:rounded-xl [&_pre]:border-zinc-800 [&_pre]:bg-black">
          <Markdown content={viewer.content || ""} />
        </div>
      ) : (
        <pre className="max-h-[60vh] overflow-auto whitespace-pre-wrap p-3 font-mono text-[0.75rem] leading-5 text-zinc-300">
          {viewer.content || ""}
        </pre>
      )}
      {viewer.truncated && (
        <div className="border-t border-zinc-900 px-3 py-2 text-xs text-amber-200">
          Preview truncated.
        </div>
      )}
    </section>
  );
}

function OpenSitePreviewButton({
  onResolveSitePreview,
  path,
}: {
  onResolveSitePreview: (path?: string) => Promise<BusinessSitePreviewResponse>;
  path?: string;
}) {
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  const openPreview = useCallback(() => {
    const popup = window.open("about:blank", "_blank");
    setLoading(true);
    setError("");
    void onResolveSitePreview(path)
      .then((res) => {
        if (!res.url) throw new Error("No preview URL returned.");
        if (popup) {
          popup.opener = null;
          popup.location.href = res.url;
        } else {
          const link = document.createElement("a");
          link.href = res.url;
          link.target = "_blank";
          link.rel = "noreferrer";
          link.click();
        }
      })
      .catch((err) => {
        if (popup) popup.close();
        setError(err instanceof Error ? err.message : String(err));
      })
      .finally(() => setLoading(false));
  }, [onResolveSitePreview, path]);

  return (
    <span className="inline-flex flex-col gap-1">
      <PanelActionButton
        icon={<ExternalLink className="h-3.5 w-3.5" />}
        onClick={openPreview}
      >
        {loading ? "Opening..." : "Preview"}
      </PanelActionButton>
      {error && <span className="text-xs text-red-400">{error}</span>}
    </span>
  );
}

function ScopeSwitcher({
  disabled,
  onSelect,
  scope,
}: {
  disabled: boolean;
  onSelect: (business: string) => Promise<void>;
  scope: ScopeState;
}) {
  const [open, setOpen] = useState(false);
  const currentLabel = scopeName(scope);
  const businesses = scope.businesses;

  const choose = (business: string) => {
    setOpen(false);
    void onSelect(business);
  };

  return (
    <div className="relative min-w-0">
      <button
        className={cn(
          "flex max-w-[42vw] items-center gap-2 rounded-full border border-zinc-800 bg-zinc-950 px-3 py-1.5 text-left text-xs text-zinc-200 transition-colors hover:border-zinc-700 hover:bg-zinc-900 sm:max-w-[280px]",
          disabled && "cursor-not-allowed opacity-50",
        )}
        disabled={disabled}
        onClick={() => setOpen((value) => !value)}
        title={scopeDetail(scope)}
        type="button"
      >
        {scope.business ? (
          <Building2 className="h-3.5 w-3.5 shrink-0 text-zinc-500" />
        ) : (
          <Globe2 className="h-3.5 w-3.5 shrink-0 text-zinc-500" />
        )}
        <span className="min-w-0 truncate">{currentLabel}</span>
        <ChevronDown className="h-3.5 w-3.5 shrink-0 text-zinc-600" />
      </button>

      {open && (
        <div className="absolute left-0 top-full z-30 mt-2 w-[min(86vw,340px)] overflow-hidden rounded-2xl border border-zinc-800 bg-zinc-950 p-1 shadow-2xl shadow-black/70">
          <ScopeOption
            active={!scope.business}
            detail="account root"
            icon={<Globe2 className="h-4 w-4" />}
            label="Global"
            onClick={() => choose("")}
          />
          {businesses.length > 0 && (
            <div className="my-1 h-px bg-zinc-900" />
          )}
          <div className="max-h-72 overflow-y-auto">
            {businesses.map((item) => {
              const slug = item.slug || "";
              if (!slug) return null;
              return (
                <ScopeOption
                  active={scope.business === slug}
                  detail={businessModeLabel(item)}
                  icon={<Building2 className="h-4 w-4" />}
                  key={slug}
                  label={item.name || slug}
                  onClick={() => choose(slug)}
                  suffix={slug}
                />
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}

function ScopeOption({
  active,
  detail,
  icon,
  label,
  onClick,
  suffix,
}: {
  active: boolean;
  detail: string;
  icon: ReactNode;
  label: string;
  onClick: () => void;
  suffix?: string;
}) {
  return (
    <button
      className={cn(
        "flex w-full items-center gap-3 rounded-xl px-3 py-2 text-left transition-colors",
        active ? "bg-zinc-800 text-zinc-50" : "text-zinc-400 hover:bg-zinc-900 hover:text-zinc-100",
      )}
      onClick={onClick}
      type="button"
    >
      <span className="text-zinc-500">{icon}</span>
      <span className="min-w-0 flex-1">
        <span className="block truncate text-sm">{label}</span>
        <span className="mt-0.5 block truncate text-xs text-zinc-600">
          {suffix ? `business:${suffix} · ${detail}` : detail}
        </span>
      </span>
      {active && <Check className="h-4 w-4 shrink-0 text-zinc-300" />}
    </button>
  );
}

function BusinessSnapshot({
  onCommand,
  onResolveSitePreview,
  scope,
}: {
  onCommand: (line: string) => void;
  onResolveSitePreview: (path?: string) => Promise<BusinessSitePreviewResponse>;
  scope: ScopeState;
}) {
  const [previewError, setPreviewError] = useState("");
  const [previewLoading, setPreviewLoading] = useState(false);
  const overview = scope.overview || {};
  const metrics = overview.metrics || {};
  const product = overview.product || {};
  const budget = overview.budget || {};
  const cron = overview.cron || [];
  const jobs = overview.jobs || [];
  const artifacts = overview.artifacts || {};
  const website = artifacts.website || {};
  const previewPath = website.source_path || product.source_path || "product/site";
  const openSitePreview = useCallback(() => {
    const popup = window.open("about:blank", "_blank");
    setPreviewLoading(true);
    setPreviewError("");
    void onResolveSitePreview(previewPath)
      .then((res) => {
        if (!res.url) throw new Error("No preview URL returned.");
        if (popup) {
          popup.opener = null;
          popup.location.href = res.url;
        } else {
          const link = document.createElement("a");
          link.href = res.url;
          link.target = "_blank";
          link.rel = "noreferrer";
          link.click();
        }
      })
      .catch((err) => {
        if (popup) popup.close();
        setPreviewError(err instanceof Error ? err.message : String(err));
      })
      .finally(() => setPreviewLoading(false));
  }, [onResolveSitePreview, previewPath]);

  if (!scope.business) {
    return (
      <PanelSection icon={<Globe2 className="h-4 w-4" />} title="Home">
        <div className="rounded-xl border border-zinc-800 bg-zinc-950 px-3 py-3">
          <div className="flex items-center gap-2 text-sm font-medium text-zinc-100">
            <Sparkles className="h-4 w-4 text-zinc-500" />
            Global account
          </div>
          <p className="mt-2 text-xs leading-5 text-zinc-500">
            Choose a business from the header, type a switch request in chat, or use a
            slash command. Business work stays separated by scope.
          </p>
          <div className="mt-3 grid grid-cols-2 gap-2">
            <SnapshotMetric
              icon={<Briefcase className="h-3.5 w-3.5" />}
              label="Businesses"
              value={formatCount(scope.businesses.length)}
            />
            <SnapshotMetric
              icon={<Command className="h-3.5 w-3.5" />}
              label="Commands"
              value="/"
              detail="type to browse"
            />
          </div>
          <div className="mt-3 flex flex-wrap gap-2">
            <PanelActionButton icon={<Gauge className="h-3.5 w-3.5" />} onClick={() => onCommand("/status")}>
              Status
            </PanelActionButton>
          </div>
        </div>

        {scope.businesses.length > 0 && (
          <div className="space-y-2">
            {scope.businesses.slice(0, 5).map((item) => (
              <div
                className="rounded-xl border border-zinc-900 bg-zinc-950 px-3 py-2"
                key={item.slug || item.name}
              >
                <div className="flex items-center justify-between gap-2">
                  <div className="min-w-0">
                    <div className="truncate text-sm font-medium text-zinc-100">
                      {item.name || item.slug}
                    </div>
                    <div className="mt-0.5 truncate text-xs text-zinc-600">
                      business:{item.slug} · {businessModeLabel(item)}
                    </div>
                  </div>
                  <ChevronRight className="h-4 w-4 shrink-0 text-zinc-700" />
                </div>
              </div>
            ))}
          </div>
        )}
      </PanelSection>
    );
  }

  const productStatus = product.status || "missing";
  const hasSurface = product.source_path || product.design_brief_path || product.filesystem_index;
  const openMessages =
    asNumber(metrics.unresolved_inbound) ||
    asNumber(overview.conversations?.unresolved_messages);
  const activeCron = cron.filter((job) => job.enabled !== false);
  const nextWake = activeCron.find((job) => job.next_run)?.next_run;
  const visibleJobs = jobs
    .filter((job) => job.status || job.kind)
    .slice(0, 4);
  const outreach = artifacts.outreach || {};
  const creativeAssets = artifacts.creative_assets || {};
  const creativeAssetsDir = creativeAssets.path
    ? creativeAssets.path.split("/").slice(0, -1).join("/") || "."
    : ".";

  return (
    <PanelSection icon={<Briefcase className="h-4 w-4" />} title="Business">
      <div className="rounded-xl border border-zinc-800 bg-zinc-950 px-3 py-3">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <div className="truncate text-sm font-semibold text-zinc-100">
              {scope.current?.name || `business:${scope.business}`}
            </div>
            <div className="mt-0.5 truncate text-xs text-zinc-600">
              business:{scope.business}
            </div>
          </div>
          <span className="shrink-0 rounded-full border border-zinc-800 px-2 py-0.5 text-[0.65rem] uppercase text-zinc-500">
            {overview.mode || scope.current?.mode || "scoped"}
          </span>
        </div>
        {(overview.goal || scope.current?.goal) && (
          <p className="mt-3 line-clamp-3 text-xs leading-5 text-zinc-500">
            {overview.goal || scope.current?.goal}
          </p>
        )}
      </div>

      <div className="grid grid-cols-2 gap-2">
        <SnapshotMetric
          icon={<Users className="h-3.5 w-3.5" />}
          label="Customers"
          value={formatCount(metrics.users)}
          detail={`${formatCount(metrics.paid_customers)} paid`}
        />
        <SnapshotMetric
          icon={<DollarSign className="h-3.5 w-3.5" />}
          label="Revenue"
          value={formatCents(metrics.revenue_cents)}
          detail={`${formatCents(metrics.mrr_cents)} MRR`}
        />
        <SnapshotMetric
          icon={<MessageCircle className="h-3.5 w-3.5" />}
          label="Needs reply"
          value={formatCount(openMessages)}
          detail={`${formatCount(metrics.usage_events)} app events`}
        />
        <SnapshotMetric
          icon={<Wallet className="h-3.5 w-3.5" />}
          label="App budget"
          value={formatMicrousd(budget.app_remaining_microusd)}
          detail={`${budget.app_status || "budget"} remaining`}
        />
      </div>

      <PreviewCard
        icon={<Gauge className="h-4 w-4" />}
        title="Product surface"
        value={productStatus}
        detail={
          hasSurface
            ? product.source_path || product.design_brief_path || product.filesystem_index
            : "No product surface contract recorded yet."
        }
      >
        <div className="flex flex-wrap gap-2">
          <PanelActionButton
            icon={<FileText className="h-3.5 w-3.5" />}
            onClick={() => onCommand("/read app/surface.md")}
          >
            Surface
          </PanelActionButton>
          <PanelActionButton
            icon={<Folder className="h-3.5 w-3.5" />}
            onClick={() => onCommand(product.source_path ? `/files ${product.source_path}` : "/files app")}
          >
            Files
          </PanelActionButton>
        </div>
        {(product.verification_status || product.routes_count !== undefined) && (
          <div className="mt-2 text-xs leading-5 text-zinc-600">
            {product.verification_status
              ? `Verification: ${product.verification_status}`
              : `${product.routes_count || 0} routes recorded`}
          </div>
        )}
      </PreviewCard>

      <PreviewCard
        icon={<Globe2 className="h-4 w-4" />}
        title="Website"
        value={
          website.deploy_status === "pending"
            ? "Deploy pending"
            : humanizeArtifactStatus(website.status)
        }
        detail={website.path || "No website source visible yet."}
      >
        <div className="flex flex-wrap gap-2">
          {website.path && (
            <PanelActionButton
              icon={<ExternalLink className="h-3.5 w-3.5" />}
              onClick={openSitePreview}
            >
              {previewLoading ? "Opening..." : "Preview"}
            </PanelActionButton>
          )}
          {website.path && (
            <PanelActionButton
              icon={<FileText className="h-3.5 w-3.5" />}
              onClick={() => onCommand(`/read ${website.path}`)}
            >
              Index
            </PanelActionButton>
          )}
          <PanelActionButton
            icon={<Folder className="h-3.5 w-3.5" />}
            onClick={() => onCommand(`/files ${website.source_path || "product/site"}`)}
          >
            Source
          </PanelActionButton>
        </div>
        {previewError && <div className="mt-2 text-xs leading-5 text-red-400">{previewError}</div>}
      </PreviewCard>

      <PreviewCard
        icon={<MessageCircle className="h-4 w-4" />}
        title="Outreach"
        value={humanizeArtifactStatus(outreach.status)}
        detail={
          outreach.path ||
          (outreach.status === "draft_only"
            ? "Draft exists, but no local publish receipt."
            : "No local outreach publication visible yet.")
        }
      >
        <div className="flex flex-wrap gap-2">
          {outreach.path && (
            <PanelActionButton
              icon={<FileText className="h-3.5 w-3.5" />}
              onClick={() => onCommand(`/read ${outreach.path}`)}
            >
              {outreach.status === "draft_only" ? "Draft" : "Post"}
            </PanelActionButton>
          )}
          {outreach.receipt && (
            <PanelActionButton
              icon={<CheckCircle2 className="h-3.5 w-3.5" />}
              onClick={() => onCommand(`/read ${outreach.receipt}`)}
            >
              Receipt
            </PanelActionButton>
          )}
        </div>
      </PreviewCard>

      <PreviewCard
        icon={<Play className="h-4 w-4" />}
        title="Creative assets"
        value={humanizeArtifactStatus(creativeAssets.status)}
        detail={creativeAssets.path || "No generated image/video asset visible yet."}
      >
        <div className="flex flex-wrap gap-2">
          {creativeAssets.path && (
            <PanelActionButton
              icon={<Play className="h-3.5 w-3.5" />}
              onClick={() => onCommand(`/files ${creativeAssetsDir}`)}
            >
              Files
            </PanelActionButton>
          )}
          {creativeAssets.receipt && (
            <PanelActionButton
              icon={<CheckCircle2 className="h-3.5 w-3.5" />}
              onClick={() => onCommand(`/read ${creativeAssets.receipt}`)}
            >
              Receipt
            </PanelActionButton>
          )}
        </div>
      </PreviewCard>

      <PreviewCard
        icon={<Clock3 className="h-4 w-4" />}
        title="Scheduled checks"
        value={activeCron.length ? `${activeCron.length} active` : "None active"}
        detail={nextWake ? `Next check ${readableDate(nextWake)}` : "No scheduled CEO check is visible."}
      >
        <div className="flex flex-wrap gap-2">
          <PanelActionButton icon={<Clock3 className="h-3.5 w-3.5" />} onClick={() => onCommand("/cron list")}>
            List
          </PanelActionButton>
          <PanelActionButton icon={<Play className="h-3.5 w-3.5" />} onClick={() => onCommand("/wake")}>
            Wake now
          </PanelActionButton>
        </div>
      </PreviewCard>

      {visibleJobs.length > 0 && (
        <PreviewCard
          icon={<Activity className="h-4 w-4" />}
          title="Recent work"
          value={`${visibleJobs.length} jobs`}
          detail={`${formatCount(metrics.queued_jobs)} queued`}
        >
          <div className="space-y-1.5">
            {visibleJobs.map((job, index) => (
              <div
                className="rounded-lg border border-zinc-900 bg-black/30 px-2.5 py-1.5"
                key={job.id || `${job.kind}-${index}`}
              >
                <div className="flex items-center justify-between gap-2">
                  <div className="min-w-0 truncate text-xs text-zinc-400">
                    {job.kind || "job"}
                  </div>
                  <span className="shrink-0 text-[0.65rem] text-zinc-600">
                    {job.status || "recorded"}
                  </span>
                </div>
              </div>
            ))}
          </div>
        </PreviewCard>
      )}
    </PanelSection>
  );
}

function BusinessFileBrowser({
  initialFiles,
  onCommand,
  onListFiles,
  onReadFile,
  onResolveMedia,
}: {
  initialFiles: BusinessOverviewFile[];
  onCommand: (line: string) => void;
  onListFiles: (path: string) => Promise<BusinessOverviewFile[]>;
  onReadFile?: (path: string) => Promise<BusinessFileReadResponse>;
  onResolveMedia: (path: string) => Promise<BusinessMediaResponse>;
}) {
  const [path, setPath] = useState(".");
  const [files, setFiles] = useState(initialFiles);
  const [preview, setPreview] = useState<BusinessMediaResponse | null>(null);
  const [textPreview, setTextPreview] = useState<BusinessFileReadResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  const openPath = useCallback(
    async (nextPath: string) => {
      setLoading(true);
      setError("");
      try {
        const nextFiles = await onListFiles(nextPath || ".");
        setPath(nextPath || ".");
        setFiles(nextFiles);
        setPreview(null);
        setTextPreview(null);
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err));
      } finally {
        setLoading(false);
      }
    },
    [onListFiles],
  );

  const parentPath = useMemo(() => {
    if (!path || path === ".") return "";
    const parts = path.split("/").filter(Boolean);
    parts.pop();
    return parts.length ? parts.join("/") : ".";
  }, [path]);

  return (
    <div className="space-y-2">
      <div className="flex min-w-0 items-center gap-2 text-xs text-zinc-600">
        <span className="truncate">/{path === "." ? "" : path}</span>
        {parentPath && (
          <button
            className="ml-auto rounded-md border border-zinc-900 px-2 py-0.5 text-[0.68rem] text-zinc-500 transition-colors hover:border-zinc-800 hover:text-zinc-200"
            onClick={() => void openPath(parentPath)}
            type="button"
          >
            Up
          </button>
        )}
      </div>
      {error && <EmptyPanelLine text={error} />}
      {loading ? (
        <EmptyPanelLine text="Loading files..." />
      ) : files.length === 0 ? (
        <EmptyPanelLine text="No business files visible here." />
      ) : (
        <div className="grid gap-1.5">
          {files.slice(0, 10).map((item) => {
            const itemPath = item.path || ".";
            const isDir = item.type === "dir";
            const mediaKind = mediaKindForPath(itemPath);
            return (
              <button
                className="flex min-w-0 items-center gap-2 rounded-lg border border-zinc-900 bg-black/30 px-2.5 py-1.5 text-left text-xs text-zinc-400 transition-colors hover:border-zinc-800 hover:bg-zinc-900 hover:text-zinc-100"
                key={`${item.type}-${itemPath}`}
                onClick={() => {
                  if (isDir) {
                    void openPath(itemPath);
                    return;
                  }
                  if (mediaKind) {
                    setTextPreview(null);
                    void onResolveMedia(itemPath).then(setPreview).catch((err) => {
                      setError(err instanceof Error ? err.message : String(err));
                    });
                    return;
                  }
                  if (onReadFile) {
                    setLoading(true);
                    setError("");
                    setPreview(null);
                    void onReadFile(itemPath)
                      .then(setTextPreview)
                      .catch((err) => setError(err instanceof Error ? err.message : String(err)))
                      .finally(() => setLoading(false));
                    return;
                  }
                  onCommand(`/read ${itemPath}`);
                }}
                title={itemPath}
                type="button"
              >
                {isDir ? (
                  <Folder className="h-3.5 w-3.5 shrink-0 text-zinc-600" />
                ) : mediaKind === "video" ? (
                  <Play className="h-3.5 w-3.5 shrink-0 text-zinc-600" />
                ) : mediaKind === "image" ? (
                  <ExternalLink className="h-3.5 w-3.5 shrink-0 text-zinc-600" />
                ) : (
                  <FileText className="h-3.5 w-3.5 shrink-0 text-zinc-600" />
                )}
                <span className="min-w-0 truncate">{compactPath(itemPath)}</span>
                <span className="ml-auto shrink-0 text-[0.65rem] text-zinc-700">
                  {isDir ? "dir" : mediaKind || "file"}
                </span>
              </button>
            );
          })}
        </div>
      )}
      {preview?.url && preview.path && (
        <MediaPreview media={preview} title={preview.path.split("/").pop() || preview.path} />
      )}
      {textPreview?.path && (
        <div className="mt-2 overflow-hidden rounded-lg border border-zinc-900 bg-black">
          <div className="flex items-center justify-between gap-2 border-b border-zinc-900 px-3 py-2">
            <div className="min-w-0">
              <div className="truncate text-xs font-medium text-zinc-100">
                {textPreview.path.split("/").pop() || textPreview.path}
              </div>
              <div className="truncate font-mono text-[0.65rem] text-zinc-600">{textPreview.path}</div>
            </div>
            {textPreview.truncated && (
              <span className="shrink-0 rounded-full bg-amber-300/10 px-2 py-0.5 text-[0.65rem] text-amber-100">
                truncated
              </span>
            )}
          </div>
          <pre className="max-h-72 overflow-auto whitespace-pre-wrap p-3 font-mono text-[0.72rem] leading-5 text-zinc-300">
            {textPreview.content || ""}
          </pre>
        </div>
      )}
    </div>
  );
}

function SnapshotMetric({
  detail,
  icon,
  label,
  value,
}: {
  detail?: string;
  icon: ReactNode;
  label: string;
  value: string;
}) {
  return (
    <div className="min-w-0 rounded-xl border border-zinc-900 bg-zinc-950 px-3 py-2">
      <div className="flex items-center gap-1.5 text-[0.68rem] uppercase text-zinc-600">
        {icon}
        <span className="truncate">{label}</span>
      </div>
      <div className="mt-1 truncate text-sm font-semibold text-zinc-100">{value}</div>
      {detail && <div className="mt-0.5 truncate text-[0.68rem] text-zinc-600">{detail}</div>}
    </div>
  );
}

function PreviewCard({
  children,
  detail,
  icon,
  title,
  value,
}: {
  children?: ReactNode;
  detail?: string;
  icon: ReactNode;
  title: string;
  value: string;
}) {
  return (
    <div className="rounded-xl border border-zinc-900 bg-zinc-950 px-3 py-2.5">
      <div className="flex items-start justify-between gap-3">
        <div className="flex min-w-0 items-start gap-2">
          <span className="mt-0.5 text-zinc-600">{icon}</span>
          <div className="min-w-0">
            <div className="text-sm font-medium text-zinc-100">{title}</div>
            {detail && (
              <div className="mt-0.5 truncate text-xs leading-5 text-zinc-600">
                {detail}
              </div>
            )}
          </div>
        </div>
        <span className="shrink-0 rounded-full bg-zinc-900 px-2 py-0.5 text-[0.65rem] text-zinc-500">
          {value}
        </span>
      </div>
      {children && <div className="mt-3">{children}</div>}
    </div>
  );
}

function PanelActionButton({
  children,
  icon,
  onClick,
}: {
  children: ReactNode;
  icon: ReactNode;
  onClick: () => void;
}) {
  return (
    <button
      className="inline-flex h-7 items-center gap-1.5 rounded-lg border border-zinc-800 bg-black px-2.5 text-xs text-zinc-300 transition-colors hover:border-zinc-700 hover:bg-zinc-900 hover:text-zinc-50"
      onClick={onClick}
      type="button"
    >
      {icon}
      <span>{children}</span>
    </button>
  );
}

function DeliverablesPanel({
  cwd,
  deliverables,
  historicalOutputs,
  onCommand,
  onListFiles,
  onResolveMedia,
  onResolveSitePreview,
  onClose,
  scope,
  sessionId,
  showClose,
  statusItems,
  tools,
}: {
  cwd?: string;
  deliverables: Deliverable[];
  historicalOutputs: Deliverable[];
  onCommand: (line: string) => void;
  onListFiles: (path: string) => Promise<BusinessOverviewFile[]>;
  onResolveMedia: (path: string) => Promise<BusinessMediaResponse>;
  onResolveSitePreview: (path?: string) => Promise<BusinessSitePreviewResponse>;
  onClose: () => void;
  scope: ScopeState;
  sessionId: string | null;
  showClose: boolean;
  statusItems: string[];
  tools: ToolEntry[];
}) {
  const [activeTab, setActiveTab] = useState<PanelTab>("home");
  const effectiveTab: PanelTab = scope.business ? activeTab : "home";
  const outputs = useMemo(
    () => mergeOutputs(deliverables, historicalOutputs),
    [deliverables, historicalOutputs],
  );
  useEffect(() => {
    if (!scope.business && activeTab !== "home") setActiveTab("home");
  }, [activeTab, scope.business]);
  const panelTitle = scope.business
    ? effectiveTab === "home"
      ? "Business home"
      : `${PANEL_TABS.find((tab) => tab.id === effectiveTab)?.label || "Business"}`
    : "Takyon home";

  return (
    <div className="flex min-h-0 w-full flex-col bg-black text-zinc-100">
      <header className="flex h-14 shrink-0 items-center justify-between gap-3 border-b border-zinc-900 px-4">
        <div className="min-w-0">
          <div className="text-sm font-medium">{panelTitle}</div>
          <div className="mt-0.5 truncate text-xs text-zinc-600">
            {scopeDetail(scope)}
            {sessionId ? ` · session ${sessionId}` : ""}
            {cwd ? ` · ${cwd}` : ""}
          </div>
        </div>
        {showClose && (
          <IconButton label="Close side panel" onClick={onClose}>
            <X className="h-4 w-4" />
          </IconButton>
        )}
      </header>

      {scope.business && <PanelTabs active={effectiveTab} onChange={setActiveTab} />}

      <div className="min-h-0 flex-1 overflow-y-auto px-4 py-4">
        {effectiveTab === "home" && (
          <>
            <BusinessSnapshot
              onCommand={onCommand}
              onResolveSitePreview={onResolveSitePreview}
              scope={scope}
            />
          </>
        )}

        {effectiveTab === "next" && (
          <NextPanel onCommand={onCommand} scope={scope} />
        )}

        {effectiveTab === "files" && (
          <FilesPanel
            onCommand={onCommand}
            onListFiles={onListFiles}
            onResolveMedia={onResolveMedia}
            scope={scope}
          />
        )}

        {effectiveTab === "outputs" && (
          <OutputsPanel onCommand={onCommand} onResolveMedia={onResolveMedia} outputs={outputs} />
        )}

        {effectiveTab === "dev" && (
          <DevPanel
            cwd={cwd}
            scope={scope}
            sessionId={sessionId}
            statusItems={statusItems}
            tools={tools}
          />
        )}
      </div>
    </div>
  );
}

function PanelTabs({
  active,
  onChange,
}: {
  active: PanelTab;
  onChange: (tab: PanelTab) => void;
}) {
  return (
    <div className="shrink-0 border-b border-zinc-900 px-3 py-2">
      <div className="grid grid-cols-5 gap-1 rounded-xl border border-zinc-900 bg-zinc-950 p-1">
        {PANEL_TABS.map((tab) => (
          <button
            className={cn(
              "h-8 rounded-lg px-1 text-xs font-medium transition-colors",
              active === tab.id
                ? "bg-zinc-800 text-zinc-50"
                : "text-zinc-500 hover:bg-zinc-900 hover:text-zinc-200",
            )}
            key={tab.id}
            onClick={() => onChange(tab.id)}
            type="button"
          >
            {tab.label}
          </button>
        ))}
      </div>
    </div>
  );
}

function NextPanel({
  onCommand,
  scope,
}: {
  onCommand: (line: string) => void;
  scope: ScopeState;
}) {
  return <TaskBoard onCommand={onCommand} scope={scope} />;
}

function FilesPanel({
  onCommand,
  onListFiles,
  onReadFile,
  onResolveMedia,
  scope,
}: {
  onCommand: (line: string) => void;
  onListFiles: (path: string) => Promise<BusinessOverviewFile[]>;
  onReadFile?: (path: string) => Promise<BusinessFileReadResponse>;
  onResolveMedia: (path: string) => Promise<BusinessMediaResponse>;
  scope: ScopeState;
}) {
  if (!scope.business) {
    return (
      <PanelSection icon={<Folder className="h-4 w-4" />} title="Files">
        <EmptyPanelLine text="Choose a business to browse its filesystem." />
      </PanelSection>
    );
  }

  return (
    <PanelSection icon={<Folder className="h-4 w-4" />} title="Files">
      <div className="rounded-xl border border-zinc-900 bg-zinc-950 px-3 py-2">
        <div className="mb-3 text-xs text-zinc-600">
          business:{scope.business} workspace
        </div>
        <BusinessFileBrowser
          key={scope.business}
          initialFiles={scope.overview?.files || []}
          onCommand={onCommand}
          onListFiles={onListFiles}
          onReadFile={onReadFile}
          onResolveMedia={onResolveMedia}
        />
      </div>
    </PanelSection>
  );
}

function OutputsPanel({
  onCommand,
  onResolveMedia,
  outputs,
}: {
  onCommand: (line: string) => void;
  onResolveMedia: (path: string) => Promise<BusinessMediaResponse>;
  outputs: Deliverable[];
}) {
  return (
    <PanelSection icon={<FileText className="h-4 w-4" />} title="Outputs">
      {outputs.length === 0 ? (
        <EmptyPanelLine text="No historical or current-session outputs yet." />
      ) : (
        outputs.map((item) => (
          <DeliverableItem
            item={item}
            key={item.id}
            onCommand={onCommand}
            onResolveMedia={onResolveMedia}
          />
        ))
      )}
    </PanelSection>
  );
}

function TaskBoard({
  onCommand,
  scope,
}: {
  onCommand: (line: string) => void;
  scope: ScopeState;
}) {
  const overview = scope.overview || {};
  const tasks = overview.tasks || [];
  const fallbackJobs = (overview.jobs || []).filter((job) => job.kind || job.status);
  const displayTasks: Array<BusinessOverviewTask | BusinessOverviewJob> =
    tasks.length > 0 ? tasks : fallbackJobs;
  const ceoLoop = overview.ceo_loop;
  const statusCards = overview.status_cards || [];
  const research = overview.research || {};
  const wakeHealth = overview.wake_health;

  return (
    <div className="space-y-6">
      {ceoLoop && (
        <PanelSection icon={<Sparkles className="h-4 w-4" />} title="CEO Loop">
          <TaskRow
            detail={ceoLoop.detail || ceoLoop.next_action}
            label={ceoLoop.headline || "CEO is choosing the next move"}
            status={humanizeStatus(ceoLoop.status)}
            tone={ceoLoop.status}
          />
          {ceoLoop.next_action && (
            <TaskRow
              detail={ceoLoop.next_action}
              label="Next move"
              status="Visible"
              tone="active"
            />
          )}
        </PanelSection>
      )}

      <PanelSection icon={<Gauge className="h-4 w-4" />} title="Company State">
        <div className="grid gap-2 md:grid-cols-2 xl:grid-cols-4">
          {statusCards.length === 0 ? (
            <EmptyPanelLine text="No status cards recorded yet." />
          ) : (
            statusCards.map((card, index) => (
              <TaskRow
                detail={card.detail}
                key={`${card.label}-${index}`}
                label={card.label || "Status"}
                status={humanizeStatus(card.status)}
                tone={card.tone || card.status}
              />
            ))
          )}
        </div>
      </PanelSection>

      <PanelSection icon={<Search className="h-4 w-4" />} title="Research">
        <div className="grid gap-2 md:grid-cols-2">
          <TaskRow
            detail={research.latest_path || research.strategy_path || research.icp_path || "ICP, channel, and strategy evidence should appear here first."}
            label="ICP and strategy"
            status={humanizeStatus(research.status)}
            tone={research.status === "visible" ? "done" : "waiting"}
          />
          <TaskRow
            detail={research.channels_path || "Channel evidence and reachable-user strategy"}
            label="Channels"
            status={research.channels_path ? "Ready" : "Waiting"}
            tone={research.channels_path ? "done" : "waiting"}
          />
        </div>
      </PanelSection>

      <PanelSection icon={<ListChecks className="h-4 w-4" />} title="Tasks">
        {displayTasks.length === 0 ? (
          <EmptyPanelLine text="No task records are visible yet." />
        ) : (
          <div className="grid gap-2 lg:grid-cols-2">
            {displayTasks.slice(0, 12).map((task, index) => (
              <TaskRow
                detail={taskDetail(task)}
                key={task.id || `${taskLabel(task)}-${index}`}
                label={taskLabel(task)}
                status={humanizeStatus(task.status)}
                tone={task.tone || task.status}
              />
            ))}
          </div>
        )}
      </PanelSection>

      <PanelSection icon={<Clock3 className="h-4 w-4" />} title="Scheduled checks">
        <TaskRow
          detail={wakeHealth?.detail || "No scheduled CEO check is visible."}
          label={wakeHealth?.headline || "Scheduled CEO check"}
          status={humanizeStatus(wakeHealth?.status)}
          tone={wakeHealth?.status}
        />
        <div className="flex flex-wrap gap-2 pt-1">
          <PanelActionButton icon={<Clock3 className="h-3.5 w-3.5" />} onClick={() => onCommand("/cron list")}>
            List
          </PanelActionButton>
          <PanelActionButton icon={<Play className="h-3.5 w-3.5" />} onClick={() => onCommand("/wake")}>
            Wake now
          </PanelActionButton>
        </div>
      </PanelSection>
    </div>
  );
}

function DevPanel({
  cwd,
  scope,
  sessionId,
  statusItems,
  tools,
}: {
  cwd?: string;
  scope: ScopeState;
  sessionId: string | null;
  statusItems: string[];
  tools: ToolEntry[];
}) {
  const builderItems = tools
    .filter((tool) => /tool|file|write|patch|shell|exec|agent|build|verify|npm|python|git|code/i.test(`${tool.name} ${tool.context || ""} ${tool.summary || ""}`))
    .slice()
    .reverse()
    .slice(0, 5);
  const visibleTasks = (scope.overview?.tasks || []).filter((task) =>
    /agent|job/.test(task.source || ""),
  );

  return (
    <div className="space-y-6">
      <PanelSection icon={<Code2 className="h-4 w-4" />} title="Build activity">
        <div className="space-y-2">
          {builderItems.length === 0 && visibleTasks.length === 0 ? (
            <TaskRow
              detail={scope.overview?.product?.source_path || "No builder activity recorded in this session."}
              label="Builder feed"
              status="Quiet"
              tone="neutral"
            />
          ) : (
            <>
              {builderItems.map((tool) => (
                <TaskRow
                  detail={toolDetail(tool)}
                  key={tool.id}
                  label={naturalToolLabel(tool)}
                  status={humanizeStatus(tool.status)}
                  tone={tool.status}
                />
              ))}
              {visibleTasks.slice(0, 3).map((task, index) => (
                <TaskRow
                  detail={taskDetail(task)}
                  key={task.id || `${task.source}-${index}`}
                  label={taskLabel(task)}
                  status={humanizeStatus(task.status)}
                  tone={task.tone || task.status}
                />
              ))}
            </>
          )}
        </div>
      </PanelSection>

      <PanelSection icon={<Command className="h-4 w-4" />} title="Build">
        <TaskRow
          detail={scopeDetail(scope)}
          label="Dashboard build"
          status="ui"
        />
        <TaskRow
          detail={cwd || "cwd unavailable"}
          label={sessionId ? `session ${sessionId}` : "session pending"}
          status="runtime"
        />
      </PanelSection>

      <PanelSection icon={<CheckCircle2 className="h-4 w-4" />} title="Actions">
        {tools.length === 0 ? (
          <EmptyPanelLine text="No tool calls yet." />
        ) : (
          tools
            .slice()
            .reverse()
            .map((tool) => <ToolActivityItem key={tool.id} tool={tool} />)
        )}
      </PanelSection>

      <PanelSection icon={<Clock3 className="h-4 w-4" />} title="Pulse">
        {statusItems.length === 0 ? (
          <EmptyPanelLine text="No pulse yet." />
        ) : (
          statusItems.map((item, index) => (
            <div
              className="rounded-xl border border-zinc-900 bg-zinc-950 px-3 py-2 text-xs leading-5 text-zinc-400"
              key={`${item}-${index}`}
            >
              {item}
            </div>
          ))
        )}
      </PanelSection>
    </div>
  );
}

function TaskRow({
  detail,
  label,
  status,
  tone,
}: {
  detail?: string;
  label: string;
  status: string;
  tone?: string;
}) {
  return (
    <div className="rounded-xl border border-zinc-900 bg-zinc-950 px-3 py-2">
      <div className="flex items-center justify-between gap-2">
        <div className="min-w-0 truncate text-sm text-zinc-200">{label}</div>
        <span className={cn("shrink-0 rounded-full border px-2 py-0.5 text-[0.65rem]", toneClasses(tone || status))}>
          {status}
        </span>
      </div>
      {detail && (
        <div className="mt-0.5 truncate text-xs leading-5 text-zinc-600">
          {detail}
        </div>
      )}
    </div>
  );
}

function PanelSection({
  children,
  className,
  icon,
  title,
}: {
  children: ReactNode;
  className?: string;
  icon: ReactNode;
  title: string;
}) {
  return (
    <section className={className}>
      <div className="mb-2 flex items-center gap-2 text-xs font-medium uppercase tracking-[0.14em] text-zinc-600">
        {icon}
        {title}
      </div>
      <div className="space-y-2">{children}</div>
    </section>
  );
}

function DeliverableItem({
  item,
  onCommand,
  onResolveMedia,
}: {
  item: Deliverable;
  onCommand: (line: string) => void;
  onResolveMedia: (path: string) => Promise<BusinessMediaResponse>;
}) {
  const [media, setMedia] = useState<BusinessMediaResponse | null>(null);
  const [error, setError] = useState("");
  const mediaKind = mediaKindForPath(item.path) || (item.kind === "image" || item.kind === "video" ? item.kind : undefined);

  return (
    <div className="rounded-xl border border-zinc-900 bg-zinc-950 px-3 py-2">
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="truncate text-sm font-medium text-zinc-100">{item.title}</div>
          {item.path && (
            <div className="mt-0.5 truncate font-mono text-[0.7rem] text-zinc-600">
              {item.path}
            </div>
          )}
        </div>
        <span className="shrink-0 text-[0.65rem] text-zinc-600">
          {prettyTime(item.at)}
        </span>
      </div>
      <div className="mt-1 line-clamp-2 text-xs leading-5 text-zinc-500">
        {item.detail}
      </div>
      {item.path && (
        <div className="mt-2 flex flex-wrap gap-2">
          {mediaKind ? (
            <PanelActionButton
              icon={mediaKind === "video" ? <Play className="h-3.5 w-3.5" /> : <ExternalLink className="h-3.5 w-3.5" />}
              onClick={() => {
                setError("");
                void onResolveMedia(item.path || "")
                  .then(setMedia)
                  .catch((err) => setError(err instanceof Error ? err.message : String(err)));
              }}
            >
              Preview
            </PanelActionButton>
          ) : (
            <PanelActionButton
              icon={<FileText className="h-3.5 w-3.5" />}
              onClick={() => onCommand(`/read ${item.path || ""}`)}
            >
              Read
            </PanelActionButton>
          )}
          <PanelActionButton
            icon={<FileText className="h-3.5 w-3.5" />}
            onClick={() => window.navigator.clipboard?.writeText(item.path || "")}
          >
            Copy path
          </PanelActionButton>
        </div>
      )}
      {error && <div className="mt-2 text-xs text-red-300">{error}</div>}
      {media?.url && <MediaPreview media={media} title={item.title} />}
    </div>
  );
}

function MediaPreview({
  media,
  title,
}: {
  media: BusinessMediaResponse;
  title: string;
}) {
  const kind = media.media_type?.startsWith("video/") ? "video" : "image";
  return (
    <div className="mt-2 overflow-hidden rounded-lg border border-zinc-900 bg-black">
      {kind === "video" ? (
        <video className="max-h-52 w-full bg-black" controls src={media.url} title={title} />
      ) : (
        <img alt={title} className="max-h-52 w-full object-contain" src={media.url} />
      )}
      <div className="flex items-center justify-between gap-2 border-t border-zinc-900 px-2 py-1 text-[0.65rem] text-zinc-600">
        <span className="min-w-0 truncate">{media.path}</span>
        {media.url && (
          <a className="shrink-0 text-zinc-400 hover:text-zinc-100" href={media.url} rel="noreferrer" target="_blank">
            Open
          </a>
        )}
      </div>
    </div>
  );
}

function ToolActivityItem({ tool }: { tool: ToolEntry }) {
  return (
    <div className="rounded-xl border border-zinc-900 bg-zinc-950 px-3 py-2">
      <div className="flex items-center justify-between gap-2">
        <div className="min-w-0 truncate text-sm text-zinc-200">{naturalToolLabel(tool)}</div>
        <span
          className={cn(
            "shrink-0 rounded-full px-2 py-0.5 text-[0.65rem]",
            tool.status === "running" && "bg-amber-400/10 text-amber-200",
            tool.status === "done" && "bg-emerald-400/10 text-emerald-200",
            tool.status === "error" && "bg-red-400/10 text-red-200",
          )}
        >
          {humanizeStatus(tool.status)}
        </span>
      </div>
      {(tool.error || tool.summary || tool.preview || tool.context) && (
        <div className="mt-1 line-clamp-3 text-xs leading-5 text-zinc-500">
          {toolDetail(tool)}
        </div>
      )}
    </div>
  );
}

function EmptyPanelLine({ text }: { text: string }) {
  return (
    <div className="rounded-xl border border-dashed border-zinc-900 px-3 py-5 text-center text-xs text-zinc-600">
      {text}
    </div>
  );
}

function IconButton({
  children,
  className,
  label,
  onClick,
}: {
  children: ReactNode;
  className?: string;
  label: string;
  onClick: () => void;
}) {
  return (
    <button
      aria-label={label}
      className={cn(
        "flex h-8 w-8 items-center justify-center rounded-full text-zinc-500 transition-colors hover:bg-zinc-900 hover:text-zinc-100",
        className,
      )}
      onClick={onClick}
      title={label}
      type="button"
    >
      {children}
    </button>
  );
}

function HeaderLinkActionButton({
  children,
  href,
  label,
}: {
  children: ReactNode;
  href: string;
  label: string;
}) {
  return (
    <a
      aria-label={label}
      className="inline-flex h-8 items-center gap-1.5 rounded-full border border-zinc-800 px-3 text-xs text-zinc-400 transition-colors hover:border-zinc-700 hover:bg-zinc-900 hover:text-zinc-100"
      href={href}
      rel="noreferrer"
      target="_blank"
      title={label}
    >
      {children}
      <span className="hidden sm:inline">{label}</span>
    </a>
  );
}
