import React from "react";
import { Stack } from "expo-router";
import { StatusBar } from "expo-status-bar";
import { SafeAreaProvider } from "react-native-safe-area-context";
import { ProductAuthProvider } from "../src/lib/product-auth";

// Root layout: auth provider + a stack. expo-router file routes double as the deep-link map that
// the deep_links rail's apple-app-site-association (served from the web publish) points at.
export default function RootLayout() {
  return (
    <SafeAreaProvider>
      <ProductAuthProvider>
        <StatusBar style="light" />
        <Stack screenOptions={{ headerStyle: { backgroundColor: "#0b0b12" }, headerTintColor: "#f5f5f7" }}>
          <Stack.Screen name="index" options={{ title: "Home" }} />
          <Stack.Screen name="app" options={{ title: "App" }} />
          <Stack.Screen name="profile" options={{ title: "Account" }} />
          <Stack.Screen name="settings/licenses" options={{ title: "Licenses" }} />
        </Stack>
      </ProductAuthProvider>
    </SafeAreaProvider>
  );
}
