// The dashboard can be served either at the root of its host (e.g.
// https://kanban.tilos.com/) or under a URL prefix when reverse-proxied
// (e.g. https://mission-control.tilos.com/takyon/). The Python backend
// injects ``window.__TAKYON_BASE_PATH__`` into index.html based on the
// incoming ``X-Forwarded-Prefix`` header so the SPA can address its own
// ``/api/...`` and ``/dashboard-plugins/...`` URLs correctly without a
// rebuild. Empty string means "served at root".
function readBasePath(): string {
  if (typeof window === "undefined") return "";
  const raw = window.__TAKYON_BASE_PATH__ ?? "";
  if (!raw) return "";
  // Normalise: ensure leading slash, strip trailing slash.
  const withLead = raw.startsWith("/") ? raw : `/${raw}`;
  return withLead.replace(/\/+$/, "");
}

export const TAKYON_BASE_PATH = readBasePath();
const BASE = TAKYON_BASE_PATH;


// Ephemeral session token for protected endpoints.
// Injected into index.html by the server — never fetched via API.
declare global {
  interface Window {
    __TAKYON_SESSION_TOKEN__?: string;
    __TAKYON_BASE_PATH__?: string;
  }
}
let _sessionToken: string | null = null;
const SESSION_HEADER = "X-Takyon-Session-Token";

function readSessionToken(): string {
  if (typeof window === "undefined") return "";
  return String(window.__TAKYON_SESSION_TOKEN__ || "").trim();
}

function setSessionHeader(headers: Headers, token: string): void {
  if (!headers.has(SESSION_HEADER)) {
    headers.set(SESSION_HEADER, token);
  }
}

export async function fetchJSON<T>(url: string, init?: RequestInit): Promise<T> {
  // Inject the session token into all /api/ requests.
  const headers = new Headers(init?.headers);
  const token = window.__TAKYON_SESSION_TOKEN__;
  if (token) {
    setSessionHeader(headers, token);
  }
  const res = await fetch(`${BASE}${url}`, { ...init, headers });
  if (!res.ok) {
    const text = await res.text().catch(() => res.statusText);
    throw new Error(`${res.status}: ${text}`);
  }
  return res.json();
}

export async function fetchJSONWithTimeout<T>(
  url: string,
  timeoutMs: number,
  init?: RequestInit,
  errorLabel?: string,
): Promise<T> {
  const controller = new AbortController();
  const timer = window.setTimeout(() => controller.abort(), timeoutMs);
  try {
    return await fetchJSON<T>(url, {
      ...init,
      signal: controller.signal,
    });
  } catch (err) {
    if (err instanceof DOMException && err.name === "AbortError") {
      throw new Error(`request timed out: ${errorLabel || url}`);
    }
    throw err;
  } finally {
    window.clearTimeout(timer);
  }
}

async function getSessionToken(): Promise<string> {
  if (_sessionToken) return _sessionToken;
  const injected = readSessionToken();
  if (injected) {
    _sessionToken = injected;
    return _sessionToken;
  }
  throw new Error("Session token not available — page must be served by the Takyon dashboard server");
}

export function buildTakyonBusinessSitePreviewFrameUrl(slug: string, path = "product/site"): string {
  const businessSlug = String(slug || "").trim();
  if (!businessSlug) return "";
  const normalizedPath = String(path || "").trim() || "product/site";
  const token = readSessionToken();
  const query = new URLSearchParams({ path: normalizedPath });
  if (token) query.set("token", token);
  return `${BASE}/api/takyon/site-preview/${encodeURIComponent(businessSlug)}?${query.toString()}`;
}

export const api = {
  getStatus: () => fetchJSON<StatusResponse>("/api/status"),
  getDashboardAuthState: async () => {
    const headers = new Headers();
    const token = window.__TAKYON_SESSION_TOKEN__;
    if (token) {
      setSessionHeader(headers, token);
    }
    const res = await fetch(`${BASE}/auth/me`, { headers });
    if (res.status === 401) {
      return {
        authenticated: false,
        auth0_required: true,
      } satisfies DashboardAuthStateResponse;
    }
    if (!res.ok) {
      const text = await res.text().catch(() => res.statusText);
      throw new Error(`${res.status}: ${text}`);
    }
    return res.json() as Promise<DashboardAuthStateResponse>;
  },
  getTakyonOperatorAccount: () =>
    fetchJSON<TakyonOperatorAccountResponse>("/api/takyon/operator/account"),
  getTakyonOperatorHome: () =>
    fetchJSONWithTimeout<TakyonOperatorHomeResponse>(
      "/api/takyon/operator/home",
      15_000,
      undefined,
      "operator home",
    ),
  createTakyonOperatorTopupCheckout: (amountCents: number, returnPath: string) =>
    fetchJSON<{ checkout_url?: string; session_id?: string; amount_cents?: number }>(
      "/api/takyon/operator/topup/checkout",
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ amount_cents: amountCents, return_path: returnPath }),
      },
    ),
  createTakyonOperatorBillingPortal: (returnPath: string) =>
    fetchJSON<{ portal_url?: string; customer_id?: string }>(
      "/api/takyon/operator/billing/portal",
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ return_path: returnPath }),
      },
    ),
  listTakyonOperatorBillingPlans: () =>
    fetchJSON<{ plans: TakyonOperatorPlan[] }>(
      "/api/takyon/operator/billing/plans",
    ),
  listTakyonPublicOperatorPlans: () =>
    fetchJSON<{ plans: TakyonOperatorPlan[] }>(
      "/api/takyon/public/operator/plans",
    ),
  createTakyonOperatorSubscriptionCheckout: (planId: string, returnPath: string) =>
    fetchJSON<{ checkout_url?: string; session_id?: string; plan_id?: string; plan_name?: string }>(
      "/api/takyon/operator/billing/checkout",
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ plan_id: planId, return_path: returnPath }),
      },
    ),
  createTakyonOperatorPayoutConnect: (returnPath: string) =>
    fetchJSON<{
      connect_url?: string;
      link_type?: string;
      stripe_connect_account_id?: string;
      stripe_connect_status?: string;
    }>("/api/takyon/operator/payouts/connect", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ return_path: returnPath }),
    }),
  getTakyonOperatorBusinesses: () =>
    fetchJSONWithTimeout<TakyonOperatorBusinessesResponse>(
      "/api/takyon/operator/businesses",
      15_000,
      undefined,
      "operator businesses",
    ),
  getTakyonOperatorMetaCampaigns: () =>
    fetchJSONWithTimeout<TakyonOperatorMetaCampaignsResponse>(
      "/api/takyon/operator/meta-campaigns",
      15_000,
      undefined,
      "operator meta campaigns",
    ),
  getTakyonBusinessCreativeCredits: (slug: string) =>
    fetchJSON<TakyonBusinessCreativeCreditsResponse>(
      `/api/takyon/businesses/${encodeURIComponent(slug)}/creative-credits`,
    ),
  setTakyonBusinessChannelCreditBudgets: (
    slug: string,
    allocations: Record<"x" | "meta" | "reddit", number>,
  ) =>
    fetchJSON<TakyonBusinessCreativeCreditsResponse>(
      `/api/takyon/businesses/${encodeURIComponent(slug)}/creative-credits/budgets`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ allocations }),
      },
    ),
  getTakyonBusinessCreativeCreditPacks: (slug: string) =>
    fetchJSON<TakyonBusinessCreativeCreditPacksResponse>(
      `/api/takyon/businesses/${encodeURIComponent(slug)}/creative-credits/packs`,
    ),
  createTakyonBusinessCreativeCreditCheckout: (
    slug: string,
    options: {
      packId?: string | null;
      credits?: number | null;
      returnPath?: string;
      successPath?: string;
      cancelPath?: string;
    },
  ) =>
    fetchJSON<{
      checkout_url?: string;
      session_id?: string;
      business_slug?: string;
      pack_id?: string;
      credits?: number;
      amount_cents?: number;
    }>(`/api/takyon/businesses/${encodeURIComponent(slug)}/creative-credits/checkout`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        pack_id: options.packId || undefined,
        credits: options.credits || undefined,
        success_path: options.successPath || options.returnPath || "/",
        cancel_path: options.cancelPath || options.returnPath || options.successPath || "/",
      }),
    }),
  reconcileTakyonBusinessCreativeCreditCheckout: (slug: string, sessionId: string) =>
    fetchJSON<TakyonBusinessCreativeCreditsResponse>(
      `/api/takyon/businesses/${encodeURIComponent(slug)}/creative-credits/reconcile`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ session_id: sessionId }),
      },
    ),
  getTakyonBusinessFile: (slug: string, path: string) =>
    fetchJSONWithTimeout<TakyonBusinessFileReadResponse>(
      `/api/takyon/businesses/${encodeURIComponent(slug)}/file?path=${encodeURIComponent(path)}`,
      15_000,
      undefined,
      "takyon.file.read",
    ),
  getTakyonBusinessSitePreview: (slug: string, path = "") =>
    fetchJSONWithTimeout<{
      business_slug?: string;
      path?: string;
      size?: number;
      url?: string;
      mode?: "inline_html" | "live_url";
      status?: string;
    }>(
      `/api/takyon/businesses/${encodeURIComponent(slug)}/site-preview?path=${encodeURIComponent(path)}`,
      15_000,
      undefined,
      "takyon.site.preview",
    ),
  getTakyonBusinessWorkspace: (slug: string, limit = 50, view: "full" | "boot" = "full") =>
    fetchJSONWithTimeout<TakyonBusinessWorkspaceResponse>(
      `/api/takyon/businesses/${encodeURIComponent(slug)}/workspace?limit=${encodeURIComponent(String(limit))}&view=${encodeURIComponent(view)}`,
      15_000,
      undefined,
      "takyon.dashboard.workspace",
    ),
  getTakyonBusinessHome: (slug: string) =>
    fetchJSONWithTimeout<TakyonBusinessWorkspaceResponse>(
      `/api/takyon/businesses/${encodeURIComponent(slug)}/home`,
      15_000,
      undefined,
      "takyon.dashboard.home",
    ),
  getTakyonBusinessTraction: (slug: string, range = "M") =>
    fetchJSONWithTimeout<TakyonBusinessTractionResponse>(
      `/api/takyon/businesses/${encodeURIComponent(slug)}/traction?range=${encodeURIComponent(range)}`,
      15_000,
      undefined,
      "takyon.dashboard.traction",
    ),
  bindTakyonBusinessMetaManualLaunch: (
    slug: string,
    campaignSlug: string,
    body: {
      campaign_id: string;
      adset_id: string;
      ad_id: string;
      creative_id?: string;
      launched_at?: string;
      actual_daily_budget_usd?: number | string;
      idempotency_key?: string;
    },
  ) =>
    fetchJSON<MetaActionResponse>(
      `/api/takyon/businesses/${encodeURIComponent(slug)}/meta-campaigns/${encodeURIComponent(campaignSlug)}/bind-manual-launch`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      },
    ),
  syncTakyonBusinessMetaManualMetrics: (
    slug: string,
    campaignSlug: string,
    body: {
      spend_usd: number | string;
      impressions: number | string;
      clicks: number | string;
      time_range?: { since?: string; until?: string };
      date_preset?: string;
      idempotency_key?: string;
    },
  ) =>
    fetchJSON<MetaActionResponse>(
      `/api/takyon/businesses/${encodeURIComponent(slug)}/meta-campaigns/${encodeURIComponent(campaignSlug)}/manual-metrics`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      },
    ),
  getSessions: (limit = 20, offset = 0) =>
    fetchJSON<PaginatedSessions>(`/api/sessions?limit=${limit}&offset=${offset}`),
  getSessionMessages: (id: string) =>
    fetchJSON<SessionMessagesResponse>(`/api/sessions/${encodeURIComponent(id)}/messages`),
  getSessionLatestDescendant: (id: string) =>
    fetchJSON<SessionLatestDescendantResponse>(
      `/api/sessions/${encodeURIComponent(id)}/latest-descendant`,
    ),
  deleteSession: (id: string) =>
    fetchJSON<{ ok: boolean }>(`/api/sessions/${encodeURIComponent(id)}`, {
      method: "DELETE",
    }),
  getLogs: (params: { file?: string; lines?: number; level?: string; component?: string }) => {
    const qs = new URLSearchParams();
    if (params.file) qs.set("file", params.file);
    if (params.lines) qs.set("lines", String(params.lines));
    if (params.level && params.level !== "ALL") qs.set("level", params.level);
    if (params.component && params.component !== "all") qs.set("component", params.component);
    return fetchJSON<LogsResponse>(`/api/logs?${qs.toString()}`);
  },
  getAnalytics: (days: number) =>
    fetchJSON<AnalyticsResponse>(`/api/analytics/usage?days=${days}`),
  getModelsAnalytics: (days: number) =>
    fetchJSON<ModelsAnalyticsResponse>(`/api/analytics/models?days=${days}`),
  getConfig: () => fetchJSON<Record<string, unknown>>("/api/config"),
  getDefaults: () => fetchJSON<Record<string, unknown>>("/api/config/defaults"),
  getSchema: () => fetchJSON<{ fields: Record<string, unknown>; category_order: string[] }>("/api/config/schema"),
  getModelInfo: () => fetchJSON<ModelInfoResponse>("/api/model/info"),
  getModelOptions: () => fetchJSON<ModelOptionsResponse>("/api/model/options"),
  getAuxiliaryModels: () => fetchJSON<AuxiliaryModelsResponse>("/api/model/auxiliary"),
  setModelAssignment: (body: ModelAssignmentRequest) =>
    fetchJSON<ModelAssignmentResponse>("/api/model/set", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),
  saveConfig: (config: Record<string, unknown>) =>
    fetchJSON<{ ok: boolean }>("/api/config", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ config }),
    }),
  getConfigRaw: () => fetchJSON<{ yaml: string }>("/api/config/raw"),
  saveConfigRaw: (yaml_text: string) =>
    fetchJSON<{ ok: boolean }>("/api/config/raw", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ yaml_text }),
    }),
  getEnvVars: () => fetchJSON<Record<string, EnvVarInfo>>("/api/env"),
  setEnvVar: (key: string, value: string) =>
    fetchJSON<{ ok: boolean }>("/api/env", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ key, value }),
    }),
  deleteEnvVar: (key: string) =>
    fetchJSON<{ ok: boolean }>("/api/env", {
      method: "DELETE",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ key }),
    }),
  revealEnvVar: async (key: string) => {
    const token = await getSessionToken();
    return fetchJSON<{ key: string; value: string }>("/api/env/reveal", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        [SESSION_HEADER]: token,
      },
      body: JSON.stringify({ key }),
    });
  },

  // Cron jobs
  getCronJobs: (profile = "all") =>
    fetchJSON<CronJob[]>(`/api/cron/jobs?profile=${encodeURIComponent(profile)}`),
  createCronJob: (job: { prompt: string; schedule: string; name?: string; deliver?: string }, profile = "default") =>
    fetchJSON<CronJob>(`/api/cron/jobs?profile=${encodeURIComponent(profile)}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(job),
    }),
  pauseCronJob: (id: string, profile = "default") =>
    fetchJSON<CronJob>(`/api/cron/jobs/${encodeURIComponent(id)}/pause?profile=${encodeURIComponent(profile)}`, { method: "POST" }),
  resumeCronJob: (id: string, profile = "default") =>
    fetchJSON<CronJob>(`/api/cron/jobs/${encodeURIComponent(id)}/resume?profile=${encodeURIComponent(profile)}`, { method: "POST" }),
  triggerCronJob: (id: string, profile = "default") =>
    fetchJSON<CronJob>(`/api/cron/jobs/${encodeURIComponent(id)}/trigger?profile=${encodeURIComponent(profile)}`, { method: "POST" }),
  deleteCronJob: (id: string, profile = "default") =>
    fetchJSON<{ ok: boolean }>(`/api/cron/jobs/${encodeURIComponent(id)}?profile=${encodeURIComponent(profile)}`, { method: "DELETE" }),

  // Profiles (minimal)
  getProfiles: () =>
    fetchJSON<{ profiles: ProfileInfo[] }>("/api/profiles"),
  createProfile: (body: { name: string; clone_from_default: boolean }) =>
    fetchJSON<{ ok: boolean; name: string; path: string }>("/api/profiles", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),
  renameProfile: (name: string, newName: string) =>
    fetchJSON<{ ok: boolean; name: string; path: string }>(
      `/api/profiles/${encodeURIComponent(name)}`,
      {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ new_name: newName }),
      },
    ),
  deleteProfile: (name: string) =>
    fetchJSON<{ ok: boolean }>(
      `/api/profiles/${encodeURIComponent(name)}`,
      { method: "DELETE" },
    ),
  getProfileSetupCommand: (name: string) =>
    fetchJSON<{ command: string }>(
      `/api/profiles/${encodeURIComponent(name)}/setup-command`,
    ),
  getProfileSoul: (name: string) =>
    fetchJSON<{ content: string; exists: boolean }>(
      `/api/profiles/${encodeURIComponent(name)}/soul`,
    ),
  updateProfileSoul: (name: string, content: string) =>
    fetchJSON<{ ok: boolean }>(
      `/api/profiles/${encodeURIComponent(name)}/soul`,
      {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ content }),
      },
    ),

  // Skills & Toolsets
  getSkills: () => fetchJSON<SkillInfo[]>("/api/skills"),
  toggleSkill: (name: string, enabled: boolean) =>
    fetchJSON<{ ok: boolean }>("/api/skills/toggle", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name, enabled }),
    }),
  getToolsets: () => fetchJSON<ToolsetInfo[]>("/api/tools/toolsets"),

  // Session search (FTS5)
  searchSessions: (q: string) =>
    fetchJSON<SessionSearchResponse>(`/api/sessions/search?q=${encodeURIComponent(q)}`),

  // OAuth provider management
  getOAuthProviders: () =>
    fetchJSON<OAuthProvidersResponse>("/api/providers/oauth"),
  disconnectOAuthProvider: async (providerId: string) => {
    const token = await getSessionToken();
    return fetchJSON<{ ok: boolean; provider: string }>(
      `/api/providers/oauth/${encodeURIComponent(providerId)}`,
      {
        method: "DELETE",
        headers: { [SESSION_HEADER]: token },
      },
    );
  },
  startOAuthLogin: async (providerId: string) => {
    const token = await getSessionToken();
    return fetchJSON<OAuthStartResponse>(
      `/api/providers/oauth/${encodeURIComponent(providerId)}/start`,
      {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          [SESSION_HEADER]: token,
        },
        body: "{}",
      },
    );
  },
  submitOAuthCode: async (providerId: string, sessionId: string, code: string) => {
    const token = await getSessionToken();
    return fetchJSON<OAuthSubmitResponse>(
      `/api/providers/oauth/${encodeURIComponent(providerId)}/submit`,
      {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          [SESSION_HEADER]: token,
        },
        body: JSON.stringify({ session_id: sessionId, code }),
      },
    );
  },
  pollOAuthSession: (providerId: string, sessionId: string) =>
    fetchJSON<OAuthPollResponse>(
      `/api/providers/oauth/${encodeURIComponent(providerId)}/poll/${encodeURIComponent(sessionId)}`,
    ),
  cancelOAuthSession: async (sessionId: string) => {
    const token = await getSessionToken();
    return fetchJSON<{ ok: boolean }>(
      `/api/providers/oauth/sessions/${encodeURIComponent(sessionId)}`,
      {
        method: "DELETE",
        headers: { [SESSION_HEADER]: token },
      },
    );
  },

  // Gateway / update actions
  restartGateway: () =>
    fetchJSON<ActionResponse>("/api/gateway/restart", { method: "POST" }),
  updateTakyon: () =>
    fetchJSON<ActionResponse>("/api/takyon/update", { method: "POST" }),
  getActionStatus: (name: string, lines = 200) =>
    fetchJSON<ActionStatusResponse>(
      `/api/actions/${encodeURIComponent(name)}/status?lines=${lines}`,
    ),

  // Dashboard plugins
  getPlugins: () =>
    fetchJSON<PluginManifestResponse[]>("/api/dashboard/plugins"),
  rescanPlugins: () =>
    fetchJSON<{ ok: boolean; count: number }>("/api/dashboard/plugins/rescan"),

  getPluginsHub: () => fetchJSON<PluginsHubResponse>("/api/dashboard/plugins/hub"),

  installAgentPlugin: (body: AgentPluginInstallRequest) =>
    fetchJSON<AgentPluginInstallResponse>("/api/dashboard/agent-plugins/install", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ...body }),
    }),

  enableAgentPlugin: (name: string) =>
    fetchJSON<{ ok: boolean; name: string; unchanged?: boolean }>(
      `/api/dashboard/agent-plugins/${encodeURIComponent(name)}/enable`,
      { method: "POST" },
    ),

  disableAgentPlugin: (name: string) =>
    fetchJSON<{ ok: boolean; name: string; unchanged?: boolean }>(
      `/api/dashboard/agent-plugins/${encodeURIComponent(name)}/disable`,
      { method: "POST" },
    ),

  updateAgentPlugin: (name: string) =>
    fetchJSON<AgentPluginUpdateResponse>(
      `/api/dashboard/agent-plugins/${encodeURIComponent(name)}/update`,
      { method: "POST" },
    ),

  removeAgentPlugin: (name: string) =>
    fetchJSON<{ ok: boolean; name: string }>(
      `/api/dashboard/agent-plugins/${encodeURIComponent(name)}`,
      { method: "DELETE" },
    ),

  savePluginProviders: (body: PluginProvidersPutRequest) =>
    fetchJSON<{ ok: boolean }>("/api/dashboard/plugin-providers", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),

  setPluginVisibility: (name: string, hidden: boolean) =>
    fetchJSON<{ ok: boolean; name: string; hidden: boolean }>(
      `/api/dashboard/plugins/${encodeURIComponent(name)}/visibility`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ hidden }),
      },
    ),
};

export interface ActionResponse {
  name: string;
  ok: boolean;
  pid: number;
}

export interface ActionStatusResponse {
  exit_code: number | null;
  lines: string[];
  name: string;
  pid: number | null;
  running: boolean;
}

export interface PlatformStatus {
  error_code?: string;
  error_message?: string;
  state: string;
  updated_at: string;
}

export interface StatusResponse {
  active_sessions: number;
  config_path: string;
  config_version: number;
  env_path: string;
  gateway_exit_reason: string | null;
  gateway_health_url: string | null;
  gateway_pid: number | null;
  gateway_platforms: Record<string, PlatformStatus>;
  gateway_running: boolean;
  gateway_state: string | null;
  gateway_updated_at: string | null;
  takyon_home: string;
  latest_config_version: number;
  release_date: string;
  version: string;
}

export interface DashboardAuthStateResponse {
  authenticated: boolean;
  auth0_required: boolean;
  user?: {
    email?: string;
    name?: string;
    sub?: string;
  };
}

export interface TakyonOperatorPlan {
  id: string;
  name: string;
  description?: string;
  tagline?: string;
  weekly_allowance_cents?: number;
  amount_cents?: number;
  currency?: string;
  interval?: string;
  featured?: boolean;
  features?: string[];
}

export interface TakyonOperatorAccountResponse {
  available: boolean;
  user_id?: string;
  status?: string;
  owned_business_count?: number;
  allowance_included_cents?: number;
  allowance_used_cents?: number;
  allowance_remaining_cents?: number;
  allowance_percent_used?: number | null;
  allowance_percent_remaining?: number | null;
  operator_plan_name?: string | null;
  operator_plan_weekly_allowance_cents?: number | null;
  operator_plans?: TakyonOperatorPlan[];
  allowance_period_start?: string | null;
  allowance_resets_at?: string | null;
  topup_balance_cents?: number;
  reserved_cents?: number;
  reserved_allowance_cents?: number;
  reserved_topup_cents?: number;
  spendable_cents?: number;
  operator_subscription_status?: string;
  owed_balance_cents?: number;
  paid_out_cents?: number;
  payout_currency?: string;
  stripe_connect_status?: string;
  payouts_enabled?: boolean;
  details_submitted?: boolean;
  reason?: string;
}

export interface TakyonOperatorHomeResponse {
  available: boolean;
  businesses: TakyonOperatorBusinessSummary[];
  account: TakyonOperatorAccountResponse;
  owned_business_count?: number;
  user_id?: string;
  reason?: string;
}

export interface TakyonOperatorBusinessSummary {
  slug?: string;
  name?: string;
  goal?: string;
  mode?: string;
  status?: string;
  state?: string;
  reason?: string;
}

export interface TakyonOperatorBusinessesResponse {
  available: boolean;
  businesses: TakyonOperatorBusinessSummary[];
  owned_business_count?: number;
  reason?: string;
  user_id?: string;
}

export interface TakyonMetaCampaignSnapshot {
  business_slug: string;
  slug: string;
  status?: string;
  launch_mode?: string;
  asset_kind?: string;
  asset_path?: string | null;
  asset_download_url?: string | null;
  plan_path?: string | null;
  receipt_path?: string | null;
  created_at?: string;
  updated_at?: string;
  externally_launched_at?: string;
  objective?: string;
  campaign_name?: string;
  adset_name?: string;
  ad_name?: string;
  daily_budget_usd?: number;
  actual_daily_budget_usd?: number;
  message?: string;
  link?: string;
  tracked_link?: string;
  call_to_action?: string;
  targeting?: Record<string, unknown>;
  manual_launch?: Record<string, unknown> | null;
  ids?: Record<string, unknown>;
  latest_metrics?: Record<string, unknown> | null;
}

export interface TakyonOperatorMetaCampaignsResponse {
  available: boolean;
  campaigns: TakyonMetaCampaignSnapshot[];
  owned_business_count?: number;
  reason?: string;
}

export interface TakyonBusinessCreativeCreditsResponse {
  available: boolean;
  business_slug: string;
  balance_credits?: number;
  reserved_credits?: number;
  supports_custom_credits?: boolean;
  price_cents_per_credit?: number;
  minimum_checkout_credits?: number;
  minimum_checkout_amount_cents?: number;
  channels?: Record<string, TakyonBusinessCreativeCreditChannelBudget>;
  channel_budgets?: Record<string, TakyonBusinessCreativeCreditChannelBudget>;
  total_allocated_credits?: number;
  total_used_credits?: number;
  budget_capacity_credits?: number;
  unallocated_credits?: number;
  unbucketed_used_credits?: number;
  action_costs?: Record<string, TakyonBusinessCreativeCreditActionCost>;
  reason?: string;
}

export interface TakyonBusinessCreativeCreditChannelBudget {
  allocated_credits?: number;
  used_credits?: number;
  reserved_credits?: number;
  remaining_credits?: number;
}

export interface TakyonBusinessCreativeCreditActionCost {
  credits?: number;
  default_bucket?: string | null;
}

export interface TakyonBusinessCreativeCreditPack {
  id: string;
  name?: string;
  description?: string;
  credits?: number;
  amount_cents?: number;
  currency?: string;
}

export interface TakyonBusinessCreativeCreditPacksResponse {
  business_slug: string;
  packs: TakyonBusinessCreativeCreditPack[];
  supports_custom_credits?: boolean;
  price_cents_per_credit?: number;
  minimum_checkout_credits?: number;
  minimum_checkout_amount_cents?: number;
}

export interface TakyonBusinessFileReadResponse {
  business_slug: string;
  path?: string;
  size?: number;
  content?: string;
  truncated?: boolean;
}

export interface TakyonBusinessMediaItem {
  id?: string;
  path: string;
  title?: string;
  detail?: string;
  kind?: "image" | "video";
  role?: "image" | "video" | "ad" | "logo" | "site";
  source?: string;
  at?: number;
}

export interface TakyonBusinessWorkspaceResponse {
  business_slug: string;
  current?: Record<string, unknown>;
  overview?: Record<string, unknown>;
  outputs?: unknown[];
  deliverables?: unknown[];
  media?: TakyonBusinessMediaItem[];
  background_run?: Record<string, unknown> | null;
  live_state?: Record<string, unknown> | null;
}

export interface TakyonBusinessTractionPoint {
  start: string;
  label: string;
  revenue_cents: number;
  users: number;
  usage_events: number;
  pageviews?: number;
  visits?: number;
}

export interface TakyonBusinessTractionResponse {
  success?: boolean;
  business?: string;
  range?: string;
  generated_at?: string;
  points: TakyonBusinessTractionPoint[];
  totals: {
    revenue_cents: number;
    users: number;
    usage_events: number;
    pageviews?: number;
    visits?: number;
  };
  previous_totals: {
    revenue_cents: number;
    users: number;
    usage_events: number;
    pageviews?: number;
    visits?: number;
  };
}

export interface MetaActionResponse {
  success?: boolean;
  status?: string;
  error?: string;
  receipt?: string;
  value?: Record<string, unknown>;
  totals?: Record<string, unknown>;
  metrics_path?: string;
}

export interface SessionInfo {
  id: string;
  source: string | null;
  model: string | null;
  title: string | null;
  started_at: number;
  ended_at: number | null;
  last_active: number;
  is_active: boolean;
  message_count: number;
  tool_call_count: number;
  input_tokens: number;
  output_tokens: number;
  preview: string | null;
  parent_session_id?: string | null;
}

export interface SessionLatestDescendantResponse {
  requested_session_id: string;
  session_id: string;
  path: string[];
  changed: boolean;
}

export interface PaginatedSessions {
  sessions: SessionInfo[];
  total: number;
  limit: number;
  offset: number;
}

export interface EnvVarInfo {
  is_set: boolean;
  redacted_value: string | null;
  description: string;
  url: string | null;
  category: string;
  is_password: boolean;
  tools: string[];
  advanced: boolean;
}

export interface SessionMessage {
  role: "user" | "assistant" | "system" | "tool";
  content: string | null;
  tool_calls?: Array<{
    id: string;
    function: { name: string; arguments: string };
  }>;
  tool_name?: string;
  tool_call_id?: string;
  timestamp?: number;
}

export interface SessionMessagesResponse {
  session_id: string;
  messages: SessionMessage[];
}

export interface LogsResponse {
  file: string;
  lines: string[];
}

export interface AnalyticsDailyEntry {
  day: string;
  input_tokens: number;
  output_tokens: number;
  cache_read_tokens: number;
  reasoning_tokens: number;
  estimated_cost: number;
  actual_cost: number;
  sessions: number;
  api_calls: number;
}

export interface AnalyticsModelEntry {
  model: string;
  input_tokens: number;
  output_tokens: number;
  estimated_cost: number;
  sessions: number;
  api_calls: number;
}

export interface AnalyticsSkillEntry {
  skill: string;
  view_count: number;
  manage_count: number;
  total_count: number;
  percentage: number;
  last_used_at: number | null;
}

export interface AnalyticsSkillsSummary {
  total_skill_loads: number;
  total_skill_edits: number;
  total_skill_actions: number;
  distinct_skills_used: number;
}

export interface AnalyticsResponse {
  daily: AnalyticsDailyEntry[];
  by_model: AnalyticsModelEntry[];
  totals: {
    total_input: number;
    total_output: number;
    total_cache_read: number;
    total_reasoning: number;
    total_estimated_cost: number;
    total_actual_cost: number;
    total_sessions: number;
    total_api_calls: number;
  };
  skills: {
    summary: AnalyticsSkillsSummary;
    top_skills: AnalyticsSkillEntry[];
  };
}

export interface ProfileInfo {
  name: string;
  path: string;
  is_default: boolean;
  model: string | null;
  provider: string | null;
  has_env: boolean;
  skill_count: number;
}

export interface ModelsAnalyticsModelEntry {
  model: string;
  provider: string;
  input_tokens: number;
  output_tokens: number;
  cache_read_tokens: number;
  reasoning_tokens: number;
  estimated_cost: number;
  actual_cost: number;
  sessions: number;
  api_calls: number;
  tool_calls: number;
  last_used_at: number;
  avg_tokens_per_session: number;
  capabilities: {
    supports_tools?: boolean;
    supports_vision?: boolean;
    supports_reasoning?: boolean;
    context_window?: number;
    max_output_tokens?: number;
    model_family?: string;
  };
}

export interface ModelsAnalyticsResponse {
  models: ModelsAnalyticsModelEntry[];
  totals: {
    distinct_models: number;
    total_input: number;
    total_output: number;
    total_cache_read: number;
    total_reasoning: number;
    total_estimated_cost: number;
    total_actual_cost: number;
    total_sessions: number;
    total_api_calls: number;
  };
  period_days: number;
}

export interface CronJob {
  id: string;
  profile?: string | null;
  profile_name?: string | null;
  takyon_home?: string | null;
  is_default_profile?: boolean;
  name?: string | null;
  prompt?: string | null;
  script?: string | null;
  schedule?: { kind?: string; expr?: string; display?: string };
  schedule_display?: string | null;
  enabled: boolean;
  state?: string | null;
  deliver?: string | null;
  last_run_at?: string | null;
  next_run_at?: string | null;
  last_error?: string | null;
}

export interface SkillInfo {
  name: string;
  description: string;
  category: string;
  enabled: boolean;
}

export interface ToolsetInfo {
  name: string;
  label: string;
  description: string;
  enabled: boolean;
  configured: boolean;
  tools: string[];
}

export interface SessionSearchResult {
  session_id: string;
  snippet: string;
  role: string | null;
  source: string | null;
  model: string | null;
  session_started: number | null;
}

export interface SessionSearchResponse {
  results: SessionSearchResult[];
}

// ── Model info types ──────────────────────────────────────────────────

export interface ModelInfoResponse {
  model: string;
  provider: string;
  auto_context_length: number;
  config_context_length: number;
  effective_context_length: number;
  capabilities: {
    supports_tools?: boolean;
    supports_vision?: boolean;
    supports_reasoning?: boolean;
    context_window?: number;
    max_output_tokens?: number;
    model_family?: string;
  };
}

// ── Model options / assignment types ──────────────────────────────────

export interface ModelOptionProvider {
  name: string;
  slug: string;
  models?: string[];
  total_models?: number;
  is_current?: boolean;
  is_user_defined?: boolean;
  source?: string;
  warning?: string;
}

export interface ModelOptionsResponse {
  model?: string;
  provider?: string;
  providers?: ModelOptionProvider[];
}

export interface AuxiliaryTaskAssignment {
  task: string;
  provider: string;
  model: string;
  base_url: string;
}

export interface AuxiliaryModelsResponse {
  tasks: AuxiliaryTaskAssignment[];
  main: { provider: string; model: string };
}

export interface ModelAssignmentRequest {
  scope: "main" | "auxiliary";
  provider: string;
  model: string;
  /** For auxiliary: task slot name, "" for all, "__reset__" to reset all. */
  task?: string;
}

export interface ModelAssignmentResponse {
  ok: boolean;
  scope?: string;
  provider?: string;
  model?: string;
  tasks?: string[];
  reset?: boolean;
}

// ── OAuth provider types ────────────────────────────────────────────────

export interface OAuthProviderStatus {
  logged_in: boolean;
  source?: string | null;
  source_label?: string | null;
  token_preview?: string | null;
  expires_at?: string | null;
  has_refresh_token?: boolean;
  last_refresh?: string | null;
  error?: string;
}

export interface OAuthProvider {
  id: string;
  name: string;
  /** "pkce" (browser redirect + paste code), "device_code" (show code + URL),
   *  or "external" (delegated to a separate CLI like Claude Code or Qwen). */
  flow: "pkce" | "device_code" | "external";
  cli_command: string;
  docs_url: string;
  status: OAuthProviderStatus;
}

export interface OAuthProvidersResponse {
  providers: OAuthProvider[];
}

/** Discriminated union — the shape of /start depends on the flow. */
export type OAuthStartResponse =
  | {
      session_id: string;
      flow: "pkce";
      auth_url: string;
      expires_in: number;
    }
  | {
      session_id: string;
      flow: "device_code";
      user_code: string;
      verification_url: string;
      expires_in: number;
      poll_interval: number;
    };

export interface OAuthSubmitResponse {
  ok: boolean;
  status: "approved" | "error";
  message?: string;
}

export interface OAuthPollResponse {
  session_id: string;
  status: "pending" | "approved" | "denied" | "expired" | "error";
  error_message?: string | null;
  expires_at?: number | null;
}

// ── Dashboard plugin types ─────────────────────────────────────────────

export interface PluginManifestResponse {
  name: string;
  label: string;
  description: string;
  icon: string;
  version: string;
  tab: {
    path: string;
    position?: string;
    override?: string;
    hidden?: boolean;
  };
  slots?: string[];
  entry: string;
  css?: string | null;
  has_api: boolean;
  source: string;
}

export interface HubAgentPluginRow {
  name: string;
  version: string;
  description: string;
  source: string;
  runtime_status: "disabled" | "enabled" | "inactive";
  has_dashboard_manifest: boolean;
  dashboard_manifest: PluginManifestResponse | null;
  path: string;
  can_remove: boolean;
  can_update_git: boolean;
  auth_required: boolean;
  auth_command: string;
  user_hidden: boolean;
}

export interface PluginsHubProviders {
  memory_provider: string;
  memory_options: Array<{ name: string; description: string }>;
  context_engine: string;
  context_options: Array<{ name: string; description: string }>;
}

export interface PluginsHubResponse {
  plugins: HubAgentPluginRow[];
  orphan_dashboard_plugins: PluginManifestResponse[];
  providers: PluginsHubProviders;
}

export interface AgentPluginInstallRequest {
  identifier: string;
  force?: boolean;
  enable?: boolean;
}

export interface AgentPluginInstallResponse {
  ok: boolean;
  plugin_name?: string;
  warnings?: string[];
  missing_env?: string[];
  after_install_path?: string | null;
  enabled?: boolean;
  error?: string;
}

export interface AgentPluginUpdateResponse {
  ok: boolean;
  name?: string;
  output?: string;
  unchanged?: boolean;
  error?: string;
}

export interface PluginProvidersPutRequest {
  memory_provider?: string;
  context_engine?: string;
}
