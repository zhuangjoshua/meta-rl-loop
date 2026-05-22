"use client";

import Link from "next/link";
import { useMemo, useState, FormEvent } from "react";

type Correction = { before?: string; after?: string; reason?: string };
type ProductResult = {
  title?: string;
  style?: string;
  latex?: string;
  corrections?: Correction[];
  sections?: string[];
  summary?: string;
};

type RunResponse = {
  ok?: boolean;
  error?: string;
  result?: ProductResult | string;
  output?: ProductResult | string;
  data?: ProductResult | string;
};

const STYLES = [
  { id: "arxiv", label: "arXiv preprint", hint: "\\documentclass{article} with amsmath/amssymb." },
  { id: "ieee", label: "IEEE conference", hint: "IEEEtran two-column layout." },
  { id: "lecture", label: "Lecture notes", hint: "Article class, larger margins, theorem env." }
];

function parseResult(payload: RunResponse | null): ProductResult | null {
  if (!payload) return null;
  const candidate = payload.result ?? payload.output ?? payload.data;
  if (!candidate) return null;
  if (typeof candidate === "string") {
    try {
      const parsed = JSON.parse(candidate);
      if (parsed && typeof parsed === "object") return parsed as ProductResult;
    } catch {
      return { latex: candidate };
    }
  }
  if (typeof candidate === "object") return candidate as ProductResult;
  return null;
}

export default function ProductPage() {
  const [email, setEmail] = useState("");
  const [title, setTitle] = useState("");
  const [author, setAuthor] = useState("");
  const [style, setStyle] = useState<string>("arxiv");
  const [notes, setNotes] = useState("");
  const [status, setStatus] = useState<"idle" | "running" | "ok" | "error">("idle");
  const [error, setError] = useState<string>("");
  const [result, setResult] = useState<ProductResult | null>(null);
  const [copied, setCopied] = useState(false);

  const notesCount = notes.trim().length;
  const canSubmit = email.includes("@") && notesCount >= 40 && status !== "running";

  const brief = useMemo(() => {
    const lines = [
      `Style preset: ${style}`,
      title ? `Title: ${title}` : "Title: (infer from notes)",
      author ? `Author: ${author}` : "Author: (omit)",
      "",
      "--- Rough notes (English + inline math) ---",
      notes
    ];
    return lines.join("\n");
  }, [style, title, author, notes]);

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!canSubmit) return;
    setStatus("running");
    setError("");
    setResult(null);
    setCopied(false);
    try {
      const response = await fetch("/api/product/run", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ email, brief })
      });
      const payload = (await response.json().catch(() => null)) as RunResponse | null;
      if (!response.ok || !payload?.ok) {
        setStatus("error");
        setError(payload?.error || "We could not generate the article. Please try again.");
        return;
      }
      const parsed = parseResult(payload);
      setResult(parsed);
      setStatus("ok");
    } catch (err) {
      setStatus("error");
      setError(err instanceof Error ? err.message : "Network error. Please try again.");
    }
  }

  async function copyLatex() {
    if (!result?.latex) return;
    try {
      await navigator.clipboard.writeText(result.latex);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      setCopied(false);
    }
  }

  return (
    <main className="lx-root">
      <header className="lx-nav">
        <Link href="/" className="lx-brand">
          <span className="lx-brand-mark">L</span>
          <span className="lx-brand-name">Latexflow</span>
        </Link>
        <nav className="lx-nav-links">
          <Link href="/" className="lx-nav-link">Home</Link>
          <Link href="/signup" className="lx-nav-link">Sign in</Link>
        </nav>
      </header>

      <section className="lx-app">
        <div className="lx-app-head">
          <h1 className="lx-app-title">Notes &rarr; Article</h1>
          <p className="lx-app-lede">
            Paste your rough English + inline math. We&rsquo;ll return a compile-ready{" "}
            <code className="lx-code">.tex</code> source and a list of every notation fix we made.
          </p>
        </div>

        <form className="lx-app-grid" onSubmit={onSubmit}>
          <section className="lx-panel lx-panel-input">
            <div className="lx-panel-head">
              <h2 className="lx-panel-title">Your draft</h2>
              <span className={`lx-counter ${notesCount >= 40 ? "lx-counter-ok" : ""}`}>
                {notesCount} chars{notesCount < 40 ? " (need 40+)" : ""}
              </span>
            </div>

            <div className="lx-row">
              <label className="lx-field">
                <span className="lx-field-label">Work email</span>
                <input
                  className="lx-input"
                  type="email"
                  required
                  autoComplete="email"
                  placeholder="you@university.edu"
                  value={email}
                  onChange={(event) => setEmail(event.target.value)}
                />
              </label>
            </div>

            <div className="lx-row lx-row-2">
              <label className="lx-field">
                <span className="lx-field-label">Article title (optional)</span>
                <input
                  className="lx-input"
                  type="text"
                  placeholder="On the heat equation on a bounded interval"
                  value={title}
                  onChange={(event) => setTitle(event.target.value)}
                />
              </label>
              <label className="lx-field">
                <span className="lx-field-label">Author line (optional)</span>
                <input
                  className="lx-input"
                  type="text"
                  placeholder="A. Researcher, Dept. of Physics"
                  value={author}
                  onChange={(event) => setAuthor(event.target.value)}
                />
              </label>
            </div>

            <fieldset className="lx-fieldset">
              <legend className="lx-field-label">Style preset</legend>
              <div className="lx-style-row">
                {STYLES.map((option) => (
                  <label
                    key={option.id}
                    className={`lx-style ${style === option.id ? "lx-style-active" : ""}`}
                  >
                    <input
                      type="radio"
                      name="style"
                      value={option.id}
                      checked={style === option.id}
                      onChange={() => setStyle(option.id)}
                      className="lx-style-input"
                    />
                    <span className="lx-style-label">{option.label}</span>
                    <span className="lx-style-hint">{option.hint}</span>
                  </label>
                ))}
              </div>
            </fieldset>

            <label className="lx-field">
              <span className="lx-field-label">Rough notes (English + inline math)</span>
              <textarea
                className="lx-textarea"
                required
                rows={16}
                placeholder={`# Section 1 - Heat equation\nWe start from du/dt = alpha * d^2u/dx^2 on [0, L] with u(0,t)=u(L,t)=0.\n\nLet u(x,t) = X(x)T(t). Then T'/(aT) = X''/X = -lambda.\n\nSolve X'' + lambda X = 0, BCs give X_n = sin(n pi x / L), lambda_n = (n pi / L)^2.\n\nFull solution: u(x,t) = sum_n b_n sin(n pi x / L) exp(-alpha lambda_n t).`}
                value={notes}
                onChange={(event) => setNotes(event.target.value)}
              />
            </label>

            <div className="lx-actions">
              <button
                type="submit"
                className="lx-btn lx-btn-primary"
                disabled={!canSubmit}
              >
                {status === "running" ? "Generating article..." : "Generate LaTeX article"}
              </button>
              <span className="lx-actions-hint">
                Free plan: 25 generations included. No card required.
              </span>
            </div>

            {status === "error" && error ? (
              <p className="lx-form-msg lx-form-msg-error">{error}</p>
            ) : null}
          </section>

          <aside className="lx-panel lx-panel-output">
            <div className="lx-panel-head">
              <h2 className="lx-panel-title">LaTeX article</h2>
              {result?.latex ? (
                <button type="button" className="lx-btn lx-btn-ghost lx-btn-sm" onClick={copyLatex}>
                  {copied ? "Copied" : "Copy .tex"}
                </button>
              ) : null}
            </div>

            {status === "idle" && !result ? (
              <div className="lx-empty">
                <div className="lx-empty-mark">{"{ }"}</div>
                <p className="lx-empty-title">No article yet</p>
                <p className="lx-empty-body">
                  Paste your notes on the left and click <strong>Generate LaTeX article</strong>. The
                  compile-ready source and a corrections list will appear here.
                </p>
              </div>
            ) : null}

            {status === "running" ? (
              <div className="lx-empty">
                <div className="lx-spinner" aria-hidden="true" />
                <p className="lx-empty-title">Converting your draft</p>
                <p className="lx-empty-body">Structuring sections, normalizing notation, balancing equations.</p>
              </div>
            ) : null}

            {result ? (
              <div className="lx-result">
                {result.title || result.style ? (
                  <div className="lx-result-meta">
                    {result.title ? <span className="lx-result-meta-item"><strong>Title:</strong> {result.title}</span> : null}
                    {result.style ? <span className="lx-result-meta-item"><strong>Style:</strong> {result.style}</span> : null}
                  </div>
                ) : null}

                {result.summary ? (
                  <p className="lx-result-summary">{result.summary}</p>
                ) : null}

                {result.sections && result.sections.length ? (
                  <div className="lx-result-block">
                    <h3 className="lx-result-block-title">Sections</h3>
                    <ol className="lx-result-sections">
                      {result.sections.map((section, index) => (
                        <li key={index}>{section}</li>
                      ))}
                    </ol>
                  </div>
                ) : null}

                {result.latex ? (
                  <div className="lx-result-block">
                    <h3 className="lx-result-block-title">.tex source</h3>
                    <pre className="lx-tex-output"><code>{result.latex}</code></pre>
                    <p className="lx-result-hint">
                      Copy and paste into Overleaf or your local TeX install to compile.
                    </p>
                  </div>
                ) : null}

                {result.corrections && result.corrections.length ? (
                  <div className="lx-result-block">
                    <h3 className="lx-result-block-title">Corrections ({result.corrections.length})</h3>
                    <ul className="lx-corrections">
                      {result.corrections.map((correction, index) => (
                        <li key={index} className="lx-correction">
                          <div className="lx-correction-diff">
                            <code className="lx-code lx-code-before">{correction.before || "—"}</code>
                            <span className="lx-correction-arrow">&rarr;</span>
                            <code className="lx-code lx-code-after">{correction.after || "—"}</code>
                          </div>
                          {correction.reason ? (
                            <p className="lx-correction-reason">{correction.reason}</p>
                          ) : null}
                        </li>
                      ))}
                    </ul>
                  </div>
                ) : null}

                {!result.latex && !result.corrections?.length && !result.summary ? (
                  <pre className="lx-tex-output"><code>{JSON.stringify(result, null, 2)}</code></pre>
                ) : null}
              </div>
            ) : null}
          </aside>
        </form>
      </section>

      <footer className="lx-foot">
        <div className="lx-foot-left">
          <span className="lx-brand-mark lx-brand-mark-sm">L</span>
          <span className="lx-foot-name">Latexflow</span>
        </div>
        <nav className="lx-foot-links">
          <Link href="/" className="lx-foot-link">Home</Link>
          <Link href="/signup" className="lx-foot-link">Sign in</Link>
        </nav>
      </footer>
    </main>
  );
}
