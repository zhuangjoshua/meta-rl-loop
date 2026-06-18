-- 0034_app_plane_rls_disable_policyless.sql
-- CRITICAL production auth-lockout fix: disable row level security on the app
-- control/resolution tables that have RLS *enabled* but ZERO policies.
--
-- Root cause (proven live on prod): app_sessions, app_users, and several other
-- app control tables have relrowsecurity=true with NO policy attached. The
-- session-read path (plugins/takyon/app_identity.py validate_session) runs under
-- the non-bypassing `takyon_app` role (set via core.py _pg_app_scope). RLS-enabled
-- + zero-policy means Postgres default-DENIES every row to a non-bypassing role, so
-- validate_session reads 0 rows -> every product customer is authenticated:false
-- forever. RLS with no policy is a PURE lockout: full default-deny and NO isolation
-- benefit whatsoever (a policyless RLS table grants nothing, it does not scope).
--
-- These are auth-resolution / control tables that the runtime reads directly under
-- the `takyon_app` scope, where the SQL query itself supplies the business +
-- session/app_user scoping (the same tested design 0027 deliberately chose: 0027
-- reads app_sessions/app_users via the scope, NOT via RLS, and grants takyon_app
-- read access in 0030). The RLS-enable on these control tables was applied
-- out-of-band — it is NOT in any repo migration 0009-0033 — so this migration
-- removes it to restore the tested design.
--
-- This DOES NOT touch the 9 policied customer-DATA leaf tables protected by
-- 0027 (app_user_profiles, app_records, app_connections, app_entitlements,
-- app_usage_events, app_revenue_events, app_checkout_intents,
-- app_checkout_sessions, app_media). Those keep RLS ENABLE + FORCE and their
-- per-tenant policies, so the 0027 data-table isolation is unaffected.
--
-- Idempotent and safe: `if exists` no-ops on a missing table; disabling RLS on a
-- table that already has it off is a no-op.

alter table if exists app_sessions disable row level security;
alter table if exists app_users disable row level security;
alter table if exists app_magic_links disable row level security;
alter table if exists app_budgets disable row level security;
alter table if exists app_surface_contracts disable row level security;
alter table if exists app_gateway_keys disable row level security;
alter table if exists app_plan_policies disable row level security;
alter table if exists app_action_schedules disable row level security;
alter table if exists app_execution_policies disable row level security;
alter table if exists approvals disable row level security;
