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
  };

  function endpoint(path) {
    return `${ENV.basePath}${path}`;
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

  function applyBoard(board) {
    const next = [];
    const cols = Array.isArray(board && board.columns) ? board.columns : [];
    cols.forEach((col) => {
      const tasks = Array.isArray(col && col.tasks) ? col.tasks : [];
      tasks.forEach((task) => {
        if (String(task && task.status || "").toLowerCase() === "archived") return;
        next.push(mapBoardTask(task || {}));
      });
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

  function latestActionablePost() {
    const posts = Array.isArray(LIVE.workspaceOverview && LIVE.workspaceOverview.posts) ? LIVE.workspaceOverview.posts : [];
    return posts.find((post) => normalizeOpenableUrl(post && post.url) || (post && post.artifact_path) || (post && post.conversation_file)) || null;
  }

  function latestInboundConversation() {
    const posts = Array.isArray(LIVE.workspaceOverview && LIVE.workspaceOverview.posts) ? LIVE.workspaceOverview.posts : [];
    return (
      posts.find((post) => Number(post && post.unresolved_messages || 0) > 0 && post && post.conversation_file) ||
      posts.find((post) => post && post.conversation_file) ||
      null
    );
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

  function applyWorkspace(snapshot, summary) {
    const overview = (snapshot && snapshot.overview) || {};
    const current = (snapshot && snapshot.current) || {};
    const product = overview.product || {};
    const cron = overview.cron || {};
    const ceo = overview.ceo_loop || {};
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
    renderPlanSummary(snapshot);
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
    return `<div class="chan"${clickable ? ` data-action="${esc(action.type)}" tabindex="0" role="button" style="cursor:pointer"` : ""}>
      <div class="chan-top"><span class="chan-nm">${esc(label)}</span>${actionTag}</div>
      <div class="meta" style="margin-top:6px">${esc(meta)}</div>
    </div>`;
  }

  const originalRenderProduct = renderProduct;
  renderProduct = function renderProductLiveAware() {
    if (!RT.live) return originalRenderProduct();
    const w = document.getElementById("w-product");
    if (!w || !RT.biz) return;
    const previewPath = previewPathForLive();
    body(w).innerHTML = `<div class="mini"><div class="mini__bar"><i></i><i></i><i></i><span>${esc(RT.biz.siteHost || `${RT.biz.slug}.com`)} · ${esc(RT.biz.mode || "test")}</span></div>
      <div class="mini__page"><div class="mini__h">${esc(RT.biz.name || RT.biz.slug || "Litebulb")}</div>
      <p class="mini__sub">${esc(RT.biz.idea || "Takyon business workspace")}${RT.biz.publicUrl ? ` ${esc("· live url available")}` : "."}</p>
      <button type="button" class="mini__cta" id="product-open-cta" style="border:0;cursor:pointer">${RT.biz.publicUrl ? "open live site →" : "open local preview →"}</button>
      <div class="mini__feats" id="feats">${(RT.shipped || []).map((f) => `<div>${esc(f)}</div>`).join("")}</div></div></div>`;
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
    const posts = Array.isArray(LIVE.workspaceOverview && LIVE.workspaceOverview.posts) ? LIVE.workspaceOverview.posts.length : 0;
    const unresolved = Number(LIVE.workspaceOverview && LIVE.workspaceOverview.metrics && LIVE.workspaceOverview.metrics.unresolved_inbound || 0);
    const credits = LIVE.creativeCredits && LIVE.creativeCredits.available
      ? `${Number(LIVE.creativeCredits.balance_credits || 0)} creative credits`
      : "creative credits unavailable";
    const latestPost = latestActionablePost();
    const latestConversation = latestInboundConversation();
    body(w).innerHTML = `
      <div class="lab">operator budget</div>
      <div class="big-wake" style="font-size:30px">$${RT.credits.toFixed(2)}</div>
      <div class="meta" style="margin:6px 0 11px">live Takyon data. This panel stays engine-shaped, but the channel controls are read-only until real per-channel budget rails exist.</div>
      ${buildOutreachRow("Published posts", `${posts} recorded`, posts > 0 ? "live" : "idle", latestPost ? { type: "published-post", label: normalizeOpenableUrl(latestPost.url) ? "open" : "preview" } : null)}
      ${buildOutreachRow("Inbound", `${unresolved} unresolved`, unresolved > 0 ? "live" : "idle", latestConversation ? { type: "inbound-thread", label: "open" } : null)}
      ${buildOutreachRow("Creative credits", credits, LIVE.creativeCredits && LIVE.creativeCredits.available ? "live" : "idle", null)}
    `;
    body(w).querySelectorAll("[data-action]").forEach((el) => {
      const actionType = el.getAttribute("data-action") || "";
      const run = () => {
        if (actionType === "published-post" && latestPost) {
          const postUrl = normalizeOpenableUrl(latestPost.url);
          if (postUrl) {
            openUrlInNewTab(postUrl);
            return;
          }
          if (latestPost.artifact_path) {
            void openDocument(latestPost.artifact_path, latestPost.title || "Published post");
            return;
          }
          if (latestPost.conversation_file) {
            void openDocument(latestPost.conversation_file, latestPost.title || "Conversation");
          }
          return;
        }
        if (actionType === "inbound-thread" && latestConversation && latestConversation.conversation_file) {
          void openDocument(latestConversation.conversation_file, latestConversation.title || "Inbound conversation");
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
    $("#mb-credits").textContent = `$${RT.credits.toFixed(2)}`;
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
      if (payload.tool_id) LIVE.toolEls.set(String(payload.tool_id), el);
      ceolog(`<span class="l-blue">[tool]</span> ${esc(payload.name || "tool")}`, true);
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
      if (board) applyBoard(board);
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

  async function createLiveBusinessFromIdea() {
    const value = (input.value || input.placeholder || "").replace(/…$/, "").trim();
    if (!value) return;
    const brand = deriveBrand(value);
    setStatus("building…", "build");
    try {
      const sessionId = await ensureSession("");
      const result = await rpc("takyon.dashboard.create", {
        session_id: sessionId,
        business: brand.slug,
        business_name: brand.name,
        goal: value,
        mode: "test",
        limit: 50,
      }, 600000);
      if (Array.isArray(result && result.businesses)) rememberBusinesses(result.businesses);
      const created = String(result && result.business_slug || brand.slug).trim().toLowerCase();
      const summary = businessSummary(created) || {
        slug: created,
        name: brand.name,
        goal: value,
        mode: "test",
      };
      await mountLiveBusiness(created, summary);
      if (result && result.output) addCeo(formatRichText(result.output));
    } catch (err) {
      setStatus("paused", "paused");
      addCeo(formatRichText(err instanceof Error ? err.message : String(err)));
    }
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

  function interceptClicks() {
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
