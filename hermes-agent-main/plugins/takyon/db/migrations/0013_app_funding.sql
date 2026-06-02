-- 0013_app_funding.sql
-- Runtime-side product funding rail:
--   * per-business subsidy pool with a cached remaining balance, and
--   * append-only funding entries that record how each request was split between
--     sub-user plan-funded credits and business subsidy.
--
-- This does NOT replace 0007 app_usage_budget. app_usage remains the outer business kill-switch;
-- this rail only decides who paid inside that safety envelope.
--
-- Idempotent DDL. Clean public schema only.
do $$
begin
    if to_regclass('public.app_business_subsidy_accounts') is not null
       and not exists (
           select 1 from information_schema.columns
           where table_schema = 'public'
             and table_name   = 'app_business_subsidy_accounts'
             and column_name  = 'business_slug'
       )
    then
        raise exception
            'public.app_business_subsidy_accounts exists but is not the takyon shape (no business_slug). '
            'Inspect and remove it before applying takyon migrations. '
            'See mediationplan.md > Build Discipline (Gate 1).'
            using errcode = 'feature_not_supported';
    end if;
    if to_regclass('public.app_funding_entries') is not null
       and not exists (
           select 1 from information_schema.columns
           where table_schema = 'public'
             and table_name   = 'app_funding_entries'
             and column_name  = 'business_slug'
       )
    then
        raise exception
            'public.app_funding_entries exists but is not the takyon shape (no business_slug). '
            'Inspect and remove it before applying takyon migrations. '
            'See mediationplan.md > Build Discipline (Gate 1).'
            using errcode = 'feature_not_supported';
    end if;
end $$;

do $$ begin
    create type app_funding_bucket as enum ('user_credit', 'subsidy');
exception when duplicate_object then null; end $$;

do $$ begin
    create type app_funding_entry_kind as enum ('grant', 'reserve', 'settle', 'release');
exception when duplicate_object then null; end $$;

create table if not exists app_business_subsidy_accounts (
    business_slug     text primary key references businesses (slug) on delete cascade,
    balance_microusd  bigint not null default 0 check (balance_microusd >= 0),
    created_at        timestamptz not null default now(),
    updated_at        timestamptz not null default now()
);

create table if not exists app_funding_entries (
    id                    bigserial primary key,
    business_slug         text not null references businesses (slug) on delete cascade,
    app_user_id           uuid references app_users (id) on delete set null,
    plan_key              text,
    bucket                app_funding_bucket not null,
    kind                  app_funding_entry_kind not null,
    amount_microusd       bigint not null check (amount_microusd >= 0),
    balance_after_microusd bigint check (balance_after_microusd is null or balance_after_microusd >= 0),
    period_start          timestamptz not null,
    reservation_key       text,
    idempotency_key       text not null unique,
    metadata              jsonb not null default '{}'::jsonb,
    created_at            timestamptz not null default now()
);

create index if not exists app_funding_entries_business_user_period_idx
    on app_funding_entries (business_slug, app_user_id, period_start, created_at desc);
create index if not exists app_funding_entries_reservation_idx
    on app_funding_entries (reservation_key)
    where reservation_key is not null;
create unique index if not exists app_funding_entries_reserve_bucket_idx
    on app_funding_entries (reservation_key, bucket)
    where kind = 'reserve' and reservation_key is not null;
