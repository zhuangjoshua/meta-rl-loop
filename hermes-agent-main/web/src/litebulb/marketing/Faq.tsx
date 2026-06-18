/* SCREEN: FAQ / Help · route: /faq (also /help in-app) · auth: public */
import { MarketingShell } from "./MarketingShell";
import type { AuthMode } from "../auth/AuthModal";

const QA: { q: string; a: string }[] = [
  { q: "What does Coscale actually do?", a: "You describe a company in a sentence. Coscale builds the product, ships a real landing page, runs acquisition (SEO, ads, social), and reinvests in what converts — autonomously." },
  { q: "Do I need to write code?", a: "No. Coscale builds and deploys the product for you. You can chat to steer the business while it ships." },
  { q: "How does spending work?", a: "Takyon uses a wallet model for operator spend. Included balance is used first, then added funds, and you can top up from Billing." },
  { q: "Does it deploy somewhere real?", a: "Yes. Each business can publish a live product site, and the workspace shows the current preview directly inside the app." },
  { q: "How does billing work?", a: "Wallet top-ups run through Stripe checkout, and payment methods plus invoices are managed in Stripe's secure Customer Portal." },
  { q: "How do I manage the company after launch?", a: "From the workspace you can review deliverables, watch activity, inspect distribution artifacts, and keep working through the live CEO chat." },
];

export function Faq({ onNav, onAuth }: { onNav: (h: string) => void; onAuth: (m: AuthMode) => void }) {
  return (
    <MarketingShell onNav={onNav} onAuth={onAuth}>
      <section className="lb-faq">
        <h1 className="lb-faq__h1">Questions, answered.</h1>
        <div className="lb-faq__list">
          {QA.map((x) => (
            <div key={x.q} className="lb-faq__item">
              <h3>{x.q}</h3>
              <p>{x.a}</p>
            </div>
          ))}
        </div>
      </section>
    </MarketingShell>
  );
}
