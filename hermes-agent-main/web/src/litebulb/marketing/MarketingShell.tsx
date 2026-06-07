/* Marketing (logged-out) shell: top nav + footer. Used by Pricing, FAQ, Legal. */
import type { ReactNode } from "react";
import { BulbMark } from "../shared/icons";
import type { AuthMode } from "../auth/AuthModal";
import "./marketing.css";

export function MarketingShell({
  onNav, onAuth, children,
}: {
  onNav: (hash: string) => void;
  onAuth: (m: AuthMode) => void;
  children: ReactNode;
}) {
  return (
    <div className="lb-mk b44-root" data-theme="light">
      <header className="lb-mk__nav">
        <button className="lb-mk__brand" onClick={() => onNav("/")} aria-label="Litebulb home"><BulbMark size={26} tone="brand" /> <span>Litebulb</span></button>
        <nav className="lb-mk__links">
          <button onClick={() => onNav("/faq")}>FAQ</button>
          <button onClick={() => onNav("/privacy")}>Privacy</button>
        </nav>
        <div className="lb-mk__auth">
          <button className="lb-mk__signin" onClick={() => onAuth("login")}>Log in</button>
          <button className="b44-btn b44-btn--brand" onClick={() => onAuth("signup")}>Sign up</button>
        </div>
      </header>
      <main className="lb-mk__main">{children}</main>
      <footer className="lb-mk__foot">
        <span className="lb-mk__foot-brand"><BulbMark size={18} tone="brand" /> Litebulb</span>
        <span className="lb-mk__foot-meta">© 2026 Four Manifold</span>
      </footer>
    </div>
  );
}
