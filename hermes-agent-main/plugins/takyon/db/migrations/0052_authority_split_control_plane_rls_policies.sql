-- 0052_authority_split_control_plane_rls_policies.sql
-- Complete the operator/app/Safebox DB authority split for existing RLS-enabled
-- control-plane tables.
--
-- 0039 added policyless-table RLS bypass policies for the legacy takyon_runtime
-- role. 0043 hardened takyon_rls_bypass() so only authority roles can satisfy it
-- when the runtime explicitly sets takyon.rls_bypass=1. After the DSN cutover,
-- operator/Safebox/migration sessions no longer connect as takyon_runtime, so
-- the old policies hide live rows from the new split roles. Add equivalent
-- policies for authority roles only. App roles remain excluded.

do $$
declare
    tbl text;
    role_name text;
    tables text[] := array[
        '_migrations',
        'agent_runs',
        'api_rate_limits',
        'billing_accounts',
        'billing_entries',
        'business_ad_spend_policies',
        'business_creative_credit_accounts',
        'business_creative_credit_entries',
        'business_revisions',
        'business_work_requests',
        'businesses',
        'control_states',
        'conversation_messages',
        'conversation_threads',
        'custody_accounts',
        'custody_entries',
        'events',
        'idempotency_keys',
        'jobs',
        'ledger_entries',
        'product_builds',
        'safebox_used_nonces',
        'user_api_keys',
        'users',
        'wake_schedules',
        'webhook_events',
        'workspaces'
    ];
    authority_roles text[] := array[
        'takyon_operator_runtime',
        'takyon_safebox_authority',
        'takyon_migration'
    ];
begin
    foreach tbl in array tables loop
        if to_regclass(format('public.%I', tbl)) is null then
            continue;
        end if;

        foreach role_name in array authority_roles loop
            if not exists (select 1 from pg_roles where rolname = role_name) then
                continue;
            end if;
            execute format(
                'drop policy if exists %I on public.%I',
                'takyon_' || role_name || '_guc_bypass',
                tbl
            );
            execute format(
                'create policy %I on public.%I ' ||
                'for all to %I ' ||
                'using (takyon_rls_bypass()) ' ||
                'with check (takyon_rls_bypass())',
                'takyon_' || role_name || '_guc_bypass',
                tbl,
                role_name
            );
        end loop;
    end loop;
end $$;
