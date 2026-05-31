-- apply_pg_cron_dispatch.sql
-- The canonical wake-dispatch home for the Postgres runtime (Phase 6 design, applied at the Phase 8
-- serving flip). It schedules the in-DB function `dispatch_due_wakes()` (migration 0010) to run on a
-- fixed interval via pg_cron, so a due `wake_schedules` row enqueues exactly one `ceo_wake` job into
-- the worker queue WITHOUT any local file ticker or external scheduler. This is the "schedule, queue,
-- idempotency, locking, AND dispatch all live in the source of truth" property mediationplan.md's
-- Phase 6 section calls for.
--
-- THIS FILE IS DELIBERATELY NOT UNDER db/migrations/. The migration runner + test conftest sweep
-- db/migrations/*.sql on every run, and pg_cron is a Supabase-/superuser-only extension that is NOT
-- available in the local/CI Postgres the tests use — sweeping this in would fail every local run.
-- It is a separate, operator-gated apply step, run once against the live Supabase control plane at
-- cutover. The function it schedules (`dispatch_due_wakes`) is already migration-installed and
-- already Phase-6 tested directly; this file only wires the *interval trigger* around it.
--
-- ================================ GATED — SUPABASE ONLY ================================
-- Requires the `pg_cron` extension, which on Supabase is enabled from the dashboard
-- (Database -> Extensions -> pg_cron) or by a superuser. Running this where pg_cron is not
-- available raises loudly (invariant #8: blocked with a reason, never a silent no-op). pg_cron is
-- OPTIONAL infrastructure, not a credential: if you do not enable it, the identical SQL
-- (`select dispatch_due_wakes();`) can instead be driven by a CRON_SECRET-gated endpoint hit on an
-- interval, or an in-process ticker — all three run the SAME function. pg_cron is the preferred home
-- because it needs no extra always-on process.
-- ======================================================================================
--
-- SAFE BY CONSTRUCTION:
--   * Idempotent / re-runnable. `cron.schedule(jobname, ...)` upserts by name, so re-applying this
--     re-points the same single job rather than stacking duplicate schedules. The unschedule guard
--     below additionally clears any pre-existing job of this name first, so the end state is exactly
--     one job named 'takyon-dispatch-wakes' regardless of prior state.
--   * Enqueue-only. `dispatch_due_wakes()` only INSERTs due wakes into the jobs queue (idempotent on
--     the per-wake idempotency key); it does not run a CEO turn. Draining the queue (running the wake
--     turn) is the separately-deployed worker plane — scheduling dispatch here never executes a turn
--     by itself, so applying this on a runtime whose worker is not yet draining is harmless: due
--     wakes accumulate as queued jobs and are drained once the worker is mounted.
--
-- REVERT (operator, one statement): stop dispatch without dropping anything else:
--   select cron.unschedule('takyon-dispatch-wakes');

do $$
begin
  if to_regproc('dispatch_due_wakes') is null then
    raise exception
      'dispatch_due_wakes() is not installed; apply db/migrations/0010_jobs_and_wakes.sql first';
  end if;

  if not exists (select 1 from pg_extension where extname = 'pg_cron') then
    raise exception
      'pg_cron is not enabled in this database; enable it (Supabase: Database -> Extensions -> '
      'pg_cron) or drive select dispatch_due_wakes() from a CRON_SECRET endpoint/ticker instead';
  end if;

  -- Clear any prior job of this name so re-applying is a clean replace (one schedule, never stacked).
  perform cron.unschedule(jobid)
  from cron.job
  where jobname = 'takyon-dispatch-wakes';
end
$$;

-- Run the dispatcher every minute. Catch-up is bounded inside dispatch_due_wakes() itself
-- (greatest(now(), next_run_at)), so a host/cron outage fires ONE catch-up enqueue per due schedule
-- on the next tick, never a backlog of missed minutes.
select cron.schedule(
  'takyon-dispatch-wakes',
  '* * * * *',
  $cron$ select dispatch_due_wakes(); $cron$
);
