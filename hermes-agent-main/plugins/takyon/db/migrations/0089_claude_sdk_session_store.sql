-- Cross-host durable Claude Agent SDK transcript mirror.
--
-- Only the trusted operator Python parent receives table authority. The Node
-- SDK subprocess reaches this store solely through an inherited private socket,
-- so no database DSN or runtime role crosses the model process boundary.

begin;

create table if not exists public.agent_sdk_session_entries (
    id              bigint generated always as identity primary key,
    owner_user_id   uuid not null references public.users (id) on delete cascade,
    business_slug   text references public.businesses (slug) on delete cascade,
    project_key     text not null check (length(project_key) between 1 and 512),
    session_id      uuid not null,
    subpath         text not null default '' check (length(subpath) <= 512),
    entry_index     integer not null check (entry_index >= 0),
    entry_uuid      text,
    entry           jsonb not null check (
        jsonb_typeof(entry) = 'object'
        and jsonb_typeof(entry -> 'type') = 'string'
        and length(entry ->> 'type') > 0
        and pg_column_size(entry) <= 1048576
    ),
    created_at      timestamptz not null default now()
);

create unique index if not exists agent_sdk_session_entries_uuid_uidx
    on public.agent_sdk_session_entries
        (owner_user_id, business_slug, project_key, session_id, subpath, entry_uuid)
    nulls not distinct
    where entry_uuid is not null;

create index if not exists agent_sdk_session_entries_load_idx
    on public.agent_sdk_session_entries
        (owner_user_id, business_slug, project_key, session_id, subpath, id);

alter table public.agent_sdk_session_entries enable row level security;
alter table public.agent_sdk_session_entries force row level security;

do $$
declare
    role_name text;
begin
    foreach role_name in array array[
        'takyon_runtime', 'takyon_operator_runtime', 'takyon_app_runtime',
        'takyon_app', 'takyon_safebox_authority', 'safebox'
    ] loop
        if exists (select 1 from pg_roles where rolname = role_name) then
            execute format(
                'revoke all on table public.agent_sdk_session_entries from %I', role_name
            );
            execute format(
                'revoke all on sequence public.agent_sdk_session_entries_id_seq from %I', role_name
            );
        end if;
    end loop;

    if exists (select 1 from pg_roles where rolname = 'takyon_operator_runtime') then
        grant select, insert, delete on public.agent_sdk_session_entries
            to takyon_operator_runtime;
        grant usage, select on sequence public.agent_sdk_session_entries_id_seq
            to takyon_operator_runtime;
        drop policy if exists takyon_agent_sdk_session_operator_bypass
            on public.agent_sdk_session_entries;
        create policy takyon_agent_sdk_session_operator_bypass
            on public.agent_sdk_session_entries
            for all to takyon_operator_runtime
            using (takyon_rls_bypass())
            with check (takyon_rls_bypass());
    end if;

    if exists (select 1 from pg_roles where rolname = 'takyon_migration') then
        grant all privileges on public.agent_sdk_session_entries to takyon_migration;
        grant all privileges on sequence public.agent_sdk_session_entries_id_seq
            to takyon_migration;
        drop policy if exists takyon_agent_sdk_session_migration_bypass
            on public.agent_sdk_session_entries;
        create policy takyon_agent_sdk_session_migration_bypass
            on public.agent_sdk_session_entries
            for all to takyon_migration
            using (takyon_rls_bypass())
            with check (takyon_rls_bypass());
    end if;
end $$;

revoke all on public.agent_sdk_session_entries from public;
revoke all on sequence public.agent_sdk_session_entries_id_seq from public;

commit;
