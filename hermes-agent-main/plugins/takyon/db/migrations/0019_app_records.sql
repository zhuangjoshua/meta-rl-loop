-- 0019_app_records.sql
-- Generic durable product records rail:
--   * one shared table for normal saved product state per (business, sub-user)
--   * generic typed records, not product-specific tables and not browser-only state
--   * closes the real MVP loop: save, list, reopen, and delete customer records honestly
--
-- This is intentionally substrate-level. Product-specific semantics still belong in the
-- business-owned surface contract + generated app source. The shared runtime only owns the
-- durable per-subuser record rail that those products can build on.
do $$
begin
    if to_regclass('public.app_records') is not null
       and (
           not exists (
               select 1 from information_schema.columns
               where table_schema = 'public'
                 and table_name   = 'app_records'
                 and column_name  = 'business_slug'
           )
           or not exists (
               select 1 from information_schema.columns
               where table_schema = 'public'
                 and table_name   = 'app_records'
                 and column_name  = 'record_type'
           )
       )
    then
        raise exception
            'public.app_records exists but is not the takyon shape (must be business-scoped with record_type). '
            'Inspect and remove it before applying takyon migrations. '
            'See mediationplan.md > Build Discipline (Gate 1).'
            using errcode = 'feature_not_supported';
    end if;
end $$;

create unique index if not exists app_users_business_id_idx
    on app_users (business_slug, id);

create table if not exists app_records (
    id            text not null,
    business_slug text not null references businesses (slug) on delete cascade,
    app_user_id   uuid not null,
    record_type   text not null check (length(record_type) > 0),
    title         text,
    data          jsonb not null default '{}'::jsonb,
    metadata      jsonb not null default '{}'::jsonb,
    created_at    timestamptz not null default now(),
    updated_at    timestamptz not null default now(),
    primary key (business_slug, app_user_id, record_type, id),
    foreign key (business_slug, app_user_id) references app_users (business_slug, id) on delete cascade
);

create index if not exists app_records_user_type_idx
    on app_records (business_slug, app_user_id, record_type, updated_at desc);
