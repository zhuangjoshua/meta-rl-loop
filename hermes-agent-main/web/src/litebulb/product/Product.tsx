import { useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkBreaks from "remark-breaks";
import remarkGfm from "remark-gfm";
import type {
  TakyonBusinessCreativeCreditsResponse,
  TakyonBusinessTractionResponse,
  TakyonBusinessWorkspaceResponse,
} from "@/lib/api";
import { buildTakyonBusinessSitePreviewFrameUrl } from "@/lib/api";
import {
  businessHasShipped,
  deriveAssistantReceipt,
  deriveLiveWorkstreamCard,
  sanitizeCustomerReply,
  type AssistantReceiptData,
} from "@/lib/takyonCeoUpdates";
import { Tabs, Textarea } from "../composer-ui/lib";
import type { Theme } from "../App";
import type { SettingsSection } from "../settings/Settings";
import type { ChatMessage, ChatProgress, LitebulbBusiness } from "../takyon/useTakyonLitebulb";
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
// `<slug>.fourmanifold.com` (see takyon_cli/web_server._company_base_domain and
// core._product_publish_target). The address bar must show this real canonical
// host — never a fabricated `.app` placeholder.
const PRODUCT_BASE_DOMAIN = "fourmanifold.com";

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

// Durable progress fallback derived from the server-mirrored `live_state`
// snapshot (status + detail). Used when no live streaming turn is producing a
// richer in-flight card — e.g. after a reload while a CEO bootstrap job is
// still running. Presentation-only: it reads the workspace mirror and renders
// the same CEO-style workstream abstraction, never the agent's turn context.
function liveStateProgress(
  workspace: TakyonBusinessWorkspaceResponse | null,
): ChatProgress | null {
  const businessName = workspaceBusinessName(workspace);
  // A business that has already shipped a product must never replay the
  // bootstrap "Starting <business> / Researching the market" placeholder when
  // the mirrored live_state is momentarily empty or generic.
  const pastBootstrap = businessHasShipped(workspace);
  const liveProgress = (
    statusValue: unknown,
    ...parts: unknown[]
  ): ChatProgress | null => {
    const status = String(statusValue || "").trim().toLowerCase();
    if (
      !status
      || ["done", "completed", "success", "failed", "error", "blocked", "cancelled", "idle"].includes(status)
    ) {
      return null;
    }
    const detail = parts.map((part) => String(part || "").trim()).find(Boolean);
    const card = deriveLiveWorkstreamCard({
      running: true,
      businessName,
      statusItems: detail ? [detail] : [],
      progressLines: [status],
      pastBootstrap,
    });
    if (card) return { ...card, live: true };
    return {
      title: `${businessName} update`,
      summary:
        detail
        || (["queued", "scheduled", "pending"].includes(status)
          ? "Queued CEO bootstrap job."
          : pastBootstrap
            ? "I'm on this — picking up where the last workstream left off."
            : "I'm moving this through the next business workstream now."),
      items: [],
      live: true,
    };
  };

  const state = workspace?.live_state;
  if (state && typeof state === "object") {
    const payload = state as Record<string, unknown>;
    // Prefer the CEO's curated headline + summary (business_post_operator_update)
    // when present — this is the warm, customer-facing copy that replaces the raw
    // assistant reasoning stream entirely.
    const headline = String(payload.headline || "").trim();
    const summary = String(payload.summary || "").trim();
    if (headline || summary) {
      return {
        title: headline || `${businessName} update`,
        summary: summary || "I'm on this — here's the latest on your company.",
        items: [],
        live: !["done", "completed", "success", "failed", "error", "idle"].includes(
          String(payload.status || "").trim().toLowerCase(),
        ),
      };
    }
    const progress = liveProgress(payload.status, payload.detail);
    if (progress) return progress;
  }
  return null;
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

function AgentReceipt({ receipt }: { receipt: AssistantReceiptData }) {
  return (
    <div className="lb-receipt">
      <div className="lb-receipt__title">{receipt.title}</div>
      <p className="lb-receipt__summary">{receipt.summary}</p>
      {receipt.liveUrl && (
        <p className="lb-receipt__link">
          Live URL: <a href={receipt.liveUrl} target="_blank" rel="noreferrer">{receipt.liveUrl}</a>
        </p>
      )}
      {receipt.bullets.length > 0 && (
        <div className="lb-receipt__section">
          <div className="lb-receipt__label">What changed</div>
          <ul className="lb-receipt__list">
            {receipt.bullets.map((bullet, index) => (
              <li key={`${bullet}-${index}`}>{bullet}</li>
            ))}
          </ul>
        </div>
      )}
      {receipt.checks.length > 0 && (
        <div className="lb-receipt__section">
          <div className="lb-receipt__label">Validation</div>
          <ul className="lb-receipt__list">
            {receipt.checks.map((check, index) => (
              <li key={`${check}-${index}`}>{check}</li>
            ))}
          </ul>
        </div>
      )}
      {receipt.next && (
        <div className="lb-receipt__section">
          <div className="lb-receipt__label">Next</div>
          <p className="lb-receipt__text">{receipt.next}</p>
        </div>
      )}
      <details className="lb-receipt__details">
        <summary>View build details</summary>
        {receipt.files.length > 0 && (
          <div className="lb-receipt__section">
            <div className="lb-receipt__label">Files changed</div>
            <ul className="lb-receipt__list">
              {receipt.files.map((file) => (
                <li key={file}>{file}</li>
              ))}
            </ul>
          </div>
        )}
        {receipt.checks.length > 0 && (
          <div className="lb-receipt__section">
            <div className="lb-receipt__label">Checks</div>
            <ul className="lb-receipt__list">
              {receipt.checks.map((check, index) => (
                <li key={`${check}-detail-${index}`}>{check}</li>
              ))}
            </ul>
          </div>
        )}
        <div className="lb-receipt__section">
          <div className="lb-receipt__label">Raw response</div>
          <AgentMessageMarkdown text={receipt.rawDetails} />
        </div>
      </details>
    </div>
  );
}

function LiveProgressCard({ progress }: { progress: ChatProgress }) {
  const completed = progress.items.filter((item) => item.status === "complete");
  return (
    <div className="lb-progress">
      <div className="lb-progress__eyebrow">CEO update</div>
      <div className="lb-progress__title">{progress.title}</div>
      <p className="lb-progress__summary">{progress.summary}</p>
      {completed.length > 0 && (
        <div className="lb-progress__section">
          <div className="lb-progress__label">What changed</div>
          <ul className="lb-progress__list">
            {completed.map((item) => (
              <li key={item.key}>{item.completeLabel}</li>
            ))}
          </ul>
        </div>
      )}
      {progress.current && (
        <div className="lb-progress__section">
          <div className="lb-progress__label">Working now</div>
          <p className="lb-progress__text">
            {progress.current}
            {progress.live && (
              <span className="lb-msg__work">
                <span className="lb-typing"><i /><i /><i /></span>
              </span>
            )}
          </p>
        </div>
      )}
      {progress.blocked ? (
        <div className="lb-progress__section">
          <div className="lb-progress__label">Blocked</div>
          <p className="lb-progress__text">{progress.blocked}</p>
        </div>
      ) : progress.next ? (
        <div className="lb-progress__section">
          <div className="lb-progress__label">Next</div>
          <p className="lb-progress__text">{progress.next}</p>
        </div>
      ) : null}
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
}: {
  business: LitebulbBusiness;
  theme: Theme;
  onToggleTheme: () => void;
  onNav: (hash: string) => void;
  onLogout: () => void;
  onOpenSettings: (s: SettingsSection) => void;
}) {
  const [menuOpen, setMenuOpen] = useState(false);
  const onSelect = (label: string) => {
    if (label === "Log out") onLogout();
    else if (MENU_SETTINGS[label]) onOpenSettings(MENU_SETTINGS[label]);
    else if (MENU_ROUTES[label]) onNav(MENU_ROUTES[label]);
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
  progress,
  streamingProgress,
  reviewUrl,
  tab,
  canStop,
  sending,
  onTab,
  onClose,
  onSend,
  onStop,
}: {
  business: LitebulbBusiness;
  messages: ChatMessage[];
  progress: ChatProgress | null;
  streamingProgress?: ChatProgress | null;
  reviewUrl?: string;
  tab: TabKey;
  canStop: boolean;
  sending: boolean;
  onTab: (tab: TabKey) => void;
  onClose: () => void;
  onSend: (text: string) => void;
  onStop: () => void;
}) {
  const [draft, setDraft] = useState("");
  const endRef = useRef<HTMLDivElement>(null);
  // Live streaming card (from the active turn's tool signals) takes priority;
  // the durable live_state card (`progress`) is the reload-safe fallback so the
  // in-flight indicator never disappears between a turn ending and the server
  // mirror catching up.
  const liveCard = streamingProgress ?? progress;

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [liveCard, messages]);

  const submit = () => {
    const text = draft.trim();
    if (!text) return;
    setDraft("");
    onSend(text);
  };

  // The customer NEVER sees the CEO's raw assistant reasoning / chain-of-thought.
  // We render only what the customer should see: their own messages, the curated
  // CEO update card (from the live_state mirror / business_post_operator_update),
  // a structured receipt for finished build/publish replies, and — collapsed and
  // off by default — the raw transcript under a "details" disclosure for debug.
  const userMessages = messages.filter((message) => message.who === "user");
  const agentMessages = messages.filter((message) => message.who === "agent");
  const finishedReply = [...agentMessages]
    .reverse()
    .find((message) => !message.working && Boolean(message.text.trim()));
  const receipt = finishedReply
    ? deriveAssistantReceipt({
        content: finishedReply.text,
        businessName: business.name,
        liveUrl: reviewUrl,
      })
    : null;
  // A finished reply that is NOT a technical build/publish receipt is a normal
  // conversational CEO answer. Render it directly in the default view — but only
  // the customer-safe prose: sanitizeCustomerReply drops any line naming a tool,
  // skill, worker, file path, or build/deploy step. If nothing safe remains, the
  // bubble is suppressed and the content stays only under the opt-in raw log.
  const conversationalReply =
    finishedReply && !receipt ? sanitizeCustomerReply(finishedReply.text) : "";
  const rawTranscript = agentMessages
    .map((message) => message.text.trim())
    .filter(Boolean);

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
        {userMessages.map((message) => (
          <div key={message.id} className="lb-msg lb-msg--user">
            <div className="lb-msg__bubble">{message.text}</div>
          </div>
        ))}
        {liveCard && (
          <div className="lb-msg lb-msg--agent lb-msg--progress">
            <div className="lb-msg__bubble">
              <LiveProgressCard progress={liveCard} />
            </div>
          </div>
        )}
        {!liveCard && receipt && (
          <div className="lb-msg lb-msg--agent">
            <div className="lb-msg__bubble">
              <AgentReceipt receipt={receipt} />
            </div>
          </div>
        )}
        {!liveCard && !receipt && conversationalReply && (
          <div className="lb-msg lb-msg--agent">
            <div className="lb-msg__bubble">
              <AgentMessageMarkdown text={conversationalReply} />
            </div>
          </div>
        )}
        {rawTranscript.length > 0 && (
          <details className="lb-chat__rawlog">
            <summary>View raw assistant log</summary>
            {rawTranscript.map((text, index) => (
              <div key={`raw-${index}`} className="lb-chat__rawentry">
                <AgentMessageMarkdown text={text} />
              </div>
            ))}
          </details>
        )}
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
        <button
          className="lb-chat__stop"
          disabled={!canStop}
          onClick={onStop}
          aria-label="Stop"
          type="button"
        >
          {Icon.stop}
        </button>
        <button className="lb-chat__send" disabled={sending} onClick={submit} aria-label="Send">{Icon.send}</button>
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
  //   2. publish_target  — the backend's canonical expected URL (slug.fourmanifold.com)
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
  const previewAvailable = Boolean(product.preview_available);
  const previewPath = typeof product.preview_path === "string" ? product.preview_path : "product/site";
  const previewStatus = typeof product.preview_status === "string" ? product.preview_status : "";
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
  chatProgress,
  sending,
  onTheme,
  onNav,
  onLogout,
  onOpenSettings,
  onSendPrompt,
  onStopPrompt,
  onSaveChannelCreditBudgets,
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
  chatProgress: ChatProgress | null;
  sending: boolean;
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
  onBuyCreativeCredits: (slug: string, credits: number) => Promise<void>;
  onTractionRangeChange: (range: "D" | "W" | "M" | "Y") => void;
}) {
  const [tab, setTab] = useState<TabKey>("company");
  const [chatOpen, setChatOpen] = useState(true);
  const overview = (workspace?.overview || {}) as Record<string, unknown>;
  const product = (overview.product || {}) as Record<string, unknown>;
  const publicUrl = typeof product.public_url === "string" ? product.public_url : "";
  // Live streaming progress (chatProgress, derived from the active turn's tool
  // signals) takes priority. When no turn is streaming but the server-mirrored
  // live_state still reports running work (e.g. after a reload during a CEO
  // bootstrap), fall back to the durable live_state card so the in-flight
  // indicator never disappears.
  const effectiveProgress = liveStateProgress(workspace);
  const liveProgress = chatProgress ?? effectiveProgress;

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
      />

      <div className="lb-workspace">
        <div className={`lb-chat-dock${chatOpen ? "" : " is-closed"}`}>
          <AgentChat
            business={business}
            messages={chatMessages}
            progress={effectiveProgress}
            streamingProgress={chatProgress}
            reviewUrl={publicUrl || undefined}
            tab={tab}
            canStop={sending || Boolean(liveProgress?.live)}
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
