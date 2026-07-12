// PLATFORM-OWNED — generated products consume this declaration through their materialized
// _takyon/ boundary. Product code may import its types but must not replace this contract.
declare const recordRefBrand: unique symbol;

/**
 * An opaque, owner-scoped record locator returned by the Takyon runtime.
 *
 * This is a locator, not an authorization capability. Every read is still authorized against the
 * current product session. App code must preserve this value and must not construct one itself.
 */
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
  /** Raw runtime IDs are not part of the generated-product SDK. Preserve and pass `ref`. */
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

export type RuntimePayload = Record<string, any>;

export interface ActionRunner {
  readonly action: string;
  state(): "idle" | "pending";
  run(payload?: RuntimePayload, options?: RuntimePayload): Promise<any>;
}

export interface SubuserRuntimeClient {
  readonly context: RuntimePayload;
  routeUrl(route: string): string;
  railStateFor(rail: string): string;
  isRailCallable(rail: string): boolean;

  buildVerifyUrl(): never;
  requestAuth(): Promise<never>;
  loginWithSupabase(accessToken: string, extra?: RuntimePayload): Promise<any>;
  logout(): Promise<any>;
  session(): Promise<any>;

  account(): Promise<any>;
  cancelSubscription(): Promise<any>;
  deleteAccount(): Promise<any>;
  profile(): Promise<any>;
  updateProfile(payload?: RuntimePayload): Promise<any>;

  listDirectory(options?: RuntimePayload): Promise<any>;
  getDirectoryMe(): Promise<any>;
  getDirectoryEntry(appUserId: string): Promise<any>;
  updateDirectoryMe(payload?: RuntimePayload): Promise<any>;
  disableDirectoryMe(): Promise<any>;

  listRecords(options?: Record<string, unknown>): Promise<RecordListResponse>;

  /** Read using the exact ref returned by saveRecord, listRecords, or an earlier read. */
  getRecord(ref: RecordRef): Promise<RecordResponse>;

  /** Strict RecordRef-only spelling for new generated product code. */
  readRecord(ref: RecordRef): Promise<RecordResponse>;

  /** Create by record_type, or update by the exact opaque `ref` returned by the runtime. */
  saveRecord(payload: SaveRecordPayload): Promise<RecordResponse>;

  deleteRecord(ref: RecordRef): Promise<RecordResponse>;

  checkout(payload?: RuntimePayload): Promise<any>;
  recordUsage(payload?: RuntimePayload): Promise<any>;
  uploadMedia(file: unknown): Promise<any>;
  mediaUrl(id: string): string;
  deleteMedia(id: string): Promise<any>;

  listConnections(options?: RuntimePayload): Promise<any>;
  actOnConnection(payload?: RuntimePayload): Promise<any>;

  generate(payload?: RuntimePayload): Promise<any>;
  search(payload?: RuntimePayload): Promise<any>;
  egress(payload?: RuntimePayload): Promise<any>;
  invokeAction(name: string, payload?: RuntimePayload, options?: RuntimePayload): Promise<any>;
  createActionRunner(name: string): ActionRunner;
  usageFromAccount(accountPayload?: RuntimePayload): RuntimePayload | null;
}

export function resolveSubuserRuntimeBase(config?: Record<string, unknown>): string;

export function createSubuserRuntimeClient(
  context?: Record<string, any>,
): SubuserRuntimeClient;

export const DEFAULT_FRONTEND_API_MODE: string;
