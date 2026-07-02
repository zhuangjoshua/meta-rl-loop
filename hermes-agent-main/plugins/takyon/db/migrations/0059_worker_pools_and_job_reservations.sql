-- 0059: ClaimScope reservation (modularization plan Stage 2, UC1).
--
-- Durable session ownership of jobs replaces the payload-hint affinity triangle
-- (TAKYON_PREFERRED_WORKER_ID_PREFIX env + sidecar file + payload->>'preferred_worker_id_prefix'
-- LIKE-matching in claim_one). Two pieces, both additive and idempotent:
--
--   1. worker_pools — the pool registry: one row per live worker pool (an SSH console's local
--      Mac pool, the VPS worker service, a dashboard embedded drain). Heartbeated lease;
--      Stage 4 extends this same table into the replica/node registry (capabilities jsonb).
--   2. jobs reservation columns — a queued job may be RESERVED for a pool. The claim predicate
--      becomes an indexed equality instead of a payload regex:
--        'any'         — unreserved (NULL pool) or policy default: exactly today's behavior.
--        'after_lease' — reserved pool first; spillable to anyone once reservation_expires_at
--                        passes (reproduces today's grace-window behavior as a config value).
--        'strict'      — claimable ONLY by the reserved pool while that pool's registry lease
--                        is alive; if the owning pool dies (lease lapses), the job SPILLS
--                        rather than strands.
--
-- Mixed-version protocol: DB migrates first — old code never reads these columns and writes
-- rows with NULL/'any', which the new predicate treats exactly as today. No down-migration;
-- rollback = revert code, leave schema (columns are nullable/defaulted).
--
-- OPERATOR-PLANE only: worker_pools is claim/topology state. The subuser/app runtime role
-- must not read or write it (defense in depth below, same pattern as 0057).

begin;

create table if not exists worker_pools (
    pool_id          text primary key,
    owner_user_id    text,
    session_key      text,
    hostname         text not null,
    exclusive        boolean not null default false,
    concurrency      int not null default 1,
    status           text not null default 'active'
        check (status in ('joining', 'active', 'draining', 'decommissioned', 'lost')),
    capabilities     jsonb not null default '{}'::jsonb,
    lease_expires_at timestamptz not null,
    registered_at    timestamptz not null default now(),
    updated_at       timestamptz not null default now()
);

create index if not exists worker_pools_live_idx
    on worker_pools (hostname, owner_user_id)
    where status in ('joining', 'active', 'draining');

alter table jobs add column if not exists reserved_pool_id text;
alter table jobs add column if not exists reservation_policy text not null default 'any';
alter table jobs add column if not exists reservation_expires_at timestamptz;
alter table jobs add column if not exists reservation_lease_seconds double precision;

-- Constraint added separately so re-runs stay idempotent (ADD COLUMN IF NOT EXISTS cannot
-- carry a named check that would collide on replay).
do $$
begin
    if not exists (
        select 1 from pg_constraint where conname = 'jobs_reservation_policy_check'
    ) then
        alter table jobs add constraint jobs_reservation_policy_check
            check (reservation_policy in ('strict', 'after_lease', 'any'));
    end if;
end $$;

create index if not exists jobs_reserved_pool_idx
    on jobs (reserved_pool_id)
    where status = 'queued';

-- RLS (mirrors 0052's control-plane convention, observed live on prod where RLS is enabled
-- by default for new public tables): enable RLS and grant the authority roles the standard
-- takyon_rls_bypass() policies. The subuser/app roles get NO policy — deny-by-absence is the
-- defense-in-depth that keeps the claim/topology plane unreachable from the product plane.
alter table worker_pools enable row level security;

do $$
declare
    role_name text;
    authority_roles text[] := array[
        'takyon_runtime', 'takyon_operator_runtime', 'takyon_safebox_authority', 'takyon_migration'
    ];
begin
    if to_regprocedure('takyon_rls_bypass()') is null then
        return;  -- pre-0039 database (e.g. a bare rig) — policies land when 0039/0052 replay
    end if;
    foreach role_name in array authority_roles loop
        if not exists (select 1 from pg_roles where rolname = role_name) then
            continue;
        end if;
        execute format('drop policy if exists %I on public.worker_pools', 'takyon_' || role_name || '_guc_bypass');
        execute format(
            'create policy %I on public.worker_pools for all to %I '
            'using (takyon_rls_bypass()) with check (takyon_rls_bypass())',
            'takyon_' || role_name || '_guc_bypass',
            role_name
        );
    end loop;
end $$;

-- Role posture (mirrors 0057): operator-plane writers only; deny the subuser plane + PUBLIC.
do $$
declare
    wr text;
begin
    execute 'revoke all on table public.worker_pools from public';
    if exists (select 1 from pg_roles where rolname = 'takyon_app_runtime') then
        execute 'revoke all on table public.worker_pools from takyon_app_runtime';
    end if;
    foreach wr in array array['takyon_operator_runtime', 'takyon_safebox_authority', 'takyon_migration', 'takyon_runtime'] loop
        if exists (select 1 from pg_roles where rolname = wr) then
            execute format('grant select, insert, update, delete on table public.worker_pools to %I', wr);
        end if;
    end loop;
end $$;

commit;
