-- 0012_business_creative_credits.sql
-- Business-scoped creative-credit ledger for fixed-price operator actions such as paid
-- creative generation and campaign staging.
--
-- This is deliberately distinct from:
--   * 0002 billing/custody — user money and sub-user payouts, and
--   * 0007 app_usage_budget — usage-metered product AI spend.
--
-- Creative credits are a separate BUSINESS-scoped product layer: the operator buys fixed
-- credit packs for a business, then future spendful tools reserve credits before doing
-- work, commit on success, and release on failure. Shape mirrors the existing ledgers:
-- one cached-balance account row per business plus an append-only entries table.
--
-- Idempotent DDL: safe to run repeatedly. Clean `public` only.
--
-- REPLACE guard: both tables are Takyon-owned and BUSINESS-scoped, so they must carry
-- `business_slug`. Fail loudly if a same-named non-Takyon table already exists.
do $$
begin
    if to_regclass('public.business_creative_credit_accounts') is not null
       and not exists (
           select 1 from information_schema.columns
           where table_schema = 'public'
             and table_name   = 'business_creative_credit_accounts'
             and column_name  = 'business_slug'
       )
    then
        raise exception
            'public.business_creative_credit_accounts exists but is not the takyon shape (no business_slug). '
            'Inspect and remove it before applying takyon migrations. '
            'See mediationplan.md > Build Discipline (Gate 1).'
            using errcode = 'feature_not_supported';
    end if;
    if to_regclass('public.business_creative_credit_entries') is not null
       and not exists (
           select 1 from information_schema.columns
           where table_schema = 'public'
             and table_name   = 'business_creative_credit_entries'
             and column_name  = 'business_slug'
       )
    then
        raise exception
            'public.business_creative_credit_entries exists but is not the takyon shape (no business_slug). '
            'Inspect and remove it before applying takyon migrations. '
            'See mediationplan.md > Build Discipline (Gate 1).'
            using errcode = 'feature_not_supported';
    end if;
end $$;

do $$ begin
    create type business_creative_credit_entry_kind as enum
        ('grant', 'reserve', 'commit', 'release');
exception when duplicate_object then null; end $$;

create table if not exists business_creative_credit_accounts (
    business_slug     text primary key references businesses (slug) on delete cascade,
    balance_credits   bigint not null default 0 check (balance_credits >= 0),
    created_at        timestamptz not null default now(),
    updated_at        timestamptz not null default now()
);

create table if not exists business_creative_credit_entries (
    id                   bigserial primary key,
    business_slug        text not null references businesses (slug) on delete cascade,
    kind                 business_creative_credit_entry_kind not null,
    amount_credits       bigint not null check (amount_credits >= 0),
    balance_after_credits bigint not null check (balance_after_credits >= 0),
    reservation_key      text,
    idempotency_key      text not null unique,
    metadata             jsonb not null default '{}'::jsonb,
    stripe_ref           text,
    created_at           timestamptz not null default now()
);

create index if not exists business_creative_credit_entries_business_idx
    on business_creative_credit_entries (business_slug, created_at desc);
create index if not exists business_creative_credit_entries_reservation_idx
    on business_creative_credit_entries (reservation_key)
    where reservation_key is not null;
create unique index if not exists business_creative_credit_entries_reserve_key_idx
    on business_creative_credit_entries (reservation_key)
    where kind = 'reserve';
