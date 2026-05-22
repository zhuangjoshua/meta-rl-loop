# Cron Design

Use one host cron:

```text
GET /api/cron/dispatch
Authorization: Bearer $CRON_SECRET
```

The dispatcher reads `cron_jobs` from Postgres.

Jobs are configurable:
- active/paused
- interval or daily time
- default limit
- metadata
- next run time
- last result/error

Required safety:
- `FOR UPDATE SKIP LOCKED`
- no overlapping same job key
- stale lock recovery
- bounded per-tick limit
- last error visible

Vercel cron is only the tick. Postgres owns the schedule.

## Verified Runtime State - 2026-05-20

Actual cron rows:
- `agent_runner`: active interval job every 300 seconds. This is only a pulse/reconciliation tick; it does not execute workflow jobs by itself.
- `ceo_wakeup`: active daily job at `09:00:00` UTC, default limit 5. This enqueues CEO wakeup workflow jobs for active businesses.
- The Takyon dashboard now reads the `ceo_wakeup` cron row and shows the next scheduled CEO wakeup inside the company `In progress` panel as a visible `Next CEO Wakeup` row.

Important operational truth:
- Long-running workflow jobs execute only when the local Mac worker or a replacement worker process is running.
- If no worker is running, cron can enqueue or pulse, but queued jobs remain queued.
- Archived businesses are excluded from future CEO wakeup enqueueing because cron selects only `businesses.status = 'active'`.

Wanted next cron behavior:
- CEO wakes on an interval.
- CEO reads evidence: business state, docs, jobs, generated app status, social posts, leads, media jobs, receipts, errors, and prior reports.
- CEO decides which bounded lanes should run next.
- CEO enqueues those lanes, records a digest/report, then sleeps until the next wake.

Still pending:
- CEO wakeup does not yet autonomously choose and enqueue improvement/growth lanes on its scheduled run.
- Engagement learning is not implemented; X/media receipts exist, but no recurring metrics fetch/learning loop exists.

## Verified Dashboard Schedule Visibility - 2026-05-20

Implemented:
- `getTakyonDashboardModel` queries `cron_jobs` for `ceo_wakeup` and maps it into the dashboard task model as a scheduled cron row.
- `TakyonBusinessWorkspace` renders scheduled task rows with relative labels such as `next in 19h` for cron rows and `queued in 3m` for future workflow jobs.
- The Tasks modal uses the same schedule label, so the schedule is visible both in the compact `In progress` panel and the expanded task list.

Acceptance checks:
- `npm run typecheck` passed.
- Direct dashboard model check for local company `19687d0b-e1d4-4e78-a45c-2d11aa2a2161` returned `cron:ceo_wakeup` with scheduled time `2026-05-21T09:00:00.000Z`.
- In-app browser verification on `http://localhost:3000/dashboard/companies/19687d0b-e1d4-4e78-a45c-2d11aa2a2161` showed `Next CEO Wakeup next in 19h` in the `In progress` panel with no console errors.
