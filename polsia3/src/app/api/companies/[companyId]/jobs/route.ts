import { requireProfileForApi } from "@/lib/auth";
import { getCompanyForProfile } from "@/lib/companies";
import { NotFoundError } from "@/lib/errors";
import { jsonError, jsonOk } from "@/lib/http";
import { listWorkflowJobs } from "@/lib/workflow-jobs";

export async function GET(_: Request, { params }: { params: Promise<{ companyId: string }> }) {
  try {
    const profile = await requireProfileForApi();
    const { companyId } = await params;
    const company = await getCompanyForProfile(companyId, profile.id);
    if (!company) throw new NotFoundError("Company not found.");
    const jobs = await listWorkflowJobs(companyId);
    return jsonOk({ ok: true, jobs });
  } catch (error) {
    return jsonError(error);
  }
}
