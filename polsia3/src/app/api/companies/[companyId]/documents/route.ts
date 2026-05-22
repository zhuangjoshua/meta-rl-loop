import { requireProfileForApi } from "@/lib/auth";
import { getCompanyForProfile } from "@/lib/companies";
import { BadRequestError, NotFoundError } from "@/lib/errors";
import { jsonError, jsonOk } from "@/lib/http";
import { listBusinessDocuments, upsertBusinessDocument } from "@/lib/documents";
import { z } from "zod";

const documentSchema = z.object({
  title: z.string().trim().min(1).max(160),
  content: z.string().min(1),
  kind: z.enum(["mission", "research_report", "daily_report", "task_report", "website_brief", "document"]).default("document"),
  source: z.enum(["agent", "workflow", "system", "operator"]).default("operator"),
  metadata: z.record(z.string(), z.unknown()).default({})
});

export async function GET(_: Request, { params }: { params: Promise<{ companyId: string }> }) {
  try {
    const profile = await requireProfileForApi();
    const { companyId } = await params;
    const company = await getCompanyForProfile(companyId, profile.id);
    if (!company) throw new NotFoundError("Company not found.");
    const documents = await listBusinessDocuments(companyId);
    return jsonOk({ ok: true, documents });
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
    const parsed = documentSchema.safeParse(await request.json());
    if (!parsed.success) throw new BadRequestError("Invalid document payload.");
    const document = await upsertBusinessDocument({ companyId, ...parsed.data });
    return jsonOk({ ok: true, document });
  } catch (error) {
    return jsonError(error);
  }
}
