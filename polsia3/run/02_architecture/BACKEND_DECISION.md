# Backend Decision

## V0 Backend

Use:
- Vercel for Takyon platform UI/API
- Supabase/Postgres for source of truth
- local Mac worker for long jobs and generated-app builds
- Vercel CLI/API for deployments
- private GitHub repo for source/versioning

Do not use:
- Vercel Sandbox as builder
- Open Lovable server as builder
- fake local-only deployments as production success

## Why

Vercel serverless is good for short API requests, UI, webhooks, and cron ticks. It is not the right place for long, iterative generated-app builds.

Supabase/Postgres is mandatory. It is the control plane, queue, memory, billing/economics store, generated-app user store, project AI wallet store, and audit ledger.

The local Mac worker gives v0:
- fast iteration
- real filesystem/build access
- Hermes/local runner support
- Vercel CLI deploys
- fewer moving parts than a VPS

Hermes remains the required local runtime target for the scoped non-deterministic workflows where v2 used it. Current v3 reality: the v2 vendored runtime folder, setup/start scripts, skill sync behavior, `skills/argon-company-factory`, and Argon runtime adapter have been copied. Hermes-backed execution is still not verified until the local gateway process is configured/running and a real `/v1/runs` receipt is recorded. The latest browser E2E recorded Hermes as skipped and used the verified local-foundation provider path.

## Later Option

If v0 works, the local worker can be replaced by:
- VPS worker
- Fly/Render/Railway worker
- GitHub Actions worker for specific jobs

That should be a worker-interface change, not an architecture rewrite.
