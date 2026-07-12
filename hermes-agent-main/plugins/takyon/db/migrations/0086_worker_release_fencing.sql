-- Release-fence every durable worker claim.
--
-- The zero SHA is a compatibility-open first-cutover/rollback sentinel: only legacy zero inserts
-- and claims remain valid until deploy quiesces the queue and activates a nonzero target. Activation
-- atomically repins untouched queued work and seals the target; after that, old or mismatched code is
-- rejected by the database. Attempted jobs are never repinned in either direction.

begin;

alter table public.worker_pools
    add column if not exists release_sha text not null default repeat('0', 40);
alter table public.worker_pools
    add column if not exists release_fence_drained_by_sha text;
alter table public.jobs
    add column if not exists required_release_sha text not null default repeat('0', 40);
alter table public.jobs
    add column if not exists claimed_release_sha text;
alter table public.jobs
    add column if not exists claimed_pool_id text;

create table if not exists public.worker_release_fence (
    singleton boolean primary key default true check (singleton),
    active_release_sha text not null default repeat('0', 40)
        check (active_release_sha ~ '^[0-9a-f]{40}$'),
    activated_at timestamptz,
    updated_at timestamptz not null default now()
);
insert into public.worker_release_fence (singleton)
values (true)
on conflict (singleton) do nothing;

do $$
begin
    if not exists (
        select 1 from pg_constraint where conname = 'worker_pools_release_sha_check'
    ) then
        alter table public.worker_pools add constraint worker_pools_release_sha_check
            check (release_sha ~ '^[0-9a-f]{40}$');
    end if;
    if not exists (
        select 1 from pg_constraint where conname = 'worker_pools_release_fence_drained_sha_check'
    ) then
        alter table public.worker_pools
            add constraint worker_pools_release_fence_drained_sha_check
            check (
                release_fence_drained_by_sha is null
                or release_fence_drained_by_sha ~ '^[0-9a-f]{40}$'
            );
    end if;
    if not exists (
        select 1 from pg_constraint where conname = 'jobs_required_release_sha_check'
    ) then
        alter table public.jobs add constraint jobs_required_release_sha_check
            check (required_release_sha ~ '^[0-9a-f]{40}$');
    end if;
    if not exists (
        select 1 from pg_constraint where conname = 'jobs_claimed_release_sha_check'
    ) then
        alter table public.jobs add constraint jobs_claimed_release_sha_check
            check (claimed_release_sha is null or claimed_release_sha ~ '^[0-9a-f]{40}$');
    end if;
end $$;

create index if not exists jobs_required_release_claim_idx
    on public.jobs (required_release_sha, status, created_at)
    where status = 'queued';

create or replace function public.takyon_get_worker_active_release()
returns text
language sql
security definer
stable
set search_path = pg_catalog, public, pg_temp
as $$
    select active_release_sha from public.worker_release_fence where singleton
$$;

create or replace function public.takyon_activate_worker_release(target_release_sha text)
returns jsonb
language plpgsql
security definer
set search_path = pg_catalog, public, pg_temp
as $$
declare
    previous_release_sha text;
    repinned_jobs integer := 0;
    drained_pools integer := 0;
begin
    if target_release_sha is null
       or target_release_sha !~ '^[0-9a-f]{40}$'
       or target_release_sha = repeat('0', 40) then
        raise exception 'activation requires a nonzero 40-character release SHA'
            using errcode = '22023';
    end if;
    select active_release_sha into previous_release_sha
    from public.worker_release_fence
    where singleton
    for update;
    if exists (select 1 from public.jobs where status = 'running') then
        raise exception 'cannot activate worker release while jobs are running'
            using errcode = '55006';
    end if;
    if exists (select 1 from public.jobs where status = 'queued' and attempts > 0) then
        raise exception 'cannot repin previously attempted queued jobs'
            using errcode = '55006';
    end if;
    if exists (
        select 1 from public.jobs
        where status = 'queued'
          and required_release_sha not in (
              previous_release_sha,
              repeat('0', 40),
              target_release_sha
          )
    ) then
        raise exception 'cannot activate with queued jobs pinned to an unrelated release'
            using errcode = '55006';
    end if;
    -- Untouched work from the prior active release (or the first-cutover zero sentinel) is safe to
    -- execute once on the deliberately activated target. Attempted work is never repinned.
    update public.jobs
    set required_release_sha = target_release_sha,
        updated_at = now()
    where status = 'queued'
      and attempts = 0
      and required_release_sha in (previous_release_sha, repeat('0', 40))
      and required_release_sha <> target_release_sha;
    get diagnostics repinned_jobs = row_count;
    update public.worker_pools
    set status = 'draining',
        release_fence_drained_by_sha = target_release_sha,
        updated_at = now()
    where release_sha <> target_release_sha
      and status in ('joining', 'active');
    get diagnostics drained_pools = row_count;
    update public.worker_release_fence
    set active_release_sha = target_release_sha,
        activated_at = now(),
        updated_at = now()
    where singleton;
    return jsonb_build_object(
        'previous_release_sha', previous_release_sha,
        'active_release_sha', target_release_sha,
        'repinned_jobs', repinned_jobs,
        'drained_pools', drained_pools
    );
end;
$$;

create or replace function public.takyon_restore_worker_release(
    target_release_sha text,
    previous_release_sha text
)
returns jsonb
language plpgsql
security definer
set search_path = pg_catalog, public, pg_temp
as $$
declare
    observed_release_sha text;
    repinned_jobs integer := 0;
    drained_pools integer := 0;
    reactivated_pools integer := 0;
begin
    if target_release_sha is null
       or target_release_sha !~ '^[0-9a-f]{40}$'
       or target_release_sha = repeat('0', 40) then
        raise exception 'legacy restore requires the nonzero target release SHA'
            using errcode = '22023';
    end if;
    if previous_release_sha is null or previous_release_sha !~ '^[0-9a-f]{40}$' then
        raise exception 'restore requires the prior 40-character release SHA or zero sentinel'
            using errcode = '22023';
    end if;
    select active_release_sha into observed_release_sha
    from public.worker_release_fence
    where singleton
    for update;
    if observed_release_sha is distinct from target_release_sha then
        raise exception 'cannot restore worker release: active release is %, expected %',
            observed_release_sha, target_release_sha
            using errcode = '55006';
    end if;
    if exists (
        select 1 from public.jobs
        where required_release_sha = target_release_sha
          and (status = 'running' or attempts > 0)
    ) then
        raise exception 'cannot restore legacy state after target work started'
            using errcode = '55006';
    end if;
    -- Quiesce only target-release pools that are currently claimable. Preserve a durable marker so
    -- the inverse cutover can revive exactly these pools, never a pool that was already draining.
    update public.worker_pools
    set status = 'draining',
        release_fence_drained_by_sha = previous_release_sha,
        updated_at = now()
    where release_sha = target_release_sha
      and status in ('joining', 'active');
    get diagnostics drained_pools = row_count;
    update public.jobs
    set required_release_sha = previous_release_sha,
        claimed_release_sha = null,
        claimed_pool_id = null,
        updated_at = now()
    where required_release_sha = target_release_sha
      and status = 'queued'
      and attempts = 0;
    get diagnostics repinned_jobs = row_count;
    update public.worker_release_fence
    set active_release_sha = previous_release_sha,
        activated_at = case
            when previous_release_sha = repeat('0', 40) then null
            else now()
        end,
        updated_at = now()
    where singleton;
    -- The singleton row remains locked throughout the restore. Once its active SHA is restored,
    -- only still-live pools that this exact target cutover drained may become claimable again.
    update public.worker_pools
    set status = 'active',
        release_fence_drained_by_sha = null,
        updated_at = now()
    where release_sha = previous_release_sha
      and status = 'draining'
      and release_fence_drained_by_sha = target_release_sha
      and lease_expires_at > now();
    get diagnostics reactivated_pools = row_count;
    -- Expired, lost, decommissioned, or independently draining pools are never revived. Clear only
    -- stale cutover markers now that this restore has consumed the matching transition.
    update public.worker_pools
    set release_fence_drained_by_sha = null,
        updated_at = now()
    where release_sha = previous_release_sha
      and release_fence_drained_by_sha = target_release_sha;
    return jsonb_build_object(
        'previous_release_sha', observed_release_sha,
        'active_release_sha', previous_release_sha,
        'repinned_jobs', repinned_jobs,
        'drained_pools', drained_pools,
        'reactivated_pools', reactivated_pools
    );
end;
$$;

create or replace function public.takyon_restore_worker_legacy_release(target_release_sha text)
returns jsonb
language sql
security definer
set search_path = pg_catalog, public, pg_temp
as $$
    select public.takyon_restore_worker_release(target_release_sha, repeat('0', 40))
$$;

create or replace function public.takyon_enforce_job_release_insert()
returns trigger
language plpgsql
security definer
set search_path = pg_catalog, public, pg_temp
as $$
declare
    active_sha text;
begin
    select active_release_sha into active_sha
    from public.worker_release_fence
    where singleton
    for share;
    if active_sha = repeat('0', 40) and new.required_release_sha = repeat('0', 40) then
        return new;
    end if;
    if active_sha is null or new.required_release_sha is distinct from active_sha then
        raise exception 'job release % is not the active release %',
            new.required_release_sha, coalesce(active_sha, '<unset>')
            using errcode = '23514';
    end if;
    return new;
end;
$$;

create or replace function public.takyon_enforce_job_release_claim()
returns trigger
language plpgsql
security definer
set search_path = pg_catalog, public, pg_temp
as $$
declare
    active_sha text;
begin
    if new.status = 'running' and old.status is distinct from 'running' then
        select active_release_sha into active_sha
        from public.worker_release_fence
        where singleton
        for share;
        if active_sha = repeat('0', 40)
           and new.required_release_sha = repeat('0', 40) then
            -- Compatibility-open first-cutover/rollback state: pre-0086 code can continue until
            -- deploy quiesces the queue and deliberately activates a nonzero release.
            return new;
        end if;
        if active_sha is null
           or active_sha = repeat('0', 40)
           or active_sha is distinct from new.required_release_sha then
            raise exception 'job release % is not active', new.required_release_sha
                using errcode = '23514';
        end if;
        if new.required_release_sha = repeat('0', 40)
           or new.claimed_release_sha is distinct from new.required_release_sha then
            raise exception 'job release claim mismatch: required %, claimed %',
                new.required_release_sha, new.claimed_release_sha
                using errcode = '23514';
        end if;
        if new.claimed_pool_id is not null and not exists (
            select 1
            from public.worker_pools p
            where p.pool_id = new.claimed_pool_id
              and p.release_sha = new.required_release_sha
              and p.status in ('joining', 'active')
              and p.lease_expires_at > now()
        ) then
            raise exception 'worker pool % is not live on required release %',
                new.claimed_pool_id, new.required_release_sha
                using errcode = '23514';
        end if;
    end if;
    return new;
end;
$$;

create or replace function public.takyon_enforce_worker_pool_release()
returns trigger
language plpgsql
security definer
set search_path = pg_catalog, public, pg_temp
as $$
declare
    active_sha text;
begin
    if new.status not in ('joining', 'active') then
        return new;
    end if;
    -- A fresh registration/heartbeat on the active release is independent proof of liveness; it
    -- must not retain a marker from an older cutover transaction.
    new.release_fence_drained_by_sha := null;
    select active_release_sha into active_sha
    from public.worker_release_fence
    where singleton
    for share;
    if active_sha = repeat('0', 40) and new.release_sha = repeat('0', 40) then
        return new;
    end if;
    if active_sha is null or new.release_sha is distinct from active_sha then
        raise exception 'worker pool release % is not the active release %',
            new.release_sha, coalesce(active_sha, '<unset>')
            using errcode = '23514';
    end if;
    return new;
end;
$$;

drop trigger if exists jobs_release_insert_fence on public.jobs;
create trigger jobs_release_insert_fence
before insert on public.jobs
for each row
execute function public.takyon_enforce_job_release_insert();

drop trigger if exists jobs_release_claim_fence on public.jobs;
create trigger jobs_release_claim_fence
before update of status on public.jobs
for each row
when (new.status = 'running' and old.status is distinct from 'running')
execute function public.takyon_enforce_job_release_claim();

drop trigger if exists worker_pools_release_fence on public.worker_pools;
create trigger worker_pools_release_fence
before insert or update of status, release_sha on public.worker_pools
for each row
execute function public.takyon_enforce_worker_pool_release();

alter table public.worker_release_fence enable row level security;

do $$
declare
    role_name text;
begin
    foreach role_name in array array[
        'takyon_runtime', 'takyon_operator_runtime', 'takyon_safebox_authority', 'takyon_migration'
    ] loop
        if not exists (select 1 from pg_roles where rolname = role_name) then
            continue;
        end if;
        execute format('drop policy if exists %I on public.worker_release_fence',
            'takyon_' || role_name || '_guc_bypass');
        execute format(
            'create policy %I on public.worker_release_fence for select to %I using (takyon_rls_bypass())',
            'takyon_' || role_name || '_guc_bypass', role_name
        );
    end loop;
end $$;

revoke all on table public.worker_release_fence from public;
revoke all on function public.takyon_activate_worker_release(text) from public;
revoke all on function public.takyon_get_worker_active_release() from public;
revoke all on function public.takyon_restore_worker_release(text, text) from public;
revoke all on function public.takyon_restore_worker_legacy_release(text) from public;
revoke all on function public.takyon_enforce_job_release_insert() from public;
revoke all on function public.takyon_enforce_job_release_claim() from public;
revoke all on function public.takyon_enforce_worker_pool_release() from public;
do $$
begin
    if exists (select 1 from pg_roles where rolname = 'takyon_migration') then
        grant select on table public.worker_release_fence to takyon_migration;
        grant execute on function public.takyon_activate_worker_release(text) to takyon_migration;
        grant execute on function public.takyon_get_worker_active_release() to takyon_migration;
        grant execute on function public.takyon_restore_worker_release(text, text) to takyon_migration;
        grant execute on function public.takyon_restore_worker_legacy_release(text) to takyon_migration;
    end if;
    if exists (select 1 from pg_roles where rolname = 'takyon_operator_runtime') then
        grant select on table public.worker_release_fence to takyon_operator_runtime;
    end if;
    if exists (select 1 from pg_roles where rolname = 'takyon_runtime') then
        grant select on table public.worker_release_fence to takyon_runtime;
    end if;
end $$;

commit;
