# Takyon API Requirements

This file tracks provider and credential requirements for the active Takyon skills and tools. Do not store secret values here.

Recheck:

- `$TAKYON_HOME/config.yaml`
- the active environment / `.env`
- skill frontmatter (`required_environment_variables`, `required_credential_files`)
- runtime capability checks and tool receipts

## Current Configured Access

Observed key names are present locally as of 2026-05-25; values are intentionally omitted.

- Core agent/model: `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `CLAUDE_BIN`
- App runtime and billing: `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET`, `STRIPE_PUBLISHABLE_KEY`, `NEXT_PUBLIC_STRIPE_PUBLISHABLE_KEY`
- App email/auth: `POSTMARK_SERVER_TOKEN`, `POSTMARK_FROM_EMAIL`, `AUTH0_DOMAIN`, `AUTH0_CLIENT_ID`, `AUTH0_CLIENT_SECRET`, `AUTH0_AUDIENCE`, `AUTH0_SECRET`
- Deploy/domain: `VERCEL_TOKEN`, `VERCEL_PROJECT_ID`, `VERCEL_TEAM_ID`, `VERCEL_OIDC_TOKEN`, `PUBLIC_COMPANY_BASE_DOMAIN`
- Research: `TAVILY_API_KEY`
- Paid/social ops: `META_ACCESS_TOKEN`, `META_SYSTEM_USER_ACCESS_TOKEN`, `META_AD_ACCOUNT_ID`, `META_APP_ID`, `META_APP_SECRET`, `META_BUSINESS_ID`, `META_PAGE_ID`
- Analytics/ops: `POSTHOG_PERSONAL_API_KEY`, `SENTRY_DSN`, `SENTRY_ORG`, `SENTRY_PROJECT`
- X/OAuth app keys: `X_CLIENT_ID`, `X_CLIENT_SECRET`

## Active Takyon Skill Surface

- `takyon-market-research`
- `takyon-build-product`
- `takyon-app-runtime`
- `takyon-distribution`
- `takyon-business-metrics`
- `takyon-claude-agent-sdk`

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

## Future Additions

If a new Takyon skill needs a provider:

1. declare it in that skill's frontmatter
2. keep live/test behavior in the tool layer
3. record blocked live work honestly when credentials are absent

Do not claim live scraping, enrichment, posting, sending, import, payment, deploy, or metrics collection happened unless the concrete tool path succeeded.
