import { useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkBreaks from "remark-breaks";
import remarkGfm from "remark-gfm";
import type {
  TakyonBusinessCreativeCreditsResponse,
  TakyonBusinessTractionResponse,
  TakyonBusinessWorkspaceResponse,
} from "@/lib/api";
import {
  chatStreamAgentMessages,
  sanitizeCustomerReply,
  sanitizeTaskErrorText,
  workspaceChatRunning,
  workspaceChatSummary,
  type ChatStreamMessage,
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

function AgentMessageMarkdown({ text }: { text: string }) {
  return (
    <div className="lb-msg__md">
      <ReactMarkdown
        remarkPlugins={[remarkGfm, remarkBreaks]}
        components={{
          a: ({ node: _node, ...props }) => <a {...props} target="_blank" rel="noreferrer" />,
        }}
      >
        {text}
      </ReactMarkdown>
    </div>
  );
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
}: {
  business: LitebulbBusiness;
  theme: Theme;
  onToggleTheme: () => void;
  onNav: (hash: string) => void;
  onLogout: () => void;
  onOpenSettings: (s: SettingsSection) => void;
  onSetWakeState: (slug: string, paused: boolean) => Promise<void>;
}) {
  const [menuOpen, setMenuOpen] = useState(false);
  const [wakeBusy, setWakeBusy] = useState(false);
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
  messages,
  agentStream,
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
  // The raw client transcript. ONLY the user bubbles are taken from here; the
  // agent bubbles come from `agentStream` (the curated, customer-safe
  // chat_stream), never from the raw history/delta assistant messages which are
  // the CEO's chain-of-thought.
  messages: ChatMessage[];
  // The curated CEO narration parsed from workspace.chat_stream (ordered
  // oldest→newest). These are the ONLY agent bubbles rendered.
  agentStream: ChatStreamMessage[];
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
  const lastBubble = bubbles[bubbles.length - 1];
  const tailIsAgentText = lastBubble?.who === "agent";
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
  const showThinking = turnInFlight && !tailIsAgentText;
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
  }, [bubbles.length, showThinking, showSummary, showLiveStateLine]);

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
        {bubbles.map((entry) =>
          entry.who === "user" ? (
            <div key={entry.id} className="lb-msg lb-msg--user">
              <div className="lb-msg__bubble">{entry.text}</div>
            </div>
          ) : (
            <div key={entry.id} className="lb-msg lb-msg--agent">
              <div className="lb-msg__bubble">
                <AgentMessageMarkdown text={entry.text} />
              </div>
            </div>
          ),
        )}
        {showSummary && (
          <div className="lb-msg lb-msg--agent">
            <div className="lb-msg__bubble">
              <AgentMessageMarkdown text={summaryText} />
            </div>
          </div>
        )}
        {showLiveStateLine && (
          <div className="lb-msg lb-msg--agent">
            <div className="lb-msg__bubble">
              <AgentMessageMarkdown text={liveStateLine!} />
            </div>
          </div>
        )}
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
  const outputs = Array.isArray(workspace?.outputs) ? workspace.outputs : [];
  const sourcePath = typeof product.source_path === "string" ? product.source_path : "";
  const publishStatus = typeof product.publish_status === "string" ? product.publish_status : "";
  const productStatus = typeof product.status === "string" ? product.status : "";
  // Embed the PUBLIC published landing (slug.coscale.app) directly — it is a
  // public page, so the preview needs no operator auth / sign-in. Only embed once it
  // is actually published; otherwise fall through to the "building" / "no preview yet"
  // state below (an unpublished slug has no live landing to show).
  const isPublished = previewStatus === "published" || publishStatus === "published";
  const frameUrl = isPublished && canonicalUrl ? canonicalUrl : "";
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
  onBuyCreativeCredits: (slug: string, credits: number) => Promise<void>;
  onTractionRangeChange: (range: "D" | "W" | "M" | "Y") => void;
}) {
  const [tab, setTab] = useState<TabKey>("company");
  const [chatOpen, setChatOpen] = useState(true);
  const overview = (workspace?.overview || {}) as Record<string, unknown>;
  const product = (overview.product || {}) as Record<string, unknown>;
  const publicUrl = typeof product.public_url === "string" ? product.public_url : "";
  // Durable one-line status from the server-mirrored live_state. Rendered as a
  // single plain assistant bubble only when there is no curated chat_stream yet
  // (e.g. a reload mid-bootstrap) — never as a card or a phase ladder.
  const liveStateLine = liveStateOneLiner(workspace);
  // The curated, customer-safe CEO narration. These (not the raw history/delta
  // assistant messages) are the ONLY agent bubbles in the transcript, so no
  // mid-thought planner/reasoning text can ever render as conversation.
  const agentStream = chatStreamAgentMessages(workspace);
  const chatSummary = workspaceChatSummary(workspace);
  const chatRunning = workspaceChatRunning(workspace);
  // TRUE in-flight running signal for the stop affordance and the thinking dots:
  // an in-flight prompt submit (`sending`) or a live session turn reported by the
  // gateway (`sessionRunning`). Deliberately NOT derived from any durable
  // live_state mirror, which can stay "running" after the turn ended — that is
  // exactly what kept the dots floating when nothing was actually running.
  const running = sending || sessionRunning;

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
      />

      <div className="lb-workspace">
        <div className={`lb-chat-dock${chatOpen ? "" : " is-closed"}`}>
          <AgentChat
            business={business}
            messages={chatMessages}
            agentStream={agentStream}
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
        </div>

        <div className={`lb-main${tab === "product" ? " lb-main--preview" : ""}`}>
          {tab === "product" ? (
            <ProductPreview business={business} workspace={workspace} publicUrl={publicUrl} />
          ) : (
            <CompanyTab
              business={business}
              workspace={workspace}
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
