import { useMemo, useState } from "react";
import type {
  TakyonBusinessTractionPoint,
  TakyonBusinessTractionResponse,
  TakyonBusinessWorkspaceResponse,
} from "@/lib/api";
import type { LitebulbBusiness } from "../takyon/useTakyonLitebulb";
import "./companytab.css";

const S = (d: string, w = 15) => (
  <svg width={w} height={w} viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"><path d={d} /></svg>
);

const I = {
  doc: S("M4 1.6h5l3 3V14.4H4zM9 1.6V4.6h3"),
  mail: S("M2 4h12v8H2zM2.5 4.5L8 8.5l5.5-4"),
  mega: S("M2.5 6.4v3.2l7.5 2.9V3.5zM10 5.4a2.6 2.6 0 010 5.2"),
  play: <svg width="15" height="15" viewBox="0 0 16 16" fill="currentColor"><path d="M5 3.4l8 4.6-8 4.6z" /></svg>,
  reply: S("M2.5 3.6h11v6.2H6.7L4 12.2V9.8H2.5z", 14),
  rt: S("M4.5 5L3 6.5 4.5 8M3 6.5h7.5a1.5 1.5 0 011.5 1.5v1M11.5 11l1.5-1.5L11.5 8M13 9.5H5.5A1.5 1.5 0 014 8V7", 14),
  like: <svg width="14" height="14" viewBox="0 0 16 16" fill="currentColor" aria-hidden="true"><path d="M8 13.7S2.6 10.3 2.6 6.5A2.6 2.6 0 018 4a2.6 2.6 0 015.4 2.5c0 3.8-5.4 7.2-5.4 7.2z" /></svg>,
};

type Metric = "revenue" | "users" | "usage";
type DistTab = "x" | "video" | "ads" | "email";

const METRICS: Array<{ key: Metric; label: string; prefix: string }> = [
  { key: "revenue", label: "Revenue", prefix: "$" },
  { key: "users", label: "Users", prefix: "" },
  { key: "usage", label: "Usage", prefix: "" },
];

const RANGE_LABEL: Record<"D" | "W" | "M" | "Y", string> = {
  D: "today",
  W: "this week",
  M: "this month",
  Y: "this year",
};

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

function pctDelta(current: number, previous: number) {
  if (!previous) return current ? 100 : 0;
  return ((current - previous) / previous) * 100;
}

function metricValue(point: TakyonBusinessTractionPoint, metric: Metric) {
  if (metric === "revenue") return Number(point.revenue_cents || 0);
  if (metric === "users") return Number(point.users || 0);
  return Number(point.usage_events || 0);
}

function seriesForMetric(points: TakyonBusinessTractionPoint[], metric: Metric) {
  return points.map((point) => metricValue(point, metric));
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
  const totals = traction?.totals || { revenue_cents: 0, users: 0, usage_events: 0 };
  const previous = traction?.previous_totals || { revenue_cents: 0, users: 0, usage_events: 0 };
  const currentValue = metric === "revenue" ? totals.revenue_cents : metric === "users" ? totals.users : totals.usage_events;
  const previousValue = metric === "revenue" ? previous.revenue_cents : metric === "users" ? previous.users : previous.usage_events;
  const delta = pctDelta(currentValue, previousValue);
  const up = currentValue >= previousValue;
  const prefix = METRICS.find((item) => item.key === metric)?.prefix || "";

  return (
    <section className="lb-card lb-trac">
      <div className="lb-trac__top">
        <div className="lb-seg lb-trac__metrics">
          {METRICS.map((item) => (
            <button key={item.key} className={metric === item.key ? "is-on" : ""} onClick={() => setMetric(item.key)}>
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
          <button key={item} className={range === item ? "is-on" : ""} onClick={() => onRangeChange(item)}>
            {item}
          </button>
        ))}
      </div>
    </section>
  );
}

function normalizeTaskStatus(value: string) {
  const status = value.toLowerCase();
  if (status.includes("running") || status.includes("working")) return "live";
  if (status.includes("done") || status.includes("complete") || status.includes("success")) return "done";
  return "queued";
}

function Activity({ tasks }: { tasks: Array<Record<string, unknown>> }) {
  const visible = tasks.slice(0, 6);
  const running = visible.filter((task) => normalizeTaskStatus(asText(task.status)) === "live").length;
  return (
    <section className="lb-card lb-act">
      <div className="lb-h"><span className="lb-act__pulse" />Activity<span className="lb-h__c">{running} running · {Math.max(0, visible.length - running)} queued</span></div>
      <div className="lb-act__list">
        {visible.map((task, index) => {
          const state = normalizeTaskStatus(asText(task.status));
          return (
            <div key={asText(task.id) || index} className={`lb-act__task is-${state}`}>
              <span className="lb-act__dot" aria-hidden="true" />
              <span className="lb-act__main">
                <span className="lb-act__row"><span className="lb-act__name">{asText(task.label) || "Recorded work"}</span><span className="lb-act__meta">{asText(task.status) || state}</span></span>
                <span className="lb-act__ev"><span className="lb-act__evtxt">{asText(task.detail) || "Tracked in the workspace overview."}</span></span>
              </span>
            </div>
          );
        })}
        {!visible.length && <div className="lb-empty">No live activity yet.</div>}
      </div>
    </section>
  );
}

function ChannelBudget({ workspace }: { workspace: TakyonBusinessWorkspaceResponse | null }) {
  const overview = asRecord(workspace?.overview);
  const outreach = asRecord(asRecord(asRecord(overview.artifacts).outreach).channels);
  const cronJobs = asList(overview.cron);
  const metaCampaigns = asList(asRecord(outreach.meta).campaigns);
  const redditCampaigns = asList(asRecord(outreach.reddit).campaigns);
  const rows = [
    {
      key: "x",
      label: "X",
      value: `${formatCount(Number(asRecord(outreach.x).published_count || 0))} posts`,
      note: asText(asRecord(outreach.x).status) || "idle",
      color: "#1d9bf0",
    },
    {
      key: "meta",
      label: "Meta ads",
      value: metaCampaigns[0] ? `$${Number(metaCampaigns[0].actual_daily_budget_usd || metaCampaigns[0].daily_budget_usd || 0)}/day` : "No campaigns",
      note: asText(metaCampaigns[0]?.status) || "idle",
      color: "#2563eb",
    },
    {
      key: "reddit",
      label: "Reddit ads",
      value: redditCampaigns[0] ? `$${Number(redditCampaigns[0].actual_daily_budget_usd || redditCampaigns[0].daily_budget_usd || 0)}/day` : "No campaigns",
      note: asText(redditCampaigns[0]?.status) || "idle",
      color: "#fb8024",
    },
  ];

  return (
    <section className="lb-card lb-bud">
      <div className="lb-h">Channels<span className="lb-h__c">read-only current allocation</span></div>
      <div className="lb-bud__list">
        {rows.map((row) => (
          <div key={row.key} className="lb-alloc">
            <div className="lb-alloc__top"><span className="lb-alloc__dot" style={{ background: row.color }} /><span className="lb-alloc__name">{row.label}</span><span className="lb-alloc__val">{row.value}</span></div>
            <div className="lb-alloc__stat">{row.note}</div>
          </div>
        ))}
      </div>
      <div className="lb-set__rule" />
      <div className="lb-h">Wake schedule</div>
      <div className="lb-bud__list">
        {cronJobs.slice(0, 3).map((job, index) => (
          <div key={asText(job.id) || index} className="lb-alloc">
            <div className="lb-alloc__top"><span className="lb-alloc__name">{asText(job.name) || "Scheduled wake"}</span><span className="lb-alloc__val">{asText(job.state) || "scheduled"}</span></div>
            <div className="lb-alloc__stat">{asText(job.schedule_display) || asText(asRecord(job.schedule).display) || "Scheduled CEO check"}</div>
          </div>
        ))}
        {!cronJobs.length && <div className="lb-empty">No wake schedule recorded yet.</div>}
      </div>
    </section>
  );
}

function Documents({ outputs }: { outputs: Array<Record<string, unknown>> }) {
  const visible = outputs.slice(0, 6);
  return (
    <section className="lb-card lb-docs">
      <div className="lb-h">Documents<span className="lb-h__c">{outputs.length} generated</span></div>
      <div className="lb-docs__grid">
        {visible.map((output, index) => (
          <div key={asText(output.id) || index} className="lb-docrow">
            <span className="lb-docrow__ic">{I.doc}</span>
            <span className="lb-docrow__main">
              <span className="lb-docrow__name">{asText(output.title) || asText(output.path) || "Output"}</span>
              <span className="lb-docrow__meta">{asText(output.detail) || asText(output.kind) || "Business artifact"}</span>
            </span>
          </div>
        ))}
        {!visible.length && <div className="lb-empty">No deliverables yet.</div>}
      </div>
    </section>
  );
}

function TweetCard({ title, source, status }: { title: string; source: string; status: string }) {
  return (
    <article className="lb-tweet">
      <div className="lb-tweet__head"><span className="lb-tweet__face">{title[0]?.toUpperCase() || "X"}</span><span className="lb-tweet__who"><b>{title}</b><span>{source}</span></span><span className="lb-tweet__logo">𝕏</span></div>
      <p className="lb-tweet__body">{status || "Recorded in Takyon."}</p>
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
  const xItems = [
    ...posts.filter((item) => {
      const source = asText(item.source).toLowerCase();
      return source === "x" || source.startsWith("x-") || source.includes("twitter");
    }),
    ...asList(asRecord(outreachChannels.x).items),
  ];
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
            <button key={item} className={tab === item ? "is-on" : ""} onClick={() => setTab(item)}>
              {item === "x" ? "Posts" : item === "video" ? "Video" : item === "ads" ? "Ads" : "Email"}
            </button>
          ))}
        </div>
      </div>

      {tab === "x" && (
        <div className="lb-tweets">
          {xItems.slice(0, 4).map((item, index) => <TweetCard key={asText(item.id) || index} title={asText(item.title) || "X post"} source={asText(item.source) || "@x"} status={asText(item.status)} />)}
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
  traction,
  tractionRange,
  onTractionRangeChange,
}: {
  business: LitebulbBusiness;
  workspace: TakyonBusinessWorkspaceResponse | null;
  traction: TakyonBusinessTractionResponse | null;
  tractionRange: "D" | "W" | "M" | "Y";
  onTractionRangeChange: (range: "D" | "W" | "M" | "Y") => void;
}) {
  const overview = useMemo(() => asRecord(workspace?.overview), [workspace]);
  const tasks = asList(overview.tasks);
  const outputs = asList(workspace?.outputs);

  return (
    <div className="lb-comp">
      <div className="lb-comp__inner">
        <Traction traction={traction} range={tractionRange} onRangeChange={onTractionRangeChange} />
        <div className="lb-comp__fold">
          <Activity tasks={tasks} />
          <ChannelBudget workspace={workspace} />
        </div>
        <Distribution business={business} workspace={workspace} />
        <Documents outputs={outputs} />
      </div>
    </div>
  );
}
