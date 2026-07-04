/* SCREEN: New company · route: /app/new · auth: authed · hooks: POST /companies
   Focused full-screen prompt. SWAPPABLE: <NewCompanyView> is standalone, so it can
   be dropped into a modal instead of this page wrapper without changing its guts. */
import { useMemo, useRef, useState } from "react";
import { BulbMark, ArrowRight } from "../shared/icons";
import type { TakyonArchetypeOption } from "../takyon/useTakyonLitebulb";
import "./newcompany.css";

const SUGGESTIONS = ["AI resume builder", "Local events app", "A niche newsletter", "Shopify plugin", "Habit tracker"];

type NewCompanyViewProps = {
  onCreate: (idea: string, archetype?: string) => void;
  archetypes?: TakyonArchetypeOption[];
};

export function NewCompanyView({ onCreate, archetypes }: NewCompanyViewProps) {
  const [idea, setIdea] = useState("");
  const ref = useRef<HTMLTextAreaElement>(null);
  // The toggle options come from the backend archetype registry (SSOT). Show it only when there is a
  // real choice to make (more than one option) — a single-option list adds noise, not signal.
  const options = useMemo(() => archetypes ?? [], [archetypes]);
  const defaultKey = useMemo(
    () => options.find((o) => o.default)?.key || options.find((o) => o.enabled)?.key || "web_saas",
    [options],
  );
  const [archetype, setArchetype] = useState<string>("");
  const selectedKey = archetype || defaultKey;
  const showToggle = options.length > 1;

  const submit = () => {
    const enabledKeys = new Set(options.filter((o) => o.enabled).map((o) => o.key));
    // Never submit a disabled ("coming soon") archetype; fall back to the default.
    const chosen = enabledKeys.has(selectedKey) ? selectedKey : defaultKey;
    onCreate(idea.trim(), chosen);
  };

  return (
    <div className="lb-new__body">
      <span className="lb-new__eyebrow">New company</span>
      <h1 className="lb-new__title">What do you want to build?</h1>
      <p className="lb-new__sub">One sentence is enough. Coscale names it, builds the product, and finds your first users.</p>

      {showToggle && (
        <div className="lb-new__arch" role="radiogroup" aria-label="What kind of product?">
          {options.map((opt) => {
            const active = opt.key === selectedKey;
            return (
              <button
                key={opt.key}
                type="button"
                role="radio"
                aria-checked={active}
                disabled={!opt.enabled}
                title={opt.description}
                className={`lb-new__arch-opt${active ? " is-active" : ""}${opt.enabled ? "" : " is-disabled"}`}
                onClick={() => opt.enabled && setArchetype(opt.key)}
              >
                <span className="lb-new__arch-label">{opt.label}</span>
                {!opt.enabled && <span className="lb-new__arch-soon">Coming soon</span>}
              </button>
            );
          })}
        </div>
      )}

      <div className="lb-new__prompt">
        <textarea
          ref={ref}
          className="lb-new__input"
          value={idea}
          onChange={(e) => setIdea(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) submit(); }}
          placeholder="An app that turns receipts into clean expense reports for freelancers"
          aria-label="Describe your company"
        />
        <div className="lb-new__foot">
          <span className="lb-new__spacer" />
          <button className="b44-btn b44-btn--brand lb-new__cta" onClick={submit}>
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
export function NewCompany({
  onCreate,
  onClose,
  archetypes,
}: {
  onCreate: (idea: string, archetype?: string) => void;
  onClose: () => void;
  archetypes?: TakyonArchetypeOption[];
}) {
  return (
    <div className="lb-new-page">
      <header className="lb-new__top">
        <button className="lb-new__brand" onClick={onClose} aria-label="Back"><BulbMark size={22} tone="ink" /> Coscale</button>
        <button className="lb-new__close" onClick={onClose} aria-label="Close">×</button>
      </header>
      <NewCompanyView onCreate={onCreate} archetypes={archetypes} />
    </div>
  );
}
