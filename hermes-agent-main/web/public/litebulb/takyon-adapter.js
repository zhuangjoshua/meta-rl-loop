(function () {
  function normalizeBasePath(raw) {
    if (!raw) return "";
    const lead = raw.startsWith("/") ? raw : `/${raw}`;
    return lead.replace(/\/+$/, "");
  }

  function ownerWindow() {
    try {
      if (window.parent && window.parent !== window) return window.parent;
    } catch (_err) {
      /* cross-window access is best effort */
    }
    return window;
  }

  const owner = ownerWindow();
  const ENV = {
    token: owner.__TAKYON_SESSION_TOKEN__ || window.__TAKYON_SESSION_TOKEN__ || "",
    basePath: normalizeBasePath(owner.__TAKYON_BASE_PATH__ || window.__TAKYON_BASE_PATH__ || ""),
  };
  if (!ENV.token) return;

  const SESSION_HEADER = "X-Takyon-Session-Token";
  const BOARD_ORDER = ["triage", "todo", "scheduled", "ready", "running", "blocked", "review", "done"];
  const WAKE_SCHEDULE_PRESETS = ["every 30m", "every 1h", "every 2h", "every 6h", "every 12h", "every 1d"];
  const WAKE_SCHEDULE_PATTERN = /^every\s+(\d+)\s*([mhd])$/i;
  const VIDEO_EXTENSIONS = new Set(["mp4", "mov", "webm", "m4v"]);
  const IMAGE_EXTENSIONS = new Set(["png", "jpg", "jpeg", "webp", "gif"]);
  const LIVE = {
    activeBusiness: "",
    sessionId: "",
    sessionBusiness: "",
    ws: null,
    reconnectTimer: null,
    pollTimer: null,
    menuTimer: null,
    toolEls: new Map(),
    assistantBubble: null,
    assistantText: "",
    assistantDeltaSeen: false,
    assistantTypingTimer: null,
    businesses: [],
    businessIndex: new Map(),
    operatorAccount: null,
    creativeCredits: null,
    bootedBusiness: "",
    planBusiness: "",
    refreshBusy: false,
    workspaceSnapshot: null,
    workspaceOutputs: [],
    liveTrace: new Map(),
    activeTurnTraceId: "",
    traceLogSeen: new Set(),
    lastOverviewTaskSignature: "",
    lastBackgroundDetail: "",
    lastCeoHeadline: "",
    pollMs: 0,
    historyPollTimer: null,
    historyPollMs: 0,
    historySeen: new Set(),
    historyRunning: false,
    refreshTimer: null,
  };

  function endpoint(path) {
    return `${ENV.basePath}${path}`;
  }

  function currentReturnPath() {
    try {
      const target = owner && owner.location ? owner.location : window.location;
      return `${target.pathname || "/"}${target.search || ""}${target.hash || ""}`;
    } catch (_err) {
      return `${window.location.pathname || "/"}${window.location.search || ""}${window.location.hash || ""}`;
    }
  }

  function navigateOwner(url) {
    const target = normalizeOpenableUrl(url);
    if (!target) throw new Error("No URL available.");
    try {
      const topWindow = owner && owner.location ? owner : window;
      topWindow.location.assign(target);
      return;
    } catch (_err) {
      window.location.assign(target);
    }
  }

  function sessionHeaders(extra) {
    const headers = new Headers(extra || {});
    if (ENV.token && !headers.has(SESSION_HEADER)) headers.set(SESSION_HEADER, ENV.token);
    return headers;
  }

  function wait(ms) {
    return new Promise((resolve) => window.setTimeout(resolve, ms));
  }

  async function fetchJSON(path, init) {
    const res = await fetch(endpoint(path), {
      ...init,
      credentials: "same-origin",
      headers: sessionHeaders(init && init.headers),
    });
    if (!res.ok) {
      const text = await res.text().catch(() => res.statusText);
      throw new Error(`${res.status}: ${text}`);
    }
    return res.json();
  }

  async function rpc(method, params, timeoutMs) {
    const controller = new AbortController();
    const timer = window.setTimeout(() => controller.abort(), timeoutMs || 120000);
    try {
      const res = await fetch(endpoint("/api/tui/rpc"), {
        method: "POST",
        credentials: "same-origin",
        signal: controller.signal,
        headers: sessionHeaders({ "Content-Type": "application/json" }),
        body: JSON.stringify({
          jsonrpc: "2.0",
          id: `litebulb-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
          method,
          params: params || {},
        }),
      });
      const payload = await res.json();
      if (payload && payload.error) {
        throw new Error(payload.error.message || `request failed: ${method}`);
      }
      return payload && payload.result;
    } finally {
      window.clearTimeout(timer);
    }
  }

  function wsUrl() {
    const scheme = window.location.protocol === "https:" ? "wss:" : "ws:";
    return `${scheme}//${window.location.host}${ENV.basePath}/api/ws?token=${encodeURIComponent(ENV.token)}`;
  }

  function currentBusinessParam() {
    const own = new URL(window.location.href).searchParams.get("business");
    if (own) return own;
    try {
      return new URL(owner.location.href).searchParams.get("business") || "";
    } catch (_err) {
      return "";
    }
  }

  function replaceSearchParam(targetWindow, slug) {
    try {
      const url = new URL(targetWindow.location.href);
      if (slug) url.searchParams.set("business", slug);
      else url.searchParams.delete("business");
      targetWindow.history.replaceState(targetWindow.history.state, "", url.toString());
    } catch (_err) {
      /* best effort */
    }
  }

  function syncBusinessParam(slug) {
    replaceSearchParam(window, slug);
    if (owner !== window) replaceSearchParam(owner, slug);
  }

  function businessSummary(slug) {
    return LIVE.businessIndex.get(String(slug || "").trim().toLowerCase()) || null;
  }

  function seedBusinessSnapshot(slug, summary) {
    const business = String(slug || "").trim().toLowerCase();
    if (!business) return null;
    return normalizeLiveSnapshot({
      business_slug: business,
      current: {
        name: String(summary && summary.name || business).trim(),
        goal: String(summary && summary.goal || "").trim(),
        mode: String(summary && summary.mode || "test").trim().toLowerCase() || "test",
      },
      overview: {},
      outputs: [],
      background_run: null,
    });
  }

  function isTransientConnectionMessage(message) {
    const text = String(message || "").trim();
    if (!text) return false;
    return /live stream (?:reconnecting|disconnected|unauthorized|forbidden)|websocket connection failed/i.test(text);
  }

  function isBusinessScopeDeniedMessage(message) {
    const text = String(message || "").trim();
    if (!text) return false;
    return /could not open business:|no businesses are visible for this account|that business is not available to this account|access denied for business:/i.test(text);
  }

  function isBusyError(err) {
    const message = err instanceof Error ? err.message : String(err);
    return /session busy|busy|4009/i.test(message);
  }

  function isMissingSessionError(err) {
    const message = err instanceof Error ? err.message : String(err);
    return /session not found|4001/i.test(message);
  }

  function historyMessageKey(role, text) {
    const cleanRole = String(role || "").trim();
    const cleanText = String(text || "").trim();
    return cleanRole && cleanText ? `${cleanRole}\n${cleanText}` : "";
  }

  function rememberHistoryMessage(role, text) {
    const key = historyMessageKey(role, text);
    if (!key) return;
    LIVE.historySeen.add(key);
  }

  function hasSeenHistoryMessage(role, text) {
    const key = historyMessageKey(role, text);
    return !!key && LIVE.historySeen.has(key);
  }

  function hasObjectKeys(value) {
    return !!(value && typeof value === "object" && !Array.isArray(value) && Object.keys(value).length);
  }

  function normalizeLiveSnapshot(value) {
    const business = String(value && (value.business_slug || value.business) || "").trim().toLowerCase();
    if (!business) return null;
    return {
      business_slug: business,
      current: hasObjectKeys(value && value.current) ? value.current : {},
      overview: hasObjectKeys(value && value.overview) ? value.overview : undefined,
      outputs: Array.isArray(value && value.outputs) ? value.outputs : [],
      background_run:
        value && value.background_run && typeof value.background_run === "object"
          ? value.background_run
          : null,
    };
  }

  function mergeLiveSnapshots(primary, fallback) {
    const preferred = normalizeLiveSnapshot(primary);
    const alternate = normalizeLiveSnapshot(fallback);
    if (!preferred) return alternate;
    if (!alternate) return preferred;
    return {
      business_slug: preferred.business_slug || alternate.business_slug,
      current: hasObjectKeys(preferred.current) ? preferred.current : alternate.current,
      overview: hasObjectKeys(preferred.overview) ? preferred.overview : alternate.overview,
      outputs: Array.isArray(preferred.outputs) && preferred.outputs.length ? preferred.outputs : alternate.outputs,
      background_run: preferred.background_run || alternate.background_run,
    };
  }

  function rememberBusinesses(items) {
    LIVE.businesses = Array.isArray(items) ? items.filter(Boolean) : [];
    LIVE.businessIndex = new Map(
      LIVE.businesses
        .filter((item) => item && item.slug)
        .map((item) => [String(item.slug).trim().toLowerCase(), item]),
    );
    renderLauncherBusinesses();
  }

  function dollarFromAccount() {
    const cents = Number((LIVE.operatorAccount && LIVE.operatorAccount.spendable_cents) || 0);
    return Number.isFinite(cents) ? Math.max(0, cents / 100) : 0;
  }

  function operatorSpendableCents(account) {
    if (!account || account.available === false) return null;
    const cents = Number(account.spendable_cents);
    return Number.isFinite(cents) ? Math.max(0, Math.round(cents)) : null;
  }

  function hasOperatorAccountBalance() {
    const account = LIVE.operatorAccount;
    if (!account || account.available === false) return false;
    return Number.isFinite(Number(account.spendable_cents));
  }

  function formatBudgetCents(value) {
    const cents = Number(value);
    if (!Number.isFinite(cents)) return "—";
    return `$${(Math.max(0, cents) / 100).toFixed(2)}`;
  }

  function microUsdToDollars(value) {
    const micro = Number(value);
    if (!Number.isFinite(micro)) return "—";
    return `$${(Math.max(0, micro) / 1_000_000).toFixed(2)}`;
  }

  function formatMetricCount(value) {
    const count = Number(value);
    if (!Number.isFinite(count)) return "0";
    const whole = Math.max(0, Math.trunc(count));
    if (whole >= 1_000_000) return `${(whole / 1_000_000).toFixed(1).replace(/\.0$/, "")}m`;
    if (whole >= 1_000) return `${(whole / 1_000).toFixed(1).replace(/\.0$/, "")}k`;
    return String(whole);
  }

  function renderWalletRail() {
    const walletEl = $("#mb-credits");
    if (!walletEl) return;
    walletEl.style.display = "";
    walletEl.textContent = hasOperatorAccountBalance()
      ? `wallet ${formatBudgetCents(LIVE.operatorAccount && LIVE.operatorAccount.spendable_cents)}`
      : "wallet n/a";
  }

  function renderTopRail() {
    renderWalletRail();
    const wakeEl = $("#mb-wake");
    if (!wakeEl) return;
    if (!RT.live) {
      wakeEl.style.display = "none";
      return;
    }
    wakeEl.style.display = "";
    wakeEl.textContent = RT.nextWakeAt
      ? `wake ${RT.paused ? "paused" : fmt(RT.nextWakeAt - Date.now())}`
      : `wake ${RT.paused ? "paused" : "n/a"}`;
  }

  function slugifyName(value) {
    const slug = String(value || "")
      .trim()
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, "-")
      .replace(/^-+|-+$/g, "");
    return slug || "business";
  }

  function selectWakeCron(overview) {
    const cronJobs = Array.isArray(overview && overview.cron) ? overview.cron : [];
    if (!cronJobs.length) return null;
    const canonical = cronJobs.find((job) => String(job && job.name || "").startsWith("takyon-ceo:"));
    if (canonical) return canonical;
    const fuzzy = cronJobs.find((job) => /(?:^|[-_\s])(?:ceo|wake)(?:$|[-_\s])/i.test(String(job && job.name || "")));
    return fuzzy || cronJobs[0] || null;
  }

  function normalizeWakeSchedule(value) {
    const trimmed = String(value || "").trim().toLowerCase().replace(/\s+/g, " ");
    const match = trimmed.match(WAKE_SCHEDULE_PATTERN);
    if (!match) return null;
    const amount = Number.parseInt(match[1] || "", 10);
    const unit = (match[2] || "").toLowerCase();
    if (!Number.isFinite(amount) || amount < 1) return null;
    return `every ${amount}${unit}`;
  }

  function liveStatusLabel(task) {
    const raw = String(task.status || "").trim().toLowerCase();
    return BOARD_ORDER.includes(raw) ? raw : "todo";
  }

  function livePriority(value) {
    const n = Number(value);
    if (!Number.isFinite(n)) return "p2";
    if (n >= 3) return "p1";
    if (n >= 2) return "p2";
    return "p3";
  }

  function laneFromOverviewText(text) {
    const raw = String(text || "").toLowerCase();
    if (/research|market|validate|evidence/.test(raw)) return "research";
    if (/product|preview|site|auth|checkout|runtime|deploy/.test(raw)) return "product";
    if (/outreach|distribution|lead|customer|sales|growth|publish/.test(raw)) return "growth";
    if (/creative|seo|content|ad/.test(raw)) return "creative";
    return "ops";
  }

  function statusFromOverviewTask(task) {
    const rawStatus = String(task && task.status || "").toLowerCase();
    const tone = String(task && task.tone || "").toLowerCase();
    if (/blocked|error|fail|attention|missing/.test(rawStatus) || tone === "blocked") return "blocked";
    if (/done|complete|success|succeeded|passed/.test(rawStatus) || tone === "done") return "done";
    if (/running|active|working|watch/.test(rawStatus) || tone === "active") return "running";
    if (/queued|scheduled|waiting|pending/.test(rawStatus) || tone === "waiting") return "scheduled";
    if (/review/.test(rawStatus)) return "review";
    return "todo";
  }

  function mapOverviewTask(task, index) {
    const label = String(task && (task.label || task.id || `task ${index + 1}`) || `task ${index + 1}`).trim();
    const detail = String(task && task.detail || "").trim();
    const lane = laneFromOverviewText(`${label} ${detail}`);
    const status = statusFromOverviewTask(task);
    return {
      id: String(task && task.id || `overview:${index}:${label}`).trim(),
      key: String(task && task.source || status || "task").trim(),
      title: label,
      body: detail,
      lane,
      assignee: String(task && task.source || lane || "ops").replace(/\s+/g, "-").slice(0, 12),
      status,
      priority: status === "running" || status === "blocked" ? "p1" : status === "scheduled" ? "p2" : "p3",
      created: Date.now() - index * 1000,
      progress: { done: status === "done" ? 1 : 0, total: 1 },
      comments: [],
      events: detail ? [{ kind: "detail", note: detail }] : [],
      runs: [],
      result: status === "done" ? detail : null,
      block_reason: status === "blocked" ? detail : "",
      _live: true,
      _detailLoaded: true,
      _fromOverview: true,
    };
  }

  function traceUpdatedAtMs(value) {
    if (typeof value === "number" && Number.isFinite(value)) return value;
    const parsed = Date.parse(String(value || ""));
    return Number.isFinite(parsed) ? parsed : 0;
  }

  function traceStatus(entry) {
    const raw = String(entry && entry.status || "").trim().toLowerCase();
    if (!raw) return "todo";
    if (raw === "started" || raw === "output" || raw === "heartbeat") return "running";
    if (/fail|error|blocked/.test(raw)) return "blocked";
    if (/complete|done|success/.test(raw)) return "done";
    if (/running|active/.test(raw)) return "running";
    if (/queued|scheduled|waiting|pending/.test(raw)) return "scheduled";
    return raw;
  }

  function mergedTraceEntries(snapshot) {
    const overview = snapshot && snapshot.overview || {};
    const base = Array.isArray(overview.trace) ? overview.trace.filter(Boolean) : [];
    const byKey = new Map();
    base.forEach((entry, index) => {
      const key = String(entry && (entry.entry_key || entry.id || `trace:${index}`) || `trace:${index}`);
      byKey.set(key, entry);
    });
    LIVE.liveTrace.forEach((entry, key) => {
      if (!entry) return;
      const existing = byKey.get(key);
      byKey.set(key, existing ? Object.assign({}, existing, entry) : entry);
    });
    return Array.from(byKey.values()).sort((a, b) => traceUpdatedAtMs(a && a.updated_at) - traceUpdatedAtMs(b && b.updated_at));
  }

  function currentActionFromSnapshot(snapshot) {
    const overview = snapshot && snapshot.overview || {};
    const currentAction = overview.current_action && typeof overview.current_action === "object"
      ? overview.current_action
      : {};
    const ceo = overview.ceo_loop && typeof overview.ceo_loop === "object"
      ? overview.ceo_loop
      : {};
    const product = overview.product && typeof overview.product === "object"
      ? overview.product
      : {};
    const label = String(currentAction.label || ceo.headline || "").trim();
    const detail = String(currentAction.detail || ceo.detail || ceo.next_action || "").trim();
    const blocker = String(currentAction.blocker || product.publish_blocker || "").trim();
    const status = traceStatus({ status: currentAction.status || ceo.status || (blocker ? "blocked" : "") });
    return {
      source: String(currentAction.source || "").trim(),
      label,
      detail,
      blocker,
      status: status || (blocker ? "blocked" : "todo"),
    };
  }

  function liveStatusFromSnapshot(snapshot) {
    if (LIVE.historyRunning) return { text: "thinking…", state: "run" };
    const currentAction = currentActionFromSnapshot(snapshot);
    if (currentAction.status === "blocked") {
      return { text: currentAction.label || "blocked", state: "paused" };
    }
    if (currentAction.status === "running" || currentAction.status === "scheduled") {
      const activityText = `${currentAction.label} ${currentAction.detail}`.toLowerCase();
      const state = /bootstrap|build|publish|deploy|product|site/.test(activityText) ? "build" : "run";
      return {
        text: currentAction.label || currentAction.detail || currentAction.status,
        state,
      };
    }
    const trace = mergedTraceEntries(snapshot);
    if (trace.some((entry) => traceStatus(entry) === "running")) {
      return { text: "running", state: "run" };
    }
    const background = mapBackgroundRunTask(snapshot);
    if (background && background.status === "running") {
      return { text: "running", state: "run" };
    }
    return { text: "idle", state: "idle" };
  }

  function mapTraceEntry(entry, index) {
    const kind = String(entry && entry.kind || "note").trim().toLowerCase();
    const label = String(entry && (entry.label || entry.skill_name || entry.tool_name || entry.id || `trace ${index + 1}`) || `trace ${index + 1}`).trim();
    const detail = String(entry && entry.detail || entry && entry.summary || "").trim();
    const status = traceStatus(entry);
    const lane = laneFromOverviewText(`${label} ${detail} ${kind}`);
    return {
      id: String(entry && (entry.entry_key || entry.id || `trace:${index}`) || `trace:${index}`).trim(),
      key: kind || status || "trace",
      title: label,
      body: detail,
      lane,
      assignee: kind === "skill" ? "skill" : kind === "turn" ? "ceo" : String(entry && entry.source || lane || "ops").replace(/\s+/g, "-").slice(0, 12),
      status,
      priority: status === "running" || status === "blocked" ? "p1" : status === "scheduled" ? "p2" : "p3",
      created: traceUpdatedAtMs(entry && entry.updated_at) || (Date.now() - index * 1000),
      progress: { done: status === "done" ? 1 : 0, total: 1 },
      comments: [],
      events: detail ? [{ kind: kind || "trace", note: detail }] : [],
      runs: [],
      result: status === "done" ? detail : null,
      block_reason: status === "blocked" ? detail : "",
      _live: true,
      _detailLoaded: true,
      _fromTrace: true,
    };
  }

  function laneFromTask(task) {
    const raw = [
      task.tenant,
      task.assignee,
      task.workflow_template_id,
      task.current_step_key,
      task.title,
      task.body,
    ]
      .filter(Boolean)
      .join(" ")
      .toLowerCase();
    if (/research|market|spec|validate/.test(raw)) return "research";
    if (/product|site|runtime|auth|checkout|copy/.test(raw)) return "product";
    if (/growth|lead|customer|sales|outreach|pipeline|distribution/.test(raw)) return "growth";
    if (/creative|content|seo|ad|meta/.test(raw)) return "creative";
    return "ops";
  }

  function assigneeLabel(task, lane) {
    const raw = String(task.assignee || lane || "ops").trim();
    return raw ? raw.replace(/\s+/g, "-").slice(0, 12) : lane;
  }

  function progressForTask(task) {
    if (task && task.progress && typeof task.progress.total === "number") {
      const done = Number(task.progress.done || 0);
      const total = Math.max(1, Number(task.progress.total || 0));
      return { done: Math.max(0, done), total };
    }
    if (String(task.status || "").toLowerCase() === "done") return { done: 1, total: 1 };
    return { done: 0, total: 1 };
  }

  function mapBoardTask(task) {
    const lane = laneFromTask(task);
    return {
      id: task.id,
      key: task.current_step_key || task.workflow_template_id || task.id,
      title: task.title || task.id,
      body: task.body || task.latest_summary || "",
      lane,
      assignee: assigneeLabel(task, lane),
      status: liveStatusLabel(task),
      priority: livePriority(task.priority),
      created: Number(task.created_at || Date.now()),
      progress: progressForTask(task),
      comments: [],
      events: [],
      runs: [],
      result: task.result || task.latest_summary || null,
      block_reason: task.last_failure_error || "",
      _live: true,
      _detailLoaded: false,
    };
  }

  function outputLaneFromPath(item) {
    const raw = `${item && item.path || ""} ${item && item.detail || ""} ${item && item.kind || ""}`.toLowerCase();
    if (/research/.test(raw)) return "research";
    if (/product|site|runtime|app/.test(raw)) return "product";
    if (/distribution|outreach|publish|receipt/.test(raw)) return "growth";
    if (/creative|image|video|ugc|ad/.test(raw)) return "creative";
    return "ops";
  }

  function mapDeliverableTask(item, index) {
    const path = String(item && item.path || "").trim();
    if (!path) return null;
    const lane = outputLaneFromPath(item);
    const detail = String(item && item.detail || "").trim();
    const title = String(item && (item.title || path) || path).trim();
    return {
      id: String(item && item.id || `deliverable:${path}:${index}`).trim(),
      key: String(item && item.kind || "deliverable").trim(),
      title,
      body: [detail, compactPath(path)].filter(Boolean).join(" · "),
      lane,
      assignee: lane,
      status: "done",
      priority: "p3",
      created: Number(item && item.at || 0) || (Date.now() - index * 1000),
      progress: { done: 1, total: 1 },
      comments: [],
      events: [
        { kind: "deliverable", note: compactPath(path) || path },
        ...(detail ? [{ kind: "type", note: detail }] : []),
      ],
      runs: [],
      result: compactPath(path) || path,
      block_reason: "",
      _live: true,
      _detailLoaded: true,
      _fromOutput: true,
      _deliverablePath: path,
    };
  }

  function backgroundRunStatus(run) {
    const raw = String(run && run.status || "").trim().toLowerCase();
    if (!raw) return "running";
    if (/error|fail|blocked/.test(raw)) return "blocked";
    if (/queued|scheduled|pending|waiting/.test(raw)) return "scheduled";
    if (/done|complete|success/.test(raw)) return "done";
    return "running";
  }

  function humanizeKey(value) {
    return String(value || "work")
      .replace(/[_-]+/g, " ")
      .trim()
      .replace(/\b\w/g, (ch) => ch.toUpperCase());
  }

  function cronTaskTitle(job) {
    const raw = String(job && job.name || "").trim();
    if (/takyon-ceo|ceo/i.test(raw)) return "CEO wake loop";
    return raw ? humanizeKey(raw) : "Scheduled work";
  }

  function mapCronTask(job, index) {
    if (!job || typeof job !== "object") return null;
    const nextRun = String(job.next_run || "").trim();
    const state = String(job.state || job.status || "").trim();
    const detailParts = [];
    if (nextRun) detailParts.push(`Next wake ${nextRun}`);
    if (state) detailParts.push(`State: ${state}`);
    return {
      id: `cron:${String(job.id || job.name || index).trim() || index}`,
      key: "cron",
      title: cronTaskTitle(job),
      body: detailParts.join(" · ") || "Scheduled CEO check.",
      lane: "ops",
      assignee: "cron",
      status: "scheduled",
      priority: "p2",
      created: parseNextRun(nextRun) || Date.now() + index,
      progress: { done: 0, total: 1 },
      comments: [],
      events: detailParts.map((note) => ({ kind: "cron", note })),
      runs: [],
      result: null,
      block_reason: "",
      _live: true,
      _detailLoaded: true,
      _fromCron: true,
    };
  }

  function mapBackgroundRunTask(snapshot) {
    const run = snapshot && snapshot.background_run;
    if (!run || typeof run !== "object") return null;
    const status = backgroundRunStatus(run);
    if (status === "done") return null;
    const label = String(run.kind || "").trim();
    const detail = String(run.detail || "").trim();
    const title = label ? humanizeKey(label) : "CEO background run";
    return {
      id: `background:${label || "run"}`,
      key: label || "background",
      title,
      body: detail || "The CEO is working in the background.",
      lane: laneFromOverviewText(`${title} ${detail}`),
      assignee: "ceo",
      status,
      priority: status === "blocked" ? "p1" : status === "scheduled" ? "p2" : "p1",
      created: Number(run.started_at || 0) * 1000 || Date.now(),
      progress: { done: 0, total: 1 },
      comments: [],
      events: detail ? [{ kind: "background", note: detail }] : [],
      runs: [],
      result: null,
      block_reason: status === "blocked" ? detail : "",
      _live: true,
      _detailLoaded: true,
      _fromSummary: true,
    };
  }

  function applyBoard(board, snapshot) {
    const next = [];
    const seen = new Set();
    const pushTask = (task) => {
      if (!task || !task.id) return;
      const key = String(task.id).trim();
      if (!key || seen.has(key)) return;
      seen.add(key);
      next.push(task);
    };
    const cols = Array.isArray(board && board.columns) ? board.columns : [];
    const boardTasks = [];
    cols.forEach((col) => {
      const tasks = Array.isArray(col && col.tasks) ? col.tasks : [];
      tasks.forEach((task) => {
        if (String(task && task.status || "").toLowerCase() === "archived") return;
        boardTasks.push(mapBoardTask(task || {}));
      });
    });
    const deliverableTasks = Array.isArray(snapshot && snapshot.outputs)
      ? snapshot.outputs
          .map((item, index) => mapDeliverableTask(item, index))
          .filter(Boolean)
      : [];
    const cronTasks = Array.isArray(snapshot && snapshot.overview && snapshot.overview.cron)
      ? snapshot.overview.cron
          .map((job, index) => mapCronTask(job, index))
          .filter(Boolean)
          .sort((a, b) => a.created - b.created)
      : [];
    const backgroundTask = mapBackgroundRunTask(snapshot);
    cronTasks.forEach(pushTask);
    if (backgroundTask) pushTask(backgroundTask);
    const overviewTasks = Array.isArray(snapshot && snapshot.overview && snapshot.overview.tasks)
      ? snapshot.overview.tasks
      : [];
    overviewTasks.forEach((task, index) => {
      const mapped = mapOverviewTask(task || {}, index);
      if (!mapped || !["running", "scheduled", "blocked"].includes(mapped.status)) return;
      pushTask(mapped);
    });
    if (!next.some((task) => task.status === "running" || task.status === "blocked")) {
      const trace = mergedTraceEntries(snapshot);
      trace.forEach((entry, index) => {
        const mapped = mapTraceEntry(entry || {}, index);
        if (!mapped || !["running", "scheduled", "blocked"].includes(mapped.status)) return;
        if (mapped.key === "tool" && mapped.assignee === "runtime") return;
        pushTask(mapped);
      });
    }
    boardTasks.forEach((task) => {
      if (!["running", "scheduled", "blocked"].includes(task.status)) return;
      pushTask(task);
    });
    deliverableTasks.forEach(pushTask);
    boardTasks.forEach((task) => {
      if (task.status !== "done") return;
      pushTask(task);
    });
    RT.tasks = next;
    renderBoard();
  }

  function featureList(snapshot) {
    const overview = (snapshot && snapshot.overview) || {};
    const outputs = Array.isArray(snapshot && snapshot.outputs) ? snapshot.outputs : [];
    const features = [];
    const product = overview.product || {};
    const research = overview.research || {};
    const posts = Array.isArray(overview.posts) ? overview.posts : [];
    const artifacts = overview.artifacts || {};
    if (String(product.public_url || "").trim()) features.push("Published site");
    else if (outputs.some((item) => String(item && item.path || "") === "product/site/index.html")) features.push("Landing page");
    if (Number(research.count || 0) > 0) features.push(`${Number(research.count)} research output${Number(research.count) === 1 ? "" : "s"}`);
    if (posts.length > 0) features.push(`${posts.length} distribution post${posts.length === 1 ? "" : "s"}`);
    if (Number(artifacts.count || 0) > 0) features.push(`${Number(artifacts.count)} artifact${Number(artifacts.count) === 1 ? "" : "s"}`);
    return features.slice(0, 4);
  }

  function parseNextRun(value) {
    if (!value) return 0;
    const ms = Date.parse(String(value));
    return Number.isFinite(ms) ? ms : 0;
  }

  function statusText(snapshot) {
    const overview = (snapshot && snapshot.overview) || {};
    const ceo = overview.ceo_loop || {};
    const wake = overview.wake_health || {};
    const background = snapshot && snapshot.background_run || {};
    const currentAction = currentActionFromSnapshot(snapshot);
    return (
      String(currentAction.label || "").trim() ||
      String(currentAction.detail || "").trim() ||
      String(ceo.next_action || "").trim() ||
      String(ceo.headline || "").trim() ||
      String(background.detail || "").trim() ||
      String(wake.headline || "").trim() ||
      "synced"
    );
  }

  function formatRichText(text) {
    return esc(String(text || "")).replace(/\n/g, "<br>");
  }

  function normalizeOpenableUrl(value) {
    const text = String(value || "").trim();
    if (!text) return "";
    if (/^https?:\/\//i.test(text) || /^data:/i.test(text)) return text;
    if (/^[a-z0-9][a-z0-9.-]*\.[a-z]{2,}(?:\/.*)?$/i.test(text)) {
      return `https://${text}`;
    }
    return "";
  }

  function openUrlInNewTab(url) {
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

  function compactPath(path) {
    const raw = String(path || "").trim();
    if (!raw) return "";
    if (raw.length <= 42) return raw;
    const parts = raw.split("/");
    if (parts.length <= 2) return `${raw.slice(0, 39)}...`;
    return `${parts[0]}/.../${parts[parts.length - 1]}`;
  }

  function mediaKindForPath(path) {
    const match = String(path || "").toLowerCase().match(/\.([a-z0-9]+)$/);
    const ext = match && match[1] || "";
    if (VIDEO_EXTENSIONS.has(ext)) return "video";
    if (IMAGE_EXTENSIONS.has(ext)) return "image";
    return "";
  }

  function wholeCredits(value) {
    const count = Number(value);
    if (!Number.isFinite(count)) return 0;
    return Math.max(0, Math.trunc(count));
  }

  function prettyHost(url) {
    return String(url || "").trim().replace(/^https?:\/\//i, "").replace(/\/+$/, "");
  }

  function renderNorthStarPanel(overview) {
    const metrics = overview && overview.metrics || {};
    const mrrCents = Number.isFinite(Number(metrics.mrr_cents))
      ? Math.max(0, Math.round(Number(metrics.mrr_cents)))
      : 0;
    const revenueCents = Number.isFinite(Number(metrics.revenue_cents))
      ? Math.max(0, Math.round(Number(metrics.revenue_cents)))
      : 0;
    const signups = Number.isFinite(Number(metrics.users))
      ? Math.max(0, Math.trunc(Number(metrics.users)))
      : 0;
    const checkouts = Number.isFinite(Number(metrics.checkout_intents))
      ? Math.max(0, Math.trunc(Number(metrics.checkout_intents)))
      : 0;
    const paying = Number.isFinite(Number(metrics.paid_customers))
      ? Math.max(0, Math.trunc(Number(metrics.paid_customers)))
      : 0;
    const queuedJobs = Number.isFinite(Number(metrics.queued_jobs))
      ? Math.max(0, Math.trunc(Number(metrics.queued_jobs)))
      : 0;
    const stagesReached = [signups, checkouts, paying].filter((value) => value > 0).length;
    const tiers = {
      mrr: { hero: `${formatBudgetCents(mrrCents)}/mo`, pill: mrrCents > 0 ? `${formatBudgetCents(mrrCents)}/mo` : "$0" },
      signups: { hero: formatMetricCount(signups), pill: formatMetricCount(signups) },
      progress: { hero: `${stagesReached}/3`, pill: `${stagesReached}/3` },
    };
    const tierLabels = { mrr: "MRR", signups: "Sign-ups", progress: "Progress" };
    const tierOrder = ["mrr", "signups", "progress"];
    const adaptiveTier = mrrCents > 0 ? "mrr" : signups > 0 ? "signups" : "progress";
    const activeTier = LIVE.northStarTab && tiers[LIVE.northStarTab] ? LIVE.northStarTab : adaptiveTier;
    const northStar = tiers[activeTier];
    function northStarBars(items) {
      const maxValue = Math.max(...items.map((bar) => bar.value), 1);
      return `<div class="board-bars">${items.map((bar) => {
        const height = bar.value > 0 ? Math.max(14, Math.round((bar.value / maxValue) * 100)) : 10;
        const fillColor = bar.value > 0 ? bar.color : "var(--paper-2)";
        return `<div class="board-bar">
          <div class="board-bar-v">${esc(bar.display)}</div>
          <div class="board-bar-track"><div class="board-bar-fill" style="height:${height}%;background:${fillColor}"></div></div>
          <div class="board-bar-l">${esc(bar.label)}</div>
        </div>`;
      }).join("")}</div>`;
    }
    let graphHtml;
    if (activeTier === "progress") {
      const pct = Math.round((stagesReached / 3) * 100);
      const stage = (label, on) => `<span class="${on ? "on" : ""}">${esc(label)}</span>`;
      graphHtml = `<div class="board-prog">
        <div class="board-prog-track"><div class="board-prog-fill" style="width:${pct}%${pct ? "" : ";border-right-width:0"}"></div></div>
        <div class="board-prog-stages">${stage("sign-ups", signups > 0)}${stage("checkout", checkouts > 0)}${stage("paying", paying > 0)}</div>
      </div>`;
    } else if (activeTier === "mrr") {
      graphHtml = northStarBars([
        { label: "mrr / mo", value: mrrCents, display: formatBudgetCents(mrrCents), color: "var(--green)" },
        { label: "lifetime", value: revenueCents, display: formatBudgetCents(revenueCents), color: "var(--blue)" },
      ]);
    } else {
      graphHtml = northStarBars([
        { label: "sign-ups", value: signups, display: formatMetricCount(signups), color: "var(--blue)" },
        { label: "checkout", value: checkouts, display: formatMetricCount(checkouts), color: "var(--amber)" },
        { label: "paying", value: paying, display: formatMetricCount(paying), color: "var(--green)" },
      ]);
    }
    return `
      <section class="board-graph">
        <div class="board-star"><div class="board-star-v">${esc(northStar.hero)}</div></div>
        <div class="board-tabs">${tierOrder.map((key) => {
          return `<button class="board-tab${key === activeTier ? " on" : ""}" data-tab="${key}" type="button"><span class="board-tab-k">${esc(tierLabels[key])}</span><span class="board-tab-v">${esc(tiers[key].pill)}</span></button>`;
        }).join("")}</div>
        ${graphHtml}
      </section>
    `;
  }

  function renderGraphWindow() {
    const w = document.getElementById("w-graph");
    if (!w) return;
    body(w).innerHTML = renderNorthStarPanel(LIVE.workspaceOverview || {});
    body(w).querySelectorAll(".board-tab").forEach((btn) => {
      btn.addEventListener("click", () => {
        LIVE.northStarTab = btn.getAttribute("data-tab") || "";
        renderGraphWindow();
      });
    });
  }

  function hasLiveProgress(snapshot) {
    const currentAction = currentActionFromSnapshot(snapshot);
    if (currentAction.status === "running" || currentAction.status === "scheduled") return true;
    const backgroundStatus = String(snapshot && snapshot.background_run && snapshot.background_run.status || "").trim().toLowerCase();
    if (backgroundStatus === "queued" || backgroundStatus === "running") return true;
    const trace = mergedTraceEntries(snapshot);
    if (trace.some((entry) => {
      const status = traceStatus(entry);
      return status === "running" || status === "scheduled";
    })) return true;
    const tasks = Array.isArray(snapshot && snapshot.overview && snapshot.overview.tasks)
      ? snapshot.overview.tasks
      : [];
    return tasks.some((task) => {
      const source = String(task && task.source || "").trim().toLowerCase();
      const label = String(task && task.label || "").trim();
      const status = String(task && task.status || "").trim().toLowerCase();
      return (
        (source === "runtime" || source === "job" || /CEO (bootstrap|wake)/i.test(label)) &&
        (status === "queued" || status === "running")
      );
    });
  }

  const LIVE_WORKSPACE_VIEW = "full";

  function restartLivePollTimer(ms) {
    const nextMs = Number(ms);
    if (!Number.isFinite(nextMs) || nextMs < 250) return;
    if (LIVE.pollTimer) window.clearInterval(LIVE.pollTimer);
    LIVE.pollMs = nextMs;
    LIVE.pollTimer = window.setInterval(() => {
      if (!LIVE.activeBusiness) return;
      void refreshBusinessData(LIVE.activeBusiness, {
        skipAccount: true,
        skipCredits: true,
        skipBoard: true,
        view: LIVE_WORKSPACE_VIEW,
      });
    }, nextMs);
  }

  function syncLivePollTimer(snapshot) {
    const desiredMs = hasLiveProgress(snapshot) ? 1500 : 15000;
    if (LIVE.pollMs !== desiredMs) restartLivePollTimer(desiredMs);
  }

  function restartHistoryPollTimer(ms) {
    const nextMs = Number(ms);
    if (!Number.isFinite(nextMs) || nextMs < 500) return;
    if (LIVE.historyPollTimer) window.clearInterval(LIVE.historyPollTimer);
    LIVE.historyPollMs = nextMs;
    LIVE.historyPollTimer = window.setInterval(() => {
      void pollSessionHistory();
    }, nextMs);
  }

  function syncHistoryPollTimer() {
    const desiredMs = LIVE.historyRunning || !LIVE.ws ? 2500 : 4000;
    if (LIVE.historyPollMs !== desiredMs) restartHistoryPollTimer(desiredMs);
  }

  function scheduleLiveRefresh(delayMs) {
    const waitMs = Number(delayMs);
    if (!Number.isFinite(waitMs) || waitMs < 0 || !LIVE.activeBusiness) return;
    if (LIVE.refreshTimer) window.clearTimeout(LIVE.refreshTimer);
    LIVE.refreshTimer = window.setTimeout(() => {
      LIVE.refreshTimer = null;
      void refreshBusinessData(LIVE.activeBusiness, {
        skipAccount: true,
        skipCredits: true,
        skipBoard: true,
        view: LIVE_WORKSPACE_VIEW,
      });
    }, waitMs);
  }

  function channelLabel(source) {
    const s = String(source || "").toLowerCase().replace(/^test-/, "");
    if (!s) return "Post";
    if (s === "x" || s.startsWith("x-") || s.includes("twitter")) return "X";
    if (s.includes("reddit")) return "Reddit";
    if (s.includes("hacker")) return "HN";
    if (s.includes("linkedin")) return "LinkedIn";
    if (s.includes("forum")) return "Forum";
    if (s.includes("outreach")) return "Outreach";
    return s.charAt(0).toUpperCase() + s.slice(1);
  }

  function publishedStateLabel(item) {
    const status = String(item && item.status || "").trim().toLowerCase();
    const mode = String(item && item.mode || "").trim().toLowerCase();
    if (status === "published_local" || mode === "test" || String(item && item.artifact_path || "").trim()) {
      return "locally published";
    }
    if (status === "published" || String(item && item.url || "").trim() || mode === "live") {
      return "published";
    }
    return status ? status.replace(/_/g, " ") : "published";
  }

  function publishedPostEntries() {
    const overview = LIVE.workspaceOverview || {};
    const posts = Array.isArray(overview.posts) ? overview.posts : [];
    const postEntries = posts.filter((post) => normalizeOpenableUrl(post && post.url) || (post && post.artifact_path));
    if (postEntries.length) {
      return postEntries.map((post) => ({
        kind: "post",
        title: String(post && post.title || channelLabel(post && post.source) || "Published post").trim(),
        meta: `${channelLabel(post && post.source)} · ${publishedStateLabel(post)}`,
        actionLabel: normalizeOpenableUrl(post && post.url) ? "open" : "preview",
        payload: post,
      }));
    }
    const outreach = overview.artifacts && overview.artifacts.outreach || {};
    const items = Array.isArray(outreach.items) ? outreach.items : [];
    return items
      .filter((item) => String(item && item.path || "").trim())
      .map((item) => ({
        kind: "artifact",
        title: compactPath(item && item.path || "") || "Published post",
        meta: publishedStateLabel(item),
        actionLabel: "preview",
        payload: item,
      }));
  }

  function deliverableEntries() {
    const outputs = Array.isArray(LIVE.workspaceOutputs) ? LIVE.workspaceOutputs : [];
    return outputs
      .filter((item) => item && item.path && item.title)
      .map((item) => ({
        id: item.id || `output:${item.path}`,
        title: String(item.title || item.path).trim(),
        detail: String(item.detail || item.kind || "").trim(),
        kind: String(item.kind || "file").trim().toLowerCase(),
        path: String(item.path || "").trim(),
        at: Number(item.at || 0),
      }))
      .sort((a, b) => b.at - a.at);
  }

  function deliverableActionLabel(item) {
    if (!item || !item.path) return "";
    if (normalizeOpenableUrl(item.url)) return "open";
    return /\.html?$/i.test(item.path) ? "preview" : "open";
  }

  function previewWindow(title, html) {
    const win = makeWin({
      id: "w-preview",
      title: title || "preview",
      x: 148,
      y: 72,
      w: 720,
      h: 480,
      html,
    });
    const titleEl = win.querySelector(".win__title");
    if (titleEl) titleEl.textContent = title || "preview";
    body(win).innerHTML = html;
    focusWin(win);
    return win;
  }

  function previewPathForLive() {
    const overview = LIVE.workspaceOverview || {};
    const website = overview.artifacts && overview.artifacts.website || {};
    const product = overview.product || {};
    return String(website.path || website.source_path || product.source_path || "").trim();
  }

  async function openSitePreview(path, label) {
    const business = String(LIVE.activeBusiness || "").trim().toLowerCase();
    const targetPath = String(path || "").trim();
    if (!business || !targetPath) return;
    const win = previewWindow(label || compactPath(targetPath) || "site preview", `<div class="lab">loading preview</div><div class="meta">${esc(targetPath)}</div>`);
    try {
      const res = await fetchJSON(`/api/takyon/businesses/${encodeURIComponent(business)}/site-preview?path=${encodeURIComponent(targetPath)}`);
      const url = normalizeOpenableUrl(res && res.url);
      if (!url) throw new Error("Preview URL unavailable.");
      body(win).innerHTML = `
        <div class="lab">${esc(label || "site preview")}</div>
        <div class="meta" style="margin-bottom:10px">${esc(res && res.path || targetPath)}</div>
        <iframe
          src="${esc(url)}"
          title="${esc(label || "site preview")}"
          style="width:100%;height:calc(100% - 44px);border:2px solid var(--ink);background:#fff"
        ></iframe>
      `;
    } catch (err) {
      body(win).innerHTML = `
        <div class="lab">preview unavailable</div>
        <div class="meta">${esc(err instanceof Error ? err.message : String(err))}</div>
      `;
    }
  }

  async function openDocument(path, label) {
    const business = String(LIVE.activeBusiness || "").trim().toLowerCase();
    const targetPath = String(path || "").trim();
    if (!business || !targetPath) return;
    if (/\.html?$/i.test(targetPath)) {
      await openSitePreview(targetPath, label);
      return;
    }
    const win = previewWindow(label || compactPath(targetPath) || "document", `<div class="lab">loading document</div><div class="meta">${esc(targetPath)}</div>`);
    try {
      const mediaKind = mediaKindForPath(targetPath);
      if (mediaKind) {
        const sessionId = await ensureSession(business);
        const media = await rpc("takyon.file.media", {
          session_id: sessionId,
          business_slug: business,
          path: targetPath,
        }, 20000);
        const mediaUrl = String(media && media.url || "");
        const mediaType = String(media && media.media_type || "");
        body(win).innerHTML = `
          <div class="lab">${esc(label || compactPath(targetPath) || "document")}</div>
          <div class="meta" style="margin-bottom:10px">${esc(media && media.path || targetPath)}</div>
          ${mediaType.startsWith("video/")
            ? `<video controls src="${esc(mediaUrl)}" style="width:100%;max-height:calc(100% - 44px);background:#000;border:2px solid var(--ink)"></video>`
            : `<img alt="${esc(label || compactPath(targetPath) || "document")}" src="${esc(mediaUrl)}" style="width:100%;height:auto;max-height:calc(100% - 44px);object-fit:contain;border:2px solid var(--ink);background:#fff" />`}
        `;
        return;
      }
      const res = await fetchJSON(`/api/takyon/businesses/${encodeURIComponent(business)}/file?path=${encodeURIComponent(targetPath)}`);
      const content = String(res && res.content || "");
      body(win).innerHTML = `
        <div class="lab">${esc(label || compactPath(targetPath) || "document")}</div>
        <div class="meta" style="margin-bottom:10px">${esc(res && res.path || targetPath)}${res && res.truncated ? " · truncated" : ""}</div>
        <pre style="margin:0;border:2px solid var(--ink);background:#fff;padding:12px;white-space:pre-wrap;overflow:auto;height:calc(100% - 44px);font:12px/1.5 'Space Mono',monospace">${esc(content)}</pre>
      `;
    } catch (err) {
      body(win).innerHTML = `
        <div class="lab">document unavailable</div>
        <div class="meta">${esc(err instanceof Error ? err.message : String(err))}</div>
      `;
    }
  }

  function renderPlanSummary(snapshot) {
    // Workspace snapshots still carry overview tasks, but the operator chat
    // should not echo them back as a synthetic plan card.
    void snapshot;
  }

  function traceLogSignature(entry) {
    return [
      entry && (entry.entry_key || entry.id || ""),
      entry && entry.kind || "",
      entry && entry.status || "",
      entry && entry.label || "",
      entry && entry.detail || "",
    ].join("|");
  }

  function traceLogHtml(entry) {
    const kind = String(entry && entry.kind || "note").trim().toLowerCase();
    const status = traceStatus(entry);
    const badge = kind === "skill" ? "skill" : kind === "tool" ? "tool" : kind === "turn" ? "turn" : "note";
    const badgeClass = status === "done" ? "l-green" : status === "blocked" ? "l-red" : "l-blue";
    const label = String(entry && entry.label || badge).trim();
    const detail = String(entry && entry.detail || "").trim();
    return `<span class="${badgeClass}">[${esc(badge)}]</span> ${esc(label)}${detail ? ` — ${esc(detail)}` : ""}`;
  }

  function syncOverviewActivity(snapshot) {
    if (!snapshot) return;
    const overview = snapshot.overview || {};
    const backgroundRun = snapshot.background_run || {};

    const backgroundDetail = String(backgroundRun.detail || "").trim();
    if (backgroundDetail && backgroundDetail !== LIVE.lastBackgroundDetail) {
      ceolog(`<span class="sys">[background]</span> ${esc(backgroundDetail)}`, true);
      LIVE.lastBackgroundDetail = backgroundDetail;
    }

    const trace = mergedTraceEntries(snapshot);
    if (trace.length) {
      trace.forEach((entry) => {
        const signature = traceLogSignature(entry);
        if (!signature || LIVE.traceLogSeen.has(signature)) return;
        LIVE.traceLogSeen.add(signature);
        ceolog(traceLogHtml(entry), true);
      });
    }
    const currentAction = currentActionFromSnapshot(snapshot);
    const product = overview.product || {};
    const blocker = String(currentAction.blocker || product.publish_blocker || "").trim();
    const headline = [
      currentAction.status,
      currentAction.label,
      currentAction.detail,
      blocker,
    ].join("|");
    if (headline && headline !== LIVE.lastCeoHeadline) {
      const statusLabel = currentAction.status || "recorded";
      const summary = currentAction.label || currentAction.detail || "Business is synced.";
      const detail = blocker || currentAction.detail;
      ceolog(
        `<span class="sys">[${esc(statusLabel)}]</span> ${esc(summary)}${detail && detail !== summary ? ` — ${esc(detail)}` : ""}`,
        true,
      );
      LIVE.lastCeoHeadline = headline;
    }
  }

  function applyWorkspace(snapshot, summary) {
    const overview = (snapshot && snapshot.overview) || {};
    const current = (snapshot && snapshot.current) || {};
    const product = overview.product || {};
    const cron = selectWakeCron(overview) || {};
    const ceo = overview.ceo_loop || {};
    LIVE.workspaceSnapshot = snapshot || null;
    LIVE.workspaceOverview = overview || {};
    LIVE.workspaceOutputs = Array.isArray(snapshot && snapshot.outputs) ? snapshot.outputs : [];
    RT.biz = Object.assign({}, RT.biz || {}, {
      slug: snapshot.business_slug || RT.biz && RT.biz.slug || "",
      name: String(current.name || summary && summary.name || RT.biz && RT.biz.name || snapshot.business_slug || "litebulb").trim(),
      idea: String(current.goal || summary && summary.goal || RT.biz && RT.biz.idea || "").trim(),
      mode: String(current.mode || summary && summary.mode || RT.biz && RT.biz.mode || "test").trim().toLowerCase() || "test",
      publicUrl: String(product.public_url || "").trim(),
      siteHost: (() => {
        try {
          return product.public_url ? new URL(product.public_url).host : `${snapshot.business_slug}.com`;
        } catch (_err) {
          return `${snapshot.business_slug}.com`;
        }
      })(),
      publishStatus: String(product.publish_status || product.status || "").trim(),
    });
    RT.shipped = featureList(snapshot);
    RT.nextWakeAt = parseNextRun(cron.next_run);
    RT.paused = cron.enabled === false || String(cron.state || "").toLowerCase() === "paused";
    RT.move = statusText(snapshot);
    RT.credits = dollarFromAccount();
    updateMenu();
    renderProduct();
    renderOutreach();
    renderDeliverablesWindow();
    if (document.getElementById("w-wallet")) renderWalletWindow();
    renderPlanSummary(snapshot);
    syncOverviewActivity(snapshot);
    syncLivePollTimer(snapshot);
    syncHistoryPollTimer();
    const modeEl = $("#mb-mode");
    if (modeEl) {
      modeEl.textContent = RT.biz.mode || "test";
      modeEl.classList.toggle("live-mode", RT.biz.mode === "live");
      modeEl.classList.toggle("test", RT.biz.mode !== "live");
    }
    $("#mb-biz").textContent = RT.biz.name || RT.biz.slug || "—";
    if (snapshot.business_slug !== LIVE.bootedBusiness) {
      addThink("connected to the live Takyon runtime.");
      LIVE.bootedBusiness = snapshot.business_slug;
    }
  }

  function buildOutreachRow(label, meta, state, action) {
    const clickable = !!(action && action.type);
    const actionTag = clickable
      ? `<span class="chan-st ${state === "live" ? "live" : ""}" style="cursor:pointer">${esc(action.label || "open")}</span>`
      : `<span class="chan-st ${state === "live" ? "live" : ""}">${esc(state)}</span>`;
    return `<div class="chan"${clickable ? ` data-action="${esc(action.type)}" data-action-index="${Number(action.index || 0)}" tabindex="0" role="button" style="cursor:pointer"` : ""}>
      <div class="chan-top"><span class="chan-nm">${esc(label)}</span>${actionTag}</div>
      <div class="meta" style="margin-top:6px">${esc(meta)}</div>
    </div>`;
  }

  function openDeliverablesWindow() {
    const win = makeWin({
      id: "w-files",
      title: "deliverables · outputs",
      x: 648,
      y: 84,
      w: 360,
      h: 360,
      html: "",
    });
    renderDeliverablesWindow();
    focusWin(win);
    return win;
  }

  function renderDeliverablesWindow() {
    const w = document.getElementById("w-files");
    if (!w || !RT.live) return;
    const items = deliverableEntries().slice(0, 16);
    body(w).innerHTML = items.length
      ? `<div class="lab">deliverables</div>${items.map((item, index) => buildOutreachRow(
          item.title,
          item.detail || compactPath(item.path),
          "live",
          { type: "deliverable", index, label: deliverableActionLabel(item) || "open" },
        )).join("")}`
      : `<div class="lab">deliverables</div><div class="meta">No deliverables yet. When the CEO ships files, receipts, or site changes, they show up here.</div>`;
    body(w).querySelectorAll("[data-action='deliverable']").forEach((el) => {
      const index = Number(el.getAttribute("data-action-index") || 0);
      const run = () => {
        const item = items[index];
        if (!item || !item.path) return;
        void openDocument(item.path, item.title || "Deliverable");
      };
      el.addEventListener("click", run);
      el.addEventListener("keydown", (event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          run();
        }
      });
    });
  }

  async function refreshOperatorShellData() {
    const settled = await Promise.allSettled([
      fetchJSON("/api/takyon/operator/businesses"),
      fetchJSON("/api/takyon/operator/account"),
    ]);
    if (settled[0].status === "fulfilled") {
      const res = settled[0].value;
      rememberBusinesses(Array.isArray(res && res.businesses) ? res.businesses : []);
    }
    if (settled[1].status === "fulfilled") {
      LIVE.operatorAccount = settled[1].value;
      RT.credits = dollarFromAccount();
      updateMenu();
    }
    if (document.getElementById("w-operator")) renderOperatorWindow();
    if (document.getElementById("w-wallet")) renderWalletWindow();
  }

  function openOperatorWindow() {
    const win = makeWin({
      id: "w-operator",
      title: "operator · businesses",
      x: 120,
      y: 76,
      w: 420,
      h: 470,
      html: "",
    });
    renderOperatorWindow();
    focusWin(win);
    void refreshOperatorShellData().then(() => renderOperatorWindow()).catch(() => {
      renderOperatorWindow();
    });
    return win;
  }

  function renderOperatorWindow() {
    const w = document.getElementById("w-operator");
    if (!w) return;
    const businessButtons = LIVE.businesses.length
      ? LIVE.businesses.map((item) => {
          const slug = String(item && item.slug || "").trim().toLowerCase();
          const active = slug && slug === LIVE.activeBusiness;
          return `<button class="cbtn${active ? " go on" : ""}" data-biz="${esc(slug)}" type="button">${esc(item && (item.name || item.slug) || "business")}</button>`;
        }).join("")
      : `<span class="meta">No businesses yet.</span>`;
    body(w).innerHTML = `
      <div class="lab">businesses</div>
      <div style="display:flex;flex-wrap:wrap;gap:8px;margin-bottom:10px">${businessButtons}</div>
      <div style="display:flex;gap:8px;margin-bottom:14px">
        <button class="cbtn" id="operator-home" type="button">show intake</button>
        <button class="cbtn" id="operator-refresh" type="button">refresh</button>
      </div>

      <div class="lab">create business</div>
      <label class="meta" for="operator-create-name" style="display:block;margin-bottom:4px">name</label>
      <input id="operator-create-name" type="text" placeholder="Optional name" style="width:100%;box-sizing:border-box;border:1.5px solid var(--ink);background:#fff;padding:8px 10px;font:12px/1.4 'Space Mono',monospace;margin-bottom:8px" />
      <label class="meta" for="operator-create-goal" style="display:block;margin-bottom:4px">idea</label>
      <textarea id="operator-create-goal" placeholder="Describe the company you want to build…" style="width:100%;min-height:82px;box-sizing:border-box;border:1.5px solid var(--ink);background:#fff;padding:8px 10px;font:12px/1.45 'Space Mono',monospace;margin-bottom:8px;resize:vertical"></textarea>
      <div style="display:flex;gap:8px;align-items:center;margin-bottom:6px">
        <select id="operator-create-mode" style="border:1.5px solid var(--ink);background:#fff;padding:7px 9px;font:12px/1.4 'Space Mono',monospace">
          <option value="test">test mode</option>
          <option value="live">live mode</option>
        </select>
        <button class="cbtn go" id="operator-create" type="button" style="flex:1">create</button>
      </div>
      <div class="meta" id="operator-create-error" style="min-height:16px;margin-bottom:14px"></div>
      <div class="meta">Operator funding lives in the wallet rail. Business switching and creation stay here.</div>
    `;

    body(w).querySelectorAll("[data-biz]").forEach((button) => {
      button.addEventListener("click", () => {
        const slug = button.getAttribute("data-biz") || "";
        if (!slug) return;
        void mountLiveBusiness(slug);
      });
    });
    $("#operator-home", w).addEventListener("click", () => {
      teardownLive();
      if (typeof reset === "function") {
        reset();
      }
      window.setTimeout(() => {
        renderLauncherBusinesses();
        renderWalletRail();
      }, 0);
    });
    $("#operator-refresh", w).addEventListener("click", () => {
      void refreshOperatorShellData().then(() => renderOperatorWindow());
    });
    $("#operator-create", w).addEventListener("click", () => {
      const goal = String($("#operator-create-goal", w).value || "").trim();
      const name = String($("#operator-create-name", w).value || "").trim();
      const mode = String($("#operator-create-mode", w).value || "test").trim().toLowerCase();
      const errorEl = $("#operator-create-error", w);
      errorEl.textContent = "";
      if (!goal) {
        errorEl.textContent = "Enter a company idea.";
        return;
      }
      void createLiveBusinessWithOptions({ goal, name, mode, errorEl });
    });
  }

  function openWalletWindow() {
    const win = makeWin({
      id: "w-wallet",
      title: "wallet · operator + business funds",
      x: 552,
      y: 72,
      w: 384,
      h: 498,
      html: "",
    });
    renderWalletWindow();
    focusWin(win);
    void refreshOperatorShellData().then(() => renderWalletWindow()).catch(() => {
      renderWalletWindow();
    });
    return win;
  }

  function renderWalletWindow() {
    const w = document.getElementById("w-wallet");
    if (!w) return;
    const account = LIVE.operatorAccount;
    const spendableCents = operatorSpendableCents(account);
    const payoutStatus = account && account.available ? String(account.stripe_connect_status || "none") : "none";
    const payoutButtonLabel = payoutStatus === "active" ? "Open payouts" : "Connect payouts";
    const creativeAvailable = !!(LIVE.creativeCredits && LIVE.creativeCredits.available);
    const creativeBalance = creativeAvailable ? wholeCredits(LIVE.creativeCredits.balance_credits) : null;
    const creativeReserved = creativeAvailable ? wholeCredits(LIVE.creativeCredits.reserved_credits) : null;
    const overview = LIVE.workspaceOverview || {};
    const budget = overview.budget || {};
    const active = LIVE.activeBusiness ? businessSummary(LIVE.activeBusiness) : null;
    const activeName = String(active && (active.name || active.slug) || LIVE.activeBusiness || "").trim();
    body(w).innerHTML = `
      <div class="wallet-stack">
        <div class="chan">
          <div class="chan-top"><span class="chan-nm">Operator balance</span><span class="chan-st live">wallet</span></div>
          <div class="big-wake" style="font-size:30px">${spendableCents === null ? "—" : formatBudgetCents(spendableCents)}</div>
          <div class="wallet-note">${!account
            ? "Loading operator budget."
            : !account.available
              ? "Per-user budget unavailable."
              : "Spendful turns and wakes use this top-level operator budget."}</div>
          <div class="stat-row"><span class="k">included remaining</span><span class="v">${account && account.available ? formatBudgetCents(account.allowance_remaining_cents) : "—"}</span></div>
          <div class="stat-row"><span class="k">added funds</span><span class="v">${account && account.available ? formatBudgetCents(account.topup_balance_cents) : "—"}</span></div>
          <div class="stat-row"><span class="k">reserved</span><span class="v">${account && account.available ? formatBudgetCents(account.reserved_cents) : "—"}</span></div>
          <div class="wallet-actions">
            <input id="wallet-topup-amount" inputmode="decimal" placeholder="25" type="text" />
            <button class="cbtn go" id="wallet-topup" type="button">Add funds</button>
          </div>
          <div class="wallet-note" id="wallet-billing-error"></div>
        </div>

        <div class="chan">
          <div class="chan-top"><span class="chan-nm">Creative credits</span><span class="chan-st${activeName ? " live" : ""}">${activeName ? "scoped" : "idle"}</span></div>
          <div class="stat-row"><span class="k">balance</span><span class="v">${creativeBalance === null ? "—" : String(creativeBalance)}</span></div>
          <div class="stat-row"><span class="k">reserved</span><span class="v">${creativeReserved === null ? "—" : String(creativeReserved)}</span></div>
          <div class="wallet-note">${activeName
            ? `<span class="wallet-business">${esc(activeName)}</span> buys fixed-price creative actions.`
            : "Select a business to manage creative credits."}</div>
          <div class="wallet-inline">
            <button class="cbtn" id="wallet-buy-credits" type="button"${activeName ? "" : " disabled"}>Buy credits</button>
            <span class="wallet-note" id="wallet-business-error"></span>
          </div>
        </div>

        <div class="chan">
          <div class="chan-top"><span class="chan-nm">Payouts</span><span class="chan-st${payoutStatus === "active" ? " live" : ""}">${esc(payoutStatus)}</span></div>
          <div class="stat-row"><span class="k">owed</span><span class="v">${account && account.available ? formatBudgetCents(account.owed_balance_cents) : "—"}</span></div>
          <div class="stat-row"><span class="k">connect status</span><span class="v">${account && account.available ? esc(payoutStatus) : "—"}</span></div>
          <div class="wallet-inline">
            <button class="cbtn" id="wallet-payouts" type="button">${esc(payoutButtonLabel)}</button>
          </div>
        </div>

        <div class="chan">
          <div class="chan-top"><span class="chan-nm">App subsidy</span><span class="chan-st${budget.app_status === "active" ? " live" : ""}">${esc(String(budget.app_status || "—"))}</span></div>
          <div class="stat-row"><span class="k">cap</span><span class="v">${microUsdToDollars(budget.app_limit_microusd)}</span></div>
          <div class="stat-row"><span class="k">spent</span><span class="v">${microUsdToDollars(budget.app_spent_microusd)}</span></div>
          <div class="stat-row"><span class="k">remaining</span><span class="v">${microUsdToDollars(budget.app_remaining_microusd)}</span></div>
          <div class="wallet-note">${activeName
            ? `<span class="wallet-business">${esc(activeName)}</span> uses this pool for customer usage funding.`
            : "Select a business to view its subsidy pool."}</div>
        </div>
      </div>
    `;

    $("#wallet-topup", w).addEventListener("click", () => {
      const amount = String($("#wallet-topup-amount", w).value || "").trim();
      void submitOperatorTopupFromWallet(amount);
    });
    $("#wallet-payouts", w).addEventListener("click", () => {
      void startOperatorPayoutConnectFromWallet();
    });
    const buyButton = $("#wallet-buy-credits", w);
    if (buyButton) {
      buyButton.addEventListener("click", () => {
        void startCreativeCreditsCheckoutFromWallet();
      });
    }
  }

  async function submitOperatorTopupFromWallet(rawAmount) {
    const w = document.getElementById("w-wallet");
    const errorEl = w ? $("#wallet-billing-error", w) : null;
    if (errorEl) errorEl.textContent = "";
    const dollars = Number.parseFloat(String(rawAmount || "").trim());
    const amountCents = Number.isFinite(dollars) ? Math.round(dollars * 100) : 0;
    if (amountCents <= 0) {
      if (errorEl) errorEl.textContent = "Enter a valid amount.";
      return;
    }
    try {
      const res = await fetchJSON("/api/takyon/operator/topup/checkout", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ amount_cents: amountCents, return_path: currentReturnPath() }),
      });
      if (!res || !res.checkout_url) throw new Error("Funding checkout unavailable.");
      navigateOwner(res.checkout_url);
    } catch (err) {
      if (errorEl) errorEl.textContent = err instanceof Error ? err.message : String(err);
    }
  }

  async function startCreativeCreditsCheckoutFromWallet(providedErrorEl) {
    const w = document.getElementById("w-wallet");
    const errorEl = providedErrorEl || (w ? $("#wallet-business-error", w) : null);
    if (errorEl) errorEl.textContent = "";
    const business = String(LIVE.activeBusiness || "").trim().toLowerCase();
    if (!business) {
      if (errorEl) errorEl.textContent = "Select a business to buy credits.";
      return;
    }
    try {
      const packs = await fetchJSON(`/api/takyon/businesses/${encodeURIComponent(business)}/creative-credits/packs`);
      const pack = Array.isArray(packs && packs.packs) ? packs.packs[0] : null;
      if (!pack || !pack.id) throw new Error("No creative credit packs are configured.");
      const res = await fetchJSON(`/api/takyon/businesses/${encodeURIComponent(business)}/creative-credits/checkout`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          pack_id: pack.id,
          success_path: currentReturnPath(),
          cancel_path: currentReturnPath(),
        }),
      });
      if (!res || !res.checkout_url) throw new Error("Creative credit checkout URL unavailable.");
      navigateOwner(res.checkout_url);
    } catch (err) {
      if (errorEl) errorEl.textContent = err instanceof Error ? err.message : String(err);
    }
  }

  async function startOperatorPayoutConnectFromWallet() {
    const w = document.getElementById("w-wallet");
    const errorEl = w ? $("#wallet-billing-error", w) : null;
    if (errorEl) errorEl.textContent = "";
    try {
      const res = await fetchJSON("/api/takyon/operator/payouts/connect", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ return_path: currentReturnPath() }),
      });
      if (!res || !res.connect_url) throw new Error("Payout connect URL unavailable.");
      navigateOwner(res.connect_url);
    } catch (err) {
      if (errorEl) errorEl.textContent = err instanceof Error ? err.message : String(err);
      await refreshOperatorShellData().catch(() => {
        /* best effort */
      });
      renderWalletWindow();
    }
  }

  function openWakeWindow() {
    if (!LIVE.activeBusiness) return null;
    const win = makeWin({
      id: "w-wake",
      title: "wake loop · cron",
      x: 556,
      y: 56,
      w: 360,
      h: 360,
      html: "",
    });
    renderWakeWindow();
    focusWin(win);
    return win;
  }

  function renderWakeWindow(errorMessage) {
    const w = document.getElementById("w-wake");
    if (!w || !RT.live) return;
    const overview = LIVE.workspaceOverview || {};
    const wakeHealth = overview.wake_health || {};
    const cron = selectWakeCron(overview);
    const currentValue = String((w.dataset.scheduleValue || (cron && cron.schedule) || "every 6h")).trim();
    const statusHeadline = String(wakeHealth.headline || ((cron && cron.enabled) ? "CEO wake loop is active." : "No CEO wake loop is configured.")).trim();
    const statusDetail = String(wakeHealth.detail || ((cron && cron.schedule)
      ? "Saving updates the recurring CEO wake cadence without triggering an immediate wake."
      : "Save a cadence to create the recurring CEO wake loop for this business.")).trim();
    body(w).innerHTML = `
      <div class="lab">ceo wake loop</div>
      <div style="display:grid;gap:8px;margin-bottom:14px">
        <div style="display:flex;justify-content:space-between;gap:12px"><span class="meta">status</span><strong style="font:700 12px 'Space Mono',monospace">${esc(statusHeadline)}</strong></div>
        <div style="display:flex;justify-content:space-between;gap:12px"><span class="meta">current cadence</span><strong style="font:700 12px 'Space Mono',monospace">${esc((cron && cron.schedule) || "Not scheduled")}</strong></div>
        <div style="display:flex;justify-content:space-between;gap:12px"><span class="meta">next run</span><strong style="font:700 12px 'Space Mono',monospace">${esc((cron && cron.next_run) || "Will be set when you save")}</strong></div>
        <div style="display:flex;justify-content:space-between;gap:12px"><span class="meta">last run</span><strong style="font:700 12px 'Space Mono',monospace">${esc((cron && cron.last_run) || "No wake recorded yet")}</strong></div>
      </div>
      <div style="display:flex;flex-wrap:wrap;gap:8px;margin-bottom:10px">
        ${WAKE_SCHEDULE_PRESETS.map((preset) => `<button class="cbtn${normalizeWakeSchedule(currentValue) === preset ? " go on" : ""}" data-wake-preset="${esc(preset)}" type="button">${esc(preset.replace(/^every\s+/i, ""))}</button>`).join("")}
      </div>
      <label class="meta" for="wake-schedule-input" style="display:block;margin-bottom:4px">custom interval</label>
      <input id="wake-schedule-input" type="text" value="${esc(currentValue)}" placeholder="every 6h" style="width:100%;box-sizing:border-box;border:1.5px solid var(--ink);background:#fff;padding:8px 10px;font:12px/1.4 'Space Mono',monospace;margin-bottom:8px" />
      <div class="meta" style="margin-bottom:4px">${esc(statusDetail)}</div>
      <div class="meta" style="margin-bottom:10px">Use an interval like every 30m, every 2h, or every 1d.</div>
      <div class="meta" id="wake-error" style="min-height:16px;margin-bottom:10px">${esc(errorMessage || "")}</div>
      <button class="cbtn go" id="wake-save" type="button" style="width:100%">save cron</button>
    `;
    body(w).querySelectorAll("[data-wake-preset]").forEach((button) => {
      button.addEventListener("click", () => {
        w.dataset.scheduleValue = button.getAttribute("data-wake-preset") || "every 6h";
        renderWakeWindow(errorMessage);
      });
    });
    $("#wake-schedule-input", w).addEventListener("input", (event) => {
      w.dataset.scheduleValue = event.target.value;
    });
    $("#wake-save", w).addEventListener("click", () => {
      void saveWakeScheduleFromWindow();
    });
  }

  async function saveWakeScheduleFromWindow() {
    const w = document.getElementById("w-wake");
    if (!w || !LIVE.activeBusiness) return;
    const raw = String($("#wake-schedule-input", w).value || w.dataset.scheduleValue || "").trim();
    const normalized = normalizeWakeSchedule(raw);
    if (!normalized) {
      renderWakeWindow("Use an interval like every 30m, every 2h, or every 1d.");
      return;
    }
    try {
      const sessionId = await ensureSession(LIVE.activeBusiness);
      const res = await rpc("takyon.wake.schedule", {
        session_id: sessionId,
        schedule: normalized,
      }, 30000);
      const output = String(res && res.output || "").trim();
      if (output) ceolog(esc(output), true);
      w.dataset.scheduleValue = normalized;
      await refreshBusinessData(LIVE.activeBusiness, {
        skipAccount: true,
        skipCredits: true,
        skipBoard: true,
        view: LIVE_WORKSPACE_VIEW,
      });
      renderWakeWindow("");
    } catch (err) {
      renderWakeWindow(err instanceof Error ? err.message : String(err));
    }
  }

  const originalRenderDeliverables = renderDeliverables;
  renderDeliverables = function renderDeliverablesLiveAware() {
    if (!RT.live) return originalRenderDeliverables();
    return renderDeliverablesWindow();
  };

  const originalRenderProduct = renderProduct;
  renderProduct = function renderProductLiveAware() {
    if (!RT.live) return originalRenderProduct();
    const w = document.getElementById("w-product");
    if (!w || !RT.biz) return;
    const overview = LIVE.workspaceOverview || {};
    const product = overview.product || {};
    const website = overview.artifacts && overview.artifacts.website || {};
    const currentAction = currentActionFromSnapshot(LIVE.workspaceSnapshot || { overview });
    const blocker = String(product.publish_blocker || website.publish_blocker || currentAction.blocker || "").trim();
    const currentStatus = String(currentAction.status || "").trim().toLowerCase();
    const live = String(product.publish_status || RT.biz.publishStatus || "").trim().toLowerCase() === "published" || !!RT.biz.publicUrl;
    const previewPath = previewPathForLive();
    const hasLocalPreview = !!previewPath;
    const directUrl = live ? normalizeOpenableUrl(RT.biz.publicUrl) : "";
    const hostLabel = live
      ? (prettyHost(RT.biz.publicUrl) || "live site")
      : hasLocalPreview
        ? compactPath(previewPath) || "local preview"
        : "product workspace";
    const hasSite = live || hasLocalPreview;
    const frameworkSummary = Array.isArray(product.detected_frameworks) && product.detected_frameworks.length
      ? product.detected_frameworks.join(", ")
      : "";
    const factLine = [
      product.publish_mode ? `publish ${String(product.publish_mode).replace(/_/g, " ")}` : "",
      frameworkSummary ? `framework ${frameworkSummary}` : "",
      product.latest_check_status ? `check ${String(product.latest_check_status).replace(/_/g, " ")}` : "",
    ].filter(Boolean).join(" · ");
    const commandLine = String(product.latest_check_command || "").trim();
    const errorLine = String(product.latest_check_error || "").trim();
    const summaryTitle = currentAction.label
      || (live ? "Public site is live." : hasLocalPreview ? "Preview is ready." : "Product is still taking shape.");
    const summaryDetail = blocker || currentAction.detail || "";
    const summaryTone = blocker || currentStatus === "blocked"
      ? "var(--alert)"
      : currentStatus === "running"
        ? "var(--amber)"
        : currentStatus === "scheduled"
          ? "var(--blue)"
          : "var(--green)";
    const summaryBadge = blocker
      ? "blocked"
      : currentStatus === "running" || currentStatus === "scheduled"
        ? currentStatus
        : live
          ? "published"
          : hasLocalPreview
            ? "preview"
            : "waiting";
    const summaryPanel = `
      <div style="padding:10px 12px;border-bottom:2px solid var(--ink);background:var(--paper)">
        <div class="chan-top">
          <span class="chan-nm">${esc(summaryTitle)}</span>
          <span class="chan-st${summaryBadge === "published" ? " live" : ""}" style="border-color:${summaryTone};color:${summaryTone}">${esc(summaryBadge)}</span>
        </div>
        ${summaryDetail ? `<div class="meta" style="${blocker ? "color:var(--alert);" : ""}margin-top:6px">${esc(summaryDetail)}</div>` : ""}
        ${factLine ? `<div class="meta" style="margin-top:6px">${esc(factLine)}</div>` : ""}
        ${commandLine ? `<div class="meta" style="margin-top:6px"><strong>command</strong> ${esc(commandLine)}</div>` : ""}
        ${errorLine && errorLine !== summaryDetail ? `<div class="meta" style="margin-top:6px;color:var(--alert)">${esc(errorLine)}</div>` : ""}
      </div>`;
    // Avoid rebuilding (and reloading the iframe) when nothing material changed.
    const sig = JSON.stringify([
      live,
      directUrl,
      hasLocalPreview,
      previewPath,
      hostLabel,
      summaryTitle,
      summaryDetail,
      factLine,
      commandLine,
      errorLine,
      summaryBadge,
    ]);
    if (w.dataset.productSig === sig) return;
    if (typeof w._productPreviewCleanup === "function") {
      try { w._productPreviewCleanup(); } catch (_) {}
      delete w._productPreviewCleanup;
    }
    w.dataset.productSig = sig;
    const frameTitle = `${RT.biz.name || RT.biz.slug || "product"} preview`;
    body(w).innerHTML = `<div class="mini">
      <div class="mini__bar"${hasSite ? ' id="product-bar" title="open the site in a new tab" style="cursor:pointer"' : ""}><i></i><i></i><i></i><span>${esc(hostLabel)}</span>${hasSite ? '<span style="margin-left:auto;font-size:9px;letter-spacing:.1em;opacity:.7">↗ open</span>' : ""}</div>
      ${summaryPanel}
      ${hasSite
        ? `<div class="mini__viewport" id="product-viewport"><iframe class="mini__frame mini__frame--scaled" id="product-frame" title="${esc(frameTitle)}"${directUrl ? ` src="${esc(directUrl)}"` : ""}></iframe></div>`
        : `<div class="mini__page"><div class="lab">product workspace</div><p class="mini__sub">${esc(RT.biz.idea || "Takyon business workspace")}</p><div class="meta">${esc(blocker || currentAction.detail || "Takyon is still bootstrapping the product surface.")}</div></div>`}
    </div>`;
    const viewport = body(w).querySelector("#product-viewport");
    const frame = body(w).querySelector("#product-frame");
    if (viewport && frame) {
      const previewWidth = 1280;
      const previewHeight = 820;
      const applyScale = () => {
        const width = viewport.clientWidth || 1;
        const height = viewport.clientHeight || 1;
        const scale = Math.max(0.12, Math.min(width / previewWidth, height / previewHeight));
        frame.style.transform = `scale(${scale})`;
      };
      let resizeObserver = null;
      if (typeof ResizeObserver === "function") {
        resizeObserver = new ResizeObserver(() => applyScale());
        resizeObserver.observe(viewport);
      } else {
        window.addEventListener("resize", applyScale);
      }
      frame.addEventListener("load", applyScale);
      applyScale();
      w._productPreviewCleanup = () => {
        if (resizeObserver) resizeObserver.disconnect();
        else window.removeEventListener("resize", applyScale);
      };
    }
    const bar = body(w).querySelector("#product-bar");
    if (bar) {
      bar.addEventListener("click", () => {
        const activeFrame = document.getElementById("product-frame");
        const target = directUrl || (activeFrame && activeFrame.getAttribute("src")) || "";
        if (target) openUrlInNewTab(target);
        else if (previewPath) void openSitePreview(previewPath, frameTitle);
      });
    }
    if (hasSite && !directUrl && previewPath) {
      const business = String(LIVE.activeBusiness || "").trim().toLowerCase();
      if (business) {
        fetchJSON(`/api/takyon/businesses/${encodeURIComponent(business)}/site-preview?path=${encodeURIComponent(previewPath)}`)
          .then((res) => {
            const url = normalizeOpenableUrl(res && res.url);
            const frame = document.getElementById("product-frame");
            if (url && frame && frame.getAttribute("src") !== url) frame.src = url;
          })
          .catch(() => {});
      }
    }
  };

  function outreachChannelLabel(key) {
    if (key === "x") return "X";
    if (key === "reddit") return "Reddit";
    if (key === "meta") return "Meta";
    return String(key || "Channel").trim();
  }

  function outreachStatusLabel(status) {
    const value = String(status || "").trim().toLowerCase();
    if (!value || value === "missing") return "idle";
    if (value === "published_local" || value === "suppressed_test_mode") return "local preview";
    if (value === "draft_only") return "draft ready";
    if (value === "created_paused") return "paused";
    if (value === "ready_for_manual_launch") return "manual handoff";
    if (value === "externally_launched") return "live";
    if (value === "queued") return "queued";
    return value.replace(/_/g, " ");
  }

  function normalizeOutreachChannel(key, raw) {
    const channel = raw && typeof raw === "object" ? raw : {};
    return {
      key,
      label: String(channel.label || outreachChannelLabel(key)).trim(),
      status: String(channel.status || "missing").trim() || "missing",
      updatedAt: String(channel.updated_at || "").trim(),
      draftPath: String(channel.draft_path || "").trim(),
      items: Array.isArray(channel.items) ? channel.items : [],
      campaigns: Array.isArray(channel.campaigns) ? channel.campaigns : [],
      latestJob: channel.latest_job && typeof channel.latest_job === "object" ? channel.latest_job : null,
      publishedCount: Number(channel.published_count || 0),
      campaignCount: Number(channel.campaign_count || 0),
      metricsCount: Number(channel.metrics_count || 0),
    };
  }

  function outreachChannels() {
    const overview = LIVE.workspaceOverview || {};
    const outreach = overview.artifacts && overview.artifacts.outreach || {};
    const channels = outreach.channels && typeof outreach.channels === "object" ? outreach.channels : {};
    return {
      x: normalizeOutreachChannel("x", channels.x),
      reddit: normalizeOutreachChannel("reddit", channels.reddit),
      meta: normalizeOutreachChannel("meta", channels.meta),
    };
  }

  function outreachMetricsSummary(metrics) {
    const data = metrics && typeof metrics === "object" ? metrics : {};
    const bits = [];
    const impressions = Number(data.impressions);
    const clicks = Number(data.clicks);
    const spendUsd = Number(data.spend_usd);
    const ctr = Number(data.ctr);
    if (Number.isFinite(impressions) && impressions > 0) bits.push(`${formatMetricCount(impressions)} impressions`);
    if (Number.isFinite(clicks) && clicks > 0) bits.push(`${formatMetricCount(clicks)} clicks`);
    if (Number.isFinite(spendUsd) && spendUsd > 0) bits.push(`$${spendUsd.toFixed(2)} spend`);
    if (Number.isFinite(ctr) && ctr > 0) bits.push(`${ctr.toFixed(2)}% CTR`);
    return bits.join(" · ");
  }

  function outreachChannelSummary(channel) {
    if (!channel) return "No outreach lane selected.";
    if (channel.key === "x") {
      if (channel.items.length > 0) {
        return `${formatMetricCount(channel.items.length)} recorded post${channel.items.length === 1 ? "" : "s"}.`;
      }
      if (channel.draftPath) return `Draft ready in ${compactPath(channel.draftPath)}.`;
      if (channel.latestJob) return `${outreachStatusLabel(channel.latestJob.status)} · ${channel.latestJob.label || "channel work recorded"}`;
      return "No X output recorded yet.";
    }
    if (channel.campaigns.length > 0) {
      const latest = channel.campaigns[0] || {};
      const metricSummary = outreachMetricsSummary(latest.latest_metrics);
      const budget = Number(latest.actual_daily_budget_usd || latest.daily_budget_usd);
      const bits = [`${formatMetricCount(channel.campaigns.length)} campaign${channel.campaigns.length === 1 ? "" : "s"} recorded`];
      if (Number.isFinite(budget) && budget > 0) bits.push(`$${budget}/day`);
      if (metricSummary) bits.push(metricSummary);
      return bits.join(" · ");
    }
    if (channel.latestJob) return `${outreachStatusLabel(channel.latestJob.status)} · ${channel.latestJob.label || "channel work recorded"}`;
    return `No ${channel.label} campaigns recorded yet.`;
  }

  async function startOutreachChannel(channelKey, errorEl) {
    const business = String(LIVE.activeBusiness || "").trim().toLowerCase();
    if (errorEl) errorEl.textContent = "";
    if (!business) {
      if (errorEl) errorEl.textContent = "Select a business first.";
      return;
    }
    try {
      const res = await fetchJSON(`/api/takyon/businesses/${encodeURIComponent(business)}/outreach/start`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ channel: channelKey }),
      });
      if (!res || res.success === false) throw new Error("Channel start request failed.");
      if (errorEl) errorEl.textContent = `${outreachChannelLabel(channelKey)} queued.`;
      await refreshBusinessData(business, {
        skipAccount: true,
        skipCredits: true,
        skipBoard: true,
        view: LIVE_WORKSPACE_VIEW,
      });
    } catch (err) {
      if (errorEl) errorEl.textContent = err instanceof Error ? err.message : String(err);
    }
  }

  function isPausedOutreachStatus(status) {
    const value = String(status || "").trim().toLowerCase();
    return value === "created_paused" || value === "paused";
  }

  async function startOutreachChannelGated(channelKey, errorEl) {
    const credits = LIVE.creativeCredits;
    const railAvailable = !!(credits && credits.available);
    const balance = railAvailable ? wholeCredits(credits.balance_credits) : 0;
    if (railAvailable && balance <= 0) {
      openAddCreditsPrompt();
      return;
    }
    await startOutreachChannel(channelKey, errorEl);
  }

  function openAddCreditsPrompt() {
    const credits = LIVE.creativeCredits;
    const balance = credits && credits.available ? wholeCredits(credits.balance_credits) : 0;
    const win = makeWin({ id: "w-add-credits", title: "add credits", x: 240, y: 150, w: 264, h: 172, html: "" });
    body(win).innerHTML = `
      <div class="big-wake" style="font-size:26px">${balance} credit${balance === 1 ? "" : "s"}</div>
      <div class="wallet-inline" style="margin-top:14px">
        <button class="cbtn go" id="add-credits-go" type="button">add credits</button>
        <button class="cbtn" id="add-credits-cancel" type="button">cancel</button>
      </div>
      <span class="wallet-note" id="add-credits-error" style="display:block;margin-top:8px"></span>
    `;
    const errorEl = body(win).querySelector("#add-credits-error");
    const goBtn = body(win).querySelector("#add-credits-go");
    const cancelBtn = body(win).querySelector("#add-credits-cancel");
    if (goBtn) goBtn.addEventListener("click", () => { void startCreativeCreditsCheckoutFromWallet(errorEl); });
    if (cancelBtn) cancelBtn.addEventListener("click", () => closeWin(win));
    focusWin(win);
    return win;
  }

  function renderOutreachJobCard(job) {
    if (!job) return "";
    return `<div class="chan">
      <div class="chan-top"><span class="chan-nm">Latest request</span><span class="chan-st">${esc(outreachStatusLabel(job.status))}</span></div>
      <div class="meta" style="margin-top:6px">${esc(String(job.label || job.kind || "Queued channel work").trim())}</div>
      ${job.detail ? `<div class="meta" style="margin-top:6px;opacity:.7">${esc(String(job.detail || "").trim())}</div>` : ""}
    </div>`;
  }

  function renderXDetailCard(item, index) {
    const mode = String(item && item.mode || "").trim().toLowerCase();
    const status = String(item && item.status || "").trim();
    const url = normalizeOpenableUrl(item && item.url);
    const artifactPath = String(item && item.artifact_path || "").trim();
    const conversationFile = String(item && item.conversation_file || "").trim();
    return `<div class="chan">
      <div class="chan-top"><span class="chan-nm">${esc(String(item && item.title || "X post").trim() || "X post")}</span><span class="chan-st${mode === "live" ? " live" : ""}">${esc(outreachStatusLabel(status || mode || "published_local"))}</span></div>
      <div class="meta" style="margin-top:6px">${esc(url ? "Published externally." : artifactPath ? "Local preview recorded." : "Thread mirrored in Takyon.")}</div>
      <div class="wallet-inline" style="margin-top:8px">
        ${url ? `<button class="cbtn" data-x-open="${index}" type="button">open</button>` : ""}
        ${artifactPath ? `<button class="cbtn" data-x-preview="${index}" type="button">preview</button>` : ""}
        ${conversationFile ? `<button class="cbtn" data-x-thread="${index}" type="button">thread</button>` : ""}
      </div>
    </div>`;
  }

  function renderCampaignDetailCard(channelKey, campaign, index) {
    const metricSummary = outreachMetricsSummary(campaign && campaign.latest_metrics);
    const budget = Number(campaign && (campaign.actual_daily_budget_usd || campaign.daily_budget_usd));
    const metaBits = [
      outreachStatusLabel(campaign && campaign.status),
      Number.isFinite(budget) && budget > 0 ? `$${budget}/day` : "",
      metricSummary,
    ].filter(Boolean);
    return `<div class="chan">
      <div class="chan-top"><span class="chan-nm">${esc(String(campaign && (campaign.campaign_name || campaign.slug) || `${outreachChannelLabel(channelKey)} campaign`).trim())}</span><span class="chan-st${String(campaign && campaign.status || "").trim() && String(campaign && campaign.status || "").trim() !== "missing" ? " live" : ""}">${esc(outreachStatusLabel(campaign && campaign.status))}</span></div>
      <div class="meta" style="margin-top:6px">${esc(metaBits.join(" · ") || "Campaign recorded in Takyon.")}</div>
      <div class="wallet-inline" style="margin-top:8px">
        ${campaign && campaign.open_url ? `<button class="cbtn" data-campaign-open="${index}" type="button">open</button>` : ""}
        ${campaign && campaign.asset_path ? `<button class="cbtn" data-campaign-asset="${index}" type="button">asset</button>` : ""}
        ${campaign && campaign.plan_path ? `<button class="cbtn" data-campaign-plan="${index}" type="button">plan</button>` : ""}
        ${campaign && campaign.receipt_path ? `<button class="cbtn" data-campaign-receipt="${index}" type="button">receipt</button>` : ""}
      </div>
    </div>`;
  }

  function renderOutreachChannelWindow(channelKey) {
    const w = document.getElementById("w-outreach-channel");
    if (!w) return;
    const channels = outreachChannels();
    const currentKey = String(channelKey || w.dataset.channel || "x").trim().toLowerCase();
    const channel = channels[currentKey] || channels.x;
    w.dataset.channel = channel.key;
    const titleEl = w.querySelector(".win__title");
    if (titleEl) titleEl.textContent = `${channel.label} · outreach`;
    const detailCards = channel.key === "x"
      ? channel.items.map((item, index) => renderXDetailCard(item, index)).join("")
      : channel.campaigns.map((campaign, index) => renderCampaignDetailCard(channel.key, campaign, index)).join("");
    const emptyState = channel.key === "x"
      ? (channel.draftPath
        ? `<div class="chan"><div class="chan-top"><span class="chan-nm">Draft</span><span class="chan-st">ready</span></div><div class="meta" style="margin-top:6px">${esc(compactPath(channel.draftPath))}</div><div class="wallet-inline" style="margin-top:8px"><button class="cbtn" data-x-draft type="button">open draft</button></div></div>`
        : `<div class="meta">No X posts or drafts are recorded yet.</div>`)
      : `<div class="meta">No ${esc(channel.label)} campaigns are recorded yet.</div>`;
    body(w).innerHTML = `
      <div class="lab">${esc(channel.label)} lane</div>
      <div class="meta" style="margin:6px 0 11px">${esc(outreachChannelSummary(channel))}</div>
      <div class="wallet-inline" style="margin-bottom:12px">
        <button class="cbtn go" data-channel-start="${esc(channel.key)}" type="button">start</button>
        <span class="wallet-note" id="outreach-channel-error"></span>
      </div>
      ${renderOutreachJobCard(channel.latestJob)}
      ${detailCards || emptyState}
    `;
    const errorEl = body(w).querySelector("#outreach-channel-error");
    body(w).querySelectorAll("[data-channel-start]").forEach((btn) => {
      btn.addEventListener("click", (event) => {
        event.preventDefault();
        void startOutreachChannelGated(channel.key, errorEl);
      });
    });
    if (channel.key === "x") {
      body(w).querySelectorAll("[data-x-open]").forEach((btn) => {
        btn.addEventListener("click", () => {
          const item = channel.items[Number(btn.getAttribute("data-x-open") || 0)];
          const target = normalizeOpenableUrl(item && item.url);
          if (target) openUrlInNewTab(target);
        });
      });
      body(w).querySelectorAll("[data-x-preview]").forEach((btn) => {
        btn.addEventListener("click", () => {
          const item = channel.items[Number(btn.getAttribute("data-x-preview") || 0)];
          if (item && item.artifact_path) void openDocument(item.artifact_path, item.title || "X post");
        });
      });
      body(w).querySelectorAll("[data-x-thread]").forEach((btn) => {
        btn.addEventListener("click", () => {
          const item = channel.items[Number(btn.getAttribute("data-x-thread") || 0)];
          if (item && item.conversation_file) void openDocument(item.conversation_file, item.title || "Thread");
        });
      });
      const draftBtn = body(w).querySelector("[data-x-draft]");
      if (draftBtn && channel.draftPath) {
        draftBtn.addEventListener("click", () => {
          void openDocument(channel.draftPath, `${channel.label} draft`);
        });
      }
      return;
    }
    body(w).querySelectorAll("[data-campaign-open]").forEach((btn) => {
      btn.addEventListener("click", () => {
        const campaign = channel.campaigns[Number(btn.getAttribute("data-campaign-open") || 0)];
        const target = normalizeOpenableUrl(campaign && campaign.open_url);
        if (target) openUrlInNewTab(target);
      });
    });
    body(w).querySelectorAll("[data-campaign-asset]").forEach((btn) => {
      btn.addEventListener("click", () => {
        const campaign = channel.campaigns[Number(btn.getAttribute("data-campaign-asset") || 0)];
        if (campaign && campaign.asset_path) void openDocument(campaign.asset_path, campaign.campaign_name || "Campaign asset");
      });
    });
    body(w).querySelectorAll("[data-campaign-plan]").forEach((btn) => {
      btn.addEventListener("click", () => {
        const campaign = channel.campaigns[Number(btn.getAttribute("data-campaign-plan") || 0)];
        if (campaign && campaign.plan_path) void openDocument(campaign.plan_path, campaign.campaign_name || "Campaign plan");
      });
    });
    body(w).querySelectorAll("[data-campaign-receipt]").forEach((btn) => {
      btn.addEventListener("click", () => {
        const campaign = channel.campaigns[Number(btn.getAttribute("data-campaign-receipt") || 0)];
        if (campaign && campaign.receipt_path) void openDocument(campaign.receipt_path, campaign.campaign_name || "Campaign receipt");
      });
    });
  }

  function openOutreachChannel(channelKey) {
    const win = makeWin({
      id: "w-outreach-channel",
      title: `${outreachChannelLabel(channelKey)} · outreach`,
      x: 148,
      y: 92,
      w: 540,
      h: 420,
      html: "",
    });
    win.dataset.channel = String(channelKey || "x").trim().toLowerCase();
    renderOutreachChannelWindow(win.dataset.channel);
    focusWin(win);
    return win;
  }

  const originalRenderOutreach = renderOutreach;
  renderOutreach = function renderOutreachLiveAware() {
    if (!RT.live) return originalRenderOutreach();
    const w = document.getElementById("w-status");
    if (!w) return;
    const channels = outreachChannels();
    body(w).innerHTML = `
      ${["x", "reddit", "meta"].map((key) => {
        const channel = channels[key];
        const status = String(channel.status || "missing");
        const paused = isPausedOutreachStatus(status);
        const stClass = paused ? " alert" : (status && status !== "missing" ? " live" : "");
        return `<div class="chan${paused ? " paused" : ""}" data-channel-view="${key}" tabindex="0" role="button" style="cursor:pointer">
          <div class="chan-top"><span class="chan-nm">${esc(channel.label)}</span><span class="chan-st${stClass}">${esc(outreachStatusLabel(status))}</span></div>
          <div class="meta" style="margin-top:6px">${esc(outreachChannelSummary(channel))}</div>
          <div class="wallet-inline" style="margin-top:8px">
            <button class="cbtn go" data-channel-start="${key}" type="button">start</button>
          </div>
        </div>`;
      }).join("")}
      <span class="wallet-note" id="outreach-panel-error" style="display:block;margin-top:2px"></span>
    `;
    const errorEl = body(w).querySelector("#outreach-panel-error");
    body(w).querySelectorAll("[data-channel-view]").forEach((el) => {
      const channelKey = String(el.getAttribute("data-channel-view") || "").trim().toLowerCase();
      const run = () => openOutreachChannel(channelKey);
      el.addEventListener("click", run);
      el.addEventListener("keydown", (event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          run();
        }
      });
    });
    body(w).querySelectorAll("[data-channel-start]").forEach((btn) => {
      const channelKey = String(btn.getAttribute("data-channel-start") || "").trim().toLowerCase();
      btn.addEventListener("click", (event) => {
        event.preventDefault();
        event.stopPropagation();
        void startOutreachChannelGated(channelKey, errorEl);
      });
    });
    if (document.getElementById("w-outreach-channel")) renderOutreachChannelWindow();
  };

  const originalUpdateMenu = updateMenu;
  updateMenu = function updateMenuLiveAware() {
    if (!RT.live) {
      originalUpdateMenu();
      renderTopRail();
      return;
    }
    renderTopRail();
  };

  const originalRenderBoard = renderBoard;
  renderBoard = function renderBoardLiveAware() {
    if (!RT.live) return originalRenderBoard();
    const w = document.getElementById("w-board");
    if (!w) return;
    const cols = ["running", "blocked", "scheduled", "done"];
    body(w).innerHTML = `<div class="board-shell">
      <div class="board-cols"><div class="kanban">${cols.map((st) => {
      const items = RT.tasks.filter((t) => t.status === st);
      return `<div class="col"><div class="col-h" style="border-color:${STATUS_C[st] || "var(--ink)"}"><span>${st}</span><span class="ct">${items.length}</span></div>
        <div class="col-list">${items.map(cardHTML).join("")}</div></div>`;
    }).join("")}</div></div></div>`;
    body(w).querySelectorAll(".card").forEach((c) => {
      c.setAttribute("role", "button");
      c.tabIndex = 0;
      c.addEventListener("click", () => openTask(c.dataset.id));
      c.addEventListener("keydown", (e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          openTask(c.dataset.id);
        }
      });
    });
    renderGraphWindow();
  };

  const originalLayoutMain = layoutMain;
  layoutMain = function layoutMainLiveAware() {
    originalLayoutMain();
  };

  if (typeof openGraph === "function") {
    const originalOpenGraphFn = openGraph;
    openGraph = function openGraphLiveAware() {
      const win = originalOpenGraphFn();
      if (RT.live) renderGraphWindow();
      return win;
    };
    try {
      if (typeof OPENERS === "object" && OPENERS) OPENERS["w-graph"] = openGraph;
    } catch (_err) {
      /* OPENERS not reachable here; mountLiveBusiness still opens the graph */
    }
  }

  const originalOpenTask = openTask;
  openTask = async function openTaskLiveAware(id) {
    const task = RT.tasks.find((item) => item.id === id);
    if (!RT.live) {
      originalOpenTask(id);
      return;
    }
    if (task && task._deliverablePath) {
      void openDocument(task._deliverablePath, task.title || "Deliverable");
      return;
    }
    originalOpenTask(id);
    if (!task || task._detailLoaded || task._loadingDetail) return;
    task._loadingDetail = true;
    try {
      const detail = await fetchJSON(`/api/plugins/kanban/tasks/${encodeURIComponent(id)}?board=${encodeURIComponent(LIVE.activeBusiness)}`);
      const payload = detail && detail.task || {};
      task.body = payload.body || task.body;
      task.result = payload.result || payload.latest_summary || task.result;
      task.block_reason = payload.last_failure_error || task.block_reason;
      task.progress = progressForTask(payload);
      task.comments = Array.isArray(detail && detail.comments)
        ? detail.comments.map((comment) => ({
            author: comment.author || "system",
            body: comment.body || "",
          }))
        : [];
      task.events = Array.isArray(detail && detail.events)
        ? detail.events.map((event) => ({
            kind: event.kind || "event",
            note: event.payload && typeof event.payload === "object"
              ? JSON.stringify(event.payload)
              : event.payload
                ? String(event.payload)
                : "",
          }))
        : [];
      task.runs = Array.isArray(detail && detail.runs)
        ? detail.runs.map((run) => ({
            profile: run.profile || task.assignee,
            status: run.status || run.outcome || "done",
            summary: run.summary || run.error || "",
          }))
        : [];
      task._detailLoaded = true;
      refreshTask(task);
    } catch (_err) {
      /* keep the lightweight card state if detail hydration fails */
    } finally {
      task._loadingDetail = false;
    }
  };

  function resetLiveState() {
    LIVE.toolEls.clear();
    LIVE.assistantBubble = null;
    LIVE.assistantText = "";
    LIVE.bootedBusiness = "";
    LIVE.planBusiness = "";
    RT.live = false;
    LIVE.workspaceOverview = null;
    LIVE.workspaceSnapshot = null;
    LIVE.workspaceOutputs = [];
    LIVE.creativeCredits = null;
    LIVE.liveTrace = new Map();
    LIVE.activeTurnTraceId = "";
    LIVE.traceLogSeen = new Set();
    LIVE.lastOverviewTaskSignature = "";
    LIVE.lastBackgroundDetail = "";
    LIVE.lastCeoHeadline = "";
    LIVE.pollMs = 0;
    LIVE.historyRunning = false;
    LIVE.historySeen = new Set();
    LIVE.historyPollMs = 0;
  }

  function stopLiveTimers() {
    if (LIVE.menuTimer) window.clearInterval(LIVE.menuTimer);
    if (LIVE.pollTimer) window.clearInterval(LIVE.pollTimer);
    if (LIVE.historyPollTimer) window.clearInterval(LIVE.historyPollTimer);
    if (LIVE.reconnectTimer) window.clearTimeout(LIVE.reconnectTimer);
    if (LIVE.refreshTimer) window.clearTimeout(LIVE.refreshTimer);
    LIVE.menuTimer = null;
    LIVE.pollTimer = null;
    LIVE.historyPollTimer = null;
    LIVE.reconnectTimer = null;
    LIVE.refreshTimer = null;
  }

  function closeLiveSocket() {
    if (!LIVE.ws) return;
    try {
      LIVE.ws.close(1000, "litebulb_reset");
    } catch (_err) {
      /* best effort */
    }
    LIVE.ws = null;
  }

  function teardownLive() {
    stopLiveTimers();
    closeLiveSocket();
    LIVE.sessionId = "";
    LIVE.sessionBusiness = "";
    LIVE.activeBusiness = "";
    resetLiveState();
  }

  function toolKind(name) {
    const raw = String(name || "").toLowerCase();
    if (/read|file/.test(raw)) return "read";
    if (/write|patch|edit/.test(raw)) return "edit";
    if (/search|fetch/.test(raw)) return "fetch";
    if (/terminal|exec|run|deploy/.test(raw)) return "execute";
    return "other";
  }

  function refreshTraceBoard() {
    if (!RT.live) return;
    applyBoard(null, LIVE.workspaceSnapshot || { business_slug: LIVE.activeBusiness, overview: {}, outputs: [] });
  }

  function upsertLiveTrace(entry) {
    if (!entry) return;
    const key = String(entry.entry_key || entry.id || "").trim();
    if (!key) return;
    const current = LIVE.liveTrace.get(key) || {};
    LIVE.liveTrace.set(key, Object.assign({}, current, entry, { entry_key: key }));
    refreshTraceBoard();
  }

  function liveToolTrace(payload, status) {
    const name = String(payload && payload.name || "").trim();
    const context = String(payload && payload.context || "").trim();
    if (!name) return null;
    if (name === "skill_view") {
      return {
        kind: "skill",
        label: context || "Skill",
        detail: context || "Loaded a skill.",
        status,
        skill_name: context,
        tool_name: name,
        updated_at: new Date().toISOString(),
      };
    }
    return {
      kind: "tool",
      label: j$(name),
      detail: context || j$(name),
      status,
      tool_name: name,
      updated_at: new Date().toISOString(),
    };
  }

  function applyToolPreview(name, preview) {
    const toolName = String(name || "").trim();
    const nextPreview = String(preview || "").trim();
    if (!toolName || !nextPreview) return;
    LIVE.toolEls.forEach((holder) => {
      if (!holder || holder.dataset.toolName !== toolName) return;
      const chip = holder.querySelector(".tool");
      if (!chip || chip.classList.contains("done")) return;
      if (holder.dataset.toolPreview === nextPreview) return;
      holder.dataset.toolPreview = nextPreview;
      const ttl = holder.querySelector(".ttl");
      if (ttl) {
        ttl.textContent = nextPreview;
        ttl.title = nextPreview;
      }
      ceolog(`<span class="l-blue">[tool]</span> ${esc(toolName)} · ${esc(nextPreview)}`, true);
    });
  }

  function cancelAssistantTypingAnimation() {
    if (LIVE.assistantTypingTimer) {
      window.clearTimeout(LIVE.assistantTypingTimer);
      LIVE.assistantTypingTimer = null;
    }
  }

  function ensureAssistantBubble() {
    if (LIVE.assistantBubble && document.body.contains(LIVE.assistantBubble)) return LIVE.assistantBubble;
    const container = document.createElement("div");
    container.className = "m m-ceo";
    container.innerHTML = `<div class="who">takyon · ceo</div><div class="bubble"></div>`;
    msgs().appendChild(container);
    LIVE.assistantBubble = $(".bubble", container);
    scrollChat();
    return LIVE.assistantBubble;
  }

  function showAssistantPlaceholder(text) {
    cancelAssistantTypingAnimation();
    LIVE.assistantText = "";
    LIVE.assistantDeltaSeen = false;
    ensureAssistantBubble().innerHTML = `<span style="color:var(--muted);font-style:italic">${esc(text || "thinking…")}</span>`;
    scrollChat();
  }

  function appendAssistantText(text) {
    if (!text) return;
    cancelAssistantTypingAnimation();
    LIVE.assistantDeltaSeen = true;
    LIVE.assistantText += text;
    ensureAssistantBubble().innerHTML = formatRichText(LIVE.assistantText);
    scrollChat();
  }

  function finishAssistantText(text) {
    cancelAssistantTypingAnimation();
    if (text) LIVE.assistantText = String(text);
    if (LIVE.assistantText) {
      ensureAssistantBubble().innerHTML = formatRichText(LIVE.assistantText);
      rememberHistoryMessage("assistant", LIVE.assistantText);
    }
    LIVE.assistantText = "";
    LIVE.assistantDeltaSeen = false;
    LIVE.assistantBubble = null;
    scrollChat();
  }

  function typeAssistantText(text) {
    const finalText = String(text || "").trim();
    if (!finalText) {
      finishAssistantText("(empty response)");
      return;
    }
    cancelAssistantTypingAnimation();
    LIVE.assistantDeltaSeen = false;
    LIVE.assistantText = "";
    rememberHistoryMessage("assistant", finalText);
    const bubble = ensureAssistantBubble();
    const total = finalText.length;
    const chunk = Math.max(2, Math.ceil(total / 36));
    let index = 0;
    const step = () => {
      index = Math.min(total, index + chunk);
      bubble.innerHTML = formatRichText(`${finalText.slice(0, index)}${index < total ? "▌" : ""}`);
      scrollChat();
      if (index >= total) {
        finishAssistantText(finalText);
        return;
      }
      LIVE.assistantTypingTimer = window.setTimeout(step, total > 320 ? 18 : 24);
    };
    step();
  }

  function mergeHistoryMessages(items) {
    const messages = Array.isArray(items) ? items : [];
    messages.forEach((item) => {
      const role = String(item && item.role || "").trim().toLowerCase();
      const text = String(item && item.text || "").trim();
      if (!text || hasSeenHistoryMessage(role, text)) return;
      if (role === "user") {
        rememberHistoryMessage("user", text);
        addYou(text);
        return;
      }
      if (role === "assistant") {
        if (LIVE.assistantBubble && !LIVE.assistantDeltaSeen && !LIVE.assistantText) typeAssistantText(text);
        else finishAssistantText(text);
        return;
      }
      if (role === "system") {
        if (isTransientConnectionMessage(text)) return;
        if (isBusinessScopeDeniedMessage(text)) {
          addCeo(formatRichText(text));
          rememberHistoryMessage("system", text);
          return;
        }
        if (/^(?:\d{3}:|request timed out:|session busy|session not found)/i.test(text)) return;
        addCeo(formatRichText(text));
        rememberHistoryMessage("system", text);
      }
    });
  }

  async function pollSessionHistory() {
    if (!LIVE.sessionId) return;
    try {
      const res = await rpc("session.history", { session_id: LIVE.sessionId }, 10000);
      LIVE.historyRunning = Boolean(res && res.running);
      mergeHistoryMessages(res && res.messages);
      syncHistoryPollTimer();
      if (!LIVE.historyRunning) return;
      scheduleLiveRefresh(250);
    } catch (err) {
      if (isMissingSessionError(err)) {
        LIVE.sessionId = "";
        LIVE.sessionBusiness = "";
        if (LIVE.activeBusiness) {
          void ensureSession(LIVE.activeBusiness).then(() => {
            syncHistoryPollTimer();
          }).catch(() => {
            /* best effort session recovery */
          });
        }
      }
    }
  }

  async function ensureSession(business) {
    const desired = String(business || "").trim().toLowerCase();
    if (LIVE.sessionId && LIVE.sessionBusiness === desired) return LIVE.sessionId;
    closeLiveSocket();
    const res = await rpc("session.create", {
      cols: 100,
      _takyon_boot_business: desired || undefined,
    }, 120000);
    LIVE.sessionId = res && res.session_id || "";
    LIVE.sessionBusiness = desired;
    if (LIVE.sessionId) connectLiveSocket(LIVE.sessionId);
    return LIVE.sessionId;
  }

  function handleGatewayEvent(ev) {
    if (!ev || !ev.type) return;
    if (ev.session_id && LIVE.sessionId && ev.session_id !== LIVE.sessionId) return;
    const payload = ev.payload || {};
    if (ev.type === "message.start") {
      LIVE.activeTurnTraceId = `turn:session:${LIVE.sessionId || "live"}:${Date.now()}`;
      upsertLiveTrace({
        entry_key: LIVE.activeTurnTraceId,
        kind: "turn",
        label: "CEO turn",
        detail: "CEO turn is running.",
        status: "running",
        updated_at: new Date().toISOString(),
      });
      setStatus("thinking…", "run");
      LIVE.historyRunning = true;
      syncHistoryPollTimer();
      showAssistantPlaceholder("thinking…");
      return;
    }
    if (ev.type === "message.delta") {
      appendAssistantText(payload.text || "");
      return;
    }
    if (ev.type === "message.complete") {
      if (LIVE.activeTurnTraceId) {
        const turnStatus = String(payload.status || "").trim().toLowerCase() === "complete" ? "completed" : "failed";
        upsertLiveTrace({
          entry_key: LIVE.activeTurnTraceId,
          kind: "turn",
          label: "CEO turn",
          detail: String(payload.text || "").trim().slice(0, 280) || "CEO turn completed.",
          status: turnStatus,
          updated_at: new Date().toISOString(),
        });
        LIVE.activeTurnTraceId = "";
      }
      const finalText = String(payload.text || "");
      if (!LIVE.assistantDeltaSeen && finalText.trim()) typeAssistantText(finalText);
      else finishAssistantText(finalText || LIVE.assistantText || "(empty response)");
      if (payload.warning) ceolog(esc(String(payload.warning)), true);
      LIVE.historyRunning = false;
      syncHistoryPollTimer();
      void refreshBusinessData(LIVE.activeBusiness, {
        skipCredits: true,
        skipBoard: true,
        view: LIVE_WORKSPACE_VIEW,
      });
      return;
    }
    if (ev.type === "thinking.delta" || ev.type === "reasoning.delta") {
      const text = String(payload.text || "").trim();
      if (text) addThink(text);
      return;
    }
    if (ev.type === "status.update") {
      const text = String(payload.text || "").trim();
      const kind = String(payload.kind || "").trim().toLowerCase();
      if (text) ceolog(esc(text), true);
      if (kind === "takyon") {
        LIVE.historyRunning = true;
        syncHistoryPollTimer();
        scheduleLiveRefresh(250);
      }
      return;
    }
    if (ev.type === "tool.start") {
      const trace = liveToolTrace(payload, "running");
      const el = addTool({
        kind: toolKind(payload.name),
        nm: payload.name || "tool",
        ttl: payload.context || payload.name || "working",
      });
      el.dataset.toolName = String(payload.name || "tool");
      if (payload.tool_id) LIVE.toolEls.set(String(payload.tool_id), el);
      if (payload.tool_id) {
        upsertLiveTrace(Object.assign({ entry_key: `tool:${String(payload.tool_id)}` }, trace || {}));
      }
      ceolog(
        `<span class="l-blue">[${esc(trace && trace.kind === "skill" ? "skill" : "tool")}]</span> ${esc(trace && trace.label || payload.name || "tool")}`,
        true
      );
      return;
    }
    if (ev.type === "tool.progress") {
      applyToolPreview(payload.name, payload.preview);
      return;
    }
    if (ev.type === "tool.complete") {
      const key = String(payload.tool_id || "");
      const holder = LIVE.toolEls.get(key);
      if (holder) {
        const chip = holder.querySelector(".tool");
        if (chip) chip.classList.add("done");
      }
      if (key) {
        const trace = liveToolTrace(payload, "completed") || {};
        if (String(payload.summary || "").trim()) trace.detail = String(payload.summary || "").trim();
        trace.updated_at = new Date().toISOString();
        upsertLiveTrace(Object.assign({ entry_key: `tool:${key}`, status: "completed" }, trace));
      }
      if (payload.summary) ceolog(`<span class="l-green">[tool]</span> ${esc(payload.summary)}`, true);
      scheduleLiveRefresh(150);
      void refreshBusinessData(LIVE.activeBusiness, {
        skipCredits: true,
        skipBoard: true,
        view: LIVE_WORKSPACE_VIEW,
      });
      return;
    }
    if (ev.type === "error") {
      const text = String(payload.message || "The live CEO stream reported an error.").trim();
      if (isTransientConnectionMessage(text)) {
        setStatus("syncing…", "build");
        return;
      }
      if (LIVE.activeTurnTraceId) {
        upsertLiveTrace({
          entry_key: LIVE.activeTurnTraceId,
          kind: "turn",
          label: "CEO turn",
          detail: text,
          status: "failed",
          updated_at: new Date().toISOString(),
        });
        LIVE.activeTurnTraceId = "";
      }
      if (LIVE.assistantBubble) finishAssistantText(text);
      else addCeo(formatRichText(text));
      setStatus("paused", "paused");
    }
  }

  function connectLiveSocket(sessionId) {
    if (!sessionId) return;
    closeLiveSocket();
    const ws = new WebSocket(wsUrl());
    LIVE.ws = ws;
    ws.addEventListener("message", (event) => {
      try {
        const msg = JSON.parse(event.data);
        if (msg && msg.method === "event") handleGatewayEvent(msg.params || {});
      } catch (_err) {
        /* malformed frame */
      }
    });
    ws.addEventListener("close", () => {
      if (LIVE.ws !== ws) return;
      LIVE.ws = null;
      syncHistoryPollTimer();
      if (!LIVE.sessionId || !LIVE.activeBusiness) return;
      setStatus("syncing…", "build");
      LIVE.reconnectTimer = window.setTimeout(() => {
        void ensureSession(LIVE.activeBusiness).catch(() => {
          /* best effort reconnect */
        });
      }, 1200);
    });
  }

  function launcherResumeButton(summary) {
    return `<button class="cbtn" data-business="${esc(summary.slug || "")}" style="font-size:11px;padding:6px 9px">${esc(summary.name || summary.slug || "business")}</button>`;
  }

  function renderLauncherBusinesses() {
    const launcher = document.getElementById("launcher");
    if (!launcher) return;
    let host = document.getElementById("launcher-resume");
    if (!host) {
      host = document.createElement("div");
      host.id = "launcher-resume";
      host.style.cssText = "display:flex;flex-wrap:wrap;gap:8px;margin-top:14px;align-items:center";
      const hint = launcher.querySelector(".hint");
      if (hint && hint.parentNode) hint.parentNode.insertBefore(host, hint.nextSibling);
      else launcher.appendChild(host);
    }
    if (!LIVE.businesses.length) {
      host.innerHTML = "";
      return;
    }
    host.innerHTML = `<span style="font-size:12px;color:var(--muted);letter-spacing:.08em;text-transform:uppercase">resume existing:</span>${LIVE.businesses.map(launcherResumeButton).join("")}`;
    host.querySelectorAll("button[data-business]").forEach((btn) => {
      btn.addEventListener("click", (event) => {
        event.preventDefault();
        const slug = btn.getAttribute("data-business") || "";
        void mountLiveBusiness(slug);
      });
    });
  }

  async function refreshBusinessData(slug, options) {
    const business = String(slug || "").trim().toLowerCase();
    if (!business || LIVE.refreshBusy) return;
    LIVE.refreshBusy = true;
    try {
      const view = String(options && options.view || LIVE_WORKSPACE_VIEW).trim().toLowerCase() === "full" ? "full" : "boot";
      const skipBoard = options && Object.prototype.hasOwnProperty.call(options, "skipBoard")
        ? !!options.skipBoard
        : true;
      const skipCredits = !!(options && options.skipCredits);
      const skipAccount = !!(options && options.skipAccount);
      const skipDashboardState = !!(options && options.skipDashboardState);
      const activeSessionId =
        !skipDashboardState && view === "full" && LIVE.sessionId && LIVE.sessionBusiness === business ? LIVE.sessionId : "";
      const workspacePromise = fetchJSON(`/api/takyon/businesses/${encodeURIComponent(business)}/workspace?limit=50&view=${encodeURIComponent(view)}`);
      const boardPromise = skipBoard
        ? Promise.resolve(null)
        : fetchJSON(`/api/plugins/kanban/board?board=${encodeURIComponent(business)}`);
      const creditsPromise = skipCredits
        ? Promise.resolve(LIVE.creativeCredits)
        : fetchJSON(`/api/takyon/businesses/${encodeURIComponent(business)}/creative-credits`);
      const accountPromise = skipAccount
        ? Promise.resolve(LIVE.operatorAccount)
        : fetchJSON("/api/takyon/operator/account");
      const dashboardPromise = activeSessionId
        ? rpc("takyon.dashboard.state", {
          session_id: activeSessionId,
          business_slug: business,
          view,
          limit: 50,
        }, 10000)
        : Promise.resolve(null);
      const [workspaceSettled, dashboardSettled] = await Promise.allSettled([
        workspacePromise,
        dashboardPromise,
      ]);
      const workspace = workspaceSettled.status === "fulfilled" ? workspaceSettled.value : null;
      const dashboardState = dashboardSettled.status === "fulfilled" ? dashboardSettled.value : null;
      const snapshot = mergeLiveSnapshots(workspace, dashboardState);
      if (snapshot) {
        LIVE.workspaceOverview = snapshot.overview || {};
        applyWorkspace(snapshot, businessSummary(business));
        const nextStatus = liveStatusFromSnapshot(snapshot);
        setStatus(nextStatus.text, nextStatus.state);
      }
      if (!snapshot) setStatus("idle", "idle");
      const settled = await Promise.allSettled([boardPromise, creditsPromise, accountPromise]);
      const board = settled[0].status === "fulfilled" ? settled[0].value : null;
      LIVE.creativeCredits = settled[1].status === "fulfilled" ? settled[1].value : LIVE.creativeCredits;
      LIVE.operatorAccount = settled[2].status === "fulfilled" ? settled[2].value : LIVE.operatorAccount;
      if (snapshot || board) applyBoard(board, snapshot || LIVE.workspaceSnapshot || null);
      if (document.getElementById("w-operator")) renderOperatorWindow();
      if (document.getElementById("w-wallet")) renderWalletWindow();
      if (document.getElementById("w-wake")) renderWakeWindow("");
      if (document.getElementById("w-files")) renderDeliverablesWindow();
    } catch (err) {
      setStatus("paused", "paused");
      addCeo(formatRichText(err instanceof Error ? err.message : String(err)));
    } finally {
      LIVE.refreshBusy = false;
    }
  }

  async function mountLiveBusiness(slug, providedSummary, initialSnapshot) {
    const business = String(slug || "").trim().toLowerCase();
    if (!business) return;
    LIVE.activeBusiness = business;
    syncBusinessParam(business);
    const summary = providedSummary || businessSummary(business) || { slug: business, name: business, goal: "", mode: "test" };
    const brand = deriveBrand(summary.goal || summary.name || business);
    const biz = {
      slug: business,
      name: summary.name || brand.name,
      idea: summary.goal || "",
      mode: summary.mode || "test",
    };
    RT.live = true;
    mountLiveShell(biz);
    const seededSnapshot = mergeLiveSnapshots(initialSnapshot, {
      business_slug: business,
      current: {
        name: biz.name,
        goal: biz.idea,
        mode: biz.mode,
      },
    });
    if (seededSnapshot) {
      applyWorkspace(seededSnapshot, summary);
      applyBoard(null, seededSnapshot);
      if (document.getElementById("w-wake")) renderWakeWindow("");
      if (document.getElementById("w-files")) renderDeliverablesWindow();
      setStatus("running", "run");
    }
    const sessionPromise = ensureSession(business).catch(() => "");
    await refreshBusinessData(business, {
      skipAccount: true,
      skipDashboardState: true,
      skipBoard: true,
      view: LIVE_WORKSPACE_VIEW,
    });
    if (LIVE.activeBusiness === business) {
      await refreshBusinessData(business, {
        skipAccount: true,
        skipCredits: true,
        skipDashboardState: true,
        skipBoard: false,
        view: LIVE_WORKSPACE_VIEW,
      });
    }
    const sessionId = await sessionPromise;
    if (sessionId && LIVE.activeBusiness === business) {
      await refreshBusinessData(business, {
        skipAccount: true,
        skipCredits: true,
        skipBoard: false,
        view: LIVE_WORKSPACE_VIEW,
      });
    }
  }

  function mountLiveShell(biz) {
    stopLiveTimers();
    if (RT.ro) {
      try {
        RT.ro.disconnect();
      } catch (_err) {
        /* observer cleanup is best effort */
      }
    }
    Object.assign(RT, {
      biz,
      tasks: [],
      seq: 1,
      wake: 0,
      nextWakeAt: 0,
      paused: false,
      credits: dollarFromAccount(),
      move: "—",
      tickH: null,
      wakeH: null,
      secH: null,
      ceoFeed: null,
      shipped: [],
      moved: new Set(),
      logBuf: [],
      ro: null,
      blockedNote: false,
      channels: freshChannels(),
      live: true,
    });
    document.getElementById("launcher").style.display = "none";
    document.getElementById("workspace").classList.add("on");
    document.getElementById("mb-ws").classList.add("on");
    document.getElementById("mb-biz").textContent = biz.name || biz.slug || "—";
    const modeEl = document.getElementById("mb-mode");
    modeEl.textContent = biz.mode || "test";
    modeEl.classList.toggle("live-mode", biz.mode === "live");
    modeEl.classList.toggle("test", biz.mode !== "live");
    bulb.classList.add("on");
    setStatus("syncing…", "build");
    desk = $("#desk");
    if (desk) {
      desk.querySelectorAll(".win").forEach((win) => win.remove());
      desk.classList.remove("stack");
    }
    openBoard();
    openGraph();
    openProduct();
    openStatus();
    openCeoLog();
    syncDock();
    renderGraphWindow();
    layoutMain();
    try {
      RT.ro = new ResizeObserver(layoutMain);
      RT.ro.observe(desk);
    } catch (_err) {
      /* layout observer is optional */
    }
    msgs().innerHTML = "";
    RT.logBuf = [];
    if (RT.ceoFeed) renderLog(RT.ceoFeed);
    addThink("connecting to Takyon.");
    updateMenu();
    LIVE.menuTimer = window.setInterval(() => updateMenu(), 1000);
    restartLivePollTimer(15000);
    restartHistoryPollTimer(4000);
  }

  async function createLiveBusinessWithOptions(options) {
    const goal = String(options && options.goal || "").trim();
    const name = String(options && options.name || "").trim();
    const mode = String(options && options.mode || "test").trim().toLowerCase() === "live" ? "live" : "test";
    const errorEl = options && options.errorEl || null;
    if (errorEl) errorEl.textContent = "";
    if (!goal) {
      if (errorEl) errorEl.textContent = "Enter a company idea.";
      return;
    }
    const brand = deriveBrand(name || goal);
    const businessName = name || brand.name;
    const businessSlug = slugifyName(name || brand.slug);
    setStatus("building…", "build");
    try {
      const sessionId = await ensureSession("");
      const result = await rpc("takyon.dashboard.create", {
        session_id: sessionId,
        business: businessSlug,
        business_name: businessName,
        goal,
        mode,
        limit: 50,
      }, 600000);
      if (Array.isArray(result && result.businesses)) rememberBusinesses(result.businesses);
      const created = String(result && result.business_slug || businessSlug).trim().toLowerCase();
      const summary = businessSummary(created) || {
        slug: created,
        name: businessName,
        goal,
        mode,
      };
      const initialSnapshot = normalizeLiveSnapshot({
        business_slug: created,
        current: result && result.current || {},
        overview: result && result.overview || {},
        outputs: result && result.outputs || [],
        background_run: result && result.background_run || null,
      });
      await mountLiveBusiness(created, summary, initialSnapshot);
      if (result && result.output) addCeo(formatRichText(result.output));
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      setStatus("paused", "paused");
      if (errorEl) {
        errorEl.textContent = message;
        return;
      }
      addCeo(formatRichText(message));
    }
  }

  async function createLiveBusinessFromIdea() {
    const value = (input.value || input.placeholder || "").replace(/…$/, "").trim();
    if (!value) return;
    await createLiveBusinessWithOptions({ goal: value, mode: "test" });
  }

  async function submitLivePrompt() {
    const field = document.getElementById("say");
    const text = field.value.trim();
    if (!text || !LIVE.activeBusiness) return;
    field.value = "";
    rememberHistoryMessage("user", text);
    addYou(text);
    try {
      let sessionId = await ensureSession(LIVE.activeBusiness);
      showAssistantPlaceholder(LIVE.historyRunning ? "interrupting the current turn…" : "thinking…");
      setStatus("thinking…", "run");
      if (LIVE.historyRunning && sessionId) {
        await rpc("session.interrupt", { session_id: sessionId }, 10000);
        await wait(400);
        showAssistantPlaceholder("thinking…");
      }
      LIVE.historyRunning = true;
      syncHistoryPollTimer();
      for (let attempt = 0; attempt < 8; attempt++) {
        try {
          await rpc("prompt.submit", {
            session_id: sessionId,
            text,
            create_in_test_mode: String(RT.biz && RT.biz.mode || "test") !== "live",
          }, 30000);
          return;
        } catch (err) {
          if (isMissingSessionError(err)) {
            LIVE.sessionId = "";
            LIVE.sessionBusiness = "";
            sessionId = await ensureSession(LIVE.activeBusiness);
            continue;
          }
          if (attempt < 7 && isBusyError(err)) {
            await wait(350 + attempt * 200);
            continue;
          }
          throw err;
        }
      }
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      if (LIVE.assistantBubble) finishAssistantText(message);
      else addCeo(formatRichText(message));
      LIVE.historyRunning = false;
      syncHistoryPollTimer();
      setStatus("paused", "paused");
    }
  }

  function bindLiveChrome() {
    [
      ["mb-biz", openOperatorWindow, "open operator and business controls", true],
      ["mb-credits", openWalletWindow, "open wallet", false],
      ["mb-wake", openWakeWindow, "open wake schedule", true],
    ].forEach(([id, handler, label, requiresLive]) => {
      const el = document.getElementById(id);
      if (!el || el.dataset.liveBound) return;
      el.dataset.liveBound = "1";
      el.setAttribute("role", "button");
      el.setAttribute("tabindex", "0");
      el.setAttribute("title", label);
      el.style.cursor = "pointer";
      el.addEventListener("click", () => {
        if (requiresLive && !RT.live) return;
        handler();
      });
      el.addEventListener("keydown", (event) => {
        if (requiresLive && !RT.live) return;
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          handler();
        }
      });
    });
  }

  function interceptClicks() {
    bindLiveChrome();
    document.getElementById("go").addEventListener("click", (event) => {
      event.preventDefault();
      event.stopImmediatePropagation();
      void createLiveBusinessFromIdea();
    }, true);
    input.addEventListener("keydown", (event) => {
      if (event.key !== "Enter") return;
      event.preventDefault();
      event.stopImmediatePropagation();
      void createLiveBusinessFromIdea();
    }, true);
    document.getElementById("send").addEventListener("click", (event) => {
      event.preventDefault();
      event.stopImmediatePropagation();
      void submitLivePrompt();
    }, true);
    document.getElementById("say").addEventListener("keydown", (event) => {
      if (event.key !== "Enter") return;
      event.preventDefault();
      event.stopImmediatePropagation();
      void submitLivePrompt();
    }, true);
    document.getElementById("reset").addEventListener("click", () => {
      teardownLive();
      window.setTimeout(() => {
        renderLauncherBusinesses();
        renderWalletRail();
      }, 0);
    }, true);
    document.getElementById("mb-newidea").addEventListener("click", () => {
      teardownLive();
      window.setTimeout(() => {
        renderLauncherBusinesses();
        renderWalletRail();
      }, 0);
    }, true);
    window.addEventListener("beforeunload", teardownLive);
  }

  async function bootstrapLive() {
    interceptClicks();
    const requestedBusiness = String(currentBusinessParam() || "").trim().toLowerCase();
    if (requestedBusiness) {
      const placeholder = { slug: requestedBusiness, name: requestedBusiness, goal: "", mode: "test" };
      void mountLiveBusiness(
        requestedBusiness,
        placeholder,
        seedBusinessSnapshot(requestedBusiness, placeholder),
      );
    }
    const businessesPromise = fetchJSON("/api/takyon/operator/businesses")
      .then((businesses) => {
        rememberBusinesses(Array.isArray(businesses && businesses.businesses) ? businesses.businesses : []);
        if (!requestedBusiness) {
          const initialBusiness = LIVE.businesses.length === 1 ? String(LIVE.businesses[0].slug || "") : "";
          if (initialBusiness) void mountLiveBusiness(initialBusiness);
        }
      })
      .catch(() => {
        if (!LIVE.activeBusiness) renderLauncherBusinesses();
      });
    const accountPromise = fetchJSON("/api/takyon/operator/account")
      .then((account) => {
        LIVE.operatorAccount = account;
        renderWalletRail();
      })
      .catch(() => {
        renderWalletRail();
      });
    await Promise.allSettled([businessesPromise, accountPromise]);
  }

  void bootstrapLive();
})();
