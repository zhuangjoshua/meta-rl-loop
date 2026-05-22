import type Stripe from "stripe";
import { db } from "../db";
import { upsertBusinessDocument } from "../documents";
import { ConfigurationError, ForbiddenError, NotFoundError } from "../errors";
import { createEvent } from "../events";
import { toJson } from "../json";
import { stripeClient } from "../vendors/stripe";
import { writeBusinessWorkspaceFile } from "../business-workspace";

type AppUserRow = {
  id: string;
  email: string;
  name: string | null;
  status: string;
  tier: string;
  metadata: unknown;
  created_at: string;
  updated_at: string;
};

type EntitlementRow = {
  id: string;
  app_user_id: string;
  tier: string;
  status: string;
  source: string;
  stripe_customer_id: string | null;
  stripe_subscription_id: string | null;
  company_payment_link_id: string | null;
  current_period_end: string | null;
  metadata: unknown;
  created_at: string;
  updated_at: string;
};

type ProductRunRow = {
  id: string;
  status: string;
  input: unknown;
  output: unknown;
  error: string | null;
  created_at: string;
};

type PlanPolicyRow = {
  plan_key: string;
  tier: string;
  price_usd_cents: number;
  billing_interval: string;
  included_ai_budget_microusd: string;
  included_action_quota: number;
  allow_overage: boolean;
};

function dollars(cents: number) {
  return `$${(cents / 100).toFixed(2)}`;
}

function firstRecord(value: unknown) {
  return value && typeof value === "object" && !Array.isArray(value) ? (value as Record<string, unknown>) : {};
}

function subscriptionLifecycleMetadata(subscription: Stripe.Subscription) {
  const raw = subscription as unknown as Record<string, unknown>;
  const epoch = (key: string) => typeof raw[key] === "number" ? new Date(Number(raw[key]) * 1000).toISOString() : null;
  return {
    stripe_subscription_status: subscription.status,
    cancel_at_period_end: Boolean(raw.cancel_at_period_end),
    current_period_start: epoch("current_period_start"),
    current_period_end: epoch("current_period_end"),
    trial_start: epoch("trial_start"),
    trial_end: epoch("trial_end"),
    cancel_at: epoch("cancel_at"),
    canceled_at: epoch("canceled_at"),
    ended_at: epoch("ended_at")
  };
}

export function generatedAppEntitlementStatusFromStripe(status: Stripe.Subscription.Status) {
  if (status === "active" || status === "trialing") return "active";
  if (status === "canceled") return "cancelled";
  return "past_due";
}

export function generatedAppSubscriptionLifecycle(subscription: Stripe.Subscription) {
  const raw = subscription as unknown as Record<string, unknown>;
  const rawPeriodEnd = raw.current_period_end;
  return {
    customerId: typeof subscription.customer === "string" ? subscription.customer : subscription.customer?.id ?? null,
    periodEnd: typeof rawPeriodEnd === "number" ? new Date(rawPeriodEnd * 1000) : null,
    status: generatedAppEntitlementStatusFromStripe(subscription.status),
    metadata: subscriptionLifecycleMetadata(subscription)
  };
}

async function entitlementsForUser(businessId: string, appUserId: string) {
  const sql = db();
  return sql<EntitlementRow[]>`
    SELECT id,
           app_user_id,
           tier,
           status,
           source,
           stripe_customer_id,
           stripe_subscription_id,
           company_payment_link_id,
           current_period_end::text,
           metadata,
           created_at::text,
           updated_at::text
    FROM generated_app_entitlements
    WHERE business_id = ${businessId}
      AND app_user_id = ${appUserId}
    ORDER BY
      CASE WHEN status = 'active' THEN 0 WHEN status = 'past_due' THEN 1 ELSE 2 END,
      CASE tier WHEN 'owner' THEN 0 WHEN 'paid' THEN 1 ELSE 2 END,
      updated_at DESC
  `;
}

async function effectivePlanPolicy(businessId: string, entitlement: EntitlementRow | null) {
  const sql = db();
  const tier = entitlement?.status === "active" ? entitlement.tier : "free";
  const rows = await sql<PlanPolicyRow[]>`
    SELECT plan_key,
           tier,
           price_usd_cents,
           billing_interval,
           included_ai_budget_microusd::text,
           included_action_quota,
           allow_overage
    FROM generated_app_plan_policies
    WHERE business_id = ${businessId}
      AND (tier = ${tier} OR plan_key = ${tier})
    ORDER BY price_usd_cents DESC, created_at DESC
    LIMIT 1
  `;
  if (rows[0]) return rows[0];
  const fallback = await sql<PlanPolicyRow[]>`
    SELECT plan_key,
           tier,
           price_usd_cents,
           billing_interval,
           included_ai_budget_microusd::text,
           included_action_quota,
           allow_overage
    FROM generated_app_plan_policies
    WHERE business_id = ${businessId}
      AND tier = 'free'
    ORDER BY created_at ASC
    LIMIT 1
  `;
  return fallback[0] ?? null;
}

export async function getGeneratedAppCustomerAccount(input: { businessId: string; appUserId: string }) {
  const sql = db();
  const users = await sql<AppUserRow[]>`
    SELECT id, email, name, status, tier, metadata, created_at::text, updated_at::text
    FROM generated_app_users
    WHERE business_id = ${input.businessId}
      AND id = ${input.appUserId}
    LIMIT 1
  `;
  const user = users[0];
  if (!user) throw new NotFoundError("Generated app user not found.");
  if (user.status !== "active") throw new ForbiddenError("Generated app user is not active.");

  const [entitlements, recentRuns, usageRows, revenueRows] = await Promise.all([
    entitlementsForUser(input.businessId, input.appUserId),
    sql<ProductRunRow[]>`
      SELECT id, status, input, output, error, created_at::text
      FROM generated_app_product_runs
      WHERE business_id = ${input.businessId}
        AND generated_app_user_id = ${input.appUserId}
      ORDER BY created_at DESC
      LIMIT 20
    `,
    sql<{ event_count: number; estimated_microusd: string; actual_microusd: string }[]>`
      SELECT count(*)::int AS event_count,
             COALESCE(sum(estimated_cost_microusd), 0)::text AS estimated_microusd,
             COALESCE(sum(actual_cost_microusd), 0)::text AS actual_microusd
      FROM project_ai_usage_events
      WHERE business_id = ${input.businessId}
        AND app_user_key = ${input.appUserId}
        AND created_at >= date_trunc('month', now())
    `,
    sql<{ amount_paid_cents: number; currency: string; occurred_at: string }[]>`
      SELECT amount_paid_cents, currency, occurred_at::text
      FROM company_revenue_events
      WHERE business_id = ${input.businessId}
        AND lower(customer_email) = lower(${user.email})
      ORDER BY occurred_at DESC
      LIMIT 20
    `
  ]);

  const activeEntitlement = entitlements.find((row) => row.status === "active") ?? entitlements[0] ?? null;
  const plan = await effectivePlanPolicy(input.businessId, activeEntitlement);
  const usage = usageRows[0] ?? { event_count: 0, estimated_microusd: "0", actual_microusd: "0" };
  const revenueCents = revenueRows.reduce((sum, row) => sum + Number(row.amount_paid_cents || 0), 0);

  return {
    user,
    effectiveTier: activeEntitlement?.status === "active" ? activeEntitlement.tier : "free",
    plan,
    entitlements,
    billingPortalAvailable: entitlements.some((row) => Boolean(row.stripe_customer_id)),
    usageThisPeriod: {
      actionCount: usage.event_count,
      estimatedCostMicrousd: usage.estimated_microusd,
      actualCostMicrousd: usage.actual_microusd
    },
    revenue: {
      totalPaidCents: revenueCents,
      totalPaidDisplay: dollars(revenueCents),
      recent: revenueRows
    },
    recentRuns
  };
}

async function stripeCustomerIdForUser(businessId: string, appUserId: string) {
  const sql = db();
  const rows = await sql<{ stripe_customer_id: string | null; email: string }[]>`
    SELECT e.stripe_customer_id, u.email
    FROM generated_app_entitlements e
    JOIN generated_app_users u ON u.id = e.app_user_id
    WHERE e.business_id = ${businessId}
      AND e.app_user_id = ${appUserId}
      AND e.stripe_customer_id IS NOT NULL
    ORDER BY CASE e.status WHEN 'active' THEN 0 ELSE 1 END, e.updated_at DESC
    LIMIT 1
  `;
  if (rows[0]?.stripe_customer_id) return rows[0].stripe_customer_id;

  const userRows = await sql<{ email: string }[]>`
    SELECT email
    FROM generated_app_users
    WHERE business_id = ${businessId}
      AND id = ${appUserId}
    LIMIT 1
  `;
  const email = userRows[0]?.email;
  if (!email) throw new NotFoundError("Generated app user not found.");

  const checkoutRows = await sql<{ stripe_customer_id: string | null }[]>`
    SELECT stripe_customer_id
    FROM company_checkout_sessions
    WHERE business_id = ${businessId}
      AND lower(customer_email) = lower(${email})
      AND stripe_customer_id IS NOT NULL
    ORDER BY completed_at DESC NULLS LAST, updated_at DESC
    LIMIT 1
  `;
  return checkoutRows[0]?.stripe_customer_id ?? null;
}

async function portalConfigurationId(stripe: Stripe, businessId: string) {
  const existing = await stripe.billingPortal.configurations.list({ limit: 1, active: true });
  if (existing.data[0]) return existing.data[0].id;

  const sql = db();
  const prices = await sql<{ stripe_product_id: string; stripe_price_id: string }[]>`
    SELECT stripe_product_id, stripe_price_id
    FROM company_payment_links
    WHERE business_id = ${businessId}
      AND active = true
      AND billing_interval <> 'one_time'
    ORDER BY unit_amount_cents DESC
    LIMIT 20
  `;
  const productMap = new Map<string, string[]>();
  for (const row of prices) {
    const list = productMap.get(row.stripe_product_id) ?? [];
    list.push(row.stripe_price_id);
    productMap.set(row.stripe_product_id, list);
  }

  type PortalConfigurationCreateParams = Parameters<typeof stripe.billingPortal.configurations.create>[0];
  const features: PortalConfigurationCreateParams["features"] = {
    customer_update: { enabled: true, allowed_updates: ["email", "name"] },
    payment_method_update: { enabled: true },
    subscription_cancel: { enabled: true, mode: "at_period_end" },
    invoice_history: { enabled: true }
  };
  if (productMap.size > 0) {
    features.subscription_update = {
      enabled: true,
      default_allowed_updates: ["price"],
      proration_behavior: "create_prorations",
      products: [...productMap.entries()].map(([product, pricesForProduct]) => ({
        product,
        prices: pricesForProduct
      }))
    };
  }

  const created = await stripe.billingPortal.configurations.create({
    business_profile: {
      headline: "Manage your subscription"
    },
    features,
    metadata: {
      source: "takyon_generated_app_customer_ops",
      business_id: businessId
    }
  });
  return created.id;
}

export async function createGeneratedAppBillingPortalSession(input: {
  businessId: string;
  appUserId: string;
  returnUrl: string;
}) {
  const customerId = await stripeCustomerIdForUser(input.businessId, input.appUserId);
  if (!customerId) throw new NotFoundError("No Stripe customer exists for this generated-app user yet.");
  const stripe = stripeClient();
  const configuration = await portalConfigurationId(stripe, input.businessId);
  const session = await stripe.billingPortal.sessions.create({
    customer: customerId,
    return_url: input.returnUrl,
    configuration
  });
  if (!session.url) throw new ConfigurationError("Stripe did not return a billing portal URL.");
  return { url: session.url, customerId, configuration };
}

export async function ensureGeneratedAppProductRunAllowance(input: {
  businessId: string;
  appUserId: string;
}) {
  const entitlements = await entitlementsForUser(input.businessId, input.appUserId);
  const active = entitlements.find((row) => row.status === "active") ?? null;
  const plan = await effectivePlanPolicy(input.businessId, active);
  if (!plan) return { tier: "free", planKey: "free", used: 0, includedActionQuota: 25, allowOverage: false };

  const sql = db();
  const usageRows = await sql<{ used: number }[]>`
    SELECT count(*)::int AS used
    FROM generated_app_product_runs
    WHERE business_id = ${input.businessId}
      AND generated_app_user_id = ${input.appUserId}
      AND created_at >= date_trunc('month', now())
  `;
  const used = usageRows[0]?.used ?? 0;
  if (!plan.allow_overage && used >= plan.included_action_quota) {
    throw new ForbiddenError(`This ${plan.plan_key} plan has used its ${plan.included_action_quota} product runs for the current period.`);
  }
  return {
    tier: active?.status === "active" ? active.tier : plan.tier,
    planKey: plan.plan_key,
    used,
    includedActionQuota: plan.included_action_quota,
    allowOverage: plan.allow_overage
  };
}

export async function syncGeneratedAppUserTierFromEntitlements(input: { businessId: string; appUserId: string }) {
  const sql = db();
  const rows = await sql<{ tier: string }[]>`
    SELECT COALESCE((
      SELECT e.tier
      FROM generated_app_entitlements e
      WHERE e.business_id = ${input.businessId}
        AND e.app_user_id = ${input.appUserId}
        AND e.status = 'active'
      ORDER BY CASE e.tier WHEN 'owner' THEN 0 WHEN 'paid' THEN 1 ELSE 2 END, e.updated_at DESC
      LIMIT 1
    ), 'free') AS tier
  `;
  const tier = rows[0]?.tier ?? "free";
  await sql`
    UPDATE generated_app_users
    SET tier = ${tier},
        updated_at = now()
    WHERE business_id = ${input.businessId}
      AND id = ${input.appUserId}
  `;
  return tier;
}

export async function generatedAppCustomerOpsSnapshot(businessId: string) {
  const sql = db();
  const [
    userCounts,
    entitlementCounts,
    runCounts,
    revenueCounts,
    recentUsers,
    recentEntitlements,
    recentRuns
  ] = await Promise.all([
    sql<{ total: number; active: number; free: number; paid: number }[]>`
      SELECT count(*)::int AS total,
             count(*) FILTER (WHERE status = 'active')::int AS active,
             count(*) FILTER (WHERE tier = 'free')::int AS free,
             count(*) FILTER (WHERE tier IN ('paid', 'owner'))::int AS paid
      FROM generated_app_users
      WHERE business_id = ${businessId}
    `,
    sql<{ status: string; tier: string; source: string; count: number }[]>`
      SELECT status, tier, source, count(*)::int AS count
      FROM generated_app_entitlements
      WHERE business_id = ${businessId}
      GROUP BY status, tier, source
      ORDER BY source ASC, tier ASC, status ASC
    `,
    sql<{ total: number; completed: number; blocked: number; last_30d: number }[]>`
      SELECT count(*)::int AS total,
             count(*) FILTER (WHERE status = 'completed')::int AS completed,
             count(*) FILTER (WHERE status <> 'completed')::int AS blocked,
             count(*) FILTER (WHERE created_at >= now() - interval '30 days')::int AS last_30d
      FROM generated_app_product_runs
      WHERE business_id = ${businessId}
    `,
    sql<{ total_cents: string; paid_events: number; recent_cents: string }[]>`
      SELECT COALESCE(sum(amount_paid_cents), 0)::text AS total_cents,
             count(*) FILTER (WHERE amount_paid_cents > 0)::int AS paid_events,
             COALESCE(sum(amount_paid_cents) FILTER (WHERE occurred_at >= now() - interval '30 days'), 0)::text AS recent_cents
      FROM company_revenue_events
      WHERE business_id = ${businessId}
    `,
    sql<AppUserRow[]>`
      SELECT id, email, name, status, tier, metadata, created_at::text, updated_at::text
      FROM generated_app_users
      WHERE business_id = ${businessId}
      ORDER BY updated_at DESC
      LIMIT 50
    `,
    sql<EntitlementRow[]>`
      SELECT id, app_user_id, tier, status, source, stripe_customer_id, stripe_subscription_id,
             company_payment_link_id, current_period_end::text, metadata, created_at::text, updated_at::text
      FROM generated_app_entitlements
      WHERE business_id = ${businessId}
      ORDER BY updated_at DESC
      LIMIT 50
    `,
    sql<ProductRunRow[]>`
      SELECT id, status, input, output, error, created_at::text
      FROM generated_app_product_runs
      WHERE business_id = ${businessId}
      ORDER BY created_at DESC
      LIMIT 50
    `
  ]);
  const revenue = revenueCounts[0] ?? { total_cents: "0", paid_events: 0, recent_cents: "0" };
  const totals = {
    users: userCounts[0] ?? { total: 0, active: 0, free: 0, paid: 0 },
    productRuns: runCounts[0] ?? { total: 0, completed: 0, blocked: 0, last_30d: 0 },
    revenue: {
      totalCents: Number(revenue.total_cents || 0),
      totalDisplay: dollars(Number(revenue.total_cents || 0)),
      last30dCents: Number(revenue.recent_cents || 0),
      last30dDisplay: dollars(Number(revenue.recent_cents || 0)),
      paidEvents: revenue.paid_events
    }
  };
  return {
    businessId,
    generatedAt: new Date().toISOString(),
    totals,
    entitlementsByStatus: entitlementCounts,
    recentUsers,
    recentEntitlements,
    recentRuns
  };
}

function customerOpsMarkdown(snapshot: Awaited<ReturnType<typeof generatedAppCustomerOpsSnapshot>>) {
  const recentCustomers = snapshot.recentUsers.slice(0, 10);
  const atRisk = snapshot.recentEntitlements.filter((row) => row.status === "past_due" || row.status === "cancelled").slice(0, 10);
  return [
    "# Customer Ops Snapshot",
    "",
    `Generated: ${snapshot.generatedAt}`,
    "",
    "## Current State",
    `- Active users: ${snapshot.totals.users.active}`,
    `- Paid or owner users: ${snapshot.totals.users.paid}`,
    `- Free users: ${snapshot.totals.users.free}`,
    `- Product runs all time: ${snapshot.totals.productRuns.total}`,
    `- Product runs last 30 days: ${snapshot.totals.productRuns.last_30d}`,
    `- Revenue all time: ${snapshot.totals.revenue.totalDisplay}`,
    `- Revenue last 30 days: ${snapshot.totals.revenue.last30dDisplay}`,
    "",
    "## Entitlements",
    ...(snapshot.entitlementsByStatus.length
      ? snapshot.entitlementsByStatus.map((row) => `- ${row.source}/${row.tier}/${row.status}: ${row.count}`)
      : ["- No entitlement rows yet."]),
    "",
    "## Attention",
    ...(atRisk.length
      ? atRisk.map((row) => {
          const metadata = firstRecord(row.metadata);
          const stripeStatus = typeof metadata.stripe_subscription_status === "string" ? metadata.stripe_subscription_status : row.status;
          return `- ${row.tier} ${row.status} (${stripeStatus}) user=${row.app_user_id} period_end=${row.current_period_end ?? "unknown"}`;
        })
      : ["- No past-due or cancelled entitlements in the recent snapshot."]),
    "",
    "## Recent Customers",
    ...(recentCustomers.length
      ? recentCustomers.map((row) => `- ${row.email} tier=${row.tier} status=${row.status} updated=${row.updated_at}`)
      : ["- No generated-app customers yet."])
  ].join("\n") + "\n";
}

export async function runCustomerOpsWatch(input: { businessId: string; profileId?: string | null }) {
  const snapshot = await generatedAppCustomerOpsSnapshot(input.businessId);
  const content = customerOpsMarkdown(snapshot);
  const document = await upsertBusinessDocument({
    companyId: input.businessId,
    title: "Customer Ops Snapshot",
    kind: "document",
    content,
    source: "workflow",
    metadata: {
      workflow_id: "customer_ops_watch",
      totals: snapshot.totals
    },
    replaceMetadata: true
  });
  const workspaceWrite = await writeBusinessWorkspaceFile({
    businessId: input.businessId,
    relativePath: "product/customer-ops.md",
    content
  });
  await createEvent({
    businessId: input.businessId,
    actorProfileId: input.profileId ?? null,
    kind: "customer_ops.watch_completed",
    subjectType: "business_document",
    subjectId: document.id,
    payload: {
      totals: snapshot.totals,
      workspace_path: workspaceWrite.path
    }
  });
  return {
    status: "completed",
    documentId: document.id,
    workspacePath: workspaceWrite.path,
    totals: snapshot.totals
  };
}
