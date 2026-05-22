import { NextResponse } from "next/server";
import { z } from "zod";
import { requireProfile } from "@/lib/auth";
import { createCompany } from "@/lib/companies";
import { createTask } from "@/lib/tasks";
import { enqueueBuildCompanyPlan } from "@/lib/workflow-jobs";

const startTakyonCompanySchema = z.object({
  businessName: z.preprocess((value) => (value === null || value === "" ? undefined : value), z.string().trim().max(120).optional()),
  businessIdea: z.string().trim().min(1).max(8000),
  template: z.preprocess((value) => (value === null || value === "" ? undefined : value), z.string().trim().max(80).optional())
});

function seedNameFromIdea(idea: string) {
  const words = idea
    .replace(/[^a-zA-Z0-9 ]/g, " ")
    .split(/\s+/)
    .filter((word) => word.length > 2)
    .slice(0, 4);
  const title = words.map((word) => word[0].toUpperCase() + word.slice(1).toLowerCase()).join(" ");
  return title || "New Company";
}

export async function POST(request: Request) {
  const form = await request.formData();
  const parsed = startTakyonCompanySchema.safeParse({
    businessName: form.get("businessName"),
    businessIdea: form.get("businessIdea"),
    template: form.get("template")
  });

  if (!parsed.success || parsed.data.businessIdea.length < 8) {
    return NextResponse.redirect(new URL("/new/takyon", request.url), 303);
  }

  const profile = await requireProfile();
  const idea = parsed.data.businessIdea;
  const body = {
    name: parsed.data.businessName || seedNameFromIdea(idea),
    pitch: idea,
    customer: "",
    pain: "",
    offer: "",
    template: parsed.data.template ?? ""
  };

  const { company } = await createCompany(body, profile);
  const task = await createTask({
    companyId: company.id,
    profileId: profile.id,
    title: `Build ${company.name}`,
    description: body.pitch,
    category: "build_company",
    priority: 100
  });
  await enqueueBuildCompanyPlan({
    companyId: company.id,
    profileId: profile.id,
    taskId: task.id,
    brief: body
  });

  return NextResponse.redirect(new URL(`/?business=${encodeURIComponent(company.slug)}&queued=1`, request.url), 303);
}
