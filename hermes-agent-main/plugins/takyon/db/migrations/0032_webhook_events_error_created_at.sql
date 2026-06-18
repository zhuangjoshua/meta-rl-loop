-- 0032_webhook_events_error_created_at.sql
-- Reconcile the LIVE webhook_events table with the shape app_payments.py writes against.
--
-- THE GAP THIS CLOSES
-- app_payments.record_webhook_and_process() (the product sub-app Stripe webhook entry) marks an
-- event processed with `update webhook_events set processed_at = now(), error = null ...`. The
-- takyon shape for this table — migration 0008_app_payments.sql — declares `error text` and
-- `created_at timestamptz not null default now()`. But on the live control plane the
-- webhook_events table predates 0008: it was created by the older polsia-lineage schema and has
-- only (id, provider, provider_event_id, payload, processed_at, received_at). 0008's
-- `create table if not exists webhook_events (...)` therefore bound to the pre-existing table and
-- NEVER added the `error` / `created_at` columns.
--   * Result: the `... set error = null` UPDATE raises
--     `psycopg.errors.UndefinedColumn: column "error" of relation "webhook_events" does not exist`.
--   * That UPDATE shares the single transaction with the entitlement grant + revenue ledger +
--     owner custody accrual, so the WHOLE dispatch rolls back, the event is never marked
--     processed_at, and Stripe retries it forever. webhook_events has processed exactly zero events
--     (0 rows). Product revenue only ever settled via the recovery path
--     reconcile_checkout_session, not the webhook rail.
--
-- THE FIX (smallest correct change)
-- Add ONLY the two columns the code references, with EXACTLY 0008's types, idempotently. This does
-- not touch the existing `received_at` column (a harmless polsia-lineage extra the takyon code
-- never reads) and applies no other 0008 DDL — the takyon app tables already exist and are in use.
-- Idempotent / safe to re-run: `add column if not exists` is a no-op once the columns are present,
-- so this aligns both a fresh takyon DB (where 0008 already created them) and the live drifted DB.
--
-- Column types match 0008_app_payments.sql verbatim:
--   error      text                                   (nullable; cleared to null on success)
--   created_at timestamptz not null default now()     (row-insert timestamp; default backfills rows)

alter table if exists webhook_events
    add column if not exists error text;

alter table if exists webhook_events
    add column if not exists created_at timestamptz not null default now();
