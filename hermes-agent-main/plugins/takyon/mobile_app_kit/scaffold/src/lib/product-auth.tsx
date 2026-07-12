// Supabase native OAuth (mirror of the web scaffold's product-auth.tsx). Google sign-in via an
// in-app browser (expo-web-browser) with a PKCE flow; on success the Supabase access token is
// handed to the Takyon backend (loginWithSupabase), which mints the app session and returns the
// session_token we persist in the Keychain. No provider keys on device.
import React, { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import * as WebBrowser from "expo-web-browser";
import * as Linking from "expo-linking";
import { createClient, type SupabaseClient } from "@supabase/supabase-js";
import { surface, client } from "./takyon";

WebBrowser.maybeCompleteAuthSession();

interface AuthState {
  ready: boolean;
  authenticated: boolean;
  configured: boolean;
  user: any | null;
  account: any | null;
  tier: string;
  signInWithGoogle: () => Promise<void>;
  logoutLocal: () => Promise<void>;
  refresh: () => Promise<void>;
}

const Ctx = createContext<AuthState | null>(null);

function normalizedEntitlementStatus(entitlement: any): string {
  const status = String(entitlement?.status ?? "").trim().toLowerCase();
  if (status === "paid") return "active";
  if (status === "cancelled") return "canceled";
  return status;
}

export function hasNonterminalStripeSubscription(account: any): boolean {
  const entitlements = Array.isArray(account?.entitlements) ? account.entitlements : [];
  return entitlements.some((entitlement: any) => {
    const source = String(entitlement?.source ?? "").trim().toLowerCase();
    const subscriptionId = String(
      entitlement?.stripe_subscription_id ?? entitlement?.stripeSubscriptionId ?? "",
    ).trim();
    return (
      (!source || source === "stripe") &&
      Boolean(subscriptionId) &&
      !["canceled", "cancelled", "sandbox_retired"].includes(
        normalizedEntitlementStatus(entitlement),
      )
    );
  });
}

function makeSupabase(): SupabaseClient | null {
  const url = surface.auth?.url;
  const key = surface.auth?.publishableKey;
  if (!url || !key || url.startsWith("__")) return null;
  return createClient(url, key, {
    auth: { detectSessionInUrl: false, flowType: "pkce", persistSession: false },
  });
}

export function ProductAuthProvider({ children }: { children: React.ReactNode }) {
  const supabase = useMemo(makeSupabase, []);
  const configured = !!supabase && !!surface.auth?.googleProvider;
  const [ready, setReady] = useState(false);
  const [authenticated, setAuthenticated] = useState(false);
  const [user, setUser] = useState<any | null>(null);
  const [account, setAccount] = useState<any | null>(null);
  const [tier, setTier] = useState("unentitled");

  const refresh = useCallback(async () => {
    try {
      const s = await client.session();
      const signedIn = !!s?.authenticated;
      setAuthenticated(signedIn);
      if (!signedIn) {
        setUser(null);
        setAccount(null);
        setTier("unentitled");
        return;
      }
      setUser(s?.user ?? null);
      setTier(String(s?.tier ?? "unentitled"));
      try {
        const nextAccount = await client.account();
        setAccount(nextAccount ?? null);
        setUser(nextAccount?.user ?? s?.user ?? null);
        setTier(String(nextAccount?.user?.tier ?? s?.tier ?? "unentitled"));
      } catch {
        // Session truth remains valid when a display/account projection is temporarily unavailable.
        // Fail closed for cancellation by clearing stale entitlement data.
        setAccount(null);
      }
    } catch {
      setAuthenticated(false);
      setUser(null);
      setAccount(null);
      setTier("unentitled");
    } finally {
      setReady(true);
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const signInWithGoogle = useCallback(async () => {
    if (!supabase) throw new Error("auth_unconfigured");
    const redirectTo = Linking.createURL("/app");
    const { data, error } = await supabase.auth.signInWithOAuth({
      provider: (surface.auth?.googleProvider as any) || "google",
      options: { redirectTo, skipBrowserRedirect: true },
    });
    if (error || !data?.url) throw error ?? new Error("no_oauth_url");
    const result = await WebBrowser.openAuthSessionAsync(data.url, redirectTo);
    if (result.type !== "success" || !result.url) return;
    const code = Linking.parse(result.url).queryParams?.code;
    if (!code || typeof code !== "string") return;
    const { data: sess, error: exErr } = await supabase.auth.exchangeCodeForSession(code);
    if (exErr || !sess?.session?.access_token) throw exErr ?? new Error("exchange_failed");
    await client.loginWithSupabase(sess.session.access_token);
    await refresh();
  }, [supabase, refresh]);

  const logoutLocal = useCallback(async () => {
    try {
      await client.logout();
    } finally {
      setAuthenticated(false);
      setUser(null);
      setAccount(null);
      setTier("unentitled");
    }
  }, []);

  const value: AuthState = {
    ready,
    authenticated,
    configured,
    user,
    account,
    tier,
    signInWithGoogle,
    logoutLocal,
    refresh,
  };
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useProductAuth(): AuthState {
  const v = useContext(Ctx);
  if (!v) throw new Error("useProductAuth must be used within ProductAuthProvider");
  return v;
}
