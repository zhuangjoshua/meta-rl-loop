import { Link } from "react-router-dom";
import { Button } from "../components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "../components/ui/card";
import { brandMarkDataUri, businessDisplayName } from "../lib/branding";
import { resolveViewerCta, useViewerAccess } from "../lib/hooks";
import { useProductAuth } from "../lib/product-auth";

function accessSummaryLabel(state: string): string {
  switch (state) {
    case "ready":
      return "Active account and usable app session";
    case "subscription_required":
      return "Signed in, but paid access is still required";
    case "past_due":
      return "Signed in, but billing needs attention";
    case "account_unavailable":
      return "Signed in, waiting on account details";
    default:
      return "No active customer session yet";
  }
}

export function LandingScreen() {
  const access = useViewerAccess();
  const auth = useProductAuth();
  const productName = businessDisplayName();
  const cta = resolveViewerCta(access);

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
              <span className="text-sm text-muted-foreground">
                Shared Supabase login bridge + app runtime shell
              </span>
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

        <section className="grid gap-6 lg:grid-cols-[minmax(0,1.2fr)_minmax(20rem,0.8fr)]">
          <div className="flex flex-col gap-6">
            <div className="flex flex-col gap-4">
              <p className="text-sm font-medium uppercase tracking-[0.2em] text-muted-foreground">
                Customer access
              </p>
              <h1 className="font-heading text-4xl font-semibold tracking-tight text-foreground sm:text-5xl">
                {productName} is ready for real Google sign-in, not a mock auth shell.
              </h1>
              <p className="max-w-3xl text-base leading-7 text-muted-foreground sm:text-lg">
                This starter now bridges Supabase Auth into the canonical Takyon app session rail,
                so product apps can use Google OAuth and then rely on the same cookie-backed account,
                entitlement, and checkout reads as the rest of the runtime.
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
                  disabled={!auth.available || !auth.configured || auth.busy}
                >
                  {auth.busy ? "Finishing sign-in…" : "Continue with Google"}
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
              <span>{accessSummaryLabel(access.state)}</span>
              {!auth.configured ? (
                <span>
                  Supabase public config is still missing from the runtime environment, so the Google
                  button stays blocked until `SUPABASE_URL` and a publishable key are present.
                </span>
              ) : null}
              {auth.error ? <span className="text-destructive">{auth.error}</span> : null}
            </div>
          </div>

          <Card className="h-fit">
            <CardHeader>
              <CardTitle>Runtime access snapshot</CardTitle>
              <CardDescription>
                The landing page reads the same session and account rails the gated app uses.
              </CardDescription>
            </CardHeader>
            <CardContent className="grid gap-4">
              <div className="rounded border border-border bg-background p-4">
                <p className="text-sm font-medium text-foreground">Session state</p>
                <p className="mt-1 text-sm text-muted-foreground">
                  {access.loading
                    ? "Checking session…"
                    : access.authenticated
                      ? "Authenticated"
                      : "Anonymous"}
                </p>
              </div>
              <div className="rounded border border-border bg-background p-4">
                <p className="text-sm font-medium text-foreground">Membership state</p>
                <p className="mt-1 text-sm text-muted-foreground">
                  {access.loading ? "Checking billing…" : access.subscriptionState || "none"}
                </p>
              </div>
              <div className="rounded border border-border bg-background p-4">
                <p className="text-sm font-medium text-foreground">Primary CTA</p>
                <p className="mt-1 text-sm text-muted-foreground">{cta.primaryLabel}</p>
              </div>
            </CardContent>
          </Card>
        </section>
      </div>
    </main>
  );
}
