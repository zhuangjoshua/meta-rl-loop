/* Shared company-grid view. Uses generated thumbs as visual placeholders, but
   the company list itself comes from the real operator home payload. */
import "./companies.css";
import type { LitebulbBusiness } from "../takyon/useTakyonLitebulb";

/* A miniature of the company's own landing page — its tile (browser-framed). */
export function LandingThumb({ name, tagline }: { name: string; tagline: string }) {
  let h = 0;
  for (let i = 0; i < name.length; i++) h = (h * 31 + name.charCodeAt(i)) % 360;
  const bg = `radial-gradient(90% 80% at 50% -10%, hsl(${h} 80% 90%), hsl(${h} 45% 96%) 60%, #ffffff)`;
  const accent = `hsl(${h} 52% 48%)`;
  const url = name.toLowerCase().replace(/[^a-z0-9]/g, "") + ".app";
  return (
    <div className="lb-thumb">
      <div className="lb-thumb__bar"><i /><i /><i /><span className="lb-thumb__url">{url}</span></div>
      <div className="lb-thumb__page" style={{ background: bg }}>
        <div className="lb-thumb__nav">
          <span className="lb-thumb__brand"><span className="lb-thumb__logo" style={{ background: accent }} />{name}</span>
          <span className="lb-thumb__links"><i /><i /><i /></span>
          <span className="lb-thumb__navcta" style={{ background: accent }} />
        </div>
        <div className="lb-thumb__hero">
          <span className="lb-thumb__eyebrow" />
          <div className="lb-thumb__title">{tagline}</div>
          <div className="lb-thumb__subs"><i /><i /></div>
          <div className="lb-thumb__cta">Get started</div>
        </div>
      </div>
    </div>
  );
}

export function CompaniesGrid({
  companies,
  onOpen,
  onNew,
}: {
  companies: LitebulbBusiness[];
  onOpen: (slug: string) => void;
  onNew: () => void;
}) {
  return (
    <div className="lb-pf-grid">
      {companies.map((c, i) => (
        <button key={c.slug || c.name || i} className="lb-coCard" onClick={() => onOpen(c.slug)}>
          <span className="lb-coCard__thumb"><LandingThumb name={c.name} tagline={c.tagline || c.goal || c.name} /></span>
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
  );
}
