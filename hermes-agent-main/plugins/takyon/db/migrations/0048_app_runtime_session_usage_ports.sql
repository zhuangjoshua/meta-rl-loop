-- 0048_app_runtime_session_usage_ports.sql
-- Session-bound app-runtime usage write ports.
--
-- App roles must not execute the generic safebox_* usage gates by passing arbitrary
-- business/app_user arguments. Product app traffic presents an app session; these ports validate
-- that session inside SECURITY DEFINER code, derive the app_user_id there, and only then call the
-- generic safebox-owned money gate.

create or replace function takyon_app_session_plan(
    p_business_slug text,
    p_session_hash text
)
returns jsonb
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
    v_user_id uuid;
    v_entitlement jsonb;
    v_plan jsonb;
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
        return jsonb_build_object('entitlement', null, 'plan', null);
    end if;

    with active_entitlement as (
        select e.*
          from app_entitlements e
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
         limit 1
    )
    select to_jsonb(e), to_jsonb(p)
      into v_entitlement, v_plan
      from active_entitlement e
      left join lateral (
          select p.*
            from app_plan_policies p
           where p.business_slug = p_business_slug
             and (
                  (e.plan_key is not null and p.plan_key = e.plan_key)
                  or (e.plan_key is null and p.tier = e.tier)
             )
           order by
             case when e.plan_key is not null and p.plan_key = e.plan_key then 0 else 1 end asc,
             p.price_cents asc,
             p.plan_key asc
           limit 1
      ) p on true;

    return jsonb_build_object('entitlement', v_entitlement, 'plan', v_plan);
end;
$$;

create or replace function takyon_app_reserve_usage(
    p_business_slug text,
    p_session_hash text,
    p_expected_app_user_id uuid,
    p_estimated_cost_microusd bigint,
    p_reservation_key text,
    p_user_monthly_limit_microusd bigint,
    p_app_user_tier text,
    p_purpose text,
    p_route text,
    p_provider text,
    p_model text,
    p_metadata jsonb
)
returns safebox_usage_gate_result
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
    r safebox_usage_gate_result;
    v_user_id uuid;
    v_user_tier text;
begin
    select u.id, u.tier
      into v_user_id, v_user_tier
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
        r.refusal := 'app_user_not_found';
        return r;
    end if;
    if p_expected_app_user_id is not null and p_expected_app_user_id <> v_user_id then
        r.refusal := 'app_user_not_found';
        return r;
    end if;

    return safebox_reserve_usage(
        p_business_slug,
        p_estimated_cost_microusd,
        p_reservation_key,
        v_user_id,
        p_user_monthly_limit_microusd,
        coalesce(nullif(p_app_user_tier, ''), nullif(v_user_tier, '')),
        p_purpose,
        p_route,
        p_provider,
        p_model,
        coalesce(p_metadata, '{}'::jsonb)
    );
end;
$$;

create or replace function takyon_app_settle_usage(
    p_business_slug text,
    p_session_hash text,
    p_reservation_key text,
    p_actual_cost_microusd bigint,
    p_input_tokens integer,
    p_output_tokens integer,
    p_provider_request_id text,
    p_provider text,
    p_model text,
    p_metadata jsonb
)
returns safebox_usage_gate_result
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
    r safebox_usage_gate_result;
    v_user_id uuid;
    v_owner_id uuid;
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
        r.refusal := 'unknown_reservation';
        return r;
    end if;

    select e.app_user_id
      into v_owner_id
      from app_usage_events e
     where e.business_slug = p_business_slug
       and e.reservation_key = p_reservation_key
     limit 1;

    if v_owner_id is null or v_owner_id <> v_user_id then
        r.refusal := 'unknown_reservation';
        return r;
    end if;

    return safebox_settle_usage(
        p_business_slug,
        p_reservation_key,
        p_actual_cost_microusd,
        p_input_tokens,
        p_output_tokens,
        p_provider_request_id,
        p_provider,
        p_model,
        p_metadata
    );
end;
$$;

create or replace function takyon_app_release_usage(
    p_business_slug text,
    p_session_hash text,
    p_reservation_key text,
    p_error text,
    p_metadata jsonb
)
returns safebox_usage_gate_result
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
    r safebox_usage_gate_result;
    v_user_id uuid;
    v_owner_id uuid;
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
        r.refusal := 'unknown_reservation';
        return r;
    end if;

    select e.app_user_id
      into v_owner_id
      from app_usage_events e
     where e.business_slug = p_business_slug
       and e.reservation_key = p_reservation_key
     limit 1;

    if v_owner_id is null or v_owner_id <> v_user_id then
        r.refusal := 'unknown_reservation';
        return r;
    end if;

    return safebox_release_usage(
        p_business_slug,
        p_reservation_key,
        p_error,
        p_metadata
    );
end;
$$;

revoke execute on function takyon_app_session_plan(text, text) from public;
revoke execute on function takyon_app_reserve_usage(
    text, text, uuid, bigint, text, bigint, text, text, text, text, text, jsonb) from public;
revoke execute on function takyon_app_settle_usage(
    text, text, text, bigint, integer, integer, text, text, text, jsonb) from public;
revoke execute on function takyon_app_release_usage(text, text, text, text, jsonb) from public;

grant execute on function takyon_app_session_plan(text, text)
    to takyon_app_runtime, takyon_app;
grant execute on function takyon_app_reserve_usage(
    text, text, uuid, bigint, text, bigint, text, text, text, text, text, jsonb)
    to takyon_app_runtime, takyon_app;
grant execute on function takyon_app_settle_usage(
    text, text, text, bigint, integer, integer, text, text, text, jsonb)
    to takyon_app_runtime, takyon_app;
grant execute on function takyon_app_release_usage(text, text, text, text, jsonb)
    to takyon_app_runtime, takyon_app;

revoke execute on function safebox_reserve_usage(
    text, bigint, text, uuid, bigint, text, text, text, text, text, jsonb)
    from takyon_app_runtime, takyon_app;
revoke execute on function safebox_settle_usage(
    text, text, bigint, integer, integer, text, text, text, jsonb)
    from takyon_app_runtime, takyon_app;
revoke execute on function safebox_release_usage(text, text, text, jsonb)
    from takyon_app_runtime, takyon_app;
revoke execute on function safebox_reconcile_held_usage(bigint)
    from takyon_app_runtime, takyon_app;
