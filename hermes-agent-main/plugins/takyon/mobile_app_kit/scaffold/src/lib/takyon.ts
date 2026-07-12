// The ONE client construction point (mirror of the web scaffold's src/lib/takyon.ts). App code
// imports the client from here; it never touches _takyon/ directly beyond the surface context.
import { createMobileRuntimeClient } from "@takyon/runtime-client";
export type {
  AccountPayload,
  AppEntitlement,
  ProductRuntimeContract,
  SubscriptionCancellationPolicy,
  SubscriptionCancellationResult,
  SubscriptionState,
} from "@takyon/runtime-client";
import { surfaceContext } from "@takyon/surface-context";
import { getToken, setToken, clearToken } from "./session-store";

export const surface = surfaceContext;

export const client = createMobileRuntimeClient(surfaceContext, {
  getToken,
  setToken,
  clearToken,
});

export function useTakyon() {
  return client;
}
