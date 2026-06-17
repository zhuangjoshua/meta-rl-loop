import { Link } from "react-router-dom";
import { Button } from "../components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "../components/ui/card";
import { Skeleton } from "../components/ui/skeleton";
import { useViewerAccess } from "../lib/hooks";
import { useProductAuth } from "../lib/product-auth";

function displayField(value: unknown, fallback: string): string {
  const text = String(value || "").trim();
  return text || fallback;
}

export function ProfileScreen() {
  const access = useViewerAccess();
  const auth = useProductAuth();

  if (access.loading) {
    return (
      <div className="flex min-h-[50vh] flex-col gap-4" aria-busy="true">
        <Skeleton className="h-8 w-1/4" />
        <Skeleton className="h-24 w-full" />
        <Skeleton className="h-24 w-full" />
      </div>
    );
  }

  if (!access.authenticated) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>Account unavailable</CardTitle>
          <CardDescription>
            A product account page only becomes meaningful after the runtime can read your app
            session. Sign in first, then this page will show the real user and entitlement state.
          </CardDescription>
        </CardHeader>
        <CardContent className="flex flex-wrap gap-3">
          <Button
            size="lg"
            onClick={() => void auth.signInWithGoogle()}
            disabled={!auth.available || !auth.configured || auth.busy}
          >
            {auth.busy ? "Finishing sign-in…" : "Continue with Google"}
          </Button>
          <Link
            to="/"
            className="inline-flex h-12 items-center justify-center rounded border border-border bg-card px-6 text-base font-medium text-foreground transition-colors hover:bg-muted"
          >
            Back to home
          </Link>
        </CardContent>
      </Card>
    );
  }

  return (
    <section className="grid gap-6 xl:grid-cols-[minmax(0,1.1fr)_minmax(18rem,0.9fr)]">
      <Card>
        <CardHeader>
          <CardTitle>Account overview</CardTitle>
          <CardDescription>
            These fields come from the canonical session/account rails, not a browser-only auth
            cache.
          </CardDescription>
        </CardHeader>
        <CardContent className="grid gap-4">
          <div className="rounded border border-border bg-background p-4">
            <p className="text-sm font-medium text-foreground">Email</p>
            <p className="mt-1 text-sm text-muted-foreground">
              {displayField(access.user?.email, "Unavailable")}
            </p>
          </div>
          <div className="rounded border border-border bg-background p-4">
            <p className="text-sm font-medium text-foreground">App user ID</p>
            <p className="mt-1 break-all text-sm text-muted-foreground">
              {displayField(access.user?.id, "Unavailable")}
            </p>
          </div>
          <div className="rounded border border-border bg-background p-4">
            <p className="text-sm font-medium text-foreground">Subscription state</p>
            <p className="mt-1 text-sm text-muted-foreground">
              {displayField(access.subscriptionState, "none")}
            </p>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Session controls</CardTitle>
        </CardHeader>
        <CardContent className="grid gap-3">
          <Button onClick={() => void auth.logout()} disabled={auth.busy}>
            {auth.busy ? "Signing out…" : "Sign out"}
          </Button>
          <Link
            to="/app"
            className="inline-flex h-10 items-center justify-center rounded border border-border bg-card px-4 text-sm font-medium text-foreground transition-colors hover:bg-muted"
          >
            Back to app
          </Link>
        </CardContent>
      </Card>
    </section>
  );
}
