import type { BuildState } from "../takyon/useTakyonLitebulb";
import { deriveLinearArtifacts, deriveLiveWorkstreamCard, deriveWorkstreamItems } from "@/lib/takyonCeoUpdates";
import { BulbMark } from "../shared/icons";
import "./building.css";

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
  const terminal = state.terminal.length ? state.terminal : ["Booting Coscale CEO…"];
  const done = state.status === "ready";
  const errored = state.status === "error";
  const businessName = state.businessName.trim() || "your company";
  const progress = deriveLiveWorkstreamCard({
    running: !done && !errored,
    businessName,
    statusItems: narration,
    progressLines: terminal,
  });
  const fallbackItems = deriveWorkstreamItems({
    statusItems: narration,
    progressLines: terminal,
  }).items;
  const items = progress?.items.length ? progress.items : fallbackItems;
  // Linear, chronological artifact feed: each artifact the CEO writes appears in
  // first-seen order as it lands during bootstrap. Sourced append-only from the
  // narration + terminal stream (presentation only).
  const artifacts = deriveLinearArtifacts([...narration, ...terminal]);
  const completed = items.filter((item) => item.status === "complete");
  const title = state.businessName.trim();

  return (
    <div className="lb-bld">
      <div className="lb-bld__inner">
        <header className="lb-bld__head">
          <span className="lb-bld__mark"><BulbMark size={26} tone="ink" /></span>
          <div className="lb-bld__head-txt">
            <span className="lb-bld__status"><span className="lb-bld__pulse" />{done ? "Workspace ready" : errored ? "Build failed" : "Coscale is building"}</span>
            {title ? <h1 className="lb-bld__name">{title}</h1> : null}
          </div>
        </header>

        <div className="lb-bld__cols">
          <section className="lb-bld__card">
            <div className="lb-bld__eyebrow">CEO update</div>
            <h2 className="lb-bld__card-title">
              {errored
                ? `${businessName} needs attention`
                : done
                  ? `${businessName} workspace is ready`
                  : progress?.title || `Starting ${businessName}`}
            </h2>
            <p className="lb-bld__card-copy">
              {errored
                ? (state.error || "The build needs attention before it can continue.")
                : done
                  ? "The first company workspace is ready. I’m handing you into the live operating surface now."
                  : progress?.summary || "I’m moving this through the next business workstream now."}
            </p>

            {completed.length > 0 && (
              <div className="lb-bld__section">
                <div className="lb-bld__label">What changed</div>
                <ul className="lb-bld__list">
                  {completed.map((item) => (
                    <li key={item.key}>{item.completeLabel}</li>
                  ))}
                </ul>
              </div>
            )}

            {artifacts.length > 0 && (
              <div className="lb-bld__section">
                <div className="lb-bld__label">Artifacts</div>
                <ol className="lb-bld__artifacts">
                  {artifacts.map((artifact) => (
                    <li key={artifact.path} className={`lb-bld__artifact is-${artifact.category}`}>
                      <span className="lb-bld__artifact-ic" aria-hidden="true" />
                      <span className="lb-bld__artifact-name">{artifact.title}</span>
                      <span className="lb-bld__artifact-path">{artifact.path}</span>
                    </li>
                  ))}
                </ol>
              </div>
            )}

            {!errored && progress?.current && (
              <div className="lb-bld__section">
                <div className="lb-bld__label">Working now</div>
                <p className="lb-bld__text">
                  {progress.current}
                  {!done && <span className="lb-bld__inline-live">▋</span>}
                </p>
              </div>
            )}

            {errored ? null : progress?.next ? (
              <div className="lb-bld__section">
                <div className="lb-bld__label">Next</div>
                <p className="lb-bld__text">{progress.next}</p>
              </div>
            ) : null}

            {progress?.blocked && (
              <div className="lb-bld__section">
                <div className="lb-bld__label">Blocked</div>
                <p className="lb-bld__text">{progress.blocked}</p>
              </div>
            )}
          </section>

          <aside className="lb-bld__side">
            <details className="lb-bld__details">
              <summary>View build details</summary>
              <div className="lb-bld__term">
                {terminal.map((line, index) => (
                  <div key={`${index}-${line}`} className="lb-bld__termline"><span className="lb-bld__t">{clock(index)}</span>{line}</div>
                ))}
                {!done && !errored && <div className="lb-bld__termline"><span className="lb-bld__t">{clock(terminal.length)}</span><span className="lb-bld__blink">▋</span></div>}
              </div>
              <div className="lb-bld__raw">
                <div className="lb-bld__label">Narration</div>
                <ul className="lb-bld__raw-list">
                  {narration.map((line, index) => (
                    <li key={`${index}-${line}`}>{line}</li>
                  ))}
                  {errored && state.error && <li>{state.error}</li>}
                </ul>
              </div>
            </details>
          </aside>
        </div>

        <div className="lb-bld__foot">
          {done
            ? <button className="b44-btn b44-btn--brand lb-bld__enter" onClick={onDone}>{title ? `Enter ${title} →` : "Enter workspace →"}</button>
            : errored
              ? <span className="lb-bld__pct">failed</span>
              : null}
        </div>
      </div>
    </div>
  );
}
