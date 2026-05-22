import { headers } from "next/headers";
import { db } from "@/lib/db";
import { getStripeEnv } from "@/lib/env";
import { recordStripeCheckoutSession, updateGeneratedAppSubscriptionEntitlement } from "@/lib/generated-apps/commerce";
import { jsonError, jsonOk } from "@/lib/http";
import { toJson } from "@/lib/json";
import { stripeClient } from "@/lib/vendors/stripe";

export async function POST(request: Request) {
  try {
    const env = getStripeEnv();
    const stripe = stripeClient();
    const rawBody = await request.text();
    const signature = (await headers()).get("stripe-signature");
    if (!signature) return jsonOk({ ok: false, received: false, error: "missing stripe-signature" }, { status: 400 });

    const event = stripe.webhooks.constructEvent(rawBody, signature, env.STRIPE_WEBHOOK_SECRET);
    const sql = db();
    await sql`
      INSERT INTO webhook_events (provider, provider_event_id, payload)
      VALUES ('stripe', ${event.id}, ${sql.json(toJson(event))})
      ON CONFLICT (provider, provider_event_id) DO NOTHING
    `;

    if (event.type === "checkout.session.completed") {
      await recordStripeCheckoutSession(event.id, event.created, event.data.object);
    }
    if (event.type === "customer.subscription.created" || event.type === "customer.subscription.updated" || event.type === "customer.subscription.deleted") {
      await updateGeneratedAppSubscriptionEntitlement(event.data.object);
    }

    await sql`
      UPDATE webhook_events
      SET processed_at = now()
      WHERE provider = 'stripe'
        AND provider_event_id = ${event.id}
    `;

    return jsonOk({ ok: true, received: true });
  } catch (error) {
    return jsonError(error);
  }
}
