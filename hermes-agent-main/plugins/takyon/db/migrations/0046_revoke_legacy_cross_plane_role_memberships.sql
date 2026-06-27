-- 0046_revoke_legacy_cross_plane_role_memberships.sql
-- Remove legacy SET ROLE bridges after the operator/app/Safebox login split.
--
-- 0031/0038 kept the old product-app RLS model alive by granting takyon_app to broad runtime roles.
-- The split target is simpler: every live service connects as its own login role from the start, and
-- no request path changes authority plane by role membership.

do $$
declare
    pair record;
begin
    for pair in
        select *
          from (values
              -- Old app demotion bridge.
              ('takyon_app', 'takyon_runtime'),
              ('takyon_app', 'takyon_operator_runtime'),
              ('takyon_app', 'takyon_app_runtime'),
              ('takyon_app', 'takyon_safebox_authority'),

              -- No new split role may be a SET ROLE wrapper around another plane.
              ('takyon_app_runtime', 'takyon_runtime'),
              ('takyon_app_runtime', 'takyon_operator_runtime'),
              ('takyon_app_runtime', 'takyon_safebox_authority'),
              ('takyon_app_runtime', 'takyon_app'),

              ('takyon_operator_runtime', 'takyon_runtime'),
              ('takyon_operator_runtime', 'takyon_app_runtime'),
              ('takyon_operator_runtime', 'takyon_safebox_authority'),
              ('takyon_operator_runtime', 'takyon_app'),

              ('takyon_safebox_authority', 'takyon_runtime'),
              ('takyon_safebox_authority', 'takyon_operator_runtime'),
              ('takyon_safebox_authority', 'takyon_app_runtime'),
              ('takyon_safebox_authority', 'takyon_app')
          ) as pairs(parent_role, member_role)
    loop
        if exists (select 1 from pg_roles where rolname = pair.parent_role)
           and exists (select 1 from pg_roles where rolname = pair.member_role)
        then
            execute format('revoke %I from %I', pair.parent_role, pair.member_role);
        end if;
    end loop;
end $$;
