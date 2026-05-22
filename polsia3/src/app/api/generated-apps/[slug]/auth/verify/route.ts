import { NextResponse } from "next/server";
import { consumeRateLimit, envInt, requestIpBucket } from "@/lib/abuse-protection";
import { generatedAppPublicUrl } from "@/lib/generated-app-url";
import { generatedAppSessionCookieName, verifyGeneratedAppMagicLink } from "@/lib/generated-app-auth";
import { observedRequest } from "@/lib/observability";

type RouteContext = {
  params: Promise<{ slug: string }>;
};

export async function GET(request: Request, context: RouteContext) {
  return observedRequest(
    { request, route: "/api/generated-apps/[slug]/auth/verify", action: "generated_app.auth.verify" },
    async (observation) => {
      const { slug } = await context.params;
      observation.set({ metadata: { slug } });
      await consumeRateLimit({
        action: "generated_app.magic_link.verify.ip.hour",
        bucketKey: `${slug}:${requestIpBucket(request)}`,
        limit: envInt("TAKYON_MAGIC_VERIFY_PER_IP_HOUR", 60),
        windowSeconds: 60 * 60,
        message: "Too many sign-in attempts. Try again later."
      });
      const url = new URL(request.url);
      const token = url.searchParams.get("token") || "";
      const verified = await verifyGeneratedAppMagicLink({ slug, token });
      observation.set({ businessId: verified.businessId });
      const canonicalUrl = new URL(generatedAppPublicUrl(verified.slug));
      const redirectUrl = new URL(url.host === canonicalUrl.host ? "/" : canonicalUrl.toString());
      const response = NextResponse.redirect(redirectUrl);

      response.cookies.set(generatedAppSessionCookieName, verified.sessionToken, {
        httpOnly: true,
        secure: url.protocol === "https:",
        sameSite: "lax",
        path: "/",
        maxAge: 60 * 60 * 24 * 30
      });

      return response;
    }
  );
}
