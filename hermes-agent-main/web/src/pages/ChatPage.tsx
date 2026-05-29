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
import {
  displayNameFromId,
  metadataDebugDetail,
} from "@/lib/takyonActivity";
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

const EMPTY_BUSINESS_FILES: BusinessOverviewFile[] = [];

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
  polling: "HTTP fallback",
  closed: "Reconnecting",
  error: "Offline, retrying",
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

function readableFileTime(value?: number | string): string {
  if (typeof value === "number" && Number.isFinite(value)) {
    return readableDate(new Date(value).toISOString());
  }
  return readableDate(typeof value === "string" ? value : "");
}

function formatBytes(value?: number): string {
  if (typeof value !== "number" || !Number.isFinite(value) || value < 0) return "";
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(value < 10 * 1024 ? 1 : 0)} KB`;
  return `${(value / (1024 * 1024)).toFixed(value < 10 * 1024 * 1024 ? 1 : 0)} MB`;
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
  if (/queued|pending|waiting|scheduled|needed/.test(value)) return "Not started";
  if (/running|active|watch|working|research_first/.test(value)) return "Working";
  if (/done|complete|success|passed|visible|previewable/.test(value)) return "Done";
  if (value === "quiet") return "Quiet";
  return humanizeJobKind(value);
}

function registryTool(
  registry: RegistryDisplayPayload | undefined,
  name?: string,
): RegistryDisplayEntry | undefined {
  const key = (name || "").trim();
  return key ? registry?.tools?.[key] : undefined;
}

function parseRuntimeTool(task: BusinessOverviewTask | BusinessOverviewJob): {
  detail?: string;
  duration?: string;
  phase: "preparing" | "started" | "completed";
  toolName: string;
} | null {
  const text = `${task.label || ""} ${task.detail || ""}`.trim();
  const delegatedMatch = text.match(
    /\bagent\s*(?:->|→)\s*(delegate_task|business_claude_agent_task):\s*(.*)/i,
  );
  if (delegatedMatch) {
    const detail = (delegatedMatch[2] || "")
      .replace(/\s*·\s*(?:running|working|done)$/i, "")
      .replace(/^subagent\b/i, "Subagent")
      .trim();
    return {
      detail,
      phase: /\b(done|completed|finished)\b/i.test(detail) ? "completed" : "started",
      toolName: delegatedMatch[1],
    };
  }
  const match =
    text.match(/\b(preparing tool|tool started|tool completed)\s*(?:->|→|-)?\s*([a-zA-Z0-9_.:-]+)(?:\s*·\s*(.*))?/i) ||
    text.match(/\bagent\s*(?:->|→)\s*(executing tool|tool completed):\s*([a-zA-Z0-9_.:-]+)(?:\s*\(([^)]*)\))?/i);
  if (!match) return null;
  const phase =
    match[1].toLowerCase().includes("completed")
      ? "completed"
      : match[1].toLowerCase().includes("started") || match[1].toLowerCase().includes("executing")
        ? "started"
        : "preparing";
  const tail = (match[3] || "").trim();
  const duration = /^\d+(?:\.\d+)?s$/.test(tail) ? tail : undefined;
  return {
    detail: duration ? "" : tail,
    duration,
    phase,
    toolName: match[2],
  };
}

function taskLabel(
  task: BusinessOverviewTask | BusinessOverviewJob,
  registry?: RegistryDisplayPayload,
): string {
  const runtimeTool = parseRuntimeTool(task);
  if (runtimeTool) {
    return displayNameFromId(runtimeTool.toolName, registry, "tools").label;
  }
  const source = (task as BusinessOverviewTask).source;
  const kind = (task as BusinessOverviewJob).kind;
  const rawId = source || kind;
  if (!task.label && rawId) return displayNameFromId(rawId, registry, "tools").label;
  return task.label || humanizeJobKind(rawId);
}

function taskDetail(
  task: BusinessOverviewTask | BusinessOverviewJob,
  registry?: RegistryDisplayPayload,
): string {
  const runtimeTool = parseRuntimeTool(task);
  if (runtimeTool) {
    const display = displayNameFromId(runtimeTool.toolName, registry, "tools");
    const statusDetail = runtimeTool.duration ? `done in ${runtimeTool.duration}` : "";
    return metadataDebugDetail(
      runtimeTool.toolName,
      display.hasMetadata,
      [runtimeTool.detail, statusDetail].filter(Boolean).join(" · "),
    );
  }
  if (task.detail) return task.detail;
  if ("kind" in task) return gatedActionDetail(task);
  return task.updated_at ? `Updated ${readableDate(task.updated_at)}` : "";
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

function toolDetail(tool: ToolEntry, registry?: RegistryDisplayPayload): string {
  const detail = friendlyError(tool.error || tool.summary || tool.preview || tool.context || "");
  const display = displayNameFromId(tool.name || "tool", registry, "tools");
  return metadataDebugDetail(tool.name || "", display.hasMetadata, detail || humanizeStatus(tool.status));
}

function formatElapsedSeconds(seconds: number, options: { compact?: boolean } = {}): string {
  const value = Math.max(0, seconds);
  if (!options.compact && value < 10) return `${value.toFixed(1)}s`;
  return `${Math.round(value)}s`;
}

function toolElapsedSeconds(tool: ToolEntry, now: number): number | null {
  if (typeof tool.duration_s === "number" && Number.isFinite(tool.duration_s)) {
    return Math.max(0, tool.duration_s);
  }
  if (tool.startedAt <= 0) return null;
  const end = tool.completedAt || now;
  return Math.max(0, (end - tool.startedAt) / 1000);
}

function toolActivityStatus(tool: ToolEntry, now: number): string {
  const elapsed = toolElapsedSeconds(tool, now);
  if (tool.status === "running") {
    return elapsed === null ? "running" : `running ${formatElapsedSeconds(elapsed, { compact: true })}`;
  }
  if (tool.status === "error") {
    return elapsed === null ? "needs attention" : `needs attention in ${formatElapsedSeconds(elapsed)}`;
  }
  return elapsed === null ? "done" : `done in ${formatElapsedSeconds(elapsed)}`;
}

function conciseActivityDetail(value?: string): string {
  const text = cleanText(value || "");
  if (!text) return "";
  return text.length > 90 ? `${text.slice(0, 87)}...` : text;
}

function isStatusOnlyDetail(value?: string): boolean {
  const text = (value || "").trim().toLowerCase();
  return (
    !text ||
    text === "done" ||
    text === "working" ||
    text === "running" ||
    /^running\s+\d+(?:\.\d+)?s$/.test(text) ||
    /^done\s+in\s+\d+(?:\.\d+)?s$/.test(text)
  );
}

function cleanTraceDetail(detail?: string, status?: string, label?: string): string {
  const text = conciseActivityDetail(detail);
  if (!text) return "";
  const normalized = text.toLowerCase();
  const normalizedStatus = (status || "").trim().toLowerCase();
  const normalizedLabel = (label || "").trim().toLowerCase();
  if (isStatusOnlyDetail(text)) return "";
  if (normalized === normalizedStatus || normalized === normalizedLabel) return "";
  if (normalized === `raw: runtime`) return "";
  return text;
}

function cleanTraceStatus(status?: string): string {
  const text = (status || "").trim();
  if (!text) return "";
  if (/^running\b/i.test(text)) return text.replace(/^running/i, "Running");
  if (/^working$/i.test(text)) return "Working";
  if (/^done\b/i.test(text)) return text.replace(/^done/i, "Done");
  if (/^needs attention\b/i.test(text)) return text.replace(/^needs attention/i, "Needs attention");
  return humanizeStatus(text);
}

function parseRuntimeAgentEvent(task: BusinessOverviewTask | BusinessOverviewJob): ActivityTraceItem | null {
  const id = String(task.id || `${task.label || "runtime"}:${task.detail || ""}`);
  const text = `${task.label || ""} ${task.detail || ""}`.trim();
  const line = text.replace(/^(agent|ceo live trace)\s+/i, "").trim();
  if (!line || /^\.?\s*done$/i.test(line) || /^tool\s*$/i.test(line)) return null;

  let match = line.match(/^agent\s*(?:->|→)\s*receiving stream response/i);
  if (match) {
    return {
      id,
      label: "Receiving model response",
      status: "Working",
      tone: "running",
    };
  }

  match = line.match(/^agent\s*(?:->|→)\s*starting API call\s+#?(\d+)/i);
  if (match) {
    return {
      detail: `API call #${match[1]}`,
      id,
      label: "Starting model call",
      status: "Working",
      tone: "running",
    };
  }

  match = line.match(/^agent\s*(?:->|→)\s*API call\s+#?(\d+)\s+completed/i);
  if (match) {
    return {
      detail: `API call #${match[1]}`,
      id,
      label: "Model call finished",
      status: "Done",
      tone: "done",
    };
  }

  return null;
}

interface ActivityTraceItem {
  detail?: string;
  id: string;
  label: string;
  rawId?: string;
  status: string;
  tone?: string;
}

function compactActivityItems(items: Array<ActivityTraceItem | null | undefined>): ActivityTraceItem[] {
  const seen = new Set<string>();
  const compacted: ActivityTraceItem[] = [];
  for (const item of items) {
    if (!item) continue;
    const label = (item.label || "").trim();
    const status = cleanTraceStatus(item.status);
    const detail = cleanTraceDetail(item.detail, status, label);
    if (!label || (/^(agent|tool|\.)$/i.test(label) && !detail)) continue;
    const key = `${label.toLowerCase()}:${detail.toLowerCase()}:${status.toLowerCase()}`;
    if (seen.has(key)) continue;
    seen.add(key);
    compacted.push({ ...item, detail, status });
  }
  return compacted;
}

function normalizedTraceKey(value?: string): string {
  return cleanText(value || "")
    .toLowerCase()
    .replace(/\(iteration\s+\d+\/\d+\)/g, "(iteration)")
    .replace(/\brunning\s+\d+(?:\.\d+)?s\b/g, "running")
    .replace(/\bdone\s+in\s+\d+(?:\.\d+)?s\b/g, "done")
    .replace(/\s+/g, " ")
    .trim();
}

function isWorkerTask(
  task: BusinessOverviewTask | BusinessOverviewJob,
  registry?: RegistryDisplayPayload,
): boolean {
  const runtimeTool = parseRuntimeTool(task);
  if (runtimeTool && isWorkerToolName(runtimeTool.toolName, registry)) return true;
  const rawId = (task as BusinessOverviewTask).source || (task as BusinessOverviewJob).kind || "";
  return isWorkerToolName(rawId, registry);
}

function activityFromTool(
  tool: ToolEntry,
  registry: RegistryDisplayPayload | undefined,
  now: number,
): ActivityTraceItem {
  const display = displayNameFromId(tool.name || "tool", registry, "tools");
  const status = toolActivityStatus(tool, now);
  const detail = cleanTraceDetail(
    tool.status === "running"
      ? tool.context || tool.preview
      : tool.context || tool.summary || tool.preview,
    status,
    naturalToolLabel(tool, registry),
  );
  return {
    detail: metadataDebugDetail(tool.name || "", display.hasMetadata, detail),
    id: tool.id,
    label: naturalToolLabel(tool, registry),
    rawId: display.hasMetadata ? undefined : tool.name,
    status,
    tone: tool.status,
  };
}

function activityFromTask(
  task: BusinessOverviewTask | BusinessOverviewJob,
  registry: RegistryDisplayPayload | undefined,
): ActivityTraceItem | null {
  const runtimeAgentEvent = parseRuntimeAgentEvent(task);
  if (runtimeAgentEvent) return runtimeAgentEvent;
  const runtimeTool = parseRuntimeTool(task);
  if (runtimeTool) {
    const display = displayNameFromId(runtimeTool.toolName, registry, "tools");
    const status =
      runtimeTool.phase === "completed"
        ? `done${runtimeTool.duration ? ` in ${runtimeTool.duration}` : ""}`
        : "running";
    return {
      detail: metadataDebugDetail(
        runtimeTool.toolName,
        display.hasMetadata,
        cleanTraceDetail(runtimeTool.detail, status, display.label),
      ),
      id: String(task.id || `${runtimeTool.phase}:${runtimeTool.toolName}`),
      label: display.label,
      rawId: display.hasMetadata ? undefined : runtimeTool.toolName,
      status,
      tone: task.tone || task.status,
    };
  }
  const rawId = (task as BusinessOverviewTask).source || (task as BusinessOverviewJob).kind || "";
  const display = displayNameFromId(rawId, registry, "tools");
  const label = taskLabel(task, registry);
  const status = humanizeStatus(task.status).toLowerCase();
  if (/^(agent|tool|\.)$/i.test(label.trim()) && !cleanTraceDetail(task.detail, status, label)) {
    return null;
  }
  return {
    detail: cleanTraceDetail(taskDetail(task, registry), status, label),
    id: String(task.id || `${taskLabel(task, registry)}:${task.status || ""}`),
    label: /^preparing$/i.test(label) ? "Preparing next step" : label,
    rawId: rawId && rawId !== "runtime" && !display.hasMetadata ? rawId : undefined,
    status,
    tone: task.tone || task.status,
  };
}

interface WorkerDisplayItem {
  id: string;
  latestDetail?: string;
  name: string;
  purpose?: string;
  rawId?: string;
  status: string;
  tone?: string;
}

function isWorkerTool(tool: ToolEntry, registry?: RegistryDisplayPayload): boolean {
  return isWorkerToolName(tool.name, registry);
}

function isWorkerToolName(name?: string, registry?: RegistryDisplayPayload): boolean {
  const meta = registryTool(registry, name);
  if (meta?.category === "agent") return true;
  return /delegate|subagent|agent|claude/i.test(name || "");
}

function workerItems(
  tools: ToolEntry[],
  overviewWorkers: BusinessOverviewWorker[],
  overviewTasks: BusinessOverviewTask[],
  registry: RegistryDisplayPayload | undefined,
  now: number,
): WorkerDisplayItem[] {
  const liveWorkers = tools
    .filter((tool) => isWorkerTool(tool, registry))
    .slice()
    .reverse()
    .slice(0, 6)
    .map((tool) => {
      const display = displayNameFromId(tool.name || "tool", registry, "tools");
      const status = toolActivityStatus(tool, now);
      const name = naturalToolLabel(tool, registry);
      const purpose = cleanTraceDetail(tool.context || tool.preview || tool.summary || tool.error, status, name);
      const latestDetail = cleanTraceDetail(tool.summary || tool.preview || tool.error, status, name);
      return {
        id: `live:${tool.tool_id}`,
        latestDetail,
        name,
        purpose,
        rawId: display.hasMetadata ? undefined : tool.name,
        status,
        tone: tool.status,
      };
    });
  const runtimeWorkers = overviewTasks
    .map((task) => ({ task, runtimeTool: parseRuntimeTool(task) }))
    .filter(({ runtimeTool }) => runtimeTool && isWorkerToolName(runtimeTool.toolName, registry))
    .slice(0, 6)
    .map(({ task, runtimeTool }) => {
      const toolName = runtimeTool?.toolName || "";
      const display = displayNameFromId(toolName, registry, "tools");
      const phase = runtimeTool?.phase || "started";
      const status = phase === "completed"
        ? `done${runtimeTool?.duration ? ` in ${runtimeTool.duration}` : ""}`
        : "running";
      const detail = cleanTraceDetail(runtimeTool?.detail || task.detail || "", status, display.label);
      return {
        id: `runtime:${task.id || toolName}:${phase}`,
        latestDetail: detail,
        name: display.label,
        purpose: detail,
        rawId: display.hasMetadata ? undefined : toolName,
        status,
        tone: task.tone || task.status,
      };
    });
  const historicalWorkers = overviewWorkers.map((worker) => {
    const rawId = worker.tool_name || "";
    const display = displayNameFromId(rawId, registry, "tools");
    return {
      id: `overview:${worker.id || worker.tool_name || worker.name || worker.updated_at || "worker"}`,
      latestDetail: conciseActivityDetail(worker.latest_detail),
      name: display.hasMetadata ? display.label : worker.name || display.label,
      purpose: conciseActivityDetail(worker.purpose),
      rawId: rawId && !display.hasMetadata ? rawId : undefined,
      status: humanizeStatus(worker.status).toLowerCase(),
      tone: worker.tone || worker.status,
    };
  });

  const seen = new Set<string>();
  return [...liveWorkers, ...runtimeWorkers, ...historicalWorkers].filter((worker) => {
    const key = [
      worker.rawId || worker.name,
      normalizedTraceKey(worker.purpose || worker.latestDetail),
      normalizedTraceKey(worker.status),
    ].join(":");
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  }).slice(0, 3);
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
    gate = "Requires provider credentials, product auth, budget gates, and usage audit records.";
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

function researchOutputItems(overview?: BusinessOverview): BusinessOverviewResearchOutput[] {
  const rawItems = [
    ...((overview?.research_outputs || []) as BusinessOverviewResearchOutput[]),
    ...((overview?.research?.outputs || []) as BusinessOverviewResearchOutput[]),
  ];
  const byPath = new Map<string, BusinessOverviewResearchOutput>();
  for (const item of rawItems) {
    const path = normalizeBusinessPath(item.path);
    if (!path || byPath.has(path)) continue;
    byPath.set(path, { ...item, path });
  }
  return [...byPath.values()].sort((a, b) => {
    const aTime = typeof a.updated_at === "number" ? a.updated_at : Date.parse(String(a.updated_at || ""));
    const bTime = typeof b.updated_at === "number" ? b.updated_at : Date.parse(String(b.updated_at || ""));
    return (Number.isFinite(bTime) ? bTime : 0) - (Number.isFinite(aTime) ? aTime : 0);
  });
}

function normalizeBusinessPath(path?: string): string {
  return (path || "").trim().replace(/^\/+/, "").replace(/\/+$/, "");
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

function reserveTabForUserClick(): Window | null {
  try {
    return window.open("about:blank", "_blank", "noopener,noreferrer");
  } catch {
    return null;
  }
}

function navigateReservedTab(tab: Window | null, url: string): boolean {
  const target = normalizeOpenableUrl(url);
  if (!target) throw new Error("No URL available.");
  if (!tab || tab.closed) return false;
  try {
    tab.location.href = target;
    return true;
  } catch {
    return false;
  }
}

const STATE_STATUSES = new Set([
  "visible",
  "available",
  "previewable",
  "ready",
  "done",
  "complete",
  "completed",
  "published",
  "live",
]);

const STATE_PHRASE_REGEX =
  /\b(is|are|was|were|has|have)\b[^.!?]*\b(available|ready|visible|live|published|complete|completed|done|created|generated|previewable)\b/i;

function isActionableTask(
  task: BusinessOverviewTask | BusinessOverviewJob,
): boolean {
  const status = (task.status || "").toLowerCase().trim();
  if (status && STATE_STATUSES.has(status)) return false;
  const text = `${task.label || ""} ${task.detail || ""}`.trim();
  if (text && STATE_PHRASE_REGEX.test(text)) return false;
  return true;
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
  item: GatewayHistoryMessage,
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

function mergePolledMessages(prev: ChatMessage[], polled: ChatMessage[]): ChatMessage[] {
  if (!polled.length) return prev;
  const hasAssistant = polled.some((message) => message.role === "assistant");
  const next = hasAssistant
    ? prev.filter((message) => !(message.role === "assistant" && message.status === "streaming"))
    : [...prev];
  const seen = new Set(next.map((message) => `${message.role}\n${message.content}`));
  for (const message of polled) {
    const key = `${message.role}\n${message.content}`;
    if (seen.has(key)) continue;
    seen.add(key);
    next.push(message);
  }
  return next;
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
  if (state === "connecting" || state === "idle" || state === "polling") return "bg-amber-400";
  return "bg-red-500";
}

function canUseConnection(state: ConnectionState): boolean {
  return state === "open" || state === "polling";
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

const WS_AUTH_RELOAD_KEY = "takyon.dashboard.wsAuthReloaded";

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
  const reconnectAttemptsRef = useRef(0);
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
  const [pendingBusinessSlug, setPendingBusinessSlug] = useState<string | null>(null);
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
          const startedTool: ToolEntry = {
            id: `tool-${toolId}-${Date.now()}`,
            tool_id: toolId,
            name: p.name || "tool",
            context: p.context,
            status: "running" as const,
            startedAt: Date.now(),
          };
          setTools((prev) => [...prev, startedTool].slice(-30));
          const label = naturalToolLabel(startedTool);
          const ctx = (p.context || "").trim();
          setMessages((prev) => {
            const id = `toolmsg-${toolId}`;
            if (prev.some((m) => m.id === id)) return prev;
            return [
              ...prev,
              {
                id,
                role: "system",
                content: ctx ? `▶ ${label} · ${ctx}` : `▶ ${label}`,
                status: "streaming",
              },
            ];
          });
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
        const label = naturalToolLabel(completedTool);
        const tail = (p.error || p.summary || "").trim();
        const icon = p.error ? "✗" : "✓";
        setMessages((prev) => {
          const id = `toolmsg-${p.tool_id}`;
          const content = tail ? `${icon} ${label} · ${tail}` : `${icon} ${label}`;
          const status: ChatMessage["status"] = p.error ? "error" : "complete";
          const existing = prev.findIndex((m) => m.id === id);
          if (existing === -1) {
            return [...prev, { id, role: "system", content, status }];
          }
          const next = [...prev];
          next[existing] = { ...next[existing], content, status };
          return next;
        });
        setDeliverables((prev) =>
          upsertDeliverables(prev, deliverablesFromTool(completedTool)),
        );
        refreshScope();
      }),
    );

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

    const initializeSession = async () => {
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

        const reusableSessionId = sessionIdRef.current;
        if (reusableSessionId) {
          await hydrateScope(reusableSessionId);
          if (cancelled) return;
          setSessionId(reusableSessionId);
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
      } catch (err) {
        if (!cancelled) {
          const message = err instanceof Error ? err.message : String(err);
          setError(message);
          setMessages((prev) => [...prev, makeMessage("system", message)]);
        }
      }
    };

    void initializeSession();

    return () => {
      cancelled = true;
      for (const fn of cleanup) fn();
      gw.close();
    };
  }, [gw, initialBusinessParam, resumeParam]);

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
          if (scopeState.business) {
            void gw
              .request<ScopeState>(
                "takyon.scope.get",
                { session_id: sessionId },
                10_000,
              )
              .then((scope) => {
                if (!cancelled) setScopeState(normalizeScopeState(scope));
              })
              .catch(() => {
                /* scope refresh is best effort */
              });
          }
        })
        .catch(() => {
          /* transport recovery is driven by reconnect state */
        });
    };

    refresh();
    const timer = window.setInterval(refresh, state === "polling" ? 2500 : 4000);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [gw, running, scopeState.business, sessionId, state]);

  useEffect(() => {
    if (!canUseConnection(state) || !sessionId || !scopeState.business) return;
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
    if (!canUseConnection(state) || !sessionId || !scopeState.business) return;
    let cancelled = false;
    const refresh = () => {
      void gw
        .request<ScopeState>(
          "takyon.scope.get",
          { session_id: sessionId },
          10_000,
        )
        .then((scope) => {
          if (!cancelled) setScopeState(normalizeScopeState(scope));
        })
        .catch(() => {
          /* scope polling is best effort */
        });
    };
    refresh();
    const timer = window.setInterval(refresh, 2500);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [gw, scopeState.business, sessionId, state]);

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

  const enterPendingBusiness = useCallback(
    (business: string, mode: "test" | "live", goal?: string) => {
      const slug = normalizeBusinessLookup(business);
      if (!slug) return;
      setPendingBusinessSlug(slug);
      setScopeState((prev) => {
        const existing = prev.businesses.find(
          (item) => normalizeBusinessLookup(item.slug || item.name || "") === slug,
        );
        const current: BusinessSummary = existing || {
          slug,
          name: slug,
          goal,
          mode,
          status: "creating",
          state: "working",
          reason: "Create requested from dashboard",
        };
        const businesses = existing
          ? prev.businesses
          : [current, ...prev.businesses];
        return normalizeScopeState({
          ...prev,
          business: slug,
          current,
          businesses,
        });
      });
      const params = new URLSearchParams(searchParams);
      params.set("business", slug);
      setSearchParams(params, { replace: true });
      requestAnimationFrame(() => inputRef.current?.focus());
    },
    [searchParams, setSearchParams],
  );

  useEffect(() => {
    if (!canUseConnection(state) || !sessionId || !pendingBusinessSlug) return;
    let cancelled = false;
    let attempts = 0;
    let timer: number | undefined;

    const confirmScope = async () => {
      attempts += 1;
      try {
        const res = await gw.request<ScopeState>(
          "takyon.scope.set",
          { session_id: sessionId, business: pendingBusinessSlug },
          10_000,
        );
        if (cancelled) return;
        const nextScope = normalizeScopeState(res);
        if (nextScope.business === pendingBusinessSlug) {
          setScopeState(nextScope);
          setPendingBusinessSlug(null);
          return;
        }
      } catch {
        /* create may still be registering the business; keep the optimistic page */
      }

      if (!cancelled && attempts < 120) {
        timer = window.setTimeout(confirmScope, 1500);
      } else if (!cancelled) {
        setPendingBusinessSlug(null);
      }
    };

    void confirmScope();
    return () => {
      cancelled = true;
      if (timer !== undefined) window.clearTimeout(timer);
    };
  }, [gw, pendingBusinessSlug, sessionId, state]);

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
        600_000,
      );
      const nextScope = normalizeScopeState(res);
      setScopeState(nextScope);
      if (/^\s*\/?(?:create|build|init)(?:\s|$)/i.test(effectiveText) && nextScope.business) {
        setPendingBusinessSlug(nextScope.business);
        const params = new URLSearchParams(searchParams);
        params.set("business", nextScope.business);
        setSearchParams(params, { replace: true });
      }
      const output = cleanText(res.output || "").trim();
      if (output) appendSystem(output);
    },
    [appendSystem, createInTestMode, gw, searchParams, sessionId, setSearchParams],
  );

  const runTakyonLine = useCallback(
    async (line: string) => {
      if (!canUseConnection(state)) return;
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

  const canAct = canUseConnection(state) && (!!input.trim() || running);
  const canInteract = canUseConnection(state) && !!sessionId;
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
                disabled={!canInteract}
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
              onCommand={runTakyonLine}
              onListFiles={listBusinessFiles}
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
              onEnterPendingBusiness={enterPendingBusiness}
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
          ) : (
            <DeliverablesPanel
              cwd={info.cwd}
              deliverables={deliverables}
              historicalOutputs={scopedHistoricalOutputs}
              onCommand={runTakyonLine}
              onListFiles={listBusinessFiles}
              onReadFile={readBusinessFile}
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
  onEnterPendingBusiness,
  onSelectBusiness,
  running,
  scope,
  state,
  statusItems,
  tools,
}: {
  error: string | null;
  onCreate: (line: string) => Promise<void>;
  onEnterPendingBusiness: (business: string, mode: "test" | "live", goal?: string) => void;
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
  const canCreate = canUseConnection(state) && !running && (!!name.trim() || !!goal.trim());
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
    onEnterPendingBusiness(slug, mode, goal.trim());
    void onCreate(parts.join(" "));
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
                    disabled={!slug || !canUseConnection(state)}
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
  compact?: boolean;
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
          disabled={disabled}
          placeholder={
            disabled
              ? "Backend disconnected - reconnect to create or wake"
              : isRunning
                ? "Add an interjection..."
                : "Ask Takyon anything or type /"
          }
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
  onCommand,
  onListFiles,
  onReadFile,
  onResolveMedia,
  onResolveSitePreview,
  scope,
  statusItems,
  tools,
}: {
  onCommand: (line: string) => void;
  onListFiles: (path: string) => Promise<BusinessOverviewFile[]>;
  onReadFile: (path: string) => Promise<BusinessFileReadResponse>;
  onResolveMedia: (path: string) => Promise<BusinessMediaResponse>;
  onResolveSitePreview: (path?: string) => Promise<BusinessSitePreviewResponse>;
  scope: ScopeState;
  statusItems: string[];
  tools: ToolEntry[];
}) {
  return (
    <div className="flex min-h-0 flex-1 flex-col bg-[#050505]">
      <CompanyStatusHero
        onCommand={onCommand}
        scope={scope}
        statusItems={statusItems}
        tools={tools}
      />

      <div className="min-h-0 flex-1 overflow-y-auto">
        <CompanyOverview
          onCommand={onCommand}
          onListFiles={onListFiles}
          onReadFile={onReadFile}
          onResolveMedia={onResolveMedia}
          onResolveSitePreview={onResolveSitePreview}
          scope={scope}
          statusItems={statusItems}
          tools={tools}
        />
      </div>
    </div>
  );
}

function CompanyStatusHero({
  onCommand,
  scope,
  statusItems,
  tools,
}: {
  onCommand: (line: string) => void;
  scope: ScopeState;
  statusItems: string[];
  tools: ToolEntry[];
}) {
  const overview = scope.overview || {};
  const registry = overview.registry;
  const cron = (overview.cron || []).filter((job) => job.enabled !== false);
  const activeTask = (overview.tasks || []).find((task) =>
    /running|working|active|creating/i.test(`${task.status || ""} ${task.tone || ""}`),
  );
  const nextWake = cron.find((job) => job.next_run)?.next_run;
  const activeTool = tools.slice().reverse().find((t) => t.status === "running");
  const lastCompleted = tools
    .slice()
    .reverse()
    .find((t) => t.status === "done" || t.status === "error");
  const liveStatus = statusItems[0];

  let headline: string;
  let tone: "active" | "idle" | "sleep";
  if (activeTool) {
    headline = `Working — ${naturalToolLabel(activeTool, registry)}`;
    tone = "active";
  } else if (activeTask) {
    headline = taskLabel(activeTask, registry);
    tone = "active";
  } else if (liveStatus) {
    headline = liveStatus;
    tone = "active";
  } else if (nextWake) {
    headline = `CEO sleeps until ${readableDate(nextWake)}`;
    tone = "sleep";
  } else {
    headline = "CEO is idle";
    tone = "idle";
  }

  const sub = activeTool
    ? toolDetail(activeTool, registry)
    : activeTask
      ? taskDetail(activeTask, registry)
    : lastCompleted
      ? `Last: ${naturalToolLabel(lastCompleted, registry)}${
          lastCompleted.completedAt
            ? ` · ${relativeTime(lastCompleted.completedAt)}`
            : ""
        }`
      : "No tool calls yet this session.";

  return (
    <div className="flex shrink-0 items-start justify-between gap-3 border-b border-zinc-900 px-4 py-3">
      <div className="min-w-0">
        <div className="text-xs uppercase tracking-[0.16em] text-zinc-500">
          Company
        </div>
        <div className="mt-1 truncate text-lg font-semibold text-zinc-100">
          {scope.current?.name || scope.business}
        </div>
        <div className="mt-2 flex flex-wrap items-center gap-2 text-xs">
          <span
            className={cn(
              "inline-flex items-center gap-1.5 rounded-full border px-2 py-0.5",
              tone === "active" && "border-sky-300/30 bg-sky-300/10 text-sky-100",
              tone === "sleep" && "border-zinc-700 bg-zinc-900 text-zinc-300",
              tone === "idle" && "border-amber-300/30 bg-amber-300/10 text-amber-100",
            )}
          >
            {tone === "active" && (
              <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-sky-300" />
            )}
            {headline}
          </span>
          <span className="truncate text-zinc-500">{sub}</span>
        </div>
      </div>
      <div className="flex shrink-0 items-center gap-2">
        <button
          className="inline-flex items-center gap-1.5 rounded-lg bg-zinc-100 px-3 py-2 text-xs font-medium text-zinc-900 transition-colors hover:bg-white disabled:opacity-60"
          disabled={Boolean(activeTool || activeTask)}
          onClick={() => onCommand("/wake")}
          type="button"
        >
          <Play className="h-3.5 w-3.5" />
          Wake CEO
        </button>
      </div>
    </div>
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

function CompanyOverview({
  onCommand,
  onListFiles,
  onReadFile,
  onResolveMedia,
  onResolveSitePreview,
  scope,
  statusItems,
  tools,
}: {
  onCommand: (line: string) => void;
  onListFiles: (path: string) => Promise<BusinessOverviewFile[]>;
  onReadFile: (path: string) => Promise<BusinessFileReadResponse>;
  onResolveMedia: (path: string) => Promise<BusinessMediaResponse>;
  onResolveSitePreview: (path?: string) => Promise<BusinessSitePreviewResponse>;
  scope: ScopeState;
  statusItems: string[];
  tools: ToolEntry[];
}) {
  const overview = scope.overview || {};
  const product = overview.product || {};
  const artifacts = overview.artifacts || {};
  const website = artifacts.website || {};
  const tasks = overview.tasks || [];
  const registry = overview.registry;
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!tools.some((tool) => tool.status === "running")) return;
    const id = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(id);
  }, [tools]);
  const previewPath = website.path || website.source_path || product.source_path || "product/site";
  const publicSiteUrl = customerWebsiteUrl({
    business: scope.business,
    product,
    website,
  });
  const activeTool = tools.slice().reverse().find((tool) => tool.status === "running");
  const latestActivity: Array<ActivityTraceItem | null> = [
    ...(activeTool && !isWorkerTool(activeTool, registry) ? [activityFromTool(activeTool, registry, now)] : []),
    ...tools
      .slice()
      .reverse()
      .filter((tool) => tool.id !== activeTool?.id && !isWorkerTool(tool, registry))
      .slice(0, 3)
      .map((tool) => activityFromTool(tool, registry, now)),
    ...statusItems.slice(0, 3).map((item) => ({
      id: `status:${item}`,
      label: "Live update",
      detail: item,
      status: "working",
      tone: "active",
    })),
    ...tasks.filter((task) => isActionableTask(task) && !isWorkerTask(task, registry)).slice(0, 6).map((task) => activityFromTask(task, registry)),
  ];
  const visibleActivity = compactActivityItems(latestActivity).slice(0, 8);
  const workers = workerItems(tools, overview.workers || [], overview.tasks || [], registry, now);
  const isBusinessBusy = Boolean(activeTool)
    || tasks.some((task) =>
      /(running|working|active|queued|waiting|preparing)/i.test(
        `${task?.status || ""} ${task?.tone || ""}`,
      ))
    || workers.some((worker) =>
      /(running|working|active|queued|waiting|preparing)/i.test(
        `${worker?.status || ""} ${worker?.tone || ""}`,
      ));
  const canonicalRootCards = [
    {
      root: "research",
      label: "Research",
      icon: <Search className="h-4 w-4" />,
      empty: "No research files visible yet.",
    },
    {
      root: "product",
      label: "Product",
      icon: <Globe2 className="h-4 w-4" />,
      empty: "No product files visible yet.",
      primary: publicSiteUrl || website.path ? (
        <ProductPreviewHero
          onResolveSitePreview={onResolveSitePreview}
          previewPath={previewPath}
          publicSiteUrl={publicSiteUrl}
          websitePath={website.path}
        />
      ) : undefined,
    },
    {
      root: "distribution",
      label: "Distribution",
      icon: <MessageCircle className="h-4 w-4" />,
      empty: "No distribution files visible yet.",
    },
    {
      root: "metrics",
      label: "Metrics",
      icon: <Activity className="h-4 w-4" />,
      empty: "No metrics files visible yet.",
    },
  ];
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
  const hasViewer = Boolean(viewer);
  const showAside = Boolean(viewer || workers.length > 0 || visibleActivity.length > 0);

  const workspaceColumn = (
    <div className="grid content-start gap-3">
      <section className="grid gap-3">
        {canonicalRootCards.map((card) => (
          <SourceCard
            icon={card.icon}
            key={card.root}
            label={card.label}
            onOpenDoc={openDocument}
            primary={card.primary}
            status=""
            tone="neutral"
          >
            <BusinessFileBrowser
              autoRefreshIntervalMs={isBusinessBusy ? 2500 : 0}
              initialFiles={EMPTY_BUSINESS_FILES}
              initialPath={card.root}
              onCommand={onCommand}
              onListFiles={onListFiles}
              onReadFile={onReadFile}
              onResolveMedia={onResolveMedia}
            />
          </SourceCard>
        ))}
      </section>
    </div>
  );

  const activityContents = (
      <div className="mt-3 grid gap-2">
        {visibleActivity.map((item, index) => (
          <ActivityTraceRow
            detail={item.detail}
            key={item.id || `${item.label}-${index}`}
            label={item.label}
            rawId={item.rawId}
            status={item.status}
            tone={item.tone}
          />
        ))}
      </div>
  );
  const workersBlock = workers.length > 0 && (
    <section className="rounded-xl border border-zinc-900 bg-zinc-950 px-3 py-2.5">
      <div className="flex items-center gap-2 text-xs font-medium uppercase tracking-[0.14em] text-zinc-500">
        <Users className="h-4 w-4" />
        Delegated work
      </div>
      <div className="mt-3 grid gap-2">
        {workers.map((worker) => (
          <WorkerTraceRow key={worker.id} worker={worker} />
        ))}
      </div>
    </section>
  );
  const activityBlock = visibleActivity.length > 0 && (
      <section className="rounded-xl border border-zinc-900 bg-zinc-950 px-3 py-2.5">
        <div className="flex items-center gap-2 text-xs font-medium uppercase tracking-[0.14em] text-zinc-500">
          <Activity className="h-4 w-4" />
          Recent steps
        </div>
        {activityContents}
      </section>
  );

  if (hasViewer && viewer) {
    return (
      <div className="mx-auto grid w-full max-w-7xl gap-3 p-4 xl:grid-cols-[minmax(0,1fr)_minmax(300px,360px)]">
        <div className="grid content-start gap-3">
          <InlineDocumentViewer
            onClose={() => setViewer(null)}
            onOpenDoc={openDocument}
            viewer={viewer}
          />
        </div>
        <div className="grid content-start gap-3">
          {workspaceColumn}
          {workersBlock}
          {activityBlock}
        </div>
      </div>
    );
  }

  return (
    <div
      className={cn(
        "mx-auto grid w-full max-w-7xl gap-3 p-4",
        showAside && "xl:grid-cols-[minmax(0,1fr)_minmax(320px,420px)]",
      )}
    >
      {workspaceColumn}

      {showAside && (
        <div className="grid content-start gap-3">
          {workersBlock}
          {activityBlock}
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
  primary,
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
  primary?: ReactNode;
  status?: string;
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
        {status && (
          <span className={cn("shrink-0 rounded-full border px-2 py-0.5 text-[0.65rem]", toneClasses(tone || status))}>
            {status}
          </span>
        )}
      </div>
      {primary && <div className="mt-3">{primary}</div>}
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

function ResearchFileList({
  items,
  limit = 12,
  onOpenFile,
}: {
  items: BusinessOverviewResearchOutput[];
  limit?: number;
  onOpenFile?: (path: string) => void;
}) {
  if (items.length === 0) {
    return <EmptyPanelLine text="No research files are visible yet." />;
  }
  return (
    <div className="grid gap-1.5">
      {items.slice(0, limit).map((item) => {
        const path = item.path || "";
        const updated = readableFileTime(item.updated_at);
        const size = formatBytes(item.size);
        const meta = [updated, size].filter(Boolean).join(" · ");
        return (
          <button
            className="flex min-w-0 items-center gap-2 rounded-lg border border-zinc-900 bg-black/30 px-2.5 py-1.5 text-left text-xs text-zinc-400 transition-colors hover:border-zinc-800 hover:bg-zinc-900 hover:text-zinc-100"
            key={path}
            onClick={() => path && onOpenFile?.(path)}
            title={path}
            type="button"
          >
            <FileText className="h-3.5 w-3.5 shrink-0 text-zinc-600" />
            <span className="min-w-0 flex-1">
              <span className="block truncate font-mono text-[0.72rem] text-zinc-300">
                {path}
              </span>
              {meta && (
                <span className="mt-0.5 block truncate text-[0.65rem] text-zinc-600">
                  {meta}
                </span>
              )}
            </span>
          </button>
        );
      })}
    </div>
  );
}

const CHANNEL_DISPLAY: Record<string, string> = {
  show_hn: "Show HN",
  hn: "Hacker News",
  hacker_news: "Hacker News",
  reddit: "Reddit",
  email: "Email",
  twitter: "X (Twitter)",
  x: "X (Twitter)",
  linkedin: "LinkedIn",
  bluesky: "Bluesky",
  threads: "Threads",
  slack: "Slack",
  discord: "Discord",
};

function prettyChannel(channel: string): string {
  const key = channel.toLowerCase();
  if (CHANNEL_DISPLAY[key]) return CHANNEL_DISPLAY[key];
  const redditMatch = key.match(/^reddit[_-](.+)$/);
  if (redditMatch) return `Reddit r/${redditMatch[1]}`;
  return key
    .replace(/[_-]+/g, " ")
    .replace(/\b\w/g, (c) => c.toUpperCase());
}

function parseOutreachDestination(path: string): {
  channel: string;
  target?: string;
  status: "local" | "published";
} | null {
  const match = path.match(
    /^outreach\/(local-published|published)\/([^/]+)\/(.+?)\.md$/i,
  );
  if (!match) return null;
  const status = match[1].toLowerCase() === "published" ? "published" : "local";
  const channel = match[2];
  const filename = match[3];
  let target: string | undefined;
  const fileMatch = filename.match(/^\d{4}-\d{2}-\d{2}-(.+)-[0-9a-f]{6,}$/i);
  if (fileMatch) target = fileMatch[1];
  return { channel, status, target };
}

function resolveSiblingPath(basePath: string, href: string): string {
  const cleanHref = href.split(/[?#]/)[0];
  if (!cleanHref) return basePath;
  if (cleanHref.startsWith("/")) return cleanHref.replace(/^\/+/, "");
  const segments = basePath.split("/").slice(0, -1);
  for (const part of cleanHref.split("/")) {
    if (!part || part === ".") continue;
    if (part === "..") {
      segments.pop();
      continue;
    }
    segments.push(part);
  }
  return segments.join("/");
}

function InlineDocumentViewer({
  onClose,
  onOpenDoc,
  viewer,
}: {
  onClose: () => void;
  onOpenDoc?: (doc: { label?: string; path?: string }) => void;
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
  const outreachDest = parseOutreachDestination(viewer.path);
  const handleLinkClick = useCallback(
    (href: string) => {
      if (/^(https?:|mailto:|tel:)/i.test(href)) return false;
      if (href.startsWith("#")) return false;
      const resolved = resolveSiblingPath(viewer.path, href);
      if (resolved && onOpenDoc) {
        onOpenDoc({ label: compactPath(resolved), path: resolved });
      }
      return true;
    },
    [onOpenDoc, viewer.path],
  );
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
      {outreachDest && (
        <div
          className={cn(
            "border-b px-3 py-2 text-xs",
            outreachDest.status === "published"
              ? "border-emerald-400/20 bg-emerald-400/5 text-emerald-100"
              : "border-amber-300/20 bg-amber-300/5 text-amber-100",
          )}
        >
          <span className="font-medium">
            {outreachDest.status === "published" ? "Published to" : "Would publish to"}
            {" "}
            {prettyChannel(outreachDest.channel)}
          </span>
          {outreachDest.target && (
            <span className="ml-1 font-mono text-[0.7rem] opacity-80">
              → {outreachDest.target}
            </span>
          )}
          {outreachDest.status === "local" && (
            <span className="ml-2 opacity-70">Test mode — draft only, not sent.</span>
          )}
        </div>
      )}
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
        <div className="max-h-[calc(100vh-180px)] overflow-auto px-3 py-3 text-sm leading-6 text-zinc-300 [&_.text-foreground]:text-zinc-100 [&_a]:text-zinc-100 [&_code]:rounded [&_code]:bg-black [&_code]:text-zinc-100 [&_pre]:rounded-xl [&_pre]:border-zinc-800 [&_pre]:bg-black">
          <Markdown content={viewer.content || ""} onLinkClick={handleLinkClick} />
        </div>
      ) : (
        <pre className="max-h-[calc(100vh-180px)] overflow-auto whitespace-pre-wrap p-3 font-mono text-[0.75rem] leading-5 text-zinc-300">
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
  label = "Preview",
  onResolveSitePreview,
  path,
  variant = "compact",
}: {
  label?: string;
  onResolveSitePreview: (path?: string) => Promise<BusinessSitePreviewResponse>;
  path?: string;
  variant?: "compact" | "hero";
}) {
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  const openPreview = useCallback(() => {
    setLoading(true);
    setError("");
    const reservedTab = reserveTabForUserClick();
    void onResolveSitePreview(path)
      .then((res) => {
        if (!res.url) throw new Error("No preview URL returned.");
        if (!navigateReservedTab(reservedTab, res.url)) {
          openUrlInNewTab(res.url);
        }
      })
      .catch((err) => {
        if (reservedTab && !reservedTab.closed) reservedTab.close();
        setError(err instanceof Error ? err.message : String(err));
      })
      .finally(() => setLoading(false));
  }, [onResolveSitePreview, path]);

  if (variant === "hero") {
    return (
      <span className="flex flex-col gap-1">
        <button
          className="inline-flex w-full items-center justify-center gap-2 rounded-xl bg-zinc-100 px-4 py-3 text-sm font-medium text-zinc-900 transition-colors hover:bg-white disabled:opacity-60"
          disabled={loading}
          onClick={openPreview}
          type="button"
        >
          <ExternalLink className="h-4 w-4" />
          {loading ? "Opening preview..." : label}
        </button>
        {error && <span className="text-xs text-red-400">{error}</span>}
      </span>
    );
  }

  return (
    <span className="inline-flex flex-col gap-1">
      <PanelActionButton
        icon={<ExternalLink className="h-3.5 w-3.5" />}
        onClick={openPreview}
      >
        {loading ? "Opening..." : label}
      </PanelActionButton>
      {error && <span className="text-xs text-red-400">{error}</span>}
    </span>
  );
}

function ProductPreviewHero({
  onResolveSitePreview,
  previewPath,
  publicSiteUrl,
  websitePath,
}: {
  onResolveSitePreview: (path?: string) => Promise<BusinessSitePreviewResponse>;
  previewPath?: string;
  publicSiteUrl?: string;
  websitePath?: string;
}) {
  if (publicSiteUrl) {
    return (
      <div className="flex flex-col gap-2">
        <button
          className="inline-flex w-full items-center justify-center gap-2 rounded-xl bg-zinc-100 px-4 py-3 text-sm font-medium text-zinc-900 transition-colors hover:bg-white"
          onClick={() => openUrlInNewTab(publicSiteUrl)}
          type="button"
        >
          <ExternalLink className="h-4 w-4" />
          Open website
        </button>
      </div>
    );
  }
  if (websitePath) {
    return (
      <OpenSitePreviewButton
        label="Open website"
        onResolveSitePreview={onResolveSitePreview}
        path={previewPath}
        variant="hero"
      />
    );
  }
  return (
    <div className="rounded-xl border border-dashed border-zinc-900 px-3 py-3 text-xs text-zinc-600">
      No product source or public URL recorded.
    </div>
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
  const artifacts = overview.artifacts || {};
  const website = artifacts.website || {};
  const sourcePath = website.source_path || product.source_path || "";
  const publicSiteUrl = customerWebsiteUrl({
    business: scope.business,
    product,
    website,
  });
  const previewPath = sourcePath || "product/site";
  const openSitePreview = useCallback(() => {
    setPreviewLoading(true);
    setPreviewError("");
    void onResolveSitePreview(previewPath)
      .then((res) => {
        if (!res.url) throw new Error("No preview URL returned.");
        openUrlInNewTab(res.url);
      })
      .catch((err) => {
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

  const productStatus = publicSiteUrl
    ? "live"
    : sourcePath || website.path
      ? "built_local"
      : "No deliverables";
  const openMessages =
    asNumber(metrics.unresolved_inbound) ||
    asNumber(overview.conversations?.unresolved_messages);
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
        icon={<FileText className="h-4 w-4" />}
        title="Deliverables"
        value={productStatus}
        detail={
          publicSiteUrl ||
          sourcePath ||
          website.path ||
          "No deliverable recorded yet."
        }
      >
        <div className="flex flex-wrap gap-2">
          {sourcePath && (
            <PanelActionButton
              icon={<Folder className="h-3.5 w-3.5" />}
              onClick={() => onCommand(`/files ${sourcePath}`)}
            >
              Source
            </PanelActionButton>
          )}
        </div>
        {(product.verification_status || product.routes_count !== undefined) && (
          <div className="mt-2 text-xs leading-5 text-zinc-600">
            {product.verification_status
              ? `Verification: ${product.verification_status}`
              : `${product.routes_count || 0} routes recorded`}
          </div>
        )}
      </PreviewCard>

      {(publicSiteUrl || sourcePath || website.path) && (
        <PreviewCard
          icon={<Globe2 className="h-4 w-4" />}
          title="Website"
          value={publicSiteUrl ? "Live" : humanizeArtifactStatus(website.status)}
          detail={publicSiteUrl || sourcePath || website.path}
        >
          <div className="flex flex-wrap gap-2">
            {publicSiteUrl && (
              <PanelActionButton
                icon={<ExternalLink className="h-3.5 w-3.5" />}
                onClick={() => openUrlInNewTab(publicSiteUrl)}
              >
                Open
              </PanelActionButton>
            )}
            {website.path && !publicSiteUrl && (
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
            {sourcePath && (
              <PanelActionButton
                icon={<Folder className="h-3.5 w-3.5" />}
                onClick={() => onCommand(`/files ${sourcePath}`)}
              >
                Source
              </PanelActionButton>
            )}
          </div>
          {previewError && <div className="mt-2 text-xs leading-5 text-red-400">{previewError}</div>}
        </PreviewCard>
      )}

      {outreach.path && (
        <PreviewCard
          icon={<MessageCircle className="h-4 w-4" />}
          title="Outreach"
          value={humanizeArtifactStatus(outreach.status)}
          detail={outreach.path}
        >
          <div className="flex flex-wrap gap-2">
            <PanelActionButton
              icon={<FileText className="h-3.5 w-3.5" />}
              onClick={() => onCommand(`/read ${outreach.path}`)}
            >
              Post
            </PanelActionButton>
          </div>
        </PreviewCard>
      )}

      {creativeAssets.path && (
        <PreviewCard
          icon={<Play className="h-4 w-4" />}
          title="Creative assets"
          value={humanizeArtifactStatus(creativeAssets.status)}
          detail={creativeAssets.path}
        >
          <div className="flex flex-wrap gap-2">
            <PanelActionButton
              icon={<Play className="h-3.5 w-3.5" />}
              onClick={() => onCommand(`/files ${creativeAssetsDir}`)}
            >
              Files
            </PanelActionButton>
          </div>
        </PreviewCard>
      )}
    </PanelSection>
  );
}

function BusinessFileBrowser({
  autoRefreshIntervalMs = 0,
  initialFiles,
  initialPath = ".",
  onCommand,
  onListFiles,
  onReadFile,
  onResolveMedia,
}: {
  autoRefreshIntervalMs?: number;
  initialFiles: BusinessOverviewFile[];
  initialPath?: string;
  onCommand: (line: string) => void;
  onListFiles: (path: string) => Promise<BusinessOverviewFile[]>;
  onReadFile?: (path: string) => Promise<BusinessFileReadResponse>;
  onResolveMedia: (path: string) => Promise<BusinessMediaResponse>;
}) {
  const [path, setPath] = useState(initialPath);
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
  const showParentButton = Boolean(parentPath && path !== initialPath);

  useEffect(() => {
    const normalizedPath = initialPath || ".";
    setPath(normalizedPath);
    if (normalizedPath === "." && initialFiles.length > 0) {
      setFiles(initialFiles);
      return;
    }
    void openPath(normalizedPath);
  }, [initialFiles, initialPath, openPath]);

  useEffect(() => {
    if (!autoRefreshIntervalMs || preview || textPreview) return;
    const timer = window.setInterval(() => {
      if (!loading) void openPath(path);
    }, autoRefreshIntervalMs);
    return () => window.clearInterval(timer);
  }, [autoRefreshIntervalMs, loading, openPath, path, preview, textPreview]);

  return (
    <div className="space-y-2">
      <div className="flex min-w-0 items-center gap-2 text-xs text-zinc-600">
        <span className="truncate">/{path === "." ? "" : path}</span>
        {showParentButton && (
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
          {files.map((item) => {
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
  onReadFile,
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
  onReadFile: (path: string) => Promise<BusinessFileReadResponse>;
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
          <NextPanel onCommand={onCommand} onReadFile={onReadFile} scope={scope} />
        )}

        {effectiveTab === "files" && (
          <FilesPanel
            onCommand={onCommand}
            onListFiles={onListFiles}
            onReadFile={onReadFile}
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
  onReadFile,
  scope,
}: {
  onCommand: (line: string) => void;
  onReadFile: (path: string) => Promise<BusinessFileReadResponse>;
  scope: ScopeState;
}) {
  return <TaskBoard onCommand={onCommand} onReadFile={onReadFile} scope={scope} />;
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
          initialFiles={scope.overview?.files || EMPTY_BUSINESS_FILES}
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
  onReadFile,
  scope,
}: {
  onCommand: (line: string) => void;
  onReadFile: (path: string) => Promise<BusinessFileReadResponse>;
  scope: ScopeState;
}) {
  const overview = scope.overview || {};
  const registry = overview.registry;
  const tasks = (overview.tasks || []).filter(isActionableTask);
  const fallbackJobs = (overview.jobs || [])
    .filter((job) => job.kind || job.status)
    .filter(isActionableTask);
  const displayTasks: Array<BusinessOverviewTask | BusinessOverviewJob> =
    tasks.length > 0 ? tasks : fallbackJobs;
  const ceoLoop = overview.ceo_loop;
  const statusCards = overview.status_cards || [];
  const researchOutputs = researchOutputItems(overview);
  const wakeHealth = overview.wake_health;
  const [researchPreview, setResearchPreview] = useState<{
    content?: string;
    error?: string;
    loading?: boolean;
    path: string;
    truncated?: boolean;
  } | null>(null);
  const openResearchFile = useCallback(
    (path: string) => {
      setResearchPreview({ loading: true, path });
      void onReadFile(path)
        .then((res) => setResearchPreview({
          content: res.content || "",
          path: res.path || path,
          truncated: Boolean(res.truncated),
        }))
        .catch((err) => setResearchPreview({
          error: friendlyError(err instanceof Error ? err.message : String(err)),
          path,
        }));
    },
    [onReadFile],
  );

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
        <ResearchFileList items={researchOutputs} onOpenFile={openResearchFile} />
        {researchPreview && (
          <div className="mt-2 overflow-hidden rounded-lg border border-zinc-900 bg-black">
            <div className="flex items-center justify-between gap-2 border-b border-zinc-900 px-3 py-2">
              <div className="min-w-0">
                <div className="truncate text-xs font-medium text-zinc-100">
                  {researchPreview.path.split("/").pop() || researchPreview.path}
                </div>
                <div className="truncate font-mono text-[0.65rem] text-zinc-600">
                  {researchPreview.path}
                </div>
              </div>
              <IconButton label="Close research file" onClick={() => setResearchPreview(null)}>
                <X className="h-4 w-4" />
              </IconButton>
            </div>
            {researchPreview.loading ? (
              <div className="p-3">
                <EmptyPanelLine text="Opening..." />
              </div>
            ) : researchPreview.error ? (
              <div className="p-3">
                <EmptyPanelLine text={researchPreview.error} />
              </div>
            ) : (
              <pre className="max-h-72 overflow-auto whitespace-pre-wrap p-3 font-mono text-[0.72rem] leading-5 text-zinc-300">
                {researchPreview.content || ""}
              </pre>
            )}
            {researchPreview.truncated && (
              <div className="border-t border-zinc-900 px-3 py-2 text-xs text-amber-200">
                Preview truncated.
              </div>
            )}
          </div>
        )}
      </PanelSection>

      <PanelSection icon={<ListChecks className="h-4 w-4" />} title="Tasks">
        {displayTasks.length === 0 ? (
          <EmptyPanelLine text="No task records are visible yet." />
        ) : (
          <div className="grid gap-2 lg:grid-cols-2">
            {displayTasks.slice(0, 12).map((task, index) => (
              <TaskRow
                detail={taskDetail(task, registry)}
                key={task.id || `${taskLabel(task, registry)}-${index}`}
                label={taskLabel(task, registry)}
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
  const registry = scope.overview?.registry;
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
                  detail={toolDetail(tool, registry)}
                  key={tool.id}
                  label={naturalToolLabel(tool, registry)}
                  status={humanizeStatus(tool.status)}
                  tone={tool.status}
                />
              ))}
              {visibleTasks.slice(0, 3).map((task, index) => (
                <TaskRow
                  detail={taskDetail(task, registry)}
                  key={task.id || `${task.source}-${index}`}
                  label={taskLabel(task, registry)}
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
            .map((tool) => <ToolActivityItem key={tool.id} registry={registry} tool={tool} />)
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
  onClick,
  status,
  tone,
}: {
  detail?: string;
  label: string;
  onClick?: () => void;
  status: string;
  tone?: string;
}) {
  const content = (
    <>
      <div className="flex items-center justify-between gap-2">
        <div className="min-w-0 truncate text-sm text-zinc-200">{label}</div>
        <span className="inline-flex shrink-0 items-center gap-1">
          {onClick && <ExternalLink className="h-3.5 w-3.5 text-zinc-600" />}
          <span className={cn("rounded-full border px-2 py-0.5 text-[0.65rem]", toneClasses(tone || status))}>
            {status}
          </span>
        </span>
      </div>
      {detail && (
        <div className="mt-0.5 truncate text-xs leading-5 text-zinc-600">
          {detail}
        </div>
      )}
    </>
  );
  const className = cn(
    "w-full rounded-xl border border-zinc-900 bg-zinc-950 px-3 py-2",
    onClick && "text-left transition-colors hover:border-zinc-700 hover:bg-zinc-900",
  );
  if (onClick) {
    return (
      <button className={className} onClick={onClick} title={detail || label} type="button">
        {content}
      </button>
    );
  }
  return (
    <div className={className}>
      {content}
    </div>
  );
}

function ActivityTraceRow({
  detail,
  label,
  rawId,
  status,
  tone,
}: {
  detail?: string;
  label: string;
  rawId?: string;
  status: string;
  tone?: string;
}) {
  const cleanedStatus = cleanTraceStatus(status);
  const cleanedDetail = cleanTraceDetail(
    detail && detail !== `raw: ${rawId || ""}` ? detail : "",
    cleanedStatus,
    label,
  );
  const debug = rawId && !detail?.includes(rawId) ? `raw: ${rawId}` : "";
  const active = /active|running|working/i.test(`${tone || ""} ${cleanedStatus || ""}`);
  const statusTone = `${tone || ""} ${cleanedStatus || ""}`;
  return (
    <div className="rounded-lg border border-zinc-900 bg-black/30 px-3 py-2">
      <div className="flex min-w-0 items-start gap-2">
        <span
          className={cn(
            "mt-2 h-1.5 w-1.5 shrink-0 rounded-full",
            active ? "animate-pulse bg-sky-300" : "bg-zinc-700",
          )}
        />
        <div className="min-w-0 flex-1">
          <div className="flex min-w-0 items-start justify-between gap-3">
            <div className="break-words text-sm leading-6 text-zinc-300">
              {label}
            </div>
            {cleanedStatus && (
              <span
                className={cn(
                  "shrink-0 rounded-full px-2 py-0.5 text-[0.65rem]",
                  /active|running|working/i.test(statusTone) && "bg-sky-300/10 text-sky-100",
                  /done|complete|success/i.test(statusTone) && "bg-emerald-400/10 text-emerald-200",
                  /attention|blocked|error|fail/i.test(statusTone) && "bg-red-400/10 text-red-200",
                  !/active|running|working|done|complete|success|attention|blocked|error|fail/i.test(statusTone) && "bg-zinc-900 text-zinc-400",
                )}
              >
                {cleanedStatus}
              </span>
            )}
          </div>
          {cleanedDetail && (
            <div className="mt-0.5 break-words text-xs leading-5 text-zinc-500">
              {cleanedDetail}
            </div>
          )}
          {debug && (
            <div className="mt-0.5 break-words font-mono text-[0.68rem] leading-5 text-zinc-600">
              {debug}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function WorkerTraceRow({ worker }: { worker: WorkerDisplayItem }) {
  const active = /active|running|working/i.test(`${worker.tone || ""} ${worker.status || ""}`);
  return (
    <div className="rounded-lg border border-zinc-900 bg-black/30 px-3 py-2">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex min-w-0 items-center gap-2">
            <span
              className={cn(
                "h-1.5 w-1.5 shrink-0 rounded-full",
                active ? "animate-pulse bg-sky-300" : "bg-zinc-700",
              )}
            />
            <div className="truncate text-sm font-medium text-zinc-200">{worker.name}</div>
          </div>
          {worker.purpose && (
            <div className="mt-1 break-words text-xs leading-5 text-zinc-500">
              {worker.purpose}
            </div>
          )}
          {worker.latestDetail && (
            <div className="mt-1 break-words text-xs leading-5 text-zinc-400">
              {worker.latestDetail}
            </div>
          )}
          {worker.rawId && (
            <div className="mt-1 break-words font-mono text-[0.68rem] leading-5 text-zinc-600">
              raw: {worker.rawId}
            </div>
          )}
        </div>
        <span
          className={cn(
            "shrink-0 rounded-full px-2 py-0.5 text-[0.65rem]",
            active && "bg-sky-300/10 text-sky-100",
            !active && /done|complete|success/.test(worker.status) && "bg-emerald-400/10 text-emerald-200",
            !active && /attention|blocked|error|fail/.test(worker.status) && "bg-red-400/10 text-red-200",
            !active && !/done|complete|success|attention|blocked|error|fail/.test(worker.status) && "bg-zinc-900 text-zinc-400",
          )}
        >
          {worker.status}
        </span>
      </div>
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

function ToolActivityItem({
  registry,
  tool,
}: {
  registry?: RegistryDisplayPayload;
  tool: ToolEntry;
}) {
  return (
    <div className="rounded-xl border border-zinc-900 bg-zinc-950 px-3 py-2">
      <div className="flex items-center justify-between gap-2">
        <div className="min-w-0 truncate text-sm text-zinc-200">{naturalToolLabel(tool, registry)}</div>
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
          {toolDetail(tool, registry)}
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
