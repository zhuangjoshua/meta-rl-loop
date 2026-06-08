import { useEffect, useRef, useState } from "react";
import type {
  TakyonBusinessCreativeCreditsResponse,
  TakyonBusinessTractionResponse,
  TakyonBusinessWorkspaceResponse,
} from "@/lib/api";
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
  refresh: <S d="M13 7a5 5 0 10-.6 3.4M13 4v3h-3" />,
  external: <S d="M9 3h4v4M13 3l-6 6M11 9v3.5H3.5V5H7" />,
  collapse: <S d="M9.5 4l-4 4 4 4" w={13} />,
  chat: <S d="M2 3.5h12v7H6.5l-3 2.5v-2.5H2z" />,
};

function siteHost(name: string) {
  return name.toLowerCase().replace(/[^a-z0-9]/g, "") + ".app";
}

function backgroundRunProgress(
  workspace: TakyonBusinessWorkspaceResponse | null,
): ChatProgress | null {
  const liveProgress = (statusValue: unknown, ...parts: unknown[]): ChatProgress | null => {
    const status = String(statusValue || "").trim().toLowerCase();
    if (!status || ["done", "completed", "success", "failed", "error", "blocked", "cancelled", "idle"].includes(status)) {
      return null;
    }
    const detail = parts
      .map((part) => String(part || "").trim())
      .find(Boolean);
    return {
      text: detail || (["queued", "scheduled", "pending"].includes(status) ? "Queued CEO bootstrap job." : "Working…"),
      live: true,
    };
  };

  const run = workspace?.background_run;
  if (run && typeof run === "object") {
    const payload = run as Record<string, unknown>;
    const progress = liveProgress(payload.status, payload.detail);
    if (progress) return progress;
  }

  const overview = workspace?.overview;
  if (!overview || typeof overview !== "object") {
    return null;
  }
  const currentAction = (overview as Record<string, unknown>).current_action;
  if (currentAction && typeof currentAction === "object") {
    const payload = currentAction as Record<string, unknown>;
    const progress = liveProgress(payload.status, payload.detail, payload.label);
    if (progress) return progress;
  }
  const ceoLoop = (overview as Record<string, unknown>).ceo_loop;
  if (ceoLoop && typeof ceoLoop === "object") {
    const payload = ceoLoop as Record<string, unknown>;
    const progress = liveProgress(payload.status, payload.detail, payload.next_action, payload.headline);
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
  ["Billing", "Profile settings"],
  ["FAQ"],
  ["Log out"],
];

const MENU_ROUTES: Record<string, string> = {
  "My portfolio": "/",
  "New company": "/app/new",
  "FAQ": "/faq",
};

const MENU_SETTINGS: Record<string, SettingsSection> = {
  Billing: "billing",
  "Profile settings": "profile",
};

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
      <button className="lb-topbar__group lb-brand" onClick={() => onNav("/")} aria-label="Litebulb — all companies" title="All companies">
        <BulbMark size={22} tone="ink" />
        <span className="lb-topbar__name">Litebulb</span>
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
  tab,
  sending,
  onTab,
  onClose,
  onSend,
}: {
  business: LitebulbBusiness;
  messages: ChatMessage[];
  progress: ChatProgress | null;
  tab: TabKey;
  sending: boolean;
  onTab: (tab: TabKey) => void;
  onClose: () => void;
  onSend: (text: string) => void;
}) {
  const [draft, setDraft] = useState("");
  const endRef = useRef<HTMLDivElement>(null);
  const progressText = (progress?.text || "").trim() || (progress?.live ? "Working…" : "");

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages, progressText]);

  const submit = () => {
    const text = draft.trim();
    if (!text) return;
    setDraft("");
    onSend(text);
  };

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
        {messages.map((message) => (
          <div key={message.id} className={`lb-msg lb-msg--${message.who}`}>
            <div className="lb-msg__bubble">{message.text}</div>
          </div>
        ))}
        {progressText && (
          <div className="lb-msg lb-msg--agent lb-msg--progress">
            <div className="lb-msg__bubble">
              {progressText}
              {progress?.live && (
                <span className="lb-msg__work">
                  <span className="lb-typing"><i /><i /><i /></span>
                </span>
              )}
            </div>
          </div>
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
        <button className="lb-chat__send" disabled={sending} onClick={submit} aria-label="Send">{Icon.send}</button>
      </div>
    </aside>
  );
}

function ProductPreview({
  business,
  workspace,
  previewUrl,
  publicUrl,
}: {
  business: LitebulbBusiness;
  workspace: TakyonBusinessWorkspaceResponse | null;
  previewUrl: string;
  publicUrl?: string;
}) {
  const [device, setDevice] = useState<"desktop" | "mobile">("desktop");
  const site = publicUrl || siteHost(business.name);
  const overview = (workspace?.overview || {}) as Record<string, unknown>;
  const product = (overview.product || {}) as Record<string, unknown>;
  const outputs = Array.isArray(workspace?.outputs) ? workspace.outputs : [];
  const sourcePath = typeof product.source_path === "string" ? product.source_path : "";
  const publishStatus = typeof product.publish_status === "string" ? product.publish_status : "";
  const productStatus = typeof product.status === "string" ? product.status : "";
  const frameUrl = publicUrl || previewUrl || "";
  const hasLocalSource = Boolean(
    sourcePath
    || outputs.some((item) => {
      const output = item && typeof item === "object" ? item as Record<string, unknown> : {};
      const path = typeof output.path === "string" ? output.path : "";
      return path.startsWith("product/site/");
    }),
  );
  const isLoading = !workspace;
  const isBuilding = !frameUrl && !isLoading && hasLocalSource && publishStatus !== "published";
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
          <span className="lb-browser__addr">{site}</span>
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
  previewUrl,
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
  onTractionRangeChange,
}: {
  business: LitebulbBusiness;
  workspace: TakyonBusinessWorkspaceResponse | null;
  creativeCredits: TakyonBusinessCreativeCreditsResponse | null;
  previewUrl: string;
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
  onTractionRangeChange: (range: "D" | "W" | "M" | "Y") => void;
}) {
  const [tab, setTab] = useState<TabKey>("company");
  const [chatOpen, setChatOpen] = useState(true);
  const overview = (workspace?.overview || {}) as Record<string, unknown>;
  const product = (overview.product || {}) as Record<string, unknown>;
  const publicUrl = typeof product.public_url === "string" ? product.public_url : "";
  const effectiveProgress = chatProgress ?? backgroundRunProgress(workspace);

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
            tab={tab}
            sending={sending}
            onTab={setTab}
            onClose={() => setChatOpen(false)}
            onSend={onSendPrompt}
          />
        </div>

        <div className={`lb-main${tab === "product" ? " lb-main--preview" : ""}`}>
          {tab === "product" ? (
            <ProductPreview business={business} workspace={workspace} previewUrl={previewUrl} publicUrl={publicUrl} />
          ) : (
            <CompanyTab
              business={business}
              workspace={workspace}
              creativeCredits={creativeCredits}
              traction={traction}
              tractionRange={tractionRange}
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
