-- 0085_live_action_bundle_build_binding.sql
--
-- A product request reads app_surface_contracts before resolving its immutable action bundle.
-- Bind the app-role read to that exact expected build so a concurrent live pointer flip cannot
-- return build B while the execution receipt is stamped as build A.
--
-- The legacy two-argument function remains temporarily for rolling-deploy compatibility: migrations
-- run before both serving replicas restart, so removing its signature here would break in-flight
-- requests from the old consumer. New consumers use only the bound signature below. Revoke/drop the
-- compatibility signature in a later migration after every replica is confirmed on this consumer.

begin;

alter table product_builds
    add column if not exists activated_at timestamptz;
alter table product_builds
    add column if not exists servable_until timestamptz;
alter table product_builds
    add column if not exists activation_state text not null default 'staged';
alter table product_builds
    add column if not exists activation_attempt_id text;
alter table product_builds
    add column if not exists activation_previous_build_id text;
alter table product_builds
    add column if not exists activation_previous_servable_until timestamptz;
alter table product_builds
    add column if not exists activation_error text;
alter table product_builds
    add column if not exists activation_prior_r2_previous_pointer text;

-- One-time rolling-deploy grace for HTML published before the build marker existed. The column is
-- added and stamped in the SAME conditional block, so replaying this migration can never extend the
-- deadline or grant unbound HTTP action access to a future build.
do $$
begin
    if not exists (
        select 1
          from information_schema.columns
         where table_schema = 'public'
           and table_name = 'product_builds'
           and column_name = 'legacy_unbound_until'
    ) then
        alter table product_builds add column legacy_unbound_until timestamptz;
        update product_builds pb
           set legacy_unbound_until = now() + interval '24 hours'
          from app_surface_contracts surface
         where surface.business_slug = pb.business_slug
           and surface.publish_status = 'published'
           and surface.live_build_id = pb.build_id
           and pb.status = 'live';
    end if;
end
$$;

update product_builds
   set activation_state = case
       when status = 'live' then 'live'
       when status = 'previous' then 'previous'
       when status = 'staged' then 'staged'
       else 'inactive'
   end
 where activation_state is null
    or (activation_state = 'staged' and status <> 'staged')
    or activation_state not in (
        'staged', 'pointer_pending', 'ambiguous', 'live', 'previous', 'rolled_back', 'inactive'
    );

alter table product_builds
    drop constraint if exists product_builds_activation_state_check;
alter table product_builds
    add constraint product_builds_activation_state_check
    check (activation_state in (
        'staged', 'pointer_pending', 'ambiguous', 'live', 'previous', 'rolled_back', 'inactive'
    ));
alter table product_builds
    drop constraint if exists product_builds_activation_prior_pointer_size_check;
alter table product_builds
    add constraint product_builds_activation_prior_pointer_size_check
    check (octet_length(coalesce(activation_prior_r2_previous_pointer, '')) <= 4096);

-- Durable, principal-scoped action idempotency. Local receipt files are diagnostic mirrors only:
-- they cannot coordinate two sub-user replicas and, without app_user_id in the key, could replay
-- one customer's result to another customer that chose the same caller idempotency key.
create table if not exists app_action_invocations (
    business_slug text not null references businesses(slug) on delete cascade,
    app_user_id uuid not null,
    reservation_key text not null,
    finish_token_hash text not null,
    action_name text not null,
    live_build_id text not null,
    status text not null check (status in ('running', 'completed', 'failed')),
    result_json text,
    run_json text,
    receipt_path text,
    error text,
    claimed_at timestamptz not null default now(),
    completed_at timestamptz,
    primary key (business_slug, reservation_key),
    foreign key (business_slug, app_user_id)
        references app_users(business_slug, id) on delete cascade,
    check (octet_length(coalesce(result_json, '')) <= 524288),
    check (octet_length(coalesce(run_json, '')) <= 131072)
);

alter table app_action_invocations
    add column if not exists finish_token_hash text;
update app_action_invocations
   set status = case when status = 'running' then 'failed' else status end,
       error = case when status = 'running' then 'superseded by finish-capability migration' else error end,
       completed_at = case when status = 'running' then now() else completed_at end,
       finish_token_hash = md5(business_slug || ':' || reservation_key)
                           || md5(reservation_key || ':finish')
 where finish_token_hash is null;
alter table app_action_invocations
    alter column finish_token_hash set not null;

alter table app_action_invocations enable row level security;
revoke all on table app_action_invocations from public, takyon_app_runtime, takyon_app;

create or replace function takyon_app_claim_action_invocation(
    p_business_slug text,
    p_session_hash text,
    p_finish_token_hash text,
    p_reservation_key text,
    p_action_name text,
    p_live_build_id text,
    p_receipt_path text
)
returns table (
    is_new boolean,
    status text,
    error text,
    result_json text,
    run_json text,
    receipt_path text
)
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
    v_app_user_id uuid;
    v_inserted boolean := false;
    v_row_count integer := 0;
begin
    if trim(p_finish_token_hash) !~ '^[0-9a-f]{64}$' then
        raise exception 'invalid action invocation finish capability';
    end if;
    select s.app_user_id
      into v_app_user_id
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
    if v_app_user_id is null then
        return;
    end if;

    insert into app_action_invocations (
        business_slug, app_user_id, reservation_key, finish_token_hash, action_name,
        live_build_id, status, receipt_path
    ) values (
        trim(p_business_slug), v_app_user_id, trim(p_reservation_key), trim(p_finish_token_hash),
        trim(p_action_name), trim(p_live_build_id), 'running', nullif(trim(p_receipt_path), '')
    ) on conflict (business_slug, reservation_key) do nothing;
    get diagnostics v_row_count = row_count;
    v_inserted := v_row_count > 0;

    return query
    select v_inserted, invocation.status, invocation.error, invocation.result_json,
           invocation.run_json, invocation.receipt_path
      from app_action_invocations invocation
     where invocation.business_slug = trim(p_business_slug)
       and invocation.app_user_id = v_app_user_id
       and invocation.reservation_key = trim(p_reservation_key)
       and invocation.action_name = trim(p_action_name)
       and invocation.live_build_id = trim(p_live_build_id)
     limit 1;
end;
$$;

create or replace function takyon_app_finish_action_invocation(
    p_business_slug text,
    p_finish_token_hash text,
    p_reservation_key text,
    p_status text,
    p_result_json text,
    p_run_json text,
    p_receipt_path text,
    p_error text
)
returns boolean
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
    v_updated integer := 0;
begin
    if trim(p_status) not in ('completed', 'failed') then
        raise exception 'invalid action invocation terminal status';
    end if;
    if trim(p_finish_token_hash) !~ '^[0-9a-f]{64}$' then
        raise exception 'invalid action invocation finish capability';
    end if;
    update app_action_invocations
       set status = trim(p_status),
           result_json = case when trim(p_status) = 'completed' then p_result_json else null end,
           run_json = p_run_json,
           receipt_path = nullif(trim(p_receipt_path), ''),
           error = case when trim(p_status) = 'failed' then p_error else null end,
           completed_at = now()
     where business_slug = trim(p_business_slug)
       and reservation_key = trim(p_reservation_key)
       and finish_token_hash = trim(p_finish_token_hash)
       and status = 'running';
    get diagnostics v_updated = row_count;
    if v_updated > 0 then
        return true;
    end if;
    return exists (
        select 1 from app_action_invocations
         where business_slug = trim(p_business_slug)
           and reservation_key = trim(p_reservation_key)
           and finish_token_hash = trim(p_finish_token_hash)
           and status = trim(p_status)
    );
end;
$$;

revoke execute on function takyon_app_claim_action_invocation(text, text, text, text, text, text, text)
    from public;
revoke execute on function takyon_app_finish_action_invocation(text, text, text, text, text, text, text, text)
    from public;
grant execute on function takyon_app_claim_action_invocation(text, text, text, text, text, text, text)
    to takyon_app_runtime, takyon_app, takyon_operator_runtime, takyon_runtime;
grant execute on function takyon_app_finish_action_invocation(text, text, text, text, text, text, text, text)
    to takyon_app_runtime, takyon_app, takyon_operator_runtime, takyon_runtime;

-- Keep the rolling-deploy signature, but apply the same staged-build denial as the bound reader.
create or replace function takyon_app_live_action_bundle(
    p_business_slug text,
    p_session_hash text
)
returns table (
    action_bundle_json text,
    action_bundle_sha256 text
)
language sql
stable
security definer
set search_path = public, pg_temp
as $$
    select pb.action_bundle_json, pb.action_bundle_sha256
      from app_sessions s
      join app_users u
        on u.business_slug = s.business_slug
       and u.id = s.app_user_id
      join app_surface_contracts surface
        on surface.business_slug = s.business_slug
      join product_builds pb
        on pb.business_slug = surface.business_slug
       and pb.build_id = surface.live_build_id
     where s.business_slug = trim(p_business_slug)
       and s.token_hash = trim(p_session_hash)
       and s.revoked_at is null
       and s.expires_at > now()
       and u.status = 'active'
       and surface.publish_status = 'published'
       and nullif(trim(surface.live_build_id), '') is not null
       and pb.status = 'live'
       and pb.activation_state in ('pointer_pending', 'ambiguous', 'live')
     limit 1;
$$;

revoke execute on function takyon_app_live_action_bundle(text, text) from public;
grant execute on function takyon_app_live_action_bundle(text, text)
    to takyon_app_runtime, takyon_app;

create or replace function takyon_app_legacy_unbound_live_build(
    p_business_slug text,
    p_session_hash text
)
returns table (
    live_build_id text,
    legacy_unbound_until timestamptz
)
language sql
stable
security definer
set search_path = public, pg_temp
as $$
    select pb.build_id, pb.legacy_unbound_until
      from app_sessions s
      join app_users u
        on u.business_slug = s.business_slug
       and u.id = s.app_user_id
      join app_surface_contracts surface
        on surface.business_slug = s.business_slug
      join product_builds pb
        on pb.business_slug = surface.business_slug
       and pb.build_id = surface.live_build_id
     where s.business_slug = trim(p_business_slug)
       and s.token_hash = trim(p_session_hash)
       and s.revoked_at is null
       and s.expires_at > now()
       and u.status = 'active'
       and surface.publish_status = 'published'
       and pb.status = 'live'
       and pb.legacy_unbound_until > now()
     limit 1;
$$;

revoke execute on function takyon_app_legacy_unbound_live_build(text, text) from public;
grant execute on function takyon_app_legacy_unbound_live_build(text, text)
    to takyon_app_runtime, takyon_app;

create or replace function takyon_app_live_action_bundle(
    p_business_slug text,
    p_session_hash text,
    p_expected_live_build_id text
)
returns table (
    live_build_id text,
    action_bundle_json text,
    action_bundle_sha256 text
)
language sql
stable
security definer
set search_path = public, pg_temp
as $$
    select pb.build_id, pb.action_bundle_json, pb.action_bundle_sha256
      from app_sessions s
      join app_users u
        on u.business_slug = s.business_slug
       and u.id = s.app_user_id
      join app_surface_contracts surface
        on surface.business_slug = s.business_slug
      join product_builds pb
        on pb.business_slug = surface.business_slug
       and pb.build_id = trim(p_expected_live_build_id)
     where s.business_slug = trim(p_business_slug)
       and s.token_hash = trim(p_session_hash)
       and s.revoked_at is null
       and s.expires_at > now()
       and u.status = 'active'
       and nullif(trim(p_expected_live_build_id), '') is not null
       and (
            (
                surface.publish_status = 'published'
                and surface.live_build_id = trim(p_expected_live_build_id)
                and pb.status = 'live'
                and pb.activation_state in ('pointer_pending', 'ambiguous', 'live')
            )
            or (
                pb.status = 'previous'
                and pb.servable_until > now()
            )
       )
     limit 1;
$$;

revoke execute on function takyon_app_live_action_bundle(text, text, text) from public;
grant execute on function takyon_app_live_action_bundle(text, text, text)
    to takyon_app_runtime, takyon_app;

commit;
