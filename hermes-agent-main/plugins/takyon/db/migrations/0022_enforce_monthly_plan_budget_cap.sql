-- 0022_enforce_monthly_plan_budget_cap.sql
-- Defense in depth for product app plans:
-- monthly non-free plans may never store included AI budget above the
-- plan price cap (price_cents * 10_000 microusd), even if a buggy code
-- path or manual SQL bypasses the application-layer validation.

do $$
begin
    if not exists (
        select 1
        from pg_constraint
        where conname = 'app_plan_policies_monthly_ai_budget_cap'
          and conrelid = 'public.app_plan_policies'::regclass
    ) then
        alter table app_plan_policies
            add constraint app_plan_policies_monthly_ai_budget_cap
            check (
                billing_interval <> 'month'
                or lower(tier) = 'free'
                or included_ai_budget_microusd <= (price_cents::bigint * 10000)
            );
    end if;
end $$;
