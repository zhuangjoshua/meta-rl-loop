import { TakyonCompanies } from "@/components/takyon/TakyonCompanies";
import { TakyonOnboarding } from "@/components/takyon/TakyonOnboarding";
import { requireProfile } from "@/lib/auth";
import { listTakyonCompanies } from "@/lib/takyon-dashboard";

export const dynamic = "force-dynamic";

export default async function TakyonCompaniesPage() {
  const profile = await requireProfile();
  const companies = await listTakyonCompanies(profile.id);

  if (companies.length === 0) {
    return <TakyonOnboarding />;
  }

  return <TakyonCompanies companies={companies} />;
}
