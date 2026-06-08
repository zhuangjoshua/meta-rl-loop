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
import { useAuth } from "../auth/useAuth";

export type ChatMessage = {
  id: string;
  who: "agent" | "user";
  text: string;
  working?: boolean;
};

export type ChatProgress = {
  text: string;
  live: boolean;
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
};

export type LitebulbBusiness = {
  slug: string;
  name: string;
  goal: string;
  mode: string;
  status: string;
  tagline: string;
  meta: string;
};

type HistoryPayload = {
  messages?: Array<{ role?: string; text?: string }>;
  running?: boolean;
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

function businessMeta(item: TakyonOperatorBusinessSummary) {
  const mode = trimText(item.mode || item.state || item.status || "live");
  if (!mode) return "Business";
  return `${mode.charAt(0).toUpperCase()}${mode.slice(1)} mode`;
}

function mapBusiness(item: TakyonOperatorBusinessSummary): LitebulbBusiness {
  const slug = trimText(item.slug).toLowerCase();
  const name = trimText(item.name) || titleCaseSlug(slug || "business");
  const goal = trimText(item.goal);
  return {
    slug,
    name,
    goal,
    mode: trimText(item.mode || item.state || item.status || "live") || "live",
    status: trimText(item.status || item.state || "active") || "active",
    tagline: goal || name,
    meta: businessMeta(item),
  };
}

function pushUniqueLine(lines: string[], next: string, limit = 18) {
  const text = trimText(next);
  if (!text) return lines;
  if (lines[lines.length - 1] === text) return lines;
  return [...lines, text].slice(-limit);
}

function shouldMirrorStatusInChat(kind: unknown) {
  const value = trimText(kind).toLowerCase();
  return !value || value === "takyon" || value === "process" || value === "status";
}

function mapHistoryMessages(payload: HistoryPayload | null | undefined): ChatMessage[] {
  const messages = Array.isArray(payload?.messages) ? payload.messages : [];
  const mapped = messages
    .map((item, index) => {
      const role = trimText(item?.role).toLowerCase();
      const text = trimText(item?.text);
      if (!text) return null;
      if (role === "user") {
        return { id: `history-user-${index}`, who: "user" as const, text };
      }
      if (role === "assistant") {
        return { id: `history-agent-${index}`, who: "agent" as const, text };
      }
      return null;
    })
    .filter((item): item is ChatMessage => Boolean(item));

  if (!payload?.running) return mapped;

  const lastAssistantIndex = [...mapped].reverse().findIndex((item) => item.who === "agent");
  if (lastAssistantIndex === -1) return mapped;
  const realIndex = mapped.length - 1 - lastAssistantIndex;
  return mapped.map((item, index) => (index === realIndex ? { ...item, working: true } : item));
}

function mergeHistoryMessages(prev: ChatMessage[], next: ChatMessage[]): ChatMessage[] {
  if (!next.length) return prev;
  const hasAssistant = next.some((message) => message.who === "agent");
  const base = hasAssistant
    ? prev.filter((message) => !(message.who === "agent" && message.working))
    : [...prev];
  const seen = new Set(base.map((message) => `${message.who}\n${message.text}`));
  for (const message of next) {
    const key = `${message.who}\n${message.text}`;
    if (seen.has(key)) continue;
    seen.add(key);
    base.push(message);
  }
  return base;
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
  const [sitePreviewUrl, setSitePreviewUrl] = useState("");
  const [tractionRange, setTractionRange] = useState<"D" | "W" | "M" | "Y">("M");
  const [traction, setTraction] = useState<TakyonBusinessTractionResponse | null>(null);
  const [chatMessages, setChatMessages] = useState<ChatMessage[]>([]);
  const [chatProgress, setChatProgress] = useState<ChatProgress | null>(null);
  const [buildState, setBuildState] = useState<BuildState>(() => createEmptyBuildState());
  const [submitting, setSubmitting] = useState(false);
  const [sessionRunning, setSessionRunning] = useState(false);
  const [billingBusy, setBillingBusy] = useState(false);
  const [topupBusy, setTopupBusy] = useState(false);

  const gatewayRef = useRef<GatewayClient | null>(null);
  const sessionIdRef = useRef("");
  const sessionBusinessRef = useRef("");
  const assistantMessageIdRef = useRef("");
  const workspacePollRef = useRef<number | null>(null);

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

  const loadWorkspace = useCallback(async (slug: string) => {
    if (!slug) return;
    const workspacePayload = await api.getTakyonBusinessWorkspace(slug, 60);
    setWorkspace(workspacePayload);
    try {
      const previewPayload = await api.getTakyonBusinessSitePreview(slug);
      const nextPreviewUrl = trimText(previewPayload.url);
      if (nextPreviewUrl) {
        setSitePreviewUrl(nextPreviewUrl);
      }
    } catch {
      // Keep the last good preview URL instead of blanking the product pane
      // on transient preview read failures.
    }
  }, []);

  const loadCreativeCredits = useCallback(async (slug: string) => {
    if (!slug) return;
    try {
      const payload = await api.getTakyonBusinessCreativeCredits(slug);
      setCreativeCredits(payload);
    } catch {
      setCreativeCredits(null);
    }
  }, []);

  const loadTraction = useCallback(async (slug: string, range: "D" | "W" | "M" | "Y") => {
    if (!slug) return;
    try {
      const payload = await api.getTakyonBusinessTraction(slug, range);
      setTraction(payload);
    } catch {
      setTraction(null);
    }
  }, []);

  const ensureGateway = useCallback(() => {
    if (gatewayRef.current) return gatewayRef.current;
    gatewayRef.current = new GatewayClient();
    return gatewayRef.current;
  }, []);

  const ensureAssistantMessage = useCallback(() => {
    if (assistantMessageIdRef.current) return assistantMessageIdRef.current;
    const id = `agent-${Date.now()}`;
    assistantMessageIdRef.current = id;
    setChatMessages((messages) => [...messages, { id, who: "agent", text: "", working: true }]);
    return id;
  }, []);

  const appendAssistantText = useCallback((text: string) => {
    const delta = rawText(text);
    if (!delta.length) return;
    const id = ensureAssistantMessage();
    setChatMessages((messages) => messages.map((message) => (
      message.id === id
        ? { ...message, text: `${message.text}${delta}`, working: true }
        : message
    )));
  }, [ensureAssistantMessage]);

  const completeAssistantText = useCallback((text?: string) => {
    const finalText = trimText(text);
    const id = assistantMessageIdRef.current || (finalText ? ensureAssistantMessage() : "");
    if (!id) return;
    setChatMessages((messages) => messages.map((message) => {
      if (message.id !== id) return message;
      return {
        ...message,
        text: finalText || message.text,
        working: false,
      };
    }));
    assistantMessageIdRef.current = "";
  }, [ensureAssistantMessage]);

  const startChatProgress = useCallback((text = "Working…") => {
    const line = trimText(text) || "Working…";
    setChatProgress((current) => (
      current?.text === line && current.live ? current : { text: line, live: true }
    ));
  }, []);

  const clearChatProgress = useCallback(() => {
    setChatProgress(null);
  }, []);

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
      setSessionRunning(true);
      startChatProgress();
    });
    const offDelta = gateway.on("message.delta", (event) => {
      const text = rawText((event.payload as { text?: string } | undefined)?.text);
      if (!text.length) return;
      appendAssistantText(text);
      pushBuildNarration(text);
    });
    const offComplete = gateway.on("message.complete", (event) => {
      const text = trimText((event.payload as { text?: string } | undefined)?.text);
      completeAssistantText(text);
      if (text) pushBuildNarration(text);
      setSubmitting(false);
      setSessionRunning(false);
      clearChatProgress();
      if (sessionBusinessRef.current) {
        void loadWorkspace(sessionBusinessRef.current);
        void loadTraction(sessionBusinessRef.current, tractionRange);
      }
    });
    const offThinking = gateway.onAny((event) => {
      if (!["thinking.delta", "reasoning.delta", "reasoning.available"].includes(event.type)) return;
      const text = trimText((event.payload as { text?: string } | undefined)?.text);
      if (!text) return;
      pushBuildNarration(text);
      startChatProgress(text);
    });
    const offStatus = gateway.on("status.update", (event) => {
      const payload = (event.payload as { text?: string; kind?: string } | undefined) || {};
      const text = trimText(payload.text);
      if (text) pushBuildTerminal(text);
      if (text && shouldMirrorStatusInChat(payload.kind)) {
        startChatProgress(text);
      }
    });
    const offToolStart = gateway.on("tool.start", (event) => {
      const payload = (event.payload as { name?: string; context?: string } | undefined) || {};
      const name = trimText(payload.name || payload.context || "tool");
      pushBuildTerminal(`Running ${name}`);
    });
    const offToolComplete = gateway.on("tool.complete", (event) => {
      const payload = (event.payload as { name?: string; summary?: string } | undefined) || {};
      const text = trimText(payload.summary || payload.name || "Tool complete");
      pushBuildTerminal(text);
    });
    const offError = gateway.on("error", () => {
      setSubmitting(false);
      setSessionRunning(false);
      clearChatProgress();
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
    clearChatProgress,
    completeAssistantText,
    ensureGateway,
    loadTraction,
    loadWorkspace,
    pushBuildNarration,
    pushBuildTerminal,
    startChatProgress,
    tractionRange,
  ]);

  const ensureSession = useCallback(async (slug: string) => {
    const businessSlug = trimText(slug).toLowerCase();
    const gateway = ensureGateway();
    await gateway.connect();
    if (sessionIdRef.current && sessionBusinessRef.current === businessSlug) {
      return sessionIdRef.current;
    }
    const result = await gateway.request<{ session_id?: string }>("session.create", {
      cols: 100,
      _takyon_boot_business: businessSlug || undefined,
    });
    sessionIdRef.current = trimText(result?.session_id);
    sessionBusinessRef.current = businessSlug;
    assistantMessageIdRef.current = "";
    if (businessSlug) {
      const history = await gateway.request<HistoryPayload>("session.history", {
        session_id: sessionIdRef.current,
      }).catch<HistoryPayload>(() => ({ messages: [], running: false }));
      setSessionRunning(Boolean(history.running));
      setChatProgress(history.running ? { text: "Working…", live: true } : null);
      setChatMessages(mapHistoryMessages(history));
    } else {
      setChatMessages([]);
      setChatProgress(null);
      setSessionRunning(false);
    }
    return sessionIdRef.current;
  }, [ensureGateway]);

  const openBusiness = useCallback(async (slug: string) => {
    const businessSlug = trimText(slug).toLowerCase();
    if (!businessSlug) return;
    const matched = businesses.find((item) => item.slug === businessSlug)
      || { slug: businessSlug, name: titleCaseSlug(businessSlug), goal: "", mode: "live", status: "active", tagline: titleCaseSlug(businessSlug), meta: "Live mode" };
    setWorkspace(null);
    setCreativeCredits(null);
    setTraction(null);
    setSitePreviewUrl("");
    setChatProgress(null);
    setActiveBusiness(matched);
    await Promise.all([
      ensureSession(businessSlug),
      loadWorkspace(businessSlug),
      loadCreativeCredits(businessSlug),
      loadTraction(businessSlug, tractionRange),
    ]);
  }, [businesses, ensureSession, loadCreativeCredits, loadTraction, loadWorkspace, tractionRange]);

  const sendPrompt = useCallback(async (text: string) => {
    const value = trimText(text);
    if (!value || !activeBusiness) return;
    setSubmitting(true);
    startChatProgress();
    setChatMessages((messages) => [...messages, { id: `user-${Date.now()}`, who: "user", text: value }]);
    try {
      const sessionId = await ensureSession(activeBusiness.slug);
      const gateway = ensureGateway();
      await gateway.request("prompt.submit", {
        session_id: sessionId,
        text: value,
      });
    } catch (error) {
      completeAssistantText(error instanceof Error ? error.message : "Failed to send message.");
      clearChatProgress();
      setSubmitting(false);
    }
  }, [activeBusiness, clearChatProgress, completeAssistantText, ensureGateway, ensureSession, startChatProgress]);

  const createBusiness = useCallback(async (goal: string) => {
    const idea = trimText(goal);
    if (!idea) return "";
    setBuildState({
      status: "creating",
      goal: idea,
      businessSlug: "",
      businessName: "",
      narration: [`Reading your idea — ${idea}.`],
      terminal: ["Booting Litebulb CEO…"],
      error: "",
    });
    setSubmitting(true);
    try {
      const sessionId = await ensureSession("");
      const gateway = ensureGateway();
      const result = await gateway.request<{
        business_slug?: string;
        business_name?: string;
        businesses?: TakyonOperatorBusinessSummary[];
        current?: Record<string, unknown>;
        overview?: Record<string, unknown>;
        outputs?: unknown[];
        background_run?: Record<string, unknown> | null;
        streaming?: boolean;
      }>("takyon.dashboard.create", {
        session_id: sessionId,
        goal: idea,
        mode: "live",
        limit: 60,
      });
      const businessSlug = trimText(result?.business_slug).toLowerCase();
      const businessName = trimText(result?.business_name) || titleCaseSlug(businessSlug || "business");
      if (Array.isArray(result?.businesses) && result.businesses.length) {
        setBusinesses(result.businesses.map(mapBusiness));
      } else {
        void loadHome();
      }
      sessionBusinessRef.current = businessSlug;
      setActiveBusiness({
        slug: businessSlug,
        name: businessName,
        goal: idea,
        mode: "live",
        status: "active",
        tagline: idea,
        meta: "Live mode",
      });
      setSitePreviewUrl("");
      setWorkspace({
        business_slug: businessSlug,
        current: result?.current || {},
        overview: result?.overview || {},
        outputs: result?.outputs || [],
        background_run: result?.background_run || null,
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
      setSessionRunning(Boolean(result?.streaming));
      return businessSlug;
    } catch (error) {
      setBuildState((state) => ({
        ...state,
        status: "error",
        error: error instanceof Error ? error.message : "Failed to create company.",
        terminal: pushUniqueLine(state.terminal, error instanceof Error ? error.message : "Failed to create company."),
      }));
      setSubmitting(false);
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

  const startTopup = useCallback(async (amountCents: number) => {
    if (topupBusy || amountCents <= 0) return;
    setTopupBusy(true);
    try {
      const result = await api.createTakyonOperatorTopupCheckout(
        amountCents,
        window.location.pathname + window.location.search + window.location.hash,
      );
      const target = trimText(result.checkout_url);
      if (target) {
        window.location.assign(target);
      }
    } finally {
      setTopupBusy(false);
    }
  }, [topupBusy]);

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
    setSitePreviewUrl("");
    setTraction(null);
    setChatMessages([]);
    setChatProgress(null);
    setSessionRunning(false);
  }, [auth.status, loadHome]);

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
          setChatMessages((messages) => mergeHistoryMessages(messages, mapHistoryMessages(history)));
          setSessionRunning(Boolean(history.running));
          setChatProgress((current) => (history.running ? current || { text: "Working…", live: true } : null));
          if (!history.running) {
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
  }, [ensureGateway, sessionRunning, submitting]);

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
    return Number(account.spendable_cents || 0);
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
    sitePreviewUrl,
    traction,
    tractionRange,
    setTractionRange,
    chatMessages,
    chatProgress,
    buildState,
    resetBuildState,
    submitting,
    billingBusy,
    topupBusy,
    loadHome,
    openBusiness,
    sendPrompt,
    createBusiness,
    openBillingPortal,
    startTopup,
  };
}
