import React from "react";
import { Linking } from "react-native";
import { Screen, Card, CardHeader, CardTitle, CardDescription, CardContent, Button, Text } from "../components/ui";

// Open-source licenses screen (readmodular §4.2). react-native-legal aggregates every transitive
// dependency's license at prebuild and exposes a native "Used open source libraries" screen; this
// entry opens it. Satisfies MIT/BSD attribution + Apache-2.0 NOTICE-in-display obligations for
// every generated app, automatically.
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
              // react-native-legal registers a native screen; on iOS it is reachable via the
              // launchModal API the plugin exposes. Fallback: the aggregated notices file.
              try {
                // eslint-disable-next-line @typescript-eslint/no-var-requires
                const legal = require("react-native-legal");
                if (legal?.RNLegal?.launchLicenseListScreen) {
                  legal.RNLegal.launchLicenseListScreen();
                  return;
                }
              } catch {
                /* fall through */
              }
              Linking.openURL("https://__TAKYON_SLUG__.coscale.app/licenses").catch(() => {});
            }}
          >
            View licenses
          </Button>
          <Text style={{ color: "#9ca3af", fontSize: 12 }}>
            Generated from all bundled dependencies at build time.
          </Text>
        </CardContent>
      </Card>
    </Screen>
  );
}
