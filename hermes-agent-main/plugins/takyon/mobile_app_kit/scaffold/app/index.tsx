import React from "react";
import { Redirect } from "expo-router";
import { useProductAuth } from "../src/lib/product-auth";
import LandingScreen from "../src/screens/landing";

// Signed-out landing; signed-in users go straight to /app.
export default function Index() {
  const auth = useProductAuth();
  if (auth.ready && auth.authenticated) return <Redirect href="/app" />;
  return <LandingScreen />;
}
