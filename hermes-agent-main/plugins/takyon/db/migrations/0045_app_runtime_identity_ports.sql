-- 0045_app_runtime_identity_ports.sql
-- Narrow app-runtime ports for the operator/app DB authority split.
--
-- The product app login connects as takyon_app_runtime from the start. It must not get broad
-- INSERT/UPDATE grants on app_users/app_sessions, but it still needs to bind a server-verified
-- Supabase identity to one product sub-user and mint/revoke the Takyon app session. These
-- SECURITY DEFINER functions are that bounded port. They never grant payment, entitlement, revenue,
-- usage, or operator authority.

create or replace function takyon_app_runtime_business(
    p_business_slug text
)
returns table (
    slug text,
    name text,
    status text,
    mode text,
    work_focus text
)
language sql
stable
security definer
set search_path = public, pg_temp
as $$
    select b.slug, b.name, b.status, b.mode, b.work_focus
      from businesses b
     where b.slug = trim(p_business_slug)
       and b.status = 'active'
     limit 1;
$$;

create or replace function takyon_app_control_blocker(
    p_scopes text[]
)
returns table (
    scope text,
    state text,
    reason text,
    actor text,
    updated_at timestamptz
)
language sql
stable
security definer
set search_path = public, pg_temp
as $$
    select c.scope, c.state, c.reason, c.actor, c.updated_at
      from control_states c
     where c.scope = any(p_scopes)
       and c.state in ('killed', 'paused')
     order by array_position(p_scopes, c.scope)
$$;

create or replace function takyon_app_resolve_tier(
    p_business_slug text,
    p_app_user_id uuid
)
returns text
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
    v_tier text;
begin
    select e.tier
      into v_tier
      from app_entitlements e
     where e.business_slug = p_business_slug
       and e.app_user_id = p_app_user_id
       and e.status in ('active', 'trialing')
       and lower(e.tier) not in ('', 'free', 'none', 'unentitled')
       and e.source <> 'openmeter'
     order by
       case lower(e.tier)
         when 'owner' then 0
         when 'paid' then 1
         when 'pro' then 1
         else 5
       end asc,
       e.updated_at desc
     limit 1;

    v_tier := coalesce(v_tier, 'unentitled');

    update app_users
       set tier = v_tier,
           updated_at = now()
     where business_slug = p_business_slug
       and id = p_app_user_id;

    return v_tier;
end;
$$;

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
begin
    if nullif(trim(p_business_slug), '') is null then
        raise exception 'business_slug is required';
    end if;
    if nullif(trim(p_supabase_user_id), '') is null then
        raise exception 'supabase_user_id is required';
    end if;
    if nullif(trim(p_session_hash), '') is null or length(trim(p_session_hash)) <> 64 then
        raise exception 'session_hash is required';
    end if;
    if coalesce(p_session_ttl_days, 0) <= 0 then
        raise exception 'session_ttl_days must be positive';
    end if;

    v_email := coalesce(
        nullif(lower(trim(p_email)), '')::citext,
        (trim(p_supabase_user_id) || '@supabase.local')::citext
    );

    select *
      into v_user
      from app_users u
     where u.business_slug = trim(p_business_slug)
       and u.supabase_user_id = trim(p_supabase_user_id)
     limit 1;

    if not found then
        update app_users u
           set supabase_user_id = trim(p_supabase_user_id),
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
            trim(p_supabase_user_id)
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

    insert into app_sessions (
        business_slug,
        app_user_id,
        token_hash,
        expires_at
    )
    values (
        v_user.business_slug,
        v_user.id,
        trim(p_session_hash),
        now() + make_interval(days => p_session_ttl_days)
    )
    returning id, expires_at
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

create or replace function takyon_app_validate_session(
    p_business_slug text,
    p_session_hash text
)
returns table (
    app_user_id uuid,
    business_slug text,
    email citext,
    name text,
    status text,
    tier text
)
language sql
stable
security definer
set search_path = public, pg_temp
as $$
    select u.id, u.business_slug, u.email, u.name, u.status, u.tier
      from app_sessions s
      join app_users u
        on u.business_slug = s.business_slug
       and u.id = s.app_user_id
     where s.business_slug = trim(p_business_slug)
       and s.token_hash = trim(p_session_hash)
       and s.revoked_at is null
       and s.expires_at > now()
       and u.status = 'active'
     limit 1;
$$;

create or replace function takyon_app_revoke_session(
    p_business_slug text,
    p_session_hash text
)
returns boolean
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
    v_id uuid;
begin
    if nullif(trim(p_business_slug), '') is null then
        return false;
    end if;
    if nullif(trim(p_session_hash), '') is null then
        return false;
    end if;

    update app_sessions
       set revoked_at = now()
     where business_slug = trim(p_business_slug)
       and token_hash = trim(p_session_hash)
       and revoked_at is null
     returning id
      into v_id;

    return v_id is not null;
end;
$$;

create or replace function takyon_app_service_email_recipient(
    p_business_slug text,
    p_service_session_hash text,
    p_recipient_app_user_id text
)
returns table (
    app_user_id uuid,
    business_slug text,
    email citext,
    name text,
    status text,
    tier text
)
language sql
stable
security definer
set search_path = public, pg_temp
as $$
    with service_session as (
        select 1 as authorized
          from app_sessions s
          join app_users u
            on u.business_slug = s.business_slug
           and u.id = s.app_user_id
         where s.business_slug = trim(p_business_slug)
           and s.token_hash = trim(p_service_session_hash)
           and s.revoked_at is null
           and s.expires_at > now()
           and u.status = 'active'
           and lower(u.email::text) like '%.takyon.invalid'
         limit 1
    )
    select r.id, r.business_slug, r.email, r.name, r.status, r.tier
      from service_session
      join app_users r
        on r.business_slug = trim(p_business_slug)
       and r.id::text = trim(p_recipient_app_user_id)
       and r.status = 'active'
       and lower(r.email::text) not like '%.takyon.invalid'
     limit 1;
$$;

create or replace function takyon_app_service_email_sends_today(
    p_business_slug text,
    p_service_session_hash text
)
returns bigint
language sql
stable
security definer
set search_path = public, pg_temp
as $$
    with service_session as (
        select 1
          from app_sessions s
          join app_users u
            on u.business_slug = s.business_slug
           and u.id = s.app_user_id
         where s.business_slug = trim(p_business_slug)
           and s.token_hash = trim(p_service_session_hash)
           and s.revoked_at is null
           and s.expires_at > now()
           and u.status = 'active'
           and lower(u.email::text) like '%.takyon.invalid'
         limit 1
    )
    select count(e.*)::bigint
      from service_session ss
      left join app_usage_events e
        on e.business_slug = trim(p_business_slug)
       and e.purpose = 'email_send'
       and e.created_at >= date_trunc('day', now() at time zone 'utc')
     group by ss.authorized;
$$;

create or replace function takyon_app_visible_directory_entries(
    p_business_slug text,
    p_session_hash text,
    p_limit integer default 50
)
returns table (
    user_id uuid,
    user_business_slug text,
    user_email citext,
    user_name text,
    user_status text,
    user_tier text,
    entry_id uuid,
    entry_business_slug text,
    entry_enabled boolean,
    entry_profile jsonb,
    entry_created_at timestamptz,
    entry_updated_at timestamptz
)
language sql
stable
security definer
set search_path = public, pg_temp
as $$
    with viewer as (
        select u.id as viewer_id
          from app_sessions s
          join app_users u
            on u.business_slug = s.business_slug
           and u.id = s.app_user_id
         where s.business_slug = trim(p_business_slug)
           and s.token_hash = trim(p_session_hash)
           and s.revoked_at is null
           and s.expires_at > now()
           and u.status = 'active'
         limit 1
    )
    select
        u.id,
        u.business_slug,
        u.email,
        u.name,
        u.status,
        u.tier,
        p.id,
        p.business_slug,
        p.directory_enabled,
        p.directory_profile,
        p.created_at,
        coalesce(p.directory_updated_at, p.updated_at)
      from viewer v
      join app_user_profiles p
        on p.business_slug = trim(p_business_slug)
       and p.directory_enabled = true
       and p.id <> v.viewer_id
      join app_users u
        on u.business_slug = p.business_slug
       and u.id = p.id
       and u.status = 'active'
     where not exists (
        select 1
          from app_connections c
         where c.business_slug = p.business_slug
           and c.state = 'block'
           and (
                (c.source_app_user_id = v.viewer_id and c.target_app_user_id = p.id)
                or (c.source_app_user_id = p.id and c.target_app_user_id = v.viewer_id)
           )
     )
     order by coalesce(p.directory_updated_at, p.updated_at) desc, p.id asc
     limit greatest(1, least(coalesce(p_limit, 50), 100));
$$;

create or replace function takyon_app_visible_directory_entry(
    p_business_slug text,
    p_session_hash text,
    p_target_app_user_id text,
    p_target_email text
)
returns table (
    user_id uuid,
    user_business_slug text,
    user_email citext,
    user_name text,
    user_status text,
    user_tier text,
    entry_id uuid,
    entry_business_slug text,
    entry_enabled boolean,
    entry_profile jsonb,
    entry_created_at timestamptz,
    entry_updated_at timestamptz
)
language sql
stable
security definer
set search_path = public, pg_temp
as $$
    with viewer as (
        select u.id as viewer_id
          from app_sessions s
          join app_users u
            on u.business_slug = s.business_slug
           and u.id = s.app_user_id
         where s.business_slug = trim(p_business_slug)
           and s.token_hash = trim(p_session_hash)
           and s.revoked_at is null
           and s.expires_at > now()
           and u.status = 'active'
         limit 1
    )
    select
        u.id,
        u.business_slug,
        u.email,
        u.name,
        u.status,
        u.tier,
        p.id,
        p.business_slug,
        p.directory_enabled,
        p.directory_profile,
        p.created_at,
        coalesce(p.directory_updated_at, p.updated_at)
      from viewer v
      join app_user_profiles p
        on p.business_slug = trim(p_business_slug)
       and p.directory_enabled = true
       and p.id <> v.viewer_id
      join app_users u
        on u.business_slug = p.business_slug
       and u.id = p.id
       and u.status = 'active'
     where (
            (nullif(trim(coalesce(p_target_app_user_id, '')), '') is not null
             and p.id::text = trim(p_target_app_user_id))
            or
            (nullif(trim(coalesce(p_target_email, '')), '') is not null
             and lower(u.email::text) = lower(trim(p_target_email)))
       )
       and not exists (
        select 1
          from app_connections c
         where c.business_slug = p.business_slug
           and c.state = 'block'
           and (
                (c.source_app_user_id = v.viewer_id and c.target_app_user_id = p.id)
                or (c.source_app_user_id = p.id and c.target_app_user_id = v.viewer_id)
           )
       )
     limit 1;
$$;

create or replace function takyon_app_record_event(
    p_scope text,
    p_business_slug text,
    p_event_type text,
    p_payload_json text
)
returns text
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
    v_business_slug text := trim(coalesce(p_business_slug, ''));
    v_scope text := trim(coalesce(p_scope, ''));
    v_event_type text := trim(coalesce(p_event_type, ''));
    v_id text := replace(gen_random_uuid()::text, '-', '');
begin
    if v_business_slug = '' then
        raise exception 'business_slug is required';
    end if;
    if v_scope <> ('business:' || v_business_slug || '/app') then
        raise exception 'app runtime events must use the app scope for their business';
    end if;
    if v_event_type = '' or v_event_type not like 'app.%' then
        raise exception 'app runtime event_type must start with app.';
    end if;

    insert into events (id, scope, business_slug, event_type, payload_json, created_at)
    values (v_id, v_scope, v_business_slug, v_event_type, coalesce(p_payload_json, '{}'), now()::text);

    return v_id;
end;
$$;

create or replace function takyon_app_media_usage(
    p_business_slug text,
    p_app_user_id text
)
returns table (
    user_bytes bigint,
    business_bytes bigint
)
language sql
stable
security definer
set search_path = public, pg_temp
as $$
    select
        coalesce(sum(size_bytes) filter (where app_user_id = trim(p_app_user_id)), 0)::bigint as user_bytes,
        coalesce(sum(size_bytes), 0)::bigint as business_bytes
      from app_media
     where business_slug = trim(p_business_slug)
       and nullif(trim(p_app_user_id), '') is not null;
$$;

drop policy if exists takyon_app_media_write on app_media;
create policy takyon_app_media_write
    on app_media
    for all
    using (
        takyon_rls_bypass()
        or (
            business_slug = takyon_rls_business_slug()
            and app_user_id = coalesce(takyon_rls_effective_app_user_id()::text, '')
        )
    )
    with check (
        takyon_rls_bypass()
        or (
            business_slug = takyon_rls_business_slug()
            and app_user_id = coalesce(takyon_rls_effective_app_user_id()::text, '')
        )
    );

drop policy if exists takyon_app_checkout_intents_write on app_checkout_intents;
create policy takyon_app_checkout_intents_write
    on app_checkout_intents
    for all
    using (
        takyon_rls_bypass()
        or (
            business_slug = takyon_rls_business_slug()
            and app_user_id = takyon_rls_effective_app_user_id()
        )
    )
    with check (
        takyon_rls_bypass()
        or (
            business_slug = takyon_rls_business_slug()
            and app_user_id = takyon_rls_effective_app_user_id()
        )
    );

revoke execute on function takyon_app_resolve_tier(text, uuid) from public;
revoke execute on function takyon_app_runtime_business(text) from public;
revoke execute on function takyon_app_control_blocker(text[]) from public;
revoke execute on function takyon_app_bind_supabase_session(text, text, text, text, text, integer) from public;
revoke execute on function takyon_app_validate_session(text, text) from public;
revoke execute on function takyon_app_revoke_session(text, text) from public;
revoke execute on function takyon_app_service_email_recipient(text, text, text) from public;
revoke execute on function takyon_app_service_email_sends_today(text, text) from public;
revoke execute on function takyon_app_visible_directory_entries(text, text, integer) from public;
revoke execute on function takyon_app_visible_directory_entry(text, text, text, text) from public;
revoke execute on function takyon_app_record_event(text, text, text, text) from public;
revoke execute on function takyon_app_media_usage(text, text) from public;

grant execute on function takyon_app_runtime_business(text)
    to takyon_app_runtime, takyon_app;
grant execute on function takyon_app_control_blocker(text[])
    to takyon_app_runtime, takyon_app;
-- Internal helper only. App runtime must not call this directly with arbitrary
-- {business, app_user}; takyon_app_bind_supabase_session() reaches it while
-- minting exactly one verified Supabase session.
grant execute on function takyon_app_bind_supabase_session(text, text, text, text, text, integer)
    to takyon_app_runtime, takyon_app;
grant execute on function takyon_app_validate_session(text, text)
    to takyon_app_runtime, takyon_app;
grant execute on function takyon_app_revoke_session(text, text)
    to takyon_app_runtime, takyon_app;
grant execute on function takyon_app_service_email_recipient(text, text, text)
    to takyon_app_runtime, takyon_app;
grant execute on function takyon_app_service_email_sends_today(text, text)
    to takyon_app_runtime, takyon_app;
grant execute on function takyon_app_visible_directory_entries(text, text, integer)
    to takyon_app_runtime, takyon_app;
grant execute on function takyon_app_visible_directory_entry(text, text, text, text)
    to takyon_app_runtime, takyon_app;
grant execute on function takyon_app_record_event(text, text, text, text)
    to takyon_app_runtime, takyon_app;
grant execute on function takyon_app_media_usage(text, text)
    to takyon_app_runtime, takyon_app;

revoke select on app_users, app_sessions
    from takyon_app_runtime, takyon_app;

grant select on app_surface_contracts, app_plan_policies
    to takyon_app_runtime, takyon_app;
