/* SCREEN: New company · route: /app/new · auth: authed · hooks: POST /companies
   Focused full-screen prompt. SWAPPABLE: <NewCompanyView> is standalone, so it can
   be dropped into a modal instead of this page wrapper without changing its guts. */
import { useRef, useState } from "react";
import { BulbMark, ArrowRight } from "../shared/icons";
import "./newcompany.css";

const SUGGESTIONS = ["AI resume builder", "Local events app", "A niche newsletter", "Shopify plugin", "Habit tracker"];

export function NewCompanyView({ onCreate }: { onCreate: (idea: string) => void }) {
  const [idea, setIdea] = useState("");
  const ref = useRef<HTMLTextAreaElement>(null);
  return (
    <div className="lb-new__body">
      <span className="lb-new__eyebrow">New company</span>
      <h1 className="lb-new__title">What do you want to build?</h1>
      <p className="lb-new__sub">One sentence is enough. Coscale names it, builds the product, and finds your first users.</p>

      <div className="lb-new__prompt">
        <textarea
          ref={ref}
          className="lb-new__input"
          value={idea}
          onChange={(e) => setIdea(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) onCreate(idea.trim()); }}
          placeholder="An app that turns receipts into clean expense reports for freelancers"
          aria-label="Describe your company"
        />
        <div className="lb-new__foot">
          <span className="lb-new__spacer" />
          <button className="b44-btn b44-btn--brand lb-new__cta" onClick={() => onCreate(idea.trim())}>
            Create company <ArrowRight size={16} />
          </button>
        </div>
      </div>

      <div className="lb-new__pills">
        {SUGGESTIONS.map((s) => (
          <button key={s} className="lb-new__pill" onClick={() => { setIdea(s); ref.current?.focus(); }}>{s}</button>
        ))}
      </div>
    </div>
  );
}

/* Full-screen page wrapper (the current container; swap for a modal later). */
export function NewCompany({ onCreate, onClose }: { onCreate: (idea: string) => void; onClose: () => void }) {
  return (
    <div className="lb-new-page">
      <header className="lb-new__top">
        <button className="lb-new__brand" onClick={onClose} aria-label="Back"><BulbMark size={22} tone="ink" /> Coscale</button>
        <button className="lb-new__close" onClick={onClose} aria-label="Close">×</button>
      </header>
      <NewCompanyView onCreate={onCreate} />
    </div>
  );
}
