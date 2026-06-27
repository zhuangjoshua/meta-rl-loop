-- 0054_safebox_app_checkout_reconcile_grants.sql
--
-- Safebox owns product-app Stripe reconciliation. A completed Checkout session
-- can arrive through a signed webhook or through the explicit recovery route
-- when Stripe completed but the webhook did not land. That authority route
-- inserts the settled checkout-session audit row, marks the checkout intent
-- completed, grants the entitlement through the existing safebox_* functions,
-- syncs the cached app_users tier, and ensures the 1:1 app profile exists.
--
-- The split-role migration gave the Safebox direct write authority for the
-- money ledgers, but omitted the checkout-session/projection tables needed by
-- this reconciliation path. Grant only those Safebox writes. App roles remain
-- denied direct checkout-session and money-ledger access.

grant insert, update on app_checkout_sessions
    to takyon_safebox_authority;

grant update (status, completed_at, updated_at) on app_checkout_intents
    to takyon_safebox_authority;

grant update (tier, updated_at) on app_users
    to takyon_safebox_authority;

grant insert on app_user_profiles
    to takyon_safebox_authority;

revoke select, insert, update, delete on app_checkout_sessions
    from takyon_app_runtime, takyon_app;
