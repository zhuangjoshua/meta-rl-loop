-- 0024_app_media.sql
-- Product media rail: bounded per-subuser image storage metadata.
--   * one shared table for uploaded media metadata per (business, sub-user)
--   * raw bytes live in the storage backend keyed media/<business>/<media_id>
--   * the rail owns identity, quotas, metering, and receipts; this table is just the index
--
-- Substrate-level, like app_records. Product-specific semantics stay in the surface
-- contract + generated app source; the shared runtime owns only this durable index.
do $$
begin
    if to_regclass('public.app_media') is not null
       and (
           not exists (
               select 1 from information_schema.columns
               where table_schema = 'public'
                 and table_name   = 'app_media'
                 and column_name  = 'business_slug'
           )
           or not exists (
               select 1 from information_schema.columns
               where table_schema = 'public'
                 and table_name   = 'app_media'
                 and column_name  = 'media_id'
           )
       )
    then
        raise exception
            'public.app_media exists but is not the takyon shape (must be business-scoped with media_id). '
            'Inspect and remove it before applying takyon migrations.'
            using errcode = 'feature_not_supported';
    end if;
end $$;

create table if not exists app_media (
    id            text primary key,
    business_slug text not null references businesses(slug) on delete cascade,
    app_user_id   text not null,
    media_id      text not null,
    filename      text,
    mime          text not null,
    size_bytes    bigint not null,
    storage_key   text not null,
    created_at    timestamptz not null default now(),
    unique (business_slug, media_id)
);

create index if not exists app_media_business_user_idx
    on app_media (business_slug, app_user_id);
