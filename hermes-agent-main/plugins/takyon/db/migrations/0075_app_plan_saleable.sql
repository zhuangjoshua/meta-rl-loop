-- Checkout catalog lifecycle is explicit and fail-closed. Stripe catalog retirement is an explicit,
-- audited environment-cutover operation (0076), never a side effect of migration replay.

begin;

alter table if exists public.app_plan_policies
    add column if not exists saleable boolean not null default true;

update public.app_plan_policies
set saleable = false, updated_at = now()
where lower(coalesce(metadata->>'status', metadata->>'lifecycle_status', ''))
      in ('archived', 'deprecated', 'disabled', 'inactive', 'retired');

commit;
