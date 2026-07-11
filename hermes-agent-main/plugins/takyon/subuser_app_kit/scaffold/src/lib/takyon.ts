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

/** Human-readable "$N/month" label for the default subscribe plan, derived from the published
 *  plan catalog's `priceCents` + `billingInterval`. Returns "" if no priced plan is published. */
export function defaultPlanPriceLabel(): string {
  const plan = productPlans[0];
  if (!plan) return "";
  const cents = Number(plan.priceCents ?? plan.price_cents ?? Number.NaN);
  if (!Number.isFinite(cents)) return "";
  const dollars = cents / 100;
  const amount = Number.isInteger(dollars) ? String(dollars) : dollars.toFixed(2);
  const interval = String(plan.billingInterval ?? plan.billing_interval ?? "month").trim() || "month";
  return `$${amount}/${interval}`;
}

/** Customer-facing limits derived only from the published billing plan. Never invents an offer. */
export function defaultPlanLimitLabels(): string[] {
  const plan = productPlans[0];
  if (!plan) return [];
  const labels: string[] = [];
  const actionQuota = Number(plan.includedActionQuota ?? plan.included_action_quota ?? 0);
  if (Number.isFinite(actionQuota) && actionQuota > 0) {
    labels.push(`${actionQuota.toLocaleString()} product actions per billing period`);
  }
  const aiBudgetMicrousd = Number(plan.includedAiBudgetMicrousd ?? plan.included_ai_budget_microusd ?? 0);
  if (Number.isFinite(aiBudgetMicrousd) && aiBudgetMicrousd > 0) {
    const dollars = aiBudgetMicrousd / 1_000_000;
    labels.push(`${dollars.toLocaleString(undefined, { style: "currency", currency: "USD" })} AI usage allowance per billing period`);
  }
  return labels;
}

/** One buyable product in the business's Shopify storefront. */
export interface StorefrontProduct {
  productId: string;
  title: string;
  price: string;
  handle: string;
  cartPermalink: string;
  previewUrl: string;
}

/** The business's buyable Shopify catalog, materialized into the surface context at publish time
 *  (the SAME wiring as `productPlans`). Each entry carries a Shopify cart permalink; the store
 *  section renders only when this is non-empty, so a non-Shopify business shows nothing. */
const surfaceCatalog: unknown = (surfaceContext as { shopifyCatalog?: unknown }).shopifyCatalog;
export const productCatalog: StorefrontProduct[] = Array.isArray(surfaceCatalog)
  ? (surfaceCatalog as Array<Record<string, unknown>>)
      .map((p) => ({
        productId: String(p.product_id ?? ""),
        title: String(p.title ?? "").trim(),
        price: String(p.price ?? "").trim(),
        handle: String(p.handle ?? ""),
        cartPermalink: String(p.cart_permalink ?? "").trim(),
        previewUrl: String(p.preview_url ?? ""),
      }))
      .filter((p) => p.title && p.cartPermalink)
  : [];

/** Whether this business has a buyable Shopify storefront (at least one released product). */
export function hasStorefront(): boolean {
  return productCatalog.length > 0;
}

/** "$39.99"-style label for a store-currency price string. Returns "" when unset. */
export function storePriceLabel(price: string): string {
  const trimmed = String(price ?? "").trim();
  if (!trimmed) return "";
  return trimmed.startsWith("$") ? trimmed : `$${trimmed}`;
}

/** Errors thrown by the kit's action runner carry classification metadata. */
export interface TakyonActionError extends Error {
  kind?: string;
  status?: number;
  checkoutUrl?: string;
  rail?: string;
  railState?: string;
}
