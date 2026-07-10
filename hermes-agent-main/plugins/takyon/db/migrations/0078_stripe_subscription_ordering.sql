-- 0078_stripe_subscription_ordering.sql
-- Keep Stripe lifecycle delivery order and payment reversals from restoring unpaid access.

begin;

create or replace function safebox_set_subscription_entitlement_status(
    p_stripe_subscription_id text,
    p_status text,
    p_stripe_customer_id text,
    p_current_period_end timestamptz,
    p_metadata jsonb
)
returns table (business_slug text, app_user_id uuid, plan_key text)
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
    v_ent app_entitlements%rowtype;
    v_patch jsonb := coalesce(p_metadata, '{}'::jsonb);
    v_existing_lifecycle bigint;
    v_incoming_lifecycle bigint;
    v_revoked_at bigint;
    v_payment_at bigint;
    v_stale boolean;
    v_payment_revoked boolean;
    v_payment_settled boolean;
    v_reactivation_blocked boolean;
    v_apply boolean;
begin
    if coalesce(trim(p_stripe_subscription_id), '') = '' or coalesce(trim(p_status), '') = '' then
        raise exception 'invalid_subscription_status_update' using errcode = '22023';
    end if;

    if coalesce(v_patch->>'stripe_lifecycle_event_created', '') ~ '^[0-9]+$' then
        v_incoming_lifecycle := (v_patch->>'stripe_lifecycle_event_created')::bigint;
    else
        v_incoming_lifecycle := null;
    end if;
    if coalesce(v_patch->>'stripe_payment_event_created', '') ~ '^[0-9]+$' then
        v_payment_at := (v_patch->>'stripe_payment_event_created')::bigint;
    else
        v_payment_at := null;
    end if;
    v_payment_settled := lower(coalesce(v_patch->>'payment_settled', 'false'))
        in ('true', '1', 'yes', 'on');

    for v_ent in
        select e.*
          from app_entitlements e
         where e.source = 'stripe'
           and e.stripe_subscription_id = p_stripe_subscription_id
         order by e.id
         for update
    loop
        if coalesce(v_ent.metadata->>'stripe_lifecycle_event_created', '') ~ '^[0-9]+$' then
            v_existing_lifecycle :=
                (v_ent.metadata->>'stripe_lifecycle_event_created')::bigint;
        else
            v_existing_lifecycle := null;
        end if;
        if coalesce(v_ent.metadata->>'payment_revoked_event_created', '') ~ '^[0-9]+$' then
            v_revoked_at := (v_ent.metadata->>'payment_revoked_event_created')::bigint;
        else
            v_revoked_at := null;
        end if;
        v_payment_revoked := lower(coalesce(v_ent.metadata->>'payment_revoked', 'false'))
            in ('true', '1', 'yes', 'on');
        -- A current non-entitling Stripe proof is authoritative even when an older webhook
        -- triggered the fetch. Ordering may block only a stale reactivation, never a revocation.
        v_stale := lower(p_status) in ('active', 'trialing')
            and v_incoming_lifecycle is not null
            and v_existing_lifecycle is not null
            and v_incoming_lifecycle < v_existing_lifecycle;
        v_reactivation_blocked := lower(p_status) in ('active', 'trialing')
            and v_payment_revoked
            and not (
                v_payment_settled
                and v_payment_at is not null
                and v_revoked_at is not null
                and v_payment_at > v_revoked_at
            );
        v_apply := not v_stale and not v_reactivation_blocked;

        update app_entitlements e
           set status = case when v_apply then p_status else e.status end,
               stripe_customer_id = case
                   when v_apply then coalesce(p_stripe_customer_id, e.stripe_customer_id)
                   else e.stripe_customer_id
               end,
               current_period_end = case
                   when v_apply then coalesce(p_current_period_end, e.current_period_end)
                   else e.current_period_end
               end,
               metadata = case
                   when v_stale then e.metadata || jsonb_build_object(
                       'stripe_lifecycle_event_ignored', v_patch->>'stripe_lifecycle_event_id',
                       'stripe_lifecycle_event_ignored_created', v_incoming_lifecycle
                   )
                   when v_reactivation_blocked then e.metadata || jsonb_build_object(
                       'payment_reactivation_blocked', true,
                       'payment_reactivation_blocked_event', coalesce(
                           v_patch->>'stripe_payment_event_id',
                           v_patch->>'stripe_lifecycle_event_id'
                       )
                   )
                   else e.metadata || v_patch || case
                       when v_existing_lifecycle is not null
                            and v_incoming_lifecycle is not null
                            and v_existing_lifecycle > v_incoming_lifecycle
                       then jsonb_build_object(
                           'stripe_lifecycle_event_created', v_existing_lifecycle
                       )
                       else '{}'::jsonb
                   end
               end,
               updated_at = now()
         where e.id = v_ent.id;

        return query
            select v_ent.business_slug, v_ent.app_user_id, v_ent.plan_key;
    end loop;
end;
$$;

revoke execute on function safebox_set_subscription_entitlement_status(
    text, text, text, timestamptz, jsonb
) from public, takyon_operator_runtime, takyon_app_runtime, takyon_runtime, takyon_app, safebox;
grant execute on function safebox_set_subscription_entitlement_status(
    text, text, text, timestamptz, jsonb
) to takyon_safebox_authority;

commit;
