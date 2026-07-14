-- Code-owned, durable bootstrap phase checkpoints.
--
-- The model cannot write this table.  The operator worker records only
-- runtime-validated artifacts and tool receipts while holding the phase row
-- lock, so assistant prose is never a completion signal.

begin;

create table if not exists public.bootstrap_phase_runs (
    job_id               uuid primary key references public.jobs (id) on delete cascade,
    sdk_session_id       uuid not null,
    owner_user_id        uuid not null references public.users (id) on delete cascade,
    business_slug        text not null references public.businesses (slug) on delete cascade,
    input_sha256         text not null check (input_sha256 ~ '^[0-9a-f]{64}$'),
    immutable_inputs     jsonb not null check (jsonb_typeof(immutable_inputs) = 'object'),
    phase_plan           jsonb not null check (
        jsonb_typeof(phase_plan) = 'array' and jsonb_array_length(phase_plan) = 9
    ),
    phase_idempotency    jsonb not null check (jsonb_typeof(phase_idempotency) = 'object'),
    current_phase        text,
    completed_phases     jsonb not null default '[]'::jsonb
                              check (jsonb_typeof(completed_phases) = 'array'),
    phase_evidence       jsonb not null default '{}'::jsonb
                              check (jsonb_typeof(phase_evidence) = 'object'),
    phase_receipts       jsonb not null default '{}'::jsonb
                              check (jsonb_typeof(phase_receipts) = 'object'),
    phase_attempts       jsonb not null default '{}'::jsonb
                              check (jsonb_typeof(phase_attempts) = 'object'),
    first_job_attempt    integer not null check (first_job_attempt >= 1),
    last_job_attempt     integer not null check (last_job_attempt >= first_job_attempt),
    status               text not null default 'running'
                              check (status in ('running', 'completed')),
    created_at           timestamptz not null default now(),
    updated_at           timestamptz not null default now()
);

create index if not exists bootstrap_phase_runs_business_idx
    on public.bootstrap_phase_runs (business_slug, created_at desc);

create or replace function public.takyon_guard_bootstrap_phase_run_immutables()
returns trigger
language plpgsql
set search_path = pg_catalog, public
as $$
begin
    if new.job_id is distinct from old.job_id
       or new.sdk_session_id is distinct from old.sdk_session_id
       or new.owner_user_id is distinct from old.owner_user_id
       or new.business_slug is distinct from old.business_slug
       or new.input_sha256 is distinct from old.input_sha256
       or new.immutable_inputs is distinct from old.immutable_inputs
       or new.phase_plan is distinct from old.phase_plan
       or new.phase_idempotency is distinct from old.phase_idempotency
       or new.first_job_attempt is distinct from old.first_job_attempt then
        raise exception 'bootstrap phase run immutable identity/input changed';
    end if;
    new.updated_at := now();
    return new;
end;
$$;

drop trigger if exists bootstrap_phase_runs_immutable_guard
    on public.bootstrap_phase_runs;
create trigger bootstrap_phase_runs_immutable_guard
before update on public.bootstrap_phase_runs
for each row execute function public.takyon_guard_bootstrap_phase_run_immutables();

alter table public.bootstrap_phase_runs enable row level security;
alter table public.bootstrap_phase_runs force row level security;
revoke all on public.bootstrap_phase_runs from public;

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
                'revoke all on table public.bootstrap_phase_runs from %I', role_name
            );
        end if;
    end loop;

    if exists (select 1 from pg_roles where rolname = 'takyon_operator_runtime') then
        grant select, insert, update on public.bootstrap_phase_runs
            to takyon_operator_runtime;
        drop policy if exists bootstrap_phase_runs_operator_bypass
            on public.bootstrap_phase_runs;
        create policy bootstrap_phase_runs_operator_bypass
            on public.bootstrap_phase_runs for all to takyon_operator_runtime
            using (takyon_rls_bypass()) with check (takyon_rls_bypass());
    end if;

    if exists (select 1 from pg_roles where rolname = 'takyon_migration') then
        grant all privileges on public.bootstrap_phase_runs to takyon_migration;
        drop policy if exists bootstrap_phase_runs_migration_bypass
            on public.bootstrap_phase_runs;
        create policy bootstrap_phase_runs_migration_bypass
            on public.bootstrap_phase_runs for all to takyon_migration
            using (takyon_rls_bypass()) with check (takyon_rls_bypass());
    end if;
end $$;

commit;
