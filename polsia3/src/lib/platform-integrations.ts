import { decryptSecret, encryptSecret } from "./crypto";
import { db } from "./db";
import { toJson } from "./json";

export type PlatformIntegrationStatus = "not_configured" | "active" | "paused" | "error";

type PlatformIntegrationRow = {
  id: string;
  status: PlatformIntegrationStatus;
  public_config: unknown;
  encrypted_config: unknown;
  last_error: string | null;
  last_verified_at: string | null;
};

function recordFrom(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value) ? (value as Record<string, unknown>) : {};
}

function nonEmptySecrets(secrets: Record<string, string | undefined | null>) {
  return Object.fromEntries(
    Object.entries(secrets)
      .map(([key, value]) => [key, value?.trim() ?? ""] as const)
      .filter(([, value]) => value.length > 0)
      .map(([key, value]) => [key, encryptSecret(value)])
  );
}

export async function getPlatformIntegration(id: string) {
  const sql = db();
  const rows = await sql<PlatformIntegrationRow[]>`
    SELECT id, status, public_config, encrypted_config, last_error, last_verified_at
    FROM platform_integrations
    WHERE id = ${id}
    LIMIT 1
  `;
  return rows[0] ?? null;
}

export async function upsertPlatformIntegrationSecrets(input: {
  id: string;
  secrets: Record<string, string | undefined | null>;
  publicConfig?: Record<string, unknown>;
  status?: PlatformIntegrationStatus;
}) {
  const encryptedConfig = nonEmptySecrets(input.secrets);
  const publicConfig = input.publicConfig ?? {};
  if (Object.keys(encryptedConfig).length === 0 && Object.keys(publicConfig).length === 0) return null;

  const sql = db();
  const rows = await sql<PlatformIntegrationRow[]>`
    INSERT INTO platform_integrations (id, status, public_config, encrypted_config, last_error, last_verified_at)
    VALUES (
      ${input.id},
      ${input.status ?? "active"},
      ${sql.json(toJson(publicConfig))}::jsonb,
      ${sql.json(toJson(encryptedConfig))}::jsonb,
      NULL,
      now()
    )
    ON CONFLICT (id) DO UPDATE SET
      status = EXCLUDED.status,
      public_config = platform_integrations.public_config || EXCLUDED.public_config,
      encrypted_config = platform_integrations.encrypted_config || EXCLUDED.encrypted_config,
      last_error = NULL,
      last_verified_at = now()
    RETURNING id, status, public_config, encrypted_config, last_error, last_verified_at
  `;
  return rows[0] ?? null;
}

export async function readPlatformIntegrationSecrets(id: string, keys: string[]) {
  const row = await getPlatformIntegration(id);
  const encryptedConfig = recordFrom(row?.encrypted_config);
  const result: Record<string, string> = {};

  for (const key of keys) {
    const encoded = encryptedConfig[key];
    if (typeof encoded !== "string" || !encoded.trim()) continue;
    result[key] = decryptSecret(encoded);
  }

  return result;
}

export async function markPlatformIntegrationVerified(input: { id: string; publicConfig?: Record<string, unknown> }) {
  const sql = db();
  await sql`
    INSERT INTO platform_integrations (id, status, public_config, encrypted_config, last_error, last_verified_at)
    VALUES (${input.id}, 'active', ${sql.json(toJson(input.publicConfig ?? {}))}::jsonb, '{}'::jsonb, NULL, now())
    ON CONFLICT (id) DO UPDATE SET
      status = 'active',
      public_config = platform_integrations.public_config || EXCLUDED.public_config,
      last_error = NULL,
      last_verified_at = now()
  `;
}

export async function markPlatformIntegrationError(input: { id: string; error: string }) {
  const sql = db();
  await sql`
    INSERT INTO platform_integrations (id, status, public_config, encrypted_config, last_error)
    VALUES (${input.id}, 'error', '{}'::jsonb, '{}'::jsonb, ${input.error})
    ON CONFLICT (id) DO UPDATE SET
      status = 'error',
      last_error = EXCLUDED.last_error
  `;
}
