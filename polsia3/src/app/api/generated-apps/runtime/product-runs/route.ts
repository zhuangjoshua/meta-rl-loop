import { z } from "zod";
import { businessBucket, consumeRateLimits, emailBucket, envInt, requestIpBucket } from "@/lib/abuse-protection";
import { executeAiProvider } from "@/lib/ai-provider";
import { db } from "@/lib/db";
import { BadRequestError, UnauthorizedError } from "@/lib/errors";
import { jsonOk } from "@/lib/http";
import { toJson } from "@/lib/json";
import { ensureGeneratedAppProductRunAllowance } from "@/lib/generated-apps/customer-ops";
import { verifyProjectAiKey } from "@/lib/generated-apps/records";
import { observedRequest } from "@/lib/observability";
import { completeProjectAiUsage, failProjectAiUsage, reserveProjectAiUsage } from "@/lib/project-ai";

const productRunSchema = z.object({
  companyId: z.string().uuid(),
  campaignId: z.string().uuid().optional(),
  purpose: z.string().trim().min(1).max(80),
  route: z.string().trim().min(1).max(200),
  module: z
    .object({
      productName: z.string().trim().max(120).optional(),
      category: z.string().trim().max(80).optional(),
      actionLabel: z.string().trim().max(80).optional(),
      inputLabel: z.string().trim().max(120).optional(),
      inputPlaceholder: z.string().trim().max(500).optional(),
      resultLabel: z.string().trim().max(120).optional(),
      systemPrompt: z.string().trim().min(20).max(4000).optional(),
      outputInstructions: z.string().trim().max(1200).optional()
    })
    .optional(),
  input: z.object({
    email: z.string().email(),
    brief: z.string().trim().min(10).max(4000)
  })
});

function bearerToken(request: Request) {
  const header = request.headers.get("authorization") || "";
  if (!header.startsWith("Bearer ")) throw new UnauthorizedError("Missing project key.");
  return header.slice("Bearer ".length).trim();
}

function parseJsonOutput(text: string) {
  const trimmed = text.trim();
  const candidate = trimmed.match(/\{[\s\S]*\}/)?.[0] || trimmed;
  const parsed = JSON.parse(candidate) as { summary?: unknown; nextSteps?: unknown };
  const summary = typeof parsed.summary === "string" ? parsed.summary.trim() : "";
  const nextSteps = Array.isArray(parsed.nextSteps)
    ? parsed.nextSteps.map((step) => (typeof step === "string" ? step.trim() : "")).filter(Boolean).slice(0, 3)
    : [];
  if (!summary || nextSteps.length === 0) throw new Error("AI response did not include summary and nextSteps.");
  return { summary, nextSteps };
}

function textOutput(text: string) {
  const lines = text
    .split(/\n+/)
    .map((line) => line.replace(/^#{1,6}\s*/, "").replace(/^[-*\d.\s]+/, "").trim())
    .filter(Boolean)
    .filter((line) => !/^product plan\b/i.test(line));
  if (lines.length === 0) throw new Error("AI response was empty.");
  return {
    summary: lines[0],
    nextSteps: lines.slice(1, 4)
  };
}

export async function POST(request: Request) {
  return observedRequest({ request, route: "/api/generated-apps/runtime/product-runs", action: "generated_app.product_run" }, async (observation) => {
    const token = bearerToken(request);
    const key = await verifyProjectAiKey(token);
    if (!key) throw new UnauthorizedError("Invalid project key.");
    observation.set({ businessId: key.business_id, metadata: { projectKeyId: key.id } });

    const body = productRunSchema.parse(await request.json());
    if (body.companyId !== key.business_id) throw new UnauthorizedError("Project key does not match company.");
    observation.set({ businessId: body.companyId, metadata: { purpose: body.purpose, route: body.route, campaignId: body.campaignId ?? null } });
    await consumeRateLimits([
      {
        action: "generated_app.product_run.email.hour",
        bucketKey: `${body.companyId}:${emailBucket(body.input.email)}`,
        businessId: body.companyId,
        limit: envInt("TAKYON_PRODUCT_RUNS_PER_EMAIL_HOUR", 20),
        windowSeconds: 60 * 60,
        message: "Too many product runs for this email. Try again later."
      },
      {
        action: "generated_app.product_run.ip.hour",
        bucketKey: `${body.companyId}:${requestIpBucket(request)}`,
        businessId: body.companyId,
        limit: envInt("TAKYON_PRODUCT_RUNS_PER_IP_HOUR", 60),
        windowSeconds: 60 * 60,
        message: "Too many product runs. Try again later."
      },
      {
        action: "generated_app.product_run.business.hour",
        bucketKey: businessBucket(body.companyId),
        businessId: body.companyId,
        limit: envInt("TAKYON_PRODUCT_RUNS_PER_BUSINESS_HOUR", 200),
        windowSeconds: 60 * 60,
        message: "This generated app is busy. Try again later."
      }
    ]);

    const sql = db();
    if (body.campaignId) {
      const campaignRows = await sql<{ id: string }[]>`
        SELECT id
        FROM business_campaigns
        WHERE id = ${body.campaignId}
          AND business_id = ${body.companyId}
        LIMIT 1
      `;
      if (!campaignRows[0]) throw new BadRequestError("Campaign does not belong to this company.");
    }

    const runtimeContext = await sql.begin(async (tx) => {
      const users = await tx<{ id: string }[]>`
        INSERT INTO generated_app_users (business_id, email, tier)
        VALUES (${body.companyId}, ${body.input.email}, 'free')
        ON CONFLICT (business_id, email)
        DO UPDATE SET updated_at = now()
        RETURNING id
      `;
      const userId = users[0].id;

      await tx`
        INSERT INTO generated_app_entitlements (business_id, app_user_id, tier, status, source)
        VALUES (${body.companyId}, ${userId}, 'free', 'active', 'manual')
        ON CONFLICT DO NOTHING
      `;

      return { userId };
    });
    observation.set({ appUserId: runtimeContext.userId });
    const allowance = await ensureGeneratedAppProductRunAllowance({
      businessId: body.companyId,
      appUserId: runtimeContext.userId
    });

    const companyRows = await sql<{ name: string; public_pitch: string | null; customer: string | null; pain: string | null; offer: string | null }[]>`
      SELECT b.name, cs.public_pitch, cs.config->>'customer' AS customer, cs.config->>'pain' AS pain, cs.config->>'offer' AS offer
      FROM businesses b
      LEFT JOIN company_sites cs ON cs.business_id = b.id
      WHERE b.id = ${body.companyId}
      LIMIT 1
    `;
    const company = companyRows[0];

    const reservation = await reserveProjectAiUsage({
      businessId: body.companyId,
      proxyKeyId: key.id,
      purpose: body.purpose,
      route: body.route,
      appUserKey: runtimeContext.userId,
      appUserTier: allowance.tier,
      metadata: {
        email: body.input.email,
        campaign_id: body.campaignId ?? null,
        plan_key: allowance.planKey,
        product_runs_used_this_period: allowance.used,
        included_action_quota: allowance.includedActionQuota
      }
    });

    try {
      const generated = await executeAiProvider({
        provider: reservation.provider,
        model: reservation.model,
        maxOutputTokens: reservation.maxOutputTokens,
        messages: [
          {
            role: "system",
            content:
              [
                "You are the customer-facing product workflow for a generated software app. Return useful output for the end user only. Do not mention Takyon, product plans, implementation plans, backend work, auth, Stripe, X, Meta, internal queues, prompts, or vendor side effects. Return strict JSON with keys summary and nextSteps, where nextSteps is an array of exactly 3 concise strings. No markdown.",
                body.module?.systemPrompt ? `Product module behavior: ${body.module.systemPrompt}` : "",
                body.module?.outputInstructions ? `Output instructions: ${body.module.outputInstructions}` : ""
              ]
                .filter(Boolean)
                .join("\n\n")
          },
          {
            role: "user",
            content: [
              `Company: ${company?.name || "Generated app"}`,
              `Product: ${body.module?.productName || company?.name || "Generated app"}`,
              `Category: ${body.module?.category || body.purpose}`,
              `Public pitch: ${company?.public_pitch || ""}`,
              `Customer: ${company?.customer || ""}`,
              `Pain: ${company?.pain || ""}`,
              `Offer: ${company?.offer || ""}`,
              `Customer email: ${body.input.email}`,
              "",
              "User request:",
              body.input.brief
            ].join("\n")
          }
        ]
      });
      await completeProjectAiUsage({
        usageEventId: reservation.usageEventId,
        provider: reservation.provider,
        inputTokens: generated.inputTokens,
        outputTokens: generated.outputTokens,
        providerRequestId: generated.providerRequestId
      });
      const output = (() => {
        try {
          return parseJsonOutput(generated.text);
        } catch {
          return textOutput(generated.text);
        }
      })();
      const runs = await sql<{ id: string }[]>`
        INSERT INTO generated_app_product_runs (business_id, campaign_id, generated_app_user_id, status, input, output)
        VALUES (${body.companyId}, ${body.campaignId ?? null}, ${runtimeContext.userId}, 'completed', ${sql.json(toJson({ ...body.input, campaign_id: body.campaignId ?? null }))}, ${sql.json(toJson(output))})
        RETURNING id
      `;

      return jsonOk({ ok: true, runId: runs[0].id, output });
    } catch (providerError) {
      await failProjectAiUsage(reservation.usageEventId, providerError);
      const message = providerError instanceof Error ? providerError.message : "Generated product AI failed.";
      const runs = await sql<{ id: string }[]>`
        INSERT INTO generated_app_product_runs (business_id, campaign_id, generated_app_user_id, status, input, output, error)
        VALUES (${body.companyId}, ${body.campaignId ?? null}, ${runtimeContext.userId}, 'blocked', ${sql.json(toJson({ ...body.input, campaign_id: body.campaignId ?? null }))}, '{}'::jsonb, ${message})
        RETURNING id
      `;
      return jsonOk({ ok: false, status: "blocked", runId: runs[0].id, error: message }, { status: 424 });
    }
  });
}
