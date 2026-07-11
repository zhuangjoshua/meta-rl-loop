-- 0082_live_build_action_bundles.sql
-- Server actions are immutable build artifacts. Every sub-user replica resolves the bundle through
-- the live build pointer; no producer host SSHs mutable product source into serving caches.

begin;

alter table product_builds
    add column if not exists action_bundle_json text not null default '{}';
alter table product_builds
    add column if not exists action_bundle_sha256 text not null default '';

alter table product_builds
    drop constraint if exists product_builds_action_bundle_size_check;
alter table product_builds
    add constraint product_builds_action_bundle_size_check
    check (octet_length(action_bundle_json) <= 524288);

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
     limit 1;
$$;

revoke execute on function takyon_app_live_action_bundle(text, text) from public;
grant execute on function takyon_app_live_action_bundle(text, text)
    to takyon_app_runtime, takyon_app;

-- App roles consume bundles only through the session-bound function above.
revoke select, insert, update, delete on product_builds
    from takyon_app_runtime, takyon_app;

commit;
