-- 0061_scoped_replica_app_login_roles.sql
-- Per-replica SCOPED app-plane logins (modularization plan Stage 4b hardening bullet).
--
-- Each subuser replica logs in under its OWN revocable role `takyon_app_runtime__<node>` — a plain
-- INHERIT member of takyon_app_runtime minted/dropped by the environment provisioner
-- (env_provisioner._enroll_replica_credentials / _revoke_replica_credentials). Grants and RLS
-- policies stay on the ONE canonical role (membership inherits them); revoking a replica is
-- DROP ROLE, which kills exactly that replica's DB access and nothing else.
--
-- This migration fixes the ONE database helper that string-matched current_user for app-plane
-- semantics. takyon_rls_bound_app_user_id() (0050) must treat a scoped replica login exactly like
-- takyon_app_runtime — a settable GUC is NOT customer identity for app roles — or a replica login
-- could bind takyon.rls_app_user_id to another user's UUID and satisfy the app-plane RLS policies.
-- Both legs of the new arm are load-bearing:
--   * the name pattern alone is never authority (any role could be NAMED takyon_app_runtime__x);
--   * membership alone is never authority (takyon_migration holds a NON-inherit ADMIN membership
--     of takyon_app_runtime and must keep its authority-plane semantics).
-- 'member' (not 'usage') is deliberate on the restrictive arm: ANY scoped-named member is demoted
-- to session-derived identity, even a mis-granted non-inherit one — fail closed, never open.
--
-- takyon_rls_bypass() (0060) needs NO change: scoped replica logins are app-plane and must stay
-- OUT of the trusted bypass set — its current_user name gate already excludes them.

create or replace function takyon_rls_bound_app_user_id()
returns uuid
language sql
stable
as $$
    select case
        when current_user in ('takyon_app', 'takyon_app_runtime') then null::uuid
        when current_user like 'takyon\_app\_runtime\_\_%' escape '\'
             and pg_has_role(current_user, 'takyon_app_runtime', 'member') then null::uuid
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
