import { useState } from "react";
import { ArrowRight, BulbMark, Globe } from "../shared/icons";
import { Prompt } from "../shared/Prompt";
import "./landing.css";

const SUGGESTIONS = ["AI resume builder", "Local events app", "A niche newsletter", "Shopify plugin"];

const LOOP = [
  { k: "01", t: "Describe it", d: "A sentence or two. Coscale picks the wedge and names the company." },
  { k: "02", t: "It ships", d: "A real product and a live site deploy to production." },
  { k: "03", t: "It grows", d: "Campaigns go live. Coscale doubles down on what converts." },
];

export function Landing({ onLaunch, onLogin }: { onLaunch: (idea: string) => void; onLogin: () => void }) {
  const [idea, setIdea] = useState("");

  const launch = () => onLaunch(idea);
  const pick = (s: string) => setIdea(s);
  const scrollTo = (id: string) => {
    document.getElementById(id)?.scrollIntoView({ behavior: "smooth", block: "start" });
  };

  return (
    <div className="lb-view lb-landing b44-root b44-hero" data-theme="light">
      {/* ── floating pill navbar ── */}
      <div className="lb-nav-wrap">
        <nav className="b44-navbar lb-nav" aria-label="Primary">
          <span className="b44-navbar__brand">
            <BulbMark size={26} tone="brand" />
            <span>Coscale</span>
          </span>
          <div className="b44-navbar__links">
            <button className="b44-navbar__link" type="button" onClick={() => scrollTo("lb-product")}>Product</button>
            <button className="b44-navbar__link" type="button" onClick={() => scrollTo("lb-how-it-works")}>How it works</button>
            <a className="b44-navbar__link" href="#/faq">FAQ</a>
            <a className="b44-navbar__link" href="#/privacy">Privacy</a>
          </div>
          <div className="lb-nav-right">
            <Globe className="lb-globe" />
            <a className="b44-navbar__link lb-signin" href="#" onClick={(e) => { e.preventDefault(); onLogin(); }}>Log in</a>
            <button className="b44-btn b44-btn--brand" onClick={launch}>Start building</button>
          </div>
        </nav>
      </div>

      {/* ── hero ── */}
      <main className="lb-hero" id="lb-product">
        <h1 className="lb-hero-title">Coscale — turn an idea into a running company.</h1>
        <p className="lb-hero-sub">
          Describe it. Coscale builds the product, launches it, and runs the
          business — autonomously.
        </p>

        {/* shared hero prompt — identical to the logged-in home */}
        <Prompt value={idea} onChange={setIdea} onSubmit={launch}
          placeholder="An app that turns receipts into clean expense reports for freelancers" />

        <p className="b44-eyebrow lb-eyebrow">Try one of these</p>
        <div className="lb-pills">
          {SUGGESTIONS.map((s) => (
            <button key={s} className="b44-tag" onClick={() => pick(s)}>{s}</button>
          ))}
        </div>
      </main>

      {/* ── how it works ── */}
      <section className="lb-loop" id="lb-how-it-works">
        <p className="b44-eyebrow lb-loop-eyebrow">The company-building loop</p>
        <h2 className="lb-loop-title">You bring the spark. It does the rest.</h2>
        <div className="lb-loop-grid">
          {LOOP.map((s) => (
            <article key={s.k} className="lb-loop-card">
              <span className="lb-loop-k">{s.k}</span>
              <h3>{s.t}</h3>
              <p>{s.d}</p>
            </article>
          ))}
        </div>
        <button className="b44-btn b44-btn--brand lb-loop-cta" onClick={launch}>
          Start building <ArrowRight size={17} />
        </button>
      </section>

      {/* ── footer ── */}
      <footer className="lb-foot">
        <span className="lb-foot-brand"><BulbMark size={20} tone="brand" /> Coscale</span>
        <span className="lb-foot-meta">© 2026 Four Manifold — Private beta</span>
      </footer>
    </div>
  );
}
