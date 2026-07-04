import React from "react";
import { Linking } from "react-native";
import { Screen, Card, CardHeader, CardTitle, CardDescription, CardContent, Button, Text } from "../components/ui";

// Open-source licenses screen (readmodular §4.2). Attribution is served from the product's web
// surface at /licenses (published alongside the site). An in-app native aggregator was tried
// (react-native-legal) but its config plugin unconditionally writes Android license metadata and
// crashes managed iOS-only prebuild/introspect (verified against the real EAS lane 2026-07-04),
// so the web page is the canonical notice surface until an iOS-safe aggregator exists.
export default function LicensesScreen() {
  return (
    <Screen>
      <Card>
        <CardHeader>
          <CardTitle>Open source licenses</CardTitle>
          <CardDescription>This app is built with open source software.</CardDescription>
        </CardHeader>
        <CardContent>
          <Button
            onPress={() => {
              Linking.openURL("https://__TAKYON_SLUG__.coscale.app/licenses").catch(() => {});
            }}
          >
            View licenses
          </Button>
          <Text style={{ color: "#9ca3af", fontSize: 12 }}>
            Full attribution for all bundled dependencies.
          </Text>
        </CardContent>
      </Card>
    </Screen>
  );
}
