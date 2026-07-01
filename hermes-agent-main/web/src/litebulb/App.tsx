import { lazy, Suspense, useEffect, useState } from "react";
import { AuthProvider, useAuth } from "./auth/useAuth";
import { AuthModal, type AuthMode } from "./auth/AuthModal";
import { Landing } from "./landing/Landing";
import { AppHome } from "./app/AppHome";
import type { SettingsSection } from "./settings/Settings";
import { useTakyonLitebulb } from "./takyon/useTakyonLitebulb";

// Rarely-used / authed surfaces are code-split so they stay off the initial
// critical-path bundle. Only Landing/AppHome are eager for the first paint.
const NewCompany = lazy(() =>
  import("./app/NewCompany").then((m) => ({ default: m.NewCompany })),
);
const Product = lazy(() =>
  import("./product/Product").then((m) => ({ default: m.Product })),
);
const Building = lazy(() =>
  import("./product/Building").then((m) => ({ default: m.Building })),
);
const Settings = lazy(() =>
  import("./settings/Settings").then((m) => ({ default: m.Settings })),
);
const Faq = lazy(() =>
  import("./marketing/Faq").then((m) => ({ default: m.Faq })),
);
const Legal = lazy(() =>
  import("./marketing/Legal").then((m) => ({ default: m.Legal })),
);
const NotFound = lazy(() =>
  import("./common/NotFound").then((m) => ({ default: m.NotFound })),
);

export type { SettingsSection };

export type Theme = "light" | "dark";

const PENDING_IDEA_KEY = "litebulb.pending.idea";

const nav = (hash: string) => { window.location.hash = hash; };

const usePath = () => {
  const read = () => window.location.hash.replace(/^#/, "") || "/";
  const [path, setPath] = useState(read);
  useEffect(() => {
    const syncPath = (resetScroll = false) => {
      const next = read();
      setPath((current) => (current === next ? current : next));
      if (resetScroll) window.scrollTo({ top: 0 });
    };
    const onHash = () => syncPath(true);
    const onPageShow = () => syncPath(false);
    window.addEventListener("hashchange", onHash);
    window.addEventListener("popstate", onHash);
    window.addEventListener("pageshow", onPageShow);
    return () => {
      window.removeEventListener("hashchange", onHash);
      window.removeEventListener("popstate", onHash);
      window.removeEventListener("pageshow", onPageShow);
    };
  }, []);
  return path;
};

function readPendingIdea() {
  try {
    return sessionStorage.getItem(PENDING_IDEA_KEY) || "";
  } catch {
    return "";
  }
}

function writePendingIdea(value: string) {
  try {
    if (value) sessionStorage.setItem(PENDING_IDEA_KEY, value);
    else sessionStorage.removeItem(PENDING_IDEA_KEY);
  } catch {
    /* best effort */
  }
}

const AUTHED = (path: string) =>
  path === "/app/new" || path === "/building" || path.startsWith("/app/c") || path.startsWith("/settings");

function Router() {
  const auth = useAuth();
  const {
    businesses,
    account,
    activeBusiness,
    workspace,
    creativeCredits,
    traction,
    tractionRange,
    buildState,
    chatMessages,
    submitting,
    sessionRunning,
    billingBusy,
    subscribeBusy,
    subscribeError,
    resetBuildState,
    openBusiness,
    sendPrompt,
    stopPrompt,
    createBusiness,
    saveChannelCreditBudgets,
    setBusinessWakeState,
    wakeBusinessNow,
    deleteBusiness,
    startCreativeCreditCheckout,
    startCreativeCreditPackCheckout,
    openBillingPortal,
    subscribeToPlan,
    setTractionRange,
  } = useTakyonLitebulb();
  const path = usePath();
  const businessRouteSlug = path.startsWith("/app/c/")
    ? path.slice("/app/c/".length).split("/")[0].trim().toLowerCase()
    : "";

  const [theme, setThemeState] = useState<Theme>(() => {
    try {
      return localStorage.getItem("lb-theme") === "dark" ? "dark" : "light";
    } catch {
      return "light";
    }
  });
  const [authModal, setAuthModal] = useState<AuthMode | null>(null);
  const [settings, setSettings] = useState<SettingsSection | null>(null);
  const [billingNudge, setBillingNudge] = useState<string>("");
  const [pendingIdea, setPendingIdea] = useState(() => readPendingIdea());

  const setTheme = (value: Theme) => {
    setThemeState(value);
    try {
      localStorage.setItem("lb-theme", value);
    } catch {
      /* best effort */
    }
  };

  useEffect(() => {
    const onAppSurface = auth.status === "in" && (path === "/" || path === "" || AUTHED(path));
    document.documentElement.setAttribute("data-theme", onAppSurface ? theme : "light");
  }, [auth.status, path, theme]);

  useEffect(() => {
    writePendingIdea(pendingIdea);
  }, [pendingIdea]);

  useEffect(() => {
    if (!path.startsWith("/settings")) return;
    setSettings(
      path === "/settings/billing"
        ? "billing"
        : path === "/settings/plans"
          ? "plans"
          : "profile",
    );
  }, [path]);

  useEffect(() => {
    if (auth.status !== "in") return;
    if (!businessRouteSlug) return;
    void openBusiness(businessRouteSlug);
  }, [auth.status, businessRouteSlug, openBusiness]);

  useEffect(() => {
    if (auth.status !== "in") return;
    if (path !== "/building") return;
    if (!pendingIdea) return;
    if ((buildState.status === "ready" || buildState.status === "error") && buildState.goal !== pendingIdea) {
      // A fresh build request must not inherit the previous company's redirect state.
      resetBuildState();
      return;
    }
    if (buildState.status !== "idle") return;
    // Operator create is ungated from the plan (dogfooding): no wallet-balance precondition. The
    // per-turn runtime usage gate remains the spend chokepoint; subuser/product rails stay gated.
    void createBusiness(pendingIdea);
    // Clear the auto-create trigger the instant we initiate, so a RELOAD of /building (which resets
    // the in-memory buildState to "idle") cannot re-fire createBusiness and mint a SECOND company.
    // The build screen reads its idea from buildState.goal (set by createBusiness) below.
    setPendingIdea("");
  }, [auth.status, buildState.goal, buildState.status, createBusiness, path, pendingIdea, resetBuildState, setPendingIdea]);

  useEffect(() => {
    if (path !== "/building") return;
    if (buildState.status !== "ready" || !buildState.businessSlug) return;
    const timer = window.setTimeout(() => {
      setPendingIdea("");
      nav(`/app/c/${buildState.businessSlug}`);
    }, 1200);
    return () => window.clearTimeout(timer);
  }, [buildState.businessSlug, buildState.status, path]);

  // Never strand the user on the /building loading screen after a hard failure.
  // 4030 (out of credits) is the one error with a clear destination: auto-route to
  // Plans & Billing with a subscribe nudge, mirroring the pre-create guard above.
  // Every OTHER hard error stays on the build screen, which now renders the human
  // message plus a Back / Try-again control (see Building.tsx) — actionable, not stranded.
  useEffect(() => {
    if (path !== "/building") return;
    if (buildState.status !== "error") return;
    if (buildState.errorCode !== 4030) return;
    // Clear the pending idea so re-entering /building doesn't immediately re-create.
    setPendingIdea("");
    setBillingNudge(buildState.error || "Subscribe to a plan to create a company.");
    setSettings("plans");
    nav("/");
  }, [buildState.status, buildState.errorCode, buildState.error, path]);

  // Build-screen exits for the non-4030 errored state. Try-again clears the build
  // and re-enters /building (which re-runs createBusiness); Back returns home.
  const retryBuild = () => {
    const idea = (buildState.goal || pendingIdea).trim();
    resetBuildState();
    if (idea) {
      setPendingIdea(idea);
      nav("/building");
    } else {
      nav("/app/new");
    }
  };
  const exitBuild = () => {
    resetBuildState();
    setPendingIdea("");
    nav("/");
  };

  const gated = AUTHED(path) && auth.status === "out";

  const startBuild = (idea: string) => {
    const text = idea.trim();
    if (!text) return;
    resetBuildState();
    setPendingIdea(text);
    if (auth.status !== "in") {
      setAuthModal("signup");
      return;
    }
    // Operator create is ungated from the plan (dogfooding): no wallet-balance precondition here, so
    // /building proceeds regardless of the operator's plan/allowance. The per-turn runtime usage
    // gate remains the real spend chokepoint; subuser/product rails stay gated.
    nav("/building");
  };

  const openSettings = (section: SettingsSection) => setSettings(section);
  const closeSettings = () => {
    setSettings(null);
    setBillingNudge("");
  };

  let view;
  if (gated) {
    view = <Landing onLaunch={startBuild} onLogin={() => setAuthModal("login")} />;
  } else if (path === "/" || path === "") {
    view = auth.status === "in"
      ? (
          <AppHome
            companies={businesses}
            account={account}
            onNav={nav}
            onStart={startBuild}
            onOpen={(slug) => nav(`/app/c/${slug}`)}
            onNew={() => nav("/app/new")}
            onDelete={deleteBusiness}
            onLogout={auth.logout}
            onOpenSettings={openSettings}
          />
        )
      : <Landing onLaunch={startBuild} onLogin={() => setAuthModal("login")} />;
  } else if (path === "/faq" || path === "/help") {
    view = <Faq onNav={nav} onAuth={() => setAuthModal("login")} />;
  } else if (path === "/terms" || path === "/privacy") {
    view = <Legal kind={path === "/terms" ? "terms" : "privacy"} onNav={nav} onAuth={() => setAuthModal("login")} />;
  } else if (path === "/app/new") {
    view = <NewCompany onCreate={startBuild} onClose={() => nav("/")} />;
  } else if (path === "/building") {
    view = (
      <Building
        idea={buildState.goal || pendingIdea}
        state={buildState}
        workspace={workspace}
        onDone={() => buildState.businessSlug && nav(`/app/c/${buildState.businessSlug}`)}
        onRetry={retryBuild}
        onBack={exitBuild}
      />
    );
  } else if (path.startsWith("/app/c/")) {
    view = activeBusiness && activeBusiness.slug === businessRouteSlug ? (
      <Product
        key={businessRouteSlug}
        business={activeBusiness}
        workspace={workspace}
        creativeCredits={creativeCredits}
        traction={traction}
        tractionRange={tractionRange}
        theme={theme}
        chatMessages={chatMessages}
        sending={submitting}
        sessionRunning={sessionRunning}
        onTheme={setTheme}
        onNav={nav}
        onLogout={auth.logout}
        onOpenSettings={openSettings}
        onSendPrompt={sendPrompt}
        onStopPrompt={stopPrompt}
        onSaveChannelCreditBudgets={saveChannelCreditBudgets}
        onSetWakeState={setBusinessWakeState}
        onWakeNow={wakeBusinessNow}
        onBuyCreativeCredits={startCreativeCreditCheckout}
        onTractionRangeChange={setTractionRange}
      />
    ) : null;
  } else {
    view = <NotFound onHome={() => nav("/")} />;
  }

  return (
    <>
      <Suspense fallback={null}>{view}</Suspense>
      {authModal && (
        <AuthModal
          mode={authModal}
          onClose={() => setAuthModal(null)}
          onAuthed={() => auth.login()}
          onSwitch={() => setAuthModal((mode) => (mode === "signup" ? "login" : "signup"))}
        />
      )}
      {settings && (
        <Suspense fallback={null}>
        <Settings
          section={settings}
          theme={theme}
          account={account}
          businesses={businesses}
          portalBusy={billingBusy}
          nudge={subscribeError || billingNudge}
          subscribeBusy={subscribeBusy}
          onTheme={setTheme}
          onOpenPortal={openBillingPortal}
          onSubscribe={subscribeToPlan}
          onBuyCreditPack={startCreativeCreditPackCheckout}
          onClose={closeSettings}
        />
        </Suspense>
      )}
    </>
  );
}

export function App() {
  return (
    <AuthProvider>
      <Router />
    </AuthProvider>
  );
}
