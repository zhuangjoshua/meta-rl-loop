-- 0002_ledgers.sql
-- Phase 2: the two money ledgers, kept strictly separate (mediationplan.md).
--
--   Flow A — billing  (user -> platform): the user pays for compute. Two buckets:
--     * allowance = included usage, an opaque metering unit, NEVER shown as money;
--     * topup     = exact money the user paid in.
--     Spend allowance first, then topup. Reserve-then-settle around each costly job.
--
--   Flow B — custody (sub-users -> user, held by the platform): the user's customers
--     pay on the shared platform Stripe; the platform accrues net (gross - app fee)
--     as money OWED to the user and later pays it out via Stripe Connect. Accrued
--     from day one, independent of whether the user has connected Connect yet.
--
-- The two never merge: no row, balance, or query joins flow A to flow B.
--
-- Shape: each ledger has a cached-balance account row (one per user, the row that
-- reserve/settle/payout lock with FOR UPDATE) plus an APPEND-ONLY entries table.
-- The cached balances are always re-derivable from the entries; reconcile_* proves
-- it. CHECK constraints are defense-in-depth: even a buggy writer cannot drive a
-- balance negative or oversell the allowance — the transaction aborts instead.
--
-- Idempotent DDL: safe to run repeatedly. Clean `public` only (local test DB, or
-- live Supabase AFTER the polsia2 teardown).
--
-- REPLACE guard (robustness #1 — mediationplan.md, 2026-05-30): takyon OWNS
-- public.billing_accounts. The live Supabase still hosts polsia2's Stripe-subscription
-- shaped `billing_accounts`; `create table if not exists` would silently bind to it.
-- Fail loud if a non-takyon `billing_accounts` is present (takyon's has
-- allowance_included_cents) and point at the backup-gated teardown. See the twin guard
-- in 0001_identity_spine.sql and plugins/takyon/db/retire_polsia2_public.sql.
do $$
begin
    if to_regclass('public.billing_accounts') is not null
       and not exists (
           select 1 from information_schema.columns
           where table_schema = 'public'
             and table_name   = 'billing_accounts'
             and column_name  = 'allowance_included_cents'
       )
    then
        raise exception
            'public.billing_accounts exists but is not the takyon shape (no '
            'allowance_included_cents). Run plugins/takyon/db/retire_polsia2_public.sql '
            'first (one-time, backup-gated polsia2 teardown), then re-apply takyon '
            'migrations. See mediationplan.md > Ground Truth (REPLACE decision).'
            using errcode = 'feature_not_supported';
    end if;
end $$;

-- enums (create-if-absent; CREATE TYPE has no IF NOT EXISTS)
do $$ begin
    create type billing_bucket as enum ('allowance', 'topup');
exception when duplicate_object then null; end $$;

do $$ begin
    create type billing_entry_kind as enum
        ('grant', 'reserve', 'settle', 'refund', 'topup', 'debit');
exception when duplicate_object then null; end $$;

do $$ begin
    create type custody_entry_kind as enum
        ('accrual', 'app_fee', 'payout', 'refund', 'adjustment');
exception when duplicate_object then null; end $$;

-- Flow A: cached billing balances, one row per user. allowance_used counts UP
-- toward allowance_included (the period cap); topup_balance is spendable money that
-- counts DOWN as it is reserved. Both reset/derive from billing_entries.
create table if not exists billing_accounts (
    user_id                   uuid primary key references users (id) on delete cascade,
    allowance_included_cents  bigint not null default 0,
    allowance_used_cents      bigint not null default 0,
    allowance_period_start    timestamptz,
    allowance_resets_at       timestamptz,
    topup_balance_cents       bigint not null default 0,
    created_at                timestamptz not null default now(),
    updated_at                timestamptz not null default now(),
    constraint billing_allowance_used_nonneg
        check (allowance_used_cents >= 0),
    constraint billing_allowance_within_cap
        check (allowance_used_cents <= allowance_included_cents),
    constraint billing_topup_nonneg
        check (topup_balance_cents >= 0)
);

-- Flow A: append-only ledger. amount_cents is a non-negative magnitude; `kind`
-- gives the direction. reservation_key groups the reserve + its later settle/refund
-- entries (one logical job); idempotency_key makes every write replay-safe.
create table if not exists billing_entries (
    id                   uuid primary key default gen_random_uuid(),
    user_id              uuid not null references users (id) on delete cascade,
    business_slug        text references businesses (slug),
    bucket               billing_bucket not null,
    kind                 billing_entry_kind not null,
    amount_cents         bigint not null,
    balance_after_cents  bigint not null,
    reservation_key      text,
    job_id               text,
    idempotency_key      text not null unique,
    metadata             jsonb not null default '{}'::jsonb,
    created_at           timestamptz not null default now(),
    constraint billing_amount_nonneg check (amount_cents >= 0)
);

create index if not exists billing_entries_user_idx
    on billing_entries (user_id);
create index if not exists billing_entries_resv_idx
    on billing_entries (reservation_key);

-- Flow B: cached custody balances, one row per user. owed_balance is money the
-- platform is holding for the user; paid_out is the lifetime sum already withdrawn.
create table if not exists custody_accounts (
    user_id             uuid primary key references users (id) on delete cascade,
    owed_balance_cents  bigint not null default 0,
    paid_out_cents      bigint not null default 0,
    currency            text not null default 'usd',
    created_at          timestamptz not null default now(),
    updated_at          timestamptz not null default now(),
    constraint custody_owed_nonneg    check (owed_balance_cents >= 0),
    constraint custody_paidout_nonneg check (paid_out_cents >= 0)
);

-- Flow B: append-only ledger. gross/fee are non-negative magnitudes; net_cents is
-- the SIGNED effect on owed_balance (accrual +net, payout -amount), so
-- owed_balance == Σ net_cents holds exactly and reconcile_custody checks it.
create table if not exists custody_entries (
    id               uuid primary key default gen_random_uuid(),
    user_id          uuid not null references users (id) on delete cascade,
    business_slug    text references businesses (slug),
    kind             custody_entry_kind not null,
    gross_cents      bigint not null default 0,
    fee_cents        bigint not null default 0,
    net_cents        bigint not null,
    stripe_ref       text,
    idempotency_key  text not null unique,
    metadata         jsonb not null default '{}'::jsonb,
    created_at       timestamptz not null default now(),
    constraint custody_gross_nonneg check (gross_cents >= 0),
    constraint custody_fee_nonneg   check (fee_cents >= 0)
);

create index if not exists custody_entries_user_idx
    on custody_entries (user_id);
