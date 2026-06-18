-- 0031_app_plane_rls_role_set_grant.sql
-- Make the 0030 `takyon_app` role switch actually SUCCEED on the live database, so the 0027
-- app-plane RLS policies engage as designed (defense-in-depth at the DB layer).
--
-- THE GAP THIS CLOSES
-- 0030 creates the restricted, NON-bypassrls `takyon_app` role and grants it the app-plane DML.
-- core.TakyonStore._pg_app_scope then runs `SET LOCAL ROLE takyon_app` for the duration of an
-- app-customer request so the DB enforces the per-customer boundary. But `SET ROLE r` requires the
-- *connected login role* to be a member of `r` WITH the SET option — UNLESS it is a true superuser
-- (a superuser may SET ROLE to anything). 0030 never granted that membership.
--   * On the throwaway test rig the login role is a TRUE superuser (`initdb -U postgres`), so the
--     SET ROLE succeeds implicitly and the inv3 RLS tests pass.
--   * On the LIVE Supabase database the runtime login role (`postgres`, from DATABASE_URL) is
--     `rolbypassrls = true` but `rolsuper = FALSE`. A non-superuser with no SET-able membership in
--     `takyon_app` CANNOT `SET ROLE takyon_app`: PostgreSQL raises
--     `InsufficientPrivilege: permission denied to set role "takyon_app"`.
-- That exception is caught at each app handler's `except Exception -> tool_error`, so prod is
-- FAIL-CLOSED (no cross-tenant leak), but the DB-layer RLS never engages and the ~19 scoped
-- app-customer ops error instead of running under the policies. This migration removes that gap.
--
-- THE FIX (smallest correct change)
-- Grant the live runtime login role membership in `takyon_app` WITH SET TRUE, so `SET ROLE
-- takyon_app` succeeds and the request runs under the NON-bypassrls role the 0027 policies bite on.
-- Nothing is weakened: the runtime role KEEPS its own privileges/bypassrls for the operator/service
-- path (which never SET ROLEs); RLS only applies for the duration of the explicit app scope.
-- INHERIT is FALSE so the login role does not passively pick up `takyon_app`'s grants — it must
-- *explicitly* `SET ROLE` to drop into the restricted scope, exactly as _pg_app_scope does.
--
-- TARGET ROLE: the *connected* role applying this migration (`current_user`). On prod that is the
-- DATABASE_URL login role (`postgres`) — the same role the runtime later opens its connections as
-- and runs `SET ROLE takyon_app` from. Keying on `current_user` makes the grant correct even if the
-- login role is renamed, with no hardcoded role literal to drift.
--
-- IDEMPOTENT + GUARDED. No-op when:
--   * the `takyon_app` role does not exist (0030 not yet applied), or
--   * the connected role IS a superuser (it can already SET ROLE to anything — nothing to grant), or
--   * the connected role is `takyon_app` itself, or
--   * the connected role is ALREADY a SET-able member of `takyon_app`.
-- Re-running is safe; it never widens beyond "this login role may SET ROLE takyon_app".
--
-- POSTGRES VERSION: uses the PG 16+ `WITH SET TRUE` membership option (live + rig are PG 16). On
-- PG <16 a plain membership already conferred SET-ROLE capability, so this exact statement is the
-- right one for the supported server; a pre-16 server would reject the `SET` option loudly rather
-- than silently mis-grant.

do $$
declare
    runtime_role text := current_user;
    is_super boolean;
    already_set_member boolean;
begin
    -- 0030 must have created the restricted role; otherwise there is nothing to grant into.
    if not exists (select 1 from pg_roles where rolname = 'takyon_app') then
        raise notice '0031: takyon_app role absent (0030 not applied); skipping SET grant';
        return;
    end if;

    -- A superuser can already SET ROLE to anything; granting would be redundant noise.
    select rolsuper into is_super from pg_roles where rolname = runtime_role;
    if coalesce(is_super, false) then
        raise notice '0031: runtime role % is a superuser; SET ROLE takyon_app already permitted', runtime_role;
        return;
    end if;

    -- Never grant takyon_app into itself.
    if runtime_role = 'takyon_app' then
        raise notice '0031: connected as takyon_app; nothing to grant';
        return;
    end if;

    -- Already a SET-able member? (set_option is the PG16 column that gates SET ROLE.)
    select exists (
        select 1
        from pg_auth_members m
        join pg_roles grp on grp.oid = m.roleid
        join pg_roles mem on mem.oid = m.member
        where grp.rolname = 'takyon_app'
          and mem.rolname = runtime_role
          and m.set_option
    ) into already_set_member;

    if already_set_member then
        raise notice '0031: % already a SET-able member of takyon_app; no-op', runtime_role;
        return;
    end if;

    -- To grant `takyon_app` to ANY role, the granting (connected) role must hold ADMIN OPTION on
    -- `takyon_app` (PG 16). On the normal deploy this holds: the SAME runtime login role applied
    -- 0030, and `CREATE ROLE takyon_app` by a non-superuser CREATEROLE role implicitly grants the
    -- creator membership WITH ADMIN. If `takyon_app` was instead pre-created out-of-band by a
    -- DIFFERENT role (e.g. a superuser), the runtime role has no ADMIN and the grant would raise an
    -- opaque "permission denied to grant role". Detect that here and fail LOUD with the exact remedy
    -- rather than a cryptic privilege error.
    if not exists (
        select 1
        from pg_auth_members m
        join pg_roles grp on grp.oid = m.roleid
        join pg_roles mem on mem.oid = m.member
        where grp.rolname = 'takyon_app'
          and mem.rolname = runtime_role
          and m.admin_option
    ) then
        raise exception
            '0031: runtime role % lacks ADMIN OPTION on takyon_app, so it cannot self-grant SET. '
            'Run once as a role that holds ADMIN on takyon_app (or the bootstrap superuser): '
            'GRANT takyon_app TO %I WITH INHERIT FALSE, SET TRUE;',
            runtime_role, runtime_role;
    end if;

    -- Grant membership WITH SET so `SET ROLE takyon_app` is permitted; WITHOUT INHERIT so the login
    -- role does not passively gain takyon_app's privileges — it must explicitly drop into the scope.
    execute format(
        'grant takyon_app to %I with inherit false, set true',
        runtime_role
    );
    raise notice '0031: granted takyon_app to % WITH SET TRUE, INHERIT FALSE (RLS app-scope now engages)', runtime_role;
end $$;
