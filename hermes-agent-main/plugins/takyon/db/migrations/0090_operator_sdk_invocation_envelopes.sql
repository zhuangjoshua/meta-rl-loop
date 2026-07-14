-- Safebox-owned cumulative spend envelopes for primary Claude Agent SDK turns.
--
-- A signed operator.session capability binds one row's identity and ceilings.
-- Every provider call locks the invocation row before claiming estimate, so
-- concurrent calls cannot collectively cross the total ceiling. No runtime
-- or operator role can write either authority table.

begin;

create table if not exists public.operator_sdk_invocations (
    invocation_id              uuid primary key,
    owner_user_id              uuid not null references public.users (id) on delete cascade,
    business_slug              text references public.businesses (slug) on delete cascade,
    total_ceiling_microusd     bigint not null check (total_ceiling_microusd > 0),
    per_call_ceiling_microusd  bigint not null check (
        per_call_ceiling_microusd > 0
        and per_call_ceiling_microusd <= total_ceiling_microusd
    ),
    expires_at                 timestamptz not null,
    created_at                 timestamptz not null default now(),
    check (expires_at > created_at)
);

create table if not exists public.operator_sdk_invocation_calls (
    invocation_id      uuid not null references public.operator_sdk_invocations (invocation_id)
                              on delete cascade,
    call_id             uuid not null,
    estimate_microusd   bigint not null check (estimate_microusd >= 0),
    actual_microusd     bigint check (
        actual_microusd is null
        or (actual_microusd >= 0 and actual_microusd <= estimate_microusd)
    ),
    status              text not null default 'held'
                        check (status in ('held', 'settled', 'released')),
    created_at          timestamptz not null default now(),
    finalized_at        timestamptz,
    primary key (invocation_id, call_id),
    check (
        (status = 'held' and actual_microusd is null and finalized_at is null)
        or (status = 'settled' and actual_microusd is not null and finalized_at is not null)
        or (status = 'released' and actual_microusd is null and finalized_at is not null)
    )
);

create index if not exists operator_sdk_invocation_calls_status_idx
    on public.operator_sdk_invocation_calls (invocation_id, status);

alter table public.operator_sdk_invocations enable row level security;
alter table public.operator_sdk_invocations force row level security;
alter table public.operator_sdk_invocation_calls enable row level security;
alter table public.operator_sdk_invocation_calls force row level security;

revoke all on public.operator_sdk_invocations from public;
revoke all on public.operator_sdk_invocation_calls from public;

do $$
declare
    role_name text;
begin
    foreach role_name in array array[
        'takyon_runtime', 'takyon_operator_runtime', 'takyon_app_runtime',
        'takyon_app', 'safebox'
    ] loop
        if exists (select 1 from pg_roles where rolname = role_name) then
            execute format(
                'revoke all on table public.operator_sdk_invocations from %I', role_name
            );
            execute format(
                'revoke all on table public.operator_sdk_invocation_calls from %I', role_name
            );
        end if;
    end loop;

    if exists (select 1 from pg_roles where rolname = 'takyon_safebox_authority') then
        grant select, insert, update on public.operator_sdk_invocations
            to takyon_safebox_authority;
        grant select, insert, update on public.operator_sdk_invocation_calls
            to takyon_safebox_authority;
        drop policy if exists operator_sdk_invocations_safebox_authority
            on public.operator_sdk_invocations;
        create policy operator_sdk_invocations_safebox_authority
            on public.operator_sdk_invocations for all to takyon_safebox_authority
            using (takyon_rls_bypass()) with check (takyon_rls_bypass());
        drop policy if exists operator_sdk_invocation_calls_safebox_authority
            on public.operator_sdk_invocation_calls;
        create policy operator_sdk_invocation_calls_safebox_authority
            on public.operator_sdk_invocation_calls for all to takyon_safebox_authority
            using (takyon_rls_bypass()) with check (takyon_rls_bypass());
    end if;

    if exists (select 1 from pg_roles where rolname = 'takyon_migration') then
        grant all privileges on public.operator_sdk_invocations to takyon_migration;
        grant all privileges on public.operator_sdk_invocation_calls to takyon_migration;
        drop policy if exists operator_sdk_invocations_migration
            on public.operator_sdk_invocations;
        create policy operator_sdk_invocations_migration
            on public.operator_sdk_invocations for all to takyon_migration
            using (takyon_rls_bypass()) with check (takyon_rls_bypass());
        drop policy if exists operator_sdk_invocation_calls_migration
            on public.operator_sdk_invocation_calls;
        create policy operator_sdk_invocation_calls_migration
            on public.operator_sdk_invocation_calls for all to takyon_migration
            using (takyon_rls_bypass()) with check (takyon_rls_bypass());
    end if;
end $$;

commit;
