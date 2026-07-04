import React, { useState } from "react";
import { Screen, Card, CardHeader, CardTitle, CardDescription, CardContent, Button, Text } from "../components/ui";
import { useProductAuth } from "../lib/product-auth";
import { surface } from "../lib/takyon";

// Signed-out marketing + Continue with Google. Real copy/branding is filled per business.
export default function LandingScreen() {
  const auth = useProductAuth();
  const [busy, setBusy] = useState(false);
  return (
    <Screen>
      <Card>
        <CardHeader>
          <CardTitle>{surface.branding?.name || "Welcome"}</CardTitle>
          <CardDescription>Sign in to get started.</CardDescription>
        </CardHeader>
        <CardContent>
          {auth.configured ? (
            <Button
              busy={busy}
              onPress={async () => {
                setBusy(true);
                try {
                  await auth.signInWithGoogle();
                } finally {
                  setBusy(false);
                }
              }}
            >
              Continue with Google
            </Button>
          ) : (
            <Text style={{ color: "#9ca3af" }}>Sign-in is not configured yet.</Text>
          )}
        </CardContent>
      </Card>
    </Screen>
  );
}
