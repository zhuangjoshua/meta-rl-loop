"use client";

import { useCallback, useEffect, useMemo, useRef, useState, useTransition } from "react";
import {
  ArrowDownRight,
  ArrowUp,
  ArrowUpRight,
  Check,
  ChevronLeft,
  CreditCard,
  ExternalLink,
  FileText,
  Mail,
  Megaphone,
  Power,
  Radio,
  RefreshCw,
  Settings,
  Users,
  X
} from "lucide-react";
import { AutoResizeTextarea, resetAutoResizeTextarea } from "./AutoResizeTextarea";
import type {
  TakyonDashboardModel,
  TakyonDocumentItem,
  TakyonDraftItem,
  TakyonPreview,
  TakyonTargetItem,
  TakyonTaskItem
} from "@/lib/takyon-dashboard";

type FormAction = (formData: FormData) => void | Promise<void>;
type Lever = "twitter" | "community" | "outreach" | "ads";

type TakyonBusinessWorkspaceProps = {
  model: TakyonDashboardModel;
  leverAction: FormAction;
  chatAction: FormAction;
  endAction: FormAction;
};

const leverCopy: Record<Lever, { title: string; body: string; action: string }> = {
  twitter: {
    title: "Publish X post",
    body: "The growth agent will create a grounded launch post from current company state and publish automatically when the X rate limit allows it.",
    action: "Publish post"
  },
  community: {
    title: "Refresh community posts",
    body: "The agent will search real communities and prepare channel-specific launch post copy. It will not claim posting happened.",
    action: "Refresh posts"
  },
  outreach: {
    title: "Refresh leads",
    body: "The agent will identify sourced email leads and write outbound copy.",
    action: "Refresh leads"
  },
  ads: {
    title: "Generate Meta creative",
    body: "The growth agent will generate a display-only Meta UGC video creative and show it here. Meta posting and spend stay disabled.",
    action: "Generate Meta UGC"
  }
};

function relativeTime(value: string) {
  const ms = Date.now() - Date.parse(value);
  if (!Number.isFinite(ms)) return "";
  const minutes = Math.max(0, Math.round(ms / 60_000));
  if (minutes < 1) return "now";
  if (minutes < 60) return `${minutes}m`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours}h`;
  return `${Math.round(hours / 24)}d`;
}

function compact(value: number) {
  return new Intl.NumberFormat("en-US", { notation: "compact", maximumFractionDigits: 1 }).format(value);
}

function money(cents: number, currency: string) {
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: currency.toUpperCase(),
    maximumFractionDigits: 0
  }).format(cents / 100);
}

function microUsd(value: number) {
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    maximumFractionDigits: value > 0 && value < 1_000_000 ? 4 : 2
  }).format(value / 1_000_000);
}

function hostLabel(url: string | null, defaultLabel: string) {
  if (!url) return defaultLabel;
  try {
    return new URL(url).hostname;
  } catch {
    return defaultLabel;
  }
}

function Tile({
  label,
  action,
  area,
  children,
  className = "",
  onClickCapture
}: {
  label: string;
  action?: React.ReactNode;
  area: React.CSSProperties;
  children: React.ReactNode;
  className?: string;
  onClickCapture?: React.MouseEventHandler<HTMLElement>;
}) {
  return (
    <section className={`takyon-tile ${className}`} style={area} onClickCapture={onClickCapture}>
      <header>
        <span>{label}</span>
        {action}
      </header>
      <div className="takyon-tile-body">{children}</div>
    </section>
  );
}

function StatusDot({ tone, pulsing = false }: { tone: string; pulsing?: boolean }) {
  return <span className={`takyon-dot takyon-dot-${tone}${pulsing ? " takyon-dot-pulsing" : ""}`} />;
}

function MetaBadge() {
  return (
    <span className="takyon-ad-platform">
      <svg viewBox="0 0 36 16" xmlns="http://www.w3.org/2000/svg" fill="none" stroke="#0866FF" strokeWidth="2.8" strokeLinejoin="round" strokeLinecap="round">
        <path d="M 3 8 C 3 4, 7 3, 10 5 C 13 8, 16 12, 19 12 C 25 12, 27 7, 24 4 C 21 2, 18 4, 16 7 C 14 11, 11 13, 7 13 C 4 13, 3 11, 3 8 Z" />
      </svg>
      Meta
    </span>
  );
}

function XVerifiedBadge() {
  return (
    <span className="takyon-x-verified" aria-label="Verified">
      <svg viewBox="0 0 24 24" focusable="false" aria-hidden>
        <path d="M22.5 12c0 1.2-1.5 2.1-1.9 3.1-.4 1.1.2 2.7-.6 3.5-.8.8-2.4.2-3.5.6-1 .4-1.9 1.9-3.1 1.9s-2.1-1.5-3.1-1.9c-1.1-.4-2.7.2-3.5-.6-.8-.8-.2-2.4-.6-3.5-.4-1-1.9-1.9-1.9-3.1s1.5-2.1 1.9-3.1c.4-1.1-.2-2.7.6-3.5.8-.8 2.4-.2 3.5-.6 1-.4 1.9-1.9 3.1-1.9s2.1 1.5 3.1 1.9c1.1.4 2.7-.2 3.5.6.8.8.2 2.4.6 3.5.4 1 1.9 1.9 1.9 3.1Z" />
        <path d="m9.2 12.4 1.9 1.9 4.1-4.4" fill="none" stroke="white" strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" />
      </svg>
    </span>
  );
}

function RedditBadge() {
  return (
    <span className="takyon-reddit-badge" aria-hidden>
      <svg viewBox="0 0 24 24" focusable="false">
        <path d="M15.9 7.3 17 3.8l3.1.7" fill="none" stroke="currentColor" strokeLinecap="round" strokeWidth="2" />
        <circle cx="20.1" cy="4.6" r="1.7" fill="currentColor" />
        <circle cx="12" cy="14" r="6.4" fill="currentColor" />
        <circle cx="9.7" cy="13.3" r="1" fill="#ff4500" />
        <circle cx="14.3" cy="13.3" r="1" fill="#ff4500" />
        <path d="M9.4 16.1c1.7 1.1 3.5 1.1 5.2 0" fill="none" stroke="#ff4500" strokeLinecap="round" strokeWidth="1.5" />
      </svg>
    </span>
  );
}

function subredditLabel(item: TakyonDraftItem) {
  const fromUrl = item.url?.match(/reddit\.com\/r\/([^/?#]+)/i)?.[1];
  const fromStatus = item.statusLabel.match(/\br\/([A-Za-z0-9_-]+)/i)?.[1];
  const fromTitle = item.title.match(/\br\/([A-Za-z0-9_-]+)/i)?.[1];
  const raw = fromUrl || fromStatus || fromTitle || item.statusLabel || item.title;
  const slug = raw
    .replace(/^r\//i, "")
    .replace(/[^A-Za-z0-9_-]+/g, "")
    .trim();
  return `r/${slug || "reddit"}`;
}

function RedditCard({ item }: { item: TakyonDraftItem }) {
  const subreddit = subredditLabel(item);
  const body = item.body || item.title;
  const isPublished = Boolean(item.url);
  const when = item.createdAt ? relativeTime(item.createdAt) : "";

  const content = (
    <>
      <div className="takyon-r-post-top">
        <span className="takyon-r-post-sub">{subreddit}</span>
        {when ? <span className="takyon-r-post-when">{when}</span> : null}
      </div>
      <p className="takyon-r-post-title">{body}</p>
    </>
  );

  if (item.url) {
    return (
      <a className="takyon-r-post takyon-r-post-published" href={item.url} target="_blank" rel="noreferrer">
        {content}
      </a>
    );
  }

  return <div className={`takyon-r-post takyon-r-post-pending${isPublished ? "" : ""}`}>{content}</div>;
}

function LineChart({ data }: { data: number[] }) {
  const points = useMemo(() => {
    const maxAbs = Math.max(...data.map((value) => Math.abs(value)), 1);
    return data
      .map((value, index) => {
        const x = data.length === 1 ? 0 : (index / (data.length - 1)) * 100;
        const y = 50 - (value / maxAbs) * 38;
        return `${x},${y}`;
      })
      .join(" ");
  }, [data]);

  return (
    <svg className="takyon-line-chart" viewBox="0 0 100 100" preserveAspectRatio="none" aria-hidden>
      <line className="takyon-line-chart-zero" x1="0" x2="100" y1="50" y2="50" />
      <polyline
        points={points}
        fill="none"
        stroke="#15A34A"
        strokeWidth="2.25"
        strokeLinecap="round"
        strokeLinejoin="round"
        vectorEffect="non-scaling-stroke"
      />
    </svg>
  );
}

function SiteFrame({ preview, kind }: { preview: TakyonPreview; kind: "site" | "product" }) {
  const label = kind === "site" ? "Open website" : "Open product";
  const kindLabel = kind === "site" ? "website" : "product";
  const [iframeLoaded, setIframeLoaded] = useState(false);

  useEffect(() => {
    setIframeLoaded(false);
  }, [preview.url]);

  if (!preview.url) {
    return (
      <div className="takyon-preview-shell-full" aria-label={`${kindLabel} preview pending`}>
        <div className="takyon-browser-bar">
          <span />
          <span />
          <span />
          <strong>{kindLabel}</strong>
        </div>
        <div className="takyon-preview-empty">
          <StatusDot tone="blue" pulsing />
          <span className="takyon-preview-empty-label">{kindLabel}</span>
          <span className="takyon-preview-empty-sub">{preview.subtitle}</span>
        </div>
      </div>
    );
  }

  return (
    <a
      className="takyon-preview-shell-full"
      href={preview.url}
      title={label}
      aria-label={label}
      target="_blank"
      rel="noreferrer"
    >
      <div className="takyon-browser-bar">
        <span />
        <span />
        <span />
        <strong>{hostLabel(preview.url, preview.title)}</strong>
      </div>
      <span className="takyon-preview-corner" aria-hidden>
        <ExternalLink size={11} />
      </span>
      <div className="takyon-preview-iframe-wrap">
        <div className="takyon-preview-iframe-placeholder" aria-hidden>
          <StatusDot tone="blue" pulsing={!iframeLoaded || preview.state === "working"} />
          <strong>{preview.title || kindLabel}</strong>
          <span>{preview.subtitle}</span>
        </div>
        <iframe
          src={preview.url}
          className={`takyon-preview-iframe${iframeLoaded ? " takyon-preview-iframe-ready" : ""}`}
          title={`${kindLabel} preview`}
          loading="eager"
          sandbox="allow-scripts allow-same-origin"
          referrerPolicy="no-referrer"
          tabIndex={-1}
          aria-hidden
          onLoad={() => setIframeLoaded(true)}
        />
        {iframeLoaded && preview.state === "working" ? (
          <div className="takyon-preview-working" aria-hidden>
            <StatusDot tone="blue" pulsing />
          </div>
        ) : null}
      </div>
    </a>
  );
}

function futureDistance(value?: string | null) {
  if (!value) return "";
  const target = Date.parse(value);
  if (!Number.isFinite(target)) return "";
  const minutes = Math.round((target - Date.now()) / 60_000);
  if (minutes <= 0) return "now";
  if (minutes < 60) return `in ${minutes}m`;
  const hours = Math.round(minutes / 60);
  if (hours < 48) return `in ${hours}h`;
  return new Intl.DateTimeFormat(undefined, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" }).format(new Date(target));
}

function taskScheduleLabel(task: TakyonTaskItem) {
  if (!task.scheduledAt) return task.statusLabel;
  const distance = futureDistance(task.scheduledAt);
  if (!distance) return task.statusLabel;
  if (task.category === "cron") return task.status === "paused" ? "paused" : `next ${distance}`;
  if (task.status === "queued") return distance === "now" ? "due now" : `queued ${distance}`;
  return task.statusLabel;
}

function TaskRow({ task, onOpen }: { task: TakyonTaskItem; onOpen: () => void }) {
  return (
    <button className="takyon-task-row" onClick={onOpen} type="button">
      <StatusDot tone={task.tone} />
      <span>{task.title}</span>
      <em>{taskScheduleLabel(task)}</em>
    </button>
  );
}

function DocCard({ document, onOpen }: { document: TakyonDocumentItem; onOpen: () => void }) {
  return (
    <button className="takyon-doc-card" type="button" onClick={onOpen}>
      <FileText size={13} />
      <strong>{document.title}</strong>
      <span>{document.label}</span>
    </button>
  );
}

function DraftRow({ item, icon }: { item: TakyonDraftItem; icon?: React.ReactNode }) {
  const content = (
    <>
      <span className="takyon-row-icon">{icon ?? <FileText size={13} />}</span>
      <div>
        <strong>{item.title}</strong>
        <p>{item.body}</p>
      </div>
      <em>{item.statusLabel}</em>
    </>
  );

  if (item.url) {
    return (
      <a className="takyon-draft-row" href={item.url} target="_blank" rel="noreferrer">
        {content}
      </a>
    );
  }

  return (
    <div className="takyon-draft-row">
      {content}
    </div>
  );
}

function TargetRow({ item }: { item: TakyonTargetItem }) {
  const showEmailedChip = item.status === "emailed";
  const content = (
    <>
      <div>
        <strong>{item.name}</strong>
        <p>{item.detail}</p>
      </div>
      {showEmailedChip ? (
        <span className="takyon-target-chip takyon-target-chip-emailed">
          <Check size={10} />
          sent email
        </span>
      ) : (
        <em>{item.status}</em>
      )}
    </>
  );

  if (item.url) {
    return (
      <a className="takyon-target-row" href={item.url} target="_blank" rel="noreferrer">
        {content}
      </a>
    );
  }

  return <div className="takyon-target-row">{content}</div>;
}

function OperatingLane({
  title,
  status,
  items,
  empty,
  refreshLabel,
  onRefresh,
  children
}: {
  title: React.ReactNode;
  status: string;
  items: unknown[];
  empty: string;
  refreshLabel: string;
  onRefresh: () => void;
  children: React.ReactNode;
}) {
  return (
    <div className="takyon-operating-lane">
      <header>
        <div>
          <strong>{title}</strong>
          <span>{status}</span>
        </div>
        <button type="button" onClick={onRefresh} title={refreshLabel} aria-label={refreshLabel}>
          <RefreshCw size={13} />
        </button>
      </header>
      <div className="takyon-channel-list">{items.length ? children : <div className="takyon-channel-empty">{empty}</div>}</div>
    </div>
  );
}

type PersistentGlow = {
  glowing: Set<string>;
  markSeen: (id: string) => void;
  markAllSeen: () => void;
};

function usePersistentGlow<T>(
  items: T[],
  idOf: (item: T) => string,
  isDone: (item: T) => boolean,
  completedAt?: (item: T) => string | null | undefined,
  storageKey?: string,
  freshWindowMs = 25_000
): PersistentGlow {
  const fullStorageKey = storageKey ? `takyon-seen-${storageKey}` : null;
  const [seen, setSeen] = useState<Set<string>>(() => {
    if (typeof window === "undefined" || !fullStorageKey) return new Set();
    try {
      const raw = window.localStorage.getItem(fullStorageKey);
      if (!raw) return new Set();
      const parsed = JSON.parse(raw);
      if (!Array.isArray(parsed)) return new Set();
      return new Set(parsed.filter((value): value is string => typeof value === "string"));
    } catch {
      return new Set();
    }
  });
  const [glowing, setGlowing] = useState<Set<string>>(new Set());
  const initialMountRef = useRef(true);

  useEffect(() => {
    const newlyDone: string[] = [];
    const now = Date.now();
    for (const item of items) {
      const id = idOf(item);
      if (!isDone(item)) continue;
      if (seen.has(id)) continue;
      if (glowing.has(id)) continue;
      if (initialMountRef.current) {
        const ts = completedAt?.(item);
        if (!ts) continue;
        const parsed = Date.parse(ts);
        if (Number.isNaN(parsed) || now - parsed > freshWindowMs) continue;
      }
      newlyDone.push(id);
    }
    initialMountRef.current = false;
    if (!newlyDone.length) return;
    setGlowing((prev) => {
      const next = new Set(prev);
      for (const id of newlyDone) next.add(id);
      return next;
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [items]);

  const persistSeen = useCallback(
    (nextSeen: Set<string>) => {
      if (!fullStorageKey || typeof window === "undefined") return;
      try {
        window.localStorage.setItem(fullStorageKey, JSON.stringify([...nextSeen]));
      } catch {
        // ignore quota / privacy mode errors
      }
    },
    [fullStorageKey]
  );

  const markSeen = useCallback(
    (id: string) => {
      setSeen((prev) => {
        if (prev.has(id)) return prev;
        const next = new Set(prev);
        next.add(id);
        persistSeen(next);
        return next;
      });
      setGlowing((prev) => {
        if (!prev.has(id)) return prev;
        const next = new Set(prev);
        next.delete(id);
        return next;
      });
    },
    [persistSeen]
  );

  const markAllSeen = useCallback(() => {
    if (!glowing.size) return;
    setSeen((prev) => {
      const next = new Set(prev);
      for (const id of glowing) next.add(id);
      persistSeen(next);
      return next;
    });
    setGlowing(new Set());
  }, [glowing, persistSeen]);

  return { glowing, markSeen, markAllSeen };
}

function ModalShell({
  children,
  onClose,
  wide = false,
  video = false
}: {
  children: React.ReactNode;
  onClose: () => void;
  wide?: boolean;
  video?: boolean;
}) {
  return (
    <div className="takyon-modal-backdrop" onClick={onClose}>
      <div className={`takyon-modal ${wide ? "takyon-modal-wide" : ""}${video ? " takyon-modal-video" : ""}`} onClick={(event) => event.stopPropagation()}>
        {children}
      </div>
    </div>
  );
}

export function TakyonBusinessWorkspace({ model, leverAction, chatAction, endAction }: TakyonBusinessWorkspaceProps) {
  const [lever, setLever] = useState<Lever | null>(null);
  const [shutdownOpen, setShutdownOpen] = useState(false);
  const [openTask, setOpenTask] = useState<TakyonTaskItem | null>(null);
  const [openDoc, setOpenDoc] = useState<TakyonDocumentItem | null>(null);
  const [docsOpen, setDocsOpen] = useState(false);
  const [tasksOpen, setTasksOpen] = useState(false);
  const [openAd, setOpenAd] = useState<TakyonDraftItem | null>(null);
  const [chatDraft, setChatDraft] = useState<string | null>(null);
  const [chatPending, startChatTransition] = useTransition();
  const chatFormRef = useRef<HTMLFormElement>(null);

  const revenue = money(model.metrics.revenueCents, model.metrics.revenueCurrency);
  const hasRevenue = model.metrics.revenueCents > 0;
  const chartData = hasRevenue
    ? model.metrics.chart
    : (model.metrics.chart.length ? model.metrics.chart : [0, 0, 0, 0, 0, 0, 0]).map(() => 0);
  const first = chartData[0] ?? 0;
  const last = chartData[chartData.length - 1] ?? 0;
  const delta = last - first;
  const trendUp = delta >= 0;
  const sentLeadEmails = model.outreach.leads.some((lead) => lead.status === "emailed");
  const ops = model.observability;
  const latestOpsError = ops.lastError && ops.lastError.length > 84 ? `${ops.lastError.slice(0, 81)}...` : ops.lastError;

  const companyKey = model.company.id;
  const xGlow = usePersistentGlow(
    model.social.x,
    (post) => post.id,
    (post) => post.status === "published",
    (post) => post.createdAt,
    `${companyKey}-x`
  );
  const docGlow = usePersistentGlow(
    model.documents,
    (doc) => doc.id,
    () => true,
    (doc) => doc.updatedAt,
    `${companyKey}-docs`
  );
  const adGlow = usePersistentGlow(
    model.ads.campaigns,
    (campaign) => campaign.id,
    (campaign) => Boolean(campaign.url),
    (campaign) => campaign.createdAt,
    `${companyKey}-ads`
  );
  const leadGlow = usePersistentGlow(
    model.outreach.leads,
    (lead) => lead.id,
    (lead) => lead.status === "emailed" || lead.status === "found",
    (lead) => lead.createdAt ?? null,
    `${companyKey}-leads`
  );
  const communityGlow = usePersistentGlow(
    model.social.community,
    (item) => item.id,
    () => true,
    (item) => item.createdAt,
    `${companyKey}-community`
  );
  const supportGlow = usePersistentGlow(
    model.support,
    (msg) => msg.id,
    () => true,
    (msg) => msg.createdAt,
    `${companyKey}-support`
  );

  return (
    <main className="takyon-root takyon-workspace">
      <section className="takyon-board-wrap">
        <header className="takyon-company-bar">
          <div>
            <a href="/dashboard/takyon" title="All companies">
              <ChevronLeft size={18} />
            </a>
            <strong>{model.company.name}</strong>
          </div>
          <nav>
            <span>
              <StatusDot tone={model.live ? "green" : "gray"} pulsing={model.live} />
              {model.live ? "working" : model.company.status}
            </span>
            <button type="button" className="takyon-company-end" title="End app operations" aria-label="End app operations" onClick={() => setShutdownOpen(true)}>
              <Power size={15} />
            </button>
            <a href={`/dashboard/businesses/${model.company.id}`} title="Settings">
              <Settings size={16} />
            </a>
          </nav>
        </header>

        <div className="takyon-board">
          <Tile
            label="Performance"
            area={{ gridColumn: "1 / 4", gridRow: "1 / 8" }}
            action={<span className="takyon-pill">Revenue</span>}
            className="takyon-performance"
          >
            <div className="takyon-metric-line">
              <strong>{revenue}</strong>
              {hasRevenue ? (
                <span className={trendUp ? "takyon-green" : "takyon-red"}>
                  {trendUp ? <ArrowUpRight size={12} /> : <ArrowDownRight size={12} />}
                  {compact(Math.abs(delta))}
                </span>
              ) : null}
            </div>
            <LineChart data={chartData} />
            <div className="takyon-metric-tabs">
              <span>{model.metrics.customers} customers</span>
              <span>{model.metrics.leads} leads</span>
              <span>{model.metrics.posts} posts</span>
            </div>
          </Tile>

          <div style={{ gridColumn: "4 / 7", gridRow: "1 / 4" }} className="takyon-bare">
            <SiteFrame preview={model.previews.site} kind="site" />
          </div>

          <div style={{ gridColumn: "7 / 10", gridRow: "1 / 4" }} className="takyon-bare">
            <SiteFrame preview={model.previews.product} kind="product" />
          </div>

          <div
            style={{ gridColumn: "10 / 13", gridRow: "1 / 4" }}
            className={`takyon-bare${xGlow.glowing.size ? " takyon-just-completed" : ""}`}
            onClickCapture={() => xGlow.markAllSeen()}
          >
            <OperatingLane
              title="X"
              status={model.social.x[0]?.statusLabel || "auto publish when allowed"}
              items={model.social.x}
              empty="Launch copy will appear after the X lane runs"
              refreshLabel="Run X lane"
              onRefresh={() => setLever("twitter")}
            >
              {model.social.x.slice(0, 1).map((post) => (
                <a
                  key={post.id}
                  className={`takyon-x-card ${post.url ? "" : "takyon-x-card-disabled"}`}
                  href={post.url || undefined}
                  target={post.url ? "_blank" : undefined}
                  rel={post.url ? "noreferrer" : undefined}
                  aria-disabled={post.url ? undefined : true}
                >
                  <div>
                    <span aria-hidden>F</span>
                    <strong>Four Manifold</strong>
                    <XVerifiedBadge />
                    <em>@fourmanifold</em>
                  </div>
                  <p>{post.body}</p>
                  <small>{relativeTime(post.createdAt)}</small>
                </a>
              ))}
            </OperatingLane>
          </div>

          <Tile
            label="In progress"
            area={{ gridColumn: "4 / 10", gridRow: "4 / 8" }}
            action={
              <button type="button" onClick={() => setTasksOpen(true)}>
                Manage
              </button>
            }
          >
            <div className="takyon-task-list">
              {model.tasks.active.length ? (
                model.tasks.active.slice(0, 5).map((task) => <TaskRow key={task.id} task={task} onOpen={() => setOpenTask(task)} />)
              ) : (
                <div className="takyon-channel-empty">Operating plan is clear</div>
              )}
            </div>
          </Tile>

          <div style={{ gridColumn: "1 / 4", gridRow: "8 / 10" }} className="takyon-payment-card">
            <CreditCard size={16} />
            <div>
              <strong>{model.payments.label}</strong>
              <span>{model.payments.connected ? "Checkout is ready" : "Revenue path is queued"}</span>
            </div>
          </div>

          <Tile
            label="Documents"
            area={{ gridColumn: "4 / 10", gridRow: "8 / 10" }}
            action={
              <button type="button" onClick={() => setDocsOpen(true)}>
                See all
              </button>
            }
            className={docGlow.glowing.size ? "takyon-just-completed" : ""}
            onClickCapture={() => docGlow.markAllSeen()}
          >
            <div className="takyon-doc-grid">
              {model.documents.length ? (
                model.documents.slice(0, 4).map((document) => <DocCard key={document.id} document={document} onOpen={() => setOpenDoc(document)} />)
              ) : (
                <div className="takyon-channel-empty">Reports will appear here</div>
              )}
            </div>
          </Tile>

          {(() => {
            const campaigns = model.ads.campaigns;
            const primaryAd =
              campaigns.find((c) => Boolean(c.url)) ??
              campaigns.find((c) => c.status !== "failed") ??
              campaigns[0] ??
              null;
            return (
              <div
                style={{ gridColumn: "10 / 13", gridRow: "4 / 7" }}
                className={`takyon-bare${adGlow.glowing.size ? " takyon-just-completed" : ""}`}
                onClickCapture={() => adGlow.markAllSeen()}
              >
                <OperatingLane
                  title="Meta creative"
                  status={primaryAd?.url ? "completed" : primaryAd?.statusLabel || model.ads.budgetLabel}
                  items={campaigns}
                  empty="Creative generation will appear here"
                  refreshLabel="Run Meta creative lane"
                  onRefresh={() => setLever("ads")}
                >
                  {primaryAd && primaryAd.url ? (
                    <button
                      type="button"
                      className="takyon-ad-full"
                      onClick={() => setOpenAd(primaryAd)}
                    >
                      <video
                        src={primaryAd.url}
                        muted
                        loop
                        playsInline
                        autoPlay
                        preload="metadata"
                      />
                      <MetaBadge />
                    </button>
                  ) : primaryAd && primaryAd.status === "failed" ? (
                    <button
                      type="button"
                      className="takyon-ad-full takyon-ad-full-failed"
                      onClick={() => setOpenAd(primaryAd)}
                    >
                      <X size={22} />
                      <span>Creative blocked</span>
                      <MetaBadge />
                    </button>
                  ) : primaryAd ? (
                    <div className="takyon-ad-full takyon-ad-full-generating" aria-live="polite">
                      <RefreshCw size={18} className="takyon-spin" />
                      <span>Generating</span>
                      <MetaBadge />
                    </div>
                  ) : null}
                </OperatingLane>
              </div>
            );
          })()}

          <div
            style={{ gridColumn: "10 / 13", gridRow: "7 / 10" }}
            className={`takyon-bare${leadGlow.glowing.size ? " takyon-just-completed" : ""}`}
            onClickCapture={() => leadGlow.markAllSeen()}
          >
            <OperatingLane
              title="Leads"
              status={model.outreach.leads.length ? `${model.outreach.leads.length} ${sentLeadEmails ? "sent" : "ready to send"}` : "automatic outreach"}
              items={model.outreach.leads}
              empty="Email leads appear after outreach runs"
              refreshLabel="Refresh leads"
              onRefresh={() => setLever("outreach")}
            >
              {model.outreach.leads.slice(0, 3).map((lead) => (
                <TargetRow key={lead.id} item={lead} />
              ))}
            </OperatingLane>
          </div>

          <Tile
            label={`Customer service · ${model.support.length}`}
            area={{ gridColumn: "4 / 10", gridRow: "10 / 13" }}
            className={supportGlow.glowing.size ? "takyon-just-completed" : ""}
            onClickCapture={() => supportGlow.markAllSeen()}
          >
            <div className="takyon-support-list">
              {model.support.length ? (
                model.support.slice(0, 3).map((message) => <DraftRow key={message.id} item={message} icon={<Mail size={13} />} />)
              ) : (
                <div className="takyon-channel-empty">All clear</div>
              )}
            </div>
          </Tile>

          <div
            style={{ gridColumn: "10 / 13", gridRow: "10 / 13" }}
            className={`takyon-bare${communityGlow.glowing.size ? " takyon-just-completed" : ""}`}
            onClickCapture={() => communityGlow.markAllSeen()}
          >
            <OperatingLane
              title={
                <span className="takyon-lane-title-with-badge">
                  <RedditBadge />
                  Reddit
                </span>
              }
              status={model.social.community.length ? `${model.social.community.length} sent` : "automatic research"}
              items={model.social.community}
              empty="Reddit posts will appear here"
              refreshLabel="Refresh Reddit posts"
              onRefresh={() => setLever("community")}
            >
              {model.social.community.slice(0, 3).map((community) => (
                <RedditCard key={community.id} item={community} />
              ))}
            </OperatingLane>
          </div>

          <Tile label="Team & ops" area={{ gridColumn: "1 / 4", gridRow: "10 / 13" }}>
            <div className="takyon-team-list">
              {model.team.slice(0, 2).map((member) => (
                <div key={member.id}>
                  <span>
                    <Users size={13} />
                  </span>
                  <div>
                    <strong>{member.name}</strong>
                    <p>{member.role}</p>
                  </div>
                </div>
              ))}
              <div className="takyon-ops-list" aria-label="Operations">
                <div className="takyon-ops-row">
                  <span>requests 24h</span>
                  <strong>{compact(ops.requests24h)}</strong>
                </div>
                <div className="takyon-ops-row">
                  <span>blocked</span>
                  <strong>{compact(ops.blocked24h)}</strong>
                </div>
                <div className="takyon-ops-row">
                  <span>rate limited</span>
                  <strong>{compact(ops.rateLimited24h)}</strong>
                </div>
                <div className="takyon-ops-row">
                  <span>AI 24h</span>
                  <strong>{compact(ops.aiRequests24h)} / {microUsd(ops.aiCostMicrousd24h)}</strong>
                </div>
              </div>
              {latestOpsError ? <p className="takyon-ops-error">{latestOpsError}</p> : null}
              {model.learning.campaign ? (
                <p className="takyon-learning-note">
                  <strong>Campaign</strong>
                  {model.learning.campaign}
                </p>
              ) : null}
              {model.learning.customer ? (
                <p className="takyon-learning-note">
                  <strong>Customer</strong>
                  {model.learning.customer}
                </p>
              ) : null}
              <button type="button" className="takyon-add-member" title="Team invites are not wired yet">
                + Add member
              </button>
            </div>
          </Tile>
        </div>
      </section>

      <aside className="takyon-chat-rail">
        <div className="takyon-chat-head">
          <StatusDot tone="green" pulsing />
          <span>CEO</span>
        </div>
        <div className="takyon-chat-scroll">
          {model.chat.length ? (
            model.chat.map((message) => (
              <article key={message.id} className={message.title === "CEO" ? "" : "takyon-chat-you"}>
                <strong>{message.title}</strong>
                <p>{message.body}</p>
              </article>
            ))
          ) : (
            <article>
              <strong>CEO</strong>
              <p>I am watching product, growth, sales, and support. Tell me what to prioritize.</p>
            </article>
          )}
          {chatPending && chatDraft ? (
            <article className="takyon-chat-you">
              <strong>You</strong>
              <p>{chatDraft}</p>
            </article>
          ) : null}
          {chatPending ? (
            <article className="takyon-chat-thinking">
              <p>Thinking</p>
            </article>
          ) : null}
        </div>
        <form
          ref={chatFormRef}
          onSubmit={(event) => {
            event.preventDefault();
            if (chatPending) return;
            const form = event.currentTarget;
            const formData = new FormData(form);
            const body = String(formData.get("body") || "").trim();
            if (!body) return;
            setChatDraft(body);
            startChatTransition(async () => {
              await chatAction(formData);
              form.reset();
              const bodyField = form.elements.namedItem("body");
              resetAutoResizeTextarea(bodyField instanceof HTMLTextAreaElement ? bodyField : null, 130);
              setChatDraft(null);
            });
          }}
          className="takyon-chat-form"
        >
          <AutoResizeTextarea
            name="body"
            rows={1}
            maxAutoHeight={130}
            placeholder={chatPending ? "CEO is thinking..." : "Ask Takyon anything"}
            disabled={chatPending}
          />
          <button type="submit" aria-label="Send" disabled={chatPending}>
            {chatPending ? <RefreshCw size={15} className="takyon-spin" /> : <ArrowUp size={16} />}
          </button>
        </form>
      </aside>

      {lever ? (
        <ModalShell onClose={() => setLever(null)}>
          <div className="takyon-modal-head">
            <div>
              <Radio size={16} />
              <h2>{leverCopy[lever].title}</h2>
            </div>
            <button type="button" onClick={() => setLever(null)} aria-label="Close">
              <X size={15} />
            </button>
          </div>
          <div className="takyon-modal-body">
            <p>{leverCopy[lever].body}</p>
            <form action={leverAction}>
              <input type="hidden" name="lever" value={lever} />
              <button type="submit" className="takyon-primary-button">
                {leverCopy[lever].action}
              </button>
            </form>
          </div>
        </ModalShell>
      ) : null}

      {shutdownOpen ? (
        <ModalShell onClose={() => setShutdownOpen(false)}>
          <div className="takyon-modal-head">
            <div>
              <Power size={16} />
              <h2>End app operations</h2>
            </div>
            <button type="button" onClick={() => setShutdownOpen(false)} aria-label="Close">
              <X size={15} />
            </button>
          </div>
          <div className="takyon-modal-body">
            <p>
              This archives the company, takes the site offline in Takyon, cancels queued/running workflow jobs and agent runs, and attempts to remove Vercel aliases/deployments.
            </p>
            <form action={endAction} className="takyon-shutdown-form">
              <textarea name="reason" rows={3} placeholder="Reason for ending this app" defaultValue="Operator ended this generated app." />
              <button type="submit" className="takyon-danger-button">
                End app
              </button>
            </form>
          </div>
        </ModalShell>
      ) : null}

      {openTask ? (
        <ModalShell onClose={() => setOpenTask(null)}>
          <div className="takyon-modal-head">
            <div>
              <Check size={16} />
              <h2>{openTask.title}</h2>
            </div>
            <button type="button" onClick={() => setOpenTask(null)} aria-label="Close">
              <X size={15} />
            </button>
          </div>
          <div className="takyon-modal-body">
            <p>{openTask.description}</p>
            <span className={`takyon-status-chip takyon-status-${openTask.tone}`}>{openTask.statusLabel}</span>
          </div>
        </ModalShell>
      ) : null}

      {openDoc ? (
        <ModalShell onClose={() => setOpenDoc(null)} wide>
          <div className="takyon-modal-head">
            <div>
              <FileText size={16} />
              <h2>{openDoc.title}</h2>
            </div>
            <button type="button" onClick={() => setOpenDoc(null)} aria-label="Close">
              <X size={15} />
            </button>
          </div>
          <div className="takyon-doc-reader">{openDoc.content}</div>
        </ModalShell>
      ) : null}

      {docsOpen ? (
        <ModalShell onClose={() => setDocsOpen(false)} wide>
          <div className="takyon-modal-head">
            <div>
              <FileText size={16} />
              <h2>Documents</h2>
            </div>
            <button type="button" onClick={() => setDocsOpen(false)} aria-label="Close">
              <X size={15} />
            </button>
          </div>
          <div className="takyon-list-modal">
            {model.documents.map((document) => (
              <button
                key={document.id}
                type="button"
                onClick={() => {
                  setDocsOpen(false);
                  setOpenDoc(document);
                }}
              >
                <span>{document.title}</span>
                <em>{relativeTime(document.updatedAt)}</em>
              </button>
            ))}
          </div>
        </ModalShell>
      ) : null}

      {tasksOpen ? (
        <ModalShell onClose={() => setTasksOpen(false)} wide>
          <div className="takyon-modal-head">
            <div>
              <Megaphone size={16} />
              <h2>Tasks</h2>
            </div>
            <button type="button" onClick={() => setTasksOpen(false)} aria-label="Close">
              <X size={15} />
            </button>
          </div>
          <div className="takyon-list-modal">
            {model.tasks.all.map((task) => (
              <button
                key={task.id}
                type="button"
                onClick={() => {
                  setTasksOpen(false);
                  setOpenTask(task);
                }}
              >
                <span>{task.title}</span>
                <em>{taskScheduleLabel(task)}</em>
              </button>
            ))}
          </div>
        </ModalShell>
      ) : null}

      {openAd ? (
        <ModalShell onClose={() => setOpenAd(null)} video>
          <div className="takyon-video-modal">
            <button type="button" onClick={() => setOpenAd(null)} aria-label="Close">
              <X size={16} />
            </button>
            {openAd.url ? (
              <video className="takyon-video-preview" src={openAd.url} controls playsInline autoPlay />
            ) : (
              <div className="takyon-video-empty">Creative is not ready yet</div>
            )}
          </div>
        </ModalShell>
      ) : null}
    </main>
  );
}
