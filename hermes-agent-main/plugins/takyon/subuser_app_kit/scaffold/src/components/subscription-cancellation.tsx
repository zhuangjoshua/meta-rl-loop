import { useState } from "react";
import { Button } from "./ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "./ui/card";
import { hasNonterminalStripeSubscription, useViewerAccess } from "../lib/hooks";
import { client } from "../lib/takyon";

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error || "Cancellation failed");
}

function isObject(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function cancellationPolicy(account: unknown): Record<string, unknown> | null {
  if (!isObject(account) || !isObject(account.subscription_cancellation_policy)) return null;
  return account.subscription_cancellation_policy;
}

function noRefundSuffix(policy: Record<string, unknown> | null): string {
  return policy?.refund_policy === "none" ? " Cancellation does not issue a refund." : "";
}

function preCancellationCopy(account: unknown): { description: string; confirmation: string } {
  const policy = cancellationPolicy(account);
  const refund = noRefundSuffix(policy);
  if (policy?.effective_timing === "immediate") {
    return {
      description: `Cancellation ends access immediately.${refund}`,
      confirmation: `Cancel your subscription now? Your access will end immediately.${refund}`,
    };
  }
  if (policy?.effective_timing === "period_end") {
    return {
      description: `Cancellation takes effect at the end of the current billing period.${refund}`,
      confirmation: `Cancel your subscription? Access will remain available until the end of the current billing period.${refund}`,
    };
  }
  return {
    description: "The server will confirm when cancellation takes effect.",
    confirmation: "Cancel your subscription? The server response will confirm when access ends.",
  };
}

function cancellationResultCopy(outcome: unknown): string {
  if (!isObject(outcome)) return "Cancellation confirmed.";
  const policy = isObject(outcome.subscription_cancellation_policy)
    ? outcome.subscription_cancellation_policy
    : null;
  const refund = policy?.refund_policy === "none" ? " No refund was issued." : "";
  if (outcome.effective_immediately === true && outcome.cancel_at_period_end === false) {
    return `Your access ended immediately.${refund}`;
  }
  if (outcome.cancel_at_period_end === true && outcome.effective_immediately !== true) {
    const rawEnd = String(outcome.current_period_end ?? "").trim();
    const parsedEnd = rawEnd ? new Date(rawEnd) : null;
    const endLabel =
      parsedEnd && !Number.isNaN(parsedEnd.getTime())
        ? new Intl.DateTimeFormat(undefined, { dateStyle: "long" }).format(parsedEnd)
        : "the end of the current billing period";
    return `Your subscription will end on ${endLabel}.${refund}`;
  }
  return "Cancellation confirmed. Refresh your account to see the current access state.";
}

/** Canonical backend-truthful self-service billing control.
 *
 * This component is rendered by starter-owned main.tsx and force-refreshed on every product
 * materialization. Product workers may restyle the surrounding account screen, but they cannot
 * replace immediate self-service cancellation with support-mediated billing.
 */
export function SubscriptionCancellation() {
  const access = useViewerAccess();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [successMessage, setSuccessMessage] = useState<string | null>(null);

  if (successMessage) {
    return (
      <Card data-takyon-appkit="subscription-cancellation-success" role="status">
        <CardHeader>
          <CardTitle>Subscription canceled</CardTitle>
          <CardDescription>{successMessage}</CardDescription>
        </CardHeader>
      </Card>
    );
  }
  if (!hasNonterminalStripeSubscription(access.account)) return null;
  const copy = preCancellationCopy(access.account);

  const cancelNow = async () => {
    const confirmed = window.confirm(copy.confirmation);
    if (!confirmed) return;
    setBusy(true);
    setError(null);
    try {
      const outcome = await client.cancelSubscription();
      // The server response is provider-authoritative terminal truth. Hide the control now; a
      // projection refresh is best-effort and must never turn completed cancellation into an error.
      setSuccessMessage(cancellationResultCopy(outcome));
    } catch (cause) {
      setError(errorMessage(cause));
      setBusy(false);
      return;
    }
    try {
      await access.refresh();
    } catch {
      // The next account read will reconcile UI projection; cancellation already succeeded.
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card data-takyon-appkit="subscription-cancellation">
      <CardHeader>
        <CardTitle>Cancel subscription</CardTitle>
        <CardDescription>{copy.description}</CardDescription>
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
