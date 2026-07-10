-- 0073_operator_ssh_profile_access.sql
--
-- A narrow, auditable exception to paid product access for Four Manifold staff testing.
-- This is NOT a web/admin role and NOT a second checkout path:
--
--   * the target must already be an active product profile with a fresh verified Supabase
--     email binding and active session; this command never creates/adopts a profile;
--   * the profile email must be exactly @fourmanifold.com;
--   * the grant must name an existing monthly app plan, so feature/model allowlists and the
--     existing per-user provider-spend ceiling remain authoritative;
--   * only the isolated takyon_migration credential can execute grant/revoke/list. That credential
--     is stored under root's unreadable home on the operator host, absent from every service env,
--     and the tracked launcher independently requires key-only root SSH before reading it;
--   * normal app_entitlements.grant_entitlement remains Stripe-evidence-only.
--
-- app_operator_access_grants is the private DB audit/source record. Product and operator web
-- roles receive no privileges on it. The linked app_entitlements row is the normal runtime
-- projection, with source='operator_ssh' and no Stripe identifiers.

-- Future migration-created functions must opt into callers explicitly. Without this, PostgreSQL's
-- default PUBLIC EXECUTE would silently expand runtime callers after a later deploy.
alter default privileges for role takyon_migration
    revoke execute on functions from public;
alter default privileges
    revoke execute on functions from public;

-- Durable proof that a verified Supabase JWT supplied the current email for this exact subject.
-- Existing profiles deliberately receive no backfill: staff must log in once after deployment,
-- making historical/stale rows ineligible for free access.
create table if not exists app_supabase_verified_email_bindings (
    business_slug       text not null,
    app_user_id         uuid not null,
    supabase_user_id    uuid not null,
    verified_email      citext not null,
    verified_at         timestamptz not null,
    last_session_id     uuid not null references app_sessions (id) on delete cascade,
    primary key (business_slug, app_user_id),
    foreign key (business_slug, app_user_id)
        references app_users (business_slug, id) on delete cascade
);

create unique index if not exists app_supabase_verified_email_binding_subject_idx
    on app_supabase_verified_email_bindings (business_slug, supabase_user_id);

alter table app_supabase_verified_email_bindings enable row level security;
alter table app_supabase_verified_email_bindings force row level security;
drop policy if exists app_supabase_verified_email_bindings_migration_only
    on app_supabase_verified_email_bindings;
create policy app_supabase_verified_email_bindings_migration_only
    on app_supabase_verified_email_bindings
    for all
    using (current_user = 'takyon_migration')
    with check (current_user = 'takyon_migration');

create table if not exists app_operator_access_grants (
    id                       uuid primary key default gen_random_uuid(),
    business_slug            text not null references businesses (slug) on delete cascade,
    app_user_id              uuid not null,
    verified_email           citext not null,
    profile_supabase_user_id uuid not null,
    profile_verified_at      timestamptz not null,
    plan_key                 text not null,
    tier                     text not null check (length(tier) > 0),
    entitlement_id           uuid not null unique references app_entitlements (id) on delete cascade,
    status                   text not null default 'active'
                                 check (status in ('active', 'revoked')),
    grant_source             text not null default 'root_ssh'
                                 check (grant_source = 'root_ssh'),
    grant_request_id         uuid not null unique,
    granted_at               timestamptz not null default now(),
    granted_from             inet not null,
    granted_on_host          text not null check (length(granted_on_host) > 0),
    revoke_request_id        uuid unique,
    revoked_at               timestamptz,
    revoked_from             inet,
    revoked_on_host          text,
    revoked_reason           text,
    foreign key (business_slug, app_user_id)
        references app_users (business_slug, id) on delete cascade,
    foreign key (business_slug, plan_key)
        references app_plan_policies (business_slug, plan_key)
        on delete no action deferrable initially deferred
);

alter table app_operator_access_grants
    add column if not exists profile_verified_at timestamptz,
    add column if not exists revoked_reason text;
update app_operator_access_grants
   set profile_verified_at = coalesce(profile_verified_at, granted_at)
 where profile_verified_at is null;
alter table app_operator_access_grants
    alter column profile_verified_at set not null;

create unique index if not exists app_operator_access_grants_one_active_user_idx
    on app_operator_access_grants (business_slug, app_user_id)
    where status = 'active';

create unique index if not exists app_operator_access_grants_one_active_email_idx
    on app_operator_access_grants (business_slug, verified_email)
    where status = 'active';

create index if not exists app_operator_access_grants_audit_idx
    on app_operator_access_grants (business_slug, granted_at desc);

alter table app_operator_access_grants enable row level security;
alter table app_operator_access_grants force row level security;
drop policy if exists app_operator_access_grants_migration_only
    on app_operator_access_grants;
create policy app_operator_access_grants_migration_only
    on app_operator_access_grants
    for all
    using (current_user = 'takyon_migration')
    with check (current_user = 'takyon_migration');

create or replace function operator_ssh_sync_user_tier(
    p_business_slug text,
    p_app_user_id uuid
)
returns void
language sql
security definer
set search_path = public, pg_temp
as $$
    update app_users u
       set tier = coalesce(
               (
                   select e.tier
                     from app_entitlements e
                    where e.business_slug = p_business_slug
                      and e.app_user_id = p_app_user_id
                      and e.status in ('active', 'trialing')
                      and lower(e.tier) not in ('', 'free', 'none', 'unentitled')
                      and e.source <> 'openmeter'
                    order by case lower(e.tier)
                               when 'owner' then 0
                               when 'paid' then 1
                               when 'pro' then 1
                               else 5
                             end,
                             e.updated_at desc
                    limit 1
               ),
               'unentitled'
           ),
           updated_at = now()
     where u.business_slug = p_business_slug
       and u.id = p_app_user_id;
$$;

-- Revoke only stale SSH grants. Stripe entitlements are never selected or changed. This is called
-- both from the authenticated-login rail and before every SSH grant/list/revoke operation, so an
-- email/subject/status change cannot resurrect old free access.
create or replace function operator_ssh_revoke_stale_access(
    p_business_slug text default null,
    p_app_user_id uuid default null,
    p_reason text default 'verified_identity_stale'
)
returns bigint
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
    v_grant app_operator_access_grants%rowtype;
    v_count bigint := 0;
begin
    for v_grant in
        select g.*
          from app_operator_access_grants g
          left join businesses business
            on business.slug = g.business_slug
          left join app_users u
            on u.business_slug = g.business_slug
           and u.id = g.app_user_id
          left join app_supabase_verified_email_bindings b
            on b.business_slug = g.business_slug
           and b.app_user_id = g.app_user_id
         where g.status = 'active'
           and (p_business_slug is null or g.business_slug = p_business_slug)
           and (p_app_user_id is null or g.app_user_id = p_app_user_id)
           and (
               business.slug is null
               or business.status <> 'active'
               or u.id is null
               or u.status <> 'active'
               or u.supabase_user_id is distinct from g.profile_supabase_user_id
               or lower(u.email::text) <> lower(g.verified_email::text)
               or b.app_user_id is null
               or b.supabase_user_id is distinct from g.profile_supabase_user_id
               or lower(b.verified_email::text) <> lower(g.verified_email::text)
           )
         for update of g
    loop
        update app_operator_access_grants
           set status = 'revoked',
               revoked_at = coalesce(revoked_at, now()),
               revoked_reason = coalesce(nullif(trim(p_reason), ''), 'verified_identity_stale')
         where id = v_grant.id
           and status = 'active';
        if found then
            update app_entitlements
               set status = 'cancelled',
                   metadata = metadata || jsonb_build_object(
                       'operator_access_revoked', true,
                       'operator_access_revoked_at', now(),
                       'operator_access_revoked_reason',
                       coalesce(nullif(trim(p_reason), ''), 'verified_identity_stale')
                   ),
                   updated_at = now()
             where id = v_grant.entitlement_id
               and source = 'operator_ssh';
            perform operator_ssh_sync_user_tier(v_grant.business_slug, v_grant.app_user_id);
            v_count := v_count + 1;
        end if;
    end loop;
    return v_count;
end;
$$;

create or replace function operator_ssh_revoke_on_app_user_change()
returns trigger
language plpgsql
security definer
set search_path = public, pg_temp
as $$
begin
    if new.status <> 'active'
       or new.email is distinct from old.email
       or new.supabase_user_id is distinct from old.supabase_user_id then
        perform operator_ssh_revoke_stale_access(
            new.business_slug,
            new.id,
            case
                when new.status <> 'active' then 'profile_inactive'
                else 'verified_identity_changed'
            end
        );
    end if;
    return new;
end;
$$;

drop trigger if exists operator_ssh_revoke_on_app_user_change_trigger on app_users;
create trigger operator_ssh_revoke_on_app_user_change_trigger
after update of email, supabase_user_id, status on app_users
for each row execute function operator_ssh_revoke_on_app_user_change();

create or replace function operator_ssh_revoke_on_business_change()
returns trigger
language plpgsql
security definer
set search_path = public, pg_temp
as $$
begin
    if new.status <> 'active' and new.status is distinct from old.status then
        perform operator_ssh_revoke_stale_access(new.slug, null, 'business_inactive');
    end if;
    return new;
end;
$$;

drop trigger if exists operator_ssh_revoke_on_business_change_trigger on businesses;
create trigger operator_ssh_revoke_on_business_change_trigger
after update of status on businesses
for each row execute function operator_ssh_revoke_on_business_change();

-- Record the verified email supplied by the verified-Supabase-JWT caller every time a product
-- session is minted. The previous function kept the original email when a Supabase subject's email
-- changed; that stale value is unsafe for a domain-bound staff grant, so this replacement updates
-- it atomically and the trigger above revokes any old grant before the new binding lands.
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
    if nullif(trim(p_email), '') is null then
        raise exception 'verified supabase email is required';
    end if;
    v_email := lower(trim(p_email))::citext;
    if nullif(trim(p_session_hash), '') is null or length(trim(p_session_hash)) <> 64 then
        raise exception 'session_hash is required';
    end if;
    if coalesce(p_session_ttl_days, 0) <= 0 then
        raise exception 'session_ttl_days must be positive';
    end if;

    select *
      into v_user
      from app_users u
     where u.business_slug = trim(p_business_slug)
       and u.supabase_user_id = v_supabase_user_id
     limit 1;

    if found then
        update app_users u
           set email = v_email,
               name = coalesce(p_name, u.name),
               updated_at = now()
         where u.business_slug = v_user.business_slug
           and u.id = v_user.id
         returning * into v_user;
    else
        update app_users u
           set supabase_user_id = v_supabase_user_id,
               name = coalesce(p_name, u.name),
               updated_at = now()
         where u.business_slug = trim(p_business_slug)
           and u.email = v_email
           and u.supabase_user_id is null
         returning * into v_user;
    end if;

    if not found then
        insert into app_users (
            business_slug, email, name, status, tier, supabase_user_id
        ) values (
            trim(p_business_slug), v_email, p_name, 'active', 'unentitled', v_supabase_user_id
        ) returning * into v_user;
    end if;

    if v_user.status <> 'active' then
        update app_sessions
           set revoked_at = now()
         where app_sessions.business_slug = v_user.business_slug
           and app_sessions.app_user_id = v_user.id
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

    insert into app_supabase_verified_email_bindings (
        business_slug, app_user_id, supabase_user_id, verified_email, verified_at, last_session_id
    ) values (
        v_user.business_slug, v_user.id, v_supabase_user_id, v_email, now(), session_id
    )
    on conflict on constraint app_supabase_verified_email_bindings_pkey do update
       set supabase_user_id = excluded.supabase_user_id,
           verified_email = excluded.verified_email,
           verified_at = excluded.verified_at,
           last_session_id = excluded.last_session_id;

    app_user_id := v_user.id;
    business_slug := v_user.business_slug;
    email := v_user.email;
    name := v_user.name;
    status := v_user.status;
    tier := v_user.tier;
    return next;
end;
$$;

create or replace function operator_ssh_grant_app_access(
    p_business_slug text,
    p_email text,
    p_plan_key text,
    p_request_id uuid,
    p_ssh_client inet,
    p_operator_host text
)
returns table (
    grant_id uuid,
    entitlement_id uuid,
    app_user_id uuid,
    verified_email text,
    plan_key text,
    tier text,
    status text,
    changed boolean
)
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
    v_email text := lower(trim(coalesce(p_email, '')));
    v_business text := trim(coalesce(p_business_slug, ''));
    v_plan_key text := trim(coalesce(p_plan_key, ''));
    v_host text := trim(coalesce(p_operator_host, ''));
    v_user app_users%rowtype;
    v_binding app_supabase_verified_email_bindings%rowtype;
    v_plan app_plan_policies%rowtype;
    v_existing app_operator_access_grants%rowtype;
    v_grant_id uuid := gen_random_uuid();
    v_entitlement_id uuid := gen_random_uuid();
begin
    if session_user <> 'takyon_migration' then
        raise exception 'operator_migration_role_required' using errcode = '42501';
    end if;
    if v_business = '' or v_plan_key = '' or p_request_id is null
       or p_ssh_client is null or v_host = '' then
        raise exception 'complete_operator_ssh_grant_context_required' using errcode = '22023';
    end if;
    if v_email !~ '^[^@[:space:]]+@fourmanifold[.]com$' then
        raise exception 'fourmanifold_email_required' using errcode = '22023';
    end if;

    perform operator_ssh_revoke_stale_access(v_business, null, 'verified_identity_stale');

    if not exists (
        select 1 from businesses b where b.slug = v_business and b.status = 'active'
    ) then
        raise exception 'active_business_required' using errcode = 'P0001';
    end if;

    -- A request-id replay returns its original durable receipt and cannot be rebound to a
    -- different profile/plan.
    select g.*
      into v_existing
      from app_operator_access_grants g
     where g.grant_request_id = p_request_id;
    if found then
        if v_existing.business_slug <> v_business
           or lower(v_existing.verified_email::text) <> v_email
           or v_existing.plan_key <> v_plan_key then
            raise exception 'grant_request_id_scope_mismatch' using errcode = '22023';
        end if;
        return query
        select v_existing.id, v_existing.entitlement_id, v_existing.app_user_id,
               v_existing.verified_email::text, v_existing.plan_key, v_existing.tier,
               v_existing.status, false;
        return;
    end if;

    -- FOR UPDATE serializes concurrent grants to the same profile before the partial unique
    -- indexes are reached. A non-null subject alone is insufficient: require the fresh binding
    -- emitted by a verified Supabase login and its still-active session. This command never
    -- creates or adopts a profile.
    select u.*
      into v_user
      from app_users u
     where u.business_slug = v_business
       and lower(u.email::text) = v_email
       and u.supabase_user_id is not null
       and u.status = 'active'
     for update;
    if not found then
        raise exception 'verified_active_profile_required' using errcode = 'P0001';
    end if;

    select b.*
      into v_binding
      from app_supabase_verified_email_bindings b
      join app_sessions s
        on s.id = b.last_session_id
       and s.business_slug = b.business_slug
       and s.app_user_id = b.app_user_id
     where b.business_slug = v_business
       and b.app_user_id = v_user.id
       and b.supabase_user_id = v_user.supabase_user_id
       and lower(b.verified_email::text) = v_email
       and lower(v_user.email::text) = v_email
       and b.verified_at >= now() - interval '15 minutes'
       and s.revoked_at is null
       and s.expires_at > now()
     for update of b;
    if not found then
        raise exception 'fresh_verified_supabase_login_required' using errcode = 'P0001';
    end if;

    select p.*
      into v_plan
      from app_plan_policies p
     where p.business_slug = v_business
       and p.plan_key = v_plan_key
       and p.billing_interval = 'month'
       and p.price_cents > 0
       and lower(p.tier) not in ('', 'free', 'none', 'unentitled');
    if not found then
        raise exception 'active_monthly_plan_required' using errcode = 'P0001';
    end if;

    select g.*
      into v_existing
      from app_operator_access_grants g
     where g.business_slug = v_business
       and g.app_user_id = v_user.id
       and g.status = 'active'
     for update;
    if found then
        if v_existing.plan_key <> v_plan_key then
            raise exception 'access_already_granted_with_different_plan'
                using errcode = 'P0001';
        end if;
        return query
        select v_existing.id, v_existing.entitlement_id, v_existing.app_user_id,
               v_existing.verified_email::text, v_existing.plan_key, v_existing.tier,
               v_existing.status, false;
        return;
    end if;

    insert into app_entitlements (
        id, business_slug, app_user_id, tier, status, source, plan_key,
        current_period_end, metadata
    ) values (
        v_entitlement_id, v_business, v_user.id, v_plan.tier, 'active',
        'operator_ssh', v_plan.plan_key,
        null,
        jsonb_build_object(
            'operator_access_grant_id', v_grant_id,
            'access_kind', 'fourmanifold_staff_test',
            'usage_period', 'calendar_month'
        )
    );

    insert into app_user_profiles (id, business_slug, display_name)
    values (v_user.id, v_business, v_user.name)
    on conflict (id) do nothing;

    insert into app_operator_access_grants (
        id, business_slug, app_user_id, verified_email, profile_supabase_user_id,
        profile_verified_at,
        plan_key, tier, entitlement_id, grant_request_id, granted_from, granted_on_host
    ) values (
        v_grant_id, v_business, v_user.id, v_user.email, v_user.supabase_user_id,
        v_binding.verified_at,
        v_plan.plan_key, v_plan.tier, v_entitlement_id, p_request_id, p_ssh_client, v_host
    );

    -- Stripe rows remain untouched; a later paid row can coexist and survives revocation.
    perform operator_ssh_sync_user_tier(v_business, v_user.id);

    return query
    select v_grant_id, v_entitlement_id, v_user.id, v_user.email::text,
           v_plan.plan_key, v_plan.tier, 'active'::text, true;
end;
$$;

create or replace function operator_ssh_revoke_app_access(
    p_business_slug text,
    p_email text,
    p_request_id uuid,
    p_ssh_client inet,
    p_operator_host text
)
returns table (
    grant_id uuid,
    entitlement_id uuid,
    app_user_id uuid,
    verified_email text,
    plan_key text,
    tier text,
    status text,
    changed boolean
)
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
    v_email text := lower(trim(coalesce(p_email, '')));
    v_business text := trim(coalesce(p_business_slug, ''));
    v_host text := trim(coalesce(p_operator_host, ''));
    v_grant app_operator_access_grants%rowtype;
begin
    if session_user <> 'takyon_migration' then
        raise exception 'operator_migration_role_required' using errcode = '42501';
    end if;
    if v_business = '' or p_request_id is null or p_ssh_client is null or v_host = '' then
        raise exception 'complete_operator_ssh_grant_context_required' using errcode = '22023';
    end if;
    if v_email !~ '^[^@[:space:]]+@fourmanifold[.]com$' then
        raise exception 'fourmanifold_email_required' using errcode = '22023';
    end if;

    perform operator_ssh_revoke_stale_access(v_business, null, 'verified_identity_stale');

    select g.*
      into v_grant
      from app_operator_access_grants g
     where g.revoke_request_id = p_request_id;
    if found then
        if v_grant.business_slug <> v_business
           or lower(v_grant.verified_email::text) <> v_email then
            raise exception 'revoke_request_id_scope_mismatch' using errcode = '22023';
        end if;
        return query
        select v_grant.id, v_grant.entitlement_id, v_grant.app_user_id,
               v_grant.verified_email::text, v_grant.plan_key, v_grant.tier,
               v_grant.status, false;
        return;
    end if;

    select g.*
      into v_grant
      from app_operator_access_grants g
     where g.business_slug = v_business
       and lower(g.verified_email::text) = v_email
       and g.status = 'active'
     for update;
    if not found then
        select g.*
          into v_grant
          from app_operator_access_grants g
         where g.business_slug = v_business
           and lower(g.verified_email::text) = v_email
         order by g.granted_at desc
         limit 1;
        if found then
            return query
            select v_grant.id, v_grant.entitlement_id, v_grant.app_user_id,
                   v_grant.verified_email::text, v_grant.plan_key, v_grant.tier,
                   v_grant.status, false;
            return;
        end if;
        raise exception 'operator_ssh_access_not_found' using errcode = 'P0001';
    end if;

    update app_operator_access_grants g
       set status = 'revoked',
           revoke_request_id = p_request_id,
           revoked_at = now(),
           revoked_from = p_ssh_client,
           revoked_on_host = v_host,
           revoked_reason = 'root_ssh_revoke'
     where g.id = v_grant.id;

    update app_entitlements e
       set status = 'cancelled',
           metadata = e.metadata || jsonb_build_object(
               'operator_access_revoked', true,
               'operator_access_revoked_at', now()
           ),
           updated_at = now()
     where e.id = v_grant.entitlement_id
       and e.source = 'operator_ssh';

    perform operator_ssh_sync_user_tier(v_business, v_grant.app_user_id);

    return query
    select v_grant.id, v_grant.entitlement_id, v_grant.app_user_id,
           v_grant.verified_email::text, v_grant.plan_key, v_grant.tier,
           'revoked'::text, true;
end;
$$;

create or replace function operator_ssh_list_app_access(
    p_business_slug text default null,
    p_email text default null
)
returns table (
    grant_id uuid,
    business_slug text,
    app_user_id uuid,
    verified_email text,
    plan_key text,
    tier text,
    status text,
    entitlement_id uuid,
    grant_request_id uuid,
    granted_at timestamptz,
    granted_from text,
    granted_on_host text,
    revoke_request_id uuid,
    revoked_at timestamptz,
    revoked_from text,
    revoked_on_host text,
    revoked_reason text,
    usage_period_start timestamptz,
    used_microusd bigint,
    monthly_limit_microusd bigint
)
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
    v_business text := nullif(trim(coalesce(p_business_slug, '')), '');
    v_email text := nullif(lower(trim(coalesce(p_email, ''))), '');
begin
    if session_user <> 'takyon_migration' then
        raise exception 'operator_migration_role_required' using errcode = '42501';
    end if;
    if v_email is not null and v_email !~ '^[^@[:space:]]+@fourmanifold[.]com$' then
        raise exception 'fourmanifold_email_required' using errcode = '22023';
    end if;

    perform operator_ssh_revoke_stale_access(v_business, null, 'verified_identity_stale');

    return query
    select g.id,
           g.business_slug,
           g.app_user_id,
           g.verified_email::text,
           g.plan_key,
           g.tier,
           g.status,
           g.entitlement_id,
           g.grant_request_id,
           g.granted_at,
           g.granted_from::text,
           g.granted_on_host,
           g.revoke_request_id,
           g.revoked_at,
           g.revoked_from::text,
           g.revoked_on_host,
           g.revoked_reason,
           date_trunc('month', now()),
           coalesce(usage.used_microusd, 0)::bigint,
           p.included_ai_budget_microusd::bigint
      from app_operator_access_grants g
      join app_plan_policies p
        on p.business_slug = g.business_slug
       and p.plan_key = g.plan_key
      left join lateral (
          select coalesce(sum(case
                     when e.status = 'reserved' then e.estimated_cost_microusd
                     when e.status = 'completed' then e.actual_cost_microusd
                     else 0
                 end), 0)::bigint as used_microusd
            from app_usage_events e
           where e.business_slug = g.business_slug
             and e.app_user_id = g.app_user_id
             and e.created_at >= date_trunc('month', now())
      ) usage on true
     where (v_business is null or g.business_slug = v_business)
       and (v_email is null or lower(g.verified_email::text) = v_email)
     order by g.granted_at desc;
end;
$$;

-- Keep migration 0064's reserve gate byte-for-byte except for the period-start choice marked
-- below. A persistent operator_ssh grant has no Stripe renewal webhook to advance
-- current_period_end, so its allowance uses the current CALENDAR month. Every other entitlement
-- keeps the existing Stripe-anchored/fallback behavior, including persistent-credit overflow.
-- CREATE OR REPLACE preserves this function's OID: both direct Safebox broker calls and
-- takyon_app_reserve_usage's session-bound call continue through this exact gate.
create or replace function safebox_reserve_usage(
    p_business_slug           text,
    p_estimated_cost_microusd bigint,
    p_reservation_key         text,
    p_app_user_id             uuid,
    p_user_monthly_limit_microusd bigint,
    p_app_user_tier           text,
    p_purpose                 text,
    p_route                   text,
    p_provider                text,
    p_model                   text,
    p_metadata                jsonb
)
returns safebox_usage_gate_result
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
    r              safebox_usage_gate_result;
    v_status       text;
    v_hard_limit   bigint;
    v_period_start timestamptz;
    v_rolled_status       text;
    v_rolled_hard_limit   bigint;
    v_rolled_period_start timestamptz;
    v_user_exists  boolean;
    v_user_committed bigint;
    v_committed    bigint;
    v_user_period_start timestamptz;
    v_user_period_end   timestamptz;
    v_entitlement_source text;
    v_shortfall    bigint := 0;
    v_grant_row    record;
    v_debit        bigint;
    v_holds        jsonb := '[]'::jsonb;
    v_meta         jsonb;
begin
    insert into app_budgets (business_slug) values (p_business_slug)
        on conflict (business_slug) do nothing;
    select status, hard_limit_microusd, current_period_start
        into v_status, v_hard_limit, v_period_start
        from app_budgets where business_slug = p_business_slug for update;
    update app_budgets set
        current_period_start = date_trunc('week', now()),
        current_period_end = date_trunc('week', now()) + interval '1 week',
        updated_at = now()
        where business_slug = p_business_slug and current_period_end <= now()
        returning status, hard_limit_microusd, current_period_start
        into v_rolled_status, v_rolled_hard_limit, v_rolled_period_start;
    if found then
        v_status := v_rolled_status;
        v_hard_limit := v_rolled_hard_limit;
        v_period_start := v_rolled_period_start;
    end if;

    if v_status is distinct from 'active' then
        r.refusal := 'budget_inactive';
        r.fig_status := v_status;
        return r;
    end if;

    select e.id, e.business_slug, e.app_user_id, e.app_user_tier, e.reservation_key, e.purpose,
           e.route, e.status, e.estimated_cost_microusd, e.actual_cost_microusd, e.input_tokens,
           e.output_tokens, e.provider_request_id, e.provider, e.model, e.error, e.metadata,
           e.created_at, e.completed_at
        into r.id, r.business_slug, r.app_user_id, r.app_user_tier, r.reservation_key, r.purpose,
             r.route, r.status, r.estimated_cost_microusd, r.actual_cost_microusd, r.input_tokens,
             r.output_tokens, r.provider_request_id, r.provider, r.model, r.error, r.metadata,
             r.created_at, r.completed_at
        from app_usage_events e
        where e.business_slug = p_business_slug and e.reservation_key = p_reservation_key;
    if found then
        return r;
    end if;

    if p_app_user_id is not null then
        select exists (
            select 1 from app_users where business_slug = p_business_slug and id = p_app_user_id
        ) into v_user_exists;
        if not v_user_exists then
            r.refusal := 'app_user_not_found';
            return r;
        end if;
    end if;

    -- Per-subuser gate over the customer's entitlement-anchored monthly window (0063), with
    -- persistent-grant OVERFLOW above the allowance (0064). The only 0073 delta is that a
    -- root-SSH staff grant uses the current calendar month instead of an unrenewed Stripe date.
    if p_app_user_id is not null and p_user_monthly_limit_microusd is not null then
        v_user_period_start := v_period_start;
        select e.current_period_end, e.source
          into v_user_period_end, v_entitlement_source
          from app_entitlements e
         where e.business_slug = p_business_slug
           and e.app_user_id = p_app_user_id
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
        if v_entitlement_source = 'operator_ssh' then
            v_user_period_start := date_trunc('month', now());
        elsif v_user_period_end is not null
           and v_user_period_end > now()
           and v_user_period_end - interval '1 month' <= now() then
            v_user_period_start := v_user_period_end - interval '1 month';
        end if;
        select coalesce(sum(case
                when status = 'reserved' then estimated_cost_microusd
                when status = 'completed' then actual_cost_microusd
                else 0 end), 0)
            into v_user_committed
            from app_usage_events
            where business_slug = p_business_slug and app_user_id = p_app_user_id
              and created_at >= v_user_period_start;
        if v_user_committed + p_estimated_cost_microusd > p_user_monthly_limit_microusd then
            v_shortfall := v_user_committed + p_estimated_cost_microusd
                           - p_user_monthly_limit_microusd;
            -- Overflow: debit persistent grants (oldest first) for the slice above the
            -- allowance. All debits happen here, under the budget row lock, so the
            -- read-sum-then-debit sequence cannot race another reserve. Refunds elsewhere
            -- are pure increments and only make more available — never less.
            for v_grant_row in
                select id, remaining_microusd
                  from app_user_credit_grants
                 where business_slug = p_business_slug and app_user_id = p_app_user_id
                   and remaining_microusd > 0
                 order by created_at asc
                   for update
            loop
                exit when v_shortfall <= 0;
                v_debit := least(v_shortfall, v_grant_row.remaining_microusd);
                update app_user_credit_grants
                   set remaining_microusd = remaining_microusd - v_debit,
                       updated_at = now()
                 where id = v_grant_row.id;
                v_holds := v_holds || jsonb_build_array(
                    jsonb_build_object('grant_id', v_grant_row.id, 'microusd', v_debit));
                v_shortfall := v_shortfall - v_debit;
            end loop;
            if v_shortfall > 0 then
                -- Not fully coverable: roll back any partial debits (still inside this
                -- transaction's statement scope — but debits above were real updates, so
                -- undo them explicitly) and refuse with the exact figures.
                perform safebox_refund_grant_holds(
                    v_holds,
                    (select coalesce(sum((elem->>'microusd')::bigint), 0)::bigint
                       from jsonb_array_elements(v_holds) as elem));
                r.refusal := 'app_user_budget_exceeded';
                r.fig_user_limit_microusd := p_user_monthly_limit_microusd;
                r.fig_committed_microusd := v_user_committed;
                r.fig_requested_microusd := p_estimated_cost_microusd;
                return r;
            end if;
        end if;
    end if;

    if v_hard_limit is not null then
        select coalesce(sum(case
                when status = 'reserved' then estimated_cost_microusd
                when status = 'completed' then actual_cost_microusd
                else 0 end), 0)
            into v_committed
            from app_usage_events
            where business_slug = p_business_slug and created_at >= v_period_start;
        if v_committed + p_estimated_cost_microusd > v_hard_limit then
            -- Refuse at the pool ceiling: return any grant debits taken above.
            perform safebox_refund_grant_holds(
                v_holds,
                (select coalesce(sum((elem->>'microusd')::bigint), 0)::bigint
                   from jsonb_array_elements(v_holds) as elem));
            r.refusal := 'budget_exceeded';
            r.fig_hard_limit_microusd := v_hard_limit;
            r.fig_committed_microusd := v_committed;
            r.fig_requested_microusd := p_estimated_cost_microusd;
            return r;
        end if;
    end if;

    v_meta := coalesce(p_metadata, '{}'::jsonb);
    if jsonb_array_length(v_holds) > 0 then
        v_meta := v_meta || jsonb_build_object(
            'grant_holds', v_holds,
            'grant_hold_microusd',
            (select coalesce(sum((elem->>'microusd')::bigint), 0)::bigint
               from jsonb_array_elements(v_holds) as elem));
    end if;

    insert into app_usage_events
        (business_slug, app_user_id, app_user_tier, reservation_key, purpose, route,
         status, estimated_cost_microusd, provider, model, metadata)
        values (p_business_slug, p_app_user_id, p_app_user_tier, p_reservation_key, p_purpose,
                p_route, 'reserved', p_estimated_cost_microusd, p_provider, p_model, v_meta)
        returning id, business_slug, app_user_id, app_user_tier, reservation_key, purpose, route,
                  status, estimated_cost_microusd, actual_cost_microusd, input_tokens, output_tokens,
                  provider_request_id, provider, model, error, metadata, created_at, completed_at
        into r.id, r.business_slug, r.app_user_id, r.app_user_tier, r.reservation_key, r.purpose,
             r.route, r.status, r.estimated_cost_microusd, r.actual_cost_microusd, r.input_tokens,
             r.output_tokens, r.provider_request_id, r.provider, r.model, r.error, r.metadata,
             r.created_at, r.completed_at;
    return r;
end;
$$;

-- PUBLIC functions default to EXECUTE; close that default explicitly for every runtime role.
alter table app_supabase_verified_email_bindings owner to takyon_migration;
alter table app_operator_access_grants owner to takyon_migration;
alter function operator_ssh_sync_user_tier(text, uuid) owner to takyon_migration;
alter function operator_ssh_revoke_stale_access(text, uuid, text) owner to takyon_migration;
alter function operator_ssh_revoke_on_app_user_change() owner to takyon_migration;
alter function operator_ssh_revoke_on_business_change() owner to takyon_migration;
alter function takyon_app_bind_supabase_session(text, text, text, text, text, integer)
    owner to takyon_migration;
alter function operator_ssh_grant_app_access(text, text, text, uuid, inet, text)
    owner to takyon_migration;
alter function operator_ssh_revoke_app_access(text, text, uuid, inet, text)
    owner to takyon_migration;
alter function operator_ssh_list_app_access(text, text) owner to takyon_migration;

revoke all on app_supabase_verified_email_bindings, app_operator_access_grants from public;
revoke all on app_supabase_verified_email_bindings, app_operator_access_grants from
    takyon_app,
    takyon_app_runtime,
    takyon_runtime,
    takyon_operator_runtime,
    takyon_safebox_authority;

-- Close the remaining legacy PUBLIC application-function grants. Extension-owned citext helpers
-- remain normal SQL primitives.
revoke execute on function dispatch_due_wakes() from public;
grant execute on function dispatch_due_wakes()
    to takyon_runtime, takyon_operator_runtime, takyon_migration;

revoke execute on function safebox_prune_used_nonces(bigint) from public;
grant execute on function safebox_prune_used_nonces(bigint)
    to safebox, takyon_safebox_authority, takyon_migration;

revoke execute on function takyon_rls_bypass() from public;
revoke execute on function takyon_rls_business_slug() from public;
revoke execute on function takyon_rls_bound_app_user_id() from public;
revoke execute on function takyon_rls_session_hash() from public;
revoke execute on function takyon_rls_effective_app_user_id() from public;
revoke execute on function takyon_rls_effective_email() from public;
grant execute on function takyon_rls_bypass(),
                          takyon_rls_business_slug(),
                          takyon_rls_bound_app_user_id(),
                          takyon_rls_session_hash(),
                          takyon_rls_effective_app_user_id(),
                          takyon_rls_effective_email()
    to takyon_app, takyon_app_runtime, takyon_runtime, takyon_operator_runtime,
       takyon_safebox_authority, takyon_migration;

revoke execute on function operator_ssh_sync_user_tier(text, uuid)
    from public, takyon_app, takyon_app_runtime, takyon_runtime,
         takyon_operator_runtime, takyon_safebox_authority;
revoke execute on function operator_ssh_revoke_stale_access(text, uuid, text)
    from public, takyon_app, takyon_app_runtime, takyon_runtime,
         takyon_operator_runtime, takyon_safebox_authority;
revoke execute on function operator_ssh_revoke_on_app_user_change()
    from public, takyon_app, takyon_app_runtime, takyon_runtime,
         takyon_operator_runtime, takyon_safebox_authority;
revoke execute on function operator_ssh_revoke_on_business_change()
    from public, takyon_app, takyon_app_runtime, takyon_runtime,
         takyon_operator_runtime, takyon_safebox_authority;

revoke execute on function takyon_app_bind_supabase_session(
    text, text, text, text, text, integer
) from public;
grant execute on function takyon_app_bind_supabase_session(
    text, text, text, text, text, integer
) to takyon_app, takyon_app_runtime;

revoke execute on function operator_ssh_grant_app_access(
    text, text, text, uuid, inet, text
) from public, takyon_app, takyon_app_runtime, takyon_runtime,
       takyon_operator_runtime, takyon_safebox_authority;
revoke execute on function operator_ssh_revoke_app_access(
    text, text, uuid, inet, text
) from public, takyon_app, takyon_app_runtime, takyon_runtime,
       takyon_operator_runtime, takyon_safebox_authority;
revoke execute on function operator_ssh_list_app_access(text, text)
    from public, takyon_app, takyon_app_runtime, takyon_runtime,
         takyon_operator_runtime, takyon_safebox_authority;
grant execute on function operator_ssh_grant_app_access(
    text, text, text, uuid, inet, text
) to takyon_migration;
grant execute on function operator_ssh_revoke_app_access(
    text, text, uuid, inet, text
) to takyon_migration;
grant execute on function operator_ssh_list_app_access(text, text)
    to takyon_migration;

revoke execute on function safebox_reserve_usage(
    text, bigint, text, uuid, bigint, text, text, text, text, text, jsonb
) from public, takyon_app, takyon_app_runtime, takyon_operator_runtime;
grant execute on function safebox_reserve_usage(
    text, bigint, text, uuid, bigint, text, text, text, text, text, jsonb
) to takyon_runtime, takyon_safebox_authority, takyon_migration;
