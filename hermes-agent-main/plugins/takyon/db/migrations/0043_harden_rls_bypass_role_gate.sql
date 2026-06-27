-- 0043_harden_rls_bypass_role_gate.sql
-- A settable GUC is not authority.
--
-- Before this migration, takyon_rls_bypass() returned true whenever a session set
-- takyon.rls_bypass=1. That was tolerable only while app/customer work always ran under a trusted
-- runtime login that deliberately flipped the GUC off before SET ROLE takyon_app. In the authority
-- split, app/customer roles must not be able to bypass tenant RLS merely by setting their own GUC.
--
-- Bypass now requires BOTH:
--   1. current_user is an operator/Safebox/migration authority role, and
--   2. takyon.rls_bypass is explicitly enabled.
--
-- The legacy takyon_runtime role stays allowed during the cutover. The app roles are deliberately
-- absent: takyon_app / takyon_app_runtime setting takyon.rls_bypass=1 still returns false.

create or replace function takyon_rls_bypass()
returns boolean
language sql
stable
as $$
    select
        current_user in (
            'postgres',
            'takyon_runtime',
            'takyon_operator_runtime',
            'takyon_safebox_authority',
            'takyon_migration'
        )
        and coalesce(nullif(current_setting('takyon.rls_bypass', true), ''), '0') in ('1', 'true', 'on');
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
            execute format('grant execute on function takyon_rls_bypass() to %I', role_name);
        end if;
    end loop;
end $$;
