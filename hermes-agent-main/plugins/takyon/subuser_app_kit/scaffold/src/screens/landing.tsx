import { Link } from "react-router-dom";
import { Button } from "../components/ui/button";
import {
  brandHeroEyebrow,
  brandHeroHeadline,
  brandHeroSubhead,
  brandMarkDataUri,
  businessDisplayName,
} from "../lib/branding";
import { resolveViewerCta, useViewerAccess } from "../lib/hooks";
import { useProductAuth } from "../lib/product-auth";

export function LandingScreen() {
  const access = useViewerAccess();
  const auth = useProductAuth();
  const productName = businessDisplayName();
  const cta = resolveViewerCta(access);
  // Idea-branded hero copy from the bootstrap brief (injected via surface-context) so the very first
  // published landing is already on-message; falls back to the generic welcome when unset.
  const heroEyebrow = brandHeroEyebrow() || "Get started";
  const heroHeadline = brandHeroHeadline() || `Welcome to ${productName}.`;
  const heroSubhead =
    brandHeroSubhead() || "Sign in with Google to access your account and get started.";

  return (
    <main className="min-h-screen bg-background">
      <div className="mx-auto flex w-full max-w-6xl flex-col gap-10 px-6 py-12 sm:py-16">
        <header className="flex flex-wrap items-center justify-between gap-4">
          <div className="flex items-center gap-3">
            <img
              src={brandMarkDataUri()}
              alt={`${productName} logo`}
              className="h-10 w-10 rounded-lg"
              width={40}
              height={40}
            />
            <div className="flex flex-col">
              <span className="font-heading text-lg font-semibold text-foreground">
                {productName}
              </span>
              <span className="text-sm text-muted-foreground">Welcome</span>
            </div>
          </div>
          <nav className="flex flex-wrap gap-2">
            {[
              { to: "/faq", label: "FAQ" },
              { to: "/privacy", label: "Privacy" },
              { to: "/terms", label: "Terms" },
              { to: "/app", label: "Open app" },
            ].map((item) => (
              <Link
                key={item.to}
                to={item.to}
                className="rounded border border-border bg-card px-3 py-2 text-sm font-medium text-foreground transition-colors hover:bg-muted"
              >
                {item.label}
              </Link>
            ))}
          </nav>
        </header>

        <section className="grid gap-6">
          <div className="flex flex-col gap-6">
            <div className="flex flex-col gap-4">
              <p className="text-sm font-medium uppercase tracking-[0.2em] text-muted-foreground">
                {heroEyebrow}
              </p>
              <h1 className="font-heading text-4xl font-semibold tracking-tight text-foreground sm:text-5xl">
                {heroHeadline}
              </h1>
              <p className="max-w-3xl text-base leading-7 text-muted-foreground sm:text-lg">
                {heroSubhead}
              </p>
            </div>

            <div className="flex flex-wrap gap-3">
              {access.authenticated ? (
                <Link
                  to={cta.primaryHref}
                  className="inline-flex h-12 items-center justify-center rounded bg-primary px-6 text-base font-medium text-primary-foreground transition-opacity hover:opacity-90"
                >
                  {cta.primaryLabel}
                </Link>
              ) : (
                <Button
                  size="lg"
                  onClick={() => void auth.signInWithGoogle()}
                  disabled={!auth.available || !auth.configured || auth.busy || access.authenticated}
                >
                  {auth.busy ? "Signing you in…" : "Continue with Google"}
                </Button>
              )}
              <Link
                to={access.authenticated ? cta.secondaryHref : "/faq"}
                className="inline-flex h-12 items-center justify-center rounded border border-border bg-card px-6 text-base font-medium text-foreground transition-colors hover:bg-muted"
              >
                {access.authenticated ? cta.secondaryLabel : "Read the FAQ"}
              </Link>
            </div>

            <div className="flex flex-col gap-2 text-sm text-muted-foreground">
              {!auth.available || !auth.configured ? (
                <span>Sign-in is temporarily unavailable. Please try again shortly.</span>
              ) : null}
              {auth.error ? <span className="text-destructive">{auth.error}</span> : null}
            </div>
          </div>
        </section>
      </div>
    </main>
  );
}
