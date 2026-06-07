/* SCREEN: Legal · route: /terms, /privacy · auth: public */
import { MarketingShell } from "./MarketingShell";
import type { AuthMode } from "../auth/AuthModal";

export function Legal({ kind, onNav, onAuth }: { kind: "terms" | "privacy"; onNav: (h: string) => void; onAuth: (m: AuthMode) => void }) {
  return (
    <MarketingShell onNav={onNav} onAuth={onAuth}>
      <section className="lb-legal">
        <h1>{kind === "terms" ? "Terms of Service" : "Privacy Policy"}</h1>
        <p>Placeholder — final copy from legal. This page is wired and routable so the real document can drop in.</p>
      </section>
    </MarketingShell>
  );
}
