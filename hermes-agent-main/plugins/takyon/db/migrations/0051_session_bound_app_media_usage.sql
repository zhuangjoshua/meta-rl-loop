-- 0051_session_bound_app_media_usage.sql
-- App media quota reads are product-customer authority reads, not caller-argument reads.
--
-- 0045 introduced takyon_app_media_usage(text, text) as a SECURITY DEFINER port but the
-- second argument was an app_user_id. Because app runtime roles can execute the function
-- directly, that made media quota totals depend on caller-supplied identity. Keep the wire
-- signature stable, but reinterpret the second argument as the hashed app session token and
-- derive the app user inside the function.

create or replace function takyon_app_media_usage(
    p_business_slug text,
    p_session_hash text
)
returns table (
    user_bytes bigint,
    business_bytes bigint
)
language plpgsql
stable
security definer
set search_path = public, pg_temp
as $$
declare
    v_business_slug text := trim(coalesce(p_business_slug, ''));
    v_session_hash text := trim(coalesce(p_session_hash, ''));
    v_app_user_id text;
begin
    select u.id::text
      into v_app_user_id
      from app_sessions s
      join app_users u
        on u.business_slug = s.business_slug
       and u.id = s.app_user_id
     where s.business_slug = v_business_slug
       and s.token_hash = v_session_hash
       and s.revoked_at is null
       and s.expires_at > now()
       and u.status = 'active'
     limit 1;

    if v_app_user_id is null then
        raise exception 'app_session_required' using errcode = '28000';
    end if;

    return query
    select
        coalesce(sum(size_bytes) filter (where app_user_id = v_app_user_id), 0)::bigint as user_bytes,
        coalesce(sum(size_bytes), 0)::bigint as business_bytes
      from app_media
     where business_slug = v_business_slug;
end;
$$;

revoke execute on function takyon_app_media_usage(text, text) from public;
grant execute on function takyon_app_media_usage(text, text)
    to takyon_app_runtime, takyon_app;
