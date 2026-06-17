/* SCREEN: Pricing (public) · route: /pricing · auth: public
   hooks: real operator tier catalog (config-driven, no free tier) → CTA → Auth modal →
   (after auth) Settings ▸ Plans subscription checkout. The tiers are NOT hardcoded copy:
   they come from the operator-owned catalog so this page never invents prices or a free tier
   (GOAL_RULES §3 inv9 + §5 creative-is-operator-owned). */
import { useEffect, useState } from "react";
import { MarketingShell } from "./MarketingShell";
import { api, type TakyonOperatorPlan } from "@/lib/api";
import type { AuthMode } from "../auth/AuthModal";

function formatUsd(cents: number | null | undefined) {
  if (typeof cents !== "number" || !Number.isFinite(cents) || cents <= 0) return "";
  return `$${(cents / 100).toFixed(cents % 100 === 0 ? 0 : 2)}`;
}

export function Pricing({ onNav, onAuth }: { onNav: (h: string) => void; onAuth: (m: AuthMode) => void }) {
  const [plans, setPlans] = useState<TakyonOperatorPlan[]>([]);
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    let cancelled = false;
    void api
      .listTakyonPublicOperatorPlans()
      .then((res) => {
        if (cancelled) return;
        setPlans(Array.isArray(res?.plans) ? res.plans : []);
      })
      .catch(() => {
        if (!cancelled) setPlans([]);
      })
      .finally(() => {
        if (!cancelled) setLoaded(true);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <MarketingShell onNav={onNav} onAuth={onAuth}>
      <section className="lb-pricing">
        <h1 className="lb-pricing__h1">Pay when it pays.</h1>
        <p className="lb-pricing__sub">Every plan ships a real product and finds real users. Upgrade as your companies grow.</p>
        {loaded && plans.length === 0 ? (
          <p className="lb-pricing__sub">Plans are being finalized — sign up and we'll get you started.</p>
        ) : (
          <div className="lb-pricing__grid">
            {plans.map((p) => (
              <div key={p.id} className={`lb-plan${p.featured ? " is-featured" : ""}`}>
                {p.featured && <span className="lb-plan__tag">Most popular</span>}
                <div className="lb-plan__name">{p.name}</div>
                {formatUsd(p.amount_cents) ? (
                  <div className="lb-plan__price">
                    {formatUsd(p.amount_cents)}
                    <span className="lb-plan__per">/{p.interval || "month"}</span>
                  </div>
                ) : null}
                {p.tagline ? <div className="lb-plan__blurb">{p.tagline}</div> : null}
                {p.weekly_allowance_cents ? (
                  <div className="lb-plan__blurb">{formatUsd(p.weekly_allowance_cents)} weekly included usage</div>
                ) : null}
                {p.features && p.features.length > 0 ? (
                  <ul className="lb-plan__feats">{p.features.map((f) => <li key={f}>{f}</li>)}</ul>
                ) : null}
                <button
                  className={`b44-btn ${p.featured ? "b44-btn--brand" : "b44-btn--outline"} lb-plan__cta`}
                  data-plan-id={p.id}
                  onClick={() => onAuth("signup")}
                >
                  Get {p.name}
                </button>
              </div>
            ))}
          </div>
        )}
      </section>
    </MarketingShell>
  );
}
