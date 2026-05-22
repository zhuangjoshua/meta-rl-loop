import { createHash, randomBytes } from "node:crypto";
import { NextRequest, NextResponse } from "next/server";
import { requireProfileForApi } from "@/lib/auth";
import { getCompanyForProfile } from "@/lib/companies";
import { getXClientEnv } from "@/lib/env";
import { ForbiddenError } from "@/lib/errors";

function base64Url(buffer: Buffer) {
  return buffer.toString("base64").replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/g, "");
}

function callbackUrl(request: NextRequest) {
  return new URL("/api/integrations/x/oauth/callback", request.url).toString();
}

export async function GET(request: NextRequest) {
  const profile = await requireProfileForApi();
  const env = getXClientEnv();
  const state = base64Url(randomBytes(24));
  const verifier = base64Url(randomBytes(48));
  const challenge = base64Url(createHash("sha256").update(verifier).digest());
  const returnTo = request.nextUrl.searchParams.get("returnTo") || "/dashboard";
  const businessId = request.nextUrl.searchParams.get("businessId")?.trim() || "";
  const scopeParam = request.nextUrl.searchParams.get("scope")?.trim() || "";
  const scopeType = scopeParam === "platform" ? "platform" : businessId ? "business" : "profile";

  if (businessId) {
    const company = await getCompanyForProfile(businessId, profile.id);
    if (!company) throw new ForbiddenError("You do not have access to this business.");
  }

  const authorizeUrl = new URL("https://twitter.com/i/oauth2/authorize");
  authorizeUrl.searchParams.set("response_type", "code");
  authorizeUrl.searchParams.set("client_id", env.X_CLIENT_ID);
  authorizeUrl.searchParams.set("redirect_uri", callbackUrl(request));
  authorizeUrl.searchParams.set("scope", "tweet.read tweet.write users.read offline.access");
  authorizeUrl.searchParams.set("state", state);
  authorizeUrl.searchParams.set("code_challenge", challenge);
  authorizeUrl.searchParams.set("code_challenge_method", "S256");

  const response = NextResponse.redirect(authorizeUrl);
  const secure = request.nextUrl.protocol === "https:";
  response.cookies.set("x_oauth_state", state, { httpOnly: true, sameSite: "lax", secure, path: "/", maxAge: 600 });
  response.cookies.set("x_oauth_verifier", verifier, { httpOnly: true, sameSite: "lax", secure, path: "/", maxAge: 600 });
  response.cookies.set("x_oauth_return_to", returnTo.startsWith("/") ? returnTo : "/dashboard", {
    httpOnly: true,
    sameSite: "lax",
    secure,
    path: "/",
    maxAge: 600
  });
  response.cookies.set("x_oauth_scope_type", scopeType, { httpOnly: true, sameSite: "lax", secure, path: "/", maxAge: 600 });
  response.cookies.set("x_oauth_business_id", businessId, { httpOnly: true, sameSite: "lax", secure, path: "/", maxAge: 600 });
  response.cookies.set("x_oauth_profile_id", profile.id, { httpOnly: true, sameSite: "lax", secure, path: "/", maxAge: 600 });
  return response;
}
