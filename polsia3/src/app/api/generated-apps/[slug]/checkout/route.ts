import { NextRequest, NextResponse } from "next/server";
import { consumeRateLimit, envInt, requestIpBucket } from "@/lib/abuse-protection";
import { getPublicSite } from "@/lib/companies";
import { NotFoundError } from "@/lib/errors";
import { createGeneratedAppCheckoutSession } from "@/lib/generated-apps/commerce";
import { observedRequest } from "@/lib/observability";

export async function GET(request: NextRequest, { params }: { params: Promise<{ slug: string }> }) {
  return observedRequest({ request, route: "/api/generated-apps/[slug]/checkout", action: "generated_app.checkout" }, async (observation) => {
    const { slug } = await params;
    const planKey = request.nextUrl.searchParams.get("plan") || "starter";
    const rawCampaignId = request.nextUrl.searchParams.get("campaign") || request.nextUrl.searchParams.get("campaign_id") || "";
    const campaignId = /^[0-9a-f-]{36}$/i.test(rawCampaignId) ? rawCampaignId : null;
    const site = await getPublicSite(slug);
    if (!site) throw new NotFoundError("Generated app not found.");
    observation.set({ businessId: site.business_id, metadata: { slug, planKey, campaignId } });
    await consumeRateLimit({
      action: "generated_app.checkout.ip.hour",
      bucketKey: `${site.business_id}:${requestIpBucket(request)}`,
      businessId: site.business_id,
      limit: envInt("TAKYON_CHECKOUTS_PER_IP_HOUR", 20),
      windowSeconds: 60 * 60,
      message: "Too many checkout attempts. Try again later."
    });
    const checkout = await createGeneratedAppCheckoutSession({ slug, planKey, campaignId });
    return NextResponse.redirect(checkout.checkoutUrl);
  });
}
