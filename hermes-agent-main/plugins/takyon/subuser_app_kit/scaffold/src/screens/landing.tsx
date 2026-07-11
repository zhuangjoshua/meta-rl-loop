import { Navigate } from "react-router-dom";
import { PublicSiteHeader } from "../components/site-navigation";
import { Skeleton } from "../components/ui/skeleton";
import {
  brandHeroEyebrow,
  brandHeroHeadline,
  brandHeroSubhead,
  businessDisplayName,
} from "../lib/branding";
import { useViewerAccess } from "../lib/hooks";
import { useProductAuth } from "../lib/product-auth";
import { StoreSection } from "./store";

function LandingLoading() {
  return (
    <main className="min-h-screen bg-background" aria-busy="true">
      <div className="border-b border-border px-6 py-4">
        <Skeleton className="h-10 w-full max-w-7xl" />
      </div>
      <div className="mx-auto grid w-full max-w-7xl gap-10 px-6 py-16 lg:grid-cols-2">
        <div className="space-y-5">
          <Skeleton className="h-5 w-36" />
          <Skeleton className="h-24 w-full" />
          <Skeleton className="h-16 w-4/5" />
          <Skeleton className="h-12 w-64" />
        </div>
        <Skeleton className="min-h-96 w-full" />
      </div>
    </main>
  );
}

export function LandingScreen() {
  const access = useViewerAccess();
  const auth = useProductAuth();
  const productName = businessDisplayName();
  const heroEyebrow = brandHeroEyebrow() || "A better way to work";
  const heroHeadline = brandHeroHeadline() || `Meet ${productName}.`;
  const heroSubhead =
    brandHeroSubhead() || "Turn your next important task into a clear, repeatable workflow.";

  if (access.loading) return <LandingLoading />;
  if (access.authenticated) return <Navigate to="/app" replace />;

  return (
    <main className="min-h-screen bg-background">
      <PublicSiteHeader access={access} />

      <section className="mx-auto grid w-full max-w-7xl items-center gap-12 px-6 py-16 lg:min-h-[calc(100vh-73px)] lg:grid-cols-[minmax(0,0.9fr)_minmax(32rem,1.1fr)]">
        <div className="flex flex-col gap-6">
          <p className="text-sm font-semibold uppercase tracking-[0.2em] text-primary">{heroEyebrow}</p>
          <h1 className="font-heading text-5xl font-semibold tracking-tight text-foreground sm:text-6xl">
            {heroHeadline}
          </h1>
          <p className="max-w-2xl text-lg leading-8 text-muted-foreground">{heroSubhead}</p>
          <div className="flex flex-wrap gap-3">
            <button
              type="button"
              onClick={() => void auth.signUpWithGoogle()}
              disabled={!auth.available || !auth.configured || auth.busy}
              className="inline-flex h-12 items-center justify-center rounded bg-primary px-6 text-base font-medium text-primary-foreground hover:opacity-90 disabled:opacity-50"
            >
              Sign up
            </button>
            <button
              type="button"
              onClick={() => void auth.signInWithGoogle()}
              disabled={!auth.available || !auth.configured || auth.busy}
              className="inline-flex h-12 items-center justify-center rounded border border-border bg-card px-6 text-base font-medium text-foreground hover:bg-muted disabled:opacity-50"
            >
              Log in
            </button>
          </div>
          {auth.error ? <p className="text-sm text-destructive">{auth.error}</p> : null}
        </div>

        <div className="rounded-2xl border border-border bg-card p-4 shadow-xl" aria-label={`${productName} product preview`}>
          <div className="flex items-center justify-between border-b border-border pb-4">
            <div>
              <p className="text-sm font-semibold text-foreground">Your workspace</p>
              <span className="block text-xs text-muted-foreground">A preview of the product experience</span>
            </div>
            <span className="rounded-full bg-primary/10 px-3 py-1 text-xs font-medium text-primary">Live</span>
          </div>
          <div className="grid gap-4 pt-4 sm:grid-cols-[11rem_minmax(0,1fr)]">
            <div className="space-y-2 rounded-xl bg-muted p-3">
              {["Overview", "Create", "Library", "Account"].map((label, index) => (
                <div key={label} className={`rounded px-3 py-2 text-sm ${index === 0 ? "bg-card font-medium text-foreground shadow-sm" : "text-muted-foreground"}`}>
                  {label}
                </div>
              ))}
            </div>
            <div className="space-y-4">
              <div className="grid gap-3 sm:grid-cols-3">
                {["Recent work", "Saved results", "Next action"].map((label) => (
                  <div key={label} className="rounded-xl border border-border p-3">
                    <p className="text-xs text-muted-foreground">{label}</p>
                    <div className="mt-3 h-8 rounded bg-primary/15" />
                  </div>
                ))}
              </div>
              <div className="rounded-xl border border-border p-4">
                <div className="h-3 w-2/3 rounded bg-muted" />
                <div className="mt-3 h-3 w-full rounded bg-muted" />
                <div className="mt-3 h-24 rounded-lg bg-gradient-to-br from-primary/20 to-accent/20" />
              </div>
            </div>
          </div>
        </div>
      </section>

      <section className="border-y border-border bg-card">
        <div className="mx-auto grid w-full max-w-7xl gap-6 px-6 py-12 md:grid-cols-3">
          <div>
            <p className="text-sm font-semibold text-primary">Outcome-focused</p>
            <h2 className="mt-2 font-heading text-2xl font-semibold text-foreground">See the work, not a promise.</h2>
          </div>
          <div className="rounded-xl border border-border bg-background p-5">
            <p className="font-medium text-foreground">Real product visuals</p>
            <span className="mt-2 block text-sm leading-6 text-muted-foreground">Understand the workflow before creating an account.</span>
          </div>
          <div className="rounded-xl border border-border bg-background p-5">
            <p className="font-medium text-foreground">Evidence over hype</p>
            <span className="mt-2 block text-sm leading-6 text-muted-foreground">Published outcome claims must be backed by verified research or product data.</span>
          </div>
        </div>
      </section>

      <StoreSection />
    </main>
  );
}
