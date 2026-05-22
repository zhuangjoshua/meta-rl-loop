import { z } from "zod";
import { ConfigurationError } from "./errors";
import { loadLocalSecrets } from "./secrets";

loadLocalSecrets();

const required = z.string().trim().min(1);

function parseEnv<T extends z.ZodRawShape>(schema: z.ZodObject<T>) {
  loadLocalSecrets();
  const parsed = schema.safeParse(process.env);
  if (!parsed.success) {
    const missing = parsed.error.issues.map((issue) => issue.path.join(".")).join(", ");
    throw new ConfigurationError(`Missing or invalid environment variables: ${missing}`);
  }
  return parsed.data;
}

export function getDatabaseEnv() {
  return parseEnv(
    z.object({
      DATABASE_URL: required
    })
  );
}

export function getMigrationEnv() {
  return parseEnv(
    z.object({
      MIGRATION_DATABASE_URL: required
    })
  );
}

export function getAppEnv() {
  return parseEnv(
    z.object({
      APP_URL: required.url(),
      APP_BASE_URL: required.url(),
      PUBLIC_COMPANY_BASE_DOMAIN: required
    })
  );
}

export function getAuth0Env() {
  return parseEnv(
    z.object({
      APP_BASE_URL: required.url(),
      AUTH0_DOMAIN: required,
      AUTH0_CLIENT_ID: required,
      AUTH0_CLIENT_SECRET: required,
      AUTH0_SECRET: required
    })
  );
}

export function getCronEnv() {
  return parseEnv(
    z.object({
      CRON_SECRET: required
    })
  );
}

export function getVercelEnv() {
  return parseEnv(
    z.object({
      VERCEL_TOKEN: required,
      VERCEL_TEAM_ID: required,
      VERCEL_PROJECT_ID: required
    })
  );
}

export function getLlmsEnv() {
  return parseEnv(
    z.object({
      ANTHROPIC_API_KEY: required.optional(),
      OPENAI_API_KEY: required.optional()
    })
  );
}

export function getEncryptionEnv() {
  const env = parseEnv(
    z.object({
      APP_ENCRYPTION_KEY: required
    })
  );
  const key = Buffer.from(env.APP_ENCRYPTION_KEY, "base64");
  if (key.length !== 32) {
    throw new ConfigurationError("APP_ENCRYPTION_KEY must be a 32-byte base64 value.");
  }
  return { APP_ENCRYPTION_KEY: env.APP_ENCRYPTION_KEY, key };
}

export function getStripeEnv() {
  return parseEnv(
    z.object({
      STRIPE_SECRET_KEY: required,
      STRIPE_WEBHOOK_SECRET: required,
      STRIPE_CONNECT_APPLICATION_FEE_BPS: required
    })
  );
}

export function getAtlasEnv() {
  return parseEnv(
    z.object({
      ATLAS_API_KEY: required
    })
  );
}

export function getOpenAiVideoEnv() {
  return parseEnv(
    z.object({
      OPENAI_API_KEY: required
    })
  );
}

export function getTavilyEnv() {
  return parseEnv(
    z.object({
      TAVILY_API_KEY: required
    })
  );
}

export function getArgonRuntimeEnv() {
  loadLocalSecrets();
  const baseUrl =
    process.env.ARGON_RUNTIME_URL?.trim() ||
    process.env.VOICE_ARGON_API_URL?.trim() ||
    "http://127.0.0.1:8642";

  return {
    ARGON_RUNTIME_URL: baseUrl.replace(/\/$/, ""),
    ARGON_RUNTIME_API_KEY:
      process.env.ARGON_RUNTIME_API_KEY?.trim() ||
      process.env.VOICE_ARGON_API_KEY?.trim() ||
      process.env.API_SERVER_KEY?.trim() ||
      "",
    ARGON_RUNTIME_MODEL:
      process.env.ARGON_RUNTIME_MODEL?.trim() ||
      process.env.ARGON_CEO_MODEL?.trim() ||
      process.env.ARGON_PRODUCT_AI_MODEL?.trim() ||
      "claude-opus-4-7"
  };
}

export function getLocalFoundationEnv() {
  loadLocalSecrets();
  return {
    TAVILY_API_KEY: process.env.TAVILY_API_KEY?.trim() || "",
    ANTHROPIC_API_KEY: process.env.ANTHROPIC_API_KEY?.trim() || "",
    OPENAI_API_KEY: process.env.OPENAI_API_KEY?.trim() || "",
    ARGON_FOUNDATION_MODEL:
      process.env.ARGON_FOUNDATION_MODEL?.trim() ||
      process.env.ARGON_CEO_MODEL?.trim() ||
      process.env.ARGON_PRODUCT_AI_MODEL?.trim() ||
      "claude-opus-4-7"
  };
}

export function getProductAiPolicyEnv() {
  loadLocalSecrets();
  const explicitProvider = process.env.ARGON_PRODUCT_AI_PROVIDER?.trim();
  const provider = explicitProvider || (process.env.ANTHROPIC_API_KEY?.trim() ? "anthropic" : "openai");
  const model =
    process.env.ARGON_PRODUCT_AI_MODEL?.trim() ||
    (provider === "anthropic" ? "claude-opus-4-7" : "gpt-5.2");
  const qualityTier = process.env.ARGON_PRODUCT_AI_QUALITY_TIER?.trim() || (provider === "anthropic" ? "frontier" : "quality");
  return { provider, model, qualityTier };
}

export function getPostmarkEnv() {
  return parseEnv(
    z.object({
      POSTMARK_SERVER_TOKEN: required,
      POSTMARK_FROM_EMAIL: required.email()
    })
  );
}

export function getXClientEnv() {
  return parseEnv(
    z.object({
      X_CLIENT_ID: required,
      X_CLIENT_SECRET: required
    })
  );
}

export function localAuthBypassEnabled() {
  loadLocalSecrets();
  return process.env.ARGON_LOCAL_AUTH_BYPASS === "1" || process.env.ARGON_LOCAL_AUTH_BYPASS === "true";
}

export function getLocalAuthSeed() {
  loadLocalSecrets();
  return {
    subject: process.env.ARGON_LOCAL_AUTH_SUBJECT || "local-operator",
    email: process.env.ARGON_LOCAL_AUTH_EMAIL || "operator@fourmanifold.com",
    name: process.env.ARGON_LOCAL_AUTH_NAME || "Operator"
  };
}
