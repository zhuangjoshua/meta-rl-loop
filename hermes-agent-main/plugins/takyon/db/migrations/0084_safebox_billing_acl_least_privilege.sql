-- 0084_safebox_billing_acl_least_privilege.sql
--
-- 0083 repaired the missing direct grant for the live Safebox login, but the three reserve/finalize
-- functions still carried 0038's transitional EXECUTE grant to takyon_runtime. That legacy role is
-- NOLOGIN, but retaining dormant application authority still leaves the ACL wider than the split-plane
-- service topology and makes a future membership/login regression immediately money-authoritative.
-- Revoke every application/runtime/legacy transport role explicitly, then pin the one live
-- authority login. The migration role retains its DDL ownership path and is not an application
-- caller.

revoke execute on function safebox_billing_open_account(uuid, bigint)
    from public, takyon_runtime, takyon_operator_runtime, takyon_app_runtime, takyon_app, safebox;

revoke execute on function safebox_billing_grant_allowance(
    uuid,
    bigint,
    text,
    timestamptz,
    timestamptz
) from public, takyon_runtime, takyon_operator_runtime, takyon_app_runtime, takyon_app, safebox;

revoke execute on function safebox_billing_reserve(uuid, bigint, text, text, text)
    from public, takyon_runtime, takyon_operator_runtime, takyon_app_runtime, takyon_app, safebox;

revoke execute on function safebox_billing_settle(text, bigint)
    from public, takyon_runtime, takyon_operator_runtime, takyon_app_runtime, takyon_app, safebox;

revoke execute on function safebox_billing_refund(text)
    from public, takyon_runtime, takyon_operator_runtime, takyon_app_runtime, takyon_app, safebox;

grant execute on function safebox_billing_open_account(uuid, bigint)
    to takyon_safebox_authority;

grant execute on function safebox_billing_grant_allowance(
    uuid,
    bigint,
    text,
    timestamptz,
    timestamptz
) to takyon_safebox_authority;

grant execute on function safebox_billing_reserve(uuid, bigint, text, text, text)
    to takyon_safebox_authority;

grant execute on function safebox_billing_settle(text, bigint)
    to takyon_safebox_authority;

grant execute on function safebox_billing_refund(text)
    to takyon_safebox_authority;
