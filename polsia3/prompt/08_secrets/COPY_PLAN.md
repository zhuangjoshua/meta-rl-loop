# Secret Copy Plan

During implementation:

1. Copy v2 `.env.local` values into v3 `.env.local` without printing them.
2. Preserve existing v3 `.env.local` entries unless replacing is explicitly intended.
3. Generate `CRON_SECRET`.
4. Do not invent missing vendor credentials.
5. Record missing secrets as blocked/config state.
6. Push required secrets to the correct Vercel project only after Vercel project target is confirmed.

Never commit `.env.local`.

## Exceptions

Do not copy stale v2 `X_PLATFORM_ACCESS_TOKEN` or `X_PLATFORM_REFRESH_TOKEN` into v3.

Do not copy stale v2 `X_PLATFORM_USERNAME` into v3 either. The copied v2 username was for the old account state; the verified DB-backed account is `OpenBizApp`.

For X:
- copy/use `X_CLIENT_ID` and `X_CLIENT_SECRET`
- use the encrypted DB integration row as the runtime token source
- update local env tokens only through a fresh no-print token flow if a specific local-only tool later proves it cannot read the DB

## Verified Copy Status

Completed locally on 2026-05-19:
- v2 non-empty `.env.local` values were copied into v3 `.env.local` without printing values.
- Existing v3 `VERCEL_OIDC_TOKEN` was preserved.
- `X_CLIENT_ID` and `X_CLIENT_SECRET` were copied.
- `X_PLATFORM_ACCESS_TOKEN`, `X_PLATFORM_REFRESH_TOKEN`, and `X_PLATFORM_USERNAME` were intentionally not copied.
- `CRON_SECRET` was generated locally.
- Local `VERCEL_PROJECT_ID` and `VERCEL_TEAM_ID` match the linked `argon-site` Vercel project.

Completed in Vercel:
- `CRON_SECRET` was set for production, preview, and development on `argon-site`.

Still true:
- Do not commit `.env.local`.
- Runtime X publish/identity must read the encrypted `platform_integrations.x_platform` DB row unless a later no-print worker token sync is explicitly implemented.
