import React from "react";
import { Redirect } from "expo-router";
import { useProductAuth } from "../src/lib/product-auth";
import AppHomeScreen from "../src/screens/app-home";

// Gated core flow. Unauthenticated visitors bounce to landing.
export default function App() {
  const auth = useProductAuth();
  if (auth.ready && !auth.authenticated) return <Redirect href="/" />;
  return <AppHomeScreen />;
}
