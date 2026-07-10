-- Session-bound product checkout-intent creation.
--
-- The app caller presents only its business, opaque session hash, requested plan, idempotency
-- reference, and non-authoritative metadata. The definer port derives the active app user and
-- email from that live session, then serializes the subscription/open-intent checks with the
-- insert. App roles lose direct INSERT/DELETE so they cannot bypass these checks with a caller-
-- supplied app_user_id.

begin;

create or replace function takyon_app_create_checkout_intent(
    p_business_slug text,
    p_session_hash text,
    p_plan_key text,
    p_client_reference_id text,
    p_metadata jsonb
)
returns table (
    id uuid,
    business_slug text,
    app_user_id uuid,
    plan_key text,
    status text,
    client_reference_id text,
    stripe_checkout_session_id text,
    checkout_url text,
    customer_email text,
    metadata jsonb,
    created_at timestamptz,
    updated_at timestamptz,
    completed_at timestamptz
)
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
    v_business text := trim(coalesce(p_business_slug, ''));
    v_session_hash text := trim(coalesce(p_session_hash, ''));
    v_plan_key text := trim(coalesce(p_plan_key, ''));
    v_reference text := trim(coalesce(p_client_reference_id, ''));
    v_app_user_id uuid;
    v_revalidated_user_id uuid;
    v_customer_email text;
    v_plan_id uuid;
    v_other_plan text;
    v_existing app_checkout_intents%rowtype;
begin
    if v_business = '' or v_session_hash = '' or v_plan_key = '' or v_reference = '' then
        raise exception 'app_checkout_invalid_request' using errcode = '22023';
    end if;

    -- First resolve only enough identity to choose the unforgeable per-user lock. The same session
    -- is revalidated under row locks below, so revocation/closure cannot race the insert.
    select u.id, u.email::text
      into v_app_user_id, v_customer_email
      from app_sessions s
      join app_users u
        on u.business_slug = s.business_slug
       and u.id = s.app_user_id
      join businesses b on b.slug = s.business_slug
     where s.business_slug = v_business
       and s.token_hash = v_session_hash
       and s.revoked_at is null
       and s.expires_at > now()
       and u.status = 'active'
       and b.status = 'active'
     limit 1;
    if not found then
        raise exception 'app_checkout_invalid_session' using errcode = '28000';
    end if;

    perform pg_advisory_xact_lock(hashtextextended(
        'takyon-app-checkout-user:' || v_business || ':' || v_app_user_id::text, 0
    ));
    perform pg_advisory_xact_lock(hashtextextended(
        'takyon-plan-economics:' || v_business || ':' || v_plan_key, 0
    ));

    -- Revalidate after both advisory locks and hold the identity rows through this statement.
    select u.id, u.email::text
      into v_revalidated_user_id, v_customer_email
      from app_sessions s
      join app_users u
        on u.business_slug = s.business_slug
       and u.id = s.app_user_id
      join businesses b on b.slug = s.business_slug
     where s.business_slug = v_business
       and s.token_hash = v_session_hash
       and s.revoked_at is null
       and s.expires_at > now()
       and u.status = 'active'
       and b.status = 'active'
       and u.id = v_app_user_id
     limit 1
       for update of s, u;
    if not found or v_revalidated_user_id is distinct from v_app_user_id then
        raise exception 'app_checkout_invalid_session' using errcode = '28000';
    end if;

    select p.id
      into v_plan_id
      from app_plan_policies p
     where p.business_slug = v_business
       and p.plan_key = v_plan_key
       and p.saleable
       and p.price_cents > 0
       and lower(p.currency) = 'usd'
       and p.billing_interval = 'month'
       and lower(coalesce(p.metadata->>'status', p.metadata->>'lifecycle_status', ''))
           not in ('archived', 'deprecated', 'disabled', 'inactive', 'retired')
     limit 1
       for share;
    if not found then
        raise exception 'app_checkout_plan_unavailable' using errcode = 'P0001';
    end if;

    -- Lock every Stripe subscription row before evaluating terminal state. The app-user row lock
    -- also serializes a concurrent new entitlement insert through its FK.
    perform 1
      from app_entitlements e
     where e.business_slug = v_business
       and e.app_user_id = v_app_user_id
       and e.source = 'stripe'
       and e.stripe_subscription_id is not null
       for update;
    if exists (
        select 1
          from app_entitlements e
         where e.business_slug = v_business
           and e.app_user_id = v_app_user_id
           and e.source = 'stripe'
           and e.stripe_subscription_id is not null
           and lower(e.status) not in ('canceled', 'cancelled', 'sandbox_retired')
    ) then
        raise exception 'app_checkout_active_subscription' using errcode = 'P0001';
    end if;

    select i.plan_key
      into v_other_plan
      from app_checkout_intents i
     where i.business_slug = v_business
       and i.app_user_id = v_app_user_id
       and i.status in ('created', 'stripe_creating', 'pending')
       and i.created_at > now() - interval '25 hours'
       and i.plan_key <> v_plan_key
     order by i.created_at desc, i.id desc
     limit 1
       for update;
    if found then
        raise exception 'app_checkout_already_open:%', v_other_plan using errcode = 'P0001';
    end if;

    select i.*
      into v_existing
      from app_checkout_intents i
     where i.business_slug = v_business
       and i.app_user_id = v_app_user_id
       and i.plan_key = v_plan_key
       and i.status in ('created', 'stripe_creating', 'pending')
       and i.created_at > now() - interval '25 hours'
     order by i.created_at desc, i.id desc
     limit 1
       for update;
    if found then
        return query select
            v_existing.id, v_existing.business_slug, v_existing.app_user_id,
            v_existing.plan_key, v_existing.status, v_existing.client_reference_id,
            v_existing.stripe_checkout_session_id, v_existing.checkout_url,
            v_existing.customer_email, v_existing.metadata, v_existing.created_at,
            v_existing.updated_at, v_existing.completed_at;
        return;
    end if;

    -- The reference is globally unique. Lock it explicitly so a cross-user collision becomes a
    -- stable refusal instead of a raw unique-constraint race; it can never return another user's
    -- row.
    perform pg_advisory_xact_lock(hashtextextended(
        'takyon-app-checkout-reference:' || v_reference, 0
    ));
    if exists (
        select 1 from app_checkout_intents i where i.client_reference_id = v_reference
    ) then
        raise exception 'app_checkout_reference_conflict' using errcode = 'P0001';
    end if;

    return query
    insert into app_checkout_intents as i (
        business_slug, app_user_id, plan_key, status, client_reference_id,
        customer_email, metadata
    ) values (
        v_business, v_app_user_id, v_plan_key, 'created', v_reference,
        v_customer_email, coalesce(p_metadata, '{}'::jsonb)
    )
    returning i.id, i.business_slug, i.app_user_id, i.plan_key, i.status,
              i.client_reference_id, i.stripe_checkout_session_id, i.checkout_url,
              i.customer_email, i.metadata, i.created_at, i.updated_at, i.completed_at;
end;
$$;

alter function takyon_app_create_checkout_intent(text, text, text, text, jsonb)
    owner to takyon_migration;

revoke execute on function takyon_app_create_checkout_intent(text, text, text, text, jsonb)
    from public, takyon_operator_runtime, takyon_safebox_authority, takyon_runtime,
         takyon_operator_access, safebox;
grant execute on function takyon_app_create_checkout_intent(text, text, text, text, jsonb)
    to takyon_app_runtime, takyon_app;

-- Creation must pass through the session-bound port. App code still needs the narrow state-link
-- updates used after Stripe returns a session URL; no app caller may rewrite identity/plan/email.
revoke insert, delete, update on app_checkout_intents
    from takyon_app_runtime, takyon_app;
grant update (status, stripe_checkout_session_id, checkout_url, updated_at)
    on app_checkout_intents to takyon_app_runtime, takyon_app;

commit;
