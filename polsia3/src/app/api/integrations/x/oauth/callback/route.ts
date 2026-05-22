import { NextRequest, NextResponse } from "next/server";
import { requireProfileForApi } from "@/lib/auth";
import { getCompanyForProfile } from "@/lib/companies";
import { getXClientEnv } from "@/lib/env";
import { BadRequestError, ForbiddenError } from "@/lib/errors";
import { getXCurrentUser, persistXTokens } from "@/lib/vendors/x";

type XTokenResponse = {
  access_token?: string;
  refresh_token?: string;
  expires_in?: number;
  scope?: string;
  error?: string;
  error_description?: string;
};

async function readBody(response: Response) {
  const text = await response.text();
  if (!text) return null;
  try {
    return JSON.parse(text) as unknown;
  } catch {
    return { raw: text };
  }
}

function callbackUrl(request: NextRequest) {
  return new URL("/api/integrations/x/oauth/callback", request.url).toString();
}

async function exchangeCode(input: { code: string; verifier: string; redirectUri: string }) {
  const env = getXClientEnv();
  const credentials = Buffer.from(`${env.X_CLIENT_ID}:${env.X_CLIENT_SECRET}`).toString("base64");
  const response = await fetch("https://api.x.com/2/oauth2/token", {
    method: "POST",
    headers: {
      Authorization: `Basic ${credentials}`,
      "Content-Type": "application/x-www-form-urlencoded"
    },
    body: new URLSearchParams({
      grant_type: "authorization_code",
      code: input.code,
      redirect_uri: input.redirectUri,
      code_verifier: input.verifier,
      client_id: env.X_CLIENT_ID
    })
  });
  const parsed = (await readBody(response)) as XTokenResponse | null;
  if (!response.ok || !parsed?.access_token || !parsed?.refresh_token) {
    throw new Error(`X OAuth callback returned ${response.status}.`);
  }
  return parsed;
}

export async function GET(request: NextRequest) {
  const profile = await requireProfileForApi();
  const code = request.nextUrl.searchParams.get("code");
  const state = request.nextUrl.searchParams.get("state");
  const error = request.nextUrl.searchParams.get("error");
  const cookieState = request.cookies.get("x_oauth_state")?.value;
  const verifier = request.cookies.get("x_oauth_verifier")?.value;
  const returnTo = request.cookies.get("x_oauth_return_to")?.value || "/";
  const scopeType = request.cookies.get("x_oauth_scope_type")?.value || "profile";
  const businessId = request.cookies.get("x_oauth_business_id")?.value || "";
  const profileId = request.cookies.get("x_oauth_profile_id")?.value || profile.id;
  if (error) throw new BadRequestError(`X OAuth failed: ${error}`);
  if (!code || !state || !cookieState || !verifier || state !== cookieState) {
    throw new BadRequestError("X OAuth callback state was invalid or expired.");
  }

  if (profileId !== profile.id) throw new ForbiddenError("X OAuth callback profile did not match the signed-in user.");
  const context =
    scopeType === "platform"
      ? {}
      : scopeType === "business"
        ? { businessId }
        : { profileId: profile.id };
  if (scopeType === "business") {
    const company = await getCompanyForProfile(businessId, profile.id);
    if (!company) throw new ForbiddenError("You do not have access to this business.");
  }

  const token = await exchangeCode({ code, verifier, redirectUri: callbackUrl(request) });
  await persistXTokens({
    accessToken: token.access_token!,
    refreshToken: token.refresh_token!,
    expiresIn: token.expires_in,
    scope: token.scope,
    context
  });
  await getXCurrentUser(token.access_token, context);

  const redirectUrl = new URL(returnTo.startsWith("/") ? returnTo : "/", request.url);
  redirectUrl.searchParams.set("x", "connected");
  const response = NextResponse.redirect(redirectUrl);
  response.cookies.delete("x_oauth_state");
  response.cookies.delete("x_oauth_verifier");
  response.cookies.delete("x_oauth_return_to");
  response.cookies.delete("x_oauth_scope_type");
  response.cookies.delete("x_oauth_business_id");
  response.cookies.delete("x_oauth_profile_id");
  return response;
}
