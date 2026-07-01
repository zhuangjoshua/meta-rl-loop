import {
  Component,
  useEffect,
  useRef,
  useState,
  type CSSProperties,
  type ErrorInfo,
  type KeyboardEvent as ReactKeyboardEvent,
  type PointerEvent as ReactPointerEvent,
  type ReactNode,
} from "react";
import ReactMarkdown from "react-markdown";
import remarkBreaks from "remark-breaks";
import remarkGfm from "remark-gfm";
import type {
  TakyonBusinessCreativeCreditsResponse,
  TakyonBusinessTractionResponse,
  TakyonBusinessWorkspaceResponse,
} from "@/lib/api";
import {
  buildTakyonBusinessArtifactUrl,
  buildTakyonBusinessAssetUrl,
  buildTakyonBusinessSitePreviewFrameUrl,
} from "@/lib/api";
import {
  chatStreamAgentMessages,
  liveWorkSteps,
  sanitizeCustomerReply,
  sanitizeTaskErrorText,
  workspaceChatRunning,
  workspaceChatSummary,
  type ChatStreamMessage,
  type LiveWorkStep,
} from "@/lib/takyonCeoUpdates";
import { Tabs, Textarea } from "../composer-ui/lib";
import type { Theme } from "../App";
import type { SettingsSection } from "../settings/Settings";
import type { ChatMessage, LitebulbBusiness } from "../takyon/useTakyonLitebulb";
import { CompanyTab } from "./CompanyTab";
import { BulbMark } from "../shared/icons";
import "./product.css";

const S = (p: { d: string; w?: number; fill?: string }) => (
  <svg width={p.w ?? 14} height={p.w ?? 14} viewBox="0 0 16 16" fill={p.fill ?? "none"} stroke={p.fill ? "none" : "currentColor"} strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round">
    <path d={p.d} />
  </svg>
);

// Append-only agent transcript: merge each poll's chat_stream into a per-instance
// accumulator so a transient short/empty server snapshot never removes a turn that
// is already on screen. Product is keyed by business, so this resets per business.
// Bubble identity is the stable `ceo-turn:<posted_at>` id (text updates in place).
function useAccumulatedAgentStream(
  workspace: TakyonBusinessWorkspaceResponse | null,
): ChatStreamMessage[] {
  const [stream, setStream] = useState<ChatStreamMessage[]>(() => chatStreamAgentMessages(workspace));
  const byKey = useRef<Map<string, ChatStreamMessage>>(new Map());
  const order = useRef<string[]>([]);
  useEffect(() => {
    let changed = false;
    for (const msg of chatStreamAgentMessages(workspace)) {
      const key = msg.id || msg.text;
      const prev = byKey.current.get(key);
      if (!prev) {
        order.current.push(key);
        byKey.current.set(key, msg);
        changed = true;
      } else if (prev.text !== msg.text) {
        byKey.current.set(key, msg);
        changed = true;
      }
    }
    if (changed) setStream(order.current.map((key) => byKey.current.get(key)!));
  }, [workspace]);
  return stream;
}

// Append-only live work view: merge each poll's liveWorkSteps into a per-business
// accumulator so a transient short/empty trace snapshot never removes a step that
// is already on screen. The view GROWS as the CEO works (a chat turn or a wake)
// and a step's status updates in place (running -> completed) — it never
// truncates or gets replaced mid-turn. `running` (the live in-flight signal)
// drives the reset: when a turn settles AND the server reports no live steps, the
// accumulator clears so the NEXT turn starts fresh rather than re-showing the last
// turn's steps. Keyed by business via the caller's remount; mirrors
// useAccumulatedAgentStream.
function useAccumulatedWorkSteps(
  workspace: TakyonBusinessWorkspaceResponse | null,
  active: boolean,
): LiveWorkStep[] {
  const [steps, setSteps] = useState<LiveWorkStep[]>([]);
  const byKey = useRef<Map<string, LiveWorkStep>>(new Map());
  const order = useRef<string[]>([]);
  // Was the turn active on the previous poll? Used to clear the accumulator
  // exactly once, on the running->idle edge with no live steps, so a settled
  // turn's steps don't bleed into the next one.
  const wasActive = useRef(false);
  useEffect(() => {
    const live = liveWorkSteps(workspace);
    if (!active && wasActive.current && live.length === 0) {
      // Turn just settled and nothing live remains — reset for the next turn.
      byKey.current.clear();
      order.current = [];
      if (steps.length) setSteps([]);
      wasActive.current = false;
      return;
    }
    if (active) wasActive.current = true;
    let changed = false;
    for (const step of live) {
      const key = step.id;
      const prev = byKey.current.get(key);
      if (!prev) {
        order.current.push(key);
        byKey.current.set(key, step);
        changed = true;
      } else if (prev.status !== step.status || prev.label !== step.label || prev.detail !== step.detail) {
        byKey.current.set(key, step);
        changed = true;
      }
    }
    if (changed) setSteps(order.current.map((key) => byKey.current.get(key)!));
  }, [workspace, active, steps.length]);
  return steps;
}

const Icon = {
  caret: <S d="M4 6l4 4 4-4" w={12} />,
  user: <S d="M5.4 6a2.6 2.6 0 105.2 0 2.6 2.6 0 00-5.2 0zM3 13.5c0-2.6 2.2-4 5-4s5 1.4 5 4" />,
  sun: <S d="M8 11a3 3 0 100-6 3 3 0 000 6zM8 1v1.5M8 13.5V15M1 8h1.5M13.5 8H15M3 3l1 1M12 12l1 1M13 3l-1 1M4 12l-1 1" />,
  moon: <S d="M13 9.5A5.5 5.5 0 016.5 3a5.5 5.5 0 100 11A5.5 5.5 0 0013 9.5z" />,
  monitor: <S d="M2 3.5h12v8H2zM6 14h4M8 11.5V14" />,
  phone: <S d="M5 1.5h6v13H5zM7 12.5h2" />,
  send: <S d="M14 8L2 2.5l2.2 5.5L2 13.5z" />,
  stop: <S d="M5 5h6v6H5z" fill="currentColor" />,
  refresh: <S d="M13 7a5 5 0 10-.6 3.4M13 4v3h-3" />,
  external: <S d="M9 3h4v4M13 3l-6 6M11 9v3.5H3.5V5H7" />,
  collapse: <S d="M9.5 4l-4 4 4 4" w={13} />,
  chat: <S d="M2 3.5h12v7H6.5l-3 2.5v-2.5H2z" />,
};

// Canonical product domain. Product sub-apps are served at
// `<slug>.coscale.app` (see takyon_cli/web_server._company_base_domain and
// core._product_publish_target). The address bar must show this real canonical
// host — never a fabricated `.app` placeholder.
const PRODUCT_BASE_DOMAIN = "coscale.app";
const CHAT_ARTIFACT_ROOTS = [
  "product/",
  "distribution/",
  "research/",
  "brain/",
  "metrics/",
  "outreach/",
  "campaigns/",
  "conversation/",
  "app/",
  "memory/",
];
const CHAT_MEDIA_SUFFIXES = new Set([".png", ".jpg", ".jpeg", ".webp", ".gif", ".mp4", ".mov", ".webm", ".m4v"]);

// Canonical expected product host derived from the business slug. Used only as a
// last-resort fallback when neither the published public_url nor the backend
// publish_target is available yet.
function canonicalProductHost(slug: string) {
  const clean = (slug || "").toLowerCase().replace(/[^a-z0-9-]/g, "");
  return clean ? `${clean}.${PRODUCT_BASE_DOMAIN}` : "";
}

// Strip scheme/trailing slash so the address bar reads as a clean host+path.
function addressBarText(url: string) {
  return (url || "").replace(/^https?:\/\//i, "").replace(/\/$/, "");
}

function outputSuffix(path: string) {
  const clean = String(path || "").trim().toLowerCase();
  const index = clean.lastIndexOf(".");
  return index >= 0 ? clean.slice(index) : "";
}

function normalizeArtifactPath(value: string) {
  return String(value || "").trim().replace(/^\/+/, "");
}

function isHttpUrl(value: string) {
  return /^https?:\/\//i.test(String(value || "").trim());
}

function looksLikeBusinessArtifactPath(value: string) {
  const path = normalizeArtifactPath(value);
  if (!path || isHttpUrl(path)) return false;
  if (path === "product/site" || path === "product/site/index.html") return true;
  return CHAT_ARTIFACT_ROOTS.some((prefix) => path.startsWith(prefix));
}

function resolveBusinessArtifactHref(slug: string, value: string) {
  const target = normalizeArtifactPath(value);
  if (!target) return "";
  if (isHttpUrl(target)) return target;
  const suffix = outputSuffix(target);
  if (CHAT_MEDIA_SUFFIXES.has(suffix)) {
    return buildTakyonBusinessAssetUrl(slug, target);
  }
  if (
    target === "product/site"
    || target === "product/site/index.html"
    || (target.startsWith("product/site/") && (suffix === "" || suffix === ".html" || suffix === ".htm"))
  ) {
    return buildTakyonBusinessSitePreviewFrameUrl(slug);
  }
  if (looksLikeBusinessArtifactPath(target)) {
    return buildTakyonBusinessArtifactUrl(slug, target);
  }
  return "";
}

function escapeRegExp(value: string) {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

function collectWorkspaceArtifactPaths(workspace: TakyonBusinessWorkspaceResponse | null): string[] {
  const seen = new Set<string>();
  const add = (value: unknown) => {
    const path = normalizeArtifactPath(typeof value === "string" ? value : "");
    if (looksLikeBusinessArtifactPath(path)) {
      seen.add(path);
    }
  };
  const addRecordPath = (value: unknown) => {
    if (!value || typeof value !== "object") return;
    add((value as { path?: unknown }).path);
  };

  for (const item of Array.isArray(workspace?.deliverables) ? workspace.deliverables : []) {
    addRecordPath(item);
  }
  for (const item of Array.isArray(workspace?.outputs) ? workspace.outputs : []) {
    addRecordPath(item);
  }
  const liveState = workspace?.live_state;
  const tasks = liveState && typeof liveState === "object"
    ? (liveState as { tasks?: unknown }).tasks
    : null;
  for (const task of Array.isArray(tasks) ? tasks : []) {
    if (!task || typeof task !== "object") continue;
    const outputs = (task as { outputs?: unknown }).outputs;
    for (const output of Array.isArray(outputs) ? outputs : []) {
      add(typeof output === "string" ? output : "");
    }
  }
  return [...seen];
}

function linkifyArtifactMentions(
  text: string,
  businessSlug: string,
  knownPaths: string[],
) {
  const safe = typeof text === "string" ? text : String(text ?? "");
  const business = String(businessSlug || "").trim().toLowerCase();
  if (!safe.trim() || !business) return safe;

  const replacements = new Map<string, string>();
  const basenameCounts = new Map<string, number>();
  for (const path of knownPaths) {
    const normalized = normalizeArtifactPath(path);
    if (!normalized) continue;
    replacements.set(normalized, normalized);
    const base = normalized.split("/").pop() || "";
    if (base) basenameCounts.set(base, (basenameCounts.get(base) || 0) + 1);
  }
  for (const path of knownPaths) {
    const normalized = normalizeArtifactPath(path);
    const base = normalized.split("/").pop() || "";
    if (base && basenameCounts.get(base) === 1) {
      replacements.set(base, normalized);
    }
  }
  for (const match of safe.matchAll(/\b(?:product|distribution|research|brain|metrics|outreach|campaigns|conversation|app|memory)\/[^\s)\]"'`,;:]+/g)) {
    const normalized = normalizeArtifactPath(match[0]);
    if (looksLikeBusinessArtifactPath(normalized)) {
      replacements.set(normalized, normalized);
    }
  }

  const candidates = [...replacements.entries()]
    .map(([label, path]) => ({ label, href: resolveBusinessArtifactHref(business, path) }))
    .filter((item) => item.href)
    .sort((left, right) => right.label.length - left.label.length);
  if (!candidates.length) return safe;

  let linked = safe;
  for (const candidate of candidates) {
    const pattern = new RegExp(
      `(^|[\\s(["'])(${escapeRegExp(candidate.label)})(?=$|[\\s).,!?:\\]"'])`,
      "gm",
    );
    linked = linked.replace(
      pattern,
      (_match, prefix: string, label: string) => `${prefix}[${label}](${candidate.href})`,
    );
  }
  return linked;
}

// Chat dock horizontal resize: a per-browser persisted width, clamped to a usable
// range (never narrower than the composer, never wider than 60% of the viewport).
// Presentation-only — the dock width is local UI state, it never touches workspace
// data or the agent turn.
const CHAT_WIDTH_KEY = "lb-chat-width";
const CHAT_MIN_WIDTH = 280;
const CHAT_MAX_WIDTH = 760;
const CHAT_DEFAULT_WIDTH = 340;
function clampChatWidth(px: number): number {
  const viewportCap =
    typeof window !== "undefined" ? Math.round(window.innerWidth * 0.6) : CHAT_MAX_WIDTH;
  const max = Math.max(CHAT_MIN_WIDTH, Math.min(CHAT_MAX_WIDTH, viewportCap));
  return Math.max(CHAT_MIN_WIDTH, Math.min(Math.round(px), max));
}
function readStoredChatWidth(): number {
  if (typeof window === "undefined") return CHAT_DEFAULT_WIDTH;
  try {
    const raw = Number(window.localStorage.getItem(CHAT_WIDTH_KEY));
    return Number.isFinite(raw) && raw > 0 ? clampChatWidth(raw) : CHAT_DEFAULT_WIDTH;
  } catch {
    return CHAT_DEFAULT_WIDTH;
  }
}
function persistChatWidth(px: number): void {
  try {
    window.localStorage.setItem(CHAT_WIDTH_KEY, String(px));
  } catch {
    /* private mode / storage disabled — width simply isn't remembered */
  }
}

function workspaceBusinessName(
  workspace: TakyonBusinessWorkspaceResponse | null,
): string {
  const slug = String(workspace?.business_slug || "").trim();
  if (!slug) return "This business";
  return slug
    .split(/[-_\s]+/)
    .filter(Boolean)
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(" ") || "This business";
}

// Durable one-liner derived from the server-mirrored `live_state` snapshot. Used
// only when the chat transcript has no agent messages yet — e.g. after a reload
// while a CEO bootstrap job is still running, before any conversational line has
// streamed in. It renders as a SINGLE plain assistant bubble (not a card, no
// phase ladder, no "What changed"). Presentation-only: it reads the workspace
// mirror's curated headline/summary, never the agent's raw turn context.
function liveStateOneLiner(
  workspace: TakyonBusinessWorkspaceResponse | null,
): string {
  const businessName = workspaceBusinessName(workspace);
  const state = workspace?.live_state;
  if (!state || typeof state !== "object") return "";
  const payload = state as Record<string, unknown>;
  // Prefer the CEO's curated, customer-facing summary (business_post_operator_update)
  // over the headline; fall back to a warm, human, result-led line for an
  // in-flight status. The raw summary/headline/detail can still carry internal
  // plumbing wording, so every branch that returns them runs through
  // sanitizeCustomerReply first — a banned status-log line ("I'm on this …",
  // tool/skill/path nouns) is stripped and we fall through to the warm copy.
  const summary = sanitizeCustomerReply(String(payload.summary || ""));
  if (summary) return summary;
  const headline = sanitizeCustomerReply(String(payload.headline || ""));
  if (headline) return headline;
  const status = String(payload.status || "").trim().toLowerCase();
  // The live_state detail can carry a failed task's raw provider error
  // (`Error code: 400 - {'type': 'error', ...}`), which sanitizeCustomerReply
  // does NOT catch (it targets plumbing nouns, not provider dicts). Run the
  // "fail better" error sanitizer first so a raw provider dict becomes a calm
  // user line, then strip any remaining plumbing (BUG-002).
  const detail = sanitizeCustomerReply(sanitizeTaskErrorText(String(payload.detail || "")));
  if (detail) return detail;
  if (["queued", "scheduled", "pending"].includes(status)) {
    return `Getting ${businessName} set up now — I'll update you here in a moment.`;
  }
  if (["running", "active"].includes(status)) {
    return `Working on ${businessName} now — I'll update you here in a moment.`;
  }
  return "";
}

function liveProgress(status: unknown, detail: unknown) {
  const state = String(status || "").trim().toLowerCase();
  const summary = sanitizeTaskErrorText(String(detail || "").trim());
  return {
    state,
    detail: summary,
  };
}

function liveStateProgress(
  workspace: TakyonBusinessWorkspaceResponse | null,
) {
  const state = workspace?.live_state;
  if (!state || typeof state !== "object") return null;
  const payload = state as Record<string, unknown>;
  const progress = liveProgress(payload.status, payload.detail);
  return progress;
}

function CompanyMark({ name, size = 22 }: { name: string; size?: number }) {
  const ch = (name.trim()[0] || "C").toUpperCase();
  let h = 0;
  for (let i = 0; i < name.length; i++) h = (h * 31 + name.charCodeAt(i)) % 360;
  return (
    <span className="lb-comark" style={{ width: size, height: size, background: `hsl(${h} 48% 44%)`, fontSize: Math.round(size * 0.5) }}>
      {ch}
    </span>
  );
}

const MENU: string[][] = [
  ["My portfolio", "New company"],
  ["Plans & billing", "Profile settings"],
  ["FAQ"],
  ["Log out"],
];

const MENU_ROUTES: Record<string, string> = {
  "My portfolio": "/",
  "New company": "/app/new",
  "FAQ": "/faq",
};

const MENU_SETTINGS: Record<string, SettingsSection> = {
  "Plans & billing": "billing",
  "Profile settings": "profile",
};

// Render a single agent reply as markdown, but NEVER let a malformed token
// (a half-streamed GFM table, stray HTML, an unbalanced fence) throw during
// render and white-out the whole cockpit. A local boundary around the markdown
// renderer falls back to the raw text as plain, pre-wrapped content so the
// bubble — and the rest of the page — stay alive.
class MarkdownBoundary extends Component<
  { text: string; children: ReactNode },
  { failed: boolean }
> {
  state: { failed: boolean } = { failed: false };
  static getDerivedStateFromError(): { failed: boolean } {
    return { failed: true };
  }
  componentDidCatch(error: Error) {
    // Recoverable: the message still renders as plain text. Log for diagnosis.
    console.error("[cockpit-chat] markdown render failed, falling back to text", error);
  }
  componentDidUpdate(prev: { text: string }) {
    // A fresh delta/new bubble text should get another render attempt rather
    // than staying permanently in the plain-text fallback.
    if (this.state.failed && prev.text !== this.props.text) {
      this.setState({ failed: false });
    }
  }
  render() {
    if (this.state.failed) {
      return <div className="lb-msg__md lb-msg__md--raw">{this.props.text}</div>;
    }
    return this.props.children;
  }
}

function AgentMessageMarkdown({
  text,
  businessSlug = "",
  knownArtifacts = [],
}: {
  text: string;
  businessSlug?: string;
  knownArtifacts?: string[];
}) {
  const safe = typeof text === "string" ? text : String(text ?? "");
  const linked = linkifyArtifactMentions(safe, businessSlug, knownArtifacts);
  return (
    <div className="lb-msg__md">
      <MarkdownBoundary text={safe}>
        <ReactMarkdown
          remarkPlugins={[remarkGfm, remarkBreaks]}
          components={{
            a: (props) => <a {...props} target="_blank" rel="noreferrer" />,
          }}
        >
          {linked}
        </ReactMarkdown>
      </MarkdownBoundary>
    </div>
  );
}

// A page-level boundary for the entire chat panel. If anything in the chat
// subtree throws (an unexpected event payload shape, a render edge case), this
// catches it and shows a small inline fallback INSTEAD of unmounting the whole
// cockpit to a blank white screen. The product preview / company tab beside the
// chat keep working. A remount key (driven by business slug) lets a fresh
// business / re-open recover cleanly.
class ChatErrorBoundary extends Component<
  { children: ReactNode },
  { error: Error | null }
> {
  state: { error: Error | null } = { error: null };
  static getDerivedStateFromError(error: Error): { error: Error } {
    return { error };
  }
  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error("[cockpit-chat] chat panel crashed (contained)", error, info?.componentStack);
  }
  render() {
    if (this.state.error) {
      return (
        <aside className="lb-chat lb-chat--errored">
          <div className="lb-chat__log">
            <div className="lb-msg lb-msg--agent">
              <div className="lb-msg__bubble">
                <AgentMessageMarkdown
                  text="Something in the chat view hit a snag and recovered. Your business is still running — reload the page to restore the live conversation."
                  businessSlug=""
                />
              </div>
            </div>
            <button
              type="button"
              className="lb-chat__send"
              onClick={() => window.location.reload()}
              style={{ alignSelf: "flex-start", width: "auto", padding: "0 12px" }}
            >
              Reload
            </button>
          </div>
        </aside>
      );
    }
    return this.props.children;
  }
}

// A standalone agent row that contains ONLY the animated thinking dots — its own
// bubble, its own vertical space. Shown while the turn is running and before any
// customer-facing text has arrived; the message bubble replaces it once text
// streams in (mirrors Claude/OpenAI: thinking → reply). The dots NEVER stack on
// top of message words.
function ThinkingRow() {
  return (
    <div className="lb-msg lb-msg--agent">
      <div className="lb-msg__bubble lb-msg__bubble--think" aria-label="Assistant is thinking">
        <span className="lb-typing"><i /><i /><i /></span>
      </div>
    </div>
  );
}

// The GROWING live work view shown while the CEO is actively working a turn or a
// wake — the same liveness the bootstrap build screen shows, surfaced in the
// post-bootstrap cockpit. Each row is one de-identified step (a tool/agent
// action) the CEO is taking, newest last; the list GROWS append-only as the turn
// runs and a row flips running -> completed in place. This is the WORK view, NOT
// a customer chat bubble: it has its own compact treatment (a "Working…" header +
// step rows with a running/done dot), and every label/detail was already
// de-identified by liveWorkSteps so no raw tool/path noun — and never the CEO's
// raw thinking — reaches the screen. The trailing typing dots make movement
// visible even between discrete steps so a long step never reads as frozen.
function LiveWorkView({ steps }: { steps: LiveWorkStep[] }) {
  // Keep the view legible: show the most recent steps (the list still grows, we
  // just window the tail so a very long turn doesn't push the composer offscreen).
  const visible = steps.slice(-8);
  return (
    <div className="lb-msg lb-msg--agent">
      <div className="lb-work" aria-label="What the CEO is doing now" aria-live="polite">
        <div className="lb-work__head">
          <span className="lb-work__dot lb-work__dot--live" aria-hidden="true" />
          <span className="lb-work__title">Working…</span>
          <span className="lb-typing lb-work__typing" aria-hidden="true"><i /><i /><i /></span>
        </div>
        <ul className="lb-work__list">
          {visible.map((step) => (
            <li key={step.id} className={`lb-work__row lb-work__row--${step.status}`}>
              <span className={`lb-work__row-dot lb-work__row-dot--${step.status}`} aria-hidden="true" />
              <span className="lb-work__row-label">{step.label}</span>
              {step.detail && step.detail !== step.label ? (
                <span className="lb-work__row-detail">{step.detail}</span>
              ) : null}
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}

function ProfileMenu({ onClose, onSelect }: { onClose: () => void; onSelect: (label: string) => void }) {
  return (
    <>
      <div className="lb-menu-scrim" onClick={onClose} />
      <div className="lb-menu" role="menu">
        {MENU.map((group, groupIndex) => (
          <div className="lb-menu__group" key={groupIndex}>
            {group.map((label) => (
              <button key={label} className="lb-menu__item" role="menuitem" onClick={() => { onSelect(label); onClose(); }}>
                {label}
              </button>
            ))}
          </div>
        ))}
      </div>
    </>
  );
}

function TopBar({
  business,
  theme,
  onToggleTheme,
  onNav,
  onLogout,
  onOpenSettings,
  onSetWakeState,
  onWakeNow,
}: {
  business: LitebulbBusiness;
  theme: Theme;
  onToggleTheme: () => void;
  onNav: (hash: string) => void;
  onLogout: () => void;
  onOpenSettings: (s: SettingsSection) => void;
  onSetWakeState: (slug: string, paused: boolean) => Promise<void>;
  onWakeNow: (slug: string) => Promise<void>;
}) {
  const [menuOpen, setMenuOpen] = useState(false);
  const [wakeBusy, setWakeBusy] = useState(false);
  const [wakeNowBusy, setWakeNowBusy] = useState(false);
  const wakesPaused = Boolean(business.wakesPaused);
  const onSelect = (label: string) => {
    if (label === "Log out") onLogout();
    else if (MENU_SETTINGS[label]) onOpenSettings(MENU_SETTINGS[label]);
    else if (MENU_ROUTES[label]) onNav(MENU_ROUTES[label]);
  };
  const onToggleWakes = async () => {
    if (wakeBusy) return;
    setWakeBusy(true);
    try {
      await onSetWakeState(business.slug, !wakesPaused);
    } finally {
      setWakeBusy(false);
    }
  };
  const onWakeNowClick = async () => {
    if (wakeNowBusy) return;
    setWakeNowBusy(true);
    try {
      await onWakeNow(business.slug);
    } finally {
      setWakeNowBusy(false);
    }
  };
  return (
    <header className="lb-topbar">
      <button className="lb-topbar__group lb-brand" onClick={() => onNav("/")} aria-label="Coscale — all companies" title="All companies">
        <BulbMark size={22} tone="ink" />
        <span className="lb-topbar__name">Coscale</span>
        <span className="lb-brand__caret">{Icon.caret}</span>
      </button>

      <div className="lb-topbar__group">
        <span className="lb-wstat__name">{business.name}</span>
        <span className={`lb-badge ${business.mode === "live" ? "is-live" : ""}`}>{business.mode || "live"}</span>
        {wakesPaused && <span className="lb-badge lb-badge--paused">wakes paused</span>}
        <button
          type="button"
          className={`lb-badge lb-wakebtn${wakesPaused ? " is-paused" : ""}`}
          onClick={onToggleWakes}
          disabled={wakeBusy}
          aria-pressed={wakesPaused}
          title={wakesPaused ? "Resume the autonomous CEO wake loop" : "Pause the autonomous CEO wake loop"}
        >
          {wakeBusy ? "…" : wakesPaused ? "Resume wakes" : "Pause wakes"}
        </button>
        <button
          type="button"
          className="lb-badge lb-wakebtn lb-wakenow"
          onClick={onWakeNowClick}
          disabled={wakeNowBusy}
          title="Wake the CEO now instead of waiting for the next scheduled wake"
        >
          {wakeNowBusy ? "…" : "Wake now"}
        </button>
      </div>

      <div className="lb-topbar__group lb-topbar__group--end">
        <button className="lb-iconbtn" aria-label="Toggle theme" onClick={onToggleTheme}>{theme === "dark" ? Icon.sun : Icon.moon}</button>
        <div className="lb-profile">
          <button
            className={`lb-avatar${menuOpen ? " is-open" : ""}`}
            aria-label="Menu"
            aria-haspopup="menu"
            aria-expanded={menuOpen}
            onClick={() => setMenuOpen((open) => !open)}
          >
            <span className="lb-avatar__face">{Icon.user}</span>{Icon.caret}
          </button>
          {menuOpen && <ProfileMenu onClose={() => setMenuOpen(false)} onSelect={onSelect} />}
        </div>
      </div>
    </header>
  );
}

type TabKey = "product" | "company";

function AgentChat({
  business,
  artifactPaths,
  messages,
  agentStream,
  workSteps,
  chatSummary,
  chatRunning,
  liveStateLine,
  tab,
  running,
  sending,
  onTab,
  onClose,
  onSend,
  onStop,
}: {
  business: LitebulbBusiness;
  artifactPaths: string[];
  // The raw client transcript. ONLY the user bubbles are taken from here; the
  // agent bubbles come from `agentStream` (the curated, customer-safe
  // chat_stream), never from the raw history/delta assistant messages which are
  // the CEO's chain-of-thought.
  messages: ChatMessage[];
  // The curated CEO narration parsed from workspace.chat_stream (ordered
  // oldest→newest). These are the ONLY agent bubbles rendered.
  agentStream: ChatStreamMessage[];
  // The append-only GROWING live work view (de-identified per-step trace) shown
  // while a turn or wake is in flight — the cockpit's equivalent of the bootstrap
  // build screen's live "Working on…" view. This is the WORK view, never a
  // customer chat bubble.
  workSteps: LiveWorkStep[];
  // Durable end-of-turn summary mirrored from workspace.chat_summary. The
  // chat_stream's last message usually already carries it; this is a fallback.
  chatSummary?: string;
  // The backend's authoritative chat_running flag (live_state.chat_running);
  // used together with the client's own `running` to drive the thinking dots.
  chatRunning: boolean;
  // Durable one-line status from the server-mirrored live_state, used ONLY as a
  // single plain assistant bubble when there is no chat_stream yet (e.g. a cold
  // reload mid-bootstrap before any curated message exists). Never a card.
  liveStateLine?: string;
  tab: TabKey;
  // TRUE in-flight signal (an active prompt submit or a live gateway turn). The
  // stop affordance is shown ONLY while this is true; never derived from a
  // durable live_state mirror.
  running: boolean;
  sending: boolean;
  onTab: (tab: TabKey) => void;
  onClose: () => void;
  onSend: (text: string) => void;
  onStop: () => void;
}) {
  const [draft, setDraft] = useState("");
  // Optimistic stop state: reflect the interrupt immediately on click, then clear
  // once the turn actually ends (running flips false).
  const [stopping, setStopping] = useState(false);
  const endRef = useRef<HTMLDivElement>(null);

  // The optimistic "stopping" affordance is only meaningful while a turn is
  // actually running; when running flips false the Stop button unmounts (Send
  // takes its place), so a stale flag can never show. Deriving it (rather than
  // clearing it in an effect) keeps the indicator truthful without a
  // setState-in-effect cascade.
  const isStopping = stopping && running;

  const submit = () => {
    const text = draft.trim();
    if (!text) return;
    // A fresh turn clears any prior optimistic stop state.
    setStopping(false);
    setDraft("");
    onSend(text);
  };

  // Build the conversational transcript by MERGING two ordered streams:
  //   • USER bubbles — taken only from `messages` (who === 'user'). The raw
  //     history/delta AGENT messages are deliberately DROPPED here: they are the
  //     CEO's chain-of-thought and must never reach the customer.
  //   • AGENT bubbles — the per-turn chat_stream (`agentStream`): one bubble per
  //     completed CEO turn (the turn's own reply), NOT the raw history/delta stream.
  // Ordering: every item with a wall-clock ts (user send time, or the stream
  // item's posted_at) sorts by ts; items without a ts keep their relative
  // insertion order (stable). A bootstrap (no user messages) reads as just the
  // chat_stream in order; a follow-up reads as the user message then the CEO's
  // reply. Each agent bubble is lightly cleaned for display (no ban-list); only a
  // genuinely-empty bubble is omitted. There is NO card, NO phase ladder, and NO
  // "What changed" panel.
  type TranscriptEntry = {
    id: string;
    who: "agent" | "user";
    text: string;
    ts?: number;
    order: number;
    working?: boolean;
  };
  const userEntries: TranscriptEntry[] = messages
    .filter((message) => message.who === "user")
    .map((message, index) => ({
      id: message.id,
      who: "user" as const,
      text: message.text.trim(),
      ts: typeof message.ts === "number" && Number.isFinite(message.ts) ? message.ts : undefined,
      // User messages sort before an agent stream item sharing the same ms.
      order: index * 2,
    }))
    .filter((entry) => Boolean(entry.text));
  const agentEntries: TranscriptEntry[] = agentStream
    .map((item, index) => ({
      id: item.id,
      who: "agent" as const,
      text: sanitizeCustomerReply(item.text),
      ts: typeof item.ts === "number" && Number.isFinite(item.ts) ? item.ts : undefined,
      order: index * 2 + 1,
    }))
    .filter((entry) => Boolean(entry.text));
  // Stable merge: timestamped entries interleave by ts; entries without a ts
  // hold their relative position (their `order` breaks ties deterministically).
  const bubbles = [...userEntries, ...agentEntries].sort((a, b) => {
    if (a.ts != null && b.ts != null && a.ts !== b.ts) return a.ts - b.ts;
    return a.order - b.order;
  });
  const hasAgentBubble = agentEntries.length > 0;
  // The thinking dots are a STANDALONE agent row (their own bubble, their own
  // vertical space, always at the tail) — they never stack on the words.
  //   • The backend's chat_running flag is authoritative: while the CEO turn is
  //     running the dots show even when curated narration is already the tail
  //     (the turn is still in flight; more is coming).
  //   • The client's own in-flight signal (`running`, e.g. a just-submitted
  //     prompt with no curated reply yet) also shows dots, but yields the moment
  //     a fresh agent bubble lands at the tail so the streamed reply isn't
  //     shadowed by a duplicate indicator.
  // The LIVE streamed reply for an operator-sent turn: the gateway streams the
  // CEO's answer token-by-token into a `working` agent message on `messages`
  // (appendAssistantText). Render THAT as it grows so the reply types out like a
  // real agent instead of "dots then a finished bubble pops in". It is shown only
  // while in-flight; the moment the turn settles, the durable curated bubble
  // (agentStream) owns it. Dedup by cleaned text so the live draft and the curated
  // copy never double-render during the hand-off.
  const draftMsg = [...messages].reverse().find(
    (message) => message.who === "agent" && Boolean(message.working) && Boolean(message.text.trim()),
  );
  const draftText = draftMsg ? sanitizeCustomerReply(draftMsg.text) : "";
  const draftAlreadyCurated =
    Boolean(draftText) && agentEntries.some((entry) => entry.text.trim() === draftText.trim());
  const streamingBubble = draftText && !draftAlreadyCurated ? { id: draftMsg!.id, text: draftText } : null;
  const lastBubble = bubbles[bubbles.length - 1];
  const tailIsAgentText = lastBubble?.who === "agent" || Boolean(streamingBubble);
  const isRunning = chatRunning || running;
  // The durable end-of-turn summary. The chat_stream's last message already
  // carries it, so we only surface a standalone summary bubble when it is not
  // already the tail agent text (avoids a duplicate). Settled state only.
  const summaryText = (chatSummary || "").trim();
  const showSummary =
    !isRunning
    && Boolean(summaryText)
    && summaryText !== (tailIsAgentText ? lastBubble!.text.trim() : "");
  // A turn is "in flight" when the client is actively sending (`running`, the same
  // signal the Stop button uses) OR the backend mirror says a turn is genuinely
  // running AND it has not yet settled into an end-of-turn summary. This re-admits
  // the mirror for the cold-reload-mid-turn case (running=false but a real turn is
  // in flight, with an existing agent bubble) WITHOUT resurrecting the forever-spin
  // P0: the instant a summary lands — or the server stops reporting 'running' —
  // turnInFlight goes false and the dots clear. The Stop button stays gated on
  // `running` only, so it can never show with nothing to stop.
  const turnInFlight = running || (chatRunning && !showSummary && !summaryText);
  // Live "working" indicator: show whenever a turn is GENUINELY in flight — the
  // interactive `running` signal OR the backend `chatRunning` flag (which is gated
  // on a genuinely-live background_run, so it cannot spin forever after the run
  // settles). It now stays visible even when a narration bubble is the tail, so the
  // gap BETWEEN bootstrap turns reads as "still working", never frozen/dead. When
  // the reply is actively streaming (streamingBubble), the growing text IS the live
  // indicator, so the dots yield to it rather than stacking under the words.
  //
  // Live work view: while a turn/wake is genuinely in flight and the final reply
  // is NOT yet streaming, show the GROWING per-step work view (the de-identified
  // trace stream) so a long multi-step turn — and a wake — reads as live, growing
  // work instead of a frozen "working…" line. The work view carries its own typing
  // dots, so it IS the movement indicator; the bare ThinkingRow shows only before
  // any step has landed yet (so there is never a dead "working…" with nothing
  // below it). Both yield to the streaming reply when it starts.
  const showWorkView = isRunning && !streamingBubble && workSteps.length > 0;
  const showThinking = isRunning && !streamingBubble && !showWorkView;
  // On a cold reload of a SETTLED business with no chat_stream yet, surface the
  // durable live_state one-liner as a single plain assistant bubble so the chat is
  // never blank. NEVER while a turn is in flight: the live_state one-liner is a
  // deterministic status-ladder line (e.g. "There's a working version of your
  // product…") and must not impersonate the agent's voice mid-build — during an
  // active turn the streamed per-response bubbles (and the thinking row) are the
  // agent. So gate on !turnInFlight too.
  const showLiveStateLine =
    !hasAgentBubble && !showSummary && !turnInFlight && Boolean(liveStateLine);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [
    bubbles.length,
    showThinking,
    showSummary,
    showLiveStateLine,
    streamingBubble?.text.length,
    showWorkView,
    workSteps.length,
  ]);

  return (
    <aside className="lb-chat">
      <div className="lb-chat__head">
        <CompanyMark name={business.name} size={22} />
        <b className="lb-chat__name">{business.name}</b>
        <span className="lb-chat__spacer" />
        <Tabs
          className="lb-viewtabs lb-chat__tabs"
          value={tab}
          onChange={(value) => onTab(value as TabKey)}
          options={[{ value: "company", label: "Company" }, { value: "product", label: "Product" }]}
        />
        <button className="lb-chat__collapse" onClick={onClose} aria-label="Hide chat" title="Hide chat">{Icon.collapse}</button>
      </div>

      <div className="lb-chat__log">
        {bubbles.map((message) =>
          message.who === "user" ? (
            <div key={message.id} className="lb-msg lb-msg--user">
              <div className="lb-msg__bubble">{message.text}</div>
            </div>
          ) : (
            <div key={message.id} className="lb-msg lb-msg--agent">
              <div className="lb-msg__bubble">
                <AgentMessageMarkdown
                  text={message.text}
                  businessSlug={business.slug}
                  knownArtifacts={artifactPaths}
                />
                {message.working && (
                  <span className="lb-msg__work">Working…</span>
                )}
              </div>
            </div>
          ),
        )}
        {showSummary && (
          <div className="lb-msg lb-msg--agent">
            <div className="lb-msg__bubble">
              <AgentMessageMarkdown
                text={summaryText}
                businessSlug={business.slug}
                knownArtifacts={artifactPaths}
              />
            </div>
          </div>
        )}
        {showLiveStateLine && (
          <div className="lb-msg lb-msg--agent">
            <div className="lb-msg__bubble">
              <AgentMessageMarkdown
                text={liveStateLine!}
                businessSlug={business.slug}
                knownArtifacts={artifactPaths}
              />
            </div>
          </div>
        )}
        {streamingBubble && (
          <div key={streamingBubble.id} className="lb-msg lb-msg--agent">
            <div className="lb-msg__bubble lb-msg__bubble--stream">
              <AgentMessageMarkdown
                text={streamingBubble.text}
                businessSlug={business.slug}
                knownArtifacts={artifactPaths}
              />
              <span className="lb-stream-caret" aria-hidden="true" />
            </div>
          </div>
        )}
        {showWorkView && <LiveWorkView steps={workSteps} />}
        {showThinking && <ThinkingRow />}
        <div ref={endRef} />
      </div>

      <div className="lb-chat__input">
        <Textarea
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter" && !event.shiftKey) {
              event.preventDefault();
              submit();
            }
          }}
          placeholder="Change anything…"
          rows={2}
        />
        {running ? (
          <button
            className="lb-chat__stop"
            disabled={isStopping}
            onClick={() => {
              setStopping(true);
              onStop();
            }}
            aria-label="Stop"
            title={isStopping ? "Stopping…" : "Stop"}
            type="button"
          >
            {Icon.stop}
          </button>
        ) : (
          <button className="lb-chat__send" disabled={sending} onClick={submit} aria-label="Send">{Icon.send}</button>
        )}
      </div>
    </aside>
  );
}

function ProductPreview({
  business,
  workspace,
  publicUrl,
}: {
  business: LitebulbBusiness;
  workspace: TakyonBusinessWorkspaceResponse | null;
  publicUrl?: string;
}) {
  const [device, setDevice] = useState<"desktop" | "mobile">("desktop");
  const overview = (workspace?.overview || {}) as Record<string, unknown>;
  const product = (overview.product || {}) as Record<string, unknown>;
  // Authoritative URL resolution, best-to-fallback:
  //   1. publicUrl       — the published canonical URL when live.
  //   2. publish_target  — the backend's canonical expected URL (slug.coscale.app)
  //                        even before the product is published.
  //   3. canonical host derived client-side from the slug.
  // Never fabricate a `.app` placeholder.
  const publishTarget = typeof product.publish_target === "string" ? product.publish_target : "";
  const canonicalUrl = publicUrl || publishTarget || (
    canonicalProductHost(business.slug) ? `https://${canonicalProductHost(business.slug)}/` : ""
  );
  const site = addressBarText(canonicalUrl) || canonicalProductHost(business.slug);
  const addressLink = canonicalUrl || (
    canonicalProductHost(business.slug) ? `https://${canonicalProductHost(business.slug)}/` : ""
  );
  const previewStatus = typeof product.preview_status === "string" ? product.preview_status : "";
  const previewAvailable = Boolean(product.preview_available);
  const previewPath = typeof product.preview_path === "string" ? product.preview_path : "product/site";
  const outputs = Array.isArray(workspace?.outputs) ? workspace.outputs : [];
  const sourcePath = typeof product.source_path === "string" ? product.source_path : "";
  const publishStatus = typeof product.publish_status === "string" ? product.publish_status : "";
  const productStatus = typeof product.status === "string" ? product.status : "";
  const frameUrl = previewAvailable
    ? buildTakyonBusinessSitePreviewFrameUrl(business.slug, previewPath)
    : "";
  const hasLocalSource = Boolean(
    sourcePath
    || outputs.some((item) => {
      const output = item && typeof item === "object" ? item as Record<string, unknown> : {};
      const path = typeof output.path === "string" ? output.path : "";
      return path.startsWith("product/site/");
    }),
  );
  const stateLabel = String(previewStatus || publishStatus || productStatus || "").trim().toLowerCase();
  const isLoading = !workspace;
  const isBuilding = !frameUrl && !isLoading && (
    hasLocalSource
    || ["queued", "scheduled", "pending", "running", "ready", "local_source"].includes(stateLabel)
  );
  const detail = isLoading
    ? "Loading the product workspace and preview surface."
    : isBuilding
    ? "Waiting for the product build or publish step to produce a previewable surface."
    : "Once the CEO ships `product/site`, the preview appears here.";

  return (
    <section className="lb-stage">
      <div className={`lb-browser lb-browser--${device}`}>
        <div className="lb-browser__chrome">
          <span className="lb-traffic"><span /><span /><span /></span>
          {addressLink ? (
            <a
              className="lb-browser__addr"
              href={addressLink}
              target="_blank"
              rel="noopener noreferrer"
              title={addressLink}
            >
              {site}
            </a>
          ) : (
            <span className="lb-browser__addr">{site}</span>
          )}
          <span className="lb-browser__tools">
            <span className="lb-seg2">
              <button className={device === "desktop" ? "is-on" : ""} onClick={() => setDevice("desktop")} aria-label="Desktop">{Icon.monitor}</button>
              <button className={device === "mobile" ? "is-on" : ""} onClick={() => setDevice("mobile")} aria-label="Mobile">{Icon.phone}</button>
            </span>
            {publicUrl && (
              <button className="lb-iconbtn lb-iconbtn--sm" aria-label="Open in new tab" onClick={() => window.open(publicUrl, "_blank", "noopener,noreferrer")}>
                {Icon.external}
              </button>
            )}
          </span>
        </div>
        <div className="lb-browser__view">
          {frameUrl ? (
            <iframe
              className="lb-browser__iframe"
              title={`${business.name} preview`}
              src={frameUrl}
            />
          ) : isLoading || isBuilding ? (
            <div className="lb-browser__empty">
              <div className="lb-browser__loader" aria-hidden="true" />
              <h3>{isLoading ? "Loading product preview" : "Building product preview"}</h3>
              <p>{detail}</p>
            </div>
          ) : (
            <div className="lb-browser__empty">
              <h3>No product preview yet</h3>
              <p>{detail}</p>
              {productStatus && productStatus !== "missing" && (
                <p className="lb-browser__hint">Current status: {productStatus}</p>
              )}
            </div>
          )}
        </div>
      </div>
    </section>
  );
}

export function Product({
  business,
  workspace,
  creativeCredits,
  traction,
  tractionRange,
  theme,
  chatMessages,
  sending,
  sessionRunning,
  onTheme,
  onNav,
  onLogout,
  onOpenSettings,
  onSendPrompt,
  onStopPrompt,
  onSaveChannelCreditBudgets,
  onSetWakeState,
  onWakeNow,
  onBuyCreativeCredits,
  onTractionRangeChange,
}: {
  business: LitebulbBusiness;
  workspace: TakyonBusinessWorkspaceResponse | null;
  creativeCredits: TakyonBusinessCreativeCreditsResponse | null;
  traction: TakyonBusinessTractionResponse | null;
  tractionRange: "D" | "W" | "M" | "Y";
  theme: Theme;
  chatMessages: ChatMessage[];
  sending: boolean;
  sessionRunning: boolean;
  onTheme: (theme: Theme) => void;
  onNav: (hash: string) => void;
  onLogout: () => void;
  onOpenSettings: (section: SettingsSection) => void;
  onSendPrompt: (text: string) => void;
  onStopPrompt: () => void;
  onSaveChannelCreditBudgets: (
    slug: string,
    allocations: Record<"x" | "meta" | "reddit", number>,
  ) => Promise<TakyonBusinessCreativeCreditsResponse | null>;
  onSetWakeState: (slug: string, paused: boolean) => Promise<void>;
  onWakeNow: (slug: string) => Promise<void>;
  onBuyCreativeCredits: (slug: string, credits: number) => Promise<void>;
  onTractionRangeChange: (range: "D" | "W" | "M" | "Y") => void;
}) {
  const [tab, setTab] = useState<TabKey>("company");
  const [chatOpen, setChatOpen] = useState(true);
  // Horizontally resizable chat dock. `chatWidth` drives a CSS custom property on
  // the dock; the drag is pointer-based and disables the dock's width transition
  // while in flight (via `chatResizing`) so it tracks the cursor 1:1.
  const [chatWidth, setChatWidth] = useState<number>(readStoredChatWidth);
  const [chatResizing, setChatResizing] = useState(false);

  // Re-clamp if the viewport shrinks so the dock can never exceed the 60% cap.
  useEffect(() => {
    const onResize = () => setChatWidth((w) => clampChatWidth(w));
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, []);

  const startChatResize = (event: ReactPointerEvent<HTMLDivElement>) => {
    if (event.button !== 0) return; // primary button only
    event.preventDefault();
    const startX = event.clientX;
    const startWidth = chatWidth; // current width from this render's closure
    let latest = startWidth; // tracks the live width without a render-synced ref
    setChatResizing(true);
    document.body.style.userSelect = "none";
    document.body.style.cursor = "col-resize";
    const onMove = (moveEvent: PointerEvent) => {
      latest = clampChatWidth(startWidth + (moveEvent.clientX - startX));
      setChatWidth(latest);
    };
    const onUp = () => {
      setChatResizing(false);
      document.body.style.userSelect = "";
      document.body.style.cursor = "";
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
      persistChatWidth(latest);
    };
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp);
  };

  // Keyboard a11y: arrow keys nudge the divider (Shift = larger step).
  const nudgeChatResize = (event: ReactKeyboardEvent<HTMLDivElement>) => {
    const step = event.shiftKey ? 48 : 16;
    const delta = event.key === "ArrowLeft" ? -step : event.key === "ArrowRight" ? step : 0;
    if (!delta) return;
    event.preventDefault();
    setChatWidth((w) => {
      const next = clampChatWidth(w + delta);
      persistChatWidth(next);
      return next;
    });
  };

  const overview = (workspace?.overview || {}) as Record<string, unknown>;
  const product = (overview.product || {}) as Record<string, unknown>;
  const publicUrl = typeof product.public_url === "string" ? product.public_url : "";
  const effectiveProgress = liveStateProgress(workspace);
  // Durable one-line status from the server-mirrored live_state. Rendered as a
  // single plain assistant bubble only when there is no curated chat_stream yet
  // (e.g. a reload mid-bootstrap) — never as a card or a phase ladder.
  const liveStateLine = liveStateOneLiner(workspace);
  // The curated, customer-safe CEO narration. These (not the raw history/delta
  // assistant messages) are the ONLY agent bubbles in the transcript, so no
  // mid-thought planner/reasoning text can ever render as conversation.
  // ACCUMULATED (append-only): each poll's chat_stream is MERGED into a per-business
  // transcript, never replaced. A transient short/empty chat_stream poll (seen during
  // bootstrap, when the server snapshot momentarily rebuilds) can no longer wipe turns
  // that were already on screen — so the conversation grows like a real agent instead
  // of flashing one turn and blanking to "…" before the next.
  const agentStream = useAccumulatedAgentStream(workspace);
  const chatSummary = workspaceChatSummary(workspace);
  const chatRunning = workspaceChatRunning(workspace);
  // TRUE in-flight running signal for the stop affordance and the thinking dots:
  // an in-flight prompt submit (`sending`) or a live session turn reported by the
  // gateway (`sessionRunning`). Deliberately NOT derived from any durable
  // live_state mirror, which can stay "running" after the turn ended — that is
  // exactly what kept the dots floating when nothing was actually running.
  const running = sending || sessionRunning;
  // A turn/wake is "in flight" for the live work view when the operator's own
  // turn is running (`running`) OR the backend mirror reports a genuinely-live
  // background run (`chatRunning`, gated on a real live background_run so it can't
  // spin forever). chatRunning is what makes a WAKE — which the operator did not
  // submit — surface its growing work view; `running` covers a chat turn.
  const workActive = running || chatRunning;
  // Append-only GROWING live work view (the de-identified per-step trace stream),
  // accumulated across polls so it never truncates mid-turn and reset cleanly when
  // the turn settles. Same liveness as the bootstrap build screen, for the cockpit.
  const workSteps = useAccumulatedWorkSteps(workspace, workActive);
  const artifactPaths = collectWorkspaceArtifactPaths(workspace);

  useEffect(() => {
    setTab("company");
  }, [business.slug]);

  return (
    <div className="lb-view lb-product">
      <TopBar
        business={business}
        theme={theme}
        onToggleTheme={() => onTheme(theme === "dark" ? "light" : "dark")}
        onNav={onNav}
        onLogout={onLogout}
        onOpenSettings={onOpenSettings}
        onSetWakeState={onSetWakeState}
        onWakeNow={onWakeNow}
      />

      <div className="lb-workspace">
        <div
          className={`lb-chat-dock${chatOpen ? "" : " is-closed"}${chatResizing ? " is-resizing" : ""}`}
          style={{ "--lb-chat-w": `${chatWidth}px` } as CSSProperties}
        >
          <ChatErrorBoundary key={business.slug}>
            <AgentChat
              business={business}
              artifactPaths={artifactPaths}
              messages={chatMessages}
              agentStream={agentStream}
              workSteps={workSteps}
              chatSummary={chatSummary || undefined}
              chatRunning={chatRunning}
              liveStateLine={liveStateLine || undefined}
              tab={tab}
              running={running}
              sending={sending}
              onTab={setTab}
              onClose={() => setChatOpen(false)}
              onSend={onSendPrompt}
              onStop={onStopPrompt}
            />
          </ChatErrorBoundary>
        </div>

        {chatOpen && (
          <div
            className={`lb-chat-resize${chatResizing ? " is-active" : ""}`}
            role="separator"
            aria-orientation="vertical"
            aria-label="Resize chat panel"
            aria-valuemin={CHAT_MIN_WIDTH}
            aria-valuemax={CHAT_MAX_WIDTH}
            aria-valuenow={chatWidth}
            tabIndex={0}
            title="Drag to resize chat"
            onPointerDown={startChatResize}
            onKeyDown={nudgeChatResize}
          />
        )}

        <div className={`lb-main${tab === "product" ? " lb-main--preview" : ""}`}>
          {tab === "product" ? (
            <ProductPreview business={business} workspace={workspace} publicUrl={publicUrl} />
          ) : (
            <CompanyTab
              business={business}
              workspace={workspace}
              progress={effectiveProgress}
              creativeCredits={creativeCredits}
              traction={traction}
              tractionRange={tractionRange}
              onSaveChannelCreditBudgets={onSaveChannelCreditBudgets}
              onBuyCreativeCredits={onBuyCreativeCredits}
              onTractionRangeChange={onTractionRangeChange}
            />
          )}
        </div>

        {!chatOpen && (
          <button className="lb-chat-handle" onClick={() => setChatOpen(true)} aria-label="Open chat">
            {Icon.chat}<span>Chat</span>
          </button>
        )}
      </div>
    </div>
  );
}
