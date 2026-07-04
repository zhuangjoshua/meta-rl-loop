import React, { useState } from "react";
import { useRouter } from "expo-router";
import { Screen, Card, CardHeader, CardTitle, CardDescription, CardContent, Button, Input, Text } from "../components/ui";
import { useTakyon } from "../lib/takyon";

// The core signed-in flow. Demo wiring for the generate rail (all AI brokered server-side — no
// provider key on device). Replaced per business by the CEO worker.
export default function AppHomeScreen() {
  const client = useTakyon();
  const router = useRouter();
  const [prompt, setPrompt] = useState("");
  const [out, setOut] = useState("");
  const [busy, setBusy] = useState(false);

  return (
    <Screen>
      <Card>
        <CardHeader>
          <CardTitle>Your workspace</CardTitle>
          <CardDescription>A starting point — the product is built per business.</CardDescription>
        </CardHeader>
        <CardContent>
          <Input placeholder="Ask something…" value={prompt} onChangeText={setPrompt} />
          <Button
            busy={busy}
            disabled={!prompt.trim() || !client.isRailCallable("generate")}
            onPress={async () => {
              setBusy(true);
              try {
                const r = await client.generate({ prompt });
                setOut(String((r as any)?.text ?? JSON.stringify(r)));
              } catch (e: any) {
                setOut(String(e?.message ?? e));
              } finally {
                setBusy(false);
              }
            }}
          >
            Generate
          </Button>
          {!!out && <Text style={{ color: "#f5f5f7" }}>{out}</Text>}
        </CardContent>
      </Card>
      <Button variant="secondary" onPress={() => router.push("/profile")}>
        Account
      </Button>
    </Screen>
  );
}
