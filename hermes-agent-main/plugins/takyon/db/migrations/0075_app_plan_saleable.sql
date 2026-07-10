-- Checkout catalog lifecycle is explicit and fail-closed. Existing Stripe linkages with no active
-- subscribers are cleared so the next live plan upsert provisions product/price metadata carrying
-- the new account/business/plan/economics binding. Active subscribers keep their historical price
-- for renewal; new checkout against an unbound historical price fails until a new plan version is
-- deliberately provisioned.

begin;

alter table if exists public.app_plan_policies
    add column if not exists saleable boolean not null default true;

update public.app_plan_policies
set saleable = false, updated_at = now()
where lower(coalesce(metadata->>'status', metadata->>'lifecycle_status', ''))
      in ('archived', 'deprecated', 'disabled', 'inactive', 'retired');

update public.app_plan_policies p
set stripe_product_id = null,
    stripe_price_id = null,
    updated_at = now()
where p.stripe_price_id is not null
  and not exists (
      select 1
      from public.app_entitlements e
      where e.business_slug = p.business_slug
        and e.plan_key = p.plan_key
        and e.status in ('active', 'trialing')
  );

commit;
