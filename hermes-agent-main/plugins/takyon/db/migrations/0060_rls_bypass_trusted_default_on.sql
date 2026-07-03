-- 0060_rls_bypass_trusted_default_on.sql
-- Trusted-plane RLS bypass must be deterministic under transaction pooling.
--
-- takyon_rls_bypass() (0043) requires BOTH a trusted authority login AND takyon.rls_bypass
-- enabled. The runtime established that GUC once per client connection at SESSION scope
-- (configure_takyon_pg_session), but the live DATABASE_URL is Supabase's transaction-mode pooler
-- (port 6543): a session-scope SET lands only on whichever pooled server backend served that one
-- transaction, while later statements from the same client run on other backends. Trusted-plane
-- reads therefore flapped between bypass and deny depending on which backend served the
-- transaction, and pool-release scrubs (set_config '0' at session scope) actively poisoned shared
-- backends for every other client (2026-07-03 incident: intermittent empty control-plane reads on
-- the operator plane; safebox authority lookups intermittently raising unknown_business, blocking
-- live business launches).
--
-- The GUC was never authority for a trusted login anyway: it is USERSET, so any trusted-plane
-- session could always self-assert '1' (and the runtime unconditionally did, on every acquire).
-- Its real jobs are (a) the EXPLICIT demote used by the app scope and the money-ledger gates
-- (set '0' around statements that must see the tenant-scoped world), and (b) accident prevention.
-- Under a transaction pooler, job (b) inverted into nondeterministic denial of the planes that own
-- the tables. So:
--
--   * Trusted authority logins now DEFAULT to bypass when the GUC is unset/empty — deterministic
--     at backend birth, no dependence on which backend a SET landed on.
--   * An EXPLICIT takyon.rls_bypass='0' still demotes: _pg_app_scope and the ledger gates keep
--     their exact semantics (they set '0' transaction-/session-locally around gated work).
--   * App/customer roles are unchanged and remain excluded by the current_user gate regardless of
--     any GUC value (0043 invariant: a settable GUC is not authority) — takyon_app setting '1'
--     still returns false, and takyon_app "forgetting" bypass was always false by role.
--
-- Fail-closed posture for the tenant boundary is therefore preserved where it matters (untrusted
-- roles), while the trusted planes stop flapping. Pinned by
-- tests/authoritative/test_inv3_no_cross_tenant_access.py.

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
        and coalesce(nullif(current_setting('takyon.rls_bypass', true), ''), '1') in ('1', 'true', 'on');
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
