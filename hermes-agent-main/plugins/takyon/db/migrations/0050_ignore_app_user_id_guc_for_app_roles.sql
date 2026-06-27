-- 0050_ignore_app_user_id_guc_for_app_roles.sql
-- A settable app-user GUC is not customer identity for product app roles.
--
-- App/customer sessions must derive their DB-visible app user from a live app session hash. A
-- caller-controlled app role must not be able to set takyon.rls_app_user_id to another user's UUID
-- and satisfy app_records/profile/media/checkout RLS policies. Non-app authority planes may still
-- bind app_user_id as an internal scoped helper while their own role gates remain in force.

create or replace function takyon_rls_bound_app_user_id()
returns uuid
language sql
stable
as $$
    select case
        when current_user in ('takyon_app', 'takyon_app_runtime') then null::uuid
        else nullif(current_setting('takyon.rls_app_user_id', true), '')::uuid
    end;
$$;

do $$
declare
    role_name text;
    role_names text[] := array[
        'takyon_app',
        'takyon_runtime',
        'takyon_operator_runtime',
        'takyon_app_runtime',
        'takyon_safebox_authority',
        'takyon_migration'
    ];
begin
    foreach role_name in array role_names loop
        if exists (select 1 from pg_roles where rolname = role_name) then
            execute format('grant execute on function takyon_rls_bound_app_user_id() to %I', role_name);
        end if;
    end loop;
end $$;
