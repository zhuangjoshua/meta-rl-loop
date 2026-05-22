import { getPublicSite } from "@/lib/companies";
import { NotFoundError } from "@/lib/errors";
import { currentGeneratedAppSession } from "@/lib/generated-app-auth";
import { jsonOk } from "@/lib/http";
import { observedRequest } from "@/lib/observability";

type RouteContext = {
  params: Promise<{ slug: string }>;
};

export async function GET(request: Request, context: RouteContext) {
  return observedRequest({ request, route: "/api/generated-apps/[slug]/session", action: "generated_app.session" }, async (observation) => {
    const { slug } = await context.params;
    const site = await getPublicSite(slug);
    if (!site) throw new NotFoundError("Generated app not found.");
    observation.set({ businessId: site.business_id, metadata: { slug } });
    const session = await currentGeneratedAppSession(site.business_id);
    if (session) observation.set({ appUserId: session.appUserId });
    return jsonOk({
      ok: true,
      signedIn: Boolean(session),
      email: session?.email ?? null,
      tier: session?.tier ?? "anonymous",
      appUserKey: session?.appUserId ?? null
    });
  });
}
