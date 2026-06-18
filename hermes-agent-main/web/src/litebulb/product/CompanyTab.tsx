import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  TAKYON_BASE_PATH,
  api,
  type TakyonBusinessCreativeCreditsResponse,
  type TakyonBusinessFileReadResponse,
  type TakyonBusinessTractionPoint,
  type TakyonBusinessTractionResponse,
  type TakyonBusinessWorkspaceResponse,
} from "@/lib/api";
import type { LitebulbBusiness } from "../takyon/useTakyonLitebulb";
import "./companytab.css";

const S = (d: string, w = 15) => (
  <svg width={w} height={w} viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"><path d={d} /></svg>
);

const I = {
  doc: S("M4 1.6h5l3 3V14.4H4zM9 1.6V4.6h3"),
  ext: S("M9 3h4v4M13 3l-6 6M11 9.5V13H3V5h3.5"),
  mail: S("M2 4h12v8H2zM2.5 4.5L8 8.5l5.5-4"),
  mega: S("M2.5 6.4v3.2l7.5 2.9V3.5zM10 5.4a2.6 2.6 0 010 5.2"),
  play: <svg width="15" height="15" viewBox="0 0 16 16" fill="currentColor"><path d="M5 3.4l8 4.6-8 4.6z" /></svg>,
  image: S("M2 3h12v10H2zM2 11l3.5-3.5 2.5 2.5 3-3L14 10"),
  film: S("M2 3h12v10H2zM5 3v10M11 3v10M2 6.5h3M11 6.5h3M2 9.5h3M11 9.5h3"),
  reply: S("M2.5 3.6h11v6.2H6.7L4 12.2V9.8H2.5z", 14),
  rt: S("M4.5 5L3 6.5 4.5 8M3 6.5h7.5a1.5 1.5 0 011.5 1.5v1M11.5 11l1.5-1.5L11.5 8M13 9.5H5.5A1.5 1.5 0 014 8V7", 14),
  like: <svg width="14" height="14" viewBox="0 0 16 16" fill="currentColor" aria-hidden="true"><path d="M8 13.7S2.6 10.3 2.6 6.5A2.6 2.6 0 018 4a2.6 2.6 0 015.4 2.5c0 3.8-5.4 7.2-5.4 7.2z" /></svg>,
  close: S("M4 4l8 8M12 4l-8 8"),
};

type Metric = "revenue" | "users" | "usage" | "pageviews" | "visits";
type DistTab = "x" | "video" | "ads" | "email";
type ChannelBudgetKey = "x" | "meta" | "reddit";

type DocumentPreviewState = {
  output: Record<string, unknown>;
  file: TakyonBusinessFileReadResponse | null;
  loading: boolean;
  error: string;
};

const METRICS: Array<{ key: Metric; label: string; prefix: string }> = [
  { key: "revenue", label: "Revenue", prefix: "$" },
  { key: "users", label: "Users", prefix: "" },
  { key: "usage", label: "Usage", prefix: "" },
  { key: "pageviews", label: "Pageviews", prefix: "" },
  { key: "visits", label: "Visits", prefix: "" },
];

const RANGE_LABEL: Record<"D" | "W" | "M" | "Y", string> = {
  D: "today",
  W: "this week",
  M: "this month",
  Y: "this year",
};

const TEXT_OUTPUT_SUFFIXES = new Set([".md", ".txt", ".json", ".js", ".css", ".html", ".ts", ".tsx", ".jsx", ".yml", ".yaml"]);
const MEDIA_OUTPUT_SUFFIXES = new Set([".png", ".jpg", ".jpeg", ".webp", ".gif", ".mp4", ".mov", ".webm", ".m4v"]);
const VIDEO_OUTPUT_SUFFIXES = new Set([".mp4", ".mov", ".webm", ".m4v"]);
const HIDDEN_DOCUMENT_SUFFIXES = new Set([".js", ".jsx", ".ts", ".tsx"]);
const CHANNEL_BUDGET_KEYS: ChannelBudgetKey[] = ["x", "meta", "reddit"];

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" ? value as Record<string, unknown> : {};
}

function asList(value: unknown): Array<Record<string, unknown>> {
  return Array.isArray(value) ? value.filter((item): item is Record<string, unknown> => Boolean(item) && typeof item === "object") : [];
}

function asText(value: unknown) {
  return String(value || "").trim();
}

function formatCount(value: number) {
  return value.toLocaleString();
}

function formatMetric(prefix: string, value: number) {
  if (!Number.isFinite(value)) return "—";
  if (prefix === "$") return `$${(value / 100).toLocaleString(undefined, { minimumFractionDigits: 0, maximumFractionDigits: 0 })}`;
  return value.toLocaleString();
}

function formatUsdCents(value: number) {
  if (!Number.isFinite(value)) return "—";
  return `$${(value / 100).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

function pctDelta(current: number, previous: number) {
  if (!previous) return current ? 100 : 0;
  return ((current - previous) / previous) * 100;
}

function metricValue(point: TakyonBusinessTractionPoint, metric: Metric) {
  if (metric === "revenue") return Number(point.revenue_cents || 0);
  if (metric === "users") return Number(point.users || 0);
  if (metric === "usage") return Number(point.usage_events || 0);
  if (metric === "pageviews") return Number(point.pageviews || 0);
  return Number(point.visits || 0);
}

function totalForMetric(totals: TakyonBusinessTractionResponse["totals"], metric: Metric) {
  if (metric === "revenue") return Number(totals.revenue_cents || 0);
  if (metric === "users") return Number(totals.users || 0);
  if (metric === "usage") return Number(totals.usage_events || 0);
  if (metric === "pageviews") return Number(totals.pageviews || 0);
  return Number(totals.visits || 0);
}

function seriesForMetric(points: TakyonBusinessTractionPoint[], metric: Metric) {
  return points.map((point) => metricValue(point, metric));
}

function readInt(value: unknown): number | null {
  if (value === null || value === undefined || value === "") return null;
  const number = Number(value);
  if (!Number.isFinite(number)) return null;
  return Math.max(0, Math.round(number));
}

function pickFirstInt(...values: unknown[]) {
  for (const value of values) {
    const next = readInt(value);
    if (next !== null) return next;
  }
  return null;
}

function statusLabel(value: unknown) {
  const status = asText(value).toLowerCase();
  if (!status) return "idle";
  if (status === "published_local" || status === "suppressed_test_mode") return "local preview";
  if (status === "draft_only") return "draft ready";
  if (status === "created_paused") return "paused";
  if (status === "activated" || status === "externally_launched" || status === "live") return "live";
  return status.replace(/_/g, " ");
}

function outputSuffix(path: string) {
  const clean = asText(path).toLowerCase();
  const index = clean.lastIndexOf(".");
  return index >= 0 ? clean.slice(index) : "";
}

function inlinePreviewFile(
  businessSlug: string,
  output: Record<string, unknown>,
): TakyonBusinessFileReadResponse | null {
  const path = asText(output.path);
  const content = output.preview_content;
  if (!path || typeof content !== "string") return null;
  const size = Number(output.preview_size);
  return {
    business_slug: businessSlug,
    path,
    size: Number.isFinite(size) ? size : content.length,
    content,
    truncated: Boolean(output.preview_truncated),
  };
}

function buildAssetUrl(slug: string, path: string) {
  return `${TAKYON_BASE_PATH}/api/takyon/businesses/${encodeURIComponent(slug)}/asset?path=${encodeURIComponent(path)}`;
}

function channelAllocatedCredits(channel: Record<string, unknown>) {
  const campaigns = asList(channel.campaigns);
  const latestCampaign = asRecord(campaigns[0]);
  const latestJob = asRecord(channel.latest_job);
  const direct = pickFirstInt(
    channel.allocated_credits,
    channel.allocation_credits,
    channel.credits_allocated,
    channel.budget_credits,
    channel.channel_budget_credits,
    channel.creative_credit_allocation,
    channel.requested_credits,
    channel.reserved_credits,
    asRecord(channel.allocation).credits,
    asRecord(channel.budget).credits,
    asRecord(channel.channel_budget).credits,
    latestCampaign.allocated_credits,
    latestCampaign.allocation_credits,
    latestCampaign.credits_allocated,
    latestCampaign.requested_credits,
    latestCampaign.reserved_credits,
    latestJob.allocated_credits,
    latestJob.allocation_credits,
    latestJob.requested_credits,
    latestJob.reserved_credits,
  );
  if (direct !== null) return direct;
  const campaignTotal = campaigns.reduce((sum, campaign) => {
    const next = pickFirstInt(
      campaign.allocated_credits,
      campaign.allocation_credits,
      campaign.credits_allocated,
      campaign.requested_credits,
      campaign.reserved_credits,
      campaign.credits_charged,
    );
    return sum + (next || 0);
  }, 0);
  if (campaignTotal > 0) return campaignTotal;
  return pickFirstInt(
    channel.credits_charged,
    latestCampaign.credits_charged,
    latestJob.credits_charged,
    latestJob.requested_credits,
  ) || 0;
}

function channelBudgetSnapshot(
  creativeCredits: TakyonBusinessCreativeCreditsResponse | null,
  key: ChannelBudgetKey,
) {
  const container = asRecord(creativeCredits?.channel_budgets ?? creativeCredits?.channels);
  return asRecord(container[key]);
}

function channelStatLine(channelKey: "x" | "meta" | "reddit", channel: Record<string, unknown>) {
  if (channelKey === "x") {
    const pieces: string[] = [];
    const publishedCount = readInt(channel.published_count) || 0;
    if (publishedCount > 0) pieces.push(`${formatCount(publishedCount)} posts`);
    const charged = pickFirstInt(channel.credits_charged);
    if (charged) pieces.push(`${charged} credits charged`);
    const status = statusLabel(channel.status);
    if (!pieces.length || status !== "idle") pieces.push(status);
    return pieces.join(" · ");
  }
  const latestCampaign = asRecord(asList(channel.campaigns)[0]);
  const latestMetrics = asRecord(latestCampaign.latest_metrics);
  const pieces: string[] = [];
  const campaignCount = readInt(channel.campaign_count) || 0;
  if (campaignCount > 0) pieces.push(`${formatCount(campaignCount)} campaign${campaignCount === 1 ? "" : "s"}`);
  const impressions = readInt(latestMetrics.impressions);
  if (impressions) pieces.push(`${formatCount(impressions)} impressions`);
  const clicks = readInt(latestMetrics.clicks);
  if (clicks) pieces.push(`${formatCount(clicks)} clicks`);
  const charged = pickFirstInt(channel.credits_charged, latestCampaign.credits_charged);
  if (charged) pieces.push(`${charged} credits charged`);
  const status = statusLabel(latestCampaign.status || channel.status);
  if (!pieces.length || status !== "idle") pieces.push(status);
  return pieces.join(" · ");
}

function Modal({
  title,
  sub,
  wide,
  onClose,
  children,
}: {
  title: string;
  sub?: React.ReactNode;
  wide?: boolean;
  onClose: () => void;
  children: React.ReactNode;
}) {
  return (
    <div className="lb-tmod-scrim" onClick={onClose}>
      <div className={`lb-tmod${wide ? " lb-tmod--wide" : ""}`} role="dialog" aria-modal="true" aria-label={title} onClick={(event) => event.stopPropagation()}>
        <header className="lb-tmod__head">
          <h3 className="lb-tmod__title">{title}{sub && <span className="lb-tmod__sub2">{sub}</span>}</h3>
          <button className="lb-tmod__x" type="button" onClick={onClose} aria-label="Close">{I.close}</button>
        </header>
        <div className="lb-tmod__body">{children}</div>
      </div>
    </div>
  );
}

function Chart({ values, up }: { values: number[]; up: boolean }) {
  const safe = values.length > 1 ? values : [0, values[0] || 0];
  const W = 720;
  const H = 200;
  const pad = 10;
  const min = Math.min(...safe);
  const max = Math.max(...safe);
  const span = max - min || 1;
  const X = (i: number) => pad + (i / (safe.length - 1)) * (W - pad * 2);
  const Y = (v: number) => pad + (1 - (v - min) / span) * (H - pad * 2);
  const line = safe.map((value, index) => `${index === 0 ? "M" : "L"} ${X(index).toFixed(1)} ${Y(value).toFixed(1)}`).join(" ");
  const area = `${line} L ${X(safe.length - 1).toFixed(1)} ${H} L ${X(0).toFixed(1)} ${H} Z`;
  const gradientId = `lb-traction-${up ? "up" : "down"}`;
  return (
    <svg className={`lb-cht ${up ? "is-up" : "is-down"}`} viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" aria-hidden="true">
      <defs><linearGradient id={gradientId} x1="0" y1="0" x2="0" y2="1"><stop offset="0" className="lb-cht__g0" /><stop offset="1" className="lb-cht__g1" /></linearGradient></defs>
      <path className="lb-cht__area" d={area} fill={`url(#${gradientId})`} />
      <path className="lb-cht__line" d={line} fill="none" vectorEffect="non-scaling-stroke" pathLength={1} />
    </svg>
  );
}

function Traction({
  traction,
  range,
  onRangeChange,
}: {
  traction: TakyonBusinessTractionResponse | null;
  range: "D" | "W" | "M" | "Y";
  onRangeChange: (range: "D" | "W" | "M" | "Y") => void;
}) {
  const [metric, setMetric] = useState<Metric>("revenue");
  const points = traction?.points || [];
  const values = seriesForMetric(points, metric);
  const totals = traction?.totals || { revenue_cents: 0, users: 0, usage_events: 0, pageviews: 0, visits: 0 };
  const previous = traction?.previous_totals || { revenue_cents: 0, users: 0, usage_events: 0, pageviews: 0, visits: 0 };
  const currentValue = totalForMetric(totals, metric);
  const previousValue = totalForMetric(previous, metric);
  const delta = pctDelta(currentValue, previousValue);
  const up = currentValue >= previousValue;
  const prefix = METRICS.find((item) => item.key === metric)?.prefix || "";

  return (
    <section className="lb-card lb-trac">
      <div className="lb-trac__top">
        <div className="lb-seg lb-trac__metrics">
          {METRICS.map((item) => (
            <button key={item.key} className={metric === item.key ? "is-on" : ""} type="button" onClick={() => setMetric(item.key)}>
              {item.label}
            </button>
          ))}
        </div>
        <span className="lb-trac__now"><i />{points.length ? `${points.length} points` : "No history yet"}</span>
      </div>
      <div className="lb-trac__big">{formatMetric(prefix, currentValue)}</div>
      <div className={`lb-trac__chg ${up ? "is-up" : "is-down"}`}>
        {up ? "▲" : "▼"} {Math.abs(delta).toFixed(1)}% <span>· {RANGE_LABEL[range]}</span>
      </div>
      <Chart values={values} up={up} />
      <div className="lb-seg lb-trac__ranges">
        {(["D", "W", "M", "Y"] as const).map((item) => (
          <button key={item} className={range === item ? "is-on" : ""} type="button" onClick={() => onRangeChange(item)}>
            {item}
          </button>
        ))}
      </div>
    </section>
  );
}

// Canonical task status set — operator-approved pill labels (GOAL_RULES §5/§7,
// locked 2026-06-17), mirroring the backend canonical_task in
// tui_gateway/server.py. The backend already emits a spec-compliant `status`
// and `status_label`; this normaliser is only a defensive fallback for older
// payloads so the pill never shows a raw runtime status like "recorded".
//   queued -> PLANNED, running -> RUNNING, blocked -> BLOCKED,
//   needs_review -> NEEDS REVIEW, completed -> DONE, failed -> FAILED.
const TASK_STATUS_LABELS: Record<string, string> = {
  queued: "PLANNED",
  running: "RUNNING",
  blocked: "BLOCKED",
  needs_review: "NEEDS REVIEW",
  completed: "DONE",
  failed: "FAILED",
  idle: "Idle",
};

function normalizeTaskStatus(value: string): keyof typeof TASK_STATUS_LABELS {
  const status = value.toLowerCase();
  if (status.includes("review") || status.includes("approval") || status.includes("awaiting") || status === "needs_review") return "needs_review";
  if (status.includes("block") || status.includes("stuck") || status.includes("paused")) return "blocked";
  if (status.includes("fail") || status.includes("error")) return "failed";
  if (status.includes("running") || status.includes("working") || status.includes("live") || status.includes("active")) return "running";
  if (status.includes("queue") || status.includes("planned") || status.includes("pending") || status.includes("schedul") || status.includes("wait")) return "queued";
  if (status.includes("done") || status.includes("complete") || status.includes("success")) return "completed";
  return "idle";
}

// Operator-approved category taxonomy (GOAL_RULES §5/§7, locked 2026-06-17):
// one pill per task, drawn from RESEARCH / PRODUCT / LAUNCH / GROWTH / OPS.
const TASK_CATEGORY_LABELS: Record<string, string> = {
  RESEARCH: "Research",
  PRODUCT: "Product",
  LAUNCH: "Launch",
  GROWTH: "Growth",
  OPS: "Ops",
};

function taskCategory(value: string): string {
  const cat = value.trim().toUpperCase();
  return TASK_CATEGORY_LABELS[cat] ? cat : "PRODUCT";
}

function TaskDetail({ task }: { task: Record<string, unknown> }) {
  const description = asText(task.description) || asText(task.detail);
  const outputs = (Array.isArray(task.outputs) ? task.outputs : []).map((o) => asText(o)).filter(Boolean);
  const steps = asList(task.steps);
  return (
    <div className="lb-task__detail">
      {description && (
        <div className="lb-task__goal">
          <span className="lb-task__goal-h">Goal</span>
          <span className="lb-task__goal-t">{description}</span>
        </div>
      )}
      {outputs.length > 0 && (
        <div className="lb-task__outs">
          <span className="lb-task__goal-h">Outputs</span>
          <ul className="lb-task__outlist">
            {outputs.map((out, i) => <li key={i}>{out}</li>)}
          </ul>
        </div>
      )}
      {steps.length > 0 && (
        <ol className="lb-task__steps">
          {steps.map((step, i) => {
            const done = normalizeTaskStatus(asText(step.status)) === "completed";
            return (
              <li key={asText(step.id) || i} className={done ? "is-done" : ""}>
                <span className="lb-task__step-mark" aria-hidden="true">{done ? "✓" : "○"}</span>
                <span className="lb-task__step-t">{asText(step.label) || asText(step.title) || `Step ${i + 1}`}</span>
              </li>
            );
          })}
        </ol>
      )}
      {/* Raw tool-call name only shown here, in the expanded detail (spec #4). */}
      {asText(task.label) && asText(task.label) !== asText(task.title) && (
        <div className="lb-task__raw">raw: {asText(task.label)}</div>
      )}
    </div>
  );
}

function Tasks({ tasks }: { tasks: Array<Record<string, unknown>> }) {
  // Only intent-level tasks are top-level rows; raw tool calls carry a task_id
  // pointing at a parent (spec #6), so nested events are not listed flat here.
  const intentTasks = useMemo(
    () => tasks.filter((task) => {
      const id = asText(task.id);
      const parent = asText(task.task_id);
      return !parent || parent === id;
    }),
    [tasks],
  );
  const ordered = useMemo(
    () => [
      ...intentTasks.filter((task) => normalizeTaskStatus(asText(task.status)) !== "completed"),
      ...intentTasks.filter((task) => normalizeTaskStatus(asText(task.status)) === "completed"),
    ],
    [intentTasks],
  );
  const [expanded, setExpanded] = useState<string | null>(null);
  const running = ordered.filter((task) => normalizeTaskStatus(asText(task.status)) === "running").length;
  const queued = ordered.filter((task) => normalizeTaskStatus(asText(task.status)) === "queued").length;
  // FAILED / BLOCKED / NEEDS REVIEW all count as items needing operator attention.
  const issues = ordered.filter((task) => {
    const s = normalizeTaskStatus(asText(task.status));
    return s === "failed" || s === "blocked" || s === "needs_review";
  }).length;
  return (
    <section className="lb-card lb-act">
      <div className="lb-h"><span className="lb-act__pulse" />Tasks<span className="lb-h__c">{running} running · {queued} queued{issues ? ` · ${issues} issue${issues === 1 ? "" : "s"}` : ""}</span></div>
      <div className="lb-act__list">
        {ordered.map((task, index) => {
          const state = normalizeTaskStatus(asText(task.status));
          const id = asText(task.id) || String(index);
          const category = taskCategory(asText(task.category));
          const statusLabel = asText(task.status_label) || TASK_STATUS_LABELS[state] || state;
          const title = asText(task.title) || asText(task.label) || "Recorded work";
          const description = asText(task.description) || asText(task.detail) || "Tracked in the workspace overview.";
          const isOpen = expanded === id;
          return (
            <div key={id} className={`lb-act__task lb-task is-${state} ${isOpen ? "is-open" : ""}`}>
              <button
                type="button"
                className="lb-task__head"
                aria-expanded={isOpen}
                onClick={() => setExpanded(isOpen ? null : id)}
              >
                <span className="lb-act__dot" aria-hidden="true" />
                <span className="lb-act__main">
                  <span className="lb-act__row">
                    <span className="lb-act__name">{title}</span>
                    <span className="lb-task__pills">
                      <span className={`lb-task__pill lb-task__pill--cat is-${category.toLowerCase()}`}>{TASK_CATEGORY_LABELS[category]}</span>
                      <span className={`lb-task__pill lb-task__pill--status is-${state}`}>{statusLabel}</span>
                    </span>
                  </span>
                  <span className="lb-act__ev"><span className="lb-act__evtxt">{description}</span></span>
                </span>
              </button>
              {isOpen && <TaskDetail task={task} />}
            </div>
          );
        })}
        {!ordered.length && <div className="lb-empty">No live tasks yet.</div>}
      </div>
    </section>
  );
}

function ChannelBudget({
  workspace,
  creativeCredits,
  onSaveChannelCreditBudgets,
  onBuyCreativeCredits,
}: {
  workspace: TakyonBusinessWorkspaceResponse | null;
  creativeCredits: TakyonBusinessCreativeCreditsResponse | null;
  onSaveChannelCreditBudgets: (
    slug: string,
    allocations: Record<ChannelBudgetKey, number>,
  ) => Promise<TakyonBusinessCreativeCreditsResponse | null>;
  onBuyCreativeCredits: (slug: string, credits: number) => Promise<void>;
}) {
  const overview = asRecord(workspace?.overview);
  const outreach = asRecord(asRecord(asRecord(overview.artifacts).outreach).channels);
  const xChannel = asRecord(outreach.x);
  const metaChannel = asRecord(outreach.meta);
  const redditChannel = asRecord(outreach.reddit);
  const xBudget = channelBudgetSnapshot(creativeCredits, "x");
  const metaBudget = channelBudgetSnapshot(creativeCredits, "meta");
  const redditBudget = channelBudgetSnapshot(creativeCredits, "reddit");
  const savedAllocations = useMemo<Record<ChannelBudgetKey, number>>(() => ({
    x: pickFirstInt(xBudget.allocated_credits, channelAllocatedCredits(xChannel)) || 0,
    meta: pickFirstInt(metaBudget.allocated_credits, channelAllocatedCredits(metaChannel)) || 0,
    reddit: pickFirstInt(redditBudget.allocated_credits, channelAllocatedCredits(redditChannel)) || 0,
  }), [
    metaBudget.allocated_credits,
    metaChannel,
    redditBudget.allocated_credits,
    redditChannel,
    xBudget.allocated_credits,
    xChannel,
  ]);
  const rowFloors = useMemo<Record<ChannelBudgetKey, number>>(() => ({
    x: (readInt(xBudget.used_credits) || 0) + (readInt(xBudget.reserved_credits) || 0),
    meta: (readInt(metaBudget.used_credits) || 0) + (readInt(metaBudget.reserved_credits) || 0),
    reddit: (readInt(redditBudget.used_credits) || 0) + (readInt(redditBudget.reserved_credits) || 0),
  }), [
    metaBudget.reserved_credits,
    metaBudget.used_credits,
    redditBudget.reserved_credits,
    redditBudget.used_credits,
    xBudget.reserved_credits,
    xBudget.used_credits,
  ]);
  const businessSlug = asText(workspace?.business_slug || creativeCredits?.business_slug);
  // Distinguish "still loading / not fetched yet" (creativeCredits === null) from
  // a confirmed-unavailable response (available === false). During a normal warm
  // reload the value is briefly null while the background revalidation runs — we
  // must show a neutral loading line, NOT the alarming "Creative credits are
  // unavailable right now" copy, which previously flashed on every load.
  const creditsConfirmedUnavailable = creativeCredits !== null && !creativeCredits.available;
  const [draftAllocations, setDraftAllocations] = useState<Record<ChannelBudgetKey, number>>(savedAllocations);
  const [saving, setSaving] = useState(false);
  const [buying, setBuying] = useState(false);
  const [saveError, setSaveError] = useState("");
  const [buyError, setBuyError] = useState("");
  const actionCosts = asRecord(creativeCredits?.action_costs);
  const checkoutPriceCents = readInt(creativeCredits?.price_cents_per_credit);
  const minimumCheckoutCreditsValue = readInt(creativeCredits?.minimum_checkout_credits);
  const minimumCheckoutAmountCents = readInt(creativeCredits?.minimum_checkout_amount_cents);
  const ugcActionCost = readInt(asRecord(actionCosts.ugc_ad_generate).credits);
  const staticAdActionCost = readInt(asRecord(actionCosts.static_ad_generate).credits);
  const xPublishActionCost = readInt(asRecord(actionCosts.x_publish_outreach).credits);
  const metaLaunchActionCost = readInt(asRecord(actionCosts.meta_ad_launch).credits);
  const redditLaunchActionCost = readInt(asRecord(actionCosts.reddit_ad_launch).credits);
  const actionCostHint = [
    ugcActionCost ? `UGC ${ugcActionCost} on the chosen channel` : "",
    staticAdActionCost ? `static ads ${staticAdActionCost}` : "",
    xPublishActionCost ? `X posts ${xPublishActionCost}` : "",
    metaLaunchActionCost ? `Meta launch ${metaLaunchActionCost}` : "",
    redditLaunchActionCost ? `Reddit launch ${redditLaunchActionCost}` : "",
  ].filter(Boolean).join(" · ");
  const defaultBuyCredits = Math.max(100, minimumCheckoutCreditsValue || 1);
  const [buyCreditsInput, setBuyCreditsInput] = useState(String(defaultBuyCredits));
  const syncedBusinessRef = useRef(businessSlug);
  const hasChanges = CHANNEL_BUDGET_KEYS.some((key) => draftAllocations[key] !== savedAllocations[key]);
  const buyCreditsValue = readInt(buyCreditsInput) || 0;
  const hasMinimumCheckout = minimumCheckoutCreditsValue !== null;
  const canCheckoutChosenAmount = buyCreditsValue > 0 && (!hasMinimumCheckout || buyCreditsValue >= minimumCheckoutCreditsValue);
  const estimatedCheckoutAmountCents = checkoutPriceCents !== null ? buyCreditsValue * checkoutPriceCents : null;

  useEffect(() => {
    if (syncedBusinessRef.current !== businessSlug) {
      syncedBusinessRef.current = businessSlug;
      setDraftAllocations(savedAllocations);
      setBuyCreditsInput(String(defaultBuyCredits));
      setSaveError("");
      setBuyError("");
      return;
    }
    if (!saving && !hasChanges) {
      setDraftAllocations(savedAllocations);
      setSaveError("");
    }
  }, [businessSlug, defaultBuyCredits, hasChanges, savedAllocations, saving]);

  const budgetCapacity = Math.max(
    readInt(creativeCredits?.budget_capacity_credits) || 0,
    ...CHANNEL_BUDGET_KEYS.map((key) => rowFloors[key]),
    ...CHANNEL_BUDGET_KEYS.map((key) => draftAllocations[key]),
  );
  const spendableCredits = readInt(creativeCredits?.balance_credits) || 0;
  const totalAllocated = CHANNEL_BUDGET_KEYS.reduce((sum, key) => sum + draftAllocations[key], 0);
  const unallocatedCredits = Math.max(0, budgetCapacity - totalAllocated);
  const sliderMax = Math.max(1, budgetCapacity, totalAllocated);
  const canEdit = Boolean(creativeCredits?.available && businessSlug);
  const canBuy = Boolean(businessSlug);
  const needsCredits = canBuy && budgetCapacity <= 0 && spendableCredits <= 0;
  const feedbackError = buyError || saveError;
  const rows = [
    {
      key: "x",
      label: "X",
      color: "#1d9bf0",
      value: draftAllocations.x,
      used: readInt(xBudget.used_credits) || 0,
      reserved: readInt(xBudget.reserved_credits) || 0,
      stat: channelStatLine("x", xChannel),
    },
    {
      key: "meta",
      label: "Meta ads",
      color: "#1d6ff0",
      value: draftAllocations.meta,
      used: readInt(metaBudget.used_credits) || 0,
      reserved: readInt(metaBudget.reserved_credits) || 0,
      stat: channelStatLine("meta", metaChannel),
    },
    {
      key: "reddit",
      label: "Reddit ads",
      color: "#fb8024",
      value: draftAllocations.reddit,
      used: readInt(redditBudget.used_credits) || 0,
      reserved: readInt(redditBudget.reserved_credits) || 0,
      stat: channelStatLine("reddit", redditChannel),
    },
  ] as const;

  const setAllocation = useCallback((key: ChannelBudgetKey, nextRaw: number) => {
    setDraftAllocations((current) => {
      const minimum = rowFloors[key];
      const otherTotal = CHANNEL_BUDGET_KEYS.reduce((sum, bucket) => (
        bucket === key ? sum : sum + current[bucket]
      ), 0);
      const maxForBucket = Math.max(minimum, budgetCapacity - otherTotal);
      return {
        ...current,
        [key]: Math.max(minimum, Math.min(Math.round(nextRaw), maxForBucket)),
      };
    });
    setSaveError("");
    setBuyError("");
  }, [budgetCapacity, rowFloors]);

  const saveBudgets = useCallback(async () => {
    if (!canEdit || !businessSlug || saving || !hasChanges) return;
    setSaving(true);
    setSaveError("");
    setBuyError("");
    try {
      await onSaveChannelCreditBudgets(businessSlug, draftAllocations);
    } catch (error) {
      setSaveError(error instanceof Error ? error.message : "Failed to save channel budgets.");
    } finally {
      setSaving(false);
    }
  }, [businessSlug, canEdit, draftAllocations, hasChanges, onSaveChannelCreditBudgets, saving]);

  const buyCredits = useCallback(async () => {
    if (!businessSlug || buying) return;
    if (buyCreditsValue <= 0) {
      setBuyError("Enter the number of credits you want to buy.");
      return;
    }
    if (minimumCheckoutCreditsValue !== null && buyCreditsValue < minimumCheckoutCreditsValue) {
      const minimumText = minimumCheckoutAmountCents !== null
        ? `${minimumCheckoutCreditsValue.toLocaleString()} credits (${formatUsdCents(minimumCheckoutAmountCents)})`
        : `${minimumCheckoutCreditsValue.toLocaleString()} credits`;
      setBuyError(`Minimum checkout is ${minimumText}.`);
      return;
    }
    setBuying(true);
    setBuyError("");
    setSaveError("");
    try {
      await onBuyCreativeCredits(businessSlug, buyCreditsValue);
    } catch (error) {
      setBuyError(error instanceof Error ? error.message : "Failed to start creative credit checkout.");
    } finally {
      setBuying(false);
    }
  }, [
    businessSlug,
    buyCreditsValue,
    buying,
    minimumCheckoutAmountCents,
    minimumCheckoutCreditsValue,
    onBuyCreativeCredits,
  ]);

  return (
    <section className="lb-card lb-bud">
      <div className="lb-h">Channel budget<span className="lb-h__c">{totalAllocated.toLocaleString()} credits allocated · {unallocatedCredits.toLocaleString()} unassigned</span></div>
      <div className="lb-bud__bar" aria-hidden="true">
        {rows.map((row) => (
          <span
            key={row.key}
            style={{
              width: `${totalAllocated > 0 ? (row.value / totalAllocated) * 100 : 0}%`,
              background: row.color,
            }}
          />
        ))}
      </div>
      <div className="lb-bud__list">
        {rows.map((row) => {
          const progress = sliderMax > 0 ? (row.value / sliderMax) * 100 : 0;
          const remaining = Math.max(0, row.value - row.used - row.reserved);
          const detail = [row.stat, `${row.used} used`, row.reserved ? `${row.reserved} reserved` : "", `${remaining} free`]
            .filter(Boolean)
            .join(" · ");
          return (
            <div key={row.key} className="lb-alloc">
              <div className="lb-alloc__top">
                <span className="lb-alloc__dot" style={{ background: row.color }} />
                <span className="lb-alloc__name">{row.label}</span>
                <span className="lb-alloc__val">{row.value.toLocaleString()}<i> credits</i></span>
              </div>
              <input
                type="range"
                min={rowFloors[row.key]}
                max={sliderMax}
                step={1}
                value={row.value}
                disabled={!canEdit || saving || budgetCapacity <= 0}
                aria-label={`${row.label} credits allocated`}
                onChange={(event) => setAllocation(row.key, Number(event.target.value || 0))}
                style={{
                  ["--p" as string]: `${progress}%`,
                  ["--fillc" as string]: row.color,
                  ["--thumbc" as string]: row.color,
                }}
              />
              <div className="lb-alloc__stat">{detail}</div>
            </div>
          );
        })}
      </div>
      <div className="lb-bud__foot">
        <div className="lb-bud__note">
          {creativeCredits?.available ? (
            `${spendableCredits.toLocaleString()} spendable now · ${budgetCapacity.toLocaleString()} total credits in budget scope${needsCredits ? " · Buy credits to unlock allocation." : ""}`
          ) : creditsConfirmedUnavailable ? (
            "Creative credits are unavailable right now, so channel budgets cannot be edited."
          ) : (
            <span className="lb-skel lb-skel--text" style={{ minWidth: 220 }} aria-label="Loading credit balances">
              Loading credit balances…
            </span>
          )}
          {creativeCredits?.available && buyCreditsValue > 0 && estimatedCheckoutAmountCents !== null
            ? <span className="lb-bud__helper">Checkout: {buyCreditsValue.toLocaleString()} credits for {formatUsdCents(estimatedCheckoutAmountCents)}.</span>
            : null}
          {creativeCredits?.available && minimumCheckoutCreditsValue !== null
            ? (
                <span className="lb-bud__helper">
                  Minimum: {minimumCheckoutCreditsValue.toLocaleString()} credits
                  {minimumCheckoutAmountCents !== null ? ` (${formatUsdCents(minimumCheckoutAmountCents)})` : ""}.
                </span>
              )
            : null}
          {creativeCredits?.available && actionCostHint
            ? (
                <span className="lb-bud__helper">
                  UGC and creative generation use these same credits. Costs: {actionCostHint}.
                </span>
              )
            : null}
          {feedbackError ? <span className="lb-bud__error">{feedbackError}</span> : null}
        </div>
        <div className="lb-bud__actions">
          <label className="lb-bud__buyfield">
            <span>Credits</span>
            <input
              type="number"
              inputMode="numeric"
              min={minimumCheckoutCreditsValue || 1}
              step={1}
              value={buyCreditsInput}
              disabled={!canBuy || buying}
              aria-label="Creative credits to buy"
              onChange={(event) => {
                const next = String(event.target.value || "").replace(/[^\d]/g, "");
                setBuyCreditsInput(next);
                setBuyError("");
              }}
            />
          </label>
          <button
            type="button"
            className="lb-bud__buy"
            disabled={!canBuy || buying || !canCheckoutChosenAmount}
            onClick={() => { void buyCredits(); }}
          >
            {buying
              ? "Opening Stripe..."
              : buyCreditsValue > 0
                ? `Buy ${buyCreditsValue.toLocaleString()} credits`
                : "Buy credits"}
          </button>
          <button
            type="button"
            className="lb-bud__save"
            disabled={!canEdit || saving || !hasChanges}
            onClick={() => { void saveBudgets(); }}
          >
            {saving ? "Saving..." : hasChanges ? "Save budgets" : "Saved"}
          </button>
        </div>
      </div>
    </section>
  );
}

function Documents({
  business,
  deliverables,
}: {
  business: LitebulbBusiness;
  deliverables: Array<Record<string, unknown>>;
}) {
  const [preview, setPreview] = useState<DocumentPreviewState | null>(null);
  const fileCacheRef = useRef<Map<string, TakyonBusinessFileReadResponse>>(new Map());
  const pendingReadsRef = useRef<Map<string, Promise<TakyonBusinessFileReadResponse>>>(new Map());
  const operatorVisibleDeliverables = useMemo(
    () =>
      deliverables.filter((output) => {
        const suffix = outputSuffix(asText(output.path));
        // Hidden source modules and generated media (now owned by the dedicated
        // Media panel) are excluded so Documents stays a clean list of readable
        // text deliverables and media is not duplicated across two panels.
        return !HIDDEN_DOCUMENT_SUFFIXES.has(suffix) && !MEDIA_OUTPUT_SUFFIXES.has(suffix);
      }),
    [deliverables],
  );
  const visible = operatorVisibleDeliverables.slice(0, 6);

  const loadDocument = useCallback((path: string) => {
    const cached = fileCacheRef.current.get(path);
    if (cached) return Promise.resolve(cached);
    const pending = pendingReadsRef.current.get(path);
    if (pending) return pending;
    const request = api.getTakyonBusinessFile(business.slug, path)
      .then((file) => {
        fileCacheRef.current.set(path, file);
        return file;
      })
      .finally(() => {
        pendingReadsRef.current.delete(path);
      });
    pendingReadsRef.current.set(path, request);
    return request;
  }, [business.slug]);

  const openDocument = useCallback(async (output: Record<string, unknown>) => {
    const path = asText(output.path);
    if (!path) return;
    const suffix = outputSuffix(path);
    if (MEDIA_OUTPUT_SUFFIXES.has(suffix)) {
      window.open(buildAssetUrl(business.slug, path), "_blank", "noopener,noreferrer");
      return;
    }
    const cached = fileCacheRef.current.get(path);
    if (cached) {
      setPreview({ output, file: cached, loading: false, error: "" });
      return;
    }
    const inlineFile = inlinePreviewFile(business.slug, output);
    if (inlineFile) {
      const shouldHydrateFullFile = inlineFile.truncated || Number(output.preview_size || 0) > String(inlineFile.content || "").length;
      setPreview({ output, file: inlineFile, loading: shouldHydrateFullFile, error: "" });
      if (!shouldHydrateFullFile) return;
      try {
        const file = await loadDocument(path);
        setPreview((current) => {
          if (!current || asText(current.output.path) !== path) return current;
          return { output, file, loading: false, error: "" };
        });
      } catch (error) {
        setPreview((current) => {
          if (!current || asText(current.output.path) !== path) return current;
          return {
            output,
            file: current.file,
            loading: false,
            error: error instanceof Error ? error.message : "Failed to load file preview.",
          };
        });
      }
      return;
    }
    setPreview({ output, file: null, loading: true, error: "" });
    try {
      const file = await loadDocument(path);
      setPreview({ output, file, loading: false, error: "" });
    } catch (error) {
      setPreview({
        output,
        file: null,
        loading: false,
        error: error instanceof Error ? error.message : "Failed to load file preview.",
      });
    }
  }, [business.slug, loadDocument]);

  useEffect(() => {
    const candidates = visible
      .map((output) => asText(output.path))
      .filter((path) => path && !MEDIA_OUTPUT_SUFFIXES.has(outputSuffix(path)));
    if (!candidates.length) return;
    const timer = window.setTimeout(() => {
      candidates.forEach((path) => {
        if (fileCacheRef.current.has(path) || pendingReadsRef.current.has(path)) return;
        void loadDocument(path).catch(() => {});
      });
    }, 150);
    return () => window.clearTimeout(timer);
  }, [loadDocument, visible]);

  return (
    <>
      <section className="lb-card lb-docs">
        <div className="lb-h">Documents<span className="lb-h__c">{operatorVisibleDeliverables.length} generated</span></div>
        <div className="lb-docs__grid">
          {visible.map((output, index) => (
            <button
              key={asText(output.id) || index}
              type="button"
              className="lb-docrow"
              onClick={() => void openDocument(output)}
            >
              <span className="lb-docrow__ic">{I.doc}</span>
              <span className="lb-docrow__main">
                <span className="lb-docrow__name">{asText(output.title) || asText(output.path) || "Output"}</span>
                <span className="lb-docrow__meta">{asText(output.detail) || asText(output.kind) || "Business artifact"}</span>
              </span>
              <span className="lb-docrow__open" aria-hidden="true">{I.ext}</span>
            </button>
          ))}
          {!visible.length && <div className="lb-empty">No deliverables yet.</div>}
        </div>
      </section>

      {preview && (
        <Modal
          title={asText(preview.output.title) || asText(preview.output.path) || "Document"}
          sub={asText(preview.output.path)}
          wide
          onClose={() => setPreview(null)}
        >
          <div className="lb-docview">
            {!preview.file && preview.loading && <div className="lb-empty">Loading file preview…</div>}
            {!preview.file && !preview.loading && preview.error && <div className="lb-docview__error">{preview.error}</div>}
            {preview.file && (
              <>
                <pre className={`lb-docview__code${TEXT_OUTPUT_SUFFIXES.has(outputSuffix(asText(preview.file?.path || preview.output.path))) ? "" : " is-plain"}`}>
                  {asText(preview.file?.content) || "File is empty."}
                </pre>
                {preview.loading && <div className="lb-docview__note">Loading the rest of the file…</div>}
                {!preview.loading && preview.error && <div className="lb-docview__note">{preview.error}</div>}
                {preview.file?.truncated && (
                  <div className="lb-docview__note">Preview truncated to the first portion of the file.</div>
                )}
              </>
            )}
          </div>
        </Modal>
      )}
    </>
  );
}

const MEDIA_ROLE_LABEL: Record<string, string> = {
  video: "UGC video",
  ad: "Ad creative",
  logo: "Logo",
  image: "Image",
  site: "Site asset",
};

function mediaRoleLabel(role: string, kind: string) {
  const normalized = role.trim().toLowerCase();
  if (MEDIA_ROLE_LABEL[normalized]) return MEDIA_ROLE_LABEL[normalized];
  return kind === "video" ? "Video" : "Image";
}

function MediaThumb({
  item,
  slug,
  onOpen,
}: {
  item: Record<string, unknown>;
  slug: string;
  onOpen: () => void;
}) {
  const path = asText(item.path);
  const kind = asText(item.kind) || (MEDIA_OUTPUT_SUFFIXES.has(outputSuffix(path)) ? "image" : "");
  const isVideo = kind === "video" || VIDEO_OUTPUT_SUFFIXES.has(outputSuffix(path));
  const role = asText(item.role);
  const title = asText(item.title) || path.split("/").pop() || "Media asset";
  const detail = asText(item.detail) || mediaRoleLabel(role, isVideo ? "video" : "image");
  const src = buildAssetUrl(slug, path);
  return (
    <button type="button" className="lb-media__item" onClick={onOpen} title={title}>
      <span className={`lb-media__thumb${isVideo ? " is-video" : ""}`}>
        {isVideo ? (
          // Video must NOT load eagerly — only the lightweight play affordance
          // shows in the grid; the actual stream loads on click in the modal.
          <span className="lb-media__playmark" aria-hidden="true">{I.play}</span>
        ) : (
          // Images load lazily so the grid never blocks the dashboard render.
          <img
            className="lb-media__img"
            src={src}
            alt={title}
            loading="lazy"
            decoding="async"
          />
        )}
        <span className="lb-media__badge">{isVideo ? I.film : I.image}</span>
      </span>
      <span className="lb-media__meta">
        <span className="lb-media__name">{title}</span>
        <span className="lb-media__role">{detail}</span>
      </span>
    </button>
  );
}

function Media({
  business,
  media,
}: {
  business: LitebulbBusiness;
  media: Array<Record<string, unknown>>;
}) {
  const [open, setOpen] = useState<Record<string, unknown> | null>(null);
  const items = useMemo(
    () => media.filter((item) => asText(item.path)).slice(0, 24),
    [media],
  );
  if (!items.length) return null;
  const openPath = asText(open?.path);
  const openIsVideo =
    asText(open?.kind) === "video" || VIDEO_OUTPUT_SUFFIXES.has(outputSuffix(openPath));
  const openTitle = asText(open?.title) || openPath.split("/").pop() || "Media";
  const openSrc = openPath ? buildAssetUrl(business.slug, openPath) : "";
  return (
    <>
      <section className="lb-card lb-media">
        <div className="lb-h">Media<span className="lb-h__c">{items.length} generated</span></div>
        <div className="lb-media__grid">
          {items.map((item, index) => (
            <MediaThumb
              key={asText(item.id) || asText(item.path) || index}
              item={item}
              slug={business.slug}
              onOpen={() => setOpen(item)}
            />
          ))}
        </div>
      </section>

      {open && openSrc && (
        <Modal
          title={openTitle}
          sub={openPath}
          wide
          onClose={() => setOpen(null)}
        >
          <div className="lb-media__view">
            {openIsVideo ? (
              // Loaded only now (on open), with preload metadata, so heavy video
              // never lags the dashboard during normal browsing.
              <video
                className="lb-media__player"
                src={openSrc}
                controls
                autoPlay
                preload="metadata"
                playsInline
              />
            ) : (
              <img className="lb-media__full" src={openSrc} alt={openTitle} />
            )}
            <a className="lb-media__open" href={openSrc} target="_blank" rel="noopener noreferrer">
              Open original {I.ext}
            </a>
          </div>
        </Modal>
      )}
    </>
  );
}

function TweetCard({
  businessName,
  source,
  text,
}: {
  businessName: string;
  source: string;
  text: string;
}) {
  return (
    <article className="lb-tweet">
      <div className="lb-tweet__head"><span className="lb-tweet__face">{businessName[0]?.toUpperCase() || "X"}</span><span className="lb-tweet__who"><b>{businessName}</b><span>{source}</span></span><span className="lb-tweet__logo">𝕏</span></div>
      <p className="lb-tweet__body">{text || "Recorded in Takyon."}</p>
      <div className="lb-tweet__foot"><span>{I.reply}</span><span>{I.rt}</span><span className="lb-tweet__like">{I.like}</span></div>
    </article>
  );
}

function VideoCard({ title, detail }: { title: string; detail: string }) {
  return (
    <figure className="lb-vid">
      <span className="lb-vid__thumb" style={{ background: "linear-gradient(150deg, hsl(190 70% 55%), hsl(230 65% 38%))" }}><span className="lb-vid__play">{I.play}</span></span>
      <figcaption>{title}<span className="lb-vid__stats">{detail}</span></figcaption>
    </figure>
  );
}

function AdCard({ title, detail }: { title: string; detail: string }) {
  return (
    <article className="lb-ad">
      <span className="lb-ad__img" style={{ background: "linear-gradient(135deg, hsl(24 85% 92%), hsl(54 70% 80%))" }}><span className="lb-ad__mega">{I.mega}</span></span>
      <div className="lb-ad__body"><div className="lb-ad__head">{title}</div><div className="lb-ad__stats">{detail}</div></div>
    </article>
  );
}

function MailRow({ title, detail }: { title: string; detail: string }) {
  return (
    <div className="lb-mailrow">
      <span className="lb-mailrow__face">{I.mail}</span>
      <span className="lb-mailrow__to">{title}</span>
      <span className="lb-mailrow__txt">{detail}</span>
      <span className="lb-mailrow__status">recorded</span>
    </div>
  );
}

function Distribution({ business, workspace }: { business: LitebulbBusiness; workspace: TakyonBusinessWorkspaceResponse | null }) {
  const [tab, setTab] = useState<DistTab>("x");
  const overview = asRecord(workspace?.overview);
  const posts = asList(overview.posts);
  const outputs = asList(workspace?.outputs);
  const outreachChannels = asRecord(asRecord(asRecord(overview.artifacts).outreach).channels);
  const xChannelItems = asList(asRecord(outreachChannels.x).items);
  const xItems = xChannelItems.length ? xChannelItems : posts.filter((item) => {
    const source = asText(item.source).toLowerCase();
    return source === "x" || source.startsWith("x-") || source.includes("twitter");
  });
  const videoItems = outputs.filter((item) => {
    const kind = asText(item.kind).toLowerCase();
    return kind === "video" || (kind === "image" && asText(item.detail).toLowerCase().includes("asset"));
  });
  const adItems = [
    ...asList(asRecord(outreachChannels.meta).campaigns),
    ...asList(asRecord(outreachChannels.reddit).campaigns),
  ];
  const emailItems = posts.filter((item) => {
    const source = asText(item.source).toLowerCase();
    return source.includes("email") || source.includes("outreach");
  });

  return (
    <section className="lb-card lb-dist">
      <div className="lb-h">
        Distribution<span className="lb-h__c">current channel outputs for {business.name}</span>
        <div className="lb-seg lb-dist__tabs">
          {(["x", "video", "ads", "email"] as DistTab[]).map((item) => (
            <button key={item} className={tab === item ? "is-on" : ""} type="button" onClick={() => setTab(item)}>
              {item === "x" ? "Posts" : item === "video" ? "Video" : item === "ads" ? "Ads" : "Email"}
            </button>
          ))}
        </div>
      </div>

      {tab === "x" && (
        <div className="lb-tweets">
          {xItems.slice(0, 4).map((item, index) => (
            <TweetCard
              key={asText(item.id) || index}
              businessName={business.name}
              source={asText(item.source) || "X"}
              text={asText(item.title) || asText(item.status) || "X post"}
            />
          ))}
          {!xItems.length && <div className="lb-empty">No X posts recorded yet.</div>}
        </div>
      )}

      {tab === "video" && (
        <div className="lb-vids">
          {videoItems.slice(0, 6).map((item, index) => <VideoCard key={asText(item.id) || index} title={asText(item.title) || asText(item.path) || "Creative asset"} detail={asText(item.detail) || "Generated media asset"} />)}
          {!videoItems.length && <div className="lb-empty">No video or creative assets recorded yet.</div>}
        </div>
      )}

      {tab === "ads" && (
        <div className="lb-ads">
          {adItems.slice(0, 4).map((item, index) => {
            const dailyBudget = Number(item.actual_daily_budget_usd || item.daily_budget_usd || 0);
            const metrics: string[] = [];
            if (Number.isFinite(dailyBudget) && dailyBudget > 0) metrics.push(`$${dailyBudget}/day`);
            const latestMetrics = asRecord(item.latest_metrics);
            if (latestMetrics.impressions) metrics.push(`${formatCount(Number(latestMetrics.impressions || 0))} impressions`);
            if (latestMetrics.clicks) metrics.push(`${formatCount(Number(latestMetrics.clicks || 0))} clicks`);
            return <AdCard key={asText(item.slug) || index} title={asText(item.campaign_name) || asText(item.slug) || "Campaign"} detail={metrics.join(" · ") || asText(item.status) || "Recorded campaign"} />;
          })}
          {!adItems.length && <div className="lb-empty">No ad campaigns recorded yet.</div>}
        </div>
      )}

      {tab === "email" && (
        <div className="lb-inbox">
          {emailItems.slice(0, 6).map((item, index) => <MailRow key={asText(item.id) || index} title={asText(item.title) || asText(item.source) || "Email thread"} detail={asText(item.status) || "Recorded in conversations"} />)}
          {!emailItems.length && <div className="lb-empty">No email outreach threads recorded yet.</div>}
        </div>
      )}
    </section>
  );
}

export function CompanyTab({
  business,
  workspace,
  creativeCredits,
  traction,
  tractionRange,
  onSaveChannelCreditBudgets,
  onBuyCreativeCredits,
  onTractionRangeChange,
}: {
  business: LitebulbBusiness;
  workspace: TakyonBusinessWorkspaceResponse | null;
  creativeCredits: TakyonBusinessCreativeCreditsResponse | null;
  traction: TakyonBusinessTractionResponse | null;
  tractionRange: "D" | "W" | "M" | "Y";
  onSaveChannelCreditBudgets: (
    slug: string,
    allocations: Record<ChannelBudgetKey, number>,
  ) => Promise<TakyonBusinessCreativeCreditsResponse | null>;
  onBuyCreativeCredits: (slug: string, credits: number) => Promise<void>;
  onTractionRangeChange: (range: "D" | "W" | "M" | "Y") => void;
}) {
  const liveState = useMemo(() => asRecord(workspace?.live_state), [workspace]);
  const tasks = useMemo(() => asList(liveState.tasks), [liveState]);
  const deliverables = useMemo(() => asList(workspace?.deliverables), [workspace]);
  const media = useMemo(() => asList(workspace?.media), [workspace]);

  return (
    <div className="lb-comp">
      <div className="lb-comp__inner">
        <Traction traction={traction} range={tractionRange} onRangeChange={onTractionRangeChange} />
        <div className="lb-comp__fold">
          <Tasks tasks={tasks} />
          <ChannelBudget
            workspace={workspace}
            creativeCredits={creativeCredits}
            onSaveChannelCreditBudgets={onSaveChannelCreditBudgets}
            onBuyCreativeCredits={onBuyCreativeCredits}
          />
        </div>
        <Distribution business={business} workspace={workspace} />
        <Media business={business} media={media} />
        <Documents business={business} deliverables={deliverables} />
      </div>
    </div>
  );
}
