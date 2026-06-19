-- 0035_app_budget_weekly_period.sql
-- Product AI-spend budget period: MONTHLY → WEEKLY.
--
-- 0007_app_usage_budget.sql opened every business budget on a calendar-MONTH period
-- (`current_period_start default date_trunc('month', now())`, `current_period_end default
-- (date_trunc('month', now()) + interval '1 month')`). The product Plans & Billing surface now
-- presents the per-subuser AI allowance as a WEEKLY allocation that resets every week, so the
-- canonical period the usage gate aggregates over (`app_usage._committed_microusd`, which counts
-- events with `created_at >= current_period_start`) must be the ISO week, not the calendar month.
--
-- `app_usage._ensure_budget_locked` inserts only `business_slug` and relies on these column
-- DEFAULTS for the period, so changing the defaults here is sufficient for every NEW business
-- budget to open on a weekly period — no Python insert change is needed. Already-open rows are
-- realigned in-place below so they reset weekly too.
--
-- date_trunc('week', now()) is the start of the current ISO week (Monday 00:00 UTC); the period
-- end is one week later. This is the same date_trunc family already used for the month period, so
-- there is no new period machinery — only a narrower unit.
--
-- Idempotent: re-running re-asserts the weekly defaults and re-aligns rows whose period is not the
-- current ISO week. Safe no-op once the database is current.

-- 1) New-row defaults: weekly period.
alter table if exists app_budgets
    alter column current_period_start set default date_trunc('week', now());

alter table if exists app_budgets
    alter column current_period_end set default (date_trunc('week', now()) + interval '1 week');

-- 2) Realign already-open rows onto the current ISO week so they reset weekly. A row still sitting
--    on a (monthly or stale) period whose start is not this week's Monday is moved to the current
--    week. Committed spend is re-derived from app_usage_events by created_at, so moving the window
--    forward simply starts the customer's weekly allocation fresh from this week — the intended
--    "resets weekly" behavior. Rows already aligned to the current week are left untouched.
update app_budgets
set current_period_start = date_trunc('week', now()),
    current_period_end   = date_trunc('week', now()) + interval '1 week',
    updated_at           = now()
where current_period_start is distinct from date_trunc('week', now());
