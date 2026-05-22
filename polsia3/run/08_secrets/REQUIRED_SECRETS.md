# Required Secrets

## Required For Platform V0

- APP_URL
- APP_BASE_URL
- PUBLIC_COMPANY_BASE_DOMAIN
- DATABASE_URL
- MIGRATION_DATABASE_URL
- APP_ENCRYPTION_KEY
- CRON_SECRET
- AUTH0_DOMAIN
- AUTH0_CLIENT_ID
- AUTH0_CLIENT_SECRET
- AUTH0_SECRET
- ANTHROPIC_API_KEY or OPENAI_API_KEY
- VERCEL_TOKEN
- VERCEL_TEAM_ID
- VERCEL_PROJECT_ID

## Required For Generated-App Commerce

- STRIPE_SECRET_KEY
- STRIPE_WEBHOOK_SECRET
- STRIPE_CONNECT_APPLICATION_FEE_BPS
- POSTMARK_SERVER_TOKEN
- POSTMARK_FROM_EMAIL

## Optional Add-On Secrets

- X_CLIENT_ID
- X_CLIENT_SECRET
- X DB integration row `platform_integrations.id = 'x_platform'` with encrypted `access_token` and `refresh_token`
- OPENAI_API_KEY for Sora media generation
- ATLAS_API_KEY only for legacy Atlas/Seedance experiments; it is no longer the v0 media lane requirement
- TAVILY_API_KEY
- HUNTER_API_KEY
- RESEND_API_KEY
- OUTBOUND_EMAIL_PROVIDER and vendor-specific keys
- META_PAGE_ID
- META_BUSINESS_ID
- META_APP_ID
- META_APP_SECRET
- META_SYSTEM_USER_ACCESS_TOKEN
- META_ACCESS_TOKEN
- META_AD_ACCOUNT_ID
- Hermes runtime env vars

## Deprecated / Do Not Copy As Truth

- X_PLATFORM_ACCESS_TOKEN
- X_PLATFORM_REFRESH_TOKEN
- X_PLATFORM_USERNAME

The v2 local `.env.local` values for these keys are stale. V3 should read X runtime tokens from the encrypted `platform_integrations` DB row. Env X platform tokens may only be used as an explicit local-development override after they are freshly minted and documented; they must never be copied from stale v2 env as authoritative secrets.

## Verified Local Setup Status

As of the 2026-05-19 setup slice, v3 `.env.local` has all required Platform V0 keys listed above, plus the required generated-app commerce keys. Values were not printed.

The optional X client credentials are present locally. Runtime X tokens are not present locally by design; the encrypted DB row is the source of truth.

## Verified Media Secret Update - 2026-05-19 PT

`OPENAI_API_KEY` is present locally and was used to submit a real Sora `sora-2` video job.

`ATLAS_API_KEY` is not present locally and is not required for the current v0 media lane.

## Verified Local Meta Secret Update - 2026-05-22 PT

The operator provided Meta Page, Business, App, System User token, access token, and Ad Account values. They were stored in local `.env.local` through `./takyon secret set` without printing secret values.

Present locally:
- `META_PAGE_ID`
- `META_BUSINESS_ID`
- `META_APP_ID`
- `META_APP_SECRET`
- `META_SYSTEM_USER_ACCESS_TOKEN`
- `META_ACCESS_TOKEN`
- `META_AD_ACCOUNT_ID`

Current policy:
- These secrets do not enable v0 Meta launch/spend/upload/pause/budget mutation.
- The capability surface is `meta_ads_read` for read-only credential visibility.
- Paid media business skills remain planning/read-only unless a future audited Meta read runner is implemented.

Acceptance check:
- `./takyon setup meta_ads_read --json` returned `canRun: true` without printing secret values.
