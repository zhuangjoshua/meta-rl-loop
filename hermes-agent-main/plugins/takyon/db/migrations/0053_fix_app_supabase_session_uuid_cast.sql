-- Fix app-plane Supabase session binding after the split-role cutover.
--
-- app_users.supabase_user_id is a uuid column, while the SECURITY DEFINER port accepts the verified
-- Supabase subject as text from Python. Compare and write a single explicit uuid cast instead of
-- relying on a nonexistent uuid=text operator.

create or replace function takyon_app_bind_supabase_session(
    p_business_slug text,
    p_supabase_user_id text,
    p_email text,
    p_name text,
    p_session_hash text,
    p_session_ttl_days integer default 30
)
returns table (
    app_user_id uuid,
    business_slug text,
    email citext,
    name text,
    status text,
    tier text,
    session_id uuid,
    session_expires_at timestamptz
)
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
    v_user app_users%rowtype;
    v_email citext;
    v_supabase_user_id uuid;
begin
    if nullif(trim(p_business_slug), '') is null then
        raise exception 'business_slug is required';
    end if;
    if nullif(trim(p_supabase_user_id), '') is null then
        raise exception 'supabase_user_id is required';
    end if;
    v_supabase_user_id := trim(p_supabase_user_id)::uuid;
    if nullif(trim(p_session_hash), '') is null or length(trim(p_session_hash)) <> 64 then
        raise exception 'session_hash is required';
    end if;
    if coalesce(p_session_ttl_days, 0) <= 0 then
        raise exception 'session_ttl_days must be positive';
    end if;

    v_email := coalesce(
        nullif(lower(trim(p_email)), '')::citext,
        (v_supabase_user_id::text || '@supabase.local')::citext
    );

    select *
      into v_user
      from app_users u
     where u.business_slug = trim(p_business_slug)
       and u.supabase_user_id = v_supabase_user_id
     limit 1;

    if not found then
        update app_users u
           set supabase_user_id = v_supabase_user_id,
               name = coalesce(p_name, u.name),
               updated_at = now()
         where u.business_slug = trim(p_business_slug)
           and u.email = v_email
           and u.supabase_user_id is null
         returning *
          into v_user;
    end if;

    if not found then
        insert into app_users (
            business_slug,
            email,
            name,
            status,
            tier,
            supabase_user_id
        )
        values (
            trim(p_business_slug),
            v_email,
            p_name,
            'active',
            'unentitled',
            v_supabase_user_id
        )
        returning *
         into v_user;
    end if;

    if v_user.status <> 'active' then
        update app_sessions
           set revoked_at = now()
         where business_slug = v_user.business_slug
           and app_user_id = v_user.id
           and revoked_at is null;
        raise exception 'inactive app user: %', v_user.id;
    end if;

    v_user.tier := takyon_app_resolve_tier(v_user.business_slug, v_user.id);

    insert into app_sessions (business_slug, app_user_id, token_hash, expires_at)
    values (
        v_user.business_slug,
        v_user.id,
        trim(p_session_hash),
        now() + make_interval(days => p_session_ttl_days)
    )
    returning id, app_sessions.expires_at
      into session_id, session_expires_at;

    app_user_id := v_user.id;
    business_slug := v_user.business_slug;
    email := v_user.email;
    name := v_user.name;
    status := v_user.status;
    tier := v_user.tier;
    return next;
end;
$$;

revoke execute on function takyon_app_bind_supabase_session(text, text, text, text, text, integer) from public;
grant execute on function takyon_app_bind_supabase_session(text, text, text, text, text, integer)
    to takyon_app, takyon_app_runtime;
