import React, { useState } from "react";
import { Alert } from "react-native";
import { useRouter } from "expo-router";
import { Screen, Card, CardHeader, CardTitle, CardDescription, CardContent, Button, Text } from "../components/ui";
import { useProductAuth } from "../lib/product-auth";
import { useTakyon } from "../lib/takyon";

// Account overview + Sign out + Delete account (Apple 5.1.1(v)). The Delete flow is reviewer-tested
// (sign in -> delete -> sign in again), so it must actually work end-to-end — it calls the
// account.delete rail, which closes + anonymizes the account server-side, then clears the local
// session. Maestro taps this exact flow as the sim acceptance.
export default function ProfileScreen() {
  const auth = useProductAuth();
  const client = useTakyon();
  const router = useRouter();
  const [busy, setBusy] = useState(false);

  const confirmDestructive = () =>
    new Promise<boolean>((resolve) => {
      Alert.alert(
        "Delete your account?",
        "This permanently deletes your account and data. This cannot be undone.",
        [
          { text: "Cancel", style: "cancel", onPress: () => resolve(false) },
          { text: "Delete", style: "destructive", onPress: () => resolve(true) },
        ],
      );
    });

  return (
    <Screen>
      <Card>
        <CardHeader>
          <CardTitle>Account</CardTitle>
          <CardDescription>{auth.user?.email ?? "Signed in"} · {auth.tier}</CardDescription>
        </CardHeader>
        <CardContent>
          <Button variant="secondary" onPress={() => router.push("/settings/licenses")}>
            Open source licenses
          </Button>
          <Button
            variant="secondary"
            onPress={async () => {
              await auth.logoutLocal();
              router.replace("/");
            }}
          >
            Sign out
          </Button>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Delete account</CardTitle>
          <CardDescription>Permanently delete your account and data. This cannot be undone.</CardDescription>
        </CardHeader>
        <CardContent>
          <Button
            variant="destructive"
            busy={busy}
            onPress={async () => {
              if (!(await confirmDestructive())) return;
              setBusy(true);
              try {
                await client.deleteAccount();
                await auth.logoutLocal();
                router.replace("/");
              } catch (e: any) {
                Alert.alert("Could not delete account", String(e?.message ?? e));
              } finally {
                setBusy(false);
              }
            }}
          >
            Delete my account
          </Button>
          <Text style={{ color: "#9ca3af", fontSize: 12 }}>
            Deleting frees your email for a fresh sign-up later.
          </Text>
        </CardContent>
      </Card>
    </Screen>
  );
}
