import { NextResponse } from "next/server";
import { getPublicSite } from "@/lib/companies";
import { UnauthorizedError, NotFoundError } from "@/lib/errors";
import { currentGeneratedAppSession } from "@/lib/generated-app-auth";
import { createGeneratedAppBillingPortalSession } from "@/lib/generated-apps/customer-ops";
import { generatedAppPublicUrl } from "@/lib/generated-app-url";
import { observedRequest } from "@/lib/observability";

export async function GET(request: Request, context: { params: Promise<{ slug: string }> }) {
  const { slug } = await context.params;
  return observedRequest({ request, route: "/api/generated-apps/[slug]/billing/portal", action: "generated_app.billing_portal" }, async (observation) => {
    const site = await getPublicSite(slug);
    if (!site) throw new NotFoundError("Generated app not found.");
    const session = await currentGeneratedAppSession(site.business_id);
    if (!session) throw new UnauthorizedError("Sign in before opening the billing portal.");
    observation.set({ businessId: site.business_id, appUserId: session.appUserId });
    const portal = await createGeneratedAppBillingPortalSession({
      businessId: site.business_id,
      appUserId: session.appUserId,
      returnUrl: generatedAppPublicUrl(site.slug, "/account")
    });
    return NextResponse.redirect(portal.url);
  });
}
