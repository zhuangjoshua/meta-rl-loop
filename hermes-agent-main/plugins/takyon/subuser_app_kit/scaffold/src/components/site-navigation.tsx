import { Link, useNavigate } from "react-router-dom";
import { brandMarkDataUri, businessDisplayName } from "../lib/branding";
import type { ViewerAccessResult } from "../lib/hooks";
import { useProductAuth } from "../lib/product-auth";

type HeaderAccess = Pick<ViewerAccessResult, "authenticated" | "loading">;

export function PublicSiteHeader({ access }: { access: HeaderAccess }) {
  const auth = useProductAuth();
  const productName = businessDisplayName();

  return (
    <header className="sticky top-0 z-40 border-b border-border bg-background/95 shadow-sm backdrop-blur">
      <div className="flex w-full flex-wrap items-center justify-between gap-4 px-6 py-4 sm:px-8 lg:px-12">
        <Link to={access.authenticated ? "/app" : "/"} className="flex items-center gap-3">
          <img
            src={brandMarkDataUri()}
            alt={`${productName} logo`}
            className="h-10 w-10 rounded-lg"
            width={40}
            height={40}
          />
          <span className="font-heading text-lg font-semibold text-foreground">{productName}</span>
        </Link>

        {access.loading ? (
          <div className="h-10 w-72 animate-pulse rounded bg-muted" aria-label="Loading navigation" />
        ) : access.authenticated ? (
          <nav aria-label="Account navigation" className="flex flex-wrap items-center gap-2">
            <Link className="rounded px-3 py-2 text-sm font-medium text-foreground hover:bg-muted" to="/app">
              App
            </Link>
            <Link className="rounded px-3 py-2 text-sm font-medium text-foreground hover:bg-muted" to="/app/profile">
              Account
            </Link>
            <button
              type="button"
              onClick={() => void auth.logout()}
              disabled={auth.busy}
              className="rounded border border-border bg-card px-4 py-2 text-sm font-medium text-foreground hover:bg-muted disabled:opacity-50"
            >
              Sign out
            </button>
          </nav>
        ) : (
          <div className="flex flex-wrap items-center gap-2">
            <nav aria-label="Public navigation" className="flex flex-wrap items-center gap-1">
              <Link className="rounded px-3 py-2 text-sm font-medium text-foreground hover:bg-muted" to="/">
                Home
              </Link>
              <Link className="rounded px-3 py-2 text-sm font-medium text-foreground hover:bg-muted" to="/pricing">
                Pricing
              </Link>
              <Link className="rounded px-3 py-2 text-sm font-medium text-foreground hover:bg-muted" to="/faq">
                FAQ
              </Link>
              <Link className="rounded px-3 py-2 text-sm font-medium text-foreground hover:bg-muted" to="/privacy">
                Privacy
              </Link>
              <Link className="rounded px-3 py-2 text-sm font-medium text-foreground hover:bg-muted" to="/terms">
                Terms
              </Link>
            </nav>
            <button
              type="button"
              onClick={() => void auth.signInWithGoogle()}
              disabled={!auth.available || !auth.configured || auth.busy}
              className="rounded border border-border bg-card px-4 py-2 text-sm font-medium text-foreground hover:bg-muted disabled:opacity-50"
            >
              Log in
            </button>
            <button
              type="button"
              onClick={() => void auth.signUpWithGoogle()}
              disabled={!auth.available || !auth.configured || auth.busy}
              className="rounded bg-primary px-4 py-2 text-sm font-medium text-primary-foreground hover:opacity-90 disabled:opacity-50"
            >
              Sign up
            </button>
          </div>
        )}
      </div>
    </header>
  );
}

export function BackButton({ fallback = "/" }: { fallback?: string }) {
  const navigate = useNavigate();

  return (
    <button
      type="button"
      onClick={() => {
        if (window.history.length > 1) navigate(-1);
        else navigate(fallback);
      }}
      className="inline-flex w-fit items-center gap-2 rounded px-3 py-2 text-sm font-medium text-foreground hover:bg-muted"
      aria-label="Go back"
    >
      <span aria-hidden="true">←</span>
      Back
    </button>
  );
}
