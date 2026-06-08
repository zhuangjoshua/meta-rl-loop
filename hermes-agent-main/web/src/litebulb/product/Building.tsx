import type { BuildState } from "../takyon/useTakyonLitebulb";
import { BulbMark } from "../shared/icons";
import "./building.css";

const PHASES = ["Understand", "Name", "Design", "Build", "Deploy", "Launch"];

const clock = (index: number) => {
  const t = 9 * 3600 + 41 * 60 + 7 + index * 3;
  const h = Math.floor(t / 3600) % 24;
  const m = Math.floor((t % 3600) / 60);
  const s = t % 60;
  return [h, m, s].map((value) => String(value).padStart(2, "0")).join(":");
};

export function Building({
  idea,
  state,
  onDone,
}: {
  idea: string;
  state: BuildState;
  onDone: () => void;
}) {
  const narration = state.narration.length ? state.narration : [`Reading your idea — ${idea}.`];
  const terminal = state.terminal.length ? state.terminal : ["Booting Litebulb CEO…"];
  const done = state.status === "ready";
  const errored = state.status === "error";
  const activeIdx = Math.min(PHASES.length - 1, Math.max(0, narration.length - 1));
  const pct = done ? 100 : Math.min(95, Math.max(8, narration.length * 12));
  const title = state.businessName.trim();

  return (
    <div className="lb-bld">
      <div className="lb-bld__inner">
        <header className="lb-bld__head">
          <span className="lb-bld__mark"><BulbMark size={26} tone="ink" /></span>
          <div className="lb-bld__head-txt">
            <span className="lb-bld__status"><span className="lb-bld__pulse" />{done ? "Workspace ready" : errored ? "Build blocked" : "Litebulb is building"}</span>
            {title ? <h1 className="lb-bld__name">{title}</h1> : null}
          </div>
        </header>

        <div className="lb-bld__cols">
          <div className="lb-bld__stream">
            {narration.map((line, index) => (
              <p key={`${index}-${line}`} className={`lb-bld__say${index === narration.length - 1 && !done && !errored ? " is-live" : ""}`}>{line}</p>
            ))}
            {errored && state.error && <p className="lb-bld__say is-live">{state.error}</p>}
          </div>

          <aside className="lb-bld__side">
            <ol className="lb-bld__phases">
              {PHASES.map((phase, index) => {
                const status = done || index < activeIdx ? "done" : index === activeIdx ? "active" : "todo";
                return (
                  <li key={phase} className={`lb-bld__phase is-${status}`}>
                    <span className="lb-bld__phase-ic" aria-hidden="true" />{phase}
                  </li>
                );
              })}
            </ol>
            <div className="lb-bld__term" aria-hidden="true">
              {terminal.map((line, index) => (
                <div key={`${index}-${line}`} className="lb-bld__termline"><span className="lb-bld__t">{clock(index)}</span>{line}</div>
              ))}
              {!done && !errored && <div className="lb-bld__termline"><span className="lb-bld__t">{clock(terminal.length)}</span><span className="lb-bld__blink">▋</span></div>}
            </div>
          </aside>
        </div>

        <div className="lb-bld__foot">
          <div className="lb-bld__bar"><span style={{ width: `${pct}%` }} /></div>
          {done
            ? <button className="b44-btn b44-btn--brand lb-bld__enter" onClick={onDone}>{title ? `Enter ${title} →` : "Enter workspace →"}</button>
            : errored
              ? <span className="lb-bld__pct">blocked</span>
              : <span className="lb-bld__pct">{pct}%</span>}
        </div>
      </div>
    </div>
  );
}
