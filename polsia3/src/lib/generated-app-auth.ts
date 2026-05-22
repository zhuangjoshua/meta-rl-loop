import { createHash, randomBytes } from "node:crypto";
import { cookies } from "next/headers";
import { db } from "./db";
import { getPostmarkEnv } from "./env";
import { BadRequestError, ForbiddenError, IntegrationCallError, NotFoundError } from "./errors";
import { toJson } from "./json";

export const generatedAppSessionCookieName = "argon_app_session";

type PublicSiteForAuth = {
  business_id: string;
  slug: string;
  public_title: string;
};

export type GeneratedAppSession = {
  sessionId: string;
  appUserId: string;
  email: string;
  tier: "free" | "paid" | "owner";
};

function hashToken(token: string) {
  return createHash("sha256").update(token).digest("hex");
}

function allowedDomains() {
  return (process.env.ARGON_BETA_ALLOWED_EMAIL_DOMAINS || "")
    .split(",")
    .map((value) => value.trim().toLowerCase())
    .filter(Boolean);
}

export function assertGeneratedAppEmailAllowed(email: string) {
  const normalized = email.trim().toLowerCase();
  const domain = normalized.split("@")[1] || "";
  const allowed = allowedDomains();
  if (allowed.length > 0 && !allowed.includes(domain)) {
    throw new ForbiddenError(`This beta is limited to ${allowed.map((item) => `@${item}`).join(", ")} emails.`);
  }
  return normalized;
}

async function sendPostmarkMagicLink(input: {
  to: string;
  from: string;
  productName: string;
  link: string;
}) {
  const response = await fetch("https://api.postmarkapp.com/email", {
    method: "POST",
    headers: {
      "X-Postmark-Server-Token": getPostmarkEnv().POSTMARK_SERVER_TOKEN,
      "Content-Type": "application/json",
      Accept: "application/json"
    },
    body: JSON.stringify({
      From: input.from,
      To: input.to,
      Subject: `Sign in to ${input.productName}`,
      TextBody: [
        `Use this secure link to sign in to ${input.productName}:`,
        "",
        input.link,
        "",
        "This link expires in 15 minutes and can be used once."
      ].join("\n"),
      HtmlBody: [
        `<p>Use this secure link to sign in to ${input.productName}:</p>`,
        `<p><a href="${input.link}">Sign in to ${input.productName}</a></p>`,
        `<p>This link expires in 15 minutes and can be used once.</p>`
      ].join("")
    }),
    signal: AbortSignal.timeout(30_000)
  });

  const body = (await response.json().catch(() => null)) as { MessageID?: string; Message?: string } | null;
  if (!response.ok) {
    throw new IntegrationCallError("Postmark", `${response.status} ${JSON.stringify(body)}`, response.status);
  }

  return body?.MessageID ?? null;
}

export async function requestGeneratedAppMagicLink(input: {
  site: PublicSiteForAuth;
  email: string;
  origin: string;
}) {
  const email = assertGeneratedAppEmailAllowed(input.email);
  const token = randomBytes(32).toString("base64url");
  const tokenHash = hashToken(token);
  const verifyUrl = new URL(`/api/generated-apps/${input.site.slug}/auth/verify`, input.origin);
  verifyUrl.searchParams.set("token", token);

  const sql = db();
  await sql`
    INSERT INTO generated_app_magic_links (business_id, email, token_hash, expires_at)
    VALUES (${input.site.business_id}, ${email}, ${tokenHash}, now() + interval '15 minutes')
  `;

  const fromEmail = getPostmarkEnv().POSTMARK_FROM_EMAIL;
  const providerMessageId = await sendPostmarkMagicLink({
    to: email,
    from: fromEmail,
    productName: input.site.public_title,
    link: verifyUrl.toString()
  });

  await sql`
    INSERT INTO business_email_messages (
      business_id,
      direction,
      from_email,
      to_email,
      subject,
      body_text,
      status,
      provider_message_id,
      provider,
      audience_type,
      metadata,
      sent_at
    )
    VALUES (
      ${input.site.business_id},
      'outbound',
      ${fromEmail},
      ${email},
      ${`Sign in to ${input.site.public_title}`},
      ${`Magic link sent for ${input.site.slug}`},
      'delivered',
      ${providerMessageId},
      'postmark',
      'transactional',
      ${sql.json(toJson({ kind: "generated_app_magic_link" }))},
      now()
    )
  `;
}

export async function verifyGeneratedAppMagicLink(input: { slug: string; token: string }) {
  if (!input.token.trim()) throw new BadRequestError("Missing magic link token.");
  const sql = db();
  const tokenHash = hashToken(input.token);

  const rows = await sql<{
    id: string;
    business_id: string;
    email: string | null;
    slug: string;
    generated_app_user_id: string | null;
  }[]>`
    SELECT m.id, m.business_id, m.email, s.slug, m.generated_app_user_id
    FROM generated_app_magic_links m
    JOIN company_sites s ON s.business_id = m.business_id
    WHERE s.slug = ${input.slug}
      AND m.token_hash = ${tokenHash}
      AND COALESCE(m.used_at, m.consumed_at) IS NULL
      AND m.expires_at > now()
    LIMIT 1
  `;
  const link = rows[0];
  if (!link) throw new ForbiddenError("This magic link is invalid or expired.");
  if (!link.email && !link.generated_app_user_id) throw new NotFoundError("Magic link user is missing.");

  const sessionToken = randomBytes(32).toString("base64url");
  const sessionHash = hashToken(sessionToken);

  await sql.begin(async (tx) => {
    await tx`
      UPDATE generated_app_magic_links
      SET used_at = now(),
          consumed_at = now()
      WHERE id = ${link.id}
    `;

    const users = link.email
      ? await tx<{ id: string }[]>`
          INSERT INTO generated_app_users (business_id, email)
          VALUES (${link.business_id}, ${link.email})
          ON CONFLICT (business_id, email)
          DO UPDATE SET status = 'active'
          RETURNING id
        `
      : await tx<{ id: string }[]>`
          SELECT id
          FROM generated_app_users
          WHERE id = ${link.generated_app_user_id}
            AND business_id = ${link.business_id}
          LIMIT 1
        `;
    const userId = users[0]?.id;
    if (!userId) throw new NotFoundError("Generated app user not found.");

    await tx`
      INSERT INTO generated_app_entitlements (business_id, app_user_id, tier, status, source)
      SELECT ${link.business_id}, ${userId}, 'free', 'active', 'manual'
      WHERE NOT EXISTS (
        SELECT 1
        FROM generated_app_entitlements
        WHERE business_id = ${link.business_id}
          AND app_user_id = ${userId}
          AND source = 'manual'
          AND stripe_subscription_id IS NULL
      )
    `;

    await tx`
      INSERT INTO generated_app_sessions (business_id, app_user_id, token_hash, expires_at)
      VALUES (${link.business_id}, ${userId}, ${sessionHash}, now() + interval '30 days')
    `;
  });

  return { sessionToken, slug: link.slug, businessId: link.business_id };
}

export async function currentGeneratedAppSession(businessId: string): Promise<GeneratedAppSession | null> {
  const token = (await cookies()).get(generatedAppSessionCookieName)?.value;
  if (!token) return null;
  const sql = db();
  const rows = await sql<GeneratedAppSession[]>`
    SELECT
      s.id AS "sessionId",
      u.id AS "appUserId",
      u.email,
      COALESCE((
        SELECT e.tier
        FROM generated_app_entitlements e
        WHERE e.business_id = s.business_id
          AND e.app_user_id = u.id
          AND e.status = 'active'
        ORDER BY CASE e.tier WHEN 'owner' THEN 0 WHEN 'paid' THEN 1 ELSE 2 END
        LIMIT 1
      ), 'free') AS tier
    FROM generated_app_sessions s
    JOIN generated_app_users u ON u.id = s.app_user_id
    WHERE s.business_id = ${businessId}
      AND s.token_hash = ${hashToken(token)}
      AND s.revoked_at IS NULL
      AND s.expires_at > now()
      AND u.status = 'active'
    LIMIT 1
  `;
  return rows[0] ?? null;
}
