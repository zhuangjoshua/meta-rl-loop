-- 0023_app_action_schedules.sql
-- Shared schedule substrate for the actions runtime rail:
--   * one row per (business, action)
--   * cron cursor state owned by Takyon, not by action code
--   * shared by the Postgres worker dispatcher and the legacy local cron tick

do $$
begin
    if to_regclass('public.app_action_schedules') is not null
       and (
           not exists (
               select 1
               from information_schema.columns
               where table_schema = 'public'
                 and table_name   = 'app_action_schedules'
                 and column_name  = 'business_slug'
           )
           or not exists (
               select 1
               from information_schema.columns
               where table_schema = 'public'
                 and table_name   = 'app_action_schedules'
                 and column_name  = 'action_name'
           )
       )
    then
        raise exception
            'public.app_action_schedules exists but is not the takyon shape (must be business-scoped by action_name). '
            'Inspect and remove it before applying takyon migrations.'
            using errcode = 'feature_not_supported';
    end if;
end $$;

create table if not exists app_action_schedules (
    business_slug text not null references businesses (slug) on delete cascade,
    action_name   text not null check (length(action_name) > 0),
    cron_schedule text not null check (length(cron_schedule) > 0),
    enabled       boolean not null default true,
    next_run_at   timestamptz not null,
    last_run_at   timestamptz,
    last_status   text not null default '',
    last_error    text not null default '',
    created_at    timestamptz not null default now(),
    updated_at    timestamptz not null default now(),
    primary key (business_slug, action_name)
);

create index if not exists app_action_schedules_due_idx
    on app_action_schedules (enabled, next_run_at);
