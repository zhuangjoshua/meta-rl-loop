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
    businesses: [],
    businessIndex: new Map(),
    operatorAccount: null,
    creativeCredits: null,
    bootedBusiness: "",
    planBusiness: "",
    refreshBusy: false,
    workspaceSnapshot: null,
    workspaceOutputs: [],
    lastOverviewTaskSignature: "",
    lastBackgroundDetail: "",
    lastCeoHeadline: "",
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

  function applyBoard(board, snapshot) {
    const next = [];
    const cols = Array.isArray(board && board.columns) ? board.columns : [];
    cols.forEach((col) => {
      const tasks = Array.isArray(col && col.tasks) ? col.tasks : [];
      tasks.forEach((task) => {
        if (String(task && task.status || "").toLowerCase() === "archived") return;
        next.push(mapBoardTask(task || {}));
      });
    });
    if (!next.length) {
      const overviewTasks = Array.isArray(snapshot && snapshot.overview && snapshot.overview.tasks)
        ? snapshot.overview.tasks
        : [];
      overviewTasks.forEach((task, index) => {
        next.push(mapOverviewTask(task || {}, index));
      });
    }
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
    return (
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
    return String(website.path || website.source_path || product.source_path || "product/site").trim();
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
    if (!snapshot || LIVE.planBusiness === snapshot.business_slug) return;
    const overview = snapshot.overview || {};
    const rawTasks = Array.isArray(overview.tasks) ? overview.tasks : [];
    const entries = rawTasks
      .slice(0, 5)
      .map((item) => {
        const status = String(item && item.status || "").toLowerCase();
        return {
          t: String(item && (item.label || item.id || item.detail) || "task"),
          s: status === "done" ? "done" : status === "running" || status === "ready" || status === "scheduled" ? "doing" : "todo",
        };
      });
    if (entries.length > 0) {
      addPlan(entries);
      LIVE.planBusiness = snapshot.business_slug;
    }
  }

  function syncOverviewActivity(snapshot) {
    if (!snapshot) return;
    const backgroundRun = snapshot.background_run || {};
    const ceo = snapshot.overview && snapshot.overview.ceo_loop || {};
    const overviewTasks = Array.isArray(snapshot.overview && snapshot.overview.tasks)
      ? snapshot.overview.tasks
      : [];

    const backgroundDetail = String(backgroundRun.detail || "").trim();
    if (backgroundDetail && backgroundDetail !== LIVE.lastBackgroundDetail) {
      ceolog(`<span class="sys">[background]</span> ${esc(backgroundDetail)}`, true);
      LIVE.lastBackgroundDetail = backgroundDetail;
    }

    const ceoHeadline = [String(ceo.headline || "").trim(), String(ceo.next_action || "").trim()]
      .filter(Boolean)
      .join(" · ");
    if (ceoHeadline && ceoHeadline !== LIVE.lastCeoHeadline) {
      addCeo(formatRichText(ceoHeadline));
      LIVE.lastCeoHeadline = ceoHeadline;
    }

    const signature = overviewTasks
      .map((task) => [task && task.id, task && task.status, task && task.detail].join("|"))
      .join("::");
    if (!signature || signature === LIVE.lastOverviewTaskSignature) return;
    LIVE.lastOverviewTaskSignature = signature;
    const active = overviewTasks.filter((task) => {
      const status = String(task && task.status || "").toLowerCase();
      const tone = String(task && task.tone || "").toLowerCase();
      return tone === "active" || /running|active|working|watch/.test(status);
    });
    active.slice(0, 4).forEach((task) => {
      const label = String(task && task.label || "task").trim();
      const detail = String(task && task.detail || "").trim();
      ceolog(`<span class="l-blue">[focus]</span> ${esc(label)}${detail ? ` — ${esc(detail)}` : ""}`, true);
    });
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
    renderPlanSummary(snapshot);
    syncOverviewActivity(snapshot);
    const modeEl = $("#mb-mode");
    if (modeEl) {
      modeEl.textContent = RT.biz.mode || "test";
      modeEl.classList.toggle("live-mode", RT.biz.mode === "live");
      modeEl.classList.toggle("test", RT.biz.mode !== "live");
    }
    $("#mb-biz").textContent = RT.biz.name || RT.biz.slug || "—";
    const headline = String(ceo.headline || "").trim();
    const detail = String(ceo.detail || "").trim();
    if (snapshot.business_slug !== LIVE.bootedBusiness) {
      addThink("connected to the live Takyon runtime.");
      if (headline || detail) addCeo([headline, detail].filter(Boolean).map(formatRichText).join("<br>"));
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
    if (!w || !RT.live) return;
    const account = LIVE.operatorAccount;
    const payoutStatus = account && account.available ? String(account.stripe_connect_status || "none") : "none";
    const payoutButtonLabel = payoutStatus === "active" ? "open payouts" : "connect payouts";
    const spendableCents = operatorSpendableCents(account);
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

      <div class="lab">operator budget</div>
      <div class="big-wake" style="font-size:28px">${spendableCents === null ? "—" : formatBudgetCents(spendableCents)}</div>
      <div class="meta" style="margin:6px 0 10px">${!account
        ? "Loading operator budget."
        : !account.available
          ? "Per-user budget unavailable."
          : "Spendful turns use this top-level operator budget."}</div>
      <div style="display:grid;gap:6px;margin-bottom:12px">
        <div style="display:flex;justify-content:space-between;gap:12px"><span class="meta">included remaining</span><span class="meta">${account && account.available ? formatBudgetCents(account.allowance_remaining_cents) : "—"}</span></div>
        <div style="display:flex;justify-content:space-between;gap:12px"><span class="meta">top-up balance</span><span class="meta">${account && account.available ? formatBudgetCents(account.topup_balance_cents) : "—"}</span></div>
        <div style="display:flex;justify-content:space-between;gap:12px"><span class="meta">reserved</span><span class="meta">${account && account.available ? formatBudgetCents(account.reserved_cents) : "—"}</span></div>
        <div style="display:flex;justify-content:space-between;gap:12px"><span class="meta">customer payouts</span><span class="meta">${account && account.available ? formatBudgetCents(account.owed_balance_cents) : "—"}</span></div>
        <div style="display:flex;justify-content:space-between;gap:12px"><span class="meta">connect status</span><span class="meta">${account && account.available ? esc(payoutStatus) : "—"}</span></div>
      </div>
      <div style="display:flex;gap:8px;margin-bottom:8px">
        <input id="operator-topup-amount" inputmode="decimal" placeholder="25" type="text" style="flex:1;border:1.5px solid var(--ink);background:#fff;padding:8px 10px;font:12px/1.4 'Space Mono',monospace" />
        <button class="cbtn" id="operator-topup" type="button">top up</button>
      </div>
      <div style="display:flex;gap:8px;align-items:center">
        <button class="cbtn" id="operator-payouts" type="button">${esc(payoutButtonLabel)}</button>
        <span class="meta" id="operator-billing-error"></span>
      </div>
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
      window.setTimeout(renderLauncherBusinesses, 0);
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
    $("#operator-topup", w).addEventListener("click", () => {
      const amount = String($("#operator-topup-amount", w).value || "").trim();
      void submitOperatorTopupFromWindow(amount);
    });
    $("#operator-payouts", w).addEventListener("click", () => {
      void startOperatorPayoutConnectFromWindow();
    });
  }

  async function submitOperatorTopupFromWindow(rawAmount) {
    const w = document.getElementById("w-operator");
    const errorEl = w ? $("#operator-billing-error", w) : null;
    if (errorEl) errorEl.textContent = "";
    const dollars = Number.parseFloat(String(rawAmount || "").trim());
    const amountCents = Number.isFinite(dollars) ? Math.round(dollars * 100) : 0;
    if (amountCents <= 0) {
      if (errorEl) errorEl.textContent = "Enter a valid top-up amount.";
      return;
    }
    try {
      const res = await fetchJSON("/api/takyon/operator/topup/checkout", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ amount_cents: amountCents, return_path: currentReturnPath() }),
      });
      if (!res || !res.checkout_url) throw new Error("Top-up checkout URL unavailable.");
      navigateOwner(res.checkout_url);
    } catch (err) {
      if (errorEl) errorEl.textContent = err instanceof Error ? err.message : String(err);
    }
  }

  async function startOperatorPayoutConnectFromWindow() {
    const w = document.getElementById("w-operator");
    const errorEl = w ? $("#operator-billing-error", w) : null;
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
      renderOperatorWindow();
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
      await refreshBusinessData(LIVE.activeBusiness, { skipAccount: true });
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
    const live = String(product.publish_status || RT.biz.publishStatus || "").trim().toLowerCase() === "published" || !!RT.biz.publicUrl;
    const hasLocalPreview = !!String(website.path || "").trim();
    const previewPath = hasLocalPreview ? previewPathForLive() : "";
    const productLabel = live ? "live product" : hasLocalPreview ? "local preview" : "product in progress";
    const hostLabel = live
      ? prettyHost(RT.biz.publicUrl)
      : hasLocalPreview
        ? compactPath(String(website.path || "").trim())
        : "no site yet";
    const publishTarget = prettyHost(website.publish_target || product.publish_target || "");
    body(w).innerHTML = `<div class="mini"><div class="mini__page">
      <div class="lab">${productLabel}</div>
      <div class="mini__h">${esc(RT.biz.name || RT.biz.slug || "Litebulb")}</div>
      <div class="meta" style="margin:6px 0 0">${esc(hostLabel)}</div>
      <p class="mini__sub">${esc(RT.biz.idea || "Takyon business workspace")}</p>
      ${!live && publishTarget ? `<p class="meta" style="margin:0 0 12px">Publish target: ${esc(publishTarget)}${String(product.publish_status || "").trim().toLowerCase() === "published" ? "" : " · not live yet"}</p>` : ""}
      ${live || hasLocalPreview
        ? `<button type="button" class="mini__cta" id="product-open-cta" style="border:0;cursor:pointer">${live ? "open website →" : "open local preview →"}</button>`
        : `<div class="meta" style="margin:0 0 12px">Preview appears after the site exists.</div>`}
      ${(RT.shipped || []).length ? `<div class="mini__feats" id="feats">${(RT.shipped || []).map((f) => `<div>${esc(f)}</div>`).join("")}</div>` : ""}</div></div>`;
    const cta = body(w).querySelector("#product-open-cta");
    if (cta) {
      cta.addEventListener("click", (event) => {
        event.preventDefault();
        event.stopPropagation();
        if (RT.biz.publicUrl) {
          openUrlInNewTab(RT.biz.publicUrl);
          return;
        }
        void openSitePreview(previewPath, `${RT.biz.name || RT.biz.slug || "Product"} preview`);
      });
    }
  };

  const originalRenderOutreach = renderOutreach;
  renderOutreach = function renderOutreachLiveAware() {
    if (!RT.live) return originalRenderOutreach();
    const w = document.getElementById("w-status");
    if (!w) return;
    const publishedPosts = publishedPostEntries();
    const creativeAvailable = !!(LIVE.creativeCredits && LIVE.creativeCredits.available);
    const creativeBalance = creativeAvailable ? wholeCredits(LIVE.creativeCredits.balance_credits) : null;
    const creativeReserved = creativeAvailable ? wholeCredits(LIVE.creativeCredits.reserved_credits) : null;
    body(w).innerHTML = `
      <div class="lab">paid outreach credits</div>
      <div class="big-wake" style="font-size:30px">${creativeBalance === null ? "—" : String(creativeBalance)}</div>
      <div class="meta" style="margin:6px 0 11px">${creativeBalance === null
        ? "Creative credits are unavailable for this business right now. Operator budget stays in the top rail."
        : `${creativeBalance} available${creativeReserved ? ` · ${creativeReserved} reserved` : ""}. Operator budget stays in the top rail.`}</div>
      ${publishedPosts.length
        ? publishedPosts.map((item, index) => buildOutreachRow(item.title, item.meta, "live", { type: "published-post", index, label: item.actionLabel })).join("")
        : buildOutreachRow("Published posts", "No published posts yet", "idle", null)}
    `;
    body(w).querySelectorAll("[data-action]").forEach((el) => {
      const actionType = el.getAttribute("data-action") || "";
      const actionIndex = Number(el.getAttribute("data-action-index") || 0);
      const run = () => {
        if (actionType === "published-post") {
          const entry = publishedPosts[actionIndex];
          const item = entry && entry.payload || null;
          if (!item) return;
          const postUrl = normalizeOpenableUrl(item.url);
          if (postUrl) {
            openUrlInNewTab(postUrl);
            return;
          }
          if (item.artifact_path) {
            void openDocument(item.artifact_path, item.title || entry.title || "Published post");
            return;
          }
          if (item.path) {
            void openDocument(item.path, entry.title || "Published post");
            return;
          }
          if (item.conversation_file) {
            void openDocument(item.conversation_file, item.title || entry.title || "Conversation");
          }
          return;
        }
      };
      el.addEventListener("click", run);
      el.addEventListener("keydown", (event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          run();
        }
      });
    });
  };

  const originalUpdateMenu = updateMenu;
  updateMenu = function updateMenuLiveAware() {
    if (!RT.live) return originalUpdateMenu();
    $("#mb-wake").style.display = "";
    $("#mb-credits").style.display = "";
    $("#mb-wake").textContent = RT.nextWakeAt ? `wake ${RT.paused ? "paused" : fmt(RT.nextWakeAt - Date.now())}` : `wake ${RT.paused ? "paused" : "n/a"}`;
    $("#mb-credits").textContent = hasOperatorAccountBalance() ? `operator $${RT.credits.toFixed(2)}` : "operator n/a";
  };

  const originalRenderBoard = renderBoard;
  renderBoard = function renderBoardLiveAware() {
    if (!RT.live) return originalRenderBoard();
    const w = document.getElementById("w-board");
    if (!w) return;
    const present = new Set(RT.tasks.map((t) => t.status));
    const cols = BOARD_ORDER.filter((status) => present.has(status) || ["todo", "scheduled", "ready", "running", "review", "done"].includes(status));
    body(w).innerHTML = `<div class="kanban">${cols.map((st) => {
      const items = RT.tasks.filter((t) => t.status === st);
      return `<div class="col"><div class="col-h" style="border-color:${STATUS_C[st] || "var(--ink)"}"><span>${st}</span><span class="ct">${items.length}</span></div>
        <div class="col-list">${items.map(cardHTML).join("")}</div></div>`;
    }).join("")}</div>`;
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
  };

  const originalOpenTask = openTask;
  openTask = async function openTaskLiveAware(id) {
    originalOpenTask(id);
    if (!RT.live) return;
    const task = RT.tasks.find((item) => item.id === id);
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
    LIVE.lastOverviewTaskSignature = "";
    LIVE.lastBackgroundDetail = "";
    LIVE.lastCeoHeadline = "";
  }

  function stopLiveTimers() {
    if (LIVE.menuTimer) window.clearInterval(LIVE.menuTimer);
    if (LIVE.pollTimer) window.clearInterval(LIVE.pollTimer);
    if (LIVE.reconnectTimer) window.clearTimeout(LIVE.reconnectTimer);
    LIVE.menuTimer = null;
    LIVE.pollTimer = null;
    LIVE.reconnectTimer = null;
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

  function appendAssistantText(text) {
    if (!text) return;
    LIVE.assistantText += text;
    ensureAssistantBubble().innerHTML = formatRichText(LIVE.assistantText);
    scrollChat();
  }

  function finishAssistantText(text) {
    if (text) LIVE.assistantText = String(text);
    if (LIVE.assistantText) ensureAssistantBubble().innerHTML = formatRichText(LIVE.assistantText);
    LIVE.assistantText = "";
    LIVE.assistantBubble = null;
    scrollChat();
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
      setStatus("running", "run");
      LIVE.assistantText = "";
      LIVE.assistantBubble = null;
      return;
    }
    if (ev.type === "message.delta") {
      appendAssistantText(payload.text || "");
      return;
    }
    if (ev.type === "message.complete") {
      finishAssistantText(payload.text || "");
      setStatus("running", "run");
      void refreshBusinessData(LIVE.activeBusiness);
      return;
    }
    if (ev.type === "thinking.delta" || ev.type === "reasoning.delta") {
      const text = String(payload.text || "").trim();
      if (text) addThink(text);
      return;
    }
    if (ev.type === "status.update") {
      const text = String(payload.text || "").trim();
      if (text) ceolog(esc(text), true);
      return;
    }
    if (ev.type === "tool.start") {
      const el = addTool({
        kind: toolKind(payload.name),
        nm: payload.name || "tool",
        ttl: payload.context || payload.name || "working",
      });
      el.dataset.toolName = String(payload.name || "tool");
      if (payload.tool_id) LIVE.toolEls.set(String(payload.tool_id), el);
      ceolog(`<span class="l-blue">[tool]</span> ${esc(payload.name || "tool")}`, true);
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
      if (payload.summary) ceolog(`<span class="l-green">[tool]</span> ${esc(payload.summary)}`, true);
      void refreshBusinessData(LIVE.activeBusiness);
      return;
    }
    if (ev.type === "error") {
      const text = String(payload.message || "The live CEO stream reported an error.").trim();
      addCeo(formatRichText(text));
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
      if (!LIVE.sessionId || !LIVE.activeBusiness) return;
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
      const settled = await Promise.allSettled([
        fetchJSON(`/api/takyon/businesses/${encodeURIComponent(business)}/workspace?limit=50`),
        fetchJSON(`/api/plugins/kanban/board?board=${encodeURIComponent(business)}`),
        fetchJSON(`/api/takyon/businesses/${encodeURIComponent(business)}/creative-credits`),
        options && options.skipAccount ? Promise.resolve(LIVE.operatorAccount) : fetchJSON("/api/takyon/operator/account"),
      ]);
      const workspace = settled[0].status === "fulfilled" ? settled[0].value : null;
      const board = settled[1].status === "fulfilled" ? settled[1].value : null;
      LIVE.creativeCredits = settled[2].status === "fulfilled" ? settled[2].value : LIVE.creativeCredits;
      LIVE.operatorAccount = settled[3].status === "fulfilled" ? settled[3].value : LIVE.operatorAccount;
      if (workspace) {
        LIVE.workspaceOverview = workspace.overview || {};
        applyWorkspace(workspace, businessSummary(business));
      }
      if (workspace || board) applyBoard(board, workspace || LIVE.workspaceSnapshot || null);
      if (document.getElementById("w-operator")) renderOperatorWindow();
      if (document.getElementById("w-wake")) renderWakeWindow("");
      if (document.getElementById("w-files")) renderDeliverablesWindow();
      setStatus("running", "run");
    } catch (err) {
      setStatus("paused", "paused");
      addCeo(formatRichText(err instanceof Error ? err.message : String(err)));
    } finally {
      LIVE.refreshBusy = false;
    }
  }

  async function mountLiveBusiness(slug, providedSummary) {
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
    await ensureSession(business);
    await refreshBusinessData(business);
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
    openProduct();
    openStatus();
    openCeoLog();
    syncDock();
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
    LIVE.pollTimer = window.setInterval(() => {
      void refreshBusinessData(LIVE.activeBusiness, { skipAccount: true });
    }, 15000);
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
      await mountLiveBusiness(created, summary);
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
    addYou(text);
    try {
      const sessionId = await ensureSession(LIVE.activeBusiness);
      setStatus("running", "run");
      await rpc("prompt.submit", {
        session_id: sessionId,
        text,
        create_in_test_mode: String(RT.biz && RT.biz.mode || "test") !== "live",
      }, 30000);
    } catch (err) {
      addCeo(formatRichText(err instanceof Error ? err.message : String(err)));
      setStatus("paused", "paused");
    }
  }

  function bindLiveChrome() {
    [
      ["mb-biz", openOperatorWindow, "open operator and business controls"],
      ["mb-credits", openOperatorWindow, "open operator budget controls"],
      ["mb-wake", openWakeWindow, "open wake schedule"],
    ].forEach(([id, handler, label]) => {
      const el = document.getElementById(id);
      if (!el || el.dataset.liveBound) return;
      el.dataset.liveBound = "1";
      el.setAttribute("role", "button");
      el.setAttribute("tabindex", "0");
      el.setAttribute("title", label);
      el.style.cursor = "pointer";
      el.addEventListener("click", () => {
        if (!RT.live) return;
        handler();
      });
      el.addEventListener("keydown", (event) => {
        if (!RT.live) return;
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
      window.setTimeout(renderLauncherBusinesses, 0);
    }, true);
    document.getElementById("mb-newidea").addEventListener("click", () => {
      teardownLive();
      window.setTimeout(renderLauncherBusinesses, 0);
    }, true);
    window.addEventListener("beforeunload", teardownLive);
  }

  async function bootstrapLive() {
    interceptClicks();
    try {
      const [businesses, account] = await Promise.all([
        fetchJSON("/api/takyon/operator/businesses"),
        fetchJSON("/api/takyon/operator/account"),
      ]);
      LIVE.operatorAccount = account;
      rememberBusinesses(Array.isArray(businesses && businesses.businesses) ? businesses.businesses : []);
      const initialBusiness = currentBusinessParam() || (LIVE.businesses.length === 1 ? String(LIVE.businesses[0].slug || "") : "");
      if (initialBusiness) {
        await mountLiveBusiness(initialBusiness);
      }
    } catch (_err) {
      renderLauncherBusinesses();
    }
  }

  void bootstrapLive();
})();
