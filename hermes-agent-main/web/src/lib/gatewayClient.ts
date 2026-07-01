/**
 * Browser WebSocket client for the tui_gateway JSON-RPC protocol.
 *
 * Speaks the exact same newline-delimited JSON-RPC dialect that the Ink TUI
 * drives over stdio. The server-side transport abstraction
 * (tui_gateway/transport.py + ws.py) routes the same dispatcher's writes
 * onto either stdout or a WebSocket depending on how the client connected.
 *
 *   const gw = new GatewayClient()
 *   await gw.connect()
 *   const { session_id } = await gw.request<{ session_id: string }>("session.create")
 *   gw.on("message.delta", (ev) => console.log(ev.payload?.text))
 *   await gw.request("prompt.submit", { session_id, text: "hi" })
 */

import { TAKYON_BASE_PATH } from "@/lib/api";

export type GatewayEventName =
  | "gateway.ready"
  | "session.info"
  | "message.start"
  | "message.delta"
  | "message.complete"
  | "thinking.delta"
  | "reasoning.delta"
  | "reasoning.available"
  | "status.update"
  | "tool.start"
  | "tool.progress"
  | "tool.complete"
  | "tool.generating"
  | "clarify.request"
  | "approval.request"
  | "sudo.request"
  | "secret.request"
  | "background.complete"
  | "error"
  | "skin.changed"
  | (string & {});

export interface GatewayEvent<P = unknown> {
  type: GatewayEventName;
  session_id?: string;
  payload?: P;
}

export type ConnectionState =
  | "idle"
  | "connecting"
  | "open"
  | "polling"
  | "closed"
  | "error";

interface Pending {
  resolve: (v: unknown) => void;
  reject: (e: Error) => void;
  timer: ReturnType<typeof setTimeout>;
}

const DEFAULT_REQUEST_TIMEOUT_MS = 120_000;
const HTTP_PREFERRED_METHODS = new Set([
  "takyon.dashboard.state",
  "takyon.dashboard.workspace",
  "takyon.file.read",
  "takyon.file.media",
  "takyon.files.list",
  "takyon.outputs.list",
  "takyon.site.preview",
]);

/** Wildcard listener key: subscribe to every event regardless of type. */
const ANY = "*";

/**
 * High-frequency streaming event types whose listener fan-out is batched via a
 * microtask so multiple deltas arriving in the same task coalesce into one
 * render tick instead of triggering a synchronous fan-out per WS frame.
 */
const BATCHED_EVENT_TYPES = new Set<string>([
  "message.delta",
  "reasoning.delta",
  "thinking.delta",
  "tool.progress",
]);

export class GatewayClient {
  private ws: WebSocket | null = null;
  private connectPromise: Promise<void> | null = null;
  private reqId = 0;
  private pending = new Map<string, Pending>();
  private listeners = new Map<string, Set<(ev: GatewayEvent) => void>>();
  private closingSockets = new WeakSet<WebSocket>();
  private _state: ConnectionState = "idle";
  private stateListeners = new Set<(s: ConnectionState) => void>();
  private _lastCloseCode: number | null = null;
  private _lastCloseReason = "";
  private _lastCloseMessage = "";
  private eventQueue: GatewayEvent[] = [];
  private flushScheduled = false;

  get state(): ConnectionState {
    return this._state;
  }

  get lastCloseCode(): number | null {
    return this._lastCloseCode;
  }

  get lastCloseReason(): string {
    return this._lastCloseReason;
  }

  get lastCloseMessage(): string {
    return this._lastCloseMessage;
  }

  private setState(s: ConnectionState) {
    if (this._state === s) return;
    this._state = s;
    for (const cb of this.stateListeners) cb(s);
  }

  onState(cb: (s: ConnectionState) => void): () => void {
    this.stateListeners.add(cb);
    cb(this._state);
    return () => this.stateListeners.delete(cb);
  }

  /** Subscribe to a specific event type. Returns an unsubscribe function. */
  on<P = unknown>(
    type: GatewayEventName,
    cb: (ev: GatewayEvent<P>) => void,
  ): () => void {
    let set = this.listeners.get(type);
    if (!set) {
      set = new Set();
      this.listeners.set(type, set);
    }
    set.add(cb as (ev: GatewayEvent) => void);
    return () => set!.delete(cb as (ev: GatewayEvent) => void);
  }

  /** Subscribe to every event (fires after type-specific listeners). */
  onAny(cb: (ev: GatewayEvent) => void): () => void {
    return this.on(ANY as GatewayEventName, cb);
  }

  async connect(token?: string): Promise<void> {
    if (this._state === "open") return;
    if (this._state === "connecting" && this.connectPromise) {
      return this.connectPromise;
    }
    this.connectPromise = this.doConnect(token).finally(() => {
      this.connectPromise = null;
    });
    return this.connectPromise;
  }

  private async doConnect(token?: string): Promise<void> {
    this.setState("connecting");

    const resolved = token ?? window.__TAKYON_SESSION_TOKEN__ ?? "";
    const scheme = location.protocol === "https:" ? "wss:" : "ws:";
    this._lastCloseCode = null;
    this._lastCloseReason = "";
    this._lastCloseMessage = "";
    const qs = resolved ? `?token=${encodeURIComponent(resolved)}` : "";
    const ws = new WebSocket(
      `${scheme}//${location.host}${TAKYON_BASE_PATH}/api/ws${qs}`,
    );
    this.ws = ws;

    // Register message + close BEFORE awaiting open — the server emits
    // `gateway.ready` immediately after accept, so a listener attached
    // after the open promise resolves can race past it and drop the
    // initial skin payload.
    ws.addEventListener("message", (ev) => {
      try {
        this.dispatch(JSON.parse(ev.data));
      } catch {
        /* malformed frame — ignore */
      }
    });

    ws.addEventListener("close", (ev) => {
      const intentional = this.closingSockets.has(ws);
      if (intentional) this.closingSockets.delete(ws);
      const current = this.ws === ws;
      if (current) this.ws = null;
      if (intentional || !current) return;
      this._lastCloseCode = ev.code || null;
      this._lastCloseReason = ev.reason || "";
      this._lastCloseMessage = this.describeClose(ev);
      this.setState(ev.code === 4401 ? "error" : "closed");
      this.rejectAllPending(new Error(this._lastCloseMessage));
    });

    await new Promise<void>((resolve, reject) => {
      let settled = false;
      const cleanup = () => {
        ws.removeEventListener("open", onOpen);
        ws.removeEventListener("error", onError);
        ws.removeEventListener("close", onCloseBeforeOpen);
      };
      const onOpen = () => {
        if (settled) return;
        settled = true;
        cleanup();
        this.setState("open");
        resolve();
      };
      const onError = () => {
        if (settled) return;
        settled = true;
        cleanup();
        this.setState("polling");
        reject(new Error("WebSocket connection failed"));
      };
      const onCloseBeforeOpen = (ev: CloseEvent) => {
        if (settled) return;
        settled = true;
        cleanup();
        this.setState(ev.code === 4401 || ev.code === 4403 ? "error" : "polling");
        reject(new Error(this.describeClose(ev)));
      };
      ws.addEventListener("open", onOpen, { once: true });
      ws.addEventListener("error", onError, { once: true });
      ws.addEventListener("close", onCloseBeforeOpen, { once: true });
    });
  }

  close() {
    const ws = this.ws;
    if (!ws) return;
    this.closingSockets.add(ws);
    this.ws = null;
    this.rejectAllPending(new Error("Live stream reconnecting"));
    if (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING) {
      ws.close(1000, "client_close");
    }
    this.setState("idle");
  }

  private dispatch(msg: Record<string, unknown>) {
    const id = msg.id as string | undefined;

    if (id !== undefined && this.pending.has(id)) {
      const p = this.pending.get(id)!;
      this.pending.delete(id);
      clearTimeout(p.timer);

      const err = msg.error as { message?: string; code?: number } | undefined;
      if (err) {
        // Preserve the JSON-RPC numeric error code on the thrown Error so callers
        // (e.g. createBusiness) can branch on it — 4030 out-of-credits vs 5051 hard error.
        const e = new Error(err.message ?? "request failed");
        if (typeof err.code === "number") Object.assign(e, { code: err.code });
        p.reject(e);
      } else p.resolve(msg.result);
      return;
    }

    if (msg.method !== "event") return;

    const params = (msg.params ?? {}) as GatewayEvent;
    if (typeof params.type !== "string") return;

    // High-frequency streaming events are queued and flushed together on a
    // microtask so bursts of deltas coalesce into one render tick. Ordering is
    // preserved: once anything is queued, subsequent events also queue until
    // the flush drains, so no event can jump ahead of an earlier one.
    if (this.flushScheduled || BATCHED_EVENT_TYPES.has(params.type)) {
      this.eventQueue.push(params);
      this.scheduleFlush();
      return;
    }

    this.emitEvent(params);
  }

  private scheduleFlush() {
    if (this.flushScheduled) return;
    this.flushScheduled = true;
    queueMicrotask(() => this.flushEvents());
  }

  private flushEvents() {
    this.flushScheduled = false;
    const queued = this.eventQueue;
    this.eventQueue = [];
    for (const ev of queued) this.emitEvent(ev);
  }

  private emitEvent(params: GatewayEvent) {
    for (const cb of this.listeners.get(params.type) ?? []) cb(params);
    for (const cb of this.listeners.get(ANY) ?? []) cb(params);
  }

  private rejectAllPending(err: Error) {
    for (const p of this.pending.values()) {
      clearTimeout(p.timer);
      p.reject(err);
    }
    this.pending.clear();
  }

  private describeClose(ev: CloseEvent): string {
    const suffix = ev.reason ? `: ${ev.reason}` : ev.code ? ` (${ev.code})` : "";
    if (ev.code === 4401) return `Live stream unauthorized${suffix}`;
    if (ev.code === 4403) return `Live stream forbidden${suffix}`;
    return `Live stream disconnected${suffix}`;
  }

  /** Send a JSON-RPC request. Rejects on error response or timeout. */
  async request<T = unknown>(
    method: string,
    params: Record<string, unknown> = {},
    timeoutMs = DEFAULT_REQUEST_TIMEOUT_MS,
  ): Promise<T> {
    if (HTTP_PREFERRED_METHODS.has(method)) {
      return this.requestHttp<T>(method, params, timeoutMs);
    }
    // During a transient reconnect window, briefly await the in-progress
    // connect() so bursts of RPCs coalesce back onto the single WebSocket
    // once it opens instead of each spawning a fresh HTTP request.
    if (this._state !== "open" && this.connectPromise) {
      await this.connectPromise.catch(() => {});
    }
    if (!this.ws || this._state !== "open") {
      return this.requestHttp<T>(method, params, timeoutMs);
    }

    const id = `w${++this.reqId}`;

    return new Promise<T>((resolve, reject) => {
      const timer = setTimeout(() => {
        if (this.pending.delete(id)) {
          reject(new Error(`request timed out: ${method}`));
        }
      }, timeoutMs);

      this.pending.set(id, {
        resolve: (v) => resolve(v as T),
        reject,
        timer,
      });

      try {
        this.ws!.send(JSON.stringify({ jsonrpc: "2.0", id, method, params }));
      } catch (e) {
        clearTimeout(timer);
        this.pending.delete(id);
        reject(e instanceof Error ? e : new Error(String(e)));
      }
    });
  }

  private async requestHttp<T>(
    method: string,
    params: Record<string, unknown>,
    timeoutMs: number,
  ): Promise<T> {
    const resolved = window.__TAKYON_SESSION_TOKEN__ ?? "";
    const id = `h${++this.reqId}`;
    const controller = new AbortController();
    const timer = window.setTimeout(() => controller.abort(), timeoutMs);
    const headers: Record<string, string> = {
      "Content-Type": "application/json",
    };
    if (resolved) {
      headers["X-Takyon-Session-Token"] = resolved;
    }

    try {
      const res = await fetch(`${TAKYON_BASE_PATH}/api/tui/rpc`, {
        method: "POST",
        headers,
        body: JSON.stringify({ jsonrpc: "2.0", id, method, params }),
        credentials: "same-origin",
        signal: controller.signal,
      });
      if (!res.ok) {
        const text = await res.text().catch(() => res.statusText);
        throw new Error(`${res.status}: ${text}`);
      }
      const msg = await res.json();
      const err = msg.error as { message?: string; code?: number } | undefined;
      if (err) {
        // Preserve the JSON-RPC numeric error code on the thrown Error so callers
        // (e.g. createBusiness) can branch on it — 4030 out-of-credits vs 5051 hard error.
        const e = new Error(err.message ?? "request failed");
        if (typeof err.code === "number") Object.assign(e, { code: err.code });
        throw e;
      }
      if (this._state !== "open") this.setState("polling");
      return msg.result as T;
    } catch (err) {
      if (this._state !== "open") this.setState("error");
      if (err instanceof DOMException && err.name === "AbortError") {
        throw new Error(`request timed out: ${method}`);
      }
      throw err instanceof Error ? err : new Error(String(err));
    } finally {
      window.clearTimeout(timer);
    }
  }
}

declare global {
  interface Window {
    __TAKYON_SESSION_TOKEN__?: string;
  }
}
