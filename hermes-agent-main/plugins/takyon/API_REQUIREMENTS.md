# Takyon API Requirements

This retained file tracks provider/API requirements for Takyon skills and tools. Do not store secret values here. Recheck `$TAKYON_HOME/config.yaml`, `secrets/.env`, `business_registry`, and runtime capability snapshots before live work.

## Current Configured Access

Observed key names are present locally as of 2026-05-25; values are intentionally omitted.

- Core agent/model: `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `CLAUDE_BIN`.
- App runtime and billing: `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET`, `STRIPE_PUBLISHABLE_KEY`, `NEXT_PUBLIC_STRIPE_PUBLISHABLE_KEY`.
- App email/auth: `POSTMARK_SERVER_TOKEN`, `POSTMARK_FROM_EMAIL`, `AUTH0_DOMAIN`, `AUTH0_CLIENT_ID`, `AUTH0_CLIENT_SECRET`, `AUTH0_AUDIENCE`, `AUTH0_SECRET`.
- Deploy/domain: `VERCEL_TOKEN`, `VERCEL_PROJECT_ID`, `VERCEL_TEAM_ID`, `VERCEL_OIDC_TOKEN`, `PUBLIC_COMPANY_BASE_DOMAIN`.
- Research: `TAVILY_API_KEY`.
- Paid/social ops: `META_ACCESS_TOKEN`, `META_SYSTEM_USER_ACCESS_TOKEN`, `META_AD_ACCOUNT_ID`, `META_APP_ID`, `META_APP_SECRET`, `META_BUSINESS_ID`, `META_PAGE_ID`.
- Analytics/ops: `POSTHOG_PERSONAL_API_KEY`, `SENTRY_DSN`, `SENTRY_ORG`, `SENTRY_PROJECT`.
- X/OAuth app keys: `X_CLIENT_ID`, `X_CLIENT_SECRET`; live X posting still needs the exact tool-required bearer/API token gate.

## Integrated Skill Surface

- Demand Radar is merged into `takyon:market-research`.
- Traffic Attribution is merged into `takyon:app-runtime` for runtime gates; interpretation currently stays CEO/research-led.
- Experiment tracking, sales-pipeline handling, and third-party skill review are not standalone Takyon skills in the current trimmed set.

These skill integrations use existing Takyon tools first: `business_registry`, `business_calculate_pulse`, `business_read_business`, `business_write_file`, `business_record_event`, `business_enqueue_job`, conversation tools, outreach tools, app-runtime tools, and guarded provider gates.

## Aspirational Canonical Tools

Add these only when durable queryable state or runtime rails are needed.

- Demand Radar: `business_record_market_signal`, `business_read_market_signals`, `business_update_market_signal`.
- Experiment Ledger: `business_upsert_experiment`, `business_record_experiment_observation`, `business_read_experiments`.
- Sales Pipeline: `business_upsert_sales_account`, `business_upsert_sales_contact`, `business_upsert_sales_opportunity`, `business_record_sales_touch`, `business_read_sales_pipeline`.
- Traffic Attribution: `business_record_traffic_event`, `business_record_analytics_snapshot`, `business_read_funnel`, plus a guarded app analytics endpoint.
- Skill Safety Review: no dedicated tool required for v1; consider a structured audit receipt tool only if reviews need durable querying.

## Aspirational Provider Gates

- Demand Radar live enrichment: `SERPAPI_API_KEY`, `SEMRUSH_API_KEY`, `FIRECRAWL_API_KEY`, `APIFY_TOKEN`, Reddit API credentials, Google Custom Search or equivalent. Use current `TAVILY_API_KEY` where it is enough.
- Sales Pipeline live enrichment/CRM/sending: `APOLLO_API_KEY`, `HUBSPOT_ACCESS_TOKEN`, `PDL_API_KEY` or equivalent people/company data provider, LinkedIn-approved integration, email provider sending credentials.
- Traffic Attribution imports: Google OAuth app credentials for GA4 and Search Console, ad-platform reporting credentials, campaign cost import source.
- Skill Safety Review stronger scans: local tools such as `clamscan`, `gitleaks`, `trufflehog`, `osv-scanner`, `semgrep`, or `yara`; private repo access if scanning private sources.

## Live-Mode Rule

If a feature needs a provider listed above and the credential/tool is absent, implement only the local or guarded path, record the missing gate, and do not claim live scraping, enrichment, posting, sending, import, payment, deploy, or metric collection happened.
