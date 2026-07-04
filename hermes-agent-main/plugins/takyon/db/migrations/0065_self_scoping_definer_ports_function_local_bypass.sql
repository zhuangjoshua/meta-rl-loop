-- 0065_self_scoping_definer_ports_function_local_bypass.sql
-- Fix: session-path money/identity SECURITY DEFINER ports were silently RLS-filtered.
--
-- Mechanism of the bug (verified live 2026-07-04): the app-plane connection pool pins the
-- session GUC takyon.rls_bypass = '0' (defense-in-depth for DIRECT table access by app roles).
-- But GUCs are session-wide: inside a SECURITY DEFINER function current_user becomes the
-- trusted owner (takyon_migration), yet current_setting('takyon.rls_bypass') still reads the
-- caller's '0', so takyon_rls_bypass() = trusted-role AND guc-on = FALSE — and every
-- FORCE-RLS table read inside the definer (app_entitlements, app_usage_events) is filtered by
-- scope GUCs the pool never set. Observed effects on the session-token paths:
--   * takyon_app_session_plan returned {entitlement: null} for a provably active paid
--     entitlement → broker_message_for_business 402s subscription_required → the direct
--     /generate rail refuses PAYING customers;
--   * the committed-spend aggregates inside the usage gate read through the same filter, so
--     the session-path gate arithmetic was untrustworthy (fail direction varies by policy).
--
-- Fix: these ports are SELF-SCOPING — they derive the app user from a validated session hash
-- (or take an explicit business+reservation key) and pin EVERY internal query by that scope,
-- so row security inside them is redundant. Give each a FUNCTION-SCOPED
-- `SET takyon.rls_bypass = '1'`: PostgreSQL applies it for the function's duration and
-- restores the caller's value on exit, so the app-plane session keeps its '0' for any direct
-- table access. This does NOT loosen the subuser boundary: app roles still have no direct
-- table privileges (0037/0038/0041 revokes), the ports still validate the session/scope
-- arguments themselves, and an attacker without a valid session hash gets exactly what they
-- got before — nothing.
--
-- MAINTENANCE INVARIANT (read before touching any function below): CREATE OR REPLACE resets a
-- function's SET clauses to whatever the new definition declares. Any FUTURE migration that
-- replaces one of these functions MUST carry `set takyon.rls_bypass = '1'` in its own
-- definition (next to `set search_path`) — a later replay of this file will NOT rescue a
-- function replaced by a later-numbered migration, because migrations replay in name order.

-- ── safebox usage gate (0037, rewritten 0063/0064) ────────────────────────────────────────
alter function safebox_reserve_usage(text, bigint, text, uuid, bigint, text, text, text, text, text, jsonb)
    set "takyon.rls_bypass" = '1';
alter function safebox_settle_usage(text, text, bigint, integer, integer, text, text, text, jsonb)
    set "takyon.rls_bypass" = '1';
alter function safebox_release_usage(text, text, text, jsonb)
    set "takyon.rls_bypass" = '1';
alter function safebox_reconcile_held_usage(bigint)
    set "takyon.rls_bypass" = '1';

-- ── persistent credit grants (0064) ──────────────────────────────────────────────────────
alter function safebox_grant_app_user_credits(text, uuid, bigint, text, text)
    set "takyon.rls_bypass" = '1';
alter function safebox_app_user_grant_balance(text, uuid)
    set "takyon.rls_bypass" = '1';
alter function safebox_refund_grant_holds(jsonb, bigint)
    set "takyon.rls_bypass" = '1';

-- ── session-scoped app-plane ports (0047/0048/0051) ──────────────────────────────────────
alter function takyon_app_session_plan(text, text)
    set "takyon.rls_bypass" = '1';
alter function takyon_app_account_entitlements(text, text)
    set "takyon.rls_bypass" = '1';
alter function takyon_app_account_usage_summary(text, text, timestamptz)
    set "takyon.rls_bypass" = '1';
alter function takyon_app_account_revenue_summary(text, text)
    set "takyon.rls_bypass" = '1';
alter function takyon_app_action_usage_limit(text, text)
    set "takyon.rls_bypass" = '1';
alter function takyon_app_reserve_usage(text, text, uuid, bigint, text, bigint, text, text, text, text, text, jsonb)
    set "takyon.rls_bypass" = '1';
alter function takyon_app_settle_usage(text, text, text, bigint, integer, integer, text, text, text, jsonb)
    set "takyon.rls_bypass" = '1';
alter function takyon_app_release_usage(text, text, text, text, jsonb)
    set "takyon.rls_bypass" = '1';

do $$
begin
    -- 0051's session-bound media-usage summary, if present on this database.
    if exists (select 1 from pg_proc where proname = 'takyon_app_media_usage') then
        execute 'alter function takyon_app_media_usage(text, text) set "takyon.rls_bypass" = ''1''';
    end if;
end $$;
