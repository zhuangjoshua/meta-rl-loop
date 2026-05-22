"use client";

import Link from "next/link";
import { useState, FormEvent } from "react";
import { requestMagicLink, generatedAppCheckoutUrl } from "@/lib/platform-client";

export default function SignupPage() {
  const [email, setEmail] = useState("");
  const [status, setStatus] = useState<"idle" | "loading" | "sent" | "error">("idle");
  const [message, setMessage] = useState<string>("");

  const checkoutUrl = generatedAppCheckoutUrl("starter");

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setStatus("loading");
    setMessage("");
    try {
      await requestMagicLink(email);
      setStatus("sent");
      setMessage("Check your inbox for a sign-in link.");
    } catch (err) {
      setStatus("error");
      setMessage(err instanceof Error ? err.message : "Could not send the sign-in link.");
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
          <Link href="/product" className="lx-nav-link">Editor</Link>
          <Link href="/" className="lx-nav-link">Home</Link>
        </nav>
      </header>

      <section className="lx-auth">
        <div className="lx-auth-card">
          <h1 className="lx-auth-title">Sign in to Latexflow</h1>
          <p className="lx-auth-lede">
            We&rsquo;ll email you a one-time link &mdash; no password to remember.
          </p>

          <form className="lx-auth-form" onSubmit={onSubmit}>
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
                disabled={status === "loading" || status === "sent"}
              />
            </label>
            <button
              className="lx-btn lx-btn-primary lx-btn-block"
              type="submit"
              disabled={status === "loading" || status === "sent"}
            >
              {status === "loading" ? "Sending link..." : status === "sent" ? "Link sent" : "Email me a sign-in link"}
            </button>
            {status !== "idle" && message ? (
              <p className={`lx-form-msg ${status === "error" ? "lx-form-msg-error" : "lx-form-msg-ok"}`}>
                {message}
              </p>
            ) : null}
          </form>

          <div className="lx-auth-divider"><span>or</span></div>

          <Link href="/product" className="lx-btn lx-btn-ghost lx-btn-block">
            Try the editor first
          </Link>
        </div>

        <aside className="lx-auth-side">
          <h2 className="lx-auth-side-title">Choose a plan</h2>
          <p className="lx-auth-side-lede">
            You can switch any time. Both plans use the same editor.
          </p>

          <article className="lx-plan-mini">
            <div className="lx-plan-mini-head">
              <h3 className="lx-plan-mini-name">Free</h3>
              <span className="lx-plan-mini-price">$0</span>
            </div>
            <ul className="lx-plan-mini-list">
              <li>25 article generations included</li>
              <li>arXiv, IEEE, lecture presets</li>
              <li>Corrections list on every output</li>
            </ul>
            <p className="lx-plan-mini-foot">
              Free is selected by default after you sign in.
            </p>
          </article>

          <article className="lx-plan-mini lx-plan-mini-featured">
            <div className="lx-plan-mini-head">
              <h3 className="lx-plan-mini-name">Starter</h3>
              <span className="lx-plan-mini-price">$19<span className="lx-plan-mini-period">/mo</span></span>
            </div>
            <ul className="lx-plan-mini-list">
              <li>500 article generations / month</li>
              <li>Longer multi-section drafts</li>
              <li>Priority handling</li>
            </ul>
            <a className="lx-btn lx-btn-primary lx-btn-block" href={checkoutUrl}>
              Choose Starter
            </a>
          </article>
        </aside>
      </section>

      <footer className="lx-foot">
        <div className="lx-foot-left">
          <span className="lx-brand-mark lx-brand-mark-sm">L</span>
          <span className="lx-foot-name">Latexflow</span>
        </div>
        <nav className="lx-foot-links">
          <Link href="/" className="lx-foot-link">Home</Link>
          <Link href="/product" className="lx-foot-link">Editor</Link>
        </nav>
      </footer>
    </main>
  );
}
