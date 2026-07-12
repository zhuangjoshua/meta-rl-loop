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
 *   2. Absolute base → `runtimeApiBase` is baked absolute (https://<product host>/api/takyon/
 *      apps/<slug>), since RN has no window.location.
 *
 * Security posture identical to web: all paid AI/search is brokered server-side (never a provider
 * key on device); rails are gated by `ensureRail`; session token is the only credential and it
 * lives in the Keychain via SecureStore.
 */

export type Rail = string;

export type SubscriptionState =
  | "active"
  | "trialing"
  | "past_due"
  | "canceled"
  | "sandbox_retired"
  | "none";

export interface SubscriptionCancellationPolicy {
  readonly version: 1;
  readonly effective_timing: "immediate";
  readonly refund_policy: "none";
}

export interface ProductRuntimeContract {
  readonly version: 1;
  readonly subscription: {
    readonly cancellation: SubscriptionCancellationPolicy;
  };
  readonly records: {
    readonly identifier: "opaque_ref";
  };
}

export interface AppEntitlement {
  status?: SubscriptionState | "cancelled" | "paid" | string;
  tier?: string;
  source?: string;
  plan_key?: string | null;
  planKey?: string | null;
  stripe_subscription_id?: string | null;
  stripeSubscriptionId?: string | null;
  [key: string]: unknown;
}

export interface AccountPayload {
  authenticated?: boolean;
  user?: Record<string, unknown>;
  entitlements?: AppEntitlement[];
  /** Backward-compatible projection; AppKit reads the canonical nested contract below. */
  subscription_cancellation_policy?: SubscriptionCancellationPolicy;
  product_runtime_contract: ProductRuntimeContract;
  [key: string]: unknown;
}

export interface SubscriptionCancellationResult {
  recorded: true;
  cancel_at_period_end: false;
  effective_immediately: true;
  stripe_subscription_status: "canceled" | "cancelled";
  current_period_end?: string | null;
  already_canceled?: boolean;
  subscription_cancellation_policy: SubscriptionCancellationPolicy;
  product_runtime_contract?: ProductRuntimeContract;
  [key: string]: unknown;
}

declare const recordRefBrand: unique symbol;
export type RecordRef = string & { readonly [recordRefBrand]: "TakyonRecordRef" };

export interface AppRecord {
  type: string;
  ref: RecordRef;
  title?: string | null;
  data?: unknown;
  metadata?: Record<string, unknown>;
  created_at?: string;
  updated_at?: string;
  [key: string]: unknown;
}

export interface RecordResponse extends AppRecord {
  record: AppRecord;
}

export interface RecordListResponse {
  records: AppRecord[];
  count?: number;
  next_cursor?: string;
}

export interface SaveRecordFields {
  title?: string | null;
  /** The records rail is an upsert contract; data is required for both create and update. */
  data: NonNullable<unknown>;
  metadata?: Record<string, unknown>;
  /** Raw runtime IDs are compatibility-only. Generated products preserve and pass `ref`. */
  id?: never;
  record_id?: never;
  record_ref?: never;
  [key: string]: unknown;
}

export type SaveRecordPayload = SaveRecordFields & (
  | {
      /** Create a new record of this type. */
      record_type: string;
      type?: string;
      ref?: never;
    }
  | {
      /** Backward-compatible create spelling. */
      type: string;
      record_type?: string;
      ref?: never;
    }
  | {
      /** Update the record addressed by this exact runtime-owned reference. */
      ref: RecordRef;
      record_type?: never;
      type?: never;
    }
);

const RECORD_REF_PATTERN = /^tkr_[0-9a-f]{32}$/;

function isObject(value: unknown): value is Record<string, any> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function requireRecordRef(ref: RecordRef): RecordRef {
  if (typeof ref !== "string" || !RECORD_REF_PATTERN.test(ref)) {
    throw new TypeError(
      "record_ref is invalid; pass the ref returned by saveRecord, listRecords, or readRecord",
    );
  }
  return ref;
}

function recordWithRef(record: unknown): unknown {
  if (!isObject(record)) throw new TypeError("record response is invalid");
  const { id: _id, record_id: _recordId, ...publicRecord } = record;
  return { ...publicRecord, ref: requireRecordRef(record.ref as RecordRef) };
}

function payloadWithRecordRefs(payload: any): any {
  if (!isObject(payload)) return payload;
  const out = { ...payload };
  if (isObject(payload.record)) {
    const record = recordWithRef(payload.record) as Record<string, unknown>;
    out.record = record;
    // Preserve the historical envelope and also expose exactly that record at the top level. Some
    // generated screens consumed mutations/reads as a flat record; deterministic mirroring keeps
    // those screens compatible without introducing a second identity.
    for (const [key, value] of Object.entries(record)) out[key] = value;
    delete out.id;
    delete out.record_id;
  }
  if (Array.isArray(payload.records)) out.records = payload.records.map(recordWithRef);
  return out;
}

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

function requireProductRuntimeContract(payload: unknown): AccountPayload {
  const contract = isObject(payload) ? payload.product_runtime_contract : null;
  const cancellation = isObject(contract?.subscription)
    ? contract.subscription.cancellation
    : null;
  if (
    !isObject(contract) ||
    contract.version !== 1 ||
    !isObject(cancellation) ||
    cancellation.version !== 1 ||
    cancellation.effective_timing !== "immediate" ||
    cancellation.refund_policy !== "none" ||
    !isObject(contract.records) ||
    contract.records.identifier !== "opaque_ref"
  ) {
    throw new Error("invalid_product_runtime_contract");
  }
  return payload as AccountPayload;
}

function requireImmediateCancellationResult(payload: unknown): SubscriptionCancellationResult {
  const policy = isObject(payload) ? payload.subscription_cancellation_policy : null;
  const status = String(isObject(payload) ? payload.stripe_subscription_status || "" : "")
    .trim()
    .toLowerCase();
  if (
    !isObject(payload) ||
    payload.recorded !== true ||
    payload.cancel_at_period_end !== false ||
    payload.effective_immediately !== true ||
    !["canceled", "cancelled"].includes(status) ||
    !isObject(policy) ||
    policy.version !== 1 ||
    policy.effective_timing !== "immediate" ||
    policy.refund_policy !== "none"
  ) {
    throw new Error("invalid_subscription_cancellation_result");
  }
  return payload as SubscriptionCancellationResult;
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
  account(): Promise<AccountPayload>;
  cancelSubscription(): Promise<SubscriptionCancellationResult>;
  profile(): Promise<any>;
  updateProfile(p: Record<string, unknown>): Promise<any>;
  // records
  listRecords(o?: Record<string, unknown>): Promise<RecordListResponse>;
  getRecord(ref: RecordRef): Promise<RecordResponse>;
  readRecord(ref: RecordRef): Promise<RecordResponse>;
  saveRecord(p: SaveRecordPayload): Promise<RecordResponse>;
  deleteRecord(ref: RecordRef): Promise<RecordResponse>;
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

  async function getRecord(ref: RecordRef): Promise<RecordResponse> {
    ensureRail("records");
    return payloadWithRecordRefs(
      await req(`records/by-ref/${encodeURIComponent(requireRecordRef(ref))}`, {
        method: "GET",
      }),
    );
  }

  async function readRecord(ref: RecordRef): Promise<RecordResponse> {
    ensureRail("records");
    return payloadWithRecordRefs(
      await req(`records/by-ref/${encodeURIComponent(requireRecordRef(ref))}`, {
        method: "GET",
      }),
    );
  }

  async function deleteRecord(ref: RecordRef): Promise<RecordResponse> {
    ensureRail("records");
    return payloadWithRecordRefs(
      await req(`records/by-ref/${encodeURIComponent(requireRecordRef(ref))}`, {
        method: "DELETE",
      }),
    );
  }

  async function saveRecord(payload: Record<string, any> = {}): Promise<RecordResponse> {
    ensureRail("records");
    if (!Object.prototype.hasOwnProperty.call(payload, "data") || payload.data == null) {
      throw new TypeError("data is required");
    }
    if (payload.record_ref != null || payload.id != null || payload.record_id != null) {
      throw new TypeError("raw record identifiers are not accepted; use the runtime-owned ref");
    }
    const suppliedRef = payload.ref == null ? "" : requireRecordRef(payload.ref as RecordRef);
    const suppliedTypes = [payload.record_type, payload.type]
      .map((value) => String(value || "").trim())
      .filter(Boolean);
    if (new Set(suppliedTypes).size > 1) {
      throw new TypeError("record_type does not match the supplied type");
    }
    const suppliedType = suppliedTypes[0] || "";
    if (suppliedRef && suppliedType) {
      throw new TypeError("record_type cannot accompany a ref update");
    }
    if (!suppliedRef && !suppliedType) throw new Error("record_type is required");
    const route = suppliedRef
      ? `records/by-ref/${encodeURIComponent(suppliedRef)}`
      : "records";
    const { ref: _ref, record_type: _recordType, type: _type, ...recordPayload } = payload;
    return payloadWithRecordRefs(
      await req(route, {
        method: "POST",
        body: JSON.stringify({
          ...recordPayload,
          ...(suppliedType ? { record_type: suppliedType } : {}),
        }),
      }),
    );
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
      return requireProductRuntimeContract(await req("account", { method: "GET" }));
    },
    async cancelSubscription() {
      ensureRail("account");
      return requireImmediateCancellationResult(
        await req("account", {
          method: "POST",
          body: JSON.stringify({ action: "cancel_subscription" }),
        }),
      );
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
      return payloadWithRecordRefs(
        await req(`records${qs ? `?${qs}` : ""}`, { method: "GET" }),
      );
    },
    getRecord,
    readRecord,
    saveRecord,
    deleteRecord,

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
