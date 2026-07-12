import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { client, defaultSubscribePlanKey, type TakyonActionError } from "./takyon";
import { setSubscribeAfterAuth, shouldSubscribeAfterAuth } from "./product-auth";

export interface SessionUser {
  [key: string]: unknown;
}

export interface SessionPayload {
  [key: string]: unknown;
}

export interface AccountPayload {
  [key: string]: unknown;
}

export interface UseSessionResult {
  user: SessionUser | null;
  loading: boolean;
  error: Error | null;
}

export type ViewerAccessState =
  | "anonymous"
  | "account_unavailable"
  | "subscription_required"
  | "past_due"
  | "ready";

export interface ViewerAccessResult {
  state: ViewerAccessState;
  authenticated: boolean;
  entitled: boolean;
  user: SessionUser | null;
  session: SessionPayload | null;
  account: AccountPayload | null;
  subscriptionState: string;
  loading: boolean;
  error: Error | null;
  refresh: () => Promise<void>;
}

export interface ViewerCta {
  primaryHref: string;
  primaryLabel: string;
  secondaryHref: string;
  secondaryLabel: string;
  membershipState: string;
}

function isObject(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function lowerText(value: unknown): string {
  return String(value ?? "").trim().toLowerCase();
}

function sessionUser(payload: unknown): SessionUser | null {
  if (!isObject(payload)) return null;
  if (isObject(payload.user)) return payload.user as SessionUser;
  if (isObject(payload.session) && isObject(payload.session.user)) {
    return payload.session.user as SessionUser;
  }
  return null;
}

function accountUser(payload: AccountPayload | null): SessionUser | null {
  return payload && isObject(payload.user) ? (payload.user as SessionUser) : null;
}

function accountEntitlements(payload: AccountPayload | null): Record<string, unknown>[] {
  return payload && Array.isArray(payload.entitlements)
    ? payload.entitlements.filter((item): item is Record<string, unknown> => isObject(item))
    : [];
}

function isPaidTier(value: unknown): boolean {
  return ["paid", "pro", "trial"].includes(lowerText(value));
}

function entitlementStatus(entitlement: Record<string, unknown>): string {
  const status = lowerText(entitlement.status);
  if (status === "paid") return "active";
  if (status === "cancelled") return "canceled";
  return status;
}

function activePaidEntitlement(entitlement: Record<string, unknown>): boolean {
  const status = entitlementStatus(entitlement);
  const tier = lowerText(entitlement.tier);
  if (!["active", "trialing", "paid"].includes(status)) return false;
  if (isPaidTier(tier)) return true;
  if (String(entitlement.plan_key ?? entitlement.planKey ?? "").trim()) return true;
  if (entitlement.stripe_subscription_id || entitlement.stripeSubscriptionId) return true;
  return false;
}

export function hasNonterminalStripeSubscription(payload: AccountPayload | null): boolean {
  return accountEntitlements(payload).some((entitlement) => {
    const source = String(entitlement.source ?? "").trim().toLowerCase();
    const subscriptionId = String(
      entitlement.stripe_subscription_id ?? entitlement.stripeSubscriptionId ?? "",
    ).trim();
    const status = entitlementStatus(entitlement);
    return (
      (!source || source === "stripe") &&
      Boolean(subscriptionId) &&
      !["canceled", "cancelled", "sandbox_retired"].includes(status)
    );
  });
}

// Preserve the historical active/trialing predicate for worker-owned callers. Cancellation uses
// the separate nonterminal helper because a denied/past-due Stripe subscription is still live and
// must remain cancellable.
export function hasActiveStripeSubscription(payload: AccountPayload | null): boolean {
  return accountEntitlements(payload).some((entitlement) => {
    const subscriptionId = String(
      entitlement.stripe_subscription_id ?? entitlement.stripeSubscriptionId ?? "",
    ).trim();
    return Boolean(subscriptionId) && activePaidEntitlement(entitlement);
  });
}

export function isAccountEntitled(payload: AccountPayload | null): boolean {
  if (!payload || !isObject(payload)) return false;
  if (accountEntitlements(payload).some((entitlement) => activePaidEntitlement(entitlement))) {
    return true;
  }
  return false;
}

export function subscriptionStateFromAccount(payload: AccountPayload | null): string {
  const ranked = accountEntitlements(payload)
    .filter((entitlement) => {
      const tier = lowerText(entitlement.tier);
      return Boolean(
        isPaidTier(tier) ||
          String(entitlement.plan_key ?? entitlement.planKey ?? "").trim() ||
          entitlement.stripe_subscription_id ||
          entitlement.stripeSubscriptionId,
      );
    })
    .map((entitlement) => entitlementStatus(entitlement))
    .sort((left, right) => {
      const rank: Record<string, number> = {
        active: 0,
        trialing: 1,
        past_due: 2,
        canceled: 3,
        revoked: 4,
      };
      return (rank[left] ?? 9) - (rank[right] ?? 9);
    });
  if (ranked[0]) return ranked[0];
  return "none";
}

export function resolveViewerCta(access: Pick<ViewerAccessResult, "authenticated" | "entitled" | "state" | "subscriptionState"> | null): ViewerCta {
  if (!access || !access.authenticated || access.state === "anonymous") {
    return {
      primaryHref: "/app?intent=subscribe",
      primaryLabel: "Subscribe",
      secondaryHref: "/app?intent=signin",
      secondaryLabel: "Sign in",
      membershipState: "",
    };
  }
  if (access.entitled || access.state === "ready") {
    return {
      primaryHref: "/app",
      primaryLabel: "Open app",
      secondaryHref: "/app/profile",
      secondaryLabel: "Account",
      membershipState: access.subscriptionState || "active",
    };
  }
  if (access.state === "account_unavailable") {
    return {
      primaryHref: "/app",
      primaryLabel: "Continue",
      secondaryHref: "/app/profile",
      secondaryLabel: "Account",
      membershipState: access.subscriptionState || "signed_in",
    };
  }
  if (access.state === "past_due") {
    return {
      primaryHref: "/app?intent=subscribe",
      primaryLabel: "Update billing",
      secondaryHref: "/app/profile",
      secondaryLabel: "Account",
      membershipState: access.subscriptionState || "past_due",
    };
  }
  return {
    primaryHref: "/app?intent=subscribe",
    primaryLabel: "Complete subscription",
    secondaryHref: "/app/profile",
    secondaryLabel: "Account",
    membershipState: access.subscriptionState || "signed_in",
  };
}

/** Consume the `?intent=subscribe` CTA. The subscribe links across the kit point at
 *  `/app?intent=subscribe`; this is the ONE place that intent is turned into a real checkout call,
 *  so every business gets a working subscribe without the generated UI wiring it by hand. When a
 *  signed-in, not-yet-entitled viewer carries intent=subscribe, start checkout via the shared rail
 *  and redirect to the returned URL; on no-URL/failure, clear the intent so it can be retried. */
export function useSubscribeIntent(
  access: Pick<ViewerAccessResult, "authenticated" | "entitled" | "loading">,
  intent: string | null,
): void {
  const startedRef = useRef(false);
  useEffect(() => {
    if (typeof window === "undefined") return;
    // `intent` MUST be passed from the router (useSearchParams) and be in the deps, so a
    // client-side <Link> click to /app?intent=subscribe re-runs this effect.
    // The subscribe intent must ALSO survive the sign-in round-trip: Google OAuth redirects to a
    // FIXED redirect path (config.redirectPath) that drops the ?intent=subscribe query, so a
    // signed-out Subscribe click used to lead to the sign-in gate and then silently do nothing
    // (intent lost). We persist the intent in sessionStorage and resume on return.
    const wantsSubscribe = intent === "subscribe" || shouldSubscribeAfterAuth();
    if (!wantsSubscribe) {
      startedRef.current = false;
      return;
    }
    if (access.loading) return;
    if (access.entitled) {
      // Already subscribed — nothing to buy; clear any stale resume flag.
      setSubscribeAfterAuth(false);
      return;
    }
    if (!access.authenticated) {
      // Stash the intent across the sign-in OAuth redirect; the app's sign-in gate is already
      // shown. On return (authenticated) this effect re-runs and resumes checkout below.
      setSubscribeAfterAuth(true);
      return;
    }
    if (startedRef.current) return;
    startedRef.current = true;
    let cancelled = false;
    void (async () => {
      let failed = false;
      try {
        const planKey = defaultSubscribePlanKey();
        const response = await client.checkout(planKey ? { plan_key: planKey } : {});
        const url = String((response && (response.url || response.checkout_url)) || "").trim();
        if (!cancelled && url) {
          setSubscribeAfterAuth(false);
          window.location.assign(url);
          return;
        }
        failed = true; // authorized but no checkout URL came back
      } catch {
        failed = true;
      }
      if (cancelled) return;
      // Do NOT swallow failures silently (the old bare `catch {}` made a failed Subscribe look
      // dead). Clear the resume flag and surface ?checkout=error so the app shows a retry banner.
      setSubscribeAfterAuth(false);
      const next = new URLSearchParams(window.location.search);
      next.delete("intent");
      if (failed) next.set("checkout", "error");
      const query = next.toString();
      window.history.replaceState(
        null,
        "",
        window.location.pathname + (query ? `?${query}` : "") + window.location.hash,
      );
      startedRef.current = false;
    })();
    return () => {
      cancelled = true;
    };
  }, [intent, access.authenticated, access.entitled, access.loading]);
}

/** Read the query string from BOTH the real query and any hash query. Stripe's `success_url` lands
 *  back in the SPA, and depending on how the public site routes, the return params can sit either on
 *  `window.location.search` (`/app?checkout=success`) or inside the hash query of a hash-style URL
 *  (`/app#/?checkout=success`). Checking both means the return-from-checkout refresh fires regardless
 *  of which form Stripe redirected to. */
function checkoutReturnParams(): URLSearchParams {
  const merged = new URLSearchParams();
  if (typeof window === "undefined") return merged;
  const absorb = (raw: string) => {
    const trimmed = raw.replace(/^[?#]/, "");
    if (!trimmed) return;
    for (const [key, value] of new URLSearchParams(trimmed)) {
      if (!merged.has(key)) merged.set(key, value);
    }
  };
  absorb(window.location.search);
  const hash = window.location.hash;
  const queryIndex = hash.indexOf("?");
  if (queryIndex >= 0) absorb(hash.slice(queryIndex));
  return merged;
}

function hasCheckoutReturnSignal(params: URLSearchParams): boolean {
  return params.get("checkout") === "success" || Boolean(String(params.get("session_id") ?? "").trim());
}

/** Strip the one-shot checkout return params from the URL (both the real query and any hash query) so
 *  a later reload does not re-trigger the refresh poll. Uses history.replaceState directly so it works
 *  even when the params live in the hash, which react-router's setSearchParams would not touch. */
function stripCheckoutReturnParams(): void {
  if (typeof window === "undefined") return;
  const drop = (raw: string): string => {
    const hadPrefix = raw.startsWith("?") || raw.startsWith("#");
    const prefix = raw.startsWith("#") ? "#" : "?";
    const params = new URLSearchParams(raw.replace(/^[?#]/, ""));
    params.delete("checkout");
    params.delete("session_id");
    const next = params.toString();
    if (!next) return "";
    return (hadPrefix ? prefix : "") + next;
  };
  const search = drop(window.location.search);
  let hash = window.location.hash;
  const queryIndex = hash.indexOf("?");
  if (queryIndex >= 0) {
    hash = hash.slice(0, queryIndex) + drop(hash.slice(queryIndex));
  }
  window.history.replaceState(null, "", window.location.pathname + search + hash);
}

/** Re-read entitlement after the customer returns from Stripe checkout, without a manual reload.
 *  Two triggers, both guarded so they cannot loop:
 *
 *  1. On mount, if the URL carries the Stripe return signal (`?checkout=success` and/or `session_id`,
 *     in the query OR the hash query), poll `refresh()` a few times over ~8s to absorb webhook lag,
 *     stopping as soon as `entitled` flips true, then strip the params so a later reload is inert.
 *  2. On `visibilitychange`/window `focus`, re-read once (debounced) so returning to the tab after
 *     paying elsewhere updates the badge/CTA.
 *
 *  `entitled` is intentionally NOT a dep of the mount effect — the poll reads the latest value through
 *  a ref, so the effect runs once per page load and the interval is the only thing that re-checks. */
export function useCheckoutReturnRefresh(
  access: Pick<ViewerAccessResult, "entitled" | "refresh">,
): void {
  const refresh = access.refresh;
  const entitledRef = useRef(access.entitled);
  entitledRef.current = access.entitled;
  const handledReturnRef = useRef(false);

  // 1. Return-from-checkout poll (runs at most once per page load).
  useEffect(() => {
    if (typeof window === "undefined") return;
    if (handledReturnRef.current) return;
    const params = checkoutReturnParams();
    if (!hasCheckoutReturnSignal(params)) return;
    handledReturnRef.current = true;
    // Purchase attribution is intentionally NOT emitted by the browser. The Safebox sends an
    // unguessable per-business CAPI event only after a Stripe-signed, live-account-proven paid
    // checkout; a product subuser cannot manufacture that server-only conversion signal.
    stripCheckoutReturnParams();

    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | null = null;
    let attempts = 0;
    const maxAttempts = 5; // initial + retries, ~0/2/4/6/8s
    const poll = () => {
      void (async () => {
        await refresh();
        if (cancelled) return;
        attempts += 1;
        if (entitledRef.current || attempts >= maxAttempts) return;
        timer = setTimeout(poll, 2000);
      })();
    };
    poll();

    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, [refresh]);

  // 2. Re-read on tab focus / visibility, debounced so quick focus flaps don't hammer the API.
  useEffect(() => {
    if (typeof window === "undefined") return;
    let timer: ReturnType<typeof setTimeout> | null = null;
    const schedule = () => {
      if (document.visibilityState === "hidden") return;
      if (timer) clearTimeout(timer);
      timer = setTimeout(() => {
        void refresh();
      }, 400);
    };
    window.addEventListener("focus", schedule);
    document.addEventListener("visibilitychange", schedule);
    return () => {
      if (timer) clearTimeout(timer);
      window.removeEventListener("focus", schedule);
      document.removeEventListener("visibilitychange", schedule);
    };
  }, [refresh]);
}

/** Loads the current product session once on mount via client.session(). */
export function useSession(): UseSessionResult {
  const [user, setUser] = useState<SessionUser | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<Error | null>(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const payload = await client.session();
        if (cancelled) return;
        const sessionUser =
          payload && typeof payload === "object" && payload.user
            ? (payload.user as SessionUser)
            : null;
        setUser(sessionUser);
        setError(null);
      } catch (err) {
        if (cancelled) return;
        setUser(null);
        setError(err instanceof Error ? err : new Error(String(err)));
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  return { user, loading, error };
}

/** Loads session first, then account, and keeps CTA-safe access state in one place. */
export function useViewerAccess(): ViewerAccessResult {
  const [state, setState] = useState<ViewerAccessState>("anonymous");
  const [authenticated, setAuthenticated] = useState(false);
  const [entitled, setEntitled] = useState(false);
  const [user, setUser] = useState<SessionUser | null>(null);
  const [session, setSession] = useState<SessionPayload | null>(null);
  const [account, setAccount] = useState<AccountPayload | null>(null);
  const [subscriptionState, setSubscriptionState] = useState("none");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<Error | null>(null);
  const aliveRef = useRef(true);
  // Shared in-flight promise so overlapping refresh() calls — mount, the
  // checkout-return poll, and the tab-focus/visibility re-read — dedupe onto a
  // single session()+account() request chain instead of each firing its own.
  const inflightRef = useRef<Promise<void> | null>(null);

  useEffect(() => {
    aliveRef.current = true;
    return () => {
      aliveRef.current = false;
    };
  }, []);

  const refresh = useCallback(async () => {
    if (inflightRef.current) return inflightRef.current;
    setLoading(true);
    const run = (async () => {
    try {
      const sessionPayload = await client.session();
      if (!aliveRef.current) return;
      const nextSession = isObject(sessionPayload) ? (sessionPayload as SessionPayload) : null;
      const nextSessionUser = sessionUser(nextSession);
      const hasSession =
        (nextSession && nextSession.authenticated === true) ||
        nextSessionUser !== null ||
        Boolean(String(nextSession?.email ?? "").trim());

      setSession(nextSession);

      if (!hasSession) {
        setState("anonymous");
        setAuthenticated(false);
        setEntitled(false);
        setUser(null);
        setAccount(null);
        setSubscriptionState("none");
        setError(null);
        return;
      }

      setAuthenticated(true);
      setUser(nextSessionUser);

      try {
        const accountPayload = await client.account();
        if (!aliveRef.current) return;
        const nextAccount =
          isObject(accountPayload) && accountPayload.authenticated === false
            ? null
            : isObject(accountPayload)
              ? (accountPayload as AccountPayload)
              : null;
        const nextEntitled = isAccountEntitled(nextAccount);
        const nextSubscriptionState = subscriptionStateFromAccount(nextAccount);

        setAccount(nextAccount);
        setUser(accountUser(nextAccount) ?? nextSessionUser);
        setEntitled(nextEntitled);
        setSubscriptionState(nextSubscriptionState);
        setState(
          nextEntitled
            ? "ready"
            : nextSubscriptionState === "past_due" || nextSubscriptionState === "canceled"
              ? "past_due"
              : "subscription_required",
        );
        setError(null);
      } catch (err) {
        if (!aliveRef.current) return;
        setAccount(null);
        setEntitled(false);
        setSubscriptionState("none");
        setState("account_unavailable");
        setError(err instanceof Error ? err : new Error(String(err)));
      }
    } catch (err) {
      if (!aliveRef.current) return;
      setState("anonymous");
      setAuthenticated(false);
      setEntitled(false);
      setUser(null);
      setSession(null);
      setAccount(null);
      setSubscriptionState("none");
      setError(err instanceof Error ? err : new Error(String(err)));
    } finally {
      if (aliveRef.current) setLoading(false);
    }
    })();
    inflightRef.current = run;
    try {
      await run;
    } finally {
      inflightRef.current = null;
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  return {
    state,
    authenticated,
    entitled,
    user,
    session,
    account,
    subscriptionState,
    loading,
    error,
    refresh,
  };
}

export interface RecordItem {
  [key: string]: unknown;
}

export interface UseRecordsResult {
  records: RecordItem[];
  loading: boolean;
  error: Error | null;
  refresh: () => Promise<void>;
}

const recordsCache = new Map<string, RecordItem[]>();

/** Lists records of one type via client.listRecords({ type }); refresh re-lists. */
export function useRecords(type: string): UseRecordsResult {
  const [records, setRecords] = useState<RecordItem[]>(() => recordsCache.get(type) ?? []);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<Error | null>(null);
  const aliveRef = useRef(true);

  useEffect(() => {
    aliveRef.current = true;
    return () => {
      aliveRef.current = false;
    };
  }, []);

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const payload = await client.listRecords({ type });
      if (!aliveRef.current) return;
      const list = Array.isArray(payload?.records)
        ? (payload.records as RecordItem[])
        : [];
      recordsCache.set(type, list);
      setRecords(list);
      setError(null);
    } catch (err) {
      if (!aliveRef.current) return;
      setError(err instanceof Error ? err : new Error(String(err)));
    } finally {
      if (aliveRef.current) setLoading(false);
    }
  }, [type]);

  useEffect(() => {
    setRecords(recordsCache.get(type) ?? []);
    void refresh();
  }, [refresh, type]);

  return { records, loading, error, refresh };
}

export interface UseActionRunnerResult {
  /** Runs the action; resolves with the result payload, or null when it failed
   *  (the classified error lands in `error`, preserving .kind/.checkoutUrl). */
  run: (payload?: Record<string, unknown>) => Promise<unknown | null>;
  pending: boolean;
  error: TakyonActionError | null;
}

/** Wraps client.createActionRunner(name) as React state. */
export function useActionRunner(name: string): UseActionRunnerResult {
  const runner = useMemo(() => client.createActionRunner(name), [name]);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<TakyonActionError | null>(null);

  const run = useCallback(
    async (payload: Record<string, unknown> = {}) => {
      setPending(true);
      setError(null);
      try {
        return await runner.run(payload);
      } catch (err) {
        // The kit classifies errors with .kind (budget, rate_limited,
        // already_running, unavailable, timeout, network, action_error)
        // and attaches .checkoutUrl on budget errors when checkout is callable.
        setError(err as TakyonActionError);
        return null;
      } finally {
        setPending(false);
      }
    },
    [runner],
  );

  return { run, pending, error };
}
