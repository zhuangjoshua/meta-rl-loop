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
