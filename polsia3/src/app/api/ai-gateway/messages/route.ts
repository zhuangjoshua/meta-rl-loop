import { z } from "zod";
import { consumeRateLimits, envInt, projectKeyBucket } from "@/lib/abuse-protection";
import { executeAiProvider } from "@/lib/ai-provider";
import { UnauthorizedError } from "@/lib/errors";
import { jsonOk } from "@/lib/http";
import { hashObservabilityValue, observedRequest } from "@/lib/observability";
import { completeProjectAiUsage, failProjectAiUsage, reserveProjectAiUsage } from "@/lib/project-ai";
import { verifyProjectAiKey } from "@/lib/generated-apps/records";

const messageSchema = z.object({
  purpose: z.string().trim().min(1).max(80),
  route: z.string().trim().min(1).max(200),
  appUserTier: z.string().trim().min(1).max(80),
  appUserKey: z.string().trim().min(1).max(200),
  messages: z.array(z.object({ role: z.enum(["system", "user", "assistant"]), content: z.string().min(1).max(8000) })).min(1).max(12)
});

function bearerToken(request: Request) {
  const header = request.headers.get("authorization") || "";
  if (!header.startsWith("Bearer ")) throw new UnauthorizedError("Missing project key.");
  return header.slice("Bearer ".length).trim();
}

export async function POST(request: Request) {
  return observedRequest({ request, route: "/api/ai-gateway/messages", action: "ai_gateway.messages" }, async (observation) => {
    const token = bearerToken(request);
    const key = await verifyProjectAiKey(token);
    if (!key) throw new UnauthorizedError("Invalid project key.");
    observation.set({ businessId: key.business_id, metadata: { projectKeyId: key.id } });
    const body = messageSchema.parse(await request.json());
    observation.set({ metadata: { purpose: body.purpose, route: body.route, appUserKey: hashObservabilityValue("app_user_key", body.appUserKey) } });
    await consumeRateLimits([
      {
        action: "ai_gateway.key.minute",
        bucketKey: projectKeyBucket(key.id),
        businessId: key.business_id,
        limit: envInt("TAKYON_AI_GATEWAY_PER_KEY_MINUTE", 60),
        windowSeconds: 60,
        message: "AI gateway rate limit reached. Try again shortly."
      },
      {
        action: "ai_gateway.app_user.hour",
        bucketKey: `${key.business_id}:${hashObservabilityValue("app_user_key", body.appUserKey)}`,
        businessId: key.business_id,
        limit: envInt("TAKYON_AI_GATEWAY_PER_APP_USER_HOUR", 120),
        windowSeconds: 60 * 60,
        message: "Too many AI requests for this user. Try again later."
      }
    ]);

    const reservation = await reserveProjectAiUsage({
      businessId: key.business_id,
      proxyKeyId: key.id,
      purpose: body.purpose,
      route: body.route,
      appUserKey: body.appUserKey,
      appUserTier: body.appUserTier,
      metadata: { message_count: body.messages.length }
    });

    try {
      const provider = await executeAiProvider({
        provider: reservation.provider,
        model: reservation.model,
        maxOutputTokens: reservation.maxOutputTokens,
        messages: body.messages
      });
      const cost = await completeProjectAiUsage({
        usageEventId: reservation.usageEventId,
        provider: reservation.provider,
        inputTokens: provider.inputTokens,
        outputTokens: provider.outputTokens,
        providerRequestId: provider.providerRequestId
      });

      return jsonOk({
        ok: true,
        status: "completed",
        text: provider.text,
        provider: reservation.provider,
        model: reservation.model,
        usage: {
          inputTokens: provider.inputTokens,
          outputTokens: provider.outputTokens
        },
        cost
      });
    } catch (providerError) {
      await failProjectAiUsage(reservation.usageEventId, providerError);
      throw providerError;
    }
  });
}
