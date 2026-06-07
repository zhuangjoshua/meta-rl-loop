/* SCREEN: Home (app) · route: / (auth-aware) · auth: authed
   Standalone (NO sidebar): top nav (brand + account menu) + warm-glow hero prompt
   + enclosed Your-companies grid. The account menu opens the Settings modal.
   hooks: GET /companies, POST /companies (prompt → create) */
import { useState } from "react";
import { useAuth } from "../auth/useAuth";
import type { SettingsSection } from "../settings/Settings";
import { CompaniesGrid } from "./companies";
import { BulbMark } from "../shared/icons";
import { Prompt } from "../shared/Prompt";
import type { LitebulbBusiness } from "../takyon/useTakyonLitebulb";
import "./apphome.css";

const caret = (
  <svg width="13" height="13" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round"><path d="M4 6l4 4 4-4" /></svg>
);

function HomeNav({ user, onNav, onOpenSettings, onLogout }: {
  user: { name: string; email: string } | null;
  onNav: (hash: string) => void;
  onOpenSettings: (s: SettingsSection) => void;
  onLogout: () => void;
}) {
  const [menu, setMenu] = useState(false);
  const initial = (user?.name?.[0] ?? "U").toUpperCase();
  const close = () => setMenu(false);
  return (
    <header className="lb-homenav">
      <button className="lb-homenav__brand" onClick={() => onNav("/")} aria-label="Litebulb home">
        <BulbMark size={26} tone="ink" /> <span>Litebulb</span>
      </button>
      <div className="lb-homenav__acct">
        <button className={`lb-homenav__avatar${menu ? " is-open" : ""}`} onClick={() => setMenu((m) => !m)} aria-haspopup="menu" aria-expanded={menu}>
          <span className="lb-homenav__face">{initial}</span>
          <span className="lb-homenav__name">{user?.name ?? "Account"}</span>
          <span className="lb-homenav__caret">{caret}</span>
        </button>
        {menu && (
          <>
            <div className="lb-menu-scrim" onClick={close} />
            <div className="lb-homenav__menu" role="menu">
              <button role="menuitem" onClick={() => { onOpenSettings("profile"); close(); }}>Profile settings</button>
              <button role="menuitem" onClick={() => { onOpenSettings("billing"); close(); }}>Billing</button>
              <button role="menuitem" onClick={() => { onNav("/faq"); close(); }}>Help &amp; FAQ</button>
              <div className="lb-homenav__menu-sep" />
              <button role="menuitem" onClick={() => { onLogout(); close(); }}>Log out</button>
            </div>
          </>
        )}
      </div>
    </header>
  );
}

export function AppHome({
  companies,
  onNav, onStart, onOpen, onNew, onLogout, onOpenSettings,
}: {
  companies: LitebulbBusiness[];
  onNav: (hash: string) => void;
  onStart: (idea: string) => void;
  onOpen: (slug: string) => void;
  onNew: () => void;
  onLogout: () => void;
  onOpenSettings: (s: SettingsSection) => void;
}) {
  const { user } = useAuth();
  const [idea, setIdea] = useState("");
  const launch = () => onStart(idea.trim());
  const hasCompanies = companies.length > 0;

  return (
    <div className="lb-aph">
      <HomeNav user={user} onNav={onNav} onOpenSettings={onOpenSettings} onLogout={onLogout} />

      <section className="lb-aph__hero">
        <h1 className="lb-aph__greet">Ready to build{user?.name ? `, ${user.name}` : ""}?</h1>

        <Prompt value={idea} onChange={setIdea} onSubmit={launch}
          placeholder="An app that turns receipts into clean expense reports for freelancers" />

        {!hasCompanies && (
          <p className="lb-aph__firsthint">This is your first company — describe an idea above to begin.</p>
        )}
      </section>

      {hasCompanies && (
        <section className="lb-aph__cos">
          <div className="lb-cospanel">
            <div className="lb-cospanel__head">
              <h2 className="lb-cospanel__title">Your companies</h2>
              <button className="lb-cospanel__browse" onClick={onNew}>+ New company</button>
            </div>
            <div className="lb-cospanel__body">
              <CompaniesGrid companies={companies} onOpen={onOpen} onNew={onNew} />
            </div>
          </div>
        </section>
      )}
    </div>
  );
}
