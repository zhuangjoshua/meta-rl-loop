-- 0017_operator_billing_identity.sql
-- Persist the dedicated top-level operator Stripe customer/subscription handles so
-- operator billing never collides with product-app customer Stripe records that
-- happen to share the same email address.

BEGIN;

ALTER TABLE users
    ADD COLUMN IF NOT EXISTS operator_billing_customer_id text,
    ADD COLUMN IF NOT EXISTS operator_billing_subscription_id text,
    ADD COLUMN IF NOT EXISTS operator_billing_subscription_status text NOT NULL DEFAULT 'none';

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'users_operator_billing_subscription_status_chk'
    ) THEN
        ALTER TABLE users
            ADD CONSTRAINT users_operator_billing_subscription_status_chk
            CHECK (
                operator_billing_subscription_status IN (
                    'none',
                    'active',
                    'trialing',
                    'past_due',
                    'paused',
                    'canceled',
                    'incomplete',
                    'incomplete_expired',
                    'unpaid'
                )
            );
    END IF;
END $$;

CREATE UNIQUE INDEX IF NOT EXISTS users_operator_billing_customer_idx
    ON users (operator_billing_customer_id)
    WHERE operator_billing_customer_id IS NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS users_operator_billing_subscription_idx
    ON users (operator_billing_subscription_id)
    WHERE operator_billing_subscription_id IS NOT NULL;

COMMIT;
