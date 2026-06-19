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

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function dollars(value: unknown): string | null {
  return typeof value === "number" && Number.isFinite(value) ? `$${value.toFixed(2)}` : null;
}

/** "$X of $Y used this week" line from the account's weekly usage allowance. Returns null when no
 *  allowance is present so the line simply hides instead of faking a quota. */
function weeklyAllocationLine(access: ReturnType<typeof useViewerAccess>): string | null {
  const allocation = isRecord(access.account?.usage_allocation)
    ? (access.account?.usage_allocation as Record<string, unknown>)
    : null;
  if (!allocation) return null;
  const used = dollars(allocation.committed_usd);
  if (used === null) return null;
  const limit = dollars(allocation.hard_limit_usd);
  return limit ? `${used} of ${limit} used this week` : `${used} used this week`;
}

export function AppHomeScreen() {
  const access = useViewerAccess();
  const auth = useProductAuth();
  const cta = resolveViewerCta(access);
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
          <CardTitle>You're signed in.</CardTitle>
          <CardDescription>Welcome back to {productName}.</CardDescription>
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
    </section>
  );
}
