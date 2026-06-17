-- 0028_remove_free_app_tier.sql
-- Remove the stale "free" app-runtime tier. Unpaid product users are represented by
-- `app_users.tier = 'unentitled'` plus NO active entitlement row.

alter table if exists app_users
    alter column tier set default 'unentitled';

alter table if exists app_plan_policies
    alter column tier drop default;

alter table if exists app_entitlements
    alter column tier drop default;

-- Legacy bootstrap rows used `free` as a fake entitlement. Keep the rows for audit/history,
-- but make them inert so they no longer confer access or show up as an active plan tier.
update app_entitlements
set
    tier = 'unentitled',
    status = case when status in ('active', 'trialing') then 'revoked' else status end,
    updated_at = now()
where lower(tier) = 'free';

with ranked as (
    select
        business_slug,
        app_user_id,
        tier,
        row_number() over (
            partition by business_slug, app_user_id
            order by
                case lower(tier)
                    when 'owner' then 0
                    when 'paid' then 1
                    when 'pro' then 1
                    else 5
                end asc,
                updated_at desc
        ) as rn
    from app_entitlements
    where status in ('active', 'trialing')
      and lower(tier) not in ('free', 'none', 'unentitled')
)
update app_users u
set
    tier = coalesce(
        (
            select r.tier
            from ranked r
            where r.business_slug = u.business_slug
              and r.app_user_id = u.id
              and r.rn = 1
        ),
        'unentitled'
    ),
    updated_at = now()
where true;
