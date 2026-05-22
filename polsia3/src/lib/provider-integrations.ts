import { decryptSecret, encryptSecret } from "./crypto";
import { db } from "./db";
import { toJson } from "./json";

export type ProviderIntegrationScopeType = "platform" | "business" | "profile" | "generated_app";
export type ProviderIntegrationStatus = "not_configured" | "active" | "paused" | "error" | "revoked";

export type ProviderIntegrationScope = {
  scopeType: ProviderIntegrationScopeType;
  businessId?: string | null;
  profileId?: string | null;
  generatedAppUserId?: string | null;
};

export type ProviderIntegrationRow = {
  id: string;
  provider: string;
  scope_type: ProviderIntegrationScopeType;
  business_id: string | null;
  profile_id: string | null;
  generated_app_user_id: string | null;
  status: ProviderIntegrationStatus;
  public_config: unknown;
  encrypted_config: unknown;
  last_error: string | null;
  last_verified_at: string | null;
};

const zeroUuid = "00000000-0000-0000-0000-000000000000";

function normalizedProvider(provider: string) {
  return provider.trim().toLowerCase();
}

function recordFrom(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value) ? (value as Record<string, unknown>) : {};
}

function encryptedSecrets(secrets: Record<string, string | undefined | null>) {
  return Object.fromEntries(
    Object.entries(secrets)
      .map(([key, value]) => [key, value?.trim() ?? ""] as const)
      .filter(([, value]) => value.length > 0)
      .map(([key, value]) => [key, encryptSecret(value)])
  );
}

function scopeValues(scope: ProviderIntegrationScope) {
  return {
    scopeType: scope.scopeType,
    businessId: scope.businessId ?? null,
    profileId: scope.profileId ?? null,
    generatedAppUserId: scope.generatedAppUserId ?? null
  };
}

export function providerScopeKey(provider: string, scope: ProviderIntegrationScope) {
  const values = scopeValues(scope);
  return [
    normalizedProvider(provider),
    values.scopeType,
    values.businessId ?? zeroUuid,
    values.profileId ?? zeroUuid,
    values.generatedAppUserId ?? zeroUuid
  ].join(":");
}

export async function upsertProviderIntegrationSecrets(input: {
  provider: string;
  scope: ProviderIntegrationScope;
  secrets: Record<string, string | undefined | null>;
  publicConfig?: Record<string, unknown>;
  status?: ProviderIntegrationStatus;
}) {
  const provider = normalizedProvider(input.provider);
  const scope = scopeValues(input.scope);
  const encryptedConfig = encryptedSecrets(input.secrets);
  const publicConfig = input.publicConfig ?? {};
  if (Object.keys(encryptedConfig).length === 0 && Object.keys(publicConfig).length === 0) return null;

  const sql = db();
  const rows = await sql<ProviderIntegrationRow[]>`
    INSERT INTO provider_integrations (
      provider,
      scope_type,
      business_id,
      profile_id,
      generated_app_user_id,
      status,
      public_config,
      encrypted_config,
      last_error,
      last_verified_at
    )
    VALUES (
      ${provider},
      ${scope.scopeType},
      ${scope.businessId},
      ${scope.profileId},
      ${scope.generatedAppUserId},
      ${input.status ?? "active"},
      ${sql.json(toJson(publicConfig))}::jsonb,
      ${sql.json(toJson(encryptedConfig))}::jsonb,
      NULL,
      now()
    )
    ON CONFLICT (
      provider,
      scope_type,
      COALESCE(business_id, '00000000-0000-0000-0000-000000000000'::uuid),
      COALESCE(profile_id, '00000000-0000-0000-0000-000000000000'::uuid),
      COALESCE(generated_app_user_id, '00000000-0000-0000-0000-000000000000'::uuid)
    )
    DO UPDATE SET
      status = EXCLUDED.status,
      public_config = provider_integrations.public_config || EXCLUDED.public_config,
      encrypted_config = provider_integrations.encrypted_config || EXCLUDED.encrypted_config,
      last_error = NULL,
      last_verified_at = now()
    RETURNING id, provider, scope_type, business_id, profile_id, generated_app_user_id,
              status, public_config, encrypted_config, last_error, last_verified_at
  `;
  return rows[0] ?? null;
}

export async function getProviderIntegration(provider: string, scope: ProviderIntegrationScope) {
  const normalized = normalizedProvider(provider);
  const values = scopeValues(scope);
  const sql = db();
  const rows = await sql<ProviderIntegrationRow[]>`
    SELECT id, provider, scope_type, business_id, profile_id, generated_app_user_id,
           status, public_config, encrypted_config, last_error, last_verified_at
    FROM provider_integrations
    WHERE provider = ${normalized}
      AND scope_type = ${values.scopeType}
      AND COALESCE(business_id, ${zeroUuid}::uuid) = COALESCE(${values.businessId}::uuid, ${zeroUuid}::uuid)
      AND COALESCE(profile_id, ${zeroUuid}::uuid) = COALESCE(${values.profileId}::uuid, ${zeroUuid}::uuid)
      AND COALESCE(generated_app_user_id, ${zeroUuid}::uuid) = COALESCE(${values.generatedAppUserId}::uuid, ${zeroUuid}::uuid)
    LIMIT 1
  `;
  return rows[0] ?? null;
}

export async function resolveProviderIntegration(input: {
  provider: string;
  businessId?: string | null;
  profileId?: string | null;
  generatedAppUserId?: string | null;
  includePlatformScope?: boolean;
}) {
  const provider = normalizedProvider(input.provider);
  const includePlatformScope = input.includePlatformScope ?? true;
  const sql = db();
  const rows = await sql<ProviderIntegrationRow[]>`
    SELECT id, provider, scope_type, business_id, profile_id, generated_app_user_id,
           status, public_config, encrypted_config, last_error, last_verified_at
    FROM provider_integrations
    WHERE provider = ${provider}
      AND (
        (${includePlatformScope}::boolean AND scope_type = 'platform')
        OR (scope_type = 'business' AND business_id = ${input.businessId ?? null})
        OR (scope_type = 'profile' AND profile_id = ${input.profileId ?? null})
        OR (scope_type = 'generated_app' AND generated_app_user_id = ${input.generatedAppUserId ?? null})
      )
    ORDER BY
      CASE scope_type
        WHEN 'generated_app' THEN 0
        WHEN 'business' THEN 1
        WHEN 'profile' THEN 2
        ELSE 3
      END,
      last_verified_at DESC NULLS LAST,
      updated_at DESC
    LIMIT 1
  `;
  return rows[0] ?? null;
}

export async function readResolvedProviderSecrets(input: {
  provider: string;
  keys: string[];
  businessId?: string | null;
  profileId?: string | null;
  generatedAppUserId?: string | null;
  includePlatformScope?: boolean;
}) {
  const row = await resolveProviderIntegration(input);
  const encryptedConfig = recordFrom(row?.encrypted_config);
  const secrets: Record<string, string> = {};

  for (const key of input.keys) {
    const encoded = encryptedConfig[key];
    if (typeof encoded !== "string" || !encoded.trim()) continue;
    secrets[key] = decryptSecret(encoded);
  }

  return { row, secrets };
}

export async function markProviderIntegrationVerified(input: {
  provider: string;
  scope: ProviderIntegrationScope;
  publicConfig?: Record<string, unknown>;
}) {
  await upsertProviderIntegrationSecrets({
    provider: input.provider,
    scope: input.scope,
    secrets: {},
    publicConfig: input.publicConfig ?? {},
    status: "active"
  });
}

export async function markProviderIntegrationError(input: {
  provider: string;
  scope: ProviderIntegrationScope;
  error: string;
}) {
  const provider = normalizedProvider(input.provider);
  const scope = scopeValues(input.scope);
  const sql = db();
  await sql`
    INSERT INTO provider_integrations (
      provider,
      scope_type,
      business_id,
      profile_id,
      generated_app_user_id,
      status,
      public_config,
      encrypted_config,
      last_error
    )
    VALUES (
      ${provider},
      ${scope.scopeType},
      ${scope.businessId},
      ${scope.profileId},
      ${scope.generatedAppUserId},
      'error',
      '{}'::jsonb,
      '{}'::jsonb,
      ${input.error}
    )
    ON CONFLICT (
      provider,
      scope_type,
      COALESCE(business_id, '00000000-0000-0000-0000-000000000000'::uuid),
      COALESCE(profile_id, '00000000-0000-0000-0000-000000000000'::uuid),
      COALESCE(generated_app_user_id, '00000000-0000-0000-0000-000000000000'::uuid)
    )
    DO UPDATE SET status = 'error', last_error = EXCLUDED.last_error
  `;
}
