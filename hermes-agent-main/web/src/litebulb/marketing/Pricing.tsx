/* SCREEN: Pricing (public) · route: /pricing · auth: public
   hooks: Stripe price IDs; CTA → Auth modal → (after auth) plan select/checkout. */
import { MarketingShell } from "./MarketingShell";
import type { AuthMode } from "../auth/AuthModal";

const PLANS = [
  { id: "free", name: "Free", price: "$0", blurb: "Kick the tires.", features: ["1 company", "5 task credits / mo", "Community support"] },
  { id: "pro", name: "Pro", price: "$40", per: "/mo", blurb: "For builders shipping for real.", features: ["5 companies", "500 task credits / mo", "Custom domains", "Priority agent"], featured: true },
  { id: "scale", name: "Scale", price: "$200", per: "/mo", blurb: "Portfolio on autopilot.", features: ["Unlimited companies", "5,000 task credits / mo", "Bigger ad budgets", "Email support"] },
];

export function Pricing({ onNav, onAuth }: { onNav: (h: string) => void; onAuth: (m: AuthMode) => void }) {
  return (
    <MarketingShell onNav={onNav} onAuth={onAuth}>
      <section className="lb-pricing">
        <h1 className="lb-pricing__h1">Start free. Pay when it pays.</h1>
        <p className="lb-pricing__sub">Every plan ships a real product and finds real users. Upgrade as your companies grow.</p>
        <div className="lb-pricing__grid">
          {PLANS.map((p) => (
            <div key={p.id} className={`lb-plan${p.featured ? " is-featured" : ""}`}>
              {p.featured && <span className="lb-plan__tag">Most popular</span>}
              <div className="lb-plan__name">{p.name}</div>
              <div className="lb-plan__price">{p.price}<span className="lb-plan__per">{p.per ?? ""}</span></div>
              <div className="lb-plan__blurb">{p.blurb}</div>
              <ul className="lb-plan__feats">{p.features.map((f) => <li key={f}>{f}</li>)}</ul>
              <button className={`b44-btn ${p.featured ? "b44-btn--brand" : "b44-btn--outline"} lb-plan__cta`} onClick={() => onAuth("signup")}>
                {p.id === "free" ? "Start building" : `Get ${p.name}`}
              </button>
            </div>
          ))}
        </div>
      </section>
    </MarketingShell>
  );
}
