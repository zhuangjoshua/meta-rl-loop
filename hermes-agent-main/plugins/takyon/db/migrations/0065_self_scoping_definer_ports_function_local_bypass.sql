-- 0065_self_scoping_definer_ports_function_local_bypass.sql
-- Fix: session-path money/identity SECURITY DEFINER ports were silently RLS-filtered.
--
-- Mechanism of the bug (verified live 2026-07-04): the app-plane connection pool pins the
-- session GUC takyon.rls_bypass = '0' (defense-in-depth for DIRECT table access by app roles).
-- But GUCs are session-wide and survive into SECURITY DEFINER functions: inside a definer port
-- current_user becomes the trusted owner (takyon_migration) while current_setting still reads
-- the caller's '0', so takyon_rls_bypass() = trusted-role AND guc-on went FALSE — and every
-- FORCE-RLS table read inside the port (app_entitlements, app_usage_events) was filtered by
-- scope GUCs the pool never set. Observed effects on the session-token paths:
--   * takyon_app_session_plan returned {entitlement: null} for a provably active paid
--     entitlement → broker_message_for_business 402'd subscription_required → the direct
--     /generate rail refused PAYING customers;
--   * the committed-spend aggregates inside the usage gate read through the same filter, so
--     session-path gate arithmetic was untrustworthy.
--
-- The upstream flaw is in takyon_rls_bypass() itself (last shaped by 0060): it conflates two
-- different trust questions —
--   (1) "should a trusted LOGIN's direct statements bypass row security?"  → governed by the
--       session GUC, and the '0' pin must keep working there; and
--   (2) "should trusted DEFINER CODE bypass row security?" → yes, always: a SECURITY DEFINER
--       port owned by a trusted role IS the sanctioned access path; it validates and pins its
--       own scope (session hash / business + key) on every internal query. Row security
--       inside it is redundant, and letting the caller's session GUC veto it is exactly the
--       bug above.
-- The definer context is precisely `current_user IS DISTINCT FROM session_user` (the login
-- stays the app role while the definer switches current_user to the trusted owner), so the fix
-- is ONE function redefinition, no per-port changes and no parameter-permission grants (prod's
-- takyon_migration may not ALTER ... SET this parameter — learned the hard way).
--
-- Security review (subusers are hostile):
--   * App roles gain nothing: they are not in the trusted list, they have NO direct table
--     privileges on the money/identity tables (outright permission denied, verified), and the
--     ports still validate the session/scope arguments — a bogus session hash still resolves
--     to nothing (verified).
--   * A trusted login's DIRECT statements with the GUC pinned to '0' behave exactly as before
--     (current_user = session_user → the GUC still governs).
--   * Definer ports owned by trusted roles were AUTHORED under owner-bypass assumptions (0047
--     revoked direct SELECT precisely because the ports are the sanctioned reader); this
--     restores that contract.

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
        and (
            coalesce(nullif(current_setting('takyon.rls_bypass', true), ''), '1') in ('1', 'true', 'on')
            -- SECURITY DEFINER context: the login (session_user) is an untrusted plane role
            -- while current_user is the trusted function owner. Trusted definer code is the
            -- sanctioned access path and self-scopes every query; the caller's session GUC
            -- must not veto it (that veto is the 402-paying-customers bug this fixes).
            or current_user is distinct from session_user
        );
$$;
