-- 0033_billing_drop_topup.sql
-- Remove the à-la-carte operator "topup" overflow bucket (operator decision 2026-06-18):
-- operator funding now comes solely from the subscription allowance. The buy path
-- (control_api `/billing/topup/checkout` + the `takyon_topup` webhook branch + `billing.topup`)
-- and the `+ topup` spend-authority sums were deleted in the same change; reserve/settle/refund
-- and reconcile are allowance-only.
--
-- Drop the cached column. Historical `billing_entries` rows with bucket='topup' stay as
-- immutable audit; the `billing_bucket`/entry-kind enum values are left in place (Postgres
-- cannot drop an enum value still referenced by existing rows, and new code never writes them).
alter table if exists billing_accounts
    drop column if exists topup_balance_cents;
