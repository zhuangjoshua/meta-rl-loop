import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { createClient, type Provider, type SupabaseClient } from "@supabase/supabase-js";
import { useLocation, useNavigate } from "react-router-dom";
import { surfaceContext } from "@takyon/surface-context.js";
import { client } from "./takyon";
import {
  LOADING_VIEWER_ACCESS,
  resolveViewerAccessSnapshot,
  type ViewerAccessResult,
  type ViewerAccessSnapshot,
} from "./hooks";

interface SurfaceAuthConfig {
  provider?: string;
  configured?: boolean;
  url?: string;
  publishableKey?: string;
  googleProvider?: string;
  redirectPath?: string;
}

interface ProductAuthContextValue {
  available: boolean;
  configured: boolean;
  busy: boolean;
  error: string | null;
  signInWithGoogle: () => Promise<void>;
  logout: () => Promise<void>;
  clearError: () => void;
}

const ProductAuthContext = createContext<ProductAuthContextValue | null>(null);
/** The single shared viewer-access store. Hoisted out of useViewerAccess so all four screens read
 *  one source of truth and flip together on every auth transition (sign-in AND sign-out). */
const ViewerAccessContext = createContext<ViewerAccessResult | null>(null);
let browserSupabaseClient: SupabaseClient | null | undefined;

function isObject(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function readSurfaceAuthConfig(): SurfaceAuthConfig {
  if (!isObject(surfaceContext.auth)) return {};
  return surfaceContext.auth as SurfaceAuthConfig;
}

function runtimeHasAuthRail(): boolean {
  return Array.isArray(surfaceContext.runtimeFeatures) && surfaceContext.runtimeFeatures.includes("auth");
}

function normalizeRedirectPath(value: string | undefined): string {
  const text = String(value || "").trim();
  if (!text) return "/app";
  return text.startsWith("/") ? text : `/${text}`;
}

function stripOauthParams(search: string): string {
  const params = new URLSearchParams(search);
  for (const key of ["code", "error", "error_code", "error_description", "state"]) {
    params.delete(key);
  }
  const next = params.toString();
  return next ? `?${next}` : "";
}

function displayError(err: unknown): string {
  return err instanceof Error ? err.message : String(err || "Authentication failed");
}

function getSupabaseBrowserClient(config: SurfaceAuthConfig): SupabaseClient | null {
  if (browserSupabaseClient !== undefined) return browserSupabaseClient;
  const url = String(config.url || "").trim();
  const publishableKey = String(config.publishableKey || "").trim();
  if (!url || !publishableKey) {
    browserSupabaseClient = null;
    return browserSupabaseClient;
  }
  browserSupabaseClient = createClient(url, publishableKey, {
    auth: {
      detectSessionInUrl: false,
      flowType: "pkce",
    },
  });
  return browserSupabaseClient;
}

export function ProductAuthProvider({ children }: { children: ReactNode }) {
  const location = useLocation();
  const navigate = useNavigate();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const handledCallbackRef = useRef("");

  // Single shared viewer-access store. This is the ONE place the session+account are fetched and
  // derived; every screen reads it through context, so they all flip together on login/logout and
  // the duplicate /session + /account calls (one per island) collapse into one fetch per auth
  // change. `authenticated` here is also what gates a second sign-in below.
  const [viewer, setViewer] = useState<ViewerAccessSnapshot>(LOADING_VIEWER_ACCESS);
  const aliveRef = useRef(true);
  // Coalesce overlapping refreshes (StrictMode double-invoke, focus + login racing) so only the
  // freshest result wins and we don't thrash the store.
  const refreshSeqRef = useRef(0);

  useEffect(() => {
    aliveRef.current = true;
    return () => {
      aliveRef.current = false;
    };
  }, []);

  const refresh = useCallback(async () => {
    const seq = ++refreshSeqRef.current;
    setViewer((prev) => (prev.loading ? prev : { ...prev, loading: true }));
    const snapshot = await resolveViewerAccessSnapshot();
    // Drop stale results: only the most recent refresh may commit.
    if (!aliveRef.current || seq !== refreshSeqRef.current) return;
    setViewer(snapshot);
  }, []);

  // First read on mount, and a re-read whenever the tab regains focus / visibility — that catches a
  // login or logout that happened in another tab or a cookie that expired, which no in-app event
  // can observe.
  useEffect(() => {
    void refresh();
    if (typeof window === "undefined") return;
    const onFocus = () => {
      void refresh();
    };
    const onVisibility = () => {
      if (document.visibilityState === "visible") void refresh();
    };
    window.addEventListener("focus", onFocus);
    document.addEventListener("visibilitychange", onVisibility);
    return () => {
      window.removeEventListener("focus", onFocus);
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, [refresh]);

  const available = runtimeHasAuthRail();
  const config = readSurfaceAuthConfig();
  const configured =
    available &&
    config.provider === "supabase" &&
    config.configured === true &&
    Boolean(String(config.url || "").trim()) &&
    Boolean(String(config.publishableKey || "").trim());

  useEffect(() => {
    const params = new URLSearchParams(location.search);
    const code = String(params.get("code") || "").trim();
    const oauthError = String(
      params.get("error_description") || params.get("error") || "",
    ).trim();
    if (!code && !oauthError) return;
    const callbackKey = `${location.pathname}?${location.search}`;
    if (handledCallbackRef.current === callbackKey) return;
    handledCallbackRef.current = callbackKey;

    let cancelled = false;
    (async () => {
      setBusy(true);
      try {
        if (!configured) {
          throw new Error("Supabase Auth is not configured for this product.");
        }
        if (oauthError) {
          throw new Error(oauthError);
        }
        const supabase = getSupabaseBrowserClient(config);
        if (!supabase || !code) {
          throw new Error("Supabase Auth is not configured for this product.");
        }
        const { data, error: exchangeError } = await supabase.auth.exchangeCodeForSession(code);
        if (exchangeError) throw exchangeError;
        const accessToken = String(data.session?.access_token || "").trim();
        if (!accessToken) {
          throw new Error("Supabase session is missing an access token.");
        }
        await client.loginWithSupabase(accessToken);
        if (cancelled) return;
        setError(null);
        // The app_session cookie is now set server-side. Re-read the shared viewer store BEFORE
        // navigating so the gated shell sees `authenticated === true` on the first sign-in and never
        // falls back to a second "Continue with Google".
        await refresh();
        if (cancelled) return;
        const nextPath = location.pathname === "/" ? "/app" : location.pathname;
        navigate(`${nextPath}${stripOauthParams(location.search)}${location.hash}`, {
          replace: true,
        });
      } catch (err) {
        if (cancelled) return;
        setError(displayError(err));
        navigate(
          `${location.pathname}${stripOauthParams(location.search)}${location.hash}`,
          { replace: true },
        );
      } finally {
        if (!cancelled) setBusy(false);
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [config, configured, location.hash, location.pathname, location.search, navigate, refresh]);

  async function signInWithGoogle() {
    // Already signed in: never start a second OAuth round-trip / redundant loginWithSupabase. Just
    // route to the post-login destination. The shared viewer store makes `authenticated` reliable
    // here, so this guard (plus the button gating in the screens) makes a double login impossible.
    if (viewer.authenticated) {
      navigate(normalizeRedirectPath(config.redirectPath), { replace: true });
      return;
    }
    setBusy(true);
    setError(null);
    try {
      if (!configured) {
        throw new Error("Supabase Auth is not configured for this product.");
      }
      const supabase = getSupabaseBrowserClient(config);
      if (!supabase) {
        throw new Error("Supabase Auth is not configured for this product.");
      }
      const provider = (String(config.googleProvider || "google").trim() || "google") as Provider;
      const redirectTo = new URL(
        normalizeRedirectPath(config.redirectPath),
        window.location.origin,
      ).toString();
      const { error: signInError } = await supabase.auth.signInWithOAuth({
        provider,
        options: { redirectTo },
      });
      if (signInError) throw signInError;
    } catch (err) {
      setError(displayError(err));
      setBusy(false);
    }
  }

  async function logout() {
    setBusy(true);
    setError(null);
    try {
      await client.logout();
      const supabase = getSupabaseBrowserClient(config);
      if (supabase) {
        const { error: signOutError } = await supabase.auth.signOut({ scope: "local" });
        if (signOutError) throw signOutError;
      }
      // Re-read the shared viewer store so the gated shell drops back to anonymous in place after
      // sign-out — every screen flips together because they all read this one store.
      await refresh();
      navigate("/", { replace: true });
    } catch (err) {
      setError(displayError(err));
    } finally {
      setBusy(false);
    }
  }

  const viewerValue: ViewerAccessResult = { ...viewer, refresh };

  return (
    <ProductAuthContext.Provider
      value={{
        available,
        configured,
        busy,
        error,
        signInWithGoogle,
        logout,
        clearError: () => setError(null),
      }}
    >
      <ViewerAccessContext.Provider value={viewerValue}>{children}</ViewerAccessContext.Provider>
    </ProductAuthContext.Provider>
  );
}

export function useProductAuth(): ProductAuthContextValue {
  const value = useContext(ProductAuthContext);
  if (!value) {
    throw new Error("useProductAuth must be used inside ProductAuthProvider");
  }
  return value;
}

/** Non-throwing accessor for consumers that must still work if rendered outside
 *  ProductAuthProvider. */
export function useOptionalProductAuth(): ProductAuthContextValue | null {
  return useContext(ProductAuthContext);
}

/** Reads the single shared viewer-access store owned by ProductAuthProvider. useViewerAccess wraps
 *  this. Falls back to a stable loading snapshot if rendered outside the provider so screens never
 *  crash on a missing context. */
export function useViewerAccessContext(): ViewerAccessResult {
  const value = useContext(ViewerAccessContext);
  if (value) return value;
  return {
    ...LOADING_VIEWER_ACCESS,
    refresh: async () => {},
  };
}
