import { NextRequest } from "next/server";
import { requireProfileForApi } from "@/lib/auth";
import { getCompanyForProfile } from "@/lib/companies";
import { ForbiddenError } from "@/lib/errors";
import { getXCurrentUser } from "@/lib/vendors/x";
import { jsonError, jsonOk } from "@/lib/http";

export async function GET(request: NextRequest) {
  try {
    const profile = await requireProfileForApi();
    const businessId = request.nextUrl.searchParams.get("businessId")?.trim() || "";
    if (businessId) {
      const company = await getCompanyForProfile(businessId, profile.id);
      if (!company) throw new ForbiddenError("You do not have access to this business.");
    }
    const user = await getXCurrentUser(undefined, businessId ? { businessId } : { profileId: profile.id });
    return jsonOk({ ok: true, user: user.response });
  } catch (error) {
    return jsonError(error);
  }
}
