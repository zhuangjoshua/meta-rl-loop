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
import { businessDisplayName, formatUsageAllowance } from "../lib/branding";
import { useViewerAccess } from "../lib/hooks";
import { useProductAuth } from "../lib/product-auth";

function accountEmail(access: ReturnType<typeof useViewerAccess>): string {
  return String(access.user?.email || access.session?.email || access.account?.email || "").trim();
}

/** "$X of $Y used this week" line from the account's weekly usage allowance. Returns null when no
 *  allowance is present so the line simply hides instead of faking a quota. Delegates to the shared
 *  formatter so account-usage display stays a single source of truth. */
function weeklyAllocationLine(access: ReturnType<typeof useViewerAccess>): string | null {
  return formatUsageAllowance(access.account);
}

export function AppHomeScreen() {
  const access = useViewerAccess();
  const auth = useProductAuth();
  const productName = businessDisplayName();
  const weeklyAllocation = weeklyAllocationLine(access);

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
    const signInBlocked = !auth.available || !auth.configured;
    return (
      <section className="grid gap-6 lg:grid-cols-[minmax(0,1.1fr)_minmax(18rem,0.9fr)]">
        <Card>
          <CardHeader>
            <CardTitle>Sign in to {productName}</CardTitle>
            <CardDescription>Sign in to continue.</CardDescription>
          </CardHeader>
          <CardContent className="flex flex-col gap-4">
            <Button
              size="lg"
              onClick={() => void auth.signInWithGoogle()}
              disabled={signInBlocked || auth.busy}
            >
              {auth.busy ? "Signing you in…" : "Continue with Google"}
            </Button>
            {signInBlocked ? (
              <p className="text-sm text-muted-foreground">
                Sign-in is temporarily unavailable. Please try again shortly.
              </p>
            ) : null}
          </CardContent>
        </Card>
      </section>
    );
  }

  return (
    <section className="grid gap-6">
      <Card>
        <CardHeader>
          <div className="flex flex-wrap items-center gap-3">
            <Badge>{access.subscriptionState || "signed_in"}</Badge>
            <span className="text-sm text-muted-foreground">{productName}</span>
          </div>
          <CardTitle>Your workspace is ready.</CardTitle>
          <CardDescription>Continue your work in {productName}.</CardDescription>
        </CardHeader>
        <CardContent className="grid gap-4">
          <div className="rounded border border-border bg-background p-4">
            <p className="text-sm font-medium text-foreground">Signed-in email</p>
            <p className="mt-1 text-sm text-muted-foreground">
              {accountEmail(access) || "No email surfaced yet"}
            </p>
          </div>
          {weeklyAllocation ? (
            <div className="rounded border border-border bg-background p-4">
              <p className="text-sm font-medium text-foreground">Weekly AI allowance</p>
              <p className="mt-1 text-sm text-muted-foreground">{weeklyAllocation}</p>
            </div>
          ) : null}
          <div className="flex flex-wrap gap-3">
            <Link
              to="/app/profile"
              className="inline-flex h-11 items-center justify-center rounded border border-border bg-card px-5 text-sm font-medium text-foreground transition-colors hover:bg-muted"
            >
              Account settings
            </Link>
          </div>
        </CardContent>
      </Card>
    </section>
  );
}
