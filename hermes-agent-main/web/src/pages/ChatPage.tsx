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
  Command,
  DollarSign,
  ExternalLink,
  FileText,
  Folder,
  Gauge,
  Globe2,
  MessageCircle,
  PanelRight,
  Play,
  Plus,
  RefreshCw,
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
}

interface BusinessOverviewConversations {
  active_threads?: number;
  unresolved_messages?: number;
  latest_message_at?: string;
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
  posts?: BusinessOverviewPost[];
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
const CHAT_UI_REVISION = "chat-creative-assets-2026-05-24";
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

function wakeupLabel(job: BusinessOverviewCron): string {
  if (/takyon-ceo|ceo/i.test(job.name || "")) return "CEO check-in";
  return job.name || "Scheduled check-in";
}

function wakeupDetail(job: BusinessOverviewCron): string {
  const parts = [
    "Review replies, usage, blockers, and choose the next action",
    job.schedule,
    job.next_run ? `next ${readableDate(job.next_run)}` : "",
  ];
  return parts.filter(Boolean).join(" · ");
}

function compactPath(path?: string): string {
  if (!path) return "";
  if (path.length <= 34) return path;
  const parts = path.split("/");
  if (parts.length <= 2) return path.slice(0, 31) + "...";
  return `${parts[0]}/.../${parts[parts.length - 1]}`;
}

function isExternalUrl(value?: string): boolean {
  return /^https?:\/\//i.test(value || "");
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
  const [createInTestMode, setCreateInTestMode] = useState(loadCreateInTestModeDefault);
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
          const activeSessionId = sessionIdRef.current;
          if (activeSessionId) {
            void gw
              .request<ScopeState>(
                "takyon.scope.get",
                { session_id: activeSessionId },
                10_000,
              )
              .then((scope) => {
                if (!cancelled) {
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
                }
              })
              .catch(() => {
                /* scope refresh is best effort */
              });
          }
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

  return (
    <div className="flex h-full min-h-0 w-full flex-col overflow-hidden bg-black normal-case text-zinc-100 [font-family:Inter,ui-sans-serif,system-ui,-apple-system,BlinkMacSystemFont,'Segoe_UI',sans-serif]">
      <PluginSlot name="chat:top" />

      {rightOpen && (
        <button
          aria-label="Close side panel"
          onClick={() => setRightOpen(false)}
          className="fixed inset-0 z-[55] bg-black/70 backdrop-blur-sm lg:hidden"
          type="button"
        />
      )}

      <div className="grid min-h-0 min-w-0 flex-1 bg-black lg:grid-cols-[minmax(0,1fr)_380px]">
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
              <IconButton
                label="Open side panel"
                onClick={() => setRightOpen(true)}
                className="lg:hidden"
              >
                <PanelRight className="h-4 w-4" />
              </IconButton>
              <HeaderLinkActionButton href={newChatHref} label="New chat">
                <Plus className="h-4 w-4" />
              </HeaderLinkActionButton>
              <IconButton label="Reconnect chat" onClick={reconnect}>
                <RefreshCw className="h-4 w-4" />
              </IconButton>
            </div>
          </header>

          <Thread
            error={error}
            messages={messages}
            running={running}
            scope={scopeState}
            scrollerRef={scrollerRef}
          >
            <Composer
              canAct={canAct}
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
        </main>

        <aside
          className={cn(
            "min-h-0 overflow-hidden border-l border-zinc-900 bg-black",
            "lg:relative lg:z-auto lg:flex",
            rightOpen
              ? "fixed inset-y-0 right-0 z-[60] flex w-[min(92vw,390px)]"
              : "hidden",
          )}
        >
          <DeliverablesPanel
            createInTestMode={createInTestMode}
            cwd={info.cwd}
            deliverables={deliverables}
            historicalOutputs={
              historicalOutputs.business === scopeState.business
                ? historicalOutputs.items
                : []
            }
            onCommand={runTakyonLine}
            onCreateInTestModeChange={setCreateInTestMode}
            onListFiles={listBusinessFiles}
            onResolveMedia={resolveBusinessMedia}
            onClose={() => setRightOpen(false)}
            scope={scopeState}
            sessionId={sessionId}
            showClose={rightOpen}
            statusItems={statusItems}
            tools={tools}
          />
        </aside>
      </div>

      <PluginSlot name="chat:bottom" />
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
}: {
  children: ReactNode;
  error: string | null;
  messages: ChatMessage[];
  running: boolean;
  scope: ScopeState;
  scrollerRef: RefObject<HTMLDivElement | null>;
}) {
  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div
        ref={scrollerRef}
        className="min-h-0 flex-1 overflow-y-auto px-4 py-6 sm:px-6"
      >
        <div className="mx-auto flex min-h-full w-full max-w-3xl flex-col">
          {error && <ErrorBanner message={error} />}
          {messages.length === 0 ? (
            <ThreadWelcome scope={scope} />
          ) : (
            <div className="space-y-6 pb-6">
              {messages.map((message) => (
                <Message key={message.id} message={message} />
              ))}
              {running && <LoadingIndicator />}
            </div>
          )}
        </div>
      </div>
      <div className="mx-auto w-full max-w-3xl px-4 pb-4 sm:px-6 sm:pb-6">
        {children}
      </div>
    </div>
  );
}

function ThreadWelcome({ scope }: { scope: ScopeState }) {
  const inBusiness = !!scope.business;
  return (
    <div className="flex flex-1 items-center justify-center py-10 text-center">
      <div>
        <h2 className="text-xl font-medium text-zinc-100">What should Takyon work on?</h2>
        <p className="mt-2 text-sm text-zinc-500">
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
      <div className="flex items-end gap-2 rounded-3xl border border-zinc-800 bg-zinc-950 px-3 py-2 shadow-[0_0_0_1px_rgba(255,255,255,0.02)] transition-colors focus-within:border-zinc-600">
        <textarea
          ref={inputRef}
          aria-label="Message input"
          autoFocus
          className="max-h-36 min-h-10 flex-1 resize-none bg-transparent py-2 text-sm leading-6 text-zinc-100 outline-none placeholder:text-zinc-600"
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

function Message({ message }: { message: ChatMessage }) {
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
          "max-w-[86%] break-words text-sm leading-6",
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

function LoadingIndicator() {
  return (
    <div className="flex items-center gap-1.5 py-2">
      <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-zinc-500 [animation-delay:-0.3s]" />
      <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-zinc-500 [animation-delay:-0.15s]" />
      <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-zinc-500" />
    </div>
  );
}

function ErrorBanner({ message }: { message: string }) {
  return (
    <div className="mb-4 flex items-start gap-2 rounded-xl border border-red-500/30 bg-red-500/10 px-3 py-2 text-sm text-red-200">
      <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />
      <span className="min-w-0 whitespace-pre-wrap">{message}</span>
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
  scope,
}: {
  onCommand: (line: string) => void;
  scope: ScopeState;
}) {
  const overview = scope.overview || {};
  const metrics = overview.metrics || {};
  const product = overview.product || {};
  const budget = overview.budget || {};
  const cron = overview.cron || [];
  const jobs = overview.jobs || [];

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
        icon={<Clock3 className="h-4 w-4" />}
        title="Wakeups"
        value={activeCron.length ? `${activeCron.length} active` : "None active"}
        detail={nextWake ? `Next wake ${readableDate(nextWake)}` : "No scheduled CEO wake is visible."}
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
  onResolveMedia,
}: {
  initialFiles: BusinessOverviewFile[];
  onCommand: (line: string) => void;
  onListFiles: (path: string) => Promise<BusinessOverviewFile[]>;
  onResolveMedia: (path: string) => Promise<BusinessMediaResponse>;
}) {
  const [path, setPath] = useState(".");
  const [files, setFiles] = useState(initialFiles);
  const [preview, setPreview] = useState<BusinessMediaResponse | null>(null);
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
                    void onResolveMedia(itemPath).then(setPreview).catch((err) => {
                      setError(err instanceof Error ? err.message : String(err));
                    });
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

function PanelLinkButton({
  children,
  href,
  icon,
}: {
  children: ReactNode;
  href: string;
  icon: ReactNode;
}) {
  return (
    <a
      className="inline-flex h-7 items-center gap-1.5 rounded-lg border border-zinc-800 bg-black px-2.5 text-xs text-zinc-300 transition-colors hover:border-zinc-700 hover:bg-zinc-900 hover:text-zinc-50"
      href={href}
      rel="noreferrer"
      target="_blank"
    >
      {icon}
      <span>{children}</span>
    </a>
  );
}

function CreateModeToggle({
  enabled,
  onChange,
}: {
  enabled: boolean;
  onChange: (enabled: boolean) => void;
}) {
  return (
    <div className="mb-4 rounded-xl border border-zinc-900 bg-zinc-950 px-3 py-2.5">
      <div className="flex items-center justify-between gap-3">
        <div className="min-w-0">
          <div className="text-sm font-medium text-zinc-100">New businesses</div>
          <div className="mt-0.5 text-xs leading-5 text-zinc-600">
            {enabled
              ? "Created in test mode unless you type --live."
              : "Created with the command default unless you type --test."}
          </div>
        </div>
        <button
          aria-label={
            enabled
              ? "Disable new business test mode default"
              : "Enable new business test mode default"
          }
          aria-pressed={enabled}
          className={cn(
            "flex h-7 w-12 shrink-0 items-center rounded-full border p-0.5 transition-colors",
            enabled
              ? "border-emerald-400/40 bg-emerald-400/15"
              : "border-zinc-800 bg-black",
          )}
          onClick={() => onChange(!enabled)}
          type="button"
        >
          <span
            className={cn(
              "h-5 w-5 rounded-full transition-transform",
              enabled
                ? "translate-x-5 bg-emerald-300"
                : "translate-x-0 bg-zinc-600",
            )}
          />
        </button>
      </div>
    </div>
  );
}

function DeliverablesPanel({
  createInTestMode,
  cwd,
  deliverables,
  historicalOutputs,
  onCommand,
  onCreateInTestModeChange,
  onListFiles,
  onResolveMedia,
  onClose,
  scope,
  sessionId,
  showClose,
  statusItems,
  tools,
}: {
  createInTestMode: boolean;
  cwd?: string;
  deliverables: Deliverable[];
  historicalOutputs: Deliverable[];
  onCommand: (line: string) => void;
  onCreateInTestModeChange: (enabled: boolean) => void;
  onListFiles: (path: string) => Promise<BusinessOverviewFile[]>;
  onResolveMedia: (path: string) => Promise<BusinessMediaResponse>;
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
            <CreateModeToggle
              enabled={createInTestMode}
              onChange={onCreateInTestModeChange}
            />
            <BusinessSnapshot onCommand={onCommand} scope={scope} />
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
          <OutputsPanel onResolveMedia={onResolveMedia} outputs={outputs} />
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
  const overview = scope.overview || {};
  const metrics = overview.metrics || {};
  const cron = overview.cron || [];
  const jobs = overview.jobs || [];
  const posts = overview.posts || [];
  const unresolvedReplies = asNumber(overview.conversations?.unresolved_messages);
  const usageEvents = asNumber(metrics.usage_events);
  const checkoutIntents = asNumber(metrics.checkout_intents);
  const blockedJobs = jobs.filter((job) =>
    /blocked|error|fail|paused/i.test(`${job.status || ""} ${job.kind || ""}`),
  );
  const gatedActions = jobs.filter((job) => job.kind || job.status);
  const activeCron = cron.filter((job) => job.enabled !== false);
  const nextWake = activeCron.find((job) => job.next_run)?.next_run;
  const hasWaitingSignal =
    posts.length > 0 || unresolvedReplies > 0 || usageEvents > 0 || checkoutIntents > 0;

  return (
    <div className="space-y-6">
      <PanelSection icon={<Gauge className="h-4 w-4" />} title="Overview">
        <div className="grid grid-cols-2 gap-2">
          <SnapshotMetric
            icon={<Activity className="h-3.5 w-3.5" />}
            label="Gated"
            value={formatCount(gatedActions.length)}
          />
          <SnapshotMetric
            icon={<AlertCircle className="h-3.5 w-3.5" />}
            label="Blocked"
            value={formatCount(blockedJobs.length)}
          />
          <SnapshotMetric
            icon={<Clock3 className="h-3.5 w-3.5" />}
            label="Wakeups"
            value={formatCount(activeCron.length)}
            detail={nextWake ? readableDate(nextWake) : "none scheduled"}
          />
          <SnapshotMetric
            icon={<MessageCircle className="h-3.5 w-3.5" />}
            label="Replies"
            value={formatCount(unresolvedReplies)}
            detail={`${formatCount(posts.length)} posts`}
          />
        </div>
      </PanelSection>

      <PanelSection icon={<MessageCircle className="h-4 w-4" />} title="Current Posts">
        {posts.length === 0 ? (
          <EmptyPanelLine text="No posts or outreach threads recorded yet." />
        ) : (
          posts.map((post, index) => (
            <PostItem
              key={post.id || `${post.source}-${post.title}-${index}`}
              onCommand={onCommand}
              post={post}
            />
          ))
        )}
      </PanelSection>

      <PanelSection icon={<Clock3 className="h-4 w-4" />} title="Waiting">
        {!hasWaitingSignal ? (
          <EmptyPanelLine text="No waiting signals recorded. On wakeup, the CEO checks current state before choosing work." />
        ) : (
          <div className="space-y-2">
            {posts.length > 0 && (
              <TaskRow
                detail={`${formatCount(unresolvedReplies)} unresolved replies across ${formatCount(posts.length)} recorded posts`}
                label="Watch outreach responses"
                status="signal"
              />
            )}
            {(usageEvents > 0 || checkoutIntents > 0) && (
              <TaskRow
                detail={`${formatCount(usageEvents)} usage events · ${formatCount(checkoutIntents)} checkout intents`}
                label="Watch product/customer signal"
                status="signal"
              />
            )}
          </div>
        )}
      </PanelSection>

      <PanelSection icon={<Activity className="h-4 w-4" />} title="Gated Actions">
        {gatedActions.length === 0 ? (
          <EmptyPanelLine text="No gated follow-up actions recorded." />
        ) : (
          gatedActions.map((job, index) => (
            <TaskRow
              detail={gatedActionDetail(job)}
              key={job.id || `${job.kind}-${index}`}
              label={humanizeJobKind(job.kind)}
              status={job.status || "recorded"}
            />
          ))
        )}
      </PanelSection>

      <PanelSection icon={<Clock3 className="h-4 w-4" />} title="Wakeups">
        {cron.length === 0 ? (
          <EmptyPanelLine text="No CEO wakeups are scheduled." />
        ) : (
          cron.map((job, index) => (
            <TaskRow
              detail={wakeupDetail(job)}
              key={job.id || `${job.name}-${index}`}
              label={wakeupLabel(job)}
              status={job.enabled === false ? "off" : job.state || "scheduled"}
            />
          ))
        )}
        <div className="flex flex-wrap gap-2 pt-1">
          <PanelActionButton
            icon={<Clock3 className="h-3.5 w-3.5" />}
            onClick={() => onCommand("/cron list")}
          >
            List
          </PanelActionButton>
          <PanelActionButton
            icon={<Play className="h-3.5 w-3.5" />}
            onClick={() => onCommand("/wake")}
          >
            Wake now
          </PanelActionButton>
        </div>
      </PanelSection>
    </div>
  );
}

function PostItem({
  onCommand,
  post,
}: {
  onCommand: (line: string) => void;
  post: BusinessOverviewPost;
}) {
  const external = isExternalUrl(post.url);
  const testPost = post.mode === "test" || !external;
  const artifactPath = post.artifact_path || (!external ? post.url : "");
  const updated = post.updated_at || post.created_at;

  return (
    <div className="rounded-xl border border-zinc-900 bg-zinc-950 px-3 py-2.5">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="truncate text-sm font-medium text-zinc-100">
            {post.title || "Post"}
          </div>
          <div className="mt-0.5 flex min-w-0 flex-wrap gap-x-2 gap-y-1 text-[0.68rem] text-zinc-600">
            <span>{post.source || "outreach"}</span>
            <span>{testPost ? "test/local" : "live"}</span>
            {updated && <span>{readableDate(updated)}</span>}
          </div>
        </div>
        <span className="shrink-0 rounded-full bg-zinc-900 px-2 py-0.5 text-[0.65rem] text-zinc-500">
          {post.unresolved_messages ? `${post.unresolved_messages} replies` : post.status || "active"}
        </span>
      </div>
      <div className="mt-3 flex flex-wrap gap-2">
        {external && (
          <PanelLinkButton href={post.url || ""} icon={<ExternalLink className="h-3.5 w-3.5" />}>
            Open post
          </PanelLinkButton>
        )}
        {artifactPath && (
          <PanelActionButton
            icon={<FileText className="h-3.5 w-3.5" />}
            onClick={() => onCommand(`/read ${artifactPath}`)}
          >
            Local post
          </PanelActionButton>
        )}
        {post.conversation_file && (
          <PanelActionButton
            icon={<MessageCircle className="h-3.5 w-3.5" />}
            onClick={() => onCommand(`/read ${post.conversation_file}`)}
          >
            Responses
          </PanelActionButton>
        )}
      </div>
    </div>
  );
}

function FilesPanel({
  onCommand,
  onListFiles,
  onResolveMedia,
  scope,
}: {
  onCommand: (line: string) => void;
  onListFiles: (path: string) => Promise<BusinessOverviewFile[]>;
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
          onResolveMedia={onResolveMedia}
        />
      </div>
    </PanelSection>
  );
}

function OutputsPanel({
  onResolveMedia,
  outputs,
}: {
  onResolveMedia: (path: string) => Promise<BusinessMediaResponse>;
  outputs: Deliverable[];
}) {
  return (
    <PanelSection icon={<FileText className="h-4 w-4" />} title="Outputs">
      {outputs.length === 0 ? (
        <EmptyPanelLine text="No historical or current-session outputs yet." />
      ) : (
        outputs.map((item) => (
          <DeliverableItem item={item} key={item.id} onResolveMedia={onResolveMedia} />
        ))
      )}
    </PanelSection>
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
  return (
    <div className="space-y-6">
      <PanelSection icon={<Command className="h-4 w-4" />} title="Build">
        <TaskRow
          detail={scopeDetail(scope)}
          label={CHAT_UI_REVISION}
          status="ui"
        />
        <TaskRow
          detail={cwd || "cwd unavailable"}
          label={sessionId ? `session ${sessionId}` : "session pending"}
          status="runtime"
        />
      </PanelSection>

      <PanelSection icon={<CheckCircle2 className="h-4 w-4" />} title="Tool activity">
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
}: {
  detail?: string;
  label: string;
  status: string;
}) {
  return (
    <div className="rounded-xl border border-zinc-900 bg-zinc-950 px-3 py-2">
      <div className="flex items-center justify-between gap-2">
        <div className="min-w-0 truncate text-sm text-zinc-200">{label}</div>
        <span className="shrink-0 rounded-full bg-zinc-900 px-2 py-0.5 text-[0.65rem] text-zinc-500">
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
  onResolveMedia,
}: {
  item: Deliverable;
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
      {item.path && mediaKind && (
        <div className="mt-2 flex flex-wrap gap-2">
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
        <div className="min-w-0 truncate text-sm text-zinc-200">{tool.name}</div>
        <span
          className={cn(
            "shrink-0 rounded-full px-2 py-0.5 text-[0.65rem]",
            tool.status === "running" && "bg-amber-400/10 text-amber-200",
            tool.status === "done" && "bg-emerald-400/10 text-emerald-200",
            tool.status === "error" && "bg-red-400/10 text-red-200",
          )}
        >
          {tool.status}
        </span>
      </div>
      {(tool.error || tool.summary || tool.preview || tool.context) && (
        <div className="mt-1 line-clamp-3 text-xs leading-5 text-zinc-500">
          {tool.error || tool.summary || tool.preview || tool.context}
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
