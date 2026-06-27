-- 0047_app_runtime_money_read_ports.sql
-- Session-bound app-runtime read ports for product-account money/access display.
--
-- App roles must not read app_entitlements/app_usage_events/app_revenue_events directly by setting
-- caller-controlled RLS GUCs. These ports validate the presented app session hash and derive the app
-- user inside SECURITY DEFINER code before reading money/access ledgers.

create or replace function takyon_app_account_entitlements(
    p_business_slug text,
    p_session_hash text
)
returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
    v_user_id uuid;
begin
    select u.id
      into v_user_id
      from app_sessions s
      join app_users u
        on u.business_slug = s.business_slug
       and u.id = s.app_user_id
     where s.business_slug = p_business_slug
       and s.token_hash = p_session_hash
       and s.revoked_at is null
       and s.expires_at > now()
       and u.status = 'active'
     limit 1;

    if v_user_id is null then
        return '[]'::jsonb;
    end if;

    return coalesce(
        (
            select jsonb_agg(to_jsonb(e) order by e.updated_at desc)
              from app_entitlements e
             where e.business_slug = p_business_slug
               and e.app_user_id = v_user_id
        ),
        '[]'::jsonb
    );
end;
$$;

create or replace function takyon_app_account_usage_summary(
    p_business_slug text,
    p_session_hash text,
    p_period_start timestamptz
)
returns table(count bigint, estimated_cost_microusd bigint, actual_cost_microusd bigint)
language plpgsql
security definer
set search_path = public
as $$
declare
    v_user_id uuid;
begin
    select u.id
      into v_user_id
      from app_sessions s
      join app_users u
        on u.business_slug = s.business_slug
       and u.id = s.app_user_id
     where s.business_slug = p_business_slug
       and s.token_hash = p_session_hash
       and s.revoked_at is null
       and s.expires_at > now()
       and u.status = 'active'
     limit 1;

    if v_user_id is null then
        return query select 0::bigint, 0::bigint, 0::bigint;
        return;
    end if;

    return query
        select count(*)::bigint,
               coalesce(sum(e.estimated_cost_microusd), 0)::bigint,
               coalesce(sum(e.actual_cost_microusd), 0)::bigint
          from app_usage_events e
         where e.business_slug = p_business_slug
           and e.app_user_id = v_user_id
           and e.created_at >= p_period_start;
end;
$$;

create or replace function takyon_app_account_revenue_summary(
    p_business_slug text,
    p_session_hash text
)
returns table(amount_paid_cents bigint, count bigint)
language plpgsql
security definer
set search_path = public
as $$
declare
    v_email text;
begin
    select lower(coalesce(u.email, ''))
      into v_email
      from app_sessions s
      join app_users u
        on u.business_slug = s.business_slug
       and u.id = s.app_user_id
     where s.business_slug = p_business_slug
       and s.token_hash = p_session_hash
       and s.revoked_at is null
       and s.expires_at > now()
       and u.status = 'active'
     limit 1;

    if coalesce(v_email, '') = '' then
        return query select 0::bigint, 0::bigint;
        return;
    end if;

    return query
        select coalesce(sum(e.amount_paid_cents), 0)::bigint,
               count(*)::bigint
          from app_revenue_events e
         where e.business_slug = p_business_slug
           and lower(coalesce(e.customer_email, '')) = v_email;
end;
$$;

create or replace function takyon_app_action_usage_limit(
    p_business_slug text,
    p_session_hash text
)
returns table(app_user_id uuid, tier text, plan_key text, included_ai_budget_microusd bigint)
language plpgsql
security definer
set search_path = public
as $$
declare
    v_user_id uuid;
begin
    select u.id
      into v_user_id
      from app_sessions s
      join app_users u
        on u.business_slug = s.business_slug
       and u.id = s.app_user_id
     where s.business_slug = p_business_slug
       and s.token_hash = p_session_hash
       and s.revoked_at is null
       and s.expires_at > now()
       and u.status = 'active'
     limit 1;

    if v_user_id is null then
        return;
    end if;

    return query
        select e.app_user_id,
               e.tier,
               e.plan_key,
               coalesce(p.included_ai_budget_microusd, 0)::bigint
          from app_entitlements e
          left join app_plan_policies p
            on p.business_slug = e.business_slug
           and p.plan_key = e.plan_key
         where e.business_slug = p_business_slug
           and e.app_user_id = v_user_id
           and e.status in ('active', 'trialing')
           and lower(coalesce(e.tier, '')) not in ('', 'free', 'none', 'unentitled')
           and e.source <> 'openmeter'
         order by
           case lower(e.tier)
             when 'owner' then 0
             when 'paid' then 1
             when 'pro' then 1
             else 100
           end asc,
           e.updated_at desc
         limit 1;
end;
$$;

revoke execute on function takyon_app_account_entitlements(text, text) from public;
revoke execute on function takyon_app_account_usage_summary(text, text, timestamptz) from public;
revoke execute on function takyon_app_account_revenue_summary(text, text) from public;
revoke execute on function takyon_app_action_usage_limit(text, text) from public;

grant execute on function takyon_app_account_entitlements(text, text)
    to takyon_app_runtime, takyon_app;
grant execute on function takyon_app_account_usage_summary(text, text, timestamptz)
    to takyon_app_runtime, takyon_app;
grant execute on function takyon_app_account_revenue_summary(text, text)
    to takyon_app_runtime, takyon_app;
grant execute on function takyon_app_action_usage_limit(text, text)
    to takyon_app_runtime, takyon_app;

revoke select on app_entitlements, app_usage_events, app_revenue_events
    from takyon_app_runtime, takyon_app;
