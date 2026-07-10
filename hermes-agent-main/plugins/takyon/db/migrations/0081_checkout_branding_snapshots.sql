-- 0081_checkout_branding_snapshots.sql
-- Compile Stripe Checkout presentation on the operator plane at publish time. The Safebox only
-- selects the frozen build snapshot while authorizing an intent and forwards allowlisted fields.

begin;

alter table product_builds
    add column if not exists checkout_branding_params_json text not null default '{}';

-- Existing intents may already have used their Stripe idempotency key without branding. Freeze only
-- those rows to the unbranded sentinel the first time this migration adds the column; a replay must
-- not reclassify post-cutover intents that have not yet been claimed.
do $$
begin
    if not exists (
        select 1
          from information_schema.columns
         where table_schema = 'public'
           and table_name = 'app_checkout_intents'
           and column_name = 'checkout_branding_build_id'
    ) then
        alter table app_checkout_intents add column checkout_branding_build_id text;
        update app_checkout_intents set checkout_branding_build_id = '';
    end if;
end
$$;

do $$
begin
    if not exists (
        select 1
          from information_schema.columns
         where table_schema = 'public'
           and table_name = 'app_checkout_intents'
           and column_name = 'checkout_branding_params_json'
    ) then
        alter table app_checkout_intents add column checkout_branding_params_json text;
        update app_checkout_intents set checkout_branding_params_json = '{}';
    end if;
end
$$;

drop function if exists takyon_safebox_claim_app_checkout_intent(
    uuid, text, text, text, text, text
);

create function takyon_safebox_claim_app_checkout_intent(
    p_intent_id uuid,
    p_business_slug text,
    p_plan_key text,
    p_customer_email text,
    p_client_reference_id text,
    p_live_target_account_id text
)
returns table (
    intent_id uuid,
    app_user_id uuid,
    customer_email text,
    client_reference_id text,
    price_cents integer,
    currency text,
    billing_interval text,
    tier text,
    included_ai_budget_microusd bigint,
    included_action_quota integer,
    plan_metadata jsonb,
    business_mode text,
    checkout_branding jsonb
)
language plpgsql
security definer
set search_path = public, pg_temp
as $$
begin
    if session_user <> 'takyon_safebox_authority' then
        raise exception 'safebox_session_required' using errcode = '42501';
    end if;
    if p_live_target_account_id is not null then
        if p_live_target_account_id !~ '^acct_[A-Za-z0-9]+$' then
            raise exception 'stripe_live_target_account_mismatch' using errcode = '22023';
        end if;
        if not exists (
            select 1
              from stripe_environment_cutovers c
             where c.cutover_key = 'sandbox-to-live-v1'
               and c.target_account_id = p_live_target_account_id
        ) then
            raise exception 'stripe_live_cutover_required' using errcode = '55000';
        end if;
    end if;
    perform pg_advisory_xact_lock(
        hashtextextended('takyon-plan-economics:' || p_business_slug || ':' || p_plan_key, 0)
    );
    return query
    with claimed as (
        update app_checkout_intents i
           set status = 'stripe_creating',
               checkout_branding_build_id = coalesce(
                   i.checkout_branding_build_id,
                   coalesce(
                       (
                           select nullif(s.live_build_id, '')
                             from app_surface_contracts s
                            where s.business_slug = i.business_slug
                       ),
                       ''
                   )
               ),
               checkout_branding_params_json = coalesce(
                   i.checkout_branding_params_json,
                   coalesce(
                       (
                           select nullif(pb.checkout_branding_params_json, '')
                             from product_builds pb
                            where pb.business_slug = i.business_slug
                              and pb.build_id = coalesce(
                                  i.checkout_branding_build_id,
                                  (
                                      select nullif(s.live_build_id, '')
                                        from app_surface_contracts s
                                       where s.business_slug = i.business_slug
                                  )
                              )
                       ),
                       '{}'
                   )
               ),
               updated_at = now()
          from app_plan_policies p, app_users u, businesses b
         where i.id = p_intent_id
           and i.business_slug = p_business_slug
           and i.plan_key = p_plan_key
           and i.status in ('created', 'stripe_creating')
           and i.created_at > now() - interval '23 hours'
           and i.app_user_id is not null
           and p.business_slug = i.business_slug
           and p.plan_key = i.plan_key
           and p.billing_interval = 'month'
           and p.price_cents > 0
           and lower(p.currency) = 'usd'
           and p.saleable
           and lower(coalesce(p.metadata->>'status', p.metadata->>'lifecycle_status', ''))
               not in ('archived', 'deprecated', 'disabled', 'inactive', 'retired')
           and u.business_slug = i.business_slug
           and u.id = i.app_user_id
           and u.status = 'active'
           and b.slug = i.business_slug
           and b.status = 'active'
           and b.mode = 'live'
           and (coalesce(p_customer_email, '') = '' or i.customer_email is null
                or lower(i.customer_email) = lower(p_customer_email))
           and (coalesce(p_client_reference_id, '') = ''
                or i.client_reference_id = p_client_reference_id)
        returning i.id, i.app_user_id, i.customer_email, i.client_reference_id,
                  p.price_cents, lower(p.currency) as currency, p.billing_interval, p.tier,
                  p.included_ai_budget_microusd, p.included_action_quota, p.metadata,
                  b.mode, i.checkout_branding_params_json
    )
    select c.id, c.app_user_id, c.customer_email, c.client_reference_id,
           c.price_cents, c.currency, c.billing_interval, c.tier,
           c.included_ai_budget_microusd, c.included_action_quota, c.metadata, c.mode,
           coalesce(nullif(c.checkout_branding_params_json, '')::jsonb, '{}'::jsonb)
      from claimed c;
end;
$$;

revoke execute on function takyon_safebox_claim_app_checkout_intent(
    uuid, text, text, text, text, text
) from public, takyon_operator_runtime, takyon_app_runtime, takyon_runtime, takyon_app, safebox;
grant execute on function takyon_safebox_claim_app_checkout_intent(
    uuid, text, text, text, text, text
) to takyon_safebox_authority;

commit;
