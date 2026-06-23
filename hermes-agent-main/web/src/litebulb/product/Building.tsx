import { useEffect, useRef } from "react";
import type { TakyonBusinessWorkspaceResponse } from "@/lib/api";
import type { BuildState } from "../takyon/useTakyonLitebulb";
import {
  chatStreamAgentMessages,
  formatPhaseDuration,
  livePhases,
  liveWorkerTasks,
  sanitizeCustomerReply,
  sanitizeTaskErrorText,
  workspaceChatSummary,
} from "@/lib/takyonCeoUpdates";
import { BulbMark } from "../shared/icons";
import "./building.css";

const clock = (index: number) => {
  const t = 9 * 3600 + 41 * 60 + 7 + index * 3;
  const h = Math.floor(t / 3600) % 24;
  const m = Math.floor((t % 3600) / 60);
  const s = t % 60;
  return [h, m, s].map((value) => String(value).padStart(2, "0")).join(":");
};

// Canonical product domain — product sub-apps are served at `<slug>.coscale.app`
// (same convention as product/Product.tsx). Show the REAL host as soon as the slug is
// known; never a fabricated `.app` placeholder.
const PRODUCT_BASE_DOMAIN = "coscale.app";

function canonicalProductHost(slug: string) {
  const clean = (slug || "").toLowerCase().replace(/[^a-z0-9-]/g, "");
  return clean ? `${clean}.${PRODUCT_BASE_DOMAIN}` : "";
}

function workspaceMatchesBuild(
  workspace: TakyonBusinessWorkspaceResponse | null | undefined,
  businessSlug: string,
) {
  const expected = (businessSlug || "").trim().toLowerCase();
  if (!workspace || !expected) return false;
  const actual = String((workspace as { business_slug?: unknown }).business_slug || "")
    .trim()
    .toLowerCase();
  return actual === expected;
}

export function Building({
  idea,
  state,
  workspace,
  onDone,
  onRetry,
  onBack,
}: {
  idea: string;
  state: BuildState;
  // The live business workspace, present once takyon.dashboard.create returns.
  // Its curated `chat_stream` is the conversational narration we render; the raw
  // narration/delta stream stays only in the collapsed 'View build details'
  // disclosure (never the customer-facing conversation).
  workspace?: TakyonBusinessWorkspaceResponse | null;
  onDone: () => void;
  onRetry?: () => void;
  onBack?: () => void;
}) {
  const narration = state.narration.length ? state.narration : [`Reading your idea — ${idea}.`];
  const terminal = state.terminal.length ? state.terminal : ["Booting Coscale CEO…"];
  const done = state.status === "ready";
  const errored = state.status === "error";
  const businessName = state.businessName.trim() || "your company";
  const scopedWorkspace = workspaceMatchesBuild(workspace, state.businessSlug) ? workspace : null;
  // Conversational progress stream — the CEO's curated, customer-safe chat_stream
  // narration (ordered oldest→newest), like a Claude/Lovable agent. No phase
  // ladder, no "What changed", no artifact list, no mid-thought planner/tool
  // text. Until the curated chat_stream has at least one item, the conversation
  // shows ONLY the single fixed "Reading your idea — …" line plus the thinking
  // dots. We deliberately do NOT fall back to customerNarrationLines(narration):
  // that is fed raw message deltas (the CEO's chain-of-thought), which belong
  // strictly inside the collapsed "View build details" disclosure, never in the
  // customer-facing conversation.
  const curatedStream = chatStreamAgentMessages(scopedWorkspace)
    .map((item) => sanitizeCustomerReply(item.text))
    .filter(Boolean);
  const stream = curatedStream.length
    ? curatedStream
    : [`Reading your idea — ${idea}.`];
  // Durable end-of-turn summary mirrored on the workspace; rendered as the
  // settled closing line when present (otherwise the static ready line below).
  const chatSummary = workspaceChatSummary(scopedWorkspace).trim();
  const running = !done && !errored;
  // Live per-step worker progress for the build screen's "Working on…" column —
  // the running/queued milestones plus the running milestone's runtime children
  // from the canonical workspace mirror. Each detail is sanitized at render so no
  // tool/path noun reaches the screen; capped to the most recent few. Empty
  // before the boot payload arrives (the column simply doesn't render).
  const workerTasks = (running ? liveWorkerTasks(scopedWorkspace) : []).slice(-6);
  // Deterministic, timed build-phase ladder (Landing -> Logo -> Search Console
  // -> Sign-on -> Subscription/account -> Product/launch), derived from the real
  // per-tool runtime traces + persisted durations on the workspace mirror. Shows
  // the customer that the landing is fast while the logo + Google step are the
  // long tail, and where sign-in/subscriptions get wired. Only renders once the
  // boot payload carries trace events (otherwise the array is all-queued with no
  // timing, and we hide it until the build actually starts moving).
  const phases = livePhases(scopedWorkspace);
  const showPhases = phases.some((phase) => phase.status !== "queued");
  const title = state.businessName.trim();
  // Real product host, shown from the moment the slug is known (as soon as
  // takyon.dashboard.create returns business_slug). No fake placeholder before then.
  const productHost = canonicalProductHost(state.businessSlug);

  const endRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [stream.length, running, done, errored, chatSummary]);

  return (
    <div className="lb-bld">
      <div className="lb-bld__inner">
        <header className="lb-bld__head">
          <span className="lb-bld__mark"><BulbMark size={26} tone="ink" /></span>
          <div className="lb-bld__head-txt">
            <span className="lb-bld__status"><span className="lb-bld__pulse" />{done ? "Workspace ready" : errored ? "Build failed" : "Coscale is building"}</span>
            {title ? <h1 className="lb-bld__name">{title}</h1> : null}
            {productHost ? (
              <a
                className="lb-bld__url"
                href={`https://${productHost}`}
                target="_blank"
                rel="noopener noreferrer"
              >
                {productHost}
              </a>
            ) : null}
          </div>
        </header>

        <div className="lb-bld__cols">
          <section className="lb-bld__chat" aria-live="polite">
            {/* Conversational progress stream — Claude/Lovable-style: each line is
                a small assistant message, newest last. No card, no phase ladder,
                no "What changed". */}
            {stream.map((line, index) => (
              <p key={`${index}-${line}`} className="lb-bld__line">{line}</p>
            ))}
            {/* Thinking indicator: its OWN row with just the animated dots, shown
                only while the build is running. It never stacks on the words. */}
            {running && (
              <div className="lb-bld__think" aria-label="Coscale is working">
                <span className="lb-typing"><i /><i /><i /></span>
              </div>
            )}
            {/* End-of-turn summary line once the workspace is ready or errored.
                Prefer the CEO's durable chat_summary when present; otherwise a
                warm static line. Suppressed if it would duplicate the tail of the
                curated stream. */}
            {done && (() => {
              const settled = chatSummary
                || `${businessName} is ready — handing you into the live operating surface now.`;
              if (settled === stream[stream.length - 1]) return null;
              return (
                <p className="lb-bld__line lb-bld__line--summary">{settled}</p>
              );
            })()}
            {errored && (
              <p className="lb-bld__line lb-bld__line--error">
                {sanitizeTaskErrorText(state.error) || "The build needs attention before it can continue."}
              </p>
            )}
            <div ref={endRef} />
          </section>

          <aside className="lb-bld__side">
            {/* Timed build-phase ladder — the canonical ordered bootstrap phases
                (landing -> logo -> Google -> sign-in -> subscriptions -> launch),
                each with a running/complete/queued dot and elapsed/final time
                derived from real per-tool events. Customer-safe warm labels (no
                tool names). Hidden until the build has actually started moving. */}
            {showPhases && (
              <div className="lb-bld__phases" aria-label="Build progress">
                <div className="lb-bld__label">Building your business</div>
                <ol className="lb-bld__phase-list">
                  {phases.map((phase) => {
                    const time = formatPhaseDuration(phase.durationS);
                    return (
                      <li
                        key={phase.id}
                        className={`lb-bld__phase lb-bld__phase--${phase.status}`}
                      >
                        <span
                          className={`lb-bld__phase-dot lb-bld__phase-dot--${phase.status}`}
                          aria-hidden="true"
                        />
                        <span className="lb-bld__phase-label">{phase.label}</span>
                        {time ? (
                          <span className="lb-bld__phase-time">{time}</span>
                        ) : phase.status === "queued" ? (
                          <span className="lb-bld__phase-time lb-bld__phase-time--queued">
                            queued
                          </span>
                        ) : null}
                      </li>
                    );
                  })}
                </ol>
              </div>
            )}
            {/* Live "Working on…" list — the canonical per-step worker progress
                from the workspace mirror, refreshed by the existing workspace
                poll. One compact row per current step (label + sanitized detail
                + a running-tone dot), newest last, so the long worker phase is
                legible on the screen the user is actually watching. Raw deltas
                stay strictly inside the collapsed disclosure below. */}
            {workerTasks.length > 0 && (
              <div className="lb-bld__work" aria-label="Working on">
                <div className="lb-bld__label">Working on…</div>
                <ul className="lb-bld__work-list">
                  {workerTasks.map((task) => {
                    const detail = sanitizeCustomerReply(task.detail);
                    const label = sanitizeCustomerReply(task.label ?? "") || detail || "Working";
                    return (
                      <li key={task.id} className="lb-bld__work-row">
                        <span className="lb-bld__work-dot" aria-hidden="true" />
                        <span className="lb-bld__work-label">{label}</span>
                        {detail && detail !== label ? (
                          <span className="lb-bld__work-detail">{detail}</span>
                        ) : null}
                      </li>
                    );
                  })}
                </ul>
              </div>
            )}
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
                  {errored && state.error && <li>{sanitizeTaskErrorText(state.error)}</li>}
                </ul>
              </div>
            </details>
          </aside>
        </div>

        <div className="lb-bld__foot">
          {done ? (
            <button className="b44-btn b44-btn--brand lb-bld__enter" onClick={onDone}>{title ? `Enter ${title} →` : "Enter workspace →"}</button>
          ) : errored ? (
            <div className="lb-bld__foot-actions">
              {onRetry ? (
                <button className="b44-btn b44-btn--brand lb-bld__retry" onClick={onRetry}>Try again</button>
              ) : null}
              {onBack ? (
                <button className="b44-btn lb-bld__back" onClick={onBack}>← Back</button>
              ) : null}
            </div>
          ) : null}
        </div>
      </div>
    </div>
  );
}
