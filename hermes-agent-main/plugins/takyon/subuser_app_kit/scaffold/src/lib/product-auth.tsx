import {
  createContext,
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
  }, [config, configured, location.hash, location.pathname, location.search, navigate]);

  async function signInWithGoogle() {
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
      navigate("/", { replace: true });
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
