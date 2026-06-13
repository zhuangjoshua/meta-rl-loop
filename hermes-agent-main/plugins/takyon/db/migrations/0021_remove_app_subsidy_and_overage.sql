-- 0021_remove_app_subsidy_and_overage.sql
-- The product app runtime now uses a single authoritative rail for AI spend:
-- the current sub-user plan's included monthly AI budget, enforced directly by
-- app_usage, plus the separate business-wide app budget as an outer kill switch.
--
-- That makes the old subsidy fallback rail dead:
--   * app_plan_policies.allow_overage is removed
--   * app_business_subsidy_accounts is removed
--   * app_funding_entries is removed
--   * the supporting enum types are removed when unused

alter table if exists app_plan_policies
    drop column if exists allow_overage;

drop table if exists app_funding_entries;
drop table if exists app_business_subsidy_accounts;

drop type if exists app_funding_entry_kind;
drop type if exists app_funding_bucket;
