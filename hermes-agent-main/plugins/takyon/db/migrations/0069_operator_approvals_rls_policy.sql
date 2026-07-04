-- 0069_operator_approvals_rls_policy.sql
-- Fix: operator_approvals (0062) has RLS enabled by the global hardening sweep but NO policy, so a
-- non-owner trusted role sees zero rows. This never surfaced because the only consumers were the
-- operator/runtime plane. The egress deposit route (delta 6) is the FIRST safebox-authority reader
-- of operator_approvals — it verifies a connection's grant was approved — and got
-- connection_not_approved on prod because takyon_safebox_authority (grant present, not BYPASSRLS)
-- could not see the approved row.
--
-- Fix: add the same trusted-bypass policy provider_connections/app_usage_events use. Purely
-- ADDITIVE — the policy only lets already-trusted roles (safebox/operator/runtime/migration) through
-- the RLS layer; it cannot reduce any access, and the subuser/app-runtime role still has REVOKE ALL
-- on operator_approvals (0062), so it remains unable to read or mint an approval.
--
-- Idempotent: guard the CREATE POLICY on pg_policies (PG 16 has no CREATE POLICY IF NOT EXISTS).

begin;

alter table if exists public.operator_approvals enable row level security;

do $$
begin
    if not exists (
        select 1 from pg_policies
        where schemaname = 'public'
          and tablename = 'operator_approvals'
          and policyname = 'takyon_operator_approvals_bypass'
    ) then
        create policy takyon_operator_approvals_bypass
            on public.operator_approvals
            for all
            using (takyon_rls_bypass())
            with check (takyon_rls_bypass());
    end if;
end $$;

commit;
