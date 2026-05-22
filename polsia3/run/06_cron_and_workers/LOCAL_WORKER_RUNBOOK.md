# Local Worker Runbook

The local worker should be runnable from this repo.

Expected commands will be finalized during implementation, but the shape is:

```text
npm run worker
npm run worker:builds
npm run worker:cron
```

Responsibilities:
- load `.env.local`
- connect to Postgres
- claim workflow jobs
- run generated-app builds
- run local runner/Hermes for configured Hermes-backed workflows
- deploy generated apps
- persist logs/results

The worker must be restart-safe.

No job should depend on the terminal staying open without a persisted DB status.
