-- 0036_rate_limits_subjects.sql
-- Converge control-plane and app-plane abuse counters onto the one canonical
-- api_rate_limits table.
--
-- 0003 originally constrained api_rate_limits.user_id to top-level users(id)
-- because the limiter was control-plane-only. The sub-user plane now keys
-- /generate, /search, directory, and action limits on app_users.id. Keep the
-- column and primary key shape stable, but remove the users-only FK so this table
-- can hold any Takyon principal UUID without adding a second limiter.

do $$
declare
    constraint_name text;
begin
    if to_regclass('public.api_rate_limits') is null then
        return;
    end if;

    select con.conname
      into constraint_name
      from pg_constraint con
     where con.conrelid = 'public.api_rate_limits'::regclass
       and con.contype = 'f'
       and pg_get_constraintdef(con.oid) ilike '%references users%';

    if constraint_name is not null then
        execute format('alter table public.api_rate_limits drop constraint %I', constraint_name);
    end if;
end $$;

comment on table api_rate_limits is
    'Atomic fixed-window counters keyed by Takyon principal UUIDs: top-level users or app sub-users.';

comment on column api_rate_limits.user_id is
    'Compatibility column name; stores the UUID of the rate-limited principal, not necessarily users.id.';
