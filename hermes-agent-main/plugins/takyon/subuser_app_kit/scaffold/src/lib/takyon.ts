// The one place the Takyon kit is constructed. All app code imports `client`
// from here; nothing else may import from _takyon/ directly except this file
// (the platform overwrites _takyon/ wholesale in real products).
import { createSubuserRuntimeClient } from "@takyon/runtime-client.js";
import { surfaceContext } from "@takyon/surface-context.js";

export const client = createSubuserRuntimeClient(surfaceContext);

export type TakyonClient = typeof client;

/** The business's published plan catalog, materialized into the surface context at publish time
 *  (each entry carries `planKey`). Lets a subscribe CTA resolve which plan to check out without
 *  hardcoding a key — every business inherits the same wiring. */
const surfacePlans: unknown = (surfaceContext as { plans?: unknown }).plans;
export const productPlans: Array<Record<string, unknown>> = Array.isArray(surfacePlans)
  ? (surfacePlans as Array<Record<string, unknown>>)
  : [];

/** The plan key a "subscribe" CTA should check out: the first published plan with a key, else "". */
export function defaultSubscribePlanKey(): string {
  for (const plan of productPlans) {
    const key = String(plan.planKey ?? plan.plan_key ?? "").trim();
    if (key) return key;
  }
  return "";
}

/** Errors thrown by the kit's action runner carry classification metadata. */
export interface TakyonActionError extends Error {
  kind?: string;
  status?: number;
  checkoutUrl?: string;
  rail?: string;
  railState?: string;
}
