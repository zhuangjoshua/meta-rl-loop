import { randomUUID } from "node:crypto";
import type Stripe from "stripe";
import { db } from "../db";
import { getAppEnv } from "../env";
import { NotFoundError } from "../errors";
import { toJson } from "../json";
import { stripeClient } from "../vendors/stripe";

type PaymentLinkRow = {
  id: string;
  business_id: string;
  company_site_id: string;
  site_slug: string;
  business_name: string;
  plan_key: string;
  name: string;
  description: string;
  currency: string;
  unit_amount_cents: number;
  billing_interval: string;
  stripe_product_id: string;
  stripe_price_id: string;
  stripe_payment_link_id: string;
  stripe_payment_link_url: string;
};

function objectId(value: unknown) {
  if (typeof value === "string") return value;
  if (value && typeof value === "object" && "id" in value && typeof value.id === "string") return value.id;
  return null;
}

function generatedAppUrl(slug: string, suffix = "") {
  const { PUBLIC_COMPANY_BASE_DOMAIN } = getAppEnv();
  return `https://${slug}.${PUBLIC_COMPANY_BASE_DOMAIN}${suffix}`;
}

async function getPlan(slug: string, planKey: string) {
  const sql = db();
  const rows = await sql<{
    business_id: string;
    company_site_id: string;
    site_slug: string;
    business_name: string;
    public_pitch: string;
    plan_key: string;
    tier: string;
    price_usd_cents: number;
    billing_interval: string;
  }[]>`
    SELECT
      b.id AS business_id,
      cs.id AS company_site_id,
      cs.slug AS site_slug,
      b.name AS business_name,
      cs.public_pitch,
      gp.plan_key,
      gp.tier,
      gp.price_usd_cents,
      gp.billing_interval
    FROM company_sites cs
    JOIN businesses b ON b.id = cs.business_id
    JOIN generated_app_plan_policies gp ON gp.business_id = b.id
    WHERE cs.slug = ${slug}
      AND gp.plan_key = ${planKey}
    LIMIT 1
  `;
  return rows[0] ?? null;
}

export async function ensureGeneratedAppPaymentLink(input: { slug: string; planKey: string }) {
  const sql = db();
  const existing = await sql<PaymentLinkRow[]>`
    SELECT l.*, cs.slug AS site_slug, b.name AS business_name
    FROM company_payment_links l
    JOIN company_sites cs ON cs.id = l.company_site_id
    JOIN businesses b ON b.id = l.business_id
    WHERE cs.slug = ${input.slug}
      AND l.plan_key = ${input.planKey}
      AND l.active = true
    LIMIT 1
  `;
  if (existing[0]) return existing[0];

  const plan = await getPlan(input.slug, input.planKey);
  if (!plan || plan.price_usd_cents <= 0) throw new NotFoundError("Paid generated-app plan not found.");

  const stripe = stripeClient();
  const metadata = {
    business_id: plan.business_id,
    company_site_id: plan.company_site_id,
    plan_key: plan.plan_key,
    source: "polsia_v3_generated_app"
  };
  const product = await stripe.products.create(
    {
      name: `${plan.business_name} ${plan.plan_key}`,
      description: plan.public_pitch || undefined,
      metadata
    },
    { idempotencyKey: `v3-product:${plan.business_id}:${plan.plan_key}` }
  );
  const price = await stripe.prices.create(
    {
      product: product.id,
      currency: "usd",
      unit_amount: plan.price_usd_cents,
      recurring: plan.billing_interval === "one_time" ? undefined : { interval: "month" },
      metadata
    },
    { idempotencyKey: `v3-price:${plan.business_id}:${plan.plan_key}:${plan.price_usd_cents}` }
  );
  const paymentLink = await stripe.paymentLinks.create(
    {
      line_items: [{ price: price.id, quantity: 1 }],
      after_completion: {
        type: "redirect",
        redirect: { url: generatedAppUrl(plan.site_slug, "?checkout=success") }
      },
      metadata
    },
    { idempotencyKey: `v3-payment-link:${plan.business_id}:${plan.plan_key}` }
  );

  const rows = await sql<PaymentLinkRow[]>`
    INSERT INTO company_payment_links (
      business_id,
      company_site_id,
      plan_key,
      name,
      description,
      currency,
      unit_amount_cents,
      billing_interval,
      stripe_product_id,
      stripe_price_id,
      stripe_payment_link_id,
      stripe_payment_link_url,
      metadata
    )
    VALUES (
      ${plan.business_id},
      ${plan.company_site_id},
      ${plan.plan_key},
      ${plan.plan_key},
      ${`${plan.business_name} ${plan.plan_key} plan`},
      'usd',
      ${plan.price_usd_cents},
      ${plan.billing_interval},
      ${product.id},
      ${price.id},
      ${paymentLink.id},
      ${paymentLink.url},
      ${sql.json(toJson(metadata))}
    )
    RETURNING *, ${plan.site_slug} AS site_slug, ${plan.business_name} AS business_name
  `;

  await sql`
    UPDATE generated_app_plan_policies
    SET company_payment_link_id = ${rows[0].id}
    WHERE business_id = ${plan.business_id}
      AND plan_key = ${plan.plan_key}
  `;

  return rows[0];
}

export async function createGeneratedAppCheckoutSession(input: { slug: string; planKey: string; campaignId?: string | null }) {
  const link = await ensureGeneratedAppPaymentLink(input);
  const sql = db();
  const clientReferenceId = randomUUID();
  const intentRows = await sql<{ id: string }[]>`
    INSERT INTO company_checkout_intents (
      business_id,
      campaign_id,
      company_site_id,
      company_payment_link_id,
      stripe_payment_link_id,
      client_reference_id
    )
    VALUES (${link.business_id}, ${input.campaignId ?? null}, ${link.company_site_id}, ${link.id}, ${link.stripe_payment_link_id}, ${clientReferenceId})
    RETURNING id
  `;

  const mode = link.billing_interval === "one_time" ? "payment" : "subscription";
  const metadata = {
    business_id: link.business_id,
    campaign_id: input.campaignId ?? "",
    company_site_id: link.company_site_id,
    company_payment_link_id: link.id,
    plan_key: link.plan_key,
    checkout_intent_id: intentRows[0].id
  };
  const stripe = stripeClient();
  const session = await stripe.checkout.sessions.create(
    {
      mode,
      line_items: [{ price: link.stripe_price_id, quantity: 1 }],
      success_url: generatedAppUrl(link.site_slug, "?checkout=success&checkout_session_id={CHECKOUT_SESSION_ID}"),
      cancel_url: generatedAppUrl(link.site_slug, "?checkout=cancelled"),
      client_reference_id: clientReferenceId,
      customer_creation: mode === "payment" ? "if_required" : undefined,
      metadata,
      subscription_data: mode === "subscription" ? { metadata } : undefined,
      payment_intent_data: mode === "payment" ? { metadata } : undefined
    },
    { idempotencyKey: `v3-checkout:${link.id}:${clientReferenceId}` }
  );
  if (!session.url) throw new Error("Stripe did not return a checkout URL.");
  return { checkoutUrl: session.url, sessionId: session.id, clientReferenceId, paymentLinkId: link.id };
}

async function resolveCheckoutAttribution(session: Stripe.Checkout.Session) {
  const sql = db();
  const metadata = session.metadata ?? {};
  if (metadata.checkout_intent_id) {
    const rows = await sql<{ business_id: string; campaign_id: string | null; company_site_id: string; company_payment_link_id: string; checkout_intent_id: string }[]>`
      SELECT business_id, campaign_id, company_site_id, company_payment_link_id, id AS checkout_intent_id
      FROM company_checkout_intents
      WHERE id = ${metadata.checkout_intent_id}
      LIMIT 1
    `;
    if (rows[0]) return rows[0];
  }
  if (session.client_reference_id) {
    const rows = await sql<{ business_id: string; campaign_id: string | null; company_site_id: string; company_payment_link_id: string; checkout_intent_id: string }[]>`
      SELECT business_id, campaign_id, company_site_id, company_payment_link_id, id AS checkout_intent_id
      FROM company_checkout_intents
      WHERE client_reference_id = ${session.client_reference_id}
      LIMIT 1
    `;
    if (rows[0]) return rows[0];
  }
  return null;
}

export async function recordStripeCheckoutSession(eventId: string, eventCreated: number, session: Stripe.Checkout.Session) {
  const attribution = await resolveCheckoutAttribution(session);
  if (!attribution) return { recorded: false, reason: "missing_attribution" };

  const sql = db();
  const customerId = objectId(session.customer);
  const subscriptionId = objectId(session.subscription);
  const paymentIntentId = objectId(session.payment_intent);
  const invoiceId = objectId(session.invoice);
  const customerEmail = session.customer_details?.email ?? session.customer_email ?? null;
  const completedAt = new Date(eventCreated * 1000);

  await sql`
    INSERT INTO company_checkout_sessions (
      business_id,
      campaign_id,
      company_site_id,
      company_payment_link_id,
      checkout_intent_id,
      stripe_checkout_session_id,
      stripe_customer_id,
      stripe_payment_intent_id,
      stripe_subscription_id,
      stripe_invoice_id,
      mode,
      payment_status,
      status,
      currency,
      amount_subtotal_cents,
      amount_total_cents,
      client_reference_id,
      customer_email,
      metadata,
      raw_event_id,
      completed_at
    )
    VALUES (
      ${attribution.business_id},
      ${attribution.campaign_id},
      ${attribution.company_site_id},
      ${attribution.company_payment_link_id},
      ${attribution.checkout_intent_id},
      ${session.id},
      ${customerId},
      ${paymentIntentId},
      ${subscriptionId},
      ${invoiceId},
      ${session.mode},
      ${session.payment_status},
      ${session.status},
      ${session.currency},
      ${session.amount_subtotal},
      ${session.amount_total},
      ${session.client_reference_id},
      ${customerEmail},
      ${sql.json(toJson(session.metadata ?? {}))},
      ${eventId},
      ${completedAt}
    )
    ON CONFLICT (stripe_checkout_session_id)
    DO UPDATE SET
      payment_status = EXCLUDED.payment_status,
      status = EXCLUDED.status,
      stripe_subscription_id = EXCLUDED.stripe_subscription_id,
      stripe_invoice_id = EXCLUDED.stripe_invoice_id,
      completed_at = EXCLUDED.completed_at
  `;

  await sql`
    UPDATE company_checkout_intents
    SET status = 'completed',
        completed_at = ${completedAt}
    WHERE id = ${attribution.checkout_intent_id}
  `;

  if (customerEmail && (subscriptionId || session.payment_status === "paid")) {
    const users = await sql<{ id: string }[]>`
      INSERT INTO generated_app_users (business_id, email, tier)
      VALUES (${attribution.business_id}, ${customerEmail.toLowerCase()}, 'paid')
      ON CONFLICT (business_id, email)
      DO UPDATE SET tier = 'paid', updated_at = now()
      RETURNING id
    `;
    await sql`
      INSERT INTO generated_app_entitlements (
        business_id,
        app_user_id,
        tier,
        status,
        source,
        stripe_customer_id,
        stripe_subscription_id,
        company_payment_link_id,
        metadata
      )
      VALUES (
        ${attribution.business_id},
        ${users[0].id},
        'paid',
        'active',
        'stripe',
        ${customerId},
        ${subscriptionId},
        ${attribution.company_payment_link_id},
        ${sql.json(toJson({ stripe_checkout_session_id: session.id, raw_event_id: eventId }))}
      )
      ON CONFLICT DO NOTHING
    `;
  }

  if (session.currency && session.payment_status === "paid") {
    await sql`
    INSERT INTO company_revenue_events (
        business_id,
        campaign_id,
        company_site_id,
        company_payment_link_id,
        checkout_intent_id,
        provider_event_id,
        stripe_object_type,
        stripe_object_id,
        stripe_checkout_session_id,
        stripe_customer_id,
        revenue_type,
        status,
        currency,
        amount_paid_cents,
        customer_email,
        occurred_at,
        metadata
      )
      VALUES (
        ${attribution.business_id},
        ${attribution.campaign_id},
        ${attribution.company_site_id},
        ${attribution.company_payment_link_id},
        ${attribution.checkout_intent_id},
        ${eventId},
        'checkout.session',
        ${session.id},
        ${session.id},
        ${customerId},
        'checkout',
        ${session.payment_status},
        ${session.currency},
        ${session.amount_total ?? 0},
        ${customerEmail},
        ${completedAt},
        ${sql.json(toJson(session.metadata ?? {}))}
      )
      ON CONFLICT DO NOTHING
    `;
  }

  return { recorded: true };
}

export async function updateGeneratedAppSubscriptionEntitlement(subscription: Stripe.Subscription) {
  const sql = db();
  const customerId = objectId(subscription.customer);
  const rawPeriodEnd = (subscription as unknown as { current_period_end?: unknown }).current_period_end;
  const periodEnd = typeof rawPeriodEnd === "number"
    ? new Date(rawPeriodEnd * 1000)
    : null;
  const status = subscription.status === "active" || subscription.status === "trialing" ? "active" : subscription.status === "canceled" ? "cancelled" : "past_due";
  await sql`
    UPDATE generated_app_entitlements
    SET status = ${status},
        stripe_customer_id = COALESCE(${customerId}, stripe_customer_id),
        current_period_end = COALESCE(${periodEnd}, current_period_end),
        metadata = metadata || ${sql.json(toJson({ stripe_subscription_status: subscription.status }))}::jsonb
    WHERE source = 'stripe'
      AND stripe_subscription_id = ${subscription.id}
  `;
}
