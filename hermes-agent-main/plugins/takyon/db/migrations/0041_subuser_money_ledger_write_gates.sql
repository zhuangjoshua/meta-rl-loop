-- C1 continuation: the restricted runtime principal must not directly write sub-user money/access
-- truth. 0037 already gated app_usage_events; this gates the remaining app-customer money/access
-- tables the plan names: app_entitlements and app_revenue_events.

create or replace function safebox_insert_app_entitlement(
    p_business_slug text,
    p_app_user_id uuid,
    p_tier text,
    p_status text,
    p_source text,
    p_stripe_customer_id text,
    p_stripe_subscription_id text,
    p_stripe_checkout_session_id text,
    p_plan_key text,
    p_current_period_end timestamptz,
    p_metadata jsonb
)
returns app_entitlements
language sql
security definer
set search_path = public, pg_temp
as $$
    insert into app_entitlements (
        business_slug,
        app_user_id,
        tier,
        status,
        source,
        stripe_customer_id,
        stripe_subscription_id,
        stripe_checkout_session_id,
        plan_key,
        current_period_end,
        metadata
    )
    values (
        p_business_slug,
        p_app_user_id,
        p_tier,
        p_status,
        p_source,
        p_stripe_customer_id,
        p_stripe_subscription_id,
        p_stripe_checkout_session_id,
        p_plan_key,
        p_current_period_end,
        coalesce(p_metadata, '{}'::jsonb)
    )
    returning *;
$$;

create or replace function safebox_set_subscription_entitlement_status(
    p_stripe_subscription_id text,
    p_status text,
    p_stripe_customer_id text,
    p_current_period_end timestamptz,
    p_metadata jsonb
)
returns table (business_slug text, app_user_id uuid, plan_key text)
language sql
security definer
set search_path = public, pg_temp
as $$
    with updated as (
        update app_entitlements set
            status = p_status,
            stripe_customer_id = coalesce(p_stripe_customer_id, stripe_customer_id),
            current_period_end = coalesce(p_current_period_end, current_period_end),
            metadata = metadata || coalesce(p_metadata, '{}'::jsonb),
            updated_at = now()
        where source = 'stripe'
          and stripe_subscription_id = p_stripe_subscription_id
        returning app_entitlements.business_slug, app_entitlements.app_user_id, app_entitlements.plan_key
    )
    select distinct updated.business_slug, updated.app_user_id, updated.plan_key from updated;
$$;

create or replace function safebox_patch_subscription_entitlement_metadata(
    p_stripe_subscription_id text,
    p_metadata jsonb
)
returns bigint
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
    v_count bigint;
begin
    update app_entitlements set
        metadata = metadata || coalesce(p_metadata, '{}'::jsonb),
        updated_at = now()
    where source = 'stripe'
      and stripe_subscription_id = p_stripe_subscription_id;
    get diagnostics v_count = row_count;
    return v_count;
end;
$$;

create or replace function safebox_cancel_checkout_session_entitlements(
    p_business_slug text,
    p_stripe_checkout_session_id text,
    p_metadata jsonb
)
returns bigint
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
    v_count bigint;
begin
    update app_entitlements set
        status = 'cancelled',
        metadata = metadata || coalesce(p_metadata, '{}'::jsonb),
        updated_at = now()
    where source = 'stripe'
      and business_slug = p_business_slug
      and stripe_checkout_session_id = p_stripe_checkout_session_id;
    get diagnostics v_count = row_count;
    return v_count;
end;
$$;

create or replace function safebox_retire_openmeter_entitlements(
    p_business_slug text,
    p_app_user_id uuid,
    p_current_period_end timestamptz,
    p_metadata jsonb
)
returns bigint
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
    v_count bigint;
begin
    update app_entitlements set
        status = 'cancelled',
        current_period_end = coalesce(p_current_period_end, current_period_end),
        metadata = metadata || coalesce(p_metadata, '{}'::jsonb),
        updated_at = now()
    where business_slug = p_business_slug
      and app_user_id = p_app_user_id
      and source = 'openmeter'
      and lower(status) not in ('cancelled', 'canceled');
    get diagnostics v_count = row_count;
    return v_count;
end;
$$;

create or replace function safebox_insert_app_revenue_event(
    p_business_slug text,
    p_provider_event_id text,
    p_stripe_object_type text,
    p_stripe_object_id text,
    p_stripe_checkout_session_id text,
    p_stripe_customer_id text,
    p_revenue_type text,
    p_status text,
    p_currency text,
    p_amount_paid_cents integer,
    p_customer_email text,
    p_occurred_at timestamptz,
    p_metadata jsonb
)
returns uuid
language sql
security definer
set search_path = public, pg_temp
as $$
    insert into app_revenue_events (
        business_slug,
        provider_event_id,
        stripe_object_type,
        stripe_object_id,
        stripe_checkout_session_id,
        stripe_customer_id,
        revenue_type,
        status,
        currency,
        amount_paid_cents,
        customer_email,
        occurred_at,
        metadata
    )
    values (
        p_business_slug,
        p_provider_event_id,
        p_stripe_object_type,
        p_stripe_object_id,
        p_stripe_checkout_session_id,
        p_stripe_customer_id,
        p_revenue_type,
        p_status,
        p_currency,
        p_amount_paid_cents,
        p_customer_email,
        coalesce(p_occurred_at, now()),
        coalesce(p_metadata, '{}'::jsonb)
    )
    on conflict (business_slug, provider_event_id, stripe_object_id) do nothing
    returning id;
$$;

revoke execute on function safebox_insert_app_entitlement(
    text, uuid, text, text, text, text, text, text, text, timestamptz, jsonb
) from public;
revoke execute on function safebox_set_subscription_entitlement_status(
    text, text, text, timestamptz, jsonb
) from public;
revoke execute on function safebox_patch_subscription_entitlement_metadata(text, jsonb) from public;
revoke execute on function safebox_cancel_checkout_session_entitlements(text, text, jsonb) from public;
revoke execute on function safebox_retire_openmeter_entitlements(text, uuid, timestamptz, jsonb) from public;
revoke execute on function safebox_insert_app_revenue_event(
    text, text, text, text, text, text, text, text, text, integer, text, timestamptz, jsonb
) from public;

grant execute on function safebox_insert_app_entitlement(
    text, uuid, text, text, text, text, text, text, text, timestamptz, jsonb
) to takyon_runtime;
grant execute on function safebox_set_subscription_entitlement_status(
    text, text, text, timestamptz, jsonb
) to takyon_runtime;
grant execute on function safebox_patch_subscription_entitlement_metadata(text, jsonb) to takyon_runtime;
grant execute on function safebox_cancel_checkout_session_entitlements(text, text, jsonb) to takyon_runtime;
grant execute on function safebox_retire_openmeter_entitlements(text, uuid, timestamptz, jsonb) to takyon_runtime;
grant execute on function safebox_insert_app_revenue_event(
    text, text, text, text, text, text, text, text, text, integer, text, timestamptz, jsonb
) to takyon_runtime;

-- If the runtime can SET ROLE takyon_app, takyon_app must not become an alternate direct-write path
-- to the same money/access tables.
revoke insert, update, delete on app_entitlements, app_revenue_events from takyon_runtime;
revoke insert, update, delete on app_entitlements, app_revenue_events from takyon_app;
