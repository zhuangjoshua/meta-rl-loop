-- 0044_authority_split_login_roles.sql
-- Target login roles for the operator/app/Safebox authority split.
--
-- This migration creates the database identities and grants. It deliberately does NOT set passwords:
-- password generation, rotation, and DSN placement are a deployment/secret-manager step. After that
-- step the services use:
--   TAKYON_OPERATOR_DATABASE_URL -> takyon_operator_runtime
--   TAKYON_APP_DATABASE_URL      -> takyon_app_runtime
--   TAKYON_SAFEBOX_DATABASE_URL  -> takyon_safebox_authority
--   TAKYON_MIGRATION_DATABASE_URL -> takyon_migration
--
-- Simplicity rule: explicit grants, no role-membership bridge between planes.

do $$
begin
    if not exists (select 1 from pg_roles where rolname = 'takyon_operator_runtime') then
        create role takyon_operator_runtime login noinherit nosuperuser nobypassrls;
    end if;
    if not exists (select 1 from pg_roles where rolname = 'takyon_app_runtime') then
        create role takyon_app_runtime login noinherit nosuperuser nobypassrls;
    end if;
    if not exists (select 1 from pg_roles where rolname = 'takyon_safebox_authority') then
        create role takyon_safebox_authority login noinherit nosuperuser nobypassrls;
    end if;
    if not exists (select 1 from pg_roles where rolname = 'takyon_migration') then
        create role takyon_migration login noinherit nosuperuser nobypassrls createrole createdb;
    end if;
end $$;

grant usage on schema public to
    takyon_operator_runtime,
    takyon_app_runtime,
    takyon_safebox_authority,
    takyon_migration;

-- app runtime: direct app-plane grants, no operator/Safebox membership
grant select, insert, update, delete on
    app_user_profiles,
    app_records,
    app_connections,
    app_checkout_intents,
    app_checkout_sessions,
    app_media
    to takyon_app_runtime;

grant select on
    app_entitlements,
    app_usage_events,
    app_revenue_events
    to takyon_app_runtime;

revoke insert, update, delete on
    app_usage_events,
    app_entitlements,
    app_revenue_events
    from takyon_app_runtime;

grant execute on function takyon_rls_bypass() to takyon_app_runtime;
grant execute on function takyon_rls_business_slug() to takyon_app_runtime;
grant execute on function takyon_rls_bound_app_user_id() to takyon_app_runtime;
grant execute on function takyon_rls_session_hash() to takyon_app_runtime;
grant execute on function takyon_rls_effective_app_user_id() to takyon_app_runtime;
grant execute on function takyon_rls_effective_email() to takyon_app_runtime;

grant execute on function safebox_reserve_usage(
    text, bigint, text, uuid, bigint, text, text, text, text, text, jsonb) to takyon_app_runtime;
grant execute on function safebox_settle_usage(
    text, text, bigint, integer, integer, text, text, text, jsonb) to takyon_app_runtime;
grant execute on function safebox_release_usage(text, text, text, jsonb) to takyon_app_runtime;
grant execute on function safebox_reconcile_held_usage(bigint) to takyon_app_runtime;

-- operator runtime: broad non-money runtime state, no SET ROLE app bridge
grant select, insert, update, delete on all tables in schema public to takyon_operator_runtime;
grant usage, select, update on all sequences in schema public to takyon_operator_runtime;

revoke insert, update, delete on
    billing_accounts,
    billing_entries,
    custody_accounts,
    custody_entries,
    business_creative_credit_accounts,
    business_creative_credit_entries,
    app_usage_events,
    app_entitlements,
    app_revenue_events
    from takyon_operator_runtime;

revoke update on businesses from takyon_operator_runtime;
do $$
declare
    col_list text;
begin
    select string_agg(format('%I', column_name), ', ')
      into col_list
      from information_schema.columns
     where table_schema = 'public'
       and table_name = 'businesses'
       and column_name <> 'owner_user_id';
    if col_list is not null then
        execute format('grant update (%s) on businesses to takyon_operator_runtime', col_list);
    end if;
end $$;

grant execute on function takyon_rls_bypass() to takyon_operator_runtime;
grant execute on function takyon_rls_business_slug() to takyon_operator_runtime;
grant execute on function takyon_rls_bound_app_user_id() to takyon_operator_runtime;
grant execute on function takyon_rls_session_hash() to takyon_operator_runtime;
grant execute on function takyon_rls_effective_app_user_id() to takyon_operator_runtime;
grant execute on function takyon_rls_effective_email() to takyon_operator_runtime;

-- Safebox authority: the only runtime login with direct money/ledger write authority
grant select on all tables in schema public to takyon_safebox_authority;
grant usage, select, update on all sequences in schema public to takyon_safebox_authority;

grant insert, update, delete on
    billing_accounts,
    billing_entries,
    custody_accounts,
    custody_entries,
    business_creative_credit_accounts,
    business_creative_credit_entries,
    app_usage_events,
    app_entitlements,
    app_revenue_events,
    safebox_used_nonces,
    webhook_events
    to takyon_safebox_authority;

do $$
declare
    fn record;
begin
    for fn in
        select n.nspname, p.proname, pg_get_function_identity_arguments(p.oid) as args
          from pg_proc p
          join pg_namespace n on n.oid = p.pronamespace
         where n.nspname = 'public'
           and p.proname like 'safebox\_%' escape '\'
    loop
        execute format(
            'grant execute on function %I.%I(%s) to takyon_safebox_authority',
            fn.nspname,
            fn.proname,
            fn.args
        );
    end loop;
end $$;

-- migration role: DDL-only deployment identity, never a live service DSN
grant create on schema public to takyon_migration;
grant all privileges on all tables in schema public to takyon_migration;
grant all privileges on all sequences in schema public to takyon_migration;
grant all privileges on all functions in schema public to takyon_migration;

alter default privileges in schema public
    grant all privileges on tables to takyon_migration;
alter default privileges in schema public
    grant all privileges on sequences to takyon_migration;
alter default privileges in schema public
    grant all privileges on functions to takyon_migration;
