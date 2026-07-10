-- Freeze each active provider credential to the exact operator-approved canonical authority scope.
-- Existing active rows are deliberately returned to pending: their historical approvals predate the
-- canonical snapshot and cannot be proven equivalent. Reapproval + redeposit reactivates them.

begin;

alter table if exists public.provider_connections
    add column if not exists approved_scope_digest text;

update public.provider_connections
set status = 'pending', approved_scope_digest = null, updated_at = now()
where status = 'active';

do $$
begin
    if not exists (
        select 1 from pg_constraint
        where conrelid = 'public.provider_connections'::regclass
          and conname = 'provider_connections_active_scope_digest_check'
    ) then
        alter table public.provider_connections
            add constraint provider_connections_active_scope_digest_check
            check (
                status <> 'active'
                or approved_scope_digest ~ '^[0-9a-f]{64}$'
            );
    end if;
end $$;

create or replace function public.takyon_provider_connection_scope_guard()
returns trigger
language plpgsql
security definer
set search_path = pg_catalog, public
as $$
begin
    if tg_op = 'INSERT' then
        new.status := 'pending';
        new.approved_scope_digest := null;
    elsif new.provider_kind is distinct from old.provider_kind
       or new.allowed_host is distinct from old.allowed_host
       or new.allowed_path_prefix is distinct from old.allowed_path_prefix
       or new.allowed_methods is distinct from old.allowed_methods
       or new.placement is distinct from old.placement
       or new.scope is distinct from old.scope then
        new.status := 'pending';
        new.approved_scope_digest := null;
    end if;
    return new;
end;
$$;

revoke all on function public.takyon_provider_connection_scope_guard() from public;

drop trigger if exists takyon_provider_connection_scope_guard
    on public.provider_connections;
create trigger takyon_provider_connection_scope_guard
before insert or update on public.provider_connections
for each row execute function public.takyon_provider_connection_scope_guard();

do $$
declare
    runtime_role text;
begin
    foreach runtime_role in array array['takyon_operator_runtime', 'takyon_runtime'] loop
        if exists (select 1 from pg_roles where rolname = runtime_role) then
            execute format(
                'revoke insert (status) on public.provider_connections from %I', runtime_role
            );
            execute format(
                'revoke update (status) on public.provider_connections from %I', runtime_role
            );
        end if;
    end loop;
end $$;

commit;
