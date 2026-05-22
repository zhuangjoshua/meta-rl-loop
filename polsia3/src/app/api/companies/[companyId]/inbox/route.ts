import { requireProfileForApi } from "@/lib/auth";
import { getCompanyForProfile } from "@/lib/companies";
import { BadRequestError, NotFoundError } from "@/lib/errors";
import { jsonError, jsonOk } from "@/lib/http";
import { createInboxMessage, listInboxMessages } from "@/lib/inbox";
import { z } from "zod";

const messageSchema = z.object({
  body: z.string().trim().min(1).max(8000),
  forwardToCeo: z.boolean().default(false)
});

export async function GET(_: Request, { params }: { params: Promise<{ companyId: string }> }) {
  try {
    const profile = await requireProfileForApi();
    const { companyId } = await params;
    const company = await getCompanyForProfile(companyId, profile.id);
    if (!company) throw new NotFoundError("Company not found.");
    const messages = await listInboxMessages(companyId);
    return jsonOk({ ok: true, messages });
  } catch (error) {
    return jsonError(error);
  }
}

export async function POST(request: Request, { params }: { params: Promise<{ companyId: string }> }) {
  try {
    const profile = await requireProfileForApi();
    const { companyId } = await params;
    const company = await getCompanyForProfile(companyId, profile.id);
    if (!company) throw new NotFoundError("Company not found.");
    const parsed = messageSchema.safeParse(await request.json());
    if (!parsed.success) throw new BadRequestError("Invalid message payload.");
    const message = await createInboxMessage({
      companyId,
      profileId: profile.id,
      authorLabel: profile.name || profile.email,
      body: parsed.data.body,
      source: "dashboard",
      forwardToCeo: parsed.data.forwardToCeo
    });
    return jsonOk({ ok: true, message });
  } catch (error) {
    return jsonError(error);
  }
}
