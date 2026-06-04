-- 0014_app_profiles.sql
-- Generic product app profile rail:
--   * one business-scoped profile record per product sub-user, stored in the shared app plane
--   * owned by app_users (0005), not a second identity layer
--
-- This is deliberately narrow and parsimonious: auth/session still live in 0005, plans/entitlements
-- in 0006, usage in 0007, payments in 0008, funding in 0013. This table is only the durable
-- customer profile document that product apps can read/write honestly instead of inventing browser
-- state. It is GENERIC (display_name/headline/bio/attributes/metadata), so later product-specific
-- rails such as dating matches or messaging can build on the same app_user_id spine without
-- hardcoding them into identity.
--
-- Shared-table pattern matches the rest of the app plane: clean public schema, one top-level table,
-- tenant-scoped by business_slug. Not per-business tables, not JSON stuffed into app_users, and not
-- a new "sub_user_app" mapping because app_users already is that mapping.
do $$
begin
    if to_regclass('public.app_user_profiles') is not null
       and (
           not exists (
           select 1 from information_schema.columns
           where table_schema = 'public'
             and table_name   = 'app_user_profiles'
             and column_name  = 'business_slug'
           )
           or exists (
               select 1 from information_schema.columns
               where table_schema = 'public'
                 and table_name   = 'app_user_profiles'
                 and column_name  = 'app_user_id'
           )
       )
    then
        raise exception
            'public.app_user_profiles exists but is not the takyon shape (must be business-scoped and keyed directly by app_users.id). '
            'Inspect and remove it before applying takyon migrations. '
            'See mediationplan.md > Build Discipline (Gate 1).'
            using errcode = 'feature_not_supported';
    end if;
end $$;

create unique index if not exists app_users_business_id_idx
    on app_users (business_slug, id);

create table if not exists app_user_profiles (
    id            uuid primary key,
    business_slug text not null references businesses (slug) on delete cascade,
    display_name  text,
    headline      text,
    bio           text not null default '',
    attributes    jsonb not null default '{}'::jsonb,
    metadata      jsonb not null default '{}'::jsonb,
    created_at    timestamptz not null default now(),
    updated_at    timestamptz not null default now(),
    foreign key (business_slug, id) references app_users (business_slug, id) on delete cascade
);

create index if not exists app_user_profiles_business_idx
    on app_user_profiles (business_slug, updated_at desc);
