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
-- Running this against the LIVE Supabase IRREVERSIBLY drops polsia2's businesses,
-- billing_accounts, agent_runs, events, and idempotency_keys (and, via cascade, every
-- foreign-key constraint that other polsia2 tables hold against them). Do NOT run it on
-- live until BOTH are true:
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
-- SCOPE (honest): this retires only the takyon-COLLIDING roots — the five table NAMES the
-- takyon migrations OWN but polsia2 also defined with an incompatible shape: billing_accounts,
-- businesses (0001/0002), and agent_runs, events, idempotency_keys (Phase 8 operator port,
-- 0011). It does NOT wipe the rest of polsia2's `public` (profiles, workflow_*, the orphaned
-- business_id dependents); that full wipe is a SEPARATE gated step needing a verified live table
-- inventory AND a Supabase role/grant review (anon/authenticated/service_role privileges on
-- public), neither doable without a live connection. Do NOT blind-drop the schema.
--
-- Each guarded block is the INVERSE of the matching migration's REPLACE guard: it drops the table
-- ONLY when one of that name exists AND is NOT already the takyon shape, so on a clean DB it is a
-- pure no-op and AFTER takyon owns the table it is ALSO a no-op (a re-run can never nuke takyon
-- data). Live introspection (2026-05-31): agent_runs (70 rows, no `scope`), events (1274 rows, has
-- `kind`/no `event_type`), idempotency_keys (0 rows, has `response`/no `operation_hash`) — all
-- polsia2 shape; takyon's 0011 shapes carry scope / event_type / operation_hash respectively.
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

-- Phase 8 operator-port collisions (0011 OWNS these names). Guard: takyon's shapes have
-- scope / event_type / operation_hash; polsia2's do not.

-- Agent-run history: polsia2's business_id/workflow_id-shaped agent_runs (no `scope`).
do $$
begin
    if to_regclass('public.agent_runs') is not null
       and not exists (
           select 1 from information_schema.columns
           where table_schema = 'public'
             and table_name   = 'agent_runs'
             and column_name  = 'scope'
       )
    then
        raise notice 'retire_polsia2: dropping legacy public.agent_runs (cascade)';
        execute 'drop table public.agent_runs cascade';
    else
        raise notice 'retire_polsia2: public.agent_runs absent or already takyon shape — skip';
    end if;
end $$;

-- Audit log: polsia2's kind/subject_type-shaped events (no `event_type`).
do $$
begin
    if to_regclass('public.events') is not null
       and not exists (
           select 1 from information_schema.columns
           where table_schema = 'public'
             and table_name   = 'events'
             and column_name  = 'event_type'
       )
    then
        raise notice 'retire_polsia2: dropping legacy public.events (cascade)';
        execute 'drop table public.events cascade';
    else
        raise notice 'retire_polsia2: public.events absent or already takyon shape — skip';
    end if;
end $$;

-- Idempotency store: polsia2's response/business_id-shaped idempotency_keys (no `operation_hash`).
do $$
begin
    if to_regclass('public.idempotency_keys') is not null
       and not exists (
           select 1 from information_schema.columns
           where table_schema = 'public'
             and table_name   = 'idempotency_keys'
             and column_name  = 'operation_hash'
       )
    then
        raise notice 'retire_polsia2: dropping legacy public.idempotency_keys (cascade)';
        execute 'drop table public.idempotency_keys cascade';
    else
        raise notice 'retire_polsia2: public.idempotency_keys absent or already takyon shape — skip';
    end if;
end $$;

-- ====================== PHASE 2 — full orphan wipe (gated) =======================
-- The SCOPE note above deferred "the rest of polsia2's public (profiles, workflow_*, the
-- orphaned business_id dependents)" to a SEPARATE gated step needing (1) a verified live
-- table inventory and (2) a Supabase role/grant review. Both were done 2026-06-19 against
-- the live four-manifold-prod control plane (ref ddftvmjpfghfrdxhavvp, PG 17.6):
--   * Inventory: public held 142 base tables; exactly 44 are created by db/migrations/0001+
--     (the takyon canonical set). The 98 below are the remainder — five accreted prior
--     generations (polsia "company/profile" control plane, the old agent_* runtime, the
--     superseded business_* control plane, the growth/ads/outreach experiments, and the old
--     generated_app_* product pipeline). The old chain that built them is still recorded in
--     public._migrations (e.g. 0018_growth_learning_loop, 0019_meta_ads_seedance_pipeline,
--     0016_generated_app_economics) — names the current chain reused with different files.
--   * Safety: NONE of the 98 are referenced by current runtime code; NO canonical table holds
--     a FK into them; NO view/matview depends on them; none are in a realtime publication; all
--     were frozen (last write ≤ 2026-05-23) while the canonical set is active to 2026-06-19;
--     anon/authenticated/service_role hold only REFERENCES/TRIGGER/TRUNCATE on them (no data
--     DML), so the data API is not serving them. Operator go-ahead given 2026-06-19
--     ("delete all the legacy tables, I don't care about historical rows").
--
-- Idempotent + self-protecting: the list below is an EXPLICIT enumeration of the 98 orphan
-- names (no wildcard, no schema-wide drop), each dropped only `if exists` and with `cascade`
-- so inter-legacy FK constraints are cleared regardless of order. Not one canonical table name
-- appears in it; on a DB where these are already gone (or never existed, e.g. a test/clean DB)
-- it is a pure no-op. Re-running can never touch the takyon set.
do $$
declare
    legacy_tables text[] := array[
        'action_policies', 'addons', 'agent_actions', 'agent_artifacts',
        'agent_attempts', 'agent_definitions', 'agent_objectives', 'agent_observations',
        'agent_run_steps', 'agent_run_summaries', 'agent_steps', 'approvals',
        'browser_site_policies', 'business_agent_run_steps', 'business_agent_runs', 'business_agent_settings',
        'business_boost_cycles', 'business_boost_sessions', 'business_budget_accounts', 'business_budget_ledger',
        'business_campaigns', 'business_ceo_wakeups', 'business_conversation_messages', 'business_conversation_threads',
        'business_documents', 'business_email_messages', 'business_facts', 'business_growth_budgets',
        'business_inbox_messages', 'business_learnings', 'business_member_invites', 'business_memberships',
        'business_memory_records', 'business_social_posts', 'campaign_metric_snapshots', 'cold_outreach_events',
        'community_targets', 'company_action_policies', 'company_addons', 'company_checkout_intents',
        'company_checkout_sessions', 'company_email_identities', 'company_payment_links', 'company_revenue_events',
        'company_sites', 'context_graph_nodes', 'creator_payout_accounts', 'credit_ledger',
        'cron_jobs', 'customer_response_signals', 'encrypted_vendor_tokens', 'generated_app_build_steps',
        'generated_app_builds', 'generated_app_deployments', 'generated_app_entitlements', 'generated_app_magic_links',
        'generated_app_plan_policies', 'generated_app_product_runs', 'generated_app_runtime_manifests', 'generated_app_sessions',
        'generated_app_users', 'growth_experiments', 'growth_metric_snapshots', 'growth_recommendations',
        'growth_variants', 'hunter_enrichment_events', 'integrations', 'leads',
        'media_generation_jobs', 'meta_ad_campaigns', 'meta_ad_insights', 'meta_ad_sets',
        'meta_ads', 'outbox_events', 'outreach_rate_limits', 'platform_integrations',
        'platform_rate_limit_buckets', 'platform_request_logs', 'profile_ai_wallet_events', 'profile_ai_wallets',
        'profiles', 'project_ai_allocations', 'project_ai_budget_policies', 'project_ai_model_policies',
        'project_ai_proxy_keys', 'project_ai_usage_events', 'project_ai_wallet_events', 'project_ai_wallets',
        'prompt_versions', 'prompts', 'provider_integrations', 'recurring_tasks',
        'runtime_sessions', 'script_runs', 'takyon_control_states', 'task_credit_checkout_sessions',
        'tasks', 'workflow_jobs'
    ];
    -- Defense in depth: the canonical names db/migrations/0001+ OWN. If any ever leaked into the
    -- array above, dropping it would be refused here rather than nuking takyon data.
    canonical_owned text[] := array[
        'agent_runs', 'api_rate_limits', 'app_action_schedules', 'app_budgets',
        'app_checkout_intents', 'app_checkout_sessions', 'app_connections', 'app_entitlements',
        'app_execution_policies', 'app_gateway_keys', 'app_magic_links', 'app_media',
        'app_plan_policies', 'app_records', 'app_revenue_events', 'app_sessions',
        'app_surface_contracts', 'app_usage_events', 'app_user_profiles', 'app_users',
        'billing_accounts', 'billing_entries', 'business_ad_spend_policies',
        'business_creative_credit_accounts', 'business_creative_credit_entries', 'business_revisions',
        'business_work_requests', 'businesses', 'control_states', 'conversation_messages',
        'conversation_threads', 'custody_accounts', 'custody_entries', 'events',
        'idempotency_keys', 'jobs', 'ledger_entries', 'product_builds', 'user_api_keys',
        'users', 'wake_schedules', 'webhook_events', 'workspaces', '_migrations'
    ];
    tbl text;
begin
    foreach tbl in array legacy_tables
    loop
        if tbl = any (canonical_owned) then
            raise exception 'retire_polsia2 phase2: REFUSING to drop canonical table public.% — list is corrupt', tbl;
        end if;
        if to_regclass(format('public.%I', tbl)) is not null then
            raise notice 'retire_polsia2 phase2: dropping legacy public.% (cascade)', tbl;
            execute format('drop table if exists public.%I cascade', tbl);
        else
            raise notice 'retire_polsia2 phase2: public.% absent — skip', tbl;
        end if;
    end loop;
end $$;
