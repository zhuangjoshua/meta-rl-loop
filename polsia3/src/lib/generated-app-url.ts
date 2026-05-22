import { getAppEnv } from "./env";

function normalizedBaseDomain() {
  const baseDomain = getAppEnv().PUBLIC_COMPANY_BASE_DOMAIN.trim().replace(/^https?:\/\//, "").replace(/\/$/, "");
  if (!baseDomain || baseDomain.includes("localhost") || baseDomain.includes("127.0.0.1")) return null;
  return baseDomain;
}

export function generatedAppPublicUrl(siteSlug: string, pathSuffix = "") {
  const baseDomain = normalizedBaseDomain();
  if (baseDomain) return `https://${siteSlug}.${baseDomain}${pathSuffix}`;
  return `${getAppEnv().APP_URL}/c/${siteSlug}${pathSuffix}`;
}
