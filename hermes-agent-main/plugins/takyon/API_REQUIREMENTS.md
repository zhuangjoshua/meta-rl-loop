# Takyon API Requirements

This file tracks provider and credential requirements for the active Takyon skills and tools. Do not store secret values here.

Recheck:

- `$TAKYON_HOME/config.yaml`
- the active environment / `.env`
- skill frontmatter (`required_environment_variables`, `required_credential_files`)
- runtime capability checks and tool receipts

## Current Configured Access

Observed key names are present locally as of 2026-05-25; values are intentionally omitted.

- Core agent/model: raw provider keys live behind Safebox/provider proxy; runtime planes use brokered capability paths plus `CLAUDE_BIN` where needed.
- App runtime and billing: product app runtime may use public Stripe publishable config only. `STRIPE_SECRET_KEY`, app/billing webhook secrets, and reconciliation authority live on Safebox.
- App email/auth: product/runtime planes may use public config such as `POSTMARK_FROM_EMAIL`, `AUTH0_DOMAIN`, and `AUTH0_CLIENT_ID`. `POSTMARK_SERVER_TOKEN`, `AUTH0_CLIENT_SECRET`, `AUTH0_SECRET`, and Supabase JWT/service-role secrets live on Safebox or are unused by runtime planes.
- Deploy/domain: `VERCEL_TOKEN`, `VERCEL_PROJECT_ID`, `VERCEL_TEAM_ID`, `VERCEL_OIDC_TOKEN`, `PUBLIC_COMPANY_BASE_DOMAIN`
- Research: `TAVILY_API_KEY` lives behind Safebox/brokered provider paths; generic runtime Tavily does not read it from process env.
- Meta Ads v2: official Meta Ads MCP OAuth (`META_MCP_OAUTH_TOKEN`) held by Safebox, with optional `META_MCP_ENDPOINT` defaulting to `https://mcp.facebook.com/ads`; provider defaults remain `META_AD_ACCOUNT_ID` and `META_PAGE_ID`. Legacy app-registration/system-user values such as `META_SYSTEM_USER_ACCESS_TOKEN`, `META_APP_ID`, `META_APP_SECRET`, and `META_BUSINESS_ID` remain only for Graph shims/diagnostics such as pixel/custom-conversion/video-upload gaps; they are not the v2 launch transport. Composio Meta Ads is not a valid fallback for `takyon-meta-ads-v2`.
- Reddit organic (skill/tool requirement): `COMPOSIO_API_KEY` plus one active Composio Reddit connected account (`COMPOSIO_REDDIT_CONNECTED_ACCOUNT_ID`, or a resolvable `COMPOSIO_REDDIT_USER_ID` / alias pair).
- Reddit Ads (skill/tool requirement): `COMPOSIO_API_KEY` plus one active Composio Reddit Ads connected account (`COMPOSIO_REDDIT_ADS_CONNECTED_ACCOUNT_ID`, or a resolvable `COMPOSIO_REDDIT_ADS_USER_ID` / alias pair). Optional provider defaults remain `REDDIT_ADS_BUSINESS_ID`, `REDDIT_ADS_ACCOUNT_ID`, `REDDIT_ADS_PROFILE_ID`, `REDDIT_ADS_FUNDING_INSTRUMENT_ID`, and `REDDIT_ADS_PIXEL_ID`.
- Analytics/ops: `POSTHOG_PERSONAL_API_KEY`, `SENTRY_DSN`, `SENTRY_ORG`, `SENTRY_PROJECT`
- X/OAuth app keys: `COMPOSIO_API_KEY` plus one active Composio Twitter connected account (`COMPOSIO_TWITTER_CONNECTED_ACCOUNT_ID`, or a resolvable `COMPOSIO_TWITTER_USER_ID` / alias pair). Existing app-registration values such as `X_CLIENT_ID` and `X_CLIENT_SECRET` remain provider-console setup inputs rather than runtime posting credentials.

## Active Takyon Skill Surface

- `takyon-market-research`
- `takyon-product`
- `takyon-app-runtime`
- `takyon-distribution`
- `takyon-business-metrics`
- `takyon-x`
- `takyon-reddit`
- `takyon-meta-ads-v2`
- `takyon-reddit-ads`

These skills should declare readiness in Hermes-native frontmatter:

- `required_environment_variables`
- `required_credential_files`
- `metadata.hermes.*`

Takyon should not depend on a separate registry for skill API readiness.

## Tool Surface

The durable execution layer remains the `business_*` toolset, including:

- `business_read_business`
- `business_write_file`
- `business_record_event`
- `business_enqueue_job`
- outreach tools
- conversation tools
- app-runtime tools
- guarded runtime/provider gates

## Local Runtime Prerequisites

- The actions rail requires `deno` on `PATH` for local verification and on every VPS plane that can execute product actions.
- Operator and sub-user deploys should verify both `deno` and `systemd-run`; absence is a truthful runtime blocker, never a silent skip.
- Scheduled actions also require `plugins.takyon.app_actions.rails_base_url` to be configured for the serving topology; do not guess ports or point scheduled action traffic at the operator plane.

## Future Additions

If a new Takyon skill needs a provider:

1. declare it in that skill's frontmatter
2. keep live/test behavior in the tool layer
3. record blocked live work honestly when credentials are absent

Do not claim live scraping, enrichment, posting, sending, import, payment, deploy, or metrics collection happened unless the concrete tool path succeeded.
