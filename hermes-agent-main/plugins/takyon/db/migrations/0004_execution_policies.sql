-- 0004_execution_policies.sql
-- Phase 4: per-business execution policy — the knobs the execution-policy engine
-- (plugins/takyon/policy.py) reads to decide how a unit of the CEO's own work runs:
-- inline vs background job vs a cheaper model tier vs blocked. The money itself is the
-- USER's flow-A billing (allowance + topup, ledgered in billing_entries); this table
-- only holds the per-business routing knobs plus an OPTIONAL per-business sub-cap.
--
-- NOT the product/sub-user budget. app_budgets / app_plan_policies (the SQLite trunk's
-- product tier — how much a business spends serving ITS customers, in microUSD) are a
-- separate concern, ported as-is in Phase 5. This table governs the CEO's own compute
-- against the user's flow-A budget (cents). monthly_app_budget_cents is a guardrail
-- sub-cap, NOT a second wallet; the real balance is always the flow-A ledger.
--
-- Idempotent DDL: safe to run repeatedly. Clean `public` only (local test DB, or
-- live Supabase AFTER the polsia2 teardown).
--
-- REPLACE guard (robustness #1 — mediationplan.md): app_execution_policies is net-new
-- to takyon (no known polsia2 table of this name), but `create table if not exists`
-- would SILENTLY bind to a differently-shaped pre-existing table if one ever existed.
-- Fail loud instead if a non-takyon `app_execution_policies` is present (takyon's has
-- preferred_model_tier). Trivial pass on a clean DB and on re-runs. Mirrors the guards
-- in 0001_identity_spine.sql, 0002_ledgers.sql, and 0003_rate_limits.sql.
do $$
begin
    if to_regclass('public.app_execution_policies') is not null
       and not exists (
           select 1 from information_schema.columns
           where table_schema = 'public'
             and table_name   = 'app_execution_policies'
             and column_name  = 'preferred_model_tier'
       )
    then
        raise exception
            'public.app_execution_policies exists but is not the takyon shape (no '
            'preferred_model_tier). A differently-shaped table of this name is '
            'unexpected; inspect and remove it before applying takyon migrations. See '
            'mediationplan.md > Ground Truth (REPLACE decision).'
            using errcode = 'feature_not_supported';
    end if;
end $$;

-- One row per business. Absent row => the engine applies documented conservative
-- defaults (it does not auto-insert on read), so a business with no explicit policy
-- still routes safely. CHECK constraints keep a misconfigured row from inverting a
-- decision (non-positive caps, negative retry/budget).
create table if not exists app_execution_policies (
    business_slug            text primary key
                                 references businesses (slug) on delete cascade,
    preferred_model_tier     text not null default 'standard'
                                 check (length(preferred_model_tier) > 0),
    max_runtime_seconds      integer not null default 300
                                 check (max_runtime_seconds > 0),
    max_output_bytes         bigint not null default 5000000
                                 check (max_output_bytes > 0),
    allow_worker_escalation  boolean not null default true,
    allow_expensive_branches boolean not null default true,
    quality_mode             text not null default 'balanced'
                                 check (length(quality_mode) > 0),
    retry_depth              integer not null default 1
                                 check (retry_depth >= 0),
    -- NULL = no per-business sub-cap (the user-level flow-A budget is the only gate).
    monthly_app_budget_cents bigint
                                 check (monthly_app_budget_cents is null
                                        or monthly_app_budget_cents >= 0),
    created_at               timestamptz not null default now(),
    updated_at               timestamptz not null default now()
);
