-- 0003_rate_limits.sql
-- Phase 3: per-user fixed-window rate limiting for the control-plane boundary.
--
-- The opaque API key is the whole per-user surface, so abuse control lives at the
-- same grain: one counter row per (user, epoch-aligned window). check_rate_limit
-- (plugins/takyon/rate_limit.py) does a single atomic upsert-increment against this
-- table; the (user_id, window_start) primary key is what makes that one statement
-- both the lock and the dedupe — concurrent requests for the same user collapse onto
-- the same row and cannot race past the cap.
--
-- Idempotent DDL: safe to run repeatedly. Clean `public` only (local test DB, or
-- live Supabase AFTER the polsia2 teardown).
--
-- REPLACE guard (robustness #1 — mediationplan.md): api_rate_limits is net-new to
-- takyon (no known polsia2 table of this name), but `create table if not exists`
-- would SILENTLY bind to a differently-shaped pre-existing table if one ever existed.
-- Fail loud instead if a non-takyon `api_rate_limits` is present (takyon's has
-- window_start). Trivial pass on a clean DB and on re-runs. Mirrors the guards in
-- 0001_identity_spine.sql and 0002_ledgers.sql.
do $$
begin
    if to_regclass('public.api_rate_limits') is not null
       and not exists (
           select 1 from information_schema.columns
           where table_schema = 'public'
             and table_name   = 'api_rate_limits'
             and column_name  = 'window_start'
       )
    then
        raise exception
            'public.api_rate_limits exists but is not the takyon shape (no '
            'window_start). A differently-shaped table of this name is unexpected; '
            'inspect and remove it before applying takyon migrations. See '
            'mediationplan.md > Ground Truth (REPLACE decision).'
            using errcode = 'feature_not_supported';
    end if;
end $$;

-- One row per (user, window). window_start is the epoch-aligned start of the window
-- (floor(now/w)*w), so all callers agree on which window a request belongs to.
-- request_count is the number of requests counted into that window so far. Old rows
-- are pruned by prune_rate_limits; they never affect a decision once the window rolls.
create table if not exists api_rate_limits (
    user_id        uuid not null references users (id) on delete cascade,
    window_start   timestamptz not null,
    request_count  bigint not null default 0,
    primary key (user_id, window_start),
    constraint api_rate_limits_count_nonneg check (request_count >= 0)
);

-- Supports prune_rate_limits' range delete on window_start.
create index if not exists api_rate_limits_window_idx
    on api_rate_limits (window_start);
