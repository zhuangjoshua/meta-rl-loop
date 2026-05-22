import { getXClientEnv } from "../env";
import { BadRequestError, ConfigurationError } from "../errors";
import {
  markPlatformIntegrationError,
  markPlatformIntegrationVerified,
  readPlatformIntegrationSecrets,
  upsertPlatformIntegrationSecrets
} from "../platform-integrations";
import {
  markProviderIntegrationError,
  markProviderIntegrationVerified,
  readResolvedProviderSecrets,
  upsertProviderIntegrationSecrets,
  type ProviderIntegrationRow,
  type ProviderIntegrationScope
} from "../provider-integrations";

const xPlatformIntegrationId = "x_platform";

type XRuntimeContext = {
  businessId?: string | null;
  profileId?: string | null;
};

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

function xScope(input: XRuntimeContext = {}): ProviderIntegrationScope {
  if (input.businessId) return { scopeType: "business", businessId: input.businessId };
  if (input.profileId) return { scopeType: "profile", profileId: input.profileId };
  return { scopeType: "platform" };
}

function xCredentialScope(input: XRuntimeContext = {}): ProviderIntegrationScope {
  if (input.businessId) return { scopeType: "platform" };
  return xScope(input);
}

function xScopeFromRow(row: ProviderIntegrationRow | null | undefined, defaultScope: ProviderIntegrationScope): ProviderIntegrationScope {
  if (!row) return defaultScope;
  if (row.scope_type === "business") return { scopeType: "business", businessId: row.business_id };
  if (row.scope_type === "profile") return { scopeType: "profile", profileId: row.profile_id };
  if (row.scope_type === "generated_app") return { scopeType: "generated_app", generatedAppUserId: row.generated_app_user_id };
  return { scopeType: "platform" };
}

function xScopeLabel(scope: ProviderIntegrationScope, input: XRuntimeContext = {}) {
  if (scope.scopeType === "platform" && input.businessId) return `platform outreach identity for business ${input.businessId}`;
  if (scope.scopeType === "business") return `business ${scope.businessId}`;
  if (scope.scopeType === "profile") return `profile ${scope.profileId}`;
  return "platform";
}

async function readXRuntimeConfig(input: XRuntimeContext = {}) {
  const oauth = getXClientEnv();
  let stored: Record<string, string> = {};
  const credentialScope = xCredentialScope(input);
  const resolved = await readResolvedProviderSecrets({
    provider: "x",
    keys: ["access_token", "refresh_token"],
    businessId: credentialScope.scopeType === "business" ? credentialScope.businessId : null,
    profileId: credentialScope.scopeType === "profile" ? credentialScope.profileId : null,
    includePlatformScope: credentialScope.scopeType === "platform"
  }).catch(() => ({ row: null, secrets: {} as Record<string, string> }));
  const resolvedScope = xScopeFromRow(resolved.row, credentialScope);
  if (resolved.row && resolved.row.status !== "active") {
    throw new ConfigurationError(`X integration for ${xScopeLabel(resolvedScope, input)} is ${resolved.row.status}.`);
  }
  stored = resolved.secrets;
  if (credentialScope.scopeType === "platform" && !stored.access_token) {
    stored = await readPlatformIntegrationSecrets(xPlatformIntegrationId, ["access_token", "refresh_token"]).catch(
      () => ({} as Record<string, string>)
    );
  }
  const accessToken = stored.access_token || "";
  const refreshToken = stored.refresh_token || "";
  if (!accessToken) throw new ConfigurationError(`X access token is not present for ${xScopeLabel(resolvedScope, input)}.`);
  if (!refreshToken) throw new ConfigurationError(`X refresh token is not present for ${xScopeLabel(resolvedScope, input)}.`);
  return {
    clientId: oauth.X_CLIENT_ID,
    clientSecret: oauth.X_CLIENT_SECRET,
    accessToken,
    refreshToken,
    scope: resolvedScope
  };
}

export async function persistXTokens(input: {
  accessToken: string;
  refreshToken: string;
  expiresIn?: number;
  scope?: string;
  context?: XRuntimeContext;
  providerScope?: ProviderIntegrationScope;
}) {
  const scope = input.providerScope ?? xScope(input.context);
  await upsertProviderIntegrationSecrets({
    provider: "x",
    scope,
    secrets: {
      access_token: input.accessToken,
      refresh_token: input.refreshToken
    },
    publicConfig: {
      scope: input.scope ?? null,
      expires_in: input.expiresIn ?? null
    },
    status: "active"
  });
  if (scope.scopeType !== "platform") return;
  await upsertPlatformIntegrationSecrets({
    id: xPlatformIntegrationId,
    secrets: {
      access_token: input.accessToken,
      refresh_token: input.refreshToken
    },
    publicConfig: {
      scope: input.scope ?? null,
      expires_in: input.expiresIn ?? null
    },
    status: "active"
  });
}

export async function persistXPlatformTokens(input: {
  accessToken: string;
  refreshToken: string;
  expiresIn?: number;
  scope?: string;
}) {
  await persistXTokens({ ...input, context: {} });
}

async function refreshAccessToken(input: XRuntimeContext = {}) {
  const env = await readXRuntimeConfig(input);
  const credentials = Buffer.from(`${env.clientId}:${env.clientSecret}`).toString("base64");
  const response = await fetch("https://api.x.com/2/oauth2/token", {
    method: "POST",
    headers: {
      Authorization: `Basic ${credentials}`,
      "Content-Type": "application/x-www-form-urlencoded"
    },
    body: new URLSearchParams({
      grant_type: "refresh_token",
      refresh_token: env.refreshToken,
      client_id: env.clientId
    })
  });
  const parsed = (await readBody(response)) as XTokenResponse | null;
  if (!response.ok || !parsed?.access_token) {
    throw new Error(`X token refresh returned ${response.status}.`);
  }
  if (parsed.refresh_token) {
    await persistXTokens({
      accessToken: parsed.access_token,
      refreshToken: parsed.refresh_token,
      expiresIn: parsed.expires_in,
      scope: parsed.scope,
      context: input,
      providerScope: env.scope
    });
  }
  return { accessToken: parsed.access_token, scope: env.scope };
}

async function createPost(accessToken: string, text: string) {
  const response = await fetch("https://api.x.com/2/tweets", {
    method: "POST",
    headers: {
      Authorization: `Bearer ${accessToken}`,
      "Content-Type": "application/json"
    },
    body: JSON.stringify({ text })
  });
  return { ok: response.ok, status: response.status, body: await readBody(response) };
}

export async function getXCurrentUser(accessToken?: string, context: XRuntimeContext = {}, tokenScope?: ProviderIntegrationScope) {
  let token = accessToken;
  let providerScope = tokenScope ?? xScope(context);
  if (!token) {
    const env = await readXRuntimeConfig(context);
    token = env.accessToken;
    providerScope = env.scope;
  }

  const response = await fetch("https://api.x.com/2/users/me", {
    headers: { Authorization: `Bearer ${token}` }
  });
  const body = await readBody(response);
  if (!response.ok) {
    if (!accessToken && response.status === 401) {
      const refreshed = await refreshAccessToken(context);
      return getXCurrentUser(refreshed.accessToken, context, refreshed.scope);
    }
    const message = `X users/me returned ${response.status}.`;
    await markProviderIntegrationError({ provider: "x", scope: providerScope, error: message }).catch(() => null);
    if (providerScope.scopeType === "platform") {
      await markPlatformIntegrationError({ id: xPlatformIntegrationId, error: message }).catch(() => null);
    }
    throw new Error(message);
  }

  const record = body && typeof body === "object" ? (body as Record<string, unknown>) : {};
  const data = record.data && typeof record.data === "object" ? (record.data as Record<string, unknown>) : {};
  const publicConfig = {
    user_id: typeof data.id === "string" ? data.id : null,
    username: typeof data.username === "string" ? data.username : null
  };
  await markProviderIntegrationVerified({
    provider: "x",
    scope: providerScope,
    publicConfig
  }).catch(() => null);
  if (providerScope.scopeType === "platform") {
    await markPlatformIntegrationVerified({
      id: xPlatformIntegrationId,
      publicConfig
    }).catch(() => null);
  }
  return { response: body };
}

export async function publishXPost(input: { text: string; madeWithAi?: boolean; businessId?: string | null; profileId?: string | null }) {
  const text = input.text.trim();
  if (!text) throw new BadRequestError("X post text is required.");
  if (text.length > 280) throw new BadRequestError("X post text must be 280 characters or fewer.");

  const context = { businessId: input.businessId ?? null, profileId: input.profileId ?? null };
  const env = await readXRuntimeConfig(context);
  const first = await createPost(env.accessToken, text);
  if (first.ok) return { response: first.body, tokenRefreshed: false, madeWithAi: input.madeWithAi ?? true };
  if (first.status !== 401) throw new Error(`X create post returned ${first.status}.`);

  const refreshed = await refreshAccessToken(context);
  const second = await createPost(refreshed.accessToken, text);
  if (!second.ok) throw new Error(`X create post returned ${second.status}.`);
  return { response: second.body, tokenRefreshed: true, madeWithAi: input.madeWithAi ?? true };
}
