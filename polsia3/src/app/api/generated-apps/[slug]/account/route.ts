import { getPublicSite } from "@/lib/companies";
import { UnauthorizedError, NotFoundError } from "@/lib/errors";
import { currentGeneratedAppSession } from "@/lib/generated-app-auth";
import { getGeneratedAppCustomerAccount } from "@/lib/generated-apps/customer-ops";
import { jsonOk } from "@/lib/http";
import { observedRequest } from "@/lib/observability";

export async function GET(request: Request, context: { params: Promise<{ slug: string }> }) {
  const { slug } = await context.params;
  return observedRequest({ request, route: "/api/generated-apps/[slug]/account", action: "generated_app.account" }, async (observation) => {
    const site = await getPublicSite(slug);
    if (!site) throw new NotFoundError("Generated app not found.");
    const session = await currentGeneratedAppSession(site.business_id);
    if (!session) throw new UnauthorizedError("Sign in to view this account.");
    observation.set({ businessId: site.business_id, appUserId: session.appUserId });
    const account = await getGeneratedAppCustomerAccount({
      businessId: site.business_id,
      appUserId: session.appUserId
    });
    return jsonOk({ ok: true, account });
  });
}
