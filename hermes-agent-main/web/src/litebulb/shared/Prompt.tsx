/* Shared hero prompt — ONE box used by both the logged-out landing and the
   logged-in home, so they're identical (same size, same controls). */
import { ArrowRight, Paperclip } from "./icons";
import "./prompt.css";

export function Prompt({
  value, onChange, onSubmit, placeholder, cta = "Start building",
}: {
  value: string;
  onChange: (v: string) => void;
  onSubmit: () => void;
  placeholder?: string;
  cta?: string;
}) {
  return (
    <div className="lb-prompt2">
      <textarea
        className="lb-prompt2__input"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        onKeyDown={(e) => { if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) onSubmit(); }}
        placeholder={placeholder}
        aria-label="Describe the company you want to build"
      />
      <div className="lb-prompt2__bar">
        <span className="lb-prompt2__attach" aria-hidden="true"><Paperclip size={20} /></span>
        <span className="lb-prompt2__spacer" />
        <button className="b44-btn b44-btn--brand lb-prompt2__cta" onClick={onSubmit}>
          {cta} <ArrowRight size={18} />
        </button>
      </div>
    </div>
  );
}
