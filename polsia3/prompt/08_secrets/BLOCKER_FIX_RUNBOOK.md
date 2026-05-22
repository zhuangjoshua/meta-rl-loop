# Blocker Fix Runbook

This file explains how to resolve the connection blockers observed in `CONNECTION_AUDIT_2026-05-19.md`.

## Vercel Project Link

The user confirmed that the current `argon-site` deployment may be overwritten.

Fix completed:

- `/Users/Zygote/polsia3` was linked to `argon-site` with `vercel link --yes --project argon-site`.
- The copied local `VERCEL_PROJECT_ID` and `VERCEL_TEAM_ID` match `.vercel/project.json`.
- `CRON_SECRET` was set in Vercel production, preview, and development.

Notes:
- `argon-site` currently has the important production env vars and `fourmanifold.com` domains.
- Do not deploy until code and env are ready.

## GitHub Repo And Local Git

Observed:
- `tejdiv/polsia3` exists and is private.
- `/Users/Zygote/polsia3` is a git repo on branch `codex/rebuild-v3`.
- `origin` points to `https://github.com/tejdiv/polsia3.git`.
- global git author name/email are unset.

Fix completed:
- Repo-local git author is set to `tejdiv <203025654+tejdiv@users.noreply.github.com>`.
- Global git author remains unset, which is acceptable because this repo has local identity configured.

Commit/push should happen only after the initial project files are ready.

## X Token 401 - Resolved For DB-Backed OAuth

Observed:
- X `users/me` returned 401 with the current access token.
- The `.env.local` refresh token is invalid.
- A DB-stored encrypted refresh token initially refreshed successfully during diagnostics, but X rotates refresh tokens on use and the diagnostic did not persist the returned values. A later refresh failed with `invalid_request`.
- OAuth client credentials are present.

Meaning:
- The old env tokens were stale and the previous DB refresh token was consumed during diagnostics.

Fix completed:

1. Re-authorize through the existing X OAuth route.
2. Use the deployed app route if the current production app is still available:

```text
https://app.fourmanifold.com/api/integrations/x/oauth/start?returnTo=/dashboard
```

3. After callback succeeds, verify the encrypted DB tokens with `GET https://api.x.com/2/users/me`.
4. Copy/refetch tokens into local/v3 env only through a no-print secret flow if local worker needs env copies.

Current status:
- `GET https://api.x.com/2/users/me` returns 200 using the encrypted DB token.
- Connected account username is `OpenBizApp`.
- `platform_integrations.x_platform` status is `active`.
- No X post was created during verification.
- Direct publishing still requires a real `POST /2/tweets` receipt before that action path is marked fully proven.
- Local `.env.local` still contains stale X token values; do not copy those into v3 as truth.

## Claude CLI / Agent SDK

Observed:
- v2 has `CLAUDE_BIN` configured as `claude`.
- `claude --version` is not executable on PATH.

Decision:
- avoid Claude CLI
- prefer SDK/library/local-worker paths
- do not use CLI fallback as the default generated-app builder path

Implementation rule:

- V3 should not require `CLAUDE_BIN` for the core implementation.
- V2 had `@anthropic-ai/claude-agent-sdk`, but v2's SDK call still passed `pathToClaudeCodeExecutable`. So the v2 SDK path may still require a Claude Code executable.
- If an agent SDK path is used in v3, first verify whether the current SDK can run without a Claude CLI/binary dependency.
- If it cannot, do not make it a core dependency; keep the modular template/local-worker builder path primary and mark SDK-backed generation blocked until the executable/runtime requirement is solved.
- The preferred v0 generated-app path is modular templates plus local worker build gates.

Do not use Vercel Sandbox or Open Lovable as a workaround.

## Hermes Runtime

Observed:
- Hermes source folder exists in v2.
- start script exists.
- Hermes virtualenv is missing.
- `127.0.0.1:8642` is not listening.
- Hermes env vars are not configured locally.

Decision:
- Hermes is needed for the scoped workflow categories where v2 used it.
- The code can be copied from v2.
- It still runs behind a local gateway API process (`/v1/runs`) when Hermes-backed workflows execute.
- This is local infrastructure, not an external API service and not a VPS requirement.

Fix:

```bash
cd /Users/Zygote/Downloads/polsia2
scripts/setup-argon-hermes-runtime.sh
scripts/start-argon-hermes-runtime.sh
```

Then verify:

```bash
curl http://127.0.0.1:8642/v1/health
```

If v3 vendors Hermes later, the setup scripts should move into v3 and use v3 env paths.

## Supabase CLI

Observed:
- `supabase` CLI is missing.

Clarification:
- Supabase/Postgres itself is mandatory core infrastructure.
- Only the Supabase CLI is optional if the implementation uses a Node migration runner instead.

Fix if using Supabase CLI workflows:

```bash
brew install supabase/tap/supabase
supabase --version
```

Alternative:
- Use Node-based migrations against the mandatory Supabase/Postgres database and do not require Supabase CLI.

## psql CLI

Observed:
- `psql` CLI is missing.

Fix:

```bash
brew install libpq
brew link --force libpq
psql --version
```

Alternative:
- Use Node migration scripts via the `postgres` npm package against the mandatory Supabase/Postgres database and do not require `psql`.
