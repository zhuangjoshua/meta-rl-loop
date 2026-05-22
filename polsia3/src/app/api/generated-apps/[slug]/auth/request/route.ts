import { consumeRateLimits, emailBucket, envInt, requestIpBucket } from "@/lib/abuse-protection";
import { getPublicSite } from "@/lib/companies";
import { BadRequestError, NotFoundError } from "@/lib/errors";
import { requestGeneratedAppMagicLink } from "@/lib/generated-app-auth";
import { jsonOk } from "@/lib/http";
import { observedRequest } from "@/lib/observability";
import { z } from "zod";

type RouteContext = {
  params: Promise<{ slug: string }>;
};

const schema = z.object({
  email: z.string().trim().email()
});

export async function POST(request: Request, context: RouteContext) {
  return observedRequest(
    { request, route: "/api/generated-apps/[slug]/auth/request", action: "generated_app.auth.request" },
    async (observation) => {
      const { slug } = await context.params;
      const parsed = schema.safeParse(await request.json());
      if (!parsed.success) throw new BadRequestError("Enter a valid email.");
      const site = await getPublicSite(slug);
      if (!site) throw new NotFoundError("Generated app not found.");
      observation.set({ businessId: site.business_id, metadata: { slug } });
      await consumeRateLimits([
        {
          action: "generated_app.magic_link.email.hour",
          bucketKey: `${site.business_id}:${emailBucket(parsed.data.email)}`,
          businessId: site.business_id,
          limit: envInt("TAKYON_MAGIC_LINKS_PER_EMAIL_HOUR", 5),
          windowSeconds: 60 * 60,
          message: "Too many sign-in links requested for this email. Try again later."
        },
        {
          action: "generated_app.magic_link.ip.hour",
          bucketKey: `${site.business_id}:${requestIpBucket(request)}`,
          businessId: site.business_id,
          limit: envInt("TAKYON_MAGIC_LINKS_PER_IP_HOUR", 30),
          windowSeconds: 60 * 60,
          message: "Too many sign-in links requested. Try again later."
        }
      ]);
      const url = new URL(request.url);
      await requestGeneratedAppMagicLink({
        site,
        email: parsed.data.email,
        origin: url.origin
      });
      return jsonOk({ ok: true });
    }
  );
}
