-- 0020_app_directory_and_connections.sql
-- Generic two-sided product substrate:
--   * authenticated opt-in directory projection on top of app_user_profiles
--   * directed cross-user state (like/pass/block) in app_connections
--
-- This is intentionally generic rather than dating-specific. The shared
-- runtime owns only consented discoverability and directed relationship state.
-- Product semantics, copy, and UI still belong to the business-owned surface
-- contract and generated app source.

alter table if exists app_user_profiles
    add column if not exists directory_enabled boolean not null default false;

alter table if exists app_user_profiles
    add column if not exists directory_profile jsonb not null default '{}'::jsonb;

alter table if exists app_user_profiles
    add column if not exists directory_updated_at timestamptz;

do $$
begin
    if to_regclass('public.app_connections') is not null
       and (
           not exists (
               select 1 from information_schema.columns
               where table_schema = 'public'
                 and table_name   = 'app_connections'
                 and column_name  = 'source_app_user_id'
           )
           or not exists (
               select 1 from information_schema.columns
               where table_schema = 'public'
                 and table_name   = 'app_connections'
                 and column_name  = 'target_app_user_id'
           )
       )
    then
        raise exception
            'public.app_connections exists but is not the takyon shape (must be business-scoped with source_app_user_id and target_app_user_id). '
            'Inspect and remove it before applying takyon migrations. '
            'See mediationplan.md > Build Discipline (Gate 1).'
            using errcode = 'feature_not_supported';
    end if;
end $$;

create table if not exists app_connections (
    business_slug       text not null references businesses (slug) on delete cascade,
    source_app_user_id  uuid not null,
    target_app_user_id  uuid not null,
    state               text not null check (state in ('like', 'pass', 'block')),
    created_at          timestamptz not null default now(),
    updated_at          timestamptz not null default now(),
    primary key (business_slug, source_app_user_id, target_app_user_id),
    foreign key (business_slug, source_app_user_id) references app_users (business_slug, id) on delete cascade,
    foreign key (business_slug, target_app_user_id) references app_users (business_slug, id) on delete cascade,
    check (source_app_user_id <> target_app_user_id)
);

create index if not exists app_connections_source_state_idx
    on app_connections (business_slug, source_app_user_id, state, updated_at desc);

create index if not exists app_connections_target_state_idx
    on app_connections (business_slug, target_app_user_id, state, updated_at desc);

create index if not exists app_user_profiles_directory_idx
    on app_user_profiles (business_slug, directory_enabled, coalesce(directory_updated_at, updated_at) desc);
