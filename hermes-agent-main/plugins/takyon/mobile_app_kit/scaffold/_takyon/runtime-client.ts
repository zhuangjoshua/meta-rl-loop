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

declare const recordRefBrand: unique symbol;
export type RecordRef = string & { readonly [recordRefBrand]: "TakyonRecordRef" };

export interface AppRecord {
  id: string;
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
  [key: string]: unknown;
}

export interface RecordListResponse {
  records: AppRecord[];
  [key: string]: unknown;
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
      record_type?: string;
      type?: string;
    }
);

const RECORD_REF_PREFIX = "takyon-record-v1.";

function isObject(value: unknown): value is Record<string, any> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function recordRefFromRecord(record: unknown): RecordRef | "" {
  if (!isObject(record)) return "";
  const type = String(record.type || record.record_type || "").trim();
  const id = String(record.id || record.record_id || "").trim();
  if (!type || !id) return "";
  // Preserve a runtime-supplied canonical ref byte-for-byte. The deterministic v1 encoding is
  // only the compatibility path for older record responses that still expose type/id alone.
  const existingRef = typeof record.ref === "string" ? record.ref : "";
  if (existingRef) {
    try {
      const existingKey = recordKeyFromRef(existingRef as RecordRef);
      if (existingKey.type === type && existingKey.id === id) return existingRef as RecordRef;
    } catch {
      // Ignore a malformed/stale legacy response ref and derive the compatible v1 locator below.
    }
  }
  return `${RECORD_REF_PREFIX}${encodeURIComponent(JSON.stringify([1, type, id]))}` as RecordRef;
}

function recordKeyFromRef(ref: RecordRef): { type: string; id: string } {
  if (typeof ref !== "string" || !ref.startsWith(RECORD_REF_PREFIX)) {
    throw new TypeError(
      "record_ref is invalid; pass the ref returned by saveRecord, listRecords, or readRecord",
    );
  }
  try {
    const decoded: unknown = JSON.parse(decodeURIComponent(ref.slice(RECORD_REF_PREFIX.length)));
    if (
      !Array.isArray(decoded) ||
      decoded.length !== 3 ||
      decoded[0] !== 1 ||
      typeof decoded[1] !== "string" ||
      !decoded[1].trim() ||
      typeof decoded[2] !== "string" ||
      !decoded[2].trim()
    ) {
      throw new Error("invalid record ref payload");
    }
    return { type: decoded[1], id: decoded[2] };
  } catch {
    throw new TypeError(
      "record_ref is invalid; pass the ref returned by saveRecord, listRecords, or readRecord",
    );
  }
}

function recordWithRef(record: unknown): unknown {
  if (!isObject(record)) return record;
  const ref = recordRefFromRecord(record);
  return ref ? { ...record, ref } : record;
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
  cancelSubscription(): Promise<any>;
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

  async function getRecord(ref: RecordRef): Promise<RecordResponse>;
  async function getRecord(type: string, id: string): Promise<RecordResponse>;
  async function getRecord(refOrType: RecordRef | string, legacyId?: string): Promise<RecordResponse> {
    ensureRail("records");
    const key = legacyId === undefined
      ? recordKeyFromRef(refOrType as RecordRef)
      : { type: String(refOrType || "").trim(), id: String(legacyId || "").trim() };
    return payloadWithRecordRefs(
      await req(`records/${encodeURIComponent(key.type)}/${encodeURIComponent(key.id)}`, {
        method: "GET",
      }),
    );
  }

  async function readRecord(ref: RecordRef): Promise<RecordResponse> {
    ensureRail("records");
    const key = recordKeyFromRef(ref);
    return payloadWithRecordRefs(
      await req(`records/${encodeURIComponent(key.type)}/${encodeURIComponent(key.id)}`, {
        method: "GET",
      }),
    );
  }

  async function deleteRecord(ref: RecordRef): Promise<RecordResponse>;
  async function deleteRecord(type: string, id: string): Promise<RecordResponse>;
  async function deleteRecord(refOrType: RecordRef | string, legacyId?: string): Promise<RecordResponse> {
    ensureRail("records");
    const key = legacyId === undefined
      ? recordKeyFromRef(refOrType as RecordRef)
      : { type: String(refOrType || "").trim(), id: String(legacyId || "").trim() };
    return payloadWithRecordRefs(
      await req(`records/${encodeURIComponent(key.type)}/${encodeURIComponent(key.id)}`, {
        method: "DELETE",
      }),
    );
  }

  async function saveRecord(payload: Record<string, any> = {}): Promise<RecordResponse> {
    ensureRail("records");
    if (!Object.prototype.hasOwnProperty.call(payload, "data") || payload.data == null) {
      throw new TypeError("data is required");
    }
    const suppliedRefs = [payload.ref, payload.record_ref]
      .map((value) => String(value || "").trim())
      .filter(Boolean);
    if (new Set(suppliedRefs).size > 1) {
      throw new TypeError("ref does not match the supplied record_ref");
    }
    const suppliedRef = suppliedRefs[0] || "";
    const refKey = suppliedRef ? recordKeyFromRef(suppliedRef as RecordRef) : null;
    const suppliedTypes = [payload.record_type, payload.type]
      .map((value) => String(value || "").trim())
      .filter(Boolean);
    const legacyRecordIds = [payload.record_id, payload.id]
      .map((value) => String(value || "").trim())
      .filter(Boolean);
    if (new Set(suppliedTypes).size > 1) {
      throw new TypeError("record_type does not match the supplied type");
    }
    if (new Set(legacyRecordIds).size > 1) {
      throw new TypeError("record_id does not match the supplied id");
    }
    const suppliedType = suppliedTypes[0] || "";
    const legacyRecordId = legacyRecordIds[0] || "";
    if (refKey && suppliedTypes.some((value) => value !== refKey.type)) {
      throw new TypeError("record_type does not match the supplied record ref");
    }
    if (refKey && legacyRecordIds.some((value) => value !== refKey.id)) {
      throw new TypeError("record_id does not match the supplied record ref");
    }
    const recordType = refKey ? refKey.type : suppliedType;
    if (!recordType) throw new Error("record_type is required");
    // Raw IDs remain accepted by the runtime implementation for already-published apps, but the
    // MobileRuntimeClient type exposes only RecordRef updates to newly generated product code.
    const recordId = refKey ? refKey.id : legacyRecordId;
    const route = recordId
      ? `records/${encodeURIComponent(recordType)}/${encodeURIComponent(recordId)}`
      : "records";
    const { ref: _ref, record_ref: _recordRef, ...recordPayload } = payload;
    return payloadWithRecordRefs(
      await req(route, {
        method: "POST",
        body: JSON.stringify({
          ...recordPayload,
          record_type: recordType,
          record_id: recordId || undefined,
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
      return req("account", { method: "GET" });
    },
    async cancelSubscription() {
      ensureRail("account");
      return req("account", {
        method: "POST",
        body: JSON.stringify({ action: "cancel_subscription" }),
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
