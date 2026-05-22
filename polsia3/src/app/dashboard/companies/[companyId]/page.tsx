import { notFound } from "next/navigation";
import { TakyonBusinessWorkspace } from "@/components/takyon/TakyonBusinessWorkspace";
import { requireProfile } from "@/lib/auth";
import { getCompanyForProfile } from "@/lib/companies";
import { getTakyonDashboardModel } from "@/lib/takyon-dashboard";
import { endTakyonCompanyFromForm, sendTakyonCeoMessageFromForm, startTakyonLeverFromForm, updateTakyonDistributionPolicyFromForm } from "./actions";
import { LiveDashboardRefresher } from "./LiveDashboardRefresher";

type PageProps = {
  params: Promise<{ companyId: string }>;
  searchParams: Promise<Record<string, string | string[] | undefined>>;
};

export const dynamic = "force-dynamic";

export default async function CompanyPage({ params, searchParams }: PageProps) {
  const profile = await requireProfile();
  const [{ companyId }, query] = await Promise.all([params, searchParams]);
  const company = await getCompanyForProfile(companyId, profile.id);
  if (!company) notFound();

  const model = await getTakyonDashboardModel(companyId);
  const shouldRefresh = model.live || query.auto === "pipeline";

  return (
    <>
      <LiveDashboardRefresher enabled={shouldRefresh} />
      <TakyonBusinessWorkspace
        model={model}
        leverAction={startTakyonLeverFromForm.bind(null, companyId)}
        chatAction={sendTakyonCeoMessageFromForm.bind(null, companyId)}
        settingsAction={updateTakyonDistributionPolicyFromForm.bind(null, companyId)}
        endAction={endTakyonCompanyFromForm.bind(null, companyId)}
      />
    </>
  );
}
