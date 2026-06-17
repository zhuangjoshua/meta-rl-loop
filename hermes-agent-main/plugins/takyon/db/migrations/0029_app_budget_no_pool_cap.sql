-- 0029_app_budget_no_pool_cap.sql
-- Invariant 9 (GOAL_RULES §3): remove the flat per-business AI-spend pool cap.
--
-- 0007_app_usage_budget.sql opened every business budget at a flat $5
-- (`hard_limit_microusd bigint not null default 5000000`). That is exactly the
-- "arbitrary per-business cap" invariant 9 forbids: it both caps a paid business below
-- its plan's included_ai_budget_microusd AND hands a free $5 pool to an unentitled
-- business. Product AI budget now comes ONLY from the active paid subscription's
-- per-subuser `included_ai_budget_microusd` (the per-subuser gate in app_usage.reserve_usage).
--
-- This migration turns `hard_limit_microusd` into a SENTINEL column:
--   * NULL  → NO per-business pool cap (the per-subuser subscription gate is the sole gate);
--   * a non-null integer → an explicit, enforced ceiling an operator/internal rail may set
--     (e.g. 0 = refuse all product spend for an unentitled business; the >= 0 check stays).
-- New budgets open with NO pool cap (the default is dropped, leaving NULL).
--
-- The old $5 default value is neutralized for already-opened rows so no business keeps a
-- silent free pool. Rows that carry a non-default explicit cap are preserved.

alter table if exists app_budgets
    alter column hard_limit_microusd drop not null;

alter table if exists app_budgets
    alter column hard_limit_microusd drop default;

-- Neutralize the legacy flat $5 default on existing rows: a row still sitting at exactly the
-- old default never had a real per-business cap chosen for it, so clear it to "no pool cap".
update app_budgets
set hard_limit_microusd = null,
    updated_at = now()
where hard_limit_microusd = 5000000;
