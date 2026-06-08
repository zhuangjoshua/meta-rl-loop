import {
  ArrowUp,
  ExternalLink,
  Lightbulb,
  PanelRight,
  Plus,
  RefreshCw,
  Square,
  User,
  X,
} from "lucide-react";
import {
  Fragment,
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
import {
  api,
  type TakyonBusinessFileReadResponse,
  type TakyonBusinessWorkspaceResponse,
  type TakyonOperatorAccountResponse,
  type TakyonOperatorBusinessSummary,
} from "@/lib/api";
import {
  GatewayClient,
  type ConnectionState,
} from "@/lib/gatewayClient";
import { displayNameFromId } from "@/lib/takyonActivity";
import { cn } from "@/lib/utils";
import { PluginSlot } from "@/plugins";

import "./chat-dashboard.css";

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
  takyon_boot?: {
    requested_business?: string;
    accepted?: boolean;
    reason?: string;
  };
  info?: SessionInfo;
}

interface GatewayHistoryMessage {
  role?: "user" | "assistant" | "system" | "tool";
  text?: string;
  name?: string;
  context?: string;
}

interface SessionResumeResponse {
  session_id: string;
  resumed?: string;
  messages?: GatewayHistoryMessage[];
  info?: SessionInfo;
}

interface SessionHistoryResponse {
  count?: number;
  messages?: GatewayHistoryMessage[];
  running?: boolean;
}

type BusinessSummary = TakyonOperatorBusinessSummary;

interface BusinessOverviewProduct {
  status?: string;
  source_path?: string;
  runtime_api_base?: string;
  publish_target?: string;
  publish_policy?: string;
  publish_status?: string;
  public_url?: string;
  published_at?: string;
  publish_receipt_path?: string;
  publish_blocker?: string;
  routes_count?: number;
  surface_status?: string;
  surface_receipt?: string;
  inventory_status?: string;
  risk_marker_count?: number;
  claim_snippet_count?: number;
  pretend_finding_count?: number;
  local_continuable_work?: string[];
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

interface BusinessOverviewWorker {
  id?: string;
  tool_name?: string;
  name?: string;
  purpose?: string;
  status?: string;
  updated_at?: string;
  latest_detail?: string;
  tone?: string;
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
  count?: number;
  outputs?: BusinessOverviewResearchOutput[];
}

interface BusinessOverviewResearchOutput {
  path?: string;
  updated_at?: number | string;
  size?: number;
  source?: string;
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
  publish_policy?: string;
  publish_status?: string;
  publish_blocker?: string;
  publish_receipt_path?: string;
  count?: number;
  published_count?: number;
  items?: BusinessArtifactItem[];
  receipts?: string[];
}

interface BusinessArtifactItem {
  status?: string;
  path?: string;
  receipt?: string;
  updated_at?: number;
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

interface RegistryDisplayEntry {
  display_name?: string;
  activity_verb?: string;
  detail_hint?: string;
  detail_keys?: string[];
  implementation_status?: string;
  category?: string;
  effect?: string;
}

interface RegistryDisplayPayload {
  version?: number | null;
  tools?: Record<string, RegistryDisplayEntry>;
  skills?: Record<string, RegistryDisplayEntry>;
  warning?: string;
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
  workers?: BusinessOverviewWorker[];
  tasks?: BusinessOverviewTask[];
  status_cards?: BusinessOverviewStatusCard[];
  ceo_loop?: BusinessOverviewCeoLoop;
  research?: BusinessOverviewResearch;
  research_outputs?: BusinessOverviewResearchOutput[];
  wake_health?: BusinessOverviewWakeHealth;
  posts?: BusinessOverviewPost[];
  artifacts?: {
    website?: BusinessArtifactSummary;
    outreach?: BusinessArtifactSummary;
    creative_assets?: BusinessArtifactSummary;
  };
  conversations?: BusinessOverviewConversations;
  registry?: RegistryDisplayPayload;
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

interface TakyonBackgroundRun {
  kind?: string;
  business?: string;
  status?: string;
  started_at?: number;
  finished_at?: number;
  detail?: string;
  job_id?: string;
}

interface TakyonDashboardWorkspaceResponse {
  business_slug?: string;
  current?: BusinessSummary;
  overview?: BusinessOverview;
  outputs?: Deliverable[];
  background_run?: TakyonBackgroundRun | null;
}

interface TakyonDashboardStateResponse extends TakyonDashboardWorkspaceResponse {
  scope?: string;
  business?: string;
  businesses?: BusinessSummary[];
  auto_switched_business?: string;
  auto_scope_warning?: string;
}

interface TakyonDashboardCreateResponse extends TakyonDashboardWorkspaceResponse {
  business_slug: string;
  business_name?: string;
  goal?: string;
  mode?: string;
  job_id?: string;
  job_kind?: string;
  job_status?: string;
  lifecycle_state?: string;
  output?: string;
  scope?: string;
  businesses?: BusinessSummary[];
}

interface BusinessMediaResponse extends ScopeState {
  path?: string;
  media_type?: string;
  size?: number;
  url?: string;
}

type BusinessFileReadResponse = TakyonBusinessFileReadResponse;

interface BusinessSitePreviewResponse {
  business_slug?: string;
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
  duration_s?: number;
}

interface Deliverable {
  id: string;
  title: string;
  detail: string;
  path?: string;
  kind: "file" | "diff" | "tool" | "deploy" | "receipt" | "report" | "image" | "video";
  at: number;
}

interface TakyonProgressState {
  business: string;
  lines: string[];
  active: boolean;
  status: ChatMessage["status"];
}

const STATE_LABEL: Record<ConnectionState, string> = {
  idle: "starting",
  connecting: "connecting",
  open: "ready",
  polling: "HTTP fallback",
  closed: "Reconnecting",
  error: "Offline, retrying",
};

const BUDGET_FORMATTER = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});

const EMPTY_SCOPE_STATE: ScopeState = {
  scope: "global",
  business: "",
  current: {},
  businesses: [],
};

const MEDIA_EXTENSIONS = "mp4|mov|webm|m4v|png|jpg|jpeg|webp|gif";
const TEXT_EXTENSIONS = "ts|tsx|js|jsx|py|md|json|css|html|yml|yaml|toml|txt|sql";
const PATH_EXTENSIONS = `${TEXT_EXTENSIONS}|${MEDIA_EXTENSIONS}`;
const VIDEO_EXTENSIONS = new Set(["mp4", "mov", "webm", "m4v"]);
const IMAGE_EXTENSIONS = new Set(["png", "jpg", "jpeg", "webp", "gif"]);
const TAKYON_PROGRESS_MAX_LINES = 8;
const WAKE_SCHEDULE_PRESETS = [
  "every 30m",
  "every 1h",
  "every 2h",
  "every 6h",
  "every 12h",
  "every 1d",
] as const;
const WAKE_SCHEDULE_PATTERN = /^every\s+(\d+)\s*([mhd])$/i;
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

function formatBudgetCents(value?: number | null): string {
  const cents = typeof value === "number" && Number.isFinite(value) ? value : 0;
  return BUDGET_FORMATTER.format(cents / 100);
}

function formatPercent(value?: number | null): string {
  if (typeof value !== "number" || !Number.isFinite(value)) return "—";
  return `${value.toFixed(1)}%`;
}

function operatorUsageRemainingPercent(
  account?: TakyonOperatorAccountResponse | null,
): number | null {
  if (!account?.available) return null;
  const percent = Number(account.allowance_percent_remaining ?? NaN);
  return Number.isFinite(percent) ? Math.max(0, percent) : null;
}

function businessCountLabel(count: number): string {
  return `${count} business${count === 1 ? "" : "es"}`;
}

function optimisticBusinessSummary(
  businesses: BusinessSummary[],
  slug: string,
): BusinessSummary {
  const existing = businesses.find(
    (item) => normalizeBusinessLookup(item.slug || item.name || "") === slug,
  );
  if (existing) {
    return {
      ...existing,
      slug: existing.slug || slug,
      name: existing.name || slug,
    };
  }
  return { slug, name: slug };
}

function makeMessage(role: ChatRole, content: string): ChatMessage {
  return { id: nextId(role), role, content: cleanText(content), status: "complete" };
}

function asText(value: unknown): string {
  return typeof value === "string" ? value : "";
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

function naturalToolLabel(tool: ToolEntry, registry?: RegistryDisplayPayload): string {
  const display = displayNameFromId(tool.name || "tool", registry, "tools");
  if (display.hasMetadata || /^business_/.test(tool.name || "")) {
    return display.label;
  }
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

function friendlyError(message?: string | null): string {
  const text = (message || "").trim();
  if (!text) return "";
  if (/No inference provider configured|OPENROUTER_API_KEY|OPENAI_API_KEY|ANTHROPIC_API_KEY/i.test(text)) {
    return "Model connection unavailable.";
  }
  return text.split(/\n/)[0].slice(0, 140);
}

function isTransientConnectionMessage(message?: string | null): boolean {
  const text = (message || "").trim();
  if (!text) return false;
  return /live stream (?:reconnecting|disconnected|unauthorized|forbidden)|websocket connection failed/i.test(
    text,
  );
}

function isBusinessScopeDeniedMessage(message?: string | null): boolean {
  const text = (message || "").trim();
  if (!text) return false;
  return /could not open business:|no businesses are visible for this account|that business is not available to this account|access denied for business:/i.test(
    text,
  );
}

function isDetachedTakyonProgressMessage(message?: string | null): boolean {
  const text = cleanText(message || "").trim();
  if (!text) return false;
  return /^(?:CEO bootstrap: |Create started for business:|Wake started for business:|Create for business:|Wake for business:)/i.test(
    text,
  );
}

function isToolActivityMessage(message?: string | null): boolean {
  const text = cleanText(message || "").trim();
  if (!text) return false;
  return /^(?:[▶✓✗])\s/.test(text) || /^Tool:/i.test(text);
}

function isEphemeralGatewayErrorMessage(message?: string | null): boolean {
  const text = cleanText(message || "").trim();
  if (!text) return false;
  return /^(?:\d{3}:|request timed out:|session busy|session not found)/i.test(text);
}

function isTakyonChatNoiseMessage(message: ChatMessage): boolean {
  if (message.role !== "system") return false;
  return (
    isTransientConnectionMessage(message.content) ||
    isBusinessScopeDeniedMessage(message.content) ||
    isDetachedTakyonProgressMessage(message.content) ||
    isToolActivityMessage(message.content) ||
    isEphemeralGatewayErrorMessage(message.content)
  );
}

function stripTakyonChatNoise(messages: ChatMessage[]): ChatMessage[] {
  let changed = false;
  const next = messages.filter((message) => {
    const keep = !isTakyonChatNoiseMessage(message);
    if (!keep) changed = true;
    return keep;
  });
  return changed ? next : messages;
}

function compactPath(path?: string): string {
  if (!path) return "";
  if (path.length <= 34) return path;
  const parts = path.split("/");
  if (parts.length <= 2) return path.slice(0, 31) + "...";
  return `${parts[0]}/.../${parts[parts.length - 1]}`;
}

function normalizeOpenableUrl(value?: string): string {
  const text = (value || "").trim();
  if (!text) return "";
  if (/^https?:\/\//i.test(text) || /^data:/i.test(text)) return text;
  if (/^[a-z0-9][a-z0-9.-]*\.[a-z]{2,}(?:\/.*)?$/i.test(text)) {
    return `https://${text}`;
  }
  return "";
}

function customerWebsiteUrl({
  product,
  website,
}: {
  business?: string;
  product?: BusinessOverviewProduct;
  website?: BusinessArtifactSummary;
}): string {
  const explicit = normalizeOpenableUrl(website?.public_url || product?.public_url);
  if (explicit) return explicit;
  return "";
}

function openUrlInNewTab(url: string): void {
  const target = normalizeOpenableUrl(url);
  if (!target) throw new Error("No URL available.");
  const opened = window.open(target, "_blank", "noopener,noreferrer");
  if (opened) return;
  const link = document.createElement("a");
  link.href = target;
  link.target = "_blank";
  link.rel = "noopener noreferrer";
  link.style.display = "none";
  document.body.appendChild(link);
  link.click();
  link.remove();
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
  item: GatewayHistoryMessage,
): ChatMessage | null {
  if (item.role === "tool") {
    return null;
  }
  if (item.role === "user" || item.role === "assistant" || item.role === "system") {
    const text = item.text?.trim();
    if (!text) return null;
    const message = makeMessage(item.role, text);
    return isTakyonChatNoiseMessage(message) ? null : message;
  }
  return null;
}

function mergePolledMessages(prev: ChatMessage[], polled: ChatMessage[]): ChatMessage[] {
  if (!polled.length) return prev;
  const hasAssistant = polled.some((message) => message.role === "assistant");
  const next = hasAssistant
    ? prev.filter((message) => !(message.role === "assistant" && message.status === "streaming"))
    : [...prev];
  const seen = new Set(next.map((message) => `${message.role}\n${message.content}`));
  for (const message of polled) {
    if (isTakyonChatNoiseMessage(message)) continue;
    const key = `${message.role}\n${message.content}`;
    if (seen.has(key)) continue;
    seen.add(key);
    next.push(message);
  }
  return next;
}

function parseTakyonProgressBusiness(text: string): string {
  const match = cleanText(text).match(/business:([a-z0-9-]+)/i);
  return match ? normalizeBusinessLookup(match[1] || "") : "";
}

function updateTakyonProgress(
  current: TakyonProgressState | null,
  rawText: string,
  fallbackBusiness: string,
): TakyonProgressState | null {
  const line = cleanText(rawText).trim();
  if (!line) return current;
  const business = parseTakyonProgressBusiness(line) || fallbackBusiness || current?.business || "";
  const terminal = line.match(/^(?:Create|Wake) for business:[a-z0-9-]+\s+(done|error)\b/i);
  const nextStatus: ChatMessage["status"] = terminal
    ? (terminal[1] || "").toLowerCase() === "done"
      ? "complete"
      : "error"
    : "streaming";
  const reset = /^Started \//.test(line) || (!!business && business !== current?.business);
  const previousLines = reset ? [] : current?.lines || [];
  const deduped =
    previousLines.length && previousLines[previousLines.length - 1] === line
      ? previousLines
      : [...previousLines, line];
  return {
    business,
    lines: deduped.slice(-TAKYON_PROGRESS_MAX_LINES),
    active: !terminal,
    status: nextStatus,
  };
}

function latestTakyonProgressLine(
  progress: TakyonProgressState | null | undefined,
  business?: string | null,
): string {
  if (!progress?.active) return "";
  if (business && normalizeBusinessLookup(progress.business || "") !== normalizeBusinessLookup(business)) {
    return "";
  }
  return (progress.lines[progress.lines.length - 1] || "").trim();
}

function syncTakyonProgressFromTask(
  current: TakyonProgressState | null,
  business: string,
  detail: string,
): TakyonProgressState {
  const normalizedBusiness = normalizeBusinessLookup(business);
  const sameBusiness =
    normalizeBusinessLookup(current?.business || "") === normalizedBusiness;
  const previousLines =
    sameBusiness && current?.active ? current.lines || [] : [];
  const deduped =
    previousLines.length && previousLines[previousLines.length - 1] === detail
      ? previousLines
      : [...previousLines, detail].slice(-TAKYON_PROGRESS_MAX_LINES);
  if (
    sameBusiness &&
    current?.active &&
    deduped.length === (current.lines || []).length &&
    deduped[deduped.length - 1] === (current.lines || [])[deduped.length - 1]
  ) {
    return current;
  }
  return {
    business,
    lines: deduped,
    active: true,
    status: "streaming",
  };
}

function recentToolLabels(tools: ToolEntry[] | undefined): string[] {
  const now = Date.now();
  const recent = (tools || [])
    .filter((tool) => {
      if (tool.status === "running") return true;
      if (!tool.completedAt) return false;
      return now - tool.completedAt <= 12_000;
    })
    .slice(-4)
    .map((tool) => naturalToolLabel(tool));
  return recent.filter((label, index) => index === 0 || recent[index - 1] !== label);
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

function connectionDot(state: ConnectionState): string {
  if (state === "open") return "bg-emerald-400";
  if (state === "connecting" || state === "idle" || state === "polling") return "bg-amber-400";
  return "bg-red-500";
}

function canUseConnection(state: ConnectionState): boolean {
  return state === "open" || state === "polling";
}

function selectWakeCron(overview?: BusinessOverview): BusinessOverviewCron | null {
  const cronJobs = Array.isArray(overview?.cron) ? overview?.cron : [];
  if (!cronJobs.length) return null;
  const canonical = cronJobs.find((job) => (job.name || "").startsWith("takyon-ceo:"));
  if (canonical) return canonical;
  const fuzzy = cronJobs.find((job) => /(?:^|[-_\s])(?:ceo|wake)(?:$|[-_\s])/i.test(job.name || ""));
  return fuzzy || cronJobs[0] || null;
}

function normalizeWakeSchedule(value: string): string | null {
  const trimmed = value.trim().toLowerCase().replace(/\s+/g, " ");
  const match = trimmed.match(WAKE_SCHEDULE_PATTERN);
  if (!match) return null;
  const amount = Number.parseInt(match[1] || "", 10);
  const unit = (match[2] || "").toLowerCase();
  if (!Number.isFinite(amount) || amount < 1) return null;
  return `every ${amount}${unit}`;
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

function normalizeWorkspaceSnapshot(
  value: Partial<TakyonDashboardWorkspaceResponse> | null | undefined,
): TakyonDashboardWorkspaceResponse | null {
  const business = normalizeBusinessLookup(value?.business_slug || "");
  if (!business) return null;
  return {
    business_slug: business,
    current:
      value?.current && typeof value.current === "object" ? value.current : {},
    overview:
      value?.overview && typeof value.overview === "object"
        ? value.overview
        : undefined,
    outputs: Array.isArray(value?.outputs) ? value.outputs : [],
    background_run:
      value?.background_run && typeof value.background_run === "object"
        ? value.background_run
        : null,
  };
}

function normalizeDashboardState(
  value: Partial<TakyonDashboardStateResponse> | null | undefined,
): ScopeState {
  return normalizeScopeState({
    scope: value?.scope,
    business:
      typeof value?.business_slug === "string"
        ? value.business_slug
        : typeof value?.business === "string"
          ? value.business
          : "",
    businesses: Array.isArray(value?.businesses) ? value.businesses : [],
    current:
      value?.current && typeof value.current === "object" ? value.current : {},
    overview:
      value?.overview && typeof value.overview === "object"
        ? value.overview
        : undefined,
    auto_switched_business:
      typeof value?.auto_switched_business === "string"
        ? value.auto_switched_business
        : undefined,
    auto_scope_warning:
      typeof value?.auto_scope_warning === "string"
        ? value.auto_scope_warning
        : undefined,
  });
}

function scopeName(scope: ScopeState): string {
  if (!scope.business) return "Global";
  const currentName = scope.current?.name || scope.current?.slug || scope.business;
  return currentName === scope.business ? `business:${scope.business}` : currentName;
}

function businessModeLabel(item: BusinessSummary): string {
  const parts = [item.state || item.status, item.mode].filter(Boolean);
  return parts.length ? parts.join("/") : "business";
}

function prettyHost(url: string): string {
  const text = (url || "").trim();
  if (!text) return "";
  return text.replace(/^https?:\/\//i, "").replace(/\/+$/, "");
}

function channelLabel(source?: string): string {
  const s = (source || "").toLowerCase().replace(/^test-/, "");
  if (!s) return "Post";
  if (s === "x" || s.startsWith("x-") || s.includes("twitter")) return "X";
  if (s.includes("reddit")) return "Reddit";
  if (s.includes("hacker")) return "HN";
  if (s.includes("linkedin")) return "LinkedIn";
  if (s.includes("forum")) return "Forum";
  if (s.includes("outreach")) return "Outreach";
  return s.charAt(0).toUpperCase() + s.slice(1);
}

function modeDotClass(item: BusinessSummary): "td-live" | "td-test" | "td-idle" {
  const mode = (item.mode || "").toLowerCase();
  if (mode === "live") return "td-live";
  return "td-idle";
}

// True when the business has a publicly reachable product (real structural
// state, never a fabricated metric).
function productIsLive(product?: BusinessOverviewProduct): boolean {
  if (!product) return false;
  const publishStatus = (product.publish_status || "").toLowerCase();
  const status = (product.status || "").toLowerCase();
  return (
    publishStatus === "published" ||
    publishStatus === "live" ||
    status === "live" ||
    status === "published" ||
    !!normalizeOpenableUrl(product.public_url)
  );
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

function businessFromLocationSearch(): string {
  if (typeof window === "undefined") return "";
  const params = new URLSearchParams(window.location.search);
  return normalizeBusinessLookup(params.get("business") || params.get("scope") || "");
}

function syncBusinessSearchParams(params: URLSearchParams, business: string): URLSearchParams {
  if (business) {
    params.set("business", business);
  } else {
    params.delete("business");
  }
  params.delete("scope");
  return params;
}

function takyonBootMessage(boot?: SessionCreateResponse["takyon_boot"] | null): string {
  const requested = normalizeBusinessLookup(boot?.requested_business || "");
  if (!requested || boot?.accepted) return "";
  const reason = (boot?.reason || "").trim();
  return reason || `Could not open business:${requested}.`;
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

function isSlashCommandPrefix(value: string): boolean {
  return value.startsWith("/") && !/\s/.test(value.slice(1));
}

function isMissingSessionError(value: unknown): boolean {
  const message =
    value instanceof Error ? value.message : typeof value === "string" ? value : "";
  return /session not found/i.test(message);
}

const WS_AUTH_RELOAD_KEY = "takyon.dashboard.wsAuthReloaded";

export default function ChatPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const resumeParam = searchParams.get("resume");
  const [version, setVersion] = useState(0);
  const gw = useMemo(() => {
    void version;
    return new GatewayClient();
  }, [version]);

  const [state, setState] = useState<ConnectionState>("idle");
  const reconnectAttemptsRef = useRef(0);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [, setInfo] = useState<SessionInfo>({});
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [tools, setTools] = useState<ToolEntry[]>([]);
  const [deliverables, setDeliverables] = useState<Deliverable[]>([]);
  const [historicalOutputs, setHistoricalOutputs] = useState<{
    business: string;
    items: Deliverable[];
  }>({ business: "", items: [] });
  const [workspaceSnapshot, setWorkspaceSnapshot] =
    useState<TakyonDashboardWorkspaceResponse | null>(null);
  const [statusItems, setStatusItems] = useState<string[]>([]);
  const [scopeState, setScopeState] = useState<ScopeState>(EMPTY_SCOPE_STATE);
  const [operatorAccount, setOperatorAccount] =
    useState<TakyonOperatorAccountResponse | null>(null);
  const [operatorBusinesses, setOperatorBusinesses] = useState<BusinessSummary[]>([]);
  const [operatorBusinessesAvailable, setOperatorBusinessesAvailable] = useState(false);
  const [operatorBusinessesLoading, setOperatorBusinessesLoading] = useState(true);
  const [operatorBusinessesReason, setOperatorBusinessesReason] = useState<string | null>(null);
  const [takyonProgress, setTakyonProgress] = useState<TakyonProgressState | null>(null);
  const [pendingBusinessSlug, setPendingBusinessSlug] = useState<string | null>(null);
  const [blockedBootBusinessSlug, setBlockedBootBusinessSlug] = useState<string | null>(null);
  const [blockedBootMessage, setBlockedBootMessage] = useState<string | null>(null);
  const [input, setInput] = useState("");
  const [slashItems, setSlashItems] = useState<SlashCompletionItem[]>([]);
  const [slashIndex, setSlashIndex] = useState(0);
  const [running, setRunning] = useState(false);
  const [cronOpen, setCronOpen] = useState(false);
  const [cronSchedule, setCronSchedule] = useState("every 6h");
  const [cronSaving, setCronSaving] = useState(false);
  const [cronError, setCronError] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(() =>
    typeof window !== "undefined" && !window.__TAKYON_SESSION_TOKEN__
      ? "Session token unavailable. Open this page through the Litebulb dashboard server."
      : null,
  );
  const [rightOpen, setRightOpen] = useState(false);

  const scrollerRef = useRef<HTMLDivElement | null>(null);
  const inputRef = useRef<HTMLTextAreaElement | null>(null);
  const connectionStateRef = useRef<ConnectionState>("idle");
  const sessionIdRef = useRef<string | null>(null);
  const scopeBusinessRef = useRef<string>("");
  const workspaceSnapshotRef = useRef<TakyonDashboardWorkspaceResponse | null>(null);
  const pendingBusinessSlugRef = useRef<string | null>(null);
  const scopeHydrationInFlightRef = useRef(false);
  const takyonRefreshTimerRef = useRef<number | null>(null);
  const sessionRecoveryInFlightRef = useRef(false);
  const sessionBootstrapRef = useRef<Promise<string | null> | null>(null);
  const requestedBusinessSlug = useMemo(
    () =>
      normalizeBusinessLookup(
        pendingBusinessSlug || businessFromLocationSearch() || scopeState.business || "",
      ),
    [pendingBusinessSlug, scopeState.business, searchParams],
  );
  const snapshotBusinessSlug = useMemo(
    () => normalizeBusinessLookup(workspaceSnapshot?.business_slug || ""),
    [workspaceSnapshot],
  );
  const activeBusinessSlug = useMemo(
    () => {
      const scopeBusiness = normalizeBusinessLookup(scopeState.business || "");
      if (scopeBusiness) return scopeBusiness;
      if (
        snapshotBusinessSlug &&
        (!requestedBusinessSlug || snapshotBusinessSlug === requestedBusinessSlug)
      ) {
        return snapshotBusinessSlug;
      }
      return "";
    },
    [requestedBusinessSlug, scopeState.business, snapshotBusinessSlug],
  );
  const businessRequestPending = !!requestedBusinessSlug && requestedBusinessSlug !== activeBusinessSlug;
  const visibleBusinesses = useMemo(() => {
    if (operatorBusinesses.length > 0) return operatorBusinesses;
    if (operatorBusinessesAvailable) return operatorBusinesses;
    return scopeState.businesses;
  }, [operatorBusinesses, operatorBusinessesAvailable, scopeState.businesses]);
  const displayScope = useMemo(() => {
    if (!activeBusinessSlug) return scopeState;
    const snapshot =
      workspaceSnapshot &&
      normalizeBusinessLookup(workspaceSnapshot.business_slug || "") === activeBusinessSlug
        ? workspaceSnapshot
        : null;
    return normalizeScopeState({
      ...scopeState,
      business: activeBusinessSlug,
      current:
        snapshot?.current && Object.keys(snapshot.current).length
          ? snapshot.current
          : scopeState.current,
      overview:
        snapshot?.overview && Object.keys(snapshot.overview).length
          ? snapshot.overview
          : scopeState.overview,
    });
  }, [activeBusinessSlug, scopeState, workspaceSnapshot]);
  const wakeCron = useMemo(() => selectWakeCron(displayScope.overview), [displayScope.overview]);

  const recoverMissingSession = useCallback(
    (
      requestedBusiness?: string,
      options?: {
        clearResume?: boolean;
      },
    ) => {
      if (sessionRecoveryInFlightRef.current) return;
      sessionRecoveryInFlightRef.current = true;
      const params = new URLSearchParams(searchParams);
      if (options?.clearResume) params.delete("resume");
      if (requestedBusiness !== undefined) {
        syncBusinessSearchParams(params, normalizeBusinessLookup(requestedBusiness));
      }
      setSearchParams(params, { replace: true });
      scopeHydrationInFlightRef.current = false;
      sessionIdRef.current = null;
      setSessionId(null);
      setRunning(false);
      setError(null);
      setPendingBusinessSlug(
        requestedBusiness === ""
          ? null
          : requestedBusiness || businessFromLocationSearch() || null,
      );
      setStatusItems((prev) => ["Refreshing dashboard session…", ...prev].slice(0, 5));
      setVersion((current) => current + 1);
    },
    [searchParams, setSearchParams],
  );

  const noteBootIssue = useCallback((business: string, message: string) => {
    const slug = normalizeBusinessLookup(business);
    const content = message.trim();
    if (!slug || !content) return;
    setBlockedBootBusinessSlug(slug);
    setBlockedBootMessage(content);
    setPendingBusinessSlug((current) => (current === slug ? null : current));
    setScopeState((prev) => {
      if (normalizeBusinessLookup(prev.business || "") !== slug) return prev;
      return normalizeScopeState({
        ...prev,
        business: "",
        current: undefined,
        overview: undefined,
        businesses: prev.businesses.filter(
          (item) => normalizeBusinessLookup(item.slug || "") !== slug,
        ),
      });
    });
  }, []);

  useEffect(() => {
    setCronOpen(false);
    setCronSaving(false);
    setCronError(null);
  }, [activeBusinessSlug]);

  const refreshOperatorAccount = useCallback(async () => {
    try {
      setOperatorAccount(await api.getTakyonOperatorAccount());
    } catch {
      setOperatorAccount((prev) => prev || { available: false, reason: "request_failed" });
    }
  }, []);

  const refreshOperatorBusinesses = useCallback(async () => {
    setOperatorBusinessesLoading(true);
    try {
      const res = await api.getTakyonOperatorBusinesses();
      const items = Array.isArray(res.businesses)
        ? res.businesses.filter((item): item is BusinessSummary => !!item && typeof item === "object")
        : [];
      setOperatorBusinessesAvailable(Boolean(res.available));
      setOperatorBusinessesReason(res.available ? null : res.reason || "read_failed");
      if (res.available) {
        setOperatorBusinesses(items);
        setScopeState((prev) =>
          normalizeScopeState({
            ...prev,
            businesses: items,
          }),
        );
      }
    } catch {
      setOperatorBusinessesAvailable(false);
      setOperatorBusinessesReason("request_failed");
    } finally {
      setOperatorBusinessesLoading(false);
    }
  }, []);

  const applyWorkspaceSnapshot = useCallback(
    (
      business: string,
      value: Partial<TakyonBusinessWorkspaceResponse> | null | undefined,
      options?: {
        syncUrl?: boolean;
      },
    ): ScopeState | null => {
      const slug = normalizeBusinessLookup(business);
      const snapshot = normalizeWorkspaceSnapshot(
        {
          business_slug: slug,
          ...(value || {}),
        } as Partial<TakyonDashboardWorkspaceResponse>,
      );
      if (!slug || !snapshot) return null;
      const fallbackCurrent = optimisticBusinessSummary(visibleBusinesses, slug);
      const nextScope = normalizeScopeState({
        scope: `business:${slug}`,
        business: slug,
        businesses: visibleBusinesses,
        current:
          snapshot.current && Object.keys(snapshot.current).length
            ? snapshot.current
            : fallbackCurrent,
        overview:
          snapshot.overview && Object.keys(snapshot.overview).length
            ? snapshot.overview
            : undefined,
      });
      setScopeState(nextScope);
      setWorkspaceSnapshot(snapshot);
      setHistoricalOutputs({
        business: slug,
        items: Array.isArray(snapshot.outputs) ? snapshot.outputs : [],
      });
      setPendingBusinessSlug(null);
      setBlockedBootBusinessSlug((current) => (current === slug ? null : current));
      setBlockedBootMessage((current) =>
        current && current.includes(`business:${slug}`) ? null : current,
      );
      if (options?.syncUrl) {
        const params = new URLSearchParams(searchParams);
        syncBusinessSearchParams(params, slug);
        setSearchParams(params, { replace: true });
      }
      return nextScope;
    },
    [
      searchParams,
      setSearchParams,
      visibleBusinesses,
    ],
  );

  useEffect(() => {
    connectionStateRef.current = state;
  }, [state]);

  useEffect(() => {
    sessionIdRef.current = sessionId;
  }, [sessionId]);

  useEffect(() => {
    scopeBusinessRef.current = activeBusinessSlug;
  }, [activeBusinessSlug]);

  useEffect(() => {
    workspaceSnapshotRef.current = workspaceSnapshot;
  }, [workspaceSnapshot]);

  useEffect(() => {
    pendingBusinessSlugRef.current = pendingBusinessSlug;
  }, [pendingBusinessSlug]);

  useEffect(() => {
    return () => {
      if (takyonRefreshTimerRef.current !== null) {
        window.clearTimeout(takyonRefreshTimerRef.current);
        takyonRefreshTimerRef.current = null;
      }
    };
  }, []);

  useEffect(() => {
    void refreshOperatorAccount();
  }, [refreshOperatorAccount]);

  useEffect(() => {
    void refreshOperatorBusinesses();
  }, [refreshOperatorBusinesses]);

  const ensureDashboardSession = useCallback(
    async (requestedBusiness?: string): Promise<string | null> => {
      if (sessionIdRef.current) return sessionIdRef.current;
      if (!canUseConnection(connectionStateRef.current)) return null;
      if (sessionBootstrapRef.current) return await sessionBootstrapRef.current;
      const bootstrap = (async () => {
        const bootBusiness = normalizeBusinessLookup(
          requestedBusiness ||
            pendingBusinessSlugRef.current ||
            businessFromLocationSearch() ||
            "",
        );
        const res = await gw.request<SessionCreateResponse>("session.create", {
          cols: 100,
          _takyon_boot_business: bootBusiness || undefined,
        });
        if (!res.session_id) return null;
        sessionIdRef.current = res.session_id;
        setSessionId(res.session_id);
        setInfo((prev) => ({ ...prev, ...res.info }));
        return res.session_id;
      })();
      sessionBootstrapRef.current = bootstrap.finally(() => {
        sessionBootstrapRef.current = null;
      });
      return await sessionBootstrapRef.current;
    },
    [gw],
  );

  const syncRequestedBusinessToSession = useCallback(
    async (
      business: string,
      options?: {
        sessionId?: string;
      },
    ): Promise<void> => {
      const slug = normalizeBusinessLookup(business);
      if (!slug || !canUseConnection(connectionStateRef.current)) return;
      let activeSessionId = options?.sessionId || sessionIdRef.current;
      if (!activeSessionId) {
        activeSessionId = await ensureDashboardSession(slug);
      }
      if (!activeSessionId || !canUseConnection(connectionStateRef.current)) return;
      try {
        const state = await gw.request<TakyonDashboardStateResponse>(
          "takyon.dashboard.state",
          {
            session_id: activeSessionId,
            business_slug: slug,
            limit: 50,
          },
          10_000,
        );
        const dashboardBusinesses = Array.isArray(state.businesses)
          ? state.businesses.filter((item): item is BusinessSummary => !!item && typeof item === "object")
          : [];
        if (dashboardBusinesses.length > 0) {
          setOperatorBusinesses(dashboardBusinesses);
          setOperatorBusinessesAvailable(true);
          setOperatorBusinessesReason(null);
          setOperatorBusinessesLoading(false);
          setScopeState((prev) =>
            normalizeScopeState({
              ...prev,
              businesses: dashboardBusinesses,
            }),
          );
        }
      } catch (err) {
        if (isMissingSessionError(err)) {
          recoverMissingSession(slug);
        }
      }
    },
    [ensureDashboardSession, gw, recoverMissingSession],
  );

  const loadDashboardState = useCallback(
    async (
      requestedBusiness?: string,
      options?: {
        sessionId?: string;
        syncUrl?: boolean;
      },
    ): Promise<ScopeState | null> => {
      const locationBusiness = businessFromLocationSearch();
      const business = normalizeBusinessLookup(
        requestedBusiness !== undefined
          ? requestedBusiness
          : pendingBusinessSlugRef.current ||
              locationBusiness ||
              scopeBusinessRef.current ||
              "",
      );
      if (business) {
        try {
          const workspace = await api.getTakyonBusinessWorkspace(business, 50);
          const nextScope = applyWorkspaceSnapshot(business, workspace, {
            syncUrl: options?.syncUrl,
          });
          void syncRequestedBusinessToSession(business, {
            sessionId: options?.sessionId,
          });
          return nextScope;
        } catch (err) {
          const message = err instanceof Error ? err.message : String(err);
          if (/^404:/i.test(message)) {
            noteBootIssue(
              business,
              `Could not open business:${business}. That business is not available to this account.`,
            );
            return null;
          }
          if (!canUseConnection(connectionStateRef.current)) {
            throw err;
          }
        }
      }
      let activeSessionId = options?.sessionId || sessionIdRef.current;
      if (!activeSessionId && canUseConnection(connectionStateRef.current)) {
        activeSessionId = await ensureDashboardSession(business || undefined);
      }
      if (!activeSessionId || !canUseConnection(connectionStateRef.current)) return null;
      const shouldRequestBusiness =
        requestedBusiness !== undefined ||
        !!normalizeBusinessLookup(pendingBusinessSlugRef.current || "") ||
        !!locationBusiness ||
        !!normalizeBusinessLookup(scopeBusinessRef.current || "");
      try {
        const state = await gw.request<TakyonDashboardStateResponse>(
          "takyon.dashboard.state",
          {
            session_id: activeSessionId,
            ...(shouldRequestBusiness
              ? { business_slug: business || "global" }
              : {}),
            limit: 50,
          },
          10_000,
        );
        const dashboardBusinesses = Array.isArray(state.businesses)
          ? state.businesses.filter((item): item is BusinessSummary => !!item && typeof item === "object")
          : [];
        if (dashboardBusinesses.length > 0) {
          setOperatorBusinesses(dashboardBusinesses);
          setOperatorBusinessesAvailable(true);
          setOperatorBusinessesReason(null);
          setOperatorBusinessesLoading(false);
        }
        const nextScope = normalizeDashboardState(state);
        const snapshot = normalizeWorkspaceSnapshot(state);
        const existingSnapshot = workspaceSnapshotRef.current;
        const existingSnapshotBusiness = normalizeBusinessLookup(
          existingSnapshot?.business_slug || "",
        );
        if (business && !nextScope.business && existingSnapshot && existingSnapshotBusiness === business) {
          if (options?.syncUrl) {
            const params = new URLSearchParams(searchParams);
            syncBusinessSearchParams(params, business);
            setSearchParams(params, { replace: true });
          }
          return normalizeScopeState({
            ...nextScope,
            scope: `business:${business}`,
            business,
            businesses: dashboardBusinesses.length > 0 ? dashboardBusinesses : visibleBusinesses,
            current: existingSnapshot.current || {},
            overview: existingSnapshot.overview,
          });
        }
        setScopeState(nextScope);
        setWorkspaceSnapshot(snapshot);
        if (nextScope.business && snapshot) {
          setHistoricalOutputs({
            business: nextScope.business,
            items: Array.isArray(snapshot.outputs) ? snapshot.outputs : [],
          });
        } else {
          setHistoricalOutputs({ business: "", items: [] });
        }
        if (nextScope.business) {
          setBlockedBootBusinessSlug((current) =>
            current === nextScope.business ? null : current,
          );
          setBlockedBootMessage((current) =>
            current && current.includes(`business:${nextScope.business}`) ? null : current,
          );
        }
        setPendingBusinessSlug(null);
        if (options?.syncUrl) {
          const params = new URLSearchParams(searchParams);
          if (nextScope.business || requestedBusiness !== undefined) {
            syncBusinessSearchParams(params, nextScope.business || "");
          }
          setSearchParams(params, { replace: true });
        }
        return nextScope;
      } catch (err) {
        const message = err instanceof Error ? err.message : String(err);
        if (isMissingSessionError(err)) {
          recoverMissingSession(
            business || scopeBusinessRef.current || businessFromLocationSearch() || undefined,
          );
          return null;
        }
        if (shouldRequestBusiness && business && isBusinessScopeDeniedMessage(message)) {
          noteBootIssue(business, message);
          return null;
        }
        throw err;
      }
    },
    [
      applyWorkspaceSnapshot,
      ensureDashboardSession,
      gw,
      noteBootIssue,
      recoverMissingSession,
      searchParams,
      setSearchParams,
      syncRequestedBusinessToSession,
    ],
  );

  const refreshBusinessSurfaces = useCallback(
    async (requestedBusiness?: string) => {
      try {
        await loadDashboardState(requestedBusiness);
      } catch {
        /* best effort refresh */
      }
    },
    [loadDashboardState],
  );

  const scheduleTakyonRefresh = useCallback(() => {
    if (takyonRefreshTimerRef.current !== null) return;
    takyonRefreshTimerRef.current = window.setTimeout(() => {
      takyonRefreshTimerRef.current = null;
      void refreshBusinessSurfaces();
    }, 250);
  }, [refreshBusinessSurfaces]);

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
    setMessages((prev) => stripTakyonChatNoise(prev));
  }, [sessionId, scopeState.business]);

  useEffect(() => {
    let cancelled = false;
    const cleanup: Array<() => void> = [];

    const refreshScope = () => {
      void loadDashboardState(undefined)
        .then((nextScope) => {
          if (cancelled || !nextScope) return;
          if (nextScope.auto_switched_business) {
            setMessages((prev) => [
              ...prev,
              makeMessage(
                "system",
                `Entered business:${nextScope.auto_switched_business}`,
              ),
            ]);
          } else if (nextScope.auto_scope_warning) {
            const warning = nextScope.auto_scope_warning || "";
            if (isBusinessScopeDeniedMessage(warning)) {
              noteBootIssue(
                nextScope.auto_switched_business ||
                  scopeBusinessRef.current ||
                  pendingBusinessSlugRef.current ||
                  businessFromLocationSearch(),
                warning,
              );
            } else if (!isTransientConnectionMessage(warning)) {
              setMessages((prev) => [...prev, makeMessage("system", warning)]);
            }
          }
        })
        .catch(() => {
          /* missing-session and access paths are handled inside loadDashboardState */
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
      gw.on("takyon.operator.account", () => {
        void refreshOperatorAccount();
      }),
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
      gw.on<{ kind?: string; text?: string }>("status.update", (ev) => {
        const text = asText(ev.payload?.text).trim();
        const kind = asText(ev.payload?.kind).trim().toLowerCase();
        if (kind === "takyon" && text) {
          const businessHint =
            scopeBusinessRef.current ||
            pendingBusinessSlugRef.current ||
            businessFromLocationSearch();
          setTakyonProgress((prev) => updateTakyonProgress(prev, text, businessHint));
          scheduleTakyonRefresh();
        }
        if (text) setStatusItems((prev) => [cleanText(text), ...prev].slice(0, 5));
      }),
    );

    cleanup.push(
      gw.on<{ message?: string }>("error", (ev) => {
        const message = ev.payload?.message || "The chat gateway reported an error.";
        setRunning(false);
        if (isTransientConnectionMessage(message)) {
          setError(null);
          return;
        }
        if (isBusinessScopeDeniedMessage(message)) {
          noteBootIssue(
            scopeBusinessRef.current ||
              pendingBusinessSlugRef.current ||
              businessFromLocationSearch(),
            message,
          );
          setError(null);
          return;
        }
        setError(message);
      }),
    );

    cleanup.push(
      gw.on<{ tool_id?: string; name?: string; context?: string }>(
        "tool.start",
        (ev) => {
          const p = ev.payload;
          if (!p?.tool_id) return;
          const toolId = p.tool_id;
          const startedTool: ToolEntry = {
            id: `tool-${toolId}-${Date.now()}`,
            tool_id: toolId,
            name: p.name || "tool",
            context: p.context,
            status: "running" as const,
            startedAt: Date.now(),
          };
          setTools((prev) => [...prev, startedTool].slice(-30));
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
        duration_s?: number;
      }>("tool.complete", (ev) => {
        const p = ev.payload;
        if (!p?.tool_id) return;
        const completedAt = Date.now();
        const durationSeconds =
          typeof p.duration_s === "number" && Number.isFinite(p.duration_s)
            ? Math.max(0, p.duration_s)
            : undefined;
        const completedTool: ToolEntry = {
          id: `tool-${p.tool_id}`,
          tool_id: p.tool_id,
          name: p.name || "tool",
          status: p.error ? "error" : "done",
          summary: p.summary,
          error: p.error,
          inline_diff: p.inline_diff,
          startedAt: durationSeconds === undefined ? completedAt : completedAt - durationSeconds * 1000,
          completedAt,
          duration_s: durationSeconds,
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
                  duration_s: durationSeconds,
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

    const hydrateScope = async (
      nextSessionId: string,
      boot?: SessionCreateResponse["takyon_boot"],
    ) => {
      const bootBusiness = businessFromLocationSearch();
      const bootIssue = takyonBootMessage(boot);
      console.warn(
        `[takyon-debug] hydrateScope start business=${bootBusiness || "<none>"} accepted=${boot?.accepted ? "yes" : "no"} issue=${bootIssue || "<none>"} session=${nextSessionId}`,
      );
      if (bootBusiness && bootIssue) {
        noteBootIssue(bootBusiness, bootIssue);
      }
      try {
        const nextScope = await loadDashboardState(
          !resumeParam && bootBusiness && !bootIssue && !boot?.accepted
            ? bootBusiness
            : undefined,
          {
            sessionId: nextSessionId,
            syncUrl: Boolean(bootBusiness && !bootIssue),
          },
        );
        if (cancelled) return;
        if (!nextScope) return;
        console.info("[takyon-scope] hydrateScope", {
          bootBusiness,
          nextBusiness: nextScope.business,
          sessionId: nextSessionId,
        });
        if (nextScope.business) {
          setBlockedBootBusinessSlug((current) =>
            current === nextScope.business ? null : current,
          );
          setBlockedBootMessage((current) =>
            current && current.includes(`business:${nextScope.business}`) ? null : current,
          );
        }
        if (bootBusiness && nextScope.business !== bootBusiness && !bootIssue && !boot?.accepted) {
          console.info("[takyon-scope] hydrateScope pending fallback", {
            bootBusiness,
            nextBusiness: nextScope.business,
            sessionId: nextSessionId,
          });
          await loadDashboardState(bootBusiness, {
            sessionId: nextSessionId,
            syncUrl: true,
          });
        }
      } catch (err) {
        if (!cancelled && bootBusiness) {
          const message = err instanceof Error ? err.message : String(err);
          console.warn(
            `[takyon-debug] hydrateScope error business=${bootBusiness} session=${nextSessionId} message=${message}`,
          );
          if (/access denied|could not open business|no businesses are visible/i.test(message)) {
            noteBootIssue(bootBusiness, message);
          } else {
            setPendingBusinessSlug(bootBusiness);
          }
        }
        throw err;
      } finally {
        scopeHydrationInFlightRef.current = false;
      }
    };

    const initializeSession = async () => {
      const bootBusiness = businessFromLocationSearch();
      sessionRecoveryInFlightRef.current = false;
      console.warn(
        `[takyon-debug] initializeSession business=${bootBusiness || "<none>"} resume=${resumeParam || "<none>"} reusable=${sessionIdRef.current || "<none>"}`,
      );
      try {
        await gw.connect();
        if (cancelled) return;
        setError(null);
      } catch {
        if (cancelled) return;
        setError(null);
      }

      try {
        if (resumeParam) {
          scopeHydrationInFlightRef.current = true;
          let res: SessionResumeResponse;
          try {
            res = await gw.request<SessionResumeResponse>(
              "session.resume",
              { session_id: resumeParam, cols: 100 },
            );
          } catch (err) {
            if (!cancelled && isMissingSessionError(err)) {
              recoverMissingSession(bootBusiness || undefined, { clearResume: true });
              return;
            }
            throw err;
          }
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

        const reusableSessionId = sessionIdRef.current;
        if (reusableSessionId) {
          scopeHydrationInFlightRef.current = true;
          try {
            await hydrateScope(reusableSessionId);
          } catch (err) {
            if (!cancelled && isMissingSessionError(err)) {
              recoverMissingSession(bootBusiness || undefined);
              return;
            }
            throw err;
          }
          if (cancelled) return;
          setSessionId(reusableSessionId);
          return;
        }

        const res = await gw.request<SessionCreateResponse>("session.create", {
          cols: 100,
          _takyon_boot_business: bootBusiness || undefined,
        });
        console.warn(
          `[takyon-debug] session.create result business=${bootBusiness || "<none>"} session=${res.session_id} boot=${JSON.stringify(res.takyon_boot || null)}`,
        );
        if (cancelled) return;
        setSessionId(res.session_id);
        setInfo((prev) => ({ ...prev, ...res.info }));
        scopeHydrationInFlightRef.current = true;
        void hydrateScope(res.session_id, res.takyon_boot).catch(() => {
          /* scope hydration is best effort */
        });
      } catch (err) {
        if (!cancelled) {
          const message = err instanceof Error ? err.message : String(err);
          if (isTransientConnectionMessage(message)) {
            setError(null);
          } else if (isBusinessScopeDeniedMessage(message)) {
            noteBootIssue(
              businessFromLocationSearch() || pendingBusinessSlugRef.current || "",
              message,
            );
            setError(null);
          } else {
            setError(message);
            setMessages((prev) => [...prev, makeMessage("system", message)]);
          }
        }
      }
    };

    void initializeSession();

    return () => {
      cancelled = true;
      for (const fn of cleanup) fn();
      gw.close();
    };
  }, [
    gw,
    loadDashboardState,
    noteBootIssue,
    recoverMissingSession,
    refreshOperatorAccount,
    resumeParam,
    scheduleTakyonRefresh,
  ]);

  useEffect(() => {
    const urlBusiness = businessFromLocationSearch();
    if (!urlBusiness || !canUseConnection(state)) return;
    if (scopeHydrationInFlightRef.current) return;
    if (blockedBootBusinessSlug === urlBusiness) return;
    if (scopeState.business === urlBusiness) return;
    if (pendingBusinessSlug === urlBusiness && !sessionId) return;
    console.info("[takyon-scope] url fallback pending", {
      sessionId,
      state,
      urlBusiness,
      scopeBusiness: scopeState.business,
      pendingBusinessSlug,
    });
    setPendingBusinessSlug(urlBusiness);
    void loadDashboardState(urlBusiness, { syncUrl: true });
  }, [
    blockedBootBusinessSlug,
    loadDashboardState,
    pendingBusinessSlug,
    scopeState.business,
    searchParams,
    sessionId,
    state,
  ]);

  useEffect(() => {
    if (state === "open") {
      reconnectAttemptsRef.current = 0;
      sessionStorage.removeItem(WS_AUTH_RELOAD_KEY);
      return;
    }

    if (state === "error" && gw.lastCloseCode === 4401) {
      if (sessionStorage.getItem(WS_AUTH_RELOAD_KEY) !== "1") {
        sessionStorage.setItem(WS_AUTH_RELOAD_KEY, "1");
        window.location.reload();
      }
      return;
    }

    if (state !== "closed" && state !== "error" && state !== "polling") return;

    reconnectAttemptsRef.current += 1;
    const delayMs = Math.min(8_000, 500 * reconnectAttemptsRef.current);
    if (state === "error" && gw.lastCloseMessage && gw.lastCloseCode === 4403) {
      setError(gw.lastCloseMessage);
    } else {
      setError(null);
    }
    const timer = window.setTimeout(() => setVersion((v) => v + 1), delayMs);
    return () => window.clearTimeout(timer);
  }, [gw, state]);

  useEffect(() => {
    if (!sessionId || (!running && state !== "polling" && state !== "closed")) return;
    let cancelled = false;

    const refresh = () => {
      void gw
        .request<SessionHistoryResponse>(
          "session.history",
          { session_id: sessionId },
          10_000,
        )
        .then((res) => {
          if (cancelled) return;
          const polled = (res.messages || [])
            .map(messageFromResume)
            .filter((message): message is ChatMessage => !!message);
          setMessages((prev) => mergePolledMessages(prev, polled));
          setRunning(Boolean(res.running));
          if (scopeBusinessRef.current) {
            void refreshBusinessSurfaces(scopeBusinessRef.current);
          }
        })
        .catch((err) => {
          if (isMissingSessionError(err)) {
            recoverMissingSession(
              scopeBusinessRef.current || businessFromLocationSearch() || undefined,
            );
          }
        });
    };

    refresh();
    const timer = window.setInterval(refresh, state === "polling" ? 2500 : 4000);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [gw, recoverMissingSession, refreshBusinessSurfaces, running, sessionId, state]);

  useEffect(() => {
    if (!takyonProgress?.active || !takyonProgress.business) return;
    if (!canUseConnection(state) || !sessionId) return;
    let cancelled = false;
    const refresh = () => {
      if (cancelled) return;
      void refreshBusinessSurfaces();
    };
    refresh();
    const timer = window.setInterval(refresh, 1500);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [refreshBusinessSurfaces, sessionId, state, takyonProgress]);

  useEffect(() => {
    const progressTask = (displayScope.overview?.tasks || []).find(
      (task) =>
        (task?.source === "runtime" || task?.source === "job") &&
        ["queued", "running"].includes((task.status || "").toLowerCase()) &&
        /CEO (bootstrap|wake)/i.test(task.label || ""),
    );
    const business = activeBusinessSlug;
    if (!business) return;
    if (!progressTask) {
      let shouldRefreshAfterFinish = false;
      setTakyonProgress((prev) => {
        if (
          !prev?.active ||
          normalizeBusinessLookup(prev.business || "") !== normalizeBusinessLookup(business)
        ) {
          return prev;
        }
        shouldRefreshAfterFinish = true;
        return { ...prev, active: false, status: "complete" };
      });
      if (shouldRefreshAfterFinish && canUseConnection(connectionStateRef.current) && sessionIdRef.current) {
        void refreshBusinessSurfaces(business);
        const timer = window.setTimeout(() => {
          void refreshBusinessSurfaces(business);
        }, 1250);
        return () => {
          window.clearTimeout(timer);
        };
      }
      return;
    }
    const detail = cleanText(
      `${progressTask.label || "CEO run"}: ${progressTask.detail || "Running"}`,
    ).trim();
    if (!detail) return;
    setTakyonProgress((prev) => syncTakyonProgressFromTask(prev, business, detail));
  }, [activeBusinessSlug, displayScope.overview]);

  useEffect(() => {
    if (!canUseConnection(state) || !sessionId || !activeBusinessSlug) return;
    void refreshBusinessSurfaces(activeBusinessSlug);
  }, [activeBusinessSlug, refreshBusinessSurfaces, sessionId, state]);

  useEffect(() => {
    if (!canUseConnection(state) || !sessionId || !isSlashCommandPrefix(input)) {
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
    if (isTransientConnectionMessage(text)) return;
    setMessages((prev) => [...prev, makeMessage("system", text)]);
  }, []);

  const openCronScheduler = useCallback(() => {
    setCronSchedule(wakeCron?.schedule || "every 6h");
    setCronError(null);
    setCronOpen(true);
  }, [wakeCron]);

  const saveCronSchedule = useCallback(async () => {
    if (!sessionId) {
      setCronError("Chat is still connecting.");
      return;
    }
    const normalized = normalizeWakeSchedule(cronSchedule);
    if (!normalized) {
      setCronError("Use an interval like every 30m, every 2h, or every 1d.");
      return;
    }

    setCronSaving(true);
    setCronError(null);
    try {
      const res = await gw.request<TakyonShellResponse>(
        "takyon.wake.schedule",
        { session_id: sessionId, schedule: normalized },
        30_000,
      );
      const nextScope = normalizeScopeState(res);
      setScopeState(nextScope);
      const output = cleanText(res.output || "").trim();
      if (output) appendSystem(output);
      setCronOpen(false);
    } catch (err) {
      if (isMissingSessionError(err)) {
        recoverMissingSession(scopeState.business);
        return;
      }
      const message = err instanceof Error ? err.message : String(err);
      setCronError(message);
    } finally {
      setCronSaving(false);
    }
  }, [appendSystem, cronSchedule, gw, recoverMissingSession, scopeState.business, sessionId]);

  const interrupt = useCallback(async () => {
    if (!sessionId) return;
    try {
      await gw.request("session.interrupt", { session_id: sessionId }, 10_000);
      setRunning(false);
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      if (isTransientConnectionMessage(message)) {
        setError(null);
        return;
      }
      setError(message);
      appendSystem(`Interrupt failed: ${message}`);
    }
  }, [appendSystem, gw, sessionId]);

  const setTakyonScope = useCallback(
    async (business: string) => {
      if (!sessionId) return;
      const requestedBusiness = normalizeBusinessLookup(business);
      const params = new URLSearchParams(searchParams);
      syncBusinessSearchParams(params, requestedBusiness);
      setSearchParams(params, { replace: true });
      if (requestedBusiness) {
        const optimistic = optimisticBusinessSummary(visibleBusinesses, requestedBusiness);
        setPendingBusinessSlug(requestedBusiness);
        setScopeState((prev) =>
          normalizeScopeState({
            ...prev,
            business: requestedBusiness,
            current: optimistic,
            overview: undefined,
          }),
        );
      } else {
        setPendingBusinessSlug(null);
        setWorkspaceSnapshot(null);
        setScopeState((prev) =>
          normalizeScopeState({
            ...prev,
            business: "",
            current: {},
            overview: undefined,
          }),
        );
      }
      requestAnimationFrame(() => inputRef.current?.focus());
      try {
        await gw.request(
          "takyon.scope.set",
          {
            session_id: sessionId,
            business: requestedBusiness || "global",
          },
          10_000,
        );
        const nextScope = await loadDashboardState(requestedBusiness, { syncUrl: true });
        if (!nextScope) return;
        setPendingBusinessSlug(null);
        if (nextScope.business) {
          setBlockedBootBusinessSlug((current) =>
            current === nextScope.business ? null : current,
          );
          setBlockedBootMessage((current) =>
            current && current.includes(`business:${nextScope.business}`) ? null : current,
          );
        }
        appendSystem(
          nextScope.business
            ? `Using business:${nextScope.business}`
            : "Using global scope",
        );
      } catch (err) {
        if (isMissingSessionError(err)) {
          recoverMissingSession(requestedBusiness);
          return;
        }
        throw err;
      }
    },
    [
      appendSystem,
      gw,
      loadDashboardState,
      recoverMissingSession,
      searchParams,
      sessionId,
      setSearchParams,
      visibleBusinesses,
    ],
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

      setTools([]);
      setStatusItems([]);
      setRunning(true);
      setError(null);
      await requestPromptSubmit(text);
    },
    [gw, requestPromptSubmit, running, sessionId],
  );

  const executeTakyonSlash = useCallback(
    async (text: string) => {
      if (!sessionId) throw new Error("Chat is still connecting.");
      const res = await gw.request<TakyonShellResponse>(
        "takyon.shell.exec",
        { session_id: sessionId, line: text },
        600_000,
      );
      const nextScope = normalizeScopeState(res);
      setScopeState(nextScope);
      if (nextScope.business) {
        void refreshBusinessSurfaces(nextScope.business);
      } else {
        setWorkspaceSnapshot(null);
      }
      void refreshOperatorAccount();
      if (/^\s*\/?(?:create|build|init)(?:\s|$)/i.test(text) && nextScope.business) {
        setPendingBusinessSlug(nextScope.business);
        const params = new URLSearchParams(searchParams);
        syncBusinessSearchParams(params, nextScope.business);
        setSearchParams(params, { replace: true });
      }
      const output = cleanText(res.output || "").trim();
      if (output) {
        if (/^(?:Create|Wake) started for business:/i.test(output)) {
          setStatusItems((prev) => [output, ...prev].slice(0, 5));
        } else {
          appendSystem(output);
        }
      }
    },
    [
      appendSystem,
      gw,
      refreshOperatorAccount,
      searchParams,
      sessionId,
      setSearchParams,
    ],
  );

  const createTakyonBusiness = useCallback(
    async ({
      goal,
      name,
      slug,
    }: {
      goal: string;
      name: string;
      slug: string;
    }) => {
      if (!sessionId) throw new Error("Chat is still connecting.");
      const res = await gw.request<TakyonDashboardCreateResponse>(
        "takyon.dashboard.create",
        {
          session_id: sessionId,
          business: slug,
          business_name: name,
          goal,
          mode: "live",
          limit: 50,
        },
        600_000,
      );
      const createdBusiness = normalizeBusinessLookup(res.business_slug || slug);
      const snapshot = normalizeWorkspaceSnapshot(res);
      const createdBusinesses = Array.isArray(res.businesses)
        ? res.businesses.filter((item): item is BusinessSummary => !!item && typeof item === "object")
        : [];
      if (createdBusinesses.length > 0) {
        setOperatorBusinesses(createdBusinesses);
        setOperatorBusinessesAvailable(true);
        setOperatorBusinessesReason(null);
        setOperatorBusinessesLoading(false);
      }
      if (snapshot) {
        setWorkspaceSnapshot(snapshot);
        setHistoricalOutputs({
          business: snapshot.business_slug || createdBusiness,
          items: Array.isArray(snapshot.outputs) ? snapshot.outputs : [],
        });
      }
      setScopeState(
        normalizeScopeState({
          ...res,
          business: createdBusiness,
          current: res.current || snapshot?.current || {},
          overview: res.overview || snapshot?.overview,
          businesses: Array.isArray(res.businesses) ? res.businesses : visibleBusinesses,
        }),
      );
      setPendingBusinessSlug(createdBusiness || null);
      setBlockedBootBusinessSlug((current) =>
        current === createdBusiness ? null : current,
      );
      setBlockedBootMessage((current) =>
        current && current.includes(`business:${createdBusiness}`) ? null : current,
      );
      const params = new URLSearchParams(searchParams);
      syncBusinessSearchParams(params, createdBusiness);
      setSearchParams(params, { replace: true });
      if (res.output) {
        setStatusItems((prev) => [cleanText(res.output || ""), ...prev].slice(0, 5));
      }
      if (
        createdBusiness &&
        ["queued", "running"].includes(String(res.job_status || "").toLowerCase())
      ) {
        const detail =
          cleanText(snapshot?.background_run?.detail || "").trim() ||
          (String(res.job_status || "").toLowerCase() === "running"
            ? "CEO bootstrap is running."
            : "CEO bootstrap is queued.");
        setTakyonProgress((prev) =>
          syncTakyonProgressFromTask(prev, createdBusiness, detail),
        );
      }
      void refreshOperatorAccount();
      void refreshOperatorBusinesses();
      void refreshBusinessSurfaces(createdBusiness);
    },
    [
      gw,
      refreshBusinessSurfaces,
      refreshOperatorBusinesses,
      refreshOperatorAccount,
      searchParams,
      sessionId,
      setSearchParams,
      visibleBusinesses,
    ],
  );

  const submitCreateBusiness = useCallback(
    async (request: {
      name: string;
      slug: string;
      goal: string;
    }) => {
      if (!canUseConnection(state)) return;
      setTools([]);
      setStatusItems([]);
      setRunning(true);
      setError(null);
      try {
        await createTakyonBusiness(request);
      } catch (err) {
        const message = err instanceof Error ? err.message : String(err);
        setError(message);
        setStatusItems((prev) => [friendlyError(message), ...prev].slice(0, 5));
      } finally {
        setRunning(false);
      }
    },
    [createTakyonBusiness, state],
  );

  const resolveBusinessMedia = useCallback(
    async (path: string, business?: string): Promise<BusinessMediaResponse> => {
      if (!sessionId) throw new Error("Chat is still connecting.");
      const targetBusiness = normalizeBusinessLookup(
        business || scopeBusinessRef.current || pendingBusinessSlugRef.current || "",
      );
      return await gw.request<BusinessMediaResponse>(
        "takyon.file.media",
        { session_id: sessionId, business_slug: targetBusiness, path },
        20_000,
      );
    },
    [gw, sessionId],
  );

  const readBusinessFile = useCallback(
    async (path: string, business?: string): Promise<BusinessFileReadResponse> => {
      const targetBusiness = normalizeBusinessLookup(
        business || scopeBusinessRef.current || pendingBusinessSlugRef.current || "",
      );
      if (!targetBusiness) throw new Error("Business is still connecting.");
      return await api.getTakyonBusinessFile(targetBusiness, path);
    },
    [],
  );

  const resolveBusinessSitePreview = useCallback(
    async (path?: string, business?: string): Promise<BusinessSitePreviewResponse> => {
      const targetBusiness = normalizeBusinessLookup(
        business || scopeBusinessRef.current || pendingBusinessSlugRef.current || "",
      );
      if (!targetBusiness) throw new Error("Business is still connecting.");
      return await api.getTakyonBusinessSitePreview(targetBusiness, path || "");
    },
    [],
  );

  const handleSubmit = useCallback(async () => {
    const text = input.trim();
    if (!canUseConnection(state)) return;
    if (!text) {
      if (running) await interrupt();
      return;
    }

    setInput("");
    setSlashItems([]);
    setSlashIndex(0);
    setMessages((prev) => [...prev, makeMessage("user", text)]);

    try {
      const requestedBusiness = naturalScopeChange(
        text,
        normalizeScopeState({
          ...scopeState,
          businesses: visibleBusinesses,
        }),
      );
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
      if (isTransientConnectionMessage(message)) {
        setError(null);
        return;
      }
      setError(message);
      if (text.startsWith("/")) {
        appendSystem(message);
      }
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

  const canAct = canUseConnection(state) && (!!input.trim() || running);
  const canInteract = canUseConnection(state) && !!sessionId;
  const inBusiness = !!activeBusinessSlug;
  const scopedHistoricalOutputs =
    historicalOutputs.business === activeBusinessSlug
      ? historicalOutputs.items
      : [];

  const overviewForShell = displayScope.overview || {};
  const productPublicUrl = customerWebsiteUrl({
    business: displayScope.business,
    product: overviewForShell.product || {},
    website: overviewForShell.artifacts?.website || {},
  });

  return (
    <div className="tk-dash flex h-full min-h-0 w-full flex-col overflow-hidden">
      <PluginSlot name="chat:top" />

      {inBusiness && rightOpen && (
        <button
          aria-label="Close CEO panel"
          onClick={() => setRightOpen(false)}
          className="fixed inset-0 z-[55] bg-[#1a1916]/35 backdrop-blur-sm lg:hidden"
          type="button"
        />
      )}

      <div className={cn("td-app min-h-0 flex-1", !inBusiness && "td-app--global")}>
        <BizSidebar
          businesses={visibleBusinesses}
          businessesAvailable={operatorBusinessesAvailable || visibleBusinesses.length > 0}
          businessesReason={operatorBusinessesReason}
          canInteract={canInteract}
          loadingBusinesses={operatorBusinessesLoading}
          onCreate={() => {
            void setTakyonScope("");
          }}
          operatorAccount={operatorAccount}
          onSelect={setTakyonScope}
          scope={displayScope}
          state={state}
        />

        <main className="td-main">
          <div className="td-topbar">
            <div className="td-title">
              <h1>{inBusiness ? scopeName(displayScope) : "Portfolio"}</h1>
              {inBusiness ? (
                <BusinessStatusPill scope={displayScope} />
              ) : (
                <span className="td-meta">account scope</span>
              )}
              {inBusiness && productPublicUrl && (
                <span className="td-url">{prettyHost(productPublicUrl)}</span>
              )}
            </div>
            <div className="td-spacer" />
            <span className="td-meta" title={STATE_LABEL[state]}>
              <span
                className={cn(
                  "mr-1.5 inline-block h-1.5 w-1.5 rounded-full align-middle",
                  connectionDot(state),
                )}
              />
              {STATE_LABEL[state]}
            </span>
            {inBusiness && (
              <button
                aria-label="Open CEO wake schedule"
                className="td-pill td-ghost"
                disabled={!canInteract || cronSaving}
                onClick={openCronScheduler}
                title={
                  wakeCron?.schedule
                    ? `CEO wake schedule: ${wakeCron.schedule}`
                    : "Configure the CEO wake schedule"
                }
                type="button"
              >
                Cron
              </button>
            )}
            {inBusiness && (
              <button
                aria-label="Open CEO panel"
                className="td-pill td-ghost lg:hidden"
                onClick={() => setRightOpen(true)}
                type="button"
              >
                <PanelRight className="h-3.5 w-3.5" /> CEO
              </button>
            )}
            <button
              aria-label="Reconnect"
              className="td-pill td-ghost"
              onClick={reconnect}
              type="button"
            >
              <RefreshCw className="h-3.5 w-3.5" /> Reconnect
            </button>
          </div>

          {inBusiness ? (
            <CompanyWorkspace
              businessSlug={activeBusinessSlug}
              deliverables={mergeOutputs(deliverables, scopedHistoricalOutputs)}
              onReadFile={readBusinessFile}
              onResolveMedia={resolveBusinessMedia}
              onResolveSitePreview={resolveBusinessSitePreview}
              productPublicUrl={productPublicUrl}
              scope={displayScope}
              takyonProgress={takyonProgress}
            />
          ) : businessRequestPending ? (
            <BusinessScopeSyncState
              business={requestedBusinessSlug}
              message={blockedBootBusinessSlug === requestedBusinessSlug ? blockedBootMessage : null}
              onRetry={() => void loadDashboardState(requestedBusinessSlug, { syncUrl: true })}
              state={state}
            />
          ) : (
            <GlobalLaunchpad
              error={error || blockedBootMessage}
              onCreate={submitCreateBusiness}
              operatorAccount={operatorAccount}
              running={running}
              state={state}
              statusItems={statusItems}
              takyonProgress={takyonProgress}
              tools={tools}
            />
          )}
        </main>

        {inBusiness && (
          <aside
            className={cn(
              "td-rail flex-col",
              rightOpen
                ? "fixed inset-y-0 right-0 z-[60] flex w-[min(92vw,540px)]"
                : "hidden lg:flex",
            )}
          >
            <IntercomPanel
              onClose={() => setRightOpen(false)}
              running={running}
              scope={displayScope}
              sessionId={sessionId}
              showClose={rightOpen}
            >
              <Thread
                error={error}
                messages={messages}
                running={running}
                scope={displayScope}
                scrollerRef={scrollerRef}
                statusItems={statusItems}
                takyonProgress={takyonProgress}
                tools={tools}
              >
                <Composer
                  canAct={canAct}
                  disabled={!canInteract}
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
          </aside>
        )}

        {inBusiness && cronOpen && (
          <CronScheduleModal
            business={displayScope.business}
            canInteract={canInteract}
            cron={wakeCron}
            error={cronError}
            onChange={setCronSchedule}
            onClose={() => {
              if (cronSaving) return;
              setCronOpen(false);
              setCronError(null);
            }}
            onSave={() => void saveCronSchedule()}
            saving={cronSaving}
            value={cronSchedule}
            wakeHealth={displayScope.overview?.wake_health}
          />
        )}

      </div>

      <PluginSlot name="chat:bottom" />
    </div>
  );
}

function BusinessScopeSyncState({
  business,
  message,
  onRetry,
  state,
}: {
  business: string;
  message: string | null;
  onRetry: () => void;
  state: ConnectionState;
}) {
  const blocked = !!message;
  return (
    <section className="td-launchpad td-launchpad--business">
      <div className="td-launch-left">
        <p className="td-eyebrow">{blocked ? "Scope blocked" : "Opening workspace"}</p>
        <h2>{blocked ? `Could not open business:${business}` : `Opening business:${business}`}</h2>
        <p className="td-copy">
          {blocked
            ? message
            : canUseConnection(state)
              ? "Waiting for the dashboard to resolve the requested business from backend state."
              : "Waiting for the dashboard connection before loading the requested business."}
        </p>
        <div className="td-actions">
          <button
            className="td-primary"
            disabled={!canUseConnection(state)}
            onClick={onRetry}
            type="button"
          >
            Retry
          </button>
        </div>
      </div>
    </section>
  );
}

function BizSidebar({
  businesses,
  businessesAvailable,
  businessesReason,
  canInteract,
  loadingBusinesses,
  onCreate,
  operatorAccount,
  onSelect,
  scope,
  state,
}: {
  businesses: BusinessSummary[];
  businessesAvailable: boolean;
  businessesReason: string | null;
  canInteract: boolean;
  loadingBusinesses: boolean;
  onCreate: () => void;
  operatorAccount: TakyonOperatorAccountResponse | null;
  onSelect: (business: string) => Promise<void>;
  scope: ScopeState;
  state: ConnectionState;
}) {
  const ready = canUseConnection(state) && canInteract;
  const ownedBusinessCount =
    typeof operatorAccount?.owned_business_count === "number"
      ? operatorAccount.owned_business_count
      : businesses.length;
  const emptyBusinessesMessage = loadingBusinesses
    ? "Loading businesses…"
    : !businessesAvailable
      ? ownedBusinessCount > 0
        ? "Business list failed to sync."
        : businessesReason === "operator_principal_unavailable"
          ? "Sign in to load businesses."
          : "Business list unavailable."
      : "No businesses yet.";
  return (
    <aside className="td-side">
      <div className="td-brand">
        <span className="td-mark">
          <Lightbulb className="h-4 w-4" strokeWidth={2} />
        </span>
        <span className="td-name">Litebulb</span>
      </div>

      <div className="min-h-0">
        <div className="td-side-label">
          <p className="td-eyebrow">Businesses</p>
          <span className="td-meta">{businesses.length || ""}</span>
        </div>
        <div className="td-biz-list">
          {businesses.length === 0 ? (
            <p className="td-meta" style={{ padding: "4px 8px" }}>
              {emptyBusinessesMessage}
            </p>
          ) : (
            businesses.map((item) => {
              const slug = item.slug || "";
              const active = !!slug && slug === scope.business;
              return (
                <button
                  className={cn("td-biz", active && "td-active")}
                  disabled={!slug || !canUseConnection(state)}
                  key={slug || item.name || "biz"}
                  onClick={() => {
                    if (slug) void onSelect(slug);
                  }}
                  type="button"
                >
                  <span className={cn("td-dot", modeDotClass(item))} />
                  <span className="min-w-0">
                    <span className="td-bname truncate">{item.name || slug}</span>
                    <span className="td-sub">{businessModeLabel(item)}</span>
                  </span>
                  <span className="td-mrr td-defer-inline">—</span>
                </button>
              );
            })
          )}
          <button
            className="td-side-add"
            disabled={!ready}
            onClick={onCreate}
            type="button"
          >
            <Plus className="h-4 w-4" />
            New business
          </button>
        </div>
      </div>

      <div className="td-side-foot">
        <button
          className="td-acct"
          onClick={() => void onSelect("")}
          style={{ width: "100%", textAlign: "left" }}
          type="button"
        >
          <span className="td-av">
            <User className="h-4 w-4" strokeWidth={2} />
          </span>
          <span className="min-w-0">
            <span className="td-who" style={{ display: "block" }}>
              Operator
            </span>
            <span className="td-role" style={{ display: "block" }}>
              global scope · {businessCountLabel(ownedBusinessCount)}
            </span>
          </span>
        </button>
      </div>
    </aside>
  );
}

function BusinessStatusPill({ scope }: { scope: ScopeState }) {
  const product = scope.overview?.product;
  if (productIsLive(product)) {
    return (
      <span className="td-pill td-live">
        <span className="td-pdot" />
        Live
      </span>
    );
  }
  const status = (product?.status || "").toLowerCase();
  const label =
    status === "building" || status === "in_progress"
      ? "Building"
      : status
        ? status.replace(/_/g, " ")
        : "Setup";
  return <span className="td-pill td-soft">{label}</span>;
}

function CronScheduleModal({
  business,
  canInteract,
  cron,
  error,
  onChange,
  onClose,
  onSave,
  saving,
  value,
  wakeHealth,
}: {
  business: string;
  canInteract: boolean;
  cron: BusinessOverviewCron | null;
  error: string | null;
  onChange: (value: string) => void;
  onClose: () => void;
  onSave: () => void;
  saving: boolean;
  value: string;
  wakeHealth?: BusinessOverviewWakeHealth;
}) {
  const statusHeadline =
    wakeHealth?.headline || (cron?.enabled ? "CEO wake loop is active." : "No CEO wake loop is configured.");
  const statusDetail =
    wakeHealth?.detail ||
    (cron?.schedule
      ? "Saving updates the recurring CEO wake cadence without triggering an immediate wake."
      : "Save a cadence to create the recurring CEO wake loop for this business.");

  return (
    <div
      aria-modal="true"
      className="td-modal-backdrop"
      onClick={onClose}
      role="dialog"
    >
      <div className="td-modal" onClick={(event) => event.stopPropagation()}>
        <div className="td-modal-head">
          <div>
            <p className="td-eyebrow">CEO wake loop</p>
            <h3>Cron</h3>
            <p className="td-meta">business:{business}</p>
          </div>
          <button
            aria-label="Close cron scheduler"
            className="td-pill td-ghost"
            disabled={saving}
            onClick={onClose}
            type="button"
          >
            <X className="h-3.5 w-3.5" />
          </button>
        </div>

        <div className="td-cron-grid">
          <div className="td-cron-line">
            <span className="td-meta">Status</span>
            <strong>{statusHeadline}</strong>
          </div>
          <div className="td-cron-line">
            <span className="td-meta">Current cadence</span>
            <strong>{cron?.schedule || "Not scheduled"}</strong>
          </div>
          <div className="td-cron-line">
            <span className="td-meta">Next run</span>
            <strong>{cron?.next_run || "Will be set when you save"}</strong>
          </div>
          <div className="td-cron-line">
            <span className="td-meta">Last run</span>
            <strong>{cron?.last_run || "No wake recorded yet"}</strong>
          </div>
        </div>

        <form
          className="grid gap-4"
          onSubmit={(event) => {
            event.preventDefault();
            onSave();
          }}
        >
          <div className="td-cron-presets">
            {WAKE_SCHEDULE_PRESETS.map((preset) => {
              const active = normalizeWakeSchedule(value) === preset;
              return (
                <button
                  className={cn("td-cron-chip", active && "td-active")}
                  key={preset}
                  onClick={() => onChange(preset)}
                  type="button"
                >
                  {preset.replace(/^every\s+/i, "")}
                </button>
              );
            })}
          </div>

          <label className="td-cron-field">
            <span className="td-meta">Custom interval</span>
            <input
              autoFocus
              className="td-cron-input"
              onChange={(event) => onChange(event.target.value)}
              placeholder="every 6h"
              value={value}
            />
          </label>

          <p className="td-defer-note">{statusDetail}</p>
          <p className="td-defer-note">Use an interval like every 30m, every 2h, or every 1d.</p>
          {error && (
            <p className="td-meta" style={{ color: "var(--td-accent-ink)" }}>
              {friendlyError(error)}
            </p>
          )}

          <div className="flex items-center justify-end gap-2">
            <button className="td-btn td-btn-secondary" disabled={saving} onClick={onClose} type="button">
              Close
            </button>
            <button
              className="td-btn td-btn-primary"
              disabled={!canInteract || saving}
              type="submit"
            >
              {saving ? "Saving…" : "Save cron"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}

function GlobalLaunchpad({
  error,
  onCreate,
  operatorAccount,
  running,
  state,
  statusItems,
  takyonProgress,
  tools,
}: {
  error: string | null;
  onCreate: (request: {
    name: string;
    slug: string;
    goal: string;
  }) => Promise<void>;
  operatorAccount: TakyonOperatorAccountResponse | null;
  running: boolean;
  state: ConnectionState;
  statusItems: string[];
  takyonProgress: TakyonProgressState | null;
  tools: ToolEntry[];
}) {
  const [name, setName] = useState("");
  const [goal, setGoal] = useState("");
  const activeTool = tools.slice().reverse().find((tool) => tool.status === "running");
  const latestStatus =
    latestTakyonProgressLine(takyonProgress) ||
    activeTool?.name ||
    statusItems[0] ||
    "";
  const canCreate = canUseConnection(state) && !running && (!!name.trim() || !!goal.trim());
  const displayError = friendlyError(error);
  const usageRemainingPercent = operatorUsageRemainingPercent(operatorAccount);
  const topupBalanceCents = operatorAccount?.available
    ? Math.max(0, Number(operatorAccount.topup_balance_cents || 0))
    : null;
  const operatorBudgetNote =
    usageRemainingPercent === null
      ? "Auto wake follows Takyon's default cadence. Operator budget state is unavailable in this dashboard mode."
      : usageRemainingPercent === 0
        ? "Auto wake follows Takyon's default cadence. Business creation still works, but CEO turns and wakes will block until budget is added."
        : `Auto wake follows Takyon's default cadence. Operator usage: ${formatPercent(usageRemainingPercent)} remaining this week${topupBalanceCents && topupBalanceCents > 0 ? ` with ${formatBudgetCents(topupBalanceCents)} in top-ups` : ""}.`;

  const submit = (event: FormEvent) => {
    event.preventDefault();
    const rawName = name.trim();
    const rawGoal = goal.trim();
    if (!rawName && !rawGoal) return;
    void onCreate({
      goal: rawGoal,
      name: rawName,
      slug: normalizeBusinessLookup(rawName),
    });
  };

  return (
    <div className="td-scroll">
      <div className="mx-auto grid w-full max-w-xl gap-5">
        <form className="td-card" onSubmit={submit}>
          <div className="td-card-h">
            <h3>New business</h3>
            <span className="td-meta">{STATE_LABEL[state]}</span>
          </div>
          {displayError && (
            <p className="td-meta" style={{ color: "var(--td-accent-ink)", marginBottom: 12 }}>
              {displayError}
            </p>
          )}
          {latestStatus && (
            <p className="td-meta" style={{ color: "var(--td-up)", marginBottom: 12 }}>
              {latestStatus}
            </p>
          )}

          <div className="grid gap-3">
            <label className="grid gap-1.5">
              <span className="td-meta">Name</span>
              <input
                className="h-10 rounded-[10px] border border-[var(--td-border)] bg-[var(--td-surface)] px-3 text-[14px] text-[var(--td-fg)] outline-none transition-colors placeholder:text-[var(--td-meta)] focus:border-[var(--td-accent)]"
                onChange={(event) => setName(event.target.value)}
                placeholder="latexflow"
                value={name}
              />
            </label>
            <label className="grid gap-1.5">
              <span className="td-meta">Goal</span>
              <textarea
                className="min-h-28 resize-none rounded-[10px] border border-[var(--td-border)] bg-[var(--td-surface)] px-3 py-2 text-[14px] leading-6 text-[var(--td-fg)] outline-none transition-colors placeholder:text-[var(--td-meta)] focus:border-[var(--td-accent)]"
                onChange={(event) => setGoal(event.target.value)}
                placeholder="Build a business around…"
                value={goal}
              />
            </label>
          </div>
          <p className="td-defer-note" style={{ marginTop: 10 }}>
            {operatorBudgetNote}
          </p>

          <div className="mt-4 flex items-center justify-end">
            <button className="td-btn td-btn-primary" disabled={!canCreate} type="submit">
              Create business
              <ArrowUp className="td-ar h-3.5 w-3.5" style={{ transform: "rotate(45deg)" }} />
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}

function Thread({
  children,
  error,
  messages,
  running,
  scope,
  scrollerRef,
  statusItems,
  takyonProgress,
  tools,
}: {
  children: ReactNode;
  error: string | null;
  messages: ChatMessage[];
  running: boolean;
  scope: ScopeState;
  scrollerRef: RefObject<HTMLDivElement | null>;
  statusItems?: string[];
  takyonProgress?: TakyonProgressState | null;
  tools?: ToolEntry[];
}) {
  const displayError = friendlyError(error);
  const activeTool = (tools || []).slice().reverse().find((tool) => tool.status === "running");
  const activityLabels = recentToolLabels(tools);
  const takyonActivity = latestTakyonProgressLine(takyonProgress, scope.business || "");
  const showActivity = running || !!takyonActivity;
  const workingLabel = takyonActivity ? "Activity" : running ? "Working…" : "";
  const activitySummary =
    takyonActivity ||
    (activityLabels.length > 0
      ? activityLabels.join(" · ")
      : activeTool?.name || (statusItems && statusItems[0]) || "Thinking");
  return (
    <>
      <div ref={scrollerRef} className="td-thread">
        {displayError && (
          <div className="td-msg td-ceo">
            <div className="td-mrole">CEO</div>
            <div
              className="td-mbody"
              style={{ borderColor: "var(--td-accent)", color: "var(--td-accent-ink)" }}
            >
              {displayError}
            </div>
          </div>
        )}
        {messages.length === 0 && !displayError && <ThreadWelcome scope={scope} />}
        {messages.map((message) => (
          <Message key={message.id} message={message} />
        ))}
        {showActivity && (
          <div aria-live="polite" className="td-activity">
            <div className="td-activity-line">
              <span aria-hidden className="td-activity-dot" />
              <span className="truncate">{workingLabel}</span>
            </div>
            <div className="td-activity-tokens">
              <span className="td-activity-token">{activitySummary}</span>
            </div>
          </div>
        )}
      </div>
      {children}
    </>
  );
}

function ThreadWelcome({ scope }: { scope: ScopeState }) {
  const inBusiness = !!scope.business;
  const name = scope.current?.name || scope.business;
  return (
    <div className="td-msg td-ceo">
      <div className="td-mrole">CEO</div>
      <div className="td-mbody">
        {inBusiness
          ? `I'm running ${name}. Ask me to research, build, ship, or grow — or open a deliverable on the left.`
          : "Pick a business on the left, or create one to get started."}
      </div>
    </div>
  );
}

function Composer({
  canAct,
  disabled = false,
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
  disabled?: boolean;
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
    <form className="td-composer" onSubmit={onSubmit}>
      {slashItems.length > 0 && (
        <SlashPalette
          activeIndex={slashIndex}
          items={slashItems}
          onApply={onSlashApply}
          onHover={setSlashIndex}
        />
      )}
      <div className="td-box">
        <textarea
          ref={inputRef}
          aria-label="Message input"
          autoFocus
          onChange={(event) => onChange(event.target.value)}
          onKeyDown={onKeyDown}
          disabled={disabled}
          placeholder={
            disabled
              ? "Disconnected — reconnect to chat"
              : isRunning
                ? "Add an interjection…"
                : "Message the CEO…  ( / for commands )"
          }
          rows={1}
          value={value}
        />
        <button
          aria-label={isRunning && !hasInput ? "Stop generating" : "Send message"}
          className="td-send"
          disabled={!canAct}
          type="submit"
        >
          {isRunning && !hasInput ? (
            <Square className="h-3 w-3 fill-current" />
          ) : (
            <ArrowUp className="h-4 w-4" />
          )}
        </button>
      </div>
      <div className="td-composer-meta">
        <span className="td-meta">{isRunning ? "CEO is working" : "CEO is idle"}</span>
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
    <div
      className="absolute inset-x-3 bottom-full z-20 mb-2 overflow-hidden rounded-xl border border-[var(--td-border)] bg-[var(--td-surface)] p-1"
      style={{ boxShadow: "0 14px 30px -14px rgba(26,25,22,.4)" }}
    >
      <div className="max-h-72 overflow-y-auto">
        {items.map((item, index) => (
          <button
            className={cn(
              "flex w-full items-start gap-3 rounded-lg px-3 py-2 text-left transition-colors",
              index === activeIndex
                ? "bg-[var(--td-surface-2)]"
                : "hover:bg-[var(--td-fg-soft)]",
            )}
            key={`${item.text}-${index}`}
            onClick={() => onApply(item)}
            onMouseEnter={() => onHover(index)}
            type="button"
          >
            <span className="w-28 shrink-0 font-mono text-[13px] text-[var(--td-fg)]">
              {item.display || item.text}
            </span>
            <span className="block min-w-0 flex-1 truncate text-xs text-[var(--td-muted)]">
              {item.description || item.meta || "Litebulb command"}
            </span>
          </button>
        ))}
      </div>
    </div>
  );
}

function Message({ message }: { message: ChatMessage }) {
  const isUser = message.role === "user";
  const isSystem = message.role === "system";

  if (isSystem) {
    return (
      <div className="td-msg td-ceo">
        <div className="td-mrole">system</div>
        <div
          className="td-mbody"
          style={{ whiteSpace: "pre-wrap", fontFamily: "var(--td-font-mono)", fontSize: 12 }}
        >
          {message.content}
        </div>
      </div>
    );
  }

  return (
    <div className={cn("td-msg", isUser ? "td-you" : "td-ceo")}>
      <div className="td-mrole">{isUser ? "You" : "CEO"}</div>
      <div className="td-mbody">
        {isUser ? (
          message.content
        ) : (
          <div className="td-prose [&_a]:text-[var(--td-accent-ink)] [&_a]:underline [&_code]:rounded [&_code]:bg-[var(--td-surface)] [&_code]:px-1 [&_pre]:overflow-auto [&_pre]:rounded-lg [&_pre]:border [&_pre]:border-[var(--td-border)] [&_pre]:bg-[var(--td-surface)] [&_pre]:p-2">
            <Markdown
              content={message.content}
              streaming={message.status === "streaming"}
            />
          </div>
        )}
        {message.status === "interrupted" && (
          <div className="td-meta" style={{ marginTop: 6 }}>
            Interrupted
          </div>
        )}
        {message.status === "error" && (
          <div className="td-meta" style={{ marginTop: 6, color: "var(--td-accent-ink)" }}>
            Error
          </div>
        )}
      </div>
    </div>
  );
}

function IntercomPanel({
  children,
  onClose,
  running,
  scope,
  sessionId,
  showClose,
}: {
  children: ReactNode;
  onClose: () => void;
  running: boolean;
  scope: ScopeState;
  sessionId: string | null;
  showClose: boolean;
}) {
  return (
    <div className="flex h-full min-h-0 w-full flex-col bg-[var(--td-surface)]">
      <header className="td-rail-head">
        <span className="min-w-0">
          <span className="td-t" style={{ display: "block" }}>
            CEO
          </span>
          <span className="td-meta truncate" style={{ display: "block" }}>
            {scope.current?.name || scope.business}
            {sessionId ? ` · ${sessionId.slice(0, 8)}` : ""}
          </span>
        </span>
        <span className={cn("td-s", running ? "td-working" : "td-sleep")}>
          <span className="td-d" />
          {running ? "working" : "idle"}
        </span>
        {showClose && (
          <button
            aria-label="Close CEO panel"
            className="td-linkish lg:hidden"
            onClick={onClose}
            style={{ marginLeft: 4 }}
            type="button"
          >
            <X className="h-4 w-4" />
          </button>
        )}
      </header>
      {children}
    </div>
  );
}

const DELIVERABLE_KIND_LABEL: Record<Deliverable["kind"], string> = {
  deploy: "Deploy",
  diff: "Code",
  file: "File",
  image: "Image",
  receipt: "Receipt",
  report: "Report",
  tool: "Action",
  video: "Video",
};

type DeliverableAction = { type: "open"; url: string } | { type: "preview" } | null;

function deliverableActionFor(item: Deliverable): DeliverableAction {
  if (item.kind === "deploy" || item.kind === "report") {
    const url = normalizeOpenableUrl(item.detail);
    if (url) return { type: "open", url };
  }
  if (item.path && item.path.trim()) return { type: "preview" };
  const url = normalizeOpenableUrl(item.detail);
  if (url) return { type: "open", url };
  return null;
}

function CompanyWorkspace({
  businessSlug,
  deliverables,
  onReadFile,
  onResolveMedia,
  onResolveSitePreview,
  productPublicUrl,
  scope,
  takyonProgress,
}: {
  businessSlug: string;
  deliverables: Deliverable[];
  onReadFile: (path: string, business?: string) => Promise<BusinessFileReadResponse>;
  onResolveMedia: (path: string, business?: string) => Promise<BusinessMediaResponse>;
  onResolveSitePreview: (path?: string, business?: string) => Promise<BusinessSitePreviewResponse>;
  productPublicUrl: string;
  scope: ScopeState;
  takyonProgress: TakyonProgressState | null;
}) {
  const overview = scope.overview || {};
  const product = overview.product || {};
  const website = overview.artifacts?.website || {};
  const name = scope.current?.name || scope.business;
  const publicUrl =
    productPublicUrl || customerWebsiteUrl({ business: scope.business, product, website });
  const publishTarget = normalizeOpenableUrl(website.publish_target || product.publish_target);
  const live = productIsLive(product) || !!publicUrl;
  const previewPath =
    website.path || website.source_path || product.source_path || "product/site";
  const latest = deliverables[0];

  const ceoLoop = overview.ceo_loop || {};
  const intent = (ceoLoop.headline || "").trim();
  const nextTask = (ceoLoop.next_action || "").trim();

  const outreach = overview.artifacts?.outreach || {};
  const posts = Array.isArray(overview.posts) ? overview.posts : [];
  const publishedCount = outreach.published_count || 0;
  const outreachStatus = (outreach.status || "").trim();
  const distActive =
    posts.length > 0 || publishedCount > 0 || (!!outreachStatus && outreachStatus !== "missing");

  // Progressive disclosure: a section only appears once it actually exists.
  // Product shows when live or a built site/preview exists; Distribution when
  // outreach is active; Deliverables is always the spine. Section numbers count
  // only the sections that render, so they stay sequential as boxes pop in.
  const hasProduct = live || !!website.path;
  const capitalize = (value: string) =>
    value ? value.charAt(0).toUpperCase() + value.slice(1) : value;
  // Deck stands alone — the masthead name is the headline, so no "{name} — …"
  // prefix (the business name is already shown top-left and in the masthead).
  const deckText = capitalize(
    intent
      ? intent
      : nextTask
        ? `Next: ${nextTask}`
        : live
          ? latest
            ? `Live. Latest shipped: ${latest.title}.`
            : "Live — start distribution to bring in customers."
          : deliverables.length
            ? "Taking shape — deliverables are landing."
            : "Just getting started. Ask the CEO to research and build.",
  );
  const sectionKeys: string[] = [];
  if (hasProduct) sectionKeys.push("product");
  if (distActive) sectionKeys.push("distribution");
  sectionKeys.push("deliverables");
  const sectionNo = (key: string) => String(sectionKeys.indexOf(key) + 1).padStart(2, "0");

  const [viewer, setViewer] = useState<{
    content?: string;
    error?: string;
    loading?: boolean;
    media?: BusinessMediaResponse;
    siteUrl?: string;
    path: string;
    title: string;
    truncated?: boolean;
  } | null>(null);

  useEffect(() => {
    setViewer(null);
  }, [scope.business]);

  const progressLine =
    takyonProgress?.active &&
    normalizeBusinessLookup(takyonProgress.business || "") ===
      normalizeBusinessLookup(scope.business || "")
      ? (takyonProgress.lines[takyonProgress.lines.length - 1] || "").trim()
      : "";

  const openSitePreview = useCallback(
    (path?: string, label?: string) => {
      const targetPath = (path || previewPath).trim();
      if (!targetPath) return;
      const title = label || compactPath(targetPath);
      setViewer({ loading: true, path: targetPath, title });
      void onResolveSitePreview(targetPath, businessSlug)
        .then((res) =>
          setViewer({
            loading: false,
            path: res.path || targetPath,
            siteUrl: res.url || "",
            title,
          }),
        )
        .catch((err) =>
          setViewer({
            error: friendlyError(err instanceof Error ? err.message : String(err)),
            loading: false,
            path: targetPath,
            title,
          }),
        );
    },
    [onResolveSitePreview, previewPath],
  );

  const openDocument = useCallback(
    (doc: { label?: string; path?: string }) => {
      const path = (doc.path || "").trim();
      if (!path) return;
      const title = doc.label || compactPath(path);
      if (/\.html?$/i.test(path)) {
        openSitePreview(path, title);
        return;
      }
      setViewer({ loading: true, path, title });
      if (mediaKindForPath(path)) {
        void onResolveMedia(path, businessSlug)
          .then((media) =>
            setViewer({ loading: false, media, path: media.path || path, title }),
          )
          .catch((err) =>
            setViewer({
              error: friendlyError(err instanceof Error ? err.message : String(err)),
              loading: false,
              path,
              title,
            }),
          );
        return;
      }
      void onReadFile(path, businessSlug)
        .then((res) =>
          setViewer({
            content: res.content || "",
            loading: false,
            path: res.path || path,
            title,
            truncated: Boolean(res.truncated),
          }),
        )
        .catch((err) =>
          setViewer({
            error: friendlyError(err instanceof Error ? err.message : String(err)),
            loading: false,
            path,
            title,
          }),
        );
    },
    [onReadFile, onResolveMedia, openSitePreview],
  );

  const openPreview = useCallback(() => {
    openSitePreview(previewPath, `${name} preview`);
  }, [name, openSitePreview, previewPath]);

  return (
    <>
      <div className="td-scroll">
        <header className="td-mast">
          <p className="td-mast-eyebrow">
            <span className="td-mast-tick" />
            {live ? "Live" : deliverables.length ? "In progress" : "New business"}
            {publicUrl ? ` · ${prettyHost(publicUrl)}` : ""}
          </p>
          <h1 className="td-mast-name">{name}</h1>
          <p className="td-mast-deck">{deckText}</p>
          {progressLine && (
            <p className="td-mast-progress">
              <span className="td-dotline" />
              {progressLine}
            </p>
          )}
        </header>

        {hasProduct && (
          <section className="td-block">
            <div className="td-sec">
              <span className="td-sec-no">{sectionNo("product")}</span>
              <h3 className="td-sec-label">Product</h3>
              <span className="td-sec-meta">
                {live ? prettyHost(publicUrl) : publishTarget ? `target ${prettyHost(publishTarget)}` : "local preview"}
              </span>
            </div>
            <div className="td-plate">
              <div className="td-plate-top">
                <span className="td-plate-eyebrow">{live ? "Live product" : "Local preview"}</span>
                <span className="td-plate-host">
                  {live
                    ? prettyHost(publicUrl)
                    : website.path
                      ? compactPath(website.path)
                      : "no site yet"}
                </span>
              </div>
              <h2 className="td-plate-name">{name}</h2>
              {overview.goal && <p className="td-plate-goal">{overview.goal}</p>}
              {!live && publishTarget && (
                <p className="td-meta" style={{ margin: "10px 0 0" }}>
                  Publish target: {prettyHost(publishTarget)}{" "}
                  {product.publish_status === "published" ? "" : "· not live yet"}
                </p>
              )}
              <div className="td-funnel">
                {["Signups", "Active", "Paying"].map((label, index) => (
                  <Fragment key={label}>
                    {index > 0 && <span className="td-arr">→</span>}
                    <div className="td-step">
                      <div className="td-fv">—</div>
                      <div className="td-fl">{label}</div>
                    </div>
                  </Fragment>
                ))}
              </div>
              <div className="td-prod-foot">
                {live ? (
                  <button
                    className="td-btn td-btn-primary"
                    onClick={() => openUrlInNewTab(publicUrl)}
                    type="button"
                  >
                    Open website <ExternalLink className="td-ar h-3.5 w-3.5" />
                  </button>
                ) : (
                  <button className="td-btn td-btn-primary" onClick={openPreview} type="button">
                    Open local preview <ExternalLink className="td-ar h-3.5 w-3.5" />
                  </button>
                )}
              </div>
            </div>
          </section>
        )}

        {distActive && (
          <section className="td-block">
            <div className="td-sec">
              <span className="td-sec-no">{sectionNo("distribution")}</span>
              <h3 className="td-sec-label">Distribution</h3>
              <span className="td-sec-meta">
                {publishedCount
                  ? `${publishedCount} published`
                  : outreachStatus === "draft_only"
                    ? "drafts ready"
                    : `${posts.length} thread${posts.length === 1 ? "" : "s"}`}
              </span>
            </div>
            {posts.length > 0 ? (
              <div className="td-deliv">
                {posts.slice(0, 5).map((post, index) => {
                  const postUrl = normalizeOpenableUrl(post.url);
                  return (
                    <div className="td-drow" key={post.id || `${post.source}-${index}`}>
                      <span className="td-dtype">
                        <span className="td-ti" />
                        {channelLabel(post.source)}
                      </span>
                      <span className="min-w-0">
                        <span className="td-dtitle" style={{ display: "block" }}>
                          {post.title || "Untitled post"}
                        </span>
                        {(post.status || post.mode) && (
                          <span className="td-ddesc" style={{ display: "block" }}>
                            {[post.status, post.mode].filter(Boolean).join(" · ")}
                          </span>
                        )}
                      </span>
                      <span className="td-dimpact td-defer-inline">—</span>
                      <span className="td-dact">
                        {postUrl ? (
                          <a href={postUrl} rel="noreferrer" target="_blank">
                            Open ↗
                          </a>
                        ) : post.artifact_path ? (
                          <button
                            onClick={() =>
                              openDocument({ label: post.title, path: post.artifact_path })
                            }
                            type="button"
                          >
                            Preview
                          </button>
                        ) : post.conversation_file ? (
                          <button
                            onClick={() =>
                              openDocument({ label: post.title, path: post.conversation_file })
                            }
                            type="button"
                          >
                            Open
                          </button>
                        ) : null}
                      </span>
                    </div>
                  );
                })}
              </div>
            ) : (
              <p style={{ margin: 0, fontSize: 13.5, color: "var(--td-muted)" }}>
                {outreachStatus === "draft_only"
                  ? "Outreach drafts are ready to publish."
                  : "Outreach has been published locally."}
                {outreach.path ? ` (${compactPath(outreach.path)})` : ""}
              </p>
            )}
          </section>
        )}

        <section className="td-block">
          <div className="td-sec">
            <span className="td-sec-no">{sectionNo("deliverables")}</span>
            <h3 className="td-sec-label">Deliverables</h3>
            <span className="td-sec-meta">{latest ? `Updated ${relativeTime(latest.at)}` : ""}</span>
          </div>
          {deliverables.length === 0 ? (
            <div className="td-empty">
              <p className="td-empty-title">No deliverables yet</p>
              <p>When the CEO ships research, a site, posts, or revenue receipts, they show up here.</p>
            </div>
          ) : (
            <div className="td-deliv">
              {deliverables.map((item) => {
                const action = deliverableActionFor(item);
                return (
                  <div className="td-drow" key={item.id}>
                    <span className="td-dtype">
                      <span className="td-ti" />
                      {DELIVERABLE_KIND_LABEL[item.kind] || "Item"}
                    </span>
                    <span className="min-w-0">
                      <span className="td-dtitle" style={{ display: "block" }}>
                        {item.title}
                      </span>
                      {item.detail && (
                        <span className="td-ddesc" style={{ display: "block" }}>
                          {item.detail}
                        </span>
                      )}
                    </span>
                    <span className="td-dimpact td-defer-inline">—</span>
                    <span className="td-dact">
                      {action?.type === "open" ? (
                        <a href={action.url} rel="noreferrer" target="_blank">
                          Open ↗
                        </a>
                      ) : action ? (
                        <button
                          onClick={() => openDocument({ label: item.title, path: item.path })}
                          type="button"
                        >
                          Preview
                        </button>
                      ) : null}
                    </span>
                  </div>
                );
              })}
            </div>
          )}
        </section>
      </div>

      {viewer && (
        <div
          className="fixed inset-0 z-[70] flex items-center justify-center p-4"
          onClick={() => setViewer(null)}
          role="presentation"
          style={{ background: "rgba(26,25,22,.42)", backdropFilter: "blur(2px)" }}
        >
          <div
            className="td-card"
            onClick={(event) => event.stopPropagation()}
            style={{
              display: "flex",
              flexDirection: "column",
              maxHeight: "86vh",
              maxWidth: 780,
              overflow: "hidden",
              padding: 0,
              width: "100%",
            }}
          >
            <div
              className="td-card-h"
              style={{
                borderBottom: "1px solid var(--td-border)",
                marginBottom: 0,
                padding: "14px 18px",
              }}
            >
              <div className="min-w-0">
                <h3 className="truncate">{viewer.title}</h3>
                <div className="td-meta truncate">{viewer.path}</div>
              </div>
              <button
                aria-label="Close preview"
                className="td-linkish"
                onClick={() => setViewer(null)}
                style={{ marginLeft: "auto" }}
                type="button"
              >
                <X className="h-4 w-4" />
              </button>
            </div>
            <div style={{ overflow: "auto", padding: 18 }}>
              {viewer.loading ? (
                <p className="td-meta">Opening…</p>
              ) : viewer.error ? (
                <p className="td-meta" style={{ color: "var(--td-accent-ink)" }}>
                  {viewer.error}
                </p>
              ) : viewer.siteUrl ? (
                <iframe
                  src={viewer.siteUrl}
                  title={viewer.title}
                  style={{
                    background: "#fff",
                    border: "1px solid var(--td-border)",
                    borderRadius: 18,
                    height: "70vh",
                    width: "100%",
                  }}
                />
              ) : viewer.media ? (
                viewer.media.media_type?.startsWith("video/") ? (
                  <video className="w-full rounded-lg" controls src={viewer.media.url} />
                ) : (
                  <img alt={viewer.title} className="w-full rounded-lg" src={viewer.media.url} />
                )
              ) : /\.md$/i.test(viewer.path) ? (
                <div
                  className="td-prose [&_a]:text-[var(--td-accent-ink)] [&_a]:underline [&_code]:rounded [&_code]:bg-[var(--td-surface-2)] [&_code]:px-1 [&_pre]:overflow-auto [&_pre]:rounded-lg [&_pre]:border [&_pre]:border-[var(--td-border)] [&_pre]:bg-[var(--td-surface-2)] [&_pre]:p-3"
                  style={{ color: "var(--td-fg)", fontSize: 14, lineHeight: 1.6 }}
                >
                  <Markdown content={viewer.content || ""} />
                </div>
              ) : (
                <pre
                  style={{
                    color: "var(--td-fg-2)",
                    fontFamily: "var(--td-font-mono)",
                    fontSize: 12.5,
                    lineHeight: 1.55,
                    margin: 0,
                    whiteSpace: "pre-wrap",
                  }}
                >
                  {viewer.content || ""}
                </pre>
              )}
              {viewer.truncated && (
                <p className="td-meta" style={{ marginTop: 10 }}>
                  Preview truncated.
                </p>
              )}
            </div>
          </div>
        </div>
      )}
    </>
  );
}

function relativeTime(ts: number): string {
  const diff = Date.now() - ts;
  if (diff < 0) return "just now";
  const s = Math.floor(diff / 1000);
  if (s < 60) return `${s}s ago`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ago`;
  const d = Math.floor(h / 24);
  return `${d}d ago`;
}
