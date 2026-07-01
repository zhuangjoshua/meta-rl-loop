import {
  createContext,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import type { Provider, SupabaseClient } from "@supabase/supabase-js";
import { useLocation, useNavigate } from "react-router-dom";
import { surfaceContext } from "@takyon/surface-context.js";
import { client } from "./takyon";

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

async function getSupabaseBrowserClient(config: SurfaceAuthConfig): Promise<SupabaseClient | null> {
  if (browserSupabaseClient !== undefined) return browserSupabaseClient;
  const url = String(config.url || "").trim();
  const publishableKey = String(config.publishableKey || "").trim();
  if (!url || !publishableKey) {
    browserSupabaseClient = null;
    return browserSupabaseClient;
  }
  const { createClient } = await import("@supabase/supabase-js");
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
  const available = runtimeHasAuthRail();
  // surfaceContext.auth is static at runtime; memoize so the config object
  // identity stays stable and the callback effect below doesn't re-subscribe
  // on every render.
  const config = useMemo(() => readSurfaceAuthConfig(), []);
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
        const supabase = await getSupabaseBrowserClient(config);
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
        const nextPath = location.pathname === "/" ? "/app" : location.pathname;
        // Full reload (not a client-side navigate) so the gated shell remounts and re-reads the freshly
        // minted session — every screen flips to signed-in. A client-side navigate leaves the already
        // mounted viewer-access state showing the stale anonymous view (the OAuth params are stripped
        // first, so the callback effect does not re-run on the reload).
        window.location.replace(`${nextPath}${stripOauthParams(location.search)}${location.hash}`);
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
  }, [config, configured, location.hash, location.pathname, location.search, navigate]);

  async function signInWithGoogle() {
    setBusy(true);
    setError(null);
    try {
      if (!configured) {
        throw new Error("Supabase Auth is not configured for this product.");
      }
      const supabase = await getSupabaseBrowserClient(config);
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
      const supabase = await getSupabaseBrowserClient(config);
      if (supabase) {
        const { error: signOutError } = await supabase.auth.signOut({ scope: "local" });
        if (signOutError) throw signOutError;
      }
      // Full reload so the gated shell remounts and re-reads the now-anonymous session — every screen
      // flips back to signed-out in place.
      window.location.replace("/");
    } catch (err) {
      setError(displayError(err));
    } finally {
      setBusy(false);
    }
  }

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
      {children}
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
