# Local Mac Worker

## Role

The local Mac worker is the v0 backend worker for long-running work.

For v0, this is the only intentionally manual runtime process after secrets/login/Vercel/Postgres are configured. The operator starts the worker on the local Mac; `app.fourmanifold.com` then queues jobs through Postgres and the worker claims them automatically.

It should:
- poll/claim workflow jobs from Postgres
- run generated-app builds
- run typecheck/build/smoke tests
- deploy with Vercel CLI/API
- update Postgres with status, logs, artifacts, and URLs
- call Hermes/local runner for configured scoped skill workflows

## Operator Commands

Start the v0 worker loop:

```bash
npm run worker:local
```

Recover interrupted running jobs and exit:

```bash
npm run worker:recover
```

Run only one job, optionally for a specific company:

```bash
WORKER_BUSINESS_ID=<company-id> npm run worker
```

If the worker is not running, Build Company still creates the company and queues the lanes, but no long-running lane executes until the worker starts.

## What Vercel Does Instead

Vercel hosts:
- UI
- API routes
- webhooks
- cron tick endpoint
- generated app proxy/session/checkout/AI gateway

Vercel cron should enqueue/dispatch due work but should not do long builds inline.

## Worker Safety

- One job claim uses row lock/lease.
- Stale locks are recoverable: the worker now runs stale-lock recovery before claims, and `npm run worker:recover` can force recovery after an interrupted local run.
- Logs are persisted.
- Failed build is `failed`, not `completed`.
- Missing config is `blocked`, not hidden.
- Worker can be restarted without losing job state.
