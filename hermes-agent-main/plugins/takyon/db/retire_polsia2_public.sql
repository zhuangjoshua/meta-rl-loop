-- retire_polsia2_public.sql
-- One-time teardown of polsia2's overlapping control tables in `public`, so the
-- takyon control-plane migrations (db/migrations/0001+, which OWN these names) can
-- install their own shape. Implements the REPLACE decision recorded in
-- mediationplan.md > Ground Truth (2026-05-30, operator): takyon owns `public`;
-- polsia2's live rows are disposable.
--
-- THIS FILE IS DELIBERATELY NOT UNDER db/migrations/. The test conftest sweeps
-- db/migrations/*.sql on every run; a destructive drop must never ride along in
-- that idempotent forward set. This is a separate, manually-run cutover step.
--
-- ============================ DESTRUCTIVE — READ FIRST ============================
-- Running this against the LIVE Supabase IRREVERSIBLY drops polsia2's businesses and
-- billing_accounts (and, via cascade, every foreign-key constraint that other polsia2
-- tables hold against them). Do NOT run it on live until BOTH are true:
--   (a) a fresh Supabase backup/snapshot exists, and
--   (b) the operator has given an explicit go-ahead for this run.
-- ================================================================================
--
-- SAFE BY CONSTRUCTION:
--   * Idempotent / re-runnable. Each drop is guarded so it fires ONLY when a table of
--     that name exists AND is NOT already the takyon shape. On a clean DB it is a pure
--     no-op; AFTER takyon owns the table it is ALSO a no-op — re-running can never nuke
--     takyon's own data (the guard is the inverse of 0001/0002's REPLACE guards).
--   * Order: billing_accounts then businesses. `drop ... cascade` on businesses removes
--     the FK constraints polsia2's ~20 business_id-dependent tables hold against it; it
--     does NOT drop those tables themselves (they are left orphaned, see SCOPE below).
--
-- SCOPE (honest): this retires only the two takyon-COLLIDING roots. A full wipe of the
-- rest of polsia2's `public` (profiles, agent_runs, and the orphaned business_id
-- dependents) is a SEPARATE gated step: it needs a verified live table inventory AND a
-- Supabase role/grant review (anon/authenticated/service_role privileges on public),
-- neither of which can be done without a live connection. Do NOT blind-drop the schema.
--
-- USAGE (live, after backup + go-ahead): run inside one transaction, e.g.
--   psql "$MIGRATION_DATABASE_URL" -1 -f plugins/takyon/db/retire_polsia2_public.sql
-- then apply db/migrations/0001_identity_spine.sql, 0002_ledgers.sql, ... in order.

-- Flow-A account table: retire polsia2's Stripe-subscription-shaped billing_accounts.
-- Guard: takyon's billing_accounts has allowance_included_cents; polsia2's does not.
do $$
begin
    if to_regclass('public.billing_accounts') is not null
       and not exists (
           select 1 from information_schema.columns
           where table_schema = 'public'
             and table_name   = 'billing_accounts'
             and column_name  = 'allowance_included_cents'
       )
    then
        raise notice 'retire_polsia2: dropping legacy public.billing_accounts (cascade)';
        execute 'drop table public.billing_accounts cascade';
    else
        raise notice 'retire_polsia2: public.billing_accounts absent or already takyon shape — skip';
    end if;
end $$;

-- Root control table: retire polsia2's id-PK / owner_profile_id businesses. CASCADE
-- clears the FK constraints its dependents hold (it does not drop the dependents).
-- Guard: takyon's businesses has owner_user_id; polsia2's does not.
do $$
begin
    if to_regclass('public.businesses') is not null
       and not exists (
           select 1 from information_schema.columns
           where table_schema = 'public'
             and table_name   = 'businesses'
             and column_name  = 'owner_user_id'
       )
    then
        raise notice 'retire_polsia2: dropping legacy public.businesses (cascade)';
        execute 'drop table public.businesses cascade';
    else
        raise notice 'retire_polsia2: public.businesses absent or already takyon shape — skip';
    end if;
end $$;
