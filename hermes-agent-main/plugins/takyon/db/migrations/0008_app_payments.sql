-- 0008_app_payments.sql
-- Phase 5 (increment d): product CHECKOUT + Stripe WEBHOOK + REVENUE ledger, PLUS the
-- net-new owner->custody ACCRUAL that the SQLite product path never had.
--
-- Builds on 0001 (users + businesses.owner_user_id), 0002 (custody ledger / flow B),
-- 0005 (sub-user identity), 0006 (entitlements). Four tables, three business-scoped and
-- one global:
--   * app_checkout_intents   — one row per started checkout (a product customer clicking
--     "upgrade"). client_reference_id is the caller's idempotency handle into Stripe.
--   * app_checkout_sessions  — the settled Stripe Checkout Session, keyed UNIQUE on the
--     stripe_checkout_session_id (so a replayed webhook upserts the one row).
--   * app_revenue_events     — append-a-row revenue ledger; UNIQUE(business_slug,
--     provider_event_id, stripe_object_id) makes a replayed paid event record once.
--   * webhook_events         — GLOBAL provider-event dedup, UNIQUE(provider,
--     provider_event_id). NOT business-scoped (a Stripe event id is global).
--
-- WHY this increment exists / what it fixes (mediationplan Phase 5 ADD (a)): the SQLite
-- product webhook (core.py:6844 _process_checkout_completed) records business REVENUE on a
-- paid checkout but performs ZERO owner accrual — `grep` finds no custody/accrual/app-fee
-- reference anywhere in the product path. A business's customers (sub-users) pay on the
-- shared platform Stripe, but the money never reaches the business OWNER's custody ledger
-- (flow B in 0002). This increment closes that: on a paid revenue event we resolve
-- business_slug -> businesses.owner_user_id (the linkage 0001 added and SQLite lacks) and
-- accrue the gross minus the platform app fee (STRIPE_CONNECT_APPLICATION_FEE_BPS, default
-- 2000 bps = 20%) into the owner's custody account via the EXISTING custody.accrue() — so
-- "sub-user payment shows in owner custody" (the Phase 5 acceptance). Accrual does NOT need
-- Connect; the owed balance is a ledger fact from day one.
--
-- WHY a webhook_events row LOCK (robustness #1, a deliberate improvement over SQLite): the
-- SQLite handler INSERT-OR-IGNOREs the dedup row but then processes UNCONDITIONALLY, and its
-- entitlement insert (core.py:6915) is a plain INSERT with no conflict target — so a
-- redelivered checkout.session.completed would append a DUPLICATE entitlement. The Postgres
-- port closes that: app_payments.record_webhook_and_process() takes `... for update` on the
-- webhook_events row and SKIPS if processed_at is already set, so each event is processed to
-- completion at most once even under concurrent redelivery (mirrors billing.py's
-- single-row-lock invariant). The whole dispatch runs in ONE transaction, so a mid-failure
-- rolls back the dedup row too and the event is cleanly retryable.
--
-- Postgres port of the SQLite trunk's DDL (core.py:3141-3234). Shape changes from SQLite,
-- matching 0001-0007: uuid PKs via gen_random_uuid() (vs TEXT hex), timestamptz (vs TEXT
-- ISO), bigint cents (vs INTEGER, matches 0002's *_cents), jsonb `metadata`/`payload` (vs
-- *_json TEXT). FK behaviour is preserved: business_slug CASCADE, app_user_id SET NULL (the
-- revenue/checkout record survives the customer), checkout_intent_id SET NULL.
--
-- Idempotent DDL: safe to run repeatedly. Clean `public` only (local test DB, or live
-- Supabase AFTER the polsia2 teardown).
--
-- REPLACE guard (robustness #1 — mediationplan.md): mirror 0001-0007. All four tables are
-- net-new to Postgres, but `create table if not exists` would SILENTLY bind to a
-- differently-shaped pre-existing table if one existed. The three product tables are
-- BUSINESS-scoped (carry business_slug); webhook_events is global and carries
-- provider_event_id. Fail loud if a same-named table lacks the distinguishing column.
do $$
begin
    if to_regclass('public.app_checkout_intents') is not null
       and not exists (
           select 1 from information_schema.columns
           where table_schema = 'public'
             and table_name   = 'app_checkout_intents'
             and column_name  = 'business_slug'
       )
    then
        raise exception
            'public.app_checkout_intents exists but is not the takyon shape (no business_slug). '
            'Inspect and remove it before applying takyon migrations. '
            'See mediationplan.md > Build Discipline (Gate 1).'
            using errcode = 'feature_not_supported';
    end if;
    if to_regclass('public.app_checkout_sessions') is not null
       and not exists (
           select 1 from information_schema.columns
           where table_schema = 'public'
             and table_name   = 'app_checkout_sessions'
             and column_name  = 'business_slug'
       )
    then
        raise exception
            'public.app_checkout_sessions exists but is not the takyon shape (no business_slug). '
            'Inspect and remove it before applying takyon migrations. '
            'See mediationplan.md > Build Discipline (Gate 1).'
            using errcode = 'feature_not_supported';
    end if;
    if to_regclass('public.app_revenue_events') is not null
       and not exists (
           select 1 from information_schema.columns
           where table_schema = 'public'
             and table_name   = 'app_revenue_events'
             and column_name  = 'business_slug'
       )
    then
        raise exception
            'public.app_revenue_events exists but is not the takyon shape (no business_slug). '
            'Inspect and remove it before applying takyon migrations. '
            'See mediationplan.md > Build Discipline (Gate 1).'
            using errcode = 'feature_not_supported';
    end if;
    if to_regclass('public.webhook_events') is not null
       and not exists (
           select 1 from information_schema.columns
           where table_schema = 'public'
             and table_name   = 'webhook_events'
             and column_name  = 'provider_event_id'
       )
    then
        raise exception
            'public.webhook_events exists but is not the takyon shape (no provider_event_id). '
            'Inspect and remove it before applying takyon migrations. '
            'See mediationplan.md > Build Discipline (Gate 1).'
            using errcode = 'feature_not_supported';
    end if;
end $$;

-- A started checkout. client_reference_id is the caller's idempotency handle (one started
-- checkout per logical upgrade attempt); the row is later linked to its Stripe session and
-- marked completed by the webhook.
create table if not exists app_checkout_intents (
    id                          uuid primary key default gen_random_uuid(),
    business_slug               text not null references businesses (slug) on delete cascade,
    app_user_id                 uuid references app_users (id) on delete set null,
    plan_key                    text not null check (length(plan_key) > 0),
    status                      text not null default 'created' check (length(status) > 0),
    client_reference_id         text not null unique check (length(client_reference_id) > 0),
    stripe_checkout_session_id  text,
    checkout_url                text,
    customer_email              text,
    metadata                    jsonb not null default '{}'::jsonb,
    created_at                  timestamptz not null default now(),
    updated_at                  timestamptz not null default now(),
    completed_at                timestamptz
);

-- The settled Stripe Checkout Session. UNIQUE on stripe_checkout_session_id so a replayed
-- checkout.session.completed upserts the single row rather than duplicating it.
create table if not exists app_checkout_sessions (
    id                          uuid primary key default gen_random_uuid(),
    business_slug               text not null references businesses (slug) on delete cascade,
    checkout_intent_id          uuid references app_checkout_intents (id) on delete set null,
    plan_key                    text,
    stripe_checkout_session_id  text not null unique check (length(stripe_checkout_session_id) > 0),
    stripe_customer_id          text,
    stripe_payment_intent_id    text,
    stripe_subscription_id      text,
    stripe_invoice_id           text,
    mode                        text,
    payment_status              text,
    status                      text,
    currency                    text,
    amount_subtotal_cents       bigint check (amount_subtotal_cents is null or amount_subtotal_cents >= 0),
    amount_total_cents          bigint check (amount_total_cents is null or amount_total_cents >= 0),
    client_reference_id         text,
    customer_email              text,
    raw_event_id                text,
    metadata                    jsonb not null default '{}'::jsonb,
    completed_at                timestamptz,
    created_at                  timestamptz not null default now(),
    updated_at                  timestamptz not null default now()
);

-- Append-a-row revenue ledger. UNIQUE(business_slug, provider_event_id, stripe_object_id)
-- makes a replayed paid event record exactly once (NULLs are distinct, matching SQLite, so
-- the dedup applies only when both ids are present — which they are for a Stripe event).
create table if not exists app_revenue_events (
    id                          uuid primary key default gen_random_uuid(),
    business_slug               text not null references businesses (slug) on delete cascade,
    provider_event_id           text,
    stripe_object_type          text,
    stripe_object_id            text,
    stripe_checkout_session_id  text,
    stripe_customer_id          text,
    revenue_type                text not null default 'checkout' check (length(revenue_type) > 0),
    status                      text not null default 'paid' check (length(status) > 0),
    currency                    text not null default 'usd' check (length(currency) > 0),
    amount_paid_cents           bigint not null default 0 check (amount_paid_cents >= 0),
    customer_email              text,
    occurred_at                 timestamptz not null default now(),
    metadata                    jsonb not null default '{}'::jsonb,
    created_at                  timestamptz not null default now(),
    unique (business_slug, provider_event_id, stripe_object_id)
);

-- GLOBAL provider-event dedup ledger. UNIQUE(provider, provider_event_id) is the dedup key;
-- the reserve gate locks this row `for update` and skips when processed_at is set, so each
-- delivered event is processed to completion at most once.
create table if not exists webhook_events (
    id                  uuid primary key default gen_random_uuid(),
    provider            text not null check (length(provider) > 0),
    provider_event_id   text not null check (length(provider_event_id) > 0),
    payload             jsonb not null default '{}'::jsonb,
    processed_at        timestamptz,
    error               text,
    created_at          timestamptz not null default now(),
    unique (provider, provider_event_id)
);

create index if not exists app_checkout_intents_business_idx
    on app_checkout_intents (business_slug, created_at desc);
create index if not exists app_checkout_sessions_business_idx
    on app_checkout_sessions (business_slug, created_at desc);
-- Entitlement subscription-lifecycle updates look up rows by stripe_subscription_id; the
-- checkout sessions index by subscription id keeps that lookup cheap.
create index if not exists app_checkout_sessions_subscription_idx
    on app_checkout_sessions (stripe_subscription_id);
create index if not exists app_revenue_events_business_idx
    on app_revenue_events (business_slug, occurred_at desc);
