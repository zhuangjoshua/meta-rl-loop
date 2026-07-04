// PLATFORM-OWNED — do not edit. The pinned kit client; the platform overwrites the _takyon/
// directory wholesale per business. App code must import the client only via "@takyon/*".
/**
 * Takyon mobile runtime client — the React Native transport onto the SAME subuser rails the web
 * app uses (mirror of subuser_app_kit/runtime-client.js, method-for-method). There is NO second
 * backend: this is a second transport onto one contract.
 *
 * Two deltas vs the web client, both forced by the platform, neither a contract change:
 *   1. No cookie jar → every request carries `Authorization: Bearer <session_token>` from the
 *      injected token provider (SecureStore), replacing the web client's same-origin cookie.
 *   2. Absolute base → `runtimeApiBase` is baked absolute (https://<slug>.coscale.app/api/takyon/
 *      apps/<slug>), since RN has no window.location.
 *
 * Security posture identical to web: all paid AI/search is brokered server-side (never a provider
 * key on device); rails are gated by `ensureRail`; session token is the only credential and it
 * lives in the Keychain via SecureStore.
 */

export type Rail = string;

export interface SurfaceContext {
  runtimeApiBase: string; // absolute for mobile
  runtimeFeatures?: Rail[];
  railState?: Record<string, { callable?: boolean; reason?: string }>;
  auth?: { url?: string; publishableKey?: string; googleProvider?: string };
  plans?: unknown;
  routes?: unknown;
  branding?: { accent?: string; logoUrl?: string; name?: string };
}

export interface TokenStore {
  getToken: () => Promise<string>;
  setToken: (t: string) => Promise<void>;
  clearToken: () => Promise<void>;
}

export interface LoginResult {
  success: boolean;
  session_token: string;
  app_user_id: string;
  email: string;
  tier: string;
}

export class RailUnavailableError extends Error {
  constructor(public rail: Rail, reason?: string) {
    super(`rail_unavailable:${rail}${reason ? ` (${reason})` : ""}`);
    this.name = "RailUnavailableError";
  }
}

export class ActionError extends Error {
  constructor(message: string, public code: string, public detail?: unknown) {
    super(message);
    this.name = "ActionError";
  }
}

// Mirror of web classifyActionError: normalize a failed action envelope into a typed error.
function classifyActionError(payload: any): ActionError {
  const code = String(payload?.error_code || payload?.code || "action_failed");
  const message = String(payload?.error || payload?.message || "action failed");
  return new ActionError(message, code, payload?.detail);
}

export interface MobileRuntimeClient {
  railStateFor(rail: Rail): { callable: boolean; reason?: string };
  isRailCallable(rail: Rail): boolean;
  // auth / session
  loginWithSupabase(accessToken: string, extra?: Record<string, unknown>): Promise<LoginResult>;
  session(): Promise<{ success: boolean; authenticated: boolean; user?: any; tier?: string }>;
  logout(): Promise<void>;
  deleteAccount(): Promise<{ success: boolean; deleted: boolean }>;
  // account / profile
  account(): Promise<any>;
  cancelSubscription(payload?: Record<string, unknown>): Promise<any>;
  profile(): Promise<any>;
  updateProfile(p: Record<string, unknown>): Promise<any>;
  // records
  listRecords(o?: Record<string, unknown>): Promise<{ records: any[] }>;
  getRecord(type: string, id: string): Promise<any>;
  saveRecord(p: Record<string, unknown>): Promise<any>;
  deleteRecord(type: string, id: string): Promise<any>;
  // paid rails (brokered server-side)
  generate(payload: Record<string, unknown>): Promise<any>;
  search(payload: Record<string, unknown>): Promise<any>;
  // actions
  invokeAction(name: string, payload?: Record<string, unknown>): Promise<unknown>;
}

export function createMobileRuntimeClient(
  context: SurfaceContext,
  tokens: TokenStore,
): MobileRuntimeClient {
  const base = context.runtimeApiBase.replace(/\/+$/, "");
  const railState = context.railState || {};
  const features = new Set(context.runtimeFeatures || []);

  function railStateFor(rail: Rail) {
    const st = railState[rail];
    if (st && typeof st.callable === "boolean") return { callable: st.callable, reason: st.reason };
    return { callable: features.has(rail), reason: features.has(rail) ? undefined : "not selected" };
  }
  function isRailCallable(rail: Rail) {
    return railStateFor(rail).callable;
  }
  function ensureRail(rail: Rail) {
    const st = railStateFor(rail);
    if (!st.callable) throw new RailUnavailableError(rail, st.reason);
  }

  async function req(path: string, init: RequestInit = {}): Promise<any> {
    const token = await tokens.getToken();
    const headers: Record<string, string> = {
      "Content-Type": "application/json",
      ...(init.headers as Record<string, string> | undefined),
    };
    if (token) headers["Authorization"] = `Bearer ${token}`;
    const res = await fetch(`${base}/${path.replace(/^\/+/, "")}`, { ...init, headers });
    const text = await res.text();
    let body: any = {};
    try {
      body = text ? JSON.parse(text) : {};
    } catch {
      body = { raw: text };
    }
    if (!res.ok && !body?.success) {
      // Preserve the server's structured error where present.
      throw new ActionError(
        String(body?.error || `HTTP ${res.status}`),
        String(body?.error_code || `http_${res.status}`),
        body,
      );
    }
    return body;
  }

  return {
    railStateFor,
    isRailCallable,

    async loginWithSupabase(accessToken, extra = {}) {
      ensureRail("auth");
      const r: LoginResult = await req("auth/session", {
        method: "POST",
        body: JSON.stringify({ access_token: accessToken, ...extra }),
      });
      if (r?.success && r.session_token) await tokens.setToken(r.session_token);
      return r;
    },
    async session() {
      ensureRail("auth");
      return req("session", { method: "GET" });
    },
    async logout() {
      ensureRail("auth");
      try {
        await req("session", { method: "DELETE" });
      } finally {
        await tokens.clearToken();
      }
    },
    async deleteAccount() {
      // Apple 5.1.1(v). The server resolves the target user from the session (no id sent) and
      // closes + anonymizes it; we clear the local token regardless of the network outcome.
      ensureRail("account");
      try {
        return await req("account", { method: "DELETE" });
      } finally {
        await tokens.clearToken();
      }
    },

    async account() {
      ensureRail("account");
      return req("account", { method: "GET" });
    },
    async cancelSubscription(payload = {}) {
      ensureRail("account");
      return req("account", {
        method: "POST",
        body: JSON.stringify({ ...payload, action: "cancel_subscription" }),
      });
    },
    async profile() {
      ensureRail("profile");
      return req("profile", { method: "GET" });
    },
    async updateProfile(p) {
      ensureRail("profile");
      return req("profile", { method: "POST", body: JSON.stringify(p) });
    },

    async listRecords(o = {}) {
      ensureRail("records");
      const qs = new URLSearchParams(o as Record<string, string>).toString();
      return req(`records${qs ? `?${qs}` : ""}`, { method: "GET" });
    },
    async getRecord(type, id) {
      ensureRail("records");
      return req(`records/${encodeURIComponent(type)}/${encodeURIComponent(id)}`, { method: "GET" });
    },
    async saveRecord(p) {
      ensureRail("records");
      return req("records", { method: "POST", body: JSON.stringify(p) });
    },
    async deleteRecord(type, id) {
      ensureRail("records");
      return req(`records/${encodeURIComponent(type)}/${encodeURIComponent(id)}`, { method: "DELETE" });
    },

    async generate(payload) {
      ensureRail("generate");
      return req("generate", { method: "POST", body: JSON.stringify(payload) });
    },
    async search(payload) {
      ensureRail("search");
      return req("search", { method: "POST", body: JSON.stringify(payload) });
    },

    async invokeAction(name, payload = {}) {
      ensureRail("actions");
      const body = await req(`actions/${encodeURIComponent(name)}`, {
        method: "POST",
        body: JSON.stringify(payload),
      });
      if (body && body.success === false) throw classifyActionError(body);
      // Unwrap the action envelope (mirror web: envelope.result).
      return body?.result !== undefined ? body.result : body;
    },
  };
}
