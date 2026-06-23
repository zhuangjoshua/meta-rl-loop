import { useEffect, useState } from "react";
import { AuthProvider, useAuth } from "./auth/useAuth";
import { AuthModal, type AuthMode } from "./auth/AuthModal";
import { Landing } from "./landing/Landing";
import { AppHome } from "./app/AppHome";
import { NewCompany } from "./app/NewCompany";
import { Product } from "./product/Product";
import { Building } from "./product/Building";
import { Settings, type SettingsSection } from "./settings/Settings";
import { Faq } from "./marketing/Faq";
import { Legal } from "./marketing/Legal";
import { NotFound } from "./common/NotFound";
import { useTakyonLitebulb } from "./takyon/useTakyonLitebulb";

export type Theme = "light" | "dark";

const PENDING_IDEA_KEY = "litebulb.pending.idea";

const nav = (hash: string) => { window.location.hash = hash; };

const usePath = () => {
  const read = () => window.location.hash.replace(/^#/, "") || "/";
  const [path, setPath] = useState(read);
  useEffect(() => {
    const onHash = () => {
      setPath(read());
      window.scrollTo({ top: 0 });
    };
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
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
    walletBalance,
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
    resetBuildState,
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
    // GOAL_RULES §3 gap #2: defense in depth — even if /building is reached directly, never call
    // createBusiness until the account balance has loaded AND is positive. A null balance (account
    // not yet loaded) must NOT silently create; bounce to billing instead of spending.
    if (walletBalance === null) return;
    if (walletBalance <= 0) {
      setBillingNudge("Subscribe to a plan to create a company.");
      setSettings("plans");
      nav("/");
      return;
    }
    void createBusiness(pendingIdea);
    // Clear the auto-create trigger the instant we initiate, so a RELOAD of /building (which resets
    // the in-memory buildState to "idle") cannot re-fire createBusiness and mint+bill a SECOND
    // company. The build screen reads its idea from buildState.goal (set by createBusiness) below.
    setPendingIdea("");
  }, [auth.status, buildState.goal, buildState.status, createBusiness, path, pendingIdea, resetBuildState, setPendingIdea, walletBalance]);

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
    // GOAL_RULES §3 gap #2 (frontend half): never enter /building (which auto-calls createBusiness)
    // for an operator who cannot pay. walletBalance is spendable_cents (subscription allowance remaining).
    //   * null  → account not loaded yet: defer (do NOT create); the user can retry once it loads.
    //   * <= 0  → no funds: open Plans instead of /building and show a subscribe nudge.
    // The backend preflight (takyon.dashboard.create → _operator_create_balance_preflight) is the
    // authoritative gate; this is the UX guard so no /building screen is shown for a zero balance.
    if (walletBalance === null) {
      setBillingNudge("Loading your balance… try again in a moment.");
      setSettings("billing");
      return;
    }
    if (walletBalance <= 0) {
      setBillingNudge("Subscribe to a plan to create a company.");
      setSettings("plans");
      return;
    }
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
      {view}
      {authModal && (
        <AuthModal
          mode={authModal}
          onClose={() => setAuthModal(null)}
          onAuthed={() => auth.login()}
          onSwitch={() => setAuthModal((mode) => (mode === "signup" ? "login" : "signup"))}
        />
      )}
      {settings && (
        <Settings
          section={settings}
          theme={theme}
          account={account}
          businesses={businesses}
          portalBusy={billingBusy}
          nudge={billingNudge}
          subscribeBusy={subscribeBusy}
          onTheme={setTheme}
          onOpenPortal={openBillingPortal}
          onSubscribe={subscribeToPlan}
          onBuyCreditPack={startCreativeCreditPackCheckout}
          onClose={closeSettings}
        />
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
