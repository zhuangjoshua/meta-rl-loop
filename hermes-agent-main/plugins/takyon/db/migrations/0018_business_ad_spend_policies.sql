-- 0018_business_ad_spend_policies.sql
-- Guarded backend authority for live business ad-campaign spend policy.
--
-- This table intentionally stores the mutable live-campaign authority that must NOT live in
-- business-editable workspace files. Workspace receipts remain operator-facing artifacts, but the
-- enforced spend cap, pacing, schedule, reservation key, and provider ids for live campaigns live
-- here behind backend/Safebox-gated mutations.
--
-- Idempotent DDL. Clean public schema only.
do $$
begin
    if to_regclass('public.business_ad_spend_policies') is not null
       and not exists (
           select 1 from information_schema.columns
           where table_schema = 'public'
             and table_name   = 'business_ad_spend_policies'
             and column_name  = 'business_slug'
       )
    then
        raise exception
            'public.business_ad_spend_policies exists but is not the takyon shape (no business_slug). '
            'Inspect and remove it before applying takyon migrations. '
            'See mediationplan.md > Build Discipline (Gate 1).'
            using errcode = 'feature_not_supported';
    end if;
end $$;

create table if not exists business_ad_spend_policies (
    business_slug            text not null references businesses (slug) on delete cascade,
    channel                  text not null,
    slug                     text not null,
    reservation_key          text not null unique,
    reserved_credits         bigint not null check (reserved_credits >= 0),
    daily_budget_cents       bigint not null check (daily_budget_cents > 0),
    total_budget_cents       bigint not null check (total_budget_cents > 0),
    start_at                 timestamptz not null,
    end_at                   timestamptz not null,
    provider_account_id      text,
    provider_campaign_id     text,
    provider_group_id        text,
    provider_ad_id           text,
    provider_post_id         text,
    status                   text not null default 'reserved',
    last_synced_spend_cents  bigint not null default 0 check (last_synced_spend_cents >= 0),
    settled_credits          bigint not null default 0 check (settled_credits >= 0),
    metadata                 jsonb not null default '{}'::jsonb,
    created_at               timestamptz not null default now(),
    updated_at               timestamptz not null default now(),
    primary key (business_slug, channel, slug),
    check (channel in ('meta', 'reddit')),
    check (end_at > start_at)
);

create index if not exists business_ad_spend_policies_status_idx
    on business_ad_spend_policies (status, updated_at desc);
create index if not exists business_ad_spend_policies_provider_campaign_idx
    on business_ad_spend_policies (channel, provider_campaign_id)
    where provider_campaign_id is not null;
