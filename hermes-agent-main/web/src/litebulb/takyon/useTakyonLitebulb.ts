import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  api,
  type TakyonBusinessCreativeCreditsResponse,
  type TakyonBusinessTractionResponse,
  type TakyonBusinessWorkspaceResponse,
  type TakyonOperatorAccountResponse,
  type TakyonOperatorBusinessSummary,
} from "@/lib/api";
import { GatewayClient } from "@/lib/gatewayClient";
import {
  businessHasShipped,
  deriveLiveWorkstreamCard,
  type LiveWorkstreamCardData,
  type ProgressToolSignal,
} from "@/lib/takyonCeoUpdates";
import { useAuth } from "../auth/useAuth";

export type ChatMessage = {
  id: string;
  who: "agent" | "user";
  text: string;
  working?: boolean;
  // Wall-clock ms for ordering against the curated chat_stream (whose items
  // carry an ISO posted_at). User messages are stamped with Date.now() on send;
  // messages reconstructed from server history have no timestamp (the history
  // payload carries no created_at) and fall back to insertion order.
  ts?: number;
};

export type ChatProgress = LiveWorkstreamCardData & {
  live: boolean;
};

type LiveChatSignals = {
  statusItems: string[];
  progressLines: string[];
  tools: ProgressToolSignal[];
};

export type BuildStatus = "idle" | "creating" | "ready" | "error";

export type BuildState = {
  status: BuildStatus;
  goal: string;
  businessSlug: string;
  businessName: string;
  narration: string[];
  terminal: string[];
  error: string;
  // Numeric JSON-RPC error code from takyon.dashboard.create on failure (0 = none).
  // 4030 = out of credits (route to Plans & Billing); other codes = generic hard error.
  errorCode: number;
};

export type LitebulbBusiness = {
  slug: string;
  name: string;
  goal: string;
  mode: string;
  status: string;
  tagline: string;
  meta: string;
  // Real published product URL (https://<slug>.coscale.app or a recorded
  // public_url). Always populated from the canonical host so the company card
  // never shows a fabricated `.app` placeholder.
  productUrl: string;
  // Published brand-logo asset path (e.g. product/brand/logos/brand-logo.png),
  // when a paid logo has been generated for the business. Empty until then —
  // the card falls back to a monogram chip.
  logoPath: string;
  // True when the business's autonomous CEO wake loop is paused. Derived from the
  // canonical wake gate (wake_schedules.enabled) via the operator businesses payload.
  wakesPaused?: boolean;
};

type HistoryPayload = {
  messages?: Array<{ role?: string; text?: string }>;
  running?: boolean;
};

type SessionResumePayload = {
  session_id?: string;
  resumed?: string;
  messages?: Array<{ role?: string; text?: string }>;
};

type SessionTitlePayload = {
  session_key?: string;
};

const LITEBULB_SESSION_STORAGE_KEY = "takyon.litebulb.sessions.v1";
// Stale-while-revalidate cache. Last-known workspace / creative-credits / traction
// snapshots persist across reloads in localStorage so a warm reload renders
// instantly with real data while the network revalidation runs in the
// background — no blank flash, no "unavailable" error-flash during normal load.
const LITEBULB_SWR_CACHE_KEY = "takyon.litebulb.swr.v1";
// Cap each cached entry's age so a long-stale snapshot is treated as a cold
// load (revalidate without painting stale numbers as if fresh).
const LITEBULB_SWR_MAX_AGE_MS = 24 * 60 * 60 * 1000;
const LITEBULB_PENDING_TURN_STORAGE_KEY = "takyon.litebulb.pendingTurns.v1";
const LITEBULB_PENDING_CREATIVE_CREDIT_CHECKOUT_STORAGE_KEY =
  "takyon.litebulb.pendingCreativeCreditCheckout.v1";
const LITEBULB_CREATIVE_CREDIT_CHECKOUT_SESSION_QUERY_KEY =
  "creative_credit_checkout_session_id";

type PendingTurn = {
  id: string;
  text: string;
  createdAt: number;
  userCountBefore: number;
};

type PendingCreativeCreditCheckout = {
  businessSlug: string;
  sessionId: string;
  credits: number;
  createdAt: number;
};

function trimText(value: unknown) {
  return String(value || "").trim();
}

function rawText(value: unknown) {
  return value == null ? "" : String(value);
}

function titleCaseSlug(value: string) {
  return value
    .split(/[-_\s]+/)
    .filter(Boolean)
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(" ");
}

function wait(ms: number) {
  return new Promise((resolve) => window.setTimeout(resolve, ms));
}

function isBusyError(error: unknown) {
  const message = error instanceof Error ? error.message : String(error);
  return /session busy|busy|4009/i.test(message);
}

function isMissingSessionError(error: unknown) {
  const message = error instanceof Error ? error.message : String(error);
  return /session not found|4001/i.test(message);
}

// Canonical product domain — product sub-apps are served at
// `<slug>.coscale.app` (mirrors product/Product.tsx canonicalProductHost and
// core._product_publish_target). Used to derive a REAL product URL for the
// company card whenever the backend payload omits an explicit public_url.
const PRODUCT_BASE_DOMAIN = "coscale.app";

function canonicalProductUrl(slug: string) {
  const clean = (slug || "").toLowerCase().replace(/[^a-z0-9-]/g, "");
  return clean ? `https://${clean}.${PRODUCT_BASE_DOMAIN}` : "";
}

function businessMeta(item: TakyonOperatorBusinessSummary) {
  const mode = trimText(item.mode || item.state || item.status || "live");
  if (!mode) return "Business";
  return `${mode.charAt(0).toUpperCase()}${mode.slice(1)} mode`;
}

function mapBusiness(item: TakyonOperatorBusinessSummary): LitebulbBusiness {
  const slug = trimText(item.slug).toLowerCase();
  const name = trimText(item.name) || titleCaseSlug(slug || "business");
  const goal = trimText(item.goal);
  // Prefer a backend-recorded public URL; otherwise derive the canonical host so
  // the card embed/address always points at the REAL product, never a `.app`.
  const productUrl = trimText(item.product_url) || canonicalProductUrl(slug);
  // Logo is the recorded published-asset PATH (resolved through the authenticated
  // /asset endpoint by the card). Empty until a paid logo is generated.
  const logoPath = trimText(item.logo_url);
  return {
    slug,
    name,
    goal,
    mode: trimText(item.mode || item.state || item.status || "live") || "live",
    status: trimText(item.status || item.state || "active") || "active",
    tagline: goal || name,
    meta: businessMeta(item),
    productUrl,
    logoPath,
    wakesPaused: Boolean(item.wakes_paused),
  };
}

function pushUniqueLine(lines: string[], next: string, limit = 18) {
  const text = trimText(next);
  if (!text) return lines;
  if (lines[lines.length - 1] === text) return lines;
  return [...lines, text].slice(-limit);
}

function pushUniqueToolSignal(
  signals: ProgressToolSignal[],
  next: ProgressToolSignal,
  limit = 12,
) {
  const normalized: ProgressToolSignal = {
    name: trimText(next.name),
    label: trimText(next.label),
    context: trimText(next.context),
    preview: trimText(next.preview),
    summary: trimText(next.summary),
    error: trimText(next.error),
    status: trimText(next.status),
  };
  const key = JSON.stringify(normalized);
  const previous = signals[signals.length - 1];
  if (previous && JSON.stringify(previous) === key) return signals;
  return [...signals, normalized].slice(-limit);
}

function shouldMirrorStatusInChat(kind: unknown) {
  const value = trimText(kind).toLowerCase();
  return !value || value === "takyon" || value === "process" || value === "status";
}

function mapHistoryMessages(payload: HistoryPayload | null | undefined): ChatMessage[] {
  const messages = Array.isArray(payload?.messages) ? payload.messages : [];
  const mapped: ChatMessage[] = [];
  let previousKey = "";
  messages.forEach((item, index) => {
    const role = trimText(item?.role).toLowerCase();
    const text = trimText(item?.text);
    if (!text) return;
    const who = role === "user" ? "user" : role === "assistant" ? "agent" : "";
    if (!who) return;
    const key = `${who}\n${text}`;
    if (key === previousKey) return;
    previousKey = key;
    mapped.push({
      id: `history-${who}-${index}`,
      who,
      text,
    });
  });

  if (!payload?.running) return mapped;

  const lastAssistantIndex = [...mapped].reverse().findIndex((item) => item.who === "agent");
  if (lastAssistantIndex === -1) return mapped;
  const realIndex = mapped.length - 1 - lastAssistantIndex;
  return mapped.map((item, index) => (index === realIndex ? { ...item, working: true } : item));
}

function historyHasPendingReply(payload: HistoryPayload | null | undefined) {
  const messages = Array.isArray(payload?.messages) ? payload.messages : [];
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    const role = trimText(messages[index]?.role).toLowerCase();
    if (role === "assistant") return false;
    if (role === "user") return true;
  }
  return false;
}

function latestAssistantReply(messages: ChatMessage[]) {
  let lastUserIndex = -1;
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    if (messages[index]?.who === "user") {
      lastUserIndex = index;
      break;
    }
  }
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    const message = messages[index];
    if (message?.who !== "agent") continue;
    if (index < lastUserIndex) return null;
    return message;
  }
  return null;
}

function mergeHistoryMessages(prev: ChatMessage[], next: ChatMessage[]): ChatMessage[] {
  if (!next.length) return prev;
  const liveWorkingAssistant = [...prev].reverse().find((message) => message.who === "agent" && message.working);
  const trailingAssistant = latestAssistantReply(next);
  const replaceWorkingAssistant = Boolean(
    liveWorkingAssistant
      && trailingAssistant
      && trimText(trailingAssistant.text).length >= trimText(liveWorkingAssistant.text).length,
  );
  const base = replaceWorkingAssistant
    ? prev.filter((message) => !(message.who === "agent" && message.working))
    : [...prev];
  const seen = new Set(base.map((message) => `${message.who}\n${message.text}`));
  for (const message of next) {
    // While a working assistant is still streaming richer text than the poll
    // snapshot, do NOT also push the snapshot's trailing assistant — that would
    // render a duplicate (flickering) agent bubble. Keep the live streaming one
    // until history confirms the final (>= length) reply, which the
    // replaceWorkingAssistant branch above adopts.
    if (
      !replaceWorkingAssistant
      && liveWorkingAssistant
      && message === trailingAssistant
      && message.who === "agent"
    ) {
      continue;
    }
    const key = `${message.who}\n${message.text}`;
    if (seen.has(key)) continue;
    seen.add(key);
    base.push(message);
  }
  return base;
}

function readStoredPendingTurns(): Record<string, PendingTurn> {
  if (typeof window === "undefined") return {};
  try {
    const raw = window.sessionStorage.getItem(LITEBULB_PENDING_TURN_STORAGE_KEY);
    if (!raw) return {};
    const parsed = JSON.parse(raw);
    return parsed && typeof parsed === "object" ? parsed as Record<string, PendingTurn> : {};
  } catch {
    return {};
  }
}

function readStoredPendingTurn(slug: string): PendingTurn | null {
  const businessSlug = trimText(slug).toLowerCase();
  if (!businessSlug) return null;
  const pending = readStoredPendingTurns()[businessSlug];
  if (!pending || typeof pending !== "object") return null;
  const text = trimText(pending.text);
  const id = trimText(pending.id);
  if (!text || !id) return null;
  return {
    id,
    text,
    createdAt: Number(pending.createdAt || 0) || Date.now(),
    userCountBefore: Number.isFinite(Number((pending as { userCountBefore?: unknown }).userCountBefore))
      ? Math.max(0, Number((pending as { userCountBefore?: unknown }).userCountBefore))
      : -1,
  };
}

function writeStoredPendingTurn(slug: string, pendingTurn: PendingTurn) {
  if (typeof window === "undefined") return;
  const businessSlug = trimText(slug).toLowerCase();
  if (!businessSlug) return;
  try {
    const next = readStoredPendingTurns();
    next[businessSlug] = pendingTurn;
    window.sessionStorage.setItem(LITEBULB_PENDING_TURN_STORAGE_KEY, JSON.stringify(next));
  } catch {
    /* best effort */
  }
}

function clearStoredPendingTurn(slug: string) {
  if (typeof window === "undefined") return;
  const businessSlug = trimText(slug).toLowerCase();
  if (!businessSlug) return;
  try {
    const next = readStoredPendingTurns();
    if (!(businessSlug in next)) return;
    delete next[businessSlug];
    window.sessionStorage.setItem(LITEBULB_PENDING_TURN_STORAGE_KEY, JSON.stringify(next));
  } catch {
    /* best effort */
  }
}

function readStoredPendingCreativeCreditCheckout(): PendingCreativeCreditCheckout | null {
  if (typeof window === "undefined") return null;
  try {
    const raw = window.sessionStorage.getItem(LITEBULB_PENDING_CREATIVE_CREDIT_CHECKOUT_STORAGE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw);
    return parsed && typeof parsed === "object" ? parsed as PendingCreativeCreditCheckout : null;
  } catch {
    return null;
  }
}

function writeStoredPendingCreativeCreditCheckout(value: PendingCreativeCreditCheckout) {
  if (typeof window === "undefined") return;
  try {
    window.sessionStorage.setItem(
      LITEBULB_PENDING_CREATIVE_CREDIT_CHECKOUT_STORAGE_KEY,
      JSON.stringify(value),
    );
  } catch {
    /* best effort */
  }
}

function clearStoredPendingCreativeCreditCheckout() {
  if (typeof window === "undefined") return;
  try {
    window.sessionStorage.removeItem(LITEBULB_PENDING_CREATIVE_CREDIT_CHECKOUT_STORAGE_KEY);
  } catch {
    /* best effort */
  }
}

function readCreativeCreditCheckoutSessionIdFromUrl() {
  if (typeof window === "undefined") return "";
  try {
    const url = new URL(window.location.href);
    return trimText(url.searchParams.get(LITEBULB_CREATIVE_CREDIT_CHECKOUT_SESSION_QUERY_KEY));
  } catch {
    return "";
  }
}

function clearCreativeCreditCheckoutSessionIdFromUrl() {
  if (typeof window === "undefined") return;
  try {
    const url = new URL(window.location.href);
    if (!url.searchParams.has(LITEBULB_CREATIVE_CREDIT_CHECKOUT_SESSION_QUERY_KEY)) return;
    url.searchParams.delete(LITEBULB_CREATIVE_CREDIT_CHECKOUT_SESSION_QUERY_KEY);
    window.history.replaceState(window.history.state, "", `${url.pathname}${url.search}${url.hash}`);
  } catch {
    /* best effort */
  }
}

function historyUserTexts(payload: HistoryPayload | null | undefined) {
  const messages = Array.isArray(payload?.messages) ? payload.messages : [];
  return messages
    .filter((item) => trimText(item?.role).toLowerCase() === "user")
    .map((item) => trimText(item?.text))
    .filter(Boolean);
}

function historyHasPendingTurn(
  payload: HistoryPayload | null | undefined,
  pendingTurn: PendingTurn | null,
) {
  const target = trimText(pendingTurn?.text);
  if (!target) return false;
  if (!pendingTurn) return false;
  const userTexts = historyUserTexts(payload);
  if (!Number.isFinite(pendingTurn.userCountBefore) || pendingTurn.userCountBefore < 0) {
    return false;
  }
  const nextUserText = userTexts[pendingTurn.userCountBefore];
  return nextUserText === target;
}

function pendingTurnMessage(pendingTurn: PendingTurn): ChatMessage {
  return {
    id: pendingTurn.id,
    who: "user",
    text: pendingTurn.text,
    // The send-time wall clock so the user bubble orders correctly against the
    // CEO's curated chat_stream narration (which is keyed by ISO posted_at).
    ts: Number(pendingTurn.createdAt) || Date.now(),
  };
}

function readStoredLitebulbSessions(): Record<string, string> {
  if (typeof window === "undefined") return {};
  try {
    const raw = window.sessionStorage.getItem(LITEBULB_SESSION_STORAGE_KEY);
    if (!raw) return {};
    const parsed = JSON.parse(raw);
    return parsed && typeof parsed === "object" ? parsed as Record<string, string> : {};
  } catch {
    return {};
  }
}

function readStoredLitebulbSession(slug: string) {
  const businessSlug = trimText(slug).toLowerCase();
  if (!businessSlug) return "";
  return trimText(readStoredLitebulbSessions()[businessSlug]);
}

function writeStoredLitebulbSession(slug: string, sessionId: string) {
  if (typeof window === "undefined") return;
  const businessSlug = trimText(slug).toLowerCase();
  const value = trimText(sessionId);
  if (!businessSlug || !value) return;
  try {
    const next = readStoredLitebulbSessions();
    next[businessSlug] = value;
    window.sessionStorage.setItem(LITEBULB_SESSION_STORAGE_KEY, JSON.stringify(next));
  } catch {
    /* best effort */
  }
}

function clearStoredLitebulbSession(slug: string) {
  if (typeof window === "undefined") return;
  const businessSlug = trimText(slug).toLowerCase();
  if (!businessSlug) return;
  try {
    const next = readStoredLitebulbSessions();
    if (!(businessSlug in next)) return;
    delete next[businessSlug];
    window.sessionStorage.setItem(LITEBULB_SESSION_STORAGE_KEY, JSON.stringify(next));
  } catch {
    /* best effort */
  }
}

type SwrSlot = "workspace" | "creativeCredits" | "traction";

type SwrCacheEntry = {
  workspace?: { at: number; value: TakyonBusinessWorkspaceResponse };
  creativeCredits?: { at: number; value: TakyonBusinessCreativeCreditsResponse };
  traction?: { at: number; value: TakyonBusinessTractionResponse };
};

function readSwrCache(): Record<string, SwrCacheEntry> {
  if (typeof window === "undefined") return {};
  try {
    const raw = window.localStorage.getItem(LITEBULB_SWR_CACHE_KEY);
    if (!raw) return {};
    const parsed = JSON.parse(raw);
    return parsed && typeof parsed === "object" ? (parsed as Record<string, SwrCacheEntry>) : {};
  } catch {
    return {};
  }
}

function readCachedSlot<T>(slug: string, slot: SwrSlot): T | null {
  const businessSlug = trimText(slug).toLowerCase();
  if (!businessSlug) return null;
  const entry = readSwrCache()[businessSlug];
  const cached = entry ? (entry as Record<string, { at: number; value: unknown }>)[slot] : undefined;
  if (!cached || typeof cached !== "object") return null;
  if (!Number.isFinite(cached.at) || Date.now() - Number(cached.at) > LITEBULB_SWR_MAX_AGE_MS) {
    return null;
  }
  return (cached.value as T) ?? null;
}

function writeCachedSlot(slug: string, slot: SwrSlot, value: unknown) {
  if (typeof window === "undefined") return;
  const businessSlug = trimText(slug).toLowerCase();
  if (!businessSlug || value == null) return;
  try {
    const cache = readSwrCache();
    const entry: SwrCacheEntry = { ...(cache[businessSlug] || {}) };
    (entry as Record<string, { at: number; value: unknown }>)[slot] = { at: Date.now(), value };
    cache[businessSlug] = entry;
    // Bound the cache to the most recent businesses so localStorage cannot grow
    // without limit across many created companies.
    const keys = Object.keys(cache);
    if (keys.length > 24) {
      for (const stale of keys.slice(0, keys.length - 24)) delete cache[stale];
    }
    window.localStorage.setItem(LITEBULB_SWR_CACHE_KEY, JSON.stringify(cache));
  } catch {
    /* best effort */
  }
}

function createEmptyBuildState(): BuildState {
  return {
    status: "idle",
    goal: "",
    businessSlug: "",
    businessName: "",
    narration: [],
    terminal: [],
    error: "",
    errorCode: 0,
  };
}

// Turn a raw create failure into a clean, human, customer-safe sentence. Strips
// the leading JSON-RPC `<code>:` prefix and any tool/path internals; gives a
// purpose-built line for the known codes (4030 out-of-credits, 4004 input/bounce).
function humanizeCreateError(error: unknown, code: number): string {
  if (code === 4030) {
    return "You’re out of credits. Subscribe to a plan to create a company.";
  }
  const raw = error instanceof Error ? error.message : "";
  // Drop a leading numeric "5051: " / "4004: " prefix and collapse whitespace.
  const cleaned = raw.replace(/^\s*\d{3,4}\s*:\s*/, "").trim();
  if (!cleaned) return "Something went wrong creating your company. Please try again.";
  // Avoid surfacing obvious tool/worker internals; fall back to a generic line.
  if (/business_|claude[_\s-]?agent|workspace root|traceback/i.test(cleaned)) {
    return "Something went wrong creating your company. Please try again.";
  }
  return cleaned;
}

function createEmptyLiveChatSignals(): LiveChatSignals {
  return {
    statusItems: [],
    progressLines: [],
    tools: [],
  };
}

export function useTakyonLitebulb() {
  const auth = useAuth();
  const [homeLoading, setHomeLoading] = useState(false);
  const [homeError, setHomeError] = useState("");
  const [businesses, setBusinesses] = useState<LitebulbBusiness[]>([]);
  const [account, setAccount] = useState<TakyonOperatorAccountResponse | null>(null);
  const [activeBusiness, setActiveBusiness] = useState<LitebulbBusiness | null>(null);
  const [workspace, setWorkspace] = useState<TakyonBusinessWorkspaceResponse | null>(null);
  const [creativeCredits, setCreativeCredits] = useState<TakyonBusinessCreativeCreditsResponse | null>(null);
  const [tractionRange, setTractionRange] = useState<"D" | "W" | "M" | "Y">("M");
  const [traction, setTraction] = useState<TakyonBusinessTractionResponse | null>(null);
  const [chatMessages, setChatMessages] = useState<ChatMessage[]>([]);
  const [chatProgress, setChatProgress] = useState<ChatProgress | null>(null);
  const [buildState, setBuildState] = useState<BuildState>(() => createEmptyBuildState());
  const [submitting, setSubmitting] = useState(false);
  const [sessionRunning, setSessionRunning] = useState(false);
  const [billingBusy, setBillingBusy] = useState(false);
  const [subscribeBusy, setSubscribeBusy] = useState<string | null>(null);

  const gatewayRef = useRef<GatewayClient | null>(null);
  const sessionIdRef = useRef("");
  const sessionBusinessRef = useRef("");
  const assistantMessageIdRef = useRef("");
  const assistantDraftTextRef = useRef("");
  const workspacePollRef = useRef<number | null>(null);
  const openingBusinessRef = useRef("");
  const visibleBusinessRef = useRef("");
  const activeBusinessNameRef = useRef("");
  const chatMessagesRef = useRef<ChatMessage[]>([]);
  const sessionRunningRef = useRef(false);
  const liveChatTurnRef = useRef(false);
  const liveChatSignalsRef = useRef<LiveChatSignals>(createEmptyLiveChatSignals());
  // Whether the active business is already past its first bootstrap (shipped a
  // product / has prior history). Kept in a ref so the live-chat card derivation
  // can read it without re-subscribing the streaming callbacks. When true, a
  // transient empty live-state must never reset the card to the bootstrap
  // "Starting <business> / Researching the market" placeholder.
  const pastBootstrapRef = useRef(false);

  useEffect(() => {
    activeBusinessNameRef.current = trimText(activeBusiness?.name);
  }, [activeBusiness?.name]);

  useEffect(() => {
    pastBootstrapRef.current = businessHasShipped(workspace);
  }, [workspace]);

  const isVisibleScope = useCallback((slug: string) => {
    return trimText(slug).toLowerCase() === visibleBusinessRef.current;
  }, []);

  const isVisibleBusiness = useCallback((slug: string) => {
    const businessSlug = trimText(slug).toLowerCase();
    return Boolean(businessSlug) && isVisibleScope(businessSlug);
  }, [isVisibleScope]);

  const loadHome = useCallback(async () => {
    if (auth.status !== "in") return;
    setHomeLoading(true);
    setHomeError("");
    try {
      const home = await api.getTakyonOperatorHome();
      setBusinesses((home.businesses || []).map(mapBusiness));
      setAccount(home.account || null);
    } catch (error) {
      setHomeError(error instanceof Error ? error.message : "Failed to load businesses.");
    } finally {
      setHomeLoading(false);
    }
  }, [auth.status]);

  const loadBusinessHomeShell = useCallback(async (slug: string) => {
    const businessSlug = trimText(slug).toLowerCase();
    if (!businessSlug) return;
    const workspacePayload = await api.getTakyonBusinessHome(businessSlug);
    writeCachedSlot(businessSlug, "workspace", workspacePayload);
    if (!isVisibleBusiness(businessSlug)) return;
    setWorkspace((current) => {
      if (!current || current.business_slug !== businessSlug) {
        return workspacePayload;
      }
      return current;
    });
  }, [isVisibleBusiness]);

  const loadWorkspace = useCallback(async (slug: string) => {
    const businessSlug = trimText(slug).toLowerCase();
    if (!businessSlug) return;
    const workspacePayload = await api.getTakyonBusinessWorkspace(businessSlug, 60, "full");
    writeCachedSlot(businessSlug, "workspace", workspacePayload);
    if (!isVisibleBusiness(businessSlug)) return;
    setWorkspace(workspacePayload);
  }, [isVisibleBusiness]);

  const loadCreativeCredits = useCallback(async (slug: string) => {
    const businessSlug = trimText(slug).toLowerCase();
    if (!businessSlug) return;
    try {
      const pendingCheckout = readStoredPendingCreativeCreditCheckout();
      const pendingSessionId = (
        pendingCheckout
        && trimText(pendingCheckout.businessSlug).toLowerCase() === businessSlug
      )
        ? trimText(pendingCheckout.sessionId)
        : "";
      const returnSessionId = readCreativeCreditCheckoutSessionIdFromUrl();
      const reconcileSessionId = returnSessionId || pendingSessionId;
      const payload = reconcileSessionId
        ? await api.reconcileTakyonBusinessCreativeCreditCheckout(
          businessSlug,
          reconcileSessionId,
        ).catch(() => api.getTakyonBusinessCreativeCredits(businessSlug))
        : await api.getTakyonBusinessCreativeCredits(businessSlug);
      if (reconcileSessionId && payload?.available) {
        clearStoredPendingCreativeCreditCheckout();
        clearCreativeCreditCheckoutSessionIdFromUrl();
      }
      if (payload) writeCachedSlot(businessSlug, "creativeCredits", payload);
      if (!isVisibleBusiness(businessSlug)) return;
      setCreativeCredits(payload);
    } catch {
      if (!isVisibleBusiness(businessSlug)) return;
      // Never blow away a good cached value with null on a transient fetch
      // error — that would re-introduce the "unavailable" flash. Keep the last
      // known credits (SWR); only fall through to null when there is no cache.
      setCreativeCredits((current) => current ?? readCachedSlot<TakyonBusinessCreativeCreditsResponse>(businessSlug, "creativeCredits"));
    }
  }, [isVisibleBusiness]);

  const saveChannelCreditBudgets = useCallback(async (
    slug: string,
    allocations: Record<"x" | "meta" | "reddit", number>,
  ) => {
    const businessSlug = trimText(slug).toLowerCase();
    if (!businessSlug) return null;
    const payload = await api.setTakyonBusinessChannelCreditBudgets(slug, allocations);
    if (!isVisibleBusiness(businessSlug)) return payload;
    setCreativeCredits(payload);
    return payload;
  }, [isVisibleBusiness]);

  // Pause/resume the business's autonomous CEO wake loop through the canonical
  // control.set rail (which also flips wake_schedules.enabled server-side).
  // Optimistically reflect the new state in the active business + card list, then
  // refresh from the canonical operator payload.
  const setBusinessWakeState = useCallback(async (slug: string, paused: boolean) => {
    const businessSlug = trimText(slug).toLowerCase();
    if (!businessSlug) return;
    setActiveBusiness((current) =>
      current && current.slug === businessSlug ? { ...current, wakesPaused: paused } : current,
    );
    setBusinesses((current) =>
      current.map((b) => (b.slug === businessSlug ? { ...b, wakesPaused: paused } : b)),
    );
    await api.setTakyonBusinessWakeState(businessSlug, paused);
    await loadHome();
  }, [loadHome]);

  // Operator "Wake now": enqueue an immediate ceo_wake so the worker runs a CEO turn on its next
  // tick instead of waiting for the scheduled cadence (e.g. every 6h). Enqueue-only — the turn's
  // narration arrives through the normal workspace event stream, so we just refresh home afterward.
  const wakeBusinessNow = useCallback(async (slug: string) => {
    const businessSlug = trimText(slug).toLowerCase();
    if (!businessSlug) return;
    await api.wakeTakyonBusinessNow(businessSlug);
    await loadHome();
  }, [loadHome]);

  const startCreativeCreditCheckout = useCallback(async (slug: string, credits: number) => {
    const businessSlug = trimText(slug).toLowerCase();
    const creditCount = Number.isFinite(credits) ? Math.max(0, Math.round(credits)) : 0;
    if (!businessSlug || creditCount <= 0) return;
    const cancelPath = window.location.pathname + window.location.search + window.location.hash;
    const successUrl = new URL(window.location.href);
    successUrl.searchParams.set(
      LITEBULB_CREATIVE_CREDIT_CHECKOUT_SESSION_QUERY_KEY,
      "{CHECKOUT_SESSION_ID}",
    );
    const successPath = `${successUrl.pathname}${successUrl.search}${successUrl.hash}`.replace(
      encodeURIComponent("{CHECKOUT_SESSION_ID}"),
      "{CHECKOUT_SESSION_ID}",
    );
    const checkout = await api.createTakyonBusinessCreativeCreditCheckout(
      businessSlug,
      {
        credits: creditCount,
        successPath,
        cancelPath,
      },
    );
    const checkoutUrl = trimText(checkout.checkout_url);
    if (!checkoutUrl) {
      throw new Error("Creative credit checkout unavailable.");
    }
    const sessionId = trimText(checkout.session_id);
    if (sessionId) {
      writeStoredPendingCreativeCreditCheckout({
        businessSlug,
        sessionId,
        credits: creditCount,
        createdAt: Date.now(),
      });
    }
    window.location.assign(checkoutUrl);
  }, []);

  // Fixed creative-credit pack purchase for a chosen business. Reuses the same
  // control-plane Stripe checkout the per-business panel uses, but keyed by the
  // configured pack id (operator picks the business on the Plans & Billing page).
  const startCreativeCreditPackCheckout = useCallback(async (slug: string, packId: string) => {
    const businessSlug = trimText(slug).toLowerCase();
    const pack = trimText(packId);
    if (!businessSlug || !pack) return;
    const cancelPath = window.location.pathname + window.location.search + window.location.hash;
    const successUrl = new URL(window.location.href);
    successUrl.searchParams.set(
      LITEBULB_CREATIVE_CREDIT_CHECKOUT_SESSION_QUERY_KEY,
      "{CHECKOUT_SESSION_ID}",
    );
    const successPath = `${successUrl.pathname}${successUrl.search}${successUrl.hash}`.replace(
      encodeURIComponent("{CHECKOUT_SESSION_ID}"),
      "{CHECKOUT_SESSION_ID}",
    );
    const checkout = await api.createTakyonBusinessCreativeCreditCheckout(
      businessSlug,
      {
        packId: pack,
        successPath,
        cancelPath,
      },
    );
    const checkoutUrl = trimText(checkout.checkout_url);
    if (!checkoutUrl) {
      throw new Error("Creative credit checkout unavailable.");
    }
    const sessionId = trimText(checkout.session_id);
    if (sessionId) {
      writeStoredPendingCreativeCreditCheckout({
        businessSlug,
        sessionId,
        credits: Number(checkout.credits || 0),
        createdAt: Date.now(),
      });
    }
    window.location.assign(checkoutUrl);
  }, []);

  const loadTraction = useCallback(async (slug: string, range: "D" | "W" | "M" | "Y") => {
    const businessSlug = trimText(slug).toLowerCase();
    if (!businessSlug) return;
    try {
      const payload = await api.getTakyonBusinessTraction(businessSlug, range);
      if (payload) writeCachedSlot(businessSlug, "traction", payload);
      if (!isVisibleBusiness(businessSlug)) return;
      setTraction(payload);
    } catch {
      if (!isVisibleBusiness(businessSlug)) return;
      // Keep last-known traction on a transient error rather than blanking the
      // chart to its empty state (SWR).
      setTraction((current) => current ?? readCachedSlot<TakyonBusinessTractionResponse>(businessSlug, "traction"));
    }
  }, [isVisibleBusiness]);

  const ensureGateway = useCallback(() => {
    if (gatewayRef.current) return gatewayRef.current;
    gatewayRef.current = new GatewayClient();
    return gatewayRef.current;
  }, []);

  const ensureAssistantMessage = useCallback(() => {
    if (assistantMessageIdRef.current) return assistantMessageIdRef.current;
    const id = `agent-${Date.now()}`;
    assistantMessageIdRef.current = id;
    setChatMessages((messages) => {
      const next: ChatMessage[] = [...messages, { id, who: "agent" as const, text: "", working: true }];
      chatMessagesRef.current = next;
      return next;
    });
    return id;
  }, []);

  const appendAssistantText = useCallback((text: string) => {
    const delta = rawText(text);
    if (!delta.length) return;
    assistantDraftTextRef.current += delta;
    // Render the streamed tokens inline on the in-flight working message so the
    // chat shows the answer building up — no blank gap, no two-stage flash when
    // the turn completes. Presentation-only: this mirrors the same tokens the
    // gateway already streamed into the agent's turn; it does not alter the
    // agent's runtime/intra-turn context.
    const id = ensureAssistantMessage();
    const draft = assistantDraftTextRef.current;
    setChatMessages((messages) => {
      const next = messages.map((message) => (
        message.id === id ? { ...message, text: draft, working: true } : message
      ));
      chatMessagesRef.current = next;
      return next;
    });
  }, [ensureAssistantMessage]);

  const completeAssistantText = useCallback((text?: string) => {
    const finalText = trimText(text) || trimText(assistantDraftTextRef.current);
    if (/^(?:\d{3}:|request timed out:|session busy|session not found)/i.test(finalText)) {
      assistantMessageIdRef.current = "";
      assistantDraftTextRef.current = "";
      return;
    }
    const id = assistantMessageIdRef.current || (finalText ? ensureAssistantMessage() : "");
    if (!id) return;
    setChatMessages((messages) => {
      const next = messages.map((message) => {
        if (message.id !== id) return message;
        return {
          ...message,
          text: finalText || message.text,
          working: false,
        };
      });
      chatMessagesRef.current = next;
      return next;
    });
    assistantMessageIdRef.current = "";
    assistantDraftTextRef.current = "";
  }, [ensureAssistantMessage]);

  const discardAssistantMessage = useCallback(() => {
    const id = assistantMessageIdRef.current;
    assistantMessageIdRef.current = "";
    assistantDraftTextRef.current = "";
    if (!id) return;
    setChatMessages((messages) => {
      const next = messages.filter((message) => (
        message.id !== id || Boolean(trimText(message.text))
      ));
      chatMessagesRef.current = next;
      return next;
    });
  }, []);

  const currentBusinessName = useCallback(() => {
    const explicit = trimText(activeBusinessNameRef.current);
    if (explicit) return explicit;
    const slug = trimText(sessionBusinessRef.current || visibleBusinessRef.current);
    return slug ? titleCaseSlug(slug) : "This business";
  }, []);

  const renderLiveChatProgress = useCallback((running = liveChatTurnRef.current) => {
    const card = deriveLiveWorkstreamCard({
      running,
      businessName: currentBusinessName(),
      statusItems: liveChatSignalsRef.current.statusItems,
      progressLines: liveChatSignalsRef.current.progressLines,
      tools: liveChatSignalsRef.current.tools,
      pastBootstrap: pastBootstrapRef.current,
    });
    setChatProgress(card ? { ...card, live: running } : null);
  }, [currentBusinessName]);

  const resetLiveChatSignals = useCallback(() => {
    liveChatSignalsRef.current = createEmptyLiveChatSignals();
  }, []);

  const noteLiveChatStatus = useCallback((text: string) => {
    const line = trimText(text);
    if (!line) return;
    liveChatSignalsRef.current = {
      ...liveChatSignalsRef.current,
      statusItems: pushUniqueLine(liveChatSignalsRef.current.statusItems, line, 12),
    };
    renderLiveChatProgress(true);
  }, [renderLiveChatProgress]);

  const noteLiveChatTool = useCallback((signal: ProgressToolSignal) => {
    liveChatSignalsRef.current = {
      ...liveChatSignalsRef.current,
      tools: pushUniqueToolSignal(liveChatSignalsRef.current.tools, signal, 12),
    };
    renderLiveChatProgress(true);
  }, [renderLiveChatProgress]);

  const startChatProgress = useCallback((_text = "Working…") => {
    renderLiveChatProgress(true);
  }, [renderLiveChatProgress]);

  const syncPendingChatProgress = useCallback((pending: boolean) => {
    if (!pending) {
      resetLiveChatSignals();
      setChatProgress(null);
      return;
    }
    const hasSignals =
      liveChatSignalsRef.current.statusItems.length > 0
      || liveChatSignalsRef.current.progressLines.length > 0
      || liveChatSignalsRef.current.tools.length > 0;
    if (!hasSignals) {
      resetLiveChatSignals();
    }
    renderLiveChatProgress(true);
  }, [renderLiveChatProgress, resetLiveChatSignals]);

  const clearChatProgress = useCallback(() => {
    resetLiveChatSignals();
    setChatProgress(null);
  }, [resetLiveChatSignals]);

  const beginChatTurn = useCallback((text = "Working…") => {
    liveChatTurnRef.current = true;
    assistantDraftTextRef.current = "";
    resetLiveChatSignals();
    startChatProgress(text);
  }, [resetLiveChatSignals, startChatProgress]);

  const endChatTurn = useCallback(() => {
    liveChatTurnRef.current = false;
    assistantDraftTextRef.current = "";
    clearChatProgress();
  }, [clearChatProgress]);

  const updateLiveChatProgress = useCallback((_text = "Working…") => {
    if (!liveChatTurnRef.current) return;
    renderLiveChatProgress(true);
  }, [renderLiveChatProgress]);

  const replayPendingTurn = useCallback(async (
    sessionId: string,
    slug: string,
    pendingTurn: PendingTurn | null,
  ) => {
    const businessSlug = trimText(slug).toLowerCase();
    const text = trimText(pendingTurn?.text);
    if (!sessionId || !businessSlug || !text) return;
    setSubmitting(true);
    beginChatTurn();
    try {
      const gateway = ensureGateway();
      ensureAssistantMessage();
      await gateway.request("prompt.submit", {
        session_id: sessionId,
        text,
      });
    } catch {
      /* best effort replay on reload */
    }
  }, [beginChatTurn, ensureAssistantMessage, ensureGateway]);

  const resetBuildState = useCallback(() => {
    setBuildState(createEmptyBuildState());
  }, []);

  const pushBuildNarration = useCallback((text: string) => {
    setBuildState((state) => (
      state.status === "creating"
        ? { ...state, narration: pushUniqueLine(state.narration, text, 10) }
        : state
    ));
  }, []);

  const pushBuildTerminal = useCallback((text: string) => {
    setBuildState((state) => (
      state.status === "creating"
        ? { ...state, terminal: pushUniqueLine(state.terminal, text, 18) }
        : state
    ));
  }, []);

  useEffect(() => {
    const gateway = ensureGateway();
    const offStart = gateway.on("message.start", () => {
      liveChatTurnRef.current = true;
      sessionRunningRef.current = true;
      setSessionRunning(true);
      updateLiveChatProgress();
      if (sessionBusinessRef.current) {
        void loadWorkspace(sessionBusinessRef.current).catch(() => undefined);
      }
    });
    const offDelta = gateway.on("message.delta", (event) => {
      const text = rawText((event.payload as { text?: string } | undefined)?.text);
      if (!text.length) return;
      // Raw assistant text (the CEO's chain-of-thought / planning) is captured
      // only for the collapsed, off-by-default debug transcript and the
      // build-screen narration disclosure — it is NEVER fed into the visible
      // "CEO update" progress card. The customer-facing card is driven by the
      // curated update (business_post_operator_update) plus structured tool/status
      // signals, so no internal reasoning can leak through the card derivation.
      appendAssistantText(text);
      pushBuildNarration(text);
    });
    const offComplete = gateway.on("message.complete", (event) => {
      const text = trimText((event.payload as { text?: string } | undefined)?.text);
      completeAssistantText(text);
      if (text) pushBuildNarration(text);
      setSubmitting(false);
      sessionRunningRef.current = false;
      setSessionRunning(false);
      endChatTurn();
      if (sessionBusinessRef.current) {
        clearStoredPendingTurn(sessionBusinessRef.current);
      }
      if (sessionBusinessRef.current) {
        void loadWorkspace(sessionBusinessRef.current);
        void loadTraction(sessionBusinessRef.current, tractionRange);
      }
    });
    const offThinking = gateway.onAny((event) => {
      if (!["thinking.delta", "reasoning.delta", "reasoning.available"].includes(event.type)) return;
      const text = trimText((event.payload as { text?: string } | undefined)?.text);
      if (!text) return;
      // Reasoning/thinking stays in the collapsed build-narration disclosure only;
      // never into the visible CEO update card (no chain-of-thought to customer).
      pushBuildNarration(text);
    });
    const offStatus = gateway.on("status.update", (event) => {
      const payload = (event.payload as { text?: string; kind?: string } | undefined) || {};
      const text = trimText(payload.text);
      if (text) pushBuildTerminal(text);
      if (text && shouldMirrorStatusInChat(payload.kind)) {
        noteLiveChatStatus(text);
      }
    });
    const offToolStart = gateway.on("tool.start", (event) => {
      const payload = (event.payload as { name?: string; context?: string } | undefined) || {};
      const name = trimText(payload.name || payload.context || "tool");
      const line = `Running ${name}`;
      pushBuildTerminal(line);
      noteLiveChatTool({
        name,
        context: trimText(payload.context),
        status: "running",
      });
    });
    const offToolComplete = gateway.on("tool.complete", (event) => {
      const payload = (event.payload as { name?: string; summary?: string } | undefined) || {};
      const text = trimText(payload.summary || payload.name || "Tool complete");
      pushBuildTerminal(text);
      noteLiveChatTool({
        name: trimText(payload.name),
        summary: text,
        status: "done",
      });
    });
    const offError = gateway.on("error", () => {
      setSubmitting(false);
      sessionRunningRef.current = false;
      setSessionRunning(false);
      discardAssistantMessage();
      endChatTurn();
      if (sessionBusinessRef.current) {
        clearStoredPendingTurn(sessionBusinessRef.current);
      }
    });
    return () => {
      offStart();
      offDelta();
      offComplete();
      offThinking();
      offStatus();
      offToolStart();
      offToolComplete();
      offError();
      gateway.close();
    };
  }, [
    appendAssistantText,
    completeAssistantText,
    discardAssistantMessage,
    endChatTurn,
    ensureGateway,
    loadTraction,
    loadWorkspace,
    noteLiveChatStatus,
    noteLiveChatTool,
    pushBuildNarration,
    pushBuildTerminal,
    tractionRange,
    updateLiveChatProgress,
  ]);

  const ensureSession = useCallback(async (slug: string) => {
    const businessSlug = trimText(slug).toLowerCase();
    const gateway = ensureGateway();
    await gateway.connect();
    if (!isVisibleScope(businessSlug)) return "";
    const applyHistory = (history: HistoryPayload) => {
      if (!isVisibleScope(businessSlug)) {
        return {
          pendingTurn: null,
          pendingTurnMissing: false,
        };
      }
      const pendingTurn = readStoredPendingTurn(businessSlug);
      const pendingTurnInHistory = historyHasPendingTurn(history, pendingTurn);
      if (pendingTurn && pendingTurnInHistory) {
        clearStoredPendingTurn(businessSlug);
      }
      const pendingTurnMissing = Boolean(pendingTurn && !pendingTurnInHistory);
      const pending = Boolean(history.running) || historyHasPendingReply(history) || pendingTurnMissing;
      const nextMessages = pendingTurnMissing
        ? mergeHistoryMessages(mapHistoryMessages(history), [pendingTurnMessage(pendingTurn!)])
        : mapHistoryMessages(history);
      chatMessagesRef.current = nextMessages;
      liveChatTurnRef.current = pending;
      sessionRunningRef.current = pending;
      setSessionRunning(pending);
      syncPendingChatProgress(pending);
      setChatMessages(nextMessages);
      return {
        pendingTurn,
        pendingTurnMissing,
      };
    };
    const loadHistory = async (sessionId: string, fallback?: HistoryPayload) => {
      const history = await gateway.request<HistoryPayload>("session.history", {
        session_id: sessionId,
      }).catch<HistoryPayload>(() => fallback ?? { messages: [], running: false });
      const applied = applyHistory(history);
      return { history, ...applied };
    };
    const resolveStoredSessionId = async (storedSessionId: string) => {
      const candidate = trimText(storedSessionId);
      if (!candidate) return "";
      const latest = await api.getSessionLatestDescendant(candidate).catch(() => null);
      return trimText(latest?.session_id) || candidate;
    };
    const readDurableSessionId = async (sessionId: string) => {
      const payload = await gateway.request<SessionTitlePayload>("session.title", {
        session_id: sessionId,
      }).catch<SessionTitlePayload | null>(() => null);
      return trimText(payload?.session_key);
    };

    if (sessionIdRef.current && sessionBusinessRef.current === businessSlug) {
      const loaded = await loadHistory(sessionIdRef.current);
      if (!isVisibleBusiness(businessSlug)) return "";
      if (loaded?.pendingTurnMissing) {
        void replayPendingTurn(sessionIdRef.current, businessSlug, loaded.pendingTurn);
      }
      return sessionIdRef.current;
    }

    if (businessSlug) {
      const storedSessionId = await resolveStoredSessionId(
        readStoredLitebulbSession(businessSlug),
      );
      if (!isVisibleScope(businessSlug)) return "";
      if (storedSessionId) {
        const resumed = await gateway.request<SessionResumePayload>("session.resume", {
          session_id: storedSessionId,
          cols: 100,
          _takyon_boot_business: businessSlug || undefined,
        }).catch<SessionResumePayload | null>(() => null);
        if (!isVisibleScope(businessSlug)) return "";
        if (resumed?.session_id) {
          sessionIdRef.current = trimText(resumed.session_id);
          sessionBusinessRef.current = businessSlug;
          assistantMessageIdRef.current = "";
          writeStoredLitebulbSession(
            businessSlug,
            trimText(resumed.resumed) || storedSessionId,
          );
          const loaded = await loadHistory(sessionIdRef.current, {
            messages: resumed.messages || [],
            running: true,
          });
          if (!isVisibleScope(businessSlug)) return "";
          if (loaded?.pendingTurnMissing) {
            void replayPendingTurn(sessionIdRef.current, businessSlug, loaded.pendingTurn);
          }
          return sessionIdRef.current;
        }
        clearStoredLitebulbSession(businessSlug);
      }
    }

    const result = await gateway.request<{ session_id?: string }>("session.create", {
      cols: 100,
      _takyon_boot_business: businessSlug || undefined,
    });
    if (!isVisibleScope(businessSlug)) return "";
    sessionIdRef.current = trimText(result?.session_id);
    sessionBusinessRef.current = businessSlug;
    assistantMessageIdRef.current = "";
    if (businessSlug && sessionIdRef.current) {
      const durableSessionId = await readDurableSessionId(sessionIdRef.current);
      if (!isVisibleScope(businessSlug)) return "";
      if (durableSessionId) {
        writeStoredLitebulbSession(businessSlug, durableSessionId);
      } else {
        clearStoredLitebulbSession(businessSlug);
      }
      const loaded = await loadHistory(sessionIdRef.current);
      if (!isVisibleScope(businessSlug)) return "";
      if (loaded?.pendingTurnMissing) {
        void replayPendingTurn(sessionIdRef.current, businessSlug, loaded.pendingTurn);
      }
    } else {
      assistantDraftTextRef.current = "";
      resetLiveChatSignals();
      chatMessagesRef.current = [];
      liveChatTurnRef.current = false;
      sessionRunningRef.current = false;
      setChatMessages([]);
      setChatProgress(null);
      setSessionRunning(false);
    }
    return sessionIdRef.current;
  }, [ensureGateway, isVisibleScope, replayPendingTurn, resetLiveChatSignals, syncPendingChatProgress]);

  const openBusiness = useCallback(async (slug: string) => {
    const businessSlug = trimText(slug).toLowerCase();
    if (!businessSlug) return;
    if (openingBusinessRef.current === businessSlug) return;
    const matched = businesses.find((item) => item.slug === businessSlug)
      || { slug: businessSlug, name: titleCaseSlug(businessSlug), goal: "", mode: "live", status: "active", tagline: titleCaseSlug(businessSlug), meta: "Live mode", productUrl: canonicalProductUrl(businessSlug), logoPath: "" };
    visibleBusinessRef.current = businessSlug;
    if (activeBusiness?.slug === businessSlug && sessionBusinessRef.current === businessSlug) {
      setActiveBusiness((current) => (
        current?.slug === businessSlug ? { ...current, ...matched } : matched
      ));
      return;
    }
    openingBusinessRef.current = businessSlug;
    try {
      visibleBusinessRef.current = businessSlug;
      assistantMessageIdRef.current = "";
      assistantDraftTextRef.current = "";
      sessionIdRef.current = "";
      sessionBusinessRef.current = "";
      resetLiveChatSignals();
      // Stale-while-revalidate: hydrate from the last-known cached snapshot so the
      // panels render instantly with real data while the network revalidation
      // (below) runs in the background. Falling back to null only when there is
      // no cache keeps a true cold open honest (skeletons), but a warm reopen of
      // a known business never flashes empty or "unavailable".
      setWorkspace(readCachedSlot<TakyonBusinessWorkspaceResponse>(businessSlug, "workspace"));
      setCreativeCredits(readCachedSlot<TakyonBusinessCreativeCreditsResponse>(businessSlug, "creativeCredits"));
      setTraction(readCachedSlot<TakyonBusinessTractionResponse>(businessSlug, "traction"));
      chatMessagesRef.current = [];
      liveChatTurnRef.current = false;
      sessionRunningRef.current = false;
      setChatMessages([]);
      setChatProgress(null);
      setSessionRunning(false);
      setActiveBusiness(matched);
      await Promise.all([
        ensureSession(businessSlug),
        loadBusinessHomeShell(businessSlug).catch(() => undefined),
        loadCreativeCredits(businessSlug),
        loadTraction(businessSlug, tractionRange),
      ]);
      void loadWorkspace(businessSlug).catch(() => undefined);
    } finally {
      if (openingBusinessRef.current === businessSlug) {
        openingBusinessRef.current = "";
      }
    }
  }, [activeBusiness?.slug, businesses, ensureSession, loadBusinessHomeShell, loadCreativeCredits, loadTraction, loadWorkspace, resetLiveChatSignals, tractionRange]);

  const stopPrompt = useCallback(async (preservePendingTurn = false) => {
    const sessionId = trimText(sessionIdRef.current);
    const businessSlug = trimText(sessionBusinessRef.current).toLowerCase();
    if (!sessionId) return false;
    try {
      await ensureGateway().request("session.interrupt", {
        session_id: sessionId,
      });
      setSubmitting(false);
      sessionRunningRef.current = false;
      setSessionRunning(false);
      endChatTurn();
      if (businessSlug && !preservePendingTurn) {
        clearStoredPendingTurn(businessSlug);
      }
      return true;
    } catch {
      return false;
    }
  }, [endChatTurn, ensureGateway]);

  const sendPrompt = useCallback(async (text: string) => {
    const value = trimText(text);
    if (!value || !activeBusiness) return;
    const pendingTurn: PendingTurn = {
      id: `pending-user-${Date.now()}`,
      text: value,
      createdAt: Date.now(),
      userCountBefore: chatMessagesRef.current.filter((message) => message.who === "user").length,
    };
    setSubmitting(true);
    beginChatTurn();
    writeStoredPendingTurn(activeBusiness.slug, pendingTurn);
    setChatMessages((messages) => {
      const next = [...messages, pendingTurnMessage(pendingTurn)];
      chatMessagesRef.current = next;
      return next;
    });
    try {
      let sessionId = await ensureSession(activeBusiness.slug);
      if (sessionRunningRef.current && sessionId) {
        await stopPrompt(true);
        await wait(400);
        beginChatTurn();
      }
      const gateway = ensureGateway();
      ensureAssistantMessage();
      for (let attempt = 0; attempt < 8; attempt += 1) {
        try {
          await gateway.request("prompt.submit", {
            session_id: sessionId,
            text: value,
          });
          void loadWorkspace(activeBusiness.slug).catch(() => undefined);
          return;
        } catch (error) {
          if (isMissingSessionError(error)) {
            clearStoredLitebulbSession(activeBusiness.slug);
            sessionIdRef.current = "";
            sessionBusinessRef.current = "";
            sessionId = await ensureSession(activeBusiness.slug);
            continue;
          }
          if (attempt < 7 && isBusyError(error)) {
            await wait(350 + attempt * 200);
            continue;
          }
          throw error;
        }
      }
    } catch (error) {
      clearStoredPendingTurn(activeBusiness.slug);
      setChatMessages((messages) => {
        const next = messages.filter((message) => message.id !== pendingTurn.id);
        chatMessagesRef.current = next;
        return next;
      });
      discardAssistantMessage();
      if (!isBusyError(error) && !isMissingSessionError(error)) {
        completeAssistantText(error instanceof Error ? error.message : "Failed to send message.");
      }
      endChatTurn();
      setSubmitting(false);
    }
  }, [activeBusiness, beginChatTurn, completeAssistantText, discardAssistantMessage, endChatTurn, ensureAssistantMessage, ensureGateway, ensureSession, loadWorkspace, stopPrompt]);

  const createBusiness = useCallback(async (goal: string) => {
    const idea = trimText(goal);
    if (!idea) return "";
    setBuildState({
      status: "creating",
      goal: idea,
      businessSlug: "",
      businessName: "",
      narration: [`Reading your idea — ${idea}.`],
      terminal: ["Booting Coscale CEO…"],
      error: "",
      errorCode: 0,
    });
    setSubmitting(true);
    try {
      const gateway = ensureGateway();
      visibleBusinessRef.current = "";
      assistantMessageIdRef.current = "";
      let sessionId = await ensureSession("");
      let result: {
        business_slug?: string;
        business_name?: string;
        businesses?: TakyonOperatorBusinessSummary[];
        current?: Record<string, unknown>;
        overview?: Record<string, unknown>;
        outputs?: unknown[];
        deliverables?: unknown[];
        background_run?: Record<string, unknown> | null;
        live_state?: Record<string, unknown> | null;
        streaming?: boolean;
      } | null = null;
      for (let attempt = 0; attempt < 4; attempt += 1) {
        try {
          result = await gateway.request<{
            business_slug?: string;
            business_name?: string;
            businesses?: TakyonOperatorBusinessSummary[];
            current?: Record<string, unknown>;
            overview?: Record<string, unknown>;
            outputs?: unknown[];
            deliverables?: unknown[];
            background_run?: Record<string, unknown> | null;
            live_state?: Record<string, unknown> | null;
            streaming?: boolean;
          }>("takyon.dashboard.create", {
            session_id: sessionId,
            goal: idea,
            mode: "live",
            limit: 60,
          });
          break;
        } catch (error) {
          if (isMissingSessionError(error)) {
            sessionIdRef.current = "";
            sessionBusinessRef.current = "";
            liveChatTurnRef.current = false;
            sessionRunningRef.current = false;
            setSessionRunning(false);
            sessionId = await ensureSession("");
            continue;
          }
          if (attempt < 3 && isBusyError(error)) {
            await wait(350 + attempt * 200);
            sessionId = await ensureSession("");
            continue;
          }
          throw error;
        }
      }
      if (!result) {
        throw new Error("Failed to create company.");
      }
      const businessSlug = trimText(result?.business_slug).toLowerCase();
      const businessName = trimText(result?.business_name) || titleCaseSlug(businessSlug || "business");
      if (Array.isArray(result?.businesses) && result.businesses.length) {
        setBusinesses(result.businesses.map(mapBusiness));
      } else {
        void loadHome();
      }
      visibleBusinessRef.current = businessSlug;
      sessionBusinessRef.current = businessSlug;
      setActiveBusiness({
        slug: businessSlug,
        name: businessName,
        goal: idea,
        mode: "live",
        status: "active",
        tagline: idea,
        meta: "Live mode",
        productUrl: canonicalProductUrl(businessSlug),
        logoPath: "",
      });
      setWorkspace({
        business_slug: businessSlug,
        current: result?.current || {},
        overview: result?.overview || {},
        outputs: result?.outputs || [],
        deliverables: result?.deliverables || [],
        background_run: result?.background_run || null,
        live_state: result?.live_state || null,
      });
      setBuildState((state) => ({
        ...state,
        status: "ready",
        businessSlug,
        businessName,
        terminal: pushUniqueLine(state.terminal, "Workspace ready."),
      }));
      void Promise.all([
        loadWorkspace(businessSlug).catch(() => undefined),
        loadCreativeCredits(businessSlug).catch(() => undefined),
        loadTraction(businessSlug, tractionRange).catch(() => undefined),
      ]);
      setSubmitting(false);
      liveChatTurnRef.current = Boolean(result?.streaming);
      sessionRunningRef.current = Boolean(result?.streaming);
      setSessionRunning(Boolean(result?.streaming));
      return businessSlug;
    } catch (error) {
      // Preserve the numeric error code (set on the Error by gatewayClient) so the
      // router can branch — 4030 out-of-credits → Plans & Billing; other → home.
      const code =
        error && typeof error === "object" && typeof (error as { code?: unknown }).code === "number"
          ? (error as { code: number }).code
          : 0;
      const human = humanizeCreateError(error, code);
      setBuildState((state) => ({
        ...state,
        status: "error",
        error: human,
        errorCode: code,
        terminal: pushUniqueLine(state.terminal, human),
      }));
      setSubmitting(false);
      liveChatTurnRef.current = false;
      sessionRunningRef.current = false;
      setSessionRunning(false);
      return "";
    }
  }, [ensureGateway, ensureSession, loadCreativeCredits, loadHome, loadTraction, loadWorkspace, tractionRange]);

  const openBillingPortal = useCallback(async () => {
    if (billingBusy) return;
    setBillingBusy(true);
    try {
      const result = await api.createTakyonOperatorBillingPortal(window.location.pathname + window.location.search + window.location.hash);
      const target = trimText(result.portal_url);
      if (target) {
        window.location.assign(target);
      }
    } finally {
      setBillingBusy(false);
    }
  }, [billingBusy]);

  const subscribeToPlan = useCallback(async (planId: string) => {
    const id = trimText(planId);
    if (subscribeBusy || !id) return;
    setSubscribeBusy(id);
    try {
      const result = await api.createTakyonOperatorSubscriptionCheckout(
        id,
        window.location.pathname + window.location.search + window.location.hash,
      );
      const target = trimText(result.checkout_url);
      if (target) {
        window.location.assign(target);
      }
    } finally {
      setSubscribeBusy(null);
    }
  }, [subscribeBusy]);

  useEffect(() => {
    if (auth.status === "in") {
      void loadHome();
      return;
    }
    setBusinesses([]);
    setAccount(null);
    setActiveBusiness(null);
    setWorkspace(null);
    setCreativeCredits(null);
    setTraction(null);
    assistantDraftTextRef.current = "";
    resetLiveChatSignals();
    chatMessagesRef.current = [];
    setChatMessages([]);
    setChatProgress(null);
    liveChatTurnRef.current = false;
    sessionRunningRef.current = false;
    setSessionRunning(false);
  }, [auth.status, loadHome, resetLiveChatSignals]);

  useEffect(() => {
    const sessionId = sessionIdRef.current;
    const businessSlug = sessionBusinessRef.current;
    if (!sessionId || !businessSlug || (!sessionRunning && !submitting)) return;
    let cancelled = false;

    const refresh = () => {
      void ensureGateway()
        .request<HistoryPayload>("session.history", { session_id: sessionId }, 10_000)
        .then((history) => {
          if (cancelled) return;
          const pendingTurn = readStoredPendingTurn(businessSlug);
          const pendingTurnInHistory = historyHasPendingTurn(history, pendingTurn);
          if (pendingTurn && pendingTurnInHistory) {
            clearStoredPendingTurn(businessSlug);
          }
          const pendingTurnMissing = Boolean(pendingTurn && !pendingTurnInHistory);
          const mappedHistory = mapHistoryMessages(history);
          const pending = Boolean(history.running) || historyHasPendingReply(history) || pendingTurnMissing;
          setChatMessages((messages) => {
            // Robustness guard: never blank the log while a turn is still live
            // on the client (a streaming working message, or an in-flight
            // live-chat turn the server snapshot hasn't caught up to yet). A
            // transient empty/non-running history during a resume race must not
            // wipe the user's just-sent message or the streaming reply.
            const hasLiveWorkingMessage = messages.some(
              (message) => message.who === "agent" && message.working,
            );
            const clientTurnLive =
              sessionRunningRef.current || liveChatTurnRef.current || hasLiveWorkingMessage;
            // Only blank the transcript when the authoritative server history
            // has reset to empty AND nothing is live on either side.
            if (!pending && !pendingTurnMissing && mappedHistory.length === 0) {
              if (clientTurnLive) return messages;
              chatMessagesRef.current = [];
              return [];
            }
            const mergedHistory = mergeHistoryMessages(messages, mappedHistory);
            const next = pendingTurnMissing
              ? mergeHistoryMessages(mergedHistory, [pendingTurnMessage(pendingTurn!)])
              : mergedHistory;
            chatMessagesRef.current = next;
            return next;
          });
          liveChatTurnRef.current = pending;
          sessionRunningRef.current = pending;
          setSessionRunning(pending);
          syncPendingChatProgress(pending);
          if (!history.running && pendingTurnMissing && pendingTurn) {
            void replayPendingTurn(sessionId, businessSlug, pendingTurn);
          }
          if (!pending) {
            setSubmitting(false);
          }
        })
        .catch(() => {
          /* best effort while live session is active */
        });
    };

    refresh();
    const timer = window.setInterval(refresh, 2500);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [ensureGateway, replayPendingTurn, sessionRunning, submitting, syncPendingChatProgress]);

  useEffect(() => {
    if (workspacePollRef.current !== null) {
      window.clearInterval(workspacePollRef.current);
      workspacePollRef.current = null;
    }
    if (!activeBusiness?.slug || auth.status !== "in") return;
    workspacePollRef.current = window.setInterval(() => {
      void loadWorkspace(activeBusiness.slug);
      void loadCreativeCredits(activeBusiness.slug);
      void loadTraction(activeBusiness.slug, tractionRange);
    }, 8000);
    return () => {
      if (workspacePollRef.current !== null) {
        window.clearInterval(workspacePollRef.current);
        workspacePollRef.current = null;
      }
    };
  }, [activeBusiness?.slug, auth.status, loadCreativeCredits, loadTraction, loadWorkspace, tractionRange]);

  const walletBalance = useMemo(() => {
    if (!account?.available) return null;
    if (typeof account.spendable_cents === "number") {
      return Math.max(0, account.spendable_cents);
    }
    if (typeof account.allowance_remaining_cents === "number") {
      return Math.max(0, account.allowance_remaining_cents);
    }
    const included = Number(account.allowance_included_cents || 0);
    const used = Number(account.allowance_used_cents || 0);
    return Math.max(0, included - used);
  }, [account]);

  return {
    auth,
    homeLoading,
    homeError,
    businesses,
    account,
    walletBalance,
    activeBusiness,
    workspace,
    creativeCredits,
    traction,
    tractionRange,
    setTractionRange,
    chatMessages,
    chatProgress,
    buildState,
    resetBuildState,
    submitting,
    sessionRunning,
    billingBusy,
    subscribeBusy,
    loadHome,
    openBusiness,
    sendPrompt,
    stopPrompt,
    createBusiness,
    saveChannelCreditBudgets,
    setBusinessWakeState,
    wakeBusinessNow,
    startCreativeCreditCheckout,
    startCreativeCreditPackCheckout,
    openBillingPortal,
    subscribeToPlan,
  };
}
