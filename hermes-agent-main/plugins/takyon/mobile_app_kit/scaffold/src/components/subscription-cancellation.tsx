import React, { useState } from "react";
import { Alert } from "react-native";
import { Button, Card, CardContent, CardDescription, CardHeader, CardTitle } from "./ui";
import {
  hasNonterminalStripeSubscription,
  useProductAuth,
} from "../lib/product-auth";
import { useTakyon } from "../lib/takyon";

/** Platform-owned immediate cancellation control; product screens may compose it anywhere. */
export function SubscriptionCancellation() {
  const auth = useProductAuth();
  const client = useTakyon();
  const [busy, setBusy] = useState(false);
  const [canceledLocally, setCanceledLocally] = useState(false);

  if (canceledLocally || !hasNonterminalStripeSubscription(auth.account)) return null;

  const confirmCancellation = () =>
    new Promise<boolean>((resolve) => {
      Alert.alert(
        "Cancel your subscription now?",
        "Your access will end immediately. There is no grace period.",
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
        <CardDescription>
          Cancellation ends access immediately. There is no grace period, and you will not be
          charged for another billing period.
        </CardDescription>
      </CardHeader>
      <CardContent>
        <Button
          variant="destructive"
          busy={busy}
          onPress={async () => {
            if (!(await confirmCancellation())) return;
            setBusy(true);
            try {
              await client.cancelSubscription();
            } catch (error: unknown) {
              const message = error instanceof Error ? error.message : String(error);
              Alert.alert("Could not cancel subscription", message);
              setBusy(false);
              return;
            }
            // Provider-authoritative cancellation succeeded; hide the control before the local
            // account projection refresh, which is best-effort and cannot reverse success.
            setCanceledLocally(true);
            Alert.alert("Subscription canceled", "Your access has ended immediately.");
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
