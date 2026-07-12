-- 0083_safebox_billing_function_acl.sql
--
-- The live Safebox executes the operator-billing rail as the dedicated
-- takyon_safebox_authority login. 0038 created these SECURITY DEFINER functions before that login
-- existed, and 0044 granted every then-current safebox_* function through a one-time catalog scan.
-- A production ACL drift left safebox_billing_refund(text) without the direct authority grant, so
-- a failed/retried job could not release its hold. Pin every live billing entry point explicitly;
-- future authorization no longer depends on the historical wildcard scan having run last.
--
-- open_account and grant_allowance are included because the Safebox onboarding/allowance endpoints
-- call them through the same authority connection. No operator, app, or shared runtime role gains a
-- new grant here.

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
