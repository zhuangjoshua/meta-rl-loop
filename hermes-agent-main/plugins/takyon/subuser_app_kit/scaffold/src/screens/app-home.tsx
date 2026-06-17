import { Link } from "react-router-dom";
import { Badge } from "../components/ui/badge";
import { Button } from "../components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "../components/ui/card";
import { Skeleton } from "../components/ui/skeleton";
import { businessDisplayName } from "../lib/branding";
import { resolveViewerCta, useViewerAccess } from "../lib/hooks";
import { useProductAuth } from "../lib/product-auth";

function accountEmail(access: ReturnType<typeof useViewerAccess>): string {
  return String(access.user?.email || access.session?.email || access.account?.email || "").trim();
}

export function AppHomeScreen() {
  const access = useViewerAccess();
  const auth = useProductAuth();
  const cta = resolveViewerCta(access);
  const productName = businessDisplayName();

  if (access.loading) {
    return (
      <div className="flex min-h-[50vh] flex-col gap-4" aria-busy="true">
        <Skeleton className="h-8 w-1/3" />
        <Skeleton className="h-32 w-full" />
        <Skeleton className="h-24 w-full" />
      </div>
    );
  }

  if (!access.authenticated) {
    return (
      <section className="grid gap-6 lg:grid-cols-[minmax(0,1.1fr)_minmax(18rem,0.9fr)]">
        <Card>
          <CardHeader>
            <CardTitle>Sign in to enter the app</CardTitle>
            <CardDescription>
              The private app shell reads the shared Takyon session cookie. Use Google OAuth to mint
              that session through Supabase Auth.
            </CardDescription>
          </CardHeader>
          <CardContent className="flex flex-col gap-4">
            <Button
              size="lg"
              onClick={() => void auth.signInWithGoogle()}
              disabled={!auth.available || !auth.configured || auth.busy}
            >
              {auth.busy ? "Finishing sign-in…" : "Continue with Google"}
            </Button>
            <p className="text-sm text-muted-foreground">
              If this app should allow access but the button is disabled, the public Supabase config
              has not been materialized into `_takyon/surface-context.js` yet.
            </p>
          </CardContent>
        </Card>
        <Card>
          <CardHeader>
            <CardTitle>Why this route exists</CardTitle>
          </CardHeader>
          <CardContent className="grid gap-3 text-sm text-muted-foreground">
            <p>The scaffold keeps a real `/app` surface instead of a blank placeholder shell.</p>
            <p>
              Once authenticated, the same route can load account state, usage-led features, records,
              checkout, and any future runtime rails without changing the auth boundary.
            </p>
          </CardContent>
        </Card>
      </section>
    );
  }

  return (
    <section className="grid gap-6 xl:grid-cols-[minmax(0,1.2fr)_minmax(18rem,0.8fr)]">
      <Card>
        <CardHeader>
          <div className="flex flex-wrap items-center gap-3">
            <Badge>{access.subscriptionState || "signed_in"}</Badge>
            <span className="text-sm text-muted-foreground">{productName}</span>
          </div>
          <CardTitle>App session is active</CardTitle>
          <CardDescription>
            This product session was minted by the Takyon runtime after Supabase verified the browser
            login. The app can now rely on the normal account and entitlement rails.
          </CardDescription>
        </CardHeader>
        <CardContent className="grid gap-4">
          <div className="rounded border border-border bg-background p-4">
            <p className="text-sm font-medium text-foreground">Signed-in email</p>
            <p className="mt-1 text-sm text-muted-foreground">
              {accountEmail(access) || "No email surfaced yet"}
            </p>
          </div>
          <div className="rounded border border-border bg-background p-4">
            <p className="text-sm font-medium text-foreground">Recommended next step</p>
            <p className="mt-1 text-sm text-muted-foreground">
              {cta.primaryLabel} through the canonical `{cta.primaryHref}` route.
            </p>
          </div>
          <div className="flex flex-wrap gap-3">
            <Link
              to={cta.primaryHref}
              className="inline-flex h-11 items-center justify-center rounded bg-primary px-5 text-sm font-medium text-primary-foreground transition-opacity hover:opacity-90"
            >
              {cta.primaryLabel}
            </Link>
            <Link
              to="/app/profile"
              className="inline-flex h-11 items-center justify-center rounded border border-border bg-card px-5 text-sm font-medium text-foreground transition-colors hover:bg-muted"
            >
              Open account
            </Link>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Access state</CardTitle>
        </CardHeader>
        <CardContent className="grid gap-3 text-sm text-muted-foreground">
          <p>Viewer state: {access.state}</p>
          <p>Subscription state: {access.subscriptionState || "none"}</p>
          <p>Entitled: {access.entitled ? "yes" : "no"}</p>
          <p>Authenticated: yes</p>
        </CardContent>
      </Card>
    </section>
  );
}
