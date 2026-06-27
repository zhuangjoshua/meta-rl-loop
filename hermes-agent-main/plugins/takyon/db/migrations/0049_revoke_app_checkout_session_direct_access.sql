-- 0049_revoke_app_checkout_session_direct_access.sql
-- Product app roles create/read their own checkout intents, but settled Stripe Checkout Session
-- rows are payment evidence owned by Safebox/webhook reconciliation. The app role should not read
-- or mutate those rows directly.

revoke select, insert, update, delete on app_checkout_sessions
    from takyon_app_runtime, takyon_app;
