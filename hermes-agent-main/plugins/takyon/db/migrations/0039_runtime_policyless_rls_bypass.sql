-- 0039_runtime_policyless_rls_bypass.sql
-- Restore the intended operator-runtime read/write path after the G3 role cutover.
--
-- 0038 demoted the runtime connection to `takyon_runtime` and retained broad app grants while revoking
-- direct writes on the money ledgers. On the live Supabase database, several historical control-plane
-- tables already had RLS enabled with *no* policies. A NOBYPASSRLS runtime role therefore saw an empty
-- control plane: no users, no businesses, no billing rows, and no configured wallet/plans in the UI.
--
-- The operator runtime is supposed to pass RLS via the explicit `takyon.rls_bypass` GUC branch, not via a
-- BYPASSRLS role attribute. Add that GUC policy to the policyless control-plane tables. The dangerous
-- ledger DML boundary remains in the GRANT layer from 0038: `takyon_runtime` still lacks
-- INSERT/UPDATE/DELETE on billing/custody/creative ledgers and lacks UPDATE(owner_user_id) on businesses.

do $$
declare
    tbl text;
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
begin
    if not exists (select 1 from pg_roles where rolname = 'takyon_runtime') then
        return;
    end if;

    foreach tbl in array tables loop
        if to_regclass(format('public.%I', tbl)) is not null then
            execute format(
                'drop policy if exists takyon_runtime_guc_bypass on public.%I',
                tbl
            );
            execute format(
                'create policy takyon_runtime_guc_bypass on public.%I ' ||
                'for all to takyon_runtime ' ||
                'using (takyon_rls_bypass()) ' ||
                'with check (takyon_rls_bypass())',
                tbl
            );
        end if;
    end loop;
end $$;
