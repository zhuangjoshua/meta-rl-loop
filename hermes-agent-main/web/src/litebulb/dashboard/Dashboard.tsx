import { useState } from "react";
import { deriveCompany } from "../shared/company";
import { BulbMark } from "../shared/icons";
import "./dashboard.css";

/* Seed companies for the portfolio (the mockup's "all companies"). */
const SEED = [
  { idea: "An app that turns receipts into clean expense reports for freelancers", meta: "Live · 1,286 visitors" },
  { idea: "AI resume builder", meta: "Live · 240 signups" },
  { idea: "A niche newsletter", meta: "Building · MVP" },
  { idea: "Habit tracker", meta: "Live · $612 MRR" },
  { idea: "Shopify plugin for abandoned carts", meta: "Building · launch" },
  { idea: "Local events app", meta: "Live · 3.1k visitors" },
];

/* A miniature of the company's own landing page — used as its tile. */
function LandingThumb({ name, tagline }: { name: string; tagline: string }) {
  let h = 0;
  for (let i = 0; i < name.length; i++) h = (h * 31 + name.charCodeAt(i)) % 360;
  const bg = `radial-gradient(90% 80% at 50% -10%, hsl(${h} 80% 90%), hsl(${h} 45% 96%) 60%, #ffffff)`;
  return (
    <div className="lb-thumb">
      <div className="lb-thumb__bar"><i /><i /><i /></div>
      <div className="lb-thumb__page" style={{ background: bg }}>
        <div className="lb-thumb__nav">
          <span className="lb-thumb__logo" style={{ background: `hsl(${h} 46% 46%)` }} />
          {name}
        </div>
        <div className="lb-thumb__hero">
          <div className="lb-thumb__title">{tagline}</div>
          <div className="lb-thumb__prompt" />
          <div className="lb-thumb__cta">Get started</div>
        </div>
      </div>
    </div>
  );
}

export function Dashboard({
  onOpen, onNew, onLanding, theme, onToggleTheme,
}: {
  onOpen: (idea: string) => void;
  onNew: () => void;
  onLanding: () => void;
  theme: "light" | "dark";
  onToggleTheme: () => void;
}) {
  const companies = SEED.map((s) => ({ ...deriveCompany(s.idea), idea: s.idea, meta: s.meta }));
  const [menu, setMenu] = useState(false);

  return (
    <div className="lb-view lb-portfolio">
      <header className="lb-pf-top">
        <button className="lb-pf-brand" onClick={onLanding} aria-label="Coscale">
          <BulbMark size={24} tone="ink" />
          <span>Coscale</span>
        </button>
        <div className="lb-pf-actions">
          <button className="lb-pf-newbtn" onClick={onNew}>+ New company</button>
          <button className="lb-iconbtn2" aria-label="Toggle theme" onClick={onToggleTheme}>
            {theme === "dark" ? "☀" : "☾"}
          </button>
          <button className="lb-pf-avatar" onClick={() => setMenu((m) => !m)} aria-label="Account">
            <span className="lb-pf-avatar__face" />
          </button>
        </div>
      </header>

      <main className="lb-pf-main">
        <div className="lb-pf-head">
          <h1 className="lb-pf-h1">Your companies</h1>
          <p className="lb-pf-sub">{companies.length} building autonomously</p>
        </div>

        <div className="lb-pf-grid">
          {companies.map((c, i) => (
            <button key={c.name + i} className="lb-coCard" onClick={() => onOpen(c.idea)}>
              <span className="lb-coCard__thumb"><LandingThumb name={c.name} tagline={c.tagline} /></span>
              <span className="lb-coCard__name">{c.name}</span>
              <span className="lb-coCard__meta">{c.meta}</span>
            </button>
          ))}

          <button className="lb-coCard lb-coCard--new" onClick={onNew}>
            <span className="lb-coCard__thumb lb-coCard__thumb--new"><span className="lb-coCard__plus">+</span></span>
            <span className="lb-coCard__name">New company</span>
            <span className="lb-coCard__meta">Describe an idea</span>
          </button>
        </div>
      </main>

      {menu && <div className="lb-menu-scrim" onClick={() => setMenu(false)} />}
    </div>
  );
}
