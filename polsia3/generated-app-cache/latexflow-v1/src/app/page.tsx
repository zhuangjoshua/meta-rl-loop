import Link from "next/link";

export default function HomePage() {
  return (
    <main className="lx-root">
      <header className="lx-nav">
        <Link href="/" className="lx-brand">
          <span className="lx-brand-mark">L</span>
          <span className="lx-brand-name">Latexflow</span>
        </Link>
        <nav className="lx-nav-links">
          <Link href="/product" className="lx-nav-link">Editor</Link>
          <a href="#features" className="lx-nav-link">Features</a>
          <a href="#pricing" className="lx-nav-link">Pricing</a>
          <Link href="/signup" className="lx-nav-link">Sign in</Link>
          <Link href="/product" className="lx-btn lx-btn-primary lx-btn-sm">Open editor</Link>
        </nav>
      </header>

      <section className="lx-hero">
        <div className="lx-hero-copy">
          <span className="lx-eyebrow">For STEM PhD researchers</span>
          <h1 className="lx-h1">
            Rough notes in.
            <br />
            <span className="lx-h1-accent">Compile-ready LaTeX out.</span>
          </h1>
          <p className="lx-lede">
            Paste English prose with messy inline math &mdash; ASCII fractions, mixed Greek, half-balanced
            parens, even partly wrong derivations. Latexflow returns a structured <code className="lx-code">.tex</code> source
            you can drop into Overleaf, plus a plain-English list of every notation fix it made.
          </p>
          <div className="lx-cta-row">
            <Link href="/product" className="lx-btn lx-btn-primary">Open the editor</Link>
            <Link href="/signup" className="lx-btn lx-btn-ghost">Create free account</Link>
          </div>
          <ul className="lx-hero-checks">
            <li>Article-level conversion, not just snippets</li>
            <li>Auto-balanced parens, braces, and aligned environments</li>
            <li>arXiv, IEEE, and lecture-notes presets</li>
          </ul>
        </div>

        <div className="lx-preview">
          <div className="lx-preview-tabs">
            <span className="lx-tab lx-tab-active">notes.txt</span>
            <span className="lx-tab">article.tex</span>
            <span className="lx-tab">corrections</span>
          </div>
          <div className="lx-preview-body">
            <div className="lx-preview-col">
              <div className="lx-preview-label">What you paste</div>
              <pre className="lx-pre lx-pre-notes">{`# Heat equation, separation of vars
du/dt = alpha * d^2u/dx^2 on [0,L]
BC: u(0,t)=u(L,t)=0

let u(x,t) = X(x)T(t)
T'/(aT) = X''/X = -lambda
X'' + lambda X = 0
X_n = sin(n pi x / L)
lambda_n = (n pi / L)^2

so u(x,t) = sum_n b_n sin(...) exp(-alpha lambda_n t)`}</pre>
            </div>
            <div className="lx-preview-col">
              <div className="lx-preview-label">What you get</div>
              <pre className="lx-pre lx-pre-tex"><span className="lx-tex-kw">{"\\section"}</span>{"{Heat equation}\nWe seek "}<span className="lx-tex-math">{"$u(x,t)$"}</span>{" with\n"}<span className="lx-tex-kw">{"\\begin{equation}"}</span>{"\n  "}<span className="lx-tex-math">{"\\frac{\\partial u}{\\partial t} = \\alpha\\,\\frac{\\partial^{2} u}{\\partial x^{2}}"}</span>{"\n"}<span className="lx-tex-kw">{"\\end{equation}"}</span>{"\non "}<span className="lx-tex-math">{"$[0,L]$"}</span>{", "}<span className="lx-tex-math">{"$u(0,t)=u(L,t)=0$"}</span>{".\n\nLet "}<span className="lx-tex-math">{"$u(x,t)=X(x)T(t)$"}</span>{". Then\n"}<span className="lx-tex-kw">{"\\begin{align*}"}</span>{"\n  \\frac{T'}{\\alpha T} &= \\frac{X''}{X} = -\\lambda \\\\\n  X_n(x) &= \\sin\\!\\left(\\tfrac{n\\pi x}{L}\\right)\n"}<span className="lx-tex-kw">{"\\end{align*}"}</span></pre>
            </div>
          </div>
          <div className="lx-preview-foot">
            <span className="lx-dot" />
            Static example. Output depends on your actual notes.
          </div>
        </div>
      </section>

      <section id="features" className="lx-section">
        <h2 className="lx-h2">Built for derivation-heavy drafts</h2>
        <p className="lx-section-lede">
          Most LaTeX assistants stop at single equations or handwriting OCR. Latexflow takes a whole rough
          draft and returns a structured article you can compile and keep editing.
        </p>
        <div className="lx-feature-grid">
          <article className="lx-feature">
            <div className="lx-feature-icon">{"\\sum"}</div>
            <h3 className="lx-feature-title">Article-level conversion</h3>
            <p className="lx-feature-body">
              Sections, subsections, aligned equation blocks, and connective prose &mdash; not just a single
              <code className="lx-code"> \frac</code> at a time.
            </p>
          </article>
          <article className="lx-feature">
            <div className="lx-feature-icon">{"\\partial"}</div>
            <h3 className="lx-feature-title">Notation auto-fix</h3>
            <p className="lx-feature-body">
              Greek letters, partial derivatives, balanced braces, sub/superscripts, and operator spacing are
              normalized to standard LaTeX conventions.
            </p>
          </article>
          <article className="lx-feature">
            <div className="lx-feature-icon">{"\\Delta"}</div>
            <h3 className="lx-feature-title">Corrections diff</h3>
            <p className="lx-feature-body">
              Every notation change comes with a one-line reason &mdash; so you can audit what was inferred
              and keep the math you intended.
            </p>
          </article>
          <article className="lx-feature">
            <div className="lx-feature-icon">{"\\tex"}</div>
            <h3 className="lx-feature-title">Preset templates</h3>
            <p className="lx-feature-body">
              Choose arXiv, IEEE, or lecture-notes formatting. The right
              <code className="lx-code"> \documentclass</code> and packages are wired up for you.
            </p>
          </article>
        </div>
      </section>

      <section className="lx-section lx-section-alt">
        <h2 className="lx-h2">How it works</h2>
        <ol className="lx-steps">
          <li className="lx-step">
            <span className="lx-step-num">1</span>
            <h3 className="lx-step-title">Paste your draft</h3>
            <p className="lx-step-body">
              Drop in English notes with rough inline math. Add an optional title, author line, and style
              preset.
            </p>
          </li>
          <li className="lx-step">
            <span className="lx-step-num">2</span>
            <h3 className="lx-step-title">Latexflow rewrites it</h3>
            <p className="lx-step-body">
              An AI pass infers sectioning, converts inline math to proper LaTeX, and balances each equation
              block.
            </p>
          </li>
          <li className="lx-step">
            <span className="lx-step-num">3</span>
            <h3 className="lx-step-title">Review and compile</h3>
            <p className="lx-step-body">
              Copy the <code className="lx-code">.tex</code> source into Overleaf or your local TeX install,
              and scan the corrections list to confirm intent.
            </p>
          </li>
        </ol>
      </section>

      <section id="pricing" className="lx-section">
        <h2 className="lx-h2">Pricing</h2>
        <p className="lx-section-lede">
          Start free. Move to Starter when you have a real deadline.
        </p>
        <div className="lx-pricing-grid">
          <article className="lx-plan">
            <header className="lx-plan-head">
              <h3 className="lx-plan-name">Free</h3>
              <div className="lx-plan-price"><span className="lx-plan-amount">$0</span><span className="lx-plan-period">/forever</span></div>
              <p className="lx-plan-tag">Try the full workflow on small drafts.</p>
            </header>
            <ul className="lx-plan-list">
              <li>25 article generations included</li>
              <li>arXiv, IEEE, and lecture presets</li>
              <li>Corrections list on every output</li>
              <li>Email magic-link sign in</li>
            </ul>
            <Link href="/signup" className="lx-btn lx-btn-ghost lx-btn-block">Create free account</Link>
          </article>
          <article className="lx-plan lx-plan-featured">
            <header className="lx-plan-head">
              <span className="lx-plan-badge">Most useful</span>
              <h3 className="lx-plan-name">Starter</h3>
              <div className="lx-plan-price"><span className="lx-plan-amount">$19</span><span className="lx-plan-period">/month</span></div>
              <p className="lx-plan-tag">For active drafting toward a real deadline.</p>
            </header>
            <ul className="lx-plan-list">
              <li>500 article generations / month</li>
              <li>Longer drafts &mdash; full multi-section papers</li>
              <li>Priority handling on the conversion queue</li>
              <li>Everything in Free</li>
            </ul>
            <Link href="/signup" className="lx-btn lx-btn-primary lx-btn-block">Choose Starter</Link>
          </article>
        </div>
      </section>

      <section className="lx-section lx-faq">
        <h2 className="lx-h2">Common questions</h2>
        <div className="lx-faq-list">
          <details className="lx-faq-item">
            <summary>Do you compile the PDF for me?</summary>
            <p>
              Latexflow returns the LaTeX source and a corrections list. You compile the PDF in Overleaf or
              your local TeX install &mdash; that keeps the final document under your control and inside your
              normal review workflow.
            </p>
          </details>
          <details className="lx-faq-item">
            <summary>How accurate are the notation fixes?</summary>
            <p>
              Every non-trivial change appears in the corrections list with a short reason, so you can audit
              what was inferred and reject anything that misread your intent.
            </p>
          </details>
          <details className="lx-faq-item">
            <summary>What inputs work best?</summary>
            <p>
              Plain English with inline math in whatever shorthand you already use &mdash; ASCII fractions,
              mixed Greek, partial derivative shorthand. Section headers as plain lines (e.g. <code className="lx-code">## Proof</code>)
              help the converter pick up structure.
            </p>
          </details>
          <details className="lx-faq-item">
            <summary>Is my draft kept private?</summary>
            <p>
              Drafts are processed only to generate your LaTeX article and corrections list. Nothing is
              published or shared on your behalf.
            </p>
          </details>
        </div>
      </section>

      <section className="lx-cta-band">
        <div className="lx-cta-band-inner">
          <h2 className="lx-h2 lx-cta-band-title">Stop hand-typing every <code className="lx-code">\frac</code>.</h2>
          <p className="lx-cta-band-lede">
            Open the editor, paste a section of your current draft, and see a compile-ready version in seconds.
          </p>
          <div className="lx-cta-row">
            <Link href="/product" className="lx-btn lx-btn-primary">Open the editor</Link>
            <Link href="/signup" className="lx-btn lx-btn-ghost">Create free account</Link>
          </div>
        </div>
      </section>

      <footer className="lx-foot">
        <div className="lx-foot-left">
          <span className="lx-brand-mark lx-brand-mark-sm">L</span>
          <span className="lx-foot-name">Latexflow</span>
        </div>
        <nav className="lx-foot-links">
          <Link href="/product" className="lx-foot-link">Editor</Link>
          <Link href="/signup" className="lx-foot-link">Sign in</Link>
          <a href="#pricing" className="lx-foot-link">Pricing</a>
          <a href="#features" className="lx-foot-link">Features</a>
        </nav>
      </footer>
    </main>
  );
}
