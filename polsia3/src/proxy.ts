import { NextResponse } from "next/server";
import { auth0 } from "./lib/auth0";

const operatorSubdomains = new Set(["app", "www"]);
const publicPathPrefixes = [
  // Operational endpoints remain non-Auth0, but each has its own bearer/signature/project-key gate.
  "/api/health",
  "/api/cron/dispatch",
  "/api/webhooks/stripe",
  // Generated-app customer/runtime endpoints are not operator UI and must stay callable by public apps.
  "/api/generated-apps/",
  "/api/payment-links/",
  "/c/",
  // Auth0 SDK routes.
  "/auth/"
];
const generatedAppPlatformApiPrefixes = ["/api/generated-apps/", "/api/payment-links/"];
const operatorPathPrefixes = ["/dashboard", "/api/companies", "/api/workflow", "/api/actions"];

function isStaticAsset(pathname: string) {
  return (
    pathname.startsWith("/_next/") ||
    /\.(?:html?|css|js(?!on)|jpe?g|png|gif|svg|webp|ico|woff2?|ttf|map)$/i.test(pathname)
  );
}

function generatedAppSlugFromHost(hostHeader: string | null) {
  const host = (hostHeader || "").split(":")[0].toLowerCase();
  const baseDomain = (process.env.PUBLIC_COMPANY_BASE_DOMAIN || "fourmanifold.com").trim().toLowerCase();
  if (!host || !baseDomain || host === baseDomain || !host.endsWith(`.${baseDomain}`)) return null;
  const subdomain = host.slice(0, -`.${baseDomain}`.length);
  if (!subdomain || operatorSubdomains.has(subdomain)) return null;
  if (subdomain.includes(".")) return null;
  return subdomain;
}

export async function proxy(request: Request) {
  if (process.env.ARGON_LOCAL_AUTH_BYPASS === "1" || process.env.ARGON_LOCAL_AUTH_BYPASS === "true") {
    return NextResponse.next();
  }

  const url = new URL(request.url);
  const host = request.headers.get("host")?.split(":")[0].toLowerCase() || "";
  const baseDomain = (process.env.PUBLIC_COMPANY_BASE_DOMAIN || "fourmanifold.com").trim().toLowerCase();

  if (
    baseDomain &&
    host === baseDomain &&
    operatorPathPrefixes.some((prefix) => url.pathname === prefix || url.pathname.startsWith(`${prefix}/`))
  ) {
    const canonical = new URL(url);
    canonical.hostname = `app.${baseDomain}`;
    return NextResponse.redirect(canonical);
  }

  const generatedAppSlug = generatedAppSlugFromHost(request.headers.get("host"));
  if (generatedAppSlug && !generatedAppPlatformApiPrefixes.some((prefix) => url.pathname.startsWith(prefix))) {
    const rewrite = new URL(`/c/${generatedAppSlug}${url.pathname === "/" ? "" : url.pathname}`, url);
    rewrite.search = url.search;
    return NextResponse.rewrite(rewrite);
  }

  if (!generatedAppSlug && isStaticAsset(url.pathname)) return NextResponse.next();
  if (publicPathPrefixes.some((prefix) => url.pathname === prefix || url.pathname.startsWith(prefix))) {
    return NextResponse.next();
  }

  return auth0.middleware(request);
}

export const config = {
  matcher: ["/:path*"]
};
