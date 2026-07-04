-- 0066_app_close_account_port.sql
-- App Store rail (readmodular §4.3) — the SECURITY DEFINER port for Apple 5.1.1(v) account
-- deletion, so the sub-user delete rail actually works on the app-runtime plane.
--
-- WHY THIS EXISTS: migration 0045 REVOKED all direct DML (and SELECT) on app_users/app_sessions
-- from takyon_app_runtime/takyon_app — every identity mutation must go through a bounded
-- SECURITY DEFINER port (takyon_app_validate_session, takyon_app_revoke_session, …). The account
-- delete tool needs to (a) revoke ALL of a user's sessions and (b) close the account — neither had
-- a port, so on prod it would have failed `permission denied for table app_sessions`. This adds the
-- one missing port, self-scoped by the session hash (no app_user_id argument surface at all — the
-- port derives the user from the presented session, so it is structurally IDOR-proof).
--
-- SELF-SCOPING + DEFINER-CONTEXT BYPASS (per 0065): app_users/app_sessions are FORCE-RLS; the app
-- pool pins takyon.rls_bypass='0'. This port validates the session itself and pins every write by
-- the derived app_user_id, so row security inside it is redundant. It does NOT carry a per-function
-- `set takyon.rls_bypass` — prod's takyon_migration may not SET that parameter (0065 "learned the
-- hard way"). Instead 0065's takyon_rls_bypass() grants bypass whenever
-- `current_user IS DISTINCT FROM session_user` — true inside this SECURITY DEFINER port (owner =
-- trusted takyon_migration, login = the app role). This does NOT loosen the subuser boundary: app
-- roles still hold no direct table privileges, and a caller without a valid session hash gets
-- nothing (v_user_id stays null → closed=false).
--
-- SUBUSER-SECURITY INVARIANT: no new table, no widened grant beyond EXECUTE on this one port to the
-- app roles (exactly like every other 0045 port). Deleting revokes all sessions, sets status
-- 'closed' (validate_session already requires status='active', so the account is dead immediately),
-- and anonymizes email + nulls supabase_user_id so a later sign-up with the same address is a FRESH
-- account (avoids the closed-row lockout / unique(business_slug,email) collision).

begin;

create or replace function takyon_app_close_account(
    p_business_slug text,
    p_session_hash text
)
returns table (
    out_app_user_id uuid,   -- named to avoid ambiguity with app_sessions.app_user_id inside the body
    out_closed boolean
)
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
    v_user_id uuid;
begin
    if nullif(trim(p_business_slug), '') is null then
        return query select null::uuid, false; return;
    end if;
    if nullif(trim(p_session_hash), '') is null then
        return query select null::uuid, false; return;
    end if;

    -- Derive the target from the LIVE session only (business-scoped, unrevoked, unexpired, active).
    select u.id
      into v_user_id
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

    if v_user_id is null then
        return query select null::uuid, false; return;   -- nothing to do; idempotent, no error
    end if;

    -- Revoke every live session for this user.
    update app_sessions
       set revoked_at = now()
     where business_slug = trim(p_business_slug)
       and app_user_id = v_user_id
       and revoked_at is null;

    -- Close + anonymize so a re-signup with the same email lands as a fresh account. The tombstone
    -- keeps the row (FK targets, receipts) but frees the email + detaches the Supabase identity.
    update app_users
       set status = 'closed',
           email = 'deleted+' || v_user_id::text || '@deleted.invalid',
           supabase_user_id = null,
           updated_at = now()
     where business_slug = trim(p_business_slug)
       and id = v_user_id;

    return query select v_user_id, true;
end;
$$;

revoke execute on function takyon_app_close_account(text, text) from public;
grant execute on function takyon_app_close_account(text, text)
    to takyon_app_runtime, takyon_app;

commit;
