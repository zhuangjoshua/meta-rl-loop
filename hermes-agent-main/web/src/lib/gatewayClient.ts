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
  | "closed"
  | "error";

interface Pending {
  resolve: (v: unknown) => void;
  reject: (e: Error) => void;
  timer: ReturnType<typeof setTimeout>;
}

const DEFAULT_REQUEST_TIMEOUT_MS = 120_000;

/** Wildcard listener key: subscribe to every event regardless of type. */
const ANY = "*";

export class GatewayClient {
  private ws: WebSocket | null = null;
  private reqId = 0;
  private pending = new Map<string, Pending>();
  private listeners = new Map<string, Set<(ev: GatewayEvent) => void>>();
  private closingSockets = new WeakSet<WebSocket>();
  private _state: ConnectionState = "idle";
  private stateListeners = new Set<(s: ConnectionState) => void>();
  private _lastCloseCode: number | null = null;
  private _lastCloseReason = "";
  private _lastCloseMessage = "";

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
    if (this._state === "open" || this._state === "connecting") return;
    this.setState("connecting");

    const resolved = token ?? window.__TAKYON_SESSION_TOKEN__ ?? "";
    if (!resolved) {
      this.setState("error");
      throw new Error(
        "Session token not available — page must be served by the Takyon dashboard",
      );
    }

    const scheme = location.protocol === "https:" ? "wss:" : "ws:";
    this._lastCloseCode = null;
    this._lastCloseReason = "";
    this._lastCloseMessage = "";
    const ws = new WebSocket(
      `${scheme}//${location.host}${TAKYON_BASE_PATH}/api/ws?token=${encodeURIComponent(resolved)}`,
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
        this.setState("error");
        reject(new Error("WebSocket connection failed"));
      };
      const onCloseBeforeOpen = (ev: CloseEvent) => {
        if (settled) return;
        settled = true;
        cleanup();
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
    this.rejectAllPending(new Error("Intercom reconnecting"));
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

      const err = msg.error as { message?: string } | undefined;
      if (err) p.reject(new Error(err.message ?? "request failed"));
      else p.resolve(msg.result);
      return;
    }

    if (msg.method !== "event") return;

    const params = (msg.params ?? {}) as GatewayEvent;
    if (typeof params.type !== "string") return;

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
    if (ev.code === 4401) return `Intercom unauthorized${suffix}`;
    if (ev.code === 4403) return `Intercom forbidden${suffix}`;
    return `Intercom disconnected${suffix}`;
  }

  /** Send a JSON-RPC request. Rejects on error response or timeout. */
  request<T = unknown>(
    method: string,
    params: Record<string, unknown> = {},
    timeoutMs = DEFAULT_REQUEST_TIMEOUT_MS,
  ): Promise<T> {
    if (!this.ws || this._state !== "open") {
      return Promise.reject(
        new Error(`gateway not connected (state=${this._state})`),
      );
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
}

declare global {
  interface Window {
    __TAKYON_SESSION_TOKEN__?: string;
  }
}
