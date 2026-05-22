# Connection Audit - 2026-05-19

This audit tested live/local connections needed for the rebuild. Secret values were not printed.

## Summary

Ready:
- GitHub CLI auth for `tejdiv`
- Private GitHub repo `tejdiv/polsia3`
- Vercel CLI auth for `tejdiv`
- Vercel project `argon-site`
- Local Vercel link to `argon-site`
- Local v3 required Platform V0 and generated-app commerce secrets copied from v2 without printing values
- `CRON_SECRET` generated locally and set in Vercel production, preview, and development
- Supabase/Postgres runtime URL from v2
- Supabase/Postgres migration URL from v2
- Auth0 domain
- Stripe API key
- Postmark server token
- OpenAI API key
- Anthropic API key
- Tavily API key
- X OAuth DB token verified with `GET /2/users/me` after re-authorization
- v2 handoff and Hermes source folder presence

Blocked or needs action:
- No initial commit has been created or pushed yet.
- Global git `user.name` and `user.email` are unset, but repo-local identity is configured for `/Users/Zygote/polsia3`.
- `supabase` CLI is missing.
- `psql` CLI is missing.
- `CLAUDE_BIN` is configured in v2 but `claude --version` is not executable on PATH.
- Hermes venv is missing and local Hermes API is not listening.
- Local v2 env lacks Atlas/Seedance, Meta Ads, Hunter, Firecrawl, and Hermes secrets.

## GitHub And Git

Observed:
- `gh auth status` succeeds for GitHub account `tejdiv`.
- `tejdiv/argon-site` exists and is private.
- `tejdiv/polsia3` exists and is private.
- `/Users/Zygote/polsia3` is a git repository on `codex/rebuild-v3`.
- `origin` points to `https://github.com/tejdiv/polsia3.git`.
- Repo-local git author is configured.
- Global git author name/email are unset.

Required before implementation push:
- create initial commit
- push branch

## Vercel

Observed:
- `vercel whoami` succeeds as `tejdiv`.
- Vercel projects include both `argon-site` and `polsia3`.
- `argon-site` has production URL `https://fourmanifold.com` in the CLI project list.
- `argon-site` has env vars present, including platform/auth/DB/Stripe/X/OpenAI/Anthropic/Tavily and several Meta/Atlas-related keys.
- `argon-site` has multiple verified `*.fourmanifold.com` domains.
- Current local `.vercel/project.json` is linked to `argon-site`.
- Local v3 `VERCEL_PROJECT_ID` and `VERCEL_TEAM_ID` match `.vercel/project.json`.
- Local v3 `.env.local` has all required Platform V0 and generated-app commerce keys.
- v3 intentionally excludes stale `X_PLATFORM_ACCESS_TOKEN`, `X_PLATFORM_REFRESH_TOKEN`, and `X_PLATFORM_USERNAME`.
- `CRON_SECRET` exists locally and in Vercel production, preview, and development.

Required before deployment:
- keep `.env.local` uncommitted
- deploy only after code and env are ready

## Supabase/Postgres

Observed:
- v2 `DATABASE_URL` connects successfully.
- v2 `MIGRATION_DATABASE_URL` connects successfully.
- Host type detected as Supabase.
- PostgreSQL version reported as `PostgreSQL 17.6`.
- Existing relevant tables include:
  - `agent_actions`
  - `approvals`
  - `business_documents`
  - `businesses`
  - `company_sites`
  - `events`
  - `generated_app_entitlements`
  - `generated_app_plan_policies`
  - `generated_app_sessions`
  - `generated_app_users`
  - `profiles`
  - `project_ai_proxy_keys`
  - `project_ai_usage_events`
  - `project_ai_wallets`
  - `tasks`
  - `workflow_jobs`

Required before implementation:
- decide whether to reuse this database or create fresh v3 schema/database
- install/use a migration runner; `psql` CLI is not currently installed
- if using Supabase CLI workflows, install `supabase` CLI or avoid depending on it

## Auth0

Observed:
- Auth0 `.well-known/openid-configuration` returned 200 using v2 config.

Status:
- ready once secrets are copied into v3/Vercel target.

## Stripe

Observed:
- Stripe account endpoint returned 200 using v2 secret key.

Status:
- ready once secrets are copied into v3/Vercel target.

## Postmark

Observed:
- Postmark server endpoint returned 200 using v2 server token.

Status:
- ready once secrets are copied into v3/Vercel target.

## LLM Providers

Observed:
- OpenAI models endpoint returned 200 using v2 key.
- Anthropic models endpoint returned 200 using v2 key.

Status:
- ready once secrets are copied into v3/Vercel target.

## Tavily

Observed:
- Tavily search request returned 200 using v2 key.

Status:
- ready once secrets are copied into v3/Vercel target.

## X

Observed:
- X `users/me` with current v2 access token returned 401.
- `.env.local` refresh token is invalid.
- DB-stored encrypted refresh token initially refreshed successfully during diagnostics, but was consumed/rotated by that check and is now invalid.
- OAuth client id/secret are present.
- X Developer app settings were corrected from read-only and an old callback URL to read/write with the deployed callback URL.
- OAuth re-authorization completed and the encrypted DB token now returns `GET https://api.x.com/2/users/me` status 200.
- Verified connected account username: `OpenBizApp`.
- Verification timestamp observed locally: `2026-05-20T00:50:01.350Z`.
- Local `.env.local` still has stale token values and `X_PLATFORM_USERNAME=fourmanifo`; for v3, DB tokens are authoritative unless local worker env tokens are explicitly refreshed through a no-print secret flow.

Status:
- ready for identity verification
- ready to wire into v3 as a configured integration
- do not fake X publish; require a real `POST /2/tweets` receipt
- direct publish has not been tested in this audit

## Meta And Sora

Observed:
- Local v2 env lacks `ATLAS_API_KEY`.
- Local v2 env lacks Meta Ads launch secrets.
- Vercel `argon-site` env list includes several Meta/Atlas-related keys, but values were not read or printed.

V0 decision still stands:
- allowed: generate/display Sora creative if `OPENAI_API_KEY` is available to the worker/runtime
- forbidden: Meta campaign/ad/adset/upload/spend calls in v0

Verified update:
- `OPENAI_API_KEY` is present locally.
- Local worker submitted a real OpenAI Sora job and saved a real provider job id.
- `ATLAS_API_KEY` is not required for the current v0 media lane.
- Do not require Meta launch secrets for v0.

## Hermes / Local Agent Runtime

Observed:
- v2 Hermes source folder exists.
- Hermes start script exists.
- Hermes venv is missing.
- local Hermes API at `127.0.0.1:8642` is not listening.
- local v2 env lacks `ARGON_RUNTIME_URL`, `ARGON_RUNTIME_API_KEY`, and `HERMES_WEBHOOK_SECRET`.

Status:
- not ready as a running runtime
- Hermes is required for the scoped workflow categories where v2 used it
- local setup is needed before using Hermes-backed workflows
- Hermes is not required for deterministic side effects, generated-app builds, payments, cron state, or AI metering

## Local CLI/Tooling

Present:
- `gh`
- `vercel`
- `node`
- `npm`
- `git`

Missing:
- `supabase`
- `psql`

Problem:
- configured `CLAUDE_BIN` resolves to `claude`, but `claude --version` is not executable.

## Immediate Next Actions

1. Scaffold the Next.js app foundation.
2. Add migrations and DB client using a Node migration path unless `psql`/Supabase CLI is installed later.
3. Keep X runtime tokens DB-backed; do not copy stale env tokens.
4. Copy and wire Hermes as a scoped local runtime adapter when implementing the Hermes-backed workflow slice.
5. Avoid Claude CLI as a core dependency. Verify any SDK path does not require a Claude Code executable before relying on it.
6. Create the initial commit and push after the initial project files are ready.
