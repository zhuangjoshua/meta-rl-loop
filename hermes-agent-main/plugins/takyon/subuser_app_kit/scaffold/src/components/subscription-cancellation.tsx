import { useState } from "react";
import { Button } from "./ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "./ui/card";
import { hasActiveStripeSubscription, useViewerAccess } from "../lib/hooks";
import { client } from "../lib/takyon";

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error || "Cancellation failed");
}

/** Canonical self-service billing control.
 *
 * This component is rendered by starter-owned main.tsx and force-refreshed on every product
 * materialization. Product workers may restyle the surrounding account screen, but they cannot
 * replace immediate self-service cancellation with support-mediated billing.
 */
export function SubscriptionCancellation() {
  const access = useViewerAccess();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (!hasActiveStripeSubscription(access.account)) return null;

  const cancelNow = async () => {
    const confirmed = window.confirm(
      "Cancel your subscription now? Your access will end immediately. There is no grace period.",
    );
    if (!confirmed) return;
    setBusy(true);
    setError(null);
    try {
      await client.cancelSubscription();
      await access.refresh();
    } catch (cause) {
      setError(errorMessage(cause));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card data-takyon-appkit="subscription-cancellation">
      <CardHeader>
        <CardTitle>Cancel subscription</CardTitle>
        <CardDescription>
          Cancellation ends access immediately. There is no grace period, and you will not be
          charged for another billing period.
        </CardDescription>
      </CardHeader>
      <CardContent className="grid gap-3">
        <Button variant="destructive" onClick={() => void cancelNow()} disabled={busy}>
          {busy ? "Canceling subscription…" : "Cancel subscription now"}
        </Button>
        {error ? (
          <p role="alert" className="text-sm text-destructive">
            We couldn&apos;t cancel your subscription: {error}
          </p>
        ) : null}
      </CardContent>
    </Card>
  );
}
