import React, { useState } from "react";
import { Alert } from "react-native";
import { Button, Card, CardContent, CardDescription, CardHeader, CardTitle } from "./ui";
import {
  hasNonterminalStripeSubscription,
  useProductAuth,
} from "../lib/product-auth";
import { useTakyon } from "../lib/takyon";

function cancellationPolicy(account: any): Record<string, unknown> | null {
  const policy = account?.subscription_cancellation_policy;
  return policy && typeof policy === "object" && !Array.isArray(policy) ? policy : null;
}

function noRefundSuffix(policy: Record<string, unknown> | null): string {
  return policy?.refund_policy === "none" ? " Cancellation does not issue a refund." : "";
}

function preCancellationCopy(account: any): { description: string; confirmation: string } {
  const policy = cancellationPolicy(account);
  const refund = noRefundSuffix(policy);
  if (policy?.effective_timing === "immediate") {
    return {
      description: `Cancellation ends access immediately.${refund}`,
      confirmation: `Your access will end immediately.${refund}`,
    };
  }
  if (policy?.effective_timing === "period_end") {
    return {
      description: `Cancellation takes effect at the end of the current billing period.${refund}`,
      confirmation: `Access will remain available until the end of the current billing period.${refund}`,
    };
  }
  return {
    description: "The server will confirm when cancellation takes effect.",
    confirmation: "The server response will confirm when access ends.",
  };
}

function cancellationResultCopy(outcome: any): string {
  const policy =
    outcome?.subscription_cancellation_policy &&
    typeof outcome.subscription_cancellation_policy === "object"
      ? outcome.subscription_cancellation_policy
      : null;
  const refund = policy?.refund_policy === "none" ? " No refund was issued." : "";
  if (outcome?.effective_immediately === true && outcome?.cancel_at_period_end === false) {
    return `Your access ended immediately.${refund}`;
  }
  if (outcome?.cancel_at_period_end === true && outcome?.effective_immediately !== true) {
    const rawEnd = String(outcome?.current_period_end ?? "").trim();
    const parsedEnd = rawEnd ? new Date(rawEnd) : null;
    const endLabel =
      parsedEnd && !Number.isNaN(parsedEnd.getTime())
        ? new Intl.DateTimeFormat(undefined, { dateStyle: "long" }).format(parsedEnd)
        : "the end of the current billing period";
    return `Your subscription will end on ${endLabel}.${refund}`;
  }
  return "Cancellation confirmed. Refresh your account to see the current access state.";
}

/** Platform-owned backend-truthful cancellation control; product screens may compose it anywhere. */
export function SubscriptionCancellation() {
  const auth = useProductAuth();
  const client = useTakyon();
  const [busy, setBusy] = useState(false);
  const [canceledLocally, setCanceledLocally] = useState(false);

  if (canceledLocally || !hasNonterminalStripeSubscription(auth.account)) return null;
  const copy = preCancellationCopy(auth.account);

  const confirmCancellation = () =>
    new Promise<boolean>((resolve) => {
      Alert.alert(
        "Cancel your subscription now?",
        copy.confirmation,
        [
          { text: "Keep subscription", style: "cancel", onPress: () => resolve(false) },
          { text: "Cancel now", style: "destructive", onPress: () => resolve(true) },
        ],
      );
    });

  return (
    <Card>
      <CardHeader>
        <CardTitle>Cancel subscription</CardTitle>
        <CardDescription>{copy.description}</CardDescription>
      </CardHeader>
      <CardContent>
        <Button
          variant="destructive"
          busy={busy}
          onPress={async () => {
            if (!(await confirmCancellation())) return;
            setBusy(true);
            let outcome: any;
            try {
              outcome = await client.cancelSubscription();
            } catch (error: unknown) {
              const message = error instanceof Error ? error.message : String(error);
              Alert.alert("Could not cancel subscription", message);
              setBusy(false);
              return;
            }
            // Provider-authoritative cancellation succeeded; hide the control before the local
            // account projection refresh, which is best-effort and cannot reverse success.
            setCanceledLocally(true);
            Alert.alert("Subscription canceled", cancellationResultCopy(outcome));
            try {
              await auth.refresh();
            } catch {
              // A later account read will refresh projection; Stripe cancellation is complete.
            } finally {
              setBusy(false);
            }
          }}
        >
          Cancel subscription now
        </Button>
      </CardContent>
    </Card>
  );
}
