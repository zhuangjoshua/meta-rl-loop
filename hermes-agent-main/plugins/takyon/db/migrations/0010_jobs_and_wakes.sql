-- 0010_jobs_and_wakes.sql
-- Phase 6: the WORKER PLANE + scheduled CEO wakes, Postgres-native (mediationplan.md > Worker Plane).
--
-- WHAT THIS ADDS: two net-new tables and one in-DB dispatch function. Together they let heavy work
-- run as durable, idempotent, budget-gated jobs, and let recurring CEO wakes be *due-rows enqueued
-- into the same queue* — no systemd timer, no `.takyon/cron/jobs.json`, no `.tick.lock`.
--   * `jobs`           — the at-least-once queue. One job, one worker (SELECT … FOR UPDATE SKIP
--                        LOCKED). Result persisted atomically; partial = blocked/failed, never
--                        completed. Retries re-check budget (exhausted → blocked, not infinite).
--   * `wake_schedules` — one row per recurring CEO wake. THE SCHEDULE LIVES HERE, not in a file;
--                        `next_run_at` is advanced by the dispatcher, never by a local process.
--   * dispatch_due_wakes() — enqueue-when-due: insert one job per due schedule (exactly-once on the
--                        fired minute-window via jobs.idempotency_key) then advance the schedule.
--
-- GATE-1 (inspect-before-build, verified at source — repo AND live backend):
--   * policy.py already emits PolicyDecision(outcome="job", estimate_cents=…) (policy.py:328) with
--     NO consumer. This is the queue that receives it. policy DECIDES; the worker RESERVES.
--   * billing.py reserve(conn, user_id, estimate_cents, idempotency_key, *, business_slug, job_id)
--     already takes job_id (billing.py:180). The worker reuses the flow-A reserve→settle/refund
--     engine unchanged; `jobs.reserved_billing_entry_id` is the back-reference. NO new money path.
--   * Live Supabase (read-only catalog check, 105 public tables): the EXACT names `jobs` and
--     `wake_schedules` are ABSENT. The polsia2-era analogs — `cron_jobs` (conflates schedule+lock+
--     status on a job_key), `business_ceo_wakeups`, `workflow_jobs`, `media_generation_jobs` — are
--     disposable/orphaned (the retire script covers only the colliding roots; a full polsia2 wipe is
--     a separate gated step). They are NOT read or migrated. This design SEPARATES schedule
--     (wake_schedules) from queue (jobs) — the correct single-path REPLACE, not a parallel system.
--   * The legacy FILE cron (cron/scheduler.py::tick, cron/jobs.py jobs.json, gateway/run.py
--     _start_cron_ticker) is SQLite-era and is retired in Phase 8 — NOT here. This installs the
--     Postgres replacement ALONGSIDE it; nothing in this migration touches the file cron.
--
-- GATE-2 (credentials/providers): NONE new. Dispatch is gated by CRON_SECRET (already provisioned).
-- Heavy jobs run on the runtime worker (no external job runner). pg_cron is an in-DB Supabase
-- extension enabled at cutover; equivalently a CRON_SECRET-bearer endpoint runs the same SQL on an
-- interval. No new account, key, or paid service.
--
-- REPLACE guard (robustness #1 — mediationplan.md): mirror 0001-0009. `create table if not exists`
-- would SILENTLY bind to a differently-shaped pre-existing table if one existed. Fail loud instead,
-- keyed on a distinguishing takyon-shape column: `jobs.reserved_billing_entry_id` (the flow-A
-- back-reference no unrelated jobs-ish table carries) and `wake_schedules.next_run_at` (the
-- dispatcher-owned cursor central to this design).

-- ── jobs guard ───────────────────────────────────────────────────────────────────────────────────
do $$
begin
    if to_regclass('public.jobs') is not null
       and not exists (
           select 1 from information_schema.columns
           where table_schema = 'public'
             and table_name   = 'jobs'
             and column_name  = 'reserved_billing_entry_id'
       )
    then
        raise exception
            'public.jobs exists but is not the takyon shape (no reserved_billing_entry_id). '
            'Inspect and remove it before applying takyon migrations. '
            'See mediationplan.md > Build Discipline (Gate 1).'
            using errcode = 'feature_not_supported';
    end if;
end $$;

-- ── wake_schedules guard ─────────────────────────────────────────────────────────────────────────
do $$
begin
    if to_regclass('public.wake_schedules') is not null
       and not exists (
           select 1 from information_schema.columns
           where table_schema = 'public'
             and table_name   = 'wake_schedules'
             and column_name  = 'next_run_at'
       )
    then
        raise exception
            'public.wake_schedules exists but is not the takyon shape (no next_run_at). '
            'Inspect and remove it before applying takyon migrations. '
            'See mediationplan.md > Build Discipline (Gate 1).'
            using errcode = 'feature_not_supported';
    end if;
end $$;

-- ── jobs queue ─────────────────────────────────────────────────────────────────────────────────
-- business_slug CASCADE: a deleted business takes its queued/finished jobs with it. idempotency_key
-- UNIQUE is the at-least-once dedup key (enqueue is `on conflict do nothing`) AND the wake-window
-- guard. status is a CHECKED lifecycle (matches 0007's app_usage_events convention):
--   queued → running → completed | blocked | failed | cancelled
-- result/error are written ATOMICALLY with the terminal status (the job row is its own receipt);
-- `completed` is set ONLY when the work truly ran (invariant #8: partial = blocked/failed, never a
-- fake completion). reserved_billing_entry_id holds the flow-A reservation handle (the reservation_key
-- billing.settle/refund take) so a crashed worker's hold can be reconciled. attempts/max_attempts
-- bound retries; locked_by/locked_at are the claim stamp set under FOR UPDATE SKIP LOCKED.
create table if not exists jobs (
    id                        uuid primary key default gen_random_uuid(),
    business_slug             text not null references businesses (slug) on delete cascade,
    kind                      text not null check (length(kind) > 0),
    status                    text not null default 'queued'
                                  check (status in
                                      ('queued', 'running', 'completed', 'blocked', 'failed', 'cancelled')),
    idempotency_key           text not null unique check (length(idempotency_key) > 0),
    payload                   jsonb not null default '{}'::jsonb,
    result                    jsonb,
    error                     jsonb,
    reserved_billing_entry_id text,
    attempts                  int not null default 0 check (attempts >= 0),
    max_attempts              int not null default 5 check (max_attempts >= 1),
    locked_by                 text,
    locked_at                 timestamptz,
    created_at                timestamptz not null default now(),
    updated_at                timestamptz not null default now()
);

-- Drain pickup: oldest queued job (optionally filtered by kind), served by a partial index so the
-- claim scan never walks finished rows. created_at order = FIFO fairness.
create index if not exists jobs_queued_idx
    on jobs (created_at) where status = 'queued';
-- A business's job history (status board, audit).
create index if not exists jobs_business_idx
    on jobs (business_slug, created_at desc);

-- ── wake_schedules ───────────────────────────────────────────────────────────────────────────────
-- One row per recurring CEO wake, PK'd on business_slug (one schedule per business; CASCADE with the
-- business). enabled gates dispatch. interval_seconds is the cadence; next_run_at is the dispatcher's
-- cursor (advanced ONLY by dispatch_due_wakes, never by a local process). last_enqueued_at is the
-- observable proof the dispatcher fired. payload rides onto the enqueued job.
create table if not exists wake_schedules (
    business_slug    text primary key references businesses (slug) on delete cascade,
    kind             text not null default 'ceo_wake' check (length(kind) > 0),
    enabled          boolean not null default true,
    interval_seconds int not null check (interval_seconds > 0),
    next_run_at      timestamptz not null,
    last_enqueued_at timestamptz,
    payload          jsonb not null default '{}'::jsonb,
    created_at       timestamptz not null default now(),
    updated_at       timestamptz not null default now()
);

-- Dispatch pickup: due, enabled schedules only.
create index if not exists wake_schedules_due_idx
    on wake_schedules (next_run_at) where enabled;

-- ── dispatch_due_wakes() ─────────────────────────────────────────────────────────────────────────
-- Enqueue-when-due, then advance — atomically, in one statement. Returns the number of jobs newly
-- enqueued. Three coupled effects over the SAME locked `due` set:
--   1. `due`  locks every enabled, due schedule with FOR UPDATE SKIP LOCKED, so two concurrent
--             dispatchers (or pg_cron overlap) never process the same row.
--   2. `enq`  inserts one job per due row, keyed `wake:<slug>:<YYYYMMDDHH24MI>` of the *scheduled*
--             time. `on conflict (idempotency_key) do nothing` makes a replay or an overlapping tick
--             collapse to ONE job for that minute-window.
--   3. `adv`  advances next_run_at to greatest(now(), next_run_at) + interval. The greatest() is the
--             catch-up bound: a host that was down for N intervals fires ONE enqueue and realigns to
--             now — never an N-deep backlog. A missed minute self-heals because next_run_at <= now()
--             stays true until a dispatch advances it.
-- NB: `adv` is a data-modifying CTE. Postgres runs every data-modifying WITH clause exactly once and
-- to completion regardless of whether the primary query reads its output (see Postgres docs: WITH
-- Queries / Data-Modifying Statements), so the schedule is advanced even though the final SELECT only
-- counts `enq`.
create or replace function dispatch_due_wakes() returns integer
language plpgsql
as $$
declare
    enqueued_count integer;
begin
    with due as (
        select business_slug, kind, payload, next_run_at, interval_seconds
        from wake_schedules
        where enabled and next_run_at <= now()
        for update skip locked
    ),
    enq as (
        insert into jobs (business_slug, kind, idempotency_key, payload, status)
        select due.business_slug,
               due.kind,
               'wake:' || due.business_slug || ':' || to_char(due.next_run_at, 'YYYYMMDDHH24MI'),
               coalesce(due.payload, '{}'::jsonb),
               'queued'
        from due
        on conflict (idempotency_key) do nothing
        returning 1
    ),
    adv as (
        update wake_schedules w
        set next_run_at      = greatest(now(), w.next_run_at) + make_interval(secs => w.interval_seconds),
            last_enqueued_at = now(),
            updated_at       = now()
        from due
        where w.business_slug = due.business_slug
        returning 1
    )
    select count(*) into enqueued_count from enq;
    return enqueued_count;
end $$;
