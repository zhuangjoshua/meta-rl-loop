-- 0068_provider_connections_rls_policy.sql
-- Fix: provider_connections (0067) is read/written DIRECTLY by the safebox authority role (not via
-- a SECURITY DEFINER function), but a global RLS-hardening step enables row security on public
-- tables. With RLS on and NO policy, a non-owner trusted role (takyon_safebox_authority) sees ZERO
-- rows — so the deposit + egress routes got `connection_unknown` on prod despite the row existing.
--
-- Fix: add the SAME trusted-bypass policy app_usage_events uses — `for all using
-- (takyon_rls_bypass())`. After 0065, takyon_rls_bypass() is true for a trusted login with the
-- bypass GUC on (the safebox conn: verified guc='1' → true) AND in any SECURITY DEFINER context.
-- This does NOT weaken the subuser boundary: the subuser/app-runtime role has REVOKE ALL on the
-- table (0067) and is not in the trusted set, so it still cannot read a single row; the policy only
-- lets the already-trusted safebox/migration/operator roles through the RLS layer that the sweep
-- turned on. Cross-tenant safety is unchanged — the safebox always resolves by the HMAC-signed
-- scope's business_slug.
--
-- Idempotent: guard the CREATE POLICY on pg_policies (PG 16 has no CREATE POLICY IF NOT EXISTS).

begin;

alter table if exists public.provider_connections enable row level security;

do $$
begin
    if not exists (
        select 1 from pg_policies
        where schemaname = 'public'
          and tablename = 'provider_connections'
          and policyname = 'takyon_provider_connections_bypass'
    ) then
        create policy takyon_provider_connections_bypass
            on public.provider_connections
            for all
            using (takyon_rls_bypass())
            with check (takyon_rls_bypass());
    end if;
end $$;

commit;
