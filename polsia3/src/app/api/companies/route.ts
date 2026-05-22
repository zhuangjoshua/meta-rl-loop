import { requireProfileForApi } from "@/lib/auth";
import { consumeRateLimit, envInt, profileBucket } from "@/lib/abuse-protection";
import { createCompany, createCompanySchema, listCompaniesForProfile } from "@/lib/companies";
import { jsonOk } from "@/lib/http";
import { observedRequest } from "@/lib/observability";
import { createTask } from "@/lib/tasks";
import { enqueueBusinessStartup } from "@/lib/workflow-jobs";

export async function GET(request: Request) {
  return observedRequest({ request, route: "/api/companies", action: "companies.list" }, async (observation) => {
    const profile = await requireProfileForApi();
    observation.set({ profileId: profile.id });
    const companies = await listCompaniesForProfile(profile.id);
    return jsonOk({ ok: true, companies });
  });
}

export async function POST(request: Request) {
  return observedRequest({ request, route: "/api/companies", action: "companies.create" }, async (observation) => {
    const profile = await requireProfileForApi();
    observation.set({ profileId: profile.id });
    await consumeRateLimit({
      action: "company.create.profile.hour",
      bucketKey: profileBucket(profile.id),
      profileId: profile.id,
      limit: envInt("TAKYON_COMPANY_CREATE_PER_PROFILE_HOUR", 8),
      windowSeconds: 60 * 60,
      message: "Too many company builds queued. Try again later."
    });

    const body = createCompanySchema.parse(await request.json());
    const { company, site } = await createCompany(body, profile);
    observation.set({ businessId: company.id, metadata: { companySlug: company.slug } });
    const task = await createTask({
      companyId: company.id,
      profileId: profile.id,
      title: `Build ${company.name}`,
      description: body.pitch,
      category: "build_company",
      priority: 100
    });
    const jobs = await enqueueBusinessStartup({
      companyId: company.id,
      profileId: profile.id,
      taskId: task.id,
      brief: body
    });

    return jsonOk({ ok: true, company, site, task, jobs }, { status: 201 });
  });
}
