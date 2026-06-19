import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { client, defaultSubscribePlanKey, type TakyonActionError } from "./takyon";
import { useViewerAccessContext } from "./product-auth";

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

export function isAccountEntitled(payload: AccountPayload | null): boolean {
  if (!payload || !isObject(payload)) return false;
  if (payload.entitled === true) return true;
  if (isObject(payload.plan) && payload.plan.active === true) return true;
  if (accountEntitlements(payload).some((entitlement) => activePaidEntitlement(entitlement))) {
    return true;
  }
  const user = accountUser(payload);
  return isPaidTier(user?.tier);
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
  const user = accountUser(payload);
  if (isPaidTier(user?.tier)) {
    return lowerText(user?.tier) === "trial" ? "trialing" : "active";
  }
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
    // client-side <Link> click to /app?intent=subscribe re-runs this effect. Reading
    // window.location.search alone would only fire on a full reload (when `access` changes),
    // which is exactly the bug that made the click do nothing. Reset the once-guard when the
    // intent clears so a later click re-arms it.
    if (intent !== "subscribe") {
      startedRef.current = false;
      return;
    }
    if (access.loading || !access.authenticated || access.entitled) return;
    if (startedRef.current) return;
    startedRef.current = true;
    let cancelled = false;
    void (async () => {
      try {
        const planKey = defaultSubscribePlanKey();
        const response = await client.checkout(planKey ? { plan_key: planKey } : {});
        const url = String((response && (response.url || response.checkout_url)) || "").trim();
        if (!cancelled && url) {
          window.location.assign(url);
          return;
        }
      } catch {
        // fall through to clear the intent so the CTA can be retried without looping
      }
      if (cancelled) return;
      const next = new URLSearchParams(window.location.search);
      next.delete("intent");
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

/** Resolved viewer state minus the refresh handle — the provider owns the single store and
 *  derives this snapshot once per auth change, then exposes it through context. */
export type ViewerAccessSnapshot = Omit<ViewerAccessResult, "refresh">;

/** Loading placeholder before the first session read completes. */
export const LOADING_VIEWER_ACCESS: ViewerAccessSnapshot = {
  state: "anonymous",
  authenticated: false,
  entitled: false,
  user: null,
  session: null,
  account: null,
  subscriptionState: "none",
  loading: true,
  error: null,
};

/** Reads the product session, then (when authenticated) the account, and derives the CTA-safe
 *  access snapshot in ONE place. The provider calls this once per auth transition so every screen
 *  flips together off a single source of truth. Pure aside from the two client GETs — no React. */
export async function resolveViewerAccessSnapshot(): Promise<ViewerAccessSnapshot> {
  const base: ViewerAccessSnapshot = {
    ...LOADING_VIEWER_ACCESS,
    loading: false,
  };
  try {
    const sessionPayload = await client.session();
    const nextSession = isObject(sessionPayload) ? (sessionPayload as SessionPayload) : null;
    const nextSessionUser = sessionUser(nextSession);
    const hasSession =
      (nextSession && nextSession.authenticated === true) ||
      nextSessionUser !== null ||
      Boolean(String(nextSession?.email ?? "").trim());

    if (!hasSession) {
      return { ...base, session: nextSession, state: "anonymous" };
    }

    try {
      const accountPayload = await client.account();
      const nextAccount =
        isObject(accountPayload) && accountPayload.authenticated === false
          ? null
          : isObject(accountPayload)
            ? (accountPayload as AccountPayload)
            : null;
      const nextEntitled = isAccountEntitled(nextAccount);
      const nextSubscriptionState = subscriptionStateFromAccount(nextAccount);
      return {
        ...base,
        authenticated: true,
        entitled: nextEntitled,
        user: accountUser(nextAccount) ?? nextSessionUser,
        session: nextSession,
        account: nextAccount,
        subscriptionState: nextSubscriptionState,
        state: nextEntitled
          ? "ready"
          : nextSubscriptionState === "past_due" || nextSubscriptionState === "canceled"
            ? "past_due"
            : "subscription_required",
      };
    } catch (err) {
      return {
        ...base,
        authenticated: true,
        user: nextSessionUser,
        session: nextSession,
        state: "account_unavailable",
        error: err instanceof Error ? err : new Error(String(err)),
      };
    }
  } catch (err) {
    return {
      ...base,
      state: "anonymous",
      error: err instanceof Error ? err : new Error(String(err)),
    };
  }
}

/** Reads the single shared viewer-access store from ProductAuthProvider. Every screen calls this so
 *  they all flip together on sign-in AND sign-out — no per-component useState, no per-mount refetch,
 *  no state islands. The provider owns the fetch/derivation (resolveViewerAccessSnapshot) and the
 *  refetch on every auth transition. */
export function useViewerAccess(): ViewerAccessResult {
  return useViewerAccessContext();
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

/** Lists records of one type via client.listRecords({ type }); refresh re-lists. */
export function useRecords(type: string): UseRecordsResult {
  const [records, setRecords] = useState<RecordItem[]>([]);
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
      setRecords(list);
      setError(null);
    } catch (err) {
      if (!aliveRef.current) return;
      setRecords([]);
      setError(err instanceof Error ? err : new Error(String(err)));
    } finally {
      if (aliveRef.current) setLoading(false);
    }
  }, [type]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

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
