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
    workspace,
    creativeCredits,
    sitePreviewUrl,
    traction,
    tractionRange,
    buildState,
    chatMessages,
    submitting,
    billingBusy,
    topupBusy,
    openBusiness,
    sendPrompt,
    createBusiness,
    openBillingPortal,
    startTopup,
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
    setSettings(path === "/settings/billing" ? "billing" : "profile");
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
    if (buildState.status !== "idle") return;
    void createBusiness(pendingIdea);
  }, [auth.status, buildState.status, createBusiness, path, pendingIdea]);

  useEffect(() => {
    if (path !== "/building") return;
    if (buildState.status !== "ready" || !buildState.businessSlug) return;
    const timer = window.setTimeout(() => {
      setPendingIdea("");
      nav(`/app/c/${buildState.businessSlug}`);
    }, 1200);
    return () => window.clearTimeout(timer);
  }, [buildState.businessSlug, buildState.status, path]);

  const gated = AUTHED(path) && auth.status === "out";

  const startBuild = (idea: string) => {
    const text = idea.trim();
    if (!text) return;
    setPendingIdea(text);
    if (auth.status !== "in") {
      setAuthModal("signup");
      return;
    }
    nav("/building");
  };

  const openSettings = (section: SettingsSection) => setSettings(section);
  const closeSettings = () => setSettings(null);

  let view;
  if (gated) {
    view = <Landing onLaunch={startBuild} onLogin={() => setAuthModal("login")} />;
  } else if (path === "/" || path === "") {
    view = auth.status === "in"
      ? (
          <AppHome
            companies={businesses}
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
    view = <Building idea={pendingIdea} state={buildState} onDone={() => buildState.businessSlug && nav(`/app/c/${buildState.businessSlug}`)} />;
  } else if (path.startsWith("/app/c/")) {
    view = activeBusiness && activeBusiness.slug === businessRouteSlug ? (
      <Product
        key={businessRouteSlug}
        business={activeBusiness}
        workspace={workspace}
        creativeCredits={creativeCredits}
        previewUrl={sitePreviewUrl}
        traction={traction}
        tractionRange={tractionRange}
        theme={theme}
        chatMessages={chatMessages}
        sending={submitting}
        onTheme={setTheme}
        onNav={nav}
        onLogout={auth.logout}
        onOpenSettings={openSettings}
        onSendPrompt={sendPrompt}
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
          portalBusy={billingBusy}
          topupBusy={topupBusy}
          onTheme={setTheme}
          onOpenPortal={openBillingPortal}
          onTopup={startTopup}
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
