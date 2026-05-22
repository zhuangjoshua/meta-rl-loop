import Stripe from "stripe";
import { getStripeEnv } from "../env";

export function stripeClient() {
  return new Stripe(getStripeEnv().STRIPE_SECRET_KEY);
}
