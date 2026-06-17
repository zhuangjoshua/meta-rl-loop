-- 0030_app_plane_rls_role.sql
-- Privilege separation that makes the 0027 app-plane RLS policies actually BITE.
--
-- 0027 ENABLEs + FORCEs row level security and writes correct per-tenant policies, but a
-- PostgreSQL superuser (or any role with BYPASSRLS) is NEVER subject to RLS — not even under
-- FORCE. The Takyon control-plane/runtime login role is the database owner/superuser, so until
-- there is a *non-bypassing* role to run app-customer requests under, the 0027 policies are
-- structurally present but unenforced: a stray app-scoped query would still see/modify every
-- tenant's rows. That is the inv3 (no cross-tenant access) DB-layer hole.
--
-- This migration creates a single, minimally-privileged, NON-login, NON-superuser, NON-BYPASSRLS
-- role (`takyon_app`) and grants it ONLY the DML the shared app-plane tables need plus EXECUTE on
-- the RLS helper functions. App-facing request scopes (`core.TakyonStore._pg_app_scope`) switch the
-- connection to this role with `SET LOCAL ROLE takyon_app`, so for the duration of one app request
-- the DB enforces the same per-customer boundary as the runtime — and a forgotten scope or stray
-- query is DENIED, not silently allowed (fail-closed). Internal operator/service connections keep
-- their privileged login role and the `takyon.rls_bypass='1'` GUC, so they retain full authority
-- through the policies' `takyon_rls_bypass() OR ...` branch.
--
-- Idempotent: the role is created only if absent; grants are repeatable.

do $$
begin
    if not exists (select 1 from pg_roles where rolname = 'takyon_app') then
        -- NOLOGIN: this role is only ever reached via SET ROLE on an already-authenticated
        -- connection; it can never be a connection identity of its own.
        -- NOSUPERUSER + NOBYPASSRLS: the whole point — it MUST be subject to RLS.
        create role takyon_app nologin nosuperuser nobypassrls;
    end if;
end $$;

-- Schema + helper-function access. The 0027 RLS helpers are SECURITY DEFINER where they need to
-- read app_sessions/app_users, so the restricted role can resolve its effective identity without
-- direct table privileges on those auth tables.
grant usage on schema public to takyon_app;

-- DML only on the shared app-plane customer tables that 0027 protects. No DDL, no ownership, no
-- access to control-plane money/identity tables (businesses, users, user_api_keys, ledgers, …):
-- the restricted role can never reach beyond the per-customer substrate.
grant select, insert, update, delete on
    app_user_profiles,
    app_records,
    app_connections,
    app_entitlements,
    app_usage_events,
    app_revenue_events,
    app_checkout_intents,
    app_checkout_sessions,
    app_media
    to takyon_app;

-- Read-only access to the identity/session tables. App leaves running inside `_pg_app_scope`
-- (e.g. app_records.save_record → app_identity.validate_session / get_app_user) resolve the
-- current customer by reading app_users / app_sessions directly; the restricted role needs SELECT
-- to do that. These two tables carry no per-customer RLS policy (0027 isolates the customer-DATA
-- tables granted above) — they are the identity lookup the scope is keyed on — so read-only access
-- here exposes no cross-tenant customer data on its own. No INSERT/UPDATE/DELETE: identity/session
-- mutation stays on the privileged operator/runtime path, never the restricted app-request role.
grant select on app_users, app_sessions to takyon_app;

-- The RLS helper functions are invoked inside the policies; the role must be able to call them.
grant execute on function takyon_rls_bypass() to takyon_app;
grant execute on function takyon_rls_business_slug() to takyon_app;
grant execute on function takyon_rls_bound_app_user_id() to takyon_app;
grant execute on function takyon_rls_session_hash() to takyon_app;
grant execute on function takyon_rls_effective_app_user_id() to takyon_app;
grant execute on function takyon_rls_effective_email() to takyon_app;
