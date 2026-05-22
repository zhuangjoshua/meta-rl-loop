import { redirect } from "next/navigation";
import { auth0 } from "./auth0";
import { db } from "./db";
import { ForbiddenError, UnauthorizedError } from "./errors";
import { getAuth0Env, getLocalAuthSeed, localAuthBypassEnabled } from "./env";

export type Profile = {
  id: string;
  auth_provider: string;
  auth_subject: string;
  email: string;
  name: string | null;
};

export async function upsertProfile(input: {
  authProvider: string;
  authSubject: string;
  email: string;
  name?: string | null;
}) {
  const sql = db();
  const rows = await sql<Profile[]>`
    INSERT INTO profiles (auth_provider, auth_subject, email, name)
    VALUES (${input.authProvider}, ${input.authSubject}, ${input.email}, ${input.name ?? null})
    ON CONFLICT (auth_provider, auth_subject)
    DO UPDATE SET email = EXCLUDED.email, name = EXCLUDED.name
    RETURNING id, auth_provider, auth_subject, email, name
  `;

  return rows[0];
}

async function currentProfile() {
  if (localAuthBypassEnabled()) {
    const seed = getLocalAuthSeed();
    return upsertProfile({
      authProvider: "local-dev",
      authSubject: seed.subject,
      email: seed.email,
      name: seed.name
    });
  }

  getAuth0Env();
  const session = await auth0.getSession();
  if (!session) return null;

  const subject = session.user.sub;
  const email = session.user.email;
  if (!subject || !email) {
    throw new UnauthorizedError("Signed-in Auth0 user is missing subject or email.");
  }

  const allowedDomains = (process.env.ARGON_BETA_ALLOWED_EMAIL_DOMAINS || "")
    .split(",")
    .map((domain) => domain.trim().toLowerCase())
    .filter(Boolean);
  const emailDomain = email.split("@")[1]?.toLowerCase() || "";
  if (allowedDomains.length > 0 && !allowedDomains.includes(emailDomain)) {
    throw new ForbiddenError(`This beta is limited to ${allowedDomains.map((domain) => `@${domain}`).join(", ")} emails.`);
  }

  return upsertProfile({
    authProvider: "auth0",
    authSubject: subject,
    email,
    name: session.user.name ?? session.user.nickname ?? null
  });
}

export async function requireProfile() {
  const profile = await currentProfile();
  if (!profile) redirect("/auth/login");
  return profile;
}

export async function requireProfileForApi() {
  const profile = await currentProfile();
  if (!profile) throw new UnauthorizedError();
  return profile;
}
